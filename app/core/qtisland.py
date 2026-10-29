"""The island's widgets in Qt (2026-10-24, the owner's recording "TwoCode for Windows, part 5: Make the island yours"; docs/adr/0006): the
open notch's Home as a grid of tiles the owner arranges (core/island.py's rules), its edit mode (a tile held: they all wiggle; − removes,
the corner badge resizes, a drag moves, an empty cell's + adds), the widget menu (an app and a size, or a ready-made layout; under the
notch's Settings since 2026-10-25, the owner: "the + should be reverted", + being the file drop again), and the Note and Clipboard views.
Focus is a Pomodoro timer (25 minutes, a 5-minute break, a 15-minute one after every fourth).

Every tile shows what its source says, and nothing else: Monday's state and the owner's agents (the notch's State and AGENT_WATCH), the
last finished task (GET /api/tasks), Windows' media sessions (core/media.py, MediaWatch), this PC's clipboard and clock, the notch's file
in hand and its settings, the focus timer and the note kept in data/desktop.json ("island").

Checked by `python -m core.qtnotch --check` (_check_island here).
"""
from __future__ import annotations

import asyncio
import math
import re
import sys
import threading
import time
from datetime import datetime

from . import island as I
from . import qtkit as K
from . import qtlooks as L
from .notch import OPEN_W, PAD, STATES
from .qtkit import TEXT, TEXT_2, TEXT_3, Button, Elided, IconButton, font, label, mix, reduced_motion
from PySide6.QtCore import QLocale, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QBoxLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

TILE_BG, TILE_R = "#121617", 14           # a tile (the recording's, sampled) and its corners (the notch's cards')
GAP, CELL_H = 6, 58                       # between tiles, and a cell's height (the recording's 7 and 64 at its scale); its width: the grid's share
LIME, LIME_HOVER, LIME_INK = "#C4E59A", "#D3EEB0", "#10150A"   # Done, Add, the size chosen (the recording's) and the words on them
ROW, ROW_HOVER, ROW_ON, ROW_ON_EDGE = "#161819", "#1E2124", "#1A2012", "#5D7542"  # the menu's buttons and rows; the app chosen
OFF_PILL = "#1A1E1F"                      # a size not chosen
BADGE_BG, DASH = "#5A5C61", "#3E4045"     # an edit badge's disc; an empty cell's dashes
RING_TRACK, RING_FILL = "#2B2522", "#F2A24C"   # the focus ring (the recording's dark ring and its orange arc)
BREAK_FILL = "#3FC1A4"                          # ...its arc during a Pomodoro break
BAR_TRACK, BAR_FILL = "#262A2B", "#E2E6E7"     # the music's progress (the recording's)
HOLD_MS = 650                             # a tile held this long: edit mode (the recording's "Hold a widget"; 450 until 2026-10-29: a slow
#                                           press of the touchpad's reached it, and a click turned into dragging tiles)
WIGGLE_DEG, WIGGLE_HZ = 1.3, 3.0          # edit mode's wiggle: each tile turns this far either way, this often, out of step with the rest
BADGE_R = 8.5                             # the − and resize badges
MOVE_MS = 220                             # a tile gliding to its new place (a drop, a resize, a ready-made layout)
LIFT_SCALE, LIFT_EDGE, LIFT_LIGHT = 1.06, 0.22, 0.05  # a tile being dragged: this much bigger, a light edge and a lighter face (lifted)
FACE_EVERY_MS = 1000                      # in edit mode, the tiles' faces drawn again this often (the clock goes on under them)
FRAME_MS = 33                             # edit mode's frames while something moves
MEDIA_EVERY_MS = 1500                     # Windows' media sessions read this often while a music tile shows
NOTE_SAVE_MS = 400                        # the note kept this long after its last key
PILLS_ON_AI = 2                           # the AI tile's pills (Big): the dock's first ones; past them, "N more"
NOTE_H, CLIP_ROW_H = 168, 30              # the Note view's card; a copy's row in the Clipboard view
NOTE_MINI, NOTE_PX = 20, 11.5             # the Note tile with words on it: its little tile, and the words' size (as many lines as it holds)
MEDIA_GLYPH = "music"


def phase(key):
    """Each tile's wiggle starts at its own point (out of step with its neighbours, as on a phone)."""
    return (I.MENU.index(key) if key in I.MENU else 0) * 1.7


_PAL = None


def app_tile(p, r, key, dim=1.0):
    """A widget's tile (the menu's rows, the Tray, Clipboard, Note and Settings cards): its colour, lighter at the top, with Monday's pal on
    it (the recording's mascot tiles)."""
    global _PAL
    if _PAL is None:
        _PAL = K.pal("#F6F7F9")
    col = QColor(I.WIDGETS[key][1])
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, mix(col, "#ffffff", 0.16))
    g.setColorAt(1, col)
    p.save()
    p.setOpacity(p.opacity() * dim)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, r.width() * 0.26, r.width() * 0.26)
    _PAL.draw(p, r.adjusted(r.width() * 0.1, r.height() * 0.13, -r.width() * 0.1, -r.height() * 0.02), 1.0, "idle")
    p.restore()


def paint_thumb(p, r, layout):
    """A layout's picture on its button: its widgets as blocks in their colours (muted), on the 4 x 2 grid."""
    g = max(1.5, r.width() / 22)
    cw, ch = (r.width() - (I.COLS - 1) * g) / I.COLS, (r.height() - (I.ROWS - 1) * g) / I.ROWS
    p.setPen(Qt.PenStyle.NoPen)
    for x, y, w, h, colour in I.thumb(layout):
        p.setBrush(mix(colour, "#1b1d1f", 0.32))
        p.drawRoundedRect(QRectF(r.left() + x * (cw + g), r.top() + y * (ch + g), w * cw + (w - 1) * g, h * ch + (h - 1) * g), 2.2, 2.2)


def h24():
    """This PC's clock: 24-hour or AM / PM (its short time format)."""
    return "a" not in QLocale.system().timeFormat(QLocale.FormatType.ShortFormat).lower()


# -- small pieces -----------------------------------------------------------------------------------------------------------------------
class AppIcon(QWidget):
    def __init__(self, key, size, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        app_tile(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.key)
        p.end()


class Lime(K._Keyed, QAbstractButton):
    """The island's pill: dark words on the recording's lime (Done, Add, the size chosen); `lit` False, the menu's dark pill (the sizes not
    chosen). Disabled: dimmed, and its words say why elsewhere."""

    def __init__(self, text, name=None, height=24, width=None, lit=True, parent=None):
        super().__init__(parent)
        self.lit = lit
        self.setText(text)
        self.setAccessibleName(name or text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(height)
        if width:
            self.setFixedWidth(width)
        self._hover = K.Hover(self)

    def set_lit(self, lit):
        if lit != self.lit:
            self.lit = lit
            self.update()

    def sizeHint(self):
        return QSize(int(QFontMetricsF(font(11.5, 650)).horizontalAdvance(self.text()) + 30), self.height())

    def enterEvent(self, e):
        self._hover.go(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.go(False)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not self.isEnabled():
            p.setOpacity(0.42)
        bg = mix(LIME, LIME_HOVER, self._hover.value) if self.lit else mix(OFF_PILL, ROW_HOVER, self._hover.value)
        if self.isDown():
            bg = bg.darker(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor(LIME_INK if self.lit else TEXT))
        p.setFont(font(11.5, 650))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        self._ring(p, r, r.height() / 2)
        p.end()


class Row(K._Keyed, QAbstractButton):
    """One of the widget menu's dark rows: an app (its tile, its name, a lime dot while it is on Home) or a ready-made layout (its picture,
    its name). `on`: the one chosen (the recording's olive ground and lime edge)."""

    def __init__(self, text, name, lead, lead_w, height=30, dot=False, parent=None):
        super().__init__(parent)
        self.lead, self.lead_w, self.dot, self.on = lead, lead_w, dot, False
        self.setText(text)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = K.Hover(self)

    def set_on(self, on, dot=None):
        self.on = on
        if dot is not None:
            self.dot = dot
        self.update()

    def sizeHint(self):
        return QSize(int(self.lead_w + 26 + QFontMetricsF(font(12, 650)).horizontalAdvance(self.text()) + (16 if self.dot else 0)), self.height())

    def enterEvent(self, e):
        self._hover.go(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.go(False)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(ROW_ON_EDGE), 1.2) if self.on else Qt.PenStyle.NoPen)
        p.setBrush(QColor(ROW_ON) if self.on else mix(ROW, ROW_HOVER, self._hover.value))
        p.drawRoundedRect(r, 10, 10)
        lead = QRectF(r.left() + 7, r.center().y() - (r.height() - 10) / 2, self.lead_w, r.height() - 10)
        self.lead(p, lead)
        p.setPen(QColor(TEXT))
        p.setFont(font(12, 650))
        room = r.width() - self.lead_w - 22 - (16 if self.dot else 0)
        p.drawText(QRectF(lead.right() + 8, r.top(), room, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(room)))
        if self.dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(LIME))
            p.drawEllipse(QPointF(r.right() - 11, r.center().y()), 4, 4)
        self._ring(p, r, 10)
        p.end()


class Ring(QWidget):
    """The focus tile's ring: the session gone so far as an orange arc on a dark ring, a timer's outline in the middle (the recording's)."""

    def __init__(self, size):
        super().__init__()
        self.setFixedSize(size, size)
        self.part, self.colour = 0.0, RING_FILL
        self.island, self.running = False, False  # the island's: a white play disc while ready, a ring and a stop square while running
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set(self, part, colour=RING_FILL):
        part = max(0.0, min(1.0, part))
        if abs(part - self.part) > 1e-4 or colour != self.colour:
            self.part, self.colour = part, colour
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        stroke = max(2.5, w / 13)
        r = QRectF(stroke / 2 + 1, stroke / 2 + 1, w - stroke - 2, w - stroke - 2)
        if self.island:  # (the "Home got a glow-up" recording's Focus button)
            if not self.running:  # ready or paused: a white disc, a play triangle in the timer's colour
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#FFFFFF"))
                p.drawEllipse(QRectF(1, 1, w - 2, w - 2))
                t = w * 0.30
                tri = QPainterPath()
                tri.moveTo(w / 2 - t * 0.38, w / 2 - t / 2)
                tri.lineTo(w / 2 + t * 0.56, w / 2)
                tri.lineTo(w / 2 - t * 0.38, w / 2 + t / 2)
                tri.closeSubpath()
                p.fillPath(tri, QColor(L.FOCUS_GLOW if self.colour == RING_FILL else self.colour))  # (the island's purple; a break's teal)
                return p.end()
            p.setPen(QPen(K.alpha("#FFFFFF", 0.22), stroke))  # running: a white ring filling as it goes, a stop square in it
            p.drawEllipse(r)
            p.setPen(QPen(QColor("#FFFFFF"), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, int(-max(0.02, self.part) * 360 * 16))
            q = w * 0.26
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF((w - q) / 2, (w - q) / 2, q, q), 2, 2)
            return p.end()
        p.setPen(QPen(QColor(RING_TRACK), stroke))
        p.drawEllipse(r)
        if self.part > 0:
            p.setPen(QPen(QColor(self.colour), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, int(-self.part * 360 * 16))
        s = w * 0.42
        K.paint_icon(p, "timer", QRectF((w - s) / 2, (w - s) / 2, s, s), self.colour, 2.0)
        p.end()


class DayBar(QWidget):
    """The clock's short bar: how much of the day has gone (the recording's line under the date)."""

    def __init__(self, width=56, colour="#7482E6"):
        super().__init__()
        self.part, self.colour = 0.0, colour
        self.setFixedSize(width, 4)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set(self, part):
        if abs(part - self.part) > 0.001:
            self.part = part
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#242B35"))
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)
        p.setBrush(QColor(self.colour))
        p.drawRoundedRect(QRectF(0, 0, max(4.0, self.width() * self.part), self.height()), 2, 2)
        p.end()


class Progress(QWidget):
    """The music's progress (the recording's white on dark); hidden when Windows doesn't say where in the track it is."""

    def __init__(self, fill=None):
        super().__init__()
        self.part, self.fill = 0.0, fill or BAR_FILL  # fill: a plan's bar takes its own colour (the 2026-10-26 recording's amber and green)
        self.setFixedHeight(4)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set(self, part):
        part = max(0.0, min(1.0, part))
        if abs(part - self.part) > 0.002:
            self.part = part
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BAR_TRACK))
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)
        p.setBrush(QColor(self.fill))
        p.drawRoundedRect(QRectF(0, 0, self.width() * self.part, self.height()), 2, 2)
        p.end()


