"""Power: this PC's own power actions (the owner's brief, 2026-10-26). "Shut down the PC" had gone to Hermes, whose own blocklist refuses
shutdowns from an agent's terminal (rightly), and its "I cannot" was then read as done (TASK-2026-0309). Here each action is one documented
Windows call with fixed arguments: nothing from a model, a web page or the owner's words reaches it but the action's id and a delay in
whole seconds. router.permit asks the owner first for a shutdown, restart, sign-out, hibernate or sleep (HIGH risk, in every approval
mode); executor._power runs the step; what Windows answered is what Monday says.

    ACTIONS               id -> label, risk, whether it takes a delay (the tool registry's "power" capabilities, core/tools.py)
    supported(api)        {id: (yes, why)} on this PC now: the OS and its power capabilities (Modern Standby has no app-started sleep)
    check(id, args)       the arguments made valid, else ValueError
    brief(id, args, ...)  the confirmation's words: what, which PC, what it means, when
    ready(id, api)        None when Windows will take it now, else the result saying why not (asked before Monday stops anything)
    run(id, args, api)    {"ok", "state", "action", "text", "verified", "code", "at", "logged", "pc"}: Windows' own answer
    pending()             the shutdown or restart Monday scheduled, while it stands

States: scheduled (Windows accepted it for a later time), initiated (Windows began it now), done (seen: locked, cancelled, woke), nothing
(no shutdown was scheduled), already (one is scheduled or under way), blocked (Windows refused: a right it lacks, a lock, other people
signed in), failed, unsupported.

python -m core.power          self-check on a stand-in Windows: nothing is powered off, locked or cancelled
python -m core.power --live   the harmless real calls only: what this PC supports and its status
"""
import asyncio
import datetime as _dt
import os
import platform
import re
import subprocess
import sys
import time

WIN = sys.platform == "win32"
MAX_DELAY_S = 24 * 3600     # "shut down in N minutes": at most a day ahead
UNDER_WAY_S = 120           # a shutdown Monday started still counts as under way this long after its time (an app may hold it up to save)
LOCK_SEEN_S = 3.0           # how long the lock screen may take to show before Monday says it didn't see it
SLEPT_MIN_S = 5.0           # a suspend that came back sooner than this didn't happen
LOG_LOOK_S = 120            # how far back Windows' System log is read for the shutdown Monday just started
LOG_TRIES, LOG_GAP_S = 8, 0.4  # Windows writes its event a moment after the call answers (seen 2026-10-26: not there 0.1 s later): read again
REASON_PLANNED = 0x80000000  # SHTDN_REASON_FLAG_PLANNED (major and minor "other"): "Other (Planned)" in Windows' log
NO_WINDOW = 0x08000000 if WIN else 0

ACTIONS = {
    "shutdown": {"label": "Shut down this PC", "risk": "HIGH", "delay": True},
    "restart": {"label": "Restart this PC", "risk": "HIGH", "delay": True},
    "sign_out": {"label": "Sign out of Windows", "risk": "HIGH", "delay": False},
    "hibernate": {"label": "Hibernate this PC", "risk": "HIGH", "delay": False},
    "sleep": {"label": "Put this PC to sleep", "risk": "HIGH", "delay": False},
    "lock": {"label": "Lock this PC", "risk": "LOW", "delay": False},
    "cancel_shutdown": {"label": "Cancel the scheduled shutdown", "risk": "LOW", "delay": False},
    "status": {"label": "Check this PC's power status", "risk": "LOW", "delay": False},
}
ASKS = {k for k, v in ACTIONS.items() if v["risk"] == "HIGH"}      # router.permit asks the owner before these, in every mode
ENDS_SESSION = ("shutdown", "restart", "sign_out", "hibernate", "sleep")  # Monday's other tasks can't carry on through these
# What each one does to the PC, for the confirmation (the brief: the operation, the computer, the consequence, the delay)
WHAT = {"shutdown": "Windows closes every app and turns {pc} off.", "restart": "Windows closes every app and starts {pc} again.",
        "sign_out": "Windows closes every app and signs you out; {pc} stays on.",
        "hibernate": "Windows saves what's open to disk and powers {pc} off; it comes back as it was when you turn it on.",
        "sleep": "{pc} sleeps; it wakes as it was."}
UNSAVED = "An app with unsaved work can stop it and ask you first."
# Windows' own words on screen while a later shutdown waits (fixed: no request text ever goes in them)
MESSAGES = {"shutdown": "Monday is shutting down this PC because you asked it to. Say “cancel the shutdown” to stop it.",
            "restart": "Monday is restarting this PC because you asked it to. Say “cancel the shutdown” to stop it."}
