"""The main BigSheet window."""

import os
import time

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QStackedWidget, QTableView, QTabBar, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

import duckdb

from . import config
from .dialogs import (EXCEL_EXT, DuplicatesDialog, FilterDialog, FormulaDialog, ImportDialog,
                      PasswordDialog, SaveSecurityDialog, SortDialog, SqlDialog, StatsDialog,
                      SummarizeDialog, TextDialog, TypeDialog, FORMULA_HELP)
from .engine import EngineError, PasswordRequired, Workbook, WrongPassword, friendly_type
from .model import Runner, SheetModel, column_letters

WORKBOOK_EXT = (".bigsheet", ".duckdb", ".db")
DATA_EXT = (".csv", ".tsv", ".txt", ".parquet", ".json", ".jsonl", ".ndjson") + EXCEL_EXT
OPEN_FILTER = ("All supported files (*.bigsheet *.duckdb *.db *.csv *.tsv *.txt *.xlsx *.xlsm "
               "*.xls *.xlsb *.ods *.parquet *.json *.jsonl *.ndjson);;"
               "BigSheet workbooks (*.bigsheet *.duckdb *.db);;"
               "CSV and text (*.csv *.tsv *.txt);;Excel (*.xlsx *.xlsm *.xls *.xlsb *.ods);;"
               "Parquet (*.parquet);;JSON (*.json *.jsonl *.ndjson);;All files (*)")
SMALL_EDIT_CELLS = 20_000
UNDOABLE_CLEAR_CELLS = 100_000
MAX_COPY_CELLS = 2_000_000


