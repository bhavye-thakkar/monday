"""Monday's global shortcuts (2026-10-19): keys that work from any app, on Windows, macOS and Linux; each is said to be on only once
the OS gave it to Monday (the owner's Coucou recording: a Shortcuts page with a switch and the keys for each, Reset to defaults).

The owner's keymap is kept in data/desktop.json ("keys": action -> {"keys": "Ctrl+Alt+M", "on": true}), keys in Qt's portable
words. Windows: RegisterHotKey on the window's thread; its WM_HOTKEY comes through Qt's event loop, where a native event filter takes
it; a held key (push-to-talk) is let go when GetAsyncKeyState says so. macOS and Linux: the pynput package when it is installed (its
listener runs on its own thread; macOS asks for Accessibility); without it each key says why it isn't on, never "on".

Check: `python -m core.hotkeys --check` (parsing, conflicts, the keymap's checks, and on Windows a real registration answered
through Qt's event loop, with keys nobody uses).
"""
from __future__ import annotations

import ctypes
import os
import sys

from . import qtkit  # noqa: F401  (PySide6 on the path)
from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtCore import Qt

# (action, what the owner reads, default keys, on by default, held, the keys instead where the default types a character on this keyboard).
# allow / deny are taken only while an agent's request waits. The owner's layout (ENG IN, 2026-10-20 night: "how would I write a dot
# above m?") types ā ḍ ē ṅ ḥ ī l̥ ṁ ṇ ō æ r̥ ś ṭ ū ṣ ñ ₹ – on Ctrl+Alt (AltGr); B C F J K P V W Z, the digits but 4, and - type nothing.
ACTIONS = (
    ("talk", "Talk to Monday while held", "Ctrl+Space", True, True, "Ctrl+Space"),
    ("notch", "Open or close the notch", "Ctrl+Alt+M", True, False, "Ctrl+Alt+B"),
    ("chat", "Open the chat in the notch", "Ctrl+Alt+Space", True, False, "Ctrl+Alt+K"),
    ("waiting", "Go to what waits for you", "Ctrl+Alt+A", False, False, "Ctrl+Alt+F"),
    ("allow", "Allow an agent's waiting request", "Ctrl+Alt+Y", True, False, "Ctrl+Alt+1"),  # 1 = yes, 0 = no (= types – here)
    ("deny", "Deny an agent's waiting request", "Ctrl+Alt+N", True, False, "Ctrl+Alt+0"),
    ("circle", "Circle part of the screen and ask", "Ctrl+Alt+C", True, False, "Ctrl+Alt+J"),
    ("mic", "Turn the microphone on or off", "Ctrl+Alt+L", False, False, "Ctrl+Alt+V"),
    ("window", "Open Monday's window", "Ctrl+Alt+O", False, False, "Ctrl+Alt+W"),
    ("pets", "Open the pet's wardrobe", "Ctrl+Alt+G", False, False, "Ctrl+Alt+P"),
)
NAMES = {a[0]: a[1] for a in ACTIONS}
HELD = {a[0] for a in ACTIONS if a[4]}
LIVE_ONLY = {"allow", "deny"}            # registered only while there is something to answer (the notch says when)
HOLD_POLL_MS = 20                       # a held key's state is read this often until it is let go
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
BASE_ID = 0x4D00                        # our hotkey ids (an app's own range)


def defaults():
    """Each shortcut's keys and whether it is on. Where the usual keys also type a character on this keyboard (AltGr: on the owner's ENG IN
    layout Ctrl+Alt+M types ṁ, Y ñ, N ṇ; Shift only capitalises them), the action takes its other keys instead (ACTIONS' last field:
    Ctrl+Alt+B for the notch, = and - for allow and deny), so that letter stays typeable (the owner, 2026-10-20 night: "how would I
    write a dot above m?"). On a layout where nothing types, the usual keys stay. Every shortcut is on when it is on by default."""
    return {a: {"keys": alt if types_char(k) and not types_char(alt) else k, "on": on} for a, _n, k, on, _h, alt in ACTIONS}


def alternate(keys):
    """The same keys with Shift (Ctrl+Alt+Space -> Ctrl+Alt+Shift+Space), taken instead when another app holds the keys the owner set
    (Claude's desktop app holds Ctrl+Alt+Space, 2026-10-20); "" when Shift is in them already."""
    p = parse(keys)
    if not p or p[0] & Qt.KeyboardModifier.ShiftModifier:
        return ""
    return QKeySequence((p[0] | Qt.KeyboardModifier.ShiftModifier) | p[1]).toString(QKeySequence.SequenceFormat.PortableText)


