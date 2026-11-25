#!/usr/bin/env python3
"""
FreeDnet Local Client (Windows 11 GUI + SOCKS5/HTTP Proxy + Smart Multi-Account GAS Balancer)
Uses only Python 3.8+ standard library.
"""

import base64
import ctypes
import json
import os
import queue
import socket
import ssl
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import urllib.error
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEFAULT_CONFIG = {
    "auth_key": "change-me-freednet-key",
    "listen_host": "127.0.0.1",
    "listen_port": 1080,
    "max_concurrent_per_account": 20,
    "accounts": [],
}


def enable_windows_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class GasAccount:
    def __init__(self, name: str, url: str, enabled: bool = True):
        self.name = name
        self.url = url.strip()
        self.enabled = enabled
        self.active = 0
        self.total_requests = 0
        self.errors = 0
        self.consecutive_rate_limits = 0
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
            return f"Cooldown ({rem}s)"
        if self.active > 0:
            return "Active"
        if self.last_error:
            return "Warning"
        return "Ready"


class GasAccountPool:
    """
    Fair & quota-aware scheduler across multiple Google Apps Script Web App accounts.
    - Enforces max_concurrent per account (safely below GAS's 30-concurrent limit).
    - Distributes requests evenly using (active_concurrent, total_requests) ordering.
    - Automatically applies exponential cooldown when an account hits GAS rate/quota limits.
    """

    def __init__(self, max_concurrent: int = 20):
        self.lock = threading.Condition()
        self.accounts: list[GasAccount] = []
        self.max_concurrent = max(1, min(int(max_concurrent), 28))

    def set_max_concurrent(self, val: int):
        with self.lock:
            self.max_concurrent = max(1, min(int(val), 28))
            self.lock.notify_all()

    def load_accounts(self, items: list[dict]):
        with self.lock:
            self.accounts = [
                GasAccount(
                    name=item.get("name", f"Account {i + 1}"),
                    url=item.get("url", ""),
                    enabled=bool(item.get("enabled", True)),
                )
                for i, item in enumerate(items)
                if item.get("url", "").strip()
            ]
            self.lock.notify_all()

    def acquire(self, timeout: float = 12.0, exclude: set = None) -> GasAccount:
        deadline = time.time() + timeout
        exclude = exclude or set()
        with self.lock:
            while True:
                now = time.time()
                candidates = [
                    acc
                    for acc in self.accounts
                    if acc.enabled
                    and acc.url
                    and acc not in exclude
                    and now >= acc.cooldown_until
                    and acc.active < self.max_concurrent
                ]
                if candidates:
                    # Fair-share selection: lowest active slots first, then fewest total requests
                    chosen = min(candidates, key=lambda a: (a.active, a.total_requests))
                    chosen.active += 1
                    chosen.total_requests += 1
                    return chosen

                # Check if any enabled account exists at all
                any_enabled = any(acc.enabled and acc.url and acc not in exclude for acc in self.accounts)
                if not any_enabled:
                    raise RuntimeError("No enabled GAS accounts available")

                remaining = deadline - now
                if remaining <= 0:
                    raise RuntimeError("All GAS accounts are busy or in cooldown")
                self.lock.wait(timeout=min(remaining, 0.5))

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
    """
    Sends a JSON POST to a Google Apps Script Web App URL.
    Returns (parsed_json, is_rate_limited).
    """
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

    # Detect Google HTML quota/auth error pages
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


