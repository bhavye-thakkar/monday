"""Monday's hands: deterministic computer control. A model may propose an action; only this code executes it.

- Driver: one persistent `cua-driver mcp --direct` child for UI Automation (elements, element clicks, set_value),
  the list of installed apps and launching them (desktop and Store apps alike).
- Win32 through ctypes: windows, focus, keyboard and mouse input (SendInput), media and volume keys.
- Screen: a PIL grab plus Windows' own OCR engine (winrt), local and ~150 ms for the whole screen. It is the
  fallback for text UI Automation can't see (web pages in Chrome expose no UIA tree).

Every action returns {"ok", "text", "verified", ...}. verified=True means Monday looked at the screen and saw the
result; False means it looked and didn't; None means the result can't be checked, and the text says so.
Self-check: python -m core.computer   (offline parts only; add --live to open and close Notepad)
"""
import asyncio
import difflib
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import psutil

from . import cua_proxy, probes

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "uploads"          # screenshots become ordinary `files` rows, served by /api/files/{id}
DRIVER_TIMEOUT_S = 20
CURSOR_TIMEOUT_S = 5  # one move of Monday's cursor (Driver._cursor)
LIST_APPS_TIMEOUT_S = 40
START_APPS_TIMEOUT_S = 20
APPS_TTL_S = 900                           # installed apps change rarely; list_apps takes ~5 s, so it is cached
WINDOW_WAIT_S = 10                         # how long a launched app may take to show a window
LABEL_WORDS = 4                            # a control's name at most this many words: a longer one is only looked for by its UI name
VENDORS = {"google", "microsoft", "windows", "adobe", "mozilla", "apple", "jetbrains"}  # "google chrome": the app is its last word
TYPO_MIN = 0.86                            # a name this close to an app's (or a well-known one's: "notpad") is that app
NEAR_MIN = 0.7                             # an installed app's name this close to an unknown one is offered ("Did you mean Notepad?")
NEAR_WORD = 0.8                            # ...or one of its own words this close ("photo shop": Microsoft Photos)
READY_WAIT_S = 4.0                         # "open X and write …": X gets this long to be ready for typing, after its window shows
SAW_WAITS_S = (0.1, 0.2, 0.35)             # typed words: looks at the window's controls this far apart, then OCR (2026-10-10: one look at
#                                            0.15 s missed Notepad's text twice in three, and OCR added ~2 s)
READY_STABLE_S = 0.25                      # ...ready = its title unchanged this long (Notepad showed "Notepad" ~0.6 s while it restored
#                                            its tabs, measured 2026-10-10; keys sent then went nowhere: "I can't see it there")
# Editors whose Ctrl+N makes a new, empty document: when the document they open on already holds text (Notepad restores the owner's tabs),
# "open X and write …" writes into a new one, never into the owner's text. An accelerator: other apps type where their cursor is.
NEW_DOCUMENT = ("notepad", "wordpad", "word", "visual studio code", "sublime text")
WORKED_IN_S = 300  # "save it" this long after Monday wrote in a window means that window, wherever the owner has been since
# A new document's own name in its window's title ("Untitled - Notepad", "Untitled-1 - …", Word's "Document2"). Anything else is a file
# (or text Notepad named the tab after): 2026-10-15 "open notepad and write hello" typed into ".env - Notepad", its text unread by UIA
UNTITLED = re.compile(r"^\*?\s*(?:untitled|new\s+(?:document|file|tab)|document\d*)\b", re.I)
URL_WAIT_S = 8
CLOSE_WAIT_S = 5
SAVE_WAIT_S = 5                            # after Enter in a Save dialog, how long the file may take to appear
POLL_S = 0.25
MAX_ELEMENTS = 400
COPY_WAIT_S = 1.5  # what a Ctrl+C or a Copy button puts on the clipboard arrives within this (transfer)
# "copy … from X and paste it in Y" (Computer.transfer): which words, and the app's own Copy button under an answer
LAST_ANSWER = re.compile(r"\b(?:last|latest|newest|recent)\b.*\b(?:answer|reply|response|message|output)\b|^(?:the\s+|its\s+|his\s+)?"
                         r"(?:answer|reply|response|output)$", re.I)
WHOLE = re.compile(r"\b(?:everything|all\s+(?:of\s+)?(?:it|the\s+text)|the\s+whole\s+(?:thing|text|page|document))\b", re.I)
COPY_BUTTON = re.compile(r"^copy(?:\s+(?:message|response|answer|text|code|to\s+clipboard|reply))?$", re.I)
OCR_LINES = 60
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
WIN = sys.platform == "win32"


class ComputerError(Exception):
    pass


SESSION_ENDED = re.compile(r"session has ended|call start_session", re.I)


def done(text, verified=None, **extra):
    return {"ok": True, "text": text, "verified": verified, **extra}


def failed(text, **extra):
    return {"ok": False, "text": text, "verified": False, **extra}


# -- Win32 --------------------------------------------------------------------------------------------------
if WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    try:  # physical pixels everywhere: OCR boxes, window rects and cursor positions share one coordinate space
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class _WTSINFOEX(ctypes.Structure):  # WTSINFOEXW up to SessionFlags; its union holds 64-bit times, so it starts at 8
        _fields_ = [("level", wintypes.DWORD), ("_pad", wintypes.DWORD), ("session_id", wintypes.ULONG), ("state", ctypes.c_int),
                    ("flags", wintypes.LONG)]

    wtsapi32 = ctypes.WinDLL("wtsapi32")
    _ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    for _fn in ("SetForegroundWindow", "BringWindowToTop", "IsIconic", "IsZoomed", "IsWindowVisible", "GetWindowTextLengthW"):
        getattr(user32, _fn).argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.SwitchDesktop.argtypes = (wintypes.HANDLE,)
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype, kernel32.GlobalAlloc.argtypes = ctypes.c_void_p, (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalLock.restype, kernel32.GlobalLock.argtypes = ctypes.c_void_p, (ctypes.c_void_p,)
    kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.GetClipboardData.restype, user32.GetClipboardData.argtypes = ctypes.c_void_p, (wintypes.UINT,)
    user32.SetClipboardData.restype, user32.SetClipboardData.argtypes = ctypes.c_void_p, (wintypes.UINT, ctypes.c_void_p)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
TYPE_KEY_DELAY_S = 0.01  # between typed characters (AutoHotkey's default key delay); raise it if an app still drops keys
PASTE_MIN_CHARS = 120    # text this long, or with line breaks, goes in through the clipboard and Ctrl+V: one exact insert instead of thousands
#                          of injected keystrokes (2026-10-13: long dictations arrived garbled or short); the owner's clipboard text is put back
PASTE_SETTLE_S = 0.4     # the app takes the paste a moment after Ctrl+V; the clipboard is put back only after that
MOUSE_MOVE, LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP, WHEEL, HWHEEL = 0x1, 0x2, 0x4, 0x8, 0x10, 0x800, 0x1000
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 3, 6, 9, 4, 7
WM_CLOSE = 0x0010
VK = {"backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
      "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D, "delete": 0x2E, "del": 0x2E, "win": 0x5B,
      "windows": 0x5B, "cmd": 0x5B, "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF, "next_track": 0xB0,
      "prev_track": 0xB1, "play_pause": 0xB3, **{f"f{i}": 0x6F + i for i in range(1, 25)}}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3}


def vk_of(key):
    k = re.sub(r"\s+", "", key.lower())
    if k in VK:
        return VK[k]
    if len(k) == 1 and k.isalnum():
        return ord(k.upper())
    raise ComputerError(f"I don't know the key “{key}”.")


def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise ComputerError("Windows refused the input (another program may be running as administrator).")


def _key(vk, up=False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)))


def _mouse(flags, data=0):
    return INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(dx=0, dy=0, mouseData=data & 0xFFFFFFFF, dwFlags=flags, time=0, dwExtraInfo=0)))


def press(keys):
    """Press a combination (["ctrl", "shift", "t"]) or a single key, releasing in reverse order."""
    vks = [vk_of(k) for k in keys]
    _send([_key(v) for v in vks] + [_key(v, True) for v in reversed(vks)])


def tap(vk, times=1):
    for _ in range(times):
        _send([_key(vk), _key(vk, True)])


def type_unicode(text):
    """Types exactly `text` into the focused control (KEYEVENTF_UNICODE: no keyboard layout guessing), one character
    at a time: Windows 11's Notepad garbles keys injected as one batch (it got "Hello," then dots). Blocking: call it
    through asyncio.to_thread."""
    for line_no, line in enumerate(text.split("\n")):
        if line_no:
            tap(VK["enter"])
            time.sleep(TYPE_KEY_DELAY_S)
        units = line.encode("utf-16-le")
        for i in range(0, len(units), 2):
            code = int.from_bytes(units[i:i + 2], "little")
            _send([INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | up, time=0, dwExtraInfo=0)))
                   for up in (0, KEYEVENTF_KEYUP)])
            time.sleep(TYPE_KEY_DELAY_S)


def click_at(x, y, button="left", count=1):
    user32.SetCursorPos(int(x), int(y))
    down, up = (RIGHT_DOWN, RIGHT_UP) if button == "right" else (LEFT_DOWN, LEFT_UP)
    for _ in range(count):
        _send([_mouse(down), _mouse(up)])


def wheel(clicks, horizontal=False):
    _send([_mouse(HWHEEL if horizontal else WHEEL, int(120 * clicks))])


def _mouse_to(x, y, flags=0):
    """Real mouse input at screen pixel (x, y): absolute over the whole virtual desktop, so drag-and-drop sees it
    (SetCursorPos alone doesn't generate the input OLE drag-and-drop watches)."""
    vx, vy, vw, vh = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    nx, ny = round((x - vx) * 65535 / max(1, vw - 1)), round((y - vy) * 65535 / max(1, vh - 1))
    return INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(dx=nx, dy=ny, mouseData=0, dwFlags=MOUSE_MOVE | 0x8000 | 0x4000 | flags,
                                                    time=0, dwExtraInfo=0)))


def drag_path(a, b, steps=14):
    """Press at a, move to b in small steps (apps need intermediate moves to start a drag), release at b."""
    _send([_mouse_to(*a), _mouse_to(*a, LEFT_DOWN)])
    time.sleep(0.05)
    for i in range(1, steps + 1):
        _send([_mouse_to(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)])
        time.sleep(0.015)
    _send([_mouse_to(*b, LEFT_UP)])


def cursor():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# -- the Camera app (the brief's "take a picture of me", 2026-10-06) ------------------------------------------------------------
PHOTO_WAIT_S = 4          # the new picture lands in the Camera Roll within this
CAMERA_WARM_S = 1.5       # a just-opened Camera shows its preview before the shutter works
CAMERA_SHUTTER = ("Take photo", "Take Photo", "Capture", "Take picture")  # the shutter control's accessible name, by Camera version
CAMERA_PHOTO_MODE = "Switch to photo mode"  # the Camera remembers video mode, where the shutter is "Take video" (seen here 2026-10-06)
CAMERA_MODE_S = 0.8       # the mode switch redraws the controls


def camera_rolls():
    """Where the Camera app saves: the Camera Roll under Pictures (OneDrive's when Pictures lives there); every one that exists."""
    from . import files
    homes = {files.known_folder("pictures"), Path.home() / "Pictures", Path.home() / "OneDrive" / "Pictures"}
    return [p / "Camera Roll" for p in homes if p and (p / "Camera Roll").is_dir()]


def camera_roll_files():
    return {str(f) for d in camera_rolls() for f in d.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")}


# -- clipboard (text only) ----------------------------------------------------------------------------------------------
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
# Text that is a secret whoever copied it: card numbers, API keys and tokens, or one long password-like word.
_SECRETISH = re.compile(r"\b(?:\d[ -]?){13,19}\b|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|"
                        r"\bxox[bpas]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")


def looks_secret(text):
    t = (text or "").strip()
    if _SECRETISH.search(t):
        return True
    classes = sum(bool(re.search(rx, t)) for rx in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]"))
    return " " not in t and 12 <= len(t) <= 128 and classes >= 3  # one long mixed word: a password or a token


def shows_secret(text):
    """A card number, an API key or token, a private key: what a picture of the whole screen must not carry off this PC (looks_secret's
    one long mixed word is every address bar's URL, so a look at the screen goes by these alone)."""
    return bool(_SECRETISH.search(text or ""))


def _open_clipboard():
    for _ in range(20):  # another app may hold it for a moment
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise ComputerError("Another app is holding the clipboard right now.")


def clipboard_text():
    """The clipboard's text, or None when it holds no text."""
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(ptr) if ptr else None
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard(text):
    data = ctypes.create_unicode_buffer(text)
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(data))
    ptr = kernel32.GlobalLock(h)
    ctypes.memmove(ptr, data, ctypes.sizeof(data))
    kernel32.GlobalUnlock(h)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise ComputerError("Windows didn't accept the new clipboard text.")
    finally:
        user32.CloseClipboard()


# -- processes ---------------------------------------------------------------------------------------------------------
# Never force-closed: Windows itself, security, and the shell (mirrors router.CRITICAL_PROCESSES, which rules first).
CRITICAL_PROCESSES = {"system", "idle", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe",
                      "services.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe",
                      "msmpeng.exe", "securityhealthservice.exe", "nissrv.exe", "audiodg.exe", "spoolsv.exe", "taskhostw.exe",
                      "runtimebroker.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "searchhost.exe"}


def process_matches(query, procs, titles):
    """Processes whose name (with or without .exe) or window title matches what the owner called them."""
    q = _norm(query).replace(" ", "")
    exe = PROCESS_HINTS.get(_norm(query)) or ALIASES.get(_norm(query))
    out = []
    for pr in procs:
        name = (pr["name"] or "").lower()
        stem = name.removesuffix(".exe")
        title = titles.get(pr["pid"], "").lower()
        if q and (q == stem.replace(" ", "") or (exe and name == str(exe).lower()) or (len(q) > 3 and q in stem.replace(" ", ""))
                  or (len(q) > 3 and q in title.replace(" ", ""))):
            out.append(pr)
    return out


def process_table(cpu=False, memory=True):
    """Every process: pid, name, memory (MB), CPU % (sampled over 0.4 s when asked), its window title if it has one.
    Memory is the slow part (~0.6 s, 2 s inside the Core): Windows refuses ~160 processes direct access and psutil takes
    a whole-system snapshot for each. Names alone take ~10 ms, so memory=False skips it (mb None; see rss_mb)."""
    procs = []
    for pr in psutil.process_iter(["pid", "name"] + (["memory_info"] if memory else [])):
        try:
            mi = pr.info.get("memory_info")
            procs.append({"pid": pr.info["pid"], "name": pr.info["name"] or "?",
                          "mb": round((mi.rss if mi else 0) / 2**20, 1) if memory else None, "_p": pr})
        except (psutil.Error, AttributeError):
            continue
    if cpu:
        for x in procs:
            try:
                x["_p"].cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(0.4)
        n = psutil.cpu_count() or 1
        for x in procs:
            try:
                x["cpu"] = round(x["_p"].cpu_percent(None) / n, 1)
            except psutil.Error:
                x["cpu"] = 0.0
    for x in procs:
        x.pop("_p", None)
    return procs


def rss_mb(pid):
    """One process's memory in MB (0 when Windows won't say)."""
    try:
        return round(psutil.Process(pid).memory_info().rss / 2**20, 1)
    except psutil.Error:
        return 0.0


@dataclass
class Win:
    hwnd: int
    pid: int
    title: str
    process: str

    @property
    def rect(self):
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def view(self):
        return {"hwnd": self.hwnd, "pid": self.pid, "title": self.title, "process": self.process}


_proc_names = {}


def _proc(pid):
    name = _proc_names.get(pid)
    if name is None:
        try:
            name = psutil.Process(pid).name()
        except (psutil.Error, ValueError):
            name = "?"
        _proc_names[pid] = name
    return name


_families = {}


def family(pid):
    """The Store package a process runs from ("spotifyab.spotifymusic_zpdnekdrzrea0"), "" for an ordinary program: read off its
    folder, WindowsApps/<name>_<version>_<arch>__<publisher>/. Spotify titles its window with the song that plays, so the
    package is the only thing that always says whose window it is."""
    fam = _families.get(pid)
    if fam is None:
        try:
            m = re.search(r"\\WindowsApps\\([^\\_]+)_[^\\]*__([^\\]+)\\", psutil.Process(pid).exe())
        except (psutil.Error, ValueError, OSError):
            m = None
        fam = _families[pid] = f"{m[1]}_{m[2]}".lower() if m else ""
    return fam


def _cloaked(hwnd):
    val = wintypes.DWORD()
    return dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val)) == 0 and val.value != 0


