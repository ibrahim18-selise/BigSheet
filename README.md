# BigSheet

A private, Excel-like desktop spreadsheet for very large CSV and Excel files. It runs on
macOS and Windows, never connects to the internet, and can encrypt your workbooks.

Files are converted once into a fast columnar workbook (a DuckDB database file with the
extension `.bigsheet`). After that, scrolling, sorting, filtering and calculating work on
tens of millions of rows. Measured on 10 million rows: import about 7 seconds, jumping to any
row in milliseconds, sort plus filter about half a second, a new formula column about 1 second.

## Download

Ready-made apps are on the **Releases** page of this repository (right-hand side on GitHub):

* **Mac (Apple Silicon: M1, M2, M3, M4):** download `BigSheet-mac-apple-silicon.zip`, double-click it
  to unzip, and drag **BigSheet.app** into Applications. The first time, right-click the app
  and choose **Open**, then **Open** again (the app is not notarized by Apple). If macOS says
  the app is damaged, open Terminal and run
  `xattr -dr com.apple.quarantine /Applications/BigSheet.app`.
* **Windows 10 and 11:** download `BigSheet-windows.zip`, right-click it and choose
  **Extract All**, then open the `BigSheet` folder and double-click **BigSheet.exe**. If
  Windows SmartScreen appears, click **More info**, then **Run anyway**. Keep the whole folder
  together; the exe needs the files next to it.
* **Intel Macs, or if you prefer:** run from source as described in section 1.

No Python is needed for the ready-made apps.

## 1. Run from source

You need Python 3.10 or newer.

**Mac**
1. Install Python from https://www.python.org/downloads/macos/ if you do not have it.
2. Unzip BigSheet, open the folder, and double-click `run_mac.command`.
   If macOS blocks it, right-click it and choose Open. If it is not executable, run
   `chmod +x run_mac.command` in Terminal once.

**Windows**
1. Install Python from https://www.python.org/downloads/windows/ and tick
   "Add python.exe to PATH" during installation.
2. Unzip BigSheet and double-click `run_windows.bat`.

The first start takes a minute while it sets up a private environment in the `.venv`
folder. Later starts are quick.

## 2. Build a standalone app (no Python needed on the computer that runs it)

Run the build once on each platform (it needs Python on the build computer only):

* Mac: `./build_mac.sh` creates `dist/BigSheet.app`. Drag it into Applications. On first launch,
  right-click it and choose Open, because the app is not notarized by Apple. Build on the
  same kind of Mac you will use (Apple Silicon or Intel).
* Windows: double-click `build_windows.bat`. It creates `dist\BigSheet\BigSheet.exe`. Keep the
  whole `dist\BigSheet` folder together.

Check any installation without opening a window:

    BigSheet --self-test          (standalone app)
    python main.py --self-test    (from source)

It reports whether encryption works and confirms that network access is blocked.

## 3. Using BigSheet

**Opening data.** Drag files onto the window, or use File, Import file. CSV, TSV, TXT,
Excel (xlsx, xlsm, xls, xlsb, ods), Parquet and JSON are supported. Each file becomes a
sheet (tab). In the import window you can pick the separator, the text encoding, whether the
first row has column names, and which Excel worksheets to import. If a CSV fails to import,
tick "Treat all columns as text" or "Skip rows that cannot be read".

**Saving.** A new workbook is temporary until you use File, Save as. After that, every change
is written to the workbook file as you work, like a database, so there is nothing to lose if
the computer crashes. When you save you can protect the workbook with a password.

**Editing.** Double-click a cell or just start typing. Values are checked against the column
type (number, date, text and so on). You can also edit in the bar above the grid. Copy and paste
work with Excel and Google Sheets. Pasting one value into a selection fills it. Delete clears
the selected cells. Undo and redo cover cell edits.

**Rows and columns.** Right-click a cell for insert and delete row options. Right-click a
column header to sort, filter, rename, change type, delete, or see statistics.

**Sort and filter.** Sort A to Z or Z to A on the current column, or use Data, Custom sort for
up to three levels. Filters can be stacked: equals, contains, greater than, is empty and more.
"Filter by selected value" is the fastest way. A bar above the grid shows what is active and
has a button to clear it. The status bar shows how many rows match.

**Formula columns.** Columns, Add formula column. Formulas use Excel syntax with column names
in square brackets and are calculated for every row:

    =[price] * [qty]
    =IF([qty] > 10, [price] * 0.9, [price])
    =[amount] / SUM([amount])            (SUM of one column = total of the whole column)
    =LEFT([customer], 3) & "-" & YEAR([order_date])

