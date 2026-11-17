"""Rust Live Map: карта из папки сервера (.map локально или по FTP) и подписи монументов."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from .config import (
    map_output,
    maps_dir,
    save_config,
)
from .fsbackend import (
    proto_of,
)
from .mapsource import (
    parse_map_name,
)
from .monuments import (
    load_monuments,
    monuments_file_name,
)
from .workers import (
    MapRenderWorker,
    MapSourceWorker,
)


class MonumentsMixin:
    """Источник карты — файл .map в папке сервера; из него же берём подписи монументов."""

    def setup_monuments(self):
        self.src_worker = None
        self.files_win = None
        self._from_folder = False
        self._auto_fetch = False
        self._retry_pending = False
        self._upgrade_pending = False
        self._map_retries = 0
        self._upgrades = 0
        self.a_monuments = QAction("🏷 Подписи монументов", self, checkable=True)
        self.a_monuments.setChecked(bool(self.cfg.get("show_monuments")))
        self.a_monuments.setToolTip("Названия монументов на карте (берутся из файла .map)")
        self.a_monuments.triggered.connect(self.toggle_monuments)
        self.refresh_monuments()

    # ---- подписи ----
    def _mon_key(self):
        w = self.archive_wipe
        if w is not None:
            return int(w["world"] or self.cfg["world_size"]), w["seed"]
        return int(self.cfg["world_size"]), (self.cfg.get("map_seed") or self.log_store.current_seed())

    def _mon_path(self):
        world, seed = self._mon_key()
        return maps_dir() / monuments_file_name(world, seed) if seed else None

    def refresh_monuments(self):
        items = []
        if self.cfg.get("show_monuments"):
            p = self._mon_path()
            items = (load_monuments(p) if p else None) or []
        self.view.set_monuments(items)

    def toggle_monuments(self, on):
        self.cfg["show_monuments"] = bool(on)
        save_config(self.cfg)
        self.refresh_monuments()
        if not on:
            return
        p = self._mon_path()
        if p and p.exists():
            return
        if self.archive_wipe is not None:
            QMessageBox.information(self, "Подписи монументов",
                                    "Для этого вайпа нет данных о монументах: их можно получить только "
                                    "из файла .map, пока он лежит в папке сервера. Откройте текущий вайп "
                                    "и загрузите карту из папки сервера.")
        elif self.map_source_set():
            self.fetch_map_from_server()           # скачает .map, отрисует карту и достанет монументы
        else:
            r = QMessageBox.question(
                self, "Подписи монументов",
                "Подписи берутся из файла карты .map.\n\nУкажите в «Настройках» папку сервера "
                "(…\\server\\my_server_identity) — локальную или по FTP.\n\nОткрыть настройки?")
            if r == QMessageBox.StandardButton.Yes:
                self.open_settings()

    def open_server_files(self):
        if not self.map_source_set():
            r = QMessageBox.question(self, "Файлы сервера",
                                     "Сначала укажите доступ к серверу (папка, FTP или SFTP) в «Настройках».\n\nОткрыть настройки?")
            if r == QMessageBox.StandardButton.Yes:
                self.open_settings()
            return
        from .dialogs_files import ServerFilesDialog
        win = getattr(self, "files_win", None)
        if win is not None and win.isVisible():           # уже открыто — просто вернуть на передний план
            win.raise_()
            win.activateWindow()
            return
        win = ServerFilesDialog(self.cfg, None, save=lambda: save_config(self.cfg))   # отдельное окно, без привязки к главному
        win.setModal(False)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        win.show()
        self.files_win = win

    def _map_menu_texts(self):
        """Пункт «Обновить карту» подсказывает, откуда она возьмётся: из папки сервера или командой сервера."""
        if self.map_source_set():
            self.a_map_update.setText("🗺 Обновить карту (из папки сервера)")
            self.a_map_update.setToolTip("Берёт свежий .map из папки сервера и рисует карту сама")
        else:
            self.a_map_update.setText("🗺 Обновить карту (файл .map или PNG)…")
            self.a_map_update.setToolTip("Папка сервера не указана: можно выбрать .map или готовый PNG с этого компьютера. "
                                         "Укажите папку в «Настройках» — карта будет грузиться сама")

    def sync_world_size(self):
        """Размер мира — из настроек самого сервера (server.worldsize по RCON, он запоминается в вайпе):
        если он отличается от сохранённого, берём серверный и перерисовываем карту."""
        w = self.log_store.current_wipe()
        world = int(w["world"]) if w and w["world"] else 0
        if world and world != int(self.cfg["world_size"]):
            self.cfg["world_size"] = world
            save_config(self.cfg)
            if self.archive_wipe is None:                  # архивный вайп на экране — у него свой размер, не трогаем
                self.load_profile_map()

    # ---- карта из папки сервера ----
    def map_source_set(self):
        c = self.cfg
        return bool((c.get("srv_dir") or "").strip() and (proto_of(c) in ("local", "smb") or c.get("ftp_host")))

    def fetch_map_from_folder(self):
        if self.src_worker and self.src_worker.isRunning():
            return
        if self.render_worker and self.render_worker.isRunning():
            return
        self.map_label.setText("Беру карту из папки сервера…")
        self.busy.show()
        w = MapSourceWorker(self.cfg, self.log_store.current_seed())
        w.profile_id = self.cfg.get("active_profile")
        w.status.connect(self.map_label.setText)
        w.hostkey.connect(self.remember_hostkey)
        w.done.connect(self.on_source_map)
        w.not_ready.connect(self.on_source_not_ready)
        w.failed.connect(self.on_source_failed)
        self.src_worker = w
        w.start()

    def on_source_map(self, path):
        if self.src_worker and self.src_worker.profile_id != self.cfg.get("active_profile"):
            self.busy.hide()
            return                                    # пока ждали, переключили сервер
        info = parse_map_name(Path(path).name)
        if info:
            self.cfg["world_size"] = info[0]
            self.cfg["map_seed"] = info[1]
            self.view.set_world_size(info[0])
        same = self.cfg.get("map_path") == path and map_output().exists()
        self.cfg["map_path"] = path
        save_config(self.cfg)
        mp = self._mon_path()
        if same and self._auto_fetch and not self._from_folder and (mp is None or mp.exists()):
            self.busy.hide()                          # этот .map уже нарисован — не тратим время при каждом запуске
            self.load_profile_map()
            self.check_map_seed()
            return
        self.map_label.setText("Рисую карту из .map…")
        self._from_folder = True
        self.render_worker = MapRenderWorker(path, info[1] if info else None)
        self.render_worker.finished_ok.connect(self.on_render_done)
        self.render_worker.failed.connect(self.on_render_failed)
        self.render_worker.start()

    def remember_hostkey(self, fp):
        """Первое подключение по SSH: запоминаем отпечаток ключа сервера; дальше он сверяется."""
        if fp and not self.cfg.get("ssh_hostkey"):
            self.cfg["ssh_hostkey"] = fp
            save_config(self.cfg)

    def on_source_not_ready(self, msg):
        """В папке сервера ещё нет карты нового вайпа (у неё может быть другой размер, старую не подсовываем)."""
        self.busy.hide()
        if self.cfg.get("rcon_password"):
            self.map_label.setText("Карты нового вайпа в папке ещё нет — беру карту командой сервера…")
            self.fetch_map_via_rcon()
        else:
            self.map_label.setText("⏳ " + msg)
            self._schedule_map_retry()

    def on_source_failed(self, err):
        self.busy.hide()
        if self.cfg.get("rcon_password"):                 # запасной путь — команда сервера, без вопросов
            self.map_label.setText(f"Папка сервера недоступна ({err}) — беру карту командой сервера…")
            self.fetch_map_via_rcon()
        elif self._auto_fetch:
            self.map_label.setText(f"⚠ Карта: {err}")
            self._schedule_map_retry()
        else:
            self.load_profile_map()
            QMessageBox.warning(self, "Карта из папки сервера", err)

    def _schedule_map_retry(self):
        """Карту нового вайпа ждём автоматически: проверяем папку раз в полминуты (около 20 минут)."""
        if self._map_retries >= 40 or self._retry_pending:
            return
        self._map_retries += 1
        self._retry_pending = True
        QTimer.singleShot(30000, self._retry_map)

    def _retry_map(self):
        self._retry_pending = False
        if self.archive_wipe is None and self.map_stale:
            self.fetch_map_from_server(auto=True)

    def maybe_upgrade_from_folder(self):
        """Карта получена командой сервера, а подписям монументов нужен .map: через пару минут пробуем папку сервера."""
        p = self._mon_path()
        if (self.map_source_set() and self.cfg.get("show_monuments") and not self._upgrade_pending
                and self._upgrades < 5 and not (p and p.exists())):
            self._upgrades += 1
            self._upgrade_pending = True
            QTimer.singleShot(120000, self._upgrade_map)

    def _upgrade_map(self):
        self._upgrade_pending = False
        busy = any(w and w.isRunning() for w in (self.src_worker, self.fetch_worker, self.render_worker))
        if self.archive_wipe is None and not busy:
            self.fetch_map_from_server(auto=True)

    def finish_render(self, seed):
        """После отрисовки из .map: флаги актуальности карты, архив, метки, подписи."""
        if seed:
            self.cfg["map_seed"] = str(seed)
            cur = self.log_store.current_seed()
            self.map_stale = bool(cur and str(cur) != str(seed))
            # в папке может ещё не быть .map нового вайпа: спросить один раз, а не по кругу
            self._map_prompted = True if self._from_folder else not self.map_stale
        else:
            self.mark_map_current()
        self._from_folder = False
        self._map_retries = 0
        save_config(self.cfg)
        self.archive_store_current(True)
        self.refresh_markers()
        self.update_event_marks()
        self.check_map_seed()
        self.refresh_heat(True)
        self.refresh_monuments()
        if self.map_stale:
            base = self.map_label.text()
            if "⚠" not in base:
                self.map_label.setText(base + " ⚠ в папке сервера нет карты нового вайпа")



