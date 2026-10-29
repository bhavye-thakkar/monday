"""Monday as a desktop app (the owner, 2026-10-08: "firstly a desktop app"; 2026-10-09: "only a desktop application", native; 2026-10-19:
on Qt, in the look of their Coucou recording, on Windows, macOS and Linux: docs/adr/0002-qt-all-platforms.md).

    pythonw -m core.desktop            the app: a tray icon that runs Monday Core hidden, Monday's window and its notch (launch_qt).
                                       Launched again, it only brings the window back (one Monday, one ear); --quit quits it. On Windows
                                       it runs as Monday.exe (data/runtime: Task Manager's one "Monday", monday_exe).
    python -m core.desktop --install   the icon, and the Start menu and desktop shortcuts ("Monday"; Windows)
    python -m core.desktop --check     self-check: no tray, no window, no Core

No HTML (the owner, 2026-10-15: "fully desktop app only, no html at all"). The tray supervises Core the way "Start Monday.cmd" does,
without a console: restarted after a crash (2 s), at once after a self-repair (exit 75), given up after MAX_CRASHES quick crashes
(the tray then says so). Core's output goes to data/logs/core.log, the app's own errors to data/logs/desktop.log.
The old WinForms window (core/native.py, core/notch.py) was retired on 2026-10-19 (the owner: "yes, retire it").
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("MONDAY_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
LOG = ROOT / "data" / "logs" / "core.log"
ICON = ROOT / "static" / "monday.ico"
ICON_PNG = ROOT / "static" / "monday-256.png"   # the large icon (the shortcuts', notifications')
APP_ID = "Monday.Desktop"                       # the taskbar and notification identity, shared with the shortcuts
RESTART_EXIT = 75                               # core/monday.py RESTART_EXIT: a self-repair or "restart yourself"
RESTART_DELAY_S = 2
MAX_CRASHES, CRASH_WINDOW_S = 5, 120            # this many crashes this close together: stop and say so
CORE_WAIT_S = 90                                # a cold start (Ollama, the screen driver) answers well within this
REFRESH_WAIT_S = 600                            # a Core running older code waits this long at most for its work to end before it restarts
REFRESH_POLL_S = 3                              # how often a refresh asks Core again
STARTUP_AGAIN_S = 60                            # clicks within this of the last start-up check don't make another (nor another notification)
RESTART_WAIT_S = 90                             # a restart's new launch (--after) waits this long at most for the Monday before it to end
STARTUP_WAIT_S = 200                          # the start-up check's answer (Ollama's first start after a reboot alone can take 2 min)
STARTUP_SAID = "startup_said"                   # data/desktop.json: the start-up check's last verdict shown, [title, words] (startup_said)
LOG_MAX = 5_000_000
NO_WINDOW = 0x08000000                          # CREATE_NO_WINDOW
NEW_GROUP = 0x00000200                          # CREATE_NEW_PROCESS_GROUP: Core ignores a Ctrl+C a child sends to its console
RUNTIME = ROOT / "data" / "runtime"             # Windows: Monday's own copies of Python's programs, one folder per Python version (monday_exe)
APP_EXE = "Monday.exe"                          # every program Monday keeps running: Task Manager groups a process tree by file name
EXIT_MEANING = {0: "stopped on purpose", 1: "a Python error (the lines above say which)", RESTART_EXIT: "restarting to load a change",
                0xC0000005: "a native crash (access violation)", 0xC000013A: "a Ctrl+C or console close reached it",
                0xC0000409: "a native crash (fail-fast)", 0xC00000FD: "a stack overflow", 0xC0000374: "heap corruption",
                0x40010004: "Windows shut down, restarted or signed out", 0xC0000142: "Windows couldn't start it (often: low on memory)",
                0xFFFFFFFF: "killed from outside (end task, an antivirus, or taskkill)"}


def exit_meaning(code):
    c = code & 0xFFFFFFFF if code is not None else None
    return EXIT_MEANING.get(c, "killed from outside, or a crash Windows didn't name") if c is not None else "unknown"


def hello(timeout=1.5):
    """Core's handshake (GET /api/hello: its name, process, app folder, code stamp, busy, readiness), or None when nothing answers."""
    try:
        h = httpx.get(f"{BASE}/api/hello", timeout=timeout).json()
        return h if isinstance(h, dict) else None
    except (httpx.HTTPError, ValueError):
        return None


def core_answers(timeout=1.5):
    """Monday's name when Core answers, else None."""
    h = hello(timeout)
    return (h.get("name") or "Monday") if h is not None else None


def core_fit(h, root=ROOT):
    """What a Core that already answers is to this app (2026-10-23, the owner: Monday starts its Core itself, without a Claude Code session to
    restart it): "foreign" (another app folder's Core holds the port: never used as ours), "stale" (the code it runs changed on disk since it
    started, as it says itself: restarted once it is idle), or "ok". A Core from before the handshake says neither: "ok", as before."""
    if h.get("root") and os.path.normcase(os.path.realpath(h["root"])) != os.path.normcase(os.path.realpath(root)):
        return "foreign"
    return "stale" if h.get("stale") is True else "ok"


def port_taken():
    """Is something listening on Core's port: a Core answering, busy, or on its way out? (Refused only when nothing is.)"""
    import socket
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        return True
    except OSError:
        return False


def wait_core(timeout=CORE_WAIT_S, stop=lambda: False):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not stop():
        name = core_answers()
        if name:
            return name
        time.sleep(0.5)
    return None


STATE = Path(os.environ.get("MONDAY_DESKTOP_STATE") or ROOT / "data" / "desktop.json")  # the window's and the notch's places, the
# pet, the keys (no secret in it); MONDAY_DESKTOP_STATE: a test app's own, so a check never writes the owner's


