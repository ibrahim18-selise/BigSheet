"""The data engine behind BigSheet.

A workbook is a DuckDB database file. Every sheet is a table with a hidden
__rid column that always holds the row positions 0..n-1 in sheet order. That
lets the grid fetch any screenful of rows with a range filter that DuckDB
answers from its min/max indexes in milliseconds, even for very large tables.

A sorted or filtered view is a small two-column table (__pos, __rid) kept in
an in-memory scratch database, so sorting never rewrites the sheet.

Security: the engine never touches the network. Automatic extension
downloads are off, HTTP and S3 file systems are disabled, and the
configuration is locked so nothing typed into the SQL window can turn those
back on. Workbooks can be encrypted with AES (key derived from a password).
"""

import csv
import json
import datetime as dt
import decimal
import os
import pathlib
import re
import secrets
import shutil
import tempfile
import threading

import duckdb

from . import config
from .formula import FormulaError, quote_ident, sql_string, translate

RID = "__rid"
META_PREFIX = "__bs_"
EXCEL_MAX_ROWS = 1_048_575  # data rows, plus one header row

NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
    "FLOAT", "DOUBLE", "REAL",
}

TYPE_CHOICES = [
    ("Text", "VARCHAR"),
    ("Whole number", "BIGINT"),
    ("Decimal number", "DOUBLE"),
    ("Date", "DATE"),
    ("Date and time", "TIMESTAMP"),
    ("True / False", "BOOLEAN"),
]

FILTER_OPS = [
    ("eq", "equals"),
    ("ne", "does not equal"),
    ("gt", "is greater than"),
    ("ge", "is greater than or equal to"),
    ("lt", "is less than"),
    ("le", "is less than or equal to"),
    ("contains", "contains"),
    ("not_contains", "does not contain"),
    ("starts", "starts with"),
    ("ends", "ends with"),
    ("empty", "is empty"),
    ("not_empty", "is not empty"),
]
FILTER_OP_LABELS = dict(FILTER_OPS)
FILTER_OP_LABELS.update({"in": "is one of", "not_in": "is not one of"})
NO_VALUE_OPS = {"empty", "not_empty"}
LIST_OPS = {"in", "not_in"}

AGGREGATES = [
    ("count", "Count of rows"),
    ("count_distinct", "Unique count"),
    ("sum", "Sum"),
    ("avg", "Average"),
    ("min", "Minimum"),
    ("max", "Maximum"),
    ("median", "Median"),
    ("count_values", "Count of values"),
]


class EngineError(Exception):
    """An error with a message that is safe to show to the user."""


class PasswordRequired(EngineError):
    pass


class WrongPassword(EngineError):
    pass


def is_numeric(col_type):
    t = col_type.upper()
    return t in NUMERIC_TYPES or t.startswith("DECIMAL")


def friendly_type(col_type):
    t = col_type.upper()
    if t in ("VARCHAR", "TEXT", "STRING"):
        return "text"
    if t in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT") or t.startswith("U"):
        return "whole number"
    if is_numeric(t):
        return "number"
    if t == "DATE":
        return "date (YYYY-MM-DD)"
    if t.startswith("TIMESTAMP"):
        return "date and time (YYYY-MM-DD HH:MM:SS)"
    if t.startswith("TIME"):
        return "time (HH:MM:SS)"
    if t == "BOOLEAN":
        return "TRUE or FALSE"
    return t.lower()


_CURRENCY = re.compile(r"^(?:[$\u20ac\u00a3\u09f3\u20b9]|CHF|USD|EUR|GBP|BDT|INR|TK)\s*|\s*(?:[$\u20ac\u00a3\u09f3\u20b9]|CHF|USD|EUR|GBP|BDT|INR|TK)$",
                       re.IGNORECASE)
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y",
                 "%d %B %Y", "%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y", "%Y%m%d", "%d.%m.%y", "%d/%m/%y")
_TIME_FORMATS = ("", " %H:%M", " %H:%M:%S", "T%H:%M:%S", " %H:%M:%S.%f", "T%H:%M:%S.%f")


