"""Rust Live Map: иконка приложения и тёмная рамка окон Windows."""

import sys
from pathlib import Path
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget

CAPTION_BGR = 0x002D2A2A     # #2a2a2d (COLORREF = 0x00BBGGRR) — цвет панелей приложения
TEXT_BGR = 0x00E6E6E6


def resource_path(name):
    """Файл рядом с программой: в сборке PyInstaller — во временной папке, из исходников — в корне проекта."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name


def app_icon():
    p = resource_path("icon.png")
    return QIcon(str(p)) if p.exists() else QIcon()


def set_app_id():
    """Свой AppUserModelID: в панели задач показывается иконка программы, а не python/pyinstaller."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RustLiveMap.App")
    except Exception:
        pass


def dark_titlebar(widget):
    """Тёмный заголовок окна Windows (Win10 1809+: тёмная тема; Win11: ещё и цвет под панели приложения)."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import byref, c_int, sizeof, windll
        hwnd = int(widget.winId())
        for attr, val in ((20, 1), (19, 1)):                   # DWMWA_USE_IMMERSIVE_DARK_MODE (новый и старый номер)
            if windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, byref(c_int(val)), sizeof(c_int)) == 0:
                break
        for attr, val in ((35, CAPTION_BGR), (36, TEXT_BGR), (34, CAPTION_BGR)):   # Win11: заголовок, текст, рамка
            windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, byref(c_int(val)), sizeof(c_int))
    except Exception:
        pass


class WindowStyler(QObject):
    """Фильтр событий приложения: каждому окну верхнего уровня (и диалогам) — иконка и тёмный заголовок."""

    def __init__(self, icon):
        super().__init__()
        self.icon = icon

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            if not obj.property("_styled"):
                obj.setProperty("_styled", True)
                if not self.icon.isNull() and obj.windowIcon().isNull():
                    obj.setWindowIcon(self.icon)
                dark_titlebar(obj)
        return False
