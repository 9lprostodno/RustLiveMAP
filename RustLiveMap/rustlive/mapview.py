"""Rust Live Map: модуль mapview."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QLabel, QWidget

from .gamedata import (
    GRID_CELL,
    col_letters,
    team_color,
)
from .heat import (
    HEAT_CELL,
    _HEAT_STOPS,
    heat_color,
    heat_dims,
)


# ============================================================
# КАРТА
# ============================================================

class PlayerMarker(QGraphicsEllipseItem):
    R = 7

    def __init__(self, steam_id):
        r = self.R
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.steam_id = steam_id
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

        self.arrow = QGraphicsLineItem(0, 0, 0, -(r + 9), self)
        self.arrow.setPen(QPen(QColor("white"), 2))

        self.label_bg = QGraphicsRectItem(self)
        self.label_bg.setPen(QPen(Qt.PenStyle.NoPen))
        self.label_bg.setBrush(QBrush(QColor(0, 0, 0, 175)))

        self.label = QGraphicsSimpleTextItem(self.label_bg)
        self.label.setBrush(QBrush(QColor("white")))
        font = QFont("Arial", 8)
        font.setBold(True)
        self.label.setFont(font)

        self.hp_bg = QGraphicsRectItem(self.label_bg)
        self.hp_bg.setPen(QPen(Qt.PenStyle.NoPen))
        self.hp_bg.setBrush(QBrush(QColor("#444")))
        self.hp_fill = QGraphicsRectItem(self.label_bg)
        self.hp_fill.setPen(QPen(Qt.PenStyle.NoPen))

    def update_data(self, p, show_name, show_hp, selected):
        self.setBrush(QBrush(QColor(team_color(p.get("team")))))
        self.setPen(QPen(QColor("#ffd400" if selected else "#ffffff"), 2))
        self.setZValue(30 if selected else 10)

        rot = p.get("rotation")
        self.arrow.setVisible(rot is not None)
        if rot is not None:
            self.arrow.setRotation(rot)

        # Имя и HP включаются независимо друг от друга
        hp = p.get("health")
        has_bar = bool(show_hp) and hp is not None
        lines = []
        if show_name:
            lines.append(p["name"])
        if has_bar:
            lines.append(f"HP {hp:.0f}")

        self.label_bg.setVisible(bool(lines))
        if not lines:
            return
        self.label.setText("\n".join(lines))

        br = self.label.boundingRect()
        width = max(br.width(), 54) + 8
        height = br.height() + 4 + (5 if has_bar else 0)
        top = self.R + 4
        self.label_bg.setRect(-width / 2, top, width, height)
        self.label.setPos(-width / 2 + 4, top + 2)

        self.hp_bg.setVisible(has_bar)
        self.hp_fill.setVisible(has_bar)
        if has_bar:
            frac = max(0.0, min(1.0, hp / (p.get("max_health") or 100.0)))
            bar_y = top + 2 + br.height() + 1
            bar_w = width - 8
            self.hp_bg.setRect(-width / 2 + 4, bar_y, bar_w, 3)
            self.hp_fill.setRect(-width / 2 + 4, bar_y, bar_w * frac, 3)
            color = "#39d353" if frac > 0.6 else ("#e3b341" if frac > 0.3 else "#f85149")
            self.hp_fill.setBrush(QBrush(QColor(color)))


class BaseMarker(QGraphicsPathItem):
    """Иконка дома: стены + крыша + дверь. Серый дом — шкаф без авторизованных."""

    def __init__(self, entity_id):
        super().__init__()
        self.entity_id = entity_id
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(4)

        # стены
        body = QPainterPath()
        body.addRect(-9, -1, 18, 10)
        self.setPath(body)

        # крыша
        self.roof = QGraphicsPolygonItem(
            QPolygonF([QPointF(-13, -1), QPointF(0, -13), QPointF(13, -1)]), self)

        # дверь
        self.door = QGraphicsRectItem(-2.5, 3, 5, 6, self)
        self.door.setPen(QPen(Qt.PenStyle.NoPen))
        self.door.setBrush(QBrush(QColor(45, 28, 12, 230)))

    def update_data(self, b, selected):
        empty = b["authed"] == 0
        underground = b["y"] < -5  # ниже уровня моря: подземная/подводная постройка
        self.setBrush(QBrush(QColor("#8c8c8c" if empty else "#e8a548")))
        self.roof.setBrush(QBrush(QColor("#5c5c5c" if empty else "#b0412a")))
        if selected:
            pen = QPen(QColor("#ffd400"), 2)
        elif underground:
            pen = QPen(QColor("#9b6bff"), 2)
        else:
            pen = QPen(QColor("#222222"), 1)
        self.setPen(pen)
        self.roof.setPen(pen)
        self.setZValue(25 if selected else 4)


class EventBadge(QGraphicsEllipseItem):
    """Значок убийства/смерти на карте; event_idx — номер события в списке игрока."""
    def __init__(self, event_idx):
        super().__init__(-11, -11, 22, 22)
        self.event_idx = event_idx


class MonumentLabel(QGraphicsItem):
    """Подпись монумента: плашка с названием, размер не зависит от масштаба карты."""
    FONT_PT = 8

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(3.5)
        self.font = QFont()
        self.font.setPointSize(self.FONT_PT)
        self.font.setBold(True)
        fm = QFontMetrics(self.font)
        self.w = fm.horizontalAdvance(text) + 12
        self.h = fm.height() + 2

    def boundingRect(self):
        return QRectF(-self.w / 2, -self.h / 2, self.w, self.h)

    def paint(self, p, _opt, _w=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QBrush(QColor(14, 14, 16, 175)))
        p.drawRoundedRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        p.setFont(self.font)
        p.setPen(QColor("#f3e3a1"))
        p.drawText(self.boundingRect(), Qt.AlignmentFlag.AlignCenter, self.text)


class HudChip(QLabel):
    """Плашка поверх карты: полупрозрачная, со скруглёнными углами."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setStyleSheet("background: rgba(18,18,20,205); color: #e6e6e6; border: 1px solid rgba(255,255,255,30);"
                           "border-radius: 9px; padding: 3px 10px;")


