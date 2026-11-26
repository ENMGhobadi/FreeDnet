"""Modern theme palettes and Qt stylesheet builder for FreeDnet."""

THEMES = {
    "Obsidian Slate (Default)": {
        "is_light": False,
        "bg": "#0b0f19",
        "header": "#101625",
        "card": "#131b2e",
        "kpi": "#0f172a",
        "toolbar": "#101829",
        "input": "#0b1220",
        "console": "#070b14",
        "border": "#22304a",
        "input_border": "#2d3f5e",
        "text": "#f1f5f9",
        "title": "#ffffff",
        "muted": "#94a3b8",
        "label": "#e2e8f0",
        "accent1": "#3b82f6",
        "accent2": "#06b6d4",
        "accent_hover1": "#2563eb",
        "accent_hover2": "#0891b2",
        "accent_light": "#60a5fa",
        "tile_bg": "#0f172a",
        "tile_checked_bg": "#172554",
        "badge_bg": "#1d4ed8",
        "badge_fg": "#eff6ff",
        "btn_bg": "#1e293b",
        "btn_hover": "#283548",
        "btn_border": "#334155",
        "sel_bg": "#1d4ed8",
        "alt_row": "#101827",
        "hdr_bg": "#152036",
        "pill_stop_bg": "#2b1218",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#7f1d1d",
        "pill_run_bg": "#063120",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Nordic Frost (Dark)": {
        "is_light": False,
        "bg": "#191e29",
        "header": "#141822",
        "card": "#212736",
        "kpi": "#1c2230",
        "toolbar": "#1b202d",
        "input": "#151a24",
        "console": "#10141c",
        "border": "#303a4f",
        "input_border": "#3c4861",
        "text": "#e6edf3",
        "title": "#ffffff",
        "muted": "#9fb0c7",
        "label": "#d8e2ef",
        "accent1": "#58a6ff",
        "accent2": "#38bdf8",
        "accent_hover1": "#3b82f6",
        "accent_hover2": "#0ea5e9",
        "accent_light": "#79c0ff",
        "tile_bg": "#1b212e",
        "tile_checked_bg": "#1e3a5f",
        "badge_bg": "#2563eb",
        "badge_fg": "#f0f9ff",
        "btn_bg": "#2a3244",
        "btn_hover": "#354057",
        "btn_border": "#465470",
        "sel_bg": "#2563eb",
        "alt_row": "#1e2432",
        "hdr_bg": "#1a202c",
        "pill_stop_bg": "#3b1b22",
        "pill_stop_fg": "#fda4af",
        "pill_stop_bd": "#9f1239",
        "pill_run_bg": "#11382d",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Carbon Amber (Dark)": {
        "is_light": False,
        "bg": "#0e0e11",
        "header": "#141418",
        "card": "#18181d",
        "kpi": "#131317",
        "toolbar": "#141419",
        "input": "#0c0c0f",
        "console": "#08080a",
        "border": "#2a2a32",
        "input_border": "#3b3b47",
        "text": "#f4f4f5",
        "title": "#ffffff",
        "muted": "#a1a1aa",
        "label": "#e4e4e7",
        "accent1": "#f59e0b",
        "accent2": "#ea580c",
        "accent_hover1": "#d97706",
        "accent_hover2": "#c2410c",
        "accent_light": "#fbbf24",
        "tile_bg": "#141418",
        "tile_checked_bg": "#3b230a",
        "badge_bg": "#b45309",
        "badge_fg": "#fef3c7",
        "btn_bg": "#23232a",
        "btn_hover": "#2f2f38",
        "btn_border": "#3f3f4a",
        "sel_bg": "#b45309",
        "alt_row": "#131317",
        "hdr_bg": "#1b1b21",
        "pill_stop_bg": "#2c1215",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#7f1d1d",
        "pill_run_bg": "#0b2e1f",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Forest Mint (Dark)": {
        "is_light": False,
        "bg": "#101715",
        "header": "#0d1311",
        "card": "#17211e",
        "kpi": "#131c19",
        "toolbar": "#141d1a",
        "input": "#0e1513",
        "console": "#090e0c",
        "border": "#263832",
        "input_border": "#314740",
        "text": "#ecfdf5",
        "title": "#ffffff",
        "muted": "#9cb8ae",
        "label": "#d1fae5",
        "accent1": "#10b981",
        "accent2": "#14b8a6",
        "accent_hover1": "#059669",
        "accent_hover2": "#0d9488",
        "accent_light": "#34d399",
        "tile_bg": "#131c19",
        "tile_checked_bg": "#0b3b2c",
        "badge_bg": "#047857",
        "badge_fg": "#d1fae5",
        "btn_bg": "#1f2e29",
        "btn_hover": "#293c36",
        "btn_border": "#375048",
        "sel_bg": "#047857",
        "alt_row": "#141d1a",
        "hdr_bg": "#182420",
        "pill_stop_bg": "#36181c",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#991b1b",
        "pill_run_bg": "#0b3628",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Alabaster Daylight (Light)": {
        "is_light": True,
        "bg": "#f1f5f9",
        "header": "#ffffff",
        "card": "#ffffff",
        "kpi": "#f8fafc",
        "toolbar": "#f8fafc",
        "input": "#ffffff",
        "console": "#f8fafc",
        "border": "#cbd5e1",
        "input_border": "#94a3b8",
        "text": "#0f172a",
        "title": "#020617",
        "muted": "#475569",
        "label": "#1e293b",
        "accent1": "#2563eb",
        "accent2": "#0284c7",
        "accent_hover1": "#1d4ed8",
        "accent_hover2": "#0369a1",
        "accent_light": "#2563eb",
        "tile_bg": "#f8fafc",
        "tile_checked_bg": "#dbeafe",
        "badge_bg": "#1d4ed8",
        "badge_fg": "#ffffff",
        "btn_bg": "#e2e8f0",
        "btn_hover": "#cbd5e1",
        "btn_border": "#94a3b8",
        "sel_bg": "#bfdbfe",
        "alt_row": "#f8fafc",
        "hdr_bg": "#e2e8f0",
        "pill_stop_bg": "#fee2e2",
        "pill_stop_fg": "#b91c1c",
        "pill_stop_bd": "#fca5a5",
        "pill_run_bg": "#dcfce7",
        "pill_run_fg": "#15803d",
        "pill_run_bd": "#86efac",
    },
    "Iced Latte Cream (Light)": {
        "is_light": True,
        "bg": "#f5ede2",
        "header": "#fbf6ef",
        "card": "#fffaf3",
        "kpi": "#f2e7d8",
        "toolbar": "#f7efe4",
        "input": "#ffffff",
        "console": "#f8f1e7",
        "border": "#dcc7ad",
        "input_border": "#bfa280",
        "text": "#2e1c10",
        "title": "#1f1209",
        "muted": "#7c5c42",
        "label": "#4a2e1a",
        "accent1": "#9a5a28",
        "accent2": "#b87339",
        "accent_hover1": "#7d471d",
        "accent_hover2": "#965a28",
        "accent_light": "#9a5a28",
        "tile_bg": "#f6ede0",
        "tile_checked_bg": "#ebd7be",
        "badge_bg": "#8c4d1f",
        "badge_fg": "#fffaf3",
        "btn_bg": "#eadbca",
        "btn_hover": "#dec9b2",
        "btn_border": "#bfa280",
        "sel_bg": "#e6cfb4",
        "alt_row": "#f9f3ea",
        "hdr_bg": "#eadbca",
        "pill_stop_bg": "#fee2e2",
        "pill_stop_fg": "#b91c1c",
        "pill_stop_bd": "#fca5a5",
        "pill_run_bg": "#dcfce7",
        "pill_run_fg": "#15803d",
        "pill_run_bd": "#86efac",
    },
    "Obsidian Orange & Black": {
        "is_light": False,
        "bg": "#050505",
        "header": "#0a0a0a",
        "card": "#0f0f0f",
        "kpi": "#0b0b0b",
        "toolbar": "#0c0c0c",
        "input": "#070707",
        "console": "#030303",
        "border": "#261810",
        "input_border": "#3d2415",
        "text": "#f5f5f5",
        "title": "#ffffff",
        "muted": "#a8998c",
        "label": "#e8ddd5",
        "accent1": "#ea6a1a",
        "accent2": "#f97316",
        "accent_hover1": "#d4590f",
        "accent_hover2": "#ea580c",
        "accent_light": "#fb923c",
        "tile_bg": "#0c0c0c",
        "tile_checked_bg": "#2e1506",
        "badge_bg": "#c2410c",
        "badge_fg": "#fff7ed",
        "btn_bg": "#171717",
        "btn_hover": "#241a14",
        "btn_border": "#382419",
        "sel_bg": "#c2410c",
        "alt_row": "#0a0a0a",
        "hdr_bg": "#14100d",
        "pill_stop_bg": "#2c1215",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#7f1d1d",
        "pill_run_bg": "#0b2e1f",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Abyssal Jellyfish (Dark)": {
        "is_light": False,
        "bg": "#030812",
        "header": "#050c1b",
        "card": "#071124",
        "kpi": "#050d1d",
        "toolbar": "#060e1f",
        "input": "#040915",
        "console": "#02050c",
        "border": "#10284d",
        "input_border": "#1a3d73",
        "text": "#eaf4ff",
        "title": "#ffffff",
        "muted": "#7fa6d6",
        "label": "#cde2ff",
        "accent1": "#0ea5e9",
        "accent2": "#2563eb",
        "accent_hover1": "#0284c7",
        "accent_hover2": "#1d4ed8",
        "accent_light": "#38bdf8",
        "tile_bg": "#060f20",
        "tile_checked_bg": "#0b2852",
        "badge_bg": "#0284c7",
        "badge_fg": "#f0f9ff",
        "btn_bg": "#0b1933",
        "btn_hover": "#12264a",
        "btn_border": "#1d3c70",
        "sel_bg": "#0369a1",
        "alt_row": "#050c1a",
        "hdr_bg": "#09162e",
        "pill_stop_bg": "#2b1218",
        "pill_stop_fg": "#fca5a5",
        "pill_stop_bd": "#7f1d1d",
        "pill_run_bg": "#063120",
        "pill_run_fg": "#6ee7b7",
        "pill_run_bd": "#059669",
    },
    "Misty Alpine Lake (Light)": {
        "is_light": True,
        "bg": "#dce6f0",
        "header": "#edf3f8",
        "card": "#f3f7fb",
        "kpi": "#e3ecf4",
        "toolbar": "#e7eff6",
        "input": "#ffffff",
        "console": "#e8f0f7",
        "border": "#b3c4d6",
        "input_border": "#8da5bd",
        "text": "#132230",
        "title": "#0b1622",
        "muted": "#4d647a",
        "label": "#1e3347",
        "accent1": "#3b678c",
        "accent2": "#4f82ab",
        "accent_hover1": "#2c5273",
        "accent_hover2": "#3b678c",
        "accent_light": "#2c5273",
        "tile_bg": "#e6eef6",
        "tile_checked_bg": "#cadbeb",
        "badge_bg": "#2c5273",
        "badge_fg": "#f8fbff",
        "btn_bg": "#cfdce8",
        "btn_hover": "#bdcee0",
        "btn_border": "#94adc4",
        "sel_bg": "#b8cee3",
        "alt_row": "#eaf1f8",
        "hdr_bg": "#d2e0ec",
        "pill_stop_bg": "#fee2e2",
        "pill_stop_fg": "#b91c1c",
        "pill_stop_bd": "#fca5a5",
        "pill_run_bg": "#dcfce7",
        "pill_run_fg": "#15803d",
        "pill_run_bd": "#86efac",
    },
}

