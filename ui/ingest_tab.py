import json
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFrame,
    QApplication,
)

from clip_model import ClipModel, model_dimension
from config import Config
from database import Database, valid_bucket_name
from hydrus_service import HydrusService
from workers import HydrusCheckWorker, IngestWorker, ModelLoadWorker

QUEUE_FILE = "hyclip_sifter.queue.json"


class IngestTab(QWidget):
    buckets_changed = Signal()
    model_state_changed = Signal()

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
        self.ingest_worker: IngestWorker | None = None
        self.model_worker: ModelLoadWorker | None = None
        self.hydrus_worker: HydrusCheckWorker | None = None
        self._queue: list[dict] = []
        self._current_job: dict | None = None
        self._paused: bool = False
        self._queue_path = Path(QUEUE_FILE)
        self._load_queue()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(self._build_bucket_group())
        layout.addWidget(self._build_ingest_group())
        layout.addWidget(self._build_model_group())
        layout.addWidget(self._build_hydrus_group())
        layout.addStretch(1)

    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(frame)
        v.addWidget(QLabel(f"<b>{title}</b>"))
        return frame, v

    def _build_bucket_group(self) -> QWidget:
        frame, v = self._section("Buckets")
        row = QHBoxLayout()
        self.new_btn = QPushButton("New Bucket")
        self.new_btn.clicked.connect(self._new_bucket)
        row.addWidget(self.new_btn)
        self.delete_bucket_btn = QPushButton("Delete Bucket")
        self.delete_bucket_btn.clicked.connect(self._delete_bucket)
        row.addWidget(self.delete_bucket_btn)
        v.addLayout(row)
        self.bucket_combo = QComboBox()
        self.bucket_combo.currentIndexChanged.connect(self._update_bucket_counts)
        v.addWidget(self.bucket_combo)
        self.bucket_counts_label = QLabel("")
        v.addWidget(self.bucket_counts_label)
        return frame

    def _build_ingest_group(self) -> QWidget:
        frame, v = self._section("Add to Bucket")
        columns = QHBoxLayout()

        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        self.hash_edit = QTextEdit()
        self.hash_edit.setPlaceholderText("Paste newline-separated sha256 hashes here, or use the button below...")
        self.hash_edit.setMinimumHeight(120)
        left_v.addWidget(self.hash_edit)
        row = QHBoxLayout()
        self.paste_btn = QPushButton("Paste from Clipboard")
        self.paste_btn.clicked.connect(self._paste_clipboard)
        row.addWidget(self.paste_btn)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.hash_edit.clear)
        row.addWidget(self.clear_btn)
        left_v.addLayout(row)
        self.ingest_btn = QPushButton("Start Ingest")
        self.ingest_btn.setStyleSheet("background-color: #1565c0; color: white;")
        self.ingest_btn.clicked.connect(self._start_ingest)
        left_v.addWidget(self.ingest_btn)
        columns.addWidget(left, 1)

        columns.addWidget(self._build_queue_pane())
        v.addLayout(columns)
        return frame

    def _build_queue_pane(self) -> QWidget:
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        pane.setFixedWidth(240)
        v = QVBoxLayout(pane)
        v.addWidget(QLabel("<b>Ingest Queue</b>"))
        self.queue_list = QListWidget()
        self.queue_list.setSelectionMode(QListWidget.NoSelection)
        v.addWidget(self.queue_list)
        row = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        row.addWidget(self.pause_btn)
        self.clear_queue_btn = QPushButton("Clear")
        self.clear_queue_btn.clicked.connect(self._clear_queue)
        row.addWidget(self.clear_queue_btn)
        v.addLayout(row)
        self._render_queue()
        return pane

    def _build_model_group(self) -> QWidget:
        frame, v = self._section("CLIP Model")
        self.model_status = QLabel("Model not loaded")
        v.addWidget(self.model_status)
        row = QHBoxLayout()
        self.load_btn = QPushButton("Load Model")
        self.load_btn.clicked.connect(self.load_model)
        row.addWidget(self.load_btn)
        self.eject_btn = QPushButton("Eject Model")
        self.eject_btn.clicked.connect(self._eject_model)
        self.eject_btn.setEnabled(False)
        row.addWidget(self.eject_btn)
        v.addLayout(row)
        v.addWidget(QLabel(f"Configured model: {self.config.clip_model}"))
        return frame

    def _build_hydrus_group(self) -> QWidget:
        frame, v = self._section("Hydrus API")
        self.test_hydrus_btn = QPushButton("Test Hydrus API")
        self.test_hydrus_btn.clicked.connect(self._test_hydrus)
        v.addWidget(self.test_hydrus_btn)
        return frame

    def refresh_buckets(self, select: str | None = None) -> None:
        if select is None:
            select = self.bucket_combo.currentText() or None
        buckets = self.db.list_buckets()
        self.bucket_combo.clear()
        for b in buckets:
            self.bucket_combo.addItem(b)
        if select and select in buckets:
            self.bucket_combo.setCurrentText(select)
        self._update_bucket_counts()
        self._refresh_actions_state()

    def _update_bucket_counts(self) -> None:
        bucket = self.bucket_combo.currentText()
        if not bucket:
            self.bucket_counts_label.setText("")
            return
        count = self.db.bucket_count(bucket)
        hist = self.db.history_counts(bucket)
        archived = hist.get(1, 0)
        deleted = hist.get(0, 0)
        skipped = hist.get(2, 0)
        deferred = hist.get(3, 0)
        self.bucket_counts_label.setText(
            f"In bucket: {count}  |  Archived: {archived}  |  Deleted: {deleted}  |  Skipped: {skipped}  |  Deferred: {deferred}"
        )

    def refresh_model_status(self) -> None:
        if self.clip.is_loaded:
            self.model_status.setText(
                f"Loaded: {self.clip.model_name} ({self.clip.device})"
            )
            self.load_btn.setEnabled(False)
            self.eject_btn.setEnabled(True)
        else:
            self.model_status.setText("Model not loaded")
            self.load_btn.setEnabled(True)
            self.eject_btn.setEnabled(False)
        self._refresh_actions_state()

    def _refresh_actions_state(self) -> None:
        has_selection = bool(self.bucket_combo.currentText())
        model_loaded = self.clip.is_loaded
        ingest_running = self.ingest_worker is not None and self.ingest_worker.isRunning()
        queue_active = ingest_running or bool(self._queue)
        can_ingest = has_selection and model_loaded
        self.ingest_btn.setEnabled(can_ingest)
        self.hash_edit.setEnabled(can_ingest)
        self.paste_btn.setEnabled(can_ingest)
        self.clear_btn.setEnabled(True)
        self.delete_bucket_btn.setEnabled(has_selection and not queue_active)
        self.clear_queue_btn.setEnabled(bool(self._queue) and not ingest_running)

    def _new_bucket(self) -> None:
        name, ok = QInputDialog.getText(self, "New Bucket", "Bucket name (no spaces):")
        if not ok:
            return
        name = name.strip()
        if not valid_bucket_name(name):
            QMessageBox.warning(self, "Invalid name", "Bucket name must be non-empty, contain no spaces, and use only letters, digits, _ or -.")
            return
        try:
            dim = model_dimension(self.config.clip_model)
        except Exception as exc:
            QMessageBox.warning(self, "Model error", f"Cannot resolve model dimension: {exc}")
            return
        try:
            self.db.create_bucket(name, dim)
        except Exception as exc:
            QMessageBox.warning(self, "Create bucket", str(exc))
            return
        self.refresh_buckets(select=name)
        self.buckets_changed.emit()
        self.set_status(f"Created bucket '{name}' (dim={dim}).")

    def _delete_bucket(self) -> None:
        name = self.bucket_combo.currentText()
        if not name:
            return
        if QMessageBox.question(
            self,
            "Delete bucket",
            f"Delete bucket '{name}' and all its embeddings? History is preserved.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.db.delete_bucket(name)
        self.refresh_buckets()
        self.buckets_changed.emit()
        self.set_status(f"Deleted bucket '{name}'.")

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        self.hash_edit.setPlainText(text)

    def _start_ingest(self) -> None:
        bucket = self.bucket_combo.currentText()
        if not bucket:
            QMessageBox.information(self, "Ingest", "Select or create a bucket first.")
            return
        if not self.clip.is_loaded:
            QMessageBox.information(self, "Ingest", "Load the CLIP model first.")
            return
        text = self.hash_edit.toPlainText()
        hashes = [h.strip() for h in text.splitlines() if h.strip()]
        if not hashes:
            QMessageBox.information(self, "Ingest", "No hashes to ingest.")
            return
        self._queue.append({"bucket": bucket, "hashes": hashes})
        self.hash_edit.clear()
        self._save_queue()
        self._render_queue()
        self._refresh_actions_state()
        self.set_status(f"Queued {len(hashes)} hashes for '{bucket}'.")
        self._advance_queue()

    def _advance_queue(self) -> None:
        if self.ingest_worker is not None and self.ingest_worker.isRunning():
            return
        if self._paused:
            return
        if not self._queue:
            self._current_job = None
            self._render_queue()
            return
        if not self.clip.is_loaded:
            return
        job = self._queue[0]
        self._current_job = job
        self.ingest_worker = IngestWorker(
            self.db, self.hydrus, self.clip, job["bucket"], list(job["hashes"])
        )
        self.ingest_worker.progress.connect(self._on_progress)
        self.ingest_worker.log.connect(self.set_status)
        self.ingest_worker.finished_ok.connect(self._on_ingest_done)
        self.ingest_worker.failed.connect(self._on_ingest_failed)
        self.ingest_worker.finished.connect(self._on_ingest_finished)
        self._refresh_actions_state()
        self._render_queue()
        self.ingest_worker.start()

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = paused
        self.pause_btn.setText("Resume" if paused else "Pause")
        if paused:
            if self.ingest_worker is not None and self.ingest_worker.isRunning():
                self._current_job = None
                self.ingest_worker.cancel()
            self.set_status("Queue paused.")
        else:
            self.set_status("Queue resumed.")
            self._advance_queue()

    def _clear_queue(self) -> None:
        if self.ingest_worker is not None and self.ingest_worker.isRunning():
            QMessageBox.information(self, "Queue", "Cannot clear while an ingest is running.")
            return
        self._queue.clear()
        self._save_queue()
        self._render_queue()
        self._refresh_actions_state()

    def _render_queue(self) -> None:
        self.queue_list.clear()
        if self._current_job is not None:
            item = QListWidgetItem(
                f"▶ {self._current_job['bucket']} ({len(self._current_job['hashes'])} files)"
            )
            self.queue_list.addItem(item)
        for job in self._queue:
            if job is self._current_job:
                continue
            self.queue_list.addItem(
                QListWidgetItem(f"⏳ {job['bucket']} ({len(job['hashes'])} files)")
            )
        if self._queue_list_is_empty():
            self.queue_list.addItem(QListWidgetItem("(empty)"))

    def _queue_list_is_empty(self) -> bool:
        return self._current_job is None and not self._queue

    def _save_queue(self) -> None:
        try:
            if self._queue:
                self._queue_path.write_text(json.dumps(self._queue))
            else:
                self._queue_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _load_queue(self) -> None:
        try:
            if not self._queue_path.exists():
                self._queue = []
                return
            data = json.loads(self._queue_path.read_text())
            if isinstance(data, list):
                self._queue = [
                    j for j in data
                    if isinstance(j, dict) and "bucket" in j and "hashes" in j
                ]
        except Exception:
            self._queue = []

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        if self._paused:
            return
        bucket = self._current_job["bucket"] if self._current_job else ""
        self.set_status(f"[{bucket}] Ingesting [{current}/{total}]: {msg}")

    def _on_ingest_done(self, count: int) -> None:
        if not self._paused:
            self.set_status(f"Ingest finished: {count} images added.")
        self.buckets_changed.emit()
        self._update_bucket_counts()
        self._refresh_actions_state()

    def _on_ingest_failed(self, msg: str) -> None:
        if not self._paused:
            self.set_status(f"Ingest failed: {msg}")
            QMessageBox.warning(self, "Ingest failed", msg)
        self._refresh_actions_state()

    def _on_ingest_finished(self) -> None:
        worker = self.ingest_worker
        self.ingest_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._current_job is not None and self._queue and self._current_job is self._queue[0]:
            self._queue.pop(0)
        self._current_job = None
        self._save_queue()
        self._render_queue()
        self._refresh_actions_state()
        if self._paused:
            self.set_status("Queue paused.")
        else:
            self._advance_queue()

    def _test_hydrus(self) -> None:
        if self.hydrus_worker is not None and self.hydrus_worker.isRunning():
            return
        self.test_hydrus_btn.setEnabled(False)
        self.set_status("Testing Hydrus API connection...")
        self.hydrus_worker = HydrusCheckWorker(
            self.hydrus,
            tag_service_key=self.config.tag_service_key,
            rating_service_key=self.config.rating_service_key,
        )
        self.hydrus_worker.ok.connect(self._on_hydrus_ok)
        self.hydrus_worker.failed.connect(self._on_hydrus_failed)
        self.hydrus_worker.finished.connect(self._on_hydrus_finished)
        self.hydrus_worker.start()

    def _on_hydrus_ok(self, message: str) -> None:
        self.test_hydrus_btn.setEnabled(True)
        self.set_status(message)
        QMessageBox.information(self, "Hydrus API", message)

    def _on_hydrus_failed(self, msg: str) -> None:
        self.test_hydrus_btn.setEnabled(True)
        self.set_status(f"Hydrus API check failed: {msg}")
        QMessageBox.warning(self, "Hydrus API", f"Could not reach Hydrus API:\n{msg}")

    def _on_hydrus_finished(self) -> None:
        worker = self.hydrus_worker
        self.hydrus_worker = None
        if worker is not None:
            worker.deleteLater()
        self.test_hydrus_btn.setEnabled(True)

    def load_model(self) -> None:
        if self.model_worker is not None and self.model_worker.isRunning():
            return
        self.load_btn.setEnabled(False)
        self.model_status.setText("Loading model...")
        self.set_status("Loading CLIP model...")
        self.model_worker = ModelLoadWorker(self.clip, self.config.clip_model)
        self.model_worker.loaded.connect(self._on_model_loaded)
        self.model_worker.failed.connect(self._on_model_failed)
        self.model_worker.finished.connect(self._on_model_finished)
        self.model_worker.start()

    def _on_model_loaded(self, model_name: str, device: str) -> None:
        self.refresh_model_status()
        self.set_status(f"Model loaded: {model_name} on {device}.")
        self.model_state_changed.emit()
        self._advance_queue()

    def _on_model_failed(self, msg: str) -> None:
        self.refresh_model_status()
        self.set_status(f"Model load failed: {msg}")
        QMessageBox.warning(self, "Model load", msg)
        self.model_state_changed.emit()

    def _on_model_finished(self) -> None:
        worker = self.model_worker
        self.model_worker = None
        if worker is not None:
            worker.deleteLater()

    def _eject_model(self) -> None:
        self.clip.unload()
        self.refresh_model_status()
        self.set_status("Model ejected from memory.")
        self._refresh_actions_state()
        self.model_state_changed.emit()
