"""Rust Live Map: вкладка «Команды» и окно команды."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from .dialogs_teams import TeamDialog
from .gamedata import team_color


class TeamsMixin:
    """Составы команд из global.teaminfo; цвет команды — как у игроков на карте (по ID)."""

    def build_teams_tab(self):
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Команда", "Лидер", "Онлайн", "Состав"])
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.verticalHeader().setVisible(False)
        h = t.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        t.setAlternatingRowColors(True)
        t.cellDoubleClicked.connect(lambda r, _c: self.open_team(t.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        self.teams_table = t
        return t

    def online_sids(self):
        return set() if self.archive_wipe is not None else {p["steam_id"] for p in self.players}

    def online_sids_sig(self):
        return frozenset(self.online_sids())

    def _teams_wipe_id(self):
        w = self.archive_wipe or self.log_store.current_wipe()
        return w["id"] if w else None

    def update_team_targets(self):
        """Кого спрашивать global.teaminfo: по одному игроку онлайн от каждой команды из playerlist."""
        w = self.teams_worker
        if not w:
            return
        groups = {}
        for p in self.players:
            if p.get("team"):
                groups.setdefault(int(p["team"]), []).append(p["steam_id"])
        w.set_targets({t: (sids[0], frozenset(sids)) for t, sids in groups.items()})

    def on_team_info(self, info):
        try:
            self.log_store.team_snapshot(info["ts"], info)
        except Exception as e:
            print(f"Команды: {e}")
            return
        self.refresh_teams()

    def refresh_teams(self):
        wid = self._teams_wipe_id()
        if wid is None:
            return
        online = self.online_sids()
        sig = (self.log_store.team_version, wid, frozenset(online))
        if sig == self._teams_sig:
            return
        self._teams_sig = sig
        st = self.log_store
        state = st.teams_state(wid)
        rows = []
        for team, v in state.items():
            sids = [s for s, _l in v["members"]]
            n_on = sum(1 for s in sids if s in online)
            leader = next((s for s, lead in v["members"] if lead), None)
            rows.append((-n_on, -len(sids), team, sids, leader, n_on))
        rows.sort()
        self.tabs.setTabText(self.tabs.indexOf(self.teams_table), f"Команды ({len(rows)})")
        sb = self.teams_table.verticalScrollBar()
        scroll = sb.value()
        self.teams_table.setRowCount(len(rows))
        for r, (_a, _b, team, sids, leader, n_on) in enumerate(rows):
            names = ", ".join(st.name_of(s) for s in sids)
            vals = [f"● {team}", st.name_of(leader) if leader else "—", f"{n_on}/{len(sids)}", names]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, team)
                    it.setForeground(QBrush(QColor(team_color(team))))
                elif c == 2 and n_on:
                    it.setForeground(QBrush(QColor("#39d353")))
                if c == 3:
                    it.setToolTip(names)
                self.teams_table.setItem(r, c, it)
        sb.setValue(scroll)

    def open_team(self, team):
        wid = self._teams_wipe_id()
        if team is None or wid is None:
            return
        d = self.team_dialogs.get(int(team))
        if d is not None:
            try:
                if d.isVisible() and d.wipe_id == wid:
                    d.raise_()
                    d.activateWindow()
                    return
            except RuntimeError:
                pass
        d = TeamDialog(self, int(team), wid)
        self.team_dialogs[int(team)] = d
        d.show()
        if self.teams_worker:                         # освежить состав (в том числе офлайн-участников)
            v = self.log_store.teams_state(wid).get(int(team))
            if v and v["members"]:
                self.teams_worker.ask(v["members"][0][0])
