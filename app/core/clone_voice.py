"""Monday's own voice: the owner's recording, cloned on this PC (2026-10-13). A small HTTP server that core/speech.py (Custom) runs in its own
Python (E:\\monday\\voice\\env: F5-TTS, its torch) and asks for audio; Monday Core never imports the engine.

- Engine: F5-TTS (SWivid/F5-TTS; code MIT, model weights CC-BY-NC 4.0, F5TTS_v1_Base, downloaded once from Hugging Face into HF_HOME beside the
  env), zero-shot: the reference clip is the voice. Documented and measured: a reference of 6-12 s of clean speech works best; the engine
  clips a longer one at a silence before 12 s. A 30 s recording is fine: the best 12 s are taken (see `cut`), the rest is kept untouched.
- The reference's words: `sample.txt` beside the recording when it is the whole clip (a recording of 12 s or less); otherwise the engine
  transcribes the clip once (Whisper large-v3-turbo through F5-TTS, a one-time download of ~1.6 GB, said in the status) and `ready.txt`
  keeps the result.
- Before anything is cloned the recording is inspected (`analyze`): length, rate, channels, clipping, noise floor, speech level, and the
  verdict in plain words (too short, too quiet, clipped, noisy: what to record instead). Nothing is claimed about a sample from its name.
- Endpoints (127.0.0.1 only): GET /health, POST /prepare (inspect and prepare the recording), POST /tts {"text"} -> audio/wav.
Self-check: python -m core.clone_voice --check (the analysis and the server, with a stand-in engine; no model, no network).
Serve: python -m core.clone_voice --serve --dir data/voice [--port 8768]      Try: --synth "Hello." --dir data/voice --out hello.wav
"""
import argparse
import io
import json
import math
import os
import struct
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENGINE = "F5-TTS"
MODEL = "F5TTS_v1_Base"
SAMPLE_NAMES = ("sample.wav", "sample.flac", "sample.mp3", "sample.m4a", "sample.ogg", "sample.webm")
REF_MAX_S = 12.0          # F5-TTS clips a reference longer than this; the best window of this length is taken
REF_MIN_S = 3.0           # below this a clone is not attempted (F5-TTS has nothing to copy a voice from)
REF_GOOD_S = 6.0          # from here the voice is usually captured well
NFE = int(os.environ.get("MONDAY_VOICE_NFE", "16"))  # denoising steps: 16 is ~2x faster than the default 32 on a CPU, a little rougher
FRAME_S = 0.05
CLIP_LEVEL = 0.99
CLIP_WARN = 0.002         # more than this share of samples at the ceiling: clipped
SNR_WARN_DB = 15.0
QUIET_DBFS = -35.0
MAX_TEXT = 1200