def keymap(saved):
    """The owner's saved keymap over the defaults, every value checked (a hand-edited desktop.json can't break the shortcuts)."""
    out = defaults()
    for a, v in (saved or {}).items() if isinstance(saved, dict) else ():
        if a in out and isinstance(v, dict):
            keys = v.get("keys")
            if isinstance(keys, str) and (keys == "" or parse(keys)):
                out[a]["keys"] = canonical(keys) if keys else ""
            if isinstance(v.get("on"), bool):
                out[a]["on"] = v["on"]
    return out


def parse(keys: str):
    """'Ctrl+Alt+M' -> (Qt modifiers, Qt key) or None. Needs one modifier at least, unless it is F13-F24 (no app types with those)."""
    try:
        seq = QKeySequence.fromString(keys, QKeySequence.SequenceFormat.PortableText)
    except (TypeError, ValueError):
        return None
    if seq.count() != 1:
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    if key in (Qt.Key.Key_unknown, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, 0):
        return None
    if mods == Qt.KeyboardModifier.NoModifier and not (Qt.Key.Key_F13 <= key <= Qt.Key.Key_F24):
        return None
    return mods, key


def canonical(keys: str) -> str:
    p = parse(keys)
    return QKeySequence(p[0] | p[1]).toString(QKeySequence.SequenceFormat.PortableText) if p else ""


def conflict(km, action, keys):
    """Another action already on these keys (its name), or None."""
    c = canonical(keys)
    return next((NAMES[a] for a, v in km.items() if a != action and v["keys"] and canonical(v["keys"]) == c), None)


# -- Windows ------------------------------------------------------------------------------------------------------------------------
QT_VK = {Qt.Key.Key_Space: 0x20, Qt.Key.Key_Return: 0x0D, Qt.Key.Key_Enter: 0x0D, Qt.Key.Key_Tab: 0x09, Qt.Key.Key_Escape: 0x1B,
         Qt.Key.Key_Backspace: 0x08, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Home: 0x24, Qt.Key.Key_End: 0x23,
         Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27,
         Qt.Key.Key_Down: 0x28, Qt.Key.Key_Pause: 0x13, Qt.Key.Key_Print: 0x2C}


def win_vk(key) -> int | None:
    k = int(key)
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return k
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return 0x70 + (k - int(Qt.Key.Key_F1))
    if key in QT_VK:
        return QT_VK[key]
    if 0x20 < k < 0x7F:  # punctuation: where this keyboard has it
        r = ctypes.windll.user32.VkKeyScanW(ctypes.c_wchar(chr(k)))
        return None if r == -1 else r & 0xFF
    return None


def win_mods(mods) -> int:
    m = 0
    if mods & Qt.KeyboardModifier.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.KeyboardModifier.AltModifier:
        m |= MOD_ALT
    if mods & Qt.KeyboardModifier.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.KeyboardModifier.MetaModifier:
        m |= MOD_WIN
    return m


def types_char(keys: str) -> str | None:
    """What these keys type on this keyboard's layout instead (Ctrl+Alt is AltGr on many: Ctrl+Alt+A types "ą" in Polish), or None.
    Windows only (ToUnicodeEx, without touching the keyboard's state)."""
    if sys.platform != "win32" or not (p := parse(keys)):
        return None
    mods, key = p
    if not (mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.AltModifier):
        return None
    vk = win_vk(key)
    if vk is None:
        return None
    u = ctypes.windll.user32
    state = (ctypes.c_ubyte * 256)()
    for v in (0x11, 0x12, 0xA2, 0xA5):  # VK_CONTROL, VK_MENU, VK_LCONTROL, VK_RMENU: AltGr
        state[v] = 0x80
    if mods & Qt.KeyboardModifier.ShiftModifier:
        state[0x10] = 0x80
    buf = ctypes.create_unicode_buffer(8)
    hkl = u.GetKeyboardLayout(0)
    n = u.ToUnicodeEx(vk, u.MapVirtualKeyW(vk, 0), state, buf, 8, 0x4, hkl)  # 0x4: leave the keyboard's state alone
    ch = buf.value[:n] if n > 0 else ""
    return ch if ch and ch.isprintable() and not ch.isspace() else None


