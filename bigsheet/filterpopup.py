"""The dropdown that opens from a column header: sort, filter by condition, filter by values."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import theme
from .engine import FILTER_OPS, LIST_OPS, NO_VALUE_OPS, EngineError, friendly_type

LIMIT = 1000
BLANK = "(Blanks)"


class FilterPopup(QFrame):
    def __init__(self, window, sheet, col, col_type):
        super().__init__(window, Qt.Popup)
        self.setObjectName("Popup")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.window_ref = window
        self.wb = window.wb
        self.sheet, self.col, self.col_type = sheet, col, col_type
        if hasattr(window, "_target_view"):
            self.filters = window._target_view(sheet)[1]
        else:
            self.filters = self.wb.view(sheet).filters
        self.existing = next((f for f in self.filters if f["col"] == col), None)
        self.checked = {}       # value text (or None for blanks) -> checked
        self.truncated = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        title = QLabel(f"<b>{col}</b>  <span style='color:{theme.TEXT_MUTED}'>{friendly_type(col_type)}</span>")
        lay.addWidget(title)

        sort_row = QHBoxLayout()
        az = QPushButton(theme.icon("sort_az"), " Sort A to Z")
        za = QPushButton(theme.icon("sort_za"), " Sort Z to A")
        az.clicked.connect(lambda: self._sort(True))
        za.clicked.connect(lambda: self._sort(False))
        sort_row.addWidget(az)
        sort_row.addWidget(za)
        lay.addLayout(sort_row)

        lay.addWidget(self._section("FILTER BY CONDITION"))
        cond_row = QHBoxLayout()
        self.op = QComboBox()
        self.op.addItem("None", None)
        for key, label in FILTER_OPS:
            self.op.addItem(label, key)
        self.value = QLineEdit()
        self.value.setPlaceholderText("Value")
        self.op.currentIndexChanged.connect(self._sync_condition)
        cond_row.addWidget(self.op, 1)
        cond_row.addWidget(self.value, 1)
        lay.addLayout(cond_row)

        lay.addWidget(self._section("FILTER BY VALUES"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search values")
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        links = QHBoxLayout()
        all_btn = QPushButton("Select all")
        none_btn = QPushButton("Clear")
        for b in (all_btn, none_btn):
            b.setProperty("flat", True)
            b.setCursor(Qt.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        links.addWidget(all_btn)
        links.addWidget(none_btn)
        links.addStretch(1)
        links.addWidget(self.count_label)
        lay.addLayout(links)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setMinimumHeight(220)
        self.tree.itemChanged.connect(self._item_changed)
        lay.addWidget(self.tree, 1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(self.note)

        buttons = QHBoxLayout()
        clear = QPushButton("Clear filter")
        clear.setEnabled(self.existing is not None)
        clear.clicked.connect(self._clear)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        ok = QPushButton("Apply")
        ok.setProperty("primary", True)
        ok.setDefault(True)
        ok.clicked.connect(self._apply)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        lay.addLayout(buttons)

        self._init_from_existing()
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self._load_values)
        self.search.textChanged.connect(lambda: self._timer.start())
        self._load_values()
        self.resize(340, 520)

    @staticmethod
    def _section(text):
        lab = QLabel(text)
        lab.setObjectName("SectionLabel")
        return lab

    def _sync_condition(self):
        op = self.op.currentData()
        self.value.setEnabled(op is not None and op not in NO_VALUE_OPS)

    def _init_from_existing(self):
        f = self.existing
        self.mode = "not_in"
        self.allowed = None
        if f and f["op"] not in LIST_OPS:
            self.op.setCurrentIndex(max(0, self.op.findData(f["op"])))
            self.value.setText(str(f.get("value") or ""))
        elif f and f["op"] == "in":
            self.mode = "in"
            self.allowed = set(f.get("values") or [])
            if f.get("blanks"):
                self.allowed.add(None)
        elif f and f["op"] == "not_in":
            for v in f.get("values") or []:
                self.checked[v] = False
            if not f.get("blanks"):
                self.checked[None] = False
        self._sync_condition()

    def _default_checked(self, value):
        if value in self.checked:
            return self.checked[value]
        if self.allowed is not None:
            return value in self.allowed
        return True

    def _load_values(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rows, self.truncated = self.wb.distinct_values(
                self.sheet, self.col, self.filters, self.search.text().strip() or None, LIMIT)
        except EngineError as e:
            rows, self.truncated = [], False
            self.note.setText(str(e))
        finally:
            QApplication.restoreOverrideCursor()
        self.tree.blockSignals(True)
        self.tree.clear()
        for value, n in rows:
            item = QTreeWidgetItem([BLANK if value is None else value, f"{n:,}"])
            item.setData(0, Qt.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if self._default_checked(value) else Qt.Unchecked)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setForeground(1, QGuiApplication.palette().placeholderText())
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self.count_label.setText(f"{len(rows):,}{'+' if self.truncated else ''} values")
        self.note.setText(f"Showing the first {LIMIT:,} values. Search to find others."
                          if self.truncated else "")

    def _item_changed(self, item, column):
        if column == 0:
            self.checked[item.data(0, Qt.UserRole)] = item.checkState(0) == Qt.Checked

    def _set_all(self, on):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
            self.checked[item.data(0, Qt.UserRole)] = on
        self.tree.blockSignals(False)
        if on and not self.search.text():
            self.checked = {}
            self.allowed = None
            self.mode = "not_in"

    def _value_filter(self):
        """The filter implied by the checkboxes, or None when everything is checked."""
        loaded = [self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount())]
        if self.allowed is not None:
            allowed = set(self.allowed)
            for v, on in self.checked.items():
                if on:
                    allowed.add(v)
                else:
                    allowed.discard(v)
            if not self.search.text() and not self.truncated and allowed >= set(loaded):
                return None
            return {"col": self.col, "op": "in", "values": sorted(v for v in allowed if v is not None),
                    "blanks": None in allowed}
        unchecked = {v for v, on in self.checked.items() if not on}
        if not unchecked:
            return None
        complete = not self.search.text() and not self.truncated
        if complete:
            kept = [v for v in loaded if v not in unchecked]
            if len(kept) < len(unchecked):
                return {"col": self.col, "op": "in", "values": [v for v in kept if v is not None],
                        "blanks": None in kept}
        return {"col": self.col, "op": "not_in", "values": sorted(v for v in unchecked if v is not None),
                "blanks": None not in unchecked}

    def _apply(self):
        op = self.op.currentData()
        if op is not None:
            new = {"col": self.col, "op": op, "value": self.value.text()}
            if op not in NO_VALUE_OPS and not self.value.text().strip():
                self.note.setText("Enter a value for the condition, or set the condition to None.")
                return
            try:
                self.wb.validate_filter_value(self.sheet, self.col, op, new["value"])
            except EngineError as e:
                self.note.setText(str(e))
                return
        else:
            new = self._value_filter()
        self.close()
        self.window_ref.set_column_filter(self.col, new)

    def _clear(self):
        self.close()
        self.window_ref.set_column_filter(self.col, None)

    def _sort(self, ascending):
        self.close()
        self.window_ref.sort_by(self.col, ascending)
