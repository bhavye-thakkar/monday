"""Monday's eyes: OpenCV on this PC's camera (2026-10-09, the owner: "when I say what is this, it should say this is what and this is
what, live, using my camera… in the backend use OpenCV… when I say remember this, in chat, on the camera or from the phone, it should
store it and remember it, and OpenCV will remember it too").

- The camera opens only while Monday looks or a page watches the live view, and closes CAMERA_IDLE_S after (its light says so).
  Nothing it sees is kept, except what the owner asks Monday to remember: one picture of that thing, in data/vision/.
- "What is this?": YOLOX-S finds the objects (80 kinds: a cup, a phone, a laptop…). Each is compared with the things the owner taught
  (MobileNetV2's 1280 pooled features, cosine ≥ SAME_MIN: "your mug"). With nothing found, MobileNetV2 names the middle of the picture
  (1,000 kinds). The owner is in most pictures, so a person is only mentioned when nothing else is there. Below SURE_MIN it is a guess.
- "Remember this is my mug": the object in the picture (the biggest one found, else the middle), from the camera or from a photo sent
  in chat or from the phone. Its features, and a mirrored copy's, go to data/vision/known.json (beside the database); teaching it again adds another view.
- Models: OpenCV's model zoo (huggingface.co/opencv, Apache-2.0) in MODELS, checked by SHA-256 when downloaded (2026-10-09).
- Jev's pictures (2026-10-17, its image pack): when OpenCV knows none of the owner's things in a look, Jev compares the picture with the
  kept picture of each taught thing (core/jev.py which_known); only with the owner's Settings switch on, and only from KNOWN_TRUST.
"""
import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path

MODELS = Path(os.environ.get("MONDAY_MODELS") or r"E:\monday\models")
DETECTOR = "object_detection_yolox_2022nov.onnx"
NAMER = "image_classification_mobilenetv2_2022apr.onnx"
NAMES_FILE = "labelsimagenet1k.h"
FEATURES = "onnx_node!Reshape_103"  # MobileNetV2's 1280 pooled features (OpenCV 5's classic engine names it so; the new one can't give it)
CAMERA = int(os.environ.get("MONDAY_CAMERA", "0"))
FRAME_SIZE = (1280, 720)
WARMUP_S = 0.8        # a webcam's first frames are dark while it sets its exposure (counted from its first frame, not from opening it)
DARK_MEAN = 20        # a look's picture darker than this (mean of 0-255) is waited on, then said as black: a shutter, a dark room
DARK_WAIT_S = 3.0     # how long a look waits for the exposure to settle before it says so
BACKGROUND = {"chair", "couch", "bed", "dining table", "tv", "toilet", "sink", "refrigerator", "oven", "microwave", "bench", "potted plant"}
# (the room, not what's held up: said after the things in front of the camera)
CAMERA_IDLE_S = 20    # the camera (and its light) goes off this long after the last look, when no page watches
DETECT_MIN = 0.40     # YOLOX score from which a thing is named at all
SURE_MIN = 0.60       # below this a thing is said as a guess ("probably a cup")
NAME_MIN = 0.45       # MobileNetV2's top probability from which the middle of the picture is named (as a guess below NAME_SURE);
NAME_SURE = 0.70      # (2026-10-20: at .20 / .45 a dim frame was "a shower curtain", said as sure at .49)
SAME_MIN = 0.78       # cosine of the features from which a thing is one the owner taught
MIN_SIDE = 24         # pixels: a smaller box is too little to compare
MAX_SAID = 4          # things named in one answer
READ_MIN_WORDS = 3    # fewer words on a thing aren't read out (a logo, the letters on a key)
READ_SAID_WORDS = 60  # how much of a page is said out loud; all of it is in the answer's `read`
COVER_TALLER = 1.8    # one line this many times taller than the middle one: a cover's title (a book, a box), when YOLOX saw no book
LETTER = re.compile(r"\b(?:dear\b|sincerely|regards|yours\s+(?:faithfully|truly|sincerely)|to\s+whom\s+it\s+may\s+concern|subject\s*:|invoice"
                    r"|statement|notice|ref(?:erence)?\s*(?:no|number)?\s*[:#])", re.I)
MAX_VIEWS = 12        # views kept per taught thing (the newest)
STREAM_FPS = 10
DETECT_EVERY_S = 0.5  # the live view's boxes are refreshed this often
LIVE_JPEG = 70
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
COCO = ("person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light", "fire hydrant", "stop sign",
        "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
        "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
        "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv",
        "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
        "scissors", "teddy bear", "hair drier", "toothbrush")
SAY = {"tv": "TV", "cell phone": "phone", "remote": "remote control", "dining table": "table", "potted plant": "plant", "hair drier": "hair dryer"}


