import subprocess
import sys

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QMenu,
    QListWidget,
    QListWidgetItem,
)

from hydrus_service import HydrusService
from workers import ThumbnailLoader

HASH_ROLE = Qt.UserRole
PIXMAP_ROLE = Qt.UserRole + 1


def open_externally(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def copy_to_clipboard(text: str) -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.clipboard().setText(text)


def open_containing_folder(path: str) -> None:
    import os

    folder = os.path.dirname(path) or path
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class ThumbnailGrid(QListWidget):
    search_requested = Signal(str)
    open_externally_requested = Signal(str)
    archive_requested = Signal(list)
    delete_requested = Signal(list)
    skip_requested = Signal(list)
    defer_requested = Signal(list)

    def __init__(self, hydrus: HydrusService, parent=None):
        super().__init__(parent)
        self.hydrus = hydrus
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setUniformItemSizes(True)
        self.setMovement(QListWidget.Static)
        self.setSelectionMode(QListWidget.MultiSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self._icon_size = 400
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(30)
        self._loader: ThumbnailLoader | None = None
        self._retiring: list = []
        self._menu_factory = None
        self._anchor_row: int | None = None
        self._drag_start_row: int | None = None
        self._drag_last_row: int | None = None
        self._dragging = False
        self._drag_rows: set[int] = set()
        self._drag_moved = False
        self.customContextMenuRequested.connect(self._on_context_menu)

    def set_menu_factory(self, factory) -> None:
        self._menu_factory = factory

    def set_icon_size(self, size: int) -> None:
        self._icon_size = max(32, size)
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        hint = QSize(self._icon_size + 12, self._icon_size + 28)
        for i in range(self.count()):
            item = self.item(i)
            item.setSizeHint(hint)
            pix = item.data(PIXMAP_ROLE)
            if isinstance(pix, QPixmap) and not pix.isNull():
                item.setIcon(self._scaled(pix))

    def _scaled(self, pix: QPixmap) -> QPixmap:
        return pix.scaled(
            self._icon_size,
            self._icon_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def set_hashes(self, hashes: list[str], tooltips: dict[str, str] | None = None) -> None:
        self._cancel_loader()
        self.clear()
        self._anchor_row = None
        self._drag_start_row = None
        self._drag_last_row = None
        for h in hashes:
            item = QListWidgetItem()
            item.setData(HASH_ROLE, h)
            item.setData(PIXMAP_ROLE, QPixmap())
            item.setSizeHint(QSize(self._icon_size + 12, self._icon_size + 28))
            if tooltips and h in tooltips:
                item.setToolTip(tooltips[h])
            self.addItem(item)
        if hashes:
            loader = ThumbnailLoader(self.hydrus, hashes)
            loader.loaded.connect(self._on_loaded)
            loader.finished_all.connect(self._on_loader_finished)
            self._loader = loader
            loader.start()

    def _on_loaded(self, hash_: str, data: bytes) -> None:
        if not data:
            return
        item = self._find_item(hash_)
        if item is None:
            return
        pix = QPixmap()
        pix.loadFromData(data)
        if pix.isNull():
            return
        item.setData(PIXMAP_ROLE, pix)
        item.setIcon(self._scaled(pix))

    def _find_item(self, hash_: str) -> QListWidgetItem | None:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(HASH_ROLE) == hash_:
                return item
        return None

    def _cancel_loader(self) -> None:
        loader = self._loader
        self._loader = None
        if loader is not None:
            try:
                loader.loaded.disconnect()
            except Exception:
                pass
            try:
                loader.finished_all.disconnect()
            except Exception:
                pass
            if loader.isRunning():
                loader.cancel()
                self._retiring.append(loader)
                loader.finished.connect(lambda _=None, o=loader: self._retire(o))
            else:
                loader.deleteLater()

    def _retire(self, worker) -> None:
        try:
            self._retiring.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def _on_loader_finished(self) -> None:
        worker = self._loader
        self._loader = None
        if worker is not None:
            worker.deleteLater()

    def remove_hash(self, hash_: str) -> None:
        item = self._find_item(hash_)
        if item is not None:
            row = self.row(item)
            self.takeItem(row)

    def selected_hashes(self) -> list[str]:
        return [
            item.data(HASH_ROLE)
            for item in self.selectedItems()
            if item.data(HASH_ROLE)
        ]

    def deselect_all(self) -> None:
        self.clearSelection()

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
            self._anchor_row = self.row(item)
        hash_ = item.data(HASH_ROLE)
        global_pos = self.mapToGlobal(pos)
        menu: QMenu
        if self._menu_factory is not None:
            menu = self._menu_factory(hash_)
        else:
            menu = QMenu(self)
        if menu is not None and not menu.isEmpty():
            menu.exec(global_pos)

    def make_default_actions(self, hash_: str, *, allow_ops: bool = True) -> list[QAction]:
        actions: list[QAction] = []
        act = QAction("Search using this image", self)
        act.triggered.connect(lambda: self.search_requested.emit(hash_))
        actions.append(act)
        if allow_ops:
            arch = QAction("Archive", self)
            arch.triggered.connect(lambda: self._emit_bulk(self.archive_requested))
            actions.append(arch)
            dele = QAction("Delete", self)
            dele.triggered.connect(lambda: self._emit_bulk(self.delete_requested))
            actions.append(dele)
            skip = QAction("Skip", self)
            skip.triggered.connect(lambda: self._emit_bulk(self.skip_requested))
            actions.append(skip)
            defer = QAction("Defer", self)
            defer.triggered.connect(lambda: self._emit_bulk(self.defer_requested))
            actions.append(defer)
        path = self.hydrus.get_local_path(hash_)
        if path:
            oe = QAction("Open externally", self)
            oe.triggered.connect(lambda checked=False, p=path: open_externally(p))
            actions.append(oe)
            cp = QAction("Copy file path", self)
            cp.triggered.connect(lambda checked=False, p=path: copy_to_clipboard(p))
            actions.append(cp)
            cf = QAction("Open containing folder", self)
            cf.triggered.connect(lambda checked=False, p=path: open_containing_folder(p))
            actions.append(cf)
        return actions

    def _emit_bulk(self, signal) -> None:
        sel = self.selected_hashes()
        if sel:
            signal.emit(sel)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Delete:
            sel = self.selected_hashes()
            if sel:
                self.delete_requested.emit(sel)
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            clicked_row = self.row(self.itemAt(event.position().toPoint()))
            if event.modifiers() & Qt.ShiftModifier:
                if clicked_row >= 0 and self._anchor_row is not None:
                    lo = min(self._anchor_row, clicked_row)
                    hi = max(self._anchor_row, clicked_row)
                    for row in range(lo, hi + 1):
                        self.item(row).setSelected(True)
                    event.accept()
                    return
            elif event.modifiers() & Qt.ControlModifier:
                if clicked_row >= 0:
                    self.item(clicked_row).setSelected(not self.item(clicked_row).isSelected())
                    self._anchor_row = clicked_row
                event.accept()
                return
            else:
                if clicked_row >= 0:
                    self._drag_start_row = clicked_row
                    self._drag_last_row = clicked_row
                    self._dragging = True
                    self._drag_moved = False
                    self._drag_rows = set()
                    self._anchor_row = clicked_row
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self._drag_start_row is not None:
            current_row = self.row(self.itemAt(event.position().toPoint()))
            if current_row < 0:
                return
            if current_row != self._drag_start_row:
                self._drag_moved = True
            if not self._drag_moved:
                return
            lo = min(self._drag_start_row, current_row)
            hi = max(self._drag_start_row, current_row)
            new_range = set(range(lo, hi + 1))
            for row in new_range - self._drag_rows:
                self.item(row).setSelected(True)
            for row in self._drag_rows - new_range:
                self.item(row).setSelected(False)
            self._drag_rows = new_range
            self._drag_last_row = current_row
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            if not self._drag_moved and self._drag_start_row is not None:
                row = self._drag_start_row
                item = self.item(row)
                if item is not None:
                    item.setSelected(not item.isSelected())
            self._dragging = False
            self._drag_start_row = None
            self._drag_last_row = None
            self._drag_rows = set()
            self._drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
