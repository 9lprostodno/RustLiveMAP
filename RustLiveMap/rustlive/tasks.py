"""Rust Live Map: задания — команды в консоль сервера по расписанию (чистая логика, без Qt).

Выполняются только пока программа запущена и подключена; пропущенное за время простоя не догоняется.
Время — часы компьютера, где запущена программа."""

import datetime
import re
import time
import uuid

KINDS = {"daily": "Каждый день", "once": "Один раз", "period": "Периодически"}
MAX_LATE = 30          # окно, дольше которого опоздавший запуск считается пропущенным (сон ПК, зависание), сек


def new_task(kind, cmds, *, hhmm="00:00", at=0.0, every=3600, now=None):
    now = time.time() if now is None else now
    return {"id": uuid.uuid4().hex[:8], "kind": kind, "cmds": cmds.strip(), "enabled": True,
            "time": hhmm, "at": float(at), "every": int(every), "anchor": now,
            "last": 0.0, "status": "", "done": False}


def split_cmds(text):
    """«say a; restart 60» -> ["say a", "restart 60"]"""
    return [c.strip() for c in str(text or "").split(";") if c.strip()]


def expand_cmds(text, players):
    return [c.replace("${playerCount}", str(int(players))) for c in split_cmds(text)]


def _hhmm(s):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)), int(m.group(2))


def _at_day(day, hm):
    return datetime.datetime(day.year, day.month, day.day, hm[0], hm[1]).timestamp()


def due(t, prev, now):
    """Наступил ли срок задания в окне (prev, now]. Пропущенное раньше окна не выполняется."""
    if not t.get("enabled", True) or t.get("done"):
        return False
    k = t.get("kind")
    if k == "once":
        return prev < float(t.get("at") or 0) <= now
    if k == "daily":
        hm = _hhmm(t.get("time"))
        if not hm:
            return False
        d0 = datetime.date.fromtimestamp(prev)
        d1 = datetime.date.fromtimestamp(now)
        day = d0
        while day <= d1:
            if prev < _at_day(day, hm) <= now:
                return True
            day += datetime.timedelta(days=1)
        return False
    if k == "period":
        every = int(t.get("every") or 0)
        anchor = float(t.get("anchor") or 0)
        if every < 60 or now < anchor + every:
            return False
        n = int((now - anchor) // every)
        return prev < anchor + n * every <= now
    return False


def next_run(t, now):
    """Когда задание сработает в следующий раз (epoch) или None: пауза, выполнено, время прошло."""
    if not t.get("enabled", True) or t.get("done"):
        return None
    k = t.get("kind")
    if k == "once":
        at = float(t.get("at") or 0)
        return at if at > now else None
    if k == "daily":
        hm = _hhmm(t.get("time"))
        if not hm:
            return None
        day = datetime.date.fromtimestamp(now)
        for i in range(2):
            ts = _at_day(day + datetime.timedelta(days=i), hm)
            if ts > now:
                return ts
        return None
    if k == "period":
        every = int(t.get("every") or 0)
        if every < 60:
            return None
        anchor = float(t.get("anchor") or 0)
        n = max(1, int((now - anchor) // every) + 1)
        return anchor + n * every
    return None


def fmt_every(sec):
    sec = int(sec)
    h, m = divmod(sec // 60, 60)
    return f"{h} ч {m:02d} мин" if h else f"{m} мин"


def fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def when_text(t):
    k = t.get("kind")
    if k == "daily":
        return str(t.get("time") or "")
    if k == "once":
        return fmt_ts(t["at"]) if t.get("at") else "—"
    return "каждые " + fmt_every(t.get("every") or 0)
