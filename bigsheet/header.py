"""Grid pieces: a column header with filter buttons, and a cell painter."""

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHeaderView, QStyle, QStyledItemDelegate

from . import theme
from .model import column_letters

BUTTON = 18


class FilterHeaderView(QHeaderView):
    """Two-line column header (letter and name) with a dropdown button per column."""

    menuRequested = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setSectionResizeMode(QHeaderView.Interactive)
        self.setMinimumSectionSize(60)
        self.setMouseTracking(True)
        self.filter_mode = False  # filter buttons shown on every column
        self.filtered = set()     # column indexes with a filter
        self.sorts = {}           # column index: ascending
        self.formula_cols = set()  # column indexes holding formulas
        self.current = -1
        self._hover_button = -1

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), max(46, self.fontMetrics().height() * 2 + 14))

    def button_rect(self, logical):
        x = self.sectionViewportPosition(logical)
        w = self.sectionSize(logical)
        h = self.height()
        return QRect(x + w - BUTTON - 5, h - BUTTON - 5, BUTTON, BUTTON)

    def paintSection(self, painter, rect, logical):
        if not rect.isValid():
            return
        model = self.model()
        name = model.headerData(logical, Qt.Horizontal, Qt.DisplayRole) if model else ""
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        active = logical == self.current
        painter.fillRect(rect, QColor("#e8f0fe" if active else theme.CHROME))
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if active:
            painter.fillRect(QRect(rect.left(), rect.bottom() - 1, rect.width(), 2), QColor(theme.ACCENT))

        # Column letter on the first line.
        small = QFont(self.font())
        small.setPointSizeF(max(8.0, self.font().pointSizeF() - 2))
        painter.setFont(small)
        painter.setPen(QColor("#1d4ed8" if active else "#9aa1ac"))
        top = QRect(rect.left(), rect.top() + 3, rect.width(), painter.fontMetrics().height())
        painter.drawText(top, Qt.AlignHCenter | Qt.AlignVCenter, column_letters(logical))

        # Column name on the second line, with room for the button.
        bold = QFont(self.font())
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(theme.TEXT))
        btn = self.button_rect(logical)
        btn.moveLeft(rect.right() - BUTTON - 4)
        btn.moveTop(rect.bottom() - BUTTON - 4)
        show_button = self.filter_mode or logical in self.filtered
        if not show_button:
            btn.moveLeft(rect.right() + 2)  # the name can use the full width
        text_left = rect.left() + 8
        if logical in self.formula_cols:
            fx = QFont(self.font())
            fx.setItalic(True)
            painter.setFont(fx)
            painter.setPen(QColor(theme.ACCENT))
            painter.drawText(QRect(text_left, btn.top(), 18, BUTTON), Qt.AlignVCenter, "fx")
            text_left += 18
            painter.setFont(bold)
            painter.setPen(QColor(theme.TEXT))
        sort_w = 12 if logical in self.sorts else 0
        avail = max(0, btn.left() - text_left - 4 - sort_w)
        label = painter.fontMetrics().elidedText(str(name or ""), Qt.ElideRight, avail)
        name_rect = QRect(text_left, btn.top(), avail, BUTTON)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, label)

        if logical in self.sorts:
            x = text_left + painter.fontMetrics().horizontalAdvance(label) + 6
            cy = btn.center().y()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            if self.sorts[logical]:
                tri = QPolygonF([QPointF(x, cy + 3), QPointF(x + 7, cy + 3), QPointF(x + 3.5, cy - 3)])
            else:
                tri = QPolygonF([QPointF(x, cy - 3), QPointF(x + 7, cy - 3), QPointF(x + 3.5, cy + 3)])
            painter.drawPolygon(tri)

        # Dropdown or filter button (only while filtering is on).
        if not show_button:
            painter.restore()
            return
        filtered = logical in self.filtered
        if filtered or logical == self._hover_button:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT_SOFT if filtered else theme.HOVER))
            painter.drawRoundedRect(btn, 4, 4)
        icon_name = "filter" if filtered else "caret"
        if not filtered and logical != self._hover_button:
            painter.setPen(QPen(QColor("#c9d0da"), 1))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(btn.adjusted(0, 0, -1, -1), 4, 4)
        color = theme.ACCENT if filtered else theme.TEXT_MUTED
        pm = theme.pixmap(icon_name, color, 14)
        painter.drawPixmap(btn.left() + (BUTTON - 14) // 2, btn.top() + (BUTTON - 14) // 2, pm)
        painter.restore()

    def _has_button(self, logical):
        return self.filter_mode or logical in self.filtered

    def mousePressEvent(self, event):
        logical = self.logicalIndexAt(event.position().toPoint())
        if logical >= 0 and self._has_button(logical) and self.button_rect(logical).contains(event.position().toPoint()):
            r = self.button_rect(logical)
            self.menuRequested.emit(logical, self.viewport().mapToGlobal(QPoint(r.left(), r.bottom() + 4)))
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        logical = self.logicalIndexAt(event.position().toPoint())
        over = (logical if logical >= 0 and self._has_button(logical)
                and self.button_rect(logical).contains(event.position().toPoint()) else -1)
        if over != self._hover_button:
            self._hover_button = over
            self.setCursor(Qt.PointingHandCursor if over >= 0 else Qt.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_button != -1:
            self._hover_button = -1
            self.setCursor(Qt.ArrowCursor)
            self.viewport().update()
        super().leaveEvent(event)


class GridDelegate(QStyledItemDelegate):
    """Paints cells and a clear outline around the current cell."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def paint(self, painter, option, index):
        option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)
        if index == self.view.currentIndex():
            painter.save()
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(option.rect.adjusted(1, 1, -1, -1))
            painter.restore()
