"""Backend server pool, GAS account pool, and HTTP keep-alive transport."""

from collections import deque
import http.client
import json
import ssl
import threading
import time
import urllib.parse
import urllib.request

from fd_config import normalize_tunnel_url, today_str

def detect_server_provider(srv: "BackendServer") -> str:
    """Identify backend provider ('render', 'cloudflare', 'snapdeploy', or 'custom') from URL and name."""
    try:
        host = (urllib.parse.urlsplit(srv.url).hostname or "").lower()
    except Exception:
        host = ""
    name_l = (srv.name or "").lower()
    if host.endswith(".onrender.com") or "onrender.com" in host or "render" in name_l:
        return "render"
    if host.endswith(".workers.dev") or "workers.dev" in host or "cloudflare" in name_l:
        return "cloudflare"
    if "snapdeploy" in host or "snapdeploy" in name_l:
        return "snapdeploy"
    return "custom"


def compute_auto_profile(n_gas: int, n_cf: int, n_render: int, n_snap: int, n_custom: int = 0) -> dict:
    """Compute optimal GAS & Backend settings based on active account/server counts."""
    n_srv = n_cf + n_render + n_snap + n_custom
    if n_gas >= 4:
        conc, daily, rpm = 18, 16000, 140
    elif n_gas >= 2:
        conc, daily, rpm = 22, 18000, 180
    else:
        conc, daily, rpm = 26, 19000, 210
    gas_mode = "fair" if n_gas >= 2 else "priority"
    server_mode = "fair" if n_srv >= 2 else "priority"
    return {
        "max_concurrent": conc,
        "daily_limit": daily,
        "max_rpm": rpm,
        "gas_mode": gas_mode,
        "server_mode": server_mode,
    }

class BackendServer:
    def __init__(self, name: str, url: str, enabled: bool = True, deploy_meta: dict | None = None):
        self.name = name
        self.url = url.strip().rstrip("/")
        self.enabled = enabled
        self.deploy_meta = dict(deploy_meta) if isinstance(deploy_meta, dict) else {}
        self.active = 0
        self.total_requests = 0
        self.errors = 0
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.last_latency_ms = 0
        self.last_error = ""

    def to_dict(self):
        d = {"name": self.name, "url": self.url, "enabled": self.enabled}
        if self.deploy_meta:
            d["deploy_meta"] = self.deploy_meta
        return d

    @property
    def status_label(self) -> str:
        if not self.enabled:
            return "Disabled"
        now = time.time()
        if now < self.cooldown_until:
            rem = int(self.cooldown_until - now) + 1
            return f"Down ({rem}s)"
        if self.last_error:
            return "Warning"
        return "Ready"