def load_state(path=None):
    """data/desktop.json as a dict ({} when missing or not an object): the window's place (qrect), the notch's (notch_at), the pet, the
    keys; each part checks its own values when it reads them. Every key is kept, so one part saving never drops another's."""
    try:
        st = json.loads(Path(path or STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    return st if isinstance(st, dict) else {}


def save_state(st, path=None):
    try:
        Path(path or STATE).parent.mkdir(parents=True, exist_ok=True)
        Path(path or STATE).write_text(json.dumps(st), encoding="utf-8")
    except OSError:  # the choice is only remembered, never needed
        pass


def rotate(log, limit=LOG_MAX):
    """Keep one older log: core.log.1."""
    if log.exists() and log.stat().st_size > limit:
        log.replace(log.with_name(log.name + ".1"))


def too_many(crashes, now):
    """The crash times still inside the window, and whether there are enough of them to stop."""
    recent = [t for t in crashes if now - t < CRASH_WINDOW_S]
    return recent, len(recent) >= MAX_CRASHES


def ask_core(path, **body):
    try:
        httpx.post(f"{BASE}{path}", json=body, timeout=5, headers={"Origin": BASE})
        return True
    except httpx.HTTPError:
        return False


# -- the supervisor ------------------------------------------------------------------------------------------------
class Supervisor:
    """Runs `python -m core` hidden, restarts it, and tells the tray what is true right now."""

    def __init__(self, on_state):
        self.on_state, self.proc, self.quitting, self.state, self.detail = on_state, None, False, "starting", ""
        self.thread = None

    def set(self, state, detail=""):
        self.state, self.detail = state, detail
        self.on_state(state, detail)

    def start(self):
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        crashes = []
        # Windows: Python's python.exe as Monday.exe (monday_exe), else python.exe itself (its output goes to the log); macOS / Linux:
        # this Python (2026-10-19)
        python = Path(sys.executable)
        if sys.platform == "win32":
            python = monday_exe(gui=False) or Path(sys.base_exec_prefix) / "python.exe"
        spawn = {"creationflags": NO_WINDOW | NEW_GROUP} if sys.platform == "win32" else {"start_new_session": True}
        while not self.quitting:
            if port_taken():  # a Core holds the port (started elsewhere: a terminal, the window before this one): watch it, start ours when
                # it's gone. The port, not one answer: on 2026-10-21 a Core slow to answer for a moment was taken for gone, a second one
                # started, found it running and left (exit 0), which read as "stopped": the tray said so and this watch ended, Core running on
                h = hello(timeout=5)
                if h is not None:
                    self.adopt(h)
                time.sleep(3)
                continue
            self.set("starting")
            LOG.parent.mkdir(parents=True, exist_ok=True)
            rotate(LOG)
            with open(LOG, "ab") as out:
                out.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} Monday Core starting\n".encode())
                out.flush()
                self.proc = subprocess.Popen([str(python), "-m", "core"], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, env={**os.environ, "MONDAY_SUPERVISED": "1", "PYTHONIOENCODING": "utf-8"},
                                             **spawn)
            threading.Thread(target=self._announce, args=(self.proc,), daemon=True).start()
            code = self.proc.wait()
            with open(LOG, "ab") as out:  # every exit, with its code: three silent deaths on 2026-10-09 left nothing to go on
                out.write(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} Monday Core exited: code {code} "
                          f"(0x{(code or 0) & 0xFFFFFFFF:08X}, {exit_meaning(code)})\n".encode())
            if self.quitting:
                break
            if code == RESTART_EXIT:
                self.set("restarting")
                continue
            if code == 0 and port_taken():  # ours found another Core running and left it be: watch that one
                continue
            if code == 0:  # Core stopped on purpose (it was told to quit)
                self.set("stopped", "Monday Core stopped")
                return
            crashes, stop = too_many(crashes + [time.time()], time.time())
            if stop:
                self.set("failed", f"Monday Core stopped {len(crashes)} times in a row (exit code {code}). The log says why.")
                return
            self.set("restarting", f"Monday Core stopped unexpectedly (exit code {code}); starting it again")
            time.sleep(RESTART_DELAY_S)

    def adopt(self, h):
        """A Core this app didn't start answers on the port: used when it is this app's; another app folder's is said, never used."""
        if self.state == "restarting" and core_fit(h) == "stale":  # refresh asked it to restart: on its way out, not "running" (2026-10-29: the
            return                                                # watch's next hello said "running", and a window call said "unreachable")
        said = (("failed", f"Port {PORT} is held by another Monday's Core ({h.get('root')}): quit that one to use this Monday.")
                if core_fit(h) == "foreign" else ("running", "started outside the app"))
        if said != (self.state, self.detail):
            self.set(*said)

    def refresh(self, then=None):
        """The owner launched Monday (its start, or a second launch handing over to this one): a Core running older code than is on disk
        restarts to load it, once it is idle (a task, a chat or a plan is never cut short; REFRESH_WAIT_S at most), whether this app
        started it (exit 75: the loop above starts it again) or found it running (it exits, the port frees, the loop starts this app's).
        then(): called once Core answers with the code on disk (the start-up check, launch_qt); never for another app folder's Core."""
        def go():
            end, asked = time.monotonic() + REFRESH_WAIT_S, False
            while time.monotonic() < end and not self.quitting:
                h = hello(timeout=5)
                fit = core_fit(h) if h is not None else None
                if fit in ("ok", "foreign"):
                    if then and fit == "ok":
                        then()
                    return
                if fit == "stale" and not asked and not h.get("busy"):
                    # "restarting" said first: the window holds its calls before Core goes (2026-10-29: one GET /api/status said
                    # "unreachable" in the moment between the shutdown and the word), then the shutdown; then this waits for the new Core
                    self.set("restarting", "Monday Core was running older code: restarting it to load the newest")
                    asked = ask_core("/api/shutdown", restart=True)
                    if not asked:
                        self.set("running", "older code: restarts once it's idle")
                elif fit == "stale" and not asked and self.detail != "older code: restarts once it's idle":
                    self.set("running", "older code: restarts once it's idle")
                time.sleep(REFRESH_POLL_S)
        t = threading.Thread(target=go, daemon=True)
        t.start()
        return t

    def _announce(self, proc):
        if wait_core(stop=lambda: proc.poll() is not None or self.quitting):
            self.set("running")

    def stop(self):
        """Quit: Core shuts down cleanly (its tasks, its Chrome); killed only if it doesn't within 15 s."""
        self.quitting = True
        p = self.proc
        alive = p is not None and p.poll() is None
        if alive or core_answers():  # our own Core asked even when slow to answer a hello (2026-10-26: unasked, it was killed after 15 s)
            ask_core("/api/shutdown")
        if alive:
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()


# -- the tray ------------------------------------------------------------------------------------------------------
TIPS = {"starting": "Monday · starting…", "running": "Monday · running", "restarting": "Monday · restarting…",
        "stopped": "Monday · stopped", "failed": "Monday · stopped: Core keeps crashing"}


