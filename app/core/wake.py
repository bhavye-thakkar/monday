"""Waking Monday with a sound (the owner, 2026-10-09: "when I clap 2 times, or whistle once, or snap my fingers, it should wake itself
up, and while waking up make it yawn, to sound dramatic").

- Asleep ("go to sleep"): Windows' online dictation is off, so nothing heard leaves the PC; this module listens instead, on the PC itself,
  for three gestures: two claps (0.15-0.8 s apart), one finger snap (a short, high, isolated crack), one whistle (a steady tone for
  WHISTLE_S). A burst of sounds (typing, a knock, a door) is none of them. The microphone is read through Windows' own wave API (winmm,
  ctypes: nothing to install), 20 ms at a time, in memory only: no sound is stored, logged or sent.
- Awake: this listener stops; Monday yawns (a sound made here, by formant synthesis, played on the PC's speakers) and the pages show it.
- Every threshold is a named constant: a real room needs tuning (python -m core.wake --listen prints what it hears, nothing kept).
"""
import ctypes
import os
import threading
import time
import wave
from collections import deque
from ctypes import wintypes
from pathlib import Path

RATE = 16000
FRAME = 320                  # 20 ms
FFT_N = 512
ABS_MIN_DB = -48.0           # dBFS: quieter than this is never a gesture
ONSET_DB = 11.0              # a transient starts this far above the room's floor (18 until 2026-10-21: the owner's claps, on this laptop's
#                              microphone at desk distance, peaked 13 dB above a -48 dB floor and lasted 340 ms, and were never heard)...
RELEASE_DB = 6.0             # ...and lasts while it stays this far above it...
DECAY_DB = 25.0              # ...and within this of its own peak: a loud clap's echo is no more clap (2026-10-22: the owner's -11 dB clap,
#                              49 dB over the floor, rang on as one 820 ms "long" sound and swallowed the clap after it)...
REATTACK_DB = 7.0            # ...and doesn't rise this much again out of its own dip or echo: that is a new sound (the second clap; the
#                              owner's came 7-8 dB softer than the first, into an echo still 16 dB under it)
TRANSIENT_MAX_S = 0.6        # longer than this is a sound, not a clap or a snap (a clap's room decay on that microphone: ~340 ms; 0.45
#                              until 2026-10-22: a clap in a room that rings, one of the owner's reference claps, lasted 460 ms)
CLAP_GAP_S = (0.15, 0.80)    # two claps, this far apart (start to start)
CLAP_MATCH_DB = 20.0         # the two claps about as loud as each other (12 until 2026-10-22: the owner's, as this microphone hears
#                              them, came 13-19 dB apart)
CLAP_CENTROID = (700.0, 2500.0)  # Hz: a clap is broadband, above a thud on the desk and below a click or an "s" (2026-10-22, on this
#                              laptop's microphone, which dulls the highs: the owner's claps 1300-2236 Hz; every false pair heard in their
#                              room had a sound at 2600-3800, keys and clicks. The reference claps they sent, recorded with every high, 1100-2600)
CLAP_VOICED = 0.5            # ...and no voice in it: a clap is noise, a syllable has a pitch (`voiced`: the reference claps 0.08-0.40).
#                              2026-10-21: with the onset at 11 dB, two syllables said alone, or two clicks, 0.15-0.8 s apart passed as
#                              claps (replayed here); that evening 17 double claps in nine minutes each opened Monday's ear for the room's
#                              next sentence, and "All right, I'll join all of you…" became a task
CLAP_HF_MAX = 0.4            # ...and not a click: a mouse button, a key or an "s" puts much of its energy above 3 kHz, a clap less
#                              (the owner's 0.17-0.20, the reference claps 0.01-0.33)
CLAP_LOUD_DB = 25.0          # ...and the louder of the two this far over the room's floor (2026-10-22: the owner's claps 36-49 dB over
#                              it; the room's quiet pairs taken for claps at 12:19 and 13:09 reached 17 and 18)
STAND_S = 2.0                # a gesture stands out of the room: nothing about as loud (STAND_DB) this long before it, nor QUIET_AFTER_S
#                              after it (typing, a conversation, three claps, a song are each one sound after another)
STAND_DB = 8.0               # only sounds this close to the gesture's loudness count: a room's murmur well under it doesn't. 2026-10-22:
#                              the owner's claps, 38-44 dB above the floor, were never taken: a TV's voices 11-31 dB above it, and
#                              Monday's own "Yes?", were always somewhere near
# ponytail: thresholds, not a model. Unvoiced pairs that stand out still pass: two coughs, two knocks, a song's claps in a quiet room. A
# learned clap profile per room is the upgrade if those bite.
SNAP_MAX_S = 0.06            # a snap is shorter than a clap...
SNAP_CENTROID = 2000.0       # ...and higher (Hz)
SNAP_HF = 0.35               # share of its energy above 3 kHz
SNAP_DB = 22.0               # and well above the floor
QUIET_AFTER_S = 0.6          # a gesture is judged once this long has passed after it (what follows it is known)...
JUDGE_S = 0.5                # ...and within this much more: later, the sounds before it may have left the record (`events` keeps 4 s)
WHISTLE_BAND = (600.0, 4000.0)
WHISTLE_TONAL = 0.50         # share of the band's energy in the peak's few bins: a whistle is one tone
WHISTLE_DB = 12.0
WHISTLE_S = 0.36
WHISTLE_GLIDE = 0.30         # the tone may glide this much from one frame to the next
COOLDOWN_S = 2.0
SILENT_S = 4.0               # a microphone that gives exact silence this long is blocked, not quiet
NBUF = 8
NAMES = {"double_clap": "you clapped twice", "snap": "you snapped your fingers", "whistle": "you whistled", "asked": "you asked", "message": "you wrote",
         "name": "you said my name"}