class FreeDnetProxyServer:
    def __init__(self, pool: GasAccountPool, auth_key: str, host: str, port: int, log_fn, stats_fn):
        self.pool = pool
        self.auth_key = auth_key
        self.host = host
        self.port = port
        self.log = log_fn
        self.stats_fn = stats_fn

        self.running = False
        self.server_sock: socket.socket | None = None
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
        self.log(f"Proxy listening on {self.host}:{self.port} (SOCKS5 + HTTP)")

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
        self.log("Proxy stopped.")

    def _gas_request(self, action: str, body: dict, retries: int = 1) -> dict:
        payload = {"authKey": self.auth_key, "action": action, **body}
        excluded = set()
        last_err = "Unknown error"

        for _ in range(retries + 1):
            acc = self.pool.acquire(exclude=excluded)
            t0 = time.time()
            res, rate_limited = post_to_gas(acc.url, payload)
            lat = int((time.time() - t0) * 1000)

            if res.get("ok"):
                self.pool.release(acc, ok=True, latency_ms=lat)
                return res

            last_err = res.get("error", "GAS error")
            self.pool.release(acc, ok=False, latency_ms=lat, rate_limited=rate_limited, err_msg=last_err)
            excluded.add(acc)
            if not rate_limited and "Session not found" in last_err:
                break

        return {"ok": False, "error": last_err}

    def _accept_loop(self):
        while self.running and self.server_sock:
            try:
                client_sock, addr = self.server_sock.accept()
                client_sock.settimeout(15.0)
                threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, client_sock: socket.socket):
        session_id = None
        with self._stat_lock:
            self.active_tunnels += 1
        try:
            first = client_sock.recv(1, socket.MSG_PEEK)
            if not first:
                return

            if first[0] == 0x05:
                target = self._handshake_socks5(client_sock)
                initial_data = b""
            else:
                target, initial_data = self._handshake_http(client_sock)

            if not target:
                return

            host, port = target
            init_b64 = base64.b64encode(initial_data).decode("ascii") if initial_data else ""
            if initial_data:
                with self._stat_lock:
                    self.tx_bytes += len(initial_data)

            open_res = self._gas_request(
                "open",
                {"targetHost": host, "targetPort": port, "data": init_b64},
                retries=1,
            )
            if not open_res.get("ok"):
                self.log(f"Open failed {host}:{port} -> {open_res.get('error')}")
                return

            session_id = open_res["sessionId"]
            if first[0] == 0x05:
                # SOCKS5 success reply
                client_sock.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

            if open_res.get("data"):
                raw_out = base64.b64decode(open_res["data"])
                if raw_out:
                    client_sock.sendall(raw_out)
                    with self._stat_lock:
                        self.rx_bytes += len(raw_out)

            if open_res.get("closed"):
                return

            self._relay_loop(client_sock, session_id)
        except Exception:
            pass
        finally:
            if session_id:
                try:
                    self._gas_request("close", {"sessionId": session_id}, retries=0)
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
        sock.sendall(b"\x05\x00")  # No authentication

        req = self._recv_exact(sock, 4)
        if not req or req[1] != 0x01:  # Only CONNECT supported
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
                return None, b""
            buf += chunk
            if len(buf) > 65536:
                return None, b""

        head, rest = buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if not lines:
            return None, b""

        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None, b""

        method, target = parts[0].upper(), parts[1]
        if method == "CONNECT":
            if ":" in target:
                host, port_s = target.rsplit(":", 1)
                port = int(port_s)
            else:
                host, port = target, 443
            sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            return (host, port), rest
        else:
            # Plain HTTP proxy request
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
                return None, b""
            return (host, port), buf

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise EOFError("Socket closed")
            data += chunk
        return data

    def _relay_loop(self, client_sock: socket.socket, session_id: str):
        client_sock.settimeout(0.05)
        while self.running:
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
                with self._stat_lock:
                    self.tx_bytes += len(outgoing)
                b64_in = base64.b64encode(outgoing).decode("ascii")
                wait_ms = 0
            else:
                b64_in = ""
                wait_ms = 2500

            res = self._gas_request(
                "sync",
                {"sessionId": session_id, "data": b64_in, "waitMs": wait_ms},
                retries=1,
            )
            if not res.get("ok"):
                return

            if res.get("data"):
                raw_out = base64.b64decode(res["data"])
                if raw_out:
                    try:
                        client_sock.sendall(raw_out)
                        with self._stat_lock:
                            self.rx_bytes += len(raw_out)
                    except OSError:
                        return

            if res.get("closed"):
                return