THEME_ALIASES = {
    "Midnight Dark (Default)": "Obsidian Slate (Default)",
    "Slate Blue & Cool Gray": "Nordic Frost (Dark)",
    "Emerald & Slate Gray": "Forest Mint (Dark)",
    "Warm Nescafe (Light)": "Alabaster Daylight (Light)",
}


def build_qss(t: dict) -> str:
    return f"""
QWidget {{
    color: {t["text"]};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QMainWindow, QWidget#Root {{
    background-color: {t["bg"]};
    color: {t["text"]};
}}
QLabel {{
    color: {t["text"]};
    background-color: transparent;
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
    selection-color: #ffffff;
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
    selection-color: #ffffff;
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
QPushButton#ModeTile {{
    background-color: {t["tile_bg"]};
    color: {t["text"]};
    border: 1.5px solid {t["border"]};
    border-radius: 10px;
    padding: 9px 14px;
    text-align: left;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton#ModeTile:hover {{
    border-color: {t["accent_light"]};
    background-color: {t["btn_hover"]};
}}
QPushButton#ModeTile:checked {{
    background-color: {t["tile_checked_bg"]};
    border: 2px solid {t["accent1"]};
    color: {t["title"]};
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
    padding: 6px 12px;
    font-size: 12px;
    border-radius: 6px;
}}
QRadioButton, QCheckBox {{
    color: {t["text"]};
    background-color: transparent;
    spacing: 7px;
    font-weight: 500;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1.5px solid {t["input_border"]};
    background-color: {t["input"]};
}}
QCheckBox::indicator {{
    border-radius: 4px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {t["accent1"]};
    border-color: {t["accent1"]};
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
    padding: 9px 18px;
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
QTableWidget::item {{
    color: {t["text"]};
    padding: 4px;
}}
QHeaderView::section {{
    background-color: {t["hdr_bg"]};
    color: {t["label"]};
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
QDialog, QMessageBox {{
    background-color: {t["card"]};
    color: {t["text"]};
}}
QDialog QLabel, QMessageBox QLabel {{
    color: {t["text"]};
}}
QFrame#ToastInfo, QFrame#ToastSuccess, QFrame#ToastWarning, QFrame#ToastError {{
    background-color: {t["card"]};
    border: 1.5px solid {t["accent1"]};
    border-radius: 10px;
}}
QFrame#ToastSuccess {{
    border-color: #10b981;
}}
QFrame#ToastWarning {{
    border-color: #f59e0b;
}}
QFrame#ToastError {{
    border-color: #ef4444;
}}
QLabel#ToastText {{
    color: {t["text"]};
    font-size: 12.5px;
    font-weight: 600;
}}
QPushButton#ToastClose {{
    background-color: transparent;
    border: none;
    color: {t["muted"]};
    font-size: 14px;
    font-weight: 700;
    padding: 2px 6px;
}}
QPushButton#ToastClose:hover {{
    color: {t["title"]};
}}
"""
