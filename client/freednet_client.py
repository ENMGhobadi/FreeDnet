#!/usr/bin/env python3
"""
FreeDnet Client (Windows 11 Next-Gen PySide6 / Qt 6 Dark Fluent UI + SOCKS5/HTTP Proxy)
- Single unified client: PySide6 UI + Multi-GAS & Multi-Server Smart Router
- GAS Quota Presets (Low / Medium / High / X-High for free Gmail accounts)
- GAS Fair-Share (1/N) Toggle + Priority (#1 -> #2) Failover
- Backend Mode: Priority (#1 -> #2), Fair-Share (1/N), OR Only: <Server>
- Smart Keep-Awake (10m): keeps #1 awake in Priority/Only mode, or ALL enabled servers in Fair-Share mode
"""

import base64
from collections import deque
import ctypes
import datetime
import gzip
import http.client
import io
import json
import os
import queue
import select
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib



def _resource_path(rel_path: str) -> str:
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, rel_path))
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), rel_path))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path))
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def _resolve_base_dir() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.basename(exe_dir).lower() == "dist":
            parent_dir = os.path.dirname(exe_dir)
            if os.path.exists(os.path.join(parent_dir, "freednet_client.py")) or os.path.exists(os.path.join(parent_dir, "config.json")):
                return parent_dir
        if os.path.exists(os.path.join(exe_dir, "config.json")):
            return exe_dir
        appdata_dir = os.path.join(os.environ.get("APPDATA", exe_dir), "FreeDnet")
        os.makedirs(appdata_dir, exist_ok=True)
        return appdata_dir
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _resolve_base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
XRAY_DIR = os.path.join(BASE_DIR, "xray")
XRAY_EXE = os.path.join(XRAY_DIR, "xray.exe")
WINTUN_DLL = os.path.join(XRAY_DIR, "wintun.dll")
XRAY_TUN_CFG = os.path.join(XRAY_DIR, "tun_config.json")
XRAY_ZIP_URL = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip"
WINTUN_ZIP_URL = "https://www.wintun.net/builds/wintun-0.14.1.zip"

COUNTRY_CHECK_ENDPOINTS = [
    ("ipwho.is", "https://ipwho.is/"),
    ("ipapi.co", "https://ipapi.co/json/"),
    ("api.ip.sb", "https://api.ip.sb/geoip"),
    ("ipinfo.io", "https://ipinfo.io/json"),
]


def fmt_bytes(b: float) -> str:
    if b < 1024:
        return f"{b:.0f} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


def fmt_rate(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 * 1024):.2f} MB/s"


def is_windows_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def set_windows_system_proxy(enable: bool, host: str = "127.0.0.1", port: int = 1080) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.*;10.*;172.16.*;192.168.*;<local>")
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            else:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH
        return True
    except Exception:
        return False

# Free Gmail Google Apps Script limits: 30 concurrent, 20,000 UrlFetch/day
GAS_QUOTA_PRESETS = {
    "Low (Safe: 10 conc | 8k/day | 60 rpm)": (10, 8000, 60),
    "Medium (Balanced: 18 conc | 14k/day | 120 rpm)": (18, 14000, 120),
    "High (Fast: 24 conc | 18k/day | 180 rpm)": (24, 18000, 180),
    "X-High (Max Free: 28 conc | 19.5k/day | 240 rpm)": (28, 19500, 240),
}
DEFAULT_PRESET = "Medium (Balanced: 18 conc | 14k/day | 120 rpm)"

DEFAULT_CONFIG = {
    "auth_key": "change-me-freednet-key",
    "listen_host": "127.0.0.1",
    "listen_port": 1080,
    "route_mode": "gas",       # "gas" or "direct"
    "gas_mode": "fair",        # "fair" (1/N) or "priority" (#1 -> #2)
    "server_mode": "priority", # "priority" (#1 -> #2), "fair" (1/N), or "only:<name>"
    "quota_preset": DEFAULT_PRESET,
    "keep_awake": True,
    "theme": "Midnight Dark (Default)",
    "language": "English",
    "accounts": [],
    "servers": [],
}


def today_str() -> str:
    return datetime.date.today().isoformat()


def enable_windows_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def normalize_tunnel_url(raw_url: str) -> str:
    trimmed = raw_url.strip().rstrip("/")
    if not trimmed:
        return ""
    return trimmed if trimmed.endswith("/tunnel") else f"{trimmed}/tunnel"


class BackendServer:
    def __init__(self, name: str, url: str, enabled: bool = True):
        self.name = name
        self.url = url.strip().rstrip("/")
        self.enabled = enabled
        self.total_requests = 0
        self.errors = 0
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.last_latency_ms = 0
        self.last_error = ""

    def to_dict(self):
        return {"name": self.name, "url": self.url, "enabled": self.enabled}

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
        self.lock = threading.Lock()
        self.servers: list[BackendServer] = []
        self.server_mode: str = "priority"

    def load_servers(self, items: list[dict], server_mode: str = "priority"):
        with self.lock:
            self.servers = [
                BackendServer(
                    name=item.get("name", f"Server {i + 1}"),
                    url=item.get("url", ""),
                    enabled=bool(item.get("enabled", True)),
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

            if self.server_mode.startswith("only:"):
                target_name = self.server_mode[5:]
                pinned = [s for s in enabled if s.name == target_name]
                if pinned:
                    return pinned

            healthy = [s for s in enabled if now >= s.cooldown_until]
            cooldown = sorted([s for s in enabled if now < s.cooldown_until], key=lambda s: s.cooldown_until)

            if self.server_mode == "fair":
                healthy.sort(key=lambda s: (s.consecutive_failures, s.total_requests))
            return healthy + cooldown

    def get_keep_awake_targets(self, require_explicit: bool = False) -> list[BackendServer]:
        with self.lock:
            enabled = [s for s in self.servers if s.enabled and s.url]
            if not enabled:
                return []
            if self.server_mode == "fair":
                return list(enabled)
            if self.server_mode.startswith("only:"):
                target_name = self.server_mode[5:]
                pinned = [s for s in enabled if s.name == target_name]
                return pinned if pinned else [enabled[0]]
            return [enabled[0]]

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
        self.max_concurrent = max(1, min(int(max_concurrent), 28))
        self.daily_limit = max(100, int(daily_limit))
        self.max_rpm = max(10, int(max_rpm))
        self.gas_mode = gas_mode  # "fair" or "priority"

    def update_limits(self, max_concurrent: int, daily_limit: int, max_rpm: int, gas_mode: str | None = None):
        with self.lock:
            self.max_concurrent = max(1, min(int(max_concurrent), 28))
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
        with self._lock:
            bucket = self._conns.get(key)
            if bucket:
                conn = bucket.pop()
                conn.timeout = timeout
                return conn
        if scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=self._ssl_ctx)
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def _release(self, scheme: str, host: str, port: int, conn):
        key = (scheme, host, port)
        with self._lock:
            bucket = self._conns.setdefault(key, [])
            if len(bucket) < 48:
                bucket.append(conn)
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
            conn = self._acquire(scheme, host, port, timeout)
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
            except (http.client.HTTPException, OSError, BrokenPipeError):
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 0:
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
            if code >= 500:
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


class FreeDnetProxyServer:
    def __init__(
        self,
        pool: GasAccountPool,
        server_pool: ServerPool,
        auth_key: str,
        host: str,
        port: int,
        log_fn,
        route_mode: str = "gas",
        keep_awake: bool = True,
    ):
        self.pool = pool
        self.server_pool = server_pool
        self.auth_key = auth_key
        self.host = host
        self.port = port
        self.log = log_fn
        self.route_mode = route_mode  # "gas" or "direct"
        self.keep_awake = keep_awake

        self.running = False
        self.server_sock = None
        self.active_tunnels = 0
        self.tx_bytes = 0
        self.rx_bytes = 0
        self._stat_lock = threading.Lock()
        self._tunnel_sem = threading.Semaphore(96)
        self._gas_open_sem = threading.Semaphore(16)
        self._last_rl_log = 0.0

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(128)
        self.server_sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._keep_awake_loop, daemon=True).start()
        mode_lbl = "Direct" if self.route_mode == "direct" else f"GAS:{self.pool.gas_mode}"
        self.log(f"Proxy listening on {self.host}:{self.port} [{mode_lbl} | Backend: {self.server_pool.server_mode}]")

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
        self.log("Proxy stopped.")

    def _send_request(
        self,
        action: str,
        body: dict,
        backend: BackendServer | None = None,
        retries: int = 1,
    ) -> dict:
        payload = {"authKey": self.auth_key, "action": action, **body}

        if self.route_mode == "direct":
            if backend is None:
                return {"ok": False, "error": "No backend server configured for Direct mode", "upstreamError": True}
            t0 = time.time()
            res = post_direct_to_server(backend.url, payload, self.auth_key)
            lat = int((time.time() - t0) * 1000)
            if res.get("ok"):
                self.server_pool.report_success(backend, lat)
                return res
            if res.get("upstreamError"):
                self.server_pool.report_failure(backend, res.get("error", "Upstream error"))
            return res

        if backend is not None:
            payload["serverUrl"] = backend.url

        excluded = set()
        last_err = "Unknown error"
        acquire_timeout = 6.0 if action == "open" else 15.0

        open_acquired = False
        if action == "open":
            open_acquired = self._gas_open_sem.acquire(timeout=4.0)
            if not open_acquired:
                return {"ok": False, "error": "GAS open queue busy (burst)"}

        try:
            for attempt in range(retries + 1):
                try:
                    acc = self.pool.acquire(timeout=acquire_timeout, exclude=excluded)
                except RuntimeError as e:
                    err_str = str(e)
                    if attempt > 0 and err_str == "No alternative GAS accounts available":
                        return {"ok": False, "error": last_err}
                    return {"ok": False, "error": err_str}

                t0 = time.time()
                res, rate_limited = post_to_gas(acc.url, payload)
                lat = int((time.time() - t0) * 1000)

                if res.get("ok"):
                    self.pool.release(acc, ok=True, latency_ms=lat)
                    self.server_pool.report_success(backend, lat)
                    return res

                last_err = res.get("error", "GAS error")
                is_upstream_err = bool(res.get("upstreamError")) or last_err.startswith("Upstream HTTP")
                if is_upstream_err and backend is not None:
                    self.pool.release(acc, ok=True, latency_ms=lat)
                    self.server_pool.report_failure(backend, last_err)
                    return {"ok": False, "error": last_err, "detail": res.get("detail", ""), "upstreamError": True}

                self.pool.release(acc, ok=False, latency_ms=lat, rate_limited=rate_limited, err_msg=last_err)
                if rate_limited:
                    now_rl = time.time()
                    if now_rl - self._last_rl_log >= 10.0:
                        self._last_rl_log = now_rl
                        self.log(f"GAS Rate-Limit [{acc.name}]: {last_err} (entering cooldown)")
                excluded.add(acc)
                if not rate_limited and "Session not found" in last_err:
                    break

            return {"ok": False, "error": last_err}
        finally:
            if open_acquired:
                self._gas_open_sem.release()

    def _open_with_failover(self, host: str, port: int, init_b64: str) -> tuple[dict, BackendServer | None]:
        candidates = self.server_pool.get_candidates(require_explicit=(self.route_mode == "direct"))
        if not candidates:
            return {"ok": False, "error": "No enabled backend servers available"}, None

        last_res = {"ok": False, "error": "No backend servers available"}
        for srv in candidates:
            res = self._send_request(
                "open",
                {"targetHost": host, "targetPort": port, "data": init_b64},
                backend=srv,
                retries=1,
            )
            if res.get("ok"):
                return res, srv
            last_res = res
            if srv is not None and len(candidates) > 1 and res.get("upstreamError"):
                detail = f" ({res.get('detail', '')[:80]})" if res.get("detail") else ""
                self.log(f"Backend [{srv.name}] failed ({res.get('error')}{detail}), failing over...")
                continue
            if not res.get("upstreamError"):
                break
        return last_res, None

    def _keep_awake_loop(self):
        """
        Every 10 minutes:
        - In Priority / Only mode: pings ONLY the #1 (or pinned) backend server.
        - In Fair-Share (1/N) mode: pings ALL enabled backend servers so none sleep.
        """
        interval = 600
        elapsed = 0
        while self.running:
            time.sleep(2)
            elapsed += 2
            if not self.running:
                break
            if elapsed < interval:
                continue
            elapsed = 0
            if not self.keep_awake:
                continue
            try:
                targets = self.server_pool.get_keep_awake_targets(require_explicit=(self.route_mode == "direct"))
                for srv in targets:
                    if not self.running:
                        break
                    t0 = time.time()
                    res = self._send_request("ping", {}, backend=srv, retries=0)
                    lat = int((time.time() - t0) * 1000)
                    if res.get("ok"):
                        self.log(f"Keep-Awake OK [{srv.name}]: {lat} ms")
                    else:
                        self.log(f"Keep-Awake FAILED [{srv.name}]: {res.get('error')}")
            except Exception:
                pass

    def _accept_loop(self):
        while self.running and self.server_sock:
            try:
                client_sock, _ = self.server_sock.accept()
                client_sock.settimeout(15.0)
                if not self._tunnel_sem.acquire(blocking=False):
                    try:
                        client_sock.close()
                    except Exception:
                        pass
                    continue
                threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, client_sock: socket.socket):
        session_id = None
        pinned_backend = None
        with self._stat_lock:
            self.active_tunnels += 1
        try:
            first = client_sock.recv(1, socket.MSG_PEEK)
            if not first:
                return

            is_socks5 = first[0] == 0x05
            is_http_connect = False
            if is_socks5:
                target = self._handshake_socks5(client_sock)
                initial_data = b""
            else:
                target, initial_data, is_http_connect = self._handshake_http(client_sock)

            if not target:
                return

            host, port = target
            proto = "SOCKS5" if is_socks5 else "HTTP"
            self.log(f"{proto} -> {host}:{port}")
            init_b64 = base64.b64encode(initial_data).decode("ascii") if initial_data else ""
            if initial_data:
                with self._stat_lock:
                    self.tx_bytes += len(initial_data)

            open_res, pinned_backend = self._open_with_failover(host, port, init_b64)
            if not open_res.get("ok"):
                self.log(f"Open failed {host}:{port} -> {open_res.get('error')}")
                try:
                    if is_socks5:
                        client_sock.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                    elif is_http_connect:
                        client_sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                except OSError:
                    pass
                return

            session_id = open_res["sessionId"]
            if is_socks5:
                client_sock.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            elif is_http_connect:
                client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

            if open_res.get("data"):
                raw_out = base64.b64decode(open_res["data"])
                if raw_out:
                    client_sock.sendall(raw_out)
                    with self._stat_lock:
                        self.rx_bytes += len(raw_out)

            if open_res.get("closed"):
                return

            self._relay_loop(client_sock, session_id, pinned_backend)
        except Exception:
            pass
        finally:
            if session_id:
                try:
                    self._send_request("close", {"sessionId": session_id}, backend=pinned_backend, retries=0)
                except Exception:
                    pass
            try:
                client_sock.close()
            except Exception:
                pass
            with self._stat_lock:
                self.active_tunnels = max(0, self.active_tunnels - 1)
            self._tunnel_sem.release()

    def _handshake_socks5(self, sock: socket.socket):
        header = self._recv_exact(sock, 2)
        if not header or header[0] != 0x05:
            return None
        nmethods = header[1]
        self._recv_exact(sock, nmethods)
        sock.sendall(b"\x05\x00")

        req = self._recv_exact(sock, 4)
        if not req or req[1] != 0x01:
            if req and req[1] == 0x03:
                self.log("SOCKS5 UDP Associate ignored (TCP only)")
            sock.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            return None

        atyp = req[3]
        if atyp == 0x01:
            addr = socket.inet_ntoa(self._recv_exact(sock, 4))
        elif atyp == 0x03:
            ln = self._recv_exact(sock, 1)[0]
            addr = self._recv_exact(sock, ln).decode("utf-8", errors="replace")
        elif atyp == 0x04:
            addr = socket.inet_ntop(socket.AF_INET6, self._recv_exact(sock, 16))
        else:
            sock.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            return None

        port = struct.unpack("!H", self._recv_exact(sock, 2))[0]
        return (addr, port)

    def _handshake_http(self, sock: socket.socket):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return None, b"", False
            buf += chunk
            if len(buf) > 65536:
                return None, b"", False

        head, rest = buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if not lines:
            return None, b"", False

        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None, b"", False

        method, target = parts[0].upper(), parts[1]
        if method == "CONNECT":
            if ":" in target:
                host, port_s = target.rsplit(":", 1)
                port = int(port_s)
            else:
                host, port = target, 443
            return (host, port), rest, True
        else:
            host = ""
            port = 80
            for ln in lines[1:]:
                if ln.lower().startswith("host:"):
                    hval = ln.split(":", 1)[1].strip()
                    if ":" in hval:
                        host, p_s = hval.rsplit(":", 1)
                        port = int(p_s)
                    else:
                        host = hval
                    break
            if not host:
                return None, b"", False
            return (host, port), buf, False

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise EOFError("Socket closed")
            data += chunk
        return data

    def _encode_payload(self, raw: bytes) -> dict:
        looks_like_tls = len(raw) >= 2 and 0x14 <= raw[0] <= 0x17 and raw[1] == 0x03
        if not looks_like_tls and 512 < len(raw) <= 524288:
            gz = gzip.compress(raw, compresslevel=1)
            if len(gz) < len(raw):
                return {"dataZ": base64.b64encode(gz).decode("ascii")}
        return {"data": base64.b64encode(raw).decode("ascii")}

    def _decode_payload(self, res: dict) -> bytes:
        if res.get("dataZ"):
            return gzip.decompress(base64.b64decode(res["dataZ"]))
        if res.get("data"):
            return base64.b64decode(res["data"])
        return b""

    def _relay_loop(self, client_sock: socket.socket, session_id: str, pinned_backend: BackendServer | None):
        closed_evt = threading.Event()
        last_activity = [time.time()]

        def tx_worker():
            try:
                client_sock.settimeout(15.0)
                while self.running and not closed_evt.is_set():
                    rlist, _, _ = select.select([client_sock], [], [], 0.1)
                    if not rlist:
                        continue
                    outgoing = b""
                    try:
                        chunk = client_sock.recv(65536)
                        if not chunk:
                            closed_evt.set()
                            return
                        outgoing = chunk
                        while len(outgoing) < 262144:
                            r2, _, _ = select.select([client_sock], [], [], 0.005)
                            if not r2:
                                break
                            more = client_sock.recv(65536)
                            if not more:
                                break
                            outgoing += more
                    except OSError:
                        closed_evt.set()
                        return

                    if not outgoing:
                        continue

                    last_activity[0] = time.time()
                    with self._stat_lock:
                        self.tx_bytes += len(outgoing)
                    body = {"sessionId": session_id, "waitMs": 0, "txOnly": True, **self._encode_payload(outgoing)}
                    res = self._send_request("sync", body, backend=pinned_backend, retries=1)
                    if not res.get("ok"):
                        closed_evt.set()
                        return
                    extra_rx = self._decode_payload(res)
                    if extra_rx:
                        last_activity[0] = time.time()
                        try:
                            client_sock.sendall(extra_rx)
                            with self._stat_lock:
                                self.rx_bytes += len(extra_rx)
                        except OSError:
                            closed_evt.set()
                            return
                    if res.get("closed"):
                        closed_evt.set()
                        return
            except Exception:
                closed_evt.set()

        tx_thread = threading.Thread(target=tx_worker, daemon=True)
        tx_thread.start()

        prev_had_rx = False
        idle_cycles = 0
        try:
            while self.running and not closed_evt.is_set():
                if prev_had_rx:
                    wait_ms = 120
                else:
                    if time.time() - last_activity[0] > 90:
                        return
                    idle_cycles += 1
                    if self.active_tunnels > 6:
                        wait_ms = 1200
                    else:
                        wait_ms = 1800 if idle_cycles == 1 else (3500 if idle_cycles == 2 else 6000)

                res = self._send_request(
                    "sync",
                    {"sessionId": session_id, "waitMs": wait_ms, "acceptGzip": True},
                    backend=pinned_backend,
                    retries=1,
                )
                if not res.get("ok"):
                    return

                raw_out = self._decode_payload(res)
                prev_had_rx = len(raw_out) > 0
                if raw_out:
                    idle_cycles = 0
                    last_activity[0] = time.time()
                    try:
                        client_sock.sendall(raw_out)
                        with self._stat_lock:
                            self.rx_bytes += len(raw_out)
                    except OSError:
                        return

                if res.get("closed"):
                    return
        finally:
            closed_evt.set()
            tx_thread.join(timeout=0.5)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

