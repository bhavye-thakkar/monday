"""Monday's drawing on the screen, on Qt (2026-10-19: Windows, macOS, Linux): core/overlay.py's marks and circle mode with its shapes and
rules unchanged (mark_points, extent, partial, progress, stroke_box, crop_rect, fit_size, prune); only the windows are Qt's.

- Marks: Core found where something is (SCREEN_MARKS, executor "point"): a circle, a box, an underline or an arrow drawn like a marker, one
  after another, held MARKS_S, then faded, in a see-through window over just the marks that never takes a click or the focus.
- Circle (Ctrl+Alt+C, the tray): the screen under the pointer, frozen and a little dimmed, takes the mouse while the owner circles what
  they mean (Esc or a right click puts it away). The circled part, the stroke drawn on it, goes to data/shots and on to the notch like a
  dropped picture (qtnotch.Notch.circle), where Monday is asked about it. On Wayland the screen can't be read: it says so.

Core's rectangles are the screen's pixels; Qt's windows are in logical ones (pixels / the screen's scale).
ponytail: one scale, the main screen's: on a second screen at another scale a mark can sit off; per-screen mapping if that matters.

    python -m core.qtoverlay --check   the marks drawn and circle mode on a made-up screen (nothing shown)
    python -m core.qtoverlay --show    a mark round the middle of the screen for a few seconds
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from . import qtkit as K  # noqa: F401  (puts PySide6 on the path first)
from .overlay import (CLICK_PX, DIM_ALPHA, DRAW_S, FADE_S, FRAME_MS, GRACE_S, MARKS_S, SHOTS, crop_rect, extent, fit_size, label_spots,
                      mark_points, partial, prune, stroke_box)
from .qtkit import EDGE, TEXT, font
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QGuiApplication, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

ACCENT = "#E5484D"   # the ink: a red pen, as a person marks a page (2026-10-29, the owner: "dont use the neon colors"; was cyan with a glow)
KINDS = ("circle", "box", "underline", "arrow")


def scale():
    """The screen's pixels per logical pixel (the main screen's)."""
    s = QGuiApplication.primaryScreen()
    return s.devicePixelRatio() if s else 1.0


def foreground():
    """The app window in front, as (hwnd, pid); None for none, on another OS, or when it isn't an app's window (untitled or a tool
    window: the taskbar, a flyout)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h or not u.GetWindowTextLengthW(h) or u.GetWindowLongW(h, -20) & 0x80:  # GWL_EXSTYLE & WS_EX_TOOLWINDOW
        return None
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return int(h), pid.value


def draw_marker(p: QPainter, polys, color=ACCENT, width=1.0):
    """A pen's stroke, round at the ends: a faint dark edge under the ink so it holds on any background (no glow)."""
    for c, w in ((QColor(0, 0, 0, 60), 6.5), (QColor(color), 4.0)):
        pen = QPen(c, max(1.5, w * width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for poly in polys:
            if len(poly) >= 2:
                p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in poly]))


