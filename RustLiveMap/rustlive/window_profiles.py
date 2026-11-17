"""Rust Live Map: модуль window_profiles."""

import datetime
import shutil
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from .config import (
    ARCHIVE_DIR,
    load_profile,
    new_profile,
    pdir,
    save_config,
    set_active_profile,
)
from .dialogs import (
    DateRangeDialog,
)
from .maps import (
    archive_map_path,
)
from .periods import (
    fmt_date,
    is_range_period,
    is_wipe_period,
    shift_range,
)
from .stats import (
    open_stats_store,
)


class ProfilesMixin:
    """Профили серверов, периоды статистики, резервные копии и очистка."""

    def update_dates_label(self, force=False):
        lbl = getattr(self, "dates_label", None)
        if lbl is None:
            return
        sig = (id(self.log_store), self.log_store.version)
        if sig == self._dates_sig and not force:
            return
        self._dates_sig = sig
        cur = self.log_store.current_wipe()
        start = fmt_date(datetime.date.fromtimestamp(cur["start"])) if cur and cur["start"] > 0 else "—"
        bounds = self.log_store.date_bounds()
        first = fmt_date(bounds[0]) if bounds else "—"
        lbl.setText(f"Текущий вайп с {start}   ·   Статистика с {first}")
        lbl.setToolTip("Начало текущего вайпа — когда программа впервые увидела его сид.\n"
                       "Начало статистики — самое раннее событие в базе (логи или консоль).")

    # ---------- профили серверов ----------
    def profile_name(self):
        for p in self.cfg.get("profiles") or []:
            if p["id"] == self.cfg.get("active_profile"):
                return p["name"]
        return "Сервер"

    def fill_profiles(self):
        cb = self.profile_cb
        cb.blockSignals(True)
        cb.clear()
        for p in self.cfg.get("profiles") or []:
            cb.addItem(p["name"], p["id"])
        cb.setCurrentIndex(max(0, cb.findData(self.cfg.get("active_profile"))))
        cb.blockSignals(False)
        self.setWindowTitle(f"Rust Live Map — {self.profile_name()}")

    def on_profile_selected(self, i):
        pid = self.profile_cb.itemData(i)
        if pid and pid != self.cfg.get("active_profile"):
            self.switch_profile(pid)

    def switch_profile(self, pid):
        self.stop_rcon()
        save_config(self.cfg)            # текущие серверные настройки -> в запись старого профиля
        self.log_store.close()
        load_profile(self.cfg, pid)
        set_active_profile(pid)
        save_config(self.cfg)
        self.open_profile_state()

    def open_profile_state(self):
        # всё, что зависит от профиля: БД статистики, карта, позиции, шкафы, чат
        for d in list(self.team_dialogs.values()):       # окна команд старого сервера
            try:
                d.close()
            except RuntimeError:
                pass
        self.team_dialogs.clear()
        self._teams_sig = None
        self.log_store = open_stats_store(self.cfg)
        self.log_store.wipe_cb = lambda wid, seed, world: self.wipe_changed.emit(int(wid), str(seed), int(world or 0))
        self.players, self.bases = [], []
        self.selected = self.selected_base = None
        self._html_cache.clear()
        self._period_sig = None
        self._map_prompted = False
        self._host_warned = False
        self.archive_wipe = None
        self._arch_seed = self._arch_prev = None
        self._heat_sig = None
        self._all_marks_sig = None
        self.player_heat = None
        self.chat_view.clear()
        self.clear_console()
        self._search_token += 1
        self.chat_search.clear()
        self.load_chat_history()
        self.load_profile_map()
        self.fill_profiles()
        self.fill_periods(True)
        self.sync_map_to_period()
        self.on_players([])
        self.on_bases([])
        self.refresh_server_tab()
        self.logs_label.setText("Статистика: —")
        self.start_rcon()
        QTimer.singleShot(300, self.auto_load_map)
        QTimer.singleShot(2500, self.check_long_wipes)

    def add_profile(self):
        name, ok = QInputDialog.getText(self, "Новый сервер", "Название сервера:")
        name = name.strip()
        if not ok or not name:
            return
        save_config(self.cfg)
        p = new_profile(name, {"world_size": self.cfg["world_size"]})
        self.cfg["profiles"].append(p)
        self.switch_profile(p["id"])
        self.open_settings(ask_folder=True)   # сразу просим адрес и пароль RCON и (необязательно) папку сервера

    def rename_profile(self):
        name, ok = QInputDialog.getText(self, "Переименовать сервер", "Название:", text=self.profile_name())
        name = name.strip()
        if not ok or not name:
            return
        for p in self.cfg["profiles"]:
            if p["id"] == self.cfg["active_profile"]:
                p["name"] = name
        save_config(self.cfg)
        self.fill_profiles()

    def delete_profile(self):
        profs = self.cfg.get("profiles") or []
        if len(profs) <= 1:
            QMessageBox.information(self, "Удаление сервера", "Нельзя удалить единственный сервер.")
            return
        name = self.profile_name()
        r = QMessageBox.question(
            self, "Удалить сервер",
            f"Убрать сервер «{name}» из списка?\n\n"
            "Его папка (статистика, карта, резервные копии) не стирается, а переносится в data/archive.")
        if r != QMessageBox.StandardButton.Yes:
            return
        pid = self.cfg["active_profile"]
        self.stop_rcon()
        self.log_store.close()
        try:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            if pdir().exists():
                shutil.move(str(pdir()), str(ARCHIVE_DIR / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"))
        except OSError as e:
            QMessageBox.warning(self, "Архив", f"Не удалось перенести папку сервера: {e}")
        self.cfg["profiles"] = [p for p in profs if p["id"] != pid]
        nxt = self.cfg["profiles"][0]["id"]
        load_profile(self.cfg, nxt)
        set_active_profile(nxt)
        save_config(self.cfg)
        self.open_profile_state()

    # ---------- период статистики ----------
    def fill_periods(self, force=False):
        if force:
            self.update_dates_label(True)
        per = self.log_store.period
        cur = self.log_store.current_wipe()
        counts = self.log_store.wipe_event_counts()
        past = [w for w in reversed(self.log_store.wipes)
                if (w["seed"] or counts.get(w["id"])) and (not cur or w["id"] != cur["id"])]
        sig = (per, tuple(w["id"] for w in past))
        if sig == self._period_sig and not force:
            return
        self._period_sig = sig
        cb = self.period_cb
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("Текущий вайп", "current")
        for w in past:
            start = self.log_store.wipe_start_label(w)
            mark = " 📁" if archive_map_path(w, self.cfg["world_size"]) else ""
            cb.addItem(f"Вайп {start}{mark}", f"wipe:{w['id']}")
        cb.addItem("За всё время", "all")
        if is_range_period(per):
            cb.addItem("📅 " + self.log_store.period_label(), "range")
        cb.addItem("📅 Выбрать даты…", "pick")
        if per is None:
            key = "all"
        elif is_range_period(per):
            key = "range"
        elif is_wipe_period(per):
            key = f"wipe:{per[1]}"
        else:
            key = "current"
        i = cb.findData(key)
        cb.setCurrentIndex(i if i >= 0 else 0)
        cb.blockSignals(False)

    def pick_dates(self):
        bounds = self.log_store.date_bounds()
        if not bounds:
            QMessageBox.information(self, "Период", "В базе пока нет событий. Загрузите историю из логов "
                                                    "или подождите, пока накопится статистика.")
            return False
        cur = None
        per = self.log_store.period
        if is_range_period(per):
            cur = (datetime.date.fromtimestamp(per[1]), datetime.date.fromtimestamp(per[2] - 1))
        dlg = DateRangeDialog(bounds, cur, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        d1, d2 = dlg.dates()
        t1 = datetime.datetime.combine(d1, datetime.time()).timestamp()
        t2 = datetime.datetime.combine(d2 + datetime.timedelta(days=1), datetime.time()).timestamp()
        self.log_store.set_period(("range", t1, t2))
        return True

    def on_period_changed(self, i):
        data = self.period_cb.itemData(i)
        if data == "wipes":
            self.manage_wipes()        # период не меняется
            return
        if data == "pick":
            self.pick_dates()          # при отмене останется прежний период
        elif data == "all":
            self.log_store.set_period(None)
        elif data == "current":
            self.log_store.set_period("current")
        elif isinstance(data, str) and data.startswith("wipe:"):
            self.log_store.set_period(("wipe", int(data[5:])))
        self._period_changed()

    def _period_changed(self):
        self._period_sig = None
        self.fill_periods(True)
        self._html_cache.clear()
        self.sync_map_to_period()
        self.refresh_heat(True)
        self.refresh_details()
        self.refresh_server_tab()

    def step_period(self, direction):
        # ↑ (direction=-1) — период раньше, ↓ (+1) — позже; за границы данных не уходим
        per = self.log_store.period
        if not is_range_period(per):
            return
        d1 = datetime.date.fromtimestamp(per[1])
        d2 = datetime.date.fromtimestamp(per[2] - 1)
        nd1, nd2 = shift_range(d1, d2, direction)
        bounds = self.log_store.date_bounds()
        if not bounds or nd2 < bounds[0] or nd1 > max(bounds[1], datetime.date.today()):
            return
        t1 = datetime.datetime.combine(nd1, datetime.time()).timestamp()
        t2 = datetime.datetime.combine(nd2 + datetime.timedelta(days=1), datetime.time()).timestamp()
        self.log_store.set_period(("range", t1, t2))
        self._period_changed()

    def backup_now(self):
        try:
            dst = self.log_store.backup("manual")
            QMessageBox.information(self, "Резервная копия", f"Сохранено:\n{dst}")
        except Exception as e:
            QMessageBox.warning(self, "Резервная копия", f"Не удалось: {e}")

    def clear_stats(self, whole):
        if whole:
            name = self.profile_name()
            text, ok = QInputDialog.getText(
                self, "Очистить всю статистику",
                f"Будет удалена ВСЯ статистика сервера «{name}» (все вайпы).\n"
                "Перед этим автоматически сделается резервная копия.\n\n"
                "Для подтверждения введите название сервера:")
            if not ok or text.strip() != name:
                return
            wid = None
        else:
            ids = self.log_store._scope_ids()
            if not ids:
                QMessageBox.information(self, "Очистка",
                                        "Очистка доступна для периода «Текущий вайп» — выберите его в списке.")
                return
            wid = ids[0]
            r = QMessageBox.question(
                self, "Очистить вайп",
                "Очистить статистику текущего вайпа?\n\n"
                "Резервная копия создаётся автоматически.")
            if r != QMessageBox.StandardButton.Yes:
                return
        self.log_store.clear(wid)
        self._html_cache.clear()
        self.fill_periods(True)
        self.refresh_details()
        self.refresh_server_tab()
        self.logs_label.setText("Статистика очищена")

    def on_stats_tick(self):
        self._tick += 1
        live = bool(self.chat_worker and self.chat_worker.isRunning() and self.cfg.get("live_stats", True))
        self.logs_label.setText(f"Статистика: {self.log_store.n_players()} игроков" + (" · live" if live else ""))
        self.check_map_seed()
        self.fill_periods()
        self.update_dates_label()
        self.log_store.heat_flush()
        self.refresh_heat()
        self.update_event_marks()
        if self.tabs.currentWidget() is self.server_view and self._tick % 2 == 0:
            self.refresh_server_tab()