def ready_words(r):
    """Core's readiness (Monday.readiness, with GET /api/hello and each COMPONENTS event) as the tray says it: "ready", "ready, offline:
    this PC's models only", "starting (checking its services)", "degraded: Ollama offline"."""
    state, missing = (r or {}).get("state") or "starting", ", ".join((r or {}).get("missing") or [])
    words = {"ready": "ready", "starting": f"starting ({missing or 'checking its services'})", "degraded": f"degraded: {missing}"}.get(state, state)
    return words + (", offline: this PC's models only" if (r or {}).get("internet") == "offline" else "")


def startup_said(d, err, toast, path=None):
    """The start-up check's verdict (POST /api/startup -> Monday.startup_words: title, words) in a notification only when it differs from
    the last one shown, kept in the desktop store (STARTUP_SAID): "Monday is ready" once, an unchanged "needs you" never again (2026-10-27:
    the owner, signed in to the Claude app and not its CLI, read "Claude Code: run `claude`…" at every start, 35 times over 28 starts).
    A check that failed (err) goes to the log only. -> True when shown."""
    if err or not (d or {}).get("title"):
        print(f"start-up check: {err or d}", file=sys.stderr)
        return False
    said, st = [d.get("title"), d.get("words")], load_state(path)
    if st.get(STARTUP_SAID) == said:
        print(time.strftime("%H:%M:%S"), f"start-up check unchanged, not shown again: {said[0]}", flush=True)
        return False
    save_state({**st, STARTUP_SAID: said}, path)
    toast(*said)
    return True



# -- the app on Qt: Windows, macOS, Linux ----------------------------------------------------------------------------
def windowless_log():
    """pythonw has no console (stdout and stderr are None): the app's own errors, a Qt slot's traceback and a native crash's stack, go to
    data/logs/desktop.log instead of nowhere."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    import faulthandler
    path = LOG.with_name("desktop.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate(path)
    f = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f  # (both: started by the Start menu or WMI, pythonw can get a stdout that leads nowhere, and every print
    # the window made was lost while only tracebacks reached this log: found 2026-10-21, when the notch's swipe lines never showed)
    faulthandler.enable(f)
    print(f"-- Monday's window, started {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()})", file=f)


def code_stamp(root=ROOT):
    """The newest change to the app's own code on disk (core/*.py): a window that started before it runs older code."""
    try:
        return max((f.stat().st_mtime for f in (root / "core").glob("*.py")), default=0.0)
    except OSError:
        return 0.0


def wait_gone(pid, timeout=RESTART_WAIT_S):
    """Until the process pid has ended (a restart: the Monday before this one), timeout at most. -> True when it is gone."""
    import psutil
    try:
        psutil.Process(pid).wait(timeout)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        return False
    return True


