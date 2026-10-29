"""The notch's and the pet's rules, shared by every OS: the windows are Qt's (core/qtnotch.py: the notch, the desk pet; core/qtkit.py
PetArt draws the pets). The WinForms notch that lived here was retired on 2026-10-19 (the owner: "yes, retire it"); its rules stayed:

- State: a view over Monday's real state, from Core's events only (a task's real step, a real approval, the ear's own events), never a
  made-up progress or success; STATES gives each state its pet mood, title and dot.
- Dropped files: examine (a file, not a folder; Core's 100 MB limit; never run) and only what Monday can really do with its kind
  (KIND_ACTIONS); each action is a command with the file attached, so Jev routes it like anything else.
- The pet: prefs (each choice checked), PETS and Dotpals' builder parts (DP_*); its sounds (tone_wav); the motion (Spring, CLOSE_CURVE).
- Words: perm_words, ask_words, chat_turns, who_menu, agent_places, mail_draft_parse.
- One of the owner's agents in detail (Home's agent view, after Dotpals; the retired notch had it): code_card (the last edit), meter_rows
  (the plan / context / limits bars, a bar only where Core says how far), agent_actions (Next agent, Story, Rewind…, Steps),
  steps_view (Dotpals' filters and Files), State.next_agent, say_words (Write to), rewind_words / rewind_button (Rewind…).

    python -m core.notch --check     self-check: prefs, file kinds, the tones, the motion, the state reducer, the words
"""
import math
import os
import re
import time
from pathlib import Path

OPEN_W = 640                 # the open notch's width: the owner's reference (2026-10-17, 797 px wide at 125 %)
PAD = 10                     # the black shell around the cards
CARD_R = 14                  # a card's corner radius
AUTOCLOSE = (10, 15, 30)     # an open notch the mouse has left closes after this many seconds (Settings), unless something needs it open
SPRING = (0.5, 0.72)         # opening and growing: Coucou's spring (its anim.ts, MIT: SwiftUI's response 0.5 s, damping 0.72)
CLOSE_MS = 340               # closing and shrinking: its 340 ms curve, cubic-bezier(.45, 0, .2, 1), no overshoot
CORNER = (14, 22)            # the outline's bottom corners, closed and open (its ROUNDED_CORNER, EXPANDED_CORNER)
DASH_S = 1.1                 # the drop zone's dashes marching round it (its dash-march)
HOVER_S = 0.6                # the mouse resting on the closed notch this long opens it (a peek); a click opens Monday's window
CLICK_HOLD_S = 0.3           # a press let go sooner is a click, however its pointer drifted on the way (a touchpad's press moves it 10-30 px)...
FLING_PX = 48                # ...unless it went this far at once (a quick drag that is meant: dragging)
RESULT_S = 12.0              # a finished task stays on show this long
WATCH_DONE_S = 60.0          # an agent the owner runs themselves: its "done, in plain words" stays this long on the closed notch (core/watch.py)
WATCH_SHOWN_S = 1800.0       # ...and the opened notch keeps showing it, its last edit as the code card, this long (as long as Core lists it:
#                              watch.RECENT_S; the owner, 2026-10-20: "when the IDE changes the code I wanna see it in the notch")
ANSWER_S = 60.0              # an answer to something asked from here: it was asked for, so it stays long enough to read (2026-10-15)
ASKED_S = 90.0               # something asked from here shows "Asked Monday" until its answer comes, at most this long
ANSWER_MAX = 700             # characters of an answer shown in the open notch (the whole of it is in the conversation)
HEARD_S = 1.6                # "hearing you" lasts this long after the ear's last words
POKES, POKE_WINDOW_S, DIZZY_S = 3, 1.6, 2.6   # this many pokes this fast make the pet dizzy, for this long
REACT_S = 0.7                # one poke's reaction (wave, bounce, spin)
EAT_S = 1.6                  # the pet eating a dropped file
UPLOAD_MAX = 100 * 1024 * 1024   # = core/app.py MAX_UPLOAD_BYTES
CHUNK = 256 * 1024
BG, PANEL, LINE, TEXT, MUTED, ACCENT, GOOD, WARN, BAD = ("#0E0F11", "#1A1C20", "#2C2F35", "#ECEEF0", "#A3ACB3", "#55E0F0", "#7EE2B8",
                                                          "#F2B65A", "#FF8A80")
# the reference's look (2026-10-17, sampled from the owner's recording at 125 %): a black shell, near-black cards with a faint edge, one
# indigo glow under the prompt; white words, two quieter greys; a pal's colour for each AI tool (its pill and its little face)
CHAT_KEEP, CHAT_MAX_H, TURN_CHARS = 30, 210, 12000   # the chat in Ask: turns kept, its height at most (the wheel scrolls back), a turn's words
# (TURN_CHARS was 700 until 2026-10-20 night, the owner: "the answer length is fixed and we can't scroll the answer": a reply shows whole, the
#  thread scrolls; only a book-length one is cut)
THREAD_STATES = ("idle", "listening", "asked", "answer", "working", "hearing", "speaking", "asleep", "offline")  # Chat shows Monday's
# chat in these (an approval, a file or a result shows its own card); an agent's chat is on Home's agent view
IDE_COLOURS = {"vscode": "#2F9BEF", "cursor": "#C9CED6", "kiro": "#9D6BFF", "antigravity": "#F2667A", "windsurf": "#27C2A0", "claude": "#D97757",
               "codex": "#21C55C", "zed": "#4E8CF7", "trae": "#F2C94C", "void": "#B07CFF"}  # a pill's light under the mouse and while it works
LIVELY = ("TASK_CREATED", "TASK_STARTED", "TASK_STEP", "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED", "PERMISSION_REQUIRED",
          "MESSAGE_CREATED", "WAKE")  # what counts as something happening (a component check every few seconds doesn't)

# -- the pets ------------------------------------------------------------------------------------------------------------------------
PETS = {  # kind -> (what the owner reads, default name, body, accent)
    "witch": ("Witch hat", "Wisp", "#F3F4F7", "#5524A4"),  # the owner's reference mascot (2026-10-17), drawn here: the accent is its hat
    "blob": ("Blob", "Puff", "#F3F5F7", "#FF9EB5"),
    "bot": ("Tiny robot", "Bolt", "#B9C4CF", "#55E0F0"),
    "cat": ("Cat", "Miso", "#F2C18D", "#FF9EB5"),
    "fox": ("Fox", "Ember", "#F08A4B", "#FFF1E3"),
    "gem": ("Geometric", "Prism", "#8F80F2", "#FFFFFF"),
    "maus": ("Maus (a pointer)", "Maus", "#377FE6", "#FFFFFF"),  # OpenMausBot's mascot (2026-10-17): a mouse pointer with a face
    # Dotpals' nine (2026-10-17, its src/characters.js, MIT), drawn from its pal builder's parts (DP_PRESETS), and the owner's own pal
    "blu": ("Blu", "Blu", "#1E88FF", "#FFFFFF"), "hop": ("Hop", "Hop", "#8BE22E", "#FFFFFF"), "sunny": ("Sunny", "Sunny", "#FFC21A", "#FFFFFF"),
    "lovi": ("Lovi", "Lovi", "#FF2FC4", "#FFFFFF"), "muse": ("Muse", "Muse", "#9D6BFF", "#FFFFFF"), "grok": ("Grok", "Grok", "#4C5566", "#FFFFFF"),
    "nova": ("Nova", "Nova", "#FF7A2F", "#FFFFFF"), "byte": ("Byte", "Byte", "#16C6AE", "#FFFFFF"), "webby": ("Webby", "Webby", "#6F5CFF", "#FFFFFF"),
    "pal": ("My own pal (build it)", "My pal", "#FF7A2F", "#FFFFFF"),
}
# Dotpals' pal builder (its src/custom.js, MIT): bodies, eyes and tops in its 200x200 box, scaled by DP_K about (100, DP_Y) into a pet's box.
# A shape: (its words, its outline as SVG path commands M L C Q Z, the head's top, where ears attach, the eyes' (y, spread), the mouth's y,
# the cheeks' distance). The frog's two eye bumps are circles (its SVG draws them with arcs).
DP_K, DP_Y = 0.4, 150.0
DP_SHAPES = {
    "round": ("Round", "M12 262 L12 152 C12 98 52 72 100 72 C148 72 188 98 188 152 L188 262 Z", 72, ((46, 98), (154, 98)), (140, 23), 172, 42),
    "square": ("Boxy", "M14 262 L14 104 C14 72 36 52 68 52 L132 52 C164 52 186 72 186 104 L186 262 Z", 52, ((38, 66), (162, 66)), (124, 26), 164, 46),
    "blob": ("Fluffy", "M16 262 C2 240 0 206 16 190 C0 170 6 138 34 134 C30 108 56 92 82 100 C94 82 132 80 146 100 C172 96 196 118 186 146 "
                       "C204 160 202 196 186 208 C196 226 192 250 186 262 Z", 86, ((50, 108), (158, 106)), (158, 17), 188, 34),
    "tall": ("Pointy", "M12 262 L14 180 C18 120 60 36 100 34 C140 36 182 120 186 180 L188 262 Z", 34, ((70, 70), (130, 70)), (136, 25), 174, 38),
    "heart": ("Heart", "M100 290 C58 254 8 204 8 132 C8 86 38 56 70 56 C86 56 96 64 100 78 C104 64 114 56 130 56 C162 56 192 86 192 132 "
                       "C192 204 142 254 100 290 Z", 70, ((52, 66), (148, 66)), (134, 25), 170, 40),
    "bean": ("Frog", "M16 262 L16 176 C16 132 52 112 100 112 C148 112 184 132 184 176 L184 262 Z", 64, ((40, 80), (160, 80)), (98, 36), 146, 52),
}
DP_EYES = {"dots": "Dots", "round": "Button", "googly": "Googly", "pixel": "Pixel", "visor": "Visor", "shades": "Shades"}
DP_TOPS = {"none": "Nothing", "ears": "Cat ears", "horns": "Horns", "antenna": "Antenna", "sprout": "Sprout", "sparkle": "Sparkle", "bow": "Bow",
           "crown": "Crown", "beret": "Beret"}
DP_PRESETS = {  # Dotpals' characters as its builder's parts (Sunny's round glasses are Monday's own; Webby adds its legs and its thread)
    "blu": ("blob", "dots", "beret"), "hop": ("bean", "googly", "none"), "sunny": ("tall", "dots", "none"), "lovi": ("heart", "shades", "none"),
    "muse": ("tall", "dots", "sparkle"), "grok": ("square", "visor", "none"), "nova": ("round", "round", "antenna"),
    "byte": ("square", "pixel", "ears"), "webby": ("round", "googly", "none"),
}
IDLE = {"bob": "Gentle bob", "breathe": "Breathing", "still": "Still"}
INTERACT = {"wave": "Wave", "bounce": "Bounce", "spin": "Spin", "none": "Nothing"}
TEMPER = {"cheerful": "Cheerful", "calm": "Calm", "sleepy": "Sleepy"}   # the face it rests with
# What the pet wears: Dotpals' accessories, then the wardrobe of the 2026-10-26 recording ("Dress Mochi up", seasonal outfits included); in
# the wardrobe's order. "auto" is the season's (wear_now).
WEAR = {"auto": "Auto (the season's)", "none": "Nothing", "party": "Party hat", "beanie": "Beanie", "crown": "Crown", "sunglasses": "Sunglasses",
        "glasses": "Round glasses", "beret": "A black beret", "scarf": "Scarf", "witch": "Witch hat", "pumpkin": "Pumpkin", "santa": "Santa hat",
        "bunny": "Bunny ears"}
