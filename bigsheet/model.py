"""Qt model that shows a sheet by fetching only the rows on screen."""

import datetime as dt
import decimal
import traceback
from collections import OrderedDict

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt,
                            QThreadPool, Signal)
from PySide6.QtGui import QColor, QFont

from .engine import EngineError, friendly_type, is_numeric

BLOCK = 256
MAX_BLOCKS = 400


def format_value(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        return "%.15g" % v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    return str(v)


NUMBER_FORMATS = [
    ("general", "Automatic"),
    ("number", "Number"),
    ("currency", "Currency"),
    ("percent", "Percent"),
    ("scientific", "Scientific"),
]
DATE_FORMATS = [
    ("date_iso", "2024-01-31"),
    ("date_dmy", "31.01.2024"),
    ("date_dmy_slash", "31/01/2024"),
    ("date_mdy", "01/31/2024"),
    ("date_long", "31 Jan 2024"),
]
CURRENCY_SYMBOLS = ["$", "\u20ac", "\u00a3", "\u09f3", "\u20b9", "CHF "]
_DATE_PATTERNS = {"date_iso": "%Y-%m-%d", "date_dmy": "%d.%m.%Y", "date_dmy_slash": "%d/%m/%Y",
                  "date_mdy": "%m/%d/%Y", "date_long": "%d %b %Y"}
_INT_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
              "UINTEGER", "UBIGINT")


def default_decimals(numfmt, col_type):
    if numfmt == "percent":
        return 1
    if numfmt in ("currency", "scientific"):
        return 2
    return 0 if col_type.upper() in _INT_TYPES else 2


def make_formatter(fmt, col_type):
    """A function turning a cell value into display text for a column format."""
    numfmt = fmt.get("numfmt", "general")
    if numfmt in _DATE_PATTERNS:
        pattern = _DATE_PATTERNS[numfmt]

        def fmt_date(v):
            if isinstance(v, (dt.date, dt.datetime)):
                text = v.strftime(pattern)
                if isinstance(v, dt.datetime) and (v.hour or v.minute or v.second):
                    text += v.strftime(" %H:%M:%S")
                return text
            return format_value(v)
        return fmt_date
    if numfmt == "general":
        def fmt_general(v):
            if isinstance(v, float) and v == v and not v.is_integer():
                return "%.12g" % v
            return format_value(v)
        return fmt_general
    d = fmt.get("decimals")
    if d is None:
        d = default_decimals(numfmt, col_type)
    d = max(0, min(int(d), 10))
    symbol = fmt.get("symbol", "$")

    def fmt_num(v):
        if v is None or isinstance(v, bool):
            return format_value(v)
        try:
            x = float(v)
        except (TypeError, ValueError):
            return format_value(v)
        if numfmt == "number":
            return f"{x:,.{d}f}"
        if numfmt == "currency":
            return ("-" if x < 0 else "") + f"{symbol}{abs(x):,.{d}f}"
        if numfmt == "percent":
            return f"{x * 100:,.{d}f}%"
        if numfmt == "scientific":
            return f"{x:.{d}E}"
        return format_value(v)
    return fmt_num


def column_letters(index):
    s = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        s = chr(65 + rem) + s
    return s