def launch_qt():
    """Monday on Qt (2026-10-19, docs/adr/0002-qt-all-platforms.md): the same app on Windows, macOS and Linux, in the look of the
    owner's Coucou recording. One process: Qt's own tray icon, Monday's window (core/qtwindow.py), the notch (core/qtnotch.py), the
    global shortcuts (core/hotkeys.py), and the Supervisor running Monday Core hidden, as before. Launched again, it only brings the
    window back (a local socket named per Core port: a named pipe on Windows, a socket file elsewhere)."""
    windowless_log()
    started_code = code_stamp()  # (a second launch finding newer code on disk starts this window again: every change loads)
    after = next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--after"), None)
    if after and after.isdigit() and not wait_gone(int(after)):  # a restart (docs/adr/0010): the Monday before this one ends first
        print(f"the Monday before this one (pid {after}) was still running after {RESTART_WAIT_S} s: this launch only shows it")
    exe = monday_exe()
    if exe and Path(sys.executable).resolve() != exe.resolve():  # Python's pythonw (the shortcuts, Start Monday.cmd): Monday.exe runs Monday,
        # the root of its processes in Task Manager once this launch has ended
        p = subprocess.Popen([str(exe), "-m", "core.desktop", *sys.argv[1:]], cwd=ROOT)
        try:
            code = p.wait(5)
        except subprocess.TimeoutExpired:
            return 0
        if code == 0:  # (it handed a running Monday the request)
            return 0
        print(f"{APP_EXE} stopped at once (exit code {code}): Monday runs on Python's pythonw instead", file=sys.stderr)
    from . import qtkit  # noqa: F401  (first: it puts pylib, where Qt is, on the path)
    from PySide6.QtCore import QLockFile, QObject, QRect, QUrl, Signal
    from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon
    from .qtkit import make_app
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = make_app(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # closing the window leaves Monday in the tray
    name = f"monday-desktop-{PORT}"
    probe = QLocalSocket()

    def tell(word, tries=1):  # a running Monday: its window comes back (or it quits, when asked)
        for _ in range(tries):
            probe.connectToServer(name)
            if probe.waitForConnected(400):
                probe.write(word)
                probe.flush()
                probe.waitForBytesWritten(500)
                return True
        return False
    if tell(b"quit" if "--quit" in sys.argv else b"open"):  # Monday is running: this launch ends
        return 0
    if "--quit" in sys.argv:
        return 0
    lock = QLockFile(str(STATE.with_name(f"desktop-{PORT}.lock")))  # one Monday, without the gap the question above leaves: at sign-in on
    # 2026-10-22 four launches in four seconds each found no Monday answering yet, and two windows ran (two ears, two notches, one Core)
    if not lock.tryLock(0):  # another launch has it and is still starting: its window comes up; this one only tells it (up to ~10 s)
        tell(b"open", tries=25)
        return 0
    QLocalServer.removeServer(name)  # (one left by a Monday that crashed)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # only this user's programs may ask it anything
    server.listen(name)
    # Monday Core first (2026-10-28: at sign-in the window was built before Core was started, and Core answered 50-90 s after the window
    # asked it): the supervisor starts it now, while the window's modules load and its pages are built; its word so far is kept for the tray
    early = {"emit": None, "last": None}

    def sup_state(s, d):
        early["last"] = (s, d)
        if early["emit"]:
            early["emit"](s, d)
    sup = Supervisor(sup_state)
    sup.start()
    from .hotkeys import Hotkeys
    from .qtnotch import Notch
    from .qtwindow import Bridge, Window
    if sys.platform == "win32":
        # ponytail: pythonnet (Windows' own voices, native.windows_speech) loads here, on the app's thread, before any window paints:
        # loaded first by a worker during the first paint, it crashed the app once in garbage collection (2026-10-19). A worker's
        # collection racing Qt's painting elsewhere stays possible; that fix would be PySide6's or Python's.
        try:
            import clr  # noqa: F401
        except Exception as e:  # (then the voices say why, where they're read)
            print(f"pythonnet didn't load: {e}", file=sys.stderr)
    st = load_state()
    bridge = Bridge()
    keys = Hotkeys(st.get("keys"))
    win = Window(bridge, st, hotkeys=keys, persist=True)
    # the supervisor's own word on whether Core is being (re)started, read by the window's calls on their threads: the window's copy of it
    # (core_phase) arrives through the app's event loop, and a call that failed in between said "unreachable" (2026-10-29, at a restart)
    bridge.waiting = lambda: sup.state in ("starting", "restarting")
    win.hide_on_close = True
    qr = st.get("qrect")
    if isinstance(qr, list) and len(qr) == 4 and all(isinstance(v, int) for v in qr) and \
            any(s.availableGeometry().intersects(QRect(*qr)) for s in QGuiApplication.screens()):
        win.setGeometry(*qr)
    notch = Notch(win)
    win.notch = notch
    notch.toast = lambda title, words: tray.showMessage(title, words, QSystemTrayIcon.MessageIcon.Information, 8000)
    win.ptt_ok = keys.status("talk")[0] == "on"
    # push-to-talk: the ear opens while the keys are held, the sentence ends when they're let go (native.Ear, Core's microphone)
    keys.fired.connect(lambda a: win.ear.hold() if a == "talk" and win.ear else None)
    keys.released.connect(lambda a: threading.Thread(target=win.ear.release, daemon=True).start() if a == "talk" and win.ear else None)

    class Relay(QObject):  # the Supervisor's thread -> the app's
        state = Signal(str, str)
        done = Signal()
        check = Signal()
    relay = Relay()
    tray = QSystemTrayIcon(QIcon(str(ICON_PNG)), app)
    tray.setToolTip(TIPS["starting"])
    apply_icon(app, tray, win.pet_prefs)  # the app's icon is the owner's pet, the logo the window shows; and stays it when they change it
    win.pet_changed = lambda prefs: apply_icon(app, tray, prefs)

    def on_state(s, d):
        win.core_phase = notch.core_phase = s  # (while this app starts Core, the window and the notch say "starting", not "offline")
        if s in ("failed", "stopped"):  # (the pages' calls held for a starting Core say why they failed: Bridge.release)
            bridge.release()
        tray.setToolTip((TIPS[s] + (f" ({d})" if d and s == "running" else ""))[:127])
        if s in ("failed", "stopped") and d:  # the one thing worth interrupting for
            tray.showMessage("Monday", d, QSystemTrayIcon.MessageIcon.Warning, 10000)
    relay.state.connect(on_state)
    early["emit"] = relay.state.emit  # (from here on the supervisor's thread tells the app's through the relay)
    if early["last"]:
        on_state(*early["last"])
    relay.done.connect(app.quit)

    def open_window():
        win.showNormal()
        win.raise_()
        win.activateWindow()

    def restart():
        if sup.state in ("failed", "stopped"):
            sup.start()
        elif not ask_core("/api/shutdown", restart=True):
            sup.start()

    def quit_all():  # Core takes a few seconds to stop its tasks and its Chrome: off the app's thread
        tray.setToolTip("Monday · quitting…")
        win.quitting = True
        threading.Thread(target=lambda: (sup.stop(), relay.done.emit()), daemon=True).start()

    def relaunch(keep_core=False):
        """Monday starts again (docs/adr/0010): a new launch that waits for this one to end (--after), then this one quits. keep_core: the
        window's own code changed, Core carries on (the new launch uses it; a Core on older code restarts once idle, sup.refresh)."""
        subprocess.Popen([sys.executable, "-m", "core.desktop", "--after", str(os.getpid())], cwd=ROOT)
        if keep_core:
            win.quitting = True
            return relay.done.emit()
        quit_all()

    def install_package(path):
        """The owner's Update (docs/adr/0010): Windows Installer's own wizard on the package Core checked against the owner's signature,
        then Monday closes so the wizard can replace its files; the wizard's last page starts the new Monday."""
        os.startfile(path)
        quit_all()
    win.install_package = install_package if sys.platform == "win32" else None
    win.restart_app = relaunch

    menu = QMenu()
    menu.addAction("Open Monday", open_window)
    again = menu.addAction("Restart Monday")
    again.triggered.connect(restart)
    menu.addAction("Open the log", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG))))
    menu.addAction("Circle and ask", notch.circle)  # Dotpals' circle (core/qtoverlay.py), as Ctrl+Alt+C
    show_notch = menu.addAction("Hide the notch")
    show_notch.triggered.connect(lambda: notch.set_pref("visible", not notch.p["visible"]))
    menu.aboutToShow.connect(lambda: (again.setText("Start Monday again" if sup.state in ("failed", "stopped") else "Restart Monday"),
                                      show_notch.setText("Hide the notch" if notch.p["visible"] else "Show the notch")))
    menu.addSeparator()
    menu.addAction("Quit Monday", quit_all)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda why: open_window() if why in (QSystemTrayIcon.ActivationReason.Trigger,
                                                                QSystemTrayIcon.ActivationReason.DoubleClick) else None)
    tray.show()

    checked = [-STARTUP_AGAIN_S]

    def check():  # the owner opened Monday: what preflight looks at started and checked (Monday.startup_check), said in a notification
        # when its verdict changed (startup_said); clicks close together get one (2026-10-24: three quick clicks showed three notifications,
        # which read as three Monday's)
        if time.monotonic() - checked[0] < STARTUP_AGAIN_S:
            return
        checked[0] = time.monotonic()
        bridge.post("/api/startup", {}, lambda d, err: startup_said(d, err, notch.toast), timeout=STARTUP_WAIT_S)
    relay.check.connect(check)

    def asked():  # another launch: "open" (the window back; a Core running older code restarts once idle), or "quit" (--quit)
        c = server.nextPendingConnection()
        if c is None:
            return
        c.waitForReadyRead(500)
        if bytes(c.readAll()).strip() == b"quit":
            return quit_all()
        if code_stamp() > started_code:  # the owner, 2026-10-28: "when we change anything then it should be added when we start it"
            print(time.strftime("%H:%M:%S"), "another launch: the window's code changed on disk since it started: starting it again", flush=True)
            return relaunch(keep_core=True)
        print(time.strftime("%H:%M:%S"), "another launch: this Monday's window brought back (one instance)", flush=True)
        open_window()
        sup.refresh(then=relay.check.emit)  # (each click on Monday: the start-up check, once Core runs the code on disk)
    server.newConnection.connect(asked)

    def ready(ev):  # Core's own word on how ready it is (Monday.readiness, with each COMPONENTS), in the tray's tooltip
        r = (ev.get("data") or {}).get("readiness") if ev.get("type") == "COMPONENTS" else None
        if r and sup.state == "running":
            tray.setToolTip(f"Monday · {ready_words(r)}"[:127])
    bridge.event.connect(ready)
    sup.refresh(then=relay.check.emit)  # (a Core found running older code than is on disk: restarted once it is idle; then the start-up check)
    win.show()
    win.start()
    code = app.exec()
    keys.close()
    bridge.stop()
    return code


