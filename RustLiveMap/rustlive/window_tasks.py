"""Rust Live Map: задания — выполнение команд в консоли сервера по расписанию."""

import time
from PySide6.QtCore import QTimer

from .config import save_config
from .dialogs_tasks import TaskEditDialog, TasksDialog
from .tasks import MAX_LATE, due, expand_cmds


class TasksMixin:
    """Раз в секунду проверяет cfg["tasks"]. Выполняет строго по расписанию и только пока программа подключена."""

    def setup_tasks(self):
        self.tasks_win = None
        self._task_prev = time.time()
        self.task_timer = QTimer(self)
        self.task_timer.timeout.connect(self.tick_tasks)
        self.task_timer.start(1000)

    def save_tasks(self):
        save_config(self.cfg)

    def tick_tasks(self):
        now = time.time()
        prev = self._task_prev
        self._task_prev = now
        if now - prev > MAX_LATE:                 # сон ПК или долгое зависание: накопившееся не догоняем
            prev = now - 1
        changed = False
        for t in list(self.cfg.get("tasks") or []):
            if due(t, prev, now):
                self.run_task(t)
            elif (t.get("kind") == "once" and not t.get("done") and t.get("enabled", True)
                  and t.get("at") and now - float(t["at"]) > MAX_LATE):
                t["done"], t["status"] = True, "пропущено (программа была закрыта)"
                changed = True
        if changed:
            self.save_tasks()

    def run_task(self, t, manual=False):
        now = time.time()
        label = f"Задание «{t['cmds'][:40]}»"
        if not (self.chat_worker and self.chat_worker.isRunning()):
            self.on_console_lines([(now, "Error", f"{label}: нет подключения к консоли сервера — не выполнено")])
            if t.get("kind") == "once" and not manual:
                t["done"], t["status"] = True, "не выполнено (нет связи)"
                self.save_tasks()
            return False
        for cmd in expand_cmds(t["cmds"], len(self.players)):
            self.on_console_lines([(now, "Cmd", f"[Задание] {cmd}")])
            self.chat_worker.send_command(cmd)
        t["last"] = now
        if t.get("kind") == "once" and not manual:
            t["done"], t["status"] = True, "выполнено " + time.strftime("%d.%m %H:%M", time.localtime(now))
        self.save_tasks()
        return True

    def open_tasks(self):
        win = self.tasks_win
        if win is not None:
            try:
                if win.isVisible():
                    win.raise_()
                    win.activateWindow()
                    return
            except RuntimeError:
                pass
        win = TasksDialog(self)
        self.tasks_win = win
        win.show()

    def new_task(self):
        d = TaskEditDialog(None, self)
        if d.exec() == d.DialogCode.Accepted:
            self.cfg.setdefault("tasks", []).append(d.result)
            self.save_tasks()
            if self.tasks_win is not None:
                try:
                    self.tasks_win.refresh(True)
                except RuntimeError:
                    pass
