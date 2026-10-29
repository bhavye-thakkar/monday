"""Monday's ear (2026-10-15): the microphone, speech detection and the reading of words. Nothing heard is kept.

Why: Windows' online dictation (core/stt.py `Ear`) stopped returning words here. core.log, 2026-10-13 to 10-15: 138 sessions, 0 partial and
0 final words, while Windows captured the microphone and detected speech (one 202 s session: speech 104 times, words 0). This engine is
Monday's default ear since (settings `default_stt`: "local" for Whisper alone, or "windows" for the old one).

"okay Monday" first (2026-10-20, the owner: "when I say okay Monday, then and then only it will hear me, otherwise it won't hear me"):
with the start op's `wake` (Monday's name), a phrase is read on this PC only, and nothing of it is sent, shown or kept, until a reading
holds the wake phrase (`after_wake`). Then a `wake` event (the window's "Yes?" and blip), and the words after the phrase are the
sentence's. "okay Monday" said alone lets in the next sentence that begins within WAKE_S. Push-to-talk needs no phrase.

Who reads the words after the phrase (the start op's `reader`): "google" (the default; 2026-10-15, the owner: "use Google's free one"):
every phrase, at its pause, goes to Google's free recognizer in the owner's English (en-IN); a phrase it doesn't answer is read here
(said in the final's `note`). "whisper": on this PC only. "auto": Google (it meant Parakeet until 2026-10-20 night, when that 661 MB
careful reader went: the owner, "if we are using the Google recognizer, delete them"). The wake phrase is looked for in a quick reading
(beam 1, since 2026-10-27: most phrases are the room's) and, when it isn't there, in a second one, careful and told to expect it (a
reading once heard the owner's "okay Monday" as "Look came in day"; the bare name counts there too, `second_look`, since 2026-10-27:
a half-second "Monday" was never read as the name live). Whisper base.en reads the open phrase quickly for the words on screen.
Committed words are tidied (`tidy`). Between sentences the microphone stays open and what it hears waits for the next one (`Engine.gap`,
2026-10-27: it used to close while the last phrases were still being read, and the name said then was never heard).

It runs in the voice environment's Python (E:\\monday\\voice\\env: faster-whisper, CTranslate2 int8 on the CPU), started by Monday Core
(core/stt.py `Local`) and spoken to over its pipes, one JSON object per line:
    in:  {"op": "start", "sid", "patience", "vocab", "reader", "lang", "wake"} · {"op": "finish", "sid"} (end the sentence now) ·
         {"op": "cancel", "sid"}
         (vocab: the names to expect, most important first, given to Whisper as hotwords: Monday's own, its agents', the owner's
         projects and apps; 2026-10-15, the owner's "open Notepad" was heard as "open note where")
    out: {"type": "ready", "model", "careful", "load_s"} · {"type": "start"|"speech"|"wake"|"partial"|"final"|"error"|"end", "sid", ...} ·
         {"type": "log"} · {"type": "microphone", "ok": true} (a lost microphone is back: the ear tried it again by itself, MIC_RETRY_S)
         (a partial's `committed`: every word in it was read carefully at a pause; else it ends in a quick guess, beam 1; a final's
         `reader` says who read it, `note` why this PC read a phrase Google was asked for, `woke` that it followed the wake phrase; a
         `speech` with `before_wake` is counted, never shown; a `wake` with `carried` came from "okay Monday" said alone just before)

A sentence (Endpointer, pure: the self-check drives it with made-up speech):
- the microphone (winmm; core/wake.py's structures) in 32 ms chunks; Silero VAD (faster-whisper's ONNX copy) says speech or not, with
  hysteresis (SPEECH_ON / SPEECH_OFF), and START_S of speech in a row begins a phrase (a click or a cough is shorter);
- a pause of PAUSE_S commits the phrase so far (transcribed once, carefully); while the owner speaks, the open phrase is transcribed
  every PARTIAL_EVERY_S for the words on screen;
- silence of END_S[patience] after speech ends the sentence: its final words, then the session ends (the window opens the next);
  a phrase with no pause for PHRASE_MAX_S is committed anyway, so a minute-long sentence is read in pieces, none lost;
- no audio for DEAD_S is a failed microphone (said so, never taken for silence); exact zeros for ZERO_S is a muted or blocked one.

    python -m core.hear --check                 the endpointing, offline (any Python)
    python -m core.hear --wav F [--patience P] [--vocab "Notepad, Kiro, …"] [--reader auto|google|whisper] [--wake Monday] [--fast]
                                                a WAV through the whole ear (VAD, endpointing, readers) at real speed (the voice env)
    python -m core.hear --wake-check [folder]   missed and false wakes on clips made here (Windows' voices), offline (the voice env)
    python -m core.hear                         the engine on its pipes (Core starts it)
"""
import json
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

RATE = 16000
CHUNK = 512                         # 32 ms: Silero VAD's window at 16 kHz
SPEECH_ON, SPEECH_OFF = 0.5, 0.35   # VAD probability that is speech / under which a chunk is silence again (hysteresis)
START_S = 0.16                      # speech this long in a row begins a phrase
PREROLL_S = 0.4                     # audio kept from before a phrase began: the first syllable's onset
PAUSE_S = 0.45                      # a pause this long commits the phrase so far (a natural pause, not the end)
# Silence after speech that ends the sentence (core/voice.py PATIENCE), the old pages' patience: measured 2026-10-15, a dictation's
# pauses between sentences ran 1.6-1.9 s, and 1.4 s cut it after the first. A one-step exact command ends sooner: the window asks
# ("finish") once Core says the words so far are one (Monday.early `exact`) and they held still EXACT_END_MS (core/native.py).
END_S = {"quick": 1.4, "normal": 2.4, "relaxed": 3.6}
PARTIAL_EVERY_S = 0.6               # the open phrase is read this often while the owner speaks
PHRASE_MAX_S = 24.0                 # a phrase with no pause this long is committed anyway (Whisper reads 30 s at a time)
WAIT_S = 120.0                      # a session with no speech this long ends quietly (the window opens the next)
HOLD_END_S = 60.0                   # push-to-talk (patience "hold"): the release ends the sentence (finish); a minute of silence anyway
DEAD_S = 1.5                        # no audio this long while the microphone is open: its stream failed
ZERO_S = 4.0                        # only exact zeros this long: muted, or a privacy switch blocks desktop apps
MIC_IDLE_S = 3.0                    # the microphone closes when no sentence has wanted it this long
MIC_RETRY_S = 15.0                  # after a lasting microphone failure (none, muted, blocked) the ear tries it again this often by itself
#                                     (2026-10-27; until then the window stood down and the owner had to press Ctrl+M)
GAP_MAX_S = 30.0                    # what is said between one sentence's end and the next one's start is kept this long (in memory, with
#                                     its VAD verdicts) for the next sentence. 2026-10-27, the deaf gap: the microphone closed when a
#                                     sentence was over while its phrases were still being read, 1-15 s after every room phrase, and
#                                     "Monday" said then was never heard
NBUF = 48                           # buffers queued at Windows (48 x 32 ms = 1.5 s): a busy moment loses nothing
MODEL_DIR = Path(os.environ.get("MONDAY_EAR_DIR", r"E:\monday\voice"))  # whisper-<name> folders (plain files, on E:)
# ponytail: base.en by default. small.en reads names and accents better, but needs ~1 GB more free memory than this PC has had
# (2026-10-15: commit 0.5-1.5 GB free, its load failed in MKL); "MONDAY_EAR_MODEL=small.en,base.en" tries it first, once its
# whisper-small.en folder is back on E: (unused, it went to the Recycle Bin on 2026-10-20: the owner, "remove which one is not in use").
MODELS = tuple(m.strip() for m in os.environ.get("MONDAY_EAR_MODEL", "base.en").split(",") if m.strip())
THREADS = 4                         # CTranslate2's CPU threads
BEAM_COMMIT, BEAM_PARTIAL = 5, 1
NO_SPEECH = 0.6                     # a segment Whisper itself doubts is speech, read with low confidence: noise, dropped
NAME_CLIP_S = 2.0                   # ...unless the clip is this short and the reading is the name: a half-second "Monday" (2026-10-27)
NAME_WORDS = 3                      # the second look's bare name counts when the quick look read at most this many words (a lone name,
#                                     misread: "I'm on day"; a room sentence read whole, with the name hallucinated over it: room)
GOOGLE_URL = "https://www.google.com/speech-api/v2/recognize"
def env_key(name):
    """A key from the real environment, else from the app's .env, where every key of Monday's lives (the owner, 2026-10-29: never in the
    code). ponytail: not jev.read_env_file: the ear runs in its own voice environment and imports nothing of Core's."""
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        for line in (Path(__file__).resolve().parent.parent / ".env").read_text(encoding="utf-8-sig").splitlines():
            k, sep, val = line.strip().partition("=")
            if sep and k.strip() == name:
                return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# Google's recognizer's key (MONDAY_GOOGLE_SPEECH_KEY in .env: the free key the SpeechRecognition library ships for recognize_google,
