"""The notch's two new looks in Qt (the owner's brief of 2026-10-29 and its four recordings; docs/adr/0011; core/island.py MODES): what
the floating island and the AI-limits notch draw that the classic notch doesn't. The notch (core/qtnotch.py) stays one window with one
state, one chat, one set of approvals: a look changes its outline, its closed face, its header and its Home, nothing else.

- the eyes: the floating island's face (the TwoCode recordings' "Robo"): the owner's pet's eyes alone, its blink, its moods, tinted by
  Monday's state (working orange, waiting amber, done green, failed red)
- the island's closed pill: the eyes; with something running, what it is and for how long; beside it a bubble for a running focus timer
  and one for music ("Three things at once")
- the island's header over its views (the page, the eyes in the state's colour, the date and time), its tabs going to the bottom
- each tile's glow ("Home got a glow-up"): its colour rising from the bottom while it has something to show
- the limits notch: its closed bar (the busiest real limit's ring at the left, its % at the right; core/usage.py busiest) and Home's
  usage cards, one per AI, each from the source that AI supports (core/usage.py), never a number made up

Checked by `python -m core.qtnotch --check` (_check_looks).
"""
from __future__ import annotations

import math
import time
from datetime import datetime

from . import island as I
from . import qtkit as K
from . import usage as U
from .notch import OPEN_W, PAD, STATES
from .qtkit import TEXT, TEXT_2, TEXT_3, IconButton, font, mix
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

SHELL = "#000000"
EDGE = "#2A2B30"              # the island's edge when the owner dragged it off the top (hanging, it has none: the recording's)
EAR = 10                      # the recording's flared top corners where the island meets the screen's edge
TINT = {"working": "#F2A24C", "asked": "#F2A24C", "waiting": "#F2C94C", "alert": "#F2C94C", "done": "#5FD38D", "verified": "#5FD38D",
        "answer": "#5FD38D", "brief": "#5FD38D", "unverified": "#F2C94C", "failed": "#E5484D", "offline": "#7A7F87"}  # the eyes by state
HEAT = {"ok": "#E6E8EA", "high": "#F2A24C", "full": "#E5484D"}   # a limit's bar and %: white, then the recording's orange, then red
TRACK = "#2A2D31"
CARD_BG, BADGE_BG = "#17191B", "#2A2C30"
FOCUS_GLOW, BREAK_GLOW = "#7C4DDB", "#2FA58C"   # the focus tile's purple (the recording's), a break's teal
GLOW_AI = {"working": "#C2603A", "asked": "#C2603A", "waiting": "#C8862E", "alert": "#C8862E", "done": "#2C9A78", "verified": "#2C9A78",
           "answer": "#2C9A78", "failed": "#B5474B"}   # the AI tile's light by Monday's state (none while all is quiet)
LOGO_COLOURS = {"claude_code": "#D97757", "codex": "#E6E8EA", "openrouter": "#6E7CF0", "ollama": "#E6E8EA", "jev": "#9E83F2"}
VIEW_TITLES = {"home": ("", ""), "music": ("Music", "Player"), "focus": ("Focus", "Timer"), "chat": ("Monday", "Chat"), "drop": ("File", "Tray"), "settings": ("Island", "Settings"),
               "agent": ("Agent", "View"), "note": ("Quick", "Note"), "clip": ("Clip", "board"), "wardrobe": ("Pet", "Wardrobe")}
VIEW_TINTS = {"home": "#C4E59A", "music": "#5AA9FF", "focus": "#B48CF2", "chat": "#5FD38D", "drop": "#5AA9FF", "settings": "#9E83F2", "agent": "#F2A24C", "note": "#F2C94C",
              "clip": "#3FC1A4", "wardrobe": "#E26FA6"}   # the second word of the header, as the recording colours "Claude Code"
HEAD_H = 22                  # the island's header over its views
BUBBLE_GAP = 6               # between the pill and each bubble beside it
CARD_W = (OPEN_W - 2 * PAD - 8) / 2
ROW_H, CARD_TOP, CARD_FOOT = 44, 38, 10   # a limit's row (its name and %, the bar, its reset), a card's head, its bottom margin
LIMITS_MAX_H = 300           # the cards' area at most this tall (more scroll)
LIMITS_HEAD_H = 26


def tint(n):
    st = n.shown[0] if getattr(n, "shown", None) else "idle"
    return QColor(TINT.get(st, n.p.get("body", "#F3F4F7") if hasattr(n, "p") else "#F3F4F7"))


