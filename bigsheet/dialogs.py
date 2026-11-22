"""Dialog windows."""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from .engine import AGGREGATES, FILTER_OPS, NO_VALUE_OPS, TYPE_CHOICES, friendly_type
from .model import format_value

EXCEL_EXT = (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")


def _buttons(dialog, ok_text="OK"):
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    box.button(QDialogButtonBox.Ok).setText(ok_text)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


def _check_list(items, checked=False):
    lst = QListWidget()
    for text in items:
        it = QListWidgetItem(text)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        lst.addItem(it)
    return lst


def _checked(lst):
    return [lst.item(i).text() for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]


def mono_font():
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(max(f.pointSize(), 12))
    return f


class ImportDialog(QDialog):
    def __init__(self, path, excel_sheets=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import " + os.path.basename(path))
        self.ext = os.path.splitext(path)[1].lower()
        form = QFormLayout()
        self.name = QLineEdit(os.path.splitext(os.path.basename(path))[0])
        form.addRow("Sheet name", self.name)
        self.is_excel = self.ext in EXCEL_EXT
        self.is_text = not self.is_excel and self.ext not in (".parquet", ".json", ".jsonl", ".ndjson")
        if self.is_text:
            self.delim = QComboBox()
            for label in ("Detect automatically", "Comma ,", "Semicolon ;", "Tab", "Pipe |"):
                self.delim.addItem(label)
            if self.ext == ".tsv":
                self.delim.setCurrentIndex(3)
            form.addRow("Separator", self.delim)
            self.encoding = QComboBox()
            self.encoding.addItems(["UTF-8", "Latin-1 (Windows Western)", "UTF-16"])
            form.addRow("Text encoding", self.encoding)
        if self.is_text or self.is_excel:
            self.header = QComboBox()
            self.header.addItems(["Detect automatically", "First row has column names",
                                  "No column names"])
            if self.is_excel:
                self.header.setCurrentIndex(1)
            form.addRow("Column names", self.header)
            self.all_text = QCheckBox("Treat all columns as text (keeps leading zeros, IDs, codes)")
            form.addRow("", self.all_text)
        if self.is_text:
            self.skip_bad = QCheckBox("Skip rows that cannot be read")
            form.addRow("", self.skip_bad)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        if self.is_excel and excel_sheets:
            layout.addWidget(QLabel("Worksheets to import (each becomes a sheet):"))
            self.sheets = _check_list(excel_sheets)
            self.sheets.item(0).setCheckState(Qt.Checked)
            self.sheets.setMaximumHeight(160)
            layout.addWidget(self.sheets)
        layout.addWidget(_buttons(self, "Import"))
        self.resize(520, self.sizeHint().height())

    def options(self):
        opts = {"sheet_name": self.name.text().strip() or None}
        if self.is_text:
            opts["delimiter"] = [None, ",", ";", "\t", "|"][self.delim.currentIndex()]
            opts["encoding"] = ["utf-8", "latin-1", "utf-16"][self.encoding.currentIndex()]
            opts["skip_bad"] = self.skip_bad.isChecked()
        if self.is_text or self.is_excel:
            opts["header"] = [None, True, False][self.header.currentIndex()]
            opts["all_text"] = self.all_text.isChecked()
        if self.is_excel and hasattr(self, "sheets"):
            opts["excel_sheets"] = _checked(self.sheets)
        return opts

    def accept(self):
        if self.is_excel and hasattr(self, "sheets") and not _checked(self.sheets):
            QMessageBox.warning(self, "Import", "Choose at least one worksheet.")
            return
        super().accept()


class FilterDialog(QDialog):
    def __init__(self, columns, column=None, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter rows")
        self.columns = columns
        form = QFormLayout()
        self.col = QComboBox()
        for name, typ in columns:
            self.col.addItem(f"{name}  ({friendly_type(typ)})", name)
        if column:
            self.col.setCurrentIndex([c for c, _ in columns].index(column))
        self.op = QComboBox()
        for key, label in FILTER_OPS:
            self.op.addItem(label, key)
        self.value = QLineEdit()
        self.value.setPlaceholderText("Value, e.g. Dhaka, 100 or 2024-01-31")
        self.op.currentIndexChanged.connect(self._sync)
        form.addRow("Column", self.col)
        form.addRow("Condition", self.op)
        form.addRow("Value", self.value)
        layout = QVBoxLayout(self)
        if existing:
            layout.addWidget(QLabel("This adds to the filters already applied:\n" + existing))
        layout.addLayout(form)
        note = QLabel("Text comparisons ignore upper and lower case.")
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)
        layout.addWidget(_buttons(self, "Apply filter"))
        self._sync()
        self.resize(460, self.sizeHint().height())

    def _sync(self):
        self.value.setEnabled(self.op.currentData() not in NO_VALUE_OPS)

    def result_filter(self):
        return {"col": self.col.currentData(), "op": self.op.currentData(), "value": self.value.text()}


class SortDialog(QDialog):
    def __init__(self, columns, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sort")
        grid = QGridLayout()
        self.rows = []
        current = current or []
        for i in range(3):
            col = QComboBox()
            col.addItem("(none)" if i else "(choose a column)", None)
            for name, _ in columns:
                col.addItem(name, name)
            direction = QComboBox()
            direction.addItems(["A to Z / smallest first", "Z to A / largest first"])
            if i < len(current):
                col.setCurrentIndex(col.findData(current[i][0]))
                direction.setCurrentIndex(0 if current[i][1] else 1)
            grid.addWidget(QLabel("Sort by" if i == 0 else "then by"), i, 0)
            grid.addWidget(col, i, 1)
            grid.addWidget(direction, i, 2)
            self.rows.append((col, direction))
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(_buttons(self, "Sort"))

    def sorts(self):
        out = []
        for col, direction in self.rows:
            if col.currentData():
                out.append((col.currentData(), direction.currentIndex() == 0))
        return out


FORMULA_HELP = """<b>Write a formula that is calculated for every row.</b> Refer to columns
in square brackets, for example <code>[price]</code>.<br><br>
<b>Examples</b><br>
<code>=[price] * [qty]</code><br>
<code>=IF([qty] &gt; 10, [price] * 0.9, [price])</code><br>
<code>=[price] / SUM([price])</code> &nbsp; (SUM of one column is the whole-column total)<br>
<code>=LEFT([name], 3) &amp; "-" &amp; YEAR([order date])</code><br>
<code>=IFERROR(VALUE([code]), 0)</code><br><br>
<b>Functions</b>: IF, IFERROR, AND, OR, NOT, ISBLANK, ISNUMBER, ROUND, ROUNDUP, ROUNDDOWN,
INT, ABS, SQRT, POWER, MOD, LN, LOG10, EXP, CEILING, FLOOR, LEN, LEFT, RIGHT, MID, UPPER,
LOWER, TRIM, CONCAT, SUBSTITUTE, FIND, SEARCH, VALUE, EXACT, TODAY, NOW, YEAR, MONTH, DAY,
HOUR, MINUTE, SECOND, WEEKDAY, DATE, DATEDIF, EOMONTH, SUM, AVERAGE, MEDIAN, COUNT, MIN,
MAX. Any DuckDB function also works, for example <code>regexp_extract([email], '@(.*)', 1)</code>.<br><br>
The result is stored as a new column. Use <i>Columns, Recalculate formulas</i> after
editing data it depends on."""


class FormulaDialog(QDialog):
    def __init__(self, wb, sheet, parent=None):
        super().__init__(parent)
        self.wb, self.sheet = wb, sheet
        self.setWindowTitle("Add formula column")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("New column name")
        form.addRow("Column name", self.name)
        self.formula = QPlainTextEdit()
        self.formula.setFont(mono_font())
        self.formula.setPlaceholderText("=[price] * [qty]")
        self.formula.setFixedHeight(80)
        form.addRow("Formula", self.formula)
        self.sql_mode = QCheckBox("This is a DuckDB SQL expression (advanced)")
        form.addRow("", self.sql_mode)
        layout.addLayout(form)
        cols = QLabel("Columns: " + ", ".join(f"[{c}]" for c, _ in wb.columns(sheet)))
        cols.setWordWrap(True)
        cols.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(cols)
        row = QHBoxLayout()
        preview = QPushButton("Preview first rows")
        preview.clicked.connect(self._preview)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        row.addWidget(preview)
        row.addWidget(self.preview_label, 1)
        layout.addLayout(row)
        help_box = QTextBrowser()
        help_box.setHtml(FORMULA_HELP)
        help_box.setMinimumHeight(220)
        layout.addWidget(help_box)
        layout.addWidget(_buttons(self, "Add column"))
        self.resize(640, 620)

    def _preview(self):
        try:
            vals = self.wb.preview_formula(self.sheet, self.formula.toPlainText(), self.sql_mode.isChecked())
            self.preview_label.setText("Result: " + ",  ".join("(blank)" if v is None else v for v in vals))
            self.preview_label.setStyleSheet("")
        except Exception as e:
            self.preview_label.setText(str(e))
            self.preview_label.setStyleSheet("color: #c0392b;")

    def values(self):
        return self.name.text().strip(), self.formula.toPlainText().strip(), self.sql_mode.isChecked()

    def accept(self):
        name, formula, _ = self.values()
        if not name or not formula:
            QMessageBox.warning(self, "Formula", "Enter a column name and a formula.")
            return
        super().accept()


class SummarizeDialog(QDialog):
    def __init__(self, sheet, columns, filtered, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Summarize / pivot")
        self.columns = columns
        names = [c for c, _ in columns]
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Group rows by</b> (leave empty for overall totals):"))
        self.groups = _check_list(names)
        self.groups.setMaximumHeight(150)
        layout.addWidget(self.groups)
        layout.addWidget(QLabel("<b>Values to calculate</b>:"))
        self.values_box = QVBoxLayout()
        self.value_rows = []
        layout.addLayout(self.values_box)
        add = QPushButton("Add another value")
        add.clicked.connect(lambda: self._add_value_row())
        layout.addWidget(add, alignment=Qt.AlignLeft)
        self._add_value_row("count")
        form = QFormLayout()
        self.pivot = QComboBox()
        self.pivot.addItem("(no pivot)", None)
        for n in names:
            self.pivot.addItem(n, n)
        form.addRow("Spread values across columns by", self.pivot)
        self.only_view = QCheckBox("Only rows that match the current filter")
        self.only_view.setChecked(filtered)
        self.only_view.setEnabled(filtered)
        form.addRow("", self.only_view)
        self.name = QLineEdit(f"{sheet} summary")
        form.addRow("New sheet name", self.name)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Create summary"))
        self.resize(520, 560)

    def _add_value_row(self, func="sum"):
        row = QHBoxLayout()
        f = QComboBox()
        for key, label in AGGREGATES:
            f.addItem(label, key)
        f.setCurrentIndex(f.findData(func))
        c = QComboBox()
        for n, _ in self.columns:
            c.addItem(n, n)
        f.currentIndexChanged.connect(lambda: c.setEnabled(f.currentData() != "count"))
        c.setEnabled(func != "count")
        remove = QPushButton("Remove")
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(f)
        row.addWidget(QLabel("of"))
        row.addWidget(c, 1)
        row.addWidget(remove)
        entry = (holder, f, c)
        self.value_rows.append(entry)

        def drop():
            if len(self.value_rows) > 1:
                self.value_rows.remove(entry)
                holder.deleteLater()
        remove.clicked.connect(drop)
        self.values_box.addWidget(holder)

    def spec(self):
        aggs = [(f.currentData(), None if f.currentData() == "count" else c.currentData())
                for _, f, c in self.value_rows]
        return {"group_cols": _checked(self.groups), "aggs": aggs,
                "pivot_col": self.pivot.currentData(), "use_view": self.only_view.isChecked(),
                "name": self.name.text().strip() or None}


class DuplicatesDialog(QDialog):
    def __init__(self, sheet, columns, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove duplicates")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Rows count as duplicates when these columns match.\n"
                                "The first row of each group is kept. The result is a new sheet; "
                                "the original is not changed."))
        self.cols = _check_list([c for c, _ in columns], checked=True)
        layout.addWidget(self.cols)
        form = QFormLayout()
        self.name = QLineEdit(f"{sheet} unique")
        form.addRow("New sheet name", self.name)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Remove duplicates"))
        self.resize(420, 460)

    def accept(self):
        if not _checked(self.cols):
            QMessageBox.warning(self, "Remove duplicates", "Choose at least one column.")
            return
        super().accept()

    def spec(self):
        return _checked(self.cols), self.name.text().strip() or None


class TypeDialog(QDialog):
    def __init__(self, column, current_type, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Change type of {column}")
        form = QFormLayout(self)
        form.addRow(QLabel(f"Current type: {friendly_type(current_type)}"))
        self.typ = QComboBox()
        for label, key in TYPE_CHOICES:
            self.typ.addItem(label, key)
        form.addRow("New type", self.typ)
        form.addRow(_buttons(self, "Change"))

    def type_name(self):
        return self.typ.currentData()


class TextDialog(QDialog):
    """Ask for a single line of text."""

    def __init__(self, title, label, text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.edit = QLineEdit(text)
        self.edit.selectAll()
        form.addRow(label, self.edit)
        form.addRow(_buttons(self))
        self.resize(380, self.sizeHint().height())

    def text(self):
        return self.edit.text().strip()


class PasswordDialog(QDialog):
    def __init__(self, filename, message="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Password required")
        form = QFormLayout(self)
        form.addRow(QLabel(message or f"{filename} is protected with a password."))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        form.addRow("Password", self.pw)
        form.addRow(_buttons(self, "Open"))

    def password(self):
        return self.pw.text()


class SaveSecurityDialog(QDialog):
    def __init__(self, crypto_available, required, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Protect workbook")
        self.required = required
        layout = QVBoxLayout(self)
        self.encrypt = QCheckBox("Encrypt this workbook with a password (AES-256)")
        self.encrypt.setChecked(crypto_available)
        self.encrypt.setEnabled(crypto_available and not required)
        layout.addWidget(self.encrypt)
        form = QFormLayout()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        form.addRow("Password", self.pw)
        form.addRow("Repeat password", self.pw2)
        layout.addLayout(form)
        note = QLabel("There is no way to recover the data if the password is lost.")
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)
        if not crypto_available:
            warn = QLabel("Encryption is not available because the encryption component is "
                          "missing. Reinstall with requirements.txt.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #c0392b;")
            layout.addWidget(warn)
        self.encrypt.toggled.connect(self._sync)
        layout.addWidget(_buttons(self, "Save"))
        self._sync()
        self.resize(440, self.sizeHint().height())

    def _sync(self):
        on = self.encrypt.isChecked()
        self.pw.setEnabled(on)
        self.pw2.setEnabled(on)

    def accept(self):
        if self.required and not self.encrypt.isChecked():
            QMessageBox.warning(self, "Protect workbook", "Your organization requires encrypted workbooks.")
            return
        if self.encrypt.isChecked():
            if len(self.pw.text()) < 8:
                QMessageBox.warning(self, "Protect workbook", "Use a password of at least 8 characters.")
                return
            if self.pw.text() != self.pw2.text():
                QMessageBox.warning(self, "Protect workbook", "The two passwords do not match.")
                return
        super().accept()

    def password(self):
        return self.pw.text() if self.encrypt.isChecked() else None


class StatsDialog(QDialog):
    def __init__(self, column, stats, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Statistics for {column}")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Type", QLabel(friendly_type(stats["type"])))
        form.addRow("Rows", QLabel(f"{stats['rows']:,}"))
        form.addRow("Filled", QLabel(f"{stats['filled']:,}"))
        form.addRow("Empty", QLabel(f"{stats['empty']:,}"))
        form.addRow("Unique values (approx.)", QLabel(f"{stats['distinct']:,}"))
        form.addRow("Smallest", QLabel(stats["min"] or ""))
        form.addRow("Largest", QLabel(stats["max"] or ""))
        if stats["avg"] is not None:
            form.addRow("Average", QLabel(format_value(stats["avg"])))
            form.addRow("Sum", QLabel(format_value(stats["sum"])))
        layout.addLayout(form)
        layout.addWidget(QLabel("<b>Most common values</b>"))
        table = QTableWidget(len(stats["top"]), 2)
        table.setHorizontalHeaderLabels(["Value", "Rows"])
        for i, (v, n) in enumerate(stats["top"]):
            table.setItem(i, 0, QTableWidgetItem("(blank)" if v is None else v))
            item = QTableWidgetItem(f"{n:,}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(i, 1, item)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(table)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.resize(420, 520)


class SqlDialog(QDialog):
    """A SQL window. Sheets are tables; refer to them by name, e.g. SELECT * FROM "my sheet"."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setWindowTitle("SQL")
        self.setModal(False)
        layout = QVBoxLayout(self)
        hint = QLabel("Each sheet is a table. Put names with spaces in double quotes, for example "
                      '<code>SELECT city, sum(price) FROM "sales 2024" GROUP BY city</code>.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setFont(mono_font())
        sheets = main.wb.sheets() if main.wb else []
        first = sheets[0] if sheets else "my_sheet"
        self.editor.setPlainText(f'SELECT *\nFROM "{first}"\nLIMIT 100')
        self.editor.setMinimumHeight(150)
        layout.addWidget(self.editor)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run (preview)")
        self.run_btn.setShortcut("Ctrl+Return")
        self.save_btn = QPushButton("Save result as new sheet")
        self.exec_btn = QPushButton("Run statement that changes data")
        row.addWidget(self.run_btn)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        row.addWidget(self.exec_btn)
        layout.addLayout(row)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.run_btn.clicked.connect(self.preview)
        self.save_btn.clicked.connect(self.save)
        self.exec_btn.clicked.connect(self.execute)
        self.resize(900, 640)

    def sql(self):
        return self.editor.toPlainText().strip()

    def preview(self):
        sql = self.sql()
        if not sql:
            return
        if not self.main.wb.is_query(sql):
            self.status.setText("This is not a query. Use 'Run statement that changes data' for it.")
            return
        self.status.setText("Running...")

        def done(result):
            cols, rows, more = result
            self.table.clear()
            self.table.setColumnCount(len(cols))
            self.table.setRowCount(len(rows))
            self.table.setHorizontalHeaderLabels(cols)
            for r, row in enumerate(rows):
                for c, v in enumerate(row):
                    self.table.setItem(r, c, QTableWidgetItem(format_value(v)))
            note = " (showing the first 500)" if more else ""
            self.status.setText(f"{len(rows):,} rows{note}")

        self.main.run_task("Running query", self.main.wb.sql_preview, done,
                           lambda m: self.status.setText(m), sql)

    def save(self):
        sql = self.sql()
        if not sql or not self.main.wb.is_query(sql):
            self.status.setText("Only a query (SELECT ...) can be saved as a sheet.")
            return
        dlg = TextDialog("Save result", "New sheet name", "Query result", self)
        if dlg.exec() != QDialog.Accepted or not dlg.text():
            return

        def done(name):
            self.status.setText(f"Saved as sheet {name}")
            self.main.after_sheets_changed(select=name)

        self.main.run_task("Creating sheet from query", self.main.wb.sql_to_sheet, done,
                           lambda m: self.status.setText(m), sql, dlg.text())

    def execute(self):
        sql = self.sql()
        if not sql:
            return
        ok = QMessageBox.question(
            self, "Run statement",
            "This statement can change or delete data and cannot be undone. Run it?")
        if ok != QMessageBox.Yes:
            return

        def done(_):
            self.status.setText("Done.")
            self.main.after_sheets_changed()

        self.main.run_task("Running statement", self.main.wb.sql_execute, done,
                           lambda m: self.status.setText(m), sql)
