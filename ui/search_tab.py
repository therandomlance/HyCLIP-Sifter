from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from workers import (
    HydrusOperationWorker,
    OP_ARCHIVE,
    OP_DELETE,
    OP_DEFER,
    OP_SKIP,
    SearchWorker,
    ThumbnailLoader,
)

PLACEHOLDER_THUMB = (
    "No query image selected.\n"
    "Right-click a result and choose\n"
    '"Search using this image", or press\n'
    '"Random" to sample the bucket.'
)


class QueryThumbLabel(QLabel):
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


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
        self._op_workers: list[HydrusOperationWorker] = []
        self._retiring: list = []

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

        self.thumb_label = QueryThumbLabel(PLACEHOLDER_THUMB)
        self.thumb_label.setFixedSize(200, 200)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("border: 1px solid #444; background: #222;")
        self.thumb_label.doubleClicked.connect(self.clear_query_image)
        v.addWidget(self.thumb_label)

        v.addWidget(QLabel("Text query:"))
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Add text to refine search...")
        self.text_edit.setEnabled(False)
        v.addWidget(self.text_edit)

        weight_row = QHBoxLayout()
        self.sign_btn = QPushButton("+")
        self.sign_btn.setFixedWidth(32)
        self.sign_btn.setCheckable(True)
        self.sign_btn.setEnabled(False)
        self.sign_btn.toggled.connect(self._on_sign_toggled)
        weight_row.addWidget(self.sign_btn)
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(0.0, 100.0)
        self.weight_spin.setSingleStep(0.25)
        self.weight_spin.setValue(1.5)
        self.weight_spin.setSuffix("x")
        self.weight_spin.setEnabled(False)
        weight_row.addWidget(self.weight_spin)
        v.addLayout(weight_row)

        v.addWidget(QLabel("Thumbnail size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(48, 512)
        self.size_spin.setSingleStep(25)
        self.size_spin.setValue(self.config.thumbnail_size)
        self.size_spin.setSuffix(" px")
        self.grid.set_icon_size(self.size_spin.value())
        self.size_spin.valueChanged.connect(self.grid.set_icon_size)
        v.addWidget(self.size_spin)

        v.addWidget(QLabel("Number of results:"))
        self.results_spin = QSpinBox()
        self.results_spin.setRange(1, 2000)
        self.results_spin.setValue(self.config.search_size)
        v.addWidget(self.results_spin)

        v.addWidget(QLabel("Bucket:"))
        self.bucket_combo = QComboBox()
        v.addWidget(self.bucket_combo)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.run_search)
        self.random_btn = QPushButton("Random")
        self.random_btn.clicked.connect(self.run_random)
        self.clear_btn = QPushButton("Clear Query")
        self.clear_btn.clicked.connect(self.clear_query)
        self.deselect_btn = QPushButton("Deselect All")
        self.deselect_btn.clicked.connect(self.grid.deselect_all)

        nav_grid = QGridLayout()
        nav_grid.addWidget(self.search_btn, 0, 0)
        nav_grid.addWidget(self.random_btn, 0, 1)
        nav_grid.addWidget(self.clear_btn, 1, 0)
        nav_grid.addWidget(self.deselect_btn, 1, 1)
        v.addLayout(nav_grid)

        v.addSpacing(12)

        self.archive_btn = QPushButton("Archive")
        self.archive_btn.setStyleSheet("background-color: #2e7d32; color: white;")
        self.archive_btn.clicked.connect(lambda: self._apply_operation(OP_ARCHIVE))
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setStyleSheet("background-color: #c62828; color: white;")
        self.delete_btn.clicked.connect(lambda: self._apply_operation(OP_DELETE))
        self.defer_btn = QPushButton("Defer")
        self.defer_btn.setStyleSheet("background-color: #6a1b9a; color: white;")
        self.defer_btn.clicked.connect(lambda: self._apply_operation(OP_DEFER))
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setStyleSheet("background-color: #f9a825; color: black;")
        self.skip_btn.clicked.connect(lambda: self._apply_operation(OP_SKIP))

        op_grid = QGridLayout()
        op_grid.addWidget(self.archive_btn, 0, 0)
        op_grid.addWidget(self.delete_btn, 0, 1)
        op_grid.addWidget(self.defer_btn, 1, 0)
        op_grid.addWidget(self.skip_btn, 1, 1)
        v.addLayout(op_grid)

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

    def clear_query_image(self) -> None:
        self.query_hash = None
        self.thumb_label.clear()
        self.thumb_label.setText(PLACEHOLDER_THUMB)

    def clear_query(self) -> None:
        self.clear_query_image()
        self.grid.set_hashes([])

    def refresh_model_status(self) -> None:
        loaded = self.clip.is_loaded
        self.text_edit.setEnabled(loaded)
        self.weight_spin.setEnabled(loaded)
        self.sign_btn.setEnabled(loaded)

    def _on_sign_toggled(self, checked: bool) -> None:
        self.sign_btn.setText("-" if checked else "+")

    def _load_query_thumbnail(self, hash_: str) -> None:
        if self._thumb_loader is not None:
            old = self._thumb_loader
            try:
                old.loaded.disconnect()
            except Exception:
                pass
            try:
                old.finished_all.disconnect()
            except Exception:
                pass
            if old.isRunning():
                old.cancel()
                self._retiring.append(old)
                old.finished.connect(lambda _=None, o=old: self._retire(o))
            else:
                old.deleteLater()
            self._thumb_loader = None
        loader = ThumbnailLoader(self.hydrus, [hash_])
        loader.loaded.connect(self._on_query_thumb)
        loader.finished_all.connect(self._on_thumb_loader_finished)
        self._thumb_loader = loader
        loader.start()

    def _on_thumb_loader_finished(self) -> None:
        worker = self.sender()
        if worker is self._thumb_loader:
            self._thumb_loader = None
        if worker is not None:
            worker.deleteLater()

    def _on_query_thumb(self, hash_: str, data: bytes) -> None:
        pix = QPixmap()
        pix.loadFromData(data)
        if pix.isNull():
            return
        self.thumb_label.setPixmap(
            pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def run_search(self) -> None:
        self._start_search(self.query_hash, random=False)

    def run_random(self) -> None:
        self.clear_query_image()
        self._start_search(None, random=True)

    def _start_search(self, query_hash: str | None, random: bool = False) -> None:
        bucket = self.current_bucket()
        if not bucket:
            QMessageBox.information(self, "Search", "No bucket selected.")
            return
        if self._search_worker is not None:
            old = self._search_worker
            try:
                old.results.disconnect()
                old.random_results.disconnect()
                old.failed.disconnect()
                old.finished.disconnect()
            except Exception:
                pass
            if old.isRunning():
                old.cancel()
                self._retiring.append(old)
                old.finished.connect(lambda _=None, o=old: self._retire(o))
            else:
                old.deleteLater()
            self._search_worker = None
        text = self.text_edit.text().strip()
        if not random:
            magnitude = self.weight_spin.value()
            multiplier = -magnitude if self.sign_btn.isChecked() else magnitude
        else:
            multiplier = 1.0
        k = self.results_spin.value()
        self.set_status(f"Searching bucket '{bucket}'...")
        worker = SearchWorker(
            self.db,
            self.hydrus,
            self.clip,
            bucket,
            query_hash,
            k,
            text=text if not random else "",
            text_multiplier=multiplier,
            random=random,
        )
        worker.results.connect(self._on_results)
        worker.random_results.connect(self._on_random)
        worker.failed.connect(self._on_search_failed)
        worker.finished.connect(self._on_search_finished)
        self._search_worker = worker
        worker.start()

    def _retire(self, worker) -> None:
        try:
            self._retiring.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def _on_results(self, neighbors) -> None:
        hashes = [h for h, _ in neighbors]
        tooltips = {h: f"{h}\ndistance: {d:.4f}" for h, d in neighbors}
        self.grid.set_hashes(hashes, tooltips)
        self.grid.scrollToTop()
        self.set_status(f"Found {len(hashes)} results.")

    def _on_random(self, hashes) -> None:
        self.grid.set_hashes(hashes)
        self.grid.scrollToTop()
        self.set_status(f"Random sample: {len(hashes)} results.")

    def _on_search_failed(self, msg: str) -> None:
        self.set_status(f"Search failed: {msg}")
        QMessageBox.warning(self, "Search failed", msg)

    def _on_search_finished(self) -> None:
        worker = self.sender()
        if worker is self._search_worker:
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
        tag_key = ""
        if operation == OP_DEFER:
            tag_key = self.config.tag_service_key
            if not tag_key:
                QMessageBox.warning(self, "Config error", "No tag service key set in ini file.")
                return
        worker = HydrusOperationWorker(self.hydrus, operation, selected, tag_key)
        worker.bucket = bucket
        worker.op_name = name
        worker.done.connect(self._on_op_done)
        worker.failed.connect(self._on_op_failed)
        worker.finished.connect(self._on_op_finished)
        self._op_workers.append(worker)
        worker.start()
        self.set_status(f"{name.capitalize()}ing {len(selected)} image(s)...")

    def _on_op_done(self, operation: int, hashes: list[str]) -> None:
        worker = self.sender()
        bucket = getattr(worker, "bucket", None)
        name = getattr(worker, "op_name", "operation")
        if bucket:
            for h in hashes:
                self.db.remove_from_bucket(bucket, h, operation)
                self.grid.remove_hash(h)
        self.set_status(f"{name.capitalize()}d {len(hashes)} image(s).")
        self.buckets_changed.emit()

    def _on_op_failed(self, operation: int, hashes: list[str], msg: str) -> None:
        worker = self.sender()
        name = getattr(worker, "op_name", "operation")
        self.set_status(f"{name.capitalize()} failed: {msg}")
        QMessageBox.warning(self, "API error", f"{name} failed: {msg}")

    def _on_op_finished(self) -> None:
        worker = self.sender()
        if worker in self._op_workers:
            self._op_workers.remove(worker)
        worker.deleteLater()

    def on_query_search_requested(self, hash_: str) -> None:
        self.set_query_hash(hash_)
        self.run_search()
