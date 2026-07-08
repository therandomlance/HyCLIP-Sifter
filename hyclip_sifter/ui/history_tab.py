"""History tab: browse triaged images by bucket / operation."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter, QFrame,
    QPushButton, QComboBox, QSpinBox, QLineEdit, QLabel, QDateEdit, QMessageBox,
    QFileDialog,
)
from PySide6.QtCore import QDate

from ..database import Database, OP_ARCHIVE, OP_DELETE, OP_SKIP, OP_DEFER
from ..clip_model import ClipModel
from ..hydrus_service import HydrusService
from ..workers import HydrusOperationWorker, UndeleteWorker
from .widgets import hrule
from .thumbnail_grid import ThumbnailGrid


class HistoryTab(QWidget):
    search_with_image = Signal(str)

    def __init__(self, config, db: Database, clip: ClipModel, hydrus: HydrusService,
                 thumb_cache, parent=None):
        super().__init__(parent)
        self._config = config
        self._db = db
        self._clip = clip
        self._hydrus = hydrus
        self._cache = thumb_cache
        self._trash_worker: UndeleteWorker | None = None
        self._build_ui()
        self.refresh_buckets()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        sidebar = QFrame()
        sidebar.setObjectName("SectionBox")
        sidebar.setMinimumWidth(300)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 12, 12, 12)
        side.setSpacing(8)

        title = QLabel("FILTER")
        title.setObjectName("SectionTitle")
        side.addWidget(title)
        form = QFormLayout()
        form.setSpacing(4)
        self.bucket_combo = QComboBox()
        self.bucket_combo.addItem("(all buckets)", "")
        form.addRow("Bucket:", self.bucket_combo)
        side.addLayout(form)

        # Operation segmented buttons.
        self.op_buttons: dict[int, QPushButton] = {}
        grid = QHBoxLayout()
        grid2 = QHBoxLayout()
        for code, name, obj, row in [
            (OP_ARCHIVE, "Archived", "ArchiveBtn", 0),
            (OP_DELETE, "Deleted", "DeleteBtn", 0),
            (OP_SKIP, "Skipped", "SkipBtn", 1),
            (OP_DEFER, "Deferred", "DeferBtn", 1),
        ]:
            btn = QPushButton(name)
            btn.setObjectName(obj)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, c=code: self._on_op_toggled(c))
            self.op_buttons[code] = btn
            (grid if row == 0 else grid2).addWidget(btn)
        side.addLayout(grid)
        side.addLayout(grid2)

        self.count_label = QLabel("0 matching entries")
        side.addWidget(self.count_label)
        side.addWidget(hrule())

        date_form = QFormLayout()
        date_form.setSpacing(4)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setSpecialValueText("(any)")
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setSpecialValueText("(any)")
        self.date_to.setDate(self.date_to.minimumDate())
        date_form.addRow("Date from:", self.date_from)
        date_form.addRow("Date to:", self.date_to)
        side.addLayout(date_form)
        self.clear_dates_btn = QPushButton("Clear dates")
        side.addWidget(self.clear_dates_btn)
        side.addWidget(hrule())

        size_form = QFormLayout()
        size_form.setSpacing(4)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(48, 1000)
        self.size_spin.setSingleStep(25)
        self.size_spin.setSuffix(" px")
        self.size_spin.setValue(self._config.thumbnail_size)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 10000)
        self.limit_spin.setValue(100)
        size_form.addRow("Thumbnail size:", self.size_spin)
        size_form.addRow("Limit:", self.limit_spin)
        side.addLayout(size_form)

        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setObjectName("PrimaryBtn")
        side.addWidget(self.browse_btn)
        self.export_btn = QPushButton("Export CSV")
        side.addWidget(self.export_btn)
        side.addStretch(1)
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.grid = ThumbnailGrid(self._hydrus, self._cache, self, show_triage=False)
        splitter.addWidget(self.grid)
        splitter.setSizes([300, 10000])

        # Wiring.
        self.bucket_combo.currentTextChanged.connect(self._update_count)
        self.clear_dates_btn.clicked.connect(self._clear_dates)
        self.browse_btn.clicked.connect(self.browse)
        self.export_btn.clicked.connect(self.export_csv)
        self.size_spin.valueChanged.connect(self.grid.set_icon_size)
        self.grid.search_with_image.connect(self.search_with_image)
        self.grid.remove_from_trash.connect(self._remove_from_trash)

    # ----------------------------------------------------------- buckets
    def refresh_buckets(self, current: str | None = None) -> None:
        names = self._db.list_buckets()
        self.bucket_combo.blockSignals(True)
        self.bucket_combo.clear()
        self.bucket_combo.addItem("(all buckets)", "")
        for n in names:
            self.bucket_combo.addItem(n, n)
        if current:
            idx = self.bucket_combo.findData(current)
            if idx >= 0:
                self.bucket_combo.setCurrentIndex(idx)
        self.bucket_combo.blockSignals(False)
        self._update_count()

    def set_filter(self, bucket: str | None = None, operation: int | None = None) -> None:
        if bucket:
            idx = self.bucket_combo.findData(bucket)
            if idx >= 0:
                self.bucket_combo.setCurrentIndex(idx)
        if operation is not None:
            for code, btn in self.op_buttons.items():
                btn.setChecked(code == operation)
        self._update_count()
        self.browse()

    def _on_op_toggled(self, code: int) -> None:
        btn = self.op_buttons[code]
        if not btn.isChecked():
            # Don't allow unchecking via a second click — keep it checked.
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
            return
        for c, b in self.op_buttons.items():
            if c != code:
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
        self._update_count()
        self.browse()

    def _current_op(self) -> int | None:
        for code, btn in self.op_buttons.items():
            if btn.isChecked():
                return code
        return None

    def _current_bucket(self) -> str | None:
        data = self.bucket_combo.currentData()
        return data or None

    def _date_str(self, edit: QDateEdit) -> str | None:
        d = edit.date()
        if not d.isValid() or d == edit.minimumDate():
            return None
        return d.toString("yyyy-MM-dd")

    def _clear_dates(self) -> None:
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to.setDate(self.date_to.minimumDate())
        self._update_count()

    def _update_count(self) -> None:
        n = self._db.history_count(
            bucket=self._current_bucket(),
            operation=self._current_op(),
            date_from=self._date_str(self.date_from),
            date_to=self._date_str(self.date_to),
        )
        self.count_label.setText(f"{n:,} matching entries")

    # ----------------------------------------------------------- browse
    def browse(self) -> None:
        rows = self._db.history_query_filtered(
            bucket=self._current_bucket(),
            operation=self._current_op(),
            limit=self.limit_spin.value(),
            date_from=self._date_str(self.date_from),
            date_to=self._date_str(self.date_to),
        )
        hashes = [r[0] for r in rows]
        tips = {r[0]: f"{r[0]}\ntimestamp: {r[3]}" for r in rows}
        self.grid.set_hashes(hashes)
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            h = item.data(Qt.ItemDataRole.UserRole)
            if h in tips:
                item.setToolTip(tips[h])

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "history.csv",
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            n = self._db.history_export_csv(
                path, bucket=self._current_bucket(), operation=self._current_op()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Wrote {n} rows to {path}.")

    def _remove_from_trash(self, hash_: str) -> None:
        """Call Hydrus undelete_files for a single hash via a cancellable worker.

        Uses :class:`UndeleteWorker` so the call benefits from the HydrusService
        retry logic, is cancellable, and is waited on during shutdown (no raw
        daemon thread that may outlive the DB/client).
        """
        if self._trash_worker is not None:
            return
        self._trash_worker = UndeleteWorker(self._hydrus, [hash_])
        self._trash_worker.done.connect(self._on_undelete_done)
        self._trash_worker.failed.connect(self._on_undelete_failed)
        self._trash_worker.start()

    def _on_undelete_done(self, hashes: list) -> None:
        worker = self._trash_worker
        self._trash_worker = None
        if worker is not None:
            try:
                worker.disconnect()
                worker.deleteLater()
            except (TypeError, RuntimeError):
                pass

    def _on_undelete_failed(self, msg: str) -> None:
        worker = self._trash_worker
        self._trash_worker = None
        if worker is not None:
            try:
                worker.disconnect()
                worker.deleteLater()
            except (TypeError, RuntimeError):
                pass
        QMessageBox.warning(self, "Undelete failed", msg)

    def cleanup(self) -> None:
        if self._trash_worker is not None:
            self._trash_worker.cancel()
            self._trash_worker.wait(2000)