class Marks(QWidget):
    """Monday's marks on the screen. show_marks(marks): [{"kind": circle | box | underline | arrow, "rect": [x, y, w, h] in the screen's
    pixels, "label": words beside it}]; a new show replaces what's there. -> False when there is nothing to draw (nothing shown)."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                         | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("Monday's marks on the screen")
        self.setAccessibleName("Monday's marks on the screen")
        self.lines, self.labels, self.parts, self.t0, self.front = [], [], [], None, None
        self.starts, self.end, self.talking = [], 0.0, False
        self._t = QTimer(self)
        self._t.setInterval(FRAME_MS)
        self._t.timeout.connect(self._tick)

    def layout_marks(self, marks, avoid=None):
        """The marks' lines and labels in the window's own (logical) pixels, and the window just round them, inside the screens. Each
        label goes where overlay.label_spots finds it clear of the others and of every mark and what it marks, anywhere on the screens,
        and off `avoid` (the screen's other words, in its pixels, as a look at the screen sends them) where it can."""
        s = scale()
        marks = [m for m in marks or [] if m.get("kind") in KINDS and len(m.get("rect") or ()) == 4]
        polys = [mark_points(m["kind"], [v / s for v in m["rect"]]) for m in marks]
        if not polys:
            return False
        v = QGuiApplication.primaryScreen().virtualGeometry()
        words, fm = [str(m.get("label") or "")[:60] for m in marks], QFontMetrics(font(12, 600))
        sizes = [(fm.horizontalAdvance(w) + 16, 24) if w else (0, 0) for w in words]
        covers = []  # a mark's lines and the thing it marks, from the screens' top-left
        for p, m in zip(polys, marks):
            xs = [q[0] for poly in p for q in poly] + [m["rect"][0] / s, (m["rect"][0] + m["rect"][2]) / s]
            ys = [q[1] for poly in p for q in poly] + [m["rect"][1] / s, (m["rect"][1] + m["rect"][3]) / s]
            covers.append((min(xs) - v.left(), min(ys) - v.top(), max(xs) - min(xs), max(ys) - min(ys)))
        words_at = [(x / s - v.left(), y / s - v.top(), w / s, h / s) for x, y, w, h in (r for r in avoid or () if len(r) == 4)]
        spots = [None if sp is None else (sp[0] + v.left(), sp[1] + v.top())
                 for sp in label_spots(covers, sizes, [m["kind"] for m in marks], (v.width(), v.height()), avoid=words_at)]
        boxes = [extent(p, 1.0) for p in polys] + [(sp[0] - 4, sp[1] - 4, tw + 8, th + 8) for sp, (tw, th) in zip(spots, sizes) if sp]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
        x0, y0, x1, y1 = max(x0, v.left()), max(y0, v.top()), min(x1, v.right() + 1), min(y1, v.bottom() + 1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return False
        x0, y0 = int(x0), int(y0)
        self.setGeometry(QRect(x0, y0, int(x1 - x0), int(y1 - y0)))
        self.lines = [[[(px - x0, py - y0) for px, py in poly] for poly in p] for p in polys]
        self.labels = [(w or None, sp and (sp[0] - x0, sp[1] - y0)) for w, sp in zip(words, spots)]
        return True

    def show_marks(self, marks, avoid=None, front=None, hold=None, staged=False):
        """front: the window the marks are about (a look at the screen's): they fade once another app's window comes in front. hold: how
        long they stay once drawn (MARKS_S; a look's: the time to read its explanation, or while Monday says it). staged: none is drawn
        until reveal(i) (a look said out loud: each as its part is said). Nothing to draw: what was there goes (Core's "stop")."""
        if not self.layout_marks(marks, avoid):
            self.clear()
            return False
        now, n = time.monotonic(), len(self.lines)
        self.t0, self.parts, self.front, self.talking = now, [0.0] * n, front, False
        self.starts = [None] * n if staged else [now + i * DRAW_S for i in range(n)]  # when each mark is drawn (None: not yet)
        self.end = now + (0 if staged else n * DRAW_S) + (MARKS_S if hold is None else float(hold))  # when they start to fade
        self.setWindowOpacity(1.0)
        self.show()
        self._t.start()
        self.update()
        return True

    def clear(self):
        self._t.stop()
        self.hide()

    def reveal(self, i):
        """Mark i is drawn now (a staged look's: its part is being said)."""
        if self.isVisible() and 0 <= i < len(self.starts) and self.starts[i] is None:
            self.starts[i] = time.monotonic()
            self.end = max(self.end, self.starts[i] + DRAW_S + GRACE_S)

    def hold_for(self, seconds):
        """The marks start to fade `seconds` from now."""
        self.end = time.monotonic() + seconds

    def _end_in(self, after, keep=False):
        """The marks start to fade `after` seconds from now: no later than that (keep: no sooner)."""
        t = time.monotonic() + after
        self.end = max(self.end, t) if keep else min(self.end, t)

    def speaking(self, on):
        """Monday's voice started or stopped: the marks stay while it speaks (it is saying what they're about), and go GRACE_S after; a
        mark still waiting when it stops comes then (all of it was said)."""
        if not self.isVisible():
            return
        if on:
            self.talking = True
        elif self.talking:
            self.talking = False
            late = [i for i, s in enumerate(self.starts) if s is None]
            for i in late:
                self.reveal(i)
            self.hold_for(GRACE_S + (DRAW_S if late else 0))

    def fade(self):
        """Stopped (Esc, "stop", "stop talking"): the marks go now."""
        if self.isVisible():
            self.talking, self.front = False, None
            self._end_in(0)

    def _tick(self):
        fg = foreground() if self.front else None
        if fg and fg[0] != self.front and fg[1] != os.getpid():  # another app's window in front (Monday's own notch and window don't count):
            self.fade()                                          # the marks were about the one before
        elif self.talking:
            self._end_in(GRACE_S, keep=True)
        now = time.monotonic()
        parts = [0.0 if s is None else min(1.0, max(0.0, (now - s) / DRAW_S)) for s in self.starts]
        alpha = 255 if now < self.end else max(0, int(255 * (1 - (now - self.end) / FADE_S)))
        if alpha <= 0:
            return self.clear()
        if parts != self.parts:
            self.parts = parts
            self.update()
        self.setWindowOpacity(alpha / 255)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(12, 600))
        for polys, part, (label, spot) in zip(self.lines, self.parts, self.labels):
            draw_marker(p, partial(polys, part))
            if label and spot and part >= 1:  # (where overlay.label_spots put it: off the words, other labels and other marks)
                words = str(label)
                tw, th = p.fontMetrics().horizontalAdvance(words) + 16, 24
                bx, by = spot
                p.setPen(Qt.PenStyle.NoPen)  # (the ink's own colour, white words: a note in the same pen as the mark)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(QRectF(bx, by, tw, th), 7, 7)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(QRectF(bx, by, tw, th), Qt.AlignmentFlag.AlignCenter, words)
        p.end()


class Circle(QWidget):
    """Circle mode: the screen under the pointer frozen and dimmed takes the mouse while the owner circles something; Esc or a right click
    puts it away (on_cancel). The circled part, the stroke drawn on it, is saved in data/shots (at most SHOT_MAX px across) and handed to
    on_done({"path", "box", "crop"}), the rectangles in the screen's pixels. shot: a picture to circle on instead (the self-check)."""

    def __init__(self, on_done, on_cancel=None, shot: QPixmap | None = None, folder=SHOTS):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.on_done, self.on_cancel, self.shot, self.folder = on_done, on_cancel or (lambda: None), shot, Path(folder)
        self.points, self.over, self.origin = [], False, (0, 0)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setWindowTitle("Circle what you mean")
        self.setAccessibleName("Circle what you mean on the screen; Esc puts it away")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def start(self, show=True):
        """The screen under the pointer, read now (its own pixels). -> None, or why it can't (Wayland gives no picture of the screen)."""
        if self.shot is None:
            scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if QGuiApplication.platformName().startswith("wayland"):
                return "Circling needs a picture of the screen, and Wayland doesn't give one to apps."
            self.shot = scr.grabWindow(0)
            if self.shot.isNull():
                return "The screen couldn't be read (on macOS: System Settings › Privacy & Security › Screen Recording)."
            g = scr.geometry()
            self.origin = (round(g.x() * self.shot.devicePixelRatio()), round(g.y() * self.shot.devicePixelRatio()))
            self.setGeometry(g)
        else:
            r = self.shot.devicePixelRatio()
            self.setGeometry(0, 0, round(self.shot.width() / r), round(self.shot.height() / r))
        if show:
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus()
        return None

    def _px(self, pos):
        """A point on this window -> the shot's own pixels."""
        r = self.shot.width() / max(1, self.width())
        return (pos.x() * r, pos.y() * r)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            return self.cancel()
        if e.button() == Qt.MouseButton.LeftButton:
            self.points = [self._px(e.position())]

    def mouseMoveEvent(self, e):
        if self.points and e.buttons() & Qt.MouseButton.LeftButton:
            self.points.append(self._px(e.position()))
            self.update()

    def mouseReleaseEvent(self, e):
        if self.points and e.button() == Qt.MouseButton.LeftButton:
            self.points.append(self._px(e.position()))
            self.finish()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancel()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, DIM_ALPHA))
        words = "Circle what you mean  ·  Esc puts it away"
        p.setFont(font(13, 600))
        tw, th = p.fontMetrics().horizontalAdvance(words) + 28, 34
        box = QRectF((self.width() - tw) / 2, 18, tw, th)
        p.setPen(QPen(QColor(EDGE), 1))
        p.setBrush(QColor(26, 28, 32, 240))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor(TEXT))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, words)
        if len(self.points) >= 2:
            r = self.width() / max(1, self.shot.width())
            draw_marker(p, [[(x * r, y * r) for x, y in self.points]])
        p.end()

    def cancel(self):
        if not self.over:
            self.over = True
            self.close()
            self.on_cancel()

    def finish(self):
        """The circled part to a file: the screen round the stroke (CROP_PAD), the stroke drawn on it, at most SHOT_MAX px across."""
        if self.over:
            return None
        self.over = True
        w, h = self.shot.width(), self.shot.height()
        box = stroke_box(self.points)
        cx, cy, cw, ch = crop_rect(box, (w, h))
        ow, oh = fit_size(cw, ch)
        s = ow / cw
        out = QImage(ow, oh, QImage.Format.Format_ARGB32)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shot = self.shot.toImage()
        shot.setDevicePixelRatio(1.0)  # its own pixels, as the crop's
        p.drawImage(QRectF(0, 0, ow, oh), shot, QRectF(cx, cy, cw, ch))
        xs, ys = [q[0] for q in self.points], [q[1] for q in self.points]
        click = max(xs) - min(xs) < CLICK_PX and max(ys) - min(ys) < CLICK_PX  # (stroke_box's rule): a ring round the square instead
        stroke = mark_points("circle", ((box[0] - cx) * s, (box[1] - cy) * s, box[2] * s, box[3] * s), s) if click else \
            [[((x - cx) * s, (y - cy) * s) for x, y in self.points]]
        draw_marker(p, stroke, width=s * max(1.0, w / max(1, self.width())))
        p.end()
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"circled-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.png"
        out.save(str(path), "PNG")
        prune(self.folder)
        self.close()
        ox, oy = self.origin
        got = {"path": str(path), "box": (ox + box[0], oy + box[1], box[2], box[3]), "crop": (ox + cx, oy + cy, cw, ch)}
        self.on_done(got)
        return got


