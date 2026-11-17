"""Rust Live Map: где взять файл карты .map — папка сервера (локально или по FTP). Без Qt, только стандартная библиотека."""

import os
import re
from pathlib import Path

from .fsbackend import make_backend

RE_MAP = re.compile(r"\.(\d{3,5})\.(\d+)\.(\d+)\.map$", re.IGNORECASE)    # proceduralmap.<размер>.<сид>.<версия>.map
KEEP_CACHED = 3                                                           # сколько последних .map держать в кэше


def parse_map_name(name):
    """proceduralmap.3750.12345.263.map -> (размер мира, сид, версия) или None."""
    m = RE_MAP.search(name)
    return (int(m.group(1)), m.group(2), int(m.group(3))) if m else None


class MapNotReady(FileNotFoundError):
    """В папке сервера ещё нет карты текущего вайпа (появится после перезапуска сервера)."""


def pick_map(entries, seed=None, strict=False):
    """entries: [(имя, время изменения, размер)]. Карта текущего сида, иначе самая свежая. None, если .map нет.
    strict=True: если сид известен, а среди файлов есть карты с другими сидами — карты текущего вайпа ещё нет (None),
    чужую (прошлого вайпа) не подсовываем: у неё может быть другой размер мира."""
    maps = [e for e in entries if e[0].lower().endswith(".map")]
    if not maps:
        return None
    if seed:
        for e in sorted(maps, key=lambda e: (-e[1], e[0])):
            info = parse_map_name(e[0])
            if info and info[1] == str(seed):
                return e
        if strict and any(parse_map_name(e[0]) for e in maps):
            return None
    return max(maps, key=lambda e: (e[1], e[0]))


def map_entries(fs, folder):
    """Файлы .map: в самой папке, в её подпапках и в server/<identity> (корень сервера Rust):
    [(путь относительно folder, время, размер)]."""
    def maps_in(rel):
        path = fs.join(folder, *rel.split("/")) if rel else folder
        dirs, found = [], []
        for e in fs.listdir(path):
            if e.is_dir:
                dirs.append(e.name)
            elif e.name.lower().endswith(".map"):
                found.append(((rel + "/" if rel else "") + e.name, e.mtime, e.size))
        return dirs, found

    dirs, out = maps_in("")
    if out:
        return out
    for d in dirs[:20]:
        try:
            sub, found = maps_in(d)
            out += found
            if d.lower() == "server":                 # …/server/my_server_identity/proceduralmap.*.map
                for d2 in sub[:20]:
                    out += maps_in(d + "/" + d2)[1]
        except Exception:
            continue
    return out


def trim_cache(folder, keep=KEEP_CACHED):
    """Оставляет в кэше только последние карты: каждая весит десятки мегабайт."""
    try:
        files = sorted(Path(folder).glob("*.map"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep:]:
            p.unlink()
    except OSError:
        pass


def fetch_map(cfg, cache_dir, seed=None, status=lambda s: None, info=None, strict=False):
    """Находит нужный .map в папке сервера (локально, FTP или SFTP) и кладёт копию в cache_dir.
    Возвращает путь к копии; в info["host_fp"] — отпечаток SSH-ключа сервера. Исключение — с понятным текстом."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    folder = (cfg.get("srv_dir") or "").strip()
    if not folder:
        raise ValueError("Не указана папка сервера.")
    status("Подключение к серверу…")
    with make_backend(cfg) as fs:
        if info is not None:
            info["host_fp"] = fs.host_fp
        ents = map_entries(fs, folder)
        e = pick_map(ents, seed, strict)
        if not e:
            if ents:
                raise MapNotReady(f"В папке сервера пока нет карты текущего вайпа (сид {seed}): она появится после "
                                  "перезапуска сервера.")
            raise FileNotFoundError(f"В папке {folder} нет файлов .map")
        name = e[0].rsplit("/", 1)[-1]
        dst = cache / name
        if not (dst.exists() and e[2] and dst.stat().st_size == e[2]):
            status(f"Скачиваю {name}…")
            tmp = cache / (name + ".part")
            fs.download(fs.join(folder, *e[0].split("/")), str(tmp))
            os.replace(tmp, dst)
    trim_cache(cache)
    return dst


def _locate(fs, root, server_path, depth=2, cap=150):
    """Где на доступном нам диске лежит файл, путь к которому назвал сервер (путь на сервере и у нас могут различаться)."""
    norm = server_path.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    r = root.replace("\\", "/").rstrip("/")
    if r and norm.casefold().startswith(r.casefold() + "/"):
        cand = fs.join(root, *norm[len(r) + 1:].split("/"))
        if fs.exists(cand):
            return cand
    budget = [cap]

    def walk(path, d):
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        try:
            items = fs.listdir(path)
        except Exception:
            return None
        for e in items:
            if not e.is_dir and e.name.casefold() == base.casefold():
                return fs.join(path, e.name)
        if d > 0:
            for e in items:
                if e.is_dir:
                    hit = walk(fs.join(path, e.name), d - 1)
                    if hit:
                        return hit
        return None

    start = root
    for _ in range(3):                                   # папка сервера, затем на уровень выше, и ещё выше
        hit = walk(start, depth)
        if hit:
            return hit
        parent = fs.parent(start)
        if parent == start:
            break
        start = parent
    return None


def fetch_render_png(cfg, server_path, dst):
    """PNG, который сервер сохранил командой world.rendermap, но с этого компьютера по тому же пути не виден:
    забираем его через настроенный доступ (SMB, FTP, SFTP). Возвращает True, если файл найден и скачан."""
    root = (cfg.get("srv_root") or cfg.get("srv_dir") or "").strip()
    if not root:
        return False
    with make_backend(cfg) as fs:
        found = _locate(fs, root, server_path)
        if not found:
            return False
        tmp = Path(str(dst) + ".part")
        fs.download(found, str(tmp))
        os.replace(tmp, dst)
        return True
