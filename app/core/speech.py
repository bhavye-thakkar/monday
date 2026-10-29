"""Speech providers: what turns Monday's words into sound (TTS), and the owner's voice into words (STT).

Providers are discovered, never assumed: each says whether it can run here and, if not, exactly why, and lists the
voices it really has with the gender the provider itself states (a voice whose gender isn't stated is shown that way,
never guessed). The browser's own voices are the default: no key, no network. Google's are optional, and their prices
were checked on PRICES_CHECKED; they can change:
- gemini: Gemini API speech (GEMINI_API_KEY from Google AI Studio). Voices come from its Voice Library, which states
  gender, accent, pitch and style. The Flash TTS models are on the free tier (limits shown in AI Studio); Pro TTS is paid.
- google_cloud: Cloud Text-to-Speech (GOOGLE_TTS_API_KEY, else GOOGLE_API_KEY). Chirp 3: HD voices: 1M characters a
  month free, then $30 per 1M; the Cloud project needs billing enabled even for the free part. voices.list states gender.
- custom (the default since 2026-10-13): the owner's own voice, cloned on this PC from their recording by core/clone_voice.py (F5-TTS in
  its own Python environment, E:\\monday\\voice\\env; MONDAY_VOICE_PYTHON overrides), reached over 127.0.0.1:VOICE_PORT. It is the one
  voice Monday speaks with; when it is missing or fails, the reply stays on screen with the reason, and no other voice speaks in its place.
STT: Windows' own speech recognition (core/stt.py), in Monday's window.
Google Cloud Speech-to-Text is listed with the reason it isn't built instead of pretending.
Keys are read server-side (Monday/.env or the environment) and go nowhere but to their provider.
Self-check: python -m core.speech
"""
import asyncio
import base64
import io
import os
import re
import subprocess
import sys
import time
import wave
from collections import OrderedDict
from pathlib import Path

import httpx

from . import voice as voices
from .jev import setting

ROOT = Path(__file__).resolve().parent.parent
PRICES_CHECKED = "2026-09-27"
HTTP_TIMEOUT_S = 15.0      # one synthesis or one voice list
VOICE_PORT = 8768          # core/clone_voice.py, the owner's voice engine, on this computer only
VOICE_PYTHON = r"E:\monday\voice\env\python.exe"  # its own Python (F5-TTS needs 3.10-3.12 and its own torch); MONDAY_VOICE_PYTHON overrides
VOICE_START_S = 120.0      # the engine loads its model when it starts (measured 2026-10-13: see ARCHITECTURE.md › Your voice); a synthesis
#                            asked for meanwhile waits for it this long at most
VOICE_SYNTH_S = 90.0       # one synthesis on the CPU may take longer than a cloud call
VOICE_FILES = ("sample.wav", "sample.flac", "sample.mp3", "sample.m4a", "sample.ogg", "sample.webm")  # the owner's recording, as it arrived
VOICES_TTL_S = 3600.0      # provider voice lists rarely change
ERROR_TTL_S = 60.0         # a failed list (bad key, quota) is asked again after this
AUDIO_CACHE_MAX = 96       # short replies ("Done.") repeat: keep their audio
MAX_TTS_CHARS = 1200       # per request; the window speaks a sentence or two at a time
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
CLOUD_BASE = "https://texttospeech.googleapis.com/v1"
GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"   # free tier (GEMINI_TTS_MODEL in .env overrides)
# Cloud voice families, best first; Standard / Polyglot / News voices are left out (robotic or special-purpose).
CLOUD_TYPES = {"Chirp3-HD": "Chirp 3 HD", "Chirp-HD": "Chirp HD", "Studio": "Studio", "Neural2": "Neural2", "Wavenet": "WaveNet"}
TRANSLATE_TTS = "https://translate.google.{tld}/translate_tts"  # Google's free voice (GoogleFree); measured 2026-10-15: 0.6 s for a sentence
TTS_CHUNK = 190            # characters per request to it (it reads about 200)
MP3_TO_PCM = ("import av,io,sys\n"  # run by the voice environment's Python (PyAV): MP3 on stdin -> 16-bit mono 24 kHz PCM on stdout
              "src=av.open(io.BytesIO(sys.stdin.buffer.read()))\n"
              "rs=av.AudioResampler(format='s16',layout='mono',rate=24000)\n"
              "for f in [*src.decode(audio=0),None]:\n"
              "    for g in rs.resample(f):\n"
              "        sys.stdout.buffer.write(bytes(g.planes[0])[:g.samples*2])\n")


class SpeechError(Exception):
    pass


def gender_of(g):
    """A provider's own gender label -> 'female' / 'male' / 'neutral' / None (not stated)."""
    g = (g or "").strip().lower()
    return g if g in ("female", "male", "neutral") else None


def _pick(d, *names):
    """The first present field, whichever case the API used (REST camelCase or snake_case)."""
    for n in names:
        v = d.get(n)
        if v not in (None, "", [], {}):
            return v
    return None


