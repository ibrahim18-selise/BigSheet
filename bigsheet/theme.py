"""Look and feel: a light theme, one stylesheet and a small set of vector icons.

The same Fusion style is used on macOS and Windows so the app looks identical
on both. Icons are simple SVG drawings rendered at runtime.
"""

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#2563eb"
ACCENT_SOFT = "#dbe7fd"
ACCENT_LINE = "#b9cdf8"
TEXT = "#1f2937"
TEXT_MUTED = "#6b7280"
CHROME = "#f8fafc"
BORDER = "#e3e7ed"
GRID = "#e8ebef"
HOVER = "#eef2f7"

STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: #ffffff; }}

QToolBar {{ background: {CHROME}; border: none; border-bottom: 1px solid {BORDER};
           padding: 4px 8px; spacing: 2px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 6px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px; background: transparent; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:pressed, QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_LINE}; }}
QToolButton:disabled {{ color: #b6bcc6; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton[popupMode="1"] {{ padding-right: 12px; }}
QToolButton::menu-button {{ border: none; width: 12px; }}

QComboBox {{ border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 8px; background: #ffffff;
            min-height: 20px; }}
QComboBox:hover {{ border-color: #c9d0da; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT};
                               selection-color: {TEXT}; background: #ffffff; outline: 0; }}

QLineEdit, QPlainTextEdit, QSpinBox, QTextBrowser {{ border: 1px solid {BORDER}; border-radius: 6px;
    padding: 4px 6px; background: #ffffff; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}

QPushButton {{ border: 1px solid #d5dae2; border-radius: 6px; padding: 5px 14px; background: #ffffff; }}
QPushButton:hover {{ background: #f3f5f8; }}
QPushButton:pressed {{ background: {HOVER}; }}
QPushButton:default, QPushButton[primary="true"] {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; }}
QPushButton:default:hover, QPushButton[primary="true"]:hover {{ background: #1d4ed8; }}
QPushButton[flat="true"] {{ border: none; background: transparent; color: {ACCENT}; padding: 2px 4px; }}
QPushButton[flat="true"]:hover {{ text-decoration: underline; }}
QPushButton:disabled {{ color: #b6bcc6; background: #f8fafc; }}

QTableView {{ border: none; gridline-color: {GRID}; background: #ffffff;
             selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QTableView QLineEdit {{ border: 2px solid {ACCENT}; border-radius: 0; padding: 0 3px; }}
QHeaderView {{ background: {CHROME}; }}
QHeaderView::section {{ background: {CHROME}; color: {TEXT_MUTED}; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; padding: 0 6px; }}
QHeaderView::section:vertical {{ padding: 0 6px 0 4px; }}
QTableCornerButton::section {{ background: {CHROME}; border: none; border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER}; }}

QTabBar {{ background: {CHROME}; }}
QTabBar::tab {{ background: transparent; border: none; padding: 6px 14px; margin: 3px 2px;
               color: {TEXT_MUTED}; border-radius: 6px; }}
QTabBar::tab:hover {{ background: {HOVER}; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {ACCENT_SOFT}; color: #1d4ed8; font-weight: 600; }}

QStatusBar {{ background: {CHROME}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
QStatusBar QLabel {{ color: {TEXT_MUTED}; }}
QStatusBar::item {{ border: none; }}

QMenu {{ background: #ffffff; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::item:disabled {{ color: #b6bcc6; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {CHROME}; padding: 8px 10px; border-bottom: 1px solid {BORDER};
                     font-weight: 600; text-align: left; }}

QListWidget, QTreeWidget {{ border: 1px solid {BORDER}; border-radius: 6px; background: #ffffff;
    outline: 0; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QListWidget::item, QTreeWidget::item {{ padding: 3px 4px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {HOVER}; }}

QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px; background: #ffffff; height: 8px;
               max-height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #cfd5dd; border-radius: 5px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #b4bcc7; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #cfd5dd; border-radius: 5px; min-width: 30px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #b4bcc7; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {TEXT}; color: #ffffff; border: none; padding: 4px 8px; border-radius: 4px; }}

QLabel#Title {{ font-size: 26px; font-weight: 700; }}
QLabel#Subtitle {{ color: {TEXT_MUTED}; font-size: 14px; }}
QLabel#SectionLabel {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 600; }}
QLabel#Fx {{ color: {TEXT_MUTED}; font-style: italic; font-weight: 600; padding: 0 4px; }}
QLabel#NameBox {{ border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 8px; background: #ffffff; }}
QWidget#FormulaBar {{ background: #ffffff; border-bottom: 1px solid {BORDER}; }}
QWidget#ViewBar {{ background: #f0f5ff; border-bottom: 1px solid {ACCENT_LINE}; }}
QWidget#TabsRow {{ background: {CHROME}; border-top: 1px solid {BORDER}; }}
QFrame#Popup {{ background: #ffffff; border: 1px solid #d5dae2; border-radius: 10px; }}
QFrame#Card {{ border: 1px solid {BORDER}; border-radius: 10px; background: #ffffff; }}
"""


def apply_theme(app):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#ffffff"))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor(CHROME))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor("#ffffff"))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT_SOFT))
    pal.setColor(QPalette.HighlightedText, QColor(TEXT))
    pal.setColor(QPalette.ToolTipBase, QColor(TEXT))
    pal.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor("#9aa1ac"))
    pal.setColor(QPalette.Link, QColor(ACCENT))
    pal.setColor(QPalette.Mid, QColor(BORDER))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#b6bcc6"))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)


# --------------------------------------------------------------------- icons
# 24 x 24 drawings. "C" is replaced with the stroke colour.

_ICONS = {
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    "bold": '<path d="M7 5h6a3.5 3.5 0 0 1 0 7H7z" stroke-width="2.4"/>'
            '<path d="M7 12h7a3.5 3.5 0 0 1 0 7H7z" stroke-width="2.4"/>',
    "italic": '<path d="M10 5h8M6 19h8M14 5l-4 14"/>',
    "text_color": '<path d="M7 16 12 4l5 12M8.6 12h6.8"/>',
    "fill": '<path d="m5 11 6-6 7 7-6 6a2 2 0 0 1-2.8 0L5 13.8a2 2 0 0 1 0-2.8z"/>'
            '<path d="M5 12h13"/><path d="M19.5 15s1.5 1.8 1.5 2.8a1.5 1.5 0 0 1-3 0c0-1 1.5-2.8 1.5-2.8z"/>',
    "align_left": '<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>',
    "align_center": '<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>',
    "align_right": '<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>',
    "filter": '<path d="M4 5h16l-6 7.5V19l-4-2v-4.5z"/>',
    "sort_az": '<path d="M7 4v15M4 16l3 3 3-3"/><path d="M14 10l2.5-6 2.5 6M14.8 8h3.4M14 14h5l-5 6h5"/>',
    "sort_za": '<path d="M7 4v15M4 16l3 3 3-3"/><path d="M14 4h5l-5 6h5M14 20l2.5-6 2.5 6M14.8 18h3.4"/>',
    "fx": '<path d="M12 4.5c-2 0-3 1-3.4 3L6.5 19.5c-.3 1.5-1.3 2-2.5 2"/><path d="M6 11h7"/>'
          '<path d="m14 12 6 7M20 12l-6 7"/>',
    "pivot": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h16M9 4v16"/>'
             '<path d="M13 13h4v4" stroke-width="1.6"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/>',
    "sql": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/>'
           '<path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    "import": '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "export": '<path d="M12 15V4M7.5 8.5 12 4l4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "save": '<path d="M5 4h11l3 3v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "open": '<path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
    "dec_minus": '<path d="M5 17h1M9 17a2 2 0 0 0 4 0v-4a2 2 0 0 0-4 0z"/><path d="M15 8h6"/>',
    "dec_plus": '<path d="M3 17h1M7 17a2 2 0 0 0 4 0v-4a2 2 0 0 0-4 0z"/><path d="M15 8h6M18 5v6"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2.2"/><circle cx="17" cy="17" r="2.2"/>',
    "currency": '<path d="M12 3v18"/><path d="M16.5 7.5C16 6 14.3 5 12 5c-2.6 0-4.5 1.3-4.5 3.3 0 4.4 9 2.4 9 7.2'
                ' 0 2-1.9 3.5-4.5 3.5-2.4 0-4.2-1.1-4.7-2.8"/>',
    "clear_format": '<path d="M6 5h12M12 5l-3 14"/><path d="m15 14 6 6M21 14l-6 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "sparkle": '<path d="M12 4l1.8 4.7L18.5 10.5 13.8 12.3 12 17l-1.8-4.7L5.5 10.5l4.7-1.8z"/>',
    "panel": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M14.5 4.5v15"/>',
    "caret": '<path d="m7 10 5 5 5-5"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
}


def _svg(name, color):
    body = _ICONS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


_cache = {}


def pixmap(name, color=TEXT, size=18, ratio=2.0):
    key = (name, color, size, ratio)
    if key not in _cache:
        renderer = QSvgRenderer(QByteArray(_svg(name, color).encode()))
        pm = QPixmap(int(size * ratio), int(size * ratio))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size * ratio, size * ratio))
        p.end()
        pm.setDevicePixelRatio(ratio)
        _cache[key] = pm
    return _cache[key]


def icon(name, color=TEXT, size=18):
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal)
    ic.addPixmap(pixmap(name, "#b6bcc6", size), QIcon.Disabled)
    return ic


def color_icon(name, bar_color, size=18):
    """An icon with a coloured bar underneath (text colour and fill buttons)."""
    ratio = 2.0
    base = pixmap(name, TEXT, size, ratio)
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.drawPixmap(0, int(-2 * ratio), base)
    p.fillRect(int(3 * ratio), int((size - 3.5) * ratio), int((size - 6) * ratio), int(3 * ratio),
               QColor(bar_color or "#ffffff"))
    if not bar_color or QColor(bar_color).lightness() > 235:
        p.setPen(QColor("#c9d0da"))
        p.drawRect(int(3 * ratio), int((size - 3.5) * ratio), int((size - 6) * ratio) - 1, int(3 * ratio) - 1)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)


ICON_SIZE = QSize(18, 18)

SWATCHES = [
    "#000000", "#434343", "#666666", "#999999", "#cccccc", "#efefef", "#ffffff",
    "#b91c1c", "#c2410c", "#a16207", "#15803d", "#0e7490", "#1d4ed8", "#7e22ce",
    "#fecaca", "#fed7aa", "#fef08a", "#bbf7d0", "#a5f3fc", "#bfdbfe", "#e9d5ff",
]