def windows():
    """Visible, titled top-level windows, top of the z-order first (cloaked Store-app ghosts and tool windows skipped)."""
    if not WIN:
        return []
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: owned popups aren't apps
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n or user32.GetWindowLongW(hwnd, -20) & 0x80 or _cloaked(hwnd):  # GWL_EXSTYLE & WS_EX_TOOLWINDOW
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = buf.value
        if title in ("Program Manager", "Windows Input Experience") or title.startswith("Cua.AgentCursorOverlay"):
            return True
        out.append(Win(int(hwnd), pid.value, title, _proc(pid.value)))
        return True

    user32.EnumWindows(_ENUM(cb), 0)
    return out


POPUP_MIN = 60  # a pop-up's least size each way, in px (Office's tooltips and transient frames are smaller)
MAIN_CLASSES = ("OpusApp", "PPTFrameClass", "XLMAIN", "MsoSplash")  # Office's own document windows and its splash: the work, not pop-ups


def top_hwnds():
    """Every visible top-level window now: the `before` of popups()."""
    if not WIN:
        return set()
    out = set()

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            out.add(int(hwnd))
        return True

    user32.EnumWindows(_ENUM(cb), 0)
    return out


def popups(names, before):
    """The windows that came up over an app since `before` (the hwnds there were then): visible, top-level, owned dialogs included (windows()
    leaves those out as not apps), of the processes named `names` and their children, not their main or splash windows, POPUP_MIN px each
    way at least -> [Win]. A sign-in or licence prompt, a message box: the pop-up guard's (executor.watch_popups, 2026-10-26)."""
    if not WIN:
        return []
    want, pids = {n.lower() for n in names}, set()
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() in want:
            pids.add(p.pid)
            try:
                pids |= {c.pid for c in p.children(recursive=True)}
            except psutil.Error:
                pass
    out = []

    def cb(hwnd, _):
        if int(hwnd) in before or not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        k, r = ctypes.create_unicode_buffer(64), wintypes.RECT()
        user32.GetClassNameW(hwnd, k, 64)
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if k.value in MAIN_CLASSES or r.right - r.left < POPUP_MIN or r.bottom - r.top < POPUP_MIN:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append(Win(int(hwnd), pid.value, buf.value, _proc(pid.value)))
        return True

    user32.EnumWindows(_ENUM(cb), 0)
    return out


def still_there(hwnd):
    return bool(WIN and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))


def foreground_raw():
    """(hwnd, title, class) of the real foreground window: dialogs included, which windows() leaves out."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, "", ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf, cls = ctypes.create_unicode_buffer(n + 1), ctypes.create_unicode_buffer(64)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    user32.GetClassNameW(hwnd, cls, 64)
    return hwnd, buf.value, cls.value


def _flat(s):
    """Text as an editor holds it, for exact comparison: one newline kind, no trailing spaces on a line, none at the ends."""
    return "\n".join(line.rstrip() for line in (s or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def seen(want, text):
    """Did `text` (OCR or a control's value) show `want`? Exact after normalizing, else a long enough fuzzy run
    (OCR reads "Hello, I am Monday." as "Hello, l am Monday")."""
    w, t = _norm(want), _norm(text)
    if not w:
        return None
    if w in t:
        return True
    m = difflib.SequenceMatcher(None, w, t, autojunk=False).find_longest_match(0, len(w), 0, len(t))
    if m.size >= max(4, int(0.8 * len(w))):
        return True
    blocks = difflib.SequenceMatcher(None, w, t, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks) >= 0.85 * len(w)


def foreground():
    hwnd = user32.GetForegroundWindow() if WIN else 0
    return next((w for w in windows() if w.hwnd == hwnd), None)


def locked():
    """True while the session is locked (the lock screen shows; apps can't be seen or used) or a secure desktop such
    as a UAC prompt owns the input."""
    if not WIN:
        return False
    buf, n = ctypes.c_void_p(), wintypes.DWORD()
    if wtsapi32.WTSQuerySessionInformationW(None, wintypes.DWORD(0xFFFFFFFF), 25, ctypes.byref(buf), ctypes.byref(n)):  # WTSSessionInfoEx
        try:
            if ctypes.cast(buf, ctypes.POINTER(_WTSINFOEX)).contents.flags == 0:  # WTS_SESSIONSTATE_LOCK
                return True
        finally:
            wtsapi32.WTSFreeMemory(buf)
    h = user32.OpenInputDesktop(0, False, 0x0100)  # fails while a secure desktop (UAC, credentials) has the input
    if not h:
        return True
    user32.CloseDesktop(h)
    return False


LOCKED = "Your PC is locked, so I can't see or use its apps right now. Unlock it and ask again."


def activate(hwnd):
    """Bring a window to the front. Windows only lets the process that sent the last input move focus, so Monday
    first sends a zero-distance mouse move (no visible effect), then asks."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    _send([_mouse(MOUSE_MOVE)])
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    for _ in range(10):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.03)
    return False


# -- cua-driver (MCP over stdio, one long-lived child) -------------------------------------------------------------
class Driver:
    def __init__(self):
        self.proc = None
        self._id = 0
        self._waiting = {}
        self._reader = None
        self._lock = asyncio.Lock()
        self.frames, self.cursor_on = {}, False  # Monday's cursor (cua_proxy): where the last snapshots' elements are; shown yet

    def available(self):
        return probes.cua_driver() is not None

    async def _cursor(self, point):
        """Monday's cursor glides to `point` before it acts there (the driver draws it; the owner's own mouse doesn't move)."""
        for name, args in cua_proxy.cursor_calls(point, self.cursor_on):
            r = await self._rpc("tools/call", {"name": name, "arguments": args}, CURSOR_TIMEOUT_S)
            if r.get("isError"):  # an ended session, or a driver without the cursor: shown again from the start next time
                self.cursor_on = False
                return
        self.cursor_on = True
        await asyncio.sleep(cua_proxy.CURSOR_GLIDE_S)

    async def show_cursor(self, point):
        """Monday's cursor at `point` with nothing to act on there (a document being built in front of the owner: core/office.py)."""
        try:
            await self._ensure()
            await self._cursor(point)
        except ComputerError:
            self.cursor_on = False

    async def _ensure(self):
        if self.proc and self.proc.returncode is None:
            return
        async with self._lock:
            if self.proc and self.proc.returncode is None:
                return
            exe = probes.cua_driver()
            if not exe:
                raise ComputerError("Screen control isn't set up: MONDAY_CUA_DRIVER is not set in Monday\\.env.")
            env = probes.cua_env(exe)  # (the driver to run is MONDAY_CUA_DRIVER: on Windows the same program as Monday.exe)
            self.proc = await asyncio.create_subprocess_exec(
                env["MONDAY_CUA_DRIVER"], "mcp", "--direct", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env={**probes.worker_env(), **env}, limit=64 << 20, creationflags=NO_WINDOW)
            self._reader = asyncio.create_task(self._read(self.proc))
            self.cursor_on = False  # (a new driver: its cursor session starts again)
            await self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "monday-core", "version": "0.2"}})
            self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')

    async def _read(self, proc):
        async for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            fut = self._waiting.pop(msg.get("id"), None) if isinstance(msg, dict) else None
            if fut and not fut.done():
                fut.set_result(msg)
        for fut in self._waiting.values():  # the driver exited: fail whatever still waits
            if not fut.done():
                fut.set_exception(ComputerError("The screen-control driver stopped."))
        self._waiting.clear()

    async def _rpc(self, method, params, timeout=DRIVER_TIMEOUT_S):
        self._id += 1
        rid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n").encode())
        await self.proc.stdin.drain()
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._waiting.pop(rid, None)
            raise ComputerError(f"The screen-control driver didn't answer {method} within {timeout}s.")
        if "error" in msg:
            raise ComputerError(f"Driver error: {msg['error'].get('message')}")
        return msg.get("result") or {}

    async def call(self, tool, args=None, timeout=DRIVER_TIMEOUT_S, _retry=True):
        await self._ensure()
        point = cua_proxy.cursor_point(tool, args, self.frames)
        if point:
            try:
                await self._cursor(point)
            except ComputerError:  # (the act matters, not its cursor)
                self.cursor_on = False
        r = await self._rpc("tools/call", {"name": tool, "arguments": args or {}}, timeout)
        text = " ".join(c.get("text", "") for c in r.get("content") or [] if c.get("type") == "text").strip()
        if r.get("isError"):
            if _retry and SESSION_ENDED.search(text):
                # The driver ends an idle session and ordinary calls never revive it (seen 2026-09-25 after a quiet spell).
                try:
                    await self._rpc("tools/call", {"name": "start_session", "arguments": {}}, timeout)
                except ComputerError:
                    await self.close()  # last resort: a fresh driver process gets a fresh session
                return await self.call(tool, args, timeout, _retry=False)
            raise ComputerError(text or f"{tool} failed")
        out = r.get("structuredContent")
        if tool == "get_window_state":
            cua_proxy.frames_of(r, self.frames)
        return out if isinstance(out, dict) else {"text": text}

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None


# -- installed apps ---------------------------------------------------------------------------------------------------
# What people call an app -> words in its Start-menu name. Anything else is matched against the real list.
ALIASES = {
    "chrome": "google chrome", "google chrome": "google chrome", "vs code": "visual studio code", "vscode": "visual studio code",
    "code": "visual studio code", "visual studio code": "visual studio code", "terminal": "windows terminal", "the terminal": "windows terminal",
    "cmd": "command prompt", "command prompt": "command prompt", "powershell": "windows powershell", "explorer": "file explorer",
    "file explorer": "file explorer", "files": "file explorer", "file manager": "file explorer", "notepad": "notepad", "calculator": "calculator",
    "calc": "calculator", "settings": "settings", "windows settings": "settings", "control panel": "control panel", "task manager": "task manager",
    "edge": "microsoft edge", "firefox": "mozilla firefox", "brave": "brave", "word": "word", "excel": "excel", "powerpoint": "powerpoint",
    "outlook": "outlook", "teams": "microsoft teams", "whatsapp": "whatsapp", "spotify": "spotify", "discord": "discord", "obsidian": "obsidian",
    "claude": "claude", "claude desktop": "claude", "chatgpt": "chatgpt", "chat gpt": "chatgpt", "copilot": "copilot", "antigravity": "antigravity ide",
    "kiro": "kiro", "cursor": "cursor", "postman": "postman", "github desktop": "github desktop", "android studio": "android studio",
    "intellij": "intellij idea", "paint": "paint", "photos": "photos", "camera": "camera", "clock": "clock", "snipping tool": "snipping tool",
    "store": "microsoft store", "microsoft store": "microsoft store", "mail": "outlook", "calendar": "calendar",
}
# Store apps whose window title doesn't carry their name: match them by process instead.
PROCESS_HINTS = {"windows terminal": "windowsterminal.exe", "terminal": "windowsterminal.exe", "settings": "systemsettings.exe",
                 "windows notepad": "notepad.exe", "notepad": "notepad.exe", "file explorer": "explorer.exe", "explorer": "explorer.exe"}


def web_app(name):
    """The owner's installed web app called `name` (a Chrome or Edge app, e.g. their YouTube app: its Start-menu shortcut runs
    chrome_proxy.exe --profile-directory=… --app-id=…), as (that program, its arguments), or None. Read from the shortcut, so it opens
    in their own profile, signed in as they are (the owner, 2026-10-20: "if the app is available then in the app only")."""
    if not WIN:
        return None
    import glob
    base = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    for lnk in sorted(glob.glob(str(base / "**" / f"{glob.escape(name)}.lnk"), recursive=True)):
        try:
            import pythoncom
            from win32com.client import Dispatch
            pythoncom.CoInitialize()
            sc = Dispatch("WScript.Shell").CreateShortcut(lnk)
            target, args = sc.TargetPath or "", sc.Arguments or ""
        except Exception:  # (pywin32 missing, or a shortcut that can't be read: not this app)
            continue
        if Path(target).name.lower() in ("chrome_proxy.exe", "msedge_proxy.exe") and "--app-id=" in args and os.path.isfile(target):
            return target, args
    return None