# for personal use; Google may limit or end it, then Whisper reads and the final's note says why). None in .env: Whisper reads.
GOOGLE_KEY = env_key("MONDAY_GOOGLE_SPEECH_KEY")
GOOGLE_TIMEOUT_S = 6.0              # measured 2026-10-15 from this PC: 0.8 s for a 1.5 s phrase, 1.6 s for 5 s
# (Parakeet, the 661 MB careful reader of 2026-10-20, went that night: the owner, "if we are using the Google recognizer, delete them";
#  its folder is in the Recycle Bin. Whisper stays: the live words while the owner speaks, the wake phrase on this PC, and the reading
#  when Google answers nothing.)
WAKE_S = 8.0                        # "okay Monday" said alone: the next sentence that begins this soon is let in (core/monday.py WAKE_S)
_WAKE_LEAD = r"(?:o\.?\s?k\.?|okay|okey|oh\s?kay|hey|hi|hello|yo)"
_ROOM_AFTER = r"(?:\W+(?:is|was|are|were|will|would|has|had|and|or|the|next|last|this|to|till|until|through|morning|night|afternoon|evening)\b|'s\b)"
FILLERS = re.compile(r"(?<![\w.])(?:u+m+|u+h+|e+r+m+|h+m+)\b[,.]?\s*", re.I)  # the sounds between words, not words ("mm" stays: 5 mm)
DANGLING = re.compile(r"\b(?:and|then|or|but|to|so|with|the|a|an|of|for|by|in|on|from)\W*$", re.I)  # words that say more is coming
DANGLING_S = 5.0                    # a sentence that stops on one of them waits this long for the rest (2026-10-20: "open YouTube and")
# -- the wake-check (python -m core.hear --wake-check, 2026-10-27): missed and false wakes measured on clips made here, repeatably ----
WAKE_DIR = Path(__file__).resolve().parents[1] / "data" / "test" / "ear"  # the ear's fixtures; the clips are built once into wake/
WAKE_CLIPS = (  # name; what is said (parts, WAKE_GAP_S of silence between them); the words expected after the name (None: a negative,
    #           it must not wake); whether the wake must be `named` (True), must not be (False), or either (None: one breath or two)
    ("monday", ("Monday",), "", True), ("monday-dot", ("Monday.",), "", True),
    ("monday-comma-notepad", ("Monday, open Notepad",), "open notepad", None), ("monday-chrome", ("Monday open Chrome",), "open chrome", None),
    ("hey-monday-time", ("hey Monday what time is it",), "what time is it", False), ("okay-monday", ("okay Monday",), "", False),
    ("monday-pause-notepad", ("Monday", "open Notepad"), "open notepad", True),
    ("holiday", ("Monday is a holiday",), None, None), ("mondays", ("Mondays are busy",), None, None),
    ("morning", ("see you Monday morning",), None, None), ("money", ("money",), None, None),
    ("mandy", ("Mandy, open the door",), None, None), ("sunday", ("Sunday, open Notepad",), None, None))
WAKE_ROOM = ("short", "pauses", "long", "minute")  # the fixtures as they are: speech without the name, so negatives too
WAKE_VOICES = ("Zira", "David")     # Windows' own voices (System.Speech), by first name; the installed ones are used
WAKE_LEVELS_DB = (-20.0, -35.0)     # the voice's peak, dBFS: a normal recording, and the owner's quiet laptop microphone
WAKE_NOISE_DB = -25.0               # long.wav under every other clip, this far under the voice's peak: a room with a TV on
WAKE_GAP_S = 3.0                    # the silence between a clip's parts ("Monday", then "open Notepad")


def wake_rx(name):
    """The wake phrase in a reading: "okay" / "OK" / "hey" / "hi", then the name as the readers write it ("Okay, Monday." "OK Mon-day"),
    anywhere; or the name alone opening the phrase, said to Monday ("Monday, what time is it?", "Monday open Chrome": the owner,
    2026-10-20, "when I say Monday, it should sense"; the room talking about it, "Monday is a holiday", "Monday morning…", isn't)."""
    n, bare = _name_rx(name), _name_rx(name, plural=False)
    return re.compile(rf"(?:\b{_WAKE_LEAD}\W*{n}\b|^\W*{bare}\b(?!s)(?!{_ROOM_AFTER}))\W*", re.I)


def _name_rx(name, plural=True):
    """The name as the readers write it: Monday's own spellings ("Mon-day", "Munday"), else the owner's name for it, exactly."""
    return (r"m[ou]n[\s-]?d[aeiy]{1,2}" + ("s?" if plural else "")) if (name or "").lower() == "monday" else re.escape(name or "Monday")


def after_wake(text, name):
    """The words after the wake phrase in one reading, "" for the phrase alone, None when it isn't there (whatever came before it is
    dropped: room talk, never Monday's). Said again ("Monday, Monday, hey Monday, play …", live 2026-10-20: the repeats kept the request
    from being read as a command), the name goes too."""
    m = wake_rx(name).search(text or "")
    if not m:
        return None
    rest, again = text[m.end():].strip(), re.compile(rf"^\W*(?:{_WAKE_LEAD}\W*)?{_name_rx(name)}\b\W*", re.I)
    while (m := again.match(rest)) and m.end():
        rest = rest[m.end():].strip()
    return rest


def led(text, name):
    """Is the wake phrase in this reading "okay" / "hey" / "hi" + the name, said to Monday on purpose, not the bare name? The bare name
    alone opens the ear too, but what it lets in is marked (`named`): Core asks Jev whether that is said to Monday (Monday._gesture_doubt;
    2026-10-21 it opened nothing, after the room's "Monday" let room talk in as tasks; 2026-10-22 the owner: "if im saying only monday
    hen too it cant")."""
    return bool(re.search(rf"\b{_WAKE_LEAD}\W*{_name_rx(name)}\b", text or "", re.I))


def second_look(first, second, name):
    """The wake phrase in a second reading (beam 5, told to expect the name) where the quick look (`first`) found none -> the words after
    it, or None. "okay / hey" + the name counts as it did (2026-10-21); the bare name too since 2026-10-27 (a half-second "Monday" was
    read as "Mandy", "money", or nothing: core.log 10-22 to 10-27, 3844 sessions, none ended on the name alone), but only alone, said
    once, and only when the quick look read at most NAME_WORDS words: a reader told to expect the name hears it in a name like it
    ("Mandy, open the door" came back "Monday, open the door"), over a room sentence read whole, and where it made it up it echoes
    its prompt ("money" came back "Monday, Monday, Monday"; the real name once: the wake-check, 2026-10-27). The name with a request
    in one breath is the quick look's to hear (it does, with the names to expect). What the bare name lets in this way is marked
    `named`: Core asks Jev whether what follows is said to Monday (core/voice.py wake_sure)."""
    rest = after_wake(second, name)
    if rest is None or led(second, name):
        return rest
    once = re.fullmatch(rf"\W*{_name_rx(name, plural=False)}\W*", second or "", re.I)
    return "" if once and len(re.findall(r"[A-Za-z0-9']+", first or "")) <= NAME_WORDS else None


def tidy(text):
    """Wispr Flow's kind of clean-up, the safe part: "um", "uh", "erm", "hmm" out, spaces and a dangling comma fixed."""
    t = FILLERS.sub("", text or "")
    t = re.sub(r"\s+([,.!?])", r"\1", re.sub(r"\s{2,}", " ", t)).strip(" ,")
    return t[:1].upper() + t[1:] if t[:1].islower() and (text or "")[:1].isupper() else t


class Endpointer:
    """One sentence from the VAD's verdicts. feed(chunk, p) -> actions: ("speech",), ("partial", chunks), ("commit", chunks), ("end",).
    The chunks of every commit, in order, are all the audio from a phrase's pre-roll to its end: nothing is read twice or lost."""

    def __init__(self, end_s, dt=CHUNK / RATE):
        self.end_s, self.dt = end_s, dt
        self.pre = deque(maxlen=round(PREROLL_S / dt))
        self.phrase, self.run, self.quiet, self.since, self.waited = [], 0, 0.0, 0.0, 0.0
        self.speaking = self.heard = self.over = False

    def feed(self, chunk, p):
        if self.over:
            return []
        if not self.speaking:
            self.pre.append(chunk)
            self.run = self.run + 1 if p >= SPEECH_ON else 0
            if self.run * self.dt >= START_S - 1e-9:
                self.speaking = self.heard = True
                self.phrase, self.quiet, self.since = list(self.pre), 0.0, 0.0
                self.pre.clear()
                return [("speech",)]
            if self.heard:
                self.quiet += self.dt
                return self._end() if self.quiet >= self.end_s else []
            self.waited += self.dt
            return self._end() if self.waited >= WAIT_S else []
        self.phrase.append(chunk)
        self.quiet = self.quiet + self.dt if p < SPEECH_OFF else 0.0
        self.since += self.dt
        if self.quiet >= PAUSE_S or len(self.phrase) * self.dt >= PHRASE_MAX_S:
            return [("commit", self.take())]
        if self.since >= PARTIAL_EVERY_S:
            self.since = 0.0
            return [("partial", list(self.phrase))]
        return []

    def take(self):
        """The open phrase, committed (the sentence goes on: a pause, or a phrase PHRASE_MAX_S long)."""
        out, self.phrase, self.speaking, self.run = self.phrase, [], False, 0
        return out

    def _end(self):
        self.over = True
        return [("end",)]


# -- the engine (the voice environment's Python) ---------------------------------------------------------------------------------
OUT, LOCK = sys.stdout, threading.Lock()


def emit(**ev):
    with LOCK:
        try:
            OUT.write(json.dumps(ev) + "\n")
            OUT.flush()
        except (OSError, ValueError):  # Core closed its end (it stopped or restarted): no one left to tell, so the ear ends, quietly
            os._exit(0)


def load_model():
    """The first of MODELS that loads -> (model, name, seconds); raises with every reason when none does."""
    from faster_whisper import WhisperModel
    why = []
    for name in MODELS:
        t = time.monotonic()
        try:
            return WhisperModel(str(MODEL_DIR / f"whisper-{name}"), device="cpu", compute_type="int8", cpu_threads=THREADS), name, \
                round(time.monotonic() - t, 2)
        except Exception as e:  # missing, or no memory for it (MKL's "failed to allocate"): the next one
            why.append(f"{name}: {str(e)[:160]}")
    raise RuntimeError("; ".join(why) or "no model named")


class Vad:
    """Silero VAD (faster-whisper's ONNX copy), one chunk at a time, its state carried from chunk to chunk."""

    def __init__(self):
        import numpy as np
        from faster_whisper.vad import get_vad_model
        self.np, self.run = np, get_vad_model().session.run
        self.h, self.c, self.ctx = np.zeros((1, 1, 128), np.float32), np.zeros((1, 1, 128), np.float32), np.zeros(64, np.float32)

    def __call__(self, x):
        out, self.h, self.c = self.run(None, {"input": self.np.concatenate([self.ctx, x])[None, :], "h": self.h, "c": self.c})
        self.ctx = x[-64:]
        return float(self.np.asarray(out).reshape(-1)[0])


def has_words(text):
    """Whisper writes "..." for a sound it can't read: that isn't words, and is never sent as a sentence (2026-10-15 it was)."""
    return bool(re.search(r"[A-Za-z0-9]", text or ""))


