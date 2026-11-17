"""Rust Live Map: модуль heat."""

import math
import time



# ============================================================
# HEATMAP: клетки, цвета
# ============================================================

HEAT_CELL = 25.0          # размер клетки, м (укрупнять можно целым числом клеток)
HEAT_INTERVAL = 10.0      # как часто снимаем позиции игроков, сек
HEAT_FLUSH = 60.0         # как часто сбрасываем накопленное в БД, сек
HEAT_MOVE_EPS = 3.0       # сдвиг больше этого за интервал = «в движении», м


def heat_cell(x, z, world):
    # индексы клетки: начало отсчёта — юго-западный угол карты, z растёт на север
    return int((x + world / 2) // HEAT_CELL), int((z + world / 2) // HEAT_CELL)


def heat_dims(world):
    n = int(-(-float(world) // HEAT_CELL))
    return n, n


HEAT_FLOOR_MOVE = 40.0    # «красное» = не меньше стольких (сглаженных) отсчётов: мало данных — карта не краснеет
HEAT_FLOOR_PVP = 1.5      # ... и не меньше стольких убийств в одном месте
HEAT_FLOOR_PLAYER = 12.0  # карта одного игрока: красный — от стольких отсчётов в клетке
HEAT_MIN_LEVEL = 0.06     # слабее — не рисуем
HEAT_CELL_CAP = 20        # один игрок за день в одной клетке даёт не больше стольких отсчётов (~3 мин)


def heat_levels(cells, gx, gz, floor=HEAT_FLOOR_MOVE):
    """{(cx, cz): значение} -> {(cx, cz): уровень 0..1}. Сглаживание 3×3 и логарифмическая шкала
    от нуля до max(самая горячая клетка, floor): красный — только по-настоящему популярное место,
    а редкие клетки остаются синими и не становятся красными просто как «лучшие из слабых»."""
    if not cells:
        return {}
    w = {(-1, -1): 1, (0, -1): 2, (1, -1): 1, (-1, 0): 2, (0, 0): 4, (1, 0): 2, (-1, 1): 1, (0, 1): 2, (1, 1): 1}
    out = {}
    for (cx, cz), v in cells.items():
        if not (0 <= cx < gx and 0 <= cz < gz):
            continue
        for (dx, dz), k in w.items():
            key = (cx + dx, cz + dz)
            if 0 <= key[0] < gx and 0 <= key[1] < gz:
                out[key] = out.get(key, 0.0) + v * k / 16.0
    if not out:
        return {}
    ref = math.log1p(max(max(out.values()), float(floor)))
    res = {}
    for k, v in out.items():
        t = math.log1p(v) / ref
        if t >= HEAT_MIN_LEVEL:
            res[k] = min(1.0, t)
    return res


_HEAT_STOPS = ((0.0, (40, 80, 255)), (0.25, (0, 200, 255)), (0.5, (60, 220, 80)),
               (0.75, (255, 220, 0)), (1.0, (255, 40, 20)))


def heat_color(t):
    """уровень 0..1 -> (r, g, b, a): синий (мало) -> зелёный -> жёлтый -> красный (много)."""
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(_HEAT_STOPS, _HEAT_STOPS[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r, g, b = (int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
            return r, g, b, int(70 + 150 * t)
    return 255, 40, 20, 220


def day_int(ts):
    return int(time.strftime("%Y%m%d", time.localtime(ts)))