def paint_eyes(p, cx, cy, h, colour, mood, now, look=(0.0, 0.0), blinking=False):
    """Two eyes centred on (cx, cy), each `h` tall: upright ovals with a soft glow (the recording's), on the pointer; ^ ^ happy, ‿ ‿
    asleep, x x offline, a line blinking or flat."""
    ew, gap = h * 0.44, h * 1.15
    pen = QPen(colour, max(1.4, h * 0.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.save()
    for s in (-1, 1):
        x = cx + s * gap / 2
        if mood in ("happy", "eating"):
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x - ew * 0.9, cy - h * 0.2, ew * 1.8, h * 0.8), 20 * 16, 140 * 16)
        elif mood == "sleepy":
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x - ew * 0.9, cy - h * 0.55, ew * 1.8, h * 0.8), 200 * 16, 140 * 16)
        elif mood == "offline":
            p.setPen(pen)
            d = h * 0.24
            p.drawLine(QPointF(x - d, cy - d), QPointF(x + d, cy + d))
            p.drawLine(QPointF(x - d, cy + d), QPointF(x + d, cy - d))
        elif blinking or mood in ("flat", "sad"):
            p.setPen(pen)
            p.drawLine(QPointF(x - ew * 0.55, cy), QPointF(x + ew * 0.55, cy))
        else:
            dx, dy = look[0] * h / 14, look[1] * h / 18
            glow = QRadialGradient(QPointF(x + dx, cy + dy), h * 0.9)
            glow.setColorAt(0, K.alpha(colour, 0.32))
            glow.setColorAt(1, K.alpha(colour, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(x + dx, cy + dy), h * 0.9, h * 0.9)
            p.setBrush(colour)
            p.drawEllipse(QRectF(x + dx - ew / 2, cy + dy - h / 2, ew, h))
    p.restore()


def mood_of(n):
    """The eyes' mood: the pet's, by Monday's state (notch.STATES), sleepy while Core is still starting (the start: docs/adr/0011)."""
    if getattr(n, "booting", False):
        return "sleepy"
    return getattr(n, "mood", None) or "idle"  # (the notch's own: the state's, or the start's greeting)


# -- the floating island's closed pill and its bubbles ---------------------------------------------------------------------------------
def bubbles(n):
    """What gets a bubble beside the pill: a running focus timer (its ring), music playing (its cover). [(kind, value)]"""
    out = []
    f = I.focus_now(n.island.get("focus"))
    if f["phase"] == "running":
        total = I.KINDS.get(f["kind"], I.FOCUS_S)
        out.append(("focus", 1.0 - f["left"] / total if total else 0.0, f["kind"]))
    m = getattr(getattr(n, "media", None), "now", None)
    if m and m.get("status") == "playing":
        out.append(("music", getattr(n.media, "cover", None), m.get("title") or ""))
    return out


def bubbles_w(n, h):
    return sum(h + BUBBLE_GAP for _ in bubbles(n))


def elapsed(n, now=None):
    """How long Monday's newest running task has run ("0:42", "12:03", "1:02:09"), or "" when Core gave no start (never invented)."""
    tasks = [t for t in (getattr(n.state, "tasks", None) or {}).values() if isinstance(t, dict)]
    at = None
    for t in reversed(tasks):
        v = t.get("started_at") or t.get("created_at")
        try:
            at = float(v) if isinstance(v, (int, float)) else datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() if v else None
        except ValueError:
            at = None
        if at:
            break
    if not at:
        return ""
    s = max(0, int((time.time() if now is None else now) - at))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def pill_path(r):
    path = QPainterPath()
    path.addRoundedRect(r, r.height() / 2, r.height() / 2)
    return path


def ears_path(w, h, r, ear=EAR):
    """Hanging from the screen's top edge as the recording's island does: square along the edge, flared into it at both top corners
    (concave `ear`), round below (`r`)."""
    path = QPainterPath()
    r = max(0.0, min(r, (h - ear) / 2 if h > ear else 0.0, (w - 2 * ear) / 2))
    path.moveTo(0, 0)
    path.lineTo(w, 0)
    path.quadTo(w - ear, 0, w - ear, ear)
    path.lineTo(w - ear, h - r)
    path.quadTo(w - ear, h, w - ear - r, h)
    path.lineTo(ear + r, h)
    path.quadTo(ear, h, ear, h - r)
    path.lineTo(ear, ear)
    path.quadTo(ear, 0, 0, 0)
    path.closeSubpath()
    return path


def paint_island(n, p, w, h):
    """The island closed (the recording's): hanging from the top edge with its flared corners (a pill when the owner dragged it off the
    edge), Monday's own pet in its middle; with something going on, its words at the left and its time at the right; a bubble beside it
    for each of bubbles()."""
    bs = bubbles(n)
    pw = w - len(bs) * (h + BUBBLE_GAP)
    hang = n.hanging()
    path = ears_path(pw, h, h * 0.55) if hang else pill_path(QRectF(0.5, 0.5, pw - 1, h - 1))
    p.fillPath(path, QColor(SHELL))
    if not hang:
        p.setPen(QPen(QColor(EDGE), 1.0))
        p.drawPath(path)
    st, title, _ = n.shown
    now = n.clock()
    colour = tint(n)
    busy = st not in ("idle", "listening", "asleep") and not getattr(n, "booting", False)
    x0, x1 = (EAR, pw - EAR) if hang else (0.0, pw)  # (the body between the flared corners)
    k = h - 6
    if busy:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawEllipse(QPointF(x0 + 16, h / 2), 4, 4)
        p.setPen(QColor(TEXT))
        p.setFont(font(13, 600))
        right = elapsed(n)
        mid = (x0 + x1) / 2
        room = mid - k / 2 - 8 - (x0 + 26)
        p.drawText(QRectF(x0 + 26, 0, room, h), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(room)))
        n.mascot().draw(p, QRectF(mid - k / 2, 2, k, k), now, n.mood)
        if right:
            p.setPen(QColor(TEXT_2))
            p.setFont(font(13, 500))
            p.drawText(QRectF(mid + k / 2 + 8, 0, x1 - 14 - (mid + k / 2 + 8), h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       right)
    else:
        n.mascot().draw(p, QRectF((x0 + x1) / 2 - k / 2, 2, k, k), now, n.mood)  # (Monday's own pet: never another app's mascot)
    for i, (kind, val, extra) in enumerate(bs):
        r = QRectF(pw + BUBBLE_GAP + i * (h + BUBBLE_GAP) + 0.5, 0.5, h - 1, h - 1)
        p.setPen(Qt.PenStyle.NoPen if hang else QPen(QColor(EDGE), 1.0))
        p.setBrush(QColor(SHELL))
        p.drawEllipse(r)
        if kind == "focus":  # the recording's purple ring, filling as the pomodoro goes (teal in a break)
            ring = r.adjusted(h * 0.22, h * 0.22, -h * 0.22, -h * 0.22)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(TRACK), 3))
            p.drawEllipse(ring)
            p.setPen(QPen(QColor(FOCUS_GLOW if extra == "focus" else BREAK_GLOW), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, -int(360 * 16 * max(0.02, min(1.0, val))))
        else:  # the music's cover in a circle, or its colour with a note when the player gives none
            inner = r.adjusted(4, 4, -4, -4)
            clip = QPainterPath()
            clip.addEllipse(inner)
            p.save()
            p.setClipPath(clip)
            if val is not None and not val.isNull():
                p.drawPixmap(inner.toRect(), val)
            else:
                p.fillRect(inner, QColor(I.WIDGETS["music"][1]))
                p.setPen(QColor(TEXT))
                p.setFont(font(13, 700))
                p.drawText(inner, Qt.AlignmentFlag.AlignCenter, "♪")
            p.restore()


# -- the island's header (its tabs go under its views) ----------------------------------------------------------------------------------
class IslandHead(QWidget):
    """The recording's header: the page in two words (the second in its colour), two dots in Monday's state's colour, the date and the
    time (its minutes bold) as this PC's clock writes them, and at the right the music's bars while something plays (its cover's colour)
    and ⋯ (the notch's menu)."""

    def __init__(self, notch):
        super().__init__()
        self.n = notch
        self.setFixedHeight(HEAD_H)
        self.setMouseTracking(True)
        self.setAccessibleName("The island's header: its page, the date and time; ⋯ opens the notch's menu")

    def dots_rect(self):
        return QRectF(self.width() - 30, 0, 28, self.height())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.dots_rect().contains(e.position()):
            return self.n._context(e.globalPosition().toPoint())
        e.ignore()  # (anywhere else: the notch's own, a drag moves it)

    def words(self, now=None):
        d = now or datetime.now()
        from .qtisland import h24
        hm = d.strftime("%H:%M") if h24() else d.strftime("%I:%M %p").lstrip("0")
        return f"{I.WEEKDAYS[d.weekday()]} {d.day} {I.MONTHS[d.month - 1]} {d.year}", hm

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        a, b = VIEW_TITLES.get(self.n.view, ("Monday", ""))
        f = font(13, 700)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        aw = QFontMetricsF(f).horizontalAdvance(a + " ")
        p.drawText(QRectF(6, 0, aw + 2, h), Qt.AlignmentFlag.AlignVCenter, a + " ")
        p.setPen(QColor(VIEW_TINTS.get(self.n.view, TEXT)))
        p.drawText(QRectF(6 + aw, 0, 200, h), Qt.AlignmentFlag.AlignVCenter, b)
        day, hm = self.words()
        fd, ft = font(12, 400), font(12, 700)
        dw, tw = QFontMetricsF(fd).horizontalAdvance(day + " "), QFontMetricsF(ft).horizontalAdvance(hm)
        x0 = self.width() / 2 - (dw + tw) / 2 + 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tint(self.n))
        for dx in (-22, -13):  # (Monday's state, as two small dots)
            p.drawEllipse(QPointF(x0 + dx, h / 2), 2.6, 3.4)
        p.setFont(fd)
        p.setPen(QColor(TEXT_2))
        p.drawText(QRectF(x0, 0, dw, h), Qt.AlignmentFlag.AlignVCenter, day + " ")
        p.setFont(ft)
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(x0 + dw, 0, tw + 2, h), Qt.AlignmentFlag.AlignVCenter, hm)
        d = self.dots_rect()  # ⋯
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TEXT))
        for i in range(3):
            p.drawEllipse(QPointF(d.center().x() - 6 + i * 6, h / 2), 1.8, 1.8)
        m = getattr(getattr(self.n, "media", None), "now", None)
        if m and m.get("status") == "playing":  # the music's bars, moving, in its cover's colour (the recording's)
            col = cover_tint(getattr(self.n.media, "cover", None)) or QColor(I.WIDGETS["music"][1])
            p.setBrush(col)
            t = self.n.clock()
            for i in range(4):
                bh = 5 + 7 * abs(math.sin(t * 3.1 + i * 1.3))
                p.drawRoundedRect(QRectF(d.left() - 26 + i * 5, h / 2 - bh / 2, 3, bh), 1.2, 1.2)
        p.end()


