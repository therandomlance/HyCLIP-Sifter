"""Small reusable widget helpers."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget
)

from .theme import ROLES


def hrule() -> QFrame:
    line = QFrame()
    line.setObjectName("HRule")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Build a titled section frame; returns (frame, content_layout)."""
    frame = QFrame()
    frame.setObjectName("SectionBox")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    content = QVBoxLayout()
    content.setSpacing(6)
    layout.addLayout(content)
    return frame, content


def count_badge(text: str, color_role: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("CountBadge")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    color = ROLES.get(color_role, color_role)
    label.setStyleSheet(
        f"background-color: {color}; color: white; border-radius: 9px; padding: 1px 8px;"
    )
    return label


class Toast(QWidget):
    """Non-blocking toast notification sliding up at bottom of a parent widget."""

    accepted = Signal()
    rejected = Signal()

    def __init__(self, text: str, parent: QWidget, auto_ms: int = 5000,
                 confirm: bool = True):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        msg = QLabel(text)
        layout.addWidget(msg, 1)
        if confirm:
            yes = QPushButton("Yes")
            no = QPushButton("No")
            yes.clicked.connect(lambda: self._done(True))
            no.clicked.connect(lambda: self._done(False))
            layout.addWidget(yes)
            layout.addWidget(no)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._done(False))
        self._timer.start(auto_ms)
        self.adjustSize()
        self._reposition()
        self.show()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        pgeo = parent.rect()
        self.adjustSize()
        x = (pgeo.width() - self.width()) // 2
        y = pgeo.height() - self.height() - 20
        self.move(max(0, x), max(0, y))

    def _done(self, ok: bool) -> None:
        self._timer.stop()
        if ok:
            self.accepted.emit()
        else:
            self.rejected.emit()
        self.close()


def make_distance_pixmap(text: str, size: tuple[int, int] = (44, 16)) -> QPixmap:
    pm = QPixmap(*size)
    pm.fill(QColor(0, 0, 0, 140))
    p = QPainter(pm)
    p.setPen(QColor(255, 255, 255))
    f = QFont()
    f.setPointSize(7)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return pm


def skeleton_pixmap(size: int, color: QColor | None = None) -> QPixmap:
    color = color or QColor("#2a2a2a")
    pm = QPixmap(size, size)
    pm.fill(color)
    return pm
