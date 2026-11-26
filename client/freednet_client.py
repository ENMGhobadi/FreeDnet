#!/usr/bin/env python3
"""
FreeDnet Client (Windows 11 Next-Gen PySide6 / Qt 6 Dark Fluent UI + SOCKS5/HTTP Proxy)
- Modular architecture: fd_config, fd_net, fd_proxy, fd_themes, fd_i18n, fd_dialogs
"""

import io
import json
import os
import queue
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
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
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fd_config import (
    BASE_DIR,
    CONFIG_PATH,
    COUNTRY_CHECK_ENDPOINTS,
    DEFAULT_CONFIG,
    DEFAULT_PRESET,
    GAS_QUOTA_PRESETS,
    WINTUN_DLL,
    WINTUN_ZIP_URL,
    XRAY_DIR,
    XRAY_EXE,
    XRAY_TUN_CFG,
    XRAY_ZIP_URL,
    enable_windows_dpi_awareness,
    fmt_bytes,
    fmt_rate,
    is_windows_admin,
    parse_chain_uri,
    _resource_path,
    set_windows_system_proxy,
)
from fd_dialogs import (
    CloudflareDeployDialog,
    EntryDialog,
    FlowLayout,
    RenderDeployDialog,
    SnapDeployDialog,
)
from fd_i18n import LANGUAGES
from fd_net import (
    BackendServer,
    GasAccount,
    GasAccountPool,
    ServerPool,
    _DIRECT_OPENER,
    post_direct_to_server,
    post_to_gas,
)
from fd_proxy import (
    FreeDnetProxyServer,
    _CERT_MGR,
    install_windows_ca_cert,
)
from fd_themes import THEME_ALIASES, THEMES, build_qss

