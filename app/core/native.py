"""Monday Core's client and the window's shared parts (the window itself is Qt: core/qtwindow.py, the notch core/qtnotch.py).
The WinForms window that lived here was retired on 2026-10-19 (the owner: "yes, retire it"); what it shared stayed:

- Core: the one API client (httpx, this computer only, the window token on every call) and its event stream (events, sse_events).
- Ear and Speaker: the microphone as Core's ear (core/stt.py: a session per sentence) and replies said aloud in the voice Core holds,
  by the old pages' rules (to_say, is_echo, speakable); windows_voices / os_voice_cmd: this computer's own voices on each OS.
- The pages' words from Core's answers (task_rows, todo_rows, bot_line, tool_rows, reflex_view, google_line, …): pure functions.

Approvals are the owner's clicks on Approve / Deny, nothing else; nothing is decided here.

    python -m core.native --check    self-check: the parsing, the pages' words, Windows' voices, the window token
"""
import ctypes
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
from datetime import datetime
import uuid

import httpx

PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pylib")  # beside the app folder:
# E:\monday\pylib here, <install>\pylib for an installed Monday (installer/build.py)
PORT = int(os.environ.get("MONDAY_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
RETRY_S = 2                  # the event stream reconnects this soon after Core stops answering
OFFLINE_AFTER_S = 8          # no stream this long: "Offline"
STREAM_READ_S = 35           # nothing on the event stream this long (core/app.py SSE_PING_S: a ": ping" every 15 s; two missed, 5 s more): the
                             # socket is dead, not quiet (a sleep/resume on a Modern Standby PC, 2026-10-27), so it is opened again (?since=)
EAR_REOPEN_S = 0.4           # a sentence's session ended: the next one opens this soon
EAR_BACK_S = 15              # the ear turned itself off on a lasting error: the status poll tries it again no sooner than this (Ear.back)
VOICE_BEAT_S = 5             # "listening" is said to Core this often (core/app.py EAR_FRESH_S is 12)
FRAME_MAX = 4_000_000        # one browser frame larger than this is a broken stream, not a picture
SPEAK_MAX = 200              # characters spoken per reply; the rest stays on screen
ECHO_SHARE = 0.6             # a sentence heard while Monday talks is its own voice coming back when this share of its words were just said
LASTING = ("service-not-allowed", "audio-capture", "not-allowed", "language-not-supported", "unavailable")  # the ear can't open until the owner changes something
EARLY_TICK_S = 0.25          # the words so far go to Core's early execution this often while they change or hold still
EARLY_HOLD_MS = 750          # = router.EARLY_HOLD_MS (700) and a tick: an action clause may start once its words held still this long
EXACT_END_MS = 700           # a one-step exact command whose words held still this long (Core's `exact`) ends its sentence now; the ear's
#                              own patience (core/hear.py END_S, 2.4 s) is for everything else. The hold starts at the phrase's commit,
#                              itself 0.45 s into the silence, so this ends ~1.2-1.5 s after the last word
PREPASS_EVERY_S = 0.5        # debug mode's reflex strip: the words so far go to Jev (POST /api/prepass) at most this often, when they changed,
PREPASS_MIN_WORDS = 2        # once there are this many ("open" alone says nothing yet); the finished sentence always goes. Each call costs tokens
AGENT_NAMES = {"hermes": "Monday's agent", "computer": "Monday's own hands", "claude_code": "Claude Code", "codex": "Codex", "gemini_cli": "Gemini CLI",  # (no "Hermes" in the UI: the owner, 2026-10-20)
               "kilo": "Kilo Code", "grok_build": "Grok Build", "fastest": "Whichever is ready and fastest"}
def who_words(s):
    """The Who works button's words (under the box): computer work · code, in short names; N/A where Core didn't say."""
    short = lambda v: {"computer": "Monday", "fastest": "Fastest"}.get(v) or AGENT_NAMES.get(v) or str(v).replace("_", " ").title()  # noqa: E731
    comp, code = (s or {}).get("default_computer_agent"), (s or {}).get("default_coding_agent")
    return f"{short(comp) if comp else 'N/A'} · {short(code) if code else 'N/A'} ▾"


STAGE_WORDS = {"unavailable": "Mic: speech recognition isn't available", "blocked": "Mic: blocked by a Windows switch", "closed": "Mic: off",
               "open": "Mic: open, no capture seen yet", "capturing": "Mic: capturing, no speech yet",
               "speech": "Mic: speech detected, no words yet", "partial": "Mic: hearing words", "final": "Mic: hearing sentences"}


# -- pure parts (the self-check runs these without a window) ------------------------------------------------------------------------
def sse_events(lines):
    """Server-sent events -> dicts. Monday's stream has no `event:` lines; each `data:` line is one JSON event (core/app.py _sse)."""
    for line in lines:
        if line.startswith("data:"):
            try:
                yield json.loads(line[5:].strip())
            except ValueError:
                continue


def mjpeg_frames(chunks, limit=FRAME_MAX):
    """multipart/x-mixed-replace bytes -> each JPEG, found by its start and end markers (the boundary headers are skipped)."""
    buf = b""
    for chunk in chunks:
        buf += chunk
        while True:
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
            if a < 0 or b < 0:
                if len(buf) > limit:
                    buf = b""
                break
            yield buf[a:b + 2]
            buf = buf[b + 2:]


def perm_question(p):
    """A permission request as the owner reads it (the workspace's wording): "Allow Hermes to run a command?"."""
    title = p.get("title") or "Approve this action?"
    if " wants to " in title:
        who, what = title.split(" wants to ", 1)
        return f"Allow {who} to {what.rstrip('.?')}?"
    return title


def mic_line(st, ptt=None):
    """GET /api/stt -> the microphone line: the furthest step Windows has proven (core/stt.py stage), never "listening" alone.
    ptt: whether push-to-talk has its key (PushToTalk; None before it started); said while the mic is off."""
    if not st:
        return "Mic: N/A (Core isn't answering)"
    words = STAGE_WORDS.get(st.get("stage"), f"Mic: {st.get('stage') or 'N/A'}")
    if st.get("stage") == "closed" and ptt is not None:
        words += " · hold Ctrl+Space to talk" if ptt else " · push-to-talk is off: another app holds Ctrl+Space"
    return words


def speakable(text, limit=SPEAK_MAX):
    """What is said of a reply: no code blocks or markdown marks, whole sentences up to `limit` characters."""
    clean = re.sub(r"\s+", " ", re.sub(r"[`*_#>•]", "", re.sub(r"```[\s\S]*?(?:```|$)", " ", str(text or "")))).strip()
    if len(clean) <= limit:
        return clean
    out = ""
    for s in re.findall(r"[^.!?]+[.!?]+(?:\s|$)", clean) or [clean]:
        if len(out + s) > limit:
            break
        out += s
    return (out or clean[:limit]).strip()


def to_say(m, asked, mode):
    """A reply Monday should say out loud, or None. Only replies to what was sent from this window (asked: user message id ->
    {"spoken", "said"}), once each. Assistant mode: every such reply, briefly. The silent modes (pure agent, debug): only the answer to a
    question asked out loud (meta.answer), never an action seen to work (meta.quiet)."""
    meta = m.get("meta") or {}
    cmd = asked.get(meta.get("reply_to"))
    if m.get("role") != "monday" or not cmd or cmd.get("said") == m.get("id"):
        return None
    kind, assistant = m.get("kind"), mode == "assistant"
    if kind == "permission":
        text = f"I need your permission: {m.get('text')}" if assistant else None
    elif kind not in ("text", "error", "completion") or (meta.get("quiet") and kind != "error"):
        text = None
    elif assistant or (meta.get("answer") and cmd.get("spoken")):
        text = meta.get("speak") or speakable(m.get("text"))
    else:
        text = None
    if text:
        cmd["said"] = m.get("id")
    return text or None


MODES = (("pure_agent", "Silent agent: acts, and speaks only to answer what you asked out loud"),
         ("debug", "Debug: silent too"),
         ("assistant", "Assistant: says its replies out loud"))  # core/voice.py MODES, as the owner reads them
APPROVAL_MODES = (("ask", "Approve or Deny, every time"),
                  ("always", "Approve, Deny, or Always allow a bot's routine step"),
                  ("auto", "Auto mode: routine steps go ahead, risky ones still ask"))  # router.APPROVAL_MODES, as the owner reads them
WHERE = {"auto": "Monday decides", "cloud": "cloud", "local": "this PC"}


def model_choices(data):
    """GET /api/models -> [(id, words, selected, detail)] in the registry's order; one that isn't there says so."""
    out = []
    for m in (data or {}).get("models") or []:
        name, prov = m.get("name") or m.get("id"), m.get("provider_label") or ""
        label = name if not prov or prov in ("Monday", name) else f"{prov} {name}"
        out.append((m.get("id"), f"{label} ({WHERE.get(m.get('where'), m.get('where') or 'N/A')})" + ("" if m.get("available") else ": not available"),
                    bool(m.get("selected")), m.get("detail") or ""))
    return out


def provider_choices(data):
    """GET /api/voice -> [(id, words)]: who can speak, each with the status Core measured."""
    return [(p.get("id"), f"{p.get('label') or p.get('id')}: {str(p.get('status') or 'N/A').replace('_', ' ')}")
            for p in (data or {}).get("providers") or []]


def voice_choices(data):
    """GET /api/voice -> [(id, short name, status words, chosen)]: the chat's Voice menu (2026-10-20, "add the TTS and a custom one too"):
    every speaker Core knows, the owner's own voice as "Your voice" (its long label cut at the bracket), with the status Core measured."""
    cur = ((data or {}).get("settings") or {}).get("tts_provider")
    return [(p.get("id"), (p.get("label") or p.get("id") or "").split(" (")[0], str(p.get("status") or "N/A").replace("_", " "), p.get("id") == cur)
            for p in (data or {}).get("providers") or []]


BROWSER_WORDS = {"mine": "your own browser", "yours": "Monday's Chrome"}  # the answers to "my Chrome, or your browser?"


def memory_rows(data):
    """GET /api/memory -> [(id, when, kind, words as shown, note, words)]: newest first; an old one says it may be out of date."""
    out = []
    for it in (data or {}).get("items") or []:
        at = it.get("at")
        when = time.strftime("%Y-%m-%d", time.localtime(at)) if isinstance(at, (int, float)) else str(at or "N/A")
        words = it.get("text") or ""
        out.append((it.get("id"), when, it.get("kind") or "", words + (" (may be out of date)" if it.get("stale") else ""), it.get("note") or "", words))
    return out


def project_rows(data):
    """GET /api/projects -> [(id, name, folder, also called)]."""
    return [(p.get("id"), p.get("name") or "", p.get("path") or "", p.get("aliases") or "") for p in (data or {}).get("projects") or []]


def learned_rows(data):
    """GET /api/learned -> [(what, why)]: only what Monday saw work (core/learn.py)."""
    d = data or {}
    rows = [(f"“{said}” means {a.get('app')}", f"{a.get('why') or 'seen to work'}; used {a.get('uses', 0)} times since")
            for said, a in sorted((d.get("apps") or {}).items())]
    rows += [(f"“{r.get('goal')}” in {r.get('app')}", f"{len(r.get('steps') or [])} steps, worked {r.get('wins', 0)} times") for r in d.get("recipes") or []]
    if (d.get("browser") or {}).get("default"):
        rows.append(("A web address opens in", f"{BROWSER_WORDS.get(d['browser']['default'], d['browser']['default'])} (your same answer three times)"))
    return rows


ALL_GROUPS, NO_GROUP = "all", "none"  # the Tasks tab's group filter, besides a group id


def _day(at):
    return time.strftime("%Y-%m-%d", time.localtime(at)) if isinstance(at, (int, float)) else "N/A"


def _organized(items, state, kind, group, archived):
    """Items of one kind with their pin / group / archive from core/side.py, kept when they pass the group filter (ALL_GROUPS, NO_GROUP or
    a group id) and the archive one: [(item, its meta, its group's name)]."""
    names = {g.get("id"): g.get("name") for g in (state or {}).get("groups") or []}
    meta = {str(m.get("ref")): m for m in (state or {}).get("meta") or [] if m.get("kind") == kind}
    out = []
    for t in items:
        m = meta.get(str(t.get("id"))) or {}
        gid = m.get("group_id")
        if bool(m.get("archived_at")) == archived and (group == ALL_GROUPS or (group == NO_GROUP and gid is None) or gid == group):
            out.append((t, m, names.get(gid, "")))
    return out


def todo_rows(state, group=ALL_GROUPS, archived=False):
    """GET /api/side (or SIDE_CHANGED) -> the owner's to-dos as dicts {kind, id, title, group, money, added, done, pinned}: pinned first,
    then newest; archived ones only when asked for (core/side.py keeps them)."""
    rows = [{"kind": "todo", "id": t.get("id"), "title": t.get("title") or "", "group": g, "money": bool(t.get("money")),
             "added": _day(t.get("created_at")), "done": bool(t.get("done_at")), "pinned": bool(m.get("pinned_at")), "at": t.get("created_at") or 0}
            for t, m, g in _organized((state or {}).get("todos") or [], state, "todo", group, archived)]
    rows.sort(key=lambda r: (not r["pinned"], -r["at"]))
    return rows


def task_rows(tasks, state, group=ALL_GROUPS, archived=False):
    """GET /api/tasks (the recent ones) and GET /api/side -> Monday's own tasks as dicts {kind, id, title, group, status, started, pinned},
    ordered as the to-dos are. An old task the owner pinned or archived comes from the sidebar's state; a deleted one is dropped."""
    gone, seen, items = set((state or {}).get("deleted") or []), set(), []
    for t in [*(tasks or []), *((state or {}).get("tasks") or [])]:
        if t.get("id") not in gone and t.get("id") not in seen:
            seen.add(t.get("id"))
            items.append(t)
    rows = [{"kind": "task", "id": t.get("id"), "title": t.get("title") or "", "group": g,
             "status": str(t.get("status") or "N/A").lower().replace("_", " "), "started": _day(t.get("created_at")),
             "pinned": bool(m.get("pinned_at")), "at": t.get("created_at") or 0} for t, m, g in _organized(items, state, "task", group, archived)]
    rows.sort(key=lambda r: (not r["pinned"], -r["at"]))
    return rows


def group_choices(state):
    """The groups as [(id, name)], oldest first (as Core keeps them)."""
    return [(g.get("id"), g.get("name") or "") for g in (state or {}).get("groups") or []]


def judge_words(r):
    """POST /api/todos/judge -> one line: who would do it (only when Jev is sure enough, core/side.py WHO_MIN) and the money hold."""
    who = {"monday": "Monday can do this", "owner": "This one is yours"}.get((r or {}).get("who"), "You choose who does it")
    return who + (" · it involves money, so Monday waits for your word" if (r or {}).get("money") else "") + \
        ("" if (r or {}).get("by") == "jev" else f" ({(r or {}).get('detail') or 'Jev decided nothing'})")


VERDICT = {"PASSED": "passed", "FAILED": "failed", "BLOCKED": "blocked", "CANCELLED": "cancelled", "RUNNING": "running"}


def recording_rows(data):
    """GET /api/recordings -> [(id, name, app, steps, last result)], newest first (as Core lists them)."""
    return [(r.get("id"), r.get("name") or "", r.get("target_app") or "", str(r.get("steps") or 0),
             "never run" if r.get("last_status") is None else VERDICT.get(r.get("last_status"), str(r.get("last_status")).lower()))
            for r in (data or {}).get("recordings") or []]


def recording_line(st):
    """core/recorder.py state() -> the line above the list: whether a workflow is being recorded right now."""
    if not (st or {}).get("active"):
        return "Not recording."
    n = st.get("steps") or 0
    return (f"Recording “{st.get('name')}”: {n} step{'' if n == 1 else 's'} so far" + (" (paused)" if st.get("paused") else "") +
            ". Do the workflow, then Stop and save.")


def step_rows(rec):
    """GET /api/recordings/{id} -> [(index, what it does, what is checked, ticked)]: an unticked step isn't replayed or checked."""
    return [(s.get("index"), (s.get("plan") or {}).get("label") or s.get("kind") or "", (s.get("expect") or {}).get("desc") or "",
             s.get("essential", True) is not False) for s in ((rec or {}).get("workflow") or {}).get("steps") or []]


def run_rows(rec):
    """A recording's runs, newest first: [(run id, when, result, checks, time)]."""
    out = []
    for r in (rec or {}).get("runs") or []:
        at, n, skipped = r.get("started_at"), (r.get("passed") or 0) + (r.get("failed") or 0), r.get("skipped") or 0
        out.append((r.get("id"), time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if isinstance(at, (int, float)) else "N/A",
                    VERDICT.get(r.get("status"), str(r.get("status") or "N/A").lower()),
                    f"{r.get('passed') or 0} of {n} passed" + (f", {skipped} left out" if skipped else ""),
                    f"{round(r['duration_ms'] / 1000, 1)} s" if isinstance(r.get("duration_ms"), (int, float)) else "N/A"))
    return out


def check_rows(run):
    """One run's checks, with their evidence: [(step, result, expected, actual)]; a blocked run says why first."""
    res = (run or {}).get("result") or {}
    rows = [(r.get("label") or "", r.get("status") or "", r.get("expected") or "",
             str(r.get("actual")) if r.get("actual") is not None else ("left out" if r.get("status") == "skipped" else "N/A"))
            for r in res.get("results") or []]
    return ([("Why it was blocked", "blocked", "", str(res["reason"]))] if res.get("reason") else []) + rows


def agent_choices(data):
    """GET /api/bots -> [(agent key, its name)]: who may do a bot's work ("auto": Monday decides), in Core's order."""
    return [(k, v) for k, v in ((data or {}).get("agents") or {}).items()]


BOT_COLOURS = tuple((c, "#FFFFFF", words) for c, words in (("#009957", "Green"), ("#377FE6", "Blue"), ("#D94B52", "Red"), ("#E78531", "Orange"),
                                                             ("#8057C8", "Purple"), ("#0EA5C6", "Cyan"), ("#D84F8B", "Pink"), ("#D8A729", "Yellow"),
                                                             ("#01A492", "Teal"), ("#E5634E", "Coral")))  # OpenMausBot's ten (MAUS_COLORS)


def bot_line(b):
    """A roster row's second line, from Core's `status` only: working on what, waiting for the owner, or at rest with its last task."""
    st = b.get("status") or {}
    last = st.get("last") or {}
    title = f" · {last['title']}" if last.get("title") else ""
    if st.get("state") == "waiting":
        return "Waiting for you" + title
    if st.get("state") == "working":
        return "Working" + title
    if not last:
        return "Idle · no tasks yet"  # (its chat answers aren't tasks: Core counts tasks)
    return ("Idle · last" + title + (" · failed" if last.get("status") == "FAILED" else "")
            + (f" · {last['tests']}" if last.get("tests") else ""))


def bot_mood(b):
    """Its face (core/notch.py Pet): working, waiting on the owner, sad after a failed last task, else at rest."""
    st = b.get("status") or {}
    return ("working" if st.get("state") == "working" else "waiting" if st.get("state") == "waiting" else
            "sad" if (st.get("last") or {}).get("status") == "FAILED" else "idle")


def roster_rows(data, show_hidden=False):
    """GET /api/bots (already in the roster's order: pinned first, hidden last) -> the rows shown: the hidden ones only when unfolded."""
    return [b for b in (data or {}).get("bots") or [] if show_hidden or not b.get("hidden")]


RESPONDER_WORDS = {"everyone": "Everyone answers", "mentions": "Only those @mentioned answer"}  # "lead": "Lead: Scout answers"


def responder_choices(room):
    """A room's ways to answer, as its picker says them (OpenMausBot's): its lead, everyone, only those @mentioned."""
    return [("lead", f"Lead: {(room or {}).get('lead') or 'the first bot'} answers"), *RESPONDER_WORDS.items()]


def room_hint(room):
    """The room composer's hint, as OpenMausBot's: who answers a message that names nobody, and how to bring a bot in."""
    r = room or {}
    who = {"lead": f"{r.get('lead') or 'its lead'} answers", "everyone": "everyone answers", "mentions": "only those you @mention answer"}
    return f"Message {r.get('name') or 'the room'}: {who.get(r.get('responder'), '')} · @ to bring a bot in, @everyone for all"


def speaker(m):
    """Who said a room's line: You, a bot by name, or Monday (a note of Monday's own)."""
    return "You" if m.get("role") == "user" else ((m.get("meta") or {}).get("bot") or "Monday")


def bot_pets():
    """The faces a bot can have: every pet but the owner's own pal (core/bots.py PET_KINDS, in the notch's order)."""
    from .notch import PETS
    return [k for k in PETS if k != "pal"]


def roster_match(b, q):
    """The roster's search (OpenMausBot's): a bot whose name, role or instructions hold the words."""
    q = (q or "").strip().lower()
    return not q or any(q in str(b.get(k) or "").lower() for k in ("name", "role", "instructions"))


def team_preview(d):
    """A team file read before it is imported -> (the team's name, its members' names, why it can't be imported or None). Core checks it again."""
    t = (d or {}).get("team") if isinstance(d, dict) else None
    if not isinstance(t, dict) or d.get("format") != "openmaus.team":
        return None, [], "That isn't an OpenMaus team file (.mausteam.json)."
    names = [str(m.get("name") or "?") for m in t.get("members") or [] if isinstance(m, dict)]
    return str(t.get("name") or "A team"), names, None if names else "That team file has no members."


def colour_index(look):
    """Which of BOT_COLOURS a face wears, or -1 (its own colours)."""
    return next((i for i, c in enumerate(BOT_COLOURS) if (c[0], c[1]) == ((look or {}).get("body"), (look or {}).get("accent"))), -1)


def bot_model_choices(models):
    """GET /api/models -> [(id, words)] a bot may answer with: Monday's own choice first, then the models that are there."""
    return [("", "Monday's own choice")] + [(m["id"], f"{m.get('name') or m['id']} · {WHERE.get(m.get('where'), m.get('where') or '')}".rstrip(" ·"))
                                            for m in (models or {}).get("models") or [] if m.get("available") and m.get("id") != "auto"]


def missing_templates(data):
    """The reference's bots (Scout, Pixel, Forge) the owner doesn't have yet, by name."""
    have = {(b.get("name") or "").lower() for b in (data or {}).get("bots") or []}
    return [t for t in ((data or {}).get("templates") or {}).values() if t.get("name", "").lower() not in have]


OPENABLE = ("coding agent", "editor", "computer agent", "agent")  # what POST /api/ai-tools/{key}/open takes: installed agents and editors
EXTRA_KEYS = ("TYPESAFE_API_KEY", "GOOGLE_TTS_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "FISH_AUDIO_API_KEY")  # Jev's, Google's
# voices, Cloudflare's browsers (research.py), Fish Audio's cloned voice (speech.py); each chat AI names its own key (Core refuses any other
# name: jev.ENV_KEYS)
TOOL_STATUS = {"available": "ready", "not_authenticated": "not signed in", "detected_unsupported": "found; Monday can't drive it yet",
               "unavailable": "not ready"}


def tool_rows(data):
    """GET /api/ai-tools -> [(key, name, kind, status in words, detail)]: every AI tool Monday found, as Core measured it."""
    return [(t.get("key"), t.get("name") or t.get("key") or "", t.get("kind") or "",
             "not installed" if t.get("installed") is False else TOOL_STATUS.get(t.get("status"), str(t.get("status") or "N/A").replace("_", " ")),
             t.get("detail") or "") for t in (data or {}).get("tools") or []]


def tool_buttons(t):
    """What a tool can offer here: {install, login, open, site, how} (Core's own setup facts, agents.SETUP)."""
    s, installed = (t or {}).get("setup") or {}, bool((t or {}).get("installed"))
    return {"install": bool(s.get("install")) and not installed, "login": bool(s.get("login")) and installed,
            "open": installed and (t or {}).get("kind") in OPENABLE, "site": s.get("site") or None,
            "remove": bool((t or {}).get("custom")),  # an engine the owner added (data/engines.json); the program itself stays
            "how": (s.get("login_how") if installed else s.get("install_how")) or ""}


def key_choices(data):
    """The keys Monday can be given here, [(name, words)]: each chat AI's own, then Jev's and the voices'; one already set says so."""
    names = list(dict.fromkeys([p.get("key_env") for p in (data or {}).get("providers") or [] if p.get("key_env")] + list(EXTRA_KEYS)))
    have = set((data or {}).get("keys") or [])
    return [(n, n + (" (set)" if n in have else "")) for n in names]


CAMERA_RECHECK_S = 4.5  # Core closes the camera ~2-4 s after its last viewer leaves (core/vision.py _close_soon): then its state is read again


def camera_line(st):
    """GET /api/vision -> one line: whether the camera view can run here, and the camera as Core reports it."""
    st = st or {}
    if not st.get("available"):
        return f"The camera view isn't available: {st.get('detail') or 'N/A'}"
    last = (st.get("last") or {}).get("text")
    return ("Camera on" if st.get("camera") == "on" else "Camera off") + (f" · {st['detail']}" if st.get("detail") else "") + \
        (f" · last look: {last}" if last else "")


def known_rows(st):
    """GET /api/vision -> what Monday knows by sight: [(label, views, taught, from)] (names and counts only: no pictures)."""
    return [(k.get("label") or "", str(k.get("views") or 0), _day(k.get("at")), k.get("from") or "") for k in (st or {}).get("known") or []]


GOOGLE_STATE = {"not_configured": "not set up (no OAuth client of your own yet)", "not_connected": "not connected", "connected": "connected",
                "expired": "the connection expired: connect again"}
GOOGLE_SERVICE = {"off": "off", "browser": "through Monday's Chrome", "connected": "connected", "expired": "expired",
                  "not_connected": "not connected"}


def google_line(st):
    """GET /api/google -> one line: its state, the account, the last error."""
    st = st or {}
    who = (st.get("account") or {}).get("email")
    return "Google: " + GOOGLE_STATE.get(st.get("state"), str(st.get("state") or "N/A").replace("_", " ")) + (f" as {who}" if who else "") + \
        (f" · {st['detail']}" if st.get("detail") else "")


def google_rows(st):
    """GET /api/google -> its services: [(id, name, status in words, what it does, enabled)]."""
    return [(s.get("id"), s.get("label") or s.get("id") or "", GOOGLE_SERVICE.get(s.get("status"), str(s.get("status") or "N/A")),
             s.get("does") or "", bool(s.get("enabled"))) for s in (st or {}).get("services") or []]


def hook_line(h):
    """A webhook in Settings' list: on or off, its words, how often it fired."""
    fired = (f"fired {h['runs']} time{'s' if h['runs'] != 1 else ''}, last {time.strftime('%d %b %H:%M', time.localtime(h['last_at']))}"
             if h.get("runs") and h.get("last_at") else "never fired")
    return f"{'On ' if h.get('enabled') else 'Off'} · {h.get('prompt') or h.get('name') or ''} · {fired}"


# OpenMausBot's routines (core/routines.py), as the Routines tab shows them
ROUTINE_DAYS = ((1, "Mon"), (2, "Tue"), (3, "Wed"), (4, "Thu"), (5, "Fri"), (6, "Sat"), (0, "Sun"))  # the editor's order; 0 is Sunday (OpenMausBot's)
RUN_MARKS = {"completed": "✓", "failed": "✗", "missed": "–", "cancelled": "–", "running": "…", "waiting": "!", "queued": "·"}


def routine_line(r):
    """A routine in the list: "Morning brief · Scout · Weekdays at 08:30 · next Tue 21 Oct 08:30", "off", or its bot gone."""
    nxt = time.strftime("next %a %d %b %H:%M", time.localtime(r["next_at"])) if r.get("enabled") and r.get("next_at") else \
        "" if r.get("enabled") else "off"
    return " · ".join(x for x in (r.get("name"), f"{r.get('bot')}" + (" (no such bot now)" if r.get("bot_missing") else ""), r.get("when"), nxt) if x)


def run_line(run):
    """A receipt: "Mon 20 Oct 08:30 · ✓ Morning brief · Scout · Three emails need you."."""
    what = " ".join(str((run.get("output") if run.get("status") == "completed" else run.get("error")) or run.get("status") or "").split())
    return (f"{time.strftime('%a %d %b %H:%M', time.localtime(run['scheduled_for']))} · {RUN_MARKS.get(run.get('status'), '·')} "
            f"{run.get('name')} · {run.get('bot')}" + (f" · {what[:120]}" if what else ""))


def routine_body(name, bot, prompt, once, when, hhmm, days, minutes, enabled):
    """The editor's fields as Core's RoutineIn: once at "2026-10-21 09:00" (this computer's clock), or every day at "08:30" on the ticked
    weekdays. ValueError for a date or a time it can't read."""
    if once:
        try:
            sched = {"type": "once", "at": datetime.strptime(str(when).strip(), "%Y-%m-%d %H:%M").timestamp()}
        except ValueError:
            raise ValueError("Write the date and time as 2026-10-21 09:00")
    elif not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(hhmm).strip()):
        raise ValueError("Write the time as 08:30")
    else:
        sched = {"type": "daily", "time": str(hhmm).strip(), "weekdays": sorted(days)}
    return {"name": str(name).strip(), "bot": str(bot or "").strip(), "prompt": str(prompt).strip(), "enabled": bool(enabled),
            "minutes": int(minutes), "schedule": sched}


def week_blocks(view, w, h, head):
    """The week's calendar (OpenMausBot's): 7 columns from view["start"], each day's 24 hours down the height under the header; the
    receipts, coloured by how they went, and the times still to come ("ahead", outlined). -> [(x, y, width, height, status, name)]."""
    start, colw, scale, out = view["start"], w / 7.0, (h - head) / 86400.0, []
    items = [(r["scheduled_for"], r.get("minutes") or 30, r.get("status"), r.get("name")) for r in view.get("runs") or []] + \
        [(a["at"], a.get("minutes") or 30, "ahead", a.get("name")) for a in view.get("ahead") or []]
    for at, minutes, status, name in items:
        day = int((at - start) // 86400)
        if 0 <= day < 7:  # ponytail: a day is 86400 s, so on a clock change a block sits an hour off; per-day midnights if it matters
            out.append((day * colw + 2, head + (at - start - day * 86400) * scale, colw - 4, max(14.0, minutes * 60 * scale), status, name))
    return out


def is_echo(heard, spoken):
    """Is a sentence heard while Monday talks just Monday's own voice, picked up by the microphone?"""
    words = re.findall(r"[a-z0-9']+", (heard or "").lower())
    said = set(re.findall(r"[a-z0-9']+", (spoken or "").lower()))
    return not words or sum(w in said for w in words) / len(words) >= ECHO_SHARE


def picture_point(box_w, box_h, img_w, img_h, x, y):
    """A click on a zoomed picture -> (fx, fy) fractions of the frame, or None outside it (the letterbox)."""
    if not (box_w and box_h and img_w and img_h):
        return None
    s = min(box_w / img_w, box_h / img_h)
    ox, oy = (box_w - img_w * s) / 2, (box_h - img_h * s) / 2
    fx, fy = (x - ox) / (img_w * s), (y - oy) / (img_h * s)
    return (fx, fy) if 0 <= fx <= 1 and 0 <= fy <= 1 else None


# Debug mode's reflex strip (the old HUD's): Jev's read of a sentence while it is said. Each question in plain words, and its verdict word at or
# above its gate and below it. The gate is Core's (`gates`, same name; `addressed` is drawn against addressed_filming in filming mode, which
# also asks `narrating`)
REFLEX_QUESTIONS = (("complete", "Finished sentence", ("finished", "waiting")), ("addressed", "Said to me", ("to me", "not me")),
                    ("stakes", "Would send, delete or spend", ("confirm", "clear")), ("narrating", "Talking to a camera", ("narration", "clear")))
REFLEX_WHY = {"not_configured": "Not configured: Jev has no key (TYPESAFE_API_KEY), so nothing is judged.",
              "off": "Off: routing is set to rules only, so Jev isn't asked.", "offline": "Offline: Jev isn't answering right now.",
              "timeout": "Failed: Jev was too slow for this sentence.", "error": "Failed: Jev returned an error for this sentence.",
              "rule": "A hard rule (stop, pause, filming mode): no Jev asked.", "empty": "N/A: no words yet."}


def reflex_view(r, err, turn):
    """POST /api/prepass's answer -> the strip: {"rows": [(words, value, gate, verdict, receipt)], "pill", "tone", "text"}. Every number is
    Core's: Jev's probability (status ok, a cached read too) or None, drawn as N/A, against Core's own gate. A value that crossed its gate since
    this sentence's last read keeps a receipt ("35% → 97%"). r and err None: no read yet (none, or the sentence's first is on its way); err:
    Core didn't answer (Core.call's words). turn: this sentence's {"cost", "prev", "receipts"}, carried from read to read and updated here."""
    r = r or {}
    st, film, gates = r.get("status"), bool(r.get("filming")), r.get("gates") or {}
    nums = (r.get("numbers") or {}) if st == "ok" else {}
    rows = []
    for q, words, (yes, no) in REFLEX_QUESTIONS:
        if q == "narrating" and not film:
            continue
        v, g = nums.get(q), gates.get("addressed_filming" if q == "addressed" and film else q)
        if v is not None:
            prev = turn["prev"].get(q)
            if g is not None and prev is not None and (prev >= g) != (v >= g):
                turn["receipts"][q] = f"{prev:.0%} → {v:.0%}"
            turn["prev"][q] = v
        rows.append((words, v, g, None if v is None or g is None else yes if v >= g else no, turn["receipts"].get(q) if v is not None else None))
    if st == "ok":
        c = r.get("cost_usd")
        turn["cost"] = None if c is None or turn["cost"] is None else turn["cost"] + c
        took = "cached" if r.get("cached") else f"{r['latency_ms']:.0f} ms" if r.get("latency_ms") is not None else "time N/A"
        cost = "cost N/A" if turn["cost"] is None else f"${turn['cost']:.6f} this sentence"
        pill, tone = f"{r.get('model') or 'Jev'} · {len(nums)} question{'s' if len(nums) != 1 else ''} · {took} · {cost}", "ok"
    elif err or st is None:
        pill, tone = (f"Failed: {err}", "bad") if err else ("N/A: no read yet.", "off")
    else:
        pill = REFLEX_WHY.get(st, f"N/A: Jev said {st}.") + (f" {r['error']}" if st == "error" and r.get("error") else "")
        tone = "bad" if st in ("offline", "timeout", "error") else "ok" if st == "rule" else "off"
    text = "; ".join(f"{w}: " + ("N/A" if v is None else f"{v:.0%}" + (f", gate {g:.0%}" if g is not None else "") + (f", {word}" if word else "")
                                 + (f", {rc}" if rc else "")) for w, v, g, word, rc in rows)
    return {"rows": rows, "pill": pill, "tone": tone, "text": f"{pill.rstrip('.')}. {text}"}


# -- Core --------------------------------------------------------------------------------------------------------------------------
class Unsent(str):
    """A call's words when it never reached Core (refused, or no connection in time): the same words as before, marked as safe to send
    again. qtwindow.Bridge holds these while the desktop app is still starting Core (2026-10-28: at sign-in the pages asked a Core that
    wasn't up yet and kept "… couldn't be read: Monday Core is unreachable." after it came up)."""
    unsent = True


class Core:
    """Monday Core's API. Errors come back as (None, words): the window says them, never hides them."""

    def __init__(self, base=BASE):
        self.base = base
        app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Core's own rule for its database: MONDAY_DB, a relative one in app/
        db = os.environ.get("MONDAY_DB") or os.path.join(app, "data", "monday.db")
        self.token_file, self.token = os.path.join(app, os.path.dirname(db), "window.token"), (None, "")  # (its mtime, the token)
        self.http = httpx.Client(base_url=base, timeout=httpx.Timeout(15.0, connect=3.0), event_hooks={"request": [self._token]})

    def _token(self, request):
        """Core's per-boot token (window.token beside its database), sent on every call: it tells Core an Approve, a Deny or a setup
        came from this window (and the notch, which shares this client), not from another program on the PC. Read again when Core
        restarts (the file's mtime); no file (a Core from before it) sends nothing."""
        try:
            at = os.stat(self.token_file).st_mtime_ns
            if at != self.token[0]:
                with open(self.token_file, encoding="utf-8") as f:
                    self.token = (at, f.read().strip())
        except OSError:
            return
        if self.token[1]:
            request.headers["x-monday-window"] = self.token[1]

    def call(self, method, path, body=None, timeout=None):
        """timeout: seconds for a call that waits on something slow (opening an editor waits for its window); else the client's."""
        try:
            r = self.http.request(method, path, json=body, **({"timeout": httpx.Timeout(timeout, connect=3.0)} if timeout else {}))
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:  # never reached Core (nothing listening yet): Unsent, safe to send again
            return None, Unsent("Monday Core didn't answer in time." if isinstance(e, httpx.ConnectTimeout) else "Monday Core is unreachable.")
        except httpx.TimeoutException:
            return None, "Monday Core didn't answer in time."
        except httpx.HTTPError:
            return None, "Monday Core is unreachable."
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = None
        if r.status_code >= 400:
            why = (data or {}).get("detail") or (data or {}).get("error") if isinstance(data, dict) else None
            return None, why if isinstance(why, str) else f"HTTP {r.status_code}"
        return data, None

    def events(self, since, on_event, on_state, stop):
        """The one live stream (GET /api/events), reconnected with ?since= so nothing is missed. Runs until stop() is true."""
        last, down_since = since, time.time()  # down since: when the stream was last lost (None while it is up)
        while not stop():
            try:
                url = f"/api/events?since={last}" if last is not None else "/api/events"
                with self.http.stream("GET", url, timeout=httpx.Timeout(10.0, read=STREAM_READ_S)) as r:  # (a ReadTimeout is an HTTPError)
                    r.raise_for_status()
                    on_state("ONLINE")
                    down_since = None
                    for ev in sse_events(r.iter_lines()):
                        if stop():
                            return
                        if ev.get("id") is not None:
                            last = ev["id"]
                        on_event(ev)
            except httpx.HTTPError:
                pass
            down_since = down_since or time.time()  # (2026-10-19: it was the stream's first start, so every later drop said Offline at once)
            on_state("OFFLINE" if time.time() - down_since > OFFLINE_AFTER_S else "RECONNECTING")
            time.sleep(RETRY_S)


class Ear:
    """The microphone: one session per sentence through Core's ear (core/stt.py; Monday's own Whisper by default): POST /api/stt/start,
    the session's words on GET /api/stt/events, opened again when one ends while the owner has the mic on. The final words are the
    sentence; they go to Core as a command with the latency marks and the sentence's id. While the owner is still talking, the words so
    far go to Core's early execution (POST /api/early, Monday.early): an exact, safe first clause ("open YouTube and …") starts at once and
    isn't run again when the sentence ends. A pause doesn't end a sentence here: the ear's endpointing (core/hear.py) does. Nothing heard
    is kept here."""

    def __init__(self, core, say, heard, send, woke=None):
        self.core, self.say, self.heard, self.send = core, say, heard, send
        self.woke = woke or (lambda carried: None)  # the ear heard "okay Monday" (core/hear.py): the window says "Yes?" at once
        self.on, self.sid, self.page = False, None, f"native-{uuid.uuid4().hex[:12]}"
        self.lost, self.lost_at = False, 0.0  # a lasting error turned the mic off (not the owner): `back` turns it on again when it can
        self.thread = None
        self.words, self.still = "", 0.0  # the open sentence's words so far, and since when they have held still
        self.held, self.ptt_sid = False, None  # push-to-talk: the key is down; its sentence's session
        self.gen, self.resume = 0, False       # which open-mic loop is the live one; reopen the mic after push-to-talk

    # push-to-talk (PushToTalk, from any app): one sentence while the key is held
    def hold(self):
        if self.held:
            return
        self.resume = self.on  # the open mic pauses while the key is held and comes back after ("always listening", the owner)
        if self.on:
            self.stop()
        self.held = True
        threading.Thread(target=self._ptt, daemon=True).start()

    def release(self):
        """Let go: the sentence ends now, with its words (POST /api/stt/finish). On PushToTalk's thread."""
        self.held = False
        if self.ptt_sid:
            self.core.call("POST", "/api/stt/finish", {"session": self.ptt_sid})

    def _ptt(self):
        sid, marks = uuid.uuid4().hex, {"stt_start": time.time() * 1000}
        marks["stt_held"] = marks["stt_start"]  # push-to-talk: said to Monday on purpose, so it needs no name (Core's room rule)
        try:
            with self.core.http.stream("GET", f"/api/stt/events?session={sid}", timeout=httpx.Timeout(10.0, read=None)) as r:
                r.raise_for_status()
                _, err = self.core.call("POST", "/api/stt/start", {"session": sid, "hold": True})  # the stream listens first
                if err:
                    self.say(f"Mic: {err}")
                self.ptt_sid = sid
                if not self.held:  # let go while it opened: a tap ends at once
                    self.core.call("POST", "/api/stt/finish", {"session": sid})
                self._events(r, sid, marks, lambda: True)
        except httpx.HTTPError:
            self.say("Mic: Monday Core stopped answering the microphone stream.")
        finally:
            self.ptt_sid = None
            if self.resume and not self.held:
                self.resume = False
                self.start()

    def _events(self, r, sid, marks, live):
        """One sentence's events from Core's ear (the open mic's and push-to-talk's): words shown as they come, the final words sent
        as a command with their marks. Ends at the ear's `end`, or when live() turns false."""
        for ev in sse_events(r.iter_lines()):
            k = ev.get("type")
            now = time.time() * 1000
            if k == "start":
                marks["stt_open"] = now
            elif k == "speech":  # a phrase begins: the owner is talking, so nothing is "holding still"
                marks.setdefault("stt_speech", now)
                self.still = time.monotonic()
                self.heard("", False)
            elif k == "wake":  # "okay Monday", heard on this PC: what follows is for Monday
                marks.setdefault("stt_speech", now)
                self.woke(bool(ev.get("carried")))
            elif k == "partial" and ev.get("text"):
                marks.setdefault("stt_first_partial", now)
                marks["stt_last_word"] = now
                if ev["text"] != self.words:
                    self.words, self.still = ev["text"], time.monotonic()
                self.heard(ev["text"], False)
            elif k == "final" and ev.get("text"):
                marks.setdefault("stt_first_partial", now)
                marks["stt_last_word"] = marks["stt_final"] = now
                self.words = ""  # the sentence is whole: no more early reads of it
                self.heard(ev["text"], True)
                if ev.get("note"):  # Google was asked and didn't answer: Whisper read some of it (core/hear.py), and that is said
                    self.say(f"Mic: {ev['note']}")
                self.send(ev["text"], dict(marks), sid)
            elif k == "error":
                self.say(f"Mic: {ev.get('message') or ev.get('code')}")
                if ev.get("code") in LASTING:  # audio-capture: the ear retries the mic by itself; unavailable: its engine exited or can't start
                    self.on, self.lost = False, ev.get("code") in ("audio-capture", "unavailable")  # (a Core reload: back when Core answers again)
                    self.lost_at = time.monotonic()
            elif k == "end":
                break
            if not live():
                break

    def _early(self, sid):
        """Core's early execution for this sentence while it is open: the words so far, when they change and once they hold still.
        When Core says they are a one-step exact command (`exact`) and they held still EXACT_END_MS, the sentence ends now."""
        sent = None
        while self.on and self.sid == sid:
            time.sleep(EARLY_TICK_S)
            text, held = self.words, int((time.monotonic() - self.still) * 1000)
            key = (text, held >= EARLY_HOLD_MS, held >= EXACT_END_MS)
            if not text or self.sid != sid or key == sent:
                continue
            sent = key
            r, _ = self.core.call("POST", "/api/early", {"text": text, "utterance": sid, "held": held})
            if r and r.get("exact") and held >= EXACT_END_MS and self.sid == sid and text == self.words:
                self.core.call("POST", "/api/stt/finish", {"session": sid})
                return

    def back(self, force=False):
        """Listen again when a lasting error turned the mic off (never when the owner did): at once on Core's MICROPHONE event (core/stt.py
        Local.on_microphone: the ear tried the microphone again by itself, hear.MIC_RETRY_S), and from the window's status poll once Core
        says the ear is available again after EAR_BACK_S (a Core reload ends the ear's engine: "unavailable … exited", 2026-10-28)."""
        if self.lost and not self.on and (force or time.monotonic() - getattr(self, "lost_at", 0.0) >= EAR_BACK_S):
            self.lost = False
            self.start()

    def start(self):
        if self.on:
            return
        self.on, self.lost = True, False
        self.gen += 1  # a loop from before a stop sees it isn't the live one any more and ends
        self.thread = threading.Thread(target=self._run, args=(self.gen,), daemon=True)
        self.thread.start()
        threading.Thread(target=self._beat, args=(self.gen,), daemon=True).start()

    def stop(self):
        self.on = False
        sid, self.sid = self.sid, None
        if sid:
            threading.Thread(target=self.core.call, args=("POST", "/api/stt/stop", {"session": sid}), daemon=True).start()
        threading.Thread(target=self.core.call, args=("POST", "/api/voice", {"state": "idle", "page": self.page}), daemon=True).start()

    def _beat(self, gen=0):
        while self.on and self.gen == gen:
            r, _ = self.core.call("POST", "/api/voice", {"state": "listening", "page": self.page})
            if r and r.get("duplicate"):  # another Monday window holds the ear: one ear (core/app.py voice)
                self.on = False
                self.say("Mic: another Monday window is listening; this one stood down.")
                return
            time.sleep(VOICE_BEAT_S)

    def _run(self, gen=0):
        live = lambda: self.on and self.gen == gen  # noqa: E731
        while live():
            sid = self.sid = uuid.uuid4().hex
            marks = {"stt_start": time.time() * 1000}
            self.words, self.still = "", time.monotonic()
            try:
                with self.core.http.stream("GET", f"/api/stt/events?session={sid}", timeout=httpx.Timeout(10.0, read=None)) as r:
                    r.raise_for_status()
                    _, err = self.core.call("POST", "/api/stt/start", {"session": sid})  # the stream listens first: no word is lost
                    if err:
                        self.say(f"Mic: {err}")
                    threading.Thread(target=self._early, args=(sid,), daemon=True).start()
                    self._events(r, sid, marks, live)
            except httpx.HTTPError:
                self.say("Mic: Monday Core stopped answering the microphone stream.")
                time.sleep(RETRY_S)
            if self.sid == sid:
                self.core.call("POST", "/api/stt/stop", {"session": sid})
            time.sleep(EAR_REOPEN_S)


def mci(cmd):
    """One command to Windows' media player API (winmm MCI): (error code, its answer)."""
    buf = ctypes.create_unicode_buffer(128)
    return ctypes.windll.winmm.mciSendStringW(cmd, buf, 128, None), buf.value


def play_elsewhere(path, control):
    """macOS / Linux (2026-10-19): the OS's own player (afplay; paplay or aplay), stopped when asked; pause isn't offered there."""
    import subprocess
    for cmd in ([["afplay", path]] if sys.platform == "darwin" else [["paplay", path], ["aplay", "-q", path]]):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            continue
        while p.poll() is None:
            if control() == "stop":
                p.terminate()
                return None
            time.sleep(0.05)
        return None if p.returncode == 0 else f"The audio player stopped (exit {p.returncode})."
    return "No audio player on this computer (afplay, paplay or aplay)."


def play(path, control):
    """Play a WAV file to its end; control() says "stop", "pause" or None while it plays. An error in words, or None.
    WAV only: Windows' MPEG device failed to load on this PC (MCI 266), the WAV one is always there; every voice gives WAV."""
    if sys.platform != "win32":
        return play_elsewhere(path, control)
    alias = f"monday{uuid.uuid4().hex[:10]}"
    err, _ = mci(f'open "{path}" type waveaudio alias {alias}')
    if err:
        return f"Windows couldn't open the audio (MCI error {err})."
    try:
        mci(f"play {alias}")
        started, t0, paused = False, time.monotonic(), False
        while True:
            err, mode = mci(f"status {alias} mode")
            if err:
                return f"Windows stopped playing the audio (MCI error {err})."
            started = started or mode == "playing"
            if mode not in ("playing", "paused", "seeking") and (started or time.monotonic() - t0 > 2):
                return None
            want = control()
            if want == "stop":
                mci(f"stop {alias}")
                return None
            if (want == "pause") != paused:
                paused = want == "pause"
                mci(f"{'pause' if paused else 'resume'} {alias}")
            time.sleep(0.05)
    finally:
        mci(f"close {alias}")


def wav_seconds(path):
    """How long a WAV file plays, in seconds; None when it can't be read."""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return None


def drop_file(path):
    try:
        os.unlink(path)
    except OSError:  # still held a moment after playing: the temp folder is cleaned by Windows
        pass


def windows_speech():
    if PYLIB not in sys.path:
        sys.path.append(PYLIB)
    import clr
    clr.AddReference("System.Speech, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")  # .NET Framework: the GAC's full name
    from System.Speech.Synthesis import SpeechSynthesizer
    return SpeechSynthesizer()


def os_voice_cmd(text, path, speed=1.0, platform=None, which=None):
    """macOS / Linux (2026-10-19): "this computer's voices" as the OS speaks them, to a WAV: macOS' say, Linux's espeak-ng or espeak (their
    default voice; the voice list is Windows' only). -> the command, or None when there is no voice program."""
    import shutil
    platform, which = platform or sys.platform, which or shutil.which
    wpm = str(max(80, min(400, int(175 * speed))))
    if platform == "darwin" and which("say"):
        return ["say", "-r", wpm, "-o", path, "--data-format=LEI16@22050", text]
    exe = which("espeak-ng") or which("espeak")
    return [exe, "-s", wpm, "-w", path, text] if exe else None


def os_voice(text, speed=1.0):
    """os_voice_cmd, run: (path, None) or (None, why not)."""
    import subprocess
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="monday-say-")
    os.close(fd)
    cmd = os_voice_cmd(text, path, speed)
    if not cmd:
        drop_file(path)
        return None, "No voice program on this computer to speak with (Linux: install espeak-ng)."
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        drop_file(path)
        return None, f"This computer's voice failed: {e}"
    if r.returncode:
        drop_file(path)
        return None, f"This computer's voice failed (exit {r.returncode})."
    return path, None


def windows_voices():
    """The voices installed in Windows, as POST /api/voices takes them (the "this computer" voices)."""
    s = windows_speech()
    try:
        default = s.Voice.Name
        return [{"name": v.VoiceInfo.Name, "lang": v.VoiceInfo.Culture.Name, "localService": True, "voiceURI": v.VoiceInfo.Name,
                 "default": v.VoiceInfo.Name == default} for v in s.GetInstalledVoices() if v.Enabled]
    finally:
        s.Dispose()


class Speaker:
    """Monday out loud: the voice Monday Core holds (settings.voice), one piece at a time. A server voice (the owner's own, Gemini,
    Google Cloud) is POST /api/tts's audio; tts_provider "browser" ("this computer's voices") is a Windows voice (System.Speech).
    A voice that fails is said on screen and nothing speaks in its place (the owner, 2026-10-13: one voice, theirs).
    ponytail: a reply is said once it is complete, not sentence by sentence as it streams; stream it when time to first audio matters.
    Pitch isn't applied to Windows voices (SAPI has none outside SSML)."""

    def __init__(self, core, on_error, on_state):
        self.core, self.on_error, self.on_state = core, on_error, on_state
        self.settings, self.jobs, self.want = {}, queue.Queue(), None
        self.text, self.until = "", 0.0  # what is being said, and until when the microphone may still hear it
        threading.Thread(target=self._run, daemon=True).start()

    def enabled(self):
        return self.settings.get("enabled") is not False and bool(self.settings.get("tts_provider"))

    def say(self, text, reply_to=None, preview=None, cue=None):
        """preview: {"provider", "voice_id"}, a voice tried in Settings before it is chosen (no other voice stands in for it). cue(seconds):
        called on this thread as the piece starts to play, with its length (None when it can't be read): a look's marks, drawn as their
        part is said."""
        self.jobs.put((text, reply_to, preview, cue))

    def control(self, op):
        """VOICE_CONTROL from Core ("stop talking", "pause", "resume") and Esc."""
        if op == "stop_speaking":
            while not self.jobs.empty():
                self.jobs.get_nowait()
            self.want = "stop"
        elif op in ("pause", "resume"):
            self.want = "pause" if op == "pause" else None

    def talking(self):
        return time.monotonic() < self.until

    def _run(self):
        while True:
            text, reply_to, preview, cue = self.jobs.get()
            try:
                self._one(text, reply_to, preview, cue)
            except Exception as e:  # the speaker thread outlives any one piece
                self.until = 0.0
                self.on_error(str(e))

    def _one(self, text, reply_to, preview=None, cue=None):
        self.want, marks = None, {"tts_start": time.time() * 1000}
        path, err = self._audio(text, preview)
        if err:
            self.on_error(err)
            return
        self.text, self.until = text, float("inf")
        self.on_state("speaking")
        if cue:
            cue(wav_seconds(path))
        marks["tts_first_audio"] = time.time() * 1000
        try:
            err = play(path, lambda: self.want)
        finally:
            self.until = time.monotonic() + 1.5  # the room's echo outlasts the audio a little
            drop_file(path)
            self.on_state("idle")
        marks["tts_end"] = time.time() * 1000
        if err:
            self.on_error(err)
        if reply_to:
            self.core.call("POST", "/api/latency", {"message_id": reply_to, "marks": marks})

    def _audio(self, text, preview=None):
        """The piece as an audio file: (path, None) or (None, why not)."""
        s = self.settings
        if ((preview or {}).get("provider") or s.get("tts_provider")) == "browser" and sys.platform != "win32":
            return os_voice(text, float(s.get("speed") or 1))
        if ((preview or {}).get("provider") or s.get("tts_provider")) == "browser":
            pick = {"voice_id": preview["voice_id"]} if preview and preview.get("voice_id") else (s.get("choices") or {}).get("browser") or s
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="monday-say-")
            os.close(fd)
            try:
                syn = windows_speech()
                try:
                    if pick.get("voice_id"):
                        syn.SelectVoice(pick["voice_id"])
                    syn.Rate = max(-10, min(10, round((float(s.get("speed") or 1) - 1) * 10)))
                    syn.SetOutputToWaveFile(path)
                    syn.Speak(text)
                finally:
                    syn.Dispose()
                return path, None
            except Exception as e:
                drop_file(path)
                return None, f"Windows couldn't speak in the voice {pick.get('voice_name') or pick.get('voice_id')}: {e}"
        try:
            body = {"text": text, **({"provider": preview.get("provider"), "voice_id": preview.get("voice_id")} if preview else {})}
            r = self.core.http.post("/api/tts", json=body, timeout=httpx.Timeout(60.0, connect=3.0))
        except httpx.HTTPError:
            return None, "Monday Core is unreachable."
        if r.status_code >= 400:
            try:
                why = r.json().get("detail")
            except ValueError:
                why = None
            return None, why if isinstance(why, str) else f"HTTP {r.status_code}"
        if "wav" not in r.headers.get("content-type", "") and r.content[:4] != b"RIFF":
            return None, f"the voice sent {r.headers.get('content-type') or 'audio'} and this window plays WAV only."
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="monday-say-")
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        return path, None