# -- install: the icon and the shortcuts ---------------------------------------------------------------------------
def icon_images(prefs=None):
    """The app's icon at every size, as PIL images: the owner's pet on Monday's tile (qtkit.pet_image; 2026-10-20, it was a cyan ring)."""
    import io
    from PIL import Image
    from .qtkit import ICON_SIZES, pet_image  # (first: it puts Qt, in pylib, on the path)
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841  (painting needs one; the running app's when there is one)
    out = []
    for n in ICON_SIZES:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pet_image(prefs, n).save(buf, "PNG")
        out.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))
    return out


def write_icons(prefs=None):
    """static/monday.ico and monday-256.png (the shortcuts', notifications') as the owner's pet, written only when they changed; Windows'
    shell is told, so the Start menu and desktop shortcuts show it. -> True when they changed."""
    import io
    imgs = icon_images(prefs)
    png = io.BytesIO()
    imgs[-1].save(png, format="PNG")
    if ICON_PNG.exists() and ICON.exists() and ICON_PNG.read_bytes() == png.getvalue():
        return False
    imgs[-1].save(ICON, format="ICO", sizes=[im.size for im in imgs], append_images=imgs[:-1])
    ICON_PNG.write_bytes(png.getvalue())
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED: shortcuts' icons drawn again
    return True


def apply_icon(app, tray, prefs):
    """The running app's icons (its windows, taskbar button and tray) and the icon files, as the owner's pet now."""
    from .qtkit import pet_icon
    ic = pet_icon(prefs)
    app.setWindowIcon(ic)
    tray.setIcon(ic)
    try:
        write_icons(prefs)
    except OSError as e:  # (a file another program holds: the running app's icons are right anyway)
        print(f"icon files not written: {e}", file=sys.stderr)


def shortcut(path, target, args, workdir, icon, description):
    """A .lnk carrying the app's id, so the taskbar and notifications know it as Monday."""
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    link.SetPath(str(target))
    link.SetArguments(args)
    link.SetWorkingDirectory(str(workdir))
    link.SetIconLocation(str(icon), 0)
    link.SetDescription(description)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
    return path


def startup_link():
    from win32com.shell import shell, shellcon
    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_STARTUP, None, 0)) / "Monday.lnk"


STARTUP_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"  # Windows' own Startup apps


def approved(flag):
    """Windows' Startup apps switch for one Startup-folder shortcut, from its StartupApproved value (Task Manager -> Startup apps, Settings
    -> Apps -> Startup write it): off when the first byte's low bit is set (03, 07), on when it isn't (02, 06) or there is no value."""
    return not (isinstance(flag, (bytes, bytearray)) and len(flag) > 0 and flag[0] & 1)


def startup_allowed(name="Monday.lnk"):
    """Is Monday's Startup shortcut switched on in Windows' own Startup apps? (2026-10-29: it was off there, so Windows didn't start Monday
    at sign-in, while the shortcut was in place and General's switch said on.)"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED) as k:
            return approved(winreg.QueryValueEx(k, name)[0])
    except OSError:  # (no value: never switched off)
        return True


def startup_allow(name="Monday.lnk"):
    """The owner turned Start with Windows on in Monday's General: Windows' own switch for its shortcut is turned on with it (only when it
    was off; nothing else in the key is touched)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            flag = winreg.QueryValueEx(k, name)[0]
            if not approved(flag):
                winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, bytes([flag[0] & ~1]) + bytes(11))
    except FileNotFoundError:  # (never switched off in Windows: nothing to turn on)
        pass


def startup_on():
    """Does Monday start with Windows? Its shortcut in the owner's Startup folder, and Windows' own Startup apps switch on for it."""
    try:
        return startup_link().exists() and startup_allowed()
    except Exception:  # no shell API here: it doesn't
        return False


def startup(on):
    """Start Monday when Windows starts, or not: General's switch (the owner's click). The same shortcut as the Start menu's."""
    p = startup_link()
    if on:
        shortcut(p, Path(sys.base_exec_prefix) / "pythonw.exe", "-m core.desktop", ROOT, ICON, "Monday: starts with Windows.")  # (it hops to Monday.exe)
        startup_allow()
    elif p.exists():
        p.unlink()


def autostart_file():
    """Where macOS and Linux keep a program that starts at login: a LaunchAgent, an XDG autostart entry."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.monday.desktop.plist"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "monday.desktop"


def autostart_on():
    """Does Monday start when the owner logs in (General's switch), on this OS?"""
    return startup_on() if sys.platform == "win32" else autostart_file().exists()


def autostart(on):
    """Start Monday at login, or not (the owner's click in General): Windows' Startup shortcut, a macOS LaunchAgent, an XDG autostart
    entry; each runs `python -m core.desktop` from this folder."""
    if sys.platform == "win32":
        return startup(on)
    f = autostart_file()
    if not on:
        if f.exists():
            f.unlink()
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    if sys.platform == "darwin":
        from xml.sax.saxutils import escape
        f.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                     '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
                     '<key>Label</key><string>com.monday.desktop</string>'
                     f'<key>ProgramArguments</key><array><string>{escape(py)}</string><string>-m</string><string>core.desktop</string></array>'
                     f'<key>WorkingDirectory</key><string>{escape(str(ROOT))}</string><key>RunAtLoad</key><true/></dict></plist>\n',
                     encoding="utf-8")
    else:
        f.write_text(f'[Desktop Entry]\nType=Application\nName=Monday\nComment=Monday starts when you log in\n'
                     f'Exec="{py}" -m core.desktop\nPath={ROOT}\nX-GNOME-Autostart-enabled=true\n', encoding="utf-8")


