"""Rust Live Map: модуль window_chat."""

import html
import re
import time

from .chat import (
    chat_file_line,
    tail_chat_lines,
)
from .config import (
    chat_dir,
)
from .workers import (
    ChatSearchWorker,
)


class ChatMixin:
    """Игровой чат (архив, поиск) и консоль сервера."""

    # ---------- чат ----------
    @staticmethod
    def format_chat(e):
        esc = html.escape
        ts = time.strftime("%H:%M", time.localtime(e["time"]))
        color = e["color"]
        if re.fullmatch(r"#[0-9a-fA-F]{3}", color):
            color = "#" + "".join(ch * 2 for ch in color[1:])
        elif not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#e6e6e6"
        channel = {1: "[Команда] ", 2: "[Сервер] "}.get(e["channel"], "")
        name = esc(e["name"]) or "Сервер"
        return (f"<span style='color:#777'>{ts}</span> "
                f"<span style='color:#999'>{channel}</span>"
                f"<b style='color:{color}'>{name}</b>: {esc(e['text'])}")

    def load_chat_history(self):
        # хвост сегодняшнего архива — чтобы после запуска чат не был пустым
        self._chat_hist = set()
        for day, line in tail_chat_lines(chat_dir(), 150):
            self._chat_hist.add(line)
            self.chat_view.append(f"<span style='color:#777'>{html.escape(day[8:10] + '.' + day[5:7])} "
                                  f"{html.escape(line)}</span>")

    def search_chat(self):
        q = self.chat_search.text().strip()
        self._search_token += 1
        if not q:
            self.chat_stack.setCurrentIndex(0)
            return
        self.search_view.setHtml("<i>Поиск…</i>")
        self.chat_stack.setCurrentIndex(1)
        wk = ChatSearchWorker(self._search_token, chat_dir(), q, self)
        wk.done.connect(self.on_chat_search_done)
        wk.finished.connect(lambda w=wk: self._search_workers.discard(w))
        self._search_workers.add(wk)                    # держим ссылку, пока поток работает
        wk.start()

    def on_chat_search_done(self, token, q, res, more):
        if token != self._search_token:                 # пришёл ответ на устаревший запрос
            return
        rx = re.compile("(" + "|".join(re.escape(w) for w in q.lower().split()) + ")", re.IGNORECASE)
        head = (f"<b>Найдено: {len(res)}{'+' if more else ''}</b> "
                f"<span style='color:#888'>(новые сверху{', показаны первые 500' if more else ''})</span><br>")
        rows = []
        for day, line in res:
            parts = rx.split(line)                      # нечётные элементы — совпадения; экранируем по частям
            txt = "".join(f"<span style='background:#6b5a00;color:#fff'>{html.escape(x)}</span>" if i % 2 else html.escape(x)
                          for i, x in enumerate(parts))
            rows.append(f"<span style='color:#777'>{day[8:10]}.{day[5:7]}.{day[:4]}</span> {txt}")
        self.search_view.setHtml(head + ("<br>".join(rows) if rows else "<i>Ничего не найдено</i>"))

    CONSOLE_COLORS = {"Error": "#ff6b6b", "Warning": "#f0a030", "Reply": "#7fd1ff", "Cmd": "#9be37f"}

    def _console_html(self, ts, typ, text):
        color = self.CONSOLE_COLORS.get(typ, "#d4d4d4")
        return (f"<span style='color:#666'>{time.strftime('%H:%M:%S', time.localtime(ts))}</span> "
                f"<span style='color:{color};white-space:pre-wrap'>{html.escape(text)}</span>")

    def on_console_lines(self, items):
        flt = self.console_filter.text().strip().lower()
        for ts, typ, text in items:
            self.console_buf.append((ts, typ, text))
            if not flt or flt in text.lower():
                self._console_pending.append((ts, typ, text))
        if self._console_pending and not self._console_timer.isActive():
            self._console_timer.start(150)             # копим строки и выводим пачкой

    def flush_console(self):
        items, self._console_pending = self._console_pending, []
        if not items:
            return
        self.console_view.setUpdatesEnabled(False)
        for ts, typ, text in items:
            self.console_view.append(self._console_html(ts, typ, text))
        self.console_view.setUpdatesEnabled(True)
        if self.console_scroll.isChecked():
            sb = self.console_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def render_console(self, *_):
        flt = self.console_filter.text().strip().lower()
        self._console_pending = []
        self.console_view.clear()
        self.console_view.setHtml("<br>".join(
            self._console_html(ts, typ, text) for ts, typ, text in self.console_buf
            if not flt or flt in text.lower()))
        if self.console_scroll.isChecked():
            sb = self.console_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def clear_console(self):
        self.console_buf.clear()
        self._console_pending = []
        self.console_view.clear()

    def send_console_command(self):
        cmd = self.console_input.text().strip()
        if not cmd:
            return
        self.console_input.push(cmd)
        self.console_input.clear()
        if not (self.chat_worker and self.chat_worker.isRunning()):
            self.on_console_lines([(time.time(), "Error", "Нет подключения к серверу — команда не отправлена")])
            return
        self.chat_worker.send_command(cmd)

    def on_chat(self, entries):
        sb = self.chat_view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        for e in entries:
            if e.get("channel") == 1:                      # чат команды — в базу (привязка к команде по составу)
                self.log_store.add_team_chat(e["time"], e["user_id"], e["name"], e["text"])
            if chat_file_line(e) in self._chat_hist:      # уже показано из архива при запуске
                continue
            self.chat_view.append(self.format_chat(e))
        if at_bottom:
            sb.setValue(sb.maximum())

    def on_chat_status(self, text):
        if text != self._chat_status_last:  # не спамим одинаковыми ошибками
            self._chat_status_last = text
            self.chat_view.append(f"<i style='color:#f0a030'>{html.escape(text)}</i>")