def unglue(text, vocab):
    """Whisper, given names to expect, sometimes glues one to the word before it: "OpenKiro" (2026-10-15, with every way of handing
    it the names). A word that ends in a given name, at a lower-to-upper case boundary, is split there, unless the whole word is a
    name: "WhatsApp" and "PowerPoint" stay ("App" and "Point" aren't names)."""
    names = {n.strip().lower() for n in (vocab or "").split(",") if n.strip()}

    def split(m):
        w = m.group(0)
        if w.lower() not in names:
            for i in range(1, len(w)):
                if w[i].isupper() and w[i - 1].islower() and w[i:].lower() in names:
                    return w[:i] + " " + w[i:]
        return w
    return re.sub(r"[A-Za-z]+", split, text) if names else text


def google_best(body):
    """Google's answer (one JSON object per line; the first is often {"result":[]}) -> its best reading, or "" (no words heard)."""
    for line in (body or "").splitlines():
        try:
            res = json.loads(line).get("result") or []
        except (ValueError, AttributeError):
            continue
        alts = (res[0].get("alternative") or []) if res else []
        if alts:
            return (alts[0].get("transcript") or "").strip()
    return ""


def google_read(audio, lang="en-US"):
    """One phrase (16 kHz float samples) through Google's free recognizer -> (its reading, None), "" when it heard no words, or
    (None, why) when it couldn't be asked or didn't answer."""
    if not GOOGLE_KEY:
        return None, "no MONDAY_GOOGLE_SPEECH_KEY in .env"
    import io
    import requests
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, audio, RATE, format="FLAC", subtype="PCM_16")
    try:
        r = requests.post(GOOGLE_URL, params={"output": "json", "lang": lang or "en-US", "key": GOOGLE_KEY, "pFilter": "0"},
                          data=buf.getvalue(), headers={"Content-Type": f"audio/x-flac; rate={RATE}"}, timeout=GOOGLE_TIMEOUT_S)
    except requests.RequestException as e:
        return None, f"Google's recognizer didn't answer ({type(e).__name__})"
    if r.status_code != 200:
        return None, f"Google's recognizer said HTTP {r.status_code}"
    return google_best(r.text), None


class Sentence:
    def __init__(self, sid, end_s, vocab=None, reader="whisper", lang="en-US", wake=None):
        self.sid, self.ep, self.words, self.gone, self.t0 = sid, Endpointer(end_s), [], False, time.monotonic()
        self.vocab = (vocab or "").strip() or None
        self.reader, self.lang, self.read_by, self.note = reader, lang, set(), None  # read_by: who read its phrases
        self.wake, self.awake = wake or None, not wake  # the name the wake phrase holds; heard yet (always, with no phrase asked)
        self.named = False  # opened by the bare name alone, not "okay Monday" (`led`): what it lets in is asked about (Core, Jev)
        self.end_s, self.bare = end_s, False  # its own patience; waiting for the rest ("okay Monday" alone, or words that stop on "and")
        self.n = {"speech": 0, "partial": 0, "commit": 0, "room": 0}


class Engine:
    """Sentences over the microphone: the audio thread feeds the VAD and the Endpointer; one reader thread reads (the commits in
    order, and only the newest partial)."""

    def __init__(self, model, vad, mic_factory):
        import numpy as np
        self.np, self.model, self.vad = np, model, vad
        self.cur, self.jobs, self.partial, self.cv, self.reading = None, deque(), None, threading.Condition(), False
        self.finishing = None  # a sentence over at the microphone, waiting for its reader (its final): the microphone stays open meanwhile
        self.gap = deque(maxlen=round(GAP_MAX_S * RATE / CHUNK))  # (chunk, VAD verdict) heard while no sentence is open: the next one's
        self.awake_until = 0.0  # "okay Monday" said alone: the next sentence that begins before this is let in
        self.awake_named = False  # ...and it was the bare name alone: that sentence is marked `named` as it is let in
        self.mic = mic_factory(self)
        threading.Thread(target=self._reader, name="ear-reader", daemon=True).start()

    # the owner's side (Core's commands)
    def start(self, sid, patience, vocab=None, reader="whisper", lang="en-US", wake=None):
        self.cancel(None, "another session starts (one ear)")  # (the sentence capturing now; one waiting for its reader keeps its final)
        hold = patience == "hold"  # push-to-talk is said to Monday on purpose: no phrase
        if hold:
            self.gap.clear()  # ...and begins at the key, not with what the room said before it
        s = self.cur = Sentence(sid, HOLD_END_S if hold else END_S.get(patience, END_S["normal"]), vocab,
                                reader if reader in ("google", "auto") else "whisper", lang or "en-US", None if hold else wake)
        err = self.mic.want()
        if err:
            self.cur = None
            emit(type="error", sid=sid, code=err[0], message=err[1])
            emit(type="end", sid=sid)
            return
        emit(type="start", sid=sid)
        if s.wake and time.monotonic() < self.awake_until:  # "okay Monday" alone, a moment ago: this sentence is let in
            s.awake, s.named, self.awake_until = True, self.awake_named, 0.0
            emit(type="wake", sid=sid, carried=True, **({"named": True} if s.named else {}))
        emit(type="log", text=f"ear: session open (patience {patience or 'normal'}, end after {s.ep.end_s} s of silence, "
                              f"{len(s.vocab.split(', ')) if s.vocab else 0} names expected, read by {self._who(s)}"
                              + (f", only after “{s.wake}, …” or “okay {s.wake}”)" if s.wake and not s.awake else ")"))

    def awake(self, seconds):
        """Core heard the owner's name another way (two claps, a snap: Monday.gesture_awake, 2026-10-21): the sentence open now is let in,
        else the next one that begins within `seconds` (as after "okay Monday" said alone). Until this op the ear kept its own gate, and
        a clap opened Core's while the sentence never left this process: the owner, "the clapping doesn't work"."""
        with self.cv:
            s = self.cur
            if s is not None and s.wake and not s.awake:
                s.awake, self.awake_until = True, 0.0
                emit(type="wake", sid=s.sid, carried=True)
            else:
                self.awake_until, self.awake_named = time.monotonic() + float(seconds or WAKE_S), False

    def _who(self, s):
        """Who reads this sentence's phrases: Google ("google", and "auto" since Parakeet went, 2026-10-20 night), or Whisper on this PC."""
        return "google" if s.reader in ("google", "auto") else "whisper"

    def finish(self, sid):
        """End the sentence now with the words heard so far (the open phrase committed first): the window's early end of a one-step
        exact command."""
        s = self.cur
        if not (s and s.sid == sid):
            return
        if not s.ep.heard:  # push-to-talk let go before any speech: nothing to send, the sentence just ends
            self.cancel(sid, "let go before any speech")
            return
        s.ep.over, self.cur, self.finishing = True, None, s
        with self.cv:
            if s.ep.speaking:
                self._queue_commit(s, s.ep.take())
            self._queue(("final", s))

    def cancel(self, sid, why=""):
        s = self.cur
        if s and (sid is None or s.sid == sid):
            s.gone, self.cur = True, None
            emit(type="end", sid=s.sid)
            self._summary(s, why or "cancelled")
        f = self.finishing
        if f and sid is not None and f.sid == sid:  # Core let go of a sentence waiting for its reader: its final goes nowhere
            f.gone, self.finishing = True, None
            emit(type="end", sid=f.sid)
            self._summary(f, why or "cancelled")

    # the audio thread
    def feed(self, x):
        """Every chunk the microphone gives, open sentence or not: it joins `gap`, and a sentence takes the gap in order (what was said
        while the last sentence was still being read is heard first by the next one; with no sentence open, it waits there)."""
        self.gap.append((x, self.vad(x)))
        while self.gap and self.cur is not None:
            self._feed(self.cur, *self.gap.popleft())

    def _feed(self, s, x, p):
        for act in s.ep.feed(x, p):
            k = act[0]
            if k == "speech":
                s.n["speech"] += 1
                if s.bare:  # the owner goes on after "okay Monday": their usual patience again
                    s.bare, s.ep.end_s = False, s.end_s
                emit(type="speech", sid=s.sid, **({} if s.awake else {"before_wake": True}))  # (counted by Core, not shown before the phrase)
            elif k == "partial":
                if not s.awake:  # no words on screen before the wake phrase, and none read
                    continue
                with self.cv:
                    self.partial = (s, act[1])
                    self.cv.notify()
            elif k == "commit":
                with self.cv:
                    self._queue_commit(s, act[1])
            elif k == "end":
                with self.cv:
                    if s.ep.heard:
                        self.finishing = s  # over at the microphone; its final comes when its phrases are read (the microphone stays open)
                        self._queue(("final", s))
                    else:
                        s.gone = True
                        emit(type="end", sid=s.sid)
                        self._summary(s, "no speech")
                if self.cur is s:
                    self.cur = None

    def mic_failed(self, code, message):
        s, self.cur = self.cur, None
        if s:
            s.gone = True
            emit(type="error", sid=s.sid, code=code, message=message)
            emit(type="end", sid=s.sid)
            self._summary(s, "the microphone failed")

    # the reader
    def _queue_commit(self, s, chunks):
        if self.partial and self.partial[0] is s:
            self.partial = None  # superseded by the commit
        s.n["commit"] += 1
        self._queue(("commit", s, chunks))

    def _queue(self, job):
        self.jobs.append(job)
        self.cv.notify()

    def _reader(self):
        while True:
            with self.cv:
                while not self.jobs and self.partial is None:
                    self.cv.wait()
                if self.jobs:
                    job = self.jobs.popleft()
                else:
                    job, self.partial = ("partial", *self.partial), None
                self.reading = True  # (a job under way: the microphone stays open for it, and WavMic's fast feed waits)
            try:
                self._do(job)
            except Exception as e:  # a reading that fails is said, the next sentence still heard
                s = job[1]
                if not s.gone:
                    s.gone = True
                    emit(type="error", sid=s.sid, code="network", message=f"The speech reader failed: {str(e)[:160]}")
                    emit(type="end", sid=s.sid)
            finally:
                self.reading = False

    def _do(self, job):
        k, s = job[0], job[1]
        if s.gone:
            return
        if k == "final":
            s.gone = True
            text = tidy(" ".join(w for w in s.words if w).strip())
            if has_words(text):
                emit(type="final", sid=s.sid, text=text, reader="+".join(sorted(s.read_by)) or self._who(s), note=s.note,
                     **({"woke": True} if s.wake else {}))
            if s.wake and s.awake and not has_words(text):  # "okay Monday" (or the name) alone: the next sentence is let in
                self.awake_until, self.awake_named = time.monotonic() + WAKE_S, s.named
            self._summary(s, "ended" if has_words(text) else "only the name (Jev asked about what follows)" if s.named and s.awake else
                          "only the wake phrase" if s.wake and s.awake else "nothing for me (no wake phrase)" if s.n["room"] else "no words")
            if self.finishing is s:
                self.finishing = None
            emit(type="end", sid=s.sid)
            return
        audio = self.np.concatenate(job[2])
        text = None
        if k == "commit":
            heard = None
            if not s.awake:  # the wake phrase first: this phrase is read here, and kept, shown or sent nowhere unless it holds it
                heard = self.glance(audio, s.vocab, s.wake)  # a quick look (beam 1): most phrases are the room's (2026-10-27)
                rest, second = after_wake(heard, s.wake), False
                if rest is None:  # not read as the phrase: once more, carefully, told to expect it (a reading missed "okay Monday",
                    #               2026-10-20; the name alone, 2026-10-27: second_look says what counts)
                    first, second = heard, True
                    heard = self.read(audio, BEAM_COMMIT, f"Okay {s.wake}, {s.wake}", s.wake)
                    rest = second_look(first, heard, s.wake)
                if rest is None:
                    s.n["room"] += 1
                    return
                if rest and not second:  # the name and more in one breath, at a glance: read carefully for the words (and whether
                    again = after_wake(self.careful(audio, s.vocab), s.wake)  # there are any: the room's TV after "Monday" isn't)
                    if again is not None:
                        rest = again
                # "Monday." alone opens it as "okay Monday" does, marked (Jev is asked about what follows)
                s.awake, s.named = True, not rest and not led(heard, s.wake)
                emit(type="wake", sid=s.sid, **({"named": True} if s.named else {}))
                heard = rest[:1].upper() + rest[1:]
                if not heard and not s.ep.speaking:  # "okay Monday" alone: the same sentence waits for the rest (no gap between sessions)
                    s.bare, s.ep.end_s = True, max(s.ep.end_s, WAKE_S)
            if self._who(s) == "google" and heard is None:  # (the phrase that woke is this PC's reading: room talk may open it)
                text, why = google_read(audio, s.lang)
                if text == "":  # Google heard no words in speech the VAD heard (2026-10-20: a whole sentence came back empty): this PC reads it
                    text, why = None, "Google's recognizer heard no words"
                if text is None:  # Google didn't answer: this PC reads this phrase, and the sentence says so
                    s.note = f"{why}; read on this PC instead"
                    emit(type="log", text=f"ear: {why}; the phrase is read on this PC")
                else:
                    s.read_by.add("google")
            if text is None:
                text = heard if heard is not None else self.careful(audio, s.vocab)
                s.read_by.add("whisper")
            s.words.append(tidy(text))
            if DANGLING.search(s.words[-1]) and not s.ep.speaking:  # "open YouTube and": more is coming, the sentence waits for it
                s.bare, s.ep.end_s = True, max(s.ep.end_s, DANGLING_S)
            text = ""
        else:
            text = self.read(audio, BEAM_PARTIAL, s.vocab)
            with self.cv:
                if any(j[1] is s and j[0] == "commit" for j in self.jobs):
                    return  # a commit for this sentence came in meanwhile: this partial is stale
        said = " ".join(w for w in [*s.words, text] if w).strip()
        if has_words(said) and not s.gone:
            s.n["partial"] += 1
            emit(type="partial", sid=s.sid, text=said, committed=k == "commit")  # every word read carefully (beam 5), or a quick guess

    def read(self, audio, beam, vocab=None, name=None):
        """One reading. `name`: a clip NAME_CLIP_S or shorter whose reading is the name is kept even when Whisper doubts it is speech
        (NO_SPEECH): a half-second "Monday" was dropped as noise (2026-10-27)."""
        segs, _ = self.model.transcribe(audio, language="en", beam_size=beam, vad_filter=False, condition_on_previous_text=False,
                                        without_timestamps=True, hotwords=vocab, **({} if beam > 1 else {"temperature": 0.0}))
        short = bool(name) and len(audio) <= NAME_CLIP_S * RATE
        return unglue(" ".join(g.text.strip() for g in segs if not (g.no_speech_prob > NO_SPEECH and g.avg_logprob < -1.0)
                               or (short and after_wake(g.text, name) is not None)).strip(), vocab)

    def glance(self, audio, vocab=None, name=None):
        """A quick look at a phrase before the wake (beam 1, the names to expect): most phrases are the room's, and this costs about half
        a careful reading (2026-10-27: the ear idled at ~0.6 of a core, two beam-5 readings per room phrase)."""
        return self.read(audio, BEAM_PARTIAL, vocab, name)

    def careful(self, audio, vocab=None):
        """A phrase read carefully on this PC: Whisper with beam 5 and the names to expect (Parakeet, the careful reader until 2026-10-20
        night, went: Google reads the owner's sentences, and Whisper's live words and wake phrase are all this PC needs)."""
        return self.read(audio, BEAM_COMMIT, vocab)

    def _summary(self, s, why):
        emit(type="log", text=f"ear: session over after {time.monotonic() - s.t0:.0f} s ({why}): speech {s.n['speech']}, "
                              f"phrases {s.n['commit']}" + (f" ({s.n['room']} without the wake phrase)" if s.n["room"] else "")
                              + f", partial words {s.n['partial']}")