MODERN = ("This PC uses Modern Standby, which Windows starts only from its own power button, the lid or Start › Power › Sleep: there's no "
          "documented way for an app to start it.")
# Windows' answers (winerror.h) -> (state, what it means for the owner)
ERRORS = {
    5: ("blocked", "Windows refused: this account may not do that here (access denied)."),
    21: ("blocked", "Windows isn't ready for that yet. Ask again in a moment."),
    87: ("failed", "Windows didn't accept the request's details."),
    1115: ("already", "Windows is already shutting down or restarting."),
    1116: ("nothing", "No shutdown or restart was scheduled, so there was nothing to cancel."),
    1190: ("already", "A shutdown or restart is already scheduled, not by me. Say “cancel the shutdown” to clear it, then ask again."),
    1191: ("blocked", "Someone else is signed in to this PC, and Windows won't shut it down under them."),
    1271: ("blocked", "This PC is locked, and Windows won't do that without forcing open apps closed. Unlock it and ask again."),
    1300: ("blocked", "This Windows account doesn't hold the right to shut this PC down."),
    1314: ("blocked", "This Windows account doesn't hold the right to shut this PC down."),
}
_PENDING = {}   # "p": the one shutdown or restart Monday scheduled {"action", "at", "set"}; Windows' own 1190 covers one it didn't


