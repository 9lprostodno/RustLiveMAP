"""Rust Live Map: модуль workers."""

import json
import re
import shutil
import threading
import time
import websocket
from PySide6.QtCore import QThread, Signal
from collections import deque
from pathlib import Path
from urllib.parse import quote

from .chat import (
    DayLog,
    chat_file_line,
    parse_chat,
    search_chat_files,
)
from .config import (
    chat_dir,
    console_dir,
    log_error,
    map_output,
    mapsrc_dir,
    maps_dir,
    render_dir,
)
from .events import (
    LIVE_MARKERS_RE,
)
from .teams import parse_teaminfo
from .mapsource import (
    MapNotReady,
    fetch_map,
    fetch_render_png,
    parse_map_name,
)
from .monuments import (
    compact_monuments,
    monuments_file_name,
    save_monuments,
)
from .maps import (
    crop_render_margin,
)
from .gamedata import (
    parse_bases,
    parse_players,
)


# ============================================================
# ПОТОКИ
# ============================================================

class _RconThread(QThread):
    """Общая база: своё WebSocket-соединение + запрос команды с быстрой реакцией на stop()."""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = dict(cfg)
        self._stop = False
        self._ident = 1000

    def stop(self):
        self._stop = True

    def _sleep(self, seconds):
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(0.05)

    def _connect(self):
        cfg = self.cfg
        url = f"ws://{cfg['rcon_host']}:{int(cfg['rcon_port'])}/{quote(str(cfg['rcon_password']), safe='')}"
        return websocket.create_connection(url, timeout=5)

    def _query(self, ws, command, timeout=5):
        self._ident += 1
        ident = self._ident
        ws.send(json.dumps({"Identifier": ident, "Message": command, "Stacktrace": "", "Type": 3}))
        deadline = time.time() + timeout
        while not self._stop:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                # короткие «квантизации» ожидания, чтобы stop() срабатывал быстро
                ws.settimeout(min(0.5, remaining))
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("Identifier") == ident:
                msg = data.get("Message")
                if isinstance(msg, str):
                    try:
                        return json.loads(msg)
                    except Exception:
                        return msg
                return msg
        return None

    @staticmethod
    def _close(ws):
        if ws:
            try:
                ws.close()
            except Exception:
                pass


RE_ENV_TIME = re.compile(r"env\.time\W*", re.IGNORECASE)


def parse_env_time(res):
    """Ответ на env.time -> часы 0..24 (float) или None. Понимает «env.time: 14.5» и «… 14:30»."""
    text = str(res or "")
    m = RE_ENV_TIME.search(text)
    if m:
        text = text[m.end():]
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        h = int(m.group(1)) % 12 if re.search(r"[AP]M", text, re.IGNORECASE) else int(m.group(1))
        if re.search(r"PM", text, re.IGNORECASE):
            h += 12
        return h + int(m.group(2)) / 60.0
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return v if 0 <= v <= 24 else None