class AccountDialog(simpledialog.Dialog):
    def __init__(self, parent, title: str, name: str = "", url: str = ""):
        self.init_name = name
        self.init_url = url
        self.result_data = None
        super().__init__(parent, title=title)

    def body(self, master):
        ttk.Label(master, text="Account Name:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.name_var = tk.StringVar(value=self.init_name)
        self.name_entry = ttk.Entry(master, textvariable=self.name_var, width=48)
        self.name_entry.grid(row=0, column=1, padx=6, pady=6)

        ttk.Label(master, text="GAS Web App URL:").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.url_var = tk.StringVar(value=self.init_url)
        self.url_entry = ttk.Entry(master, textvariable=self.url_var, width=48)
        self.url_entry.grid(row=1, column=1, padx=6, pady=6)
        return self.name_entry

    def validate(self):
        name = self.name_var.get().strip()
        url = self.url_var.get().strip()
        if not name or not url.startswith("http"):
            messagebox.showwarning("Invalid Input", "Please enter a valid name and https://... GAS URL.", parent=self)
            return False
        return True

    def apply(self):
        self.result_data = (self.name_var.get().strip(), self.url_var.get().strip())


class FreeDnetApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FreeDnet Client — Multi-Account GAS Proxy")
        self.root.geometry("860x620")
        self.root.minsize(760, 540)

        self.config = self._load_config()
        self.pool = GasAccountPool(self.config.get("max_concurrent_per_account", 20))
        self.pool.load_accounts(self.config.get("accounts", []))
        self.proxy: FreeDnetProxyServer | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self._refresh_table()
        self._schedule_ui_tick()

    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged = {**DEFAULT_CONFIG, **data}
                return merged
            except Exception:
                pass
        return dict(DEFAULT_CONFIG)

    def _save_config(self):
        self.config["auth_key"] = self.auth_var.get().strip()
        self.config["listen_host"] = self.host_var.get().strip() or "127.0.0.1"
        try:
            self.config["listen_port"] = int(self.port_var.get().strip())
        except ValueError:
            self.config["listen_port"] = 1080
        try:
            self.config["max_concurrent_per_account"] = int(self.max_conc_var.get().strip())
        except ValueError:
            self.config["max_concurrent_per_account"] = 20
        self.config["accounts"] = [acc.to_dict() for acc in self.pool.accounts]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        pad = {"padx": 10, "pady": 6}

        # Header / Control Bar
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        self.status_var = tk.StringVar(value="● Stopped")
        self.status_lbl = ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold"), foreground="#b91c1c")
        self.status_lbl.pack(side="left")

        self.start_btn = ttk.Button(top, text="Start Proxy", command=self.toggle_proxy, width=16)
        self.start_btn.pack(side="right", padx=4)

        self.test_btn = ttk.Button(top, text="Test All Accounts", command=self.test_all_accounts, width=18)
        self.test_btn.pack(side="right", padx=4)

        # Settings Frame
        cfg_frame = ttk.LabelFrame(self.root, text="Connection & Quota Settings")
        cfg_frame.pack(fill="x", padx=10, pady=4)

        ttk.Label(cfg_frame, text="Auth Key:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.auth_var = tk.StringVar(value=self.config["auth_key"])
        self.auth_entry = ttk.Entry(cfg_frame, textvariable=self.auth_var, show="*", width=28)
        self.auth_entry.grid(row=0, column=1, sticky="w", padx=4, pady=6)

        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, text="Show", variable=self.show_key_var, command=self._toggle_key).grid(row=0, column=2, padx=4)

        ttk.Label(cfg_frame, text="Listen IP:").grid(row=0, column=3, sticky="w", padx=8)
        self.host_var = tk.StringVar(value=self.config["listen_host"])
        ttk.Entry(cfg_frame, textvariable=self.host_var, width=14).grid(row=0, column=4, padx=4)

        ttk.Label(cfg_frame, text="Port:").grid(row=0, column=5, sticky="w", padx=8)
        self.port_var = tk.StringVar(value=str(self.config["listen_port"]))
        ttk.Entry(cfg_frame, textvariable=self.port_var, width=7).grid(row=0, column=6, padx=4)

        ttk.Label(cfg_frame, text="Max Concurrent / Account:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.max_conc_var = tk.StringVar(value=str(self.config["max_concurrent_per_account"]))
        ttk.Entry(cfg_frame, textvariable=self.max_conc_var, width=8).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(cfg_frame, text="(Google Apps Script limit is 30; 15–22 recommended)", foreground="#555").grid(
            row=1, column=2, columnspan=5, sticky="w", padx=4
        )

        # Accounts Table Frame
        acc_frame = ttk.LabelFrame(self.root, text="Google Apps Script Accounts (Fair-Share Load Balanced)")
        acc_frame.pack(fill="both", expand=True, padx=10, pady=4)

        cols = ("name", "status", "active", "requests", "share", "latency", "url")
        self.tree = ttk.Treeview(acc_frame, columns=cols, show="headings", height=8)
        headers = [
            ("name", "Account", 110),
            ("status", "Status", 105),
            ("active", "Active", 65),
            ("requests", "Requests", 75),
            ("share", "Share %", 70),
            ("latency", "Latency", 75),
            ("url", "GAS Web App URL", 300),
        ]
        for col, text, w in headers:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w" if col in ("name", "url") else "center")

        self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(acc_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.tree.configure(yscrollcommand=sb.set)

        btn_bar = ttk.Frame(self.root)
        btn_bar.pack(fill="x", padx=10, pady=2)
        ttk.Button(btn_bar, text="+ Add Account", command=self.add_account).pack(side="left", padx=3)
        ttk.Button(btn_bar, text="Edit", command=self.edit_account).pack(side="left", padx=3)
        ttk.Button(btn_bar, text="Remove", command=self.remove_account).pack(side="left", padx=3)
        ttk.Button(btn_bar, text="Enable / Disable", command=self.toggle_account).pack(side="left", padx=3)

        self.stats_var = tk.StringVar(value="Tunnels: 0  |  TX: 0 KB  |  RX: 0 KB")
        ttk.Label(btn_bar, textvariable=self.stats_var, foreground="#333").pack(side="right", padx=6)

        # Log Frame
        log_frame = ttk.LabelFrame(self.root, text="Activity Log")
        log_frame.pack(fill="both", expand=False, padx=10, pady=(4, 10))
        self.log_text = tk.Text(log_frame, height=7, state="disabled", font=("Consolas", 9), bg="#f8fafc")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

    def _toggle_key(self):
        self.auth_entry.configure(show="" if self.show_key_var.get() else "*")

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}\n")

    def add_account(self):
        dlg = AccountDialog(self.root, "Add Google Apps Script Account", name=f"Account {len(self.pool.accounts) + 1}")
        if dlg.result_data:
            name, url = dlg.result_data
            with self.pool.lock:
                self.pool.accounts.append(GasAccount(name, url, True))
            self._save_config()
            self._refresh_table()
            self.log(f"Added account: {name}")

    def edit_account(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        acc = self.pool.accounts[idx]
        dlg = AccountDialog(self.root, "Edit Account", name=acc.name, url=acc.url)
        if dlg.result_data:
            acc.name, acc.url = dlg.result_data
            self._save_config()
            self._refresh_table()

    def remove_account(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        with self.pool.lock:
            removed = self.pool.accounts.pop(idx)
        self._save_config()
        self._refresh_table()
        self.log(f"Removed account: {removed.name}")

    def toggle_account(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        acc = self.pool.accounts[idx]
        acc.enabled = not acc.enabled
        acc.cooldown_until = 0
        self._save_config()
        self._refresh_table()

    def test_all_accounts(self):
        self._save_config()
        auth_key = self.auth_var.get().strip()
        if not self.pool.accounts:
            messagebox.showinfo("No Accounts", "Please add at least one GAS Web App URL first.")
            return

        def worker():
            for acc in list(self.pool.accounts):
                t0 = time.time()
                res, rl = post_to_gas(acc.url, {"authKey": auth_key, "action": "ping"}, timeout=15.0)
                lat = int((time.time() - t0) * 1000)
                if res.get("ok"):
                    acc.last_latency_ms = lat
                    acc.last_error = ""
                    self.log(f"Ping OK [{acc.name}]: {lat} ms (server active sessions: {res.get('activeSessions', 0)})")
                else:
                    acc.last_error = res.get("error", "Error")
                    self.log(f"Ping FAILED [{acc.name}]: {acc.last_error}")

        threading.Thread(target=worker, daemon=True).start()

    def toggle_proxy(self):
        if self.proxy and self.proxy.running:
            self.proxy.stop()
            self.proxy = None
            self.status_var.set("● Stopped")
            self.status_lbl.configure(foreground="#b91c1c")
            self.start_btn.configure(text="Start Proxy")
            return

        if not any(a.enabled for a in self.pool.accounts):
            messagebox.showwarning("No Active Accounts", "Please add and enable at least one GAS account.")
            return

        self._save_config()
        self.pool.set_max_concurrent(self.config["max_concurrent_per_account"])
        try:
            self.proxy = FreeDnetProxyServer(
                pool=self.pool,
                auth_key=self.config["auth_key"],
                host=self.config["listen_host"],
                port=self.config["listen_port"],
                log_fn=self.log,
                stats_fn=None,
            )
            self.proxy.start()
            self.status_var.set(f"● Running on {self.config['listen_host']}:{self.config['listen_port']}")
            self.status_lbl.configure(foreground="#15803d")
            self.start_btn.configure(text="Stop Proxy")
        except Exception as e:
            messagebox.showerror("Start Failed", str(e))
            self.proxy = None

    def _refresh_table(self):
        selected = self.tree.selection()
        sel_id = selected[0] if selected else None
        for row in self.tree.get_children():
            self.tree.delete(row)

        total_reqs = sum(a.total_requests for a in self.pool.accounts)
        for idx, acc in enumerate(self.pool.accounts):
            share = f"{(acc.total_requests / total_reqs * 100):.1f}%" if total_reqs > 0 else "0.0%"
            lat = f"{acc.last_latency_ms} ms" if acc.last_latency_ms > 0 else "-"
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    acc.name,
                    acc.status_label,
                    f"{acc.active}/{self.pool.max_concurrent}",
                    acc.total_requests,
                    share,
                    lat,
                    acc.url,
                ),
            )
        if sel_id and sel_id in self.tree.get_children():
            self.tree.selection_set(sel_id)

    def _schedule_ui_tick(self):
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self._refresh_table()
        if self.proxy and self.proxy.running:
            tx_kb = self.proxy.tx_bytes / 1024
            rx_kb = self.proxy.rx_bytes / 1024
            self.stats_var.set(
                f"Tunnels: {self.proxy.active_tunnels}  |  TX: {tx_kb:.1f} KB  |  RX: {rx_kb:.1f} KB"
            )
        self.root.after(700, self._schedule_ui_tick)


def main():
    enable_windows_dpi_awareness()
    root = tk.Tk()
    FreeDnetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