# -- the real calls ---------------------------------------------------------------------------------------------------------------------
class Win32:
    """Windows' own power calls. Each act returns (ok, Windows' error code: 0 when ok). Fixed arguments only."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as W
        self.c = ctypes
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u32 = ctypes.WinDLL("user32", use_last_error=True)
        self.pp = ctypes.WinDLL("powrprof", use_last_error=True)

        class LUID(ctypes.Structure):
            _fields_ = [("Low", W.DWORD), ("High", W.LONG)]

        class LA(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", W.DWORD)]

        class TP(ctypes.Structure):
            _fields_ = [("Count", W.DWORD), ("Privileges", LA * 1)]

        class Caps(ctypes.Structure):  # SYSTEM_POWER_CAPABILITIES (winnt.h), 76 bytes
            _fields_ = [(n, ctypes.c_ubyte) for n in ("power_button", "sleep_button", "lid", "s1", "s2", "s3", "s4", "s5", "hiberfile",
                                                      "full_wake", "video_dim", "apm", "ups", "thermal", "throttle", "min_throttle",
                                                      "max_throttle", "fast_s4", "hiberboot", "wake_alarm", "aoac", "spin_down",
                                                      "hiberfile_type", "aoac_connectivity")] + \
                       [("spare3", ctypes.c_ubyte * 6), ("batteries", ctypes.c_ubyte), ("short_term", ctypes.c_ubyte),
                        ("scale", W.DWORD * 6), ("wake", W.DWORD * 5)]
        self.LUID, self.LA, self.TP, self.Caps = LUID, LA, TP, Caps
        self.k32.GetCurrentProcess.restype = W.HANDLE
        self.k32.CloseHandle.argtypes = (W.HANDLE,)
        self.k32.QueryUnbiasedInterruptTime.argtypes = (ctypes.POINTER(ctypes.c_ulonglong),)
        self.k32.GetTickCount64.restype = ctypes.c_ulonglong
        self.adv.OpenProcessToken.argtypes = (W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE))
        self.adv.LookupPrivilegeValueW.argtypes = (W.LPCWSTR, W.LPCWSTR, ctypes.POINTER(LUID))
        self.adv.AdjustTokenPrivileges.argtypes = (W.HANDLE, W.BOOL, ctypes.POINTER(TP), W.DWORD, ctypes.c_void_p, ctypes.c_void_p)
        self.adv.InitiateSystemShutdownExW.argtypes = (W.LPWSTR, W.LPWSTR, W.DWORD, W.BOOL, W.BOOL, W.DWORD)
        self.adv.InitiateSystemShutdownExW.restype = W.BOOL
        self.adv.AbortSystemShutdownW.argtypes = (W.LPWSTR,)
        self.adv.AbortSystemShutdownW.restype = W.BOOL
        self.u32.ExitWindowsEx.argtypes = (W.UINT, W.DWORD)
        self.u32.ExitWindowsEx.restype = W.BOOL
        self.u32.LockWorkStation.restype = W.BOOL
        self.pp.SetSuspendState.argtypes = (W.BOOLEAN, W.BOOLEAN, W.BOOLEAN)
        self.pp.SetSuspendState.restype = W.BOOLEAN
        self.pp.GetPwrCapabilities.argtypes = (ctypes.POINTER(Caps),)
        self.pp.GetPwrCapabilities.restype = W.BOOLEAN

    def _call(self, fn, *a):
        self.c.set_last_error(0)
        ok = bool(fn(*a))
        return ok, 0 if ok else (self.c.get_last_error() or 1)

    def privilege(self):
        """SeShutdownPrivilege turned on for this process (Windows asks it of a shutdown, restart, cancel or suspend) -> 0, else the code."""
        from ctypes import wintypes as W
        c, h = self.c, W.HANDLE()
        if not self.adv.OpenProcessToken(self.k32.GetCurrentProcess(), 0x20 | 0x8, c.byref(h)):  # TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY
            return c.get_last_error() or 1
        try:
            luid = self.LUID()
            if not self.adv.LookupPrivilegeValueW(None, "SeShutdownPrivilege", c.byref(luid)):
                return c.get_last_error() or 1
            tp = self.TP(1, (self.LA * 1)(self.LA(luid, 0x2)))  # SE_PRIVILEGE_ENABLED
            c.set_last_error(0)
            if not self.adv.AdjustTokenPrivileges(h, False, c.byref(tp), 0, None, None):
                return c.get_last_error() or 1
            return c.get_last_error()  # 0, or 1300 (ERROR_NOT_ALL_ASSIGNED) when the account doesn't hold the right
        finally:
            self.k32.CloseHandle(h)

    def shutdown(self, delay, restart, message):
        # bForceAppsClosed FALSE: an app with unsaved work may stop it and ask the owner (shutdown.exe /t N would force them closed)
        return self._call(self.adv.InitiateSystemShutdownExW, None, message if delay else None, delay, False, restart, REASON_PLANNED)

    def abort(self):
        return self._call(self.adv.AbortSystemShutdownW, None)

    def logoff(self):
        return self._call(self.u32.ExitWindowsEx, 0, REASON_PLANNED)  # EWX_LOGOFF, not forced: apps may ask to save first

    def lock(self):
        return self._call(self.u32.LockWorkStation)

    def suspend(self, hibernate):
        return self._call(self.pp.SetSuspendState, bool(hibernate), False, False)

    def caps(self):
        c = self.Caps()
        if not self.pp.GetPwrCapabilities(self.c.byref(c)):
            return None
        return {k: bool(getattr(c, k)) for k in ("s1", "s2", "s3", "s4", "hiberfile", "aoac", "batteries")}

    def locked(self):
        from .computer import locked
        return locked()

    def awake_s(self):
        """Seconds this PC has been awake since it started: sleep and hibernation left out (QueryUnbiasedInterruptTime)."""
        t = self.c.c_ulonglong()
        self.k32.QueryUnbiasedInterruptTime(self.c.byref(t))
        return t.value / 1e7

    def up_s(self):
        return self.k32.GetTickCount64() / 1000.0

    def name(self):
        return platform.node() or os.environ.get("COMPUTERNAME") or "this PC"

    def logged(self, action, since):
        """Windows' own record of the shutdown or restart this process just started (System log, User32 event 1074) -> True / False; None
        when the log couldn't be read."""
        q = f"*[System[Provider[@Name='User32'] and (EventID=1074) and TimeCreated[timediff(@SystemTime) <= {LOG_LOOK_S * 1000}]]]"
        read = False
        for i in range(LOG_TRIES):
            if i:
                time.sleep(LOG_GAP_S)
            try:
                out = subprocess.run(["wevtutil", "qe", "System", f"/q:{q}", "/c:5", "/rd:true", "/f:xml"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=10, creationflags=NO_WINDOW).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            read = True
            if logged_in(out, action, since, sys.executable):
                return True
        return False if read else None


def logged_in(xml, action, since, exe):
    """Whether Windows' event-1074 XML (wevtutil's) holds a power-off or restart started by `exe` at or after `since` (epoch seconds)."""
    # Windows' words for it (seen 2026-10-26): "shutdown" for InitiateSystemShutdownEx's, "power off" for Start's, "restart" for a restart
    want = ("restart",) if action == "restart" else ("shutdown", "power off")
    for ev in re.findall(r"<Event[ >].*?</Event>", xml or "", re.S):
        data = dict(re.findall(r"<Data Name='(\w+)'>(.*?)</Data>", ev, re.S))
        m = re.search(r"SystemTime='(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z'", ev)
        if not m or data.get("param5", "").strip().lower() not in want:
            continue
        at = _dt.datetime.fromisoformat(m[1]).replace(tzinfo=_dt.timezone.utc).timestamp() + float(f"0.{m[2] or 0}")
        if at >= since - 2 and str(exe).lower() in data.get("param1", "").lower():
            return True
    return False


_API = {}


def win32():
    """The real Windows calls, made once (None off Windows)."""
    if WIN and "w" not in _API:
        _API["w"] = Win32()
    return _API.get("w")


# -- the rules ----------------------------------------------------------------------------------------------------------------------------
def supported(api=None):
    """What this PC can do now: {id: (yes, why)}. Off Windows, nothing: Monday's power actions are Windows' own calls."""
    api = api or win32()
    if api is None:
        return {k: (False, "Not on this OS: Monday's power actions are Windows' own calls.") for k in ACTIONS}
    caps = api.caps() or {}
    out = {k: (True, "") for k in ACTIONS}
    if not (caps.get("s4") and caps.get("hiberfile")):
        out["hibernate"] = (False, "Hibernate is turned off on this PC (Windows' power options).")
    if not (caps.get("s1") or caps.get("s2") or caps.get("s3")):
        alt = " Say “hibernate” to save everything and power off, or “lock my computer”." if out["hibernate"][0] else \
            " Say “lock my computer” instead."
        out["sleep"] = (False, (MODERN if caps.get("aoac") else "This PC has no sleep state Windows lets an app start.") + alt)
    return out


def check(action, args=None):
    """The action's arguments made valid: a shutdown or restart takes {"delay": whole seconds, 0 to MAX_DELAY_S}; the others take nothing."""
    if action not in ACTIONS:
        raise ValueError(f"“{action}” isn't one of Monday's power actions.")
    a = dict(args or {})
    extra = sorted(set(a) - ({"delay"} if ACTIONS[action]["delay"] else set()))
    if extra:
        raise ValueError(f"{ACTIONS[action]['label']} takes no {', '.join(extra)}.")
    if ACTIONS[action]["delay"]:
        d = a.get("delay", 0)
        if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= MAX_DELAY_S:
            raise ValueError(f"The delay must be whole seconds from 0 to {MAX_DELAY_S}.")
        a["delay"] = d
    return a


def clock(at):
    return time.strftime("%H:%M", time.localtime(at))


def span(s):
    s = int(round(s))
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60} min" if s >= 60 else f"{s} s"