def _np():
    import numpy as np
    return np


def took_words(took):
    """The numbers of the sound(s) a gesture was (`Ears.took`), for Core's log: a false one explains itself, nothing of the room kept."""
    return "; ".join(f"one tone of {e['centroid']:.0f} Hz held {e['dur'] * 1000:.0f} ms, {e['above']:.0f} dB over the room, {e['pure']:.2f} pure"
                     if e.get("tone") else
                     f"{e['dur'] * 1000:.0f} ms at {e['peak']:.0f} dB ({e['above']:.0f} over the room), {e['centroid']:.0f} Hz, "
                     f"hf {e['hf']:.2f}, voiced {e['voiced']:.2f}" for e in took)


def voiced(dbs, xs):
    """How periodic a sound's loudest 60 ms are, from its frames' levels (dB) and samples: 0..1, the best normalized autocorrelation at a
    voice's pitch (80-400 Hz). A voice has a pitch; a clap or a click is noise (2026-10-21 on this laptop's microphone: the room's talk
    0.44-0.94, synthetic claps 0.11-0.28)."""
    np = _np()
    p = int(np.argmax(dbs))
    x = np.concatenate(xs[p:p + 3]).astype(np.float64)
    x -= x.mean()
    best = 0.0
    for k in range(RATE // 400, min(RATE // 80, len(x) // 2) + 1):
        a, b = x[:-k], x[k:]
        d = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
        if d > 0:
            best = max(best, float(np.dot(a, b)) / d)
    return best


class Ears:
    """The detector: fed 20 ms frames (float, -1..1), returns the gestures it is sure of. Pure, so the self-check feeds it sounds."""

    def __init__(self):
        np = _np()
        self.freqs = np.fft.rfftfreq(FFT_N, 1 / RATE)
        self.window = np.hanning(FRAME)
        self.band = (self.freqs >= WHISTLE_BAND[0]) & (self.freqs <= WHISTLE_BAND[1])
        self.floor, self.t, self.loud, self.events = None, 0.0, None, deque()  # the floor starts at the room's own level
        self.tail = None  # a sound's echo after it ended DECAY_DB under its peak: {"low", "frames"}
        self.tonal, self.tone_f, self.quiet_until, self.silent_for = 0, None, 0.0, 0.0
        self.tone_db, self.tone_pure = 0.0, 0.0  # a held tone's loudest (dB over the room) and its purity summed: Core's log gives them
        self.last = {}  # the newest frame's numbers, for --listen
        self.debug, self.told = False, set()  # --listen: print every sound and, once each, why two of them weren't a double clap
        self.took = ()  # the sound(s) the last gesture was: Core's log says their numbers (a false clap explains itself, 2026-10-22)

    def feed(self, x):
        np = _np()
        self.t += FRAME / RATE
        t = self.t
        rms = float(np.sqrt(np.mean(np.square(x))))
        self.silent_for = self.silent_for + FRAME / RATE if rms == 0.0 else 0.0
        db = 20 * np.log10(rms + 1e-9)
        spec = np.abs(np.fft.rfft(x * self.window, FFT_N)) ** 2
        total = float(spec.sum()) + 1e-12
        centroid = float((self.freqs * spec).sum() / total)
        hf = float(spec[self.freqs >= 3000].sum() / total)
        if self.floor is None:  # the first frames after the microphone opens are near-silent: the floor starts at the room's own level
            if db < ABS_MIN_DB - 30:
                return []
            self.floor = db
        above = db - self.floor
        self.last = {"db": round(db, 1), "floor": round(self.floor, 1), "centroid": round(centroid), "hf": round(hf, 2)}
        begin = {"start": t, "peak": db, "low": db, "centroid": centroid, "hf": hf, "frames": 1, "dbs": [db], "xs": [x]}
        if self.loud is None and self.tail is not None:  # a sound's own echo, once it fell DECAY_DB under its peak: no sound of its own...
            self.tail["low"], self.tail["frames"] = min(self.tail["low"], db), self.tail["frames"] + 1
            if above < RELEASE_DB:
                self.tail = None
            elif db >= self.tail["low"] + REATTACK_DB and above >= ONSET_DB:  # ...unless a new one rises out of it (the second clap)
                self.tail, self.loud = None, begin
            elif self.tail["frames"] * FRAME / RATE > TRANSIENT_MAX_S:  # (an echo that stays: the room is louder now)
                self.floor += 0.05 * (db - self.floor)
        elif self.loud is None:
            if above >= ONSET_DB and db >= ABS_MIN_DB:
                self.loud = begin
            elif db < self.floor:
                self.floor += 0.2 * (db - self.floor)   # the floor follows the room down fast...
            else:
                self.floor += 0.004 * (db - self.floor)  # ...and up slowly, so a sound doesn't become the floor
        elif (above >= RELEASE_DB and db >= self.loud["peak"] - DECAY_DB  # (rising again out of a dip REATTACK_DB deep: a new sound)
              and not (self.loud["low"] <= self.loud["peak"] - REATTACK_DB and db >= self.loud["low"] + REATTACK_DB)):
            ld = self.loud
            ld["frames"] += 1
            if db > ld["peak"]:
                ld.update(peak=db, low=db, centroid=centroid, hf=hf)
            ld["low"] = min(ld["low"], db)
            if ld["frames"] * FRAME / RATE > TRANSIENT_MAX_S:  # a long sound (talk, music, a fan): the room is louder now
                self.floor += 0.05 * (db - self.floor)
            else:  # (its frames, kept while it could still be a clap: `voiced` reads them)
                ld["dbs"].append(db)
                ld["xs"].append(x)
        else:  # over: near the floor again, DECAY_DB under its peak (its echo follows), or a new sound rising out of its dip
            ld, self.loud = self.loud, None
            rises = above >= RELEASE_DB and db >= ld["low"] + REATTACK_DB
            if rises:
                self.loud = begin
            elif above >= RELEASE_DB:
                self.tail = {"low": db, "frames": 1}
            dur = ld["frames"] * FRAME / RATE
            self.events.append({"voiced": voiced(ld.pop("dbs"), ld.pop("xs")), **ld, "dur": dur, "long": dur > TRANSIENT_MAX_S,
                                "above": ld["peak"] - self.floor})
            if self.debug:  # --listen: every sound as the detector saw it (2026-10-21: the owner's claps weren't taken for claps)
                print(f"  sound at {t:6.2f}s: {dur * 1000:3.0f} ms, peak {ld['peak']:.0f} dB ({ld['peak'] - self.floor:.0f} above the floor "
                      f"{self.floor:.0f}), centroid {ld['centroid']:.0f} Hz, hf {ld['hf']:.2f}, voiced {self.events[-1]['voiced']:.2f}"
                      f"{'  LONG' if dur > TRANSIENT_MAX_S else ''}", flush=True)
        # a whistle: one tone, held
        b = spec[self.band]
        i = int(b.argmax())
        tonal = float(b[max(0, i - 2):i + 3].sum() / (b.sum() + 1e-12))
        f = float(self.freqs[self.band][i])
        if tonal >= WHISTLE_TONAL and above >= WHISTLE_DB and (self.tone_f is None or abs(f - self.tone_f) <= WHISTLE_GLIDE * self.tone_f):
            self.tonal, self.tone_f = self.tonal + 1, f
            self.tone_db, self.tone_pure = max(self.tone_db, above), self.tone_pure + tonal
        else:
            self.tonal, self.tone_f, self.tone_db, self.tone_pure = 0, None, 0.0, 0.0
        while self.events and t - self.events[0]["start"] > 4.0:
            self.events.popleft()
        if t < self.quiet_until:
            return []
        hit = self._judge(t)
        if hit:
            self.quiet_until, self.tonal, self.tone_f, self.tone_db, self.tone_pure = t + COOLDOWN_S, 0, None, 0.0, 0.0
            self.events.clear()
            return [hit]
        return []

    def _judge(self, t):
        if self.tonal * FRAME / RATE >= WHISTLE_S:
            self.took = ({"tone": True, "dur": self.tonal * FRAME / RATE, "centroid": self.tone_f, "above": self.tone_db,  # (its numbers in
                          "pure": self.tone_pure / self.tonal},)  # Core's log too: 2026-10-22, the room passed for whistles at 625-844 Hz)
            return "whistle"
        ev = [e for e in self.events if e["above"] >= ONSET_DB - 3]  # a faint tail (a clap's decay over a floor that sank meanwhile) is no sound of its own
        going = [{"start": self.loud["start"], "peak": self.loud["peak"]}] if self.loud is not None else []  # (a sound still going: company)

        def alone(level, lo, hi, *these):  # nothing from lo to hi about as loud as `level` (STAND_DB) but these: the gesture stands out
            return not [e for e in ev + going if lo <= e["start"] <= hi and e["peak"] >= level - STAND_DB and all(e is not x for x in these)]
        clap = lambda e: (not e["long"] and CLAP_CENTROID[0] <= e["centroid"] <= CLAP_CENTROID[1] and e["voiced"] < CLAP_VOICED  # noqa: E731
                          and e["hf"] < CLAP_HF_MAX)
        for last in ev:  # each sound judged once, when what follows it is known (QUIET_AFTER_S): a quiet one after it hides nothing
            if not QUIET_AFTER_S <= t - last["start"] < QUIET_AFTER_S + JUDGE_S:
                continue
            for first in ev:
                gap = last["start"] - first["start"]
                if not CLAP_GAP_S[0] <= gap <= CLAP_GAP_S[1]:
                    continue
                level = max(first["peak"], last["peak"])  # (the louder clap: a murmur louder than a soft one can't make a third)
                stands = alone(level, first["start"] - STAND_S, last["start"] + QUIET_AFTER_S, first, last)
                loud = max(first["above"], last["above"]) >= CLAP_LOUD_DB
                if clap(first) and clap(last) and abs(first["peak"] - last["peak"]) <= CLAP_MATCH_DB and stands and loud:
                    self.took = (first, last)
                    return "double_clap"
                if self.debug and (first["start"], last["start"]) not in self.told:
                    self.told.add((first["start"], last["start"]))
                    why = ["sounds as loud near them"] * (not stands) + [f"{n} is long" for n, e in (("the first", first), ("the second", last)) if e["long"]] + \
                          [f"quiet ({max(first['above'], last['above']):.0f} dB over the room)"] * (not loud) + \
                          [f"peaks {abs(first['peak'] - last['peak']):.0f} dB apart"] * bool(abs(first["peak"] - last["peak"]) > CLAP_MATCH_DB) + \
                          [f"centroid {e['centroid']:.0f} Hz" for e in (first, last) if not CLAP_CENTROID[0] <= e["centroid"] <= CLAP_CENTROID[1]] + \
                          [f"a voice in it ({e['voiced']:.2f})" for e in (first, last) if e["voiced"] >= CLAP_VOICED] + \
                          [f"a click ({e['hf']:.2f} above 3 kHz)" for e in (first, last) if e["hf"] >= CLAP_HF_MAX]
                    print(f"  two sounds {gap:.2f}s apart, not a double clap: {'; '.join(why) or 'no reason found'}", flush=True)
            if (not last["long"] and last["dur"] <= SNAP_MAX_S and last["centroid"] >= SNAP_CENTROID and last["hf"] >= SNAP_HF
                    and last["above"] >= SNAP_DB and alone(last["peak"], last["start"] - STAND_S, last["start"] + QUIET_AFTER_S, last)):
                self.took = (last,)
                return "snap"
        return None


# -- the microphone: Windows' wave API (winmm), through ctypes -------------------------------------------------------------
class _WaveFormat(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class _WaveHdr(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD), ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_void_p), ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p)]


