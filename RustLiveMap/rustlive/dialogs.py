"""Rust Live Map: модуль dialogs."""

import datetime
import time
from PySide6.QtCore import QDate, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QCalendarWidget, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QFileDialog, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from .fsbackend import (
    make_backend,
    proto_of,
)
from .mapsource import (
    map_entries,
    pick_map,
)
from .events import (
    first_thursday,
)


class HistoryLineEdit(QLineEdit):
    """Строка ввода команд: ↑ / ↓ листают ранее введённые."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hist = []
        self.pos = 0

    def push(self, text):
        if text and (not self.hist or self.hist[-1] != text):
            self.hist.append(text)
            del self.hist[:-100]
        self.pos = len(self.hist)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Up and self.hist:
            self.pos = max(0, self.pos - 1)
            self.setText(self.hist[self.pos])
            return
        if e.key() == Qt.Key.Key_Down and self.hist:
            if self.pos < len(self.hist) - 1:
                self.pos += 1
                self.setText(self.hist[self.pos])
            else:
                self.pos = len(self.hist)
                self.clear()
            return
        super().keyPressEvent(e)


class PeriodCombo(QComboBox):
    """Список периодов: при выбранных датах стрелки ↑/↓ сдвигают период назад/вперёд."""
    step_requested = Signal(int)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.currentData() == "range":
            self.step_requested.emit(-1 if e.key() == Qt.Key.Key_Up else 1)
            e.accept()
            return
        super().keyPressEvent(e)


class DateRangeDialog(QDialog):
    """Выбор периода статистики по календарю: один день, неделя, месяц или любой диапазон."""

    def __init__(self, bounds, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Период статистики")
        self.bounds = bounds
        self.cal_from = QCalendarWidget()
        self.cal_to = QCalendarWidget()
        for cal in (self.cal_from, self.cal_to):
            cal.setGridVisible(True)
            cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            if bounds:
                cal.setMinimumDate(QDate(bounds[0].year, bounds[0].month, bounds[0].day))
                cal.setMaximumDate(QDate(bounds[1].year, bounds[1].month, bounds[1].day))
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#999")

        presets = QHBoxLayout()
        for text, fn in (("Сегодня", lambda t: (t, t)),
                         ("Вчера", lambda t: (t - datetime.timedelta(days=1),) * 2),
                         ("7 дней", lambda t: (t - datetime.timedelta(days=6), t)),
                         ("30 дней", lambda t: (t - datetime.timedelta(days=29), t)),
                         ("Этот месяц", lambda t: (t.replace(day=1), t)),
                         ("Прошлый месяц", self._prev_month)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, fn=fn: self._preset(fn))
            presets.addWidget(b)

        cals = QHBoxLayout()
        for title, cal in (("С даты", self.cal_from), ("По дату (включительно)", self.cal_to)):
            col = QVBoxLayout()
            col.addWidget(QLabel(title))
            col.addWidget(cal)
            cals.addLayout(col)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(presets)
        lay.addLayout(cals)
        lay.addWidget(self.hint)
        lay.addWidget(buttons)

        today = datetime.date.today()
        if current:
            d1, d2 = current
        else:
            d1, d2 = today - datetime.timedelta(days=6), today
        self._set(d1, d2)
        self.cal_from.selectionChanged.connect(self._fix_order_from)
        self.cal_to.selectionChanged.connect(self._fix_order_to)

    @staticmethod
    def _prev_month(t):
        last = t.replace(day=1) - datetime.timedelta(days=1)
        return last.replace(day=1), last

    @staticmethod
    def _qd(d):
        return QDate(d.year, d.month, d.day)

    def _set(self, d1, d2):
        if self.bounds:   # календарь не даёт выйти за границы данных
            d1 = min(max(d1, self.bounds[0]), self.bounds[1])
            d2 = min(max(d2, self.bounds[0]), self.bounds[1])
        self.cal_from.setSelectedDate(self._qd(d1))
        self.cal_to.setSelectedDate(self._qd(d2))
        self._update_hint()

    def _preset(self, fn):
        self._set(*fn(datetime.date.today()))

    def _fix_order_from(self):
        if self.cal_from.selectedDate() > self.cal_to.selectedDate():
            self.cal_to.setSelectedDate(self.cal_from.selectedDate())
        self._update_hint()

    def _fix_order_to(self):
        if self.cal_to.selectedDate() < self.cal_from.selectedDate():
            self.cal_from.setSelectedDate(self.cal_to.selectedDate())
        self._update_hint()

    def _update_hint(self):
        a, b = self.dates()
        n = (b - a).days + 1
        txt = f"Выбрано дней: {n}"
        if self.bounds:
            txt += f" · данные в базе: {self.bounds[0].strftime('%d.%m.%Y')} — {self.bounds[1].strftime('%d.%m.%Y')}"
        self.hint.setText(txt)

    def dates(self):
        a, b = self.cal_from.selectedDate(), self.cal_to.selectedDate()
        return (datetime.date(a.year(), a.month(), a.day()), datetime.date(b.year(), b.month(), b.day()))


class WipesDialog(QDialog):
    """Границы вайпов. Программа знает начало вайпа только если видела смену сида; если сиды в логах
    не встречались, весь период склеен в один вайп. Здесь границы можно расставить по календарю
    (вайп = первый четверг месяца) или вручную."""

    WIPE_HOUR = 21     # во сколько по умолчанию предлагаем вайп в первый четверг

    def __init__(self, store, parent=None, focus_wid=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Границы вайпов")
        self.resize(720, 420)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Начало", "Конец", "Дней", "Сид", "Событий"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#999")

        b2 = QPushButton("➕ Граница вручную…")
        b2.clicked.connect(self.add_manual)
        b5 = QPushButton("🗺 Загрузить карту…")
        b5.setToolTip("Файл .map или PNG для выбранного вайпа (карта сохранится в архив карт)")
        b5.clicked.connect(self.assign_png)
        b4 = QPushButton("🗑 Удалить вайп с данными")
        b4.setToolTip("Удалить выбранный вайп и всю его статистику (перед этим делается копия базы)")
        b4.clicked.connect(self.delete_selected)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        for b in (b2, b5, b4):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addWidget(self.hint)
        lay.addLayout(row)
        self.rows = []
        self.refill()
        if focus_wid is not None:      # открыто по предложению: сразу показать варианты
            for r, w in enumerate(self.rows):
                if w["id"] == focus_wid:
                    self.table.selectRow(r)
                    QTimer.singleShot(0, self.suggest)
                    break

    @staticmethod
    def _f(ts):
        return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

    def refill(self):
        counts = self.store.wipe_event_counts()
        self.rows = [w for w in self.store.wipes if w["seed"] or counts.get(w["id"])]
        self.table.setRowCount(len(self.rows))
        long_wipe = False
        for r, w in enumerate(self.rows):
            start, end = self.store.wipe_span(w)
            days = ((end or time.time()) - start) / 86400 if start else 0
            long_wipe = long_wipe or days > 36
            vals = [self._f(start) if start else "?" if w["start"] <= 0 else self._f(w["start"]),
                    self._f(end) if end else "сейчас", f"{days:.0f}", str(w["seed"] or "—"),
                    str(counts.get(w["id"], (0,))[0])]
            if w["start"] <= 0:
                vals[0] += " (по данным)"
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if days > 36:
                    it.setForeground(QColor("#f0a030"))
                self.table.setItem(r, c, it)
        self.hint.setText("Оранжевым выделены вайпы дольше 36 дней — так долго вайп не идёт, скорее всего в логах "
                          "не было сида. Выберите такой вайп и нажмите «Граница вручную…»."
                          if long_wipe else "Вайп длится до первого четверга следующего месяца.")

    def assign_png(self):
        w = self.current()
        if w is None:
            QMessageBox.information(self, "Границы вайпов", "Выберите вайп в списке.")
            return
        self.parent().assign_map_png(w)
        self.refill()

    def current(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def suggest(self):
        w = self.current()
        if w is None:
            QMessageBox.information(self, "Границы вайпов", "Выберите вайп в списке.")
            return
        start, end = self.store.wipe_span(w)
        if end is None:
            QMessageBox.information(self, "Границы вайпов", "Текущий вайп делить нельзя: его границу задаёт сид карты.")
            return
        cands = []
        d = datetime.date.fromtimestamp(start).replace(day=1)
        while True:
            ft = first_thursday(d.year, d.month)
            ts = self.store.guess_wipe_time(ft, self.WIPE_HOUR)
            if ts >= end:
                break
            if ts > start + 3600:
                cands.append(ts)
            d = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        if not cands:
            QMessageBox.information(self, "Границы вайпов", "Внутри этого вайпа первых четвергов нет.")
            return
        pick = _PickDialog(cands, self)
        if pick.exec() != QDialog.DialogCode.Accepted:
            return
        errs = [e for e in (self.store.split_wipe(ts) for ts in pick.chosen()) if e]
        if errs:
            QMessageBox.warning(self, "Границы вайпов", "\n".join(errs))
        self.refill()

    def add_manual(self):
        w = self.current()
        d0, d1 = None, None
        if w is not None:
            st, en = self.store.wipe_span(w)
            d0, d1 = st or None, en
        fmt = "%d.%m.%Y %H:%M"
        dlg = QDialog(self)
        dlg.setWindowTitle("Граница вайпа вручную")
        e_start = QLineEdit(datetime.datetime.fromtimestamp(d0).strftime(fmt) if d0
                            else datetime.datetime.now().strftime("%d.%m.%Y 21:00"))
        e_end = QLineEdit(datetime.datetime.fromtimestamp(d1).strftime(fmt) if d1 else "")
        e_end.setPlaceholderText("пусто — до начала следующего вайпа")
        form = QFormLayout()
        form.addRow("Начало (ДД.ММ.ГГГГ ЧЧ:ММ):", e_start)
        form.addRow("Конец (ДД.ММ.ГГГГ ЧЧ:ММ):", e_end)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Вайп с этими началом и концом будет вырезан из того вайпа, внутри которого лежит.\n"
                             "События распределятся по новым границам."))
        lay.addLayout(form)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            start = datetime.datetime.strptime(e_start.text().strip(), fmt).timestamp()
            end = datetime.datetime.strptime(e_end.text().strip(), fmt).timestamp() if e_end.text().strip() else None
        except ValueError:
            QMessageBox.warning(self, "Граница вайпа", "Не понял дату. Формат: 07.05.2026 21:00")
            return
        if end is not None and end <= start:
            QMessageBox.warning(self, "Граница вайпа", "Конец должен быть позже начала.")
            return
        with self.store.lock:
            host = self.store._wipe_at(start) or next(
                (x for x in self.store.wipes if x["seed"] is None and x["start"] <= 0), None)
        if host is None:
            QMessageBox.warning(self, "Граница вайпа", "В этот момент вайпа нет.")
            return
        h_start, h_end = self.store.wipe_span(host)
        if end is not None and h_end is not None and end > h_end + 60:
            QMessageBox.warning(self, "Граница вайпа", "Конец позже, чем кончается вайп, внутри которого лежит начало.\n"
                                f"Он заканчивается {self._f(h_end)}.")
            return
        errs = []
        if not h_start or start > h_start + 60:       # граница начала уже совпадает с началом вайпа — не делим
            errs.append(self.store.split_wipe(start))
        if end is not None and (h_end is None or end < h_end - 60):
            errs.append(self.store.split_wipe(end))
        errs = [e for e in errs if e]
        if errs:
            QMessageBox.warning(self, "Граница вайпа", "\n".join(errs))
        self.refill()

    def delete_selected(self):
        w = self.current()
        if w is None:
            QMessageBox.information(self, "Границы вайпов", "Выберите вайп в списке.")
            return
        start, end = self.store.wipe_span(w)
        n = self.store.wipe_event_counts().get(w["id"], (0,))[0]
        if QMessageBox.question(
                self, "Удалить вайп",
                f"Удалить вайп {self._f(start) if start else '?'} — {self._f(end) if end else 'сейчас'} "
                f"(сид {w['seed'] or '—'}) вместе с данными?\n\nБудет удалено событий: {n}, а также его Heatmap.\n"
                "Перед удалением сохраняется копия базы. Файл карты в архиве остаётся.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        err = self.store.delete_wipe(w["id"])
        if err:
            QMessageBox.warning(self, "Границы вайпов", err)
        self.refill()

    def merge(self):
        w = self.current()
        if w is None:
            return
        err = self.store.merge_wipe_back(w["id"])
        if err:
            QMessageBox.warning(self, "Границы вайпов", err)
        self.refill()


class _PickDialog(QDialog):
    """Список предложенных границ с галочками."""

    def __init__(self, stamps, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Предложенные границы")
        self.stamps = stamps
        self.boxes = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Первые четверги месяцев внутри вайпа. Снимите галочку с лишних.\n"
                             "Время — по первому запуску сервера в тот день (иначе 21:00); точнее можно поправить «Границей вручную»."))
        wd = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
        for ts in stamps:
            d = datetime.datetime.fromtimestamp(ts)
            cb = QCheckBox(f"{d.strftime('%d.%m.%Y %H:%M')} ({wd[d.weekday()]})")
            cb.setChecked(True)
            lay.addWidget(cb)
            self.boxes.append(cb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self):
        return [ts for ts, cb in zip(self.stamps, self.boxes, strict=True) if cb.isChecked()]


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None, ask_folder=False):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(420)

        self.host = QLineEdit(str(cfg["rcon_host"]))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(cfg["rcon_port"]))
        self.password = QLineEdit(str(cfg["rcon_password"]))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_pw = QCheckBox("Показать пароль")
        self.show_pw.toggled.connect(
            lambda on: self.password.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))

        conn = QFormLayout()
        conn.addRow("Адрес (хост):", self.host)
        conn.addRow("RCON порт:", self.port)
        conn.addRow("RCON пароль:", self.password)
        conn.addRow("", self.show_pw)
        box_conn = QGroupBox("Подключение к серверу")
        box_conn.setLayout(conn)

        # папка сервера — необязательная: оттуда берём карту .map, а в окне «Файлы сервера» управляем файлами
        self.proto = QComboBox()
        for key, text in (("local", "Этот компьютер"), ("smb", r"Сетевая папка (\\сервер\папка)"),
                          ("ftp", "FTP / FTPS"), ("sftp", "SFTP (Linux, SSH)")):
            self.proto.addItem(text, key)
        self.proto.setCurrentIndex(max(0, self.proto.findData(proto_of(cfg))))
        self.srv_dir = QLineEdit(str(cfg.get("srv_root") or cfg.get("srv_dir") or ""))
        self.srv_dir.setPlaceholderText(r"D:\Server\rustds   |   \\server-pc\rust   |   /home/rust/server")
        self.browse = QPushButton("Обзор…")
        self.browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.srv_dir, 1)
        row.addWidget(self.browse)
        self.ftp_host = QLineEdit(str(cfg.get("ftp_host") or ""))
        self.ftp_host.setPlaceholderText("адрес сервера")
        self.ftp_port = QSpinBox()
        self.ftp_port.setRange(1, 65535)
        self.ftp_port.setValue(int(cfg.get("ftp_port") or 21))
        self.ftp_user = QLineEdit(str(cfg.get("ftp_user") or ""))
        self.ftp_user.setPlaceholderText("логин (для сетевой папки — необязательно)")
        self.ftp_pass = QLineEdit(str(cfg.get("ftp_pass") or ""))
        self.ftp_pass.setPlaceholderText("пароль")
        self.ftp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_key = QLineEdit(str(cfg.get("ssh_key") or ""))
        self.ssh_key.setPlaceholderText("файл ключа SSH (если входите по ключу; тогда пароль — к ключу)")
        self.key_btn = QPushButton("Файл…")
        self.key_btn.clicked.connect(self._browse_key)
        self.ftp_tls = QCheckBox("FTPS (шифрование TLS)")
        self.ftp_tls.setChecked(bool(cfg.get("ftp_tls")))
        self.ftp_test = QPushButton("Проверить")
        self.ftp_test.clicked.connect(self._test_source)
        self.ftp_info = QLabel("")
        self.ftp_info.setWordWrap(True)
        self._hostkey = str(cfg.get("ssh_hostkey") or "")
        self._conn0 = (str(cfg.get("ftp_host") or ""), int(cfg.get("ftp_port") or 21))
        self.proto.currentIndexChanged.connect(self._proto_changed)

        # блок удалённого доступа: виден только для FTP/SFTP
        self.remote_box = QWidget()
        rl = QVBoxLayout(self.remote_box)
        rl.setContentsMargins(0, 0, 0, 0)
        self.host_row = QWidget()
        r1 = QHBoxLayout(self.host_row)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.addWidget(self.ftp_host, 1)
        r1.addWidget(self.ftp_port)
        r2 = QHBoxLayout()
        r2.addWidget(self.ftp_user, 1)
        r2.addWidget(self.ftp_pass, 1)
        self.key_row = QWidget()
        r3 = QHBoxLayout(self.key_row)
        r3.setContentsMargins(0, 0, 0, 0)
        r3.addWidget(self.ssh_key, 1)
        r3.addWidget(self.key_btn)
        rl.addWidget(self.host_row)
        rl.addLayout(r2)
        rl.addWidget(self.key_row)
        rl.addWidget(self.ftp_tls)
        self.ftp_warn = QLabel("⚠ Обычный FTP передаёт пароль и файлы открытым текстом. Лучше FTPS или SFTP.")
        self.ftp_warn.setWordWrap(True)
        self.ftp_warn.setStyleSheet("color:#f0c040")
        rl.addWidget(self.ftp_warn)
        self.ftp_tls.toggled.connect(self._proto_changed)

        self.box_src = QGroupBox("Папка сервера (необязательно)")
        self.box_src.setCheckable(True)
        self.box_src.setChecked(bool(ask_folder or (cfg.get("srv_root") or cfg.get("srv_dir"))))
        sl = QVBoxLayout(self.box_src)
        sl.addWidget(self.proto)
        sl.addLayout(row)
        sl.addWidget(self.remote_box)
        brow = QHBoxLayout()
        brow.addWidget(self.ftp_test)
        brow.addWidget(self.ftp_info, 1)
        sl.addLayout(brow)
        self.box_src.toggled.connect(self._folder_toggled)
        self._proto_changed()
        self._folder_toggled(self.box_src.isChecked())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(box_conn)
        lay.addWidget(self.box_src)
        lay.addWidget(buttons)

    def _proto_key(self):
        return self.proto.currentData() or "local"

    def _folder_toggled(self, on):
        for w in (self.proto, self.srv_dir, self.browse, self.remote_box, self.ftp_test, self.ftp_info):
            w.setVisible(bool(on))
        if on:
            self._proto_changed()
        self.adjustSize()

    def _proto_changed(self, *_):
        p = self._proto_key()
        on = self.box_src.isChecked()
        self.browse.setVisible(p in ("local", "smb") and on)
        self.remote_box.setVisible(p != "local" and on)
        self.host_row.setVisible(p in ("ftp", "sftp"))
        self.key_row.setVisible(p == "sftp")
        self.ftp_tls.setVisible(p == "ftp")
        self.ftp_warn.setVisible(p == "ftp" and not self.ftp_tls.isChecked() and on)
        self.ftp_user.setPlaceholderText("логин (пусто — текущая учётная запись Windows)" if p == "smb" else "логин")
        if self.ftp_port.value() in (21, 22):             # стандартный порт подставляем сами
            self.ftp_port.setValue(22 if p == "sftp" else 21)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Папка сервера", self.srv_dir.text())
        if d:
            self.srv_dir.setText(d)

    def _browse_key(self):
        f, _ = QFileDialog.getOpenFileName(self, "Закрытый ключ SSH", self.ssh_key.text())
        if f:
            self.ssh_key.setText(f)

    def _source_cfg(self):
        on = self.box_src.isChecked()
        p = self._proto_key() if on else "local"
        conn = (self.ftp_host.text().strip(), self.ftp_port.value())
        if conn != self._conn0:                           # другой сервер — старый отпечаток ключа не годится
            self._hostkey = ""
            self._conn0 = conn
        return {"srv_proto": p, "srv_ftp": p == "ftp", "srv_dir": self.srv_dir.text().strip() if on else "",
                "srv_root": "",
                "ftp_host": conn[0], "ftp_port": conn[1], "ftp_user": self.ftp_user.text().strip(),
                "ftp_pass": self.ftp_pass.text(), "ftp_tls": self.ftp_tls.isChecked(),
                "ssh_key": self.ssh_key.text().strip(), "ssh_hostkey": self._hostkey}

    def _test_source(self):
        """Быстрая проверка: подключаемся и ищем .map (без скачивания)."""
        c = self._source_cfg()
        c["srv_proto"] = self._proto_key()
        try:
            with make_backend(c) as fs:
                ents = map_entries(fs, c["srv_dir"])
                if fs.host_fp and not self._hostkey:
                    self._hostkey = fs.host_fp
            e = pick_map(ents)
            self.ftp_info.setText(f"✓ Найдено карт: {len(ents)}. Свежая: {e[0]}" if e
                                  else "✓ Подключение есть, файлов .map не найдено")
        except Exception as ex:
            self.ftp_info.setText(f"✗ {ex}")

    def apply(self, cfg):
        cfg["rcon_host"] = self.host.text().strip()
        cfg["rcon_port"] = self.port.value()
        cfg["rcon_password"] = self.password.text()
        cfg.update(self._source_cfg())