About 50 Excel functions are supported (Help, Formula help lists them), plus any DuckDB
function. Use "Preview first rows" to check a formula before adding it. The result is stored
as a normal column. After you edit data the formula depends on, use Columns, Recalculate
formulas.

**Summaries and pivots.** Data, Summarize / pivot. Group by one or more columns and calculate
counts, sums, averages, minimums, maximums or medians. Optionally spread one column's values
across columns (a pivot table). The result is a new sheet.

**Other tools.** Remove duplicates (to a new sheet), Find (Ctrl+F or Cmd+F), Go to row
(Ctrl+G), column statistics, and the selection summary in the status bar
(count, sum, average, min, max). Tools, SQL window lets advanced users query sheets
with SQL and save results as new sheets.

**Export.** File, Export sheet writes CSV, Excel, Parquet or JSON, either the whole sheet or
only the current sorted and filtered view.

## 4. Security

* **No network access.** The engine cannot download anything, HTTP and S3 access is disabled,
  and the configuration is locked so it cannot be re-enabled, not even from the SQL window.
* **Encrypted workbooks.** Workbooks saved with a password are encrypted with AES-256, including
  temporary spill files. Unsaved work is also kept encrypted, with a random key. There is no
  way to recover a workbook if its password is lost.
* **Things that are not encrypted:** files you export, text you copy to the clipboard, and a
  short-lived temporary copy of the data made while importing an Excel file (deleted right
  after import). Turn on FileVault (Mac) or BitLocker (Windows) to protect those too.
* **Policy file** (optional, for IT departments). Copy `policy.example.json` to `policy.json`
  in one of these folders and change the values:
  * Mac: `/Library/Application Support/BigSheet/` (all users) or
    `~/Library/Application Support/BigSheet/` (one user)
  * Windows: `C:\ProgramData\BigSheet\` (all users) or `%APPDATA%\BigSheet\` (one user)

  Settings: `allow_export`, `allow_copy`, `allow_sql`, `require_encryption`. The all-users file
  overrides the per-user file. Help, About shows the policy in effect.

## 5. Limits

* Formulas fill whole columns. They are not per-cell formulas, and they do not recalculate
  automatically.
* Undo covers cell edits only. Deleting rows or columns asks for confirmation first.
* Excel export is capped at 1,048,575 rows (Excel's own limit). CSV and Parquet have no limit.
* The grid shows up to about 2.1 billion rows per sheet.
* Sorting or filtering with tens of millions of rows takes a second or two. A progress bar
  with a Cancel button appears for anything slow.

## 6. Troubleshooting

* **"Encryption is not available"**: `duckdb` and `duckdb-extension-httpfs` must be the same
  version. Delete the `.venv` folder and start again so `requirements.txt` is reinstalled.
* **A CSV will not import**: try another separator or encoding in the import window, or tick
  "Treat all columns as text".
* **Mac says the app is damaged or from an unidentified developer**: right-click it and
  choose Open, or run `xattr -dr com.apple.quarantine /Applications/BigSheet.app`.
* **Try it with test data**: `python tools/make_sample_data.py 10000000` writes a
  10-million-row `sample_sales.csv`.

## 7. Publishing a new version

GitHub builds the apps automatically (see `.github/workflows/build.yml`). Every push to
`main` builds both apps; download them from the run's Artifacts on the Actions tab. To publish
a release with downloadable apps, create a version tag:

    git tag v1.0.1
    git push origin v1.0.1

## 8. For developers

    bigsheet/engine.py      DuckDB workbook engine: import, views, edits, formulas, export, encryption
    bigsheet/formula.py     Excel-style formula to SQL translator
    bigsheet/model.py       Virtual Qt table model and background task runner
    bigsheet/dialogs.py     Dialog windows
    bigsheet/mainwindow.py  Main window
    bigsheet/config.py      App paths and policy file
    main.py                 Entry point (also --self-test)
    tests/test_engine.py    Engine tests:  python -m pytest tests -q

How it stays fast: every sheet has a hidden `__rid` column holding row positions 0 to n-1,
so the grid fetches any screen of rows with a range lookup that DuckDB answers from its
min/max indexes. A sort or filter is stored as a small (position, row id) table, so the sheet
itself is never rewritten. All slow work runs on background threads, each with its own
database cursor.
