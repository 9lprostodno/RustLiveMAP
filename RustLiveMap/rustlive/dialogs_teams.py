"""Rust Live Map: окно команды — слева состав, справа чат команды с историей."""

import html
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHeaderView, QLabel, QSplitter, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget,
)

from .gamedata import team_color


class TeamDialog(QDialog):
    """Отдельное неблокирующее окно. Данные берёт из базы статистики главного окна и обновляется само."""

    def __init__(self, win, team, wipe_id):
        super().__init__(None)
        self.win, self.team, self.wipe_id = win, int(team), wipe_id
        self.color = team_color(self.team)
        self.setWindowTitle(f"Команда {self.team}")
        self.resize(900, 520)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.head = QLabel()
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Участник", "Статус"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._center)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.head)
        ll.addWidget(self.table)

        self.chat = QTextBrowser()
        self.chat.setOpenLinks(False)
        title = QLabel("Чат команды")
        title.setStyleSheet("color:#999")
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(title)
        rl.addWidget(self.chat)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([280, 620])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self._sig = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2500)
        self.refresh()

    def _center(self, r, _c):
        it = self.table.item(r, 0)
        if it is not None and hasattr(self.win, "center_on_sid"):
            self.win.center_on_sid(it.data(Qt.ItemDataRole.UserRole))

    def refresh(self):
        st = self.win.log_store
        sig = (st.team_version, self.win.online_sids_sig())
        if sig == self._sig:
            return
        self._sig = sig
        info = st.teams_state(self.wipe_id).get(self.team)
        members = info["members"] if info else []
        online = self.win.online_sids()
        self.head.setText(f"<b style='color:{self.color}'>● Команда {self.team}</b> · участников: {len(members)}, "
                          f"онлайн: {sum(1 for s, _l in members if s in online)}")
        self.table.setRowCount(len(members))
        for r, (sid, leader) in enumerate(members):
            on = sid in online
            name = st.name_of(sid) + ("  👑" if leader else "")
            a = QTableWidgetItem(name)
            a.setData(Qt.ItemDataRole.UserRole, sid)
            a.setToolTip(sid)
            b = QTableWidgetItem("● онлайн" if on else "офлайн")
            b.setForeground(QBrush(QColor("#39d353" if on else "#888888")))
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, b)
        sb = self.chat.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        rows = st.team_chat(self.wipe_id, self.team)
        out, day = [], None
        for ts, sid, name, text in rows:
            d = time.strftime("%d.%m.%Y", time.localtime(ts))
            if d != day:
                day = d
                out.append(f"<div style='color:#777;margin-top:6px'>— {d} —</div>")
            out.append(f"<span style='color:#777'>{time.strftime('%H:%M:%S', time.localtime(ts))}</span> "
                       f"<b style='color:{self.color}'>{html.escape(name or st.name_of(sid))}</b>: {html.escape(text)}")
        self.chat.setHtml("<br>".join(out) if out else
                          "<i style='color:#888'>Сообщений команды пока нет: история копится с момента, "
                          "когда программа начала их записывать.</i>")
        if at_bottom or not rows:
            sb.setValue(sb.maximum())
        else:
            sb.setValue(min(sb.value(), sb.maximum()))