def _check():
    import tempfile
    app = K.make_app(["monday-overlay"])  # noqa: F841
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        ok = ok and bool(cond)

    s = scale()
    m = Marks()
    m.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    check("marks: laid out round what Core found (in logical px), unknown kinds left out",
          m.layout_marks([{"kind": "circle", "rect": [500 * s, 400 * s, 200 * s, 80 * s], "label": "Save"}, {"kind": "nope", "rect": [0, 0, 1, 1]}])
          and m.x() < 500 and m.y() < 400 and m.x() + m.width() > 700 and m.y() + m.height() > 480 and len(m.lines) == 1, str(m.geometry()))
    m.parts = [1.0]
    img = m.grab().toImage()
    px, py = m.lines[0][0][len(m.lines[0][0]) // 3]
    r = img.devicePixelRatio()
    check("marks: the marker's line is drawn, and what it circles stays visible", img.pixelColor(int(px * r), int(py * r)).alpha() > 0
          and img.pixelColor(int((500 - m.x() + 100) * r), int((400 - m.y() + 40) * r)).alpha() == 0)
    check("marks: nothing to draw, nothing shown", not m.show_marks([]) and not m.show_marks([{"kind": "circle", "rect": [1, 2]}]))
    from .overlay import _hits
    four = [{"kind": "box", "rect": [40, 730, 693, 69], "label": "1 · YouTube video info"}, {"kind": "circle", "rect": [1211, 27, 81, 17],
            "label": "2 · ChatGPT interface"}, {"kind": "underline", "rect": [1210, 97, 438, 22], "label": "3 · your question"},
            {"kind": "box", "rect": [1211, 147, 465, 57], "label": "4 · its answer"}]  # (a look at a 1920 x 1080 screen explained, 2026-10-29)
    g = QGuiApplication.primaryScreen().virtualGeometry()
    k, ox, oy = min(1.0, g.width() * s / 1920, g.height() * s / 1080), g.left() * s, g.top() * s  # (fitted to this PC's screens)
    four = [{**mk, "rect": [ox + x * k, oy + y * k, w * k, h * k]} for mk in four for x, y, w, h in [mk["rect"]]]
    m.layout_marks(four)
    fm = QFontMetrics(font(12, 600))
    put = [(sx, sy, fm.horizontalAdvance(w) + 16, 24) for w, (sx, sy) in m.labels]
    marked = [(x / s - m.x(), y / s - m.y(), w / s, h / s) for x, y, w, h in (mk["rect"] for mk in four)]
    m3 = Marks()  # a look's marks belong to the window looked at: another app's in front fades them; Monday's own notch doesn't
    m3.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    m3.show_marks([{"kind": "circle", "rect": [500 * s, 400 * s, 200 * s, 80 * s], "label": "x"}], front=111)
    real_fg, fronts = globals()["foreground"], []
    for fg in ((111, 1), (222, os.getpid()), (222, 1)):
        globals()["foreground"] = lambda fg=fg: fg
        m3._tick()
        fronts.append((m3.front, m3.end <= time.monotonic() + 0.01))
    globals()["foreground"] = real_fg
    m3.clear()
    check("marks: fade once another app's window is in front, not for the same one or Monday's own (2026-10-29)",
          fronts == [(111, False), (111, False), (None, True)], str(fronts))

    def left(mk):  # seconds until the marks start to fade
        return mk.end - time.monotonic()
    m3.show_marks([{"kind": "circle", "rect": [500 * s, 400 * s, 200 * s, 80 * s], "label": "x"}], hold=10)
    first = left(m3)
    m3.speaking(True)
    m3.end = time.monotonic() - 5  # (its time ran out while Monday was still talking)
    m3._tick()
    talking = left(m3)
    m3.speaking(False)
    after = left(m3)
    m3.fade()
    stopped = left(m3)
    check("marks: a look's own hold; kept while Monday speaks; gone GRACE_S after it stops; at once on stop (the owner, 2026-10-29)",
          9 < first <= 10.5 and GRACE_S - 0.2 < talking <= GRACE_S and GRACE_S - 0.2 < after <= GRACE_S and stopped <= 0.01,
          f"{first:.2f} {talking:.2f} {after:.2f} {stopped:.2f}")
    m3.show_marks([])
    check("marks: an empty set takes the marks away (Core's “stop”)", not m3.isVisible())
    two = [{"kind": "circle", "rect": [500 * s, 400 * s, 200 * s, 80 * s], "label": "1"}, {"kind": "box", "rect": [500 * s, 600 * s, 200 * s, 80 * s],
           "label": "2"}]
    m3.show_marks(two, hold=600, staged=True)  # a look said out loud: each mark as its part is said
    m3._tick()
    hidden = m3.parts == [0.0, 0.0]
    m3.reveal(0)
    m3.speaking(True)
    m3.starts[0] -= DRAW_S  # (its part was said a moment ago)
    m3._tick()
    one = m3.parts == [1.0, 0.0]
    m3.speaking(False)  # said to the end: what still waited comes now, then all go GRACE_S after
    m3.starts[1] -= DRAW_S
    m3._tick()
    check("marks: a look's marks come as their parts are said; one still waiting comes when the speech ends (2026-10-29)",
          hidden and one and m3.parts == [1.0, 1.0] and GRACE_S < left(m3) <= GRACE_S + DRAW_S, f"{hidden} {one} {m3.parts} {left(m3):.2f}")
    m3.clear()
    check("marks: each label clear of the others and of everything marked (an underline's off its words), inside the window",
          len(put) == 4 and not any(_hits(a, b) for i, a in enumerate(put) for b in put[i + 1:]) and not any(_hits(a, b) for a in put for b in marked)
          and all(0 <= x and 0 <= y and x + w <= m.width() and y + h <= m.height() for x, y, w, h in put), str(put))
    with tempfile.TemporaryDirectory() as d:
        pic = QPixmap(1200, 800)
        pic.fill(QColor("#ffffff"))
        p = QPainter(pic)
        p.fillRect(300, 200, 200, 100, QColor("#000000"))
        p.end()
        got, gone = [], []
        c = Circle(got.append, lambda: gone.append(1), shot=pic, folder=d)
        c.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        c.start(show=False)
        c.points = [(280, 180), (520, 180), (520, 320), (280, 320), (282, 182)]
        c.finish()
        g0 = got[0] if got else {}
        check("circle: the circled part saved with the stroke on it, and handed on", len(got) == 1 and not gone
              and g0["box"] == (280, 180, 241, 141) and Path(g0["path"]).name.startswith("circled-"), str(g0))
        im = QImage(g0.get("path", ""))
        bx, by = g0["box"][0] - g0["crop"][0], g0["box"][1] - g0["crop"][1]
        check("circle: what was circled is in the picture", im.size().width() == g0["crop"][2]
              and im.pixelColor(bx + 120, by + 70).name() == "#000000")
        c2 = Circle(got.append, shot=pic, folder=d)
        c2.start(show=False)
        c2.points = [(400, 250), (402, 251)]
        c2.finish()
        check("circle: a click circles the square round it", got[-1]["box"][2] == got[-1]["box"][3] and len(got) == 2)
        c3 = Circle(got.append, lambda: gone.append(1), shot=pic, folder=d)
        c3.start(show=False)
        c3.cancel()
        c3.cancel()
        check("circle: Esc puts it away once, nothing sent", gone == [1] and len(got) == 2)
    return ok


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if _check() else 1)
    elif "--show" in sys.argv:
        app = K.make_app(["monday-overlay"])
        g = QGuiApplication.primaryScreen().geometry()
        k = scale()
        mk = Marks()
        mk.show_marks([{"kind": "circle", "rect": [(g.center().x() - 120) * k, (g.center().y() - 40) * k, 240 * k, 80 * k],
                        "label": "Monday's marks"}])
        QTimer.singleShot(5000, app.quit)
        app.exec()