WHDR_DONE, WHDR_PREPARED = 0x1, 0x2
MM_ERRORS = {2: "there is no microphone", 4: "another program has the microphone", 6: "Windows has no driver for it", 32: "the format isn't supported"}


class Listener:
    """The detector on the default microphone, in its own thread. on_gesture(name) is called from that thread."""

    def __init__(self, on_gesture):
        self.on_gesture, self.thread, self.stop_ev = on_gesture, None, threading.Event()
        self.debug = False  # --listen: the detector says what it hears and why it doesn't take two sounds for a double clap
        self.error, self.running, self.heard = None, False, None
        self.level = 0.0  # the loudest sample of the last moment (0..1), smoothed: Core's status shows the listener hears the room at all

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_ev.clear()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="monday-wake", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(2)

    def _run(self):
        np = _np()
        if os.name != "nt":
            self.error = "Waking by sound needs Windows' microphone API."
            return
        winmm = ctypes.WinDLL("winmm")
        hwi = wintypes.HANDLE()
        fmt = _WaveFormat(1, 1, RATE, RATE * 2, 2, 16, 0)
        rc = winmm.waveInOpen(ctypes.byref(hwi), ctypes.c_uint(0xFFFFFFFF), ctypes.byref(fmt), None, None, 0)
        if rc:
            self.error = f"The microphone didn't open: {MM_ERRORS.get(rc, f'Windows said {rc}')}."
            return
        bufs = [ctypes.create_string_buffer(FRAME * 2) for _ in range(NBUF)]
        hdrs = [_WaveHdr(ctypes.cast(b, ctypes.c_void_p), FRAME * 2, 0, None, 0, 0, None, None) for b in bufs]
        size = ctypes.sizeof(_WaveHdr)
        ears = Ears()
        ears.debug = self.debug
        try:
            for h in hdrs:
                winmm.waveInPrepareHeader(hwi, ctypes.byref(h), size)
                winmm.waveInAddBuffer(hwi, ctypes.byref(h), size)
            winmm.waveInStart(hwi)
            self.running, i = True, 0
            while not self.stop_ev.is_set():
                h = hdrs[i]
                if not h.dwFlags & WHDR_DONE:
                    time.sleep(0.005)
                    continue
                x = np.frombuffer(ctypes.string_at(bufs[i], h.dwBytesRecorded), np.int16).astype(np.float32) / 32768.0
                h.dwFlags = WHDR_PREPARED
                winmm.waveInAddBuffer(hwi, ctypes.byref(h), size)
                i = (i + 1) % NBUF
                if len(x) == FRAME:
                    self.level = max(float(np.abs(x).max()), self.level * 0.97)
                    for g in ears.feed(x):
                        self.heard = (time.time(), g)
                        print(f"{time.strftime('%H:%M:%S')} wake: {g}, from " + took_words(ears.took), flush=True)  # (numbers only:
                        # the room was never kept)
                        self.on_gesture(g)
                    self.error = ("The microphone gives only silence: Windows' microphone privacy setting may block desktop apps."
                                  if ears.silent_for >= SILENT_S else None)
        finally:
            self.running = False
            winmm.waveInReset(hwi)
            for h in hdrs:
                winmm.waveInUnprepareHeader(hwi, ctypes.byref(h), size)
            winmm.waveInClose(hwi)


