"""Rust Live Map: модуль config."""

import copy
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

from . import vault



if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
SERVERS_DIR = DATA_DIR / "servers"       # data/servers/<id профиля>/ — всё, что относится к одному серверу
ARCHIVE_DIR = DATA_DIR / "archive"       # удалённые профили уезжают сюда, а не стираются
CONFIG_FILE = BASE_DIR / "config.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SERVERS_DIR.mkdir(parents=True, exist_ok=True)

_ACTIVE = {"id": "default"}


def set_active_profile(pid):
    _ACTIVE["id"] = pid or "default"
    pdir().mkdir(parents=True, exist_ok=True)


def pdir():
    return SERVERS_DIR / _ACTIVE["id"]


def map_output():
    return pdir() / "map.png"


def render_dir():
    return pdir() / "render"


def console_dir():
    return pdir() / "console"      # data/<сервер>/console/ГГГГ-ММ-ДД.txt — всё, что писал сервер в консоль


def chat_dir():
    return pdir() / "chat"         # data/<сервер>/chat/ГГГГ-ММ-ДД.txt — архив игрового чата


def maps_dir():
    return pdir() / "maps"               # архив карт всех вайпов: map_<размер>_<сид>.png


def db_path():
    return pdir() / "stats2.db"          # схема v2: типизированные таблицы


# Параметры сбора не настраиваются: всегда включены и зафиксированы (старые значения в config.json игнорируются).
FIXED_CONFIG = {
    "update_interval": 1.0,         # опрос игроков, сек
    "players_command": "playerlist",
    "bases_interval": 60,           # опрос шкафов, сек
    "chat_enabled": True,           # игровой чат
    "live_stats": True,             # статистика из консоли RCON
    "heat_enabled": True,           # позиции игроков для Heatmap
    "teams_interval": 300,          # состав команд (global.teaminfo), сек; 0 = не опрашивать
}

DEFAULT_CONFIG = {
    "theme": "modern",     # "modern" — оформление со стилями; "classic" — как раньше (на случай проблем с отображением)
    "rcon_host": "127.0.0.1",
    "rcon_port": 28016,
    "rcon_password": "",
    "world_size": 3750,
    "update_interval": 1.0,
    # Команда, которая возвращает JSON со списком игроков.
    # "playerlist" — ванильная; "livemap.players" — команда из плагина LiveMapData.cs
    "players_command": "playerlist",
    "map_path": "",
    "show_names": True,
    "show_hp": True,
    "show_bases": True,
    "show_players": True,
    "show_grid": False,
    "bases_interval": 60,   # секунд; 0 = не опрашивать шкафы
    "chat_enabled": True,   # читать игровой чат (chat.tail + push-сообщения)
    "live_stats": True,     # копить статистику (убийства, смерти, сессии) из консоли RCON
    "heat_enabled": True,   # копить позиции игроков для Heatmap «Передвижение»
    "heat_mode": "off",     # слой на карте: off | move (передвижение) | pvp (убийства игрок-игрок)
    "marks_cats": ["pvp", "self", "pve"],   # какие события показывать на карте
    "show_marks": True,     # ✕ убийства/смерти выбранного игрока на карте
    "map_seed": "",         # сид вайпа, для которого загружена карта
    "srv_dir": "",          # папка сервера (identity) с файлом карты .map: локальная или на FTP/SFTP
    "srv_proto": "",        # доступ к файлам сервера: "local" | "ftp" | "sftp" ("" — по старой галочке srv_ftp)
    "srv_ftp": False,       # устарело: то же, что srv_proto == "ftp"
    "srv_root": "",         # корень сервера для вкладки «Файлы сервера» (пусто — папка identity)
    "ssh_key": "",          # файл закрытого ключа для SFTP
    "ssh_hostkey": "",      # отпечаток ключа SSH-сервера (запоминается при первом подключении)
    "ftp_host": "",
    "ftp_port": 21,
    "ftp_user": "",
    "ftp_pass": "",
    "ftp_tls": False,       # FTPS (явный TLS)
    "show_monuments": False,  # подписи монументов на карте (правый клик по карте)
    "tasks": [],            # задания: команды в консоль по расписанию (у каждого сервера свои)
    "profiles": [],         # профили серверов: [{id, name, settings{...}}]
    "active_profile": "",
}

# настройки, которые принадлежат конкретному серверу (остальное — общие настройки интерфейса)
PROFILE_KEYS = ("rcon_host", "rcon_port", "rcon_password", "world_size", "map_path", "map_seed",
                "srv_dir", "srv_proto", "srv_ftp", "srv_root", "ssh_key", "ssh_hostkey",
                "ftp_host", "ftp_port", "ftp_user", "ftp_pass", "ftp_tls", "tasks")


def new_profile(name, settings=None):
    s = {k: copy.deepcopy(DEFAULT_CONFIG[k]) for k in PROFILE_KEYS}
    if settings:
        s.update(settings)
    return {"id": uuid.uuid4().hex[:8], "name": name, "settings": s}


