"""Rust Live Map: окна заданий — список и создание/правка."""

import datetime
import time
from PySide6.QtCore import QDateTime, QTime, QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from .tasks import KINDS, fmt_ts, new_task, next_run, split_cmds, when_text


class TaskEditDialog(QDialog):
    """Создание и правка одного задания. Результат — словарь задания в self.result."""

    def __init__(self, task=None, parent=None):
        super().__init__(parent)
        self.task = task
        self.result = None
        self.setWindowTitle("Изменить задание" if task else "Новое задание")
        self.setMinimumWidth(560)

        self.radios = {}
        row = QHBoxLayout()
        for k, label in KINDS.items():
            r = QRadioButton(label)
            r.toggled.connect(self._on_kind)
            self.radios[k] = r
            row.addWidget(r)
        row.addStretch(1)

        self.t_daily = QTimeEdit(QTime(6, 0))
        self.t_daily.setDisplayFormat("HH:mm")
        self.t_once = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.t_once.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.t_once.setCalendarPopup(True)
        self.h = QSpinBox()
        self.h.setRange(0, 999)
        self.h.setSuffix(" ч")
        self.m = QSpinBox()
        self.m.setRange(0, 59)
        self.m.setSuffix(" мин")
        self.m.setValue(55)
        pw = QWidget()
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel("каждые"))
        pl.addWidget(self.h)
        pl.addWidget(self.m)
        pl.addStretch(1)
        self.stack = QStackedWidget()
        for w in (self.t_daily, self.t_once, pw):
            self.stack.addWidget(w)
        self.stack.setSizePolicy(self.stack.sizePolicy().horizontalPolicy(), self.t_daily.sizePolicy().verticalPolicy())

        self.cmds = QLineEdit()
        self.cmds.setPlaceholderText("например: say Онлайн: ${playerCount}")
        self.err = QLabel("")
        self.err.setStyleSheet("color:#ff8080")
        hint = QLabel("Примеры:  say Всем привет   ·   server.save   ·   say Рестарт через 5 минут; restart 300\n"
                      "Чтобы выполнить несколько команд за один раз, разделяйте их знаком «;».\n"
                      "${playerCount} в команде заменяется числом игроков онлайн на момент выполнения.\n"
                      "Задания выполняются только пока программа запущена и подключена к серверу.")
        hint.setStyleSheet("color:#999")
        hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Тип:", self._wrap(row))
        form.addRow("Время / период:", self.stack)
        form.addRow("Команды:", self.cmds)
        bb = QDialogButtonBox()
        ok = bb.addButton("Сохранить" if task else "Создать", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        ok.setDefault(True)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.err)
        lay.addWidget(hint)
        lay.addWidget(bb)

        kind = (task or {}).get("kind", "daily")
        self.radios[kind].setChecked(True)
        if task:
            self.cmds.setText(task.get("cmds", ""))
            if kind == "daily":
                hh, mm = (str(task.get("time") or "06:00") + ":0").split(":")[:2]
                self.t_daily.setTime(QTime(int(hh), int(mm)))
            elif kind == "once" and task.get("at"):
                self.t_once.setDateTime(QDateTime.fromSecsSinceEpoch(int(task["at"])))
            elif kind == "period":
                mins = int(task.get("every") or 3600) // 60
                self.h.setValue(mins // 60)
                self.m.setValue(mins % 60)
        self._on_kind()

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    def kind(self):
        return next((k for k, r in self.radios.items() if r.isChecked()), "daily")

    def _on_kind(self, *_):
        self.stack.setCurrentIndex(list(KINDS).index(self.kind()))

    def _accept(self):
        cmds = self.cmds.text().strip()
        kind = self.kind()
        if not split_cmds(cmds):
            self.err.setText("Введите команду.")
            return
        base = dict(self.task) if self.task else new_task(kind, cmds)
        base.update(kind=kind, cmds=cmds)
        if kind == "daily":
            base["time"] = self.t_daily.time().toString("HH:mm")
        elif kind == "once":
            at = self.t_once.dateTime().toSecsSinceEpoch()
            if at <= time.time():
                self.err.setText("Это время уже прошло.")
                return
            base["at"] = float(at)
        else:
            every = (self.h.value() * 60 + self.m.value()) * 60
            if every < 60:
                self.err.setText("Период — не меньше 1 минуты.")
                return
            base["every"] = every
        base.update(anchor=time.time(), done=False, status="")      # расписание отсчитывается с момента сохранения
        self.result = base
        self.accept()


class TasksDialog(QDialog):
    """Список заданий. Работает прямо с cfg["tasks"] главного окна."""

    def __init__(self, win):
        super().__init__(None)
        self.win = win
        self.setWindowTitle("Задания")
        self.resize(820, 380)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Тип", "Когда", "Команды", "Следующий запуск"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        for c, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.ResizeToContents)):
            h.setSectionResizeMode(c, mode)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        self.table.itemChanged.connect(self._on_check)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)

        b_new = QPushButton("➕ Создать")
        b_new.clicked.connect(self.create)
        b_edit = QPushButton("✏ Изменить")
        b_edit.clicked.connect(self.edit)
        b_run = QPushButton("▶ Выполнить сейчас")
        b_run.clicked.connect(self.run_now)
        b_del = QPushButton("🗑 Удалить")
        b_del.clicked.connect(self.delete)
        b_close = QPushButton("Закрыть")
        b_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        for b in (b_new, b_edit, b_run, b_del):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(b_close)
        self.note = QLabel("Галочка слева включает и ставит на паузу. Задания выполняются, пока программа "
                           "запущена и подключена; пропущенное за время простоя не догоняется.")
        self.note.setStyleSheet("color:#999")
        self.note.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addWidget(self.note)
        lay.addLayout(row)
        self._sig = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def tasks(self):
        return self.win.cfg.setdefault("tasks", [])

    def current(self):
        r = self.table.currentRow()
        it = self.table.item(r, 1) if r >= 0 else None
        tid = it.data(Qt.ItemDataRole.UserRole) if it else None
        return next((t for t in self.tasks() if t["id"] == tid), None)

    def refresh(self, force=False):
        now = time.time()
        sig = (repr(self.tasks()), int(now // 20))
        if sig == self._sig and not force:
            return
        self._sig = sig
        cur = self.current()
        cid = cur["id"] if cur else None
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.tasks()))
        for r, t in enumerate(self.tasks()):
            nxt = next_run(t, now)
            if t.get("done"):
                state = t.get("status") or "выполнено"
            elif not t.get("enabled", True):
                state = "пауза"
            elif nxt:
                state = fmt_ts(nxt)
            else:
                state = "—"
            if t.get("kind") != "once" and t.get("last"):
                state += f"   (был: {datetime.datetime.fromtimestamp(t['last']).strftime('%d.%m %H:%M')})"
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if t.get("enabled", True) else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            kind = QTableWidgetItem(KINDS.get(t["kind"], t["kind"]))
            kind.setData(Qt.ItemDataRole.UserRole, t["id"])
            self.table.setItem(r, 1, kind)
            self.table.setItem(r, 2, QTableWidgetItem(when_text(t)))
            cm = QTableWidgetItem(t["cmds"])
            cm.setToolTip(t["cmds"])
            self.table.setItem(r, 3, cm)
            st = QTableWidgetItem(state)
            if t.get("done") or not t.get("enabled", True):
                st.setForeground(QBrush(QColor("#888888")))
            self.table.setItem(r, 4, st)
            if t["id"] == cid:
                self.table.selectRow(r)
        self.table.blockSignals(False)

    def _on_check(self, item):
        if item.column() != 0:
            return
        t = next((t for t in self.tasks() if t["id"] == self.table.item(item.row(), 1).data(Qt.ItemDataRole.UserRole)), None)
        if t is None:
            return
        t["enabled"] = item.checkState() == Qt.CheckState.Checked
        if t["enabled"] and t["kind"] == "period":
            t["anchor"] = time.time()                  # снятое с паузы периодическое отсчитывается заново
        self.win.save_tasks()
        self.refresh(True)

    def create(self):
        d = TaskEditDialog(None, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.tasks().append(d.result)
            self.win.save_tasks()
            self.refresh(True)

    def edit(self):
        t = self.current()
        if t is None:
            return
        d = TaskEditDialog(t, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            t.update(d.result)
            self.win.save_tasks()
            self.refresh(True)

    def run_now(self):
        t = self.current()
        if t is not None:
            self.win.run_task(t, manual=True)
            self.refresh(True)

    def delete(self):
        t = self.current()
        if t is None:
            return
        r = QMessageBox.question(self, "Задания", f"Удалить задание «{t['cmds'][:60]}»?")
        if r == QMessageBox.StandardButton.Yes:
            self.win.cfg["tasks"] = [x for x in self.tasks() if x["id"] != t["id"]]
            self.win.save_tasks()
            self.refresh(True)

    def _menu(self, pos):
        if self.table.itemAt(pos) is None:
            return
        m = QMenu(self)
        m.addAction("✏ Изменить", self.edit)
        m.addAction("▶ Выполнить сейчас", self.run_now)
        m.addAction("🗑 Удалить", self.delete)
        m.exec(self.table.viewport().mapToGlobal(pos))
