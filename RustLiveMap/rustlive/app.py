"""Rust Live Map: модуль app."""

import sys
from PySide6.QtGui import QColor, QImageReader, QPalette
from PySide6.QtWidgets import QApplication

from .config import (
    load_config,
    log_error,
)
from .branding import WindowStyler, app_icon, set_app_id
from .window import (
    MainWindow,
)


# ============================================================
# MAIN
# ============================================================

APP_QSS = """
QToolTip { background: #2b2b2e; color: #e6e6e6; border: 1px solid #4a4a50; padding: 4px 6px; }
QToolBar { background: #2a2a2d; border: 0; border-bottom: 1px solid #3a3a3f; spacing: 4px; padding: 3px 6px; }
QToolBar::separator { background: #3f3f45; width: 1px; margin: 5px 6px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 9px; color: #e6e6e6; }
QToolButton:hover { background: #3a3a40; border-color: #4a4a52; }
QToolButton:pressed { background: #44444b; }
QToolButton:checked { background: #7a2f22; border-color: #b43c28; color: #ffffff; }
QToolButton[popupMode="1"] { padding-right: 20px; }
QToolButton::menu-button { border: 0; width: 16px; border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QToolButton::menu-button:hover { background: #4a4a52; }
QMenu { background: #2b2b2e; border: 1px solid #4a4a50; padding: 3px; }
QLineEdit { background: #1e1e21; border: 1px solid #3f3f45; border-radius: 6px; padding: 4px 8px; selection-background-color: #b43c28; }
QLineEdit:focus { border-color: #b43c28; }
QPushButton { background: #38383d; border: 1px solid #4a4a52; border-radius: 6px; padding: 5px 14px; }
QPushButton:hover { background: #43434a; }
QPushButton:pressed { background: #4c4c54; }
QPushButton:disabled { color: #777777; border-color: #3a3a3f; }
QTabWidget::pane { border: 1px solid #3a3a3f; top: -1px; }
QTabBar::tab { background: transparent; color: #aaaaaa; padding: 6px 14px; border: 0; border-bottom: 2px solid transparent; margin-right: 2px; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #cd412b; }
QTabBar::tab:hover:!selected { color: #dddddd; background: #2f2f33; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 0; }
QScrollBar::handle:vertical { background: #55555c; border-radius: 5px; min-height: 28px; margin: 1px; }
QScrollBar::handle:vertical:hover { background: #6c6c74; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle:horizontal { background: #55555c; border-radius: 5px; min-width: 28px; margin: 1px; }
QScrollBar::handle:horizontal:hover { background: #6c6c74; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QHeaderView::section { background: #2f2f33; color: #bbbbbb; border: 0; border-right: 1px solid #3a3a3f; padding: 4px 6px; }
QDockWidget::title { background: #2a2a2d; padding: 6px 8px; border-bottom: 1px solid #3a3a3f; }
QStatusBar { background: #2a2a2d; border-top: 1px solid #3a3a3f; }
QStatusBar::item { border: 0; }
"""


def apply_dark_theme(app, theme="modern"):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(37, 37, 38))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(230, 230, 230))
    pal.setColor(QPalette.ColorRole.Base, QColor(28, 28, 30))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(37, 37, 38))
    pal.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
    pal.setColor(QPalette.ColorRole.Button, QColor(55, 55, 58))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(230, 230, 230))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(180, 60, 40))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ColorRole.Link, QColor("#6cb6ff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 146))   # подсказки в пустых полях
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 50))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(230, 230, 230))
    app.setPalette(pal)
    if theme != "classic":
        app.setStyleSheet(APP_QSS)


def install_error_log():
    """Окно без консоли: необработанные ошибки пишем в error.log рядом с программой."""
    import traceback

    def hook(etype, value, tb):
        log_error("".join(traceback.format_exception(etype, value, tb)))
        sys.__excepthook__(etype, value, tb)

    sys.excepthook = hook


def main():
    install_error_log()
    QImageReader.setAllocationLimit(0)  # большие PNG карты (иначе Qt может отказаться грузить)
    set_app_id()
    app = QApplication(sys.argv)
    icon = app_icon()
    app.setWindowIcon(icon)
    app._styler = WindowStyler(icon)       # иконка и тёмный заголовок для всех окон
    app.installEventFilter(app._styler)
    apply_dark_theme(app, load_config().get("theme", "modern"))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