def when(delay, now=None):
    """"now", or "at 14:32 (in 10 min)"."""
    return "now" if not delay else f"at {clock((now or time.time()) + delay)} (in {span(delay)})"


def brief(action, args=None, pc=None, running=0, now=None, after=None):
    """The confirmation's words: its first line is the act itself ("Shut down MSI · now"), then what it means and when. after: what was done
    first and seen ("save my document"), for "…and then shut down my computer"."""
    a = check(action, args)
    pc = pc or (win32() or Win32Name()).name()
    lines = [f"{ACTIONS[action]['label'].replace('this PC', pc)} · {when(a.get('delay', 0), now)}"]
    if after:
        lines.append(f"Done first, and seen on screen: {after}.")
    if action in WHAT:
        lines.append(WHAT[action].format(pc=pc) + (f" {UNSAVED}" if action in ("shutdown", "restart", "sign_out") else ""))
    if running and action in ENDS_SESSION:
        lines.append(f"Monday stops its {running} running task{'s' if running != 1 else ''} first.")
    if a.get("delay"):
        lines.append("Say “cancel the shutdown” before then to stop it.")
    return "\n".join(lines)


class Win32Name:
    def name(self):
        return platform.node() or "this PC"


def pending(now=None):
    """The shutdown or restart Monday scheduled, while it stands: {"action", "at", "set"}, else None."""
    p = _PENDING.get("p")
    if p and (now or time.time()) > p["at"] + UNDER_WAY_S:
        _PENDING.clear()
        p = None
    return p


def _out(action, state, text, ok=None, verified=None, code=None, **more):
    ok = state in ("scheduled", "initiated", "done", "nothing") if ok is None else ok
    return {"ok": ok, "state": state, "action": action, "text": text, "verified": verified if ok else False, "code": code, **more}


def _refused(action, code):
    state, words = ERRORS.get(code, ("failed", None))
    if words is None:
        import ctypes
        words = f"Windows refused (error {code}: {ctypes.FormatError(code).strip().rstrip('.') if WIN else 'unknown'})."
    return _out(action, state, words, ok=state == "nothing", verified=True if state == "nothing" else False, code=code)


