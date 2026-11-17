"""Rust Live Map: раскладка панелей и вывод карты на весь экран / в отдельное окно / на другой монитор."""

import base64

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .config import save_config


class MapFloatWindow(QWidget):
    """Отдельное окно, в которое переезжает карта. Esc или закрытие окна возвращает её на место, F11 — весь экран."""
    closed = Signal()

    def __init__(self, view):
        super().__init__()
        self.setWindowTitle("Rust Live Map — карта")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_full)

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def closeEvent(self, e):
        super().closeEvent(e)
        self.closed.emit()


class ScreenMixin:
    """Раскладка панелей (запоминается между запусками) и кнопка «⛶» в правом нижнем углу карты."""

    # ---- раскладка панелей ----
    def restore_layout(self):
        """Прошлая раскладка из конфига; если её нет или она не читается — раскладка по умолчанию."""
        ok = False
        st = self.cfg.get("dock_state")
        if st:
            try:
                ok = bool(self.restoreState(base64.b64decode(st)))
            except Exception:
                ok = False
        if not ok:
            self.default_layout()

    def default_layout(self):
        self.chat_dock.show()
        self.panel_dock.show()
        self.console_dock.show()
        QTimer.singleShot(0, self._default_sizes)

    def _default_sizes(self):
        self.resizeDocks([self.chat_dock, self.panel_dock], [270, 400], Qt.Orientation.Horizontal)
        self.resizeDocks([self.console_dock], [150], Qt.Orientation.Vertical)

    def reset_layout(self):
        self.cfg.pop("dock_state", None)
        for d, area in ((self.chat_dock, Qt.DockWidgetArea.LeftDockWidgetArea),
                        (self.panel_dock, Qt.DockWidgetArea.RightDockWidgetArea),
                        (self.console_dock, Qt.DockWidgetArea.BottomDockWidgetArea)):
            d.setFloating(False)
            self.addDockWidget(area, d)
        self.default_layout()

    def save_layout(self):
        try:
            self.cfg["dock_state"] = base64.b64encode(bytes(self.saveState())).decode("ascii")
            save_config(self.cfg)
        except Exception:
            pass

    # ---- карта на весь экран / на другой монитор ----
    def setup_map_screen(self):
        self.map_win = None
        # карта лежит в контейнере, который остаётся центральным виджетом всегда: размеры панелей не прыгают,
        # когда карта уезжает в отдельное окно и возвращается
        self.takeCentralWidget()
        self.map_box = QWidget()
        self.map_lay = QVBoxLayout(self.map_box)
        self.map_lay.setContentsMargins(0, 0, 0, 0)
        self.map_lay.addWidget(self.view)
        self.setCentralWidget(self.map_box)
        self.map_ph = QLabel("Карта открыта в отдельном окне.\nВернуть: Esc в том окне или кнопка ⛶ на карте.")
        self.map_ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_ph.setStyleSheet("color:#888;font-size:14px")
        self.map_ph.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.map_ph.setMinimumSize(0, 0)
        self.map_ph.hide()
        self.map_lay.addWidget(self.map_ph)
        vp = self.view.viewport()
        self.fs_btn = QToolButton(vp)
        self.fs_btn.setText("⛶")
        self.fs_btn.setToolTip("Карта на весь экран, в отдельное окно или на другой монитор")
        self.fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fs_btn.setFixedSize(34, 34)
        self.fs_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.fs_btn.setStyleSheet(
            "QToolButton{background:rgba(18,18,20,205);color:#e6e6e6;border:1px solid rgba(255,255,255,40);"
            "border-radius:9px;font-size:17px;padding:0}"
            "QToolButton:hover{background:rgba(60,60,66,230)}QToolButton::menu-indicator{image:none}")
        self.fs_menu = QMenu(self.fs_btn)
        self.fs_menu.aboutToShow.connect(self._fill_screen_menu)
        self.fs_btn.setMenu(self.fs_menu)
        self.fs_btn.show()
        self.view.resized.connect(self._place_fs_btn)
        self._place_fs_btn()

    def _place_fs_btn(self):
        vp = self.view.viewport()
        self.fs_btn.move(max(8, vp.width() - self.fs_btn.width() - 12), max(8, vp.height() - self.fs_btn.height() - 12))
        self.fs_btn.raise_()

    def _fill_screen_menu(self):
        m = self.fs_menu
        m.clear()
        if self.map_win is not None:
            m.addAction("↩ Вернуть карту в главное окно (Esc)", self.return_map)
            m.addSeparator()
            m.addAction("Весь экран / обычное окно (F11)", self.map_win.toggle_full)
            m.addSeparator()
        else:
            m.addAction("⛶ На весь экран", lambda: self.detach_map(True, self.screen()))
            m.addAction("🗗 В отдельное окно", lambda: self.detach_map(False, self.screen()))
            m.addSeparator()
        scr = QGuiApplication.screens()
        if len(scr) > 1:
            for i, s in enumerate(scr, 1):
                g = s.geometry()
                name = f"Монитор {i} ({g.width()}×{g.height()})"
                m.addAction(f"⛶ Весь экран: {name}", lambda s=s: self.detach_map(True, s))
            m.addSeparator()
            for i, s in enumerate(scr, 1):
                g = s.geometry()
                m.addAction(f"🗗 Окно на мониторе {i}", lambda s=s: self.detach_map(False, s))

    def detach_map(self, full, screen):
        """Переносит карту в отдельное окно (на весь экран выбранного монитора или обычным окном)."""
        if self.map_win is None:
            self.map_lay.removeWidget(self.view)
            self.map_ph.show()
            self.map_win = MapFloatWindow(self.view)
            self.map_win.closed.connect(self.return_map)
        w = self.map_win
        if screen is not None:
            try:
                w.setScreen(screen)
            except Exception:
                pass
            g = screen.geometry()
            w.resize(min(1280, g.width() - 120), min(860, g.height() - 120))
            w.move(g.topLeft() + QPoint(60, 60))
        if w.isFullScreen():
            w.showNormal()
        w.show()
        if full:
            w.showFullScreen()
        w.raise_()
        w.activateWindow()
        self.view.setFocus()
        QTimer.singleShot(0, self._place_fs_btn)

    def return_map(self):
        w = self.map_win
        if w is None:
            return
        self.map_win = None
        try:
            w.closed.disconnect()
        except Exception:
            pass
        self.map_ph.hide()
        self.map_lay.insertWidget(0, self.view)
        self.view.show()
        w.close()
        w.deleteLater()
        QTimer.singleShot(0, self._place_fs_btn)
