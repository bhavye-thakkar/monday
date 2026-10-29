"""Monday's window in Qt (2026-10-19): the whole app on Windows, macOS and Linux, in the look of the owner's Coucou recording.

A black rail on the left (Monday's pages, then the notch's), the page on a near-black panel: each page opens on its banner and shows
what Monday Core reports, through the one API client (native.Core) and the one event stream. Nothing here decides anything: Core and
its rules do; a control that changes something calls Core and then shows what Core answered (Failed and why when it refused). A page
is listed in the rail only once it is built: no row leads nowhere. The notch (core/qtnotch.py) shares this process, this Bridge and
its stream.

Run: `python -m core.qtwindow` (against MONDAY_PORT, 8765 by default). Check: `python -m core.qtwindow --check`.
Pictures of every page: `python -m core.qtwindow --shot <folder> [--port 8773]` (drawn off screen, at the recording's size).
"""
from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import qtkit as K  # noqa: F401  (puts PySide6 on the path first)
from .native import Core, Ear, mic_line
from .qtkit import (DIVIDER, EDGE, GREEN_TEXT, ORANGE, PANEL, PANEL_EDGE, RADIUS_PANEL, RAIL, RAIL_W, TEXT, TEXT_2, TEXT_3,
                    Button, Card, Dot, Empty, GlowCard, Hero, NavItem, Notice, Page, Pet, PetArt, Segmented, _section, dropdown, field,
                    filled, font, label, make_app, named, render)
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QMainWindow, QMenu, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QStackedWidget, QToolTip, QVBoxLayout, QWidget)

CALL_WORKERS = 6          # Core calls in flight at once (each on a worker thread; the answer comes back on the window's)
CORE_DOWN_LOG_S = 10      # a call Core didn't answer goes to desktop.log at most this often (while it is down, every call fails)
HOLD_MAX_S = 240          # a call held while the desktop app is still (re)starting Core (Bridge.waiting) waits this long at most, then fails
WARM_S = 180              # this long after Core first answers, a read it was too busy to answer in time is asked again (its warm-up: Ollama's
#                           start, the AI tools' first discovery; 2026-10-29: GET /api/ai-tools waited 50 s on them, the window gave up at 15)
CONTENT_W = 1100          # a page's column at most (the recording's: 1373 px at 125 %), left in the panel
WHERE_OPENED, WHERE_AGENTS = 5, 3   # Statistics' "Where Monday worked": the most opened, then the agents most used
WINDOW_SIZE = (1280, 800)  # the first time; then where the owner left it (data/desktop.json "rect")
MIN_SIZE = (900, 600)
POLL_S = 3.0              # the mic line and the rail's state, read again this often while the window shows
LOOK_VOICE_WAIT_MS = 12000  # a look's explanation not heard within this: its marks come at reading pace instead (the voice failed or is slow)
REFRESH_S = 10.0          # a page's numbers, at most this often when events say they changed
SEARCH_MS = 300           # typing in a search box: Core is asked this long after the last key
HISTORY_ROWS = 12         # Home's history: the newest this many
GREETINGS = ((12, "Good morning"), (18, "Good afternoon"), (24, "Good evening"))  # as the notch's brief (Notch._brief)
READERS = (("google", "Google's recognizer", "Each phrase the microphone hears goes to Google to be read."),
           ("local", "Whisper, on this computer", "Heard and read on this PC: nothing leaves it."),
           ("windows", "Windows dictation", "Windows' online dictation; takes effect when Monday Core restarts."))
TONES = {"done": "ok", "bad": "bad", "wait": "wait", "busy": "info", "off": "off"}  # an activity row's tone -> its dot


def greeting(now=None):
    hour = time.localtime(now).tm_hour
    return next(words for end, words in GREETINGS if hour < end)


def ago(ts, now=None):
    """When, in the fewest words: 'now', '12 min', '3 h', 'Mon 14:05', '12 Oct'."""
    now = now or time.time()
    s = max(0, now - ts)
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)} min"
    if s < 12 * 3600:
        return f"{int(s // 3600)} h"
    if s < 6 * 86400:
        return time.strftime("%a %H:%M", time.localtime(ts))
    return time.strftime("%d %b", time.localtime(ts)).lstrip("0")


def duration(s):
    """Seconds as the recording's cards say them: '0 min', '42 s', '4 min', '1 h 12 min'."""
    if s is None:
        return "N/A"
    if s < 60:
        return f"{round(s)} s" if s else "0 min"
    m = round(s / 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


# -- Core, for the window and the notch ---------------------------------------------------------------------------------------
class Bridge(QObject):
    """Monday Core for the window and the notch: the one API client (native.Core) and the one event stream, every answer brought
    back to the UI thread. call(method, path, body, done): the request runs on a worker; done(data, err) runs on the UI thread, err
    being Core's words when it failed ("Monday Core is unreachable.", its detail), never hidden."""
    event = Signal(dict)
    online = Signal(str)                    # ONLINE / RECONNECTING / OFFLINE, as the stream finds Core
    _back = Signal(object, object, object)  # (done, data, err) from a worker
    _later = Signal(object)                 # fn() from another thread (the ear's, the speaker's), run on the UI thread

    def __init__(self, core: Core | None = None):
        super().__init__()
        self.core = core or Core()
        self.state, self.pending, self._stop = "CONNECTING", 0, False
        self._said_down = float("-inf")  # when a failed Core call was last written to desktop.log (CORE_DOWN_LOG_S apart)
        # While core/desktop.py's supervisor is starting Core (waiting(): the window's _starting), a read that failed, or any call that never
        # reached Core (native.Unsent), is held and sent again once the stream finds Core (2026-10-28: at sign-in Core came up 50-90 s after
        # the window, and every page kept "… couldn't be read: Monday Core is unreachable."). Released with its error after HOLD_MAX_S, or
        # at once when the supervisor gives up (release()).
        self.held, self.waiting = [], (lambda: False)
        self._online_at = None  # when the stream first found Core (WARM_S: a read Core was too busy to answer is asked again)
        self._pool = ThreadPoolExecutor(max_workers=CALL_WORKERS, thread_name_prefix="core-call")
        self._back.connect(self._deliver)
        self._later.connect(lambda fn: fn())

    def later(self, fn):
        """fn on the UI thread, from any thread (a timer started on a thread without Qt's loop would never fire)."""
        self._later.emit(fn)

    def call(self, method, path, body=None, done=None, timeout=None):
        self.pending += 1
        self._pool.submit(self._run, method, path, body, done, timeout)

    def get(self, path, done):
        self.call("GET", path, None, done)

    def post(self, path, body=None, done=None, timeout=None):
        self.call("POST", path, body if body is not None else {}, done, timeout)

    def run(self, fn, done):
        """Other slow work off the UI thread (an upload): fn() -> (data, err); done(data, err) on the UI thread."""
        self.pending += 1

        def go():
            try:
                data, err = fn()
            except Exception as e:
                data, err = None, f"Monday Core couldn't take it: {e!r}"[:200]
            self._back.emit(done, data, err)
        self._pool.submit(go)

    def _run(self, method, path, body, done, timeout, since=None):
        try:
            data, err = self.core.call(method, path, body, timeout)
        except Exception as e:  # a bug in a reply's handling must say so, not freeze a page on "Loading"
            data, err = None, f"Failed: {e!r}"[:200]
        if err and (getattr(err, "unsent", False) or (method == "GET" and err.startswith("Monday Core"))) and self.waiting():
            since = since or time.monotonic()
            if time.monotonic() - since < HOLD_MAX_S:  # Core is still being started by this app: asked again once it answers
                self.later(lambda: self._hold((method, path, body, done, timeout, since, err)))
                return
        if err == "Monday Core didn't answer in time." and method == "GET" and not getattr(err, "unsent", False) \
                and self._online_at is not None and time.monotonic() - self._online_at < WARM_S:
            # Core answers but is still warming up: the same read again a second later (its answer, or its failure after WARM_S)
            self.later(lambda: QTimer.singleShot(1000, lambda: self._pool.submit(self._run, method, path, body, done, timeout, since)))
            return
        if err and err.startswith("Monday Core") and time.monotonic() - self._said_down > CORE_DOWN_LOG_S:  # "unreachable" / "didn't
            self._said_down = time.monotonic()  # answer in time": in desktop.log with the time and the call, to set beside core.log
            print(f"{time.strftime('%H:%M:%S')} Core call failed: {method} {path}: {err} (the stream: {self.state})")
        self._back.emit(done, data, err)

    def _hold(self, item):
        """On the UI thread: a call Core couldn't take while it starts, sent again now if the stream already found Core, else when it does."""
        if self.state == "ONLINE":  # (half a second apart: a Core that answers the stream but not this call isn't asked in a loop)
            QTimer.singleShot(500, lambda: self._pool.submit(self._run, *item[:6]))
            return
        if not self.held:
            QTimer.singleShot(int(HOLD_MAX_S * 1000), self.release)
        self.held.append(item)

    def release(self):
        """Every held call answered with its own failure (the supervisor gave up on Core, or HOLD_MAX_S passed): the pages say why."""
        held, self.held = self.held, []
        for _m, _p, _b, done, _t, _s, err in held:
            self._deliver(done, None, err)

    def _deliver(self, done, data, err):
        self.pending -= 1
        if done is not None:
            try:
                done(data, err)
            except Exception:
                traceback.print_exc()

    def start(self):
        threading.Thread(target=self.core.events, args=(None, self.event.emit, self._state, lambda: self._stop), daemon=True,
                         name="core-events").start()

    def _state(self, s):
        self.state = s
        if s == "ONLINE" and self._online_at is None:
            self._online_at = time.monotonic()
        if s == "ONLINE" and self.held:  # (the stream's thread: each held call goes back to a worker, its page answered as usual)
            self.later(self._resend)
        self.online.emit(s)

    def _resend(self):
        held, self.held = self.held, []
        for item in held:
            self._pool.submit(self._run, *item[:6])

    def stop(self):
        self._stop = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def settle(self, timeout=10.0):
        """Until every call has come back (the checks and the pictures wait for Core's answers, never a fixed time)."""
        end = time.monotonic() + timeout
        app = K.QApplication.instance()
        while self.pending and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        return not self.pending


# -- the frame ------------------------------------------------------------------------------------------------------------------
class Panel(QWidget):
    """The rounded near-black panel the pages sit on (the recording's, inset from the window's edges)."""

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(PANEL_EDGE), 1))
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_PANEL, RADIUS_PANEL)
        p.end()


class StatusPill(QWidget):
    """The rail's foot: Monday's state as Core and the ear report it (the recording's "● Dictation is off")."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(32)
        self.dot, self.words = "off", "Connecting…"
        self.setAccessibleName("Monday's state: Connecting…")

    def set(self, dot, words):
        if (dot, words) != (self.dot, self.words):
            self.dot, self.words = dot, words
            self.setAccessibleName(f"Monday's state: {words}")
            self.setToolTip(words)
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0c0c0c"))
        p.drawRoundedRect(r, 8, 8)
        p.setBrush(QColor(Dot.COLORS.get(self.dot, self.dot)))
        p.drawEllipse(QPointF(16, r.center().y()), 3.5, 3.5)
        p.setPen(QColor(TEXT_3))
        p.setFont(font(12.5))
        fm = p.fontMetrics()
        p.drawText(QRectF(28, 0, r.width() - 36, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.words, Qt.TextElideMode.ElideRight, int(r.width() - 36)))
        p.end()


class Rail(QWidget):
    """The left rail: Monday's mark and name, its pages in sections (the recording's FLOW / NOTCH), its state and version."""

    def __init__(self, win: "Window"):
        super().__init__()
        filled(self, RAIL)
        self.win = win
        self.setFixedWidth(RAIL_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 22, 12, 12)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 0, 0, 0)
        head.setSpacing(10)
        self.pet = Pet(30, PetArt(win.pet_prefs), name="Monday")
        head.addWidget(self.pet)
        self.name = label("Monday", 18, 700)
        head.addWidget(self.name)
        head.addStretch(1)
        v.addLayout(head)
        v.addSpacing(22)
        nav = QWidget()
        self.nav = QVBoxLayout(nav)
        self.nav.setContentsMargins(0, 0, 0, 0)
        self.nav.setSpacing(0)  # (the recording's step is 35 for its 9 rows; Monday's 17 fit the first window's 800 at 32)
        scroll = QScrollArea()
        scroll.setWidget(nav)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.addWidget(scroll, 1)
        self.items: dict[str, NavItem] = {}
        section = None
        for sec, key, icon_name, name in win.pages_listed():
            if sec != section:
                if section is not None:
                    self.nav.addSpacing(10)
                lb = _section(sec)
                lb.setContentsMargins(10, 0, 0, 6)
                self.nav.addWidget(lb)
                section = sec
            item = NavItem(icon_name, name)
            item.clicked.connect(lambda _=False, k=key: win.show_page(k))
            self.items[key] = item
            self.nav.addWidget(item)
        self.nav.addStretch(1)
        v.addSpacing(10)
        self.pill = StatusPill()
        v.addWidget(self.pill)
        v.addSpacing(10)
        self.version = label("", 11, 400, TEXT_3)
        self.version.setContentsMargins(10, 0, 0, 0)
        v.addWidget(self.version)

    def lit(self, key):
        if key in self.items:
            self.items[key].setChecked(True)


class Screen(QScrollArea):
    """One page: its banner and cards in a column (at most CONTENT_W wide, left in the panel), scrolled. build() makes it once,
    load() asks Core for what it shows, on_event(ev) keeps it current (each only while it is the page on show)."""
    key = icon = name = section = ""

    def __init__(self, win: "Window"):
        super().__init__()
        self.win, self.bridge = win, win.bridge
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(28, 24, 18, 0)
        self.page = Page()
        self.page.setMaximumWidth(CONTENT_W)
        self.page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(self.page, 1)
        h.addStretch(0)
        self.setWidget(holder)
        K.SmoothScroll(self)
        self.setAccessibleName(self.name)
        self.built, self.loaded_at = False, 0.0

    def ensure(self):
        if not self.built:
            self.built = True
            self.build()

    def build(self):
        raise NotImplementedError

    def load(self):
        self.loaded_at = time.monotonic()

    def on_event(self, ev):
        pass

    def leave(self):
        """Another page was chosen, or the window hid: what runs only while the page shows (a live picture) stops."""

    def stale(self, every=REFRESH_S):
        """Load again when Core's events say the page changed, at most once per `every` seconds."""
        if time.monotonic() - self.loaded_at >= every:
            self.load()


START_MIN_MS, START_FADE_MS = 700, 320  # the start screen stays at least this long (one start, not a flash), then fades over this


class _Breath(QWidget):
    """Three dots breathing one after the other while Monday Core is still coming up; still when motion is off, gone once it answers."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(54, 14)
        self.t = 0.0
        self._a = QVariantAnimation(self)
        self._a.setDuration(1400)
        self._a.setStartValue(0.0)
        self._a.setEndValue(1.0)
        self._a.setLoopCount(-1)
        self._a.valueChanged.connect(lambda v: (setattr(self, "t", float(v)), self.update()))
        if not K.reduced_motion():
            self._a.start()

    def stop(self):
        self._a.stop()

    def paintEvent(self, _e):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            k = 0.35 + 0.65 * (0.5 + 0.5 * math.sin((self.t - i * 0.18) * 2 * math.pi)) if self._a.state() == QVariantAnimation.State.Running else 0.6
            p.setBrush(K.alpha(TEXT_2, k))
            p.drawEllipse(QPointF(9 + i * 18, 7), 4, 4)
        p.end()


class StartScreen(QWidget):
    """The start (the owner, 2026-10-21: "a starting animation like the video"): over the pages until Monday Core answers, the owner's own
    pet (alive: its blink and bob), "Monday", "Starting…" and three dots breathing while Core is still coming up. It stays at least
    START_MIN_MS so the start reads as one, then fades out (done, on the first ONLINE). Nothing here pretends: the dots breathe only while
    Core hasn't answered. Motion off: no breathing, no fade; it simply goes."""

    def __init__(self, parent, pet_prefs):
        super().__init__(parent)
        self.shown_at, self.leaving, self._fade = time.monotonic(), False, None
        self.setAccessibleName("Starting Monday")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background: {PANEL}; border-radius: {RADIUS_PANEL}px;")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        self.pet = K.Pet(96, K.PetArt(pet_prefs), live=True, name="Monday's pet, waking")
        lay.addWidget(self.pet, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(label("Monday", 20, 700), 0, Qt.AlignmentFlag.AlignHCenter)
        self.words = label("Starting…", 13, 400, TEXT_2)
        lay.addWidget(self.words, 0, Qt.AlignmentFlag.AlignHCenter)
        self.dots = _Breath()
        lay.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignHCenter)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.raise_()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Resize and obj is self.parentWidget():
            self.setGeometry(obj.rect())
        return False

    def done(self):
        """Monday Core answered: the dots stop, and after START_MIN_MS since the start the screen fades out and goes."""
        if self.leaving or self.isHidden():
            return
        self.leaving = True
        self.dots.stop()
        self.words.setText("Ready.")
        QTimer.singleShot(max(0, START_MIN_MS - int((time.monotonic() - self.shown_at) * 1000)), self._fade_out)

    def _fade_out(self):
        if K.reduced_motion() or not self.isVisible():
            return self.hide()
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(START_FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade.valueChanged.connect(lambda v: eff.setOpacity(float(v)))
        self._fade.finished.connect(self.hide)
        self._fade.start()


class Window(QMainWindow):
    """The window: the rail, the panel, the page on show. One per process; the notch shares its Bridge."""

    def __init__(self, bridge: Bridge, store=None, ear: bool = True, hotkeys=None, persist: bool = False):
        super().__init__()
        self.bridge, self.store = bridge, store if store is not None else {}
        self.hotkeys, self.persist, self.notch = hotkeys, persist, None
        self.pet_prefs = (self.store.get("pet") if hasattr(self.store, "get") else None) or {}
        self.status, self.ptt_ok, self.stt = {}, None, {}
        self.voice: dict = {}             # Core's voice settings (GET /api/voice; VOICE_CHANGED): who speaks, when, filming, debug mode
        self.rx_want, self.rx_turn, self.rx_shown, self.rx_totals, self.rx_view = None, 0, None, None, None  # the reflex strip's sentence
        self.rx_wake, self._rx_thread = threading.Event(), None
        self.setWindowTitle("Monday")
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*WINDOW_SIZE)
        root = filled(QWidget(), RAIL)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.screens: dict[str, Screen] = {cls.key: cls(self) for cls in SCREENS}
        self.rail = Rail(self)
        h.addWidget(self.rail)
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(2, 14, 10, 12)
        self.panel = Panel()
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(1, 1, 1, 1)
        self.stack = QStackedWidget()
        for s in self.screens.values():
            self.stack.addWidget(s)
        bar_box = QWidget()  # an agent asking, above whatever page shows (hidden, with its room, while none asks)
        bl = QHBoxLayout(bar_box)
        bl.setContentsMargins(28, 16, 18, 0)
        self.ask_bar = AskBar(self)
        self.ask_bar.setMaximumWidth(CONTENT_W)
        bl.addWidget(self.ask_bar, 1)
        bar_box.hide()
        pv.addWidget(bar_box)
        pv.addWidget(self.stack)
        hv.addWidget(self.panel)
        h.addWidget(holder, 1)
        self.setCentralWidget(root)
        self.start_screen = StartScreen(self.panel, self.pet_prefs)  # the start: the pet and "Starting…" until Core answers (2026-10-21)
        self.current = None
        bridge.event.connect(self.on_event)
        bridge.online.connect(self._online)
        bridge.waiting = lambda: self._starting() is not None  # (the supervisor starting Core: a page's call waits for it, 2026-10-28)
        self.install_package = self.restart_app = None  # (core/desktop.py's, when this is the app: Windows' installer, a fresh start)
        self.ear = Ear(bridge.core, self._mic_say, self._heard, self._send_spoken, self._woke) if ear else None
        from .native import Speaker
        self.speaker = Speaker(bridge.core, lambda why: bridge.later(lambda: self._mic_say(f"Not spoken: {why}")),
                               lambda st: bridge.later(lambda: self._voice_state(st))) if ear else None
        self.asked: dict = {}             # message id -> {"spoken", "said"}: what was asked from here, so its answer may be said
        self.said_tasks: set = set()      # tasks whose look at the screen was said as it was marked (_explain): their reply isn't said again
        self.mine: dict = {}              # client_id -> spoken, for commands sent from this process (window, notch, ear)
        self.last_partial = 0.0
        self._poll = QTimer(self)
        self._poll.setInterval(int(POLL_S * 1000))
        self._poll.timeout.connect(self.poll)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_mic)
        QShortcut(QKeySequence("Esc"), self, activated=self.stop_all)
        self.hide_on_close, self.quitting = False, False  # the desktop app's: closing leaves Monday in the tray; Quit closes it
        self._geo = QTimer(self)
        self._geo.setSingleShot(True)
        self._geo.setInterval(600)
        self._geo.timeout.connect(lambda: self.keep("qrect", [self.normalGeometry().x(), self.normalGeometry().y(),
                                                              self.normalGeometry().width(), self.normalGeometry().height()]))
        self.show_page("home")

    def keep(self, key, value):
        """One of the window process's own choices (the pet, the keys, the notch's place) into data/desktop.json, read fresh first so
        one part saving never drops another's (desktop.load_state); only when this window is the desktop app's (persist)."""
        if value is None:
            self.store.pop(key, None)
        else:
            self.store[key] = value
        if self.persist:
            from . import desktop
            st = desktop.load_state()
            if value is None:
                st.pop(key, None)
            else:
                st[key] = value
            desktop.save_state(st)
        if key == "pet" and self.notch is not None and hasattr(self.notch, "set_prefs"):
            self.notch.set_prefs(value)
        if key == "pet" and getattr(self, "pet_changed", None):  # the app's icon follows the pet (core/desktop.py apply_icon)
            self.pet_changed(value)

    # pages
    def pages_listed(self):
        return [(cls.section, cls.key, cls.icon, cls.name) for cls in SCREENS]

    def show_page(self, key):
        s = self.screens[key]
        if self.current not in (None, key):
            self.screens[self.current].leave()
        s.ensure()
        held = K.QApplication.focusWidget()
        if held is not None and self.stack.isAncestorOf(held):  # (hidden with the old page, its focus would go on to the new page's first
            held.clearFocus()                                  # control as if Tab was pressed: an orange ring nobody asked for)
        self.stack.setCurrentWidget(s)
        self.rail.lit(key)
        self.current = key
        s.load()

    def page(self) -> Screen:
        return self.screens[self.current]

    # Core
    def start(self):
        """Once shown for real: the stream, the poll, and the ear (always listening, unless MONDAY_MIC=0)."""
        self.bridge.start()
        self.poll()
        self._poll.start()
        self.bridge.get("/api/voice", self._got_voice)
        if self.speaker and sys.platform == "win32":  # this computer's voices, for "speak in a Windows voice" (the old window's)
            from .native import windows_voices
            self.bridge.run(lambda: (windows_voices(), None), lambda v, e: self.bridge.post("/api/voices", {"voices": v}) if v else
                            self._mic_say(f"Windows' voices couldn't be read: {e}") if e else None)
        if self.ear and os.environ.get("MONDAY_MIC", "1") != "0":
            self.ear.start()

    def poll(self):
        self.bridge.get("/api/status", self._got_status)
        self.bridge.get("/api/stt", self._got_stt)

    def update_now(self, said=lambda words: None):
        """The owner's Update (the notch's card, Settings -> General -> Updates; docs/adr/0010): Core copies the package and checks it
        against the owner's signature, then this app opens Windows' installer on it and closes (install_package), or starts again on the code
        on disk (restart_app). said(words): what stopped it, or that the app can't do it from here."""
        def got(d, err):
            act = (d or {}).get("action")
            if err:
                return said(f"Not updated: {err}")
            if act == "install" and self.install_package:
                said(f"Opening Windows' installer for Monday {d.get('version')}…")
                return self.install_package(d["path"])
            if act == "restart" and self.restart_app:
                return self.restart_app()
            said("This window can't do that by itself: quit Monday from the tray and start it again.")
        self.bridge.post("/api/update/install", {}, got, timeout=180)

    def _starting(self):
        """core/desktop.py's supervisor is starting (or restarting) Monday Core: its words for the rail, else None."""
        phase = getattr(self, "core_phase", None)
        return f"Monday Core is {phase}…" if phase in ("starting", "restarting") else None

    def _got_status(self, d, err):
        if err:
            self.rail.pill.set(*(("wait", self._starting()) if self._starting() and err.startswith("Monday Core") else
                                 ("bad", "Monday Core is offline" if "unreachable" in err else f"Failed: {err}")))
            return
        self.status = d
        self.rail.name.setText(d.get("name") or "Monday")
        self.rail.version.setText(f"Monday {d.get('version')}" if d.get("version") else "")
        self.setWindowTitle(d.get("name") or "Monday")
        self._pill()

    def _got_stt(self, d, err):
        self.stt = d or {}
        if self.ear and getattr(self.ear, "lost", False) and not self.ear.on and (d or {}).get("available"):  # the ear turned itself off on a
            self.ear.back()  # lasting error (a Core reload ends its engine, 2026-10-28): Core answers again, so it listens again (native.EAR_BACK_S)
        self._pill()
        home = self.screens.get("home")
        if home is not None and home.built:  # Home's "Listen on this computer" line says the same step as the rail
            home.mic_changed(bool(self.ear and self.ear.on))

    def _pill(self):
        """The rail's state, most important first: Core away, asleep, waiting for the owner, working, the ear."""
        st = self.status
        if self.bridge.state in ("OFFLINE",) and not st:
            return self.rail.pill.set(*(("wait", self._starting()) if self._starting() else ("bad", "Monday Core is offline")))
        if st.get("asleep"):
            return self.rail.pill.set("off", "Asleep: two claps, a snap or a whistle wake it")
        waiting = len(st.get("pending_permissions") or [])
        if waiting:
            return self.rail.pill.set("wait", f"{waiting} approval{'s' if waiting != 1 else ''} waiting for you")
        running = len(st.get("running") or [])
        if running:
            return self.rail.pill.set("info", f"Working on {running} task{'s' if running != 1 else ''}")
        on = bool(self.ear and self.ear.on)
        self.rail.pill.set("ok" if on else "off", mic_line(self.stt, self.ptt_ok) if on and self.stt else
                           ("Listening" if on else "Mic lost: trying again every 15 s" if self.ear and getattr(self.ear, "lost", False)
                            else "Mic is off: Ctrl+M turns it on"))

    def _online(self, s):
        if s != "ONLINE":
            self._down = True
            self.rail.pill.set(*(("wait", self._starting()) if self._starting() else
                                 ("wait" if s == "RECONNECTING" else "bad", "Reconnecting to Monday Core…" if s == "RECONNECTING"
                                  else "Monday Core is offline")))
        else:
            self.poll()
            self.start_screen.done()  # Core answers: the start screen leaves (a fade), the pages show
            if getattr(self, "_down", False) and self.current is not None:  # back after an outage: the page on show is loaded again (it loaded
                self._down = False                                            # while Core was away and stayed on "Monday Core is unreachable":
                self.page().load()                                            # Statistics, Voice, History after a Core restart, 2026-10-20)

    def on_event(self, ev):
        t, d = ev.get("type"), ev.get("data") or {}
        if t in ("AGENT_ASK", "AGENT_ASK_DONE"):
            self.ask_bar.on_event(t, d)
        if t in ("HELLO", "CORE_STATE", "PERMISSION_REQUIRED", "PERMISSION_GRANTED", "PERMISSION_DENIED", "SLEEP", "WAKE") or \
                t in ("TASK_STARTED", "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED"):
            self.bridge.get("/api/status", self._got_status)
        if t == "MESSAGE_CREATED" and d.get("message") and not (d["message"].get("meta") or {}).get("chatroom") \
                and d["message"].get("source") != "preflight":
            m = d["message"]
            if m.get("role") == "user" and m.get("client_id") in self.mine and m.get("id") is not None:
                self.asked.setdefault(m["id"], {"spoken": self.mine[m["client_id"]], "said": None})
                if self.notch is not None:  # (the notch's closed bar shows its answer too; 2026-10-27: a spoken question's never did)
                    self.notch.state.asked.add(m["id"])
            else:
                self._maybe_say(m)
        elif t == "VOICE_CHANGED" and isinstance(d.get("voice"), dict):
            self._got_voice({"settings": d["voice"]}, None)
        elif t == "AWAKE":  # two claps or a snap, awake (2026-10-20 night): Monday's ear is open, as after its name said alone
            self._yes(f"Yes? I'm listening ({d.get('words') or 'you called'}): say it within {float(d.get('seconds') or 8):g} s")
        elif t == "MICROPHONE" and self.ear and d.get("ok") and hasattr(self.ear, "back"):  # the ear has a lost microphone back (2026-10-27)
            self.ear.back(force=True)
            self._got_status(self.status, None) if getattr(self, "status", None) else None
        elif t == "SCREEN_LOOK":  # a look at the screen begins (executor._explain_screen): the chat goes, so the screen is seen and marked
            self._clear_for_look()
        elif t == "SCREEN_EXPLAIN" and self.notch is not None:  # its explanation, said while each mark is drawn as its part is said
            self._explain(d)
        elif t == "VOICE_CONTROL" and self.speaker:
            self.speaker.control(d.get("op"))
            if d.get("op") == "stop_speaking" and self.notch is not None:  # ("stop talking": the marks it was explaining go too)
                self.notch.marks_fade()
        elif t == "SLEEP" and self.ear and self.ear.on:  # asleep: the ear closes (two claps, a snap or a whistle wake Monday)
            self.toggle_mic()
        for s in self.screens.values():
            if s.built and (s.key == self.current or getattr(s, "always", False)):
                s.on_event(ev)

    # speaking (native.Speaker: the voice Core holds; replies said by native.to_say's rules)
    def _got_voice(self, d, err):
        if not err and d:
            self.voice = d.get("settings") or {}
            if self.speaker:
                self.speaker.settings = self.voice
            chat = self.screens.get("chat")
            if chat is not None and chat.built:
                chat.reflex.setVisible(self.debug())
            home = self.screens.get("home")
            if home is not None and home.built:  # Home's "Only after “Monday, …” or “okay Monday”" switch
                home.wake_changed()

    # debug mode's reflex strip (the old HUD's): Jev's read of each sentence while it is said (POST /api/prepass, Monday.prepass). It only
    # informs: nothing here sends, holds or blocks anything; each read costs Jev tokens, so none is asked for outside debug mode.
    def debug(self):
        return self.voice.get("interaction_mode") == "debug"

    def _reflex_feed(self, text, final, read=True):
        """The ear's words (UI thread), in debug mode: the sentence so far for _reflex_loop; words after a finished sentence begin the next.
        read False: filming mode's gate reads this finished sentence itself (one read). -> the sentence's turn; None outside debug mode."""
        if not text or not self.debug():
            return None
        if self.rx_want is None or self.rx_want[2]:
            self.rx_turn += 1
        self.rx_want = (self.rx_turn, text, final, read)
        if self._rx_thread is None:
            self._rx_thread = threading.Thread(target=self._reflex_loop, daemon=True, name="reflex")
            self._rx_thread.start()
        self.rx_wake.set()
        return self.rx_turn

    def _reflex_loop(self):
        """One read at a time, the latest words winning: the words so far at most every PREPASS_EVERY_S, once there are PREPASS_MIN_WORDS
        and only when they changed; the finished sentence at once. Never outside debug mode."""
        from .native import PREPASS_EVERY_S, PREPASS_MIN_WORDS
        done, at = None, 0.0
        while not self.quitting:
            self.rx_wake.clear()
            want = self.rx_want
            if want is None or want == done or not self.debug():
                self.rx_wake.wait()
                continue
            turn, text, final, read = want
            if not read or (not final and len(text.split()) < PREPASS_MIN_WORDS):
                done = want
                continue
            wait = at + PREPASS_EVERY_S - time.monotonic()
            if wait > 0 and not final:
                time.sleep(wait)
                continue  # the words may have moved on meanwhile
            at, done = time.monotonic(), want
            r, err = self.bridge.core.call("POST", "/api/prepass", {"text": text, "final": final})
            self.bridge.later(lambda t=turn, r=r, err=err: self._reflex_draw(t, r, err))

    def _reflex_draw(self, turn, r, err):
        """Core's answer about one sentence, drawn; one about a sentence the owner has moved on from is dropped."""
        from .native import reflex_view
        if self.rx_want and self.rx_want[0] != turn:
            return
        if turn != self.rx_shown:  # the sentence's first read: the last one's numbers and receipts go
            self.rx_shown, self.rx_totals = turn, {"cost": 0.0, "prev": {}, "receipts": {}}
        self.rx_view = reflex_view(r, err, self.rx_totals)
        chat = self.screens.get("chat")
        if chat is not None and chat.built:
            chat.reflex.set(self.rx_view)

    def _maybe_say(self, m):
        """A reply to something asked from here, out loud when Core's voice settings say so (assistant mode: every reply, briefly;
        the silent modes: only the answer to a question asked out loud); never within 2 s of words still being heard."""
        from .native import to_say
        if not self.speaker or time.monotonic() - self.last_partial < 2.0 or m.get("task_id") in self.said_tasks:
            return  # (a look at the screen: its explanation was said as its marks were drawn, _explain)
        text = to_say(m, self.asked, self.voice.get("interaction_mode") or "pure_agent")
        if text and self.speaker.enabled():
            self.speaker.say(text, reply_to=(m.get("meta") or {}).get("reply_to"))

    def _clear_for_look(self):
        """A look at the screen begins: Monday's chat goes so the screen is clear to see and mark (the owner, 2026-10-29: "when it marks it
        should close the chat window"): the notch closes, and this window goes down when it is the one in front. Only when Monday will
        say the explanation: with its voice off the chat stays, where the answer is read."""
        if not (self.speaker and self.speaker.enabled()):
            return
        if self.notch is not None and self.notch.opened:
            self.notch.close_notch()
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.showMinimized()

    def _explain(self, d):
        """A look's explanation said out loud while its marks come, each as its part is said: `at` is how far into the words (overlay.cues),
        the speech's length from its audio (Speaker's cue). With the voice off, or no speech within LOOK_VOICE_WAIT_MS, at reading pace.
        Its reply isn't said again (said_tasks)."""
        from .overlay import GRACE_S, read_s
        n, marks, at, said = self.notch, d.get("marks") or [], d.get("at") or [], d.get("say") or ""
        if marks:
            n.marks_show(marks, d.get("avoid"), d.get("front"), hold=600, staged=True)
        cued = []

        def come(seconds, said_aloud):
            if cued:
                return
            cued.append(seconds)
            for i, f in enumerate(at):
                QTimer.singleShot(int(max(0.0, float(f)) * seconds * 1000), lambda i=i: n.marks_reveal(i))
            if not said_aloud:  # (said out loud, the speech's end takes them away: Marks.speaking)
                n.marks_hold(seconds + GRACE_S + 1.0)
        if said and self.speaker and self.speaker.enabled():
            if d.get("task"):  # (never None: every reply without a task would go unsaid)
                self.said_tasks.add(d["task"])
            self.speaker.say(said, cue=lambda secs: self.bridge.later(lambda: come(secs or len(said) / 14.0, True)))
            QTimer.singleShot(LOOK_VOICE_WAIT_MS, lambda: come(read_s(said), False))
        else:
            come(read_s(said), False)

    def _voice_state(self, st):
        """The speaker started or stopped: the notch's face, and Core told (idle is "listening" while the ear is open)."""
        if self.notch is not None:
            self.notch.state.speaking = st == "speaking"
            self.notch.marks_speaking(st == "speaking")  # (a look's marks stay while it is said, and go when it ends)
            self.notch.refresh()
        on = bool(self.ear and self.ear.on)
        self.bridge.post("/api/voice", {"state": "listening" if st == "idle" and on else st})

    def command(self, text, attachments=(), timing=None, utterance=None, spoken=False, done=None, answered=None):
        """Something said or typed to Monday (POST /api/command: Jev, then the work; one pipeline for the window, the notch and the
        ear). What it was is kept, so its answer can be said out loud. done(message, err); answered(Core's whole answer, err)."""
        cid = uuid.uuid4().hex
        self.mine[cid] = spoken  # the stream may bring the reply before this post returns: its MESSAGE_CREATED notes it first

        def got(d, err):
            m = (d or {}).get("message") or {}
            if not err and m.get("id") is not None:
                self.asked.setdefault(m["id"], {"spoken": spoken, "said": None})
                if self.notch is not None:  # (the notch's closed bar shows its answer too; 2026-10-27: a spoken question's never did)
                    self.notch.state.asked.add(m["id"])
            if done:
                done(m, err)
            if answered:
                answered(d or {}, err)
        self.bridge.post("/api/command", {"text": text, "client_id": cid, "attachments": list(attachments), "timing": timing,
                                          "utterance": utterance}, got)

    # the ear (native.Ear: Core's microphone, driven from here)
    def toggle_mic(self):
        if not self.ear:
            return
        if self.ear.on:
            self.ear.stop()
        else:
            self.ear.start()
        self._pill()
        for s in self.screens.values():
            if s.built and hasattr(s, "mic_changed"):
                s.mic_changed(self.ear.on)

    def _mic_say(self, words):
        """A line from the ear or the speaker (any thread): on the rail's pill."""
        self.bridge.later(lambda: self.rail.pill.set("wait", words))

    def _heard(self, text, final):
        """The ear's words (its thread): the chat's line above the box and the notch's "Hearing you"."""
        def show():
            if not final:
                self.last_partial = time.monotonic()  # mid-sentence: a reply waits (_maybe_say)
                self._reflex_feed(text, False)  # debug mode: Jev's read of the words so far
            chat = self.screens.get("chat")
            if chat is not None and chat.built:
                chat.heard(text, final)
            if self.notch is not None:  # speech began (no words yet), words, the sentence sent (notch.Notch.heard's rules)
                st = self.notch.state
                st.last = time.monotonic()
                if text and not final:
                    st.heard = text
                st.heard_at = float("-inf") if final else time.monotonic()
                self.notch.refresh()
        self.bridge.later(show)

    def _send_spoken(self, text, marks, sid):
        """A sentence the ear heard whole (its thread): not Monday's own voice coming back, through the filming gate when that is on,
        then sent like anything typed (spoken, so its answer may be said). With the wake phrase on (voice `wake`), Core lets in only
        what follows "okay Monday", so the filming gate's read isn't needed (or paid for)."""
        def go():
            from .native import is_echo
            # push-to-talk is said to Monday on purpose: not gated
            gated = bool(self.voice.get("filming")) and not self.voice.get("wake", True) and "stt_held" not in marks
            turn = self._reflex_feed(text, True, read=not gated)  # debug mode: a gated sentence's one read is the gate's
            sp = self.speaker
            if sp and sp.talking() and is_echo(text, sp.text):
                return self._mic_say(f"Heard my own voice; not sent: {text[:80]}")
            if gated:
                return self._filming_gate(text, marks, sid, turn)
            self._spoken(text, marks, sid)
        self.bridge.later(go)

    def _spoken(self, text, marks, sid):
        """Sent; then, as Core answered: not for Monday (no "okay Monday": left alone, and Monday goes on talking), "okay Monday" alone
        (it listens for the next sentence), or taken (the owner talking over Monday: it stops and listens)."""
        marks["sent"] = time.time() * 1000

        def got(d, err):
            name = self.status.get("name") or "Monday"
            if err:
                self._mic_say(f"Not sent: {err}")
            elif d.get("ignored"):  # (a clap or a snap let it in, and Jev said it wasn't said to Monday: Core's own words, why)
                self._mic_say(str(d["ignored"]) if d.get("gesture") else f"Not for me (no “{name}, …” or “okay {name}”): {text[:60]}")
            elif d.get("awake"):
                self._yes(f"Yes? I'm listening: say it within {d['awake']:g} s")
            elif self.speaker and self.speaker.talking():
                self.speaker.control("stop_speaking")  # the owner talking over Monday: it stops and listens
        self.command(text, timing=marks, utterance=sid, spoken=True, answered=got)

    def _yes(self, words):
        """Monday heard its wake phrase: the mic line says so, and the notch shows "Yes?" with its blip (when its sounds are on)."""
        self._mic_say(words)
        if self.notch is not None:
            self.notch.state.heard, self.notch.state.heard_at = "Yes?", time.monotonic()
            self.notch._sound("prompt")
            self.notch.refresh()

    def _woke(self, carried):
        """The ear heard "okay Monday" (its thread). A sentence let in by the phrase said alone just before is quiet: "Yes?" was said."""
        if not carried:
            self.bridge.later(lambda: self._yes("Yes? I'm listening"))

    def _filming_gate(self, text, marks, sid, turn=None):
        """Filming mode (the old window's): an open-mic sentence goes to Monday only when Core's read says it was for Monday. turn: the
        reflex strip's sentence in debug mode, which draws this read."""
        self._mic_say(f"Checking whether that was for me: {text[:60]}")

        def got(d, err):
            if turn is not None:
                self._reflex_draw(turn, d, err)
            if err:
                return self._mic_say("Filming mode: I could not check that with Monday Core, so I stayed quiet.")
            if (d or {}).get("verdict") == "answer" or (d or {}).get("filming") is False:
                self._spoken(text, marks, sid)
            else:
                self._mic_say(f"Filming mode: {((d or {}).get('why') or 'not for me').rstrip('.')}. Not sent: {text[:60]}")
        self.bridge.post("/api/prepass", {"text": text, "final": True}, got)

    def stop_all(self):
        """Esc: everything Monday is doing stops (each running task through Core); what Core confirmed is what's said."""
        if self.notch is not None:
            self.notch.marks_fade()  # (and what it was showing on the screen)

        def got(d, err):
            if err:
                return self.rail.pill.set("bad", f"Couldn't stop: {err}")
            tids = [t.get("id") for t in (d or {}).get("tasks") or []]
            if not tids:
                return self.rail.pill.set("off", "Nothing was running")
            done = []

            def one(_d, e, tid):
                done.append(e)
                if len(done) == len(tids):
                    ok = sum(x is None for x in done)
                    self.rail.pill.set("off" if ok == len(tids) else "bad", f"Stopped {ok} of {len(tids)}" + (
                        f": {next(x for x in done if x)}" if ok < len(tids) else ""))
            for tid in tids:
                self.bridge.post(f"/api/tasks/{tid}/stop", None, lambda d_, e, tid=tid: one(d_, e, tid))
        self.bridge.get("/api/tasks?status=RUNNING,PLANNING,QUEUED,STARTING,WAITING,WAITING_FOR_PERMISSION,WAITING_FOR_USER", got)

    def moveEvent(self, e):
        if self.persist and self.isVisible():
            self._geo.start()
        super().moveEvent(e)

    def resizeEvent(self, e):
        if self.persist and self.isVisible():
            self._geo.start()
        super().resizeEvent(e)

    def closeEvent(self, e):
        if self.hide_on_close and not self.quitting:  # Monday keeps running in the tray, the notch with it
            e.ignore()
            self.hide()
            return
        if self.ear:
            self.ear.stop()
        super().closeEvent(e)

    def hideEvent(self, e):
        if self.current:  # hidden to the tray or minimised: a live page stops until it shows again
            self.page().leave()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if self.current and getattr(self.page(), "live", False):
            self.page().load()


# -- pages ---------------------------------------------------------------------------------------------------------------------
class ActivityRow(QWidget):
    """One line of Monday's history: when, its tone, what happened and to what."""

    def __init__(self, item: dict):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 6, 4, 6)
        h.setSpacing(10)
        when = label(ago(item.get("ts") or time.time()), 12, 400, TEXT_3)
        when.setFixedWidth(64)
        h.addWidget(when)
        h.addWidget(Dot(TONES.get(item.get("tone"), "off"), 8), 0, Qt.AlignmentFlag.AlignVCenter)
        what = item.get("what") or (item.get("type") or "").replace("_", " ").capitalize()
        h.addWidget(label(what, 13, 600))
        detail = K.Elided(item.get("detail") or "", 12.5, 400, TEXT_2)
        detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        h.addWidget(detail, 1)
        self.setAccessibleName(f"{what}: {item.get('detail') or ''}, {ago(item.get('ts') or time.time())}")


class StatLine(QWidget):
    """A big number and its words on one line (the recording's "0 words dictated")."""

    def __init__(self, words):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.num = label("–", 30, 700)
        self.words = label(words, 13, 400, TEXT_2)
        # one baseline, as the recording's "0 words dictated" (a box layout ignores AlignBaseline: the words sit as low as the number's)
        self.words.setContentsMargins(0, 0, 0, max(0, self.num.fontMetrics().descent() - self.words.fontMetrics().descent()))
        h.addWidget(self.num, 0, Qt.AlignmentFlag.AlignBottom)
        h.addWidget(self.words, 0, Qt.AlignmentFlag.AlignBottom)
        h.addStretch(1)

    def set(self, n, words=None):
        self.num.setText("N/A" if n is None else str(n))
        if words:
            self.words.setText(words)
        self.setAccessibleName(f"{self.num.text()} {self.words.text()}")


class HomeScreen(Screen):
    """Home (the recording's dashboard): a greeting and the one thing to do from here (ask Monday); talking to Monday from any app
    (the ear: on or off, who reads the words); Monday's history; today's numbers; what the owner's agents did since yesterday."""
    key, icon, name, section = "home", "home", "Home", "Monday"

    def build(self):
        self.ask = Button("Ask Monday", "primary", dot=True, name="Ask Monday: open the chat")
        self.ask.clicked.connect(lambda: self.win.show_page("chat") if "chat" in self.win.screens else None)
        self.ask.setEnabled("chat" in [c.key for c in SCREENS])
        self.hero = Hero(greeting(), "Say what you want. Monday does it on this computer, and shows you what really happened.", "warm",
                         ("white", "orange", "pink"), action=self.ask)
        self.page.add(self.hero)
        self.page.lay.addSpacing(18)  # (the recording's dashboard leaves twice the gap under its banner)
        # talking to Monday from any app: the ear
        ear = GlowCard("warm", 0.75)
        ear.lay.addWidget(label("Talk to Monday from any app", 16.5, 700))
        self.ear_words = label("", 13, 400, "#b8bcc1", wrap=True)
        ear.lay.addWidget(self.ear_words)
        ear.lay.addSpacing(8)
        row = QHBoxLayout()
        row.addWidget(label("Listen on this computer", 13.5, 600))
        row.addStretch(1)
        self.mic = K.Toggle("Listen on this computer (Ctrl+M)", bool(self.win.ear and self.win.ear.on))
        self.mic.toggled.connect(lambda on: self.win.toggle_mic() if bool(self.win.ear and self.win.ear.on) != on else None)
        self.mic.setEnabled(self.win.ear is not None)
        row.addWidget(self.mic)
        ear.lay.addLayout(row)
        self.mic_words = label("", 12.5, 400, "#9fa3a8", wrap=True)
        ear.lay.addWidget(self.mic_words)
        ear.lay.addSpacing(6)
        # the wake phrase (voice `wake`, Core's rule): the open microphone acts only on what starts with Monday's name, "Monday, …" or
        # "okay Monday" (router.wake_rest; the owner, 2026-10-20: the bare name)
        roww = QHBoxLayout()
        roww.addWidget(label("Only after “Monday, …” or “okay Monday”", 13.5, 600))
        roww.addStretch(1)
        self.wake = K.Toggle("Only after “Monday, …” or “okay Monday”: on or off", True)
        self.wake.toggled.connect(lambda on: self.bridge.post("/api/voice/settings", {"wake": on}, lambda _d, err: (
            self.reader_note.say(f"Not changed: {err}"), self.wake_changed()) if err else None))
        roww.addWidget(self.wake)
        ear.lay.addLayout(roww)
        self.wake_words = label("", 12.5, 400, "#9fa3a8", wrap=True)
        ear.lay.addWidget(self.wake_words)
        ear.lay.addSpacing(6)
        row2 = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        self.reader_name = label("Who reads your words", 13.5, 600)
        self.reader_words = label("", 12.5, 400, "#9fa3a8", wrap=True)
        left.addWidget(self.reader_name)
        left.addWidget(self.reader_words)
        row2.addLayout(left, 1)
        options = [(k, n) for k, n, _ in READERS if k != "windows" or sys.platform == "win32"]
        self.reader = dropdown(options, None, "Who reads your words", 240)
        self.reader.activated.connect(self._pick_reader)
        row2.addWidget(self.reader, 0, Qt.AlignmentFlag.AlignVCenter)
        ear.lay.addLayout(row2)
        self.reader_note = Notice("", "red")
        ear.lay.addWidget(self.reader_note)
        self.page.add(ear)
        # the history, today's numbers, the brief
        below = QHBoxLayout()
        below.setSpacing(22)
        hist = QVBoxLayout()
        hist.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(_section("History"))
        top.addStretch(1)
        self.search = field("Search your history", "Search what you asked and what Monday did", width=280)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        top.addWidget(self.search)
        hist.addLayout(top)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        hist.addLayout(self.rows)
        self.empty = Empty("What Monday does shows here. Hold Ctrl+Space anywhere and speak, or ask in the chat.")
        hist.addWidget(self.empty)
        self.hist_note = Notice("", "red")
        hist.addWidget(self.hist_note)
        hist.addStretch(1)
        below.addLayout(hist, 1)
        side = QVBoxLayout()
        side.setSpacing(16)
        today = Card(up=True)
        today.setFixedWidth(268)
        today.lay.setSpacing(14)  # (the recording's numbers are ~54 apart)
        self.n_asked, self.n_done, self.n_streak = StatLine("requests today"), StatLine("tasks done today"), StatLine("days in a row")
        for w in (self.n_asked, self.n_done, self.n_streak):
            today.lay.addWidget(w)
        rule = K._Hairline()
        today.lay.addSpacing(8)
        today.lay.addWidget(rule)
        today.lay.addSpacing(6)
        self.kept = label("✓  Counted on this computer", 13, 600, GREEN_TEXT)
        today.lay.addWidget(self.kept)
        side.addWidget(today)
        brief = GlowCard("warm", 0.7)
        brief.setFixedWidth(268)
        brief.lay.addWidget(label("Since yesterday", 15, 700))
        self.brief = label("Reading your agents' logs…", 12.5, 400, "#b8bcc1", wrap=True)
        brief.lay.addWidget(self.brief)
        self.to_agents = label("", 12.5, 600, ORANGE)
        brief.lay.addWidget(self.to_agents)
        side.addWidget(brief)
        side.addStretch(1)
        below.addLayout(side)
        self.page.lay.addLayout(below)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_MS)
        self._search_timer.timeout.connect(self._search)

    def load(self):
        super().load()
        self.hero.title_label.setText(greeting())
        self.bridge.get("/api/activity?limit=%d" % HISTORY_ROWS, self._got_history)
        self.bridge.get("/api/stats?span=day", self._got_stats)
        self.bridge.get("/api/settings", self._got_settings)
        self.bridge.get("/api/stt", self._got_stt)
        self.bridge.get("/api/agents/brief", self._got_brief)
        self.mic_changed(bool(self.win.ear and self.win.ear.on))

    def on_event(self, ev):
        if ev.get("type", "").startswith(("TASK_", "PERMISSION_", "MEMORY_", "BROWSER_", "GOOGLE_")):
            self.stale()

    def mic_changed(self, on):
        self.mic.set_quiet(on)
        self.mic_words.setText(mic_line(self.win.stt, self.win.ptt_ok) if on and self.win.stt else
                               "Off: Monday hears nothing until you turn it on (Ctrl+M) or hold Ctrl+Space.")
        self.wake_changed()

    def wake_changed(self):
        """The wake phrase as Core holds it (voice `wake`; on until the owner turns it off)."""
        on, name = self.win.voice.get("wake", True) is not False, self.win.status.get("name") or "Monday"
        self.wake.set_quiet(on)
        self.ear_words.setText(f"Hold Ctrl+Space in any app, speak, let go: Monday does what you said. With the microphone on, start with "
                               f"“{name}, …”" + (f" or “okay {name} …”." if on else "."))
        self.wake_words.setText(f"On: what the microphone hears counts only when it starts with “{name}, …” or “okay {name}” (or right after "
                                f"“{name}” or “okay {name}” said alone); everything else, “stop” too, is left alone. Esc and Ctrl+Space "
                                f"always work." if on else
                                f"Off: it answers what starts with “{name}, …”, and runs exact commands it hears (open, close, stop…).")

    def _got_history(self, d, err):
        if self.search.text().strip():
            return
        self._fill([] if err else (d or {}).get("items") or [], err)

    def _fill(self, items, err=None):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        for it in items[:HISTORY_ROWS]:
            self.rows.addWidget(ActivityRow(it))
        self.empty.setVisible(not items and not err)
        self.hist_note.say(f"The history couldn't be read: {err}" if err else "")

    def _search(self):
        q = self.search.text().strip()
        if not q:
            return self.bridge.get("/api/activity?limit=%d" % HISTORY_ROWS, self._got_history)
        from urllib.parse import quote
        self.bridge.get(f"/api/search?q={quote(q)}&limit={HISTORY_ROWS}", lambda d, err, q=q: self._found(q, d, err))

    def _found(self, q, d, err):
        if q != self.search.text().strip():
            return
        d = d or {}
        items = [{"ts": t.get("created_at"), "what": f"Task · {str(t.get('status') or '').replace('_', ' ').lower()}",
                  "detail": t.get("title"), "tone": {"COMPLETED": "done", "FAILED": "bad"}.get(t.get("status"), "off")}
                 for t in d.get("tasks") or []]
        items += [{"ts": m.get("created_at"), "what": "You asked" if m.get("role") == "user" else "Monday said",
                   "detail": (m.get("text") or "").replace("\n", " ")[:200], "tone": "off"} for m in d.get("messages") or []]
        items.sort(key=lambda i: i["ts"] or 0, reverse=True)
        self._fill(items, err)
        if not items and not err:
            self.empty.say(f"Nothing matches “{q}”.", "idle")

    def _got_stats(self, d, err):
        if err:
            for w in (self.n_asked, self.n_done, self.n_streak):
                w.set(None)
            self.kept.setText("Failed: " + err)
            return
        days = d.get("days") or [{}]
        self.n_asked.set(d["requests"]["total"], "request today" if d["requests"]["total"] == 1 else "requests today")
        self.n_done.set(days[-1].get("done", 0), "task done today" if days[-1].get("done") == 1 else "tasks done today")
        self.n_streak.set(d.get("streak_days"), "day in a row" if d.get("streak_days") == 1 else "days in a row")
        self.kept.setText("✓  Counted on this computer")

    def _got_settings(self, d, err):
        choice = (d or {}).get("default_stt") or "browser"
        key = "local" if choice == "local" else "windows" if choice == "windows" else "google"
        i = self.reader.findData(key)
        if i >= 0:
            self.reader.setCurrentIndex(i)
        self._reader_words(key)
        self.reader_note.say(f"Who reads your words couldn't be read: {err}" if err else "")  # (an answer clears an earlier failure)

    def _reader_words(self, key):
        name, words = next((n, w) for k, n, w in READERS if k == key)
        self.reader_name.setText(name)
        self.reader_words.setText(words)

    def _pick_reader(self, _i):
        key = self.reader.currentData()

        def done(_d, err):
            if err:
                self.reader_note.say(f"Not changed: {err}")
                self.bridge.get("/api/settings", self._got_settings)
            else:
                self.reader_note.say("")
                self._reader_words(key)
        self.bridge.post("/api/settings", {"default_stt": key}, done)

    def _got_stt(self, d, err):
        if not err:
            self.win.stt = d or {}
        self.mic_changed(bool(self.win.ear and self.win.ear.on))

    def _got_brief(self, d, err):
        line = re.sub(r"^\s*since yesterday\s*:\s*", "", (d or {}).get("line") or "", flags=re.I)  # (the card's title says it already)
        self.brief.setText(f"Failed: {err}" if err else (line[:1].upper() + line[1:]) if line else
                           "Nothing from your agents' logs since yesterday began.")
        self.to_agents.setText("")


class DayBars(QWidget):
    """Requests per day as bars on a dotted baseline (the recording's "Words per day"): one series, so no legend; each bar says its
    day and count when pointed at; the whole series is the widget's accessible description (the table a screen reader gets)."""

    def __init__(self):
        super().__init__()
        self.days: list[dict] = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)  # (its card's height: the title stays on top)
        self.setMouseTracking(True)
        self.setAccessibleName("Requests per day")
        self._hover = -1

    def set(self, days):
        self.days = days or []
        self.setAccessibleDescription("; ".join(f"{d['date']}: {d['requests']} request{'s' if d['requests'] != 1 else ''}"
                                                for d in self.days) or "No days yet")
        self.update()

    def _geometry(self):
        r = QRectF(self.rect()).adjusted(4, 10, -4, -30)
        n = max(1, len(self.days))
        slot = r.width() / n
        return r, slot

    def mouseMoveEvent(self, e):
        r, slot = self._geometry()
        i = int((e.position().x() - r.left()) // slot) if self.days else -1
        i = i if 0 <= i < len(self.days) else -1
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            d = self.days[i]
            day = time.strftime("%a %d %b", time.strptime(d["date"], "%Y-%m-%d"))
            QToolTip.showText(e.globalPosition().toPoint(), f"{day}: {d['requests']} request{'s' if d['requests'] != 1 else ''}"
                              f" ({d['spoken']} spoken) · {d['tasks']} task{'s' if d['tasks'] != 1 else ''}, {d['done']} done"
                              + (f", {d['failed']} failed" if d["failed"] else ""), self)

    def leaveEvent(self, e):
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, slot = self._geometry()
        top = max([d["requests"] for d in self.days] + [1])
        pen = QPen(QColor("#3a3e43"), 1, Qt.PenStyle.DotLine)
        p.setPen(pen)
        p.drawLine(QPointF(r.left(), r.bottom() + 0.5), QPointF(r.right(), r.bottom() + 0.5))
        bar_w = max(2.0, min(28.0, slot - 4))
        p.setFont(font(11.5))
        step = max(1, math.ceil(len(self.days) / 8))
        for i, d in enumerate(self.days):
            x = r.left() + i * slot + (slot - bar_w) / 2
            if d["requests"]:
                h = max(3.0, (r.height() - 8) * d["requests"] / top)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#f1f2f4" if i == self._hover else "#c9ccd1"))
                rad = min(4.0, bar_w / 2)
                p.drawRoundedRect(QRectF(x, r.bottom() - h, bar_w, h + rad), rad, rad)  # rounded top, square on the baseline
                p.setBrush(QColor(K.CARD))
                p.drawRect(QRectF(x - 1, r.bottom() + 0.5, bar_w + 2, rad + 1))
            if i % step == 0 or i == len(self.days) - 1:
                p.setPen(QColor(TEXT_3))
                day = str(int(d["date"][8:]))
                p.drawText(QRectF(r.left() + i * slot - 10, r.bottom() + 8, slot + 20, 16), Qt.AlignmentFlag.AlignHCenter, day)
        p.end()


class StatsScreen(Screen):
    """Statistics (the recording's): what the owner asked, what Monday did, how long it worked and where; counted by Monday Core from
    its own database on this computer (GET /api/stats). Monday takes requests, not dictation: no words dictated, no time saved."""
    key, icon, name, section = "stats", "stats", "Statistics", "Monday"

    def build(self):
        self.span = Segmented([("week", "Week"), ("month", "Month"), ("all", "All")], "month", "Show the last")
        self.span.changed.connect(lambda _v: self.load())
        self.hero = Hero("Statistics", "What you asked, what Monday did, and where.", "green", ("yellow", "green", "white"), action=self.span)
        self.page.add(self.hero)
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.tiles = {}
        for key, words in (("requests", "Requests"), ("worked", "Time Monday worked"), ("success", "Done, of finished"),
                           ("streak", "Days in a row")):
            c = Card(pad=20, up=True)
            c.lay.setSpacing(2)
            c.lay.addWidget(label(words, 13, 400, TEXT_2))
            num = label("–", 26.5, 700)
            sub = label("", 12, 400, TEXT_3)
            c.lay.addWidget(num)
            c.lay.addWidget(sub)
            c.lay.addStretch(1)
            c.setMinimumHeight(118)
            self.tiles[key] = (num, sub, c)
            cards.addWidget(c, 1)
        self.page.lay.addLayout(cards)
        below = QHBoxLayout()
        below.setSpacing(16)
        chart = Card("Requests per day", up=True)
        self.bars = DayBars()
        chart.lay.addWidget(self.bars)
        below.addWidget(chart, 3)
        where = Card("Where Monday worked", up=True)
        self.where = QVBoxLayout()
        self.where.setSpacing(6)
        where.lay.addLayout(self.where)
        self.where_empty = Empty("Ask Monday to open an app or a site and it shows here.", "idle")
        where.lay.addWidget(self.where_empty)
        where.lay.addStretch(1)
        below.addWidget(where, 2)
        self.page.lay.addLayout(below)
        self.note = Notice("", "red")
        self.page.add(self.note)
        self.foot = label("Counted on this computer, never sent anywhere.", 12.5, 400, TEXT_3)
        self.page.add(self.foot)
        self.page.lay.addStretch(1)

    def load(self):
        super().load()
        self.bridge.get(f"/api/stats?span={self.span.value}", self._got)

    def on_event(self, ev):
        if ev.get("type") in ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED", "MESSAGE_CREATED"):
            self.stale()

    def _tile(self, key, num, sub):
        n, s, c = self.tiles[key]
        n.setText(num)
        s.setText(sub)
        c.setAccessibleName(f"{num} {sub}")

    def _got(self, d, err):
        if err:
            self.note.say(f"The numbers couldn't be read: {err}")
            for k in self.tiles:
                self._tile(k, "N/A", "")
            self.bars.set([])
            return
        self.note.say("")
        rq, t = d["requests"], d["tasks"]
        self._tile("requests", str(rq["total"]), f"{rq['typed']} typed · {rq['spoken']} spoken")
        self._tile("worked", duration(t["worked_s"]), f"on {t['total']} task{'s' if t['total'] != 1 else ''}"
                   + (f" · median {duration(t['median_run_s'])}" if t["median_run_s"] is not None else ""))
        rate = t["success_rate"]
        self._tile("success", "N/A" if rate is None else f"{round(rate * 100)} %",
                   f"{t['done']} done · {t['failed']} failed" + (f" · {t['stopped']} stopped" if t["stopped"] else ""))
        self._tile("streak", str(d["streak_days"]), "asked Monday something each day")
        self.bars.set(d["days"])
        while self.where.count():
            w = self.where.takeAt(0).widget()
            if w:
                w.deleteLater()
        from .native import AGENT_NAMES
        rows = [(o["target"].title(), o["n"], "opened") for o in d["opened"][:WHERE_OPENED]] + \
            [(AGENT_NAMES.get(a["agent"], words(a["agent"]).title()), a["n"], "tasks") for a in t["by_agent"][:WHERE_AGENTS]]
        top = max([n for _t, n, _k in rows] + [1])
        for target, n, kind in rows:
            self.where.addWidget(WhereRow(target, n, top, kind))
        more = max(0, len(d["opened"]) - WHERE_OPENED) + max(0, len(t["by_agent"]) - WHERE_AGENTS)
        if more:  # (the rest said, not dropped: the card stays about as tall as the chart beside it)
            self.where.addWidget(label(f"and {more} more", 12, 400, TEXT_3))
        self.where_empty.setVisible(not rows)


class WhereRow(QWidget):
    """An app, a site or an agent and how often: a thin bar against the most used."""

    def __init__(self, what, n, top, kind):
        super().__init__()
        self.what, self.n, self.top, self.kind = what, n, top, kind
        self.setFixedHeight(30)
        self.setAccessibleName(f"{what}: {n} {'times opened' if kind == 'opened' else 'tasks'}")
        self.setToolTip(self.accessibleName())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        p.setPen(QColor(TEXT))
        p.setFont(font(13))
        p.drawText(QRectF(0, 0, w * 0.5, 18), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.what, Qt.TextElideMode.ElideRight, int(w * 0.5)))
        p.setPen(QColor(TEXT_3))
        p.setFont(font(12))
        p.drawText(QRectF(w * 0.5, 0, w * 0.5, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   f"{self.n} {'opened' if self.kind == 'opened' else 'tasks'}")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#22262a"))
        p.drawRoundedRect(QRectF(0, 22, w, 4), 2, 2)
        p.setBrush(QColor("#c9ccd1" if self.kind == "opened" else ORANGE))
        p.drawRoundedRect(QRectF(0, 22, max(4.0, w * self.n / self.top), 4), 2, 2)
        p.end()


def component_dot(status):
    """A component's status (GET /api/status components) -> its dot. Only what Core said works is green; an unknown word stays grey
    (2026-10-19: the old window counted "unknown" as ready)."""
    s = str(status or "").lower()
    if s in ("ready", "available", "ok", "online", "connected", "running", "verified", "configured", "on"):
        return "ok"
    if s in ("starting", "busy", "working", "checking"):
        return "wait"
    if s in ("not_configured", "not_logged_in", "not_installed", "offline", "error", "failed", "expired", "blocked", "unavailable"):
        return "bad"
    return "off"


def words(s):
    return str(s or "N/A").replace("_", " ")


class Form(QWidget):
    """Rows of a card laid out on one grid, so their labels, fields and buttons line up as the recording's Integrations do."""

    def __init__(self, stretch: int = 2):
        super().__init__()
        from PySide6.QtWidgets import QGridLayout
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(stretch, 1)
        self.n = 0

    def add(self, *cells, span_last=False):
        for col, w in enumerate(cells):
            if w is not None:
                self.grid.addWidget(w, self.n, col, 1, (5 - col) if span_last and col == len(cells) - 1 else 1,
                                    Qt.AlignmentFlag.AlignVCenter)
        self.n += 1

    def clear(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.n = 0


def name_dot(name, state="off", tip=""):
    """A name with its dot before it (the recording's "● Stripe")."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    d = Dot(state, 8, tip)
    h.addWidget(d)
    h.addWidget(label(name, 13, 500))  # (the recording's "● Stripe": 13, not bold)
    h.addStretch(1)
    w.dot = d
    w.setMinimumWidth(150)
    return w


class ShortcutsScreen(Screen):
    """Shortcuts (the recording's): each global shortcut with its switch and keys, what the keys type on this keyboard instead
    (Ctrl+Alt is AltGr on many), why one isn't on (another app holds it, the OS doesn't allow it), Reset to defaults; then the keys
    that work inside Monday's window and notch. Kept in data/desktop.json ("keys"); taken by the desktop app's Hotkeys."""
    key, icon, name, section = "shortcuts", "keyboard", "Shortcuts", "Notch"

    def build(self):
        from . import hotkeys
        self.hk = hotkeys
        self.page.add(Hero("Shortcuts", "The keys that open the notch and talk to Monday, from any app.", "indigo", ("purple", "white")))
        card = Card("Shortcuts", "Work from any app. Click a shortcut's keys to change them, then press the new keys: Esc cancels, "
                                 "Backspace removes them.")
        self.info = label("", 12.5, 400, TEXT_3, wrap=True)
        card.lay.addWidget(self.info)
        self.note = Notice("", "amber")
        card.lay.addWidget(self.note)
        card.lay.addSpacing(4)
        card.lay.addWidget(_section("From anywhere"))
        rows = QVBoxLayout()
        rows.setSpacing(6)  # (the recording's rows: 32 px apart)
        card.lay.addLayout(rows)
        self.rows = {}
        for a, name, *_rest in hotkeys.ACTIONS:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(12)
            sw = K.Toggle(f"{name}: on or off")
            sw.toggled.connect(lambda on, a=a: self._switch(a, on))
            h.addWidget(sw)
            col = QVBoxLayout()
            col.setSpacing(0)
            title = label(name, 12.5)  # (the recording's rows: 12.5)
            state = label("", 12, 400, TEXT_3)
            col.addWidget(title)
            col.addWidget(state)
            h.addLayout(col, 1)
            types = K.Chip("", "amber", small=True)  # (the recording's 59 x 19)
            h.addWidget(types)
            chip = K.KeyChip("", f"{name}: its keys (click to change)")
            chip.clicked.connect(lambda _=False, a=a: self._capture(a))
            h.addWidget(chip)
            rows.addWidget(row)
            self.rows[a] = (sw, title, state, types, chip)
        card.lay.addSpacing(10)
        reset = Button("Reset to defaults", "secondary")
        reset.clicked.connect(self._reset)
        rr = QHBoxLayout()
        rr.addWidget(reset)
        rr.addStretch(1)
        card.lay.addLayout(rr)
        card.lay.addSpacing(10)
        card.lay.addWidget(_section("In Monday's window and notch"))
        for words_, keys in (("Turn the microphone on or off", "Ctrl+M"), ("Stop everything Monday is doing", "Esc"),
                             ("Send what you typed", "Enter"), ("A new line in what you type", "Shift+Enter"),
                             ("Clear the notch's prompt, then close it", "Esc")):
            r = QHBoxLayout()
            r.addWidget(label(words_, 12.5))
            r.addStretch(1)
            c = K.KeyChip(keys, f"{words_}: {keys} (fixed)")
            c.setEnabled(False)
            c.set_state(off=True)
            r.addWidget(c)
            card.lay.addLayout(r)
        self.page.add(card)
        self.page.lay.addStretch(1)
        self.capturing = None

    def load(self):
        super().load()
        self.km = self.hk.keymap(self.win.store.get("keys"))
        hk = self.win.hotkeys
        if hk is not None:
            hk.changed.connect(self._show, Qt.ConnectionType.UniqueConnection)
        self._show()

    def _show(self):
        hk = self.win.hotkeys
        for a, (sw, title, state, types, chip) in self.rows.items():
            v = self.km[a]
            sw.set_quiet(v["on"])
            title.setStyleSheet(f"color: {TEXT if v['on'] else TEXT_2}; background: transparent;")
            chip.setText(v["keys"] or "Add keys")
            ch = self.hk.types_char(v["keys"]) if v["keys"] else None
            types.setText(f"Types “{ch}”" if ch else "")
            types.setVisible(bool(ch))
            types.setToolTip(f"On this keyboard {v['keys']} also types “{ch}”: while Monday holds the keys, it can't be typed" if ch else "")
            st, why = hk.status(a) if hk is not None else ("", "")
            chip.set_state(off=not v["on"] or not v["keys"], failed=st == "failed", capturing=self.capturing == a)
            state.setText("" if st in ("off", "") or (st == "on" and "holds" not in why) else why)  # on with other keys: which, and why
            state.setStyleSheet(f"color: {K.RED_TEXT if st == 'failed' else TEXT_3}; background: transparent;")
            state.setVisible(bool(state.text()))
        self.info.setText("" if hk is not None else "Monday's desktop app takes these keys while it runs; this window was opened "
                                                     "without it, so none is taken now.")
        self.info.setVisible(hk is None)

    def _save(self):
        self.win.keep("keys", self.km)
        if self.win.hotkeys is not None:
            self.win.hotkeys.configure(self.km)
        self._show()

    def _switch(self, a, on):
        if on and not self.km[a]["keys"]:
            self.rows[a][0].set_quiet(False)
            return self._capture(a)
        self.km[a]["on"] = on
        self._save()

    def _reset(self):
        if K.confirm(self, "Reset the shortcuts?", "Every shortcut goes back to Monday's own keys and switches.", "Reset"):
            self.km = self.hk.defaults()
            self.note.say("")
            self._save()

    # setting new keys: Monday lets go of all its keys meanwhile, so the keys reach this page
    def _capture(self, a):
        self.capturing = a
        if self.win.hotkeys is not None:
            self.win.hotkeys.suspend(True)
        self.note.say(f"Press the new keys for “{self.hk.NAMES[a]}”. Esc cancels, Backspace removes them.", "amber")
        self._show()
        self.grabKeyboard()

    def _done(self, note="", tone="amber"):
        self.releaseKeyboard()
        self.capturing = None
        if self.win.hotkeys is not None:
            self.win.hotkeys.suspend(False)
        self.note.say(note, tone)
        self._show()

    def event(self, e):
        if getattr(self, "capturing", None) and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()  # while new keys are being set, they are keys, not the window's shortcuts (Esc, Ctrl+M)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        a = self.capturing
        if not a:
            return super().keyPressEvent(e)
        key, mods = e.key(), e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return  # the modifiers first: wait for the key itself
        if key == Qt.Key.Key_Escape and not mods:
            return self._done()
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not mods:
            self.km[a] = {"keys": "", "on": False}
            self._save()
            return self._done(f"“{self.hk.NAMES[a]}” has no keys now.", "plain")
        keys = QKeySequence(mods.value | key).toString(QKeySequence.SequenceFormat.PortableText)
        if not self.hk.parse(keys):
            return self._done(f"{keys or 'That key'} alone would be taken from every app: add Ctrl, Alt, Shift or the Windows key.",
                              "red")
        other = self.hk.conflict(self.km, a, keys)
        if other:
            return self._done(f"{keys} is already “{other}”. Change that one first, or pick other keys.", "red")
        self.km[a] = {"keys": self.hk.canonical(keys), "on": True}
        self._save()
        st, why = self.win.hotkeys.status(a) if self.win.hotkeys is not None else ("", "")
        self._done(why if st == "failed" else "", "red")


class IntegrationsScreen(Screen):
    """Integrations (the recording's, with Monday's own): every service Monday reaches with its real state, the keys it can be given
    (saved into Monday's .env on this computer, never shown again), Google, and the owner's webhooks. "Connected" only where Core
    checked it; a key saved is "set", proven on first use."""
    key, icon, name, section = "integrations", "puzzle", "Integrations", "Notch"

    def build(self):
        self.hero = Hero("Integrations", "Google, the AIs Monday can ask, your webhooks, and what else it reaches.", "violet",
                         ("purple", "cyan", "yellow"))
        self.page.add(self.hero)
        self.keys_card = Card("Keys", "A key goes into Monday's .env file on this computer, never anywhere else, and is never shown "
                                      "here again. A saved key counts as set; it is proven the first time Monday uses it.")
        self.keys = Form()
        self.keys_card.lay.addWidget(self.keys)
        self.keys_note = Notice("", "red")
        self.keys_card.lay.addWidget(self.keys_note)
        self.page.add(self.keys_card)
        self.google_card = Card("Google", dot="off")
        self.google_line = label("", 12.5, 400, TEXT_2, wrap=True)
        self.google_card.lay.addWidget(self.google_line)
        gb = QHBoxLayout()
        self.g_connect, self.g_check, self.g_off = Button("Connect…", "light"), Button("Check", "secondary"), Button("Disconnect", "danger")
        self.g_connect.clicked.connect(self._g_connect)
        self.g_check.clicked.connect(lambda: self.bridge.get("/api/google?check=true", self._got_google))
        self.g_off.clicked.connect(self._g_disconnect)
        for b in (self.g_connect, self.g_check, self.g_off):
            gb.addWidget(b)
        gb.addStretch(1)
        self.google_card.lay.addLayout(gb)
        self.services = Form()
        self.google_card.lay.addWidget(self.services)
        self.google_note = Notice("", "red")
        self.google_card.lay.addWidget(self.google_note)
        self.page.add(self.google_card)
        self.parts_card = Card("What Monday reaches", "As Monday Core last checked it.")
        self.parts = Form()
        self.parts_card.lay.addWidget(self.parts)
        self.page.add(self.parts_card)
        hooks = Card("Webhooks", "A program on this computer can ask Monday to do something by calling a webhook's script. Each one "
                                 "starts off; only its script holds its secret.")
        self.hooks = Form()
        hooks.lay.addWidget(self.hooks)
        add = QHBoxLayout()
        self.hook_text = field("What Monday should do when it is called", "A new webhook's request")
        add.addWidget(self.hook_text, 1)
        self.hook_add = Button("Add", "save")
        self.hook_add.clicked.connect(self._hook_add)
        add.addWidget(self.hook_add)
        hooks.lay.addLayout(add)
        self.hooks_note = Notice("", "red")
        hooks.lay.addWidget(self.hooks_note)
        self.page.add(hooks)
        self.page.lay.addStretch(1)

    def load(self):
        super().load()
        self.bridge.get("/api/ai-tools", self._got_tools)
        self.bridge.get("/api/google", self._got_google)
        self.bridge.get("/api/status", self._got_status)
        self.bridge.get("/api/webhooks", self._got_hooks)
        self.bridge.get("/api/jev", self._got_jev)

    def on_event(self, ev):
        t = ev.get("type")
        if t == "GOOGLE_CHANGED":
            self.bridge.get("/api/google", self._got_google)
        elif t == "WEBHOOKS_CHANGED":
            self.bridge.get("/api/webhooks", self._got_hooks)
        elif t == "COMPONENTS":
            self._got_status({"components": (ev.get("data") or {}).get("components") or {}}, None)

    # keys
    def _got_tools(self, d, err):
        from .native import key_choices
        self.keys.clear()
        if err:
            return self.keys_note.say(f"The keys couldn't be read: {err}")
        self.keys_note.say("")
        have = set((d or {}).get("keys") or [])
        who = {p.get("key_env"): (p.get("name") or p.get("key"), p.get("status"), p.get("detail")) for p in (d or {}).get("providers") or []}
        who.update({"TYPESAFE_API_KEY": ("Jev (TypeSafe)", None, "Monday's first decision layer"),
                    "GOOGLE_TTS_API_KEY": ("Google's voices", None, "Google Cloud's text-to-speech voices"),
                    "CLOUDFLARE_ACCOUNT_ID": ("Cloudflare's browsers: account", None, "Browser Run reads research pages in Cloudflare's browsers"),
                    "CLOUDFLARE_API_TOKEN": ("Cloudflare's browsers: token", None, "an API token with Browser Rendering access"),
                    "FISH_AUDIO_API_KEY": ("Fish Audio", None, "clones your recording into Monday's voice (Settings → Voice → Custom voice)")})
        self._key_rows = {}
        for name, _w in key_choices(d):
            title, status, detail = who.get(name, (name, None, ""))
            set_ = name in have
            nd = name_dot(title, "ok" if set_ else "bad", "Set" if set_ else "Not set")
            box = field("Saved: paste a new one to replace it" if set_ else "Paste the key", f"{title}: {name}", secret=True)
            save = Button("Save", "save", name=f"Save {name}", height=26)  # (the recording's 26)
            save.clicked.connect(lambda _=False, n=name, b=box: self._save_key(n, b))
            box.returnPressed.connect(save.click)
            state = label("Set" if set_ else "Not set", 12, 600, GREEN_TEXT if set_ else K.RED_TEXT)
            self.keys.add(nd, label(name, 12, 400, TEXT_3, mono=True), box, save, state)
            if set_ and status and status not in ("available", "ready"):  # (not set already says why it isn't ready)
                self.keys.add(None, None, label(f"Set, but {words(status)}" + (f": {detail}" if detail else ""), 12, 400, K.AMBER, wrap=True),
                              span_last=True)

    def _save_key(self, name, box):
        value = box.text().strip()
        if not value:
            return self.keys_note.say(f"Paste the key for {name} first.", "amber")

        def done(_d, err):
            if err:
                self.keys_note.say(f"Not saved: {err}")
            else:
                box.clear()
                self.keys_note.say(f"{name} saved on this computer.", "green")
                self.bridge.get("/api/ai-tools", self._got_tools)
        self.bridge.post("/api/keys", {"name": name, "value": value}, done)

    def _got_jev(self, d, err):
        if not err and d:
            h = d.get("health") or {}
            self._jev = f"Jev: {words(h.get('status'))}" + (f" · {h.get('latency_ms')} ms" if h.get("latency_ms") else "")

    # Google
    def _got_google(self, d, err):
        from .native import google_line, google_rows
        if err:
            self.google_card.title_dot.set("bad")
            self.google_line.setText(f"Google's state couldn't be read: {err}")
            return
        st = (d or {}).get("state")
        self.google_card.title_dot.set({"connected": "ok", "expired": "bad", "not_connected": "off", "not_configured": "off"}.get(st, "off"))
        how = ((d or {}).get("client") or {}).get("how")
        self.google_line.setText(google_line(d) + (f"\n{how}" if st == "not_configured" and how else ""))
        self.g_connect.setVisible(st in ("not_connected", "expired"))
        self.g_off.setVisible(st in ("connected", "expired"))
        self.g_check.setVisible(st in ("connected", "expired"))
        self.services.clear()
        for sid, name, status, does, enabled in google_rows(d):
            sw = K.Toggle(f"Google {name}: on or off", enabled)
            sw.toggled.connect(lambda on, s=sid, w=sw: self._g_service(s, on, w))
            sw.setEnabled(st == "connected")
            self.services.add(sw, label(name, 13.5, 600), label(does, 12.5, 400, TEXT_2, wrap=True), label(status, 12, 400, TEXT_3))

    def _g_service(self, sid, on, sw):
        def done(d, err):
            if err:
                sw.set_quiet(not on)
                self.google_note.say(f"Not changed: {err}")
            else:
                self.google_note.say("")
                self._got_google(d, None)
        self.bridge.post("/api/google/service", {"service": sid, "enabled": on}, done)

    def _g_connect(self):
        self.google_note.say("Opening Google's own page in your browser: say yes there, then come back.", "amber")
        self.bridge.post("/api/google/connect", {"services": [], "open": True},
                         lambda _d, err: self.google_note.say(f"Couldn't start: {err}") if err else None, timeout=30)

    def _g_disconnect(self):
        if K.confirm(self, "Disconnect Google?", "Monday forgets its Google connection on this computer and asks Google to revoke it. "
                                                 "Connect again any time.", "Disconnect", danger=True):
            self.bridge.post("/api/google/disconnect", {}, lambda d, err: self.google_note.say(f"Not disconnected: {err}") if err
                             else self.bridge.get("/api/google", self._got_google))

    # what Monday reaches
    def _got_status(self, d, err):
        self.parts.clear()
        if err:
            self.parts.add(label(f"Monday Core couldn't be asked: {err}", 12.5, 400, K.RED_TEXT, wrap=True), span_last=True)
            return
        comps = (d or {}).get("components") or {}
        self.hero.set_pals([c for c, (k, v) in zip(("purple", "cyan", "yellow", "green", "orange", "pink"),
                                                    [(k, v) for k, v in comps.items() if component_dot((v or {}).get("status")) == "ok"])])
        for k, v in comps.items():
            v = v or {}
            if k == "calendar":  # (probes.Health reports it as not configured whatever Google says: Google's card above is the truth)
                continue
            self.parts.add(name_dot(k.replace("_", " ").capitalize(), component_dot(v.get("status"))),
                           label(words(v.get("status")), 12.5, 600, TEXT_2), label(str(v.get("detail") or ""), 12.5, 400, TEXT_3, wrap=True))

    # webhooks
    def _got_hooks(self, d, err):
        from .native import hook_line
        self.hooks.clear()
        if err:
            return self.hooks_note.say(f"The webhooks couldn't be read: {err}")
        self.hooks_note.say("")
        hs = (d or {}).get("webhooks") or []
        if not hs:
            self.hooks.add(label("No webhooks yet.", 12.5, 400, TEXT_3), span_last=True)
        for h in hs:
            sw = K.Toggle(f"Webhook {h.get('name')}: on or off", bool(h.get("enabled")))
            sw.toggled.connect(lambda on, h=h, w=sw: self._hook_switch(h, on, w))
            line = K.Elided(hook_line(h), 12.5, 400, TEXT_2)
            script = Button("Show its script", "ghost", name=f"Show webhook {h.get('name')}'s script")
            script.clicked.connect(lambda _=False, p=h.get("script"): self._reveal(p))
            script.setVisible(bool(h.get("script")))
            rm = K.IconButton("trash", f"Delete webhook {h.get('name')}")
            rm.clicked.connect(lambda _=False, h=h: self._hook_delete(h))
            self.hooks.add(sw, label(h.get("name") or "", 13.5, 600), line, script, rm)

    def _reveal(self, path):
        err = K.reveal(path) if path else "No script"
        if err:
            self.hooks_note.say(err)

    def _hook_switch(self, h, on, sw):
        def done(_d, err):
            if err:
                sw.set_quiet(not on)
                self.hooks_note.say(f"Not changed: {err}")
        self.bridge.post(f"/api/webhooks/{h['id']}", {"enabled": on}, done)

    def _hook_add(self):
        text = self.hook_text.text().strip()
        if not text:
            return self.hooks_note.say("Write what Monday should do when it is called.", "amber")
        self.hook_add.setEnabled(False)

        def done(_d, err):
            self.hook_add.setEnabled(True)
            if err:
                return self.hooks_note.say(f"Not added: {err}")
            self.hook_text.clear()
            self.bridge.get("/api/webhooks", self._got_hooks)
        self.bridge.post("/api/webhooks", {"prompt": text}, done)

    def _hook_delete(self, h):
        if K.confirm(self, "Delete this webhook?", f"“{h.get('prompt') or h.get('name')}”: its script stops working.", "Delete", danger=True):
            self.bridge.call("DELETE", f"/api/webhooks/{h['id']}", None, lambda _d, err: self.hooks_note.say(f"Not deleted: {err}") if err
                             else self.bridge.get("/api/webhooks", self._got_hooks))


class AgentsScreen(Screen):
    """Agents (the recording's): Monday's hooks in the owner's Claude Code (read only: changing them is the owner's), who does
    Monday's computer work and code, and every agent and tool Monday found, each with what can be done here (install, sign in, open)."""
    key, icon, name, section = "agents", "terminal", "Agents", "Notch"
    HOOK_WORDS = (("approvals", "Approvals from the notch", "Claude Code's permission prompts show in Monday's notch"),
                  ("stop", "The fix loop", "a session that broke its tests goes back to them"),
                  ("pre", "The commit gate and the two-agents guard", "no commit while tests fail; one file, one agent"),
                  ("mcp", "check_my_work", "Claude Code can run a project's tests through Monday"),
                  ("wake", "Write to, its turn over", "your words from the notch wake a session whose turn has ended"))

    def build(self):
        self.hero = Hero("Agents", "Claude Code, Codex and the other agents Monday can hand work to.", "steel", ("blue", "red", "green"))
        self.page.add(self.hero)
        self.cc = Card("Claude Code", "Monday's hooks in your Claude Code. Read only: they change only when you change "
                                      "~/.claude/settings.json yourself.", dot="off")
        self.hook_rows = Form(stretch=1)
        self.cc.lay.addWidget(self.hook_rows)
        self.page.add(self.cc)
        who = Card("Who does the work", "Monday picks from these; a request can still name another (“ask Codex to …”).")
        self.comp = dropdown([("", "Reading…")], "", "Who does computer work")
        self.code = dropdown([("", "Reading…")], "", "Who writes code")
        self.comp.activated.connect(lambda _i: self._pick("default_computer_agent", self.comp))
        self.code.activated.connect(lambda _i: self._pick("default_coding_agent", self.code))
        who.row("Computer work", self.comp, "Opening apps, clicking, typing on this computer")
        who.row("Code", self.code, "Changes to your projects")
        self.who_note = Notice("", "red")
        who.lay.addWidget(self.who_note)
        self.page.add(who)
        self.tools_card = Card("Agents and tools", "What Monday found on this computer, as each one answered when it was last asked.")
        refresh = Button("Look again", "ghost", icon_name="refresh", name="Look for agents and tools again")
        refresh.clicked.connect(lambda: self.bridge.get("/api/ai-tools?refresh=true", self._got_tools))
        self.tools_card.head.addWidget(refresh)
        self.tools = Form()
        self.tools_card.lay.addWidget(self.tools)
        eng = QHBoxLayout()
        eng.setSpacing(8)
        eng.addWidget(label("Add an engine", 13, 600))
        self.eng_name = field("Its name", "The new engine's name", width=160)
        self.eng_cli = field("Its command line, e.g. openclaw acp", "The new engine's command line (it speaks ACP)")
        add = Button("Add engine", "secondary", name="Add an agent that speaks ACP, by its command line")
        add.clicked.connect(self._add_engine)
        eng.addWidget(self.eng_name)
        eng.addWidget(self.eng_cli, 1)
        eng.addWidget(add)
        self.tools_card.lay.addLayout(eng)
        self.tools_note = Notice("", "red")
        self.tools_card.lay.addWidget(self.tools_note)
        self.page.add(self.tools_card)
        self.page.lay.addStretch(1)
        self.setup_lines: dict[str, QWidget] = {}

    def load(self):
        super().load()
        self.bridge.get("/api/agents/hooks", self._got_hooks)
        self.bridge.get("/api/settings", self._got_settings)
        self.bridge.get("/api/ai-tools", self._got_tools)

    def on_event(self, ev):
        if ev.get("type") == "TOOL_SETUP":
            d = ev.get("data") or {}
            line = self.setup_lines.get(d.get("key"))
            if line is not None:
                line.setText(d.get("line") or (("Done: " if d.get("ok") else "Failed: ") + str(d.get("detail") or d.get("status") or "")))
                line.show()
            if d.get("done"):
                self.bridge.get("/api/ai-tools?refresh=true", self._got_tools)

    def _got_hooks(self, d, err):
        self.hook_rows.clear()
        if err or (d or {}).get("error"):
            self.cc.title_dot.set("bad")
            return self.hook_rows.add(label(f"Failed: {err or d['error']}", 12.5, 400, K.RED_TEXT, wrap=True), span_last=True)
        n = sum(bool(d.get(k)) for k, _n, _w in self.HOOK_WORDS)
        self.cc.title_dot.set("ok" if n == len(self.HOOK_WORDS) else "wait" if n else "bad")
        for k, name, what in self.HOOK_WORDS:
            on = bool(d.get(k))
            self.hook_rows.add(name_dot(name, "ok" if on else "bad"), label(what, 12.5, 400, TEXT_2, wrap=True),
                               label("Installed" if on else "Not installed", 12, 600, GREEN_TEXT if on else K.RED_TEXT))

    def _got_settings(self, d, err):
        self.settings = d or {}
        self.who_note.say(f"Who does the work couldn't be read: {err}" if err else "")

    def _got_tools(self, d, err):
        from .native import AGENT_NAMES, tool_buttons, tool_rows
        self.tools.clear()
        self.setup_lines.clear()
        if err:
            return self.tools_note.say(f"The agents couldn't be read: {err}")
        self.tools_note.say("")
        tools = {t.get("key"): t for t in (d or {}).get("tools") or []}
        avail = lambda k: (tools.get(k) or {}).get("status") == "available"  # noqa: E731
        s = getattr(self, "settings", {})
        self.comp.clear()
        for k in ("hermes", "computer"):
            self.comp.addItem(AGENT_NAMES[k] + ("" if k == "computer" or avail(k) else " (not ready)"), k)
        self.code.clear()
        for k in ("claude_code", "codex", "gemini_cli", "kilo", "grok_build", "fastest"):
            self.code.addItem(AGENT_NAMES[k] + ("" if k == "fastest" or avail(k) else " (not ready)"), k)
        for box, key in ((self.comp, "default_computer_agent"), (self.code, "default_coding_agent")):
            i = box.findData(s.get(key))
            box.setCurrentIndex(i if i >= 0 else -1)
        ready = [t for t in tools.values() if t.get("status") == "available"]
        self.hero.set_pals(["blue", "red", "green", "purple", "orange", "cyan"][:max(1, min(6, len(ready)))])
        for key, name, kind, status, detail in tool_rows(d):
            t = tools.get(key) or {}
            b = tool_buttons(t)
            nd = name_dot(name, "ok" if t.get("status") == "available" else "bad" if t.get("installed") is False else "wait")
            info = QWidget()
            iv = QVBoxLayout(info)
            iv.setContentsMargins(0, 0, 0, 0)
            iv.setSpacing(1)
            auth = f" · {(t.get('context') or 'auth').replace('_', ' ')}: {t['auth_status'].replace('_', ' ')}" if t.get("auth_status") else ""
            iv.addWidget(label((f"{kind} · {status}" if kind else status) + auth, 12.5, 400, TEXT_2))
            if detail:
                iv.addWidget(K.Elided(detail, 12, 400, TEXT_3))
            if t.get("remediation"):  # core/agents.py's words on what to do about it (2026-10-27)
                iv.addWidget(K.Elided(str(t["remediation"]), 12, 400, TEXT_3))
            line = label("", 12, 400, K.AMBER, wrap=True, mono=True)
            line.hide()
            iv.addWidget(line)
            self.setup_lines[key] = line
            acts = QWidget()
            ah = QHBoxLayout(acts)
            ah.setContentsMargins(0, 0, 0, 0)
            ah.setSpacing(6)
            if b["install"]:
                ib = Button("Install…", "light", name=f"Install {name}")
                ib.clicked.connect(lambda _=False, k=key, n=name, how=b["how"]: self._setup(k, n, "install", how))
                ah.addWidget(ib)
            if b["login"] and t.get("auth") not in ("verified",):
                lb = Button("Sign in…", "secondary", name=f"Sign in to {name}")
                lb.clicked.connect(lambda _=False, k=key, n=name, how=b["how"]: self._setup(k, n, "login", how))
                ah.addWidget(lb)
            if b["open"]:
                ob = Button("Open", "secondary", name=f"Open {name}")
                ob.clicked.connect(lambda _=False, k=key, n=name: self._open(k, n))
                ah.addWidget(ob)
            if b["site"]:
                sb = K.IconButton("open", f"{name}'s site")
                sb.clicked.connect(lambda _=False, u=b["site"]: QDesktopServices.openUrl(QUrl(u)))
                ah.addWidget(sb)
            if b["remove"]:  # an engine the owner added (data/engines.json): Monday forgets it, the program stays
                rb = Button("Remove…", "ghost", name=f"Remove the engine {name}")
                rb.clicked.connect(lambda _=False, k=key, n=name: self._remove_engine(k, n))
                ah.addWidget(rb)
            self.tools.add(nd, info, None, acts)

    def _add_engine(self):
        """An agent that speaks ACP, by its command line (OpenMausBot's customAcp): Core keeps it in data/engines.json and checks it."""
        name, cli = self.eng_name.text().strip(), self.eng_cli.text().strip()
        if not name or not cli:
            return self.tools_note.say("Give the engine a name and its command line first.", "amber")

        def done(_d, err):
            if err:
                return self.tools_note.say(f"Not added: {err}")
            self.eng_name.clear()
            self.eng_cli.clear()
            self.bridge.get("/api/ai-tools?refresh=true", lambda d, e: (self._got_tools(d, e), None if e else self.tools_note.say(
                f"Added {name}: its row says what Monday found when it checked it.", "green")))
        self.bridge.post("/api/engines", {"key": "", "name": name, "cli": cli, "coding": True, "mcp": False}, done)

    def _remove_engine(self, key, name):
        if not K.confirm(self, f"Remove {name}?", f"Remove the engine {name} from Monday? The program itself stays on this computer.",
                         "Remove", danger=True):
            return
        self.bridge.call("DELETE", f"/api/engines/{urllib.parse.quote(key, safe='')}", None, lambda _d, err: self.tools_note.say(
            f"Not removed: {err}") if err else self.bridge.get("/api/ai-tools?refresh=true", lambda d, e: (
                self._got_tools(d, e), None if e else self.tools_note.say(f"Removed {name}.", "green"))))

    def _pick(self, key, box):
        v = box.currentData()

        def done(_d, err):
            if err:
                self.who_note.say(f"Couldn't switch: {err}")
                self.bridge.get("/api/settings", lambda d, e: (self._got_settings(d, e), self.bridge.get("/api/ai-tools", self._got_tools)))
            else:
                self.who_note.say("")
        self.bridge.post("/api/settings", {key: v}, done)

    def _setup(self, key, name, what, how):
        verb = "Install" if what == "install" else "Sign in to"
        if not K.confirm(self, f"{verb} {name}?", (how or f"Monday runs {name}'s own {'installer' if what == 'install' else 'sign-in'}.")
                         + "\nIt runs in its own window; Monday never sees a password.", verb.split()[0]):
            return
        line = self.setup_lines.get(key)
        if line is not None:
            line.setText("Starting…")
            line.show()
        self.bridge.post(f"/api/ai-tools/{key}/setup", {"what": what}, lambda d, err: line.setText(f"Failed: {err}") if err and line
                         else line.setText((d or {}).get("text") or "Started.") if line else None)

    def _open(self, key, name):
        line = self.setup_lines.get(key)
        self.bridge.post(f"/api/ai-tools/{key}/open", {}, lambda d, err: (line.setText(f"Couldn't open {name}: {err}" if err else
                                                                                       (d or {}).get("text") or f"{name} opened."), line.show())
                         if line is not None else None, timeout=45)


class SettingsScreen(Screen):
    """Settings (the recording's, with Monday's own): its name and chat model, approvals (the owner's mode and the grants they gave,
    each revocable), how Monday answers out loud, and who reads the microphone's words. Every change goes to Core and the page shows
    what Core answered."""
    key, icon, name, section = "settings", "sliders", "Settings", "Monday"

    def build(self):
        from .native import APPROVAL_MODES, MODES
        self.page.add(Hero("Settings", "How Monday works on this computer.", "cool", ("white", "grey")))
        self.looks_card = LooksCard(self.win)  # the notch's look, first here (the owner, 2026-10-29: "how can i change the ui?")
        self.page.add(self.looks_card)
        self.page.section("Monday")
        c = Card(up=True)
        self.name_box = field("Monday", "Monday's name", width=240)
        save = Button("Save", "save", height=26)
        save.clicked.connect(self._save_name)
        self.name_box.returnPressed.connect(save.click)
        nb = QWidget()
        nh = QHBoxLayout(nb)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.addWidget(self.name_box)
        nh.addWidget(save)
        c.row("Name", nb, "What Monday answers to (“Monday, …”) and calls itself.")
        self.model = dropdown([], None, "Chat model")
        self.model.activated.connect(self._pick_model)
        c.row("Chat model", self.model, "Who answers when you chat (Monday checks it answers before switching).")
        self.jev_pics = K.Toggle("Jev sees the screen: on or off")
        self.jev_pics.toggled.connect(lambda on: self._set("jev_images", on, self.jev_pics))
        c.row("Jev sees the screen", self.jev_pics, "Jev reads a screenshot when it decides what a request needs.")
        self.restore = K.Toggle("Restore points: on or off")
        self.restore.toggled.connect(lambda on: self._set("restore_points", on, self.restore))
        c.row("Restore points", self.restore, "Before an agent changes a project, Monday keeps a way back (git).")
        self.cloud_eyes = K.Toggle("A vision model looks at the camera: on or off")
        self.cloud_eyes.toggled.connect(lambda on: self._set("vision_cloud", on, self.cloud_eyes))
        c.row("A vision model sees what OpenCV can't", self.cloud_eyes, "When the camera can't name what you show it, the picture goes to your "
              "Ollama model that can see (Gemma, in Ollama's cloud): it leaves this computer.")
        self.note = Notice("", "red")
        c.lay.addWidget(self.note)
        self.page.add(c)
        self.page.section("Approvals")
        a = Card(up=True)
        self.mode = Segmented([(k, {"ask": "Ask", "always": "Always allow", "auto": "Auto"}[k]) for k, _w in APPROVAL_MODES], "ask",
                              "How approvals work")
        self.mode.changed.connect(self._pick_mode)
        self.mode_words = label("", 12.5, 400, TEXT_2, wrap=True)
        self._mode_words = dict(APPROVAL_MODES)
        row = a.row("How Monday asks", self.mode)
        row.layout().itemAt(0).layout().addWidget(self.mode_words)
        a.lay.addWidget(label("Deleting, installing, paying, sending, publishing and anything outside a project ask every time, in every "
                              "mode.", 12, 400, TEXT_3, wrap=True))
        self.grants = Form()
        a.lay.addWidget(self.grants)
        self.page.add(a)
        self.page.section("Voice")
        v = Card(up=True)
        self.voice_mode = dropdown(list(MODES), None, "When Monday speaks")
        self.voice_mode.activated.connect(lambda _i: self._voice({"interaction_mode": self.voice_mode.currentData()}))
        v.row("When Monday speaks", self.voice_mode)
        self.spoken = K.Toggle("Spoken replies: on or off")
        self.spoken.toggled.connect(lambda on: self._voice({"enabled": on}))
        v.row("Spoken replies", self.spoken, "Off: Monday only writes its replies.")
        self.provider = dropdown([], None, "Whose voice")
        self.provider.activated.connect(lambda _i: self._voice({"tts_provider": self.provider.currentData()}))
        v.row("Whose voice", self.provider)
        self.custom_voice = Button("Custom voice…", "secondary", name="Custom voice: pick a recording of about 30 seconds to clone")
        self.custom_voice.clicked.connect(self._custom_voice)
        v.row("Custom voice", self.custom_voice, "A recording of about 30 seconds becomes Monday's voice: cloned on Fish Audio when its key "
                                                 "is in Integrations, else by the voice engine on this PC.")
        self.voice_note = Notice("", "red")
        v.lay.addWidget(self.voice_note)
        self.page.add(v)
        self.page.section("Microphone")
        m = Card(up=True)
        self.reader = dropdown([(k, n) for k, n, _w in READERS if k != "windows" or sys.platform == "win32"], None, "Who reads your words")
        self.reader.activated.connect(lambda _i: self._set("default_stt", self.reader.currentData(), None))
        m.row("Who reads your words", self.reader, "Google: each phrase goes to Google. Whisper: nothing leaves this computer.")
        m.row("Microphone", label("This computer's default", 13, 400, TEXT_2), "Monday listens with the input your OS uses; change it there.")
        self.page.add(m)
        self.page.lay.addStretch(1)

    def load(self):
        super().load()
        self.looks_card.fill()
        self.bridge.get("/api/settings", self._got_settings)
        self.bridge.get("/api/models", self._got_models)
        self.bridge.get("/api/approvals", self._got_approvals)
        self.bridge.get("/api/voice", self._got_voice)

    def on_event(self, ev):
        t = ev.get("type")
        if t in ("SETTINGS_CHANGED", "MODEL_CHANGED"):
            self.bridge.get("/api/settings", self._got_settings)
            self.bridge.get("/api/models", self._got_models)
        elif t == "APPROVALS_CHANGED":
            self.bridge.get("/api/approvals", self._got_approvals)
        elif t == "VOICE_CHANGED":
            self.bridge.get("/api/voice", self._got_voice)

    def _got_settings(self, d, err):
        if err:
            return self.note.say(f"The settings couldn't be read: {err}")
        d = d or {}
        if not self.name_box.hasFocus():
            self.name_box.setText(d.get("assistant_name") or "")
        self.jev_pics.set_quiet(bool(d.get("jev_images")))
        self.restore.set_quiet(bool(d.get("restore_points")))
        self.cloud_eyes.set_quiet(bool(d.get("vision_cloud")))
        choice = d.get("default_stt") or "browser"
        i = self.reader.findData("local" if choice == "local" else "windows" if choice == "windows" else "google")
        self.reader.setCurrentIndex(i)

    def _got_models(self, d, err):
        from .native import model_choices
        self.model.clear()
        if err:
            return self.note.say(f"The models couldn't be read: {err}")
        for mid, words_, selected, detail in model_choices(d):
            self.model.addItem(words_, mid)
            self.model.setItemData(self.model.count() - 1, detail, Qt.ItemDataRole.ToolTipRole)
            if selected:
                self.model.setCurrentIndex(self.model.count() - 1)

    def _pick_model(self, _i):
        mid = self.model.currentData()
        self.bridge.post("/api/models/select", {"id": mid}, lambda d, err: (self.note.say(f"Not switched: {err}"),
                                                                            self.bridge.get("/api/models", self._got_models)) if err
                         else self.note.say((d or {}).get("text") or "", "green"))

    def _save_name(self):
        name = self.name_box.text().strip()
        if name:
            self._set("assistant_name", name, None)

    def _set(self, key, value, sw):
        def done(d, err):
            if err:
                if sw is not None:
                    sw.set_quiet(not value)
                self.note.say(f"Not changed: {err}")
            else:
                self.note.say((d or {}).get("text") or "", "green")
        self.bridge.post("/api/settings", {key: value}, done)

    def _got_approvals(self, d, err):
        self.grants.clear()
        if err:
            return self.note.say(f"Approvals couldn't be read: {err}")
        d = d or {}
        self.mode.set_value(d.get("mode") or "ask")
        self.mode_words.setText(self._mode_words.get(d.get("mode") or "ask", ""))
        for g in d.get("grants") or []:
            rv = Button("Revoke", "secondary", name=f"Revoke: {g.get('words')}")
            rv.clicked.connect(lambda _=False, g=g: self._revoke(g))
            self.grants.add(name_dot(g.get("who") or "", "ok"), label(g.get("words") or g.get("key") or "", 12.5, 400, TEXT_2, wrap=True),
                            None, rv)

    def _pick_mode(self, mode):
        def done(_d, err):
            if err:
                self.note.say(f"Not changed: {err}")
            self.bridge.get("/api/approvals", self._got_approvals)
        self.bridge.post("/api/settings", {"approval_mode": mode}, done)

    def _revoke(self, g):
        self.bridge.post("/api/approvals/revoke", {"who": g.get("who"), "kind": g.get("kind"), "key": g.get("key")},
                         lambda _d, err: self.note.say(f"Not revoked: {err}") if err else self.bridge.get("/api/approvals", self._got_approvals))

    def _got_voice(self, d, err):
        from .native import provider_choices
        if err:
            return self.voice_note.say(f"The voice settings couldn't be read: {err}")
        s = (d or {}).get("settings") or {}
        i = self.voice_mode.findData(s.get("interaction_mode"))
        self.voice_mode.setCurrentIndex(i)
        self.spoken.set_quiet(s.get("enabled") is not False)
        self.provider.clear()
        for pid, words_ in provider_choices(d):
            self.provider.addItem(words_, pid)
        self.provider.setCurrentIndex(self.provider.findData(s.get("tts_provider")))

    def _voice(self, body):
        self.bridge.post("/api/voice/settings", body, lambda _d, err: (self.voice_note.say(f"Not changed: {err}"),
                                                                       self.bridge.get("/api/voice", self._got_voice)) if err
                         else self.voice_note.say(""))

    def _custom_voice(self):
        """Custom voice (the owner, 2026-10-21): a recording picked here goes to Core (POST /api/upload, then /api/voice/sample), which clones
        it on Fish Audio when the key is set, else with the voice engine on this PC, and says what came of it."""
        path, _ = QFileDialog.getOpenFileName(self, "A recording of your voice (about 30 seconds)", "",
                                              "Audio (*.wav *.mp3 *.flac *.m4a *.ogg *.webm);;All files (*)")
        if not path:
            return
        name = os.path.basename(path)
        self.voice_note.say(f"Sending {name}…", "amber")

        def upload():
            with open(path, "rb") as fh:
                r = self.bridge.core.http.post("/api/upload", content=fh.read(), headers={"x-filename": urllib.parse.quote(name)}, timeout=300)
            data = r.json() if r.content else {}
            return data, None if r.status_code < 400 else (data.get("detail") or f"HTTP {r.status_code}")

        def sent(d, err):
            if err or not (d or {}).get("id"):
                return self.voice_note.say(f"Couldn't send {name}: {err or 'Core kept nothing'}")
            self.voice_note.say(f"Cloning {name}… (Fish Audio, or the voice engine here)", "amber")
            self.bridge.post("/api/voice/sample", {"file_id": d["id"]}, lambda dd, e: (
                self.voice_note.say(f"Not cloned: {e}") if e else self.voice_note.say(str((dd or {}).get("message") or "Done."), "green"),
                self.bridge.get("/api/voice", self._got_voice)))
        self.bridge.run(upload, sent)


class LooksCard(Card):
    """Appearance & notch UI (docs/adr/0011): the notch's three looks, the one in use marked. Use switches the notch at once (Notch.set_mode
    keeps it and unfolds the notch so the look is seen); Reset puts back that look's own layout and place. On Settings and on General;
    the notch's own settings (its gear) and its right-click menu switch it too."""

    def __init__(self, win):
        from . import island as I
        super().__init__("Appearance & notch UI", "How the notch looks: pick one and it changes now. Each look keeps its own layout and "
                                                  "place; switching never stops a task, an approval or the chat. The notch's own settings "
                                                  "(its gear) and its right-click menu switch it too.")
        self.win, self.looks = win, {}
        for k, (name, line) in I.MODES.items():
            use, reset = Button("Use", "light"), Button("Reset", "secondary")
            use.clicked.connect(lambda _=False, k_=k: self.use(k_))
            reset.clicked.connect(lambda _=False, k_=k: self.reset(k_))
            reset.setAccessibleName(f"Reset the {name} look's layout and place")
            self.row(name, use, line, extra=reset)
            self.looks[k] = use
        self.note = Notice("", "green")
        self.lay.addWidget(self.note)

    def fill(self):
        from . import island as I
        mode = I.ui(self.win.store.get(I.UI_KEY))["mode"]
        for k, b in self.looks.items():
            b.setText("In use" if k == mode else "Use")
            b.setEnabled(k != mode)
            b.setAccessibleName(f"{I.MODES[k][0]}: in use" if k == mode else f"Use the {I.MODES[k][0]} look")

    def use(self, mode):
        """A look chosen: the notch takes it at once (Notch.set_mode keeps it); with no notch here, kept for the next start."""
        from . import island as I
        n = getattr(self.win, "notch", None)
        if n is not None and hasattr(n, "set_mode"):
            n.set_mode(mode)
        else:
            self.win.keep(I.UI_KEY, {**I.ui(self.win.store.get(I.UI_KEY)), "mode": mode})
        self.fill()
        self.note.say(f"The notch now uses the {I.MODES[mode][0]} look.", "green")

    def reset(self, mode):
        """One look's layout and place back to how it started; the other looks as they are."""
        from . import island as I
        n = getattr(self.win, "notch", None)
        if n is not None and hasattr(n, "reset_mode"):
            n.reset_mode(mode)
        elif mode == "classic":
            self.win.keep("island", {**(self.win.store.get("island") or {}), "layout": I.preset(I.DEFAULT)})
            self.win.keep("notch_at", None)
        else:
            self.win.keep(I.UI_KEY, I.ui_reset(self.win.store.get(I.UI_KEY), mode))
        self.note.say(f"The {I.MODES[mode][0]} look is back to its first layout and place.", "green")


class GeneralScreen(Screen):
    """General (the recording's): the notch and its pet: sound and its volume, auto-close, showing the notch, notifications, starting
    with the computer, and the pet itself (its wardrobe). Kept in data/desktop.json ("pet", notch.prefs checks every value); the
    notch takes a change at once."""
    key, icon, name, section = "general", "sun", "General", "Notch"

    def build(self):
        from .notch import AUTOCLOSE, IDLE, INTERACT, PETS, TEMPER, WEAR
        self.page.add(Hero("General", "The notch, its pet, its sounds, and how it behaves.", "grey", ("white", "orange")))
        self.looks_card = LooksCard(self.win)  # the three looks (the owner's brief of 2026-10-29; docs/adr/0011): how the notch is drawn
        self.page.add(self.looks_card)
        c = Card("General")
        self.sound = K.Toggle("The notch's sounds: on or off")
        self.sound.toggled.connect(lambda on: self._pref("sounds", on))
        self.volume = K.slider(60, "The notch's volume")
        self.volume.valueChanged.connect(lambda v: self._pref("volume", round(v / 100, 2)))
        sv = QWidget()
        sh = QHBoxLayout(sv)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(14)
        sh.addWidget(self.sound)
        sh.addWidget(self.volume)
        c.field("Sound", sv, "Its tones only; Monday's voice is in Settings.")
        self.auto = dropdown([(str(s), f"{s} seconds") for s in AUTOCLOSE], None, "Auto-close", 140)
        self.auto.activated.connect(lambda _i: self._pref("autoclose", int(self.auto.currentData())))
        c.field("Auto-close", self.auto, "after the pointer leaves the open notch")
        self.visible = K.Toggle("Show the notch: on or off")
        self.visible.toggled.connect(lambda on: self._pref("visible", on))
        c.field("Show the notch", self.visible)
        self.desk = K.Toggle("Your pet on the desktop: on or off")
        self.desk.toggled.connect(lambda on: self._pref("desk", on))
        self.desk_size = K.slider(100, "Your pet's size on the desktop")
        self.desk_size.setRange(50, 250)
        self.desk_size.sliderReleased.connect(lambda: self._pref("desk_size", round(self.desk_size.value() / 100, 2)))
        dv = QWidget()
        dh = QHBoxLayout(dv)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.setSpacing(14)
        dh.addWidget(self.desk)
        dh.addWidget(self.desk_size)
        c.field("Pet on the desktop", dv, "above the taskbar; drag it anywhere")
        self.on_start = K.Toggle("Show the notch when Monday starts: on or off")
        self.on_start.toggled.connect(lambda on: self._pref("on_start", on))
        c.field("When Monday starts", self.on_start, "show the notch")
        self.notify = K.Toggle("Notifications: on or off")
        self.notify.toggled.connect(lambda on: self._pref("notify", on))
        c.field("Notifications", self.notify, "when an approval waits or an agent may be stuck")
        self.startup = K.Toggle("Start Monday when you log in: on or off")
        self.startup.toggled.connect(self._startup)
        c.field("Launch at startup", self.startup)
        reset = Button("Reset the notch's place", "secondary")
        reset.clicked.connect(self._reset_place)
        c.field("Position", reset, "back to the top centre of the main screen")
        self.note = Notice("", "red")
        c.lay.addWidget(self.note)
        self.page.add(c)
        # updates (docs/adr/0010): what Core found in the update channel, the owner's Update, and where updates come from
        u = Card("Updates", "Monday looks for a newer version when it starts and every 6 hours. Only one signed with your update key "
                            "is offered.")
        self.upd_go = Button("Update", "light")
        self.upd_go.clicked.connect(self._update_go)
        self.upd_go.setVisible(False)  # (only when there is something to install or load)
        self.upd_check = Button("Check now", "secondary")
        self.upd_check.clicked.connect(self._update_check)
        r = u.row("Not checked yet", self.upd_go, "What Monday finds where updates come from shows here.", extra=self.upd_check)
        self.upd_state = r.title
        self.upd_words = next(w for w in r.findChildren(K.QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly) if w is not r.title)
        self.upd_folder = field("A folder this PC can read", "Where updates come from", width=320)
        save = Button("Save", "secondary")
        save.clicked.connect(self._update_folder)
        fv = QWidget()
        fh = QHBoxLayout(fv)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(10)
        fh.addWidget(self.upd_folder)
        fh.addWidget(save)
        fh.addStretch(1)
        u.field("Updates from", fv, "this PC's releases folder, or a shared or synced one")
        self.upd_note = Notice("", "red")
        u.lay.addWidget(self.upd_note)
        self.page.add(u)
        w = Card("The pet", "Its look and how it moves. The notch and the desktop show the same one.")
        top = QHBoxLayout()
        self.preview = Pet(96, PetArt(self.win.pet_prefs), live=True, name="The pet, as it will look")
        top.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        form = QVBoxLayout()
        self.kind = dropdown([(k, v[0]) for k, v in PETS.items()], None, "Which pet", 240)
        self.kind.activated.connect(lambda _i: self._draft("kind", self.kind.currentData()))
        self.pet_name = field("Its name", "The pet's name", width=240)
        self.pet_name.editingFinished.connect(lambda: self._draft("name", self.pet_name.text().strip()))
        self.idle = dropdown(list(IDLE.items()), None, "How it rests", 240)
        self.idle.activated.connect(lambda _i: self._draft("idle", self.idle.currentData()))
        self.interact = dropdown(list(INTERACT.items()), None, "When you click it", 240)
        self.interact.activated.connect(lambda _i: self._draft("interact", self.interact.currentData()))
        self.temper = dropdown(list(TEMPER.items()), None, "Its face at rest", 240)
        self.temper.activated.connect(lambda _i: self._draft("temper", self.temper.currentData()))
        self.wear = dropdown(list(WEAR.items()), None, "What it wears", 240)
        self.wear.activated.connect(lambda _i: self._draft("wear", self.wear.currentData()))
        for words_, box in (("Pet", self.kind), ("Name", self.pet_name), ("At rest", self.idle), ("When clicked", self.interact),
                            ("Its face", self.temper), ("Wears", self.wear)):
            r = QHBoxLayout()
            lb = label(words_, 13, 400, TEXT_2)
            lb.setFixedWidth(110)
            r.addWidget(lb)
            r.addWidget(box)
            r.addStretch(1)
            form.addLayout(r)
        top.addLayout(form, 1)
        w.lay.addLayout(top)
        self.page.add(w)
        self.page.lay.addStretch(1)

    def load(self):
        from .notch import prefs
        super().load()
        p = prefs(self.win.store.get("pet"))
        self.p = p
        for sw, k in ((self.sound, "sounds"), (self.visible, "visible"), (self.on_start, "on_start"), (self.notify, "notify"),
                      (self.desk, "desk")):
            sw.set_quiet(bool(p[k]))
        self.desk_size.blockSignals(True)
        self.desk_size.setValue(int(round(p["desk_size"] * 100)))
        self.desk_size.blockSignals(False)
        self.desk_size.setEnabled(p["desk"])
        self.volume.blockSignals(True)
        self.volume.setValue(int(round(p["volume"] * 100)))
        self.volume.blockSignals(False)
        self.volume.setEnabled(p["sounds"])
        self.auto.setCurrentIndex(self.auto.findData(str(p["autoclose"])))
        for box, k in ((self.kind, "kind"), (self.idle, "idle"), (self.interact, "interact"), (self.temper, "temper"), (self.wear, "wear")):
            box.setCurrentIndex(box.findData(p[k]))
        self.pet_name.setText(p["name"])
        self.looks_card.fill()
        from . import desktop
        self.startup.set_quiet(desktop.autostart_on())
        self.bridge.get("/api/update", self._got_update)

    def on_event(self, ev):
        if ev.get("type") == "UPDATE_AVAILABLE":
            self._got_update(ev.get("data") or {}, None)

    def _got_update(self, d, err):
        """The update channel's answer (core/update.py check), in words: never "up to date" when it couldn't look."""
        if err:
            self.upd_state.setText("Couldn't check")
            self.upd_words.setText(err)
            self.upd_words.setVisible(True)
            return self.upd_go.setVisible(False)
        d = d or {}
        v, st, cur = (d.get("latest") or {}).get("version"), d.get("state"), d.get("current") or "?"
        when = time.strftime(" · checked %H:%M", time.localtime(d["checked_at"])) if d.get("checked_at") else ""
        head = {"up_to_date": f"Monday {cur} · up to date{when}", "available": f"Monday {v} is ready" if d.get("action") else
                f"Monday {v} is published{when}", "restart": f"Monday {v} is on this PC: restart to load it"}.get(st, f"Couldn't check{when}")
        note = next((x.strip() for x in str((d.get("latest") or {}).get("notes") or "").split("\n") if x.strip()), "")
        self.upd_state.setText(head)
        # (under the state: the release's note when there is something to do, else why it says what it says; nothing more when up to date)
        self.upd_words.setText((note or d.get("detail") or "") if d.get("action") else "" if st == "up_to_date" else d.get("detail") or "")
        self.upd_words.setVisible(bool(self.upd_words.text()))  # (no empty line under "up to date")
        self.upd_go.setText("Restart" if d.get("action") == "restart" else "Update")
        self.upd_go.setVisible(d.get("action") in ("install", "restart"))
        self.upd_go.setEnabled(True)
        if not self.upd_folder.hasFocus():
            self.upd_folder.setText(d.get("channel") or "")

    def _update_go(self):
        self.upd_go.setEnabled(False)
        self.upd_note.say("")
        self.win.update_now(lambda words: (self.upd_note.say(words, "red" if words.startswith("Not") else "amber"), self.upd_go.setEnabled(True)))

    def _update_check(self):
        self.upd_state.setText("Checking…")
        self.bridge.post("/api/update/check", {}, self._got_update)

    def _update_folder(self):
        def done(d, err):
            self.upd_note.say(f"Not changed: {err}" if err else "")
            if not err:
                self._got_update(d, None)
        self.bridge.post("/api/update/channel", {"folder": self.upd_folder.text().strip()}, done)

    def _pref(self, k, v):
        from .notch import prefs
        if k == "visible" and not v and not self.p.get("desk"):
            self.visible.set_quiet(True)  # (the owner, 2026-10-16: "it should always be there, the pet or the notch, one of them")
            return self.note.say("The notch stays while the pet isn't on the desktop: one of them is always there.", "amber")
        back = k == "desk" and not v and not self.p.get("visible")  # the pet put away while the notch is off: the notch comes back
        self.p = prefs({**self.p, k: v, **({"visible": True} if back else {})})
        self.visible.set_quiet(self.p["visible"])
        self.volume.setEnabled(self.p["sounds"])
        self.desk_size.setEnabled(self.p["desk"])
        self.win.keep("pet", self.p)
        self.note.say("The notch is back while the pet is away: one of them is always there." if back else "", "amber")

    def _draft(self, k, v):
        from .notch import prefs
        if k == "kind":  # a kind brings its own name and colours
            self.p = prefs({**{x: y for x, y in self.p.items() if x not in ("name", "body", "accent")}, "kind": v})
            self.pet_name.setText(self.p["name"])
        else:
            self.p = prefs({**self.p, k: v})
        self.preview.art.set(self.p)
        self.preview.update()
        self.win.keep("pet", self.p)
        self.win.rail.pet.art.set(self.p)
        self.win.rail.pet.update()

    def _startup(self, on):
        from . import desktop
        try:
            desktop.autostart(on)
            self.note.say("")
        except Exception as e:  # the folder or the shell refused: say so, leave the switch as it really is
            self.note.say(f"Not changed: {e}")
        self.startup.set_quiet(desktop.autostart_on())

    def _reset_place(self):
        self.win.keep("notch_at", None)  # (qtnotch.NOTCH_KEY: the Qt notch's own place; "notch" was the WinForms one's)
        if getattr(self.win, "notch", None) is not None and hasattr(self.win.notch, "reset_place"):
            self.win.notch.reset_place()
        self.note.say("The notch goes back to the top centre.", "green")


class PhoneScreen(Screen):
    """Phone (docs/adr/0009): the companion app on the owner's phone as a remote for this PC, through core/remote.py's own listener.
    Its switch and what Core reports of it (the addresses, the way across networks, the certificate the phone pins), a pairing code
    that lives two minutes, and every paired phone with its last use, its approvals switch and Revoke. Nothing is shown that Core
    didn't report: Off, N/A, "No phone paired yet."."""
    key, icon, name, section = "phone", "link", "Phone", "Notch"  # (the kit has no phone icon; the link is the pairing)

    def build(self):
        self.page.add(Hero("Phone", "Your phone as a remote for this PC: pair it once, revoke it any time.", "teal", ("cyan", "white", "green")))
        c = Card("Phone access", "The companion app reaches this PC over HTTPS on its own port, with a credential it gets once from a "
                                 "pairing code. Off until you turn it on; Monday's own port stays closed to everything but this computer.")
        self.switch = K.Toggle("Phone access: on or off")
        self.switch.toggled.connect(self._switch)
        c.row("Phone access", self.switch, "Listen for your phone")
        self.addr = label("N/A", 12.5, 400, TEXT_2, wrap=True, select=True)
        self.why = label("N/A", 12.5, 400, TEXT_2, wrap=True)
        self.fp = label("N/A", 12, 400, TEXT_2, wrap=True, mono=True, select=True)
        info = Form(stretch=1)
        for words_, w in (("Addresses", self.addr), ("Across networks", self.why), ("Certificate", self.fp)):
            info.add(label(words_, 13, 400, TEXT_2), w)
        c.lay.addWidget(info)
        self.note = Notice("", "red")
        c.lay.addWidget(self.note)
        self.page.add(c)
        p = Card("Pair a phone", "In the companion, enter one of the addresses above and this code. A code lives two minutes and works once.")
        self.pair = Button("Show a pairing code", "primary")
        self.pair.clicked.connect(lambda: self.bridge.post("/api/remote/pairing", {}, self._got_code))
        self.off_line = label("Turn Phone access on first.", 12.5, 400, TEXT_3)
        pb = QHBoxLayout()
        pb.setSpacing(12)
        pb.addWidget(self.pair)
        pb.addWidget(self.off_line)
        pb.addStretch(1)
        p.lay.addLayout(pb)
        self.code = label("", 34, 700, mono=True, select=True)
        self.left = label("", 12.5, 400, TEXT_2)
        p.lay.addWidget(self.code)
        p.lay.addWidget(self.left)
        self.pair_note = Notice("", "red")
        p.lay.addWidget(self.pair_note)
        self.page.add(p)
        d = Card("Paired phones", "Each keeps its credential until you revoke it here, or forget this PC on the phone.")
        self.devices = Form(stretch=1)
        d.lay.addWidget(self.devices)
        self.dev_note = Notice("", "red")
        d.lay.addWidget(self.dev_note)
        self.page.add(d)
        self.page.lay.addStretch(1)
        self.expires_at = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._show_code(None)

    def load(self):
        super().load()
        self.refresh()

    def refresh(self):
        self.bridge.get("/api/remote", self._got_state)
        self.bridge.get("/api/remote/devices", self._got_devices)

    def on_event(self, ev):
        if ev.get("type") in ("SETTINGS_CHANGED", "DEVICE_REVOKED"):
            self.refresh()

    def _got_state(self, d, err):
        if err:
            return self.note.say(f"The phone's state couldn't be read: {err}")
        d = d or {}
        on = bool(d.get("on"))
        self.switch.set_quiet(on)
        lines = list(d.get("lan") or []) + ([f"{d['public']} (ngrok)"] if d.get("public") else [])
        self.addr.setText(("\n".join(lines) or "N/A") if on else "Off")
        self.why.setText(d.get("why") or "N/A")
        fp = d.get("fingerprint") or ""
        self.fp.setText(" ".join(fp[i:i + 4] for i in range(0, len(fp), 4)) if fp else "N/A")
        self.note.say(d.get("error") or "")
        self.pair.setEnabled(on)
        self.off_line.setVisible(not on)
        if not on:
            self._show_code(None)

    def _switch(self, on):
        def done(_d, err):
            if err:
                self.switch.set_quiet(not on)
                return self.note.say(f"Not changed: {err}")
            self.refresh()
        self.bridge.post("/api/settings", {"remote_access": on}, done)

    # pairing
    def _got_code(self, d, err):
        if err:
            return self.pair_note.say(f"No code: {err}")
        self.pair_note.say("")
        self._show_code(d or {})

    def _show_code(self, d):
        """The code big, in two halves, with its countdown; None hides it."""
        code = str((d or {}).get("code") or "")
        self.expires_at = (d or {}).get("expires_at")
        self.code.setText(f"{code[:4]} {code[4:]}".strip())
        self.code.setVisible(bool(code))
        self.left.setVisible(bool(code))
        self.timer.stop()
        if code and self.expires_at:
            self.timer.start()
            self._tick()
        else:
            self.left.setText("Expires: N/A" if code else "")

    def _tick(self):
        left = math.ceil((self.expires_at or 0) - time.time())
        if left > 0:
            return self.left.setText(f"Expires in {left // 60}:{left % 60:02d}")
        self.timer.stop()
        self.left.setText("Expired")
        self.code.hide()

    # the phones
    def _got_devices(self, d, err):
        self.devices.clear()
        if err:
            return self.dev_note.say(f"The paired phones couldn't be read: {err}")
        self.dev_note.say("")
        devs = (d or {}).get("devices") or []
        if not devs:
            self.devices.add(label("No phone paired yet.", 12.5, 400, TEXT_3), span_last=True)
        for dev in devs:
            name = dev.get("name") or f"Phone {dev.get('id')}"
            what = " ".join(str(dev.get(k) or "") for k in ("platform", "app_version")).strip() or "N/A"
            gone = dev.get("revoked_at")
            if gone:  # greyed, no controls
                when = time.strftime("%d %b %H:%M", time.localtime(gone)).lstrip("0")
                self.devices.add(label(name, 13.5, 600, TEXT_3), label(f"{what} · Revoked {when}", 12.5, 400, TEXT_3, wrap=True), span_last=True)
                continue
            seen = "last seen " + (ago(dev["last_seen"]) + (f" from {dev['last_address']}" if dev.get("last_address") else "")
                                   if dev.get("last_seen") else "never")
            sw = K.Toggle(f"{name} may answer approvals: on or off", bool((dev.get("capabilities") or {}).get("approve")))
            sw.toggled.connect(lambda on, i=dev.get("id"), w=sw: self._approve(i, on, w))
            rm = Button("Revoke", "danger", name=f"Revoke {name}")
            rm.clicked.connect(lambda _=False, i=dev.get("id"), n=name: self._revoke(i, n))
            self.devices.add(label(name, 13.5, 600), label(f"{what} · {seen}", 12.5, 400, TEXT_2, wrap=True), sw,
                             label("May answer approvals", 12.5, 400, TEXT_2), rm)

    def _approve(self, device_id, on, sw):
        def done(d, err):
            if err:
                sw.set_quiet(not on)
                return self.dev_note.say(f"Not changed: {err}")
            sw.set_quiet(bool(((d or {}).get("capabilities") or {}).get("approve", on)))  # as Core has it now
        self.bridge.post(f"/api/remote/devices/{device_id}/capability", {"key": "approve", "value": on}, done)

    def _revoke(self, device_id, name):
        if not K.confirm(self, f"Revoke {name}?", f"{name} can't reach this PC any more. To let it back, pair it again with a new code.",
                         "Revoke", danger=True):
            return
        self.bridge.call("DELETE", f"/api/remote/devices/{device_id}", None,
                         lambda _d, err: self.dev_note.say(f"Not revoked: {err}") if err else self.refresh())


def md_document(doc, text, color=TEXT):
    """chat.py's markdown (md_blocks / md_spans: what Monday's models write) into a Qt text document, block by block: headings, lists,
    code, quotes, bold, italic, inline code, links (a web address only opens, never a program or a file: chat.web_link)."""
    from PySide6.QtGui import QTextBlockFormat, QTextCharFormat, QTextCursor
    from .chat import _SPAN, md_blocks, web_link
    doc.clear()
    cur = QTextCursor(doc)
    base = QTextCharFormat()
    base.setFont(font(13.5))
    base.setForeground(QColor(color))

    def fmt(px=13.5, weight=400, italic=False, mono=False, fg=None, back=None):
        f = QTextCharFormat(base)
        ft = font(px, weight, mono=mono)
        ft.setItalic(italic)
        f.setFont(ft)
        if fg:
            f.setForeground(QColor(fg))
        if back:
            f.setBackground(QColor(back))
        return f

    def spans(t, f0):
        for part in _SPAN.split(t or ""):
            if not part:
                continue
            if part.startswith("`"):
                cur.insertText(part[1:-1], fmt(12.5, mono=True, fg="#f0b27a", back="#1d2125"))
            elif part.startswith(("**", "__")):
                f = QTextCharFormat(f0)
                f.setFontWeight(700)
                cur.insertText(part[2:-2], f)
            elif part.startswith(("*", "_")):
                f = QTextCharFormat(f0)
                f.setFontItalic(True)
                cur.insertText(part[1:-1], f)
            elif part.startswith("["):
                m = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)", part)
                f = QTextCharFormat(f0)
                url = web_link(m.group(2)) if m else None
                if url:
                    f.setAnchor(True)
                    f.setAnchorHref(url)
                    f.setForeground(QColor(K.BLUE))
                    f.setFontUnderline(True)
                cur.insertText(m.group(1) if m else part, f)
            else:
                cur.insertText(part, f0)
    blocks = md_blocks(text) or [("p", "")]
    for i, b in enumerate(blocks):
        bf = QTextBlockFormat()
        bf.setBottomMargin(0 if (b[0] == "li" and i + 1 < len(blocks) and blocks[i + 1][0] == "li") else 8)
        if b[0] == "li":
            bf.setLeftMargin(18 + 18 * b[3])
            bf.setTextIndent(-12)
        elif b[0] == "quote":
            bf.setLeftMargin(14)
        elif b[0] == "code":
            bf.setLeftMargin(6)
            bf.setBackground(QColor("#16191c"))
        if i == 0:
            cur.setBlockFormat(bf)
        else:
            cur.insertBlock(bf)
        k = b[0]
        if k == "h":
            spans(b[2], fmt({1: 18, 2: 16}.get(b[1], 14.5), 700))
        elif k == "li":
            cur.insertText(("•" if not b[1][0].isdigit() else b[1]) + "  ", fmt(fg=TEXT_3))
            spans(b[2], base)
        elif k == "code":
            cur.insertText(b[2], fmt(12.5, mono=True, fg="#d7dadf"))
        elif k == "quote":
            spans(b[1], fmt(italic=True, fg=TEXT_2))
        elif k == "hr":
            cur.insertText("—" * 24, fmt(fg="#3a3e43"))
        else:
            spans(b[1], base)


class Prose(QFrame):
    """Monday's reply as it was written (md_document), read only, as tall as its words: selectable, links open in the browser."""

    def __init__(self, text="", color=TEXT, name="Monday's reply"):
        super().__init__()
        from PySide6.QtWidgets import QTextBrowser
        self.view = QTextBrowser(self)
        self.view.setOpenLinks(False)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0; }")
        self.view.anchorClicked.connect(lambda u: QDesktopServices.openUrl(u) if u.scheme() in ("http", "https") else None)
        self.view.setAccessibleName(name)
        self.color, self.text = color, ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.set(text)

    def set(self, text):
        self.text = text or ""
        md_document(self.view.document(), self.text, self.color)
        self.view.setAccessibleDescription(self.text[:500])
        self._fit()

    def append(self, piece):
        """A reply being written: its words so far, plain until the stored one replaces it (MESSAGE_DELTA)."""
        from PySide6.QtGui import QTextCursor
        self.text += piece
        c = QTextCursor(self.view.document())
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertText(piece)
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        doc = self.view.document()
        doc.setTextWidth(max(80, self.width()))
        h = int(doc.size().height()) + 4
        if self.height() != h:
            self.setFixedHeight(h)


class TaskCard(Card):
    """A task's card (chat.py's): the title, its state in colour, its live steps, what it is doing or what came of it, whether the code
    as it stands was tested (Core's line), "N steps · took", Stop while it runs."""
    COLORS = {"AZURE": K.BLUE, "AMBER": K.AMBER, "GOOD": GREEN_TEXT, "BAD": K.RED_TEXT, "GREY": TEXT_3, "MUTED": TEXT_3}

    def __init__(self, t, stop):
        super().__init__(pad=16)
        self.lay.setSpacing(4)
        self.title = label("", 14, 700)
        self.state = label("", 12, 600, K.BLUE)
        self.steps = label("", 12, 400, "#c9ccd1", mono=True, wrap=True)
        self.detail = label("", 13, 400, TEXT_2, wrap=True, select=True)
        self.tests = label("", 12, 400, TEXT_3, wrap=True)
        self.foot = label("", 12, 400, TEXT_3)
        for w in (self.title, self.state, self.steps, self.detail, self.tests, self.foot):
            self.lay.addWidget(w)
        self.stop = Button("Stop", "danger", icon_name="stop", height=28, name=f"Stop this task: {t.get('title') or ''}")
        self.stop.clicked.connect(lambda: stop(t["id"]))
        row = QHBoxLayout()
        row.addWidget(self.stop)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.lines: list = []

    def fill(self, t):
        from .chat import RUNNING, STATES as TASK_STATES, tests_line, took
        words_, color = TASK_STATES.get(t.get("status"), ((t.get("status") or "N/A").replace("_", " ").title(), "MUTED"))
        self.title.setText(t.get("title") or t.get("id") or "")
        self.state.setText(f"●  {words_}")
        self.state.setStyleSheet(f"color: {self.COLORS.get(color, TEXT_3)}; background: transparent;")
        running = t.get("status") in RUNNING
        detail = t.get("current_action") if running else (t.get("error") if t.get("status") == "FAILED" else t.get("result"))
        self.detail.setText(str(detail or "")[:600])
        self.detail.setVisible(bool(detail) and not (running and self.lines))
        self.steps.setText("\n".join(f"{m}  {lb[:120]}" for m, lb in self.lines))
        self.steps.setVisible(bool(self.lines))
        n = max(len(t.get("steps") or []), len(self.lines))
        self.foot.setText(" · ".join(x for x in (f"{n} step{'s' if n != 1 else ''}" if n else "", took(t)) if x))
        self.foot.setVisible(bool(self.foot.text()))
        tl = tests_line(t)
        self.tests.setText(tl[0] if tl else "")
        self.tests.setStyleSheet(f"color: {self.COLORS.get(tl[1], TEXT_3) if tl else TEXT_3}; background: transparent;")
        self.tests.setVisible(bool(tl))
        self.stop.setVisible(running)
        self.setAccessibleName(f"Task: {self.title.text()}, {words_}")


class PermCard(Card):
    """An approval waiting (the old window's, above the composer): the question, its risk, what exactly it will do, and Approve /
    Always allow (only when Core offers it) / Deny. A click is the owner's answer; Core's reply (or why it wasn't recorded) shows."""

    def __init__(self, p, decide):
        super().__init__(up=True, pad=16)
        from .native import perm_question
        self.lay.setSpacing(4)
        self.lay.addWidget(label(perm_question(p), 14, 700, wrap=True))
        self.lay.addWidget(label(f"{(p.get('risk') or 'N/A').capitalize()} risk. Nothing runs until you answer.", 12.5, 600, K.AMBER))
        if p.get("detail"):
            self.lay.addWidget(label(str(p["detail"])[:300], 12.5, 400, TEXT_2, wrap=True, select=True))
        row = QHBoxLayout()
        self.buttons = []
        for words_, d, kind in (("Approve", "approve", "light"), ("Always allow", "always", "secondary"), ("Deny", "deny", "secondary")):
            if d == "always" and not p.get("always"):
                continue
            b = Button(words_, kind, height=30, name=f"{words_}: {perm_question(p)}")
            b.clicked.connect(lambda _=False, d=d: decide(p, d, self))
            self.buttons.append(b)
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.note = Notice("", "red")
        self.lay.addWidget(self.note)
        self.setAccessibleName(f"Approval: {perm_question(p)}")


class AskBar(Card):
    """One of the owner's agents asking (Claude Code's hook), on top of every page while it waits: the notch's Home shows the same one.
    Its words and Deny / Always / Allow, or Claude Code's question with its options as the answers. Answered here, in the notch or with
    Ctrl+Alt+Y / N, AGENT_ASK_DONE takes it away from both (the owner, 2026-10-20: "it should reflect in the app too")."""

    def __init__(self, win):
        super().__init__(up=True, pad=14)
        self.win, self.asks, self.q_for, self.q_at, self.picks = win, {}, None, 0, {}
        self.lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(Dot("wait", 8))
        self.title = label("", 13.5, 700)
        head.addWidget(self.title, 1)
        self.more = label("", 12, 400, TEXT_3)
        head.addWidget(self.more)
        self.lay.addLayout(head)
        self.line = label("", 12.5, 400, TEXT_2, wrap=True, select=True)
        self.lay.addWidget(self.line)
        self.row = QHBoxLayout()
        self.row.setSpacing(8)
        self.lay.addLayout(self.row)
        self.note = Notice("", "red")
        self.lay.addWidget(self.note)
        self.setAccessibleName("An agent asks you")
        self.hide()

    def on_event(self, t, d):
        if t == "AGENT_ASK" and (d.get("ask") or {}).get("id"):
            self.asks[d["ask"]["id"]] = d["ask"]
        elif t == "AGENT_ASK_DONE":
            self.asks.pop(d.get("id"), None)
        else:
            return
        self.note.say("")
        self.draw()

    def draw(self):
        from .notch import ask_words
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        a = list(self.asks.values())[-1] if self.asks else None
        self.setVisible(bool(a))
        if self.parentWidget() is not None:
            self.parentWidget().setVisible(bool(a))
        if not a:
            return
        who, qs = a.get("agent") or "An agent", a.get("questions")
        self.more.setText(f"{len(self.asks) - 1} more waiting" if len(self.asks) > 1 else "")
        if qs:
            if self.q_for != a["id"]:
                self.q_for, self.q_at, self.picks = a["id"], 0, {}
            q, picks = qs[self.q_at], self.picks.get(self.q_at) or []
            self.title.setText(f"{who} asks you" + (f" ({self.q_at + 1} of {len(qs)})" if len(qs) > 1 else ""))
            self.line.setText(q["question"] + (" (pick any)" if q["multi"] else ""))
            for o in q["options"]:
                on = o["label"] in picks
                b = Button(("✓ " if on else "") + o["label"], "light" if on else "secondary", height=30,
                           name=o["label"] + (f": {o['description']}" if o["description"] else ""))
                b.setToolTip(o["description"])
                b.clicked.connect(lambda _=False, lab=o["label"]: self._pick(a, lab))
                self.row.addWidget(b)
            other = field("Other: your own answer, then Enter", f"Your own answer to: {q['question'][:80]}", width=240)
            other.setMaxLength(500)  # (Core keeps as many: monday.ANSWER_CHARS)
            other.returnPressed.connect(lambda: self._other(a, other.text()))
            self.row.addWidget(other)
            if q["multi"]:
                done = Button("Done", "light", height=30, name="Done: send these picks")
                done.setEnabled(bool(picks))
                done.clicked.connect(lambda: self._next(a))
                self.row.addWidget(done)
            later = Button("Answer in Claude Code", "quiet", height=30, name="Answer this in Claude Code instead")
            later.clicked.connect(lambda: self._post(a, "gone"))
            self.row.addWidget(later)
        else:
            self.title.setText(f"{who} wants to {a.get('what') or 'act'}")
            keys = self.win.hotkeys is None or all(self.win.hotkeys.status(k)[0] == "on" for k in ("allow", "deny"))
            self.line.setText(ask_words(a, keys=keys).split("\n", 1)[-1])
            for words_, dcs, kind in (("Allow", "allow", "light"), ("Always", "always", "secondary"), ("Deny", "deny", "secondary")):
                if dcs == "always" and not a.get("always"):
                    continue
                b = Button(words_, kind, height=30, name=f"{words_}: {who} wants to {a.get('what') or 'act'}")
                b.clicked.connect(lambda _=False, dcs=dcs: self._post(a, dcs))
                self.row.addWidget(b)
        self.row.addStretch(1)

    def _pick(self, a, label_):
        q = a["questions"][self.q_at]
        picks = self.picks.setdefault(self.q_at, [])
        if q["multi"]:
            picks.remove(label_) if label_ in picks else picks.append(label_)
            return self.draw()
        picks[:] = [label_]
        self._next(a)

    def _other(self, a, text):
        """The owner's own words as the answer (with a multi-select's picks), as the notch's "Other…" (2026-10-20)."""
        text = " ".join((text or "").split())
        if text:
            picks = self.picks.setdefault(self.q_at, [])
            picks[:] = [*picks, text] if a["questions"][self.q_at]["multi"] else [text]
            self._next(a)

    def _next(self, a):
        if self.q_at + 1 < len(a["questions"]):
            self.q_at += 1
            return self.draw()
        self._post(a, "allow", {q["question"]: ", ".join(self.picks.get(i) or []) for i, q in enumerate(a["questions"])})

    def _post(self, a, decision, answers=None):
        self.win.bridge.post(f"/api/agents/ask/{a['id']}", {"decision": decision, **({"answers": answers} if answers else {})},
                             lambda _d, err: self.note.say(f"Your answer wasn't recorded: {err}") if err else None)


class ReflexStrip(QWidget):
    """Debug mode's reflex strip (the old HUD's): Jev's read of the sentence being said (POST /api/prepass, native.reflex_view): the pill's
    line, then a row per question: its words, the bar with Core's gate as a tick, the number or N/A, the verdict word, a crossing's
    receipt. Every number is Core's; it only informs: nothing here sends, holds or blocks anything."""
    ROW = 22

    def __init__(self):
        super().__init__()
        self.setAccessibleName("Reflex: Jev's read of what you said (debug mode)")
        self.set(None)

    def set(self, view):
        from .native import reflex_view
        self.view = view or reflex_view(None, None, {"cost": 0.0, "prev": {}, "receipts": {}})
        self.setFixedHeight(8 + self.ROW * (1 + len(self.view["rows"])))
        self.setAccessibleDescription(self.view["text"])
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(EDGE), 1))
        p.setBrush(QColor(K.CARD))
        p.drawRoundedRect(r, 10, 10)
        v, h, w = self.view, self.ROW, self.width()
        mid_left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        p.setFont(font(12, 600))
        p.setPen(QColor({"ok": GREEN_TEXT, "off": TEXT_3, "bad": K.RED_TEXT}[v["tone"]]))
        p.drawText(QRectF(12, 4, w - 24, h), mid_left, p.fontMetrics().elidedText(v["pill"], Qt.TextElideMode.ElideRight, w - 24))
        left = min(200, w // 3)  # the words' column; after the bar: the number, the verdict and the receipt
        x0, x1 = left + 12, max(left + 40, w - 300)
        p.setFont(font(12))
        for i, (words_, val, gate, verdict, receipt) in enumerate(v["rows"], 1):
            y = 4 + i * h
            mid = y + h / 2
            p.setPen(QColor(TEXT_2))
            p.drawText(QRectF(12, y, left - 12, h), mid_left, words_)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#26292d"))
            p.drawRoundedRect(QRectF(x0, mid - 3, x1 - x0, 6), 3, 3)
            if val is not None:
                p.setBrush(QColor(ORANGE))
                p.drawRoundedRect(QRectF(x0, mid - 3, (x1 - x0) * min(max(val, 0.0), 1.0), 6), 3, 3)
            if gate is not None:
                p.setBrush(QColor(TEXT))
                p.drawRect(QRectF(x0 + (x1 - x0) * gate - 1, mid - 6, 2, 12))
            p.setPen(QColor(TEXT_3 if val is None else TEXT))
            p.drawText(QRectF(x1 + 10, y, 48, h), mid_left, "N/A" if val is None else f"{val:.0%}")
            p.setPen(QColor(TEXT_2))
            p.drawText(QRectF(x1 + 60, y, 80, h), mid_left, verdict or "")
            p.setPen(QColor(K.AMBER))
            p.drawText(QRectF(x1 + 145, y, max(0, w - x1 - 155), h), mid_left, receipt or "")
        p.end()


class PillsScreen(Screen):
    """Active pills (the recording's 15–16; the owner's choice 2026-10-20): which of the agents and tools Monday found show beside the pet
    on the notch's Home, at most notch.PILLS_MAX, each with its state as Core last found it (GET /api/ai-tools). Kept in data/desktop.json
    ("pet" → "pills"; None: Monday's pick, notch.auto_pills); the notch takes a change at once."""
    key, icon, name, section = "pills", "grid", "Active pills", "Notch"
    GROUPS = (("Your editors", ("editor",)), ("Computer agent and models", ("computer agent", "models", "agent")),
              ("Apps", ("app",)))  # (no command-line coding agents: the owner, 2026-10-20, "don't use the CLIs" in the pills; notch.PILL_KINDS)

    def build(self):
        self.page.add(Hero("Active pills", "The agents and tools beside your pet in the notch, each as Monday last found it.", "gold",
                           ("yellow", "blue", "green", "orange", "purple")))
        self.card = Card("Active pills", "Choose up to 4 to show on the notch's Home, next to the pet. Open IDE windows come first; a "
                                         "pill shows its state, and a click opens its agent here.")
        refresh = Button("Look again", "ghost", icon_name="refresh", name="Look for agents and tools again")
        refresh.clicked.connect(lambda: self.bridge.get("/api/ai-tools?refresh=true", self._got_tools))
        self.card.head.addWidget(refresh)
        top = QHBoxLayout()
        self.slots = label("", 12.5, 600, K.AMBER)
        top.addWidget(self.slots, 1)
        self.auto = Button("Let Monday choose", "secondary", name="Let Monday choose the pills (its agents, the ready ones first)")
        self.auto.clicked.connect(lambda: self._save(None))
        top.addWidget(self.auto)
        self.card.lay.addLayout(top)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        self.card.lay.addLayout(self.rows)
        self.note = Notice("", "red")
        self.card.lay.addWidget(self.note)
        self.page.add(self.card)
        self.page.lay.addStretch(1)
        self.tools: dict = {}

    def load(self):
        super().load()
        self.bridge.get("/api/ai-tools", self._got_tools)

    def chosen(self):
        """The pills shown now: the owner's, else Monday's pick from what Core found."""
        from .notch import auto_pills, prefs
        mine = prefs(self.win.store.get("pet"))["pills"]
        return (mine, True) if mine is not None else (auto_pills(self.tools), False)

    def _got_tools(self, d, err):
        if err:
            return self.note.say(f"The agents and tools couldn't be read: {err}")
        self.note.say("")
        self.tools = {t.get("key"): t for t in (d or {}).get("tools") or [] if isinstance(t, dict) and t.get("key")}
        self._draw()

    def _draw(self):
        from .notch import PILL_COLOURS, PILLS_MAX, pill_state
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        keys, own = self.chosen()
        self.slots.setText(f"{len(keys)}/{PILLS_MAX} in use" + ("" if own else ": Monday's pick (the agents it found, ready ones first) "
                                                                                "until you choose"))
        self.auto.setEnabled(own)
        for title, kinds in self.GROUPS:
            group = [t for t in self.tools.values() if t.get("kind") in kinds]
            if not group:
                continue
            self.rows.addSpacing(10)
            self.rows.addWidget(label(title, 12.5, 600, TEXT_2))
            for t in sorted(group, key=lambda t: (t.get("installed") is not True, str(t.get("name")))):
                dot, words = pill_state(t)
                on = t["key"] in keys
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 4, 0, 4)
                h.setSpacing(10)
                h.addWidget(Dot(PILL_COLOURS.get(t["key"], K.PALS["purple"]), 9, t.get("name") or ""))
                h.addWidget(label(t.get("name") or t["key"], 13, 600 if on else 400, TEXT if on else TEXT_2), 1)
                h.addWidget(label(words, 12, 400, K.Dot.COLORS[dot] if dot != "off" else TEXT_3))
                sw = K.Toggle(f"{t.get('name') or t['key']} in the notch's pills: on or off ({words})", on)
                sw.toggled.connect(lambda v, k=t["key"], s=sw: self._flip(k, v, s))
                h.addWidget(sw)
                self.rows.addWidget(row)

    def _flip(self, key, on, sw):
        from .notch import PILLS_MAX
        keys, _own = self.chosen()
        keys = [k for k in keys if k != key] + ([key] if on else [])
        if len(keys) > PILLS_MAX:
            sw.set_quiet(False)
            return self.note.say(f"{PILLS_MAX}/{PILLS_MAX} in use: turn one off first.", "amber")
        self.note.say("")
        self._save(keys)

    def _save(self, keys):
        from .notch import prefs
        self.win.keep("pet", prefs({**prefs(self.win.store.get("pet")), "pills": keys}))
        self._draw()


class ChatScreen(Screen):
    """Chat (Monday's own; the recording's Chat page is only its keys, which live in Integrations here): the conversation with Monday
    as Core keeps it (GET /api/messages; MESSAGE_DELTA / MESSAGE_CREATED live), task cards following their tasks, approvals above the
    composer, earlier chats read only, and the composer: + a file, the microphone, Send (Stop while a task of this chat runs and the
    box is empty), who works and which model answers."""
    key, icon, name, section = "chat", "chat", "Chat", "Monday"
    MESSAGES_MAX = 300
    FRAME_MS = 50  # a streamed reply is drawn at most this often (per delta, the whole reply and the column were laid out again: the chat lagged)

    def build(self):
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the conversation scrolls, the composer stays
        self.page.lay.setContentsMargins(0, 0, 0, 12)
        acts = QWidget()
        ah = QHBoxLayout(acts)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(8)
        new = Button("New chat", "secondary", icon_name="plus")
        new.clicked.connect(self._new_chat)
        ah.addWidget(new)
        self.chats_btn = Button("Earlier chats", "secondary", icon_name="clock", name="Open an earlier chat")
        self.chats_btn.clicked.connect(self._chats_menu)
        ah.addWidget(self.chats_btn)
        self.hero = Hero("Chat", "Talk to Monday: it does the work on this computer and says what really happened.", "ember",
                         ("red", "white"), action=acts)
        self.hero.setFixedHeight(150)
        self.page.add(self.hero)
        self.banner = Notice("", "plain")
        self.back_btn = Button("Back to this chat", "secondary")
        self.back_btn.clicked.connect(self._back)
        self.banner.layout().addWidget(self.back_btn)
        self.banner.hide()
        self.page.add(self.banner)
        conv = Card(pad=0)
        conv.lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("Conversation")
        K.SmoothScroll(self.scroll)
        inner = QWidget()
        ih = QHBoxLayout(inner)
        ih.setContentsMargins(24, 18, 24, 18)
        ih.addStretch(1)
        self.column = QWidget()
        self.column.setMaximumWidth(780)  # the reading column (chat.py COLUMN): ~80 characters a line
        self.rows = QVBoxLayout(self.column)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(14)
        self.rows.addStretch(1)
        ih.addWidget(self.column, 100)
        ih.addStretch(1)
        self.scroll.setWidget(inner)
        conv.lay.addWidget(self.scroll)
        self.page.add(conv, 1)
        self.perms = QVBoxLayout()
        self.perms.setSpacing(8)
        self.page.lay.addLayout(self.perms)
        self.page.add(self._composer())
        self.cards: dict[str, TaskCard] = {}
        self.tasks: dict[str, dict] = {}
        self.streams: dict[str, Prose] = {}
        self._delta: dict[str, str] = {}  # words streamed since the last frame, per stream (shown together: _flush_deltas)
        self._live_model: dict = {}       # the model writing each stream: another taking the reply over starts its words again (on_event)
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(self.FRAME_MS)
        self._flush.timeout.connect(self._flush_deltas)
        self.voice_state: dict = {}
        self.shown: set = set()
        self.said: dict = {}
        self.perm_cards: dict = {}
        self.viewing = None
        self.attachments: list[tuple[str, str]] = []

    def _composer(self):
        box = QFrame()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.reflex = ReflexStrip()  # debug mode only (Core's voice settings): Jev's read of the sentence being said
        if self.win.rx_view:
            self.reflex.set(self.win.rx_view)
        self.reflex.setVisible(self.win.debug())
        v.addWidget(self.reflex)
        self.heard_lbl = label("", 12, 400, TEXT_3)
        self.heard_lbl.hide()
        v.addWidget(self.heard_lbl)
        self.attach_row = QHBoxLayout()
        self.attach_row.setSpacing(6)
        v.addLayout(self.attach_row)
        row = QHBoxLayout()
        row.setSpacing(8)
        plus = K.IconButton("plus", "Attach a file", size=34)
        plus.clicked.connect(self._attach)
        row.addWidget(plus)
        from .qtnotch import Prompt, SendButton
        self.box = Prompt()
        self.box.setPlaceholderText("Ask Monday or tell it what to do")
        self.box.setAccessibleName("Ask Monday or tell it what to do")
        self.box.send.connect(self.send)
        self.box.escape.connect(lambda: self.box.clear())
        self.box.textChanged.connect(self._busy)
        row.addWidget(self.box, 1)
        self.send_btn = SendButton(self.box)
        self.send_btn.clicked.connect(self._send_or_stop)
        self.mic = K.IconButton("mic", "Microphone: on or off (Ctrl+M)", size=34)
        self.mic.clicked.connect(self.win.toggle_mic)
        row.addWidget(self.mic)
        v.addLayout(row)
        under = QHBoxLayout()
        under.setSpacing(8)
        self.who = Button("Who works ▾", "quiet", height=24, name="Who does the work: pick")
        self.who.clicked.connect(self._who_menu)
        self.model = Button("Model ▾", "quiet", height=24, name="Which model answers: pick")
        self.model.clicked.connect(self._model_menu)
        self.voice = Button("Voice ▾", "quiet", height=24, name="Whose voice speaks the replies: pick")
        self.voice.clicked.connect(self._voice_menu)
        under.addWidget(self.who)
        under.addWidget(self.model)
        under.addWidget(self.voice)
        under.addStretch(1)
        self.note = label("", 12, 400, K.RED_TEXT)
        under.addWidget(self.note)
        v.addLayout(under)
        return box

    # loading
    def load(self):
        super().load()
        self.mic_changed(bool(self.win.ear and self.win.ear.on))
        if self.viewing is None:  # this chat's start first, then its newest messages (the old view's order)
            self.bridge.get("/api/chats", lambda d, e: (self._got_chats(d, e), self.bridge.get("/api/messages?limit=40", self._got_messages)))
        self.bridge.get("/api/status", self._got_status)
        self.bridge.get("/api/settings", lambda d, e: setattr(self, "settings", d or {}) or self._who_words())
        self.bridge.get("/api/models", self._got_models)
        self.bridge.get("/api/voice", self._got_voice_pick)
        QTimer.singleShot(0, lambda: self.box.setFocus())

    def _got_chats(self, d, err):
        self.chats = (d or {}).get("chats") or []
        cur = next((c for c in self.chats if c.get("current")), None)
        self.chat_start = (cur or {}).get("start", 0)

    def _got_messages(self, d, err):
        self._clear()
        start = getattr(self, "chat_start", 0)
        rows = [m for m in (d or {}).get("messages") or [] if (m.get("id") or 0) > start]
        for m in rows:
            self.message(m)
        if err:
            self.note_line(f"Messages couldn't be loaded: {err}", K.RED_TEXT)
        if not rows and not err:
            self._greet()
        self._to_end()

    def _got_status(self, d, err):
        for p in (d or {}).get("pending_permissions") or []:
            self._perm(p)

    # the conversation
    def _clear(self):
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards.clear()
        self.streams.clear()
        self.shown.clear()

    def _add(self, w):
        self.rows.insertWidget(self.rows.count() - 1, w)
        while self.rows.count() - 1 > self.MESSAGES_MAX:
            old = self.rows.takeAt(0).widget()
            if old:
                old.deleteLater()
        return w

    def _to_end(self):
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def note_line(self, text, color=TEXT_3):
        return self._add(label(text, 12.5, 400, color, wrap=True, select=True))

    def message(self, m):
        meta = m.get("meta") or {}
        if meta.get("chat_start"):
            self._clear()
            return self._greet()
        if m.get("id") is not None:
            if m["id"] in self.shown:
                return None
            self.shown.add(m["id"])
        if getattr(self, "greeting", None) is not None:
            self.greeting.deleteLater()
            self.greeting = None
        if m.get("kind") == "permission" and m.get("permission"):
            return self._perm(m["permission"])
        if m.get("kind") == "task" and m.get("task"):
            return self.task(m["task"], new=True)
        if m.get("role") == "user":
            self.said[m.get("id")] = m.get("text") or ""
            if m.get("source") == "goal":
                return self.note_line(f"Goal step: {m.get('text') or ''}", K.BLUE)
            if m.get("source") == "webhook":
                return self.note_line(f"Webhook fired: {m.get('text') or ''}", K.BLUE)
            return self._bubble(m.get("text") or "", meta)
        if m.get("kind") == "system" or meta.get("quiet"):
            return self.note_line(m.get("text") or "")
        if m.get("kind") == "options" and meta.get("options"):
            return self._options(m)
        return self._reply(m)

    def _bubble(self, text, meta):
        files = [a.get("name") for a in meta.get("attachments") or [] if a.get("name")]
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)
        b = label(text + (("\n📎 " + ", ".join(files)) if files else ""), 13.5, 400, TEXT, wrap=True, select=True)
        b.setStyleSheet(f"color: {TEXT}; background: #1d2024; border: 1px solid #2a2e33; border-radius: 14px; padding: 8px 13px;")
        b.setMaximumWidth(560)
        widest = max((b.fontMetrics().horizontalAdvance(ln) for ln in b.text().split("\n")), default=0)
        b.setMinimumWidth(min(560, widest + 30))  # (a wrapped label otherwise shrinks to its narrowest: the owner's words in a sliver)
        b.setAccessibleName(f"You: {text[:200]}")
        h.addWidget(b)
        return self._add(row)

    def _reply(self, m):
        from .chat import plain
        meta, text, err = m.get("meta") or {}, m.get("text") or "", m.get("kind") == "error"
        doc = meta.get("document") if meta.get("brief") else None  # a long answer made into a document: its brief, the whole a click away
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        prose = Prose(meta["brief"] if doc else text, K.RED_TEXT if err else TEXT, f"{self.win.rail.name.text()} said")
        v.addWidget(prose)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        if doc:
            opener = Button("Open", "quiet", height=22, name=f"Open {doc['name']} in {doc.get('app') or 'its app'}")
            opener.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(doc["path"])) if os.path.exists(doc["path"])
                                   else opener.setText("Moved or deleted"))
            folder = Button("Show in folder", "quiet", height=22, name=f"Show {doc['name']} in its folder")
            folder.clicked.connect(lambda: K.reveal(doc["path"]))
            whole = Button("Whole answer", "link", height=22, name="Show the whole answer here, or only its start")
            whole.clicked.connect(lambda: (prose.set(meta["brief"] if prose.text == text else text),
                                           whole.setText("Less" if prose.text == text else "Whole answer")))
            for b in (opener, folder, whole):
                foot.addWidget(b)
        copy = Button("Copy", "quiet", height=22, name="Copy this reply")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(plain(text) or text))
        foot.addWidget(copy)
        asked = self.said.get(meta.get("reply_to"))
        if err and asked:
            again = Button("Try again", "link", height=22, name=f"Send it again: {asked[:80]}")
            again.clicked.connect(lambda: self.send(asked))
            foot.addWidget(again)
        if meta.get("model"):
            fb = meta.get("fallback_from")
            foot.addWidget(label(meta["model"] + (f" · after {fb} didn't answer" if fb else ""), 11.5, 400, TEXT_3))
        foot.addStretch(1)
        v.addLayout(foot)
        return self._add(box)

    def _options(self, m):
        meta = m.get("meta") or {}
        bot, opts = meta.get("bot"), [str(o) for o in meta.get("options") or []][:6]
        card = Card(m.get("text") or "", meta.get("note"), up=True, pad=16)
        chosen = []

        def pick(i, buttons):
            if chosen:
                return
            chosen.append(i)
            for k, b in enumerate(buttons):
                b.kind = "light" if k == i else "ghost"
                b.setAccessibleName(f"{'Chosen' if k == i else 'Not chosen'}: {opts[k]}")
                b.update()
            self.send(f"{bot}, {opts[i]}" if bot else opts[i])
        buttons = []
        for i, o in enumerate(opts):
            b = Button(f"{chr(65 + i)}   {o}", "secondary", height=30, name=f"Answer {chr(65 + i)}: {o}")
            b.clicked.connect(lambda _=False, i=i: pick(i, buttons))
            buttons.append(b)
            card.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        card.lay.addWidget(label("Or type your own answer below.", 12, 400, TEXT_3))
        return self._add(card)

    def task(self, t, new=False):
        old = self.tasks.get(t.get("id"))
        t = {**old, **t} if old else t
        self.tasks[t["id"]] = t
        card = self.cards.get(t["id"])
        if card is None and new and self.viewing is None:
            card = self.cards[t["id"]] = self._add(TaskCard(t, self._stop))
            from .chat import step_lines
            card.lines = step_lines(t.get("steps"))
        if card is not None:
            card.fill(t)
        self._busy()
        return card

    def step(self, tid, s):
        from .chat import STEPS_SHOWN, step_lines
        card = self.cards.get(tid)
        if card is None:
            return
        got = step_lines([s])
        if got:
            mark, lb = got[0]
            i = next((k for k in range(len(card.lines) - 1, -1, -1) if card.lines[k][1] == lb), None)
            if i is None:
                card.lines.append((mark, lb))
                del card.lines[:-STEPS_SHOWN]
            else:
                card.lines[i] = (mark, lb)
        self.task({**self.tasks.get(tid, {"id": tid}), **({"current_action": s.get("label")} if s.get("status") == "running" else {})})

    def _stop(self, tid):
        self.bridge.post(f"/api/tasks/{tid}/stop", None, lambda _d, err: self._say(f"Couldn't stop it: {err}") if err else None)

    def _greet(self):
        from .chat import SUGGESTIONS
        g = Empty(f"A new chat. Say what you want; {self.win.rail.name.text()} does it on this computer.", "idle")
        chips = QHBoxLayout()
        chips.addStretch(1)
        self.sugg = chips
        for s in SUGGESTIONS:
            b = Button(s, "secondary", height=28, name=f"Put in the box: {s}")
            b.clicked.connect(lambda _=False, s=s: (self.box.setPlainText(s), self.box.setFocus()))
            chips.addWidget(b)
        chips.addStretch(1)
        g.layout().addLayout(chips)
        self.greeting = self._add(g)
        self.bridge.get("/api/side", lambda d, e: self._personal(d))
        return g

    def _personal(self, side):
        """A new chat's suggestions from the owner's own things (chat.personal_suggestions): only when they exist."""
        from .chat import personal_suggestions
        if getattr(self, "greeting", None) is None:
            return
        got = personal_suggestions((side or {}).get("todos") or [], [], [])
        if got and got != list(personal_suggestions())[:4]:
            buttons = [self.sugg.itemAt(i).widget() for i in range(self.sugg.count()) if self.sugg.itemAt(i).widget()]
            for b, s in zip(buttons, got):
                b.setText(s)
                b.setAccessibleName(f"Put in the box: {s}")
                b.clicked.disconnect()
                b.clicked.connect(lambda _=False, s=s: (self.box.setPlainText(s), self.box.setFocus()))

    # live
    def on_event(self, ev):
        t, d = ev.get("type"), ev.get("data") or {}
        if t == "MESSAGE_DELTA" and d.get("stream_id") and not d.get("chatroom") and self.viewing is None:
            sid = d["stream_id"]
            if self._live_model.get(sid, d.get("model")) != d.get("model"):  # another model took the reply over (the first refused, called
                self._delta[sid] = ""                                         # a tool or stalled): its words alone, 2026-10-27
                if sid in self.streams:
                    self.streams[sid].set("")
            self._live_model[sid] = d.get("model")
            self._delta[sid] = self._delta.get(sid, "") + (d.get("delta") or "")
            if not self._flush.isActive():
                self._flush.start()
        elif t == "MESSAGE_CREATED" and d.get("message") and not (d["message"].get("meta") or {}).get("chatroom"):
            m = d["message"]
            if m.get("source") == "preflight" or self.viewing is not None:
                return
            self._delta.pop((m.get("meta") or {}).get("stream_id"), None)  # the stored reply replaces what was still to be drawn
            self._live_model.pop((m.get("meta") or {}).get("stream_id"), None)
            got = self.streams.pop((m.get("meta") or {}).get("stream_id"), None)
            if got is not None:
                got.deleteLater()
            self.message(m)
            self._to_end()
        elif t in ("TASK_CREATED", "TASK_STARTED", "TASK_PROGRESS", "TASK_TESTS", "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED") \
                and d.get("task"):
            self.task(d["task"])
        elif t == "TASK_STEP" and ev.get("task_id") and d.get("step"):
            self.step(ev["task_id"], d["step"])
        elif t.startswith("PERMISSION_") and d.get("permission"):
            self._perm(d["permission"])
        elif t in ("MODEL_CHANGED", "SETTINGS_CHANGED"):
            self.bridge.get("/api/models", self._got_models)
            self.bridge.get("/api/settings", lambda dd, e: setattr(self, "settings", dd or {}) or self._who_words())
        elif t == "VOICE_CHANGED":
            self.bridge.get("/api/voice", self._got_voice_pick)

    def _flush_deltas(self):
        """The words streamed since the last frame, drawn in one go per reply (2026-10-20: the chat lagged, re-laying the whole reply and
        the column for every few words). The view follows the reply only while it is at the end: reading back up, it stays put."""
        sb = self.scroll.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 48
        for sid, words_ in self._delta.items():
            p = self.streams.get(sid)
            if p is None:
                p = self.streams[sid] = self._add(Prose("", TEXT, "Monday is writing"))
            p.append(words_)
        self._delta.clear()
        if follow:
            self._to_end()

    def mic_changed(self, on):
        self.mic.set_lit(on)
        self.mic.setAccessibleName(f"Microphone: {'on' if on else 'off'} (Ctrl+M)")

    def heard(self, text, final):
        """The ear's words as they come (the old window's line above the box)."""
        self.heard_lbl.setText(("Sent: " if final else "Hearing: ") + text if text else "")
        self.heard_lbl.setVisible(bool(text))

    # approvals
    def _perm(self, p):
        from .native import perm_question
        pid = p.get("id")
        card = self.perm_cards.get(pid)
        if p.get("status") != "PENDING":
            if card is not None:
                card.deleteLater()
                del self.perm_cards[pid]
                by = {"always allow": " by your Always allow", "auto mode": " by auto mode"}.get(p.get("decided_by") or "", "")
                self.note_line(f"{perm_question(p)} {'Granted' if p.get('status') == 'GRANTED' else 'Denied'}{by}.",
                               GREEN_TEXT if p.get("status") == "GRANTED" else TEXT_3)
            return None
        if card is None:
            card = self.perm_cards[pid] = PermCard(p, self._decide)
            self.perms.addWidget(card)
        return card

    def _decide(self, p, decision, card):
        for b in card.buttons:
            b.setEnabled(False)

        def done(r, err):
            if err:
                card.note.say(f"Your answer wasn't recorded: {err}")
                for b in card.buttons:
                    b.setEnabled(True)
            elif r:
                self._perm(r)
        self.bridge.post(f"/api/permissions/{p['id']}/{decision}", None, done)

    # sending
    def _running(self):
        from .chat import RUNNING
        return [tid for tid, t in self.tasks.items() if t.get("status") in RUNNING]

    def _busy(self):
        """Send while there are words (or nothing runs); Stop while a task of this chat runs and the box is empty (chat.py's)."""
        stop = bool(self._running()) and not self.box.toPlainText().strip()
        self.send_btn.setAccessibleName("Stop what this chat's task is doing" if stop else "Send")
        self.send_btn.setToolTip("Stop" if stop else "Send (Enter)")
        self.send_btn.stopping = stop
        self.send_btn.update()

    def _send_or_stop(self):
        if getattr(self.send_btn, "stopping", False):
            for tid in self._running():
                self._stop(tid)
        else:
            self.send()

    def send(self, text=None):
        text = (text if isinstance(text, str) else self.box.toPlainText()).strip()
        if not text:
            return
        if self.viewing is not None:
            self._back()
        atts = [fid for fid, _n in self.attachments]
        if text == self.box.toPlainText().strip():
            self.box.clear()
        self.attachments.clear()
        self._attach_chips()

        def done(_d, err):
            if err:
                self._say(f"Not sent: {err}")
                if not self.box.toPlainText():
                    self.box.setPlainText(text)
            else:
                self._say("")
        self.win.command(text, atts, done=done)

    def _say(self, words):
        self.note.setText(words)

    def _attach(self):
        from PySide6.QtWidgets import QFileDialog
        from .notch import examine
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file for Monday")
        if not path:
            return
        f = examine(path)
        if not f["ok"]:
            return self._say(f["why"])
        self._say(f"Copying {f['name']}…")

        def upload():  # (POST /api/upload: the raw file, its name in a header; Core keeps it in data/uploads and says its id)
            with open(f["path"], "rb") as fh:
                r = self.bridge.core.http.post("/api/upload", content=fh.read(), headers={"x-filename": urllib.parse.quote(f["name"])},
                                               timeout=300)
            data = r.json() if r.content else {}
            return data, None if r.status_code < 400 else (data.get("detail") or f"HTTP {r.status_code}")
        self.bridge.run(upload, lambda d, e: self._attached(f, d, e))

    def _attached(self, f, d, err):
        if err or not (d or {}).get("id"):
            return self._say(f"Couldn't attach {f['name']}: {err or 'Core kept nothing'}")
        self._say("")
        self.attachments.append((d["id"], f["name"]))
        self._attach_chips()

    def _attach_chips(self):
        while self.attach_row.count():
            w = self.attach_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for fid, name in self.attachments:
            c = K.Chip(f"📎 {name}", "dark")
            self.attach_row.addWidget(c)
            x = K.IconButton("x", f"Take {name} off", size=22)
            x.clicked.connect(lambda _=False, fid=fid: (setattr(self, "attachments", [a for a in self.attachments if a[0] != fid]),
                                                        self._attach_chips()))
            self.attach_row.addWidget(x)
        self.attach_row.addStretch(1)

    # who works, which model, which chat
    def _who_words(self):
        from .native import who_words
        self.who.setText(who_words(getattr(self, "settings", {})))

    def _who_menu(self):
        from PySide6.QtWidgets import QMenu
        from .notch import who_menu

        def got(tools, err):
            m = QMenu(self)
            for section, items in who_menu(getattr(self, "settings", {}), (tools or {}).get("tools") or [], getattr(self, "models", {}))[:2]:
                m.addSection(section)
                for what, value, words_, chosen, enabled in items:
                    a = m.addAction(words_)
                    a.setCheckable(True)
                    a.setChecked(chosen)
                    a.setEnabled(enabled)
                    key = "default_computer_agent" if what == "computer" else "default_coding_agent"
                    a.triggered.connect(lambda _=False, k=key, v=value: self.bridge.post("/api/settings", {k: v}, lambda d, e: (
                        self._say(f"Couldn't switch: {e}") if e else None,
                        self.bridge.get("/api/settings", lambda dd, ee: setattr(self, "settings", dd or {}) or self._who_words()))))
            if err:
                m.addAction(f"Couldn't read the agents: {err}").setEnabled(False)
            m.exec(QCursor.pos())
        self.bridge.get("/api/ai-tools", got)

    def _got_models(self, d, err):
        self.models = d or {}
        sel = next((m for m in (d or {}).get("models") or [] if m.get("selected")), None)
        eff = (d or {}).get("effective")
        self.model.setText("Model: N/A ▾" if err or not sel else (f"Automatic ({eff}) ▾" if sel.get("id") == "auto" and eff
                                                                   else f"{sel.get('name') or sel.get('id')} ▾"))

    def _model_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        for mod in (getattr(self, "models", {}) or {}).get("models") or []:
            if mod.get("for_chat") is False:
                continue
            a = m.addAction(mod.get("name") or mod.get("id"))
            a.setCheckable(True)
            a.setChecked(bool(mod.get("selected")))
            a.setEnabled(bool(mod.get("available")))
            a.triggered.connect(lambda _=False, i=mod.get("id"): self.bridge.post("/api/models/select", {"id": i}, lambda d, e: (
                self._say(f"Couldn't switch the model: {e}") if e else None, self.bridge.get("/api/models", self._got_models))))
        if m.isEmpty():
            m.addAction("The models couldn't be read").setEnabled(False)
        m.exec(QCursor.pos())

    # the voice the replies are spoken in (2026-10-20: "add the TTS and a custom one too": every speaker Core knows, the owner's own voice
    # among them; Settings → Voice has the rest: its voices, speed, pitch, the recording for the owner's own)
    def _got_voice_pick(self, d, err):
        from .native import voice_choices
        self.voice_state = d or {}
        cur = next((short for _pid, short, _st, chosen in voice_choices(d) if chosen), None)
        self.voice.setText("Voice: N/A ▾" if err or not cur else f"Voice: {cur} ▾")

    def _voice_menu(self):
        from PySide6.QtWidgets import QMenu
        from .native import voice_choices
        m = QMenu(self)
        for pid, short, status, chosen in voice_choices(self.voice_state):
            a = m.addAction(f"{short}  ·  {status}")
            a.setCheckable(True)
            a.setChecked(chosen)
            a.triggered.connect(lambda _=False, i=pid: self.bridge.post("/api/voice/settings", {"tts_provider": i}, lambda d, e: (
                self._say(f"Couldn't switch the voice: {e}") if e else None, self.bridge.get("/api/voice", self._got_voice_pick))))
        if m.isEmpty():
            m.addAction("The voices couldn't be read").setEnabled(False)
        m.addSeparator()
        m.addAction("Voice settings: voices, speed, your own recording…").triggered.connect(lambda: self.win.show_page("voice"))
        m.exec(QCursor.pos())

    def _chats_menu(self):
        from PySide6.QtWidgets import QMenu
        from .chat import when

        def got(d, err):
            m = QMenu(self)
            for c in ((d or {}).get("chats") or [])[:30]:
                a = m.addAction(f"{c.get('title') or 'New chat'}  ·  {'this chat' if c.get('current') else when(c.get('at'))}")
                a.triggered.connect(lambda _=False, c=c: self._open_chat(c))
            if m.isEmpty():
                m.addAction(f"Couldn't read the chats: {err}" if err else "No earlier chats yet").setEnabled(False)
            m.exec(QCursor.pos())
        self.bridge.get("/api/chats", got)

    def _open_chat(self, c):
        from .chat import when
        if c.get("current"):
            return self._back()

        def got(d, err):
            self._clear()
            self.viewing = None
            for m in (d or {}).get("messages") or []:
                if not (m.get("meta") or {}).get("chat_start"):
                    self.message(m)
            if err:
                self.note_line(f"This chat couldn't be loaded: {err}", K.RED_TEXT)
            self.viewing = c
            self.banner.say(f"An earlier chat, read only: {c.get('title') or 'New chat'} · {when(c.get('at'))}", "plain")
            self.banner.show()
        self.bridge.get(f"/api/messages?after={c['start']}&before={c.get('end') or 2 ** 62}&limit=200", got)

    def _back(self):
        self.viewing = None
        self.banner.hide()
        self.load()

    def _new_chat(self):
        self.bridge.post("/api/chats/new", None, lambda _d, err: self._say(f"No new chat: {err}") if err else None)


# -- Tasks: Monday's own and the owner's to-dos (core/side.py) ------------------------------------------------------------------
TASK_DOTS = {"completed": "ok", "failed": "bad", "cancelled": "off", "queued": "wait", "waiting": "wait", "waiting for user": "wait",
             "waiting for permission": "wait"}  # any other state is a task at work: blue
TASK_DONE = ("completed", "failed", "cancelled")


class SideRow(QWidget):
    """One of Monday's tasks or one of the owner's to-dos (native.task_rows / todo_rows): a to-do's tick or a task's state, its title,
    its group, when, and ⋯ for what can be done to it."""

    def __init__(self, screen: "TasksScreen", r: dict):
        super().__init__()
        self.r = r
        h = QHBoxLayout(self)
        h.setContentsMargins(2, 4, 2, 4)
        h.setSpacing(10)
        if r["kind"] == "todo":
            self.tick = K.Tick(f"Done: {r['title']}", r["done"])
            self.tick.toggled.connect(lambda on: screen.apply("done", r, done=on))
            h.addWidget(self.tick)
        else:
            h.addWidget(Dot(TASK_DOTS.get(r["status"], "info"), 8, r["status"]), 0, Qt.AlignmentFlag.AlignVCenter)
        title = K.Elided(r["title"] or "(no title)", 13, 400, TEXT_3 if r.get("done") else TEXT)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        h.addWidget(title, 1)
        if r["pinned"]:
            h.addWidget(K.Chip("Pinned"))
        if r["group"]:
            h.addWidget(K.Chip(r["group"]))
        if r.get("money"):
            h.addWidget(K.Chip("Money", "amber"))
        if r["kind"] == "task":
            h.addWidget(label(r["status"], 12, 400, TEXT_2))
        when = label(r.get("started") or r.get("added") or "", 12, 400, TEXT_3)
        when.setFixedWidth(76)
        h.addWidget(when)
        more = K.IconButton("more", f"What to do with “{r['title']}”", 26)
        more.clicked.connect(lambda: screen.menu(r, more))
        h.addWidget(more)
        self.setAccessibleName(f"{'Task' if r['kind'] == 'task' else 'To-do'}: {r['title']}"
                               + (f", {r['status']}" if r["kind"] == "task" else ", done" if r.get("done") else ""))


class TasksScreen(Screen):
    """Tasks (the old window's Tasks tab): Monday's own tasks and the owner's to-dos in groups of their own (core/side.py). A to-do is
    read by Jev first (who would do it; money makes Monday wait for the owner's word) and can be handed to Monday; each item can be
    ticked, pinned, moved, archived, renamed, deleted. Core keeps it all: SIDE_CHANGED brings the whole new state, TASK_* each task."""
    key, icon, name, section = "tasks", "tasks", "Tasks", "Monday"

    def build(self):
        from .native import ALL_GROUPS
        self.state, self.tasks, self.judged = {}, [], None
        self.page.add(Hero("Tasks", "Monday's own tasks and your to-dos, in groups of your own.", "violet", ("purple", "white")))
        add = Card("Add a to-do", "Jev reads it first: who would do it, and whether money is involved (then Monday waits for your word).")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.box = field("What needs doing", "A new to-do")
        self.box.returnPressed.connect(lambda: self.apply("add"))
        self.box.textChanged.connect(self._typed)
        row.addWidget(self.box, 1)
        for text, kind, fn in (("Check", "secondary", lambda: self._judge()), ("Add to my list", "light", lambda: self.apply("add")),
                               ("Monday, do it", "primary", self._do)):
            b = Button(text, kind)
            b.clicked.connect(fn)
            row.addWidget(b)
        add.lay.addLayout(row)
        self.read = label("", 12.5, 400, TEXT_2, wrap=True)
        add.lay.addWidget(self.read)
        self.page.add(add)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label("Show", 13, 400, TEXT_2))
        self.filter = dropdown([(ALL_GROUPS, "All")], ALL_GROUPS, "Show which group", 200)
        self.filter.activated.connect(lambda _i: self._refill())
        bar.addWidget(self.filter)
        bar.addSpacing(10)
        self.archived = K.Toggle("Show archived: on or off")
        self.archived.toggled.connect(lambda _on: self._refill())
        bar.addWidget(self.archived)
        bar.addWidget(label("Archived", 13, 400, TEXT_2))
        bar.addStretch(1)
        self.group_buttons = {}
        for text, what in (("New group…", "group_new"), ("Rename group…", "group_rename"), ("Delete group", "group_delete")):
            b = Button(text, "ghost")
            b.clicked.connect(lambda _=False, w=what: self._group(w))
            bar.addWidget(b)
            self.group_buttons[what] = b
        self.page.lay.addLayout(bar)
        self.task_card, self.todo_card = Card("Monday's tasks"), Card("Your to-dos")
        self.task_rows, self.todo_rows = QVBoxLayout(), QVBoxLayout()
        for card, rows, empty in ((self.task_card, self.task_rows, "No tasks here. What you ask Monday to do shows up here."),
                                  (self.todo_card, self.todo_rows, "No to-dos here. Add one above.")):
            rows.setSpacing(0)
            card.lay.addLayout(rows)
            card.empty = label(empty, 12.5, 400, TEXT_3)
            card.lay.addWidget(card.empty)
            self.page.add(card)
        self.note = Notice("", "red")
        self.page.add(self.note)
        self.count = label("", 12.5, 400, TEXT_3)
        self.page.add(self.count)
        self.page.lay.addStretch(1)

    def load(self):
        super().load()
        self.bridge.get("/api/side", self._got_side)
        self.bridge.get("/api/tasks?limit=100", self._got_tasks)

    def on_event(self, ev):
        t, d = ev.get("type"), ev.get("data") or {}
        if t == "SIDE_CHANGED" and d.get("todos") is not None:
            self.show_state(d)  # Core sends the whole new state with it
        elif t in ("TASK_CREATED", "TASK_STARTED", "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED") and d.get("task"):
            self.tasks = [d["task"], *(x for x in self.tasks if x.get("id") != d["task"].get("id"))]
            self._refill()

    def _got_side(self, d, err):
        if err:
            return self.note.say(f"Couldn't read your tasks and to-dos: {err}")
        self.show_state(d)

    def _got_tasks(self, d, err):
        if err:
            return self.note.say(f"Couldn't read Monday's tasks: {err}")
        self.tasks = (d or {}).get("tasks") or []
        self._refill()

    def show_state(self, state):
        from .native import ALL_GROUPS, NO_GROUP, group_choices
        self.state = state or {}
        shown = self.filter.currentData()
        self.filter.clear()
        for gid, name in [(ALL_GROUPS, "All"), (NO_GROUP, "No group"), *group_choices(self.state)]:
            self.filter.addItem(name, gid)
        i = self.filter.findData(shown)
        self.filter.setCurrentIndex(i if i >= 0 else 0)  # the group shown is gone: all of them
        self._refill()

    def _refill(self):
        from .native import ALL_GROUPS, NO_GROUP, task_rows, todo_rows
        group, archived = self.filter.currentData() or ALL_GROUPS, self.archived.isChecked()
        one = group not in (ALL_GROUPS, NO_GROUP)
        self.group_buttons["group_rename"].setEnabled(one)
        self.group_buttons["group_delete"].setEnabled(one)
        for card, rows, items in ((self.task_card, self.task_rows, task_rows(self.tasks, self.state, group, archived)),
                                  (self.todo_card, self.todo_rows, todo_rows(self.state, group, archived))):
            while rows.count():
                w = rows.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for r in items:
                rows.addWidget(SideRow(self, r))
            card.empty.setVisible(not items)
            card.n = len(items)
        a = " archived" if archived else ""
        n, m = self.task_card.n, self.todo_card.n
        self.count.setText(f"{n}{a} task{'' if n == 1 else 's'} · {m}{a} to-do{'' if m == 1 else 's'}")

    def menu(self, r, anchor):
        from .native import group_choices
        m = QMenu(self)
        if r["kind"] == "task" and r["status"] not in TASK_DONE:
            m.addAction("Stop it", lambda: self.bridge.post(f"/api/tasks/{r['id']}/stop", None,
                                                            lambda _d, err: self.note.say(f"Couldn't stop it: {err}" if err else "")))
        m.addAction("Unpin" if r["pinned"] else "Pin", lambda: self.apply("pin", r))
        move = m.addMenu("Move to")
        move.addAction("No group", lambda: self.apply("move", r, group=None))
        for gid, name in group_choices(self.state):
            move.addAction(name, lambda g=gid: self.apply("move", r, group=g))
        m.addAction("Bring back" if self.archived.isChecked() else "Archive", lambda: self.apply("archive", r))
        m.addAction("Rename…", lambda: self._rename(r))
        m.addSeparator()
        m.addAction("Delete…", lambda: self.apply("delete", r))
        m.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _rename(self, r):
        title, ok = QInputDialog.getText(self, "Rename", "Its new name:", text=r["title"])
        if ok:
            self.apply("rename", r, title=title)

    def requests(self, what, r=None, done=None, group=None, title=None):
        """What a change sends Core: [(method, path, body)], or a sentence saying what is missing."""
        from .native import ALL_GROUPS, NO_GROUP
        if what == "add":
            text = self.box.text().strip()
            if not text:
                return "Type a to-do first."
            money = bool(self.judged and self.judged[0] == text and self.judged[1].get("money"))
            gid = self.filter.currentData()
            return [("POST", "/api/todos", {"title": text, "money": money, "group_id": gid if gid not in (ALL_GROUPS, NO_GROUP) else None})]
        path = f"/api/side/{r['kind']}/{r['id']}"
        if what == "done":
            return [("POST", f"/api/todos/{r['id']}", {"done": bool(done)})] if r["kind"] == "todo" and bool(done) != r["done"] else []
        if what == "rename":
            return [("POST", path, {"title": title.strip()})] if (title or "").strip() else "A name can't be empty."
        return {"pin": [("POST", path, {"pinned": not r["pinned"]})], "move": [("POST", path, {"group_id": group})],
                "archive": [("POST", path, {"archived": not self.archived.isChecked()})], "delete": [("DELETE", path, None)]}[what]

    def apply(self, what, r=None, **kw):
        reqs = self.requests(what, r, **kw)
        if isinstance(reqs, str):
            return self.note.say(reqs, "amber")
        if not reqs:
            return
        if what == "delete" and not K.confirm(self, "Delete it?", f"Delete “{r['title']}” for good?" if r["kind"] == "todo" else
                                              f"Delete the task “{r['title']}”? It leaves every list; what Monday did stays in Activity.",
                                              "Delete", danger=True):
            return
        method, path, body = reqs[0]

        def done(d, err):
            if err:
                self.note.say(f"Not done: {err}")
                return self._refill()  # a refused tick or rename goes back
            self.note.say("")
            if what == "add":
                self.box.clear()
            if r is not None and r["kind"] == "task":
                self.bridge.get("/api/tasks?limit=100", self._got_tasks)  # a renamed task's title lives in the task list too
            if isinstance(d, dict) and d.get("todos") is not None:
                self.show_state(d)
        self.bridge.call(method, path, body, done)

    def _group(self, what):
        gid = self.filter.currentData()
        name = self.filter.currentText()
        if what == "group_delete":
            if K.confirm(self, "Delete the group?", f"Delete the group “{name}”? What is in it stays, in no group.", "Delete", danger=True):
                self.bridge.call("DELETE", f"/api/groups/{gid}", None, self._group_done)
            return
        new, ok = QInputDialog.getText(self, "New group" if what == "group_new" else "Rename the group", "Its name:",
                                       text="" if what == "group_new" else name)
        if ok and new.strip():
            self.bridge.post("/api/groups" if what == "group_new" else f"/api/groups/{gid}", {"name": new.strip()}, self._group_done)

    def _group_done(self, d, err):
        if err:
            return self.note.say(f"Not done: {err}")
        self.note.say("")
        if isinstance(d, dict) and d.get("todos") is not None:
            self.show_state(d)

    def _typed(self, text):
        if self.judged and self.judged[0] != text.strip():
            self.judged = None  # that read was of other words
            self.read.setText("")

    def _judge(self, then=None):
        from .native import judge_words
        text = self.box.text().strip()
        if not text:
            return self.note.say("Type a to-do first.", "amber")
        self.read.setText("Asking Jev…")

        def done(d, err):
            if err:
                self.read.setText(f"Jev's read failed: {err}")
            else:
                self.judged = (text, d or {})
                self.read.setText(judge_words(d))
            if then:
                then(text, None if err else d or {})
        self.bridge.post("/api/todos/judge", {"text": text}, done)

    def _do(self):
        """Monday, do it: Jev reads it first when it hasn't (the money hold needs the read), then it goes to Monday as if said; money (or
        no read at all) asks once more."""
        text = self.box.text().strip()
        if self.judged and self.judged[0] == text:
            self._send(text, self.judged[1])
        else:
            self._judge(then=self._send)

    def _send(self, text, read):
        if not text:
            return
        if (read or {}).get("money", True) and not K.confirm(
                self, "Start it?", f"“{text}” may involve money. Monday will still ask before any payment. Start it now?", "Start"):
            return self.note.say("Not started.", "amber")
        self.win.command(text, done=lambda _m, err: self.note.say(f"Not sent: {err}" if err else "Sent to Monday: Chat shows it.",
                                                                    "red" if err else "green"))
        self.box.clear()


# -- Browser: Monday's Chrome, live -----------------------------------------------------------------------------------------------
BROWSER_KEYS = {Qt.Key.Key_Return: "Enter", Qt.Key.Key_Enter: "Enter", Qt.Key.Key_Backspace: "Backspace", Qt.Key.Key_Tab: "Tab",
                Qt.Key.Key_Escape: "Escape", Qt.Key.Key_Delete: "Delete", Qt.Key.Key_Left: "ArrowLeft", Qt.Key.Key_Right: "ArrowRight",
                Qt.Key.Key_Up: "ArrowUp", Qt.Key.Key_Down: "ArrowDown", Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End",
                Qt.Key.Key_PageUp: "PageUp", Qt.Key.Key_PageDown: "PageDown"}  # Qt -> Playwright's key names (chrome.owner_input)
NOT_OPEN = "Monday's Chrome isn't open (it starts on the first browser step)."


def browser_line(view):
    """GET /api/browser (or BROWSER_STATE) -> the state line under the address bar."""
    if not view or view.get("running") is False:
        return NOT_OPEN
    who = "you have control" if view.get("control") == "owner" else "Monday has control"
    return f"{view.get('title') or view.get('url') or 'Open'} · {who}" + (" · loading…" if view.get("loading") else "")


def read_frames(core, path, alive, frame):
    """A live picture from Core (MJPEG at `path`), on the caller's thread while alive(): each frame decoded here (a QImage may be made off
    the UI thread) and handed to frame(img). -> (HTTP status or None, why it ended in words); (None, None) when alive() went false."""
    import httpx
    from .native import mjpeg_frames
    try:
        with core.http.stream("GET", f"{path}?t={time.time()}", timeout=httpx.Timeout(10.0, read=30.0)) as r:
            if r.status_code >= 400:
                r.read()
                try:
                    why = (r.json() or {}).get("detail")
                except ValueError:
                    why = None
                return r.status_code, why if isinstance(why, str) else f"Monday Core refused it (HTTP {r.status_code})."
            for jpg in mjpeg_frames(r.iter_bytes()):
                if not alive():
                    return None, None
                img = QImage.fromData(jpg, "JPG")
                if not img.isNull():
                    frame(img)
            return None, "The picture ended."
    except Exception as e:  # the old window was silent here: the picture froze and nothing said why
        return None, f"The live picture stopped: {str(e).splitlines()[0][:120] if str(e) else type(e).__name__}."


class Picture(QWidget):
    """A live picture, letterboxed on black in a rounded frame; its words in its place while there is none."""

    def __init__(self, name: str, words: str):
        super().__init__()
        self.img, self.words = None, words
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(name)

    def ring(self):
        return QColor(EDGE), 1.0

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return max(320, int(w * 10 / 16)) if self.img is not None else 320  # no picture: no tall black box

    def show_image(self, img):
        had = self.img is not None
        self.img = img
        if had != (img is not None):
            self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(960, 600)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.fillPath(clip, QColor("#000000"))
        p.setClipPath(clip)
        if self.img is not None:
            s = min(r.width() / self.img.width(), r.height() / self.img.height())
            w, h = self.img.width() * s, self.img.height() * s
            p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), self.img)
        else:
            p.setPen(QColor(TEXT_3))
            p.setFont(font(13))
            p.drawText(r.adjusted(24, 0, -24, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.words)
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        c, w = self.ring()
        p.setPen(QPen(c, w))
        p.drawRoundedRect(r, 12, 12)
        p.end()


class LiveView(Picture):
    """The live picture of Monday's Chrome (Core's MJPEG frames). The owner's clicks, wheel and keys reach the page only while they have
    control (Take control); the keys then go to the page, the window's own shortcuts too."""

    def __init__(self, screen: "BrowserScreen"):
        super().__init__("Monday's Chrome, live", "Connecting to Monday's Chrome…")
        self.screen = screen
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def ring(self):
        lit = self.hasFocus() and self.screen.owner()  # the keys go to the page
        return QColor(ORANGE if lit else EDGE), 1.5 if lit else 1.0

    def _at(self, pos):
        from .native import picture_point
        return picture_point(self.width(), self.height(), self.img.width(), self.img.height(), pos.x(), pos.y()) \
            if self.img is not None else None

    def _click(self, e, count):
        pt = self._at(e.position())
        b = {Qt.MouseButton.RightButton: "right", Qt.MouseButton.MiddleButton: "middle"}.get(e.button(), "left")
        if pt:
            self.screen.input({"type": "click", "x": pt[0], "y": pt[1], "button": b, "count": count})

    def mousePressEvent(self, e):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()
        self._click(e, 1)

    def mouseDoubleClickEvent(self, e):
        self._click(e, 2)

    def wheelEvent(self, e):
        pt = self._at(e.position())
        if pt and self.screen.owner():
            self.screen.input({"type": "wheel", "x": pt[0], "y": pt[1], "dx": -e.angleDelta().x(), "dy": -e.angleDelta().y()})
            e.accept()
        else:
            e.ignore()  # the page scrolls instead

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride and self.hasFocus() and self.screen.owner():
            e.accept()  # Esc, Ctrl+M…: the page's while the owner drives it
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if not self.screen.owner():
            return super().keyPressEvent(e)
        mods = e.modifiers()
        combo = [n for m, n in ((Qt.KeyboardModifier.ControlModifier, "Control"), (Qt.KeyboardModifier.AltModifier, "Alt")) if mods & m]
        k = BROWSER_KEYS.get(e.key())
        if k is None and combo and 0x20 < e.key() < 0x7f:
            k = chr(e.key()).lower()
        if k:
            self.screen.input({"type": "key", "key": "+".join([*combo, *(["Shift"] if mods & Qt.KeyboardModifier.ShiftModifier
                                                                         and BROWSER_KEYS.get(e.key()) else []), k])})
        elif e.text() and e.text().isprintable():
            self.screen.input({"type": "text", "text": e.text()})
        e.accept()

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)


class BrowserScreen(Screen):
    """Browser (the old window's): Monday's own Chrome, live (GET /api/browser/stream, MJPEG) while this page shows; an address bar,
    back / forward / reload, its tabs; Take control / Resume / Stop (POST /api/browser/control: real, CLAUDE.md). A click, the wheel
    and keys reach the page only while the owner has control."""
    key, icon, name, section = "browser", "globe", "Browser", "Monday"
    live = True
    frame = Signal(object)  # a decoded frame (QImage), from the stream's thread
    lost = Signal(str)      # why the picture stopped

    def build(self):
        self.view, self.streaming, self.gen = {}, 0, 0
        bar = Card(pad=14)
        top = QHBoxLayout()
        top.setSpacing(6)
        for icon_name, words, action in (("left", "Back", "back"), ("right", "Forward", "forward"), ("refresh", "Reload", "reload")):
            b = K.IconButton(icon_name, words, 30)
            b.clicked.connect(lambda _=False, a=action: self.nav(a))
            top.addWidget(b)
        self.url = field("Type an address or what to search for", "Address")
        self.url.returnPressed.connect(lambda: self.nav("go", self.url.text().strip()))
        top.addWidget(self.url, 1)
        go = Button("Go", "secondary")
        go.clicked.connect(lambda: self.nav("go", self.url.text().strip()))
        top.addWidget(go)
        bar.lay.addLayout(top)
        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        self.dot = Dot("off", 8)
        ctl.addWidget(self.dot)
        self.state = K.Elided(NOT_OPEN, 12.5, 400, TEXT_2)
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ctl.addWidget(self.state, 1)
        self.take = Button("Take control", "light", icon_name="hand", name="Take control of Monday's Chrome")
        self.resume = Button("Resume", "secondary", icon_name="play", name="Give the browser back to Monday")
        self.stop = Button("Stop", "danger", icon_name="stop", name="Stop the task using the browser")
        for b, action in ((self.take, "take"), (self.resume, "resume"), (self.stop, "stop")):
            b.clicked.connect(lambda _=False, a=action: self.control(a))
            ctl.addWidget(b)
        bar.lay.addLayout(ctl)
        self.tabs_row = QHBoxLayout()
        self.tabs_row.setSpacing(6)
        bar.lay.addLayout(self.tabs_row)
        self.page.add(bar)
        self.pic = LiveView(self)
        self.page.add(self.pic)
        self.page.add(label("Monday drives this Chrome for its browser steps. Take control to click and type in it yourself; Resume gives "
                            "it back. What you type here goes to the page and is never kept.", 12, 400, TEXT_3, wrap=True))
        self.page.lay.addStretch(1)
        self.frame.connect(self._frame)
        self.lost.connect(self._lost)
        self._show_view({})

    def load(self):
        super().load()
        self.bridge.get("/api/browser", lambda d, err: self._lost(err) if err else self._show_view(d))
        if not self.streaming and self.win.isVisible():
            self.gen += 1
            self.streaming = self.gen
            threading.Thread(target=self._stream, args=(self.gen,), daemon=True, name="browser-frames").start()

    def leave(self):
        self.streaming = 0  # the stream's thread ends at its next frame

    def on_event(self, ev):
        t, d = ev.get("type"), ev.get("data") or {}
        if t == "BROWSER_STATE" and "running" in d:
            self._show_view(d)
        elif t in ("BROWSER_CONTROL", "BROWSER_SESSION"):
            self.bridge.get("/api/browser", lambda v, err: None if err else self._show_view(v))
            if t == "BROWSER_SESSION" and d.get("running") and not self.streaming:
                self.load()

    def owner(self):
        return self.view.get("control") == "owner"

    def _stream(self, gen):
        """The live picture, on its own thread, while this page shows; tried again while it shows (Chrome opens on a browser step)."""
        while self.streaming == gen:
            code, why = read_frames(self.bridge.core, "/api/browser/stream", lambda: self.streaming == gen, self.frame.emit)
            if self.streaming != gen:
                return
            try:
                self.lost.emit("" if code == 409 else f"{why} Trying again…")
            except RuntimeError:  # the window is gone
                return
            time.sleep(3 if code else 2)

    def _frame(self, img):
        if self.streaming:
            self.pic.show_image(img)

    def _lost(self, why):
        if not why:  # 409: not open
            self.pic.words = NOT_OPEN
            self.pic.show_image(None)
        elif self.pic.img is None:
            self.pic.words = why
        self.state.set(why or NOT_OPEN)
        self.pic.update()

    def _show_view(self, d):
        self.view = d or {}
        running = self.view.get("running") is True
        self.state.set(browser_line(self.view))
        self.dot.set("wait" if self.owner() else "info" if running else "off")
        if running and self.view.get("url") and not self.url.hasFocus():
            self.url.setText(self.view["url"])
        self.take.setEnabled(running and not self.owner())
        self.resume.setEnabled(running and self.owner())
        self.stop.setEnabled(running)
        while self.tabs_row.count():
            w = self.tabs_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        tabs = self.view.get("tabs") or []
        if len(tabs) > 1:
            for t in tabs[:8]:
                b = Button((t.get("title") or t.get("url") or f"Tab {t.get('i')}")[:28], "secondary" if t.get("active") else "ghost",
                           name=f"Tab {t.get('i')}: {t.get('title') or t.get('url')}", height=28)
                b.clicked.connect(lambda _=False, i=t.get("i"): self.nav("tab", i=i))
                self.tabs_row.addWidget(b)
            self.tabs_row.addStretch(1)
        if not running:
            self.pic.words = NOT_OPEN
            self.pic.show_image(None)

    def nav(self, action, url=None, i=None):
        if action == "go" and not url:
            return self.state.set("Type an address first.")
        self.bridge.post("/api/browser/nav", {"action": action, "url": url, "i": i},
                         lambda _d, err: self.state.set(f"Not done: {err}") if err else
                         self.bridge.get("/api/browser", lambda v, e: None if e else self._show_view(v)), timeout=45)

    def control(self, action):
        self.bridge.post("/api/browser/control", {"action": action},
                         lambda d, err: self.state.set(f"Not done: {err}") if err else self._show_view(d))

    def input(self, ev):
        if not self.owner():
            return self.state.set("Take control first: Monday is driving this browser.")
        self.bridge.post("/api/browser/input", ev, lambda _d, err: self.state.set(err) if err else None)


# -- Activity: everything Monday did ----------------------------------------------------------------------------------------------
ACTIVITY_KINDS = (("all", "All"), ("TASK_", "Tasks"), ("PERMISSION_", "Approvals"), ("BROWSER_", "Browser"), ("MEMORY_", "Memory"))
ACTIVITY_PAGE = 100


class ActivityScreen(Screen):
    """Activity (the old window's): what Monday did, from Core's persisted events only (GET /api/activity: tasks, steps checked,
    approvals, the browser, Google, memory), newest first, older ones a page at a time; kept current while it shows."""
    key, icon, name, section = "activity", "activity", "Activity", "Monday"

    def build(self):
        self.items, self.kind = [], "all"
        self.page.add(Hero("Activity", "Everything Monday did, step by step, as Core recorded it on this computer.", "teal",
                           ("green", "cyan")))
        bar = QHBoxLayout()
        self.kinds = Segmented(list(ACTIVITY_KINDS), "all", "Which kind of activity")
        self.kinds.changed.connect(self._kind)
        bar.addWidget(self.kinds)
        bar.addStretch(1)
        again = Button("Refresh", "ghost", icon_name="refresh", name="Read the activity again")
        again.clicked.connect(self.load)
        bar.addWidget(again)
        self.page.lay.addLayout(bar)
        self.card = Card()
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        self.card.lay.addLayout(self.rows)
        self.empty = Empty("Nothing yet: what Monday does shows here, each step as it was checked.")
        self.card.lay.addWidget(self.empty)
        self.more = Button("Show older", "secondary")
        self.more.clicked.connect(self._older)
        self.card.lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)
        self.page.add(self.card)
        self.note = Notice("", "red")
        self.page.add(self.note)
        self.page.lay.addStretch(1)

    def load(self):
        super().load()
        self.bridge.get(f"/api/activity?limit={ACTIVITY_PAGE}", lambda d, err: self._got(d, err, fresh=True))

    def on_event(self, ev):
        t = ev.get("type", "")
        if t.startswith(("TASK_", "PERMISSION_", "MEMORY_", "GOOGLE_")) or t in ("BROWSER_CONTROL", "BROWSER_SESSION"):
            self.stale(every=3)

    def _older(self):
        ids = [i.get("id") for i in self.items if isinstance(i.get("id"), int)]
        if ids:
            self.bridge.get(f"/api/activity?limit={ACTIVITY_PAGE}&before={min(ids)}", lambda d, err: self._got(d, err, fresh=False))

    def _got(self, d, err, fresh):
        if err:
            return self.note.say(f"The activity couldn't be read: {err}")
        self.note.say("")
        got = (d or {}).get("items") or []
        self.items = got if fresh else self.items + got
        self.more.setVisible(len(got) >= ACTIVITY_PAGE)
        self._fill()

    def _kind(self, k):
        self.kind = k
        self._fill()

    def _fill(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        shown = [i for i in self.items if self.kind == "all" or str(i.get("type") or "").startswith(self.kind)]
        for it in shown:
            self.rows.addWidget(ActivityRow(it))
        self.empty.setVisible(not shown)
        if not shown and self.items:
            self.empty.say("Nothing of this kind in what is shown. Show older to look further back.", "idle")


# -- Library: what Monday remembers, the projects, what it learned ----------------------------------------------------------------
LIBRARY_SHOWN = 150  # memory lines drawn at once (Core sends up to 500): the filter finds the others


class LibraryScreen(Screen):
    """Library (the old window's): what Monday remembers (the vault's dated lines: correct one, or forget it into Forgotten.md),
    the owner's projects (add one, update its Graphify map), and what Monday learned by itself (only what it saw work)."""
    key, icon, name, section = "library", "book", "Library", "Tools"

    def build(self):
        self.memory, self.projects, self.learned = [], [], []
        self.hero = Hero("Library", "What Monday remembers, your projects, and what it learned by doing.", "lilac", ("purple", "pink"))
        self.page.add(self.hero)
        bar = QHBoxLayout()
        self.tabs = Segmented([("memory", "Remembered"), ("projects", "Projects"), ("learned", "Learned by itself")], "memory",
                              "Which part of the library")
        self.tabs.changed.connect(self._tab)
        bar.addWidget(self.tabs)
        bar.addStretch(1)
        self.filter = field("Filter what Monday remembers", "Filter", width=260)
        self.filter.textChanged.connect(lambda _t: self._fill_memory())
        bar.addWidget(self.filter)
        self.page.lay.addLayout(bar)
        self.mem_card = Card("Remembered", "Kept in your Obsidian vault. A line older than Core's limit may be out of date.")
        self.mem_rows = Form(stretch=0)
        self.mem_card.lay.addWidget(self.mem_rows)
        self.mem_more = label("", 12, 400, TEXT_3)
        self.mem_card.lay.addWidget(self.mem_more)
        self.proj_card = Card("Projects", "Folders Monday knows by name (“work on shop”), each with its Graphify map.")
        add = QHBoxLayout()
        add.setSpacing(8)
        self.proj_name = field("Its name", "The project's name", width=180)
        self.proj_path = field("Its folder", "The project's folder")
        browse = Button("Browse…", "secondary", name="Choose the project's folder")
        browse.clicked.connect(self._browse)
        plus = Button("Add project", "primary")
        plus.clicked.connect(self._add_project)
        for w in (self.proj_name, self.proj_path):
            add.addWidget(w, 0 if w is self.proj_name else 1)
        add.addWidget(browse)
        add.addWidget(plus)
        self.proj_card.lay.addLayout(add)
        self.proj_rows = Form(stretch=1)
        self.proj_card.lay.addWidget(self.proj_rows)
        self.learn_card = Card("Learned by itself", "Only what Monday saw work; it forgets a lesson the first time it fails.")
        self.learn_rows = Form(stretch=1)
        self.learn_card.lay.addWidget(self.learn_rows)
        for c in (self.mem_card, self.proj_card, self.learn_card):
            self.page.add(c)
        self.note = Notice("", "red")
        self.page.add(self.note)
        self.page.lay.addStretch(1)
        self._tab("memory")

    def load(self):
        super().load()
        self.bridge.get("/api/memory", self._got_memory)
        self.bridge.get("/api/projects", self._got_projects)
        self.bridge.get("/api/learned", self._got_learned)

    def on_event(self, ev):
        t = ev.get("type")
        if t in ("MEMORY_SAVED", "MEMORY_FORGOTTEN"):
            self.bridge.get("/api/memory", self._got_memory)
        elif t == "PROJECT_ADDED":
            self.bridge.get("/api/projects", self._got_projects)

    def _tab(self, k):
        for key, c in (("memory", self.mem_card), ("projects", self.proj_card), ("learned", self.learn_card)):
            c.setVisible(key == k)
        self.filter.setVisible(k == "memory")

    def _counts(self):
        self.hero.words_label.setText(f"{len(self.memory)} remembered · {len(self.projects)} project{'' if len(self.projects) == 1 else 's'}"
                                      f" · {len(self.learned)} learned by doing")

    def _got_memory(self, d, err):
        from .native import memory_rows
        if err:
            return self.note.say(f"Couldn't read what Monday remembers: {err}")
        self.memory = memory_rows(d)
        self._fill_memory()
        self._counts()

    def _fill_memory(self):
        q = self.filter.text().strip().lower()
        rows = [r for r in self.memory if not q or q in r[3].lower() or q in r[4].lower() or q in r[2].lower()]
        self.mem_rows.clear()
        for iid, when, kind, shown, note, text in rows[:LIBRARY_SHOWN]:
            left = QWidget()
            lv = QVBoxLayout(left)
            lv.setContentsMargins(0, 0, 0, 0)
            lv.setSpacing(1)
            lv.addWidget(label(shown, 13, 400, TEXT, wrap=True))
            lv.addWidget(label(" · ".join(x for x in (when, kind, note) if x), 12, 400, TEXT_3))
            acts = QWidget()
            ah = QHBoxLayout(acts)
            ah.setContentsMargins(0, 0, 0, 0)
            ah.setSpacing(4)
            fix = Button("Correct…", "ghost", name=f"Correct: {text[:60]}", height=28)
            fix.clicked.connect(lambda _=False, i=iid, t=text: self._correct(i, t))
            drop = Button("Forget", "ghost", name=f"Forget: {text[:60]}", height=28)
            drop.clicked.connect(lambda _=False, i=iid: self._forget(i))
            ah.addWidget(fix)
            ah.addWidget(drop)
            self.mem_rows.add(left, acts)
        self.mem_more.setText(f"Showing {LIBRARY_SHOWN} of {len(rows)}: filter to find the others." if len(rows) > LIBRARY_SHOWN else
                              "" if rows else "Nothing matches." if q else "Nothing remembered yet: say “remember that …”.")

    def _correct(self, iid, text):
        new, ok = QInputDialog.getMultiLineText(self, "Correct what Monday remembers", "What it should remember instead:", text)
        if ok and new.strip() and new.strip() != text:
            self.bridge.post(f"/api/memory/{iid}/correct", {"text": new.strip()},
                             lambda _d, err: self.note.say(f"Not corrected: {err}" if err else "Corrected.", "red" if err else "green"))

    def _forget(self, iid):
        self.bridge.post(f"/api/memory/{iid}/forget", None, lambda _d, err: self.note.say(
            f"Not forgotten: {err}" if err else "Forgotten: it's in Forgotten.md in your vault, where you can put it back.",
            "red" if err else "green"))

    def _got_projects(self, d, err):
        from .native import project_rows
        if err:
            return self.note.say(f"Couldn't read the projects: {err}")
        self.projects = project_rows(d)
        self.proj_rows.clear()
        for pid, name, folder, aliases in self.projects:
            info = QWidget()
            iv = QVBoxLayout(info)
            iv.setContentsMargins(0, 0, 0, 0)
            iv.setSpacing(1)
            path = K.Elided(folder, 12, 400, TEXT_2)
            path.setFont(font(12, mono=True))
            iv.addWidget(path)
            if aliases:
                iv.addWidget(label(f"also called {aliases}", 12, 400, TEXT_3))
            upd = Button("Update its map", "ghost", icon_name="refresh", name=f"Update {name}'s Graphify map", height=28)
            upd.clicked.connect(lambda _=False, p=pid, n=name: self.bridge.post(
                f"/api/projects/{p}/graphify/update", None, lambda _d, e: self.note.say(
                    f"Not started: {e}" if e else f"Updating {n}'s map (changed code only); Activity says when it's done.",
                    "red" if e else "green")))
            self.proj_rows.add(name_dot(name, "info"), info, upd)
        if not self.projects:
            self.proj_rows.add(label("No projects yet: add a folder above.", 12.5, 400, TEXT_3), span_last=True)
        self._counts()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "The project's folder")
        if path:
            self.proj_path.setText(path)
            if not self.proj_name.text().strip():
                self.proj_name.setText(os.path.basename(path.rstrip("/\\")))

    def _add_project(self):
        name, path = self.proj_name.text().strip(), self.proj_path.text().strip()
        if not name or not path:
            return self.note.say("Give the project a name and its folder first.", "amber")

        def done(_d, err):
            if err:
                return self.note.say(f"Not added: {err}")
            self.note.say(f"Project added: say “work on {name}”.", "green")
            self.proj_name.clear()
            self.proj_path.clear()
            self.bridge.get("/api/projects", self._got_projects)
        self.bridge.post("/api/projects", {"name": name, "path": path}, done)

    def _got_learned(self, d, err):
        from .native import learned_rows
        if err:
            return self.note.say(f"Couldn't read what Monday learned: {err}")
        self.learned = learned_rows(d)
        self.learn_rows.clear()
        for what, why in self.learned:
            self.learn_rows.add(label(what, 13, 600, TEXT, wrap=True), label(why, 12.5, 400, TEXT_2, wrap=True))
        if not self.learned:
            self.learn_rows.add(label("Nothing yet · Monday keeps only what it saw work.", 12.5, 400, TEXT_3), span_last=True)
        self._counts()


# -- lists whose rows can be chosen -------------------------------------------------------------------------------------------------
class PickRow(K._Keyed, QAbstractButton):
    """A row that can be chosen (a workflow, a run, a routine): its words laid out inside, lit while it is the one chosen."""

    def __init__(self, name: str):
        super().__init__()
        self.setCheckable(True)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(10, 7, 10, 7)
        self.lay.setSpacing(10)
        self._hover = K.Hover(self)

    def add(self, w, stretch=0):
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # the row takes the click
        self.lay.addWidget(w, stretch)
        return w

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
        if self.isChecked() or self._hover.value:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1f2428") if self.isChecked() else K.alpha("#1f2428", self._hover.value * 0.7))
            p.drawRoundedRect(r, 9, 9)
        self._ring(p, r, 9)
        p.end()


def pick_list(lay, rows, chosen, on_pick):
    """Fill a layout with PickRows: rows = [(key, PickRow)]; the one whose key is `chosen` lit; a click lights it alone and on_pick(key)."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w:
            w.deleteLater()
    every = [row for _k, row in rows]
    for key, row in rows:
        row.setChecked(key == chosen)

        def picked(_=False, key=key, row=row):
            for other in every:
                other.setChecked(other is row)
            on_pick(key)
        row.clicked.connect(picked)
        lay.addWidget(row)


VERDICT_DOTS = {"passed": "ok", "failed": "bad", "blocked": "bad", "cancelled": "off", "running": "info", "never run": "off",
                "skipped": "off", "pass": "ok", "fail": "bad"}


# -- Tests: the workflow recorder (core/recorder.py) ------------------------------------------------------------------------------
class TestsScreen(Screen):
    """Tests (the old window's): record a workflow once (the window in front is watched; never a password), then Monday replays it on
    this computer through the executor (every step through router.permit) and checks each step against what it really sees. The
    workflows with their last result, a workflow's steps with ticks, its runs, and a run's checks with what was seen."""
    key, icon, name, section = "tests", "flask", "Tests", "Tools"

    def build(self):
        self.recs, self.rec, self.shown, self.run_shown = [], None, None, None
        self.page.add(Hero("Tests", "Record a workflow once; Monday replays it here and checks every step against what it sees.", "amber",
                           ("yellow", "orange")))
        rec = Card("Record a workflow", "The window in front is watched while you work; passwords and secret-looking values are never "
                                        "recorded.")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.name_box = field("The workflow's name", "Workflow name", width=260)
        row.addWidget(self.name_box)
        start = Button("Start recording", "primary", dot=True)
        start.clicked.connect(lambda: self.apply("start"))
        stop = Button("Stop and save", "secondary", icon_name="stop")
        stop.clicked.connect(lambda: self.apply("stop"))
        row.addWidget(start)
        row.addWidget(stop)
        row.addStretch(1)
        rec.lay.addLayout(row)
        st = QHBoxLayout()
        self.rec_dot = Dot("off", 8)
        st.addWidget(self.rec_dot)
        self.rec_line = label("Not recording.", 12.5, 400, TEXT_2, wrap=True)
        st.addWidget(self.rec_line, 1)
        rec.lay.addLayout(st)
        self.page.add(rec)
        cols = QHBoxLayout()
        cols.setSpacing(16)
        self.list_card = Card("Your workflows")
        self.list_card.setFixedWidth(340)
        self.list_rows = QVBoxLayout()
        self.list_rows.setSpacing(2)
        self.list_card.lay.addLayout(self.list_rows)
        self.list_empty = label("No workflows yet: record one above.", 12.5, 400, TEXT_3, wrap=True)
        self.list_card.lay.addWidget(self.list_empty)
        self.list_card.lay.addStretch(1)
        cols.addWidget(self.list_card, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(16)
        self.steps_card = Card("Steps", "A ticked step is replayed and checked; an unticked one is left out.")
        self.step_rows = Form(stretch=2)
        self.steps_card.lay.addWidget(self.step_rows)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.acts = []
        for text, kind, what in (("Run it", "primary", "run"), ("Save ticks", "secondary", "ticks"), ("Rename…", "ghost", "rename"),
                                 ("Delete…", "ghost", "delete")):
            b = Button(text, kind, name=f"{text.rstrip('…')} the chosen workflow")
            b.clicked.connect(lambda _=False, w=what: self.apply(w))
            acts.addWidget(b)
            self.acts.append(b)
        acts.addStretch(1)
        self.steps_card.lay.addLayout(acts)
        right.addWidget(self.steps_card)
        self.runs_card = Card("Runs, newest first")
        self.run_rows = QVBoxLayout()
        self.run_rows.setSpacing(2)
        self.runs_card.lay.addLayout(self.run_rows)
        self.runs_empty = label("Not run yet: Run it replays it on this computer.", 12.5, 400, TEXT_3)
        self.runs_card.lay.addWidget(self.runs_empty)
        right.addWidget(self.runs_card)
        self.checks_card = Card("Checks of the chosen run", "What each step expected, and what was really seen.")
        self.check_rows = Form(stretch=3)
        self.checks_card.lay.addWidget(self.check_rows)
        right.addWidget(self.checks_card)
        cols.addLayout(right, 1)
        self.page.lay.addLayout(cols)
        self.note = Notice("", "red")
        self.page.add(self.note)
        self.page.lay.addStretch(1)
        self.show_recording(None)

    def load(self):
        super().load()
        self.bridge.get("/api/recordings", self._got_list)

    def on_event(self, ev):
        t, d = ev.get("type"), ev.get("data") or {}
        if t == "RECORDING_STATE":
            self._rec_state(d)
        elif t == "RECORDINGS_CHANGED":
            self.load()
        elif t == "TEST_RESULT" and d.get("name"):
            from .native import VERDICT
            n = (d.get("passed") or 0) + (d.get("failed") or 0)
            ok = d.get("status") == "PASSED"
            self.note.say(f"“{d['name']}”: {VERDICT.get(d.get('status'), 'N/A')}, {d.get('passed') or 0} of {n} checks passed.",
                          "green" if ok else "red")
            self.load()

    def _rec_state(self, st):
        from .native import recording_line
        self.rec_line.setText(recording_line(st))
        self.rec_dot.set("wait" if (st or {}).get("active") else "off")

    def _got_list(self, d, err):
        from .native import recording_rows
        if err:
            return self.note.say(f"Couldn't read the workflows: {err}")
        self.recs = recording_rows(d)
        self._rec_state((d or {}).get("recording"))
        rows = []
        for rid, name, app, steps, last in self.recs:
            r = PickRow(f"Workflow {name}: {last}")
            words = QWidget()
            wv = QVBoxLayout(words)
            wv.setContentsMargins(0, 0, 0, 0)
            wv.setSpacing(1)
            wv.addWidget(K.Elided(name or "(no name)", 13, 600))
            wv.addWidget(label(" · ".join(x for x in (app, f"{steps} step{'' if steps == '1' else 's'}") if x), 12, 400, TEXT_3))
            r.add(Dot(VERDICT_DOTS.get(last, "off"), 8, last))
            r.add(words, 1)
            r.add(label(last, 12, 600, TEXT_2))
            rows.append((rid, r))
        pick_list(self.list_rows, rows, self.shown, self._pick)
        self.list_empty.setVisible(not rows)
        if self.shown and any(rid == self.shown for rid, _r in rows):
            self._pick(self.shown)
        elif self.shown:
            self.show_recording(None)  # the one shown is gone

    def _pick(self, rid):
        self.shown = rid
        self.bridge.get(f"/api/recordings/{rid}", lambda d, err: self.note.say(f"Couldn't read it: {err}") if err else self.show_recording(d))

    def show_recording(self, rec):
        from .native import run_rows, step_rows
        self.rec, self.shown = rec, (rec or {}).get("id")
        for b in self.acts:
            b.setEnabled(rec is not None)
        self.steps_card.title_label.setText(f"Steps of “{rec.get('name')}”" if rec else "Steps")
        self.step_rows.clear()
        self.ticks = []
        for i, what, checked, on in step_rows(rec):
            t = K.Tick(f"Step {i}: replayed and checked", on)
            self.ticks.append(t)
            self.step_rows.add(t, label(f"{i}. {what}", 13, 400, TEXT, wrap=True), label(checked or "", 12.5, 400, TEXT_2, wrap=True))
        if not rec:
            self.step_rows.add(label("Choose a workflow to see its steps, runs and checks.", 12.5, 400, TEXT_3), span_last=True)
        runs = (rec or {}).get("runs") or []
        rows = []
        for (run_id, when, result, checks, took), run in zip(run_rows(rec), runs):
            r = PickRow(f"Run {when}: {result}, {checks}")
            r.add(Dot(VERDICT_DOTS.get(result, "off"), 8, result))
            r.add(label(when, 12.5, 400, TEXT_2))
            r.add(label(result, 12.5, 600, TEXT), 1)
            r.add(label(checks, 12.5, 400, TEXT_2))
            r.add(label(took, 12, 400, TEXT_3))
            rows.append((run_id, r))
        self.run_shown = runs[0].get("id") if runs else None
        pick_list(self.run_rows, rows, self.run_shown, self._pick_run)
        self.runs_empty.setVisible(rec is not None and not runs)
        self.runs_card.setVisible(rec is not None)
        self._show_checks(runs[0] if runs else None)

    def _pick_run(self, run_id):
        self.run_shown = run_id
        self._show_checks(next((r for r in (self.rec or {}).get("runs") or [] if r.get("id") == run_id), None))

    def _show_checks(self, run):
        from .native import check_rows
        self.check_rows.clear()
        self.checks_card.setVisible(run is not None)
        for step, result, expected, seen in check_rows(run):
            res = QWidget()
            rh = QHBoxLayout(res)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(6)
            rh.addWidget(Dot(VERDICT_DOTS.get(str(result).lower(), "off"), 8))
            rh.addWidget(label(str(result), 12.5, 600, TEXT))
            self.check_rows.add(label(step, 13, 600, TEXT, wrap=True), res, label(expected, 12.5, 400, TEXT_2, wrap=True),
                                label(seen, 12.5, 400, TEXT_2, wrap=True))

    def requests(self, what):
        """What a button sends Core: [(method, path, body)], or the sentence saying what is missing."""
        name = self.name_box.text().strip()
        if what == "start":
            return [("POST", "/api/recordings/start", {"name": name or "Recording"})]
        if what == "stop":
            return [("POST", "/api/recordings/stop", {"name": name or None})]
        rid = self.shown
        if not rid:
            return "Choose a workflow first."
        if what == "run":
            return [("POST", f"/api/recordings/{rid}/run", None)]
        if what == "delete":
            return [("DELETE", f"/api/recordings/{rid}", None)]
        if what == "ticks":
            steps = [dict(s) for s in ((self.rec or {}).get("workflow") or {}).get("steps") or []]
            for s, t in zip(steps, self.ticks):
                s["essential"] = t.isChecked()
            return [("PATCH", f"/api/recordings/{rid}", {"workflow": {"steps": steps}})] if steps else "This workflow has no steps."
        if what == "rename":
            new, ok = QInputDialog.getText(self, "Rename the workflow", "Its new name:", text=(self.rec or {}).get("name") or "")
            return [("PATCH", f"/api/recordings/{rid}", {"name": new.strip()})] if ok and new.strip() else []
        return []

    def apply(self, what):
        reqs = self.requests(what)
        if isinstance(reqs, str):
            return self.note.say(reqs, "amber")
        if not reqs:
            return
        if what == "delete" and not K.confirm(self, "Delete the workflow?", f"Delete “{(self.rec or {}).get('name')}” and all its runs?",
                                              "Delete", danger=True):
            return
        self.note.say("Working…", "amber")
        done_words = {"run": "Replaying it on this computer; its result shows here when it's done.", "rename": "Renamed.",
                      "delete": "Deleted.", "ticks": "Saved: an unticked step is left out of the replay and its checks."}
        method, path, body = reqs[0]

        def done(d, err):
            if err:
                return self.note.say(f"Not done: {err}")
            self.note.say((d or {}).get("text") if isinstance(d, dict) and (d or {}).get("text") else done_words.get(what, "Done."), "green")
            if what == "delete":
                self.show_recording(None)
            self.load()
        self.bridge.call(method, path, body, done)


# -- Routines: a bot's prompt on a schedule (core/routines.py, OpenMausBot's) ------------------------------------------------------
RUN_TONES = {"completed": K.GREEN, "failed": K.RED, "missed": TEXT_3, "cancelled": TEXT_3, "running": K.BLUE, "waiting": K.AMBER,
             "queued": TEXT_3}
RUN_DOTS = {"completed": "ok", "failed": "bad", "running": "info", "waiting": "wait"}
ROUTINE_MINUTES = tuple(range(15, 241, 15))


class WeekView(QWidget):
    """This week as OpenMausBot's calendar draws it (native.week_blocks): a column a day with today's name lit, the receipts in the colour
    of how they went, the times still to come outlined, and now as a red line."""
    HEAD = 24

    def __init__(self):
        super().__init__()
        self.view = None
        self.setMinimumHeight(280)
        self.setAccessibleName("This week's routines")

    def set(self, view):
        self.view = view
        runs, ahead = len((view or {}).get("runs") or []), len((view or {}).get("ahead") or [])
        self.setAccessibleDescription(f"{runs} run{'' if runs == 1 else 's'} this week, {ahead} still to come")
        self.update()

    def paintEvent(self, _e):
        from .native import week_blocks
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, head = self.width(), self.height(), self.HEAD
        if not self.view:
            p.setPen(QColor(TEXT_3))
            p.setFont(font(12.5))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, "Reading this week…")
            return p.end()
        today = time.strftime("%Y%m%d")
        p.setFont(font(11.5, 600))
        for i in range(7):
            x = i * w / 7.0
            day = time.localtime(self.view["start"] + i * 86400 + 3600)
            p.setPen(QColor(ORANGE if time.strftime("%Y%m%d", day) == today else TEXT_3))
            p.drawText(QRectF(x + 6, 0, w / 7.0 - 8, head), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       time.strftime("%a %d", day))
            if i:
                p.setPen(QPen(QColor(DIVIDER), 1))
                p.drawLine(QPointF(x, head), QPointF(x, h))
        p.setPen(QPen(QColor(DIVIDER), 1))
        p.drawLine(QPointF(0, head), QPointF(w, head))
        p.setFont(font(10.5, 600))
        for x, y, bw, bh, status, name in week_blocks(self.view, w, h, head):
            r = QRectF(x, y, bw, bh)
            if status == "ahead":
                p.setPen(QPen(QColor(ORANGE), 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(K.alpha(RUN_TONES.get(status, ORANGE), 0.85))
            p.drawRoundedRect(r, 4, 4)
            if bh >= 13:
                p.setPen(QColor(TEXT if status == "ahead" else "#0b0b0c"))
                p.drawText(r.adjusted(4, 1, -3, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                           p.fontMetrics().elidedText(str(name or ""), Qt.TextElideMode.ElideRight, max(1, int(bw - 7))))
        now = time.time()
        if self.view["start"] <= now < self.view["start"] + 7 * 86400:
            day = int((now - self.view["start"]) // 86400)
            y = head + (now - self.view["start"] - day * 86400) * (h - head) / 86400.0
            p.setPen(QPen(QColor(K.RED), 1.5))
            p.drawLine(QPointF(day * w / 7.0, y), QPointF((day + 1) * w / 7.0, y))
        p.end()


class RoutinesScreen(Screen):
    """Routines (OpenMausBot's, the old window's tab): a bot's prompt on a schedule, every day at a time on chosen weekdays or once; this
    week's calendar and the runs' receipts. Every change goes through Core with Monday's window token."""
    key, icon, name, section = "routines", "clock", "Routines", "Tools"

    def build(self):
        from .native import ROUTINE_DAYS
        self.routines, self.chosen, self.view = [], None, None
        self.page.add(Hero("Routines", "A bot does something on a schedule. Each run leaves a receipt.", "gold", ("yellow", "white")))
        cols = QHBoxLayout()
        cols.setSpacing(16)
        self.list_card = Card("Your routines")
        self.list_card.setFixedWidth(360)
        new = Button("New", "ghost", icon_name="plus", name="A new routine")
        new.clicked.connect(self.new)
        self.list_card.head.addWidget(new)
        self.list_rows = QVBoxLayout()
        self.list_rows.setSpacing(2)
        self.list_card.lay.addLayout(self.list_rows)
        self.list_empty = label("No routines yet: write one on the right.", 12.5, 400, TEXT_3, wrap=True)
        self.list_card.lay.addWidget(self.list_empty)
        self.list_card.lay.addStretch(1)
        cols.addWidget(self.list_card, 0, Qt.AlignmentFlag.AlignTop)
        ed = Card("Routine")
        self.ed = ed
        self.r_name = field("Morning brief", "The routine's name", width=320)
        ed.field("Name", self.r_name, width=150)
        self.r_bot = dropdown([("", "Reading the bots…")], "", "The bot that does it", 240)
        bot_row = QWidget()  # the bot, and the way to make one when there is none (the owner, 2026-10-20 night: "in the routine I can't make bots")
        bh = QHBoxLayout(bot_row)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(8)
        bh.addWidget(self.r_bot)
        self.r_make = Button("Make a bot", "ghost", height=24, name="Make a bot on the Bots page")
        self.r_make.clicked.connect(lambda: self.win.show_page("bots"))
        bh.addWidget(self.r_make)
        bh.addStretch(1)
        ed.field("Bot", bot_row, width=150)
        self.r_prompt = QPlainTextEdit()
        self.r_prompt.setPlaceholderText("Summarize my inbox and tell me what needs me")
        self.r_prompt.setAccessibleName("What the bot does each time")
        self.r_prompt.setFixedHeight(84)
        ed.field("What to do", self.r_prompt, width=150)
        self.r_kind = Segmented([("daily", "Every day"), ("once", "Once")], "daily", "When it runs")
        self.r_kind.changed.connect(lambda _v: self._kind())
        ed.field("When", self.r_kind, width=150)
        self.r_time = field("08:30", "The time it runs each day", width=90)
        ed.field("Time", self.r_time, width=150)
        days = QWidget()
        dh = QHBoxLayout(days)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.setSpacing(4)
        self.r_days = {}
        for d, name in ROUTINE_DAYS:
            t = K.Tick(f"Runs on {name}", True)
            self.r_days[d] = t
            dh.addWidget(t)
            dh.addWidget(label(name, 12.5, 400, TEXT_2))
            dh.addSpacing(4)
        dh.addStretch(1)
        ed.field("On these days", days, width=150)
        self.r_at = field("2026-10-21 09:00", "The date and time it runs once", width=170)
        ed.field("Date and time", self.r_at, width=150)
        self.r_minutes = dropdown([(str(m), f"{m} minutes") for m in ROUTINE_MINUTES], "30", "How long it usually takes (for the calendar)",
                                  150)
        ed.field("Usually takes", self.r_minutes, "for the calendar", width=150)
        self.r_on = K.Toggle("The routine is on", True)
        ed.field("On", self.r_on, width=150)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        for text, kind, what in (("Save routine", "primary", "save"), ("Run now", "secondary", "run"), ("Delete…", "ghost", "delete")):
            b = Button(text, kind)
            b.clicked.connect(lambda _=False, w=what: self.apply(w))
            acts.addWidget(b)
        acts.addStretch(1)
        ed.lay.addLayout(acts)
        self.note = Notice("", "red")
        ed.lay.addWidget(self.note)
        cols.addWidget(ed, 1)
        self.page.lay.addLayout(cols)
        week = Card("This week", "Each run as it went, the times still to come outlined; now is the red line.")
        self.week = WeekView()
        week.lay.addWidget(self.week)
        self.receipts = QVBoxLayout()
        self.receipts.setSpacing(2)
        week.lay.addLayout(self.receipts)
        self.page.add(week)
        self.page.lay.addStretch(1)
        self.new()

    def load(self):
        super().load()
        self.bridge.get("/api/routines", lambda d, err: self.note.say(f"Couldn't read the routines: {err}") if err else self.show(d))

    def on_event(self, ev):
        if ev.get("type") in ("ROUTINES_CHANGED", "ROUTINE_RUN"):
            self.stale(every=2)

    def show(self, view):
        from .native import routine_line, run_line
        self.view, self.routines = view or {}, (view or {}).get("routines") or []
        had = self.r_bot.currentData()
        self.r_bot.clear()
        for b in self.view.get("bots") or []:
            self.r_bot.addItem(b, b)
        if not self.r_bot.count():
            self.r_bot.addItem("No bots yet", "")
        self.r_make.setText("Make a bot" if not self.view.get("bots") else "Make another bot")  # (bots are made on the Bots page: the button goes there)
        i = self.r_bot.findData(had)
        self.r_bot.setCurrentIndex(i if i >= 0 else 0)
        rows = []
        for r in self.routines:
            row = PickRow(routine_line(r))
            words = QWidget()
            wv = QVBoxLayout(words)
            wv.setContentsMargins(0, 0, 0, 0)
            wv.setSpacing(1)
            wv.addWidget(K.Elided(r.get("name") or "", 13, 600))
            rest = routine_line({**r, "name": None})
            wv.addWidget(K.Elided(rest, 12, 400, K.RED_TEXT if r.get("bot_missing") else TEXT_3))
            row.add(Dot("ok" if r.get("enabled") else "off", 8, "on" if r.get("enabled") else "off"))
            row.add(words, 1)
            rows.append((r["id"], row))
        pick_list(self.list_rows, rows, self.chosen, self.choose)
        self.list_empty.setVisible(not rows)
        self.week.set(self.view)
        while self.receipts.count():
            w = self.receipts.takeAt(0).widget()
            if w:
                w.deleteLater()
        for run in reversed(self.view.get("runs") or []):
            line = QWidget()
            lh = QHBoxLayout(line)
            lh.setContentsMargins(2, 3, 2, 3)
            lh.setSpacing(8)
            lh.addWidget(Dot(RUN_DOTS.get(run.get("status"), "off"), 8, str(run.get("status"))))
            txt = K.Elided(run_line(run), 12.5, 400, TEXT_2)
            txt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lh.addWidget(txt, 1)
            self.receipts.addWidget(line)
        if not self.view.get("runs"):
            self.receipts.addWidget(label("No runs this week yet.", 12.5, 400, TEXT_3))

    def choose(self, rid):
        r = next((x for x in self.routines if x["id"] == rid), None)
        if not r:
            return
        self.chosen = rid
        self.ed.title_label.setText(f"Routine: {r['name']}")
        self.r_name.setText(r["name"])
        self.r_prompt.setPlainText(r["prompt"])
        if self.r_bot.findData(r["bot"]) < 0:
            self.r_bot.addItem(f"{r['bot']} (no such bot now)", r["bot"])
        self.r_bot.setCurrentIndex(self.r_bot.findData(r["bot"]))
        sch = r["schedule"]
        self.r_kind.set_value("once" if sch["type"] == "once" else "daily")
        self.r_at.setText(time.strftime("%Y-%m-%d %H:%M", time.localtime(sch["at"])) if sch["type"] == "once" else "")
        self.r_time.setText(sch.get("time") or "")
        for d, t in self.r_days.items():
            t.set_quiet(sch["type"] == "once" or d in (sch.get("weekdays") or []))
        m = max(15, min(240, int(r.get("minutes") or 30)))
        self.r_minutes.setCurrentIndex(self.r_minutes.findData(str(m - m % 15)))
        self.r_on.set_quiet(bool(r.get("enabled")))
        self._kind()
        self.note.say("")

    def new(self):
        self.chosen = None
        for i in range(self.list_rows.count()):
            w = self.list_rows.itemAt(i).widget()
            if w:
                w.setChecked(False)
        self.ed.title_label.setText("A new routine")
        self.r_name.clear()
        self.r_prompt.clear()
        self.r_at.clear()
        self.r_time.setText("08:30")
        self.r_kind.set_value("daily")
        self.r_minutes.setCurrentIndex(self.r_minutes.findData("30"))
        self.r_on.set_quiet(True)
        for t in self.r_days.values():
            t.set_quiet(True)
        self._kind()

    def _kind(self):
        """Every day takes a time and its days; once takes a date and time."""
        once = self.r_kind.value == "once"
        self.r_at.setEnabled(once)
        self.r_time.setEnabled(not once)
        for t in self.r_days.values():
            t.setEnabled(not once)

    def requests(self, what):
        from .native import routine_body
        rid = self.chosen
        if what == "save":
            body = routine_body(self.r_name.text(), self.r_bot.currentData(), self.r_prompt.toPlainText(), self.r_kind.value == "once",
                                self.r_at.text(), self.r_time.text(), [d for d, t in self.r_days.items() if t.isChecked()],
                                int(self.r_minutes.currentData() or 30), self.r_on.isChecked())
            return [("POST", f"/api/routines/{rid}" if rid else "/api/routines", body)]
        if not rid:
            return "Choose a routine first."
        return {"run": [("POST", f"/api/routines/{rid}/run", None)], "delete": [("DELETE", f"/api/routines/{rid}", None)]}[what]

    def apply(self, what):
        try:
            reqs = self.requests(what)
        except ValueError as e:  # a date or a time it can't read
            return self.note.say(str(e), "amber")
        if isinstance(reqs, str):
            return self.note.say(reqs, "amber")
        if what == "delete" and not K.confirm(self, "Delete the routine?", "Delete it? Its receipts stay.", "Delete", danger=True):
            return
        method, path, body = reqs[0]
        self.note.say("Saving…", "amber")

        def done(d, err):
            if err:
                return self.note.say(err)
            if what == "save" and isinstance(d, dict) and d.get("id"):
                self.chosen = d["id"]
            self.note.say({"save": "Saved.", "run": "Queued: it starts when its bot is free.", "delete": "Deleted; its receipts stay."}[what],
                          "green")
            if what == "delete":
                self.new()
            self.load()
        self.bridge.call(method, path, body, done)


# -- Camera: Monday's eyes (core/vision.py) ---------------------------------------------------------------------------------------
class CameraScreen(Screen):
    """Camera (the old window's): the camera, live with what Monday sees boxed, only while turned on here, on this page, with the window
    showing (leaving the page or hiding the window turns it off; a stream that ends or is refused turns it off with Core's reason, and it
    never reconnects by itself); "what is this?"; what Monday knows by sight, with Forget."""
    key, icon, name, section = "camera", "camera", "Camera", "Tools"
    frame = Signal(object)
    ended = Signal(str)

    def build(self):
        self.on, self.vision, self.known, self.gen, self.seen = False, {}, [], 0, 0
        top = Card(pad=14)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.switch = Button("Turn on camera", "primary", icon_name="camera", name="Turn the camera on here")
        self.switch.clicked.connect(lambda: self.camera(not self.on))
        row.addWidget(self.switch)
        ask = Button("What is this?", "secondary", name="Ask Monday what the camera sees")
        ask.clicked.connect(self._ask)
        row.addWidget(ask)
        row.addSpacing(6)
        self.dot = Dot("off", 8)
        row.addWidget(self.dot)
        self.state = K.Elided("Reading the camera's state…", 12.5, 400, TEXT_2)
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.state, 1)
        top.lay.addLayout(row)
        self.page.add(top)
        self.pic = Picture("The camera, live, with what Monday sees boxed",
                           "The camera is off. Turn it on to see what Monday sees; it goes off when you leave this page.")
        self.page.add(self.pic)
        self.known_card = Card("What Monday knows by sight", "Say “remember this is my mug” with it in view to teach it.")
        self.known_rows = Form(stretch=0)
        self.known_card.lay.addWidget(self.known_rows)
        self.page.add(self.known_card)
        self.page.lay.addStretch(1)
        self.frame.connect(self._frame)
        self.ended.connect(self._ended)

    def load(self):
        super().load()
        self.bridge.get("/api/vision", lambda d, err: self.state.set(f"Couldn't read the camera's state: {err}") if err and not self.on
                        else self.show(d) if not err else None)

    def leave(self):
        if getattr(self, "on", False):
            self.camera(False)  # the camera never stays on behind another page or a hidden window

    def show(self, st):
        from .native import camera_line, known_rows
        self.vision, self.known = st or {}, known_rows(st)
        self.switch.setEnabled(bool(self.vision.get("available")) or self.on)
        if not self.on:  # while it's on, the live line stays
            self.state.set(camera_line(self.vision))
            self.dot.set("off" if self.vision.get("available") else "bad")
        self.known_rows.clear()
        for name, views, taught, source in self.known:
            forget = Button("Forget…", "ghost", name=f"Forget {name}", height=28)
            forget.clicked.connect(lambda _=False, n=name: self._forget(n))
            self.known_rows.add(label(name, 13, 600, TEXT), label(f"{views} view{'' if views == '1' else 's'} · taught {taught}"
                                                                  + (f" · from {source}" if source else ""), 12.5, 400, TEXT_2), forget)
        if not self.known:
            self.known_rows.add(label("Nothing yet.", 12.5, 400, TEXT_3), span_last=True)

    def camera(self, on):
        from .native import CAMERA_RECHECK_S
        self.on = bool(on)
        self.switch.setText("Turn off camera" if self.on else "Turn on camera")
        self.switch.setAccessibleName("Turn the camera off" if self.on else "Turn the camera on here")
        self.gen += 1
        if self.on:
            self.seen = 0
            self.state.set("Starting the camera…")
            self.dot.set("wait")
            threading.Thread(target=self._frames, args=(self.gen,), daemon=True, name="camera-frames").start()
            return
        self.pic.show_image(None)
        self.state.set("Camera view off.")
        self.dot.set("off")
        QTimer.singleShot(int(CAMERA_RECHECK_S * 1000), lambda: self.load() if not self.on else None)  # Core's word, once it has closed it

    def _frames(self, gen):
        """GET /api/vision/stream until turned off. Ending by itself (the camera didn't open, Core refused) turns the view off with
        Core's reason; never opened again by itself, so a failing camera isn't tried again and again."""
        _code, why = read_frames(self.bridge.core, "/api/vision/stream", lambda: self.on and self.gen == gen, self.frame.emit)
        if self.on and self.gen == gen:
            try:
                self.ended.emit(why or "The camera stopped.")
            except RuntimeError:  # the window is gone
                pass

    def _frame(self, img):
        if not self.on:
            return
        self.seen += 1
        self.pic.show_image(img)
        if self.seen == 1:
            self.state.set("Camera on: live (it goes off when you leave this page)")
            self.dot.set("ok")

    def _ended(self, why):
        if self.on:
            self.camera(False)
            self.state.set(why)
            self.dot.set("bad")
            self.load()

    def _ask(self):
        self.win.command("what is this", done=lambda _m, err: self.state.set(f"Not asked: {err}" if err else
                                                                               "Asked “what is this”: the answer comes in Chat."))

    def _forget(self, name):
        if K.confirm(self, f"Forget {name}?", f"Monday won't know {name} by sight any more.", "Forget", danger=True):
            self.bridge.post("/api/vision/forget", {"label": name},
                             lambda d, err: self.state.set(f"Not forgotten: {err}") if err else self.show(d))


# -- Bots and rooms (core/bots.py; OpenMausBot's roster, rooms and team files) ------------------------------------------------------
def bot_face(look, size, mood="idle", name="A bot's face"):
    """A bot's face: one of Monday's pets in the bot's colours (OpenMausBot's Maus by default), moving while it works or waits."""
    look = look or {}
    art = PetArt({"kind": look.get("kind") or "maus", "body": look.get("body") or "#377FE6", "accent": look.get("accent") or "#FFFFFF",
                  "intensity": 0.6})
    return Pet(size, art, mood, live=mood in ("working", "waiting"), name=name)


class BotRow(PickRow):
    """A bot or a room in the roster: a click chooses it; a double-click talks to the bot; a right-click its menu."""

    def __init__(self, name, talk=None, menu=None):
        super().__init__(name)
        self.talk, self.menu = talk, menu

    def mouseDoubleClickEvent(self, e):
        if self.talk:
            self.talk()
        else:
            super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        if self.menu:
            self.menu(e.globalPos())


class BotsScreen(Screen):
    """Bots (OpenMausBot's roster; the old window's tab): Monday with a role (“Scout, find …”). The roster (rooms first, then the bots:
    pinned first, hidden folded, each face in its state, unread, search), a bot's editor (role, instructions, who does its work, its model,
    computer access, its face), asking it, the starting three, rooms (several bots in one conversation: who answers, instructions, say),
    and OpenMaus team files. Every change goes through Core with Monday's window token; Ctrl+N a new bot, Ctrl+1–9 the Nth."""
    key, icon, name, section = "bots", "bot", "Bots", "Tools"

    def build(self):
        self.data, self.models, self.pick, self.room, self.show_hidden, self.room_seen = {}, [], None, None, False, set()
        self.page.add(Hero("Bots", "Monday with a role. Say “Scout, find …”, “Pixel, …” or “Forge, …”, or put several in a room.", "indigo",
                           ("blue", "pink", "yellow")))
        cols = QHBoxLayout()
        cols.setSpacing(16)
        ros = Card("Your bots")
        ros.setFixedWidth(320)
        self.find = field("Search bots", "Search bots: name, role or instructions")
        self.find.textChanged.connect(lambda _t: self.show())
        ros.lay.addWidget(self.find)
        self.roster = QVBoxLayout()
        self.roster.setSpacing(2)
        ros.lay.addLayout(self.roster)
        ros.lay.addStretch(1)
        cols.addWidget(ros, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(0)
        self.editor = self._bot_editor()
        self.room_card = self._room_panel()
        right.addWidget(self.editor)
        right.addWidget(self.room_card)
        right.addStretch(1)
        cols.addLayout(right, 1)
        self.page.lay.addLayout(cols)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.add3 = Button("Add Scout, Pixel and Forge", "light", name="Add the starting bots you don't have yet")
        self.add3.clicked.connect(lambda: self.apply("templates"))
        bar.addWidget(self.add3)
        for text, fn, words in (("New room…", self._room_new, "Make a room: several bots in one conversation"),
                                ("Export team…", self._export, "Save the roster's bots as an OpenMaus team file"),
                                ("Import team…", self._import, "Make bots from an OpenMaus team file")):
            b = Button(text, "secondary", name=words)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        self.page.lay.addLayout(bar)
        self.note = Notice("", "red")
        self.page.add(self.note)
        self.count = label("", 12.5, 400, TEXT_3)
        self.page.add(self.count)
        self.page.lay.addStretch(1)
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_bot, context=ctx)
        for i in range(9):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self._nth(i), context=ctx)
        self._dirty = QTimer(self)  # a bot's state or unread may have changed: Core read again, at most every 1.5 s
        self._dirty.setSingleShot(True)
        self._dirty.setInterval(1500)
        self._dirty.timeout.connect(self.load)
        self._room_mode(False)
        self.new_bot()

    def _bot_editor(self):
        from .native import BOT_COLOURS, bot_pets
        from .notch import PETS
        ed = Card("A new bot")
        self.b_state = label("", 12.5, 400, TEXT_2, wrap=True)
        ed.lay.addWidget(self.b_state)
        self.b_name = field("Its name", "The bot's name", width=260)
        ed.field("Name", self.b_name, width=150)
        self.b_role = field("Researcher, designer, engineer…", "The bot's role", width=320)
        ed.field("Role", self.b_role, width=150)
        self.b_text = QPlainTextEdit()
        self.b_text.setPlaceholderText("What it always keeps in mind")
        self.b_text.setAccessibleName("The bot's standing instructions")
        self.b_text.setFixedHeight(96)
        ed.field("Instructions", self.b_text, width=150)
        self.b_agent = dropdown([("auto", "Monday decides")], "auto", "Who does the bot's work", 260)
        ed.field("Who does its work", self.b_agent, width=150)
        self.b_model = dropdown([("", "Monday's own choice")], "", "The model the bot answers with", 260)
        ed.field("Its model", self.b_model, width=150)
        self.b_computer = K.Toggle("Can use this computer (Monday's agent and its own hands)", True)
        ed.field("Computer", self.b_computer, "can use this computer", width=150)
        face = QWidget()
        fh = QHBoxLayout(face)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(8)
        self.b_kind = dropdown([(k, PETS[k][0]) for k in bot_pets()], None, "The bot's face", 190)
        self.b_kind.setPlaceholderText("Face")  # nothing picked: the face is left as it is (Core gives a new bot one)
        self.b_kind.setCurrentIndex(-1)
        self.b_colour = dropdown([(c[0], c[2]) for c in BOT_COLOURS], None, "The face's colours", 130)
        self.b_colour.setPlaceholderText("Colours")
        self.b_colour.setCurrentIndex(-1)
        self.b_preview = bot_face({}, 40, name="How the bot's face looks")
        for box in (self.b_kind, self.b_colour):
            box.activated.connect(lambda _i: self._preview())
            fh.addWidget(box)
        fh.addWidget(self.b_preview)
        fh.addStretch(1)
        ed.field("Face", face, width=150)
        self.b_project = field("optional", "The bot's project", width=260)
        ed.field("Project", self.b_project, width=150)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.b_buttons = {}
        for text, kind, what in (("Save bot", "primary", "save"), ("New", "ghost", "new"), ("Duplicate", "ghost", "duplicate"),
                                 ("Pin", "ghost", "pin"), ("Hide", "ghost", "hide"), ("Delete…", "ghost", "delete")):
            b = Button(text, kind, name=f"{text.rstrip('…')} the bot" if what not in ("save", "new") else text)
            b.clicked.connect(self.new_bot if what == "new" else lambda _=False, w=what: self.apply(w))
            acts.addWidget(b)
            self.b_buttons[what] = b
        acts.addStretch(1)
        ed.lay.addLayout(acts)
        ask = QHBoxLayout()
        ask.setSpacing(8)
        self.b_ask = field("What to ask it", "What to ask the chosen bot")
        self.b_ask.returnPressed.connect(self._ask)
        ask.addWidget(self.b_ask, 1)
        go = Button("Ask", "secondary", name="Ask the chosen bot")
        go.clicked.connect(self._ask)
        ask.addWidget(go)
        ed.lay.addLayout(ask)
        return ed

    def _room_panel(self):
        c = Card()
        head = QHBoxLayout()
        head.setSpacing(8)
        back = Button("All bots", "ghost", icon_name="left", name="Back to the bots")
        back.clicked.connect(lambda: self._room_mode(False))
        head.addWidget(back)
        self.r_title = label("", 16, 700)
        head.addWidget(self.r_title, 1)
        self.r_who = dropdown([("lead", "Lead answers")], "lead", "Who answers in the room", 240)
        self.r_who.activated.connect(lambda _i: self.room_apply("responder"))
        head.addWidget(self.r_who)
        edit = Button("Edit room", "secondary")
        edit.clicked.connect(lambda: self._room_edit())
        head.addWidget(edit)
        c.lay.addLayout(head)
        self.r_bulletin = label("", 12.5, 400, TEXT_2, wrap=True)
        c.lay.addWidget(self.r_bulletin)
        self.r_editor = QWidget()
        ev = QVBoxLayout(self.r_editor)
        ev.setContentsMargins(0, 4, 0, 4)
        ev.setSpacing(8)
        self.r_name = field("The room's name", "The room's name", width=320)
        ev.addWidget(self.r_name)
        self.r_members_box = QWidget()
        self.r_members = QHBoxLayout(self.r_members_box)
        self.r_members.setContentsMargins(0, 0, 0, 0)
        self.r_members.setSpacing(6)
        ev.addWidget(label("Bots in it", 12.5, 600, TEXT_2))
        ev.addWidget(self.r_members_box)
        self.r_lead = dropdown([], None, "The room's lead (answers a message that names nobody)", 240)
        lr = QHBoxLayout()
        lr.addWidget(label("Its lead", 12.5, 600, TEXT_2))
        lr.addWidget(self.r_lead)
        lr.addStretch(1)
        ev.addLayout(lr)
        self.r_text = QPlainTextEdit()
        self.r_text.setPlaceholderText("Room instructions: every bot in it follows them")
        self.r_text.setAccessibleName("The room's instructions")
        self.r_text.setFixedHeight(70)
        ev.addWidget(self.r_text)
        rb = QHBoxLayout()
        rb.setSpacing(8)
        for text, kind, what in (("Save room", "primary", "save"), ("Delete room…", "ghost", "delete"), ("Close", "ghost", "close")):
            b = Button(text, kind)
            b.clicked.connect((lambda: self.r_editor.hide()) if what == "close" else (lambda _=False, w=what: self.room_apply(w)))
            rb.addWidget(b)
        rb.addStretch(1)
        ev.addLayout(rb)
        c.lay.addWidget(self.r_editor)
        self.r_log_box = QWidget()
        self.r_log = QVBoxLayout(self.r_log_box)
        self.r_log.setContentsMargins(0, 0, 8, 0)
        self.r_log.setSpacing(10)
        self.r_log.addStretch(1)
        self.r_scroll = QScrollArea()
        self.r_scroll.setWidget(self.r_log_box)
        self.r_scroll.setWidgetResizable(True)
        self.r_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.r_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.r_scroll.setFixedHeight(380)
        self.r_scroll.setAccessibleName("The room's conversation")
        c.lay.addWidget(self.r_scroll)
        say = QHBoxLayout()
        say.setSpacing(8)
        self.r_box = field("Message the room", "What to say in the room")
        self.r_box.returnPressed.connect(self._say)
        say.addWidget(self.r_box, 1)
        send = Button("Send", "primary", name="Say it in the room")
        send.clicked.connect(self._say)
        say.addWidget(send)
        c.lay.addLayout(say)
        return c

    # Core
    def load(self):
        super().load()
        self.bridge.get("/api/models", self._got_models)
        self.bridge.get("/api/bots", lambda d, err: self.note.say(f"Couldn't read the bots: {err}") if err else
                        self.bridge.get("/api/rooms", lambda r, _e: self.show({**(d or {}), "rooms": (r or {}).get("rooms") or []})))

    def on_event(self, ev):
        t, d = ev.get("type"), ev.get("data") or {}
        m = d.get("message") or {}
        if t in ("BOTS_CHANGED", "ROOMS_CHANGED"):
            self.load()
        elif t == "MESSAGE_CREATED" and (m.get("meta") or {}).get("chatroom"):
            self._room_line(m)
            self._dirty.start()
        elif t.startswith(("TASK_", "PERMISSION_")) or (t == "MESSAGE_CREATED" and (m.get("meta") or {}).get("bot")):
            if not self._dirty.isActive():
                self._dirty.start()

    def _got_models(self, d, err):
        from .native import bot_model_choices
        if err:
            return
        keep = self.b_model.currentData()
        self.models = bot_model_choices(d)
        self.b_model.clear()
        for mid, words_ in self.models:
            self.b_model.addItem(words_, mid)
        i = self.b_model.findData(keep if keep is not None else "")
        self.b_model.setCurrentIndex(i if i >= 0 else 0)

    def show(self, data=None):
        from .native import agent_choices, bot_line, bot_mood, missing_templates, roster_match, roster_rows
        if data is not None:
            self.data = data
        q = self.find.text()
        rows = []
        for r in self.data.get("rooms") or []:  # OpenMausBot's order: rooms first, then the bots
            if not roster_match({"name": r["name"], "role": " ".join(r.get("members") or [])}, q):
                continue
            row = BotRow(f"Room {r['name']}, with {', '.join(r.get('members') or [])}: {r.get('line') or 'no messages yet'}")
            faces = QWidget()
            faces.setFixedSize(52, 40)
            looks = {b["name"]: b.get("look") or {} for b in self.data.get("bots") or []}
            for i, member in enumerate((r.get("members") or [])[:3]):
                f = bot_face(looks.get(member), 26, name=f"{member}'s face")
                f.setParent(faces)
                f.move(i * 12, (i % 2) * 12)
            row.add(faces)
            row.add(self._two_lines(r["name"], r.get("line") or "No messages yet", TEXT_3), 1)
            rows.append((f"room:{r['id']}", row))
        bots_ = [b for b in roster_rows(self.data, True) if roster_match(b, q)]
        hidden = [b for b in bots_ if b.get("hidden")]
        for b in [b for b in bots_ if not b.get("hidden")] + (hidden if self.show_hidden else []):
            name = b["name"]
            st = (b.get("status") or {}).get("state")
            line = bot_line(b)
            row = BotRow(f"{name}{', ' + b['role'] if b.get('role') else ''}: {line}" + (f", {b['unread']} unread" if b.get("unread") else "")
                         + (", pinned" if b.get("pinned") else "") + (", hidden" if b.get("hidden") else ""),
                         talk=lambda n=name: self._talk(n), menu=lambda pos, n=name, bb=b: self._menu(bb, pos))
            row.add(bot_face(b.get("look"), 36, bot_mood(b), f"{name}'s face"))
            row.add(self._two_lines(name, line, K.AMBER if st == "waiting" else K.BLUE if st == "working" else TEXT_3,
                                    dim=bool(b.get("hidden"))), 1)
            if b.get("unread"):
                row.add(K.Chip(str(b["unread"]) if b["unread"] < 100 else "99+", "lit"))
            if b.get("pinned"):
                pin = K.IconButton("pin", f"{name} is pinned", 22)
                row.add(pin)
            rows.append((name, row))
        chosen = f"room:{self.room['id']}" if self.room else self.pick
        pick_list(self.roster, rows, chosen, self._chosen)
        if hidden:
            fold = Button(f"{'Fold away' if self.show_hidden else 'Show'} hidden bots ({len(hidden)})", "quiet",
                          name=f"{'Fold away' if self.show_hidden else 'Show'} the hidden bots")
            fold.clicked.connect(self._fold)
            self.roster.addWidget(fold)
        if not self.data.get("bots"):
            self.roster.addWidget(label("No bots yet. Add Scout, Pixel and Forge below, or make your own on the right.", 12.5, 400, TEXT_3,
                                        wrap=True))
        elif q.strip() and not rows:
            self.roster.addWidget(label(f"Nothing matches “{q.strip()}”.", 12.5, 400, TEXT_3))
        keep = self.b_agent.currentData()
        self.b_agent.clear()
        for k, words_ in agent_choices(self.data) or [("auto", "Monday decides")]:
            self.b_agent.addItem(words_, k)
        i = self.b_agent.findData(keep or "auto")
        self.b_agent.setCurrentIndex(i if i >= 0 else 0)
        missing = missing_templates(self.data)
        self.add3.setEnabled(bool(missing))
        n = len(self.data.get("bots") or [])
        self.count.setText(f"{n} bot{'' if n == 1 else 's'}" + ("" if missing or not n else " · Scout, Pixel and Forge are all here"))
        if self.room:  # the open room as Core now holds it
            self.room = next((x for x in self.data.get("rooms") or [] if x["id"] == self.room["id"]), None)
            if self.room:
                self._room_head()
            else:
                self._room_mode(False)
        if self.pick:
            self._state_line()

    @staticmethod
    def _two_lines(top, below, color, dim=False):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(K.Elided(top, 13, 600, TEXT_3 if dim else TEXT))
        v.addWidget(K.Elided(below, 12, 400, color))
        w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return w

    def _fold(self):
        self.show_hidden = not self.show_hidden
        self.show()

    def _nth(self, i):
        names = [b["name"] for b in self.data.get("bots") or [] if self.show_hidden or not b.get("hidden")]
        if i < len(names):
            self._chosen(names[i])
            self.show()

    def _chosen(self, key):
        if key.startswith("room:"):
            return self._room_open(key[5:])
        self.choose(key)

    def bot(self, name):
        return next((b for b in self.data.get("bots") or [] if b["name"] == name), None)

    def choose(self, name):
        """A bot chosen: its settings in the editor."""
        from .native import BOT_COLOURS, bot_pets, colour_index
        b = self.bot(name)
        if not b:
            return
        self.pick = name
        self._room_mode(False)
        self.editor.title_label.setText(name)
        self.b_name.setText(b.get("name") or "")
        self.b_role.setText(b.get("role") or "")
        self.b_text.setPlainText(b.get("instructions") or "")
        self.b_project.setText(b.get("project") or "")
        i = self.b_agent.findData(b.get("agent") or "auto")
        self.b_agent.setCurrentIndex(i if i >= 0 else 0)
        i = self.b_model.findData(b.get("model") or "")
        self.b_model.setCurrentIndex(i if i >= 0 else 0)
        self.b_computer.set_quiet(b.get("computer", True) is not False)
        look = b.get("look") or {}
        self.b_kind.setCurrentIndex(bot_pets().index(look["kind"]) if look.get("kind") in bot_pets() else -1)
        self.b_colour.setCurrentIndex(colour_index(look))
        self.b_preview.art = PetArt({"kind": look.get("kind") or "maus", "body": look.get("body") or BOT_COLOURS[1][0],
                                     "accent": look.get("accent") or "#FFFFFF", "intensity": 0.6})
        self.b_preview.update()
        self._state_line()
        self.note.say("")

    def _state_line(self):
        from .native import bot_line
        b = self.bot(self.pick)
        if not b:
            return
        self.b_state.setText(bot_line(b) + (f" · {b['unread']} unread" if b.get("unread") else ""))
        self.b_buttons["pin"].setText("Unpin" if b.get("pinned") else "Pin")
        self.b_buttons["hide"].setText("Show" if b.get("hidden") else "Hide")

    def new_bot(self):
        self.pick = None
        self._room_mode(False)
        for i in range(self.roster.count()):
            w = self.roster.itemAt(i).widget()
            if isinstance(w, PickRow):
                w.setChecked(False)
        self.editor.title_label.setText("A new bot")
        self.b_name.setText(self.data.get("suggested") or "")  # OpenMausBot's: a friendly name no bot has yet
        for w in (self.b_role, self.b_project):
            w.clear()
        self.b_text.clear()
        self.b_agent.setCurrentIndex(max(0, self.b_agent.findData("auto")))
        self.b_model.setCurrentIndex(0)
        self.b_computer.set_quiet(True)
        self.b_kind.setCurrentIndex(-1)
        self.b_colour.setCurrentIndex(-1)
        self._preview()
        self.b_state.setText("A new bot: give it a name, then Save bot.")
        self.b_buttons["pin"].setText("Pin")
        self.b_buttons["hide"].setText("Hide")

    def _face(self):
        from .native import BOT_COLOURS, bot_pets
        kind = self.b_kind.currentData() or "maus"
        c = BOT_COLOURS[self.b_colour.currentIndex()] if self.b_colour.currentIndex() >= 0 else BOT_COLOURS[1]
        return {"kind": kind if kind in bot_pets() else "maus", "body": c[0], "accent": c[1]}

    def _preview(self):
        self.b_preview.art = PetArt({**self._face(), "intensity": 0.6})
        self.b_preview.update()

    def requests(self, what):
        """What a bot's button sends Core: [(method, path, body)], or the sentence saying what is missing. Pin and Hide send the bot as
        Core holds it, never the editor's unsaved words."""
        from .native import missing_templates
        name = self.b_name.text().strip()
        saved = {(b.get("name") or "").lower(): b for b in self.data.get("bots") or []}
        if what == "save":
            if not name:
                return "Give the bot a name first."
            body = {"name": name, "role": self.b_role.text().strip(), "instructions": self.b_text.toPlainText().strip(),
                    "agent": self.b_agent.currentData() or "auto", "project": self.b_project.text().strip() or None,
                    "computer": self.b_computer.isChecked()}
            if name.lower() not in saved:
                body["welcome"] = True  # a new bot made here says hello in the chat (OpenMausBot's first card)
            if self.b_model.currentData() is not None:
                body["model"] = self.b_model.currentData()
            if self.b_kind.currentIndex() >= 0 or self.b_colour.currentIndex() >= 0:
                body["face"] = self._face()
            return [("POST", "/api/bots", body)]
        if what == "templates":
            return [("POST", "/api/bots", {k: t.get(k) for k in ("name", "role", "instructions", "agent")})
                    for t in missing_templates(self.data)] or "You have all three already."
        b = saved.get(name.lower())
        if not b:
            return "Choose a saved bot first."
        q = urllib.parse.quote(b["name"], safe="")
        if what in ("pin", "hide"):
            key = "pinned" if what == "pin" else "hidden"
            return [("POST", "/api/bots", {**{k: b.get(k) for k in ("name", "role", "instructions", "agent", "project")}, key: not b.get(key)})]
        return {"delete": [("DELETE", f"/api/bots/{q}", None)], "duplicate": [("POST", f"/api/bots/{q}/duplicate", None)]}[what]

    def apply(self, what):
        reqs = self.requests(what)
        if isinstance(reqs, str):
            return self.note.say(reqs, "amber")
        name = self.b_name.text().strip()
        if what == "delete" and not K.confirm(self, f"Delete {name}?", f"Delete the bot {name}? Its group and what it did stay.", "Delete",
                                              danger=True):
            return
        words_ = {"save": f"Saved. Say “{name}, …” to ask it.", "delete": "Deleted; its group and what it did stay.",
                  "templates": "Added. Say “Scout, find …”, “Pixel, …” or “Forge, …”.", "duplicate": f"Copied {name}.", "pin": "Done.",
                  "hide": "Done."}[what]
        self.note.say("Saving…", "amber")
        left = [len(reqs)]

        def done(d, err):
            if err:
                return self.note.say(f"Not saved: {err}")
            if what == "duplicate" and isinstance(d, dict) and d.get("bot"):
                self.pick = d["bot"]["name"]
            if what == "save":
                self.pick = name
            left[0] -= 1
            if left[0] == 0:
                self.note.say(words_, "green")
                if what == "delete":
                    self.new_bot()
                self.bridge.get("/api/bots", lambda b, _e: (self.show({**(b or {}), "rooms": self.data.get("rooms") or []}),
                                                            self.choose(self.pick) if self.pick and what != "delete" else None))
        for method, path, body in reqs:  # the starting three: one POST each
            self.bridge.call(method, path, body, done)

    def _menu(self, b, pos):
        """OpenMausBot's right-click menu on a bot: Pin, Mark as unread, Edit, Duplicate, Hide, Delete."""
        self.choose(b["name"])
        m = QMenu(self)
        m.addAction("Unpin" if b.get("pinned") else "Pin", lambda: self.apply("pin"))
        unread = m.addAction("Mark as unread", lambda: self.bridge.post(
            f"/api/bots/{urllib.parse.quote(b['name'], safe='')}/unread", None,
            lambda _d, err: self.note.say(f"Couldn't mark it unread: {err}") if err else self.load()))
        unread.setEnabled(not b.get("unread"))
        m.addAction("Edit", lambda: self.b_name.setFocus())
        m.addAction("Duplicate", lambda: self.apply("duplicate"))
        m.addSeparator()
        m.addAction("Show in the roster" if b.get("hidden") else "Hide", lambda: self.apply("hide"))
        m.addAction("Delete…", lambda: self.apply("delete"))
        m.exec(pos)

    def _talk(self, name):
        """A double-click on a bot: the chat, with “Name, ” in its box (core/bots.py address); its unread count starts again from now."""
        chat = self.win.screens.get("chat")
        self.win.show_page("chat")
        if chat is not None and chat.built:
            chat.box.setPlainText(f"{name}, ")
            chat.box.moveCursor(chat.box.textCursor().MoveOperation.End)
            chat.box.setFocus()
        self.bridge.post(f"/api/bots/{urllib.parse.quote(name, safe='')}/seen", None)

    def _ask(self):
        name, text = self.b_name.text().strip(), self.b_ask.text().strip()
        if not self.bot(name) and name.lower() not in {(b.get("name") or "").lower() for b in self.data.get("bots") or []}:
            return self.note.say("Choose a saved bot first.", "amber")
        if not text:
            return self.note.say(f"Type what to ask {name}.", "amber")
        self.win.command(f"{name}, {text}", done=lambda _m, err: self.note.say(f"Not asked: {err}" if err else
                                                                              f"Asked {name}: Chat shows its reply.", "red" if err else "green"))
        self.b_ask.clear()

    # rooms
    def _room_mode(self, on):
        self.editor.setVisible(not on)
        self.room_card.setVisible(on)
        if not on:
            self.room = None

    def _room_head(self):
        from .native import responder_choices, room_hint
        r = self.room
        self.r_title.setText(r["name"])
        self.r_who.clear()
        for k, words_ in responder_choices(r):
            self.r_who.addItem(words_, k)
        self.r_who.setCurrentIndex(max(0, self.r_who.findData(r.get("responder") or "lead")))
        self.r_who.setEnabled(True)
        self.r_bulletin.setText(f"Instructions: {r['bulletin']}" if r.get("bulletin") else "No room instructions. Edit room adds some.")
        self.r_box.setPlaceholderText(room_hint(r))

    def _room_open(self, rid):
        r = next((x for x in self.data.get("rooms") or [] if x["id"] == rid), None)
        if not r:
            return
        self.room, self.pick = r, None
        self._room_mode(True)
        self.room = r
        self.r_editor.hide()
        self._room_head()
        while self.r_log.count() > 1:
            w = self.r_log.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.room_seen = set()
        self.bridge.get(f"/api/rooms/{rid}/messages", lambda d, err: self.note.say(f"Couldn't read the room: {err}") if err else
                        [self._room_line(m) for m in (d or {}).get("messages") or []])

    def _room_line(self, m):
        """One line of the open room: a face and a name over the words, the newest at the bottom."""
        from .chat import plain
        from .native import speaker
        if not self.room or (m.get("meta") or {}).get("chatroom") != self.room["id"] or m.get("id") in self.room_seen:
            return
        self.room_seen.add(m.get("id"))
        who = speaker(m)
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        if who in ("You", "Monday"):
            h.addSpacing(26)
        else:
            look = (self.bot(who) or {}).get("look")
            h.addWidget(bot_face(look, 26, name=f"{who}'s face"), 0, Qt.AlignmentFlag.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(2)
        v.addWidget(label(who, 12.5, 700, ORANGE if who == "You" else TEXT))
        color = K.RED_TEXT if m.get("kind") == "error" else TEXT_3 if m.get("kind") == "system" else TEXT_2
        words_ = label(plain(m.get("text") or ""), 13, 400, color, wrap=True)
        words_.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(words_)
        h.addLayout(v, 1)
        line.setAccessibleName(f"{who}: {plain(m.get('text') or '')[:200]}")
        self.r_log.insertWidget(self.r_log.count() - 1, line)
        QTimer.singleShot(0, lambda: self.r_scroll.verticalScrollBar().setValue(self.r_scroll.verticalScrollBar().maximum()))

    def _say(self):
        text = self.r_box.text().strip()
        if not self.room or not text:
            return
        self.r_box.clear()
        self.bridge.post(f"/api/rooms/{self.room['id']}/say", {"text": text}, lambda _d, err: self.note.say(f"Not said: {err}") if err else None)

    def _room_new(self):
        self._room_mode(True)
        self.room = None
        self.r_title.setText("A new room")
        self.r_bulletin.setText("Pick its bots, then Save room.")
        self.r_who.clear()
        self.r_who.setEnabled(False)
        while self.r_log.count() > 1:
            w = self.r_log.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._room_edit(new=True)

    def _room_edit(self, new=False):
        r = None if new else self.room
        names = [b["name"] for b in self.data.get("bots") or []]
        while self.r_members.count():
            w = self.r_members.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.member_ticks = {}
        for n in names:
            t = K.Tick(f"{n} is in the room", bool(r) and n in (r.get("members") or []))
            self.member_ticks[n] = t
            self.r_members.addWidget(t)
            self.r_members.addWidget(label(n, 12.5, 400, TEXT_2))
            self.r_members.addSpacing(6)
        self.r_members.addStretch(1)
        if not names:
            self.r_members.addWidget(label("No bots yet: make one first.", 12.5, 400, TEXT_3))
        self.r_name.setText((r or {}).get("name") or "")
        self.r_lead.clear()
        for n in names:
            self.r_lead.addItem(n, n)
        self.r_lead.setCurrentIndex(self.r_lead.findData((r or {}).get("lead")))
        self.r_text.setPlainText((r or {}).get("bulletin") or "")
        self.r_editor.show()
        self.r_name.setFocus()

    def room_requests(self, what):
        r = self.room
        if what == "save":
            members = [n for n, t in getattr(self, "member_ticks", {}).items() if t.isChecked()]
            if not members:
                return "Pick at least one bot for the room."
            lead = self.r_lead.currentData()
            return [("POST", "/api/rooms", {"id": (r or {}).get("id"), "name": self.r_name.text().strip(), "members": members,
                                            "responder": (r or {}).get("responder") or "lead", "lead": lead if lead in members else None,
                                            "bulletin": self.r_text.toPlainText().strip()})]
        if not r:
            return "Choose a room first."
        if what == "responder":
            return [("POST", "/api/rooms", {**{k: r.get(k) for k in ("id", "name", "members", "lead", "bulletin")},
                                            "responder": self.r_who.currentData() or "lead"})]
        return [("DELETE", f"/api/rooms/{r['id']}", None)]

    def room_apply(self, what):
        reqs = self.room_requests(what)
        if isinstance(reqs, str):
            return self.note.say(reqs, "amber")
        if what == "delete" and not K.confirm(self, "Delete the room?", f"Delete the room {self.room['name']}? Its bots and what they said "
                                                                         "stay.", "Delete", danger=True):
            return
        method, path, body = reqs[0]

        def done(d, err):
            if err:
                return self.note.say(f"Not saved: {err}")
            self.note.say("")
            room = d if isinstance(d, dict) and d.get("id") else None  # POST /api/rooms answers with the room as Core keeps it
            if what == "delete" or not room:
                self._room_mode(False)
                self.new_bot()
            else:
                self.room = room
                self.r_editor.hide()
                self._room_head()
            self.load()
        self.bridge.call(method, path, body, done)

    def _export(self):
        names = [b["name"] for b in self.data.get("bots") or [] if self.show_hidden or not b.get("hidden")]
        if not names:
            return self.note.say("No bots in the roster to export.", "amber")
        path, _ = QFileDialog.getSaveFileName(self, "Export team", "my-team.mausteam.json", "OpenMaus team (*.mausteam.json);;JSON (*.json)")
        if not path:
            return
        team = re.sub(r"\.(mausteam\.)?json$", "", os.path.basename(path), flags=re.I).replace("-", " ").strip() or "My team"

        def done(d, err):
            if not err:
                try:
                    import json
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(d, f, indent=2, ensure_ascii=False)
                except OSError as e:
                    err = str(e)
            self.note.say(f"Not exported: {err}" if err else f"Exported {len(names)} bot{'s' if len(names) != 1 else ''} as “{team}” to {path}.",
                          "red" if err else "green")
        self.bridge.get(f"/api/teams/export?name={urllib.parse.quote(team)}&names={urllib.parse.quote(','.join(names))}", done)

    def _import(self):
        import json
        from .native import team_preview
        path, _ = QFileDialog.getOpenFileName(self, "Import team", "", "OpenMaus team (*.mausteam.json *.json)")
        if not path:
            return
        try:
            if os.path.getsize(path) > (1 << 20):
                raise ValueError("A team file is at most 1 MB.")
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            return self.note.say(f"Couldn't read it: {e}")
        team, names, bad = team_preview(d)
        if bad:
            return self.note.say(bad)
        if not K.confirm(self, f"Import “{team}”?", f"This makes {len(names)} new bot{'s' if len(names) != 1 else ''}: "
                         f"{', '.join(names[:12])}{'…' if len(names) > 12 else ''}. They start without computer access, on Monday's own choice "
                         "of agent and model; nothing else comes from the file.", "Import"):
            return
        self.bridge.post("/api/teams/import", d, lambda r, err: self.note.say(f"Not imported: {err}") if err else (
            self.note.say(f"Imported {len(r['bots'])} bots from “{r['team']}”" + (f", and its room “{r['room']['name']}”."
                                                                                  if r.get("room") else "."), "green"), self.load()))


SCREENS = (HomeScreen, ChatScreen, TasksScreen, BrowserScreen, ActivityScreen, StatsScreen, SettingsScreen, LibraryScreen, BotsScreen,
           RoutinesScreen, TestsScreen, CameraScreen, GeneralScreen, PillsScreen, AgentsScreen, IntegrationsScreen, PhoneScreen, ShortcutsScreen)
# -- running it -----------------------------------------------------------------------------------------------------------------
def shots(folder, base=None, size=QSize(1534, 794)):
    """Every page drawn off screen at the recording's size (its 1918 x 992 client area at 125 %), after Core answered. base: a test
    Core's address (never the owner's by accident: the pictures read only, and the ear stays off)."""
    make_app(["monday"])
    bridge = Bridge(Core(base) if base else None)
    from . import desktop
    win = Window(bridge, store=desktop.load_state(), ear=False)
    win.resize(size)
    os.makedirs(folder, exist_ok=True)
    out = []
    win.poll()
    bridge.start()  # the stream finding Core is what takes the start screen away (2026-10-29: every picture showed "Starting…" since 10-21)
    end = time.monotonic() + 20
    while not win.start_screen.isHidden() and time.monotonic() < end:
        K.QApplication.instance().processEvents()
        time.sleep(0.02)
    for key in win.screens:
        win.show_page(key)
        bridge.settle(45.0)  # (the agents' discovery takes a while on a cold Core)
        path = os.path.join(folder, f"win_{key}.png")
        render(win, path, size)
        out.append(path)
    bridge.stop()
    return out


def _check():
    app = make_app(["monday"])
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        ok = ok and bool(cond)

    class Stub(Core):  # Core that answers from a table, so the pages are checked without a server
        def __init__(self, answers):
            self.answers, self.calls, self.http = answers, [], None

        def call(self, method, path, body=None, timeout=None):
            self.calls.append((method, path, body))
            for prefix, ans in self.answers.items():
                if path.startswith(prefix):
                    return ans if isinstance(ans, tuple) else (ans, None)
            return None, "Monday Core is unreachable."

    day = time.strftime("%Y-%m-%d")
    stats = {"span": "day", "days": [{"date": day, "requests": 3, "spoken": 1, "tasks": 2, "done": 1, "failed": 1}],
             "requests": {"total": 3, "spoken": 1, "typed": 2, "by_source": {"desktop": 3}},
             "tasks": {"total": 2, "done": 1, "failed": 1, "stopped": 0, "active": 0, "success_rate": 0.5, "worked_s": 95.0,
                       "median_run_s": 47.5, "by_agent": [{"agent": "codex", "n": 2}]},
             "opened": [{"target": "chrome", "n": 2}], "approvals": {"asked": 0}, "streak_days": 4}
    core = Stub({"/api/status": {"name": "Monday", "version": "0.2.0", "state": "IDLE"},
                 "/api/stats": stats, "/api/activity": {"items": [{"ts": time.time() - 90, "what": "Task completed", "detail": "Open Chrome",
                                                                   "tone": "done"}]},
                 "/api/settings": {"default_stt": "local"}, "/api/stt": {"stage": "closed"},
                 "/api/agents/brief": {"line": "Claude Code worked on shop: 3 sessions."},
                 "/api/search": {"tasks": [{"created_at": time.time(), "title": "Open Spotify", "status": "COMPLETED"}], "messages": []}})
    bridge = Bridge(core)
    win = Window(bridge, ear=False)
    win.resize(1534, 794)
    bridge.settle()
    check("start: the owner's pet and “Starting…” over the pages until Core answers (2026-10-21)",
          not win.start_screen.isHidden() and not win.start_screen.leaving and win.start_screen.words.text() == "Starting…")
    win._online("ONLINE")
    check("start: Core answers: the dots stop, the start screen leaves", win.start_screen.leaving and win.start_screen.words.text() == "Ready.")
    home = win.screens["home"]
    check("home: today's numbers from /api/stats", home.n_asked.num.text() == "3" and home.n_done.num.text() == "1"
          and home.n_streak.num.text() == "4", f"{home.n_asked.num.text()} / {home.n_done.num.text()} / {home.n_streak.num.text()}")
    check("home: the history row from /api/activity", home.rows.count() == 1 and not home.empty.isVisible())
    check("home: the reader shown is Core's (local = Whisper)", home.reader.currentData() == "local"
          and home.reader_name.text().startswith("Whisper"))
    home._got_settings(None, "Monday Core is unreachable.")
    failed_shown = home.reader_note.isVisibleTo(home) and "couldn't be read" in home.reader_note.text_label.text()
    home._got_settings({"default_stt": "local"}, None)
    check("home: an answer clears an earlier “Who reads your words couldn't be read” (2026-10-28: it stayed after Core came up)",
          failed_shown and not home.reader_note.isVisibleTo(home) and not home.reader_note.text_label.text())
    # a page's call while this app is still starting Core (2026-10-28, at sign-in): held, sent again once the stream finds Core; said
    # with its own words when the supervisor gives up; said at once when Core isn't being started by this app
    from .native import Unsent

    class _Late:
        up = False

        def call(self, method, path, body=None, timeout=None):
            return ({"ok": 1}, None) if self.up else (None, Unsent("Monday Core is unreachable."))
    lb, got = Bridge(_Late()), []
    lb.waiting = lambda: True
    lb.get("/api/settings", lambda d, e: got.append((d, e)))
    lb.settle(0.6)
    held_first = not got and len(lb.held) == 1
    lb.core.up = True
    lb._state("ONLINE")
    lb.settle(3.0)
    check("start: a call made while this app starts Core waits for it, then gets Core's answer (no “unreachable” note)",
          held_first and got == [({"ok": 1}, None)] and not lb.held, f"{got} {lb.held}")
    lb.core.up, lb.state = False, "OFFLINE"
    lb.post("/api/voices", {"voices": []}, lambda d, e: got.append((d, e)))
    lb.settle(0.6)
    held_post = len(lb.held) == 1
    lb.release()
    check("start: a held call says why it failed when the supervisor gives up (Bridge.release)",
          held_post and got[-1] == (None, "Monday Core is unreachable.") and not lb.held, f"{got}")
    lb.waiting = lambda: False
    lb.get("/api/status", lambda d, e: got.append((d, e)))
    lb.settle(2.0)
    check("start: Core not being started by this app: a failure is said at once, nothing held",
          len(got) == 3 and got[-1] == (None, "Monday Core is unreachable.") and not lb.held, f"{got}")
    lb.stop()

    class _Busy:  # Core up but still warming: the first read times out, the next one answers (2026-10-29: GET /api/ai-tools at start)
        n = 0

        def call(self, method, path, body=None, timeout=None):
            self.n += 1
            return (None, "Monday Core didn't answer in time.") if self.n == 1 else ({"tools": []}, None)
    wb, wgot = Bridge(_Busy()), []
    wb._state("ONLINE")
    wb.get("/api/ai-tools", lambda d, e: wgot.append((d, e)))
    wb.settle(4.0)
    check("start: a read Core was too busy to answer just after it came up is asked again (its answer, no “didn't answer in time”)",
          wgot == [({"tools": []}, None)] and wb.core.n == 2, f"{wgot} after {wb.core.n} calls")
    wb._online_at -= WARM_S
    wb.core.n = 0
    wb.get("/api/ai-tools", lambda d, e: wgot.append((d, e)))
    wb.settle(2.0)
    check("start: long after Core came up, a slow read is said at once", wgot[-1] == (None, "Monday Core didn't answer in time.")
          and wb.core.n == 1, f"{wgot}")
    wb.stop()
    check("home: the brief is the agents' line", home.brief.text().startswith("Claude Code"))
    home.search.setText("spot")
    home._search()
    bridge.settle()
    check("home: search asks /api/search and shows the task", any(p.startswith("/api/search?q=spot") for _m, p, _b in core.calls)
          and home.rows.count() == 1)
    home.reader.setCurrentIndex(home.reader.findData("google"))
    home._pick_reader(0)
    bridge.settle()
    check("home: picking a reader posts default_stt", ("POST", "/api/settings", {"default_stt": "google"}) in core.calls)
    core.answers.update({
        "/api/chats": {"chats": [{"title": "Today", "current": True, "start": 0, "at": time.time()}]},
        "/api/messages": {"messages": [{"id": 1, "role": "user", "kind": "text", "text": "what's 2+2?"},
                                       {"id": 2, "role": "monday", "kind": "text", "text": "It's **4**.\n\n- one\n- two",
                                        "meta": {"reply_to": 1, "model": "gpt-oss"}}]},
        "/api/command": {"message": {"id": 9}}, "/api/permissions/": {"id": 4, "status": "GRANTED", "title": "Hermes wants to run a command"},
        "/api/voice": {"settings": {"interaction_mode": "pure_agent", "enabled": True, "tts_provider": "custom"},
                       "providers": [{"id": "custom", "label": "Your voice (cloned from your recording, on this PC)", "status": "ready"}]},
        "/api/tasks/": {}})
    win.show_page("chat")
    bridge.settle()
    ch = win.screens["chat"]
    check("chat: the owner's bubble and Monday's formatted reply", any(isinstance(w, Prose) and "4" in w.text for w in ch.column.findChildren(Prose))
          and len(ch.shown) == 2)
    ch.on_event({"type": "MESSAGE_DELTA", "data": {"stream_id": "s1", "delta": "Work"}})
    ch.on_event({"type": "MESSAGE_DELTA", "data": {"stream_id": "s1", "delta": "ing…"}})
    check("chat: deltas wait for the frame, then draw together", "s1" not in ch.streams and ch._flush.isActive() and ch._delta["s1"] == "Working…")
    ch._flush_deltas()
    check("chat: a reply streams in", ch.streams["s1"].text == "Working…" and not ch._delta)
    ch.on_event({"type": "MESSAGE_DELTA", "data": {"stream_id": "s1", "delta": "Instead: ", "model": "qwen3"}})
    ch._flush_deltas()
    check("chat: another model taking the reply over under the same stream id (the first refused, called a tool or stalled) starts its words "
          "again, never two answers run together (2026-10-27)", ch.streams["s1"].text == "Instead: " and ch._live_model["s1"] == "qwen3",
          ch.streams["s1"].text)
    check("chat: the Voice picker under the box says whose voice speaks (the owner's own here)", ch.voice.text() == "Voice: Your voice ▾", ch.voice.text())
    was = ch.loaded_at
    win._online("RECONNECTING")
    win._online("ONLINE")
    check("Core back after an outage: the page on show is loaded again (2026-10-20: pages stayed on 'unreachable')", ch.loaded_at > was)
    win._online("ONLINE")
    check("…and not again while it stays up", ch.loaded_at > was and not win._down)
    ch.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 3, "role": "monday", "kind": "text", "text": "Done.",
                                                                 "meta": {"stream_id": "s1"}}}})
    check("chat: the stored reply replaces its stream", "s1" not in ch.streams and 3 in ch.shown)
    check("…and which model wrote it goes with it", "s1" not in ch._live_model and "s1" not in ch._delta)
    # a long answer made into a document (2026-10-25): its brief shows, Open / Show in folder / Whole answer under it, Copy takes the whole
    whole_text = "Artemis III launches in 2027 [3].\n\n" + "## Crew\n- Four astronauts.\n" * 80
    ch.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 4, "role": "monday", "kind": "text", "text": whole_text, "meta": {
        "model": "gpt-oss", "brief": "Artemis III launches in 2027 [3].\n\nThe whole answer is in “Artemis III.docx” in your Documents.",
        "document": {"name": "Artemis III.docx", "path": os.path.join(os.environ.get("TEMP", "."), "no-such-doc.docx"), "app": "Word"}}}}})
    doc_prose = [w for w in ch.column.findChildren(Prose) if "Artemis" in w.text][-1]
    doc_box = doc_prose.parentWidget()
    doc_btns = {b.text(): b for b in doc_box.findChildren(Button)}
    check("chat: a reply with a document shows its brief and Open / Show in folder / Whole answer / Copy",
          doc_prose.text.startswith("Artemis III launches") and "Crew" not in doc_prose.text
          and {"Open", "Show in folder", "Whole answer", "Copy"} <= set(doc_btns), sorted(doc_btns))
    doc_btns["Whole answer"].click()
    check("…Whole answer shows all of it here, Less takes it back", doc_prose.text == whole_text and doc_btns["Whole answer"].text() == "Less")
    doc_btns["Whole answer"].click()  # (the same button, reading "Less" now)
    doc_btns["Open"].click()
    check("…and Open says so when the file has gone", doc_prose.text.startswith("Artemis III launches") and "Crew" not in doc_prose.text
          and doc_btns["Open"].text() == "Moved or deleted", doc_btns["Open"].text())
    t = {"id": "t1", "title": "Open Chrome", "status": "RUNNING", "current_action": "Opening Chrome"}
    ch.message({"id": 5, "role": "monday", "kind": "task", "task": t})
    ch.on_event({"type": "TASK_STEP", "task_id": "t1", "data": {"step": {"label": "Open Chrome", "status": "running"}}})
    check("chat: a task card follows its steps, Stop while it runs", not ch.cards["t1"].stop.isHidden() and
          "Open Chrome" in ch.cards["t1"].steps.text(), ch.cards["t1"].steps.text())
    ch.box.clear()
    ch._busy()
    check("chat: an empty box with a task running offers Stop", getattr(ch.send_btn, "stopping", False))
    ch.on_event({"type": "TASK_COMPLETED", "data": {"task": {**t, "status": "COMPLETED", "result": "Chrome is open."}}})
    check("chat: its end shows (Done, the result)", "Done" in ch.cards["t1"].state.text() and not ch._running())
    ch.on_event({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 4, "status": "PENDING", "title": "Hermes wants to run a command",
                                                                         "risk": "medium", "always": True}}})
    check("chat: an approval waits above the box with Approve / Always allow / Deny", 4 in ch.perm_cards
          and [b.text() for b in ch.perm_cards[4].buttons] == ["Approve", "Always allow", "Deny"])
    ch._decide({"id": 4}, "approve", ch.perm_cards[4])
    bridge.settle()
    check("chat: Approve is the owner's answer, and the card becomes a line", any(c[1] == "/api/permissions/4/approve" for c in core.calls)
          and 4 not in ch.perm_cards)
    ch.box.setPlainText("open spotify")
    ch.send()
    bridge.settle()
    check("chat: Send posts /api/command", any(c[1] == "/api/command" and (c[2] or {}).get("text") == "open spotify" for c in core.calls))
    # the wake phrase (Core decides what is for Monday): Monday talks on over room talk Core left alone, stops for a sentence it took

    class Talking:  # a stand-in speaker, mid-sentence, noting what it is told
        def __init__(self):
            self.told = []

        def talking(self):
            return True

        def control(self, op):
            self.told.append(op)
    real_sp, real_say, saved_voice, said = win.speaker, win._mic_say, dict(win.voice), []
    win.speaker, win._mic_say = Talking(), said.append
    for answer, words in (({"message": None, "ignored": "I act on what I hear only after “okay Monday”."}, "turn it up"),
                          ({"message": None, "awake": 8.0}, "okay Monday"), ({"message": {"id": 10}}, "okay Monday stop talking")):
        core.answers["/api/command"] = answer
        win._spoken(words, {"stt_start": 1.0}, f"w-{len(said)}")
        bridge.settle()
    check("wake: room talk Core left alone: Monday talks on, the mic line says why", said[:1] == ["Not for me (no “Monday, …” or “okay Monday”): turn it up"]
          and win.speaker.told == ["stop_speaking"], f"{said} {win.speaker.told}")
    check("wake: “okay Monday” alone: it listens for the next sentence", any(s.startswith("Yes? I'm listening") for s in said), said)
    said.clear()
    core.answers["/api/command"] = {"message": None, "gesture": True, "ignored": "Heard after you clapped twice, but not said to me (Jev: 32%), "
                                                                                 "so I left it: “I wouldn't let you know.”."}
    win._spoken("I wouldn't let you know.", {"stt_start": 1.0}, "w-g")
    bridge.settle()
    check("wake: a clap's sentence Jev says isn't said to Monday: the mic line gives Core's why", said == [core.answers["/api/command"]["ignored"]], said)
    said.clear()
    win._woke(True)  # 2026-10-20: the ear hears the phrase itself; a sentence it let in after the phrase alone is quiet
    bridge.settle()
    quiet = not said
    win._woke(False)
    bridge.settle()
    check("wake: the ear's own “okay Monday”: “Yes?” at once (not for a sentence the phrase alone let in)",
          quiet and said == ["Yes? I'm listening"] and (win.notch is None or win.notch.state.heard == "Yes?"), said)
    said.clear()
    win.on_event({"type": "AWAKE", "data": {"by": "snap", "seconds": 8.0, "words": "you snapped your fingers"}})
    check("wake: two claps or a snap while awake open the ear, and the mic line says so (2026-10-20 night)",
          bool(said) and said[-1].startswith("Yes? I'm listening (you snapped your fingers): say it within 8 s"), said)
    win.speaker, win._mic_say = real_sp, real_say

    # a look at the screen (2026-10-29, the owner: "when it marks it should close the chat window and also it should explain too … connect
    # when and what"): the chat goes, the explanation is said, each mark comes as its part is said, and its reply isn't said again
    class Voice:
        def __init__(self, on=True):
            self.on, self.said = on, []

        def enabled(self):
            return self.on

        def say(self, text, reply_to=None, preview=None, cue=None):
            self.said.append((text, cue))

        def control(self, op):
            pass

        def talking(self):
            return False

    class Pad:  # a stand-in notch: what the look asked of it
        def __init__(self):
            self.opened, self.did = True, []

        def close_notch(self):
            self.opened = False
            self.did.append("close")

        def marks_show(self, marks, avoid=None, front=None, hold=None, staged=False):
            self.did.append(("show", len(marks), staged))

        def marks_reveal(self, i):
            self.did.append(("reveal", i))

        def marks_hold(self, seconds):
            self.did.append(("hold", round(seconds, 1)))

    def run_for(seconds):
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
    keep_sp, keep_notch, keep_partial = win.speaker, win.notch, win.last_partial
    win.speaker, win.notch, win.last_partial = Voice(), Pad(), 0.0
    look = {"task": "T-LOOK", "say": "A diagram and a table.", "at": [0.2, 0.6], "avoid": [], "front": 7, "hold": 9.0,
            "marks": [{"kind": "box", "rect": [10, 20, 300, 200], "label": "1 · diagram"}, {"kind": "box", "rect": [10, 300, 300, 90], "label": "2 · table"}]}
    win.on_event({"type": "SCREEN_LOOK", "data": {"task": "T-LOOK"}})
    win.on_event({"type": "SCREEN_EXPLAIN", "data": look})
    said_text, cue = win.speaker.said[0] if win.speaker.said else (None, None)
    staged = list(win.notch.did)
    if cue:
        cue(0.25)  # (its audio plays 0.25 s: mark 1 at 0.05 s, mark 2 at 0.15 s)
    run_for(0.4)
    did = win.notch.did
    win.asked[77] = {"spoken": False, "said": None}
    win._maybe_say({"id": 78, "role": "monday", "kind": "text", "text": "A diagram (1) and a table (2).", "task_id": "T-LOOK", "meta": {"reply_to": 77}})
    check("look: the chat closes; the explanation is said once; each mark comes as its part is said, in order (2026-10-29)",
          staged == ["close", ("show", 2, True)] and said_text == "A diagram and a table." and len(win.speaker.said) == 1
          and [x for x in did if x[0] == "reveal"] == [("reveal", 0), ("reveal", 1)], f"{staged} {did} {win.speaker.said}")
    win.speaker, win.notch = Voice(on=False), Pad()
    win.on_event({"type": "SCREEN_LOOK", "data": {"task": "T-QUIET"}})
    win.on_event({"type": "SCREEN_EXPLAIN", "data": {**look, "task": "T-QUIET"}})
    check("look: with the voice off the chat stays, nothing is said, and the marks come at reading pace (2026-10-29)",
          "close" not in win.notch.did and not win.speaker.said and ("hold", 8 + 1.5 + 1.0) in win.notch.did, str(win.notch.did))
    win.speaker, win.notch, win.last_partial = keep_sp, keep_notch, keep_partial
    if win.isMinimized():
        win.showNormal()
    core.answers["/api/command"] = {"message": {"id": 9}}
    win._got_voice({"settings": {**saved_voice, "wake": False}}, None)
    check("home: the wake switch is Core's (off: “Monday, …”)", not home.wake.isChecked() and "“Monday, …”" in home.ear_words.text(),
          home.ear_words.text())
    home.wake.setChecked(True)  # the owner's click
    bridge.settle()
    check("home: turning it on posts wake", ("POST", "/api/voice/settings", {"wake": True}) in core.calls)
    win._got_voice({"settings": saved_voice}, None)
    check("home: on, the words name both ways in, the bare name and “okay Monday” (the owner, 2026-10-20; the code took both, the words said one)",
          home.wake.isChecked() and "“Monday, …” or “okay Monday”" in home.wake_words.text() and "“Monday, …” or “okay Monday …”" in home.ear_words.text()
          and "Only after “Monday, …” or “okay Monday”" in home.wake.accessibleName(), f"{home.wake_words.text()} / {home.ear_words.text()}")
    win.show_page("stats")
    bridge.settle()
    st = win.screens["stats"]
    check("stats: tiles from Core, N/A never invented", st.tiles["requests"][0].text() == "3" and st.tiles["success"][0].text() == "50 %"
          and st.tiles["worked"][0].text() == "2 min", ", ".join(t[0].text() for t in st.tiles.values()))
    check("stats: the chart's data is readable as text", "3 requests" in st.bars.accessibleDescription())
    core.answers["/api/stats"] = (None, "Monday Core is unreachable.")
    st.load()
    bridge.settle()
    check("stats: Core away says so (N/A and the reason)", st.tiles["requests"][0].text() == "N/A" and "unreachable" in st.note.text_label.text())
    core.answers.update({
        "/api/ai-tools": {"tools": [{"key": "codex", "name": "Codex", "kind": "coding agent", "status": "available", "installed": True,
                                     "detail": "codex 0.9", "setup": {"login": "codex login"}, "context": "cli",
                                     "auth_status": "not_authenticated", "remediation": "Run codex login in a terminal."},
                                    {"key": "kilo", "name": "Kilo Code", "kind": "coding agent", "status": "unavailable", "installed": False,
                                     "setup": {"install": "npm i -g kilo", "install_how": "npm installs it"}}],
                          "providers": [{"key": "anthropic", "name": "Claude", "key_env": "ANTHROPIC_API_KEY", "status": "available"}],
                          "keys": ["ANTHROPIC_API_KEY"]},
        "/api/google": {"state": "connected", "account": {"email": "owner@example.com"},
                        "services": [{"id": "calendar", "label": "Calendar", "status": "connected", "does": "your events", "enabled": True}]},
        "/api/webhooks": {"webhooks": [{"id": "h1", "name": "build", "prompt": "run the build", "enabled": False, "runs": 0}]},
        "/api/jev": {"health": {"status": "ok", "latency_ms": 180}},
        "/api/agents/hooks": {"approvals": True, "stop": True, "pre": True, "mcp": False},
        "/api/models": {"models": [{"id": "auto", "name": "Automatic", "where": "auto", "available": True, "selected": True}]},
        "/api/approvals": {"mode": "always", "grants": [{"who": "Scout", "kind": "command", "key": "npm test", "words": "Scout may run npm test"}]},
        "/api/voice": {"settings": {"interaction_mode": "pure_agent", "enabled": True, "tts_provider": "custom"},
                       "providers": [{"id": "custom", "label": "Your voice", "status": "ready"}]},
        "/api/keys": {"saved": "ANTHROPIC_API_KEY"}, "/api/webhooks/h1": {}, "/api/models/select": {"text": "ok"},
    })
    core.answers["/api/status"] = {"name": "Monday", "version": "0.2.0", "components": {"ollama": {"status": "ready"},
                                                                                       "obsidian": {"status": "unknown"}}}
    win.show_page("integrations")
    bridge.settle()
    ig = win.screens["integrations"]
    check("integrations: Google's state and its service switch from Core", ig.google_card.title_dot.state == "ok" and ig.services.n == 1)
    check("integrations: an unknown component stays grey (never counted as ready)", component_dot("unknown") == "off"
          and component_dot("ready") == "ok" and component_dot("not_configured") == "bad")
    box = ig.keys.grid.itemAtPosition(0, 2).widget()
    box.setText("sk-test-123")
    ig._save_key("ANTHROPIC_API_KEY", box)
    bridge.settle()
    check("integrations: a key goes to POST /api/keys, and the list read again shows an empty box", ("POST", "/api/keys", {
        "name": "ANTHROPIC_API_KEY", "value": "sk-test-123"}) in core.calls and ig.keys.grid.itemAtPosition(0, 2).widget().text() == "")
    win.show_page("agents")
    bridge.settle()
    ag = win.screens["agents"]
    check("agents: the five hooks as Claude Code's settings say (3 of 5)", ag.cc.title_dot.state == "wait" and ag.hook_rows.n == 5)
    ag.code.setCurrentIndex(ag.code.findData("codex"))
    ag._pick("default_coding_agent", ag.code)
    bridge.settle()
    check("agents: picking the coder posts it", ("POST", "/api/settings", {"default_coding_agent": "codex"}) in core.calls)
    core.answers["/api/ai-tools"]["tools"].append({"key": "myagent", "name": "My agent", "kind": "coding agent", "status": "available",
                                                   "installed": True, "custom": True, "setup": {}})
    core.answers["/api/engines"] = {"key": "openclaw"}
    ag.load()
    bridge.settle()
    removes = [b.accessibleName() for b in ag.findChildren(Button) if b.accessibleName().startswith("Remove the engine")]
    check("agents: only an engine the owner added offers Remove", removes == ["Remove the engine My agent"], ", ".join(removes))
    ag.eng_name.setText("OpenClaw")
    ag.eng_cli.setText("openclaw acp")
    ag._add_engine()
    bridge.settle()
    check("agents: Add engine posts Core's EngineIn", ("POST", "/api/engines", {"key": "", "name": "OpenClaw", "cli": "openclaw acp",
                                                                                "coding": True, "mcp": False}) in core.calls)
    check("agents: a tool's auth context and what to do about it, when Core reports them (core/agents.py, 2026-10-27)",
          any(w.text() == "coding agent · ready · cli: not authenticated" for w in ag.tools.findChildren(K.QLabel))
          and any(w.accessibleName() == "Run codex login in a terminal." for w in ag.tools.findChildren(K.Elided)),
          " | ".join(w.text() for w in ag.tools.findChildren(K.QLabel) if "·" in w.text()))
    now = time.time()
    core.answers.update({  # (the longer paths first: the stub answers by prefix)
        "/api/remote/devices": {"devices": [
            {"id": "3", "name": "Bhavy's Pixel", "platform": "android", "app_version": "0.1.0", "capabilities": {"approve": True, "control": True},
             "created_at": now - 86400, "last_seen": now - 120, "last_address": "192.168.29.40", "revoked_at": None, "rotated_at": None},
            {"id": "2", "name": "Old tablet", "platform": "ios", "app_version": "0.1.0", "capabilities": {"approve": True, "control": True},
             "created_at": now - 9 * 86400, "last_seen": None, "last_address": None, "revoked_at": now - 3600, "rotated_at": None}]},
        "/api/remote/pairing": {"code": "48210937", "expires_at": now + 108, "on": True,
                                "addresses": {"lan": ["https://192.168.29.5:8790"], "public": "https://monday.ngrok.app"}},
        "/api/remote": {"on": True, "port": 8790, "lan": ["https://192.168.29.5:8790"], "public": "https://monday.ngrok.app", "tunnel": "online",
                        "why": "Across networks through ngrok (NGROK_AUTHTOKEN in .env).", "devices": 1, "fingerprint": "ab" * 32, "error": None}})
    win.show_page("phone")
    bridge.settle()
    ph = win.screens["phone"]
    check("phone: Core's state: on, the LAN and ngrok addresses, the certificate in groups of 4, the pairing button live",
          ph.switch.isChecked() and ph.addr.text() == "https://192.168.29.5:8790\nhttps://monday.ngrok.app (ngrok)"
          and ph.fp.text() == " ".join(["abab"] * 16) and ph.why.text().startswith("Across networks") and ph.pair.isEnabled()
          and ph.off_line.isHidden() and ph.note.isHidden(), f"{ph.addr.text()!r} / {ph.fp.text()}")
    core.answers["/api/remote/pairing"]["expires_at"] = time.time() + 108
    ph.pair.click()
    bridge.settle()
    check("phone: Show a pairing code posts it; the code big in two halves, with its countdown", ("POST", "/api/remote/pairing", {}) in core.calls
          and ph.code.text() == "4821 0937" and not ph.code.isHidden() and ph.left.text() == "Expires in 1:48", f"{ph.code.text()} / {ph.left.text()}")
    ph.expires_at = time.time() - 1
    ph._tick()
    check("phone: two minutes on, the code is gone", ph.left.text() == "Expired" and ph.code.isHidden() and not ph.timer.isActive())
    sws, rms = ph.devices.findChildren(K.Toggle), ph.devices.findChildren(Button)
    rows = [w.text() for w in ph.devices.findChildren(K.QLabel)]
    check("phone: the paired phone's row, its approvals switch and Revoke named after it; the revoked one greyed with no controls",
          [w.accessibleName() for w in sws] == ["Bhavy's Pixel may answer approvals: on or off"]
          and [b.accessibleName() for b in rms] == ["Revoke Bhavy's Pixel"] and "android 0.1.0 · last seen 2 min from 192.168.29.40" in rows
          and any(r.startswith("ios 0.1.0 · Revoked ") for r in rows), " | ".join(rows))
    sws[0].setChecked(False)  # the owner's click
    bridge.settle()
    check("phone: May answer approvals posts the device's capability", ("POST", "/api/remote/devices/3/capability",
                                                                         {"key": "approve", "value": False}) in core.calls)
    real_confirm, K.confirm = K.confirm, lambda *a, **kw: True  # (the owner says Revoke in the dialog)
    rms[0].click()
    K.confirm = real_confirm
    bridge.settle()
    check("phone: Revoke deletes the device, then the list is read again", ("DELETE", "/api/remote/devices/3", None) in core.calls
          and ("GET", "/api/remote/devices", None) in core.calls[-2:])
    core.answers["/api/remote"] = {"on": False, "port": 8790, "lan": [], "public": None, "tunnel": "not_configured", "devices": 1, "fingerprint": None,
                                   "why": "On the LAN only: no NGROK_AUTHTOKEN.", "error": "can't listen on 0.0.0.0:8790: [WinError 10048] the port is taken"}
    ph.refresh()
    bridge.settle()
    check("phone: off: Off and N/A for what Core didn't report, the pairing button disabled with why, Core's error in red",
          not ph.switch.isChecked() and ph.addr.text() == "Off" and ph.fp.text() == "N/A" and not ph.pair.isEnabled()
          and not ph.off_line.isHidden() and ph.code.isHidden() and not ph.note.isHidden() and "port is taken" in ph.note.text_label.text())
    ph.switch.setChecked(True)  # the owner's click
    bridge.settle()
    check("phone: the switch posts remote_access, then shows what Core reports (still off: the port)",
          ("POST", "/api/settings", {"remote_access": True}) in core.calls and not ph.switch.isChecked())
    win.show_page("settings")
    bridge.settle()
    se = win.screens["settings"]
    check("settings: the approval mode and its grants from Core", se.mode.value == "always" and se.grants.n == 1)
    se._pick_mode("auto")
    bridge.settle()
    check("settings: a mode change posts approval_mode", ("POST", "/api/settings", {"approval_mode": "auto"}) in core.calls)
    check("settings: Voice has the Custom voice button (a recording cloned on Fish Audio or here, 2026-10-21)",
          se.custom_voice.text() == "Custom voice…" and "30 seconds" in se.custom_voice.accessibleName())
    win.show_page("general")
    bridge.settle()
    ge = win.screens["general"]
    ge._pref("autoclose", 30)
    check("general: a notch choice kept in the window's store, checked by notch.prefs", win.store["pet"]["autoclose"] == 30)
    ge.p = {**ge.p, "desk": False}
    ge._pref("visible", False)
    check("general: the notch can't go while the pet isn't on the desktop (one of them stays)", win.store["pet"]["visible"] is True)
    ge.p = {**ge.p, "desk": True, "visible": False}
    ge._pref("desk", False)
    check("general: the pet put away with the notch off brings the notch back", win.store["pet"]["desk"] is False
          and win.store["pet"]["visible"] is True and ge.visible.isChecked())
    # Appearance & notch UI (docs/adr/0011): three looks, the one in use marked; Use and Reset reach the notch, or the store without one
    from . import island as I
    lc = ge.looks_card
    check("general: Appearance & notch UI lists the three looks, Classic in use", list(lc.looks) == ["classic", "island", "limits"]
          and lc.looks["classic"].text() == "In use" and not lc.looks["classic"].isEnabled() and lc.looks["island"].isEnabled())
    check("settings: the same card first on the main Settings page (the owner: \"how can i change the ui?\")",
          isinstance(getattr(win.screens["settings"], "looks_card", None), LooksCard)
          and win.screens["settings"].page.lay.indexOf(win.screens["settings"].looks_card) == 1)
    asked = []
    keep_notch = win.notch
    win.notch = type("N", (), {"set_mode": lambda self, m: (asked.append(("use", m)), win.store.__setitem__(I.UI_KEY, {"mode": m})),
                               "reset_mode": lambda self, m: asked.append(("reset", m))})()
    lc.looks["island"].click()
    check("general: Use asks the notch to switch, and marks the new look in use", asked == [("use", "island")]
          and lc.looks["island"].text() == "In use" and lc.looks["classic"].text() == "Use" and "Floating island" in lc.note.text_label.text())
    lc.reset("limits")
    check("general: Reset asks the notch for that look only", asked[-1] == ("reset", "limits"))
    win.notch = None
    lc.use("limits")
    lc.reset("island")
    check("general: with no notch, the look and a reset are kept for the next start", win.store[I.UI_KEY]["mode"] == "limits"
          and win.store[I.UI_KEY]["island"]["layout"] == [I.item(*t) for t in I.ISLAND_LAYOUT])
    win.notch = keep_notch
    win.store.pop(I.UI_KEY, None)
    lc.fill()
    win.show_page("stats")
    bridge.settle()
    win.screens["stats"].findChildren(Segmented)[0].setFocus(Qt.FocusReason.TabFocusReason)
    win.show_page("pills")
    bridge.settle()
    check("a page shown (by a click, a key or \"open your … page\") lights no control's focus ring until Tab is pressed",
          not any(getattr(w, "_kb", False) and w.hasFocus() for w in win.findChildren(QWidget)))
    pi = win.screens["pills"]
    check("active pills: no command-line agents offered or picked (the owner: \"don't use the CLIs\"), Monday's pick said as such",
          pi.chosen() == ([], False) and "Monday's pick" in pi.slots.text() and not any(w.text() in ("Codex", "Kilo Code")
                                                                                       for w in pi.findChildren(K.QLabel)), pi.slots.text())
    pi._flip("kiro", True, K.Toggle("kiro"))
    check("active pills: a pill turned on is kept", win.store["pet"]["pills"] == ["kiro"])
    from .notch import PILLS_MAX
    full = [chr(97 + i) for i in range(PILLS_MAX)]  # eight since 2026-10-20 night (the owner: "we can use multiple")
    win.store["pet"]["pills"] = list(full)
    sw = K.Toggle("codex", True)
    pi._flip("codex", True, sw)
    check("active pills: one past the cap is refused, with why, and its switch put back", win.store["pet"]["pills"] == full
          and not sw.isChecked() and f"{PILLS_MAX}/{PILLS_MAX}" in pi.note.text_label.text())
    pi._save(None)
    check("active pills: Let Monday choose goes back to its pick", win.store["pet"]["pills"] is None and pi.chosen()[1] is False)
    win.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "w1", "agent": "Claude Code", "what": "run a command", "detail": "npm test"}}})
    bar = win.ask_bar
    seen = [b.text() for b in bar.findChildren(Button) if not b.isHidden()]
    check("an agent's ask shows in the window too, above any page (the owner, 2026-10-20)", not bar.isHidden() and "run a command" in
          bar.title.text() and {"Allow", "Deny"} <= set(seen), ", ".join(seen))
    core.answers["/api/agents/ask/"] = {"ok": True}
    next(b for b in bar.findChildren(Button) if b.text() == "Allow" and not b.isHidden()).click()
    bridge.settle()
    win.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "w1", "decision": "allow"}})
    check("Allow from the window posts it; answered anywhere (the notch too), it leaves the window", ("POST", "/api/agents/ask/w1",
          {"decision": "allow"}) in core.calls and bar.isHidden())
    win.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "w2", "agent": "Claude Code", "what": "ask you", "questions": [
        {"question": "Reload now?", "header": "", "multi": False, "options": [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]}]}}})
    next(b for b in bar.findChildren(Button) if b.text() == "Yes" and not b.isHidden()).click()
    bridge.settle()
    check("Claude Code's question in the window: its options, the pick sent as its answer", ("POST", "/api/agents/ask/w2",
          {"decision": "allow", "answers": {"Reload now?": "Yes"}}) in core.calls)
    win.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "w2"}})
    win.on_event({"type": "AGENT_ASK", "data": {"ask": {"id": "w3", "agent": "Claude Code", "what": "ask you", "questions": [
        {"question": "Reload now?", "header": "", "multi": False, "options": [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]}]}}})
    own = next((f for f in bar.findChildren(K.QLineEdit) if not f.isHidden()), None)
    if own is not None:
        own.setText(" after the build ")
        own.returnPressed.emit()
    bridge.settle()
    check("“Other” in the window too: the owner's own words are the answer (2026-10-20)", own is not None and ("POST", "/api/agents/ask/w3",
          {"decision": "allow", "answers": {"Reload now?": "after the build"}}) in core.calls)
    win.on_event({"type": "AGENT_ASK_DONE", "data": {"id": "w3"}})
    win.show_page("shortcuts")
    bridge.settle()
    sc = win.screens["shortcuts"]
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    sc._capture("mic")
    sc.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
    check("shortcuts: new keys pressed are kept (Ctrl+Alt+K)", win.store["keys"]["mic"] == {"keys": "Ctrl+Alt+K", "on": True})
    sc._capture("window")
    sc.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
    check("shortcuts: keys another shortcut has are refused, with why", win.store["keys"]["window"]["keys"] == sc.hk.defaults()["window"]["keys"]
          and "already" in sc.note.text_label.text())  # (the default depends on this keyboard: Ctrl+Alt+O, or W where O types ō)
    sc._capture("window")
    sc.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier))
    check("shortcuts: a key alone is refused", "alone" in sc.note.text_label.text() and sc.capturing is None)
    from .native import todo_rows
    now = time.time()
    side = {"groups": [{"id": 1, "name": "Home"}], "todos": [{"id": 3, "title": "Pay rent", "money": True, "created_at": now}],
            "meta": [{"kind": "todo", "ref": "3", "group_id": 1, "pinned_at": now}], "tasks": [], "deleted": []}
    core.answers.update({"/api/side": side, "/api/tasks?": {"tasks": [{"id": "T1", "title": "Open Spotify", "status": "RUNNING",
                                                                         "created_at": now}]},
                         "/api/todos/judge": {"who": "owner", "money": True, "by": "jev"}, "/api/todos": side})
    win.show_page("tasks")
    bridge.settle()
    ts = win.screens["tasks"]
    check("tasks: Monday's task and the owner's to-do, the groups to show", ts.task_card.n == 1 and ts.todo_card.n == 1
          and ts.filter.count() == 3, ts.count.text())
    ts.box.setText("Pay the electricity bill")
    ts._judge()
    bridge.settle()
    check("tasks: Jev's read says money makes Monday wait", "money" in ts.read.text(), ts.read.text())
    ts.apply("add")
    bridge.settle()
    check("tasks: a to-do goes to POST /api/todos with the money read", ("POST", "/api/todos", {"title": "Pay the electricity bill",
                                                                                              "money": True, "group_id": None}) in core.calls)
    ts.apply("done", todo_rows(side)[0], done=True)
    bridge.settle()
    check("tasks: a tick posts done", ("POST", "/api/todos/3", {"done": True}) in core.calls)
    ts.on_event({"type": "TASK_COMPLETED", "data": {"task": {"id": "T1", "title": "Open Spotify", "status": "COMPLETED", "created_at": now}}})
    check("tasks: a task's end shows at once", ts.task_rows.itemAt(0).widget().r["status"] == "completed")
    view = {"running": True, "control": "monday", "url": "https://example.com/", "title": "Example", "tabs": [
        {"i": 1, "title": "Example", "url": "https://example.com/", "active": True}, {"i": 2, "title": "Docs", "url": "https://d/", "active": False}]}
    core.answers.update({"/api/browser/control": {**view, "control": "owner"}, "/api/browser": view})
    win.show_page("browser")
    bridge.settle()
    br = win.screens["browser"]
    check("browser: Core's state, Monday driving: Take control on, Resume off", "Monday has control" in br.state.accessibleName()
          and br.take.isEnabled() and not br.resume.isEnabled() and br.tabs_row.count() == 3)
    br.input({"type": "click", "x": 0.5, "y": 0.5})
    check("browser: a click before Take control stays here, with why", "Take control first" in br.state.accessibleName()
          and not any(p == "/api/browser/input" for _m, p, _b in core.calls))
    br.control("take")
    bridge.settle()
    br.input({"type": "click", "x": 0.5, "y": 0.5})
    bridge.settle()
    check("browser: Take control, then the owner's click reaches the page", br.owner() and br.resume.isEnabled()
          and ("POST", "/api/browser/input", {"type": "click", "x": 0.5, "y": 0.5}) in core.calls)
    br.leave()
    check("browser: leaving the page stops the live picture", br.streaming == 0)
    core.answers["/api/activity"] = {"items": [
        {"id": 9, "ts": now, "type": "TASK_COMPLETED", "what": "Task completed", "detail": "Open Chrome", "tone": "done"},
        {"id": 8, "ts": now, "type": "PERMISSION_GRANTED", "what": "Approved", "detail": "Run a command", "tone": "done"}]}
    win.show_page("activity")
    bridge.settle()
    ac = win.screens["activity"]
    check("activity: Core's items, newest first", ac.rows.count() == 2 and not ac.more.isVisibleTo(ac))
    ac._kind("PERMISSION_")
    check("activity: one kind at a time (approvals)", ac.rows.count() == 1)
    core.answers.update({"/api/memory": {"items": [{"id": "m1", "at": now, "kind": "preference", "text": "Likes dark mode",
                                                    "note": "Preferences", "stale": True}]},
                         "/api/projects": {"projects": [{"id": 2, "name": "shop", "path": "E:/shop", "aliases": "store"}]},
                         "/api/learned": {"apps": {"spotty": {"app": "spotify", "why": "you said so", "uses": 3}}, "recipes": []}})
    win.show_page("library")
    bridge.settle()
    lb = win.screens["library"]
    check("library: remembered, projects and learned from Core", lb.mem_rows.n == 1 and lb.proj_rows.n == 1 and lb.learn_rows.n == 1,
          lb.hero.words_label.text())
    check("library: an old line says it may be out of date", "may be out of date" in lb.memory[0][3])
    lb._forget("m1")
    bridge.settle()
    check("library: Forget posts it", ("POST", "/api/memory/m1/forget", {}) in core.calls)
    rec = {"id": "R1", "name": "Open Notepad", "workflow": {"steps": [
        {"index": 1, "plan": {"label": "Open Notepad"}, "expect": {"desc": "Notepad's window"}, "essential": True},
        {"index": 2, "plan": {"label": "Type hello"}, "expect": {"desc": "the text in it"}, "essential": True}]},
        "runs": [{"id": "U1", "started_at": now, "status": "PASSED", "passed": 1, "failed": 0, "duration_ms": 4100, "result": {"results": [
            {"label": "Open Notepad", "status": "pass", "expected": "Notepad's window", "actual": "Notepad"}]}}]}
    core.answers.update({"/api/recordings/R1": rec, "/api/recordings": {"recordings": [
        {"id": "R1", "name": "Open Notepad", "target_app": "notepad", "steps": 2, "last_status": "PASSED"}], "recording": {"active": False}}})
    win.show_page("tests")
    bridge.settle()
    te = win.screens["tests"]
    te.list_rows.itemAt(0).widget().click()
    bridge.settle()
    check("tests: a workflow's steps with ticks, its runs, the newest run's checks with what was seen", te.list_rows.count() == 1
          and len(te.ticks) == 2 and te.run_rows.count() == 1 and te.check_rows.n == 1)
    te.ticks[1].setChecked(False)
    te.apply("ticks")
    bridge.settle()
    check("tests: Save ticks sends the steps, the unticked one left out", any(
        c[0] == "PATCH" and c[1] == "/api/recordings/R1" and [s["essential"] for s in c[2]["workflow"]["steps"]] == [True, False]
        for c in core.calls))
    te.on_event({"type": "TEST_RESULT", "data": {"name": "Open Notepad", "status": "FAILED", "passed": 1, "failed": 1}})
    check("tests: a replay's verdict is said", "1 of 2 checks passed" in te.note.text_label.text())
    start = now - (now % 86400) - 2 * 86400
    core.answers["/api/routines"] = {
        "routines": [{"id": "T7", "name": "Morning brief", "bot": "Scout", "prompt": "Summarize my inbox", "enabled": True, "minutes": 30,
                      "schedule": {"type": "daily", "time": "08:30", "weekdays": [1, 2, 3, 4, 5]}, "when": "Weekdays at 08:30",
                      "next_at": now + 3600, "bot_missing": False}],
        "runs": [{"id": "x1", "name": "Morning brief", "bot": "Scout", "status": "completed", "scheduled_for": now - 3600,
                  "output": "Three emails need you.", "minutes": 30}],
        "ahead": [{"at": now + 3600, "name": "Morning brief", "minutes": 30}], "start": start, "end": start + 7 * 86400, "bots": ["Scout", "Pixel"]}
    win.show_page("routines")
    bridge.settle()
    ro = win.screens["routines"]
    check("routines: the list, the bots, this week's receipt", ro.list_rows.count() == 1 and ro.r_bot.count() == 2 and ro.receipts.count() == 1)
    ro.list_rows.itemAt(0).widget().click()
    check("routines: choosing one fills its editor", ro.r_name.text() == "Morning brief" and ro.r_time.text() == "08:30"
          and ro.r_days[1].isChecked() and not ro.r_days[0].isChecked())
    ro.r_time.setText("8.30")
    ro.apply("save")
    check("routines: a time it can't read is refused here, with how to write it", "08:30" in ro.note.text_label.text())
    ro.r_time.setText("09:15")
    ro.apply("save")
    bridge.settle()
    check("routines: Save posts it as Core's RoutineIn", any(c[0] == "POST" and c[1] == "/api/routines/T7" and c[2]["schedule"] == {
        "type": "daily", "time": "09:15", "weekdays": [1, 2, 3, 4, 5]} for c in core.calls))
    core.answers["/api/vision"] = {"available": True, "camera": "off", "detail": "YOLOX-S and MobileNetV2", "known": [
        {"label": "my mug", "views": 3, "at": now, "from": "camera"}]}
    win.show_page("camera")
    bridge.settle()
    ca = win.screens["camera"]
    check("camera: Core's state and what it knows by sight", "Camera off" in ca.state.accessibleName() and ca.known_rows.n == 1
          and ca.switch.isEnabled())
    ca.camera(True)
    end = time.monotonic() + 5
    while ca.on and time.monotonic() < end:  # the stub has no stream: it ends at once
        app.processEvents()
        time.sleep(0.02)
    check("camera: a stream that ends turns the camera off, says why, and isn't opened again", not ca.on
          and "stopped" in ca.state.accessibleName(), ca.state.accessibleName())
    ca._ask()
    bridge.settle()
    check("camera: What is this? asks Monday", any(c[1] == "/api/command" and (c[2] or {}).get("text") == "what is this" for c in core.calls))
    scout = {"name": "Scout", "role": "researcher", "instructions": "Find sources.", "agent": "auto", "pinned": True, "unread": 2,
             "look": {"kind": "maus", "body": "#377FE6", "accent": "#FFFFFF"}, "status": {"state": "working", "last": {"title": "Find GPUs"}}}
    pixel = {"name": "Pixel", "role": "designer", "agent": "auto", "hidden": True, "status": {}}
    room = {"id": "r1", "name": "Launch", "members": ["Scout", "Pixel"], "responder": "lead", "lead": "Scout", "bulletin": "Be brief.",
            "line": "Scout: ship it"}
    core.answers.update({"/api/bots/Scout/": {"bot": scout}, "/api/bots": {
        "bots": [scout, pixel], "agents": {"auto": "Monday decides", "codex": "Codex"}, "suggested": "Nova",
        "templates": {"scout": {"name": "Scout"}, "pixel": {"name": "Pixel"}, "forge": {"name": "Forge", "role": "engineer", "agent": "auto"}}},
        "/api/rooms/r1/messages": {"messages": [{"id": 50, "role": "user", "kind": "text", "text": "what first?", "meta": {"chatroom": "r1"}},
                                                {"id": 51, "role": "monday", "kind": "text", "text": "**Ship** the roster.",
                                                 "meta": {"chatroom": "r1", "bot": "Scout"}}]},
        "/api/rooms": {"rooms": [room]}})
    win.show_page("bots")
    bridge.settle()
    bo = win.screens["bots"]
    rows = [bo.roster.itemAt(i).widget() for i in range(bo.roster.count())]
    check("bots: rooms first, then the bots; a hidden one folded away", isinstance(rows[0], BotRow) and rows[0].accessibleName().startswith("Room Launch")
          and sum(isinstance(w, BotRow) for w in rows) == 2 and any("hidden bots (1)" in w.text() for w in rows if isinstance(w, Button)))
    check("bots: a bot's row says its state, unread and pin, from Core", "Working · Find GPUs, 2 unread, pinned" in rows[1].accessibleName(),
          rows[1].accessibleName())
    rows[1].click()
    check("bots: choosing one fills its editor", bo.b_name.text() == "Scout" and bo.b_role.text() == "researcher"
          and bo.b_buttons["pin"].text() == "Unpin" and bo.editor.isVisibleTo(bo))
    bo.b_role.setText("lead researcher")
    bo.apply("save")
    bridge.settle()
    check("bots: Save sends it as Core's BotIn (no welcome for one that exists)", any(
        c[0] == "POST" and c[1] == "/api/bots" and c[2].get("role") == "lead researcher" and "welcome" not in c[2] for c in core.calls))
    bo.apply("templates")
    bridge.settle()
    check("bots: the starting three adds only the missing one", [c[2]["name"] for c in core.calls if c[1] == "/api/bots" and c[0] == "POST"
                                                                   and c[2].get("role") == "engineer"] == ["Forge"])
    bo._chosen("room:r1")
    bridge.settle()
    check("bots: a room shows its conversation and who answers", bo.room_card.isVisibleTo(bo) and not bo.editor.isVisibleTo(bo)
          and bo.r_log.count() == 3 and bo.r_bulletin.text() == "Instructions: Be brief." and bo.r_who.currentData() == "lead")
    bo.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 52, "role": "monday", "kind": "text", "text": "Done.",
                                                                 "meta": {"chatroom": "r1", "bot": "Pixel"}}}})
    bo.on_event({"type": "MESSAGE_CREATED", "data": {"message": {"id": 52, "role": "monday", "kind": "text", "text": "Done.",
                                                                 "meta": {"chatroom": "r1", "bot": "Pixel"}}}})
    check("bots: a room's new line shows once", bo.r_log.count() == 4)
    bo.r_box.setText("@Pixel make the icon")
    bo._say()
    bridge.settle()
    check("bots: saying something in a room posts it", ("POST", "/api/rooms/r1/say", {"text": "@Pixel make the icon"}) in core.calls)
    ch = win.screens["chat"]
    win._got_voice({"settings": {"interaction_mode": "debug"}}, None)
    check("reflex: shown in debug mode", ch.reflex.isVisibleTo(ch))
    win.rx_want = (7, "open notepad", False, True)
    win._reflex_draw(7, {"status": "ok", "numbers": {"complete": 0.9, "addressed": 0.3, "stakes": 0.05}, "gates": {
        "complete": 0.6, "addressed": 0.5, "stakes": 0.5}, "model": "jev", "latency_ms": 120, "cost_usd": 0.000012}, None)
    check("reflex: Core's numbers against Core's gates, its model and cost", len(ch.reflex.view["rows"]) == 3
          and "Finished sentence: 90%, gate 60%, finished" in ch.reflex.accessibleDescription() and ch.reflex.view["pill"].startswith("jev"),
          ch.reflex.accessibleDescription())
    win._reflex_draw(6, {"status": "ok", "numbers": {"complete": 0.1}}, None)
    check("reflex: a read about a sentence already moved on from is dropped", ch.reflex.view["rows"][0][1] == 0.9)
    win._got_voice({"settings": {"interaction_mode": "pure_agent"}}, None)
    check("reflex: hidden outside debug mode, and nothing is read", not ch.reflex.isVisibleTo(ch) and win._reflex_feed("open notepad", False) is None)
    gen = win.screens["general"]
    gen.ensure()
    gen._got_update({"state": "up_to_date", "current": "0.3.0", "checked_at": time.mktime((2026, 10, 29, 11, 10, 0, 0, 0, -1)),
                     "detail": "Monday 0.3.0 is the newest.", "channel": "E:/monday/releases", "action": None}, None)
    up, up_words = (gen.upd_state.text(), gen.upd_go.isVisibleTo(gen), gen.upd_folder.text()), gen.upd_words.text()
    gen._got_update({"state": "available", "current": "0.2.0", "action": "install", "detail": "Monday 0.3.0 is ready to install.",
                     "latest": {"version": "0.3.0", "notes": "Updates in the notch."}}, None)
    ready = (gen.upd_state.text(), gen.upd_words.text(), gen.upd_go.text(), gen.upd_go.isVisibleTo(gen))
    gen._got_update({"state": "refused", "current": "0.2.0", "action": None,
                     "detail": "The update in E:/x isn't signed with your update key, so it isn't offered."}, None)
    refused = (gen.upd_state.text(), gen.upd_words.text(), gen.upd_go.isVisibleTo(gen))
    gen._got_update(None, "Monday Core is unreachable.")
    down = (gen.upd_state.text(), gen.upd_words.text())
    check("Settings → General → Updates: up to date with when it looked; a newer one with its note and Update; a refused signature and an "
          "unreachable Core said as they are, never \"up to date\" (docs/adr/0010)",
          up == ("Monday 0.3.0 · up to date · checked 11:10", False, "E:/monday/releases")
          and ready == ("Monday 0.3.0 is ready", "Updates in the notch.", "Update", True) and not up_words
          and refused[0] == "Couldn't check" and "isn't signed with your update key" in refused[1] and not refused[2]
          and down == ("Couldn't check", "Monday Core is unreachable."), f"{up} | {ready} | {refused} | {down}")
    check("every control named", not named(win), ", ".join(named(win)))
    check("greeting by the hour", greeting(time.mktime((2026, 10, 19, 9, 0, 0, 0, 0, -1))) == "Good morning"
          and greeting(time.mktime((2026, 10, 19, 20, 0, 0, 0, 0, -1))) == "Good evening")
    check("durations as the cards say them", (duration(0), duration(42), duration(95), duration(4500)) == ("0 min", "42 s", "2 min", "1 h 15 min"))
    # the app's own supervisor starting Core again (a restart, self-repair's exit 75, a crash): "restarting", never "offline" (2026-10-23)
    win.core_phase = "restarting"
    win._online("OFFLINE")
    restarting = (win.rail.pill.dot, win.rail.pill.words)
    win.core_phase = "running"
    win._online("OFFLINE")
    check("Core being started again by this app: the rail says restarting, not offline; offline when it is really gone",
          restarting == ("wait", "Monday Core is restarting…") and win.rail.pill.words == "Monday Core is offline", f"{restarting} / {win.rail.pill.words}")
    win.core_phase = None
    bridge.stop()
    app.processEvents()
    return ok


def main():
    app = make_app(sys.argv)
    from . import desktop
    store = desktop.load_state() if hasattr(desktop, "load_state") else {}
    bridge = Bridge()
    win = Window(bridge, store)
    win.show()
    win.start()
    code = app.exec()
    bridge.stop()
    return code


if __name__ == "__main__":
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else None
    if "--check" in sys.argv:
        sys.exit(0 if _check() else 1)
    elif "--shot" in sys.argv:
        for path in shots(sys.argv[sys.argv.index("--shot") + 1], f"http://127.0.0.1:{port}" if port else None):
            print(path)
    else:
        sys.exit(main())
