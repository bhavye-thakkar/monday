"""Windows' own speech recognition for Monday's desktop app (2026-10-09: the owner chose a native window, which has no browser
speech recognition). One ear: a WinRT continuous dictation session in Monday Core, its words streamed to the page that asked.

- Monday's window (core/native.py Ear) opens a session per sentence and sends Windows' final words as the command.
- Windows' dictation runs on Microsoft's online speech service and needs Settings > Privacy & security > Speech > "Online speech
  recognition" on. When it is off Windows refuses, and Monday says exactly that; it never turns the setting on itself.
- One session at a time: a new start ends the one before (one ear, as the HUD already keeps across pages).
- What is heard goes only to the page's own event stream (an unguessable session id); nothing is logged or stored here.
- Windows' speech runtime has stopped listening without a word to its client (it crashed, 2026-10-09/10; a session also just went
  idle), leaving the ear "listening" for minutes while nothing could be heard. So an open session is checked every WATCH_S against
  Windows' own record of the runtime's microphone use and the recognizer's state; one Windows hasn't captured for DEAD_S is ended
  here, as if Windows had said so, and the page reopens the ear.
- What the ear has really got to is kept as evidence, never assumed from an open session (2026-10-15: Core said "listening" while
  no spoken message had reached it for two days): per session, whether Windows was seen capturing, and how many times it detected
  speech, gave partial words and final words. Counts and times only, never a word. `stage` names the furthest step proven.
"""
import asyncio
import collections
import datetime as dt
import json
import os
import sys
import time

if sys.platform == "win32":
    import winreg
else:  # macOS / Linux (2026-10-19): no Windows dictation there; Core still imports this module, and every reader says "unknown"
    winreg = None

PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pylib")  # the desktop app's own (this projection), beside the app folder:
# E:\monday\pylib here, <install>\pylib for an installed Monday (installer/build.py)
RETRY_S = 5                  # after Windows refused, a new start this soon gets the same answer without asking Windows again
SILENCE_STOP_S = 120         # Windows ends a continuous session after this much silence; the page's endpointing ends it far sooner
QUEUE_MAX = 500
PRIVACY = 0x80045509         # SPERR_SPEECH_PRIVACY_POLICY_NOT_ACCEPTED
BUSY = 0x80131509            # "invalid operation": Windows hasn't let go of the last session yet (ours closing, or Settings' while the
#                              Online speech switch is flipped; seen by the owner 2026-10-10). A moment later it opens.
BUSY_WAITS = (0.3, 0.8, 1.5)  # seconds before each retry inside Core, before the page hears of it
OPEN_S = 10                  # opening a session takes ~1 s; one that hangs is a failure the page hears of, never a request left open
CLOSE_S = 3                  # Windows' cancel answers in ~0.03 s; this only bounds one that doesn't
LASTING = ("service-not-allowed", "audio-capture")  # refusals that stand until the owner changes something: answered again at once
NO_MIC = (0x80045505, 0x8004503A, 0x80070490)  # no audio input / the device is gone
DENIED = 0x80070005          # E_ACCESSDENIED: Windows' microphone privacy switch is off (2026-10-13: the device-wide switch was off, every app
#                              was refused, and Monday said "network … Access is denied" and tried again every 5 s)
SPEECH_DETECTED = 5          # SpeechRecognizerState.SpeechDetected: Windows took the sound for speech (the page's stt_speech mark)
PRIVACY_KEY = r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy"  # HKCU; HasAccepted = 1 once the owner turns it on
MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"  # Value = Allow / Deny: HKLM the
#                              device-wide switch (needs an administrator), HKCU apps, HKCU\NonPackaged desktop apps (Monday is one)
WATCH_S = 2                  # an open session is checked against Windows this often
DEAD_S = 5                   # a session Windows hasn't been capturing for this long is over, whatever Windows said
RUNTIME = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Speech_OneCore", "common", "SpeechRuntime.exe")
USE_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged" + "\\"
           + RUNTIME.replace("\\", "#"))  # HKCU; Windows' record of the runtime's microphone use (what the privacy indicator shows)
RESULT_STATUS = {0: "success", 1: "topic language not supported", 2: "grammar language mismatch", 3: "grammar compilation failure",
                 4: "audio quality failure", 5: "user canceled", 6: "unknown", 7: "timeout exceeded", 8: "pause limit exceeded",
                 9: "network failure", 10: "microphone unavailable"}  # SpeechRecognitionResultStatus
HISTORY = 8                 # closed sessions' evidence kept (in memory) for "what has the ear heard lately"
STAGES = {  # stage -> what it proves, in the owner's words (the furthest one reached wins; see stage())
    "unavailable": "speech recognition isn't available to Monday",
    "blocked": "Windows is refusing the microphone or speech recognition",
    "closed": "not listening right now",
    "open": "a session is open, but the microphone hasn't been seen capturing yet",
    "capturing": "the microphone is capturing; no speech detected yet",
    "speech": "speech was detected, but no words came back yet",
    "partial": "words are coming through (partial transcription)",
    "final": "full sentences are coming through (final transcription)",
}
ERRORS = {  # Windows' reason -> the browser recognizer's error code the pages already handle, and words for the owner
    PRIVACY: ("service-not-allowed", "Windows' Online speech recognition is off. Turn it on in Settings > Privacy & security > Speech, "
                                     "then talk to Monday again."),
    "mic": ("audio-capture", "Windows found no microphone Monday may use (Settings > Privacy & security > Microphone > let desktop apps use it)."),
    DENIED: ("audio-capture", "Windows denied Monday the microphone{which}. Turn it on in Settings > Privacy & security > Microphone "
                              "(Microphone access, and Let desktop apps access your microphone), then talk to Monday again."),
}


def _winrt():
    if PYLIB not in sys.path:
        sys.path.append(PYLIB)
    from winrt.windows.media.speechrecognition import (SpeechContinuousRecognitionMode, SpeechRecognitionScenario,
                                                       SpeechRecognitionTopicConstraint, SpeechRecognizer)
    return SpeechRecognizer, SpeechRecognitionTopicConstraint, SpeechRecognitionScenario, SpeechContinuousRecognitionMode


def privacy():
    """Windows' Online speech recognition switch, read (never written): "on", "off" (never turned on, or turned off), or "unknown"."""
    if winreg is None:
        return "unknown"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PRIVACY_KEY) as k:
            return "on" if winreg.QueryValueEx(k, "HasAccepted")[0] == 1 else "off"
    except FileNotFoundError:  # the key is written the first time the switch is used
        return "off"
    except OSError:
        return "unknown"


def microphone():
    """Windows' microphone privacy switches, read (never written): "allowed", or which one is off: "off for this device" (the device-wide
    switch, HKLM), "off for apps" (HKCU), "off for desktop apps" (HKCU\\NonPackaged); "unknown" when none can be read."""
    def value(root, sub=""):
        try:
            with winreg.OpenKey(root, MIC_KEY + sub) as k:
                return str(winreg.QueryValueEx(k, "Value")[0]).lower()
        except OSError:
            return None
    if winreg is None:
        return "unknown"
    device, apps, desktop = value(winreg.HKEY_LOCAL_MACHINE), value(winreg.HKEY_CURRENT_USER), value(winreg.HKEY_CURRENT_USER, r"\NonPackaged")
    if device == "deny":
        return "off for this device"
    if apps == "deny":
        return "off for apps"
    if desktop == "deny":
        return "off for desktop apps"
    return "allowed" if (apps or desktop or device) else "unknown"