def mic_back():
    """Does the default microphone open and give sound now (core/wake.py mic_level: a moment, in memory)? -> its peak in dBFS, or None."""
    from .wake import mic_level
    got = mic_level(0.5)
    return None if "error" in got or got["peak_dbfs"] <= -120 else got["peak_dbfs"]


class Mic:
    """The default microphone through winmm (core/wake.py's structures), open while a sentence wants it, or is over and still being read
    (2026-10-27: the next sentence's words are heard meanwhile, Engine.gap), closed MIC_IDLE_S after."""

    def __init__(self, engine):
        self.engine, self.thread, self.wanted, self.decided, self.error = engine, None, 0.0, threading.Event(), None
        self.retry = None  # the thread trying a lost microphone again (MIC_RETRY_S)

    def _lost(self):
        """A lasting refusal (no microphone, muted, blocked): tried again every MIC_RETRY_S on a thread of its own, until it opens and gives
        sound; then a `microphone` event, and Core (core/stt.py Local) and the window listen again without the owner's Ctrl+M."""
        if not (self.retry and self.retry.is_alive()):
            self.retry = threading.Thread(target=self._retry, name="ear-mic-retry", daemon=True)
            self.retry.start()

    def _retry(self):
        while True:
            time.sleep(MIC_RETRY_S)
            if self.thread and self.thread.is_alive():  # a sentence wanted it meanwhile, and it opened
                return
            peak = mic_back()
            if peak is not None:
                self.error = None
                emit(type="log", text=f"ear: the microphone is back (peak {peak} dBFS)")
                emit(type="microphone", ok=True, peak_dbfs=peak)
                return

    def want(self):
        """Open (or keep open) the microphone for a sentence -> None, or (code, words) when Windows refuses it."""
        self.wanted = time.monotonic()
        if not (self.thread and self.thread.is_alive()):
            self.decided.clear()
            self.error = None
            self.thread = threading.Thread(target=self._run, name="ear-mic", daemon=True)
            self.thread.start()
            if not self.decided.wait(3):
                return "network", "The microphone didn't answer Windows within 3 s."
        return self.error

    def _run(self):
        import ctypes
        from ctypes import wintypes
        from .wake import MM_ERRORS, WHDR_DONE, WHDR_PREPARED, _WaveFormat, _WaveHdr
        np, winmm, hwi = self.engine.np, ctypes.WinDLL("winmm"), wintypes.HANDLE()
        fmt = _WaveFormat(1, 1, RATE, RATE * 2, 2, 16, 0)
        rc = winmm.waveInOpen(ctypes.byref(hwi), ctypes.c_uint(0xFFFFFFFF), ctypes.byref(fmt), None, None, 0)
        if rc:
            self.error = ("audio-capture" if rc in (2, 6) else "network", f"The microphone didn't open: {MM_ERRORS.get(rc, f'Windows said {rc}')}.")
            self.decided.set()
            if self.error[0] == "audio-capture":
                self._lost()
            return
        bufs = [ctypes.create_string_buffer(CHUNK * 2) for _ in range(NBUF)]
        hdrs = [_WaveHdr(ctypes.cast(b, ctypes.c_void_p), CHUNK * 2, 0, None, 0, 0, None, None) for b in bufs]
        size, i = ctypes.sizeof(_WaveHdr), 0
        try:
            for h in hdrs:
                winmm.waveInPrepareHeader(hwi, ctypes.byref(h), size)
                winmm.waveInAddBuffer(hwi, ctypes.byref(h), size)
            winmm.waveInStart(hwi)
            self.decided.set()
            emit(type="log", text="ear: microphone open")
            last = zero_since = time.monotonic()
            while True:
                if self.engine.cur is not None or self.engine.finishing is not None:
                    self.wanted = time.monotonic()  # a sentence is open, or over and being read: the next one is heard meanwhile (gap)
                elif time.monotonic() - self.wanted >= MIC_IDLE_S:
                    break
                h = hdrs[i]
                if not h.dwFlags & WHDR_DONE:
                    if time.monotonic() - last > DEAD_S:
                        self.engine.mic_failed("network", "The microphone stopped sending sound (unplugged, or another program took it).")
                        return
                    time.sleep(0.004)
                    continue
                x = np.frombuffer(ctypes.string_at(bufs[i], h.dwBytesRecorded), np.int16)
                h.dwFlags = WHDR_PREPARED
                winmm.waveInAddBuffer(hwi, ctypes.byref(h), size)
                i, last = (i + 1) % NBUF, time.monotonic()
                if len(x) != CHUNK:
                    continue
                if x.any():
                    zero_since = last
                elif last - zero_since > ZERO_S and self.engine.cur is not None:
                    zero_since = last
                    self.engine.mic_failed("audio-capture", "The microphone gives only exact silence: it is muted, or Windows' microphone "
                                                            "privacy switch blocks desktop apps (Settings > Privacy & security > Microphone).")
                    self._lost()
                self.engine.feed(x.astype(np.float32) / 32768.0)
        finally:
            self.engine.gap.clear()  # nothing heard is kept while no one listens
            winmm.waveInReset(hwi)
            for h in hdrs:
                winmm.waveInUnprepareHeader(hwi, ctypes.byref(h), size)
            winmm.waveInClose(hwi)
            emit(type="log", text="ear: microphone closed")


