"""Rust Live Map: модуль gamedata."""

import re



# ============================================================
# ВСПОМОГАТЕЛЬНОЕ
# ============================================================

GRID_CELL = 146.28  # размер квадрата сетки Rust, м

KNOWN_KEYS = {
    "steamid", "userid", "displayname", "name", "position", "health", "maxhealth",
    "ping", "connectedseconds", "address", "weapon", "activeitem", "activeweapon",
    "helditem", "rotation", "sleeping", "issleeping", "teamid", "team",
}

EXTRA_LABELS = {
    "calories": "Еда", "hydration": "Вода", "wounded": "Ранен",
    "currentlevel": "Уровень", "unspentxp": "Свободный XP",
    "violationlevel": "Violation level", "voiationlevel": "Violation level",
    "ownersteamid": "Owner SteamID", "entityid": "Entity ID", "ismuted": "Мут в чате",
}

TEAM_COLORS = ["#ff3b3b", "#3b9cff", "#39d353", "#ffb020", "#c26bff", "#20d0d0", "#ff6bb5", "#a0e030"]


def team_color(team):
    """Игроки одной команды получают один цвет. Без команды — красный."""
    if not team:
        return "#ff3b3b"
    return TEAM_COLORS[int(team) % len(TEAM_COLORS)]


def fix_mojibake(s):
    """Кириллица в UTF-8, ошибочно прочитанная как cp866 ('╨б╨╡╨╜╤П' -> 'Сеня')."""
    if "╨" in s or "╤" in s:
        try:
            return s.encode("cp866").decode("utf-8")
        except Exception:
            pass
    return s


def fmt_time(sec):
    if sec is None:
        return "—"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}ч {m}м" if h else f"{m}м {s}с"


def to_float(v):
    try:
        return float(v)
    except Exception:
        return None


def col_letters(col):
    letters, n = "", col
    while True:
        letters = chr(65 + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return letters


def grid_ref(x, z, world):
    col = int((x + world / 2) // GRID_CELL)
    row = int((world / 2 - z) // GRID_CELL)
    if col < 0 or row < 0:
        return "—"
    return f"{col_letters(col)}{row}"


# ============================================================
# ПАРСИНГ ОТВЕТОВ RCON
# ============================================================

def parse_position(low):
    pos = low.get("position")
    if isinstance(pos, dict):
        pl = {str(k).lower(): v for k, v in pos.items()}
        x, y, z = to_float(pl.get("x")), to_float(pl.get("y")), to_float(pl.get("z"))
        if x is not None and z is not None:
            return x, y or 0.0, z
    elif isinstance(pos, str):
        nums = re.findall(r"-?\d+(?:\.\d+)?", pos)
        if len(nums) >= 3:
            return float(nums[0]), float(nums[1]), float(nums[2])
    return None


def normalize_player(item):
    if not isinstance(item, dict):
        return None
    low = {str(k).lower(): v for k, v in item.items()}

    sid = low.get("steamid") or low.get("userid")
    if sid is None:
        return None

    pos = parse_position(low)
    if low.get("sleeping") or low.get("issleeping"):
        return None   # спящие не показываются

    extra = {k: v for k, v in low.items() if k not in KNOWN_KEYS and not isinstance(v, (dict, list))}

    team = low.get("teamid", low.get("team"))
    try:
        team = int(team) if team is not None else None
    except Exception:
        team = None

    return {
        "steam_id": str(sid),
        "name": fix_mojibake(str(low.get("displayname") or low.get("name") or "Unknown")),
        "x": pos[0] if pos else None,
        "y": pos[1] if pos else None,
        "z": pos[2] if pos else None,
        "health": to_float(low.get("health")),
        "max_health": to_float(low.get("maxhealth")) or 100.0,
        "ping": low.get("ping"),
        "connected": to_float(low.get("connectedseconds")),
        "address": low.get("address"),
        "rotation": to_float(low.get("rotation")),
        "team": team,
        "status": "online",
        "extra": extra,
    }


def parse_players(msg):
    if isinstance(msg, dict):
        msg = msg.get("players") or msg.get("Players") or []
    if not isinstance(msg, list):
        return []
    out = []
    for it in msg:
        p = normalize_player(it)
        if p:
            out.append(p)
    return out


# ============================================================
# БАЗЫ (шкафы)
# ============================================================

BASE_LINE = re.compile(
    r"^\s*(\d+)\s+\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s+(\d+)"
)


def parse_bases(msg):
    """server.listtoolcupboards -> [{entity_id, x, y, z, authed}].
    Основной формат — текстовая таблица (проверен на сервере); на всякий случай понимаем и --json."""
    res = []
    if isinstance(msg, list):
        for it in msg:
            if not isinstance(it, dict):
                continue
            low = {str(k).lower(): v for k, v in it.items()}
            pos = parse_position(low)
            eid = low.get("entityid")
            if pos is None or eid is None:
                continue
            authed = low.get("authed")
            authed = len(authed) if isinstance(authed, list) else int(to_float(authed) or 0)
            res.append({"entity_id": int(eid), "x": pos[0], "y": pos[1], "z": pos[2], "authed": authed})
        return res
    for line in str(msg or "").splitlines():
        m = BASE_LINE.match(line)
        if m:
            res.append({
                "entity_id": int(m.group(1)),
                "x": float(m.group(2)), "y": float(m.group(3)), "z": float(m.group(4)),
                "authed": int(m.group(5)),
            })
    return res