def mic_level(seconds=1.0):
    """What the default microphone gives right now: {"peak_dbfs", "rms_dbfs", "seconds"}, or {"error"}. The same winmm capture as the
    Listener, for a moment, in memory only (2026-10-13: Windows' dictation heard nothing at an input level of "TooSoft"; this says the
    number, so "it hears nothing" can be told from "it hears but can't make out the words")."""
    if os.name != "nt":
        return {"error": "Windows only."}
    np = _np()
    winmm = ctypes.WinDLL("winmm")
    hwi = wintypes.HANDLE()
    fmt = _WaveFormat(1, 1, RATE, RATE * 2, 2, 16, 0)
    rc = winmm.waveInOpen(ctypes.byref(hwi), ctypes.c_uint(0xFFFFFFFF), ctypes.byref(fmt), None, None, 0)
    if rc:
        return {"error": f"The microphone didn't open: {MM_ERRORS.get(rc, f'Windows said {rc}')}."}
    n = max(1, int(seconds * RATE / FRAME))
    bufs = [ctypes.create_string_buffer(FRAME * 2) for _ in range(n)]
    hdrs = [_WaveHdr(ctypes.cast(b, ctypes.c_void_p), FRAME * 2, 0, None, 0, 0, None, None) for b in bufs]
    size = ctypes.sizeof(_WaveHdr)
    try:
        for h in hdrs:
            winmm.waveInPrepareHeader(hwi, ctypes.byref(h), size)
            winmm.waveInAddBuffer(hwi, ctypes.byref(h), size)
        winmm.waveInStart(hwi)
        end = time.time() + seconds + 1.0
        while time.time() < end and not all(h.dwFlags & WHDR_DONE for h in hdrs):
            time.sleep(0.01)
        x = np.concatenate([np.frombuffer(ctypes.string_at(b, h.dwBytesRecorded), np.int16).astype(np.float32) / 32768.0
                            for b, h in zip(bufs, hdrs) if h.dwBytesRecorded]) if any(h.dwBytesRecorded for h in hdrs) else np.zeros(1, np.float32)
    finally:
        winmm.waveInReset(hwi)
        for h in hdrs:
            winmm.waveInUnprepareHeader(hwi, ctypes.byref(h), size)
        winmm.waveInClose(hwi)
    db = lambda v: round(float(20 * np.log10(v)) if v > 0 else -120.0, 1)  # noqa: E731
    return {"peak_dbfs": db(float(np.abs(x).max())), "rms_dbfs": db(float(np.sqrt(np.mean(x * x)))), "seconds": round(len(x) / RATE, 2)}