class ServerPool:
    """
    Manages multiple backend servers (Render / SnappDeploy / Oracle).
    Modes:
      - "priority": tries #1 first; fails over to #2, #3 if #1 is down/in cooldown.
      - "fair": distributes load evenly (1/N) across all healthy enabled servers.
      - "only:<name>": routes strictly to the specified server.
    """

    def __init__(self):
        self.lock = threading.Condition()
        self.servers: list[BackendServer] = []
        self.server_mode: str = "priority"

    def load_servers(self, items: list[dict], server_mode: str = "priority"):
        with self.lock:
            self.servers = [
                BackendServer(
                    name=item.get("name", f"Server {i + 1}"),
                    url=item.get("url", ""),
                    enabled=bool(item.get("enabled", True)),
                    deploy_meta=item.get("deploy_meta"),
                )
                for i, item in enumerate(items)
                if item.get("url", "").strip()
            ]
            self.server_mode = server_mode or "priority"

    def set_mode(self, mode: str):
        with self.lock:
            self.server_mode = mode or "priority"

    def get_candidates(self, require_explicit: bool = False) -> list[BackendServer | None]:
        with self.lock:
            now = time.time()
            enabled = [s for s in self.servers if s.enabled and s.url]
            if not enabled:
                return [] if require_explicit else [None]

            if self.server_mode.startswith("provider:"):
                prov = self.server_mode[9:]
                grp = [s for s in enabled if detect_server_provider(s) == prov]
                if not grp:
                    return []
                healthy = [s for s in grp if now >= s.cooldown_until]
                healthy.sort(key=lambda s: (s.active, s.consecutive_failures, s.total_requests))
                cooldown = sorted([s for s in grp if now < s.cooldown_until], key=lambda s: s.cooldown_until)
                return healthy + cooldown

            if self.server_mode.startswith("only:"):
                target_name = self.server_mode[5:]
                pinned = [s for s in enabled if s.name == target_name]
                if pinned:
                    return pinned

            healthy = [s for s in enabled if now >= s.cooldown_until]
            cooldown = sorted([s for s in enabled if now < s.cooldown_until], key=lambda s: s.cooldown_until)

            if self.server_mode in ("fair", "auto"):
                healthy.sort(key=lambda s: (s.active, s.consecutive_failures, s.total_requests))
            return healthy + cooldown

    def get_keep_awake_targets(self, require_explicit: bool = False) -> list[BackendServer]:
        with self.lock:
            enabled = [s for s in self.servers if s.enabled and s.url]
            if not enabled:
                return []
            if self.server_mode == "auto":
                # Cloudflare Workers never sleep; only keep-awake Render/SnapDeploy/custom servers
                return [s for s in enabled if detect_server_provider(s) != "cloudflare"]
            if self.server_mode == "fair":
                return list(enabled)
            if self.server_mode.startswith("provider:"):
                prov = self.server_mode[9:]
                return [s for s in enabled if detect_server_provider(s) == prov]
            if self.server_mode.startswith("only:"):
                target_name = self.server_mode[5:]
                pinned = [s for s in enabled if s.name == target_name]
                return pinned if pinned else [enabled[0]]
            return [enabled[0]]


    def max_concurrency_for(self, srv: BackendServer) -> int:
        prov = detect_server_provider(srv)
        if prov == "cloudflare":
            return 120
        if prov in ("render", "snapdeploy"):
            return 48
        return 64

    def acquire_slot(self, srv: BackendServer | None, timeout: float = 8.0) -> bool:
        if srv is None:
            return True
        limit = self.max_concurrency_for(srv)
        deadline = time.time() + timeout
        with self.lock:
            while srv.active >= limit:
                rem = deadline - time.time()
                if rem <= 0:
                    return False
                self.lock.wait(timeout=min(rem, 0.15))
            srv.active += 1
            return True

    def release_slot(self, srv: BackendServer | None):
        if srv is None:
            return
        with self.lock:
            srv.active = max(0, srv.active - 1)
            self.lock.notify_all()

    def report_success(self, srv: BackendServer | None, latency_ms: int):
        if srv is None:
            return
        with self.lock:
            srv.total_requests += 1
            srv.consecutive_failures = 0
            srv.cooldown_until = 0.0
            srv.last_error = ""
            if latency_ms > 0:
                srv.last_latency_ms = latency_ms

    def report_failure(self, srv: BackendServer | None, err_msg: str):
        if srv is None:
            return
        with self.lock:
            srv.total_requests += 1
            srv.errors += 1
            srv.consecutive_failures += 1
            srv.last_error = err_msg[:80]
            cd = min(180, 20 * (2 ** (srv.consecutive_failures - 1)))
            srv.cooldown_until = time.time() + cd


class GasAccount:
    def __init__(
        self,
        name: str,
        url: str,
        enabled: bool = True,
        daily_requests: int = 0,
        quota_date: str = "",
    ):
        self.name = name
        self.url = url.strip()
        self.enabled = enabled
        self.active = 0
        self.total_requests = 0
        self.daily_requests = daily_requests if quota_date == today_str() else 0
        self.quota_date = today_str()
        self.rpm_timestamps: deque[float] = deque()
        self.errors = 0
        self.consecutive_rate_limits = 0
        self.cooldown_until = 0.0
        self.last_latency_ms = 0
        self.last_error = ""

    def roll_day_if_needed(self):
        t = today_str()
        if self.quota_date != t:
            self.quota_date = t
            self.daily_requests = 0
            self.consecutive_rate_limits = 0
            self.cooldown_until = 0.0

    def prune_rpm(self, now: float):
        cutoff = now - 60.0
        while self.rpm_timestamps and self.rpm_timestamps[0] < cutoff:
            self.rpm_timestamps.popleft()

    def to_dict(self):
        self.roll_day_if_needed()
        return {
            "name": self.name,
            "url": self.url,
            "enabled": self.enabled,
            "daily_requests": self.daily_requests,
            "quota_date": self.quota_date,
        }

    def status_label(self, daily_limit: int) -> str:
        self.roll_day_if_needed()
        if not self.enabled:
            return "Disabled"
        if self.daily_requests >= daily_limit:
            return "Daily Limit"
        now = time.time()
        if now < self.cooldown_until:
            rem = int(self.cooldown_until - now) + 1
            return f"Cooldown ({rem}s)"
        if self.active > 0:
            return "Active"
        if self.last_error:
            return "Warning"
        return "Ready"