def open_web_app(app, url):
    """That web app (web_app) opened at `url` in its own window: Chrome's own way to start one of its apps at an address in its scope."""
    import subprocess
    exe, args = app
    subprocess.Popen(f'"{exe}" {args} --app-launch-url-for-shortcuts-menu-item="{url}"', close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the app outlives Monday Core


def store_alias(*names):
    """A Store app's launch alias on PATH (…\\WindowsApps\\Spotify.exe) for one of these names, or None. Only those: any other
    exe on PATH is a tool, not the app the owner named (2026-10-15: a misheard "open note where" started System32's where.exe)."""
    for n in names:
        p = n and shutil.which(f"{n}.exe")
        if p and "\\windowsapps\\" in p.lower():
            return p
    return None


def _norm(s):
    s = re.sub(r"[^\w\s.+#-]", " ", (s or "").lower())
    s = re.sub(r"^(?:the|my|a|an)\s+|\s+(?:app|application|program)$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


# The recognizer hears app names as they sound: "ChatGPT" came in as "chatgbd", "jetgbt" and "check GPT" (the owner's log, 2026-10-06).
# A sound-alike key (Soundex-style, the first letter in its class too) catches the first two; it is tried last, and only for a name of
# five letters or more, so a short or half-heard word never opens the wrong app.
_SOUND = {**{c: "1" for c in "bfpv"}, **{c: "2" for c in "cgjkqsxz"}, **{c: "3" for c in "dt"}, "l": "4", "m": "5", "n": "5", "r": "6"}
SOUND_MIN_LETTERS = 5


def sound_key(s):
    """A Soundex-style key of a whole name: consonant classes, adjacent repeats collapsed, vowels and h/w/y dropped."""
    out, last = [], None
    for ch in (s or "").lower():
        code = _SOUND.get(ch)
        if ch in "aeiou ":
            last = None  # a vowel separates two consonants of the same class
            continue
        if code and code != last:
            out.append(code)
        if code:
            last = code
    return "".join(out)


def sounds_like(a, b):
    letters = sum(ch.isalpha() for ch in a or "")
    return letters >= SOUND_MIN_LETTERS and sound_key(a) == sound_key(b) and abs(letters - sum(ch.isalpha() for ch in b or "")) <= 2


async def start_apps():
    """Windows' own Start-menu list as {launch id: name}. The driver guesses "<package>!App" for every Store app, but the part
    after "!" is the app's own: Spotify's is "!Spotify", Claude's "!Claude" (2026-10-01: "open Spotify" failed 8 times on that guess)."""
    code, out, _ = await probes.run("powershell", "-NoProfile", "-NonInteractive", "-Command",
                                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
                                    timeout=START_APPS_TIMEOUT_S)
    try:
        rows = json.loads(out.lstrip("﻿")) if code == 0 else []
    except ValueError:
        rows = []
    return {r["AppID"]: r["Name"] for r in (rows if isinstance(rows, list) else [rows]) if r.get("AppID") and r.get("Name")}


def merge_start(items, start):
    """The driver's app list plus the apps only Windows' Start list has, launched by their Start id as Explorer does (2026-10-16: the
    driver listed VLC once, under its reset shortcut's name, so "open VLC" would have run vlc.exe --reset-config). Store apps too, as
    `uwp` with their package family (windows_for), unless the driver listed that package: 2026-10-26, with the driver's list empty, Spotify
    wasn't in the index and "open Spotify" went to open.spotify.com. Not added: web links, and Windows' Microsoft.AutoGenerated ids
    (shortcuts that carry arguments, like that reset)."""
    have = {_norm(a["name"]) for a in items}
    families = {(a.get("bundle_id") or "").lower() for a in items if a.get("kind") == "uwp"}
    extra = [{"name": name, "launch_path": f"shell:appsFolder\\{aid}", "start_id": aid,
              **({"kind": "uwp", "bundle_id": aid.split("!")[0]} if "!" in aid else {"kind": "desktop"})} for aid, name in start.items()
             if _norm(name) not in have and aid.split("!")[0].lower() not in families
             and not aid.lower().startswith(("microsoft.autogenerated.", "http:", "https:"))]
    return items + extra


# A program's maintenance shortcuts: never what its plain name means, only what the owner names ("reset VLC", "uninstall Node.js")
MAINTENANCE = re.compile(r"\b(?:reset|uninstall|uninstaller|repair|safe\s+mode|skinned|documentation|readme|release\s+notes|licen[cs]e|"
                         r"changelog|manual)\b", re.I)


def store_launch(app, start):
    """A Store app's launch path with its real id from the Start list, unless the driver's guess is already one of them."""
    ids = [i for i in start if i.lower().startswith((app.get("bundle_id") or "?").lower() + "!")]
    if not ids or (app.get("launch_path") or "").split("\\")[-1].lower() in (i.lower() for i in ids):
        return app.get("launch_path")
    best = max(ids, key=lambda i: difflib.SequenceMatcher(None, _norm(start[i]), _norm(app["name"])).ratio())  # a package can hold several apps
    return f"shell:appsFolder\\{best}"


class Apps:
    def __init__(self, driver):
        self.driver = driver
        self.items = None
        self.at = 0.0
        self._loading = None

    async def _load(self):
        data, start = await asyncio.gather(self.driver.call("list_apps", {}, timeout=LIST_APPS_TIMEOUT_S), start_apps(), return_exceptions=True)
        data, start = (data if isinstance(data, dict) else {}), (start if isinstance(start, dict) else {})  # (the driver down: Start's alone)
        items = [a for a in data.get("apps") or [] if a.get("name") and (a.get("launch_path") or a.get("bundle_id"))]
        if not items and not start:  # (nothing read: the last good list stays, and no app is said to be missing)
            raise ComputerError("Neither the screen driver nor Windows' Start list named the installed apps.")
        said = {k.lower(): v for k, v in start.items()}
        for a in items:
            if a.get("kind") == "uwp":
                a["launch_path"] = store_launch(a, start)
                short = said.get((a["launch_path"] or "").split("\\")[-1].lower())
                if short and _norm(a["name"]).startswith(_norm(short) + " "):  # the Store's "Spotify - Music and Podcasts" is "Spotify" in the Start menu
                    a["name"] = short
        return merge_start(items, start)

    async def all(self, refresh=False):
        if refresh or self.items is None or time.monotonic() - self.at > APPS_TTL_S:
            if self._loading is None or self._loading.done():
                self._loading = asyncio.ensure_future(self._load())
                self._loading.add_done_callback(self._loaded)
            if self.items is not None and not refresh:  # an old list answers now; the new one arrives in the background (it takes 1-5 s,
                return self.items                        # and every "open X" after a quiet quarter hour waited for it)
            self.items = await asyncio.shield(self._loading)
            self.at = time.monotonic()
        return self.items

    def _loaded(self, f):
        if f.cancelled():
            return
        if f.exception() is None:
            self.items = f.result()
        self.at = time.monotonic()  # a failed refresh keeps the old list and is tried again after APPS_TTL_S

    async def near(self, query):
        """The installed app closest to a name that matched none, when it is NEAR_MIN close by its whole name (spaces ignored: "what sap"
        -> WhatsApp) or one of its own words ("photo shop" -> Microsoft Photos), else None. Offered as a question, never opened."""
        q = re.sub(r"[^\w+#]", "", (query or "").lower())  # as said: _norm would cut "what's app" to "what s"

        def close(n):  # 1 or more: past its bar (a word alone must be closer: "clod" isn't Visual Studio Code's "code")
            return max([difflib.SequenceMatcher(None, q, n.replace(" ", "")).ratio() / NEAR_MIN] +
                       [difflib.SequenceMatcher(None, q, w).ratio() / NEAR_WORD for w in n.split() if len(w) > 3 and w not in VENDORS])
        score, name = max(((close(_norm(a["name"])), a["name"]) for a in await self.all() if not MAINTENANCE.search(_norm(a["name"]))), default=(0, None))
        return name if score >= 1 else None

    async def find(self, query, scored=False):
        """Best installed app for what the owner said, or None. Exact names beat aliases beat word matches. scored: (app, its score), the
        score 100 for its whole name or an alias, 85 for its first or last word, less for a word in it, a typo or a sound-alike."""
        q = re.sub(r"\s+(?:app|application|program|software)$", "", re.sub(r"\s+(?:in|on)\s+(?:my\s+|the\s+|this\s+)?"
                                                                               r"(?:laptop|computer|pc|desktop|system|machine)$", "", _norm(query)))
        if not q or not (set(q.split()) - FILLER_WORDS):  # "open for", "open me": a half-heard word is no app (it opened Norton 360 for Gamers)
            return (None, 0) if scored else None
        want = ALIASES.get(q, q)
        best, best_score = None, 0
        upkeep = bool(MAINTENANCE.search(q))
        for a in await self.all():
            n = _norm(a["name"])
            if not upkeep and MAINTENANCE.search(n):
                continue  # "vlc" is never VLC's "reset preferences and cache files"
            if n == q or n == want:
                score = 100
            elif n.startswith(want + " ") or n.endswith(" " + want) or n.startswith(q + " "):
                score = 85
            elif all(w in n.split() for w in want.split()):
                score = 70
            else:
                ratio = difflib.SequenceMatcher(None, want, n).ratio()
                score = 60 * ratio if ratio >= TYPO_MIN else 50 if sounds_like(want, n) else 0  # last: the name as it sounds ("chatgbd")
            if score:
                score += 5 if a.get("kind") == "desktop" else 0     # a real .exe over a Store listing of the same app
                score += 3 if a.get("running") else 0
                score -= 10 if "(x86)" in a["name"] or " ise" in n else 0
            if score > best_score:
                best, best_score = a, score
        if best is None and q not in ALIASES:  # a typo of a well-known name ("notpad": Windows' is "Windows Notepad", too far for the ratio)
            typo = difflib.get_close_matches(q, list(ALIASES), n=1, cutoff=TYPO_MIN)
            if typo:
                return await self.find(typo[0], scored)
        return (best, best_score) if scored else best


FILLER_WORDS = {"a", "an", "the", "my", "me", "for", "to", "of", "and", "or", "in", "on", "at", "it", "this", "that", "up", "with",
                "please", "now", "app", "so", "is", "be", "you", "i", "uh", "um", "okay", "ok"}


def apps_near(text, items):
    """The installed apps the words come close to (Monday.jev_context, for Jev): a 1-3-word span of them is an app's name or one of its
    words, or difflib finds the two 0.8 alike. difflib's own bounds go first, as its get_close_matches does (a length check, then
    quick_ratio): a ratio for every span and app held Core's event loop 0.5-2.3 s for a spoken sentence, 38 s for a pasted brief (2026-10-24)."""
    words = _norm(text).split()
    spans = {" ".join(words[i:i + n]) for n in (1, 2, 3) for i in range(len(words) - n + 1)} - FILLER_WORDS
    near, sm = [], difflib.SequenceMatcher()
    for a in items or []:
        n = _norm(a["name"])
        own = set(n.split())
        sm.set_seq2(n)
        for s in spans:
            if s == n or len(s) > 3 and s in own:
                near.append(a["name"])
                break
            if len(s) > 3 and 3 * min(len(s), len(n)) >= 2 * max(len(s), len(n)):  # (difflib's real_quick_ratio of 0.8)
                sm.set_seq1(s)
                if sm.quick_ratio() >= 0.8 and sm.ratio() >= 0.8:
                    near.append(a["name"])
                    break
    return near


def exe_of(app):
    """C:\\...\\chrome.exe for a desktop app entry (from its launch_path), else None."""
    lp = (app or {}).get("launch_path") or ""
    m = re.match(r'^"([^"]+\.exe)"|^(\S+?\.exe)\b|^(.+?\.exe)(?:\s|$)', os.path.expandvars(lp), re.I)
    return next((g for g in m.groups() if g), None) if m else None


def launch(app, args=None):
    """Start an app the way Explorer does (ShellExecute). Measured: 0.6 s to a Calculator window, where the driver's
    launch_app took 1.9-7 s waiting for its own pid capture. Desktop apps keep their shortcut's arguments."""
    lp = os.path.expandvars(app.get("launch_path") or "")
    extra = " ".join(f'"{a}"' if " " in a and not a.startswith('"') else a for a in (args or []))
    exe = exe_of(app)
    try:
        if exe:
            rest = lp[lp.lower().index(exe.lower()) + len(exe):].lstrip('"').strip() if exe.lower() in lp.lower() else ""
            os.startfile(exe, arguments=f"{rest} {extra}".strip())
        elif lp.lower().startswith("shell:appsfolder"):
            os.startfile(lp, arguments=extra) if extra else os.startfile(lp)
        elif app.get("bundle_id"):
            os.startfile(f"shell:appsFolder\\{app['bundle_id']}" + ("" if "!" in app["bundle_id"] else "!App"))
        else:
            raise ComputerError(f"I don't know how to start {app.get('name')}.")
    except OSError as e:  # a failed step with a recovery (executor._recover), never a crash read out as "FileNotFoundError: [WinError 2]…"
        raise ComputerError(f"Windows couldn't start {app.get('name')}: {(e.strerror or str(e)).rstrip('.')}.") from e


# -- screen: grab + OCR ------------------------------------------------------------------------------------------------
def grab(rect=None):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=rect, all_screens=True)


_ocr_engine = None


async def ocr(img):
    """Windows' built-in OCR: [{"text", "x", "y", "w", "h"}] per line, in image pixels. Local; nothing leaves the PC."""
    global _ocr_engine
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    if _ocr_engine is None:
        _ocr_engine = OcrEngine.try_create_from_user_profile_languages()
        if _ocr_engine is None:
            raise ComputerError("Windows OCR has no language installed for this user.")
    img = img.convert("RGBA")
    scale = 1.0
    limit = OcrEngine.max_image_dimension
    if max(img.size) > limit:
        scale = limit / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    w = DataWriter()
    w.write_bytes(img.tobytes("raw", "BGRA"))
    bmp = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height)
    res = await _ocr_engine.recognize_async(bmp)
    lines = []
    for ln in res.lines:
        words = list(ln.words)
        if not words:
            continue
        xs = [wd.bounding_rect.x for wd in words]
        ys = [wd.bounding_rect.y for wd in words]
        x2 = [wd.bounding_rect.x + wd.bounding_rect.width for wd in words]
        y2 = [wd.bounding_rect.y + wd.bounding_rect.height for wd in words]
        lines.append({"text": ln.text, "x": min(xs) / scale, "y": min(ys) / scale, "w": (max(x2) - min(xs)) / scale,
                      "h": (max(y2) - min(ys)) / scale,
                      "words": [{"text": wd.text, "x": wd.bounding_rect.x / scale, "y": wd.bounding_rect.y / scale,
                                 "w": wd.bounding_rect.width / scale, "h": wd.bounding_rect.height / scale} for wd in words]})
    return sorted(lines, key=lambda l: (round(l["y"] / 12), l["x"]))


def find_text(lines, label):
    """The OCR box that best matches a label ("login" also finds "Log in"): exact line, then word run, then fuzzy."""
    want = _norm(label).replace(" ", "")
    if not want:
        return None
    best, best_score = None, 0.0
    for ln in lines:
        words = ln["words"]
        for i in range(len(words)):
            for j in range(i, min(i + 5, len(words))):
                run = words[i:j + 1]
                got = re.sub(r"[^\w]", "", "".join(w["text"] for w in run).lower())
                if not got:
                    continue
                score = 1.0 if got == want else difflib.SequenceMatcher(None, want, got).ratio()
                if score > best_score:
                    x, y = run[0]["x"], min(w["y"] for w in run)
                    best, best_score = {"text": " ".join(w["text"] for w in run), "x": x, "y": y,
                                        "w": run[-1]["x"] + run[-1]["w"] - x, "h": max(w["y"] + w["h"] for w in run) - y}, score
    return best if best_score >= 0.84 else None


