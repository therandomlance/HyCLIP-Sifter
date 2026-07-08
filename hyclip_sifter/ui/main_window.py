"""Main window: tabs, menu/toolbar/statusbar, cross-tab wiring, lifecycle."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QLabel, QToolBar,
    QMessageBox, QApplication, QDialog,
)

from ..config import Config
from ..database import Database
from ..clip_model import ClipModel
from ..hydrus_service import HydrusService
from ..thumbnail_cache import ThumbnailCache
from .ingest_tab import IngestTab
from .search_tab import SearchTab
from .history_tab import HistoryTab
from .dialogs import PreferencesDialog


class MainWindow(QMainWindow):
    def __init__(self, config: Config, db: Database, clip: ClipModel,
                 hydrus: HydrusService, cache: ThumbnailCache):
        super().__init__()
        self._config = config
        self._db = db
        self._clip = clip
        self._hydrus = hydrus
        self._cache = cache
        self.setWindowTitle("HyCLIP Sifter")
        self.setMinimumSize(1024, 700)
        self._restore_geometry()
        self._build_ui()
        self._wire_signals()
        self._refresh_all_buckets()
        self._maybe_show_wizard_banner()
        if self._config.load_on_startup:
            QTimer.singleShot(100, self.ingest_tab.load_model)

    # ----------------------------------------------------------- geometry
    def _restore_geometry(self) -> None:
        settings = QSettings("HyCLIP", "Sifter")
        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            screen = self.screen().availableGeometry()
            self.resize(int(screen.width() * 0.75), int(screen.height() * 0.75))
            self.move(int(screen.width() * 0.125), int(screen.height() * 0.125))

    def _save_geometry(self) -> None:
        QSettings("HyCLIP", "Sifter").setValue("geometry", self.saveGeometry())

    # ----------------------------------------------------------- UI build
    def _build_ui(self) -> None:
        container = QWidget()
        self._container_layout = QVBoxLayout(container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.search_tab = SearchTab(self._config, self._db, self._clip,
                                    self._hydrus, self._cache)
        self.ingest_tab = IngestTab(self._config, self._db, self._clip,
                                    self._hydrus, self._cache)
        self.history_tab = HistoryTab(self._config, self._db, self._clip,
                                      self._hydrus, self._cache)
        self.tabs.addTab(self.search_tab, "Search")
        self.tabs.addTab(self.ingest_tab, "Ingest")
        self.tabs.addTab(self.history_tab, "History")
        self._container_layout.addWidget(self.tabs)
        self.setCentralWidget(container)

        self._build_menu()
        self._build_toolbar()
        self._build_status_bar()

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        pref = QAction("Preferences…", self)
        pref.setShortcut(QKeySequence("Ctrl+P"))
        pref.triggered.connect(self.open_preferences)
        quit_act = QAction("Quit", self)
        quit_act.setShortcut(QKeySequence("Ctrl+Q"))
        quit_act.triggered.connect(self.close)
        file_menu.addAction(pref)
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        edit_menu = menubar.addMenu("Edit")
        copy_act = QAction("Copy", self)
        copy_act.setShortcut(QKeySequence.Copy)
        copy_act.triggered.connect(self.copy_selected_hash)
        select_all = QAction("Select All", self)
        select_all.setShortcut(QKeySequence("Ctrl+A"))
        select_all.triggered.connect(self.select_all)
        deselect_all = QAction("Deselect All", self)
        deselect_all.setShortcut(QKeySequence("Ctrl+Shift+A"))
        deselect_all.triggered.connect(self.deselect_all)
        invert = QAction("Invert Selection", self)
        invert.setShortcut(QKeySequence("Ctrl+I"))
        invert.triggered.connect(self.invert_selection)
        edit_menu.addAction(copy_act)
        edit_menu.addSeparator()
        edit_menu.addAction(select_all)
        edit_menu.addAction(deselect_all)
        edit_menu.addAction(invert)

        view_menu = menubar.addMenu("View")
        theme_menu = view_menu.addMenu("Theme")
        group = QActionGroup(self)
        self._theme_actions: dict[str, QAction] = {}
        for label, key in [("System default", "system"), ("Dark", "dark"),
                           ("Light", "light")]:
            act = QAction(label, self, checkable=True)
            act.setChecked(self._config.theme == key)
            act.triggered.connect(lambda _, k=key: self._set_theme(k))
            group.addAction(act)
            theme_menu.addAction(act)
            self._theme_actions[key] = act
        size_menu = view_menu.addMenu("Thumbnail Size")
        for label, px in [("Small 150px", 150), ("Medium 300px", 300),
                          ("Large 450px", 450)]:
            act = QAction(label, self, checkable=True)
            act.setChecked(abs(self._config.thumbnail_size - px) < 25)
            act.triggered.connect(lambda _, p=px: self._set_thumb_size(p))
            size_menu.addAction(act)

        help_menu = menubar.addMenu("Help")
        about = QAction("About", self)
        about.triggered.connect(self._about)
        shortcuts = QAction("Keyboard Shortcuts", self)
        shortcuts.triggered.connect(self._show_shortcuts)
        help_menu.addAction(about)
        help_menu.addAction(shortcuts)

    def _build_toolbar(self) -> None:
        tb = QToolBar("toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        # Back / Forward search history (search tab actions).
        self.back_act = QAction("◀ Back", self)
        self.fwd_act = QAction("Forward ▶", self)
        self.stop_act = QAction("Stop Search", self)
        self.load_act = QAction("Load Model", self)
        self.eject_act = QAction("Eject Model", self)
        self.back_act.triggered.connect(self.search_tab.go_back)
        self.fwd_act.triggered.connect(self.search_tab.go_forward)
        self.stop_act.triggered.connect(self._stop_search)
        self.load_act.triggered.connect(self.ingest_tab.load_model)
        self.eject_act.triggered.connect(self.ingest_tab.eject_model)
        tb.addAction(self.back_act)
        tb.addAction(self.fwd_act)
        tb.addSeparator()
        tb.addAction(self.stop_act)
        tb.addSeparator()
        tb.addAction(self.load_act)
        tb.addAction(self.eject_act)
        self.addToolBar(tb)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.status_msg = QLabel("Ready")
        sb.addWidget(self.status_msg, 1)
        self.model_indicator = QLabel("🧠 Not loaded")
        self.hydrus_indicator = QLabel("🔴 Hydrus")
        self.bucket_indicator = QLabel("🗄 0 buckets")
        for w in (self.model_indicator, self.hydrus_indicator, self.bucket_indicator):
            w.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            w.setFixedHeight(20)
        sb.addPermanentWidget(self.model_indicator)
        sb.addPermanentWidget(self.hydrus_indicator)
        sb.addPermanentWidget(self.bucket_indicator)

    def _maybe_show_wizard_banner(self) -> None:
        no_model = not self._clip.is_loaded
        no_buckets = not self._db.list_buckets()
        self._banner: QWidget | None = None
        if no_model or no_buckets:
            banner = QLabel(
                "Welcome! ① Load the CLIP model → ② Create a bucket → "
                "③ Ingest hashes → ④ Start searching"
            )
            banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            banner.setStyleSheet("padding: 4px; border-bottom: 1px solid #3a3a3a;")
            self._banner = banner
            self._container_layout.insertWidget(0, banner)
            self.tabs.setCurrentIndex(0)

    def _maybe_hide_banner(self) -> None:
        banner = getattr(self, "_banner", None)
        if banner is None:
            return
        if self._clip.is_loaded and self._db.list_buckets():
            banner.hide()
            self._container_layout.removeWidget(banner)
            banner.deleteLater()
            self._banner = None

    # ----------------------------------------------------------- wiring
    def _wire_signals(self) -> None:
        self.ingest_tab.buckets_changed.connect(self._on_buckets_changed)
        self.search_tab.buckets_changed.connect(self._on_buckets_changed)
        self.ingest_tab.model_state_changed.connect(self.search_tab.on_model_state_changed)
        self.ingest_tab.model_state_changed.connect(self._maybe_hide_banner)
        self.ingest_tab.model_state_changed.connect(self._update_model_indicator)
        self.ingest_tab.status_message.connect(self._set_status)
        self.search_tab.status_message.connect(self._set_status)
        self.history_tab.search_with_image.connect(self._on_search_with_image)
        # Tab shortcuts.
        for keys, idx in [("Ctrl+1", 0), ("Ctrl+2", 1), ("Ctrl+3", 2)]:
            act = QAction(self)
            act.setShortcut(QKeySequence(keys))
            act.triggered.connect(lambda _, i=idx: self.tabs.setCurrentIndex(i))
            self.addAction(act)

    def _on_buckets_changed(self) -> None:
        self._refresh_all_buckets()
        self._update_bucket_indicator()
        self._maybe_hide_banner()

    def _refresh_all_buckets(self) -> None:
        self.ingest_tab.refresh_buckets(current=self.ingest_tab._current_bucket())
        self.search_tab.refresh_buckets(current=self.search_tab.bucket_combo.currentText() or None)
        self.history_tab.refresh_buckets(current=self.history_tab._current_bucket())
        self._update_bucket_indicator()

    def _update_bucket_indicator(self) -> None:
        n = len(self._db.list_buckets())
        self.bucket_indicator.setText(f"🗄 {n} buckets")

    def _update_model_indicator(self) -> None:
        if self._clip.is_loaded:
            self.model_indicator.setText(f"🧠 {self._clip.loaded_name} ({self._clip.device})")
        else:
            self.model_indicator.setText("🧠 Not loaded")

    def _set_status(self, msg: str) -> None:
        self.status_msg.setText(msg)

    def _on_search_with_image(self, hash_: str) -> None:
        self.tabs.setCurrentWidget(self.search_tab)
        self.search_tab.search_with_image(hash_)

    # ----------------------------------------------------------- actions
    def open_preferences(self) -> None:
        dlg = PreferencesDialog(self._config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_theme(self._config.theme)
            self._update_bucket_indicator()

    def _set_theme(self, key: str) -> None:
        self._config.set("ui", "theme", key)
        self._config.save()
        from .theme import apply_theme
        apply_theme(QApplication.instance(), key, self._config.stylesheet)
        self._sync_theme_menu()
        # Re-emit so tabs can refresh inline-styled widgets.
        for tab in (self.ingest_tab, self.search_tab, self.history_tab):
            handler = getattr(tab, "on_theme_changed", None)
            if callable(handler):
                handler()

    def _sync_theme_menu(self) -> None:
        key = self._config.theme
        for k, act in getattr(self, "_theme_actions", {}).items():
            act.setChecked(k == key)

    def _set_thumb_size(self, px: int) -> None:
        self._config.set("ui", "thumbnail_size", px)
        self._config.save()
        self.search_tab.size_spin.setValue(px)
        self.history_tab.size_spin.setValue(px)

    def _stop_search(self) -> None:
        if self.search_tab._search_worker is not None:
            self.search_tab._search_worker.cancel()

    def copy_selected_hash(self) -> None:
        grid = self._current_grid()
        if grid is None:
            return
        items = grid.selectedItems()
        if not items:
            return
        QApplication.clipboard().setText(items[0].data(Qt.ItemDataRole.UserRole))

    def select_all(self) -> None:
        grid = self._current_grid()
        if grid is not None:
            grid.select_all()

    def deselect_all(self) -> None:
        grid = self._current_grid()
        if grid is not None:
            grid.deselect_all()

    def invert_selection(self) -> None:
        grid = self._current_grid()
        if grid is not None:
            grid.invert_selection()

    def _current_grid(self):
        w = self.tabs.currentWidget()
        if w is None:
            return None
        return getattr(w, "grid", None)

    def _about(self) -> None:
        QMessageBox.about(self, "About HyCLIP Sifter",
                          "<b>HyCLIP Sifter</b><br>CLIP-powered visual similarity triage "
                          "for Hydrus.<br>Version 1.0.0")

    def _show_shortcuts(self) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
        w = QDialog(self)
        w.setWindowTitle("Keyboard Shortcuts")
        w.setWindowFlags(w.windowFlags() | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        lay = QVBoxLayout(w)
        text = (
            "<b>Search tab</b><br>"
            "A — Archive &nbsp; D — Delete &nbsp; S — Skip &nbsp; F — Defer<br>"
            "Ctrl+Enter — Run search &nbsp; Ctrl+R — Random sample<br>"
            "Ctrl+L — Clear query &nbsp; Escape — Deselect / clear<br>"
            "Enter — Search using focused image &nbsp; Shift+Enter — Search using selection<br>"
            "<br><b>Grid</b><br>"
            "Ctrl+A / Ctrl+Shift+A / Ctrl+I — Select / Deselect / Invert<br>"
            "Arrows / Home / End / PageUp / PageDown — Navigate<br>"
            "Space — Toggle focused item<br>"
            "<br><b>Global</b><br>"
            "Ctrl+1/2/3 — Switch tabs &nbsp; Ctrl+P — Preferences"
        )
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.RichText)
        lay.addWidget(label)
        w.show()

    # ----------------------------------------------------------- shutdown
    def closeEvent(self, event) -> None:
        self.ingest_tab.cleanup()
        self.search_tab.cleanup()
        self.history_tab.cleanup()
        self._save_geometry()
        if self._clip.is_loaded:
            self._clip.eject()
        self._db.close()
        super().closeEvent(event)