THEMES = {
    "Midnight Dark (Default)": {
        "is_light": False,
        "bg": "#090d16",
        "header": "#0d1322",
        "card": "#111827",
        "kpi": "#0f172a",
        "toolbar": "#0d1424",
        "input": "#0b1120",
        "console": "#050811",
        "border": "#1e293b",
        "input_border": "#293548",
        "text": "#e2e8f0",
        "title": "#ffffff",
        "muted": "#94a3b8",
        "label": "#cbd5e1",
        "accent1": "#2563eb",
        "accent2": "#0284c7",
        "accent_hover1": "#1d4ed8",
        "accent_hover2": "#0369a1",
        "accent_light": "#60a5fa",
        "badge_bg": "#1d4ed8",
        "badge_fg": "#dbeafe",
        "btn_bg": "#1e293b",
        "btn_hover": "#283548",
        "btn_border": "#334155",
        "sel_bg": "#1e3a8a",
        "alt_row": "#0f172a",
        "hdr_bg": "#131c31",
        "pill_stop_bg": "#2a1218",
        "pill_stop_fg": "#f87171",
        "pill_stop_bd": "#7f1d1d",
        "pill_run_bg": "#052e1c",
        "pill_run_fg": "#34d399",
        "pill_run_bd": "#065f46",
    },
    "Slate Blue & Cool Gray": {
        "is_light": False,
        "bg": "#2b3340",
        "header": "#232a35",
        "card": "#333d4b",
        "kpi": "#2d3644",
        "toolbar": "#27303d",
        "input": "#252d39",
        "console": "#1f2630",
        "border": "#434f61",
        "input_border": "#4e5c70",
        "text": "#eef2f7",
        "title": "#ffffff",
        "muted": "#b4c0d0",
        "label": "#dce4ef",
        "accent1": "#3b82f6",
        "accent2": "#0ea5e9",
        "accent_hover1": "#2563eb",
        "accent_hover2": "#0284c7",
        "accent_light": "#7dd3fc",
        "badge_bg": "#2563eb",
        "badge_fg": "#eff6ff",
        "btn_bg": "#3b4656",
        "btn_hover": "#475468",
        "btn_border": "#56657c",
        "sel_bg": "#2563eb",
        "alt_row": "#2f3846",
        "hdr_bg": "#262e3a",
        "pill_stop_bg": "#442229",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#991b1b",
        "pill_run_bg": "#153d30",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Obsidian Orange & Black": {
        "is_light": False,
        "bg": "#09090b",
        "header": "#0f0f12",
        "card": "#141418",
        "kpi": "#111115",
        "toolbar": "#101014",
        "input": "#0c0c0f",
        "console": "#060608",
        "border": "#27272a",
        "input_border": "#3f3f46",
        "text": "#f4f4f5",
        "title": "#ffffff",
        "muted": "#a1a1aa",
        "label": "#e4e4e7",
        "accent1": "#f97316",
        "accent2": "#ea580c",
        "accent_hover1": "#ea580c",
        "accent_hover2": "#c2410c",
        "accent_light": "#fb923c",
        "badge_bg": "#c2410c",
        "badge_fg": "#ffedd5",
        "btn_bg": "#1f1f24",
        "btn_hover": "#2c2c33",
        "btn_border": "#3f3f46",
        "sel_bg": "#9a3412",
        "alt_row": "#111115",
        "hdr_bg": "#18181d",
        "pill_stop_bg": "#2c1215",
        "pill_stop_fg": "#f87171",
        "pill_stop_bd": "#7f1d1d",
        "pill_run_bg": "#0a2e1d",
        "pill_run_fg": "#34d399",
        "pill_run_bd": "#065f46",
    },
    "Emerald & Slate Gray": {
        "is_light": False,
        "bg": "#212826",
        "header": "#1a201e",
        "card": "#29322f",
        "kpi": "#242c2a",
        "toolbar": "#1e2523",
        "input": "#1c2220",
        "console": "#161b19",
        "border": "#384440",
        "input_border": "#465550",
        "text": "#e7f0ec",
        "title": "#ffffff",
        "muted": "#9fb3ac",
        "label": "#d2e0db",
        "accent1": "#10b981",
        "accent2": "#059669",
        "accent_hover1": "#059669",
        "accent_hover2": "#047857",
        "accent_light": "#34d399",
        "badge_bg": "#047857",
        "badge_fg": "#d1fae5",
        "btn_bg": "#313b38",
        "btn_hover": "#3c4844",
        "btn_border": "#4b5a55",
        "sel_bg": "#065f46",
        "alt_row": "#252d2b",
        "hdr_bg": "#1e2523",
        "pill_stop_bg": "#3b1c20",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#991b1b",
        "pill_run_bg": "#0d3b2c",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Warm Nescafe (Light)": {
        "is_light": True,
        "bg": "#f4ede4",
        "header": "#ebe1d5",
        "card": "#fcf8f3",
        "kpi": "#f7f1e9",
        "toolbar": "#efe6db",
        "input": "#ffffff",
        "console": "#faf5ee",
        "border": "#ddd0c0",
        "input_border": "#cbb9a4",
        "text": "#2c2119",
        "title": "#1f1610",
        "muted": "#776252",
        "label": "#4a382b",
        "accent1": "#b5693c",
        "accent2": "#965129",
        "accent_hover1": "#9e572e",
        "accent_hover2": "#7c4120",
        "accent_light": "#8f4b25",
        "badge_bg": "#965129",
        "badge_fg": "#fff7ed",
        "btn_bg": "#e7dbcc",
        "btn_hover": "#dccbb8",
        "btn_border": "#cbb79f",
        "sel_bg": "#d6b89c",
        "alt_row": "#f5ede3",
        "hdr_bg": "#eadfd1",
        "pill_stop_bg": "#fde8e8",
        "pill_stop_fg": "#b91c1c",
        "pill_stop_bd": "#fca5a5",
        "pill_run_bg": "#dcfce7",
        "pill_run_fg": "#15803d",
        "pill_run_bd": "#86efac",
    },
}


def build_qss(t: dict) -> str:
    return f"""
QMainWindow, QWidget#Root {{
    background-color: {t["bg"]};
    color: {t["text"]};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QFrame#HeaderBar {{
    background-color: {t["header"]};
    border-bottom: 1px solid {t["border"]};
}}
QFrame#Card {{
    background-color: {t["card"]};
    border: 1px solid {t["border"]};
    border-radius: 12px;
}}
QFrame#KpiCard {{
    background-color: {t["kpi"]};
    border: 1px solid {t["border"]};
    border-radius: 10px;
}}
QFrame#Toolbar {{
    background-color: {t["toolbar"]};
    border: 1px solid {t["border"]};
    border-radius: 8px;
}}
QLabel#BrandTitle {{
    color: {t["title"]};
    font-size: 20px;
    font-weight: 700;
}}
QLabel#ProBadge {{
    background-color: {t["badge_bg"]};
    color: {t["badge_fg"]};
    border-radius: 5px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#Muted {{
    color: {t["muted"]};
    font-size: 12px;
}}
QLabel#FieldLabel {{
    color: {t["label"]};
    font-weight: 600;
    font-size: 12px;
}}
QLabel#KpiTitle {{
    color: {t["muted"]};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#KpiValue {{
    color: {t["title"]};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#StatusPillStopped {{
    background-color: {t["pill_stop_bg"]};
    color: {t["pill_stop_fg"]};
    border: 1px solid {t["pill_stop_bd"]};
    border-radius: 12px;
    padding: 5px 12px;
    font-weight: 700;
    font-size: 12px;
}}
QLabel#StatusPillRunning {{
    background-color: {t["pill_run_bg"]};
    color: {t["pill_run_fg"]};
    border: 1px solid {t["pill_run_bd"]};
    border-radius: 12px;
    padding: 5px 12px;
    font-weight: 700;
    font-size: 12px;
}}
QLineEdit, QComboBox {{
    background-color: {t["input"]};
    color: {t["text"]};
    border: 1px solid {t["input_border"]};
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {t["accent1"]};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {t["accent_light"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {t["card"]};
    color: {t["text"]};
    border: 1px solid {t["btn_border"]};
    selection-background-color: {t["accent1"]};
}}
QPushButton {{
    background-color: {t["btn_bg"]};
    color: {t["text"]};
    border: 1px solid {t["btn_border"]};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {t["btn_hover"]};
    border-color: {t["accent_light"]};
}}
QPushButton#PrimaryBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t["accent1"]}, stop:1 {t["accent2"]});
    color: #ffffff;
    border: none;
    padding: 8px 18px;
    font-weight: 700;
}}
QPushButton#PrimaryBtn:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t["accent_hover1"]}, stop:1 {t["accent_hover2"]});
}}
QPushButton#DangerBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #dc2626, stop:1 #b91c1c);
    color: #ffffff;
    border: none;
    padding: 8px 18px;
    font-weight: 700;
}}
QPushButton#DangerBtn:hover {{
    background-color: #991b1b;
}}
QPushButton#SmallBtn {{
    padding: 5px 10px;
    font-size: 12px;
    border-radius: 6px;
}}
QRadioButton, QCheckBox {{
    color: {t["text"]};
    spacing: 7px;
    font-weight: 500;
}}
QTabWidget::pane {{
    border: 1px solid {t["border"]};
    border-radius: 10px;
    background-color: {t["card"]};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {t["header"]};
    color: {t["muted"]};
    border: 1px solid {t["border"]};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 9px 20px;
    margin-right: 4px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background-color: {t["card"]};
    color: {t["accent_light"]};
    border-bottom: 2px solid {t["accent1"]};
}}
QTableWidget {{
    background-color: {t["input"]};
    alternate-background-color: {t["alt_row"]};
    color: {t["text"]};
    gridline-color: {t["border"]};
    border: 1px solid {t["border"]};
    border-radius: 8px;
    selection-background-color: {t["sel_bg"]};
    selection-color: {t["title"]};
}}
QHeaderView::section {{
    background-color: {t["hdr_bg"]};
    color: {t["muted"]};
    padding: 8px;
    border: none;
    border-bottom: 1px solid {t["border"]};
    font-weight: 700;
    font-size: 12px;
}}
QTextEdit#LogConsole {{
    background-color: {t["console"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: 8px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    padding: 10px;
}}
QMenu {{
    background-color: {t["card"]};
    color: {t["text"]};
    border: 1px solid {t["btn_border"]};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 18px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: {t["accent1"]};
    color: #ffffff;
}}
QProgressBar#BusyBar {{
    background-color: {t["header"]};
    border: none;
    max-height: 3px;
    min-height: 3px;
}}
QProgressBar#BusyBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t["accent1"]}, stop:1 {t["accent_light"]});
    border-radius: 1px;
}}
QLabel#BusyLabel {{
    color: {t["accent_light"]};
    font-size: 12px;
    font-weight: 600;
    padding: 0 8px;
}}
QDialog {{
    background-color: {t["card"]};
    color: {t["text"]};
}}
"""

