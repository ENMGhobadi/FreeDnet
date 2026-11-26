#!/usr/bin/env python3
"""
FreeDnet Pro (Windows 11 Next-Gen PySide6 / Qt 6 Dark Fluent UI)
Shares the core proxy, quota, and failover engine from freednet_client.py.
Install dependency: pip install PySide6
"""

import json
import os
import queue
import socket
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QMessageBox,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from freednet_client import (
    CONFIG_PATH,
    DEFAULT_CONFIG,
    DEFAULT_PRESET,
    GAS_QUOTA_PRESETS,
    BackendServer,
    FreeDnetProxyServer,
    GasAccount,
    GasAccountPool,
    ServerPool,
    post_direct_to_server,
    post_to_gas,
)


PRO_QSS = """
QMainWindow, QWidget#Root {
    background-color: #090d16;
    color: #e2e8f0;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}
QFrame#HeaderBar {
    background-color: #0d1322;
    border-bottom: 1px solid #1e293b;
}
QFrame#Card {
    background-color: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
}
QFrame#KpiCard {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 10px;
}
QFrame#Toolbar {
    background-color: #0d1424;
    border: 1px solid #1e293b;
    border-radius: 8px;
}
QLabel#BrandTitle {
    color: #ffffff;
    font-size: 20px;
    font-weight: 700;
}
QLabel#ProBadge {
    background-color: #1d4ed8;
    color: #dbeafe;
    border-radius: 5px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}
QLabel#Muted {
    color: #94a3b8;
    font-size: 12px;
}
QLabel#FieldLabel {
    color: #cbd5e1;
    font-weight: 600;
    font-size: 12px;
}
QLabel#KpiTitle {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
}
QLabel#KpiValue {
    color: #f8fafc;
    font-size: 17px;
    font-weight: 700;
}
QLabel#StatusPillStopped {
    background-color: #2a1218;
    color: #f87171;
    border: 1px solid #7f1d1d;
    border-radius: 12px;
    padding: 5px 12px;
    font-weight: 700;
    font-size: 12px;
}
QLabel#StatusPillRunning {
    background-color: #052e1c;
    color: #34d399;
    border: 1px solid #065f46;
    border-radius: 12px;
    padding: 5px 12px;
    font-weight: 700;
    font-size: 12px;
}
QLineEdit, QComboBox {
    background-color: #0b1120;
    color: #f1f5f9;
    border: 1px solid #293548;
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #3b82f6;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #0f172a;
    color: #f1f5f9;
    border: 1px solid #334155;
    selection-background-color: #2563eb;
}
QPushButton {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #283548;
    border-color: #475569;
}
QPushButton#PrimaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #0284c7);
    color: #ffffff;
    border: none;
    padding: 8px 18px;
    font-weight: 700;
}
QPushButton#PrimaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1d4ed8, stop:1 #0369a1);
}
QPushButton#DangerBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #dc2626, stop:1 #b91c1c);
    color: #ffffff;
    border: none;
    padding: 8px 18px;
    font-weight: 700;
}
QPushButton#DangerBtn:hover {
    background-color: #991b1b;
}
QPushButton#SmallBtn {
    padding: 5px 10px;
    font-size: 12px;
    border-radius: 6px;
}
QRadioButton, QCheckBox {
    color: #e2e8f0;
    spacing: 7px;
    font-weight: 500;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QTabWidget::pane {
    border: 1px solid #1e293b;
    border-radius: 10px;
    background-color: #111827;
    top: -1px;
}
QTabBar::tab {
    background-color: #0d1322;
    color: #94a3b8;
    border: 1px solid #1e293b;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 18px;
    margin-right: 4px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background-color: #111827;
    color: #60a5fa;
    border-bottom: 2px solid #3b82f6;
}
QTableWidget {
    background-color: #0b1120;
    alternate-background-color: #0f172a;
    color: #e2e8f0;
    gridline-color: #1e293b;
    border: 1px solid #1e293b;
    border-radius: 8px;
    selection-background-color: #1e3a8a;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #131c31;
    color: #94a3b8;
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid #1e293b;
    font-weight: 700;
    font-size: 12px;
}
QTextEdit#LogConsole {
    background-color: #050811;
    color: #cbd5e1;
    border: 1px solid #1e293b;
    border-radius: 8px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    padding: 8px;
}
QDialog {
    background-color: #0f172a;
    color: #e2e8f0;
}
"""


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