def ready(action, api=None):
    """None when Windows will take `action` now (supported here, and the shutdown right turned on where it needs it), else the result that
    says why not. Asked before Monday stops anything for it."""
    api = api or win32()
    yes, why = supported(api).get(action, (False, "Not one of Monday's power actions."))
    if not yes:
        return _out(action, "unsupported", why, ok=False)
    if action in ("shutdown", "restart", "cancel_shutdown", "hibernate", "sleep"):
        err = api.privilege()
        if err:
            return _refused(action, err)
    return None


def status_words(api, now=None):
    now = now or time.time()
    sup = supported(api)
    verbs = {"shutdown": "shut it down", "restart": "restart it", "sign_out": "sign you out", "hibernate": "hibernate it",
             "sleep": "put it to sleep", "lock": "lock it"}
    can = [v for k, v in verbs.items() if sup[k][0]]
    p = pending(now)
    plan = f"A {p['action']} is scheduled {when(max(0, int(p['at'] - now)), now)}." if p else "Nothing is scheduled by me."
    no = [f"No {k}: {sup[k][1]}" for k in ("hibernate", "sleep") if not sup[k][0]]
    return (f"{api.name()} has been up {span(api.up_s())}. {plan} Here I can {', '.join(can[:-1])} and {can[-1]}." +
            (" " + " ".join(no) if no else ""))


async def run(action, args=None, api=None, now=None):
    """Do `action` through Windows and say what Windows answered (the module's states). Never raises for Windows' refusals; a bad argument
    is a failed result with its reason."""
    try:
        a = check(action, args)
    except ValueError as e:
        return _out(action if action in ACTIONS else str(action), "failed", str(e), ok=False)
    api = api or win32()
    now = now or time.time()
    if action == "status":
        if api is None:
            return _out(action, "unsupported", supported(None)["status"][1], ok=False)
        return _out(action, "done", status_words(api, now), verified=True, pc=api.name())
    p = pending(now)
    if action in ("shutdown", "restart") and p:  # asked again while one stands: said, never sent twice
        if p["action"] == action:
            return _out(action, "already", f"A {action} is already {'under way' if p['at'] <= now else 'scheduled ' + when(int(p['at'] - now), now)}.",
                        ok=True, verified=True, at=p["at"])
        return _out(action, "already", f"A {p['action']} is already scheduled {when(max(0, int(p['at'] - now)), now)}. Say “cancel the shutdown” "
                                       f"first if you'd rather {ACTIONS[action]['label'].split(' this')[0].lower()}.", ok=False, at=p["at"])
    no = ready(action, api)
    if no:
        return no
    pc = api.name()
    if action in ("shutdown", "restart"):
        ok, code = api.shutdown(a["delay"], action == "restart", MESSAGES[action])
        if not ok:
            return _refused(action, code)
        at = now + a["delay"]
        _PENDING["p"] = {"action": action, "at": at, "set": now}
        # a later one: Windows' own log is read for it (its event 1074). Right away, nothing waits on the log: the answer and the receipt go out
        # while Windows closes the apps, and the PC turning off is what shows it.
        logged = await asyncio.to_thread(api.logged, action, now) if a["delay"] else None
        verb = "restart" if action == "restart" else "shut down"
        text = (f"Windows is {'restarting' if action == 'restart' else 'shutting down'} {pc} now." if not a["delay"] else
                f"Windows will {verb} {pc} {when(a['delay'], now)}. Say “cancel the shutdown” to stop it.")
        if logged is False:
            text += " (Windows answered yes, but its event log doesn't show it yet.)"
        return _out(action, "scheduled" if a["delay"] else "initiated", text, verified=logged, at=at, logged=logged, pc=pc)
    if action == "cancel_shutdown":
        ok, code = api.abort()
        if not ok:
            return _refused(action, code)
        was = _PENDING.pop("p", None)
        return _out(action, "done", f"Cancelled the {was['action']} that was set for {clock(was['at'])}." if was else
                    "Cancelled the shutdown or restart Windows had scheduled.", verified=True, pc=pc)
    if action == "sign_out":
        ok, code = api.logoff()
        return _out(action, "initiated", "Windows is signing you out now.", verified=None, pc=pc) if ok else _refused(action, code)
    if action == "lock":
        if api.locked():
            return _out(action, "done", f"{pc} is already locked.", verified=True, pc=pc)
        ok, code = api.lock()
        if not ok:
            return _refused(action, code)
        end = time.monotonic() + LOCK_SEEN_S
        while not api.locked() and time.monotonic() < end:
            await asyncio.sleep(0.2)
        seen = api.locked()
        return _out(action, "done" if seen else "initiated", f"Locked {pc}." if seen else
                    "Windows accepted the lock, but I couldn't see the lock screen yet.", verified=seen, pc=pc)
    # hibernate / sleep: the call returns only once the PC is awake again, so the time it was away is the evidence
    t0, a0 = time.time(), api.awake_s()
    ok, code = await asyncio.to_thread(api.suspend, action == "hibernate")
    away = (time.time() - t0) - (api.awake_s() - a0)
    if not ok:
        return _refused(action, code)
    if away < SLEPT_MIN_S:
        return _out(action, "failed", f"Windows answered yes, but {pc} didn't {'hibernate' if action == 'hibernate' else 'sleep'}.", ok=False, pc=pc)
    return _out(action, "done", f"{'Hibernated' if action == 'hibernate' else 'Slept'} for {span(away)}; back at {clock(time.time())}.",
                verified=True, pc=pc)