class GasAccountPool:
    """
    Scheduler across 1..N Google Apps Script accounts.
    Modes:
      - "fair": Equalizes (active, daily_requests, recent_rpm) across all accounts (1/N).
      - "priority": Sticks to #1 until it hits concurrent/RPM/daily limit or cooldown, then falls back to #2, #3...
    """

    def __init__(self, max_concurrent: int = 18, daily_limit: int = 14000, max_rpm: int = 120, gas_mode: str = "fair"):
        self.lock = threading.Condition()
        self.accounts: list[GasAccount] = []
        self.max_concurrent = max(1, min(int(max_concurrent), 999))
        self.daily_limit = max(100, int(daily_limit))
        self.max_rpm = max(10, int(max_rpm))
        self.gas_mode = gas_mode  # "fair" or "priority"

    def update_limits(self, max_concurrent: int, daily_limit: int, max_rpm: int, gas_mode: str | None = None):
        with self.lock:
            self.max_concurrent = max(1, min(int(max_concurrent), 999))
            self.daily_limit = max(100, int(daily_limit))
            self.max_rpm = max(10, int(max_rpm))
            if gas_mode in ("fair", "priority"):
                self.gas_mode = gas_mode
            self.lock.notify_all()

    def load_accounts(self, items: list[dict]):
        with self.lock:
            self.accounts = [
                GasAccount(
                    name=item.get("name", f"Account {i + 1}"),
                    url=item.get("url", ""),
                    enabled=bool(item.get("enabled", True)),
                    daily_requests=int(item.get("daily_requests", 0)),
                    quota_date=str(item.get("quota_date", "")),
                )
                for i, item in enumerate(items)
                if item.get("url", "").strip()
            ]
            self.lock.notify_all()

    def acquire(self, timeout: float = 15.0, exclude: set = None) -> GasAccount:
        deadline = time.time() + timeout
        exclude = exclude or set()
        with self.lock:
            while True:
                now = time.time()
                for acc in self.accounts:
                    acc.roll_day_if_needed()
                    acc.prune_rpm(now)

                candidates = [
                    acc
                    for acc in self.accounts
                    if acc.enabled
                    and acc.url
                    and acc not in exclude
                    and now >= acc.cooldown_until
                    and acc.daily_requests < self.daily_limit
                    and acc.active < self.max_concurrent
                    and len(acc.rpm_timestamps) < self.max_rpm
                ]
                if candidates:
                    if self.gas_mode == "priority":
                        chosen = candidates[0]
                    else:
                        chosen = min(
                            candidates,
                            key=lambda a: (a.active, a.daily_requests, len(a.rpm_timestamps), a.total_requests),
                        )
                    chosen.active += 1
                    chosen.total_requests += 1
                    chosen.daily_requests += 1
                    chosen.rpm_timestamps.append(now)
                    return chosen

                if not any(acc.enabled and acc.url for acc in self.accounts):
                    raise RuntimeError("No enabled GAS accounts available")

                if not any(
                    acc.enabled and acc.url and acc.daily_requests < self.daily_limit
                    for acc in self.accounts
                ):
                    raise RuntimeError("No GAS accounts with remaining daily quota")

                if not any(
                    acc.enabled
                    and acc.url
                    and acc not in exclude
                    and acc.daily_requests < self.daily_limit
                    for acc in self.accounts
                ):
                    raise RuntimeError("No alternative GAS accounts available")

                remaining = deadline - now
                if remaining <= 0:
                    raise RuntimeError("All GAS accounts are temporarily paced or in cooldown")
                self.lock.wait(timeout=min(remaining, 0.35))

    def release(self, acc: GasAccount, ok: bool, latency_ms: int, rate_limited: bool = False, err_msg: str = ""):
        with self.lock:
            acc.active = max(0, acc.active - 1)
            if latency_ms > 0:
                acc.last_latency_ms = latency_ms
            if ok:
                acc.consecutive_rate_limits = 0
                acc.last_error = ""
            else:
                acc.errors += 1
                acc.last_error = err_msg[:80]
                if rate_limited:
                    acc.consecutive_rate_limits += 1
                    cd = min(120, 15 * (2 ** (acc.consecutive_rate_limits - 1)))
                    acc.cooldown_until = time.time() + cd
                else:
                    acc.cooldown_until = max(acc.cooldown_until, time.time() + 3.0)
            self.lock.notify_all()


class NoPostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """GAS returns 302 after executing doPost; follow it with GET as Google expects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code in (301, 302, 303, 307, 308):
            return urllib.request.Request(newurl, method="GET")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoPostRedirectHandler())
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_to_gas(url: str, payload: dict, timeout: float = 30.0) -> tuple[dict, bool]:
    data = json.dumps(payload).encode("utf-8")
    try:
        code, raw = _KA_POOL.post_gas_keepalive(url, data, timeout=timeout)
        if code == 429 or "too many" in raw.lower():
            return {"ok": False, "error": f"HTTP {code} rate limit"}, True
        if code < 200 or code >= 300:
            return {"ok": False, "error": f"HTTP {code}: {raw[:160]}"}, False
    except Exception as e:
        return {"ok": False, "error": str(e)}, False

    stripped = raw.lstrip()
    if stripped.startswith("<"):
        low = raw.lower()
        rate_limited = any(
            kw in low
            for kw in (
                "too many times",
                "too many scripts",
                "exceeded maximum",
                "service invoked",
                "quota",
                "429",
            )
        )
        return {"ok": False, "error": "GAS HTML error (quota or auth)"}, rate_limited

    try:
        parsed = json.loads(raw)
        return parsed, False
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Invalid JSON from GAS: {raw[:120]}"}, False


class KeepAlivePool:
    """Thread-safe persistent HTTP/HTTPS connection pool to avoid repeated TLS handshakes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._conns: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
        self._ssl_ctx = ssl.create_default_context()

    def _acquire(self, scheme: str, host: str, port: int, timeout: float):
        key = (scheme, host, port)
        now = time.time()
        stale = []
        chosen = None
        with self._lock:
            bucket = self._conns.get(key)
            while bucket:
                conn, ts = bucket.pop()
                if now - ts <= 15.0:
                    chosen = conn
                    break
                stale.append(conn)
        for c in stale:
            try:
                c.close()
            except Exception:
                pass
        if chosen is not None:
            chosen.timeout = timeout
            return chosen, True
        if scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=self._ssl_ctx), False
        return http.client.HTTPConnection(host, port, timeout=timeout), False

    def _release(self, scheme: str, host: str, port: int, conn):
        key = (scheme, host, port)
        now = time.time()
        with self._lock:
            bucket = self._conns.setdefault(key, [])
            if len(bucket) < 64:
                bucket.append((conn, now))
                return
        try:
            conn.close()
        except Exception:
            pass

    def _request_once(self, method: str, url: str, body_bytes: bytes | None, headers: dict, timeout: float) -> tuple[int, str, str]:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        for attempt in range(2):
            conn, is_reused = self._acquire(scheme, host, port, timeout)
            try:
                req_headers = {**headers, "Connection": "keep-alive"}
                if body_bytes is not None and method == "POST":
                    conn.request("POST", path, body=body_bytes, headers=req_headers)
                else:
                    req_headers.pop("Content-Type", None)
                    conn.request(method, path, headers=req_headers)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
                location = resp.getheader("Location") or ""
                if location and location.startswith("/"):
                    location = f"{scheme}://{host}:{port}{location}" if parsed.port else f"{scheme}://{host}{location}"
                if (resp.getheader("Connection") or "").lower() == "close":
                    try:
                        conn.close()
                    except Exception:
                        pass
                else:
                    self._release(scheme, host, port, conn)
                return status, raw, location
            except (http.client.HTTPException, ssl.SSLError, OSError, BrokenPipeError):
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 0 and (is_reused or method != "POST"):
                    continue
                raise

    def post_json(self, url: str, body_bytes: bytes, headers: dict, timeout: float = 30.0) -> tuple[int, str]:
        status, raw, _ = self._request_once("POST", url, body_bytes, headers, timeout)
        return status, raw

    def post_gas_keepalive(self, url: str, body_bytes: bytes, timeout: float = 30.0) -> tuple[int, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "FreeDnet-Client/2.0"}
        status, raw, loc = self._request_once("POST", url, body_bytes, headers, timeout)
        hops = 0
        while status in (301, 302, 303, 307, 308) and loc and hops < 4:
            hops += 1
            status, raw, loc = self._request_once("GET", loc, None, {"User-Agent": "FreeDnet-Client/2.0"}, timeout)
        return status, raw


_KA_POOL = KeepAlivePool()


def post_direct_to_server(server_url: str, payload: dict, auth_key: str, timeout: float = 30.0) -> dict:
    tunnel_url = normalize_tunnel_url(server_url)
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Auth-Key": auth_key,
        "User-Agent": "FreeDnet-Client/2.0",
    }
    try:
        code, raw = _KA_POOL.post_json(tunnel_url, data, headers, timeout=timeout)
        if 200 <= code < 300:
            return json.loads(raw)
        try:
            parsed = json.loads(raw)
            if code >= 500 and not parsed.get("targetError"):
                parsed["upstreamError"] = True
            return parsed
        except Exception:
            return {
                "ok": False,
                "upstreamError": code >= 500 or code == 429,
                "error": f"HTTP {code}",
                "detail": raw[:180],
            }
    except Exception as e:
        return {"ok": False, "upstreamError": True, "error": str(e)}
