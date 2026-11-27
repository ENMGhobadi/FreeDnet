"""Local Root CA certificate manager and FreeDnet SOCKS5/HTTP/MITM proxy server."""

import base64
import datetime
import gzip
import ipaddress
import os
import select
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import uuid

from fd_config import BASE_DIR
from fd_net import (
    BackendServer,
    GasAccountPool,
    ServerPool,
    post_direct_to_server,
    post_to_gas,
)

CERTS_DIR = os.path.join(BASE_DIR, "certs")
CA_CERT_PATH = os.path.join(CERTS_DIR, "freednet_ca.crt")
CA_KEY_PATH = os.path.join(CERTS_DIR, "freednet_ca.key")


class CertManager:
    """Generates and caches a local Root CA and per-host leaf TLS certificates for HTTP MITM mode."""

    def __init__(self, certs_dir: str = CERTS_DIR):
        self.certs_dir = certs_dir
        self.hosts_dir = os.path.join(certs_dir, "hosts")
        self.ca_cert_path = os.path.join(certs_dir, "freednet_ca.crt")
        self.ca_key_path = os.path.join(certs_dir, "freednet_ca.key")
        self._lock = threading.Lock()
        self._ctx_cache: dict[str, ssl.SSLContext] = {}
        self._ca_cert = None
        self._ca_key = None

    def _ensure_cryptography(self):
        try:
            import cryptography  # noqa: F401
            return
        except ImportError:
            if getattr(sys, "frozen", False):
                raise RuntimeError("Missing 'cryptography' in compiled EXE. Please rebuild using build_exe.bat.")
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "cryptography"],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if proc.returncode != 0:
                clean_env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
                clean_env["NO_PROXY"] = "*"
                proc2 = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "cryptography"],
                    capture_output=True,
                    text=True,
                    env=clean_env,
                    timeout=90,
                )
                if proc2.returncode != 0:
                    raise RuntimeError(f"Could not auto-install 'cryptography': {(proc2.stderr or proc.stderr)[:200]}")

    def ensure_ca(self) -> str:
        self._ensure_cryptography()
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        with self._lock:
            if self._ca_cert is not None and self._ca_key is not None and os.path.exists(self.ca_cert_path):
                return self.ca_cert_path

            os.makedirs(self.hosts_dir, exist_ok=True)
            if os.path.exists(self.ca_cert_path) and os.path.exists(self.ca_key_path):
                with open(self.ca_cert_path, "rb") as f:
                    self._ca_cert = x509.load_pem_x509_certificate(f.read())
                with open(self.ca_key_path, "rb") as f:
                    self._ca_key = serialization.load_pem_private_key(f.read(), password=None)
                return self.ca_cert_path

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "FreeDnet Local Root CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FreeDnet"),
            ])
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=False,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=True,
                        crl_sign=True,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .sign(key, hashes.SHA256())
            )
            with open(self.ca_key_path, "wb") as f:
                f.write(
                    key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.TraditionalOpenSSL,
                        encryption_algorithm=serialization.NoEncryption(),
                    )
                )
            with open(self.ca_cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            self._ca_cert = cert
            self._ca_key = key
            return self.ca_cert_path

    def _prune_host_certs(self, max_pairs: int = 150):
        try:
            entries = []
            for fn in os.listdir(self.hosts_dir):
                if fn.endswith(".crt"):
                    p = os.path.join(self.hosts_dir, fn)
                    try:
                        entries.append((os.path.getmtime(p), p, p[:-4] + ".key"))
                    except OSError:
                        pass
            if len(entries) > max_pairs:
                entries.sort(key=lambda x: x[0])
                for _, crt_p, key_p in entries[: len(entries) - max_pairs]:
                    for target_p in (crt_p, key_p):
                        try:
                            os.remove(target_p)
                        except OSError:
                            pass
        except Exception:
            pass

    def get_ssl_context(self, host: str) -> ssl.SSLContext:
        clean_host = host.strip().lower().strip("[]")
        with self._lock:
            cached = self._ctx_cache.get(clean_host)
            if cached is not None:
                return cached

        self.ensure_ca()
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        safe_name = "".join(c if (c.isalnum() or c in ".-_") else "_" for c in clean_host)[:96]
        cert_file = os.path.join(self.hosts_dir, f"{safe_name}.crt")
        key_file = os.path.join(self.hosts_dir, f"{safe_name}.key")

        with self._lock:
            cached = self._ctx_cache.get(clean_host)
            if cached is not None:
                return cached

            if not (os.path.exists(cert_file) and os.path.exists(key_file)):
                leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, clean_host[:64])])
                try:
                    san_entry = x509.IPAddress(ipaddress.ip_address(clean_host))
                except ValueError:
                    san_entry = x509.DNSName(clean_host)
                now = datetime.datetime.now(datetime.timezone.utc)
                leaf_cert = (
                    x509.CertificateBuilder()
                    .subject_name(subject)
                    .issuer_name(self._ca_cert.subject)
                    .public_key(leaf_key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(days=1))
                    .not_valid_after(now + datetime.timedelta(days=365))
                    .add_extension(x509.SubjectAlternativeName([san_entry]), critical=False)
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .sign(self._ca_key, hashes.SHA256())
                )
                with open(key_file, "wb") as f:
                    f.write(
                        leaf_key.private_bytes(
                            encoding=serialization.Encoding.PEM,
                            format=serialization.PrivateFormat.TraditionalOpenSSL,
                            encryption_algorithm=serialization.NoEncryption(),
                        )
                    )
                with open(cert_file, "wb") as f:
                    f.write(leaf_cert.public_bytes(serialization.Encoding.PEM))
                self._prune_host_certs(max_pairs=150)

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
            try:
                ctx.set_alpn_protocols(["http/1.1"])
            except Exception:
                pass
            if len(self._ctx_cache) > 256:
                self._ctx_cache.clear()
            self._ctx_cache[clean_host] = ctx
            return ctx


