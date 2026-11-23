"""Side panel for building pivot tables by dragging fields into Rows, Columns and Values."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDockWidget, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget)

from . import theme
from .engine import AGGREGATES, EngineError, is_numeric

FIELD_ROLE = Qt.UserRole
FUNC_ROLE = Qt.UserRole + 1
LABELS = dict(AGGREGATES)


def value_label(func, col):
    return "Count of rows" if func == "count" else f"{LABELS.get(func, func)} of {col}"


class FieldList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSelectionMode(QAbstractItemView.SingleSelection)


class ZoneList(QListWidget):
    """A drop zone (Rows, Columns or Values)."""

    changed = Signal()

    def __init__(self, panel, kind, placeholder):
        super().__init__()
        self.panel, self.kind = panel, kind
        self.placeholder = placeholder
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setMinimumHeight(70)
        self.setMaximumHeight(130)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(lambda it: self._menu_for(it, self.mapToGlobal(self.visualItemRect(it).center())))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            from PySide6.QtGui import QColor, QPainter
            p = QPainter(self.viewport())
            p.setPen(QColor("#9aa1ac"))
            p.drawText(self.viewport().rect(), Qt.AlignCenter, self.placeholder)
            p.end()

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if isinstance(e.source(), (FieldList, ZoneList)) else e.ignore()

    def dragMoveEvent(self, e):
        e.acceptProposedAction() if isinstance(e.source(), (FieldList, ZoneList)) else e.ignore()

    def dropEvent(self, e):
        src = e.source()
        items = src.selectedItems()
        if not items:
            return
        field = items[0].data(FIELD_ROLE)
        if isinstance(src, ZoneList) and src is not self:
            src.takeItem(src.row(items[0]))
            src.changed.emit()
        elif src is self:
            row = self.row(items[0])
            target = self.indexAt(e.position().toPoint()).row()
            moved = self.takeItem(row)
            self.insertItem(target if target >= 0 else self.count(), moved)
            e.accept()
            self.changed.emit()
            return
        self.add_field(field)
        e.accept()

    def add_field(self, field, func=None):
        if self.kind == "columns":
            self.clear()
        if self.kind != "values":
            for i in range(self.count()):
                if self.item(i).data(FIELD_ROLE) == field:
                    return
        if self.kind == "values":
            func = func or self.panel.default_func(field)
            item = QListWidgetItem(value_label(func, field))
            item.setData(FUNC_ROLE, func)
        else:
            item = QListWidgetItem(field)
        item.setData(FIELD_ROLE, field)
        self.addItem(item)
        self.changed.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.currentItem():
            self.takeItem(self.currentRow())
            self.changed.emit()
            return
        super().keyPressEvent(e)

    def _menu(self, pos):
        item = self.itemAt(pos)
        if item:
            self._menu_for(item, self.mapToGlobal(pos))

    def _menu_for(self, item, global_pos):
        menu = QMenu(self)
        if self.kind == "values":
            field = item.data(FIELD_ROLE)
            numeric = self.panel.is_numeric(field)
            for key, label in AGGREGATES:
                if key in ("sum", "avg", "median") and not numeric:
                    continue
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(item.data(FUNC_ROLE) == key)
                act.triggered.connect(lambda _=False, k=key, it=item: self._set_func(it, k))
            menu.addSeparator()
        menu.addAction("Remove", lambda: (self.takeItem(self.row(item)), self.changed.emit()))
        menu.exec(global_pos)

    def _set_func(self, item, func):
        item.setData(FUNC_ROLE, func)
        item.setText(value_label(func, item.data(FIELD_ROLE)))
        self.changed.emit()

    def fields(self):
        return [self.item(i).data(FIELD_ROLE) for i in range(self.count())]

    def values(self):
        return [[self.item(i).data(FUNC_ROLE), None if self.item(i).data(FUNC_ROLE) == "count"
                 else self.item(i).data(FIELD_ROLE)] for i in range(self.count())]


class PivotPanel(QDockWidget):
    """Edits the pivot sheet that is open, or offers to create one from the current sheet."""

    def __init__(self, window):
        super().__init__("Pivot table", window)
        self.window_ref = window
        self.setObjectName("PivotPanel")
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.sheet = None
        self.spec = None
        self.types = {}
        self._loading = False

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)
        self.source_label = QLabel("")
        self.source_label.setWordWrap(True)
        lay.addWidget(self.source_label)
        self.create_btn = QPushButton(theme.icon("pivot", "#ffffff"), " Create pivot table")
        self.create_btn.setProperty("primary", True)
        self.create_btn.clicked.connect(lambda: self.window_ref.insert_pivot())
        lay.addWidget(self.create_btn)

        self.editor = QWidget()
        ed = QVBoxLayout(self.editor)
        ed.setContentsMargins(0, 0, 0, 0)
        ed.setSpacing(6)
        ed.addWidget(self._section("FIELDS"))
        self.field_search = QLineEdit()
        self.field_search.setPlaceholderText("Search fields")
        self.field_search.textChanged.connect(self._filter_fields)
        ed.addWidget(self.field_search)
        self.fields = FieldList()
        self.fields.setMinimumHeight(120)
        self.fields.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fields.customContextMenuRequested.connect(self._field_menu)
        self.fields.itemDoubleClicked.connect(lambda it: self.values.add_field(it.data(FIELD_ROLE)))
        ed.addWidget(self.fields, 1)
        hint = QLabel("Drag fields into the boxes below, or right-click a field.")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        hint.setWordWrap(True)
        ed.addWidget(hint)
        self.rows = ZoneList(self, "rows", "Drop fields to group rows")
        self.cols = ZoneList(self, "columns", "Drop one field to spread across columns")
        self.values = ZoneList(self, "values", "Drop fields to calculate")
        for label, zone in (("ROWS", self.rows), ("COLUMNS", self.cols), ("VALUES", self.values)):
            ed.addWidget(self._section(label))
            ed.addWidget(zone)
            zone.changed.connect(self._changed)
        self.use_filters = QCheckBox("Only rows that match the source sheet's filters")
        self.use_filters.toggled.connect(self._changed)
        ed.addWidget(self.use_filters)
        lay.addWidget(self.editor, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        lay.addWidget(self.status)
        self.setWidget(body)
        self.setMinimumWidth(270)
        self._timer = QTimer(self, singleShot=True, interval=450, timeout=self._apply)

    @staticmethod
    def _section(text):
        lab = QLabel(text)
        lab.setObjectName("SectionLabel")
        return lab

    def is_numeric(self, field):
        return is_numeric(self.types.get(field, "VARCHAR"))

    def default_func(self, field):
        return "sum" if self.is_numeric(field) else "count_distinct"

    # ----------------------------------------------------------------- binding

    def bind(self, sheet):
        """Show the editor for a pivot sheet, or the create button for a normal sheet."""
        wb = self.window_ref.wb
        self.sheet = sheet
        self.spec = wb.pivot_spec(sheet) if (wb and sheet) else None
        self._loading = True
        for z in (self.rows, self.cols, self.values):
            z.clear()
        self.fields.clear()
        self.status.setText("")
        if not sheet:
            self.source_label.setText("Open or import a sheet first.")
            self.create_btn.hide()
            self.editor.hide()
        elif self.spec is None:
            self.source_label.setText(f"Summarize <b>{sheet}</b> in a new pivot sheet that updates as "
                                      "you change it.")
            self.create_btn.show()
            self.editor.hide()
        else:
            source = self.spec.get("source")
            self.source_label.setText(f"Source: <b>{source}</b>")
            self.create_btn.hide()
            self.editor.show()
            try:
                self.types = dict(wb.columns(source))
            except EngineError:
                self.types = {}
                self.status.setText(f"The source sheet {source} no longer exists.")
            for name in self.types:
                item = QListWidgetItem(name)
                item.setData(FIELD_ROLE, name)
                item.setToolTip(self.types[name])
                self.fields.addItem(item)
            for f in self.spec.get("rows", []):
                self.rows.add_field(f)
            for f in self.spec.get("columns", []):
                self.cols.add_field(f)
            for func, col in self.spec.get("values", []):
                self.values.add_field(col if col else (next(iter(self.types), "")), func)
            self.use_filters.setChecked(bool(self.spec.get("filters")))
            self._filter_fields()
        self._loading = False

    def _filter_fields(self):
        text = self.field_search.text().lower()
        for i in range(self.fields.count()):
            item = self.fields.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _field_menu(self, pos):
        item = self.fields.itemAt(pos)
        if not item:
            return
        field = item.data(FIELD_ROLE)
        menu = QMenu(self)
        menu.addAction("Add to Rows", lambda: self.rows.add_field(field))
        menu.addAction("Add to Columns", lambda: self.cols.add_field(field))
        menu.addAction("Add to Values", lambda: self.values.add_field(field))
        menu.exec(self.fields.mapToGlobal(pos))

    # ------------------------------------------------------------------ update

    def current_spec(self):
        spec = dict(self.spec or {})
        spec["rows"] = self.rows.fields()
        spec["columns"] = self.cols.fields()
        spec["values"] = self.values.values()
        source = spec.get("source")
        wb = self.window_ref.wb
        if self.use_filters.isChecked() and wb and source:
            spec["filters"] = [dict(f) for f in wb.view(source).filters] or spec.get("filters") or []
        else:
            spec["filters"] = []
        return spec

    def _changed(self, *_):
        if not self._loading and self.spec is not None:
            self.status.setText("Updating...")
            self._timer.start()

    def _apply(self):
        if self.window_ref.busy:
            self._timer.start()
            return
        self.window_ref.update_pivot(self.sheet, self.current_spec())