class _WinFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_dispatcher_MSG", b"windows_generic_MSG"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and BASE_ID <= msg.wParam < BASE_ID + 0x100:
                self.owner._hit(msg.wParam - BASE_ID)
                return True, 0
        return False, 0


# -- the shortcuts ---------------------------------------------------------------------------------------------------------------------
class Hotkeys(QObject):
    """The owner's global shortcuts, held for Monday while it runs. fired(action) when one is pressed; released(action) when a held
    one (push-to-talk) is let go. status(action) -> (state, words): on / off / waiting (allow, deny: until an agent asks) / failed /
    unavailable, with why. configure(keymap) takes the owner's keymap and takes the keys again."""
    fired = Signal(str)
    released = Signal(str)
    changed = Signal()

    def __init__(self, km=None, platform=sys.platform):
        super().__init__()
        self.platform = platform
        self.km = keymap(km)
        self.live: set[str] = set()       # allow / deny: something waits to be answered
        self.held_ids: dict[int, str] = {}
        self.states: dict[str, tuple[str, str]] = {}
        self._suspended = False
        self._filter = None
        self._hold = QTimer(self)
        self._hold.setInterval(HOLD_POLL_MS)
        self._hold.timeout.connect(self._poll_hold)
        self._holding: tuple[str, int] | None = None
        self._pyn = None
        self.apply()

    # the owner's keymap
    def configure(self, km):
        self.km = keymap(km)
        self.apply()

    def set_live(self, action, live):
        if (action in self.live) != bool(live):
            (self.live.add if live else self.live.discard)(action)
            self.apply()

    def suspend(self, on=True):
        """While the owner presses new keys for a shortcut: Monday lets go of every key, so the keys reach the Shortcuts page."""
        self._suspended = on
        self.apply()

    def status(self, action):
        return self.states.get(action, ("off", "Off"))

    # taking the keys
    def apply(self):
        self._release_all()
        want = {a: v for a, v in self.km.items() if v["on"] and v["keys"] and not self._suspended and (a not in LIVE_ONLY or a in self.live)}
        for a, v in self.km.items():
            if not v["keys"]:
                self.states[a] = ("off", "No keys: click the keys to set some")
            elif not v["on"]:
                self.states[a] = ("off", "Off")
            elif a in LIVE_ONLY and a not in self.live:
                self.states[a] = ("waiting", "Taken only while an agent's request waits for you")
            elif self._suspended:
                self.states[a] = ("off", "Paused while you set a shortcut")
        if self.platform == "win32":
            self._take_windows(want)
        else:
            self._take_pynput(want)
        self.changed.emit()

    def _release_all(self):
        if self.platform == "win32":
            for i in list(self.held_ids):
                ctypes.windll.user32.UnregisterHotKey(None, BASE_ID + i)
        self.held_ids.clear()
        if self._pyn is not None:
            try:
                self._pyn.stop()
            except Exception:
                pass
            self._pyn = None

    def _take_windows(self, want):
        if self._filter is None and QCoreApplication.instance() is not None:
            self._filter = _WinFilter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        u = ctypes.windll.user32
        for i, (a, v) in enumerate(want.items()):
            mods, key = parse(v["keys"])
            vk = win_vk(key)
            if vk is None:
                self.states[a] = ("failed", f"Failed: Windows has no key for “{v['keys']}” on this keyboard")
                continue
            ctypes.windll.kernel32.SetLastError(0)
            if u.RegisterHotKey(None, BASE_ID + i, win_mods(mods) | MOD_NOREPEAT, vk):
                self.held_ids[i] = a
                self.states[a] = ("on", f"On: {v['keys']} from any app")
                continue
            err = ctypes.windll.kernel32.GetLastError()
            alt = alternate(v["keys"]) if err == ERROR_HOTKEY_ALREADY_REGISTERED else ""
            if alt and not conflict(self.km, a, alt) and \
                    u.RegisterHotKey(None, BASE_ID + i, win_mods(mods | Qt.KeyboardModifier.ShiftModifier) | MOD_NOREPEAT, vk):
                self.held_ids[i] = a  # the keys with Shift do the job; the page says which keys work and why
                self.states[a] = ("on", f"On: {alt} from any app (another app holds {v['keys']})")
                continue
            self.states[a] = ("failed", "Failed: another app already holds " + v["keys"] if err == ERROR_HOTKEY_ALREADY_REGISTERED
                              else f"Failed: Windows refused {v['keys']} (error {err})")

    def _take_pynput(self, want):
        if not want:
            return
        if self.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            for a in want:
                self.states[a] = ("unavailable", "Not available: Wayland lets no app take keys from the others. Set one in your "
                                                 "desktop's own keyboard settings to run Monday's command instead")
            return
        try:
            from pynput import keyboard  # noqa: F401
        except ImportError:
            for a in want:
                self.states[a] = ("unavailable", "Not available here yet: global shortcuts on macOS and Linux need the pynput "
                                                 "package beside Monday (pylib)")
            return
        self._pyn = _PynputKeys(self, want)
        for a in want:
            self.states[a] = self._pyn.state

    # a shortcut pressed
    def _hit(self, i):
        a = self.held_ids.get(i)
        if not a:
            return
        self.fired.emit(a)
        if a in HELD and self.platform == "win32":
            self._holding = (a, win_vk(parse(self.km[a]["keys"])[1]))
            self._hold.start()

    def _poll_hold(self):
        if not self._holding:
            self._hold.stop()
            return
        a, vk = self._holding
        if not ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
            self._hold.stop()
            self._holding = None
            self.released.emit(a)

    def close(self):
        self._release_all()
        if self._filter is not None and QCoreApplication.instance() is not None:
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            self._filter = None