class Cover(QWidget):
    """The track's own art (Windows' media session gives it), rounded; with none, Music's tile with a note: never a stand-in picture."""

    def __init__(self, size, radius):
        super().__init__()
        self.pix, self.radius, self.over = None, radius, None  # over: play / pause shown on it while the mouse is on a small tile
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set(self, pix):
        if pix is not self.pix:
            self.pix = pix
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(clip)
        if self.pix is not None and not self.pix.isNull():
            src = QRectF(self.pix.rect())
            side = min(src.width(), src.height())
            p.drawPixmap(r, self.pix, QRectF(src.center().x() - side / 2, src.center().y() - side / 2, side, side))
        else:
            col = QColor(I.WIDGETS["music"][1])
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0, mix(col, "#ffffff", 0.14))
            g.setColorAt(1, mix(col, "#0b0d0e", 0.25))
            p.fillRect(r, g)
            s = r.width() * 0.42
            K.paint_icon(p, MEDIA_GLYPH, QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s), "#ffffff", 2.0)
        if self.over:
            p.fillRect(r, QColor(0, 0, 0, 120))
            s = r.width() * 0.4
            K.paint_icon(p, self.over, QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s), "#ffffff", 2.4)
        p.end()


class PlayDisc(K._Keyed, QAbstractButton):
    """The big music tile's play / pause: a white disc with a dark glyph (the recording's)."""

    def __init__(self, size=30):
        super().__init__()
        self.glyph = "play"
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Play")

    def set(self, playing):
        self.glyph = "pause" if playing else "play"
        self.setAccessibleName("Pause" if playing else "Play")
        self.setToolTip("Pause" if playing else "Play")
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F2F3F4" if not self.isDown() else "#D9DBDD"))
        p.drawEllipse(r)
        s = r.width() * 0.42
        K.paint_icon(p, self.glyph, QRectF(r.center().x() - s / 2 + (1 if self.glyph == "play" else 0), r.center().y() - s / 2, s, s),
                     "#111214", 2.6)
        self._ring(p, r, r.height() / 2)
        p.end()


# -- the tiles ----------------------------------------------------------------------------------------------------------------------------
class Tile(QWidget):
    """One widget on the grid at its size: the recording's near-black rounded card (its words and controls are the subclass's: build, fill).
    A tap does its own thing (tap); held, edit mode (the Grid decides: presses, holds and drags go to it)."""
    tappable = True

    def __init__(self, grid, key, size):
        super().__init__(grid)
        self.grid, self.n, self.key, self.size = grid, grid.n, key, size
        self.setAccessibleName(f"{I.WIDGETS[key][0]} widget")
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.lay = QHBoxLayout(self)
        m = (14, 12, 14, 12) if size == "big" else (12, 8, 12, 8)
        self.lay.setContentsMargins(*m)
        self.lay.setSpacing(10)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.tappable else Qt.CursorShape.ArrowCursor)
        self.build()
        if size == "small" and getattr(self.n, "mode", "classic") == "island" and getattr(grid, "cell_h", CELL_H) > CELL_H:
            self._stand_up()
        self.fill()

    def _stand_up(self):
        """The floating island's tile (the "Home got a glow-up" recording, docs/adr/0011): the widget's name at the top left, its icon at
        the top right, its words at the bottom, the first of them bigger. The same widgets and data as classic's: only where they sit."""
        lay = self.lay
        first = lay.itemAt(0)
        icon = first.widget() if first is not None else None
        if icon is not None:
            lay.removeWidget(icon)
        lay.setDirection(QBoxLayout.Direction.TopToBottom)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.name_lb = label(I.WIDGETS[self.key][0], 13, 600, "#C9CCD1")
        head.addWidget(self.name_lb, 0, Qt.AlignmentFlag.AlignTop)
        head.addStretch(1)
        if isinstance(icon, Ring):  # (the island's Focus: its white play disc)
            icon.island = True
            icon.setFixedSize(46, 46)
        elif isinstance(icon, Cover):  # (the island's Music: the track's own cover, larger)
            icon.setFixedSize(54, 54)
            icon.radius = 12
        if icon is not None:
            head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.insertLayout(0, head)
        for i in range(1, lay.count()):
            lay.setStretch(i, 0)
        lay.insertStretch(1, 1)  # (the words at the bottom)
        value = next((w for w in (getattr(self, k, None) for k in ("left", "hm", "title")) if isinstance(w, QLabel)), None)
        if value is not None:  # (the recording's big value: "0:01", "25:00", the song)
            f = value.font()
            f.setPointSizeF(f.pointSizeF() * (1.85 if self.key in ("focus", "clock") else 1.3))
            value.setFont(f)

    def name_twice(self, title):
        """On the island, the name over a tile goes when the tile's own words say it already ("Music" with nothing playing)."""
        if getattr(self, "name_lb", None) is not None:
            self.name_lb.setVisible(str(title or "").replace("\n", " ") != I.WIDGETS[self.key][0])

    def build(self):
        pass

    def fill(self):
        pass

    def tick(self):
        pass

    def tap(self):
        pass

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = 18 if getattr(self.n, "mode", "classic") == "island" else TILE_R  # (the island's tiles: rounder, the recording's)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TILE_BG))
        p.drawRoundedRect(r, rad, rad)
        g = L.glow(self) if hasattr(self.n, "mode") else None  # the floating island's light, rising from the bottom (docs/adr/0011)
        if g:
            L.paint_glow(p, r, rad, g)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(K.FOCUS), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), TILE_R - 1, TILE_R - 1)
        p.end()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.tappable:
            return self.tap()
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return e.ignore()  # (the notch's own menu on a right click)
        self.grid.pressed(self, e)

    def mouseMoveEvent(self, e):
        self.grid.dragged(e)

    def mouseReleaseEvent(self, e):
        self.grid.released(e)

    def _words(self, title_px, line_px=11):
        """A title over a line, in a column that keeps them in the middle of the tile."""
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        self.title, self.line = Elided("", title_px, 700), Elided("", line_px, 400, TEXT_2)
        col.addStretch(1)
        col.addWidget(self.title)
        col.addWidget(self.line)
        col.addStretch(1)
        return col


class IconTile(Tile):
    """Tray, Clipboard, Note, Settings: the app's tile, its name, one line of what it holds (the recording's "Tray / Empty")."""

    def build(self):
        self.icon = AppIcon(self.key, 40 if self.size == "small" else 44)
        self.lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter if self.size != "big" else Qt.AlignmentFlag.AlignTop)
        col = self._words(13 if self.size == "small" else 13.5)
        if self.size == "big":
            self.more = label("", 11.5, 400, TEXT_2, wrap=True)
            self.more.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            col.insertWidget(col.count() - 1, self.more, 1)
        self.lay.addLayout(col, 1)

    def show_words(self, title, line, more=""):
        self.title.set(title)
        self.line.set(line)
        self.name_twice(title)
        if self.size == "big":
            self.more.setText(more)
            self.more.setVisible(bool(more))


class TrayTile(IconTile):
    """The file in hand (the notch's drop: State.file): Empty, copying, ready, or why not; a tap opens Files (the drop card)."""

    def fill(self):
        f = self.n.state.file
        if not f:
            return self.show_words("Tray", I.WIDGETS["tray"][2], "Drop a file on the notch, or click to choose one.")
        if f.get("phase") == "copying":
            return self.show_words("Tray", f"Copying {f.get('name') or 'it'}… {f.get('pct', 0)}%")
        if f.get("phase") == "failed":
            return self.show_words("Tray", f"Couldn't take {f.get('name') or 'it'}", f.get("why") or "")
        from .notch import KIND_WORDS, size_words
        return self.show_words("Tray", f.get("name") or "A file",
                               f"{KIND_WORDS.get(f.get('kind'), 'file').capitalize()}, {size_words(f.get('size') or 0)}: click for what it can become.")

    def tap(self):
        self.n.show_view("drop")


class ClipTile(IconTile):
    """The last copies made while Monday runs (memory only): the newest one's words; a tap opens them all."""

    def fill(self):
        c = self.n.clips
        if not c:
            return self.show_words("Clipboard", "Nothing copied yet", "Everything you copy shows here until the PC shuts down.")
        more = "\n".join(I.clip_line(x["text"], 60) for x in c[1:4])
        return self.show_words("Clipboard", I.clip_line(c[0]["text"], 60), more)

    def tap(self):
        self.n.show_view("clip")


def fit_lines(text, f, width, most):
    """The text wrapped at width, its paragraphs kept, at most `most` lines (the last ending in … when more follow)."""
    fm, out = QFontMetricsF(f), []
    for para in (text or "").splitlines():
        words = para.split()
        line = ""
        for w in words:
            t = f"{line} {w}".strip()
            if not line or fm.horizontalAdvance(t) <= width:
                line = t
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
    if len(out) > most:
        out = out[:most]
        out[-1] = out[-1] + " …"
    return "\n".join(fm.elidedText(ln, Qt.TextElideMode.ElideRight, int(width)) if fm.horizontalAdvance(ln) > width else ln for ln in out)


class Lines(QLabel):
    """Elided's two-line twin: words over at most `most` lines, the last ending in … (the whole in its tip and for a screen reader)."""

    def __init__(self, text="", px=13, weight=400, color=TEXT, most=2):
        super().__init__()
        self.most, self._full, self.on_fit = most, "", None  # on_fit: told after each fit (its lines may have changed)
        self.setFont(font(px, weight))
        self.setStyleSheet(f"color: {color}; background: transparent;")
        self.setMinimumWidth(20)
        self.set(text)

    def set(self, text):
        self._full = " ".join(str(text or "").split())
        self.setToolTip(self._full if len(self._full) > 40 else "")
        self.setAccessibleName(self._full)
        self._fit()

    def resizeEvent(self, e):
        self._fit()
        super().resizeEvent(e)

    def _fit(self):
        super().setText(fit_lines(self._full, self.font(), max(40, self.width() - 2), self.most))
        if self.on_fit is not None:
            self.on_fit()