# -- the tiles' glow ---------------------------------------------------------------------------------------------------------------------
_COVER_TINT = {}


def cover_tint(pix):
    """A cover's own colour (its average, a little brighter), once per picture."""
    if pix is None or pix.isNull():
        return None
    k = pix.cacheKey()
    if k not in _COVER_TINT:
        c = pix.toImage().scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
        _COVER_TINT[k] = mix(c, "#ffffff", 0.12)
    return _COVER_TINT[k]


def glow(tile):
    """The light a tile rises in, on the floating island (the recording's): the AI tile in Monday's state's colour while something
    happens; the music in its cover's colour while it plays; the focus timer purple (teal in a break), brighter while it runs. None:
    dark, as the classic tile."""
    n = tile.n
    if getattr(n, "mode", "classic") != "island":
        return None
    if tile.key == "ai":
        c = GLOW_AI.get(n.shown[0]) if getattr(n, "shown", None) else None
        return (QColor(c), 1.0) if c else None
    if tile.key == "music":
        m = getattr(getattr(n, "media", None), "now", None)
        if m and m.get("status") == "playing":
            return cover_tint(getattr(n.media, "cover", None)) or QColor(I.WIDGETS["music"][1]), 1.0
        return None
    if tile.key == "focus":
        f = I.focus_now(n.island.get("focus"))
        return QColor(FOCUS_GLOW if f["kind"] == "focus" else BREAK_GLOW), (1.0 if f["phase"] == "running" else 0.55)
    return None


def paint_glow(p, r, radius, g):
    colour, k = g
    grad = QLinearGradient(r.topLeft(), r.bottomLeft())
    grad.setColorAt(0.0, K.alpha(colour, 0.0))
    grad.setColorAt(0.45, K.alpha(colour, 0.10 * k))
    grad.setColorAt(1.0, K.alpha(colour, 0.62 * k))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(r, radius, radius)