class VisionError(Exception):
    """Something the eyes can't do, said the way the owner can act on it."""


def _a(label):
    word = SAY.get(label, label)
    return f"{'an' if word[:1].lower() in 'aeiou' else 'a'} {word}"


def yours(label):
    """How a taught thing is said back: "my mug" -> "your mug"; "Rahul's charger" stays; a bare "mug" -> "the mug"."""
    t = " ".join(str(label).split())
    t = re.sub(r"^(?:my|our)\b", "your", t, flags=re.I)
    return t if re.match(r"^(?:your|the|a|an|this|that|his|her|their)\b|^[\w.-]+['’]s\b", t, re.I) else f"the {t}"


def _key(label):
    return re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-") or "thing"


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


async def read_text(rgb):
    """The words on what is held up: Windows' own OCR (core/computer.py ocr, the screen's reader since 2026-10-10: local, free, nothing to
    download; the owner, 2026-10-20: "when I'm holding a book or a letter, read that and tell me what it is"). -> OCR lines in reading
    order, [] when there are none or there is no such reader here (another OS, no language pack): the look still names the thing."""
    try:
        from PIL import Image

        from .computer import ocr
        return [ln for ln in await ocr(Image.fromarray(rgb)) if len(ln["text"].strip()) > 1]
    except Exception as e:  # (never in a look's way)
        print("vision ocr:", repr(e)[:120], flush=True)
        return []


def _cut(words, n):
    return "“" + " ".join(words[:n]) + ("…”" if len(words) > n else "”")


