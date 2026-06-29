from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QFrame,
)

from clip_model import ClipModel
from config import Config
from database import Database
from hydrus_service import HydrusService
from ui.thumbnail_grid import ThumbnailGrid
from workers import SearchWorker, ThumbnailLoader

OP_ARCHIVE = 1
OP_DELETE = 0
OP_SKIP = 2
OP_DEFER = 3

DEFER_TAG = "hyclip:defer"

PLACEHOLDER_THUMB = (
    "No query image selected.\n"
    "Right-click a result and choose\n"
    '"Search using this image", or press\n'
    '"Random" to sample the bucket.'
)


class SearchTab(QWidget):
    buckets_changed = Signal()

    def __init__(
        self,
        db: Database,
        hydrus: HydrusService,
        clip: ClipModel,
        config: Config,
        set_status,
        parent=None,
    ):
        super().__init__(parent)
        self.db = db
        self.hydrus = hydrus
        self.clip = clip
        self.config = config
        self.set_status = set_status
        self.query_hash: str | None = None
        self._search_worker: SearchWorker | None = None
        self._thumb_loader: ThumbnailLoader | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.grid = ThumbnailGrid(hydrus)
        self.grid.set_menu_factory(self._build_menu)
        self.grid.search_requested.connect(self.on_query_search_requested)
        self.grid.archive_requested.connect(lambda hs: self._apply_operation(OP_ARCHIVE, hs))
        self.grid.delete_requested.connect(lambda hs: self._apply_operation(OP_DELETE, hs))
        self.grid.skip_requested.connect(lambda hs: self._apply_operation(OP_SKIP, hs))
        self.grid.defer_requested.connect(lambda hs: self._apply_operation(OP_DEFER, hs))

        self.sidebar = self._build_sidebar()
        layout.addWidget(self.sidebar, 0)
        layout.addWidget(self.grid, 1)

    def _build_sidebar(self) -> QWidget:
        w = QFrame()
        w.setFixedWidth(230)
        v = QVBoxLayout(w)

        self.thumb_label = QLabel(PLACEHOLDER_THUMB)
        self.thumb_label.setFixedSize(200, 200)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("border: 1px solid #444; background: #222;")
        v.addWidget(self.thumb_label)

        v.addWidget(QLabel("Thumbnail size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(48, 512)
        self.size_spin.setSingleStep(25)
        self.size_spin.setValue(400)
        self.size_spin.setSuffix(" px")
        self.size_spin.valueChanged.connect(self.grid.set_icon_size)
        v.addWidget(self.size_spin)

        v.addWidget(QLabel("Number of results:"))
        self.results_spin = QSpinBox()
        self.results_spin.setRange(1, 2000)
        self.results_spin.setValue(50)
        v.addWidget(self.results_spin)

        v.addWidget(QLabel("Bucket:"))
        self.bucket_combo = QComboBox()
        v.addWidget(self.bucket_combo)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.run_search)
        v.addWidget(self.search_btn)

        self.random_btn = QPushButton("Random")
        self.random_btn.clicked.connect(self.run_random)
        v.addWidget(self.random_btn)

        self.clear_btn = QPushButton("Clear Query")
        self.clear_btn.clicked.connect(self.clear_query)
        v.addWidget(self.clear_btn)

        self.deselect_btn = QPushButton("Deselect All")
        self.deselect_btn.clicked.connect(self.grid.deselect_all)
        v.addWidget(self.deselect_btn)

        v.addSpacing(12)

        self.archive_btn = QPushButton("Archive")
        self.archive_btn.setStyleSheet("background-color: #2e7d32; color: white;")
        self.archive_btn.clicked.connect(lambda: self._apply_operation(OP_ARCHIVE))
        v.addWidget(self.archive_btn)

        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setStyleSheet("background-color: #f9a825; color: black;")
        self.skip_btn.clicked.connect(lambda: self._apply_operation(OP_SKIP))
        v.addWidget(self.skip_btn)

        self.defer_btn = QPushButton("Defer")
        self.defer_btn.setStyleSheet("background-color: #6a1b9a; color: white;")
        self.defer_btn.clicked.connect(lambda: self._apply_operation(OP_DEFER))
        v.addWidget(self.defer_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setStyleSheet("background-color: #c62828; color: white;")
        self.delete_btn.clicked.connect(lambda: self._apply_operation(OP_DELETE))
        v.addWidget(self.delete_btn)

        v.addStretch(1)
        return w

    def refresh_buckets(self, select: str | None = None) -> None:
        if select is None:
            select = self.bucket_combo.currentText() or None
        buckets = self.db.list_buckets()
        self.bucket_combo.clear()
        for b in buckets:
            self.bucket_combo.addItem(b)
        if select and select in buckets:
            self.bucket_combo.setCurrentText(select)

    def current_bucket(self) -> str | None:
        return self.bucket_combo.currentText() or None

    def _build_menu(self, hash_: str):
        menu = self.grid.make_default_actions(hash_, allow_ops=True)
        from PySide6.QtWidgets import QMenu

        m = QMenu(self.grid)
        for a in menu:
            m.addAction(a)
        return m

    def set_query_hash(self, hash_: str, fetch_thumb: bool = True) -> None:
        self.query_hash = hash_
        self.thumb_label.setText(hash_[:16] + "\n...")
        if fetch_thumb:
            self._load_query_thumbnail(hash_)

    def clear_query(self) -> None:
        self.query_hash = None
        self.thumb_label.clear()
        self.thumb_label.setText(PLACEHOLDER_THUMB)
        self.grid.set_hashes([])

    def _load_query_thumbnail(self, hash_: str) -> None:
        if self._thumb_loader is not None:
            try:
                self._thumb_loader.cancel()
                self._thumb_loader.wait(300)
            except Exception:
                pass
        self._thumb_loader = ThumbnailLoader(self.hydrus, [hash_])
        self._thumb_loader.loaded.connect(self._on_query_thumb)
        self._thumb_loader.finished_all.connect(self._thumb_loader.deleteLater)
        self._thumb_loader.start()

    def _on_query_thumb(self, hash_: str, data: bytes) -> None:
        pix = QPixmap()
        pix.loadFromData(data)
        if pix.isNull():
            return
        self.thumb_label.setPixmap(
            pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def run_search(self) -> None:
        bucket = self.current_bucket()
        if not bucket:
            QMessageBox.information(self, "Search", "No bucket selected.")
            return
        if self._search_worker is not None and self._search_worker.isRunning():
            return
        k = self.results_spin.value()
        self.set_status(f"Searching bucket '{bucket}'...")
        self._search_worker = SearchWorker(
            self.db, self.hydrus, self.clip, bucket, self.query_hash, k
        )
        self._search_worker.results.connect(self._on_results)
        self._search_worker.random_results.connect(self._on_random)
        self._search_worker.failed.connect(self._on_search_failed)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def run_random(self) -> None:
        old = self.query_hash
        self.query_hash = None
        self.run_search()
        self.query_hash = old

    def _on_results(self, neighbors) -> None:
        hashes = [h for h, _ in neighbors]
        tooltips = {h: f"{h}\ndistance: {d:.4f}" for h, d in neighbors}
        self.grid.set_hashes(hashes, tooltips)
        self.set_status(f"Found {len(hashes)} results.")

    def _on_random(self, hashes) -> None:
        self.grid.set_hashes(hashes)
        self.set_status(f"Random sample: {len(hashes)} results.")

    def _on_search_failed(self, msg: str) -> None:
        self.set_status(f"Search failed: {msg}")
        QMessageBox.warning(self, "Search failed", msg)

    def _on_search_finished(self) -> None:
        worker = self._search_worker
        self._search_worker = None
        if worker is not None:
            worker.deleteLater()

    def _apply_operation(self, operation: int, hashes: list[str] | None = None) -> None:
        bucket = self.current_bucket()
        if not bucket:
            QMessageBox.information(self, "Operation", "No bucket selected.")
            return
        selected = hashes if hashes is not None else self.grid.selected_hashes()
        if not selected:
            QMessageBox.information(self, "Operation", "No images selected.")
            return
        name = {OP_ARCHIVE: "archive", OP_DELETE: "delete", OP_SKIP: "skip", OP_DEFER: "defer"}[operation]
        if QMessageBox.question(
            self,
            "Confirm",
            f"{name.capitalize()} {len(selected)} image(s)?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            if operation == OP_ARCHIVE:
                self.hydrus.archive(selected)
            elif operation == OP_DELETE:
                self.hydrus.delete(selected)
            elif operation == OP_DEFER:
                tag_key = self.config.tag_service_key
                if not tag_key:
                    QMessageBox.warning(self, "Config error", "No tag service key set in ini file.")
                    return
                self.hydrus.add_tags(selected, tag_key, [DEFER_TAG])
        except Exception as exc:
            QMessageBox.warning(self, "API error", f"{name} failed: {exc}")
            return
        for h in selected:
            self.db.remove_from_bucket(bucket, h, operation)
            self.grid.remove_hash(h)
        self.set_status(f"{name.capitalize()}d {len(selected)} image(s).")
        self.buckets_changed.emit()

    def on_query_search_requested(self, hash_: str) -> None:
        self.set_query_hash(hash_)
        self.run_search()