def runtime_capturing():
    """Is Windows' speech runtime reading the microphone right now? Its LastUsedTimeStop is 0 while a capture is open. None when there
    is no record yet (the runtime has never run here) or it can't be read: then nothing is concluded from it."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USE_KEY) as k:
            return winreg.QueryValueEx(k, "LastUsedTimeStop")[0] == 0
    except OSError:
        return None


class CompileFailed(Exception):
    """Windows couldn't prepare dictation (SpeechRecognitionCompilationResult.status): a session opened anyway would hear nothing."""

    def __init__(self, status):
        super().__init__(RESULT_STATUS.get(status, f"status {status}"))
        self.status = status


def reason(e):
    """A Windows failure -> (error code, words). HRESULTs arrive as negative ints on OSError.winerror."""
    if isinstance(e, CompileFailed):
        return ("language-not-supported" if e.status in (1, 2) else "network",
                f"Windows couldn't prepare dictation: {e}. Check Settings > Time & language > Speech (the speech language) and the internet "
                "connection (dictation uses Microsoft's online service).")
    code = (getattr(e, "winerror", None) or 0) & 0xFFFFFFFF
    if code == PRIVACY:
        return ERRORS[PRIVACY]
    if code in NO_MIC:
        return ERRORS["mic"]
    if code == DENIED:
        which = microphone()
        return ERRORS[DENIED][0], ERRORS[DENIED][1].format(which=f": it is {which}" if which not in ("allowed", "unknown") else "")
    if code == BUSY:
        return "network", "Windows' speech recognizer is busy (still closing the last session, or another app has it)."
    return "network", f"Windows' speech recognition failed: {str(e).strip()[:160] or type(e).__name__}"


def stage(st):
    """The furthest step the ear has proven, from GET /api/stt's evidence: switches, then the open session's own counts."""
    if not st["available"]:
        return "unavailable"
    if st["privacy"] == "off" or str(st["microphone"]).startswith("off"):
        return "blocked"
    s = st.get("session")
    if not s:
        return "closed"
    for k in ("final", "partial", "speech"):
        if s[k]:
            return k
    return "capturing" if s["captured"] or st.get("capturing") else "open"


def recent_words(recent):
    """Closed sessions' evidence in a sentence, the newest first: what an owner needs to tell "it hears nothing" from "it never ran"."""
    if not recent:
        return "No session has run since Monday Core started."
    n = len(recent)
    got = {k: sum(1 for r in recent if r[k]) for k in ("captured", "speech", "partial", "final")}
    last = recent[-1]
    ago = max(0, round(time.time() - last["closed_at"]))
    return (f"Last {n} session{'s' if n > 1 else ''}: Windows seen capturing in {got['captured']}, speech detected in {got['speech']}, "
            f"partial words in {got['partial']}, final words in {got['final']}. The last closed {ago} s ago ({last['why'] or 'ended'}).")


async def close(rec):
    """Let go of a recognizer (Windows' speech engine), off the event loop; one that doesn't answer in CLOSE_S is left to its thread."""
    try:
        await asyncio.wait_for(asyncio.to_thread(rec.close), CLOSE_S)
    except Exception:
        pass