class FreeDnetProWindow(QMainWindow):
    SRV_PRIORITY_LBL = "Priority (#1 -> #2 Failover)"
    SRV_FAIR_LBL = "Fair-Share (1/N Equal)"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FreeDnet Pro — Next-Gen Smart Proxy Router")
        self.resize(1120, 820)
        self.setMinimumSize(960, 680)

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

        self.proxy = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._last_save_ts = time.time()

        self._build_ui()
        self._refresh_target_dropdown()
        self._sync_tables()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._on_ui_tick)
        self.ui_timer.start(500)

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
        self.config["auth_key"] = self.auth_edit.text().strip()
        self.config["listen_host"] = self.host_edit.text().strip() or "127.0.0.1"
        self.config["route_mode"] = "direct" if self.rb_direct.isChecked() else "gas"
        self.config["gas_mode"] = "fair" if self.chk_fair_share.isChecked() else "priority"
        self.config["server_mode"] = self.server_pool.server_mode
        self.config["quota_preset"] = self.preset_combo.currentText()
        self.config["keep_awake"] = self.chk_keep_awake.isChecked()
        try:
            self.config["listen_port"] = int(self.port_edit.text().strip())
        except ValueError:
            self.config["listen_port"] = 1080
        self.config["accounts"] = [acc.to_dict() for acc in self.pool.accounts]
        self.config["servers"] = [srv.to_dict() for srv in self.server_pool.servers]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def closeEvent(self, event):
        try:
            self._save_config()
            if self.proxy and self.proxy.running:
                self.proxy.stop()
        except Exception:
            pass
        event.accept()

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def _build_ui(self):
        root = QWidget(objectName="Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header Bar
        header = QFrame(objectName="HeaderBar")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 14, 20, 14)

        brand = QLabel("FreeDnet", objectName="BrandTitle")
        pro_badge = QLabel("PRO", objectName="ProBadge")
        sub = QLabel("Next-Gen Multi-GAS & Multi-Server Router", objectName="Muted")
        h_lay.addWidget(brand)
        h_lay.addSpacing(8)
        h_lay.addWidget(pro_badge)
        h_lay.addSpacing(12)
        h_lay.addWidget(sub)
        h_lay.addStretch()

        self.status_pill = QLabel("● STOPPED", objectName="StatusPillStopped")
        self.ping_btn = QPushButton("Ping All")
        self.e2e_btn = QPushButton("Test Proxy (E2E)")
        self.start_btn = QPushButton("▶  Start Proxy", objectName="PrimaryBtn")

        self.ping_btn.clicked.connect(self.test_all_accounts)
        self.e2e_btn.clicked.connect(self.test_proxy_e2e)
        self.start_btn.clicked.connect(self.toggle_proxy)

        h_lay.addWidget(self.status_pill)
        h_lay.addSpacing(12)
        h_lay.addWidget(self.ping_btn)
        h_lay.addWidget(self.e2e_btn)
        h_lay.addWidget(self.start_btn)
        outer.addWidget(header)

        # Content Body
        body = QVBoxLayout()
        body.setContentsMargins(18, 16, 18, 16)
        body.setSpacing(12)
        outer.addLayout(body)

        # 4 KPI Cards Row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.kpi_tunnels = self._make_kpi_card(kpi_row, "ACTIVE TUNNELS", "0")
        self.kpi_traffic = self._make_kpi_card(kpi_row, "TOTAL TRAFFIC", "0.0 KB / 0.0 KB")
        self.kpi_gas = self._make_kpi_card(kpi_row, "GAS POOL", "0 active")
        self.kpi_srv = self._make_kpi_card(kpi_row, "BACKEND SERVERS", "0 ready")
        body.addLayout(kpi_row)

        # Routing & Connection Card
        cfg_card = QFrame(objectName="Card")
        cfg_lay = QVBoxLayout(cfg_card)
        cfg_lay.setContentsMargins(16, 14, 16, 14)
        cfg_lay.setSpacing(12)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Chain Mode:", objectName="FieldLabel"))
        self.rb_gas = QRadioButton("Via Google Apps Script")
        self.rb_direct = QRadioButton("Direct to Backend")
        self.route_grp = QButtonGroup(self)
        self.route_grp.addButton(self.rb_gas)
        self.route_grp.addButton(self.rb_direct)
        if self.config.get("route_mode", "gas") == "direct":
            self.rb_direct.setChecked(True)
        else:
            self.rb_gas.setChecked(True)
        self.rb_gas.toggled.connect(self._on_route_change)

        row1.addWidget(self.rb_gas)
        row1.addWidget(self.rb_direct)
        row1.addStretch()

        row1.addWidget(QLabel("Backend Mode:", objectName="FieldLabel"))
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(240)
        self.target_combo.currentTextChanged.connect(self._on_target_change)
        row1.addWidget(self.target_combo)
        cfg_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Auth Key:", objectName="FieldLabel"))
        self.auth_edit = QLineEdit(self.config["auth_key"])
        self.auth_edit.setEchoMode(QLineEdit.Password)
        self.auth_edit.setMinimumWidth(220)
        row2.addWidget(self.auth_edit)

        self.chk_show_key = QCheckBox("Show")
        self.chk_show_key.toggled.connect(
            lambda checked: self.auth_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        row2.addWidget(self.chk_show_key)

        paste_btn = QPushButton("Paste", objectName="SmallBtn")
        paste_btn.clicked.connect(self._paste_auth_key)
        row2.addWidget(paste_btn)
        row2.addStretch()

        row2.addWidget(QLabel("Listen IP:", objectName="FieldLabel"))
        self.host_edit = QLineEdit(self.config["listen_host"])
        self.host_edit.setFixedWidth(120)
        row2.addWidget(self.host_edit)

        row2.addWidget(QLabel("Port:", objectName="FieldLabel"))
        self.port_edit = QLineEdit(str(self.config["listen_port"]))
        self.port_edit.setFixedWidth(72)
        row2.addWidget(self.port_edit)

        self.chk_keep_awake = QCheckBox("Keep Awake (10m)")
        self.chk_keep_awake.setChecked(bool(self.config.get("keep_awake", True)))
        self.chk_keep_awake.toggled.connect(self._on_keep_awake_change)
        row2.addWidget(self.chk_keep_awake)

        cfg_lay.addLayout(row2)
        body.addWidget(cfg_card)

        # Tabs: GAS Accounts & Backend Servers
        self.tabs = QTabWidget()
        body.addWidget(self.tabs, stretch=1)

        # Tab 1: GAS Accounts
        gas_tab = QWidget()
        gas_lay = QVBoxLayout(gas_tab)
        gas_lay.setContentsMargins(14, 14, 14, 14)
        gas_lay.setSpacing(10)

        gas_toolbar = QFrame(objectName="Toolbar")
        gt_lay = QHBoxLayout(gas_toolbar)
        gt_lay.setContentsMargins(12, 8, 12, 8)
        self.chk_fair_share = QCheckBox("Fair-Share Quota Distribution (1/N Equal across enabled accounts)")
        self.chk_fair_share.setChecked(self.config.get("gas_mode", "fair") == "fair")
        self.chk_fair_share.toggled.connect(self._on_gas_mode_change)
        gt_lay.addWidget(self.chk_fair_share)
        gt_lay.addStretch()

        gt_lay.addWidget(QLabel("Free Gmail Quota:", objectName="FieldLabel"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(GAS_QUOTA_PRESETS.keys()))
        init_preset = self.config.get("quota_preset", DEFAULT_PRESET)
        if init_preset in GAS_QUOTA_PRESETS:
            self.preset_combo.setCurrentText(init_preset)
        self.preset_combo.setMinimumWidth(290)
        self.preset_combo.currentTextChanged.connect(self._on_preset_change)
        gt_lay.addWidget(self.preset_combo)
        gas_lay.addWidget(gas_toolbar)

        self.gas_table = self._make_table(["#", "Account", "Status", "Active", "Daily Quota", "Share %", "Latency", "GAS Web App URL"])
        gas_lay.addWidget(self.gas_table, stretch=1)

        g_btns = QHBoxLayout()
        add_g = QPushButton("+ Add GAS Account", objectName="PrimaryBtn")
        add_g.clicked.connect(self.add_account)
        edit_g = QPushButton("Edit")
        edit_g.clicked.connect(self.edit_account)
        tog_g = QPushButton("Enable / Disable")
        tog_g.clicked.connect(self.toggle_account)
        up_g = QPushButton("▲ Up")
        up_g.clicked.connect(lambda: self.move_account(-1))
        dn_g = QPushButton("▼ Down")
        dn_g.clicked.connect(lambda: self.move_account(1))
        rem_g = QPushButton("Remove")
        rem_g.clicked.connect(self.remove_account)
        for b in (add_g, edit_g, tog_g, up_g, dn_g, rem_g):
            g_btns.addWidget(b)
        g_btns.addStretch()
        gas_lay.addLayout(g_btns)
        self.tabs.addTab(gas_tab, "Google Apps Script Accounts")

        # Tab 2: Backend Servers
        srv_tab = QWidget()
        srv_lay = QVBoxLayout(srv_tab)
        srv_lay.setContentsMargins(14, 14, 14, 14)
        srv_lay.setSpacing(10)

        self.srv_table = self._make_table(["#", "Server Name", "Status", "Requests", "Errors", "Latency", "Server URL"])
        srv_lay.addWidget(self.srv_table, stretch=1)

        s_btns = QHBoxLayout()
        add_s = QPushButton("+ Add Server", objectName="PrimaryBtn")
        add_s.clicked.connect(self.add_server)
        edit_s = QPushButton("Edit")
        edit_s.clicked.connect(self.edit_server)
        tog_s = QPushButton("Enable / Disable")
        tog_s.clicked.connect(self.toggle_server)
        up_s = QPushButton("▲ Up")
        up_s.clicked.connect(lambda: self.move_server(-1))
        dn_s = QPushButton("▼ Down")
        dn_s.clicked.connect(lambda: self.move_server(1))
        rem_s = QPushButton("Remove")
        rem_s.clicked.connect(self.remove_server)
        for b in (add_s, edit_s, tog_s, up_s, dn_s, rem_s):
            s_btns.addWidget(b)
        s_btns.addStretch()
        srv_lay.addLayout(s_btns)
        self.tabs.addTab(srv_tab, "Backend Servers (Render / SnappDeploy / Oracle)")

        # Activity Log Card
        log_card = QFrame(objectName="Card")
        log_lay = QVBoxLayout(log_card)
        log_lay.setContentsMargins(14, 10, 14, 12)
        log_lay.setSpacing(6)

        log_hdr = QHBoxLayout()
        log_hdr.addWidget(QLabel("Activity Log", objectName="FieldLabel"))
        log_hdr.addStretch()
        log_hdr.addWidget(QLabel("Free Gmail limit: 30 concurrent / 20k per day", objectName="Muted"))
        clr_btn = QPushButton("Clear", objectName="SmallBtn")
        clr_btn.clicked.connect(lambda: self.log_console.clear())
        log_hdr.addWidget(clr_btn)
        log_lay.addLayout(log_hdr)

        self.log_console = QTextEdit(objectName="LogConsole")
        self.log_console.setReadOnly(True)
        self.log_console.setFixedHeight(140)
        log_lay.addWidget(self.log_console)
        body.addWidget(log_card)

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

            threading.Thread(target=worker_direct, daemon=True).start()
            return

        if not self.pool.accounts:
            QMessageBox.information(self, "No Accounts", "Please add at least one GAS Web App URL first.")
            return

        def worker():
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

        threading.Thread(target=worker, daemon=True).start()

    def test_proxy_e2e(self):
        if not (self.proxy and self.proxy.running):
            QMessageBox.information(self, "Proxy Stopped", "Click 'Start Proxy' first.")
            return
        host = self.config["listen_host"]
        port = self.config["listen_port"]

        def worker():
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

        threading.Thread(target=worker, daemon=True).start()

    def toggle_proxy(self):
        if self.proxy and self.proxy.running:
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

    def _sync_tables(self):
        total_reqs = sum(a.total_requests for a in self.pool.accounts)
        self.gas_table.setRowCount(len(self.pool.accounts))
        for idx, acc in enumerate(self.pool.accounts):
            share = f"{(acc.total_requests / total_reqs * 100):.1f}%" if total_reqs > 0 else "0.0%"
            lat = f"{acc.last_latency_ms} ms" if acc.last_latency_ms > 0 else "-"
            status = acc.status_label(self.pool.daily_limit)
            vals = [f"#{idx + 1}", acc.name, status, f"{acc.active}/{self.pool.max_concurrent}", f"{acc.daily_requests}/{self.pool.daily_limit}", share, lat, acc.url]
            for c, v in enumerate(vals):
                item = self.gas_table.item(idx, c) or QTableWidgetItem()
                item.setText(str(v))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if c in (1, 7) else Qt.AlignCenter)
                if c == 2:
                    color = "#34d399" if status in ("Ready", "Active") else ("#94a3b8" if status == "Disabled" else "#fbbf24")
                    item.setForeground(QColor(color))
                self.gas_table.setItem(idx, c, item)

        self.srv_table.setRowCount(len(self.server_pool.servers))
        for idx, srv in enumerate(self.server_pool.servers):
            lat = f"{srv.last_latency_ms} ms" if srv.last_latency_ms > 0 else "-"
            status = srv.status_label
            vals = [f"#{idx + 1}", srv.name, status, str(srv.total_requests), str(srv.errors), lat, srv.url]
            for c, v in enumerate(vals):
                item = self.srv_table.item(idx, c) or QTableWidgetItem()
                item.setText(str(v))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if c in (1, 6) else Qt.AlignCenter)
                if c == 2:
                    color = "#34d399" if status == "Ready" else ("#94a3b8" if status == "Disabled" else "#f87171")
                    item.setForeground(QColor(color))
                self.srv_table.setItem(idx, c, item)

        enabled_gas = sum(1 for a in self.pool.accounts if a.enabled)
        enabled_srv = sum(1 for s in self.server_pool.servers if s.enabled)
        self.kpi_gas.setText(f"{enabled_gas} / {len(self.pool.accounts)} enabled")
        self.kpi_srv.setText(f"{enabled_srv} / {len(self.server_pool.servers)} enabled")

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
            doc = self.log_console.document()
            if doc.blockCount() > 500:
                cursor = self.log_console.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.movePosition(cursor.Down, cursor.KeepAnchor, doc.blockCount() - 400)
                cursor.removeSelectedText()

        self._sync_tables()
        if self.proxy and self.proxy.running:
            self.kpi_tunnels.setText(str(self.proxy.active_tunnels))
            self.kpi_traffic.setText(f"{self.proxy.tx_bytes / 1024:.1f} / {self.proxy.rx_bytes / 1024:.1f} KB")
            if time.time() - self._last_save_ts > 30:
                self._last_save_ts = time.time()
                try:
                    self._save_config()
                except Exception:
                    pass
        else:
            self.kpi_tunnels.setText("0")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(PRO_QSS)
    win = FreeDnetProWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