def jev_criteria(api=None):
    """The power actions this PC supports, as Jev's `power_action` question offers them (dynamic: an unsupported one isn't offered)."""
    sup = supported(api)
    words = {"shutdown": "Shut down or turn off this computer", "restart": "Restart or reboot this computer",
             "sign_out": "Sign out of or log off Windows", "hibernate": "Hibernate this computer", "sleep": "Put this computer to sleep",
             "lock": "Lock this computer's screen", "cancel_shutdown": "Cancel a shutdown or restart that is scheduled",
             "status": "Say whether a shutdown is scheduled, or what this computer's power can do"}
    return {k: v for k, v in words.items() if sup[k][0]} | {"none": "Not about this computer's power."}


# -- the self-check -------------------------------------------------------------------------------------------------------------------
class Stand:
    """A stand-in Windows for the self-check: answers as scripted, records every call, never powers anything off."""

    def __init__(self, caps=None, answers=None, priv=0, logged=True, lock_shows=True, away=30.0):
        self.calls, self.answers, self.priv, self._logged, self.lock_shows, self.away = [], dict(answers or {}), priv, logged, lock_shows, away
        self._caps = caps if caps is not None else {"s1": False, "s2": False, "s3": False, "s4": True, "hiberfile": True, "aoac": True}
        self.is_locked, self.slept = False, 0.0

    def _ans(self, k):
        code = self.answers.get(k, 0)
        return (code == 0, code)

    def privilege(self):
        self.calls.append(("privilege",))
        return self.priv

    def shutdown(self, delay, restart, message):
        self.calls.append(("shutdown", delay, restart, message))
        return self._ans("restart" if restart else "shutdown")

    def abort(self):
        self.calls.append(("abort",))
        return self._ans("abort")

    def logoff(self):
        self.calls.append(("logoff",))
        return self._ans("logoff")

    def lock(self):
        self.calls.append(("lock",))
        ok = self._ans("lock")
        self.is_locked = ok[0] and self.lock_shows
        return ok

    def suspend(self, hibernate):
        self.calls.append(("suspend", hibernate))
        ok = self._ans("suspend")
        if ok[0]:
            self.slept += self.away
        return ok

    def caps(self):
        return dict(self._caps)

    def locked(self):
        return self.is_locked

    def awake_s(self):
        return time.time() - self.slept   # the wall clock less the time "away": what QueryUnbiasedInterruptTime would say

    def up_s(self):
        return 3 * 3600 + 12 * 60

    def name(self):
        return "MSI"

    def logged(self, action, since):
        self.calls.append(("logged", action))
        return self._logged


