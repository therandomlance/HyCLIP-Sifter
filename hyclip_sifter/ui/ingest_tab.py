"""Ingest tab: bucket management, model loading, Hydrus check, ingest queue."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTextEdit, QListWidget, QListWidgetItem, QProgressBar,
    QInputDialog, QMessageBox, QSplitter, QFormLayout,
)

from ..database import Database
from ..clip_model import ClipModel
from ..hydrus_service import HydrusService
from ..workers import IngestWorker, ModelLoadWorker, HydrusCheckWorker
from .widgets import section, count_badge


QUEUE_PATH = Path("hyclip_sifter.queue.json")


class IngestTab(QWidget):
    buckets_changed = Signal()
    model_state_changed = Signal()
    status_message = Signal(str)

    def __init__(self, config, db: Database, clip: ClipModel, hydrus: HydrusService,
                 thumb_cache, parent=None):
        super().__init__(parent)
        self._config = config
        self._db = db
        self._clip = clip
        self._hydrus = hydrus
        self._cache = thumb_cache
        self._load_worker: ModelLoadWorker | None = None
        self._check_worker: HydrusCheckWorker | None = None
        self._ingest_worker: IngestWorker | None = None
        self._queue: list[dict] = []  # each: {bucket, hashes, done, total}
        self._build_ui()
        self.refresh_buckets()
        self._update_model_status()
        self._update_button_states()
        self._load_queue()

    # ----------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ---- Top section: two-column layout ----
        top = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(top, 1)

        # Buckets column.
        bucket_frame, bucket_layout = section("Buckets")
        bucket_frame.setMaximumWidth(320)
        bucket_frame.setMinimumWidth(220)
        row = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.delete_btn = QPushButton("Delete")
        self.rename_btn = QPushButton("Rename")
        row.addWidget(self.new_btn)
        row.addWidget(self.delete_btn)
        row.addWidget(self.rename_btn)
        bucket_layout.addLayout(row)
        self.bucket_combo = QComboBox()
        bucket_layout.addWidget(self.bucket_combo)
        self.badges_layout = QVBoxLayout()
        self.badges_layout.setSpacing(4)
        self.badges_widget = QWidget()
        self.badges_widget.setLayout(self.badges_layout)
        bucket_layout.addWidget(self.badges_widget)
        self.copy_move_btn = QPushButton("Copy / Move between buckets…")
        bucket_layout.addWidget(self.copy_move_btn)
        self.clear_cache_btn = QPushButton("Clear thumbnail cache")
        bucket_layout.addWidget(self.clear_cache_btn)
        bucket_layout.addStretch(1)
        top.addWidget(bucket_frame)

        # Add-to-Bucket column.
        add_frame, add_layout = section("Add to Bucket")
        self.hash_area = QTextEdit()
        self.hash_area.setPlaceholderText("Paste SHA256 hashes (one per line)…")
        self.hash_area.setAcceptDrops(True)
        add_layout.addWidget(self.hash_area, 1)
        btn_row = QHBoxLayout()
        self.paste_btn = QPushButton("Paste")
        self.clear_btn = QPushButton("Clear")
        self.import_btn = QPushButton("Import from Hydrus search…")
        btn_row.addWidget(self.paste_btn)
        btn_row.addWidget(self.clear_btn)
        btn_row.addWidget(self.import_btn)
        add_layout.addLayout(btn_row)
        self.ingest_btn = QPushButton("Start Ingest")
        self.ingest_btn.setObjectName("PrimaryBtn")
        add_layout.addWidget(self.ingest_btn)
        top.addWidget(add_frame)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 2)

        # ---- Middle: ingest queue ----
        queue_frame, queue_layout = section("Ingest Queue")
        self.queue_header = QLabel("0 jobs remaining")
        queue_layout.addWidget(self.queue_header)
        self.queue_list = QListWidget()
        self.queue_list.setMinimumHeight(120)
        queue_layout.addWidget(self.queue_list, 1)
        qrow = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Paused")
        self.pause_btn.setCheckable(True)
        self.clear_queue_btn = QPushButton("Clear")
        self.save_queue_btn = QPushButton("Save Queue…")
        qrow.addWidget(self.pause_btn)
        qrow.addWidget(self.clear_queue_btn)
        qrow.addWidget(self.save_queue_btn)
        qrow.addStretch(1)
        queue_layout.addLayout(qrow)
        outer.addWidget(queue_frame)

        # ---- Bottom: setup ----
        setup_frame, setup_layout = section("Setup")
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_status = QLabel("Model not loaded")
        model_row.addWidget(self.model_status, 1)
        self.load_btn = QPushButton("Load Model")
        self.eject_btn = QPushButton("Eject")
        model_row.addWidget(self.load_btn)
        model_row.addWidget(self.eject_btn)
        setup_layout.addLayout(model_row)
        hydrus_row = QHBoxLayout()
        hydrus_row.addWidget(QLabel("Hydrus:"))
        self.hydrus_status = QLabel("Not checked")
        hydrus_row.addWidget(self.hydrus_status, 1)
        self.test_btn = QPushButton("Test Hydrus API")
        hydrus_row.addWidget(self.test_btn)
        setup_layout.addLayout(hydrus_row)
        outer.addWidget(setup_frame)

        # ---- wiring ----
        self.new_btn.clicked.connect(self.new_bucket)
        self.delete_btn.clicked.connect(self.delete_bucket)
        self.rename_btn.clicked.connect(self.rename_bucket)
        self.copy_move_btn.clicked.connect(self.copy_move)
        self.clear_cache_btn.clicked.connect(self.clear_cache)
        self.paste_btn.clicked.connect(self.paste_hashes)
        self.clear_btn.clicked.connect(self.hash_area.clear)
        self.import_btn.clicked.connect(self.import_from_hydrus)
        self.ingest_btn.clicked.connect(self.start_ingest)
        self.load_btn.clicked.connect(self.load_model)
        self.eject_btn.clicked.connect(self.eject_model)
        self.test_btn.clicked.connect(self.test_hydrus)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        self.clear_queue_btn.clicked.connect(self.clear_queue)
        self.save_queue_btn.clicked.connect(self.save_queue)
        self.bucket_combo.currentTextChanged.connect(self._update_badges)

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
        self._update_badges()
        self._update_button_states()

    def _current_bucket(self) -> str | None:
        return self.bucket_combo.currentText() or None

    def new_bucket(self) -> None:
        name, ok = QInputDialog.getText(self, "New Bucket", "Bucket name:")
        if not ok or not name:
            return
        if not self._clip.is_loaded:
            QMessageBox.warning(self, "Model not loaded",
                                "Load the CLIP model first to determine the embedding dimension.")
            return
        dim = self._clip.dimension
        if dim is None:
            QMessageBox.warning(self, "Model error", "Cannot determine embedding dimension.")
            return
        try:
            self._db.create_bucket(name.strip(), int(dim))
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        self.refresh_buckets(current=name.strip())
        self.buckets_changed.emit()

    def delete_bucket(self) -> None:
        b = self._current_bucket()
        if not b:
            return
        if QMessageBox.question(self, "Delete bucket",
                                f"Delete bucket {b!r}? History is preserved.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_bucket(b)
        self.refresh_buckets()
        self.buckets_changed.emit()

    def rename_bucket(self) -> None:
        b = self._current_bucket()
        if not b:
            return
        new, ok = QInputDialog.getText(self, "Rename Bucket", "New name:", text=b)
        if not ok or not new.strip() or new.strip() == b:
            return
        try:
            self._db.rename_bucket(b, new.strip())
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        self.refresh_buckets(current=new.strip())
        self.buckets_changed.emit()

    def copy_move(self) -> None:
        names = self._db.list_buckets()
        if len(names) < 2:
            QMessageBox.information(self, "Copy / Move", "Need at least two buckets.")
            return
        src, ok = QInputDialog.getItem(self, "Copy / Move", "Source bucket:", names, 0, False)
        if not ok:
            return
        rest = [n for n in names if n != src]
        dst, ok = QInputDialog.getItem(self, "Copy / Move", "Destination bucket:", rest, 0, False)
        if not ok:
            return
        try:
            dim_s = self._db.bucket_dimension(src)
            dim_d = self._db.bucket_dimension(dst)
        except KeyError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        if dim_s != dim_d:
            QMessageBox.warning(self, "Dimensions differ",
                                f"{src} ({dim_s}d) and {dst} ({dim_d}d) are incompatible.")
            return
        mode, ok = QInputDialog.getItem(self, "Copy / Move",
                                        "Mode:", ["Copy", "Move"], 0, False)
        if not ok:
            return
        hashes = self._db.random_sample(src, 10_000)
        moved = 0
        for h in hashes:
            emb = self._db.get_embedding(src, h)
            if emb is None:
                continue
            self._db.add_embedding(dst, h, emb)
            if mode == "Move":
                # Moved hashes are relocated, not triaged — don't pollute history.
                self._db.remove_from_bucket_silent(src, h)
            moved += 1
        QMessageBox.information(self, "Done", f"{mode}d {moved} hashes to {dst}.")
        self.refresh_buckets()

    def _update_badges(self) -> None:
        # Clear existing.
        while self.badges_layout.count():
            w = self.badges_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        b = self._current_bucket()
        if not b:
            return
        try:
            in_bucket = self._db.bucket_count(b)
            counts = self._db.history_counts(b)
        except Exception:
            return
        labels = [
            (f"{in_bucket} in bucket", "primary"),
            (f"{counts.get(1, 0)} archived", "archive"),
            (f"{counts.get(0, 0)} deleted", "delete"),
            (f"{counts.get(2, 0)} skipped", "skip"),
            (f"{counts.get(3, 0)} deferred", "defer"),
        ]
        for text, role in labels:
            self.badges_layout.addWidget(count_badge(text, role))

    # ----------------------------------------------------------- CLIP model
    def load_model(self) -> None:
        if self._clip.is_loaded or self._load_worker is not None:
            return
        self.model_status.setText("Loading model…")
        self.load_btn.setEnabled(False)
        self._load_worker = ModelLoadWorker(self._clip)
        self._load_worker.download_progress.connect(self._on_model_download_progress)
        self._load_worker.loaded.connect(self._on_model_loaded)
        self._load_worker.failed.connect(self._on_model_failed)
        self._load_worker.start()

    def _on_model_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.model_status.setText(f"Loading model… {done}/{total}")
        else:
            self.model_status.setText("Loading model… (preparing)")

    def _on_model_loaded(self, name: str, device: str) -> None:
        self._finalize_worker("_load_worker")
        self._update_model_status()
        self.model_state_changed.emit()
        self._update_button_states()

    def _on_model_failed(self, msg: str) -> None:
        self._finalize_worker("_load_worker")
        self.model_status.setText("Model load failed")
        QMessageBox.critical(self, "Model load failed", msg)
        self._update_button_states()

    def eject_model(self) -> None:
        if not self._clip.is_loaded:
            return
        self._clip.eject()
        self._update_model_status()
        self.model_state_changed.emit()
        self._update_button_states()

    def _update_model_status(self) -> None:
        if self._clip.is_loaded:
            self.model_status.setText(
                f"Loaded: {self._clip.loaded_name} ({self._clip.device})"
            )
        else:
            self.model_status.setText("Model not loaded")

    # ----------------------------------------------------------- Hydrus test
    def test_hydrus(self) -> None:
        if self._check_worker is not None:
            return
        self.hydrus_status.setText("Testing…")
        self._check_worker = HydrusCheckWorker(
            self._hydrus, self._config.hydrus_tag_service_key
        )
        self._check_worker.ok.connect(self._on_hydrus_ok)
        self._check_worker.failed.connect(self._on_hydrus_failed)
        self._check_worker.start()

    def _on_hydrus_ok(self, msg: str) -> None:
        self._finalize_worker("_check_worker")
        self.hydrus_status.setText(f"✓ Connected — {msg}")

    def _on_hydrus_failed(self, msg: str) -> None:
        self._finalize_worker("_check_worker")
        self.hydrus_status.setText(f"✗ {msg}")

    # ----------------------------------------------------------- ingest
    def paste_hashes(self) -> None:
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if not text:
            return
        existing = {line.strip() for line in self.hash_area.toPlainText().splitlines() if line.strip()}
        new_lines = [ln for ln in text.splitlines() if ln.strip() and ln.strip() not in existing]
        if not new_lines:
            return
        current = self.hash_area.toPlainText()
        sep = "\n" if current and not current.endswith("\n") else ""
        self.hash_area.append(sep + "\n".join(new_lines))

    def import_from_hydrus(self) -> None:
        query, ok = QInputDialog.getText(
            self, "Import from Hydrus search",
            "Enter a Hydrus tag query (tags separated by spaces,\n"
            "quote multi-word tags with double quotes):"
        )
        if not ok or not query.strip():
            return
        tags = self._split_tag_query(query.strip())
        if not tags:
            return
        try:
            hashes = self._hydrus.search_files(tags)
        except Exception as exc:
            QMessageBox.critical(self, "Hydrus search failed", str(exc))
            return
        if not hashes:
            QMessageBox.information(self, "No results", "Hydrus returned no hashes.")
            return
        self.hash_area.append("\n".join(hashes))

    @staticmethod
    def _split_tag_query(query: str) -> list[str]:
        """Split a tag query on whitespace, honouring double-quoted multi-word tags."""
        import shlex
        try:
            return shlex.split(query, posix=True)
        except ValueError:
            # Unbalanced quotes — fall back to a naive split.
            return [t for t in query.split() if t]

    def start_ingest(self) -> None:
        bucket = self._current_bucket()
        if not bucket:
            QMessageBox.warning(self, "No bucket", "Select a bucket first.")
            return
        if not self._clip.is_loaded:
            QMessageBox.warning(self, "Model not loaded", "Load the CLIP model first.")
            return
        raw = self.hash_area.toPlainText().split()
        hashes = [h.strip() for h in raw if h.strip()]
        if not hashes:
            return
        self._queue.append({"bucket": bucket, "hashes": hashes,
                            "done": 0, "total": len(hashes)})
        self.hash_area.clear()
        self._save_queue()
        self._refresh_queue_list()
        self._maybe_start_next()

    def _maybe_start_next(self) -> None:
        if self._ingest_worker is not None:
            return
        if self.pause_btn.isChecked():
            return
        if not self._queue:
            return
        job = self._queue[0]
        done = job.get("done", 0)
        total = len(job["hashes"])
        if done > 0 and done < total:
            # Partially completed job from a previous run — resume the
            # remaining hashes instead of discarding the whole job.
            remaining = list(job["hashes"][done:])
            job["hashes"] = job["hashes"][:done] + remaining
        elif done >= total:
            # Already fully complete — drop it.
            self._queue.pop(0)
            self._refresh_queue_list()
            self._maybe_start_next()
            return
        batch = self._config.ingest_batch_size
        if batch <= 0:
            import torch
            batch = 8 if torch.cuda.is_available() else 1
        hashes_to_run = job["hashes"][done:]
        job["_offset"] = done
        self._ingest_worker = IngestWorker(
            self._db, self._clip, self._hydrus, job["bucket"],
            hashes_to_run, batch_size=batch,
        )
        self._ingest_worker.progress.connect(self._on_ingest_progress)
        self._ingest_worker.log.connect(self._on_ingest_log)
        self._ingest_worker.finished_ok.connect(self._on_ingest_done)
        self._ingest_worker.failed.connect(self._on_ingest_failed)
        self._ingest_worker.start()
        self._refresh_queue_list()

    def _on_ingest_progress(self, done: int, total: int, msg: str) -> None:
        if not self._queue:
            return
        job = self._queue[0]
        offset = job.get("_offset", 0)
        job["done"] = offset + done
        job["total"] = offset + total
        self._refresh_queue_list()
        self.status_message.emit(f"Ingesting {job['bucket']}: {msg}")

    def _on_ingest_log(self, msg: str) -> None:
        self.status_message.emit(f"Ingest: {msg}")

    def _on_ingest_done(self, count: int) -> None:
        if self._queue:
            self._queue.pop(0)
        self._finalize_worker("_ingest_worker")
        self._save_queue()
        self._refresh_queue_list()
        self.status_message.emit(f"Ingest complete: {count} embedded")
        self.buckets_changed.emit()
        self._maybe_start_next()

    def _on_ingest_failed(self, msg: str) -> None:
        if self._queue:
            self._queue.pop(0)
        self._finalize_worker("_ingest_worker")
        self._save_queue()
        self._refresh_queue_list()
        self.status_message.emit(f"Ingest failed: {msg}")
        QMessageBox.critical(self, "Ingest failed", msg)
        self._maybe_start_next()

    def _refresh_queue_list(self) -> None:
        self.queue_list.clear()
        active = self._ingest_worker is not None
        for i, job in enumerate(self._queue):
            done = job.get("done", 0)
            total = job.get("total", len(job["hashes"]))
            pct = int(done / total * 100) if total else 0
            prefix = "▶ " if (active and i == 0) else "⏳ "
            widget = QWidget()
            wlay = QVBoxLayout(widget)
            wlay.setContentsMargins(4, 4, 4, 4)
            wlay.setSpacing(2)
            label = QLabel(f"{prefix}{job['bucket']}  —  {done}/{total} files")
            bar = QProgressBar()
            bar.setValue(pct)
            bar.setFormat(f"{pct}%")
            wlay.addWidget(label)
            wlay.addWidget(bar)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.queue_list.addItem(item)
            self.queue_list.setItemWidget(item, widget)
        remaining = len(self._queue) - (1 if active else 0)
        self.queue_header.setText(f"{max(0, remaining)} jobs remaining")
        self.clear_queue_btn.setEnabled(not active)

    def _on_pause_toggled(self, paused: bool) -> None:
        self.pause_btn.setText("▶ Running" if not paused else "⏸ Paused")
        if not paused:
            self._maybe_start_next()

    def clear_queue(self) -> None:
        self._queue.clear()
        self._save_queue()
        self._refresh_queue_list()

    def save_queue(self) -> None:
        self._save_queue()

    def _save_queue(self) -> None:
        try:
            with open(QUEUE_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._queue, fh)
        except OSError:
            pass

    def _load_queue(self) -> None:
        if not QUEUE_PATH.exists():
            return
        try:
            with open(QUEUE_PATH, "r", encoding="utf-8") as fh:
                self._queue = json.load(fh)
        except (OSError, json.JSONDecodeError):
            self._queue = []
        self._refresh_queue_list()

    def clear_cache(self) -> None:
        n = self._cache.clear()
        QMessageBox.information(self, "Cache cleared", f"Removed {n} cached thumbnails.")

    # ----------------------------------------------------------- state
    def _update_button_states(self) -> None:
        ready = self._clip.is_loaded and self._current_bucket() is not None
        self.hash_area.setEnabled(ready)
        self.paste_btn.setEnabled(ready)
        self.clear_btn.setEnabled(ready)
        self.import_btn.setEnabled(ready)
        self.ingest_btn.setEnabled(ready)
        self.delete_btn.setEnabled(not self._queue and self._current_bucket() is not None)
        self.load_btn.setEnabled(not self._clip.is_loaded and self._load_worker is None)
        self.eject_btn.setEnabled(self._clip.is_loaded)

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
        for attr in ("_load_worker", "_check_worker", "_ingest_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                w.cancel()
                w.wait(2000)
                self._finalize_worker(attr)
        self._save_queue()

    def on_theme_changed(self) -> None:
        # Rebuild badges so inline-styled count_badge widgets pick up changes.
        self._update_badges()
