"""Rust Live Map: модуль periods."""

import datetime



# ============================================================
# ДИАЛОГ НАСТРОЕК
# ============================================================

def is_range_period(per):
    return isinstance(per, tuple) and per[0] == "range"


def is_wipe_period(per):
    return isinstance(per, tuple) and per[0] == "wipe"


def _month_end(d):
    return (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)


def shift_range(d1, d2, direction):
    """Сдвиг периода на такой же период: день -> день, неделя -> неделя, целый месяц -> месяц.
    direction: -1 — раньше, +1 — позже. Возвращает (начало, конец) включительно."""
    n = (d2 - d1).days + 1
    if n >= 28 and d1.day == 1 and d2 == _month_end(d1):
        nd1 = (d1 - datetime.timedelta(days=1)).replace(day=1) if direction < 0 else _month_end(d1) + datetime.timedelta(days=1)
        return nd1, _month_end(nd1)
    nd1 = d1 + datetime.timedelta(days=n * direction)
    return nd1, nd1 + datetime.timedelta(days=n - 1)


def fmt_date(d):
    return d.strftime("%d.%m.%Y")