class Ear:
    """Windows' online dictation (WinRT). Since 2026-10-15 only when the owner picks it (settings default_stt "windows"): on this PC it
    detected speech and returned no words for two days (138 sessions, core.log); Local is the default."""
    id, label = "windows", "Windows speech recognition (online dictation)"

    def __init__(self, backend=None, capturing=None, log=None):
        self.backend = backend    # tests: a stand-in for the WinRT classes
        self.capturing = capturing or runtime_capturing  # tests: a stand-in for Windows' record of the runtime's microphone use
        self.log = log or (lambda text: None)            # Monday's log (core.log): the sessions' lifecycle, never a word heard
        self.watch = None         # the open session's watchdog (_watch)
        self.releasing = None     # the last session's release (cancel + close), finished before the next opens
        self.sid, self.rec, self.session = None, None, None
        self.queues = {}          # session id -> asyncio.Queue of {"type": start|partial|final|error|end, ...}
        self.loop = None
        self.last_error = None
        self.started_at = None
        self.failed = None           # (monotonic time, code, words) of the last lasting refusal (LASTING)
        self.gone = set()            # sessions their page ended (or left) while their start was still under way
        self.cur = None              # the open session's evidence: opened_at, captured, speech/partial/final counts, last_at
        self.recent = collections.deque(maxlen=HISTORY)  # closed sessions' evidence, oldest first
        self.said_language = False   # the recognizer's language and the constraint's compile result are logged once per Core

    def status(self):
        try:
            (self.backend or _winrt)()
            ok, why = True, None
        except Exception as e:  # the projection isn't installed
            ok, why = False, f"Windows speech recognition isn't available to Monday ({type(e).__name__})."
        st = {"available": ok, "detail": why or self.last_error, "listening": self.sid is not None, "privacy": privacy(),
              "microphone": microphone(), "capturing": self.capturing(),
              "session": dict(self.cur, open_s=round(time.time() - self.cur["opened_at"], 1)) if self.cur else None,
              "recent": list(self.recent)}
        st["stage"] = stage(st)
        return st

    def summary(self):
        """(stage, sentence) for the status pages: what the desktop app's microphone has really done, not that a session is open."""
        st = self.status()
        k = st["stage"]
        words = STAGES[k] + "."
        if k in ("unavailable", "blocked"):
            words = st["detail"] or (f"{STAGES[k]}: online speech recognition is {st['privacy']}, the microphone is {st['microphone']}.")
        elif k != "final":
            words += " " + recent_words(st["recent"])
        return k, f"{self.label}: {words}"

    def queue(self, sid):
        q = self.queues.get(sid)
        if q is None:
            q = self.queues[sid] = asyncio.Queue(QUEUE_MAX)
        return q

    def _push(self, sid, ev):
        if sid == self.sid and self.cur and ev["type"] in ("speech", "partial", "final"):
            if ev["type"] == "speech" or ev.get("text"):
                self.cur[ev["type"]] += 1
                self.cur["last_at"] = time.time()
            elif ev["type"] == "final":  # Windows' result without words: its status says why (network failure, audio quality…)
                self.cur["empty"] += 1
                self.cur["empty_status"] = RESULT_STATUS.get(ev.get("status"), ev.get("status"))
        q = self.queues.get(sid)
        if q is not None and not q.full():
            q.put_nowait(ev)

    def _from_windows(self, sid, ev):  # WinRT raises its events on its own threads
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._push, sid, ev)
            if ev["type"] == "end":  # Windows ended the session itself: let go of it now, not when the next start comes
                self.loop.call_soon_threadsafe(self.log, f"ear: the session ended (status {ev.get('status')})")
                self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.stop(sid)))

    async def _open(self, sid, Recognizer, Topic, Scenario):
        """One recognizer and its running session, or the exception (a half-made recognizer is released, never left holding
        Windows' speech engine). Windows' plain (not async) calls, making and closing a recognizer, run off the event loop: inside Core
        they held it 1-3 s at a time (2026-10-10, asyncio debug), and every request to Core waited."""
        rec = await asyncio.to_thread(Recognizer)
        try:
            rec.constraints.append(Topic(Scenario.DICTATION, "dictation"))
            compiled = await asyncio.wait_for(rec.compile_constraints_async(), OPEN_S)
            status = int(getattr(compiled, "status", 0) or 0)  # unchecked before 2026-10-15: a failed compile still opens a session
            if not self.said_language:
                self.said_language = True
                lang = getattr(getattr(rec, "current_language", None), "language_tag", None)
                self.log(f"ear: Windows' recognizer language {lang or 'N/A'}, dictation constraint {RESULT_STATUS.get(status, status)}")
            if status:
                raise CompileFailed(status)
            s = rec.continuous_recognition_session
            try:
                s.auto_stop_silence_timeout = dt.timedelta(seconds=SILENCE_STOP_S)
                # Windows' default initial-silence timeout is 5 s: a quiet session ended (status 7) and the page reopened the ear 0.3-1 s
                # later, and a sentence begun meanwhile lost its first words (core.log 2026-10-12: a session every ~6 s). The ear now waits
                # as long as the auto-stop; the page's endpointing ends every sentence long before either.
                rec.timeouts.initial_silence_timeout = dt.timedelta(seconds=SILENCE_STOP_S)
            except (AttributeError, TypeError, OSError):
                pass
            rec.add_state_changed(lambda _r, a: self._from_windows(sid, {"type": "speech"}) if int(a.state) == SPEECH_DETECTED else None)
            rec.add_hypothesis_generated(lambda _r, a: self._from_windows(sid, {"type": "partial", "text": a.hypothesis.text}))
            s.add_result_generated(lambda _s, a: self._from_windows(sid, {"type": "final", "text": a.result.text,
                                                                          "confidence": int(a.result.confidence),
                                                                          "status": int(getattr(a.result, "status", 0) or 0)}))
            s.add_completed(lambda _s, a: self._from_windows(sid, {"type": "end", "status": int(a.status)}))
            await asyncio.wait_for(s.start_async(), OPEN_S)  # (the overload with a mode is a different name in this projection)
            return rec, s
        except Exception:
            await close(rec)
            raise

    async def start(self, sid, hold=False):
        """Open the ear for this page's session (ending any other first). Errors go to the session's stream, never raised. Windows'
        "busy" is tried again here (BUSY_WAITS) before the page hears of it; a lasting refusal is answered again at once."""
        self.loop = asyncio.get_running_loop()
        self.queue(sid)
        self.gone.discard(sid)
        if self.sid and self.sid != sid:
            await self.stop(self.sid, "another session starts (one ear)")
        if self.failed and time.monotonic() - self.failed[0] < RETRY_S:
            self._push(sid, {"type": "error", "code": self.failed[1], "message": self.failed[2]})
            self._push(sid, {"type": "end"})
            return False
        if self.releasing and not self.releasing.done():  # the last recognizer is still closing: one at a time (Windows' runtime is one)
            await self.releasing
        Recognizer, Topic, Scenario, Mode = (self.backend or _winrt)()
        failure = None
        for wait in (0, *BUSY_WAITS):
            await asyncio.sleep(wait)
            if sid in self.gone:  # the page ended it while Windows was busy
                return False
            try:
                rec, s = await self._open(sid, Recognizer, Topic, Scenario)
                failure = None
                break
            except Exception as e:
                failure = e
                if ((getattr(e, "winerror", None) or 0) & 0xFFFFFFFF) != BUSY:
                    break
        if failure is not None:
            code, words = reason(failure)
            self.last_error = words
            if code in LASTING:
                self.failed = (time.monotonic(), code, words)
            self._push(sid, {"type": "error", "code": code, "message": words})
            self._push(sid, {"type": "end"})
            return False
        if sid in self.gone:  # ended while it opened: never leave an ear open for a page that has gone
            self.log("ear: the session opened for a page that had already left; cancelled")
            try:
                await asyncio.wait_for(s.cancel_async(), CLOSE_S)
            except Exception:
                pass
            await close(rec)
            return False
        if self.sid and self.sid != sid:  # another page's start won while this one waited: one ear
            await self.stop(self.sid, "a later session won the start (one ear)")
        self.sid, self.rec, self.session, self.started_at, self.last_error, self.failed = sid, rec, s, time.time(), None, None
        self.cur = {"opened_at": self.started_at, "captured": False, "speech": 0, "partial": 0, "final": 0, "last_at": None,
                    "empty": 0, "empty_status": None}
        self.watch = asyncio.ensure_future(self._watch(sid, rec))
        self._push(sid, {"type": "start"})
        return True

    async def stop(self, sid=None, why=""):
        """Close the ear (this session's, or whichever is open), at once. The page already has the words (partial results come as
        they are heard), so the session is cancelled, never "stopped": Windows ends a quiet session by itself after ~5 s, and
        stop_async on a session it had ended hung 30 s and more (2026-10-10). Each hung stop held one of the page's six connections
        to Core, and with them all taken, the owner's typed messages waited and never went. `why` (who closed a live session) goes to
        the log: 17 sessions cancelled in a minute on 2026-10-10 22:12 had no explanation."""
        if sid and sid != self.sid:
            self.gone.add(sid)
            self._push(sid, {"type": "end"})
            return False
        s, rec, was = self.session, self.rec, self.sid
        if was and why and s is not None:
            self.log(f"ear: the open session is closed: {why}")
        self.sid = self.rec = self.session = None
        if self.cur:  # what this session proved, kept and logged (counts only, never a word)
            c, self.cur = self.cur, None
            c.update(closed_at=time.time(), why=why or None)
            self.recent.append(c)
            self.log(f"ear: session over after {c['closed_at'] - c['opened_at']:.0f} s: Windows seen capturing {c['captured']}, "
                     f"speech {c['speech']}, partial {c['partial']}, final {c['final']}"
                     + (f", results without words {c['empty']} ({c['empty_status']})" if c["empty"] else ""))
        if self.watch:
            self.watch.cancel()
            self.watch = None
        if s is not None or rec is not None:
            self.releasing = asyncio.ensure_future(self._release(s, rec))
            await self.releasing
        if was:
            self._push(was, {"type": "end"})
        return bool(was)

    async def finish(self, sid):
        """Windows decides when its sentence ends: nothing to do."""
        return False

    async def _release(self, s, rec):
        try:
            if s is not None:
                await asyncio.wait_for(s.cancel_async(), CLOSE_S)
        except Exception:  # already ended by Windows ("invalid operation", raised at once), or a cancel that hangs
            pass
        if rec is not None:
            await close(rec)

    async def _watch(self, sid, rec):
        """While this session is open, see that Windows is still listening for it (see the module notes): the recognizer's own state,
        and Windows' record of its runtime's microphone use. Not captured for DEAD_S: ended here, as if Windows had said so."""
        misses = 0
        while self.sid == sid:
            await asyncio.sleep(WATCH_S)
            if self.sid != sid:
                return
            try:
                idle = await asyncio.to_thread(lambda: int(rec.state) == 0)  # Idle: no session runs on this recognizer any more
            except Exception:  # the recognizer's runtime is gone
                idle = True
            cap = self.capturing()
            if cap and self.cur and self.sid == sid:
                self.cur["captured"] = True
            misses = misses + 1 if (idle or cap is False) else 0
            if misses * WATCH_S >= DEAD_S:
                self.log(f"ear: Windows stopped listening for the open session (recognizer idle: {idle}, runtime capturing: {cap}); "
                         "ending it so the page reopens the ear")
                self._from_windows(sid, {"type": "end", "status": "dead"})
                return

    def drop(self, sid):
        self.queues.pop(sid, None)
        self.gone.add(sid)
        while len(self.gone) > QUEUE_MAX:  # a session id is spent once its page left; the set only needs the recent ones
            self.gone.pop()


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_PYTHON = os.environ.get("MONDAY_VOICE_PYTHON") or r"E:\monday\voice\env\python.exe"  # = core/speech.py's: the voice environment
ENGINE_LOAD_S = 60           # the engine's start (Python, CTranslate2, the model): ~3 s here; a load that hangs is a failure said so
ENGINE_RETRY_S = 30          # after the engine failed to load (2026-10-15: no memory for the model), a start this soon gets that answer
VOCAB_WAIT_S = 0.3           # the names to expect are asked for each sentence; the app list can still be loading after a start: never a late ear
VOCAB_MAX = 800              # characters of names the ear is told to expect: Whisper keeps ~220 tokens of hints, from the front


