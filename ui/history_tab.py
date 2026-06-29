from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QFrame,
    QMenu,
)

from database import Database
from hydrus_service import HydrusService
from ui.thumbnail_grid import ThumbnailGrid

OP_DELETE = 0
OP_ARCHIVE = 1
OP_SKIP = 2
OP_DEFER = 3


class HistoryTab(QWidget):
    search_with_image = Signal(str)

    def __init__(self, db: Database, hydrus: HydrusService, set_status, parent=None):
        super().__init__(parent)
        self.db = db
        self.hydrus = hydrus
        self.set_status = set_status

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.grid = ThumbnailGrid(hydrus)
        self.grid.set_menu_factory(self._build_menu)
        self.grid.search_requested.connect(self.search_with_image)

        self.sidebar = self._build_sidebar()
        layout.addWidget(self.sidebar, 0)
        layout.addWidget(self.grid, 1)

    def _build_sidebar(self) -> QWidget:
        w = QFrame()
        w.setFixedWidth(230)
        v = QVBoxLayout(w)

        v.addWidget(QLabel("Bucket:"))
        self.bucket_combo = QComboBox()
        v.addWidget(self.bucket_combo)

        v.addWidget(QLabel("Operation:"))
        self.op_group = QButtonGroup(self)
        self.rb_archive = QRadioButton("Archived")
        self.rb_delete = QRadioButton("Deleted")
        self.rb_skip = QRadioButton("Skipped")
        self.rb_defer = QRadioButton("Deferred")
        self.rb_archive.setChecked(True)
        self.op_group.addButton(self.rb_archive, OP_ARCHIVE)
        self.op_group.addButton(self.rb_delete, OP_DELETE)
        self.op_group.addButton(self.rb_skip, OP_SKIP)
        self.op_group.addButton(self.rb_defer, OP_DEFER)
        v.addWidget(self.rb_archive)
        v.addWidget(self.rb_delete)
        v.addWidget(self.rb_skip)
        v.addWidget(self.rb_defer)

        v.addWidget(QLabel("Thumbnail size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(48, 512)
        self.size_spin.setSingleStep(25)
        self.size_spin.setValue(400)
        self.size_spin.setSuffix(" px")
        self.size_spin.valueChanged.connect(self.grid.set_icon_size)
        v.addWidget(self.size_spin)

        v.addWidget(QLabel("Limit:"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 10000)
        self.limit_spin.setValue(100)
        v.addWidget(self.limit_spin)

        self.search_btn = QPushButton("Search History")
        self.search_btn.clicked.connect(self.run_search)
        v.addWidget(self.search_btn)

        v.addStretch(1)
        return w

    def _build_menu(self, hash_: str):
        actions = self.grid.make_default_actions(hash_, allow_ops=False)
        m = QMenu(self.grid)
        for a in actions:
            m.addAction(a)
        return m

    def refresh_buckets(self) -> None:
        buckets = self.db.history_buckets()
        self.bucket_combo.clear()
        for name, count in buckets:
            self.bucket_combo.addItem(f"{name} ({count})", name)

    def current_bucket(self) -> str | None:
        idx = self.bucket_combo.currentIndex()
        if idx < 0:
            return None
        return self.bucket_combo.itemData(idx)

    def current_operation(self) -> int:
        return self.op_group.checkedId()

    def run_search(self) -> None:
        bucket = self.current_bucket()
        if not bucket:
            self.set_status("No history bucket selected.")
            return
        op = self.current_operation()
        limit = self.limit_spin.value()
        hashes = self.db.history_query(bucket, op, limit)
        self.grid.set_hashes(hashes)
        self.set_status(f"History: {len(hashes)} entries in '{bucket}'.")