class HeatLegend(QWidget):
    """Легенда Heatmap в углу карты: подпись режима и шкала «мало → много». Появляется, когда есть что показать."""
    MAX_TEXT = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedHeight(54)
        self.hide()

    def setText(self, t):
        self._text = str(t).strip()
        fm = QFontMetrics(self.font())
        self.setFixedWidth(max(200, min(self.MAX_TEXT, fm.horizontalAdvance(self._text)) + 26))
        self.setVisible(bool(self._text))
        self.update()

    def text(self):
        return self._text

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 30)))
        p.setBrush(QBrush(QColor(18, 18, 20, 205)))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor("#e6e6e6"))
        fm = QFontMetrics(p.font())
        p.drawText(13, 19, fm.elidedText(self._text, Qt.TextElideMode.ElideRight, self.width() - 26))
        bar = QRectF(13, 28, self.width() - 26, 7)
        g = QLinearGradient(bar.left(), 0, bar.right(), 0)
        for t, (cr, cg, cb) in _HEAT_STOPS:
            g.setColorAt(t, QColor(cr, cg, cb))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(bar, 3.5, 3.5)
        f = QFont(p.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        p.setPen(QColor("#9a9a9a"))
        p.drawText(13, 49, "мало")
        p.drawText(self.width() - 13 - QFontMetrics(f).horizontalAdvance("много"), 49, "много")
        p.end()


class MapView(QGraphicsView):
    resized = Signal()
    player_clicked = Signal(str)
    base_clicked = Signal(object)
    empty_clicked = Signal()
    event_clicked = Signal(int)
    cursor_world = Signal(float, float)

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QBrush(QColor("#111111")))
        self.setMouseTracking(True)

        self.world_size = 3750.0
        self.pix_item = None
        self.markers = {}
        self.base_markers = {}
        self.grid_items = []
        self.grid_visible = False
        self.event_items = []
        self.monument_items = []
        self._monuments = []
        self._marks_key = None
        self._press_pos = None
        self.heat_item = None
        self.note = None             # подпись поверх карты (например, «карта этого вайпа не сохранена»)

    def scrollContentsBy(self, dx, dy):
        """Прокрутка сдвигает и дочерние виджеты viewport (плашки, кнопку «⛶», легенду): возвращаем их на места."""
        super().scrollContentsBy(dx, dy)
        self.resized.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()            # плашки поверх карты переставляются в углы

    # ---- карта ----
    def load_map(self, path):
        pix = QPixmap(str(path))
        if pix.isNull():
            return False
        self._set_pixmap(pix)
        return True

    def load_blank(self, note=None):
        """Пустое тёмное поле вместо карты: события и heatmap всё равно можно показать."""
        pix = QPixmap(1024, 1024)
        pix.fill(QColor("#1c1c1c"))
        self._set_pixmap(pix)
        self.note = note

    def _set_pixmap(self, pix):
        self._scene.clear()
        self.markers.clear()
        self.base_markers.clear()
        self.grid_items = []
        self.event_items = []
        self.monument_items = []
        self._marks_key = None
        self.heat_item = None
        self.note = None
        self.pix_item = QGraphicsPixmapItem(pix)
        self.pix_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.pix_item.setZValue(0)
        self._scene.addItem(self.pix_item)
        r = QRectF(pix.rect())
        self._scene.setSceneRect(r.adjusted(-r.width() / 2, -r.height() / 2, r.width() / 2, r.height() / 2))
        self.rebuild_grid()
        self._draw_monuments()
        self.fit()

    def clear_map(self):
        self._scene.clear()
        self.markers.clear()
        self.base_markers.clear()
        self.grid_items = []
        self.event_items = []
        self.monument_items = []
        self._marks_key = None
        self.pix_item = None
        self.heat_item = None
        self.note = None

    def set_heat(self, levels, world):
        """levels: {(cx, cz): 0..1} или None/пусто — убрать слой. Клетки HEAT_CELL м, юго-запад — начало."""
        if self.heat_item is not None:
            self._scene.removeItem(self.heat_item)
            self.heat_item = None
        if not levels or not self.pix_item:
            return
        gx, gz = heat_dims(world)
        img = QImage(gx, gz, QImage.Format.Format_ARGB32)
        img.fill(0)
        for (cx, cz), t in levels.items():
            if 0 <= cx < gx and 0 <= cz < gz:
                r, g, b, a = heat_color(t)
                img.setPixel(cx, gz - 1 - cz, QColor(r, g, b, a).rgba())
        pr = self.pix_item.pixmap().rect()
        w = float(world)
        item = QGraphicsPixmapItem(QPixmap.fromImage(img))
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setTransform(QTransform().scale(HEAT_CELL / w * pr.width(), HEAT_CELL / w * pr.height()))
        item.setPos(0, -(gz * HEAT_CELL - w) / w * pr.height())
        item.setZValue(1)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(item)
        self.heat_item = item

    def set_monuments(self, items):
        """items: [{"n": имя, "x": X, "z": Z}] в мировых координатах; пусто — убрать подписи."""
        self._monuments = list(items or [])
        self._draw_monuments()

    def _draw_monuments(self):
        for it in self.monument_items:
            if it.scene() is not None:
                self._scene.removeItem(it)
        self.monument_items = []
        if not self.pix_item or not self._monuments:
            return
        for m in self._monuments:
            lab = MonumentLabel(m["n"])
            lab.setPos(self.world_to_scene(m["x"], m["z"]))
            self._scene.addItem(lab)
            self.monument_items.append(lab)

    def fit(self):
        if self.pix_item:
            self.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def set_world_size(self, size):
        self.world_size = float(size)
        self.rebuild_grid()

    # ---- сетка квадратов ----
    def rebuild_grid(self):
        for it in self.grid_items:
            self._scene.removeItem(it)
        self.grid_items = []
        if not self.pix_item or not self.grid_visible:
            return

        pr = self.pix_item.pixmap().rect()
        pw, ph = float(pr.width()), float(pr.height())
        sx = GRID_CELL / self.world_size * pw
        sy = GRID_CELL / self.world_size * ph

        pen = QPen(QColor(255, 255, 255, 80), 1)
        pen.setCosmetic(True)
        font = QFont("Arial", 8)
        font.setBold(True)

        def add_line(x1, y1, x2, y2):
            line = QGraphicsLineItem(x1, y1, x2, y2)
            line.setPen(pen)
            line.setZValue(2)
            self._scene.addItem(line)
            self.grid_items.append(line)

        def add_label(text, x, y):
            t = QGraphicsSimpleTextItem(text)
            t.setBrush(QBrush(QColor("#fff3b0")))
            t.setFont(font)
            t.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            t.setZValue(3)
            t.setPos(x, y)
            self._scene.addItem(t)
            self.grid_items.append(t)

        i = 0
        while i * sx < pw:
            add_line(i * sx, 0, i * sx, ph)
            add_label(f" {col_letters(i)}", i * sx, 0)          # буквы — столбцы
            i += 1
        j = 0
        while j * sy < ph:
            add_line(0, j * sy, pw, j * sy)
            # у первой строки номер уходит на строку ниже, чтобы не слипаться с буквой «A»
            add_label(f"\n{j}" if j == 0 else f" {j}", 0, j * sy)
            j += 1

    # ---- координаты ----
    def world_to_scene(self, x, z):
        if not self.pix_item:
            return QPointF(0, 0)
        pr = self.pix_item.pixmap().rect()
        w = self.world_size
        return QPointF((x + w / 2) / w * pr.width(), (w / 2 - z) / w * pr.height())

    def scene_to_world(self, pt):
        pr = self.pix_item.pixmap().rect()
        w = self.world_size
        return pt.x() / pr.width() * w - w / 2, w / 2 - pt.y() / pr.height() * w

    # ---- игроки ----
    def set_players(self, players, show_names, show_hp, selected):
        if not self.pix_item:
            return
        seen = set()
        for p in players:
            if p["x"] is None or p["z"] is None:
                continue
            sid = p["steam_id"]
            seen.add(sid)
            marker = self.markers.get(sid)
            if marker is None:
                marker = PlayerMarker(sid)
                self._scene.addItem(marker)
                self.markers[sid] = marker
            marker.setPos(self.world_to_scene(p["x"], p["z"]))
            marker.update_data(p, show_names, show_hp, sid == selected)
        for sid in list(self.markers):
            if sid not in seen:
                self._scene.removeItem(self.markers.pop(sid))

    def set_bases(self, bases, visible, selected):
        if not self.pix_item:
            return
        seen = set()
        if visible:
            for b in bases:
                eid = b["entity_id"]
                seen.add(eid)
                marker = self.base_markers.get(eid)
                if marker is None:
                    marker = BaseMarker(eid)
                    self._scene.addItem(marker)
                    self.base_markers[eid] = marker
                marker.setPos(self.world_to_scene(b["x"], b["z"]))
                marker.update_data(b, eid == selected)   # у шкафов только значок; название — в панели
        for eid in list(self.base_markers):
            if eid not in seen:
                self._scene.removeItem(self.base_markers.pop(eid))

    def set_event_marks(self, marks):
        # marks: [(x, z, глиф, цвет, подсказка, номер события)] — места убийств/смертей выбранного игрока
        key = tuple(marks)
        if key == self._marks_key:
            return
        self._marks_key = key
        for it in self.event_items:
            self._scene.removeItem(it)
        self.event_items = []
        if not self.pix_item:
            return
        font = QFont("Arial", 10)
        for x, z, glyph, color, tip, idx in marks:
            badge = EventBadge(idx)
            badge.setBrush(QBrush(QColor(20, 20, 24, 230)))
            badge.setPen(QPen(QColor(color), 2))
            badge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            badge.setZValue(20)
            badge.setToolTip(tip)
            badge.setPos(self.world_to_scene(x, z))
            txt = QGraphicsSimpleTextItem(glyph, badge)
            txt.setFont(font)
            txt.setBrush(QBrush(QColor(color)))
            br = txt.boundingRect()
            txt.setPos(-br.width() / 2, -br.height() / 2)
            self._scene.addItem(badge)
            self.event_items.append(badge)

    def center_on_world(self, x, z):
        self.centerOn(self.world_to_scene(x, z))

    EMPTY_TEXT = ("Карта не загружена\n\n"
                  "Нажмите «🗺 Карта» → «Получить с сервера»\n"
                  "или загрузите файл .map / png")

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        text = self.EMPTY_TEXT if self.pix_item is None else self.note
        if text:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor("#8a8a8a"))
            f = painter.font()
            f.setPointSize(13)
            painter.setFont(f)
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()

    # ---- события мыши ----
    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy == 0:  # горизонтальная прокрутка тачпада — игнорируем
            return
        factor = 1.2 if dy > 0 else 1 / 1.2
        cur = self.transform().m11()
        new = max(0.02, min(10.0, cur * factor))
        self.scale(new / cur, new / cur)

    def mousePressEvent(self, e):
        self._press_pos = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            pos = e.position().toPoint()
            if (pos - self._press_pos).manhattanLength() < 5:
                item = self.itemAt(pos)
                while item is not None and not isinstance(item, (PlayerMarker, BaseMarker, EventBadge)):
                    item = item.parentItem()
                if isinstance(item, EventBadge):
                    self.event_clicked.emit(item.event_idx)
                elif isinstance(item, PlayerMarker):
                    self.player_clicked.emit(item.steam_id)
                elif isinstance(item, BaseMarker):
                    self.base_clicked.emit(item.entity_id)
                else:
                    self.empty_clicked.emit()      # клик по пустому месту карты — снять выбор

    def mouseMoveEvent(self, e):
        if self.pix_item:
            x, z = self.scene_to_world(self.mapToScene(e.position().toPoint()))
            self.cursor_world.emit(x, z)
        super().mouseMoveEvent(e)
