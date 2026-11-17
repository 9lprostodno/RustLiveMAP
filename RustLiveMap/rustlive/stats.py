"""Rust Live Map: модуль stats."""

import bisect
import datetime
import sqlite3
import threading
import time
from pathlib import Path

from .config import (
    db_path,
)
from .teams import TEAMS_SQL, TeamsStore
from .events import (
    BACKUPS_KEEP,
    DB_SCHEMA,
    RE_SEED,
    is_private_ip,
    new_player,
    parse_line,
    wipe_month,
)
from .heat import (
    HEAT_CELL_CAP,
    HEAT_FLUSH,
    HEAT_INTERVAL,
    HEAT_MOVE_EPS,
    day_int,
    heat_cell,
    heat_dims,
)
from .periods import (
    fmt_date,
    is_range_period,
    is_wipe_period,
)


class Replay:
    # Пересчёт статистики игрока по его строкам из БД (колонки, без разбора текста).

    SELECT = "SELECT ts, wipe_id, kind, a, b, a_name, b_name, x, z, cat, cause, ip FROM events"

    def __init__(self, cur_wipe=None):
        self.players = {}
        self.cur_wipe = cur_wipe   # координаты показываем только у событий текущего вайпа (карта у каждого своя)

    # ---- вспомогательное ----
    def _player(self, sid):
        p = self.players.get(sid)
        if p is None:
            p = new_player()
            self.players[sid] = p
        return p

    @staticmethod
    def _touch(p, name, ts):
        if name:
            names = p["names"]
            if name in names:
                names.remove(name)
            names.append(name)
            del names[:-6]
        if p["first"] is None or ts < p["first"]:
            p["first"] = ts
        if p["last"] is None or ts > p["last"]:
            p["last"] = ts

    @staticmethod
    def _inc(d, key, n=1, cap=0):
        if cap and key not in d and len(d) >= cap:
            return
        d[key] = d.get(key, 0) + n

    @staticmethod
    def _event(p, ts, kind, oname, osid, x, z, extra=""):
        ev = p["events"]
        ev.append([ts, kind, oname, osid, x, z, extra])
        if len(ev) > 60:
            del ev[:-60]

    @staticmethod
    def _on_death(p, ts):
        # возвращает время жизни с последнего спавна (сек) или None
        life = None
        sa = p.get("spawn_at")
        if sa is not None:
            d = ts - sa
            if 0 < d < 6 * 3600:
                life = d
                p["life_sum"] += d
                p["life_n"] += 1
                if d > p["life_max"]:
                    p["life_max"] = d
        p["spawn_at"] = None
        p["streak"] = 0
        return life

    @staticmethod
    def _add_hours(p, start, end):
        # раскладываем сессию по часам суток (минуты) -> «пик активности»
        t, guard = start, 0
        while t < end and guard < 200:
            dt = datetime.datetime.fromtimestamp(t)
            nxt = (dt.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)).timestamp()
            seg = min(end, nxt) - t
            p["hours"][dt.hour] += seg / 60.0
            t = min(end, nxt)
            guard += 1

    def _join(self, ts, sid, name, ip):
        p = self._player(sid)
        self._touch(p, name, ts)
        p["sessions"] += 1
        p["ips"][ip] = ts
        if len(p["ips"]) > 8:
            for k in sorted(p["ips"], key=p["ips"].get)[:-8]:
                del p["ips"][k]
        p["open"] = ts
        p["spawn_at"] = None

    # ---- применение одной строки ----
    def apply(self, row):
        ts, wid, kind, a, b, an, bn, x, z, cat, cause, ip = row
        if wid != self.cur_wipe:
            x = z = None
        if kind == "report":              # a — на кого, b — от кого
            t = self._player(a)
            self._touch(t, an, ts)
            t["reports_in"] += 1
            self._inc(t["report_types"], cat or "?", cap=20)
            self._inc(t["reporters"], b, cap=50)
            t["reports"].append([ts, "in", bn, b, cat, cause])
            del t["reports"][:-30]
            if b:
                r = self._player(b)
                self._touch(r, bn, ts)
                r["reports_out"] += 1
                r["reports"].append([ts, "out", an, a, cat, cause])
                del r["reports"][:-30]
        elif kind == "kick":
            p = self._player(a)
            if an and an not in p["names"]:
                p["names"].append(an)
                del p["names"][:-6]
            p["kicks"] += 1
            self._inc(p["reasons"], "кик: " + (cause or "")[:30], cap=30)
        elif kind == "kill":
            if a is not None:  # погиб игрок
                v = self._player(a)
                self._touch(v, an, ts)
                if cat == "player":  # убит другим игроком (PvP)
                    k = self._player(b)
                    self._touch(k, bn, ts)
                    self._on_death(v, ts)
                    k["kills"] += 1
                    k["streak"] += 1
                    if k["streak"] > k["best_streak"]:
                        k["best_streak"] = k["streak"]
                    self._inc(k["victims"], a)
                    v["deaths_pvp"] += 1
                    self._inc(v["killers"], b)
                    self._event(k, ts, "kill", an, a, x, z)
                    self._event(v, ts, "death", bn, b, x, z)
                elif cat == "suicide":
                    life = self._on_death(v, ts)
                    v["suicides"] += 1
                    if life is not None and life < 120:  # «перебрасывание» спавна
                        v["quick_suic"] += 1
                    self._event(v, ts, "suicide", "", "", x, z)
                elif cat == "env":  # голод, холод, падение...
                    self._on_death(v, ts)
                    self._inc(v["other_deaths"], cause)
                    self._event(v, ts, "env", "", "", x, z, cause)
                else:  # учёный / животное / вертолёт и т.п.
                    self._on_death(v, ts)
                    self._inc(v["ent_deaths"], cause)
                    self._event(v, ts, cat, cause, "", x, z)
            elif b is not None:  # игрок убил NPC / животное
                k = self._player(b)
                self._touch(k, bn, ts)
                k["npc"] += 1
                self._inc(k["npc_types"], an)
        elif kind == "died":
            v = self._player(a)
            self._touch(v, an, ts)
            self._on_death(v, ts)
            self._inc(v["other_deaths"], cause)
            self._event(v, ts, "env", "", "", None, None, cause)
        elif kind == "join":
            self._join(ts, a, an, ip)
        elif kind == "disc":
            p = self._player(a)
            self._touch(p, an, ts)
            if p["open"] is not None:
                dur = ts - p["open"]
                if 0 <= dur < 7 * 86400:
                    p["playtime"] += dur
                    if dur > p["max_session"]:
                        p["max_session"] = dur
                    self._add_hours(p, p["open"], ts)
                p["open"] = None
            reason = (cause or "").strip()
            p["reason"] = reason
            self._inc(p["reasons"], reason[:40] or "—", cap=30)
        elif kind == "spawn":
            p = self._player(a)
            self._touch(p, an, ts)
            p["spawns"] += 1
            p["spawn_at"] = ts


