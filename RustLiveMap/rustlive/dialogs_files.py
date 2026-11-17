"""Rust Live Map: окно «Файлы сервера» — обзор, скачивание, загрузка, правка, переименование и удаление файлов на сервере
(локально, по FTP или SFTP). Все операции идут в отдельном потоке и не выходят за пределы корня сервера."""

import datetime
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from .fsbackend import BaseFs, LocalFs, make_backend, proto_of

MAX_EDIT_BYTES = 2 * 1024 * 1024      # файлы больше этого в редакторе не открываем


# ---------- чистая логика (без Qt, проверяется тестами) ----------

def fmt_size(n):
    n = float(n)
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or u == "ГБ":
            return f"{int(n)} {u}" if u == "Б" else f"{n:.1f} {u}"
        n /= 1024


def fmt_time(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else ""
    except (OverflowError, OSError, ValueError):
        return ""


def valid_name(name):
    """Имя одного файла или папки: без слешей и служебных «.», «..»."""
    n = (name or "").strip()
    return bool(n) and n not in (".", "..") and not any(c in n for c in "/\\\0")


def decode_text(data):
    """Байты файла -> (текст с «\\n», перевод строки файла, кодировка). ValueError, если это не текстовый UTF-8."""
    if len(data) > MAX_EDIT_BYTES:
        raise ValueError(f"Файл больше {MAX_EDIT_BYTES // 1024 // 1024} МБ — в редакторе не открываю.")
    if b"\0" in data[:8192]:
        raise ValueError("Это не текстовый файл.")
    enc = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = data.decode(enc)
    except UnicodeDecodeError:
        raise ValueError("Файл не в кодировке UTF-8 — править его здесь небезопасно.") from None
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), eol, enc


def encode_text(text, eol, enc):
    return text.replace("\r\n", "\n").replace("\n", eol).encode(enc)


def sort_entries(entries):
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.casefold()))


def do_download(fs, base, entries, dest):
    """Скачивает файлы и папки (рекурсивно) из base в локальную папку dest. Возвращает число файлов."""
    n = 0
    for e in entries:
        src = fs.join(base, e.name)
        if e.is_dir:
            for rp, rel in fs.walk_files(src):
                fs.download(rp, str(Path(dest) / e.name / Path(*rel.split("/"))))
                n += 1
        else:
            fs.download(src, str(Path(dest) / e.name))
            n += 1
    return n


def do_upload(fs, base, paths):
    """Загружает локальные файлы и папки в base. Возвращает число файлов."""
    n = 0

    def mk(p):
        try:
            fs.mkdir(p)
        except Exception:
            if not fs.exists(p):
                raise

    for lp in paths:
        lp = Path(lp)
        if lp.is_dir():
            mk(fs.join(base, lp.name))
            for root, dirs, files in os.walk(lp):
                rel = Path(root).relative_to(lp)
                tgt = fs.join(base, lp.name, *rel.parts)
                for d in dirs:
                    mk(fs.join(tgt, d))
                for f in files:
                    fs.upload(str(Path(root) / f), fs.join(tgt, f))
                    n += 1
        else:
            fs.upload(str(lp), fs.join(base, lp.name))
            n += 1
    return n


def do_delete(fs, base, entries):
    for e in entries:
        p = fs.join(base, e.name)
        if e.is_dir:
            fs.delete_tree(p)
        else:
            fs.remove_file(p)
    return len(entries)


def do_save_text(fs, path, old_bytes, new_bytes):
    """Сохраняет правку: сначала прежнее содержимое — в «<файл>.bak», новое — во временный файл, затем подмена.
    Права файла (например, исполняемый .sh на Linux) сохраняются."""
    tmpdir = Path(tempfile.mkdtemp(prefix="rlm_"))
    try:
        (tmpdir / "old").write_bytes(old_bytes)
        (tmpdir / "new").write_bytes(new_bytes)
        mode = fs.get_mode(path)
        fs.upload(str(tmpdir / "old"), path + ".bak")
        fs.upload(str(tmpdir / "new"), path + ".rlm-new")
        fs.replace(path + ".rlm-new", path)
        if mode is not None:
            fs.set_mode(path, mode)
    finally:
        for f in tmpdir.glob("*"):
            f.unlink()
        tmpdir.rmdir()