# -- the web ----------------------------------------------------------------------------------------------------------
SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "github": "https://github.com", "stackoverflow": "https://stackoverflow.com", "stack overflow": "https://stackoverflow.com",
         "wikipedia": "https://www.wikipedia.org", "amazon": "https://www.amazon.com", "reddit": "https://www.reddit.com",
         "twitter": "https://x.com", "x": "https://x.com", "linkedin": "https://www.linkedin.com", "facebook": "https://www.facebook.com",
         "instagram": "https://www.instagram.com", "netflix": "https://www.netflix.com", "maps": "https://maps.google.com",
         "google maps": "https://maps.google.com", "drive": "https://drive.google.com", "google drive": "https://drive.google.com",
         "npm": "https://www.npmjs.com", "pypi": "https://pypi.org", "bing": "https://www.bing.com", "duckduckgo": "https://duckduckgo.com",
         "whatsapp web": "https://web.whatsapp.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
         "gemini": "https://gemini.google.com", "news": "https://news.google.com", "spotify": "https://open.spotify.com"}
SEARCH = {None: "https://www.google.com/search?q={}", "google": "https://www.google.com/search?q={}",
          "youtube": "https://www.youtube.com/results?search_query={}", "bing": "https://www.bing.com/search?q={}",
          "duckduckgo": "https://duckduckgo.com/?q={}", "github": "https://github.com/search?q={}",
          "stackoverflow": "https://stackoverflow.com/search?q={}", "stack overflow": "https://stackoverflow.com/search?q={}",
          "wikipedia": "https://en.wikipedia.org/w/index.php?search={}", "amazon": "https://www.amazon.com/s?k={}",
          "reddit": "https://www.reddit.com/search/?q={}", "npm": "https://www.npmjs.com/search?q={}", "pypi": "https://pypi.org/search/?q={}",
          "maps": "https://www.google.com/maps/search/{}", "google maps": "https://www.google.com/maps/search/{}",
          "images": "https://www.google.com/search?tbm=isch&q={}", "google images": "https://www.google.com/search?tbm=isch&q={}",
          "bing images": "https://www.bing.com/images/search?q={}",
          "news": "https://news.google.com/search?q={}", "google news": "https://news.google.com/search?q={}",
          "twitter": "https://x.com/search?q={}", "x": "https://x.com/search?q={}", "linkedin": "https://www.linkedin.com/search/results/all/?keywords={}",
          "spotify": "https://open.spotify.com/search/{}"}
BROWSER_PROGIDS = {"chromehtml": "google chrome", "msedgehtm": "microsoft edge", "firefoxurl": "mozilla firefox",
                   "bravehtml": "brave", "operastable": "opera"}
