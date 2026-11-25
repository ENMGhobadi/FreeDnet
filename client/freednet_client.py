#!/usr/bin/env python3
"""
FreeDnet Local Client (Windows 11 Modern GUI + SOCKS5/HTTP Proxy + Direct / Multi-GAS & Multi-Server Failover)
Uses only Python 3.8+ standard library.
"""

import base64
from collections import deque
import ctypes
import datetime
import json
import os
import queue
import socket
import struct
import threading
import time
import urllib.error
import urllib.request

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    HAS_TK = True
except ImportError:
    HAS_TK = False
    tk = None
    ttk = None
    messagebox = None
    simpledialog = None

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEFAULT_CONFIG = {
    "auth_key": "change-me-freednet-key",
    "listen_host": "127.0.0.1",
    "listen_port": 1080,
    "route_mode": "gas",  # "gas" or "direct"
    "max_concurrent_per_account": 20,
    "daily_limit_per_account": 18000,
    "max_rpm_per_account": 120,
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
    Automatically fails over to the next healthy server when one goes down or hits limits.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.servers: list[BackendServer] = []

    def load_servers(self, items: list[dict]):
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

    def get_candidates(self, require_explicit: bool = False) -> list[BackendServer | None]:
        with self.lock:
            now = time.time()
            enabled = [s for s in self.servers if s.enabled and s.url]
            if not enabled:
                return [] if require_explicit else [None]
            healthy = [s for s in enabled if now >= s.cooldown_until]
            if not healthy:
                healthy = sorted(enabled, key=lambda s: s.cooldown_until)
            else:
                healthy.sort(key=lambda s: (s.consecutive_failures, s.total_requests))
            return healthy

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
    Long-session, quota-preserving scheduler across 1..N Google Apps Script accounts.
    """

    def __init__(self, max_concurrent: int = 20, daily_limit: int = 18000, max_rpm: int = 120):
        self.lock = threading.Condition()
        self.accounts: list[GasAccount] = []
        self.max_concurrent = max(1, min(int(max_concurrent), 28))
        self.daily_limit = max(100, int(daily_limit))
        self.max_rpm = max(10, int(max_rpm))

    def update_limits(self, max_concurrent: int, daily_limit: int, max_rpm: int):
        with self.lock:
            self.max_concurrent = max(1, min(int(max_concurrent), 28))
            self.daily_limit = max(100, int(daily_limit))
            self.max_rpm = max(10, int(max_rpm))
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
                    chosen = min(
                        candidates,
                        key=lambda a: (a.active, a.daily_requests, len(a.rpm_timestamps), a.total_requests),
                    )
                    chosen.active += 1
                    chosen.total_requests += 1
                    chosen.daily_requests += 1
                    chosen.rpm_timestamps.append(now)
                    return chosen

                any_under_daily = any(
                    acc.enabled
                    and acc.url
                    and acc not in exclude
                    and acc.daily_requests < self.daily_limit
                    for acc in self.accounts
                )
                if not any_under_daily:
                    raise RuntimeError("No GAS accounts with remaining daily quota")

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


_OPENER = urllib.request.build_opener(NoPostRedirectHandler())


def post_to_gas(url: str, payload: dict, timeout: float = 25.0) -> tuple[dict, bool]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        if e.code == 429 or "too many" in raw.lower():
            return {"ok": False, "error": f"HTTP {e.code} rate limit"}, True
        return {"ok": False, "error": f"HTTP {e.code}: {raw[:120]}"}, False
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
        return {"ok": False, "error": f"Invalid JSON from GAS: {raw[:100]}"}, False


def post_direct_to_server(server_url: str, payload: dict, auth_key: str, timeout: float = 25.0) -> dict:
    tunnel_url = normalize_tunnel_url(server_url)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        tunnel_url,
        data=data,
        headers={"Content-Type": "application/json", "X-Auth-Key": auth_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        try:
            parsed = json.loads(raw)
            if e.code >= 500:
                parsed["upstreamError"] = True
            return parsed
        except Exception:
            return {"ok": False, "upstreamError": e.code >= 500 or e.code == 429, "error": f"HTTP {e.code}: {raw[:120]}"}
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
    ):
        self.pool = pool
        self.server_pool = server_pool
        self.auth_key = auth_key
        self.host = host
        self.port = port
        self.log = log_fn
        self.route_mode = route_mode  # "gas" or "direct"

        self.running = False
        self.server_sock = None
        self.active_tunnels = 0
        self.tx_bytes = 0
        self.rx_bytes = 0
        self._stat_lock = threading.Lock()

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(128)
        self.server_sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        mode_lbl = "Direct -> Backend" if self.route_mode == "direct" else "Via Google Apps Script"
        self.log(f"Proxy listening on {self.host}:{self.port} [{mode_lbl}] (SOCKS5 + HTTP)")

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

        # Direct Mode: 127.0.0.1:1080 -> Render / SnappDeploy / Oracle directly
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

        # GAS Mode: 127.0.0.1:1080 -> GAS Accounts (1/N) -> Backend Server
        if backend is not None:
            payload["serverUrl"] = backend.url

        excluded = set()
        last_err = "Unknown error"

        for _ in range(retries + 1):
            try:
                acc = self.pool.acquire(exclude=excluded)
            except RuntimeError as e:
                return {"ok": False, "error": str(e)}

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
                return {"ok": False, "error": last_err, "upstreamError": True}

            self.pool.release(acc, ok=False, latency_ms=lat, rate_limited=rate_limited, err_msg=last_err)
            excluded.add(acc)
            if not rate_limited and "Session not found" in last_err:
                break

        return {"ok": False, "error": last_err}

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
                self.log(f"Backend [{srv.name}] failed ({res.get('error')}), failing over...")
                continue
            if not res.get("upstreamError"):
                break
        return last_res, None

    def _accept_loop(self):
        while self.running and self.server_sock:
            try:
                client_sock, _ = self.server_sock.accept()
                client_sock.settimeout(15.0)
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

    def _handshake_socks5(self, sock: socket.socket):
        header = self._recv_exact(sock, 2)
        if not header or header[0] != 0x05:
            return None
        nmethods = header[1]
        self._recv_exact(sock, nmethods)
        sock.sendall(b"\x05\x00")

        req = self._recv_exact(sock, 4)
        if not req or req[1] != 0x01:
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

    def _relay_loop(self, client_sock: socket.socket, session_id: str, pinned_backend: BackendServer | None):
        prev_had_rx = False
        idle_cycles = 0
        while self.running:
            client_sock.settimeout(0.008 if prev_had_rx else 0.05)
            outgoing = b""
            try:
                while len(outgoing) < 131072:
                    chunk = client_sock.recv(32768)
                    if not chunk:
                        return
                    outgoing += chunk
            except socket.timeout:
                pass
            except OSError:
                return

            if outgoing:
                idle_cycles = 0
                with self._stat_lock:
                    self.tx_bytes += len(outgoing)
                b64_in = base64.b64encode(outgoing).decode("ascii")
                wait_ms = 0
            elif prev_had_rx:
                idle_cycles = 0
                b64_in = ""
                wait_ms = 0
            else:
                idle_cycles += 1
                b64_in = ""
                wait_ms = 2500 if idle_cycles == 1 else (5000 if idle_cycles == 2 else 9000)

            res = self._send_request(
                "sync",
                {"sessionId": session_id, "data": b64_in, "waitMs": wait_ms},
                backend=pinned_backend,
                retries=1,
            )
            if not res.get("ok"):
                return

            raw_out = base64.b64decode(res["data"]) if res.get("data") else b""
            prev_had_rx = len(raw_out) > 0
            if raw_out:
                idle_cycles = 0
                try:
                    client_sock.sendall(raw_out)
                    with self._stat_lock:
                        self.rx_bytes += len(raw_out)
                except OSError:
                    return

            if res.get("closed"):
                return


if HAS_TK:

    class EntryDialog(simpledialog.Dialog):
        def __init__(self, parent, title: str, name_label: str, url_label: str, name: str = "", url: str = ""):
            self.name_label = name_label
            self.url_label = url_label
            self.init_name = name
            self.init_url = url
            self.result_data = None
            super().__init__(parent, title=title)

        def body(self, master):
            ttk.Label(master, text=self.name_label).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            self.name_var = tk.StringVar(value=self.init_name)
            self.name_entry = ttk.Entry(master, textvariable=self.name_var, width=52)
            self.name_entry.grid(row=0, column=1, padx=8, pady=8)

            ttk.Label(master, text=self.url_label).grid(row=1, column=0, sticky="w", padx=8, pady=8)
            self.url_var = tk.StringVar(value=self.init_url)
            self.url_entry = ttk.Entry(master, textvariable=self.url_var, width=52)
            self.url_entry.grid(row=1, column=1, padx=8, pady=8)
            return self.name_entry

        def validate(self):
            name = self.name_var.get().strip()
            url = self.url_var.get().strip()
            if not name or not url.startswith("http"):
                messagebox.showwarning("Invalid Input", "Please enter a valid name and https://... URL.", parent=self)
                return False
            return True

        def apply(self):
            self.result_data = (self.name_var.get().strip(), self.url_var.get().strip())

    class FreeDnetApp:
        BG = "#f1f5f9"
        CARD = "#ffffff"
        BORDER = "#cbd5e1"
        TEXT = "#0f172a"
        MUTED = "#64748b"
        ACCENT = "#2563eb"
        ACCENT_HOVER = "#1d4ed8"
        GREEN = "#16a34a"
        RED = "#dc2626"

        def __init__(self, root: tk.Tk):
            self.root = root
            self.root.title("FreeDnet Client — Direct / Multi-GAS & Multi-Server Failover")
            self.root.geometry("980x740")
            self.root.minsize(860, 640)
            self.root.configure(bg=self.BG)

            self.config = self._load_config()
            self.pool = GasAccountPool(
                max_concurrent=self.config.get("max_concurrent_per_account", 20),
                daily_limit=self.config.get("daily_limit_per_account", 18000),
                max_rpm=self.config.get("max_rpm_per_account", 120),
            )
            self.pool.load_accounts(self.config.get("accounts", []))
            self.server_pool = ServerPool()
            self.server_pool.load_servers(self.config.get("servers", []))
            self.proxy = None
            self.log_queue = queue.Queue()
            self._last_save_ts = time.time()

            self._configure_styles()
            self._build_ui()
            self._sync_tables()
            self._schedule_ui_tick()
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        def _configure_styles(self):
            style = ttk.Style()
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure("TFrame", background=self.BG)
            style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI", 9))
            style.configure("Card.TLabel", background=self.CARD, foreground=self.TEXT, font=("Segoe UI", 9))
            style.configure("Muted.TLabel", background=self.CARD, foreground=self.MUTED, font=("Segoe UI", 8))
            style.configure("Section.TLabel", background=self.CARD, foreground=self.TEXT, font=("Segoe UI", 10, "bold"))
            style.configure("TCheckbutton", background=self.CARD, foreground=self.TEXT, font=("Segoe UI", 9))
            style.configure("TRadiobutton", background=self.CARD, foreground=self.TEXT, font=("Segoe UI", 9))
            style.configure("TNotebook", background=self.CARD, borderwidth=0)
            style.configure("TNotebook.Tab", font=("Segoe UI", 9, "bold"), padding=[12, 6])
            style.configure("Treeview", font=("Segoe UI", 9), rowheight=26, fieldbackground="#ffffff", background="#ffffff")
            style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#e2e8f0", foreground=self.TEXT)

        def _load_config(self) -> dict:
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return {**DEFAULT_CONFIG, **data}
                except Exception:
                    pass
            return dict(DEFAULT_CONFIG)

        def _save_config(self):
            self.config["auth_key"] = self.auth_var.get().strip()
            self.config["listen_host"] = self.host_var.get().strip() or "127.0.0.1"
            self.config["route_mode"] = self.route_var.get()
            try:
                self.config["listen_port"] = int(self.port_var.get().strip())
            except ValueError:
                self.config["listen_port"] = 1080
            try:
                self.config["max_concurrent_per_account"] = int(self.max_conc_var.get().strip())
            except ValueError:
                self.config["max_concurrent_per_account"] = 20
            try:
                self.config["daily_limit_per_account"] = int(self.daily_limit_var.get().strip())
            except ValueError:
                self.config["daily_limit_per_account"] = 18000
            try:
                self.config["max_rpm_per_account"] = int(self.max_rpm_var.get().strip())
            except ValueError:
                self.config["max_rpm_per_account"] = 120
            self.config["accounts"] = [acc.to_dict() for acc in self.pool.accounts]
            self.config["servers"] = [srv.to_dict() for srv in self.server_pool.servers]
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)

        def _on_close(self):
            try:
                self._save_config()
                if self.proxy and self.proxy.running:
                    self.proxy.stop()
            except Exception:
                pass
            self.root.destroy()

        def _make_btn(self, parent, text, cmd, primary=False, danger=False, width=None):
            bg = self.ACCENT if primary else (self.RED if danger else "#e2e8f0")
            fg = "#ffffff" if (primary or danger) else self.TEXT
            btn = tk.Button(
                parent,
                text=text,
                command=cmd,
                bg=bg,
                fg=fg,
                activebackground=self.ACCENT_HOVER if primary else "#cbd5e1",
                activeforeground=fg,
                relief="flat",
                bd=0,
                padx=12,
                pady=6,
                font=("Segoe UI", 9, "bold" if (primary or danger) else "normal"),
                cursor="hand2",
            )
            if width:
                btn.configure(width=width)
            return btn

        def _build_ui(self):
            header = tk.Frame(self.root, bg="#0f172a", padx=16, pady=12)
            header.pack(fill="x")

            title_box = tk.Frame(header, bg="#0f172a")
            title_box.pack(side="left")
            tk.Label(title_box, text="FreeDnet", font=("Segoe UI", 14, "bold"), fg="#ffffff", bg="#0f172a").pack(side="left")
            tk.Label(
                title_box,
                text="  Direct / Multi-GAS + Multi-Server Failover",
                font=("Segoe UI", 9),
                fg="#94a3b8",
                bg="#0f172a",
            ).pack(side="left", pady=(3, 0))

            self.start_btn = self._make_btn(header, "Start Proxy", self.toggle_proxy, primary=True, width=14)
            self.start_btn.pack(side="right", padx=(6, 0))

            self.e2e_btn = self._make_btn(header, "Test Proxy (E2E)", self.test_proxy_e2e)
            self.e2e_btn.pack(side="right", padx=6)

            self.test_btn = self._make_btn(header, "Ping All", self.test_all_accounts)
            self.test_btn.pack(side="right", padx=6)

            self.status_var = tk.StringVar(value="● Stopped")
            self.status_lbl = tk.Label(
                header, textvariable=self.status_var, font=("Segoe UI", 10, "bold"), fg="#f87171", bg="#0f172a"
            )
            self.status_lbl.pack(side="right", padx=14)

            main = ttk.Frame(self.root)
            main.pack(fill="both", expand=True, padx=14, pady=12)

            # Card 1: Settings & Chain Mode
            cfg_card = tk.Frame(main, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1, padx=12, pady=10)
            cfg_card.pack(fill="x", pady=(0, 10))

            ttk.Label(cfg_card, text="Connection, Chain Mode & Quota Protection", style="Section.TLabel").grid(
                row=0, column=0, columnspan=8, sticky="w", pady=(0, 8)
            )

            # Row 1: Route Mode
            ttk.Label(cfg_card, text="Chain Mode:", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
            self.route_var = tk.StringVar(value=self.config.get("route_mode", "gas"))
            mode_frame = tk.Frame(cfg_card, bg=self.CARD)
            mode_frame.grid(row=1, column=1, columnspan=7, sticky="w", pady=4)
            ttk.Radiobutton(
                mode_frame,
                text="Via Google Apps Script (Severe Filtering)",
                variable=self.route_var,
                value="gas",
                command=self._on_route_change,
            ).pack(side="left", padx=(0, 18))
            ttk.Radiobutton(
                mode_frame,
                text="Direct to Backend Servers (Fast / No GAS Quota)",
                variable=self.route_var,
                value="direct",
                command=self._on_route_change,
            ).pack(side="left")

            # Row 2: Auth & Listen
            ttk.Label(cfg_card, text="Auth Key:", style="Card.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=4)
            self.auth_var = tk.StringVar(value=self.config["auth_key"])
            self.auth_entry = ttk.Entry(cfg_card, textvariable=self.auth_var, show="*", width=26)
            self.auth_entry.grid(row=2, column=1, sticky="w", padx=4, pady=4)

            self.show_key_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(cfg_card, text="Show", variable=self.show_key_var, command=self._toggle_key).grid(row=2, column=2, padx=6)

            ttk.Label(cfg_card, text="Listen IP:", style="Card.TLabel").grid(row=2, column=3, sticky="w", padx=(12, 4))
            self.host_var = tk.StringVar(value=self.config["listen_host"])
            ttk.Entry(cfg_card, textvariable=self.host_var, width=14).grid(row=2, column=4, padx=4)

            ttk.Label(cfg_card, text="Port:", style="Card.TLabel").grid(row=2, column=5, sticky="w", padx=(10, 4))
            self.port_var = tk.StringVar(value=str(self.config["listen_port"]))
            ttk.Entry(cfg_card, textvariable=self.port_var, width=7).grid(row=2, column=6, padx=4)

            # Row 3: GAS Quota Limits
            ttk.Label(cfg_card, text="Max Concurrent:", style="Card.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=4)
            self.max_conc_var = tk.StringVar(value=str(self.config["max_concurrent_per_account"]))
            ttk.Entry(cfg_card, textvariable=self.max_conc_var, width=8).grid(row=3, column=1, sticky="w", padx=4, pady=4)

            ttk.Label(cfg_card, text="Daily Limit / Acc:", style="Card.TLabel").grid(row=3, column=2, sticky="w", padx=(6, 4))
            self.daily_limit_var = tk.StringVar(value=str(self.config["daily_limit_per_account"]))
            ttk.Entry(cfg_card, textvariable=self.daily_limit_var, width=9).grid(row=3, column=3, sticky="w", padx=4)

            ttk.Label(cfg_card, text="Max Req/Min:", style="Card.TLabel").grid(row=3, column=4, sticky="w", padx=(10, 4))
            self.max_rpm_var = tk.StringVar(value=str(self.config["max_rpm_per_account"]))
            ttk.Entry(cfg_card, textvariable=self.max_rpm_var, width=7).grid(row=3, column=5, sticky="w", padx=4)

            ttk.Label(cfg_card, text="(Safe: 20 conc, 18k/day, 120 rpm)", style="Muted.TLabel").grid(
                row=3, column=6, columnspan=2, sticky="w", padx=6
            )

            # Card 2: Tabbed Pools (GAS Accounts + Backend Servers)
            pool_card = tk.Frame(main, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1, padx=12, pady=10)
            pool_card.pack(fill="both", expand=True, pady=(0, 10))

            pool_top = tk.Frame(pool_card, bg=self.CARD)
            pool_top.pack(fill="x", pady=(0, 6))
            self.stats_var = tk.StringVar(value="Tunnels: 0  |  TX: 0.0 KB  |  RX: 0.0 KB")
            ttk.Label(pool_top, textvariable=self.stats_var, style="Muted.TLabel").pack(side="right")

            self.notebook = ttk.Notebook(pool_card)
            self.notebook.pack(fill="both", expand=True)

            # Tab 1: GAS Accounts
            gas_tab = tk.Frame(self.notebook, bg=self.CARD, pady=6)
            self.notebook.add(gas_tab, text="Google Apps Script Accounts")

            gas_wrap = tk.Frame(gas_tab, bg=self.CARD)
            gas_wrap.pack(fill="both", expand=True)
            cols = ("name", "status", "active", "daily", "share", "latency", "url")
            self.tree = ttk.Treeview(gas_wrap, columns=cols, show="headings", height=6)
            for col, text, w in [
                ("name", "Account", 105),
                ("status", "Status", 100),
                ("active", "Active", 65),
                ("daily", "Daily Quota", 110),
                ("share", "Session %", 75),
                ("latency", "Latency", 75),
                ("url", "GAS Web App URL", 290),
            ]:
                self.tree.heading(col, text=text)
                self.tree.column(col, width=w, anchor="w" if col in ("name", "url") else "center")
            self.tree.pack(side="left", fill="both", expand=True)
            sb1 = ttk.Scrollbar(gas_wrap, orient="vertical", command=self.tree.yview)
            sb1.pack(side="right", fill="y")
            self.tree.configure(yscrollcommand=sb1.set)

            gas_btns = tk.Frame(gas_tab, bg=self.CARD)
            gas_btns.pack(fill="x", pady=(8, 0))
            self._make_btn(gas_btns, "+ Add GAS Account", self.add_account, primary=True).pack(side="left", padx=(0, 6))
            self._make_btn(gas_btns, "Edit", self.edit_account).pack(side="left", padx=4)
            self._make_btn(gas_btns, "Enable / Disable", self.toggle_account).pack(side="left", padx=4)
            self._make_btn(gas_btns, "Remove", self.remove_account).pack(side="left", padx=4)

            # Tab 2: Backend Servers (Render / SnappDeploy / Oracle)
            srv_tab = tk.Frame(self.notebook, bg=self.CARD, pady=6)
            self.notebook.add(srv_tab, text="Backend Servers (Render / SnappDeploy / Oracle)")

            srv_wrap = tk.Frame(srv_tab, bg=self.CARD)
            srv_wrap.pack(fill="both", expand=True)
            scols = ("name", "status", "requests", "errors", "latency", "url")
            self.srv_tree = ttk.Treeview(srv_wrap, columns=scols, show="headings", height=6)
            for col, text, w in [
                ("name", "Server Name", 130),
                ("status", "Status", 105),
                ("requests", "Requests", 85),
                ("errors", "Errors", 70),
                ("latency", "Latency", 85),
                ("url", "Server URL (e.g. https://app.onrender.com)", 340),
            ]:
                self.srv_tree.heading(col, text=text)
                self.srv_tree.column(col, width=w, anchor="w" if col in ("name", "url") else "center")
            self.srv_tree.pack(side="left", fill="both", expand=True)
            sb2 = ttk.Scrollbar(srv_wrap, orient="vertical", command=self.srv_tree.yview)
            sb2.pack(side="right", fill="y")
            self.srv_tree.configure(yscrollcommand=sb2.set)

            srv_btns = tk.Frame(srv_tab, bg=self.CARD)
            srv_btns.pack(fill="x", pady=(8, 0))
            self._make_btn(srv_btns, "+ Add Server", self.add_server, primary=True).pack(side="left", padx=(0, 6))
            self._make_btn(srv_btns, "Edit", self.edit_server).pack(side="left", padx=4)
            self._make_btn(srv_btns, "Enable / Disable", self.toggle_server).pack(side="left", padx=4)
            self._make_btn(srv_btns, "Remove", self.remove_server).pack(side="left", padx=4)
            ttk.Label(
                srv_btns,
                text="Used for Direct mode & GAS auto-failover.",
                style="Muted.TLabel",
            ).pack(side="right")

            # Card 3: Activity Log
            log_card = tk.Frame(main, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1, padx=12, pady=10)
            log_card.pack(fill="both", expand=False)

            log_hdr = tk.Frame(log_card, bg=self.CARD)
            log_hdr.pack(fill="x", pady=(0, 6))
            ttk.Label(log_hdr, text="Activity Log", style="Section.TLabel").pack(side="left")
            ttk.Label(
                log_hdr,
                text="SOCKS5 / HTTP on 127.0.0.1:1080 — Switch between Direct and GAS chain anytime.",
                style="Muted.TLabel",
            ).pack(side="right")

            self.log_text = tk.Text(
                log_card,
                height=6,
                state="disabled",
                font=("Consolas", 9),
                bg="#0f172a",
                fg="#e2e8f0",
                insertbackground="#ffffff",
                relief="flat",
                padx=8,
                pady=6,
            )
            self.log_text.pack(fill="both", expand=True)

        def _toggle_key(self):
            self.auth_entry.configure(show="" if self.show_key_var.get() else "*")

        def _on_route_change(self):
            mode = self.route_var.get()
            self._save_config()
            if self.proxy and self.proxy.running:
                self.proxy.route_mode = mode
                mode_lbl = "Direct" if mode == "direct" else "GAS"
                self.status_var.set(f"● Running [{mode_lbl}] ({self.config['listen_host']}:{self.config['listen_port']})")
                self.log(f"Switched live chain mode to: {mode_lbl}")

        def log(self, msg: str):
            ts = time.strftime("%H:%M:%S")
            self.log_queue.put(f"[{ts}] {msg}\n")

        # --- GAS Account Actions ---
        def add_account(self):
            dlg = EntryDialog(
                self.root,
                "Add Google Apps Script Account",
                "Account Name:",
                "GAS Web App URL:",
                name=f"GAS {len(self.pool.accounts) + 1}",
            )
            if dlg.result_data:
                name, url = dlg.result_data
                with self.pool.lock:
                    self.pool.accounts.append(GasAccount(name, url, True))
                    self.pool.lock.notify_all()
                self._save_config()
                self._sync_tables()
                self.log(f"Added GAS account: {name}")

        def edit_account(self):
            sel = self.tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if 0 <= idx < len(self.pool.accounts):
                acc = self.pool.accounts[idx]
                dlg = EntryDialog(self.root, "Edit GAS Account", "Account Name:", "GAS Web App URL:", acc.name, acc.url)
                if dlg.result_data:
                    acc.name, acc.url = dlg.result_data
                    self._save_config()
                    self._sync_tables()

        def remove_account(self):
            sel = self.tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if 0 <= idx < len(self.pool.accounts):
                with self.pool.lock:
                    removed = self.pool.accounts.pop(idx)
                self._save_config()
                self._sync_tables()
                self.log(f"Removed GAS account: {removed.name}")

        def toggle_account(self):
            sel = self.tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if 0 <= idx < len(self.pool.accounts):
                acc = self.pool.accounts[idx]
                acc.enabled = not acc.enabled
                acc.cooldown_until = 0
                with self.pool.lock:
                    self.pool.lock.notify_all()
                self._save_config()
                self._sync_tables()

        # --- Backend Server Actions ---
        def add_server(self):
            dlg = EntryDialog(
                self.root,
                "Add Backend Server (Render / SnappDeploy / Oracle)",
                "Server Name:",
                "Server URL (https://...):",
                name=f"Server {len(self.server_pool.servers) + 1}",
            )
            if dlg.result_data:
                name, url = dlg.result_data
                with self.server_pool.lock:
                    self.server_pool.servers.append(BackendServer(name, url, True))
                self._save_config()
                self._sync_tables()
                self.log(f"Added backend server: {name} ({url})")

        def edit_server(self):
            sel = self.srv_tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if 0 <= idx < len(self.server_pool.servers):
                srv = self.server_pool.servers[idx]
                dlg = EntryDialog(
                    self.root, "Edit Backend Server", "Server Name:", "Server URL (https://...):", srv.name, srv.url
                )
                if dlg.result_data:
                    srv.name, srv.url = dlg.result_data[0], dlg.result_data[1].rstrip("/")
                    self._save_config()
                    self._sync_tables()

        def remove_server(self):
            sel = self.srv_tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if 0 <= idx < len(self.server_pool.servers):
                with self.server_pool.lock:
                    removed = self.server_pool.servers.pop(idx)
                self._save_config()
                self._sync_tables()
                self.log(f"Removed backend server: {removed.name}")

        def toggle_server(self):
            sel = self.srv_tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if 0 <= idx < len(self.server_pool.servers):
                srv = self.server_pool.servers[idx]
                srv.enabled = not srv.enabled
                srv.cooldown_until = 0
                srv.consecutive_failures = 0
                self._save_config()
                self._sync_tables()

        def test_all_accounts(self):
            self._save_config()
            auth_key = self.auth_var.get().strip()
            mode = self.route_var.get()

            if mode == "direct":
                if not self.server_pool.servers:
                    messagebox.showinfo("No Servers", "Please add at least one Backend Server URL first.")
                    return

                def worker_direct():
                    for srv in list(self.server_pool.servers):
                        t0 = time.time()
                        res = post_direct_to_server(srv.url, {"authKey": auth_key, "action": "ping"}, auth_key, timeout=15.0)
                        lat = int((time.time() - t0) * 1000)
                        if res.get("ok"):
                            self.server_pool.report_success(srv, lat)
                            self.log(f"Direct Ping OK [{srv.name}]: {lat} ms (active: {res.get('activeSessions', 0)})")
                        else:
                            self.server_pool.report_failure(srv, res.get("error", "Error"))
                            self.log(f"Direct Ping FAILED [{srv.name}]: {res.get('error')}")

                threading.Thread(target=worker_direct, daemon=True).start()
                return

            if not self.pool.accounts:
                messagebox.showinfo("No Accounts", "Please add at least one GAS Web App URL first.")
                return

            def worker():
                servers = self.server_pool.get_candidates()
                for acc in list(self.pool.accounts):
                    srv = servers[0]
                    payload = {"authKey": auth_key, "action": "ping"}
                    if srv is not None:
                        payload["serverUrl"] = srv.url
                    t0 = time.time()
                    res, _ = post_to_gas(acc.url, payload, timeout=15.0)
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
                        t0 = time.time()
                        res, _ = post_to_gas(
                            first_gas.url, {"authKey": auth_key, "action": "ping", "serverUrl": srv.url}, timeout=15.0
                        )
                        lat = int((time.time() - t0) * 1000)
                        if res.get("ok"):
                            self.server_pool.report_success(srv, lat)
                            self.log(f"Backend OK [{srv.name}]: {lat} ms (active: {res.get('activeSessions', 0)})")
                        else:
                            self.server_pool.report_failure(srv, res.get("error", "Error"))
                            self.log(f"Backend FAILED [{srv.name}]: {res.get('error')}")

            threading.Thread(target=worker, daemon=True).start()

        def test_proxy_e2e(self):
            if not (self.proxy and self.proxy.running):
                messagebox.showinfo("Proxy Stopped", "Please click 'Start Proxy' first to run an end-to-end proxy test.")
                return

            host = self.config["listen_host"]
            port = self.config["listen_port"]

            def worker():
                t0 = time.time()
                try:
                    s = socket.create_connection((host, port), timeout=15)
                    s.sendall(b"\x05\x01\x00")
                    if s.recv(2) != b"\x05\x00":
                        raise RuntimeError("SOCKS5 auth negotiation failed")
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

            threading.Thread(target=worker, daemon=True).start()

        def toggle_proxy(self):
            if self.proxy and self.proxy.running:
                self.proxy.stop()
                self.proxy = None
                self._save_config()
                self.status_var.set("● Stopped")
                self.status_lbl.configure(fg="#f87171")
                self.start_btn.configure(text="Start Proxy", bg=self.ACCENT, activebackground=self.ACCENT_HOVER)
                return

            mode = self.route_var.get()
            if mode == "gas" and not any(a.enabled for a in self.pool.accounts):
                messagebox.showwarning("No Active GAS Accounts", "Please add and enable at least one GAS account (or switch to Direct mode).")
                return
            if mode == "direct" and not any(s.enabled for s in self.server_pool.servers):
                messagebox.showwarning("No Active Servers", "Please add and enable at least one Backend Server (Render/SnappDeploy/Oracle) for Direct mode.")
                return

            self._save_config()
            self.pool.update_limits(
                self.config["max_concurrent_per_account"],
                self.config["daily_limit_per_account"],
                self.config["max_rpm_per_account"],
            )
            try:
                self.proxy = FreeDnetProxyServer(
                    pool=self.pool,
                    server_pool=self.server_pool,
                    auth_key=self.config["auth_key"],
                    host=self.config["listen_host"],
                    port=self.config["listen_port"],
                    log_fn=self.log,
                    route_mode=mode,
                )
                self.proxy.start()
                mode_lbl = "Direct" if mode == "direct" else "GAS"
                self.status_var.set(f"● Running [{mode_lbl}] ({self.config['listen_host']}:{self.config['listen_port']})")
                self.status_lbl.configure(fg="#4ade80")
                self.start_btn.configure(text="Stop Proxy", bg=self.RED, activebackground="#b91c1c")
            except Exception as e:
                messagebox.showerror("Start Failed", str(e))
                self.proxy = None

        def _sync_tables(self):
            total_reqs = sum(a.total_requests for a in self.pool.accounts)
            existing = list(self.tree.get_children())
            if len(existing) != len(self.pool.accounts):
                for row in existing:
                    self.tree.delete(row)
                for idx, acc in enumerate(self.pool.accounts):
                    self.tree.insert("", "end", iid=str(idx), values=self._gas_row(acc, total_reqs))
            else:
                for idx, acc in enumerate(self.pool.accounts):
                    self.tree.item(str(idx), values=self._gas_row(acc, total_reqs))

            existing_s = list(self.srv_tree.get_children())
            if len(existing_s) != len(self.server_pool.servers):
                for row in existing_s:
                    self.srv_tree.delete(row)
                for idx, srv in enumerate(self.server_pool.servers):
                    self.srv_tree.insert("", "end", iid=str(idx), values=self._srv_row(srv))
            else:
                for idx, srv in enumerate(self.server_pool.servers):
                    self.srv_tree.item(str(idx), values=self._srv_row(srv))

        def _gas_row(self, acc: GasAccount, total_reqs: int):
            share = f"{(acc.total_requests / total_reqs * 100):.1f}%" if total_reqs > 0 else "0.0%"
            lat = f"{acc.last_latency_ms} ms" if acc.last_latency_ms > 0 else "-"
            return (
                acc.name,
                acc.status_label(self.pool.daily_limit),
                f"{acc.active}/{self.pool.max_concurrent}",
                f"{acc.daily_requests}/{self.pool.daily_limit}",
                share,
                lat,
                acc.url,
            )

        def _srv_row(self, srv: BackendServer):
            lat = f"{srv.last_latency_ms} ms" if srv.last_latency_ms > 0 else "-"
            return (srv.name, srv.status_label, srv.total_requests, srv.errors, lat, srv.url)

        def _schedule_ui_tick(self):
            while not self.log_queue.empty():
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")

            self._sync_tables()
            if self.proxy and self.proxy.running:
                tx_kb = self.proxy.tx_bytes / 1024
                rx_kb = self.proxy.rx_bytes / 1024
                self.stats_var.set(
                    f"Tunnels: {self.proxy.active_tunnels}  |  TX: {tx_kb:.1f} KB  |  RX: {rx_kb:.1f} KB"
                )
                if time.time() - self._last_save_ts > 30:
                    self._last_save_ts = time.time()
                    try:
                        self._save_config()
                    except Exception:
                        pass
            self.root.after(700, self._schedule_ui_tick)


def main():
    if not HAS_TK:
        raise RuntimeError("Tkinter is required for the GUI. On Windows, install standard Python 3.8+.")
    enable_windows_dpi_awareness()
    root = tk.Tk()
    FreeDnetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
