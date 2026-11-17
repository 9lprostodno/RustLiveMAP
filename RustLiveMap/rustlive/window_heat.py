"""Rust Live Map: модуль window_heat."""

from .config import (
    save_config,
)
from .heat import (
    HEAT_FLOOR_MOVE,
    HEAT_FLOOR_PLAYER,
    HEAT_FLOOR_PVP,
    heat_dims,
    heat_levels,
)
from .periods import (
    is_range_period,
)


class HeatMixin:
    """Heatmap: общий и по одному игроку."""

    # ---- Heatmap ----
    def _heat_btn_text(self):
        names = {"off": "🔥 Heatmap", "move": "🔥 Heatmap: движение", "pvp": "🔥 Heatmap: PvP"}
        self.heat_btn.setText(names.get(self.cfg.get("heat_mode", "off"), "🔥 Heatmap"))

    def set_heat_mode(self, mode):
        self.cfg["heat_mode"] = mode
        save_config(self.cfg)
        self._heat_btn_text()
        self.refresh_heat(True)

    def refresh_heat(self, force=False):
        mode = "player" if self.player_heat else self.cfg.get("heat_mode", "off")
        if mode not in ("move", "pvp", "player") or not self.view.pix_item:
            if self._heat_sig is not None or force:
                self._heat_sig = None
                self.view.set_heat(None, self.view.world_size)
            self.heat_label.setText("")
            return
        wipe = self.archive_wipe or self.log_store.current_wipe()
        if not wipe:
            self.view.set_heat(None, self.view.world_size)
            self.heat_label.setText("")
            return
        per = self.log_store.period
        t1, t2 = (per[1], per[2]) if is_range_period(per) else (None, None)
        idle = False                      # «Передвижение» считает только идущих: стоящие (базы, афк) не учитываются
        sig = (mode, idle, wipe["id"], t1, t2, self.log_store.version, self.log_store.heat_version,
               id(self.view.pix_item), self.player_heat)
        if sig == self._heat_sig and not force:
            return
        world = float(wipe["world"] or self.cfg["world_size"])
        if mode == "player":
            cells = self.log_store.heat_move(wipe["id"], t1, t2, idle, sid=self.player_heat)
            what = "передвижения " + self.log_store.name_of(self.player_heat)
        elif mode == "move":
            cells = self.log_store.heat_move(wipe["id"], t1, t2, idle)
            what = "передвижение"
        else:
            cells = self.log_store.heat_pvp(wipe["id"], world, t1, t2)
            what = "PvP"
        gx, gz = heat_dims(world)
        floor = {"move": HEAT_FLOOR_MOVE, "player": HEAT_FLOOR_PLAYER}.get(mode, HEAT_FLOOR_PVP)
        self.view.set_heat(heat_levels(cells, gx, gz, floor), world)
        self._heat_sig = sig
        if not cells:
            hint = ("данных нет: позиции копятся, пока программа подключена к серверу"
                    if mode in ("move", "player") else "убийств игрок→игрок с координатами за период нет")
            self.heat_label.setText(f"  Heatmap ({what}): {hint}")
        elif mode == "player":
            self.heat_label.setText(f"  🔥 {what}, клеток {len(cells)}")
        elif mode == "pvp":
            self.heat_label.setText(f"  Heatmap PvP: убийств {sum(cells.values())}")
        else:
            self.heat_label.setText(f"  Heatmap: {what}, клеток {len(cells)}")

    def stop_player_heat(self):
        if self.player_heat:
            self.player_heat = None
            self.refresh_heat(True)

    def show_player_heat(self, sid):
        self.select_player(sid)           # заодно снимет карту другого игрока
        self.player_heat = sid
        self.refresh_heat(True)