HATS = {"party", "beanie", "crown", "beret", "witch", "santa", "bunny"}   # on its head: in place of a pal's own top; not on the witch's own hat


def wear_now(wear, day=None):
    """What the pet wears today: its choice, or for "auto" the season's (New Year's party hat, Easter's bunny ears, October's witch hat,
    December's Santa hat; else nothing)."""
    if wear != "auto":
        return wear if wear in WEAR else "none"
    import datetime as _d
    day = day or _d.date.today()
    m, d = day.month, day.day
    if (m, d) in ((12, 31), (1, 1)):
        return "party"
    if m == 12:
        return "santa"
    if m == 10 or (m == 11 and d == 1):
        return "witch"
    if (m == 3 and d >= 20) or (m == 4 and d <= 20):
        return "bunny"
    return "none"
PET_DEFAULTS = {"kind": "witch", "name": "Wisp", "body": "#F3F4F7", "accent": "#5524A4", "size": 1.0, "idle": "bob", "interact": "wave",
                "shape": "round", "eyes": "googly", "top": "sprout", "fur": True,  # the owner's own pal (kind "pal"): Dotpals' DEFAULT_CUSTOM
                "temper": "cheerful", "wear": "none", "intensity": 0.7, "sounds": False, "visible": True, "on_start": True,
                "volume": 0.6,   # the notch's tones (Settings' slider): their loudness, 0 to 1; Monday's spoken replies have their own
                "autoclose": 15,  # seconds the open notch stays once the mouse has left it (Settings: one of AUTOCLOSE)
                "notify": True,  # Windows' notifications for what needs the owner when they may not be looking (Dotpals')
                "desk": True,  # the pet on the desktop too, above the taskbar (Dotpals' pal): always there unless the owner puts it away
                "desk_size": 1.0,  # its size there (DESK px at 1.0), set in General only (the owner, 2026-10-16)
                "lined": False,  # the closed bar folded to its line (a swipe up), kept so Monday starts as the owner left it (2026-10-26)
                "pills": None}  # Active pills: the tools shown beside the pet on Home, at most PILLS_MAX keys; None: Monday's own pick
_KEY = re.compile(r"^[a-z0-9_]{1,40}$")
# (the owner, 2026-10-16: "it should always be there, the pet or the notch, one of them; off we do manually")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def dragging(held_s, moved_px, least_px):
    """Is a press, its button still down, a drag yet: has it gone least_px (the OS's drag distance; the bar's fold: FOLD_UP_PX) after
    being down CLICK_HOLD_S, or FLING_PX at once? A click whose pointer drifts as a touchpad is pressed is never one (the owner,
    2026-10-29: "when im clicking on something it thinks like im draging it"), so the notch opens instead of folding or moving."""
    return moved_px >= FLING_PX or (held_s >= CLICK_HOLD_S and moved_px >= least_px)


def prefs(d):
    """The owner's pet and notch choices, every value checked (a hand-edited data/desktop.json can't break the notch). A kind brings its
    own name and colours; a name or colour given with it wins."""
    d = d if isinstance(d, dict) else {}
    out = dict(PET_DEFAULTS)
    if d.get("kind") in PETS:
        out.update(kind=d["kind"], name=PETS[d["kind"]][1], body=PETS[d["kind"]][2], accent=PETS[d["kind"]][3])
    name = re.sub(r"\s+", " ", str(d.get("name") or "")).strip()[:24]
    out["name"] = name or out["name"]
    for k in ("body", "accent"):
        if isinstance(d.get(k), str) and _HEX.match(d[k]):
            out[k] = d[k].upper()
    for k, lo, hi in (("size", 0.7, 1.4), ("intensity", 0.0, 1.0), ("desk_size", 0.5, 2.5), ("volume", 0.0, 1.0)):
        try:
            out[k] = round(min(hi, max(lo, float(d[k]))), 2)
        except (KeyError, TypeError, ValueError):
            pass
    if d.get("autoclose") in AUTOCLOSE and not isinstance(d.get("autoclose"), bool):
        out["autoclose"] = int(d["autoclose"])
    for k, allowed in (("idle", IDLE), ("interact", INTERACT), ("temper", TEMPER), ("wear", WEAR), ("shape", DP_SHAPES), ("eyes", DP_EYES),
                       ("top", DP_TOPS)):
        if d.get(k) in allowed:
            out[k] = d[k]
    for k in ("sounds", "visible", "on_start", "desk", "fur", "notify", "lined"):
        if isinstance(d.get(k), bool):
            out[k] = d[k]
    if isinstance(d.get("pills"), list):  # each key once, in the owner's order, at most PILLS_MAX
        out["pills"] = list(dict.fromkeys(k for k in d["pills"] if isinstance(k, str) and _KEY.match(k)))[:PILLS_MAX]
    return out


# -- Active pills (the recording's 15–16, the owner's choice 2026-10-20): the agents and tools beside the pet, each as Core last found it ----
PILLS_MAX = 8  # the owner may keep this many on (2026-10-20 night, "we can use multiple"; 4 until then); the closed bar shows the first four
PILL_COLOURS = {**IDE_COLOURS, "claude_code": IDE_COLOURS["claude"], "claude_app": IDE_COLOURS["claude"], "gemini_cli": "#4AA3F0",
                "kilo": "#F5C542", "grok_build": "#9AA0A6", "hermes": "#8B6CF0", "ollama": "#F6F7F9", "copilot_cli": "#3DDC84",
                "freebuff": "#E865C0"}
PILL_WINDOW = {"claude_app": "claude"}  # a tool whose open window the dock shows under another key (it isn't shown twice)
PILL_STATES = {"available": ("ok", "ready"), "not_authenticated": ("wait", "not signed in"), "detected_unsupported": ("off", "opens only"),
               "unavailable": ("off", "not installed")}  # /api/ai-tools' status -> the pill's dot and words; anything else is N/A
PILL_KINDS = ("models", "editor", "app", "agent")  # what may be a pill: no command-line coding agents (the owner, 2026-10-20: "don't use
# the CLIs, remove them" from the notch's pills) and no computer agent (the same evening: Hermes isn't named in the UI; it is Monday's agent,
# not a tool of the owner's to open); the editors, the apps, Ollama
AUTO_PILLS = ("ollama", "kiro", "antigravity", "cursor", "vscode", "claude_app", "chatgpt")  # Monday's pick, in this order


def pill_ok(tool):
    """One of /api/ai-tools' tools may be a pill (not a CLI)."""
    return str((tool or {}).get("kind") or "") in PILL_KINDS


def pill_state(tool):
    """(dot, words) for a pill: what Core last said about that tool, in plain words."""
    return PILL_STATES.get(str((tool or {}).get("status")), ("off", "N/A"))


AUTO_MAX = 4  # Monday's own pick stays four (the closed bar's two by two); the owner may keep up to PILLS_MAX on


def auto_pills(tools):
    """The owner hasn't chosen: the agents of Monday's that Core found installed, the ready ones first (AUTO_PILLS' order), at most
    AUTO_MAX. tools: /api/ai-tools by key."""
    have = [k for k in AUTO_PILLS if (tools.get(k) or {}).get("installed") is True and pill_ok(tools[k])]
    return sorted(have, key=lambda k: tools[k].get("status") != "available")[:AUTO_MAX]


def dock_pills(chosen, tools, places):
    """The pills after the dock's open windows (places, each with its app's key): the owner's that Core knows, or Monday's pick
    (auto_pills) in the slots the windows left; each {name, pill, state}, minus a tool whose window is already there."""
    open_keys = {str(pl.get("app") or pl.get("key") or "").lower() for pl in places}
    keys = [k for k in (auto_pills(tools) if chosen is None else chosen) if k in tools and pill_ok(tools[k])
            and PILL_WINDOW.get(k, k) not in open_keys]
    if chosen is None:
        keys = keys[:max(0, PILLS_MAX - len(places))]
    return [{"name": tools[k].get("name") or k, "pill": k, "state": pill_state(tools[k]), "exe": tools[k].get("exe")} for k in keys]  # exe: its logo


# -- dropped files: what each kind can really become ------------------------------------------------------------------------------
KINDS = {
    "text": ("txt", "md", "markdown", "csv", "tsv", "json", "log", "xml", "yaml", "yml", "ini", "toml", "html", "htm"),
    "doc": ("pdf", "docx", "pptx", "xlsx"),                            # core/files.py text_of reads these (a PDF through pypdf)
    "code": ("py", "js", "mjs", "ts", "tsx", "jsx", "java", "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "kt", "swift", "sql",
             "sh", "ps1", "bat", "cmd", "css", "scss", "vue", "svelte", "dart", "lua", "r", "scala"),
    "image": ("png", "jpg", "jpeg", "gif", "bmp", "webp"),             # Monday's eyes (core/vision.py) look at a picture sent with "what is this"
    "audio": ("wav", "mp3", "m4a", "flac", "ogg"),                     # "use this recording as your voice" (core/speech.py Custom)
}
# Opening a file with Windows' default app must never run it: scripts and programs are only ever read (the owner's brief: "never
# execute a dropped file automatically").
RUNS = {"exe", "msi", "msix", "appx", "bat", "cmd", "com", "scr", "pif", "ps1", "psm1", "vbs", "vbe", "js", "jse", "mjs", "wsf", "wsh",
        "hta", "cpl", "jar", "lnk", "url", "reg", "inf", "scf", "msc", "application", "gadget", "sh", "py", "pyw", "rb", "pl", "php"}
ACTIONS = {  # action -> (button, the command sent with the file attached; {name} is the file's)
    "summarize": ("Summarize", "Summarize the attached file “{name}” in a few plain sentences."),
    "ask": ("Ask about it", None),                         # the question box: the owner's own words, with the file
    "facts": ("Pull out the facts", "List the key facts in the attached file “{name}”: names, dates, numbers, decisions and to-dos."),
    "review": ("Review the code", "Review the code in the attached file “{name}”: real bugs and risks first, then what to fix."),
    "explain": ("Explain it", "Explain what the code in the attached file “{name}” does, in plain words."),
    "look": ("What's in it?", "What is in this picture?"),
    "screen": ("What's this?", "Explain what I circled on my screen, in plain words."),  # a part of the screen circled (Notch.circle)
    "voice": ("Use as my voice", "Use this recording as your voice."),
    "email": ("Send by email", None),                      # Mochi's card: To, Subject, the message; the owner's mail app sends it
    "open": ("Open", None),                                # Windows' default app (never for something that runs)
    "chat": ("Continue in chat", None),                    # the conversation, with the file attached to the next message
}
KIND_ACTIONS = {"text": ("summarize", "ask", "facts", "email", "open", "chat"), "doc": ("summarize", "ask", "facts", "email", "open", "chat"),
                "code": ("review", "explain", "ask", "chat"), "image": ("look", "ask", "email", "open", "chat"),
                "audio": ("voice", "email", "open", "chat"), "other": ("chat", "email", "open")}
