"""Rust Live Map: модуль window_status."""

import time
from PySide6.QtWidgets import QDialog, QMessageBox

from .config import (
    save_config,
)
from .dialogs import (
    SettingsDialog,
)
from .workers import (
    BasesWorker,
    ConsoleWorker,
    RconWorker,
    TeamsWorker,
    fmt_mem,
    fmt_uptime,
)


class StatusMixin:
    """Подключение к серверу, плашки состояния, настройки."""

    def on_server_time(self, hours):
        h = int(hours) % 24
        m = int((hours - int(hours)) * 60)
        icon = "☀" if 6 <= hours < 19 else "🌙"
        self.time_label.setText(f"{icon} {h:02d}:{m:02d}")

    # ---------- RCON ----------
    def open_settings(self, ask_folder=False):
        dlg = SettingsDialog(self.cfg, self, ask_folder)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply(self.cfg)
            save_config(self.cfg)
            self.view.set_world_size(self.cfg["world_size"])
            self.refresh_markers()
            if self.rcon:
                self.stop_rcon()
            self.start_rcon()
            self.auto_load_map()

    def toggle_rcon(self):
        if self.rcon:
            self.stop_rcon()
        else:
            self.start_rcon()

    def rcon_configured(self):
        return bool(str(self.cfg.get("rcon_host") or "").strip() and self.cfg.get("rcon_password"))

    def start_rcon(self):
        self.stop_rcon()
        if not self.rcon_configured():      # чистый запуск: не долбим сеть без настроек
            self.rcon_label.setText("RCON: не настроен — откройте «⚙ Настройки» и укажите адрес и пароль")
            self.rcon_label.setStyleSheet("color: #f0a030;")
            return

        self._world_synced = False
        self.rcon = RconWorker(self.cfg)
        self.rcon.players_updated.connect(self.on_players)
        self.rcon.time_updated.connect(self.on_server_time)
        self.rcon.info_updated.connect(self.on_server_info)
        self.rcon.status_changed.connect(self.on_rcon_status)
        self.rcon.start()

        if float(self.cfg.get("bases_interval") or 0) > 0:
            self.bases_worker = BasesWorker(self.cfg)
            self.bases_worker.bases_updated.connect(self.on_bases)
            self.bases_worker.status_changed.connect(self.bases_label.setText)
            self.bases_worker.start()
        else:
            self.on_bases([])
            self.bases_label.setText("Базы (TC): выкл.")

        if float(self.cfg.get("teams_interval") or 0) > 0:
            self.teams_worker = TeamsWorker(self.cfg)
            self.teams_worker.team_info.connect(self.on_team_info)
            self.teams_worker.start()

        if self.cfg.get("chat_enabled", True) or self.cfg.get("live_stats", True):
            self.chat_worker = ConsoleWorker(self.cfg, self.log_store)
            self.chat_worker.chat_received.connect(self.on_chat)
            self.chat_worker.console_lines.connect(self.on_console_lines)
            self.chat_worker.status_changed.connect(self.on_chat_status)
            self.chat_worker.host_seen.connect(self.on_host_seen)
            self.chat_worker.start()

        self.a_conn.setText("⏹ Отключиться")

    def stop_rcon(self):
        workers = [w for w in (self.rcon, self.bases_worker, self.teams_worker, self.chat_worker) if w]
        for w in workers:   # сначала сигнал остановки всем, потом ожидание — быстрее закрытие
            w.stop()
        for w in workers:
            w.wait(4000)
        self.rcon = None
        self.bases_worker = None
        self.teams_worker = None
        self.chat_worker = None
        self.a_conn.setText("🔌 Подключиться")

    def reposition_hud(self):
        vp = self.view.viewport()
        self.hud.adjustSize()
        self.hud.move(max(8, vp.width() - self.hud.width() - 12), 10)
        self.heat_label.move(12, max(8, vp.height() - self.heat_label.height() - 12))

    def on_server_info(self, info):
        """serverinfo раз в ~12 с: FPS, аптайм, память справа в строке статуса, остальное — в подсказке."""
        prev = self._srv_info
        self._srv_info = dict(info, _at=time.time())
        mem = float(info.get("Memory") or 0)
        up = int(info.get("Uptime") or 0)
        if self._mem_base is None or (prev and up < int(prev.get("Uptime") or 0)):
            self._mem_base = mem or None             # первый замер или рестарт сервера — точка отсчёта
        fps = float(info.get("Framerate") or 0)
        fc = "#ff6b6b" if fps < 30 else "#f0c040" if fps < 60 else "#39d353"
        grown = bool(self._mem_base) and mem > self._mem_base * 1.3 and mem - self._mem_base > 300
        mc = "#ff6b6b" if grown else "#cccccc"
        self.info_label.setText(
            f"<span style='color:{fc}'>🖥 {fps:.0f} FPS</span> &nbsp;·&nbsp; ⏱ {fmt_uptime(up)} &nbsp;·&nbsp; "
            f"<span style='color:{mc}'>🧠 {fmt_mem(mem)}</span>&nbsp;")
        self.info_label.show()
        self.info_label.setToolTip(
            f"{info.get('Hostname') or ''} · версия {info.get('Version', '?')}\n"
            f"Объектов на карте: {int(info.get('EntityCount') or 0):,}".replace(",", " ") + "\n"
            f"Сеть: ↓ {float(info.get('NetworkIn') or 0) / 1024:.1f} КБ/с · "
            f"↑ {float(info.get('NetworkOut') or 0) / 1024:.1f} КБ/с\n"
            f"Сохранение создано: {info.get('SaveCreatedTime', '—')}\n"
            f"Обновлено {time.strftime('%H:%M:%S')}")
        self.update_online_label()

    def clear_server_info(self):
        self._srv_info = None
        self._mem_base = None
        self.info_label.setText("")
        self.info_label.setToolTip("")
        self.info_label.hide()
        self.reposition_hud()

    def update_online_label(self):
        n = len(self.players)
        info = self._srv_info
        mx = f"/{int(info['MaxPlayers'])}" if info and info.get("MaxPlayers") else ""
        extra = ""
        if info:
            q, j = int(info.get("Queued") or 0), int(info.get("Joining") or 0)
            if q:
                extra += f" (+{q} в очереди)"
            if j:
                extra += f" (+{j} входят)"
        self.count_label.setText(f"Online: {n}{mx}{extra}{self._hp_note}")
        self.reposition_hud()

    def on_rcon_status(self, text, ok):
        self.rcon_label.setText(text)
        if not ok:
            self.time_label.setText("")
            self.clear_server_info()
        self.rcon_label.setStyleSheet("color: #39d353;" if ok else "color: #f0a030;")

    def on_host_seen(self, host):
        # отпечаток сервера: если hostname изменился, возможно, подключились не к тому серверу
        old = self.log_store.get_meta("hostname")
        if not old:
            self.log_store.set_meta("hostname", host)
            return
        if old == host or self._host_warned:
            return
        self._host_warned = True
        box = QMessageBox(QMessageBox.Icon.Warning, "Другой сервер?",
                          f"Имя сервера изменилось:\n  было: {old}\n  стало: {host}\n\n"
                          "Если это другой сервер, отключитесь и создайте для него отдельный профиль, "
                          "иначе статистика смешается.", parent=self)
        same = box.addButton("Это тот же сервер", QMessageBox.ButtonRole.AcceptRole)
        stop = box.addButton("Отключиться", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is stop:
            self.stop_rcon()
        elif box.clickedButton() is same:
            self.log_store.set_meta("hostname", host)