class _PynputKeys:
    """macOS / Linux (X11): one pynput listener for every shortcut: a shortcut fires when all its keys are down, and a held one is let
    go when one of them comes up. (Not run on the owner's PC, which is Windows: checked only for its parsing there.)"""

    def __init__(self, owner: Hotkeys, want):
        from pynput import keyboard
        self.owner, self.keyboard = owner, keyboard
        self.combos = {a: self._keys(v["keys"]) for a, v in want.items()}
        self.down: set = set()
        self.active: set[str] = set()
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self.listener.daemon = True
        self.listener.start()
        trusted = getattr(self.listener, "IS_TRUSTED", True)
        self.state = ("on", "On: from any app") if trusted else (
            "failed", "Failed: macOS hasn't let Monday read the keyboard (System Settings → Privacy & Security → Accessibility)")

    def _keys(self, keys):
        k = self.keyboard
        mods, key = parse(keys)
        out = set()
        if mods & Qt.KeyboardModifier.ControlModifier:
            out.add(k.Key.ctrl)
        if mods & Qt.KeyboardModifier.AltModifier:
            out.add(k.Key.alt)
        if mods & Qt.KeyboardModifier.ShiftModifier:
            out.add(k.Key.shift)
        if mods & Qt.KeyboardModifier.MetaModifier:
            out.add(k.Key.cmd)
        name = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText).lower()
        out.add(getattr(k.Key, name, None) or k.KeyCode.from_char(name))
        return out

    def _norm(self, key):
        k = self.keyboard.Key
        return {k.ctrl_l: k.ctrl, k.ctrl_r: k.ctrl, k.alt_l: k.alt, k.alt_r: k.alt, k.alt_gr: k.alt, k.shift_l: k.shift,
                k.shift_r: k.shift, k.cmd_l: k.cmd, k.cmd_r: k.cmd}.get(key, key)

    def _press(self, key):
        self.down.add(self._norm(key))
        for a, keys in self.combos.items():
            if a not in self.active and keys <= self.down:
                self.active.add(a)
                self.owner.fired.emit(a)  # (a queued signal: the window's thread takes it)

    def _release(self, key):
        self.down.discard(self._norm(key))
        for a in list(self.active):
            if not self.combos[a] <= self.down:
                self.active.discard(a)
                if a in HELD:
                    self.owner.released.emit(a)

    def stop(self):
        self.listener.stop()