# -- the limits notch: its closed bar --------------------------------------------------------------------------------------------------
def heat_colour(pct):
    return QColor(HEAT.get(U.heat(pct) or "ok"))


def paint_logo(n, p, r, key):
    """An AI's own logo (its program's icon, the owner's choice of 2026-10-26: "Keep app logos"), else its initial on its colour."""
    pal = logo_of(n, key)
    if pal is not None:
        pal.draw(p, r, 1.0, "idle")
        return
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(LOGO_COLOURS.get(key, "#9AA0A6")))
    p.drawEllipse(r)
    p.setPen(QColor("#111214"))
    p.setFont(font(r.height() * 0.55, 700))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, U.NAMES.get(key, key)[:1])
    p.restore()


_EXES = {}


def logo_of(n, key):
    """The program behind a provider (Claude Code, Codex, Ollama), found once; its icon through the notch's own logo reader."""
    if key not in _EXES:
        from . import probes
        finder = {"claude_code": probes.claude_exe, "codex": probes.codex_exe, "ollama": probes.ollama_exe}.get(key)
        try:
            _EXES[key] = finder() if finder else None
        except Exception:
            _EXES[key] = None
    return n._logo(_EXES[key]) if _EXES[key] and hasattr(n, "_logo") else None


def paint_limits(n, p, w, h):
    """The limits notch closed (the recording's "A nudge before you run out"): the busiest real limit's logo in its ring at the left, its
    % at the right, orange from usage.HIGH, red from usage.FULL. -> False when no card has a % (the classic face is drawn instead)."""
    b = U.busiest(getattr(n, "usage", None) or [])
    if not b:
        return False
    card, win, pct = b
    colour = heat_colour(pct)
    d = h - 8
    ring = QRectF(14, 4, d, d)
    p.setPen(QPen(QColor(TRACK), 2.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(ring)
    p.setPen(QPen(colour, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(ring, 90 * 16, -int(360 * 16 * max(0.02, pct / 100)))
    paint_logo(n, p, ring.adjusted(d * 0.24, d * 0.24, -d * 0.24, -d * 0.24), card["key"])
    p.setPen(colour)
    f = font(13.5, 700)
    p.setFont(f)
    pw = QFontMetricsF(f).horizontalAdvance(f"{pct}%")
    p.drawText(QRectF(0, 0, w - 16, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{pct}%")
    right = w - 16 - pw - 8
    if card["status"] == "stale":  # an old reading says so even closed: a faint "last known" by the %
        p.setPen(QColor(TEXT_3))
        p.setFont(font(10.5, 500))
        lw = QFontMetricsF(p.font()).horizontalAdvance("last known")
        p.drawText(QRectF(right - lw, 0, lw + 2, h), Qt.AlignmentFlag.AlignVCenter, "last known")
        right -= lw + 8
    st, title, _ = n.shown
    if st not in ("idle", "listening", "asleep"):  # Monday's own state between the two ends: what it does stays in sight
        left = ring.right() + 12
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(K.Dot.COLORS.get({"waiting": "wait", "working": "wait", "asked": "wait", "failed": "bad", "offline": "off",
                                            "alert": "wait"}.get(st, "ok"), K.GREEN)))
        p.drawEllipse(QPointF(left + 4, h / 2), 4, 4)
        p.setPen(QColor(TEXT))
        p.setFont(font(12.5, 600))
        p.drawText(QRectF(left + 14, 0, max(0.0, right - left - 14), h), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(max(0.0, right - left - 14))))
    return True


# -- the limits notch: Home's cards ----------------------------------------------------------------------------------------------------
def card_height(c, expanded=False):
    rows = len(c["windows"])
    body = rows * ROW_H if rows else 58  # (no number: its note, up to three lines)
    extra = (18 if c.get("note") and rows else 0) + (36 if expanded else 0)
    return int(CARD_TOP + body + extra + CARD_FOOT)


class UsageCard(QFrame):
    """One AI's limits (the recording's card): its logo, name, plan and how old the reading is; each window's name, %, bar and reset; or,
    with no number to give, what its source is and why there is none. A click opens its details (the source, when it was read), a right
    click moves or hides it (Mode 3's own layout)."""
    toggled = Signal(str)
    menu = Signal(str, object)

    def __init__(self, notch, c, expanded=False):
        super().__init__()
        self.n, self.c, self.expanded = notch, c, expanded
        self.setFixedHeight(card_height(c, expanded))
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(self.spoken())

    def spoken(self, now=None):
        c = self.c
        parts = [c["name"], c.get("plan") or "", U.STATUS[c["status"]][0]]
        for w in c["windows"]:
            parts.append(f"{w['name']}: {U.value_words(w)}, {U.reset_words(w.get('resets_at'), now)}")
        if c.get("note"):
            parts.append(c["note"])
        if c.get("at"):
            parts.append(f"read {U.age_words(c, now)}")
        return ". ".join(x for x in parts if x)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit(self.c["key"])
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu.emit(self.c["key"], e.globalPosition().toPoint())

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            return self.toggled.emit(self.c["key"])
        super().keyPressEvent(e)

    def paintEvent(self, _e):
        from .qtisland import h24
        c, now = self.c, time.time()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CARD_BG))
        p.drawRoundedRect(r, 14, 14)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(K.FOCUS), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 13, 13)
        w = self.width()
        paint_logo(self.n, p, QRectF(12, 11, 18, 18), c["key"])
        f = font(13.5, 700)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        nw = QFontMetricsF(f).horizontalAdvance(c["name"])
        p.drawText(QRectF(38, 8, nw + 2, 24), Qt.AlignmentFlag.AlignVCenter, c["name"])
        x = 38 + nw + 8
        if c.get("plan"):
            fb = font(10.5, 600)
            bw = QFontMetricsF(fb).horizontalAdvance(c["plan"]) + 12
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(BADGE_BG))
            p.drawRoundedRect(QRectF(x, 12, bw, 16), 8, 8)
            p.setPen(QColor(TEXT_2))
            p.setFont(fb)
            p.drawText(QRectF(x, 12, bw, 16), Qt.AlignmentFlag.AlignCenter, c["plan"])
        # the top right: how old the reading is; anything but "up to date" in its own words and colour
        dot = U.STATUS[c["status"]][1]
        right = U.age_words(c, now) if c["status"] in ("ok",) and c.get("at") else U.STATUS[c["status"]][0] + \
            (f" · {U.age_words(c, now)}" if c["status"] == "stale" and c.get("at") else "")
        p.setFont(font(11, 500))
        p.setPen(QColor({"ok": TEXT_3, "wait": K.AMBER, "bad": K.RED, "off": TEXT_3}[dot]))
        p.drawText(QRectF(0, 8, w - 12, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right)
        y = CARD_TOP
        for win in c["windows"]:
            pct = U.percent(win)
            p.setFont(font(12, 500))
            p.setPen(QColor(TEXT_2))
            p.drawText(QRectF(12, y, w - 24, 16), Qt.AlignmentFlag.AlignVCenter, win["name"])
            p.setFont(font(12.5, 700))
            p.setPen(heat_colour(pct) if pct is not None else QColor(TEXT_2))
            p.drawText(QRectF(12, y, w - 24, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       f"{pct}%" if pct is not None and not win.get("count") else U.value_words(win))
            bar = QRectF(12, y + 19, w - 24, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TRACK))
            p.drawRoundedRect(bar, 2, 2)
            if pct is not None:
                p.setBrush(heat_colour(pct))
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4.0, bar.width() * pct / 100), bar.height()), 2, 2)
            p.setFont(font(11, 400))
            p.setPen(QColor(TEXT_3))
            p.drawText(QRectF(12, y + 25, w - 24, 16), Qt.AlignmentFlag.AlignVCenter, U.reset_words(win.get("resets_at"), now, h24()))
            y += ROW_H
        note = c.get("note") or ""
        if not c["windows"]:  # no number to give: what the source is, and why
            p.setFont(font(11.5, 400))
            p.setPen(QColor(TEXT_2))
            p.drawText(QRectF(12, y - 2, w - 24, 56), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, note or U.STATUS[c["status"]][0])
            y += 58
        elif note:
            p.setFont(font(11, 400))
            p.setPen(QColor(TEXT_3))
            p.drawText(QRectF(12, y - 2, w - 24, 16), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, int(w - 24)))
            y += 18
        if self.expanded:
            p.setFont(font(11, 400))
            p.setPen(QColor(TEXT_3))
            read = f"asked {U.clock(c['read_at'], h24())}" if c.get("read_at") else ""
            said = f" · it said so {U.age_words(c, now)}" if c.get("at") and c["status"] != "ok" else ""
            p.drawText(QRectF(12, y, w - 24, 16), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(f"Source: {c.get('source') or 'none'}", Qt.TextElideMode.ElideRight, int(w - 24)))
            p.drawText(QRectF(12, y + 16, w - 24, 16), Qt.AlignmentFlag.AlignVCenter, f"{U.STATUS[c['status']][0]} · {read}{said}")
        p.end()