def _check():
    ok = 0

    def same(got, want, what):
        nonlocal ok
        assert got == want, f"{what}: {got!r} != {want!r}"
        ok += 1

    def go(action, args=None, api=None, now=None):
        return asyncio.run(run(action, args, api, now))
    t = time.time()
    # arguments: only a whole-second delay, only where it belongs
    same(check("shutdown"), {"delay": 0}, "a shutdown is right away by default (the owner, 2026-10-26)")
    same(check("restart", {"delay": 600}), {"delay": 600}, "a delay in seconds")
    for bad in ({"delay": -1}, {"delay": MAX_DELAY_S + 1}, {"delay": "60"}, {"delay": True}, {"delay": 1.5}, {"command": "shutdown /s"}):
        try:
            check("shutdown", bad)
            raise AssertionError(f"{bad} was accepted")
        except ValueError:
            ok += 1
    for bad_action, bad_args in (("lock", {"delay": 5}), ("format_c", {}), ("sign_out", {"delay": 0})):
        try:
            check(bad_action, bad_args)
            raise AssertionError(f"{bad_action} {bad_args} was accepted")
        except ValueError:
            ok += 1
    r = go("shutdown", {"delay": "now; rm -rf /"}, Stand())
    same((r["ok"], r["state"]), (False, "failed"), "invalid parameters: a failed result, nothing called")
    # what this PC supports: Modern Standby has no app-started sleep; hibernate when Windows has it on
    s = supported(Stand())
    same((s["sleep"][0], s["hibernate"][0], s["shutdown"][0]), (False, True, True), "Modern Standby: no sleep, hibernate yes")
    assert "Modern Standby" in s["sleep"][1] and "hibernate" in s["sleep"][1], s["sleep"]
    s = supported(Stand(caps={"s3": True, "s4": False, "hiberfile": False, "aoac": False}))
    same((s["sleep"][0], s["hibernate"][0]), (True, False), "an S3 PC sleeps; hibernate off")
    same(set(jev_criteria(Stand())) - {"none"}, set(ACTIONS) - {"sleep"}, "Jev is offered only what this PC supports")
    # shut down now: the right turned on, one fixed call, Windows' log is the evidence; a second ask isn't sent again
    _PENDING.clear()
    w = Stand()
    r = go("shutdown", {}, w, t)
    same((r["ok"], r["state"], r["verified"]), (True, "initiated", None), "shutdown now: initiated; nothing waits on the log")
    same(w.calls, [("privilege",), ("shutdown", 0, False, MESSAGES["shutdown"])], "exactly the privilege, then one fixed call, no log read")
    assert r["text"] == "Windows is shutting down MSI now.", r["text"]
    r = go("shutdown", {}, w, t + 5)
    same((r["ok"], r["state"], len(w.calls)), (True, "already", 2), "a duplicate is said, never sent")
    r = go("restart", {}, w, t + 6)
    same((r["ok"], r["state"], len(w.calls)), (False, "already", 2), "a restart while a shutdown stands: not sent, said")
    same(pending(t + UNDER_WAY_S + 1), None, "a started shutdown is forgotten once it's long past")
    # restart in 10 minutes, then cancelled: Windows' abort, and nothing left pending
    _PENDING.clear()
    w = Stand()
    r = go("restart", {"delay": 600}, w, t)
    same((r["state"], r["at"], r["verified"]), ("scheduled", t + 600, True), "a later restart is scheduled for its time, seen in Windows' log")
    assert f"at {clock(t + 600)} (in 10 min)" in r["text"] and "cancel the shutdown" in r["text"], r["text"]
    same(w.calls[-2:], [("shutdown", 600, True, MESSAGES["restart"]), ("logged", "restart")], "the restart flag, Windows' fixed notice, its log")
    r = go("cancel_shutdown", {}, w, t + 10)
    same((r["ok"], r["state"], pending(t + 10)), (True, "done", None), "cancelled, nothing pending")
    assert r["text"] == f"Cancelled the restart that was set for {clock(t + 600)}.", r["text"]
    r = go("cancel_shutdown", {}, Stand(answers={"abort": 1116}), t + 11)
    same((r["ok"], r["state"]), (True, "nothing"), "a cancel with nothing scheduled says so")
    # Windows refusing: the right missing, a locked PC, someone else signed in, already scheduled elsewhere, an unknown error
    _PENDING.clear()
    w = Stand(priv=1300)
    r = go("shutdown", {}, w, t)
    same((r["ok"], r["state"], [c[0] for c in w.calls]), (False, "blocked", ["privilege"]), "no right: blocked, nothing sent")
    for code, state in ((1271, "blocked"), (1191, "blocked"), (1190, "already"), (5, "blocked"), (1234, "failed")):
        _PENDING.clear()
        r = go("shutdown", {}, Stand(answers={"shutdown": code}), t)
        same((r["ok"], r["state"], r["code"]), (False, state, code), f"Windows' {code}")
        assert pending(t) is None, "a refused shutdown leaves nothing pending"
    assert "error 1234" in r["text"], r["text"]
    # Windows answered yes but its log doesn't show it: said, not hidden
    _PENDING.clear()
    r = go("shutdown", {"delay": 60}, Stand(logged=False), t)
    same((r["ok"], r["verified"]), (True, False), "no log entry: not verified")
    assert "event log doesn't show it" in r["text"], r["text"]
    _PENDING.clear()
    # lock: seen locked, or said that it wasn't seen
    w = Stand()
    r = go("lock", {}, w, t)
    same((r["ok"], r["state"], r["verified"], w.calls), (True, "done", True, [("lock",)]), "lock: seen locked")
    r = go("lock", {}, w, t)
    same((r["state"], len(w.calls)), ("done", 1), "already locked: nothing sent")
    global LOCK_SEEN_S
    LOCK_SEEN_S, keep = 0.3, LOCK_SEEN_S
    r = go("lock", {}, Stand(lock_shows=False), t)
    LOCK_SEEN_S = keep
    same((r["ok"], r["state"], r["verified"]), (True, "initiated", False), "lock asked but not seen: said")
    r = go("lock", {}, Stand(answers={"lock": 5}), t)
    same((r["ok"], r["state"]), (False, "blocked"), "LockWorkStation refused")
    # sign out, hibernate, sleep
    w = Stand()
    r = go("sign_out", {}, w, t)
    same((r["ok"], r["state"], w.calls), (True, "initiated", [("logoff",)]), "sign out: one call, no right needed")
    w = Stand()
    r = go("hibernate", {}, w, t)
    same((r["ok"], r["state"], w.calls[-1]), (True, "done", ("suspend", True)), "hibernate: away for a while, then back")
    assert r["text"].startswith("Hibernated for 30 s"), r["text"]
    r = go("hibernate", {}, Stand(away=0.0), t)
    same((r["ok"], r["state"]), (False, "failed"), "a suspend that never left: failed")
    w = Stand()
    r = go("sleep", {}, w, t)
    same((r["ok"], r["state"], w.calls), (False, "unsupported", []), "sleep on Modern Standby: unsupported, nothing called")
    # status and the confirmation's words
    r = go("status", {}, Stand(), t)
    assert r["ok"] and r["text"].startswith("MSI has been up 3h 12m.") and "Nothing is scheduled by me." in r["text"], r
    assert "shut it down, restart it, sign you out, hibernate it and lock it." in r["text"] and "No sleep: This PC uses" in r["text"], r
    b = brief("shutdown", {}, "MSI", running=2, now=t)
    same(b.split("\n")[0], "Shut down MSI · now", "the confirmation's first line: the act, the PC, when")
    assert "turns MSI off" in b and "unsaved work" in b and "stops its 2 running tasks" in b, b
    b = brief("restart", {"delay": 600}, "MSI", now=t)
    assert f"at {clock(t + 600)} (in 10 min)" in b and "cancel the shutdown" in b, b
    b = brief("shutdown", {}, "MSI", now=t, after="save my document")
    same(b.split("\n")[1], "Done first, and seen on screen: save my document.", "…and then shut down: what was done first is named")
    # Windows' own log, as wevtutil prints it
    xml = ("<Event xmlns='x'><System><TimeCreated SystemTime='2026-10-26T09:35:06.0960000Z'/></System><EventData>"
           "<Data Name='param1'>C:\\monday\\Monday.exe (MSI)</Data><Data Name='param5'>power off</Data></EventData></Event>")
    at = _dt.datetime(2026, 10, 26, 9, 35, 6, tzinfo=_dt.timezone.utc).timestamp()
    same(logged_in(xml, "shutdown", at - 1, "C:\\Monday\\monday.exe"), True, "our power-off in the log")
    same(logged_in(xml.replace("power off", "shutdown"), "shutdown", at - 1, "C:\\Monday\\monday.exe"), True,
         "InitiateSystemShutdownEx's own word for it (the real 1074 of 2026-10-26 06:17:52Z)")
    same(logged_in(xml, "restart", at - 1, "C:\\Monday\\monday.exe"), False, "not a restart")
    same(logged_in(xml, "shutdown", at + 60, "C:\\Monday\\monday.exe"), False, "an older entry isn't this one")
    same(logged_in(xml, "shutdown", at - 1, "C:\\other.exe"), False, "another program's")
    _PENDING.clear()
    print(f"power self-check: OK ({ok} checks)")


if __name__ == "__main__":
    if "--live" in sys.argv:  # the harmless real calls: nothing is shut down, locked or cancelled
        api = win32()
        print({k: v for k, v in supported(api).items()})
        print(asyncio.run(run("status", {}, api))["text"])
        print("privilege:", api.privilege() if api else "N/A")
    else:
        _check()