# Where each browser installs, and the registry key that records its version (Chromium's BLBeacon).
_BROWSER_HOMES = {
    "google chrome": ([r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                       r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"], r"Software\Google\Chrome\BLBeacon"),
    "microsoft edge": ([r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
                       r"Software\Microsoft\Edge\BLBeacon"),
    "brave": ([r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
               r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"], r"Software\BraveSoftware\Brave-Browser\BLBeacon"),
    "mozilla firefox": ([r"%ProgramFiles%\Mozilla Firefox\firefox.exe", r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"], None),
    "opera": ([r"%LOCALAPPDATA%\Programs\Opera\opera.exe"], None),
}
_BROWSER_NAMES = {"chrome": "google chrome", "edge": "microsoft edge", "firefox": "mozilla firefox"}


def browser_info(name):
    """{"name", "exe", "version"} of an installed browser, from its standard install folders (no driver, no network).
    exe is None when it isn't installed there; version is None when the browser doesn't record one."""
    name = _BROWSER_NAMES.get((name or "").lower(), (name or "").lower())
    paths, beacon = _BROWSER_HOMES.get(name, ([], None))
    exe = next((p for p in (os.path.expandvars(x) for x in paths) if os.path.isfile(p)), None)
    version = None
    if exe and beacon and WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, beacon) as k:
                version = winreg.QueryValueEx(k, "version")[0]
        except OSError:
            pass
    return {"name": name, "exe": exe, "version": version}
_URLISH = re.compile(r"^(?:https?://|www\.)\S+$|^[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|dev|ai|app|co|in|edu|gov|me|tv|gg|uk|us)(?:/\S*)?$", re.I)


def url_for(target):
    t = target.strip().strip("\"'")
    if _URLISH.match(t):
        return t if t.lower().startswith("http") else "https://" + t
    return SITES.get(_norm(t))


def app_name(target):
    """The app a plain "open X" names, or None when X is an address, a folder, a path or a file, or names nothing: what Monday._installed
    looks for among the installed apps before anything runs (the owner, 2026-10-26: "first check that I have installed that application")."""
    t = (target or "").strip().strip("\"'")
    if not t or _URLISH.match(t) or special_folder(t) or re.search(r"[\\/]|\.\w{2,4}$", t) or not (set(_norm(t).split()) - FILLER_WORDS):
        return None
    return t


def site_word(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    parts = [p for p in host.split(".") if p not in ("www", "com", "org", "net", "io", "co", "in", "uk", "mail", "open", "news")]
    return (parts[0] if parts else host).lower()


def default_browser_name():
    if not WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0].lower()
        return next((v for p, v in BROWSER_PROGIDS.items() if prog.startswith(p)), None)
    except OSError:
        return None


# -- folders ----------------------------------------------------------------------------------------------------------
def special_folder(target):
    t = _norm(target)
    t = re.sub(r"\s+(?:folder|directory|dir)$", "", t)
    home = Path.home()
    known = {"downloads": home / "Downloads", "download": home / "Downloads", "documents": home / "Documents",
             "desktop": home / "Desktop", "pictures": home / "Pictures", "photos folder": home / "Pictures", "music": home / "Music",
             "videos": home / "Videos", "home": home, "user folder": home}
    if t in known:
        p = known[t]
        onedrive = home / "OneDrive" / p.name  # Desktop/Documents/Pictures often live under OneDrive
        return str(onedrive if not p.exists() and onedrive.exists() else p)
    raw = os.path.expandvars(os.path.expanduser(target.strip().strip("\"'")))
    if re.match(r"^[a-z]:[\\/]|^\\\\", raw, re.I) and Path(raw).exists():
        return raw
    return None


# -- Computer: every action the executor or a model may ask for ------------------------------------------------------------
SAVE_DIALOG = re.compile(r"^\s*save(?:\s+(?:as|file\s+as|a\s+copy))?\s*$", re.I)


class Computer:
    look = None  # Jev's look at a window for what find() can't name or read (core/jev.py locate), set by Monday

    def __init__(self, own_name=lambda: "Monday"):
        self.driver = Driver()
        self.apps = Apps(self.driver)
        self.hands = asyncio.Lock()   # one pair of hands: two plans must not type into each other's windows
        self.own_name = own_name

    # -- windows ---------------------------------------------------------------------------------------------------
    def is_own(self, w):
        """Monday's own pages and console are never the window a command is about."""
        name = re.escape(self.own_name())
        return (bool(re.match(rf"^(?:\(\d+\)\s+)?{name}(?:\s+[·-]\s+.*)?\s+-\s+\S", w.title)) or "python -m core" in w.title
                or (w.title == self.own_name() and (getattr(w, "process", "") or "").lower() in ("pythonw.exe", "python.exe", "monday.exe")))  # the desktop app's own windows

    def front(self):
        """The window the owner is looking at: the top one that isn't Monday's own HUD, phone page or console."""
        return next((w for w in windows() if not self.is_own(w)), None)

    def windows_for(self, target, app=None):
        """Top-level windows of an app: by its .exe, else by its name in the title (Store apps run under a host)."""
        exe = (Path(exe_of(app)).name.lower() if exe_of(app) else PROCESS_HINTS.get(_norm((app or {}).get("name") or target)))
        words = [w for w in _norm((app or {}).get("name") or target).split() if len(w) > 2 and w not in ("windows", "microsoft")]
        fam = ((app or {}).get("bundle_id") or "").lower() if (app or {}).get("kind") == "uwp" else ""
        out = []
        for w in windows():
            if self.is_own(w):
                continue
            title = w.title.lower()
            if (exe and w.process.lower() == exe) or (fam and family(w.pid) == fam) or (words and all(x in title for x in words)):
                out.append(w)
        return out

    def window_of(self, seen, max_age=WORKED_IN_S):
        """The window a step of Monday's worked in a moment ago (its result's `window`, stamped `at`), if it is still open, else None:
        what "save it" means once the owner's own app has come to the front (2026-10-16: it pressed Ctrl+Shift+S in the Claude app)."""
        if not seen or time.time() - (seen.get("at") or 0) > max_age:
            return None
        return next((w for w in windows() if w.hwnd == seen.get("hwnd") and w.pid == seen.get("pid")), None)

    async def wait_window(self, pred, timeout=WINDOW_WAIT_S):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            w = next((w for w in windows() if pred(w)), None)
            if w:
                return w
            await asyncio.sleep(POLL_S)
        return None

    # -- open --------------------------------------------------------------------------------------------------------
    async def open(self, target, app=None, cwd=None):
        """'Chrome', 'youtube', 'github.com', 'downloads', 'C:\\x', 'terminal': URL, folder, installed app or website."""
        t = (target or "").strip()
        if app:
            if url_for(t):
                return await self.open_url(url_for(t), browser=app)
            folder = special_folder(t)
            if folder:
                return await self.open_app(app, args=[folder])
        url = url_for(t) if _URLISH.match(t) else None
        if url:
            return await self.open_url(url)
        folder = special_folder(t)
        if folder:
            return await self.open_path(folder)
        if _norm(t) in ALIASES and ALIASES[_norm(t)] == "windows terminal" and cwd:
            return await self.open_app(t, args=["-d", cwd])
        if not (set(_norm(t).split()) - FILLER_WORDS):  # "open for": a half-heard sentence names nothing to open
            return failed(f"I only caught “open {t}”. Open what?")
        found = await self.apps.find(t)
        learned, said = getattr(self, "learn", None), _norm(t)  # core/learn.py: names as the recognizer heard them, from past opens
        if not found and learned and learned.app_alias(said):
            found = await self.apps.find(learned.app_alias(said))
            if not found:
                learned.forget_app(said)
        if found:
            r = await self.open_app(found)
            if learned:
                if r["ok"]:
                    n = _norm(found["name"])
                    learned.opened(said, found["name"], exact=said == n or ALIASES.get(said) == n or n.startswith(said) or said in n.split(),
                                   sounds_like=sounds_like)
                    if learned.app_alias(said):
                        learned.used_app(said)
                elif learned.app_alias(said):
                    learned.forget_app(said)
            return r
        url = url_for(t)
        if url:
            return await self.open_url(url)
        if learned:
            learned.failed_open(said)
        near = await self.apps.near(t)  # (a misheard or mistyped name: asked, never guessed and started)
        return failed(f"{t} isn't installed or couldn't be located." + (f" Did you mean {near}?" if near else ""), near=near)

    async def open_app(self, app, args=None):
        if isinstance(app, str):
            name = app
            app = await self.apps.find(app)
            if not app:
                return failed(f"{name} isn't installed or couldn't be located.")
        existing = self.windows_for(app["name"], app)
        if existing and not args:
            ok = activate(existing[0].hwnd)
            return done(f"{app['name']} was already open; {'brought it to the front' if ok else 'Windows kept another window in front'}.",
                        verified=ok, app=app["name"], window=existing[0].view())
        before = {w.hwnd for w in windows()}
        launch(app, args)
        new = await self.wait_window(lambda w: w.hwnd not in before and not self.is_own(w) and w in self.windows_for(app["name"], app))
        if not new and args:  # e.g. VS Code reused a window and retitled it instead of opening a new one
            new = next(iter(self.windows_for(app["name"], app)), None)
        if not new:
            return failed(f"Started {app['name']}, but no window of it appeared within {WINDOW_WAIT_S}s.", app=app["name"])
        ok = activate(new.hwnd)
        return done(f"Opened {app['name']}." + ("" if ok else " Windows kept another window in front."), verified=True,
                    app=app["name"], window=new.view(), focused=ok)

    async def open_alt(self, target):
        """Launch an app another way, when the installed-app list failed: Windows' own App Paths registry. Only an app
        registered there is started (a blind `start <name>` would pop up a "Windows cannot find" dialog)."""
        import winreg
        words = re.sub(r"\.exe$", "", _norm(target), flags=re.I).split()
        exe = None
        # the whole name; its last word only after a maker's name ("google chrome" -> chrome). Never any one word of a phrase on its own
        # (2026-10-20: "note where", misheard, started where.exe)
        tries = ["".join(words)] + ([words[-1]] if len(words) > 1 and all(x in VENDORS for x in words[:-1]) else [])
        for name in dict.fromkeys(tries):
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}.exe") as k:
                        exe = winreg.QueryValue(k, None).strip('"')
                        break
                except OSError:
                    continue
            if exe:
                break
        if not exe or not os.path.isfile(exe):  # Store apps aren't in App Paths; many put an alias on PATH (…\WindowsApps\Spotify.exe)
            exe = store_alias(*tries)
        if not exe:
            return failed(f"Windows has no other way registered to start {target}.")
        before = {w.hwnd for w in windows()}
        os.startfile(exe)
        stem = Path(exe).name.lower()
        w = await self.wait_window(lambda w: w.hwnd not in before and not self.is_own(w) and w.process.lower() == stem)
        if not w:
            return failed(f"Started {Path(exe).name} through Windows' app registry, but no window of it appeared within {WINDOW_WAIT_S}s.")
        activate(w.hwnd)
        return done(f"Opened {target} through Windows' app registry ({Path(exe).name}).", verified=True, window=w.view())

    async def open_url(self, url, browser=None):
        url = url_for(url) or url
        name = browser or default_browser_name() or "google chrome"
        app = await self.apps.find(name)
        if not app:
            return failed(f"{name.title()} isn't installed or couldn't be located.")
        word = site_word(url)
        titles = {w.hwnd: w.title for w in self.windows_for(app["name"], app)}
        launch(app, [url])
        w = await self.wait_window(lambda w: not self.is_own(w) and w in self.windows_for(app["name"], app)
                                   and word in w.title.lower() and titles.get(w.hwnd) != w.title, timeout=URL_WAIT_S)
        if w:
            activate(w.hwnd)
            return done(f"Opened {url} in {app['name']}.", verified=True, url=url, window=w.view())
        w = next(iter(self.windows_for(app["name"], app)), None)
        if w:
            activate(w.hwnd)
        return done(f"Asked {app['name']} to open {url}; the page title didn't confirm it within {URL_WAIT_S}s.",
                    verified=False, url=url)

    async def search(self, query, site=None, browser=None):
        tpl = SEARCH.get((site or "").lower() or None) or SEARCH[None]
        return await self.open_url(tpl.format(urllib.parse.quote_plus(query)), browser)

    async def open_path(self, path, editor=None):
        p = Path(path)
        if not p.exists():
            return failed(f"{path} doesn't exist on this computer.")
        if editor:
            return await self.open_app(editor, args=[str(p)])
        before = {w.hwnd for w in windows()}
        os.startfile(str(p))  # Explorer for folders, the default app for files
        want = p.name.lower() or str(p).lower()
        w = await self.wait_window(lambda w: w.hwnd not in before and want in w.title.lower() and not self.is_own(w), timeout=6)
        w = w or next((w for w in windows() if want in w.title.lower() and not self.is_own(w)), None)
        if w:
            activate(w.hwnd)
        return done(f"Opened {p}.", verified=bool(w), window=w.view() if w else None)

    async def open_project(self, path, editor="visual studio code"):
        p = Path(path)
        if not p.is_dir():
            return failed(f"The project folder {path} doesn't exist.")
        app = await self.apps.find(editor or "visual studio code")
        if not app:
            return failed(f"{(editor or 'VS Code').title()} isn't installed or couldn't be located.")
        before = {w.hwnd: w.title for w in windows()}
        launch(app, [str(p)])
        folder = p.name.lower()
        w = await self.wait_window(lambda w: folder in w.title.lower() and before.get(w.hwnd) != w.title and not self.is_own(w)
                                   and w in self.windows_for(app["name"], app), timeout=WINDOW_WAIT_S + 5)
        w = w or next((w for w in self.windows_for(app["name"], app) if folder in w.title.lower()), None)
        if not w:
            return failed(f"Asked {app['name']} to open {p.name}, but no window titled {p.name} appeared.", app=app["name"])
        activate(w.hwnd)
        return done(f"Opened {p.name} in {app['name']}.", verified=True, app=app["name"], window=w.view())

    # -- windows: close, focus, minimize ---------------------------------------------------------------------------------
    async def target_window(self, target):
        if not target:
            return self.front(), None
        app = await self.apps.find(target)
        wins = self.windows_for(target, app)
        return (wins[0] if wins else None), app

    async def close(self, target=None):
        """An app's windows, or with no target the window in front."""
        app = await self.apps.find(target) if target else None
        wins = self.windows_for(target, app) if target else [w for w in (self.front(),) if w]
        name = (app or {}).get("name") or target or (wins[0].title if wins else "a window")
        if not wins:
            return failed(f"{name} isn't open.") if app else failed(f"I can't find an open window for {target}." if target else "No app window is open.")
        for w in wins:
            user32.PostMessageW(w.hwnd, WM_CLOSE, 0, 0)  # the app's own close: it can still ask to save
        end = time.monotonic() + CLOSE_WAIT_S
        left = wins
        while left and time.monotonic() < end:
            await asyncio.sleep(POLL_S)
            alive = {w.hwnd for w in windows()}
            left = [w for w in wins if w.hwnd in alive]
        if left:
            return done(f"{name} is still open: it may be asking you to save something. I left it for you.", verified=False, app=name)
        return done(f"Closed {name}.", verified=True, app=name)

    async def focus(self, target):
        w, app = await self.target_window(target)
        if not w:
            return failed(f"{(app or {}).get('name') or target} isn't open.")
        ok = activate(w.hwnd)
        return done(f"Switched to {w.title}." if ok else f"Windows didn't let me bring {w.title} to the front.", verified=ok, window=w.view())

    async def window(self, op, target=None):
        w, app = await self.target_window(target)
        if not w:
            return failed(f"{target or 'That window'} isn't open.")
        user32.ShowWindow(w.hwnd, {"minimize": SW_MINIMIZE, "maximize": SW_MAXIMIZE, "restore": SW_RESTORE}[op])
        await asyncio.sleep(0.2)
        ok = {"minimize": bool(user32.IsIconic(w.hwnd)), "maximize": bool(user32.IsZoomed(w.hwnd)),
              "restore": not user32.IsIconic(w.hwnd)}[op]
        return done(f"{op.capitalize()}d {w.title}.", verified=ok, window=w.view())

    def list_windows(self):
        wins = [w for w in windows() if not self.is_own(w)]
        if not wins:
            return done("No app windows are open.", verified=True, windows=[])
        lines = [f"{w.title} ({w.process})" for w in wins[:15]]
        return done(f"{len(wins)} window{'s' if len(wins) != 1 else ''} open, front first:\n" + "\n".join(f"• {l}" for l in lines),
                    verified=True, windows=[w.view() for w in wins[:30]])

    # -- look -------------------------------------------------------------------------------------------------------
    async def capture(self, w):
        """Pixels of one window and a function mapping image pixels to screen pixels. Straight from the screen when the
        window is in front, else through the driver, which can capture a window behind others. A minimized window has
        no pixels, so it is shown without taking focus for the capture and minimized again (only read() gets here with
        one: clicks act on the front window)."""
        front = self.front()
        if w.hwnd == user32.GetForegroundWindow() or (front and front.hwnd == w.hwnd):
            l, t, r, b = w.rect
            return grab((l, t, r, b)), lambda x, y: (l + x, t + y)
        tmp = ROOT / "data" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        out = tmp / f"win-{w.hwnd}.png"
        out.unlink(missing_ok=True)
        iconic = bool(user32.IsIconic(w.hwnd))
        if iconic:
            user32.ShowWindow(w.hwnd, SW_SHOWNOACTIVATE)
            await asyncio.sleep(0.4)  # Store apps paint a beat after they're restored
        try:
            l, t, r, b = w.rect
            st = await self.driver.call("get_window_state", {"pid": w.pid, "window_id": w.hwnd, "include_accessibility_tree": False,
                                                             "screenshot_out_file": str(out)})
        finally:
            if iconic:
                user32.ShowWindow(w.hwnd, SW_SHOWMINNOACTIVE)
        if not out.exists():
            raise ComputerError(st.get("screenshot_error") or st.get("text") or f"I couldn't capture {w.title}.")
        from PIL import Image
        with Image.open(out) as im:
            img = im.convert("RGB")
        out.unlink(missing_ok=True)  # a capture is read once; nothing of it stays in data/tmp (2026-10-20: one png per window lingered for weeks)
        if (r - l, b - t) != img.size and r - l > 0 and b - t > 0:
            # the driver downscales to logical pixels; at 1x Windows OCR misses small text, so back to real pixels
            img = img.resize((r - l, b - t), Image.LANCZOS)
        return img, lambda x, y: (l + x, t + y)

    async def read(self, what="screen", app=None):
        """Text on screen, OCR'd locally. what: screen | window | error."""
        if app:
            w, a = await self.target_window(app)
            if not w:
                return failed(f"{(a or {}).get('name') or app} isn't open, so there's nothing of it to read.")
        else:
            w = self.front()
        if not w:
            return failed("No app window is open to read.")
        img, _ = await self.capture(w)
        lines = [l for l in await ocr(img) if len(l["text"].strip()) > 1]
        texts = [l["text"].strip() for l in lines]
        others = [x.title for x in windows() if x.hwnd != w.hwnd and not self.is_own(x)][:5]
        if what == "error":
            hits = [i for i, t in enumerate(texts) if re.search(r"error|exception|failed|failure|traceback|denied|not found|invalid|"
                                                                r"cannot|can't|couldn't|unable|refused|fatal|warning|unexpected", t, re.I)]
            keep = sorted({j for i in hits for j in range(i, min(i + 3, len(texts)))})
            if not keep:
                return done(f"I read {w.title} and found no error message on it.", verified=True, window=w.view(), lines=texts[:OCR_LINES])
            return done(f"In {w.title}:\n" + "\n".join(texts[j] for j in keep[:20]), verified=True, window=w.view(),
                        lines=[texts[j] for j in keep])
        body = "\n".join(texts[:OCR_LINES])
        head = ((f"{w.title}{' (minimized)' if user32.IsIconic(w.hwnd) else ''}:" if app else f"In front: {w.title}.")
                + (f" Also open: {', '.join(others)}." if others and what == "screen" else ""))
        return done(f"{head}\n{body}" if body else f"{head} I couldn't read any text in it.", verified=True, window=w.view(),
                    lines=texts[:OCR_LINES], others=others)

    async def screenshot(self):
        img = grab()
        SHOTS.mkdir(parents=True, exist_ok=True)
        name = f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path = SHOTS / name
        img.save(path, optimize=False)
        ok = path.exists() and path.stat().st_size > 0
        note = " The PC is locked, so it shows the lock screen." if locked() else ""
        return done(f"Screenshot saved ({img.width}×{img.height}).{note}", verified=ok, path=str(path), name=name, size=path.stat().st_size)

    async def whole_screen(self):
        """The whole screen for a look (executor._explain_screen): the lines Windows' OCR reads on it, in the screen's pixels, and its
        picture for a model's eyes (base64 PNG, at most overlay.SHOT_MAX px across). Every screen; nothing is clicked or kept. (Not
        `look`: Monday sets `computer.look` to Jev's locate, which would hide a method of that name.)"""
        import base64
        import io

        from .overlay import fit_size
        img = grab()
        x0, y0 = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # where the picture of every screen starts (SM_X/YVIRTUALSCREEN)
        lines = [{"text": ln["text"].strip(), "x": ln["x"] + x0, "y": ln["y"] + y0, "w": ln["w"], "h": ln["h"]}
                 for ln in await ocr(img) if len(ln["text"].strip()) > 1]
        small, buf = img.convert("RGB").resize(fit_size(*img.size)), io.BytesIO()
        small.save(buf, "PNG")
        w = self.front()  # (the marks belong to it: they fade once the owner moves to another app's window, qtoverlay.Marks)
        return done(f"Read {len(lines)} lines off the screen.", verified=True, lines=lines, size=img.size, origin=(x0, y0),
                    front=w.hwnd if w else None, picture=base64.b64encode(buf.getvalue()).decode())

    async def elements(self, w, query=None, limit=MAX_ELEMENTS):
        """UI Automation elements of a window (name, role, value, token), or [] when it exposes none. The driver calls an
        element's name its `label`; until 2026-10-05 nothing here read that, so every click by name fell back to OCR and
        the goal loop saw only the controls that held a value."""
        try:
            st = await self.driver.call("get_window_state", {"pid": w.pid, "window_id": w.hwnd, "include_screenshot": False,
                                                             "max_elements": limit, **({"query": query} if query else {})})
        except ComputerError:
            return []
        return [{**e, "name": e.get("name") or e.get("label")} for e in st.get("elements") or [] if e.get("element_token")]

    async def find(self, label, w, roles=None):
        """An element by what the owner calls it: UIA names first, then OCR text on the window's pixels."""
        want = _norm(label)
        els = await self.elements(w, query=label.split()[0] if label.split() else None) or await self.elements(w)
        scored = []
        for e in els:
            name = _norm(e.get("name") or "")
            if not name:
                continue
            score = 3 if name == want else 2 if name.startswith(want) or want in name.split() else 1 if want in name else 0
            if score and roles and (e.get("role") or "") in roles:
                score += 1
            if score:
                scored.append((score, e))
        if scored:
            return {"element": max(scored, key=lambda s: s[0])[1]}
        if len(want.split()) > LABEL_WORDS:  # a sentence isn't a control's name: read off the pixels it's most likely the owner's own words in a
            return None                      # chat (2026-10-20: "click Statistics in Monday's own window" clicked that text in the Claude app)
        img, to_screen = await self.capture(w)
        lines = await ocr(img)
        box = find_text(lines, label)
        if box:
            (l, t), (r, b) = to_screen(box["x"], box["y"]), to_screen(box["x"] + box["w"], box["y"] + box["h"])
            return {"point": to_screen(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2), "text": box["text"], "box": (l, t, r - l, b - t)}
        if self.look and not any(looks_secret(l["text"]) for l in lines):  # an icon or a control with no name or text: Jev's look at the
            spot = await self.look(label, img)                               # window (never a window showing a password or key)
            if spot and spot.get("point"):
                rr = spot.get("rect")
                (l, t), (r, b) = (to_screen(rr[0], rr[1]), to_screen(rr[2], rr[3])) if rr else ((0, 0), (0, 0))
                return {"point": to_screen(*spot["point"]), "text": label, "via": "jev", "box": (l, t, r - l, b - t) if rr else None}
        return None

    async def point(self, target, w=None):
        """Dotpals' marks: where `target` is in the window in front, as a rectangle on the screen to circle (UI Automation's bounds, else the
        words read off the window, else Jev's look: find). Nothing is clicked or typed. -> its words and `marks` for SCREEN_MARKS."""
        w = w or self.front()
        if not w:
            return failed("No app window is open to look in.")
        hit = await self.find(target, w)
        if not hit:
            return failed(f"I couldn't find “{target}” in {w.title}.", window=w.view())
        e, box = hit.get("element") or {}, hit.get("box")
        b = e.get("frame") or e.get("bounds") or e.get("rect")
        if isinstance(b, dict) and {"x", "y", "width", "height"} <= set(b):
            box = (b["x"], b["y"], b["width"], b["height"])
        if not box and hit.get("point"):  # a point only: a small square round it
            box = (hit["point"][0] - 24, hit["point"][1] - 24, 48, 48)
        if not box:  # an element with no bounds: its words on the window
            img, to_screen = await self.capture(w)
            t = find_text(await ocr(img), target)
            if not t:
                return failed(f"I found “{target}” in {w.title}, but not where it is on the screen.", window=w.view())
            (l, tp), (r, bt) = to_screen(t["x"], t["y"]), to_screen(t["x"] + t["w"], t["y"] + t["h"])
            box = (l, tp, r - l, bt - tp)
        x, y, bw, bh = (int(round(v)) for v in box)
        name = str(e.get("name") or hit.get("text") or target).strip()
        how = "" if e else " (found by Jev's look at the window)" if hit.get("via") == "jev" else " (found by reading the screen)"
        return done(f"Found “{name}” in {w.title}{how}.", verified=True, window=w.view(),
                    marks=[{"kind": "circle", "rect": [x, y, max(bw, 8), max(bh, 8)], "label": name}])

    # -- act --------------------------------------------------------------------------------------------------------
    async def click(self, target, button="left", count=1, w=None):
        w = w or self.front()
        if not w:
            return failed("No app window is open to click in.")
        hit = await self.find(target, w, roles=("Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "CheckBox", "RadioButton"))
        if not hit:
            return failed(f"I couldn't find “{target}” in {w.title}.", window=w.view())
        if "element" in hit:
            e = hit["element"]
            args = {"pid": w.pid, "element_token": e["element_token"], "button": button, "count": count}
            try:
                await self.driver.call("click", args)
            except ComputerError as err:
                if "background" not in str(err).lower():
                    raise
                await self.driver.call("click", {**args, "delivery_mode": "foreground"})
            return done(f"Clicked “{e.get('name')}” in {w.title}.", verified=None, window=w.view(), via="ui-automation")
        activate(w.hwnd)
        click_at(*hit["point"], button=button, count=count)
        how = "by Jev's look at the window" if hit.get("via") == "jev" else "by reading the screen"
        return done(f"Clicked “{hit['text']}” in {w.title} (found {how}).", verified=None, window=w.view(), via=hit.get("via", "ocr"))

    async def click_here(self, button="left", count=1):
        """A bare "click": where the mouse already is, as a person means the word. The effect can't be seen from here (verified None)."""
        x, y = cursor()
        w = self.front()
        if w:
            activate(w.hwnd)
        click_at(x, y, button=button, count=count)
        return done(f"Clicked at ({x}, {y})" + (f" in {w.title}" if w else "") + ".", verified=None, window=w.view() if w else None)

    async def photo(self, say=None):
        """Take a photo with Windows' Camera app: bring it forward (open it if it isn't running), press its shutter control, and look for
        the new picture in the Camera Roll. verified is True only when a new file landed there."""
        before = camera_roll_files()
        w = next(iter(self.windows_for("Camera")), None)
        if not w:
            r = await self.open("Camera")
            if not r["ok"]:
                return r
            w = next(iter(self.windows_for("Camera")), None) or self.front()
            if not w:
                return failed("The Camera started, but I can't see its window.")
            await asyncio.sleep(CAMERA_WARM_S)
        else:
            activate(w.hwnd)
        if say:
            say("Pressing the Camera's shutter")
        if await self.find(CAMERA_PHOTO_MODE, w, roles=("Button",)):  # in video mode the shutter records: switch first
            r = await self.click(CAMERA_PHOTO_MODE, w=w)
            if not r["ok"]:
                return r
            await asyncio.sleep(CAMERA_MODE_S)
        label = None
        for name in CAMERA_SHUTTER:
            if await self.find(name, w, roles=("Button",)):
                label = name
                break
        if not label:
            buttons = [e.get("name") for e in await self.elements(w) if e.get("role") == "Button" and e.get("name")]
            return failed(f"I couldn't find the Camera's shutter button in {w.title} (its buttons: {', '.join(buttons[:8]) or 'none read'}).", window=w.view())
        r = await self.click(label, w=w)
        if not r["ok"]:
            return r
        end = time.monotonic() + PHOTO_WAIT_S
        while time.monotonic() < end:
            new = camera_roll_files() - before
            if new:
                name = max(new, key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0)
                return done(f"Took a photo: {Path(name).name}, in the Camera Roll.", verified=True, window=w.view(), file=name)
            await asyncio.sleep(POLL_S)
        return done(f"Pressed the Camera's shutter, but no new picture appeared in the Camera Roll within {PHOTO_WAIT_S}s.", verified=False, window=w.view())

    async def typing_target(self, target, fresh=True):
        """The window "open X and write …" types into: X's, once it is ready (its title settled after loading), and in an editor
        (NEW_DOCUMENT) whose open document already holds text, a new document first. -> (window, None), or (None, a failed result)
        when X isn't open: the words are never typed anywhere else."""
        app = await self.apps.find(target)
        name = (app or {}).get("name") or target
        end, w, title, since = time.monotonic() + READY_WAIT_S, None, None, time.monotonic()
        while time.monotonic() < end:
            w = next(iter(self.windows_for(target, app)), None)
            if w and w.title != title:
                title, since = w.title, time.monotonic()
            elif w and time.monotonic() - since >= READY_STABLE_S:
                break
            await asyncio.sleep(0.05)
        if not w:
            return None, failed(f"{name} isn't open, so I didn't type anything.")
        def holds_text(els):
            return any((e.get("value") or "").strip() for e in els if e.get("role") == "Document")
        if fresh and any(k in _norm(name).split() or k == _norm(name) for k in NEW_DOCUMENT) and \
                (not UNTITLED.match(w.title or "") or holds_text(await self.elements(w, limit=150))):
            if not activate(w.hwnd):
                return None, failed(f"Windows didn't let me focus {w.title}, so I didn't type anything.")
            press(["ctrl", "n"])
            end = time.monotonic() + READY_WAIT_S
            while time.monotonic() < end:  # the new document: an empty, untitled one in front
                await asyncio.sleep(0.15)
                w = next(iter(self.windows_for(target, app)), w)
                if UNTITLED.match(w.title or "") and not holds_text(await self.elements(w, limit=150)):
                    return w, None
            return None, failed(f"{name}'s open document already holds text and no new one appeared, so I didn't type into it.")
        return w, None

    async def type(self, text, field=None, enter=False, w=None):
        w = w or self.front()
        if not w:
            return failed("No app window is open to type into.")
        if field:
            hit = await self.find(field, w, roles=("Edit", "ComboBox", "Document"))
            if not hit:
                return failed(f"I couldn't find a “{field}” field in {w.title}.", window=w.view())
            if "element" in hit:
                e = hit["element"]
                try:
                    await self.driver.call("set_value", {"pid": w.pid, "element_token": e["element_token"], "value": text})
                    back = [x for x in await self.elements(w, query=text[:20]) if text[:20].lower() in (x.get("value") or "").lower()]
                    if enter:
                        activate(w.hwnd)
                        tap(VK["enter"])
                    return done(f"Typed into “{e.get('name')}”" + (" and pressed Enter." if enter else "."), verified=bool(back) or None,
                                window=w.view())
                except ComputerError:
                    pass  # not a ValuePattern control: fall through to clicking it and typing
            activate(w.hwnd)
            if "point" in hit:
                click_at(*hit["point"])
            else:
                await self.driver.call("click", {"pid": w.pid, "element_token": hit["element"]["element_token"], "delivery_mode": "foreground"})
        elif not activate(w.hwnd):
            return failed(f"Windows didn't let me focus {w.title}, so I didn't type anything.")
        how = await self.insert(text)
        if enter:  # after Enter the text may rightly be gone (sent, searched): nothing to read back
            tap(VK["enter"])
            return done(f"{how} {len(text)} characters into {w.title} and pressed Enter.", verified=None, window=w.view())
        if how == "Pasted":  # long text: read the document back and compare it with the exact payload
            ok, why = await self.saw_exact(w, text)
            said = {True: " I read it back: it matches exactly.", False: f" I read it back and it doesn't match: {why}.", None: f" {why}."}[ok]
            return done(f"{how} {len(text)} characters into {w.title}." + said, verified=ok, window=w.view())
        ok = await self.saw_text(w, text)
        said = {True: " I can see it there.", False: " but I can't see it there, so it may have gone elsewhere.", None: ""}[ok]
        return done(f"Typed {len(text)} characters into {w.title}." + said if ok is not False
                    else f"Typed {len(text)} characters into {w.title}," + said, verified=ok, window=w.view())

    async def insert(self, text):
        """Puts `text` into the focused control: short text as keystrokes (KEYEVENTF_UNICODE), long or multi-line text through the clipboard
        and Ctrl+V, one exact insert (the owner's clipboard text is put back after PASTE_SETTLE_S; other clipboard content, an image, is not
        kept: Windows gives no way to read it back whole). -> "Typed" or "Pasted"."""
        if len(text) < PASTE_MIN_CHARS and "\n" not in text:
            await asyncio.to_thread(type_unicode, text)
            return "Typed"
        before = await asyncio.to_thread(clipboard_text)
        await asyncio.to_thread(set_clipboard, text.replace("\r\n", "\n").replace("\n", "\r\n"))
        press(["ctrl", "v"])
        await asyncio.sleep(PASTE_SETTLE_S)
        if before is not None:
            try:
                await asyncio.to_thread(set_clipboard, before)
            except ComputerError:
                pass
        return "Pasted"

    async def document_text(self, w):
        """The text the window's document or edit control holds (UI Automation value), or None when it has none (then only the pixels tell)."""
        els = await self.elements(w, limit=150)
        texts = [e.get("value") or "" for e in els if e.get("role") in ("Document", "Edit")]
        return max(texts, key=len) if texts else None

    async def saw_exact(self, w, text):
        """After a long insert: the document read back against the exact payload. -> (True, None): it matches (a new document holds exactly it;
        a document that already held text ends with it); (False, why): what differs, where; (None, why): the window exposes no document text
        to read, so only its first words were looked for (saw_text) and the limit is said."""
        want = _flat(text)
        got = None
        for wait in SAW_WAITS_S + (0.6,):  # the app takes a long paste a moment to show in its control
            await asyncio.sleep(wait)
            got = await self.document_text(w)
            if got is None:
                break
            g = _flat(got)
            if g == want or g.endswith("\n" + want) or g.endswith(want) and len(g) > len(want):
                return True, None
            if want.startswith(g) and len(g) < len(want):
                continue  # still arriving
        if got is None:
            seen_first = await self.saw_text(w, text)
            return None, ("its first words are there, but the window exposes no document text to read back whole" if seen_first
                          else "the window exposes no document text to read back, and I can't see its first words either")
        g = _flat(got)
        if g.count(want) > 1:
            return False, "the text is there twice"
        tail = g[-len(want):] if len(g) >= len(want) else g
        at = next((i for i, (a, b) in enumerate(zip(tail, want)) if a != b), min(len(tail), len(want)))
        return False, (f"{len(g)} of {len(want)} characters are there; they differ from character {at + 1}: expected “{want[at:at + 24]}”, "
                       f"found “{tail[at:at + 24]}”")

    async def keys(self, keys, w=None):
        w = w or self.front()
        if w and not activate(w.hwnd):
            return failed(f"Windows didn't let me focus {w.title}, so I didn't press anything.")
        press(keys)
        return done(f"Pressed {'+'.join(keys)}" + (f" in {w.title}." if w else "."), verified=None, window=w.view() if w else None)

    async def scroll(self, direction, amount=4, w=None):
        w = w or self.front()
        if not w:
            return failed("No app window is open to scroll.")
        l, t, r, b = w.rect
        activate(w.hwnd)
        user32.SetCursorPos((l + r) // 2, (t + b) // 2)
        sign = 1 if direction in ("up", "left") else -1
        wheel(sign * amount, horizontal=direction in ("left", "right"))
        return done(f"Scrolled {direction} in {w.title}.", verified=None, window=w.view())

    async def volume(self, op, level=None):
        if op == "set":
            tap(VK["volume_down"], 50)  # Windows' volume keys move 2 points each: to 0, then up
            tap(VK["volume_up"], round((level or 0) / 2))
            return done(f"Volume set to about {level}% (Windows' volume keys move in steps of 2).", verified=None)
        vk = {"up": "volume_up", "down": "volume_down", "mute": "volume_mute", "unmute": "volume_mute"}[op]
        tap(VK[vk], 5 if op in ("up", "down") else 1)
        return done({"up": "Volume up.", "down": "Volume down.", "mute": "Toggled mute.", "unmute": "Toggled mute."}[op], verified=None)

    async def media(self, op):
        tap(VK[{"next": "next_track", "previous": "prev_track"}.get(op, "play_pause")])
        return done({"next": "Skipped to the next track.", "previous": "Went back a track."}.get(op, "Toggled play/pause."), verified=None)

    async def lock(self):
        user32.LockWorkStation()
        return done("Locked the computer.", verified=None)

    async def move_mouse(self, x=None, y=None, target=None, w=None):
        """To screen pixel (x, y), or onto something by its label. Verified by reading the cursor back."""
        if target:
            w = w or self.front()
            pt = await self.point_of(target, w) if w else None
            if not pt:
                return failed(f"I couldn't find “{target}” on the screen.")
            x, y = pt
        user32.SetCursorPos(int(x), int(y))
        at = cursor()
        return done(f"Moved the mouse to {target or f'{int(x)}, {int(y)}'}.", verified=abs(at[0] - x) <= 2 and abs(at[1] - y) <= 2)

    async def point_of(self, ref, w):
        """Screen point of "x, y" or of something by its label (UI Automation bounds when given, else OCR)."""
        m = re.fullmatch(r"\s*(\d{1,5})\s*[, ]\s*(\d{1,5})\s*", str(ref))
        if m:
            return int(m[1]), int(m[2])
        hit = await self.find(ref, w)
        if not hit:
            return None
        if "point" in hit:
            return hit["point"]
        e = hit["element"]
        box = e.get("frame") or e.get("bounds") or e.get("rect")
        if isinstance(box, dict) and {"x", "y", "width", "height"} <= set(box):
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        img, to_screen = await self.capture(w)  # the element has no coordinates: find its text on the window instead
        t = find_text(await ocr(img), ref)
        return to_screen(t["x"] + t["w"] / 2, t["y"] + t["h"] / 2) if t else None

    async def drag(self, source, target, w=None):
        w = w or self.front()
        if not w:
            return failed("No app window is open to drag in.")
        a, b = await self.point_of(source, w), await self.point_of(target, w)
        if not a or not b:
            return failed(f"I couldn't find “{source if not a else target}” in {w.title}.", window=w.view())
        if not activate(w.hwnd):
            return failed(f"Windows didn't let me focus {w.title}, so I didn't drag anything.")
        drag_path(a, b)
        return done(f"Dragged “{source}” to “{target}” in {w.title}. (I can't tell from here whether the app accepted the drop.)",
                    verified=None, window=w.view())

    async def clipboard(self, op, text=None):
        """get: what the clipboard holds (never read out if it looks like a password or key); set: put text on it."""
        if op == "set":
            if looks_secret(text):
                return failed("That looks like a password, card number or key, and Monday never puts those on the clipboard.")
            set_clipboard(text)
            return done(f"Copied “{text[:80]}” to the clipboard.", verified=clipboard_text() == text)
        t = clipboard_text()
        if t is None:
            return done("The clipboard holds no text.", verified=True, clipboard=None)
        if looks_secret(t):
            return done("The clipboard holds something that looks like a password, card number or key, so I won't read it out.",
                        verified=True, clipboard="(hidden)")
        return done(f"On the clipboard ({len(t)} characters):\n{t[:1500]}", verified=True, clipboard=t[:1500])

    async def transfer(self, source, target, what=""):
        """Copy from one app into another (the owner, 2026-10-24: "copy this from ChatGPT and paste it on other application"): what is
        selected in `source` (Ctrl+C there; "everything": Ctrl+A first), else its own Copy button under its newest answer (ChatGPT's,
        Claude's: "the last answer" goes straight there), pasted into `target` (opened if it isn't; in an editor whose document holds
        text, a new document: typing_target) and read back there. What was meant is never guessed: with nothing selected and no Copy
        button, it says so and nothing is pasted."""
        w, app = await self.target_window(source)
        name = (app or {}).get("name") or source
        if not w:
            return failed(f"{name} isn't open, so there was nothing to copy from it.")
        if not activate(w.hwnd):
            return failed(f"Windows didn't let me bring {w.title} forward, so nothing was copied.")
        mark = f"(monday {time.time_ns()})"  # the clipboard before Ctrl+C: still this after it, nothing was selected there
        before = await asyncio.to_thread(clipboard_text)
        await asyncio.to_thread(set_clipboard, mark)
        text, how = None, "the selected words"
        if not LAST_ANSWER.search(what or ""):
            if WHOLE.search(what or ""):
                press(["ctrl", "a"])
                await asyncio.sleep(0.1)
                how = "everything"
            press(["ctrl", "c"])
            text = await self._copied(mark)
        if text is None:  # nothing selected, or its last answer asked for: the app's own Copy button under its newest answer
            text, how = await self._copy_button(w, mark), "its last answer (its own Copy button)"
        if text is None:
            await asyncio.to_thread(set_clipboard, before or "")  # (the owner's own text back on the clipboard)
            return failed(f"Nothing is selected in {name}, and I found no Copy button there for its last answer, so nothing was pasted. "
                          f"Select the words in {name} and ask again.")
        if looks_secret(text):
            return failed(f"What I copied from {name} looks like a password, card number or key, and Monday never pastes those.")
        r = await self.open(target)  # (brought forward when it is already open)
        if not r.get("ok"):
            return r
        tw, bad = await self.typing_target(target)
        if bad:
            return bad
        put = await self.type(text, w=tw)
        return {**put, "text": f"Copied {how} from {name} ({len(text)} characters). {put['text']}"}

    async def _copied(self, mark):
        """The clipboard's text once it is something other than `mark` (what a Ctrl+C or a Copy button put there), or None."""
        end = time.monotonic() + COPY_WAIT_S
        while time.monotonic() < end:
            await asyncio.sleep(0.05)
            t = await asyncio.to_thread(clipboard_text)
            if t and t != mark and t.strip():
                return t
        return None

    async def _copy_button(self, w, mark):
        """The window's own Copy button under its newest answer (the bottom-most one: ChatGPT and Claude show one per answer), pressed.
        -> what it put on the clipboard, or None."""
        els = [e for e in await self.elements(w, query="copy") if COPY_BUTTON.match((e.get("name") or "").strip())
               and (e.get("role") or "Button") in ("Button", "MenuItem", "Hyperlink")]
        if not els:
            return None
        e = max(els, key=lambda e: ((e.get("frame") or {}).get("y") or 0))
        try:
            await self.driver.call("click", {"pid": w.pid, "element_token": e["element_token"]})
        except ComputerError:
            return None
        return await self._copied(mark)

    async def processes(self, query=None, sort="memory", limit=10):
        """What's running: the top processes by memory or CPU, or whether a given app is running."""
        titles = {x.pid: x.title for x in windows()}
        if query:  # names first (~10 ms), then memory for the matches alone
            hits = process_matches(query, process_table(memory=False), titles)
            if not hits:
                return done(f"No, {query} isn't running.", verified=True, running=False, processes=[])
            for x in hits:
                x["mb"] = rss_mb(x["pid"])
            mb = sum(x["mb"] for x in hits)
            wins = [titles[x["pid"]] for x in hits if x["pid"] in titles][:3]
            # window titles in a sentence of their own: the spoken line (its first sentence) never reads out "Inbox - me@…"
            return done(f"Yes, {query} is running: {len(hits)} process{'es' if len(hits) != 1 else ''} using {mb:.0f} MB."
                        + (f" Windows: {', '.join(wins)}." if wins else ""), verified=True, running=True, processes=hits[:20])
        procs = await asyncio.to_thread(process_table, sort == "cpu")  # every process's memory: off the event loop
        key = "cpu" if sort == "cpu" else "mb"
        # group by name: Chrome's 30 helper processes are one app to the owner
        groups = {}
        for x in procs:
            g = groups.setdefault(x["name"].lower(), {"name": x["name"], "count": 0, "mb": 0.0, "cpu": 0.0})
            g["count"] += 1
            g["mb"] += x["mb"]
            g["cpu"] += x.get("cpu", 0.0)
        top = sorted(groups.values(), key=lambda g: g[key], reverse=True)[:limit]
        rows = [f"{g['name']}" + (f" ×{g['count']}" if g["count"] > 1 else "") + f" · {g['mb']:.0f} MB" + (f" · {g['cpu']:.0f}% CPU" if key == "cpu" else "")
                for g in top]
        return done(f"{len(procs)} processes running. Top by {'CPU' if key == 'cpu' else 'memory'}:\n" + "\n".join(f"• {r}" for r in rows),
                    verified=True, processes=top)

    async def kill(self, target):
        """Force-close an app's processes (the permission engine asks first). Windows' own processes never."""
        procs = process_table(memory=False)
        hits = [x for x in process_matches(target, procs, {x.pid: x.title for x in windows()}) if x["pid"] != os.getpid()]
        if not hits:
            return failed(f"{target} isn't running.")
        from .router import CRITICAL_UNIX  # macOS' and Linux's own (router rules first; this checks again at the moment of acting)
        crit = [x["name"] for x in hits if x["name"].lower() in CRITICAL_PROCESSES or x["name"].lower() in CRITICAL_UNIX]
        if crit:
            return failed(f"{crit[0]} is part of {'Windows' if sys.platform == 'win32' else 'the system'}, and Monday never force-closes it.")
        gone = []
        for x in hits:
            try:
                psutil.Process(x["pid"]).kill()
                gone.append(x["pid"])
            except psutil.NoSuchProcess:
                gone.append(x["pid"])
            except psutil.Error:
                pass
        end = time.monotonic() + 3
        alive = gone
        while alive and time.monotonic() < end:
            await asyncio.sleep(POLL_S)
            alive = [pid for pid in gone if psutil.pid_exists(pid)]
        return done(f"Force-closed {target} ({len(gone)} process{'es' if len(gone) != 1 else ''}).", verified=not alive and len(gone) == len(hits))

    async def inspect(self, target=None):
        """The controls a window exposes through UI Automation: its buttons, fields, menus, links, tabs."""
        w, app = await self.target_window(target)
        if not w:
            return failed(f"{(app or {}).get('name') or target or 'That window'} isn't open.")
        els = await self.elements(w)
        if not els:
            return failed(f"{w.title} exposes no UI Automation controls (web pages in Chrome don't; I can still read it through the screen).",
                          window=w.view())
        by = {}
        for e in els:
            name = (e.get("name") or "").strip()
            if name and e.get("role") in ("Button", "Edit", "MenuItem", "Hyperlink", "CheckBox", "ComboBox", "TabItem", "ListItem",
                                          "RadioButton", "Document", "TreeItem", "Text"):
                by.setdefault(e["role"], [])
                if name not in by[e["role"]]:
                    by[e["role"]].append(name)
        lines = [f"{role} ({len(names)}): " + ", ".join(names[:12]) + ("…" if len(names) > 12 else "") for role, names in by.items()]
        return done(f"{w.title} has {len(els)} controls:\n" + "\n".join(f"• {l}" for l in lines), verified=True, window=w.view(),
                    controls={r: n[:40] for r, n in by.items()})

    async def saw_text(self, w, text):
        """True when the window now shows `text` (a control's value first, else OCR), False when it doesn't, None when
        there's nothing to compare (very short text) or the window can't be read."""
        if len(_norm(text)) < 3:
            return None
        for wait in SAW_WAITS_S:  # the app takes the keys a moment after they are sent: a few quick looks at its controls before OCR (~2 s)
            await asyncio.sleep(wait)
            els = await self.elements(w, limit=150)
            if any(seen(text[:60], e.get("value") or "") for e in els):
                return True
            if not any(e.get("role") in ("Document", "Edit") for e in els):
                break  # no control that holds text: only the pixels can tell
        try:
            img, _ = await self.capture(w)
            lines = await ocr(img)
        except (ComputerError, OSError):
            return None
        return bool(seen(text[:60], " ".join(ln["text"] for ln in lines)))

    async def save(self, path=None, overwrite=False, w=None):
        """Ctrl+S in the front app. When it asks where (a Save dialog), type the full path, press Enter, and verify the
        file is on disk. A file that already exists is only replaced when the owner approved it (overwrite)."""
        w = w or self.front()
        if not w:
            return failed("No app window is open to save.")
        if not activate(w.hwnd):
            return failed(f"Windows didn't let me focus {w.title}, so I didn't save anything.")
        t0 = time.time()
        press(["ctrl", "shift", "s"] if path else ["ctrl", "s"])  # "save as <name>" must ask where, even for a named file
        dlg = await self.wait_dialog(SAVE_DIALOG)
        if not dlg and path:  # some apps have no Ctrl+Shift+S: plain Ctrl+S asks for untitled documents
            press(["ctrl", "s"])
            dlg = await self.wait_dialog(SAVE_DIALOG)
        if not dlg:
            if path:
                return failed(f"{w.title} didn't open a Save dialog, so I couldn't save it as {Path(path).name}.", window=w.view())
            return done(f"Pressed Ctrl+S in {w.title}. It didn't ask for a name, so it saved to its own file (I can't see which from here).",
                        verified=None, window=w.view())
        if not path:
            return {"ok": False, "verified": False, "needs": "file name", "window": w.view(),
                    "text": f"{w.title} is asking where to save. Say “save it as notes.txt” (or type a name in the dialog)."}
        press(["ctrl", "a"])
        await asyncio.to_thread(type_unicode, str(path))
        tap(VK["enter"])
        final = Path(path) if Path(path).suffix else Path(str(path) + ".txt")  # Notepad-style apps add their extension
        end = time.monotonic() + SAVE_WAIT_S
        while time.monotonic() < end:
            await asyncio.sleep(POLL_S)
            hwnd, title, cls = foreground_raw()
            if cls == "#32770" and hwnd != dlg and re.search(r"confirm|replace|already exists", title, re.I):
                if not overwrite:  # a surprise overwrite prompt: never answer yes on the owner's behalf
                    tap(VK["esc"])
                    await asyncio.sleep(0.2)
                    tap(VK["esc"])
                    return failed(f"{Path(path).name} already exists, so I cancelled instead of replacing it.", window=w.view())
                press(["alt", "y"])
            for f in (Path(path), final):
                if f.exists() and f.stat().st_mtime >= t0 - 1:
                    return done(f"Saved {f}.", verified=True, path=str(f), window=w.view())
        return done(f"Typed {path} into the Save dialog, but no file appeared within {SAVE_WAIT_S:g}s.", verified=False, window=w.view())

    async def wait_dialog(self, rx, timeout=3.0):
        """The hwnd of a dialog (#32770) whose title matches rx, once it's in front, else None."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            hwnd, title, cls = foreground_raw()
            if cls == "#32770" and rx.search(title or ""):
                return hwnd
            await asyncio.sleep(0.1)
        return None

    # -- observe (for a model choosing the next step) ------------------------------------------------------------------
    async def observe(self, max_elements=80, max_lines=50, w=None):
        """A compact, numbered view of a window (the front one unless told): UIA elements (E#) and, when those are few, OCR lines (T#)."""
        w = w or self.front()
        if not w:
            return {"window": None, "text": "No app window is open.", "targets": {}}
        els = [e for e in await self.elements(w, limit=MAX_ELEMENTS) if (e.get("name") or e.get("value"))]
        interesting = ("Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "Edit", "ComboBox", "CheckBox", "RadioButton", "Document", "TreeItem")
        els = sorted(els, key=lambda e: (e.get("role") not in interesting))[:max_elements]
        targets, rows = {}, []
        for i, e in enumerate(els, 1):
            targets[f"E{i}"] = {"element": e}
            val = f" = {str(e.get('value'))[:60]!r}" if e.get("value") else ""
            rows.append(f"E{i} {e.get('role')} {str(e.get('name') or '')[:70]!r}{val}")
        if len(els) < 8:
            img, to_screen = await self.capture(w)
            for j, ln in enumerate([l for l in await ocr(img) if len(l["text"].strip()) > 1][:max_lines], 1):
                targets[f"T{j}"] = {"point": to_screen(ln["x"] + ln["w"] / 2, ln["y"] + ln["h"] / 2), "text": ln["text"]}
                rows.append(f"T{j} text {ln['text'][:90]!r}")
        others = [x.title for x in windows() if x.hwnd != w.hwnd and not self.is_own(x)][:8]
        text = (f"Front window: {w.title!r} ({w.process})\nOther windows: {others}\nTargets:\n" + "\n".join(rows))
        return {"window": w, "text": text, "targets": targets, "others": others}

    async def act_on(self, target, w, kind, **kw):
        """Click/type on an observed target id (E#/T#)."""
        if "element" in target:
            e = target["element"]
            if kind == "type":
                try:
                    await self.driver.call("set_value", {"pid": w.pid, "element_token": e["element_token"], "value": kw["text"]})
                    return done(f"Typed into “{e.get('name')}”.", verified=None)
                except ComputerError:
                    await self.driver.call("click", {"pid": w.pid, "element_token": e["element_token"], "delivery_mode": "foreground"})
                    await asyncio.to_thread(type_unicode, kw["text"])
                    return done(f"Typed into “{e.get('name')}”.", verified=None)
            args = {"pid": w.pid, "element_token": e["element_token"], "button": kw.get("button", "left"), "count": kw.get("count", 1)}
            try:
                await self.driver.call("click", args)
            except ComputerError as err:
                if "background" not in str(err).lower():
                    raise
                await self.driver.call("click", {**args, "delivery_mode": "foreground"})
            return done(f"Clicked “{e.get('name')}”.", verified=None)
        activate(w.hwnd)
        click_at(*target["point"], button=kw.get("button", "left"), count=kw.get("count", 1))
        if kind == "type":
            await asyncio.to_thread(type_unicode, kw["text"])
            return done(f"Typed into the field at “{target['text']}”.", verified=None)
        return done(f"Clicked “{target['text']}”.", verified=None)

    async def close_driver(self):
        await self.driver.close()


if __name__ == "__main__":  # python -m core.computer [--live]
    assert url_for("github.com") == "https://github.com" and url_for("YouTube") == "https://www.youtube.com"
    assert url_for("https://x.com/a") == "https://x.com/a" and url_for("notepad") is None and url_for("the settings") is None
    assert site_word("https://www.youtube.com/results?search_query=x") == "youtube" and site_word("https://mail.google.com") == "google"
    assert _norm("the Terminal app") == "terminal" and ALIASES[_norm("VS Code")] == "visual studio code"
    assert store_alias("notewhere", "where") is None, "System32's where.exe is a tool, never the app a misheard name meant"
    assert all(UNTITLED.match(t) for t in ("Untitled - Notepad", "*Untitled - Notepad", "Untitled-1 - Visual Studio Code", "Document2 - Word"))
    assert not any(UNTITLED.match(t) for t in (".env - Notepad", "*hii - Notepad", "notes.txt - Notepad", "Welcome - Visual Studio Code")), \
        "an open file, or a tab Notepad named after its text, is never typed into by “open … and write”: a new document is"
    assert sound_key("chatgpt") == sound_key("chatgbd") == sound_key("jetgbt") == "23213", sound_key("chatgpt")
    # copy from one app into another (2026-10-24, stand-ins for the screen and the clipboard): the selection, else the app's own Copy
    # button under its newest answer (the bottom-most), pasted where asked; nothing guessed, the owner's clipboard back when nothing came
    import types as _ty
    _real_ui = activate, press, set_clipboard, clipboard_text, COPY_WAIT_S
    _clip, _sel, _pressed, _typed, _buttons, _clicked = {"t": "the owner's words"}, [None], [], [], [], []

    def press(keys):  # noqa: F811
        _pressed.append("+".join(keys))
        if keys == ["ctrl", "c"] and _sel[0] is not None:
            _clip["t"] = _sel[0]

    async def _xcall(tool, args=None, timeout=None):
        _clicked.append((args or {}).get("element_token"))
        _clip["t"] = "the last answer's words"
        return {}
    activate, set_clipboard, clipboard_text, COPY_WAIT_S = (lambda h: True), (lambda t: _clip.__setitem__("t", t)), (lambda: _clip["t"]), 0.2
    try:
        _xc = Computer()
        _src = _ty.SimpleNamespace(hwnd=1, pid=2, title="ChatGPT")
        _xc.target_window = lambda t: asyncio.sleep(0, (_src, {"name": "ChatGPT"}) if t == "chatgpt" else (None, None))
        _xc.open = lambda t: asyncio.sleep(0, done("Opened Notepad.", verified=True))
        _xc.typing_target = lambda t: asyncio.sleep(0, (_ty.SimpleNamespace(hwnd=3, pid=4, title="Untitled - Notepad"), None))
        _xc.type = lambda text, w=None: (_typed.append(text), asyncio.sleep(0, done(f"Pasted {len(text)} characters into Untitled - Notepad.",
                                                                                     verified=True)))[1]
        _xc.elements = lambda w, query=None, limit=None: asyncio.sleep(0, list(_buttons))
        _xc.driver.call = _xcall
        _sel[0] = "the selected words"
        r = asyncio.run(_xc.transfer("chatgpt", "notepad", "this"))
        assert r["ok"] and _typed == ["the selected words"] and r["text"].startswith("Copied the selected words from ChatGPT (18 characters)."), r
        _sel[0], _buttons[:] = None, [{"name": "Copy", "role": "Button", "frame": {"y": 120}, "element_token": "old"},
                                      {"name": "Copy message", "role": "Button", "frame": {"y": 880}, "element_token": "newest"},
                                      {"name": "Copy link", "role": "Button", "frame": {"y": 990}, "element_token": "not-a-copy-of-an-answer"}]
        r = asyncio.run(_xc.transfer("chatgpt", "notepad", "this"))
        assert r["ok"] and _clicked == ["newest"] and _typed[-1] == "the last answer's words" and "its own Copy button" in r["text"], (r, _clicked)
        _pressed.clear()
        r = asyncio.run(_xc.transfer("chatgpt", "notepad", "the last answer"))
        assert r["ok"] and "ctrl+c" not in _pressed and _clicked[-1] == "newest", "the last answer: its Copy button, no Ctrl+C on the owner's selection"
        _buttons.clear()
        _clip["t"], n_typed = "the owner's words", len(_typed)
        r = asyncio.run(_xc.transfer("chatgpt", "notepad", "this"))
        assert not r["ok"] and "Nothing is selected in ChatGPT" in r["text"] and len(_typed) == n_typed and _clip["t"] == "the owner's words", r
        _sel[0] = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
        r = asyncio.run(_xc.transfer("chatgpt", "notepad", "this"))
        assert not r["ok"] and "password" in r["text"] and len(_typed) == n_typed, "a key is never pasted"
        assert "isn't open" in asyncio.run(_xc.transfer("signal", "notepad", "this"))["text"]
    finally:
        activate, press, set_clipboard, clipboard_text, COPY_WAIT_S = _real_ui
    # the apps a request comes close to: what the plain pairwise check (before 2026-10-24) found, faster
    _items = [{"name": n} for n in ("Spotify", "Visual Studio Code", "WhatsApp", "Microsoft Word", "Calculator", "Notepad++", "Steam")]

    def _plain(text):
        w = _norm(text).split()
        sp = {" ".join(w[i:i + n]) for n in (1, 2, 3) for i in range(len(w) - n + 1)} - FILLER_WORDS
        return [a["name"] for a in _items if any(s == _norm(a["name"]) or (len(s) > 3 and (s in _norm(a["name"]).split() or
                                                difflib.SequenceMatcher(None, s, _norm(a["name"])).ratio() >= 0.8)) for s in sp)]
    for _t in ("open spotfy and code", "type it in microsoft wrd please", "calculater", "what's app", "steam", "notepad++ now",
               "the weather today in Mumbai", "open the steam app and whats app", "word"):
        assert apps_near(_t, _items) == _plain(_t), (_t, apps_near(_t, _items), _plain(_t))
    assert apps_near("open spotfy and code", _items) == ["Spotify", "Visual Studio Code"] and apps_near("", _items) == []
    _apps = Apps(None)
    _apps.items, _apps.at = [{"name": "Norton 360 for Gamers", "launch_path": "n.exe"}, {"name": "Spotify", "launch_path": "s.exe"}], time.monotonic()
    import asyncio as _a
    assert _a.run(_apps.find("for")) is None and _a.run(_apps.find("me")) is None and _a.run(_apps.find("the app")) is None, "filler words open nothing"
    assert _a.run(_apps.find("spotify"))["name"] == "Spotify" and _a.run(_apps.find("norton"))["name"] == "Norton 360 for Gamers"
    assert _a.run(_apps.near("spotfy")) == "Spotify" and _a.run(_apps.near("note where")) is None, "a near name is asked about; nonsense isn't"
    _apps.items = [{"name": "Windows Notepad", "launch_path": "n.exe"}, {"name": "Microsoft Photos", "launch_path": "p.exe"}]  # (the driver's names)
    assert _a.run(_apps.find("notpad"))["name"] == "Windows Notepad", "a typo of a well-known name opens it (2026-10-20: it failed)"
    assert _a.run(_apps.near("photo shop")) == "Microsoft Photos" and _a.run(_apps.find("photo shop")) is None, "asked about by its own word, not opened"
    _apps.items += [{"name": "WhatsApp", "launch_path": "w.exe"}, {"name": "What is new in the latest version", "launch_path": "x.exe"}]
    assert _a.run(_apps.near("what's app")) == "WhatsApp", "the name as said, “app” and all"
    assert sounds_like("chatgbd", "chatgpt") and sounds_like("jetgbt", "chatgpt") and sounds_like("spotifi", "spotify"), "heard as it sounds"
    reset = {"name": "VLC media player - reset preferences and cache files", "kind": "desktop",
             "launch_path": '"C:\\Program Files\\VideoLAN\\VLC\\vlc.exe" --reset-config --reset-plugins-cache vlc://quit'}
    _apps.items = merge_start([reset], {"{6D809377-6AF0-444B-8957-A3773F02200E}\\VideoLAN\\VLC\\vlc.exe": "VLC media player",
                                        "Microsoft.AutoGenerated.{51325390}": "VLC media player skinned", "SpotifyAB.Spotify_x!Spotify": "Spotify",
                                        "https://help.example": "Help site"})
    assert [(a["name"], a["kind"]) for a in _apps.items] == [(reset["name"], "desktop"), ("VLC media player", "desktop"), ("Spotify", "uwp")], \
        "the apps only the Start list had, a Store app among them (2026-10-26: with the driver's list empty, Spotify wasn't there)"
    assert _apps.items[2]["bundle_id"] == "SpotifyAB.Spotify_x" and _apps.items[2]["launch_path"] == "shell:appsFolder\\SpotifyAB.Spotify_x!Spotify"
    assert [a["name"] for a in merge_start([{"name": "Spotify - Music", "kind": "uwp", "bundle_id": "SpotifyAB.Spotify_x", "launch_path": "x"}],
                                           {"SpotifyAB.Spotify_x!Spotify": "Spotify"})] == ["Spotify - Music"], "a package the driver listed: once"
    assert _a.run(_apps.find("spotify", scored=True))[1] >= 100 and _a.run(_apps.find("nothing like it", scored=True)) == (None, 0)
    assert [app_name(t) for t in ("Spotify", "github.com", "downloads", "C:\\x\\y.exe", "my report.docx", "the", " vs code ")] == \
        ["Spotify", None, None, None, None, None, "vs code"], "only a name is looked for among the apps"
    vlc = _a.run(_apps.find("vlc"))
    assert vlc["name"] == "VLC media player" and Path(exe_of(vlc)).name == "vlc.exe", ("never the reset shortcut for “vlc” (2026-10-16)", vlc)
    assert _a.run(_apps.find("reset vlc")) is reset and _a.run(_apps.find("vlc media player")) is vlc
    assert not sounds_like("word", "wired") and not sounds_like("chatgpt", "shotcut") and not sounds_like("cam", "camera"), "short or different names stay apart"
    assert exe_of({"launch_path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"}).endswith("chrome.exe")
    assert exe_of({"launch_path": '"C:\\Users\\x\\AppData\\Local\\Discord\\Update.exe" --processStart Discord.exe'}).endswith("Update.exe")
    assert special_folder("downloads folder") and special_folder("nonsense folder") is None
    lines = [{"text": "Log in", "x": 10, "y": 20, "w": 40, "h": 10, "words": [{"text": "Log", "x": 10, "y": 20, "w": 18, "h": 10},
                                                                              {"text": "in", "x": 32, "y": 20, "w": 18, "h": 10}]}]
    assert find_text(lines, "login")["x"] == 10 and find_text(lines, "sign up") is None
    assert vk_of("ctrl") == 0x11 and vk_of("F5") == 0x74 and vk_of("a") == ord("A")

    class _W:  # Dotpals' marks: where a thing is, as a rectangle to circle, by each way find() finds it; nothing clicked
        title, hwnd = "Notepad", 1

        def view(self):
            return {"title": self.title}

    async def _found(label, w, roles=None):
        return {"save": {"element": {"name": "Save", "frame": {"x": 100, "y": 50, "width": 60, "height": 24}}},
                "open": {"point": (300, 200), "text": "Open", "box": (280.4, 190, 40, 20)},
                "gear": {"point": (500, 400), "text": "gear", "via": "jev", "box": None}}.get(label)
    _pc = Computer.__new__(Computer)
    _pc.front, _pc.find = (lambda: _W()), _found
    _r = _a.run(_pc.point("save"))
    assert _r["ok"] and _r["marks"] == [{"kind": "circle", "rect": [100, 50, 60, 24], "label": "Save"}] and _r["text"] == "Found “Save” in Notepad.", _r
    assert _a.run(_pc.point("open"))["marks"][0]["rect"] == [280, 190, 40, 20] and "reading the screen" in _a.run(_pc.point("open"))["text"]
    assert _a.run(_pc.point("gear"))["marks"][0]["rect"] == [476, 376, 48, 48], "a point only (Jev): a square round it"
    assert not _a.run(_pc.point("nothing"))["ok"] and _a.run(Computer.point(type("C", (), {"front": lambda self: None})(), "x"))["ok"] is False
    _pc.own_name = lambda: "Monday"
    assert all(_pc.is_own(Win(1, 2, "Monday", p)) for p in ("pythonw.exe", "Monday.exe")) and not _pc.is_own(Win(1, 2, "Monday", "notepad.exe")), \
        "the desktop app's own window, as Python's pythonw or as Monday.exe (2026-10-21), is never the one a command is about"
    if WIN:
        assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        assert windows() is not None
        _c = Computer.__new__(Computer)  # window_of needs no driver: "save it" means the window Monday just wrote in, while it's open
        some = next(iter(windows()), None)
        assert _c.window_of(None) is None and (some is None or _c.window_of({**some.view(), "at": time.time() - WORKED_IN_S - 1}) is None)
        assert some is None or _c.window_of({**some.view(), "at": time.time()}).hwnd == some.hwnd
    if "--live" in sys.argv:  # opens Calculator; closes it only if it wasn't already open (never touches the owner's windows)
        async def live():
            c = Computer()
            t0 = time.perf_counter()
            await c.apps.all()
            print(f"installed apps: {len(c.apps.items)} ({time.perf_counter() - t0:.1f}s, cached afterwards)")
            was_open = bool(c.windows_for("calculator", await c.apps.find("calculator")))
            t0 = time.perf_counter()
            r = await c.open("calculator")
            print("open calculator:", r["text"], "verified:", r["verified"], f"{time.perf_counter() - t0:.2f}s")
            t0 = time.perf_counter()
            r2 = await c.read("window", "calculator")
            print("read:", r2["text"][:100].replace("\n", " | "), f"{time.perf_counter() - t0:.2f}s")
            if not was_open:
                r3 = await c.close("calculator")
                print("close:", r3["text"], "verified:", r3["verified"])
            await c.close_driver()
        asyncio.run(live())
    if "--live-notepad" in sys.argv:  # the owner's Demo 1 + save + clipboard, on the real desktop, only when nobody's using it
        class _LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = _LII(cbSize=ctypes.sizeof(_LII))
        user32.GetLastInputInfo(ctypes.byref(li))
        idle = (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000
        if locked() or (idle < 120 and "--force" not in sys.argv):
            sys.exit(f"skipped: the PC is {'locked' if locked() else f'in use (last input {idle:.0f}s ago)'}; nothing was touched")

        async def notepad():
            import tempfile
            c = Computer()
            if c.windows_for("notepad", await c.apps.find("notepad")):
                return print("skipped: Notepad is already open, and Monday never types into the owner's documents")
            clip = clipboard_text()
            path = Path(tempfile.gettempdir()) / f"monday-live-{int(time.time())}.txt"
            t = time.perf_counter
            try:
                t0 = t(); r = await c.open("notepad"); print(f"open: {r['text']} verified={r['verified']} {t() - t0:.2f}s")
                press(["ctrl", "n"]); await asyncio.sleep(0.6)  # a fresh tab: Notepad may have restored earlier ones
                t0 = t(); r = await c.type("Hello, I am Monday."); print(f"type: {r['text']} verified={r['verified']} {t() - t0:.2f}s")
                w = c.front()
                user32.GetKeyboardLayout.restype = ctypes.c_void_p
                hkl = user32.GetKeyboardLayout(user32.GetWindowThreadProcessId(w.hwnd, None)) or 0
                print("  the document holds:", [repr(e["value"][:60]) for e in await c.elements(w, limit=150) if e.get("value")][:3],
                      f"(front: {w.title}; keyboard layout {hkl & 0xFFFFFFFF:08x})")
                t0 = t(); r = await c.save(str(path)); print(f"save: {r['text']} verified={r['verified']} {t() - t0:.2f}s")
                print("  on disk:", repr(path.read_bytes()[:80]) if path.exists() else "missing")
                t0 = t(); r = await c.clipboard("set", "Monday clipboard check"); print(f"clipboard set: verified={r['verified']} {t() - t0:.2f}s")
                r = await c.clipboard("get"); print("clipboard get:", r["text"][:60].replace("\n", " | "))
                r = await c.inspect("notepad"); print("inspect:", r["text"][:160].replace("\n", " | "))
            finally:
                if clip is not None:
                    set_clipboard(clip)  # the owner's clipboard, as it was
                press(["ctrl", "w"])  # the test tab only (saved, so no prompt)
                await asyncio.sleep(0.5)
                r = await c.close("notepad"); print("close:", r["text"])
                path.unlink(missing_ok=True)
                await c.close_driver()
        asyncio.run(notepad())
    print("computer self-check: OK")