def normalize_value(col_type, text):
    """Turn what a person types into a value DuckDB understands for this column type.

    Accepts thousands separators, decimal commas, currency symbols, percentages and the
    common date styles. Returns the text unchanged when it is not recognised.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return s
    t = col_type.upper()
    if t in NUMERIC_TYPES or t.startswith("DECIMAL"):
        x = s.replace("\u00a0", "").replace(" ", "").replace("'", "")
        negative = x.startswith("(") and x.endswith(")")
        if negative:
            x = x[1:-1]
        x = _CURRENCY.sub("", x)
        percent = x.endswith("%")
        if percent:
            x = x[:-1]
        if "," in x and "." in x:
            if x.rfind(",") > x.rfind("."):
                x = x.replace(".", "").replace(",", ".")
            else:
                x = x.replace(",", "")
        elif "," in x:
            parts = x.split(",")
            if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0].lstrip("-+")) <= 3):
                x = x.replace(",", "")
            else:
                x = x.replace(",", ".")
        elif x.count(".") > 1:
            x = x.replace(".", "")
        try:
            value = float(x)
        except ValueError:
            return s
        if negative:
            value = -value
        if percent:
            value /= 100
        if t in NUMERIC_TYPES and t not in ("FLOAT", "DOUBLE", "REAL") and value.is_integer():
            return str(int(value))
        return repr(value)
    if t == "DATE" or t.startswith("TIMESTAMP"):
        for df in _DATE_FORMATS:
            for tf in (_TIME_FORMATS if t != "DATE" else ("",)):
                try:
                    parsed = dt.datetime.strptime(s, df + tf)
                except ValueError:
                    continue
                return parsed.date().isoformat() if t == "DATE" else parsed.isoformat(sep=" ")
        return s
    if t == "BOOLEAN":
        low = s.lower()
        if low in ("yes", "y", "ja", "true", "t", "1", "wahr"):
            return "true"
        if low in ("no", "n", "nein", "false", "f", "0", "falsch"):
            return "false"
    return s


def _clean_db_error(exc):
    msg = str(exc).strip()
    msg = re.sub(r"^\w+ Error:\s*", "", msg)
    return msg.split("\n\nLINE")[0].strip()


def _httpfs_extension_path():
    """Path of the bundled httpfs extension, used only for its OpenSSL crypto."""
    try:
        import duckdb_extension_httpfs  # noqa: F401
    except ImportError:
        return None
    base = pathlib.Path(duckdb_extension_httpfs.__file__).parent / "extensions"
    candidate = base / ("v" + duckdb.__version__) / "httpfs.duckdb_extension"
    return candidate if candidate.exists() else None


class ViewState:
    """Sort and filter settings for one sheet, plus the scratch table behind them."""

    def __init__(self):
        self.sorts = []      # list of (column, ascending)
        self.filters = []    # list of dicts: {"col", "op", "value"}
        self.table = None    # scratch table name, or None for the plain sheet order
        self.count = 0       # rows visible in this view
        self.total = 0       # rows in the sheet

    @property
    def is_identity(self):
        return self.table is None

    def describe(self):
        parts = []
        if self.sorts:
            parts.append("Sorted by " + ", ".join(
                f"{c} ({'A to Z' if a else 'Z to A'})" for c, a in self.sorts))
        for f in self.filters:
            label = FILTER_OP_LABELS.get(f["op"], f["op"])
            if f["op"] in LIST_OPS:
                n = len(f.get("values") or []) + (1 if f.get("blanks") and f["op"] == "in" else 0)
                value = f" {n:,} value{'s' if n != 1 else ''}"
            elif f["op"] in NO_VALUE_OPS:
                value = ""
            else:
                value = f' "{f["value"]}"'
            parts.append(f"{f['col']} {label}{value}")
        return "; ".join(parts)


class Workbook:
    """One open workbook. Methods may be called from worker threads."""

    def __init__(self, path=None, password=None):
        self.session_dir = tempfile.mkdtemp(prefix="bigsheet-")
        ext_dir = os.path.join(self.session_dir, "no-extensions")
        tmp_dir = os.path.join(self.session_dir, "spill")
        os.makedirs(ext_dir)
        os.makedirs(tmp_dir)

        self.con = duckdb.connect(":memory:", config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "allow_community_extensions": False,
            "extension_directory": ext_dir,
            "temp_directory": tmp_dir,
            "memory_limit": f"{config.memory_limit_mb()}MB",
        })
        self.crypto_available = False
        ext = _httpfs_extension_path()
        if ext is not None:
            try:
                self.con.execute(f"LOAD {sql_string(str(ext))}")
                self.crypto_available = True
            except duckdb.Error:
                self.crypto_available = False
        for setting in (
            "SET disabled_filesystems = 'HTTPFileSystem,S3FileSystem,HuggingFaceFileSystem'",
            "SET disabled_filesystems = 'HTTPFileSystem,S3FileSystem'",
        ):
            try:
                self.con.execute(setting)
                break
            except duckdb.Error:
                continue
        try:
            self.con.execute("SET temp_file_encryption = true")
        except duckdb.Error:
            pass
        self.con.execute("SET lock_configuration = true")

        self._tl = threading.local()
        self._cursors = []
        self._cursor_lock = threading.Lock()
        self._gen = 0
        self.views = {}
        self.undo_stack = []
        self.redo_stack = []

        self.path = None
        self.encrypted = False
        self.untitled = path is None
        if path is None:
            temp_path = os.path.join(self.session_dir, "untitled.bigsheet")
            key = secrets.token_hex(32) if self.crypto_available else None
            self._attach(temp_path, key)
            self.encrypted = key is not None
        else:
            self._attach(path, password, probe=True)
            self.path = os.path.abspath(path)
            self.encrypted = bool(password)
        self.con.execute("ATTACH ':memory:' AS scratch")
        self.con.execute("USE wb")
        self._ensure_meta()

    # ------------------------------------------------------------------ setup

    def _attach(self, path, key=None, probe=False):
        opts = f" (ENCRYPTION_KEY {sql_string(key)})" if key else ""
        try:
            self.con.execute(f"ATTACH {sql_string(path)} AS wb{opts}")
        except duckdb.Error as e:
            msg = str(e)
            if probe and "without a key" in msg:
                raise PasswordRequired("This workbook is protected with a password.")
            if probe and "Wrong encryption key" in msg:
                raise WrongPassword("The password is not correct.")
            if "crypto module" in msg:
                raise EngineError(
                    "Encrypted workbooks need the bundled encryption component "
                    "(duckdb-extension-httpfs). Reinstall with requirements.txt.")
            raise EngineError(_clean_db_error(e))

    def _ensure_meta(self):
        c = self.con
        c.execute(f"CREATE TABLE IF NOT EXISTS wb.main.{META_PREFIX}sheets (name VARCHAR, ord INTEGER)")
        c.execute(f"CREATE TABLE IF NOT EXISTS wb.main.{META_PREFIX}formulas "
                  "(sheet VARCHAR, col VARCHAR, formula VARCHAR, is_sql BOOLEAN)")
        c.execute(f"CREATE TABLE IF NOT EXISTS wb.main.{META_PREFIX}formats "
                  "(sheet VARCHAR, col VARCHAR, fmt VARCHAR)")
        c.execute(f"CREATE TABLE IF NOT EXISTS wb.main.{META_PREFIX}pivots (sheet VARCHAR, spec VARCHAR)")
        self._repair_tables()

    def cursor(self):
        """A DuckDB cursor for the calling thread (cursors are not thread safe)."""
        c = getattr(self._tl, "cursor", None)
        if c is None or getattr(self._tl, "gen", -1) != self._gen:
            c = self.con.cursor()
            c.execute("USE wb")
            with self._cursor_lock:
                self._cursors.append(c)
            self._tl.cursor = c
            self._tl.gen = self._gen
        return c

    def release_cursor(self):
        """Close the calling thread's cursor so it holds no open transaction."""
        c = getattr(self._tl, "cursor", None)
        self._tl.cursor = None
        if c is not None:
            with self._cursor_lock:
                if c in self._cursors:
                    self._cursors.remove(c)
            try:
                c.close()
            except Exception:
                pass

    def _close_all_cursors(self):
        with self._cursor_lock:
            for c in self._cursors:
                try:
                    c.close()
                except Exception:
                    pass
            self._cursors = []
        self._gen += 1

    def memory_status(self):
        """(used_mb, limit_text) for the engine."""
        used = self.con.execute(
            "SELECT coalesce(sum(memory_usage_bytes), 0) FROM duckdb_memory()").fetchone()[0]
        limit = self.con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        return int(used // (1024 * 1024)), limit

    def interrupt_all(self):
        with self._cursor_lock:
            cursors = list(self._cursors)
        for c in cursors:
            try:
                c.interrupt()
            except Exception:
                pass

    def close(self):
        with self._cursor_lock:
            for c in self._cursors:
                try:
                    c.close()
                except Exception:
                    pass
            self._cursors = []
        try:
            self.con.close()
        except Exception:
            pass
        shutil.rmtree(self.session_dir, ignore_errors=True)

    # ------------------------------------------------------------ name helpers

    @staticmethod
    def t(sheet):
        return "wb.main." + quote_ident(sheet)

    def _exec(self, sql, params=None):
        try:
            return self.cursor().execute(sql, params or [])
        except duckdb.InterruptException:
            raise EngineError("Cancelled.")
        except duckdb.Error as e:
            raise EngineError(_clean_db_error(e))

    def _table_exists(self, name):
        r = self._exec(
            "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'wb' "
            "AND schema_name = 'main' AND table_name = ?", [name]).fetchone()
        return r[0] > 0

    def unique_sheet_name(self, base):
        base = (base or "Sheet").strip() or "Sheet"
        base = base.replace("\n", " ")[:100]
        if base.lower().startswith(META_PREFIX):
            base = "Sheet " + base
        existing = {s.lower() for s in self.all_table_names()}
        if base.lower() not in existing:
            return base
        n = 2
        while f"{base} ({n})".lower() in existing:
            n += 1
        return f"{base} ({n})"

    def all_table_names(self):
        rows = self._exec(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = 'wb' "
            "AND schema_name = 'main'").fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ sheets

    def sheets(self):
        tables = [n for n in self.all_table_names() if not n.startswith(META_PREFIX)]
        order = dict(self._exec(f"SELECT name, ord FROM wb.main.{META_PREFIX}sheets").fetchall())
        big = 10 ** 9
        return sorted(tables, key=lambda n: (order.get(n, big), n.lower()))

    def _register_sheet(self, name):
        nxt = self._exec(f"SELECT coalesce(max(ord), 0) + 1 FROM wb.main.{META_PREFIX}sheets").fetchone()[0]
        self._exec(f"INSERT INTO wb.main.{META_PREFIX}sheets VALUES (?, ?)", [name, nxt])

    def _repair_tables(self):
        """Give any table that lacks the hidden row id one (e.g. made in the SQL window)."""
        for name in self.all_table_names():
            if name.startswith(META_PREFIX):
                continue
            cols = [c for c, _ in self._columns_raw(name)]
            if RID not in cols:
                self._exec(f"ALTER TABLE {self.t(name)} ADD COLUMN {RID} BIGINT")
                self._exec(f"UPDATE {self.t(name)} SET {RID} = rowid")
                self._exec(f"ALTER TABLE {self.t(name)} ALTER COLUMN {RID} SET NOT NULL")
            registered = self._exec(
                f"SELECT count(*) FROM wb.main.{META_PREFIX}sheets WHERE name = ?", [name]).fetchone()[0]
            if not registered:
                self._register_sheet(name)

    def _columns_raw(self, sheet):
        rows = self._exec(
            "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = 'wb' "
            "AND schema_name = 'main' AND table_name = ? ORDER BY column_index", [sheet]).fetchall()
        return rows

    def columns(self, sheet):
        """[(name, type)] excluding the hidden row id."""
        return [(c, t) for c, t in self._columns_raw(sheet) if c != RID]

    def column_type(self, sheet, col):
        for c, t in self.columns(sheet):
            if c == col:
                return t
        raise EngineError(f"Column {col} not found")

    def row_total(self, sheet):
        return self._exec(f"SELECT count(*) FROM {self.t(sheet)}").fetchone()[0]

    def rename_sheet(self, old, new):
        new = new.strip()
        if not new:
            raise EngineError("A sheet name cannot be empty.")
        if new.lower().startswith(META_PREFIX):
            raise EngineError("That name is reserved.")
        if new != old and new.lower() in {s.lower() for s in self.all_table_names()}:
            raise EngineError("Another sheet already has that name.")
        self._exec(f"ALTER TABLE {self.t(old)} RENAME TO {quote_ident(new)}")
        self._exec(f"UPDATE wb.main.{META_PREFIX}sheets SET name = ? WHERE name = ?", [new, old])
        self._exec(f"UPDATE wb.main.{META_PREFIX}formulas SET sheet = ? WHERE sheet = ?", [new, old])
        self._exec(f"UPDATE wb.main.{META_PREFIX}formats SET sheet = ? WHERE sheet = ?", [new, old])
        self._exec(f"UPDATE wb.main.{META_PREFIX}pivots SET sheet = ? WHERE sheet = ?", [new, old])
        for sheet_name, spec in self._exec(f"SELECT sheet, spec FROM wb.main.{META_PREFIX}pivots").fetchall():
            data = json.loads(spec)
            if data.get("source") == old:
                data["source"] = new
                self._exec(f"UPDATE wb.main.{META_PREFIX}pivots SET spec = ? WHERE sheet = ?",
                           [json.dumps(data), sheet_name])
        if old in self.views:
            self.views[new] = self.views.pop(old)
        self._clear_undo()

    def delete_sheet(self, sheet):
        self._drop_view_table(sheet)
        self._exec(f"DROP TABLE {self.t(sheet)}")
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}sheets WHERE name = ?", [sheet])
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}formulas WHERE sheet = ?", [sheet])
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}formats WHERE sheet = ?", [sheet])
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}pivots WHERE sheet = ?", [sheet])
        self.views.pop(sheet, None)
        self._clear_undo()

    def duplicate_sheet(self, sheet):
        name = self.unique_sheet_name(sheet + " copy")
        self._exec(f"CREATE TABLE {self.t(name)} AS SELECT * FROM {self.t(sheet)} ORDER BY {RID}")
        self._register_sheet(name)
        self._exec(
            f"INSERT INTO wb.main.{META_PREFIX}formulas SELECT ?, col, formula, is_sql "
            f"FROM wb.main.{META_PREFIX}formulas WHERE sheet = ?", [name, sheet])
        self._exec(
            f"INSERT INTO wb.main.{META_PREFIX}formats SELECT ?, col, fmt "
            f"FROM wb.main.{META_PREFIX}formats WHERE sheet = ?", [name, sheet])
        return name

    def create_sheet_from_query(self, name, query, params=None):
        """Create a sheet from any SELECT, numbering rows in the query's order."""
        name = self.unique_sheet_name(name)
        desc = self._exec(f"SELECT * FROM ({query}) LIMIT 0", params).description
        cols = [d[0] for d in desc]
        select = "* EXCLUDE (__rid)" if RID in cols else "*"
        self._exec(
            f"CREATE TABLE {self.t(name)} AS SELECT row_number() OVER () - 1 AS {RID}, {select} "
            f"FROM ({query})", params)
        self._register_sheet(name)
        return name

    # ------------------------------------------------------------------ import

    def import_file(self, path, options=None):
        """Import a CSV, TSV, Excel, Parquet or JSON file. Returns the new sheet names."""
        options = options or {}
        ext = os.path.splitext(path)[1].lower()
        base = options.get("sheet_name") or os.path.splitext(os.path.basename(path))[0]
        if ext in (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"):
            return self._import_excel(path, base, options)
        if ext == ".parquet":
            return [self._create_from_reader(base, "read_parquet(?)", [path])]
        if ext in (".json", ".jsonl", ".ndjson"):
            return [self._create_from_reader(base, "read_json_auto(?)", [path])]
        return [self._import_csv(base, path, options)]

    @staticmethod
    def _csv_reader(options, tolerant=False, serial=False, rejects=None):
        args = ["?"]
        delim = options.get("delimiter")
        if delim:
            args.append(f"delim = {sql_string(delim)}")
        header = options.get("header")
        if header is not None:
            args.append(f"header = {'true' if header else 'false'}")
        enc = options.get("encoding")
        if enc and enc != "utf-8":
            args.append(f"encoding = {sql_string(enc)}")
        if options.get("all_text"):
            args.append("all_varchar = true")
        if options.get("skip_bad"):
            args.append("ignore_errors = true")
        if tolerant:
            # Rows with missing fields are padded with blanks; extra fields are ignored.
            args.append("null_padding = true")
            args.append("strict_mode = false")
        if serial:
            args.append("parallel = false")
        if rejects and not options.get("skip_bad"):
            # Skip rows that cannot be read, but keep a record of them to tell the user.
            args.append("ignore_errors = true")
            args.append(f"store_rejects = true, rejects_table = {sql_string(rejects)}, "
                        f"rejects_scan = {sql_string(rejects + '_scan')}")
        args.append("sample_size = 100000")
        return "read_csv(" + ", ".join(args) + ")"

    def _csv_layout_ok(self, reader, params):
        """False when the file cannot be read normally, or collapses into one column."""
        try:
            desc = self.cursor().execute(f"DESCRIBE SELECT * FROM {reader}", params).fetchall()
        except duckdb.Error:
            return False
        if len(desc) == 1 and any(ch in str(desc[0][0]) for ch in ",;\t|"):
            return False
        return True

    def _import_csv(self, base, path, options):
        rejects = f"bs_rejects_{secrets.token_hex(4)}"
        name = None
        if self._csv_layout_ok(self._csv_reader(options), [path]):
            try:
                name = self._create_from_reader(base, self._csv_reader(options, rejects=rejects), [path],
                                                csv_mode=True)
            except EngineError:
                name = None  # fall through to the tolerant reader
        if name is None:
            delim = options.get("delimiter") or self._guess_delimiter(path, options.get("encoding"))
            opts = dict(options, delimiter=delim)
            try:
                name = self._create_from_reader(
                    base, self._csv_reader(opts, tolerant=True, rejects=rejects), [path], csv_mode=True)
            except EngineError as e:
                if "parallel" not in str(e).lower():
                    raise
                name = self._create_from_reader(
                    base, self._csv_reader(opts, tolerant=True, serial=True, rejects=rejects), [path],
                    csv_mode=True)
        self._note_rejects(name, rejects)
        return name

    def _note_rejects(self, sheet, rejects):
        """Remember which rows were skipped so the user can be told."""
        self.import_notes = getattr(self, "import_notes", {})
        cur = self.cursor()
        try:
            rows = cur.execute(f"SELECT DISTINCT line, error_type FROM {quote_ident(rejects)} ORDER BY line").fetchall()
        except duckdb.Error:
            return
        finally:
            for t in (rejects, rejects + "_scan"):
                try:
                    cur.execute(f"DROP TABLE IF EXISTS {quote_ident(t)}")
                except duckdb.Error:
                    pass
        if not rows:
            return
        by_line = {}
        for line, kind in rows:
            by_line.setdefault(int(line), kind)
        kinds = {"CAST": "a value of the wrong type", "MISSING COLUMNS": "too few fields",
                 "TOO MANY COLUMNS": "too many fields", "UNQUOTED VALUE": "a stray quote",
                 "LINE SIZE OVER MAXIMUM": "a line that is too long", "INVALID UNICODE": "unreadable characters"}
        reasons = sorted({kinds.get(str(k), str(k).lower()) for _, k in rows})
        line_numbers = sorted(by_line)
        lines = ", ".join(f"{n:,}" for n in line_numbers[:5]) + (" and more" if len(line_numbers) > 5 else "")
        n = len(line_numbers)
        self.import_notes[sheet] = (f"{n:,} row{'s' if n != 1 else ''} could not be read and "
                                    f"{'were' if n != 1 else 'was'} skipped (line{'s' if n != 1 else ''} "
                                    f"{lines}: {', '.join(reasons)}). All other rows were imported. To keep "
                                    "every row, import again with 'Treat all columns as text'.")

    @staticmethod
    def _guess_delimiter(path, encoding=None):
        """The separator that appears most consistently in the first lines of the file."""
        enc = {"latin-1": "latin-1", "utf-16": "utf-16"}.get(encoding or "", "utf-8")
        try:
            with open(path, "r", encoding=enc, errors="replace", newline="") as f:
                lines = [f.readline() for _ in range(50)]
        except OSError:
            return None
        lines = [ln for ln in lines if ln.strip()]
        best, best_score = None, 0
        for ch in (",", ";", "\t", "|"):
            counts = [ln.count(ch) for ln in lines]
            if not counts or counts[0] == 0:
                continue
            same = sum(1 for c in counts if c == counts[0])
            score = counts[0] * same
            if score > best_score:
                best, best_score = ch, score
        return best

    def _create_from_reader(self, base, reader, params, csv_mode=False):
        name = self.unique_sheet_name(base)
        try:
            self.cursor().execute(
                f"CREATE TABLE {self.t(name)} AS SELECT row_number() OVER () - 1 AS {RID}, * "
                f"FROM {reader}", params)
        except duckdb.InterruptException:
            raise EngineError("Import cancelled.")
        except duckdb.Error as e:
            msg = _clean_db_error(e)
            if csv_mode and ("convert" in msg.lower() or "csv" in msg.lower()):
                msg += ("\n\nTip: try again with 'Treat all columns as text' or "
                        "'Skip rows that cannot be read' in the import options.")
            raise EngineError(msg)
        self._register_sheet(name)
        return name

    @staticmethod
    def excel_sheet_names(path):
        from python_calamine import CalamineWorkbook
        return CalamineWorkbook.from_path(path).sheet_names

    def _import_excel(self, path, base, options):
        from python_calamine import CalamineWorkbook
        book = CalamineWorkbook.from_path(path)
        wanted = options.get("excel_sheets") or book.sheet_names[:1]
        created = []
        for sheet_name in wanted:
            sheet = book.get_sheet_by_name(sheet_name)
            tmp = os.path.join(self.session_dir, f"xlsx-{secrets.token_hex(6)}.csv")
            try:
                with open(tmp, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    rows = sheet.iter_rows()
                    head = []
                    for row in rows:
                        head.append([_excel_cell(v) for v in row])
                        if len(head) >= 30:
                            break
                    start = _header_row(head) if options.get("header") is not False else 0
                    for row in head[start:]:
                        writer.writerow(row)
                    for row in rows:
                        writer.writerow([_excel_cell(v) for v in row])
                label = base if len(wanted) == 1 else f"{base} - {sheet_name}"
                opts = dict(options)
                opts["delimiter"] = ","
                opts["encoding"] = "utf-8"
                if os.path.getsize(tmp) == 0:
                    continue
                created.append(self._create_from_reader(label, self._csv_reader(opts), [tmp], csv_mode=True))
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if not created:
            raise EngineError("The selected Excel sheets are empty.")
        return created

    # ------------------------------------------------------------------- views

    def view(self, sheet):
        v = self.views.get(sheet)
        if v is None:
            v = ViewState()
            v.total = v.count = self.row_total(sheet)
            self.views[sheet] = v
        return v

    def refresh_counts(self, sheet):
        v = self.view(sheet)
        v.total = self.row_total(sheet)
        if v.is_identity:
            v.count = v.total
        else:
            v.count = self._exec(f"SELECT count(*) FROM scratch.main.{quote_ident(v.table)}").fetchone()[0]
        return v

    def _where(self, sheet, filters, alias="", inline=False):
        """WHERE clause for filters. With inline=True values are written as SQL literals."""
        sql, params = self._where_params(sheet, filters, alias)
        if not inline or not params:
            return sql, params
        pieces = sql.split("?")
        if len(pieces) != len(params) + 1:
            raise EngineError("A column name contains a question mark, which this filter cannot use.")
        out = pieces[0]
        for value, rest in zip(params, pieces[1:]):
            if isinstance(value, list):
                lit = "[" + ", ".join(sql_string(v) for v in value) + "]::VARCHAR[]"
            else:
                lit = sql_string(value)
            out += lit + rest
        return out, []

    def _where_params(self, sheet, filters, alias=""):
        if not filters:
            return "", []
        types = dict(self.columns(sheet))
        clauses, params = [], []
        pre = alias + "." if alias else ""
        for f in filters:
            col, op, value = f["col"], f["op"], f.get("value")
            if col not in types:
                continue
            typ = types[col]
            c = pre + quote_ident(col)
            text = f"lower(CAST({c} AS VARCHAR))"
            if op in LIST_OPS:
                values = [str(x) for x in (f.get("values") or [])]
                blanks = bool(f.get("blanks"))
                listed = f"CAST({c} AS VARCHAR) IN (SELECT unnest(?::VARCHAR[]))"
                if op == "in":
                    parts = []
                    if values:
                        parts.append(listed)
                        params.append(values)
                    if blanks:
                        parts.append(f"{c} IS NULL")
                    clauses.append("(" + " OR ".join(parts) + ")" if parts else "false")
                else:
                    cond = f"NOT ({listed})" if values else "true"
                    if values:
                        params.append(values)
                    clauses.append(f"({c} IS NULL OR {cond})" if blanks else f"({c} IS NOT NULL AND {cond})")
            elif op == "empty":
                clauses.append(f"({c} IS NULL OR CAST({c} AS VARCHAR) = '')")
            elif op == "not_empty":
                clauses.append(f"({c} IS NOT NULL AND CAST({c} AS VARCHAR) <> '')")
            elif op == "contains":
                clauses.append(f"contains({text}, lower(?))")
                params.append(str(value))
            elif op == "not_contains":
                clauses.append(f"({c} IS NULL OR NOT contains({text}, lower(?)))")
                params.append(str(value))
            elif op == "starts":
                clauses.append(f"starts_with({text}, lower(?))")
                params.append(str(value))
            elif op == "ends":
                clauses.append(f"ends_with({text}, lower(?))")
                params.append(str(value))
            else:
                sql_op = {"eq": "=", "ne": "IS DISTINCT FROM", "gt": ">", "ge": ">=",
                          "lt": "<", "le": "<="}[op]
                if typ.upper() == "VARCHAR":
                    if op in ("eq", "ne"):
                        clauses.append(f"lower({c}) {sql_op} lower(?)")
                    else:
                        clauses.append(f"{c} {sql_op} ?")
                    params.append(str(value))
                else:
                    clauses.append(f"{c} {sql_op} TRY_CAST(? AS {typ})")
                    params.append(normalize_value(typ, value))
        if not clauses:
            return "", []
        return " WHERE " + " AND ".join(clauses), params

    def validate_filter_value(self, sheet, col, op, value):
        if op in NO_VALUE_OPS or op in LIST_OPS or op in ("contains", "not_contains", "starts", "ends"):
            return
        typ = self.column_type(sheet, col)
        if typ.upper() == "VARCHAR":
            return
        ok = self._exec(f"SELECT TRY_CAST(? AS {typ}) IS NOT NULL", [normalize_value(typ, value)]).fetchone()[0]
        if not ok:
            raise EngineError(f'"{value}" is not a valid {friendly_type(typ)} for column {col}. '
                              "Values like 1234.5, 1,234.50, 31.01.2024 or 2024-01-31 work.")

    def prepare_view(self, sheet, sorts, filters):
        """Build the scratch table for a sort/filter (worker thread). Returns (table, count)."""
        types = dict(self.columns(sheet))
        sorts = [(c, a) for c, a in sorts if c in types]
        filters = [f for f in filters if f["col"] in types]
        if not sorts and not filters:
            return None, self.row_total(sheet)
        self._gen_view = getattr(self, "_gen_view", 0) + 1
        table = f"v{self._gen_view}_{secrets.token_hex(3)}"
        order = ", ".join(
            f"{quote_ident(c)} {'ASC' if a else 'DESC'} NULLS LAST" for c, a in sorts)
        order = (order + ", " if order else "") + RID
        where, params = self._where(sheet, filters)
        self._exec(
            f"CREATE TABLE scratch.main.{quote_ident(table)} AS "
            f"SELECT row_number() OVER (ORDER BY {order}) - 1 AS __pos, {RID} "
            f"FROM {self.t(sheet)}{where} ORDER BY __pos", params)
        count = self._exec(f"SELECT count(*) FROM scratch.main.{quote_ident(table)}").fetchone()[0]
        return table, count

    def apply_view(self, sheet, sorts, filters, table, count):
        """Switch to a prepared view (main thread)."""
        v = self.view(sheet)
        old = v.table
        v.sorts, v.filters, v.table, v.count = list(sorts), list(filters), table, count
        v.total = self.row_total(sheet)
        if old and old != table:
            self._drop_scratch(old)

    def rebuild_view(self, sheet):
        """Rebuild the current view after rows or row ids changed (synchronous)."""
        v = self.view(sheet)
        table, count = self.prepare_view(sheet, v.sorts, v.filters)
        self.apply_view(sheet, v.sorts, v.filters, table, count)
        return self.view(sheet)

    def _drop_scratch(self, table):
        try:
            self.con.execute(f"DROP TABLE IF EXISTS scratch.main.{quote_ident(table)}")
        except duckdb.Error:
            pass

    def _drop_view_table(self, sheet):
        v = self.views.get(sheet)
        if v and v.table:
            self._drop_scratch(v.table)
            v.table = None

    def reset_view(self, sheet):
        self._drop_view_table(sheet)
        v = self.view(sheet)
        v.sorts, v.filters = [], []
        self.refresh_counts(sheet)

    def distinct_values(self, sheet, col, filters=None, search=None, limit=1000):
        """Values of a column with row counts, for the column filter list.

        Respects the other filters of the view (as Excel does). Returns
        (rows, truncated) where rows are (text or None, count) in value order.
        """
        other = [f for f in (filters or []) if f["col"] != col]
        where, params = self._where(sheet, other)
        q = quote_ident(col)
        cond = ""
        if search:
            cond = (" AND " if where else " WHERE ") + f"contains(lower(CAST({q} AS VARCHAR)), lower(?))"
            params = params + [search]
        rows = self._exec(
            f"SELECT CAST({q} AS VARCHAR), count(*) FROM {self.t(sheet)}{where}{cond} "
            f"GROUP BY {q} ORDER BY {q} NULLS FIRST LIMIT {int(limit) + 1}", params).fetchall()
        return rows[:limit], len(rows) > limit

    # ---------------------------------------------------------------- formats

    def column_formats(self, sheet):
        rows = self._exec(f"SELECT col, fmt FROM wb.main.{META_PREFIX}formats WHERE sheet = ?",
                          [sheet]).fetchall()
        out = {}
        for col, fmt in rows:
            try:
                out[col] = json.loads(fmt)
            except ValueError:
                pass
        return out

    def set_column_format(self, sheet, cols, updates):
        """Merge format settings into columns. A value of None removes that setting."""
        current = self.column_formats(sheet)
        for col in cols:
            fmt = dict(current.get(col, {}))
            for key, value in updates.items():
                if value is None:
                    fmt.pop(key, None)
                else:
                    fmt[key] = value
            self._exec(f"DELETE FROM wb.main.{META_PREFIX}formats WHERE sheet = ? AND col = ?", [sheet, col])
            if fmt:
                self._exec(f"INSERT INTO wb.main.{META_PREFIX}formats VALUES (?, ?, ?)",
                           [sheet, col, json.dumps(fmt)])

    def clear_column_format(self, sheet, cols):
        for col in cols:
            self._exec(f"DELETE FROM wb.main.{META_PREFIX}formats WHERE sheet = ? AND col = ?", [sheet, col])

    # ----------------------------------------------------------------- pivots

    def pivot_spec(self, sheet):
        row = self._exec(f"SELECT spec FROM wb.main.{META_PREFIX}pivots WHERE sheet = ?", [sheet]).fetchone()
        return json.loads(row[0]) if row else None

    def _pivot_query(self, spec):
        source = spec["source"]
        if not self._table_exists(source):
            raise EngineError(f"The source sheet {source} no longer exists.")
        types = dict(self.columns(source))
        rows = [c for c in spec.get("rows", []) if c in types]
        pcol = next((c for c in spec.get("columns", []) if c in types), None)
        values = [(f, c) for f, c in spec.get("values", []) if f == "count" or c in types]
        where, params = self._where(source, spec.get("filters") or [], inline=True)
        src = f"(SELECT * FROM {self.t(source)}{where})"
        if not values and not rows:
            return "SELECT 'Add fields to Rows and Values in the pivot panel' AS \"Pivot table\"", []
        if not values:
            values = [("count", None)]
        return self._summary_query(src, rows, values, pcol), params

    def create_pivot(self, source, spec=None, name=None):
        spec = dict(spec or {})
        spec["source"] = source
        spec.setdefault("rows", [])
        spec.setdefault("columns", [])
        spec.setdefault("values", [])
        query, params = self._pivot_query(spec)
        new = self.create_sheet_from_query(name or f"Pivot of {source}", query, params)
        self._exec(f"INSERT INTO wb.main.{META_PREFIX}pivots VALUES (?, ?)", [new, json.dumps(spec)])
        return new

    def update_pivot(self, sheet, spec):
        """Rebuild a pivot sheet in place from a (changed) spec."""
        old = self.pivot_spec(sheet) or {}
        spec = dict(spec)
        spec["source"] = spec.get("source") or old.get("source")
        query, params = self._pivot_query(spec)
        self._exec(f"CREATE OR REPLACE TABLE {self.t(sheet)} AS SELECT row_number() OVER () - 1 AS {RID}, * "
                   f"FROM ({query})", params)
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}pivots WHERE sheet = ?", [sheet])
        self._exec(f"INSERT INTO wb.main.{META_PREFIX}pivots VALUES (?, ?)", [sheet, json.dumps(spec)])
        self._format_pivot(sheet, spec)
        self.reset_view(sheet)
        self._clear_undo()

    def _format_pivot(self, sheet, spec):
        """Give pivot value columns a readable number format (copied from the source column if set)."""
        grouping = set(spec.get("rows", [])) | set(spec.get("columns", []))
        src_formats = self.column_formats(spec["source"]) if self._table_exists(spec["source"]) else {}
        values = spec.get("values") or []
        template = {}
        if values:
            func, col = values[0]
            if func in ("sum", "avg", "min", "max", "median") and col in src_formats:
                template = {k: v for k, v in src_formats[col].items()
                            if k in ("numfmt", "symbol", "decimals") and not str(v).startswith("date")}
        self.clear_column_format(sheet, [c for c, _ in self.columns(sheet)])
        for col, typ in self.columns(sheet):
            if col in grouping or not is_numeric(typ):
                continue
            fmt = dict(template) if template else {"numfmt": "number"}
            if typ.upper() in ("BIGINT", "INTEGER", "HUGEINT", "SMALLINT", "TINYINT", "UBIGINT"):
                fmt.setdefault("decimals", 0)
            self.set_column_format(sheet, [col], fmt)

    def create_sample_sheet(self, rows=1_000_000):
        """A sheet of made-up sales data to try BigSheet with."""
        query = f"""
            SELECT range + 1 AS order_id,
                   'Customer ' || (range % 25000 + 1) AS customer,
                   ['Dhaka', 'Chattogram', 'Sylhet', 'Khulna', 'Rajshahi', 'Barishal'][(range % 6) + 1] AS city,
                   ['Electronics', 'Grocery', 'Clothing', 'Books', 'Home'][(hash(range) % 5)::INT + 1] AS category,
                   round(((hash(range * 7) % 500000)::INT / 100.0) + 1, 2) AS amount,
                   (hash(range * 13) % 20)::INT + 1 AS quantity,
                   DATE '2023-01-01' + (range % 1000)::INT AS order_date
            FROM range({int(rows)}) ORDER BY range"""
        return self.create_sheet_from_query("Sample sales", query)

    # ---------------------------------------------------------------- reading

    def fetch(self, sheet, start, count, con=None):
        """Rows [start, start+count) of the current view: (rids, rows)."""
        v = self.view(sheet)
        cols = ", ".join("t." + quote_ident(c) for c, _ in self.columns(sheet))
        con = con or self.cursor()
        end = start + count - 1
        if v.is_identity:
            rows = con.execute(
                f"SELECT t.{RID}, {cols} FROM {self.t(sheet)} t "
                f"WHERE t.{RID} BETWEEN ? AND ? ORDER BY t.{RID}", [start, end]).fetchall()
        else:
            rows = con.execute(
                f"SELECT t.{RID}, {cols} FROM (SELECT * FROM scratch.main.{quote_ident(v.table)} "
                f"WHERE __pos BETWEEN ? AND ?) p JOIN {self.t(sheet)} t ON t.{RID} = p.{RID} "
                f"ORDER BY p.__pos", [start, end]).fetchall()
        return [r[0] for r in rows], [r[1:] for r in rows]

    def _rows_source(self, sheet):
        """SQL for all rows of the current view, in view order, with a __pos column."""
        v = self.view(sheet)
        if v.is_identity:
            return f"(SELECT {RID} AS __pos, * FROM {self.t(sheet)})"
        return (f"(SELECT p.__pos, t.* FROM scratch.main.{quote_ident(v.table)} p "
                f"JOIN {self.t(sheet)} t ON t.{RID} = p.{RID})")

    def _range_source(self, sheet, start, end):
        v = self.view(sheet)
        if v.is_identity:
            return (f"(SELECT {RID} AS __pos, * FROM {self.t(sheet)} "
                    f"WHERE {RID} BETWEEN {int(start)} AND {int(end)})")
        return (f"(SELECT p.__pos, t.* FROM (SELECT * FROM scratch.main.{quote_ident(v.table)} "
                f"WHERE __pos BETWEEN {int(start)} AND {int(end)}) p "
                f"JOIN {self.t(sheet)} t ON t.{RID} = p.{RID})")

    def rids_for_positions(self, sheet, positions):
        if not positions:
            return {}
        v = self.view(sheet)
        if v.is_identity:
            return {p: p for p in positions}
        rows = self._exec(
            f"SELECT __pos, {RID} FROM scratch.main.{quote_ident(v.table)} "
            f"WHERE __pos IN (SELECT unnest(?::BIGINT[]))", [list(positions)]).fetchall()
        return dict(rows)

    def cell_text(self, sheet, col, positions):
        """Text of cells by view position, for copying."""
        src = self._range_source(sheet, min(positions), max(positions))
        rows = self._exec(f"SELECT __pos, CAST({quote_ident(col)} AS VARCHAR) FROM {src}").fetchall()
        return dict(rows)

    def block_text(self, sheet, cols, start, end):
        names = ", ".join(f"CAST({quote_ident(c)} AS VARCHAR)" for c in cols)
        src = self._range_source(sheet, start, end)
        return self._exec(f"SELECT {names} FROM {src} ORDER BY __pos").fetchall()

    # ---------------------------------------------------------------- editing

    def _clear_undo(self):
        self.undo_stack.clear()
        self.redo_stack.clear()

    def _validate_texts(self, sheet, col, typ, texts):
        if typ.upper() == "VARCHAR":
            return
        bad = self._exec(
            f"SELECT v FROM (SELECT unnest(?::VARCHAR[]) v) "
            f"WHERE v IS NOT NULL AND TRY_CAST(v AS {typ}) IS NULL LIMIT 1", [texts]).fetchone()
        if bad:
            raise EngineError(f'"{bad[0]}" is not a valid {friendly_type(typ)} for column {col}.')

    def _write_cells(self, sheet, col, typ, rids, texts):
        cast = f"TRY_CAST(u.v AS {typ})" if typ.upper() != "VARCHAR" else "u.v"
        self._exec(
            f"UPDATE {self.t(sheet)} SET {quote_ident(col)} = {cast} "
            f"FROM (SELECT unnest(?::BIGINT[]) r, unnest(?::VARCHAR[]) v) u "
            f"WHERE {self.t(sheet)}.{RID} = u.r", [list(rids), texts])

    def set_block(self, sheet, changes, record=True):
        """Set several columns at once: changes = [(col, rids, texts)]. One undo step."""
        types = dict(self.columns(sheet))
        prepared = []
        for col, rids, texts in changes:
            if not rids:
                continue
            if col not in types:
                raise EngineError(f"Column {col} not found")
            texts = [None if (t is None or str(t) == "") else str(t) for t in texts]
            if types[col].upper() != "VARCHAR":
                texts = [None if t is None else normalize_value(types[col], t) for t in texts]
            self._validate_texts(sheet, col, types[col], texts)
            prepared.append((col, list(rids), texts))
        entry = {"sheet": sheet, "changes": []}
        for col, rids, texts in prepared:
            old = None
            if record:
                old_map = dict(self._exec(
                    f"SELECT {RID}, CAST({quote_ident(col)} AS VARCHAR) FROM {self.t(sheet)} "
                    f"WHERE {RID} IN (SELECT unnest(?::BIGINT[]))", [rids]).fetchall())
                old = [old_map.get(r) for r in rids]
            self._write_cells(sheet, col, types[col], rids, texts)
            entry["changes"].append({"col": col, "rids": rids, "old": old, "new": texts})
        if record and entry["changes"]:
            self.undo_stack.append(entry)
            if len(self.undo_stack) > 200:
                self.undo_stack.pop(0)
            self.redo_stack.clear()

    def set_cells(self, sheet, col, rids, texts, record=True):
        """Set cells from text. Empty text clears the cell. Validates the type first."""
        self.set_block(sheet, [(col, rids, texts)], record=record)

    def clear_range(self, sheet, cols, start, end):
        """Clear a large block of cells (no undo)."""
        sets = ", ".join(f"{quote_ident(c)} = NULL" for c in cols)
        self._exec(f"UPDATE {self.t(sheet)} SET {sets} WHERE {RID} IN "
                   f"(SELECT {RID} FROM {self._range_source(sheet, start, end)})")
        self._clear_undo()

    def _apply_entry(self, entry, which):
        types = dict(self.columns(entry["sheet"]))
        for ch in entry["changes"]:
            self._write_cells(entry["sheet"], ch["col"], types[ch["col"]], ch["rids"], ch[which])

    def undo(self):
        if not self.undo_stack:
            return None
        e = self.undo_stack.pop()
        self._apply_entry(e, "old")
        self.redo_stack.append(e)
        return e["sheet"]

    def redo(self):
        if not self.redo_stack:
            return None
        e = self.redo_stack.pop()
        self._apply_entry(e, "new")
        self.undo_stack.append(e)
        return e["sheet"]

    def insert_rows(self, sheet, before_rid=None, count=1):
        """Insert empty rows before a row id, or at the end when before_rid is None."""
        total = self.row_total(sheet)
        if before_rid is None or before_rid >= total:
            start = total
        else:
            start = int(before_rid)
            self._exec(f"UPDATE {self.t(sheet)} SET {RID} = {RID} + ? WHERE {RID} >= ?", [count, start])
        self._exec(f"INSERT INTO {self.t(sheet)} ({RID}) SELECT range FROM range(?, ?)",
                   [start, start + count])
        self._clear_undo()
        self.rebuild_view(sheet)
        return start

    def delete_positions(self, sheet, ranges):
        """Delete rows by view position ranges [(start, end), ...] inclusive."""
        v = self.view(sheet)
        for start, end in ranges:
            if v.is_identity:
                self._exec(f"DELETE FROM {self.t(sheet)} WHERE {RID} BETWEEN ? AND ?", [start, end])
            else:
                self._exec(
                    f"DELETE FROM {self.t(sheet)} WHERE {RID} IN (SELECT {RID} FROM "
                    f"scratch.main.{quote_ident(v.table)} WHERE __pos BETWEEN ? AND ?)", [start, end])
        self._renumber(sheet)
        self._clear_undo()
        self.rebuild_view(sheet)

    def _renumber(self, sheet):
        t = self.t(sheet)
        self._exec(
            f"UPDATE {t} SET {RID} = m.nr FROM (SELECT {RID} AS old, "
            f"row_number() OVER (ORDER BY {RID}) - 1 AS nr FROM {t}) m "
            f"WHERE {t}.{RID} = m.old AND m.old <> m.nr")

    # ---------------------------------------------------------------- columns

    def add_empty_column(self, sheet, name, typ="VARCHAR"):
        name = self._new_column_name(sheet, name)
        self._exec(f"ALTER TABLE {self.t(sheet)} ADD COLUMN {quote_ident(name)} {typ}")
        return name

    def _new_column_name(self, sheet, name):
        name = (name or "").strip()
        if not name:
            raise EngineError("Give the column a name.")
        if name == RID:
            raise EngineError("That column name is reserved.")
        if name.lower() in {c.lower() for c, _ in self.columns(sheet)}:
            raise EngineError(f"A column named {name} already exists.")
        return name

    def formula_sql(self, sheet, formula, is_sql=False):
        cols = [c for c, _ in self.columns(sheet)]
        if is_sql:
            return formula, (" over " in f" {formula.lower()} " or "over(" in formula.lower())
        try:
            sql, window, _ = translate(formula, cols)
        except FormulaError as e:
            raise EngineError(str(e))
        return sql, window

    def preview_formula(self, sheet, formula, is_sql=False, limit=5):
        expr, _ = self.formula_sql(sheet, formula, is_sql)
        rows = self._exec(
            f"SELECT CAST(x AS VARCHAR) FROM (SELECT {expr} AS x, {RID} FROM {self.t(sheet)} "
            f"ORDER BY {RID} LIMIT {int(limit)})").fetchall()
        return [r[0] for r in rows]

    def add_formula_column(self, sheet, name, formula, is_sql=False):
        name = self._new_column_name(sheet, name)
        expr, window = self.formula_sql(sheet, formula, is_sql)
        typ = self._exec(f"DESCRIBE SELECT {expr} AS x FROM {self.t(sheet)}").fetchone()[1]
        if typ.upper() in ("NULL", '"NULL"'):
            typ = "VARCHAR"
        self._exec(f"ALTER TABLE {self.t(sheet)} ADD COLUMN {quote_ident(name)} {typ}")
        try:
            self._fill_formula(sheet, name, expr, window)
        except EngineError:
            self._exec(f"ALTER TABLE {self.t(sheet)} DROP COLUMN {quote_ident(name)}")
            raise
        self._exec(f"INSERT INTO wb.main.{META_PREFIX}formulas VALUES (?, ?, ?, ?)",
                   [sheet, name, formula, bool(is_sql)])
        self._clear_undo()
        return name

    def _fill_formula(self, sheet, name, expr, window):
        t = self.t(sheet)
        if window:
            self._exec(
                f"UPDATE {t} SET {quote_ident(name)} = s.val FROM "
                f"(SELECT {RID} AS r, {expr} AS val FROM {t}) s WHERE {t}.{RID} = s.r")
        else:
            self._exec(f"UPDATE {t} SET {quote_ident(name)} = {expr}")

    def formulas(self, sheet):
        rows = self._exec(f"SELECT col, formula, is_sql FROM wb.main.{META_PREFIX}formulas "
                          "WHERE sheet = ?", [sheet]).fetchall()
        return {c: (f, s) for c, f, s in rows}

    def recalc_formulas(self, sheet):
        done = 0
        for col, (formula, is_sql) in self.formulas(sheet).items():
            expr, window = self.formula_sql(sheet, formula, is_sql)
            self._fill_formula(sheet, col, expr, window)
            done += 1
        self._clear_undo()
        return done

    def rename_column(self, sheet, old, new):
        new = self._new_column_name(sheet, new) if new.lower() != old.lower() else new.strip()
        self._exec(f"ALTER TABLE {self.t(sheet)} RENAME COLUMN {quote_ident(old)} TO {quote_ident(new)}")
        self._exec(f"UPDATE wb.main.{META_PREFIX}formulas SET col = ? WHERE sheet = ? AND col = ?",
                   [new, sheet, old])
        self._exec(f"UPDATE wb.main.{META_PREFIX}formats SET col = ? WHERE sheet = ? AND col = ?",
                   [new, sheet, old])
        v = self.view(sheet)
        v.sorts = [(new if c == old else c, a) for c, a in v.sorts]
        for f in v.filters:
            if f["col"] == old:
                f["col"] = new
        self._clear_undo()
        return new

    def drop_column(self, sheet, col):
        if len(self.columns(sheet)) <= 1:
            raise EngineError("A sheet needs at least one column.")
        v = self.view(sheet)
        used = any(c == col for c, _ in v.sorts) or any(f["col"] == col for f in v.filters)
        self._exec(f"ALTER TABLE {self.t(sheet)} DROP COLUMN {quote_ident(col)}")
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}formulas WHERE sheet = ? AND col = ?", [sheet, col])
        self._exec(f"DELETE FROM wb.main.{META_PREFIX}formats WHERE sheet = ? AND col = ?", [sheet, col])
        if used:
            v.sorts = [(c, a) for c, a in v.sorts if c != col]
            v.filters = [f for f in v.filters if f["col"] != col]
            self.rebuild_view(sheet)
        self._clear_undo()

    def count_type_failures(self, sheet, col, typ):
        return self._exec(
            f"SELECT count(*) FROM {self.t(sheet)} WHERE {quote_ident(col)} IS NOT NULL "
            f"AND TRY_CAST({quote_ident(col)} AS {typ}) IS NULL").fetchone()[0]

    def change_type(self, sheet, col, typ):
        c = quote_ident(col)
        self._exec(f"ALTER TABLE {self.t(sheet)} ALTER {c} TYPE {typ} USING TRY_CAST({c} AS {typ})")
        self._clear_undo()

    # ---------------------------------------------------------------- analysis

    def selection_stats(self, sheet, cols, start, end):
        types = dict(self.columns(sheet))
        cols = [c for c in cols if c in types]
        if not cols:
            return None
        parts = []
        for c in cols:
            q = quote_ident(c)
            parts.append(f"count({q})")
            if is_numeric(types[c]):
                parts += [f"sum({q})::DOUBLE", f"min({q})::DOUBLE", f"max({q})::DOUBLE", f"count({q})"]
            else:
                parts += ["NULL", "NULL", "NULL", "0"]
        row = self._exec(f"SELECT {', '.join(parts)} FROM {self._range_source(sheet, start, end)}").fetchone()
        count = total = num_count = 0
        mn = mx = None
        for i in range(len(cols)):
            cnt, s, lo, hi, ncnt = row[i * 5:(i + 1) * 5]
            count += cnt or 0
            if ncnt:
                num_count += ncnt
                total += s or 0
                mn = lo if mn is None else min(mn, lo)
                mx = hi if mx is None else max(mx, hi)
        result = {"count": count}
        if num_count:
            result.update(sum=total, avg=total / num_count, min=mn, max=mx, n=num_count)
        return result

    def column_stats(self, sheet, col):
        types = dict(self.columns(sheet))
        typ = types[col]
        q = quote_ident(col)
        src = self._rows_source(sheet)
        extra = f", avg({q})::DOUBLE, sum({q})::DOUBLE" if is_numeric(typ) else ", NULL, NULL"
        row = self._exec(
            f"SELECT count(*), count({q}), approx_count_distinct({q}), "
            f"CAST(min({q}) AS VARCHAR), CAST(max({q}) AS VARCHAR){extra} FROM {src}").fetchone()
        top = self._exec(
            f"SELECT CAST({q} AS VARCHAR) v, count(*) n FROM {src} GROUP BY {q} "
            f"ORDER BY n DESC, v NULLS LAST LIMIT 10").fetchall()
        return {"type": typ, "rows": row[0], "filled": row[1], "empty": row[0] - row[1],
                "distinct": row[2], "min": row[3], "max": row[4], "avg": row[5],
                "sum": row[6], "top": top}

    def find_next(self, sheet, text, from_pos, from_col_index, backward=False):
        """Find text (case-insensitive) in the current view. Returns (pos, col_index, wrapped)."""
        cols = [c for c, _ in self.columns(sheet)]
        needle = text.lower()
        conds = [f"coalesce(contains(lower(CAST({quote_ident(c)} AS VARCHAR)), $1), false)" for c in cols]
        any_cond = "(" + " OR ".join(conds) + ")"
        src = self._rows_source(sheet)
        flags_sql = "[" + ", ".join(conds) + "]"

        def row_flags(pos):
            r = self._exec(f"SELECT {flags_sql} FROM {self._range_source(sheet, pos, pos)}",
                           [needle]).fetchone()
            return r[0] if r else []

        if from_pos is not None and from_pos >= 0:
            flags = row_flags(from_pos)
            idxs = [i for i, f in enumerate(flags) if f]
            idxs = [i for i in idxs if (i < from_col_index if backward else i > from_col_index)]
            if idxs:
                return from_pos, (max(idxs) if backward else min(idxs)), False

        def search(condition, params):
            agg = "max" if backward else "min"
            return self._exec(f"SELECT {agg}(__pos) FROM {src} WHERE {condition} AND {any_cond}",
                              [needle] + params).fetchone()[0]

        start = -1 if from_pos is None else from_pos
        pos = search("__pos < $2" if backward else "__pos > $2", [start])
        wrapped = False
        if pos is None:
            pos = search("true", [])
            wrapped = True
        if pos is None:
            return None
        flags = row_flags(pos)
        idxs = [i for i, f in enumerate(flags) if f]
        return pos, (max(idxs) if backward else min(idxs)), wrapped

    def _summary_query(self, src, group_cols, aggs, pivot_col=None):
        if not aggs:
            raise EngineError("Choose at least one value to calculate.")
        labels = dict(AGGREGATES)
        agg_sql, needed = [], set()
        for func, col in aggs:
            if func == "count":
                expr, alias = "count(*)", "Count of rows"
            else:
                q = quote_ident(col)
                needed.add(col)
                expr = {"count_distinct": f"count(DISTINCT {q})", "sum": f"sum({q})",
                        "avg": f"avg({q})", "min": f"min({q})", "max": f"max({q})",
                        "median": f"median({q})", "count_values": f"count({q})"}[func]
                alias = f"{labels.get(func, func)} of {col}"
            agg_sql.append(f"{expr} AS {quote_ident(alias)}")
        groups = ", ".join(quote_ident(g) for g in group_cols)
        if pivot_col:
            distinct = self._exec(f"SELECT count(DISTINCT {quote_ident(pivot_col)}) FROM {src}").fetchone()[0]
            if distinct > 200:
                raise EngineError(f"{pivot_col} has {distinct:,} different values. "
                                  "Pivot columns work best with 200 or fewer.")
            keep = list(dict.fromkeys(list(group_cols) + [pivot_col] + sorted(needed)))
            inner = f"SELECT {', '.join(quote_ident(k) for k in keep)} FROM {src}"
            using = agg_sql
            if len(agg_sql) == 1:
                using = [agg_sql[0].rsplit(" AS ", 1)[0]]  # columns named after the pivot values only
            query = f"PIVOT ({inner}) ON {quote_ident(pivot_col)} USING {', '.join(using)}"
            if group_cols:
                query += f" GROUP BY {groups} ORDER BY {groups} NULLS LAST"
            return query
        if group_cols:
            return (f"SELECT {groups}, {', '.join(agg_sql)} FROM {src} "
                    f"GROUP BY {groups} ORDER BY {groups} NULLS LAST")
        return f"SELECT {', '.join(agg_sql)} FROM {src}"

    def summarize(self, sheet, group_cols, aggs, pivot_col=None, use_view=True, name=None):
        """Group-by summary (optionally pivoted) into a new sheet."""
        src = self._rows_source(sheet) if use_view else self.t(sheet)
        query = self._summary_query(src, group_cols, aggs, pivot_col)
        return self.create_sheet_from_query(name or f"{sheet} summary", query)

    def remove_duplicates(self, sheet, cols=None, name=None):
        all_cols = [c for c, _ in self.columns(sheet)]
        cols = cols or all_cols
        key = ", ".join(quote_ident(c) for c in cols)
        query = (f"SELECT * EXCLUDE ({RID}) FROM (SELECT DISTINCT ON ({key}) * FROM {self.t(sheet)} "
                 f"ORDER BY {key}, {RID}) ORDER BY {RID}")
        new = self.create_sheet_from_query(name or f"{sheet} unique", query)
        return new, self.row_total(sheet) - self.row_total(new)

    # --------------------------------------------------------------------- SQL

    @staticmethod
    def is_query(sql):
        first = re.sub(r"^\s*(--[^\n]*\n|\s)*", "", sql).split(None, 1)
        return bool(first) and first[0].upper() in (
            "SELECT", "WITH", "FROM", "PIVOT", "UNPIVOT", "SUMMARIZE", "DESCRIBE", "VALUES", "TABLE")

    def sql_preview(self, sql, limit=500):
        sql = sql.strip().rstrip(";")
        cur = self._exec(f"SELECT * FROM ({sql}) LIMIT {int(limit) + 1}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        keep = [i for i, c in enumerate(cols) if c != RID]
        cols = [cols[i] for i in keep]
        rows = [tuple(r[i] for i in keep) for r in rows]
        return cols, rows[:limit], len(rows) > limit

    def sql_to_sheet(self, sql, name):
        return self.create_sheet_from_query(name, sql.strip().rstrip(";"))

    def sql_execute(self, sql):
        self._exec(sql)
        self._repair_tables()
        for sheet in list(self.views):
            if not self._table_exists(sheet):
                self.views.pop(sheet, None)
            else:
                try:
                    self._renumber(sheet)
                    self.rebuild_view(sheet)
                except EngineError:
                    self.reset_view(sheet)
        self._clear_undo()

    # ------------------------------------------------------------------ export

    def export(self, sheet, path, fmt, view_only=True):
        """Export the sheet (or just the current view) to csv, parquet, json or xlsx."""
        cols = [c for c, _ in self.columns(sheet)]
        names = ", ".join(quote_ident(c) for c in cols)
        if view_only:
            query = f"SELECT {names} FROM {self._rows_source(sheet)} ORDER BY __pos"
        else:
            query = f"SELECT {names} FROM {self.t(sheet)} ORDER BY {RID}"
        if fmt == "csv":
            self._exec(f"COPY ({query}) TO {sql_string(path)} (HEADER, DELIMITER ',')")
        elif fmt == "parquet":
            self._exec(f"COPY ({query}) TO {sql_string(path)} (FORMAT parquet, COMPRESSION zstd)")
        elif fmt == "json":
            self._exec(f"COPY ({query}) TO {sql_string(path)} (FORMAT json)")
        elif fmt == "xlsx":
            return self._export_xlsx(query, cols, path)
        else:
            raise EngineError(f"Unknown format {fmt}")
        return None

    def _export_xlsx(self, query, cols, path):
        import xlsxwriter
        book = xlsxwriter.Workbook(path, {"constant_memory": True, "strings_to_numbers": False,
                                          "strings_to_formulas": False, "strings_to_urls": False})
        sheet = book.add_worksheet("Data")
        bold = book.add_format({"bold": True})
        date_fmt = book.add_format({"num_format": "yyyy-mm-dd"})
        dt_fmt = book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
        sheet.write_row(0, 0, cols, bold)
        cur = self._exec(query)
        r = 1
        truncated = False
        while True:
            batch = cur.fetchmany(20000)
            if not batch:
                break
            for row in batch:
                if r > EXCEL_MAX_ROWS:
                    truncated = True
                    break
                for ci, v in enumerate(row):
                    if v is None:
                        continue
                    if isinstance(v, dt.datetime):
                        sheet.write_datetime(r, ci, v.replace(tzinfo=None), dt_fmt)
                    elif isinstance(v, dt.date):
                        sheet.write_datetime(r, ci, dt.datetime(v.year, v.month, v.day), date_fmt)
                    elif isinstance(v, bool):
                        sheet.write_boolean(r, ci, v)
                    elif isinstance(v, (int, float, decimal.Decimal)):
                        sheet.write_number(r, ci, float(v))
                    else:
                        sheet.write_string(r, ci, str(v)[:32767])
                r += 1
            if truncated:
                break
        book.close()
        return {"rows": r - 1, "truncated": truncated}

    # -------------------------------------------------------------------- save

    def checkpoint(self):
        """Flush everything to the workbook file. Call from the main thread when idle."""
        self._close_all_cursors()
        try:
            self.con.execute("CHECKPOINT wb")
        except duckdb.Error:
            try:
                self.con.execute("FORCE CHECKPOINT wb")
            except duckdb.Error as e:
                raise EngineError(_clean_db_error(e))

    def save_as(self, path, password=None):
        """Write a copy of the workbook to path (optionally encrypted) and switch to it."""
        if password and not self.crypto_available:
            raise EngineError("Encryption is not available. Reinstall with requirements.txt.")
        path = os.path.abspath(path)
        tmp = path + ".bigsheet-tmp"
        for p in (tmp, tmp + ".wal"):
            if os.path.exists(p):
                os.remove(p)
        opts = f" (ENCRYPTION_KEY {sql_string(password)})" if password else ""
        self.checkpoint()
        try:
            self.con.execute(f"ATTACH {sql_string(tmp)} AS dst{opts}")
            self.con.execute("COPY FROM DATABASE wb TO dst")
            self.con.execute("CHECKPOINT dst")
            self.con.execute("DETACH dst")
        except duckdb.Error as e:
            try:
                self.con.execute("DETACH DATABASE IF EXISTS dst")
            except duckdb.Error:
                pass
            raise EngineError(_clean_db_error(e))
        old_temp = None if not self.untitled else os.path.join(self.session_dir, "untitled.bigsheet")
        # Switch the open workbook over to the new file.
        self._close_all_cursors()
        self.con.execute("USE scratch")
        self.con.execute("DETACH wb")
        os.replace(tmp, path)
        if os.path.exists(tmp + ".wal"):
            os.replace(tmp + ".wal", path + ".wal")
        self._attach(path, password)
        self.con.execute("USE wb")
        self.path, self.encrypted, self.untitled = path, bool(password), False
        if old_temp:
            for p in (old_temp, old_temp + ".wal"):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _header_row(rows):
    """Index of the real header row, skipping title and blank rows above a table."""
    filled = [sum(1 for v in r if str(v).strip()) for r in rows]
    if not filled:
        return 0
    widest = max(filled)
    if widest < 2:
        return 0
    for i, n in enumerate(filled):
        if n >= max(2, int(widest * 0.6)):
            return i
    return 0


def _excel_cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    return str(v)
