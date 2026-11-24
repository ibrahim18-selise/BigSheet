"""Engine tests. Run with:  python -m pytest tests -q"""

import csv
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bigsheet.engine import EngineError, PasswordRequired, Workbook, WrongPassword  # noqa: E402


@pytest.fixture()
def tmpdir_path():
    d = tempfile.mkdtemp()
    yield d


@pytest.fixture()
def sample_csv(tmpdir_path):
    path = os.path.join(tmpdir_path, "sales.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "city", "price", "qty", "order date"])
        for i in range(1, 1001):
            w.writerow([i, ["Dhaka", "Chattogram", "Sylhet"][i % 3], round(i * 1.5, 2), i % 7,
                        f"2024-01-{(i % 28) + 1:02d}"])
    return path


@pytest.fixture()
def wb(sample_csv):
    book = Workbook()
    book.import_file(sample_csv)
    yield book
    book.close()


def test_import_and_fetch(wb):
    assert wb.sheets() == ["sales"]
    cols = dict(wb.columns("sales"))
    assert cols["id"] == "BIGINT" and cols["order date"] == "DATE"
    rids, rows = wb.fetch("sales", 0, 5)
    assert rids == [0, 1, 2, 3, 4]
    assert rows[0][0] == 1 and rows[0][1] == "Chattogram"
    assert wb.view("sales").count == 1000


def test_sort_and_filter(wb):
    table, count = wb.prepare_view("sales", [("price", False)], [{"col": "city", "op": "eq", "value": "dhaka"}])
    wb.apply_view("sales", [("price", False)], [{"col": "city", "op": "eq", "value": "dhaka"}], table, count)
    assert count == 333
    _, rows = wb.fetch("sales", 0, 3)
    assert [r[0] for r in rows] == [999, 996, 993]
    with pytest.raises(EngineError):
        wb.validate_filter_value("sales", "price", "gt", "abc")
    wb.reset_view("sales")
    assert wb.view("sales").count == 1000


def test_edit_undo_redo(wb):
    wb.set_cells("sales", "qty", [0], ["42"])
    assert wb.fetch("sales", 0, 1)[1][0][3] == 42
    with pytest.raises(EngineError):
        wb.set_cells("sales", "qty", [0], ["not a number"])
    wb.undo()
    assert wb.fetch("sales", 0, 1)[1][0][3] == 1
    wb.redo()
    assert wb.fetch("sales", 0, 1)[1][0][3] == 42
    wb.set_cells("sales", "city", [1, 2], ["", "Khulna"])
    _, rows = wb.fetch("sales", 1, 2)
    assert rows[0][1] is None and rows[1][1] == "Khulna"


def test_insert_and_delete_rows(wb):
    wb.insert_rows("sales", before_rid=0, count=2)
    rids, rows = wb.fetch("sales", 0, 3)
    assert rids == [0, 1, 2] and rows[0][0] is None and rows[2][0] == 1
    wb.delete_positions("sales", [(0, 1)])
    rids, rows = wb.fetch("sales", 0, 2)
    assert rids == [0, 1] and rows[0][0] == 1
    # delete inside a filtered view, then check row ids stay contiguous
    t, c = wb.prepare_view("sales", [], [{"col": "city", "op": "eq", "value": "Sylhet"}])
    wb.apply_view("sales", [], [{"col": "city", "op": "eq", "value": "Sylhet"}], t, c)
    wb.delete_positions("sales", [(0, c - 1)])
    wb.reset_view("sales")
    assert wb.row_total("sales") == 1000 - c
    lo, hi, n = wb.cursor().execute('SELECT min(__rid), max(__rid), count(*) FROM wb.main."sales"').fetchone()
    assert lo == 0 and hi == n - 1


def test_formula_columns(wb):
    wb.add_formula_column("sales", "total", "=[price] * [qty]")
    wb.add_formula_column("sales", "share", "=ROUND([price] / SUM([price]) * 100, 4)")
    wb.add_formula_column("sales", "label", '=LEFT([city], 3) & "-" & YEAR([order date])')
    wb.add_formula_column("sales", "big", '=IF([qty] > 3, "yes", "no")')
    cols = [c for c, _ in wb.columns("sales")]
    assert cols[-4:] == ["total", "share", "label", "big"]
    _, rows = wb.fetch("sales", 0, 1)
    r = rows[0]
    assert r[5] == pytest.approx(1.5 * 1) and r[7] == "Cha-2024" and r[8] == "no"
    total_share = wb.cursor().execute('SELECT sum(share) FROM wb.main."sales"').fetchone()[0]
    assert total_share == pytest.approx(100, abs=0.01)
    with pytest.raises(EngineError):
        wb.add_formula_column("sales", "bad", "=[nope] + 1")
    assert wb.recalc_formulas("sales") == 4


def test_summarize_pivot_dedupe(wb):
    s = wb.summarize("sales", ["city"], [("count", None), ("sum", "price")])
    _, rows = wb.fetch(s, 0, 10)
    assert len(rows) == 3 and rows[0][0] == "Chattogram"
    p = wb.summarize("sales", ["qty"], [("sum", "price")], pivot_col="city")
    assert len(wb.columns(p)) == 4
    d, removed = wb.remove_duplicates("sales", ["city"])
    assert wb.row_total(d) == 3 and removed == 997


def test_find_and_stats(wb):
    hit = wb.find_next("sales", "sylhet", None, -1)
    assert hit[0] == 1 and hit[1] == 1
    hit2 = wb.find_next("sales", "sylhet", hit[0], hit[1])
    assert hit2[0] == 4
    st = wb.selection_stats("sales", ["price", "qty"], 0, 9)
    assert st["count"] == 20 and st["max"] == 15.0
    cs = wb.column_stats("sales", "city")
    assert cs["rows"] == 1000 and cs["top"][0][1] == 334


def test_export(wb, tmpdir_path):
    for fmt in ("csv", "parquet", "json", "xlsx"):
        out = os.path.join(tmpdir_path, f"out.{fmt}")
        wb.export("sales", out, fmt)
        assert os.path.getsize(out) > 0
    new = wb.import_file(os.path.join(tmpdir_path, "out.xlsx"))[0]
    assert wb.row_total(new) == 1000
    new2 = wb.import_file(os.path.join(tmpdir_path, "out.parquet"))[0]
    assert wb.row_total(new2) == 1000


def test_sql_window(wb):
    cols, rows, more = wb.sql_preview("SELECT city, count(*) n FROM sales GROUP BY city ORDER BY n DESC")
    assert cols == ["city", "n"] and rows[0][1] == 334 and not more
    s = wb.sql_to_sheet("SELECT * FROM sales WHERE qty = 0", "zero qty")
    assert wb.row_total(s) == 142
    wb.sql_execute("CREATE TABLE made_in_sql AS SELECT 1 AS a")
    assert "made_in_sql" in wb.sheets()
    assert wb.fetch("made_in_sql", 0, 1)[1] == [(1,)]
    with pytest.raises(EngineError):
        wb.sql_preview("SELECT * FROM read_csv('https://example.com/data.csv')")
    with pytest.raises(EngineError):
        wb.sql_execute("SET autoinstall_known_extensions = true")


def test_save_encrypted_and_reopen(wb, tmpdir_path):
    if not wb.crypto_available:
        pytest.skip("encryption component not installed")
    path = os.path.join(tmpdir_path, "secret.bigsheet")
    wb.set_cells("sales", "city", [0], ["Rajshahi"])
    wb.save_as(path, password="correct horse")
    assert wb.path == path and wb.encrypted
    wb.set_cells("sales", "city", [1], ["Barishal"])  # still works after the switch
    wb.close()
    raw = open(path, "rb").read()
    assert b"Rajshahi" not in raw
    with pytest.raises(PasswordRequired):
        Workbook(path)
    with pytest.raises(WrongPassword):
        Workbook(path, "wrong")
    again = Workbook(path, "correct horse")
    _, rows = again.fetch("sales", 0, 2)
    assert rows[0][1] == "Rajshahi" and rows[1][1] == "Barishal"
    again.close()


def test_save_plain(wb, tmpdir_path):
    path = os.path.join(tmpdir_path, "plain.bigsheet")
    wb.save_as(path)
    wb.close()
    again = Workbook(path)
    assert again.sheets() == ["sales"] and again.row_total("sales") == 1000
    again.close()


def test_value_list_filters_and_distinct(wb):
    vals, truncated = wb.distinct_values("sales", "city")
    assert [v for v, _ in vals] == ["Chattogram", "Dhaka", "Sylhet"] and not truncated
    assert dict(vals)["Dhaka"] == 333
    f_in = {"col": "city", "op": "in", "values": ["Dhaka", "Sylhet"]}
    t, c = wb.prepare_view("sales", [], [f_in])
    assert c == 666
    wb.set_cells("sales", "city", [0], [""])  # make one blank
    f_not = {"col": "city", "op": "not_in", "values": ["Dhaka"], "blanks": False}
    t, c = wb.prepare_view("sales", [], [f_not])
    assert c == 1000 - 333 - 1  # Dhaka rows and the blank row excluded
    f_blank = {"col": "city", "op": "in", "values": [], "blanks": True}
    t, c = wb.prepare_view("sales", [], [f_blank])
    assert c == 1
    nums, _ = wb.distinct_values("sales", "qty", filters=[f_in])
    assert [v for v, _ in nums][:3] == ["0", "1", "2"]  # numeric order, not text order
    some, _ = wb.distinct_values("sales", "city", search="dha")
    assert [v for v, _ in some] == ["Dhaka"]


def test_column_formats(wb, tmpdir_path):
    wb.set_column_format("sales", ["price", "qty"], {"bold": True, "numfmt": "currency", "symbol": "$"})
    wb.set_column_format("sales", ["price"], {"bold": None, "decimals": 1})
    f = wb.column_formats("sales")
    assert f["price"] == {"numfmt": "currency", "symbol": "$", "decimals": 1}
    assert f["qty"]["bold"] is True
    wb.rename_column("sales", "price", "unit price")
    assert "unit price" in wb.column_formats("sales")
    copy = wb.duplicate_sheet("sales")
    assert wb.column_formats(copy)["qty"]["bold"] is True
    wb.clear_column_format("sales", ["qty"])
    assert "qty" not in wb.column_formats("sales")


def test_pivot_sheets(wb):
    p = wb.create_pivot("sales")
    assert wb.columns(p)[0][0] == "Pivot table"
    wb.update_pivot(p, {"rows": ["city"], "columns": [], "values": [["sum", "price"], ["count", None]]})
    _, rows = wb.fetch(p, 0, 10)
    assert len(rows) == 3 and rows[0][0] == "Chattogram"
    assert [c for c, _ in wb.columns(p)] == ["city", "Sum of price", "Count of rows"]
    wb.update_pivot(p, {"rows": ["qty"], "columns": ["city"], "values": [["sum", "price"]],
                        "filters": [{"col": "city", "op": "in", "values": ["Dhaka", "Sylhet"]}]})
    assert len(wb.columns(p)) == 3 and wb.row_total(p) == 7
    assert wb.pivot_spec(p)["source"] == "sales"
    wb.rename_sheet("sales", "sales 2024")
    assert wb.pivot_spec(p)["source"] == "sales 2024"
    wb.update_pivot(p, wb.pivot_spec(p))  # still works after the rename


def test_sample_sheet():
    book = Workbook()
    name = book.create_sample_sheet(rows=5000)
    assert book.row_total(name) == 5000 and len(book.columns(name)) == 7
    book.close()


def test_uneven_csv_and_excel_title_rows(tmpdir_path):
    import xlsxwriter
    ragged = os.path.join(tmpdir_path, "ragged.csv")
    with open(ragged, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([f"c{i}" for i in range(6)])
        for r in range(3000):
            n = 6 + (2 if r == 2500 else 0) - (2 if r % 100 == 7 else 0)
            w.writerow([r] * n)
    report = os.path.join(tmpdir_path, "report.xlsx")
    book = xlsxwriter.Workbook(report)
    ws = book.add_worksheet("Report")
    ws.write(0, 0, "Sales report")
    ws.write_row(2, 0, ["Region", "Amount", "Units"])
    for r in range(3, 103):
        ws.write_row(r, 0, [f"R{r % 4}", r * 2.5, r])
    book.close()
    wb = Workbook()
    a = wb.import_file(ragged)[0]
    assert len(wb.columns(a)) == 6 and wb.row_total(a) == 3000
    b = wb.import_file(report, {"header": True})[0]
    assert dict(wb.columns(b)) == {"Region": "VARCHAR", "Amount": "DOUBLE", "Units": "BIGINT"}
    assert wb.row_total(b) == 100
    wb.close()


def test_local_number_and_date_formats(wb):
    from bigsheet.engine import normalize_value
    assert normalize_value("DOUBLE", "1.234,50") == "1234.5"
    assert normalize_value("DOUBLE", "\u20ac 1,234.50") == "1234.5"
    assert normalize_value("BIGINT", "1,234") == "1234"
    assert normalize_value("DOUBLE", "12%") == "0.12"
    assert normalize_value("DATE", "31.01.2024") == "2024-01-31"
    assert normalize_value("DATE", "01/31/2024") == "2024-01-31"
    wb.validate_filter_value("sales", "price", "gt", "1.000,00")
    t, c = wb.prepare_view("sales", [], [{"col": "order date", "op": "ge", "value": "28.01.2024"}])
    assert c > 0
    wb.set_cells("sales", "price", [0], ["1.234,50"])
    assert wb.fetch("sales", 0, 1)[1][0][2] == 1234.5


def test_unreadable_rows_are_skipped_and_reported(tmpdir_path):
    path = os.path.join(tmpdir_path, "broken.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "price", "city"])
        for i in range(150000):
            w.writerow([i, i * 1.5, "Dhaka"])
        w.writerow(["x", "not a number", "y", "extra"])
        w.writerow([150001, 2.5, "Sylhet"])
    wb = Workbook()
    name = wb.import_file(path)[0]
    assert dict(wb.columns(name)) == {"id": "BIGINT", "price": "DOUBLE", "city": "VARCHAR"}
    assert wb.row_total(name) == 150001
    note = wb.import_notes[name]
    assert note.startswith("1 row could not be read") and "150,002" in note
    wb.close()


def test_row_and_cell_fills_follow_rows(wb):
    wb.set_fills("sales", [2, 5], None, "#fef08a")            # two whole rows
    wb.set_fills("sales", [3], ["city", "qty"], "#bbf7d0")     # two cells
    f = wb.fills_for("sales", [2, 3, 5])
    assert f[(2, "")] == "#fef08a" and f[(3, "city")] == "#bbf7d0" and f[(5, "")] == "#fef08a"
    wb.insert_rows("sales", before_rid=0, count=1)            # everything moves down one row
    f = wb.fills_for("sales", [3, 4, 6])
    assert f[(3, "")] == "#fef08a" and f[(4, "qty")] == "#bbf7d0" and f[(6, "")] == "#fef08a"
    wb.delete_positions("sales", [(0, 3)])                     # deletes the first coloured row too
    f = wb.fills_for("sales", list(range(0, 10)))
    assert f == {(0, "city"): "#bbf7d0", (0, "qty"): "#bbf7d0", (2, ""): "#fef08a"}
    wb.clear_fills("sales", [0], ["qty"])
    assert wb.fills_for("sales", [0]) == {(0, "city"): "#bbf7d0"}  # only the cleared cell lost its colour
    wb.clear_fills("sales", None, ["city"])
    wb.set_fills("sales", [2], None, None)
    assert not wb.has_fills("sales")