CIRCLE_ACTIONS = ("screen", "ask", "chat")  # what a circled part of the screen can become (Dotpals' circle and ask)
KIND_WORDS = {"text": "text", "doc": "document", "code": "code", "image": "picture", "audio": "recording", "other": "file"}
# the drop zone's chips, the reference's four: each a kind Monday reads (KINDS: a PDF, pictures, code, Office and text documents); a
# recording or any other file still drops (it can go to the chat or an email)
DROP_CHIPS = ("PDF", "Images", "Code", "Docs")


def file_kind(path):
    ext = Path(path).suffix.lower().lstrip(".")
    return next((k for k, exts in KINDS.items() if ext in exts), "other")


def examine(path):
    """A dropped path, checked before anything is copied: {"ok", "why", "name", "path", "size", "kind", "actions", "runs"}. Nothing in it
    is opened or run."""
    p = Path(str(path))
    base = {"ok": False, "name": p.name, "path": str(p), "size": None, "kind": None, "actions": (), "runs": False}
    try:
        if p.is_dir():
            return {**base, "why": f"{p.name} is a folder; drop a file."}
        if not p.is_file():
            return {**base, "why": f"{p.name} isn't a file Monday can reach."}
        size = p.stat().st_size
    except OSError as e:
        return {**base, "why": f"Windows didn't let Monday read {p.name} ({e.strerror or e})."}
    if size > UPLOAD_MAX:
        return {**base, "size": size, "why": f"{p.name} is {size / 2**20:.0f} MB; Monday takes files up to {UPLOAD_MAX // 2**20} MB."}
    kind, runs = file_kind(p), p.suffix.lower().lstrip(".") in RUNS
    return {**base, "ok": True, "why": None, "size": size, "kind": kind, "runs": runs,
            "actions": tuple(a for a in KIND_ACTIONS[kind] if not (runs and a in ("open", "email")))}  # a program isn't mailed from here


def size_words(n):
    return f"{n} bytes" if n < 1024 else f"{n / 1024:.0f} KB" if n < 2**20 else f"{n / 2**20:.1f} MB"


def mail_draft_parse(text, name):
    """Monday's draft for the card (POST /api/mail/draft) -> (subject, message): its "Subject:" line, then the rest, with Windows' line
    breaks (a TextBox shows a bare \\n as nothing); the file's name stays the subject when the model wrote no such line."""
    t = (text or "").strip()
    m = re.match(r"\**subject\**\s*:\s*(.+?)\s*(?:\n|$)(.*)", t, re.I | re.S)
    subject, body = (m.group(1).strip(" *"), m.group(2).strip()) if m else (name, t)
    return subject[:200] or name, "\r\n".join(body.splitlines())


# -- what the notch shows: a pure reduction of Core's events ------------------------------------------------------------------------
STATES = {  # state -> (pet mood, title, the dot's colour)
    "offline": ("offline", "Offline", MUTED), "waiting": ("waiting", "Needs your OK", WARN), "file": ("eating", "Got your file", ACCENT),
    "hearing": ("hearing", "Hearing you", ACCENT), "working": ("working", "Working", WARN), "asked": ("working", "Asked Monday", WARN),
    "speaking": ("speaking", "Speaking", ACCENT), "answer": ("happy", "Answer", GOOD), "done": ("happy", "Done", GOOD),
    # a coding task (2026-10-17, core/evidence.py): happy only when the tests passed after its change; done without that evidence is flat
    "verified": ("happy", "Verified", GOOD), "unverified": ("flat", "Done, not verified", WARN),
    "failed": ("sad", "Failed", BAD), "cancelled": ("flat", "Stopped", MUTED), "dizzy": ("dizzy", "Dizzy", ACCENT),
    "asleep": ("sleepy", "Asleep", MUTED), "listening": ("listening", "Listening", ACCENT), "idle": ("idle", "All quiet", GOOD),
    # Dotpals' (2026-10-17): an alert worth a look (a request going in circles, two agents on one file), and the day's first brief
    "alert": ("waiting", "Worth a look", WARN), "brief": ("happy", "Good morning", ACCENT),
}
ALERT_S, BRIEF_S = 12.0, 10.0  # how long an alert and the morning brief stay in the notch
NOTIFY_WAIT_S = 20.0           # an approval still waiting this long is also a Windows notification (at once while the notch is hidden)
LONG_TASK_S = 60.0             # a task that took this long says it finished in a notification too
# Dotpals' sounds (its bridge/index.html tone()): (Hz, ms) in turn, 0 Hz a pause. Played on a thread: winsound.Beep blocks
TONES = {"prompt": ((620, 60),), "saved": ((1320, 25),), "needs": ((880, 90), (0, 80), (880, 90)), "done": ((784, 90), (1047, 140)),
         "error": ((230, 80), (185, 80), (140, 120))}


TONE_RATE, TONE_PEAK = 22050, 0.5   # the tones' samples a second, and their loudest (of full scale) at volume 1


def tone_wav(tone, volume):
    """One of Dotpals' tones as a WAV in memory, at `volume` (0 to 1) of TONE_PEAK: sine notes faded in and out over a few ms (no
    clicks), a 0 Hz note a pause."""
    import io
    import wave
    from array import array
    amp, fade, samples = TONE_PEAK * 32767 * min(1.0, max(0.0, float(volume))), max(1, int(TONE_RATE * 0.006)), array("h")
    for hz, ms in TONES[tone]:
        n = int(TONE_RATE * ms / 1000)
        step = 2 * math.pi * hz / TONE_RATE
        samples.extend(int(amp * min(1.0, i / fade, (n - 1 - i) / fade) * math.sin(step * i)) if hz else 0 for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TONE_RATE)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def perm_words(p):
    title = p.get("title") or "Approve this action?"
    if " wants to " in title:
        who, what = title.split(" wants to ", 1)
        return f"Allow {who} to {what.rstrip('.?')}?"
    return title


def ask_words(a, wall=None, keys=True):
    """One of the owner's agents asking (core/approve_hook.py), in words, with the time it stays here: the hook's wait, counted down
    (Dotpals' "29s"); after it, only Claude Code's own prompt. keys: the allow and deny keys as Monday holds them on this keyboard, a
    (allow, deny) pair of key names (True: the old Ctrl+Alt+Y / N); False or empty: not offered (2026-10-20: where they'd type a character)."""
    left = max(0, int((a.get("until") or 0) - (wall if wall is not None else time.time())))
    allow, deny = keys if isinstance(keys, (tuple, list)) else ("Ctrl+Alt+Y", "Ctrl+Alt+N")
    return f"{a.get('agent') or 'An agent'} wants to {a.get('what') or 'act'}:\n" + (f"{a['detail']}\n" if a.get("detail") else "") + \
        (f"Worth a second look: {' · '.join(a['flags'])}\n" if a.get("flags") else "") + \
        (f"In {a['project']}. " if a.get("project") else "") + \
        (f"{allow} allows, {deny} denies, from any app. Claude Code's own prompt still works too." if keys and allow and deny else
         "Answer here, or in Claude Code's own prompt.") + \
        (f"\n{left // 60}:{left % 60:02d} left here, then answer in Claude Code." if a.get("until") else "")


def breakable(text, every=24):
    """A run with no space in it (a path, a link) can wrap: a zero-width space every `every` characters inside it, so a label never
    grows wider than the notch (the owner's 2026-10-20 notch cut a reply at its edge)."""
    return re.sub(r"\S{%d,}" % (every + 1), lambda m: "​".join(m.group(0)[i:i + every] for i in range(0, len(m.group(0)), every)),
                  str(text or ""))


def folder_words(cwd):
    """A session's folder as the notch says it: its last two parts ("monday › app" for E:\\monday\\app; the owner, 2026-10-20 night: "a folder
    name in it too, so I'll know which folder we are working on"), "" without one."""
    parts = [p for p in re.split(r"[\\/]+", str(cwd or "").strip()) if p and not re.fullmatch(r"[A-Za-z]:", p)]
    return " › ".join(parts[-2:])


