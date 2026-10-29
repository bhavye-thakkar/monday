"""Monday's notch in Qt (2026-10-19): the floating window at the top of the screen, on Windows, macOS and Linux, as the owner's Coucou
recording shows it. Closed: a slim black bar hanging from the screen's top edge, the pet at its left and the agents' pals at its right
(wider, with a line, while something happens). Open: a black shell with its tabs (Home, Chat, + at the left; Settings and Sound at the
right) over near-black cards. It opens on the spring and closes on the curve core/notch.py measured from Coucou (SPRING, CLOSE_MS).

A real window of the desktop app (frameless, always on top, out of the taskbar and Alt+Tab, shown without taking the keyboard: a click
in the prompt takes it, closing gives it back), and a view over the same events as Monday's window (its Bridge, core/notch.py's State):
it shows only what Core said. Dragged anywhere (the OS's own move), kept in data/desktop.json ("notch_at"), put back inside a screen.

From core/notch.py (pure, no WinForms): State, prefs, examine and the file actions, the spring and the curves, tone_wav, perm_words /
ask_words, chat_turns, _plain, and the agent view's words (code_card, meter_rows, agent_actions, steps_view, say_words, rewind_words). The
pet is qtkit's PetArt (notch.Pet's drawing).

Home's agent view (2026-10-19, what the retired notch had, after Dotpals): one of the owner's own coding agents in detail, from Core's
AGENT_WATCH only: its words, the last edit as a code card (CodeCard), the plan / context / limits bars (Meters), Steps with Dotpals'
filters and Files, Story with Copy today, Next agent, its chat, Write to (POST /api/agents/say; copied and its window brought forward when
it can't take the words) and Rewind… (RewindDialog: Core's preview, the rewind, Undo).

The island (2026-10-24, the owner's TwoCode recording "Make the island yours"; docs/adr/0006): Home is a grid of widgets the owner arranges
(core/qtisland.py draws them, core/island.py holds the rules, data/desktop.json keeps them as "island"); held, they wiggle to be removed,
resized and moved; + opens the widget menu and, a click away, Files (the drop card); while an agent asks, its card takes Home's width.
Sound, Settings, Collapse and Power (the tray's Quit Monday, after asking) at the right.

Check: `python -m core.qtnotch --check`. Pictures of each view: `python -m core.qtnotch --shot <folder> [--port 8773]`.
"""
from __future__ import annotations

import math
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse

from . import island as I
from . import qtisland as Q
from . import qtkit as K
from . import qtlooks as L
from . import usage as U
from .notch import (ACCENT, ACTIONS, AGENT_TAIL, AGENT_TURNS, ANSWER_MAX, AUTOCLOSE, CARD_R, CHAT_EVERY_S, CHUNK, CIRCLE_ACTIONS, CLOSE_CURVE,
                    CLOSE_MS, CORNER, DASH_S, DROP_CHIPS, HOOKS, HOVER_S, KIND_WORDS, NOTIFY_WAIT_S, OPEN_W, PAD, PILL_COLOURS, RESULT_S,
                    STATES, THREAD_STATES, TURN_CHARS, Spring, State, _head, _plain, _tail, agent_actions, agent_words, ask_words, chat_turns,
                    breakable, code_card, dock_pills, examine, folder_words, meter_rows, perm_words, prefs, rewind_button, rewind_words,
                    said_elsewhere, say_words, size_words, steps_view, tone_wav, wear_now, dragging)
from .notch import HATS as WEAR_HATS, WEAR  # noqa: E402  (the wardrobe's outfits)
from .qtkit import (EDGE, GREEN, ICON, PALS, RED, TEXT, TEXT_2, TEXT_3, Button, Chip, IconButton, PetArt, Toggle, font, label, mix,
                    reduced_motion)
from PySide6.QtCore import (QEasingCurve, QEvent, QMimeData, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFontMetricsF, QGuiApplication, QImage, QInputDevice, QPainter, QPainterPath,
                           QPen, QRadialGradient, QTextCursor, QTextLayout, QTextOption)
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu,
                               QPlainTextEdit, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

CLOSED = (288, 32)          # the closed bar at rest: the recording's (360 x 40 px at 125 %)
ISLAND_CLOSED = (196, 40)   # the island at rest: hanging from the top edge with Monday's pet (the TwoCode recordings', measured 208 x 45)
ISLAND_LIVE = (380, 40)     # ...with something going on: its words, the pet, how long it has run
ISLAND_CELL_H = 150         # its Home: one row of three tiles, about 4:3 (the "Home got a glow-up" recording's)
ISLAND_R = 26               # its open panel's bottom corners
LIMITS_CLOSED = (300, 32)   # the limits notch at rest: the busiest limit's ring at its left, its % at its right
USAGE_CLOSED_S = 300.0      # the limits notch reads every AI's limits at most this often while closed (open: usage.EVERY_S)
CLAWD_ART = K.Clawd()       # Claude Code's mascot, in the pet's place while it asks (Notch.mascot)
CLOSED_PET_X = 22           # its pet's box from the bar's left (the recording's brim starts 23 in)
SWIPE_PX = 40               # a drag on Home's card this far sideways moves to the next or previous chat (2026-10-21)
FOLD_UP_PX = 10             # a drag up on the closed bar this far folds it: it hangs from the screen's top edge, so the pointer can't go 40
GREET_S = 1.1              # the start's greeting: the eyes' happy ^ ^ this long, once Core's handshake says it is ready (the recordings')
BOOT_POLL_MS, BOOT_MAX_S = 1000, 120  # Core answering but still starting: its /api/hello asked again this often, at most this long
ISLAND_FADE_MS = 360       # the floating island's start: it fades in where it floats as its pill grows from the eyes to its width
SWITCH_FADE_MS = 220       # a new look: the notch comes in again over this long
ARRIVE_MS, ARRIVE_FROM = 650, 24  # the start: the closed bar comes down from above the screen's edge (this far above it) and settles with a bounce
GESTURE_END_S = 0.25        # a wheel burst with no event this long is over: one swipe, one move, however many events a touchpad sends
SWIPE_SUM = 60              # ...made once its wheel delta adds up to this (a mouse wheel's notch is 120; a touchpad's events are small)
SLIDE_MS = 220              # Home's card sliding to the next chat
HANDLE_W, HANDLE_H = 48, 2.5  # the handle (the owner, 2026-10-21, of the line's thread: "like this below the notch too"): at the bottom of
#                               the closed bar and of the open notch; each slide up is one of the three steps: open, the bar, the line
CLUSTER_RIGHT, CLUSTER_STEP = 31, 16.5  # the 2 x 2 pills: the right column's centre from the bar's right, the step to the left one
CLOSED_LINE = (420, 38)     # closed, with something to say: the pet, the state's line, its dot
LINE = (140, 6)             # swiped up (2026-10-21, the owner: "the notch should be in a line", like the Claude app's): a thin line at the
#                             top edge; the mouse on it, or a swipe down, brings the bar back; something to say brings it back too
LINE_PET = (64, 30)         # the line while one of the owner's apps is at work (2026-10-26: "it should show the apps mascot if the app is
#                             working"): that app's mascot alone, hanging where the line was (narrower than LINE: refresh resizes on width);
#                             not over a full-screen app (a video, a game): Windows' own "busy" for its notifications (full_screen)
QUIET_SCREEN = (2, 3, 4)    # SHQueryUserNotificationState: busy (a full-screen app), a full-screen D3D game, presentation mode
TOP_IN = 10                 # the open shell's tabs sit this far under its top (the recording's: their middle 24 down)
TAB_GAP = 6                 # between the tabs and the cards (so the cards still start 44 down, as in the recording)
CARD_H = {"home": I.ROWS * Q.CELL_H + (I.ROWS - 1) * Q.GAP, "settings": 145, "drop": 123, "note": Q.NOTE_H, "wardrobe": 112}  # each view's cards (Chat
#                             grows with its thread; Home is the island's grid of widgets since 2026-10-24, its two rows)
ISLAND_KEY = "island"       # data/desktop.json: the island's widgets {"layout", "note", "focus"} (core/island.py; docs/adr/0006)
SETTINGS_GAP = 10           # between Settings' own card and the widget menu under it
ISLAND_TICK_MS = 1000       # the open island's clock, focus timer and music progress move on this often
CHAT_MIN, CHAT_MAX = 150, 330
CHAT_SETTLES = 2           # Chat sized again after it lands at most this often for one change of its thread (Notch._settle_chat)
CHAT_PAGE = 16              # Chat's thread: Core's messages read this many at a time, newest first; older pages as the owner scrolls up
#                             (40 until 2026-10-24: the first opening laid out 36 turns at once, a 321 ms stop; 16: 184 ms, and the next page
#                             scrolling up 60 ms instead of 358)
WARM_FONTS_MS = 2500        # after the notch is made: the fonts its cards use are loaded (Notch._warm_fonts), one per turn of the event loop
WARM_DATA_MS = 1500         # after Core answers: Chat's and Home's data read while the notch is closed (Notch._warm_views)
WARM_SIGNS = "▾ ▸ ✓ ✗ … · — → ← ↗ • ★ ₹ ° ×"  # signs the cards and Monday's words use that Segoe UI may lack (a fallback font, loaded once)
OLDER_AT_PX = 60            # scrolled this close to the top of what is loaded: the page before it is read (GET /api/messages?before=)
LIVE_FLUSH_MS = 50          # a reply being written (MESSAGE_DELTA, every 80 ms from Core): its new words drawn at most this often
CHAT_W = OPEN_W - 2 * PAD - 20 - 84 - 8 - 24 - 4  # Chat's thread's width before it was ever laid out: the inner card, less the pet and margins
LONG_TURN, LONG_SHOWN = 1200, 600  # a stored turn longer than this (a paste, a report) shows this much and "Show all", which shows the rest
#                                    (3500 / 1500 until 2026-10-24: laying out the long answers was most of the first opening's stop)
AGENT_MIN, AGENT_MAX = 150, 430   # Home's agent view grows with what it shows, to this (its middle scrolls past it)
FRAME_MS = 33               # the pet's frames while the notch shows something moving (30 a second); 15 at rest
ANIM_MS = 15                # the outline's frames while it opens, grows or closes
ANIM_MAX_DT = 0.1           # one frame moves the spring at most this much time (a frame late by more: the motion skips ahead, not on)
SAVE_AFTER_MS = 400         # a drag ended this long ago: its place is kept
NOTCH_KEY = "notch_at"      # data/desktop.json: {"cx", "y", "screen"} in logical px (the WinForms notch's "notch" is in its own px)
DOCK_SHOWN = 4
LIVE_ROWS = 2               # a live session's last steps on Home's card (with its head and links: what the card's 87 px hold)
HOME_PET = 86              # the pet on Home's card (the recording's: 98 wide with its brim, 78 tall, in a 87 px card)
GITHUB_EVERY_S = 600.0     # GitHub read through the owner's gh at most this often (while the GitHub widget is on Home)
PILL_H = 26                # a pill's height (the recording's 33 px at 125 %), two rows of them in the middle of their card
FILE_ROW = 3               # a dropped file's actions on the first row; more go on a second, and the drop card grows by FILE_ROW2_H
FILE_ROW2_H = 30
HOME_ASK_H = 140           # Home's card while one of the owner's agents asks (its words, up to four lines, and Deny / Always / Allow)
QUESTION_H, QUESTION_ROW_H = 112, 30  # Home's card for one of Claude Code's questions: its words and footer, and each row of options
OTHER_CHARS = 500           # the owner's own answer to one of them, at most (Core keeps as many: monday.ANSWER_CHARS)
NOTCH_QSS = ("QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 0; } "  # the notch's own scrollbars: thin and dark
             "QScrollBar::handle:vertical { background: #3a3e43; border-radius: 3px; min-height: 24px; } "  # (the window's are the
             "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; } "  # recording's grey ones; the recording's notch
             "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }")  # shows none)
UPLOAD_TIMEOUT_S = 300      # a 100 MB file copied to Core on this computer takes seconds; this is only the ceiling
SHELL = "#000000"
CARD_BG, CARD_EDGE = "#121316", "#1F2024"      # notch.py's measured cards
INNER_BG, INNER_EDGE = "#141818", "#202428"     # a card in a card (the recording's chat: its providers' card, sampled)
GLOW = QColor(46, 47, 97)                       # the indigo light under the prompt
CODE_BG, TRACK = "#0B0C0E", "#2C2F35"           # the code card's ground; a bar's track (the retired notch's LINE)


def claude_sessions_dirs():
    """Where the Claude app keeps its Code sessions (a local_<id>.json each): its data folder, wherever this install of it keeps it."""
    import glob
    app, local, home = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", ""), os.path.expanduser("~")
    pats = (os.path.join(app, "Claude", "claude-code-sessions"), os.path.join(local, "Claude-Data", "*", "claude-code-sessions"),
            os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code-sessions"),
            os.path.join(home, "Library", "Application Support", "Claude", "claude-code-sessions"),
            os.path.join(home, ".config", "Claude", "claude-code-sessions"))
    return [d for p in pats for d in glob.glob(p) if os.path.isdir(d)]


def claude_app_link(transcript, dirs=None):
    """The Claude app's own link to one of its Code sessions (claude://claude.ai/epitaxy/local_<id>: the app opens that chat), found by
    the session's transcript: its file's name is the app's cliSessionId, at the top of the app's local_<id>.json (read: its first 512
    bytes of ~700 KB). None when the app has no such session here."""
    import glob
    cli = os.path.splitext(os.path.basename(str(transcript or "")))[0]
    want = f'"cliSessionId":"{cli}"'.encode()
    for d in (claude_sessions_dirs() if dirs is None else dirs) if cli else ():
        for f in glob.glob(os.path.join(d, "**", "local_*.json"), recursive=True):
            try:
                with open(f, "rb") as fh:
                    if want in fh.read(512):
                        return "claude://claude.ai/epitaxy/" + os.path.splitext(os.path.basename(f))[0]
            except OSError:
                continue
    return None


def touchpad_natural():
    """Does this PC's precision touchpad scroll the natural way (Windows' default, Settings → Touchpad → Scrolling direction: "Down motion
    scrolls up", ScrollDirection 0; the owner's, 2026-10-21)? Then a swipe of the fingers up arrives as a scroll down. False elsewhere
    (macOS says it in each event: inverted)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\PrecisionTouchPad") as k:
            return winreg.QueryValueEx(k, "ScrollDirection")[0] == 0
    except OSError:
        return True  # (not set: Windows' default)


def store_logo(exe):
    """A Store app's own logo for a dark ground (the notch is black): its package (the folder holding AppxManifest.xml above its program,
    under WindowsApps) names its Square44x44Logo, and the "altform-unplated" size of it is the one Windows puts on a dark taskbar. The
    program's icon read from there came back grey with a padlock (the Codex app's, 2026-10-21: "the codex mascot still not showing"). None:
    not a Store app, or no such file."""
    import glob
    if "windowsapps" not in str(exe or "").lower():
        return None
    d = os.path.dirname(str(exe))
    while os.path.dirname(d) != d and not os.path.isfile(os.path.join(d, "AppxManifest.xml")):
        d = os.path.dirname(d)
    try:
        with open(os.path.join(d, "AppxManifest.xml"), encoding="utf-8", errors="replace") as f:
            m = re.search(r'Square44x44Logo="([^"]+)"', f.read())
    except OSError:
        return None
    stem, ext = os.path.splitext(m.group(1).replace("\\", "/")) if m else ("", "")
    found = glob.glob(os.path.join(d, stem + "*" + ext)) if stem else []
    return max(found, key=lambda f: ("altform-unplated" in f.lower(), os.path.getsize(f)), default=None)


def chat_link(w):
    """The link that opens a watched session's own chat in its app (the owner, 2026-10-21: "the chatgpt and the other ides" too): the
    Claude app's (claude_app_link), the Codex app's, the ChatGPT one (codex://threads/<id>: the id that ends its rollout's name); None
    for any other, whose window is brought forward instead (agent_places)."""
    entry, key = (w.get("where") or {}).get("entry"), str(w.get("key") or "")
    if entry == "claude-desktop":
        return claude_app_link(key)
    codex_app = entry in ("Codex Desktop", "codex_work_desktop")  # (what the ChatGPT app's Codex has called itself)
    tid = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", key, re.I) if codex_app else None
    return f"codex://threads/{tid.group(1)}" if tid else None


def play_tone(tone, volume):
    """One of the notch's tones (notch.tone_wav: made here, at the owner's volume), on this OS's own player, in the background."""
    if volume <= 0:
        return
    data = tone_wav(tone, volume)

    def run():
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                return
            path = os.path.join(tempfile.gettempdir(), f"monday-tone-{tone}.wav")
            with open(path, "wb") as f:
                f.write(data)
            import subprocess
            for cmd in (["afplay", path],) if sys.platform == "darwin" else (["paplay", path], ["aplay", "-q", path]):
                try:
                    subprocess.run(cmd, timeout=5, capture_output=True)
                    return
                except (OSError, subprocess.SubprocessError):
                    continue
        except Exception:  # a sound that can't play is skipped; the notch goes on
            pass
    threading.Thread(target=run, daemon=True).start()


def shell_path(w, h, hanging, radius) -> QPainterPath:
    """The notch's outline: hanging from the screen's top edge, square above and round below (Coucou's); floating, round all round."""
    path = QPainterPath()
    if not hanging:
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
        return path
    r = min(radius, h / 2, w / 2)
    path.moveTo(0, 0)
    path.lineTo(w, 0)
    path.lineTo(w, h - r)
    path.quadTo(w, h, w - r, h)
    path.lineTo(r, h)
    path.quadTo(0, h, 0, h - r)
    path.closeSubpath()
    return path


class NCard(QFrame):
    """One of the open notch's cards: near-black on a faint edge (notch.py's CARD / EDGE). glow: the recording's indigo light rising from
    its floor (Chat's prompt sits in it); inner: a card in a card, a shade lighter on a firmer edge (Chat's providers)."""

    def __init__(self, parent=None, dashed=False, glow=False, inner=False):
        super().__init__(parent)
        self.dashed, self.dash, self.glow, self.inner = dashed, 0.0, glow, inner
        self.tint = None  # a state's own light under the card (the 2026-10-26 recording: amber while an agent asks, green once it's done)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 10)
        self.lay.setSpacing(4)

    def set_tint(self, color):
        if color != self.tint:
            self.tint = color
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor(INNER_BG if self.inner else CARD_BG))
        if self.dashed:  # the drop zone's dashes marching round it (Coucou's dash-march)
            pen = QPen(QColor("#3E3F45"), 1.6, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([5, 4])
            pen.setDashOffset(-self.dash)
            p.setPen(pen)
        else:
            p.setPen(QPen(QColor(INNER_EDGE if self.inner else CARD_EDGE), 1))
        p.drawRoundedRect(r, CARD_R, CARD_R)
        if self.glow:  # a wide, flat light on the floor (sampled at 125 %: #2c2c5c under the prompt, #1c1c30 above it, none in the corners)
            clip = QPainterPath()
            clip.addRoundedRect(r, CARD_R, CARD_R)
            p.setClipPath(clip)
            rad = r.width() * 0.46
            p.translate(r.width() * 0.56, r.bottom() + 4)
            p.scale(1.0, 0.42)
            g = QRadialGradient(QPointF(0, 0), rad)
            for at, a in ((0.0, 210), (0.6, 70), (1.0, 0)):
                g.setColorAt(at, QColor(GLOW.red(), GLOW.green(), GLOW.blue(), a))
            p.fillRect(QRectF(-rad, -rad, 2 * rad, 2 * rad), g)
        if self.tint:  # (sampled from the recording: #503515 at the amber light's heart, #17483D at the green's, on the card's #111215)
            p.resetTransform()
            clip = QPainterPath()
            clip.addRoundedRect(r, CARD_R, CARD_R)
            p.setClipPath(clip)
            rad, c = r.width() * 0.36, QColor(self.tint)
            p.translate(r.width() * 0.56, r.bottom() + 8)
            p.scale(1.0, 0.62)
            g = QRadialGradient(QPointF(0, 0), rad)
            for at, a in ((0.0, 150), (0.5, 60), (1.0, 0)):
                c.setAlpha(a)
                g.setColorAt(at, QColor(c))
            p.fillRect(QRectF(-rad, -rad, 2 * rad, 2 * rad), g)
        p.end()


class CodeCard(QWidget):
    """The agent's last edit as its own patch has it (the owner, 2026-10-20: "i want to see the real edits too in the notch like this
    one", the Claude app's diff; notch.code_card of the session's diff). Above: the file's path from its project, "+N −M" and Copy (the
    whole path). Then each row: its line number in the gutter, the removed on a red tint, the added on a green one, with their sign in
    that hue, the lines around them plain; a long line wraps under its own text (at a word, else anywhere); hunks apart by a hairline
    (the numbers' jump says what lies between); the rows Core didn't send, counted. Monospace for the code only."""
    HEAD, PAD, HUNK, MORE = 30, 2, 13, 22     # the header, a row's air above and below, the gap between hunks, the "N more lines" row
    copied = Signal(str)

    def __init__(self):
        super().__init__()
        self.card, self.cwd, self._laid = None, "", None
        self.setAccessibleName("The last edit")
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)  # (its wrapped rows: the agent view measures it at the width it has, _agent_height)
        self.setSizePolicy(sp)
        self.copy_btn = IconButton("copy", "Copy the file's path", size=22)
        self.copy_btn.setParent(self)
        self.copy_btn.clicked.connect(lambda: self.copied.emit(self.card["path"]) if self.card else None)
        self.hide()

    def set_card(self, card, cwd=""):
        self.card, self.cwd, self._laid = card, cwd or "", None
        self.setVisible(bool(card))
        if card:
            counts = f": +{card['added']} −{card['removed']}" if card["added"] is not None else ""
            self.copy_btn.setToolTip(f"Copy {card['path']}")
            self.setToolTip(card["path"])
            self.setAccessibleDescription("\n".join([self.shown_path() + counts] + [f"{s} {t}" for s, _o, _n, t in card["rows"] if s != "@"]))
        self.updateGeometry()
        self.update()

    def shown_path(self):
        """The file from its project ("core/qtnotch.py" style, the OS's own separators); outside it, the whole path."""
        p, root = str(self.card["path"] if self.card else ""), self.cwd.rstrip("\\/")
        return os.path.relpath(p, root) if root and os.path.isabs(p) and os.path.normcase(p).startswith(os.path.normcase(root) + os.sep) else p

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(w)[0] if self.card else 0

    def sizeHint(self):
        return QSize(480, self.heightForWidth(self.width() if self.width() > 100 else 520))

    def resizeEvent(self, e):
        self.copy_btn.move(self.width() - self.copy_btn.width() - 6, (self.HEAD - self.copy_btn.height()) // 2)
        super().resizeEvent(e)

    def _fonts(self):
        mono = font(11.5, mono=True)
        return mono, QFontMetricsF(mono)

    def _layout(self, w):
        """(height, [(sign, top, height, number words, its QTextLayout or None)]) at width w, kept until the card or the width changes."""
        if self._laid and self._laid[0] == w:
            return self._laid[1]
        mono, fm = self._fonts()
        nums = [x for s, o, n, _t in self.card["rows"] if s != "@" for x in (o, n) if x is not None]
        gutter = fm.horizontalAdvance("8" * max(2, *(len(str(x)) for x in nums))) + 14 if nums else 8  # (no numbers: no gutter)
        text_x = gutter + fm.horizontalAdvance("+") + 10
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        rows, y = [], float(self.HEAD)
        for sign, old, new, text in self.card["rows"]:
            if sign == "@":  # (the first under the header: nothing; a later one: a hairline gap, the numbers jumping say the rest)
                if rows:
                    rows.append((sign, y, self.HUNK, "", None))
                    y += self.HUNK
                continue
            lay = QTextLayout(text.replace("\t", "    ")[:1000], mono)
            lay.setTextOption(opt)
            lay.beginLayout()
            lines = 0
            while True:
                line = lay.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(40.0, w - text_x - 10))
                line.setPosition(QPointF(0, lines * fm.lineSpacing()))
                lines += 1
            lay.endLayout()
            h = max(1, lines) * fm.lineSpacing() + 2 * self.PAD
            num = old if sign == "-" else new  # (the screenshot's way: a removed line by its old number, the rest by the new)
            rows.append((sign, y, h, "" if num is None else str(num), lay))
            y += h
        if self.card["more"]:
            rows.append(("more", y, self.MORE, f"{self.card['more']} more lines in this edit", None))
            y += self.MORE
        self._laid = (w, (int(y + 6), rows, gutter, text_x))
        return self._laid[1]

    def paintEvent(self, _e):
        c = self.card
        if not c:
            return
        w = self.width()
        _h, rows, gutter, text_x = self._layout(w)
        mono, fm = self._fonts()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(QPen(QColor(CARD_EDGE), 1))
        p.setBrush(QColor(CODE_BG))
        p.drawPath(frame)
        p.setClipPath(frame)  # (the tints keep to the card's rounded corners)
        left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        right = w - self.copy_btn.width() - 14
        p.setFont(font(12, 700))
        hm = p.fontMetrics()
        if c["added"] is not None:
            for text, color in ((f"−{c['removed']}", K.RED_TEXT), (f"+{c['added']}", K.GREEN_TEXT)):  # right to left: "+N −M"
                right -= hm.horizontalAdvance(text)
                p.setPen(QColor(color))
                p.drawText(QRectF(right, 0, hm.horizontalAdvance(text) + 2, self.HEAD), left, text)
                right -= 8
        p.setFont(font(12, 600))
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(10, 0, right - 14, self.HEAD), left, p.fontMetrics().elidedText(self.shown_path(), Qt.TextElideMode.ElideMiddle,
                                                                                           int(right - 18)))
        p.fillRect(QRectF(1, self.HEAD - 1, w - 2, 1), QColor(CARD_EDGE))
        tint = {"-": K.RED_TEXT, "+": K.GREEN_TEXT}
        for sign, top, h, words, lay in rows:
            if sign == "@":  # (where the next hunk starts: lines skipped between)
                p.fillRect(QRectF(10, top + h / 2, w - 20, 1), QColor(CARD_EDGE))
                continue
            if sign == "more":
                p.setFont(font(10.5, 500))
                p.setPen(QColor(TEXT_3))
                p.drawText(QRectF(10, top, w - 20, h), left, words)
                continue
            hue = tint.get(sign)
            if hue:
                p.fillRect(QRectF(1, top, w - 2, h), K.alpha(hue, 0.16))
            p.setFont(mono)
            p.setPen(K.alpha(hue, 0.78) if hue else QColor(TEXT_3))  # (a number on a tint takes its hue: never grey on colour)
            p.drawText(QRectF(4, top + self.PAD, gutter - 10, fm.lineSpacing()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, words)
            if hue:
                p.setPen(QColor(hue))
                p.drawText(QRectF(gutter, top + self.PAD, text_x - gutter, fm.lineSpacing()), left, "−" if sign == "-" else "+")
            p.setPen(QColor(TEXT if hue else TEXT_2))
            lay.draw(p, QPointF(text_x, top + self.PAD))
        p.end()


class Meters(QWidget):
    """Dotpals' bars under the card (notch.meter_rows of the session's meters): each one's words, and its bar filled as far as Core says
    (the plan amber, the context green, the limits cyan) on a dark track; where the agent's log doesn't say how far, the words alone:
    never a guessed bar."""
    ROW, BAR = 20, 120
    FILLS = {"plan": K.AMBER, "context": K.GREEN_TEXT}   # anything else (the limits): the notch's cyan

    def __init__(self):
        super().__init__()
        self.rows = []
        self.setAccessibleName("Plan, context and limits")
        self.hide()

    def set_rows(self, rows):
        self.rows = list(rows)
        self.setVisible(bool(self.rows))
        self.setFixedHeight(len(self.rows) * self.ROW + 2)
        self.setAccessibleDescription("\n".join(words for _kind, words, _fill in self.rows))
        self.update()

    def bar_rect(self, i):
        return QRectF(self.width() - self.BAR, 1 + i * self.ROW + (self.ROW - 5) / 2, self.BAR, 5)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(12))
        fm, w = p.fontMetrics(), self.width()
        for i, (kind, words, fill) in enumerate(self.rows):
            room = w - (self.BAR + 12 if fill is not None else 0)
            p.setPen(QColor(TEXT_2))
            p.drawText(QRectF(0, 1 + i * self.ROW, room, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(words, Qt.TextElideMode.ElideRight, int(room)))
            if fill is None:
                continue
            bar = self.bar_rect(i)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TRACK))
            p.drawRoundedRect(bar, 2.5, 2.5)
            if fill > 0:
                p.setBrush(QColor(self.FILLS.get(kind, ACCENT)))
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * fill, bar.height()), 2.5, 2.5)
        p.end()


class Turns(QWidget):
    """Chat's thread, and each box of turns in it, measured at the width it has. Qt's own size hint asks a box's layout for its height at
    the widest turn's natural width (504 px), a second width beside the one it is shown at (474), and the layout keeps only one: each
    change in the thread then wrapped every turn's words twice (2026-10-23: ~80 ms each time a reply's next words came)."""

    def sizeHint(self):
        lay, w = self.layout(), self.width()
        if lay is None or w < 50 or not lay.hasHeightForWidth():
            return super().sizeHint()
        return QSize(w, lay.totalHeightForWidth(w))


class PetView(QWidget):
    """The notch's pet in a card: drawn from the notch's PetArt and mood (the notch repaints it)."""

    def __init__(self, notch, size):
        super().__init__()
        self.notch = notch
        self.badge = None  # "ask" | "done" | "failed": the 2026-10-26 recording's corner badge on Home's card
        self.setFixedSize(size, size)
        self.setAccessibleName("Monday's pet")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_badge(self, badge):
        if badge != self.badge:
            self.badge = badge
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        self.notch.mascot().draw(p, QRectF(self.rect()), self.notch.clock(), self.notch.mood)
        if self.badge:
            paint_badge(p, QPointF(self.width() * 0.14, self.height() * 0.2), self.badge)
        p.end()


BADGES = {"ask": "#F2A33A", "done": "#2FB36B", "failed": "#E5484D"}   # (the recording's orange "!" and green ✓; red for a failure)


def paint_badge(p, at, kind, r=7.5):
    """A round badge drawn, not typed: an exclamation mark, a check or a cross on the state's colour, ringed in the card's black."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(CARD_BG), 2.0))
    p.setBrush(QColor(BADGES[kind]))
    p.drawEllipse(at, r, r)
    ink = QPen(QColor("#1B1206" if kind == "ask" else "#FFFFFF"), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(ink)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y = at.x(), at.y()
    if kind == "ask":
        p.drawLine(QPointF(x, y - r * 0.5), QPointF(x, y + r * 0.08))
        p.drawPoint(QPointF(x, y + r * 0.48))
    elif kind == "done":
        path = QPainterPath(QPointF(x - r * 0.42, y + r * 0.02))
        path.lineTo(QPointF(x - r * 0.1, y + r * 0.34))
        path.lineTo(QPointF(x + r * 0.45, y - r * 0.32))
        p.drawPath(path)
    else:
        d = r * 0.34
        p.drawLine(QPointF(x - d, y - d), QPointF(x + d, y + d))
        p.drawLine(QPointF(x - d, y + d), QPointF(x + d, y - d))
    p.restore()


CMD_BG, CMD_INK = "#2A2622", "#ECE7E1"   # the command's field (the recording's warm dark grey, #2E2924 sampled) and its words
CARD_TINTS = {"ask": "#C8862E", "done": "#2C9A78", "failed": "#B5474B"}  # the light under Home's card for each badge


def boot_time():
    """When the PC started (psutil: Windows' own tick count), the mark of "until shutdown" for the clipboard's copies."""
    try:
        import psutil
        return psutil.boot_time()
    except Exception:
        return time.time() - time.monotonic()


def agent_colour(name):
    """An agent's own colour (its pill's) for the dot by its name on Home's card, else the waiting amber."""
    return PILL_COLOURS.get(str(name or "").strip().lower().replace(" ", "_"), "wait")


class CmdLine(QWidget):
    """The command an agent asks to run, as the 2026-10-26 recording shows it: one line of monospace in a warm dark field (its whole text
    in the tooltip and for a screen reader)."""

    def __init__(self):
        super().__init__()
        self.text = ""
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, text):
        self.text = " ".join(str(text or "").split())
        self.setToolTip(self.text if len(self.text) > 60 else "")
        self.setAccessibleName(f"The command: {self.text}")
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CMD_BG))
        p.drawRoundedRect(r, 7, 7)
        p.setFont(font(11.5, 500, mono=True))
        p.setPen(QColor(CMD_INK))
        room = r.adjusted(10, 0, -10, 0)
        p.drawText(room, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideRight, int(room.width())))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.notch.poke()


WARDROBE_STARS_S = 1.2   # the burst of stars round the pet as it puts an outfit on (the recording's); none with reduced motion
WARDROBE_PET = 0.78      # the pet in the wardrobe's squares, small enough that the tallest hat (the witch's, the ears) stays inside


class Outfit(K._Keyed, K.QAbstractButton):
    """One of the wardrobe's outfits (the 2026-10-26 recording's row): the owner's own pet wearing it, small, on a dark tile; the one on it
    ringed; "auto" says AUTO under the season's; one its kind can't wear (a hat on the witch's own) dimmed, and says why."""

    def __init__(self, notch, key):
        super().__init__()
        self.n, self.key, self.on = notch, key, False
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover = K.Hover(self)

    def enterEvent(self, e):
        self._hover.go(True)

    def leaveEvent(self, e):
        self._hover.go(False)

    def wearable(self):
        return not (self.n.p["kind"] == "witch" and wear_now(self.key) in WEAR_HATS)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#C9CCD2" if self.on else "#2A2D31"), 1.6 if self.on else 1))
        p.setBrush(mix("#141618", "#1F2226", self._hover.value))
        p.drawRoundedRect(r, 8, 8)
        if not self.wearable():
            p.setOpacity(0.35)
        art = self.n.outfit_art(self.key)
        if art is None:  # "none": the pet as it is, crossed out
            p.setPen(QPen(QColor(TEXT_2), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.center(), 8, 8)
            p.drawLine(r.center() + QPointF(-5.5, 5.5), r.center() + QPointF(5.5, -5.5))
        else:
            art.draw(p, r.adjusted(1, 2 if self.key != "auto" else 0, -1, 0 if self.key != "auto" else -6), 1.0, "idle", scale=WARDROBE_PET)
        if self.key == "auto":
            p.setOpacity(1.0)
            p.setPen(QColor(TEXT))
            p.setFont(font(7.5, 800))
            p.drawText(QRectF(r.left(), r.bottom() - 10, r.width(), 9), Qt.AlignmentFlag.AlignCenter, "AUTO")
        self._ring(p, r, 8)
        p.end()


class Dressed(PetView):
    """The wardrobe's big pet: the owner's pet wearing what is on it now, and a burst of stars as it puts something on (a PetView: the
    notch moves it with the others)."""

    def __init__(self, notch, size):
        super().__init__(notch, size)
        self.setAccessibleName("Your pet, as it's dressed")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = time.monotonic() - self.notch.dressed_at
        self.notch.art.draw(p, QRectF(self.rect()), self.notch.clock(), "happy" if t < WARDROBE_STARS_S else "idle", scale=WARDROBE_PET)
        if t < WARDROBE_STARS_S and not reduced_motion():  # (Coucou's: stars fly out and fade)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(7):
                a = i * 2 * math.pi / 7 + 0.4
                d = 18 + 34 * (t / WARDROBE_STARS_S)
                c = QColor("#FFC93C")
                c.setAlphaF(max(0.0, 1 - t / WARDROBE_STARS_S))
                p.setBrush(c)
                x, y = self.width() / 2 + math.cos(a) * d, self.height() / 2 - 6 + math.sin(a) * d * 0.8
                p.drawPolygon(K._star_points(x, y, 3.6))
        p.end()


class Handle(QWidget):
    """The open notch's handle: the line's thread in the middle of its bottom edge (PAD under the cards). Dragged up or clicked, the notch
    closes to the bar (one of its three steps; the bar's own handle folds it on to the line). Its own widget, so its hand cursor and its
    clicks stay on it."""
    W, H = 72, 10  # its reach; the thread drawn in it is HANDLE_W x HANDLE_H

    def __init__(self, notch):
        super().__init__(notch)
        self.n, self._from, self._over = notch, None, False
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Close the notch to its bar (drag up or click)")
        self.setToolTip("Drag up or click: close · on the bar, slide up again: just a line")
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(K.alpha("#9AA0A6", 0.6 if self._over else 0.35))
        p.drawRoundedRect(QRectF((self.W - HANDLE_W) / 2, (self.H - HANDLE_H) / 2, HANDLE_W, HANDLE_H), HANDLE_H / 2, HANDLE_H / 2)
        p.end()

    def enterEvent(self, e):
        self._over = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._over = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._from = e.position().y()

    def mouseMoveEvent(self, e):
        if self._from is not None and e.position().y() - self._from <= -SWIPE_PX / 3:
            self._from = None
            self.n._fold(True)

    def mouseReleaseEvent(self, e):
        if self._from is not None and e.button() == Qt.MouseButton.LeftButton:
            self._from = None
            self.n.close_notch()


class Dots(QWidget):
    """One dot per live session of the owner's (the owner, 2026-10-20: "a dot type of things if there are multiple chats or sessions"):
    the one shown bright; a click on another shows that one (its key: keys[i])."""
    clicked = Signal(int)
    R, GAP = 3.0, 9.0

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set([], 0)

    def set(self, keys, at):
        self.keys, self.n, self.at = list(keys), len(keys), at
        self.setFixedSize(int(self.GAP * max(self.n, 1) + self.R * 2), 10)
        self.setAccessibleName(f"Session {at + 1} of {self.n}: click a dot to show another")
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.n):
            p.setBrush(QColor(TEXT if i == self.at else K.alpha(TEXT_2, 0.45)))
            p.drawEllipse(QPointF(self.R + 1 + i * self.GAP, self.height() / 2), self.R, self.R)
        p.end()

    def mouseReleaseEvent(self, e):
        i = int((e.position().x() - 1) // self.GAP)
        if 0 <= i < self.n and i != self.at:
            self.clicked.emit(i)


class Pill(QWidget):
    """One of the dock's pills (the recording's 2 x 2, 26 tall): a little face in the tool's colour (or the program's own logo), its name in
    the middle, a faint edge in its colour; an Active pill also has the dot of its state as Core last found it. A click goes to it."""
    clicked = Signal()

    def __init__(self, name, color, detail="", exe=None, state=None):
        super().__init__()
        self.name, self.art, self.logo, self.color, self.state = name, K.pal(color), None, color, state  # state: (dot, words) or None
        self.alert = False  # its agent asks the owner now: the 2026-10-26 recording's orange "!" on the pill's corner
        if exe and os.path.isfile(exe):  # the program's own logo (the owner, 2026-10-17: "each IDE's own logo"), from the OS
            from PySide6.QtCore import QFileInfo
            from PySide6.QtWidgets import QFileIconProvider
            ic = QFileIconProvider().icon(QFileInfo(exe))
            self.logo = None if ic.isNull() else ic
        self.setFixedHeight(PILL_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        words = detail or (state[1] if state else "")
        self.setAccessibleName(f"{name}{': ' + words if words else ''}")
        self.setToolTip(f"{name}: {words}" if words else name)
        self._hover = K.Hover(self)

    def sizeHint(self):  # (its logo, its whole name, its dot: what paintEvent draws)
        return QSize(int(40 + QFontMetricsF(font(10.5)).horizontalAdvance(self.name) + (12 if self.state else 0) + 8), PILL_H)

    def enterEvent(self, e):
        self._hover.go(True)

    def leaveEvent(self, e):
        self._hover.go(False)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(K.alpha(self.color, 0.36 + 0.3 * self._hover.value), 1))
        p.setBrush(mix("#0C0C10", "#1D1E23", self._hover.value))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self.logo is not None:
            self.logo.paint(p, int(r.left() + 9), int(r.center().y() - 8), 16, 16)
        else:
            self.art.draw(p, QRectF(r.left() + 3, r.top() - 4, 32, 32), 1.0, "idle")
        dot = 12 if self.state else 0
        if self.state:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(K.Dot.COLORS.get(self.state[0], self.state[0])))
            p.drawEllipse(QPointF(r.right() - 9, r.center().y()), 2.5, 2.5)
        p.setPen(QColor(TEXT_2 if not self._hover.value else TEXT))
        p.setFont(font(10.5))
        room = QRectF(r.left() + 36, r.top(), r.width() - 40 - dot, r.height())
        p.drawText(room, Qt.AlignmentFlag.AlignCenter, p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, int(room.width())))
        if self.alert:
            paint_badge(p, QPointF(r.right() - 7, r.top() + 4), "ask", 5.5)
        p.end()


# The chat's providers in the 2026-10-26 recording's order, each with its dot (Core's GET /api/models names them; one Core doesn't list isn't
# shown: no LM Studio, Monday has no way to it)
PROVIDERS = {"anthropic": ("Anthropic", "#E07B53"), "gemini": ("Google", "#4A8DF6"), "openai": ("OpenAI", "#3FB38A"),
             "openrouter": ("OpenRouter", "#7C83F2"), "ollama": ("Ollama", "#F2C94C"), "xai": ("xAI", "#C8CCD2"),
             "deepseek": ("DeepSeek", "#4D6BFE"), "mistral": ("Mistral", "#FA7A35"), "monday": ("Auto", "#9E83F2")}
MODEL_ROWS = 5            # models listed at once under the chips (the rest scroll)


def provider_of(m):
    return PROVIDERS.get(str((m or {}).get("provider") or ""), (str((m or {}).get("provider") or "Other").capitalize(), ICON))


def is_local(m):
    """An Ollama model on this PC (not one of Ollama's cloud ones): the recording's "Even local"."""
    name = str((m or {}).get("id") or "")
    return (m or {}).get("provider") == "ollama" and not re.search(r"[-:]cloud\b", name)


class ModelRow(K._Keyed, K.QAbstractButton):
    """One model in the chat's model menu: its name, "on this PC" for a local one, the one in use lit in its provider's colour with a check."""

    def __init__(self, text, color, on=False, local=False, name=None, now=False):
        super().__init__()
        self.color, self.on, self.local, self.now = color, on, local, now  # now: Automatic's model of the moment (said, not checked)
        self.setText(text)
        self.setAccessibleName(name or text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(24)
        self._hover = K.Hover(self)

    def enterEvent(self, e):
        self._hover.go(True)

    def leaveEvent(self, e):
        self._hover.go(False)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.on or self._hover.value:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mix("#17191c", self.color, 0.26) if self.on else mix("#17191c", "#26292e", self._hover.value))
            p.drawRoundedRect(r, 6, 6)
        p.setFont(font(12, 500))
        p.setPen(QColor(TEXT if self.on or self._hover.value else TEXT_2))
        right = r.right() - (22 if self.on else 8)
        if self.local or self.now:
            p.setFont(font(10, 500))
            tag = " · ".join(x for x in ("now, by Automatic" if self.now else "", "on this PC" if self.local else "") if x)
            tw = p.fontMetrics().horizontalAdvance(tag)
            p.setPen(QColor(TEXT_3))
            p.drawText(QRectF(right - tw, r.top(), tw, r.height()), Qt.AlignmentFlag.AlignVCenter, tag)
            right -= tw + 10
            p.setFont(font(12, 500))
            p.setPen(QColor(TEXT if self.on or self._hover.value else TEXT_2))
        room = QRectF(r.left() + 9, r.top(), right - r.left() - 9, r.height())
        p.drawText(room, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(room.width())))
        if self.on:  # its check, drawn
            pen = QPen(QColor(TEXT), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            x, y = r.right() - 13, r.center().y()
            path = QPainterPath(QPointF(x - 4, y))
            path.lineTo(QPointF(x - 1.2, y + 3))
            path.lineTo(QPointF(x + 4, y - 3.2))
            p.drawPath(path)
        self._ring(p, r, 6)
        p.end()


class ModelPanel(QWidget):
    """The chat's model menu as the 2026-10-26 recording draws it (opened by the model pill, in the thread's place until a pick or the pill
    again): a chip per provider Core lists, its own dot, the chosen one tinted; under them its models, the one in use lit with its check, a
    local Ollama model marked "on this PC"; a provider with no key says so and where to add it. "Loading models…" until Core's list comes.
    A pick: POST /api/models/select, then the pill from Core's answer (never assumed)."""

    def __init__(self, notch):
        super().__init__()
        self.n, self.provider, self.doc, self.loading = notch, None, None, False
        self.setAccessibleName("Pick the chat model")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        v.addLayout(self.chips)
        self.rows_box = QWidget()
        self.rows = QVBoxLayout(self.rows_box)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.rows_box)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(MODEL_ROWS * 26)
        v.addWidget(self.scroll)
        self.note = label("", 11.5, 400, TEXT_2, wrap=True)
        self.note.hide()
        v.addWidget(self.note)
        self.link = Button("Open Integrations", "link", height=22, name="Open Monday's Integrations to add the key")
        self.link.clicked.connect(lambda: self.n.open_window("integrations"))
        self.link.hide()
        v.addWidget(self.link, 0, Qt.AlignmentFlag.AlignLeft)
        v.addStretch(1)

    def _models(self):
        return [m for m in (self.doc or {}).get("models") or [] if m.get("for_chat") is not False]

    def fill(self, doc=None):
        if doc is not None:
            self.doc, self.loading = doc, False
        models = self._models()
        sel = next((m for m in models if m.get("selected")), None)
        known = [k for k in PROVIDERS if any(m.get("provider") == k for m in models)] +             sorted({m.get("provider") for m in models if m.get("provider") not in PROVIDERS})
        ready = [k for k in known if any(m.get("provider") == k and m.get("available") for m in models)]
        unset = [k for k in known if k not in ready]  # no key in Monday: one chip says which (nine chips didn't fit the recording's row)
        if self.provider not in ready + (["_unset"] if unset else []):
            self.provider = (sel or {}).get("provider") if (sel or {}).get("provider") in ready else (ready[0] if ready else "_unset" if unset else None)
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for k in ready + (["_unset"] if unset else []):
            name, color = (f"{len(unset)} not set up", "#6b6f75") if k == "_unset" else PROVIDERS.get(k, (str(k).capitalize(), ICON))
            c = Chip(name, "lit" if k == self.provider else "dark", dot=color)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.setAccessibleName((f"Providers with no key in Monday: {', '.join(provider_of({'provider': u})[0] for u in unset)}" if k == "_unset"
                                 else f"{name}'s models") + (" (shown)" if k == self.provider else ""))
            c.mousePressEvent = lambda e, k=k: self.pick_provider(k)
            self.chips.addWidget(c)
            c.show()  # (made while the menu may be put by: shown now, not on Qt's next turn)
        self.chips.addStretch(1)
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        usable = sorted((m for m in models if m.get("provider") == self.provider and m.get("available")), key=lambda m: not is_local(m))
        auto_now = (self.doc or {}).get("effective") if (sel or {}).get("id") == "auto" else None  # Automatic's model of the moment
        color = PROVIDERS.get(self.provider, ("", ICON))[1]  # (an Ollama model on this PC first: the recording's "Even local")
        if self.loading and not models:
            self.note.setText("Loading models…")
        elif not models:
            self.note.setText("No chat model answers right now.")
        elif self.provider == "_unset":
            self.note.setText(f"No key for these in Monday yet: {', '.join(provider_of({'provider': u})[0] for u in unset)}. Add one in "
                              "Integrations, and its models show here.")
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        if self.note.text():  # (a wrapped label asks for the height of a narrow one: told its real width, the menu is as tall as its words)
            self.note.setFixedHeight(self.note.heightForWidth(self.width() if self.width() > 100 else CHAT_W))
        self.link.setVisible(self.provider == "_unset")
        for m in usable:
            now_ = bool(auto_now) and auto_now in (m.get("id"), m.get("name"))
            row = ModelRow(m.get("name") or m.get("id"), color, on=bool(m.get("selected")), local=is_local(m), now=now_,
                           name=f"{m.get('name') or m.get('id')}" + (", on this PC" if is_local(m) else "") + (" (in use)" if m.get("selected") else "")
                           + (" (Automatic's choice now)" if now_ else ""))
            row.clicked.connect(lambda _=False, i=m.get("id"): self.n.pick_model(i))
            self.rows.addWidget(row)
            row.show()
        self.rows.addStretch(1)
        self.scroll.setFixedHeight(min(MODEL_ROWS, max(1, len(usable))) * 26)  # (as tall as its rows: the review)
        self.scroll.setVisible(bool(usable))
        if self.isVisible() and self.n.opened and self.n.view == "chat":
            QTimer.singleShot(0, self.n._go)  # (another provider's list: the card to its height, once Qt has laid the new rows out)

    def pick_provider(self, key):
        self.provider = key
        self.fill()


class SendButton(K._Keyed, K.QAbstractButton):
    """The prompt's round white send button with its dark arrow (the recording's), inside the prompt at its right."""

    def __init__(self, prompt):
        super().__init__(prompt)
        self.setFixedSize(30, 30)
        self.setAccessibleName("Send")
        self.setToolTip("Send (Enter)")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        prompt.resized.connect(self._place)

    def _place(self):
        pr = self.parentWidget()
        self.move(pr.width() - self.width() - 7, (pr.height() - self.height()) // 2 if pr.height() < 60 else pr.height() - self.height() - 7)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        stop = getattr(self, "stopping", False)  # the window's composer: Stop while a task of this chat runs and the box is empty
        p.setBrush(QColor(K.RED if stop else "#f4f4f5" if self.isEnabled() else "#5a5b60"))
        p.drawEllipse(r)
        K.paint_icon(p, "stop" if stop else "up", QRectF(r.center().x() - 7, r.center().y() - 7, 14, 14), "#ffffff" if stop else "#111214",
                     2.6)
        self._ring(p, r, r.height() / 2)
        p.end()


class Prompt(QPlainTextEdit):
    """The notch's prompt ("Ask me anything..."): Enter sends, Shift+Enter a new line (it grows to five), Esc clears, then closes."""
    send = Signal()
    escape = Signal()
    clicked = Signal()
    resized = Signal()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Ask me anything...")
        self.setAccessibleName("Ask Monday")
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # (it grows to five lines; past them the caret scrolls it)
        self.setStyleSheet("QPlainTextEdit { background: qradialgradient(cx:0.5, cy:0.75, radius:0.62, fx:0.5, fy:0.75, stop:0 #3a3a62, "
                           "stop:1 #242428); border: 1px solid #2b2c36; border-radius: 12px; "  # (the recording's: #242428 at its ends,
                           f"padding: 6px 44px 6px 12px; color: {TEXT}; }}")  # #38385c in its middle: the indigo light on it)
        self.textChanged.connect(self._grow)
        self._grow()

    def _grow(self):
        lines = min(5, max(1, self.document().blockCount()))  # one line per paragraph, up to five, then it scrolls
        self.setFixedHeight(int(22 * lines + 18))

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send.emit()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(e)


class Notch(QWidget):
    """The notch. win: Monday's window (its Bridge, store, ear, hotkeys, pages); it lives as long as the desktop app."""
    uploaded = Signal(object, object)   # (the file's state, Core's answer or None, its error)
    progress = Signal(int)
    places_read = Signal(object)        # the open IDE windows, read on a thread
    source_read = Signal(str, object)   # the island's own sources, read on a thread: "plan" (watch.plan_use), "github" (github.read)

    def __init__(self, win, show_on_start=True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.win, self.bridge = win, win.bridge
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("Monday's notch")
        self.setAccessibleName("Monday's notch")
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.p = prefs(win.store.get("pet"))
        self.art = PetArt(self.p)
        self.state = State()
        self.talk: list[dict] = []
        self._messages: list[dict] = []        # Monday's chat as Core sent it (chat_turns reads the turns from these)
        self.models: dict = {}
        self.mood, self.shown = "idle", ("idle", STATES["idle"][1], "")
        self.view, self.opened, self.peek = "home", False, False
        self.places: list[dict] = []
        self.hooks, self.hooks_asking = None, False   # Monday's hooks in Claude Code as Core said them; asked and not answered yet
        self._asks_drawn = None                # the agent ask Home's Deny / Always / Allow were built for
        self._card_seen = None                 # the finished card the owner put away with its OK (home_card_now)
        self.update_offer = None               # a newer Monday Core found in the update channel (docs/adr/0010): Home's card until Later
        self._update_words = ""                # what the card's Update is doing, or why it couldn't (said on the card)
        self._flow, self._flow_agents = set(), []  # what the owner allowed here: Monday's task ids; (agent, folder, when) of their agents'
        self._finished = None                  # ...and its end, as Core reported it: Home's "finished" card (the 2026-10-26 recording)
        self._q_for, self._q_at, self._q_picks = None, 0, {}  # Claude Code's questions on show: whose, which one, the owner's picks
        self._q_typing, self.q_field = False, None  # "Other…": the owner typing their own answer to the question on show
        self._chat_settles = 0                 # how many more times Chat may be sized again for its thread's last change
        self._follow_thread = True             # Chat's thread keeps its newest words in sight, until the owner scrolls up to read
        self._older, self._loading_older = False, False  # Core has messages older than the first loaded (has_more); a page on its way
        self._bubbles: list = []               # Chat's stored turns as drawn, oldest first: (key, words, widget, its label) (_fill_thread)
        self._tail: list = []                  # ...and what is still being said under them: the owner's words on their way, a reply's so far
        self._live: dict = {}                  # replies being written: stream id -> their words so far (MESSAGE_DELTA)
        self._live_model: dict = {}            # ...and the model writing each: another taking the reply over starts its words again (on_event)
        self._chat_drawn = None                # what Chat's state line and buttons were last drawn for (_show_chat_state)
        self._stepped = None                   # when the spring last moved (_step moves it by the time that really passed)
        self.tools: dict = {}                # /api/ai-tools by key: the pills' states (Core's last look at each agent and tool)
        self._logos: dict = {}                 # program -> its own logo as a mascot (K.LogoPal), or None (mascot())
        self._app_exes: dict = {}              # an app's process names -> (its running program, when looked) (_app_exe)
        self._pid_names: dict = {}             # a process id -> its program's name, for the app in front (_front_key)
        self.attach: list[str] = []            # file ids going with the next prompt ("Continue in chat")
        self.uploading = False
        self.menu_open = False
        self.prev_fg = None
        self.waits: dict = {}                  # approvals not yet notified about (_notify, _waits_due)
        self.toast = None                      # (title, words) -> the OS's notification: core/desktop.py gives the tray's
        self.story = None                      # the agent view's Story or Steps while one shows: {"live": "story" | "steps", "key", ...}
        self.agent_talk = (None, [], 0.0)      # (session key, its chat's turns, read at) for the agent view (GET /api/agents/chat)
        self.rewinder = None                   # Rewind's window, while there is one
        self._agent_drawn = None               # what the agent view last drew (it is rebuilt only when that changes)
        self._t0 = time.monotonic()
        self.ui = I.ui(win.store.get(I.UI_KEY))   # the look chosen in Settings → General, each look's own layout and place (docs/adr/0011)
        self.mode = self.ui["mode"]
        self.booting = bool(show_on_start)      # the start: Core hasn't said it is ready yet (the eyes sleepy, no greeting before it)
        self._core_phase, self._greet_until, self._boot_t0 = None, 0.0, time.monotonic()
        at = self._mode_at() or {}
        self.cx, self.top = at.get("cx"), at.get("y")
        self.w_spring, self.h_spring = Spring(CLOSED[0]), Spring(CLOSED[1])
        self.closing = None                    # (from (w, h), started) while it closes on the curve
        stored = win.store.get(ISLAND_KEY)
        self.island = dict(stored) if isinstance(stored, dict) else {}  # the island's widgets: their layout, the note, the focus timer
        self.clips: list[dict] = self._clips_load()  # every copy since the PC started (island.clips_load), for the clipboard tile and view
        self.last_task = None                  # Monday's last finished task (island.last_task), for the AI tile
        self.media = Q.MediaWatch()            # what plays on this PC (Windows' media sessions), for the music tile
        self.media.on_change = self._media_changed
        self.quit = None                       # the tray's Quit Monday (core/desktop.py), for the island's power button; none: no button
        self.card_session = None               # the session the AI tile shows (a tap opens its view, a sideways swipe the next)
        self._clip_on = False                  # the clipboard watched (from the start, wherever its widget is: the owner, 2026-10-26)
        self._menu_at = None                   # the empty cell whose + opened the widget menu (its pick goes there)
        self.plan = self.github = None         # the island's own sources (None: not read yet): plan use, GitHub through the owner's gh
        self.usage = None                      # every AI's limits (core/usage.py cards; None: not read yet): one read for every look
        self._usage_fails, self._usage_next, self._usage_told, self._usage_asked = {}, {}, {}, 0.0  # per source: failures in a row, when
        # it may be asked again (usage.wait_for), the nudges given (usage.nudges); when the last read began
        self._read_at, self._reading = {}, set()
        self._plan_card_at = None              # the plan pill's click: the plan on Home's card until its OK
        self.dressed_at, self._outfit_art = -99.0, {}  # the wardrobe: when an outfit last went on (its stars); each outfit's tile art
        self._build()
        self.setStyleSheet(NOTCH_QSS)
        self.uploaded.connect(self._got_upload)
        self.progress.connect(self._got_progress)
        self.places_read.connect(self._got_places)
        self.source_read.connect(self._got_source)
        self._anim = QTimer(self)
        self._anim.setInterval(ANIM_MS)
        self._anim.timeout.connect(self._step)
        self._frames = QTimer(self)
        self._frames.timeout.connect(self._tick)
        self._hover_t = QTimer(self)
        self._hover_t.setSingleShot(True)
        self._hover_t.timeout.connect(lambda: self.open(peek=True) if not self.opened and self.underMouse() else None)
        self._over = False  # the mouse on the closed bar: it says which session and folder (bar_words)
        self._close_t = QTimer(self)
        self._close_t.setSingleShot(True)
        self._close_t.timeout.connect(self._auto_close)
        self._waits_t = QTimer(self)
        self._waits_t.setInterval(2000)
        self._waits_t.timeout.connect(self._waits_due)
        self._waits_t.start()
        self._save_t = QTimer(self)
        self._save_t.setSingleShot(True)
        self._save_t.setInterval(SAVE_AFTER_MS)
        self._save_t.timeout.connect(self._moved)
        self._live_t = QTimer(self)
        self._live_t.setSingleShot(True)
        self._live_t.setInterval(LIVE_FLUSH_MS)
        self._live_t.timeout.connect(lambda: self._fill_thread() if self.opened and self.view == "chat" else None)
        self._island_t = QTimer(self)
        self._island_t.setInterval(ISLAND_TICK_MS)
        self._island_t.timeout.connect(self._island_tick)
        self._focus_t = QTimer(self)  # the focus timer's end, rung even with the notch closed
        self._focus_t.setSingleShot(True)
        self._focus_t.timeout.connect(self._focus_due)
        self._focus_arm()
        self._usage_t = QTimer(self)  # the limits look reads every AI's limits while closed too (at most every USAGE_CLOSED_S)
        self._usage_t.setInterval(60_000)
        self._usage_t.timeout.connect(lambda: self._load_sources(only="usage") if self.mode == "limits" else None)
        self._apply_mode(first=True)  # Home's widgets as the owner left them, in the look they chose (the recording's Default at first)
        self._pets = self.findChildren(PetView)  # (made in _build and again when the tiles change: the frames never walk the whole tree)
        self._watch_clip(True)  # (every copy until the PC shuts down, whether the Clipboard widget is on Home or not: the owner, 2026-10-26)
        QTimer.singleShot(WARM_FONTS_MS, self._warm_fonts)
        self._placing = False
        self._press, self._press_t = None, 0.0  # where and when the left button went down (a click or a drag: notch.dragging)
        self._swipe_from, self._gesture = None, None  # a drag on Home's card between chats; the wheel burst going on (wheelEvent)
        self._natural = touchpad_natural()  # (the touchpad's scrolling direction, so a swipe up is up: wheelEvent)
        self._arriving, self._arrival = False, None   # the start: the bar on its way down from the top edge (_arrive)
        self.lined = self.p["lined"]                  # swiped up: the closed bar folded to a thin line (LINE), as the owner left it
        self._fs = (-1e9, False)                      # (when Windows was last asked whether a full-screen app has the screen, its answer)
        self._links = {}  # a Claude app session's own link, by its transcript (claude_app_link, read once)
        self._open_url = lambda url: QDesktopServices.openUrl(QUrl(url))  # noqa: E731  (the ↗'s link; the check stands in for it)
        self.bridge.event.connect(self.on_event)
        self.bridge.online.connect(self._online)
        if win.hotkeys is not None:
            win.hotkeys.fired.connect(self._hotkey)
        self._place(*self.target_size())
        self.refresh()
        self.desk = None                       # the pet on the desktop (DeskPet), made when it is first shown
        if show_on_start and self.p["visible"] and self.p["on_start"]:
            self.show()
            self._arrive()  # down from the top edge, with a bounce (2026-10-21)
        elif show_on_start and not self.p["desk"]:  # the notch isn't shown at start: the pet is, as one of the two always is
            self.p = prefs({**self.p, "desk": True})
            self.win.keep("pet", self.p)
        if show_on_start:
            self._desk_show()

    # -- what is in it ---------------------------------------------------------------------------------------------------------------
    def _build(self):
        self.body = QWidget(self)
        v = QVBoxLayout(self.body)
        v.setContentsMargins(PAD, TOP_IN, PAD, PAD)
        v.setSpacing(TAB_GAP)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.tab_btns = {}
        for key, icon_name, name in (("home", "home", "Home"), ("chat", "chat", "Chat"), ("drop", "plus", "Drop a file")):
            b = IconButton(icon_name, name, size=28)
            b.setFixedSize(34, 26)  # (the recording's chosen tab is a pill, not a circle)
            b.clicked.connect(lambda _=False, k=key: self.show_view(k))
            self.tab_btns[key] = b
            tabs.addWidget(b)
        # arranging the widgets (the recording's edit mode): Done, and what can be done (new ones come from Settings, the owner 2026-10-25)
        self.done_pill = Q.Lime("Done", "Done arranging your widgets", height=24, width=62)
        self.done_pill.clicked.connect(lambda: self.grid.edit(False))
        self.done_pill.hide()
        tabs.addSpacing(4)
        tabs.addWidget(self.done_pill)
        self.edit_hint = label("Drag to move  ·  new widgets in Settings", 11, 400, TEXT_2)
        self.edit_hint.hide()
        tabs.addSpacing(4)
        tabs.addWidget(self.edit_hint)
        tabs.addStretch(1)
        self.plan_btn = Chip("", "dark", dot=Q.PLAN_GREEN)  # the 2026-10-26 recording's "● Codex 22%": only when an agent's own log said it
        self.plan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plan_btn.mousePressEvent = lambda e: self.show_plan()
        self.plan_btn.hide()
        tabs.addWidget(self.plan_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        tabs.addSpacing(4)
        self.sound = IconButton("volume", "Sounds: on or off", size=28)  # the recording's right side: Sound, Settings, Collapse, Power
        self.sound.clicked.connect(lambda: self.set_pref("sounds", not self.p["sounds"]))
        self.gear = IconButton("gear", "The notch's settings", size=28)
        self.gear.clicked.connect(lambda: self.show_view("settings"))
        self.tab_btns["settings"] = self.gear
        self.collapse = IconButton("collapse", "Fold the notch to its line", size=28)
        self.collapse.clicked.connect(self.collapse_all)
        self.power = IconButton("power", "Quit Monday…", size=28)
        self.power.clicked.connect(self._power)
        self.power.hide()  # (shown once the app gives it the tray's Quit: core/desktop.py)
        for b in (self.sound, self.gear, self.collapse, self.power):
            tabs.addWidget(b)
        self.head = L.IslandHead(self)  # the floating island's header: its page, the eyes, the date and time (hidden in the other looks)
        self.head.hide()
        v.addWidget(self.head)
        self.limit_tabs = L.IslandTabs(self, L.LIMITS_TABS, compact=True)  # the limits look's row over its cards (the recording's)
        self.limit_tabs.hide()
        v.addWidget(self.limit_tabs)
        self.tabs_box = QWidget()  # the classic and limits looks' tabs over the views (the island has its own under them: L.IslandTabs)
        tabs.setContentsMargins(0, 0, 0, 0)
        self.tabs_box.setLayout(tabs)
        v.addWidget(self.tabs_box)
        self.body_lay, self.tabs_lay = v, tabs
        self.views = QStackedWidget()
        self.views.addWidget(self._home())
        self.views.addWidget(self._chat())
        self.views.addWidget(self._drop())
        self.views.addWidget(self._settings())
        self.views.addWidget(self._agent())
        self.note_view, self.clip_view = Q.NoteView(self), Q.ClipView(self)
        for w in (self.note_view, self.clip_view):
            self.views.addWidget(w)
        self.views.addWidget(self._wardrobe())
        self.music_view, self.focus_view = L.MusicView(self), L.FocusView(self)  # the island's own Music and Focus pages (2026-10-29)
        for w in (self.music_view, self.focus_view):
            self.views.addWidget(w)
        v.addWidget(self.views, 1)
        self.island_tabs = L.IslandTabs(self)  # the island's tabs, under its views (the recording's)
        self.island_tabs.hide()
        v.addWidget(self.island_tabs)
        self.view_index = {"home": 0, "chat": 1, "drop": 2, "settings": 3, "agent": 4, "note": 5, "clip": 6, "wardrobe": 7, "music": 8,
                           "focus": 9}
        self.fade = QGraphicsOpacityEffect(self.views)
        self.fade.setOpacity(1.0)
        self.views.setGraphicsEffect(self.fade)
        self._fade_at = None
        self.body.hide()
        self.handle = Handle(self)  # (above the body: the strip under the cards is the notch's own)

    def _home(self):
        """Home (2026-10-24, the owner's TwoCode recording): the island's widgets on their grid (core/qtisland.py: AI, Music, Focus, Tray,
        Clipboard, Note, Settings, Clock); while one of the owner's agents asks, or Claude Code asks a question, its card takes the whole
        width instead (the recording of 2026-10-20's card: the agent's mascot, its words, Deny / Always / Allow or its options), the grid back
        once it is answered. The dock's pills ride on the AI tile when it is Big (_fill_dock)."""
        w = QWidget()
        h = QVBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.grid = Q.Grid(self)
        h.addWidget(self.grid)
        self.limits_view = L.LimitsView(self)  # the limits look's Home: every AI's limits (core/usage.py; docs/adr/0011)
        self.limits_view.hide()
        h.addWidget(self.limits_view)
        left = NCard()
        left.lay.setContentsMargins(8, 10, 12, 10)
        lh = QHBoxLayout()
        lh.setSpacing(8)
        left.lay.addLayout(lh)
        self.card_pet = PetView(self, HOME_PET)  # (its corner badge: "!" while it asks, a check once it's done: the 2026-10-26 recording)
        lh.addWidget(self.card_pet, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(5)
        col.addStretch(1)  # (its words in the card's middle, as the pet is: the recording's cards)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.agent_dot = K.Dot("off", 7)
        head.addWidget(self.agent_dot)
        self.agent_title = K.Elided("", 12.5, 700)  # who: "Codex", "Monday" (a long name ends in …)
        head.addWidget(self.agent_title)
        self.agent_verb = label("", 12, 400, TEXT_2)  # "needs permission", "finished", "asks you"
        head.addWidget(self.agent_verb)
        head.addStretch(1)
        col.addLayout(head)
        self.agent_cmd = CmdLine()  # the command or act itself, in its field
        self.agent_cmd.hide()
        col.addWidget(self.agent_cmd)
        self.card_result = K.Elided("", 14, 700)  # what it did, in white ("Pushed to main · 48 tests passed")
        self.card_result.hide()
        col.addWidget(self.card_result)
        self.agent_line = label("", 10.5, 400, TEXT_2, wrap=True)
        col.addWidget(self.agent_line)
        from PySide6.QtWidgets import QGridLayout
        self.ask_grid = QGridLayout()  # a question's options, two a row (Claude Code's AskUserQuestion)
        self.ask_grid.setHorizontalSpacing(6)
        self.ask_grid.setVerticalSpacing(6)
        col.addLayout(self.ask_grid)
        self.ask_buttons = QHBoxLayout()
        self.ask_buttons.setSpacing(6)
        col.addLayout(self.ask_buttons)
        col.addStretch(1)
        lh.addLayout(col, 1)
        left.hide()
        h.addWidget(left, 1)
        self.home_card = left
        self._no_dock = QWidget()  # the dock's pills ride on the AI tile when it is Big (tiles_changed); without it, here and unseen
        self._no_dock.setLayout(QHBoxLayout())
        self.dock, self.dock_grid, self.to_window = self._no_dock, self._no_dock.layout(), None
        return w

    def _chat(self):
        """The recording's chat (frame 03): one card with the pet at its left; in it a card with what Monday is doing and Monday's thread;
        under that the voice chip and the model chip (the owner, 2026-10-20: the row of provider chips over the thread went, "we have the
        selector below"; the voice joined it) and the prompt with its round send button, an indigo light rising behind them."""
        w = NCard(glow=True)
        w.lay.setContentsMargins(8, 12, 12, 12)
        h = QHBoxLayout()
        h.setSpacing(8)
        w.lay.addLayout(h)
        h.addWidget(PetView(self, 84), 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(8)
        h.addLayout(col, 1)
        card = NCard(inner=True)
        card.lay.setContentsMargins(12, 10, 12, 10)
        card.lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.state_title = label("", 12.5, 600)
        top.addWidget(self.state_title)
        self.state_link = Button("", "ghost", height=22, name="Open Monday's Integrations")
        self.state_link.kind = "link"
        self.state_link.clicked.connect(lambda: self.open_window("integrations"))
        self.state_link.hide()
        top.addWidget(self.state_link)
        top.addStretch(1)
        card.lay.addLayout(top)
        self.state_line = label("", 12, 400, TEXT_2, wrap=True, select=True)
        card.lay.addWidget(self.state_line)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        card.lay.addLayout(self.buttons)
        self.thread = Turns()
        self.thread_lay = QVBoxLayout(self.thread)
        self.thread_lay.setContentsMargins(0, 0, 4, 0)
        self.thread_lay.setSpacing(6)
        # the stored turns in one box, what is still being said (the owner's words on their way, a reply being written) in another: Qt
        # lays out every label of a box again when one of them changes (QLayout::activate empties each item's size cache), so a reply's
        # words so far re-measure only the few in the tail, and the history box's height stays as it was measured
        self.history, self.tail = Turns(), Turns()
        for box in (self.history, self.tail):
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            self.thread_lay.addWidget(box)
        self.tail.hide()
        self.thread_scroll = QScrollArea()
        self.thread_scroll.setWidget(self.thread)
        self.thread_scroll.setWidgetResizable(True)
        self.thread_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thread_scroll.setAccessibleName("Monday's chat")
        K.SmoothScroll(self.thread_scroll)
        self.thread_scroll.verticalScrollBar().valueChanged.connect(self._near_top)
        self.thread_lay.addStretch(0)  # (the bubbles go in above it: _fill_thread)
        card.lay.addWidget(self.thread_scroll, 1)
        self.model_panel = ModelPanel(self)  # the model pill's menu, in the thread's place while open (the 2026-10-26 recording)
        self.model_panel.hide()
        card.lay.addWidget(self.model_panel, 1)
        col.addWidget(card, 1)
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        chip_row.addStretch(1)
        self.voice_chip = Chip("…", "dark", dot=ICON)
        self.voice_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_chip.setAccessibleName("Whose voice speaks the replies: click to pick another")
        self.voice_chip.mousePressEvent = lambda e: self._voice_menu()
        chip_row.addWidget(self.voice_chip)
        self.model_chip = Chip("…", "dark", dot=ICON)
        self.model_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_chip.setAccessibleName("The chat model: click to pick another")
        self.model_chip.mousePressEvent = lambda e: self._model_menu()
        chip_row.addWidget(self.model_chip)
        col.addLayout(chip_row)
        self.chat_chips = QWidget()  # the island's Chat (2026-10-29): Explain, Check spelling, Translate, Summarize on the latest copy
        ch = QHBoxLayout(self.chat_chips)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(6)
        for key, words, _ask in L.CHIPS:
            b = Button(words, "ghost", height=26, name=f"{words} your latest copy")
            b.clicked.connect(lambda _=False, k=key: self.chip_ask(k))
            ch.addWidget(b)
        ch.addStretch(1)
        self.chat_chips.hide()
        col.addWidget(self.chat_chips)
        self.prompt = Prompt()
        self.prompt.send.connect(self.send)
        self.prompt.escape.connect(self._escape)
        self.prompt.clicked.connect(self.take_keys)
        col.addWidget(self.prompt)
        self.send_btn = SendButton(self.prompt)
        self.send_btn.clicked.connect(self.send)
        return w

    def _drop(self):
        self.zone = NCard(dashed=True)
        self.zone.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zone.setAccessibleName("Drop your files here, or click to choose one")
        zh = QHBoxLayout()
        zh.setSpacing(14)
        self.zone.lay.addLayout(zh)
        zh.addStretch(1)
        zh.addWidget(PetView(self, 100), 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(8)
        self.drop_title = K.Elided("Drop your files here", 12.5, 700)  # (a long file name ends in …)
        col.addStretch(1)
        col.addWidget(self.drop_title)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        for c in DROP_CHIPS:
            self.chips.addWidget(Chip(c, "dark"))
        self.chips.addStretch(1)
        col.addLayout(self.chips)
        self.drop_line = label("", 12, 400, TEXT_2, wrap=True)
        col.addWidget(self.drop_line)
        self.file_btns, self.file_btns2 = QHBoxLayout(), QHBoxLayout()  # a file's actions: FILE_ROW on the first row, the rest below
        for row in (self.file_btns, self.file_btns2):
            row.setSpacing(6)
            col.addLayout(row)
        col.addStretch(1)
        zh.addLayout(col)
        zh.addStretch(2)
        self.zone.mouseReleaseEvent = lambda e: self._choose_file() if e.button() == Qt.MouseButton.LeftButton else None
        return self.zone

    def _settings(self):
        """The notch's settings (sound, volume, auto-close; Monday's hooks in Claude Code and Core's line; Settings… in the window) and,
        under them since 2026-10-25 (the owner: "the + should be reverted and keep all that things … in the settings"), the island's widget
        menu: the ready-made layouts, the apps, their size, Add and its preview (core/qtisland.py WidgetMenu)."""
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(SETTINGS_GAP)
        card = NCard()
        card.setFixedHeight(CARD_H["settings"])
        pv.addWidget(card)
        self.menu = Q.WidgetMenu(self)
        pv.addWidget(self.menu, 1)
        card.lay.setSpacing(8)
        r0 = QHBoxLayout()  # the look (the owner, 2026-10-29: "how can i change the ui?"): right here, as well as in the window's Settings
        r0.addWidget(label("Look", 13, 600))
        self.look_seg = K.Segmented([(k, v[0]) for k, v in I.MODES.items()], self.mode, "The notch's look")
        self.look_seg.changed.connect(self.set_mode)
        r0.addWidget(self.look_seg)
        r0.addStretch(1)
        card.lay.addLayout(r0)
        r1 = QHBoxLayout()
        r1.addWidget(label("Sound", 13, 600))
        self.sw = Toggle("The notch's sounds: on or off")
        self.sw.toggled.connect(lambda on: self.set_pref("sounds", on))
        r1.addWidget(self.sw)
        self.vol = K.slider(60, "The notch's volume", 160)
        self.vol.valueChanged.connect(lambda v: self.set_pref("volume", round(v / 100, 2)))
        self.vol.sliderReleased.connect(lambda: play_tone("saved", self.p["volume"]) if self.p["sounds"] else None)
        r1.addWidget(self.vol)
        r1.addStretch(1)
        r1.addWidget(label("Auto-close", 13, 600))
        self.autoclose = K.Segmented([(str(s), f"{s} s") for s in AUTOCLOSE], str(self.p["autoclose"]), "Auto-close after")
        self.autoclose.changed.connect(lambda v: self.set_pref("autoclose", int(v)))
        r1.addWidget(self.autoclose)
        card.lay.addLayout(r1)
        r2 = QHBoxLayout()
        self.cc_dot, self.api_dot = K.Dot("off", 8), K.Dot("off", 8)
        r2.addWidget(self.cc_dot)
        self.cc_words = label("Claude Code: …", 12.5, 400, TEXT_2)
        r2.addWidget(self.cc_words)
        r2.addSpacing(14)
        r2.addWidget(self.api_dot)
        self.api_words = label("Monday Core: …", 12.5, 400, TEXT_2)
        r2.addWidget(self.api_words)
        r2.addStretch(1)
        dress = Button("Wardrobe", "ghost", height=26, name="Dress your pet: the wardrobe")
        dress.clicked.connect(lambda: self.show_view("wardrobe"))
        r2.addWidget(dress)
        more = Button("Settings…", "ghost", height=26, name="The notch's settings in Monday's window")
        more.clicked.connect(lambda: self.open_window("general"))
        r2.addWidget(more)
        card.lay.addLayout(r2)
        card.lay.addStretch(1)
        return page

    def _wardrobe(self):
        """The wardrobe (the 2026-10-26 recording's "Dress Mochi up, seasonal outfits included"): the owner's pet big at the left in what it
        has on; "Wardrobe" and that outfit's name; the outfits in a row, each on the pet itself. A pick is the pet's own "wear", kept with the
        pet (data/desktop.json): the notch, the desk pet and the window's pages all show it. Opened from Settings, or Ctrl+Alt+G."""
        w = NCard()
        w.lay.setContentsMargins(10, 8, 14, 8)
        h = QHBoxLayout()
        h.setSpacing(12)
        w.lay.addLayout(h)
        self.dressed = Dressed(self, 94)
        h.addWidget(self.dressed, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(label("Wardrobe", 14, 700))
        head.addStretch(1)
        self.outfit_name = label("", 12, 400, TEXT_2)
        head.addWidget(self.outfit_name)
        col.addStretch(1)
        col.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.outfits = {}
        for k, words in WEAR.items():
            b = Outfit(self, k)
            b.setAccessibleName(words)
            b.setToolTip(words)
            b.clicked.connect(lambda _=False, k=k: self.dress(k))
            row.addWidget(b)
            self.outfits[k] = b
        row.addStretch(1)
        col.addLayout(row)
        col.addStretch(1)
        h.addLayout(col, 1)
        return w

    def outfit_art(self, key):
        """The owner's pet wearing `key`, for its tile (None for "none": its tile says so); made again when the pet changes."""
        if key == "none":
            return None
        sig = (key, tuple(sorted((k, str(v)) for k, v in self.p.items() if k in ("kind", "body", "accent", "shape", "eyes", "top", "fur"))))
        art = self._outfit_art.get(key)
        if not art or art[0] != sig:
            pa = PetArt({**self.p, "wear": key, "idle": "still"})
            pa.motion = False
            self._outfit_art[key] = art = (sig, pa)
        return art[1]

    def _fill_wardrobe(self):
        on = self.p["wear"]
        for k, b in self.outfits.items():
            b.on = k == on
            b.setAccessibleName(WEAR[k] + (" (on now)" if b.on else "") + ("" if b.wearable() else f": not with {self.p['name']}'s own hat"))
            b.update()
        now = wear_now(on)
        self.outfit_name.setText(WEAR[on] if on != "auto" else f"Auto · {WEAR.get(now, 'Nothing') if now != 'none' else 'nothing this season'}")

    def dress(self, key):
        """An outfit picked: on the pet now (stars round it), kept with the pet. A hat on the witch's own hat isn't: it says why."""
        if not self.outfits[key].wearable():
            return self._said(f"{self.p['name']} keeps its own witch hat: pick something else, or another pet in Settings.")
        self.dressed_at = time.monotonic()
        self.set_pref("wear", key)
        self._fill_wardrobe()

    def _agent(self):
        """Home's agent view (the retired notch's, after Dotpals): one of the owner's own agents in detail: what it is on, its last edit
        as a code card, its plan / context / limits bars, its buttons (Next agent, Story, Rewind…, Steps), its chat, and the prompt
        writing to it (Write to). No Stop: it isn't Monday's work. Filled by _show_agent from State."""
        card = NCard()
        card.lay.setContentsMargins(12, 8, 12, 10)
        card.lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.ag_back = IconButton("left", "Back to Home", size=24)
        self.ag_back.clicked.connect(self._agent_back)
        head.addWidget(self.ag_back)
        self.ag_dot = K.Dot("off", 7)
        head.addWidget(self.ag_dot)
        self.ag_title = K.Elided("", 13.5, 700)
        head.addWidget(self.ag_title, 1)
        self.ag_goto = Button("", "quiet", height=24, name="Go to the agent's window")
        self.ag_goto.clicked.connect(self._agent_goto)
        self.ag_goto.hide()
        head.addWidget(self.ag_goto)
        card.lay.addLayout(head)
        body = QWidget()
        self.ag_body = QVBoxLayout(body)
        self.ag_body.setContentsMargins(0, 0, 4, 0)
        self.ag_body.setSpacing(6)
        self.ag_note = label("", 12, 600, TEXT, wrap=True, select=True)   # an agent's own line: Write to's answer, an alert, Copied
        self.ag_line = label("", 12, 400, TEXT_2, wrap=True, select=True)  # its words, or its Story or Steps
        self.code = CodeCard()
        self.code.copied.connect(self._copy_path)
        self.meters = Meters()
        for w in (self.ag_note, self.ag_line, self.code, self.meters):
            self.ag_body.addWidget(w)
        self.ag_thread = QVBoxLayout()
        self.ag_thread.setSpacing(6)
        self.ag_body.addLayout(self.ag_thread)
        self.ag_body.addStretch(1)
        self.ag_scroll = QScrollArea()
        self.ag_scroll.setWidget(body)
        self.ag_scroll.setWidgetResizable(True)
        self.ag_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.ag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ag_scroll.setAccessibleName("What your agent is doing")
        K.SmoothScroll(self.ag_scroll)
        card.lay.addWidget(self.ag_scroll, 1)
        self.ag_buttons = QHBoxLayout()
        self.ag_buttons.setSpacing(6)
        card.lay.addLayout(self.ag_buttons)
        self.say_box = Prompt()
        self.say_box.send.connect(self.say)
        self.say_box.escape.connect(lambda: self._escape(self.say_box))
        self.say_box.clicked.connect(lambda: self.take_keys(self.say_box))
        card.lay.addWidget(self.say_box)
        self.say_btn = SendButton(self.say_box)
        self.say_btn.setAccessibleName("Send to your agent")
        self.say_btn.setToolTip("Send to your agent (Enter)")
        self.say_btn.clicked.connect(self.say)
        return card

    # -- size, shape and place ---------------------------------------------------------------------------------------------------------
    def target_size(self):
        if not self.opened:
            if self.lined:  # slid up: only the line, whatever Monday is doing ("nothing else"), but an app of the owner's at work wears its
                return LINE_PET if self._at_work() and not self.full_screen() else LINE  # mascot there (2026-10-26), not over a
                # full-screen video or game; what waits on the owner opens the notch itself
            busy = self.shown[0] not in ("idle", "listening", "asleep")
            if self.mode == "island":  # the pill, wider with something going on, and a bubble beside it for a timer, for music
                w, h = ISLAND_LIVE if busy else ISLAND_CLOSED
                return w + 2 * L.EAR + L.bubbles_w(self, h), h  # (its flared corners outside its body)
            if self.mode == "limits" and U.busiest(self.usage or []):  # the busiest limit at its ends, Monday's state between them
                return CLOSED_LINE if busy else LIMITS_CLOSED
            return CLOSED_LINE if busy else CLOSED
        if self.view == "chat":
            card = min(CHAT_MAX, max(CHAT_MIN, self._chat_height() + 8))
        elif self.view == "agent":
            card = min(AGENT_MAX, max(AGENT_MIN, self._agent_height() + 8))
        elif self.view == "home" and self.home_card_now() is not None:  # (Home's card: an approval, an agent asking, a question's options,
            c = self.home_card_now()                                     # a finished card, the plan: the 2026-10-26 recording's)
            qs = list(self.state.asks.values())[-1].get("questions") if c["kind"] == "question" and self.state.asks else None
            rows = 1 if self._q_typing else math.ceil((len(qs[min(self._q_at, len(qs) - 1)]["options"]) + 1) / 2) if qs else 0  # (+ "Other…")
            card = QUESTION_H + QUESTION_ROW_H * rows if qs else HOME_ASK_H
        elif self.view == "home":  # the look's own Home: the grid at its size, or every AI's limits
            card = self.limits_view.height_for() if self.mode == "limits" else self.grid.height()
        elif self.view in ("music", "focus"):  # the island's own pages
            card = (self.music_view if self.view == "music" else self.focus_view).H
        elif self.view == "settings":  # its own card, and the widget menu under it (a Big pick's preview makes it taller); none on the
            card = CARD_H["settings"] + (SETTINGS_GAP + self.menu.height_for() if self.mode != "limits" else 0)  # limits look (no grid)
        elif self.view == "clip":
            card = self.clip_view.height_for()
        else:  # (a file with more actions than one row holds: its second row)
            card = CARD_H[self.view] + (FILE_ROW2_H if self.view == "drop" and self.file_btns2.count() > 1 else 0)
        return OPEN_W + (2 * L.EAR if self.mode == "island" else 0), self._chrome_h() + card

    def _chat_height(self):
        """Chat's natural height: its thread measured at the width it has (QScrollArea keeps its first size hint: the notch stayed two
        lines tall whatever the answer, 2026-10-20), and the rest of its card as its layout asks."""
        if self.model_panel.isVisible():  # the model menu in the thread's place: Chat as tall as the menu (the review: no empty card)
            return self.views.widget(1).sizeHint().height()
        card, mid = self.thread_scroll.parentWidget(), self.thread_lay
        mid.invalidate()
        vw = self.thread_scroll.viewport().width()
        width = vw if vw > 100 else CHAT_W
        need = mid.totalHeightForWidth(width) if mid.hasHeightForWidth() else mid.totalSizeHint().height()
        held = card.layout().itemAt(card.layout().indexOf(self.thread_scroll)).sizeHint().height()
        return self.views.widget(1).sizeHint().height() - held + need  # (the whole view: the outer card, its prompt and chip too)

    def _agent_height(self):
        """The agent view's natural height: its middle (wrapped words, the card, the bars, the chat) measured at the width it has, not
        QScrollArea's size hint (it keeps the first one it worked out), and the rest of the card as its layout asks."""
        page, mid = self.views.widget(4), self.ag_scroll.widget().layout()
        mid.invalidate()  # (a turn just added to its chat leaves the middle's cached height stale until Qt lays it out)
        vw = self.ag_scroll.viewport().width()
        width = vw if vw > 100 else OPEN_W - 2 * PAD - 24 - 14  # (not laid out yet: the open notch's card, less its margins and bar)
        need = mid.totalHeightForWidth(width) if mid.hasHeightForWidth() else mid.totalSizeHint().height()
        held = page.lay.itemAt(page.lay.indexOf(self.ag_scroll)).sizeHint().height()  # what the card's layout counts for the middle
        return page.sizeHint().height() - held + max(need, self.ag_scroll.minimumSizeHint().height())

    def screen_area(self):
        scr = QGuiApplication.screenAt(QPoint(int(self.cx or 0), int((self.top or 0) + 2))) if self.cx is not None else None
        return (scr or QGuiApplication.primaryScreen()).availableGeometry()

    def hanging(self):
        a = self.screen_area()
        return self.top is not None and abs(self.top - a.top()) <= 2

    def _place(self, w, h):
        a = self.screen_area()
        if self.cx is None or self.top is None or not a.contains(QPoint(int(self.cx), int(self.top) + 1)):
            self.cx, self.top = a.center().x(), a.top()  # the top centre of the main screen, hanging from its edge (every look's)
        x = int(round(self.cx - w / 2))
        x = max(a.left(), min(a.right() - int(w) + 1, x))
        self._placing = True
        if self._arriving:  # on its way down from the top edge (the start): its size now, its place when it lands (_arrived)
            self.resize(int(round(w)), int(round(h)))
        else:
            self.setGeometry(x, int(self.top), int(round(w)), int(round(h)))
        self._placing = False
        self._lay_body()

    def _arrive(self):
        """The start (the owner, 2026-10-21: "a starting animation like the video"): the closed bar comes down from above the screen's
        edge and settles with a little bounce (ARRIVE_MS, OutBack), the pet blinking as it lands; with motion off it is simply there."""
        if reduced_motion() or not self.isVisible():
            return
        if self.mode == "island":
            return self._arrive_island()
        x, y, h = self.x(), int(self.top if self.top is not None else 0), self.height()
        self._arriving = True
        self._placing = True
        self.move(x, y - h - ARRIVE_FROM)
        self._placing = False
        self._arrival = QPropertyAnimation(self, b"pos", self)
        self._arrival.setDuration(ARRIVE_MS)
        self._arrival.setEasingCurve(QEasingCurve.Type.OutBack)
        self._arrival.setStartValue(QPoint(x, y - h - ARRIVE_FROM))
        self._arrival.setEndValue(QPoint(x, y))
        self._arrival.finished.connect(self._arrived)
        self.art.blink_at = time.monotonic() + ARRIVE_MS / 1000 * 0.8
        self._arrival.start()

    def _arrived(self):
        self._arriving = False
        self._place(*self.target_size())

    def _arrive_island(self):
        """The floating island's start (the TwoCode recording: the eyes first, the pill forming round them): it fades in where it floats,
        its pill growing from the eyes to its width; the eyes stay closed until Core says it is ready (_hello)."""
        w, h = self.target_size()
        self.setWindowOpacity(0.0)
        self.w_spring, self.h_spring = Spring(float(h)), Spring(float(h))
        self._place(h, h)
        self._fade_in(ISLAND_FADE_MS)
        self._go()

    def _fade_in(self, ms, start=0.0):
        """The whole notch coming in (the floating island's start, a new look): none with reduced motion."""
        if reduced_motion():
            return self.setWindowOpacity(1.0)
        self._fade_win = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_win.setDuration(ms)
        self._fade_win.setStartValue(start)
        self._fade_win.setEndValue(1.0)
        self._fade_win.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_win.start()

    @property
    def core_phase(self):
        return getattr(self, "_core_phase", None)

    @core_phase.setter
    def core_phase(self, s):
        """The supervisor's word on Core (core/desktop.py: starting, running, restarting, failed, stopped). Failed or stopped at the start:
        no more waiting to greet; the closed bar says what is true (offline), never "ready"."""
        self._core_phase = s
        if s in ("failed", "stopped") and self.booting:
            self.booting = False
            self.refresh()

    def _hello(self):
        """The start's handshake (GET /api/hello: Core's own readiness). Ready, or degraded (an optional part missing must not hold the start
        up): the greeting. Still starting, or no answer: asked again in a second, the eyes still closed, for at most BOOT_MAX_S."""
        if self.booting:
            self.bridge.get("/api/hello", self._got_hello)

    def _got_hello(self, d, err):
        if not self.booting:
            return
        if not err and isinstance(d, dict) and d.get("state") in ("ready", "degraded"):
            self.booting = False
            self._greet_until = time.monotonic() + GREET_S
            self.art.blink_at = self._greet_until + 0.25  # (a blink after the greeting, as the recordings' Robo does)
            print(f"notch: Core said {d.get('state')}: the greeting ({time.monotonic() - self._boot_t0:.1f} s after the notch came)", flush=True)
            QTimer.singleShot(int(GREET_S * 1000) + 40, self.refresh)
            return self.refresh()
        if time.monotonic() - self._boot_t0 > BOOT_MAX_S:
            self.booting = False  # (Core never said ready: the bar shows what State says, offline or not, never a greeting)
            return self.refresh()
        QTimer.singleShot(BOOT_POLL_MS, self._hello)

    def _lay_body(self):
        self.body.setGeometry(0, 0, self.width(), self.height())
        if getattr(self, "handle", None) is not None:  # (the open notch's handle rides its bottom edge as it opens and grows)
            self.handle.move(int(self.width() / 2 - Handle.W / 2), int(self.height() - PAD / 2 - Handle.H / 2))
            self.handle.setVisible(self.opened and self.height() > CLOSED_LINE[1] + 40)
            self.handle.raise_()

    def _go(self):
        """Animate to the size it should have now: the spring opening or growing, the curve closing (none when motion is off)."""
        w, h = self.target_size()
        if reduced_motion() or not self.isVisible():
            self.w_spring = Spring(w)
            self.h_spring = Spring(h)
            self.closing = None
            self._place(w, h)
            self._after_size()
            return
        shrinking = w < self.width() - 1 or h < self.height() - 1
        if shrinking and not self.opened:
            self.closing = ((self.width(), self.height()), (w, h), time.monotonic())
        else:
            self.closing = None
            self.w_spring.value, self.h_spring.value = float(self.width()), float(self.height())
            self.w_spring.target, self.h_spring.target = float(w), float(h)
        self._stepped = None
        self._anim.start()

    def _step(self):
        if self.closing:
            (w0, h0), (w1, h1), t0 = self.closing
            t = min(1.0, (time.monotonic() - t0) * 1000 / CLOSE_MS)
            k = CLOSE_CURVE(t)
            self._place(w0 + (w1 - w0) * k, h0 + (h1 - h0) * k)
            if t >= 1.0:
                self.closing = None
                self._anim.stop()
                self._after_size()
            return
        # the time that really passed, not a frame's worth: a frame that took 50 ms (a translucent window repainted whole) used to move the
        # spring 15 ms, and the opening's 0.7 s took 5 s (2026-10-24, the owner: "it lags too much")
        now = time.monotonic()
        dt, self._stepped = min(ANIM_MAX_DT, now - (self._stepped or now - ANIM_MS / 1000)), now
        self.w_spring.step(dt)
        self.h_spring.step(dt)
        self._place(self.w_spring.value, self.h_spring.value)
        if self.w_spring.settled() and self.h_spring.settled():
            self._anim.stop()
            self._place(self.w_spring.target, self.h_spring.target)
            self._after_size()

    def _warm_fonts(self):
        """The fonts the open notch's cards use, loaded while it is closed: each distinct font measured once with the cards' own words and
        the signs Monday writes, one per turn of the event loop (2026-10-24: the first opening stopped 199 ms measuring "Voice: N/A  ▾":
        Segoe UI has no ▾, and Windows loaded the font it falls back to)."""
        fonts, words = {}, set(WARM_SIGNS)
        for w in [self.body, *self.body.findChildren(QWidget)]:
            f = w.font()
            fonts.setdefault((f.family(), round(f.pointSizeF(), 2), f.weight(), f.italic()), f)
            text = getattr(w, "text", None)
            if callable(text) and isinstance(t := text(), str) and t.strip():
                words.add(t[:80])
        todo, sample = list(fonts.values()), " ".join(sorted(words))

        def one():
            if todo:
                QFontMetricsF(todo.pop()).horizontalAdvance(sample)
                QTimer.singleShot(0, one)
        one()

    def _after_size(self):
        if self.opened and not self.body.isVisible() and not reduced_motion():  # just opened: the cards fade in over the grown shell
            self._fade_at = time.monotonic()
            self.fade.setOpacity(0.0)
        self.body.setVisible(self.opened)
        if self.opened and self.view == "chat":  # (grown or not, the newest words in sight: unless the owner scrolled up to read)
            bar = self.thread_scroll.verticalScrollBar()
            if self._follow_thread:
                QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
            QTimer.singleShot(0, self._settle_chat)
        self.update()

    def _settle_chat(self):
        """Laid out at its new size, Chat's words can take a line more or less (its scrollbar went or came): sized again, at most
        CHAT_SETTLES times for one change of the thread (never a loop between two heights)."""
        if self.opened and self.view == "chat" and self._chat_settles > 0 and not self._anim.isActive() \
                and abs(self.height() - self.target_size()[1]) > 1:
            bar = self.thread_scroll.verticalScrollBar()
            self._follow_thread = bar.value() >= bar.maximum() - 8  # (a reader scrolled up stays where they read; 2026-10-20 night)
            self._chat_settles -= 1
            self._go()

    # -- open, close, views ------------------------------------------------------------------------------------------------------------
    def open(self, view=None, peek=False):
        if not self.isVisible():
            self.show()
        if view or not self.opened:  # (opened by a click, a hover or a key with no view named: what it shows is read now too, 2026-10-20)
            self._switch(view or self.view)
        self.peek = peek and not self.opened
        if not self.opened:
            self.opened = True  # (its cards come in once the shell has grown, _after_size: each frame then repaints the shell alone)
            self.refresh()
        self._go()  # (also when it was open: another view has its own height)
        self._frames.start(FRAME_MS)
        self._island_t.start()
        self._watch_media()
        if self.quit is None:  # (Power: the tray's Quit Monday, once the app has made its tray)
            self.set_quit(self._tray_quit())
        if not self.underMouse():
            self._arm_close()

    def close_notch(self):
        if not self.opened:
            return
        self.opened, self.peek = False, False
        self.grid.edit(False)  # (closing is Done: every change was kept as it was made)
        if self.note_view.box.hasFocus() or self.note_view._save.isActive():
            self.note_view.keep()
        self.give_keys()
        self.body.hide()
        self._island_t.stop()
        self.media.watch(False)
        self.refresh()
        self._go()

    def collapse_all(self):
        """The header's ⌃ (the owner, 2026-10-26: "it should collapse the whole notch only"): closed and folded to its line in one click;
        a click on the line, or a swipe down, brings the bar back."""
        self.close_notch()
        self.line(True)

    def toggle(self):
        self.close_notch() if self.opened else self.open()

    def show_view(self, view):
        if not self.opened:
            return self.open(view)
        if view != self.view:
            self._switch(view)
            self._go()

    def _switch(self, view):
        old = self.view
        if old == "note" and view != "note":
            self.note_view.keep()
            if self.note_view.box.hasFocus():
                self.give_keys()  # (the keyboard back to the owner's app it came from)
        self.view = view
        if view != "home":
            self.grid.edit(False)
        tab = {"agent": "home", "note": "home", "clip": "home", "wardrobe": "settings"}.get(view, view)  # (the agent, note and clipboard views
        # are Home's; the wardrobe is Settings')
        for k, b in self.tab_btns.items():
            b.set_lit(k == tab)
        self.views.setCurrentIndex(self.view_index[view])
        self.head.update()
        self.island_tabs.update()
        self.limit_tabs.update()
        if view == "home":
            self._load_home()
        elif view == "settings":
            self._load_hooks()  # (its "Claude Code: …" line)
            self.menu.open(at=self._menu_at)  # (the widget menu under it: for the empty cell whose + opened it, else where there is room)
            self._menu_at = None
        elif view == "chat":
            self._load_chat()
        elif view == "agent":
            self._agent_chat(force=True)
        elif view == "note":
            self.note_view.load()
            if self.isVisible() and not self.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
                QTimer.singleShot(0, self._note_keys)  # (opened to write in: the keyboard comes here, never in an off-screen picture)
        elif view == "clip":
            self.clip_view.load()
        elif view == "wardrobe":
            self._fill_wardrobe()
        elif view == "music":
            self.music_view.fill()
        elif view == "focus":
            self.focus_view.fill()
        self._watch_media()
        if old != view and not reduced_motion():
            self._fade_at = time.monotonic()  # (Coucou's .view / .view.on: the new view comes in over ~460 ms)
            self.fade.setOpacity(0.0)
        self.refresh()

    def _held(self):
        """Something keeps the open notch open: an approval or a file's choice waits, words are being typed (to Monday or to an agent), a
        file is copying, a menu is open, an agent's Story or Steps shows, or one of Monday's own tasks runs (notch.py's _keep_open)."""
        return (self.state.needs_owner() or self.uploading or self.menu_open or any(bool(b.toPlainText().strip()) or b.hasFocus()
                                                                                     for b in (self.prompt, self.say_box))
                or (self.story is not None and self.view == "agent") or bool(self.state.tasks)
                or (self.view == "note" and self.note_view.box.hasFocus()) or self.grid.over.drag is not None)

    def _arm_close(self):
        if self.opened:
            self._close_t.start(int(self.p["autoclose"] * 1000) if not self.peek else 1500)

    def _auto_close(self):
        if self.underMouse() or self._held():
            return self._arm_close()
        self.close_notch()

    def enterEvent(self, e):
        self._close_t.stop()
        self._over = True
        if not self.opened:
            if not self.lined:  # (the line stays a line under a mouse passing on its way to a tab: 2026-10-21, "i cant actually see the tabs")
                self._hover_t.start(int(HOVER_S * 1000))
            self.update()  # (the bar says which session and folder while the mouse rests on it; the line brightens)
        super().enterEvent(e)

    def line(self, on):
        """The third of the notch's three steps (the owner, 2026-10-21: "three steps collapse … when we slide up it'll have a small line
        that is nothing else"): the closed bar folded to a thin line at the top edge (LINE), so the tabs under it show; a click on it or
        a swipe down: the bar again."""
        if self.opened or on == self.lined:
            return
        self.lined = on
        self.p = prefs({**self.p, "lined": on})  # (kept: a restart starts as the owner left it, 2026-10-26)
        self.win.keep("pet", self.p)
        self._go()
        self.update()

    def leaveEvent(self, e):
        self._hover_t.stop()
        self._over = False
        self._arm_close()
        self.update()
        super().leaveEvent(e)

    # -- the keyboard: shown without taking it; the prompt takes it when clicked, closing gives it back ---------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        self._quiet(True)
        self._frames.start(FRAME_MS if self.opened else FRAME_MS * 2)

    def hideEvent(self, e):
        self._frames.stop()
        super().hideEvent(e)

    def _quiet(self, on):
        """Windows: WS_EX_NOACTIVATE, so a click on a tab or a pal leaves the owner's app with the keyboard (macOS and Linux: the OS
        decides; a tool window there rarely takes it)."""
        if sys.platform != "win32" or not self.isVisible():
            return
        import ctypes
        hwnd = int(self.winId())
        get, put = ctypes.windll.user32.GetWindowLongPtrW, ctypes.windll.user32.SetWindowLongPtrW
        get.restype, put.restype = ctypes.c_ssize_t, ctypes.c_ssize_t
        get.argtypes = (ctypes.c_void_p, ctypes.c_int)
        put.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
        style = get(hwnd, -20)
        put(hwnd, -20, (style | 0x08000000) if on else (style & ~0x08000000))

    def take_keys(self, box=None):
        """box: the prompt clicked (Chat's, or Write to's on the agent view); Chat's by default."""
        if sys.platform == "win32":
            import ctypes
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg and fg != int(self.winId()):
                self.prev_fg = fg
            self._quiet(False)
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        self.activateWindow()
        (box or self.prompt).setFocus(Qt.FocusReason.MouseFocusReason)

    def give_keys(self):
        try:
            typed = bool(self.q_field is not None and self.q_field.hasFocus())
        except RuntimeError:  # (its card was rebuilt: the line is gone; 2026-10-20 this broke the notch's auto-close)
            self.q_field, typed = None, False
        had = self.prompt.hasFocus() or self.say_box.hasFocus() or self.note_view.box.hasFocus() or typed
        self.prompt.clearFocus()
        self.say_box.clearFocus()
        self.note_view.box.clearFocus()
        self._quiet(True)
        if had and sys.platform == "win32" and self.prev_fg:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(self.prev_fg)
        self.prev_fg = None

    # -- the mouse: a click opens, a drag moves (the OS's own move), the pet is poked ----------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press, self._press_t = e.globalPosition().toPoint(), time.monotonic()
            self._swipe_from = e.position().toPoint() if self._on_sessions(e.position().toPoint()) else None  # a drag there swipes chats
        elif e.button() == Qt.MouseButton.RightButton:
            self._context(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._swipe_from is not None:
            return  # Home's card with sessions on it: a drag is a swipe between chats, not a move of the notch (its edges still move it)
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton and dragging(  # (a click's drift isn't a drag: 2026-10-29)
                time.monotonic() - self._press_t, (e.globalPosition().toPoint() - self._press).manhattanLength(), QApplication.startDragDistance()):
            d = e.globalPosition().toPoint() - self._press
            if not self.opened and abs(d.y()) > abs(d.x()) * 1.5 and (d.y() <= -FOLD_UP_PX or d.y() >= SWIPE_PX):  # closed: up folds, down opens
                self._press = None
                print(f"notch: a drag on the {'line' if self.lined else 'bar'} {d.x():+},{d.y():+}: {'up' if d.y() < 0 else 'down'}", flush=True)
                return self._fold(d.y() < 0)
            if not self.opened and abs(d.y()) > abs(d.x()):
                return  # (not far enough yet to know: a sideways drag moves the notch, an up or down one folds or unfolds it)
            self._press = None
            self._close_t.stop()
            self.windowHandle().startSystemMove()
        elif not self.opened:
            self.update()

    def _on_sessions(self, pos):
        """Is this point on the AI tile while one of the owner's sessions is on it (where a swipe moves between chats)?"""
        ai = self.grid.tiles.get("ai")
        return bool(self.opened and self.view == "home" and self.card_session and ai is not None and ai.isVisible()
                    and ai.rect().contains(ai.mapFrom(self, pos)))

    def _swipe(self, step):
        """The next (+1) or previous (-1) of the owner's live sessions on Home's card (the owner, 2026-10-21: "when I swipe it should go
        to the next or the before chat"), round the end; nothing with one session."""
        keys = [s.get("key") for s in self.state.live() if s.get("key")]
        if len(keys) < 2:
            return
        cur = (self.state.agent or {}).get("key")
        i = keys.index(cur) if cur in keys else 0
        card = self.grid.tiles.get("ai")
        old = None if reduced_motion() or card is None else card.grab()
        self._show_session(keys[(i + step) % len(keys)])
        if old is not None:
            self._slide(card, old, step)
        self._sound("prompt")

    def _slide(self, card, old, step):
        """The card's last face slides out to the side the swipe went and fades, the next chat's in its place underneath (SLIDE_MS)."""
        face = QLabel(card)
        face.setPixmap(old)
        face.setGeometry(card.rect())
        face.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        fade = QGraphicsOpacityEffect(face)
        face.setGraphicsEffect(fade)
        face.show()
        run = QVariantAnimation(face)
        run.setDuration(SLIDE_MS)
        run.setStartValue(0.0)
        run.setEndValue(1.0)
        run.setEasingCurve(QEasingCurve.Type.OutCubic)
        run.valueChanged.connect(lambda t: (face.move(int(-step * face.width() * 0.6 * t), 0), fade.setOpacity(1.0 - t)))
        run.finished.connect(face.deleteLater)
        run.start()

    def _fold(self, up):
        """A swipe or a drag, one of the three steps (the owner, 2026-10-21: "up folds, down opens", then "three steps collapse"): up, the
        open notch closes to the bar and the bar folds to the line; down, the line becomes the bar, and the bar opens."""
        if up:
            if self.opened:
                self.close_notch()
            else:
                self.line(True)
        elif self.lined:
            self.line(False)
        elif not self.opened:
            self.open()

    def _in_list(self, pos):
        """Is this point over something that scrolls (Chat's thread, the agent view, a long prompt)? A swipe there scrolls it, and a
        scroll run past its end must not close the notch."""
        w = self.childAt(pos)
        while w is not None and w is not self:
            if isinstance(w, QAbstractScrollArea) and w.verticalScrollBar().maximum() > 0:
                return True
            w = w.parentWidget()
        return False

    def wheelEvent(self, e):
        """A swipe (a touchpad's burst of small wheel events, or a mouse wheel's notches) is one gesture: it ends at its ScrollEnd or after
        GESTURE_END_S without an event, and makes one move once its delta adds up to SWIPE_SUM: sideways on Home's chats, the next or the
        one before (sliding); up or down, _fold. Replayed 2026-10-21: one 0.9 s swipe used to fold, unfold and fold the notch again."""
        now, ph, d = time.monotonic(), e.phase(), e.angleDelta()
        g = self._gesture
        if g is None or ph == Qt.ScrollPhase.ScrollBegin or now - g["at"] > GESTURE_END_S:
            if g is not None and not g["done"]:
                self._trace(g, "made no move")
            g = self._gesture = {"rx": 0, "ry": 0, "pad": False, "inv": False, "done": False, "at": now,
                                 "first": f"{d.x()},{d.y()} px {e.pixelDelta().x()},{e.pixelDelta().y()} {ph.name} {e.pointingDevice().type().name}"}
        g["at"] = now
        if ph in (Qt.ScrollPhase.ScrollEnd, Qt.ScrollPhase.ScrollMomentum):  # (the fingers are off: the gesture is over, its glide too)
            self._gesture = None if ph == Qt.ScrollPhase.ScrollEnd else g
            return e.accept()
        # "up" is the fingers going up (or a wheel rolled away): a touchpad scrolling the natural way (Windows' default,
        # "Down motion scrolls up") sends a swipe up as a scroll down; Qt says so only on macOS (inverted), so Windows' own setting is read
        g["pad"] = g["pad"] or e.pointingDevice().type() == QInputDevice.DeviceType.TouchPad or d.x() % 120 != 0 or d.y() % 120 != 0
        g["inv"] = g["inv"] or e.inverted()  # (a touchpad's whole swipe is one: a step of a wheel's 120 within it doesn't make it a wheel)
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        g["rx"] += d.x() or (d.y() if shift else 0)
        g["ry"] += 0 if shift else d.y()
        flip = -1 if g["inv"] or (g["pad"] and self._natural) else 1
        g["dx"], g["dy"] = g["rx"] * flip, g["ry"] * flip
        pos = e.position().toPoint()
        if g["done"]:
            return e.accept()  # (one move per swipe)
        if self.opened and self._in_list(pos):
            g["done"] = True
            self._trace(g, "was over a list: the list's")
            return e.accept()
        if abs(g["dx"]) >= SWIPE_SUM and abs(g["dx"]) > abs(g["dy"]) and self._on_sessions(pos):
            g["done"] = True
            self._trace(g, "moved to the next chat" if g["dx"] < 0 else "moved to the chat before")
            return self._swipe(-1 if g["dx"] > 0 else 1)
        if abs(g["dy"]) >= SWIPE_SUM and abs(g["dy"]) > abs(g["dx"]):
            g["done"] = True
            self._trace(g, "went up: " + ("the bar" if self.opened else "the line") if g["dy"] > 0 else
                        "went down: " + ("the bar" if self.lined else "open" if not self.opened else "(already open)"))
            return self._fold(g["dy"] > 0)
        e.accept()

    def _trace(self, g, what):
        """One line per swipe in the window's log (2026-10-21, the owner: "still not done": what a real swipe sends, and what it did)."""
        print(f"notch: a swipe on the {'open notch' if self.opened else 'line' if self.lined else 'bar'} {what} (sum {g['rx']:+},{g['ry']:+}; "
              f"first {g['first']}; touchpad {g['pad']}, natural {self._natural}, inverted {g['inv']})", flush=True)

    def mouseReleaseEvent(self, e):
        if self._swipe_from is not None and e.button() == Qt.MouseButton.LeftButton:
            d = e.position().toPoint() - self._swipe_from
            self._swipe_from = None
            if abs(d.x()) >= SWIPE_PX and abs(d.x()) > abs(d.y()):  # a swipe: left for the next chat, right for the one before
                self._press = None
                return self._swipe(-1 if d.x() > 0 else 1)
        if self._press is not None and e.button() == Qt.MouseButton.LeftButton:
            press, self._press = self._press, None
            d = e.globalPosition().toPoint() - press
            if not self.opened and not self.lined and d.y() <= -FOLD_UP_PX and abs(d.y()) > abs(d.x()) * 1.5 and \
                    dragging(time.monotonic() - self._press_t, abs(d.y()), FOLD_UP_PX):
                return self._fold(True)  # (a drag up, held, that stopped before its move was read: the bar folds; a quick one opened it)
            if not self.opened and self.lined:
                self.line(False)  # the line clicked: the bar (the next of the three steps)
            elif not self.opened:
                if self._pet_rect().contains(e.position()):
                    self.poke()
                self.open()
            else:
                self.peek = False  # (a tile's tap is its own: the AI tile's session opens its view, Grid.released)

    def moveEvent(self, e):
        if not self._placing:
            self._save_t.start()
        super().moveEvent(e)

    def _moved(self):
        """A drag ended: inside a screen it stays (put back in when past an edge), its middle and top kept for next time."""
        g = self.geometry()
        scr = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
        a = scr.availableGeometry()
        self.cx = max(a.left() + g.width() / 2, min(a.right() - g.width() / 2, g.center().x()))
        self.top = max(a.top(), min(a.bottom() - g.height(), g.top()))
        if abs(self.top - a.top()) <= 12:  # near the top: it hangs from the edge again
            self.top = a.top()
        self._place(self.width(), self.height())
        self._keep_place({"cx": round(self.cx), "y": round(self.top), "screen": scr.name()})
        self.update()

    def reset_place(self):
        self.cx = self.top = None
        self._keep_place(None)
        self._place(self.width(), self.height())

    def _context(self, at):
        m = QMenu(self)
        m.addAction(K.icon("open"), "Open Monday", lambda: self.open_window("home"))
        m.addAction(K.icon("grid"), "Arrange the widgets", self.arrange)
        m.addAction("Reset the notch's place", self.reset_place)
        looks = m.addMenu("Look")  # the three looks (Settings → General has them too; docs/adr/0011)
        for k, (name, _line) in I.MODES.items():
            act = looks.addAction(name, lambda k_=k: self.set_mode(k_))
            act.setCheckable(True)
            act.setChecked(k == self.mode)
        m.addAction("Hide the notch", lambda: self.set_pref("visible", False))
        m.addAction(K.icon("sun"), "Pets and the notch…", lambda: self.open_window("general"))
        self.menu_open = True
        m.exec(at)
        self.menu_open = False

    # -- the pet ---------------------------------------------------------------------------------------------------------------------------
    def clock(self):
        return time.monotonic() if self.art.motion else 1.0

    def mascot(self):
        """Who stands in the pet's place: while an agent asks, its own mascot (the owner, 2026-10-20): Claude Code's (Clawd) for Claude
        Code, another tool's own logo (its program's icon) for that tool; and the same for the session Home's card shows (the owner, the
        same evening: "if I'm using Claude then Claude's mascot, if Codex then Codex, if Cursor then Cursor, so we know which chat this
        is"); else the owner's pet."""
        if self.opened and self.view == "home" and getattr(self, "_card_who", None) == "Monday":  # Monday's own card on Home (its approval,
            return self.art                                                                     # its finished task): its own pet, 2026-10-26
        a = list(self.state.asks.values())[-1] if self.state.asks else None
        w = None
        if not a:  # Home's card: its session; the closed bar: the chat of the app in front, else the session it watches (the owner,
            w = self.state.agent if self.opened and self.view == "home" else None if self.opened else \
                self._at_work() if self.lined else self._bar_session()  # 2026-10-20 night); the line: the app at work (2026-10-26)
        who = str((a or {}).get("agent") or (w or {}).get("agent") or "").lower()
        if not who:
            return self.art
        if "claude" in who:  # the agent's own mascot first (the owner, 2026-10-21: "Claude's mascot was better: the IDE's mascot, else the logo")
            return CLAWD_ART
        if who == "codex":  # Codex's own: the Codex app's logo, wherever its chat runs (the ChatGPT app, VS Code)
            logo = self._logo(self._app_exe("Codex Desktop"))
            if logo is not None:
                return logo
        if w:  # no mascot of its own: the icon of the app the chat happens in (its open window, else its running program: the ChatGPT
            # app in the tray, 2026-10-21 "the codex mascot i cant see"), else the tool's program
            host = next((pl for pl in self.places or [] if pl.get("session") and pl.get("session") == w.get("key")), None)
            logo = self._logo((host or {}).get("exe")) or self._logo(self._app_exe((w.get("where") or {}).get("entry")))
            if logo is not None:
                return logo
        exe = next((str(t.get("exe") or "") for t in (self.tools or {}).values() if str(t.get("name") or "").lower() in who
                    or who in str(t.get("name") or "").lower()), "")
        return self._logo(exe) or K.pal(PILL_COLOURS.get(who.replace(" ", "_"), PALS["purple"]))  # (no logo: a pal in its colour, as
        # the dock's pills: never the owner's own pet for another app's work, 2026-10-26)

    def _app_exe(self, entry):
        """The running program of the app a session's log names (watch.APPS: the Claude app, the ChatGPT app, VS Code, Kiro…), found once
        by its process name (a miss is tried again after 30 s: the bar asks many times a second), or None."""
        from . import watch
        names = watch.APPS.get(entry) or ()
        if not names:
            return None
        hit = self._app_exes.get(names)
        if hit and (hit[0] or time.monotonic() - hit[1] < 30):
            return hit[0]
        exe = None
        try:
            import psutil
            p = next((p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in names), None)
            exe = p.exe() if p else None
        except Exception:  # (gone, or no right to read it)
            exe = None
        self._app_exes[names] = (exe, time.monotonic())
        return exe

    def _logo(self, exe):
        """A program's own icon as a mascot (K.LogoPal), read from the OS once per program; None without one (no file, a Store app's
        shell: address, no icon)."""
        exe = str(exe or "")
        if not exe or not os.path.isfile(exe):
            return None
        if exe not in self._logos:
            from PySide6.QtCore import QFileInfo
            from PySide6.QtGui import QIcon
            from PySide6.QtWidgets import QFileIconProvider
            png = store_logo(exe)  # (a Store app: its package's own logo, not the padlocked icon)
            ic = QIcon(png) if png else QFileIconProvider().icon(QFileInfo(exe))
            self._logos[exe] = None if ic.isNull() else K.LogoPal(ic)
        return self._logos[exe]

    def _front_key(self):
        """The agent app in front, as watch.IDES keys it (claude, codex for the ChatGPT app, vscode, kiro…), or None: where the owner is
        working (the owner, 2026-10-21: "if I'm using Codex then Codex"). Windows only; each process's name read once."""
        if sys.platform != "win32":
            return None
        import ctypes
        from . import watch
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value not in self._pid_names:
            if len(self._pid_names) > 200:
                self._pid_names.clear()
            try:
                import psutil
                self._pid_names[pid.value] = (psutil.Process(pid.value).name() or "").lower()
            except Exception:  # (gone, or no right to read it)
                self._pid_names[pid.value] = ""
        return watch.IDES.get(self._pid_names[pid.value], (None,))[0]

    def _bar_session(self):
        """The session the closed bar wears and names: the chat of the app in front, else the one it watches (the owner's pick, else the
        first live one)."""
        return self._front_session() or (self.state.watched() or (None, None))[1]

    def full_screen(self):
        """Whether a full-screen app (a video, a game) or presentation mode has the screen, as Windows tells its own notifications; read at
        most once a second (the size is asked many times a second). Windows only: elsewhere False."""
        now = time.monotonic()
        if sys.platform != "win32" or now - self._fs[0] < 1.0:
            return self._fs[1] and sys.platform == "win32"
        import ctypes
        st = ctypes.c_int()
        self._fs = (now, ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st)) == 0 and st.value in QUIET_SCREEN)
        return self._fs[1]

    def _at_work(self):
        """The session of the owner's app at work that the folded line wears (2026-10-26): the app in front's, else their pick, else the
        first at work; None when none of their apps works."""
        working = [s for s in self.state.live() if s.get("state") == "working"]
        front = self._front_session()
        return front if front in working else next((s for s in working if s.get("key") == self.state.pick), working[0] if working else None)

    def _front_session(self):
        """The live session of the app in front (the owner's pick first, then one at work, then the newest), or None: so the closed bar
        shows the chat the owner is in, not the first one Core lists (a Claude session at work came first while they used Codex)."""
        from . import watch
        key = self._front_key()
        mine = [s for s in self.state.live() if key and watch.IDES.get((watch.APPS.get((s.get("where") or {}).get("entry")) or ("",))[0],
                                                                         (None,))[0] == key]
        return next((s for s in mine if s.get("key") == self.state.pick), None) or \
            next((s for s in mine if s.get("state") == "working"), None) or (mine[0] if mine else None)

    def poke(self):
        if self.art.poke():
            self.state.result = ("dizzy", "Dizzy", "Too many pokes!", time.monotonic(), 2.6)
        self.refresh()

    def _desk_show(self):
        """The pet on the desktop as the owner's choice says (General: "Your pet on the desktop"; its own ×)."""
        if self.p.get("desk"):
            if self.desk is None:
                self.desk = DeskPet(self)
            self.desk.show_pet()
        elif self.desk is not None:
            self.desk.hide_pet()

    def _pet_rect(self):  # (drawn 4 px larger, 3 up: the recording's pet starts 23 in, its brim 33 wide, its hat at the top edge)
        if self.mode == "island" and not self.opened:  # the floating island's eyes, in its pill's middle
            h = self.height()
            return QRectF((self.width() - L.bubbles_w(self, h)) / 2 - h / 2, 0, h, h)
        return QRectF(CLOSED_PET_X, 2, self.height() - 3, self.height() - 3)

    def _tick(self):
        if self._fade_at is not None:
            u = (time.monotonic() - self._fade_at) * 1000
            self.fade.setOpacity(min(1.0, max(0.0, (u - 120) / 300)))
            if u >= 460:
                self._fade_at = None
                self.fade.setOpacity(1.0)
        if self.opened and self.view == "drop" and self.zone.isVisible():
            self.zone.dash = (time.monotonic() % DASH_S) / DASH_S * 9
            self.zone.update()
        if self.isVisible():  # its eyes on the pointer (Coucou's Mochi does it)
            c = self.mapToGlobal(QPoint(int(self._pet_rect().center().x()), int(self._pet_rect().center().y())))
            d = QCursor.pos() - c
            self.art.look_at(d.x(), d.y())
        if not self.opened or self.closing:  # the bar's own pet and line; open, only the pet views move (2026-10-20 night: a whole-window
            self.update()                    # repaint 30 times a second made the chat's scrolling lag, the shell being a layered window)
        for pv in self._pets[:]:
            try:
                if pv.isVisible():
                    pv.update()
            except RuntimeError:  # its tile was replaced and Qt freed it before tiles_changed listed the pets again (desktop.log
                self._pets.remove(pv)  # 2026-10-26: 259 "Internal C++ object already deleted" in one session)

    # -- what Core says ------------------------------------------------------------------------------------------------------------------
    def _online(self, s):
        if s == "ONLINE" and self.api_dot.state != "ok":  # Core back after a gap: a reply that was being written then won't finish
            self._live.clear()
            self._live_model.clear()
        self.state.online = {"ONLINE": True, "OFFLINE": False}.get(s, self.state.online)
        starting = s != "ONLINE" and getattr(self, "core_phase", None) in ("starting", "restarting")  # (core/desktop.py's word: it is
        self.api_dot.set("ok" if s == "ONLINE" else "wait" if s == "RECONNECTING" or starting else "bad")  # starting Core, not lost it)
        self.api_words.setText(f"Monday Core: {self.core_phase}…" if starting else
                               {"ONLINE": "Monday Core: answering", "RECONNECTING": "Monday Core: reconnecting…"}.get(s, "Monday Core: offline"))
        if s == "ONLINE" and self.booting:
            self._hello()  # (the stream is up: Core's own word on whether it is ready, before any greeting)
        if s == "ONLINE" and self.opened and self.view in ("home", "settings", "chat"):  # Core back: what it couldn't say before, asked
            {"home": self._load_home, "settings": self._load_hooks, "chat": self._load_chat}[self.view]()  # again (Chat: Core replays at
        elif s == "ONLINE":  # most 500 events after a gap; the thread stays when nothing is newer, _got_messages. 2026-10-27)
            QTimer.singleShot(WARM_DATA_MS, self._warm_views)  # (Chat's and Home's words, the pills' states after them, read now: ready when
        self.refresh()                                          # the owner opens the notch)

    def _warm_views(self):
        """Chat's and Home's data read while the notch is closed (the owner, 2026-10-24: "all the connections, all the handshakes ... done when
        I clicked on it"): no chip says N/A at the first opening, and Chat's newest turns are made already (CHAT_PAGE of them, ~20 ms).
        Opening a view still reads it again (_switch). The pills' states (/api/ai-tools, Home's) come after Chat's first page, never ahead
        of it: at a start Core's look at every tool outlives the client's 15 s, and asked first it held a Bridge worker (2026-10-27)."""
        if not self.opened and self.state.online:
            self._load_chat()
            self._load_home()  # (/api/ai-tools among its reads)
        elif self.state.online:  # (open on a view of its own, the agent's or the note: the pills' states still, for Home)
            self.bridge.get("/api/ai-tools", self._got_tools)

    def on_event(self, ev):
        k = ev.get("type")
        was_asks, was_waiting = set(self.state.asks), bool(self.state.asks or self.state.perms)
        self.state.feed(ev)
        d = ev.get("data") or {}
        if k == "MESSAGE_DELTA":  # a reply's next few words: kept, and drawn by _live_t (never everything redrawn for each few words)
            if d.get("stream_id") and not d.get("chatroom"):
                sid = d["stream_id"]
                if self._live_model.get(sid, d.get("model")) != d.get("model"):  # another model took the reply over (the first refused,
                    self._live[sid] = ""                                          # called a tool or stalled): its words alone, 2026-10-27
                self._live_model[sid] = d.get("model")
                self._live[sid] = self._live.get(sid, "") + (d.get("delta") or "")
                if self.opened and self.view == "chat" and not self._live_t.isActive():
                    self._live_t.start()
            return
        self._notify(ev)
        self._finish_card(k, d)
        if k in ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED") and d.get("task"):
            self.last_task = I.last_task(d["task"]) or self.last_task  # (the AI tile's "Last: …", as it ended)
        if k == "MESSAGE_CREATED" and d.get("message"):
            m = d["message"]
            self._live.pop((m.get("meta") or {}).get("stream_id"), None)  # (the stored reply takes the place of its words so far)
            self._live_model.pop((m.get("meta") or {}).get("stream_id"), None)
            self._merge([m])  # (refresh() below draws it, when Chat shows)
        if k == "PERMISSION_REQUIRED":  # Monday's own approval: Home's card (the 2026-10-26 recording), unless the owner is in the chat
            self._sound("needs")
            self.open("chat" if self.opened and self.view == "chat" else "home")
        elif k == "AGENT_ASK":
            self._sound("needs")
            self.open("home")
        elif k == "UPDATE_AVAILABLE" and d.get("action") in ("install", "restart"):  # a newer Monday (docs/adr/0010): said once, on Home
            self._got_update(d, None)
            self._sound("prompt")
            self.open("home")
        elif k == "TASK_CREATED":
            self._sound("prompt")
        elif k == "TASK_COMPLETED":
            self._sound("done")
        elif k == "TASK_FAILED":
            self._sound("error")
        elif k == "AGENT_WATCH" and self.opened and self.view == "home":
            self._fill_dock()
        elif k == "AGENT_WATCH" and self.opened and self.view == "agent":
            self._agent_chat()  # its chat as it goes (CHAT_EVERY_S at most); refresh() below draws the rest
        elif k == "SCREEN_MARKS":  # Monday shows where something is (core/qtoverlay.py Marks), the notch shown or not; a look at the
            self.marks_show(d.get("marks") or [], d.get("avoid"), d.get("front"), d.get("hold"))  # screen sends its words (`avoid`: the
            #   labels stay off them), the window it looked at (`front`) and how long they stay unsaid (`hold`); none: they go ("stop")
        elif k == "UI_NAVIGATE":  # "open your pet", "hide your pet", "go home": asked of Core (Monday.cmd_ui), done here
            self.navigate(d.get("page"))
        waiting = bool(self.state.asks or self.state.perms)
        if (set(self.state.asks) != was_asks or waiting != was_waiting) and self.win.hotkeys is not None:  # Ctrl+Alt+Y / N taken only
            for a in ("allow", "deny"):                                                                  # while something asks
                self.win.hotkeys.set_live(a, waiting)
        self.refresh()

    def _hotkey(self, action):
        if action == "notch":
            self.toggle()
        elif action == "chat":
            self.open("chat")
            self.take_keys()
        elif action == "waiting":
            self.open("home" if self.state.asks or self.state.perms else "chat")
        elif action in ("allow", "deny") and self.state.perms:  # what Home's card shows first: Monday's own approval
            self.decide(list(self.state.perms)[-1], "approve" if action == "allow" else "deny")
        elif action in ("allow", "deny") and self.state.asks:
            a = list(self.state.asks.values())[-1]
            if action == "allow" and a.get("questions"):  # (a question is answered with a pick, not Allow: Home shows its options)
                return self.open("home")
            self.answer_ask(a, action)
        elif action == "pets":  # the wardrobe in the island (the 2026-10-26 recording's Ctrl+Alt+G)
            self.open("wardrobe")
        elif action == "window":
            self.open_window("home")
        elif action == "mic":
            self.win.toggle_mic()
        elif action == "circle":
            self.circle()

    def navigate(self, page):
        """The pet asked for: the notch shown (kept on) with Monday's window put away; hidden (while the desk pet stays); or home: the window."""
        if page == "pet":
            if not self.p["visible"]:
                self.set_pref("visible", True)
            if self.win.isVisible():
                self.win.hide()
        elif page == "pet_hide" and self.p["visible"]:
            self.set_pref("visible", False)  # (kept on when the desk pet is off: one of the two always shows)
        elif page in getattr(self.win, "screens", {}):  # home, or one of the window's pages ("open your Statistics page")
            self.open_window(page)

    # Dotpals' circle and ask, and Monday's marks (core/qtoverlay.py)
    def circle(self):
        """Ctrl+Alt+C or the tray: the screen under the pointer frozen, the owner circles what they mean, and the circled part comes here
        like a dropped picture: What's this?, Ask about it, or on to the chat."""
        from .qtoverlay import Circle
        if getattr(self, "circling", None) is not None:
            return
        self.circling = Circle(self._circled, self._uncircled)
        why = self.circling.start()
        if why:  # Wayland, or macOS without Screen Recording: said, never a silent key
            self.circling = None
            self.state.result = ("failed", "Can't circle here", why, time.monotonic(), 12.0)
            self.refresh()

    def _uncircled(self):
        self.circling = None

    def _circled(self, got):
        self.circling = None
        self.take_file(got["path"], circled=got)

    def marks_show(self, marks, avoid=None, front=None, hold=None, staged=False):
        from .qtoverlay import Marks
        if getattr(self, "marks", None) is None:
            if not marks:
                return False
            self.marks = Marks()
        return self.marks.show_marks(marks, avoid, front, hold, staged)

    def marks_reveal(self, i):
        """A look's mark i, as its part is said (Window._explain)."""
        if getattr(self, "marks", None) is not None:
            self.marks.reveal(i)

    def marks_hold(self, seconds):
        if getattr(self, "marks", None) is not None and self.marks.isVisible():
            self.marks.hold_for(seconds)

    def marks_speaking(self, on):
        """Monday's voice started or stopped (Window._voice_state): marks stay while it says what they're about, then go."""
        if getattr(self, "marks", None) is not None:
            self.marks.speaking(on)

    def marks_fade(self):
        """Esc, the Stop button, "stop talking": the marks go now."""
        if getattr(self, "marks", None) is not None:
            self.marks.fade()

    def _sound(self, tone):
        if self.p["sounds"]:
            play_tone(tone, self.p["volume"])

    # notifications (Dotpals'), for when the owner may not be looking: an agent alert at once, an approval still waiting NOTIFY_WAIT_S (at
    # once while the notch is hidden), a long task when it ends; General's "Notifications" turns them off
    def _notify(self, ev, now=None):
        """An agent's alert at once; an approval or an agent's ask kept in `waits` until answered (_waits_due tells of one still waiting
        NOTIFY_WAIT_S, at once while the notch is hidden); a task that took LONG_TASK_S, when it ends, done or failed."""
        from .notch import LONG_TASK_S, _line
        k, d = ev.get("type"), ev.get("data") or {}
        now = now or time.time()
        if k == "AGENT_ALERT" and d.get("text"):
            who = f"{d.get('agent') or 'An agent'}" + (f" · {d['project']}" if d.get("project") else "")
            self._toast(f"{who} may be stuck" if d.get("kind") == "stuck" else "Two agents, one file", d["text"])
        elif k == "PERMISSION_REQUIRED" and d.get("permission"):
            self.waits[f"p{d['permission']['id']}"] = (now, perm_words(d["permission"]))
        elif k == "AGENT_ASK" and (d.get("ask") or {}).get("id"):
            a = d["ask"]
            self.waits[f"a{a['id']}"] = (now, f"{a.get('agent') or 'An agent'} wants to {a.get('what') or 'act'}")
        elif k in ("PERMISSION_GRANTED", "PERMISSION_DENIED") and d.get("permission"):
            self.waits.pop(f"p{d['permission']['id']}", None)
        elif k == "AGENT_ASK_DONE":
            self.waits.pop(f"a{d.get('id')}", None)
        elif k in ("TASK_COMPLETED", "TASK_FAILED") and d.get("task"):
            t = d["task"]
            took = (t.get("finished_at") or now) - (t.get("started_at") or t.get("created_at") or now)
            if took >= LONG_TASK_S:
                self._toast(f"{'Done' if k == 'TASK_COMPLETED' else 'Failed'} · {t.get('title') or 'a task'}",
                            _line(t.get("result") or t.get("error") or "", 200) or f"It took {round(took / 60)} min.")

    def _toast(self, title, words):
        if self.toast and self.p.get("notify", True):
            self.toast(title, words)

    def _waits_due(self, now=None):
        now = now or time.time()
        for key, (at, words) in list(self.waits.items()):
            if not self.isVisible() or now - at >= NOTIFY_WAIT_S:
                del self.waits[key]
                self._toast("Needs your OK", words)

    def refresh(self):
        """What it shows now, from State: the closed bar's line and the pet's face; the open views' words and buttons."""
        self.state.mic = bool(self.win.ear and self.win.ear.on)
        mine = self.opened and self.view in ("chat", "drop")
        self.shown = self.state.view(mine=mine)
        st, title, detail = self.shown
        self.mood = STATES.get(st, STATES["idle"])[0]
        if self.booting and st in ("idle", "listening", "asleep"):
            self.mood = "flat"  # the start: Core not ready yet, the eyes still closed (they open when its handshake says ready; not
            #                     "sleepy": its z's say Monday is asleep, which it isn't)
        elif time.monotonic() < self._greet_until and st in ("idle", "listening"):
            self.mood = "happy"   # the greeting, once, when Core said it is ready (the recordings' ^ ^)
        self.setAccessibleDescription(f"{title}. {detail}".strip())
        self.sound.icon_name = "volume" if self.p["sounds"] else "mute"
        self.sound.setAccessibleName(f"Sounds: {'on' if self.p['sounds'] else 'off'} (click to turn them {'off' if self.p['sounds'] else 'on'})")
        self.sound.update()
        self.island_tabs.update()
        self.sw.set_quiet(self.p["sounds"])
        self.vol.setEnabled(self.p["sounds"])
        if self.opened:
            if self.view == "chat":
                self._show_chat_state()
            elif self.view == "agent" and not self._show_agent():
                self._switch("home")  # its session is over (or an agent asks): Home's cards (_switch draws them)
                self._go()
            elif self.view == "home":
                self._show_home_state()
            elif self.view == "drop":
                self._show_file()
            elif self.view == "settings":
                self.menu.fill()
            if self.view in ("home", "drop") and abs(self.height() - self.target_size()[1]) > 1 and not self._anim.isActive():
                self._go()  # (an agent's ask, a file's second row of actions: came or went)
        if not self.opened and self.isVisible() and abs(self.width() - self.target_size()[0]) > 1:
            self._go()
        self.update()

    # -- Home: Monday's hooks in Claude Code, an agent asking, the dock ----------------------------------------------------------------
    def _load_sources(self, force=False, only=None):
        """The island's own sources, read on a thread and only while shown, each at most every so often: every AI's limits (core/usage.py,
        with the owner's plan use from their agents' logs in the same read: the plan pill, the Plan widget, the limits look; one reader for
        every look, docs/adr/0011), GitHub through their signed-in gh (github.read: its widget). `only`: that source; `force`: now, every AI
        asked (the owner's Refresh)."""
        now = time.monotonic()
        want = {"usage": True, "github": I.find(self.grid.layout_, "github") is not None}
        every = {"usage": U.EVERY_S if self.opened else USAGE_CLOSED_S, "github": GITHUB_EVERY_S}
        for name in (only,) if only else ("usage", "github"):
            if not want[name] or name in self._reading or (not force and now - self._read_at.get(name, -1e9) < every[name]):
                continue
            self._reading.add(name)
            self._read_at[name] = now
            ctx = {"tools": dict(self.tools), "last": {c["key"]: c for c in self.usage or []}, "due": None if force else self._usage_due()}
            if name == "usage":
                self._usage_asked = time.time()
            threading.Thread(target=self._read_source, args=(name, ctx), daemon=True).start()

    def _read_source(self, name, ctx=None):
        try:
            if name == "usage":
                from . import watch
                plans = watch.plan_use()
                self.source_read.emit("plan", plans)  # (the plan pill and the Plan widget, as before: the same read of the logs)
                ctx = ctx or {}
                data = U.read_all(ctx.get("tools"), ctx.get("last"), ctx.get("due"), plans=plans)
            elif name == "plan":
                from . import watch
                data = watch.plan_use()
            else:
                from . import github
                data = github.read(repo=(self.island.get("github") or {}).get("repo"))
        except Exception as e:  # a reader's own fault: said on its widget, never the notch's end (the limits keep their last reading)
            data = [] if name == "plan" else None if name == "usage" else {"ok": False, "why": f"Reading GitHub failed ({type(e).__name__}).",
                                                                           "at": time.time()}
        try:
            self.source_read.emit(name, data)
        except RuntimeError:  # the notch went while gh answered (Monday quitting): no one to tell
            pass

    def _got_source(self, name, data):
        self._reading.discard(name)
        if name == "plan":
            self.plan = data if isinstance(data, list) else []
            self._plan_pill()
        elif name == "usage":
            self._got_usage(data)
        else:
            self.github = data if isinstance(data, dict) else None
        self.grid.fill()

    def _plan_pill(self):
        """The header's plan pill (the recording's "● Codex 22%"): the first plan's busiest window, only when an agent's own log said it."""
        rows = I.plan_rows(self.plan)
        p = rows[0] if rows and rows[0]["rows"] else None
        self.plan_btn.setVisible(bool(p))
        if p:
            name, used, when = max(p["rows"], key=lambda r: r[1])
            self.plan_btn.setText(f"{p['agent']} {used}%")
            self.plan_btn.setAccessibleName(f"{p['agent']} plan: {name} {used}% used" + (f", resets {when}" if when else "") + f" ({p['head']}). "
                                            "Click for the plan")
            self.plan_btn.setToolTip(self.plan_btn.accessibleName())

    def show_plan(self):
        """The plan pill clicked: the plan on Home's card (the recording put it where the session was), until its OK."""
        self._plan_card_at = time.monotonic()
        self.open("home")
        self.refresh()

    def github_repo_menu(self):
        """The GitHub widget's own setting: which repository its CI and stars are of (the owner's, as gh lists them), kept with the island."""
        g = self.github or {}
        m = QMenu(self)
        names = [x for x in (g.get("repos") or []) if x] or ([g["repo"]] if g.get("repo") else [])
        chosen = (self.island.get("github") or {}).get("repo")
        last = m.addAction("The one I pushed to last")
        last.setCheckable(True)
        last.setChecked(not chosen)
        last.triggered.connect(lambda: self._github_repo(None))
        for name in names:
            act = m.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == chosen)
            act.triggered.connect(lambda _=False, n_=name: self._github_repo(n_))
        self.menu_open = True
        m.exec(QCursor.pos())
        self.menu_open = False

    def _github_repo(self, name):
        self.island["github"] = {"repo": name} if name else {}
        self.keep_island()
        self._load_sources(force=True)

    def _load_home(self):
        self._load_sources()
        self._load_hooks()
        self.bridge.get("/api/ai-tools", self._got_tools)  # (the pills' states: what Core last found for each agent and tool)
        self.bridge.get("/api/tasks?status=COMPLETED,FAILED,CANCELLED&limit=1", self._got_last)  # (the AI tile's "Last: …")
        self.bridge.get("/api/update", self._got_update)  # (a newer Monday Core found before this notch started: its card)
        # the owner's own sessions as Core has them now: Core sends AGENT_WATCH only when they change, so a window that just connected
        # would show none until the next change (2026-10-20: the code card missing after a reload)
        self.bridge.get("/api/agents/watch", lambda d, err: None if err or not isinstance(d, dict) else self.on_event({"type": "AGENT_WATCH", "data": d}))
        self._fill_dock()
        if sys.platform == "win32":  # (EnumWindows; macOS and Linux: the agents' own sessions only, from Core)
            self.places_pending = True
            threading.Thread(target=self._read_places, daemon=True).start()

    def _got_update(self, d, err):
        """The update channel's state (docs/adr/0010): a card when there is something to do (install or restart), else none."""
        if not err and isinstance(d, dict):
            self.update_offer = d if d.get("action") in ("install", "restart") and (d.get("latest") or {}).get("version") else None
            self.refresh()

    def _update_go(self, card):
        """The update card's Update / Restart: the window asks Core for the checked package and opens Windows' installer, or restarts
        Monday (Window.update_now); anything that stops it is said on the card."""
        self._update_words = "Checking the package against your signature…" if card.get("action") == "install" else "Restarting Monday…"
        self.refresh()
        self.win.update_now(lambda words: (setattr(self, "_update_words", words), self.refresh()))

    def _load_hooks(self):
        self.hooks_asking = True
        self.bridge.get("/api/agents/hooks", self._got_hooks)

    def _got_hooks(self, d, err):
        self.hooks_asking = False
        self.hooks = None if err or (d or {}).get("error") else d
        n, of = sum(bool((d or {}).get(k)) for k in HOOKS) if self.hooks else None, len(HOOKS)
        self.cc_dot.set("off" if n is None else "ok" if n == of else "wait" if n else "bad")
        self.cc_words.setText("Claude Code: N/A" if n is None else f"Claude Code: {f'all {of} hooks' if n == of else f'{n} of {of} hooks'}")
        self._show_home_state()

    def _got_tools(self, d, err):
        if not err and isinstance((d or {}).get("tools"), list):  # (a failed read keeps the last one Core gave; none yet: no pills)
            self.tools = {t.get("key"): t for t in d["tools"] if isinstance(t, dict) and t.get("key")}
            self._fill_dock()

    def _read_places(self):
        """The open IDE windows (computer.windows: EnumWindows; watch.ide_windows: which are IDEs, their folders), off the UI thread."""
        try:
            import psutil
            from . import computer, watch

            def exe_of(pid):
                try:
                    return psutil.Process(pid).exe()
                except Exception:
                    return None
            ides = watch.ide_windows([(w.hwnd, w.pid, w.title, w.process) for w in computer.windows()], exe_of)
        except Exception as e:
            print("notch: reading the open windows:", repr(e)[:200], flush=True)
            ides = []
        try:
            self.places_read.emit(ides)
        except RuntimeError:  # the notch went while Windows answered (Monday quitting)
            pass

    def _got_places(self, ides):
        from .notch import agent_places
        try:
            self.places = agent_places(ides, self.state.watch)
        except Exception:
            self.places = []
        self.places_pending = False
        self._fill_dock()
        self.update()

    def _fill_dock(self):
        """The chip row under the AI tile's words when it is Big (the recording's "Claude …"): the dock's pills (the open IDE windows, then
        the owner's Active pills; the closed bar shows four of them too; past Q.PILLS_ON_AI, "N more"); while one of the owner's sessions
        runs, no dock (the owner, 2026-10-20) but that session's own agent, its view a click away. Made again only when what it shows
        changed. With the Agents widget on Home (the 2026-10-26 recording's pills beside the session), the dock is its, not the AI tile's."""
        agents = self.grid.tiles.get("agents") if hasattr(self, "grid") else None
        if agents is not None:
            agents.fill()
        live = self.state.live()
        if live:
            w = self.state.agent if self.state.agent in live else next((s for s in live if s.get("key") == self.state.pick), live[0])
            host = next((pl for pl in self.places or [] if pl.get("session") and pl.get("session") == w.get("key")), {})
            who = w.get("agent") or "Agent"
            shown = [{"name": who, "session": w.get("key"), "app": host.get("app") or who.lower().replace(" ", "_"), "exe": host.get("exe")}]
        elif agents is not None:
            shown = []
        else:
            items = self._dock_items()
            n = Q.PILLS_ON_AI
            shown = items[:n] if len(items) <= n else items[:n - 1] + [{"name": f"{len(items) - n + 1} more", "more": True}]
        sig = (id(self.dock_grid), bool(live), tuple((pl.get("name"), pl.get("session"), pl.get("exe"), pl.get("folder"), str(pl.get("state")))
                                                      for pl in shown))
        if sig == getattr(self, "_dock_sig", None) and self.dock_grid.count():
            return None
        self._dock_sig = sig
        while self.dock_grid.count():
            it = self.dock_grid.takeAt(0)
            w_ = it.widget()
            if w_:
                w_.hide()  # (gone at once: a pill waiting for deleteLater mustn't paint or be counted)
                w_.deleteLater()
        for pl in shown:
            pill = Pill(pl.get("name") or "?", self._colour(pl), pl.get("folder") or "", pl.get("exe"), pl.get("state"))
            if pl.get("session"):
                pill.setAccessibleName(f"{pl['name']}: this session's agent (its view)")
            pill.clicked.connect(lambda pl=pl: self._go_place(pl))
            self.dock_grid.addWidget(pill, 0 if live else 1)  # (the session's own: as wide as its name, never "Claude Co…", 2026-10-26)
        if live:
            self.dock_grid.addStretch(1)
        elif len(shown) < Q.PILLS_ON_AI:
            self.dock_grid.addStretch(Q.PILLS_ON_AI - len(shown))  # (a lone pill keeps a pill's width)

    def ai_face(self):
        """What the AI tile says now (the recording's "Idle · Last: Fix the checkout total · 12:32 · webshop · ✓ Done"): one of the owner's
        live sessions (its app's name and folder, its last steps, a dot per session), else an agent's own line (an alert), else Monday's own
        state (working, waiting, an answer…), else Monday's last finished task and how it ended. Only what Core said."""
        live = self.state.live()
        av = self.state.agent_view()  # (sets state.agent: the session shown, the owner's pick else the first live one)
        w = self.state.agent if live else None
        self.card_session = w
        r = self.state.agent_result
        alert = bool(r and time.monotonic() - r[3] < r[4])
        st, title, detail = self.shown
        dot = {"waiting": "wait", "working": "wait", "asked": "wait", "failed": "bad", "offline": "off", "alert": "wait", "hearing": "wait"}.get(st, "ok")
        keys = [x.get("key") for x in live]
        dots = (keys, next((i for i, k in enumerate(keys) if w and k == w.get("key")), 0))
        if w and not alert:
            where = folder_words((w.get("where") or {}).get("cwd"))  # "monday › app": which folder it works in (the owner, 2026-10-20 night)
            head = w.get("title") if w.get("state") == "done" else (w.get("name") or w.get("agent") or "Agent") + (f" · {where}" if where else "")
            steps = next((v[2][1:] for v in w.get("steps") or [] if v and v[0] == "all"), None)  # (its first line sums them up)
            rows = (steps or w.get("lines") or ["Working…"])[-LIVE_ROWS:]
            return {"title": head, "dot": "wait" if w.get("state") == "working" else "ok", "rows": rows, "line": rows[-1], "session": w,
                    "dots": dots}
        if av:  # an agent's own line: an alert, a standing yes it used (the dots stay: the sessions are still there)
            return {"title": av[1], "dot": "wait" if av[0] in ("working", "waiting", "alert") else "ok", "line": _head(av[2], 160), "dots": dots,
                    "session": w}
        t, line, sub, outcome = I.ai_words(st, title, self.last_task, Q.h24())
        if line is None:  # Monday at work, waiting, hearing…: its own words
            return {"title": title, "dot": dot, "line": _head(detail, 160)}
        return {"title": t, "dot": dot, "line": line, "sub": sub, "outcome": outcome}

    def ai_tap(self):
        """The AI tile tapped: the session on it, its view (its real edits, Steps, Story; the owner, 2026-10-20); else Monday's chat."""
        if self.card_session:
            return self.show_agent()
        self.show_view("chat")

    def _got_last(self, d, err):
        if not err and isinstance(d, dict) and d.get("tasks"):
            last = I.last_task(d["tasks"][0])
            if last and (not self.last_task or float(last.get("at") or 0) >= float(self.last_task.get("at") or 0)):
                self.last_task = last
                self.grid.fill()

    def _show_session(self, key):
        """A dot clicked: that session on Home (its card, and the ↗ to its chat)."""
        self.state.pick = key
        self.refresh()

    def _to_chat(self, w=None):
        """The ↗ (the owner, 2026-10-20: "this button will open the chat that we are working on rn"): with a session of the owner's on
        show (or the one given: a finished card's), its own window (the Claude app's, its IDE's) to the front; a session with no window of
        its own, its view here; else Monday's window."""
        w = w if isinstance(w, dict) else (self.state.watched() or (None, None))[1]  # (a button's click passes False)
        if not w:
            return self.open_window("home")
        link = self._links.get(w.get("key")) or chat_link(w)  # the Claude app or the Codex app: that very chat by its own link (its
        # window is usually in front already, so bringing it forward did nothing: 2026-10-20, "when we click on the notch it should work")
        if link:
            self._links[w.get("key")] = link
            if self._open_url(link):
                return None
        pl = next((p for p in self.places or [] if p.get("session") == w.get("key") and p.get("hwnd")), None)
        if pl and self._front(pl):
            return None
        return self.show_agent(w.get("key"))

    def _dock_items(self):
        """What the dock shows, in order: the open IDE windows (else the live sessions), then the owner's Active pills (Monday's pick until
        they choose) as Core last found them, a tool with its window already there not twice."""
        places = self.places or [{"name": s.get("agent") or "Agent", "folder": os.path.basename(str((s.get("where") or {}).get("cwd") or "")),
                                  "session": s.get("key"), "app": "claude"} for s in self.state.live()]  # (agent_places' shape: the key)
        return places + dock_pills(self.p.get("pills"), self.tools, places)

    @staticmethod
    def _colour(pl):
        key = pl.get("pill") or str(pl.get("app") or pl.get("key") or "").lower()
        return PILL_COLOURS.get(key, PALS["grey"] if pl.get("more") else PALS["purple"])

    def _go_place(self, pl):
        """A pal clicked: one with a session of the owner's that the watch shows opens its view (Home's agent view, as the retired notch
        did; its Go to brings the window forward); any other window comes to the front (restored if minimised; the click gave Monday the
        right to); "N more" and an agent with no window of its own: Monday's Agents page."""
        if pl.get("session") and any(s.get("key") == pl["session"] for s in self.state.live()):
            return self.show_agent(pl["session"])
        if pl.get("more") or not pl.get("hwnd") or sys.platform != "win32":
            return self.open_window("agents")
        if not self._front(pl):
            self._said(f"Windows didn't let Monday bring {pl.get('name')} to the front: it's in your taskbar.")

    def _front(self, pl):
        """A dock window to the front, restored if minimised (the owner's click gave Monday the right to) -> whether the OS let it.
        Windows only: elsewhere the dock has no windows of its own (they come from EnumWindows)."""
        if sys.platform != "win32" or not pl.get("hwnd"):
            return False
        import ctypes
        u = ctypes.windll.user32
        if u.IsIconic(pl["hwnd"]):
            u.ShowWindow(pl["hwnd"], 9)  # SW_RESTORE
        return bool(u.SetForegroundWindow(pl["hwnd"]))

    def home_card_now(self, now=None, wall=None):
        """What Home's whole-width card shows now, or None (the grid): Monday's own approval first (a shutdown's confirmation…), then one of
        the owner's agents asking (or Claude Code's question), then a task of Monday's or a session of the owner's that just finished, for
        RESULT_S and until its OK (the 2026-10-26 recording: "needs permission", Deny [N] / Allow [Y] in amber; "finished", its result, in
        green). Only what Core said."""
        now = now if now is not None else time.monotonic()
        wall = wall if wall is not None else time.time()
        p = list(self.state.perms.values())[-1] if self.state.perms else None
        if p:  # its detail's first line is the act ("Shut down MSI · now", the command), then what it means ("Why: …" only repeats the act)
            lines = [x.strip() for x in str(p.get("detail") or "").split("\n") if x.strip()]
            said = [x for x in lines[1:] if not x.startswith("Why:")] or lines[1:]
            risk = f"{str(p.get('risk')).capitalize()} risk. " if p.get("risk") else ""
            return {"kind": "perm", "id": p["id"], "who": "Monday", "verb": "needs permission", "dot": self.p["body"], "badge": "ask",
                    "cmd": lines[0] if lines else perm_words(p), "line": risk + " ".join(said), "always": bool(p.get("always"))}
        a = list(self.state.asks.values())[-1] if self.state.asks else None
        if a:
            if a.get("questions"):
                return {"kind": "question", "id": a["id"], "who": a.get("agent") or "An agent", "verb": "asks you", "badge": "ask",
                        "dot": agent_colour(a.get("agent"))}
            detail = [x.strip() for x in str(a.get("detail") or "").split("\n") if x.strip()]
            left = max(0, int((a.get("until") or 0) - wall)) if a.get("until") else None
            words = [f"Wants to {a.get('what') or 'act'}"] + ([f"worth a second look: {', '.join(a['flags'])}"] if a.get("flags") else []) + \
                ([f"in {a['project']}"] if a.get("project") else []) + ([f"{left // 60}:{left % 60:02d} left here"] if left is not None else [])
            return {"kind": "ask", "id": a["id"], "who": a.get("agent") or "An agent", "verb": "needs permission", "badge": "ask",
                    "dot": agent_colour(a.get("agent")), "cmd": detail[0] if detail else None, "line": " · ".join(words),
                    "always": bool(a.get("always"))}
        f = self._finished  # the end of what the owner just allowed from this card (decide / answer_ask), as Core reported it
        if f and now - f["at"] < RESULT_S and self._card_seen != f["id"]:
            return {**f, "until": f["at"] + RESULT_S}
        if self._plan_card_at is not None and self._card_seen != ("plan", self._plan_card_at):  # the plan pill's click, until OK
            rows = I.plan_rows(self.plan)
            p = rows[0] if rows else None
            return {"kind": "plan", "id": ("plan", self._plan_card_at), "who": f"{p['agent']} plan" if p else "Plan",
                    "verb": p["head"] if p else "nothing read yet", "dot": Q.PLAN_GREEN, "badge": None, "tint": CARD_TINTS["done"],
                    "result": "  ·  ".join(f"{r[0]} {r[1]}%" + (f" ↻ {r[2]}" if r[2] else "") for r in p["rows"]) if p else
                    "No plan use in any agent's log", "line": "Claude plan: N/A (Claude Code's log doesn't say its limits)"}
        u = self.update_offer  # last: a newer Monday (docs/adr/0010) never stands in front of an approval, an ask or a result
        v = (u or {}).get("latest", {}).get("version") if u else None
        if v and self._card_seen != ("update", v):
            install = u.get("action") == "install"
            note = next((x.strip() for x in str(u["latest"].get("notes") or "").split("\n") if x.strip()), "")
            return {"kind": "update", "id": ("update", v), "who": "Monday", "verb": f"{v} is ready" if install else f"{v} is on this PC",
                    "dot": self.p["body"], "badge": None, "action": u.get("action"),
                    "line": self._update_words or note or ("Update opens Windows' installer; Monday starts again when it finishes." if install
                                                           else "Restart Monday to load it. It's back in about a minute.")}
        return None

    def _show_home_state(self):
        card = self.home_card_now()
        a = list(self.state.asks.values())[-1] if self.state.asks else None
        qs = (a or {}).get("questions") if card and card["kind"] == "question" else None
        if qs and self._q_for != a["id"]:
            self._q_for, self._q_at, self._q_picks, self._q_typing = a["id"], 0, {}, False
        keyed = {d: not (self.win.hotkeys is not None and self.win.hotkeys.status(d)[0] != "on") for d in ("allow", "deny")}
        from . import hotkeys as hk_mod
        km = self.win.hotkeys.km if self.win.hotkeys is not None else hk_mod.defaults()  # the keys as they are on this keyboard (2026-10-20 night)
        key_of = {d: (km.get(d, {}).get("keys") or "") if keyed[d] else "" for d in ("allow", "deny")}  # (a key Windows didn't give Monday isn't shown)
        sig = (card["kind"], str(card["id"]), bool(card.get("always")), key_of["allow"], key_of["deny"], self._q_at,
               tuple(self._q_picks.get(self._q_at) or ()), self._q_typing) if card else None
        if sig != self._asks_drawn:  # (rebuilt only when the card changes: a Core event mid-click mustn't swap Allow under the pointer)
            self._asks_drawn, self.q_field = sig, None  # ("Other…"'s line goes with the card it was on)
            for lay in (self.ask_buttons, self.ask_grid):
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.hide()
                        w.deleteLater()
            if card:
                self._card_buttons(card, a, key_of)
            if card and card.get("until"):  # a finished card goes once its time is up, without an event to say so
                QTimer.singleShot(int(max(0.0, card["until"] - time.monotonic()) * 1000) + 60, self.refresh)
        self.home_card.setVisible(bool(card))  # it takes Home's whole width (its answers and their keys fit); else the grid, or in the
        self.grid.setVisible(not card and self.mode != "limits")  # limits look every AI's limits (docs/adr/0011)
        self.limits_view.setVisible(not card and self.mode == "limits")
        self._card_who = card and card["who"]  # (whose card: Monday's own shows Monday's pet, not the watched session's mascot: mascot)
        self.card_pet.set_badge(card and card["badge"])
        self.home_card.set_tint(None if not card else card.get("tint") or CARD_TINTS.get(card.get("badge")))
        if not card:
            if self.mode == "limits":
                self.limits_view.fill()
            self.grid.fill()  # (the AI tile first: it says which session shows)
            return self._fill_dock()  # (its chip row: the session's own agent, or the dock again once the last session is over)
        self.grid.edit(False)
        self.agent_dot.set(card["dot"])
        self.agent_title.set(card["who"])
        verb, line = card["verb"], card.get("line") or ""
        if qs:  # Claude Code's question on show: its words, then its options (built above) as the answers
            q = qs[self._q_at]
            verb += f" ({self._q_at + 1} of {len(qs)})" if len(qs) > 1 else ""
            line = _head(q["question"], 300) + (" (pick any)" if q["multi"] else "")
        self.agent_verb.setText(verb)
        self.agent_cmd.set(card.get("cmd") or "")
        self.agent_cmd.setVisible(bool(card.get("cmd")))
        self.card_result.set(card.get("result") or "")
        self.card_result.setVisible(bool(card.get("result")))
        two = not qs  # an ask's words on two lines at most (what it means is safety text: whole, its tip has all); a question's wrap
        self.agent_line.setWordWrap(True)
        room = max(120, self.home_card.width() - HOME_PET - 48)
        self.agent_line.setText(Q.fit_lines(line, self.agent_line.font(), room, 2) if two else _head(line, 300))
        self.agent_line.setToolTip(line if two and len(line) > 60 else "")
        self.agent_line.setVisible(bool(line))
        self.home_card.setAccessibleName(f"{card['who']} {verb}. {card.get('cmd') or card.get('result') or ''}. {line}".strip())

    def _card_buttons(self, card, a, key_of):
        """The card's answers: Deny / Always (when the owner's mode offers it) / Allow, each with its key as this PC holds it; a question's
        options; a finished card's way to it, and OK."""
        if card["kind"] == "question":
            self._question_buttons(a)
        elif card["kind"] == "update":  # (Deny / Allow's places: Later the quiet one, the act the light one)
            later = Button("Later", "secondary", height=26, size=12, name="Later: put the update away")
            later.clicked.connect(lambda _=False, c=card: self._card_ok(c))
            install = card.get("action") == "install"
            go = Button("Update" if install else "Restart", "light", height=26, size=12,
                        name="Update: Windows' installer opens and Monday closes" if install else "Restart: Monday starts again on the new code")
            go.clicked.connect(lambda _=False, c=card: self._update_go(c))
            self.ask_buttons.addWidget(later)
            self.ask_buttons.addWidget(go)
        elif card["kind"] in ("perm", "ask"):
            for words, decision, kind in (("Deny", "deny", "secondary"), ("Always", "always", "secondary"), ("Allow", "allow", "light")):
                if decision == "always" and not card.get("always"):
                    continue
                key = key_of.get(decision, "")
                b = Button(words, kind, height=26, size=12, key=key, name=f"{words}" + (f" ({key})" if key else ""))
                if card["kind"] == "perm":  # Monday's own: Core's permission (approve / deny / always)
                    b.clicked.connect(lambda _=False, pid=card["id"], d={"allow": "approve"}.get(decision, decision): self.decide(pid, d))
                else:
                    b.clicked.connect(lambda _=False, a=a, d=decision: self.answer_ask(a, d))
                self.ask_buttons.addWidget(b)
        else:
            if card.get("go"):  # (the plan card has nowhere to go: its OK only)
                go = Button(card["go"], "light", height=26, size=12)
                go.clicked.connect(lambda _=False, c=card: self._card_go(c))
                self.ask_buttons.addWidget(go)
            ok = Button("OK", "secondary", height=26, size=12, name="OK: put this away")
            ok.clicked.connect(lambda _=False, c=card: self._card_ok(c))
            self.ask_buttons.addWidget(ok)
        self.ask_buttons.addStretch(1)

    def _card_go(self, card):
        """A finished card's way to it: the session's own window or chat; Monday's task on its window's Tasks page."""
        self._card_ok(card)
        if card.get("session"):
            return self._to_chat(card["session"])
        self.open_window("tasks")

    def _card_ok(self, card):
        self._card_seen = card["id"]
        self.refresh()

    def _finish_card(self, k, d):
        """The end of what the owner allowed from Home's card, as Core reports it (TASK_COMPLETED / FAILED for Monday's task; a session of
        that agent in that folder done after the answer): kept for Home's "finished" card."""
        t = d.get("task") or {}
        if k in ("TASK_COMPLETED", "TASK_FAILED") and t.get("id") in self._flow:
            self._flow.discard(t["id"])
            ok = k == "TASK_COMPLETED"
            first = next((x.strip() for x in str((t.get("result") if ok else t.get("error")) or t.get("title") or "").split("\n") if x.strip()), "Done.")
            self._finished = {"kind": "done" if ok else "failed", "id": ("task", t["id"]), "who": "Monday", "dot": self.p["body"],
                              "verb": "finished" if ok else "couldn't finish", "badge": "done" if ok else "failed", "result": _head(first, 140),
                              "go": "Show", "at": time.monotonic()}
        elif k == "AGENT_WATCH" and self._flow_agents:
            for s in d.get("sessions") or []:
                folder = os.path.basename(str((s.get("where") or {}).get("cwd") or ""))
                hit = next((f for f in self._flow_agents if s.get("state") == "done" and (s.get("done_at") or 0) >= f[2] - 1 and f[0] == s.get("agent")
                            and (not f[1] or f[1] == folder)), None)
                if hit:
                    self._flow_agents.remove(hit)
                    who = s.get("agent") or "Agent"
                    self._finished = {"kind": "done", "id": ("session", s.get("key"), s.get("done_at")), "who": who, "dot": agent_colour(who),
                                      "verb": "finished", "badge": "done", "result": _head(s.get("said") or s.get("title") or "Done.", 140),
                                      "go": f"Go to {who}", "session": s, "at": time.monotonic()}
                    break

    def answer_ask(self, a, decision, answers=None):
        if decision in ("allow", "always") and not a.get("questions"):  # its end, once Core sees the session done, is Home's next card
            self._flow_agents = [*self._flow_agents[-3:], (a.get("agent"), a.get("project"), time.time())]
        self.bridge.post(f"/api/agents/ask/{a['id']}", {"decision": decision, **({"answers": answers} if answers else {})},
                         lambda _d, err: self._said(f"Your answer wasn't recorded: {err}" if err else None))

    def _question_buttons(self, a):
        """The question on show's options, two a row (each one's description as its tip), "Other…" (the owner's own words, as Claude
        Code's prompt offers: 2026-10-20, "I can't see the other option"), Done for a multi-select, and "Answer in Claude Code"."""
        q = a["questions"][self._q_at]
        picks = self._q_picks.get(self._q_at) or []
        self.q_field = None
        if self._q_typing:  # "Other…": a line for the owner's own answer, Enter or Send to give it
            self.q_field = f = K.field("Your answer, then Enter", f"Your own answer to: {_head(q['question'], 80)}")
            f.setMaxLength(OTHER_CHARS)
            f.returnPressed.connect(lambda: self._other(a, f.text()))
            self.ask_grid.addWidget(f, 0, 0, 1, 2)
            send = Button("Send", "light", height=24, size=11.5, name="Send your answer")
            send.clicked.connect(lambda: self._other(a, f.text()))
            back = Button("Back to the options", "quiet", height=24, size=11.5)
            back.clicked.connect(lambda: self._typing(False))
            self.ask_buttons.addWidget(send)
            self.ask_buttons.addWidget(back)
            QTimer.singleShot(0, lambda: self.q_field is f and self.take_keys(f))
            return
        for i, o in enumerate(q["options"]):
            on = o["label"] in picks
            b = Button(("✓ " if on else "") + o["label"], "light" if on else "secondary", height=24, size=11.5,
                       name=o["label"] + (f": {o['description']}" if o["description"] else ""))
            b.setToolTip(o["description"])
            b.clicked.connect(lambda _=False, lab=o["label"]: self._pick(a, lab))
            self.ask_grid.addWidget(b, i // 2, i % 2)
        n = len(q["options"])
        other = Button("Other…", "secondary", height=24, size=11.5, name="Other: type your own answer")
        other.setToolTip("Type your own answer")
        other.clicked.connect(lambda: self._typing(True))
        self.ask_grid.addWidget(other, n // 2, n % 2)
        if q["multi"]:
            done = Button("Done", "light", height=24, size=11.5, name="Done: send these picks")
            done.setEnabled(bool(picks))
            done.clicked.connect(lambda: self._next_question(a))
            self.ask_buttons.addWidget(done)
        later = Button("Answer in Claude Code", "quiet", height=24, size=11.5, name="Answer this in Claude Code instead")
        later.clicked.connect(lambda: self.answer_ask(a, "gone"))
        self.ask_buttons.addWidget(later)

    def _pick(self, a, label_):
        """One of the owner's picks for the question on show: a single choice moves on; a multi-select's toggles until Done."""
        q = a["questions"][self._q_at]
        picks = self._q_picks.setdefault(self._q_at, [])
        if q["multi"]:
            picks.remove(label_) if label_ in picks else picks.append(label_)
            return self._show_home_state()
        picks[:] = [label_]
        self._next_question(a)

    def _typing(self, on):
        """"Other…" opened (a line to type in), or closed again (the options back)."""
        self._q_typing = on
        if not on:
            self.give_keys()
        self.refresh()

    def _other(self, a, text):
        """The owner's own words as the answer to the question on show (with a multi-select's picks), then the next question."""
        text = " ".join((text or "").split())
        if not text:
            return
        q, picks = a["questions"][self._q_at], self._q_picks.setdefault(self._q_at, [])
        picks[:] = [*picks, text] if q["multi"] else [text]
        self._q_typing = False
        self.give_keys()
        self._next_question(a)

    def _next_question(self, a):
        """The next of Claude Code's questions, or, after the last, every pick sent as its answers (they reach the app through the hook)."""
        if self._q_at + 1 < len(a["questions"]):
            self._q_at += 1
            return self.refresh()
        self.answer_ask(a, "allow", {q["question"]: ", ".join(self._q_picks.get(i) or []) for i, q in enumerate(a["questions"])})

    def _said(self, words):
        if words:
            self.state.result = ("failed", "Not done", words, time.monotonic(), 12.0)
            self.refresh()

    # -- Home's agent view: one of the owner's own agents in detail (AGENT_WATCH), its chat, Write to, Story, Steps, Rewind… -------------
    def show_agent(self, key=None):
        """Home's agent view on one of the owner's sessions (a pal in the dock, Home's Details): that one, else the one Home shows."""
        if key:
            self.state.pick = key
        self.story = None
        self.show_view("agent")

    def _agent_back(self):
        """Its ←: from Story or Steps back to what it's doing now; else back to Home's cards."""
        if self.story is not None:
            return self.story_close()
        self.show_view("home")

    def _agent_act(self, act):
        w = self.state.agent or {}
        if act == "next":
            self.next_agent()
        elif act == "story":
            self.story_show()
        elif act == "steps":
            self.steps_show("key")
        elif act == "rewind" and (w.get("rewind") or {}).get("id"):
            self.rewind_open(w["rewind"])

    def _show_agent(self):
        """The agent view from State: the session shown (the owner's pick, else the first live one) with its words, its code card and
        bars, its buttons and its chat; its Story or Steps in their place while one shows. Rebuilt only when what it shows changed.
        -> False when there is no session to show (it finished over a minute ago), or one of the owner's agents asks: Home answers that."""
        got = self.state.watched()
        if not got or self.state.asks:
            return False
        self.state.agent = w = got[1]
        st, title, detail = self.state._session_words(got)
        if self.story and self.story.get("key") != w.get("key"):  # another session shown now (Next agent, a pal): its own view
            self.story = None
        mode, now = (self.story or {}).get("live"), time.monotonic()
        r = self.state.agent_result if self.state.agent_result and now - self.state.agent_result[3] < self.state.agent_result[4] else None
        turns = (self.agent_talk[1] if self.agent_talk[0] == w.get("key") else [])[-AGENT_TURNS:]
        view, chips = None, []
        if mode == "steps":
            view, lines, chips = steps_view(w, self.story.get("view"))
            words = "\n".join(lines) or "No runs or edits in this request yet."
        elif mode == "story":
            words = "\n".join(self.story.get("lines") or [])
        else:
            words = said_elsewhere(_plain(detail), turns)
        acts = [] if mode else agent_actions(w, self.state.live())
        pl = self._place_of(w)
        note = f"{r[1] or STATES[r[0]][1]}: {r[2]}" if r else ""
        sig = (w.get("key"), st, title, words, note, mode, view, tuple(chips), bool((self.story or {}).get("text")), str(w.get("diff")),
               str(w.get("meters")), tuple(a[:2] for a in acts), str(turns) if not mode else "", (pl or {}).get("hwnd"))
        if sig == self._agent_drawn:
            return True
        self._agent_drawn = sig
        self.ag_dot.set("wait" if st == "working" else "ok")
        self.ag_title.set(title)
        self.ag_back.setAccessibleName("Back to what it's doing now" if mode else "Back to Home")
        self.ag_back.setToolTip(self.ag_back.accessibleName())
        self.ag_note.setText(note)
        self.ag_note.setStyleSheet(f"color: {K.RED_TEXT if r and r[0] == 'failed' else TEXT}; background: transparent;")
        self.ag_note.setVisible(bool(note))
        self.ag_line.setText(words)
        self.ag_line.setVisible(bool(words))
        self.code.set_card(None if mode else code_card(w.get("diff")), (w.get("where") or {}).get("cwd"))  # (its last edit, done too)
        self.meters.set_rows([] if mode else meter_rows(w.get("meters")))
        self.ag_goto.setVisible(bool(pl))
        if pl:
            self.ag_goto.setText(f"Go to {pl.get('name')}")
            self.ag_goto.setAccessibleName(f"Bring {pl.get('name')}" + (f" · {pl['folder']}" if pl.get("folder") else "") + " to the front")
        who = agent_words(w)
        self.say_box.setPlaceholderText(f"Write to {who}…")
        self.say_box.setAccessibleName(f"Write to {who} (Enter sends, Shift+Enter starts a new line, Esc clears)")
        while self.ag_buttons.count():
            b = self.ag_buttons.takeAt(0).widget()
            if b:
                b.hide()  # (gone from the screen now: deleteLater waits for the event loop)
                b.deleteLater()

        def add(b, fn):
            b.clicked.connect(fn)
            self.ag_buttons.addWidget(b)
            b.show()  # (now, not on the loop's next turn as a layout would: the view measures itself below)
        if mode == "steps":  # Dotpals' filters, its Files and its Check: the one shown lit
            for v, chip in chips:
                what, n = chip.rsplit(" ", 1) if " " in chip else (chip, "")
                add(Button(chip, "light" if v == view else "quiet", height=24, name=f"Show {what.lower()} of this request ({n})"),
                    lambda _=False, v=v: self.steps_show(v))
        elif mode == "story" and self.story.get("text"):
            add(Button("Copy today", "light", height=24, name="Copy today's story"), self.story_copy)
        for act, words_, name in acts:
            add(Button(words_, "secondary", height=24, name=name), lambda _=False, a=act: self._agent_act(a))
        self.ag_buttons.addStretch(1)
        self._fill_agent_thread([] if mode else turns)
        if self.opened and self.view == "agent":
            self._go()
        return True

    def _fill_agent_thread(self, turns):
        """The agent's chat, its last turns (Core's, from its log): the owner's words on the right in a bubble (faint while they wait for
        its next step: Write to), the agent's on the left in plain words, a long turn at work as its newest words."""
        while self.ag_thread.count():
            it = self.ag_thread.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        for t in turns:
            mine, waiting, copied = t.get("who") == "you", bool(t.get("waiting")), bool(t.get("copied"))
            text = _head(str(t.get("text") or ""), 300) if mine else _tail(_plain(t.get("text")), AGENT_TAIL)
            how = ("\n(waking it with them)" if t.get("how") == "wake" else "\n(queued in Codex: read when it next runs)" if t.get("how") == "queue"
                   else "\n(waiting for its next step)") if waiting else "\n(copied: paste it in its chat)" if copied else ""
            lb = label(text + how, 12, 400, TEXT_3 if how else TEXT if mine else TEXT_2, wrap=True, select=True)
            if mine:
                lb.setStyleSheet(f"color: {TEXT_3 if how else TEXT}; background: #1D1E23; border-radius: 10px; padding: 5px 9px;")
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0, 0, 0, 0)
                row.addStretch(1)
                row.addWidget(lb, 4)
                lb = holder
            self.ag_thread.addWidget(lb)
            lb.show()  # (now: the view measures itself right after)
        bar = self.ag_scroll.verticalScrollBar()  # the chat from its newest turn; a Story or Steps from its top
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() if turns else 0))

    def _agent_chat(self, force=False):
        """The chat of the session shown (GET /api/agents/chat: the owner's words and the agent's own from its log, then the owner's words
        still waiting for it), read at most every CHAT_EVERY_S while the view shows; force: now (it opened, Write to)."""
        got = self.state.watched()
        w = got[1] if got else None
        if not w or not w.get("key"):
            return
        key, turns, at = self.agent_talk
        if not force and key == w["key"] and time.monotonic() - at < CHAT_EVERY_S:
            return
        self.agent_talk = (w["key"], turns if key == w["key"] else [], time.monotonic())
        self.bridge.get("/api/agents/chat?key=" + urllib.parse.quote(w["key"], safe=""),
                        lambda d, err, k=w["key"]: self._got_agent_chat(k, d, err))

    def _got_agent_chat(self, key, d, err):
        if err or self.agent_talk[0] != key:  # (a chat Core can't read shows none: never an old one as if new)
            if err and self.agent_talk[0] == key:
                self.agent_talk = (key, [], self.agent_talk[2])
            return self.refresh() if self.opened and self.view == "agent" else None
        turns = [t for t in (d or {}).get("turns") or [] if isinstance(t, dict)]
        if turns != self.agent_talk[1]:
            self.agent_talk = (key, turns, self.agent_talk[2])
            if self.opened and self.view == "agent":
                self.refresh()

    def _place_of(self, w):
        """The open window a session works in (the dock's, agent_places: its IDE, the Claude app), when Monday saw it."""
        return next((pl for pl in self.places if pl.get("session") == w.get("key") and pl.get("hwnd")), None) if w else None

    def _agent_goto(self):
        """Go to: the session's window to the front (the retired notch's Go to, through the dock's own window)."""
        pl = self._place_of(self.state.agent)
        if pl and not self._front(pl):
            self.state.agent_result = ("failed", f"{pl.get('name')} is open", "Windows didn't let Monday bring it to the front: it's in your taskbar.",
                                       time.monotonic(), RESULT_S)
            self.refresh()

    def next_agent(self):
        """Next agent (Dotpals', several at once): the next live session, round again, with its own view and chat."""
        if self.state.next_agent():
            self.story = None
            self._agent_chat(force=True)
            self.refresh()

    def story_show(self):
        """Dotpals' Story: what the agent shown did today, in plain words, from Core (core/watch.py reads its log whole, on this ask)."""
        w = self.state.agent
        if not w or not w.get("key"):
            return
        self.story = {"live": "story", "key": w["key"], "lines": ["Reading today's story…"], "text": ""}
        self.refresh()
        self.bridge.get("/api/agents/story?key=" + urllib.parse.quote(w["key"], safe=""), lambda d, err, k=w["key"]: self._story_got(k, d, err))

    def _story_got(self, key, d, err):
        if (self.story or {}).get("live") != "story" or self.story.get("key") != key:
            return  # closed, or another agent shown, meanwhile
        self.story = {**self.story, "lines": [f"Couldn't read today's story: {err}"], "text": ""} if err else \
            {**self.story, "lines": (d or {}).get("lines") or [], "text": (d or {}).get("text") or ""}
        self.refresh()

    def story_copy(self):
        """Copy today: the whole day's story (every request), on the clipboard."""
        self._copy((self.story or {}).get("text") or "")
        self.story = None
        self.state.agent_result = ("done", "Copied", "Today's story is on the clipboard.", time.monotonic(), RESULT_S)
        self.refresh()

    def _copy_path(self, path):
        """The code card's Copy: the edited file's whole path, said where it went."""
        self._copy(path)
        self.state.agent_result = ("done", "Copied", f"{path} is on the clipboard.", time.monotonic(), RESULT_S)
        self.refresh()

    def story_close(self):
        self.story = None
        self.refresh()

    def steps_show(self, view="key"):
        """Dotpals' step timeline through one of its filters (watch.steps_views, sent with the watch): live while it shows."""
        w = self.state.agent
        if w and w.get("key"):
            self.story = {"live": "steps", "key": w["key"], "view": view}
            self.refresh()

    def say(self):
        """Write to: the owner's words for the agent shown (POST /api/agents/say, Monday's window only); its own hooks hand them over
        before its next step, or wake it with them once its turn is over. When it can't take them, they're copied and its own chat opens
        (its window comes forward), to paste in; either way its chat here shows them."""
        text, w = self.say_box.toPlainText().strip(), self.state.agent
        if not text or not w or not w.get("key"):
            return
        self.say_box.clear()
        self.bridge.post("/api/agents/say", {"key": w["key"], "text": text}, lambda d, err, w=w, text=text: self._said_to(w, text, d, err))

    def _said_to(self, w, text, d, err):
        queued, where = bool((d or {}).get("queued")), "its window"
        if not queued:
            self._copy(text)
            link, pl = self._links.get(w.get("key")) or chat_link(w), self._place_of(w)
            if link and self._open_url(link):  # that very chat, to paste in (the Claude app's, the Codex app's: 2026-10-22)
                self._links[w.get("key")] = link
                where = "the Claude app" if link.startswith("claude:") else "the Codex app"
            elif pl and self._front(pl):
                where = pl.get("name") or where
        queued, words = say_words(w, d, err, where)
        who = w.get("agent") or "the agent"
        self.state.agent_result = ("done", f"Queued for {who}" if queued else f"Copied for {who}", words, time.monotonic(), RESULT_S)
        self._agent_chat(force=True)  # its chat shows them: waiting for it, or copied (the owner, 2026-10-22: "it isnt shwoing")
        self.refresh()

    def _copy(self, text):
        QGuiApplication.clipboard().setText(text)

    def rewind_open(self, rw, show=True):
        """Rewind… for the session's last request: a window of its own (RewindDialog), Core's preview first."""
        if self.rewinder is not None:
            self.rewinder.close()
        self.rewinder = RewindDialog(self, rw)
        if show:
            self.rewinder.show()
            self.rewinder.raise_()
            self.rewinder.activateWindow()
        self.rewinder.load()
        return self.rewinder

    # -- Chat: Monday's conversation, its state, the prompt --------------------------------------------------------------------------------
    def _load_chat(self):
        self.bridge.get(f"/api/messages?limit={CHAT_PAGE}", self._got_messages)
        self.bridge.get("/api/models", self._got_models)
        self.bridge.get("/api/voice", self._got_voice_pick)

    # whose voice speaks the replies (2026-10-20, "add the TTS and a custom one too": every speaker Core knows, the owner's own among them)
    def _got_voice_pick(self, d, err):
        from .native import voice_choices
        self.voice_state = d or {}
        cur = next(((short, st) for _pid, short, st, chosen in voice_choices(d) if chosen), None)
        self.voice_chip.setText("Voice: N/A  ▾" if err or not cur else f"{cur[0]}  ▾")
        self.voice_chip.dot_color = K.GREEN_TEXT if cur and cur[1] in ("available", "ready") else RED
        self.voice_chip.updateGeometry()
        self.voice_chip.update()
        self.voice_chip.setAccessibleName(f"The voice: {cur[0] if cur else 'N/A'}. Click to pick another")

    def _voice_menu(self):
        from .native import voice_choices
        m = QMenu(self)
        for pid, short, status, chosen in voice_choices(getattr(self, "voice_state", {})):
            act = m.addAction(f"{short}  ·  {status}")
            act.setCheckable(True)
            act.setChecked(chosen)
            act.triggered.connect(lambda _=False, i=pid: self.bridge.post("/api/voice/settings", {"tts_provider": i}, lambda _d, err: (
                self._said(f"Couldn't switch the voice: {err}") if err else None, self.bridge.get("/api/voice", self._got_voice_pick))))
        if m.isEmpty():
            m.addAction("The voices couldn't be read").setEnabled(False)
        m.addSeparator()
        m.addAction("Voice settings: voices, speed, your own recording…").triggered.connect(lambda: self.open_window("voice"))
        self.menu_open = True
        m.exec(QCursor.pos())
        self.menu_open = False

    def _got_messages(self, d, err):
        """The newest page, each time Chat opens: what isn't here yet joins the thread (the older pages the owner scrolled back to stay);
        a page with nothing in common with what is here (Core away a long while) starts the thread again."""
        if not err:
            got = (d or {}).get("messages") or []
            if not self._messages or (got and got[0].get("id", 0) > self._messages[-1]["id"]):
                self._messages, self._older = [], bool((d or {}).get("has_more"))
            elif got and got[0].get("id", 0) <= self._messages[0]["id"]:  # the page reaches the oldest turn held (a thread seeded by the
                self._older = bool((d or {}).get("has_more"))              # stream before any page, 2026-10-27): Core says what is before it
            self._merge(got)
        self._fill_thread()

    def _got_older(self, d, err):
        self._loading_older = False
        if err:  # (scrolled up again, it is asked again)
            return
        self._older = bool((d or {}).get("has_more"))
        self._merge((d or {}).get("messages") or [])
        self._fill_thread()

    def _near_top(self, v):
        """Scrolled up to the oldest words loaded: the page before them (the owner, 2026-10-23: the whole conversation, not its last turns)."""
        if v <= OLDER_AT_PX and self._older and not self._loading_older and self._messages and self.opened and self.view == "chat":
            self._loading_older = True
            self.bridge.get(f"/api/messages?before={self._messages[0]['id']}&limit={CHAT_PAGE}", self._got_older)

    def _merge(self, msgs):
        """Core's messages into the thread: once each by id (a page read again or an event replayed after a reconnect adds nothing),
        oldest first; never a room's conversation or the preflight's tests."""
        mine = {m["id"]: m for m in self._messages}
        for m in msgs:
            if m.get("id") is not None and not (m.get("meta") or {}).get("chatroom") and m.get("source") != "preflight":
                mine[m["id"]] = m
        self._messages = sorted(mine.values(), key=lambda m: m["id"])
        self.talk = chat_turns(self._messages, len(self._messages))

    def _got_models(self, d, err):
        self.models = d or {}
        sel = next((m for m in (d or {}).get("models") or [] if m.get("selected")), None)
        eff = (d or {}).get("effective")
        name = "N/A" if err or not sel else (f"auto · {eff}" if sel.get("id") == "auto" and eff else sel.get("name") or sel.get("id"))
        self.model_chip.setText(f"{name}  ▾")
        # its provider's own dot (the recording's "● claude-opus-5" in Anthropic's orange); red when Core says it can't answer
        self.model_chip.dot_color = provider_of(sel)[1] if sel and sel.get("available") else RED
        self.model_chip.updateGeometry()
        self.model_chip.update()
        self.model_chip.setAccessibleName(f"The chat model: {name}. Click to pick another")
        if self.model_panel.isVisible():
            self.model_panel.fill(self.models if not err else None)

    def _model_menu(self):
        """The model pill: its menu in the thread's place (again: the thread back). Core's list asked again as it opens."""
        if self.model_panel.isVisible():
            return self._model_panel(False)
        self.model_panel.loading = True
        self.model_panel.fill(getattr(self, "models", None) or None)
        self._model_panel(True)
        self.bridge.get("/api/models", self._got_models)

    def _model_panel(self, on):
        self.model_panel.setVisible(on)
        self.thread_scroll.setVisible(not on)
        self._chat_drawn = None  # (Chat's status words: put by while the menu shows, back after: _show_chat_state)
        self._show_chat_state()
        if self.opened and self.view == "chat":
            QTimer.singleShot(0, self._go)  # (the card to the menu's height, and back to the thread's, once laid out)
        self.model_chip.tone = "lit" if on else "dark"
        self.model_chip.update()

    def pick_model(self, model_id):
        """A model picked in the menu: Core switches (it checks the model answers first), the pill says what Core says it is now."""
        self._model_panel(False)
        self.bridge.post("/api/models/select", {"id": model_id}, lambda _d, err: (
            self._said(f"Couldn't switch the chat model: {err}") if err else None, self.bridge.get("/api/models", self._got_models)))

    def _thread_turns(self):
        """Chat's thread as it should read now: every loaded turn; under them what the owner just asked (until Core's copy of it comes),
        the replies being written (their words so far), else Monday's "…" while it works on the answer. Each with its key (a message's
        id). -> (the stored turns, the tail)."""
        turns = [{**t, "key": t["id"] if t.get("id") is not None else ("turn", i)} for i, t in enumerate(self.talk)]
        asked, tail = self.state.asked_text, []  # (what was asked from here until its answer, or Core's refusal, clears it: not the closed
        # bar's state, which a running task outranks and whose ASKED_S ends before a cold local model answers, 2026-10-27)
        if asked and (not turns or turns[-1]["who"] != "you"):
            tail.append({"who": "you", "text": self.state.asked_text[0], "key": "asked"})
        live = [{"who": "Monday", "text": w, "key": ("live", sid)} for sid, w in self._live.items() if w.strip()]
        tail += live
        if asked and not live:
            tail.append({"who": "Monday", "text": "…", "key": "waiting"})
        if not turns and not tail:
            turns = [{"who": "hint", "text": "Ask Monday anything: it answers here and in the window's chat.", "key": "hint"}]
        return turns, tail

    def _bubble(self, t, fold=False):
        """One turn's words: the owner's in a bubble at the right, Monday's as plain words. fold (a stored turn): one longer than LONG_TURN
        shows its first LONG_SHOWN characters and "Show all", the rest in place when clicked (laid out whole, the five 16,000-character
        pastes in the owner's last page held the notch's first opening ~0.75 s, 2026-10-23). -> (its widget, its label)."""
        if t["who"] == "hint":
            lb = label(t["text"], 12, 400, TEXT_3, wrap=True)
            return lb, lb
        mine = t["who"] == "you"
        words = t["text"][:TURN_CHARS] if mine else _head(_plain(t["text"]), TURN_CHARS)
        shown = _head(words, LONG_SHOWN) if fold and len(words) > LONG_TURN else words
        lb = label(breakable(shown), 12.5, 400, TEXT if mine else TEXT_2, wrap=True, select=True)  # (a long path wraps too)
        if mine:
            lb.setStyleSheet(f"color: {TEXT}; background: #1D1E23; border-radius: 10px; padding: 5px 9px;")
        if not mine and shown is words:
            return lb, lb
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        side = Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft
        if mine:  # (the owner's words at the right, as wide as they need up to four fifths of the thread)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addStretch(1)
            row.addWidget(lb, 4)
            col.addLayout(row)
        else:
            col.addWidget(lb)
        if shown is not words:
            more = Button(f"Show all · {len(words) - len(shown):,} more characters", "ghost", height=22, name="Show the whole of this turn")
            more.kind = "link"
            more.clicked.connect(lambda: (lb.setText(breakable(words)), more.hide()))
            col.addWidget(more, 0, side)
        return box, lb

    def _redraw(self, lay, have, want, fold=False):
        """One box of Chat's thread drawn again where it changed: the turns that went or say something else come out, new ones go in at
        their place, a reply being written keeps its label and only its words change. fold: very long turns show "Show all" (_bubble).
        -> (what it shows now, the turns kept by key)."""
        keys = {t["key"]: t for t in want}
        kept = {}
        for b in have:
            t = keys.get(b[0])
            if t is not None and (t["text"] == b[1] or t["who"] == "Monday"):
                kept[b[0]] = b
            else:
                lay.removeWidget(b[2])
                b[2].deleteLater()
        if [t["key"] for t in want if t["key"] in kept] != list(kept):  # (never the case as Core sends them: if ever, all drawn afresh)
            for b in kept.values():
                lay.removeWidget(b[2])
                b[2].deleteLater()
            kept = {}
        drawn = []
        for i, t in enumerate(want):
            b = kept.get(t["key"])
            if b is None:
                w, lb = self._bubble(t, fold)
                lay.insertWidget(i, w)
                b = (t["key"], t["text"], w, lb)
            elif b[1] != t["text"]:
                b[3].setText(breakable(_head(_plain(t["text"]), TURN_CHARS)))
                b = (t["key"], t["text"], b[2], b[3])
            drawn.append(b)
        return drawn, kept

    def _fill_thread(self):
        """Chat's thread drawn again only where it changed: a new turn added, older ones put above (where the owner is reading stays in
        place), a reply's words so far, nothing for an event that changed nothing in it (2026-10-23: every Core event rebuilt the whole
        thread, 12 times a second while a reply streamed, and the notch lagged ~220 ms behind the owner and jumped to the newest words
        while they read older ones). ponytail: every loaded turn is a label; a virtual list if the owner scrolls back thousands."""
        hist, tail = self._thread_turns()
        if [(t["key"], t["text"]) for t in hist] == [b[:2] for b in self._bubbles] and \
                [(t["key"], t["text"]) for t in tail] == [b[:2] for b in self._tail]:
            return
        bar = self.thread_scroll.verticalScrollBar()
        at_end, from_end = bar.value() >= bar.maximum() - 8, bar.maximum() - bar.value()
        first, before = (self._bubbles[0][0] if self._bubbles else None), len(self._bubbles) + len(self._tail)
        self._bubbles, kept = self._redraw(self.history.layout(), self._bubbles, hist, fold=True)
        self._tail, kept_tail = self._redraw(self.tail.layout(), self._tail, tail)
        self.tail.setVisible(bool(self._tail))  # (hidden, it takes no room and no spacing)
        drawn = self._bubbles + self._tail
        older = first is not None and self._bubbles[0][0] != first and first in kept  # turns put above the ones on show: the page before them
        mine = any(t["who"] == "you" and t["key"] not in kept and t["key"] not in kept_tail for t in (hist + tail)[-2:])  # the owner's own
        self._follow_thread = (at_end or mine or not before) and not older  # new words: the newest in sight
        if older:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - from_end))  # (the words read stay where they were)
        elif self._follow_thread:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
        # (sized on the loop's next turn, once Qt has laid the new words out: measured now, the notch kept its old height). At its tallest
        # with the thread overflowing, more words can't make it taller: no measuring the whole thread again for them
        full = self.height() >= TOP_IN + 28 + TAB_GAP + CHAT_MAX + PAD - 1 and bar.maximum() > 0
        if not (full and len(drawn) >= before):
            self._chat_settles = CHAT_SETTLES
            QTimer.singleShot(0, lambda: self._go() if self.opened and self.view == "chat" else None)

    def _show_chat_state(self):
        """What Monday is doing over the thread, and its buttons: drawn again only when they say something else (refresh() comes with
        every event Core sends: a style sheet set and the buttons made again each time cost the notch a layout pass for nothing)."""
        st, title, detail = self.shown
        card_states = ("waiting", "working", "failed", "done", "verified", "unverified", "cancelled", "answer", "file", "dizzy")
        p = list(self.state.perms.values())[-1] if st == "waiting" and self.state.perms else None
        if p:  # Monday's own approval: Deny / Always (when offered) / Allow
            title_ = perm_words(p)
            line = (f"{(p.get('risk') or 'N/A').capitalize()} risk. Nothing runs until you answer." +
                    (f"\n{_head(p.get('detail') or '', 300)}" if p.get("detail") else ""))
        else:
            title_ = title if st in card_states or st in THREAD_STATES else ""
            line = _head(_plain(detail), ANSWER_MAX) if st in card_states and st != "answer" else ""
        if self.model_panel.isVisible():  # the model menu has the card: no status over it (the review)
            title_, line, p = "", "", None
        drawn = (st, title_, line, p and (p["id"], bool(p.get("always"))), st == "working" and bool(self.state.tasks))
        if drawn != self._chat_drawn:
            self._chat_drawn = drawn
            while self.buttons.count():
                w = self.buttons.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self.state_title.setText(title_)
            self.state_title.setVisible(bool(title_))
            self.state_title.setStyleSheet(f"color: {K.RED_TEXT if st == 'failed' else TEXT_2 if st in ('idle', 'listening', 'asleep') else TEXT}; "
                                           "background: transparent;")
            self.state_line.setText(line)
            self.state_line.setVisible(bool(line))
            if p:
                for words_, decision, kind in (("Deny", "deny", "secondary"), ("Always allow", "always", "secondary"), ("Allow", "approve", "light")):
                    if decision == "always" and not p.get("always"):
                        continue
                    b = Button(words_, kind, height=26)
                    b.clicked.connect(lambda _=False, pid=p["id"], dcs=decision: self.decide(pid, dcs))
                    self.buttons.addWidget(b)
            elif st == "working" and self.state.tasks:
                b = Button("Stop", "danger", icon_name="stop", height=26, name="Stop Monday's work")
                b.clicked.connect(self.stop_all)
                self.buttons.addWidget(b)
            self.buttons.addStretch(1)
        self._fill_thread()

    def chip_ask(self, key):
        """One of the island's Chat chips: Monday asked about the owner's latest copy (the clipboard's newest that wasn't private), through
        the chat as if typed; nothing copied yet: said, nothing sent."""
        ask = dict((k, a) for k, _w, a in L.CHIPS).get(key)
        text = (self.clips[0]["text"] if self.clips else "").strip()
        if not ask or not text:
            return self.said_island("Copy some text first: these work on your latest copy.")
        self.prompt.setPlainText(f"{ask}\n\n{text[:4000]}")
        self.send()

    def send(self):
        text = self.prompt.toPlainText().strip()
        if not text:
            return
        atts = list(self.attach)
        self.prompt.clear()
        self.state.asked_text = (text, time.monotonic())

        def done(m, err):
            if err:  # Core refused: say why and give the words back
                self.state.asked_text = None
                self.prompt.setPlainText(text)
                if err.startswith("Monday Core") and getattr(self, "core_phase", None) in ("starting", "restarting"):
                    err = f"Monday Core is {self.core_phase}: send it again in a few seconds (your words are back in the box)."
                self.state.result = ("failed", "Not sent", err, time.monotonic(), 12.0)
            else:
                if m.get("id") is not None:
                    self.state.asked.add(m["id"])
                self.attach.clear()
            self.refresh()
        self.win.command(text, atts, done=done)  # the window's one pipeline: its answer may be said out loud
        self.refresh()

    def decide(self, pid, decision):
        tid = (self.state.perms.get(pid) or {}).get("task_id")
        if decision in ("approve", "always") and tid:  # its task's end is Home's next card ("finished", the 2026-10-26 recording)
            self._flow.add(tid)
        self.bridge.post(f"/api/permissions/{pid}/{decision}", None, lambda _d, err: self._said(f"Your answer wasn't recorded: {err}"
                                                                                               if err else None))

    def stop_all(self):
        self.marks_fade()  # (whatever Monday was showing on the screen goes with what it was doing)
        tids = list(self.state.tasks)
        results = []

        def done(_d, err, tid):
            results.append(err)
            if len(results) == len(tids):  # (say only what Core confirmed: 2026-10-19)
                ok = sum(e is None for e in results)
                self.state.result = ("cancelled" if ok else "failed", f"Stopped {ok} of {len(tids)}" if ok != len(tids) else "Stopped",
                                     "; ".join(e for e in results if e) or "", time.monotonic(), 8.0)
                self.refresh()
        for tid in tids:
            self.bridge.post(f"/api/tasks/{tid}/stop", None, lambda d, err, tid=tid: done(d, err, tid))

    def _escape(self, box=None):
        box = box or self.prompt
        if box.toPlainText():
            box.clear()
        else:
            self.close_notch()

    # -- the island: its widgets, kept; the focus timer, the music, the clipboard, Power ----------------------------------------------------
    def tiles_changed(self):
        """The grid's tiles were made anew (a layout, a size): the pets the frames move, the AI tile's pills and ↗, the clipboard and the
        music watched only while their widgets are on Home."""
        ai = self.grid.tiles.get("ai")
        big = ai is not None and ai.size == "big"
        self.dock, self.dock_grid = (ai, ai.pills) if big else (self._no_dock, self._no_dock.layout())
        self.to_window = ai.open_btn if big else None
        self._fill_dock()
        live = tuple(self.grid.tiles.values())  # (a replaced tile goes later, by deleteLater: its pet out of the frames' list now, or each
        self._pets = [pv for pv in self.findChildren(PetView)  # frame after it went raised "already deleted": desktop.log, 2026-10-26)
                      if not self.grid.isAncestorOf(pv) or any(t.isAncestorOf(pv) for t in live)]
        self._watch_media()

    def edit_changed(self, on):
        """Arranging the widgets (the recording's edit mode): Done by the tabs and what can be done (+ stays the file drop: the owner,
        2026-10-25); out of it, all as before."""
        self.done_pill.setVisible(on)
        self.edit_hint.setVisible(on)
        self.island_tabs.done.setVisible(on and self.mode == "island")
        if on:
            self._close_t.stop()
        self.update()

    def arrange(self):
        """The notch's menu: Home's widgets, wiggling to be arranged (as holding one does)."""
        self.open("home")
        if not self.state.asks:
            self.grid.edit(True)

    def open_menu(self, at=None):
        """The widget menu (in Settings, under its own card), for an empty cell (its lime +) or anywhere there is room."""
        self._menu_at = at
        self.show_view("settings")

    def keep_island(self):
        """The island as it is now, into data/desktop.json (the window's store): it comes back so after a restart. The floating island's
        grid is its own (island.ui "island"); classic's stays under ISLAND_KEY with the note and the focus timer."""
        if self.mode == "island":
            self.ui["island"]["layout"] = [dict(it) for it in self.grid.layout_]
            return self.win.keep(I.UI_KEY, self.ui)
        if self.mode == "classic":
            self.island["layout"] = [dict(it) for it in self.grid.layout_]
        self.win.keep(ISLAND_KEY, dict(self.island))

    _note_live = None  # the note as it is being written (its tile shows it at once; it is kept NOTE_SAVE_MS after the last key)

    def note_now(self):
        """The note's words now: as they are being written, else as kept."""
        return self._note_live if self._note_live is not None else (self.island.get("note") or "")

    def note_typed(self, text):
        self._note_live = text
        if "note" in self.grid.tiles:
            self.grid.tiles["note"].fill()

    def keep_note(self, text):
        self._note_live = None
        if (self.island.get("note") or "") != text:
            self.island["note"] = text
            self.win.keep(ISLAND_KEY, dict(self.island))
        if "note" in self.grid.tiles:
            self.grid.tiles["note"].fill()

    def _note_keys(self):
        """The Note opened to write in: the keyboard comes to it, the caret after the last word (the notch never takes the keyboard by
        itself, so typing went to the app in front: the owner's note of 2026-10-25 never arrived); leaving it gives the keyboard back."""
        if self.opened and self.view == "note":
            self.take_keys(self.note_view.box)
            self.note_view.box.moveCursor(QTextCursor.MoveOperation.End)

    def said_island(self, words):
        """Something the island couldn't do, said where the owner looks (the state's line: the closed bar and Chat say it too)."""
        self.state.result = ("failed", "Not done", words, time.monotonic(), 8.0)
        self.refresh()

    def _island_tick(self):
        """Once a second while open: the clock, the focus timer and the music's progress move on (only the tiles that show them)."""
        if self.view == "home" and self.mode == "limits":
            self.limits_view.fill()  # (the readings' ages and the resets' "in 1 hr 20 min" move on)
        elif self.view == "home" and not self.grid.editing:
            self.grid.tick()
        elif self.view == "settings":
            self.menu.tick()
        elif self.view == "music":
            self.music_view.tick()
        elif self.view == "focus":
            self.focus_view.fill()
        if self.mode == "island":
            self.head.update()  # (its clock)

    # the Pomodoro timer (island.focus_*): kept in data/desktop.json, rung at each end even with the notch closed
    def focus_tap(self):
        """A tap: a pomodoro starts, pauses or goes on; a break is skipped (the next pomodoro ready)."""
        st = I.focus_now(self.island.get("focus"))
        f = self.island.get("focus")
        if st["phase"] == "ended":
            return self._focus_due()  # (its end not taken yet: counted, and its break, as at the end itself)
        if st["kind"] != "focus":
            f = I.focus_reset(f)
        elif st["phase"] == "running":
            f = I.focus_pause(f)
        else:
            f = I.focus_start(f)
        self._focus_keep(f)

    def focus_reset(self):
        self._focus_keep(I.focus_reset(self.island.get("focus")))

    def focus_length(self, secs):
        """The island's Focus page: a pomodoro's length (5 / 15 / 25 / 50 min)."""
        self._focus_keep(I.focus_length(self.island.get("focus"), secs))

    def focus_more(self):
        """The island's Focus page: five more minutes on the timer running or paused."""
        self._focus_keep(I.focus_more(self.island.get("focus")))

    def _focus_keep(self, f):
        self.island["focus"] = f
        self.win.keep(ISLAND_KEY, dict(self.island))
        if self.view == "focus":
            self.focus_view.fill()
        self._focus_arm()
        for t in (self.grid.tiles.get("focus"), self.menu.preview if self.menu.preview is not None and self.menu.preview.key == "focus" else None):
            if t is not None:
                t.fill()

    def _focus_arm(self):
        """The end of a running session: its timer set (a session that ended while Monday was off is counted now, without its tone)."""
        self._focus_t.stop()
        st = I.focus_now(self.island.get("focus"))
        if st["phase"] == "running":
            self._focus_t.start(int(min(st["left"], 3600) * 1000) + 50)  # (an hour at most: it re-arms itself then)
        elif st["phase"] == "ended":
            self._focus_due()

    def _focus_due(self):
        st = I.focus_now(self.island.get("focus"))
        if st["phase"] == "running":
            return self._focus_arm()
        if st["phase"] != "ended":
            return
        f, ring, what = I.focus_finish(self.island.get("focus"))
        self.island["focus"] = f
        self.win.keep(ISLAND_KEY, dict(self.island))
        now = I.focus_now(f)
        if ring and what == "focus":  # (a pomodoro done: the tone, and the state's line on the closed bar too; its break already running)
            self._sound("done")
            brk = "a 15-minute break" if now["kind"] == "long" else "a 5-minute break"
            self.state.result = ("done", "Pomodoro done", f"{now['today']} today · {brk} now", time.monotonic(), 12.0)
        elif ring and what == "break":
            self._sound("prompt")
            self.state.result = ("done", "Break over", f"Pomodoro {now['set'] + 1} of {I.SET_OF} when you're ready", time.monotonic(), 12.0)
        self._focus_arm()  # (the break's own end)
        if "focus" in self.grid.tiles:
            self.grid.tiles["focus"].fill()
        self.refresh()

    # the music (Windows' media sessions, read and controlled here: the owner's own click, like a media key)
    def _watch_media(self):
        on = (self.opened and ((self.view == "home" and "music" in self.grid.tiles) or (self.view == "settings" and self.menu.pick == "music"))
              or (self.mode == "island" and self.isVisible()))  # (the floating island's music bubble, closed too)
        self.media.watch(on)

    def _media_changed(self):
        for t in (self.grid.tiles.get("music"), self.menu.preview if self.menu.preview is not None and self.menu.preview.key == "music" else None):
            if t is not None:
                t.fill()
        if self.view == "music":
            self.music_view.fill()
        if self.mode == "island":
            self.head.update()  # (its music bars)
            self.update()       # (the closed island's music bubble)

    def media_control(self, op):
        self.media.control(op)

    # the clipboard (the owner, 2026-10-26: "all the things that ever copied until shutdown"): watched from the start, every copy kept
    # beside Monday's state with the PC's boot time (island.clips_load); a copy a password manager marks private is never read
    def _clips_load(self):
        """The copies since the PC started, and their file rewritten so a past boot's are gone from it at once. The desktop app's own
        window only: a check's notch never reads or writes the owner's file."""
        if not self.win.persist:
            return []
        clips = I.clips_load(self._clips_file(), boot_time())
        I.clips_save(self._clips_file(), boot_time(), clips)
        return clips

    def _clips_keep(self):
        if self.win.persist:
            I.clips_save(self._clips_file(), boot_time(), self.clips)

    @staticmethod
    def _clips_file():
        from . import desktop
        return desktop.STATE.with_name("clips.json")

    def _watch_clip(self, on):
        if on == self._clip_on:
            return
        self._clip_on = on
        cb = QGuiApplication.clipboard()
        if on:
            cb.dataChanged.connect(self._on_clip)
        else:
            try:
                cb.dataChanged.disconnect(self._on_clip)
            except (RuntimeError, TypeError):
                pass

    def _on_clip(self, mime=None):
        """A copy anywhere on this PC: kept until the PC shuts down unless it is private (island.clip_private) or not words."""
        if self._copying_back:
            return
        mime = mime if mime is not None else QGuiApplication.clipboard().mimeData()
        if mime is None:
            return
        formats = {f: (bytes(mime.data(f)) if I.HISTORY_FLAG.lower() in f.lower() else None) for f in mime.formats()}
        if I.clip_private(formats) or not mime.hasText():
            return
        self.clips = I.clip_add(self.clips, mime.text())
        self._clips_keep()
        if "clipboard" in self.grid.tiles:
            self.grid.tiles["clipboard"].fill()
        if self.opened and self.view == "clip":
            self.clip_view.load()

    _copying_back = False

    def copy_back(self, text):
        """A copy clicked in the Clipboard view: on the clipboard again (moved to the top of the list), said for a moment."""
        self._copying_back = True
        try:
            self._copy(text)
        finally:
            self._copying_back = False
        self.clips = I.clip_add(self.clips, text)
        self._clips_keep()
        self.clip_view.load()
        self.clip_view.said.setText("Copied")
        QTimer.singleShot(1800, lambda: self.clip_view.said.setText(""))

    def clips_forget(self):
        self.clips = []
        self._clips_keep()
        self.clip_view.load()
        if "clipboard" in self.grid.tiles:
            self.grid.tiles["clipboard"].fill()
        self._go()

    # Power (the recording's top right): the tray's own Quit Monday, after asking
    def _power(self):
        if self.quit is None:
            return
        self.menu_open = True
        try:
            yes = K.confirm(self, "Quit Monday?", "Monday Core stops, and with it any task it is running and its Chrome. Start Monday again "
                            "from the Start menu.", "Quit Monday", danger=True)
        finally:
            self.menu_open = False
        if yes:
            self.quit()

    def set_quit(self, quit_):
        """The tray's Quit Monday: the island's power button calls it, after asking; none, no button."""
        self.quit = quit_
        self.power.setVisible(quit_ is not None)

    @staticmethod
    def _tray_quit():
        """The tray's own Quit Monday (core/desktop.py's menu, the same action as its --quit), found in this app when the notch opens (the
        tray is made after the notch); None where there is no tray (a check, a picture of the views)."""
        from PySide6.QtWidgets import QSystemTrayIcon
        app = QApplication.instance()
        for o in app.children() if app is not None else ():
            if isinstance(o, QSystemTrayIcon) and o.contextMenu() is not None:
                act = next((a for a in o.contextMenu().actions() if a.text() == "Quit Monday"), None)
                if act is not None:
                    return act.trigger
        return None

    # -- Drop: a file, checked here, copied to Core with its real progress, then what its kind can become -------------------------------
    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.open("drop")

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            e.acceptProposedAction()
            self.take_file(files[0])

    def _choose_file(self):
        if self.uploading:
            return
        self.menu_open = True
        path, _ = QFileDialog.getOpenFileName(self, "A file for Monday")
        self.menu_open = False
        if path:
            self.take_file(path)

    def take_file(self, path, circled=None):
        """A file for Monday: checked here, copied to Core with real progress, then what its kind allows. circled: a part of the screen the
        owner circled (circle), asked about rather than opened or emailed."""
        if self.uploading:
            self.state.result = ("failed", "One at a time", "A file is still copying; drop the next when it's done.", time.monotonic(), 8.0)
            return self.refresh()
        f = examine(path)
        if circled and f["ok"]:
            f = {**f, "actions": CIRCLE_ACTIONS, "circled": circled}
        if not f["ok"]:
            self.state.file = {"phase": "failed", "name": f["name"], "why": f["why"]}
            self._sound("error")
            return self.open("drop")
        self.state.file = {**f, "phase": "copying", "pct": 0}
        self.uploading = True
        self.art.eat()
        self.open("drop")
        threading.Thread(target=self._upload, args=(dict(self.state.file),), daemon=True).start()

    def _upload(self, f):
        sent = [0]

        def chunks():
            with open(f["path"], "rb") as fh:
                while True:
                    b = fh.read(CHUNK)
                    if not b:
                        return
                    sent[0] += len(b)
                    self.progress.emit(int(sent[0] * 100 / max(1, f["size"])))
                    yield b
        try:
            r = self.bridge.core.http.post("/api/upload", content=chunks(), headers={"x-filename": urllib.parse.quote(f["name"])},
                                           timeout=UPLOAD_TIMEOUT_S)
            data = r.json() if r.content else {}
            err = None if r.status_code < 400 else (data.get("detail") or data.get("error") or f"HTTP {r.status_code}")
        except OSError as e:
            data, err = None, f"Monday couldn't read the file ({e.strerror or e})."
        except Exception as e:  # httpx errors: Core away
            data, err = None, "Monday Core is unreachable." if "connect" in repr(e).lower() else f"Failed: {e!r}"[:200]
        try:
            self.uploaded.emit(f, (data, err))
        except RuntimeError:  # the notch went during the upload (Monday quitting)
            pass

    def _got_progress(self, pct):
        if self.state.file and self.state.file.get("phase") == "copying":
            self.state.file["pct"] = pct
            self.refresh()

    def _got_upload(self, f, answer):
        data, err = answer
        self.uploading = False
        if err or not (data or {}).get("id"):
            self.state.file = {"phase": "failed", "name": f["name"], "why": err or "Core didn't say it kept it."}
            self._sound("error")
        else:
            self.state.file = {**f, "phase": "ready", "id": data["id"]}
            self._sound("saved")
        self.refresh()

    def _show_file(self):
        for row in (self.file_btns, self.file_btns2):
            while row.count():
                w = row.takeAt(0).widget()
                if w:
                    w.deleteLater()
        f = self.state.file
        for i in range(self.chips.count()):
            w = self.chips.itemAt(i).widget()
            if w:
                w.setVisible(not f)
        if not f:
            self.drop_title.set("Drop your files here")
            self.drop_line.setText("")
            return
        if f["phase"] == "failed":
            self.drop_title.set(f"Couldn't take {f.get('name') or 'it'}")
            self.drop_line.setText(f.get("why") or "")
            ok = Button("OK", "secondary", height=24, size=11.5)
            ok.clicked.connect(self._file_done)
            self.file_btns.addWidget(ok)
        elif f["phase"] == "copying":
            self.drop_title.set(f["name"])
            self.drop_line.setText(f"Copying to Monday… {f.get('pct', 0)}%")
        else:
            self.drop_title.set(f["name"])
            self.drop_line.setText(f"{KIND_WORDS.get(f.get('kind'), 'file').capitalize()}, {size_words(f.get('size') or 0)}"
                                   + (" · a program: only read, never run" if f.get("runs") else ""))
            acts = list(f.get("actions") or ())
            for i, a in enumerate(acts):  # (six actions don't fit one row of the open notch: the rest go on a second, 2026-10-20)
                b = Button(ACTIONS[a][0], "light" if i == 0 else "secondary", height=24, size=11.5)
                b.clicked.connect(lambda _=False, a=a: self.file_action(a))
                (self.file_btns if i < FILE_ROW else self.file_btns2).addWidget(b)
            x = IconButton("x", "Put the file away", size=24)
            x.clicked.connect(self._file_done)
            (self.file_btns2 if len(acts) > FILE_ROW else self.file_btns).addWidget(x)
        for row in (self.file_btns, self.file_btns2):
            row.addStretch(1)

    def _file_done(self):
        self.state.file = None
        self.refresh()

    def file_action(self, a):
        f = self.state.file or {}
        if a == "open":
            if f.get("runs"):
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(f["path"]))
        elif a == "chat":
            self.attach.append(f["id"])
            self.show_view("chat")
            self.take_keys()
            self.prompt.setPlaceholderText(f"Ask about {f['name']}...")
        elif a == "ask":
            self.attach.append(f["id"])
            self.show_view("chat")
            self.take_keys()
            self.prompt.setPlaceholderText(f"Your question about {f['name']}...")
        elif a == "email":
            self.bridge.post("/api/mail/draft", {"file_id": f["id"]}, lambda d, err, f=f: self._mail(f, d, err), timeout=60)
            return
        else:
            words = ACTIONS[a][1].format(name=f.get("name"))
            self.state.asked_text = (words, time.monotonic())

            def sent(m, err):
                if err:
                    self._said(f"Not sent: {err}")
                elif m.get("id") is not None:
                    self.state.asked.add(m["id"])
                self.refresh()
            self.win.command(words, [f["id"]], done=sent)
            self.show_view("chat")
        self.state.file = None
        self.refresh()

    def _mail(self, f, d, err):
        from .notch import mail_draft_parse
        if err:
            return self._said(f"No draft: {err}")
        subject, body = mail_draft_parse((d or {}).get("text"), f["name"])
        url = "mailto:?subject=" + urllib.parse.quote(subject) + "&body=" + urllib.parse.quote(body)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(f["path"])])
        QGuiApplication.clipboard().setMimeData(mime)  # the file, ready for Ctrl+V in the mail app (a link can't carry it)
        QDesktopServices.openUrl(QUrl(url))
        self.state.result = ("done", "In your mail app", "The draft is open there; the file is copied: paste it with Ctrl+V. You send it.",
                             time.monotonic(), 20.0)
        self.state.file = None
        self.refresh()

    # -- prefs and the window ----------------------------------------------------------------------------------------------------------
    def set_pref(self, k, v):
        p = prefs({**self.p, k: v})
        if k == "visible" and not v and not p.get("desk"):
            p["visible"] = True  # one of the notch or the desk pet always stays (the owner, 2026-10-16)
        if k == "desk" and not v and not p.get("visible"):
            p["visible"] = True  # the pet put away while the notch is off: the notch comes back
        self.win.keep("pet", p)
        self.set_prefs(p)

    def set_prefs(self, p):
        self.p = prefs(p)
        self.art.set(self.p)
        if not self.p["visible"]:
            self.hide()
        elif not self.isVisible():
            self.show()
        self._desk_show()
        self.autoclose.set_value(str(self.p["autoclose"]))
        self.vol.blockSignals(True)
        self.vol.setValue(int(round(self.p["volume"] * 100)))
        self.vol.blockSignals(False)
        self._fill_dock()  # (Active pills chosen in the window show at once)
        self.refresh()

    def open_window(self, page):
        self.win.show_page(page)
        self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()

    # -- the three looks (the owner's brief of 2026-10-29; docs/adr/0011): one notch, one Core, one chat; a look is how it is drawn ----
    def _mode_at(self):
        """Where the look in use was left: classic's own key (as before 2026-10-29), else the look's entry; None: its own default place."""
        return self.win.store.get(NOTCH_KEY) if self.mode == "classic" else self.ui[self.mode]["at"]

    def _keep_place(self, at):
        if self.mode == "classic":
            return self.win.keep(NOTCH_KEY, at)
        self.ui[self.mode]["at"] = {"cx": at["cx"], "y": at["y"]} if at else None
        self.win.keep(I.UI_KEY, self.ui)

    def set_mode(self, mode):
        """Another look (Settings → General → Appearance, or the notch's own menu): its outline, closed face, header and Home change in
        place, at the look's own place; Core, the chat, the running tasks, the approvals waiting and every look's layout stay as they are.
        Kept for the next start."""
        if mode not in I.MODES or mode == self.mode:
            return
        self.grid.edit(False)
        if self.mode != "limits":
            self.keep_island()  # (the grid as it is, for the look it belongs to)
        self.mode = self.ui["mode"] = mode
        self.win.keep(I.UI_KEY, self.ui)
        self.look_seg.set_value(mode)
        if self.lined:  # a look just chosen is seen: the notch unfolds from its thin line (a swipe up folds it again, as before)
            self.lined = False
            self.p = prefs({**self.p, "lined": False})
            self.win.keep("pet", self.p)
        at = self._mode_at() or {}
        self.cx, self.top = at.get("cx"), at.get("y")
        self._apply_mode()

    def reset_mode(self, mode):
        """One look back to how it started: its layout and its place; the other looks as they are."""
        if mode == "classic":
            self.island["layout"] = I.preset(I.DEFAULT)
            self.win.keep(ISLAND_KEY, dict(self.island))
            self.win.keep(NOTCH_KEY, None)
        elif mode in I.MODES:
            self.ui = I.ui_reset(self.ui, mode)
            self.win.keep(I.UI_KEY, self.ui)
        if mode == self.mode:
            self.cx = self.top = None
            self._apply_mode()

    def _apply_mode(self, first=False):
        """The look in use, put in place: the grid at its size with its own layout, the tabs over (or, on the floating island, under)
        the views and its header, the widget menu, the limits' reads while closed; then its size and place (not while being made)."""
        island, limits = self.mode == "island", self.mode == "limits"
        self.grid.set_dims(I.ISLAND_GRID if island else I.GRID, ISLAND_CELL_H if island else Q.CELL_H)
        if not first:
            self.grid.set_layout([])  # (each look's tiles made anew: the floating island's stand up, classic's lie as before)
        self.grid.set_layout(self.ui["island"]["layout"] if island else I.normal(self.island.get("layout")))
        v = self.body_lay
        self.tabs_box.setVisible(not island and not limits)
        self.island_tabs.setVisible(island)
        self.limit_tabs.setVisible(limits)
        self.chat_chips.setVisible(island)
        self.head.setVisible(island)
        ear = L.EAR if island else 0  # (the island's cards sit between its flared corners)
        v.setContentsMargins(PAD + ear, TOP_IN, PAD + ear, PAD)
        self.menu.setVisible(not limits)
        self.menu.set_look(island)
        if limits:
            self._usage_t.start()
        else:
            self._usage_t.stop()
        if first:
            return
        if self.isVisible():
            self._fade_in(SWITCH_FADE_MS, 0.35)
        self._load_sources(only="usage")
        self._watch_media()
        self._asks_drawn = None
        if self.opened:
            self._switch(self.view)
        self._go()
        self.refresh()
        self.update()

    def _chrome_h(self):
        """The open notch's height round its cards: its tabs over them (and its header too on the floating island, the tabs under)."""
        if self.mode == "island":  # its header over the cards, its tabs under them
            return TOP_IN + L.HEAD_H + TAB_GAP + TAB_GAP + L.TABS_H + PAD
        if self.mode == "limits":  # its row of icons over the cards
            return TOP_IN + L.TABS_H + TAB_GAP + PAD
        return TOP_IN + 28 + TAB_GAP + PAD

    def read_usage(self, force=False):
        """Every AI's limits read again (the limits look's Refresh: `force`, every source now)."""
        self._load_sources(force=force, only="usage")

    def _usage_due(self):
        """The sources that may be asked now: each one's wait after its last read (longer after failures: usage.wait_for)."""
        now = time.time()
        return {k for k in U.ORDER if self._usage_next.get(k, 0.0) <= now}

    def _got_usage(self, cards):
        """A read of every AI's limits: kept as the one copy every look draws; each source asked again after its own wait; a window that
        just passed usage.HIGH or FULL is a nudge (a notification when the owner has them on), once per level until it resets."""
        if not isinstance(cards, list):
            return  # (the read itself failed: the last cards stay, each as it was)
        now = time.time()
        for c in cards:
            if (c.get("read_at") or 0) >= self._usage_asked - 1:  # (asked this time; one not due came back as it was)
                k = c["key"]
                self._usage_fails[k] = self._usage_fails.get(k, 0) + 1 if U.failed(c) else 0
                self._usage_next[k] = now + U.wait_for(self._usage_fails[k])
        self.usage = cards
        for c, w, pct, lvl in U.nudges(cards, self._usage_told, now):
            words = f"{w['name']} at {pct}% · {U.reset_words(w.get('resets_at'), now, Q.h24())}"
            print(f"notch: usage nudge: {c['name']} {words}", flush=True)
            if self.toast and self.p.get("notify"):
                self.toast(f"{c['name']}: {'limit almost reached' if lvl == 'full' else 'running low'}", words)
        if self.opened and self.view == "home" and self.mode == "limits":
            self.limits_view.fill()
            self._go()
        elif not self.opened:
            self._go()  # (closed: the limits look's bar shows the busiest one, or the classic face when none has a number)

    def limits_layout(self, move=None, hide=None, show=None):
        """The limits look's own layout: a card moved earlier (-1) or later (+1), hidden or shown again; kept in data/desktop.json."""
        lim = self.ui["limits"]
        keys = [c["key"] for c in U.arrange(self.usage or [], lim)]
        if move and move[0] in keys:
            i = keys.index(move[0])
            j = i + move[1]
            if 0 <= j < len(keys):
                keys[i], keys[j] = keys[j], keys[i]
                lim["order"] = keys
        if hide and hide not in lim["hidden"]:
            lim["hidden"].append(hide)
        if show and show in lim["hidden"]:
            lim["hidden"].remove(show)
        self.win.keep(I.UI_KEY, self.ui)
        self.limits_view.fill()
        self.limits_resized()

    def limits_resized(self):
        if self.opened and self.view == "home":
            self._go()

    # -- drawing: the shell, and the closed bar's pet, line and pals ---------------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        closed = not self.opened and h <= CLOSED_LINE[1] + 8
        if self.mode == "island" and closed and not self.lined:  # the floating island's pill and its bubbles: their own outlines
            L.paint_island(self, p, w, h)
            return p.end()
        open_k = min(1.0, max(0.0, (h - CLOSED[1]) / 60))
        radius = CORNER[0] + (CORNER[1] - CORNER[0]) * open_k
        if self.mode == "island":  # the recording's: hanging with its flared corners, round below (round all round dragged off the edge)
            radius = ISLAND_R * open_k + h * 0.55 * (1 - open_k)
            path = L.ears_path(w, h, radius) if self.hanging() else shell_path(w, h, False, min(radius, h / 2))
        else:
            path = shell_path(w, h, self.hanging(), radius)
        p.fillPath(path, QColor(SHELL))
        if not self.hanging():
            p.setPen(QPen(QColor("#2A2B30"), 1))
            p.drawPath(path)
        if closed and not (self.mode == "limits" and not self.lined and L.paint_limits(self, p, w, h)):
            self._paint_closed(p, w, h)
        p.end()

    def bar_words(self):
        """What the closed bar says while the mouse rests on it (the owner, 2026-10-20 night): the session it watches, by its app's own name
        (else its agent), and the folder it works in ("… · monday › app"); None with no session of the owner's live."""
        w = self._bar_session()
        if not w:
            return None
        folder = folder_words((w.get("where") or {}).get("cwd"))
        return (w.get("name") or w.get("agent") or "A session") + (f" · {folder}" if folder else "")

    def _paint_closed(self, p, w, h):
        if self.lined and h > LINE[1] + 4 and self._at_work():  # an app of the owner's at work: its mascot alone where the line was (2026-10-26)
            k = h - 4
            self.mascot().draw(p, QRectF(w / 2 - k / 2, 1, k, k), self.clock(), self.mood)
            return
        if self.lined and h <= LINE[1] + 4:  # the line: the shell alone, a faint lighter thread along it (the mouse on it brings the bar back)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(K.alpha("#9AA0A6", 0.55 if self._over else 0.35))
            p.drawRoundedRect(QRectF(w * 0.3, h / 2 - 1, w * 0.4, 2), 1, 1)
            return
        p.setPen(Qt.PenStyle.NoPen)  # the bar's handle: the line's thread at its bottom (a swipe or a drag up folds it, down opens it)
        p.setBrush(K.alpha("#9AA0A6", 0.55 if self._over else 0.32))
        p.drawRoundedRect(QRectF(w / 2 - HANDLE_W / 2, h - 5, HANDLE_W, HANDLE_H), HANDLE_H / 2, HANDLE_H / 2)
        pet = self._pet_rect()
        self.mascot().draw(p, QRectF(pet.left(), pet.top() - 3, pet.width() + 4, pet.height() + 4), self.clock(), self.mood)
        st, title, _detail = self.shown
        words_ = self.bar_words() if self._over else None
        if words_:  # the mouse on the bar: which session, in which folder, in place of the dock's pills or the state's line
            p.setPen(QColor(TEXT_2))
            p.setFont(font(11.5, 500))
            p.drawText(QRectF(pet.right() + 10, 0, w - pet.right() - 22, h), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(words_, Qt.TextElideMode.ElideMiddle, int(w - pet.right() - 24)))
            return
        if st not in ("idle", "listening", "asleep"):
            dot = QColor(K.Dot.COLORS.get({"waiting": "wait", "working": "wait", "asked": "wait", "failed": "bad", "offline": "off",
                                           "alert": "wait"}.get(st, "ok"), GREEN))
            p.setPen(QColor(TEXT))
            p.setFont(font(12.5, 600))
            text = p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(w - pet.right() - 70))
            p.drawText(QRectF(pet.right() + 10, 0, w - pet.right() - 60, h), Qt.AlignmentFlag.AlignVCenter, text)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QPointF(w - 22, h / 2), 4, 4)
            return
        for i, pl in enumerate(self._dock_items()[:DOCK_SHOWN]):  # the dock's pills, two by two (the recording's frame 26, at 4x): real ones only
            cx = w - CLUSTER_RIGHT - (1 - i % 2) * CLUSTER_STEP
            cy = h / 2 + (-8 if i < 2 else 8)
            (self._logo(pl.get("exe")) or K.pal(self._colour(pl))).draw(p, QRectF(cx - 10.5, cy - 10.5, 21, 21), 1.0, "idle")  # its own logo first


# -- Rewind… (Dotpals'; the retired notch's RewindDialog, on Qt) -----------------------------------------------------------------------
class RewindDialog(QDialog):
    """Rewind… for one of the owner's agents' last request (Settings → Restore points): its files put back as they were before it, the
    ones the owner keeps ticked, after a look at Core's preview (POST /api/rewind/preview: each file's change, the diff back to before).
    Core checks again that nothing changed since the preview and that no agent works there, and keeps the undo (POST /api/rewind, then
    /api/rewind/undo: Monday's window only, its token through the Bridge); this window then offers Undo the rewind."""

    def __init__(self, notch, rw):
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.n, self.rw, self.plan, self.rows, self.undo_ready = notch, rw, None, [], False
        self.setWindowTitle("Rewind this request?")
        self.setAccessibleName("Rewind this request?")
        self.setMinimumSize(480, 400)
        self.resize(640, 560)
        self.setStyleSheet(f"QDialog {{ background: {K.CARD}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Rewind this request?", 15, 700))
        self.asked = label("Reading what it changed…", 13, 400, TEXT_2, wrap=True, select=True)
        lay.addWidget(self.asked)
        holder = QWidget()
        self.files = QVBoxLayout(holder)
        self.files.setContentsMargins(0, 0, 0, 0)
        self.files.setSpacing(2)
        self.files.addStretch(1)
        self.list = QScrollArea()
        self.list.setWidget(holder)
        self.list.setWidgetResizable(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMaximumHeight(150)
        self.list.setAccessibleName("The files that go back: untick any to keep")
        K.SmoothScroll(self.list)
        lay.addWidget(self.list)
        self.note = label("", 12.5, 400, K.AMBER, wrap=True)
        self.note.hide()
        lay.addWidget(self.note)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diff.setFont(font(12, mono=True))
        self.diff.setAccessibleName("What goes back: the difference from now to before the request")
        lay.addWidget(self.diff, 1)
        bar = QHBoxLayout()
        self.status = label("", 12.5, 400, TEXT_2, wrap=True)
        bar.addWidget(self.status, 1)
        close = Button("Close", "secondary", name="Close without changing anything")
        close.clicked.connect(self.close)
        bar.addWidget(close)
        self.go = Button("Rewind", "primary", name="Put the ticked files back as they were before the request")
        self.go.setEnabled(False)
        self.go.clicked.connect(self._clicked)
        bar.addWidget(self.go)
        lay.addLayout(bar)

    def load(self):
        """Core's preview, with the files as they are now."""
        self.n.bridge.post("/api/rewind/preview", {"id": self.rw["id"]}, self.got, timeout=60)

    def got(self, plan, err=None):
        """The preview: what it was asked, each file and what happens to it (ticked), why it can't go now, the diff back to before."""
        if not plan:
            self.asked.setText(f"Couldn't read the restore point: {err or 'no answer from Monday Core.'}")
            return
        self.plan = plan
        asked, files, busy = rewind_words(plan)
        self.asked.setText(asked)
        while self.files.count() > 1:  # (the stretch stays last)
            row = self.files.takeAt(0).widget()
            row.hide()
            row.deleteLater()
        self.rows = []
        for path, words in files:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            tick = K.Tick(f"{path}: {words}. Untick it to keep it as it is now", True)
            tick.toggled.connect(lambda _on: self._count())
            h.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            h.addWidget(label(f"{path}  ·  {words}", 12.5, 400, TEXT, wrap=True, select=True), 1)
            self.files.insertWidget(self.files.count() - 1, row)
            self.rows.append((path, tick))
        self.diff.setPlainText(plan.get("patch") or "(no difference to show)")
        self.note.setText(busy or "")
        self.note.setVisible(bool(busy))
        if plan.get("rewound"):
            self._undo_offer("This request was rewound.")
        self._count()

    def picked(self):
        return [path for path, tick in self.rows if tick.isChecked()]

    def _count(self):
        """The button says how many files go back; it works only with some ticked, nothing busy there, and no rewind to undo first."""
        if self.undo_ready:
            return
        n = len(self.picked())
        self.go.setText(rewind_button(n))
        self.go.setEnabled(bool(n and self.plan and not self.plan.get("busy") and not self.plan.get("rewound")))

    def _clicked(self):
        """The one button: Rewind, or once rewound, Undo the rewind (one handler, so a click is never both)."""
        if self.undo_ready:
            return self.undo()
        paths = self.picked()
        if not paths or not self.plan:
            return None
        self.go.setEnabled(False)
        self.status.setText("Putting them back…")
        self.n.bridge.post("/api/rewind", {"id": self.rw["id"], "paths": paths, "now": self.plan["now"]}, self._done, timeout=120)
        return None

    def _done(self, r, err):
        if not r:
            self.status.setText(err or "No answer from Monday Core: nothing was changed.")
            return self._count()
        self._undo_offer(r.get("text") or "Rewound.")
        return None

    def _undo_offer(self, words):
        self.undo_ready = True
        self.status.setText(words)
        self.go.setText("Undo the rewind")
        self.go.setAccessibleName("Put the files back as they were just before the rewind")
        self.go.setEnabled(True)
        for _path, tick in self.rows:
            tick.setEnabled(False)

    def undo(self):
        self.go.setEnabled(False)
        self.status.setText("Undoing…")
        self.n.bridge.post("/api/rewind/undo", {"id": self.rw["id"]}, self._undone, timeout=120)

    def _undone(self, r, err):
        if not r:  # (Core refused: the files changed since the rewind, or it's away; nothing was changed)
            self.status.setText(err or "No answer from Monday Core: nothing was changed.")
            self.go.setEnabled(True)
            return
        self.undo_ready = False
        self.go.setAccessibleName("Put the ticked files back as they were before the request")
        self.status.setText(r.get("text") or "Undone.")
        self.load()  # the files as they are now: a fresh preview


# -- the pet on the desktop (Dotpals' pal above the taskbar; notch.py's DeskPet, on Qt) ------------------------------------------------
DESK_KEY = "desk_place"     # data/desktop.json: where the owner left it, {"x", "y"} in logical px


def no_activate(w, on=True):
    """Windows: WS_EX_NOACTIVATE, so a click on it leaves the owner's app with the keyboard (macOS and Linux: a tool window rarely takes it)."""
    if sys.platform != "win32" or not w.isVisible():
        return
    import ctypes
    hwnd = int(w.winId())
    get, put = ctypes.windll.user32.GetWindowLongPtrW, ctypes.windll.user32.SetWindowLongPtrW
    get.restype, put.restype = ctypes.c_ssize_t, ctypes.c_ssize_t
    get.argtypes = (ctypes.c_void_p, ctypes.c_int)
    put.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
    style = get(hwnd, -20)
    put(hwnd, -20, (style | 0x08000000) if on else (style & ~0x08000000))


class DeskPet(QWidget):
    """The pet on the desktop (2026-10-16, the owner: "I'm only getting the notch, not the pet"): no frame, see-through around the pet,
    on top, never taking the focus; bottom right of the main screen, standing on the taskbar, until the owner drags it (kept). The
    notch's own pet in the notch's mood. Above it its name with ⤢ (Monday's window) and × (put it away); a click on the pet pokes it
    (three quick ones: dizzy, and it says so). Frames at 10 a second at rest, 30 while it moves."""
    NAME_H, BUBBLE_H, GAP = 26, 34, 6

    def __init__(self, notch):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.n = notch
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Your pet on the desktop")
        self.setAccessibleName("Your pet on the desktop")
        self.press, self.parts, self._placing = None, {}, False
        self._t = QTimer(self)
        self._t.timeout.connect(self._frame)
        self._save_t = QTimer(self)
        self._save_t.setSingleShot(True)
        self._save_t.setInterval(SAVE_AFTER_MS)
        self._save_t.timeout.connect(self._moved)
        self.sizes()

    def sizes(self):
        """Its square from General's "Size on the desktop" (DESK px at 1.0), and room above it for its name and its bubble."""
        from .notch import DESK
        self.P = int(DESK * self.n.p["desk_size"])
        self.setFixedSize(max(270, self.P), self.BUBBLE_H + self.NAME_H + self.GAP + self.P)

    def show_pet(self):
        from .notch import DESK_REST_MS
        size = self.size()
        self.sizes()
        if not self.isVisible() or size != self.size():  # (a new size stands where its feet were)
            self._placing = True
            self.move(self._where())
            self._placing = False
        self.show()
        no_activate(self)
        if not self._t.isActive():
            self._t.start(DESK_REST_MS)

    def hide_pet(self):
        self._t.stop()
        self.hide()

    def _where(self):
        """Where the owner left it while that is on a screen, else the bottom right of the main screen, standing on the taskbar."""
        pl = self.n.win.store.get(DESK_KEY) or {}
        x, y = pl.get("x"), pl.get("y")
        if isinstance(x, int) and isinstance(y, int) and any(s.availableGeometry().contains(QPoint(x + 10, y + 10))
                                                             for s in QGuiApplication.screens()):
            return QPoint(x, y)
        a = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(a.right() + 1 - self.width() - 16, a.bottom() + 1 - self.height())

    def _frame(self):
        from .notch import DESK_REST_MS, EAT_S, REACT_S
        art, now = self.n.art, time.monotonic()
        moving = now - art.react_at < REACT_S or art.dizzy(now) or now - art.eat_at < EAT_S or \
            self.n.shown[0] in ("working", "speaking", "hearing")
        want = FRAME_MS if moving and art.motion else DESK_REST_MS
        if self._t.interval() != want:
            self._t.setInterval(want)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        art, now, w, h, P = self.n.art, self.n.clock(), self.width(), self.height(), self.P
        name = art.p.get("name") or "Monday"
        p.setFont(font(12, 600))
        tw, iw, ph = p.fontMetrics().horizontalAdvance(name), 22, self.NAME_H
        pw = 28 + tw + 2 * iw + 6
        x0, y0 = (w - pw) / 2, self.BUBBLE_H
        p.setPen(QPen(QColor(EDGE), 1))
        p.setBrush(K.alpha("#0e0e11", 0.94))
        p.drawRoundedRect(QRectF(x0, y0, pw, ph), ph / 2, ph / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(art.body)
        p.drawEllipse(QPointF(x0 + 15, y0 + ph / 2), 4, 4)
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(x0 + 24, y0, tw + 2, ph), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        xo, xc = x0 + pw - 6 - 2 * iw, x0 + pw - 6 - iw
        K.paint_icon(p, "open", QRectF(xo + 4, y0 + ph / 2 - 7, 14, 14), TEXT_2, 2.0)
        K.paint_icon(p, "x", QRectF(xc + 4, y0 + ph / 2 - 7, 14, 14), TEXT_2, 2.0)
        self.parts = {"name": QRectF(x0, y0, xo - x0, ph), "open": QRectF(xo, y0, iw, ph), "close": QRectF(xc, y0, iw, ph),
                      "pet": QRectF((w - P) / 2, h - P, P, P)}
        if art.dizzy(now):  # Dotpals' "Whoa, too many pokes at once!"
            words = "Whoa, too many pokes at once!"
            p.setFont(font(12))
            bw, bh = min(w - 2, p.fontMetrics().horizontalAdvance(words) + 20), 26
            bx = (w - bw) / 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(QRectF(bx, 0, bw, bh), 9, 9)
            tip = QPainterPath()
            tip.moveTo(w / 2 - 6, bh - 1)
            tip.lineTo(w / 2 + 6, bh - 1)
            tip.lineTo(w / 2, bh + 6)
            tip.closeSubpath()
            p.drawPath(tip)
            p.setPen(QColor("#16161a"))
            p.drawText(QRectF(bx, 0, bw, bh), Qt.AlignmentFlag.AlignCenter, words)
        art.draw(p, QRectF((w - P) / 2, h - P, P, P), now, self.n.mood)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press, self.press_t = e.globalPosition().toPoint(), time.monotonic()

    def mouseMoveEvent(self, e):
        if self.press is not None and e.buttons() & Qt.MouseButton.LeftButton and dragging(  # (a click's drift isn't a drag: notch.dragging)
                time.monotonic() - self.press_t, (e.globalPosition().toPoint() - self.press).manhattanLength(), QApplication.startDragDistance()):
            self.press = None
            self.windowHandle().startSystemMove()

    def mouseReleaseEvent(self, e):
        if self.press is None or e.button() != Qt.MouseButton.LeftButton:
            return
        self.press = None
        part = next((k for k, r in self.parts.items() if r.contains(e.position())), None)
        if part == "close":
            self.n.set_pref("desk", False)
        elif part in ("open", "name"):
            self.n.open_window("home")
        elif part == "pet":
            self.n.poke()
            self.update()

    def moveEvent(self, e):
        if not self._placing and self.isVisible():
            self._save_t.start()
        super().moveEvent(e)

    def _moved(self):
        """A drag ended: inside its screen it stays (put back when past an edge), and it is kept there."""
        g = self.geometry()
        a = (QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()).availableGeometry()
        x, y = max(a.left(), min(a.right() + 1 - g.width(), g.x())), max(a.top(), min(a.bottom() + 1 - g.height(), g.y()))
        if (x, y) != (g.x(), g.y()):
            self._placing = True
            self.move(x, y)
            self._placing = False
        self.n.win.keep(DESK_KEY, {"x": x, "y": y})


# -- checks and pictures ---------------------------------------------------------------------------------------------------------------
def home_pills(n, hidden=False):
    """The dock's pills where Home shows them: the Agents widget's (the 2026-10-26 recording's default), else the AI tile's own dock.
    hidden: those not hidden by hand, whether or not their view is on show now."""
    t = n.grid.tiles.get("agents")
    pills = (t.pills if t is not None and t.size != "small" else n.dock.findChildren(Pill))
    return [p for p in pills if (not p.isHidden() if hidden else p.isVisible())]


def _stub_window(answers=None):
    from .qtwindow import Bridge, Window, _check as _wcheck  # noqa: F401
    from .native import Core

    class Stub(Core):
        def __init__(self, answers):
            self.answers, self.calls, self.http = answers, [], None

        def call(self, method, path, body=None, timeout=None):
            self.calls.append((method, path, body))
            for prefix, ans in self.answers.items():
                if path.startswith(prefix):
                    return ans if isinstance(ans, tuple) else (ans, None)
            return None, "Monday Core is unreachable."
    core = Stub(answers or {})
    bridge = Bridge(core)
    win = Window(bridge, store={"pet": {"kind": "witch", "sounds": False}}, ear=False)
    return win, bridge, core


def _head_of(tile):
    """The AI card's head as one line, as it reads: its name, then the grey rest (two labels since 2026-10-26: the two-tone head)."""
    return " · ".join(x for x in (tile.title.accessibleName(), tile.rest.accessibleName()) if x)


def _fits(view):
    """Every button showing in a view has the room its words need: none cut on both sides or squeezed (the owner's 2026-10-20 notch read
    "en Visual Studio Co")."""
    return all(b.width() >= b.sizeHint().width() - 1 for b in view.findChildren(Button) if b.isVisible())


def _burst(target, at, dx=0, dy=0, events=40, gap=0.0):
    """A touchpad's swipe as Windows sends it: dozens of small wheel events (replayed 2026-10-21), at a point of the notch; then its end
    (GESTURE_END_S of quiet). -> (lined, opened) after each event."""
    from PySide6.QtGui import QWheelEvent
    seen = []
    for _ in range(events):
        QApplication.sendEvent(target, QWheelEvent(at, target.mapToGlobal(at), QPoint(dx, dy), QPoint(dx, dy), Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
        seen.append((target.lined, target.opened))
        if gap:
            time.sleep(gap)
            QApplication.processEvents()
    target._gesture = None
    return seen


def _check_agent_view(check, n, bridge, core):
    """Home's agent view against the stub Core: what the retired notch had (the code card, the bars, Steps, Story, Next agent, Write to,
    Rewind…), from AGENT_WATCH only. Nothing touches the real clipboard or another app's window: _copy and _front are stood in for."""
    def near(c, hex_):
        q = QColor(hex_)
        return max(abs(c.red() - q.red()), abs(c.green() - q.green()), abs(c.blue() - q.blue())) <= 12 and c.alpha() > 200

    def drawn(w):  # the widget at 1:1, whatever the screen's scale
        img = QImage(w.width(), w.height(), QImage.Format.Format_ARGB32)
        img.fill(0)
        w.render(img)
        return img

    def shown():
        return [b.text() for b in n.views.widget(4).findChildren(Button) if not b.isHidden() and b is not n.ag_goto]
    plan = {"agent": "Claude Code", "project": "shop", "asked": "Fix the VAT rounding", "busy": None, "now": "t1", "rewound": False,
            "patch": "--- a/billing.ts\n+++ b/billing.ts\n-Math.round\n+Math.floor\n",
            "files": [{"path": "billing.ts", "change": "restore", "since": False}, {"path": "new.ts", "change": "delete", "since": False}]}
    chat = [{"who": "you", "text": "Fix the VAT rounding in billing please"}, {"who": "Claude Code", "text": "Fixed it: billing.ts rounds now."}]
    core.answers.update({"/api/agents/chat": {"agent": "Claude Code", "turns": chat},
                         "/api/agents/story": {"lines": ["Today · 1 request · 1 file changed", "", "09:41 You asked: Fix the VAT rounding"],
                                               "text": "Today · 1 request · 1 file changed\n\n09:41 You asked: Fix the VAT rounding\n· Changed billing.ts  +1 −1"},
                         "/api/agents/say": {"queued": True, "why": None}, "/api/rewind/preview": plan,
                         "/api/rewind/undo": {"text": "Undone: 1 files back as they were before the rewind."},
                         "/api/rewind": {"text": "Put back 1 file.", "restored": ["billing.ts"], "deleted": [], "skipped": []}})
    copied = []
    n._copy, n._front = copied.append, lambda pl: True
    n._read_places = lambda: n.places_read.emit([])  # (no IDE windows of the owner's: the dock's places come from the check alone)
    n.places = []
    steps = [["key", "Key steps 2", ["2 key steps · 1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2", "RUN · npm test · try 2 · 1s ✓"]],
             ["problems", "Problems 1", ["1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2"]], ["files", "Files 1", ["Changed · 1", "billing.ts  +1 −1"]]]
    sa = {"key": "a", "agent": "Claude Code", "state": "working", "title": "Claude Code · shop · 2/4 · Fix the rounding", "done_at": None,
          "lines": ["You asked: Fix the VAT rounding in billing please", "Tests passed · 3 passed"],
          "diff": {"path": os.path.join(os.path.abspath(os.path.join("work", "shop")), "src", "billing.ts"), "file": "billing.ts",
                   "removed": 1, "added": 1, "more": 4, "rows": [  # (watch.diff_words: the agent's own patch, its line numbers)
                       ["@", 40, 40, ""], [" ", 40, 40, "export function vat(net, rate) {"], ["-", 41, None, "  const vat = Math.floor(net * rate)"],
                       ["+", None, 41, "  const vat = Math.round(net * rate)  // half a cent rounds up, as the tax office says: " + "every " * 30],
                       [" ", 42, 42, "  return vat"], ["@", 90, 90, ""], [" ", 90, 90, "export const RATE = 0.2"]]},
          "meters": [{"kind": "plan", "label": "Plan 2/4 · Fix the rounding", "of": 0.5},
                     {"kind": "context", "label": "Context 896k tokens (its log doesn't say how many fit)", "of": None},
                     {"kind": "use", "label": "Codex 5h 64%", "of": 0.64}],
          "where": {"entry": "claude-desktop", "cwd": os.path.abspath(os.path.join("work", "shop"))}, "steps": steps,
          "rewind": {"id": "r1", "files": 2, "rewound": False}}
    sb = {"key": "b", "agent": "Codex", "state": "working", "title": "Codex · api · Adding a limiter", "lines": [], "diff": [], "meters": [],
          "steps": [], "rewind": None, "where": {}, "done_at": None}
    n.state.perms.clear()  # (earlier checks' approvals, never answered by the stub Core: Monday's own approval takes Home's card since 2026-10-26)
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [sa, sb]}})
    n.open("home")
    bridge.settle()
    ai = n.grid.tiles["ai"]
    earlier, n.state.agent_result = n.state.agent_result, None  # (an earlier check's alert would be said on the tile for its seconds: below)
    n.refresh()
    check("Home: one of your agents on the AI tile (a tap opens its view: its edits, Steps, Story)", (n.card_session or {}).get("key") == "a"
          and _head_of(ai) == "Claude Code · work › shop" and ai.cursor().shape() == Qt.CursorShape.PointingHandCursor,
          _head_of(ai))
    n.state.agent_result = earlier
    n._swipe(1)  # a sideways drag or a touchpad swipe on the card (the owner, 2026-10-21): the next chat, round the end
    bridge.settle()
    check("Home: a swipe shows the next chat", n.state.pick == "b" and (n.state.agent or {}).get("key") == "b", f"{n.state.pick}")
    n._swipe(1)
    bridge.settle()
    check("…and round the end back to the first", n.state.pick == "a" and (n.state.agent or {}).get("key") == "a", f"{n.state.pick}")
    moved, real_show = [], n._show_session
    n._show_session = lambda k: (moved.append(k), real_show(k))[1]  # noqa: E731
    _burst(n, QPointF(ai.mapTo(n, ai.rect().center())), dx=-12)  # one sideways touchpad swipe on the AI tile
    n._show_session = real_show
    check("a sideways swipe on the AI tile's chats moves one chat, however long the swipe", moved == ["b"] and n.state.pick == "b", str(moved))
    from PySide6.QtTest import QTest
    at = ai.rect().center()
    QTest.mousePress(ai.title, Qt.MouseButton.LeftButton, pos=ai.title.rect().center())  # a mouse drag across the tile: the chat before
    QTest.mouseMove(ai.title, ai.title.rect().center() + QPoint(SWIPE_PX + 10, 2))
    QTest.mouseRelease(ai.title, Qt.MouseButton.LeftButton, pos=ai.title.rect().center() + QPoint(SWIPE_PX + 10, 2))
    check("…and a mouse dragged sideways across it: the chat before (the notch doesn't move)", n.state.pick == "a" and not n.grid.editing,
          f"{n.state.pick} {at}")
    n._swipe(1)
    os.environ["MONDAY_REDUCED_MOTION"] = "0"
    n._swipe(1)
    faces = [w for w in ai.findChildren(QLabel) if w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) and w.pixmap()
             and not w.pixmap().isNull()]
    os.environ["MONDAY_REDUCED_MOTION"] = "1"
    end = time.monotonic() + SLIDE_MS / 1000 + 0.25
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    left = [w for w in ai.findChildren(QLabel) if w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) and w.pixmap()
            and not w.pixmap().isNull()]
    check("…the tile sliding: the last chat's face moves off and goes (none with reduced motion)", len(faces) == 1 and not left
          and n.state.pick == "a", f"{len(faces)} then {len(left)}")
    # 2026-10-20, the owner: while a session runs, no pills: what it is doing; the ↗ opens its chat; a dot per session (the AI tile since
    # 2026-10-24, the island's: Big it has room for all of it)
    rows = lambda: [lb.accessibleName() for lb in ai.rows if not lb.isHidden()]  # noqa: E731
    alert, n.state.agent_result = n.state.agent_result, None  # (an earlier check's alert: below)
    n.refresh()
    QApplication.processEvents()
    chips = lambda: [p.name for p in ai.findChildren(Pill) if not p.isHidden()]  # noqa: E731
    check("the AI tile while sessions run: no dock pills, but the session's own agent as its chip (the recording's \"Claude …\"); the "
          "session's agent and project, its latest lines (the newest bright) where the last task was, a dot per session by the chip, all "
          "inside the tile", chips() == ["Claude Code"]
          and _head_of(ai) == "Claude Code · work › shop" and ai.line.isHidden()
          and rows() == ["You asked: Fix the VAT rounding in billing please", "Tests passed · 3 passed"] and f"color: {TEXT};" in ai.rows[1].styleSheet()
          and not ai.dots.isHidden() and (ai.dots.n, ai.dots.at) == (2, 0) and "chat" in n.to_window.accessibleName() and _fits(n.views.widget(0))
          and ai.rows[1].mapTo(ai, QPoint(0, ai.rows[1].height())).y() <= ai.height(), f"{ai.title.accessibleName()} {rows()}")
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [{**sa, "steps": [["all", "All 3", ["3 steps · no problems", "RUN · npm test · 1s ✓",
                                                                                              "EDIT · billing.ts  +1 −1 ✓", "RUN · npm test · try 2 …"]]]}, sb]}})
    check("its steps as they come: the last two, not the line that sums them up", rows() == ["EDIT · billing.ts +1 −1 ✓", "RUN · npm test · try 2 …"],
          str(rows()))
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [{**sa, "name": "/loop , /ponytail:ponytail-gain"}, sb]}})  # its own name (2026-10-20)
    check("the tile's title is the session's name when its app has one, else its agent; then its folder (2026-10-20 night)",
          _head_of(ai) == "/loop , /ponytail:ponytail-gain · work › shop", _head_of(ai))
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [sa, sb]}})
    n._show_session("b")
    QApplication.processEvents()
    check("a dot shows the other session (its own agent's chip with it)", n.state.pick == "b" and ai.title.accessibleName() == "Codex"
          and rows() == ["Working…"] and ai.dots.at == 1 and chips() == ["Codex"], f"{ai.title.accessibleName()} {rows()} {chips()}")
    n.state.agent_result = ("alert", "Codex may be stuck", "No step for 6 minutes.", time.monotonic(), 60)
    n.refresh()
    check("an alert about them says itself on the tile for its seconds, the dots still there", ai.title.accessibleName() == "Codex may be stuck"
          and not ai.line.isHidden() and not rows() and not ai.dots.isHidden(), ai.title.accessibleName())
    n.state.agent_result = alert
    n._show_session("a")
    QTest.mouseClick(ai.title, Qt.MouseButton.LeftButton)  # (a real press and release on the tile's words: they pass it to the tile)
    check("a click on the AI tile's session opens its view (where its edits are); a hand over the tile says it can",
          n.view == "agent" and n.state.agent["key"] == "a" and ai.cursor().shape() == Qt.CursorShape.PointingHandCursor, n.view)
    n.open("home")
    bridge.settle()
    import shutil
    tmp = tempfile.mkdtemp(prefix="monday-claude-app-")
    try:  # the Claude app's own files, as it keeps them: local_<id>.json with the transcript's id at its top (and tombstones beside)
        os.makedirs(os.path.join(tmp, "acct", "org"))
        with open(os.path.join(tmp, "acct", "org", "local_abc.json"), "w", encoding="utf-8") as f:
            f.write('{"sessionId":"local_abc","cliSessionId":"93449e6a-d25a","cwd":"E:\\\\monday","title":"' + "x" * 4000 + '"}')
        with open(os.path.join(tmp, "acct", "org", "deleted_93449e6a-d25a"), "w", encoding="utf-8") as f:
            f.write("{}")
        check("the Claude app's own link to a session, found by its transcript's name", claude_app_link(r"C:\p\93449e6a-d25a.jsonl", [tmp])
              == "claude://claude.ai/epitaxy/local_abc" and claude_app_link(r"C:\p\other.jsonl", [tmp]) is None and claude_app_link(None, [tmp]) is None)
        rollout = r"C:\Users\x\.codex\sessions\2026\10\15\rollout-2026-10-15T11-33-29-01a13e28-9669-7522-9db2-5eef2cc2b77c.jsonl"
        check("the ChatGPT app's (Codex's) own link to its chat: codex://threads/ and the thread id ending its rollout's name; Codex in VS "
              "Code and anything else: none (their window comes forward)", chat_link({"key": rollout, "where": {"entry": "Codex Desktop"}})
              == "codex://threads/01a13e28-9669-7522-9db2-5eef2cc2b77c" and chat_link({"key": rollout, "where": {"entry": "codex_work_desktop"}})
              == "codex://threads/01a13e28-9669-7522-9db2-5eef2cc2b77c" and chat_link({"key": rollout, "where": {"entry": "codex_vscode"}}) is None
              and chat_link({"key": "x", "where": {}}) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    opened, keep_link = [], globals()["claude_app_link"]
    n._open_url = lambda url: opened.append(url) or True
    globals()["claude_app_link"] = lambda t, dirs=None: "claude://claude.ai/epitaxy/local_abc" if t == "a" else None  # noqa: E731
    n.to_window.click()
    check("the ↗ on a Claude app session opens that very chat by the app's own link (its window is in front already)",
          opened == ["claude://claude.ai/epitaxy/local_abc"] and n.view == "home", f"{opened} {n.view}")
    globals()["claude_app_link"], n._links = (lambda t, dirs=None: None), {}  # noqa: E731  (no such session in the app: as below)
    fronted, n.places = [], [{"key": "claude", "name": "Claude", "session": "a", "hwnd": 4242}]
    n._front = lambda pl: fronted.append(pl["hwnd"]) or True
    n.to_window.click()
    n.places = []
    n.to_window.click()
    globals()["claude_app_link"] = keep_link
    check("the ↗ brings that session's own window (the Claude app) to the front; with none of its own, its view here",
          fronted == [4242] and n.view == "agent", f"{fronted} {n.view}")
    n._front = lambda pl: True
    n.open("home")
    bridge.settle()
    QTest.mouseClick(n.grid.tiles["ai"].title, Qt.MouseButton.LeftButton)  # (the AI tile tapped: what Details was)
    bridge.settle()
    check("the AI tile tapped: Home's agent view (the Home tab lit) on that session, its chat read from Core", n.view == "agent"
          and n.tab_btns["home"].lit and n.state.agent["key"] == "a" and any(c[1] == "/api/agents/chat?key=a" for c in core.calls))
    check("its buttons: Next agent (two are live), Story, Rewind… (Core kept a restore point), Steps; no Stop: it isn't Monday's work",
          shown() == ["Next agent", "Story", "Rewind…", "Steps"], ", ".join(shown()))
    check("what its chat shows isn't said again above it", "Fix the VAT rounding" not in n.ag_line.text() and "Tests passed" in n.ag_line.text(),
          n.ag_line.text())
    QApplication.processEvents()
    img = drawn(n.code)
    _h, laid, _gutter, _x = n.code._layout(n.code.width())
    by = {r[0] + r[3]: r for r in laid}  # (a row by its sign and its line number)
    ground = lambda r: img.pixelColor(n.code.width() - 5, int(r[1] + r[2] / 2))  # noqa: E731  (a row's ground, right of its words)
    red, green, ctx = ground(by["-41"]), ground(by["+41"]), ground(by[" 40"])
    one = n.code._fonts()[1].lineSpacing() + 2 * CodeCard.PAD
    check("the code card: the file from its project with its counts; each row by its own line number, the removed on a red tint, the "
          "added on a green one, the lines around them plain; hunks apart (a gap, not a label); the rows Core didn't send counted",
          n.code.shown_path() == os.path.join("src", "billing.ts") and (n.code.card["added"], n.code.card["removed"]) == (1, 1)
          and red.red() > red.green() + 12 and green.green() > green.red() + 12 and ctx.name() == QColor(CODE_BG).name()
          and [r[0] + r[3] for r in laid] == [" 40", "-41", "+41", " 42", "@", " 90", "more4 more lines in this edit"],
          f"{red.name()} {green.name()} {ctx.name()} {[r[0] + r[3] for r in laid]}")
    check("a long line wraps under its own text (not cut with …); the words on a tint readable (WCAG AA)", by["+41"][2] > 2 * one - 1
          and by["-41"][2] == one and K.contrast(TEXT, red.name()) >= 4.5 and K.contrast(TEXT, green.name()) >= 4.5, f"{by['+41'][2]} vs {one}")
    n.code.copy_btn.click()
    check("its Copy: the whole path, and it says so", copied == [n.code.card["path"]] and n.state.agent_result[1] == "Copied", str(copied))
    n.ag_scroll.widget().setMinimumHeight(2000)  # (a long view: its middle scrolls)
    QApplication.processEvents()
    _burst(n, QPointF(n.ag_scroll.mapTo(n, n.ag_scroll.rect().center())), dy=12)
    n.ag_scroll.widget().setMinimumHeight(0)
    check("a swipe over what scrolls (the view's middle) is its own: the notch stays open, however far it runs past the end",
          n.opened and n.view == "agent", f"opened={n.opened} {n.view}")
    copied.clear()
    n.state.agent_result = None
    img = drawn(n.meters)
    at = lambda i, k: img.pixelColor(int(n.meters.bar_rect(i).left() + n.meters.bar_rect(i).width() * k), int(n.meters.bar_rect(i).center().y()))  # noqa: E731
    check("the bars: the plan amber to half then its track; the context words alone (its log doesn't say how far); the limits cyan",
          [f for _k, _w, f in n.meters.rows] == [0.5, None, 0.64] and near(at(0, 0.25), K.AMBER) and near(at(0, 0.75), TRACK)
          and not any(near(at(1, k), TRACK) or near(at(1, k), K.GREEN_TEXT) for k in (0.1, 0.3, 0.5, 0.7, 0.9))
          and near(at(2, 0.3), ACCENT) and near(at(2, 0.8), TRACK), f"{at(0, 0.25).name()} {at(0, 0.75).name()} {at(2, 0.3).name()}")
    n._agent_act("steps")
    check("Steps: this request's runs and edits with their tries, Dotpals' filters as chips (the one shown lit) and Files; no card or bars",
          "fixed on try 2" in n.ag_line.text() and shown() == ["Key steps 2", "Problems 1", "Files 1"] and n.code.isHidden() and n.meters.isHidden()
          and n.ag_back.accessibleName() == "Back to what it's doing now", ", ".join(shown()))
    next(b for b in n.views.widget(4).findChildren(Button) if not b.isHidden() and b.text() == "Files 1").click()
    files = n.ag_line.text()
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [{**sa, "steps": steps[:2] + [["files", "Files 2", ["Changed · 2", "billing.ts  +1 −1",
                                                                                                               "rates.ts  +4 −0"]]]}, sb]}})
    check("a filter (Files), live as the agent goes on", "billing.ts  +1 −1" in files and "rates.ts  +4 −0" in n.ag_line.text()
          and "Files 2" in shown())
    n._agent_back()
    check("its ←: back to what it's doing now", n.story is None and n.view == "agent" and "Story" in shown())
    n._agent_act("story")
    bridge.settle()
    check("Story: today in plain words, from Core (GET /api/agents/story), with Copy today", any(c[1] == "/api/agents/story?key=a" for c in core.calls)
          and "09:41 You asked" in n.ag_line.text() and shown() == ["Copy today"])
    next(b for b in n.views.widget(4).findChildren(Button) if not b.isHidden() and b.text() == "Copy today").click()
    check("Copy today: the whole day copied, then back, saying so", copied == [core.answers["/api/agents/story"]["text"]] and n.story is None
          and n.ag_note.text().startswith("Copied"))
    n._agent_act("next")
    bridge.settle()
    check("Next agent: the next live one, with its own view (no edit: no card; no restore point: no Rewind) and chat",
          n.state.agent["key"] == "b" and "Codex · api" in n.ag_title.accessibleName() and n.code.isHidden() and "Rewind…" not in shown()
          and any(c[1] == "/api/agents/chat?key=b" for c in core.calls))
    n._agent_act("next")
    n.say_box.setPlainText("use port 9000")
    n.say()
    bridge.settle()
    check("Write to: the words to Core for that session (POST /api/agents/say), the box emptied", ("POST", "/api/agents/say",
          {"key": "a", "text": "use port 9000"}) in core.calls and not n.say_box.toPlainText() and n.ag_note.text().startswith("Queued for Claude Code"),
          n.ag_note.text())
    core.answers["/api/agents/chat"] = {"agent": "Claude Code", "turns": chat + [{"who": "you", "text": "use port 9000", "waiting": True}]}
    n._agent_chat(force=True)
    bridge.settle()
    check("…and its chat shows them waiting for its next step", any("use port 9000\n(waiting for its next step)" == lb.text()
                                                                    for lb in n.ag_scroll.widget().findChildren(QLabel) if not lb.isHidden()))
    core.answers["/api/agents/say"] = {"queued": False, "why": "Claude Code isn't working now, so it won't ask Monday for it"}
    n.say_box.setPlainText("and run the tests")
    n.say()
    bridge.settle()
    check("Write to when it can't take them: copied, and said where to paste them", copied[-1] == "and run the tests"
          and "Paste it in its chat in its window (Ctrl+V)." in n.ag_note.text() and n.ag_note.text().startswith("Copied for Claude Code"))
    n.places = [{"key": "vscode", "name": "VS Code", "folder": "shop", "hwnd": 123, "session": "a", "state": "working"}]
    n.say_box.setPlainText("and lint it")
    n.say()
    bridge.settle()
    check("…its window brought forward when Monday sees it (the dock's), Go to it named", "in VS Code (Ctrl+V)" in n.ag_note.text()
          and not n.ag_goto.isHidden() and n.ag_goto.text() == "Go to VS Code")
    n._links["a"] = "claude://claude.ai/epitaxy/local_abc"
    opened.clear()
    n.say_box.setPlainText("and the docs")
    n.say()
    bridge.settle()
    check("…or that very chat opened by its app's own link (2026-10-22), to paste in", opened == ["claude://claude.ai/epitaxy/local_abc"]
          and "in the Claude app (Ctrl+V)" in n.ag_note.text(), n.ag_note.text())
    core.answers["/api/agents/chat"] = {"agent": "Claude Code", "turns": chat + [{"who": "you", "text": "and the docs", "copied": True}]}
    n._agent_chat(force=True)
    bridge.settle()
    check("…and its chat here shows them, copied (the owner, 2026-10-22: \"it isnt shwoing\")", any(
        "and the docs\n(copied: paste it in its chat)" == lb.text() for lb in n.ag_scroll.widget().findChildren(QLabel) if not lb.isHidden()))
    core.answers["/api/agents/say"] = {"queued": True, "how": "wake", "why": None}
    n.say_box.setPlainText("are you there?")
    n.say()
    bridge.settle()
    check("Write to a session whose turn is over: its wake hook wakes it with them, and the notch says so",
          n.ag_note.text().startswith("Queued for Claude Code") and "Waking Claude Code with your words" in n.ag_note.text(), n.ag_note.text())
    core.answers["/api/agents/chat"] = {"agent": "Claude Code", "turns": chat + [{"who": "you", "text": "are you there?", "waiting": True,
                                                                                 "how": "wake"}]}
    n._agent_chat(force=True)
    bridge.settle()
    check("…and its chat shows them waking it", any("are you there?\n(waking it with them)" == lb.text()
                                                    for lb in n.ag_scroll.widget().findChildren(QLabel) if not lb.isHidden()))
    core.answers["/api/agents/chat"] = {"agent": "Claude Code", "turns": chat + [{"who": "you", "text": "run the tests", "waiting": True,
                                                                                 "how": "queue"}]}
    n._agent_chat(force=True)
    bridge.settle()
    check("a Codex session's words, in its own queue (codex queue, 2026-10-22): shown queued", any(
        "run the tests\n(queued in Codex: read when it next runs)" == lb.text() for lb in n.ag_scroll.widget().findChildren(QLabel) if not lb.isHidden()))
    d = n.rewind_open(sa["rewind"], show=False)
    bridge.settle()
    check("Rewind…: Core's preview first (what was asked, each file ticked with what happens to it, the diff back to before)",
          ("POST", "/api/rewind/preview", {"id": "r1"}) in core.calls and "“Fix the VAT rounding”" in d.asked.text() and len(d.rows) == 2
          and d.go.text() == "Rewind 2 files" and d.go.isEnabled() and "+Math.floor" in d.diff.toPlainText(), d.go.text())
    d.rows[1][1].click()
    check("an unticked file stays as it is", d.go.text() == "Rewind 1 file" and d.picked() == ["billing.ts"])
    d.go.click()
    bridge.settle()
    check("Rewind: the ticked files only, with the preview's tree (POST /api/rewind, the window's token), then Undo the rewind",
          ("POST", "/api/rewind", {"id": "r1", "paths": ["billing.ts"], "now": "t1"}) in core.calls and d.go.text() == "Undo the rewind"
          and d.status.text() == "Put back 1 file." and not d.rows[0][1].isEnabled())
    d.go.click()
    bridge.settle()
    check("Undo the rewind (POST /api/rewind/undo), then a fresh preview", ("POST", "/api/rewind/undo", {"id": "r1"}) in core.calls
          and sum(c[1] == "/api/rewind/preview" for c in core.calls) == 2 and d.go.text() == "Rewind 2 files" and d.status.text().startswith("Undone"))
    core.answers["/api/rewind/preview"] = {**plan, "busy": "Codex is working there now"}
    d.load()
    bridge.settle()
    check("an agent working there: no rewind, and it says when", not d.go.isEnabled() and d.note.text() == "Codex is working there now: "
          "you can rewind when it's done.")
    core.answers["/api/rewind/preview"] = (None, "There's no restore point for that request.")
    d2 = n.rewind_open({"id": "gone", "files": 1}, show=False)
    bridge.settle()
    check("a restore point Core can't read: said, nothing to press", "There's no restore point for that request." in d2.asked.text()
          and not d2.go.isEnabled())
    check("every control in Rewind's window named", not K.named(d) and not K.named(d2), ", ".join(K.named(d) + K.named(d2)))
    d2.close()
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "q1", "agent": "Claude Code", "what": "run a command", "detail": "npm test"}}})
    check("an agent asking: answered on Home's card, not behind its view, as the 2026-10-26 recording draws it (who, \"needs permission\", "
          "the command in its field, what it wants under it)", n.view == "home" and n.agent_title.accessibleName() == "Claude Code"
          and n.agent_verb.text() == "needs permission" and n.agent_cmd.text == "npm test" and not n.agent_cmd.isHidden()
          and n.agent_line.text().startswith("Wants to run a command"), f"{n.agent_title.accessibleName()} / {n.agent_verb.text()} / "
          f"{n.agent_cmd.text} / {n.agent_line.text()}")
    check("an agent asking: the whole width (the widgets step aside), Deny and Allow uncut, the title not said twice",
          n.grid.isHidden() and n.home_card.isVisible() and n.home_card.width() > OPEN_W * 0.8 and _fits(n.views.widget(0))
          and not n.agent_line.text().startswith("Claude Code wants"), n.agent_line.text())
    n.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "q1"}})
    check("…answered: the widgets back, the ask's card gone", n.grid.isVisible() and n.home_card.isHidden())
    n._go_place(n.places[0])
    check("a pal with one of your sessions opens its view, as the retired notch's did", n.view == "agent" and n.state.pick == "a")
    n.state.watch = [sa]
    n._fill_dock()  # (while it runs: its own agent's chip, no dock)
    n.state.watch = [{**sa, "state": "done", "done_at": time.time() - 3600}]  # (over long ago, and Core has said nothing since)
    n._show_home_state()
    check("the last session over with no word from Core since: the dock's pills back, not blank (on the Agents widget beside the AI tile, "
          "the 2026-10-26 default; the AI tile's own dock when it is alone)", any(not p.isHidden() for p in home_pills(n, hidden=True)),
          str([(p.name, p.isHidden()) for p in home_pills(n, hidden=False)]))
    # (the agent view shows: Home's are put by)
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": []}})
    ai = n.grid.tiles["ai"]
    check("its session over: the AI tile's own words again (its line, its pills; no steps, no dots)", n.view == "home" and not ai.line.isHidden()
          and all(lb.isHidden() for lb in ai.rows) and ai.dots.isHidden() and n.card_session is None and n.dock_grid.count() > 0)
    n.places = []


def _check_cards(check, n, bridge, core):
    """Home's card as the 2026-10-26 recording draws it, against the stub Core: Monday's own approval ("needs permission", the act in its field,
    Deny / Allow with their keys, the "!" badge, the amber light), Ctrl+Alt+Y answering it, the end of what was allowed ("finished", its result,
    Show / OK, the check, the green light), and nothing for what was denied or never asked; an agent's allowed session ending likewise."""
    app = QApplication.instance()
    n.state.perms.clear()
    n.state.asks.clear()
    n.state.watch, n._finished, n._card_seen, n._flow, n._flow_agents = [], None, None, set(), []
    n.open("home")

    def shown():
        app.processEvents()
        return [b for b in n.views.widget(0).findChildren(Button) if b.isVisible()]
    detail = ("Shut down MSI · now\nWindows closes every app and turns MSI off. An app with unsaved work can stop it and ask you first.\n\n"
              "Why: turns this PC off: every app closes")
    n.on_event({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 41, "task_id": "T41", "status": "PENDING", "kind": "POWER",
                                                                         "risk": "HIGH", "title": "Monday wants to shut down this PC",
                                                                         "detail": detail, "always": 0}}})
    b = shown()
    keep_agent, n.state.agent = n.state.agent, {"agent": "claude", "session": "s1"}  # (a Claude Code session watched, as live 2026-10-26)
    own_pet = n.mascot() is n.art
    n.state.agent = keep_agent
    check("Monday's own approval on Home's card (the 2026-10-26 recording): who, \"needs permission\", the act in its field, what it means, "
          "Deny and Allow with their keys, the \"!\" badge, the amber light, Monday's own pet (not a watched session's mascot); the widgets "
          "step aside; nothing cut", n.view == "home"
          and n.grid.isHidden() and n.agent_title.accessibleName() == "Monday" and n.agent_verb.text() == "needs permission"
          and n.agent_cmd.text == "Shut down MSI · now" and n.agent_line.text().startswith("High risk. Windows closes every app")
          and [x.text() for x in b] == ["Deny", "Allow"] and all(x.key for x in b) and n.card_pet.badge == "ask"
          and n.home_card.tint == CARD_TINTS["ask"] and _fits(n.views.widget(0)) and own_pet,
          f"{n.view} {n.agent_title.accessibleName()} / {n.agent_verb.text()} / {n.agent_cmd.text} / {[(x.text(), x.key) for x in b]}")
    n._hotkey("allow")
    bridge.settle()
    check("Ctrl+Alt+Y answers Monday's own approval (the card's first)", any(c[:2] == ("POST", "/api/permissions/41/approve") for c in core.calls))
    n.on_event({"type": "PERMISSION_GRANTED", "data": {"permission": {"id": 41, "task_id": "T41", "status": "GRANTED"}}})
    n.on_event({"type": "TASK_COMPLETED", "data": {"task": {"id": "T41", "status": "COMPLETED", "title": "Shut down this PC",
                                                            "result": "Windows is shutting down MSI now."}}})
    b = shown()
    check("…and its end on the same card: \"finished\", what Windows answered, Show and OK, the check badge, the green light",
          n.grid.isHidden() and n.agent_title.accessibleName() == "Monday" and n.agent_verb.text() == "finished"
          and n.card_result.accessibleName() == "Windows is shutting down MSI now." and n.agent_cmd.isHidden()
          and [x.text() for x in b] == ["Show", "OK"] and n.card_pet.badge == "done" and n.home_card.tint == CARD_TINTS["done"],
          f"{n.agent_verb.text()} / {n.card_result.accessibleName()} / {[x.text() for x in b]}")
    next(x for x in b if x.text() == "OK").click()
    app.processEvents()
    check("OK puts it away: the widgets again", not n.grid.isHidden() and n.home_card.isHidden() and n.card_pet.badge is None)
    n.on_event({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 42, "task_id": "T42", "status": "PENDING", "kind": "POWER",
                                                                         "risk": "HIGH", "title": "Monday wants to restart this PC",
                                                                         "detail": "Restart MSI · now", "always": 0}}})
    next(x for x in shown() if x.text() == "Deny").click()
    bridge.settle()
    n.on_event({"type": "PERMISSION_DENIED", "data": {"permission": {"id": 42, "task_id": "T42", "status": "DENIED"}}})
    n.on_event({"type": "TASK_CANCELLED", "data": {"task": {"id": "T42", "status": "CANCELLED", "error": "You denied: Restart this PC."}}})
    n.on_event({"type": "TASK_COMPLETED", "data": {"task": {"id": "T43", "status": "COMPLETED", "title": "Open Notepad", "result": "Opened Notepad."}}})
    app.processEvents()
    check("Deny posts the owner's no; a denied task, and a task nobody was asked about, leave Home's widgets as they are",
          any(c[:2] == ("POST", "/api/permissions/42/deny") for c in core.calls) and not n.grid.isHidden() and n.home_card.isHidden())
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "q41", "agent": "Codex", "what": "run a command", "detail": "git push origin main",
                                                      "project": "korus"}}})
    b = shown()
    check("an agent's ask on the same card: its own colour by its name, the command in its field",
          n.agent_title.accessibleName() == "Codex" and n.agent_cmd.text == "git push origin main" and n.agent_dot.state == PILL_COLOURS["codex"]
          and [x.text() for x in b] == ["Deny", "Allow"], f"{n.agent_dot.state} {[x.text() for x in b]}")
    next(x for x in b if x.text() == "Allow").click()
    bridge.settle()
    n.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "q41"}})
    s = {"key": "k1", "agent": "Codex", "state": "done", "done_at": time.time(), "title": "Codex · korus", "said": "Pushed to main · 48 tests passed",
         "lines": [], "where": {"cwd": os.path.join("work", "korus")}}
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [s]}})
    b = shown()
    check("…allowed, and its session done: \"Codex finished\", its own last words, Go to Codex and OK",
          n.agent_title.accessibleName() == "Codex" and n.agent_verb.text() == "finished"
          and n.card_result.accessibleName() == "Pushed to main · 48 tests passed" and [x.text() for x in b] == ["Go to Codex", "OK"],
          f"{n.agent_verb.text()} / {n.card_result.accessibleName()} / {[x.text() for x in b]}")
    n._card_ok(n.home_card_now())
    n.state.watch = []
    n.refresh()
    # a newer Monday in the update channel (docs/adr/0010): Home's card, last of all (an approval stands in front of it), Later and Update
    # in Deny and Allow's places; Later puts that version away; the source folder's is a Restart
    offer = {"state": "available", "action": "install", "current": "0.2.0", "detail": "Monday 0.3.0 is ready to install.",
             "latest": {"version": "0.3.0", "notes": "Updates in the notch.\nAnd more.", "msi": "Monday-0.3.0.msi"}}
    n.on_event({"type": "UPDATE_AVAILABLE", "data": offer})
    b = shown()
    check("a newer Monday: Home's card says so (\"0.3.0 is ready\", the release's first line), Later and Update, no badge",
          n.view == "home" and n.agent_title.accessibleName() == "Monday" and n.agent_verb.text() == "0.3.0 is ready"
          and n.agent_line.text() == "Updates in the notch." and [x.text() for x in b] == ["Later", "Update"] and n.card_pet.badge is None,
          f"{n.agent_verb.text()} / {n.agent_line.text()} / {[x.text() for x in b]}")
    n.on_event({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 77, "task_id": "T77", "status": "PENDING", "kind": "POWER",
                                                                         "risk": "HIGH", "title": "Shut down", "detail": "Shut down MSI · now",
                                                                         "always": 0}}})
    check("…an approval stands in front of it", n.home_card_now()["kind"] == "perm")
    n.on_event({"type": "PERMISSION_DENIED", "data": {"permission": {"id": 77, "task_id": "T77", "status": "DENIED"}}})
    n.state.perms.clear()
    asked_for = []
    keep_update_now, n.win.update_now = n.win.update_now, lambda said: asked_for.append(said)
    try:
        next(x for x in shown() if x.text() == "Update").click()
        app.processEvents()
        check("Update: the window is asked to install it (Window.update_now), the card says what it is doing",
              len(asked_for) == 1 and n.agent_line.text().startswith("Checking the package"), n.agent_line.text())
        asked_for[0]("Not updated: the package's SHA-256 differs.")
        app.processEvents()
        check("…and says what stopped it, on the card", n.agent_line.text() == "Not updated: the package's SHA-256 differs.", n.agent_line.text())
    finally:
        n.win.update_now = keep_update_now
    n._update_words = ""
    next(x for x in shown() if x.text() == "Later").click()
    app.processEvents()
    check("Later: that version's card put away", n.home_card_now() is None or n.home_card_now()["kind"] != "update")
    n._card_seen, n._finished = None, None  # (the finished session's card would stand in front of it again: it is gone here)
    n._got_update({**offer, "state": "restart", "action": "restart", "detail": "Monday 0.3.0 is on this PC: a restart loads it."}, None)
    b = shown()
    check("the source folder already holding it: \"0.3.0 is on this PC\", Later and Restart",
          n.agent_verb.text() == "0.3.0 is on this PC" and [x.text() for x in b] == ["Later", "Restart"], f"{[x.text() for x in b]}")
    n._got_update({"state": "up_to_date", "action": None, "latest": {"version": "0.3.0"}}, None)
    check("up to date: no card", n.home_card_now() is None or n.home_card_now()["kind"] != "update")
    n._card_seen = None


def _check_screenshot(check, n, bridge):
    """The owner's screenshot of their Home (2026-10-26, "now fix this"): text that was cut where it didn't need to be."""
    app = QApplication.instance()
    keep_watch, keep_media = list(n.state.watch), n.media.read
    n.media.read = lambda: None  # (never the owner's own media sessions in a check)
    n.grid.set_layout([I.item("ai", "big", 0, 0), I.item("music", "small", 2, 0), I.item("focus", "small", 3, 0)])
    n.open("home")
    n.state.watch = [{"key": "s1", "agent": "Claude Code", "name": "Monday", "state": "done", "done_at": time.time(),
                      "title": "Monday · app · done in 19m 23s", "lines": ["RUN · Show the time", "RUN · Write round 3 into the docs"],
                      "where": {"cwd": r"E:\monday\app"}}]
    n.refresh()
    bridge.settle()
    for _ in range(3):
        app.processEvents()
    ai = n.grid.tiles["ai"]
    pill = next((n.dock_grid.itemAt(i).widget() for i in range(n.dock_grid.count()) if isinstance(n.dock_grid.itemAt(i).widget(), Pill)), None)
    check("the AI card's head two-toned as the recording's: the session's name whole in white, the rest grey and the only part shortened "
          "(the owner's screenshot: \"Monday · done in 19…\")", ai.title.text() == "Monday" and ai.rest.accessibleName() == "done in 19m · app"
          and ai.rest.text().startswith("done in 19m") and "19m 23s" in ai.accessibleName()
          and not ai.rest.isHidden(), f"{ai.title.text()!r} / {ai.rest.text()!r}")
    check("…the session's own pill as wide as its name (it read \"Claude Co…\")", pill is not None and pill.name == "Claude Code"
          and pill.width() >= pill.sizeHint().width() - 1, f"{pill and pill.width()} / {pill and pill.sizeHint().width()}")
    mt = n.grid.tiles["music"]
    n.media._got({"ok": True, "now": {"app": "chrome.exe", "name": "YouTube", "status": "playing", "artist": "CampusX",
                                      "title": "What is Deep Learning? Deep Learning Vs Machine Learning"}})
    app.processEvents()
    two = ("\n" in mt.title.text(), mt.line.isHidden())
    n.media._got({"ok": True, "now": {"app": "Spotify.exe", "name": "Spotify", "status": "playing", "title": "Lofi", "artist": "Robo"}})
    app.processEvents()
    one = ("\n" not in mt.title.text(), not mt.line.isHidden(), mt.line.accessibleName())
    check("a small Music tile: a long title on two lines in place of the artist (it read \"What is D…\"); a short one on one line, its "
          "artist under it", two == (True, True) and one == (True, True, "Robo"), f"{two} {one}")
    n.state.watch = keep_watch
    n.media.read = keep_media
    n.grid.set_layout(I.preset(I.DEFAULT))
    n.refresh()


def _check_new_widgets(check, n, bridge, core):
    """The 2026-10-26 recording's widgets against the stub Core: Agents (the dock's pills with their logos, "!" on one that asks), Plan (an
    agent's own log's limits, Claude N/A; the header's pill and its card), GitHub (summary, Activity, My PRs; not signed in said so).
    Their sources are the notch's threads: given here as the readers would."""
    app = QApplication.instance()
    mine = [dict(it) for it in n.grid.layout_]
    n.state.perms.clear()
    n.state.asks.clear()
    n.state.watch, n._finished, n._plan_card_at = [], None, None
    n.grid.set_layout(I.preset(I.DEFAULT))
    n.open("home")
    app.processEvents()
    n.places = [{"name": "VS Code", "app": "vscode", "folder": "shop"}]  # (after the open: it reads the IDE windows again, none in a check)
    n._fill_dock()
    ag = n.grid.tiles.get("agents")
    check("the default Home is the recording's: the session (AI) and the agents beside it", sorted(n.grid.tiles) == ["agents", "ai"] and ag is not None
          and ag.size == "big")
    check("Agents: the dock's own pills (each program's logo, the owner's choice), none on the AI tile beside it",
          [p.name for p in home_pills(n)][:1] == ["VS Code"] and not any(not p.isHidden() for p in n.grid.tiles["ai"].findChildren(Pill)),
          str([p.name for p in home_pills(n)]))
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "q77", "agent": "VS Code", "what": "run a command", "detail": "npm test"}}})
    n.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "q77"}})  # (the card would take Home: the pill's badge is checked with the ask still there)
    n.state.asks["q78"] = {"id": "q78", "agent": "VS Code", "what": "edit", "detail": "a.py"}
    ag.fill()
    check("…the one whose agent asks wears the recording's orange \"!\"", [p.alert for p in ag.pills][:1] == [True])
    n.state.asks.clear()
    ag.fill()
    t0 = time.time()
    n._got_source("plan", [{"agent": "Codex", "plan": "Go", "windows": [{"name": "month", "used": 3, "resets_at": t0 + 25 * 86400}], "at": t0 - 5 * 86400}])
    app.processEvents()
    check("the header's plan pill: the agent's own log's use, only when it said it", n.plan_btn.isVisible() and n.plan_btn.text() == "Codex 3%"
          and "Month 3% used" in n.plan_btn.accessibleName(), n.plan_btn.accessibleName())
    n.show_plan()
    b = [x.text() for x in n.views.widget(0).findChildren(Button) if x.isVisible()]
    check("…clicked: the plan on Home's card (its window, use and reset; Claude N/A, never guessed), OK only",
          n.agent_title.accessibleName() == "Codex plan" and n.agent_verb.text() == "Go · updated 5 d ago"
          and n.card_result.accessibleName().startswith("Month 3% ↻ ") and "Claude plan: N/A" in n.agent_line.text() and b == ["OK"],
          f"{n.agent_verb.text()} / {n.card_result.accessibleName()} / {b}")
    n._card_ok(n.home_card_now())
    n._got_source("plan", [])
    check("no plan use in any log: no pill", n.plan_btn.isHidden())
    n.grid.set_layout([I.item("plan", "big", 0, 0), I.item("github", "big", 2, 0)])
    app.processEvents()
    pt, gt = n.grid.tiles["plan"], n.grid.tiles["github"]
    n._got_source("plan", [{"agent": "Codex", "plan": "Go", "windows": [{"name": "month", "used": 3, "resets_at": t0 + 25 * 86400}], "at": t0 - 60}])
    check("Plan widget (Big): the agent, its plan and how fresh, a bar per window, Claude N/A", pt.title.accessibleName() == "Codex plan"
          and pt.sub.accessibleName() == "Go · updated 1 min ago" and pt.rows[0][2].text() == "3%" and pt.rows[1][0].isHidden()
          and "N/A" in pt.claude.accessibleName(), pt.accessibleName())
    check("GitHub widget before its first read: says it's reading, never a number", gt.row_btns["prs"][1].text() in ("N/A", "")
          and "Reading" in gt.sub.accessibleName(), gt.sub.accessibleName())
    days = [[f"2026-10-{d:02d}", c] for d, c in ((20, 0), (21, 3), (22, 0), (23, 1), (24, 2), (25, 1), (26, 0))]
    n._got_source("github", {"ok": True, "why": None, "login": "me", "prs": [{"repo": "o/coucou", "number": 307, "title": "Windows & Linux 0.2.0",
                                                                             "url": "https://github.com/o/coucou/pull/307"}], "reviews": 2,
                             "repo": "o/coucou", "stars": 3800, "ci": {"state": "success", "title": "CI", "url": "u", "at": "x", "branch": "main"},
                             "days": days, "repos": ["o/coucou", "o/old"], "at": t0, "missing": [], "stars_all": 3900, "repo_count": 12})
    app.processEvents()
    check("GitHub widget (Big): the stars across all the owner's repositories, the last 7 days as squares, My PRs, To review, the default "
          "branch's CI in its words", gt.stars.text() == "★ 3.9k" and "12 repositories" in gt.stars.toolTip()
          and gt.row_btns["prs"][1].text() == "1" and gt.row_btns["review"][1].text() == "2"
          and gt.row_btns["ci"][1].text() == "all green" and gt.row_btns["ci"][0].text() == "CI on coucou" and len(gt.week.days) == 7,
          f"{gt.stars.text()} {[v.text() for _b, v in gt.row_btns.values()]}")
    gt.week.clicked.emit()
    app.processEvents()
    hx = gt.heat.mapTo(gt, gt.heat.rect().topLeft()).x() + gt.heat.x0  # (the map's first square, from the card's left edge)
    hr = gt.width() - hx - (gt.heat.cols * (gt.heat.cell + gt.heat.gap) - gt.heat.gap)
    overall_ = gt.sub.accessibleName()
    gt._day(len(days) - 2)  # (the pointer on yesterday's square, then off the map)
    one_day = gt.sub.accessibleName()
    gt.heat.leaveEvent(QEvent(QEvent.Type.Leave))
    check("…its squares open Activity: the year as GitHub's greens and at a glance (the owner, 2026-10-26: \"the overall\": its "
          "contributions, the streak now and the best), a day's count under the pointer, the whole again after; ‹ back; as many weeks as the "
          "card holds, the same inset both sides", gt.mode == "activity" and not gt.heat.isHidden()
          and overall_ == "7 past year · 3-day streak" and "best 3 days" in gt.sub.toolTip() and one_day == "Oct 25 · 1 contribution"
          and gt.sub.accessibleName() == overall_ and not gt.back.isHidden() and gt.heat.cols > 7 and hx >= 8 and abs(hx - hr) <= 1,
          f"{overall_} / {one_day} / {gt.sub.accessibleName()} / insets {hx:.0f} {hr:.0f}, {gt.heat.cols} weeks")
    gt.show_mode("prs")
    app.processEvents()
    opened = []
    keep_open, n._open_url = n._open_url, lambda u: opened.append(u) or True
    gt.pr_btns[0].click()
    n._open_url = keep_open
    check("…My PRs: each one, opened in the owner's own browser on a click (nothing written)", gt.title.text() == "My PRs"
          and gt.pr_btns[0].text().startswith("coucou#307") and opened == ["https://github.com/o/coucou/pull/307"], str(opened))
    gt.show_mode("summary")
    n._got_source("github", {"ok": False, "why": "GitHub CLI isn't signed in: run `gh auth login` once.", "at": t0})
    check("not signed in: said, no numbers", "gh auth login" in gt.sub.accessibleName() and gt.row_btns["prs"][1].text() == "N/A")
    n.island["github"] = {}
    called = []
    keep_load, n._load_sources = n._load_sources, lambda force=False: called.append(force)
    n._github_repo("o/old")
    n._load_sources = keep_load
    check("the widget's own setting: the repository chosen, kept with the island, read again at once", n.island["github"] == {"repo": "o/old"}
          and called == [True] and n.win.store[ISLAND_KEY].get("github") == {"repo": "o/old"})
    n.island.pop("github", None)
    n.grid.set_layout(mine)
    n.keep_island()
    n.places = []
    # the chat's model menu (the 2026-10-26 recording's provider chips over the list), from Core's own list
    doc = {"selected": "qwen3:4b", "effective": "qwen3:4b", "models": [
        {"id": "auto", "provider": "monday", "name": "Automatic", "available": True},
        {"id": "gpt-oss:120b-cloud", "provider": "ollama", "name": "gpt-oss:120b-cloud", "available": True},
        {"id": "qwen3:4b", "provider": "ollama", "name": "qwen3:4b", "available": True, "selected": True},
        {"id": "anthropic", "provider": "anthropic", "name": "Claude", "available": False},
        {"id": "gemini/flash", "provider": "gemini", "name": "flash", "available": True}]}
    base_models = core.answers.get("/api/models")
    core.answers["/api/models"] = doc  # (before the open: opening Chat asks Core for them, and a late answer must be this one)
    n.open("chat")
    bridge.settle()
    n._got_models(doc, None)
    check("the model pill in its provider's colour (Ollama's), Core's choice", n.model_chip.text().startswith("qwen3:4b")
          and n.model_chip.dot_color == PROVIDERS["ollama"][1], f"{n.model_chip.text()} {n.model_chip.dot_color}")
    n._model_menu()
    bridge.settle()
    app.processEvents()
    mp = n.model_panel
    chips = [c.text() for c in mp.findChildren(Chip) if not c.isHidden()]
    rows = [r for r in mp.findChildren(ModelRow) if not r.isHidden()]
    check("…clicked: the menu in the thread's place: a chip per provider with a model to pick (none Core doesn't list: no LM Studio), those "
          "with no key in one chip, the one in use chosen; its models, one on this PC first, the one in use lit and checked",
          mp.isVisible() and n.thread_scroll.isHidden() and chips == ["Google", "Ollama", "Auto", "1 not set up"] and mp.provider == "ollama"
          and [r.text() for r in rows] == ["qwen3:4b", "gpt-oss:120b-cloud"] and [r.on for r in rows] == [True, False]
          and [r.local for r in rows] == [True, False], f"{chips} {mp.provider} {[(r.text(), r.on, r.local) for r in rows]}")
    mp.pick_provider("_unset")
    app.processEvents()
    check("the providers with no key: named, and where to add one; nothing to pick", "No key for these in Monday yet: Anthropic." in mp.note.text()
          and mp.link.isVisible() and not [r for r in mp.findChildren(ModelRow) if not r.isHidden()], mp.note.text())
    mp.pick_provider("gemini")
    app.processEvents()
    row = next(r for r in mp.findChildren(ModelRow) if not r.isHidden() and r.text() == "flash")
    row.click()
    bridge.settle()
    check("a pick: Core switches (it checks the model answers), the thread back", ("POST", "/api/models/select", {"id": "gemini/flash"})
          in core.calls and not mp.isVisible() and not n.thread_scroll.isHidden())
    # the wardrobe (the 2026-10-26 recording's "Dress Mochi up"): Ctrl+Alt+G, the outfits on the owner's own pet, kept with it
    keep_pet = dict(n.p)
    n.set_prefs({**n.p, "kind": "blu", "wear": "none"})
    n._hotkey("pets")
    app.processEvents()
    check("Ctrl+Alt+G opens the wardrobe in the island (Settings lit): its 13 outfits, none on yet", n.view == "wardrobe"
          and n.tab_btns["settings"].lit and len(n.outfits) == 13 and n.outfits["none"].on and n.outfit_name.text() == "Nothing",
          f"{n.view} {n.outfit_name.text()}")
    n.outfits["crown"].click()
    app.processEvents()
    check("an outfit picked: on the pet now, its stars, its name; kept with the pet", n.p["wear"] == "crown" and n.art.wearing() == "crown"
          and n.win.store["pet"]["wear"] == "crown" and n.outfits["crown"].on and n.outfit_name.text() == "Crown"
          and time.monotonic() - n.dressed_at < 2, f"{n.p['wear']} {n.outfit_name.text()}")
    img = QImage(80, 80, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    n.art.draw(painter, QRectF(0, 0, 80, 80), 1.0, "idle")
    painter.end()
    gold = sum(1 for x in range(0, 80, 2) for y in range(0, 40, 2) if abs(img.pixelColor(x, y).red() - 245) < 20
               and abs(img.pixelColor(x, y).green() - 197) < 25 and img.pixelColor(x, y).blue() < 110)
    check("…drawn on its head (the crown's gold above its face), in place of Blu's own beret", gold > 10, str(gold))
    n.outfits["auto"].click()
    app.processEvents()
    import datetime as _dt
    season = wear_now("auto")
    check("Auto: the season's outfit, said by name", n.p["wear"] == "auto" and n.outfit_name.text().startswith("Auto · ")
          and (season == "none" or WEAR[season] in n.outfit_name.text()), n.outfit_name.text())
    n.set_prefs({**n.p, "kind": "witch", "wear": "none"})
    n._fill_wardrobe()
    n.state.result = None
    n.dress("santa")
    check("the witch keeps her own hat: a hat isn't put on it, and it says why", n.p["wear"] == "none" and not n.outfits["santa"].wearable()
          and "own witch hat" in (n.state.result or ("", "", ""))[2])
    n.set_prefs(keep_pet)
    n.win.keep("pet", n.p)
    if base_models is not None:
        core.answers["/api/models"] = base_models
    n.open("home")


def _check_island(check, n, bridge, core, win):
    """The island (2026-10-24, the owner's TwoCode recording; docs/adr/0006) with real presses, holds and drags on the off-screen notch:
    Home's widgets, holding one to arrange them (−, the empty cell's +, the menu in Settings, Add: the recording's own sequence), a drag, a
    resize with no room, the ready-made layouts, Done, the layout kept across a restart; each widget's own source (the Pomodoro timer, its
    tones and breaks, the clipboard with a password manager's private copy, the note typed and shown as it is typed, the music as Windows'
    sessions say, the file in hand); + the file drop again with Settings holding the menu (2026-10-25); Collapse and Power. Nothing touches
    the owner's clipboard, keyboard, media or Quit: _copy, take_keys, the media reader, confirm and quit are stood in for."""
    from PySide6.QtCore import QByteArray, QMimeData
    from PySide6.QtTest import QTest
    keep_confirm, keep_sound, keep_copy = K.confirm, n._sound, n._copy
    sounds, copied = [], []
    n._sound, n._copy = sounds.append, copied.append
    n.media.read = lambda: None  # (never the owner's own media sessions in a check)
    n.state.watch, n.state.asks, n.places, n.state.result, n.state.agent_result = [], {}, [], None, None
    n.state.perms.clear()  # (earlier checks' approval and file: Monday waiting on the owner says so on the AI tile, and + shows the file)
    n.state.tasks.clear()
    n.state.file, n.state.asked_text = None, None
    n.last_task = None  # (an earlier check's "Long build" ended later than the task Core lists below: the newest one shows)
    core.answers["/api/tasks?"] = {"tasks": [{"id": "T7", "title": "Fix the checkout total", "status": "COMPLETED", "project": "webshop",
                                              "finished_at": time.time() - 120}]}
    n.grid.set_layout(I.preset("default"))
    n.keep_island()
    n.close_notch()
    n.open("home")
    bridge.settle()
    QApplication.processEvents()
    g, lay = n.grid, n.grid.layout_
    placed = all(g.tiles[it["w"]].geometry() == g.rect_of(it["x"], it["y"], it["size"]).toRect() for it in lay)
    ai = g.tiles["ai"]
    check("Home is the island's grid: the recording's Default (AI big; Music, Focus / Tray, Settings), each tile at its cell, two rows tall",
          [it["w"] for it in lay] == ["ai", "music", "focus", "tray", "settings"] and placed and g.isVisible()
          and n.height() == TOP_IN + 28 + TAB_GAP + CARD_H["home"] + PAD and n.width() == OPEN_W, f"{n.width()}x{n.height()}")
    check("the AI tile says Monday's last finished task as Core lists it, how it ended, when and where (the recording's \"Last: …  ✓ Done\")",
          ai.line.accessibleName() == "Last: Fix the checkout total" and ai.outcome.text() == "✓ Done" and ai.sub.accessibleName().endswith("· webshop"),
          f"{ai.line.accessibleName()} / {ai.outcome.text()} / {ai.sub.accessibleName()}")
    n.on_event({"type": "TASK_FAILED", "data": {"task": {"id": "T8", "title": "Book the train", "status": "FAILED", "finished_at": time.time()}}})
    check("…and the next one as it ends (an event, not a guess): failed, said so", ai.line.accessibleName() == "Last: Book the train"
          and ai.outcome.text() == "✗ Failed", ai.outcome.text())
    # 2026-10-27: Monday's own tasks, every one a row on the AI tile (island.task_rows): the newest first and bright, a plan's step, what
    # another is on, one waiting for the screen and who has it (Monday.lease's words); the head counts them; all over → the line is back
    for i, (t, act) in enumerate((("Open Spotify and play some jazz", None), ("Find my photos", "mcp__computer__launch_app: Photos"),
                                  ("Write a note", "Waiting for the screen: TASK-R2 is using it")), 1):
        n.on_event({"type": "TASK_STARTED", "data": {"task": {"id": f"TASK-R{i}", "title": t, "status": "QUEUED" if i == 3 else "RUNNING", "current_action": act}}})
    n.on_event({"type": "TASK_STEP", "task_id": "TASK-R1", "data": {"step": {"index": 2, "total": 4, "label": "Play jazz", "status": "running"}}})
    rows = [lb.accessibleName() for lb in ai.rows if not lb.isHidden()]
    check("Monday's own tasks, every one a row on the AI tile (the newest first, bright): its plan's step, what another is on, one waiting for the "
          "screen and who has it; the head counts them (2026-10-27)", rows == ["Write a note · waiting for the screen (TASK-R2 is using it)",
          "Find my photos · mcp__computer__launch_app: Photos", "Open Spotify and play some jazz · step 2/4"] and f"color: {TEXT};" in ai.rows[0].styleSheet()
          and _head_of(ai) == "Working · 3 tasks" and ai.line.isHidden() and ai.rows[2].mapTo(ai, QPoint(0, ai.rows[2].height())).y() <= ai.height(), str(rows))
    for i in range(1, 4):
        n.on_event({"type": "TASK_COMPLETED", "data": {"task": {"id": f"TASK-R{i}", "title": "x", "status": "COMPLETED", "finished_at": time.time()}}})
    check("…all over: the rows gone, the line back", all(lb.isHidden() for lb in ai.rows) and not ai.line.isHidden())
    n.state.result = None
    tray = g.tiles["tray"]
    QTest.mouseClick(tray, Qt.MouseButton.LeftButton, pos=tray.rect().center())
    check("a quick click on a tile is its own: Tray opens the drop card (the + tab's), nothing wiggles", n.view == "drop" and not g.editing
          and n.tab_btns["drop"].lit, n.view)
    n.show_view("home")
    # hold a widget: everything wiggles (the recording's 3.5 s)
    tray = g.tiles["tray"]
    QTest.mousePress(tray, Qt.MouseButton.LeftButton, pos=tray.rect().center())
    QTest.qWait(Q.HOLD_MS + 120)
    QTest.mouseRelease(tray, Qt.MouseButton.LeftButton, pos=tray.rect().center())
    QApplication.processEvents()
    check("a tile held: edit mode (Done by the tabs, \"Drag to move · new widgets in Settings\"; + still the file drop), nothing opened by "
          "the press", g.editing and g.over.isVisible() and n.done_pill.isVisible() and n.edit_hint.isVisible() and n.tab_btns["drop"].isVisible()
          and "Settings" in n.edit_hint.text() and n.view == "home", f"editing={g.editing} view={n.view}")
    os.environ["MONDAY_REDUCED_MOTION"] = "0"
    a1 = [g.over.angle(k) for k in ("ai", "music", "tray")]
    QTest.qWait(60)
    a2 = [g.over.angle(k) for k in ("ai", "music", "tray")]
    os.environ["MONDAY_REDUCED_MOTION"] = "1"
    check("…they wiggle, each out of step, a degree or so either way (still with reduced motion)", all(abs(a) <= Q.WIGGLE_DEG for a in a1 + a2)
          and len({round(a, 3) for a in a1}) == 3 and a1 != a2 and g.over.angle("ai") == 0.0, f"{a1} {a2}")
    minus = g.over.badges(g.over.at["tray"])[0].toPoint()
    QTest.mouseClick(g.over, Qt.MouseButton.LeftButton, pos=minus)
    stored = (win.store.get(ISLAND_KEY) or {}).get("layout") or []
    check("Tray's −: removed, its cell an empty slot, the layout kept at once (data/desktop.json)", "tray" not in g.tiles
          and I.free(g.layout_) == [(2, 1)] and not any(it["w"] == "tray" for it in stored), str(I.free(g.layout_)))
    QTest.mouseClick(g.over, Qt.MouseButton.LeftButton, pos=g.rect_of(2, 1, "small").center().toPoint())
    check("the empty slot's +: Settings, its widget menu for that cell (the first app not on Home chosen, its preview at the size picked)",
          n.view == "settings" and n.menu.at == (2, 1) and n.menu.pick == "tray" and n.menu.preview is not None and n.menu.isVisible()
          and n.menu.preview.key == "tray" and n.tab_btns["settings"].lit, f"{n.view} {n.menu.at} {n.menu.pick}")
    used = {k for k, b in n.menu.app_rows.items() if b.dot}
    check("the menu marks what is on Home (a lime dot) and the ready-made layout in use (none now: Tray went)", used == {"ai", "music", "focus",
          "settings"} and not any(b.on for b in n.menu.layout_rows.values()), str(sorted(used)))
    QTest.mouseClick(n.menu.app_rows["clock"], Qt.MouseButton.LeftButton)
    word, ok, _why = n.menu.verdict()
    check("Clock picked: its preview, Small chosen, Add ready", n.menu.preview.key == "clock" and n.menu.size_btns["small"].lit and word == "Add" and ok
          and n.menu.add.isEnabled())
    QTest.mouseClick(n.menu.size_btns["big"], Qt.MouseButton.LeftButton)
    check("…Big has no room there: Add is off and says why", not n.menu.add.isEnabled() and "No room for a big Clock" in n.menu.why.text(),
          n.menu.why.text())
    QTest.mouseClick(n.menu.size_btns["small"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(n.menu.add, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    check("Add: back on Home, still arranging, Clock where Tray was (the recording's 10 s)", n.view == "home" and g.editing
          and I.find(g.layout_, "clock") == I.item("clock", "small", 2, 1) and "clock" in g.tiles, str(I.find(g.layout_, "clock")))
    music = g.over.at["music"]
    QTest.mousePress(g.over, Qt.MouseButton.LeftButton, pos=music.center().toPoint())
    QTest.mouseMove(g.over, g.over.at["focus"].center().toPoint())
    QTest.mouseRelease(g.over, Qt.MouseButton.LeftButton, pos=g.over.at["focus"].center().toPoint())
    check("a tile dragged onto another: they swap (Drag to move), kept", I.find(g.layout_, "music") == I.item("music", "small", 3, 0)
          and I.find(g.layout_, "focus") == I.item("focus", "small", 2, 0)
          and I.find((win.store.get(ISLAND_KEY) or {}).get("layout") or [], "music") == I.item("music", "small", 3, 0), str(g.layout_))
    before = [dict(it) for it in g.layout_]
    QTest.mouseClick(g.over, Qt.MouseButton.LeftButton, pos=g.over.badges(g.over.at["music"])[1].toPoint())
    check("the resize badge with no room on a full grid: nothing moves, and it says why", g.layout_ == before
          and "No room to make Music another size" in (n.state.result or ("", "", ""))[2], str((n.state.result or ())[:3]))
    n.state.result = None
    n.done_pill.click()
    check("Done: arranging over", not g.editing and not g.over.isVisible() and n.tab_btns["drop"].isVisible() and not n.done_pill.isVisible())
    for k in ("small8", "big2", "wide4", "music", "default"):
        n.show_view("settings")
        QTest.mouseClick(n.menu.layout_rows[k], Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        if g.layout_ != I.preset(k) or not g.editing or n.view != "home" or set(g.tiles) != I.in_use(I.preset(k)):
            break
    check("each ready-made layout in Settings (Default, 8 small, 2 big, 4 wide, Music): on Home at once, arranging, its tiles made",
          k == "default" and g.layout_ == I.preset("default") and g.editing, k)
    n.show_view("settings")
    check("…and the menu marks the one in use", n.menu.layout_rows["default"].on and not n.menu.layout_rows["music"].on)
    n.show_view("home")
    g.change(I.preset("small8"))
    g.edit(False)
    n2 = Notch(win, show_on_start=False)
    same = n2.grid.layout_ == g.layout_
    bridge.event.disconnect(n2.on_event)
    bridge.online.disconnect(n2._online)
    n2._watch_clip(False)
    n2.media.watch(False)
    n2.deleteLater()
    check("the layout comes back after a restart (data/desktop.json)", same, str([it["w"] for it in g.layout_]))
    # the Pomodoro timer (the owner, 2026-10-25): a tap starts and pauses a pomodoro; its end rings once (counted) and starts its break,
    # even with the notch closed; a tap on a break skips it; a break's end rings too
    focus = g.tiles["focus"]
    QTest.mouseClick(focus, Qt.MouseButton.LeftButton, pos=focus.rect().center())
    running = I.focus_now(n.island.get("focus"))["phase"] == "running" and n._focus_t.isActive() and "ends" in win.store[ISLAND_KEY]["focus"]
    QTest.mouseClick(focus, Qt.MouseButton.LeftButton, pos=focus.rect().center())
    paused = I.focus_now(n.island.get("focus"))["phase"] == "paused" and not n._focus_t.isActive()
    check("Pomodoro: a tap starts its 25 minutes (kept, its end armed), another pauses it", running and paused
          and focus.line.accessibleName() == "Paused · 1/4", focus.line.accessibleName())
    n.island["focus"] = {"kind": "focus", "ends": time.time() - 1}
    n.close_notch()
    n._focus_due()
    st = I.focus_now(n.island.get("focus"))
    check("…a pomodoro's end: the tone, \"Pomodoro done\" (the closed bar says it too), counted once, its 5-minute break running (the ring "
          "teal), its end armed", "done" in sounds and n.state.result[1] == "Pomodoro done" and st["today"] == 1 and st["kind"] == "short"
          and st["phase"] == "running" and n._focus_t.isActive() and focus.ring.colour == Q.BREAK_FILL
          and focus.line.accessibleName() == "Short break" and win.store[ISLAND_KEY]["focus"].get("count") == 1, str(n.state.result[:3]))
    n._focus_due()
    check("…never counted twice", I.focus_now(n.island.get("focus"))["today"] == 1)
    n.state.result = None
    n.open("home")
    bridge.settle()
    QTest.mouseClick(focus, Qt.MouseButton.LeftButton, pos=focus.rect().center())
    st = I.focus_now(n.island.get("focus"))
    check("…a tap on its break skips it: the next pomodoro ready (2 of 4), today's count kept", st["kind"] == "focus" and st["phase"] == "ready"
          and st["today"] == 1 and focus.line.accessibleName() == "Pomodoro 2/4" and focus.left.text() == "25:00", focus.line.accessibleName())
    n.island["focus"] = {**n.island["focus"], "kind": "short", "ends": time.time() - 1}
    n._focus_due()
    check("…a break's end rings too and says the next pomodoro is ready", sounds[-1] == "prompt" and n.state.result[1] == "Break over"
          and I.focus_now(n.island.get("focus"))["kind"] == "focus", str(n.state.result[:3]))
    n.state.result = None
    # the clipboard: watched from the start wherever its widget is (the owner, 2026-10-26), a password manager's copy never read
    mine_ = [dict(it) for it in g.layout_]
    g.set_layout([I.item("ai", "big", 0, 0)])  # (no Clipboard widget on Home: still watched)
    watched = n._clip_on and not n.win.persist and n.clips is not None  # (a check's notch keeps no file of the owner's)
    g.set_layout(mine_)
    n._watch_clip(False)  # (the copies below are handed to it: the owner's own copies stay out of a check)
    m = QMimeData()
    m.setText("npm run build")
    n._on_clip(m)
    private = QMimeData()
    private.setText("hunter2-secret")
    private.setData('application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"', QByteArray(b"\x00"))
    n._on_clip(private)
    flagged = QMimeData()
    flagged.setText("another-secret")
    flagged.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', QByteArray(b"\x00\x00\x00\x00"))
    n._on_clip(flagged)
    check("Clipboard: every copy is watched for, its widget on Home or not (the owner, 2026-10-26: \"all the things that ever copied until "
          "shutdown\"); a copy shows on its tile; a copy a password manager marks private (either of Windows' signs) is never kept",
          [c["text"] for c in n.clips] == ["npm run build"] and g.tiles["clipboard"].line.accessibleName() == "npm run build" and watched,
          str([c["text"] for c in n.clips]))
    m2 = QMimeData()
    m2.setText("https://example.com/invoice")
    n._on_clip(m2)
    QTest.mouseClick(g.tiles["clipboard"], Qt.MouseButton.LeftButton, pos=g.tiles["clipboard"].rect().center())
    rows = n.clip_view.findChildren(Q.ClipRow)
    rows[-1].click() if rows else None
    check("…its view lists them newest first; a click copies one back (to the top)", n.view == "clip" and [r.item["text"] for r in rows]
          == ["https://example.com/invoice", "npm run build"] and copied == ["npm run build"] and n.clips[0]["text"] == "npm run build")
    n.clip_view.forget.click()
    check("…Forget them: none kept", not n.clips and n.clip_view.empty.isVisible())
    # the note (the owner, 2026-10-25: "if I'm writing something then it should be seen there"): opened, it takes the keyboard (the
    # notch takes none by itself: their first note went to the app in front); what is typed shows on its tile at once and is kept
    took = []
    n.take_keys, n.give_keys = (lambda box=None: took.append(box)), (lambda: None)  # (a check never moves the owner's keyboard focus)
    n.show_view("home")
    QTest.mouseClick(g.tiles["note"], Qt.MouseButton.LeftButton, pos=g.tiles["note"].rect().center())
    QApplication.processEvents()
    in_note, no_keys_offscreen = n.view == "note", not took
    n._note_keys()  # (what opening it on the screen does)
    keys = took == [n.note_view.box]
    box = n.note_view.box
    QTest.keyClicks(box, "Call the bank")
    live = g.tiles["note"].text.text() == "Call the bank" and (win.store[ISLAND_KEY].get("note") or "") != "Call the bank"
    QTest.qWait(Q.NOTE_SAVE_MS + 200)
    kept = win.store[ISLAND_KEY].get("note") == "Call the bank"
    QTest.keyClick(box, Qt.Key.Key_Return)
    QTest.keyClicks(box, "Buy milk")
    n.show_view("home")  # (leaving it keeps it at once)
    del n.take_keys, n.give_keys
    check("Note: its tile opens it with the keyboard (never in an off-screen picture); typed words show on the tile as they are typed, kept "
          f"{Q.NOTE_SAVE_MS} ms after the last key, and at once on leaving; the tile shows the words themselves, wrapped", in_note
          and no_keys_offscreen and keys and live and kept and win.store[ISLAND_KEY].get("note") == "Call the bank\nBuy milk"
          and g.tiles["note"].text.text() == "Call the bank\nBuy milk" and g.tiles["note"].text.isVisible(),
          f"{in_note} {no_keys_offscreen} {keys} {live} {kept} {win.store[ISLAND_KEY].get('note')!r} {g.tiles['note'].text.text()!r}")
    # the music: what Windows' sessions say, nothing else
    g.change(I.preset("music"))
    mt = g.tiles["music"]
    n.media._got({"ok": False})
    unread = mt.line.accessibleName()
    n.media._got({"ok": True, "now": None})
    nothing = (mt.title.accessibleName(), mt.line.accessibleName(), mt.disc.isHidden())
    n.media._got({"ok": True, "now": {"app": "Spotify.exe", "name": "Spotify", "status": "playing", "title": "Late Night Commits", "artist": "Lofi Robo"},
                  "pos": (30.0, 120.0, time.time())})
    check("Music: can't be read here, said; nothing playing, said and no controls; a track: its title, artist, Pause, where in it it is",
          ("N/A" in unread or "Not on this OS" in unread) and nothing == ("Music", "Nothing playing", True)
          and mt.title.accessibleName() == "Late Night Commits" and mt.line.accessibleName() == "Lofi Robo" and not mt.disc.isHidden()
          and mt.disc.accessibleName() == "Pause" and mt.prog.isVisible() and 0.2 < mt.prog.part < 0.3, f"{unread} {nothing}")
    n.media._controlled({"ok": False, "text": "I asked Spotify to pause, but Windows still shows it playing."})
    check("…a control that didn't happen is said on the tile, not shown as done", "still shows it playing" in mt.line.accessibleName(),
          mt.line.accessibleName())
    n.media.said = None
    # + is the file drop again (the owner, 2026-10-25: "the + should be reverted"), and the widget menu lives in Settings, under all that
    # Settings had
    n.close_notch()
    n.open("home")
    n.tab_btns["drop"].click()
    plus = n.view == "drop" and n.tab_btns["drop"].lit and n.tab_btns["drop"].accessibleName() == "Drop a file" and n.zone.isVisible()
    n.gear.click()
    QApplication.processEvents()
    has = n.view == "settings" and all(w.isVisible() for w in (n.sw, n.vol, n.autoclose, n.cc_words, n.api_words, n.menu))
    tall = n.height() == TOP_IN + 28 + TAB_GAP + CARD_H["settings"] + SETTINGS_GAP + n.menu.height_for() + PAD
    check("+ is the file drop again, as before; Settings keeps all it had (sound, volume, auto-close, the hooks' and Core's lines) with the "
          "widget menu under it, the notch tall enough for both", plus and has and tall and not hasattr(n, "to_widgets")
          and not hasattr(n.menu, "files"), f"{plus} {has} {tall} {n.height()}")
    # Collapse and Power (the recording's top right)
    n.show_view("home")
    no_power = n.power.isHidden()
    quits = []
    n.set_quit(lambda: quits.append(1))
    K.confirm = lambda *a, **k: False
    n.power.click()
    said_no = not quits
    K.confirm = lambda *a, **k: True
    n.power.click()
    K.confirm = keep_confirm
    check("Power: there only once the app gives it Quit Monday; it asks first, and No quits nothing", no_power and n.power.isVisible() and said_no
          and quits == [1])
    from PySide6.QtWidgets import QSystemTrayIcon
    tray_menu, hits = QMenu(), []
    tray_menu.addAction("Open Monday", lambda: hits.append("open"))
    tray_menu.addAction("Quit Monday", lambda: hits.append("quit"))
    tray = QSystemTrayIcon(QApplication.instance())  # (as core/desktop.py makes it: the app's child, its menu's own Quit Monday)
    tray.setContextMenu(tray_menu)
    found = n._tray_quit()
    if found is not None:
        found()
    tray.setContextMenu(None)
    tray.deleteLater()
    check("…the tray's own Quit Monday, found in this app (no tray: none, so no button)", hits == ["quit"] and Notch._tray_quit is not None,
          str(hits))
    n.set_quit(None)
    n.collapse.click()
    check("⌃ folds the whole notch to its line in one click (the owner, 2026-10-26: \"collapse the whole notch only\"), kept so",
          not n.opened and n.lined and n.p["lined"], f"opened={n.opened} lined={n.lined}")
    n.line(False)
    n._sound, n._copy = keep_sound, keep_copy
    del n.media.read
    n.grid.set_layout(I.preset("default"))
    n.grid.set_layout([I.item("ai", "wide", 0, 0)])  # (the AI tile made anew: its old pet deleted once the loop runs)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    try:
        n._tick()
        ticked = "ok"
    except RuntimeError as e:
        ticked = str(e)
    check("a layout that replaces a tile leaves no deleted pet for the frames to move (desktop.log 2026-10-26: \"PetView already deleted\")",
          ticked == "ok" and n.grid.tiles["ai"].pet in n._pets, f"{ticked}; {len(n._pets)} pets moved")
    n.grid.set_layout(I.preset("default"))
    n.keep_island()


def _check_looks(check, n, bridge, core, win):
    """The three looks (the owner's brief of 2026-10-29; docs/adr/0011): one notch whose outline, closed face, header and Home change in
    place. Switching keeps the chat, the running task and the approval waiting (no Core call stops anything), each look keeps its own
    layout and place, a reset puts back one look only, the look comes back after a restart, and the limits look shows every AI's cards
    (stand-in readings: never the network) with its closed bar, the owner's order and hidden cards, the nudges and each source's wait."""
    from PySide6.QtCore import QRect
    n.media.read = lambda: None  # (never the owner's own media sessions in a check)
    keep_read = n._read_source
    n._read_source = lambda name, ctx=None: None  # (the stand-in readings below are the only ones: no read of this PC's sources races them)
    end = time.monotonic() + 15
    while n._reading and time.monotonic() < end:  # (a read the checks before started: its answer in first, not over the stand-ins)
        QApplication.processEvents()
        time.sleep(0.02)
    n.state.watch, n.state.asks, n.state.result, n.state.agent_result = [], {}, None, None
    n.state.perms.clear()
    n.state.tasks.clear()
    n._finished, n._plan_card_at, n.update_offer = None, None, None  # (no card from the checks before: Home shows the look's own Home)
    n.island.pop("focus", None)
    n.media.now, n.media.cover = None, None
    n.close_notch()
    for _ in range(60):
        n._step()
    check("looks: classic with nothing stored, as before 2026-10-29", n.mode == "classic" and I.UI_KEY not in win.store
          and n.grid.dims == I.GRID and not n.head.isVisible())
    classic = [dict(it) for it in n.grid.layout_]
    # what must survive every switch: the chat, a running task, an approval waiting (their objects, not copies)
    n.state.tasks["T9"] = {"id": "T9", "title": "Fix the checkout total", "status": "RUNNING", "created_at": time.time() - 83}
    msgs, talk, tasks = n._messages, n.talk, n.state.tasks
    calls = len(core.calls)
    n.set_mode("island")
    for _ in range(80):
        n._step()
    a = n.screen_area()
    check("island: hangs from the top edge as the recording's does, wider with something running", n.mode == "island" and n.hanging()
          and int(n.top) == a.top() and n.height() == ISLAND_LIVE[1] and n.width() >= ISLAND_LIVE[0] + 2 * L.EAR, f"{n.geometry()}")
    check("island: the running task's time on the pill, from Core's own start (nothing invented)", L.elapsed(n).startswith("1:2"),
          L.elapsed(n))
    check("island: the running task, the chat and its messages are the same objects after the switch", n.state.tasks is tasks
          and "T9" in n.state.tasks and n._messages is msgs and n.talk is talk)
    stopped = [c for c in core.calls[calls:] if c[0] == "POST" and any(w in c[1] for w in ("/cancel", "/stop", "/shutdown", "/pause"))]
    check("island: switching asked Core to stop nothing", not stopped, str(stopped))
    n.state.tasks.pop("T9")
    n.refresh()
    for _ in range(80):
        n._step()
    check("island: at rest, Monday's pet in its middle, its flared corners outside its body", (n.width(), n.height())
          == (ISLAND_CLOSED[0] + 2 * L.EAR, ISLAND_CLOSED[1]), f"{n.width()}x{n.height()}")
    n.island["focus"] = I.focus_start({}, time.time() - 60)
    n.media.now, n.media.cover = {"app": "x", "status": "playing", "title": "Test Track", "artist": "Test Artist"}, None
    n.refresh()
    for _ in range(80):
        n._step()
    check("island: a bubble each for the running pomodoro and the music (three things at once)",
          [b[0] for b in L.bubbles(n)] == ["focus", "music"] and n.width() == ISLAND_CLOSED[0] + 2 * L.EAR + 2 * (ISLAND_CLOSED[1] + L.BUBBLE_GAP),
          f"{n.width()}")
    img = n.grab().toImage()
    dpr = img.devicePixelRatio()  # (the picture is in the screen's own pixels: 125 % here)
    pw = ISLAND_CLOSED[0] + 2 * L.EAR
    mid = img.pixelColor(int(pw / 2 * dpr), int(ISLAND_CLOSED[1] / 2 * dpr))
    gap = img.pixelColor(int((pw + L.BUBBLE_GAP / 2) * dpr), int(ISLAND_CLOSED[1] / 2 * dpr))
    check("island: the pill drawn, the gap before the bubbles left clear", mid.alpha() > 0 and gap.alpha() == 0,
          f"{mid.name()} a{mid.alpha()} / {gap.name()} a{gap.alpha()}")
    n.island.pop("focus", None)
    n.media.now = None
    n.open("home")
    for _ in range(80):
        n._step()
    v = n.body_lay
    check("island: Home is its own row of three tiles (AI, Music, Focus), its header on, its own tabs under the views",
          n.grid.dims == I.ISLAND_GRID and [it["w"] for it in n.grid.layout_] == ["ai", "music", "focus"] and n.head.isVisible()
          and n.island_tabs.isVisible() and not n.tabs_box.isVisible() and v.indexOf(n.island_tabs) == v.count() - 1
          and n.grid.height() == ISLAND_CELL_H)
    # its tabs and its own pages (the fidelity pass, 2026-10-29)
    shown_at = {}
    for key in ("music", "focus", "drop", "clip", "note", "chat", "home"):
        n.island_tabs.go(key)
        for _ in range(80):
            n._step()
        shown_at[key] = n.view
    check("island: each tab shows its page", shown_at == {"music": "music", "focus": "focus", "drop": "drop", "clip": "clip",
          "note": "note", "chat": "chat", "home": "home"}, str(shown_at))
    n.media.now, n.media.cover, n.media.ok, n.media.pos = None, None, True, None
    n.show_view("music")
    check("island: Music with nothing playing says so, its controls off (nothing made up)", n.music_view.title.accessibleName() != ""
          and "Nothing playing" in n.music_view.accessibleName() and not n.music_view.play.isEnabled())
    n.media.now = {"app": "Spotify.exe", "name": "Spotify", "status": "playing", "title": "Test Track", "artist": "Test Artist"}
    n.media.pos = (30.0, 200.0, time.time(), True)
    n.music_view.fill()
    check("island: Music playing: the track, its player, play turns to pause", "Test Track" in n.music_view.accessibleName()
          and n.music_view.app.text() == "Spotify" and n.music_view.play.icon_name == "pause" and n.music_view.play.isEnabled())
    n.media.now, n.media.pos = None, None
    # the island's Chat chips: the latest copy asked about through the chat; nothing copied, nothing sent
    sent, keep_send = [], n.send
    n.send = lambda: sent.append(n.prompt.toPlainText())
    keep_clips, n.clips = n.clips, []
    n.show_view("chat")
    n.chip_ask("translate")
    check("island Chat: a chip with nothing copied says so and sends nothing", sent == [] and n.chat_chips.isVisible()
          and n.state.result and "Copy some text first" in n.state.result[2])
    n.state.result = None  # (its line goes, as it would after its 8 s: the checks after this one measure the notch at rest)
    n.clips = [{"text": "Bonjour tout le monde", "at": time.time()}]
    n.chip_ask("translate")
    check("island Chat: a chip asks Monday about the latest copy, through the chat", len(sent) == 1 and sent[0].startswith("Translate this")
          and sent[0].endswith("Bonjour tout le monde"))
    n.send, n.clips = keep_send, keep_clips
    n.prompt.clear()
    n.island.pop("focus", None)
    n.show_view("focus")
    fv = n.focus_view
    check("island: Focus ready: Reset, 0 hr 25 min, Start, the lengths with 25 picked", fv.reset.isVisible() and fv.field.isVisible()
          and not fv.stop.isVisible() and fv.go.text().endswith("Start") and fv.field.minutes == 25 and fv.lengths.value == "1500")
    fv.lengths._pick(3)
    check("island: Focus 50 min picked: kept with the timer, shown at once", n.island["focus"].get("length") == 3000 and fv.field.minutes == 50)
    fv.go.click()
    check("island: Focus started: Stop, +5 min, Pause, the time left big, when it will be done", I.focus_now(n.island["focus"])["phase"]
          == "running" and fv.stop.isVisible() and fv.more.isVisible() and fv.go.text().endswith("Pause")
          and (fv.big.text().startswith("49:") or fv.big.text().startswith("50:")), fv.big.text())
    before = n.island["focus"]["ends"]
    fv.more.click()
    check("island: Focus +5 min moves its end", round(n.island["focus"]["ends"] - before) == I.MORE_S)
    fv.stop.click()
    check("island: Focus Stop: ready again at its picked length", I.focus_now(n.island["focus"])["phase"] == "ready" and fv.field.minutes == 50)
    n.island.pop("focus", None)
    n.show_view("home")
    for _ in range(80):
        n._step()
    n.grid.change(I.remove(n.grid.layout_, "focus"))
    check("island: its own layout kept apart (classic's untouched)", [it["w"] for it in win.store[I.UI_KEY]["island"]["layout"]] == ["ai", "music"]
          and I.normal((win.store.get(ISLAND_KEY) or {}).get("layout")) == classic)
    n.set_mode("limits")
    for _ in range(80):
        n._step()
    now = time.time()
    cards = [U.card("codex", "ok", [U.window("5 hours", 86, resets_at=now + 4800), U.window("This week", 41, resets_at=now + 86400 * 3)],
                    source="Codex session log", at=now - 120, plan="Plus", now=now),
             U.card("openrouter", "ok", [U.window("Free models today", count=(3, 50))], source="OpenRouter /api/v1/key", at=now, plan="Free", now=now),
             U.card("claude_code", "unsupported", note="Its plan use shows only inside Claude Code", now=now),
             U.card("ollama", "local_ok", note="5 on this PC", at=now, now=now)]
    toasts, keep_toast, keep_notify = [], n.toast, n.p.get("notify")
    n.toast = lambda title, words: toasts.append((title, words))
    n.p["notify"] = True
    n._usage_asked = now
    n._got_usage(cards)
    check("limits: a window past 80% is a nudge, once", len(toasts) == 1 and "Codex" in toasts[0][0] and "86%" in toasts[0][1], str(toasts))
    n._got_usage(cards)
    check("...not again for the same reading", len(toasts) == 1)
    n.toast, n.p["notify"] = keep_toast, keep_notify
    n.open("home")
    for _ in range(80):
        n._step()
    shown = [c.c["key"] for c in n.limits_view.inner.findChildren(L.UsageCard) if c.isVisible()]
    check("limits: Home is every AI's card (the grid hidden), in usage.ORDER", n.limits_view.isVisible() and not n.grid.isVisible()
          and shown == ["claude_code", "codex", "openrouter", "ollama"], str(shown))
    check("limits: its own row of icons over the cards (the recording's), the limits page lit, no classic tabs", n.limit_tabs.isVisible()
          and not n.tabs_box.isVisible() and n.limit_tabs.current() == "home" and n.limit_tabs.compact)
    codex = next(c for c in n.limits_view.inner.findChildren(L.UsageCard) if c.c["key"] == "codex" and c.isVisible())
    check("limits: a card says its windows, their % and resets, and how old the reading is", "5 hours: 86%" in codex.accessibleName()
          and "Resets " in codex.accessibleName() and "read 2 min ago" in codex.accessibleName(), codex.accessibleName())
    n.limits_layout(hide="ollama")
    n.limits_layout(move=("openrouter", -1))
    shown = [c.c["key"] for c in n.limits_view.inner.findChildren(L.UsageCard) if c.isVisible()]
    check("limits: the owner's order and hidden cards, kept with the look", shown == ["claude_code", "openrouter", "codex"]
          and win.store[I.UI_KEY]["limits"]["hidden"] == ["ollama"], str(shown))
    n.close_notch()
    for _ in range(80):
        n._step()
    check("limits: closed, the busiest limit's ring and % (86) at the ends", (n.width(), n.height()) == LIMITS_CLOSED
          and U.busiest(n.usage)[2] == 86 and n.hanging())
    n._got_usage([c for c in cards if c["key"] in ("claude_code", "ollama")])
    n.refresh()
    for _ in range(80):
        n._step()
    check("limits: no card with a number, the classic face (no % made up)", (n.width(), n.height()) == CLOSED)
    failed_card = U.card("openrouter", "unavailable", note="couldn't reach OpenRouter (OSError)", now=time.time() + 1)
    n._usage_asked = time.time()
    n._got_usage([failed_card])
    check("limits: a source that failed waits longer before it is asked again", n._usage_fails.get("openrouter") == 1
          and "openrouter" not in n._usage_due() and n._usage_next["openrouter"] - time.time() > U.EVERY_S * 1.5)
    n._usage_fails.clear()
    n._usage_next.clear()
    n.open("settings")
    for _ in range(80):
        n._step()
    check("limits: Settings without the widget menu (no grid on its Home)", not n.menu.isVisible())
    # an approval waiting: its card on Home in every look
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "q90", "agent": "Claude Code", "what": "run a command", "detail": "npm test"}}})
    seen = {}
    for mode in ("limits", "island", "classic"):
        n.set_mode(mode)
        n.open("home")
        for _ in range(80):
            n._step()
        seen[mode] = n.home_card.isVisible() and not n.grid.isVisible() and not n.limits_view.isVisible() and "q90" in n.state.asks
    check("an approval waiting keeps its card through every switch", all(seen.values()), str(seen))
    n.state.asks.clear()
    n.refresh()
    # back to classic: as it was
    for _ in range(80):
        n._step()
    check("classic again: its own grid and layout exactly as before, its tabs on top, no header and no island or limits row", n.grid.dims == I.GRID
          and n.grid.layout_ == classic and n.tabs_box.isVisible() and not n.head.isVisible() and not n.island_tabs.isVisible()
          and not n.limit_tabs.isVisible() and not n.chat_chips.isVisible())
    kids = len(n.findChildren(QWidget))
    for _ in range(3):
        for mode in ("island", "limits", "classic"):
            n.set_mode(mode)
    app_ = QApplication.instance()
    app_.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    check("switching round the looks again and again leaves no widgets behind", len(n.findChildren(QWidget)) <= kids + 2,
          f"{kids} -> {len(n.findChildren(QWidget))}")
    # the look picked in the notch's own settings with the notch folded to its line (the owner, 2026-10-29: "how can i change the ui?")
    n.set_mode("classic")
    n.close_notch()
    n.lined = True
    n.p = prefs({**n.p, "lined": True})
    n.open("settings")
    n.look_seg._pick(list(I.MODES).index("island"))
    check("the notch's own settings: Look switches it at once, and a folded notch unfolds to show it", n.mode == "island"
          and not n.lined and win.store["pet"]["lined"] is False and n.look_seg.value == "island", f"{n.mode} lined={n.lined}")
    n.set_mode("limits")
    check("...and its Look follows a switch made elsewhere (Settings, the right-click menu)", n.look_seg.value == "limits")
    # a restart: the look and each look's layout come back
    n.set_mode("island")
    n2 = Notch(win, show_on_start=False)
    n2.media.read = lambda: None
    back = n2.mode == "island" and [it["w"] for it in n2.grid.layout_] == ["ai", "music"] and n2.grid.dims == I.ISLAND_GRID
    n2._usage_t.stop()
    n2.deleteLater()
    check("the look chosen and its layout come back after a restart", back)
    # a reset puts back one look only
    n.reset_mode("island")
    check("reset: the island's own three tiles again, the limits look's order and hidden cards untouched",
          [it["w"] for it in n.grid.layout_] == ["ai", "music", "focus"] and win.store[I.UI_KEY]["limits"]["hidden"] == ["ollama"])
    n.reset_mode("limits")
    check("reset: the limits look's order and hidden cards cleared", win.store[I.UI_KEY]["limits"] == {"order": [], "hidden": [], "at": None})
    n.set_mode("classic")
    n.close_notch()
    for _ in range(80):
        n._step()
    n.usage = None
    win.store.pop(I.UI_KEY, None)
    n._read_source = keep_read
    n._reading.discard("usage")
    check("classic at rest again: the recording's slim bar, hanging", (n.width(), n.height()) == CLOSED and n.hanging(), f"{n.geometry()}")


def _check_start(check, n, bridge, core, win):
    """The start (docs/adr/0011; the recordings: eyes opening, a happy ^ ^ once ready): the eyes closed until Core's own handshake
    (GET /api/hello) says ready or degraded, never a greeting while it says starting or doesn't answer; a failed Core ends the waiting
    and the bar says offline; the floating island fades in where it floats, its pill growing from the eyes to its width."""
    n.state.watch, n.state.asks, n.state.result, n.state.tasks = [], {}, None, {}
    n.state.perms.clear()
    n.state.online = None
    n.booting, n._boot_t0, n._greet_until = True, time.monotonic(), 0.0
    n.refresh()
    check("start: Core not ready yet, the pet's eyes closed (not asleep: no z's)", n.mood == "flat" and L.mood_of(n) == "sleepy", n.mood)
    asked = len([c for c in core.calls if c[1].startswith("/api/hello")])
    n._online("ONLINE")
    bridge.settle(20)
    check("start: the stream up, Core's handshake asked (GET /api/hello)", len([c for c in core.calls if c[1].startswith("/api/hello")]) > asked)
    n._got_hello({"state": "starting"}, None)
    check("start: Core answering but still starting: no greeting, the eyes still closed", n.booting and n.mood == "flat")
    n._got_hello(None, "Monday Core is unreachable.")
    check("start: no answer to the handshake: still waiting, never 'ready'", n.booting)
    n._got_hello({"state": "degraded", "missing": ["jev"]}, None)
    check("start: degraded (an optional part missing) doesn't hold the start up: the greeting", not n.booting and n.mood == "happy")
    n._greet_until = time.monotonic() - 1
    n.refresh()
    check("start: after the greeting, the pet at rest", n.mood == STATES["idle"][0])
    n.booting, n._boot_t0 = True, time.monotonic()
    n._got_hello({"state": "ready"}, None)
    check("start: ready: the greeting once", not n.booting and n.mood == "happy")
    n._greet_until = 0.0
    n.booting, n._boot_t0 = True, time.monotonic()
    n.core_phase = "failed"
    n._online("OFFLINE")
    check("start: Core failed to start: no more waiting, the bar says offline (no greeting)", not n.booting and n.shown[0] == "offline"
          and n.mood == "offline", f"{n.shown} {n.mood}")
    n.core_phase = None
    n._online("ONLINE")
    bridge.settle(20)
    n.booting, n._boot_t0 = True, time.monotonic() - BOOT_MAX_S - 1
    n._got_hello({"state": "starting"}, None)
    check("start: Core starting past BOOT_MAX_S: the waiting ends, still no greeting", not n.booting and n.mood != "happy")
    # the floating island's arrival, with motion on
    keep_motion = os.environ.get("MONDAY_REDUCED_MOTION")
    os.environ["MONDAY_REDUCED_MOTION"] = "0"
    if not reduced_motion():
        n.set_mode("island")
        n.close_notch()
        n.w_spring, n.h_spring = Spring(float(n.width())), Spring(float(n.height()))
        n._anim.stop()
        n._arrive()
        first = (n.width(), round(n.windowOpacity(), 2))
        end = time.monotonic() + 1.5
        while time.monotonic() < end and (n._anim.isActive() or n.windowOpacity() < 0.99):
            QApplication.processEvents()
            time.sleep(0.01)
        w, h = n.target_size()
        check("start: the floating island fades in, its pill growing from the eyes to its width", first[0] == h and first[1] < 0.5
              and n.width() == w and n.windowOpacity() > 0.99, f"{first} -> {n.width()} {n.windowOpacity():.2f}")
        n.set_mode("classic")
        end = time.monotonic() + 1.0
        while time.monotonic() < end and n.windowOpacity() < 0.99:
            QApplication.processEvents()
            time.sleep(0.01)
    else:
        check("start: the floating island's arrival (this PC has animations off: nothing moves)", True)
    if keep_motion is None:
        os.environ.pop("MONDAY_REDUCED_MOTION", None)
    else:
        os.environ["MONDAY_REDUCED_MOTION"] = keep_motion
    n.setWindowOpacity(1.0)
    n.booting = False
    n.close_notch()
    for _ in range(80):
        n._step()


def _check():
    os.environ["MONDAY_REDUCED_MOTION"] = "1"  # sizes checked where they land (the spring and the curve: python -m core.notch --check)
    app = K.make_app(["monday-notch"])
    ok = True

    def no_network(url, headers):  # (a check never asks OpenRouter or the owner's Ollama: the usage reads say they couldn't reach it)
        raise OSError("no network in a check")
    U.http_get = no_network

    def check(name, cond, detail=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        ok = ok and bool(cond)

    win, bridge, core = _stub_window({"/api/agents/hooks": {"approvals": True, "stop": True, "pre": False, "mcp": False},
                                      "/api/ai-tools": {"tools": [
                                          {"key": "claude_code", "name": "Claude Code", "kind": "coding agent", "installed": True,
                                           "status": "not_authenticated"},
                                          {"key": "cursor", "name": "Cursor", "kind": "editor", "installed": True, "status": "detected_unsupported"},
                                          {"key": "hermes", "name": "Hermes Agent", "kind": "computer agent", "installed": True, "status": "available"},
                                          {"key": "kiro", "name": "Kiro", "kind": "editor", "installed": True, "status": "available"},
                                          {"key": "chatgpt", "name": "ChatGPT", "kind": "app", "installed": False, "status": "unavailable"}]},
                                      "/api/messages": {"messages": [{"id": 1, "role": "user", "kind": "text", "text": "hello"},
                                                                     {"id": 2, "role": "monday", "kind": "text", "text": "Hello! **What** can I do?"}]},
                                      "/api/models": {"selected": "auto", "effective": "gpt-oss", "models": [
                                          {"id": "auto", "name": "Automatic", "available": True, "selected": True}]},
                                      "/api/voice": {"settings": {"tts_provider": "custom"}, "providers": [
                                          {"id": "custom", "label": "Your voice (cloned from your recording, on this PC)", "status": "available"},
                                          {"id": "google_free", "label": "Google", "status": "available"}]},
                                      "/api/command": {"message": {"id": 7, "role": "user"}}, "/api/agents/ask/": {"ok": True},
                                      "/api/permissions/": {"id": 5}, "/api/tasks/": {}})
    n = Notch(win, show_on_start=False)
    n._front_key = lambda: None  # (no app of the owner's screen in front in a check, unless one says so)
    n.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    n.show()
    app.processEvents()
    check("closed at rest: the recording's slim bar, hanging from the top", (n.width(), n.height()) == CLOSED and n.hanging(),
          f"{n.width()}x{n.height()}")
    n._arrive()  # the start (2026-10-21): down from above the edge, then settled exactly where it hangs (motion off: simply there)
    came = (n._arriving and n.y() < int(n.top)) or reduced_motion()
    if n._arrival is not None:
        n._arrival.stop()
    n._arrived()
    check("the start: the bar comes down from above the screen's edge and lands where it hangs", came and not n._arriving and n.y() == int(n.top),
          f"{came} {n.y()} {n.top} reduced={reduced_motion()}")
    mid = QPointF(n.width() / 2, n.height() / 2)
    n._read_places, n.places = (lambda: n.places_read.emit([])), []  # (the swipes below open the notch: never a read of the owner's windows)
    core.calls.clear()
    n._online("ONLINE")
    bridge.settle()
    at_once = {p.split("?")[0] for _m, p, _b in core.calls}
    order, real_call = [], bridge.call  # (the calls as they are handed to the workers: the order they come back in is the threads' own)
    bridge.call = lambda method, path, body=None, done=None, timeout=None: (order.append(path.split("?")[0]), real_call(method, path, body, done, timeout))
    n._warm_views()
    del bridge.call
    bridge.settle()
    app.processEvents()
    asked = {p.split("?")[0] for _m, p, _b in core.calls}
    check("Core answering, the notch still closed: Chat's and Home's words are read and Chat's turns made, so its first opening shows them "
          "at once (no N/A)", {"/api/messages", "/api/models", "/api/voice", "/api/agents/hooks", "/api/ai-tools"} <= asked and
          len(n._bubbles) == 2 and "N/A" not in n.model_chip.text() and not n.opened, f"{sorted(asked)} {len(n._bubbles)} {n.model_chip.text()!r}")
    check("Core's look at every agent and tool (/api/ai-tools: slow at a start, past the client's 15 s) is asked after Chat's first page, never "
          "ahead of it (2026-10-27: it held a Bridge worker at every start)", "/api/ai-tools" not in at_once
          and order.index("/api/messages") < order.index("/api/ai-tools"), f"{sorted(at_once)} {order}")
    n._natural = False  # (these bursts scroll the traditional way; the owner's, the natural way, is replayed after them)
    seen = _burst(n, mid, dy=12, gap=0.022)  # one touchpad swipe up, ~0.9 s of small wheel events (it folded, unfolded and folded again)
    flips = sum(a != b for a, b in zip(seen, seen[1:]))
    folded = n.lined and (n.width(), n.height()) == LINE
    _burst(n, QPointF(n.width() / 2, n.height() / 2), dy=-12)
    unfolded = not n.lined and not n.opened and (n.width(), n.height()) == CLOSED
    _burst(n, QPointF(n.width() / 2, n.height() / 2), dy=-12)
    check("one swipe, one move (the owner, 2026-10-21: \"up folds, down opens\"): a 0.9 s swipe up folds the bar to the line once; down, the "
          "line becomes the bar; down again, the bar opens", flips == 1 and folded and unfolded and n.opened,
          f"flips={flips} folded={folded} unfolded={unfolded} opened={n.opened}")
    _burst(n, QPointF(n.width() / 2, 14), dy=12)  # over its tabs
    to_bar = not n.opened and not n.lined and (n.width(), n.height()) == CLOSED
    _burst(n, QPointF(n.width() / 2, n.height() / 2), dy=12)
    check("three steps (the owner, 2026-10-21: \"three steps collapse … when we slide up it'll have a small line that is nothing else\"): a "
          "swipe up on the open notch closes it to the bar, the next folds the bar to the line", to_bar and n.lined
          and (n.width(), n.height()) == LINE, f"to_bar={to_bar} lined={n.lined} {n.width()}x{n.height()}")
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtTest import QTest
    n.enterEvent(QEnterEvent(QPointF(70, 3), QPointF(70, 3), QPointF(n.mapToGlobal(QPoint(70, 3)))))  # the mouse passing on its way to a tab
    passed = n.lined and not n._hover_t.isActive() and (n.width(), n.height()) == LINE
    n.leaveEvent(QEvent(QEvent.Type.Leave))
    n.state.result = ("done", "Opened YouTube", "", time.monotonic(), 20.0)  # (Monday with something to say)
    n.refresh()
    kept = n.lined and (n.width(), n.height()) == LINE
    n.state.result = None
    n.refresh()
    QTest.mouseClick(n, Qt.MouseButton.LeftButton, pos=QPoint(n.width() // 2, n.height() // 2))
    check("the line is nothing else: a mouse passing over it leaves it a line (no bar, no peek), so the tabs under it stay in reach; Monday "
          "having something to say leaves it too; a click on it brings the bar", passed and kept and not n.lined and not n.opened
          and (n.width(), n.height()) == CLOSED, f"passed={passed} kept={kept} lined={n.lined} opened={n.opened}")
    n.line(True)
    kept_line = n.p["lined"] is True and win.store.get("pet", {}).get("lined") is True  # (Monday starts as the owner left it)
    n.state.watch = [{"key": "s1", "agent": "Claude Code", "state": "working", "title": "Claude Code · app · Fixing it", "where": {}}]
    n.refresh()
    keep_fs, n.full_screen = n.full_screen, lambda: True  # (a full-screen video in front: the line stays a line)
    n.refresh()
    quiet = (n.width(), n.height()) == LINE
    n.full_screen = keep_fs
    n._fs = (-1e9, False)
    n.full_screen = lambda: False
    n.refresh()
    worn = quiet and (n.width(), n.height()) == LINE_PET and n.mascot() is CLAWD_ART
    n.state.watch[0]["agent"] = "Antigravity"  # (an app with no logo Monday can find: a pal in its colour, not the owner's pet)
    worn = worn and n.mascot() is not n.art
    n.state.watch[0]["agent"] = "Claude Code"
    n.state.watch[0].update(state="done", done_at=time.time())
    n.refresh()
    back = n.lined and (n.width(), n.height()) == LINE
    n.state.watch = []
    n.full_screen = keep_fs
    n.line(False)
    kept_line = kept_line and n.p["lined"] is False and win.store.get("pet", {}).get("lined") is False
    check("the line kept with the pet's choices, folded or not, so a restart starts as the owner left it (2026-10-26)", kept_line)
    check("the line while an app of the owner's works (the owner, 2026-10-26: \"show the apps mascot if the app is working\"): that app's "
          "mascot alone where the line was (Claude Code's Clawd), not over a full-screen app; the line again once it stops", worn and back,
          f"worn={worn} back={back} {n.width()}x{n.height()}")
    n._natural = True  # the owner's touchpad (2026-10-21, "still 3 step closing not done"): Windows' default, a swipe up comes as a scroll down
    n.open("home")
    _burst(n, QPointF(n.width() / 2, 14), dy=-12)
    step1 = not n.opened and not n.lined
    _burst(n, QPointF(n.width() / 2, n.height() / 2), dy=-12)
    step2 = n.lined and (n.width(), n.height()) == LINE
    _burst(n, QPointF(n.width() / 2, n.height() / 2), dy=12)
    check("on a touchpad scrolling the natural way (Windows' default, this PC's), the fingers going up close the notch to the bar, then "
          "fold it to the line; down, the bar again", step1 and step2 and not n.lined and not n.opened, f"{step1} {step2} lined={n.lined}")
    _burst(n, QPointF(n.width() / 2, n.height() / 2), dy=120, events=1)  # a mouse wheel's notch, rolled away: no touchpad setting turns it
    wheel = n.lined
    n.line(False)
    from .notch import CLICK_HOLD_S
    QTest.mousePress(n, Qt.MouseButton.LeftButton, pos=QPoint(n.width() // 2, n.height() - 6))
    QTest.qWait(int(CLICK_HOLD_S * 1000) + 80)  # (a drag that is meant: the button held a moment, then up)
    QTest.mouseMove(n, QPoint(n.width() // 2, 2))  # (the bar hangs from the screen's top: the pointer stops there)
    QTest.mouseRelease(n, Qt.MouseButton.LeftButton, pos=QPoint(n.width() // 2, 2))
    check("a mouse wheel rolled up folds the bar whatever the touchpad does; a short drag up on the bar folds it too (it used to need 40 px "
          "above a bar that hangs from the screen's edge)", wheel and n.lined and not n.opened, f"wheel={wheel} lined={n.lined} opened={n.opened}")
    n.line(False)
    QTest.mousePress(n, Qt.MouseButton.LeftButton, pos=QPoint(n.width() // 2, n.height() - 6))
    QTest.mouseMove(n, QPoint(n.width() // 2 + 2, n.height() - 20))  # (14 px up as the touchpad was pressed: the owner's log)
    QTest.mouseRelease(n, Qt.MouseButton.LeftButton, pos=QPoint(n.width() // 2 + 2, n.height() - 20))
    clicked_open = n.opened and not n.lined
    check("a click whose pointer drifted up as the touchpad was pressed opens the notch, never folds it (the owner, 2026-10-29: \"when im "
          "clicking on something it thinks like im draging it\")", clicked_open, f"opened={n.opened} lined={n.lined}")
    n.close_notch()
    bridge.settle()
    n.line(False)
    n._read_places, n.places = (lambda: n.places_read.emit([])), []  # (no IDE windows of the owner's read in a check)
    n.line(False)
    n.open("home")
    bridge.settle()
    hb = n.handle.geometry()
    on_edge = n.handle.isVisible() and abs(hb.center().x() - n.width() / 2) <= 1 and hb.top() >= n.height() - PAD - 1 and hb.bottom() < n.height()
    c = n.handle.rect().center()
    QTest.mousePress(n.handle, Qt.MouseButton.LeftButton, pos=c)
    QTest.mouseMove(n.handle, c - QPoint(0, 15))
    dragged = not n.opened and not n.lined
    QTest.mouseRelease(n.handle, Qt.MouseButton.LeftButton, pos=c - QPoint(0, 15))
    n.open("home")
    bridge.settle()
    QTest.mouseClick(n.handle, Qt.MouseButton.LeftButton)
    check("the open notch's handle (the owner, 2026-10-21: \"below the notch too\"): in the strip under its cards; dragged up or clicked, the "
          "notch closes to the bar (one step)", on_edge and dragged and not n.opened and not n.lined and n.handle.isHidden(),
          f"edge={on_edge} dragged={dragged} opened={n.opened} lined={n.lined}")
    shot = n.grab().toImage()
    th = shot.pixelColor(int(n.width() / 2 * shot.devicePixelRatio()), int((n.height() - 4) * shot.devicePixelRatio()))  # (the screen's scale)
    check("the closed bar's handle: the line's thread at its bottom", th.red() > 30 and th.alpha() > 200 and not n.lined, th.name())
    n._read_places = lambda: n.places_read.emit([])  # (no IDE windows of the owner's read in a check)
    n.open()  # a click, a hover or a key: no view named (the owner's notch on 2026-10-20 said N/A beside an empty dock)
    bridge.settle()
    app.processEvents()
    pills = home_pills(n)
    ai_line = n.grid.tiles["ai"].line.accessibleName()
    check("a click's open reads Home: the hooks asked (the notch's Settings says them), the AI tile's last task asked of Core (none here: it "
          "says so), nothing cut", any(p == "/api/agents/hooks" for _m, p, _b in core.calls) and n.cc_words.text() == "Claude Code: 2 of 5 hooks"
          and any(p.startswith("/api/tasks?status=COMPLETED") for _m, p, _b in core.calls) and ai_line == "No tasks yet"
          and _fits(n.views.widget(0)), f"{n.cc_words.text()} / {ai_line}")
    check("Monday's pick of pills: no CLIs and no Hermes (the owner's words), installed only, the ready first, each with its state",
          [p.name for p in pills] == ["Kiro", "Cursor"] and pills[1].state == ("off", "opens only"),
          ", ".join(f"{p.name} {p.state}" for p in pills))
    n.set_prefs({**n.p, "pills": ["claude_code", "nope", "kiro", "kiro"]})
    app.processEvents()
    check("the owner's Active pills: those Core knows, each once, never a CLI", [p.name for p in home_pills(n)] == ["Kiro"])
    n.set_prefs({**n.p, "pills": None})
    got, keep = [], n.open_window
    n.open_window = got.append
    n.to_window.click()
    n.open_window = keep
    check("the ↗ disc opens Monday's window on Home", got == ["home"])
    qs = [{"question": "Which pills?", "header": "Pills", "multi": True, "options": [{"label": "Agents", "description": "yours"},
                                                                                     {"label": "IDE", "description": ""}]},
          {"question": "Reload now?", "header": "", "multi": False, "options": [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]}]
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "q9", "agent": "Claude Code", "what": "ask you 2 questions", "tool": "AskUserQuestion",
                                                      "questions": qs}}})
    app.processEvents()
    shown = [b.text() for b in n.views.widget(0).findChildren(Button) if b.isVisible()]
    check("Claude Code's question in the notch: its words, its options, Done for a multi-select, no bare Allow (2026-10-20)",
          n.agent_title.accessibleName() == "Claude Code" and n.agent_verb.text() == "asks you (1 of 2)" and n.agent_cmd.isHidden()
          and "Which pills?" in n.agent_line.text() and {"Agents", "IDE", "Done"} <= set(shown)
          and not any(s.startswith("Allow") for s in shown) and _fits(n.views.widget(0)), ", ".join(shown))
    n._hotkey("allow")
    bridge.settle()
    check("Ctrl+Alt+Y doesn't send a bare Allow for a question", not any(c[1] == "/api/agents/ask/q9" for c in core.calls))
    from PySide6.QtGui import QImage
    img = QImage(90, 90, QImage.Format.Format_ARGB32)
    img.fill(QColor("#000000"))
    painter = QPainter(img)
    n.mascot().draw(painter, QRectF(0, 0, 90, 90), 1.0, "idle")
    painter.end()
    oranges = sum(img.pixelColor(x, y).name() == K.CLAWD_COLOR.lower() for x in range(0, 90, 3) for y in range(0, 90, 3))
    check("Claude Code asking: its own mascot (Clawd, in Claude's orange) stands in the pet's place (2026-10-20)",
          n.mascot() is CLAWD_ART and oranges > 20 and img.pixelColor(45, 45).name() == K.CLAWD_COLOR.lower(), str(oranges))
    a9 = n.state.asks["q9"]
    n._pick(a9, "Agents")
    n._next_question(a9)
    n._pick(a9, "Yes")
    bridge.settle()
    check("the owner's picks go to Core as its answers (the hook hands them to Claude Code)",
          ("POST", "/api/agents/ask/q9", {"decision": "allow", "answers": {"Which pills?": "Agents", "Reload now?": "Yes"}}) in core.calls)
    n.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "q9"}})
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "q10", "agent": "Claude Code", "what": "ask you", "tool": "AskUserQuestion",
                                                      "questions": qs[1:]}}})
    app.processEvents()
    other = next((b for b in n.views.widget(0).findChildren(Button) if b.isVisible() and b.text() == "Other…"), None)
    other_fits = bool(other) and _fits(n.views.widget(0))
    n.take_keys, n.give_keys = (lambda box=None: None), (lambda: None)  # (a check never moves the owner's keyboard focus)
    if other:
        other.click()
        app.processEvents()
    typing = n.q_field is not None and n.q_field.isVisible() and _fits(n.views.widget(0))
    if n.q_field is not None:
        n.q_field.setText("  only   the window, not Core ")
        n.q_field.returnPressed.emit()
    bridge.settle()
    check("“Other…” on Claude Code's question: the owner's own words, typed in the notch, are the answer (2026-10-20)",
          other_fits and typing and ("POST", "/api/agents/ask/q10", {"decision": "allow", "answers": {"Reload now?": "only the window, not Core"}})
          in core.calls and not n._q_typing, f"{other_fits} {typing}")
    del n.take_keys, n.give_keys
    n.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "q10"}})
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # (the old card's widgets really gone, as the live app's loop makes them)
    try:
        n.give_keys()
        closed_ok = n.q_field is None
    except RuntimeError as e:
        closed_ok = f"raised: {e}"
    check("the notch closes after an “Other…” answer, its line gone with its card (2026-10-20: the auto-close raised)", closed_ok is True,
          str(closed_ok))
    keep_tools = n.tools
    n.tools = {**keep_tools, "cursor": {"key": "cursor", "name": "Cursor", "exe": sys.executable}}  # (a program with an icon of its own)
    n.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "c1", "agent": "Cursor", "what": "run a command", "detail": "npm test"}}})
    theirs = n.mascot()
    n.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "c1"}})
    check("another tool asking: its own logo as its mascot; nobody asking: the owner's pet again",
          isinstance(theirs, K.LogoPal) and n.mascot() is n.art, type(theirs).__name__)
    # Home's card on one of the owner's sessions wears that app's mascot (the owner, 2026-10-20: "so we know which chat this is")
    live_a = {"key": "a", "agent": "Claude Code", "state": "working", "title": "Fix the rounding · Editing billing.ts", "lines": ["You asked: fix it"],
              "diff": [], "meters": [], "steps": [], "where": {"entry": "claude-desktop"}}
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [live_a]}})
    n.open("home")
    bridge.settle()
    mine = n.mascot()
    n.tools = {**keep_tools, "codex": {"key": "codex", "name": "Codex", "exe": sys.executable}}
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [{**live_a, "key": "b", "agent": "Codex", "title": "Codex · api · Adding a limiter"}]}})
    bridge.settle()
    codex = n.mascot()
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": []}})
    bridge.settle()
    check("Home's card on one of your sessions: that app's mascot in the pet's place (Clawd for Claude Code, Codex's own logo); none: the pet",
          mine is CLAWD_ART and isinstance(codex, K.LogoPal) and n.mascot() is n.art, f"{type(mine).__name__} {type(codex).__name__} {type(n.mascot()).__name__}")
    n.tools, n.places = keep_tools, []  # the ChatGPT app's Codex (2026-10-21: "the codex mascot i cant see"): no tool of that name, its
    looked, keep_exe = [], n._app_exe  # window closed to the tray: the app's running program's icon
    n._app_exe = lambda entry: (looked.append(entry), sys.executable if entry == "codex_work_desktop" else None)[1]  # noqa: E731
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [{**live_a, "key": "c", "agent": "Codex", "where": {"entry": "codex_work_desktop"}}]}})
    bridge.settle()
    tray = n.mascot()
    n._app_exe = keep_exe
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": []}})
    bridge.settle()
    check("the ChatGPT app's Codex chat with its window closed: the app's own icon from its running program, not the pet",
          isinstance(tray, K.LogoPal) and "codex_work_desktop" in looked, f"{type(tray).__name__} {looked}")
    import shutil
    pkg = tempfile.mkdtemp(prefix="monday-store-")  # a Store app's package, as OpenAI.Codex's is laid out (2026-10-21)
    try:
        app_dir, assets = os.path.join(pkg, "WindowsApps", "OpenAI.Codex_1_x64", "app"), os.path.join(pkg, "WindowsApps", "OpenAI.Codex_1_x64", "assets")
        os.makedirs(app_dir)
        os.makedirs(assets)
        with open(os.path.join(pkg, "WindowsApps", "OpenAI.Codex_1_x64", "AppxManifest.xml"), "w", encoding="utf-8") as f:
            f.write('<Package><uap:VisualElements Square150x150Logo="assets/Square150x150Logo.png" Square44x44Logo="assets/Square44x44Logo.png"/></Package>')
        for name, size in (("Square44x44Logo.png", 10), ("Square44x44Logo.targetsize-256_altform-lightunplated.png", 900),
                           ("Square44x44Logo.targetsize-256_altform-unplated.png", 800), ("Square44x44Logo.targetsize-96_altform-unplated.png", 300)):
            with open(os.path.join(assets, name), "wb") as f:
                f.write(b"x" * size)
        exe = os.path.join(app_dir, "ChatGPT.exe")
        open(exe, "wb").close()
        picked = store_logo(exe)
    finally:
        shutil.rmtree(pkg, ignore_errors=True)
    check("a Store app's mascot from its package's own logo for a dark ground (the Codex app's program icon came back grey with a padlock)",
          str(picked).endswith("targetsize-256_altform-unplated.png") and store_logo(sys.executable) is None, str(picked))
    claude_s = {**live_a, "key": "a", "where": {"entry": "claude-desktop", "cwd": r"C:\work\app"}}
    codex_s = {**live_a, "key": "c", "agent": "Codex", "title": "Codex · api", "where": {"entry": "codex_work_desktop", "cwd": r"C:\work\api"}}
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [claude_s, codex_s]}})  # (Claude at work listed first, as Core lists them)
    n.close_notch()
    keep_exe, n._app_exe = n._app_exe, (lambda entry: sys.executable if "codex" in str(entry).lower() else None)  # noqa: E731
    first = n.mascot()
    n._front_key = lambda: "codex"  # the owner in the ChatGPT app
    front, words_ = n.mascot(), n.bar_words()
    n._front_key, n._app_exe = (lambda: None), keep_exe
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": []}})
    check("the closed bar wears the chat of the app in front (the owner, 2026-10-21: the Codex mascot never showed while a Claude session "
          "at work came first): Codex's own logo and its name there; nothing in front, the first live one (Clawd)",
          first is CLAWD_ART and isinstance(front, K.LogoPal) and str(words_).startswith("Codex"), f"{type(first).__name__} {type(front).__name__} {words_}")
    # the closed bar too (the owner, 2026-10-20 night): the session's mascot, and on hover its name and folder; Home's title has the folder
    named = {**live_a, "name": "/loop , /ponytail:ponytail-gain", "where": {"entry": "claude-desktop", "cwd": "E:\\monday\\app"}}
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": [named]}})
    bridge.settle()
    n.open("home")
    bridge.settle()
    title_words = _head_of(n.grid.tiles["ai"])  # (the whole head: on the tile its grey rest ends in … where it doesn't fit)
    n.close_notch()
    for _ in range(40):
        n._step()
        app.processEvents()
    check("closed: the bar wears the session's mascot, and the mouse on it reads its name and folder",
          not n.opened and n.mascot() is CLAWD_ART and n.bar_words() == "/loop , /ponytail:ponytail-gain · monday › app", f"{n.opened} {n.bar_words()}")
    check("the AI tile's title: the session's name, then its folder", title_words == "/loop , /ponytail:ponytail-gain · monday › app", title_words)
    n.on_event({"type": "AGENT_WATCH", "data": {"sessions": []}})
    bridge.settle()
    check("…no session: the pet on the bar, nothing to say on hover", n.mascot() is n.art and n.bar_words() is None)
    core.answers["/api/agents/hooks"] = (None, "Monday Core is unreachable.")
    n.close_notch()
    n.open()
    bridge.settle()
    down = n.cc_words.text()
    core.answers["/api/agents/hooks"] = {"approvals": True, "stop": True, "pre": False, "mcp": False}
    n._online("ONLINE")
    bridge.settle()
    check("Core away: N/A said; back online: asked again and the truth shown", "N/A" in down and "2 of 5" in n.cc_words.text(),
          f"{down} → {n.cc_words.text()}")
    n.close_notch()
    n.open("home")
    bridge.settle()
    check("open on Home: its width, the hooks as Claude Code's settings say", n.width() == OPEN_W and "2 of 5" in n.cc_words.text(),
          n.cc_words.text())
    n.show_view("chat")
    bridge.settle()
    check("Chat: the thread from Core, markdown as plain words", any("What can I do?" in (w.text() if hasattr(w, "text") else "")
                                                                      for w in n.thread.findChildren(QLabel)))
    check("Chat: the model chip says Core's choice (automatic, and which model that is now)", "auto · gpt-oss" in n.model_chip.text(),
          n.model_chip.text())
    check("Chat: the voice chip says whose voice speaks (the owner's own), no row of provider chips over the thread (2026-10-20)",
          n.voice_chip.text().startswith("Your voice") and not hasattr(n, "providers"), n.voice_chip.text())
    h0, keep_talk = n.height(), list(n.talk)
    n.talk = [{"who": "you", "text": "tell me everything"}, {"who": "Monday", "text": "A long answer, " * 40 + "C:/Users/" + "x/" * 40}]
    n._fill_thread()
    for _ in range(40):
        app.processEvents()
        time.sleep(0.01)
    check("Chat grows with a long answer (it stayed two lines tall: Qt's scroll area keeps its first size) and a long path wraps",
          n.height() > h0 + 40 and n.thread.width() <= n.thread_scroll.viewport().width(), f"{h0} → {n.height()}")
    n.talk = keep_talk
    n._fill_thread()
    n.prompt.setPlainText("what's the time")
    n.send()
    bridge.settle()
    check("a prompt goes to POST /api/command and waits for its answer", any(m == "POST" and p == "/api/command" and b["text"] == "what's the time"
                                                                              for m, p, b in core.calls) and 7 in n.state.asked)
    n.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 8, "role": "monday", "kind": "text", "text": "It's 9:41.",
                                                                "meta": {"reply_to": 7}}}})
    check("its answer shows (State: answer)", n.shown[0] == "answer" and "9:41" in n.shown[2])
    # the thread drawn only where it changed (2026-10-23: every event rebuilt it, 12 a second while a reply streamed, and the owner's
    # place jumped back to the newest words; only the last 8 turns could be seen)
    drawn = [b[2] for b in n._bubbles]
    n.on_event({"type": "SYSTEM_STATUS", "data": {"system": {}}})
    check("Chat: an event that changes nothing in the thread redraws none of it", drawn and [b[2] for b in n._bubbles] == drawn)
    for piece in ("Writing it ", "now"):
        n.on_event({"type": "MESSAGE_DELTA", "data": {"stream_id": "s9", "delta": piece, "reply_to": 7}})
    n._live_t.stop()
    n._fill_thread()  # (what the timer draws, at once)
    check("Chat: a reply's words so far show as they come, under the thread", any("Writing it now" in b[3].text() for b in n._tail),
          [b[3].text() for b in n._tail])
    for _ in range(2):  # (the second: the same event replayed after a reconnect, ?since=)
        n.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 9, "role": "monday", "kind": "text", "text": "Written.",
                                                                    "meta": {"stream_id": "s9", "reply_to": 7}}}})
    said = [b[3].text() for b in n._bubbles + n._tail]
    check("Chat: the stored reply takes their place, once; the same event again adds nothing", said.count("Written.") == 1
          and not any("Writing it" in w for w in said), said[-3:])
    keep_msgs, first = core.answers["/api/messages"], n._bubbles[0][2]
    core.answers["/api/messages"] = {"messages": [{"id": -i, "role": "user", "kind": "text", "text": f"an older question {i}"}
                                                  for i in range(30, 0, -1)], "has_more": False}
    n._older = True
    n._near_top(0)
    bridge.settle()
    check("Chat: scrolled up to the oldest turn loaded, the page before it is read and goes above it (the whole conversation)",
          any(p.startswith("/api/messages?before=1&") for _m, p, _b in core.calls) and n._bubbles[0][1] == "an older question 30"
          and first in [b[2] for b in n._bubbles] and not n._older, n._bubbles[0][1])
    core.answers["/api/messages"] = keep_msgs
    n.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 10, "role": "user", "kind": "text", "text": "pasted " * 2000}}})
    lb, more = n._bubbles[-1][3], [w for w in n._bubbles[-1][2].findChildren(Button) if w.isVisibleTo(n._bubbles[-1][2])]
    short = len(lb.text()) <= LONG_SHOWN
    if more:
        more[0].click()
    check("Chat: a very long turn (a paste) shows its start and Show all, which shows the rest in place", short and more
          and len(lb.text()) > 10000 and not more[0].isVisibleTo(n._bubbles[-1][2]), len(lb.text()))
    for model, piece in (("gpt-oss", "I can't help with"), ("qwen3", "It's "), ("qwen3", "9:41.")):  # (one stream id for every model Core tries)
        n.on_event({"type": "MESSAGE_DELTA", "data": {"stream_id": "s10", "delta": piece, "model": model, "reply_to": 7}})
    n._live_t.stop()
    n._fill_thread()
    check("Chat: a model that streamed, then failed (a refusal, a tool call, a stall), and the next one's words under the same stream id: "
          "only the second's show (2026-10-27: the two answers ran together)", n._live["s10"] == "It's 9:41."
          and any("It's 9:41." in b[3].text() and "can't" not in b[3].text() for b in n._tail), [b[3].text() for b in n._tail])
    n.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 11, "role": "monday", "kind": "text", "text": "It's 9:41.",
                                                                "meta": {"stream_id": "s10", "reply_to": 7}}}})
    check("…its stored reply ends the stream, and which model wrote it goes with it", "s10" not in n._live and "s10" not in n._live_model)
    n.state.tasks["T5"] = {"id": "T5", "title": "Preflight: open Notepad", "status": "RUNNING"}  # (one of Monday's own tasks, the preflight's say)
    n.state.asked_text = ("and the date?", time.monotonic())
    n.refresh()
    n._fill_thread()
    check("Chat: a question asked while a task runs (the closed bar says Working, not Asked) still shows under the thread, with Monday's “…” "
          "until its answer (2026-10-27)", n.shown[0] == "working" and [b[:2] for b in n._tail] == [("asked", "and the date?"), ("waiting", "…")],
          f"{n.shown[0]} {[b[:2] for b in n._tail]}")
    n.state.tasks.clear()
    n.state.asked_text = None
    n.refresh()
    n._fill_thread()
    core.calls.clear()
    n.api_dot.set("bad")  # (the stream was lost: Core away a while)
    n._online("ONLINE")
    bridge.settle()
    check("Core back after a gap with Chat open: its newest page is read again (Core replays at most 500 events), and the thread stays when "
          "nothing is newer (2026-10-27)", n.opened and n.view == "chat" and any(p.startswith("/api/messages") for _m, p, _b in core.calls)
          and n._bubbles[-1][0] == 11, f"{[p for _m, p, _b in core.calls]} {n._bubbles[-1][0]}")
    held, older = list(n._messages), n._older
    n._messages, n._older = [], False
    n.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 40, "role": "user", "kind": "text", "text": "seeded by the stream"}}})
    n._got_messages({"messages": [{"id": i, "role": "user", "kind": "text", "text": f"turn {i}"} for i in range(30, 41)], "has_more": True}, None)
    check("Chat: a thread seeded by the stream before any page was read offers the pages before it once a page reaches its oldest turn "
          "(has_more; 2026-10-27: no scroll-back)", n._older and n._messages[0]["id"] == 30, f"{n._older} {n._messages[0]['id']}")
    n._messages, n._older = held, older
    n._merge([])
    n._fill_thread()
    core.answers["/api/command"], win.notch = {"message": {"id": 12, "role": "user"}}, n
    win.command("what day is it", spoken=True)  # (the ear's sentence: the window's one pipeline, not the notch's send)
    bridge.settle()
    cid = next(b["client_id"] for _m, p, b in reversed(core.calls) if p == "/api/command")
    win.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 13, "role": "user", "kind": "text", "text": "and the time",
                                                                  "client_id": cid}}})  # (the stream's copy of a question, before its POST returns)
    check("a question spoken to the ear is the notch's too: its answer shows on the closed bar (2026-10-27: only the notch's own sends did), "
          "noted from Core's answer and from the stream's copy of the question, whichever comes first",
          {12, 13} <= n.state.asked and {12, 13} <= set(win.asked), f"{sorted(n.state.asked)} {sorted(win.asked)}")
    win.notch, core.answers["/api/command"] = None, {"message": {"id": 7, "role": "user"}}
    import shiboken6
    gone = PetView(n, 20)
    n._pets.append(gone)
    shiboken6.delete(gone)  # (its tile replaced: Qt freed it before tiles_changed listed the pets again)
    n._tick()
    check("the frames skip a pet view Qt already freed, and drop it (desktop.log 2026-10-26: 259 \"already deleted\" errors in one session)",
          all(pv is not gone for pv in n._pets), len(n._pets))
    core.answers["/api/command"] = (None, "Monday Core is unreachable.")
    n.prompt.setPlainText("open spotify")
    n.send()
    bridge.settle()
    check("refused: Not sent, with why, the words given back", n.shown[0] == "failed" and n.prompt.toPlainText() == "open spotify")
    n.core_phase = "restarting"  # (core/desktop.py's supervisor starting Core again: the words say so, not "unreachable", 2026-10-23)
    n.prompt.setPlainText("open spotify")
    n.send()
    bridge.settle()
    n._online("OFFLINE")
    check("Core being restarted by the app: Not sent says so and to send again; the Core line says restarting, not offline",
          "restarting" in n.state.result[2] and n.prompt.toPlainText() == "open spotify" and n.api_words.text() == "Monday Core: restarting…",
          f"{n.state.result[2]} / {n.api_words.text()}")
    n.core_phase = None
    n._online("ONLINE")
    n.on_event({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 5, "status": "PENDING", "title": "Hermes wants to run a command",
                                                                         "risk": "medium", "always": False}}})
    labels = [w.text() for w in n.views.widget(1).findChildren(Button)]
    check("an approval opens Chat with Deny and Allow (no Always when Core doesn't offer it)", n.view == "chat" and "Allow" in labels
          and "Deny" in labels and "Always allow" not in labels, ", ".join(labels))
    n.decide(5, "approve")
    bridge.settle()
    check("Allow posts the owner's answer", any(c[:2] == ("POST", "/api/permissions/5/approve") for c in core.calls))
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "folder"))
        n.take_file(os.path.join(d, "folder"))
        check("a folder is refused, with why", n.state.file["phase"] == "failed" and "folder" in n.state.file["why"])
        n._file_done()
        prog = os.path.join(d, "setup.exe")
        open(prog, "wb").write(b"MZ")
        f = examine(prog)
        check("a program is only read: no Open, no email", "open" not in f["actions"] and "email" not in f["actions"] and f["runs"])
        txt = os.path.join(d, "quarterly-planning-notes-for-the-team-offsite.txt")
        open(txt, "w").write("hello")
        n._upload = lambda f: n.uploaded.emit(f, ({"id": "f9"}, None))  # (the stub Core has no HTTP: the copy stood in for)
        n.take_file(txt)
        for _ in range(100):
            app.processEvents()
            if (n.state.file or {}).get("phase") == "ready":
                break
            time.sleep(0.01)
        app.processEvents()
        check("a text file: its six actions on two rows, none cut, the card grown to hold them", n.file_btns2.count() > 1
              and _fits(n.views.widget(2)) and n.target_size()[1] > TOP_IN + 28 + TAB_GAP + PAD + CARD_H["drop"], str(n.target_size()))
        n._file_done()
        del n._upload
    toasts = []
    n.toast = lambda title, words: toasts.append((title, words))
    n.waits.clear()  # (approval 5 above was answered here; Core's PERMISSION_GRANTED would have taken it)
    n.on_event({"type": "AGENT_ALERT", "data": {"kind": "stuck", "agent": "Codex", "project": "shop", "text": "No output for 5 min."}})
    n.on_event({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 6, "status": "PENDING", "title": "Open a file", "risk": "low"}}})
    n._waits_due(time.time() + 1)
    early = len(toasts)
    n._waits_due(time.time() + NOTIFY_WAIT_S + 1)
    t0 = time.time()
    n.on_event({"type": "TASK_COMPLETED", "data": {"task": {"id": "T9", "status": "COMPLETED", "title": "Long build", "created_at": t0 - 600,
                                                            "finished_at": t0, "result": "Built."}}})
    check("notifications: an alert at once, an approval only after it waited, a long task's end", early == 1
          and [t for t, _w in toasts] == ["Codex · shop may be stuck", "Needs your OK", "Done · Long build"], str(toasts))
    n.p = prefs({**n.p, "notify": False})
    n.on_event({"type": "AGENT_ALERT", "data": {"kind": "stuck", "agent": "Codex", "text": "Still nothing."}})
    check("notifications off: none", len(toasts) == 3)
    from PySide6.QtGui import QMouseEvent
    n.desk = DeskPet(n)
    n.desk.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # (checked off screen: never on the owner's desktop)
    n.set_pref("desk", True)
    a = QGuiApplication.primaryScreen().availableGeometry()
    check("the pet on the desktop: shown when on, bottom right of the main screen, on the taskbar", n.desk.isVisible()
          and n.desk.geometry().bottom() == a.bottom() and n.desk.geometry().right() == a.right() - 16, str(n.desk.geometry()))
    n.desk.grab()  # a frame: where its parts are
    n.set_pref("visible", False)
    hidden = not n.isVisible()
    close = n.desk.parts["close"].center()
    n.desk.press = n.desk.mapToGlobal(close.toPoint())
    n.desk.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, close, n.desk.mapToGlobal(close), Qt.MouseButton.LeftButton,
                                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("its × puts it away, and with the notch off the notch comes back (one of the two always shows)", hidden and n.isVisible()
          and not n.desk.isVisible() and n.p["desk"] is False and n.p["visible"] is True and win.store["pet"]["desk"] is False)
    for _ in range(3):
        n.poke()
    check("three quick pokes: dizzy, and it says so (after anything waiting on the owner)", n.art.dizzy(time.monotonic())
          and n.state.result[0] == "dizzy")
    from .qtoverlay import Marks
    n.marks = Marks()
    n.marks.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    n.on_event({"type": "SCREEN_MARKS", "data": {"marks": [{"kind": "circle", "rect": [400, 300, 120, 40], "label": "Save"}]}})
    check("Monday's marks: SCREEN_MARKS draws them on the screen", n.marks.isVisible() and len(n.marks.lines) == 1)
    n.marks.clear()
    with tempfile.TemporaryDirectory() as d:
        shot = os.path.join(d, "circled-1.png")
        QImage(40, 30, QImage.Format.Format_ARGB32).save(shot)
        n.uploading = False
        n._circled({"path": shot, "box": (1, 2, 3, 4), "crop": (0, 0, 40, 30)})
        f = n.state.file or {}
        check("a circled part comes to the notch like a dropped picture: What's this?, Ask, Chat", f.get("actions") == CIRCLE_ACTIONS
              and f.get("circled", {}).get("box") == (1, 2, 3, 4), str(f.get("actions")))
        end = time.monotonic() + 5
        while n.uploading and time.monotonic() < end:  # (the stub has no Core to copy it to: it fails, and says so)
            app.processEvents()
            time.sleep(0.02)
    n.set_pref("desk", True)
    n.on_event({"type": "UI_NAVIGATE", "data": {"page": "pet_hide"}})
    hid = n.p["visible"] is False
    n.on_event({"type": "UI_NAVIGATE", "data": {"page": "pet"}})
    check("“hide your pet” / “open your pet”: the notch hidden, then back", hid and n.p["visible"] is True)
    got, keep = [], n.open_window
    n.open_window = got.append
    for page in ("stats", "pills", "nope"):
        n.on_event({"type": "UI_NAVIGATE", "data": {"page": page}})
    n.open_window = keep
    check("“open your Statistics page”: the window comes forward on that page; a page it hasn't, nothing", got == ["stats", "pills"], str(got))
    _check_agent_view(check, n, bridge, core)
    _check_island(check, n, bridge, core, win)
    _check_cards(check, n, bridge, core)
    _check_new_widgets(check, n, bridge, core)
    _check_screenshot(check, n, bridge)
    _check_looks(check, n, bridge, core, win)
    _check_start(check, n, bridge, core, win)
    n.close_notch()
    for _ in range(60):
        n._step()
    check("closing goes back to the bar", not n.opened and n.width() <= CLOSED_LINE[0])
    from PySide6.QtTest import QTest
    QTest.mouseClick(n, Qt.MouseButton.LeftButton, pos=QPoint(n.width() - 6, n.height() // 2))  # (a real click, off the pet)
    check("a click on the closed notch opens it (a real mouse press and release)", n.opened)
    n.close_notch()
    for _ in range(60):
        n._step()
    # the spring moves by the time that really passed (2026-10-24: frames of ~50 ms had stretched the 0.7 s opening to 5 s)
    keep_place, keep_closing = n._place, n.closing
    n._place, n.closing = (lambda w, h: None), None
    n.w_spring, n.h_spring = Spring(288.0), Spring(32.0)
    n.w_spring.target, n.h_spring.target = 640.0, 384.0
    n._stepped = time.monotonic() - 0.3  # this frame came 300 ms after the last
    n._step()
    moved = n.w_spring.value - 288.0
    n._anim.stop()
    n._place, n.closing = keep_place, keep_closing
    check("a late frame moves the opening by the time that passed (at most ANIM_MAX_DT), not by one frame's 15 ms", moved > 30, f"{moved:.0f} px")
    check("every control named", not K.named(n), ", ".join(K.named(n)))
    check("the shell hangs square above and round below", shell_path(100, 40, True, 14).boundingRect().height() == 40)
    bridge.stop()
    return ok


def shots(folder, base=None):
    """Each view drawn off screen at the recording's scale, against a test Core."""
    from .native import Core
    from .qtwindow import Bridge, Window
    from . import desktop
    os.environ["MONDAY_REDUCED_MOTION"] = "1"  # each view where it settles, the pet still
    app = K.make_app(["monday-notch"])
    bridge = Bridge(Core(base) if base else None)
    win = Window(bridge, store=desktop.load_state(), ear=False)
    n = Notch(win, show_on_start=False)
    n.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    n.show()
    os.makedirs(folder, exist_ok=True)
    out = []

    def grab(name):
        bridge.settle(20)
        end = time.monotonic() + 10
        while getattr(n, "places_pending", False) and time.monotonic() < end:  # the open IDE windows, read on a thread
            app.processEvents()
            time.sleep(0.02)
        for _ in range(80):
            if not n._anim.isActive():
                break
            n._step()
        app.processEvents()
        path = os.path.join(folder, f"notch_{name}.png")
        pix = n.grab()
        pix.save(path)
        out.append(path)
    grab("closed")
    for v in ("home", "chat", "drop", "settings"):
        n.open(v)
        grab(v)
    # the island (2026-10-24): arranging, the widget menu, each ready-made layout, the note and the clipboard (the owner's own layout put
    # back after: these pictures never change what Home keeps)
    mine = [dict(it) for it in n.grid.layout_]
    n.open("home")
    n.grid.edit(True)
    grab("island_edit")
    n.grid.edit(False)
    n.open_menu()
    grab("island_menu")
    for k in I.LAYOUTS:
        n.grid.set_layout(I.preset(k))
        n.open("home")
        grab(f"island_{k}")
    n.grid.set_layout(mine)
    for v in ("note", "clip"):
        n.open(v)
        grab(f"island_{v}")
    # the update card (docs/adr/0010), drawn from a sample offer: what Home shows when the channel has a newer Monday; put away after
    keep_offer, keep_seen = n.update_offer, n._card_seen
    n._card_seen = None
    n.open("home")
    bridge.settle(20)  # (Home's own reads first: its GET /api/update would put the sample away)
    n._got_update({"state": "available", "action": "install", "current": "0.3.0", "detail": "Monday 0.3.1 is ready to install.",
                   "latest": {"version": "0.3.1", "notes": "A sample release note: what the new version changes."}}, None)
    grab("home_update")
    n.update_offer, n._card_seen = keep_offer, keep_seen
    n.refresh()
    bridge.start()  # Home's agent view on the owner's real sessions: Core's watch sends AGENT_WATCH while a window listens
    n.open("home")  # (opening Home asks Core for the sessions as they are now: a window that just connected would otherwise wait for a change)
    end = time.monotonic() + 15
    while not n.state.live() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)
    if n.state.live():
        n.open("home")
        grab("home_agent")
        n.show_agent()
        grab("agent")
        if n.code.isVisible():  # the code card whole (the agent view may be scrolled past its tinted rows)
            path = os.path.join(folder, "notch_code_card.png")
            n.code.grab().save(path)
            out.append(path)
        n.close_notch()  # the closed bar with that session's mascot, the mouse on it: its name and folder (2026-10-20 night)
        n._over = True
        grab("closed_live")
        n._over = False
        if (n.state.agent or {}).get("steps"):
            n.steps_show("key")
            grab("agent_steps")
        n.story_show()
        grab("agent_story")
        n.story_close()
        rw = next((s["rewind"] for s in n.state.live() if s.get("rewind")), None)
        if rw:  # Rewind's window, Core's preview in it (never rewound from here)
            d = n.rewind_open(rw, show=False)
            d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            d.show()
            bridge.settle(60)
            app.processEvents()
            path = os.path.join(folder, "notch_rewind.png")
            d.grab().save(path)
            out.append(path)
            d.close()
    else:
        print("no agent of yours is live: the agent view isn't drawn")
    bridge.stop()
    return out


if __name__ == "__main__":
    from core import qtnotch as _one  # (one copy of the module, as the app has: the island's tiles import core.qtnotch's PetView, and a
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else None  # check run on __main__'s own copy never saw them)
    if "--check" in sys.argv:
        sys.exit(0 if _one._check() else 1)
    elif "--shot" in sys.argv:
        for p_ in _one.shots(sys.argv[sys.argv.index("--shot") + 1], f"http://127.0.0.1:{port}" if port else None):
            print(p_)
    else:
        print(__doc__)
