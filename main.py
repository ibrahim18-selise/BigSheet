"""Start BigSheet.   python main.py [file-to-open]"""

import os
import sys

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from bigsheet import config
from bigsheet.mainwindow import MainWindow
from bigsheet.theme import apply_theme


class App(QApplication):
    """Handles files opened from Finder on macOS (double-click or 'Open with')."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending = []

    def event(self, e):
        if e.type() == QEvent.FileOpen:
            path = e.file()
            if self.window:
                self.window.open_path(path)
            else:
                self.pending.append(path)
            return True
        return super().event(e)


def self_test():
    """Check the engine and encryption without opening a window:  BigSheet --self-test"""
    import tempfile
    from bigsheet.engine import Workbook
    wb = Workbook()
    print("DuckDB engine: ok")
    print("Encryption available:", wb.crypto_available)
    if wb.crypto_available:
        path = os.path.join(tempfile.mkdtemp(), "selftest.bigsheet")
        wb.sql_execute("CREATE TABLE t AS SELECT 42 AS answer")
        wb.save_as(path, "self-test-password")
        wb.close()
        again = Workbook(path, "self-test-password")
        print("Encrypted save and reopen:", "ok" if again.fetch("t", 0, 1)[1] == [(42,)] else "FAILED")
        again.close()
    else:
        wb.close()
    try:
        w2 = Workbook()
        w2.sql_preview("SELECT * FROM read_csv('https://example.com/x.csv')")
        print("Network blocked: NO")
    except Exception:
        print("Network blocked: yes")
    return 0


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    app = App(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setOrganizationName(config.APP_NAME)
    apply_theme(app)
    win = MainWindow()
    app.window = win
    win.show()
    for path in app.pending + [a for a in sys.argv[1:] if os.path.exists(a)]:
        win.open_path(path)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
