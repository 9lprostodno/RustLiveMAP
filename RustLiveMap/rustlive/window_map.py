"""Rust Live Map: модуль window_map."""

import datetime
import shutil
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox
from pathlib import Path

from .config import (
    BASE_DIR,
    map_output,
    maps_dir,
    save_config,
)
from .dialogs import (
    WipesDialog,
)
from .gamedata import (
    grid_ref,
)
from .maps import (
    archive_current_map,
    archive_map_path,
    crop_render_margin,
    size_matches,
    map_size_note,
    map_file_name,
    shrink_archive,
)
from .mapsource import (
    parse_map_name,
)
from .periods import (
    is_range_period,
    is_wipe_period,
)
from .workers import (
    MapFetchWorker,
    MapRenderWorker,
    parse_render_name,
)


class MapMixin:
    """Карта, архив карт, вайпы и границы вайпов."""

    def map_menu(self, pos):
        m = QMenu(self)
        m.addAction(self.a_fit)
        m.addAction(self.a_grid)       # с галочкой: клик — включить, ещё клик — выключить
        m.addAction(self.a_monuments)
        m.addAction("↺ Сброс панелей", self.reset_layout)
        m.exec(self.view.mapToGlobal(pos))

    # ---------- карта ----------
    def ask_map_kind(self, title, text):
        """«Файл .map» или «PNG» -> ".map" / "png"; None — отмена."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        b_map = box.addButton("Файл .map", QMessageBox.ButtonRole.AcceptRole)
        b_png = box.addButton("PNG", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        return ".map" if clicked is b_map else "png" if clicked is b_png else None

    def choose_local_map(self):
        """Папки сервера нет: предлагаем взять карту с этого компьютера — файл .map или готовый PNG."""
        kind = self.ask_map_kind("Обновить карту",
                                 "Папка сервера не указана.\n\nКакую карту загрузить с этого компьютера?")
        if kind:
            self.load_local_map(kind)

    def load_local_map(self, kind):
        is_map = kind == ".map"
        start = self.cfg.get("map_path") or str(BASE_DIR)
        path, _ = QFileDialog.getOpenFileName(
            self, "Файл карты .map" if is_map else "Файл карты PNG", start,
            "Rust map (*.map)" if is_map else "Изображение (*.png *.jpg *.jpeg)")
        if not path:
            return
        if is_map:
            info = parse_map_name(Path(path).name)       # proceduralmap.<размер>.<сид>.<версия>.map
            world = info[0] if info else self._ask_world_size(None, "")
            if not world:
                return
            self.cfg["world_size"] = int(world)
            self.cfg["map_path"] = path
            if info:
                self.cfg["map_seed"] = info[1]
            else:
                self.mark_map_current()
            save_config(self.cfg)
            self.view.set_world_size(world)
            if self.render_worker and self.render_worker.isRunning():
                return
            self._from_folder = False
            self.map_label.setText("Рисую карту из .map…")
            self.busy.show()
            self.render_worker = MapRenderWorker(path, info[1] if info else None)
            self.render_worker.finished_ok.connect(self.on_render_done)
            self.render_worker.failed.connect(self.on_render_failed)
            self.render_worker.start()
            return
        self.apply_local_png(path)

    def _ask_world_size(self, guess, dims):
        """Размер мира спрашиваем только когда его не из чего узнать."""
        world, ok = QInputDialog.getInt(
            self, "Размер карты",
            "Не удалось определить размер мира. Введите его в метрах (server.worldsize), например 3750 или 4250."
            + (f"\nРазмер PNG: {dims}." if dims else ""),
            int(guess or self.cfg["world_size"]), 1000, 8000, 50)
        return world if ok else 0

    def apply_local_png(self, path):
        """PNG с этого компьютера. Размер мира берём без вопросов, если он известен: из имени файла
        (map_<размер>_<сид>.png) или из настроек сервера (server.worldsize), при условии что картинка ему
        соответствует (мир, мир + поля 2×500 м, любой стандартный масштаб). Иначе спрашиваем."""
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Карта", "Не удалось прочитать картинку.")
            return
        dims = f"{img.width()}×{img.height()} px"
        info = parse_render_name(Path(path).name)
        world = int(info[0]) if info else int(self.cfg["world_size"])
        if img.width() != img.height():
            QMessageBox.warning(self, "Карта", f"Карта должна быть квадратной, а у картинки {dims}.")
            return
        if not size_matches(img.width(), world):
            world = self._ask_world_size(world, dims)
            if not world:
                return
            if not size_matches(img.width(), world):
                r = QMessageBox.question(
                    self, "Размер карты",
                    f"Картинка {dims} не похожа на карту мира {world} м (ожидается ≈{world} или ≈{world + 1000} px "
                    "или кратно им).\n\nВсё равно использовать? Точки на карте могут сместиться.")
                if r != QMessageBox.StandardButton.Yes:
                    return
        try:
            map_output().parent.mkdir(parents=True, exist_ok=True)
            if Path(path).resolve() != map_output().resolve():
                shutil.copy2(path, map_output())
        except OSError as e:
            QMessageBox.warning(self, "Карта", f"Не удалось скопировать файл: {e}")
            return
        self.cfg["world_size"] = int(world)
        crop_render_margin(map_output(), world)           # поля сервера срезаются сами; чистую карту не трогает
        self.apply_server_map(path, world)

    # ---- карта прямо с сервера (world.rendermap) ----
    def fetch_map_from_server(self, auto=False):
        """Основной путь — файл .map из папки сервера (SMB, FTP, SFTP, локально); запасной — команда сервера
        world.rendermap. auto=True — запуск без участия пользователя: ошибки показываем в строке карты, а не окнами."""
        self._auto_fetch = bool(auto)
        if not auto:
            self._map_retries = 0
            self._upgrades = 0
        if self.map_source_set():
            return self.fetch_map_from_folder()
        if auto:                                   # без папки сами по сети карту берём только командой сервера
            return self.fetch_map_via_rcon()
        self.choose_local_map()

    def auto_load_map(self):
        """Включена папка сервера — карту берём из неё сами (при запуске, смене сервера и сохранении настроек)."""
        if self.map_source_set() and self.archive_wipe is None:
            self.fetch_map_from_server(auto=True)

    def fetch_map_via_rcon(self):
        if not self.cfg.get("rcon_password"):
            QMessageBox.information(self, "Карта с сервера", "Сначала укажите RCON-пароль в настройках.")
            return
        if self.fetch_worker and self.fetch_worker.isRunning():
            return
        self.map_label.setText("Сервер рисует карту (world.rendermap)…")
        self.busy.show()
        w = MapFetchWorker(self.cfg, map_output(), self.cfg.get("active_profile"))
        w.done.connect(self.on_map_fetched)
        w.missing.connect(self.on_map_missing)
        w.failed.connect(self.on_map_fetch_failed)
        self.fetch_worker = w
        w.start()

    def _fetch_is_stale(self, w):
        return w.profile_id != self.cfg.get("active_profile")   # пока ждали, переключили сервер

    def on_map_fetched(self, src, dst):
        self.busy.hide()
        if self._fetch_is_stale(self.fetch_worker):
            return
        self.apply_server_map(src)

    def on_map_fetch_failed(self, err):
        self.busy.hide()
        if getattr(self, "_auto_fetch", False):
            self.map_label.setText(f"⚠ Карта: {err}")
            self._schedule_map_retry()
            return
        self.load_profile_map()
        QMessageBox.warning(self, "Карта с сервера", err)

    def on_map_missing(self, path):
        self.busy.hide()
        if self._fetch_is_stale(self.fetch_worker):
            return
        self.load_profile_map()
        r = QMessageBox.question(
            self, "Карта с сервера",
            f"Сервер сохранил карту:\n{path}\n\nно этот файл недоступен программе "
            "(сервер на другом компьютере?).\n\nВыбрать png вручную?")
        if r != QMessageBox.StandardButton.Yes:
            return
        start = str(Path(path).parent) if Path(path).parent.exists() else str(BASE_DIR)
        chosen, _ = QFileDialog.getOpenFileName(self, "Файл карты (png)", start, "Изображение (*.png *.jpg)")
        if not chosen:
            return
        try:
            shutil.copy2(chosen, map_output())
        except OSError as e:
            QMessageBox.warning(self, "Карта с сервера", f"Не удалось скопировать файл: {e}")
            return
        info = parse_render_name(Path(path).name) or parse_render_name(Path(chosen).name)
        img = QImage(str(map_output()))
        dims = f"{img.width()}×{img.height()} px" if not img.isNull() else "неизвестен"
        world, ok = QInputDialog.getInt(
            self, "Размер карты",
            f"Размер мира в метрах (server.worldsize), например 3750 или 4250.\n"
            f"Размер выбранного PNG: {dims}. Карта с сервера (world.rendermap) шире мира на 1000 м "
            "— лишние поля будут обрезаны.",
            int(info[0] if info else self.cfg["world_size"]), 1000, 8000, 50)
        if not ok:
            return
        self.cfg["world_size"] = world
        crop_render_margin(map_output(), world)
        self.apply_server_map(chosen, world)

    def apply_server_map(self, src, world=None):
        # размер мира и сид берём из имени файла map_<размер>_<сид>.png (или явно заданный размер)
        info = parse_render_name(Path(src).name)
        if world:
            self.cfg["world_size"] = int(world)
        elif info and info[0] != int(self.cfg["world_size"]):
            self.cfg["world_size"] = info[0]
        self.view.set_world_size(self.cfg["world_size"])
        if not self.view.load_map(map_output()):
            self.map_label.setText("Не удалось загрузить PNG")
            return
        self.cfg["map_path"] = str(src)
        if info:
            self.cfg["map_seed"] = info[1]
            cur = self.log_store.current_seed()
            self.map_stale = bool(cur and str(cur) != info[1])
            self._map_prompted = not self.map_stale
        else:
            self.mark_map_current()
        save_config(self.cfg)
        self.map_label.setText(f"Карта: {Path(src).name}" + map_size_note(map_output(), self.cfg["world_size"]))
        self._map_retries = 0
        self.archive_store_current(True)
        self.refresh_markers()
        self.update_event_marks()
        self.check_map_seed()
        self.refresh_heat(True)
        self.refresh_monuments()
        self.maybe_upgrade_from_folder()

    # ---- архив карт старых вайпов: maps/map_<размер>_<сид>.png ----

    def archive_store_current(self, overwrite=False):
        # карта текущего вайпа -> в архив (чтобы после вайпа её можно было открыть по датам)
        cur = self.log_store.current_wipe()
        if not cur or not cur["seed"] or self.map_stale or not map_output().exists():
            return
        ms = str(self.cfg.get("map_seed") or "")
        if ms and ms != str(cur["seed"]):
            return
        archive_current_map(cur["seed"], cur["world"] or self.cfg["world_size"], overwrite)

    def archive_previous_map(self):
        # после вайпа в map.png ещё карта прошлого вайпа: убираем её в архив (и ужимаем), пока не заменили
        ms = str(self.cfg.get("map_seed") or "")
        cur = self.log_store.current_seed()
        if not ms or not cur or ms == str(cur) or ms == self._arch_prev or not map_output().exists():
            return
        if (self.fetch_worker and self.fetch_worker.isRunning()) or \
                (self.render_worker and self.render_worker.isRunning()):
            return   # map.png как раз перезаписывается
        self._arch_prev = ms
        w = next((w for w in self.log_store.wipes if str(w["seed"]) == ms), None)
        dst = archive_current_map(ms, (w and w["world"]) or self.cfg["world_size"])
        if dst:
            shrink_archive(dst)

    def period_wipe(self):
        """Вайп, чья карта соответствует периоду: выбранный вайп; для диапазона/всего времени — последний."""
        per = self.log_store.period
        ws = list(self.log_store.wipes)
        if is_wipe_period(per):
            return next((w for w in ws if w["id"] == per[1]), None)
        if is_range_period(per):
            ws = list(self.log_store.wipes_in_range(per[1], per[2]))
        return ws[-1] if ws else None

    def sync_map_to_period(self, reset=True):
        """Карта и Heatmap следуют за периодом: вайп периода -> его карта (текущая живая или из архива)."""
        w = self.period_wipe()
        cur = self.log_store.current_wipe()
        if w is None or cur is None or w["id"] == cur["id"]:
            self.apply_view_wipe(None)
        else:
            self.apply_view_wipe(w["id"])

    def apply_view_wipe(self, wid):
        shown = self.archive_wipe["id"] if self.archive_wipe else None
        if wid == shown:
            return
        if wid is None:
            self.archive_wipe = None
            self.log_store.set_view_wipe(None)
            self.load_profile_map()
            self.check_map_seed()
        else:
            w = next((w for w in self.log_store.wipes if w["id"] == wid), None)
            if w is None:
                return self.apply_view_wipe(None)
            self.archive_wipe = w
            self.log_store.set_view_wipe(wid)
            world = int(w["world"] or self.cfg["world_size"])
            self.view.set_world_size(world)
            path = archive_map_path(w, world)
            start = self.log_store.wipe_start_label(w)
            if path and self.view.load_map(path):
                self.map_label.setText(f"📁 Архив: вайп {start} · сид {w['seed'] or 'неизвестен'}")
            else:
                self.view.load_blank("Карта этого вайпа не сохранена\n\n"
                                     "«🗂 Границы вайпов…» → «Загрузить карту…»")
                self.map_label.setText(f"📁 Архив: вайп {start} · сид {w['seed'] or 'неизвестен'} · карты нет")
        self._html_cache.clear()
        self.refresh_monuments()
        self.refresh_markers()
        self.update_event_marks()
        self.refresh_heat(True)
        self.refresh_details()
        self.refresh_server_tab()
        self.refresh_teams()

    def assign_map_png(self, w=None):
        """Карта для выбранного вайпа: файл .map (рисуется и даёт подписи монументов) или готовый PNG."""
        w = w or self.archive_wipe or self.log_store.current_wipe()
        if not w:
            return
        kind = self.ask_map_kind("Карта вайпа", f"Какую карту загрузить для вайпа (сид {w['seed'] or 'неизвестен'})?")
        if not kind:
            return
        cur = self.log_store.current_wipe()
        is_cur = bool(cur and cur["id"] == w["id"])
        is_map = kind == ".map"
        path, _ = QFileDialog.getOpenFileName(
            self, f"{'.map' if is_map else 'png'} карты вайпа (сид {w['seed'] or 'неизвестен'})",
            str(maps_dir() if maps_dir().is_dir() else BASE_DIR),
            "Rust map (*.map)" if is_map else "Изображение (*.png *.jpg *.jpeg)")
        if not path:
            return
        if is_map:
            return self._assign_map_file(w, path, is_cur)
        img = QImage(path)
        if img.isNull() or img.width() != img.height():
            QMessageBox.warning(self, "Карта вайпа", "Нужна квадратная картинка.")
            return
        info = parse_render_name(Path(path).name)
        world = int((info[0] if info else 0) or w["world"] or self.cfg["world_size"])
        if not size_matches(img.width(), world):
            world = self._ask_world_size(world, f"{img.width()}×{img.height()} px")
            if not world:
                return
        dst = maps_dir() / map_file_name(world, w["seed"] or f"w{w['id']}")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if Path(path).resolve() != dst.resolve():
                shutil.copy2(path, dst)
            crop_render_margin(dst, world)                  # поля серверного png срезаются сами
            if is_cur:                          # текущий вайп: это и живая карта
                shutil.copy2(dst, map_output())
        except OSError as e:
            QMessageBox.warning(self, "Карта вайпа", f"Не удалось сохранить: {e}")
            return
        if is_cur:
            self.cfg["map_seed"] = str(w["seed"])
            self.cfg["world_size"] = int(world)
            save_config(self.cfg)
            if self.archive_wipe is None:
                self.load_profile_map()
                self.check_map_seed()
                self.refresh_heat(True)
        else:
            shrink_archive(dst)
            self._show_archive_map(w)

    def _show_archive_map(self, w):
        if self.archive_wipe is not None and self.archive_wipe["id"] == w["id"]:
            self.archive_wipe = None        # показан именно этот вайп — перерисовать карту
            self.apply_view_wipe(w["id"])

    def _assign_map_file(self, w, path, is_cur):
        info = parse_map_name(Path(path).name)
        world = info[0] if info else self._ask_world_size(w["world"], "")
        if not world:
            return
        seed = w["seed"] or (info[1] if info else None)
        if w["seed"] and info and str(info[1]) != str(w["seed"]):
            r = QMessageBox.question(
                self, "Карта вайпа",
                f"Сид карты ({info[1]}) не совпадает с сидом вайпа ({w['seed']}). Всё равно использовать?")
            if r != QMessageBox.StandardButton.Yes:
                return
        if self.render_worker and self.render_worker.isRunning():
            QMessageBox.information(self, "Карта вайпа", "Идёт рендер другой карты — подождите.")
            return
        self.busy.show()
        if is_cur:                                  # текущий вайп — обычная загрузка карты из .map
            self.map_label.setText("Рисую карту из .map…")
            self.cfg["world_size"] = int(world)
            self.cfg["map_path"] = path
            if seed:
                self.cfg["map_seed"] = str(seed)
            save_config(self.cfg)
            self.view.set_world_size(world)
            self._from_folder = False
            self.render_worker = MapRenderWorker(path, seed)
            self.render_worker.finished_ok.connect(self.on_render_done)
        else:                                       # прошлый вайп — картинка уходит в архив, живую карту не трогаем
            dst = maps_dir() / map_file_name(world, seed or f"w{w['id']}")
            self.render_worker = MapRenderWorker(path, seed, out_path=dst)
            self.render_worker.finished_ok.connect(lambda png, w=w: self._on_archive_render(png, w))
        self.render_worker.failed.connect(self.on_render_failed)
        self.render_worker.start()

    def _on_archive_render(self, png, w):
        self.busy.hide()
        shrink_archive(png)
        self._show_archive_map(w)

    def check_long_wipes(self):
        # вайп не длится дольше месяца: если в базе длиннее — смены карты пропущены, предлагаем расставить границы
        asked = set(self.cfg.get("split_asked") or [])
        for w in self.log_store.long_wipes():
            if w["id"] in asked:
                continue
            start, end = self.log_store.wipe_span(w)
            f = lambda t: datetime.datetime.fromtimestamp(t).strftime("%d.%m.%Y")
            box = QMessageBox(self)
            box.setWindowTitle("Слишком длинный вайп")
            box.setText(f"Один вайп идёт с {f(start)} по {f(end)} — {int((end - start) / 86400)} дн.\n\n"
                        "Вайп не длится дольше месяца (он заканчивается в первый четверг), значит, часть смен "
                        "карты программа не увидела.\nРасставить границы по первым четвергам?")
            yes = box.addButton("📅 Предложить границы", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
            never = box.addButton("Не спрашивать", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() is yes:
                self.manage_wipes(w["id"])
            elif box.clickedButton() is never:
                asked.add(w["id"])
                self.cfg["split_asked"] = sorted(asked)
                save_config(self.cfg)
            return

    def manage_wipes(self, focus_wid=None):
        dlg = WipesDialog(self.log_store, self, focus_wid if type(focus_wid) is int else None)
        dlg.exec()
        self._period_sig = None
        if is_wipe_period(self.log_store.period) and \
                not any(w["id"] == self.log_store.period[1] for w in self.log_store.wipes):
            self.log_store.set_period("current")      # выбранный вайп слили
        self.fill_periods(True)
        self._arch_seed = None
        self._period_changed()

    def open_maps_dir(self):
        d = maps_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def load_profile_map(self):
        self.view.set_world_size(self.cfg["world_size"])
        crop_render_margin(map_output(), self.cfg["world_size"])   # карты, скачанные раньше с полями
        if map_output().exists() and self.view.load_map(map_output()):
            name = Path(self.cfg.get("map_path") or "").name or map_output().name
            self.map_label.setText(f"Карта: {name}")
        else:
            self.view.clear_map()
            self.map_label.setText("Карта не загружена")
        self.map_stale = False
        self.refresh_monuments()

    def mark_map_current(self):
        # пользователь выбрал карту -> считаем, что она от текущего вайпа
        cur = self.log_store.current_seed()
        if cur:
            self.cfg["map_seed"] = str(cur)
        self.map_stale = False
        self._map_prompted = False

    def check_map_seed(self):
        if self.archive_wipe is not None:
            return
        cur = self.log_store.current_seed()
        if not cur or not map_output().exists():
            return
        ms = str(self.cfg.get("map_seed") or "")
        if not ms:   # карта была загружена раньше сида — считаем её актуальной
            self.cfg["map_seed"] = str(cur)
            save_config(self.cfg)
            stale = False
        else:
            stale = ms != str(cur)
        if stale != self.map_stale:
            self.map_stale = stale
            base = self.map_label.text().replace(" ⚠ ждём карту нового вайпа", "")
            self.map_label.setText(base + (" ⚠ ждём карту нового вайпа" if stale else ""))
            self.update_event_marks()
        if stale:
            self.archive_previous_map()
            busy = any(w and w.isRunning() for w in (self.src_worker, self.fetch_worker, self.render_worker))
            if not busy:
                # карта прошлого вайпа с новыми координатами не совпадёт (а размер мира мог смениться) — не показываем её
                self.view.load_blank("Новый вайп: ждём карту")
        elif self._arch_seed != cur:
            self._arch_seed = cur
            self.archive_store_current(False)
        if stale and not self._map_prompted:
            self._map_prompted = True
            QTimer.singleShot(0, self.prompt_new_map)

    def prompt_new_map(self):
        cur = self.log_store.current_seed()
        if self.map_source_set() or self.cfg.get("rcon_password"):   # основной путь — карта с сервера, без вопросов
            self.fetch_map_from_server(auto=True)
            return
        r = QMessageBox.question(
            self, "Новый вайп — нужна новая карта",
            f"Сервер сменил карту (сид {cur}), а получить её не из чего.\n\n"
            "Укажите в «Настройках» папку сервера (SMB, FTP, SFTP) или RCON-пароль — тогда новая карта будет "
            "загружаться сама.\n\nОткрыть настройки?")
        if r == QMessageBox.StandardButton.Yes:
            self.open_settings()

    def on_new_wipe(self, wipe_id, seed, world):
        # вайп: карта прошлого вайпа -> в архив; старые шкафы больше не актуальны
        self.archive_previous_map()
        self.selected_base = None
        self.on_bases([])
        if world and int(world) != int(self.cfg["world_size"]):
            self.cfg["world_size"] = int(world)
            self.view.set_world_size(world)
        self.log_store.set_period("current")
        self._html_cache.clear()
        self.fill_periods(True)
        self.sync_map_to_period()
        self._map_prompted = False
        self.check_map_seed()
        self.refresh_details()
        self.refresh_server_tab()

    def on_render_done(self, png):
        self.busy.hide()
        w = self.render_worker
        if self.view.load_map(png):
            self.map_label.setText(f"Карта: {Path(self.cfg['map_path']).name}"
                                   + map_size_note(png, self.cfg["world_size"]))
            self.finish_render(getattr(w, "seed", None) or (parse_map_name(Path(self.cfg["map_path"]).name) or (0, None))[1])
        else:
            self._from_folder = False
            self.map_label.setText("Не удалось загрузить PNG")

    def on_render_failed(self, err):
        self.busy.hide()
        self.map_label.setText("Ошибка рендера")
        QMessageBox.critical(self, "Ошибка рендера карты", err)

    def on_cursor(self, x, z):
        self.coord_label.setText(f"X {x:.0f}  Z {z:.0f}  [{grid_ref(x, z, self.view.world_size)}]")