def wav_from_pcm(pcm, rate=24000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return b.getvalue()


def http_error(provider, r):
    """An honest sentence for a failed provider call (never includes the key)."""
    try:
        body = r.json()
    except ValueError:
        body = None
    if isinstance(body, list):  # the Interactions endpoint wraps its error in a list
        body = body[0] if body else None
    err = (body.get("error") if isinstance(body, dict) else None) or {}
    msg = str(err.get("message") or "")[:220] if isinstance(err, dict) else str(err)[:220]
    blob = f"{msg} {err.get('status', '') if isinstance(err, dict) else ''} {r.text[:600]}"
    if r.status_code in (400, 401) and re.search(r"api key not valid|invalid api key|API_KEY_INVALID", blob, re.I):
        return f"{provider} rejected the key (not a valid API key)."
    if re.search(r"BILLING_DISABLED|billing", blob, re.I) and r.status_code in (403, 400):
        return f"{provider}: billing isn't enabled on this key's Google Cloud project (required even for the free allowance)."
    if re.search(r"SERVICE_DISABLED|has not been used in project|is disabled", blob, re.I):
        return f"{provider}: the API isn't enabled for this key's project. Enable it in the Google Cloud console."
    if r.status_code == 429:
        return f"{provider}: rate limit or quota reached (429). {msg}".strip()
    if r.status_code == 403:
        return f"{provider}: access denied (403). {msg}".strip()
    return f"{provider} error {r.status_code}. {msg}".strip()


def order(vs, lang):
    """Female voices first in the owner's language (exact region, then same language), best quality, then name; numbered."""
    lang = (lang or "en").lower()
    base = lang.split("-")[0]
    vs = sorted(vs, key=lambda v: (not (v["lang"] or "").lower() == lang, not (v["lang"] or "").lower().startswith(base),
                                   v.get("rank", 9), v["name"].lower()))
    for i, v in enumerate(vs, 1):
        v["number"] = i
    return vs


def split(vs, lang):
    """(female, others): female ones numbered for "use voice 2"; others keep the gender their provider states."""
    return order([v for v in vs if v["gender"] == "female"], lang), sorted(
        [v for v in vs if v["gender"] != "female"], key=lambda v: ((v["gender"] or "zz"), v["name"].lower()))


# -- providers -------------------------------------------------------------------------------------------------------
class Browser:
    id, label, keys = "browser", "This computer's voices (Windows)", ()
    pricing = "Free: the voices installed in Windows, reported by Monday's window (core/qtwindow.py)."
    speaks_on_server = False

    def key(self):
        return "browser", None


class Gemini:
    id, label, keys = "gemini", "Gemini voices (Google AI Studio)", ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    pricing = f"Flash TTS models are on the Gemini free tier (limits shown in AI Studio); paid above it. Checked {PRICES_CHECKED}."
    speaks_on_server = True

    def key(self):
        return next(((setting(k), k) for k in self.keys if setting(k)), (None, None))

    @staticmethod
    def model():
        return setting("GEMINI_TTS_MODEL") or GEMINI_TTS_MODEL

    @staticmethod
    def parse_voices(data):
        out = []
        for v in (data or {}).get("voices") or []:
            vid = _pick(v, "id", "name", "voiceId", "voice_id")
            if not vid:
                continue
            style = _pick(v, "persona", "description")
            style = ", ".join(style) if isinstance(style, list) else style
            lang = _pick(v, "languageCode", "language_code", "languageCodes", "language_codes") or ""
            lang = lang[0] if isinstance(lang, list) else lang
            name = _pick(v, "displayName", "display_name") or vid
            out.append({"provider": "gemini", "id": vid, "name": name, "lang": lang, "gender": gender_of(_pick(v, "gender")),
                        "style": style, "accent": _pick(v, "accent"), "pitch": _pick(v, "pitch"),
                        "description": _pick(v, "description"), "natural": True, "offline": False, "rank": 0,
                        "label": " · ".join(x for x in ("Gemini", (style or "").split(",")[0].strip() or None, lang or None) if x)})
        return out, _pick(data or {}, "nextPageToken", "next_page_token")

    async def list(self, client, key):
        vs, token = [], None
        for _ in range(5):  # pages of up to 1000
            params = [("type", "prebuilt"), ("page_size", "1000")] + ([("page_token", token)] if token else [])
            r = await client.get(f"{GEMINI_BASE}/voices", params=params, headers={"x-goog-api-key": key})
            if r.status_code != 200:
                raise SpeechError(http_error("Gemini", r))
            page, token = self.parse_voices(r.json())
            vs += page
            if not token:
                break
        return vs

    @staticmethod
    def parse_audio(data):
        """Interactions API response -> (bytes, mime). Raw PCM (audio/l16) is wrapped as WAV so any <audio> plays it."""
        parts = [c for s in (data or {}).get("steps") or [] if s.get("type") == "model_output"
                 for c in s.get("content") or [] if c.get("type") == "audio" and c.get("data")]
        if not parts:
            raise SpeechError("Gemini answered without audio.")
        a = parts[-1]
        raw = base64.b64decode(a["data"])
        mime = (_pick(a, "mime_type", "mimeType") or "audio/wav").lower()
        if mime.startswith(("audio/l16", "audio/pcm")):
            rate = re.search(r"rate=(\d+)", mime)
            return wav_from_pcm(raw, int(rate.group(1)) if rate else 24000), "audio/wav"
        return raw, mime.split(";")[0]

    async def synthesize(self, client, key, text, voice_id, lang):
        body = {"model": self.model(), "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
                "response_format": {"type": "audio"}, "generation_config": {"speech_config": [{"voice": voice_id}]}}
        r = await client.post(f"{GEMINI_BASE}/interactions", json=body, headers={"x-goog-api-key": key})
        if r.status_code != 200:
            raise SpeechError(http_error("Gemini", r))
        return self.parse_audio(r.json())


class GoogleCloud:
    id, label, keys = "google_cloud", "Google Cloud Text-to-Speech", ("GOOGLE_TTS_API_KEY", "GOOGLE_API_KEY")
    pricing = (f"Chirp 3: HD voices: 1M characters a month free, then $30 per 1M; billing must be enabled on the Cloud "
               f"project. Checked {PRICES_CHECKED}.")
    speaks_on_server = True

    def key(self):
        return next(((setting(k), k) for k in self.keys if setting(k)), (None, None))

    @staticmethod
    def parse_voices(data):
        out = []
        for v in (data or {}).get("voices") or []:
            name = v.get("name") or ""
            kind = next((k for k in CLOUD_TYPES if f"-{k}-" in name), None)
            if not kind:
                continue
            lang = (v.get("languageCodes") or [""])[0]
            short = name.rsplit("-", 1)[-1]  # "en-US-Chirp3-HD-Aoede" -> "Aoede", "en-US-Neural2-F" -> "F"
            out.append({"provider": "google_cloud", "id": name, "name": f"{short} ({CLOUD_TYPES[kind]}, {lang})" if len(short) <= 2 else f"{short} ({lang})",
                        "lang": lang, "gender": gender_of(v.get("ssmlGender")), "style": CLOUD_TYPES[kind],
                        "natural": True, "offline": False, "rank": list(CLOUD_TYPES).index(kind),
                        "label": f"{CLOUD_TYPES[kind]} · {lang}"})
        return out

    async def list(self, client, key):
        r = await client.get(f"{CLOUD_BASE}/voices", params={"key": key})
        if r.status_code != 200:
            raise SpeechError(http_error("Google Cloud Text-to-Speech", r))
        return self.parse_voices(r.json())

    async def synthesize(self, client, key, text, voice_id, lang):
        body = {"input": {"text": text}, "voice": {"languageCode": lang or "-".join(voice_id.split("-")[:2]), "name": voice_id},
                "audioConfig": {"audioEncoding": "LINEAR16"}}  # WAV, with its header: Monday's window plays WAV (core/native.py play)
        r = await client.post(f"{CLOUD_BASE}/text:synthesize", params={"key": key}, json=body)
        if r.status_code != 200:
            raise SpeechError(http_error("Google Cloud Text-to-Speech", r))
        content = (r.json() or {}).get("audioContent")
        if not content:
            raise SpeechError("Google Cloud Text-to-Speech answered without audio.")
        return base64.b64decode(content), "audio/wav"


FISH_BASE = "https://api.fish.audio"
FISH_CLONE_S = 120.0  # one voice model made from a recording (fast training is seconds to a minute)


class FishAudio:
    """Fish Audio (the owner, 2026-10-21: "I'll give the 30 sec voice pack and it'll clone it and make it Monday's voice… we will use Fish
    Audio for that"): a voice model made from the recording (POST /model, fast training), then every reply through its TTS with that
    model as the reference. The key FISH_AUDIO_API_KEY lives in Monday\\.env (Integrations); nothing is claimed until Fish answered."""
    id, label, keys = "fish", "Fish Audio (your voice, cloned in its cloud)", ("FISH_AUDIO_API_KEY",)
    pricing = f"Fish Audio bills per character of speech and keeps the voice model in the owner's account (its site has the rates; checked {PRICES_CHECKED})."
    speaks_on_server = True

    def key(self):
        return next(((setting(k), k) for k in self.keys if setting(k)), (None, None))

    @staticmethod
    def auth(key):
        return {"Authorization": f"Bearer {key}"}

    @staticmethod
    def parse_models(data):
        """GET /model?self=true -> the owner's own voice models as voices (newest first as Fish lists them)."""
        out = []
        for m in (data or {}).get("items") or []:
            mid = m.get("_id") or m.get("id")
            if not mid or (m.get("type") or "tts") != "tts":
                continue
            lang = (m.get("languages") or [""])[0]
            out.append({"provider": "fish", "id": mid, "name": m.get("title") or mid, "lang": lang, "gender": None, "style": "cloned",
                        "natural": True, "offline": False, "rank": 0, "label": f"cloned · Fish Audio · {lang or 'any language'}"})
        return out

    async def list(self, client, key):
        r = await client.get(f"{FISH_BASE}/model", params={"self": "true", "page_size": 50}, headers=self.auth(key))
        if r.status_code != 200:
            raise SpeechError(http_error("Fish Audio", r))
        return self.parse_models(r.json())

    async def synthesize(self, client, key, text, voice_id, lang):
        r = await client.post(f"{FISH_BASE}/v1/tts", json={"text": text, "reference_id": voice_id, "format": "wav", "latency": "normal"},
                              headers={**self.auth(key), "Content-Type": "application/json"})
        if r.status_code != 200:
            raise SpeechError(http_error("Fish Audio", r))
        if not r.content:
            raise SpeechError("Fish Audio answered without audio.")
        return r.content, r.headers.get("content-type", "audio/wav").split(";")[0]

    async def clone(self, client, key, path, title):
        """A voice model from the owner's recording (fast training): its id, from Fish's own answer; raises with why when it refused."""
        p = Path(path)
        mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".webm": "audio/webm"
                }.get(p.suffix.lower(), "application/octet-stream")
        r = await client.post(f"{FISH_BASE}/model", headers=self.auth(key), timeout=FISH_CLONE_S,
                              data={"visibility": "private", "type": "tts", "title": title, "train_mode": "fast", "enhance_audio_quality": "true"},
                              files=[("voices", (p.name, p.read_bytes(), mime))])
        if r.status_code not in (200, 201):
            raise SpeechError(http_error("Fish Audio", r))
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        mid = body.get("_id") or body.get("id")
        if not mid:
            raise SpeechError("Fish Audio made no voice model from the recording (its answer named none).")
        return mid, body


