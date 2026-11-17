"""Rust Live Map: модуль events."""

import datetime
import re
from collections import namedtuple



# ============================================================
# ЛОГИ СЕРВЕРА (убийства, смерти, сессии)
# ============================================================

# строки консоли без этих подстрок пропускаются сразу
LIVE_MARKERS = (" was killed by ", " died (", " joined [", " joined from ip ", " disconnecting: ",
                " has spawned", "[event] ", "Saved ", "Kicking ", "Server startup complete",
                "BradleyAPC Spawned", "proceduralmap.", "[PlayerReport] ")
LIVE_MARKERS_RE = re.compile("|".join(re.escape(m) for m in LIVE_MARKERS))

RE_KILL = re.compile(
    r"^(.+)\[(\d+)\] was killed by (.+?)"
    r"(?: at \((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\))?$")
RE_REPORT = re.compile(
    r"^\[PlayerReport\] (.+?)\[(\d{17})\] reported (.+?)\[(\d{17})\](?: - \"(.*)\")?\s*$")
RE_REPORT_TYPE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
REPORT_NAMES = {"cheat": "читы", "abusive": "оскорбления", "spam": "спам", "name": "ник"}
RE_DIED = re.compile(r"^(.+)\[(\d+)\] died \((.+)\)$")
RE_JOIN = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+/(\d{17})/(.*?) joined \[")
RE_DISC = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+/(\d{17})/(.*?) disconnecting: (.*)$")
RE_SPAWN = re.compile(r"^(.+)\[(\d{17})\] has spawned$")
RE_ATT_PLAYER = re.compile(r"^(.+)\[(\d{17})\]$")

def is_private_ip(ip):
    # локальные/VPN-адреса (общий 192.168.x.x у всех) не годятся для поиска альтов
    if ip.startswith(("10.", "192.168.", "127.")):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except ValueError:
            return False
    return False


RE_JOIN2 = re.compile(
    r"^(.+) with steamid (\d{17}) joined from ip (\d{1,3}(?:\.\d{1,3}){3}):\d+ with platform")
RE_EAC = re.compile(r"^\[EAC\] Kicking (\d{17}) / (.+?) \((.+)\)$")
RE_KICK = re.compile(r"^Kicking (\d{1,3}(?:\.\d{1,3}){3}):\d+/(\d{17})/(.*?) \((.+)\)$")
RE_NPC_ATT = re.compile(r"^(.+)\[(\d+)\]$")
RE_ANIMAL = re.compile(r"^(.+) \((.+)\)$")

# три группы событий — три иконки (на карте и в списке)
CAT_STYLE = {
    "pvp": ("⚔", "#ff4d4d"),    # убит / убил игрока
    "self": ("💀", "#b07cff"),   # суицид, голод, жажда, падение, холод и т.п.
    "pve": ("🐺", "#ff9f43"),    # животные, учёные (NPC), вертолёт, техника
}
ENV_NAMES = {
    "Suicide": "суицид", "Hunger": "голод", "Thirst": "жажда", "Fall": "падение", "Cold": "холод",
    "Heat": "жара", "Drowned": "утонул", "Bleeding": "кровотечение", "Radiation": "радиация",
    "Poison": "яд", "Generic": "прочее",
}
PVE_NAMES = {
    "Scientist": "учёный", "Bear": "медведь", "Polarbear": "белый медведь", "Wolf": "волк", "Boar": "кабан",
    "Stag": "олень", "Cow": "бык/корова", "Horse": "лошадь", "Chicken": "курица", "Crocodile": "крокодил",
    "Panther": "пантера", "Tiger": "тигр", "Snake": "змея", "Shark": "акула",
    "patrolhelicopter": "патрульный вертолёт", "ch47scientists.entity": "Chinook", "napalm": "напалм",
    "bradleyapc": "Bradley APC",
}


def classify_attacker(att):
    # что убило игрока, если это не другой игрок: ("npc"|"entity"|"animal"|"env", название)
    m = RE_NPC_ATT.match(att)
    if m:
        return "npc", m.group(1)            # Scientist[139]
    if att.endswith(" (entity)"):
        return "entity", att[:-9]           # patrolhelicopter (entity)
    m = RE_ANIMAL.match(att)
    if m:
        return "animal", m.group(2)         # bull (Cow) -> Cow
    return "env", att                       # Cold, Hunger, Fall...


def event_info(kind, oname, extra):
    # -> (категория, подпись без имени игрока)
    if kind == "kill":
        return "pvp", "убил"
    if kind == "death":
        return "pvp", "убит игроком"
    if kind == "suicide":
        return "self", "суицид"
    if kind in ("env", "died"):
        return "self", ENV_NAMES.get(extra, extra) or "смерть"
    if kind == "animal":
        return "pve", "животное: " + PVE_NAMES.get(oname, oname)
    if kind == "npc":
        return "pve", "NPC: " + PVE_NAMES.get(oname, oname)
    return "pve", PVE_NAMES.get(oname, oname)   # entity и старые записи кэша


RE_SAVED = re.compile(r"^Saved ([\d,]+) ents")

EVENT_NAMES = (
    ("cargoship", "Грузовой корабль"), ("cargo_plane", "Грузовой самолёт"),
    ("ch47", "Chinook CH-47"), ("patrolhelicopter", "Патрульный вертолёт"),
    ("bradley", "Bradley APC"), ("supply", "Аирдроп"),
    ("roaming boat group", "Группа лодок (спавн)"),    # «Spawning roaming boat group at (x, y, z), heading to (...)»
)


def event_type(prefab):
    low = prefab.lower()
    for key, title in EVENT_NAMES:
        if key in low:
            return title
    name = prefab.rsplit("/", 1)[-1].replace(".prefab", "")
    return name.split(" at (")[0].strip() or name        # координаты в названии события не нужны


def sparkline(values):
    if not values:
        return ""
    chars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return chars[3] * len(values)
    return "".join(chars[int((v - lo) / (hi - lo) * 7)] for v in values)


def new_player():
    return {"names": [], "ips": {}, "first": None, "last": None,
            "sessions": 0, "playtime": 0, "open": None, "reason": "", "spawns": 0,
            "kills": 0, "deaths_pvp": 0, "npc": 0, "npc_types": {},
            "suicides": 0, "ent_deaths": {}, "other_deaths": {},
            "killers": {}, "victims": {}, "events": [],
            # расширенная статистика
            "hours": [0.0] * 24, "max_session": 0, "reasons": {},
            "spawn_at": None, "life_sum": 0.0, "life_n": 0, "life_max": 0.0,
            "streak": 0, "best_streak": 0, "quick_suic": 0, "kicks": 0,
            "reports_in": 0, "reports_out": 0, "report_types": {}, "reporters": {}, "reports": []}


# ============================================================
# ХРАНИЛИЩЕ СТАТИСТИКИ (SQLite, отдельная БД на каждый профиль сервера)
# ============================================================

RE_SEED = re.compile(r"proceduralmap\.(\d+)\.(-?\d+)\.\d+\.navmesh")
DB_SCHEMA = 3
BACKUPS_KEEP = 7


def first_thursday(y, m):
    d = datetime.date(y, m, 1)
    return d + datetime.timedelta(days=(3 - d.weekday()) % 7)


def wipe_month(ts):
    # «месяц вайпа»: вайп — первый четверг месяца, до него ещё идёт вайп прошлого месяца
    d = datetime.datetime.fromtimestamp(ts)
    if d.date() < first_thursday(d.year, d.month):
        return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return (d.year, d.month)


Ev = namedtuple("Ev", "kind a b a_name b_name x y z cat cause ip num")


def _ev(kind, **kw):
    d = dict.fromkeys(Ev._fields)
    d["kind"] = kind
    d.update(kw)
    return Ev(**d)


def parse_line(msg):
    """Строка консоли -> Ev или None. Один раз разбираем при приёме, дальше работаем с колонками.
    a — «главный» игрок (жертва / вошедший), b — второй (убийца); a_name/b_name — их ники.
    cat — кто убил игрока: player | suicide | npc | entity | animal | env; pve — игрок убил NPC/животное.
    cause — подпись причины (тип NPC, среда, причина выхода/кика, тип ивента)."""
    if msg.startswith("[PlayerReport] "):
        m = RE_REPORT.match(msg)
        if not m:
            return None
        rname, rid, tname, tid, subj = m.groups()
        tm = RE_REPORT_TYPE.match(subj or "")
        typ, text = (tm.group(1).lower(), tm.group(2)) if tm else ("?", subj or "")
        # a — на кого пожаловались, b — кто пожаловался
        return _ev("report", a=tid, a_name=tname, b=rid, b_name=rname, cat=typ[:20], cause=text[:80])
    if msg.startswith("[event] "):
        return _ev("event", cause=event_type(msg[8:].strip()))
    if msg.startswith("Saved "):
        m = RE_SAVED.match(msg)
        return _ev("saved", num=int(m.group(1).replace(",", ""))) if m else None
    if msg.startswith("Server startup complete"):
        return _ev("startup")
    if msg.startswith("BradleyAPC Spawned"):
        return _ev("bradley")
    if msg.startswith("[EAC] Kicking ") or msg.startswith("Kicking "):
        m = RE_EAC.match(msg)
        if m:
            sid, name, reason = m.groups()
        else:
            m = RE_KICK.match(msg)
            if not m:
                return None
            _ip, sid, name, reason = m.groups()
        return _ev("kick", a=sid, a_name=name, cause=reason)
    if " was killed by " in msg:
        m = RE_KILL.match(msg)
        if not m:
            return None
        vname, vid, att, x, y, z = m.groups()
        am = RE_ATT_PLAYER.match(att)
        a = vid if len(vid) == 17 else None
        b = am.group(2) if am else None
        if a is None and b is None:
            return None  # зверь убил зверя и т.п. — не интересно
        x = float(x) if x else None
        y = float(y) if y else None
        z = float(z) if z else None
        bname = am.group(1) if am else None
        if a is None:                       # игрок убил NPC / животное
            cat, cause = "pve", None
        elif am and b != a:                 # PvP
            cat, cause = "player", None
        elif att == "Suicide" or am:
            cat, cause = "suicide", None
        else:
            cat, cause = classify_attacker(att)   # npc | entity | animal | env
        return _ev("kill", a=a, b=b, a_name=vname, b_name=bname, x=x, y=y, z=z, cat=cat, cause=cause)
    if " died (" in msg:
        m = RE_DIED.match(msg)
        if m and len(m.group(2)) == 17:
            return _ev("died", a=m.group(2), a_name=m.group(1), cat="env", cause=m.group(3))
        return None
    if " joined [" in msg:
        m = RE_JOIN.match(msg)
        if m:
            ip, sid, name = m.groups()
            return _ev("join", a=sid, a_name=name, ip=ip)
        return None
    if " joined from ip " in msg:
        m = RE_JOIN2.match(msg)
        if m:
            name, sid, ip = m.groups()
            return _ev("join", a=sid, a_name=name, ip=ip)
        return None
    if " disconnecting: " in msg:
        m = RE_DISC.match(msg)
        if m:
            ip, sid, name, reason = m.groups()
            return _ev("disc", a=sid, a_name=name, ip=ip, cause=reason)
        return None
    if msg.endswith(" has spawned"):
        m = RE_SPAWN.match(msg)
        if m:
            return _ev("spawn", a=m.group(2), a_name=m.group(1))
    return None