def install():
    """The icon, then "Monday" in the Start menu and on the desktop: pythonw -m core.desktop, from this folder."""
    from win32com.shell import shell, shellcon
    write_icons()
    pythonw = Path(sys.base_exec_prefix) / "pythonw.exe"  # (Python's own, run from Monday.exe too; it hops to Monday.exe)
    made = []
    for folder in (shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0), shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)):
        made.append(shortcut(Path(folder) / "Monday.lnk", pythonw, "-m core.desktop", ROOT, ICON,
                             "Monday: your computer, by voice. Starts Monday Core and opens Monday's window."))
    return made


# -- Monday's own program (Windows) --------------------------------------------------------------------------------
def name_exe(path, icon=ICON):
    """A copy of a Python program, named Monday: its version's FileDescription and ProductName ("Python", the name Task Manager shows) say
    "Monday" (the same length, changed in place), and it carries Monday's icon. What the program doesn't have stays as it is."""
    import struct
    import win32api
    import win32con

    def first(typ):
        try:
            name = win32api.EnumResourceNames(h, typ)[0]
            return name, win32api.EnumResourceLanguages(h, typ, name)[0]
        except win32api.error:
            return None, None
    h = win32api.LoadLibraryEx(str(path), 0, win32con.LOAD_LIBRARY_AS_DATAFILE)
    try:
        ver, lang = first(win32con.RT_VERSION)
        data = win32api.LoadResource(h, win32con.RT_VERSION, ver, lang) if ver is not None else None
        group, glang = first(win32con.RT_GROUP_ICON)
        top = max((n for n in win32api.EnumResourceNames(h, win32con.RT_ICON) if isinstance(n, int)), default=0) if group is not None else 0
    finally:
        win32api.FreeLibrary(h)
    u = win32api.BeginUpdateResource(str(path), False)
    if data is not None:
        win32api.UpdateResource(u, win32con.RT_VERSION, ver, data.replace("Python\0".encode("utf-16-le"), "Monday\0".encode("utf-16-le")), lang)
    ico = Path(icon).read_bytes() if group is not None and Path(icon).exists() else b""
    count = struct.unpack_from("<HHH", ico)[2] if len(ico) >= 6 else 0
    grp = struct.pack("<HHH", 0, 1, count)
    for i in range(count):  # each image of the .ico an icon resource, and the program's icon group pointing at them instead
        w, ht, colors, res, planes, bits, size, at = struct.unpack_from("<BBBBHHII", ico, 6 + 16 * i)
        win32api.UpdateResource(u, win32con.RT_ICON, top + 1 + i, ico[at:at + size], glang)
        grp += struct.pack("<BBBBHHIH", w, ht, colors, res, planes, bits, size, top + 1 + i)
    if count:
        win32api.UpdateResource(u, win32con.RT_GROUP_ICON, group, grp, glang)
    win32api.EndUpdateResource(u, False)


def monday_copy(src, dst, dlls=(), home=None):
    """dst, named Monday.exe: a copy of the program src, named Monday in its version and icon (name_exe), the DLLs it loads (dlls) copied
    beside it and, given home, a pyvenv.cfg one folder up pointing at that Python (the same interpreter, standard library and packages).
    Task Manager shows one "Monday" for a tree of processes only when they share a file name (2026-10-21: Monday.exe and MondayCore.exe
    stayed two rows, and Hermes' two python.exe one). Made again when src is newer. -> dst, or None (the caller runs src, as before)."""
    import shutil
    src, dst = Path(src), Path(dst)
    if not (dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime):
        tmp = dst.with_name(dst.stem + ".partial")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            for d in map(Path, dlls):
                if d.exists():
                    shutil.copy2(d, dst.parent / d.name)
            if home:
                (dst.parent.parent / "pyvenv.cfg").write_text(f"home = {home}\ninclude-system-site-packages = true\n", encoding="utf-8")
            shutil.copy2(src, tmp)
            name_exe(tmp)
            os.replace(tmp, dst)
        except Exception as e:  # (no pywin32, a copy refused, or the old copy is running: it stays)
            print(f"{dst} wasn't made: {e}", file=sys.stderr)
    return dst if dst.exists() else None


def monday_exe(gui=True, runtime=RUNTIME):
    """Windows: Monday.exe for the window (gui: this Python's pythonw.exe, the row Task Manager names "Monday") or for Core (python.exe:
    a console program, so the commands it runs get no console window), with Python's DLLs: one folder per Python version, made the first
    time (the owner, 2026-10-21: all of Monday under one "Monday"). -> its path, or None (not Windows, or it couldn't be made: then Python's
    own programs run Monday, as before).
    ponytail: launched straight after Python updates itself, a Monday.exe keeps its old DLL; Monday starts through pythonw (the shortcuts,
    Start Monday.cmd), which hops to the current version's."""
    if sys.platform != "win32":
        return None
    base, home = Path(sys.base_exec_prefix), Path(runtime) / sys.version.split()[0]
    dlls = [base / f for f in (f"python{sys.version_info.major}{sys.version_info.minor}.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll")]
    return monday_copy(base / ("pythonw.exe" if gui else "python.exe"), (home if gui else home / "core") / "bin" / APP_EXE, dlls, home=base)


def named_python(python):
    """Another Python Monday keeps running (the ear's voice environment), as Monday.exe beside its own python.exe and DLLs. -> its path."""
    if sys.platform != "win32" or not Path(python).is_file():
        return python
    return str(monday_copy(python, Path(python).with_name(APP_EXE)) or python)


