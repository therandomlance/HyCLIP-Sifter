"""Thumbnail grid widget used by Search and History tabs."""

from __future__ import annotations


from PySide6.QtCore import Qt, QSize, QTimer, QPoint, Signal
from PySide6.QtGui import (
    QAction, QKeySequence, QPixmap, QColor, QPainter, QPen, QIcon, QWheelEvent,
)
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QMenu, QWidget, QSizePolicy, QToolTip, QStyle, QStyledItemDelegate,
)

from ..workers import ThumbnailLoader
from .widgets import skeleton_pixmap, make_distance_pixmap


class _ThumbDelegate(QStyledItemDelegate):
    """Renders distance badge + selection checkmark + duplicate-pair borders."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dup_pairs: list[tuple[str, str, float]] = []

    def set_dup_pairs(self, pairs: list[tuple[str, str, float]]) -> None:
        self._dup_pairs = pairs or []

    def _dup_color(self, distance: float) -> QColor:
        # Orange for close, red for very close.
        return QColor("#ef4444") if distance < 0.02 else QColor("#f59e0b")

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        # Duplicate-pair border.
        h = index.data(Qt.ItemDataRole.UserRole)
        if h and self._dup_pairs:
            for ha, hb, d in self._dup_pairs:
                if h == ha or h == hb:
                    painter.save()
                    pen = QPen(self._dup_color(d), 3)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    r = option.rect.adjusted(1, 1, -1, -1)
                    painter.drawRect(r)
                    painter.restore()
                    break
        # Distance badge.
        badge = index.data(Qt.ItemDataRole.UserRole + 2)
        if badge is not None:
            rect = option.rect
            pm = make_distance_pixmap(badge)
            painter.drawPixmap(rect.left() + 4, rect.top() + 4, pm)
        # Selection checkmark.
        if option.state & QStyle.StateFlag.State_Selected:
            painter.save()
            pen = QPen(QColor("#ffffff"), 2)
            painter.setPen(pen)
            r = option.rect.adjusted(2, 2, -2, -2)
            painter.drawLine(r.right() - 16, r.bottom() - 14, r.right() - 10, r.bottom() - 8)
            painter.drawLine(r.right() - 10, r.bottom() - 8, r.right() - 4, r.bottom() - 22)
            painter.restore()


class ThumbnailGrid(QListWidget):
    """Lazy-loading thumbnail grid with multi-selection and context menu."""

    search_with_image = Signal(str)             # hash
    search_with_selection = Signal(list)        # hashes
    add_to_negative = Signal(str)               # hash
    open_externally = Signal(str)               # hash
    copy_path = Signal(str)                     # hash
    open_containing_folder = Signal(str)        # hash
    reingest = Signal(str)                       # hash
    remove_from_trash = Signal(str)             # hash
    triage = Signal(int, list)                   # operation, hashes

    def __init__(self, hydrus, cache, parent: QWidget | None = None,
                 show_triage: bool = True):
        super().__init__(parent)
        self.setObjectName("ThumbGrid")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setIconSize(QSize(300, 300))
        self.setItemDelegate(_ThumbDelegate(self))
        self._delegate = self.itemDelegate()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._hydrus = hydrus
        self._cache = cache
        self._show_triage = show_triage
        self._loader: ThumbnailLoader | None = None
        self._retiring: list[ThumbnailLoader] = []
        self._hash_to_item: dict[str, QListWidgetItem] = {}
        self._pending: set[str] = set()
        self._dup_pairs: list[tuple[str, str, float]] = []
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._show_hover_preview)
        self._hover_index: QListWidgetItem | None = None
        self._context_menu = QMenu(self)
        self._build_context_menu()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setMouseTracking(True)
        self.entered.connect(self._on_enter)

    # ----------------------------------------------------------- sizing
    def set_icon_size(self, size: int) -> None:
        self.setIconSize(QSize(size, size))
        # Re-scale stored full-resolution pixmaps to the new size.
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            item.setSizeHint(QSize(size + 16, size + 16))
            src = item.data(Qt.ItemDataRole.UserRole + 3)
            if src is not None:
                item.setIcon(QIcon(src.scaled(size, size,
                                              Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)))

    # ----------------------------------------------------------- hash input
    def set_hashes(self, hashes: list[str], distances: dict[str, float] | None = None) -> None:
        self._cancel_loader()
        self.clear()
        self._hash_to_item.clear()
        self._pending.clear()
        self._dup_pairs = []
        size = self.iconSize().width()
        for h in hashes:
            item = QListWidgetItem(self)
            item.setData(Qt.ItemDataRole.UserRole, h)
            item.setSizeHint(QSize(size + 16, size + 16))
            pm = skeleton_pixmap(size)
            item.setIcon(QIcon(pm))
            tip = h
            if distances and h in distances:
                d = distances[h]
                tip = f"{h}\ndistance: {d:.4f}"
                item.setData(Qt.ItemDataRole.UserRole + 2, f"{d:.3f}")
            item.setToolTip(tip)
            self.addItem(item)
            self._hash_to_item[h] = item
            self._pending.add(h)
        self._load_pending()

    def add_results(self, pairs: list[tuple[str, float]]) -> None:
        size = self.iconSize().width()
        for h, d in pairs:
            if h in self._hash_to_item:
                continue
            item = QListWidgetItem(self)
            item.setData(Qt.ItemDataRole.UserRole, h)
            item.setSizeHint(QSize(size + 16, size + 16))
            pm = skeleton_pixmap(size)
            item.setIcon(QIcon(pm))
            item.setToolTip(f"{h}\ndistance: {d:.4f}")
            item.setData(Qt.ItemDataRole.UserRole + 2, f"{d:.3f}")
            self.addItem(item)
            self._hash_to_item[h] = item
            self._pending.add(h)
        self._load_pending()

    def apply_distances(self, distances: dict[str, float]) -> None:
        """Update distance badges/tooltips on existing items without reloading."""
        for h, d in distances.items():
            item = self._hash_to_item.get(h)
            if item is None:
                continue
            item.setToolTip(f"{h}\ndistance: {d:.4f}")
            item.setData(Qt.ItemDataRole.UserRole + 2, f"{d:.3f}")
        self.viewport().update()

    def current_distances(self) -> dict[str, float]:
        """Return {hash: distance} for items that currently have a distance badge."""
        out: dict[str, float] = {}
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            badge = item.data(Qt.ItemDataRole.UserRole + 2)
            if badge is None:
                continue
            try:
                out[item.data(Qt.ItemDataRole.UserRole)] = float(badge)
            except (TypeError, ValueError):
                continue
        return out

    def _load_pending(self) -> None:
        if not self._pending:
            return
        # If a loader is already running, don't cancel it — accumulate the
        # new hashes and they'll be picked up when the current loader finishes.
        if self._loader is not None:
            return
        hashes = list(self._pending)
        self._pending.clear()
        self._loader = ThumbnailLoader(self._hydrus, self._cache, hashes)
        self._loader.loaded.connect(self._on_thumb)
        self._loader.finished_all.connect(self._loader_done)
        self._loader.start()

    def _loader_done(self) -> None:
        loader = self._loader
        if loader is not None:
            self._retire_loader(loader)
            self._loader = None
        # If more hashes accumulated while the loader was running, start a new one.
        if self._pending:
            self._load_pending()

    def _retire_loader(self, loader: ThumbnailLoader | None) -> None:
        """Disconnect, cancel, and arrange async deletion without blocking.

        Implements the ``_retire`` pattern from AGENTS.md: the loader's signals
        are disconnected so late emissions can't reach the grid, the cancel flag
        is set, and deletion is deferred to the loader's ``finished`` signal so
        the UI thread never blocks on ``QThread.wait()``.
        """
        if loader is None:
            return
        try:
            loader.loaded.disconnect(self._on_thumb)
            loader.finished_all.disconnect(self._loader_done)
        except (TypeError, RuntimeError):
            pass
        loader.cancel()
        if loader.isFinished():
            loader.deleteLater()
            return
        loader.finished.connect(lambda l=loader: self._on_retired(l))
        self._retiring.append(loader)

    def _on_retired(self, loader: ThumbnailLoader) -> None:
        """Drop a retired loader once its thread has finished."""
        try:
            self._retiring.remove(loader)
        except ValueError:
            pass
        try:
            loader.deleteLater()
        except RuntimeError:
            pass

    def _on_thumb(self, hash_: str, data: bytes) -> None:
        item = self._hash_to_item.get(hash_)
        if item is None:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            size = self.iconSize().width()
            # Store the full-resolution pixmap so we can re-scale on size change.
            item.setData(Qt.ItemDataRole.UserRole + 3, pm)
            item.setIcon(QIcon(pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)))

    # ----------------------------------------------------------- queries
    def selected_hashes(self) -> list[str]:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.selectedItems()]

    def all_hashes(self) -> list[str]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def remove_hash(self, hash_: str) -> None:
        item = self._hash_to_item.pop(hash_, None)
        if item is not None:
            row = self.row(item)
            self.takeItem(row)

    def clear_all(self) -> None:
        self._cancel_loader()
        self.clear()
        self._hash_to_item.clear()
        self._pending.clear()

    def _cancel_loader(self) -> None:
        """Retire the active loader without blocking the UI thread."""
        self._retire_loader(self._loader)
        self._loader = None

    def cleanup(self) -> None:
        """Cancel and wait for any thumbnail loaders (used during shutdown)."""
        self._retire_loader(self._loader)
        self._loader = None
        for lo in list(self._retiring):
            lo.cancel()
            lo.wait(2000)
            try:
                self._retiring.remove(lo)
            except ValueError:
                pass
            try:
                lo.deleteLater()
            except RuntimeError:
                pass

    def set_duplicates(self, pairs: list[tuple[str, str, float]]) -> None:
        self._dup_pairs = pairs
        self._delegate.set_dup_pairs(pairs)
        self.viewport().update()

    # ----------------------------------------------------------- selection
    def select_all(self) -> None:
        for i in range(self.count()):
            self.item(i).setSelected(True)

    def deselect_all(self) -> None:
        self.clearSelection()

    def invert_selection(self) -> None:
        for i in range(self.count()):
            it = self.item(i)
            it.setSelected(not it.isSelected())

    # ----------------------------------------------------------- context menu
    def _build_context_menu(self) -> None:
        self._act_search = QAction("Search using this image", self)
        self._act_search.triggered.connect(self._ctx_search)
        self._act_search_selection = QAction("Search using selection", self)
        self._act_search_selection.triggered.connect(self._ctx_search_selection)
        self._act_negative = QAction("Add to negative query", self)
        self._act_negative.triggered.connect(self._ctx_negative)
        self._act_open = QAction("Open externally", self)
        self._act_open.triggered.connect(self._ctx_open)
        self._act_copy = QAction("Copy file path", self)
        self._act_copy.triggered.connect(self._ctx_copy)
        self._act_folder = QAction("Open containing folder", self)
        self._act_folder.triggered.connect(self._ctx_folder)
        self._act_reingest = QAction("Re-ingest into bucket", self)
        self._act_reingest.triggered.connect(self._ctx_reingest)
        self._act_trash = QAction("Remove from trash", self)
        self._act_trash.triggered.connect(self._ctx_trash)
        self._act_select_all = QAction("Select All", self)
        self._act_select_all.setShortcut(QKeySequence("Ctrl+A"))
        self._act_select_all.triggered.connect(self.select_all)
        self._act_deselect = QAction("Deselect All", self)
        self._act_deselect.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self._act_deselect.triggered.connect(self.deselect_all)
        self._act_invert = QAction("Invert Selection", self)
        self._act_invert.setShortcut(QKeySequence("Ctrl+I"))
        self._act_invert.triggered.connect(self.invert_selection)
        self._act_archive = QAction("Archive", self)
        self._act_archive.triggered.connect(lambda: self.triage.emit(1, self.selected_hashes()))
        self._act_delete = QAction("Delete", self)
        self._act_delete.triggered.connect(lambda: self.triage.emit(0, self.selected_hashes()))
        self._act_skip = QAction("Skip", self)
        self._act_skip.triggered.connect(lambda: self.triage.emit(2, self.selected_hashes()))
        self._act_defer = QAction("Defer", self)
        self._act_defer.triggered.connect(lambda: self.triage.emit(3, self.selected_hashes()))

    def _on_context_menu(self, pos: QPoint) -> None:
        menu = self._context_menu
        menu.clear()
        sel = self.selected_hashes()
        item = self.itemAt(pos)
        if item is not None:
            menu.addAction(self._act_search)
            if len(sel) > 1:
                menu.addAction(self._act_search_selection)
            menu.addAction(self._act_negative)
            menu.addSeparator()
        if self._show_triage and sel:
            menu.addAction(self._act_archive)
            menu.addAction(self._act_delete)
            menu.addAction(self._act_skip)
            menu.addAction(self._act_defer)
            menu.addSeparator()
        if item is not None:
            menu.addAction(self._act_open)
            menu.addAction(self._act_copy)
            menu.addAction(self._act_folder)
            menu.addSeparator()
            menu.addAction(self._act_reingest)
            menu.addAction(self._act_trash)
            menu.addSeparator()
        menu.addAction(self._act_select_all)
        menu.addAction(self._act_deselect)
        menu.addAction(self._act_invert)
        menu.exec(self.mapToGlobal(pos))

    def _ctx_search(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.search_with_image.emit(item.data(Qt.ItemDataRole.UserRole))

    def _ctx_search_selection(self) -> None:
        self.search_with_selection.emit(self.selected_hashes())

    def _ctx_negative(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.add_to_negative.emit(item.data(Qt.ItemDataRole.UserRole))

    def _ctx_open(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.open_externally.emit(item.data(Qt.ItemDataRole.UserRole))

    def _ctx_copy(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.copy_path.emit(item.data(Qt.ItemDataRole.UserRole))

    def _ctx_folder(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.open_containing_folder.emit(item.data(Qt.ItemDataRole.UserRole))

    def _ctx_reingest(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.reingest.emit(item.data(Qt.ItemDataRole.UserRole))

    def _ctx_trash(self) -> None:
        item = self.currentItem()
        if item is not None:
            self.remove_from_trash.emit(item.data(Qt.ItemDataRole.UserRole))

    # ----------------------------------------------------------- hover preview
    def _on_enter(self, index) -> None:
        self._hover_index = self.itemFromIndex(index)
        self._hover_timer.start(500)

    def leaveEvent(self, event) -> None:
        self._hover_timer.stop()
        self._hover_index = None
        QToolTip.hideText()
        super().leaveEvent(event)

    def _show_hover_preview(self) -> None:
        if self._hover_index is None:
            return
        h = self._hover_index.data(Qt.ItemDataRole.UserRole)
        if h is None:
            return
        icon = self._hover_index.icon()
        if icon.isNull():
            return
        icon.pixmap(512, 512)
        QToolTip.showText(self.mapToGlobal(self._last_pos), "", self)
        # Use tooltip text fallback with hash.
        QToolTip.showText(self.mapToGlobal(self._last_pos),
                          self._hover_index.toolTip(), self)

    def mouseMoveEvent(self, event) -> None:
        self._last_pos = event.pos()
        super().mouseMoveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        sb = self.verticalScrollBar()
        if sb is None or not sb.isVisible():
            super().wheelEvent(event)
            return
        icon_h = self.iconSize().height() + self.spacing()
        delta = event.angleDelta().y()
        step_size = max(icon_h // 3, 40)
        scroll = -delta // 120 * step_size
        sb.setValue(sb.value() + scroll)