def parse_serverinfo(res):
    """Ответ на serverinfo (JSON, возможно с префиксом) -> dict или None."""
    if isinstance(res, dict):          # _query уже разобрал JSON-ответ
        return res
    text = str(res or "")
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(text[a:b + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def fmt_uptime(secs):
    secs = int(secs or 0)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}д {h}ч" if d else f"{h}ч {m:02d}м"


def fmt_mem(mb):
    mb = float(mb or 0)
    return f"{mb / 1024:.1f} ГБ" if mb >= 1024 else f"{mb:.0f} МБ"


class RconWorker(_RconThread):
    """Опрос игроков (быстрый цикл), времени суток и serverinfo."""
    players_updated = Signal(list)
    time_updated = Signal(float)
    info_updated = Signal(dict)
    status_changed = Signal(str, bool)

    TIME_EVERY = 5.0
    INFO_EVERY = 12.0

    def _fetch(self, ws):
        return parse_players(self._query(ws, self.cfg["players_command"]))

    def run(self):
        cfg = self.cfg
        while not self._stop:
            ws = None
            try:
                self.status_changed.emit("Подключение...", False)
                ws = self._connect()
                self.status_changed.emit(f"RCON: подключено ({cfg['rcon_host']}:{cfg['rcon_port']})", True)
                last_time = 0.0
                last_info = 0.0
                while not self._stop:
                    self.players_updated.emit(self._fetch(ws))
                    if time.time() - last_info >= self.INFO_EVERY:
                        last_info = time.time()
                        info = parse_serverinfo(self._query(ws, "serverinfo", timeout=5))
                        if info:
                            self.info_updated.emit(info)
                    if time.time() - last_time >= self.TIME_EVERY:
                        last_time = time.time()
                        h = parse_env_time(self._query(ws, "env.time", timeout=3))
                        if h is not None:
                            self.time_updated.emit(h)
                    self._sleep(float(cfg["update_interval"]))
            except Exception as e:
                if not self._stop:
                    self.status_changed.emit(f"RCON ошибка: {e}. Повтор через 3с", False)
                    self._sleep(3)
            finally:
                self._close(ws)
        self.status_changed.emit("RCON: отключено", False)


class BasesWorker(_RconThread):
    """Опрос шкафов в отдельном соединении: тяжёлый ответ не тормозит обновление игроков."""
    bases_updated = Signal(list)
    status_changed = Signal(str)

    def run(self):
        every = float(self.cfg.get("bases_interval") or 0)
        if every <= 0:
            return
        self._ident = 5000
        while not self._stop:
            ws = None
            try:
                ws = self._connect()
                while not self._stop:
                    res = self._query(ws, "server.listtoolcupboards", timeout=20)
                    if res is not None:
                        self.bases_updated.emit(parse_bases(res))
                    elif not self._stop:  # при таймауте прошлый список не стираем
                        self.status_changed.emit("Базы (TC): нет ответа от сервера")
                    self._sleep(every)
            except Exception as e:
                if not self._stop:
                    self.status_changed.emit(f"Базы (TC): ошибка ({e})")
                    self._sleep(5)
            finally:
                self._close(ws)


class TeamsWorker(_RconThread):
    """Составы команд: global.teaminfo <steamid> — по одному игроку онлайн от каждой команды (раз в teams_interval
    секунд и сразу, когда у онлайн-команды поменялся состав онлайн). Отдельное соединение — игроков не тормозит."""
    team_info = Signal(dict)         # результат parse_teaminfo + "ts"
    status_changed = Signal(str)

    def __init__(self, cfg):
        super().__init__(cfg)
        self.targets = {}            # team (из playerlist) -> (steamid представителя, frozenset(steamid онлайн))
        self.extra = deque()         # steamid, по которым опросить срочно (кнопка в окне команды)
        self._done = {}              # team -> (время опроса, состав онлайн в тот момент)
        self._lock = threading.Lock()

    def set_targets(self, targets):
        with self._lock:
            self.targets = dict(targets)

    def ask(self, sid):
        self.extra.append(str(sid))

    def _ask_one(self, ws, sid):
        info = parse_teaminfo(self._query(ws, f"global.teaminfo {sid}", timeout=6))
        if info:
            info["ts"] = time.time()
            self.team_info.emit(info)
        return info

    def run(self):
        every = float(self.cfg.get("teams_interval") or 0)
        if every <= 0:
            return
        self._ident = 7000
        while not self._stop:
            ws = None
            try:
                ws = self._connect()
                while not self._stop:
                    while self.extra and not self._stop:
                        self._ask_one(ws, self.extra.popleft())
                    with self._lock:
                        targets = dict(self.targets)
                    now = time.time()
                    for team, (sid, online) in targets.items():
                        if self._stop:
                            break
                        prev = self._done.get(team)
                        if prev and now - prev[0] < every and prev[1] == online:
                            continue
                        self._done[team] = (now, online)
                        self._ask_one(ws, sid)
                        self._sleep(0.2)           # не заваливаем сервер пачкой запросов
                    self._sleep(2.0)
            except Exception as e:
                if not self._stop:
                    self.status_changed.emit(f"Команды: ошибка ({e})")
                    self._sleep(5)
            finally:
                self._close(ws)


class ChatSearchWorker(QThread):
    """Поиск по архиву чата в фоне: на больших архивах окно не зависает."""
    done = Signal(int, str, list, bool)

    def __init__(self, token, folder, query, parent=None):
        super().__init__(parent)
        self.token, self.folder, self.query = token, folder, query

    def run(self):
        res, more = search_chat_files(self.folder, self.query, 500)
        self.done.emit(self.token, self.query, res, more)


class ConsoleWorker(_RconThread):
    # Одно соединение, два дела:
    #  1) игровой чат (push-сообщения RCON + опрос chat.tail как страховка);
    #  2) консольные строки сервера -> LogStore (убийства, смерти, входы/выходы, ивенты...).
    chat_received = Signal(list)
    console_lines = Signal(list)       # [(время, тип, текст)] — вывод консоли сервера и ответы на команды
    status_changed = Signal(str)
    host_seen = Signal(str)

    POLL = 3.0
    TAIL = 40

    def __init__(self, cfg, store):
        super().__init__(cfg)
        self.store = store
        self.chat_on = bool(cfg.get("chat_enabled", True))
        self.live_on = bool(cfg.get("live_stats", True))
        self.outbox = deque(maxlen=50)   # команды, введённые в приложении
        self.cmds = {}                 # Identifier -> команда, ждём ответ
        self.clog = DayLog(console_dir())
        self.chatlog = DayLog(chat_dir(), dedup=True)

    def send_command(self, cmd):
        self.outbox.append(cmd)

    @staticmethod
    def _split(ts, typ, msg):
        text = msg if isinstance(msg, str) else ("" if msg is None else json.dumps(msg, ensure_ascii=False))
        return [(ts, typ, ln.rstrip()) for ln in text.splitlines() if ln.strip()]

    def _emit_console(self, items):
        for ts, typ, text in items:
            prefix = "" if typ in ("Generic", "None", "", "Cmd") else f"[{typ}] "
            self.clog.write(ts, f"{time.strftime('%H:%M:%S', time.localtime(ts))} {prefix}{text}")
        if items:
            self.console_lines.emit(items)

    def _live(self, text):
        lines = []
        for line in str(text or "").splitlines():
            line = line.strip()
            if line[:1] == "(" and line[9:13] == ") | ":  # на случай, если префикс времени пришёл
                line = line[13:]
            if LIVE_MARKERS_RE.search(line):
                lines.append(line)
        if lines:
            self.store.apply_live(time.time(), lines)

    @staticmethod
    def _last_int(v):
        nums = re.findall(r"-?\d+", str(v if v is not None else ""))
        return int(nums[-1]) if nums else None

    def _handshake(self, ws):
        # какой сейчас вайп (сид) и тот ли это сервер (hostname)
        seed = self._last_int(self._query(ws, "server.seed", timeout=4))
        world = self._last_int(self._query(ws, "server.worldsize", timeout=4))
        host = self._query(ws, "server.hostname", timeout=4)
        if seed is not None:
            self.store.note_seed(time.time(), seed, world, live=True)
        if host is not None:
            h = str(host)
            m = re.search(r'"(.*)"', h)
            h = (m.group(1) if m else h).strip()
            if h:
                self.host_seen.emit(h)

    def run(self):
        self._ident = 9000
        seen_set, seen_q = set(), deque()

        def fresh(entries):
            out = []
            for e in entries:
                k = e["key"]
                if k in seen_set:
                    continue
                seen_set.add(k)
                seen_q.append(k)
                if len(seen_q) > 3000:
                    seen_set.discard(seen_q.popleft())
                out.append(e)
            out.sort(key=lambda e: e["time"])
            return out

        while not self._stop:
            ws = None
            try:
                ws = self._connect()
                if self.live_on:
                    self._handshake(ws)
                pending = None
                last_poll = 0.0
                while not self._stop:
                    now = time.time()
                    while self.outbox:          # команды из окна «Консоль»
                        cmd = self.outbox.popleft()
                        self._ident += 1
                        self.cmds[self._ident] = cmd
                        while len(self.cmds) > 200:        # ответы, которые так и не пришли, не копим бесконечно
                            self.cmds.pop(next(iter(self.cmds)))
                        ws.send(json.dumps({"Identifier": self._ident, "Message": cmd, "Stacktrace": "", "Type": 3}))
                        self._emit_console([(now, "Cmd", "> " + cmd)])
                    if self.chat_on and now - last_poll >= self.POLL:
                        last_poll = now
                        self._ident += 1
                        pending = self._ident
                        ws.send(json.dumps({"Identifier": pending, "Message": f"chat.tail {self.TAIL}",
                                            "Stacktrace": "", "Type": 3}))
                    try:
                        ws.settimeout(0.5)
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(data, dict):
                        continue
                    is_chat = ((pending is not None and data.get("Identifier") == pending)
                               or str(data.get("Type")) == "Chat")
                    if is_chat:
                        if self.chat_on:
                            new = fresh(parse_chat(data.get("Message")))
                            for e in new:
                                self.chatlog.write(e["time"], chat_file_line(e))
                            if new:
                                self.chat_received.emit(new)
                    elif data.get("Identifier") in self.cmds:       # ответ на нашу команду
                        self.cmds.pop(data.get("Identifier"))
                        items = self._split(time.time(), "Reply", data.get("Message"))
                        self._emit_console(items or [(time.time(), "Reply", "(пустой ответ)")])
                    else:
                        msg = data.get("Message")
                        self._emit_console(self._split(time.time(), str(data.get("Type") or "Generic"), msg))
                        if self.live_on:
                            self._live(msg)
            except Exception as e:
                if not self._stop:
                    self.status_changed.emit(f"Консоль/чат: ошибка ({e}). Повтор через 5с")
                    self._sleep(5)
            finally:
                self._close(ws)
        self.clog.close()
        self.chatlog.close()


RE_RENDER_SAVED = re.compile(r"Saved map render to:\s*(.+?\.png)", re.IGNORECASE)
RE_RENDER_NAME = re.compile(r"map_(\d+)_(-?\d+)\.png", re.IGNORECASE)


def find_render_path(text):
    """'Saved map render to: D:\\Server\\rustds\\map_3750_505918.png' -> путь к png или None."""
    m = RE_RENDER_SAVED.search(str(text or ""))
    return m.group(1).strip() if m else None


def parse_render_name(name):
    """map_<размер>_<сид>.png -> (размер мира, сид) или None."""
    m = RE_RENDER_NAME.fullmatch(name)
    return (int(m.group(1)), m.group(2)) if m else None


class MapFetchWorker(_RconThread):
    """Просит сервер нарисовать карту (world.rendermap), ждёт строку «Saved map render to: <путь>»
    и копирует готовый png в папку профиля."""
    done = Signal(str, str)      # путь на сервере, копия в профиле
    missing = Signal(str)        # сервер сохранил карту, но файл отсюда не виден
    failed = Signal(str)

    WAIT_REPLY = 180             # сек: рендер большой карты бывает долгим
    WAIT_FILE = 15

    def __init__(self, cfg, dst, profile_id):
        super().__init__(cfg)
        self.dst = Path(dst)
        self.profile_id = profile_id

    def _wait_file(self, src):
        # файл может ещё дописываться — ждём, пока он появится и перестанет расти
        end = time.time() + self.WAIT_FILE
        last = -1
        while not self._stop and time.time() < end:
            try:
                size = src.stat().st_size
            except OSError:
                size = -1
            if size > 0 and size == last:
                return True
            last = size
            self._sleep(0.5)
        return False

    def run(self):
        ws = None
        try:
            ws = self._connect()
            self._ident += 1
            ws.send(json.dumps({"Identifier": self._ident, "Message": "world.rendermap",
                                "Stacktrace": "", "Type": 3}))
            deadline = time.time() + self.WAIT_REPLY
            path = None
            while not self._stop and time.time() < deadline and not path:
                try:
                    ws.settimeout(0.5)
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, dict):
                    path = find_render_path(data.get("Message"))
            if self._stop:
                return
            if not path:
                self.failed.emit("Сервер не прислал путь к карте (world.rendermap). Проверьте, что команда "
                                 "доступна на этом сервере.")
                return
            src = Path(path)
            self.dst.parent.mkdir(parents=True, exist_ok=True)
            got = False
            if not src.exists() and (self.cfg.get("srv_dir") or "").strip():
                # сервер на другом компьютере: забираем png через настроенный доступ к папке сервера
                for _ in range(5):
                    try:
                        if fetch_render_png(self.cfg, path, self.dst):
                            got = True
                            break
                    except Exception:
                        pass
                    if self._stop:
                        return
                    self._sleep(3)
            if not got:
                if not self._wait_file(src):
                    self.missing.emit(path)
                    return
                shutil.copy2(src, self.dst)
            info = parse_render_name(src.name)
            crop_render_margin(self.dst, info[0] if info else self.cfg.get("world_size"), force=True)
            self.done.emit(path, str(self.dst))
        except Exception as e:
            if not self._stop:
                self.failed.emit(f"{e}")
        finally:
            self._close(ws)


