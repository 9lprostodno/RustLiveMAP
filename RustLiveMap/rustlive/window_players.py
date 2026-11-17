"""Rust Live Map: модуль window_players."""

import html
import time
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QTableWidgetItem

from .config import (
    save_config,
)
from .events import (
    CAT_STYLE,
    event_info,
)
from .gamedata import (
    grid_ref,
    team_color,
)
from .periods import (
    is_range_period,
)


class PlayersMixin:
    """Игроки, базы, выбор объектов, метки событий на карте."""

    # ---------- данные игроков ----------
    def on_players(self, players):
        # Некоторые сборки сервера отдают Health = 0 у всех. Тогда HP показывать нельзя.
        if not getattr(self, "_world_synced", True):
            self._world_synced = True
            self.sync_world_size()                  # размер мира — как на сервере (server.worldsize)
        online_list = players
        hp_broken = bool(online_list) and all((p["health"] or 0) == 0 for p in online_list)
        if hp_broken:
            for p in online_list:
                p["health"] = None
        self.players = players
        online = len(online_list)
        hp_note = " | HP: сервер отдаёт 0" if hp_broken else ""
        self.a_hp.setText("HP (нет данных)" if hp_broken else "HP")
        self.a_hp.setToolTip("Сервер отдаёт Health = 0 у всех игроков, показывать нечего.\n"
                             "Нужна команда с реальным HP (например, из плагина LiveMapData.cs)."
                             if hp_broken else "Показывать HP над игроками")
        self._hp_note = hp_note
        self.update_online_label()
        self.tabs.setTabText(0, f"Игроки ({online})")
        if self.cfg.get("heat_enabled", True) and online_list:
            self.log_store.heat_sample(time.time(), online_list, self.cfg["world_size"])
        self.refresh_markers()
        self.refresh_table()
        self.refresh_all_table()
        self.update_team_targets()
        self.refresh_teams()
        self.refresh_details()

    def on_toggle(self):
        self.cfg["show_names"] = self.a_names.isChecked()
        self.cfg["show_hp"] = self.a_hp.isChecked()
        self.cfg["show_bases"] = self.a_bases.isChecked()
        self.cfg["show_players"] = self.a_players.isChecked()
        self.cfg["show_grid"] = self.a_grid.isChecked()
        self.cfg["show_marks"] = self.a_marks.isChecked()
        save_config(self.cfg)
        self.view.grid_visible = self.a_grid.isChecked()
        self.view.rebuild_grid()
        self.refresh_markers()
        self.refresh_details()

    def visible_players(self):
        return [] if self.archive_wipe is not None or not self.a_players.isChecked() else self.players

    def refresh_markers(self):
        self.view.set_players(self.visible_players(), self.a_names.isChecked(),
                              self.a_hp.isChecked(), self.selected)
        self.view.set_bases([] if self.archive_wipe is not None else self.bases,
                            self.a_bases.isChecked(), self.selected_base)

    def refresh_table(self):
        q = self.search.text().strip().casefold()
        rows = [p for p in self.players if not q or q in p["name"].casefold()]
        rows.sort(key=lambda p: p["name"].lower())
        sig = (tuple((p["steam_id"], p["name"], p["health"], p.get("team")) for p in rows), self.selected)
        if sig == self._table_sig:
            return
        self._table_sig = sig

        sb = self.table.verticalScrollBar()
        scroll = sb.value()
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            hp = f"{p['health']:.0f}" if p["health"] is not None else "—"
            vals = [p["name"], hp, str(p["team"]) if p.get("team") else "—"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, p["steam_id"])
                if c == 2 and p.get("team"):
                    item.setForeground(QBrush(QColor(team_color(p["team"]))))
                self.table.setItem(r, c, item)
            if p["steam_id"] == self.selected:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        sb.setValue(scroll)  # selectRow мог прокрутить список — возвращаем как было

    def refresh_all_table(self):
        if self.tabs.currentWidget() is not self.all_tab:
            return
        q = self.all_search.text().strip().casefold()
        online = {p["steam_id"] for p in self.players}
        allp = self.log_store.all_players()
        sig = (self.log_store.version, self.log_store.period, q, frozenset(online), self.selected)
        if sig == self._all_sig:
            return
        self._all_sig = sig
        rows = sorted((r for r in allp if not q or q in r[1].casefold() or q in r[0]),
                      key=lambda r: r[1].casefold())
        self.tabs.setTabText(self.tabs.indexOf(self.all_tab), f"Все игроки ({len(allp)})")
        sb = self.all_table.verticalScrollBar()
        scroll = sb.value()
        self.all_table.blockSignals(True)
        self.all_table.clearSelection()
        self.all_table.setRowCount(len(rows))
        for r, (sid, name, last) in enumerate(rows):
            vals = [name, time.strftime("%d.%m %H:%M", time.localtime(last)) if last else "—",
                    "●" if sid in online else ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, sid)
                if c == 2:
                    item.setForeground(QBrush(QColor("#39d353")))
                self.all_table.setItem(r, c, item)
            if sid == self.selected:
                self.all_table.selectRow(r)
        self.all_table.blockSignals(False)
        sb.setValue(scroll)

    def on_tab_changed(self, _i):
        w = self.tabs.currentWidget()
        if w is self.server_view:
            self.refresh_server_tab()
        elif w is self.all_tab:
            self._all_sig = None
            self.refresh_all_table()
        elif w is self.teams_table:
            self._teams_sig = None
            self.refresh_teams()
        if self.player_heat and w not in (self.players_tab, self.all_tab):
            self.stop_player_heat()       # ушли на другую вкладку панели — карту передвижений убираем

    def find_player(self, sid):
        for p in self.players:
            if p["steam_id"] == sid:
                return p
        return None

    def on_event_clicked(self, idx):
        # клик по значку на карте -> строка события на вкладке «Убийства / смерти»
        sid = self.selected
        if not sid:
            # метки всех игроков: клик выбирает игрока, к которому относится событие
            owner = self._all_owners.get(idx)
            if owner:
                self.select_player(owner)
            return
        self._hl_event = (sid, idx)
        self.panel_dock.show()
        self.card_tabs.setCurrentIndex(1)
        html_text = self.log_events_html(sid)
        self._html_cache[self.events_view] = html_text
        self.events_view.setHtml(html_text)
        self.events_view.scrollToAnchor(f"ev{idx}")

    def clear_selection(self):
        self.stop_player_heat()        # клик по пустому месту карты снимает и карту передвижений игрока
        if self.selected is None and self.selected_base is None:
            return
        self._hl_event = None
        self.selected = None
        self.selected_base = None
        self.table.clearSelection()
        self.all_table.clearSelection()
        self.bases_table.clearSelection()
        self._table_sig = None
        self._all_sig = None
        self.refresh_markers()
        self.refresh_table()
        self.refresh_all_table()
        self.refresh_bases_table()
        self.refresh_details()

    def select_player(self, sid):
        self._hl_event = None
        if sid != self.player_heat:
            self.stop_player_heat()
        self.selected = sid
        self.selected_base = None
        self.bases_table.clearSelection()
        if self.tabs.currentWidget() not in (self.players_tab, self.all_tab):
            self.tabs.setCurrentWidget(self.players_tab)
        self.refresh_markers()
        self.refresh_details()

    def select_base(self, eid):
        self.stop_player_heat()
        self.selected_base = int(eid)
        self.selected = None
        self.tabs.setCurrentWidget(self.bases_table)
        self.refresh_markers()
        self.refresh_bases_table()
        self.refresh_details()

    def center_on_base_row(self, row, _col):
        eid = int(self.bases_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        self.select_base(eid)
        for b in self.bases:
            if b["entity_id"] == eid:
                self.view.center_on_world(b["x"], b["z"])
                break

    # ---------- базы ----------
    def on_bases(self, bases):
        self.bases = bases
        self.bases_label.setText(f"Базы (TC): {len(bases)}")
        self.tabs.setTabText(self.tabs.indexOf(self.bases_table), f"Базы ({len(bases)})")
        self.refresh_bases_table()
        self.refresh_markers()
        if self.selected_base is not None:
            self.refresh_details()

    def refresh_bases_table(self):
        rows = sorted(self.bases, key=lambda b: (-b["authed"], b["entity_id"]))
        t = self.bases_table
        sb = t.verticalScrollBar()
        scroll = sb.value()
        t.blockSignals(True)
        t.setRowCount(len(rows))
        for r, b in enumerate(rows):
            vals = [str(b["entity_id"]), grid_ref(b["x"], b["z"], self.view.world_size),
                    str(b["authed"]), f"{b['y']:.0f}"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(b["entity_id"]))
                t.setItem(r, c, item)
            if b["entity_id"] == self.selected_base:
                t.selectRow(r)
        t.blockSignals(False)
        sb.setValue(scroll)

    def show_base_details(self):
        b = next((x for x in self.bases if x["entity_id"] == self.selected_base), None)
        self.hp_bar.setValue(0)
        self._set_html(self.events_view, "<i style='color:#999'>Для баз событий нет</i>")
        if not b:
            self.hp_bar.setFormat("—")
            self._set_html(self.details,
                           "<i style='color:#999'>Шкаф не найден (снесён или список ещё не обновился)</i>")
            return
        self.hp_bar.setFormat(f"Шкаф (TC) · авториз.: {b['authed']}")
        esc = html.escape
        gray = "color:#999"

        rows = [
            ("Entity ID", b["entity_id"]),
            ("Позиция (X, Y, Z)", f"{b['x']:.1f}, {b['y']:.1f}, {b['z']:.1f}"),
            ("Квадрат", grid_ref(b["x"], b["z"], self.view.world_size)),
            ("Авторизовано в шкафу", b["authed"]),
        ]
        if b["y"] < -5:
            rows.append(("Высота", "ниже уровня моря (подземная/подводная?)"))
        body = "".join(
            f"<tr><td style='{gray};padding:3px 10px 3px 0'>{esc(str(k))}</td><td>{esc(str(v))}</td></tr>"
            for k, v in rows)
        self._set_html(self.details,
                       f"<h3 style='margin:0 0 6px 0'>Шкаф (база)</h3><table>{body}</table>"
                       "<p style='color:#777;font-size:11px'>RCON отдаёт только число авторизованных, "
                       "не их имена.</p>")

    def player_menu(self, pos, table=None):
        table = table or self.table
        item = table.itemAt(pos)
        if item is None:
            return
        first = table.item(item.row(), 0)
        sid = str(first.data(Qt.ItemDataRole.UserRole))
        name = first.text()
        p = self.find_player(sid)
        menu = QMenu(self)
        if p and p["x"] is not None:
            menu.addAction("📍 Показать на карте", lambda: self.center_on_sid(sid))
        if self.player_heat == sid:
            menu.addAction("🔥 Скрыть передвижения", self.stop_player_heat)
        else:
            menu.addAction("🔥 Показать передвижения", lambda: self.show_player_heat(sid))
        if p:
            self._add_teleport_menu(menu, p)
        menu.addAction("📋 Копировать ник", lambda: QApplication.clipboard().setText(name))
        menu.addAction("📋 Копировать SteamID", lambda: QApplication.clipboard().setText(sid))
        menu.addAction("🌐 Профиль в Steam",
                       lambda: QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/profiles/{sid}")))
        menu.exec(table.viewport().mapToGlobal(pos))

    def _tp_arg(self, p):
        """Как назвать игрока в teleport: по нику в кавычках; если ник неоднозначный или с кавычкой — по SteamID."""
        nm = str(p.get("name") or "")
        dup = sum(1 for q in self.players if q.get("name") == nm) > 1
        return f'"{nm}"' if nm and '"' not in nm and not dup else f'"{p["steam_id"]}"'

    def _add_teleport_menu(self, menu, p):
        """«Телепортировать к…»: при наведении — список остальных игроков онлайн; выбранный игрок — цель."""
        others = sorted((q for q in self.players if q["steam_id"] != p["steam_id"]),
                        key=lambda q: str(q.get("name") or "").casefold())
        if self.archive_wipe is not None:
            return
        if not others:                                   # пункт виден всегда, чтобы было понятно, почему он недоступен
            menu.addAction("🚀 Телепортировать к… (других игроков онлайн нет)").setEnabled(False)
            return
        sub = menu.addMenu("🚀 Телепортировать к…")
        sub.setStyleSheet("QMenu { menu-scrollable: 1; }")        # длинный список листается
        for q in others:
            sub.addAction(str(q.get("name") or q["steam_id"]), lambda q=q: self.teleport_player(p, q))

    def teleport_player(self, who, to):
        if not (self.chat_worker and self.chat_worker.isRunning()):
            self.on_console_lines([(time.time(), "Error", "Нет подключения к консоли сервера — команда не отправлена")])
            return
        cmd = f"teleport {self._tp_arg(who)} {self._tp_arg(to)}"
        self.on_console_lines([(time.time(), "Cmd", cmd)])
        self.chat_worker.send_command(cmd)

    def center_on_sid(self, sid):
        p = self.find_player(sid)
        self.select_player(sid)
        if p and p["x"] is not None:
            self.view.center_on_world(p["x"], p["z"])

    def center_on_row(self, row, _col):
        self.center_on_sid(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def marks_cats(self):
        return {c for c, a in self.cat_actions.items() if a.isChecked()}

    def on_marks_cats(self):
        self.cfg["marks_cats"] = sorted(self.marks_cats())
        save_config(self.cfg)
        self._all_marks_sig = None
        self.update_event_marks()

    def update_event_marks(self):
        marks = []
        shown = self.archive_wipe is not None or not self.map_stale
        if self.a_marks.isChecked() and shown and self.view.pix_item:
            sid = self.selected if self.selected_base is None else None
            if sid:
                marks = self._player_marks(sid)
            else:
                marks = self._all_event_marks()
        self.view.set_event_marks(marks)

    def _player_marks(self, sid):
        marks = []
        allowed = self.marks_cats()
        st = self.log_store.get(sid)
        if st:
            evs = st["events"]
            base_i = max(0, len(evs) - 40)
            for i, (ts, kind, oname, _osid, x, z, extra) in enumerate(evs[base_i:], base_i):
                if x is None or z is None:
                    continue
                cat, label = event_info(kind, oname, extra)
                if cat not in allowed:
                    continue
                glyph, color = CAT_STYLE[cat]
                if kind == "kill":
                    color = "#39d353"
                who = f" {oname}" if kind in ("kill", "death") else ""
                when = time.strftime("%d.%m %H:%M", time.localtime(ts))
                marks.append((x, z, glyph, color, f"{when} · {label}{who}", i))
        return marks

    def _all_event_marks(self):
        # игрок не выбран: места смертей всех игроков за период на показанной карте
        wipe = self.archive_wipe or self.log_store.current_wipe()
        if not wipe:
            return []
        per = self.log_store.period
        t1, t2 = (per[1], per[2]) if is_range_period(per) else (None, None)
        allowed = self.marks_cats()
        sig = (wipe["id"], t1, t2, self.log_store.version, tuple(sorted(allowed)))
        if sig == self._all_marks_sig:
            return self._all_marks_cache
        marks, owners = [], {}
        for i, (ts, cat, a, an, b, bn, x, z, cause) in enumerate(self.log_store.map_events(wipe["id"], t1, t2)):
            who_v = an or a
            when = time.strftime("%d.%m %H:%M", time.localtime(ts))
            kcat = "pvp" if cat == "player" else "self" if cat in ("suicide", "env") else "pve"
            if kcat not in allowed:
                continue
            if cat == "player":
                glyph, color = CAT_STYLE["pvp"]
                color = "#ff4d4d"
                tip = f"{when} · {bn or b} убил {who_v}"
                owners[i] = b or a
            else:
                if cat == "suicide":
                    ecat, label = event_info("suicide", "", "")
                elif cat == "env":
                    ecat, label = event_info("env", "", cause)
                else:
                    ecat, label = event_info(cat, cause, "")
                glyph, color = CAT_STYLE[ecat]
                tip = f"{when} · {who_v}: {label}"
                owners[i] = a
            marks.append((x, z, glyph, color, tip, i))
        self._all_marks_sig, self._all_marks_cache, self._all_owners = sig, marks, owners
        return marks
