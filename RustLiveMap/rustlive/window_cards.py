"""Rust Live Map: модуль window_cards."""

import html
import time

from .events import (
    CAT_STYLE,
    ENV_NAMES,
    PVE_NAMES,
    REPORT_NAMES,
    event_info,
    sparkline,
)
from .gamedata import (
    EXTRA_LABELS,
    fmt_time,
    grid_ref,
)


class CardsMixin:
    """Карточки игроков и вкладка «Сервер»: HTML со статистикой."""

    def refresh_server_tab(self):
        s = self.log_store.summary()
        gray = "color:#999"
        if not s["n_players"] and not s["events"]:
            self._set_html(self.server_view,
                           "<i style='color:#999'>Статистики пока нет. Она копится из консоли RCON, пока программа подключена к серверу.</i>")
            return
        parts = [
            f"<h3 style='margin:0 0 4px 0'>Статистика сервера <span style='{gray};font-weight:normal;"
            f"font-size:12px'>· {html.escape(self.log_store.period_label())}</span></h3>",
            f"<p style='{gray};margin:0'>Игроков в базе: {s['n_players']} · PvP-убийств: {s['kills']} · "
            f"убито NPC: {s['npc']} · смертей: {s['deaths']}</p>"]

        ents = s["ents"]
        if ents:
            vals = [n for _t, n in ents]
            t0 = time.strftime("%d.%m %H:%M", time.localtime(ents[0][0]))
            parts.append(
                "<h4 style='margin:10px 0 2px 0'>Нагрузка: сущностей в мире</h4>"
                f"<p style='font-family:Consolas,monospace;margin:0'>{sparkline(vals[-40:])}</p>"
                f"<p style='{gray};margin:0'>сейчас {vals[-1]:,} · пик {max(vals):,} · мин {min(vals):,} "
                f"(по записям сохранений с {t0})</p>")

        if s["events"]:
            ev_rows = "".join(
                f"<tr><td style='padding-right:10px'>{html.escape(name)}</td><td>×{e['count']}</td>"
                f"<td style='{gray};padding-left:10px'>"
                f"{time.strftime('%d.%m %H:%M', time.localtime(e['last']))}</td></tr>"
                for name, e in sorted(s["events"].items(), key=lambda kv: kv[1]["last"], reverse=True))
            parts.append(f"<h4 style='margin:10px 0 2px 0'>События мира (последний раз)</h4><table>{ev_rows}</table>")

        def board(title, rows, fmt):
            if not rows:
                return ""
            body = "".join(
                f"<tr><td style='{gray};padding-right:6px'>{i}.</td><td>{self._plink(r[1], r[0])}</td>"
                f"<td style='padding-left:10px'>{fmt(r)}</td></tr>" for i, r in enumerate(rows, 1))
            return f"<h4 style='margin:10px 0 2px 0'>{title}</h4><table>{body}</table>"

        parts.append(board("Топ по PvP-убийствам", s["top_kills"], lambda r: f"{r[2]} (смертей {r[3]})"))
        parts.append(board("Лучший K/D (от 5 убийств)", s["top_kd"], lambda r: f"{r[2]:.2f} ({r[3]}/{r[4]})"))
        parts.append(board("Дольше всех в игре", s["top_time"],
                           lambda r: f"{self._fmt_long(r[2])} · {r[3]} сессий"))
        parts.append(board("Больше всего смертей", s["top_deaths"], lambda r: str(r[2])))
        rp = s["reports"]
        if rp["total"]:
            types = ", ".join(f"{html.escape(REPORT_NAMES.get(k, k))} ×{v}"
                              for k, v in sorted(rp["types"].items(), key=lambda kv: -kv[1]))
            parts.append(f"<h4 style='margin:10px 0 2px 0'>Репорты игроков</h4>"
                         f"<p style='{gray};margin:0'>всего {rp['total']} · {types}</p>")
            parts.append(board("На кого жаловались чаще всего", rp["top"],
                               lambda r: f"{r[2]} от {r[3]} игр. · " + ", ".join(
                                   f"{html.escape(REPORT_NAMES.get(k, k))} ×{v}" for k, v in r[4].items())))
            rows = "".join(
                f"<tr><td style='{gray};padding-right:8px'>{time.strftime('%d.%m %H:%M', time.localtime(ts))}</td>"
                f"<td>{self._plink(rn, rs)} → {self._plink(tn, ts_)}</td>"
                f"<td style='padding-left:8px'>{html.escape(REPORT_NAMES.get(c, c))}"
                f"{' — ' + html.escape(t) if t else ''}</td></tr>"
                for ts, rs, rn, ts_, tn, c, t in rp["recent"])
            parts.append(f"<h4 style='margin:10px 0 2px 0'>Последние репорты</h4><table>{rows}</table>")
        self._set_html(self.server_view, "".join(parts))

    def _set_html(self, widget, text):
        # не пересобираем виджет, если ничего не изменилось, и не сбрасываем прокрутку
        if self._html_cache.get(widget) == text:
            return
        self._html_cache[widget] = text
        sb = widget.verticalScrollBar()
        v = sb.value()
        widget.setHtml(text)
        sb.setValue(v)

    def on_anchor(self, url):
        s = url.toString()
        if s.startswith("player:"):
            self.select_player(s[len("player:"):])

    @staticmethod
    def _fmt_ts(ts):
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else "—"

    @staticmethod
    def _fmt_long(sec):
        sec = int(sec)
        d, rem = divmod(sec, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return f"{d}д {h}ч"
        return f"{h}ч {m}м" if h else f"{m}м"

    @staticmethod
    def _top(d, n=3):
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]

    @staticmethod
    def _kd(st):
        deaths = st["deaths_pvp"]
        return st["kills"] / deaths if deaths else float(st["kills"])

    @staticmethod
    def _plink(name, sid):
        esc = html.escape
        if sid:
            return f"<a href='player:{sid}'>{esc(name or sid)}</a>"
        return esc(name)

    def log_summary(self, sid):
        st = self.log_store.get(sid)
        if not st:
            return ""
        return f"PvP: {st['kills']} убийств / {st['deaths_pvp']} смертей · K/D {self._kd(st):.2f}"

    def _memo(self, key, build):
        # HTML карточки зависит только от версии данных — не собираем его заново каждую секунду
        val = self._memo_cache.get(key)
        if val is None:
            if len(self._memo_cache) > 64:
                self._memo_cache.clear()
            val = self._memo_cache[key] = build()
        return val

    def log_profile_html(self, sid):
        return self._memo(("p", sid, self.log_store.version), lambda: self._build_profile_html(sid))

    def log_events_html(self, sid):
        hl = self._hl_event[1] if self._hl_event and self._hl_event[0] == sid else None
        return self._memo(("e", sid, self.log_store.version, hl, self.view.world_size),
                          lambda: self._build_events_html(sid, hl))

    def _build_profile_html(self, sid):
        gray = "color:#999"
        st = self.log_store.get(sid)
        if not st:
            return (f"<p style='{gray};font-size:11px'>За период «{html.escape(self.log_store.period_label())}» "
                    "данных по игроку нет. Данные копятся "
                    "из консоли RCON, пока программа подключена к серверу.</p>")
        esc = html.escape
        ent_total = sum(st["ent_deaths"].values())
        other_total = sum(st["other_deaths"].values())
        deaths_all = st["deaths_pvp"] + st["suicides"] + ent_total + other_total

        def top_text(d):
            return ", ".join(f"{esc(str(k))} ×{v}" for k, v in self._top(d)) or "—"

        hours = st.get("hours") or [0.0] * 24
        if sum(hours) >= 10:
            peak = ", ".join(f"{h:02d}–{(h + 1) % 24:02d}"
                             for h in sorted(sorted(range(24), key=lambda h: hours[h], reverse=True)[:3]))
        else:
            peak = "—"
        avg_sess = st["playtime"] / st["sessions"] if st["sessions"] else 0
        avg_life = st["life_sum"] / st["life_n"] if st["life_n"] else 0
        ips = sorted(st["ips"].items(), key=lambda kv: kv[1], reverse=True)[:5]

        rows = [
            ("Ники в логах", ", ".join(esc(n) for n in reversed(st["names"])) or "—"),
            ("Первая активность", self._fmt_ts(st["first"])),
            ("Последняя активность", self._fmt_ts(st["last"])),
            ("Сессий / спавнов", f"{st['sessions']} / {st['spawns']}"),
            ("Время в игре", self._fmt_long(st["playtime"])),
            ("Сессия средняя / макс.",
             f"{self._fmt_long(avg_sess)} / {self._fmt_long(st['max_session'])}" if st["sessions"] else "—"),
            ("Обычно онлайн (часы)", peak),
            ("PvP убийств", st["kills"]),
            ("PvP смертей", st["deaths_pvp"]),
            ("K/D (PvP)", f"{self._kd(st):.2f}"),
            ("Лучшая серия убийств", st["best_streak"]),
            ("Жизнь средняя / макс.",
             f"{self._fmt_long(avg_life)} / {self._fmt_long(st['life_max'])}" if st["life_n"] else "—"),
            ("Убито NPC / животных", f"{st['npc']}  ({top_text(st['npc_types'])})" if st["npc"] else "0"),
            ("Смертей всего", f"{deaths_all}  (⚔ {st['deaths_pvp']} · 💀 {st['suicides'] + other_total}"
                              f" · 🐺 {ent_total})"),
            ("&nbsp;&nbsp;↳ суицид", f"{st['suicides']} (быстрых &lt;2 мин: {st['quick_suic']})"),
            ("&nbsp;&nbsp;↳ от животных / NPC / техники",
             top_text({PVE_NAMES.get(k, k): v for k, v in st["ent_deaths"].items()})),
            ("&nbsp;&nbsp;↳ среда (голод, холод, падение…)",
             top_text({ENV_NAMES.get(k, k): v for k, v in st["other_deaths"].items()})),
            ("IP (последние)", ", ".join(f"{esc(ip)} ({time.strftime('%d.%m', time.localtime(ts))})"
                                         for ip, ts in ips) or "—"),
            ("Причины выхода", top_text(st["reasons"])),
            ("Киков (попыток)", st.get("kicks", 0)),
            ("Репортов на него / от него", f"{st['reports_in']} / {st['reports_out']}"
             + (f"  ({top_text({REPORT_NAMES.get(k, k): v for k, v in st['report_types'].items()})})"
                if st["reports_in"] else "")),
        ]
        body = "".join(
            f"<tr><td style='{gray};padding:2px 10px 2px 0'>{k}</td><td>{v}</td></tr>"
            for k, v in rows)
        parts = [f"<h4 style='margin:12px 0 4px 0'>Статистика (консоль / логи)</h4><table>{body}</table>"]

        for title, key in (("Чаще всего убивал", "victims"), ("Чаще всего убивали его", "killers")):
            top = self._top(st[key])
            if top:
                items = ", ".join(
                    f"{self._plink(self.log_store.name_of(s), s)} ×{n}" for s, n in top)
                parts.append(f"<p style='margin:6px 0 0 0'><span style='{gray}'>{title}:</span> {items}</p>")

        if st["reports"]:
            lines = []
            for ts, way, oname, osid, typ, subj in reversed(st["reports"][-10:]):
                arrow = "на него от" if way == "in" else "он на"
                lines.append(f"{time.strftime('%d.%m %H:%M', time.localtime(ts))} · {arrow} "
                             f"{self._plink(oname, osid)} · {esc(REPORT_NAMES.get(typ, typ))}"
                             + (f" — {esc(subj)}" if subj else ""))
            parts.append(f"<p style='margin:6px 0 0 0'><span style='{gray}'>Последние репорты:</span><br>"
                         + "<br>".join(lines) + "</p>")

        alts = self.log_store.alts(sid)
        if alts:
            items = ", ".join(f"{self._plink(n, s)} ({esc(ip)})" for s, n, ip in alts)
            parts.append(
                f"<p style='margin:6px 0 0 0'><span style='{gray}'>Тот же IP у других аккаунтов:</span> {items}"
                f"<br><span style='{gray};font-size:11px'>возможны ложные совпадения (общий провайдер, VPN)</span></p>")
        return "".join(parts)

    def _build_events_html(self, sid, hl):
        st = self.log_store.get(sid)
        if not st or not st["events"]:
            return "<i style='color:#999'>Убийств и смертей в логах не найдено</i>"
        world = self.view.world_size
        rows = []
        n_ev = len(st["events"])
        for ri, (ts, kind, oname, osid, x, z, extra) in enumerate(reversed(st["events"])):
            idx = n_ev - 1 - ri
            bg = " background-color:#4a4420;" if idx == hl else ""
            cat, label = event_info(kind, oname, extra)
            glyph, color = CAT_STYLE[cat]
            if kind == "kill":
                color = "#39d353"
            text = html.escape(label)
            if kind in ("kill", "death"):
                text += " " + self._plink(oname, osid)
            when = time.strftime("%d.%m %H:%M:%S", time.localtime(ts))
            pos = grid_ref(x, z, world) if x is not None and z is not None else ""
            rows.append(
                f"<tr><td style='color:{color};padding-right:6px;{bg}'><a name='ev{idx}'></a>{glyph}</td>"
                f"<td style='color:#999;padding:2px 8px 2px 0;{bg}'>{when}</td><td style='{bg}'>{text}</td>"
                f"<td style='color:#999;padding-left:8px;{bg}'>{pos}</td></tr>")
        legend = "⚔ PvP (зелёный — убил) · 💀 суицид / среда · 🐺 NPC, животные, техника"
        head = (f"<p style='color:#999;margin:0 0 4px 0'>{legend}<br>"
                f"Последние {len(rows)} событий (новые сверху, справа — квадрат)</p>")
        return head + "<table>" + "".join(rows) + "</table>"

    def refresh_details(self):
        self._refresh_details_inner()
        self.update_event_marks()

    def _refresh_details_inner(self):
        if self.selected_base is not None:
            self.show_base_details()
            return
        sid = self.selected
        p = self.find_player(sid) if sid else None
        esc = html.escape

        if not p:
            self.hp_bar.setValue(0)
            if sid and self.log_store.has(sid):
                # игрока нет в списке сервера (открыли по ссылке из событий) — показываем только логи
                self.hp_bar.setFormat(self.log_summary(sid) or "Нет в списке сервера")
                head = (f"<h3 style='margin:0 0 4px 0'>{esc(self.log_store.name_of(sid))}</h3>"
                        f"<p style='color:#999;margin:0'>SteamID {esc(sid)} · сейчас не в списке сервера "
                        "(офлайн), данные только из логов.</p>")
                self._set_html(self.details, head + self.log_profile_html(sid))
                self._set_html(self.events_view, self.log_events_html(sid))
            else:
                self.hp_bar.setFormat("—")
                self._set_html(self.details,
                               "<i style='color:#999'>Выберите игрока или базу на карте или в списке</i>")
                self._set_html(self.events_view, "<i style='color:#999'>Выберите игрока</i>")
            return

        hp = p["health"]
        if hp is not None:
            self.hp_bar.setValue(int(max(0, min(100, hp / p["max_health"] * 100))))
            self.hp_bar.setFormat(f"{hp:.0f} / {p['max_health']:.0f} HP")
        else:
            self.hp_bar.setValue(0)
            self.hp_bar.setFormat(self.log_summary(p["steam_id"]) or "HP недоступно")

        world = self.view.world_size
        if p["x"] is not None:
            pos = f"{p['x']:.1f}, {p['y']:.1f}, {p['z']:.1f}"
            grid = grid_ref(p["x"], p["z"], world)
        else:
            pos, grid = "нет данных", "—"

        rows = [
            ("Команда", p.get("team") or "—"),
            ("Позиция (X, Y, Z)", pos),
            ("Квадрат", grid),
            ("Пинг", f"{p['ping']} ms" if p["ping"] is not None else "—"),
            ("В сети", fmt_time(p["connected"])),
            ("IP", p["address"] or "—"),
            ("SteamID", p["steam_id"]),
        ]
        for k, v in p["extra"].items():
            rows.append((EXTRA_LABELS.get(k, k), v))

        body = "".join(
            f"<tr><td style='color:#999;padding:3px 10px 3px 0'>{esc(str(k))}</td>"
            f"<td>{esc(str(v))}</td></tr>" for k, v in rows)
        self._set_html(
            self.details,
            f"<h3 style='margin:0 0 6px 0'>{esc(p['name'])}</h3><table>{body}</table>"
            + self.log_profile_html(p["steam_id"]))
        self._set_html(self.events_view, self.log_events_html(p["steam_id"]))