class StatsDB(TeamsStore):
    # SQLite-хранилище одного сервера. Одно соединение под RLock: пишет ConsoleWorker (live), читает UI. Статистика не хранится счётчиками — считается из событий по выбранному вайпу.
    # Публичный интерфейс совпадает со старым LogStore (get/has/name_of/n_players/alts/summary/apply_live/...).

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wipe_cb = None          # вызывается (wipe_id, seed, world) при live-смене вайпа
        self.period = "current"      # "current" | None (всё время) | ("range", ts_с, ts_по) — выбранные даты
        self.version = 0             # растёт при каждой записи — для кэшей
        self._cache = {}             # get(): карточки игроков
        self._sum_cache = None       # summary(): (version, period, результат)
        self._alts_cache = {}        # alts(): (sid, version) -> список
        self._starts = []            # времена начала вайпов (для bisect)
        self.view_wipe = None        # вайп, чья карта на экране (None — текущий): только у него показываем координаты
        self.heat_version = 0        # растёт при каждом сбросе позиций в БД
        self._heat_buf = {}          # (вайп, день, sid, cx, cz) -> [отсчётов, из них в движении]
        self._heat_secs = {}         # (вайп, день) -> секунд записи
        self._heat_prev = {}         # sid -> прошлая позиция (для «в движении»)
        self._heat_t = 0.0
        self._heat_flushed = time.time()
        self._last_seed = None
        self.names = {}
        self.wipes = []
        self.db = self._open()
        self._load_state()
        try:
            self._auto_backup()
        except Exception as e:
            print(f"Бэкап статистики: {e}")

    # ---- открытие / схема ----
    def _open(self):
        try:
            return self._connect_db()
        except sqlite3.DatabaseError as e:
            print(f"БД статистики повреждена ({e}) — переименована в .corrupt")
            try:
                self.path.replace(self.path.with_suffix(f".corrupt-{int(time.time())}"))
            except OSError:
                pass
            return self._connect_db()

    def _connect_db(self):
        db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        ver = db.execute("PRAGMA user_version").fetchone()[0]
        if ver < DB_SCHEMA:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE IF NOT EXISTS wipes(
                    id INTEGER PRIMARY KEY, seed TEXT, world INTEGER, start_ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, wipe_id INTEGER NOT NULL,
                    kind TEXT NOT NULL, a TEXT, b TEXT, a_name TEXT, b_name TEXT,
                    x REAL, y REAL, z REAL, cat TEXT, cause TEXT, ip TEXT, num INTEGER,
                    uniq TEXT UNIQUE);
                CREATE INDEX IF NOT EXISTS ix_ev_a ON events(a, ts);
                CREATE INDEX IF NOT EXISTS ix_ev_b ON events(b, ts);
                CREATE INDEX IF NOT EXISTS ix_ev_w ON events(wipe_id, kind, ts);
                CREATE TABLE IF NOT EXISTS names(sid TEXT PRIMARY KEY, name TEXT, ts REAL);
                CREATE TABLE IF NOT EXISTS ips(sid TEXT, ip TEXT, ts REAL, PRIMARY KEY(sid, ip));
                CREATE INDEX IF NOT EXISTS ix_ips_ip ON ips(ip);
                -- карта активности игроков: клетки 25 м, счётчики по дням (пока не заполняется)
                CREATE TABLE IF NOT EXISTS heat(
                    wipe_id INTEGER NOT NULL, day INTEGER NOT NULL, sid TEXT NOT NULL,
                    cx INTEGER NOT NULL, cz INTEGER NOT NULL, n INTEGER NOT NULL, mv INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (wipe_id, day, sid, cx, cz)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS ix_heat_sid ON heat(sid, wipe_id, day);
                CREATE TABLE IF NOT EXISTS heat_cover(
                    wipe_id INTEGER NOT NULL, day INTEGER NOT NULL, secs REAL NOT NULL,
                    PRIMARY KEY (wipe_id, day)) WITHOUT ROWID;
                -- удобные представления для анализа (можно открыть базу любым просмотрщиком SQLite)
                CREATE VIEW IF NOT EXISTS v_deaths AS
                    SELECT id, ts, wipe_id, a AS victim, a_name AS victim_name, b AS killer, b_name AS killer_name,
                           CASE WHEN kind='died' THEN 'env' ELSE cat END AS cat, cause, x, y, z
                    FROM events WHERE kind IN ('kill','died') AND a IS NOT NULL;
                CREATE VIEW IF NOT EXISTS v_pvp AS SELECT * FROM v_deaths WHERE cat='player';
                CREATE VIEW IF NOT EXISTS v_sessions AS
                    SELECT sid, wipe_id, join_ts, leave_ts, leave_ts - join_ts AS secs, reason FROM (
                        SELECT a AS sid, wipe_id, kind, ts AS join_ts,
                               LEAD(ts) OVER (PARTITION BY a ORDER BY ts, id) AS leave_ts,
                               LEAD(kind) OVER (PARTITION BY a ORDER BY ts, id) AS nk,
                               LEAD(cause) OVER (PARTITION BY a ORDER BY ts, id) AS reason
                        FROM events WHERE kind IN ('join','disc') AND a IS NOT NULL)
                    WHERE kind='join' AND nk='disc' AND leave_ts - join_ts BETWEEN 0 AND 604800;
            """ + TEAMS_SQL)
            db.execute(f"PRAGMA user_version={DB_SCHEMA}")
        return db

    def _load_state(self):
        with self.lock:
            self.names = {sid: name for sid, name in self.db.execute("SELECT sid, name FROM names")}
            # ивенты, записанные раньше «сырым» текстом с координатами -> одно нормальное название
            self.db.execute("UPDATE events SET cause='Группа лодок (спавн)' "
                            "WHERE kind='event' AND cause LIKE 'Spawning roaming boat group%'")
            self._reload_wipes()
            cur = self.current_wipe()
            self._last_seed = cur["seed"] if cur else None

    def _reload_wipes(self):
        self.wipes = [{"id": i, "seed": s, "world": w, "start": st} for i, s, w, st in
                      self.db.execute("SELECT id, seed, world, start_ts FROM wipes ORDER BY start_ts, id")]
        self._starts = [w["start"] for w in self.wipes]

    def close(self):
        with self.lock:
            try:
                self.heat_flush(True)
                self.db.close()
            except Exception:
                pass

    # ---- мета ----
    def get_meta(self, key, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
            return r[0] if r else default

    def set_meta(self, key, value):
        with self.lock:
            self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                            (key, str(value)))

    # ---- бэкапы ----
    def backup(self, tag="manual"):
        with self.lock:
            bdir = self.path.parent / "backups"
            bdir.mkdir(exist_ok=True)
            dst = bdir / f"stats2-{time.strftime('%Y%m%d-%H%M%S')}-{tag}.db"
            out = sqlite3.connect(str(dst))
            try:
                self.db.backup(out)
            finally:
                out.close()
            files = sorted(bdir.glob("stats2-*-auto.db"))
            for old in files[:-BACKUPS_KEEP]:
                try:
                    old.unlink()
                except OSError:
                    pass
            return dst

    def _auto_backup(self):
        bdir = self.path.parent / "backups"
        today = time.strftime("%Y%m%d")
        if any(bdir.glob(f"stats2-{today}-*-auto.db")) if bdir.exists() else False:
            return
        with self.lock:
            if self.db.execute("SELECT 1 FROM events LIMIT 1").fetchone():
                self.backup("auto")

    # ---- вайпы ----
    def current_wipe(self):
        return self.wipes[-1] if self.wipes else None

    def current_seed(self):
        w = self.current_wipe()
        return w["seed"] if w else None

    def _unknown_wipe(self):
        for w in self.wipes:
            if w["seed"] is None and w["start"] <= 0:
                return w
        cur = self.db.execute("INSERT INTO wipes(seed, world, start_ts) VALUES(NULL, NULL, 0)")
        self._reload_wipes()
        return next(w for w in self.wipes if w["id"] == cur.lastrowid)

    def wipe_for(self, ts):
        i = bisect.bisect_right(self._starts, ts) - 1
        if i >= 0:
            return self.wipes[i]["id"]
        return self._unknown_wipe()["id"]

    def _wipe_at(self, ts):
        i = bisect.bisect_right(self._starts, ts) - 1
        return self.wipes[i] if i >= 0 else None

    def note_seed(self, ts, seed, world=None, live=False):
        # Сид карты определяет вайп. Тот же сид — тот же вайп (ежедневный рестарт вайпом не считается).
        seed = str(seed)
        with self.lock:
            if seed == self._last_seed and self._wipe_at(ts) is not None:
                return
            cur = self._wipe_at(ts)
            if cur and cur["seed"] == seed:
                self._last_seed = seed
                return
            same = next((w for w in self.wipes if w["seed"] == seed), None)
            if same:
                # нашли начало уже известного вайпа раньше, чем думали
                if ts < same["start"] and (cur is None or cur["seed"] is None or cur["start"] < ts):
                    old = same["start"]
                    self.db.execute("UPDATE wipes SET start_ts=? WHERE id=?", (ts, same["id"]))
                    self.db.execute("UPDATE events SET wipe_id=? WHERE ts>=? AND ts<?", (same["id"], ts, old))
                    self._reload_wipes()
                self._last_seed = seed
                return
            if cur is not None and cur["seed"] is None:
                # первый встреченный сид: возможно, «неизвестный» кусок — это начало того же вайпа
                last = self.db.execute("SELECT MAX(ts) FROM events WHERE wipe_id=?", (cur["id"],)).fetchone()[0]
                if last is not None and wipe_month(last) == wipe_month(ts):
                    self.db.execute("UPDATE wipes SET seed=?, world=? WHERE id=?", (seed, world, cur["id"]))
                    self._reload_wipes()
                    self._last_seed = seed
                    return
            had_seeded = any(w["seed"] for w in self.wipes)
            c = self.db.execute("INSERT INTO wipes(seed, world, start_ts) VALUES(?, ?, ?)", (seed, world, ts))
            self._reload_wipes()
            self._last_seed = seed
            self.version += 1
            if self.current_wipe()["id"] == c.lastrowid:
                if live and had_seeded and self.wipe_cb:
                    try:
                        self.wipe_cb(c.lastrowid, seed, world)
                    except Exception as e:
                        print(f"wipe_cb: {e}")

    # ---- период ----
    def set_period(self, period):
        with self.lock:
            self.period = period
            self.version += 1

    def _scope_ids(self):
        # None — все вайпы (или диапазон дат), иначе список id (для «текущего» — один или ни одного)
        if is_wipe_period(self.period):
            return [self.period[1]]
        if self.period is None or isinstance(self.period, tuple):
            return None
        cur = self.current_wipe()
        return [cur["id"]] if cur else []

    def _scope(self):
        if is_range_period(self.period):
            return "ts >= ? AND ts < ?", [self.period[1], self.period[2]]
        ids = self._scope_ids()
        if ids is None:
            return "1=1", []
        if not ids:
            return "0", []
        return f"wipe_id IN ({','.join('?' * len(ids))})", list(ids)

    def period_label(self):
        if self.period is None:
            return "за всё время"
        if is_wipe_period(self.period):
            w = next((w for w in self.wipes if w["id"] == self.period[1]), None)
            return "вайп " + (self.wipe_start_label(w) if w else "?")
        if isinstance(self.period, tuple):
            d1 = datetime.date.fromtimestamp(self.period[1])
            d2 = datetime.date.fromtimestamp(self.period[2] - 1)
            f = lambda d: d.strftime("%d.%m.%Y")
            return f(d1) if d1 == d2 else f"{f(d1)} — {f(d2)}"
        cur = self.current_wipe()
        return "текущий вайп" + (" (неполные данные)" if cur and cur["start"] <= 0 else "")

    def wipe_event_counts(self):
        """{id вайпа: (событий, первое ts, последнее ts)}"""
        with self.lock:
            return {wid: (n, lo, hi) for wid, n, lo, hi in self.db.execute(
                "SELECT wipe_id, COUNT(*), MIN(ts), MAX(ts) FROM events WHERE ts > 0 GROUP BY wipe_id")}

    def wipe_span(self, w):
        """(начало, конец) вайпа в ts; начало неизвестно — по первому событию; конец None у текущего."""
        i = next((k for k, x in enumerate(self.wipes) if x["id"] == w["id"]), None)
        end = self.wipes[i + 1]["start"] if i is not None and i + 1 < len(self.wipes) else None
        start = w["start"]
        if start <= 0:
            lo = self.wipe_event_counts().get(w["id"], (0, None, None))[1]
            start = lo or 0
        return start, end

    def split_wipe(self, ts):
        """Ставит границу вайпа в момент ts: вайп, внутри которого ts, делится на два. Новый кусок без сида.
        -> текст ошибки или None."""
        with self.lock:
            w = self._wipe_at(ts)
            if w is None:
                w = next((x for x in self.wipes if x["seed"] is None and x["start"] <= 0), None)
            if w is None:
                return "в этот момент вайпа нет"
            if w is self.wipes[-1]:
                return "текущий вайп делить нельзя: его границу определяет сид карты"
            start, end = self.wipe_span(w)
            if ts <= start or (end is not None and ts >= end):
                return "граница должна лежать внутри вайпа"
            self.backup("before-split")
            try:
                self.db.execute("BEGIN")
                c = self.db.execute("INSERT INTO wipes(seed, world, start_ts) VALUES(NULL, ?, ?)", (w["world"], ts))
                self.db.execute("UPDATE events SET wipe_id=? WHERE wipe_id=? AND ts>=?", (c.lastrowid, w["id"], ts))
                self.db.execute("COMMIT")
            except Exception as e:
                try:
                    self.db.execute("ROLLBACK")
                except Exception:
                    pass
                return str(e)
            self._reload_wipes()
            self.version += 1
        return None

    def delete_wipe(self, wid):
        """Удаляет вайп вместе со всеми его данными (события, heat). Текущий вайп удалить нельзя. -> текст ошибки или None."""
        with self.lock:
            w = next((x for x in self.wipes if x["id"] == wid), None)
            if w is None:
                return "такого вайпа нет"
            if w is self.wipes[-1]:
                return "текущий вайп удалять нельзя: его определяет сид карты (очистить его можно в «Очистить статистику»)"
            self.backup("before-delete")
            try:
                self.db.execute("BEGIN")
                for tbl in ("events", "heat", "heat_cover"):
                    self.db.execute(f"DELETE FROM {tbl} WHERE wipe_id=?", (wid,))
                self.db.execute("DELETE FROM wipes WHERE id=?", (wid,))
                self.db.execute("COMMIT")
            except Exception as e:
                try:
                    self.db.execute("ROLLBACK")
                except Exception:
                    pass
                return str(e)
            if self.view_wipe == wid:
                self.view_wipe = None
            self._reload_wipes()
            self.version += 1
        return None

    def merge_wipe_back(self, wid):
        """Убирает границу вайпа без сида (созданную вручную): его события уходят в предыдущий вайп."""
        with self.lock:
            i = next((k for k, x in enumerate(self.wipes) if x["id"] == wid), None)
            if i is None or i == 0:
                return "предыдущего вайпа нет"
            w, prev = self.wipes[i], self.wipes[i - 1]
            if w["seed"] is not None or w["start"] <= 0:
                return "слить можно только вайп без сида, созданный вручную"
            if w is self.wipes[-1]:
                return "текущий вайп сливать нельзя"
            self.backup("before-merge")
            try:
                self.db.execute("BEGIN")
                self.db.execute("UPDATE events SET wipe_id=? WHERE wipe_id=?", (prev["id"], wid))
                self.db.execute("DELETE FROM wipes WHERE id=?", (wid,))
                self.db.execute("COMMIT")
            except Exception as e:
                try:
                    self.db.execute("ROLLBACK")
                except Exception:
                    pass
                return str(e)
            self._reload_wipes()
            self.version += 1
        return None

    def guess_wipe_time(self, day, default_hour=21):
        """Время вайпа в день day (date): первый запуск сервера после полудня, иначе default_hour:00."""
        d0 = datetime.datetime.combine(day, datetime.time()).timestamp()
        with self.lock:
            r = self.db.execute("SELECT MIN(ts) FROM events WHERE kind='startup' AND ts >= ? AND ts < ?",
                                (d0 + 12 * 3600, d0 + 86400)).fetchone()[0]
        return r if r else d0 + default_hour * 3600

    def long_wipes(self, max_days=36):
        out = []
        for w in self.wipes:
            start, end = self.wipe_span(w)
            if start and end and (end - start) / 86400 > max_days:
                out.append(w)
        return out

    def wipe_start_label(self, w):
        """«с 01.10.2026»; если начало вайпа неизвестно — по первому событию в базе: «с 01.04.2026 (по данным)»,
        иначе «(дата неизвестна)»."""
        if w["start"] > 0:
            return "с " + fmt_date(datetime.date.fromtimestamp(w["start"]))
        with self.lock:
            lo = self.db.execute("SELECT MIN(ts) FROM events WHERE wipe_id=? AND ts > 0", (w["id"],)).fetchone()[0]
        if lo:
            return "с " + fmt_date(datetime.date.fromtimestamp(lo)) + " (по данным)"
        return "(дата неизвестна)"

    def date_bounds(self):
        # (первая, последняя) дата событий в базе или None
        with self.lock:
            lo, hi = self.db.execute("SELECT MIN(ts), MAX(ts) FROM events WHERE ts > 0").fetchone()
        if lo is None:
            return None
        return datetime.date.fromtimestamp(lo), datetime.date.fromtimestamp(hi)

    # ---- вайпы и период ----
    def wipes_in_range(self, t1, t2):
        # вайпы (по порядку), пересекающиеся с [t1, t2)
        out = []
        for i, w in enumerate(self.wipes):
            start = w["start"] if w["start"] > 0 else float("-inf")
            end = self.wipes[i + 1]["start"] if i + 1 < len(self.wipes) else float("inf")
            if start < t2 and end > t1:
                out.append(w)
        return out

    def set_view_wipe(self, wid):
        # вайп, чья карта показана (None — текущий); от него зависит, чьи координаты видны в карточках
        with self.lock:
            if wid != self.view_wipe:
                self.view_wipe = wid
                self.version += 1

    MAP_EVENTS_LIMIT = 400

    def map_events(self, wipe_id, t1=None, t2=None, limit=MAP_EVENTS_LIMIT):
        """Смерти игроков с координатами по вайпу (и необязательно датам [t1, t2)), новые первыми.
        -> [(ts, kill_cat, sid_жертвы, имя_жертвы, sid_убийцы, имя_убийцы, x, z, причина)]"""
        sql = ("SELECT ts, cat, a, a_name, b, b_name, x, z, cause FROM events "
               "WHERE wipe_id=? AND kind='kill' AND a IS NOT NULL AND x IS NOT NULL AND z IS NOT NULL")
        args = [wipe_id]
        if t1 is not None:
            sql += " AND ts >= ? AND ts < ?"
            args += [t1, t2]
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ---- Heatmap: накопление позиций ----
    def heat_sample(self, ts, players, world):
        """Вызывать часто (каждое обновление списка игроков): сама ограничивает частоту."""
        if ts - self._heat_t < HEAT_INTERVAL:
            return
        with self.lock:
            cur = self.current_wipe()
            if not cur:
                return
            dt = ts - self._heat_t if self._heat_t else 0.0
            self._heat_t = ts
            wid = cur["id"]
            world = float(cur["world"] or world)
            day = day_int(ts)
            k = (wid, day)
            self._heat_secs[k] = self._heat_secs.get(k, 0.0) + min(dt, 2 * HEAT_INTERVAL)
            half, n = world / 2, heat_dims(world)[0]
            seen = set()
            for p in players:
                x, z = p.get("x"), p.get("z")
                if x is None or z is None or (abs(x) < 1 and abs(z) < 1):
                    continue   # позиция ещё не известна
                if abs(x) > half + 50 or abs(z) > half + 50:
                    continue
                sid = p["steam_id"]
                seen.add(sid)
                prev = self._heat_prev.get(sid)
                moving = prev is not None and ((x - prev[0]) ** 2 + (z - prev[1]) ** 2) ** 0.5 >= HEAT_MOVE_EPS
                self._heat_prev[sid] = (x, z)
                cx, cz = heat_cell(x, z, world)
                e = self._heat_buf.setdefault((wid, day, sid, min(max(cx, 0), n - 1), min(max(cz, 0), n - 1)), [0, 0])
                e[0] += 1
                if moving:
                    e[1] += 1
            for sid in list(self._heat_prev):
                if sid not in seen:
                    del self._heat_prev[sid]

    def heat_flush(self, force=False):
        with self.lock:
            if not self._heat_buf and not self._heat_secs:
                return
            now = time.time()
            if not force and now - self._heat_flushed < HEAT_FLUSH:
                return
            rows = [(w, d, sid, cx, cz, n, mv) for (w, d, sid, cx, cz), (n, mv) in self._heat_buf.items()]
            covers = [(w, d, secs) for (w, d), secs in self._heat_secs.items()]
            try:
                self.db.execute("BEGIN")
                self.db.executemany(
                    "INSERT INTO heat(wipe_id, day, sid, cx, cz, n, mv) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(wipe_id, day, sid, cx, cz) DO UPDATE SET n=n+excluded.n, mv=mv+excluded.mv", rows)
                self.db.executemany(
                    "INSERT INTO heat_cover(wipe_id, day, secs) VALUES(?,?,?) "
                    "ON CONFLICT(wipe_id, day) DO UPDATE SET secs=secs+excluded.secs", covers)
                self.db.execute("COMMIT")
            except Exception as e:
                print(f"Ошибка записи heat: {e}")
                try:
                    self.db.execute("ROLLBACK")
                except Exception:
                    pass
                return
            self._heat_buf.clear()
            self._heat_secs.clear()
            self._heat_flushed = now
            self.heat_version += 1

    # ---- Heatmap: чтение ----
    def heat_move(self, wipe_id, t1=None, t2=None, idle=False, sid=None):
        """Передвижение: {(cx, cz): отсчётов} по вайпу и (необязательно) датам [t1, t2).
        idle=False — только отсчёты «в движении», True — все (и стоящие)."""
        self.heat_flush(True)
        col = "n" if idle else "mv"
        sql = f"SELECT cx, cz, SUM(MIN({col}, {HEAT_CELL_CAP})) FROM heat WHERE wipe_id=?"
        args = [wipe_id]
        if sid:
            sql += " AND sid=?"
            args.append(sid)
        if t1 is not None:
            sql += " AND day BETWEEN ? AND ?"
            args += [day_int(t1), day_int(t2 - 1)]
        sql += f" GROUP BY cx, cz HAVING SUM({col}) > 0"
        with self.lock:
            return {(cx, cz): v for cx, cz, v in self.db.execute(sql, args)}

    def heat_pvp(self, wipe_id, world, t1=None, t2=None):
        """PvP: {(cx, cz): убийств игрока игроком} по вайпу и (необязательно) времени [t1, t2)."""
        sql = "SELECT x, z FROM events WHERE kind='kill' AND cat='player' AND wipe_id=? AND x IS NOT NULL"
        args = [wipe_id]
        if t1 is not None:
            sql += " AND ts >= ? AND ts < ?"
            args += [t1, t2]
        out = {}
        gx = heat_dims(world)[0]
        with self.lock:
            for x, z in self.db.execute(sql, args):
                cx, cz = heat_cell(x, z, world)
                if 0 <= cx < gx and 0 <= cz < gx:
                    out[(cx, cz)] = out.get((cx, cz), 0) + 1
        return out

    def _ingest(self, ts, msg, uniq, live):
        if "proceduralmap." in msg:
            m = RE_SEED.search(msg)
            if m:
                self.note_seed(ts, m.group(2), int(m.group(1)), live=live)
                return
        ev = parse_line(msg)
        if ev is None:
            return
        wid = self.wipe_for(ts)
        self.db.execute(
            "INSERT OR IGNORE INTO events(ts, wipe_id, kind, a, b, a_name, b_name, x, y, z, cat, cause, ip, num, uniq) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, wid, ev.kind, ev.a, ev.b, ev.a_name, ev.b_name, ev.x, ev.y, ev.z, ev.cat, ev.cause, ev.ip,
             ev.num, uniq))
        for sid, name in ((ev.a, ev.a_name), (ev.b, ev.b_name)):
            if sid and name:
                self.db.execute(
                    "INSERT INTO names(sid, name, ts) VALUES(?,?,?) ON CONFLICT(sid) DO UPDATE "
                    "SET name=excluded.name, ts=excluded.ts WHERE excluded.ts>=names.ts", (sid, name, ts))
                self.names[sid] = name
        if ev.kind == "join" and ev.ip and ev.a:
            self.db.execute(
                "INSERT INTO ips(sid, ip, ts) VALUES(?,?,?) ON CONFLICT(sid, ip) DO UPDATE "
                "SET ts=max(ts, excluded.ts)", (ev.a, ev.ip, ts))

    def _txn(self, fn):
        with self.lock:
            self.db.execute("BEGIN")
            try:
                fn()
                self.db.execute("COMMIT")
            except Exception:
                try:
                    self.db.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            self.version += 1

    def apply_live(self, ts, msgs):
        def run():
            for msg in msgs:
                self._ingest(ts, msg, None, True)
        self._txn(run)

    def clear(self, wipe_id=None):
        # wipe_id=None — всё; иначе только события этого вайпа. Перед очисткой — автоматический бэкап.
        with self.lock:
            self.backup("before-clear")
            self.db.execute("BEGIN")
            if wipe_id is None:
                for t in ("events", "names", "ips", "wipes", "heat", "heat_cover"):
                    self.db.execute(f"DELETE FROM {t}")
                self.names = {}
                self._last_seed = None
            else:
                self.db.execute("DELETE FROM events WHERE wipe_id=?", (wipe_id,))
                self.db.execute("DELETE FROM heat WHERE wipe_id=?", (wipe_id,))
                self.db.execute("DELETE FROM heat_cover WHERE wipe_id=?", (wipe_id,))
                cur = self.current_wipe()
                if cur and cur["id"] != wipe_id:
                    self.db.execute("DELETE FROM wipes WHERE id=?", (wipe_id,))
            self.db.execute("COMMIT")
            self._reload_wipes()
            self.version += 1
            self._cache.clear()

    # ---- чтение из UI ----
    def get(self, sid):
        # Результат кэшируется и отдаётся без копирования — менять его нельзя (только читать).
        with self.lock:
            sc, sp = self._scope()
            key = (sid, tuple(sp), sc, self.version)
            if key in self._cache:
                return self._cache[key]
            rows = self.db.execute(
                f"{Replay.SELECT} WHERE (a=? OR b=?) AND {sc} ORDER BY ts, id", [sid, sid] + sp).fetchall()
            p = None
            if rows:
                cur = self.current_wipe()
                rep = Replay(self.view_wipe if self.view_wipe is not None else (cur["id"] if cur else None))
                for row in rows:
                    rep.apply(row)
                p = rep.players.get(sid)
            if len(self._cache) > 40:
                self._cache.clear()
            self._cache[key] = p
            return p

    def has(self, sid):
        with self.lock:
            return sid in self.names

    def all_players(self):
        """Все игроки, встречавшиеся в событиях выбранного периода: [(sid, ник, последняя активность)]."""
        with self.lock:
            key = (self.version, self.period)
            c = getattr(self, "_all_cache", None)
            if c and c[0] == key:
                return c[1]
            sc, sp = self._scope()
            rows = self.db.execute(
                "SELECT s, MAX(ts) FROM (SELECT a AS s, ts FROM events WHERE a IS NOT NULL AND kind!='report' AND "
                + sc + " UNION ALL SELECT b, ts FROM events WHERE b IS NOT NULL AND kind!='report' AND " + sc
                + ") GROUP BY s", sp + sp).fetchall()
            res = [(sid, self.names.get(sid) or sid, ts) for sid, ts in rows]
            self._all_cache = (key, res)
            return res

    def name_of(self, sid):
        with self.lock:
            return self.names.get(sid) or sid

    def n_players(self):
        with self.lock:
            return len(self.names)

    def alts(self, sid):
        # другие аккаунты, заходившие с тем же IP (возможны ложные: общий провайдер/VPN)
        with self.lock:
            key = (sid, self.version)
            hit = self._alts_cache.get(key)
            if hit is not None:
                return hit
            out = {}
            rows = self.db.execute(
                "SELECT o.sid, o.ip FROM ips o JOIN ips m ON m.ip=o.ip WHERE m.sid=? AND o.sid!=?", (sid, sid))
            for o, ip in rows:
                if not is_private_ip(ip) and o not in out:
                    out[o] = ip
            res = [(o, self.names.get(o) or o, ip) for o, ip in list(out.items())[:10]]
            if len(self._alts_cache) > 64:
                self._alts_cache.clear()
            self._alts_cache[key] = res
            return res

    def summary(self):
        # тяжёлые запросы по всей БД — пересчитываем только после изменения данных или смены периода
        with self.lock:
            c = self._sum_cache
            if c and c[0] == self.version and c[1] == self.period:
                return c[2]
            res = self._compute_summary()
            self._sum_cache = (self.version, self.period, res)
            return res

    def _compute_summary(self):
        with self.lock:
            sc, sp = self._scope()
            q = lambda sql, extra=(): self.db.execute(sql.replace("{sc}", sc), list(extra) + sp).fetchall()
            pvp = "kind='kill' AND a IS NOT NULL AND b IS NOT NULL AND a!=b AND {sc}"
            kills_by = dict(q(f"SELECT b, COUNT(*) FROM events WHERE {pvp} GROUP BY b"))
            pdeaths_by = dict(q(f"SELECT a, COUNT(*) FROM events WHERE {pvp} GROUP BY a"))
            deaths_by = dict(q("SELECT a, COUNT(*) FROM events WHERE kind IN ('kill','died') AND a IS NOT NULL "
                               "AND {sc} GROUP BY a"))
            npc = q("SELECT COUNT(*) FROM events WHERE kind='kill' AND a IS NULL AND b IS NOT NULL AND {sc}")[0][0]
            n_players = self.db.execute(
                "SELECT COUNT(*) FROM (SELECT a AS s FROM events WHERE a IS NOT NULL AND " + sc +
                " UNION SELECT b FROM events WHERE b IS NOT NULL AND " + sc + ")", sp + sp).fetchone()[0]
            time_rows = self.db.execute(
                "WITH j AS (SELECT a AS sid, ts, kind, LEAD(ts) OVER (PARTITION BY a ORDER BY ts, id) AS nts, "
                "LEAD(kind) OVER (PARTITION BY a ORDER BY ts, id) AS nk FROM events "
                f"WHERE kind IN ('join','disc') AND a IS NOT NULL AND {sc}) "
                "SELECT sid, SUM(nts-ts), COUNT(*) FROM j WHERE kind='join' AND nk='disc' "
                "AND nts-ts BETWEEN 0 AND 604800 GROUP BY sid ORDER BY 2 DESC LIMIT 10", sp).fetchall()
            nm = lambda s: self.names.get(s) or s
            kd = [(s, nm(s), kills_by[s] / pdeaths_by[s] if pdeaths_by.get(s) else float(kills_by[s]),
                   kills_by[s], pdeaths_by.get(s, 0)) for s in kills_by if kills_by[s] >= 5]
            ev = {}
            for kind, cause, n, last in q("SELECT kind, cause, COUNT(*), MAX(ts) FROM events "
                                          "WHERE kind IN ('event','startup','bradley') AND {sc} GROUP BY kind, cause"):
                name = (cause if kind == "event"
                        else "Перезапуск сервера" if kind == "startup" else "Bradley APC (спавн)")
                e = ev.setdefault(name, {"count": 0, "last": 0})
                e["count"] += n
                e["last"] = max(e["last"], last)
            rep_tot = q("SELECT cat, COUNT(*) FROM events WHERE kind='report' AND {sc} GROUP BY cat")
            rep_by = q("SELECT a, COUNT(*), COUNT(DISTINCT b) FROM events WHERE kind='report' AND a IS NOT NULL "
                       "AND {sc} GROUP BY a ORDER BY 2 DESC LIMIT 10")
            rep_cat = {}
            for sid_, cat_, n_ in q("SELECT a, cat, COUNT(*) FROM events WHERE kind='report' AND {sc} "
                                    "GROUP BY a, cat"):
                rep_cat.setdefault(sid_, {})[cat_] = n_
            rep_recent = q("SELECT ts, b, a, cat, cause FROM events WHERE kind='report' AND {sc} "
                           "ORDER BY ts DESC, id DESC LIMIT 8")
            ents = [[ts, num] for ts, num in
                    q("SELECT ts, num FROM events WHERE kind='saved' AND {sc} ORDER BY ts DESC LIMIT 60")]
            ents.reverse()
            return {
                "n_players": n_players,
                "kills": sum(kills_by.values()),
                "npc": npc,
                "deaths": sum(deaths_by.values()),
                "top_kills": sorted(((s, nm(s), n, pdeaths_by.get(s, 0)) for s, n in kills_by.items()),
                                    key=lambda r: r[2], reverse=True)[:10],
                "top_kd": sorted(kd, key=lambda r: r[2], reverse=True)[:10],
                "top_time": [(s, nm(s), int(t), n) for s, t, n in time_rows],
                "top_deaths": sorted(((s, nm(s), n) for s, n in deaths_by.items()),
                                     key=lambda r: r[2], reverse=True)[:10],
                "events": ev,
                "ents": ents,
                "reports": {
                    "total": sum(n for _c, n in rep_tot),
                    "types": dict(rep_tot),
                    "top": [(s_, nm(s_), n_, u_, rep_cat.get(s_, {})) for s_, n_, u_ in rep_by],
                    "recent": [(ts_, b_, nm(b_), a_, nm(a_), c_, t_) for ts_, b_, a_, c_, t_ in rep_recent],
                },
            }


def open_stats_store(cfg=None):
    """Открывает базу статистики активного профиля (создаёт, если её ещё нет)."""
    return StatsDB(db_path())