LANGUAGES = {
    "English": {
        "rtl": False,
        "win_title": "FreeDnet Pro — Smart Proxy Router",
        "subtitle": "Smart Tunnel & Multi-Relay Router",
        "stopped": "● STOPPED",
        "running": "● RUNNING",
        "sys_proxy_off": "Sys Proxy: OFF",
        "sys_proxy_on": "Sys Proxy: ON",
        "tun_off": "TUN Mode: OFF",
        "tun_on": "TUN Mode: ON",
        "tun_starting": "Starting TUN...",
        "start_proxy": "▶  Start Proxy",
        "stop_proxy": "■  Stop Proxy",
        "tab_overview": "Overview",
        "tab_gas": "GAS Accounts",
        "tab_servers": "Backend Servers",
        "tab_diag": "IP & Diagnostics",
        "tab_settings": "Settings & Logs",
        "kpi_tunnels": "ACTIVE TUNNELS",
        "kpi_speed": "LIVE SPEED (↓ / ↑)",
        "kpi_peak": "PEAK SPEED (↓ / ↑)",
        "kpi_traffic": "TOTAL USAGE (↓ / ↑)",
        "kpi_pool": "POOL HEALTH",
        "chain_mode": "Chain Mode:",
        "rb_gas": "Via Google Apps Script (Anti-Filtering)",
        "rb_direct": "Direct to Backend (Max Speed)",
        "exit_ip_unset": "Exit IP: Not checked yet",
        "recent_activity": "Recent Activity",
        "row_tip": "Tip: Right-click any account or server row for actions",
        "fair_share": "Fair-Share Quota (1/N Equal)",
        "free_quota": "Free Gmail Quota:",
        "add_gas": "+ Add GAS",
        "gas_hint": "Right-click any row to Edit, Enable/Disable, Reorder, Ping, or Remove.",
        "gas_headers": ["#", "Account", "Status", "Active", "Daily Quota", "Share %", "Latency", "GAS Web App URL"],
        "backend_mode": "Backend Mode:",
        "add_server": "+ Add Server",
        "deploy_render": "🚀 Deploy on Render",
        "deploy_snap": "🚀 Deploy on SnapDeploy",
        "deploy_cf": "🚀 Deploy on Cloudflare",
        "remove_btn": "Remove",
        "srv_headers": ["#", "Server Name", "Status", "Requests", "Errors", "Latency", "Server URL"],
        "diag_title": "Real Exit IP & Country Check",
        "diag_check_btn": "Check Country / IP Now",
        "kpi_exit_ip": "EXIT IP ADDRESS",
        "kpi_country": "COUNTRY / CITY",
        "kpi_isp": "ISP / ORGANIZATION",
        "conn_tests": "Connection Tests:",
        "ping_all": "Ping All Relays & Servers",
        "test_e2e": "Test Proxy (E2E)",
        "auth_key": "Auth Key:",
        "show": "Show",
        "paste": "Paste",
        "theme": "Theme:",
        "language": "Language:",
        "listen_ip": "Listen IP:",
        "port": "Port:",
        "keep_awake": "Keep Awake (10m)",
        "full_log": "Full Activity Log",
        "copy_logs": "Copy Logs",
        "clear": "Clear",
        "tray_open": "Open FreeDnet",
        "tray_start": "Start Proxy",
        "tray_stop": "Stop Proxy",
        "tray_exit": "Exit",
    },
    "فارسی (Persian)": {
        "rtl": True,
        "win_title": "FreeDnet Pro — مسیریاب هوشمند پروکسی",
        "subtitle": "تونل هوشمند و رله چندسروره",
        "stopped": "● متوقف",
        "running": "● متصل",
        "sys_proxy_off": "پروکسی سیستم: خاموش",
        "sys_proxy_on": "پروکسی سیستم: روشن",
        "tun_off": "حالت TUN: خاموش",
        "tun_on": "حالت TUN: روشن",
        "tun_starting": "در حال راه‌اندازی TUN...",
        "start_proxy": "▶  شروع پروکسی",
        "stop_proxy": "■  توقف پروکسی",
        "tab_overview": "نمای کلی",
        "tab_gas": "اکانت‌های GAS",
        "tab_servers": "سرورهای بک‌اند",
        "tab_diag": "آی‌پی و عیب‌یابی",
        "tab_settings": "تنظیمات و لاگ",
        "kpi_tunnels": "تونل‌های فعال",
        "kpi_speed": "سرعت لحظه‌ای (↓ / ↑)",
        "kpi_peak": "اوج سرعت (↓ / ↑)",
        "kpi_traffic": "مصرف کل (↓ / ↑)",
        "kpi_pool": "سلامت استخر",
        "chain_mode": "مسیر اتصال:",
        "rb_gas": "از طریق گوگل اسکریپت (ضد فیلتر)",
        "rb_direct": "مستقیم به بک‌اند (حداکثر سرعت)",
        "exit_ip_unset": "آی‌پی خروجی: بررسی نشده",
        "recent_activity": "فعالیت‌های اخیر",
        "row_tip": "نکته: برای مدیریت هر ردیف روی آن راست‌کلیک کنید",
        "fair_share": "تقسیم عادلانه سهمیه (1/N مساوی)",
        "free_quota": "سقف اکانت رایگان جیمیل:",
        "add_gas": "+ افزودن GAS",
        "gas_hint": "برای ویرایش، فعال/غیرفعال، جابه‌جایی، پینگ یا حذف روی ردیف راست‌کلیک کنید.",
        "gas_headers": ["#", "نام اکانت", "وضعیت", "فعال", "سهمیه روزانه", "سهم %", "تاخیر", "آدرس وب‌اپ GAS"],
        "backend_mode": "حالت بک‌اند:",
        "add_server": "+ افزودن سرور",
        "deploy_render": "🚀 دیپلوی روی Render",
        "deploy_snap": "🚀 دیپلوی روی SnapDeploy",
        "deploy_cf": "🚀 دیپلوی روی Cloudflare",
        "remove_btn": "حذف",
        "srv_headers": ["#", "نام سرور", "وضعیت", "درخواست‌ها", "خطاها", "تاخیر", "آدرس سرور"],
        "diag_title": "بررسی آی‌پی خروجی و کشور واقعی",
        "diag_check_btn": "بررسی کشور / آی‌پی",
        "kpi_exit_ip": "آدرس آی‌پی خروجی",
        "kpi_country": "کشور / شهر",
        "kpi_isp": "سرویس‌دهنده / سازمان",
        "conn_tests": "تست‌های اتصال:",
        "ping_all": "پینگ همه رله‌ها و سرورها",
        "test_e2e": "تست کامل پروکسی (E2E)",
        "auth_key": "کلید احراز هویت:",
        "show": "نمایش",
        "paste": "جای‌گذاری",
        "theme": "تم ظاهری:",
        "language": "زبان:",
        "listen_ip": "آی‌پی محلی:",
        "port": "پورت:",
        "keep_awake": "بیدار نگه‌داشتن (۱۰ دقیقه)",
        "full_log": "گزارش کامل فعالیت‌ها",
        "copy_logs": "کپی لاگ‌ها",
        "clear": "پاک کردن",
        "tray_open": "باز کردن FreeDnet",
        "tray_start": "شروع پروکسی",
        "tray_stop": "توقف پروکسی",
        "tray_exit": "خروج",
    },
    "中文 (Chinese)": {
        "rtl": False,
        "win_title": "FreeDnet Pro — 智能代理路由器",
        "subtitle": "智能隧道与多中继路由",
        "stopped": "● 已停止",
        "running": "● 运行中",
        "sys_proxy_off": "系统代理: 关",
        "sys_proxy_on": "系统代理: 开",
        "tun_off": "TUN 模式: 关",
        "tun_on": "TUN 模式: 开",
        "tun_starting": "正在启动 TUN...",
        "start_proxy": "▶  启动代理",
        "stop_proxy": "■  停止代理",
        "tab_overview": "概览",
        "tab_gas": "GAS 账号",
        "tab_servers": "后端服务器",
        "tab_diag": "IP 与诊断",
        "tab_settings": "设置与日志",
        "kpi_tunnels": "活跃隧道",
        "kpi_speed": "实时速度 (↓ / ↑)",
        "kpi_peak": "峰值速度 (↓ / ↑)",
        "kpi_traffic": "总流量 (↓ / ↑)",
        "kpi_pool": "节点池状态",
        "chain_mode": "连接模式:",
        "rb_gas": "通过 Google Apps Script (抗封锁)",
        "rb_direct": "直连后端 (最高速度)",
        "exit_ip_unset": "出口 IP: 尚未检测",
        "recent_activity": "最近活动",
        "row_tip": "提示: 右键点击任意账号或服务器行可执行操作",
        "fair_share": "配额均分 (1/N 均衡)",
        "free_quota": "免费 Gmail 配额:",
        "add_gas": "+ 添加 GAS",
        "gas_hint": "右键点击任意行可编辑、启用/禁用、排序、Ping 或删除。",
        "gas_headers": ["#", "账号", "状态", "并发", "每日配额", "占比 %", "延迟", "GAS Web App URL"],
        "backend_mode": "后端模式:",
        "add_server": "+ 添加服务器",
        "deploy_render": "🚀 部署到 Render",
        "deploy_snap": "🚀 部署到 SnapDeploy",
        "deploy_cf": "🚀 部署到 Cloudflare",
        "remove_btn": "删除",
        "srv_headers": ["#", "服务器名称", "状态", "请求数", "错误数", "延迟", "服务器 URL"],
        "diag_title": "真实出口 IP 与国家/地区检测",
        "diag_check_btn": "立即检测国家 / IP",
        "kpi_exit_ip": "出口 IP 地址",
        "kpi_country": "国家 / 城市",
        "kpi_isp": "运营商 / 组织",
        "conn_tests": "连接测试:",
        "ping_all": "Ping 所有中继与服务器",
        "test_e2e": "端到端代理测试 (E2E)",
        "auth_key": "认证密钥:",
        "show": "显示",
        "paste": "粘贴",
        "theme": "主题:",
        "language": "语言:",
        "listen_ip": "监听 IP:",
        "port": "端口:",
        "keep_awake": "保持唤醒 (10分钟)",
        "full_log": "完整活动日志",
        "copy_logs": "复制日志",
        "clear": "清空",
        "tray_open": "打开 FreeDnet",
        "tray_start": "启动代理",
        "tray_stop": "停止代理",
        "tray_exit": "退出",
    },
    "Español (Spanish)": {
        "rtl": False,
        "win_title": "FreeDnet Pro — Enrutador Proxy Inteligente",
        "subtitle": "Túnel Inteligente y Multi-Relay",
        "stopped": "● DETENIDO",
        "running": "● ACTIVO",
        "sys_proxy_off": "Proxy Sistema: OFF",
        "sys_proxy_on": "Proxy Sistema: ON",
        "tun_off": "Modo TUN: OFF",
        "tun_on": "Modo TUN: ON",
        "tun_starting": "Iniciando TUN...",
        "start_proxy": "▶  Iniciar Proxy",
        "stop_proxy": "■  Detener Proxy",
        "tab_overview": "Resumen",
        "tab_gas": "Cuentas GAS",
        "tab_servers": "Servidores Backend",
        "tab_diag": "IP y Diagnóstico",
        "tab_settings": "Ajustes y Registros",
        "kpi_tunnels": "TÚNELES ACTIVOS",
        "kpi_speed": "VELOCIDAD (↓ / ↑)",
        "kpi_peak": "PICO MÁXIMO (↓ / ↑)",
        "kpi_traffic": "USO TOTAL (↓ / ↑)",
        "kpi_pool": "ESTADO DEL POOL",
        "chain_mode": "Modo de Ruta:",
        "rb_gas": "Vía Google Apps Script (Anti-Bloqueo)",
        "rb_direct": "Directo al Backend (Máx. Velocidad)",
        "exit_ip_unset": "IP de Salida: Sin comprobar",
        "recent_activity": "Actividad Reciente",
        "row_tip": "Consejo: Haz clic derecho en cualquier fila para ver acciones",
        "fair_share": "Cuota Equitativa (1/N Igual)",
        "free_quota": "Cuota Gmail Gratis:",
        "add_gas": "+ Añadir GAS",
        "gas_hint": "Clic derecho en una fila para Editar, Activar/Desactivar, Ordenar, Ping o Eliminar.",
        "gas_headers": ["#", "Cuenta", "Estado", "Activo", "Cuota Diaria", "Uso %", "Latencia", "URL Web App GAS"],
        "backend_mode": "Modo Backend:",
        "add_server": "+ Añadir Servidor",
        "deploy_render": "🚀 Desplegar en Render",
        "deploy_snap": "🚀 Desplegar en SnapDeploy",
        "deploy_cf": "🚀 Desplegar en Cloudflare",
        "remove_btn": "Eliminar",
        "srv_headers": ["#", "Servidor", "Estado", "Peticiones", "Errores", "Latencia", "URL del Servidor"],
        "diag_title": "Comprobación Real de IP y País",
        "diag_check_btn": "Comprobar País / IP",
        "kpi_exit_ip": "DIRECCIÓN IP SALIDA",
        "kpi_country": "PAÍS / CIUDAD",
        "kpi_isp": "ISP / ORGANIZACIÓN",
        "conn_tests": "Pruebas de Conexión:",
        "ping_all": "Ping a Todos los Relays",
        "test_e2e": "Probar Proxy (E2E)",
        "auth_key": "Clave Auth:",
        "show": "Mostrar",
        "paste": "Pegar",
        "theme": "Tema:",
        "language": "Idioma:",
        "listen_ip": "IP Local:",
        "port": "Puerto:",
        "keep_awake": "Mantener Activo (10m)",
        "full_log": "Registro Completo de Actividad",
        "copy_logs": "Copiar Registros",
        "clear": "Limpiar",
        "tray_open": "Abrir FreeDnet",
        "tray_start": "Iniciar Proxy",
        "tray_stop": "Detener Proxy",
        "tray_exit": "Salir",
    },
    "Deutsch (German)": {
        "rtl": False,
        "win_title": "FreeDnet Pro — Intelligenter Proxy-Router",
        "subtitle": "Smart Tunnel & Multi-Relay Router",
        "stopped": "● GESTOPPT",
        "running": "● LÄUFT",
        "sys_proxy_off": "Sys-Proxy: AUS",
        "sys_proxy_on": "Sys-Proxy: AN",
        "tun_off": "TUN-Modus: AUS",
        "tun_on": "TUN-Modus: AN",
        "tun_starting": "TUN startet...",
        "start_proxy": "▶  Proxy starten",
        "stop_proxy": "■  Proxy stoppen",
        "tab_overview": "Übersicht",
        "tab_gas": "GAS-Konten",
        "tab_servers": "Backend-Server",
        "tab_diag": "IP & Diagnose",
        "tab_settings": "Einstellungen & Logs",
        "kpi_tunnels": "AKTIVE TUNNEL",
        "kpi_speed": "GESCHWINDIGKEIT (↓ / ↑)",
        "kpi_peak": "SPITZENWERT (↓ / ↑)",
        "kpi_traffic": "GESAMTVERBRAUCH (↓ / ↑)",
        "kpi_pool": "POOL-STATUS",
        "chain_mode": "Routing-Modus:",
        "rb_gas": "Über Google Apps Script (Anti-Zensur)",
        "rb_direct": "Direkt zum Backend (Max. Tempo)",
        "exit_ip_unset": "Ausgangs-IP: Nicht geprüft",
        "recent_activity": "Letzte Aktivitäten",
        "row_tip": "Tipp: Rechtsklick auf eine Zeile für weitere Aktionen",
        "fair_share": "Faire Verteilung (1/N Gleich)",
        "free_quota": "Gratis Gmail-Kontingent:",
        "add_gas": "+ GAS hinzufügen",
        "gas_hint": "Rechtsklick auf eine Zeile zum Bearbeiten, Aktivieren, Sortieren, Pingen oder Entfernen.",
        "gas_headers": ["#", "Konto", "Status", "Aktiv", "Tageslimit", "Anteil %", "Latenz", "GAS Web-App-URL"],
        "backend_mode": "Backend-Modus:",
        "add_server": "+ Server hinzufügen",
        "deploy_render": "🚀 Auf Render deployen",
        "deploy_snap": "🚀 Auf SnapDeploy deployen",
        "deploy_cf": "🚀 Auf Cloudflare deployen",
        "remove_btn": "Entfernen",
        "srv_headers": ["#", "Servername", "Status", "Anfragen", "Fehler", "Latenz", "Server-URL"],
        "diag_title": "Echte Ausgangs-IP & Länderprüfung",
        "diag_check_btn": "Land / IP jetzt prüfen",
        "kpi_exit_ip": "AUSGANGS-IP-ADRESSE",
        "kpi_country": "LAND / STADT",
        "kpi_isp": "ANBIETER / ORGANISATION",
        "conn_tests": "Verbindungstests:",
        "ping_all": "Alle Relays & Server pingen",
        "test_e2e": "Proxy testen (E2E)",
        "auth_key": "Auth-Schlüssel:",
        "show": "Zeigen",
        "paste": "Einfügen",
        "theme": "Design:",
        "language": "Sprache:",
        "listen_ip": "Lokale IP:",
        "port": "Port:",
        "keep_awake": "Wach halten (10m)",
        "full_log": "Vollständiges Aktivitätsprotokoll",
        "copy_logs": "Logs kopieren",
        "clear": "Leeren",
        "tray_open": "FreeDnet öffnen",
        "tray_start": "Proxy starten",
        "tray_stop": "Proxy stoppen",
        "tray_exit": "Beenden",
    },
    "Русский (Russian)": {
        "rtl": False,
        "win_title": "FreeDnet Pro — Умный прокси-роутер",
        "subtitle": "Умный туннель и мульти-ретранслятор",
        "stopped": "● ОСТАНОВЛЕН",
        "running": "● РАБОТАЕТ",
        "sys_proxy_off": "Сист. прокси: ВЫКЛ",
        "sys_proxy_on": "Сист. прокси: ВКЛ",
        "tun_off": "Режим TUN: ВЫКЛ",
        "tun_on": "Режим TUN: ВКЛ",
        "tun_starting": "Запуск TUN...",
        "start_proxy": "▶  Запустить прокси",
        "stop_proxy": "■  Остановить прокси",
        "tab_overview": "Обзор",
        "tab_gas": "Аккаунты GAS",
        "tab_servers": "Серверы",
        "tab_diag": "IP и диагностика",
        "tab_settings": "Настройки и логи",
        "kpi_tunnels": "АКТИВНЫЕ ТУННЕЛИ",
        "kpi_speed": "СКОРОСТЬ (↓ / ↑)",
        "kpi_peak": "ПИКОВАЯ СКОРОСТЬ (↓ / ↑)",
        "kpi_traffic": "ТРАФИК (↓ / ↑)",
        "kpi_pool": "СОСТОЯНИЕ ПУЛА",
        "chain_mode": "Режим маршрута:",
        "rb_gas": "Через Google Apps Script (Обход)",
        "rb_direct": "Напрямую к серверу (Макс. скорость)",
        "exit_ip_unset": "Выходной IP: не проверен",
        "recent_activity": "Последняя активность",
        "row_tip": "Совет: нажмите правой кнопкой по строке для действий",
        "fair_share": "Равномерная квота (1/N)",
        "free_quota": "Лимит Gmail:",
        "add_gas": "+ Добавить GAS",
        "gas_hint": "ПКМ по строке: изменить, вкл/выкл, переместить, пинг или удалить.",
        "gas_headers": ["#", "Аккаунт", "Статус", "Актив.", "Дневной лимит", "Доля %", "Пинг", "URL GAS Web App"],
        "backend_mode": "Режим сервера:",
        "add_server": "+ Добавить сервер",
        "deploy_render": "🚀 Деплой на Render",
        "deploy_snap": "🚀 Деплой на SnapDeploy",
        "deploy_cf": "🚀 Деплой на Cloudflare",
        "remove_btn": "Удалить",
        "srv_headers": ["#", "Сервер", "Статус", "Запросы", "Ошибки", "Пинг", "URL сервера"],
        "diag_title": "Проверка реального IP и страны",
        "diag_check_btn": "Проверить страну / IP",
        "kpi_exit_ip": "ВЫХОДНОЙ IP-АДРЕС",
        "kpi_country": "СТРАНА / ГОРОД",
        "kpi_isp": "ПРОВАЙДЕР / ОРГ.",
        "conn_tests": "Тесты соединения:",
        "ping_all": "Пинг всех реле и серверов",
        "test_e2e": "Тест прокси (E2E)",
        "auth_key": "Ключ доступа:",
        "show": "Показать",
        "paste": "Вставить",
        "theme": "Тема:",
        "language": "Язык:",
        "listen_ip": "Локальный IP:",
        "port": "Порт:",
        "keep_awake": "Не усыплять (10м)",
        "full_log": "Полный журнал событий",
        "copy_logs": "Копировать логи",
        "clear": "Очистить",
        "tray_open": "Открыть FreeDnet",
        "tray_start": "Запустить прокси",
        "tray_stop": "Остановить прокси",
        "tray_exit": "Выход",
    },
    "日本語 (Japanese)": {
        "rtl": False,
        "win_title": "FreeDnet Pro — スマートプロキシ ルーター",
        "subtitle": "スマートトンネル & マルチリレー",
        "stopped": "● 停止中",
        "running": "● 稼働中",
        "sys_proxy_off": "システムプロキシ: OFF",
        "sys_proxy_on": "システムプロキシ: ON",
        "tun_off": "TUN モード: OFF",
        "tun_on": "TUN モード: ON",
        "tun_starting": "TUN 起動中...",
        "start_proxy": "▶  プロキシ開始",
        "stop_proxy": "■  プロキシ停止",
        "tab_overview": "概要",
        "tab_gas": "GAS アカウント",
        "tab_servers": "バックエンドサーバー",
        "tab_diag": "IP と診断",
        "tab_settings": "設定とログ",
        "kpi_tunnels": "アクティブ接続",
        "kpi_speed": "現在速度 (↓ / ↑)",
        "kpi_peak": "最大速度 (↓ / ↑)",
        "kpi_traffic": "合計通信量 (↓ / ↑)",
        "kpi_pool": "プール状態",
        "chain_mode": "接続モード:",
        "rb_gas": "Google Apps Script 経由 (検閲回避)",
        "rb_direct": "バックエンド直結 (最高速度)",
        "exit_ip_unset": "出口 IP: 未確認",
        "recent_activity": "最近のアクティビティ",
        "row_tip": "ヒント: 行を右クリックすると操作メニューが開きます",
        "fair_share": "均等配分 (1/N)",
        "free_quota": "無料 Gmail 上限:",
        "add_gas": "+ GAS 追加",
        "gas_hint": "行を右クリックで編集・有効/無効・並べ替え・Ping・削除ができます。",
        "gas_headers": ["#", "アカウント", "状態", "同時数", "日次枠", "割合 %", "遅延", "GAS Web App URL"],
        "backend_mode": "サーバーモード:",
        "add_server": "+ サーバー追加",
        "deploy_render": "🚀 Render にデプロイ",
        "deploy_snap": "🚀 SnapDeploy にデプロイ",
        "deploy_cf": "🚀 Cloudflare にデプロイ",
        "remove_btn": "削除",
        "srv_headers": ["#", "サーバー名", "状態", "リクエスト", "エラー", "遅延", "サーバー URL"],
        "diag_title": "出口 IP・国チェック",
        "diag_check_btn": "国 / IP を確認",
        "kpi_exit_ip": "出口 IP アドレス",
        "kpi_country": "国 / 都市",
        "kpi_isp": "ISP / 組織",
        "conn_tests": "接続テスト:",
        "ping_all": "全リレー＆サーバーに Ping",
        "test_e2e": "プロキシテスト (E2E)",
        "auth_key": "認証キー:",
        "show": "表示",
        "paste": "貼り付け",
        "theme": "テーマ:",
        "language": "言語:",
        "listen_ip": "待機 IP:",
        "port": "ポート:",
        "keep_awake": "スリープ防止 (10分)",
        "full_log": "詳細アクティビティログ",
        "copy_logs": "ログをコピー",
        "clear": "クリア",
        "tray_open": "FreeDnet を開く",
        "tray_start": "プロキシ開始",
        "tray_stop": "プロキシ停止",
        "tray_exit": "終了",
    },
    "한국어 (Korean)": {
        "rtl": False,
        "win_title": "FreeDnet Pro — 스마트 프록시 라우터",
        "subtitle": "스마트 터널 & 멀티 릴레이 라우터",
        "stopped": "● 중지됨",
        "running": "● 실행 중",
        "sys_proxy_off": "시스템 프록시: OFF",
        "sys_proxy_on": "시스템 프록시: ON",
        "tun_off": "TUN 모드: OFF",
        "tun_on": "TUN 모드: ON",
        "tun_starting": "TUN 시작 중...",
        "start_proxy": "▶  프록시 시작",
        "stop_proxy": "■  프록시 중지",
        "tab_overview": "개요",
        "tab_gas": "GAS 계정",
        "tab_servers": "백엔드 서버",
        "tab_diag": "IP 및 진단",
        "tab_settings": "설정 및 로그",
        "kpi_tunnels": "활성 터널",
        "kpi_speed": "실시간 속도 (↓ / ↑)",
        "kpi_peak": "최고 속도 (↓ / ↑)",
        "kpi_traffic": "총 사용량 (↓ / ↑)",
        "kpi_pool": "풀 상태",
        "chain_mode": "연결 모드:",
        "rb_gas": "Google Apps Script 경유 (차단 우회)",
        "rb_direct": "백엔드 직접 연결 (최고 속도)",
        "exit_ip_unset": "출구 IP: 확인 전",
        "recent_activity": "최근 활동",
        "row_tip": "팁: 항목을 마우스 오른쪽 버튼으로 클릭하면 작업 메뉴가 열립니다",
        "fair_share": "균등 할당 (1/N)",
        "free_quota": "무료 Gmail 한도:",
        "add_gas": "+ GAS 추가",
        "gas_hint": "행을 우클릭하여 편집, 활성/비활성, 순서 변경, Ping 또는 삭제할 수 있습니다.",
        "gas_headers": ["#", "계정", "상태", "활성", "일일 한도", "비율 %", "지연", "GAS Web App URL"],
        "backend_mode": "백엔드 모드:",
        "add_server": "+ 서버 추가",
        "deploy_render": "🚀 Render에 배포",
        "deploy_snap": "🚀 SnapDeploy에 배포",
        "deploy_cf": "🚀 Cloudflare에 배포",
        "remove_btn": "삭제",
        "srv_headers": ["#", "서버 이름", "상태", "요청 수", "오류", "지연", "서버 URL"],
        "diag_title": "실제 출구 IP 및 국가 확인",
        "diag_check_btn": "국가 / IP 확인",
        "kpi_exit_ip": "출구 IP 주소",
        "kpi_country": "국가 / 도시",
        "kpi_isp": "ISP / 기관",
        "conn_tests": "연결 테스트:",
        "ping_all": "모든 릴레이 및 서버 Ping",
        "test_e2e": "프록시 테스트 (E2E)",
        "auth_key": "인증 키:",
        "show": "표시",
        "paste": "붙여넣기",
        "theme": "테마:",
        "language": "언어:",
        "listen_ip": "수신 IP:",
        "port": "포트:",
        "keep_awake": "절전 방지 (10분)",
        "full_log": "전체 활동 로그",
        "copy_logs": "로그 복사",
        "clear": "지우기",
        "tray_open": "FreeDnet 열기",
        "tray_start": "프록시 시작",
        "tray_stop": "프록시 중지",
        "tray_exit": "종료",
    },
}