def monday_links(exe, runtime=RUNTIME):
    """A program that isn't Python (the screen driver), as Monday.exe: its folder's files hard-linked (the same files, no copies; copied
    where a link can't be made) into runtime/<its folder>-<its build time>, the program itself as Monday.exe. -> its path, or exe."""
    import shutil
    exe = Path(exe)
    folder = Path(runtime) / f"{exe.parent.name}-{int(exe.stat().st_mtime)}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for f in exe.parent.iterdir():
            to = folder / (APP_EXE if f.name.lower() == exe.name.lower() else f.name)
            if f.is_file() and not to.exists():
                try:
                    os.link(f, to)
                except OSError:
                    shutil.copy2(f, to)
        return str(folder / APP_EXE)
    except OSError as e:
        print(f"{folder} wasn't made: {e}", file=sys.stderr)
        return str(exe)


if __name__ == "__main__":
    if "--install" in sys.argv:
        for p in install():
            print("made", p)
        print("icon", ICON)
    elif "--check" in sys.argv:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "desktop.json"
            assert load_state(f) == {}, "first start: nothing kept yet"
            save_state({"qrect": [1, 2, 300, 400]}, f)
            assert load_state(f) == {"qrect": [1, 2, 300, 400]}
            save_state({**load_state(f), "pet": {"kind": "fox"}, "notch_at": {"cx": 5, "y": 6}}, f)
            save_state({**load_state(f), "qrect": [3, 4, 500, 600]}, f)
            assert load_state(f)["pet"] == {"kind": "fox"} and load_state(f)["notch_at"] == {"cx": 5, "y": 6}, "one part saving keeps the others'"
            f.write_text("[1, 2]")
            assert load_state(f) == {}, "not an object: nothing"
        if sys.platform == "win32":
            from .native import PYLIB
            sys.path.append(PYLIB)
            import clr  # noqa: F401  (pythonnet: Windows' own voices, native.windows_speech)
        from . import qtkit  # noqa: F401  (puts pylib, beside the app, on the path: Qt is every OS's window)
        from PySide6 import QtNetwork, QtWidgets  # noqa: F401
        from PySide6.QtCore import QLockFile
        with tempfile.TemporaryDirectory() as d:  # one Monday: what launch_qt's lock relies on (2026-10-22: two windows from one sign-in)
            first, second = QLockFile(str(Path(d) / "desktop-1.lock")), QLockFile(str(Path(d) / "desktop-1.lock"))
            assert first.tryLock(0) and not second.tryLock(0), "a second launch doesn't get the lock the first holds"
            first.unlock()
            assert second.tryLock(0), "...and gets it once the first lets go"
            second.unlock()
        assert too_many([0, 10, 20, 30], 40) == ([0, 10, 20, 30], False) and too_many([0, 10, 20, 30, 40], 50)[1]
        assert too_many([0, 10, 20, 30], 500) == ([], False), "old crashes don't count"
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "core.log"
            log.write_bytes(b"x" * 20)
            rotate(log, limit=10)
            assert not log.exists() and (Path(d) / "core.log.1").exists(), "a full log becomes core.log.1"
        imgs = icon_images({"kind": "fox"})
        img = imgs[0]
        assert [i.size for i in imgs][0] == (16, 16) and imgs[-1].size == (256, 256) and img.getpixel((8, 8))[3] == 255 \
            and img.getpixel((0, 0))[3] < 255, "the pet on a tile with rounded corners, at every size"
        big = imgs[-1].convert("RGB")
        assert len(big.getcolors(1 << 16) or ()) > 8 and imgs[-1].tobytes() != icon_images({"kind": "cat"})[-1].tobytes(), \
            "the pet is drawn (not a plain tile), and another pet is another icon"
        assert set(TIPS) == {"starting", "running", "restarting", "stopped", "failed"}
        _sv = Supervisor(lambda s, d: None)
        _sv.state = "restarting"
        _sv.adopt({"root": str(ROOT), "stale": True})
        assert _sv.state == "restarting", "a stale Core asked to restart stays 'restarting' while it is on its way out"
        _sv.adopt({"root": str(ROOT), "stale": False})
        assert _sv.state == "running", "...and the new one answering is running"
        # a restart's new launch waits for the Monday before it (docs/adr/0010); a window started before the code on disk changed is older
        assert wait_gone(2 ** 22 + 7, 1) and code_stamp() > 0, "a process that isn't there is gone at once; the app's code has a stamp"
        _sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            assert not wait_gone(_sleeper.pid, 0.3), "still running: not gone within the time given"
        finally:
            _sleeper.kill()
        assert wait_gone(_sleeper.pid, 10), "ended: gone"
        # Windows' own Startup apps switch (2026-10-29: off there, and General's switch said on): 03/07 off, 02/06 or nothing on
        assert not approved(bytes([3]) + bytes(11)) and not approved(bytes([7]) + bytes(11)) and approved(bytes([2]) + bytes(11))
        assert approved(bytes([6]) + bytes(11)) and approved(None) and approved(b"")
        assert exit_meaning(0) == "stopped on purpose" and exit_meaning(-1073741819) == "a native crash (access violation)"
        assert exit_meaning(3221225786).startswith("a Ctrl+C") and "outside" in exit_meaning(1234) and exit_meaning(None) == "unknown"
        assert exit_meaning(1073807364).startswith("Windows shut down"), "a shutdown's end isn't taken for a kill (2026-10-21)"
        # the handshake (2026-10-23): a Core found running is used only when it is this app's; one on older code restarts once idle
        assert core_fit({"root": str(ROOT), "stale": False}) == "ok"
        assert core_fit({"root": str(ROOT), "stale": True}) == "stale", "its code changed on disk since it started: older code"
        assert core_fit({"root": str(ROOT.parent / "elsewhere"), "stale": False}) == "foreign", "another app folder's Core"
        assert core_fit({"name": "Monday", "version": "0.2.0"}) == "ok", "a Core from before the handshake: used as before"
        # each click on Monday (2026-10-24): once Core answers with the code on disk, the start-up check (`then`); a stale Core restarted
        # first (once, when idle) and waited for; another app folder's Core never checked
        _hello, _ask, REFRESH_POLL_S = hello, ask_core, 0.01
        here, stale_busy, stale_idle = {"root": str(ROOT)}, {"root": str(ROOT), "stale": True, "busy": True}, {"root": str(ROOT), "stale": True}
        try:
            for said, want_asks, want_checks in (([here], 0, 1), ([stale_busy, stale_idle, stale_idle, None, here], 1, 1),
                                                 ([{"root": str(ROOT.parent / "elsewhere")}], 0, 0)):
                seq, asks, checks = list(said), [], []
                hello = lambda timeout=5, seq=seq: seq.pop(0) if len(seq) > 1 else seq[0]  # noqa: E731
                ask_core = lambda path, asks=asks, **body: asks.append(path) or True  # noqa: E731
                Supervisor(lambda s, d: None).refresh(then=lambda checks=checks: checks.append(1)).join(5)
                assert (len(asks), len(checks)) == (want_asks, want_checks), (said, asks, checks)
        finally:
            hello, ask_core, REFRESH_POLL_S = _hello, _ask, 3
        # Quit (2026-10-26): Monday's own Core is asked to stop even when it doesn't answer a quick hello, and isn't killed for it

        class _Proc:
            done = killed = False

            def poll(self):
                return 0 if self.done else None

            def wait(self, timeout=None):
                self.done = True
                return 0

            def kill(self):
                self.killed = True
        quits = []
        try:
            hello = lambda timeout=5: None  # noqa: E731  (busy: no answer within the quick hello's wait)
            ask_core = lambda path, **body: quits.append(path) or True  # noqa: E731
            sup = Supervisor(lambda s, d: None)
            sup.proc = _Proc()
            sup.stop()
            assert quits == ["/api/shutdown"] and sup.proc.done and not sup.proc.killed, ("a Core slow to answer is still asked to stop", quits)
        finally:
            hello, ask_core = _hello, _ask
        assert ready_words({"state": "degraded", "missing": ["Ollama offline"], "internet": "offline"}) == \
            "degraded: Ollama offline, offline: this PC's models only" and ready_words({"state": "ready", "internet": "online"}) == "ready"
        # the start-up check's notification (2026-10-27): shown when its verdict changed since the one last shown, never for the same
        # verdict again, through a stand-in bridge answering /api/startup as Core does (the window's Bridge: post(path, body, then, timeout))

        class _Bridge:
            def __init__(self, *answers):
                self.answers = list(answers)

            def post(self, path, body, then, timeout=None):
                assert path == "/api/startup", path
                then(*self.answers.pop(0))
        with tempfile.TemporaryDirectory() as d:
            f, shown = Path(d) / "desktop.json", []
            save_state({"pet": {"kind": "fox"}}, f)
            needs = {"title": "Monday: 1 thing needs you", "words": "Claude Code: the CLI isn't signed in. Running: Ollama, Jev."}
            fine = {"title": "Monday is ready", "words": "Running: Ollama, Jev, Hermes."}
            b = _Bridge((needs, None), (needs, None), (None, "HTTP 503"), (fine, None), (fine, None), (needs, None))
            for _ in range(6):
                b.post("/api/startup", {}, lambda d, err: startup_said(d, err, lambda t, w: shown.append((t, w)), f), timeout=1)
            assert shown == [(needs["title"], needs["words"]), (fine["title"], fine["words"]), (needs["title"], needs["words"])], \
                ("one notification per change of verdict: the same one twice shows once, a failed check nothing", shown)
            assert load_state(f)["pet"] == {"kind": "fox"} and load_state(f)[STARTUP_SAID] == [needs["title"], needs["words"]], \
                "the verdict is kept beside the window's own keys, and they beside it"
        import socket
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(4)  # a Core that holds its port but doesn't answer (busy, or on its way out): never accepted, never answered
        PORT, BASE = held.getsockname()[1], f"http://127.0.0.1:{held.getsockname()[1]}"
        assert port_taken(), "a listening port is taken"
        seen = []
        sup = Supervisor(lambda s, d: seen.append(s))
        sup.start()
        time.sleep(4)
        sup.quitting = True
        assert sup.proc is None and "starting" not in seen and "stopped" not in seen, \
            ("a Core slow to answer is watched, not taken for gone: no second Core, no 'stopped' (2026-10-21)", seen)
        held.close()
        assert not port_taken(), "a closed port is free"
        if sys.platform == "win32":
            import win32api
            import shutil
            with tempfile.TemporaryDirectory() as d:
                app, core = monday_exe(runtime=Path(d)), monday_exe(gui=False, runtime=Path(d))
                assert app and core and app.name == core.name == APP_EXE and app != core, ("the window and Core both Monday.exe", app, core)
                for e in (app, core):
                    lang, cp = win32api.GetFileVersionInfo(str(e), "\\VarFileInfo\\Translation")[0]
                    assert win32api.GetFileVersionInfo(str(e), f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription") == "Monday", e
                out = subprocess.run([str(core), "-c", "import sys, httpx; print(sys.base_prefix)"], capture_output=True, text=True, cwd=ROOT,
                                     timeout=60)
                assert out.returncode == 0 and out.stdout.strip() == sys.base_prefix and not out.stderr, ("the same Python, its packages", out)
                stamp = app.stat().st_mtime
                assert monday_exe(runtime=Path(d)) == app and app.stat().st_mtime == stamp, "made once per Python version"
                py = Path(d) / "voice" / "bin" / "python.exe"  # another Python, its DLLs beside it: Monday.exe beside them (the ear)
                py.parent.mkdir(parents=True)
                (py.parent.parent / "pyvenv.cfg").write_text(f"home = {sys.base_exec_prefix}\n", encoding="utf-8")
                for f in ["python.exe"] + [p.name for p in (Path(sys.base_exec_prefix)).glob("*.dll")]:
                    shutil.copy2(Path(sys.base_exec_prefix) / f, py.parent / f)
                ear = Path(named_python(str(py)))
                assert ear == py.with_name(APP_EXE) and subprocess.run([str(ear), "-c", "print(6 * 7)"], capture_output=True, text=True,
                                                                       timeout=60).stdout.strip() == "42", ear
                tool = Path(d) / "tool" / "tool.exe"  # a program that isn't Python, with a file it needs beside it (the screen driver)
                tool.parent.mkdir()
                tool.write_bytes(b"MZ")
                (tool.parent / "helper.dll").write_bytes(b"x")
                linked = Path(monday_links(tool, Path(d) / "links"))
                assert linked.name == APP_EXE and os.path.samefile(linked, tool) and (linked.parent / "helper.dll").exists(), linked
        print("desktop self-check: OK")
    else:
        sys.exit(launch_qt())