_CERT_MGR = CertManager()


def install_windows_ca_cert(cert_path: str) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, f"CA cert saved at: {cert_path}"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["certutil", "-user", "-addstore", "Root", cert_path],
            capture_output=True,
            text=True,
            creationflags=flags,
            timeout=30,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            return True, "CA certificate installed to Windows Current User Trusted Root store."
        return False, out.strip()[:260] or f"certutil exited with code {proc.returncode}"
    except Exception as e:
        return False, str(e)


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
        proxy_mode: str = "tcp",
        keep_awake: bool = True,
    ):
        self.pool = pool
        self.server_pool = server_pool
        self.auth_key = auth_key
        self.host = host
        self.port = port
        self.log = log_fn
        self.route_mode = route_mode  # "gas" or "direct"
        self.proxy_mode = proxy_mode  # "tcp" or "mitm"
        self.keep_awake = keep_awake

        self.running = False
        self.server_sock = None
        self.active_tunnels = 0
        self.tx_bytes = 0
        self.rx_bytes = 0
        self._stat_lock = threading.Lock()
        self._tunnel_sem = threading.Semaphore(256)
        self._gas_open_sem = threading.Semaphore(64)
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
        pmode_lbl = "HTTP-MITM" if self.proxy_mode == "mitm" else "TCP-Tunnel"
        self.log(f"Proxy listening on {self.host}:{self.port} [{mode_lbl} | {pmode_lbl} | Backend: {self.server_pool.server_mode}]")

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
            if not self.server_pool.acquire_slot(backend, timeout=8.0):
                return {"ok": False, "error": "Backend busy (concurrency limit)", "upstreamError": True}
            try:
                t0 = time.time()
                res = post_direct_to_server(backend.url, payload, self.auth_key)
                lat = int((time.time() - t0) * 1000)
                if res.get("ok"):
                    self.server_pool.report_success(backend, lat)
                    return res
                if res.get("upstreamError"):
                    self.server_pool.report_failure(backend, res.get("error", "Upstream error"))
                return res
            finally:
                self.server_pool.release_slot(backend)

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

        slot_acquired = False
        if backend is not None:
            slot_acquired = self.server_pool.acquire_slot(backend, timeout=8.0)
            if not slot_acquired:
                if open_acquired:
                    self._gas_open_sem.release()
                return {"ok": False, "error": "Backend busy (concurrency limit)", "upstreamError": True}

        eff_retries = retries
        if retries > 0:
            with self.pool.lock:
                enabled_cnt = sum(1 for a in self.pool.accounts if a.enabled and a.url)
            if enabled_cnt > 1:
                eff_retries = min(4, max(retries, enabled_cnt - 1))
        try:
            for attempt in range(eff_retries + 1):
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
                is_target_err = bool(res.get("targetError"))
                is_session_err = bool(res.get("sessionError")) or ("Session not found" in last_err) or (res.get("statusCode") in (400, 404))
                if is_target_err or is_session_err:
                    self.pool.release(acc, ok=True, latency_ms=lat)
                    self.server_pool.report_success(backend, lat)
                    return res
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

            return {"ok": False, "error": last_err}
        finally:
            if slot_acquired:
                self.server_pool.release_slot(backend)
            if open_acquired:
                self._gas_open_sem.release()

    def _http_fetch_with_failover(self, url: str, method: str, headers: dict, req_body: bytes) -> dict:
        if req_body and len(req_body) > 2_800_000:
            return {"ok": False, "status": 413, "error": "Request body exceeds 2.8 MB MITM relay limit"}
        candidates = self.server_pool.get_candidates(require_explicit=(self.route_mode == "direct"))
        if not candidates:
            return {"ok": False, "error": "No enabled backend servers available"}

        body_b64 = base64.b64encode(req_body).decode("ascii") if req_body else ""
        req_payload = {
            "url": url,
            "method": method,
            "headers": headers,
            "acceptGzip": True,
        }
        if body_b64:
            req_payload["body"] = body_b64

        last_res = {"ok": False, "error": "No backend servers available"}
        for srv in candidates:
            res = self._send_request("http_fetch", req_payload, backend=srv, retries=1)
            if res.get("ok"):
                return res
            last_res = res
            if srv is not None and len(candidates) > 1 and res.get("upstreamError"):
                self.log(f"Backend [{srv.name}] failed ({res.get('error')}), failing over...")
                continue
            if not res.get("upstreamError"):
                break
        return last_res

    def _read_http_message(self, sock: socket.socket, buf: bytearray):
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(8192)
            if not chunk:
                return None
            buf.extend(chunk)
            if len(buf) > 131072:
                return None

        sep = buf.index(b"\r\n\r\n")
        head_bytes = bytes(buf[:sep])
        del buf[: sep + 4]

        lines = head_bytes.decode("latin-1").split("\r\n")
        if not lines:
            return None
        parts = lines[0].split(" ")
        if len(parts) < 3:
            return None
        method, target, version = parts[0].upper(), parts[1], parts[2]

        headers = {}
        content_length = 0
        is_chunked = False
        conn_hdr = ""
        for ln in lines[1:]:
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            k_s, v_s = k.strip(), v.strip()
            headers[k_s] = v_s
            kl = k_s.lower()
            if kl == "content-length":
                try:
                    content_length = max(0, int(v_s))
                except ValueError:
                    content_length = 0
            elif kl == "transfer-encoding" and "chunked" in v_s.lower():
                is_chunked = True
            elif kl == "connection":
                conn_hdr = v_s.lower()

        req_body = b""
        if is_chunked:
            chunks = []
            while True:
                while b"\r\n" not in buf:
                    more = sock.recv(8192)
                    if not more:
                        return None
                    buf.extend(more)
                idx = buf.index(b"\r\n")
                sz_line = bytes(buf[:idx]).decode("latin-1").split(";", 1)[0].strip()
                del buf[: idx + 2]
                sz = int(sz_line, 16)
                if sz == 0:
                    while b"\r\n" not in buf:
                        more = sock.recv(4096)
                        if not more:
                            break
                        buf.extend(more)
                    if buf.startswith(b"\r\n"):
                        del buf[:2]
                    break
                while len(buf) < sz + 2:
                    more = sock.recv(8192)
                    if not more:
                        return None
                    buf.extend(more)
                chunks.append(bytes(buf[:sz]))
                del buf[: sz + 2]
            req_body = b"".join(chunks)
        elif content_length > 0:
            while len(buf) < content_length:
                more = sock.recv(min(65536, content_length - len(buf)))
                if not more:
                    return None
                buf.extend(more)
            req_body = bytes(buf[:content_length])
            del buf[:content_length]

        return method, target, version, headers, req_body, conn_hdr

    def _mitm_http_loop(self, sock: socket.socket, scheme: str, default_host: str, default_port: int, initial_buf: bytes = b""):
        buf = bytearray(initial_buf)
        sock.settimeout(12.0)
        while self.running:
            parsed = self._read_http_message(sock, buf)
            if not parsed:
                return
            method, target, _version, headers, req_body, conn_hdr = parsed

            host_hdr = ""
            for k, v in headers.items():
                if k.lower() == "host":
                    host_hdr = v
                    break
            if not host_hdr:
                host_hdr = default_host if (scheme == "https" and default_port == 443) or (scheme == "http" and default_port == 80) else f"{default_host}:{default_port}"

            if target.startswith(("http://", "https://")):
                full_url = target
            else:
                path = target if target.startswith("/") else f"/{target}"
                full_url = f"{scheme}://{host_hdr}{path}"

            if req_body:
                with self._stat_lock:
                    self.tx_bytes += len(req_body)

            res = self._http_fetch_with_failover(full_url, method, headers, req_body)
            if not res.get("ok"):
                err_msg = str(res.get("error", "Bad Gateway"))
                self.log(f"MITM fetch failed {full_url[:80]} -> {err_msg}")
                err_body = f"FreeDnet MITM Error: {err_msg}".encode("utf-8", errors="replace")
                resp_head = (
                    f"HTTP/1.1 502 Bad Gateway\r\n"
                    f"Content-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(err_body)}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("ascii")
                sock.sendall(resp_head + err_body)
                return

            if res.get("bodyZ"):
                resp_body = gzip.decompress(base64.b64decode(res["bodyZ"]))
            elif res.get("body"):
                resp_body = base64.b64decode(res["body"])
            else:
                resp_body = b""

            if resp_body:
                with self._stat_lock:
                    self.rx_bytes += len(resp_body)

            status = int(res.get("status", 200))
            status_text = str(res.get("statusText") or "OK")
            resp_headers = res.get("headers") or {}
            keep_alive = conn_hdr != "close"

            hdr_lines = [f"HTTP/1.1 {status} {status_text}"]
            for hk, hv in resp_headers.items():
                if hk.lower() in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                    continue
                hdr_lines.append(f"{hk}: {hv}")
            hdr_lines.append(f"Content-Length: {len(resp_body)}")
            hdr_lines.append(f"Connection: {'keep-alive' if keep_alive else 'close'}")
            raw_resp = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("latin-1", errors="replace") + resp_body
            sock.sendall(raw_resp)
            if not keep_alive:
                return

    def _open_with_failover(
        self, host: str, port: int, init_b64: str, preferred_server: str = ""
    ) -> tuple[dict, BackendServer | None]:
        candidates = []
        if preferred_server:
            with self.server_pool.lock:
                for s in self.server_pool.servers:
                    if s.enabled and s.name == preferred_server:
                        candidates = [s]
                        break
        if not candidates:
            candidates = self.server_pool.get_candidates(require_explicit=(self.route_mode == "direct"))
        if not candidates:
            return {"ok": False, "error": "No enabled backend servers available"}, None

        last_res = {"ok": False, "error": "No backend servers available"}
        for srv in candidates:
            open_id = uuid.uuid4().hex
            res = self._send_request(
                "open",
                {"targetHost": host, "targetPort": port, "data": init_b64, "clientOpenId": open_id},
                backend=srv,
                retries=1,
            )
            if res.get("ok"):
                return res, srv
            last_res = res
            if srv is not None and len(candidates) > 1 and res.get("upstreamError"):
                detail = f" ({res.get('detail', '')[:80]})" if res.get('detail') else ""
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
            pinned_server_name = ""
            if is_socks5:
                target, pinned_server_name = self._handshake_socks5(client_sock)
                initial_data = b""
            else:
                target, initial_data, is_http_connect = self._handshake_http(client_sock)

            if not target:
                return

            host, port = target
            proto = "SOCKS5" if is_socks5 else "HTTP"
            self.log(f"{proto} -> {host}:{port}")

            if self.proxy_mode == "mitm" and not is_socks5:
                if is_http_connect:
                    client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    ssl_ctx = _CERT_MGR.get_ssl_context(host)
                    tls_sock = ssl_ctx.wrap_socket(client_sock, server_side=True)
                    try:
                        self._mitm_http_loop(tls_sock, "https", host, port, initial_buf=initial_data)
                    finally:
                        try:
                            tls_sock.close()
                        except Exception:
                            pass
                else:
                    self._mitm_http_loop(client_sock, "http", host, port, initial_buf=initial_data)
                return
            init_b64 = base64.b64encode(initial_data).decode("ascii") if initial_data else ""
            if initial_data:
                with self._stat_lock:
                    self.tx_bytes += len(initial_data)

            open_res, pinned_backend = self._open_with_failover(host, port, init_b64, preferred_server=pinned_server_name)
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

            init_rx_ack = 0
            if open_res.get("data"):
                raw_out = base64.b64decode(open_res["data"])
                if raw_out:
                    client_sock.sendall(raw_out)
                    with self._stat_lock:
                        self.rx_bytes += len(raw_out)
                    if isinstance(open_res.get("rxSeq"), int) and open_res["rxSeq"] > 0:
                        init_rx_ack = open_res["rxSeq"]

            if open_res.get("closed"):
                return

            self._relay_loop(client_sock, session_id, pinned_backend, initial_rx_ack=init_rx_ack)
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
            return None, ""
        nmethods = header[1]
        methods = self._recv_exact(sock, nmethods)
        pinned_user = ""
        if 0x02 in methods:
            sock.sendall(b"\x05\x02")
            auth_hdr = self._recv_exact(sock, 2)
            ulen = auth_hdr[1]
            uname = self._recv_exact(sock, ulen) if ulen > 0 else b""
            plen = self._recv_exact(sock, 1)[0]
            if plen > 0:
                self._recv_exact(sock, plen)
            pinned_user = uname.decode("utf-8", errors="replace").strip()
            sock.sendall(b"\x01\x00")
        else:
            sock.sendall(b"\x05\x00")

        req = self._recv_exact(sock, 4)
        if not req or req[1] != 0x01:
            if req and req[1] == 0x03:
                self.log("SOCKS5 UDP Associate ignored (TCP only)")
            sock.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            return None, ""

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
            return None, ""

        port = struct.unpack("!H", self._recv_exact(sock, 2))[0]
        return (addr, port), pinned_user

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

    def _relay_loop(self, client_sock: socket.socket, session_id: str, pinned_backend: BackendServer | None, initial_rx_ack: int = 0):
        closed_evt = threading.Event()
        last_activity = [time.time()]

        def tx_worker():
            tx_seq = 0
            try:
                client_sock.settimeout(15.0)
                while self.running and not closed_evt.is_set():
                    rlist, _, _ = select.select([client_sock], [], [], 0.02)
                    if not rlist:
                        continue
                    outgoing = b""
                    try:
                        chunk = client_sock.recv(65536)
                        if not chunk:
                            # Local write half-close (FIN): propagate FIN to backend without aborting RX drain
                            try:
                                self._send_request("sync", {"sessionId": session_id, "waitMs": 0, "txOnly": True, "txFin": True}, backend=pinned_backend, retries=0)
                            except Exception:
                                pass
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
                        return

                    if not outgoing:
                        continue

                    last_activity[0] = time.time()
                    with self._stat_lock:
                        self.tx_bytes += len(outgoing)
                    tx_seq += 1
                    body = {"sessionId": session_id, "waitMs": 0, "txOnly": True, "txSeq": tx_seq, **self._encode_payload(outgoing)}
                    res = self._send_request("sync", body, backend=pinned_backend, retries=1)
                    if not res.get("ok"):
                        closed_evt.set()
                        return
                    if res.get("closed"):
                        # Backend already drained & closed; stop TX and let rx_loop exit cleanly
                        return
            except Exception:
                closed_evt.set()

        tx_thread = threading.Thread(target=tx_worker, daemon=True)
        tx_thread.start()

        rx_ack = initial_rx_ack
        prev_had_rx = False
        idle_cycles = 0
        try:
            while self.running and not closed_evt.is_set():
                if prev_had_rx:
                    wait_ms = 0
                else:
                    if time.time() - last_activity[0] > 45:
                        return
                    idle_cycles += 1
                    if self.active_tunnels > 12:
                        wait_ms = 600
                    else:
                        wait_ms = 250 if idle_cycles == 1 else (900 if idle_cycles == 2 else 2500)

                res = self._send_request(
                    "sync",
                    {"sessionId": session_id, "waitMs": wait_ms, "acceptGzip": True, "ackSeq": rx_ack},
                    backend=pinned_backend,
                    retries=1,
                )
                if not res.get("ok"):
                    return

                raw_out = self._decode_payload(res)
                rx_seq = res.get("rxSeq")
                is_dup = isinstance(rx_seq, int) and rx_seq > 0 and rx_seq <= rx_ack
                prev_had_rx = len(raw_out) > 0
                if raw_out and not is_dup:
                    idle_cycles = 0
                    last_activity[0] = time.time()
                    try:
                        client_sock.sendall(raw_out)
                        with self._stat_lock:
                            self.rx_bytes += len(raw_out)
                        if isinstance(rx_seq, int) and rx_seq > 0:
                            rx_ack = rx_seq
                    except OSError:
                        return

                if res.get("closed"):
                    return
        finally:
            closed_evt.set()
            tx_thread.join(timeout=0.5)