if __name__ == "__main__":
    if "--check" in sys.argv:
        import wave as _wave
        fd, _wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with _wave.open(_wav, "wb") as _w:  # how long a piece plays: a look's marks are drawn that far into it
            _w.setnchannels(1), _w.setsampwidth(2), _w.setframerate(16000), _w.writeframes(b"\x00\x00" * 24000)
        assert wav_seconds(_wav) == 1.5 and wav_seconds(_wav + ".gone") is None, wav_seconds(_wav)
        drop_file(_wav)
        evs = list(sse_events(["retry: 1000", "", ": ping", 'data: {"id": 3, "type": "HELLO", "data": {"name": "Monday"}}', "data: {broken"]))
        assert evs == [{"id": 3, "type": "HELLO", "data": {"name": "Monday"}}], evs
        jpg1, jpg2 = b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"
        stream = [b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg1[:4], jpg1[4:] + b"\r\n--frame\r\n\r\n" + jpg2]
        assert list(mjpeg_frames(stream)) == [jpg1, jpg2], "frames split across chunks are put together"
        assert list(mjpeg_frames([b"x" * 50], limit=10)) == []
        assert perm_question({"title": "Hermes wants to run a command."}) == "Allow Hermes to run a command?"
        assert perm_question({"title": "Delete the folder?"}) == "Delete the folder?"
        assert mic_line({"stage": "speech"}).startswith("Mic: speech detected") and mic_line(None).endswith("isn't answering)")
        assert all(k in STAGE_WORDS for k in ("unavailable", "blocked", "closed", "open", "capturing", "speech", "partial", "final"))
        from .stt import STAGES
        assert set(STAGE_WORDS) == set(STAGES), "every stage core/stt.py can report has words here"
        assert picture_point(200, 100, 100, 100, 100, 50) == (0.5, 0.5) and picture_point(200, 100, 100, 100, 10, 50) is None, "the letterbox"
        assert speakable("**Done.** ```code``` ok") == "Done. ok" and speakable("One. Two. Three.", 10) == "One. Two."
        asked = {5: {"spoken": True, "said": None}, 6: {"spoken": False, "said": None}}
        ans = {"id": 9, "role": "monday", "kind": "text", "text": "It's 3 pm.", "meta": {"reply_to": 5, "answer": True}}
        assert to_say(ans, asked, "pure_agent") == "It's 3 pm." and to_say(ans, asked, "pure_agent") is None, "said once"
        typed = {"id": 10, "role": "monday", "kind": "text", "text": "It's 3 pm.", "meta": {"reply_to": 6, "answer": True}}
        assert to_say(typed, asked, "pure_agent") is None and to_say(typed, asked, "assistant") == "It's 3 pm.", "silent: only spoken questions"
        quiet = {"id": 11, "role": "monday", "kind": "completion", "text": "Opened.", "meta": {"reply_to": 5, "quiet": True, "speak": "x"}}
        assert to_say(quiet, asked, "assistant") is None, "an action seen to work isn't narrated"
        assert to_say({**ans, "id": 12, "meta": {"reply_to": 99, "answer": True}}, asked, "assistant") is None, "another window's command"
        assert to_say({"id": 13, "role": "monday", "kind": "permission", "text": "Run it?", "meta": {"reply_to": 5}}, asked, "assistant") \
            == "I need your permission: Run it?"
        assert is_echo("it's three pm", "It's 3 pm. It's three pm.") and not is_echo("stop", "It's three pm.") and is_echo("", "x")
        mac = os_voice_cmd("Hi", "/tmp/a.wav", 1.2, "darwin", lambda n: "/usr/bin/say" if n == "say" else None)
        assert mac == ["say", "-r", "210", "-o", "/tmp/a.wav", "--data-format=LEI16@22050", "Hi"], mac
        assert os_voice_cmd("Hi", "/tmp/a.wav", 1.0, "linux", lambda n: "/usr/bin/espeak" if n == "espeak" else None)[:3] == \
            ["/usr/bin/espeak", "-s", "175"] and os_voice_cmd("Hi", "/tmp/a.wav", 1.0, "linux", lambda n: None) is None, "macOS / Linux voices"
        voices = windows_voices()
        assert voices and all(v["localService"] and v["voiceURI"] == v["name"] for v in voices), voices
        syn, wav = windows_speech(), os.path.join(tempfile.gettempdir(), f"monday-check-{uuid.uuid4().hex[:8]}.wav")
        try:
            syn.SetOutputToWaveFile(wav)
            syn.Speak("Check.")
        finally:
            syn.Dispose()
        alias = f"check{uuid.uuid4().hex[:8]}"
        assert os.path.getsize(wav) > 1000 and mci(f'open "{wav}" type waveaudio alias {alias}')[0] == 0, "Windows' player opens the audio"
        mci(f"close {alias}")
        drop_file(wav)
        print("speaking:", len(voices), "Windows voices; a Windows voice's audio opens in Windows' player (not played)")

        # the pages' words, from Core's answers as the window reads them (core/qtwindow.py shows these)
        import http.server
        SAMPLE = {
            "/api/settings": {"assistant_name": "Monday", "routing": "jev", "jev_images": True, "approval_mode": "always"},
            "/api/approvals": {"mode": "always", "grants": [{"who": "Codex", "kind": "RUN_COMMAND", "key": "npm test", "words": "Codex may run “npm test”"}]},
            "/api/rooms": {"rooms": [{"id": "r1", "name": "Desk", "members": ["Scout", "Old"], "responder": "lead", "lead": "Scout",
                                      "bulletin": "Be brief.", "at": 1.0, "line": "Scout: Ship the roster."}],
                           "responders": {"lead": "Its lead answers", "everyone": "Everyone answers", "mentions": "Only those @mentioned answer"}},
            "/api/webhooks": {"webhooks": [{"id": "h1", "name": "Scout, summarize this GitHub issue", "prompt": "Scout, summarize this GitHub issue",
                                            "enabled": False, "at": 1.0, "last_at": None, "runs": 0, "script": "C:\\nowhere\\h1.ps1"}]},
            "/api/models": {"models": [
                {"id": "auto", "provider": "monday", "provider_label": "Monday", "name": "Automatic", "where": "auto", "available": True,
                 "selected": True, "detail": "Ollama's cloud model first"},
                {"id": "anthropic", "provider": "anthropic", "provider_label": "Claude", "name": "Claude", "where": "cloud", "available": False,
                 "selected": False, "detail": "add ANTHROPIC_API_KEY"}]},
            "/api/voice": {"settings": {"tts_provider": "custom", "speed": 1.0, "enabled": True, "interaction_mode": "pure_agent",
                                        "choices": {"browser": {"voice_id": "Microsoft Zira Desktop"}}},
                           "providers": [{"id": "custom", "label": "Your voice", "status": "not_configured"},
                                         {"id": "browser", "label": "This computer's voices (Windows)", "status": "available"}]},
            "/api/voices": {"voices": [{"id": "Microsoft Zira Desktop", "name": "Microsoft Zira Desktop", "lang": "en-US", "number": 1}],
                            "others": [{"id": "Microsoft David Desktop", "name": "Microsoft David Desktop", "lang": "en-US", "gender": "male"}],
                            "error": None},
            "/api/memory": {"items": [
                {"id": "a1b2c3d4e5f60718", "at": time.mktime((2026, 10, 14, 9, 0, 0, 0, 0, -1)), "kind": "preference", "note": "Preferences",
                 "stale": False, "text": "Prefers dark mode"},
                {"id": "0f0e0d0c0b0a0908", "at": time.mktime((2026, 6, 1, 9, 0, 0, 0, 0, -1)), "kind": "decision", "note": "Decisions",
                 "stale": True, "text": "Uses Vite"}]},
            "/api/projects": {"projects": [{"id": 1, "name": "Monday Core", "path": "E:\\monday\\app", "aliases": "monday repo"}]},
            "/api/learned": {"apps": {"yputube": {"app": "YouTube", "why": "you then opened YouTube", "uses": 0}},
                             "recipes": [{"goal": "click photo", "app": "camera", "steps": [{}, {}], "wins": 2}], "browser": {"default": "yours"}}}
        assert memory_rows(SAMPLE["/api/memory"])[1] == ("0f0e0d0c0b0a0908", "2026-06-01", "decision", "Uses Vite (may be out of date)",
                                                          "Decisions", "Uses Vite"), "an old memory says it may be out of date"
        assert learned_rows(SAMPLE["/api/learned"]) == [("“yputube” means YouTube", "you then opened YouTube; used 0 times since"),
                                                        ("“click photo” in camera", "2 steps, worked 2 times"),
                                                        ("A web address opens in", "Monday's Chrome (your same answer three times)")]
        assert learned_rows({}) == [] and project_rows(SAMPLE["/api/projects"]) == [(1, "Monday Core", "E:\\monday\\app", "monday repo")]
        t0 = time.mktime((2026, 10, 1, 9, 0, 0, 0, 0, -1))
        SAMPLE["/api/side"] = {"groups": [{"id": 1, "name": "Work"}], "deleted": ["TASK-GONE"], "chat_title": "Main chat",
                               "tasks": [{"id": "TASK-OLD", "title": "Old pinned task", "status": "COMPLETED", "created_at": t0 - 99}],
                               "todos": [{"id": 3, "title": "Call the bank", "created_at": t0 + 2, "done_at": None, "money": 1},
                                         {"id": 4, "title": "Write the report", "created_at": t0 + 1, "done_at": t0 + 3, "money": 0},
                                         {"id": 5, "title": "Old thing", "created_at": t0, "done_at": None, "money": 0}],
                               "meta": [{"kind": "todo", "ref": "4", "pinned_at": t0, "group_id": 1, "archived_at": None},
                                        {"kind": "todo", "ref": "5", "pinned_at": None, "group_id": None, "archived_at": t0},
                                        {"kind": "task", "ref": "TASK-OLD", "pinned_at": t0, "group_id": 1, "archived_at": None},
                                        {"kind": "task", "ref": "TASK-A", "pinned_at": None, "group_id": None, "archived_at": t0}]}
        assert [r["id"] for r in todo_rows(SAMPLE["/api/side"])] == [4, 3], "pinned first, then newest; archived ones apart"
        assert [r["id"] for r in todo_rows(SAMPLE["/api/side"], archived=True)] == [5]
        SAMPLE["/api/tasks"] = {"tasks": [{"id": "TASK-B", "title": "Open Notepad", "status": "RUNNING", "created_at": t0 + 5},
                                          {"id": "TASK-A", "title": "Archived one", "status": "FAILED", "created_at": t0 + 4},
                                          {"id": "TASK-GONE", "title": "Deleted one", "status": "COMPLETED", "created_at": t0 + 3}]}
        tk, side_ = SAMPLE["/api/tasks"]["tasks"], SAMPLE["/api/side"]
        assert [r["id"] for r in task_rows(tk, side_)] == ["TASK-OLD", "TASK-B"], "pinned first (an old one, from the sidebar's state); a deleted one dropped"
        assert [r["id"] for r in task_rows(tk, side_, archived=True)] == ["TASK-A"]
        assert [r["id"] for r in task_rows(tk, side_, 1)] == ["TASK-OLD"] and [r["id"] for r in task_rows(tk, side_, NO_GROUP)] == ["TASK-B"]
        assert (task_rows(tk, side_)[1]["status"], task_rows(tk, side_)[0]["group"]) == ("running", "Work")
        assert [r["id"] for r in todo_rows(SAMPLE["/api/side"], 1)] == [4] and [r["id"] for r in todo_rows(SAMPLE["/api/side"], NO_GROUP)] == [3]
        assert todo_rows(SAMPLE["/api/side"])[0]["group"] == "Work" and todo_rows(SAMPLE["/api/side"])[0]["done"]
        SAMPLE["/api/recordings"] = {"recording": {"active": False, "paused": False, "name": None, "steps": 0, "started_at": None},
                                     "recordings": [{"id": "Wabc", "name": "Open Notepad and type", "target_app": "Notepad", "created_at": t0,
                                                     "steps": 2, "last_status": "FAILED"},
                                                    {"id": "Wdef", "name": "Never run", "target_app": "", "created_at": t0 - 9,
                                                     "steps": 1, "last_status": None}]}
        SAMPLE["/api/recordings/Wabc"] = {
            "id": "Wabc", "name": "Open Notepad and type", "workflow": {"name": "Open Notepad and type", "steps": [
                {"index": 1, "kind": "open", "essential": True, "plan": {"label": "Open Notepad"}, "expect": {"type": "window", "desc": "Notepad is open"}},
                {"index": 2, "kind": "type", "essential": True, "plan": {"label": "Type hello"}, "expect": {"type": "text", "desc": "the document says hello"}}]},
            "runs": [{"id": 9, "started_at": t0, "status": "FAILED", "passed": 1, "failed": 1, "skipped": 0, "duration_ms": 4200,
                      "result": {"status": "FAILED", "reason": None, "results": [
                          {"index": 1, "label": "Open Notepad", "expected": "Notepad is open", "status": "passed", "actual": "Untitled - Notepad"},
                          {"index": 2, "label": "Type hello", "expected": "the document says hello", "status": "failed", "actual": "hel"}]}}]}
        assert recording_rows(SAMPLE["/api/recordings"]) == [("Wabc", "Open Notepad and type", "Notepad", "2", "failed"),
                                                             ("Wdef", "Never run", "", "1", "never run")]
        assert recording_line({"active": True, "name": "Login", "steps": 1}) == "Recording “Login”: 1 step so far. Do the workflow, then Stop and save."
        assert recording_line({"active": False}) == "Not recording." and recording_line(None) == "Not recording."
        assert run_rows(SAMPLE["/api/recordings/Wabc"])[0][2:] == ("failed", "1 of 2 passed", "4.2 s")
        assert check_rows(SAMPLE["/api/recordings/Wabc"]["runs"][0])[1] == ("Type hello", "failed", "the document says hello", "hel"), "what was seen"
        assert check_rows({"result": {"reason": "Notepad isn't installed", "results": []}}) == [("Why it was blocked", "blocked", "", "Notepad isn't installed")]
        SAMPLE["/api/bots"] = {  # in the roster's order, as Core sends it: pinned first, hidden last
            "bots": [{"name": "Scout", "role": "Researcher", "instructions": "Find things out.", "agent": "auto", "project": None, "group_id": 2,
                      "model": None, "computer": True, "pinned": True, "hidden": False, "unread": 3,
                      "look": {"kind": "fox", "body": "#E78531", "accent": "#FFFFFF"},
                      "status": {"state": "working", "tasks": 2, "last": {"id": "TASK-9", "title": "Compare laptops", "status": "RUNNING"}}},
                     {"name": "Old", "role": "", "instructions": "", "agent": "codex", "project": None, "group_id": 3, "model": None,
                      "computer": False, "pinned": False, "hidden": True, "unread": 0, "look": {"kind": "maus", "body": "#377FE6", "accent": "#FFFFFF"},
                      "status": {"state": "idle", "tasks": 1, "last": {"id": "TASK-2", "title": "Fix login", "status": "FAILED",
                                                                       "tests": "Tests failed · 1 failed"}}}],
            "templates": {k: {"name": k.title(), "role": r, "agent": "auto", "instructions": f"{k} words"}
                          for k, r in (("scout", "Researcher"), ("pixel", "Designer"), ("forge", "Engineer"))},
            "kinds": ["blob", "bot", "cat", "fox", "gem", "maus"],
            "agents": {"auto": "Monday decides", "claude_code": "Claude Code", "codex": "Codex"}}
        sc, old = SAMPLE["/api/bots"]["bots"]
        assert [b["name"] for b in roster_rows(SAMPLE["/api/bots"])] == ["Scout"] and len(roster_rows(SAMPLE["/api/bots"], True)) == 2, \
            "a hidden bot is folded away until shown"
        assert (bot_line(sc), bot_mood(sc)) == ("Working · Compare laptops", "working")
        assert (bot_line(old), bot_mood(old)) == ("Idle · last · Fix login · failed · Tests failed · 1 failed", "sad"), "said as Core said it"
        assert bot_line({"status": {"state": "waiting", "last": {"title": "Pay the bill"}}}) == "Waiting for you · Pay the bill"
        assert bot_line({}) == "Idle · no tasks yet" and bot_mood({}) == "idle", "no status from Core: nothing invented"
        assert colour_index(sc["look"]) == 3 and colour_index({"body": "#123456", "accent": "#FFFFFF"}) == -1
        assert roster_match(sc, "research") and roster_match(sc, " FIND things ") and roster_match(sc, "") and not roster_match(sc, "zzz")
        room = SAMPLE["/api/rooms"]["rooms"][0]  # rooms (OpenMausBot's): who answers, the composer's hint, who said a line
        assert responder_choices(room) == [("lead", "Lead: Scout answers"), ("everyone", "Everyone answers"), ("mentions", "Only those @mentioned answer")]
        assert room_hint(room) == "Message Desk: Scout answers · @ to bring a bot in, @everyone for all"
        assert room_hint({**room, "responder": "mentions"}).startswith("Message Desk: only those you @mention answer")
        assert (speaker({"role": "user"}), speaker({"role": "monday", "meta": {"bot": "Scout"}}), speaker({"role": "monday"})) == ("You", "Scout", "Monday")
        assert team_preview({"format": "openmaus.team", "team": {"name": "Desk", "members": [{"name": "Scout"}, {"name": "Forge"}]}}) == \
            ("Desk", ["Scout", "Forge"], None)
        assert team_preview({"format": "x"})[2] and team_preview({"format": "openmaus.team", "team": {"name": "T", "members": []}})[2] and \
            team_preview([1])[2], "not a team file, or one with nobody in it: said before anything is sent"
        assert bot_model_choices({"models": [{"id": "auto", "available": True}, {"id": "gemma4", "name": "Gemma 4", "where": "local", "available": True},
                                             {"id": "anthropic", "name": "Claude", "where": "cloud", "available": False}]}) == \
            [("", "Monday's own choice"), ("gemma4", "Gemma 4 · this PC")], "only the models that are there"
        assert [t["name"] for t in missing_templates(SAMPLE["/api/bots"])] == ["Pixel", "Forge"], "one already there"
        assert agent_choices(SAMPLE["/api/bots"])[0] == ("auto", "Monday decides")
        SAMPLE["/api/ai-tools"] = {"keys": ["TYPESAFE_API_KEY"], "checked_at": t0, "engine_errors": {}, "tools": [
            {"key": "claude_code", "name": "Claude Code", "kind": "coding agent", "status": "not_authenticated", "installed": True, "detail": "log in",
             "setup": {"site": "https://claude.com/claude-code", "login": ["claude"], "login_how": "Type /login there.", "install": None}},
            {"key": "droid", "name": "Droid (Factory)", "kind": "coding agent", "status": "unavailable", "installed": False, "detail": "",
             "setup": {"site": "https://docs.factory.com/cli", "login": None, "install": "npm install -g droid", "install_how": None}},
            {"key": "kiro", "name": "Kiro", "kind": "editor", "status": "available", "installed": True, "detail": "1.0",
             "setup": {"site": "https://kiro.dev", "login": None, "install": None}},
            {"key": "chatgpt", "name": "ChatGPT", "kind": "app", "status": "detected_unsupported", "installed": True, "detail": "", "setup": {}}],
            "providers": [{"key": "ollama", "name": "Ollama", "status": "available"},
                          {"key": "anthropic", "name": "Claude", "status": "unavailable", "key_env": "ANTHROPIC_API_KEY"}]}
        assert [r[3] for r in tool_rows(SAMPLE["/api/ai-tools"])] == ["not signed in", "not installed", "ready", "found; Monday can't drive it yet"]
        tb = {t["key"]: tool_buttons(t) for t in SAMPLE["/api/ai-tools"]["tools"]}
        assert (tb["claude_code"]["login"], tb["claude_code"]["open"], tb["claude_code"]["install"], tb["claude_code"]["how"]) == \
            (True, True, False, "Type /login there."), "an installed agent: sign in and open, nothing to install"
        assert (tb["droid"]["install"], tb["droid"]["login"], tb["droid"]["open"]) == (True, False, False), "not installed: only Install"
        assert tb["kiro"]["open"] and not tb["kiro"]["login"] and not any(tb["chatgpt"][k] for k in ("install", "login", "open", "site"))
        assert key_choices(SAMPLE["/api/ai-tools"]) == [("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"), ("TYPESAFE_API_KEY", "TYPESAFE_API_KEY (set)"),
                                                        ("GOOGLE_TTS_API_KEY", "GOOGLE_TTS_API_KEY"), ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID"),
                                                        ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_TOKEN"), ("FISH_AUDIO_API_KEY", "FISH_AUDIO_API_KEY")]
        SAMPLE["/api/vision"] = {"available": True, "detail": None, "camera": "off", "watching": 0, "last": None,
                                 "known": [{"label": "my mug", "views": 4, "at": t0, "from": "camera"}]}
        assert camera_line(SAMPLE["/api/vision"]) == "Camera off" and known_rows(SAMPLE["/api/vision"]) == [("my mug", "4", "2026-10-01", "camera")]
        assert camera_line({"available": False, "detail": "OpenCV isn't installed here (cv2)."}) == \
            "The camera view isn't available: OpenCV isn't installed here (cv2)."
        assert camera_line({"available": True, "camera": "on", "last": {"text": "This is your mug."}}) == "Camera on · last look: This is your mug."
        SAMPLE["/api/google"] = {"state": "not_configured", "account": None, "detail": None, "redirect": "http://127.0.0.1:8765/api/google/callback",
                                 "client": {"configured": False, "source": None, "how": "Google needs an OAuth client of your own first."},
                                 "services": [{"id": "docs", "label": "Google Docs", "does": "Create documents", "route": "api", "enabled": True,
                                               "status": "not_connected"},
                                              {"id": "gmail", "label": "Gmail", "does": "Search and read your mail", "route": "api", "enabled": False,
                                               "status": "off"},
                                              {"id": "keep", "label": "Google Keep", "does": "Your notes", "route": "browser", "enabled": True,
                                               "status": "browser"}]}
        assert google_line(SAMPLE["/api/google"]) == "Google: not set up (no OAuth client of your own yet)"
        assert google_rows(SAMPLE["/api/google"])[2] == ("keep", "Google Keep", "through Monday's Chrome", "Your notes", True)
        assert google_line({"state": "connected", "account": {"email": "me@example.com"}}) == "Google: connected as me@example.com"
        from .jev import gates as jev_gates, usd
        G, tot = jev_gates(), {"cost": 0.0, "prev": {}, "receipts": {}}  # debug mode's reflex strip, against Core's own gates
        READ = {"status": "ok", "numbers": {"complete": 0.35, "addressed": 0.9, "stakes": 0.02}, "gates": G, "filming": False,
                "latency_ms": 312.4, "model": "jev-1.13.0", "input_tokens": 500, "cost_usd": usd(500), "cached": None}
        v = reflex_view(READ, None, tot)
        assert [x[:4] for x in v["rows"]] == [("Finished sentence", 0.35, G["complete"], "waiting"), ("Said to me", 0.9, G["addressed"], "to me"),
                                             ("Would send, delete or spend", 0.02, G["stakes"], "clear")], v["rows"]
        assert v["pill"] == "jev-1.13.0 · 3 questions · 312 ms · $0.000021 this sentence" and v["tone"] == "ok", v["pill"]
        v = reflex_view({**READ, "numbers": {**READ["numbers"], "complete": 0.97}, "cached": True, "input_tokens": 0, "cost_usd": 0.0}, None, tot)
        assert v["rows"][0][3:] == ("finished", "35% → 97%") and v["rows"][1][4] is None, "a value that crossed its gate keeps its receipt"
        assert v["pill"].endswith(" · cached · $0.000021 this sentence"), "a cached read cost nothing; the sentence's cost stays"
        assert "Finished sentence: 97%, gate 85%, finished, 35% → 97%" in v["text"], v["text"]
        v = reflex_view({"status": "timeout", "numbers": {}, "gates": G, "filming": False, "error": "Jev did not answer within 1.5s."}, None, tot)
        assert [x[1:] for x in v["rows"]] == [(None, G[q], None, None) for q in ("complete", "addressed", "stakes")], "N/A against the gates"
        assert v["pill"] == REFLEX_WHY["timeout"] and v["tone"] == "bad" and tot["prev"]["complete"] == 0.97, "this read failed; the last values are kept"
        for st, tone in (("not_configured", "off"), ("off", "off"), ("offline", "bad"), ("error", "bad"), ("rule", "ok")):
            v = reflex_view({"status": st, "numbers": {}, "gates": G, "filming": False, "error": "Jev API error 500."},
                            None, {"cost": 0.0, "prev": {}, "receipts": {}})
            assert v["tone"] == tone and v["pill"].startswith(REFLEX_WHY[st]) and all(x[1] is None for x in v["rows"]), (st, v)
        assert reflex_view({"status": "error", "error": "Jev API error 500."}, None, tot)["pill"].endswith("this sentence. Jev API error 500.")
        v = reflex_view(None, "Monday Core is unreachable.", {"cost": 0.0, "prev": {}, "receipts": {}})
        assert v["pill"] == "Failed: Monday Core is unreachable." and v["tone"] == "bad" and all(x[1] is x[2] is None for x in v["rows"]), v
        assert reflex_view(None, None, tot)["pill"] == "N/A: no read yet."
        v = reflex_view({**READ, "filming": True, "numbers": {**READ["numbers"], "narrating": 0.1}, "cost_usd": None}, None,
                        {"cost": 0.0, "prev": {}, "receipts": {}})
        assert v["rows"][1][2] == G["addressed_filming"] and v["rows"][-1][:4] == ("Talking to a camera", 0.1, G["narrating"], "clear"), v["rows"]
        assert v["pill"].endswith("4 questions · 312 ms · cost N/A"), "a cost Core didn't report isn't guessed"
        assert judge_words({"who": "monday", "money": False, "by": "jev"}) == "Monday can do this"
        assert judge_words({"who": None, "money": True, "by": "rules", "detail": "Jev isn't set up, so you choose."}) == \
            "You choose who does it · it involves money, so Monday waits for your word (Jev isn't set up, so you choose.)"
        assert [m[1] for m in model_choices(SAMPLE["/api/models"])] == ["Automatic (Monday decides)", "Claude (cloud): not available"]
        assert provider_choices(SAMPLE["/api/voice"])[0] == ("custom", "Your voice: not configured")
        assert voice_choices({"settings": {"tts_provider": "google_free"}, "providers": [
            {"id": "custom", "label": "Your voice (cloned from your recording, on this PC)", "status": "not_configured"},
            {"id": "google_free", "label": "Google", "status": "available"}]}) == \
            [("custom", "Your voice", "not configured", False), ("google_free", "Google", "available", True)]
        SEEN, GOT = {}, []  # the window token each POST carried, by path; the event stream's requests

        class Stub(http.server.BaseHTTPRequestHandler):  # a stand-in Core: every POST answered, the token it carried noted
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                SEEN[self.path] = self.headers.get("x-monday-window")
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # the event stream: one event, then silence (a socket left half-open by a sleep/resume: no ": ping" comes)
                GOT.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"id": 1, "type": "HELLO", "data": {}}\n\n')
                time.sleep(1.0)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        stub = Core(f"http://127.0.0.1:{srv.server_address[1]}")
        # the window token (the integration session's fix, 2026-10-17): Core's per-boot token beside its database, on every call
        app, saved = os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.environ.pop("MONDAY_DB", None)
        try:
            assert Core().token_file == os.path.join(app, "data", "window.token"), Core().token_file
            os.environ["MONDAY_DB"] = os.path.join("data", "test", "monday.db")
            assert Core().token_file == os.path.join(app, "data", "test", "window.token"), "a test Core's: beside its own database"
        finally:
            os.environ.pop("MONDAY_DB", None)
            if saved is not None:
                os.environ["MONDAY_DB"] = saved
        stub.token_file = os.path.join(tempfile.mkdtemp(), "window.token")  # (the owner's own token is never read here)
        assert stub.call("POST", "/api/permissions/1/approve")[1] is None and SEEN["/api/permissions/1/approve"] is None, "no token file: none sent"
        with open(stub.token_file, "w", encoding="utf-8") as f:
            f.write("first-boot\n")
        stub.call("POST", "/api/permissions/1/approve")
        assert SEEN["/api/permissions/1/approve"] == "first-boot", SEEN
        with open(stub.token_file, "w", encoding="utf-8") as f:
            f.write("second-boot")
        os.utime(stub.token_file, ns=(time.time_ns(), time.time_ns() + 10**9))  # Core restarted: a new token, a newer file
        stub.call("POST", "/api/agents/ask/a1", {"decision": "allow"})
        assert SEEN["/api/agents/ask/a1"] == "second-boot", "Core restarted: the new token"
        print("window token: read beside Core's database, sent on every call, read again when Core restarts, none without the file")
        # the event stream (2026-10-27): a socket gone quiet past STREAM_READ_S (no ": ping") is dropped and opened again from the last event
        assert STREAM_READ_S == 2 * 15 + 5, "two of core/app.py's SSE_PING_S (15 s) pings missed, and 5 s more"
        # a call that never reached Core keeps its words and is marked safe to send again (Unsent: qtwindow.Bridge holds it while the
        # desktop app is still starting Core, 2026-10-28); port 9 (discard) has nothing listening on this PC
        _d, _err = Core("http://127.0.0.1:9").call("GET", "/api/hello")
        assert _d is None and _err in ("Monday Core is unreachable.", "Monday Core didn't answer in time.") and getattr(_err, "unsent", False), _err
        assert not getattr("Monday Core is unreachable.", "unsent", False), "a plain failure (sent, then dropped) is not marked"
        # the ear back by itself (2026-10-28): after a lasting error that wasn't the owner's, once Core says it is available again (the
        # status poll, after EAR_BACK_S) or at once on Core's MICROPHONE event; never when the owner turned it off, never while it is on
        _e, _calls = Ear.__new__(Ear), []
        _e.on, _e.lost, _e.lost_at, _e.start = False, True, time.monotonic(), lambda: _calls.append(1)
        Ear.back(_e)
        assert not _calls and _e.lost, "lost a moment ago: the poll waits EAR_BACK_S"
        Ear.back(_e, force=True)
        assert _calls == [1] and not _e.lost, "the MICROPHONE event: at once"
        _e.on, _e.lost, _e.lost_at = False, True, time.monotonic() - EAR_BACK_S - 1
        Ear.back(_e)
        assert _calls == [1, 1], "the poll, after EAR_BACK_S"
        _e.on, _e.lost = True, True
        Ear.back(_e, force=True)
        _e.on, _e.lost = False, False
        Ear.back(_e, force=True)
        assert _calls == [1, 1], "on already, or off by the owner's hand: nothing"
        states, seen, saved = [], [], (STREAM_READ_S, RETRY_S)
        STREAM_READ_S, RETRY_S = 0.2, 0.05  # (for this check: events reads them as it runs; put back below)
        try:
            stub.events(None, seen.append, states.append, lambda: states.count("ONLINE") >= 2)
        finally:
            STREAM_READ_S, RETRY_S = saved
        assert states == ["ONLINE", "RECONNECTING", "ONLINE"] and GOT == ["/api/events", "/api/events?since=1"] and seen[0]["id"] == 1, (states, GOT)
        print("event stream: a half-open socket is dropped after STREAM_READ_S of silence and opened again from the last event (?since=)")

        srv.shutdown()
        print("native self-check: OK")
    else:
        print(__doc__)
