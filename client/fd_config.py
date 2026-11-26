"""Configuration, path resolution, formatting, Windows helpers, and Xray URI parsing."""

import ctypes
import datetime
import os
import sys
import urllib.parse

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
    "Unlimited (Max Speed: No Client Limit)": (999, 1000000, 999999),
}
DEFAULT_PRESET = "Medium (Balanced: 18 conc | 14k/day | 120 rpm)"

DEFAULT_CONFIG = {
    "auth_key": "change-me-freednet-key",
    "listen_host": "127.0.0.1",
    "listen_port": 1080,
    "route_mode": "gas",       # "gas" or "direct"
    "proxy_mode": "tcp",       # "tcp" or "mitm"
    "gas_mode": "fair",        # "fair" (1/N) or "priority" (#1 -> #2)
    "server_mode": "priority", # "priority" (#1 -> #2), "fair" (1/N), or "only:<name>"
    "quota_preset": DEFAULT_PRESET,
    "keep_awake": True,
    "chain_enabled": False,
    "chain_uri": "",
    "theme": "Obsidian Slate (Default)",
    "language": "English",
    "accounts": [],
    "servers": [],
}


def today_str() -> str:
    return datetime.date.today().isoformat()


def parse_chain_uri(raw_uri: str, dialer_proxy: str = "freednet-socks") -> dict:
    """Parse a vless:// or trojan:// share link into an Xray outbound dict chained via dialerProxy."""
    uri = (raw_uri or "").strip()
    if not uri:
        raise ValueError("Outbound chain URI is empty.")
    parsed = urllib.parse.urlsplit(uri)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("vless", "trojan"):
        raise ValueError("Only vless:// and trojan:// links are supported.")
    cred = urllib.parse.unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port
    if not cred or not host or not port:
        raise ValueError("Invalid VLESS/Trojan link (missing user, host, or port).")

    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    q = lambda k, default="": (qs.get(k, [default])[0] or default).strip()

    net_type = q("type", "tcp").lower()
    security = q("security", "none").lower()
    sni = q("sni", "") or q("peer", "") or q("host", "") or host
    fp = q("fp", "")
    alpn_raw = q("alpn", "")
    alpn = [a.strip() for a in alpn_raw.split(",") if a.strip()] if alpn_raw else []

    stream: dict = {
        "network": net_type,
        "security": security,
        "sockopt": {"dialerProxy": dialer_proxy},
    }

    if security == "tls":
        tls_cfg: dict = {"serverName": sni}
        if q("allowInsecure", "") in ("1", "true", "True") or q("insecure", "") in ("1", "true", "True"):
            tls_cfg["allowInsecure"] = True
        if fp:
            tls_cfg["fingerprint"] = fp
        if alpn:
            tls_cfg["alpn"] = alpn
        stream["tlsSettings"] = tls_cfg
    elif security == "reality":
        stream["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fp or "chrome",
            "publicKey": q("pbk", ""),
            "shortId": q("sid", ""),
            "spiderX": urllib.parse.unquote(q("spx", "")) or "/",
        }

    if net_type == "ws":
        ws_path = urllib.parse.unquote(q("path", "/")) or "/"
        ws_host = q("host", "") or sni
        stream["wsSettings"] = {"path": ws_path, "headers": {"Host": ws_host} if ws_host else {}}
    elif net_type == "grpc":
        stream["grpcSettings"] = {"serviceName": q("serviceName", "") or q("path", "")}
    elif net_type == "httpupgrade":
        stream["httpupgradeSettings"] = {
            "path": urllib.parse.unquote(q("path", "/")) or "/",
            "host": q("host", "") or sni,
        }
    elif net_type in ("xhttp", "splithttp"):
        stream[f"{net_type}Settings"] = {
            "path": urllib.parse.unquote(q("path", "/")) or "/",
            "host": q("host", "") or sni,
        }

    if scheme == "vless":
        user_obj: dict = {"id": cred, "encryption": q("encryption", "none") or "none"}
        flow = q("flow", "")
        if flow:
            user_obj["flow"] = flow
        return {
            "tag": "chain-out",
            "protocol": "vless",
            "settings": {"vnext": [{"address": host, "port": int(port), "users": [user_obj]}]},
            "streamSettings": stream,
        }
    else:
        return {
            "tag": "chain-out",
            "protocol": "trojan",
            "settings": {"servers": [{"address": host, "port": int(port), "password": cred}]},
            "streamSettings": stream,
        }


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