def reading(lines, named):
    """What a thing with words on it is, and what it says. -> (kind, said, text), or (None, None, "") with fewer than READ_MIN_WORDS words.
    A book: YOLOX saw one, or one line stands COVER_TALLER times above the rest and there is little else (a cover): its title, the tallest
    line, then the rest. A letter: its own words say so (LETTER: Dear, Regards, Subject:, Invoice…). Else a page of text. `said` is at most
    READ_SAID_WORDS words; `text` is all of it, for the task's card."""
    lines = [ln for ln in lines if str(ln.get("text") or "").strip()]
    text = "\n".join(ln["text"].strip() for ln in lines)
    words = text.split()
    if len(words) < READ_MIN_WORDS:
        return None, None, ""
    tallest = max(lines, key=lambda ln: float(ln.get("h") or 0))
    big = [ln for ln in lines if float(ln.get("h") or 0) >= 0.85 * float(tallest.get("h") or 0)]  # a title set over two lines ("ATOMIC" / "HABITS")
    # the title against the middle of the rest: its own second line isn't "the rest" (counted there, "ATOMIC" 60 vs "HABITS" 58 made no cover)
    others = sorted(float(ln.get("h") or 0) for ln in lines if ln not in big)
    cover = bool(others) and float(tallest.get("h") or 0) >= COVER_TALLER * others[len(others) // 2] and len(words) <= 40
    if any(t.get("label") == "book" for t in named) or cover:
        title = " ".join(ln["text"].strip() for ln in big)
        rest = " ".join(ln["text"].strip() for ln in lines if ln not in big).split()
        return "a book", f"its cover reads “{title}”" + (f", then {_cut(rest, max(8, READ_SAID_WORDS - len(title.split())))}" if rest else ""), text
    return ("a letter" if LETTER.search(text) else "a page of text"), "it says: " + _cut(words, READ_SAID_WORDS), text


def say(named, people):
    """One sentence for what was seen: "This is your mug, and this is a phone." Honest about guesses and about seeing nothing."""
    parts = []
    for t in named[:MAX_SAID]:
        if t.get("known"):
            parts.append(yours(t["known"]))
        elif t["score"] >= (NAME_SURE if t.get("by") == "namer" else SURE_MIN):
            parts.append(_a(t["label"]))
        else:
            parts.append(f"probably {_a(t['label'])}")
    if not parts:
        return ("I can see you, but nothing in front of the camera I can name. Hold it up a little closer?" if people
                else "I can't make out what that is. Hold it up to the camera, in good light?")
    if len(parts) == 1:
        return f"This is {parts[0]}."
    return f"This is {parts[0]}, and this is {parts[1]}." + (f" I can also see {_join(parts[2:])}." if parts[2:] else "")


class Eyes:
    jev = None  # Jev's second look at the owner's things (core/jev.py which_known), set by Monday
    cloud = None  # async (rgb) -> (phrase, model) | (None, None): a model that can see, when OpenCV can't name it (Monday.name_held; the
    # owner's Settings switch "vision_cloud", off by default: the picture leaves this computer)

    def __init__(self, data):
        self.dir = Path(data) / "vision"  # beside Monday's database: a test Core (MONDAY_DB) never reads the owner's things
        self.cam_lock = threading.Lock()   # OpenCV's capture isn't shared between threads
        self.net_lock = threading.Lock()   # nor are the nets
        self.cap = None
        self.opened_at = 0.0
        self.used_at = 0.0
        self.viewers = 0
        self.closer = None
        self.last = None                   # the last look: {"at", "text", "things"}
        self.error = None
        self._det = self._namer = self._names = None
        self.known = self._load()

    # -- what is there --------------------------------------------------------------------------------------------------
    def ready(self):
        """(ok, why): the models in MODELS and OpenCV importable. The camera itself is only known by opening it."""
        missing = [f for f in (DETECTOR, NAMER, NAMES_FILE) if not (MODELS / f).is_file()]
        if missing:
            return False, f"The vision models aren't in {MODELS}: {', '.join(missing)}."
        import importlib.util  # found, not imported: OpenCV's first import held Core's event loop ~3 s (2026-10-10); _nets imports it, in a thread
        missing = [m for m in ("cv2", "numpy") if importlib.util.find_spec(m) is None]
        if missing:
            return False, f"OpenCV isn't installed here ({missing[0]})."
        return True, None

    def status(self):
        ok, why = self.ready()
        return {"available": ok, "detail": why or self.error, "camera": "on" if self.cap is not None else "off", "watching": self.viewers,
                "known": [{"label": t["label"], "views": len(t["views"]), "at": t.get("at"), "from": t.get("from")} for t in self.known],
                "last": {k: (self.last or {}).get(k) for k in ("at", "text")} if self.last else None}

    # -- the nets ----------------------------------------------------------------------------------------------------------
    def _nets(self):
        if self._det is None:
            ok, why = self.ready()
            if not ok:
                raise VisionError(why)
            import cv2
            import numpy as np
            det = cv2.dnn.readNet(str(MODELS / DETECTOR))
            namer = cv2.dnn.readNetFromONNX(str(MODELS / NAMER), cv2.dnn.ENGINE_CLASSIC)
            names = re.findall(r'"([^"]+)"', (MODELS / NAMES_FILE).read_text(encoding="utf-8"))
            if len(names) != 1000:
                raise VisionError(f"{NAMES_FILE} should hold 1000 names, it holds {len(names)}.")
            grids, strides = [], []
            for stride in (8, 16, 32):  # YOLOX's anchor-free grid (OpenCV's yolox.py)
                n = 640 // stride
                xv, yv = np.meshgrid(np.arange(n), np.arange(n))
                grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
                strides.append(np.full((n * n, 1), stride))
            self.grids, self.strides = np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)
            self._names = [x.split(",")[0].strip() for x in names]
            self._det, self._namer = det, namer
        return self._det, self._namer

    def detect(self, rgb):
        """YOLOX-S on one RGB picture: [{"label", "score", "box": [x0, y0, x1, y1]}], best first."""
        import cv2
        import numpy as np
        h, w = rgb.shape[:2]
        r = min(640 / h, 640 / w)
        pad = np.full((640, 640, 3), 114.0, np.float32)  # letterbox, as the model was trained; in BGR, as it was trained (2026-10-20: fed
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)       # RGB it scored a teddy bear .80 where BGR gives .90, and missed things)
        pad[:int(h * r), :int(w * r)] = cv2.resize(bgr, (int(w * r), int(h * r))).astype(np.float32)
        with self.net_lock:
            det, _ = self._nets()
            det.setInput(pad.transpose(2, 0, 1)[None])
            out = det.forward(det.getUnconnectedOutLayersNames())[0][0].copy()
        out[:, :2] = (out[:, :2] + self.grids) * self.strides
        out[:, 2:4] = np.exp(out[:, 2:4]) * self.strides
        x0, y0 = out[:, 0] - out[:, 2] / 2, out[:, 1] - out[:, 3] / 2
        scores = out[:, 4:5] * out[:, 5:]
        best, cls = scores.max(1), scores.argmax(1)
        keep = cv2.dnn.NMSBoxesBatched(np.stack([x0, y0, out[:, 2], out[:, 3]], 1).tolist(), best.tolist(), cls.tolist(), DETECT_MIN, 0.5)
        things = []
        for i in np.array(keep, dtype=int).reshape(-1):
            bx = [max(0, int(x0[i] / r)), max(0, int(y0[i] / r)), min(w, int((x0[i] + out[i, 2]) / r)), min(h, int((y0[i] + out[i, 3]) / r))]
            if bx[2] - bx[0] >= 4 and bx[3] - bx[1] >= 4:
                things.append({"label": COCO[int(cls[i])], "score": round(float(best[i]), 3), "box": bx})
        return sorted(things, key=lambda t: -t["score"])

    def features(self, rgb):
        """MobileNetV2 on one RGB crop, padded to a square (the whole thing stays in view): (unit 1280-vector, 1000 probabilities)."""
        import cv2
        import numpy as np
        h, w = rgb.shape[:2]
        side = max(h, w)
        sq = np.empty((side, side, 3), np.uint8)
        sq[:] = rgb.reshape(-1, 3).mean(0).astype(np.uint8)
        sq[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = rgb
        x = cv2.resize(sq, (224, 224)).astype(np.float32) / 255.0
        x = ((x - np.array(MEAN, np.float32)) / np.array(STD, np.float32)).transpose(2, 0, 1)[None].astype(np.float32)
        with self.net_lock:
            _, namer = self._nets()
            namer.setInput(x)
            feat, logits = namer.forward([FEATURES, "output"])
        v = feat.reshape(-1).astype(np.float32)
        v /= float(np.linalg.norm(v)) + 1e-9
        z = logits.reshape(-1)
        p = np.exp(z - z.max())
        return v, p / p.sum()

    def match(self, v):
        """(label, cosine) of the taught thing nearest to v, when it is at least SAME_MIN; else (None, best cosine)."""
        import numpy as np
        best, label = 0.0, None
        for t in self.known:
            sim = float(np.max(np.asarray(t["views"], np.float32) @ v)) if t["views"] else 0.0
            if sim > best:
                best, label = sim, t["label"]
        return (label, round(best, 3)) if best >= SAME_MIN else (None, round(best, 3))

    def see(self, rgb):
        """Everything one picture shows: (named things, best first; people). Things taught by the owner are named as theirs."""
        h, w = rgb.shape[:2]
        found = self.detect(rgb)
        people = [t for t in found if t["label"] == "person"]
        named = []
        for t in found:
            x0, y0, x1, y1 = t["box"]
            if t["label"] == "person" or min(x1 - x0, y1 - y0) < MIN_SIDE:
                continue
            v, _ = self.features(rgb[y0:y1, x0:x1])
            known, sim = self.match(v)
            named.append({**t, "known": known, "similarity": sim})
        if not named:  # nothing of the 80 kinds: whatever is in the middle of the picture (its own thing, or a guess when no one is there:
            side = int(min(h, w) * 0.6)  # with the owner in the picture its middle is their face and clothes, 2026-10-20 "a sunglasses")
            box = [(w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2]
            v, p = self.features(rgb[box[1]:box[3], box[0]:box[2]])
            known, sim = self.match(v)
            top = int(p.argmax())
            if known or (float(p[top]) >= NAME_MIN and not people):
                named.append({"label": self._names[top], "score": round(float(p[top]), 3), "box": box, "known": known, "similarity": sim, "by": "namer"})
        # the owner's things first, then what is held up (the biggest thing that isn't the room), then the room
        named.sort(key=lambda t: (not t.get("known"), t["label"] in BACKGROUND, -(t["box"][2] - t["box"][0]) * (t["box"][3] - t["box"][1])))
        return named, people

    # -- the camera ----------------------------------------------------------------------------------------------------------
    def grab(self, look=False):
        """One BGR picture from the camera, opened if it wasn't (a worker thread: OpenCV blocks). look: a "what is this", which waits up to
        DARK_WAIT_S for a picture bright enough to see in and says when there is none (the live view takes what comes)."""
        import cv2
        with self.cam_lock:
            if self.cap is None:
                cap = cv2.VideoCapture(CAMERA, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    raise VisionError("The camera didn't open. Another app may be using it, or Windows' camera privacy setting "
                                      "(Settings › Privacy & security › Camera › Let desktop apps access your camera) blocks it.")
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # (most webcams give 720p fast only as MJPG: more frames for
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_SIZE[0])                # the exposure to settle on)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_SIZE[1])
                cap.read()  # (the first frame can take a second to come: the warm-up counts from it, not from opening)
                self.cap, self.opened_at = cap, time.monotonic()
            while time.monotonic() - self.opened_at < WARMUP_S:
                self.cap.read()
            ok, frame = self.cap.read()
            if look:
                until = time.monotonic() + DARK_WAIT_S
                while ok and frame is not None and float(frame.mean()) < DARK_MEAN and time.monotonic() < until:
                    ok, frame = self.cap.read()
            self.used_at = time.monotonic()
        if not ok or frame is None:
            self.release()
            raise VisionError("The camera opened but sent no picture. Is it covered, or in use by another app?")
        if look and float(frame.mean()) < DARK_MEAN:
            raise VisionError("The camera's picture is black: is its shutter closed or its cover on, or the room dark? Then ask again.")
        return frame

    def release(self):
        with self.cam_lock:
            if self.cap is not None:
                self.cap.release()
                self.cap = None

    def _close_soon(self):
        async def close():
            while True:
                await asyncio.sleep(2)
                if self.viewers == 0 and time.monotonic() - self.used_at >= CAMERA_IDLE_S:
                    await asyncio.to_thread(self.release)
                    self.closer = None
                    return
        if self.closer is None or self.closer.done():
            self.closer = asyncio.ensure_future(close())

    def _picture(self, image):
        """RGB from a photo file (sent in chat or from the phone) or, without one, from the camera."""
        import cv2
        import numpy as np
        if image:
            data = np.fromfile(str(image), np.uint8)  # imread can't open every Windows path; imdecode can
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
            if bgr is None:
                raise VisionError("That file isn't a picture I can read.")
        else:
            bgr = self.grab(look=True)
        return bgr, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    # -- what the owner asks ---------------------------------------------------------------------------------------------------
    async def warm(self):
        """Load the nets now, off the event loop (Core start; a look): the first question then waits only for the camera. Errors wait for a look."""
        def load():
            with self.net_lock:  # the same lock detect/features hold: the nets load once, whoever asks first
                self._nets()
        try:
            await asyncio.to_thread(load)
        except Exception:
            pass

    async def look(self, image=None):
        """"What is this?": what the camera (or the photo) shows, said in one sentence. -> {ok, text, verified, things}. The nets load while
        the camera opens and warms up (measured 2026-10-10: 6.4 s one after the other from cold)."""
        try:
            (_, rgb), _ = await asyncio.gather(asyncio.to_thread(self._picture, image), self.warm())
            (named, people), lines = await asyncio.gather(asyncio.to_thread(self.see, rgb), read_text(rgb))  # the nets and the OCR together
        except VisionError as e:
            self.error = str(e)
            return {"ok": False, "verified": False, "text": str(e)}
        finally:
            if not image:
                self._close_soon()
        await self._jev_knows(rgb, named)
        kind, said, read = reading(lines, named)
        text = f"This is {kind}: {said}." if kind else say(named, people)  # words on it: what it is and what it says (2026-10-20)
        sure = bool(kind) or any(t.get("known") or t["score"] >= (NAME_SURE if t.get("by") == "namer" else SURE_MIN) for t in named)
        who = None
        if not sure and self.cloud is not None:  # OpenCV can't name it for sure (its 80 kinds and 1000 names miss most things held up)
            phrase, who = await self.cloud(rgb)
            if phrase:
                text = f"It looks like {phrase}." + (" I can also see you." if people and "person" not in phrase.lower() else "")
        print(time.strftime("%H:%M:%S"), f"vision: mean {float(rgb.mean()):.0f}, "  # (what a look saw, for a later "why": no picture kept)
              f"things {[(t.get('known') or t['label'], t['score']) for t in named][:MAX_SAID]}, people {len(people)}, "
              f"words read {len(read.split())}" + (f", asked {who}" if who else ""), flush=True)
        self.last, self.error = {"at": time.time(), "text": text, "things": named + people}, None
        return {"ok": True, "verified": True, "text": text if not image else text.replace("This is", "In the photo, this is", 1),
                "things": [{k: t.get(k) for k in ("label", "score", "known", "box")} for t in named], "read": read}

    async def remember(self, label, image=None):
        """"Remember this is my mug": the thing in the picture is learned under the owner's words. -> {ok, text, verified, what}."""
        label = " ".join(str(label or "").split()).strip(" .!?,;:\"'“”")
        if not label:
            return {"ok": False, "verified": False, "text": "What should I remember it as? Say “remember this is my mug”."}
        try:
            bgr, rgb = await asyncio.to_thread(self._picture, image)
            v1, v2, box, kind = await asyncio.to_thread(self._learn_views, rgb)
        except VisionError as e:
            self.error = str(e)
            return {"ok": False, "verified": False, "text": str(e)}
        finally:
            if not image:
                self._close_soon()
        thing = next((t for t in self.known if _key(t["label"]) == _key(label)), None)
        if thing is None:
            thing = {"label": label, "views": [], "at": time.time(), "from": "photo" if image else "camera"}
            self.known.append(thing)
        thing["views"] = (thing["views"] + [[round(float(x), 5) for x in v1], [round(float(x), 5) for x in v2]])[-MAX_VIEWS:]
        thing["seen_as"] = kind
        await asyncio.to_thread(self._keep_picture, bgr, box, label)
        self._save()
        again = _key(label) in {_key(t["label"]) for t in self._load()}  # read back from the file before saying it's kept
        said = yours(label)
        return {"ok": again, "verified": again, "what": kind,
                "text": (f"Got it: that's {said}. I'll know it next time." + (f" (It looks like {_a(kind)} to me.)" if kind else "")) if again
                else "I couldn't save that on this computer."}

    def pictures(self):
        """The newest kept picture of each thing the owner taught: {label: path}."""
        out = {}
        for t in self.known:
            k = _key(t["label"])
            mine = sorted(f for f in self.dir.glob(f"{k}-*.jpg") if re.fullmatch(rf"{re.escape(k)}-\d+\.jpg", f.name))
            if mine:
                out[t["label"]] = mine[-1]
        return out

    async def _jev_knows(self, rgb, named):
        """OpenCV knew none of the owner's things here: Jev compares the picture with theirs. A thing it is sure of (KNOWN_TRUST) becomes the
        biggest thing seen (the one being shown, as in remember), or the picture's only thing when OpenCV named nothing."""
        if not self.jev or any(t.get("known") for t in named) or not self.jev.images_on():
            return
        pics = self.pictures()
        if not pics:
            return
        from .jev import KNOWN_TRUST
        label, conf = await self.jev.which_known(rgb, pics)
        if not label or (conf or 0) < KNOWN_TRUST:
            return
        if named:
            max(named, key=lambda t: (t["box"][2] - t["box"][0]) * (t["box"][3] - t["box"][1]))["known"] = label
            named.sort(key=lambda t: (not t.get("known"), -t["score"]))
        else:
            h, w = rgb.shape[:2]
            named.append({"label": "thing", "score": conf, "box": [0, 0, w, h], "known": label, "similarity": None, "by": "jev"})

    def _learn_views(self, rgb):
        h, w = rgb.shape[:2]
        found = [t for t in self.detect(rgb) if t["label"] != "person" and min(t["box"][2] - t["box"][0], t["box"][3] - t["box"][1]) >= MIN_SIDE]
        if found:  # the biggest thing in the picture is what is being shown
            t = max(found, key=lambda x: (x["box"][2] - x["box"][0]) * (x["box"][3] - x["box"][1]))
            box, kind = t["box"], t["label"]
        else:
            side = int(min(h, w) * 0.6)
            box, kind = [(w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2], None
        crop = rgb[box[1]:box[3], box[0]:box[2]]
        v1, p = self.features(crop)
        v2, _ = self.features(crop[:, ::-1].copy())  # mirrored: the same thing held the other way round
        if kind is None and float(p.max()) >= NAME_SURE:
            kind = self._names[int(p.argmax())]
        return v1, v2, box, kind

    def forget(self, label):
        before = len(self.known)
        self.known = [t for t in self.known if _key(t["label"]) != _key(label)]
        self._save()
        return before != len(self.known)

    # -- the live view ---------------------------------------------------------------------------------------------------------
    async def frames(self):
        """The camera as JPEG frames with what it sees boxed (served as MJPEG by chrome.mjpeg). Runs only while someone watches."""
        import cv2
        self.viewers += 1
        boxes, seen_at = [], 0.0
        try:
            while True:
                try:
                    bgr = await asyncio.to_thread(self.grab)
                except VisionError as e:
                    self.error = str(e)
                    return
                if time.monotonic() - seen_at >= DETECT_EVERY_S:
                    named, people = await asyncio.to_thread(self.see, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                    boxes, seen_at = named + people, time.monotonic()
                yield await asyncio.to_thread(self._draw, bgr, boxes)
                await asyncio.sleep(1 / STREAM_FPS)
        finally:
            self.viewers -= 1
            self._close_soon()

    @staticmethod
    def _draw(bgr, boxes):
        import cv2
        img = bgr.copy()
        for t in boxes:
            x0, y0, x1, y1 = t["box"]
            text = yours(t["known"]) if t.get("known") else SAY.get(t["label"], t["label"]) + f" {round(t['score'] * 100)}%"
            color = (150, 230, 120) if t.get("known") else (200, 200, 200) if t["label"] == "person" else (235, 200, 60)
            cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
            cv2.rectangle(img, (x0, max(0, y0 - th - 10)), (x0 + tw + 10, y0), color, -1)
            cv2.putText(img, text, (x0 + 5, max(th + 2, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 1, cv2.LINE_AA)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, LIVE_JPEG])
        return jpg.tobytes() if ok else b""

    # -- the store -------------------------------------------------------------------------------------------------------------
    def _load(self):
        try:
            return json.loads((self.dir / "known.json").read_text(encoding="utf-8")).get("things") or []
        except (OSError, ValueError):
            return []

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "known.json.tmp"
        tmp.write_text(json.dumps({"things": self.known}), encoding="utf-8")
        tmp.replace(self.dir / "known.json")

    def _keep_picture(self, bgr, box, label):
        import cv2
        crop = bgr[box[1]:box[3], box[0]:box[2]]
        self.dir.mkdir(parents=True, exist_ok=True)
        ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            (self.dir / f"{_key(label)}-{int(time.time())}.jpg").write_bytes(jpg.tobytes())


if __name__ == "__main__":  # python -m core.vision [--live]: offline checks; --live looks through the camera once (its light comes on)
    import sys
    import tempfile
    assert yours("my mug") == "your mug" and yours("Rahul's charger") == "Rahul's charger" and yours("mug") == "the mug" and yours("our TV") == "your TV"
    assert _a("apple") == "an apple" and _a("cell phone") == "a phone" and _a("tv") == "a TV"
    t = lambda label, score, **k: {"label": label, "score": score, "box": [0, 0, 50, 50], **k}  # noqa: E731
    assert say([t("cup", 0.9, known="my mug"), t("cell phone", 0.8)], []) == "This is your mug, and this is a phone."
    assert say([t("cup", 0.5)], []) == "This is probably a cup."
    assert say([], [t("person", 0.9)]).startswith("I can see you, but nothing")
    assert say([t(x, 0.9) for x in ("cup", "laptop", "mouse", "book")], []) == "This is a cup, and this is a laptop. I can also see a mouse and a book."
    assert say([t("remote control", 0.3, by="namer")], []) == "This is probably a remote control."
    # words on what is held up (2026-10-20): a book's cover (its title the tallest line), a letter by its words, a page; too little isn't read
    L = lambda text, h, y=0: {"text": text, "x": 0, "y": y, "w": 10 * len(text), "h": h}  # noqa: E731
    assert reading([L("ATOMIC HABITS", 60), L("James Clear", 28, 70), L("An Easy & Proven Way to Build Good Habits", 18, 100)], []) == (
        "a book", "its cover reads “ATOMIC HABITS”, then “James Clear An Easy & Proven Way to Build Good Habits”",
        "ATOMIC HABITS\nJames Clear\nAn Easy & Proven Way to Build Good Habits")
    assert reading([L("Dear Bhavye,", 14), L("Your statement for October is attached.", 14, 20), L("Kind regards, HDFC Bank", 14, 40)], [])[:2] == (
        "a letter", "it says: “Dear Bhavye, Your statement for October is attached. Kind regards, HDFC Bank”")
    assert reading([L("EXIT", 40)], []) == (None, None, "") and reading([L("ok", 10), L("12", 10)], []) == (None, None, ""), "too little to read"
    assert reading([L("ATOMIC", 60), L("HABITS", 58, 70), L("James Clear", 28, 140)], [])[1] == "its cover reads “ATOMIC HABITS”, then “James Clear”", \
        "a title set over two lines is one title"
    assert reading([L("Hello there world", 14), L("line two here", 14, 20)], [t("book", 0.8)])[0] == "a book", "YOLOX saw a book"
    kind, said, _ = reading([L(f"word{i} other words", 14, i * 20) for i in range(30)], [])
    assert kind == "a page of text" and said.endswith("…”") and len(said.split()) <= READ_SAID_WORDS + 3, (kind, said)
    if sys.platform == "win32":  # Windows' own OCR on a rendered cover: the words come back, and they make a book
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1280, 720), "white")
        draw = ImageDraw.Draw(img)
        try:
            f1, f2 = ImageFont.truetype("arial.ttf", 84), ImageFont.truetype("arial.ttf", 40)
        except OSError:
            f1 = f2 = ImageFont.load_default()
        draw.text((80, 180), "ATOMIC HABITS", fill="black", font=f1)
        draw.text((80, 330), "James Clear", fill="black", font=f2)
        lines = asyncio.run(read_text(np.array(img)))
        got = " ".join(ln["text"] for ln in lines).lower()
        assert "atomic" in got and "clear" in got and reading(lines, [])[0] == "a book", (got, reading(lines, []))
        print("ocr on a rendered cover:", reading(lines, [])[1])
    with tempfile.TemporaryDirectory() as d:
        import numpy as np
        e = Eyes(d)
        a, b = np.zeros(1280, np.float32), np.zeros(1280, np.float32)
        a[0], b[1] = 1, 1
        e.known = [{"label": "my mug", "views": [a.tolist()]}]
        e._save()
        assert Eyes(d).known[0]["label"] == "my mug", "kept across a restart"
        assert e.match(a) == ("my mug", 1.0) and e.match(b)[0] is None, "the same thing matches, another doesn't"
        assert e.forget("My Mug") and not Eyes(d).known

        class FakeJev:  # Jev's pictures: a second look only when OpenCV knew none of the owner's things, and only from KNOWN_TRUST
            def __init__(self, answer):
                self.answer, self.asked = answer, []

            def images_on(self):
                return True

            async def which_known(self, img, known):
                self.asked.append(sorted(known))
                return self.answer
        e.known = [{"label": "my mug", "views": [a.tolist()]}, {"label": "my mug cup", "views": []}]
        (Path(d) / "vision" / "my-mug-100.jpg").write_bytes(b"x")
        (Path(d) / "vision" / "my-mug-200.jpg").write_bytes(b"x")
        (Path(d) / "vision" / "my-mug-cup-300.jpg").write_bytes(b"x")
        assert e.pictures() == {"my mug": Path(d) / "vision" / "my-mug-200.jpg", "my mug cup": Path(d) / "vision" / "my-mug-cup-300.jpg"}
        rgb = np.zeros((100, 200, 3), np.uint8)
        cup, phone = t("cup", 0.9, box=[0, 0, 80, 80]), t("cell phone", 0.95, box=[0, 0, 20, 20])
        e.jev = FakeJev(("my mug", 0.82))
        asyncio.run(e._jev_knows(rgb, named := [phone, cup]))
        assert named[0] is cup and cup["known"] == "my mug" and say(named, []) == "This is your mug, and this is a phone.", named
        e.jev = FakeJev(("my mug", 0.5))
        asyncio.run(e._jev_knows(rgb, named := [t("cup", 0.9)]))
        assert not named[0].get("known"), "not sure enough: not said as theirs"
        e.jev = FakeJev(("my mug", 0.9))
        asyncio.run(e._jev_knows(rgb, named := [t("cup", 0.9, known="my mug cup")]))
        assert not e.jev.asked, "OpenCV already knew it: Jev isn't asked"
        asyncio.run(e._jev_knows(rgb, named := []))
        assert say(named, []) == "This is your mug." and e.jev.asked == [["my mug", "my mug cup"]]
        e.known = []
        # 2026-10-20: what is held up leads (the biggest thing that isn't the room); the owner's face isn't guessed at; a model that can see is
        # asked only when OpenCV isn't sure; a black picture is said as black (a simulated camera: the owner's isn't opened)
        held, room = t("cup", 0.62, box=[0, 0, 60, 60]), t("couch", 0.95, box=[0, 0, 400, 300])
        named = sorted([room, held], key=lambda x: (not x.get("known"), x["label"] in BACKGROUND, -(x["box"][2] - x["box"][0]) * (x["box"][3] - x["box"][1])))
        assert named[0] is held and say(named, []) == "This is a cup, and this is a couch.", named

        class FakeCap:
            def __init__(self, level):
                self.level = level

            def read(self):
                return True, np.full((72, 128, 3), self.level, np.uint8)

            def release(self):
                pass
        globals()["DARK_WAIT_S"] = 0.2
        e.cap, e.opened_at = FakeCap(3), 0.0
        try:
            e.grab(look=True)
            raise AssertionError("a black picture was taken as a look")
        except VisionError as err:
            assert "black" in str(err)
        assert e.grab().mean() == 3, "the live view takes what comes"
        e.cap = FakeCap(90)
        assert e.grab(look=True).mean() == 90
        e.cap = None
        asked = []

        async def cloud(rgb):
            asked.append(rgb.shape)
            return "a black USB charger", "gemma4:31b-cloud"
        e.cloud = cloud
        e.see = lambda rgb: ([t("cup", 0.5)], [t("person", 0.9)])  # (OpenCV unsure: "probably a cup")
        e._picture = lambda image: (None, np.zeros((72, 128, 3), np.uint8))
        r = asyncio.run(e.look(image="x.jpg"))
        assert asked and r["text"] == "It looks like a black USB charger. I can also see you.", r
        e.see = lambda rgb: ([t("cup", 0.9)], [])  # sure: not asked
        asked.clear()
        assert asyncio.run(e.look(image="x.jpg"))["text"] == "In the photo, this is a cup." and not asked
        del e.see, e._picture
        e.cloud = None
        ok, why = e.ready()
        if ok:  # the models are here: both nets run on a plain picture, and a plain picture names nothing for sure
            grey = np.full((480, 640, 3), 128, np.uint8)
            assert e.detect(grey) == [] and e.features(grey)[0].shape == (1280,)
            named, people = e.see(grey)
            assert not people and all(x["score"] < NAME_SURE for x in named), named
            f = Path(d) / "grey.jpg"
            import cv2
            cv2.imencode(".jpg", grey)[1].tofile(str(f))
            r = asyncio.run(e.look(image=f))
            assert r["ok"] and r["text"].startswith(("In the photo", "I can't make out")), r
            r = asyncio.run(e.remember("my test card", image=f))
            assert r["ok"] and r["verified"] and Eyes(d).known[0]["label"] == "my test card", r
            r2 = asyncio.run(e.look(image=f))
            assert "your test card" in r2["text"], ("the same picture is the thing it was taught", r2)
        else:
            print("vision self-check: models not here,", why)
    if "--live" in sys.argv:
        e = Eyes(tempfile.mkdtemp())
        r = asyncio.run(e.look())
        e.release()
        print("live look:", r["text"], [(x["label"], x["score"]) for x in r.get("things") or []])
    print("vision self-check: OK")
