"""The dropdown that opens from a column header: sort, and filter by values with search."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import theme
from .engine import LIST_OPS, EngineError

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
        self.truncated = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        for text, icon_name, ascending in (("Sort A to Z", "sort_az", True), ("Sort Z to A", "sort_za", False)):
            b = QPushButton(theme.icon(icon_name), "  " + text)
            b.setObjectName("MenuRow")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, a=ascending: self._sort(a))
            lay.addWidget(b)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {theme.BORDER};")
        lay.addWidget(line)

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
        clear.setProperty("flat", True)
        clear.setVisible(self.existing is not None)
        clear.clicked.connect(self._clear)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        ok = QPushButton("OK")
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
        self.resize(300, 470)

    @staticmethod
    def _section(text):
        lab = QLabel(text)
        lab.setObjectName("SectionLabel")
        return lab

    def _init_from_existing(self):
        """Selection model: every value is ticked when base is True, except those in exceptions
        (and the other way round when base is False). This covers values not shown in the list."""
        f = self.existing
        self.base = True
        self.exceptions = set()
        if f and f["op"] == "in":
            self.base = False
            self.exceptions = set(f.get("values") or [])
            if f.get("blanks"):
                self.exceptions.add(None)
        elif f and f["op"] == "not_in":
            self.exceptions = set(f.get("values") or [])
            if not f.get("blanks"):
                self.exceptions.add(None)

    def _default_checked(self, value):
        return self.base != (value in self.exceptions)

    def _set_checked(self, value, on):
        if on == self.base:
            self.exceptions.discard(value)
        else:
            self.exceptions.add(value)

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
            self._set_checked(item.data(0, Qt.UserRole), item.checkState(0) == Qt.Checked)

    def _set_all(self, on):
        if self.search.text().strip():
            # Only the values found by the search.
            for i in range(self.tree.topLevelItemCount()):
                self._set_checked(self.tree.topLevelItem(i).data(0, Qt.UserRole), on)
        else:
            # Every value in the column, including those not listed.
            self.base = on
            self.exceptions = set()
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setCheckState(0, Qt.Checked if self._default_checked(item.data(0, Qt.UserRole)) else Qt.Unchecked)
        self.tree.blockSignals(False)

    def _value_filter(self):
        """The filter for the current ticks: None when every value is ticked."""
        if self.base:
            if not self.exceptions:
                return None
            return {"col": self.col, "op": "not_in",
                    "values": sorted(v for v in self.exceptions if v is not None),
                    "blanks": None not in self.exceptions}
        return {"col": self.col, "op": "in", "values": sorted(v for v in self.exceptions if v is not None),
                "blanks": None in self.exceptions}

    def _apply(self):
        new = self._value_filter()
        if new is not None and new["op"] == "in" and not new["values"] and not new["blanks"]:
            self.note.setText("Tick at least one value, or click Select all.")
            return
        if new is None and self.existing is not None and self.existing["op"] not in LIST_OPS:
            new = self.existing  # keep a condition filter set from the Data menu
        self.close()
        self.window_ref.set_column_filter(self.col, new)

    def _clear(self):
        self.close()
        self.window_ref.set_column_filter(self.col, None)

    def _sort(self, ascending):
        self.close()
        self.window_ref.sort_by(self.col, ascending)