def names(*groups, limit=VOCAB_MAX):
    """The ear's names to expect (Whisper's hotwords), the most important group first: each name once (case aside), until `limit`
    characters."""
    out, seen, n = [], set(), 0
    for w in (str(x).strip() for g in groups for x in g if x and str(x).strip()):
        if w.lower() in seen:
            continue
        if n + len(w) + 2 > limit:
            break
        seen.add(w.lower())
        out.append(w)
        n += len(w) + 2
    return ", ".join(out)


class Local:
    """Monday's own ear (core/hear.py): the microphone and Silero VAD on this PC; the words read by Google's free recognizer (reader
    "google", the default since 2026-10-15) or by Whisper on this PC ("whisper": nothing leaves it). The engine runs in the voice
    environment's Python, started here on the first session and spoken to over its pipes. The same face as Ear (start, stop, queue,
    drop, status, summary), so core/app.py and the window are unchanged; its status is evidence (the model that loaded, what each
    session proved), never "listening" alone."""
    id = "local"
    LABELS = {"google": "Google's free recognizer", "whisper": "Whisper on this PC", "parakeet": "Parakeet on this PC"}

    def __init__(self, python=None, log=None, patience=lambda: "normal", argv=("-m", "core.hear"), vocab=None, reader=lambda: "whisper",
                 lang=lambda: "en-US", wake=lambda: None):
        from .desktop import named_python  # (Windows: the ear runs as Monday.exe beside the voice environment's python.exe, 2026-10-21)
        self.python, self.argv = python or named_python(VOICE_PYTHON), argv
        self.log = log or (lambda text: None)
        self.word_reader, self.lang = reader, lang  # who reads each sentence's words (asked per sentence: a change needs no restart), and in what language
        self.wake = wake                  # the name of "okay Monday" when the open mic waits for it (voice setting `wake`), else None
        self.woke = collections.OrderedDict()  # sid -> True: the ear heard the wake phrase in that sentence (Monday.for_me trusts it)
        self.careful_model = None         # Parakeet, when the engine loaded it
        self.patience = patience          # the owner's listening patience (core/voice.py PATIENCE): how long a pause ends a sentence
        self.vocab, self.vocab_last = vocab, ""  # the names to expect (Monday.ear_vocab, a coroutine), and the last ones known
        self.committed = {}                      # sid -> what the ear read carefully of that sentence (careful()); never in status()
        self.proc = self.reader = self.starting = None
        self.model, self.failed = None, None   # the model that loaded; (monotonic time, words) of the last failed start
        self.mic_lost = None              # (epoch time, words) of a lasting microphone refusal the ear is retrying by itself (2026-10-27)
        self.on_microphone = lambda: None  # Core's hook (Monday): the ear got the microphone back, the window may listen again
        self.sid, self.cur, self.queues, self.gone = None, None, {}, set()
        self.recent = collections.deque(maxlen=HISTORY)

    # the engine
    async def _engine(self):
        """The engine running with its model -> None, or why not, in words."""
        if self.proc and self.proc.returncode is None and self.model:
            return None
        if self.failed and time.monotonic() - self.failed[0] < ENGINE_RETRY_S:
            return self.failed[1]
        if self.starting is None or self.starting.done():
            self.starting = asyncio.ensure_future(self._spawn())
        return await asyncio.shield(self.starting)

    async def _spawn(self):
        if not os.path.exists(self.python):
            why = f"The ear's Python isn't installed ({self.python}): its environment holds faster-whisper."
            self.failed = (time.monotonic(), why)
            return why
        env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1"}
        t = time.monotonic()
        self.proc = await asyncio.create_subprocess_exec(self.python, *self.argv, cwd=ROOT, env=env, stdin=asyncio.subprocess.PIPE,
                                                         stdout=asyncio.subprocess.PIPE,
                                                         creationflags=0x08000000 if sys.platform == "win32" else 0)  # CREATE_NO_WINDOW
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), ENGINE_LOAD_S)
            ev = json.loads(line) if line else {"message": f"it exited (code {await self.proc.wait()})"}
        except (asyncio.TimeoutError, ValueError) as e:
            ev = {"message": f"it didn't say it was ready within {ENGINE_LOAD_S} s" if isinstance(e, asyncio.TimeoutError) else "it said something unreadable"}
        if ev.get("type") != "ready":
            why = ev.get("message") or "The ear's engine didn't start."
            self.failed = (time.monotonic(), why)
            self.log(f"ear: the engine didn't start: {why}")
            if self.proc.returncode is None:
                self.proc.kill()
            return why
        self.model, self.careful_model, self.failed = ev.get("model"), ev.get("careful"), None
        self.log(f"ear: the engine is ready: Whisper {self.model}" + (f" and {self.careful_model}" if self.careful_model else "")
                 + f" on the CPU, loaded in {ev.get('load_s')} s ({time.monotonic() - t:.1f} s with Python)")
        self.reader = asyncio.ensure_future(self._read(self.proc))
        return None

    async def _read(self, proc):
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            self._on(ev)
        code = await proc.wait()
        self.model = None
        self.log(f"ear: the engine exited (code {code})")
        if self.sid:  # a session it was hearing: said, never left open
            sid = self.sid
            self._push(sid, {"type": "error", "code": "network", "message": "The ear's engine stopped; it starts again with the next sentence."})
            self._close("the engine stopped")
            self._push(sid, {"type": "end"})

    def _send(self, cmd):
        try:
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
        except (AttributeError, OSError, RuntimeError):  # gone between the check and the write: its reader says so
            pass

    def awake(self, seconds):
        """Core heard the owner's name another way (two claps, a snap; Monday.gesture_awake, 2026-10-21): the ear lets the open sentence,
        or the next one within `seconds`, through its own wake gate (hear.Engine.awake). Nothing to tell when the engine isn't running."""
        if self.proc is not None and self.proc.returncode is None:
            self._send({"op": "awake", "seconds": float(seconds)})

    def _on(self, ev):
        t, sid = ev.pop("type", None), ev.pop("sid", None)
        if t == "log":
            self.log(ev.get("text", ""))
            return
        if t == "microphone":  # the ear tried the microphone again by itself after a lasting refusal (hear.MIC_RETRY_S) and it is back
            self.mic_lost = None
            self.log("ear: the microphone is back")
            self.on_microphone()
            return
        if sid is None:
            return
        if t == "error" and ev.get("code") == "audio-capture":  # no microphone, muted or blocked: the ear keeps trying (2026-10-27)
            self.mic_lost = (time.time(), ev.get("message"))
        if sid == self.sid and self.cur:
            if t == "start":
                self.cur["captured"] = True
            elif t in ("speech", "partial", "final"):
                self.cur[t] += 1
                self.cur["last_at"] = time.time()
            if t == "partial" and ev.get("committed"):
                self.committed[sid] = ev.get("text") or ""
        if t == "wake":  # the ear heard "okay Monday" in this sentence, on this PC ("name": the bare name alone let it in: Jev is asked)
            self.woke[sid] = "name" if ev.get("named") else True
            while len(self.woke) > QUEUE_MAX:
                self.woke.popitem(last=False)
        if t == "speech" and ev.get("before_wake"):  # counted as evidence, never shown: before the phrase, nothing was heard for Monday
            return
        self._push(sid, {"type": t, **ev})
        if t == "end" and sid == self.sid:
            self._close(None)

    def careful(self, sid):
        """What the ear has read carefully of this sentence so far (its committed phrases, beam 5): early execution acts only on
        that (2026-10-15: an action started on a quick guess, "Open Node.js" for "open Notepad"). "" before the first pause."""
        return self.committed.get(sid, "")

    # the sessions (the window's sentences)
    def queue(self, sid):
        q = self.queues.get(sid)
        if q is None:
            q = self.queues[sid] = asyncio.Queue(QUEUE_MAX)
        return q

    def _push(self, sid, ev):
        q = self.queues.get(sid)
        if q is not None and not q.full():
            q.put_nowait(ev)

    def _close(self, why):
        self.committed.pop(self.sid, None)
        self.sid, c, self.cur = None, self.cur, None
        if c:
            c.update(closed_at=time.time(), why=why)
            self.recent.append(c)

    async def start(self, sid, hold=False):
        """hold: push-to-talk; the sentence lasts while the owner holds the key (patience "hold"), and finish() at its release ends it."""
        self.queue(sid)
        self.gone.discard(sid)
        if self.sid and self.sid != sid:
            await self.stop(self.sid, "another session starts (one ear)")
        why = await self._engine()
        if why:
            self._push(sid, {"type": "error", "code": "unavailable", "message": f"The ear can't start: {why}"})
            self._push(sid, {"type": "end"})
            return False
        vocab = await self._vocab_now()
        if sid in self.gone:  # the page left while the engine started
            return False
        self.sid = sid
        self.cur = {"opened_at": time.time(), "captured": False, "speech": 0, "partial": 0, "final": 0, "last_at": None, "empty": 0}
        self._send({"op": "start", "sid": sid, "patience": "hold" if hold else self.patience(), "vocab": vocab, "reader": self.word_reader(),
                    "lang": self.lang(), "wake": None if hold else self.wake()})
        return True

    def heard_wake(self, sid):
        """Did the ear hear the wake phrase in this sentence (the open mic, on this PC)?"""
        return bool(sid and self.woke.get(sid))

    def named_wake(self, sid):
        """...and was it only Monday's bare name, not "okay Monday" (2026-10-22)? Core then asks Jev whether the sentence is said to it."""
        return bool(sid and self.woke.get(sid) == "name")

    def _reads(self):
        """Who reads the words now: "google", or this PC ("whisper"); "auto" is Google (Parakeet, the careful reader, went 2026-10-20 night)."""
        r = self.word_reader()
        return "google" if r == "auto" else r

    @property
    def label(self):
        return self.LABELS.get(self._reads(), self.LABELS["whisper"])

    async def _vocab_now(self):
        """The names this sentence should expect (Whisper's hotwords), waited for at most VOCAB_WAIT_S; else the last ones known."""
        if self.vocab:
            try:
                self.vocab_last = await asyncio.wait_for(self.vocab(), VOCAB_WAIT_S) or self.vocab_last
            except asyncio.TimeoutError:  # still loading (the app list, 1-5 s after a start): never a late ear
                pass
            except Exception as e:  # said in core.log, once per kind (2026-10-15: a NameError here was silent, and no names reached the ear)
                why = f"{type(e).__name__}: {e}"
                if why != getattr(self, "vocab_error", None):
                    self.vocab_error = why
                    self.log(f"ear: the names to expect couldn't be made ({why[:200]}); the ear hears without them")
        return self.vocab_last

    async def finish(self, sid):
        """End the sentence now, with its words (the window: a one-step exact command that held still, core/native.py Ear._early)."""
        if sid and sid == self.sid:
            self._send({"op": "finish", "sid": sid})
            return True
        return False

    async def stop(self, sid=None, why=""):
        if sid and sid != self.sid:
            self.gone.add(sid)
            self._push(sid, {"type": "end"})
            return False
        was = self.sid
        if was:
            self._send({"op": "cancel", "sid": was, "why": why})
            if why:
                self.log(f"ear: the open session is closed: {why}")
            self._close(why or "stopped")
            self._push(was, {"type": "end"})
        return bool(was)

    def drop(self, sid):
        self.queues.pop(sid, None)
        self.gone.add(sid)
        while len(self.gone) > QUEUE_MAX:
            self.gone.pop()

    def status(self):
        ok = os.path.exists(self.python)
        why = None if ok else f"The ear's Python isn't installed ({self.python})."
        if self.failed and not self.model:
            why = self.failed[1]
        reads, name = self._reads(), self.wake()
        privacy = "each phrase goes to Google's recognizer" if reads == "google" else "not needed (heard on this PC)"
        if name and reads == "google":
            privacy = f"after “{name}, …” or “okay {name}”, each phrase goes to Google's recognizer (before it, nothing leaves this PC)"
        st = {"available": ok and not (self.failed and not self.model), "detail": why, "listening": self.sid is not None,
              "privacy": privacy, "reader": self.word_reader(), "reads": reads, "wake": f"“{name}, …” or “okay {name}”" if name else None,
              "microphone": microphone(), "microphone_lost": self.mic_lost[1] if self.mic_lost else None,
              "capturing": bool(self.cur and self.cur["captured"]),
              "session": dict(self.cur, open_s=round(time.time() - self.cur["opened_at"], 1)) if self.cur else None,
              "recent": list(self.recent), "engine": {"model": self.model, "careful": self.careful_model,
                                                      "running": bool(self.proc and self.proc.returncode is None),
                                                      "names_expected": len(self.vocab_last.split(", ")) if self.vocab_last else 0}}
        st["stage"] = stage(st)
        return st

    def summary(self):
        st = self.status()
        k = st["stage"]
        words = STAGES[k] + "."
        if k in ("unavailable", "blocked"):
            words = st["detail"] or f"{STAGES[k]}: the microphone is {st['microphone']}."
        elif k != "final":
            words += " " + recent_words(st["recent"])
        live = f" (live words: Whisper {self.model}" + (f"; only after “{self.wake()}, …” or “okay {self.wake()}”)" if self.wake() else ")")
        return k, f"{self.label}{live if self.model else ''}: {words}"