class NoteTile(Tile):
    """The note's own words as they are being written (the owner, 2026-10-25: "if I'm writing something then it should be seen there"):
    the text itself, wrapped, as many lines as the tile holds, … where it goes on; empty, the recording's "Note / Write it down". A tap
    opens it to write in."""

    def build(self):
        self.icon = AppIcon("note", 40 if self.size == "small" else 44)
        self.mini = AppIcon("note", NOTE_MINI)
        self.lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lay.addWidget(self.mini, 0, Qt.AlignmentFlag.AlignTop)
        self.empty = QWidget()  # (the empty note's words, in a box of their own: hidden, it takes no room from the note's)
        self.empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty.setLayout(self._words(13 if self.size == "small" else 13.5))
        self.empty.layout().setContentsMargins(0, 0, 0, 0)
        self.lay.addWidget(self.empty, 1)
        self.text = label("", NOTE_PX, 500, TEXT)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.lay.addWidget(self.text, 1)

    def fill(self):
        words = self.n.note_now().strip()
        empty = not words
        for w in (self.icon, self.empty):
            w.setVisible(empty)
        self.mini.setVisible(not empty)
        self.text.setVisible(not empty)
        if empty:
            self.title.set("Note")
            self.line.set(I.WIDGETS["note"][2])
            self.setAccessibleName("Note widget: empty")
            return
        m = self.lay.contentsMargins()
        width = (self.width() if self.width() > 60 else self.grid.cell_w() * I.SIZES[self.size][0]) - m.left() - m.right() - NOTE_MINI \
            - self.lay.spacing()
        height = (self.height() if self.height() > 30 else getattr(self.grid, "cell_h", CELL_H) * I.SIZES[self.size][1]) - m.top() - m.bottom()
        most = max(1, int(height // QFontMetricsF(self.text.font()).lineSpacing()))
        self.text.setText(fit_lines(words, self.text.font(), max(30.0, width - 2), most))
        self.setAccessibleName(f"Note: {words}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fill()

    def tap(self):
        self.n.show_view("note")


class SettingsTile(IconTile):
    """The notch's own settings, as they are: its sound and when it closes (the recording's "Colours, sound"); a tap opens them."""

    def fill(self):
        p = self.n.p
        sound = f"Sound {'on' if p['sounds'] else 'off'}"
        return self.show_words("Settings", f"{sound} · {p['autoclose']} s",
                               f"{sound}, volume {round(p['volume'] * 100)} %\nCloses {p['autoclose']} s after the mouse leaves")

    def tap(self):
        self.n.show_view("settings")


class ClockTile(Tile):
    """This PC's time and date, and how much of the day has gone (the recording's 09:41 / Sat 24 Oct). Only tells: a tap does nothing."""
    tappable = False

    def build(self):
        big = {"small": 20, "wide": 22, "big": 34}[self.size]
        col = QVBoxLayout()
        col.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.hm = label("", big, 750)
        self.ap = label("", big * 0.5, 650, TEXT_2)
        row.addWidget(self.hm, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.ap, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        self.day = label("", 11 if self.size != "big" else 12.5, 400, TEXT_2)
        self.bar = DayBar(56 if self.size == "small" else 90, I.WIDGETS["clock"][1])
        col.addStretch(1)
        col.addLayout(row)
        col.addWidget(self.day)
        col.addSpacing(2)
        col.addWidget(self.bar)
        col.addStretch(1)
        self.lay.addLayout(col, 1)

    def fill(self):
        hm, ap, day, part = I.clock_words(datetime.now(), h24())
        if self.hm.text() != hm:
            self.hm.setText(hm)
        self.ap.setText(ap)
        self.ap.setVisible(bool(ap))
        self.day.setText(day)
        self.bar.set(part)
        self.setAccessibleName(f"Clock: {hm} {ap} {day}".replace("  ", " "))

    def tick(self):
        self.fill()


class FocusTile(Tile):
    """The Pomodoro timer (25 minutes of focus, a 5-minute break, a 15-minute one after every fourth; kept in data/desktop.json so a
    restart doesn't lose it): its ring (orange while focusing, teal on a break) and what's left; a tap starts, pauses and goes on, and on
    a break skips it. Wide and Big: that button and Reset of their own."""

    def build(self):
        self.ring = Ring({"small": 40, "wide": 44, "big": 66}[self.size])
        self.lay.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.left = label("", {"small": 16, "wide": 17, "big": 26}[self.size], 750)
        self.left.setFont(font({"small": 16, "wide": 17, "big": 26}[self.size], 750))
        self.line = Elided("", 11, 400, TEXT_2)
        col.addStretch(1)
        col.addWidget(self.left)
        col.addWidget(self.line)
        col.addStretch(1)
        self.lay.addLayout(col, 1)
        self.go = self.reset = None
        if self.size != "small":
            self.go = IconButton("play", "Start a pomodoro", size=28)
            self.go.clicked.connect(self.tap)
            self.reset = IconButton("undo", "Give up this pomodoro: back to 25:00", size=28)
            self.reset.clicked.connect(self.n.focus_reset)
            self.lay.addWidget(self.go, 0, Qt.AlignmentFlag.AlignVCenter)
            self.lay.addWidget(self.reset, 0, Qt.AlignmentFlag.AlignVCenter)

    def fill(self):
        st = I.focus_now(self.n.island.get("focus"))
        clock, line = I.focus_words(st)
        self.left.setText(clock)
        self.line.set(line)
        ph, brk = st["phase"], st["kind"] != "focus"
        self.ring.running = ph == "running"
        self.ring.set(1.0 if ph == "ended" else 0.0 if ph == "ready" else 1 - st["left"] / st["total"], BREAK_FILL if brk else RING_FILL)
        self.ring.update()
        self.setAccessibleName(f"Pomodoro timer: {clock}, {line}, {st['today']} today")
        if self.go is not None:
            self.go.icon_name = "forward" if brk else "pause" if ph == "running" else "play"
            self.go.setAccessibleName("Skip the break" if brk else {"running": "Pause the pomodoro", "paused": "Go on with the pomodoro"}.get(
                ph, "Start a pomodoro"))
            self.go.setToolTip(self.go.accessibleName())
            self.go.update()
            self.reset.setVisible(not brk and ph in ("running", "paused"))

    def tick(self):
        self.fill()

    def tap(self):
        self.n.focus_tap()


class MusicTile(Tile):
    """What plays on this PC (Windows' media sessions, core/media.py): the track's art, its title and artist; Wide and Big: back, play /
    pause, forward, each checked against what Windows shows after it; Big: where in the track it is when the player says. Nothing playing,
    or no way to read them here, is said as such. A small one plays or pauses with a tap."""

    def build(self):
        self.prog = self.disc = None
        self.btns = []
        if self.size == "big":
            self.lay.setSpacing(12)
            self.cover = Cover(98, 10)
            self.lay.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
            col = QVBoxLayout()
            col.setSpacing(2)
            self.title, self.line = Elided("", 14, 700), Elided("", 11.5, 400, TEXT_2)
            col.addWidget(self.title)
            col.addWidget(self.line)
            col.addStretch(1)
            self.prog = Progress()
            col.addWidget(self.prog)
            col.addSpacing(6)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addStretch(1)
            back = IconButton("back", "Previous track", size=26)
            back.clicked.connect(lambda: self.n.media_control("previous"))
            self.disc = PlayDisc(30)
            self.disc.clicked.connect(lambda: self.n.media_control("toggle"))
            fwd = IconButton("forward", "Next track", size=26)
            fwd.clicked.connect(lambda: self.n.media_control("next"))
            self.btns = [back, self.disc, fwd]
            for b in self.btns:
                row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
            col.addLayout(row)
            self.lay.addLayout(col, 1)
            return
        small = self.size == "small"  # (Small: its words get 85 px, room for "What is Deep / Learning? …": the owner's screenshot, 2026-10-26)
        self.cover = Cover(34 if small else 42, 8)
        if small:
            self.lay.setSpacing(8)
        self.lay.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
        col = self._words(12.5, 11)
        if small:  # a long title on two lines in place of the artist, not "What is D…"
            two = Lines("", 12, 700)
            col.replaceWidget(self.title, two)
            self.title.deleteLater()
            self.title = two
            two.on_fit = lambda: self.line.setVisible("\n" not in two.text())
        self.lay.addLayout(col, 1)
        if self.size == "wide":
            for icon_name, name, op in (("back", "Previous track", "previous"), ("play", "Play or pause", "toggle"), ("forward", "Next track", "next")):
                b = IconButton(icon_name, name, size=26)
                b.clicked.connect(lambda _=False, op=op: self.n.media_control(op))
                self.btns.append(b)
                self.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)

    def fill(self):
        m = self.n.media
        title, line, playing = I.music_words(m.now, m.ok is not False)
        said = m.said if m.said and time.monotonic() - m.said_at < 6 else None  # a control that didn't do what was asked, said for a moment
        self.title.set(title)
        self.name_twice(title)
        self.line.set(said or (line if m.ok is not None else "Reading what plays…"))
        self.cover.set(m.cover if m.now else None)
        for b in self.btns:
            b.setVisible(playing is not None)
            b.setEnabled(not m.busy)
        if self.disc is not None:
            self.disc.set(bool(playing))
        if len(self.btns) == 3 and self.disc is None:
            self.btns[1].icon_name = "pause" if playing else "play"
            self.btns[1].update()
        self.tappable = self.size == "small" and playing is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.tappable else Qt.CursorShape.ArrowCursor)
        self.tick()

    def tick(self):
        if self.prog is not None:
            part = self.n.media.part()
            self.prog.setVisible(part is not None)
            if part is not None:
                self.prog.set(part)

    def enterEvent(self, e):
        if self.size == "small" and self.tappable:
            self.cover.over = "pause" if self.n.media.now and self.n.media.now.get("status") == "playing" else "play"
            self.cover.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.cover.over:
            self.cover.over = None
            self.cover.update()
        super().leaveEvent(e)

    def tap(self):
        if self.tappable:
            self.n.media_control("toggle")


class AiTile(Tile):
    """Monday and the owner's agents (the recording's AI widget): one of the owner's live sessions (its name and folder, its last steps,
    a dot for each session, its mascot), else Monday's own state, else Monday's last finished task with how it ended ("✓ Done"). Big: the
    dock's first pills under it. A tap: that session's view, else Monday's chat. The ↗ opens the session's own chat or Monday's window."""

    def build(self):
        from .qtnotch import Dots, PetView
        self.dot = K.Dot("off", 7 if self.size != "small" else 6)
        self.dots = self.open_btn = self.sub = self.outcome = None
        self.rows, self.pills = [], None
        if self.size == "big":
            self.lay.setContentsMargins(8, 10, 12, 10)
            self.lay.setSpacing(6)
            self.pet = PetView(self.n, 78)
            self.lay.addWidget(self.pet, 0, Qt.AlignmentFlag.AlignVCenter)
            col = QVBoxLayout()
            col.setSpacing(2)
            head = QHBoxLayout()
            head.setSpacing(6)
            head.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
            self.title = Elided("", 14, 700)
            head.addWidget(self.title)
            self.rest = Elided("", 12, 400, TEXT_2)  # (the recording's two-tone head: the name whole, the rest grey and shortened first)
            head.addWidget(self.rest, 1)
            self.dots = Dots()  # (in the bottom row, by the session's own chip: the title keeps its width)
            self.dots.clicked.connect(lambda i: self.n._show_session(self.dots.keys[i]))
            self.dots.hide()
            self.open_btn = IconButton("open", "Open Monday's window", size=20, lit=True)
            self.open_btn.clicked.connect(self.n._to_chat)
            head.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignTop)
            col.addLayout(head)
            mid = QHBoxLayout()
            mid.setSpacing(8)
            self.line = Lines("", 12, 600)  # (the last task or Monday's words: two lines when they need them, the review)
            mid.addWidget(self.line, 1)
            self.outcome = label("", 12, 700, K.GREEN_TEXT)
            mid.addWidget(self.outcome, 0, Qt.AlignmentFlag.AlignVCenter)
            col.addLayout(mid)
            self.sub = Elided("", 11, 400, TEXT_2)
            col.addWidget(self.sub)
            self.rows = [Elided("", 11, 400, TEXT_2) for _ in range(I.AI_ROWS + 1)]  # a session's last steps, or Monday's own tasks + "(+N more)"
            for r in self.rows:
                r.hide()
                col.addWidget(r)
            col.addStretch(1)
            foot = QHBoxLayout()  # the recording's chip under the words: the dock's pills, or the session's own agent and its dots
            foot.setSpacing(6)
            self.pills = QHBoxLayout()
            self.pills.setSpacing(6)
            foot.addLayout(self.pills, 1)
            foot.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignVCenter)
            col.addLayout(foot)
            self.lay.addLayout(col, 1)
            return
        self.pet = PetView(self.n, 44 if self.size == "wide" else 40)
        self.lay.addWidget(self.pet, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(5)
        head.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title = Elided("", 13 if self.size == "wide" else 12.5, 700)
        head.addWidget(self.title)
        self.rest = Elided("", 11, 400, TEXT_2)
        head.addWidget(self.rest, 1)
        col.addStretch(1)
        col.addLayout(head)
        self.line = Elided("", 11, 400, TEXT_2)
        col.addWidget(self.line)
        col.addStretch(1)
        self.lay.addLayout(col, 1)
        if self.size == "wide":
            self.outcome = label("", 11.5, 700, K.GREEN_TEXT)
            self.lay.addWidget(self.outcome, 0, Qt.AlignmentFlag.AlignVCenter)

    def fill(self):
        f = self.n.ai_face()
        self.dot.set(f["dot"])
        name, _sep, rest = f["title"].partition(" · ")  # "Monday · done in 19m 23s": the name whole, the rest grey (the owner's screenshot,
        parts = rest.split(" · ") if rest else []        # 2026-10-26: "Monday · done in 19…"); a finished one's time before its folder
        if len(parts) > 1 and parts[-1].startswith("done in"):
            rest = " · ".join([re.sub(r"(\d+m) \d+s$", r"\1", parts[-1])] + parts[:-1])  # ("19m 23s": its minutes; the card's name has all)
        rows = f.get("rows") or []
        mine = []  # Monday's own running tasks, every one a row (island.task_rows), while no session of the owner's has the tile
        st = self.n.state
        if not rows and not f.get("session") and st.tasks and getattr(self.n, "shown", ("idle",))[0] in ("working", "waiting"):
            mine = I.task_rows(st.tasks, st.steps, st.perms)
            if self.size == "big":  # the head counts them, each a row under it (Wide and Small: the notch's own head, the newest task's)
                name = STATES["working"][1] if self.n.shown[0] == "working" else name
                rest = f"{len(st.tasks)} task{'s' if len(st.tasks) != 1 else ''}"
        self.title.set(name)
        self.title.setMinimumWidth(min(self.title.sizeHint().width(), AI_NAME_MAX))
        self.rest.set(rest)
        self.rest.setVisible(bool(rest))
        if self.size == "big" and (rows or mine):  # a session: its last steps where the last task was (newest last, bright); Monday's own
            self.line.hide()                        # tasks: the newest first, bright
            self.sub.hide()
            shown, bright = (rows, len(rows) - 1) if rows else (mine, 0)
            for i, r in enumerate(self.rows):
                r.set(shown[i] if i < len(shown) else "")
                r.setStyleSheet(f"color: {TEXT if i == bright else TEXT_2}; background: transparent;")
                r.setVisible(i < len(shown))
        else:
            self.line.show()
            self.line.set(self._newest() if mine else f.get("line") or "")
            for r in self.rows:
                r.hide()
            if self.sub is not None:
                self.sub.set(f.get("sub") or "")
                self.sub.setVisible(bool(f.get("sub")))
        if self.outcome is not None:
            words, dot = f.get("outcome") or ("", "off")
            self.outcome.setText(words)
            self.outcome.setStyleSheet(f"color: {K.Dot.COLORS.get(dot, TEXT_2) if dot != 'off' else TEXT_2}; background: transparent;")
            self.outcome.setVisible(bool(words))
        if self.dots is not None:
            keys, at = f.get("dots") or ([], 0)
            self.dots.set(keys, at)
            self.dots.setVisible(len(keys) > 1)
        if self.open_btn is not None:
            chat = "Open this session's chat" if f.get("session") else "Open Monday's window"
            self.open_btn.setAccessibleName(chat)
            self.open_btn.setToolTip(chat)
        self.setAccessibleName(f"AI: {name} · {rest}. " + "; ".join(mine) if mine else f"AI: {f['title']}. {f.get('line') or ''}".strip())

    def _newest(self):
        """What Monday's newest task is on (Wide and Small: one line under the head, which has its title and "+N more")."""
        st = self.n.state
        tid, t = list(st.tasks.items())[-1]
        return I.task_row(t, st.steps.get(tid), any(p.get("task_id") == tid for p in st.perms.values()))[1]

    def tap(self):
        self.n.ai_tap()


AI_NAME_MAX = 150  # the AI card's name never shortened below this (a long title of Monday's own: the ↗ stays on the card)


# -- the 2026-10-26 recording's widgets ("Coucou for Windows 0.2"): the agents beside the session, plan use, GitHub ---------------------------
PLAN_FILL = {"5 hours": "#F2A33A"}             # the recording's amber for the 5-hour window; every other window green
PLAN_GREEN = "#3DDC84"
HEAT = ("#1F2523", "#0E4429", "#006D32", "#26A641", "#39D353")  # GitHub's own five greens, none to most
GH_DOT = "#E35D5B"


class AgentsTile(Tile):
    """The agents (the recording's dark panel of 2 x 2 pills beside the session): the dock's pills, each program's own logo (the owner's
    choice, 2026-10-26), an orange "!" on one whose agent asks now; a click goes to it. Big: four (three and "N more"); Wide: two; Small: how
    many. Only what is open or the owner chose in Settings: never an agent that isn't here."""

    def build(self):
        from .qtnotch import Pill
        self.Pill, self.pills = Pill, []
        if self.size == "small":
            self.icon = AppIcon(self.key, 40)
            self.lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
            self.lay.addLayout(self._words(13), 1)
            return
        self.lay.setContentsMargins(10, 10, 10, 10)
        self.box = QGridLayout()
        self.box.setHorizontalSpacing(8)
        self.box.setVerticalSpacing(8)
        self.lay.addLayout(self.box, 1)
        self.empty = label("", 11.5, 400, TEXT_2, wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lay.addWidget(self.empty, 1)

    def _asking(self, name):
        n = str(name or "").lower()
        return any(str(a.get("agent") or "").lower() in (n, n.split(" ")[0]) for a in self.n.state.asks.values() if a.get("agent"))

    def fill(self):
        items = self.n._dock_items()
        asks = sum(1 for a in self.n.state.asks.values())
        if self.size == "small":
            self.title.set("Agents")
            self.line.set(f"{len(items)} open" + (f" · {asks} asks you" if asks else "") if items else I.WIDGETS["agents"][2])
            self.setAccessibleName(f"Agents: {self.line.accessibleName()}")
            return
        most = 4 if self.size == "big" else 2
        shown = items[:most] if len(items) <= most else items[:most - 1] + [{"name": f"{len(items) - most + 1} more", "more": True}]
        sig = tuple((pl.get("name"), pl.get("session"), pl.get("exe"), str(pl.get("state")), self._asking(pl.get("name"))) for pl in shown)
        if sig != getattr(self, "_sig", None):
            self._sig = sig
            for w in self.pills:
                w.hide()
                w.deleteLater()
            self.pills = []
            for i, pl in enumerate(shown):
                pill = self.Pill(pl.get("name") or "?", self.n._colour(pl), pl.get("folder") or "", pl.get("exe"), pl.get("state"))
                pill.alert = self._asking(pl.get("name"))
                pill.clicked.connect(lambda pl=pl: self.n._go_place(pl))
                self.box.addWidget(pill, i // 2, i % 2)
                pill.show()  # (made while Home is put by, Qt would leave it hidden until asked)
                self.pills.append(pill)
        self.empty.setText("" if items else "No agents or IDEs open. Pick the ones to keep here in Settings › Agents.")
        self.empty.setVisible(not items)
        self.setAccessibleName(f"Agents: {', '.join(pl.get('name') or '?' for pl in items) or 'none open'}" + (f"; {asks} asks you" if asks else ""))

    def tap(self):
        self.n.open_window("agents")


class PlanTile(Tile):
    """Plan use (the recording's "Your plan, always in sight"): each window of the plan as the agent's own log last said it (Codex writes its
    limits there: used %, when it starts again), how old that is; Claude Code's log says nothing of its limits, so Claude is N/A. Read on a
    thread by the notch (watch.plan_use), never guessed."""

    def build(self):
        self.dot = K.Dot(PLAN_GREEN, 7)
        if self.size == "big":
            col = QVBoxLayout()
            col.setSpacing(6)
            col.addStretch(1)
            head = QHBoxLayout()
            head.setSpacing(6)
            head.addWidget(self.dot)
            self.title = Elided("", 14, 700)
            head.addWidget(self.title)
            self.sub = Elided("", 11.5, 400, TEXT_2)
            head.addWidget(self.sub, 1)
            col.addLayout(head)
            self.rows = []
            for _ in range(2):
                r = QHBoxLayout()
                r.setSpacing(8)
                name, bar, pct, when = label("", 11.5, 400, TEXT_2), Progress(PLAN_GREEN), label("", 12, 700), label("", 11, 400, TEXT_2)
                name.setFixedWidth(62)
                bar.setFixedWidth(84)
                for w_ in (name, bar, pct, when):
                    r.addWidget(w_, 0, Qt.AlignmentFlag.AlignVCenter)
                r.addStretch(1)
                col.addLayout(r)
                self.rows.append((name, bar, pct, when))
            self.claude = Elided("", 11, 400, TEXT_3)
            col.addWidget(self.claude)
            col.addStretch(1)
            self.lay.addLayout(col, 1)
            return
        self.icon = AppIcon(self.key, 40 if self.size == "small" else 44)
        self.lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lay.addLayout(self._words(13), 1)

    def fill(self):
        plans = getattr(self.n, "plan", None)
        rows = I.plan_rows(plans)
        p = rows[0] if rows else None
        claude = "Claude: N/A (its log keeps no limits)"
        if self.size != "big":
            self.title.set(f"{p['agent']} {p['rows'][0][1]}%" if p and p["rows"] else "Plan")
            fits = (f"{p['rows'][0][0]} · {p['head']}" if self.size == "wide" else f"{p['rows'][0][0]} · {p['head'].split(' · ')[0]}") if p and \
                p["rows"] else ("Reading…" if plans is None else "No plan use read")
            self.line.set(fits)
            self.setAccessibleName(f"Plan: {self.title.accessibleName()}. {self.line.accessibleName()}")
            return
        self.title.set(f"{p['agent']} plan" if p else "Plan")
        self.sub.set(p["head"] if p else ("Reading the agents' logs…" if plans is None else "No plan use in any agent's log yet"))
        for i, (name, bar, pct, when) in enumerate(self.rows):
            r = p["rows"][i] if p and i < len(p["rows"]) else None
            for w_ in (name, bar, pct, when):
                w_.setVisible(r is not None)
            if r:
                name.setText(r[0])
                bar.fill = PLAN_FILL.get(r[0], PLAN_GREEN)
                bar.set(r[1] / 100)
                pct.setText(f"{r[1]}%")
                when.setText(f"↻ {r[2]}" if r[2] else "")
        self.claude.set(claude)
        self.setAccessibleName(f"Plan: {self.title.accessibleName()}, {self.sub.accessibleName()}. " +
                               "; ".join(f"{r[0]} {r[1]}%" + (f", resets {r[2]}" if r[2] else "") for r in (p or {}).get("rows", [])) + f". {claude}")


class Squares(QWidget):
    """The last days' contributions as GitHub's green squares (the recording's streak, beside the stars; the Activity heat map at Big)."""
    clicked = Signal()
    hovered = Signal(int)

    def __init__(self, cols, rows=1, cell=10, gap=3, fit=False):
        super().__init__()
        self.cols, self.rows_, self.cell, self.gap, self.days, self.at = cols, rows, cell, gap, [], None
        self.fit, self.all, self.x0 = fit, [], 0.0
        if fit:  # as many columns as its width holds, centred (the Activity map: the card's inner width, the same inset both sides)
            self.setFixedHeight(rows * (cell + gap) - gap)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedSize(cols * (cell + gap) - gap, rows * (cell + gap) - gap)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set(self, days):
        self.all = list(days or [])
        self.days = self.all[-self.cols * self.rows_:]
        self.update()

    def resizeEvent(self, e):
        if self.fit:
            self.cols = max(1, int((self.width() + self.gap) // (self.cell + self.gap)))
            self.x0 = (self.width() - (self.cols * (self.cell + self.gap) - self.gap)) / 2
            self.set(self.all)

    def _index(self, pos):
        x = pos.x() - self.x0
        c, r = int(x // (self.cell + self.gap)) if x >= 0 else -1, int(pos.y() // (self.cell + self.gap))
        i = c * self.rows_ + r if self.rows_ > 1 else c
        start = self.cols * self.rows_ - len(self.days)
        return i - start if 0 <= c < self.cols and 0 <= r < self.rows_ and i >= start else None

    def mouseMoveEvent(self, e):
        i = self._index(e.position())
        if i != self.at:
            self.at = i
            self.update()
            if i is not None:
                self.hovered.emit(i)

    def leaveEvent(self, e):
        self.at = None
        self.update()
        self.hovered.emit(-1)  # (the pointer gone: the card says the whole again)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        start = self.cols * self.rows_ - len(self.days)
        for i in range(self.cols * self.rows_):
            c, r = (i // self.rows_, i % self.rows_) if self.rows_ > 1 else (i, 0)
            day = self.days[i - start] if i >= start else None
            rect = QRectF(self.x0 + c * (self.cell + self.gap), r * (self.cell + self.gap), self.cell, self.cell)
            p.setPen(QPen(QColor("#E6EDF3"), 1.2) if day is not None and i - start == self.at else Qt.PenStyle.NoPen)
            p.setBrush(QColor(HEAT[I.heat(day[1])] if day else "#161A19"))
            p.drawRoundedRect(rect, 2.2, 2.2)
        p.end()


class GithubTile(Tile):
    """GitHub (the recording's "PRs, CI and your streak"), read through the owner's own signed-in gh by the notch (core/github.py): their open
    pull requests, the ones waiting on their review, the newest CI run on a repository's default branch (theirs to choose: the menu on its
    name), its stars, and the last week as squares. Big: the squares open Activity (the year as a heat map, a day's count under the
    pointer), My PRs opens their list; ‹ comes back. Nothing is written: a pull request opens in the owner's own browser."""

    def build(self):
        self.mode = "summary"
        self.dot = K.Dot(GH_DOT, 7)
        if self.size != "big":
            self.icon = AppIcon(self.key, 40 if self.size == "small" else 44)
            self.lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
            self.lay.addLayout(self._words(13), 1)
            if self.size == "wide":
                self.week = Squares(7, 1, 9, 3)
                self.week.clicked.connect(self.tap)
                self.lay.addWidget(self.week, 0, Qt.AlignmentFlag.AlignVCenter)
            return
        col = QVBoxLayout()
        col.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.back = IconButton("chevron_left", "Back to GitHub's summary", size=22)
        self.back.clicked.connect(lambda: self.show_mode("summary"))
        head.addWidget(self.back)
        head.addWidget(self.dot)
        self.title = label("GitHub", 14, 700)
        head.addWidget(self.title)
        self.stars = label("", 12, 400, TEXT_2)
        head.addWidget(self.stars)
        self.week = Squares(7, 1, 10, 3)
        self.week.setToolTip("Your last 7 days on GitHub: open the year")
        self.week.setAccessibleName("Your last 7 days on GitHub: open the year")
        self.week.clicked.connect(lambda: self.show_mode("activity"))
        head.addWidget(self.week, 0, Qt.AlignmentFlag.AlignVCenter)
        self.sub = Elided("", 11.5, 400, TEXT_2)
        head.addWidget(self.sub, 1)
        self.open_btn = IconButton("open", "Open GitHub in your browser", size=20, lit=True)
        self.open_btn.clicked.connect(self._open_profile)
        head.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(head)
        self.summary = QWidget()
        sv = QVBoxLayout(self.summary)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(2)
        self.row_btns = {}
        for key, words in (("prs", "My PRs"), ("review", "To review"), ("ci", "Default branch CI")):
            b = Button(words, "quiet", height=22, size=12, name=words)
            b.clicked.connect(lambda _=False, k=key: self._row(k))
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(b)
            row.addStretch(1)
            v = label("", 12.5, 700)
            row.addWidget(v)
            sv.addLayout(row)
            self.row_btns[key] = (b, v)
        col.addWidget(self.summary)
        self.heat = Squares(1, 7, 8, 2, fit=True)  # (the card's width of weeks, the newest at the right: the recording's map)
        self.heat.hovered.connect(self._day)
        col.addWidget(self.heat)
        self.prs = QVBoxLayout()
        self.prs.setSpacing(2)
        col.addLayout(self.prs)
        self.no_prs = label("No open pull requests", 12, 400, TEXT_2)
        self.no_prs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_prs.hide()
        col.addWidget(self.no_prs, 1)
        self.pr_btns = []
        col.addStretch(1)
        self.lay.addLayout(col, 1)
        self.show_mode("summary")

    def show_mode(self, mode):
        self.mode = mode
        if self.size == "big":
            self.back.setVisible(mode != "summary")
            self.summary.setVisible(mode == "summary")
            self.heat.setVisible(mode == "activity")
            self.week.setVisible(mode == "summary")
            self.stars.setVisible(mode == "summary")
            for b in self.pr_btns:
                b.setVisible(mode == "prs")
            self.no_prs.hide()
        self.fill()

    def _row(self, key):
        if key == "prs":
            return self.show_mode("prs")
        g = getattr(self.n, "github", None) or {}
        if key == "ci" and g.get("repo"):  # its name: the repositories to choose from (the widget's own setting, kept with the island)
            return self.n.github_repo_menu()
        if key == "review" and g.get("login"):
            self.n._open_url("https://github.com/pulls/review-requested")

    def _open_profile(self):
        g = getattr(self.n, "github", None) or {}
        self.n._open_url(f"https://github.com/{g['login']}" if g.get("login") else "https://github.com")

    @staticmethod
    def _overall(g):
        """The year at a glance (the owner, 2026-10-26: "in the github we can see the overall"): its contributions, the streak now and the
        best one; N/A with no calendar read."""
        from . import github as gh
        days = (g or {}).get("days") or []
        if not days:
            return "N/A"
        total = g["total"] if g.get("total") is not None else sum(c for _d, c in days)
        now = gh.streak(days)
        return f"{total} past year · {now}-day streak"  # (whole in the card's head, 151 px: its best streak is in the line's tip)

    def _best_tip(self, g):
        from . import github as gh
        days = (g or {}).get("days") or []
        if days:  # (after set: the year's whole, and its longest streak, under the pointer)
            self.sub.setToolTip(f"{self.sub.accessibleName()} · best {gh.best_streak(days)} days in a row")

    def _day(self, i):
        g = getattr(self.n, "github", None) or {}
        days = (g.get("days") or [])[-self.heat.cols * self.heat.rows_:]
        if i < 0:
            if self.mode == "activity":
                self.sub.set(self._overall(g))
                self._best_tip(g)
            return None
        if 0 <= i < len(days):
            d, c = days[i]
            self.sub.set(f"{_short_date(d)} · {c} contribution{'s' if c != 1 else ''}")

    def fill(self):
        from . import github as gh
        g = getattr(self.n, "github", None)
        ok = bool(g and g.get("ok"))
        prs = (g or {}).get("prs") or []
        ci = gh.ci_words((g or {}).get("ci")) if ok else "N/A"
        if self.size != "big":
            self.title.set("GitHub")
            short = {"all green": "green"}.get(ci, ci) if self.size == "small" else ci
            self.line.set("Reading…" if g is None else (f"{len(prs)} PR{'s' if len(prs) != 1 else ''} · CI {short}" if ok else
                                                        ("Not signed in" if self.size == "small" else g.get("why") or "N/A")))
            if self.size == "wide":
                self.week.set((g or {}).get("days") or [])
            self.setAccessibleName(f"GitHub: {self.line.accessibleName()}")
            return
        self.week.set((g or {}).get("days") or [])
        self.heat.set((g or {}).get("days") or [])
        overall = ok and g.get("stars_all") is not None  # (the stars across all the owner's repositories, 2026-10-26; else the one's)
        self.stars.setText(f"★ {_count(g['stars_all'])}" if overall else f"★ {_count(g.get('stars'))}" if ok and g.get("stars") is not None else "")
        self.stars.setToolTip(f"Stars across your {g.get('repo_count')} repositories" if overall else "")
        if self.mode == "activity":
            self.title.setText("Activity")
            self.sub.set(self._overall(g))
            self._best_tip(g)
        elif self.mode == "prs":
            self.title.setText("My PRs")
            self.sub.set("")
            self.no_prs.setVisible(not prs)
            for b in self.pr_btns:
                b.hide()
                b.deleteLater()
            self.pr_btns = []
            for pr in prs[:3]:
                b = Button(f"{pr['repo'].split('/')[-1]}#{pr['number']}  {pr['title']}", "plain", height=22, size=12, dot=True,
                           name=f"{pr['repo']}#{pr['number']}: {pr['title']} (opens in your browser)")
                b.clicked.connect(lambda _=False, u=pr["url"]: self.n._open_url(u))
                self.prs.addWidget(b)
                self.pr_btns.append(b)
        else:
            self.title.setText("GitHub")
            self.sub.set("Reading GitHub…" if g is None else "" if ok else g.get("why") or "N/A")
            for key, value in (("prs", str(len(prs)) if ok else "N/A"), ("review", str(g.get("reviews")) if ok and g.get("reviews") is not None else "N/A"),
                               ("ci", ci)):
                b, v = self.row_btns[key]
                v.setText(value)
                v.setStyleSheet(f"color: {K.GREEN_TEXT if key == 'ci' and ci == 'all green' else K.RED_TEXT if ci == 'failing' and key == 'ci' else TEXT};"
                                " background: transparent;")
                if key == "ci":
                    b.setText(f"CI on {g['repo'].split('/')[-1]}" if ok and g.get("repo") else "Default branch CI")
                    b.setAccessibleName(f"{b.text()}: {ci}. Click to choose the repository")
        self.setAccessibleName(f"GitHub: {len(prs)} open pull requests, CI {ci}" if ok else f"GitHub: {(g or {}).get('why') or 'reading'}")

    def tap(self):
        if self.size == "big":
            return self.show_mode("activity" if self.mode == "summary" else "summary")
        self.n.open_window("integrations")


def _short_date(d):
    try:
        y, m, dd = (int(x) for x in str(d).split("-"))
        return f"{I.MONTHS[m - 1]} {dd}"
    except (ValueError, IndexError):
        return str(d)


def _count(n):
    n = int(n or 0)
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


TILES = {"ai": AiTile, "music": MusicTile, "focus": FocusTile, "tray": TrayTile, "clipboard": ClipTile, "note": NoteTile,
         "settings": SettingsTile, "clock": ClockTile, "agents": AgentsTile, "plan": PlanTile, "github": GithubTile}


def make_tile(grid, key, size):
    return TILES[key](grid, key, size)


# -- the grid and its edit mode -----------------------------------------------------------------------------------------------------------
class Grid(QWidget):
    """Home's widgets on the island's grid (island.py's layout; 4 x 2, or the floating island's own 3 x 1 of taller cells: set_dims), each
    a Tile at its cell. A tap is the tile's; a drag (before it is held) moves the notch as its body always did, or, on the AI tile with
    sessions, swipes between them; held HOLD_MS: edit mode."""

    def __init__(self, notch):
        super().__init__()
        self.n = notch
        self.layout_, self.tiles, self.editing = [], {}, False
        self._press = None              # {"tile", "at" (grid px), "glob", "held", "moved"} while the mouse is down on a tile
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(HOLD_MS)
        self._hold.timeout.connect(self._held)
        self.dims, self.cell_h = I.GRID, CELL_H
        self.setFixedHeight(I.ROWS * CELL_H + (I.ROWS - 1) * GAP)
        self.setAccessibleName("Your widgets: hold one to arrange them")
        self.over = Overlay(self)
        self.over.hide()

    # where things are
    def set_dims(self, dims, cell_h):
        """The look's grid (island.GRID or ISLAND_GRID) and its cells' height."""
        self.dims, self.cell_h = tuple(dims), cell_h
        self.setFixedHeight(int(dims[1] * cell_h + (dims[1] - 1) * GAP))
        self._place()

    def cell_w(self):
        return (max(self.width(), 200) - (self.dims[0] - 1) * GAP) / self.dims[0]

    def pitch(self):
        return self.cell_w() + GAP, self.cell_h + GAP

    def rect_of(self, x, y, size):
        cw, ch = I.SIZES[size]
        w = self.cell_w()
        return QRectF(x * (w + GAP), y * (self.cell_h + GAP), cw * w + (cw - 1) * GAP, ch * self.cell_h + (ch - 1) * GAP)

    def set_layout(self, layout):
        """The tiles for this layout: a widget staying at its size keeps its tile (its data, its pet); one new or resized is made anew."""
        old = {it["w"]: it for it in self.layout_}
        self.layout_ = [dict(it) for it in layout]
        for k in list(self.tiles):
            it = I.find(layout, k)
            if not it or it["size"] != old[k]["size"]:
                t = self.tiles.pop(k)
                t.hide()
                t.deleteLater()
        for it in self.layout_:
            if it["w"] not in self.tiles:
                self.tiles[it["w"]] = make_tile(self, it["w"], it["size"])
                self.tiles[it["w"]].show()
        self._place()
        self.n.tiles_changed()

    def _place(self):
        for it in self.layout_:
            self.tiles[it["w"]].setGeometry(self.rect_of(it["x"], it["y"], it["size"]).toRect())
        self.over.setGeometry(self.rect())
        self.over.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def fill(self):
        for t in self.tiles.values():
            t.fill()

    def tick(self):
        for t in self.tiles.values():
            t.tick()

    def edit(self, on):
        """Edit mode on (the recording's "Everything wiggles") or off (Done, closing the notch): the layout is kept at every change."""
        if on == self.editing:
            return
        self.editing = on
        self._hold.stop()
        if on:
            self.over.start()
        else:
            self.over.stop()
        self.n.edit_changed(on)

    def change(self, layout):
        """A new layout from the owner (a drop, −, resize, Add, a ready-made layout): kept, its tiles made, their faces gliding there."""
        if layout == self.layout_:
            return False
        if self.editing:
            self.over.going_to(layout)
        else:
            self.set_layout(layout)
        self.n.keep_island()
        return True

    # the mouse on a tile (forwarded by Tile)
    def pressed(self, tile, e):
        at = tile.mapTo(self, e.position().toPoint())
        self._press = {"tile": tile, "at": at, "glob": e.globalPosition().toPoint(), "held": False, "moved": False, "t": time.monotonic()}
        if self.editing:
            return self.over.press_at(QPointF(at))
        self._hold.start()

    def dragged(self, e):
        pr = self._press
        if pr is None:
            return
        d = e.globalPosition().toPoint() - pr["glob"]
        if self.editing:
            return self.over.move_to(QPointF(pr["at"] + d))
        from .notch import dragging
        if pr["moved"] or not dragging(time.monotonic() - pr["t"], d.manhattanLength(), QApplication.startDragDistance()):
            return  # (a click whose pointer drifted as the touchpad was pressed: still the tile's tap, the owner 2026-10-29)
        pr["moved"] = True
        self._hold.stop()
        if pr["tile"].key == "ai" and self.n.card_session:  # (the AI tile with sessions: a sideways drag is a swipe, on release)
            return
        self._press = None
        self.n._close_t.stop()
        handle = self.n.windowHandle()
        if handle is not None:
            handle.startSystemMove()  # (a drag on a tile moves the notch, as its cards always did)

    def released(self, e):
        pr, self._press = self._press, None
        self._hold.stop()
        if pr is None:
            return
        if self.editing:
            return self.over.release_at(QPointF(pr["at"] + (e.globalPosition().toPoint() - pr["glob"])))
        d = e.globalPosition().toPoint() - pr["glob"]
        if pr["moved"]:
            from .qtnotch import SWIPE_PX
            if pr["tile"].key == "ai" and abs(d.x()) >= SWIPE_PX and abs(d.x()) > abs(d.y()):
                self.n._swipe(-1 if d.x() > 0 else 1)  # (a swipe on the AI tile: left for the next session, right for the one before)
            return
        if pr["tile"].tappable and pr["tile"].rect().contains(pr["tile"].mapFromGlobal(e.globalPosition().toPoint())):
            pr["tile"].tap()

    def _held(self):
        pr = self._press
        if pr is None or pr["moved"] or not self.isVisible():
            return
        pr["held"] = True
        self.edit(True)
        self.over.press_at(QPointF(pr["at"]))  # (still down: moving it now drags the tile it held, as on a phone)


class Overlay(QWidget):
    """Edit mode over the grid: each tile's face (its last look) turned a little back and forth, out of step (none with reduced motion), its
    − at the top left and its resize badge at the bottom right; each empty cell's dashes and lime +; a tile being dragged under the pointer.
    It paints over the tiles in the island's own black, so they stay put underneath (a press begun on one keeps its mouse)."""

    def __init__(self, grid):
        super().__init__(grid)
        self.g, self.faces, self.at, self.going = grid, {}, {}, []
        self.drag = None      # {"key", "grab": pointer - face's top left, "pos": its top left now}
        self.dropped = None   # the tile last let go (drawn over the ones gliding out of its way)
        self.down = None      # what the press is on: ("remove" | "resize" | "face" | "slot", key or cell, where)
        self.t0 = time.monotonic()
        self.setMouseTracking(True)
        self.setAccessibleName("Arranging your widgets: drag to move, − to remove, the corner to resize, + to add")
        self._t = QTimer(self)
        self._t.setInterval(FRAME_MS)
        self._t.timeout.connect(self._frame)
        self._faces_t = QTimer(self)
        self._faces_t.setInterval(FACE_EVERY_MS)
        self._faces_t.timeout.connect(self.grab_faces)

    def start(self):
        self.drag, self.down, self.going = None, None, []
        self.grab_faces()
        self.at = {k: self.target(k) for k in self.faces}
        self.t0 = time.monotonic()
        self.setGeometry(self.g.rect())
        self.show()
        self.raise_()
        self._t.start()
        self._faces_t.start()

    def stop(self):
        self._t.stop()
        self._faces_t.stop()
        self.drag = self.down = None
        self.hide()

    def grab_faces(self):
        """Each tile's look now (the tiles underneath stay live: the clock and the music go on)."""
        self.faces = {k: t.grab() for k, t in self.g.tiles.items()}

    def target(self, key):
        it = I.find(self.g.layout_, key)
        return self.g.rect_of(it["x"], it["y"], it["size"]) if it else QRectF()

    def going_to(self, layout):
        """A change while arranging: what left shrinks away, the rest glide from where they are to their new cells, a new one grows in."""
        before = {k: QRectF(r) for k, r in self.at.items()}
        now = time.monotonic()
        if not reduced_motion():  # (with motion off, a removed tile is simply gone)
            self.going += [(k, before[k], self.faces.get(k), now) for k in before if not I.find(layout, k) and k in self.faces]
        self.g.set_layout(layout)
        self.grab_faces()
        for k in self.faces:
            t = self.target(k)
            self.at[k] = t if reduced_motion() else before.get(k, QRectF(t.center(), QSize(0, 0)).adjusted(-t.width() * 0.3, -t.height() * 0.3,
                                                                                                             t.width() * 0.3, t.height() * 0.3))
        for k in [k for k in self.at if k not in self.faces]:
            del self.at[k]
        self.update()

    def _frame(self):
        now = time.monotonic()
        k_ease = 1 - math.exp(-FRAME_MS / 1000 / (MOVE_MS / 1000 / 4))  # an exponential glide: ~MOVE_MS to arrive
        for k in self.at:
            t = self.target(k)
            r = self.at[k]
            if reduced_motion():
                self.at[k] = t
                continue
            self.at[k] = QRectF(r.x() + (t.x() - r.x()) * k_ease, r.y() + (t.y() - r.y()) * k_ease, r.width() + (t.width() - r.width()) * k_ease,
                                r.height() + (t.height() - r.height()) * k_ease)
        self.going = [g for g in self.going if now - g[3] < MOVE_MS / 1000]
        self.update()

    def angle(self, key):
        if reduced_motion() or (self.drag and self.drag["key"] == key):
            return 0.0
        return WIGGLE_DEG * math.sin(2 * math.pi * WIGGLE_HZ * (time.monotonic() - self.t0) + phase(key))

    @staticmethod
    def badges(r):
        """Where a tile's − and resize badges are (the recording's: on its top left and bottom right corners)."""
        return QPointF(r.left() + 7, r.top() + 6), QPointF(r.right() - 7, r.bottom() - 6)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#000000"))  # (the island's own black: the tiles underneath don't show)
        for x, y in I.free(self.g.layout_, self.g.dims):
            r = self.g.rect_of(x, y, "small").adjusted(1, 1, -1, -1)
            pen = QPen(QColor(DASH), 1.3, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3.5, 3])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, TILE_R, TILE_R)
            c = r.center()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(LIME))
            p.drawEllipse(c, 11, 11)
            K.paint_icon(p, "plus", QRectF(c.x() - 7, c.y() - 7, 14, 14), LIME_INK, 2.6)
        now = time.monotonic()
        for k, r, face, t0 in self.going:  # removed: shrinking and fading away
            u = min(1.0, (now - t0) / (MOVE_MS / 1000))
            if face is not None:
                p.save()
                p.setOpacity(1 - u)
                p.translate(r.center())
                p.scale(1 - 0.4 * u, 1 - 0.4 * u)
                p.drawPixmap(QRectF(-r.width() / 2, -r.height() / 2, r.width(), r.height()), face, QRectF(face.rect()))
                p.restore()
        drag_key = self.drag["key"] if self.drag else None
        for k, face in sorted(self.faces.items(), key=lambda kv: kv[0] == self.dropped):  # (the tile just let go on top while others glide)
            if k == drag_key or k not in self.at:
                continue
            self._face(p, k, face, self.at[k], self.angle(k), badges=True)
        if self.drag and self.drag["key"] in self.faces:
            r = self.target(self.drag["key"])
            pos = self.drag["pos"]
            lifted = QRectF(pos.x(), pos.y(), r.width(), r.height())
            self._face(p, self.drag["key"], self.faces[self.drag["key"]], lifted, 0.0, badges=False, scale=LIFT_SCALE, lifted=True)
        p.end()

    def _face(self, p, key, face, r, angle, badges, scale=1.0, lifted=False):
        p.save()
        p.translate(r.center())
        p.rotate(angle)
        p.scale(scale, scale)
        local = QRectF(-r.width() / 2, -r.height() / 2, r.width(), r.height())
        p.drawPixmap(local, face, QRectF(face.rect()))
        if lifted:  # held off the grid: a shade lighter, a light edge (a dark shadow can't show on the island's black)
            p.setPen(QPen(K.alpha("#FFFFFF", LIFT_EDGE), 1.2))
            p.setBrush(K.alpha("#FFFFFF", LIFT_LIGHT))
            p.drawRoundedRect(local.adjusted(0.6, 0.6, -0.6, -0.6), TILE_R, TILE_R)
        if badges:
            for c, glyph in zip(self.badges(local), ("minus", "resize")):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(BADGE_BG))
                p.drawEllipse(c, BADGE_R, BADGE_R)
                s = BADGE_R * 1.15 if glyph == "minus" else BADGE_R * 0.95
                K.paint_icon(p, glyph, QRectF(c.x() - s / 2, c.y() - s / 2, s, s), "#FFFFFF", 2.8 if glyph == "minus" else 2.4)
        p.restore()

    def hit(self, pos):
        """What a point is on: ("remove" | "resize", key), ("face", key) or ("slot", (x, y)); None for the black between."""
        for k, r in self.at.items():  # (the badges first: they sit on the tiles' corners)
            rm, rs = self.badges(r)
            if (pos - rm).manhattanLength() <= BADGE_R * 1.8:
                return "remove", k
            if (pos - rs).manhattanLength() <= BADGE_R * 1.8:
                return "resize", k
        for k, r in self.at.items():
            if r.contains(pos):
                return "face", k
        for x, y in I.free(self.g.layout_, self.g.dims):
            if self.g.rect_of(x, y, "small").contains(pos):
                return "slot", (x, y)
        return None

    def press_at(self, pos):
        h = self.hit(pos)
        self.down = (h[0], h[1], QPointF(pos)) if h else None
        if h and h[0] == "face":
            self.drag = None

    def move_to(self, pos):
        d = self.down
        if not d or d[0] != "face":
            return
        if self.drag is None and (pos - d[2]).manhattanLength() >= QApplication.startDragDistance():
            r = self.target(d[1])
            self.drag = {"key": d[1], "grab": d[2] - r.topLeft(), "pos": r.topLeft()}
        if self.drag is not None:
            self.drag["pos"] = pos - self.drag["grab"]
            self.update()

    def release_at(self, pos):
        d, self.down = self.down, None
        if self.drag is not None:
            key, top = self.drag["key"], self.drag["pos"]
            self.drag, self.dropped = None, key
            it = I.find(self.g.layout_, key)
            if it:
                x, y = I.snap(top.x(), top.y(), *self.g.pitch(), it["size"], self.g.dims)
                if key in self.at:  # (it glides from where it was let go: to its new cell, or back to its own)
                    self.at[key] = QRectF(top.x(), top.y(), self.at[key].width(), self.at[key].height())
                if not self.g.change(I.move(self.g.layout_, key, x, y, self.g.dims)):
                    self.update()  # (no room there: it glides back to its own cell)
            return
        if not d:
            return
        h = self.hit(pos)
        if not h or h[:2] != d[:2]:
            return  # (pressed on one thing, let go on another: nothing)
        if d[0] == "remove":
            self.g.change(I.remove(self.g.layout_, d[1]))
        elif d[0] == "resize":
            new, size = I.resize(self.g.layout_, d[1], self.g.dims)
            if size is None:
                self.g.n.said_island(f"No room to make {I.WIDGETS[d[1]][0]} another size: remove a widget first.")
            else:
                self.g.change(new)
        elif d[0] == "slot":
            self.g.n.open_menu(at=d[1])

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return e.ignore()
        self.g._press = {"tile": None, "at": e.position().toPoint(), "glob": e.globalPosition().toPoint(), "held": True, "moved": False}
        self.press_at(e.position())

    def mouseMoveEvent(self, e):
        if self.g._press is not None:
            self.move_to(e.position())
        hit = self.hit(e.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit and hit[0] in ("remove", "resize", "slot") else
                       Qt.CursorShape.OpenHandCursor if hit else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self.g._press = None
        self.release_at(e.position())


# -- the + view: the widget menu ----------------------------------------------------------------------------------------------------------
class _Holder(QWidget):
    """Where the menu's preview tile lives: a tile's own home for the mouse (a preview only shows)."""

    def __init__(self, notch):
        super().__init__()
        self.n = notch
        self.cell = 150.0

    def cell_w(self):
        return self.cell

    def pressed(self, *_a):
        pass

    def dragged(self, *_a):
        pass

    def released(self, *_a):
        pass


class WidgetMenu(QWidget):
    """The recording's widget menu, under the notch's own settings (since 2026-10-25): "Widgets" and its line, Done; the five ready-made
    layouts (the one on Home marked); the apps in two columns (a lime dot on those on Home), Small / Wide / Big, Add, and the pick as it
    will look. Add puts it on Home (over the empty cell whose + opened the menu, else where it fits); one already there takes the new size.
    No room: Add is off and says so."""

    def __init__(self, notch):
        super().__init__()
        self.n, self.pick, self.size, self.at = notch, "clock", "small", None
        self._preview_for = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label("Widgets", 14.5, 750))
        self.words = label("Pick an app and a size, or a ready-made layout", 11, 400, TEXT_2)
        head.addWidget(self.words, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.done = Lime("Done", "Done: back to Home", height=24, width=62)
        self.done.clicked.connect(lambda: self.n.show_view("home"))
        head.addWidget(self.done)
        v.addLayout(head)
        lays = QHBoxLayout()
        lays.setSpacing(6)
        self.layout_rows = {}
        for k, (name, _items) in I.LAYOUTS.items():
            lay = I.preset(k)
            b = Row(name, f"Ready-made layout: {name}", lambda p, r, lay=lay: paint_thumb(p, QRectF(r.left(), r.center().y() - 9, 30, 18), lay),
                    30, height=34)
            b.clicked.connect(lambda _=False, k=k: self.apply(k))
            self.layout_rows[k] = b
            lays.addWidget(b, 1)
        v.addLayout(lays)
        body = QHBoxLayout()
        body.setSpacing(12)
        apps = QGridLayout()
        apps.setContentsMargins(0, 0, 0, 0)
        apps.setHorizontalSpacing(6)
        apps.setVerticalSpacing(5)
        self.app_rows = {}
        for i, k in enumerate(I.MENU):
            name = I.WIDGETS[k][0]
            b = Row(name, f"{name} widget", lambda p, r, k=k: app_tile(p, QRectF(r.left(), r.center().y() - 10, 20, 20), k), 20, height=28)
            b.clicked.connect(lambda _=False, k=k: self.choose(k))
            self.app_rows[k] = b
            apps.addWidget(b, i // 2, i % 2)
        apps_box = QWidget()
        apps_box.setLayout(apps)
        apps_box.setFixedWidth(250)
        body.addWidget(apps_box, 0, Qt.AlignmentFlag.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(10)
        sizes = QHBoxLayout()
        sizes.setSpacing(6)
        self.size_btns = {}
        for s in I.CYCLE:
            b = Lime(I.SIZE_WORDS[s], f"{I.SIZE_WORDS[s]} size", height=24, width=62, lit=False)
            b.clicked.connect(lambda _=False, s=s: self.set_size(s))
            self.size_btns[s] = b
            sizes.addWidget(b)
        sizes.addStretch(1)
        self.add = Lime("Add", "Add it to Home", height=24, width=118)
        self.add.clicked.connect(self.add_it)
        sizes.addWidget(self.add)
        mid.addLayout(sizes)
        self.why = label("", 10.5, 400, TEXT_3)
        self.why.hide()
        mid.addWidget(self.why, 0, Qt.AlignmentFlag.AlignRight)
        self.holder = _Holder(notch)
        self.holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mid.addWidget(self.holder)
        mid.addStretch(1)
        body.addLayout(mid, 1)
        v.addLayout(body)
        self.preview = None

    def open(self, at=None):
        """Opened by + (at None) or an empty cell's + (at that cell): the first app not on Home chosen, at a size that fits there."""
        self.at = at
        lay = self.n.grid.layout_
        self.pick = next((k for k in I.MENU if k not in I.in_use(lay)), self.pick)
        it = I.find(lay, self.pick)
        self.size = it["size"] if it else next((s for s in I.CYCLE if I.add(lay, self.pick, s, at=at, dims=self.n.grid.dims) is not None), "small")
        self.fill()

    def choose(self, key):
        self.pick = key
        it = I.find(self.n.grid.layout_, key)
        if it:
            self.size = it["size"]
        self.fill()

    def set_size(self, size):
        self.size = size
        self.fill()

    def verdict(self):
        """(Add's words, whether it can, why not)."""
        lay = self.n.grid.layout_
        it = I.find(lay, self.pick)
        name = I.WIDGETS[self.pick][0]
        if it and it["size"] == self.size:
            return "On Home", False, f"{name} is on Home at this size."
        fits = I.add(lay, self.pick, self.size, at=self.at, dims=self.n.grid.dims) is not None
        word = "Resize" if it else "Add"
        return word, fits, "" if fits else f"No room for a {I.SIZE_WORDS[self.size].lower()} {name}: remove a widget first."

    def fill(self):
        lay = self.n.grid.layout_
        used = I.in_use(lay)
        for k, b in self.app_rows.items():
            b.set_on(k == self.pick, dot=k in used)
        on = I.which_preset(lay)
        for k, b in self.layout_rows.items():
            b.set_on(k == on)
        for s, b in self.size_btns.items():
            b.set_lit(s == self.size)
        word, ok, why = self.verdict()
        self.add.setText(word)
        self.add.setAccessibleName({"Add": "Add it to Home", "Resize": "Give it this size on Home"}.get(word, "Already on Home at this size"))
        self.add.setEnabled(ok)
        self.why.setText(why)
        self.why.setVisible(bool(why))
        self._preview()

    def _preview(self):
        """The pick as it will look, at its size, live (the clock ticks, the music is what plays)."""
        want = (self.pick, self.size)
        cols = self.n.grid.dims[0]
        cell = self.n.grid.cell_w() if self.n.grid.width() > 200 else (OPEN_W - 2 * PAD - (cols - 1) * GAP) / cols  # (Home's own cell)
        self.holder.cell, self.holder.cell_h = cell, self.n.grid.cell_h
        cw, ch = I.SIZES[self.size]
        w, h = cw * cell + (cw - 1) * GAP, ch * self.n.grid.cell_h + (ch - 1) * GAP
        self.holder.setFixedHeight(int(h))
        if self._preview_for != want or self.preview is None:
            if self.preview is not None:
                self.preview.hide()
                self.preview.deleteLater()
            self.preview = make_tile(self.holder, self.pick, self.size)
            self.preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.preview.setAccessibleName(f"Preview: {I.WIDGETS[self.pick][0]}, {I.SIZE_WORDS[self.size]}")
            self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._preview_for = want
            self.preview.show()
            self.n.tiles_changed()
        self.preview.setGeometry(int(max(0, (self.holder.width() - w) / 2)), 0, int(w), int(h))  # (the recording's: in the middle under the
        #                                                                                           sizes and Add)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.preview is not None:
            self._preview()

    def tick(self):
        if self.preview is not None:
            self.preview.tick()

    def add_it(self):
        new = I.add(self.n.grid.layout_, self.pick, self.size, at=self.at, dims=self.n.grid.dims)
        if new is None:
            return self.fill()
        self.at = None
        self.n.show_view("home")
        self.n.grid.edit(True)
        self.n.grid.change(new)

    def apply(self, key):
        """A ready-made layout: on Home at once, arranging (the recording: the tiles fly to their places, Done to keep them)."""
        self.n.show_view("home")
        self.n.grid.edit(True)
        self.n.grid.change(I.preset(key))

    def set_look(self, island):
        """The floating island's Home is one row (island.ISLAND_GRID): no ready-made layouts (they are the classic grid's) and no Big."""
        self.island = island
        self._preview_for = None  # (its preview made again in this look's own cells)
        for b in self.layout_rows.values():
            b.setVisible(not island)
        self.size_btns["big"].setVisible(not island)
        self.words.setText("Pick an app and a size: one row of tiles" if island else "Pick an app and a size, or a ready-made layout")
        if island and self.size == "big":
            self.size = "small"

    def height_for(self):
        rows = math.ceil(len(I.MENU) / 2)  # the apps' two columns (six rows since the 2026-10-26 widgets)
        presets = 0 if getattr(self, "island", False) else 34 + 8
        return 26 + 8 + presets + max(rows * 28 + (rows - 1) * 5, 24 + 10 + (14 if self.why.isVisible() else 0) + self.holder.height()) + 4


# -- Note and Clipboard --------------------------------------------------------------------------------------------------------------------
class NoteBox(QPlainTextEdit):
    clicked = Signal()
    escape = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            return self.escape.emit()
        super().keyPressEvent(e)


class NoteView(QWidget):
    """The Note widget opened: one note, kept in data/desktop.json as it is written (NOTE_SAVE_MS after the last key); Done back to Home."""

    def __init__(self, notch):
        super().__init__()
        self.n = notch
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(AppIcon("note", 22))
        head.addWidget(label("Note", 14, 750))
        self.kept = label("", 10.5, 400, TEXT_3)
        head.addWidget(self.kept, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        done = Lime("Done", "Done: back to Home", height=24, width=62)
        done.clicked.connect(lambda: self.n.show_view("home"))
        head.addWidget(done)
        v.addLayout(head)
        self.box = NoteBox()
        self.box.setPlaceholderText("Write it down…")
        self.box.setAccessibleName("Your note")
        self.box.setStyleSheet(f"QPlainTextEdit {{ background: {TILE_BG}; border: 1px solid #22272A; border-radius: 12px; padding: 8px 10px; "
                               f"color: {TEXT}; selection-background-color: #3B4A2A; }}")
        self.box.setFont(font(12.5))
        self.box.clicked.connect(lambda: self.n.take_keys(self.box))
        self.box.escape.connect(lambda: self.n.show_view("home"))
        self.box.textChanged.connect(self._typed)
        v.addWidget(self.box, 1)
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(NOTE_SAVE_MS)
        self._save.timeout.connect(self.keep)

    def load(self):
        text = self.n.island.get("note") or ""
        if self.box.toPlainText() != text:
            self.box.blockSignals(True)
            self.box.setPlainText(text)
            self.box.blockSignals(False)
        self.kept.setText("Kept on this PC" if text else "")

    def _typed(self):
        self.kept.setText("…")
        self.n.note_typed(self.box.toPlainText())  # (its tile shows it as it is written; kept NOTE_SAVE_MS after the last key)
        self._save.start()

    def keep(self):
        self._save.stop()
        self.n.keep_note(self.box.toPlainText())
        self.kept.setText("Kept on this PC")


class ClipRow(K._Keyed, QAbstractButton):
    """One copy: its first line and how long ago; a click copies it back."""

    def __init__(self, item):
        super().__init__()
        self.item = item
        line = I.clip_line(item["text"], 200)
        self.setText(line)
        self.setAccessibleName(f"Copy again: {line}")
        self.setToolTip(item["text"][:400])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(CLIP_ROW_H)
        self._hover = K.Hover(self)

    def enterEvent(self, e):
        self._hover.go(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.go(False)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(ROW, ROW_HOVER, self._hover.value))
        p.drawRoundedRect(r, 9, 9)
        when = I.ago(self.item["at"])
        p.setFont(font(10.5))
        ww = p.fontMetrics().horizontalAdvance(when) + 10
        p.setPen(QColor(TEXT_3))
        p.drawText(QRectF(r.right() - ww - 30, r.top(), ww, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, when)
        K.paint_icon(p, "copy", QRectF(r.right() - 22, r.center().y() - 7, 14, 14), K.ICON if not self._hover.value else TEXT, 2.0)
        p.setFont(font(12))
        p.setPen(QColor(TEXT))
        room = r.width() - ww - 52
        p.drawText(QRectF(r.left() + 10, r.top(), room, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(room)))
        self._ring(p, r, 9)
        p.end()


class ClipView(QWidget):
    """The Clipboard widget opened: every copy since the PC started (kept until it shuts down; never one a password manager marks private),
    newest first; a click copies one back; Forget them clears the list."""

    def __init__(self, notch):
        super().__init__()
        self.n = notch
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(AppIcon("clipboard", 22))
        head.addWidget(label("Clipboard", 14, 750))
        self.said = label("", 10.5, 400, TEXT_3)
        head.addWidget(self.said, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.forget = Button("Forget them", "quiet", height=24, name="Forget the copies listed here", size=11)
        self.forget.clicked.connect(self.n.clips_forget)
        head.addWidget(self.forget)
        done = Lime("Done", "Done: back to Home", height=24, width=62)
        done.clicked.connect(lambda: self.n.show_view("home"))
        head.addWidget(done)
        v.addLayout(head)
        self.list = QVBoxLayout()
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(5)
        box = QWidget()
        box.setLayout(self.list)
        self.scroll = QScrollArea()
        self.scroll.setWidget(box)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("Your copies since the PC started")
        v.addWidget(self.scroll, 1)
        self.empty = label("Nothing copied yet. Everything you copy shows here until the PC shuts down; a copy a password manager marks "
                           "private never does.",
                           11.5, 400, TEXT_2, wrap=True)
        v.addWidget(self.empty)

    def load(self):
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for it in self.n.clips:
            row = ClipRow(it)
            row.clicked.connect(lambda _=False, t=it["text"]: self.n.copy_back(t))
            self.list.addWidget(row)
        self.list.addStretch(1)
        has = bool(self.n.clips)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        self.forget.setVisible(has)

    def height_for(self):
        """Its rows (five at most, the rest scroll), else the line saying there are none."""
        return 26 + 8 + (min(len(self.n.clips), 5) * (CLIP_ROW_H + 5) + 2 if self.n.clips else 40) + 8


# -- Windows' media sessions, for the music tile -----------------------------------------------------------------------------------------------
class MediaWatch(QObject):
    """What plays on this PC, read on a thread (core/media.py: Windows' media sessions, WinRT) every MEDIA_EVERY_MS while a music tile
    shows; its controls checked against what Windows shows after them (media.control). `ok` None until the first read, False when the
    sessions can't be read here (another OS, no WinRT): the tile says so."""
    got = Signal(object)
    done_control = Signal(object)

    def __init__(self):
        super().__init__()
        self.ok, self.now, self.cover, self.busy = None, None, None, False
        self.pos = None             # (seconds in, length, when Windows said it, playing) or None when the player doesn't say
        self.said, self.said_at = None, 0.0
        self._cover_for, self._reading = None, False
        self.got.connect(self._got)
        self.done_control.connect(self._controlled)
        self._t = QTimer(self)
        self._t.setInterval(MEDIA_EVERY_MS)
        self._t.timeout.connect(lambda: self.read())  # (looked up each time: a check's stand-in reader is the one called)
        self.on_change = lambda: None

    def watch(self, on):
        if on and not self._t.isActive():
            self._t.start()
            self.read()
        elif not on:
            self._t.stop()

    def read(self):
        if self._reading:
            return
        self._reading = True

        def go():
            r = self._run(self._read(self._cover_for))
            try:
                self.got.emit(r)
            except RuntimeError:  # its tile went while Windows answered (a layout replaced it, or Monday quit)
                pass
        threading.Thread(target=go, daemon=True).start()

    @staticmethod
    def _run(coro):
        try:
            return asyncio.run(coro)
        except Exception as e:  # (no WinRT here, or Windows refused the read)
            return {"ok": False, "why": repr(e)[:200]}

    @staticmethod
    async def _read(cover_for):
        if sys.platform != "win32":
            return {"ok": False}
        from . import media
        if not media.available():
            return {"ok": False}
        found = await media.sessions()
        x = media.pick(found)
        if not x:
            return {"ok": True, "now": None}
        out = {"ok": True, "now": media.view(x)}
        s = x["s"]
        try:
            tl = s.get_timeline_properties()
            end = tl.end_time.total_seconds()
            if end > 0:
                out["pos"] = (tl.position.total_seconds(), end, tl.last_updated_time.timestamp())
        except Exception:
            pass
        key = (x["app"], x["title"], x["artist"])
        if key != cover_for:
            out["cover_for"], out["cover"] = key, None
            try:
                from winrt.windows.storage.streams import Buffer, InputStreamOptions
                p = await s.try_get_media_properties_async()
                if p.thumbnail is not None:
                    stream = await p.thumbnail.open_read_async()
                    n = int(stream.size)
                    if 0 < n <= 8 * 1024 * 1024:
                        buf = await stream.read_async(Buffer(n), n, InputStreamOptions.READ_AHEAD)
                        out["cover"] = bytes(buf)
            except Exception:
                pass
        return out

    def _got(self, d):
        self._reading = False
        self.ok = bool(d.get("ok"))
        self.now = d.get("now") if self.ok else None
        if "cover_for" in d:
            self._cover_for = d["cover_for"]
            img = QImage.fromData(d["cover"]) if d.get("cover") else QImage()
            self.cover = QPixmap.fromImage(img) if not img.isNull() else None
        if not self.now:
            self.cover, self._cover_for = None, None
        p = d.get("pos")
        self.pos = (p[0], p[1], p[2], (self.now or {}).get("status") == "playing") if p and self.now else None
        self.on_change()

    def part(self):
        """How far into the track, 0..1, going on from when Windows last said while it plays; None when the player doesn't say."""
        if not self.pos:
            return None
        at, end, said, playing = self.pos
        now = at + (max(0.0, time.time() - said) if playing else 0.0)
        return max(0.0, min(1.0, now / end)) if end > 0 else None

    def control(self, op):
        """The owner's click: play, pause, toggle, next or previous, checked against Windows afterwards (media.control); what didn't
        happen is said on the tile."""
        if self.busy:
            return
        self.busy = True
        self.on_change()

        def go():
            try:
                from . import media
                r = asyncio.run(media.control(op))
            except Exception as e:
                r = {"ok": False, "text": f"Couldn't reach Windows' media controls ({e!r})"[:160]}
            try:
                self.done_control.emit(r)
            except RuntimeError:  # its tile went while Windows answered (a layout replaced it, or Monday quit)
                pass
        threading.Thread(target=go, daemon=True).start()

    def _controlled(self, r):
        self.busy = False
        r = r or {"ok": False, "text": "Windows' media sessions can't be read here."}
        self.said, self.said_at = (None, 0.0) if r.get("ok") and r.get("verified") is not False else (r.get("text"), time.monotonic())
        self.on_change()
        self._reading = False
        self.read()
