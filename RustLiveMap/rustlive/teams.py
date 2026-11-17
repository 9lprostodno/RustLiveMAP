"""Rust Live Map: команды игроков — разбор ответа global.teaminfo и хранение состава по времени."""

import re

RE_TEAM_ID = re.compile(r"\bID:\s*(\d+)", re.IGNORECASE)
RE_STAMP = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}\s+")
RE_MEMBER = re.compile(r"^\s*(7656\d{13})\b(.*)$")


def parse_teaminfo(res):
    """Ответ global.teaminfo <steamid> ->
    {"team": 11, "members": [{"sid", "name", "online", "leader"}]} или None (игрок не в команде / не разобрали).
    Колонки таблицы (steamID, username, online, leader) определяем по позициям в строке-заголовке:
    в нике бывают пробелы и буква «x»."""
    if isinstance(res, dict):
        res = res.get("Message") or ""
    text = str(res or "")
    lines = [RE_STAMP.sub("", ln.rstrip("\r")) for ln in text.split("\n")]
    team = None
    for ln in lines:
        m = RE_TEAM_ID.search(ln)
        if m:
            team = int(m.group(1))
            break
    if team is None:
        return None
    ci = co = cl = -1
    for ln in lines:
        low = ln.lower()
        if "steamid" in low and "username" in low:
            ci, co, cl = low.find("username"), low.find("online"), low.find("leader")
            break
    members = []
    for ln in lines:
        m = RE_MEMBER.match(ln)
        if not m:
            continue
        sid = m.group(1)
        if 0 <= ci < co < cl and len(ln) > ci:
            name = ln[ci:co].strip()
            online = ln[co:cl].strip().lower() == "x"
            leader = ln[cl:].strip().lower() == "x"
        else:                                           # без заголовка: хвост из «x» — флаги
            rest = m.group(2).rstrip()
            flags = 0
            while rest.endswith(" x") or rest.strip() == "x":
                rest = rest[:-2].rstrip() if rest.endswith(" x") else ""
                flags += 1
                if flags == 2:
                    break
            name = rest.strip()
            online, leader = flags >= 1, flags >= 2
        members.append({"sid": sid, "name": name, "online": online, "leader": leader})
    return {"team": team, "members": members} if members else None


TEAMS_SQL = """
    -- состав команд по времени: игрок состоял в команде с t1 по t2 (t2 NULL — состоит до сих пор)
    CREATE TABLE IF NOT EXISTS team_members(
        wipe_id INTEGER NOT NULL, team INTEGER NOT NULL, sid TEXT NOT NULL,
        leader INTEGER NOT NULL DEFAULT 0, t1 REAL NOT NULL, t2 REAL);
    CREATE INDEX IF NOT EXISTS ix_tm_team ON team_members(wipe_id, team, t1);
    CREATE INDEX IF NOT EXISTS ix_tm_sid ON team_members(sid, t1);
    CREATE TABLE IF NOT EXISTS team_seen(
        wipe_id INTEGER NOT NULL, team INTEGER NOT NULL, ts REAL NOT NULL,
        PRIMARY KEY (wipe_id, team)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS team_chat(
        ts REAL NOT NULL, wipe_id INTEGER NOT NULL, sid TEXT NOT NULL, name TEXT, text TEXT NOT NULL,
        PRIMARY KEY (ts, sid, text)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS ix_tc_sid ON team_chat(sid, ts);
"""


class TeamsStore:
    """Методы StatsDB для команд (примесь: нужны self.db, self.lock, self.names, self.wipe_for)."""

    team_version = 0           # растёт при любом изменении команд/чата команд — для обновления окон

    def _team_txn(self, fn):
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
            self.team_version += 1

    def team_snapshot(self, ts, info):
        """Снимок состава команды (результат parse_teaminfo): закрывает интервалы ушедших, открывает новые."""
        team = int(info["team"])
        members = {m["sid"]: m for m in info["members"]}
        wid = self.wipe_for(ts)

        def run():
            db = self.db
            open_rows = db.execute("SELECT rowid, sid, leader FROM team_members "
                                   "WHERE wipe_id=? AND team=? AND t2 IS NULL", (wid, team)).fetchall()
            have = set()
            for rowid, sid, leader in open_rows:
                if sid not in members:
                    db.execute("UPDATE team_members SET t2=? WHERE rowid=?", (ts, rowid))
                else:
                    have.add(sid)
                    lead = int(bool(members[sid]["leader"]))
                    if lead != leader:
                        db.execute("UPDATE team_members SET leader=? WHERE rowid=?", (lead, rowid))
            for sid, m in members.items():
                if sid in have:
                    continue
                db.execute("UPDATE team_members SET t2=? WHERE sid=? AND t2 IS NULL AND wipe_id=?", (ts, sid, wid))
                db.execute("INSERT INTO team_members(wipe_id, team, sid, leader, t1) VALUES(?,?,?,?,?)",
                           (wid, team, sid, int(bool(m["leader"])), ts))
                name = m.get("name")
                if name and sid not in self.names:
                    self.names[sid] = name
                    db.execute("INSERT OR IGNORE INTO names(sid, name, ts) VALUES(?,?,?)", (sid, name, ts))
            db.execute("INSERT OR REPLACE INTO team_seen(wipe_id, team, ts) VALUES(?,?,?)", (wid, team, ts))

        self._team_txn(run)

    def teams_state(self, wipe_id):
        """Команды вайпа по последнему снимку: {team: {"ts", "members": [(sid, leader)]}} (лидер первым)."""
        with self.lock:
            out = {}
            for team, ts in self.db.execute("SELECT team, ts FROM team_seen WHERE wipe_id=?", (wipe_id,)):
                out[team] = {"ts": ts, "members": []}
            for team, sid, leader in self.db.execute(
                    "SELECT team, sid, leader FROM team_members WHERE wipe_id=? AND t2 IS NULL "
                    "ORDER BY leader DESC, t1, sid", (wipe_id,)):
                if team in out:
                    out[team]["members"].append((sid, bool(leader)))
            return {t: v for t, v in out.items() if v["members"]}

    def add_team_chat(self, ts, sid, name, text):
        if not sid or sid == "0" or not text:
            return
        wid = self.wipe_for(ts)
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO team_chat(ts, wipe_id, sid, name, text) VALUES(?,?,?,?,?)",
                                  (float(ts), wid, str(sid), name, str(text)))
            if cur.rowcount:
                self.team_version += 1

    def team_chat(self, wipe_id, team, limit=500):
        """Сообщения чата команды, в хронологическом порядке: отправитель в тот момент состоял в этой команде."""
        with self.lock:
            rows = self.db.execute(
                "SELECT DISTINCT c.ts, c.sid, c.name, c.text FROM team_chat c "
                "JOIN team_members m ON m.sid=c.sid AND m.wipe_id=? AND m.team=? "
                "AND c.ts >= m.t1 - 60 AND (m.t2 IS NULL OR c.ts <= m.t2 + 60) "
                "WHERE c.wipe_id=? ORDER BY c.ts DESC LIMIT ?", (wipe_id, team, wipe_id, limit)).fetchall()
        return rows[::-1]