# -- the yawn: a sound made here (formant synthesis), not a recording -------------------------------------------------------
def yawn_wav(path, rate=22050):
    """A long, rising-then-falling "haaaaah-mmm" with a breath in front: an open vowel (/a/) closing to a hum, its pitch sweeping up and
    down, swelling and fading. Written once as a WAV, played by Windows."""
    np = _np()
    from scipy.signal import lfilter
    n = int(2.6 * rate)
    t = np.arange(n) / rate
    rng = np.random.default_rng(7)
    f0 = np.interp(t, [0, 0.35, 0.9, 1.6, 2.2, 2.6], [150, 235, 255, 185, 118, 100]) * (1 + 0.015 * np.sin(2 * np.pi * 5.2 * t))
    phase = np.cumsum(f0) / rate
    pulse = 2 * (phase % 1.0) - 1.0                      # a sawtooth: a voice's buzz
    voice = pulse * np.interp(t, [0, 0.3, 0.55, 1.5, 2.15, 2.6], [0, 0, 1.0, 0.85, 0.28, 0])
    breath = rng.standard_normal(n) * np.interp(t, [0, 0.06, 0.3, 0.5, 2.1, 2.45, 2.6], [0, 0.32, 0.22, 0.05, 0.04, 0.12, 0])
    src = voice + breath
    formants = [(np.interp(t, [0, 0.45, 1.5, 2.05, 2.6], [650, 850, 780, 380, 280]), 90, 1.0),     # F1 falls as the mouth closes
                (np.interp(t, [0, 0.45, 1.5, 2.05, 2.6], [1150, 1250, 1150, 1000, 900]), 110, 0.6),
                (np.full(n, 2600.0), 160, 0.25)]
    out = np.zeros(n)
    block = int(0.01 * rate)
    for freq, bw, gain in formants:
        r = np.exp(-np.pi * bw / rate)
        zi = np.zeros(2)
        for s in range(0, n, block):
            th = 2 * np.pi * float(freq[s]) / rate
            y, zi = lfilter([1 - r], [1, -2 * r * np.cos(th), r * r], src[s:s + block], zi=zi)
            out[s:s + block] += gain * y
    out *= 0.8 / (np.max(np.abs(out)) + 1e-9)
    pcm = (out * 32767).astype(np.int16)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def play(path):
    """Play a WAV on the PC's speakers without waiting for it. A test Core (MONDAY_SILENT=1) makes no sound."""
    if os.name == "nt" and os.environ.get("MONDAY_SILENT") != "1":
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


