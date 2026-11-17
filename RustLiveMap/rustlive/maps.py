"""Rust Live Map: модуль maps."""

import shutil
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from pathlib import Path

from .config import (
    map_output,
    maps_dir,
)


RENDER_MARGIN = 500       # м океана с каждой стороны на картинке world.rendermap (3750 м мира -> png 4750 px)
ARCHIVE_MAX_SIDE = 2048   # архивные карты прошлых вайпов ужимаем до этой стороны (0 = не ужимать)


# ============================================================
# АРХИВ КАРТ
# ============================================================

def map_file_name(world, seed):
    return f"map_{int(world)}_{seed}.png"


def archive_map_path(wipe, fallback_world=None):
    """Файл карты вайпа из архива или None."""
    seed = (wipe or {}).get("seed") or (f"w{wipe['id']}" if wipe and wipe.get("id") else None)
    if not seed:
        return None
    d = maps_dir()
    world = wipe.get("world") or fallback_world
    if world:
        p = d / map_file_name(world, seed)
        if p.exists():
            return p
    if d.is_dir():
        for p in sorted(d.glob(f"map_*_{seed}.png")):
            return p
    return None


def archive_current_map(seed, world, overwrite=False):
    """Кладёт текущую карту профиля в архив под сидом вайпа. Возвращает путь или None."""
    if not seed or not world or not map_output().exists():
        return None
    dst = maps_dir() / map_file_name(world, seed)
    if dst.exists() and not overwrite:
        return dst
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(map_output(), dst)
    except OSError as e:
        print(f"Архив карты: {e}")
        return None
    return dst


def shrink_archive(path, max_side=ARCHIVE_MAX_SIDE):
    """Ужимает архивную карту прошлого вайпа: для тепловых слоёв и меток деталей хватает."""
    if not max_side or not path or not Path(path).exists():
        return
    try:
        img = QImage(str(path))
        if img.isNull() or max(img.width(), img.height()) <= max_side:
            return
        small = img.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        tmp = Path(str(path) + ".tmp.png")
        if small.save(str(tmp), "PNG"):
            tmp.replace(path)
    except Exception as e:
        print(f"Сжатие архива карты: {e}")


STD_SCALES = (0.25, 0.5, 1, 2, 4)      # сколько пикселей на метр бывает у карты


def _fits_world(side, world):
    return any(abs(side - world * k) <= max(2, world * k * 0.005) for k in STD_SCALES)


def size_matches(side, world):
    """Сторона картинки (px) подходит миру: сам мир или мир с полями сервера (+2×500 м), в любом стандартном масштабе."""
    world = int(world)
    return world > 0 and (_fits_world(side, world) or _fits_world(side, world + 2 * RENDER_MARGIN))


def map_size_note(path, world):
    """Пустая строка, если размер картинки соответствует размеру мира; иначе — предупреждение для подписи карты."""
    try:
        img = QImage(str(path))
        world = int(world)
        if img.isNull() or world <= 0:
            return ""
        if img.width() == img.height() and _fits_world(img.width(), world):
            return ""
        return f" ⚠ картинка {img.width()}×{img.height()} не соответствует миру {world} м: точки могут сместиться"
    except Exception:
        return ""


def crop_render_margin(path, world, force=False):
    """Серверный world.rendermap рисует мир с океанскими полями (3750 м -> 4750 px), а координаты считаются
    по самому миру. Обрезает поля, чтобы картинка совпала с миром.
    force=True — картинка точно от сервера (поля есть всегда): поля вычисляются по размеру мира при любом масштабе.
    Без force обрезается только если размер в точности «мир + поля» (для чужих png). Повторный вызов безопасен."""
    try:
        world = int(world)
        if world <= 0 or not path or not Path(path).exists():
            return False
        img = QImage(str(path))
        if img.isNull() or img.width() != img.height():
            return False
        side = img.width()
        full = world + 2 * RENDER_MARGIN
        m = 0
        if force:
            if _fits_world(side, world):
                return False                       # поля уже срезаны
            m = round(side * RENDER_MARGIN / full)
        else:
            for k in STD_SCALES:
                if side == round(full * k):
                    m = round(RENDER_MARGIN * k)
        if m <= 0 or side - 2 * m <= 0:
            return False
        tmp = Path(str(path) + ".tmp.png")
        if img.copy(m, m, side - 2 * m, side - 2 * m).save(str(tmp), "PNG"):
            tmp.replace(path)
            return True
    except Exception as e:
        print(f"Обрезка полей карты: {e}")
    return False
