"""Rust Live Map: модуль window_build."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QFont, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTextBrowser, QToolBar, QToolButton, QVBoxLayout, QWidget
from collections import deque

from .dialogs import (
    HistoryLineEdit,
    PeriodCombo,
)
from .mapview import (
    HeatLegend,
    HudChip,
)


class BuildMixin:
    """Построение интерфейса: панель инструментов, панели, строка статуса."""

    # ---------- UI ----------
    def _build_toolbar(self):
        tb = QToolBar("Главная")
        tb.setMovable(False)
        self.toolbar = tb
        self.addToolBar(tb)

        tb.addWidget(QLabel(" Сервер: "))
        self.profile_cb = QComboBox()
        self.profile_cb.setMinimumWidth(150)
        self.profile_cb.setToolTip("Профиль сервера: у каждого своя статистика, карта и настройки RCON")
        tb.addWidget(self.profile_cb)
        pbtn = QToolButton()
        pbtn.setText("⋯")
        pbtn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pm = QMenu(pbtn)
        pm.addAction("➕ Добавить сервер…", self.add_profile)
        pm.addAction("✏ Переименовать…", self.rename_profile)
        pm.addAction("🗄 Удалить (в архив)…", self.delete_profile)
        pbtn.setMenu(pm)
        tb.addWidget(pbtn)
        tb.addSeparator()
        self.fill_profiles()
        self.profile_cb.currentIndexChanged.connect(self.on_profile_selected)

        mbtn = QToolButton()
        mbtn.setText("🗺 Карта")
        mbtn.setToolTip("Откуда взять карту")
        mbtn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mm = QMenu(mbtn)
        self.a_map_update = mm.addAction("🗺 Обновить карту", lambda *_: self.fetch_map_from_server())
        mm.aboutToShow.connect(self._map_menu_texts)
        mm.addAction("🗂 Файлы сервера…", self.open_server_files)
        mm.addSeparator()
        mm.addAction("📁 Открыть папку архива карт", self.open_maps_dir)
        mbtn.setMenu(mm)
        tb.addWidget(mbtn)

        tbtn = QToolButton()
        tbtn.setText("⏰ Задания")
        tbtn.setToolTip("Команды в консоль сервера по расписанию")
        tbtn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tm = QMenu(tbtn)
        tm.addAction("➕ Новое задание…", self.new_task)
        tm.addAction("📋 Список заданий…", self.open_tasks)
        tbtn.setMenu(tm)
        tb.addWidget(tbtn)

        self.a_conn = QAction("🔌 Подключиться", self)
        self.a_conn.setToolTip("Подключиться к серверу по RCON или отключиться (Ctrl+R)")
        self.a_conn.triggered.connect(self.toggle_rcon)
        tb.addAction(self.a_conn)

        a_set = QAction("⚙ Настройки", self)
        a_set.setToolTip("Адрес сервера, RCON-пароль, папка логов (Ctrl+,)")
        a_set.triggered.connect(lambda *_: self.open_settings())
        tb.addAction(a_set)

        tb.addSeparator()

        self.a_fit = QAction("⤢ По размеру", self)
        self.a_fit.setToolTip("Показать карту целиком (Ctrl+0)")
        self.a_fit.triggered.connect(self.view.fit)      # в меню по правому клику на карте

        self.a_players = QAction("👥 Игроки", self, checkable=True)
        self.a_players.setChecked(bool(self.cfg.get("show_players", True)))
        self.a_players.setToolTip("Показывать игроков на карте. Выключено — позиции всё равно копятся для Heatmap\n"
                                  "(удобно для скриншотов Heatmap без маркеров).")
        self.a_names = QAction("Имена", self, checkable=True)
        self.a_names.setToolTip("Подписи с никами игроков")
        self.a_names.setChecked(bool(self.cfg["show_names"]))
        self.a_hp = QAction("HP", self, checkable=True)
        self.a_hp.setToolTip("Полоска здоровья у игрока")
        self.a_hp.setChecked(bool(self.cfg["show_hp"]))
        self.a_bases = QAction("🏠 Базы", self, checkable=True)
        self.a_bases.setChecked(bool(self.cfg["show_bases"]))
        self.a_grid = QAction("▦ Сетка", self, checkable=True)
        self.a_grid.setToolTip("Координатная сетка карты")
        self.a_grid.setChecked(bool(self.cfg.get("show_grid")))
        self.a_marks = QAction("✕ События", self, checkable=True)
        self.a_marks.setToolTip("Места убийств и смертей на карте.\nИгрок не выбран — события всех игроков "
                                "за период (последние 400), выбран — только его события.")
        self.a_marks.setChecked(bool(self.cfg.get("show_marks", True)))
        for a in (self.a_players, self.a_names, self.a_hp, self.a_bases, self.a_grid):
            a.toggled.connect(self.on_toggle)
        tb.addSeparator()

        # «Игроки»: клик — показать/скрыть, стрелка — подписи (имена, HP)
        pb = QToolButton()
        pb.setDefaultAction(self.a_players)
        pb.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        pmn = QMenu(pb)
        pmn.addAction(self.a_names)
        pmn.addAction(self.a_hp)
        pb.setMenu(pmn)
        tb.addWidget(pb)
        tb.addAction(self.a_bases)

        # «События»: клик — включить/выключить, стрелка — какие виды показывать
        self.a_marks.toggled.connect(self.on_toggle)
        mb = QToolButton()
        mb.setDefaultAction(self.a_marks)
        mb.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        mm2 = QMenu(mb)
        self.cat_actions = {}
        on = set(self.cfg.get("marks_cats") or [])
        for cat, text in (("pvp", "⚔ PvP (убийства игроков)"), ("self", "💀 Суицид и среда (голод, падение…)"),
                          ("pve", "🐺 NPC, животные, техника")):
            ca = QAction(text, mm2, checkable=True)
            ca.setChecked(cat in on)
            ca.toggled.connect(self.on_marks_cats)
            mm2.addAction(ca)
            self.cat_actions[cat] = ca
        mb.setMenu(mm2)
        tb.addWidget(mb)

        hbtn = QToolButton()
        hbtn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        hbtn.setToolTip("Тепловая карта по всем игрокам: где ходят чаще всего или где чаще убивают.\n"
                        "Строится за выбранный период и показанный вайп.")
        hm = QMenu(hbtn)
        grp = QActionGroup(hm)
        grp.setExclusive(True)
        self.heat_actions = {}
        for mode, text in (("off", "Выключено"), ("move", "🚶 Передвижение игроков"),
                           ("pvp", "⚔ PvP-зоны (убийства игрок→игрок)")):
            a = QAction(text, hm, checkable=True)
            a.setChecked(self.cfg.get("heat_mode", "off") == mode)
            a.triggered.connect(lambda _c=False, m=mode: self.set_heat_mode(m))
            grp.addAction(a)
            hm.addAction(a)
            self.heat_actions[mode] = a
        hbtn.setMenu(hm)
        self.heat_btn = hbtn
        tb.addWidget(hbtn)
        self._heat_btn_text()

        # горячие клавиши
        for seq, fn in (("Ctrl+F", self.focus_search), ("Ctrl+0", self.view.fit),
                        ("Ctrl+R", self.toggle_rcon), ("Ctrl+,", self.open_settings)):
            sc = QAction(self)
            sc.setShortcut(QKeySequence(seq))
            sc.triggered.connect(fn)
            self.addAction(sc)

    def focus_search(self):
        self.panel_dock.show()
        self.tabs.setCurrentWidget(self.players_tab)
        self.search.setFocus()
        self.search.selectAll()

    def _build_dock(self):
        # --- игроки ---
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск игрока...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh_table())

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Игрок", "HP", "Команда"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(lambda pos: self.player_menu(pos, self.table))
        self.table.cellClicked.connect(lambda r, c: self.select_player(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        self.table.cellDoubleClicked.connect(self.center_on_row)

        players_tab = QWidget()
        pl = QVBoxLayout(players_tab)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(2)
        pl.addWidget(self.search)
        pl.addWidget(self.table)

        # --- карточка ---
        self.hp_bar = QProgressBar()
        self.hp_bar.setRange(0, 100)
        self.hp_bar.setTextVisible(True)
        self.hp_bar.setFormat("—")

        # Карточка: «Профиль» (live + статистика из логов) и «Убийства / смерти»
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self.on_anchor)
        self.events_view = QTextBrowser()
        self.events_view.setOpenLinks(False)
        self.events_view.anchorClicked.connect(self.on_anchor)
        self.card_tabs = QTabWidget()
        self.card_tabs.addTab(self.details, "Профиль")
        self.card_tabs.addTab(self.events_view, "Убийства / смерти")

        card = QWidget()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.addWidget(self.hp_bar)
        cl.addWidget(self.card_tabs)

        # --- базы ---
        self.bases_table = QTableWidget(0, 4)
        self.bases_table.setHorizontalHeaderLabels(["Entity ID", "Квадрат", "Авториз.", "Y"])
        self.bases_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bases_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bases_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.bases_table.verticalHeader().setVisible(False)
        self.bases_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bases_table.setAlternatingRowColors(True)
        self.bases_table.cellClicked.connect(
            lambda r, c: self.select_base(int(self.bases_table.item(r, 0).data(Qt.ItemDataRole.UserRole))))
        self.bases_table.cellDoubleClicked.connect(self.center_on_base_row)

        # «Все игроки»: все, кто встречался в статистике выбранного периода (в том числе офлайн)
        self.all_search = QLineEdit()
        self.all_search.setPlaceholderText("Поиск среди всех игроков вайпа...")
        self.all_search.setClearButtonEnabled(True)
        self.all_search.textChanged.connect(lambda _t: self.refresh_all_table())
        self.all_table = QTableWidget(0, 3)
        self.all_table.setHorizontalHeaderLabels(["Игрок", "Был в игре", "Online"])
        self.all_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.all_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.all_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.all_table.verticalHeader().setVisible(False)
        self.all_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.all_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.all_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.all_table.setAlternatingRowColors(True)
        self.all_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.all_table.customContextMenuRequested.connect(lambda pos: self.player_menu(pos, self.all_table))
        self.all_table.cellClicked.connect(
            lambda r, c: self.select_player(self.all_table.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        self.all_table.cellDoubleClicked.connect(lambda r, c: self.center_on_sid(
            self.all_table.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        self.all_tab = QWidget()
        al = QVBoxLayout(self.all_tab)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.addWidget(self.all_search)
        al.addWidget(self.all_table)
        self.players_tab = players_tab

        self.tabs = QTabWidget()
        self.tabs.addTab(players_tab, "Игроки")
        self.tabs.addTab(self.all_tab, "Все игроки")
        self.tabs.addTab(self.build_teams_tab(), "Команды")
        self.tabs.addTab(self.bases_table, "Базы (TC)")
        self.server_view = QTextBrowser()
        self.server_view.setOpenLinks(False)
        self.server_view.anchorClicked.connect(self.on_anchor)
        self.tabs.addTab(self.server_view, "Сервер")
        self.tabs.currentChanged.connect(self.on_tab_changed)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(card)
        split.setSizes([400, 400])

        self.panel_dock = QDockWidget("Статистика", self)
        self.panel_dock.setObjectName("panel_dock")
        self.period_cb = PeriodCombo()
        self.period_cb.setToolTip("Текущий вайп, вся статистика или выбранные даты (календарь).\n"
                                  "Для выбранных дат: стрелки ↑ / ↓ сдвигают период назад / вперёд\n"
                                  "(день, неделя или месяц целиком).\n"
                                  "Места смертей на карте показываются только для текущего вайпа\n"
                                  "(у каждого вайпа своя карта).")
        self.period_cb.currentIndexChanged.connect(self.on_period_changed)
        self.period_cb.step_requested.connect(self.step_period)
        sbtn = QToolButton()
        sbtn.setText("🗂 Данные")
        sbtn.setToolTip("Границы вайпов, резервные копии, очистка статистики")
        sbtn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sm = QMenu(sbtn)
        sm.addAction("🗂 Границы вайпов…", self.manage_wipes)
        sm.addSeparator()
        sm.addAction("💾 Резервная копия сейчас", self.backup_now)
        sm.addAction("🧹 Очистить текущий вайп…", lambda: self.clear_stats(False))
        sm.addAction("🗑 Очистить всю статистику сервера…", lambda: self.clear_stats(True))
        sbtn.setMenu(sm)
        top = QHBoxLayout()
        top.setContentsMargins(4, 4, 4, 0)
        top.addWidget(QLabel("Период:"))
        top.addWidget(self.period_cb, 1)
        top.addWidget(sbtn)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        wl.addLayout(top)
        wl.addWidget(split)
        self.panel_dock.setWidget(wrap)
        self.panel_dock.setMinimumWidth(380)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.panel_dock)

        # --- игровой чат: живая лента + поиск по архиву (txt-файлы по дням) ---
        self.chat_view = QTextBrowser()
        self.chat_view.setOpenLinks(False)
        self.chat_view.document().setMaximumBlockCount(1000)
        self.search_view = QTextBrowser()
        self.search_view.setOpenLinks(False)
        self.chat_stack = QStackedWidget()
        self.chat_stack.addWidget(self.chat_view)
        self.chat_stack.addWidget(self.search_view)
        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText("🔍 Поиск по всему архиву чата — слова через пробел, Enter")
        self.chat_search.setClearButtonEnabled(True)
        self.chat_search.returnPressed.connect(self.search_chat)
        self.chat_search.textChanged.connect(lambda t: None if t.strip() else self.chat_stack.setCurrentIndex(0))
        chat_wrap = QWidget()
        cl = QVBoxLayout(chat_wrap)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.chat_search)
        cl.addWidget(self.chat_stack)
        self.chat_dock = QDockWidget("Чат", self)
        self.chat_dock.setObjectName("chat_dock")
        self.chat_dock.setWidget(chat_wrap)
        self.chat_dock.setMinimumHeight(110)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.chat_dock)
        self._chat_hist = set()
        self._search_token = 0
        self._search_workers = set()

        # --- консоль сервера: поток RCON + ввод команд ---
        self.console_buf = deque(maxlen=5000)
        self.console_view = QTextBrowser()
        self.console_view.setOpenLinks(False)
        self.console_view.document().setMaximumBlockCount(5000)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.console_view.setFont(mono)
        self.console_filter = QLineEdit()
        self.console_filter.setPlaceholderText("Фильтр по тексту…")
        self.console_filter.setClearButtonEnabled(True)
        self._console_pending = []                     # строки, ещё не выведенные в окно
        self._console_timer = QTimer(self)
        self._console_timer.setSingleShot(True)
        self._console_timer.timeout.connect(self.flush_console)
        self._filter_timer = QTimer(self)              # фильтр применяем после паузы в наборе, не на каждую букву
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self.render_console)
        self.console_filter.textChanged.connect(lambda _t: self._filter_timer.start())
        self.console_scroll = QCheckBox("Автопрокрутка")
        self.console_scroll.setChecked(True)
        b_clear = QPushButton("Очистить окно")
        b_clear.setToolTip("Очищает только окно; файлы консоли на диске остаются")
        b_clear.clicked.connect(self.clear_console)
        self.console_input = HistoryLineEdit()
        self.console_input.setPlaceholderText("Команда серверу (Enter — отправить, ↑/↓ — история)")
        self.console_input.returnPressed.connect(self.send_console_command)
        top = QHBoxLayout()
        top.addWidget(self.console_filter, 1)
        top.addWidget(self.console_scroll)
        top.addWidget(b_clear)
        con_wrap = QWidget()
        kl = QVBoxLayout(con_wrap)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addLayout(top)
        kl.addWidget(self.console_view, 1)
        kl.addWidget(self.console_input)
        self.console_dock = QDockWidget("Консоль", self)
        self.console_dock.setObjectName("console_dock")
        self.console_dock.setWidget(con_wrap)
        self.console_dock.setMinimumHeight(110)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        # раскладка по умолчанию: чат — колонка слева, статистика — колонка справа, консоль — внизу
        # под чатом и картой (углы отданы нижней панели слева и правой колонке справа)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)

        # одно меню вместо отдельных кнопок: какие панели показывать
        self.toolbar.addSeparator()
        pbtn = QToolButton()
        pbtn.setText("🗔 Панели")
        pbtn.setToolTip("Показать или скрыть панели: статистика, чат, консоль сервера")
        pbtn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pm = QMenu(pbtn)
        for dock, text in ((self.panel_dock, "📊 Статистика"), (self.chat_dock, "💬 Чат"),
                           (self.console_dock, "⌨ Консоль")):
            act = dock.toggleViewAction()
            act.setText(text)
            pm.addAction(act)
        pbtn.setMenu(pm)
        self.toolbar.addWidget(pbtn)
        self.load_chat_history()

    def _build_dates_label(self):
        # справа на панели: когда начался текущий вайп и с какого дня есть статистика
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color:#ddd; font-weight:bold; padding-right:14px")
        self.time_label.setToolTip("Время суток на сервере (RCON env.time)")
        self.toolbar.addWidget(self.time_label)
        self.dates_label = QLabel("")
        self.dates_label.setStyleSheet("color:#aaa; padding-right:8px")
        self.toolbar.addWidget(self.dates_label)
        self._dates_sig = None

    def _build_statusbar(self):
        sb = self.statusBar()
        self.rcon_label = QLabel("RCON: отключено")
        # онлайн и состояние сервера — плашками в правом верхнем углу карты, легенда Heatmap — в левом нижнем
        vp = self.view.viewport()
        self.hud = QWidget(vp)
        hl = QHBoxLayout(self.hud)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.count_label = HudChip("Online: 0")
        self.info_label = HudChip("")
        self.info_label.hide()
        hl.addWidget(self.count_label)
        hl.addWidget(self.info_label)
        self.hud.show()
        self.view.resized.connect(self.reposition_hud)
        self.bases_label = QLabel("Базы (TC): —")
        self.logs_label = QLabel("Статистика: —")
        self.map_label = QLabel("Карта не загружена")
        self.coord_label = QLabel("")
        self._hp_note = ""
        self._srv_info = None
        self._mem_base = None
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.hide()
        sb.addWidget(self.rcon_label, 1)
        # bases_label, logs_label, map_label и coord_label в строке статуса не показываем (создаются,
        # чтобы остальной код мог обновлять их текст, но в окно не добавляются)
        self.heat_label = HeatLegend(vp)
        sb.addPermanentWidget(self.busy)
        self.reposition_hud()