class SheetModel(QAbstractTableModel):
    editFailed = Signal(str)
    edited = Signal()

    def __init__(self, wb, sheet, parent=None):
        super().__init__(parent)
        self.wb = wb
        self.sheet = sheet
        self.read_only = False
        self.frozen = False
        self._cache = OrderedDict()
        self._load_meta()

    def _load_meta(self):
        self.cols = self.wb.columns(self.sheet)
        self.numeric = [is_numeric(t) for _, t in self.cols]
        self.formulas = self.wb.formulas(self.sheet)
        self.count = self.wb.view(self.sheet).count
        self._load_formats()

    def _load_formats(self):
        try:
            self.formats = self.wb.column_formats(self.sheet)
            self.has_fills = self.wb.has_fills(self.sheet)
        except EngineError:
            self.formats = {}
            self.has_fills = False
        self._fill_colors = {}
        self._styles = []
        aligns = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}
        for i, (name, typ) in enumerate(self.cols):
            fmt = self.formats.get(name, {})
            font = None
            if fmt.get("bold") or fmt.get("italic"):
                font = QFont()
                font.setBold(bool(fmt.get("bold")))
                font.setItalic(bool(fmt.get("italic")))
            fg = QColor(fmt["color"]) if fmt.get("color") else None
            bg = QColor(fmt["fill"]) if fmt.get("fill") else None
            h = aligns.get(fmt.get("align"))
            if h is None:
                h = Qt.AlignRight if self.numeric[i] else Qt.AlignLeft
            self._styles.append((make_formatter(fmt, typ), font, fg, bg, int(h | Qt.AlignVCenter)))

    def reload_formats(self):
        self._load_formats()
        self.refresh_data()

    def display_at(self, row, col):
        vals = self.row_values(row)
        if vals is None or col >= len(vals):
            return ""
        return self._styles[col][0](vals[col])

    # ---------------------------------------------------------------- refresh

    def reset(self):
        self.beginResetModel()
        self._cache.clear()
        try:
            self.wb.refresh_counts(self.sheet)
            self._load_meta()
        except EngineError:
            self.count = 0
        self.endResetModel()

    def refresh_data(self):
        self._cache.clear()
        if self.count and self.cols:
            self.dataChanged.emit(self.index(0, 0), self.index(self.count - 1, len(self.cols) - 1))

    # ------------------------------------------------------------------- data

    def _block(self, b):
        hit = self._cache.get(b)
        if hit is not None:
            self._cache.move_to_end(b)
            return hit
        if self.frozen:
            return None
        try:
            rids, rows = self.wb.fetch(self.sheet, b * BLOCK, BLOCK)
            fills = self.wb.fills_for(self.sheet, rids) if self.has_fills else {}
            hit = (rids, rows, fills)
        except Exception:
            return None
        self._cache[b] = hit
        if len(self._cache) > MAX_BLOCKS:
            self._cache.popitem(last=False)
        return hit

    def row_values(self, row):
        blk = self._block(row // BLOCK)
        if blk is None:
            return None
        rows = blk[1]
        i = row % BLOCK
        return rows[i] if i < len(rows) else None

    def rid_at(self, row):
        blk = self._block(row // BLOCK)
        if blk is None:
            return None
        rids = blk[0]
        i = row % BLOCK
        return rids[i] if i < len(rids) else None

    def text_at(self, row, col):
        vals = self.row_values(row)
        if vals is None or col >= len(vals):
            return ""
        return format_value(vals[col])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.count

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        c = index.column()
        if role == Qt.DisplayRole:
            return self.display_at(index.row(), c)
        if role == Qt.EditRole:
            return self.text_at(index.row(), c)
        style = self._styles[c]
        if role == Qt.TextAlignmentRole:
            return style[4]
        if role == Qt.FontRole:
            return style[1]
        if role == Qt.ForegroundRole:
            return style[2]
        if role == Qt.BackgroundRole:
            if self.has_fills:
                blk = self._block(index.row() // BLOCK)
                if blk is not None and blk[2]:
                    i = index.row() % BLOCK
                    if i < len(blk[0]):
                        rid = blk[0][i]
                        color = blk[2].get((rid, self.cols[c][0])) or blk[2].get((rid, ""))
                        if color:
                            q = self._fill_colors.get(color)
                            if q is None:
                                q = self._fill_colors[color] = QColor(color)
                            return q
            return style[3]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if section >= len(self.cols):
                return None
            name, typ = self.cols[section]
            if role == Qt.DisplayRole:
                return name
            if role == Qt.ToolTipRole:
                tip = f"{column_letters(section)}: {name}\nType: {friendly_type(typ)}"
                if name in self.formulas:
                    tip += f"\nFormula: {self.formulas[name][0]}"
                return tip
        else:
            if role == Qt.DisplayRole:
                return f"{section + 1:,}"
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def flags(self, index):
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if not self.read_only:
            f |= Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or self.read_only:
            return False
        rid = self.rid_at(index.row())
        if rid is None:
            return False
        col = self.cols[index.column()][0]
        if str(value) == self.text_at(index.row(), index.column()):
            return False
        try:
            self.wb.set_cells(self.sheet, col, [rid], [value])
        except EngineError as e:
            self.editFailed.emit(str(e))
            return False
        self._cache.pop(index.row() // BLOCK, None)
        self.dataChanged.emit(index, index)
        self.edited.emit()
        return True


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except EngineError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # unexpected: show the short traceback
            tb = traceback.format_exc(limit=3)
            self.signals.failed.emit(f"{e}\n\n{tb}")
        else:
            self.signals.done.emit(result)


class Runner:
    """Runs functions on a background thread and calls back on the main thread."""

    def __init__(self, threads=2):
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(threads)
        self._live = set()

    def start(self, fn, on_done=None, on_fail=None, *args, **kwargs):
        task = Task(fn, *args, **kwargs)
        self._live.add(task)

        def finish_ok(result):
            self._live.discard(task)
            if on_done:
                on_done(result)

        def finish_fail(msg):
            self._live.discard(task)
            if on_fail:
                on_fail(msg)

        task.signals.done.connect(finish_ok)
        task.signals.failed.connect(finish_fail)
        self.pool.start(task)
        return task

    def wait(self):
        self.pool.waitForDone()