# -- the recording: read, inspect, cut ---------------------------------------------------------------------------------------------------
def read_audio(path):
    """(samples as a list of floats in -1..1, mono, rate). WAV through the standard library; anything else through soundfile/librosa, which the
    engine's environment has (the self-check needs only WAV)."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width == 2:
            vals = struct.unpack(f"<{len(raw) // 2}h", raw)
            scale = 32768.0
        elif width == 1:
            vals = [b - 128 for b in raw]
            scale = 128.0
        elif width == 4:
            vals = struct.unpack(f"<{len(raw) // 4}i", raw)
            scale = 2147483648.0
        else:
            raise ValueError(f"{path.name}: {width * 8}-bit WAV samples aren't supported (use 16-bit)")
        mono = [sum(vals[i:i + ch]) / ch / scale for i in range(0, len(vals) - ch + 1, ch)] if ch > 1 else [v / scale for v in vals]
        return mono, rate, ch
    import soundfile as sf  # the engine's environment
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return [float(sum(r) / len(r)) for r in data], rate, data.shape[1]


def write_wav(path, samples, rate):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *(int(max(-1.0, min(1.0, s)) * 32767) for s in samples)))


def dbfs(x):
    return 20 * math.log10(x) if x > 0 else -120.0


def frames_rms(samples, rate):
    n = max(1, int(rate * FRAME_S))
    return [math.sqrt(sum(v * v for v in samples[i:i + n]) / max(1, len(samples[i:i + n]))) for i in range(0, len(samples), n)]


def analyze(path):
    """What the recording really is, and whether a voice can be cloned from it. Every number is measured; the verdict names the fix."""
    samples, rate, ch = read_audio(path)
    dur = len(samples) / rate if rate else 0.0
    peak = max((abs(s) for s in samples), default=0.0)
    clipped = sum(1 for s in samples if abs(s) >= CLIP_LEVEL) / max(1, len(samples))
    rms = sorted(frames_rms(samples, rate)) if samples else [0.0]
    noise = rms[max(0, int(len(rms) * 0.05))]                    # the quietest 5 % of 50 ms frames: the room
    speech = rms[len(rms) // 2:]                                   # the louder half: the voice
    speech_db = dbfs(math.sqrt(sum(v * v for v in speech) / len(speech))) if speech else -120.0
    snr = speech_db - dbfs(noise) if noise > 0 else 60.0
    voiced = sum(1 for v in rms if dbfs(v) > dbfs(noise) + 10) * FRAME_S
    problems, notes = [], []
    if dur < REF_MIN_S:
        problems.append(f"it is {dur:.1f} s long; a voice can't be cloned from less than {REF_MIN_S:.0f} s. Record at least {max(REF_GOOD_S, 10) - dur:.0f} s more")
    elif voiced < REF_MIN_S:
        problems.append(f"only {voiced:.1f} s of it is speech; record at least {REF_GOOD_S - voiced:.0f} s more of talking")
    elif voiced < REF_GOOD_S:
        notes.append(f"{voiced:.1f} s of speech is usable but thin; {REF_GOOD_S:.0f}-{REF_MAX_S:.0f} s captures the voice better")
    if clipped > CLIP_WARN:
        problems.append(f"it is clipped ({clipped * 100:.1f} % of samples at the ceiling): record again with the input level lower")
    if speech_db < QUIET_DBFS:
        problems.append(f"it is too quiet (speech at {speech_db:.0f} dBFS): record closer to the microphone or raise its level")
    if snr < SNR_WARN_DB:
        problems.append(f"it is noisy (speech only {snr:.0f} dB above the room): record in a quieter place")
    usable = dur >= REF_MIN_S and voiced >= REF_MIN_S and clipped <= CLIP_WARN
    facts = f"{dur:.1f} s, {rate} Hz, {'mono' if ch == 1 else f'{ch} channels'}, {voiced:.1f} s of speech, peak {dbfs(peak):.1f} dBFS, {snr:.0f} dB above the room"
    summary = (("Usable" if not problems else "Usable, but") if usable else "Not usable") + f": {facts}." + \
        ("" if not problems else " " + "; ".join(problems) + ".") + ("" if not notes else " " + "; ".join(notes) + ".")
    return {"file": Path(path).name, "duration_s": round(dur, 2), "sample_rate": rate, "channels": ch, "speech_s": round(voiced, 2),
            "peak_dbfs": round(dbfs(peak), 1), "clipped_share": round(clipped, 4), "speech_dbfs": round(speech_db, 1), "noise_dbfs": round(dbfs(noise), 1),
            "snr_db": round(snr, 1), "usable": usable, "problems": problems, "summary": summary}


def cut(samples, rate, limit_s=REF_MAX_S):
    """The best window of at most `limit_s`: the one holding the most speech, ending at the quietest frame near its end (never mid-word when
    a pause is there). The whole clip when it already fits. -> (samples, start_s, end_s)."""
    if len(samples) <= limit_s * rate:
        return samples, 0.0, len(samples) / rate
    rms = frames_rms(samples, rate)
    n = int(limit_s / FRAME_S)
    best, best_i = -1.0, 0
    for i in range(0, len(rms) - n + 1):
        s = sum(rms[i:i + n])
        if s > best:
            best, best_i = s, i
    tail = rms[best_i + n - int(1.5 / FRAME_S):best_i + n]  # the last 1.5 s of the window: end at its quietest frame
    end_i = best_i + n - int(1.5 / FRAME_S) + (tail.index(min(tail)) if tail else 0)
    a, b = int(best_i * FRAME_S * rate), int(end_i * FRAME_S * rate)
    return samples[a:b], a / rate, b / rate


def prepare(folder):
    """Inspect the recording and make the reference clip (ready.wav, 24 kHz mono) beside it; the words for it from sample.txt when the clip is
    the whole recording, else left for the engine to transcribe. -> the analysis plus `ref` (path), `ref_text`, `cut`."""
    folder = Path(folder)
    sample = next((p for p in (folder / n for n in SAMPLE_NAMES) if p.exists()), None)
    if not sample:
        raise FileNotFoundError(f"no recording in {folder} (expected one of {', '.join(SAMPLE_NAMES)})")
    a = analyze(sample)
    if not a["usable"]:
        return {**a, "ref": None, "ref_text": None}
    samples, rate, _ = read_audio(sample)
    clip, start, end = cut(samples, rate)
    if rate != 24000:  # the engine resamples itself; the clip is written at the recording's own rate
        pass
    ref = folder / "ready.wav"
    write_wav(ref, clip, rate)
    whole = end - start >= len(samples) / rate - 0.01
    words = (folder / "sample.txt").read_text(encoding="utf-8").strip() if (folder / "sample.txt").exists() else ""
    ref_text = words if (words and whole) else ""
    if (folder / "ready.txt").exists() and not ref_text:
        kept = json.loads((folder / "ready.txt").read_text(encoding="utf-8"))
        if kept.get("cut") == [round(start, 2), round(end, 2)]:
            ref_text = kept.get("text") or ""
    note = ("" if ref_text else " The engine will transcribe the clip itself once (Whisper, a one-time ~1.6 GB download)"
            + (" because the recording is longer than the clip, so sample.txt can't be matched to it" if words else " (no sample.txt beside it)") + ".")
    return {**a, "ref": str(ref), "ref_text": ref_text, "cut": [round(start, 2), round(end, 2)],
            "summary": a["summary"] + f" Reference: {end - start:.1f} s ({start:.1f}-{end:.1f} s of the recording)." + note}


# -- the engine -----------------------------------------------------------------------------------------------------------------------------
_orig_load_checkpoint = None


def _lazy_checkpoint(model, ckpt_path, device="cpu", dtype=None, use_ema=True):
    """F5-TTS's load_checkpoint for a safetensors file, one tensor at a time (safe_open) straight into the model: the 1.35 GB checkpoint is
    never a second whole copy in memory (loading it whole crashed this 16 GB laptop with an access violation inside safetensors while the
    machine was near its memory limit, 2026-10-13). Mirrors the original's key handling (EMA weights, the two skipped mel keys); any other
    checkpoint format goes to the original."""
    import torch
    from safetensors import safe_open
    if not str(ckpt_path).endswith(".safetensors"):
        return _orig_load_checkpoint(model, ckpt_path, device, dtype, use_ema)
    model = model.to(dtype or torch.float32)
    own = model.state_dict()
    skip = {"initted", "step", "mel_spec.mel_stft.mel_scale.fb", "mel_spec.mel_stft.spectrogram.window"}
    missing = set(own) - skip
    with torch.no_grad(), safe_open(str(ckpt_path), framework="pt", device="cpu") as f:
        for k in f.keys():
            name = k.replace("ema_model.", "") if use_ema else k
            if name in skip or name not in own:
                continue
            own[name].copy_(f.get_tensor(k).to(own[name].dtype))
            missing.discard(name)
    missing = {m for m in missing if not m.startswith("mel_spec.")}  # the mel front-end's buffers are built, not loaded
    if missing:
        raise KeyError(f"the checkpoint lacks {sorted(missing)[:5]}")
    return model.to(device)


class Engine:
    """F5-TTS loaded once; `say(text)` -> WAV bytes. A stand-in (`fake=True`) for the self-check: a tone instead of a voice."""

    def __init__(self, folder, fake=False):
        self.folder, self.fake = Path(folder), fake
        self.tts, self.ref, self.ref_text, self.device = None, None, "", "cpu"
        self.loading, self.error, self.sample = True, None, None
        self.lock = threading.Lock()
        self.loaded_in = None

    def load(self):
        t0 = time.perf_counter()
        try:
            self.prepare()
            if not self.fake:
                if self.ref and not self.ref_text:
                    self.transcribe()  # before the voice model loads: Whisper (~1.6 GB) and F5-TTS (~1.3 GB) together ran out of memory here
                global _orig_load_checkpoint
                import torch
                from f5_tts.infer import utils_infer
                if utils_infer.load_checkpoint is not _lazy_checkpoint:
                    _orig_load_checkpoint, utils_infer.load_checkpoint = utils_infer.load_checkpoint, _lazy_checkpoint
                from f5_tts.api import F5TTS  # the engine's own environment
                # Built on one thread: torch 2.14 (CPU) initialising hundreds of layers across its OpenMP threads died with an access
                # violation on this laptop when memory ran short (2026-10-13: 80 Linear layers crashed with 8 threads, built fine with
                # one, and a real shortage then shows as a clean "not enough memory" error). Inference gets the threads back.
                threads = torch.get_num_threads()
                torch.set_num_threads(1)
                try:
                    self.tts = F5TTS(model=MODEL, device="cpu", hf_cache_dir=os.environ.get("HF_HOME"))
                finally:
                    torch.set_num_threads(max(1, min(threads, (os.cpu_count() or 2) // 2)))
                self.device = self.tts.device
            self.loaded_in = round(time.perf_counter() - t0, 1)
        except Exception as e:  # said in /health, never hidden
            self.error = f"{type(e).__name__}: {str(e)[:300] or 'out of memory'}"
        self.loading = False

    def transcribe(self):
        """The clip's words, once (Whisper through F5-TTS, a one-time download), kept in ready.txt so the next start skips this; the
        Whisper model is let go of straight after (it is as big as the voice model)."""
        import gc
        from f5_tts.infer import utils_infer
        _, self.ref_text = utils_infer.preprocess_ref_audio_text(self.ref, "", show_info=lambda *a: None)
        (self.folder / "ready.txt").write_text(json.dumps({"text": self.ref_text, "cut": self.sample.get("cut")}), encoding="utf-8")
        utils_infer.asr_pipe = None
        gc.collect()

    def prepare(self):
        self.sample = prepare(self.folder)
        self.ref, self.ref_text = self.sample.get("ref"), self.sample.get("ref_text") or ""
        if not self.ref:
            raise ValueError(self.sample["summary"])
        return self.sample

    def say(self, text):
        """WAV bytes of `text` in the owner's voice; (bytes, seconds it took)."""
        text = " ".join(str(text or "").split())[:MAX_TEXT]
        if not text:
            raise ValueError("Nothing to say.")
        if self.error:
            raise RuntimeError(self.error)
        if self.loading:
            raise RuntimeError("still loading the model")
        with self.lock:
            t0 = time.perf_counter()
            if self.fake:
                rate = 24000
                wav = [0.3 * math.sin(2 * math.pi * 440 * i / rate) for i in range(int(rate * 0.3))]
            else:
                wav, rate, _ = self.tts.infer(self.ref, self.ref_text, text, nfe_step=NFE, show_info=lambda *a: None, progress=None)
                wav = wav.tolist() if hasattr(wav, "tolist") else list(wav)
            out = io.BytesIO()
            with wave.open(out, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(rate))
                w.writeframes(struct.pack(f"<{len(wav)}h", *(int(max(-1.0, min(1.0, s)) * 32767) for s in wav)))
            return out.getvalue(), time.perf_counter() - t0

    def health(self):
        return {"engine": ENGINE, "model": MODEL, "device": self.device, "ready": bool(self.ref) and not self.loading and not self.error,
                "loading": self.loading, "error": self.error, "sample": self.sample, "loaded_in_s": self.loaded_in, "nfe": NFE,
                "detail": "loading the model" if self.loading else None, "pid": os.getpid()}