def _check():
    from .qtkit import make_app
    app = make_app(["hotkeys"])
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        ok = ok and bool(cond)

    check("keys read and written the same way", canonical("ctrl+alt+m") == "Ctrl+Alt+M" and canonical("Ctrl+Space") == "Ctrl+Space")
    check("a key alone is refused (it would take it from every app), F13-F24 alone are fine",
          parse("M") is None and parse("Space") is None and parse("F13") is not None and parse("Ctrl") is None)
    km = keymap({"notch": {"keys": "Ctrl+Alt+Space", "on": True}, "bogus": {"keys": "Ctrl+X"}, "chat": {"keys": "nonsense+++", "on": "yes"}})
    check("a saved keymap over the defaults, nonsense kept out", km["notch"]["keys"] == "Ctrl+Alt+Space" and "bogus" not in km
          and km["chat"] == defaults()["chat"])
    check("two actions on the same keys are refused", conflict(defaults(), "notch", "Ctrl+Alt+Space") == NAMES["chat"]
          and conflict(defaults(), "chat", "Ctrl+Alt+Space") is None)
    hk = Hotkeys({a: {"keys": v["keys"], "on": False} for a, v in defaults().items()})
    check("off is off, never on", all(hk.status(a)[0] == "off" for a in NAMES))
    if sys.platform == "win32":
        rare = {a: {"keys": "", "on": False} for a in NAMES}
        rare["notch"] = {"keys": "Ctrl+Alt+Shift+F24", "on": True}
        hk.configure(rare)
        check("Windows gave Monday the key (RegisterHotKey)", hk.status("notch")[0] == "on", hk.status("notch")[1])
        got = []
        hk.fired.connect(got.append)
        tid = ctypes.windll.kernel32.GetCurrentThreadId()
        i = next(i for i, a in hk.held_ids.items() if a == "notch")
        ctypes.windll.user32.PostThreadMessageW(tid, WM_HOTKEY, BASE_ID + i, 0)
        for _ in range(20):
            app.processEvents()
        check("its press comes through Qt's event loop", got == ["notch"], str(got))
        other = Hotkeys(rare)
        check("keys another app already holds, Shift in them already: Failed, with why", other.status("notch")[0] == "failed"
              and "already holds" in other.status("notch")[1], other.status("notch")[1])
        other.close()
        check("the Shift twin of some keys", alternate("Ctrl+Alt+Space") == "Ctrl+Alt+Shift+Space" and alternate("Ctrl+Alt+Shift+F24") == ""
              and alternate("nonsense") == "")
        held = {a: {"keys": "", "on": False} for a in NAMES}
        held["notch"] = {"keys": "Ctrl+Alt+F23", "on": True}
        hk.configure(held)
        other = Hotkeys(held)
        check("keys another app holds are taken with Shift, and the owner is told", other.status("notch")[0] == "on"
              and "Ctrl+Alt+Shift+F23" in other.status("notch")[1] and "holds Ctrl+Alt+F23" in other.status("notch")[1], other.status("notch")[1])
        other.close()
        hk.configure(rare)
        check("a shortcut whose keys type a character is on all the same (2026-10-20)", all(v["on"] for a, v in defaults().items()
              if a in ("notch", "chat", "allow", "deny", "circle")))
        check("…and on this keyboard none of the default keys types a character (2026-10-20 night: the letter stays the owner's)",
              not any(types_char(v["keys"]) for v in defaults().values() if v["keys"]), str({a: (v["keys"], types_char(v["keys"])) for a, v in defaults().items()}))
        check("the other keys are used only where the usual ones type", defaults()["talk"]["keys"] == "Ctrl+Space"
              and (defaults()["notch"]["keys"] == "Ctrl+Alt+B") == bool(types_char("Ctrl+Alt+M")))
        hk.suspend(True)
        check("setting a shortcut lets go of the keys", not hk.held_ids and hk.status("notch")[0] == "off")
        hk.suspend(False)
        rare["allow"] = {"keys": "Ctrl+Alt+Shift+F23", "on": True}
        hk.configure(rare)
        check("allow / deny wait for a request", hk.status("allow")[0] == "waiting")
        hk.set_live("allow", True)
        check("… and are taken while one waits", hk.status("allow")[0] == "on", hk.status("allow")[1])
        hk.set_live("allow", False)
        check("… and let go after", hk.status("allow")[0] == "waiting" and "allow" not in hk.held_ids.values())
        print("     Ctrl+Alt+A types:", repr(types_char("Ctrl+Alt+A")), "on this keyboard")
    other_os = Hotkeys(defaults(), platform="linux")
    check("macOS / Linux: each shortcut on, off, waiting, or unavailable with why (no pynput: never claimed on)",
          all(other_os.status(a)[0] in ("unavailable", "off", "waiting", "on") for a in NAMES)
          and other_os.status("circle")[0] in ("unavailable", "on") and other_os.status("circle")[1])
    hk.close()
    return ok


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if _check() else 1)
    print(__doc__)
