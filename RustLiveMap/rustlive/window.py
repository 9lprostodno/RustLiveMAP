"""Rust Live Map: модуль window."""

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QMainWindow

from .config import (
    load_config,
)
from .mapview import (
    MapView,
)
from .stats import (
    open_stats_store,
)
from .window_build import (
    BuildMixin,
)
from .window_cards import (
    CardsMixin,
)
from .window_chat import (
    ChatMixin,
)
from .window_heat import (
    HeatMixin,
)
from .window_map import (
    MapMixin,
)
from .window_monuments import (
    MonumentsMixin,
)
from .window_players import (
    PlayersMixin,
)
from .window_profiles import (
    ProfilesMixin,
)
from .window_screen import (
    ScreenMixin,
)
from .window_tasks import (
    TasksMixin,
)
from .window_teams import (
    TeamsMixin,
)
from .window_status import (
    StatusMixin,
)


class MainWindow(MapMixin, HeatMixin, StatusMixin, PlayersMixin, ProfilesMixin, CardsMixin, ChatMixin, BuildMixin, ScreenMixin, MonumentsMixin, TeamsMixin, TasksMixin, QMainWindow):
    wipe_changed = Signal(int, str, int)   # из потока консоли -> в UI

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rust Live Map")
        self.resize(1400, 860)

        self.cfg = load_config()
        self.players = []
        self.selected = None
        self.bases = []
        self.selected_base = None
        self.rcon = None
        self.bases_worker = None
        self.teams_worker = None
        self.team_dialogs = {}
        self._teams_sig = None
        self.chat_worker = None
        self.player_heat = None        # SteamID игрока, чьи передвижения показаны на карте
        self._all_sig = None
        self._chat_status_last = ""
        self.log_store = open_stats_store(self.cfg)
        self.log_store.wipe_cb = lambda wid, seed, world: self.wipe_changed.emit(int(wid), str(seed), int(world or 0))
        self.wipe_changed.connect(self.on_new_wipe)
        self.map_stale = False
        self._map_prompted = False
        self._host_warned = False
        self._period_sig = None
        self._hl_event = None
        self._table_sig = None
        self._memo_cache = {}
        self._html_cache = {}
        self._tick = 0
        self.render_worker = None
        self.fetch_worker = None
        self.src_worker = None
        self.archive_wipe = None     # вайп, чья архивная карта сейчас на экране (None — текущий, «живая» карта)
        self._arch_seed = None
        self._arch_prev = None
        self._heat_sig = None
        self._all_marks_sig = None
        self._all_marks_cache = []
        self._all_owners = {}

        self.view = MapView()
        self.view.world_size = float(self.cfg["world_size"])
        self.view.grid_visible = bool(self.cfg.get("show_grid"))
        self.view.player_clicked.connect(self.select_player)
        self.view.base_clicked.connect(self.select_base)
        self.view.empty_clicked.connect(self.clear_selection)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.map_menu)
        self.view.event_clicked.connect(self.on_event_clicked)
        self.view.cursor_world.connect(self.on_cursor)
        self.setCentralWidget(self.view)

        self._build_toolbar()
        self._build_dock()
        self._build_dates_label()
        self._build_statusbar()
        self.setup_map_screen()
        self.setup_monuments()
        self.setup_tasks()
        self.restore_layout()

        self.load_profile_map()
        self.fill_periods(True)
        self.sync_map_to_period()

        self.start_rcon()
        QTimer.singleShot(700, self.auto_load_map)
        if not self.rcon_configured():      # первый запуск — сразу предлагаем настроить сервер
            QTimer.singleShot(400, lambda: self.open_settings(ask_folder=True))

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.on_stats_tick)
        self.stats_timer.start(5000)
        QTimer.singleShot(2500, self.check_long_wipes)

    def closeEvent(self, e):
        self.save_layout()
        self.return_map()
        if self.files_win is not None:
            self.files_win.close()
        if self.tasks_win is not None:
            try:
                self.tasks_win.close()
            except RuntimeError:
                pass
        self.stop_rcon()
        for d in list(self.team_dialogs.values()):
            try:
                d.close()
            except RuntimeError:
                pass
        for wk in list(self._search_workers):
            wk.wait(2000)
        self.log_store.close()
        if self.render_worker and self.render_worker.isRunning():
            self.render_worker.wait(3000)
        if self.src_worker and self.src_worker.isRunning():
            self.src_worker.wait(3000)
        if self.fetch_worker and self.fetch_worker.isRunning():
            self.fetch_worker.stop()
            self.fetch_worker.wait(3000)
        super().closeEvent(e)
