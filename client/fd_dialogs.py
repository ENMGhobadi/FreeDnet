"""Responsive FlowLayout and deployment/entry dialogs for FreeDnet."""

import time

from fd_config import list_running_windows_apps
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

class FlowLayout(QLayout):
    """Responsive wrapping layout so toolbars and cards never overlap on narrow windows."""

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 8, v_spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, w):
        super().addWidget(w)

    def addSpacing(self, _px: int):
        pass

    def addStretch(self, _s: int = 0):
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        line_height = 0
        for item in self._items:
            sz = item.sizeHint()
            next_x = x + sz.width() + self._h_space
            if next_x - self._h_space > eff.right() + 1 and line_height > 0:
                x = eff.x()
                y += line_height + self._v_space
                next_x = x + sz.width() + self._h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_height = max(line_height, sz.height())
        return y + line_height - rect.y() + m.bottom()



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


class ToastBanner(QFrame):
    """Floating 4-second auto-dismiss notification banner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ToastInfo")
        self.setVisible(False)
        self.setMaximumWidth(460)
        self.setMinimumWidth(260)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(10)

        self.icon_lbl = QLabel("ℹ️")
        self.icon_lbl.setFixedWidth(20)
        lay.addWidget(self.icon_lbl, alignment=Qt.AlignTop)

        self.msg_lbl = QLabel("", objectName="ToastText")
        self.msg_lbl.setWordWrap(True)
        lay.addWidget(self.msg_lbl, stretch=1)

        self.close_btn = QPushButton("✕", objectName="ToastClose")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.clicked.connect(self.hide)
        lay.addWidget(self.close_btn, alignment=Qt.AlignTop)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_toast(self, message: str, level: str = "info", duration_ms: int = 4000):
        icons = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
        obj_names = {
            "success": "ToastSuccess",
            "error": "ToastError",
            "warning": "ToastWarning",
            "info": "ToastInfo",
        }
        self.icon_lbl.setText(icons.get(level, "ℹ️"))
        self.msg_lbl.setText(message)
        self.setObjectName(obj_names.get(level, "ToastInfo"))
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(max(1000, int(duration_ms)))

    def reposition(self):
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        sz = self.sizeHint()
        w = min(max(sz.width(), 280), max(280, p.width() - 40))
        h = self.heightForWidth(w) if self.hasHeightForWidth() else sz.height()
        self.resize(w, max(h, sz.height()))
        x = 20 if p.layoutDirection() == Qt.RightToLeft else max(20, p.width() - self.width() - 20)
        y = max(20, p.height() - self.height() - 20)
        self.move(x, y)


class AppRoutingDialog(QDialog):
    """Per-application backend server routing rules for Windows TUN Mode."""

    DIRECT_TOKEN = "__DIRECT__"

    def __init__(
        self,
        parent,
        enabled: bool,
        routes: list[dict],
        server_names: list[str],
        tr=None,
    ):
        super().__init__(parent)
        self._tr = tr or (lambda k: k)
        self._server_names = list(server_names)
        self._running_apps = list_running_windows_apps()

        self.setWindowTitle(self._tr("app_routing_title"))
        self.setMinimumSize(620, 440)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self.chk_enable = QCheckBox(self._tr("app_routing_enable"))
        self.chk_enable.setChecked(bool(enabled))
        top.addWidget(self.chk_enable)
        top.addStretch()

        self.refresh_btn = QPushButton(self._tr("app_routing_refresh"), objectName="SmallBtn")
        self.refresh_btn.clicked.connect(self._refresh_apps)
        self.add_btn = QPushButton(self._tr("app_routing_add"), objectName="PrimaryBtn")
        self.add_btn.clicked.connect(lambda: self._add_row())
        self.rem_btn = QPushButton(self._tr("remove_btn"), objectName="SmallBtn")
        self.rem_btn.clicked.connect(self._remove_selected)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.add_btn)
        top.addWidget(self.rem_btn)
        lay.addLayout(top)

        hint = QLabel(self._tr("app_routing_hint"), objectName="Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(self._tr("app_routing_headers"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.table, stretch=1)

        for r in routes or []:
            proc = (r.get("process") or "").strip()
            srv = (r.get("server") or "").strip()
            if proc and srv:
                self._add_row(proc, srv)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _populate_app_combo(self, combo: QComboBox, selected: str = ""):
        cur = selected or combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self._running_apps)
        if cur:
            idx = combo.findText(cur, Qt.MatchFixedString)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(cur)
        combo.blockSignals(False)

    def _refresh_apps(self):
        self._running_apps = list_running_windows_apps()
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, 0)
            if isinstance(cb, QComboBox):
                self._populate_app_combo(cb)

    def _add_row(self, process: str = "", server: str = ""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 34)

        app_cb = QComboBox()
        app_cb.setEditable(True)
        self._populate_app_combo(app_cb, process)
        self.table.setCellWidget(row, 0, app_cb)

        srv_cb = QComboBox()
        for s in self._server_names:
            srv_cb.addItem(s, s)
        srv_cb.addItem("DIRECT (Bypass)", self.DIRECT_TOKEN)
        if server:
            idx = srv_cb.findData(server)
            if idx < 0:
                idx = srv_cb.findText(server)
            if idx >= 0:
                srv_cb.setCurrentIndex(idx)
        self.table.setCellWidget(row, 1, srv_cb)

    def _remove_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def result_data(self) -> tuple[bool, list[dict]]:
        routes = []
        seen = set()
        for row in range(self.table.rowCount()):
            app_cb = self.table.cellWidget(row, 0)
            srv_cb = self.table.cellWidget(row, 1)
            if not isinstance(app_cb, QComboBox) or not isinstance(srv_cb, QComboBox):
                continue
            proc = app_cb.currentText().strip()
            srv = srv_cb.currentData() or srv_cb.currentText().strip()
            if proc and srv and proc.lower() not in seen:
                seen.add(proc.lower())
                routes.append({"process": proc, "server": srv})
        return self.chk_enable.isChecked(), routes