class WavMic:
    """A WAV file as the microphone, at real speed, then silence (python -m core.hear --wav): the whole ear, tested without a voice.
    `fast` (2026-10-27, the wake-check): no waiting for the clock, only for the reader (a chunk goes in once its queue is empty: the
    Endpointer counts chunks, not seconds, so a pause still ends a sentence, and a reading still finishes before the audio after it)."""

    def __init__(self, engine, path=None, tail_s=4.0, fast=False):
        self.engine, self.path, self.tail_s, self.fast, self.thread = engine, path, tail_s, fast, None

    def want(self):
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return None

    def _run(self):
        import wave
        np = self.engine.np
        try:
            with wave.open(str(self.path)) as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
                    raise ValueError("the WAV must be 16 kHz, mono, 16-bit")
                x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
        except (OSError, ValueError, EOFError) as e:  # a source that fails ends the sentence with the reason, like a failed microphone
            self.engine.mic_failed("network", f"The audio file couldn't be read: {e}")
            return
        spoken = len(x) // CHUNK  # chunks of the file itself; the silence after it lasts until the sentence has ended, tail_s at most
        x = np.concatenate([x, np.zeros(int(self.tail_s * RATE), np.float32)])
        t0 = time.monotonic()
        for k in range(len(x) // CHUNK):
            if k >= spoken and self.engine.cur is None:
                return
            if self.fast and self.engine.cur is not None:
                while self.engine.jobs or self.engine.reading:
                    time.sleep(0.002)
            else:  # real speed (and, fast, the moments between sessions: what is said then arrives as live audio would)
                time.sleep(max(0.0, CHUNK / RATE if self.fast else t0 + (k + 1) * CHUNK / RATE - time.monotonic()))
            self.engine.feed(x[k * CHUNK:(k + 1) * CHUNK])


def serve():
    """The engine on its pipes: stdout carries only the protocol (anything a library prints goes to stderr)."""
    sys.stdout = sys.stderr
    try:
        model, name, secs = load_model()
        vad = Vad()
    except Exception as e:
        emit(type="error", sid=None, code="unavailable", message=f"The ear couldn't load Whisper: {str(e)[:300]}")
        return 1
    t = time.monotonic()
    eng = Engine(model, vad, Mic)
    emit(type="ready", model=name, careful=None, load_s=round(secs + time.monotonic() - t, 2), threads=THREADS)
    for line in sys.stdin:
        try:
            cmd = json.loads(line)
        except ValueError:
            continue
        op = cmd.get("op")
        if op == "start":
            eng.start(cmd.get("sid"), cmd.get("patience"), cmd.get("vocab"), cmd.get("reader"), cmd.get("lang"), cmd.get("wake"))
        elif op == "finish":
            eng.finish(cmd.get("sid"))
        elif op == "cancel":
            eng.cancel(cmd.get("sid"), cmd.get("why") or "")
        elif op == "awake":  # two claps or a snap heard by Core (2026-10-21): the ear's gate opens like after "okay Monday"
            eng.awake(cmd.get("seconds"))
        elif op == "quit":
            break
    return 0


def run_wav(path, patience, vocab=None, reader="whisper", wake=None, fast=False, tail_s=4.0, quiet=False, loaded=None):
    """--wav: a WAV through the real VAD, endpointing and reader, as the window drives the ear: a session per sentence, the next
    opened when one ends with audio still to come -> (the final words, every event with its time `_t`). Prints each event unless
    `quiet`; `loaded`: (model, name, seconds) from load_model(), shared by many runs."""
    global OUT
    t0 = time.monotonic()
    model, name, secs = loaded or load_model()
    if not quiet:
        print(f"model {name} loaded in {secs} s ({time.monotonic() - t0:.1f} s in all)")
    got = []

    class Tee:
        def write(self, s):
            for ln in s.splitlines():
                ev = json.loads(ln)
                ev["_t"] = time.monotonic()
                got.append(ev)
                if not quiet and (ev["type"] != "log" or "--verbose" in sys.argv):
                    tail = f" [{ev['reader']}]" if ev.get("reader") else ""
                    print(f"{ev['_t'] - t1:6.2f}s {ev['type']:8} {ev.get('text') or ev.get('message') or ''}{tail}")

        def flush(self):
            pass
    OUT = Tee()
    eng = Engine(model, Vad(), lambda e: WavMic(e, path, tail_s, fast))
    t1, n = time.monotonic(), 0
    while True:
        n += 1
        sid = f"wav{n}"
        eng.start(sid, patience, vocab, reader, wake=wake)
        while not any(e["type"] == "end" and e.get("sid") == sid for e in got):
            if not eng.mic.thread.is_alive():
                eng.finish(sid)  # the file and the silence after it are through and the sentence hasn't ended: nothing more is coming
            time.sleep(0.005)
        eng.mic.thread.join(0.1)  # (through the file, it ends at its next chunk)
        if not eng.mic.thread.is_alive():
            break
    eng.partial, eng.gap = None, deque()  # (its reader thread lives on, idle: nothing of the audio stays with it)
    if not quiet:
        print(f"done in {time.monotonic() - t0:.1f} s, {n} session{'s' if n > 1 else ''}")
    return " ".join(e["text"] for e in got if e["type"] == "final" and e.get("text")), got


def build_wake_clips(folder):
    """The wake-check's clips, made once with Windows' own voices (System.Speech through PowerShell: nothing installed, nothing
    downloaded): each phrase of WAKE_CLIPS by each voice of WAKE_VOICES installed, at each peak of WAKE_LEVELS_DB, with long.wav
    under every other one at WAKE_NOISE_DB, resampled to the ear's 16 kHz mono 16-bit -> the paths. Needs the voice environment."""
    import subprocess
    import numpy as np
    import soundfile as sf
    import soxr
    folder.mkdir(parents=True, exist_ok=True)

    def load(path):
        x, sr = sf.read(str(path), dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        return soxr.resample(x, sr, RATE) if sr != RATE else x
    jobs = [(v, part, folder / f"raw-{v.lower()}-{i}-{j}.wav") for v in WAKE_VOICES for i, c in enumerate(WAKE_CLIPS) for j, part in enumerate(c[1])]
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
          "[System.Speech.AudioFormat.AudioChannel]::Mono); "
          "$s.GetInstalledVoices() | % { Write-Output ('voice: ' + $_.VoiceInfo.Name) }; "
          "foreach ($j in @(" + ", ".join(f"@('{v}', '{t.replace(chr(39), chr(39) * 2)}', '{p}')" for v, t, p in jobs) + ")) { "
          "$v = $s.GetInstalledVoices() | ? { $_.VoiceInfo.Name -like ('*' + $j[0] + '*') } | select -first 1; if (-not $v) { continue }; "
          "$s.SelectVoice($v.VoiceInfo.Name); $s.SetOutputToWaveFile($j[2], $fmt); $s.Speak($j[1]); $s.SetOutputToNull() }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=600)
    print(r.stdout.strip() or r.stderr.strip()[:400])
    noise, paths = load(WAKE_DIR / "long.wav"), []
    gap, lead, tail = (np.zeros(int(s * RATE), np.float32) for s in (WAKE_GAP_S, 0.5, 0.3))
    for v in WAKE_VOICES:
        for i, (name, parts, _, _) in enumerate(WAKE_CLIPS):
            raws = [folder / f"raw-{v.lower()}-{i}-{j}.wav" for j in range(len(parts))]
            if not all(p.exists() for p in raws):
                continue  # this voice isn't installed
            voice = np.concatenate([lead] + [y for j, p in enumerate(raws) for y in ((gap,) if j else ()) + (load(p),)] + [tail])
            voice /= max(float(np.abs(voice).max()), 1e-6)
            for k, level in enumerate(WAKE_LEVELS_DB):
                noisy = (WAKE_VOICES.index(v) + k) % 2 == 1  # every other clip: both voices and both levels get a noisy one
                x = voice * 10 ** (level / 20)
                if noisy:
                    at = (i * 7919) % (len(noise) - len(x))  # a different stretch of the room for each phrase, the same every time
                    seg = noise[at:at + len(x)]
                    x = x + seg / max(float(np.abs(seg).max()), 1e-6) * 10 ** ((level + WAKE_NOISE_DB) / 20)
                path = folder / f"{name}-{v.lower()}{abs(level):.0f}{'n' if noisy else ''}.wav"
                sf.write(str(path), (np.clip(x, -1, 1) * 32767).astype(np.int16), RATE, subtype="PCM_16")
                paths.append(path)
            for p in raws:
                p.unlink()
    return sorted(paths)


def wake_check(folder=None, vocab=None):
    """--wake-check: every clip through run_wav (Whisper on this PC, nothing leaves it; patience quick; `fast`): a positive must give
    exactly one `wake` (carried ones aside), `named` as expected, and the words after the name in its final (in it, not alone: the room's
    TV in the pause after the name is read as more words, a known limit, and the line shows them); a negative none -> 0, or 1 when any
    positive missed or any negative woke. Prints each clip, then the 2x2 table and the gate's latency (speech to wake, the reader's
    time: the audio before the pause goes in at once)."""
    folder = Path(folder or WAKE_DIR / "wake")
    clips = sorted(folder.glob("*.wav")) if folder.is_dir() else []
    if not clips:
        clips = build_wake_clips(folder)
    specs = {c[0]: c for c in WAKE_CLIPS}
    runs = [(p, specs[p.stem.rsplit("-", 1)[0]]) for p in clips] + [(WAKE_DIR / f"{n}.wav", (n, (), None, None)) for n in WAKE_ROOM]
    vocab = vocab or (WAKE_DIR / "vocab.txt").read_text(encoding="utf-8").strip()
    loaded, t0, table = load_model(), time.monotonic(), {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
    print(f"wake-check: {len(runs)} clips (model {loaded[1]}), reader whisper, patience quick, fast")
    bad, errors = 0, 0
    for path, (name, _, expect, named) in runs:
        text, got = run_wav(path, "quick", vocab, "whisper", "Monday", fast=True, tail_s=WAKE_S + 2, quiet=True, loaded=loaded)
        wakes = [e for e in got if e["type"] == "wake" and not e.get("carried")]
        speech = next((e["_t"] for e in got if e["type"] == "speech"), None)
        lat = f"{wakes[0]['_t'] - speech:.2f} s" if wakes and speech else "-"
        ends = [m.group(1) for e in got if e["type"] == "log" for m in [re.search(r"session over after \d+ s \((.*)\): speech", e["text"])] if m]
        err = next((e["message"] for e in got if e["type"] == "error"), None)  # (the reader failed: no verdict on this clip)
        heard = re.sub(r"[^a-z]", "", text.lower())
        positive = expect is not None
        want = re.sub(r"[^a-z]", "", expect or "")
        ok = (len(wakes) == 1 and (want in heard if want else not heard) and (named is None or bool(wakes[0].get("named")) == named)) \
            if positive else not wakes
        table[(positive, bool(wakes))] += 1
        bad += not ok and not err
        errors += bool(err)
        print(f"  {'ERR ' if err else 'ok  ' if ok else 'BAD '}{path.stem:30} {'+' if positive else '-'} wake x{len(wakes)}"
              f"{' named' if wakes and wakes[0].get('named') else ''} final={text!r} ({err or '; '.join(ends) or 'no end'}) {lat}")
    print(f"\n{'':16}{'woke':>6}{'silent':>8}\n  positives     {table[(True, True)]:>6}{table[(True, False)]:>8}   <- missed wakes: "
          f"{table[(True, False)]}\n  negatives     {table[(False, True)]:>6}{table[(False, False)]:>8}   <- false wakes: {table[(False, True)]}"
          f"\n{len(runs)} clips in {time.monotonic() - t0:.0f} s: {'OK' if not bad and not errors else f'{bad} wrong'}"
          + (f", {errors} not read (the reader failed: no verdict)" if errors else ""))
    return 1 if bad or errors else 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        dt = CHUNK / RATE

        def drive(ep, plan):
            """plan: [(seconds, p)] -> (actions, every chunk id fed, chunk ids in commits)"""
            acts, fed, n = [], [], 0
            for secs, p in plan:
                for _ in range(round(secs / dt)):
                    fed.append(n)
                    acts += [a if a[0] not in ("partial", "commit") else (a[0], list(a[1])) for a in ep.feed(n, p)]
                    n += 1
            return acts, fed

        kinds = lambda acts: [a[0] for a in acts if a[0] != "partial"]  # noqa: E731
        acts, fed = drive(Endpointer(END_S["normal"]), [(0.3, 0.0), (1.5, 0.9), (3.0, 0.0)])
        assert kinds(acts) == ["speech", "commit", "end"], kinds(acts)
        assert any(a[0] == "partial" for a in acts), "words appear while the owner speaks"
        commit = next(a[1] for a in acts if a[0] == "commit")
        onset = round(0.3 / dt)  # the first chunk of speech
        assert commit == sorted(commit) and commit[0] < onset <= commit[-1], "the pre-roll keeps the onset (and a little before it)"

        for gap in (0.8, 1.9):  # a breath, and the 1.6-1.9 s between dictated sentences measured 2026-10-15: both commit, neither ends
            acts, _ = drive(Endpointer(END_S["normal"]), [(0.5, 0.0), (2.0, 0.9), (gap, 0.1), (2.0, 0.9), (3.0, 0.0)])
            assert kinds(acts) == ["speech", "commit", "speech", "commit", "end"], ("a natural pause commits, never ends", gap, kinds(acts))

        acts, _ = drive(Endpointer(END_S["normal"]), [(0.5, 0.0), (2.0, 0.9), (3.0, 0.1), (2.0, 0.9), (3.0, 0.0)])
        assert kinds(acts)[:3] == ["speech", "commit", "end"], ("a pause longer than END_S ends the sentence", kinds(acts))

        acts, _ = drive(Endpointer(END_S["relaxed"]), [(0.5, 0.0), (2.0, 0.9), (3.0, 0.1), (2.0, 0.9), (4.0, 0.0)])
        assert kinds(acts) == ["speech", "commit", "speech", "commit", "end"], ("relaxed patience waits out a 3 s pause", kinds(acts))

        for minute in (30.0, 60.0):  # one long sentence with no pause at all: committed in pieces, every chunk exactly once
            acts, fed = drive(Endpointer(END_S["normal"]), [(0.2, 0.0), (minute, 0.95), (3.0, 0.0)])
            commits = [a[1] for a in acts if a[0] == "commit"]
            assert len(commits) == int(minute // PHRASE_MAX_S) + 1 and kinds(acts)[-1] == "end", (minute, kinds(acts))
            ids = [c for cs in commits for c in cs]
            assert len(ids) == len(set(ids)), "no chunk read twice"
            speech = [i for i in fed if round(0.2 / dt) <= i < round((0.2 + minute) / dt)]
            assert set(speech) <= set(ids), "no chunk of speech lost"

        acts, _ = drive(Endpointer(END_S["normal"]), [(0.5, 0.0), (0.064, 0.95), (2.0, 0.0)])
        assert acts == [], "a click doesn't begin a sentence"
        ep = Endpointer(END_S["normal"])
        acts, _ = drive(ep, [(WAIT_S + 1, 0.0)])
        assert kinds(acts) == ["end"] and not ep.heard, "a session that hears nothing ends quietly"
        acts, _ = drive(Endpointer(END_S["normal"]), [(0.5, 0.0), (1.0, 0.9), (1.0, 0.4)])
        assert kinds(acts) == ["speech"], "between SPEECH_OFF and SPEECH_ON the phrase goes on (hysteresis)"
        assert END_S["quick"] < END_S["normal"] < END_S["relaxed"] and set(END_S) == {"quick", "normal", "relaxed"}
        V = "Monday, Kiro, Hermes, Notepad, Monday Core, Whatsapp, Powerpoint, Code"
        assert unglue("OpenKiro and Ask Hermes to check Monday Core", V) == "Open Kiro and Ask Hermes to check Monday Core"
        assert unglue("OpenNotepad", V) == "Open Notepad" and unglue("open WhatsApp and PowerPoint, not DBeaver", V) == \
            "open WhatsApp and PowerPoint, not DBeaver", "a name that is one word stays one word"
        assert unglue("OpenKiro", "") == "OpenKiro" and unglue("Kiro", V) == "Kiro", "no names given: nothing changes"
        assert not has_words("... ... ... ...") and not has_words("") and has_words("Open Notepad.") and has_words("2"), \
            "Whisper's dots for an unreadable sound are never a sentence"

        got = []  # push-to-talk on the Engine itself (no model, no microphone): held, then let go before any speech

        class Got:
            def write(self, s):
                got.extend(json.loads(ln) for ln in s.splitlines() if ln.strip())

            def flush(self):
                pass
        OUT = Got()
        eng = Engine(None, lambda x: 0.0, lambda e: type("M", (), {"want": lambda self: None})())
        eng.start("p1", "hold")
        assert eng.cur.ep.end_s == HOLD_END_S, "a held sentence isn't ended by a pause, only by letting go"
        eng.finish("p1")
        assert [e["type"] for e in got if e.get("sid") == "p1"] == ["start", "end"] and eng.cur is None, \
            ("let go before any speech: the sentence ends, nothing is sent", got)

        body = ('{"result":[]}\n{"result":[{"alternative":[{"transcript":"open Notepad","confidence":0.88},{"transcript":"open note pad"}],'
                '"final":true}],"result_index":0}\n')  # Google's answer, as it came 2026-10-15
        assert google_best(body) == "open Notepad" and google_best('{"result":[]}\n') == "" and google_best("not json") == ""
        import numpy as np  # the reader on the Engine (no model, no microphone, no network): Google's readings are the sentence
        answers = iter([("open Notepad", None), (None, "Google's recognizer said HTTP 429")])
        google_read = lambda audio, lang="en-US": next(answers)  # noqa: E731  (the module's own name: _do looks it up there)
        eng = Engine(None, lambda x: 0.0, lambda e: type("M", (), {"want": lambda self: None})())
        eng.read = lambda audio, beam, vocab=None: "and write hello" if beam == BEAM_COMMIT else "quick guess"
        got.clear()
        eng.start("g1", "normal", None, "google")
        s, chunk = eng.cur, [np.zeros(CHUNK, np.float32)]
        eng._do(("partial", s, chunk))
        eng._do(("commit", s, chunk))
        eng._do(("commit", s, chunk))
        eng._do(("final", s))
        evs = [e for e in got if e.get("sid") == "g1"]
        assert [(e["text"], e["committed"]) for e in evs if e["type"] == "partial"] == [
            ("quick guess", False), ("open Notepad", True), ("open Notepad and write hello", True)], evs
        fin = next(e for e in evs if e["type"] == "final")
        assert fin["text"] == "open Notepad and write hello" and fin["reader"] == "google+whisper" and "HTTP 429" in fin["note"], \
            ("Google reads each phrase; one it didn't answer is read by Whisper, and the sentence says so", fin)
        google_read = lambda audio, lang="en-US": ("", None)  # noqa: E731  (2026-10-20: Google heard no words in a whole sentence)
        got.clear()
        eng.start("g2", "normal", None, "google")
        s = eng.cur
        eng._do(("commit", s, chunk))
        eng._do(("final", s))
        fin = next((e for e in got if e.get("sid") == "g2" and e["type"] == "final"), {})
        assert fin.get("text") == "and write hello" and "heard no words" in (fin.get("note") or ""), ("read on this PC instead, said so", got)

        # 2026-10-20, "okay Monday" first: room talk is read on this PC and goes nowhere; the phrase wakes; what follows is the sentence
        for said, rest in (("Okay, Monday. What time is it?", "What time is it?"), ("OK Monday", ""), ("hey monday open chrome", "open chrome"),
                           ("so I told him okay Monday, open Notepad", "open Notepad"), ("Okay Mon-day play music", "play music"),
                           ("Monday is a holiday", None), ("okay Mandy", None), ("we said okay", None), ("Mondays are fun", None),
                           ("Monday, what time is it?", "what time is it?"), ("Monday.", ""), ("Monday open Chrome", "open Chrome"),
                           ("hi Monday what's the time", "what's the time"), ("Monday morning I have a call", None), ("Monday's late", None),
                           ("Monday, Monday, Monday hey Monday play Honey Singh song on YouTube", "play Honey Singh song on YouTube")):
            assert after_wake(said, "Monday") == rest, (said, after_wake(said, "Monday"))
        assert after_wake("Okay Jarvis, lights", "Jarvis") == "lights" and after_wake("okay Monday", "Jarvis") is None, "the owner's own name"
        assert tidy("um open uh Notepad") == "open Notepad" and tidy("Hmm, play the song.") == "Play the song." and tidy("5 mm") == "5 mm"
        assert DANGLING.search("open YouTube and") and DANGLING.search("Play a song by") and not DANGLING.search("open YouTube") \
            and not DANGLING.search("play Honey Singh"), "a sentence that stops on “and” isn't over"
        sent = []
        google_read = lambda audio, lang="en-US": sent.append(lang) or ("and send it", None)  # noqa: E731
        eng = Engine(None, lambda x: 0.9, lambda e: type("M", (), {"want": lambda self: None})())
        heard = iter(["we should go out tonight", "okay Monday open Notepad"])
        eng.glance = lambda audio, vocab=None, name=None: next(heard)  # the quick look before the wake (beam 1, 2026-10-27)
        eng.read = lambda audio, beam, vocab=None, name=None: "quick guess"
        got.clear()
        eng.start("w1", "normal", None, "google", "en-IN", "Monday")
        assert any("only after “Monday, …” or “okay Monday”" in e.get("text", "") for e in got if e["type"] == "log"), \
            ("the session line says what opens the ear: the name alone too (2026-10-22), in words since 2026-10-27", got)
        s = eng.cur
        for _ in range(round((START_S + PARTIAL_EVERY_S) * RATE / CHUNK) + 2):  # speech long enough for a partial: none read before the phrase
            eng.feed(np.zeros(CHUNK, np.float32))
        assert eng.partial is None and all(e.get("before_wake") for e in got if e.get("sid") == "w1" and e["type"] == "speech"), got
        eng._do(("commit", s, chunk))  # room talk
        assert not [e for e in got if e.get("sid") == "w1" and e["type"] in ("partial", "wake", "final")] and not sent and s.n["room"] == 1
        eng._do(("commit", s, chunk))  # the phrase, and a command after it, in one breath: read here
        eng._do(("commit", s, chunk))  # the next phrase goes to the reader asked for (Google here)
        eng._do(("final", s))
        evs = [e for e in got if e.get("sid") == "w1"]
        assert [e["type"] for e in evs].count("wake") == 1 and sent == ["en-IN"], ("woken once; only what followed went to Google", evs, sent)
        fin = next(e for e in evs if e["type"] == "final")
        assert fin["text"] == "Open Notepad and send it" and fin["woke"] is True, fin
        heard = iter(["Okay Monday.", "OK Monday"])
        eng.start("w4", "normal", None, "auto", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.bare and s.ep.end_s == WAKE_S, "“okay Monday” alone: the same sentence waits for the rest"
        for _ in range(round(START_S * RATE / CHUNK) + 1):  # and the owner goes on: their own patience again
            eng.feed(np.zeros(CHUNK, np.float32))
        assert not s.bare and s.ep.end_s == END_S["normal"], (s.bare, s.ep.end_s)
        eng.cancel("w4")
        got.clear()
        eng.start("w2", "normal", None, "auto", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        eng._do(("final", s))
        assert [e["type"] for e in got if e.get("sid") == "w2"] == ["start", "wake", "end"] and eng.awake_until > time.monotonic(), \
            ("“okay Monday” alone: no sentence, and the next one is let in", got)
        eng.start("w3", "normal", None, "auto", "en-IN", "Monday")
        assert eng.cur.awake and any(e.get("sid") == "w3" and e["type"] == "wake" and e.get("carried") for e in got) and not eng.awake_until
        eng.cancel("w3")
        eng.start("p2", "hold", None, "auto", "en-IN", "Monday")
        assert eng.cur.awake and eng.cur.wake is None, "push-to-talk needs no phrase"
        eng.cancel("p2")
        eng.careful = lambda audio, vocab=None: "open YouTube and"
        eng.start("d1", "normal", None, "whisper", "en-IN")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.bare and s.ep.end_s == DANGLING_S, "“open YouTube and”, then a pause: the sentence waits for the rest"
        eng.cancel("d1")
        eng.glance = lambda audio, vocab=None, name=None: "Look came in day, what time it is?"  # the first reading misreads the phrase (live, 2026-10-20)
        eng.read = lambda audio, beam, vocab=None, name=None: "Okay Monday, what time is it?" if beam == BEAM_COMMIT and "Okay Monday" in vocab else "x"
        got.clear()
        eng.start("w5", "normal", None, "google", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        eng._do(("final", s))
        fin = next((e for e in got if e.get("sid") == "w5" and e["type"] == "final"), {})
        assert fin.get("text") == "What time is it?" and fin.get("woke"), ("the second look, told to expect the phrase, wakes it; it stays on this PC", got)
        # 2026-10-22, the owner: "if im saying only monday hen too it cant": the bare name alone opens the ear as "okay Monday" does, and
        # what it lets in is marked `named`: Core asks Jev whether that is said to Monday (2026-10-21 it had opened nothing at all)
        heard = iter(["Monday."])
        eng.glance = eng.careful = lambda audio, vocab=None, name=None: next(heard)  # (careful: the words after the wake, read here)
        eng.awake_until = 0.0
        got.clear()
        eng.start("n1", "normal", None, "whisper", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.named and s.awake and s.bare and s.ep.end_s == WAKE_S, "the bare name alone: the same sentence waits for the rest"
        eng._do(("final", s))
        assert [(e["type"], e.get("named")) for e in got if e.get("sid") == "n1"] == [("start", None), ("wake", True), ("end", None)] \
            and eng.awake_until > time.monotonic() and eng.awake_named, ("no words, and the next sentence let in, marked", got)
        eng.start("n1b", "normal", None, "whisper", "en-IN", "Monday")
        assert eng.cur.awake and eng.cur.named and not eng.awake_until and any(
            e.get("sid") == "n1b" and e["type"] == "wake" and e.get("carried") and e.get("named") for e in got), ("the next one, marked", got)
        eng.cancel("n1b")
        heard = iter(["Monday.", "open Notepad"])
        got.clear()
        eng.start("n2", "normal", None, "whisper", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        eng._do(("commit", s, chunk))
        eng._do(("final", s))
        fin = next((e for e in got if e.get("sid") == "n2" and e["type"] == "final"), {})
        assert fin.get("text", "").lower() == "open notepad" and fin.get("woke") and any(
            e.get("sid") == "n2" and e["type"] == "wake" and e.get("named") for e in got), ("“Monday, … open Notepad”: let in, marked", got)
        heard = iter(["Okay Monday."])
        got.clear()
        eng.start("n4", "normal", None, "whisper", "en-IN", "Monday")
        eng._do(("commit", eng.cur, chunk))
        assert eng.cur.awake and not eng.cur.named and not any(e.get("named") for e in got), "“okay Monday”: said to Monday, no question"
        eng.cancel("n4")
        eng.glance = lambda audio, vocab=None, name=None: "so we go out tonight"
        eng.read = lambda audio, beam, vocab=None, name=None: "Monday." if beam == BEAM_COMMIT and "Okay Monday" in (vocab or "") else "x"
        eng.start("n3", "normal", None, "whisper", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.n["room"] == 1 and not s.named and not s.awake, "the second look, told to expect the name, heard it in room talk: room"
        eng.cancel("n3")
        # 2026-10-27: the second look counts the bare name too, where a reader told to expect it may really have heard it (second_look)
        assert second_look("Mandy.", "Monday.", "Monday") == "" and second_look("", "Monday", "Monday") == "" \
            and second_look("I'm on day", "Monday.", "Monday") == "", "a lone name, misread or missed by the quick look"
        assert second_look("so we go out tonight", "Monday.", "Monday") is None, "a room sentence read whole: the name over it is made up"
        assert second_look("Open my money.", "Monday, Monday, Monday", "Monday") is None and second_look("Money,", "Monday, Monday", "Monday") is None, \
            "a reader that made the name up echoes its prompt (the wake-check's “money” clips, 2026-10-27): the name once is the owner's"
        assert second_look("Mandy, open the door.", "Monday, open the door.", "Monday") is None \
            and second_look("", "Monday open Chrome", "Monday") is None, \
            "the name and more, from a reader told to expect it: room (“Mandy, open the door” came back as the name; the wake-check, 2026-10-27)"
        assert second_look("x", "Monday is a holiday", "Monday") is None and second_look("x", "Mondays are fun", "Monday") is None \
            and second_look("x", "see you Monday morning", "Monday") is None, "the room talking about Monday stays room (2026-10-21)"
        assert second_look("look came in day what time", "Okay Monday, what time is it?", "Monday") == "what time is it?", "led: as before"
        heard = iter(["Mandy."])
        eng.glance = lambda audio, vocab=None, name=None: next(heard)
        got.clear()
        eng.start("n5", "normal", None, "whisper", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.awake and s.named and s.bare and any(e.get("sid") == "n5" and e["type"] == "wake" and e.get("named") for e in got), \
            ("“Mandy.” at a glance, “Monday.” on the second look: let in, marked", got)
        eng.cancel("n5")
        heard = iter(["Mandy, open the door."])
        eng.read = lambda audio, beam, vocab=None, name=None: "Monday, open the door." if beam == BEAM_COMMIT and "Okay Monday" in (vocab or "") else "x"
        got.clear()
        eng.start("n6", "normal", None, "google", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.n["room"] == 1 and not s.awake and sent == ["en-IN"], ("a name like it, with a request: room (the wake-check, 2026-10-27)", got)
        eng.cancel("n6")
        del eng.careful  # (the real careful reading again: it goes through read, stubbed below)
        eng.glance = lambda audio, vocab=None, name=None: "Monday open note pad"
        eng.read = lambda audio, beam, vocab=None, name=None: "Monday, open Notepad." if beam == BEAM_COMMIT else "x"
        got.clear()
        eng.start("n7", "normal", None, "whisper", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        eng._do(("final", s))
        fin = next((e for e in got if e.get("sid") == "n7" and e["type"] == "final"), {})
        assert not s.named and fin.get("text") == "Open Notepad.", ("one breath: woken at a glance, the request's words read carefully", got)
        eng.glance = lambda audio, vocab=None, name=None: "Monday play the latest video"  # the room's TV right after the name, at a glance
        eng.read = lambda audio, beam, vocab=None, name=None: "Monday." if beam == BEAM_COMMIT else "x"  # ...and not on the careful reading
        got.clear()
        eng.start("n8", "normal", None, "whisper", "en-IN", "Monday")
        s = eng.cur
        eng._do(("commit", s, chunk))
        assert s.named and s.bare and any(e.get("sid") == "n8" and e["type"] == "wake" and e.get("named") for e in got), \
            ("the careful reading decides the words: the name alone, marked, waiting for the rest", got)
        eng.cancel("n8")
        import types as _t  # read(): a half-second "Monday" Whisper doubts is speech is kept; longer, or other words, dropped as before
        seg = lambda text, doubt: _t.SimpleNamespace(text=text, no_speech_prob=0.9 if doubt else 0.0, avg_logprob=-1.6 if doubt else -0.3)  # noqa: E731
        eng.model = _t.SimpleNamespace(transcribe=lambda audio, **k: ([seg(" Monday.", True)], None))
        short, longer = np.zeros(int(NAME_CLIP_S * RATE), np.float32), np.zeros(int(NAME_CLIP_S * RATE) + CHUNK, np.float32)
        assert Engine.read(eng, short, BEAM_PARTIAL, None, "Monday") == "Monday." and Engine.read(eng, short, BEAM_PARTIAL) == "" \
            and Engine.read(eng, longer, BEAM_PARTIAL, None, "Monday") == "", "the exception: a short clip, the name, asked for"
        eng.model.transcribe = lambda audio, **k: ([seg(" Thank you.", True), seg(" Monday.", False)], None)
        assert Engine.read(eng, short, BEAM_PARTIAL, None, "Monday") == "Monday.", "other doubted words still go"
        eng.model = None
        assert eng._who(eng.cur or s) in ("google", "whisper") and not hasattr(eng, "parakeet"), "Parakeet went (2026-10-20 night)"
        # two claps or a snap heard by Core (the `awake` op, 2026-10-21): a gated sentence already open is let in now; else the next one
        eng.cancel("w5")
        got.clear()
        eng.start("c1", "normal", None, "google", "en-IN", "Monday")
        assert eng.cur is not None and not eng.cur.awake, "the gate first"
        eng.awake(8.0)
        assert eng.cur.awake and any(e.get("sid") == "c1" and e["type"] == "wake" and e.get("carried") for e in got), ("let in at once", got)
        eng.cancel("c1")
        eng.awake(8.0)
        assert eng.awake_until > time.monotonic(), "no sentence open: the next one, within the window"
        got.clear()
        eng.start("c2", "normal", None, "google", "en-IN", "Monday")
        assert eng.cur.awake and not eng.awake_until and any(e.get("sid") == "c2" and e["type"] == "wake" for e in got), ("the next sentence", got)
        eng.cancel("c2")
        # 2026-10-27, the deaf gap (GAP_MAX_S): the name said while the last sentence's phrases are still being read is heard, by the next
        # sentence: the sentence over at the microphone waits in `finishing` (the microphone stays open), what is said meanwhile in `gap`
        talking, reading = [False], threading.Event()
        eng = Engine(None, lambda x: 0.9 if talking[0] else 0.0, lambda e: type("M", (), {"want": lambda self: None})())
        heard = iter(["we should go out tonight", "Monday, open Notepad"])
        eng.glance = lambda audio, vocab=None, name=None: (reading.wait(5), next(heard))[1]  # the room phrase is read as long as the test says
        eng.read = lambda audio, beam, vocab=None, name=None: "x"
        say = lambda secs: [eng.feed(np.zeros(CHUNK, np.float32)) for _ in range(round(secs * RATE / CHUNK))]  # noqa: E731
        got.clear()
        eng.start("g1", "quick", None, "whisper", "en-IN", "Monday")
        s1 = eng.cur
        talking[0] = True
        say(1.0)  # a room phrase...
        talking[0] = False
        say(END_S["quick"] + 0.1)  # ...and the silence that ends the sentence, while that phrase is still being read
        for _ in range(100):
            if eng.reading:
                break
            time.sleep(0.01)
        n0 = len(eng.gap)  # (the few chunks of silence after the end)
        assert eng.cur is None and eng.finishing is s1 and eng.reading and n0 <= 4, ("over at the microphone, waiting for its reader", n0)
        talking[0] = True
        say(1.0)  # "Monday, open Notepad", said meanwhile
        talking[0] = False
        say(END_S["quick"] + 0.1)
        assert len(eng.gap) == n0 + round(1.0 * RATE / CHUNK) + round((END_S["quick"] + 0.1) * RATE / CHUNK) and eng.finishing is s1, \
            ("kept for the next sentence", len(eng.gap))
        eng.start("g2", "quick", None, "whisper", "en-IN", "Monday")  # (Core opens it after g1's end; early here: a start never cancels
        s2 = eng.cur                                                 #  the sentence being read)
        assert eng.finishing is s1 and not s1.gone and eng.cur is s2, "a start cancels only a sentence still capturing: g1's final still comes"
        reading.set()
        say(0.1)  # the next chunks: the gap is heard first
        for _ in range(250):
            if any(e.get("sid") == "g2" and e["type"] == "end" for e in got):
                break
            time.sleep(0.02)
        evs = [(e.get("sid"), e["type"]) for e in got if e["type"] in ("wake", "final", "end")]
        assert evs == [("g1", "end"), ("g2", "wake"), ("g2", "final"), ("g2", "end")], ("g1 ends with no words; g2 heard the name", evs)
        assert next(e["text"] for e in got if e.get("sid") == "g2" and e["type"] == "final") == "Open Notepad" and s1.n["room"] == 1 \
            and len(eng.gap) <= n0 + round(0.1 * RATE / CHUNK) and eng.finishing is None and eng.cur is None, (got, len(eng.gap))
        got.clear()
        eng.start("g3", "quick", None, "whisper", "en-IN", "Monday")
        s3 = eng.cur
        talking[0] = True
        say(1.0)
        talking[0] = False
        say(END_S["quick"] + 0.1)
        assert eng.finishing is s3
        eng.cancel("g3", "the window closed")  # Core let go of it while its reader was busy: its end comes, its final never
        assert s3.gone and eng.finishing is None and any(e.get("sid") == "g3" and e["type"] == "end" for e in got)
        say(0.5)
        eng.start("p3", "hold")
        assert not eng.gap, "push-to-talk begins at the key, not with what the room said before it"
        eng.cancel("p3")
        # 2026-10-27, reconnection: a lasting refusal is tried again by the ear itself; when the microphone is back, Core hears of it
        MIC_RETRY_S, mic_back = 0.02, lambda: -31.5  # noqa: F811
        m = Mic(eng)
        got.clear()
        m.error = ("audio-capture", "no microphone")
        m._lost()
        m.retry.join(2)
        assert not m.retry.is_alive() and m.error is None and any(e["type"] == "microphone" and e.get("ok") for e in got), got
        mic_back = lambda: None  # noqa: E731, F811  (still nothing: it keeps trying, until a sentence wants it and it opens)
        m.error = ("audio-capture", "no microphone")
        m._lost()
        time.sleep(0.1)
        assert m.retry.is_alive() and m.error is not None and sum(e["type"] == "microphone" for e in got) == 1, got
        m.thread = threading.Thread(target=lambda: time.sleep(0.2), daemon=True)
        m.thread.start()
        m.retry.join(2)
        assert not m.retry.is_alive(), "a sentence opened the microphone meanwhile: the retry ends"
        import subprocess  # Core gone (its end of the pipe closed): the ear ends at its next word, with no traceback (core.log 2026-10-17)
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import core.hear as h; h.OUT.close(); "
                            "h.emit(type='ready'); print('still here')", str(Path(__file__).resolve().parents[1])],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "Traceback" not in r.stderr and "still here" not in r.stdout, (r.returncode, r.stderr[-300:])
        print("hear self-check: OK")
    elif "--wav" in sys.argv:
        f = sys.argv[sys.argv.index("--wav") + 1]
        pat = sys.argv[sys.argv.index("--patience") + 1] if "--patience" in sys.argv else "normal"
        print("final:", run_wav(f, pat, sys.argv[sys.argv.index("--vocab") + 1] if "--vocab" in sys.argv else None,
                                sys.argv[sys.argv.index("--reader") + 1] if "--reader" in sys.argv else "whisper",
                                sys.argv[sys.argv.index("--wake") + 1] if "--wake" in sys.argv else None, fast="--fast" in sys.argv)[0])
    elif "--wake-check" in sys.argv:
        sys.exit(wake_check(sys.argv[sys.argv.index("--wake-check") + 1] if len(sys.argv) > sys.argv.index("--wake-check") + 1
                            and not sys.argv[sys.argv.index("--wake-check") + 1].startswith("-") else None))
    else:
        sys.exit(serve())