def tts_chunks(text, n=TTS_CHUNK):
    """Words packed into pieces of at most n characters (Google Translate's voice reads about 200 at a time); only a word longer than a
    piece (an address) is split."""
    out = []
    for w in (text or "").split():
        if out and len(out[-1]) + 1 + len(w) <= n:
            out[-1] += " " + w
        else:
            out += [w[i:i + n] for i in range(0, len(w), n)]
    return out


def mp3_to_wav(mp3, python=None):
    """MP3 -> WAV (24 kHz mono) through the voice environment's PyAV: Monday's window plays WAV only (core/native.py play: this PC's
    Windows player can't open MP3, MCI 266), and Core's own Python has no decoder."""
    py = python or setting("MONDAY_VOICE_PYTHON") or VOICE_PYTHON
    try:
        p = subprocess.run([py, "-I", "-c", MP3_TO_PCM], input=mp3, capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SpeechError(f"The voice environment couldn't turn Google's MP3 into WAV ({type(e).__name__}).") from e
    if p.returncode or not p.stdout:
        raise SpeechError(f"The voice environment couldn't turn Google's MP3 into WAV: {p.stderr.decode(errors='replace')[-200:]}")
    return wav_from_pcm(p.stdout, 24000)


class GoogleFree:
    """Google's free voice: the one Google Translate reads aloud with, no key (the owner, 2026-10-15: "use the earlier TTS"; their voice was
    Chrome's "Google US English", picked 2026-09-25 and 2026-10-06). Not an official API: it is checked, never assumed (list() has it read a
    word), and a failure is said, with no other voice in its place."""
    id, label, keys = "google", "Google's free voice (Google Translate's)", ()
    pricing = "Free, no key: Google Translate's read-aloud voice, over the internet (not an official API: Google may limit it)."
    speaks_on_server = True
    VOICES = {"Google US English": ("en-US", "com"), "Google UK English Female": ("en-GB", "co.uk")}  # name -> (language, Translate's site)

    def key(self):
        return "free", None

    async def _mp3(self, client, text, tld):
        r = await client.get(TRANSLATE_TTS.format(tld=tld), params={"ie": "UTF-8", "client": "tw-ob", "tl": "en", "q": text},
                             headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("audio/"):
            raise SpeechError(f"Google's free voice didn't answer (HTTP {r.status_code}).")
        return r.content

    async def list(self, client, key):
        await self._mp3(client, "ok", "com")  # it answers now (at most once an hour: Speech.voices caches)
        return [{"provider": "google", "id": name, "name": name, "lang": lang, "gender": voices.gender({"name": name}),
                 "natural": True, "offline": False, "rank": 0, "label": f"Google · online · {lang}"} for name, (lang, _) in self.VOICES.items()]

    async def synthesize(self, client, key, text, voice_id, lang):
        tld = self.VOICES.get(voice_id, self.VOICES["Google US English"])[1]
        mp3 = b"".join([await self._mp3(client, piece, tld) for piece in tts_chunks(text)])
        return await asyncio.to_thread(mp3_to_wav, mp3), "audio/wav"


class Custom:
    """The owner's own voice: core/clone_voice.py (F5-TTS) in its own process, fed the recording in data/voice/. Discovered, never assumed:
    the recording, the engine's Python and the engine's own health each say what is missing."""
    id, label, keys = "custom", "Your voice (cloned from your recording, on this PC)", ()
    pricing = "Free, offline: F5-TTS on this computer (its code MIT; its model weights CC-BY-NC 4.0, for personal use)."
    speaks_on_server = True

    def __init__(self, data_dir=None):
        self.dir = Path(data_dir or ROOT / "data") / "voice"
        self.pid, self.started_at, self.health = None, 0.0, None

    def sample(self):
        """The owner's recording as it arrived (preserved), or None."""
        return next((p for p in (self.dir / n for n in VOICE_FILES) if p.exists()), None)

    def python(self):
        p = setting("MONDAY_VOICE_PYTHON") or VOICE_PYTHON
        return p if Path(p).exists() else None

    def key(self):
        s = self.sample()
        return (str(s) if s else None), None

    def url(self, path):
        return f"http://127.0.0.1:{VOICE_PORT}{path}"

    async def probe(self, client):
        """The engine's own account of itself (GET /health), or None when it isn't running."""
        try:
            r = await client.get(self.url("/health"), timeout=3.0)
            self.health = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            self.health = None
        return self.health

    def start(self):
        """The engine, hidden, in its own Python (like Ollama: probes.start_ollama). Its log: data/voice/engine.log."""
        py = self.python()
        if not py or (self.pid and time.monotonic() - self.started_at < VOICE_START_S):
            return self.pid
        self.dir.mkdir(parents=True, exist_ok=True)
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
        env.setdefault("HF_HOME", str(Path(py).parent.parent / "hf"))  # the model's cache beside the engine, never on the small C: drive
        env.setdefault("OMP_NUM_THREADS", "1")  # the model is built on one thread (core/clone_voice.py says why); inference raises it
        with open(self.dir / "engine.log", "ab") as log:
            self.pid = subprocess.Popen([py, "-m", "core.clone_voice", "--serve", "--port", str(VOICE_PORT), "--dir", str(self.dir)],
                                        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=env).pid
        self.started_at = time.monotonic()
        return self.pid

    async def ensure(self, client, wait=VOICE_START_S):
        """The engine running and ready (started here when it isn't), or its last health (an error, still loading) after `wait`."""
        h = await self.probe(client)
        if h is None:
            self.start()
        end = time.monotonic() + wait
        while time.monotonic() < end and not (h and (h.get("ready") or h.get("error"))):
            await __import__("asyncio").sleep(1.0)
            h = await self.probe(client)
        return h

    async def state(self, client, start=True):
        """(status, detail): available / not_configured / waiting / error, in the owner's words, from what is really there."""
        if not self.sample():
            return "not_configured", ("No recording yet. Attach a WAV of about 30 seconds of the voice you want and say “use this recording "
                                      "as your voice” (the panel's Replace sample does the same). Monday keeps a copy in data/voice/ and never "
                                      "alters the original.")
        if not self.python():
            return "not_configured", (f"The voice engine isn't installed: {setting('MONDAY_VOICE_PYTHON') or VOICE_PYTHON} is missing. "
                                      "README › Your voice says how to set it up.")
        h = await self.probe(client)
        if h is None and start:
            if not self.start():
                return "error", "The voice engine couldn't be started."
            return "waiting", "The voice engine is starting (loading its model)."
        if h is None:
            return "waiting", "The voice engine isn't running yet."
        if h.get("error"):
            return "error", h["error"]
        if not h.get("ready"):
            return "waiting", h.get("detail") or "The voice engine is loading its model."
        a = h.get("sample") or {}
        detail = f"Your voice, from {self.sample().name}: {a.get('summary', 'ready')}" if a else "Your voice is ready."
        return "available", detail + f" ({h.get('engine', 'F5-TTS')}, {h.get('device', 'cpu')})."

    def voice(self, lang=None):
        return {"provider": "custom", "id": "owner", "name": "Your voice", "lang": lang or "", "gender": None, "style": "cloned",
                "natural": True, "offline": True, "rank": 0, "label": "cloned from your recording · offline · F5-TTS"}

    async def list(self, client, key):
        return [self.voice()]

    async def synthesize(self, client, key, text, voice_id, lang):
        h = await self.ensure(client)
        if not h:
            return self._fail("The voice engine didn't answer: it isn't running and couldn't be started (data/voice/engine.log says why).")
        if h.get("error"):
            return self._fail(h["error"])
        if not h.get("ready"):
            return self._fail("The voice engine is still loading its model; the reply stayed on screen.")
        try:
            r = await client.post(self.url("/tts"), json={"text": text, "lang": lang}, timeout=VOICE_SYNTH_S)
        except httpx.HTTPError as e:
            return self._fail(f"The voice engine stopped answering ({type(e).__name__}).")
        if r.status_code != 200:
            try:
                why = r.json().get("error")
            except ValueError:
                why = None
            return self._fail(why or f"The voice engine answered {r.status_code}.")
        return r.content, r.headers.get("content-type", "audio/wav").split(";")[0]

    async def prepare(self, client):
        """The engine inspects and prepares the recording (POST /prepare): its analysis, or the reason it can't be used."""
        h = await self.ensure(client)
        if not h or h.get("error") and not h.get("ready"):
            raise SpeechError((h or {}).get("error") or "The voice engine isn't running.")
        r = await client.post(self.url("/prepare"), json={}, timeout=VOICE_SYNTH_S)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200:
            raise SpeechError(body.get("error") or f"The voice engine answered {r.status_code}.")
        return body

    @staticmethod
    def _fail(why):
        raise SpeechError(why)


TTS = {p.id: p for p in (Custom(), FishAudio(), GoogleFree(), Browser(), Gemini(), GoogleCloud())}
STT = [
    {"id": "google_cloud", "label": "Google Cloud Speech-to-Text", "status": "not_built",
     "detail": f"Not built: Monday's ear uses Google's free recognizer (the one Chrome's speech recognition used) instead. Cloud "
               f"Speech-to-Text V2 has no free tier ($0.016 a minute) and V1 gives 60 free minutes a month (checked {PRICES_CHECKED})."},
]


class Speech:
    """One per Core: provider status, cached voice lists, cached audio."""

    def __init__(self, data_dir=None):
        self._voices = {}                 # provider -> (at, voices, error)
        self._audio = OrderedDict()       # (provider, voice, text) -> (bytes, mime)
        self._client = None
        self.custom = TTS["custom"] = Custom(data_dir)  # the owner's voice lives beside this Core's data (test Cores have their own)

    def forget(self, pid):
        """Drop the cached audio of a provider (the owner's recording was replaced: the old voice must not replay)."""
        self._audio = OrderedDict((k, v) for k, v in self._audio.items() if k[0] != pid)

    def client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
        return self._client

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def voices(self, pid, reported=None, win=None, force=False):
        """(voices, error) for a provider. The browser's are what the HUD reported (gender from Windows or the name)."""
        p = TTS.get(pid)
        if p is None:
            return [], f"There is no speech provider called {pid}."
        if pid == "browser":
            return [{**voices.describe(v, win), "provider": "browser", "vendor": voices.describe(v, win)["provider"],
                     "rank": 0} for v in reported or []], None
        if pid == "custom":  # one voice, the owner's, listed only when it can really speak now
            st, detail = await p.state(self.client())
            return ([p.voice()] if st == "available" else []), (None if st == "available" else detail)
        key, env = p.key()
        if not key:
            return [], f"Add {p.keys[0]}=… to Monday\\.env to use these voices."
        hit = self._voices.get(pid)
        if hit and not force and time.monotonic() - hit[0] < (ERROR_TTL_S if hit[2] else VOICES_TTL_S):
            return hit[1], hit[2]
        try:
            vs, err = await p.list(self.client(), key), None
        except SpeechError as e:
            vs, err = [], str(e)
        except httpx.HTTPError as e:
            vs, err = [], f"{p.label} unreachable ({type(e).__name__})."
        self._voices[pid] = (time.monotonic(), vs, err)
        return vs, err

    async def status(self, reported=None, win=None, lang="en"):
        """Every TTS provider: available / not_configured / waiting / error, with why, pricing and female-voice count."""
        rows = []
        for pid, p in TTS.items():
            key, env = p.key()
            if pid == "custom":
                st, detail = await p.state(self.client())
                rows.append({"id": pid, "label": p.label, "status": st, "detail": detail, "pricing": p.pricing, "key_env": None,
                             "female": 0, "voices": 1 if st == "available" else 0, "server": True, "sample": (p.health or {}).get("sample"),
                             "sample_file": p.sample().name if p.sample() else None})
                continue
            vs, err = await self.voices(pid, reported, win)
            fem = [v for v in vs if v["gender"] == "female"]
            if pid == "browser":
                st = "available" if vs else "waiting"
                detail = f"{len(fem)} female of {len(vs)} Windows voices reported by Monday's window." if vs else \
                    "Monday's window reports Windows' voices when it opens."
            elif not key:
                st, detail = "not_configured", err
            elif err:
                st, detail = "error", err
            else:
                st, detail = "available", f"{len(fem)} female of {len(vs)} voices ({env or 'no key needed'})."
            rows.append({"id": pid, "label": p.label, "status": st, "detail": detail, "pricing": p.pricing,
                         "key_env": None if pid == "browser" or not p.keys else (env or p.keys[0]), "female": len(fem), "voices": len(vs),
                         "server": p.speaks_on_server})
        return rows

    async def synthesize(self, pid, voice_id, text, lang=None):
        """Audio for text in a server provider's voice: (bytes, mime). Raises SpeechError with an honest reason."""
        p = TTS.get(pid)
        if p is None or not p.speaks_on_server:
            raise SpeechError("That voice is spoken by the browser, not by Monday Core.")
        text = re.sub(r"\s+", " ", text or "").strip()[:MAX_TTS_CHARS]
        if not text:
            raise SpeechError("Nothing to say.")
        key, _ = p.key()
        if not key:
            raise SpeechError("Your voice isn't set up yet: add your recording first (the voice panel's Replace sample)." if pid == "custom"
                              else f"{p.label} isn't configured: add {p.keys[0]} to Monday\\.env.")
        ck = (pid, voice_id, text)
        if ck in self._audio:
            self._audio.move_to_end(ck)
            return self._audio[ck]
        try:
            out = await p.synthesize(self.client(), key, text, voice_id, lang)
        except httpx.HTTPError as e:
            raise SpeechError(f"{p.label} unreachable ({type(e).__name__}).") from e
        self._audio[ck] = out
        while len(self._audio) > AUDIO_CACHE_MAX:
            self._audio.popitem(last=False)
        return out


def brief(text, limit=140):
    """What to say out loud about a long result: its first sentence (or clause), never more than `limit` characters.
    The whole text stays on screen."""
    t = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    t = re.sub(r"[`*_#>•]|\bTASK-\d{4}-\d{4}\b:?", "", t)
    t = re.sub(r"\s*\(\d(?:\s*,\s*\d)*\)", "", t)  # a look at the screen's "(1)", "(2, 3)": numbers marked on the screen, never read out
    t = re.sub(r"\s+", " ", t).strip()
    first = re.match(r"(.+?[.!?])(?=\s|$)", t)
    s = first.group(1) if first else t
    if len(s) > limit:
        cut = s[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
        s = cut + "…"
    return s


# Steps whose effect is on the screen or the speakers: the owner sees or hears it, so there is nothing to say (the brief, 2026-10-06:
# "the music playing is the response"). Reading, answering, listing, testing and the rest carry information, and are said or shown.
SILENT_ACTIONS = {"open", "close", "focus", "window", "type", "fill_form", "click", "click_element", "key", "scroll", "play", "media", "photo",
                  "open_url", "search", "navigate", "save", "new_tab", "switch_tab", "close_tab", "back", "volume", "open_project",
                  "wait", "drag", "move_mouse", "download"}


def quiet(steps=()):
    """Nothing to say: every step was an action on the screen or the speakers, and each was seen to work. One unseen or failed step,
    or any step that reads or answers, means the reply carries something the computer itself doesn't show."""
    done = [x for x in steps if x]
    return bool(done) and all(x.get("status") == "done" and x.get("verified") is True and x.get("action") in SILENT_ACTIONS for x in done)


def result_line(text, steps=()):
    """What to say when Monday's own actions finish: "Done." when a plan of several steps was seen to work at every
    step (the brief's §34), else the last step's first sentence (a question's answer, or what the last action did).
    A screen loop's steps have no total: its result is its verdict ("Done: … I can see it in …"), said as it is."""
    done = [x for x in steps if x and x.get("status") == "done"]
    if not done or any(x.get("total") is None for x in done):
        return brief(text) or "Done."
    if len(done) >= 2 and all(x.get("verified") is True for x in done):
        # what is playing is worth the words: "Playing Blinding Lights by The Weeknd.", not "Done." (the owner's brief, 2026-10-05)
        return brief(done[-1].get("text") or text) if done[-1].get("action") in ("play", "media") else "Done."
    return brief(done[-1].get("text") or text) or "Done."


if __name__ == "__main__":  # python -m core.speech: parsing, ordering and honesty, no network
    g, tok = Gemini.parse_voices({"voices": [
        {"id": "Kore", "displayName": "Kore", "languageCode": "en-US", "gender": "female", "persona": ["Firm"], "accent": "American"},
        {"name": "Puck", "display_name": "Puck", "language_code": "en-US", "gender": "male", "description": "Upbeat"},
        {"id": "Leda", "languageCode": "en-IN", "gender": "FEMALE", "persona": "Youthful"},
        {"id": "Mystery", "languageCode": "en-GB"}], "nextPageToken": "p2"})
    assert [v["id"] for v in g] == ["Kore", "Puck", "Leda", "Mystery"] and tok == "p2"
    assert [v["gender"] for v in g] == ["female", "male", "female", None], "gender is the provider's own, never guessed"
    fem, other = split(g, "en-IN")
    assert [v["id"] for v in fem] == ["Leda", "Kore"] and fem[0]["number"] == 1, "the owner's region first"
    assert [v["id"] for v in other] == ["Puck", "Mystery"]
    c = GoogleCloud.parse_voices({"voices": [
        {"name": "en-US-Standard-C", "languageCodes": ["en-US"], "ssmlGender": "FEMALE"},
        {"name": "en-US-Chirp3-HD-Aoede", "languageCodes": ["en-US"], "ssmlGender": "FEMALE"},
        {"name": "en-US-Neural2-F", "languageCodes": ["en-US"], "ssmlGender": "FEMALE"},
        {"name": "en-GB-Chirp3-HD-Charon", "languageCodes": ["en-GB"], "ssmlGender": "MALE"},
        {"name": "en-US-Wavenet-X", "languageCodes": ["en-US"], "ssmlGender": "SSML_VOICE_GENDER_UNSPECIFIED"}]})
    assert [v["id"] for v in c] == ["en-US-Chirp3-HD-Aoede", "en-US-Neural2-F", "en-GB-Chirp3-HD-Charon", "en-US-Wavenet-X"], "Standard left out"
    fem, other = split(c, "en-US")
    assert [v["id"] for v in fem] == ["en-US-Chirp3-HD-Aoede", "en-US-Neural2-F"], "Chirp 3 HD before Neural2"
    assert fem[0]["name"] == "Aoede (en-US)" and fem[1]["name"] == "F (Neural2, en-US)" and other[-1]["gender"] is None
    wav, mime = Gemini.parse_audio({"steps": [{"type": "thought"}, {"type": "model_output", "content": [
        {"type": "text", "text": "x"}, {"type": "audio", "data": base64.b64encode(b"\0\0" * 2400).decode(), "mime_type": "audio/l16;rate=24000"}]}]})
    assert mime == "audio/wav" and wav[:4] == b"RIFF" and len(wav) == 44 + 4800, "raw PCM is wrapped as a playable WAV"
    raw, mime = Gemini.parse_audio({"steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(b"RIFFxx").decode()}]}]})
    assert (raw, mime) == (b"RIFFxx", "audio/wav")
    try:
        Gemini.parse_audio({"steps": []})
        raise AssertionError("no audio must fail")
    except SpeechError:
        pass
    # Fish Audio (2026-10-21): the owner's own models as voices, the TTS call's shape, a clone's id from Fish's answer; a refusal says why
    fv = FishAudio.parse_models({"total": 2, "items": [{"_id": "m1", "title": "Bhavye", "type": "tts", "languages": ["en"]}, {"_id": "x", "type": "svc"}]})
    assert fv == [{"provider": "fish", "id": "m1", "name": "Bhavye", "lang": "en", "gender": None, "style": "cloned", "natural": True, "offline": False,
                   "rank": 0, "label": "cloned · Fish Audio · en"}], fv

    class _R:
        def __init__(self, code, body=None, content=b"", ctype="application/json"):
            self.status_code, self._b, self.content, self.headers, self.text = code, body, content, {"content-type": ctype}, ""

        def json(self):
            return self._b

    class _C:
        def __init__(self, *answers):
            self.answers, self.calls = list(answers), []

        async def get(self, url, **kw):
            self.calls.append(("GET", url, kw))
            return self.answers.pop(0)

        async def post(self, url, **kw):
            self.calls.append(("POST", url, kw))
            return self.answers.pop(0)
    import asyncio
    import tempfile
    fish = FishAudio()
    cl = _C(_R(200, content=b"RIFFfake", ctype="audio/wav"), _R(201, {"_id": "m9", "title": "Monday"}), _R(402, {"message": "no credit"}))
    assert asyncio.run(fish.synthesize(cl, "k", "hello", "m1", "en")) == (b"RIFFfake", "audio/wav")
    assert cl.calls[0][2]["json"] == {"text": "hello", "reference_id": "m1", "format": "wav", "latency": "normal"} and cl.calls[0][2]["headers"]["Authorization"] == "Bearer k"
    with tempfile.TemporaryDirectory() as d:
        rec = Path(d) / "me.wav"
        rec.write_bytes(b"RIFF" + b"\0" * 100)
        mid, _body = asyncio.run(fish.clone(cl, "k", rec, "Monday"))
        assert mid == "m9" and cl.calls[1][2]["data"]["train_mode"] == "fast" and cl.calls[1][2]["files"][0][0] == "voices" \
            and cl.calls[1][2]["files"][0][1][0] == "me.wav", cl.calls[1]
        try:
            asyncio.run(fish.clone(cl, "k", rec, "Monday"))
            raise AssertionError("a refusal must be said")
        except SpeechError as e:
            assert "Fish Audio" in str(e), e
    assert "fish" in TTS and FishAudio().key() == (None, None) or FishAudio().key()[1] == "FISH_AUDIO_API_KEY"

    class R:  # a stand-in httpx.Response for the error wording
        def __init__(self, code, body):
            self.status_code, self._b = code, body
            self.text = str(body)

        def json(self):
            return self._b
    assert "rejected the key" in http_error("Gemini", R(400, {"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}))
    assert "billing" in http_error("Google Cloud Text-to-Speech", R(403, {"error": {"message": "This API method requires billing to be enabled.", "status": "PERMISSION_DENIED", "details": [{"reason": "BILLING_DISABLED"}]}}))
    assert "isn't enabled" in http_error("Google Cloud Text-to-Speech", R(403, {"error": {"message": "Cloud Text-to-Speech API has not been used in project 123 before or it is disabled.", "status": "PERMISSION_DENIED"}}))
    assert "429" in http_error("Gemini", R(429, {"error": {"message": "Resource has been exhausted"}}))
    assert "rejected the key" in http_error("Gemini", R(400, [{"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}])), "list-wrapped errors"
    long = "Monday opened Notepad and wrote the shopping list you asked for. " * 6
    pieces = tts_chunks(long)
    assert all(len(p) <= TTS_CHUNK for p in pieces) and " ".join(pieces) == long.strip() and len(pieces) == 3, pieces
    assert tts_chunks("") == [] and "".join(tts_chunks("x" * 300)) == "x" * 300, "a word longer than a piece is split, never lost"
    g = GoogleFree()
    assert g.key()[0] and not g.keys and voices.gender({"name": "Google US English"}) == "female", "no key; the owner's earlier voice"
    try:
        mp3_to_wav(b"not audio", python=r"C:\nowhere\python.exe")
        raise AssertionError("a missing voice environment must fail in words")
    except SpeechError as e:
        assert "couldn't turn Google's MP3 into WAV" in str(e)
    assert http_error("Gemini", R(502, "<html>bad gateway</html>")).startswith("Gemini error 502")
    assert brief("Claude Code finished TASK-2026-0012: Fix the login redirect. 4 files changed, tests pass.") == "Claude Code finished Fix the login redirect."
    assert brief("x " * 200).endswith("…") and len(brief("x " * 200)) <= 141
    assert brief("Done.") == "Done." and brief("") == ""
    assert brief("You're looking at a ChatGPT conversation (1) about Sarvam AI (2, 3). Then more.") == \
        "You're looking at a ChatGPT conversation about Sarvam AI.", "the numbers marked on the screen aren't read out"
    assert brief("Python (3.14) and port (8765) stay.") == "Python (3.14) and port (8765) stay.", "only a lone mark number goes"
    row = lambda text, verified=True, total=2: {"status": "done", "verified": verified, "total": total, "text": text}  # noqa: E731
    opened, typed = row("Opened Windows Notepad."), row("Typed 5 characters into Untitled - Notepad. I can see it there.")
    assert result_line(f"{opened['text']}\n{typed['text']}", [opened, typed]) == "Done."
    unseen = row("Typed 5 characters into Untitled - Notepad, but I can't see it there.", False)
    assert result_line("…", [opened, unseen]) == "Typed 5 characters into Untitled - Notepad, but I can't see it there.", "unseen: say what happened"
    answer = row("Yes, chrome is running: 11 processes using 1496 MB. Windows: Inbox - me@example.com - Gmail.", total=1)
    assert result_line(answer["text"], [answer]) == "Yes, chrome is running: 11 processes using 1496 MB.", "the answer, not the window titles"
    screen = row("The editor shows main.py with a syntax error. Line 12 is highlighted.\n\n(Read locally from Code.)", total=1)
    assert result_line(screen["text"], [screen]) == "The editor shows main.py with a syntax error.", "the summary, not the source note"
    loop = row("Type “hi” into the document", None, total=None)
    assert result_line("Done: open Notepad and write hi. I can see it in Untitled - Notepad.", [loop]) == "Done: open Notepad and write hi."
    act = lambda action, verified=True, status="done": {**row("x", verified), "action": action, "status": status}  # noqa: E731
    assert quiet([act("open"), act("play")]) and quiet([act("type")]) and quiet([act("open"), act("photo")]), "actions seen to work: the computer is the reply"
    assert not quiet([act("open"), act("type", False)]) and not quiet([act("open", status="failed")]), "unseen or failed: say so"
    assert not quiet([act("open"), act("now_playing")]) and not quiet([act("read_screen")]) and not quiet([act("tests")]), "an answer is shown"
    assert not quiet([]) and not quiet([None]), "no steps: nothing is known to have worked"
    print("speech self-check: OK", f"(TTS providers: {', '.join(TTS)}; STT: {', '.join(s['id'] for s in STT)}; prices checked {PRICES_CHECKED})")
