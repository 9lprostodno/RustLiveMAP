"""Rust Live Map: модуль chat."""

import json
import threading
import time
from collections import deque
from pathlib import Path

from .gamedata import (
    fix_mojibake,
    to_float,
)


def parse_chat(msg):
    # chat.tail отдаёт список, push-сообщение (Type = "Chat") — одну запись (dict или JSON-строку)
    if isinstance(msg, str):
        try:
            msg = json.loads(msg)
        except Exception:
            return []
    if isinstance(msg, dict):
        msg = [msg]
    if not isinstance(msg, list):
        return []
    out = []
    for it in msg:
        if not isinstance(it, dict):
            continue
        low = {str(k).lower(): v for k, v in it.items()}
        text = low.get("message")
        if text is None:
            continue
        t = to_float(low.get("time"))
        if t and t > 1e11:  # миллисекунды
            t /= 1000.0
        uid = str(low.get("userid") or "0")
        out.append({
            "key": (str(low.get("time")), uid, str(text)),
            "time": t if t else time.time(),
            "user_id": uid,
            "name": fix_mojibake(str(low.get("username") or "")),
            "text": fix_mojibake(str(text)),
            "color": str(low.get("color") or ""),
            "channel": int(to_float(low.get("channel")) or 0),
        })
    return out


class DayLog:
    """Дописывает строки в <папка>/ГГГГ-ММ-ДД.txt, каждый день — новый файл (бессрочно).
    dedup: не писать строку, которая уже есть среди последних записанных — после перезапуска программы
    chat.tail отдаёт старые сообщения ещё раз."""

    KEEP = 400

    def __init__(self, folder, dedup=False):
        self.folder = Path(folder)
        self.dedup = dedup
        self.lock = threading.Lock()
        self._day = None
        self._f = None
        self._recent = deque()
        self._set = set()

    def _open(self, day):
        self.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{day}.txt"
        self._recent.clear()
        self._set.clear()
        if self.dedup and path.exists():
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-self.KEEP:]:
                    self._recent.append(line)
                    self._set.add(line)
            except OSError:
                pass
        self._f = open(path, "a", encoding="utf-8", newline="\n")
        self._day = day

    def write(self, ts, line):
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        with self.lock:
            try:
                if day != self._day or self._f is None:
                    self._open(day)
                if self.dedup:
                    if line in self._set:
                        return False
                    if len(self._recent) >= self.KEEP:
                        self._set.discard(self._recent.popleft())
                    self._recent.append(line)
                    self._set.add(line)
                self._f.write(line + "\n")
                self._f.flush()
            except OSError:
                return False
        return True

    def close(self):
        if self._f:
            try:
                self._f.close()
            except OSError:
                pass
        self._f = None
        self._day = None


def chat_file_line(e):
    """Запись чата -> строка архива: «21:05:33 [Команда] Ник: текст»."""
    ch = {1: "[Команда] ", 2: "[Сервер] "}.get(e["channel"], "")
    text = " ".join(str(e["text"]).split())
    return f"{time.strftime('%H:%M:%S', time.localtime(e['time']))} {ch}{e['name'] or 'Сервер'}: {text}"


def _day_files(folder):
    try:
        return sorted(Path(folder).glob("????-??-??.txt"), reverse=True)     # новые первыми
    except OSError:
        return []


def tail_chat_lines(folder, n=150):
    """Последние n строк архива (с датой): [(ГГГГ-ММ-ДД, строка)] в хронологическом порядке."""
    out = []
    for f in _day_files(folder):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        out = [(f.stem, ln) for ln in lines] + out
        if len(out) >= n:
            break
    return out[-n:]


def search_chat_files(folder, query, limit=500):
    """Поиск слов (все слова должны встретиться, регистр не важен) по всему архиву.
    -> ([(день, строка)] новые первыми, есть_ли_ещё)"""
    words = [w for w in str(query).lower().split() if w]
    res = []
    if not words:
        return res, False
    for f in _day_files(folder):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in reversed(lines):
            low = ln.lower()
            if all(w in low for w in words):
                if len(res) >= limit:
                    return res, True
                res.append((f.stem, ln))
    return res, False
