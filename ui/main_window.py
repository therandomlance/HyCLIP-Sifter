from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
)

from clip_model import ClipModel
from config import Config
from database import Database
from hydrus_service import HydrusService
from ui.history_tab import HistoryTab
from ui.ingest_tab import IngestTab
from ui.search_tab import SearchTab


class MainWindow(QMainWindow):
    def __init__(self, config_path: str = "hyclip_sifter.ini", db_path: str = "hyclip_sifter.db"):
        super().__init__()
        self.setWindowTitle("HyCLIP Sifter")
        self.resize(2560, 1080)

        self.config = Config(config_path)
        self.db = Database(db_path)
        self.clip = ClipModel()
        self.hydrus = HydrusService(self.config.hydrus_url, self.config.hydrus_key)

        self._status_label = QLabel("Ready")
        self._status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sb = QStatusBar()
        sb.addWidget(self._status_label, 1)
        self.setStatusBar(sb)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.search_tab = SearchTab(self.db, self.hydrus, self.clip, self.config, self.set_status)
        self.ingest_tab = IngestTab(
            self.db, self.hydrus, self.clip, self.config, self.set_status
        )
        self.history_tab = HistoryTab(self.db, self.hydrus, self.set_status)

        self.tabs.addTab(self.search_tab, "Search")
        self.tabs.addTab(self.ingest_tab, "Ingest")
        self.tabs.addTab(self.history_tab, "History")

        self._wire_signals()
        self._refresh_all()

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def _wire_signals(self) -> None:
        self.ingest_tab.buckets_changed.connect(self._refresh_all)
        self.search_tab.buckets_changed.connect(self._refresh_all)
        self.history_tab.search_with_image.connect(self._on_history_search_with_image)

    def _refresh_all(self) -> None:
        self.ingest_tab.refresh_buckets()
        self.search_tab.refresh_buckets()
        self.history_tab.refresh_buckets()
        self.ingest_tab.refresh_model_status()

    def _on_history_search_with_image(self, hash_: str) -> None:
        self.tabs.setCurrentWidget(self.search_tab)
        self.search_tab.on_query_search_requested(hash_)

    def closeEvent(self, event) -> None:
        try:
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