class MapSourceWorker(QThread):
    """Берёт файл карты .map из папки сервера (локально или по FTP) и кладёт копию в папку профиля."""
    status = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    not_ready = Signal(str)      # карты текущего вайпа в папке ещё нет
    hostkey = Signal(str)        # отпечаток ключа SSH-сервера: запомнить при первом подключении

    def __init__(self, cfg, seed=None):
        super().__init__()
        self.cfg = dict(cfg)
        self.seed = seed

    def run(self):
        try:
            info = {}
            path = fetch_map(self.cfg, mapsrc_dir(), self.seed, self.status.emit, info, strict=bool(self.seed))
            if info.get("host_fp"):
                self.hostkey.emit(info["host_fp"])
            self.done.emit(str(path))
        except MapNotReady as e:
            self.not_ready.emit(str(e))
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)


class MapRenderWorker(QThread):
    """Рисует png карты из .map и заодно вытаскивает из него монументы (кэш рядом с архивом карт)."""
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, map_path, seed=None, out_path=None):
        super().__init__()
        self.map_path = Path(map_path)
        self.seed = seed
        self.out_path = Path(out_path) if out_path else None     # None — текущая карта профиля

    def run(self):
        try:
            from rustmap_parser import ExportConfig, ExportOptions, RustMapExporter, TerrainOptions

            RENDER_DIR = render_dir()
            RENDER_DIR.mkdir(parents=True, exist_ok=True)
            config = ExportConfig(
                map_path=self.map_path,
                output_dir=RENDER_DIR,
                exports=ExportOptions(terrain=TerrainOptions(full_size=True, formats=("png",)), monuments=True),
                status_updates=False,
                timing_debug=False,
            )
            result = RustMapExporter(config).run()
            generated = Path(result.full_map_image)
            if not generated.exists():
                raise FileNotFoundError(f"Результат рендера не найден: {generated}")
            out = self.out_path or map_output()
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(generated, out)
            self._save_monuments(result)
            self.finished_ok.emit(str(out))
        except Exception as e:
            self.failed.emit(str(e))

    def _save_monuments(self, result):
        """Подписи монументов — приятное дополнение: если не вышло, карта всё равно загружается."""
        try:
            mf = getattr(result, "monuments_file", None)
            if not mf:
                return
            data = compact_monuments(json.loads(Path(mf).read_text(encoding="utf-8")))
            info = parse_map_name(self.map_path.name)
            world = data["world"] or (info[0] if info else 0)
            seed = self.seed or (info[1] if info else None)
            if world and seed:
                save_monuments(maps_dir() / monuments_file_name(world, seed), data)
        except Exception as e:
            log_error(f"Монументы из .map: {e}")