class FreeDnetProWindow(QMainWindow):
    SRV_PRIORITY_LBL = "Priority (#1 -> #2 Failover)"
    SRV_FAIR_LBL = "Fair-Share (1/N Equal)"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FreeDnet Pro — Smart Proxy Router")
        self.resize(1040, 680)
        self.setMinimumSize(880, 580)

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
        self._pending_srv_queue: queue.Queue = queue.Queue()
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
        self._last_speed_ui_ts = time.time()
        self._speed_tx_ema = 0.0
        self._speed_rx_ema = 0.0

        icon_path = _resource_path("freednet.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self._build_ui()
        self._setup_tray(icon_path)
        if self._config_load_err:
            self.log(self._config_load_err)
        self._current_theme_is_light = False
        self._apply_theme(self.config.get("theme", "Obsidian Slate (Default)"), save=False)
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
        self.config["proxy_mode"] = "mitm" if self.rb_mitm.isChecked() else "tcp"
        self.config["gas_mode"] = "fair" if self.chk_fair_share.isChecked() else "priority"
        self.config["server_mode"] = self.server_pool.server_mode
        self.config["quota_preset"] = self.preset_combo.currentText()
        self.config["keep_awake"] = self.chk_keep_awake.isChecked()
        if hasattr(self, "chk_chain_enabled"):
            self.config["chain_enabled"] = self.chk_chain_enabled.isChecked()
        if hasattr(self, "chain_uri_edit"):
            self.config["chain_uri"] = self.chain_uri_edit.text().strip()
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
        h_lay = FlowLayout(header, margin=14, h_spacing=10, v_spacing=8)
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

        r1_hdr = QHBoxLayout()
        self.lbl_chain_mode = QLabel("Chain Mode:", objectName="FieldLabel")
        r1_hdr.addWidget(self.lbl_chain_mode)
        r1_hdr.addStretch()
        self.ip_badge = QLabel("Exit IP: Not checked yet", objectName="Muted")
        r1_hdr.addWidget(self.ip_badge)
        r_lay.addLayout(r1_hdr)

        r1_tiles = QHBoxLayout()
        r1_tiles.setSpacing(10)
        self.rb_gas = QPushButton("🛡️  GAS Relay\nAnti-Filtering · Multi-Account", objectName="ModeTile")
        self.rb_gas.setCheckable(True)
        self.rb_direct = QPushButton("⚡  Direct Backend\nMax Speed · Low Latency", objectName="ModeTile")
        self.rb_direct.setCheckable(True)
        self.route_grp = QButtonGroup(self)
        self.route_grp.setExclusive(True)
        self.route_grp.addButton(self.rb_gas)
        self.route_grp.addButton(self.rb_direct)
        if self.config.get("route_mode", "gas") == "direct":
            self.rb_direct.setChecked(True)
        else:
            self.rb_gas.setChecked(True)
        self.rb_gas.toggled.connect(self._on_route_change)
        r1_tiles.addWidget(self.rb_gas, stretch=1)
        r1_tiles.addWidget(self.rb_direct, stretch=1)
        r_lay.addLayout(r1_tiles)

        r2_hdr = QHBoxLayout()
        self.lbl_proxy_mode = QLabel("Proxy Mode:", objectName="FieldLabel")
        r2_hdr.addWidget(self.lbl_proxy_mode)
        r2_hdr.addStretch()
        self.install_ca_btn = QPushButton("🔒 Install CA Cert", objectName="SmallBtn")
        self.install_ca_btn.clicked.connect(self.install_ca_certificate)
        r2_hdr.addWidget(self.install_ca_btn)
        r_lay.addLayout(r2_hdr)

        r2_tiles = QHBoxLayout()
        r2_tiles.setSpacing(10)
        self.rb_tcp = QPushButton("🔌  TCP Tunnel\nAll Apps · SOCKS5 & HTTP", objectName="ModeTile")
        self.rb_tcp.setCheckable(True)
        self.rb_mitm = QPushButton("🚀  HTTP MITM Relay\nFast Web · 1-Hop Browser", objectName="ModeTile")
        self.rb_mitm.setCheckable(True)
        self.proxy_mode_grp = QButtonGroup(self)
        self.proxy_mode_grp.setExclusive(True)
        self.proxy_mode_grp.addButton(self.rb_tcp)
        self.proxy_mode_grp.addButton(self.rb_mitm)
        if self.config.get("proxy_mode", "tcp") == "mitm":
            self.rb_mitm.setChecked(True)
        else:
            self.rb_tcp.setChecked(True)
        self.rb_tcp.toggled.connect(self._on_proxy_mode_change)
        r2_tiles.addWidget(self.rb_tcp, stretch=1)
        r2_tiles.addWidget(self.rb_mitm, stretch=1)
        r_lay.addLayout(r2_tiles)
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
        gt_lay = FlowLayout(gas_toolbar, margin=10, h_spacing=8, v_spacing=8)
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
        st_lay = FlowLayout(srv_toolbar, margin=10, h_spacing=8, v_spacing=8)
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

        c_lay1 = FlowLayout(margin=0, h_spacing=10, v_spacing=8)
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

        c_lay2 = FlowLayout(margin=0, h_spacing=10, v_spacing=8)
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

        c_lay3 = FlowLayout(margin=0, h_spacing=10, v_spacing=8)
        self.chk_chain_enabled = QCheckBox(self._t("chain_enable"))
        self.chk_chain_enabled.setChecked(bool(self.config.get("chain_enabled", False)))
        self.chk_chain_enabled.toggled.connect(lambda _: self._save_config())
        c_lay3.addWidget(self.chk_chain_enabled)

        self.chain_uri_edit = QLineEdit(self.config.get("chain_uri", ""))
        self.chain_uri_edit.setPlaceholderText(self._t("chain_uri_ph"))
        self.chain_uri_edit.setMinimumWidth(360)
        self.chain_uri_edit.editingFinished.connect(self._save_config)
        c_lay3.addWidget(self.chain_uri_edit)
        cfg_vlay.addLayout(c_lay3)
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
        theme_name = THEME_ALIASES.get(theme_name, theme_name)
        if theme_name not in THEMES:
            theme_name = "Obsidian Slate (Default)"
        if hasattr(self, "theme_combo") and self.theme_combo.currentText() != theme_name:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentText(theme_name)
            self.theme_combo.blockSignals(False)
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
        self.lbl_proxy_mode.setText(self._t("proxy_mode"))
        self.rb_tcp.setText(self._t("rb_tcp"))
        self.rb_mitm.setText(self._t("rb_mitm"))
        self.install_ca_btn.setText(self._t("install_ca"))
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
        if hasattr(self, "chk_chain_enabled"):
            self.chk_chain_enabled.setText(self._t("chain_enable"))
        if hasattr(self, "chain_uri_edit"):
            self.chain_uri_edit.setPlaceholderText(self._t("chain_uri_ph"))
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
            self.status_pill.setText(f"{self._t('running')} · {lbl}")
            self.log(f"Switched live chain mode to: {lbl}")

    def _on_proxy_mode_change(self):
        pmode = "mitm" if self.rb_mitm.isChecked() else "tcp"
        if pmode == "mitm":
            try:
                _CERT_MGR.ensure_ca()
            except Exception as e:
                self.log(f"MITM CA setup error: {e}")
                QMessageBox.warning(self, "CA Error", str(e))
                self.rb_tcp.blockSignals(True)
                self.rb_tcp.setChecked(True)
                self.rb_tcp.blockSignals(False)
                pmode = "tcp"
        self._save_config()
        if self.proxy and self.proxy.running:
            self.proxy.proxy_mode = pmode
            self.log(f"Switched live proxy mode to: {'HTTP-MITM' if pmode == 'mitm' else 'TCP-Tunnel'}")

    def install_ca_certificate(self):
        try:
            cert_path = _CERT_MGR.ensure_ca()
            ok, msg = install_windows_ca_cert(cert_path)
            self.log(f"CA Cert: {msg}")
            if ok:
                QMessageBox.information(self, "CA Certificate Installed", f"{msg}\n\nRestart your browser if it was already open.")
            else:
                QMessageBox.information(self, "CA Certificate", f"{msg}\n\nPath: {cert_path}")
        except Exception as e:
            self.log(f"CA Cert error: {e}")
            QMessageBox.warning(self, "CA Error", str(e))

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
    def _wait_until_server_live(
        self,
        tid: int,
        label: str,
        svc_url: str,
        auth_key: str,
        max_attempts: int = 30,
        interval_s: float = 12.0,
    ) -> int | None:
        for attempt in range(1, max_attempts + 1):
            with self._busy_lock:
                if tid in self._busy_tasks:
                    self._busy_tasks[tid] = f"Waiting for {label} LIVE ({attempt}/{max_attempts})..."
            t0 = time.time()
            res = post_direct_to_server(svc_url, {"authKey": auth_key, "action": "ping"}, auth_key, timeout=12.0)
            lat = int((time.time() - t0) * 1000)
            if res.get("ok"):
                return max(1, lat)

            first_gas = next((a for a in self.pool.accounts if a.enabled and a.url), None)
            if first_gas is not None:
                t1 = time.time()
                res_g, _ = post_to_gas(
                    first_gas.url,
                    {"authKey": auth_key, "action": "ping", "serverUrl": svc_url},
                    timeout=20.0,
                )
                lat_g = int((time.time() - t1) * 1000)
                if res_g.get("ok"):
                    return max(1, lat_g)

            if attempt < max_attempts:
                time.sleep(interval_s)
        return None

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
                self.log(f"Render Deploy STARTED -> {svc_url} | Waiting for build to go LIVE (~2-3 min)...")
                lat = self._wait_until_server_live(tid, label, svc_url, params["auth_key"], max_attempts=30, interval_s=12.0)
                if lat is not None:
                    self._pending_srv_queue.put((label, svc_url, lat))
                    self.log(f"✅ {label} is LIVE & READY ({lat} ms) -> {svc_url} | Added to Backend Servers.")
                else:
                    self._pending_srv_queue.put((label, svc_url, 0))
                    self.log(f"⚠️ {label} still building -> {svc_url} | Added to Backend Servers (Ping in a moment).")
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

                label = f"SnapDeploy ({params['name']})"
                self.log(f"SnapDeploy STARTED -> {svc_url} | Waiting for build to go LIVE (~2-5 min)...")
                lat = self._wait_until_server_live(tid, label, svc_url, params["auth_key"], max_attempts=30, interval_s=12.0)
                if lat is not None:
                    self._pending_srv_queue.put((label, svc_url, lat))
                    self.log(f"✅ {label} is LIVE & READY ({lat} ms) -> {svc_url} | Added to Backend Servers.")
                else:
                    self._pending_srv_queue.put((label, svc_url, 0))
                    self.log(f"⚠️ {label} still building -> {svc_url} | Added to Backend Servers (Ping in a moment).")
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
                label = f"Cloudflare ({name})"
                self.log(f"Cloudflare uploaded -> {worker_url} | Verifying LIVE status...")
                lat = self._wait_until_server_live(tid, label, worker_url, params["auth_key"], max_attempts=8, interval_s=3.0)
                if lat is not None:
                    self._pending_srv_queue.put((label, worker_url, lat))
                    self.log(f"✅ {label} is LIVE & READY ({lat} ms) -> {worker_url} | Added to Backend Servers.")
                else:
                    self._pending_srv_queue.put((label, worker_url, 0))
                    self.log(f"⚠️ {label} deployed -> {worker_url} | Added to Backend Servers (check Ping).")
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
                self.sys_proxy_btn.setText(self._t("sys_proxy_off"))
                self.sys_proxy_btn.setObjectName("")
                self.sys_proxy_btn.style().unpolish(self.sys_proxy_btn)
                self.sys_proxy_btn.style().polish(self.sys_proxy_btn)
            self.proxy.stop()
            self.proxy = None
            self._save_config()
            self.status_pill.setObjectName("StatusPillStopped")
            self.status_pill.setText(self._t("stopped"))
            self.status_pill.style().unpolish(self.status_pill)
            self.status_pill.style().polish(self.status_pill)
            self.start_btn.setObjectName("PrimaryBtn")
            self.start_btn.setText(self._t("start_proxy"))
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
            pmode = "mitm" if self.rb_mitm.isChecked() else "tcp"
            if pmode == "mitm":
                _CERT_MGR.ensure_ca()
            self.proxy = FreeDnetProxyServer(
                pool=self.pool,
                server_pool=self.server_pool,
                auth_key=self.config["auth_key"],
                host=self.config["listen_host"],
                port=self.config["listen_port"],
                log_fn=self.log,
                route_mode=mode,
                proxy_mode=pmode,
                keep_awake=bool(self.chk_keep_awake.isChecked()),
            )
            self.proxy.start()
            lbl = "DIRECT" if mode == "direct" else "GAS"
            self.status_pill.setObjectName("StatusPillRunning")
            self.status_pill.setText(f"{self._t('running')} · {lbl}")
            self.status_pill.style().unpolish(self.status_pill)
            self.status_pill.style().polish(self.status_pill)
            self.start_btn.setObjectName("DangerBtn")
            self.start_btn.setText(self._t("stop_proxy"))
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
        bypass_domains = ["domain:script.google.com", "domain:googleusercontent.com", "domain:onrender.com", "domain:snapdeploy.app", "domain:snapdeploy.dev", "domain:workers.dev"]
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
                *(
                    [parse_chain_uri(self.config.get("chain_uri", ""), dialer_proxy="freednet-socks")]
                    if self.config.get("chain_enabled") and self.config.get("chain_uri", "").strip()
                    else []
                ),
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
                    {
                        "type": "field",
                        "network": "tcp",
                        "outboundTag": "chain-out" if (self.config.get("chain_enabled") and self.config.get("chain_uri", "").strip()) else "freednet-socks",
                    },
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
            self.sys_proxy_btn.setText(self._t("sys_proxy_off"))
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
            self.sys_proxy_btn.setText(self._t("sys_proxy_on"))
            self.sys_proxy_btn.setObjectName("PrimaryBtn")
            self.sys_proxy_btn.style().unpolish(self.sys_proxy_btn)
            self.sys_proxy_btn.style().polish(self.sys_proxy_btn)
            self.log(f"Windows System Proxy set to {host}:{port}")

    def toggle_tun_mode(self):
        if self.tun_active:
            self._stop_tun_process()
            self.tun_btn.setText(self._t("tun_off"))
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

        self._save_config()
        if self.config.get("chain_enabled"):
            if self.config.get("proxy_mode", "tcp") == "mitm":
                QMessageBox.warning(
                    self,
                    "Outbound Chain",
                    "Outbound Chain (VLESS/Trojan) requires TCP Tunnel mode. Switch Proxy Mode from HTTP MITM Relay to TCP Tunnel first.",
                )
                return
            try:
                parse_chain_uri(self.config.get("chain_uri", ""))
            except Exception as e:
                QMessageBox.warning(self, "Outbound Chain", f"Invalid VLESS/Trojan URI:\n{e}")
                return

        if not (self.proxy and self.proxy.running):
            self.toggle_proxy()
            if not (self.proxy and self.proxy.running):
                return

        self.tun_btn.setEnabled(False)
        self.tun_btn.setText(self._t("tun_starting"))

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
            self.tun_btn.setText(self._t("tun_on"))
            self.tun_btn.setObjectName("PrimaryBtn")
        else:
            self.tun_btn.setText(self._t("tun_off"))
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
            conc_lbl = "∞" if self.pool.max_concurrent >= 999 else str(self.pool.max_concurrent)
            daily_lbl = "∞" if self.pool.daily_limit >= 1000000 else str(self.pool.daily_limit)
            vals = [f"#{idx + 1}", acc.name, status, f"{acc.active}/{conc_lbl}", f"{acc.daily_requests}/{daily_lbl}", share, lat, acc.url]
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
        if self.proxy and self.proxy.running:
            self.kpi_tunnels.setText(str(self.proxy.active_tunnels))
            if now - self._last_speed_ui_ts >= 0.5:
                dt = max(0.1, now - self._last_stat_ts)
                self._last_stat_ts = now
                self._last_speed_ui_ts = now
                tx = self.proxy.tx_bytes
                rx = self.proxy.rx_bytes
                tx_inst = max(0.0, (tx - self._prev_tx) / dt)
                rx_inst = max(0.0, (rx - self._prev_rx) / dt)
                self._prev_tx, self._prev_rx = tx, rx
                alpha = 0.45
                self._speed_tx_ema = alpha * tx_inst + (1.0 - alpha) * self._speed_tx_ema
                self._speed_rx_ema = alpha * rx_inst + (1.0 - alpha) * self._speed_rx_ema
                if self._speed_tx_ema < 1.0:
                    self._speed_tx_ema = 0.0
                if self._speed_rx_ema < 1.0:
                    self._speed_rx_ema = 0.0
                self._peak_tx_rate = max(self._peak_tx_rate, self._speed_tx_ema)
                self._peak_rx_rate = max(self._peak_rx_rate, self._speed_rx_ema)

                self.kpi_speed.setText(f"{fmt_rate(self._speed_rx_ema)} ↓  |  {fmt_rate(self._speed_tx_ema)} ↑")
                self.kpi_peak.setText(f"{fmt_rate(self._peak_rx_rate)} ↓  |  {fmt_rate(self._peak_tx_rate)} ↑")
                self.kpi_traffic.setText(f"{fmt_bytes(rx)} ↓  |  {fmt_bytes(tx)} ↑")
            if now - self._last_save_ts > 30:
                self._last_save_ts = now
                try:
                    self._save_config()
                except Exception:
                    pass
        else:
            self._speed_tx_ema = 0.0
            self._speed_rx_ema = 0.0
            self.kpi_tunnels.setText("0")
            self.kpi_speed.setText("0 B/s  |  0 B/s")

        if self._tun_ui_dirty:
            self._tun_ui_dirty = False
            self._update_tun_btn_ui()

        added_srv = False
        while True:
            try:
                srv_name, srv_url, srv_lat = self._pending_srv_queue.get_nowait()
            except queue.Empty:
                break
            srv_obj = BackendServer(srv_name, srv_url, True)
            if srv_lat > 0:
                srv_obj.last_latency_ms = srv_lat
            with self.server_pool.lock:
                self.server_pool.servers.append(srv_obj)
            added_srv = True
        if added_srv:
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
            self.tray_proxy_act.setText(self._t("tray_stop") if running else self._t("tray_start"))
            self.tray_tun_act.setText(self._t("tun_on") if self.tun_active else self._t("tun_off"))


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