class EntryDialog(QDialog):
    def __init__(self, parent, title: str, name_label: str, url_label: str, name: str = "", url: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://...")
        form.addRow(name_label, self.name_edit)
        form.addRow(url_label, self.url_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name or not url.startswith("http"):
            QMessageBox.warning(self, "Invalid Input", "Please enter a valid name and https://... URL.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.url_edit.text().strip()



RENDER_REGIONS = [
    ("Frankfurt, Germany (EU)", "frankfurt"),
    ("Oregon, USA (US West)", "oregon"),
    ("Ohio, USA (US East)", "ohio"),
    ("Virginia, USA (US East)", "virginia"),
    ("Singapore (Asia)", "singapore"),
]


class RenderDeployDialog(QDialog):
    def __init__(self, parent, default_auth_key: str):
        super().__init__(parent)
        self.setWindowTitle("Deploy FreeDnet Server on Render")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("rnd_xxxxxxxxxxxxxxxxxxxxxxxx")
        form.addRow("Render API Key:", self.api_key_edit)

        self.name_edit = QLineEdit(f"freednet-node-{int(time.time()) % 10000}")
        form.addRow("Service Name:", self.name_edit)

        self.region_combo = QComboBox()
        for label, slug in RENDER_REGIONS:
            self.region_combo.addItem(label, slug)
        form.addRow("Region / Location:", self.region_combo)

        self.repo_edit = QLineEdit("https://github.com/ENMGhobadi/FreeDnet")
        form.addRow("GitHub Repo URL:", self.repo_edit)

        self.auth_edit = QLineEdit(default_auth_key)
        form.addRow("AUTH_KEY (Env):", self.auth_edit)

        layout.addLayout(form)
        hint = QLabel("Get your Render API Key from: dashboard.render.com/u/settings#api-keys", objectName="Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Deploy Now")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "Missing API Key", "Please enter your Render API Key.")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Name", "Please enter a Service Name.")
            return
        if not self.repo_edit.text().strip().startswith("https://github.com/"):
            QMessageBox.warning(self, "Invalid Repo", "Please enter a valid https://github.com/... repository URL.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "api_key": self.api_key_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "region": self.region_combo.currentData(),
            "repo": self.repo_edit.text().strip(),
            "auth_key": self.auth_edit.text().strip() or "change-me-freednet-key",
        }


class SnapDeployDialog(QDialog):
    def __init__(self, parent, default_auth_key: str):
        super().__init__(parent)
        self.setWindowTitle("Deploy FreeDnet Server on SnapDeploy")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("sk_... or sd_pat_...")
        form.addRow("SnapDeploy API Key:", self.api_key_edit)

        self.name_edit = QLineEdit(f"freednet-node-{int(time.time()) % 10000}")
        form.addRow("Container Name:", self.name_edit)

        self.repo_edit = QLineEdit("ENMGhobadi/FreeDnet")
        form.addRow("GitHub Repo (owner/name):", self.repo_edit)

        self.auth_edit = QLineEdit(default_auth_key)
        form.addRow("AUTH_KEY (Env):", self.auth_edit)

        layout.addLayout(form)
        hint = QLabel("Runs on AWS US-East. GitHub must be connected in your SnapDeploy dashboard.", objectName="Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Deploy Now")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "Missing API Key", "Please enter your SnapDeploy API Key.")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Name", "Please enter a Container Name.")
            return
        repo = self.repo_edit.text().strip().replace("https://github.com/", "").strip("/")
        if "/" not in repo:
            QMessageBox.warning(self, "Invalid Repo", "Enter repo as owner/name (e.g. ENMGhobadi/FreeDnet).")
            return
        self.accept()

    def values(self) -> dict:
        repo = self.repo_edit.text().strip().replace("https://github.com/", "").strip("/")
        return {
            "api_key": self.api_key_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "repo": repo,
            "auth_key": self.auth_edit.text().strip() or "change-me-freednet-key",
        }


CF_LOCATIONS = [
    ("Auto (Nearest)", ""),
    ("Western Europe (EU)", "weur"),
    ("Eastern Europe (EU)", "eeur"),
    ("Eastern North America (US)", "enam"),
    ("Western North America (US)", "wnam"),
    ("Asia-Pacific (APAC)", "apac"),
]


class CloudflareDeployDialog(QDialog):
    def __init__(self, parent, default_auth_key: str):
        super().__init__(parent)
        self.setWindowTitle("Deploy FreeDnet Worker on Cloudflare")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("32-char Cloudflare Account ID")
        form.addRow("Account ID:", self.account_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("Cloudflare API Token (Workers Scripts:Edit)")
        form.addRow("API Token:", self.token_edit)

        self.name_edit = QLineEdit(f"freednet-cf-{int(time.time()) % 10000}")
        form.addRow("Worker Name:", self.name_edit)

        self.loc_combo = QComboBox()
        for label, hint in CF_LOCATIONS:
            self.loc_combo.addItem(label, hint)
        form.addRow("Location Hint:", self.loc_combo)

        self.auth_edit = QLineEdit(default_auth_key)
        form.addRow("AUTH_KEY (Env):", self.auth_edit)

        layout.addLayout(form)
        hint = QLabel("Deploys Worker + SQLite Durable Object (Free plan compatible). Ensure workers.dev subdomain is enabled in Cloudflare.", objectName="Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Deploy Now")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.account_edit.text().strip():
            QMessageBox.warning(self, "Missing Account ID", "Please enter your Cloudflare Account ID.")
            return
        if not self.token_edit.text().strip():
            QMessageBox.warning(self, "Missing API Token", "Please enter your Cloudflare API Token.")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Name", "Please enter a Worker Name.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "account_id": self.account_edit.text().strip(),
            "api_token": self.token_edit.text().strip(),
            "name": self.name_edit.text().strip().lower(),
            "location_hint": self.loc_combo.currentData(),
            "location_label": self.loc_combo.currentText(),
            "auth_key": self.auth_edit.text().strip() or "change-me-freednet-key",
        }

class FreeDnetProWindow(QMainWindow):
    SRV_PRIORITY_LBL = "Priority (#1 -> #2 Failover)"
    SRV_FAIR_LBL = "Fair-Share (1/N Equal)"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FreeDnet Pro — Smart Proxy Router")
        self.resize(980, 640)
        self.setMinimumSize(860, 560)

        self._config_load_err = ""
        self.config = self._load_config()
        preset = self.config.get("quota_preset", DEFAULT_PRESET)
        if preset not in GAS_QUOTA_PRESETS:
            preset = DEFAULT_PRESET
        conc, daily, rpm = GAS_QUOTA_PRESETS[preset]

        self.pool = GasAccountPool(
            max_concurrent=conc,
            daily_limit=daily,
            max_rpm=rpm,
            gas_mode=self.config.get("gas_mode", "fair"),
        )
        self.pool.load_accounts(self.config.get("accounts", []))
        self.server_pool = ServerPool()
        self.server_pool.load_servers(
            self.config.get("servers", []),
            server_mode=self.config.get("server_mode", "priority"),
        )

        self._force_quit = False
        self._tray_notified = False
        self._tun_ui_dirty = False
        self._pending_ip_info = None
        self._pending_render_srv = None
        self._busy_lock = threading.Lock()
        self._busy_tasks: dict[int, str] = {}
        self._busy_seq = 0
        self._spin_idx = 0
        self.proxy = None
        self.xray_proc = None
        self.tun_active = False
        self.sys_proxy_active = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._last_save_ts = time.time()
        self._last_stat_ts = time.time()
        self._prev_tx = 0
        self._prev_rx = 0
        self._peak_tx_rate = 0.0
        self._peak_rx_rate = 0.0

        icon_path = _resource_path("freednet.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self._build_ui()
        self._setup_tray(icon_path)
        if self._config_load_err:
            self.log(self._config_load_err)
        self._current_theme_is_light = False
        self._apply_theme(self.config.get("theme", "Midnight Dark (Default)"), save=False)
        self._apply_language(self.config.get("language", "English"), save=False)
        self._refresh_target_dropdown()
        self._sync_tables()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._on_ui_tick)
        self.ui_timer.start(200)

        if "--auto-tun" in sys.argv:
            QTimer.singleShot(350, self.toggle_tun_mode)

    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
            except Exception as e:
                self._config_load_err = f"Failed to read {CONFIG_PATH}: {e}"
        return dict(DEFAULT_CONFIG)

    def _save_config(self):
        self.config["auth_key"] = self.auth_edit.text().strip()
        self.config["listen_host"] = self.host_edit.text().strip() or "127.0.0.1"
        self.config["route_mode"] = "direct" if self.rb_direct.isChecked() else "gas"
        self.config["gas_mode"] = "fair" if self.chk_fair_share.isChecked() else "priority"
        self.config["server_mode"] = self.server_pool.server_mode
        self.config["quota_preset"] = self.preset_combo.currentText()
        self.config["keep_awake"] = self.chk_keep_awake.isChecked()
        if hasattr(self, "theme_combo"):
            self.config["theme"] = self.theme_combo.currentText()
        if hasattr(self, "lang_combo"):
            self.config["language"] = self.lang_combo.currentText()
        try:
            self.config["listen_port"] = int(self.port_edit.text().strip())
        except ValueError:
            self.config["listen_port"] = 1080
        self.config["accounts"] = [acc.to_dict() for acc in self.pool.accounts]
        self.config["servers"] = [srv.to_dict() for srv in self.server_pool.servers]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def _setup_tray(self, icon_path: str):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("FreeDnet Pro")

        menu = QMenu(self)
        self.tray_open_act = menu.addAction("Open FreeDnet")
        self.tray_open_act.triggered.connect(self._restore_from_tray)
        self.tray_proxy_act = menu.addAction("Start Proxy")
        self.tray_proxy_act.triggered.connect(self.toggle_proxy)
        self.tray_tun_act = menu.addAction("TUN Mode: OFF")
        self.tray_tun_act.triggered.connect(self.toggle_tun_mode)
        menu.addSeparator()
        self.tray_exit_act = menu.addAction("Exit")
        self.tray_exit_act.triggered.connect(self._quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        self._force_quit = True
        try:
            if self.sys_proxy_active:
                set_windows_system_proxy(False)
                self.sys_proxy_active = False
            self._stop_tun_process()
            self._save_config()
            if self.proxy and self.proxy.running:
                self.proxy.stop()
            if getattr(self, "tray", None):
                self.tray.hide()
        except Exception:
            pass
        QApplication.quit()

    def closeEvent(self, event):
        if not self._force_quit and getattr(self, "tray", None) and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_notified:
                self._tray_notified = True
                self.tray.showMessage(
                    "FreeDnet Pro",
                    "Running in background. Right-click tray icon and choose Exit to close.",
                    QSystemTrayIcon.Information,
                    2500,
                )
            return
        try:
            if self.sys_proxy_active:
                set_windows_system_proxy(False)
                self.sys_proxy_active = False
            self._stop_tun_process()
            self._save_config()
            if self.proxy and self.proxy.running:
                self.proxy.stop()
        except Exception:
            pass
        event.accept()

    def _start_busy(self, label: str) -> int:
        with self._busy_lock:
            self._busy_seq += 1
            tid = self._busy_seq
            self._busy_tasks[tid] = label
            return tid

    def _stop_busy(self, tid: int):
        with self._busy_lock:
            self._busy_tasks.pop(tid, None)

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def _build_ui(self):
        root = QWidget(objectName="Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Clean Minimal Header Bar
        header = QFrame(objectName="HeaderBar")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 12, 20, 12)

        h_lay.addWidget(QLabel("FreeDnet", objectName="BrandTitle"))
        h_lay.addSpacing(8)
        h_lay.addWidget(QLabel("PRO", objectName="ProBadge"))
        h_lay.addSpacing(12)
        self.subtitle_lbl = QLabel("Smart Tunnel & Multi-Relay Router", objectName="Muted")
        h_lay.addWidget(self.subtitle_lbl)
        h_lay.addStretch()

        self.status_pill = QLabel("● STOPPED", objectName="StatusPillStopped")
        self.sys_proxy_btn = QPushButton("Sys Proxy: OFF")
        self.tun_btn = QPushButton("TUN Mode: OFF")
        self.start_btn = QPushButton("▶  Start Proxy", objectName="PrimaryBtn")

        self.sys_proxy_btn.clicked.connect(self.toggle_system_proxy)
        self.tun_btn.clicked.connect(self.toggle_tun_mode)
        self.start_btn.clicked.connect(self.toggle_proxy)

        self.busy_label = QLabel("", objectName="BusyLabel")
        self.busy_label.setVisible(False)
        h_lay.addWidget(self.busy_label)
        h_lay.addWidget(self.status_pill)
        h_lay.addSpacing(8)
        h_lay.addWidget(self.sys_proxy_btn)
        h_lay.addWidget(self.tun_btn)
        h_lay.addWidget(self.start_btn)
        outer.addWidget(header)

        self.busy_bar = QProgressBar(objectName="BusyBar")
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setVisible(False)
        outer.addWidget(self.busy_bar)

        # Main Tabbed Workspace
        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        outer.addLayout(body)

        self.tabs = QTabWidget()
        body.addWidget(self.tabs, stretch=1)

        # ==========================================================
        # TAB 1: OVERVIEW (KPIs + Routing + Quick Diagnostics)
        # ==========================================================
        ov_tab = QWidget()
        ov_lay = QVBoxLayout(ov_tab)
        ov_lay.setContentsMargins(16, 16, 16, 16)
        ov_lay.setSpacing(14)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.kpi_tunnels, self.kpi_tunnels_ttl = self._make_kpi_card_ex(kpi_row, "ACTIVE TUNNELS", "0")
        self.kpi_speed, self.kpi_speed_ttl = self._make_kpi_card_ex(kpi_row, "LIVE SPEED (↓ / ↑)", "0 B/s  |  0 B/s")
        self.kpi_peak, self.kpi_peak_ttl = self._make_kpi_card_ex(kpi_row, "PEAK SPEED (↓ / ↑)", "0 B/s  |  0 B/s")
        self.kpi_traffic, self.kpi_traffic_ttl = self._make_kpi_card_ex(kpi_row, "TOTAL USAGE (↓ / ↑)", "0 B  |  0 B")
        self.kpi_pool, self.kpi_pool_ttl = self._make_kpi_card_ex(kpi_row, "POOL HEALTH", "GAS 0 | SRV 0")
        ov_lay.addLayout(kpi_row)

        route_card = QFrame(objectName="Card")
        r_lay = QVBoxLayout(route_card)
        r_lay.setContentsMargins(16, 14, 16, 14)
        r_lay.setSpacing(12)

        r1 = QHBoxLayout()
        self.lbl_chain_mode = QLabel("Chain Mode:", objectName="FieldLabel")
        r1.addWidget(self.lbl_chain_mode)
        self.rb_gas = QRadioButton("Via Google Apps Script (Anti-Filtering)")
        self.rb_direct = QRadioButton("Direct to Backend (Max Speed)")
        self.route_grp = QButtonGroup(self)
        self.route_grp.addButton(self.rb_gas)
        self.route_grp.addButton(self.rb_direct)
        if self.config.get("route_mode", "gas") == "direct":
            self.rb_direct.setChecked(True)
        else:
            self.rb_gas.setChecked(True)
        self.rb_gas.toggled.connect(self._on_route_change)
        r1.addWidget(self.rb_gas)
        r1.addWidget(self.rb_direct)
        r1.addStretch()

        self.ip_badge = QLabel("Exit IP: Not checked yet", objectName="Muted")
        r1.addWidget(self.ip_badge)
        r_lay.addLayout(r1)
        ov_lay.addWidget(route_card)

        # Compact recent activity preview on Overview
        ov_log_hdr = QHBoxLayout()
        self.lbl_recent_act = QLabel("Recent Activity", objectName="FieldLabel")
        ov_log_hdr.addWidget(self.lbl_recent_act)
        ov_log_hdr.addStretch()
        self.lbl_row_tip = QLabel("Tip: Right-click any account or server row for actions", objectName="Muted")
        ov_log_hdr.addWidget(self.lbl_row_tip)
        ov_lay.addLayout(ov_log_hdr)

        self.ov_log_preview = QTextEdit(objectName="LogConsole")
        self.ov_log_preview.setReadOnly(True)
        ov_lay.addWidget(self.ov_log_preview, stretch=1)
        self.tabs.addTab(ov_tab, "Overview")

        # ==========================================================
        # TAB 2: GAS ACCOUNTS (Clean toolbar + Right-click menu)
        # ==========================================================
        gas_tab = QWidget()
        gas_lay = QVBoxLayout(gas_tab)
        gas_lay.setContentsMargins(16, 16, 16, 16)
        gas_lay.setSpacing(10)

        gas_toolbar = QFrame(objectName="Toolbar")
        gt_lay = QHBoxLayout(gas_toolbar)
        gt_lay.setContentsMargins(12, 8, 12, 8)
        self.chk_fair_share = QCheckBox("Fair-Share Quota (1/N Equal)")
        self.chk_fair_share.setChecked(self.config.get("gas_mode", "fair") == "fair")
        self.chk_fair_share.toggled.connect(self._on_gas_mode_change)
        gt_lay.addWidget(self.chk_fair_share)
        gt_lay.addStretch()

        self.lbl_free_quota = QLabel("Free Gmail Quota:", objectName="FieldLabel")
        gt_lay.addWidget(self.lbl_free_quota)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(GAS_QUOTA_PRESETS.keys()))
        init_preset = self.config.get("quota_preset", DEFAULT_PRESET)
        if init_preset in GAS_QUOTA_PRESETS:
            self.preset_combo.setCurrentText(init_preset)
        self.preset_combo.setMinimumWidth(275)
        self.preset_combo.currentTextChanged.connect(self._on_preset_change)
        gt_lay.addWidget(self.preset_combo)

        self.add_g_btn = QPushButton("+ Add GAS", objectName="PrimaryBtn")
        add_g = self.add_g_btn
        add_g.clicked.connect(self.add_account)
        gt_lay.addSpacing(8)
        gt_lay.addWidget(add_g)
        self.rem_g_btn = QPushButton("Remove")
        self.rem_g_btn.clicked.connect(self.remove_account)
        gt_lay.addSpacing(4)
        gt_lay.addWidget(self.rem_g_btn)
        gas_lay.addWidget(gas_toolbar)

        self.gas_table = self._make_table(["#", "Account", "Status", "Active", "Daily Quota", "Share %", "Latency", "GAS Web App URL"])
        self.gas_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.gas_table.customContextMenuRequested.connect(self._show_gas_context_menu)
        self.gas_table.doubleClicked.connect(lambda _: self.edit_account())
        gas_lay.addWidget(self.gas_table, stretch=1)
        self.lbl_gas_hint = QLabel("Right-click any row to Edit, Enable/Disable, Reorder, Ping, or Remove.", objectName="Muted")
        gas_lay.addWidget(self.lbl_gas_hint)
        self.tabs.addTab(gas_tab, "GAS Accounts")

        # ==========================================================
        # TAB 3: BACKEND SERVERS (Clean toolbar + Right-click menu)
        # ==========================================================
        srv_tab = QWidget()
        srv_lay = QVBoxLayout(srv_tab)
        srv_lay.setContentsMargins(16, 16, 16, 16)
        srv_lay.setSpacing(10)

        srv_toolbar = QFrame(objectName="Toolbar")
        st_lay = QHBoxLayout(srv_toolbar)
        st_lay.setContentsMargins(12, 8, 12, 8)
        self.lbl_backend_mode = QLabel("Backend Mode:", objectName="FieldLabel")
        st_lay.addWidget(self.lbl_backend_mode)
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(250)
        self.target_combo.currentTextChanged.connect(self._on_target_change)
        st_lay.addWidget(self.target_combo)
        st_lay.addStretch()
        self.deploy_render_btn = QPushButton("🚀 Deploy on Render")
        self.deploy_render_btn.clicked.connect(self.deploy_on_render)
        st_lay.addWidget(self.deploy_render_btn)
        st_lay.addSpacing(4)
        self.deploy_snap_btn = QPushButton("🚀 Deploy on SnapDeploy")
        self.deploy_snap_btn.clicked.connect(self.deploy_on_snapdeploy)
        st_lay.addWidget(self.deploy_snap_btn)
        st_lay.addSpacing(4)
        self.deploy_cf_btn = QPushButton("🚀 Deploy on Cloudflare")
        self.deploy_cf_btn.clicked.connect(self.deploy_on_cloudflare)
        st_lay.addWidget(self.deploy_cf_btn)
        st_lay.addSpacing(4)
        self.add_s_btn = QPushButton("+ Add Server", objectName="PrimaryBtn")
        add_s = self.add_s_btn
        add_s.clicked.connect(self.add_server)
        st_lay.addWidget(add_s)
        st_lay.addSpacing(4)
        self.rem_s_btn = QPushButton("Remove")
        self.rem_s_btn.clicked.connect(self.remove_server)
        st_lay.addWidget(self.rem_s_btn)
        srv_lay.addWidget(srv_toolbar)

        self.srv_table = self._make_table(["#", "Server Name", "Status", "Requests", "Errors", "Latency", "Server URL"])
        self.srv_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.srv_table.customContextMenuRequested.connect(self._show_srv_context_menu)
        self.srv_table.doubleClicked.connect(lambda _: self.edit_server())
        srv_lay.addWidget(self.srv_table, stretch=1)
        self.tabs.addTab(srv_tab, "Backend Servers")

        # ==========================================================
        # TAB 4: IP & DIAGNOSTICS (Dedicated Country Check & Tests)
        # ==========================================================
        diag_tab = QWidget()
        diag_lay = QVBoxLayout(diag_tab)
        diag_lay.setContentsMargins(16, 16, 16, 16)
        diag_lay.setSpacing(14)

        ip_card = QFrame(objectName="Card")
        ip_lay = QVBoxLayout(ip_card)
        ip_lay.setContentsMargins(16, 16, 16, 16)
        ip_lay.setSpacing(12)

        ip_hdr = QHBoxLayout()
        self.lbl_diag_title = QLabel("Real Exit IP & Country Check", objectName="FieldLabel")
        ip_hdr.addWidget(self.lbl_diag_title)
        ip_hdr.addStretch()
        self.diag_country_btn = QPushButton("Check Country / IP Now", objectName="PrimaryBtn")
        self.diag_country_btn.clicked.connect(self.check_country_ip)
        ip_hdr.addWidget(self.diag_country_btn)
        ip_lay.addLayout(ip_hdr)

        ip_kpi_row = QHBoxLayout()
        ip_kpi_row.setSpacing(10)
        self.diag_ip_val, self.diag_ip_ttl = self._make_kpi_card_ex(ip_kpi_row, "EXIT IP ADDRESS", "-")
        self.diag_country_val, self.diag_country_ttl = self._make_kpi_card_ex(ip_kpi_row, "COUNTRY / CITY", "-")
        self.diag_isp_val, self.diag_isp_ttl = self._make_kpi_card_ex(ip_kpi_row, "ISP / ORGANIZATION", "-")
        ip_lay.addLayout(ip_kpi_row)
        diag_lay.addWidget(ip_card)

        tools_card = QFrame(objectName="Card")
        t_lay = QHBoxLayout(tools_card)
        t_lay.setContentsMargins(16, 14, 16, 14)
        self.lbl_conn_tests = QLabel("Connection Tests:", objectName="FieldLabel")
        t_lay.addWidget(self.lbl_conn_tests)
        t_lay.addSpacing(10)
        self.d_ping_btn = QPushButton("Ping All Relays & Servers")
        d_ping = self.d_ping_btn
        self.d_e2e_btn = QPushButton("Test Proxy (E2E)")
        d_e2e = self.d_e2e_btn
        d_ping.clicked.connect(self.test_all_accounts)
        d_e2e.clicked.connect(self.test_proxy_e2e)
        t_lay.addWidget(d_ping)
        t_lay.addWidget(d_e2e)
        t_lay.addStretch()
        diag_lay.addWidget(tools_card)
        diag_lay.addStretch()
        self.tabs.addTab(diag_tab, "IP & Diagnostics")

        # ==========================================================
        # TAB 5: SETTINGS & FULL LOGS
        # ==========================================================
        set_tab = QWidget()
        set_lay = QVBoxLayout(set_tab)
        set_lay.setContentsMargins(16, 16, 16, 16)
        set_lay.setSpacing(12)

        cfg_card = QFrame(objectName="Card")
        cfg_vlay = QVBoxLayout(cfg_card)
        cfg_vlay.setContentsMargins(16, 14, 16, 14)
        cfg_vlay.setSpacing(10)

        c_lay1 = QHBoxLayout()
        self.lbl_auth_key = QLabel("Auth Key:", objectName="FieldLabel")
        c_lay1.addWidget(self.lbl_auth_key)
        self.auth_edit = QLineEdit(self.config["auth_key"])
        self.auth_edit.setEchoMode(QLineEdit.Password)
        self.auth_edit.setMinimumWidth(220)
        c_lay1.addWidget(self.auth_edit)

        self.chk_show_key = QCheckBox("Show")
        self.chk_show_key.toggled.connect(
            lambda checked: self.auth_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        c_lay1.addWidget(self.chk_show_key)

        self.paste_btn = QPushButton("Paste", objectName="SmallBtn")
        paste_btn = self.paste_btn
        paste_btn.clicked.connect(self._paste_auth_key)
        c_lay1.addWidget(paste_btn)
        c_lay1.addStretch()

        self.lbl_theme = QLabel("Theme:", objectName="FieldLabel")
        c_lay1.addWidget(self.lbl_theme)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        saved_theme = self.config.get("theme", "Midnight Dark (Default)")
        if saved_theme in THEMES:
            self.theme_combo.setCurrentText(saved_theme)
        self.theme_combo.setMinimumWidth(210)
        self.theme_combo.currentTextChanged.connect(lambda name: self._apply_theme(name, save=True))
        c_lay1.addWidget(self.theme_combo)
        cfg_vlay.addLayout(c_lay1)

        c_lay2 = QHBoxLayout()
        self.lbl_listen_ip = QLabel("Listen IP:", objectName="FieldLabel")
        c_lay2.addWidget(self.lbl_listen_ip)
        self.host_edit = QLineEdit(self.config["listen_host"])
        self.host_edit.setFixedWidth(125)
        c_lay2.addWidget(self.host_edit)

        c_lay2.addSpacing(8)
        self.lbl_port = QLabel("Port:", objectName="FieldLabel")
        c_lay2.addWidget(self.lbl_port)
        self.port_edit = QLineEdit(str(self.config["listen_port"]))
        self.port_edit.setFixedWidth(72)
        c_lay2.addWidget(self.port_edit)

        c_lay2.addSpacing(12)
        self.lbl_language = QLabel("Language:", objectName="FieldLabel")
        c_lay2.addWidget(self.lbl_language)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(list(LANGUAGES.keys()))
        saved_lang = self.config.get("language", "English")
        if saved_lang in LANGUAGES:
            self.lang_combo.setCurrentText(saved_lang)
        self.lang_combo.setMinimumWidth(190)
        self.lang_combo.currentTextChanged.connect(lambda name: self._apply_language(name, save=True))
        c_lay2.addWidget(self.lang_combo)

        c_lay2.addStretch()
        self.chk_keep_awake = QCheckBox("Keep Awake (10m)")
        self.chk_keep_awake.setChecked(bool(self.config.get("keep_awake", True)))
        self.chk_keep_awake.toggled.connect(self._on_keep_awake_change)
        c_lay2.addWidget(self.chk_keep_awake)
        cfg_vlay.addLayout(c_lay2)
        set_lay.addWidget(cfg_card)

        log_hdr = QHBoxLayout()
        self.lbl_full_log = QLabel("Full Activity Log", objectName="FieldLabel")
        log_hdr.addWidget(self.lbl_full_log)
        log_hdr.addStretch()
        self.cpy_btn = QPushButton("Copy Logs", objectName="SmallBtn")
        cpy_btn = self.cpy_btn
        cpy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.log_console.toPlainText()))
        self.clr_btn = QPushButton("Clear", objectName="SmallBtn")
        clr_btn = self.clr_btn
        clr_btn.clicked.connect(lambda: (self.log_console.clear(), self.ov_log_preview.clear()))
        log_hdr.addWidget(cpy_btn)
        log_hdr.addWidget(clr_btn)
        set_lay.addLayout(log_hdr)

        self.log_console = QTextEdit(objectName="LogConsole")
        self.log_console.setReadOnly(True)
        set_lay.addWidget(self.log_console, stretch=1)
        self.tabs.addTab(set_tab, "Settings & Logs")

    def _show_gas_context_menu(self, pos):
        row = self.gas_table.rowAt(pos.y())
        if row < 0 or row >= len(self.pool.accounts):
            return
        self.gas_table.selectRow(row)
        acc = self.pool.accounts[row]
        menu = QMenu(self)
        menu.addAction("Edit Account", self.edit_account)
        menu.addAction("Disable" if acc.enabled else "Enable", self.toggle_account)
        menu.addAction("Ping This Account", lambda: self._ping_single_gas(row))
        menu.addSeparator()
        menu.addAction("Move Up (▲)", lambda: self.move_account(-1))
        menu.addAction("Move Down (▼)", lambda: self.move_account(1))
        menu.addSeparator()
        menu.addAction("Remove", self.remove_account)
        menu.exec(self.gas_table.viewport().mapToGlobal(pos))

    def _show_srv_context_menu(self, pos):
        row = self.srv_table.rowAt(pos.y())
        if row < 0 or row >= len(self.server_pool.servers):
            return
        self.srv_table.selectRow(row)
        srv = self.server_pool.servers[row]
        menu = QMenu(self)
        menu.addAction("Edit Server", self.edit_server)
        menu.addAction("Disable" if srv.enabled else "Enable", self.toggle_server)
        menu.addAction(f"Lock Output to Only: {srv.name}", lambda: self._lock_only_server(srv.name))
        menu.addAction("Ping This Server", lambda: self._ping_single_server(row))
        menu.addSeparator()
        menu.addAction("Move Up (▲)", lambda: self.move_server(-1))
        menu.addAction("Move Down (▼)", lambda: self.move_server(1))
        menu.addSeparator()
        menu.addAction("Remove", self.remove_server)
        menu.exec(self.srv_table.viewport().mapToGlobal(pos))

    def _lock_only_server(self, name: str):
        self.server_pool.set_mode(f"only:{name}")
        self._refresh_target_dropdown()
        self._save_config()
        self.log(f"Locked Backend Mode to Only: {name}")

    def _ping_single_gas(self, row: int):
        if not (0 <= row < len(self.pool.accounts)):
            return
        acc = self.pool.accounts[row]
        auth_key = self.auth_edit.text().strip()
        servers = self.server_pool.get_candidates()
        srv = servers[0]

        def worker():
            tid = self._start_busy(f"Pinging {acc.name}...")
            try:
                self._run_ping_single_gas(acc, auth_key, srv)
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def _run_ping_single_gas(self, acc, auth_key, srv):
        if True:
            payload = {"authKey": auth_key, "action": "ping"}
            if srv is not None:
                payload["serverUrl"] = srv.url
            t0 = time.time()
            res, _ = post_to_gas(acc.url, payload, timeout=45.0)
            lat = int((time.time() - t0) * 1000)
            if res.get("ok"):
                acc.last_latency_ms = lat
                acc.last_error = ""
                self.log(f"GAS Ping OK [{acc.name}]: {lat} ms")
            else:
                acc.last_error = res.get("error", "Error")
                self.log(f"GAS Ping FAILED [{acc.name}]: {acc.last_error}")

    def _ping_single_server(self, row: int):
        if not (0 <= row < len(self.server_pool.servers)):
            return
        srv = self.server_pool.servers[row]
        auth_key = self.auth_edit.text().strip()
        direct = self.rb_direct.isChecked()

        def worker():
            tid = self._start_busy(f"Pinging {srv.name}...")
            try:
                self._run_ping_single_server(srv, auth_key, direct)
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def _run_ping_single_server(self, srv, auth_key, direct):
        if True:
            for attempt in range(7):
                t0 = time.time()
                if direct or not self.pool.accounts:
                    res = post_direct_to_server(srv.url, {"authKey": auth_key, "action": "ping"}, auth_key, timeout=45.0)
                else:
                    first_gas = next((a for a in self.pool.accounts if a.enabled), self.pool.accounts[0])
                    res, _ = post_to_gas(first_gas.url, {"authKey": auth_key, "action": "ping", "serverUrl": srv.url}, timeout=45.0)
                lat = int((time.time() - t0) * 1000)
                if res.get("ok"):
                    self.server_pool.report_success(srv, lat)
                    self.log(f"Backend OK [{srv.name}]: {lat} ms (active: {res.get('activeSessions', 0)})")
                    return
                det = str(res.get("detail") or "")
                if ("Waking up" in det or "503" in str(res.get("error", ""))) and attempt < 6:
                    self.log(f"Backend [{srv.name}] is waking up (503), waiting 10s ({attempt + 1}/6)...")
                    time.sleep(10)
                    continue
                self.server_pool.report_failure(srv, res.get("error", "Error"))
                self.log(f"Backend FAILED [{srv.name}]: {res.get('error')}")
                return

    def _make_kpi_card_ex(self, parent_layout, title: str, initial: str) -> tuple[QLabel, QLabel]:
        card = QFrame(objectName="KpiCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        ttl = QLabel(title, objectName="KpiTitle")
        lay.addWidget(ttl)
        val = QLabel(initial, objectName="KpiValue")
        lay.addWidget(val)
        parent_layout.addWidget(card)
        return val, ttl

    def _make_kpi_card(self, parent_layout, title: str, initial: str) -> QLabel:
        card = QFrame(objectName="KpiCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lay.addWidget(QLabel(title, objectName="KpiTitle"))
        val = QLabel(initial, objectName="KpiValue")
        lay.addWidget(val)
        parent_layout.addWidget(card)
        return val

    def _make_table(self, headers: list[str]) -> QTableWidget:
        tbl = QTableWidget(0, len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setAlternatingRowColors(True)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        hdr = tbl.horizontalHeader()
        for i in range(len(headers) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        return tbl

    def _paste_auth_key(self):
        clip = QApplication.clipboard().text().strip()
        if clip:
            self.auth_edit.setText(clip)
            self.log("Pasted Auth Key from clipboard.")

    def _refresh_target_dropdown(self):
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        options = [self.SRV_PRIORITY_LBL, self.SRV_FAIR_LBL] + [f"Only: {s.name}" for s in self.server_pool.servers]
        self.target_combo.addItems(options)
        mode = self.server_pool.server_mode
        if mode == "fair":
            self.target_combo.setCurrentText(self.SRV_FAIR_LBL)
        elif mode.startswith("only:") and f"Only: {mode[5:]}" in options:
            self.target_combo.setCurrentText(f"Only: {mode[5:]}")
        else:
            self.server_pool.set_mode("priority")
            self.target_combo.setCurrentText(self.SRV_PRIORITY_LBL)
        self.target_combo.blockSignals(False)

    def _on_target_change(self, val: str):
        if not val:
            return
        if val == self.SRV_PRIORITY_LBL:
            self.server_pool.set_mode("priority")
        elif val == self.SRV_FAIR_LBL:
            self.server_pool.set_mode("fair")
        elif val.startswith("Only: "):
            self.server_pool.set_mode(f"only:{val[6:]}")
        self._save_config()
        self.log(f"Backend Mode set to: {val}")

    def _on_gas_mode_change(self, checked: bool):
        gmode = "fair" if checked else "priority"
        conc, daily, rpm = GAS_QUOTA_PRESETS.get(self.preset_combo.currentText(), (18, 14000, 120))
        self.pool.update_limits(conc, daily, rpm, gas_mode=gmode)
        self._save_config()
        self.log(f"GAS Quota Distribution: {'Fair-Share (1/N Equal)' if checked else 'Priority (#1 -> #2 Failover)'}")

    def _on_preset_change(self, preset: str):
        if preset in GAS_QUOTA_PRESETS:
            conc, daily, rpm = GAS_QUOTA_PRESETS[preset]
            self.pool.update_limits(conc, daily, rpm)
            self._save_config()
            self.log(f"GAS Quota Profile: {preset}")

    def _on_keep_awake_change(self, val: bool):
        self._save_config()
        if self.proxy and self.proxy.running:
            self.proxy.keep_awake = val
            self.log(f"Keep Awake {'enabled' if val else 'disabled'}.")

    def _apply_theme(self, theme_name: str, save: bool = True):
        if theme_name not in THEMES:
            theme_name = "Midnight Dark (Default)"
        theme = THEMES[theme_name]
        self._current_theme_is_light = bool(theme.get("is_light", False))
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(theme))
        if save:
            self._save_config()
            self.log(f"Theme changed to: {theme_name}")

    def _t(self, key: str):
        lang = getattr(self, "_current_lang", "English")
        return LANGUAGES.get(lang, LANGUAGES["English"]).get(key, LANGUAGES["English"].get(key, key))

    def _apply_language(self, lang_name: str, save: bool = True):
        if lang_name not in LANGUAGES:
            lang_name = "English"
        self._current_lang = lang_name
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(Qt.RightToLeft if LANGUAGES[lang_name]["rtl"] else Qt.LeftToRight)
        self._retranslate_ui()
        if save:
            self._save_config()
            self.log(f"Language changed to: {lang_name}")

    def _retranslate_ui(self):
        self.setWindowTitle(self._t("win_title"))
        self.subtitle_lbl.setText(self._t("subtitle"))
        running = bool(self.proxy and self.proxy.running)
        if running:
            lbl = "DIRECT" if self.proxy.route_mode == "direct" else "GAS"
            self.status_pill.setText(f"{self._t('running')} · {lbl}")
            self.start_btn.setText(self._t("stop_proxy"))
        else:
            self.status_pill.setText(self._t("stopped"))
            self.start_btn.setText(self._t("start_proxy"))

        self.sys_proxy_btn.setText(self._t("sys_proxy_on") if self.sys_proxy_active else self._t("sys_proxy_off"))
        if self.tun_btn.isEnabled():
            self.tun_btn.setText(self._t("tun_on") if self.tun_active else self._t("tun_off"))

        self.tabs.setTabText(0, self._t("tab_overview"))
        self.tabs.setTabText(1, self._t("tab_gas"))
        self.tabs.setTabText(2, self._t("tab_servers"))
        self.tabs.setTabText(3, self._t("tab_diag"))
        self.tabs.setTabText(4, self._t("tab_settings"))

        self.kpi_tunnels_ttl.setText(self._t("kpi_tunnels"))
        self.kpi_speed_ttl.setText(self._t("kpi_speed"))
        self.kpi_peak_ttl.setText(self._t("kpi_peak"))
        self.kpi_traffic_ttl.setText(self._t("kpi_traffic"))
        self.kpi_pool_ttl.setText(self._t("kpi_pool"))

        self.lbl_chain_mode.setText(self._t("chain_mode"))
        self.rb_gas.setText(self._t("rb_gas"))
        self.rb_direct.setText(self._t("rb_direct"))
        if self.ip_badge.text().startswith(("Exit IP: Not", "آی‌پی خروجی:", "出口 IP:", "IP de Salida:", "Ausgangs-IP:", "Выходной IP:", "출구 IP:")):
            self.ip_badge.setText(self._t("exit_ip_unset"))
        self.lbl_recent_act.setText(self._t("recent_activity"))
        self.lbl_row_tip.setText(self._t("row_tip"))

        self.chk_fair_share.setText(self._t("fair_share"))
        self.lbl_free_quota.setText(self._t("free_quota"))
        self.add_g_btn.setText(self._t("add_gas"))
        self.rem_g_btn.setText(self._t("remove_btn"))
        self.lbl_gas_hint.setText(self._t("gas_hint"))
        self.gas_table.setHorizontalHeaderLabels(self._t("gas_headers"))

        self.lbl_backend_mode.setText(self._t("backend_mode"))
        self.deploy_render_btn.setText(self._t("deploy_render"))
        self.deploy_snap_btn.setText(self._t("deploy_snap"))
        self.deploy_cf_btn.setText(self._t("deploy_cf"))
        self.add_s_btn.setText(self._t("add_server"))
        self.rem_s_btn.setText(self._t("remove_btn"))
        self.srv_table.setHorizontalHeaderLabels(self._t("srv_headers"))

        self.lbl_diag_title.setText(self._t("diag_title"))
        self.diag_country_btn.setText(self._t("diag_check_btn"))
        self.diag_ip_ttl.setText(self._t("kpi_exit_ip"))
        self.diag_country_ttl.setText(self._t("kpi_country"))
        self.diag_isp_ttl.setText(self._t("kpi_isp"))
        self.lbl_conn_tests.setText(self._t("conn_tests"))
        self.d_ping_btn.setText(self._t("ping_all"))
        self.d_e2e_btn.setText(self._t("test_e2e"))

        self.lbl_auth_key.setText(self._t("auth_key"))
        self.chk_show_key.setText(self._t("show"))
        self.paste_btn.setText(self._t("paste"))
        self.lbl_theme.setText(self._t("theme"))
        self.lbl_language.setText(self._t("language"))
        self.lbl_listen_ip.setText(self._t("listen_ip"))
        self.lbl_port.setText(self._t("port"))
        self.chk_keep_awake.setText(self._t("keep_awake"))
        self.lbl_full_log.setText(self._t("full_log"))
        self.cpy_btn.setText(self._t("copy_logs"))
        self.clr_btn.setText(self._t("clear"))

        if getattr(self, "tray", None):
            self.tray_open_act.setText(self._t("tray_open"))
            self.tray_proxy_act.setText(self._t("tray_stop") if running else self._t("tray_start"))
            self.tray_tun_act.setText(self._t("tun_on") if self.tun_active else self._t("tun_off"))
            self.tray_exit_act.setText(self._t("tray_exit"))

    def _on_route_change(self):
        mode = "direct" if self.rb_direct.isChecked() else "gas"
        self._save_config()
        if self.proxy and self.proxy.running:
            self.proxy.route_mode = mode
            lbl = "DIRECT" if mode == "direct" else "GAS"
            self.status_pill.setText(f"● RUNNING · {lbl}")
            self.log(f"Switched live chain mode to: {lbl}")

    # --- GAS Account CRUD ---
    def add_account(self):
        dlg = EntryDialog(self, "Add Google Apps Script Account", "Account Name:", "GAS Web App URL:", f"GAS {len(self.pool.accounts) + 1}")
        if dlg.exec() == QDialog.Accepted:
            name, url = dlg.values()
            with self.pool.lock:
                self.pool.accounts.append(GasAccount(name, url, True))
                self.pool.lock.notify_all()
            self._save_config()
            self._sync_tables()
            self.log(f"Added GAS account: {name}")

    def edit_account(self):
        row = self.gas_table.currentRow()
        if 0 <= row < len(self.pool.accounts):
            acc = self.pool.accounts[row]
            dlg = EntryDialog(self, "Edit GAS Account", "Account Name:", "GAS Web App URL:", acc.name, acc.url)
            if dlg.exec() == QDialog.Accepted:
                acc.name, acc.url = dlg.values()
                self._save_config()
                self._sync_tables()

    def toggle_account(self):
        row = self.gas_table.currentRow()
        if 0 <= row < len(self.pool.accounts):
            acc = self.pool.accounts[row]
            acc.enabled = not acc.enabled
            acc.cooldown_until = 0
            with self.pool.lock:
                self.pool.lock.notify_all()
            self._save_config()
            self._sync_tables()

    def move_account(self, delta: int):
        row = self.gas_table.currentRow()
        new_row = row + delta
        with self.pool.lock:
            if 0 <= row < len(self.pool.accounts) and 0 <= new_row < len(self.pool.accounts):
                self.pool.accounts[row], self.pool.accounts[new_row] = self.pool.accounts[new_row], self.pool.accounts[row]
            else:
                return
        self._save_config()
        self._sync_tables()
        self.gas_table.selectRow(new_row)

    def remove_account(self):
        row = self.gas_table.currentRow()
        if 0 <= row < len(self.pool.accounts):
            with self.pool.lock:
                removed = self.pool.accounts.pop(row)
            self._save_config()
            self._sync_tables()
            self.log(f"Removed GAS account: {removed.name}")

    # --- Backend Server CRUD ---
    def deploy_on_render(self):
        dlg = RenderDeployDialog(self, self.auth_edit.text().strip() or "change-me-freednet-key")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()

        def worker():
            tid = self._start_busy(f"Deploying {params['name']} on Render ({params['region']})...")
            try:
                headers = {
                    "Authorization": f"Bearer {params['api_key']}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "FreeDnet-Client/2.0",
                }
                req_owners = urllib.request.Request("https://api.render.com/v1/owners?limit=20", headers=headers, method="GET")
                with _DIRECT_OPENER.open(req_owners, timeout=20.0) as resp:
                    owners = json.loads(resp.read().decode("utf-8", errors="replace"))
                if not owners or not isinstance(owners, list):
                    raise RuntimeError("No Render workspace/owner found for this API key.")
                owner_id = owners[0]["owner"]["id"]

                payload = {
                    "type": "web_service",
                    "name": params["name"],
                    "ownerId": owner_id,
                    "repo": params["repo"],
                    "branch": "main",
                    "autoDeployTrigger": "commit",
                    "envVars": [{"key": "AUTH_KEY", "value": params["auth_key"]}],
                    "serviceDetails": {
                        "runtime": "docker",
                        "plan": "free",
                        "region": params["region"],
                        "envSpecificDetails": {
                            "dockerfilePath": "./Dockerfile",
                            "dockerContext": ".",
                        },
                    },
                }
                req_create = urllib.request.Request(
                    "https://api.render.com/v1/services",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with _DIRECT_OPENER.open(req_create, timeout=30.0) as resp:
                    created = json.loads(resp.read().decode("utf-8", errors="replace"))

                svc = created.get("service", {})
                svc_url = (svc.get("serviceDetails") or {}).get("url", "").strip()
                dash_url = svc.get("dashboardUrl", "")
                if not svc_url:
                    raise RuntimeError(f"Service created, but URL missing. Dashboard: {dash_url}")

                label = f"Render ({params['region']})"
                self._pending_render_srv = (label, svc_url)
                self.log(f"Render Deploy STARTED -> {svc_url} | Region: {params['region']} | Build takes ~2-3 min ({dash_url})")
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:260]
                self.log(f"Render Deploy FAILED (HTTP {e.code}): {err_body}")
            except Exception as e:
                self.log(f"Render Deploy FAILED: {e}")
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def deploy_on_snapdeploy(self):
        dlg = SnapDeployDialog(self, self.auth_edit.text().strip() or "change-me-freednet-key")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()

        def worker():
            tid = self._start_busy(f"Deploying {params['name']} on SnapDeploy...")
            try:
                headers = {
                    "X-API-Key": params["api_key"],
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "FreeDnet-Client/2.0",
                }
                payload = {
                    "repo": params["repo"],
                    "name": params["name"],
                    "branch": "main",
                    "size": "small",
                    "env": {"AUTH_KEY": params["auth_key"]},
                }
                req = urllib.request.Request(
                    "https://snapdeploy.dev/api/mobile/deploy",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with _DIRECT_OPENER.open(req, timeout=30.0) as resp:
                    created = json.loads(resp.read().decode("utf-8", errors="replace"))

                svc_url = str(created.get("url") or "").strip()
                if not svc_url:
                    raise RuntimeError(f"SnapDeploy returned no URL: {created}")

                self._pending_render_srv = (f"SnapDeploy ({params['name']})", svc_url)
                self.log(f"SnapDeploy STARTED -> {svc_url} (build takes ~2-5 min)")
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:260]
                self.log(f"SnapDeploy FAILED (HTTP {e.code}): {err_body}")
            except Exception as e:
                self.log(f"SnapDeploy FAILED: {e}")
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def _load_cf_worker_source(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cloudflare", "worker.js"),
            os.path.join(BASE_DIR, "cloudflare", "worker.js"),
            _resource_path(os.path.join("cloudflare", "worker.js")),
        ]
        for cp in candidates:
            if os.path.exists(cp):
                with open(cp, "r", encoding="utf-8") as f:
                    return f.read()
        raise FileNotFoundError("cloudflare/worker.js not found. Make sure cloudflare/worker.js exists in the repo.")

    def deploy_on_cloudflare(self):
        dlg = CloudflareDeployDialog(self, self.auth_edit.text().strip() or "change-me-freednet-key")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()

        def worker():
            tid = self._start_busy(f"Deploying {params['name']} on Cloudflare...")
            try:
                worker_js = self._load_cf_worker_source()
                acc_id = params["account_id"]
                name = params["name"]
                headers_auth = {
                    "Authorization": f"Bearer {params['api_token']}",
                    "User-Agent": "FreeDnet-Client/2.0",
                }

                # 1. Fetch workers.dev subdomain
                req_sub = urllib.request.Request(
                    f"https://api.cloudflare.com/client/v4/accounts/{acc_id}/workers/subdomain",
                    headers={**headers_auth, "Accept": "application/json"},
                    method="GET",
                )
                with _DIRECT_OPENER.open(req_sub, timeout=20.0) as resp:
                    sub_data = json.loads(resp.read().decode("utf-8", errors="replace"))
                subdomain = (sub_data.get("result") or {}).get("subdomain", "").strip()
                if not subdomain:
                    raise RuntimeError("No workers.dev subdomain found. Enable workers.dev once in Cloudflare Dashboard.")

                # 2. Multipart upload Worker + SQLite Durable Object migration
                def build_multipart(include_migration: bool):
                    boundary = f"----FreeDnetCF{int(time.time() * 1000)}"
                    meta = {
                        "main_module": "worker.js",
                        "compatibility_date": "2025-01-01",
                        "bindings": [
                            {"type": "durable_object_namespace", "name": "TUNNEL_DO", "class_name": "TunnelSessionDO"},
                            {"type": "plain_text", "name": "AUTH_KEY", "text": params["auth_key"]},
                            {"type": "plain_text", "name": "LOCATION_HINT", "text": params["location_hint"]},
                        ],
                    }
                    if include_migration:
                        meta["migrations"] = {"new_sqlite_classes": ["TunnelSessionDO"], "tag": "v1"}
                    parts = [
                        (
                            f"--{boundary}\r\n"
                            f'Content-Disposition: form-data; name="metadata"\r\n'
                            f"Content-Type: application/json\r\n\r\n"
                            f"{json.dumps(meta)}\r\n"
                        ).encode("utf-8"),
                        (
                            f"--{boundary}\r\n"
                            f'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n'
                            f"Content-Type: application/javascript+module\r\n\r\n"
                            f"{worker_js}\r\n"
                            f"--{boundary}--\r\n"
                        ).encode("utf-8"),
                    ]
                    return b"".join(parts), f"multipart/form-data; boundary={boundary}"

                url_upload = f"https://api.cloudflare.com/client/v4/accounts/{acc_id}/workers/scripts/{name}"
                try:
                    body_bytes, ctype = build_multipart(include_migration=True)
                    req_up = urllib.request.Request(
                        url_upload,
                        data=body_bytes,
                        headers={**headers_auth, "Content-Type": ctype},
                        method="PUT",
                    )
                    with _DIRECT_OPENER.open(req_up, timeout=30.0) as resp:
                        resp.read()
                except urllib.error.HTTPError as e:
                    err_txt = e.read().decode("utf-8", errors="replace")
                    if e.code == 400 and "migration" in err_txt.lower():
                        body_bytes, ctype = build_multipart(include_migration=False)
                        req_up2 = urllib.request.Request(
                            url_upload,
                            data=body_bytes,
                            headers={**headers_auth, "Content-Type": ctype},
                            method="PUT",
                        )
                        with _DIRECT_OPENER.open(req_up2, timeout=30.0) as resp:
                            resp.read()
                    else:
                        raise RuntimeError(f"HTTP {e.code}: {err_txt[:260]}")

                # 3. Enable workers.dev route for this script
                req_en = urllib.request.Request(
                    f"https://api.cloudflare.com/client/v4/accounts/{acc_id}/workers/scripts/{name}/subdomain",
                    data=json.dumps({"enabled": True}).encode("utf-8"),
                    headers={**headers_auth, "Content-Type": "application/json"},
                    method="POST",
                )
                with _DIRECT_OPENER.open(req_en, timeout=20.0) as resp:
                    resp.read()

                worker_url = f"https://{name}.{subdomain}.workers.dev"
                self._pending_render_srv = (f"Cloudflare ({name})", worker_url)
                self.log(f"Cloudflare Deploy OK -> {worker_url}")
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:260]
                self.log(f"Cloudflare Deploy FAILED (HTTP {e.code}): {err_body}")
            except Exception as e:
                self.log(f"Cloudflare Deploy FAILED: {e}")
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def add_server(self):
        dlg = EntryDialog(self, "Add Backend Server", "Server Name:", "Server URL (https://...):", f"Server {len(self.server_pool.servers) + 1}")
        if dlg.exec() == QDialog.Accepted:
            name, url = dlg.values()
            with self.server_pool.lock:
                self.server_pool.servers.append(BackendServer(name, url, True))
            self._refresh_target_dropdown()
            self._save_config()
            self._sync_tables()
            self.log(f"Added backend server: {name} ({url})")

    def edit_server(self):
        row = self.srv_table.currentRow()
        if 0 <= row < len(self.server_pool.servers):
            srv = self.server_pool.servers[row]
            old_name = srv.name
            dlg = EntryDialog(self, "Edit Backend Server", "Server Name:", "Server URL (https://...):", srv.name, srv.url)
            if dlg.exec() == QDialog.Accepted:
                srv.name, srv.url = dlg.values()[0], dlg.values()[1].rstrip("/")
                if self.server_pool.server_mode == f"only:{old_name}":
                    self.server_pool.set_mode(f"only:{srv.name}")
                self._refresh_target_dropdown()
                self._save_config()
                self._sync_tables()

    def toggle_server(self):
        row = self.srv_table.currentRow()
        if 0 <= row < len(self.server_pool.servers):
            srv = self.server_pool.servers[row]
            srv.enabled = not srv.enabled
            srv.cooldown_until = 0
            srv.consecutive_failures = 0
            self._save_config()
            self._sync_tables()

    def move_server(self, delta: int):
        row = self.srv_table.currentRow()
        new_row = row + delta
        with self.server_pool.lock:
            if 0 <= row < len(self.server_pool.servers) and 0 <= new_row < len(self.server_pool.servers):
                self.server_pool.servers[row], self.server_pool.servers[new_row] = (
                    self.server_pool.servers[new_row],
                    self.server_pool.servers[row],
                )
            else:
                return
        self._refresh_target_dropdown()
        self._save_config()
        self._sync_tables()
        self.srv_table.selectRow(new_row)

    def remove_server(self):
        row = self.srv_table.currentRow()
        if 0 <= row < len(self.server_pool.servers):
            with self.server_pool.lock:
                removed = self.server_pool.servers.pop(row)
            self._refresh_target_dropdown()
            self._save_config()
            self._sync_tables()
            self.log(f"Removed backend server: {removed.name}")

    # --- Actions ---
    def test_all_accounts(self):
        self._save_config()
        auth_key = self.auth_edit.text().strip()
        direct = self.rb_direct.isChecked()

        if direct:
            if not self.server_pool.servers:
                QMessageBox.information(self, "No Servers", "Please add at least one Backend Server URL first.")
                return

            def worker_direct():
                tid = self._start_busy("Pinging backend servers...")
                try:
                    self._run_ping_all_direct(auth_key)
                finally:
                    self._stop_busy(tid)

            threading.Thread(target=worker_direct, daemon=True).start()
            return

        if not self.pool.accounts:
            QMessageBox.information(self, "No Accounts", "Please add at least one GAS Web App URL first.")
            return

        def worker():
            tid = self._start_busy("Pinging relays & servers...")
            try:
                self._run_ping_all_gas(auth_key)
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def _run_ping_all_direct(self, auth_key):
        if True:
            if True:
                for srv in list(self.server_pool.servers):
                    for attempt in range(7):
                        t0 = time.time()
                        res = post_direct_to_server(srv.url, {"authKey": auth_key, "action": "ping"}, auth_key, timeout=45.0)
                        lat = int((time.time() - t0) * 1000)
                        if res.get("ok"):
                            self.server_pool.report_success(srv, lat)
                            self.log(f"Direct Ping OK [{srv.name}]: {lat} ms (active: {res.get('activeSessions', 0)})")
                            break
                        det = str(res.get("detail") or "")
                        if ("Waking up" in det or res.get("error") == "HTTP 503") and attempt < 6:
                            self.log(f"Backend [{srv.name}] is waking up (503), waiting 10s ({attempt + 1}/6)...")
                            time.sleep(10)
                            continue
                        self.server_pool.report_failure(srv, res.get("error", "Error"))
                        self.log(f"Direct Ping FAILED [{srv.name}]: {res.get('error')}")
                        break

    def _run_ping_all_gas(self, auth_key):
        if True:
            servers = self.server_pool.get_candidates()
            for acc in list(self.pool.accounts):
                srv = servers[0]
                payload = {"authKey": auth_key, "action": "ping"}
                if srv is not None:
                    payload["serverUrl"] = srv.url
                t0 = time.time()
                res, _ = post_to_gas(acc.url, payload, timeout=45.0)
                lat = int((time.time() - t0) * 1000)
                if res.get("ok"):
                    acc.last_latency_ms = lat
                    acc.last_error = ""
                    self.log(f"GAS Ping OK [{acc.name}]: {lat} ms")
                else:
                    acc.last_error = res.get("error", "Error")
                    self.log(f"GAS Ping FAILED [{acc.name}]: {acc.last_error}")

            if any(s is not None for s in servers):
                first_gas = next((a for a in self.pool.accounts if a.enabled), self.pool.accounts[0])
                for srv in list(self.server_pool.servers):
                    for attempt in range(7):
                        t0 = time.time()
                        res, _ = post_to_gas(first_gas.url, {"authKey": auth_key, "action": "ping", "serverUrl": srv.url}, timeout=45.0)
                        lat = int((time.time() - t0) * 1000)
                        if res.get("ok"):
                            self.server_pool.report_success(srv, lat)
                            self.log(f"Backend OK [{srv.name}]: {lat} ms (active: {res.get('activeSessions', 0)})")
                            break
                        det = str(res.get("detail") or "")
                        if ("Waking up" in det or "HTTP 503" in str(res.get("error", ""))) and attempt < 6:
                            self.log(f"Backend [{srv.name}] is waking up (503), waiting 10s ({attempt + 1}/6)...")
                            time.sleep(10)
                            continue
                        self.server_pool.report_failure(srv, res.get("error", "Error"))
                        self.log(f"Backend FAILED [{srv.name}]: {res.get('error')}")
                        break

    def test_proxy_e2e(self):
        if not (self.proxy and self.proxy.running):
            QMessageBox.information(self, "Proxy Stopped", "Click 'Start Proxy' first.")
            return
        host = self.config["listen_host"]
        port = self.config["listen_port"]

        def worker():
            tid = self._start_busy("Testing E2E proxy...")
            t0 = time.time()
            try:
                s = socket.create_connection((host, port), timeout=25)
                s.sendall(b"\x05\x01\x00")
                if s.recv(2) != b"\x05\x00":
                    raise RuntimeError("SOCKS5 auth failed")
                target = b"www.gstatic.com"
                s.sendall(b"\x05\x01\x00\x03" + bytes([len(target)]) + target + (80).to_bytes(2, "big"))
                rep = s.recv(10)
                if len(rep) < 2 or rep[1] != 0x00:
                    raise RuntimeError(f"SOCKS5 CONNECT failed (code {rep[1] if len(rep) > 1 else '?'})")
                s.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: www.gstatic.com\r\nConnection: close\r\n\r\n")
                resp = s.recv(256)
                s.close()
                lat = int((time.time() - t0) * 1000)
                first_line = resp.decode("latin-1", errors="replace").split("\r\n")[0]
                self.log(f"E2E Proxy Test OK via {host}:{port} -> {first_line} ({lat} ms)")
            except Exception as e:
                self.log(f"E2E Proxy Test FAILED -> {e}")
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def toggle_proxy(self):
        if self.proxy and self.proxy.running:
            if self.sys_proxy_active:
                set_windows_system_proxy(False)
                self.sys_proxy_active = False
                self.sys_proxy_btn.setText("Sys Proxy: OFF")
                self.sys_proxy_btn.setObjectName("")
                self.sys_proxy_btn.style().unpolish(self.sys_proxy_btn)
                self.sys_proxy_btn.style().polish(self.sys_proxy_btn)
            self.proxy.stop()
            self.proxy = None
            self._save_config()
            self.status_pill.setObjectName("StatusPillStopped")
            self.status_pill.setText("● STOPPED")
            self.status_pill.style().unpolish(self.status_pill)
            self.status_pill.style().polish(self.status_pill)
            self.start_btn.setObjectName("PrimaryBtn")
            self.start_btn.setText("▶  Start Proxy")
            self.start_btn.style().unpolish(self.start_btn)
            self.start_btn.style().polish(self.start_btn)
            return

        mode = "direct" if self.rb_direct.isChecked() else "gas"
        if mode == "gas" and not any(a.enabled for a in self.pool.accounts):
            QMessageBox.warning(self, "No Active GAS Accounts", "Enable at least one GAS account or switch to Direct mode.")
            return
        if mode == "direct" and not any(s.enabled for s in self.server_pool.servers):
            QMessageBox.warning(self, "No Active Servers", "Enable at least one Backend Server for Direct mode.")
            return

        self._save_config()
        conc, daily, rpm = GAS_QUOTA_PRESETS.get(self.preset_combo.currentText(), (18, 14000, 120))
        self.pool.update_limits(conc, daily, rpm, gas_mode=("fair" if self.chk_fair_share.isChecked() else "priority"))
        try:
            self.proxy = FreeDnetProxyServer(
                pool=self.pool,
                server_pool=self.server_pool,
                auth_key=self.config["auth_key"],
                host=self.config["listen_host"],
                port=self.config["listen_port"],
                log_fn=self.log,
                route_mode=mode,
                keep_awake=bool(self.chk_keep_awake.isChecked()),
            )
            self.proxy.start()
            lbl = "DIRECT" if mode == "direct" else "GAS"
            self.status_pill.setObjectName("StatusPillRunning")
            self.status_pill.setText(f"● RUNNING · {lbl}")
            self.status_pill.style().unpolish(self.status_pill)
            self.status_pill.style().polish(self.status_pill)
            self.start_btn.setObjectName("DangerBtn")
            self.start_btn.setText("■  Stop Proxy")
            self.start_btn.style().unpolish(self.start_btn)
            self.start_btn.style().polish(self.start_btn)
        except Exception as e:
            QMessageBox.critical(self, "Start Failed", str(e))
            self.proxy = None

    def _socks5_https_get(self, proxy_host: str, proxy_port: int, url: str, timeout: float = 15.0) -> str:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        s = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            s.close()
            raise RuntimeError("SOCKS5 auth failed")
        h_bytes = host.encode("utf-8")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(h_bytes)]) + h_bytes + port.to_bytes(2, "big"))
        rep = s.recv(10)
        if len(rep) < 2 or rep[1] != 0x00:
            s.close()
            raise RuntimeError(f"SOCKS5 connect failed ({rep[1] if len(rep) > 1 else '?'})")

        ctx = ssl.create_default_context()
        tls_sock = ctx.wrap_socket(s, server_hostname=host)
        req = f"GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: curl/8.0\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
        tls_sock.sendall(req.encode("ascii"))
        chunks = []
        while True:
            data = tls_sock.recv(8192)
            if not data:
                break
            chunks.append(data)
        tls_sock.close()
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        body = raw.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw else raw
        return body.strip()

    def check_country_ip(self):
        via_proxy = bool(self.proxy and self.proxy.running)
        host = self.config["listen_host"]
        port = self.config["listen_port"]

        def worker():
            tid = self._start_busy("Checking Exit IP & Country...")
            try:
                self._run_country_ip(via_proxy, host, port)
            finally:
                self._stop_busy(tid)

        threading.Thread(target=worker, daemon=True).start()

    def _run_country_ip(self, via_proxy, host, port):
        if True:
            self.log(f"Checking IP/Country ({'via FreeDnet proxy' if via_proxy else 'direct system connection'})...")
            for name, url in COUNTRY_CHECK_ENDPOINTS:
                try:
                    if via_proxy:
                        raw = self._socks5_https_get(host, port, url, timeout=18.0)
                    else:
                        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0", "Accept": "application/json"})
                        with _DIRECT_OPENER.open(req, timeout=12.0) as resp:
                            raw = resp.read().decode("utf-8", errors="replace")
                    data = json.loads(raw)
                    ip = data.get("ip") or data.get("query") or "?"
                    country = data.get("country_name") or data.get("country") or "?"
                    code = data.get("country_code") or ""
                    city = data.get("city") or ""
                    isp = (
                        (data.get("connection") or {}).get("isp")
                        if isinstance(data.get("connection"), dict)
                        else (data.get("org") or data.get("organization") or "")
                    )
                    loc = f"{country} ({code})" if code else str(country)
                    if city:
                        loc = f"{loc} - {city}"
                    self.log(f"Country Check OK [{name}]: IP={ip} | {loc} | {isp}")
                    self._pending_ip_info = (str(ip), str(loc), str(isp or "-"))
                    return
                except Exception as e:
                    self.log(f"Country Check [{name}] failed ({e}), trying next...")
            self.log("Country Check FAILED: All IP lookup services timed out.")

    def _download_with_fallback(self, url: str, timeout: float = 35.0) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "FreeDnet-Client/2.0"})
        try:
            with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            if self.proxy and self.proxy.running:
                proxy_url = f"http://{self.config['listen_host']}:{self.config['listen_port']}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
                with opener.open(req, timeout=60.0) as resp:
                    return resp.read()
            raise

    def _ensure_xray_tun_assets(self) -> bool:
        os.makedirs(XRAY_DIR, exist_ok=True)
        if not os.path.exists(XRAY_EXE):
            self.log("Downloading Xray-core Windows x64...")
            zbytes = self._download_with_fallback(XRAY_ZIP_URL)
            with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                for member in zf.namelist():
                    base = os.path.basename(member)
                    if base in ("xray.exe", "geoip.dat", "geosite.dat"):
                        with open(os.path.join(XRAY_DIR, base), "wb") as out:
                            out.write(zf.read(member))
            self.log("Xray-core x64 extracted.")
        if not os.path.exists(WINTUN_DLL):
            self.log("Downloading Wintun driver (amd64)...")
            wbytes = self._download_with_fallback(WINTUN_ZIP_URL)
            with zipfile.ZipFile(io.BytesIO(wbytes)) as zf:
                for member in zf.namelist():
                    if member.endswith("bin/amd64/wintun.dll"):
                        with open(WINTUN_DLL, "wb") as out:
                            out.write(zf.read(member))
                        break
            self.log("Wintun.dll ready.")
        return os.path.exists(XRAY_EXE) and os.path.exists(WINTUN_DLL)

    def _write_tun_config(self):
        host = self.config["listen_host"]
        port = int(self.config["listen_port"])
        bypass_domains = ["domain:script.google.com", "domain:googleusercontent.com", "domain:onrender.com", "domain:snapdeploy.app", "domain:snapdeploy.dev"]
        for srv in self.server_pool.servers:
            try:
                h = urllib.parse.urlsplit(srv.url).hostname
                if h:
                    bypass_domains.append(f"full:{h}")
            except Exception:
                pass

        cfg = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "tun-in",
                    "protocol": "tun",
                    "settings": {
                        "name": "FreeDnetTUN",
                        "desc": "FreeDnet",
                        "mtu": 1400,
                        "gateway": ["10.0.0.1/16"],
                        "dns": ["1.1.1.1", "8.8.8.8"],
                        "autoSystemRoutingTable": ["0.0.0.0/0"],
                        "autoOutboundsInterface": "auto",
                    },
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
                }
            ],
            "outbounds": [
                {
                    "tag": "freednet-socks",
                    "protocol": "socks",
                    "settings": {"servers": [{"address": host, "port": port}]},
                },
                {"tag": "direct", "protocol": "freedom", "settings": {}},
                {"tag": "block", "protocol": "blackhole", "settings": {}},
            ],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {"type": "field", "process": ["FreeDnet.exe", "python.exe", "pythonw.exe"], "outboundTag": "direct"},
                    {"type": "field", "port": "53", "outboundTag": "direct"},
                    {"type": "field", "ip": ["geoip:private", "127.0.0.1/8"], "outboundTag": "direct"},
                    {"type": "field", "domain": list(dict.fromkeys(bypass_domains)), "outboundTag": "direct"},
                    *(
                        [{"type": "field", "ip": ["geoip:ir"], "outboundTag": "direct"}]
                        if os.path.exists(os.path.join(XRAY_DIR, "geoip.dat"))
                        else []
                    ),
                    *(
                        [{"type": "field", "domain": ["geosite:category-ir", "regexp:.*\\.ir$"], "outboundTag": "direct"}]
                        if os.path.exists(os.path.join(XRAY_DIR, "geosite.dat"))
                        else []
                    ),
                    {"type": "field", "network": "tcp", "outboundTag": "freednet-socks"},
                    {"type": "field", "network": "udp", "outboundTag": "block"},
                ],
            },
        }
        with open(XRAY_TUN_CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    def _stop_tun_process(self):
        if self.xray_proc is not None:
            try:
                self.xray_proc.terminate()
                self.xray_proc.wait(timeout=3)
            except Exception:
                try:
                    self.xray_proc.kill()
                except Exception:
                    pass
            self.xray_proc = None
        if sys.platform == "win32" and self.tun_active:
            try:
                subprocess.run(["taskkill", "/F", "/IM", "xray.exe"], capture_output=True)
            except Exception:
                pass
        self.tun_active = False

    def toggle_system_proxy(self):
        if sys.platform != "win32":
            QMessageBox.information(self, "Windows Only", "System Proxy toggle is for Windows.")
            return
        if self.sys_proxy_active:
            set_windows_system_proxy(False)
            self.sys_proxy_active = False
            self.sys_proxy_btn.setText("Sys Proxy: OFF")
            self.sys_proxy_btn.setObjectName("")
            self.sys_proxy_btn.style().unpolish(self.sys_proxy_btn)
            self.sys_proxy_btn.style().polish(self.sys_proxy_btn)
            self.log("Windows System Proxy disabled.")
            return
        if not (self.proxy and self.proxy.running):
            QMessageBox.warning(self, "Start Proxy First", "Please click 'Start Proxy' first.")
            return
        host = self.config["listen_host"]
        port = int(self.config["listen_port"])
        if set_windows_system_proxy(True, host, port):
            self.sys_proxy_active = True
            self.sys_proxy_btn.setText("Sys Proxy: ON")
            self.sys_proxy_btn.setObjectName("PrimaryBtn")
            self.sys_proxy_btn.style().unpolish(self.sys_proxy_btn)
            self.sys_proxy_btn.style().polish(self.sys_proxy_btn)
            self.log(f"Windows System Proxy set to {host}:{port}")

    def toggle_tun_mode(self):
        if self.tun_active:
            self._stop_tun_process()
            self.tun_btn.setText("TUN Mode: OFF")
            self.tun_btn.setObjectName("")
            self.tun_btn.style().unpolish(self.tun_btn)
            self.tun_btn.style().polish(self.tun_btn)
            self.log("Xray TUN Mode stopped (FreeDnetTUN adapter removed).")
            return

        if sys.platform != "win32":
            QMessageBox.information(self, "Windows Only", "Wintun TUN Mode is designed for Windows 10/11 x64.")
            return

        if not is_windows_admin():
            res = QMessageBox.question(
                self,
                "Administrator Required",
                "Creating the FreeDnetTUN virtual network adapter requires Administrator privileges.\nRestart FreeDnet as Administrator and auto-start TUN now?",
            )
            if res == QMessageBox.Yes:
                launch_arg = "--auto-tun" if getattr(sys, "frozen", False) else f'"{os.path.abspath(__file__)}" --auto-tun'
                ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, launch_arg, BASE_DIR, 1)
                self._quit_app()
            return

        if not (self.proxy and self.proxy.running):
            self.toggle_proxy()
            if not (self.proxy and self.proxy.running):
                return

        self.tun_btn.setEnabled(False)
        self.tun_btn.setText("Starting TUN...")

        def worker():
            tid = self._start_busy("Preparing Xray TUN & Wintun...")
            try:
                ok = self._ensure_xray_tun_assets()
                if not ok:
                    raise RuntimeError("Failed to prepare xray.exe or wintun.dll")
                self._write_tun_config()
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self.xray_proc = subprocess.Popen(
                    [XRAY_EXE, "run", "-c", XRAY_TUN_CFG],
                    cwd=XRAY_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=flags,
                )
                time.sleep(1.5)
                if self.xray_proc.poll() is not None:
                    out = ""
                    try:
                        out, _ = self.xray_proc.communicate(timeout=2)
                    except Exception:
                        pass
                    err_line = " | ".join(ln.strip() for ln in out.splitlines() if ln.strip())[-220:]
                    raise RuntimeError(err_line or "xray.exe exited unexpectedly (check Admin rights / Wintun)")
                self.tun_active = True
                self.log("Xray TUN Mode ACTIVE -> Virtual adapter 'FreeDnetTUN' created (visible in Control Panel & Task Manager).")
            except Exception as e:
                self.tun_active = False
                self.log(f"TUN Mode FAILED -> {e}")
            finally:
                self._stop_busy(tid)
                self._tun_ui_dirty = True

        threading.Thread(target=worker, daemon=True).start()

    def _update_tun_btn_ui(self):
        self.tun_btn.setEnabled(True)
        if self.tun_active:
            self.tun_btn.setText("TUN Mode: ON")
            self.tun_btn.setObjectName("PrimaryBtn")
        else:
            self.tun_btn.setText("TUN Mode: OFF")
            self.tun_btn.setObjectName("")
        self.tun_btn.style().unpolish(self.tun_btn)
        self.tun_btn.style().polish(self.tun_btn)

    def _sync_tables(self):
        total_reqs = sum(a.total_requests for a in self.pool.accounts)
        self.gas_table.setRowCount(len(self.pool.accounts))
        for idx, acc in enumerate(self.pool.accounts):
            share = f"{(acc.total_requests / total_reqs * 100):.1f}%" if total_reqs > 0 else "0.0%"
            lat = f"{acc.last_latency_ms} ms" if acc.last_latency_ms > 0 else "-"
            status = acc.status_label(self.pool.daily_limit)
            vals = [f"#{idx + 1}", acc.name, status, f"{acc.active}/{self.pool.max_concurrent}", f"{acc.daily_requests}/{self.pool.daily_limit}", share, lat, acc.url]
            for c, v in enumerate(vals):
                item = self.gas_table.item(idx, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.gas_table.setItem(idx, c, item)
                item.setText(str(v))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if c in (1, 7) else Qt.AlignCenter)
                if c == 2:
                    if getattr(self, "_current_theme_is_light", False):
                        color = "#15803d" if status in ("Ready", "Active") else ("#786252" if status == "Disabled" else "#b45309")
                    else:
                        color = "#34d399" if status in ("Ready", "Active") else ("#94a3b8" if status == "Disabled" else "#fbbf24")
                    item.setForeground(QColor(color))

        self.srv_table.setRowCount(len(self.server_pool.servers))
        for idx, srv in enumerate(self.server_pool.servers):
            lat = f"{srv.last_latency_ms} ms" if srv.last_latency_ms > 0 else "-"
            status = srv.status_label
            vals = [f"#{idx + 1}", srv.name, status, str(srv.total_requests), str(srv.errors), lat, srv.url]
            for c, v in enumerate(vals):
                item = self.srv_table.item(idx, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.srv_table.setItem(idx, c, item)
                item.setText(str(v))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if c in (1, 6) else Qt.AlignCenter)
                if c == 2:
                    if getattr(self, "_current_theme_is_light", False):
                        color = "#15803d" if status == "Ready" else ("#786252" if status == "Disabled" else "#b91c1c")
                    else:
                        color = "#34d399" if status == "Ready" else ("#94a3b8" if status == "Disabled" else "#f87171")
                    item.setForeground(QColor(color))

        enabled_gas = sum(1 for a in self.pool.accounts if a.enabled)
        enabled_srv = sum(1 for s in self.server_pool.servers if s.enabled)
        self.kpi_pool.setText(f"GAS {enabled_gas}/{len(self.pool.accounts)}  |  SRV {enabled_srv}/{len(self.server_pool.servers)}")

    def _on_ui_tick(self):
        lines = []
        while len(lines) < 60:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            for ln in lines:
                self.log_console.append(ln)
                self.ov_log_preview.append(ln)
            for box, max_b, keep_b in ((self.log_console, 500, 400), (self.ov_log_preview, 120, 80)):
                doc = box.document()
                if doc.blockCount() > max_b:
                    cursor = box.textCursor()
                    cursor.movePosition(cursor.Start)
                    cursor.movePosition(cursor.Down, cursor.KeepAnchor, doc.blockCount() - keep_b)
                    cursor.removeSelectedText()

        self._sync_tables()
        now = time.time()
        dt = max(0.1, now - self._last_stat_ts)
        self._last_stat_ts = now
        if self.proxy and self.proxy.running:
            tx = self.proxy.tx_bytes
            rx = self.proxy.rx_bytes
            tx_rate = max(0.0, (tx - self._prev_tx) / dt)
            rx_rate = max(0.0, (rx - self._prev_rx) / dt)
            self._prev_tx, self._prev_rx = tx, rx
            self._peak_tx_rate = max(self._peak_tx_rate, tx_rate)
            self._peak_rx_rate = max(self._peak_rx_rate, rx_rate)

            self.kpi_tunnels.setText(str(self.proxy.active_tunnels))
            self.kpi_speed.setText(f"{fmt_rate(rx_rate)} ↓  |  {fmt_rate(tx_rate)} ↑")
            self.kpi_peak.setText(f"{fmt_rate(self._peak_rx_rate)} ↓  |  {fmt_rate(self._peak_tx_rate)} ↑")
            self.kpi_traffic.setText(f"{fmt_bytes(rx)} ↓  |  {fmt_bytes(tx)} ↑")
            if now - self._last_save_ts > 30:
                self._last_save_ts = now
                try:
                    self._save_config()
                except Exception:
                    pass
        else:
            self.kpi_tunnels.setText("0")
            self.kpi_speed.setText("0 B/s  |  0 B/s")

        if self._tun_ui_dirty:
            self._tun_ui_dirty = False
            self._update_tun_btn_ui()

        if self._pending_render_srv is not None:
            srv_name, srv_url = self._pending_render_srv
            self._pending_render_srv = None
            with self.server_pool.lock:
                self.server_pool.servers.append(BackendServer(srv_name, srv_url, True))
            self._refresh_target_dropdown()
            self._save_config()
            self._sync_tables()

        if self._pending_ip_info is not None:
            ip, loc, isp = self._pending_ip_info
            self._pending_ip_info = None
            self.ip_badge.setText(f"Exit IP: {ip} ({loc})")
            self.diag_ip_val.setText(ip)
            self.diag_country_val.setText(loc)
            self.diag_isp_val.setText(isp)

        with self._busy_lock:
            active_labels = list(self._busy_tasks.values())
        if active_labels:
            frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
            self._spin_idx = (self._spin_idx + 1) % len(frames)
            self.busy_label.setText(f"{frames[self._spin_idx]} {active_labels[-1]}")
            self.busy_label.setVisible(True)
            self.busy_bar.setVisible(True)
        else:
            self.busy_label.setVisible(False)
            self.busy_bar.setVisible(False)

        if getattr(self, "tray", None):
            running = bool(self.proxy and self.proxy.running)
            self.tray_proxy_act.setText("Stop Proxy" if running else "Start Proxy")
            self.tray_tun_act.setText("TUN Mode: ON" if self.tun_active else "TUN Mode: OFF")


def main():
    enable_windows_dpi_awareness()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    ico = _resource_path("freednet.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    win = FreeDnetProWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