if __name__ == "__main__":  # python -m core.wake [--listen [seconds]] [--yawn]
    import re
    import sys
    import tempfile
    np = _np()
    rng = np.random.default_rng(1)

    def room(seconds, level=0.002):
        return rng.standard_normal(int(seconds * RATE)) * level

    def burst(seconds, level, decay, hp=False):
        x = rng.standard_normal(int(seconds * RATE)) * level * np.exp(-np.arange(int(seconds * RATE)) / (decay * RATE))
        return np.diff(x, prepend=0) if hp else x  # a difference is a high-pass: a snap's crack

    def tone(seconds, f, level):
        tt = np.arange(int(seconds * RATE)) / RATE
        return level * np.sin(2 * np.pi * f * tt * (1 + 0.02 * np.sin(2 * np.pi * 3 * tt)))

    def hear(*parts, ears=None):
        sig = np.concatenate(parts).astype(np.float32)
        e, got = ears or Ears(), []
        for s in range(0, len(sig) - FRAME + 1, FRAME):
            got += e.feed(sig[s:s + FRAME])
        return got

    whistled = Ears()
    assert hear(room(1.5), tone(0.6, 1800, 0.2), room(1.0), ears=whistled) == ["whistle"] and \
        re.fullmatch(r"one tone of \d{4} Hz held \d{3} ms, \d+ dB over the room, 0\.\d\d pure", took_words(whistled.took)), \
        ("a whistle's numbers in Core's log", whistled.took)

    from scipy.signal import lfilter
    clap = lambda: lfilter([0.3], [1, -0.7], burst(0.06, 0.9, 0.012))  # noqa: E731  (a clap is broadband but lower than a snap)
    assert hear(room(1.5), clap(), room(0.3), clap(), room(1.2)) == ["double_clap"]
    assert hear(room(1.5), clap(), room(1.5)) == [], "one clap is no gesture"
    # the owner's claps as this laptop's microphone hears them at desk distance (traced 2026-10-21): quiet (13 dB above a -48 dB floor),
    # smeared by the room to ~340 ms; they must still be two claps
    quiet_clap = lambda: lfilter([0.3], [1, -0.7], burst(0.34, 0.09, 0.08))  # noqa: E731
    assert hear(room(1.5, 0.004), quiet_clap(), room(0.3, 0.004), quiet_clap(), room(1.2, 0.004)) == ["double_clap"], "quiet, smeared claps count"
    assert hear(room(1.5, 0.004), quiet_clap(), room(1.5, 0.004)) == [], "one quiet clap is still no gesture"
    ringing = lambda: lfilter([0.3], [1, -0.7], burst(0.5, 0.3, 0.12))  # noqa: E731  (a clap in a room that rings: ~0.5 s, 2026-10-22)
    assert hear(room(1.5, 0.004), ringing(), room(0.1, 0.004), ringing(), room(1.5, 0.004)) == ["double_clap"], "claps that ring count"

    def over(x, y, at):  # y starting `at` seconds into x, both sounding
        out = np.concatenate([x, np.zeros(max(0, int(at * RATE) + len(y) - len(x)))])
        out[int(at * RATE):int(at * RATE) + len(y)] += y
        return out
    loud = lambda: lfilter([0.3], [1, -0.7], burst(1.0, 0.9, 0.15))  # noqa: E731  (a loud clap whose echo is still sounding 0.8 s on)
    assert hear(room(1.5), over(loud(), loud(), 0.3), room(1.5)) == ["double_clap"], \
        "a loud clap's echo doesn't swallow the second (2026-10-22: the owner's -11 dB clap was one 820 ms sound, and no pair)"
    assert hear(room(1.5), over(loud(), 0.4 * loud(), 0.3), room(1.5)) == ["double_clap"], "...nor a second one 8 dB softer"
    assert hear(room(1.5), loud(), room(1.5)) == [], "one loud clap, ringing, is still one clap"
    assert hear(room(1.5), burst(0.03, 0.6, 0.004, hp=True), room(1.5)) == ["snap"]
    assert hear(room(1.5), tone(0.6, 1800, 0.2), room(1.0)) == ["whistle"]
    assert hear(room(1.5), tone(0.15, 1800, 0.2), room(1.0)) == [], "a short beep is no whistle"
    typing = np.concatenate([np.concatenate([burst(0.02, 0.4, 0.003, hp=True), room(0.12)]) for _ in range(12)])
    assert hear(room(1.5), typing, room(1.5)) == [], "typing is a burst, not a gesture"
    assert hear(room(1.5), clap(), room(0.3), clap(), room(0.3), clap(), room(1.2)) == [], "three in a row is a burst, not two claps"
    speech = np.concatenate([sum(tone(0.25, f0 * k, 0.08 / k) for k in range(1, 9)) for f0 in (140, 180, 160, 120, 150)])
    assert hear(room(1.5), speech, room(1.0)) == [], "talking is no whistle"
    assert hear(room(1.5), burst(0.4, 0.4, 0.2), room(1.5)) == [], "a long noise is no clap"

    def syllable(f0, level=0.06, rise=0.04):  # a voiced syllable: harmonics of f0, rising to its vowel
        n = int(0.2 * RATE)
        env = np.minimum(1.0, np.arange(n) / (rise * RATE)) * np.minimum(1.0, (n - np.arange(n)) / (0.05 * RATE))
        return env * sum(tone(0.2, f0 * k, level / k) for k in range(1, 9))
    # 2026-10-21: these passed as double claps, and each opened Monday's ear for whatever the room said next
    assert hear(room(1.5), syllable(130), room(0.3), syllable(150), room(1.2)) == [], "two syllables said alone are talk, not claps"
    assert hear(room(1.5), syllable(180, rise=0.01), room(0.25), syllable(170, rise=0.01), room(1.2)) == [], "...quick ones too"
    click = lambda: burst(0.006, 0.12, 0.002, hp=True)  # noqa: E731  (a mouse button or a key)
    assert hear(room(1.5), click(), room(0.3), click(), room(1.2)) == [], "two clicks are no claps"
    assert hear(room(1.5), syllable(130), room(1.0), 0.2 * clap(), room(0.3), 0.2 * clap(), room(1.2)) == [], \
        "claps as loud as the talk just before them are part of the conversation"
    assert hear(room(1.5), syllable(130), room(3.0), 0.2 * clap(), room(0.3), 0.2 * clap(), room(1.2)) == ["double_clap"], "a voice long before doesn't count"
    # 2026-10-22: the owner's claps, 38-44 dB above the floor, were never taken: a TV's voices 11-31 dB above it, and Monday's own "Yes?"
    murmur = lambda: 0.3 * syllable(110)  # noqa: E731  (the room's talk, well under the claps)
    assert hear(room(1.5), syllable(130), room(1.0), clap(), room(0.3), clap(), room(1.2)) == ["double_clap"], "claps well over the talk are claps"
    assert hear(room(1.5), murmur(), room(0.2), clap(), room(0.3), clap(), room(0.1), murmur(), room(1.2)) == ["double_clap"], \
        "claps over the room's murmur, a voice starting right after them"
    assert hear(room(1.5), clap(), room(0.3), clap(), room(0.1), murmur(), room(0.1), clap(), room(1.2)) == [], "...but a third clap still makes it no pair"
    assert hear(room(2.0, 0.02), clap(), room(0.3), clap(), room(1.2)) == ["double_clap"], "a louder room: the floor is the room's own"
    with tempfile.TemporaryDirectory() as d:
        p = yawn_wav(Path(d) / "yawn.wav")
        with wave.open(str(p)) as w:
            assert w.getnframes() / w.getframerate() > 2.4 and w.getnchannels() == 1
    if "--yawn" in sys.argv:
        p = yawn_wav(Path(tempfile.gettempdir()) / "monday-yawn.wav")
        play(p)
        time.sleep(2.8)
    if "--listen" in sys.argv:
        secs = float(sys.argv[sys.argv.index("--listen") + 1]) if len(sys.argv) > sys.argv.index("--listen") + 1 else 20.0
        lis = Listener(lambda g: print(f"  heard: {g}", flush=True))
        lis.debug = True
        lis.start()
        print(f"listening {secs:.0f} s: clap twice, snap, or whistle (nothing is kept)", flush=True)
        time.sleep(secs)
        lis.stop()
        print("error:", lis.error)
    print("wake self-check: OK")