class LimitsView(QWidget):
    """Home of the limits notch: a head (when they were read, Refresh) and a card per AI, two to a row (more scroll); the same cards the
    notch reads for every look (Notch.usage). The owner's order and hidden cards are Mode 3's own layout (island.ui "limits")."""

    def __init__(self, notch):
        super().__init__()
        self.n, self.expanded, self._drawn = notch, set(), None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 0, 0)
        head.setSpacing(8)
        self.title = K.label("AI limits", 13, 700)
        head.addWidget(self.title)
        self.when = K.label("", 11.5, 400, TEXT_3)
        head.addWidget(self.when)
        head.addStretch(1)
        self.hidden_btn = K.label("", 11.5, 500, TEXT_2)
        self.hidden_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hidden_btn.mousePressEvent = lambda e: self._hidden_menu(e.globalPosition().toPoint())
        head.addWidget(self.hidden_btn)
        self.refresh_btn = IconButton("refresh", "Read every AI's limits again now", size=26)
        self.refresh_btn.clicked.connect(lambda: self.n.read_usage(force=True))
        head.addWidget(self.refresh_btn)
        v.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("Your AIs' limits")
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.scroll.setWidget(self.inner)
        v.addWidget(self.scroll, 1)
        self.empty = QLabel("")
        self.empty.setFont(font(12, 400))
        self.empty.setStyleSheet(f"color: {TEXT_2}; background: transparent;")
        self.empty.setWordWrap(True)
        v.addWidget(self.empty)

    def cards(self):
        return U.arrange(getattr(self.n, "usage", None) or [], self.n.ui["limits"])

    def rows_h(self):
        cs = self.cards()
        hs = [max(card_height(a, a["key"] in self.expanded), card_height(b, b["key"] in self.expanded) if b else 0)
              for a, b in ((cs[i], cs[i + 1] if i + 1 < len(cs) else None) for i in range(0, len(cs), 2))]
        return sum(hs) + 8 * max(0, len(hs) - 1)

    def height_for(self):
        return LIMITS_HEAD_H + 8 + (min(LIMITS_MAX_H, self.rows_h()) if self.cards() else 40)

    def fill(self):
        cs = self.cards()
        reading = getattr(self.n, "usage", None) is None
        hidden = [k for k in self.n.ui["limits"]["hidden"] if any(c["key"] == k for c in getattr(self.n, "usage", None) or [])]
        self.hidden_btn.setText(f"{len(hidden)} hidden ▾" if hidden else "")
        self.hidden_btn.setAccessibleName(f"{len(hidden)} hidden: click to show one" if hidden else "")
        read_at = max((c.get("read_at") or 0 for c in cs), default=0)
        from .qtisland import h24
        self.when.setText("Reading…" if reading else f"read {U.clock(read_at, h24())}" if read_at else "")
        key = (tuple((c["key"], c["status"], c.get("at"), c.get("read_at"), str(c["windows"]), c.get("note")) for c in cs),
               tuple(sorted(self.expanded)))
        self.empty.setVisible(not cs)
        self.empty.setText("Reading your AIs' limits…" if reading else "No AI of yours is set up for Monday to read: add a key in "
                           "Settings → Integrations, or sign in to Claude Code or Codex.")
        if key == self._drawn:
            for card in self.inner.findChildren(UsageCard):
                card.update()  # (the ages and resets move on)
            return
        self._drawn = key
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():  # (hidden now: deleteLater frees it only after this turn of the event loop)
                it.widget().hide()
                it.widget().deleteLater()
        for i, c in enumerate(cs):
            card = UsageCard(self.n, c, c["key"] in self.expanded)
            card.toggled.connect(self._toggle)
            card.menu.connect(self._card_menu)
            self.grid.addWidget(card, i // 2, i % 2, Qt.AlignmentFlag.AlignTop)
            card.show()  # (a layout shows a new child only on the next turn of the event loop)
        self.grid.setRowStretch(max(1, (len(cs) + 1) // 2), 1)
        self.scroll.setFixedHeight(min(LIMITS_MAX_H, self.rows_h()) if cs else 0)

    def _toggle(self, key):
        self.expanded ^= {key}
        self._drawn = None
        self.fill()
        self.n.limits_resized()

    def _card_menu(self, key, at):
        """A card's right click: move it earlier or later, or hide it (Mode 3's own layout, kept in data/desktop.json)."""
        keys = [c["key"] for c in self.cards()]
        m = QMenu(self)
        i = keys.index(key) if key in keys else -1
        if i > 0:
            m.addAction("Move earlier", lambda: self.n.limits_layout(move=(key, -1)))
        if 0 <= i < len(keys) - 1:
            m.addAction("Move later", lambda: self.n.limits_layout(move=(key, 1)))
        m.addAction(f"Hide {U.NAMES.get(key, key)}", lambda: self.n.limits_layout(hide=key))
        self.n.menu_open = True
        m.exec(at)
        self.n.menu_open = False

    def _hidden_menu(self, at):
        m = QMenu(self)
        for k in self.n.ui["limits"]["hidden"]:
            m.addAction(f"Show {U.NAMES.get(k, k)}", lambda k_=k: self.n.limits_layout(show=k_))
        if not m.isEmpty():
            self.n.menu_open = True
            m.exec(at)
            self.n.menu_open = False


# -- the island's own tabs and pages (the fidelity pass, 2026-10-29: "the ui doesnt match ... make it exactly same") ---------------------
ISLAND_TABS = (("home", "home", "Home"), ("ai", "bot", "AI"), ("music", "music", "Music"), ("chat", "chat", "Chat"),
               ("focus", "timer", "Focus"), ("drop", "inbox", "Tray"), ("clip", "clipboard", "Clipboard"), ("note", "note", "Note"))
TAB_TINTS = {"home": "#C4E59A", "ai": "#F2A24C", "music": "#5AA9FF", "chat": "#5FD38D", "focus": "#B48CF2", "drop": "#5AA9FF",
             "clip": "#3FC1A4", "note": "#F2C94C"}
LIMITS_TABS = (("home", "stats", "AI limits"), ("music", "music", "Music"), ("chat", "chat", "Chat"), ("focus", "timer", "Focus"),
               ("drop", "inbox", "Tray"), ("clip", "clipboard", "Clipboard"), ("note", "note", "Note"))  # the limits look's top row
CHIPS = (("explain", "Explain", "Explain this, briefly:"), ("spell", "Check spelling", "Check the spelling and grammar of this and give it back "
         "corrected:"), ("translate", "Translate", "Translate this into English:"), ("summarize", "Summarize", "Summarize this:"))  # the island's Chat
TAB_OF_VIEW = {"home": "home", "agent": "ai", "music": "music", "chat": "chat", "focus": "focus", "drop": "drop", "clip": "clip",
               "note": "note"}
TABS_H = 30
TAB_W, TAB_GAP = 34, 4
PAGE_H = 150      # the island's Music and Focus pages


def mmss(s):
    s = max(0, int(round(s)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class IslandTabs(QWidget):
    """The island's tabs under its views (the recording's): each page an icon, the one showing a pill with its name in its colour; the
    sound switch at the right, and Done while the widgets are being arranged. AI is the session on the AI tile, else Monday's chat.
    compact: the limits look's top row (the Glasswave recording's): icons only, the one showing on a round light, Settings at the right."""

    def __init__(self, notch, tabs=ISLAND_TABS, compact=False):
        super().__init__()
        from . import qtisland as Q
        self.n, self._hover, self.tabs, self.compact = notch, None, tabs, compact
        self.gear = None
        if compact:
            self.gear = IconButton("gear", "The notch's settings", size=26, parent=self)
            self.gear.clicked.connect(lambda: self.n.show_view("settings"))
        self.setFixedHeight(TABS_H)
        self.setMouseTracking(True)
        self.setAccessibleName("The island's pages: Home, AI, Music, Chat, Focus, Tray, Clipboard, Note")
        self.sound = IconButton("volume", "Sounds: on or off", size=26, parent=self)
        self.sound.clicked.connect(lambda: self.n.set_pref("sounds", not self.n.p["sounds"]))
        self.done = Q.Lime("Done", "Done arranging your widgets", height=24, width=62, parent=self)
        self.done.clicked.connect(lambda: self.n.grid.edit(False))
        self.done.hide()

    def current(self):
        return TAB_OF_VIEW.get(self.n.view)

    def rects(self):
        """Each tab's place: an icon's width, and the one showing wide enough for its name too."""
        out, x, cur = {}, 4.0, self.current()
        f = font(12.5, 650)
        for key, _icon, name in self.tabs:
            w = TAB_W + (QFontMetricsF(f).horizontalAdvance(name) + 10 if key == cur and not self.compact else 0)
            out[key] = QRectF(x, 2, w, self.height() - 4)
            x += w + TAB_GAP
        return out

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sound.move(self.width() - self.sound.width() - 2, (self.height() - self.sound.height()) // 2)
        if self.gear is not None:
            self.gear.move(self.sound.x() - self.gear.width() - 4, self.sound.y())
        self.done.move(self.sound.x() - self.done.width() - 8, (self.height() - self.done.height()) // 2)

    def paintEvent(self, _e):
        self.sound.icon_name = "volume" if self.n.p["sounds"] else "mute"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cur = self.current()
        for key, icon_name, name in self.tabs:
            r = self.rects()[key]
            tint = TAB_TINTS.get(key, TEXT)
            if key == cur and self.compact:  # the limits look's row: the page showing on a round light (the recording's)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(K.alpha("#FFFFFF", 0.14))
                p.drawEllipse(QPointF(r.left() + TAB_W / 2, r.center().y()), 13, 13)
                colour = TEXT
            elif key == cur:  # the page showing: a pill in its colour, its icon and its name
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(K.alpha(tint, 0.16))
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
                colour = tint
            else:
                colour = TEXT if key == self._hover else TEXT_2
            ic = K.icon(icon_name, colour, 16)
            p.drawPixmap(int(r.left() + (TAB_W - 16) / 2), int(r.center().y() - 8), ic.pixmap(16, 16))
            if key == cur and not self.compact:
                p.setPen(QColor(tint))
                p.setFont(font(12.5, 650))
                p.drawText(QRectF(r.left() + TAB_W - 4, r.top(), r.width() - TAB_W, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        p.end()

    def key_at(self, pos):
        return next((k for k, r in self.rects().items() if r.contains(pos)), None)

    def mouseMoveEvent(self, e):
        k = self.key_at(e.position())
        if k != self._hover:
            self._hover = k
            self.setCursor(Qt.CursorShape.PointingHandCursor if k else Qt.CursorShape.ArrowCursor)
            self.setToolTip(dict((a, c) for a, _b, c in self.tabs).get(k, "") if k else "")
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        k = self.key_at(e.position()) if e.button() == Qt.MouseButton.LeftButton else None
        if not k:
            return e.ignore()  # (anywhere else: the notch's own, a drag moves it)
        self.go(k)

    def go(self, key):
        if key == "ai":
            return self.n.ai_tap()
        self.n.show_view(key)


class MusicView(QWidget):
    """The island's Music page (the recording's player): the cover, the track and its artist, its bars while it plays, where in the track
    it is (when the player says), back / play-pause / forward (Windows' own media session, each checked after: media.control), and the
    player it plays in; the card tinted by the cover. Nothing playing, or no way to read it here: said as such."""
    H = PAGE_H

    def __init__(self, notch):
        super().__init__()
        from . import qtisland as Q
        self.n = notch
        self.setFixedHeight(self.H)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(14)
        self.cover = Q.Cover(64, 12)
        top.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = K.Elided("", 17, 700)
        self.artist = K.Elided("", 13, 400, TEXT_2)
        col.addWidget(self.title)
        col.addWidget(self.artist)
        col.addStretch(1)
        top.addLayout(col, 1)
        top.addSpacing(40)  # (its bars, painted)
        v.addLayout(top)
        v.addSpacing(18)  # (where in the track it is, painted)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch(1)
        self.back = IconButton("back", "Previous track", size=30)
        self.back.clicked.connect(lambda: self.n.media_control("previous"))
        self.play = IconButton("play", "Play or pause", size=34)
        self.play.clicked.connect(lambda: self.n.media_control("toggle"))
        self.fwd = IconButton("forward", "Next track", size=30)
        self.fwd.clicked.connect(lambda: self.n.media_control("next"))
        for b in (self.back, self.play, self.fwd):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.app = K.label("", 11.5, 600, TEXT_2)
        self.app.setParent(self)
        self.app.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def fill(self):
        m = self.n.media
        title, line, playing = I.music_words(m.now, m.ok is not False)
        self.title.set(title)
        self.artist.set(line if m.ok is not None else "Reading what plays…")
        self.cover.set(m.cover if m.now else None)
        on = playing is not None and not m.busy
        for b in (self.back, self.play, self.fwd):
            b.setEnabled(on)
        self.play.icon_name = "pause" if playing else "play"
        self.play.update()
        self.app.setText((m.now or {}).get("name") or (m.now or {}).get("app") or "")
        self.setAccessibleName(f"Music: {title}. {line}")
        self.update()

    def tick(self):
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.app.setGeometry(self.width() - 136, self.height() - 40, 120, 26)

    def paintEvent(self, _e):
        m = self.n.media
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        tint = cover_tint(m.cover) if m.now else None
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, QColor("#0E0F11"))
        g.setColorAt(1.0, mix("#0E0F11", tint, 0.55) if tint else QColor("#141618"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 18, 18)
        playing = bool(m.now and m.now.get("status") == "playing")
        if playing:  # its bars, moving (the recording's), in its cover's colour
            col = tint or QColor(I.WIDGETS["music"][1])
            p.setBrush(col)
            t = self.n.clock()
            for i in range(5):
                bh = 6 + 12 * abs(math.sin(t * 2.7 + i * 1.1))
                p.drawRoundedRect(QRectF(r.right() - 56 + i * 7, 32 - bh / 2, 4, bh), 1.6, 1.6)
        part = m.part() if m.now else None
        y = 92
        if part is not None and m.pos:  # where in the track it is: the time gone, the bar, the time left (only when the player says)
            length = m.pos[1]
            p.setFont(font(11, 500))
            p.setPen(QColor(TEXT_2))
            p.drawText(QRectF(16, y - 8, 40, 16), Qt.AlignmentFlag.AlignVCenter, mmss(part * length))
            p.drawText(QRectF(r.right() - 60, y - 8, 44, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       "-" + mmss(length - part * length))
            bar = QRectF(60, y - 1.5, r.width() - 128, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(K.alpha("#FFFFFF", 0.18))
            p.drawRoundedRect(bar, 1.5, 1.5)
            p.setBrush(QColor(TEXT))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(3.0, bar.width() * part), bar.height()), 1.5, 1.5)
        p.end()


class FocusView(QWidget):
    """The island's Focus page (the recording's timer), the same Pomodoro timer as the Focus widget (island.focus_*, kept in
    data/desktop.json, a break after each): ready, Reset, the minutes picked in their field (the step before and after it faint), Start, and
    5 / 15 / 25 / 50 min; running or paused, Stop, +5 min, Pause or Go on, the time left big and when it will be done."""
    H = 126

    def __init__(self, notch):
        super().__init__()
        self.n = notch
        self.setFixedHeight(self.H)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.reset = K.Button("↺  Reset", "secondary", height=34, name="Back to a pomodoro ready to start")
        self.reset.clicked.connect(self.n.focus_reset)
        self.stop = K.Button("Stop", "secondary", height=34, name="Stop this pomodoro")
        self.stop.clicked.connect(self.n.focus_reset)
        self.more = K.Button("+ 5 min", "secondary", height=34, name="Five more minutes")
        self.more.clicked.connect(self.n.focus_more)
        self.field = _Minutes(self)
        self.big = K.label("", 30, 750)
        self.big.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.go = K.Button("Start", "light", height=34, name="Start the pomodoro")
        self.go.clicked.connect(self.n.focus_tap)
        for w in (self.reset, self.stop, self.more):
            row.addWidget(w)
        row.addWidget(self.field, 1)
        row.addWidget(self.big, 1)
        row.addWidget(self.go)
        v.addLayout(row)
        low = QHBoxLayout()
        low.setSpacing(8)
        self.lengths = K.Segmented([(str(s), f"{s // 60} min") for s in I.LENGTHS], str(I.FOCUS_S), "A pomodoro's length")
        self.lengths.changed.connect(lambda val: self.n.focus_length(int(val)))
        low.addWidget(self.lengths)
        low.addStretch(1)
        self.when = K.label("", 12.5, 500, TEXT_2)
        low.addWidget(self.when)
        v.addLayout(low)
        v.addStretch(1)

    def fill(self):
        f = self.n.island.get("focus")
        st = I.focus_now(f)
        ph, brk = st["phase"], st["kind"] != "focus"
        live = ph in ("running", "paused")
        self.reset.setVisible(not live)
        self.field.setVisible(not live)
        self.stop.setVisible(live)
        self.more.setVisible(live)
        self.big.setVisible(live)
        self.big.setText(mmss(st["left"]))
        self.go.setText({"running": "❚❚  Pause", "paused": "▶  Go on"}.get(ph, "Skip the break" if brk else "▶  Start"))
        self.go.setAccessibleName(self.go.text())
        self.lengths.set_value(str(int((f or {}).get("length") or I.FOCUS_S)))
        self.field.minutes = int(st["total"] // 60) if not live else int(st["left"] // 60)
        self.field.update()
        end = time.time() + st["left"] if ph != "paused" else None
        from .qtisland import h24
        self.when.setText((("Break" if brk else "Done") + f" at {U.clock(end, h24())}") if end else "Paused")
        self.setAccessibleName(f"Focus: {mmss(st['left'])} {('a break' if brk else 'a pomodoro')}, {ph}. {self.when.text()}")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0E0F11"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)
        p.end()


class _Minutes(QWidget):
    """The Focus page's field: the length picked, "0 hr  25 min", the step before and after it faint above and below (the recording's
    wheel); the lengths under it pick it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.minutes = 25
        self.setFixedHeight(56)
        self.setMinimumWidth(180)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        r = QRectF(0.5, full.height() / 2 - 20, full.width() - 1, 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1B1D20"))
        p.drawRoundedRect(r, 10, 10)
        h, m = divmod(self.minutes, 60)
        p.setFont(font(12, 600))
        p.setPen(K.alpha(TEXT_2, 0.35))  # (the wheel's step before and after, faint above and below)
        for dy, val in ((-1, (m - 1) % 60), (1, (m + 1) % 60)):
            p.drawText(QRectF(r.width() * 0.78 - 40, r.center().y() + dy * 24 - 8, 38, 16), Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter, str(val))
        for x, value, unit in ((r.width() * 0.32, h, "hr"), (r.width() * 0.78, m, "min")):
            p.setPen(QColor(TEXT))
            p.setFont(font(19, 700))
            vw = QFontMetricsF(p.font()).horizontalAdvance(str(value))
            p.drawText(QRectF(x - vw - 2, r.top(), vw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(value))
            p.setPen(QColor(TEXT_2))
            p.setFont(font(13, 500))
            p.drawText(QRectF(x + 4, r.top(), 40, r.height()), Qt.AlignmentFlag.AlignVCenter, unit)
        p.end()