def fmt_num(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return f"{int(v):,}"
    return format(v, ",.10g")


class GridView(QTableView):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state() != QAbstractItemView.EditingState:
            self.window_ref.clear_cells()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.policy = config.load_policy()
        self.runner = Runner(threads=2)
        self.stats_runner = Runner(threads=1)
        self.wb = None
        self.models = {}
        self.busy = False
        self._busy_started = 0.0
        self._stats_request = 0
        self._import_queue = []
        self.sql_dialog = None
        self.setAcceptDrops(True)
        self.resize(1280, 800)
        self._build_ui()
        self._build_actions()
        self.stats_timer = QTimer(self, singleShot=True, interval=250, timeout=self._compute_stats)
        self.busy_timer = QTimer(self, interval=500, timeout=self._tick_busy)
        self.new_workbook(confirm=False)

    # ================================================================ UI setup

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(4, 4, 4, 0)
        outer.setSpacing(4)

        bar = QHBoxLayout()
        self.name_box = QLabel("")
        self.name_box.setMinimumWidth(170)
        self.name_box.setStyleSheet("QLabel { border: 1px solid palette(mid); padding: 3px 6px; }")
        self.cell_edit = QLineEdit()
        self.cell_edit.setPlaceholderText("Cell value. Type and press Enter to change it.")
        self.cell_edit.returnPressed.connect(self._commit_cell_edit)
        self.formula_hint = QLabel("")
        self.formula_hint.setStyleSheet("color: gray;")
        bar.addWidget(self.name_box)
        bar.addWidget(self.cell_edit, 1)
        bar.addWidget(self.formula_hint)
        outer.addLayout(bar)

        self.view_bar = QWidget()
        vb = QHBoxLayout(self.view_bar)
        vb.setContentsMargins(6, 2, 6, 2)
        self.view_label = QLabel("")
        self.view_label.setWordWrap(True)
        clear_btn = QPushButton("Clear sort and filters")
        clear_btn.clicked.connect(self.clear_view)
        vb.addWidget(self.view_label, 1)
        vb.addWidget(clear_btn)
        self.view_bar.setStyleSheet("background: palette(alternate-base);")
        self.view_bar.hide()
        outer.addWidget(self.view_bar)

        self.find_bar = QWidget()
        fb = QHBoxLayout(self.find_bar)
        fb.setContentsMargins(0, 0, 0, 0)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find text in this sheet (Enter = next, Shift+Enter = previous)")
        self.find_edit.returnPressed.connect(lambda: self.find(backward=False))
        prev_btn = QPushButton("Previous")
        next_btn = QPushButton("Next")
        close_btn = QPushButton("Close")
        prev_btn.clicked.connect(lambda: self.find(backward=True))
        next_btn.clicked.connect(lambda: self.find(backward=False))
        close_btn.clicked.connect(self.find_bar.hide)
        self.find_status = QLabel("")
        fb.addWidget(QLabel("Find:"))
        fb.addWidget(self.find_edit, 1)
        fb.addWidget(prev_btn)
        fb.addWidget(next_btn)
        fb.addWidget(self.find_status)
        fb.addWidget(close_btn)
        self.find_bar.hide()
        outer.addWidget(self.find_bar)

        self.stack = QStackedWidget()
        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        wl.addStretch(1)
        title = QLabel("<h2>BigSheet</h2>")
        title.setAlignment(Qt.AlignCenter)
        info = QLabel("Drop a CSV, Excel, Parquet or JSON file here, or click the button below.\n"
                      "Files of any size are converted once into a fast, private workbook.")
        info.setAlignment(Qt.AlignCenter)
        btn = QPushButton("Import a file")
        btn.setFixedWidth(200)
        btn.clicked.connect(self.import_dialog)
        wl.addWidget(title)
        wl.addWidget(info)
        wl.addSpacing(12)
        wl.addWidget(btn, alignment=Qt.AlignCenter)
        wl.addStretch(2)
        self.stack.addWidget(welcome)

        self.table = GridView(self)
        self.table.setWordWrap(False)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        vh = self.table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setDefaultSectionSize(max(22, self.table.fontMetrics().height() + 6))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        hh.setHighlightSections(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._cell_menu)
        self.stack.addWidget(self.table)
        outer.addWidget(self.stack, 1)

        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setMovable(False)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_menu)
        add_tab = QToolButton()
        add_tab.setText("+")
        add_tab.setToolTip("Import a file as a new sheet")
        add_tab.clicked.connect(self.import_dialog)
        tabs_row.addWidget(add_tab)
        tabs_row.addWidget(self.tabs, 1)
        outer.addLayout(tabs_row)
        self.setCentralWidget(central)

        sb = self.statusBar()
        self.msg_label = QLabel("")
        self.stats_label = QLabel("")
        self.count_label = QLabel("")
        self.busy_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(140)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_task)
        sb.addWidget(self.msg_label, 1)
        sb.addPermanentWidget(self.stats_label)
        sb.addPermanentWidget(self.busy_label)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)
        sb.addPermanentWidget(self.count_label)
        for w in (self.busy_label, self.progress, self.cancel_btn):
            w.hide()

    def _act(self, menu, text, slot, shortcut=None, mutates=False, needs_sheet=True, tip=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
        a.setProperty("mutates", mutates)
        a.setProperty("needs_sheet", needs_sheet)
        if menu is not None:
            menu.addAction(a)
        self._actions.append(a)
        return a

    def _build_actions(self):
        self._actions = []
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self.a_new = self._act(m, "New workbook", self.new_workbook, QKeySequence.New, needs_sheet=False)
        self.a_open = self._act(m, "Open...", self.open_dialog, QKeySequence.Open, needs_sheet=False,
                                tip="Open a workbook, or import a data file into this workbook")
        self.a_import = self._act(m, "Import file...", self.import_dialog, "Ctrl+I", mutates=True,
                                  needs_sheet=False)
        m.addSeparator()
        self.a_save = self._act(m, "Save", self.save, QKeySequence.Save, needs_sheet=False)
        self.a_save_as = self._act(m, "Save as...", self.save_as, QKeySequence.SaveAs, needs_sheet=False)
        self.a_export = self._act(m, "Export sheet...", self.export_sheet, "Ctrl+E")
        m.addSeparator()
        self._act(m, "Quit", self.close, QKeySequence.Quit, needs_sheet=False)

        m = mb.addMenu("&Edit")
        self.a_undo = self._act(m, "Undo", self.undo, QKeySequence.Undo, mutates=True)
        self.a_redo = self._act(m, "Redo", self.redo, QKeySequence.Redo, mutates=True)
        m.addSeparator()
        self.a_copy = self._act(m, "Copy", self.copy, QKeySequence.Copy)
        self.a_paste = self._act(m, "Paste", self.paste, QKeySequence.Paste, mutates=True)
        self.a_clear = self._act(m, "Clear cells", self.clear_cells, mutates=True)
        m.addSeparator()
        self.a_find = self._act(m, "Find...", self.show_find, QKeySequence.Find)
        self._act(m, "Go to row...", self.go_to_row, "Ctrl+G")
        m.addSeparator()
        self.a_ins = self._act(m, "Insert row above", self.insert_row_above, "Ctrl+Shift+=", mutates=True)
        self.a_app = self._act(m, "Add row at end", self.append_row, mutates=True)
        self.a_del = self._act(m, "Delete selected rows", self.delete_rows, "Ctrl+-", mutates=True)

        m = mb.addMenu("&Data")
        self.a_sort_az = self._act(m, "Sort A to Z (current column)", lambda: self.sort_current(True), mutates=True)
        self.a_sort_za = self._act(m, "Sort Z to A (current column)", lambda: self.sort_current(False), mutates=True)
        self._act(m, "Custom sort...", self.custom_sort, mutates=True)
        m.addSeparator()
        self.a_filter = self._act(m, "Filter...", self.add_filter, "Ctrl+Shift+L", mutates=True)
        self._act(m, "Filter by selected value", lambda: self.filter_by_value("eq"), mutates=True)
        self._act(m, "Hide rows with selected value", lambda: self.filter_by_value("ne"), mutates=True)
        self.a_clear_view = self._act(m, "Clear sort and filters", self.clear_view, mutates=True)
        m.addSeparator()
        self.a_summary = self._act(m, "Summarize / pivot...", self.summarize, mutates=True)
        self._act(m, "Remove duplicates...", self.remove_duplicates, mutates=True)

        m = mb.addMenu("&Columns")
        self.a_formula = self._act(m, "Add formula column...", self.add_formula, mutates=True)
        self._act(m, "Add empty column...", self.add_empty_column, mutates=True)
        self._act(m, "Recalculate formulas", self.recalc_formulas, mutates=True)
        m.addSeparator()
        self._act(m, "Rename column...", lambda: self.rename_column(), mutates=True)
        self._act(m, "Change column type...", lambda: self.change_type(), mutates=True)
        self._act(m, "Delete column", lambda: self.delete_column(), mutates=True)
        m.addSeparator()
        self._act(m, "Column statistics...", lambda: self.column_stats())

        m = mb.addMenu("&Tools")
        self.a_sql = self._act(m, "SQL window...", self.open_sql, "Ctrl+Shift+Q", needs_sheet=False)

        m = mb.addMenu("&Help")
        self._act(m, "Formula help", self.formula_help, needs_sheet=False)
        self._act(m, "About BigSheet", self.about, needs_sheet=False)

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        for a in (self.a_open, self.a_import, self.a_save, None, self.a_undo, self.a_redo, None,
                  self.a_sort_az, self.a_sort_za, self.a_filter, self.a_clear_view, None,
                  self.a_formula, self.a_summary, None, self.a_find, self.a_sql, self.a_export):
            if a is None:
                tb.addSeparator()
            else:
                tb.addAction(a)
        self.a_sort_az.setIconText("Sort A-Z")
        self.a_sort_za.setIconText("Sort Z-A")
        self.a_clear_view.setIconText("Clear filters")
        self.a_formula.setIconText("Formula column")
        self.a_summary.setIconText("Summarize")
        self.a_sql.setIconText("SQL")
        self.a_export.setIconText("Export")
        self.a_import.setIconText("Import")
        self.addToolBar(tb)

    # ============================================================== helpers

    @property
    def sheet(self):
        i = self.tabs.currentIndex()
        return self.tabs.tabData(i) if i >= 0 else None

    @property
    def model(self):
        return self.models.get(self.sheet)

    def _current_col(self):
        idx = self.table.currentIndex()
        m = self.model
        if not m or not idx.isValid():
            return None
        return m.cols[idx.column()][0]

    def info(self, text, ms=6000):
        self.msg_label.setText(text)
        if ms:
            QTimer.singleShot(ms, lambda t=text: self.msg_label.text() == t and self.msg_label.setText(""))

    def error(self, text, title="BigSheet"):
        QMessageBox.warning(self, title, text)

    def _update_actions(self):
        has_sheet = self.sheet is not None
        for a in self._actions:
            enabled = True
            if a.property("needs_sheet") and not has_sheet:
                enabled = False
            if a.property("mutates") and self.busy:
                enabled = False
            a.setEnabled(enabled)
        if self.wb:
            self.a_undo.setEnabled(self.a_undo.isEnabled() and bool(self.wb.undo_stack))
            self.a_redo.setEnabled(self.a_redo.isEnabled() and bool(self.wb.redo_stack))
        self.a_export.setEnabled(self.a_export.isEnabled() and self.policy["allow_export"])
        self.a_copy.setEnabled(self.a_copy.isEnabled() and self.policy["allow_copy"])
        self.a_sql.setEnabled(self.a_sql.isEnabled() and self.policy["allow_sql"])

    def _update_title(self):
        if not self.wb:
            return
        name = os.path.basename(self.wb.path) if self.wb.path else "Untitled"
        lock = "  (encrypted)" if (self.wb.encrypted and self.wb.path) else ""
        self.setWindowTitle(f"{name}{lock} - BigSheet")

    def _update_status(self):
        m = self.model
        if not m:
            self.count_label.setText("")
            self.view_bar.hide()
            return
        v = self.wb.view(self.sheet)
        if v.is_identity:
            self.count_label.setText(f"  {v.count:,} rows  ")
            self.view_bar.hide()
        else:
            self.count_label.setText(f"  {v.count:,} of {v.total:,} rows  ")
            self.view_label.setText(v.describe())
            self.view_bar.show()

    # ============================================================ busy tasks

    def run_task(self, label, fn, on_done=None, on_fail=None, *args, freeze=False):
        if self.busy:
            self.error("Please wait for the current task to finish (or cancel it).")
            return None
        self.busy = True
        self._busy_started = time.time()
        self.busy_label.setText(label)
        for w in (self.busy_label, self.progress, self.cancel_btn):
            w.show()
        self.busy_timer.start()
        for m in self.models.values():
            m.read_only = True
            m.frozen = freeze
        self._update_actions()

        def finish():
            self.busy = False
            self.busy_timer.stop()
            for w in (self.busy_label, self.progress, self.cancel_btn):
                w.hide()
            for m in self.models.values():
                m.read_only = False
                m.frozen = False
            self._update_actions()

        def done(result):
            finish()
            if on_done:
                on_done(result)
            self._update_actions()

        def fail(msg):
            finish()
            if on_fail:
                on_fail(msg)
            else:
                self.error(msg)

        wb = self.wb

        def work(*a):
            try:
                return fn(*a)
            finally:
                if wb is not None:
                    wb.release_cursor()

        return self.runner.start(work, done, fail, *args)

    def _tick_busy(self):
        secs = int(time.time() - self._busy_started)
        base = self.busy_label.text().split("  (")[0]
        self.busy_label.setText(f"{base}  ({secs}s)")

    def cancel_task(self):
        if self.wb:
            self.wb.interrupt_all()
        self.info("Cancelling...")

    # ============================================================ workbook

    def _workbook_has_unsaved_data(self):
        return self.wb is not None and self.wb.untitled and bool(self.wb.sheets())

    def _confirm_discard(self):
        if not self._workbook_has_unsaved_data():
            return True
        r = QMessageBox.question(
            self, "Save workbook?",
            "This workbook has not been saved yet. Save it before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_as(blocking=True)
        return True

    def _set_workbook(self, wb):
        old = self.wb
        if self.sql_dialog:
            self.sql_dialog.close()
            self.sql_dialog = None
        self.table.setModel(None)
        self.models = {}
        self.wb = wb
        if old is not None:
            try:
                old.checkpoint()
            except Exception:
                pass
            old.close()
        self.after_sheets_changed()
        self._update_title()

    def new_workbook(self, confirm=True):
        if self.busy:
            return
        if confirm and not self._confirm_discard():
            return
        self._set_workbook(Workbook())
        self.info("New workbook. Import a file to start.")

    def open_dialog(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open", "", OPEN_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in WORKBOOK_EXT:
            self.open_workbook(path)
        else:
            self.import_paths([path])

    def open_workbook(self, path):
        if self.busy or not self._confirm_discard():
            return
        password = None
        message = ""
        for _ in range(5):
            try:
                wb = Workbook(path, password)
            except PasswordRequired:
                pass
            except WrongPassword:
                message = "That password is not correct. Try again."
            except (EngineError, duckdb.Error) as e:
                self.error(f"Could not open {os.path.basename(path)}:\n\n{e}")
                return
            else:
                self._set_workbook(wb)
                self.info(f"Opened {os.path.basename(path)}")
                return
            dlg = PasswordDialog(os.path.basename(path), message, self)
            if dlg.exec() != QDialog.Accepted:
                return
            password = dlg.password()
        self.error("Too many incorrect passwords.")

    def save(self):
        if not self.wb or self.busy:
            return
        if self.wb.untitled:
            self.save_as()
            return
        try:
            self.wb.checkpoint()
            self.info("Saved. (Changes are written to the workbook file as you work.)")
        except EngineError as e:
            self.error(str(e))

    def save_as(self, blocking=False):
        if not self.wb or self.busy:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Save workbook as", "Workbook.bigsheet",
                                              "BigSheet workbook (*.bigsheet)")
        if not path:
            return False
        if not path.lower().endswith(".bigsheet"):
            path += ".bigsheet"
        if self.wb.path and os.path.abspath(path) == self.wb.path:
            self.error("Choose a different file name than the open workbook.")
            return False
        dlg = SaveSecurityDialog(self.wb.crypto_available, self.policy["require_encryption"], self)
        if dlg.exec() != QDialog.Accepted:
            return False
        password = dlg.password()
        if os.path.exists(path):
            try:
                os.remove(path)
                if os.path.exists(path + ".wal"):
                    os.remove(path + ".wal")
            except OSError as e:
                self.error(f"Cannot replace {path}:\n{e}")
                return False
        if blocking:
            try:
                self.wb.save_as(path, password)
            except EngineError as e:
                self.error(str(e))
                return False
            return True

        def done(_):
            self._update_title()
            for m in self.models.values():
                m.reset()
            self.info(f"Saved as {os.path.basename(path)}" + (" (encrypted)" if password else ""))

        self.run_task("Saving workbook", self.wb.save_as, done, None, path, password, freeze=True)
        return True

    def closeEvent(self, event):
        if self.busy:
            r = QMessageBox.question(self, "Quit", "A task is still running. Cancel it and quit?")
            if r != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_task()
            self.runner.wait()
        if not self._confirm_discard():
            event.ignore()
            return
        self.stats_runner.wait()
        if self.wb:
            try:
                self.wb.checkpoint()
            except Exception:
                pass
            self.wb.close()
            self.wb = None
        event.accept()

    # ================================================================ sheets

    def after_sheets_changed(self, select=None):
        if not self.wb:
            return
        current = select or self.sheet
        sheets = self.wb.sheets()
        for gone in [s for s in self.models if s not in sheets]:
            self.models.pop(gone, None)
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for s in sheets:
            i = self.tabs.addTab(s)
            self.tabs.setTabData(i, s)
        self.tabs.blockSignals(False)
        if sheets:
            idx = sheets.index(current) if current in sheets else 0
            self.tabs.setCurrentIndex(idx)
            self._tab_changed(idx)
            self.stack.setCurrentIndex(1)
        else:
            self.table.setModel(None)
            self.stack.setCurrentIndex(0)
            self._update_status()
        self._update_actions()

    def _tab_changed(self, index):
        sheet = self.tabs.tabData(index) if index >= 0 else None
        if not sheet:
            return
        model = self.models.get(sheet)
        fresh = model is None
        if fresh:
            model = SheetModel(self.wb, sheet, self)
            model.editFailed.connect(lambda m: self.error(m, "Cannot change cell"))
            model.edited.connect(self._after_edit)
            self.models[sheet] = model
        else:
            model.reset()
        self.table.setModel(model)
        sel = self.table.selectionModel()
        sel.selectionChanged.connect(lambda *_: self.stats_timer.start())
        sel.currentChanged.connect(self._current_changed)
        self._size_columns(model)
        self._update_status()
        self._update_actions()
        self.stats_label.setText("")
        if model.rowCount() and model.columnCount():
            self.table.setCurrentIndex(model.index(0, 0))

    def _size_columns(self, model):
        fm = self.table.fontMetrics()
        hh = self.table.horizontalHeader()
        sample = min(model.rowCount(), 60)
        for c in range(model.columnCount()):
            width = fm.horizontalAdvance(model.cols[c][0]) + 28
            for r in range(sample):
                width = max(width, fm.horizontalAdvance(model.text_at(r, c)) + 16)
            hh.resizeSection(c, max(80, min(width + 12, 340)))

    def _tab_menu(self, pos):
        i = self.tabs.tabAt(pos)
        if i < 0 or self.busy:
            return
        sheet = self.tabs.tabData(i)
        menu = QMenu(self)
        menu.addAction("Rename sheet...", lambda: self.rename_sheet(sheet))
        menu.addAction("Duplicate sheet", lambda: self.duplicate_sheet(sheet))
        if self.policy["allow_export"]:
            menu.addAction("Export sheet...", lambda: (self.tabs.setCurrentIndex(i), self.export_sheet()))
        menu.addSeparator()
        menu.addAction("Delete sheet", lambda: self.delete_sheet(sheet))
        menu.exec(self.tabs.mapToGlobal(pos))

    def rename_sheet(self, sheet):
        dlg = TextDialog("Rename sheet", "New name", sheet, self)
        if dlg.exec() == QDialog.Accepted and dlg.text() and dlg.text() != sheet:
            try:
                self.wb.rename_sheet(sheet, dlg.text())
            except EngineError as e:
                self.error(str(e))
                return
            self.models.pop(sheet, None)
            self.after_sheets_changed(select=dlg.text())

    def duplicate_sheet(self, sheet):
        self.run_task("Duplicating sheet", self.wb.duplicate_sheet,
                      lambda name: self.after_sheets_changed(select=name), None, sheet)

    def delete_sheet(self, sheet):
        r = QMessageBox.question(self, "Delete sheet",
                                 f"Delete the sheet '{sheet}' and all its data? This cannot be undone.")
        if r != QMessageBox.Yes:
            return
        try:
            self.wb.delete_sheet(sheet)
        except EngineError as e:
            self.error(str(e))
            return
        self.models.pop(sheet, None)
        self.after_sheets_changed()

    # ================================================================ import

    def import_dialog(self):
        if self.busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Import files", "", OPEN_FILTER)
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        if self.busy:
            self.error("Please wait for the current task to finish.")
            return
        self._import_queue = list(paths)
        self._import_next(None)

    def _import_next(self, last_sheet):
        if not self._import_queue:
            if last_sheet:
                self.after_sheets_changed(select=last_sheet)
            return
        path = self._import_queue.pop(0)
        ext = os.path.splitext(path)[1].lower()
        if ext in WORKBOOK_EXT:
            self.error(f"{os.path.basename(path)} is a workbook. Use File, Open to open it.")
            self._import_next(last_sheet)
            return
        excel_sheets = None
        if ext in EXCEL_EXT:
            try:
                excel_sheets = Workbook.excel_sheet_names(path)
            except Exception as e:
                self.error(f"Cannot read {os.path.basename(path)}:\n{e}")
                self._import_next(last_sheet)
                return
        dlg = ImportDialog(path, excel_sheets, self)
        if dlg.exec() != QDialog.Accepted:
            self._import_next(last_sheet)
            return
        opts = dlg.options()
        started = time.time()

        def done(names):
            secs = time.time() - started
            rows = sum(self.wb.row_total(n) for n in names)
            self.info(f"Imported {os.path.basename(path)}: {rows:,} rows in {secs:.1f} s")
            self.after_sheets_changed(select=names[0])
            self._import_next(names[0])

        def fail(msg):
            self.error(f"Could not import {os.path.basename(path)}:\n\n{msg}")
            self._import_next(last_sheet)

        self.run_task(f"Importing {os.path.basename(path)}", self.wb.import_file, done, fail, path, opts)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        workbooks = [p for p in paths if os.path.splitext(p)[1].lower() in WORKBOOK_EXT]
        if workbooks:
            self.open_workbook(workbooks[0])
            return
        self.import_paths(paths)

    # ================================================================ export

    def export_sheet(self):
        if not self.policy["allow_export"]:
            self.error("Exporting is turned off by your organization's policy.")
            return
        sheet = self.sheet
        if not sheet or self.busy:
            return
        v = self.wb.view(sheet)
        view_only = False
        if not v.is_identity:
            box = QMessageBox(self)
            box.setWindowTitle("Export")
            box.setText("This sheet is sorted or filtered. What do you want to export?")
            cur = box.addButton(f"Current view ({v.count:,} rows)", QMessageBox.AcceptRole)
            box.addButton(f"Whole sheet ({v.total:,} rows)", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is None or box.buttonRole(clicked) == QMessageBox.RejectRole:
                return
            view_only = clicked is cur
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export sheet", sheet,
            "CSV (*.csv);;Excel workbook (*.xlsx);;Parquet (*.parquet);;JSON lines (*.json)")
        if not path:
            return
        fmt = {"CSV": "csv", "Excel": "xlsx", "Parquet": "parquet", "JSON": "json"}[chosen.split(" ")[0]]
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        rows = v.count if view_only else v.total
        if fmt == "xlsx" and rows > 1_048_575:
            r = QMessageBox.question(
                self, "Export to Excel",
                f"Excel can hold at most 1,048,575 rows, and this export has {rows:,}. "
                "Only the first 1,048,575 will be written. Continue? "
                "(CSV or Parquet have no limit.)")
            if r != QMessageBox.Yes:
                return
        if self.wb.encrypted:
            r = QMessageBox.question(
                self, "Export",
                "The exported file will not be encrypted. Anyone with access to it can read it. Continue?")
            if r != QMessageBox.Yes:
                return

        def done(result):
            msg = f"Exported to {os.path.basename(path)}"
            if result and result.get("truncated"):
                msg += f" (first {result['rows']:,} rows, the Excel limit)"
            self.info(msg)

        self.run_task("Exporting", self.wb.export, done, None, sheet, path, fmt, view_only)

    # ============================================================ view (sort/filter)

    def _apply_view_async(self, sheet, sorts, filters, label="Sorting and filtering"):
        def build():
            return self.wb.prepare_view(sheet, sorts, filters)

        def done(result):
            table, count = result
            self.wb.apply_view(sheet, sorts, filters, table, count)
            m = self.models.get(sheet)
            if m:
                m.reset()
            self._update_status()
            if count == 0:
                self.info("No rows match the filter.")

        self.run_task(label, build, done)

    def sort_current(self, ascending):
        col = self._current_col()
        if not col:
            self.error("Click a cell in the column you want to sort by.")
            return
        v = self.wb.view(self.sheet)
        self._apply_view_async(self.sheet, [(col, ascending)], v.filters, "Sorting")

    def custom_sort(self):
        if not self.sheet:
            return
        v = self.wb.view(self.sheet)
        dlg = SortDialog(self.model.cols, v.sorts, self)
        if dlg.exec() == QDialog.Accepted:
            self._apply_view_async(self.sheet, dlg.sorts(), v.filters, "Sorting")

    def add_filter(self, column=None):
        if not self.sheet:
            return
        v = self.wb.view(self.sheet)
        dlg = FilterDialog(self.model.cols, column or self._current_col(), v.describe() if v.filters else "", self)
        if dlg.exec() != QDialog.Accepted:
            return
        f = dlg.result_filter()
        try:
            self.wb.validate_filter_value(self.sheet, f["col"], f["op"], f["value"])
        except EngineError as e:
            self.error(str(e))
            return
        self._apply_view_async(self.sheet, v.sorts, v.filters + [f], "Filtering")

    def filter_by_value(self, op):
        idx = self.table.currentIndex()
        if not self.model or not idx.isValid():
            return
        col = self.model.cols[idx.column()][0]
        value = self.model.text_at(idx.row(), idx.column())
        if value == "":
            f = {"col": col, "op": "not_empty" if op == "ne" else "empty", "value": ""}
        else:
            f = {"col": col, "op": op, "value": value}
        v = self.wb.view(self.sheet)
        self._apply_view_async(self.sheet, v.sorts, v.filters + [f], "Filtering")

    def clear_column_filter(self, col):
        v = self.wb.view(self.sheet)
        self._apply_view_async(self.sheet, v.sorts, [f for f in v.filters if f["col"] != col], "Filtering")

    def clear_view(self):
        if not self.sheet or self.busy:
            return
        self.wb.reset_view(self.sheet)
        self.model.reset()
        self._update_status()

    # ================================================================ editing

    def _after_edit(self):
        self._update_actions()
        self._current_changed(self.table.currentIndex(), None)
        self.stats_timer.start()

    def _current_changed(self, current, _previous):
        m = self.model
        if not m or not current.isValid():
            self.name_box.setText("")
            self.cell_edit.setText("")
            self.formula_hint.setText("")
            return
        name, typ = m.cols[current.column()]
        self.name_box.setText(f"{column_letters(current.column())}{current.row() + 1:,}  {name}")
        self.name_box.setToolTip(f"Type: {friendly_type(typ)}")
        self.cell_edit.setText(m.text_at(current.row(), current.column()))
        f = m.formulas.get(name)
        self.formula_hint.setText(f"Formula column: {f[0]}" if f else "")

    def _commit_cell_edit(self):
        idx = self.table.currentIndex()
        if not self.model or not idx.isValid():
            return
        if self.busy:
            self.error("Please wait for the current task to finish.")
            return
        self.model.setData(idx, self.cell_edit.text())
        self.table.setFocus()

    def undo(self):
        if self.busy or not self.wb:
            return
        try:
            sheet = self.wb.undo()
        except EngineError as e:
            self.error(str(e))
            return
        if sheet and sheet in self.models:
            self.models[sheet].refresh_data()
        self._after_edit()

    def redo(self):
        if self.busy or not self.wb:
            return
        try:
            sheet = self.wb.redo()
        except EngineError as e:
            self.error(str(e))
            return
        if sheet and sheet in self.models:
            self.models[sheet].refresh_data()
        self._after_edit()

    def _ranges(self):
        sel = self.table.selectionModel()
        if not sel:
            return []
        out = [(r.top(), r.bottom(), r.left(), r.right()) for r in sel.selection()]
        if not out:
            idx = self.table.currentIndex()
            if idx.isValid():
                out = [(idx.row(), idx.row(), idx.column(), idx.column())]
        return out

    def copy(self):
        if not self.policy["allow_copy"]:
            self.error("Copying is turned off by your organization's policy.")
            return
        ranges = self._ranges()
        if not ranges or not self.model:
            return
        top, bottom, left, right = ranges[0]
        cells = (bottom - top + 1) * (right - left + 1)
        if cells > MAX_COPY_CELLS:
            self.error(f"That is {cells:,} cells, too many for the clipboard. "
                       "Use Export instead.")
            return
        cols = [c for c, _ in self.model.cols[left:right + 1]]

        def to_text(rows):
            lines = ["\t".join("" if v is None else str(v).replace("\t", " ").replace("\n", " ")
                               for v in row) for row in rows]
            QGuiApplication.clipboard().setText("\n".join(lines))
            self.info(f"Copied {cells:,} cells")

        if cells <= SMALL_EDIT_CELLS:
            try:
                to_text(self.wb.block_text(self.sheet, cols, top, bottom))
            except EngineError as e:
                self.error(str(e))
        else:
            self.run_task("Copying", self.wb.block_text, to_text, None, self.sheet, cols, top, bottom)

    def paste(self):
        m = self.model
        idx = self.table.currentIndex()
        if not m or not idx.isValid() or self.busy:
            return
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        grid = [line.split("\t") for line in lines]
        ranges = self._ranges()
        top, left = idx.row(), idx.column()
        if ranges:
            top, left = min(r[0] for r in ranges), min(r[2] for r in ranges)
        if len(grid) == 1 and len(grid[0]) == 1 and ranges:
            t, b, l, r = ranges[0]
            if (b - t + 1) * (r - l + 1) > SMALL_EDIT_CELLS:
                self.error("Fill a smaller area at a time, or use a formula column.")
                return
            grid = [[grid[0][0]] * (r - l + 1) for _ in range(b - t + 1)]
            top, left = t, l
        height = min(len(grid), m.count - top)
        width = min(max(len(r) for r in grid), len(m.cols) - left)
        if height <= 0 or width <= 0:
            return
        if height * width > SMALL_EDIT_CELLS * 5:
            self.error("That paste is too large. Import the data as a file instead.")
            return
        rid_map = self.wb.rids_for_positions(self.sheet, list(range(top, top + height)))
        rids = [rid_map[p] for p in range(top, top + height)]
        changes = []
        for j in range(width):
            col = m.cols[left + j][0]
            texts = [(grid[i][j] if j < len(grid[i]) else "") for i in range(height)]
            changes.append((col, rids, texts))
        try:
            self.wb.set_block(self.sheet, changes)
        except EngineError as e:
            self.error(str(e), "Cannot paste")
            return
        m.refresh_data()
        cut = len(grid) > height or max(len(r) for r in grid) > width
        self.info(f"Pasted {height * width:,} cells" + (" (some did not fit and were left out)" if cut else ""))
        self._after_edit()

    def clear_cells(self):
        m = self.model
        ranges = self._ranges()
        if not m or not ranges or self.busy:
            return
        cells = sum((b - t + 1) * (r - l + 1) for t, b, l, r in ranges)
        if cells <= UNDOABLE_CLEAR_CELLS:
            changes = {}
            for t, b, l, r in ranges:
                rid_map = self.wb.rids_for_positions(self.sheet, list(range(t, b + 1)))
                for c in range(l, r + 1):
                    col = m.cols[c][0]
                    ch = changes.setdefault(col, ([], []))
                    ch[0].extend(rid_map[p] for p in range(t, b + 1))
                    ch[1].extend([None] * (b - t + 1))
            try:
                self.wb.set_block(self.sheet, [(c, rs, ts) for c, (rs, ts) in changes.items()])
            except EngineError as e:
                self.error(str(e))
                return
            m.refresh_data()
            self._after_edit()
            return
        r = QMessageBox.question(self, "Clear cells",
                                 f"Clear {cells:,} cells? This many cells cannot be undone.")
        if r != QMessageBox.Yes:
            return

        def work():
            for t, b, l, rr in ranges:
                self.wb.clear_range(self.sheet, [c for c, _ in m.cols[l:rr + 1]], t, b)

        self.run_task("Clearing cells", work, lambda _: (m.refresh_data(), self._after_edit()))

    def insert_row_above(self):
        m = self.model
        idx = self.table.currentIndex()
        if not m:
            return
        rid = m.rid_at(idx.row()) if idx.isValid() else None
        sheet = self.sheet

        def done(pos):
            m.reset()
            self._update_status()
            if self.wb.view(sheet).is_identity:
                self.table.setCurrentIndex(m.index(pos, max(idx.column(), 0)))
            self.info("Row inserted")

        self.run_task("Inserting row", self.wb.insert_rows, done, None, sheet, rid, 1)

    def append_row(self):
        m = self.model
        if not m:
            return
        sheet = self.sheet

        def done(pos):
            m.reset()
            self._update_status()
            if self.wb.view(sheet).is_identity:
                i = m.index(pos, 0)
                self.table.setCurrentIndex(i)
                self.table.scrollTo(i)
            self.info("Row added at the end")

        self.run_task("Adding row", self.wb.insert_rows, done, None, sheet, None, 1)

    def delete_rows(self):
        m = self.model
        ranges = self._ranges()
        if not m or not ranges:
            return
        spans = sorted({(t, b) for t, b, _, _ in ranges})
        merged = []
        for t, b in spans:
            if merged and t <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((t, b))
        n = sum(b - t + 1 for t, b in merged)
        r = QMessageBox.question(self, "Delete rows",
                                 f"Delete {n:,} row{'s' if n != 1 else ''}? This cannot be undone.")
        if r != QMessageBox.Yes:
            return

        def done(_):
            m.reset()
            self._update_status()
            self.info(f"Deleted {n:,} rows")

        self.run_task("Deleting rows", self.wb.delete_positions, done, None, self.sheet, merged)

    # ============================================================ find / go to

    def show_find(self):
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def find(self, backward=False):
        text = self.find_edit.text()
        if not text or not self.model:
            return
        from PySide6.QtWidgets import QApplication
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            backward = True
        idx = self.table.currentIndex()
        row = idx.row() if idx.isValid() else None
        col = idx.column() if idx.isValid() else -1
        m = self.model

        def done(hit):
            if not hit:
                self.find_status.setText("Not found")
                return
            pos, c, wrapped = hit
            target = m.index(pos, c)
            self.table.setCurrentIndex(target)
            self.table.scrollTo(target, QAbstractItemView.PositionAtCenter)
            self.find_status.setText("Wrapped around" if wrapped else f"Row {pos + 1:,}")

        self.find_status.setText("Searching...")
        self.run_task("Searching", self.wb.find_next, done, lambda e: self.find_status.setText(e),
                      self.sheet, text, row, col, backward)

    def go_to_row(self):
        m = self.model
        if not m:
            return
        dlg = TextDialog("Go to row", f"Row number (1 to {m.count:,})", "", self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            n = int(dlg.text().replace(",", "")) - 1
        except ValueError:
            return
        n = max(0, min(n, m.count - 1))
        col = max(self.table.currentIndex().column(), 0)
        i = m.index(n, col)
        self.table.setCurrentIndex(i)
        self.table.scrollTo(i, QAbstractItemView.PositionAtTop)

    # ================================================================ columns

    def _ask_column(self, col):
        return col or self._current_col()

    def add_formula(self):
        if not self.sheet:
            return
        dlg = FormulaDialog(self.wb, self.sheet, self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, formula, is_sql = dlg.values()
        m = self.model

        def done(col):
            m.reset()
            self._size_columns(m)
            self.table.setCurrentIndex(m.index(max(self.table.currentIndex().row(), 0), len(m.cols) - 1))
            self.info(f"Added formula column {col}")

        self.run_task("Calculating formula", self.wb.add_formula_column, done, None,
                      self.sheet, name, formula, is_sql)

    def add_empty_column(self):
        if not self.sheet:
            return
        dlg = TextDialog("Add column", "Column name", "", self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.wb.add_empty_column(self.sheet, dlg.text())
        except EngineError as e:
            self.error(str(e))
            return
        self.model.reset()
        self._size_columns(self.model)

    def recalc_formulas(self):
        if not self.sheet:
            return
        m = self.model

        def done(n):
            m.reset()
            self.info(f"Recalculated {n} formula column{'s' if n != 1 else ''}" if n else
                      "This sheet has no formula columns.")

        self.run_task("Recalculating formulas", self.wb.recalc_formulas, done, None, self.sheet)

    def rename_column(self, col=None):
        col = self._ask_column(col)
        if not col:
            return
        dlg = TextDialog("Rename column", "New name", col, self)
        if dlg.exec() != QDialog.Accepted or not dlg.text() or dlg.text() == col:
            return
        try:
            self.wb.rename_column(self.sheet, col, dlg.text())
        except EngineError as e:
            self.error(str(e))
            return
        self.model.reset()
        self._update_status()

    def change_type(self, col=None):
        col = self._ask_column(col)
        if not col:
            return
        typ = self.wb.column_type(self.sheet, col)
        dlg = TypeDialog(col, typ, self)
        if dlg.exec() != QDialog.Accepted:
            return
        new = dlg.type_name()
        sheet = self.sheet
        m = self.model

        def check():
            return self.wb.count_type_failures(sheet, col, new)

        def checked(bad):
            if bad:
                r = QMessageBox.question(
                    self, "Change type",
                    f"{bad:,} values in {col} cannot be converted to {friendly_type(new)} and will "
                    "become empty. Continue?")
                if r != QMessageBox.Yes:
                    return
            self.run_task("Changing type", self.wb.change_type, lambda _: (m.reset(), self._update_status()),
                          None, sheet, col, new)

        self.run_task("Checking values", check, checked)

    def delete_column(self, col=None):
        col = self._ask_column(col)
        if not col:
            return
        r = QMessageBox.question(self, "Delete column", f"Delete the column '{col}'? This cannot be undone.")
        if r != QMessageBox.Yes:
            return
        m = self.model
        self.run_task("Deleting column", self.wb.drop_column,
                      lambda _: (m.reset(), self._size_columns(m), self._update_status()), None, self.sheet, col)

    def column_stats(self, col=None):
        col = self._ask_column(col)
        if not col:
            return
        self.run_task("Calculating statistics", self.wb.column_stats,
                      lambda st: StatsDialog(col, st, self).exec(), None, self.sheet, col)

    # ================================================================ analysis

    def summarize(self):
        if not self.sheet:
            return
        v = self.wb.view(self.sheet)
        dlg = SummarizeDialog(self.sheet, self.model.cols, not v.is_identity, self)
        if dlg.exec() != QDialog.Accepted:
            return
        spec = dlg.spec()
        self.run_task("Summarizing", self.wb.summarize, lambda name: self.after_sheets_changed(select=name),
                      None, self.sheet, spec["group_cols"], spec["aggs"], spec["pivot_col"],
                      spec["use_view"], spec["name"])

    def remove_duplicates(self):
        if not self.sheet:
            return
        dlg = DuplicatesDialog(self.sheet, self.model.cols, self)
        if dlg.exec() != QDialog.Accepted:
            return
        cols, name = dlg.spec()

        def done(result):
            new, removed = result
            self.after_sheets_changed(select=new)
            self.info(f"Removed {removed:,} duplicate rows into new sheet {new}")

        self.run_task("Removing duplicates", self.wb.remove_duplicates, done, None, self.sheet, cols, name)

    def open_sql(self):
        if not self.policy["allow_sql"]:
            self.error("The SQL window is turned off by your organization's policy.")
            return
        if self.sql_dialog is None:
            self.sql_dialog = SqlDialog(self, self)
        self.sql_dialog.show()
        self.sql_dialog.raise_()
        self.sql_dialog.activateWindow()

    # ============================================================ context menus

    def _header_menu(self, pos):
        m = self.model
        if not m or self.busy:
            return
        c = self.table.horizontalHeader().logicalIndexAt(pos)
        if c < 0:
            return
        col = m.cols[c][0]
        cur = self.table.currentIndex()
        self.table.setCurrentIndex(m.index(max(cur.row(), 0), c))
        v = self.wb.view(self.sheet)
        menu = QMenu(self)
        menu.addAction("Sort A to Z", lambda: self.sort_current(True))
        menu.addAction("Sort Z to A", lambda: self.sort_current(False))
        menu.addAction("Filter...", lambda: self.add_filter(col))
        if any(f["col"] == col for f in v.filters):
            menu.addAction("Remove filter on this column", lambda: self.clear_column_filter(col))
        menu.addSeparator()
        menu.addAction("Column statistics...", lambda: self.column_stats(col))
        menu.addSeparator()
        menu.addAction("Rename...", lambda: self.rename_column(col))
        menu.addAction("Change type...", lambda: self.change_type(col))
        menu.addAction("Add formula column...", self.add_formula)
        if col in m.formulas:
            menu.addAction("Recalculate formulas", self.recalc_formulas)
        menu.addSeparator()
        menu.addAction("Delete column", lambda: self.delete_column(col))
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _cell_menu(self, pos):
        if not self.model or self.busy:
            return
        idx = self.table.indexAt(pos)
        if idx.isValid() and not self.table.selectionModel().isSelected(idx):
            self.table.setCurrentIndex(idx)
        menu = QMenu(self)
        if self.policy["allow_copy"]:
            menu.addAction("Copy", self.copy)
        menu.addAction("Paste", self.paste)
        menu.addAction("Clear cells", self.clear_cells)
        menu.addSeparator()
        menu.addAction("Filter by this value", lambda: self.filter_by_value("eq"))
        menu.addAction("Hide rows with this value", lambda: self.filter_by_value("ne"))
        menu.addSeparator()
        menu.addAction("Insert row above", self.insert_row_above)
        menu.addAction("Add row at end", self.append_row)
        menu.addAction("Delete selected rows", self.delete_rows)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ============================================================ status stats

    def _compute_stats(self):
        m = self.model
        ranges = self._ranges()
        if not m or not ranges:
            self.stats_label.setText("")
            return
        cells = sum((b - t + 1) * (r - l + 1) for t, b, l, r in ranges)
        if cells <= 1:
            self.stats_label.setText("")
            return
        self._stats_request += 1
        request = self._stats_request
        sheet = self.sheet
        cols_by_range = [([c for c, _ in m.cols[l:r + 1]], t, b) for t, b, l, r in ranges[:8]]

        wb = self.wb

        def work():
            try:
                return gather()
            finally:
                wb.release_cursor()

        def gather():
            total = {"count": 0, "n": 0, "sum": 0.0, "min": None, "max": None}
            for cols, t, b in cols_by_range:
                st = self.wb.selection_stats(sheet, cols, t, b)
                if not st:
                    continue
                total["count"] += st["count"]
                if st.get("n"):
                    total["n"] += st["n"]
                    total["sum"] += st["sum"]
                    total["min"] = st["min"] if total["min"] is None else min(total["min"], st["min"])
                    total["max"] = st["max"] if total["max"] is None else max(total["max"], st["max"])
            return total

        def done(st):
            if request != self._stats_request:
                return
            parts = [f"Count: {st['count']:,}"]
            if st["n"]:
                parts += [f"Sum: {fmt_num(st['sum'])}", f"Average: {fmt_num(st['sum'] / st['n'])}",
                          f"Min: {fmt_num(st['min'])}", f"Max: {fmt_num(st['max'])}"]
            self.stats_label.setText("   ".join(parts) + "   ")

        self.stats_runner.start(work, done, lambda _msg: None)

    # ================================================================== help

    def formula_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("Formula help")
        box.setTextFormat(Qt.RichText)
        box.setText(FORMULA_HELP)
        box.exec()

    def about(self):
        crypto = "available" if (self.wb and self.wb.crypto_available) else "not installed"
        policy = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in self.policy.items())
        QMessageBox.about(
            self, "About BigSheet",
            f"<b>BigSheet {config.APP_VERSION}</b><br>A private spreadsheet for very large files.<br><br>"
            f"Engine: DuckDB {duckdb.__version__}<br>Workbook encryption: {crypto}<br>"
            f"Network access: disabled<br>Policy: {policy}<br><br>"
            f"Policy files are read from:<br>{config.user_config_dir() / 'policy.json'}<br>"
            f"{config.system_config_dir() / 'policy.json'}")
