"""Search tab: query, vector search, triage."""

from __future__ import annotations

import os
import subprocess
import sys
from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QAction, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter, QFrame,
    QPushButton, QComboBox, QSpinBox, QLineEdit, QLabel, QDoubleSpinBox, QMessageBox, QToolButton,
)
from ..database import Database, OP_DELETE, OP_ARCHIVE, OP_SKIP, OP_DEFER
from ..clip_model import ClipModel
from ..hydrus_service import HydrusService
from ..workers import SearchWorker, HydrusOperationWorker, DedupWorker
from .widgets import hrule
from .thumbnail_grid import ThumbnailGrid


class QueryThumb(QLabel):
    """A thumbnail label that clears the query on double-click."""

    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(150, 150)
        self.setFrameShape(QFrame.Shape.Box)
        self.setText("No query image")
        self._hash: str | None = None
        self._src_pixmap: QPixmap | None = None

    def set_image(self, data: bytes, hash_: str) -> None:
        pm = QPixmap()
        if pm.loadFromData(data):
            self._src_pixmap = pm
            self._rescale_pixmap()
        self._hash = hash_

    def _rescale_pixmap(self) -> None:
        pm = getattr(self, "_src_pixmap", None)
        if pm is None or pm.isNull():
            return
        self.setPixmap(pm.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale_pixmap()

    def clear_image(self) -> None:
        self._src_pixmap = None
        self.setPixmap(QPixmap())
        self.setText("No query image")
        self._hash = None
        self.cleared.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        self.clear_image()


class SearchTab(QWidget):
    buckets_changed = Signal()
    status_message = Signal(str)

    def __init__(self, config, db: Database, clip: ClipModel, hydrus: HydrusService,
                 thumb_cache, parent=None):
        super().__init__(parent)
        self._config = config
        self._db = db
        self._clip = clip
        self._hydrus = hydrus
        self._cache = thumb_cache
        self._search_worker: SearchWorker | None = None
        self._triage_worker: HydrusOperationWorker | None = None
        self._dedup_worker: DedupWorker | None = None
        self._retiring: list = []
        self._query_hash: str | None = None
        self._query_bytes: bytes | None = None
        self._query_embedding: list[float] | None = None
        self._negative_hash: str | None = None
        self._history: deque[tuple[str | None, str, float, bool]] = deque()
        self._history_idx: int = -1
        self._suppress_history: bool = False
        self._build_ui()
        self.refresh_buckets()
        self._update_button_states()

    # ----------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        # ---- Sidebar ----
        sidebar = QFrame()
        sidebar.setObjectName("SectionBox")
        sidebar.setMinimumWidth(300)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 12, 12, 12)
        side.setSpacing(8)

        title1 = QLabel("QUERY")
        title1.setObjectName("SectionTitle")
        side.addWidget(title1)
        self.query_thumb = QueryThumb()
        self.query_thumb.cleared.connect(self._on_query_cleared)
        side.addWidget(self.query_thumb)
        self.negative_toggle = QPushButton("Set Negative Image")
        self.negative_toggle.setCheckable(True)
        side.addWidget(self.negative_toggle)
        self.negative_thumb = QueryThumb()
        self.negative_thumb.setVisible(False)
        self.negative_thumb.cleared.connect(self._on_negative_cleared)
        self.negative_toggle.toggled.connect(self.negative_thumb.setVisible)
        side.addWidget(self.negative_thumb)
        side.addWidget(QLabel("Text influence:"))
        text_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Describe what to look for (or avoid)…")
        self.text_sign = QToolButton()
        self.text_sign.setText("+")
        self.text_sign.setCheckable(True)
        self.text_sign.setFixedSize(28, 28)
        self.text_sign.setToolTip(
            "Added to image embedding. + = more like this, − = less like this."
        )
        self.text_sign.toggled.connect(
            lambda checked: self.text_sign.setText("−" if checked else "+")
        )
        self.text_weight = QDoubleSpinBox()
        self.text_weight.setRange(0.0, 100.0)
        self.text_weight.setSingleStep(0.25)
        self.text_weight.setValue(1.5)
        self.text_weight.setSuffix("x")
        text_row.addWidget(self.text_input, 1)
        text_row.addWidget(self.text_sign)
        text_row.addWidget(self.text_weight)
        side.addLayout(text_row)
        side.addWidget(hrule())

        title2 = QLabel("SEARCH")
        title2.setObjectName("SectionTitle")
        side.addWidget(title2)
        form = QFormLayout()
        form.setSpacing(4)
        self.bucket_combo = QComboBox()
        self.results_spin = QSpinBox()
        self.results_spin.setRange(1, 2000)
        self.results_spin.setValue(self._config.search_size)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(48, 1000)
        self.size_spin.setSingleStep(25)
        self.size_spin.setSuffix(" px")
        self.size_spin.setValue(self._config.thumbnail_size)
        form.addRow("Bucket:", self.bucket_combo)
        form.addRow("Results:", self.results_spin)
        form.addRow("Size:", self.size_spin)
        side.addLayout(form)
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("PrimaryBtn")
        side.addWidget(self.search_btn)
        self.random_btn = QPushButton("Random Sample")
        side.addWidget(self.random_btn)
        clr_row = QHBoxLayout()
        self.clear_query_btn = QPushButton("Clear Query")
        self.deselect_btn = QPushButton("Deselect")
        clr_row.addWidget(self.clear_query_btn)
        clr_row.addWidget(self.deselect_btn)
        side.addLayout(clr_row)
        side.addWidget(hrule())

        title3 = QLabel("TRIAGE")
        title3.setObjectName("SectionTitle")
        side.addWidget(title3)
        triage_grid = QHBoxLayout()
        self.archive_btn = QPushButton("Archive")
        self.archive_btn.setObjectName("ArchiveBtn")
        self.archive_btn.setToolTip("Archive selected images (A)")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("DeleteBtn")
        self.delete_btn.setToolTip("Delete selected images (D)")
        defer_btn = QPushButton("Defer")
        defer_btn.setObjectName("DeferBtn")
        defer_btn.setToolTip("Defer selected images (F)")
        skip_btn = QPushButton("Skip")
        skip_btn.setObjectName("SkipBtn")
        skip_btn.setToolTip("Skip selected images (S)")
        triage_grid.addWidget(self.archive_btn)
        triage_grid.addWidget(self.delete_btn)
        triage_grid2 = QHBoxLayout()
        triage_grid2.addWidget(defer_btn)
        triage_grid2.addWidget(skip_btn)
        side.addLayout(triage_grid)
        side.addLayout(triage_grid2)

        # Sidebar triage confirmation (below the 2x2 grid, no timeout).
        self.sidebar_confirm = QFrame()
        sc_layout = QHBoxLayout(self.sidebar_confirm)
        sc_layout.setContentsMargins(0, 4, 0, 0)
        self.sidebar_confirm_label = QLabel()
        sc_layout.addWidget(self.sidebar_confirm_label, 1)
        self.sidebar_confirm_yes = QPushButton("Yes")
        self.sidebar_confirm_no = QPushButton("Cancel")
        sc_layout.addWidget(self.sidebar_confirm_yes)
        sc_layout.addWidget(self.sidebar_confirm_no)
        self.sidebar_confirm.hide()
        side.addWidget(self.sidebar_confirm)

        side.addStretch(1)
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(4)
        header = QHBoxLayout()
        self.result_label = QLabel("0 results")
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("distance ascending")
        self.sort_combo.addItem("distance descending")
        self.sort_combo.addItem("random")
        self.dups_btn = QPushButton("Show Duplicates")
        header.addWidget(self.result_label)
        header.addStretch(1)
        header.addWidget(QLabel("Sorted by:"))
        header.addWidget(self.sort_combo)
        header.addWidget(self.dups_btn)
        rlay.addLayout(header)

        self.grid = ThumbnailGrid(self._hydrus, self._cache, self)
        rlay.addWidget(self.grid, 1)

        # Floating triage bar with confirmation row above buttons.
        self.triage_bar = QFrame(right)
        self.triage_bar.setObjectName("Toast")
        triage_bar_vlay = QVBoxLayout(self.triage_bar)
        triage_bar_vlay.setContentsMargins(4, 4, 4, 4)
        triage_bar_vlay.setSpacing(4)

        # Confirmation row (above buttons, no timeout).
        self.bar_confirm = QFrame()
        bc_layout = QHBoxLayout(self.bar_confirm)
        bc_layout.setContentsMargins(0, 0, 0, 0)
        self.bar_confirm_label = QLabel()
        bc_layout.addWidget(self.bar_confirm_label)
        self.bar_confirm_yes = QPushButton("Yes")
        self.bar_confirm_no = QPushButton("Cancel")
        bc_layout.addWidget(self.bar_confirm_yes)
        bc_layout.addWidget(self.bar_confirm_no)
        bc_layout.addStretch(1)
        self.bar_confirm.hide()
        triage_bar_vlay.addWidget(self.bar_confirm)

        bar_layout = QHBoxLayout()
        for code, name, obj in [
            (OP_ARCHIVE, "Archive (A)", "ArchiveBtn"),
            (OP_DELETE, "Delete (D)", "DeleteBtn"),
            (OP_DEFER, "Defer (F)", "DeferBtn"),
            (OP_SKIP, "Skip (S)", "SkipBtn"),
        ]:
            btn = QPushButton(name)
            btn.setObjectName(obj)
            btn.clicked.connect(lambda _, c=code: self._request_triage(c, source="bar"))
            bar_layout.addWidget(btn)
        bar_layout.addStretch(1)
        self.selection_count = QLabel("0 selected")
        bar_layout.addWidget(self.selection_count)
        triage_bar_vlay.addLayout(bar_layout)
        self.triage_bar.hide()
        rlay.addWidget(self.triage_bar)

        splitter.addWidget(right)
        splitter.setSizes([300, 10000])
        self.search_btn.clicked.connect(self.run_search)
        self.random_btn.clicked.connect(self.run_random)
        self.clear_query_btn.clicked.connect(self.clear_query)
        self.deselect_btn.clicked.connect(self.grid.deselect_all)
        self.archive_btn.clicked.connect(lambda: self._request_triage(OP_ARCHIVE, source="sidebar"))
        self.delete_btn.clicked.connect(lambda: self._request_triage(OP_DELETE, source="sidebar"))
        defer_btn.clicked.connect(lambda: self._request_triage(OP_DEFER, source="sidebar"))
        skip_btn.clicked.connect(lambda: self._request_triage(OP_SKIP, source="sidebar"))
        self.size_spin.valueChanged.connect(self.grid.set_icon_size)
        self.bucket_combo.currentTextChanged.connect(self._update_button_states)
        self.grid.itemSelectionChanged.connect(self._on_selection_changed)
        self.grid.search_with_image.connect(self.search_with_image)
        self.grid.search_with_selection.connect(self.search_with_selection)
        self.grid.add_to_negative.connect(self._on_add_negative)
        self.grid.triage.connect(lambda op, hs: self._request_triage(op, hs, "sidebar"))
        self.grid.open_externally.connect(self._open_externally)
        self.grid.copy_path.connect(self._copy_path)
        self.grid.open_containing_folder.connect(self._open_folder)
        self.dups_btn.clicked.connect(self._run_dedup)
        self.sort_combo.currentIndexChanged.connect(self._apply_sort)
        self.bar_confirm_yes.clicked.connect(self._confirm_triage)
        self.bar_confirm_no.clicked.connect(self._cancel_confirm)
        self.sidebar_confirm_yes.clicked.connect(self._confirm_triage)
        self.sidebar_confirm_no.clicked.connect(self._cancel_confirm)
        self._pending_triage = None
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        for keys, fn in [
            ("Ctrl+Return", self.run_search),
            ("Ctrl+Enter", self.run_search),
            ("Ctrl+R", self.run_random),
            ("Ctrl+L", self.clear_query),
        ]:
            act = QAction(self)
            act.setShortcut(QKeySequence(keys))
            act.triggered.connect(fn)
            self.addAction(act)
        for key, op in [("A", OP_ARCHIVE), ("D", OP_DELETE),
                        ("S", OP_SKIP), ("F", OP_DEFER)]:
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(lambda _, c=op: self._request_triage(c, source="bar"))
            self.addAction(act)

    # ----------------------------------------------------------- buckets
    def refresh_buckets(self, current: str | None = None) -> None:
        names = self._db.list_buckets()
        self.bucket_combo.blockSignals(True)
        self.bucket_combo.clear()
        self.bucket_combo.addItems(names)
        if current and current in names:
            self.bucket_combo.setCurrentText(current)
        elif names:
            self.bucket_combo.setCurrentIndex(0)
        self.bucket_combo.blockSignals(False)
        self._update_button_states()

    def on_model_state_changed(self) -> None:
        loaded = self._clip.is_loaded
        self.text_input.setEnabled(loaded)
        self.text_sign.setEnabled(loaded)
        self.text_weight.setEnabled(loaded)
        self._update_button_states()

    # ----------------------------------------------------------- query
    def search_with_image(self, hash_: str) -> None:
        self._query_hash = hash_
        self._query_bytes = None
        self._query_embedding = None
        bucket = self.bucket_combo.currentText()
        if bucket and not self._db.has_hash(bucket, hash_):
            # Not in the target bucket; we'll need raw bytes to embed.
            if self._clip.is_loaded:
                try:
                    self._query_bytes = self._hydrus.get_file(hash_)
                except Exception:
                    self._query_bytes = None
        else:
            # Cache the embedding so searches still work if the hash is
            # later triaged (deleted/archived) from the bucket.
            self._query_embedding = self._db.get_embedding(bucket, hash_)
        try:
            data = self._hydrus.get_thumbnail(hash_)
            self.query_thumb.set_image(data, hash_)
        except Exception:
            self.query_thumb.clear_image()
            self.query_thumb.setText(hash_[:12] + "…")
        self.run_search()

    def search_with_selection(self, hashes: list[str]) -> None:
        if not hashes:
            return
        if len(hashes) == 1:
            self.search_with_image(hashes[0])
            return
        self._query_hash = None
        self._query_bytes = None
        self._query_embedding = None
        self.query_thumb.clear_image()
        self.query_thumb.setText(f"{len(hashes)} images (averaged)")
        self.run_search(multi=hashes)

    def _on_query_cleared(self) -> None:
        self._query_hash = None
        self._query_bytes = None
        self._query_embedding = None

    def _on_negative_cleared(self) -> None:
        self._negative_hash = None

    def _on_add_negative(self, hash_: str) -> None:
        self._negative_hash = hash_
        try:
            data = self._hydrus.get_thumbnail(hash_)
            self.negative_thumb.set_image(data, hash_)
        except Exception:
            pass

    def clear_query(self) -> None:
        self._query_hash = None
        self._query_bytes = None
        self._query_embedding = None
        self._negative_hash = None
        self.query_thumb.clear_image()
        self.negative_thumb.clear_image()
        self.text_input.clear()
        self.grid.deselect_all()

    # ----------------------------------------------------------- search history
    def _push_history(self) -> None:
        entry = (
            self._query_hash,
            self.text_input.text().strip(),
            self.text_weight.value(),
            self.text_sign.isChecked(),
        )
        # Drop any forward history when a new search is performed.
        if self._history_idx >= 0 and self._history_idx < len(self._history) - 1:
            del self._history[self._history_idx + 1:]
        if self._history and self._history[-1] == entry:
            return
        self._history.append(entry)
        self._history_idx = len(self._history) - 1

    def go_back(self) -> None:
        if self._history_idx <= 0:
            return
        self._history_idx -= 1
        self._restore_history_entry(self._history[self._history_idx])

    def go_forward(self) -> None:
        if self._history_idx >= len(self._history) - 1:
            return
        self._history_idx += 1
        self._restore_history_entry(self._history[self._history_idx])

    def _restore_history_entry(self, entry) -> None:
        query_hash, text, weight, negative = entry
        self._suppress_history = True
        try:
            self._query_hash = query_hash
            self._query_bytes = None
            self._query_embedding = None
            bucket = self.bucket_combo.currentText()
            if bucket and query_hash and self._db.has_hash(bucket, query_hash):
                self._query_embedding = self._db.get_embedding(bucket, query_hash)
            self.text_input.setText(text)
            self.text_weight.setValue(weight)
            self.text_sign.setChecked(negative)
            if query_hash:
                try:
                    data = self._hydrus.get_thumbnail(query_hash)
                    self.query_thumb.set_image(data, query_hash)
                except Exception:
                    self.query_thumb.clear_image()
            else:
                self.query_thumb.clear_image()
            self.run_search()
        finally:
            self._suppress_history = False

    # ----------------------------------------------------------- search
    def run_search(self, multi: list[str] | None = None) -> None:
        bucket = self.bucket_combo.currentText()
        if not bucket:
            QMessageBox.warning(self, "No bucket", "Select a bucket first.")
            return
        k = self.results_spin.value()
        text = self.text_input.text().strip()
        weight = self.text_weight.value()
        negative = self.text_sign.isChecked()
        if not self._suppress_history and multi is None:
            self._push_history()
        if self._search_worker is not None:
            self._search_worker.cancel()
        self.result_label.setText("0 results")
        self.grid.clear_all()
        self.status_message.emit(f"Searching {bucket!r}…")
        self._search_worker = SearchWorker(
            self._db, self._clip, bucket, k,
            query_hash=self._query_hash,
            query_bytes=self._query_bytes,
            query_embedding=self._query_embedding,
            text=text, text_weight=weight, text_negative=negative,
            negative_hash=self._negative_hash,
            multi_hashes=multi,
        )
        self._search_worker.incremental_results.connect(self._on_incremental)
        self._search_worker.results.connect(self._on_results)
        self._search_worker.random_results.connect(self._on_random)
        self._search_worker.failed.connect(self._on_search_failed)
        self._search_worker.start()

    def run_random(self) -> None:
        bucket = self.bucket_combo.currentText()
        if not bucket:
            return
        self._query_hash = None
        self._query_bytes = None
        self._query_embedding = None
        self.query_thumb.clear_image()
        if self._search_worker is not None:
            self._search_worker.cancel()
        self.grid.clear_all()
        k = self.results_spin.value()
        self._search_worker = SearchWorker(
            self._db, self._clip, bucket, k, random=True,
        )
        self._search_worker.random_results.connect(self._on_random)
        self._search_worker.failed.connect(self._on_search_failed)
        self._search_worker.start()

    def _on_incremental(self, pairs: list) -> None:
        self.grid.add_results(pairs)
        self.result_label.setText(f"{self.grid.count()} results from {self.bucket_combo.currentText()!r}")
        self.status_message.emit(f"{self.grid.count()} results loaded…")

    def _on_results(self, results: list) -> None:
        dist = {h: d for h, d in results}
        if not self.grid.count():
            self.grid.set_hashes([h for h, _ in results], distances=dist)
        else:
            self.grid.apply_distances(dist)
        self.result_label.setText(
            f"{self.grid.count()} results from {self.bucket_combo.currentText()!r}"
        )
        self.status_message.emit(f"Search complete: {len(results)} results")

    def _on_random(self, hashes: list) -> None:
        self.grid.set_hashes(hashes)
        self.result_label.setText(f"{len(hashes)} random samples from {self.bucket_combo.currentText()!r}")

    def _on_search_failed(self, msg: str) -> None:
        self._finalize_worker("_search_worker")
        self.status_message.emit(f"Search failed: {msg}")
        QMessageBox.warning(self, "Search failed", msg)

    def _apply_sort(self) -> None:
        mode = self.sort_combo.currentIndex()
        hashes = self.grid.all_hashes()
        distances = self.grid.current_distances()
        if mode == 0:
            pass
        elif mode == 1:
            hashes = list(reversed(hashes))
        else:
            import random
            random.shuffle(hashes)
        self.grid.set_hashes(hashes, distances=distances)

    # ----------------------------------------------------------- triage
    def _on_selection_changed(self) -> None:
        n = len(self.grid.selectedItems())
        if n > 0:
            self.triage_bar.show()
            self.selection_count.setText(f"{n} selected")
        else:
            self.triage_bar.hide()
            self._cancel_confirm()

    _OP_NAMES = {OP_ARCHIVE: "Archive", OP_DELETE: "Delete",
                 OP_SKIP: "Skip", OP_DEFER: "Defer"}

    def _request_triage(self, operation: int, hashes: list[str] | None = None,
                        source: str = "sidebar") -> None:
        if hashes is None:
            hashes = self.grid.selected_hashes()
        if not hashes:
            return
        bucket = self.bucket_combo.currentText()
        if not bucket:
            return
        if operation == OP_DEFER and not self._config.hydrus_tag_service_key:
            QMessageBox.warning(self, "Defer unavailable",
                                "Configure a tag service key in Preferences.")
            return
        name = self._OP_NAMES[operation]
        self._pending_triage = (operation, hashes, bucket)
        if not self._config.confirm_triage:
            self._confirm_triage()
            return
        label_text = f"{name} {len(hashes)} images?"
        if source == "bar":
            self.sidebar_confirm.hide()
            self.bar_confirm_label.setText(label_text)
            self.bar_confirm.show()
        else:
            self.bar_confirm.hide()
            self.sidebar_confirm_label.setText(label_text)
            self.sidebar_confirm.show()

    def _cancel_confirm(self) -> None:
        self._pending_triage = None
        self.bar_confirm.hide()
        self.sidebar_confirm.hide()

    def _confirm_triage(self) -> None:
        pending = getattr(self, "_pending_triage", None)
        self._cancel_confirm()
        if pending is None:
            return
        operation, hashes, bucket = pending
        if self._triage_worker is not None:
            self._triage_worker.cancel()
        self._triage_worker = HydrusOperationWorker(
            self._db, self._hydrus, bucket, operation, hashes,
            tag_service_key=self._config.hydrus_tag_service_key,
        )
        self._triage_worker.done.connect(self._on_triage_done)
        self._triage_worker.failed.connect(self._on_triage_failed)
        self._triage_worker.start()

    def _on_triage_done(self, operation: int, hashes: list) -> None:
        for h in hashes:
            self.grid.remove_hash(h)
        self._finalize_worker("_triage_worker")
        self.result_label.setText(
            f"{self.grid.count()} results from {self.bucket_combo.currentText()!r}"
        )
        self.buckets_changed.emit()

    def _on_triage_failed(self, operation: int, hashes: list, msg: str) -> None:
        self._finalize_worker("_triage_worker")
        QMessageBox.critical(self, "Triage failed", msg)

    # ----------------------------------------------------------- dedup
    def _run_dedup(self) -> None:
        bucket = self.bucket_combo.currentText()
        if not bucket:
            return
        if self._dedup_worker is not None:
            from .widgets import Toast
            Toast("Duplicate scan already running…", self, auto_ms=2000, confirm=False)
            return
        threshold, ok = self._ask_double(
            "Duplicate threshold", "Max cosine distance:", 0.05, 0.0, 1.0
        )
        if not ok:
            return
        self._dedup_worker = DedupWorker(self._db, bucket, threshold)
        self._dedup_worker.results.connect(self._on_dedup_results)
        self._dedup_worker.failed.connect(self._on_dedup_failed)
        self._dedup_worker.start()

    def _ask_double(self, title, label, default, lo, hi):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getDouble(self, title, label, default, lo, hi, 4)

    def _on_dedup_results(self, pairs: list) -> None:
        self._finalize_worker("_dedup_worker")
        self.grid.set_duplicates(pairs)
        from .widgets import Toast
        if pairs:
            Toast(f"{len(pairs)} duplicate pairs highlighted.", self, auto_ms=3000, confirm=False)
        else:
            Toast("No duplicates found.", self, auto_ms=3000, confirm=False)

    def _on_dedup_failed(self, msg: str) -> None:
        self._finalize_worker("_dedup_worker")
        QMessageBox.warning(self, "Dedup failed", msg)

    # ----------------------------------------------------------- external
    def _open_externally(self, hash_: str) -> None:
        path = self._hydrus.get_file_path(hash_)
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Not available", "Local file path unavailable.")
            return
        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "Open failed", str(exc))

    def _copy_path(self, hash_: str) -> None:
        from PySide6.QtWidgets import QApplication
        path = self._hydrus.get_file_path(hash_) or ""
        QApplication.clipboard().setText(path)

    def _open_folder(self, hash_: str) -> None:
        path = self._hydrus.get_file_path(hash_)
        if not path or not os.path.exists(path):
            return
        folder = os.path.dirname(path)
        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", folder])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["explorer", folder])
        except Exception:
            pass

    # ----------------------------------------------------------- state
    def _update_button_states(self) -> None:
        bucket = bool(self.bucket_combo.currentText())
        self.search_btn.setEnabled(bucket)
        self.random_btn.setEnabled(bucket)

    def _finalize_worker(self, attr: str) -> None:
        worker = getattr(self, attr, None)
        if worker is None:
            return
        try:
            worker.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        setattr(self, attr, None)

    def cleanup(self) -> None:
        for attr in ("_search_worker", "_triage_worker", "_dedup_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                w.cancel()
                w.wait(2000)
        # Grid thumbnail loaders.
        self.grid.cleanup()

    def on_theme_changed(self) -> None:
        # Refresh inline-styled widgets if any; grid uses QSS.
        pass
