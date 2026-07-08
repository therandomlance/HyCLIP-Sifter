"""Built-in themes and QSS stylesheets using semantic role names."""

from __future__ import annotations

from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

# Dark-theme roles (default). Light overrides are applied by apply_theme.
_DARK_ROLES = {
    "primary": "#3b82f6",
    "archive": "#22c55e",
    "delete": "#ef4444",
    "defer": "#a855f7",
    "skip": "#f59e0b",
    "surface": "#1e1e1e",
    "surface_alt": "#262626",
    "border": "#3a3a3a",
    "text": "#e5e5e5",
    "text_muted": "#9a9a9a",
    "accent": "#3b82f6",
}

_LIGHT_ROLES = {
    "primary": "#3b82f6",
    "archive": "#16a34a",
    "delete": "#dc2626",
    "defer": "#9333ea",
    "skip": "#d97706",
    "surface": "#f4f4f4",
    "surface_alt": "#ffffff",
    "border": "#d4d4d4",
    "text": "#1a1a1a",
    "text_muted": "#737373",
    "accent": "#3b82f6",
}

# Semantic color roles used throughout the QSS. Updated by apply_theme.
ROLES = dict(_DARK_ROLES)


def _set_roles(roles: dict[str, str]) -> None:
    ROLES.clear()
    ROLES.update(roles)


def _substitute(qss: str) -> str:
    # Replace longest tokens first so @surface does not eat @surface_alt.
    for role in sorted(ROLES, key=len, reverse=True):
        qss = qss.replace(f"@{role}", ROLES[role])
    return qss


_BASE_QSS = """
QWidget {
    background-color: @surface;
    color: @text;
    font-size: 13px;
}
QMainWindow, QDialog { background-color: @surface; }
QMenuBar { background-color: @surface_alt; border-bottom: 1px solid @border; }
QMenuBar::item:selected { background-color: @border; }
QMenu { background-color: @surface_alt; border: 1px solid @border; }
QMenu::item:selected { background-color: @border; }
QTabWidget::pane { border: 1px solid @border; top: -1px; }
QTabBar::tab {
    background-color: @surface_alt;
    padding: 6px 14px;
    border: 1px solid @border;
    border-bottom: none;
    margin-right: 2px;
}
QTabBar::tab:selected { background-color: @surface; border-bottom: 1px solid @surface; }
QToolBar { background-color: @surface_alt; border: none; border-bottom: 1px solid @border; spacing: 4px; padding: 3px; }
QStatusBar { background-color: @surface_alt; border-top: 1px solid @border; }
QStatusBar::item { border: none; }
QSplitter::handle { background-color: @border; }
QSplitter::handle:hover { background-color: @accent; }
QFrame#SectionFrame { border: 1px solid @border; border-radius: 4px; }
QFrame#HRule { background-color: @border; max-height: 1px; }
QGroupBox, QFrame#SectionBox {
    border: 1px solid @border;
    border-radius: 6px;
    margin-top: 10px;
    padding: 12px;
}
QGroupBox::title, QFrame#SectionTitle {
    color: @text;
    font-weight: 600;
    padding: 0 4px;
}
QPushButton {
    background-color: @surface_alt;
    border: 1px solid @border;
    border-radius: 4px;
    padding: 5px 12px;
    color: @text;
}
QPushButton:hover { background-color: @border; }
QPushButton:disabled { color: @text_muted; }
QPushButton#PrimaryBtn, QPushButton#accent {
    background-color: @accent;
    border-color: @accent;
    color: white;
    font-weight: 600;
}
QPushButton#PrimaryBtn:hover { background-color: #2f6fbd; }
QPushButton#ArchiveBtn { background-color: @archive; border-color: @archive; color: white; }
QPushButton#DeleteBtn  { background-color: @delete;  border-color: @delete;  color: white; }
QPushButton#DeferBtn   { background-color: @defer;   border-color: @defer;   color: white; }
QPushButton#SkipBtn    { background-color: @skip;    border-color: @skip;    color: black; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit, QDateEdit {
    background-color: @surface_alt;
    border: 1px solid @border;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: @accent;
}
QComboBox::drop-down { border: none; width: 18px; }
QListWidget#ThumbGrid {
    background-color: @surface;
    border: none;
    outline: 0;
}
QListWidget#ThumbGrid::item {
    border: 2px solid transparent;
    border-radius: 4px;
    margin: 3px;
}
QListWidget#ThumbGrid::item:selected {
    border: 2px solid @accent;
    background: transparent;
}
QProgressBar {
    background-color: @surface_alt;
    border: 1px solid @border;
    border-radius: 3px;
    text-align: center;
    height: 14px;
}
QProgressBar::chunk { background-color: @accent; border-radius: 2px; }
QLabel#CountBadge {
    border-radius: 9px;
    padding: 1px 8px;
    color: white;
}
QLabel#Toast {
    background-color: @surface_alt;
    border: 1px solid @border;
    border-radius: 6px;
    padding: 8px 12px;
}
"""


def apply_theme(app: QApplication, theme: str = "system", stylesheet_path: str = "") -> None:
    """Apply the built-in theme plus an optional user QSS override."""
    if theme == "light":
        _set_roles(_LIGHT_ROLES)
        _set_light_palette(app)
    elif theme == "dark":
        _set_roles(_DARK_ROLES)
        _set_dark_palette(app)
    else:
        # For system theme, pick roles based on the palette lightness.
        pal = QApplication.style().standardPalette()
        is_dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        _set_roles(_DARK_ROLES if is_dark else _LIGHT_ROLES)
        _set_system_palette(app)
    qss = _substitute(_BASE_QSS)
    if stylesheet_path:
        try:
            with open(stylesheet_path, "r", encoding="utf-8") as fh:
                qss += "\n" + fh.read()
        except OSError:
            pass
    app.setStyleSheet(qss)


def _set_dark_palette(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#e5e5e5"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#262626"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#2e2e2e"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#e5e5e5"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#262626"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#e5e5e5"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#3b82f6"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)


def _set_light_palette(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#f4f4f4"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#eeeeee"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#1a1a1a"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#e6e6e6"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#1a1a1a"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#3b82f6"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)


def _set_system_palette(app: QApplication) -> None:
    app.setPalette(QApplication.style().standardPalette())