# -- the server -----------------------------------------------------------------------------------------------------------------------------
def serve(folder, port, fake=False):
    engine = Engine(folder, fake)
    threading.Thread(target=engine.load, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            if self.path == "/health":
                return self._json(200, engine.health())
            self._json(404, {"error": "no such path"})

        def do_POST(self):
            if self.path == "/prepare":
                try:
                    engine.error = None
                    s = engine.prepare()
                    if not fake and not engine.ref_text:
                        engine.transcribe()
                    return self._json(200, {**s, "ref_text": engine.ref_text})
                except Exception as e:
                    return self._json(400, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            if self.path == "/tts":
                try:
                    data, took = engine.say(self._body().get("text"))
                except Exception as e:
                    return self._json(503, {"error": str(e)[:300]})
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-TTS-Ms", str(round(took * 1000)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json(404, {"error": "no such path"})

        def log_message(self, fmt, *args):  # the text spoken never goes to the log; one line per request
            sys.stdout.write(f"{time.strftime('%H:%M:%S')} {self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")
            sys.stdout.flush()

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"{time.strftime('%H:%M:%S')} voice engine: {ENGINE} serving 127.0.0.1:{port} for {folder} (nfe {NFE})", flush=True)
    srv.serve_forever()


def check():
    """The analysis and the server with a stand-in engine: no model, no network."""
    import tempfile
    import urllib.request
    rate = 16000
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        # 20 s: 0.5 s of room noise, then "speech" (a chord with a syllable envelope) with a pause every 2 s, loud enough, not clipped
        wav = []
        for i in range(rate * 20):
            t = i / rate
            env = 0.0 if t < 0.5 or (int(t * 2) % 4 == 3) else 0.5 * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t))
            wav.append(env * (0.5 * math.sin(2 * math.pi * 180 * t) + 0.3 * math.sin(2 * math.pi * 320 * t)) + 0.004 * math.sin(2 * math.pi * 60 * t + i % 7))
        write_wav(folder / "sample.wav", wav, rate)
        a = analyze(folder / "sample.wav")
        assert a["usable"] and a["duration_s"] == 20.0 and a["channels"] == 1 and a["speech_s"] > 10 and not a["problems"], a
        p = prepare(folder)
        assert p["ref"] and Path(p["ref"]).exists() and p["cut"][1] - p["cut"][0] <= REF_MAX_S and p["ref_text"] == "" and "transcribe" in p["summary"], p
        clip, r2, _ = read_audio(p["ref"])
        assert abs(len(clip) / r2 - (p["cut"][1] - p["cut"][0])) < 0.1, "ready.wav is the cut"
        (folder / "sample.txt").write_text("the words", encoding="utf-8")
        assert prepare(folder)["ref_text"] == "", "a transcript of a 20 s recording can't be matched to a 12 s clip: the engine transcribes"
        write_wav(folder / "sample.wav", wav[: rate * 10], rate)
        assert prepare(folder)["ref_text"] == "the words", "a recording that fits whole keeps its own transcript"
        write_wav(folder / "sample.wav", wav[: rate * 2], rate)
        short = analyze(folder / "sample.wav")
        assert not short["usable"] and "Record at least" in short["summary"], short
        write_wav(folder / "sample.wav", [min(1.0, s * 6) for s in wav[: rate * 10]], rate)
        assert any("clipped" in x for x in analyze(folder / "sample.wav")["problems"]), "clipping is caught"
        write_wav(folder / "sample.wav", [s * 0.01 for s in wav[: rate * 10]], rate)
        assert any("too quiet" in x for x in analyze(folder / "sample.wav")["problems"]), "a quiet recording is caught"
        write_wav(folder / "sample.wav", wav[: rate * 10], rate)
        port = 8769
        threading.Thread(target=serve, args=(folder, port, True), daemon=True).start()
        for _ in range(50):
            try:
                h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read())
                if not h["loading"]:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert h["ready"] and h["engine"] == ENGINE and h["sample"]["usable"], h
        req = urllib.request.Request(f"http://127.0.0.1:{port}/tts", data=json.dumps({"text": "Hello."}).encode(), headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=5)
        data = r.read()
        assert r.headers["Content-Type"] == "audio/wav" and data[:4] == b"RIFF" and int(r.headers["X-TTS-Ms"]) >= 0, "a WAV, with its time"
        req = urllib.request.Request(f"http://127.0.0.1:{port}/tts", data=json.dumps({"text": ""}).encode(), headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("nothing to say must fail")
        except urllib.error.HTTPError as e:
            assert e.code == 503 and "Nothing" in json.loads(e.read())["error"]
    print("clone_voice self-check: OK")


if __name__ == "__main__":
    # Before torch is ever imported: its OpenMP pool starts with one thread (building the model across threads crashed this laptop when
    # memory ran short, 2026-10-13: an access violation in Linear.__init__ that torch.set_num_threads(1) at run time did not prevent);
    # inference raises the count again (Engine.load).
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--synth", help="say this once (to --out) and exit")
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent / "data" / "voice"))
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--out", default="out.wav")
    ap.add_argument("--fake", action="store_true", help="a stand-in engine (a tone): the server and the files without the model")
    args = ap.parse_args()
    if args.check:
        check()
    elif args.synth:
        e = Engine(args.dir, args.fake)
        e.load()
        if e.error:
            sys.exit(f"engine: {e.error}")
        print("loaded in", e.loaded_in, "s; reference:", e.ref, repr(e.ref_text[:80]))
        data, took = e.say(args.synth)
        Path(args.out).write_bytes(data)
        print(f"wrote {args.out}: {len(data)} bytes in {took:.1f} s")
    elif args.serve:
        serve(args.dir, args.port, args.fake)
    else:
        ap.print_help()