def _line(text, n=220):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
MD_RULE = re.compile(r"\s*(?:[-*_]\s*){3,}")
MD_TABLE_RULE = re.compile(r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*")


def _plain(text):
    """Markdown as plain words for the notch, which has no rich text (the owner's screenshot, 2026-10-17): [words](link) -> words,
    **bold**, *it* and `code` -> their words, headings and quotes without their marks, list items as "• " on their own lines, a table's
    rows as "a · b", code fences dropped (their lines kept), blank runs as one."""
    out, fence = [], False
    for ln in str(text or "").replace("\r\n", "\n").split("\n"):
        s = ln.rstrip()
        if s.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            if MD_RULE.fullmatch(s) or MD_TABLE_RULE.fullmatch(s):
                continue
            s = re.sub(r"^\s{0,3}#{1,6}\s+", "", s)
            s = re.sub(r"^\s{0,3}>\s?", "", s)
            s = re.sub(r"^(\s*)[-*+]\s+(?:\[[ xX]\]\s+)?", lambda m: m.group(1) + "• ", s)
            if s.strip().startswith("|") and s.strip().endswith("|"):
                s = " · ".join(c.strip() for c in s.strip().strip("|").split("|"))
            s = MD_LINK.sub(lambda m: m.group(1) or m.group(2), s)
            s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
            s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
            s = re.sub(r"`([^`]+)`", r"\1", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _head(text, n):
    """A long turn's first n characters or so, its lines kept, then "…"."""
    t = str(text or "").strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


class State:
    """Monday's state as the notch shows it, from Core's events only (feed), and what to show now (view). Pure: no window."""

    def __init__(self):
        self.online, self.mic, self.speaking, self.asleep = None, False, False, False
        self.tasks, self.steps, self.perms = {}, {}, {}
        self.result = None          # (state, title, text, at, keep_s): the last task's end, an answer, the pet's own line
        self.heard_at, self.heard = float("-inf"), ""
        self.file = None            # a dropped file's flow: {"phase": copying | ready | failed, ...}
        self.asked, self.asked_text = set(), None   # message ids sent from here (their answers show here); (text, at) until one comes
        self.watch = []             # the owner's own coding agents' sessions (AGENT_WATCH, core/watch.py): Dotpals' view of them
        self.watching = None        # the one of them the last view() showed (its Story asks Core by its key), or None
        self.pick = None            # the key of the one the owner moved to (Next agent): shown while it is live
        self.asks = {}              # their permission requests waiting on the owner (AGENT_ASK; core/approve_hook.py), by id
        self.agent_result = None    # an agent's own line (Dotpals' alert, a standing yes): the closed notch and the agent view, never Chat
        self.agent = None           # the session Home's agent view shows (agent_view), apart from `watching` (the closed notch's)
        self.last = time.monotonic()

    def feed(self, ev, now=None):
        now = now if now is not None else time.monotonic()
        k, d = ev.get("type"), ev.get("data") or {}
        if k in LIVELY:
            self.last = now
        if k in ("TASK_CREATED", "TASK_STARTED", "TASK_PROGRESS", "TASK_TESTS", "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED") and d.get("task"):
            t = d["task"]
            if t.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                st = {"COMPLETED": "done", "FAILED": "failed", "CANCELLED": "cancelled"}[t["status"]]
                tests = t.get("tests") if isinstance(t.get("tests"), dict) and t["tests"].get("changed") else None
                text = (t.get("error") if st != "done" else t.get("result")) or t.get("title") or ""
                if st == "done" and tests:  # code changed: its tests say whether it's done for sure (core/evidence.py), never the agent's word
                    st, text = ("verified" if tests.get("state") == "passing" else "unverified"), f"{tests.get('line') or ''}\n{text}".strip()
                self.result = (st, None, text, now, RESULT_S)
                self.tasks.pop(t["id"], None)
                self.steps.pop(t["id"], None)
            else:
                self.tasks[t["id"]] = t
        elif k == "TASK_STEP" and ev.get("task_id") and d.get("step"):
            self.steps[ev["task_id"]] = d["step"]
        elif k in ("PERMISSION_REQUIRED", "PERMISSION_GRANTED", "PERMISSION_DENIED") and d.get("permission"):
            p = d["permission"]
            if p.get("status") == "PENDING":
                self.perms[p["id"]] = p
            else:
                self.perms.pop(p["id"], None)
        elif k == "MESSAGE_CREATED" and d.get("message"):
            m = d["message"]
            if m.get("role") == "monday" and (m.get("meta") or {}).get("reply_to") in self.asked \
                    and m.get("kind") in ("text", "error", "completion"):
                self.result = ("failed" if m.get("kind") == "error" else "answer", None, said(m), now, ANSWER_S)
                self.asked_text = None
        elif k == "SLEEP":
            self.asleep = True
        elif k == "WAKE":
            self.asleep = False
        elif k == "AGENT_ASK" and (d.get("ask") or {}).get("id"):
            self.asks[d["ask"]["id"]] = d["ask"]
        elif k == "AGENT_ALERT" and d.get("text"):  # Dotpals' alerts: going in circles, two agents on one file
            who = f"{d.get('agent') or 'An agent'}" + (f" · {d['project']}" if d.get("project") else "")
            self.agent_result = ("alert", f"{who} may be stuck" if d.get("kind") == "stuck" else "Two agents, one file", d["text"], now, ALERT_S)
        elif k == "AGENT_ASK_DONE":
            self.asks.pop(d.get("id"), None)
            if d.get("by"):  # answered by the owner's own standing yes (Settings → Approvals): shown, never silent
                self.agent_result = ("done", f"Allowed by your {'Always allow' if d['by'] == 'always allow' else 'auto mode'}", d.get("words") or "",
                                     now, RESULT_S)
        elif k == "AGENT_WATCH":
            self.watch = d.get("sessions") or []

    def live(self, wall=None, keep=WATCH_SHOWN_S):
        """The owner's own agents' sessions the notch can show: the working ones, then those that finished within `keep` seconds
        (WATCH_SHOWN_S: the opened notch, where the last edit stays on show; WATCH_DONE_S: the closed notch's own line, `view`)."""
        wall = wall if wall is not None else time.time()
        return [s for s in self.watch if s.get("state") == "working"] + \
            [s for s in self.watch if s.get("state") == "done" and s.get("done_at") and wall - s["done_at"] < keep]

    def watched(self, wall=None, keep=WATCH_SHOWN_S):
        """("working" | "done", session) for the owner's own agent the notch shows now (Monday's own work comes first), or None: the one
        the owner moved to while it is live, else the first working one, else one that finished (with its code card, while Core lists it;
        the closed notch's line keeps it for a minute, `keep`=WATCH_DONE_S)."""
        live = self.live(wall, keep)
        s = next((s for s in live if self.pick and s.get("key") == self.pick), live[0] if live else None)
        return (s["state"], s) if s else None

    def agents_line(self, wall=None):
        """Dotpals' header, when more than one is live: "3 agents · 2 working · 1 just finished"."""
        live = self.live(wall)
        w = sum(1 for s in live if s.get("state") == "working")
        return f"{len(live)} agents · {w} working" + (f" · {len(live) - w} just finished" if len(live) > w else "") if len(live) > 1 else None

    def next_agent(self, wall=None):
        """Next agent (Dotpals' several at once): the live session after the one shown (`agent`), round again -> its key, now `pick`;
        None when none is live."""
        keys = [s.get("key") for s in self.live(wall)]
        if not keys:
            return None
        cur = (self.agent or {}).get("key")
        self.pick = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0]
        return self.pick

    def view(self, now=None, mine=False):
        """(state, title, detail) to show now: the first that holds wins. mine: Monday's own only, for Chat and + (the owner's agents are
        Home's, the owner 2026-10-17), and `watching` left as it was."""
        now = now if now is not None else time.monotonic()
        if not mine:
            self.watching = None
        if self.online is False:
            return "offline", STATES["offline"][1], "Monday Core isn't answering."
        if self.perms:
            return "waiting", STATES["waiting"][1], perm_words(list(self.perms.values())[-1])
        if self.asks and not mine:  # one of the owner's own agents asking: after Monday's own approvals, before anything else
            a = list(self.asks.values())[-1]
            return "waiting", f"{a.get('agent') or 'An agent'} wants to {a.get('what') or 'act'}", a.get("detail") or ""
        f = self.file
        if f and f.get("phase") in ("copying", "ready", "failed"):
            if f["phase"] == "failed":
                return "failed", f"Couldn't take {f['name']}" if f.get("name") else "Couldn't take it", f.get("why") or ""
            if f["phase"] == "copying":
                return "file", STATES["file"][1], f"Copying {f['name']}… {f.get('pct', 0)}%"
            return "file", f["name"], f"{KIND_WORDS.get(f.get('kind'), 'file').capitalize()}, {size_words(f.get('size') or 0)}"
        lines = [x for x in (self.result, None if mine else self.agent_result) if x and now - x[3] < x[4]]
        r = max(lines, key=lambda x: x[3]) if lines else None  # the newest line still shown: Monday's own, or an agent's
        if r and r[0] == "dizzy":
            return "dizzy", r[1], r[2]
        if now - self.heard_at < HEARD_S:
            return "hearing", STATES["hearing"][1], self.heard or "…"
        if self.tasks:
            tid, t = list(self.tasks.items())[-1]
            s = self.steps.get(tid)
            step = (f"{s['index']}/{s['total']} · {s['label']}" if s.get("total") else s["label"]) if s and s.get("status") == "running" \
                else t.get("current_action")
            more = f" (+{len(self.tasks) - 1} more)" if len(self.tasks) > 1 else ""
            return "working", _line(t.get("title") or STATES["working"][1], 48) + more, step or "Working…"
        if self.asked_text and now - self.asked_text[1] < ASKED_S:
            return "asked", STATES["asked"][1], self.asked_text[0]
        if self.speaking:
            return "speaking", STATES["speaking"][1], ""
        if r:
            return r[0], r[1] or STATES[r[0]][1], r[2]
        w = None if mine else self.watched(keep=WATCH_DONE_S)  # after Monday's own: a result of the owner's click ("In your mail app") isn't hidden by it
        if w:  # an agent the owner runs themselves (Dotpals): what it is on, what was asked, its last edit, the tests, its limits (a minute after it ends)
            self.watching = w[1]
            return self._session_words(w)
        if self.asleep:
            return "asleep", STATES["asleep"][1], "Two claps, a snap or a whistle wake me."
        if self.mic:
            return "listening", STATES["listening"][1], "Talk to me · Ctrl+M turns the mic off"
        return "idle", STATES["idle"][1], "Click me, or drop a file on me"

    def _session_words(self, w):
        more, head = len(self.live()) - 1, self.agents_line()
        return w[0], _line(w[1].get("title") or "", 48 if more else 60) + (f" (+{more} more)" if more else ""), \
            "\n".join(([head] if head else []) + (w[1].get("lines") or [])) or "Working…"

    def agent_view(self, now=None):
        """(state, title, detail) for Home's agent view: an agent's approval waiting, an agent's own line (an alert, a standing yes), else
        the session (the owner's pick, else the first live one); None when there is none. Sets `agent`."""
        now = now if now is not None else time.monotonic()
        w = self.watched()  # the opened notch: a session that finished keeps its place, its last edit on show, while Core lists it
        self.agent = w[1] if w else None
        if self.asks:
            a = list(self.asks.values())[-1]
            return "waiting", f"{a.get('agent') or 'An agent'} wants to {a.get('what') or 'act'}", a.get("detail") or ""
        r = self.agent_result
        if r and now - r[3] < r[4]:
            return r[0], r[1] or STATES[r[0]][1], r[2]
        return self._session_words(w) if w else None

    def needs_owner(self):
        """Something waits on the owner: an approval, or a dropped file's choice."""
        return bool(self.perms) or bool(self.asks) or bool(self.file and self.file.get("phase") == "ready")


# -- who works: the picker and the dock (2026-10-15, the owner's video: "both") --------------------------------------------------
CODERS = ("codex", "gemini_cli", "kilo", "grok_build", "claude_code")  # the settings API's default_coding_agent choices (and "fastest")


def who_menu(settings, tools, models):
    """The picker's sections from Core's answers (GET /api/settings, /api/ai-tools, /api/models): [(section, [(what, value, label,
    chosen, enabled)])]. `what` is the setting a pick changes: computer, code or chat. Only what can answer now can be picked."""
    settings, by = settings or {}, {t["key"]: t for t in tools or []}
    ready = lambda k: (by.get(k) or {}).get("status") == "available"  # noqa: E731
    comp, code = settings.get("default_computer_agent"), settings.get("default_coding_agent")
    sel = (models or {}).get("selected") or "auto"
    return [("Computer work", [("computer", "hermes", "Monday's agent" + ("" if ready("hermes") else " (not ready)"), comp == "hermes", ready("hermes")),
                               ("computer", "computer", "Monday's own hands", comp == "computer", True)]),
            ("Code", [("code", k, by[k]["name"] + ("" if ready(k) else f" ({by[k]['status'].replace('_', ' ')})"), code == k, ready(k))
                      for k in CODERS if (by.get(k) or {}).get("installed")]
             + [("code", "fastest", "Whichever is ready and fastest", code == "fastest", True)]),
            ("Chat", [("chat", m["id"], m["name"] if m["id"] == "auto" else f"{m['name']} · {m.get('provider_label') or m.get('provider')}",
                       sel == m["id"], True) for m in (models or {}).get("models") or [] if m.get("available") and m.get("for_chat") is not False])]


# -- motion: Coucou's close curve and spring (core/qtnotch.py moves the notch with them) ------------------------------------------


def bezier(x1, y1, x2, y2):
    """A CSS cubic-bezier as t -> eased value (bisection on x, as Coucou's anim.ts)."""
    def f(x):
        x = min(1.0, max(0.0, x))
        lo, hi, t = 0.0, 1.0, x
        for _ in range(14):
            v = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
            lo, hi = (t, hi) if v < x else (lo, t)
            t = (lo + hi) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3
    return f


CLOSE_CURVE = bezier(0.45, 0.0, 0.2, 1.0)


class Spring:
    """SwiftUI's spring (Coucou's): omega = 2 pi / response, damping ratio; integrated in 1/240 s steps so a late frame never jumps."""

    def __init__(self, value, response=SPRING[0], damping=SPRING[1]):
        self.value, self.target, self.velocity = float(value), float(value), 0.0
        self.omega, self.zeta = 2 * math.pi / response, damping

    def step(self, dt):
        n = max(1, math.ceil(dt * 240))
        h = dt / n
        for _ in range(n):
            self.velocity += (self.omega ** 2 * (self.target - self.value) - 2 * self.zeta * self.omega * self.velocity) * h
            self.value += self.velocity * h

    def settled(self):
        return abs(self.target - self.value) < 0.5 and abs(self.velocity) < 2.0


# -- the notch's chat and its agents --------------------------------------------------------------------------------------------------


def chat_turns(messages, n=CHAT_KEEP):
    """Monday's chat as the notch shows it, from Core's messages (GET /api/messages, MESSAGE_CREATED): the owner's words and Monday's
    answers, oldest first, the last n; never room talk Monday stayed quiet on (its quiet note, a system note, names it), nor system notes.
    A reply that is only quiet out loud (nothing to say: a pet shown, a computer task's end, a model switched) hides nothing (2026-10-27:
    the owner's questions vanished from the notch with their answers)."""
    quiet = {(m.get("meta") or {}).get("reply_to") for m in messages if (m.get("meta") or {}).get("quiet") and m.get("kind") == "system"}
    out = []
    for m in messages:
        if m.get("id") in quiet or not (m.get("text") or "").strip():
            continue
        if m.get("role") == "user" and m.get("kind") == "text":
            out.append({"who": "you", "text": m["text"], "id": m.get("id")})
        elif m.get("role") == "monday" and m.get("kind") in ("text", "error", "completion"):
            out.append({"who": "Monday", "text": said(m), "id": m.get("id")})
    return out[-n:]


def said(m):
    """What the notch shows of Monday's reply: its words, or, for a long answer made into a document (2026-10-25), its brief: what it says
    first and where the whole of it is."""
    return (m.get("meta") or {}).get("brief") or m.get("text") or ""


def agent_places(ides, sessions):
    """The notch's agents (the owner, 2026-10-17): every open IDE window (core/watch.py ide_windows: its logo's program, its folder), and
    in the Claude app one per Claude Code session the watch shows, with that session's folder. A session working in an IDE (the same
    folder) marks that window: its state and its key (its chat). A CLI's session has no window of its own and is not one of them."""
    from . import watch
    out = [dict(a, session=None, state=None) for a in ides]
    for s in sessions:
        where = s.get("where") or {}
        folder = os.path.basename(str(where.get("cwd") or "").rstrip("\\/"))
        app = watch.IDES.get((watch.APPS.get(where.get("entry")) or ("",))[0], (None,))[0]
        if app is None:
            continue
        if app in ("claude", "codex"):  # the Claude app, the ChatGPT app's Codex: one window, many chats (its title names no folder),
            base = next((a for a in out if a["key"] == app), None)  # so its sessions are its agents, each with its folder
            if base is None:
                continue
            free = next((a for a in out if a["key"] == app and a["session"] is None), None)
            (free.update if free else lambda **kw: out.append(dict(base, **kw)))(folder=folder, session=s.get("key"), state=s.get("state"))
            continue
        hit = next((a for a in out if a["key"] == app and a["folder"].lower() == folder.lower()), None) or \
            next((a for a in out if a["key"] != "claude" and folder and a["folder"].lower() == folder.lower()), None)  # (Claude Code in Kiro says VS Code)
        if hit:
            hit.update(session=s.get("key"), state=s.get("state"))
    return out


# -- one of the owner's agents in detail: Home's agent view (core/qtnotch.py), after Dotpals and the retired WinForms notch -------------
AGENT_TAIL = 280             # an agent's turn at work is many pieces: its newest words this long (the Story has the rest)
AGENT_TURNS = 4              # the agent's chat on its view: its last turns (GET /api/agents/chat reads them from its log)
CHAT_EVERY_S = 3.0           # that chat is read again at most this often while the view shows
CHANGE_WORDS = {"restore": "put back as it was", "delete": "deleted: the request made it", "recreate": "made again: the request deleted it"}
DIFF_HEAD = re.compile(r"^(.*)  −(\d+) \+(\d+)$")   # watch.diff_words' first row: "billing.ts  −1 +1"


def _tail(text, n):
    """The newest words of a long turn: its last n characters or so, from a word on, after "…" (its lines kept)."""
    t = str(text or "").strip()
    if len(t) <= n:
        return t
    start = len(t) - (n - 1)
    if not t[start - 1].isspace():  # mid-word: from the next word on
        m = re.search(r"\s", t[start:])
        start += m.end() if m else 0
    return "…" + t[start:].lstrip()


def said_elsewhere(detail, turns):
    """An agent's lines without those its chat shows already (what the owner asked, its own last words): a line goes when 20 or more of
    its first words are in a turn."""
    flat = lambda x: re.sub(r"\s+", " ", str(x or "")).strip()  # noqa: E731
    said = [flat(t.get("text")) for t in turns]

    def shown(ln):
        words = flat(ln.removeprefix("You asked: ")).rstrip("…").strip()[:60]
        return len(words) >= 20 and any(words in s for s in said)
    return "\n".join(ln for ln in str(detail or "").split("\n") if not shown(ln))


def agent_words(w):
    """ "Claude Code · shop": the agent and its folder."""
    folder = Path(str((w.get("where") or {}).get("cwd") or "")).name
    return (w.get("agent") or "Agent") + (f" · {folder}" if folder else "")


def code_card(diff):
    """The code card (the last edit, as the agent's own patch has it: the owner, 2026-10-20, "i want to see the real edits too in the
    notch like this one") from the session's `diff` (watch.diff_words: {"path", "file", "removed", "added", "rows": [[sign, old line,
    new line, text]], "more"}: "-" removed, "+" added, " " the lines around them, "@" where a hunk starts; a number None where the
    agent's log has none) -> {"file", "path", "removed", "added", "rows": [(sign, old, new, text)], "more"}; the counts only where Core
    says them. An older Core's rows ("billing.ts  −1 +1", "− …", "+ …") read as rows without numbers. None without an edit to show."""
    num = lambda x: x if isinstance(x, int) and not isinstance(x, bool) else None  # noqa: E731
    if isinstance(diff, dict) and diff:
        path = str(diff.get("path") or diff.get("file") or "?")
        rows = [(str(r[0])[:1] if str(r[0])[:1] in ("-", "+", " ", "@") else " ", num(r[1]), num(r[2]), str(r[3]))
                for r in diff.get("rows") or [] if isinstance(r, (list, tuple)) and len(r) == 4]
        return {"file": str(diff.get("file") or Path(path).name), "path": path, "removed": num(diff.get("removed")),
                "added": num(diff.get("added")), "rows": rows, "more": num(diff.get("more")) or 0}
    if not diff:
        return None
    m = DIFF_HEAD.match(str(diff[0]))
    file = m.group(1) if m else str(diff[0])
    return {"file": file, "path": file, "removed": int(m.group(2)) if m else None, "added": int(m.group(3)) if m else None,
            "rows": [("+" if str(r).startswith("+") else "-", None, None, str(r)[2:] if str(r)[:2] in ("+ ", "− ") else str(r))
                     for r in diff[1:]], "more": 0}


def meter_rows(meters):
    """Dotpals' bars from the session's `meters` (watch.meters: the plan, the context, the limits) -> [(kind, words, fill)]: fill 0 to 1
    only where Core's "of" says how far, else None (the words alone), never a guessed bar."""
    out = []
    for m in (x for x in meters or [] if isinstance(x, dict)):
        of = m.get("of")
        fill = min(1.0, max(0.0, float(of))) if isinstance(of, (int, float)) and not isinstance(of, bool) and math.isfinite(of) else None
        out.append((str(m.get("kind") or ""), str(m.get("label") or ""), fill))
    return out


def agent_actions(w, live):
    """The agent view's buttons (Dotpals'), only those that can act now: Next agent while several are live, its Story, Rewind… when Core
    kept a restore point of its last request (the watch's `rewind`; once rewound, to undo it), Steps once it ran or edited something.
    -> [(action, words, what a screen reader says)]."""
    who, keys, rw = w.get("agent") or "it", [s.get("key") for s in live], w.get("rewind") or {}
    out = []
    if len(keys) > 1:
        i = keys.index(w.get("key")) + 1 if w.get("key") in keys else 1
        out.append(("next", "Next agent", f"Show the next of your {len(keys)} agents (this is {i} of {len(keys)})"))
    out.append(("story", "Story", f"Today's story: what {who} did today, in plain words"))
    if rw.get("id"):
        n = rw.get("files") or 0
        out.append(("rewind", "Rewound · Undo…", f"{who}'s last request was rewound: look at it again, or undo the rewind") if rw.get("rewound")
                   else ("rewind", "Rewind…", f"Put back what {who}'s last request changed ({n} file{'s' * (n != 1)}), after a look at what goes back"))
    if w.get("steps"):
        out.append(("steps", "Steps", f"What {who} did for this request, step by step: runs and edits, their tries and times"))
    return out


def steps_view(w, view):
    """Steps through one of Dotpals' filters (the session's `steps`, watch.steps_views: key steps, changes, commands, problems, all, its
    Files, its Check) -> (the filter shown, its lines, [(filter, its chip)]); one that isn't there any more falls back to the first."""
    views = [v for v in w.get("steps") or [] if isinstance(v, (list, tuple)) and len(v) == 3]
    got = next((v for v in views if v[0] == view), views[0] if views else None)
    return (got[0], list(got[2]), [(v[0], v[1]) for v in views]) if got else (None, [], [])


HOOKS = ("approvals", "stop", "pre", "mcp", "wake")  # Monday's in the owner's Claude Code, as Core reads them (agent_hooks.installed)


def say_words(w, answer, err, where):
    """Core's answer to the owner's words for an agent (POST /api/agents/say) -> (queued, what its view says). Queued: its own hooks hand
    them over before its next step, or its wake hook wakes it with them, its turn over ("how": "wake", 2026-10-22). Not (it isn't
    Claude Code, has no Monday hooks, nothing listens for it; Core away): the words are copied for the owner to paste in its chat, in
    `where` (its app with that chat opened, its window brought forward, or "its window")."""
    agent = w.get("agent") or "the agent"
    if (answer or {}).get("queued"):
        how = answer.get("how")
        return True, (f"Waking {agent} with your words: they show in its chat in a moment." if how == "wake" else
                      f"Queued in {agent}: that thread reads your words as your message when it next runs." if how == "queue" else
                      f"Waiting for its next step: {agent}'s own hooks hand your words over then.")
    why = str((answer or {}).get("why") or err or "It can’t take it now").rstrip(". ")
    return False, f"{why}. Paste it in its chat in {where} (Ctrl+V)."


def rewind_words(plan):
    """Core's preview (POST /api/rewind/preview) in words for Rewind's window -> (who was asked what, [(path, what happens to it)], why
    it can't go now or None)."""
    who = f"{plan.get('agent') or 'The agent'}" + (f" in {plan['project']}" if plan.get("project") else "")
    files = [(str(x["path"]), CHANGE_WORDS.get(x.get("change"), str(x.get("change") or "")) +
              ("  ·  changed again since: that goes too" if x.get("since") else "")) for x in plan.get("files") or [] if isinstance(x, dict) and x.get("path")]
    return (f"{who} was asked: “{_line(plan.get('asked') or '', 200)}”. Untick a file to keep it as it is now.", files,
            f"{plan['busy']}: you can rewind when it's done." if plan.get("busy") else None)


def rewind_button(n):
    """Rewind's button: how many files go back."""
    return f"Rewind {n} file{'s' * (n != 1)}" if n else "Rewind"


# -- the pet on the desktop (Dotpals' pal above the taskbar; drawn by core/qtnotch.py DeskPet) -----------------------------------------
DESK = 120          # the pet on the desktop, logical px
DESK_REST_MS = 100  # its frames at rest (10 a second: the bob and the blink still read); FRAME_MS while it reacts. A frame is ~2.6 ms,
#                     so 30 a second all the time was 8% of a core for a pet that mostly sits (2026-10-16)


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        assert folder_words(r"E:\monday\app") == "monday › app" and folder_words("/home/b/proj") == "b › proj" and folder_words("C:\\") == "" \
            and folder_words(None) == "" and folder_words("E:/one") == "one", folder_words(r"E:\monday\app")
        assert prefs(None) == PET_DEFAULTS and prefs({"kind": "fox"})["name"] == "Ember" and prefs({"kind": "fox"})["body"] == PETS["fox"][2]
        from .bots import PET_KINDS
        assert set(PETS) - {"pal"} == set(PET_KINDS), "a bot's face is one of these pets (core/bots.py PET_KINDS); the owner's own pal is theirs"
        assert set(DP_PRESETS) | {"pal"} <= set(PETS) and all(s in DP_SHAPES and e in DP_EYES and t in DP_TOPS for s, e, t in DP_PRESETS.values())
        q = prefs({"kind": "pal", "shape": "heart", "eyes": "visor", "top": "crown", "fur": False})
        assert (q["shape"], q["eyes"], q["top"], q["fur"]) == ("heart", "visor", "crown", False) and prefs({"shape": "cube", "eyes": "x"})["shape"] == "round", \
            "the builder's choices, each checked (Dotpals' cleanCustom)"
        assert prefs({})["desk"] is True and prefs({"desk": False})["desk"] is False, "the pet on the desktop: there unless put away"
        assert prefs({})["lined"] is False and prefs({"lined": True})["lined"] is True and prefs({"lined": "yes"})["lined"] is False, \
            "the folded line: kept as a bool, off unless the owner folded it"
        assert (prefs({})["volume"], prefs({"volume": 3})["volume"], prefs({"volume": "x"})["volume"]) == (0.6, 1.0, 0.6) and \
            (prefs({})["autoclose"], prefs({"autoclose": 10})["autoclose"], prefs({"autoclose": 12})["autoclose"], prefs({"autoclose": True})["autoclose"]) \
            == (15, 10, 15, 15), "Settings' volume (0 to 1) and auto-close (only the reference's 10, 15 or 30 s), each checked"
        import struct
        peak = lambda wav: max(abs(v) for v in struct.unpack(f"<{(len(wav) - 44) // 2}h", wav[44:]))  # noqa: E731 (a WAV's 44-byte header)
        assert abs(peak(tone_wav("done", 1.0)) / peak(tone_wav("done", 0.25)) - 4) < 0.1 and peak(tone_wav("done", 0.0)) == 0 and \
            peak(tone_wav("done", 1.0)) <= TONE_PEAK * 32767 + 1, "the volume is the tones' real loudness"
        s, top = Spring(0.0), 0.0
        s.target = 100.0
        for _ in range(90):  # 1.5 s of frames
            s.step(1 / 60)
            top = max(top, s.value)
        assert 102 < top < 112 and abs(s.value - 100) < 0.5 and s.settled(), ("Coucou's spring: a touch of overshoot, then still", top, s.value)
        assert CLOSE_CURVE(0) < 1e-3 and CLOSE_CURVE(1) > 0.999 and CLOSE_CURVE(0.25) < 0.25 < CLOSE_CURVE(0.6), "its close curve: slow in, settles"
        assert len(tone_wav("needs", 1.0)) - 44 == 2 * sum(int(TONE_RATE * ms / 1000) for _, ms in TONES["needs"]), "each note as long as Dotpals'"
        assert (prefs({})["desk_size"], prefs({"desk_size": 9})["desk_size"], prefs({"desk_size": 0.1})["desk_size"]) == (1.0, 2.5, 0.5), \
            "its size on the desktop: 50% to 250%"
        p = prefs({"kind": "cat", "name": "  Biscuit   the  cat ", "body": "#00ff00", "accent": "red", "size": 9, "intensity": -1,
                   "idle": "dance", "sounds": "yes", "visible": False})
        assert (p["name"], p["body"], p["accent"], p["size"], p["intensity"], p["idle"], p["sounds"], p["visible"]) == \
            ("Biscuit the cat", "#00FF00", PETS["cat"][3], 1.4, 0.0, "bob", False, False), p
        assert all(set(KIND_ACTIONS[k]) <= set(ACTIONS) for k in KIND_ACTIONS) and set(KINDS) | {"other"} == set(KIND_ACTIONS)
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="monday-notch-"))
        try:
            (tmp / "brief.pdf").write_bytes(b"%PDF-1.4 x")
            (tmp / "tool.py").write_text("print(1)")
            (tmp / "setup.exe").write_bytes(b"MZ")
            a = examine(tmp / "brief.pdf")
            assert a["ok"] and a["kind"] == "doc" and a["actions"][0] == "summarize" and "open" in a["actions"] and "email" in a["actions"], a
            assert "open" not in examine(tmp / "tool.py")["actions"] and examine(tmp / "tool.py")["kind"] == "code", "a script is read, never run"
            ex = examine(tmp / "setup.exe")
            assert ex["ok"] and ex["actions"] == ("chat",) and ex["runs"], "a program can be talked about, never opened from here"
            assert not examine(tmp)["ok"] and "folder" in examine(tmp)["why"] and not examine(tmp / "nope.txt")["ok"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        assert mail_draft_parse("Subject: The brief\n\nHere's the brief.\nThanks", "b.pdf") == ("The brief", "Here's the brief.\r\nThanks")
        assert mail_draft_parse("**Subject:** Q3 numbers\nHi, attached.", "b.pdf") == ("Q3 numbers", "Hi, attached.")
        assert mail_draft_parse("Here's the brief.", "b.pdf") == ("b.pdf", "Here's the brief."), "no subject line: the file's name stays"
        T = [{"key": "hermes", "name": "Hermes Agent", "kind": "computer agent", "installed": True, "status": "available"},
             {"key": "codex", "name": "Codex", "kind": "coding agent", "installed": True, "status": "available"},
             {"key": "claude_code", "name": "Claude Code", "kind": "coding agent", "installed": True, "status": "not_authenticated"},
             {"key": "freebuff", "name": "Freebuff", "kind": "coding agent", "installed": True, "status": "detected_unsupported"},
             {"key": "copilot_cli", "name": "GitHub Copilot CLI", "kind": "coding agent", "installed": True, "status": "detected_unsupported"},
             {"key": "antigravity", "name": "Antigravity IDE", "kind": "editor", "installed": True, "status": "available"},
             {"key": "kilo", "name": "Kilo Code CLI", "kind": "coding agent", "installed": False, "status": "unavailable"},
             {"key": "chatgpt", "name": "ChatGPT", "kind": "app", "installed": True, "status": "detected_unsupported"}]
        M = {"selected": "gpt-oss:120b-cloud", "models": [
            {"id": "auto", "name": "Automatic", "available": True},
            {"id": "gpt-oss:120b-cloud", "name": "gpt-oss:120b-cloud", "provider_label": "Ollama", "available": True, "for_chat": True},
            {"id": "hermes-3:8b", "name": "hermes-3:8b", "provider_label": "Ollama", "available": True, "for_chat": False},
            {"id": "anthropic", "name": "Claude", "provider_label": "Anthropic", "available": False}]}
        S = {"default_computer_agent": "hermes", "default_coding_agent": "claude_code"}
        menu = dict(who_menu(S, T, M))
        assert [r[1:] for r in menu["Computer work"]] == [("hermes", "Monday's agent", True, True), ("computer", "Monday's own hands", False, True)], \
            "the picker never says Hermes (the owner, 2026-10-20)"
        assert [(r[1], r[3], r[4]) for r in menu["Code"]] == [("codex", False, True), ("claude_code", True, False), ("fastest", False, True)], \
            "an agent that isn't signed in shows (chosen or not) and can't be picked; one not installed isn't there"
        assert [(r[1], r[3]) for r in menu["Chat"]] == [("auto", False), ("gpt-oss:120b-cloud", True)], "only chat models that answer"
        s = State()
        assert s.view(0)[0] == "idle"
        s.mic = True
        assert s.view(0)[0] == "listening"
        s.feed({"type": "TASK_CREATED", "data": {"task": {"id": "T1", "title": "Open YouTube and play it", "status": "QUEUED"}}}, now=1)
        s.feed({"type": "TASK_STEP", "task_id": "T1", "data": {"step": {"index": 2, "total": 4, "label": "Open YouTube", "status": "running"}}}, now=2)
        assert s.view(2) == ("working", "Open YouTube and play it", "2/4 · Open YouTube"), s.view(2)
        s.feed({"type": "PERMISSION_REQUIRED", "data": {"permission": {"id": 7, "title": "Hermes wants to run a command.", "status": "PENDING"}}}, now=3)
        assert s.view(3)[0] == "waiting" and s.view(3)[2] == "Allow Hermes to run a command?" and s.needs_owner(), "an approval comes first"
        s.feed({"type": "PERMISSION_GRANTED", "data": {"permission": {"id": 7, "status": "GRANTED"}}}, now=4)
        s.heard_at, s.heard = 4.0, "open you"
        assert s.view(4.5) == ("hearing", "Hearing you", "open you") and s.view(4 + HEARD_S + 0.1)[0] == "working"
        s.feed({"type": "TASK_FAILED", "data": {"task": {"id": "T1", "title": "Open YouTube", "status": "FAILED", "error": "YouTube isn't installed."}}}, now=10)
        assert s.view(11) == ("failed", "Failed", "YouTube isn't installed.") and s.view(10 + RESULT_S + 1)[0] == "listening", s.view(11)
        # a coding task: happy only when the tests passed after its change (core/evidence.py); its word alone is "not verified"
        passing = {"changed": True, "state": "passing", "line": "Tests passed · 3 passed (Monday ran them after the last change)"}
        s.feed({"type": "TASK_TESTS", "data": {"task": {"id": "T2", "title": "Fix the sum", "status": "RUNNING", "tests": {**passing, "state": "failing"}}}}, now=12)
        assert s.tasks["T2"]["tests"]["state"] == "failing" and s.view(12)[0] == "working", "the tests' news keeps the task's card current"
        s.feed({"type": "TASK_COMPLETED", "data": {"task": {"id": "T2", "title": "Fix the sum", "status": "COMPLETED", "result": "Fixed.", "tests": passing}}}, now=13)
        assert s.view(13) == ("verified", "Verified", passing["line"] + "\nFixed.") and STATES["verified"][0] == "happy", s.view(13)
        s.feed({"type": "TASK_COMPLETED", "data": {"task": {"id": "T3", "title": "Fix it", "status": "COMPLETED", "result": "Done!",
                                                            "tests": {"changed": True, "state": "untested", "line": "Not tested: no test command"}}}}, now=14)
        assert s.view(14)[:2] == ("unverified", "Done, not verified") and STATES["unverified"][0] != "happy", "no test evidence: never a happy pet"
        s.feed({"type": "TASK_COMPLETED", "data": {"task": {"id": "T4", "title": "Explain it", "status": "COMPLETED", "result": "It sums.",
                                                            "tests": {"changed": False, "state": "passing"}}}}, now=15)
        assert s.view(15) == ("done", "Done", "It sums."), "no code changed: an ordinary done"
        s.result = None
        s.asked_text = ("Summarize the attached file", 30.0)
        assert s.view(30.5) == ("asked", "Asked Monday", "Summarize the attached file"), "asked, not done: no happy face before an answer"
        s.asked.add(41)
        s.feed({"type": "MESSAGE_CREATED", "data": {"message": {"role": "monday", "kind": "text", "text": "It says the launch moved.", "meta": {"reply_to": 41}}}}, now=31)
        s.feed({"type": "MESSAGE_CREATED", "data": {"message": {"role": "monday", "kind": "text", "text": "not ours", "meta": {"reply_to": 99}}}}, now=32)
        assert s.view(32) == ("answer", "Answer", "It says the launch moved.") and s.asked_text is None, "only answers to what was asked here"
        assert s.view(31 + RESULT_S + 5)[0] == "answer" and s.view(31 + ANSWER_S + 1)[0] == "listening", "an answer stays long enough to read"
        s2 = State()  # a long answer made into a document (2026-10-25): the notch shows its brief, not the head of the whole
        s2.asked.add(41)
        doc_reply = {"role": "monday", "kind": "text", "text": "## Artemis III\n" + "- A point.\n" * 300,
                     "meta": {"reply_to": 41, "brief": "It launches in 2027.\n\nThe whole answer is in “Artemis III.docx”."}}
        s2.feed({"type": "MESSAGE_CREATED", "data": {"message": doc_reply}}, now=31)
        assert s2.view(31)[2].startswith("It launches in 2027.") and chat_turns([doc_reply])[0]["text"] == doc_reply["meta"]["brief"], \
            "a long answer's brief, in the closed bar and in the notch's chat"
        you = {"id": 50, "role": "user", "kind": "text", "text": "show your pet"}
        hush = {"id": 51, "role": "monday", "kind": "text", "text": "Showing my pet in the notch.", "meta": {"reply_to": 50, "quiet": True}}
        assert [t["who"] for t in chat_turns([you, hush])] == ["you", "Monday"], \
            "a reply that is only quiet out loud (speech.quiet, a pet shown, a task's end) keeps the owner's turn and its own (2026-10-27)"
        note = {"id": 52, "role": "monday", "kind": "system", "text": "Stayed quiet", "meta": {"reply_to": 50, "quiet": True}}
        assert chat_turns([you, note]) == [], "room talk Monday stayed quiet on (its system note names it): neither is a turn"
        last = s.last
        s.feed({"type": "COMPONENTS", "data": {}}, now=500)
        assert s.last == last, "a routine component check isn't something happening (the pet may doze)"
        s.online = False
        assert s.view(32)[0] == "offline", "offline before anything"
        s.online, s.file = True, {"phase": "copying", "name": "brief.pdf", "pct": 42}
        assert s.view(33) == ("file", "Got your file", "Copying brief.pdf… 42%")
        s.file = {"phase": "failed", "name": "big.iso", "why": "too big"}
        assert s.view(33) == ("failed", "Couldn't take big.iso", "too big")
        s.file = None  # Dotpals: an agent the owner runs themselves shows when Monday has nothing of its own going on
        s.feed({"type": "AGENT_WATCH", "data": {"sessions": [{"state": "working", "title": "Claude Code · shop · 2/4 · Fix the rounding",
                                                                "lines": ["You asked: Fix the VAT rounding", "Plan 2/4 · Fix the rounding"],
                                                                "diff": ["billing.ts  −1 +1", "− floor", "+ round"]}]}}, now=40)
        assert s.view(40)[0] == "answer", "Monday's answer to what was asked from here is still being read: it comes first"
        assert s.view(100) == ("working", "Claude Code · shop · 2/4 · Fix the rounding", "You asked: Fix the VAT rounding\nPlan 2/4 · Fix the rounding"), \
            "the words; the edit is the code card's (watch.diff_words)"
        assert s.watching and s.watching["title"].startswith("Claude Code"), "the session shown, for its Story"
        s.feed({"type": "AGENT_WATCH", "data": {"sessions": [{"state": "done", "title": "Claude Code · shop · done in 1m 47s",
                                                                "lines": ["You asked: x", "Fixed it."], "done_at": time.time() - 5}]}}, now=101)
        assert s.view(101)[0] == "done" and s.watched()[1]["title"].endswith("1m 47s"), "done, in its own words, for a minute"
        later = time.time() + WATCH_DONE_S + 10
        assert s.watched(wall=later, keep=WATCH_DONE_S) is None, "then the closed notch's line is quiet again (view)"
        # ...but the opened notch keeps it, with its last edit, while Core lists it (the owner, 2026-10-20: the IDE's changes in the notch)
        assert s.watched(wall=later)[1]["title"].endswith("1m 47s") and s.agent_view(102)[0] == "done" and s.agent is not None
        assert s.watched(wall=time.time() + WATCH_SHOWN_S + 10) is None, "gone with Core's listing"
        s.tasks["T9"] = {"id": "T9", "title": "Monday's own"}
        assert s.view(102)[1] == "Monday's own" and s.watching is None, "Monday's own work comes first (no Story button on it)"
        s.tasks.clear()
        s.result = ("done", "In your mail app", "To a@b.co: press Send there.", 103, RESULT_S)
        assert s.view(103)[1] == "In your mail app", "what the owner's own click did comes before an agent they run"
        s.result = None
        s.feed({"type": "AGENT_WATCH", "data": {"sessions": [  # Dotpals with several agents: "3 agents · 2 working · 1 just finished"
            {"key": "a", "agent": "Claude Code", "state": "working", "title": "Claude Code · app · Fixing the notch", "lines": ["Plan 1/2"]},
            {"key": "c", "agent": "Gemini CLI", "state": "done", "title": "Gemini CLI · docs · done in 2m", "lines": [], "done_at": time.time() - 5},
            {"key": "b", "agent": "Codex", "state": "working", "title": "Codex · api · Adding a limiter", "lines": []}]}}, now=104)
        st, title, detail = s.view(120)
        assert (st, title, detail) == ("working", "Claude Code · app · Fixing the notch (+2 more)", "3 agents · 2 working · 1 just finished\nPlan 1/2"), \
            (title, detail)
        s.pick = "b"
        assert s.view(121)[1] == "Codex · api · Adding a limiter (+2 more)" and s.watching["key"] == "b", "the one the owner moved to"
        s.pick = "c"
        assert s.view(122)[:2] == ("done", "Gemini CLI · docs · done in 2m (+2 more)"), "a finished one too, while it shows"
        s.pick = "gone"
        assert s.view(123)[1].startswith("Claude Code · app"), "one that isn't live any more: the first working one again"
        s.watch, s.pick = [], None
        a = {"agent": "Claude Code", "what": "run a command", "detail": "npm test", "project": "shop", "until": 1000.0}
        assert ask_words(a, wall=1000.0 - 61.4) == ("Claude Code wants to run a command:\nnpm test\nIn shop. Ctrl+Alt+Y allows, Ctrl+Alt+N denies, "
                                                    "from any app. Claude Code's own prompt still works too.\n1:01 left here, then answer in Claude Code.")
        assert ask_words(a, wall=1100.0).endswith("\n0:00 left here, then answer in Claude Code.") and "left here" not in ask_words({**a, "until": None}), \
            "never below zero; an older hook (no wait sent): no countdown"
        assert ask_words({**a, "detail": "git push -f", "flags": ["Force-pushes to git (can overwrite others' work)"]}, wall=1000.0).startswith(
            "Claude Code wants to run a command:\ngit push -f\nWorth a second look: Force-pushes to git (can overwrite others' work)\nIn shop."), \
            "Dotpals' flags on the approval card"
        assert "Ctrl+Alt+Y" not in ask_words(a, wall=1000.0, keys=False), "keys that aren't Monday's on this keyboard are never offered in words"
        long_ = breakable("run C:/Users/" + "x/" * 40 + "gemini.exe once")
        assert max(map(len, long_.replace("\u200b", " ").split())) <= 24 and long_.replace("\u200b", "").startswith("run C:/Users/x/x/"), \
            "a path or a link in a reply can wrap in the notch (nothing else changes)"
        # Active pills (2026-10-20): the owner's choice kept clean; Monday's pick from what Core found, in the slots the windows leave
        assert prefs({"pills": ["codex", "codex", "Bad Key", 3, "hermes", "kilo", "gemini_cli", "grok_build"]})["pills"] == \
            ["codex", "hermes", "kilo", "gemini_cli", "grok_build"] and prefs({})["pills"] is None and prefs({"pills": "codex"})["pills"] is None
        assert len(prefs({"pills": [f"k{i}" for i in range(12)]})["pills"]) == PILLS_MAX, "up to eight of the owner's (2026-10-20 night)"
        tl = {"claude_code": {"name": "Claude Code", "kind": "coding agent", "installed": True, "status": "not_authenticated"},
              "codex": {"name": "Codex", "kind": "coding agent", "installed": True, "status": "available"},
              "hermes": {"name": "Hermes Agent", "kind": "computer agent", "installed": True, "status": "available"},
              "kiro": {"name": "Kiro", "kind": "editor", "installed": True, "status": "available"},
              "cursor": {"name": "Cursor", "kind": "editor", "installed": True, "status": "detected_unsupported"},
              "ollama": {"name": "Ollama", "kind": "models", "installed": False, "status": "unavailable"},
              "vscode": {"name": "VS Code", "kind": "editor", "status": "weird"}}
        assert auto_pills(tl) == ["kiro", "cursor"], "Monday's pick: installed, no CLIs and no Hermes (the owner's words), the ready ones first"
        assert pill_state(tl["claude_code"]) == ("wait", "not signed in") and pill_state(tl["vscode"]) == ("off", "N/A")
        assert [p["pill"] for p in dock_pills(None, tl, [{"key": "claude"}, {"key": "kiro"}])] == ["cursor"], \
            "the free slots only, a tool whose window shows not twice"
        assert [p["pill"] for p in dock_pills(["hermes", "cursor"], tl, [])] == ["cursor"], "even chosen, Hermes is no pill (2026-10-20)"
        assert [p["pill"] for p in dock_pills(["vscode", "codex", "gone", "cursor"], tl, [{"key": "vscode"}])] == ["cursor"], \
            "the owner's own: those Core knows, no CLI even if once chosen, no window twice"
        # one of the owner's agents in detail (Home's agent view): the code card, the bars, its buttons, Steps, Next agent, Write to, Rewind
        real = {"path": r"C:\shop\src\billing.ts", "file": "billing.ts", "removed": 1, "added": 1, "more": 3, "rows": [
            ["@", 40, 40, ""], [" ", 40, 40, "export function vat(net, rate) {"], ["-", 41, None, "  return Math.floor(net * rate);"],
            ["+", None, 41, "  return Math.round(net * rate);"], ["?", True, "7", "junk"], ["short"]]}
        assert code_card(real) == {"file": "billing.ts", "path": r"C:\shop\src\billing.ts", "removed": 1, "added": 1, "more": 3, "rows": [
            ("@", 40, 40, ""), (" ", 40, 40, "export function vat(net, rate) {"), ("-", 41, None, "  return Math.floor(net * rate);"),
            ("+", None, 41, "  return Math.round(net * rate);"), (" ", None, None, "junk")]}, \
            "watch.diff_words as the card: the agent's own rows and line numbers; a number that isn't one is none, a short row dropped"
        assert code_card(["billing.ts  −1 +2", "− floor", "+ round", "+ x"]) == {"file": "billing.ts", "path": "billing.ts", "removed": 1,
                                                                                "added": 2, "more": 0, "rows": [("-", None, None, "floor"),
                                                                                ("+", None, None, "round"), ("+", None, None, "x")]}, \
            "an older Core's rows: red and green, no numbers made up"
        assert code_card([]) is None and code_card({}) is None and code_card(["notes.md"])["removed"] is None, \
            "no edit, no card; a first row without counts: no counts made up"
        wins = [{"key": "codex", "name": "ChatGPT", "folder": "", "hwnd": 7, "pid": 1, "exe": r"E:\WindowsApps\OpenAI.Codex\app\ChatGPT.exe"},
                {"key": "claude", "name": "Claude", "folder": "", "hwnd": 8, "pid": 2, "exe": r"C:\x\claude.exe"}]
        chats = [{"key": "k1", "state": "working", "where": {"entry": "codex_work_desktop", "cwd": r"C:\work\api"}},
                 {"key": "k2", "state": "done", "where": {"entry": "claude-desktop", "cwd": r"C:\work\app"}}]
        joined = {p["session"]: (p["name"], p["folder"], p["hwnd"]) for p in agent_places(wins, chats) if p["session"]}
        assert joined == {"k1": ("ChatGPT", "api", 7), "k2": ("Claude", "app", 8)}, \
            f"the ChatGPT app's Codex chat joins its window as Claude's do the Claude app's (its mascot, its ↗; 2026-10-21): {joined}"
        assert meter_rows([{"kind": "plan", "label": "Plan 2/4", "of": 0.5}, {"kind": "context", "label": "Context 896k tokens", "of": None},
                           {"kind": "use", "label": "Codex week 130%", "of": 1.3}, {"kind": "use", "label": "x", "of": "64%"}, {"of": True}, "junk"]) == [
            ("plan", "Plan 2/4", 0.5), ("context", "Context 896k tokens", None), ("use", "Codex week 130%", 1.0), ("use", "x", None), ("", "", None)], \
            "a bar only where Core says how far, at most full; anything else is words alone, never a guessed bar"
        w1 = {"key": "a", "agent": "Claude Code", "state": "working", "title": "Claude Code · shop · 2/4 · Fix the rounding",
              "where": {"cwd": os.path.join("work", "shop")}, "rewind": {"id": "r1", "files": 1, "rewound": False},
              "steps": [["key", "Key steps 2", ["2 key steps · 1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2", "RUN · npm test · try 2 · 1s ✓"]],
                        ["problems", "Problems 1", ["1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2"]],
                        ["files", "Files 1", ["Changed · 1", "billing.ts  +1 −1"]]]}
        assert agent_words(w1) == "Claude Code · shop" and agent_words({}) == "Agent"
        acts = agent_actions(w1, [w1])
        assert [x[:2] for x in acts] == [("story", "Story"), ("rewind", "Rewind…"), ("steps", "Steps")] and "(1 file)" in acts[1][2], acts
        assert [x[0] for x in agent_actions({**w1, "steps": [], "rewind": None}, [w1, {"key": "b"}])] == ["next", "story"] and \
            "this is 1 of 2" in agent_actions(w1, [w1, {"key": "b"}])[0][2], "Next agent only with several; no Rewind without a restore point, no Steps yet"
        assert agent_actions({**w1, "rewind": {"id": "r1", "files": 3, "rewound": True}}, [w1])[1][1] == "Rewound · Undo…"
        assert steps_view(w1, "problems") == ("problems", ["1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2"],
                                              [("key", "Key steps 2"), ("problems", "Problems 1"), ("files", "Files 1")]), "a filter, and the chips"
        assert steps_view(w1, "changes")[:2] == ("key", w1["steps"][0][2]) and steps_view({}, "key") == (None, [], []), \
            "a filter that isn't there any more: the first; none before any step"
        s.watch, s.pick = [w1, {"key": "b", "agent": "Codex", "state": "working", "title": "Codex · api"},
                           {"key": "c", "agent": "Gemini CLI", "state": "done", "title": "Gemini CLI · docs", "done_at": time.time() - 5}], None
        s.agent_view()
        assert s.agent["key"] == "a" and s.next_agent() == "b" and s.agent_view() and s.agent["key"] == "b", "Next agent: the next live one"
        s.agent_view()
        assert s.next_agent() == "c" and s.agent_view() and s.next_agent() == "a", "a finished one too, while it shows; then round again"
        s.watch, s.pick = [], None
        assert s.next_agent() is None and s.agent_view() is None
        assert say_words(w1, {"queued": True, "why": None}, None, "its window") == \
            (True, "Waiting for its next step: Claude Code's own hooks hand your words over then.")
        assert say_words(w1, {"queued": True, "how": "wake", "why": None}, None, "its window") == \
            (True, "Waking Claude Code with your words: they show in its chat in a moment."), "its turn over: its wake hook wakes it"
        assert say_words({"agent": "Codex"}, {"queued": True, "how": "queue", "why": None}, None, "its window") == \
            (True, "Queued in Codex: that thread reads your words as your message when it next runs."), "Codex: its own queue"
        assert say_words(w1, {"queued": False, "why": "Claude Code isn't working now, so it won't ask Monday for it"}, None, "VS Code") == \
            (False, "Claude Code isn't working now, so it won't ask Monday for it. Paste it in its chat in VS Code (Ctrl+V).")
        assert say_words(w1, None, "Monday Core is unreachable.", "its window")[1] == \
            "Monday Core is unreachable. Paste it in its chat in its window (Ctrl+V).", "Core away: copied, and said once"
        plan = {"agent": "Claude Code", "project": "shop", "asked": "Fix the VAT rounding", "busy": None, "now": "t1",
                "files": [{"path": "billing.ts", "change": "restore", "since": False}, {"path": "new.ts", "change": "delete", "since": True}]}
        assert rewind_words(plan) == ("Claude Code in shop was asked: “Fix the VAT rounding”. Untick a file to keep it as it is now.",
                                      [("billing.ts", "put back as it was"), ("new.ts", "deleted: the request made it  ·  changed again since: that goes too")],
                                      None), rewind_words(plan)
        assert rewind_words({**plan, "busy": "Codex is working there now"})[2] == "Codex is working there now: you can rewind when it's done."
        assert (rewind_button(0), rewind_button(1), rewind_button(3)) == ("Rewind", "Rewind 1 file", "Rewind 3 files")
        assert _tail("one two three four", 9) == "…four" and _tail("short", 10) == "short", "an agent's turn at work: its newest words"
        assert said_elsewhere("You asked: Fix the VAT rounding in billing please\nPlan 2/4 · Fix",
                              [{"who": "you", "text": "Fix the VAT rounding in billing please"}]) == "Plan 2/4 · Fix", "what the chat shows isn't said twice"
        # the wardrobe (2026-10-26): "auto" is the season's, a pick is kept as it is, anything else is nothing
        import datetime as _d
        assert [wear_now("auto", _d.date(2026, m, d)) for m, d in ((10, 26), (12, 10), (12, 31), (1, 1), (4, 5), (7, 4))] == \
            ["witch", "santa", "party", "party", "bunny", "none"]
        assert wear_now("crown") == "crown" and wear_now("tophat") == "none" and prefs({"wear": "pumpkin"})["wear"] == "pumpkin" \
            and prefs({"wear": "tophat"})["wear"] == "none" and HATS <= set(WEAR)
        # a click is a click (the owner, 2026-10-29: "when im clicking on something it thinks like im draging it"; the log: the bar folded
        # by clicks whose pointer drifted 11-16 px up as the touchpad was pressed)
        assert not dragging(0.12, 16, 10) and not dragging(0.25, 29, 10), "a quick press: a click, however the pointer drifted"
        assert dragging(0.35, 12, 10) and dragging(0.05, FLING_PX, 10), "held and moved, or flung far: a drag"
        assert not dragging(2.0, 6, 10), "held without going anywhere: still a click (a long press is the tiles' own)"

        print("notch self-check: OK")
    else:
        print(__doc__)