def stash_profile(cfg):
    # текущие «серверные» настройки -> в запись активного профиля
    for p in cfg.get("profiles") or []:
        if p["id"] == cfg.get("active_profile"):
            p["settings"] = {k: copy.deepcopy(cfg.get(k, DEFAULT_CONFIG[k])) for k in PROFILE_KEYS}


def load_profile(cfg, pid):
    for p in cfg.get("profiles") or []:
        if p["id"] == pid:
            cfg["active_profile"] = pid
            for k in PROFILE_KEYS:
                cfg[k] = copy.deepcopy(p["settings"].get(k, DEFAULT_CONFIG[k]))
            return p
    return None


def migrate_legacy(pid):
    # файлы старой версии (общие на всё приложение) -> в папку первого профиля
    set_active_profile(pid)
    for name, dst in (("map.png", pdir() / "map.png"),):
        src_f = DATA_DIR / name
        if src_f.exists() and not dst.exists():
            try:
                shutil.move(str(src_f), str(dst))
            except OSError as e:
                print(f"Миграция {name}: {e}")
    legacy = pdir() / "legacy"
    for name in ("log_stats.json", "bases_state.json"):
        src_f = DATA_DIR / name
        if src_f.exists():
            legacy.mkdir(exist_ok=True)
            try:
                shutil.move(str(src_f), str(legacy / name))
            except OSError as e:
                print(f"Миграция {name}: {e}")


def ensure_profiles(cfg):
    profs = cfg.get("profiles")
    if not isinstance(profs, list) or not profs:
        p = new_profile("Сервер 1", {k: cfg[k] for k in PROFILE_KEYS if k in cfg})
        p["settings"]["map_seed"] = ""
        cfg["profiles"] = [p]
        cfg["active_profile"] = p["id"]
        migrate_legacy(p["id"])
    if load_profile(cfg, cfg.get("active_profile")) is None:
        load_profile(cfg, cfg["profiles"][0]["id"])
    set_active_profile(cfg["active_profile"])


_SECRETS = set()


def register_secrets(cfg):
    """Запоминает пароли (и их URL-вариант), чтобы log_error никогда не писал их в error.log."""
    from urllib.parse import quote
    for d in vault._dicts(cfg):
        for k in vault.SECRET_KEYS:
            v = d.get(k)
            if isinstance(v, str) and len(v) >= 3 and not vault.is_sealed(v):
                _SECRETS.update((v, quote(v, safe="")))


def scrub(text):
    text = str(text)
    for sec in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(sec, "***")
    return text


def log_error(text):
    """Дописывает строку в error.log рядом с программой (окно без консоли: иначе ошибок не видно)."""
    text = scrub(text)
    try:
        with open(BASE_DIR / "error.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + str(text).rstrip() + "\n")
    except OSError:
        pass


CONFIG_BAK = CONFIG_FILE.with_name(CONFIG_FILE.name + ".bak")


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    for path in (CONFIG_FILE, CONFIG_BAK):          # основной файл, при порче — предыдущая копия
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if "show_hp" not in raw and "show_details" in raw:  # старое имя настройки
                raw["show_hp"] = raw["show_details"]
            raw.pop("show_details", None)
            cfg.update(raw)
            break
        except Exception as e:
            log_error(f"Не удалось прочитать {path.name}: {e}")
            if path == CONFIG_FILE:                  # испорченный файл не затираем молча
                try:
                    shutil.copy2(path, path.with_name("config.corrupt.json"))
                except OSError:
                    pass
    cfg.update(FIXED_CONFIG)
    ensure_profiles(cfg)
    plain, failed = vault.open_secrets(cfg)
    register_secrets(cfg)          # пароли в файле зашифрованы — открываем их в памяти
    if failed:
        log_error("Пароли в config.json не удалось расшифровать (файл с другого компьютера или учётной записи "
                  "Windows): введите их в Настройках заново.")
    if plain and vault.available():                  # старый конфиг с открытыми паролями — сразу шифруем
        save_config(cfg)
    return cfg


def _file_has_plain(path):
    try:
        return vault.has_plain(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return False


def save_config(cfg):
    try:
        stash_profile(cfg)
        register_secrets(cfg)
        tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
        tmp.write_text(json.dumps(vault.sealed_copy(cfg), indent=4, ensure_ascii=False), encoding="utf-8")
        # копию с открытыми паролями не оставляем: .bak из такого файла не делаем, а старую удаляем
        if vault.available():
            for f in (CONFIG_BAK, CONFIG_FILE):
                if f.exists() and _file_has_plain(f):
                    if f is CONFIG_BAK:
                        f.unlink(missing_ok=True)
        if CONFIG_FILE.exists() and not (vault.available() and _file_has_plain(CONFIG_FILE)):
            shutil.copy2(CONFIG_FILE, CONFIG_BAK)
        tmp.replace(CONFIG_FILE)                     # подмена файла целиком: обрыв записи его не портит
    except Exception as e:
        log_error(f"Ошибка записи config.json: {e}")


def mapsrc_dir():
    return pdir() / "mapsrc"             # копии .map, взятые из папки сервера