def make(choice=None, log=None, patience=lambda: "normal", vocab=None, reader=lambda: "whisper", lang=lambda: "en-US", wake=lambda: None):
    """The ear Monday listens with: its own (Local, the default since 2026-10-15; reader() says who reads the words, wake() whether the
    open mic waits for "okay Monday"), or Windows' online dictation when the owner chose it (settings default_stt "windows")."""
    return Ear(log=log) if choice == "windows" else Local(log=log, patience=patience, vocab=vocab, reader=reader, lang=lang, wake=wake)


def reader_for(choice):
    """settings default_stt -> who reads the words after "okay Monday": Google's free recognizer (unset, "google", or the old "browser":
    Chrome's was Google's), in the owner's English; it read them better than Parakeet live (2026-10-20: Parakeet wrote "a rigid singer"
    for Arijit Singh), and this PC reads a phrase Google doesn't answer. "local" is this PC alone; "auto" is Parakeet, else Google."""
    return {"local": "whisper", "auto": "auto"}.get(choice, "google")


if __name__ == "__main__":  # python -m core.stt: the session bookkeeping with a stand-in recognizer; --live tries Windows' own once
    import types

    class Ev:
        def __init__(self):
            self.fns = []

        def add(self, fn):
            self.fns.append(fn)

        def fire(self, *a):
            for f in self.fns:
                f(None, *a)

    def fake(fail=None, busy=0, compiled=0):
        busy_left = [busy]  # Windows answers "invalid operation" this many times first (the last session still closing)

        class Session:
            def __init__(self):
                self.result, self.done, self.started, self.stopped = Ev(), Ev(), False, False
                self.add_result_generated, self.add_completed = self.result.add, self.done.add

            async def start_async(self):
                if fail:
                    raise fail
                if busy_left[0] > 0:
                    busy_left[0] -= 1
                    e = OSError("The text associated with this error code could not be found.")
                    e.winerror = -2146233079  # 0x80131509, as the owner saw it 2026-10-10
                    raise e
                self.started = True

            async def stop_async(self):  # as Windows' did on a session it had ended itself (2026-10-10): no answer
                await asyncio.Event().wait()

            async def cancel_async(self):
                self.stopped = True

        class Recognizer:
            made = []

            def __init__(self):
                self.constraints, self.hyp, self.changed, self.continuous_recognition_session = [], Ev(), Ev(), Session()
                self.add_hypothesis_generated, self.add_state_changed = self.hyp.add, self.changed.add
                self.timeouts = types.SimpleNamespace(initial_silence_timeout=dt.timedelta(seconds=5))  # Windows' default
                self.state = 1  # Capturing
                Recognizer.made.append(self)

            async def compile_constraints_async(self):
                return types.SimpleNamespace(status=compiled)

            def close(self):
                pass
        return lambda: (Recognizer, lambda *a: ("topic", a), types.SimpleNamespace(DICTATION=0), types.SimpleNamespace(DEFAULT=0))

    async def check():
        be = fake()
        ear = Ear(be, capturing=lambda: None)  # not the real registry: the owner's own ear may be capturing right now
        assert ear.status()["available"] and ear.status()["privacy"] in ("on", "off", "unknown")
        assert await ear.start("a")
        R = be()[0]
        r = R.made[-1]
        assert r.timeouts.initial_silence_timeout.total_seconds() == SILENCE_STOP_S, "a quiet session is not ended by Windows' 5 s default"
        r.changed.fire(types.SimpleNamespace(state=1))               # Capturing: not speech
        r.changed.fire(types.SimpleNamespace(state=SPEECH_DETECTED))
        r.hyp.fire(types.SimpleNamespace(hypothesis=types.SimpleNamespace(text="open spot")))
        r.continuous_recognition_session.result.fire(types.SimpleNamespace(result=types.SimpleNamespace(text="Open Spotify.", confidence=1)))
        await asyncio.sleep(0.01)
        q = ear.queue("a")
        got = [q.get_nowait()["type"] for _ in range(q.qsize())]
        assert got == ["start", "speech", "partial", "final"], got
        r.continuous_recognition_session.result.fire(types.SimpleNamespace(result=types.SimpleNamespace(text="", confidence=3, status=9)))
        await asyncio.sleep(0.01)
        assert ear.cur["empty"] == 1 and ear.cur["empty_status"] == "network failure", "a result without words is counted, with Windows' why"
        q.get_nowait()
        st = ear.status()  # evidence, not "a session is open": counts only, never the words
        assert st["stage"] == "final" and (st["session"]["speech"], st["session"]["partial"], st["session"]["final"]) == (1, 1, 1), st
        assert "Open Spotify" not in repr(st) and "open spot" not in repr(st), "nothing heard is kept in the status"
        base = {"available": True, "privacy": "on", "microphone": "allowed", "capturing": None}
        none = {"captured": False, "speech": 0, "partial": 0, "final": 0}
        assert stage({**base, "session": None}) == "closed"
        assert stage({**base, "session": none}) == "open", "an open session alone proves nothing"
        assert stage({**base, "capturing": True, "session": none}) == "capturing"
        assert stage({**base, "session": {**none, "captured": True, "speech": 2}}) == "speech"
        assert stage({**base, "privacy": "off", "session": none}) == "blocked"
        assert stage({**base, "microphone": "off for desktop apps", "session": None}) == "blocked"
        assert stage({**base, "available": False, "session": None}) == "unavailable"
        assert await asyncio.wait_for(ear.start("b"), 1) and r.continuous_recognition_session.stopped, "a second page's start ends the first ear"
        assert len(ear.recent) == 1 and ear.recent[0]["final"] == 1 and ear.recent[0]["why"], "a closed session's evidence is kept"
        assert ear.status()["stage"] == "open" and ear.status()["session"]["final"] == 0, "the new session starts with no evidence"
        assert "final words in 1" in recent_words(list(ear.recent)), recent_words(list(ear.recent))
        assert ear.queue("a").get_nowait()["type"] == "end" and ear.sid == "b"
        assert await asyncio.wait_for(ear.stop("b"), 1) and ear.sid is None, "a stop answers at once (cancel), never waits on Windows' stop"
        e = OSError("The speech privacy policy was not accepted prior to attempting a speech recognition.")
        e.winerror = -2147199735
        ear2 = Ear(fake(fail=e))
        assert not await ear2.start("c")
        q = ear2.queue("c")
        ev = q.get_nowait()
        assert ev["type"] == "error" and ev["code"] == "service-not-allowed" and "Online speech recognition" in ev["message"], ev
        assert q.get_nowait()["type"] == "end" and ear2.sid is None and "Online speech" in ear2.status()["detail"]
        made = len(ear2.backend()[0].made) if hasattr(ear2.backend()[0], "made") else None
        assert not await ear2.start("d") and ear2.queue("d").get_nowait()["code"] == "service-not-allowed", "the refusal again, at once"
        assert made is None or len(ear2.backend()[0].made) == made, "Windows isn't asked again within RETRY_S"
        e = OSError("Access is denied.")  # the microphone privacy switch is off (2026-10-13): said as that, with the switch that is off
        e.winerror = -2147024891
        code, words = reason(e)
        assert code == "audio-capture" and "Privacy & security > Microphone" in words and "Access is denied" not in words, words
        assert microphone() in ("allowed", "unknown", "off for this device", "off for apps", "off for desktop apps")
        assert "microphone" in ear.status()

        said = []
        ear_c = Ear(fake(compiled=1), capturing=lambda: None, log=said.append)  # Windows can't prepare dictation: refused, never "listening"
        assert not await ear_c.start("k") and ear_c.sid is None
        ev = ear_c.queue("k").get_nowait()
        assert ev["code"] == "language-not-supported" and "topic language not supported" in ev["message"], ev
        assert said and "dictation constraint topic language not supported" in said[0], said

        global BUSY_WAITS
        BUSY_WAITS = (0.01, 0.01, 0.01)
        ear3 = Ear(fake(busy=1))  # busy once: tried again inside Core; the page only hears that it started
        assert await ear3.start("e") and ear3.sid == "e" and not ear3.failed
        q = ear3.queue("e")
        assert [q.get_nowait()["type"] for _ in range(q.qsize())] == ["start"]
        ear4 = Ear(fake(busy=99))  # busy every time: said as busy, and not cached (the next start asks Windows again)
        assert not await ear4.start("f") and not ear4.failed
        ev = ear4.queue("f").get_nowait()
        assert ev["type"] == "error" and "busy" in ev["message"] and "could not be found" not in ev["message"], ev
        ear5 = Ear(fake())  # Windows ends the session itself: let go of at once
        assert await ear5.start("g")
        ear5.backend()[0].made[-1].continuous_recognition_session.done.fire(types.SimpleNamespace(status=0))
        await asyncio.sleep(0.05)
        assert ear5.sid is None, "a session Windows ended is released, not held until the next start"
        BUSY_WAITS = (0.3, 0.3, 0.3)
        ear6 = Ear(fake(busy=1))  # the page leaves while Windows is busy: no ear stays open for it
        t = asyncio.ensure_future(ear6.start("h"))
        await asyncio.sleep(0.05)
        await ear6.stop("h")
        assert not await t and ear6.sid is None and not [r for r in ear6.backend()[0].made if r.continuous_recognition_session.started and not r.continuous_recognition_session.stopped]

        global WATCH_S, DEAD_S
        WATCH_S, DEAD_S = 0.02, 0.05
        alive, said = [True], []
        ear7 = Ear(fake(), capturing=lambda: alive[0], log=said.append)  # Windows' runtime stops capturing without a word (2026-10-10)
        assert await ear7.start("w")
        await asyncio.sleep(0.15)
        assert ear7.sid == "w", "a session Windows keeps capturing for stays open"
        alive[0] = False
        await asyncio.sleep(0.3)
        assert ear7.sid is None and any("stopped listening" in s for s in said), (ear7.sid, said)
        q = ear7.queue("w")
        got = [q.get_nowait()["type"] for _ in range(q.qsize())]
        assert got[0] == "start" and "end" in got, got
        ear8 = Ear(fake(), capturing=lambda: True)
        assert await ear8.start("x")
        ear8.backend()[0].made[-1].state = 0  # the recognizer itself says idle: no session runs on it
        await asyncio.sleep(0.3)
        assert ear8.sid is None, "a recognizer gone idle under an open session is ended"
        ear9 = Ear(fake(), capturing=lambda: None)  # no record of the runtime at all: nothing is concluded from it
        assert await ear9.start("y")
        await asyncio.sleep(0.2)
        assert ear9.sid == "y"
        await ear9.stop("y")

        # Local: Core's side of core/hear.py, against a stand-in engine that speaks the same pipe protocol (no model, no microphone)
        engine = ("import json, sys, time\n"
                  "say = lambda **e: print(json.dumps(e), flush=True)\n"
                  "say(type='ready', model='stand-in', load_s=0)\n"
                  "for line in sys.stdin:\n"
                  "    c = json.loads(line)\n"
                  "    if c['op'] == 'start' and c['sid'].startswith('hold'):\n"
                  "        say(type='start', sid=c['sid']); say(type='speech', sid=c['sid'])\n"
                  "    elif c['op'] == 'start' and c['sid'].startswith('wake'):\n"
                  "        say(type='log', text='ear: wake ' + str(c.get('wake')))\n"
                  "        for t, x in (('start', {}), ('speech', {'before_wake': True}), ('wake', {}), ('final', {'text': 'Open Notepad.', 'woke': True}), ('end', {})):\n"
                  "            say(type=t, sid=c['sid'], **x)\n"
                  "    elif c['op'] == 'start' and c['sid'].startswith('lost'):\n"
                  "        say(type='error', sid=c['sid'], code='audio-capture', message='no microphone'); say(type='end', sid=c['sid'])\n"
                  "        time.sleep(0.3); say(type='microphone', ok=True)\n"
                  "    elif c['op'] == 'start' and c['sid'].startswith('careful'):\n"
                  "        say(type='start', sid=c['sid']); say(type='partial', sid=c['sid'], text='Open Node.js', committed=False)\n"
                  "        say(type='partial', sid=c['sid'], text='Open Notepad', committed=True)\n"
                  "    elif c['op'] == 'start':\n"
                  "        say(type='log', text='ear: session open (patience ' + c['patience'] + ', vocab ' + str(c.get('vocab')) + ', read by '\n"
                  "            + str(c.get('reader')) + ' in ' + str(c.get('lang')) + ', wake ' + str(c.get('wake')) + ')')\n"
                  "        for t, x in (('start', {}), ('speech', {}), ('partial', {'text': 'open note'}), ('final', {'text': 'Open Notepad.'}), ('end', {})):\n"
                  "            say(type=t, sid=c['sid'], **x)\n"
                  "    elif c['op'] == 'cancel':\n"
                  "        say(type='end', sid=c['sid'])\n"
                  "    elif c['op'] == 'finish':\n"
                  "        say(type='final', sid=c['sid'], text='Open Spotify.'); say(type='end', sid=c['sid'])\n"
                  "    elif c['op'] == 'quit':\n"
                  "        sys.exit(3)\n")
        said = []
        slow = []

        async def ear_vocab():  # Monday.ear_vocab's stand-in: the second sentence finds it slow (the app list still loading)
            if slow:
                await asyncio.sleep(5)
            return "Monday, Notepad, Kiro"
        reader, wake = ["google"], [None]
        loc = Local(python=sys.executable, log=said.append, patience=lambda: "relaxed", argv=("-c", engine), vocab=ear_vocab,
                    reader=lambda: reader[0], lang=lambda: "en-US", wake=lambda: wake[0])
        assert loc.status()["stage"] == "closed" and make("windows").id == "windows" and make("browser").id == make(None).id == "local"
        assert [reader_for(c) for c in (None, "browser", "google", "local", "auto")] == ["google", "google", "google", "whisper", "auto"], \
            "Google reads the words (after the phrase) unless the owner chose this PC alone (\"local\") or Parakeet first (\"auto\")"
        assert loc.label == "Google's free recognizer" and "Google" in loc.status()["privacy"], "where the words go is said"
        assert await loc.start("s1")
        q, got = loc.queue("s1"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert [e["type"] for e in got] == ["start", "speech", "partial", "final", "end"], got
        assert got[3]["text"] == "Open Notepad." and "sid" not in got[3], "the words go to the page, the bookkeeping stays here"
        assert loc.sid is None and loc.recent[-1]["final"] == 1 and loc.recent[-1]["captured"], loc.recent
        assert any("patience relaxed" in s for s in said) and any("stand-in" in s for s in said), said
        assert any("read by google in en-US" in s for s in said), ("each sentence tells the ear who reads it", said)
        reader[0] = "whisper"
        assert loc.label == "Whisper on this PC" and loc.status()["privacy"].startswith("not needed"), "a change needs no restart"
        reader[0] = "google"
        assert any("vocab Monday, Notepad, Kiro" in s for s in said) and loc.status()["engine"]["names_expected"] == 3, "the names reach the ear"
        assert names(["Monday", "Hermes"], ["monday", " Notepad ", None], ["Kiro"], limit=40) == "Monday, Hermes, Notepad, Kiro", \
            "each name once, case aside, the first group first"
        assert names(["a" * 30, "b" * 30], limit=40) == "a" * 30, "cut at the limit, from the end (Whisper keeps the front)"
        slow.append(1)
        t = time.monotonic()
        assert await loc.start("s1b") and time.monotonic() - t < 2, "a slow list never holds the ear (VOCAB_WAIT_S)"
        q, got = loc.queue("s1b"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert sum("vocab Monday, Notepad, Kiro" in s for s in said) == 2, "the last names known, when they are slow to come"
        slow.clear()

        assert await loc.start("s9", hold=True)
        q, got = loc.queue("s9"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert any("patience hold" in s for s in said), "push-to-talk: the ear is told the sentence lasts while the key is held"
        wake[0] = "Monday"  # 2026-10-20: the open mic waits for "okay Monday"; the ear hears it on this PC and says so
        assert await loc.start("wake1")
        q, got = loc.queue("wake1"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert [e["type"] for e in got] == ["start", "wake", "final", "end"] and loc.heard_wake("wake1") and not loc.heard_wake("s1"), \
            ("speech before the phrase is counted, never shown; the sentence that woke is known by its id", got)
        assert any("ear: wake Monday" in s for s in said) and "okay Monday" in loc.status()["privacy"] + str(loc.status()["wake"])
        assert "“Monday, …”" in loc.status()["wake"] and "“Monday, …”" in loc.status()["privacy"] and "“Monday, …”" in loc.summary()[1], \
            ("the name alone opens the ear too (2026-10-22), and the words say so (2026-10-27)", loc.status()["wake"], loc.summary()[1])
        loc._on({"type": "wake", "sid": "nm1", "named": True})  # 2026-10-22: let in by the bare name alone: Core asks Jev about it
        assert loc.heard_wake("nm1") and loc.named_wake("nm1") and not loc.named_wake("wake1") and not loc.named_wake(None)
        back = []  # 2026-10-27: a lasting microphone refusal is remembered; the ear tries again by itself and says when it is back
        loc.on_microphone = lambda: back.append(1)
        assert await loc.start("lost1")
        q, got = loc.queue("lost1"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert got[0]["code"] == "audio-capture" and loc.mic_lost and loc.status()["microphone_lost"] == "no microphone", got
        for _ in range(100):
            if back:
                break
            await asyncio.sleep(0.02)
        assert back == [1] and loc.mic_lost is None and loc.status()["microphone_lost"] is None and any("microphone is back" in s for s in said), \
            ("the engine's `microphone` event clears the refusal and tells Core", back, said[-3:])
        assert await loc.start("s9b", hold=True)
        q, got = loc.queue("s9b"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert any("patience hold" in s and "wake None)" in s for s in said), ("push-to-talk sends no phrase to wait for", said[-3:])
        wake[0] = None
        assert await loc.start("careful1")
        await asyncio.sleep(0.3)
        assert loc.careful("careful1") == "Open Notepad", "only the careful reading counts, never a quick guess"
        assert "Notepad" not in repr(loc.status()) and "Node" not in repr(loc.status()), "status counts, never a word"
        await loc.stop("careful1")
        assert loc.careful("careful1") == "", "a closed sentence keeps nothing"

        async def broken():
            raise NameError("name 'stt' is not defined")
        bad = Local(python=sys.executable, log=said.append, argv=("-c", engine), vocab=broken)
        assert await bad._vocab_now() == "" and await bad._vocab_now() == ""
        assert sum("couldn't be made (NameError" in s for s in said) == 1, "a failure is said in the log, once, never silent"
        assert "Whisper stand-in" in loc.summary()[1] and "Open Notepad" not in repr(loc.status()), "counts only, never a word"
        assert await loc.start("hold") and loc.sid == "hold"
        await asyncio.sleep(0.2)
        assert loc.status()["stage"] == "speech", loc.status()
        assert await loc.stop("hold", "the page stopped it") and loc.sid is None, "a stop cancels in the engine and answers at once"
        assert await loc.start("hold2") and await loc.finish("hold2") and not await loc.finish("other"), "only the open sentence is finished"
        q, got = loc.queue("hold2"), []
        while not got or got[-1]["type"] != "end":
            got.append(await asyncio.wait_for(q.get(), 10))
        assert [e["type"] for e in got][-2:] == ["final", "end"] and loc.sid is None, got
        loc._send({"op": "quit"})  # the engine dies under an open session: the page hears it, and the next start brings it back
        assert await loc.start("s2")
        for _ in range(50):
            if loc.model is None:
                break
            await asyncio.sleep(0.05)
        assert loc.model is None and any("exited" in s for s in said), said
        assert await loc.start("s3") and loc.model == "stand-in", "the next sentence starts the engine again"
        await loc.stop("s3")
        loc._send({"op": "quit"})
        none = Local(python=r"C:\nowhere\python.exe")
        assert not await none.start("x") and none.queue("x").get_nowait()["code"] == "unavailable" and none.status()["stage"] == "unavailable"
        dead = Local(python=sys.executable, argv=("-c", "import sys; sys.exit(2)"))
        assert not await dead.start("y")
        ev = dead.queue("y").get_nowait()
        assert ev["code"] == "unavailable" and "exited" in ev["message"], ev
        assert not await dead.start("z") and dead.proc.returncode == 2, "a failed start is answered again at once (ENGINE_RETRY_S), not retried"

        if "--live" in sys.argv:
            live = Ear()
            from . import wake
            print("windows:", live.status(), "| online speech recognition:", privacy(), "| microphone:", microphone(),
                  "| runtime capturing now:", runtime_capturing(), "| input level (1 s):", wake.mic_level(1.0))
            ok = await live.start("live")
            q = live.queue("live")
            await asyncio.sleep(1.0)
            print("started" if ok else "refused", [q.get_nowait() for _ in range(q.qsize())])
            await live.stop("live")
    asyncio.run(check())
    print("stt self-check: OK")