# ---------- окно ----------

class FsJob(QThread):
    ok = Signal(object)
    err = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.ok.emit(self.fn())
        except Exception as e:
            self.err.emit(str(e) or e.__class__.__name__)


class TextEditDialog(QDialog):
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 640)
        self.edit = QPlainTextEdit()
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.edit.setFont(f)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setPlainText(text)
        self._orig = text
        hint = QLabel("Перед сохранением прежняя версия файла будет записана рядом с суффиксом .bak")
        hint.setStyleSheet("color:#999")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.edit, 1)
        lay.addWidget(hint)
        lay.addWidget(box)

    def text(self):
        return self.edit.toPlainText()

    def changed(self):
        return self.text() != self._orig


class ServerFilesDialog(QDialog):
    """cfg — настройки доступа (словарь). Отпечаток SSH-ключа при первом подключении записывается в cfg['ssh_hostkey'];
    save — необязательная функция, которую вызываем после этого (сохранить конфиг)."""

    def __init__(self, cfg, parent=None, save=None):
        super().__init__(parent)
        self.cfg = cfg
        self.save = save
        self.setWindowTitle("Файлы сервера")
        self.resize(960, 600)
        proto = proto_of(cfg)
        self.pf = LocalFs() if proto in ("local", "smb") else BaseFs()           # только разбор путей, без подключения
        self.root = (cfg.get("srv_root") or cfg.get("srv_dir") or "").strip()
        self.cur = self.root
        self.entries = []
        self._job = None
        self._busy = False

        self.btn_up = QPushButton("⬆")
        self.btn_up.setToolTip("На уровень выше (Backspace)")
        self.btn_up.setFixedWidth(36)
        self.btn_up.clicked.connect(self.go_up)
        self.path = QLineEdit(self.cur)
        self.path.returnPressed.connect(self.go_typed)
        top = QHBoxLayout()
        top.addWidget(self.btn_up)
        top.addWidget(self.path, 1)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Имя", "Размер", "Изменён"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self.on_double)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        for key, fn in (("F5", self.refresh), ("Delete", self.delete), ("Backspace", self.go_up)):
            QShortcut(QKeySequence(key), self.table, activated=fn, context=Qt.ShortcutContext.WidgetShortcut)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        # на виду только самое частое; остальное (правка, переименование, новая папка, обновление) — по правому клику
        self.btn_down = QPushButton("⬇ Скачать")
        self.btn_down.clicked.connect(self.download)
        self.btn_up_load = QPushButton("⬆ Загрузить")
        um = QMenu(self.btn_up_load)
        um.addAction("Файлы…", self.upload_files)
        um.addAction("Папку…", self.upload_folder)
        self.btn_up_load.setMenu(um)
        self.btn_del = QPushButton("🗑 Удалить")
        self.btn_del.clicked.connect(self.delete)
        self.btns = [self.btn_down, self.btn_up_load, self.btn_del]
        row = QHBoxLayout()
        for b in self.btns:
            row.addWidget(b)
        row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status)
        lay.addLayout(row)
        if not self.root:
            self.say("Укажите корень сервера или папку identity в «Настройках».", True)
        else:
            self.refresh()

    # ---- служебное ----
    def say(self, text, err=False):
        self.status.setStyleSheet("color:#ff8080" if err else "color:#aaa")
        self.status.setText(text)

    def set_busy(self, on, text=""):
        self._busy = on
        for b in self.btns + [self.btn_up]:
            b.setEnabled(not on)
        self.path.setEnabled(not on)
        if on:
            self.say(text or "Работаю с сервером…")

    def context_menu(self, pos):
        m = QMenu(self)
        m.addAction("✎ Править", self.edit_file)
        m.addAction("Переименовать", self.rename)
        m.addAction("📁 Новая папка", self.make_dir)
        m.addSeparator()
        m.addAction("⟳ Обновить", self.refresh)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def reject(self):
        if self._busy:
            return                                   # дождёмся конца операции, чтобы не оборвать запись на сервере
        super().reject()

    def run(self, work, done, text="Работаю с сервером…"):
        """work(fs) выполняется в потоке с отдельным подключением; done(результат) — в окне."""
        cfg = dict(self.cfg)

        def job():
            with make_backend(cfg) as fs:
                return work(fs), fs.host_fp

        def ok(res):
            self.set_busy(False)
            value, fp = res
            if fp and not self.cfg.get("ssh_hostkey"):
                self.cfg["ssh_hostkey"] = fp             # первое подключение: запоминаем ключ сервера
                if self.save:
                    self.save()
            done(value)

        def err(msg):
            self.set_busy(False)
            self.say(msg, True)

        self.set_busy(True, text)
        self._job = FsJob(job, self)
        self._job.ok.connect(ok)
        self._job.err.connect(err)
        self._job.start()

    def selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.entries[r] for r in rows if 0 <= r < len(self.entries)]

    def inside(self, p):
        return self.pf.within(self.root, p)

    # ---- навигация ----
    def refresh(self):
        cur = self.cur

        def done(entries):
            if cur != self.cur:
                return
            self.entries = sort_entries(entries)
            self.table.setRowCount(len(self.entries))
            for r, e in enumerate(self.entries):
                it = QTableWidgetItem(("📁 " if e.is_dir else "") + e.name)
                it.setData(Qt.ItemDataRole.UserRole, e.name)
                self.table.setItem(r, 0, it)
                self.table.setItem(r, 1, QTableWidgetItem("" if e.is_dir else fmt_size(e.size)))
                self.table.setItem(r, 2, QTableWidgetItem(fmt_time(e.mtime)))
            self.say(f"{cur} — элементов: {len(self.entries)}")

        self.path.setText(cur)
        self.run(lambda fs: fs.listdir(cur), done, "Читаю папку…")

    def goto(self, p):
        if not self.inside(p):
            self.say("Выход за пределы папки сервера запрещён.", True)
            self.path.setText(self.cur)
            return
        self.cur = p
        self.refresh()

    def go_up(self):
        if not self.pf.within(self.cur, self.root):            # мы глубже корня, а не в нём самом
            self.goto(self.pf.parent(self.cur))

    def go_typed(self):
        t = self.path.text().strip()
        if t:
            self.goto(t)

    def on_double(self, row, _col):
        if not (0 <= row < len(self.entries)):
            return
        e = self.entries[row]
        if e.is_dir:
            self.goto(self.pf.join(self.cur, e.name))
        else:
            self.edit_file()

    # ---- операции ----
    def download(self):
        sel = self.selected()
        if not sel:
            return self.say("Выберите файлы или папки для скачивания.", True)
        dest = QFileDialog.getExistingDirectory(self, "Куда скачать")
        if not dest:
            return
        base = self.cur
        self.run(lambda fs: do_download(fs, base, sel, dest),
                 lambda n: self.say(f"Скачано файлов: {n} → {dest}"), "Скачиваю…")

    def _confirm_overwrite(self, names):
        have = {e.name for e in self.entries}
        clash = [n for n in names if n in have]
        if not clash:
            return True
        r = QMessageBox.question(self, "Файлы уже есть",
                                 "На сервере уже есть:\n  " + "\n  ".join(clash[:10]) + ("\n  …" if len(clash) > 10 else "")
                                 + "\n\nЗаменить?")
        return r == QMessageBox.StandardButton.Yes

    def upload_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Что загрузить на сервер")
        self._upload(paths)

    def upload_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Какую папку загрузить на сервер")
        self._upload([d] if d else [])

    def _upload(self, paths):
        if not paths or not self._confirm_overwrite([Path(p).name for p in paths]):
            return
        base = self.cur
        self.run(lambda fs: do_upload(fs, base, paths), self._after_change("Загружено файлов: {}"), "Загружаю…")

    def _after_change(self, fmt):
        def done(n):
            self.refresh()
            self.say(fmt.format(n))
        return done

    def edit_file(self):
        sel = [e for e in self.selected() if not e.is_dir]
        if len(sel) != 1:
            return self.say("Выберите один файл для правки.", True)
        e = sel[0]
        if e.size > MAX_EDIT_BYTES:
            return self.say(f"{e.name}: файл больше {MAX_EDIT_BYTES // 1024 // 1024} МБ.", True)
        path = self.pf.join(self.cur, e.name)

        def read(fs):
            tmp = Path(tempfile.mkdtemp(prefix="rlm_")) / "f"
            try:
                fs.download(path, str(tmp))
                return tmp.read_bytes()
            finally:
                if tmp.exists():
                    tmp.unlink()
                tmp.parent.rmdir()

        def opened(data):
            try:
                text, eol, enc = decode_text(data)
            except ValueError as ex:
                return self.say(f"{e.name}: {ex}", True)
            dlg = TextEditDialog(f"{e.name} — {self.cur}", text, self)
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.changed():
                return self.say("Без изменений.")
            new = encode_text(dlg.text(), eol, enc)
            self.run(lambda fs: do_save_text(fs, path, data, new),
                     lambda _r: (self.refresh(), self.say(f"{e.name}: сохранено, прежняя версия — {e.name}.bak")),
                     "Сохраняю на сервер…")

        self.run(read, opened, "Читаю файл…")

    def make_dir(self):
        name, ok = QInputDialog.getText(self, "Новая папка", "Имя папки:")
        if not ok:
            return
        if not valid_name(name):
            return self.say("Некорректное имя папки.", True)
        if any(e.name == name.strip() for e in self.entries):
            return self.say("Такое имя уже есть.", True)
        p = self.pf.join(self.cur, name.strip())
        self.run(lambda fs: fs.mkdir(p), lambda _r: self.refresh(), "Создаю папку…")

    def rename(self):
        sel = self.selected()
        if len(sel) != 1:
            return self.say("Выберите один элемент для переименования.", True)
        old = sel[0].name
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", QLineEdit.EchoMode.Normal, old)
        if not ok or name.strip() == old:
            return
        if not valid_name(name):
            return self.say("Некорректное имя.", True)
        if any(e.name == name.strip() for e in self.entries):
            return self.say("Такое имя уже есть.", True)
        a, b = self.pf.join(self.cur, old), self.pf.join(self.cur, name.strip())
        self.run(lambda fs: fs.rename(a, b), lambda _r: self.refresh(), "Переименовываю…")

    def delete(self):
        sel = self.selected()
        if not sel:
            return self.say("Выберите, что удалить.", True)
        names = [("📁 " if e.is_dir else "") + e.name for e in sel]
        text = "Удалить с сервера:\n  " + "\n  ".join(names[:12]) + ("\n  …" if len(names) > 12 else "")
        if any(e.is_dir for e in sel):
            text += "\n\nПапки удаляются вместе со всем содержимым."
        text += "\n\nОтменить это будет нельзя."
        box = QMessageBox(QMessageBox.Icon.Warning, "Удаление", text, parent=self)
        yes = box.addButton("Удалить", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not yes:
            return
        base = self.cur
        self.run(lambda fs: do_delete(fs, base, sel), self._after_change("Удалено элементов: {}"), "Удаляю…")
