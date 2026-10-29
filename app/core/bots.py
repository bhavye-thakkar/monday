"""Bots with roles (2026-10-13, after the owner's MausBot reference: "Scout" researches, "Pixel" designs, "Forge" builds). A bot is a name,
a role, standing instructions and the agent that does its work; it is Monday underneath, with the same router, permissions and receipts:
- "Scout, find …" / "ask Scout to …" / "… with Scout" addresses it (address()); the workspace's composer can be pointed at it too;
- its agent is "auto" (Monday decides, as for any request) or a fixed one (Claude Code, Codex, Gemini CLI, Grok Build, Hermes, Monday's own
  computer loop, a custom ACP engine): a fixed agent gets every request as a task, its instructions first (persona());
- its tasks land in its own sidebar group (core/side.py), and its replies carry its name (meta.bot), so each bot's work reads apart;
- nothing a bot asks for skips Monday's rules: the permission engine, approvals and "payments wait for the owner" apply unchanged.
- after OpenMausBot's bot profiles (2026-10-17, its source): a bot may have its own chat model (`model`, an id from Monday's own registry,
  checked when saved; its chat turns use it, the rest falls back as usual) and its computer access (`computer`, on unless turned off: a bot
  without it never gets Hermes or Monday's own hands, refused in Monday._dispatch); `status` says what each is doing now, from its tasks.
Stored in settings.bots (SQLite). Self-check: python -m core.bots
"""
import re
import unicodedata

from . import router, side
from .db import ACTIVE

KEY = "bots"
NAME_MAX, ROLE_MAX, TEXT_MAX = 24, 40, 600
# OpenMausBot's roster (2026-10-17, the owner: all of it, with its UI): each bot has a face, one of the notch's pets in its own colours
# (core/notch.py PETS: its check keeps the two lists equal), pinned to the top or hidden from the roster, and an unread count.
PET_KINDS = ("witch", "blob", "bot", "cat", "fox", "gem", "maus", "blu", "hop", "sunny", "lovi", "muse", "grok", "nova", "byte", "webby")  # + Dotpals' nine; the witch is the notch's reference mascot (2026-10-17)
# a new bot is a Maus in OpenMausBot's ten colours, in turn by its name (its src/lib/mascot.ts MAUS_COLORS); the owner can pick another face
LOOKS = tuple(("maus", c, "#FFFFFF") for c in ("#009957", "#377FE6", "#D94B52", "#E78531", "#8057C8", "#0EA5C6", "#D84F8B", "#D8A729",
                                               "#01A492", "#E5634E"))
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# The reference's three, as starting points the owner adds with one click (their wording, Monday's rules)
TEMPLATES = {
    "scout": {"name": "Scout", "role": "Researcher", "agent": "auto",
              "instructions": "Find things out: search the web and read pages, compare sources, and answer with what you found and where each fact "
                              "came from. Say plainly when something couldn't be confirmed."},
    "pixel": {"name": "Pixel", "role": "Designer", "agent": "auto",
              "instructions": "Work on how things look and read: layouts, copy, colours and screens. Show the change and say what it improves."},
    "forge": {"name": "Forge", "role": "Engineer", "agent": "auto",
              "instructions": "Build and fix code in the owner's projects: make the change, run the project's checks, and report what changed and "
                              "how it was verified."},
}


def all_(db):
    return [b for b in (db.setting(KEY) or []) if isinstance(b, dict) and b.get("name")]


def find(db, name):
    n = (name or "").strip().lower()
    return next((b for b in all_(db) if b["name"].lower() == n), None)


# OpenMausBot's names for a new bot (its server/names.ts: short, friendly, a little pet-like) and its first card (store.ts onboardingCard)
NAMES = ("Scout", "Pixel", "Atlas", "Nova", "Juno", "Koda", "Miso", "Mochi", "Biscuit", "Pepper", "Clover", "Ember", "Willow", "Comet",
         "Orbit", "Echo", "Indigo", "Sage", "Zephyr", "Poppy", "Maple", "Cosmo", "Luna", "Otto", "Ivy", "Finch", "Wren", "Basil", "Hazel",
         "Nimbus", "Onyx", "Pearl", "Quill", "Rocket", "Sunny", "Tango", "Vega", "Waffle", "Ziggy", "Noodle", "Pickle", "Churro", "Panko",
         "Dumpling", "Pesto", "Olive", "Cocoa", "Taffy", "Bramble", "Fig", "Juniper", "Moss", "Pebble", "Rio", "Skye", "Tuli", "Ursa", "Yuki",
         "Zuko", "Momo", "Kiwi", "Plum", "Sprout", "Turnip")
WELCOME_CARD = {"title": "What do you mostly want help with?", "note": "Pick whatever's closest; we can always expand from there.",
                "options": ["Work & projects", "Writing & research", "Life admin", "A bit of everything"]}


def pick_name(taken, rng=None):
    """A friendly name no bot has yet; when all are taken, one numbered ("Scout 2")."""
    import random
    rng = rng or random
    used = {str(n).strip().lower() for n in taken}
    free = [n for n in NAMES if n.lower() not in used]
    if free:
        return rng.choice(free)
    base = rng.choice(NAMES)
    return next(f"{base} {i}" for i in range(2, 10000) if f"{base} {i}".lower() not in used)


def look(name, d=None):
    """A bot's face: {"kind", "body", "accent"}, each part checked; what isn't given comes from its name (the same name, the same face)."""
    k = LOOKS[sum(ord(c) for c in (name or "").lower()) % len(LOOKS)]
    d = d if isinstance(d, dict) else {}
    return {"kind": d["kind"] if d.get("kind") in PET_KINDS else k[0],
            **{part: d[part].upper() if isinstance(d.get(part), str) and _HEX.match(d[part]) else dflt
               for part, dflt in (("body", k[1]), ("accent", k[2]))}}


def roster(bots):
    """The roster's order: pinned first, then as they were made; hidden ones last (the window folds them away)."""
    return sorted(bots, key=lambda b: (bool(b.get("hidden")), not b.get("pinned")))


def save(db, name, role="", instructions="", agent="auto", project=None, assistant="Monday", model=None, computer=None,
         face=None, pinned=None, hidden=None):
    """Add or update a bot; its sidebar group is made (or reused) under its name. -> the bot. model / computer / face / pinned / hidden
    None keep what the bot has (a window that doesn't send them changes neither); model "" is Monday's own choice again."""
    name = re.sub(r"\s+", " ", (name or "").strip())[:NAME_MAX]
    if not re.fullmatch(r"[A-Za-z][\w' -]*", name or ""):
        raise ValueError("A bot needs a name that starts with a letter.")
    taken = {assistant.lower(), *(v.lower() for v in router.AGENT_NAMES.values()), *router.AGENT_NAMES}
    if name.lower() in taken:
        raise ValueError(f"{name} is already the name of {assistant} or an agent. Pick another.")
    if agent != "auto" and agent not in router.AGENT_NAMES or agent == "chat":
        raise ValueError(f"{agent} isn't an agent Monday has.")
    g = db.one("SELECT id FROM groups WHERE lower(name)=lower(?)", name)
    gid = g["id"] if g else side.group_create(db, name)
    old = find(db, name) or {}
    bot = {"name": name, "role": (role or "").strip()[:ROLE_MAX], "instructions": (instructions or "").strip()[:TEXT_MAX],
           "agent": agent, "project": (project or None), "group_id": gid,
           "model": old.get("model") if model is None else (str(model).strip()[:200] or None),
           "computer": old.get("computer", True) if computer is None else bool(computer),
           "look": look(name, old.get("look") if face is None else face),
           "pinned": bool(old.get("pinned")) if pinned is None else bool(pinned),
           "hidden": bool(old.get("hidden")) if hidden is None else bool(hidden),
           "seen_at": old.get("seen_at")}
    db.set_setting(KEY, [b for b in all_(db) if b["name"].lower() != name.lower()] + [bot])
    return bot


def duplicate(db, name, assistant="Monday"):
    """OpenMausBot's Duplicate: the same bot under the next free name ("Scout 2"), with a group of its own, not pinned."""
    b = find(db, name)
    if not b:
        raise KeyError(f"No bot called {name}.")
    names = {x["name"].lower() for x in all_(db)}
    base = re.sub(r"\s+\d+$", "", b["name"])[:NAME_MAX - 3]
    new = next(f"{base} {n}" for n in range(2, 1000) if f"{base} {n}".lower() not in names)
    return save(db, new, b.get("role", ""), b.get("instructions", ""), b.get("agent", "auto"), b.get("project"), assistant,
                b.get("model") or "", b.get("computer", True), b.get("look"), False, False)


def seen(db, name, now):
    """The owner opened this bot's conversation: its replies up to now are read."""
    db.set_setting(KEY, [{**b, "seen_at": now} if b["name"].lower() == (name or "").strip().lower() else b for b in all_(db)])


def unread(db, b):
    """The bot's replies since the owner last looked (OpenMausBot's unread badge): Monday's messages that carry its name."""
    r = db.one("SELECT count(*) AS n FROM messages WHERE role='monday' AND json_extract(meta, '$.bot')=? AND created_at>?",
               b["name"], b.get("seen_at") or 0)
    return r["n"] if r else 0


def unseen(db, name):
    """OpenMausBot's Mark as unread: the bot's newest reply counts as unread again."""
    b = find(db, name)
    r = db.one("SELECT max(created_at) AS t FROM messages WHERE role='monday' AND json_extract(meta, '$.bot')=?", b["name"]) if b else None
    if r and r["t"]:
        seen(db, b["name"], r["t"] - 0.001)
        return True
    return False


# -- OpenMausBot's team files (`openmaus.team` v1, from its server/team-manifest.ts, MIT: THIRD_PARTY_NOTICES.md) ----------------------
TEAM_FORMAT, TEAM_VERSION, TEAM_MAX, TEAM_BYTES = "openmaus.team", 1, 50, 1 << 20
MAUS_COLOURS = {"green": "#009957", "blue": "#377FE6", "red": "#D94B52", "orange": "#E78531", "purple": "#8057C8", "cyan": "#0EA5C6",
                "pink": "#D84F8B", "yellow": "#D8A729", "teal": "#01A492", "coral": "#E5634E"}
_TEAM_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _colour_name(hexs):
    """The nearest of OpenMausBot's ten colours to a face's colour (a team file carries one of their names)."""
    rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    c = rgb(hexs if isinstance(hexs, str) and _HEX.match(hexs) else "#377FE6")
    return min(MAUS_COLOURS, key=lambda k: sum((a - b) ** 2 for a, b in zip(rgb(MAUS_COLOURS[k]), c)))


def team_read(d):
    """An untrusted team file -> the same file with every part checked against OpenMausBot's own limits, or ValueError saying what is wrong."""
    def text(v, field, most, need=False):
        if v is None or (isinstance(v, str) and not v.strip()):
            if need:
                raise ValueError(f"{field} is required.")
            return ""
        if not isinstance(v, str):
            raise ValueError(f"{field} must be text.")
        if len(v.strip()) > most:
            raise ValueError(f"{field} is too long.")
        return v.strip()
    if not isinstance(d, dict) or d.get("format") != TEAM_FORMAT:
        raise ValueError("This isn't an OpenMaus team file.")
    if d.get("version") != TEAM_VERSION:
        raise ValueError(f"Team file version {d.get('version')} isn't supported.")
    t = d.get("team")
    if not isinstance(t, dict):
        raise ValueError("The file has no team in it.")
    name, about = text(t.get("name"), "team.name", 100, True), text(t.get("description"), "team.description", 2000)
    ms = t.get("members")
    if not isinstance(ms, list) or not ms:
        raise ValueError("A team needs at least one member.")
    if len(ms) > TEAM_MAX:
        raise ValueError(f"A team can have at most {TEAM_MAX} members.")
    keys, members = set(), []
    for i, m in enumerate(ms):
        f = f"team.members[{i}]"
        if not isinstance(m, dict):
            raise ValueError(f"{f} must be an object.")
        key = text(m.get("key"), f"{f}.key", 64, True)
        if not _TEAM_KEY.match(key):
            raise ValueError(f"{f}.key may only hold lowercase letters, numbers, - and _.")
        if key in keys:
            raise ValueError(f"Two members have the key {key}.")
        keys.add(key)
        look_ = m.get("appearance")
        if not isinstance(look_, dict) or look_.get("color") not in MAUS_COLOURS:
            raise ValueError(f"{f}.appearance.color isn't one OpenMausBot knows.")
        members.append({"key": key, "name": text(m.get("name"), f"{f}.name", 100, True), "title": text(m.get("title"), f"{f}.title", 200),
                        "description": text(m.get("description"), f"{f}.description", 4000), "appearance": {"color": look_["color"]}})
    room = t.get("room")
    if not isinstance(room, dict):
        raise ValueError("team.room is required.")
    r = room.get("defaultResponder")
    kind = r.get("kind") if isinstance(r, dict) else None
    if kind in ("everyone", "mentions"):
        who = {"kind": kind}
    elif kind == "member":
        who = {"kind": "member", "member": text(r.get("member"), "team.room.defaultResponder.member", 64, True)}
        if who["member"] not in keys:
            raise ValueError(f"The room's lead {who['member']} isn't one of the members.")
    else:
        raise ValueError("team.room.defaultResponder is required.")
    return {"format": TEAM_FORMAT, "version": TEAM_VERSION,
            "team": {"name": name, **({"description": about} if about else {}), "members": members,
                     "room": {"name": text(room.get("name"), "team.room.name", 100, True), "bulletin": text(room.get("bulletin"), "team.room.bulletin", 12000),
                              "defaultResponder": who}}}


def team_file(bots, name):
    """These bots as OpenMausBot's team file: only what another app may know (name, role, instructions, colour), never a bot's agent, model,
    computer access, project or what it did; the room as OpenMausBot writes one (no bulletin, everyone responds)."""
    used, members = set(), []
    for i, b in enumerate(bots[:TEAM_MAX]):
        stem = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", b["name"]).encode("ascii", "ignore").decode().lower()).strip("-")[:48]
        stem = stem or f"member-{i + 1}"
        key, n = stem, 2
        while key in used:
            key, n = f"{stem}-{n}", n + 1
        used.add(key)
        members.append({"key": key, "name": b["name"], "title": b.get("role") or "", "description": b.get("instructions") or "",
                        "appearance": {"color": _colour_name((b.get("look") or look(b["name"]))["body"])}})
    name = re.sub(r"\s+", " ", name or "").strip()[:100] or "My team"
    return team_read({"format": TEAM_FORMAT, "version": TEAM_VERSION, "team": {"name": name, "members": members,
                      "room": {"name": name, "bulletin": "", "defaultResponder": {"kind": "everyone"}}}})


def team_import(db, d, assistant="Monday"):
    """A team file's members as new bots and its room, all of it or none. A taken name gets the next free number; each starts with Monday's
    own choice of agent and model and WITHOUT computer access (a shared file is untrusted: the owner turns that on), as a Maus in its colour.
    The room keeps its name, its instructions and who answers (a member lead, everyone, or mentions only). -> (the team, the bots, the room)."""
    t = team_read(d)["team"]
    before, rooms_before, groups, made = db.setting(KEY), db.setting(ROOMS), {g["id"] for g in db.all("SELECT id FROM groups")}, []
    taken = {assistant.lower(), *(v.lower() for v in router.AGENT_NAMES.values()), *router.AGENT_NAMES}
    try:
        for m in t["members"]:
            base = re.sub(r"\s+", " ", m["name"]).strip()[:NAME_MAX - 3]
            base = base if re.fullmatch(r"[A-Za-z][\w' -]*", base) and base.lower() not in taken else f"Bot {re.sub(r'[^\w -]', '', base)}".strip()[:NAME_MAX - 3]
            names, name = {x["name"].lower() for x in all_(db)}, base
            if name.lower() in names:  # taken: the next free number on its stem, as Duplicate does ("Scout 2" -> "Scout 5")
                stem, n = re.sub(r"\s+\d+$", "", base) or base, 2
                while (name := f"{stem} {n}").lower() in names:
                    n += 1
            made.append(save(db, name, m["title"][:ROLE_MAX], m["description"][:TEXT_MAX], "auto", None, assistant, "", False,
                             {"kind": "maus", "body": MAUS_COLOURS[m["appearance"]["color"]], "accent": "#FFFFFF"}, False, False))
        by_key = {m["key"]: b["name"] for m, b in zip(t["members"], made)}
        who = t["room"]["defaultResponder"]
        room = room_save(db, t["room"]["name"], [b["name"] for b in made], "lead" if who["kind"] == "member" else who["kind"],
                         by_key.get(who.get("member")), t["room"]["bulletin"])
    except ValueError:
        db.set_setting(KEY, before)  # a team comes in whole or not at all
        db.set_setting(ROOMS, rooms_before)
        for g in db.all("SELECT id FROM groups"):
            if g["id"] not in groups:
                side.group_delete(db, g["id"])
        raise
    return t, made, room


# -- rooms: several bots in one conversation, after OpenMausBot's (2026-10-17). Not the open microphone's "room" (Monday.room_talk). ---------
# A room is a name, its members (bots), who answers a message that names nobody (`responder`: its lead, everyone, or only those @mentioned)
# and its instructions (`bulletin`, OpenMausBot's), which every member follows. The owner's message goes to the members it names
# (@Name, @everyone) or else as `responder` says; each member's turn is an ordinary request addressed to that bot, routed by Jev with the
# rules and approvals as for anything else (Monday.room_say), the room's messages tagged `chatroom`. A member that @mentions another brings it
# in once (ROOM_HOPS). Stored in settings.rooms.
ROOMS, ROOM_MAX, ROOM_HOPS, ROOM_CONTEXT = "rooms", 30, 1, 30  # rooms kept; @mention hops a reply may start; room messages a member reads
RESPONDERS = {"lead": "Its lead answers", "everyone": "Everyone answers", "mentions": "Only those @mentioned answer"}


def rooms(db):
    return [r for r in (db.setting(ROOMS) or []) if isinstance(r, dict) and r.get("id")]


def room_find(db, rid):
    return next((r for r in rooms(db) if r["id"] == rid), None)


def room_save(db, name, members, responder="lead", lead=None, bulletin="", rid=None):
    """Add or change a room: its members must be bots the owner has, its lead one of them (the first, unless named). -> the room."""
    import time
    import uuid
    have = {b["name"].lower(): b["name"] for b in all_(db)}
    ms = []
    for m in members or []:
        n = have.get(str(m).strip().lower())
        if not n:
            raise ValueError(f"{m} isn't one of your bots.")
        if n not in ms:
            ms.append(n)
    if not ms:
        raise ValueError("A room needs at least one bot.")
    if responder not in RESPONDERS:
        raise ValueError(f"A room's answerer is one of: {', '.join(RESPONDERS)}.")
    lead = have.get(str(lead or "").strip().lower()) or ms[0]
    if lead not in ms:
        raise ValueError(f"The lead, {lead}, isn't in the room.")
    old = room_find(db, rid) if rid else None
    if rid and not old:
        raise KeyError("No such room.")
    if not old and len(rooms(db)) >= ROOM_MAX:
        raise ValueError(f"Monday keeps at most {ROOM_MAX} rooms.")
    r = {"id": rid or uuid.uuid4().hex[:10], "name": re.sub(r"\s+", " ", name or "").strip()[:60] or f"{ms[0]} & co.", "members": ms,
         "responder": responder, "lead": lead, "bulletin": (bulletin or "").strip()[:4000], "at": (old or {}).get("at") or time.time()}
    db.set_setting(ROOMS, [x for x in rooms(db) if x["id"] != r["id"]] + [r])
    return r


def room_remove(db, rid):
    left = [r for r in rooms(db) if r["id"] != rid]
    if len(left) == len(rooms(db)):
        return False
    db.set_setting(ROOMS, left)
    return True


def mentions(text, names):
    """The names @mentioned in text, in the order they come ("@everyone" as "everyone"); a longer name wins ("@Squeak 2" isn't "@Squeak")."""
    found, t = [], text or ""
    for n in sorted([*names, "everyone"], key=len, reverse=True):
        for m in re.finditer(rf"(?<![\w@])@{re.escape(n)}(?![\w])", t, re.I):
            found.append((m.start(), n))
            t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]  # taken: a shorter name can't match inside it
    return [n for _, n in sorted(found)]


def room_targets(room, text):
    """Who answers the owner's message in a room: those it @mentions (@everyone: every member), else as the room's `responder` says."""
    named = mentions(text, room["members"])
    if "everyone" in named:
        return list(room["members"])
    if named:
        return list(dict.fromkeys(named))
    return {"lead": [room["lead"]], "everyone": list(room["members"]), "mentions": []}[room["responder"]]


def room_brief(room, bot):
    """What a member is told about the room, beside its own persona (Monday._system_prompt)."""
    others = [m for m in room["members"] if m != bot]
    return (f"You are {bot} in the room “{room['name']}”" + (f" with {', '.join(others)}" if others else "") + ". Each message in it is "
            "labelled with who wrote it. To bring a teammate in, @mention them by name." +
            (f" The room's instructions, which you follow: {room['bulletin']}" if room.get("bulletin") else ""))


def room_line(room, last=None):
    """A room's line in the roster: its newest message as "You: …" / "Scout: …", or that nothing was said yet."""
    if not last:
        return "No messages yet"
    who = "You" if last.get("role") == "user" else ((last.get("meta") or {}).get("bot") or "Monday")
    return f"{who}: {' '.join(str(last.get('text') or '').split())[:120]}"


def remove(db, name):
    """The bot goes; its sidebar group and what it did stay (the owner deletes those themselves). It leaves its rooms; a room left with
    no one goes too."""
    if not find(db, name):
        raise KeyError(f"No bot called {name}.")
    db.set_setting(KEY, [b for b in all_(db) if b["name"].lower() != name.strip().lower()])
    left = []
    for r in rooms(db):
        ms = [m for m in r["members"] if m.lower() != name.strip().lower()]
        if ms:
            left.append({**r, "members": ms, "lead": r["lead"] if r["lead"] in ms else ms[0]})
    db.set_setting(ROOMS, left)


def address(text, bots):
    """'Scout, find the best laptop' / 'ask Scout to find …' / 'find … with Scout' -> (bot, 'find the best laptop'), else None."""
    t = (text or "").strip()
    for b in bots:
        n = re.escape(b["name"])
        m = (re.match(rf"^(?:(?:hey|ok|okay)\s+)?{n}\s*[,:]\s*(?P<r>.+)$", t, re.I)
             or re.match(rf"^(?:please\s+)?(?:ask|tell|have|get|let)\s+{n}\s+(?:to\s+)?(?P<r>.+)$", t, re.I)
             or re.match(rf"^(?P<r>.+?)\s+(?:with|using|via)\s+{n}\s*[.!?]?$", t, re.I))
        if m:
            return b, m["r"].strip()
    return None


def status(db, b):
    """What a bot is doing now, from its own tasks (its sidebar group): {"state": waiting (an approval of one of its tasks waits for the
    owner) | working | idle, "tasks", "last": its newest task}. Read from the tasks each time; nothing kept."""
    rows = db.all("SELECT t.id, t.title, t.status, t.created_at, t.finished_at, t.tests FROM tasks t JOIN side s ON s.kind='task' AND s.ref=t.id "
                  "WHERE s.group_id=? AND s.deleted_at IS NULL ORDER BY t.created_at DESC LIMIT 50", b.get("group_id"))
    active = [r["id"] for r in rows if r["status"] in ACTIVE]
    waiting = active and db.one(f"SELECT 1 AS x FROM permissions WHERE status='PENDING' AND task_id IN ({','.join('?' * len(active))})", *active)
    last = rows[0] if rows else None
    return {"state": "waiting" if waiting else "working" if active else "idle", "tasks": len(rows),
            "last": {"id": last["id"], "title": last["title"], "status": last["status"], "at": last["finished_at"] or last["created_at"],
                     "tests": (last["tests"] or {}).get("line") if isinstance(last["tests"], dict) else None} if last else None}


def persona(b):
    """What the bot's agent (or the chat model) is told before the request."""
    who = f"You are {b['name']}" + (f", the owner's {b['role'].lower()}" if b.get("role") else "") + ", one of the owner's bots in Monday."
    return f"{who} {b.get('instructions') or ''}".strip()


# -- webhooks, after OpenMausBot's (2026-10-17, the owner: "add, each off until enabled") ------------------------------------------------
# A webhook is the owner's own words ("Scout, summarize this GitHub issue") that a program on this computer can fire: POST
# /api/hooks/{id} with its secret in `x-monday-hook` (the guard still admits this computer only). What it posts is attached as a file,
# data and never instructions; the words go through Jev and the rules like anything the owner types, and approvals apply as usual.
# Off until the owner turns it on; only the secret's hash is kept here (its script beside the database holds the secret itself).
HOOKS = "webhooks"
HOOK_MAX, HOOK_BYTES = 20, 16384      # webhooks kept; bytes one may post
HOOK_RATE = (6, 60.0)                 # fires at most this many times in this many seconds
_fired = {}                           # webhook id -> recent fire times (in memory: a restart forgets them)


def _digest(secret):
    import hashlib
    return hashlib.sha256(secret.encode()).hexdigest()


def hooks(db):
    return [h for h in (db.setting(HOOKS) or []) if isinstance(h, dict) and h.get("id")]


def hook_view(h):
    """A webhook as the window shows it: never its hash."""
    return {k: h.get(k) for k in ("id", "name", "prompt", "enabled", "at", "last_at", "runs")}


def hook_new(db, prompt):
    """A new webhook, off: -> (the webhook, its secret). The secret is returned once, for its script, and only its hash is kept."""
    import secrets
    import time
    import uuid
    prompt = re.sub(r"\s+", " ", (prompt or "").strip())[:TEXT_MAX]
    if len(prompt) < 3:
        raise ValueError("Say what Monday should do when it fires, e.g. “Scout, summarize this GitHub issue”.")
    if len(hooks(db)) >= HOOK_MAX:
        raise ValueError(f"Monday keeps at most {HOOK_MAX} webhooks. Delete one first.")
    secret = secrets.token_urlsafe(24)
    h = {"id": uuid.uuid4().hex[:12], "name": prompt[:40], "prompt": prompt, "hash": _digest(secret), "enabled": False,
         "at": time.time(), "last_at": None, "runs": 0}
    db.set_setting(HOOKS, hooks(db) + [h])
    return h, secret


def hook_update(db, hid, **change):
    """enabled / prompt changed by the owner -> the webhook, or None when there is no such one."""
    out = None
    for h in (all_h := hooks(db)):
        if h["id"] == hid:
            if change.get("enabled") is not None:
                h["enabled"] = bool(change["enabled"])
            if change.get("prompt"):
                h["prompt"] = re.sub(r"\s+", " ", change["prompt"].strip())[:TEXT_MAX]
                h["name"] = h["prompt"][:40]
            if change.get("fired"):
                h["last_at"], h["runs"] = change["fired"], (h.get("runs") or 0) + 1
            out = h
    if out:
        db.set_setting(HOOKS, all_h)
    return out


def hook_remove(db, hid):
    left = [h for h in hooks(db) if h["id"] != hid]
    if len(left) == len(hooks(db)):
        return False
    db.set_setting(HOOKS, left)
    _fired.pop(hid, None)
    return True


def hook_check(h, secret):
    """The secret matches (compared in constant time)."""
    import hmac
    return bool(secret) and hmac.compare_digest(h.get("hash") or "", _digest(secret))


def hook_rate(hid, now):
    """Whether it may fire now (HOOK_RATE), counting this fire when it may."""
    n, window = HOOK_RATE
    recent = [t for t in _fired.get(hid, []) if now - t < window]
    if len(recent) >= n:
        _fired[hid] = recent
        return False
    _fired[hid] = recent + [now]
    return True


def hook_script(h, secret, port):
    """The PowerShell that fires it, kept beside the database for the owner (its secret is in it, as Monday's window token is in its file)."""
    return (f"# Monday's webhook: {h['name']}\n# Fires it: .\\{h['id']}.ps1 -Payload 'what to attach'  (or pipe text in). Off until you turn it on in"
            f" Settings.\n# The secret below is this webhook's own: keep this file to yourself.\nparam([Parameter(ValueFromPipeline=$true)][string]$Payload = '')\n"
            f"Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:{port}/api/hooks/{h['id']}' -Headers @{{'x-monday-hook' = '{secret}'}} "
            f"-ContentType 'text/plain; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($Payload))\n")


if __name__ == "__main__":  # python -m core.bots
    import tempfile
    from pathlib import Path

    from .db import DB
    with tempfile.TemporaryDirectory() as d:
        db = DB(Path(d) / "t.db")
        s = save(db, **TEMPLATES["scout"])
        assert s["group_id"] and db.one("SELECT name FROM groups WHERE id=?", s["group_id"])["name"] == "Scout", "a bot has its own group"
        f = save(db, "Forge", "Engineer", "Build things.", "codex", project="Monday Core")
        assert [b["name"] for b in all_(db)] == ["Scout", "Forge"] and find(db, "forge")["agent"] == "codex"
        assert save(db, "Scout", "Researcher", "New words.")["group_id"] == s["group_id"] and len(all_(db)) == 2, "an update keeps its group"
        for bad in (("Monday", "auto"), ("Codex", "auto"), ("hermes", "auto"), ("1bot", "auto"), ("Pixel", "nobody"), ("Pixel", "chat")):
            try:
                save(db, bad[0], agent=bad[1])
                raise AssertionError(f"{bad} must be refused")
            except ValueError:
                pass
        bs = all_(db)
        assert address("Scout, find the best laptop under 1000", bs)[1] == "find the best laptop under 1000"
        assert address("ask Forge to fix the login page", bs)[0]["name"] == "Forge" and address("hey scout: what's new", bs)[1] == "what's new"
        assert address("compare these phones with Scout", bs)[1] == "compare these phones" and address("open Notepad", bs) is None
        assert address("Scouting is fun", bs) is None, "a word that starts with a bot's name is not the bot"
        assert persona(f).startswith("You are Forge, the owner's engineer") and "Build things." in persona(f)
        # OpenMausBot's per-bot model and computer access: kept when a save doesn't send them, changed when it does
        assert (s["model"], s["computer"]) == (None, True), "a new bot: Monday's own model choice, computer access on"
        save(db, "Scout", "Researcher", "Find.", model="gpt-oss:120b-cloud", computer=False)
        b = save(db, "Scout", "Researcher", "Find things.")  # the Bots tab's own save sends neither
        assert (b["model"], b["computer"], b["instructions"]) == ("gpt-oss:120b-cloud", False, "Find things."), b
        assert save(db, "Scout", model="")["model"] is None, "an empty model is Monday's own choice again"
        # what each is doing now, from its own tasks
        assert status(db, b) == {"state": "idle", "tasks": 0, "last": None}
        for tid, st in (("TASK-1", "COMPLETED"), ("TASK-2", "RUNNING")):
            db.insert("tasks", id=tid, title=f"t {tid}", prompt="p", agent="codex", status=st, created_at=float(tid[-1]), tests={"line": "Tests passed · 3 passed"})
            side.update(db, "task", tid, {"group_id": b["group_id"]})
        assert status(db, b)["state"] == "working" and status(db, b)["last"]["id"] == "TASK-2" and status(db, b)["last"]["tests"] == "Tests passed · 3 passed"
        db.insert("permissions", task_id="TASK-2", agent="codex", kind="RUN_COMMAND", title="x", risk="LOW", status="PENDING", created_at=1.0)
        assert status(db, b)["state"] == "waiting", "an approval of its task waits on the owner"
        assert status(db, f)["state"] == "idle", "another bot's tasks aren't this one's"
        # OpenMausBot's names: free ones first, then numbered
        import random as _r
        assert pick_name(["Scout", "pixel"], _r.Random(1)) not in ("Scout", "Pixel") and pick_name(NAMES, _r.Random(1)).rsplit(" ", 1)[1] == "2"
        assert set(WELCOME_CARD) == {"title", "note", "options"} and len(WELCOME_CARD["options"]) == 4
        # OpenMausBot's roster: a face from the name unless chosen, pinned first, hidden last, duplicate, unread replies
        assert b["look"] == look("Scout") and b["look"]["kind"] in PET_KINDS and look("Scout") == look("scout"), "the same name, the same face"
        assert save(db, "Scout", face={"kind": "fox", "body": "#ff0000", "accent": "nope"})["look"] == {"kind": "fox", "body": "#FF0000",
                                                                                                         "accent": look("Scout")["accent"]}
        assert save(db, "Scout", face={"kind": "dragon"})["look"]["kind"] == look("Scout")["kind"], "a kind there is no pet for: its own"
        assert save(db, "Forge", pinned=True)["pinned"] and save(db, "Forge")["pinned"], "kept when a save doesn't send it"
        d2 = duplicate(db, "Scout")
        assert d2["name"] == "Scout 2" and d2["look"] == find(db, "Scout")["look"] and d2["group_id"] != s["group_id"] and not d2["pinned"]
        assert duplicate(db, "Scout 2")["name"] == "Scout 3", "the next free number"
        save(db, "Scout 3", hidden=True)
        assert [x["name"] for x in roster(all_(db))] == ["Forge", "Scout", "Scout 2", "Scout 3"], [x["name"] for x in roster(all_(db))]
        for i, who in enumerate(("Scout", "Scout", "Forge")):
            db.insert("messages", role="monday", kind="text", text=f"r{i}", created_at=10.0 + i, meta={"bot": who})
        assert unread(db, find(db, "Scout")) == 2 and unread(db, find(db, "Forge")) == 1
        seen(db, "scout", 11.5)
        assert unread(db, find(db, "Scout")) == 0 and unread(db, find(db, "Forge")) == 1 and find(db, "Scout")["seen_at"] == 11.5
        assert unseen(db, "Scout") and unread(db, find(db, "Scout")) == 1, "Mark as unread: its newest reply"
        # OpenMausBot's team files: what is shared, what is checked, what comes in (and what never does)
        f = team_file([find(db, "Scout"), find(db, "Forge"), find(db, "Scout 2")], "  Research   desk ")
        m0 = f["team"]["members"][0]
        assert f["format"] == "openmaus.team" and f["team"]["name"] == "Research desk" and f["team"]["room"]["defaultResponder"] == {"kind": "everyone"}
        assert [m["key"] for m in f["team"]["members"]] == ["scout", "forge", "scout-2"] and m0["appearance"]["color"] in MAUS_COLOURS
        assert set(m0) == {"key", "name", "title", "description", "appearance"}, "never its agent, model, computer access or project"
        assert team_file([{"name": "Zoë"}, {"name": "Zoe"}], "")["team"]["members"][1]["key"] == "zoe-2" and _colour_name("#E78530") == "orange"
        for bad, why in (({}, "isn't an OpenMaus"), ({**f, "version": 2}, "version 2"), ({**f, "team": {**f["team"], "members": []}}, "at least one"),
                         ({**f, "team": {**f["team"], "members": [{**m0, "key": "Bad Key"}]}}, "lowercase"),
                         ({**f, "team": {**f["team"], "members": [m0, m0]}}, "Two members"),
                         ({**f, "team": {**f["team"], "members": [{**m0, "appearance": {"color": "magenta"}}]}}, "color"),
                         ({**f, "team": {**f["team"], "room": {"name": "R", "defaultResponder": {"kind": "member", "member": "ghost"}}}}, "lead ghost"),
                         ({**f, "team": {**f["team"], "members": [m0] * 51}}, "at most 50")):
            try:
                team_read(bad)
                raise AssertionError(f"must be refused: {why}")
            except ValueError as e:
                assert why in str(e), (why, str(e))
        n0, g0 = len(all_(db)), db.one("SELECT count(*) AS n FROM groups")["n"]
        t, made, room = team_import(db, {**f, "team": {**f["team"], "members": f["team"]["members"] + [{**m0, "key": "monday", "name": "Monday"},
                                                                                                          {**m0, "key": "x", "name": "42"}],
                                                       "room": {"name": "Desk", "bulletin": "Be brief.", "defaultResponder": {"kind": "member", "member": "forge"}}}})
        assert (room["name"], room["members"], room["responder"], room["lead"], room["bulletin"]) == \
            ("Desk", [b["name"] for b in made], "lead", "Forge 2", "Be brief."), room
        assert [b["name"] for b in made] == ["Scout 4", "Forge 2", "Scout 5", "Bot Monday", "Bot 42"], [b["name"] for b in made]
        assert all(b["computer"] is False and b["agent"] == "auto" and b["model"] is None and b["look"]["kind"] == "maus" for b in made), \
            "imported bots start without computer access, on Monday's own agent and model"
        assert made[0]["look"]["body"] == MAUS_COLOURS[m0["appearance"]["color"]] and len(all_(db)) == n0 + 5
        before, real, calls = len(all_(db)), save, []

        def save(*a, **k):  # the second member can't be made (as a full disk would): none of them is
            calls.append(a[1])
            if len(calls) == 2:
                raise ValueError("disk full")
            return real(*a, **k)
        try:
            team_import(db, {**f, "team": {**f["team"], "members": [m0, {**m0, "key": "y"}]}})
            raise AssertionError("a failed import must say so")
        except ValueError as e:
            assert str(e) == "disk full"
        save = real
        assert len(all_(db)) == before and db.one("SELECT count(*) AS n FROM groups")["n"] == g0 + 5, "undone: bots and their groups"
        # rooms: members, who answers, @mentions (the longest name first), the bulletin, a bot that leaves
        r = room_save(db, "", ["scout", "Forge", "Scout 2", "scout"], "lead", None, "  Answer in one paragraph. ")
        assert (r["name"], r["members"], r["lead"], r["bulletin"]) == ("Scout & co.", ["Scout", "Forge", "Scout 2"], "Scout", "Answer in one paragraph.")
        for bad in ((["Nobody"], "lead"), ([], "lead"), (["Scout"], "loudest")):
            try:
                room_save(db, "R", *bad)
                raise AssertionError(f"{bad} must be refused")
            except ValueError:
                pass
        assert mentions("@Scout 2 and @scout, ask @Everyone; mail a@Forge.com", r["members"]) == ["Scout 2", "Scout", "everyone"], \
            mentions("@Scout 2 and @scout, ask @Everyone; mail a@Forge.com", r["members"])
        assert room_targets(r, "what do you think?") == ["Scout"] and room_targets(r, "@Forge and @Scout 2, go") == ["Forge", "Scout 2"]
        assert room_targets(r, "@everyone, vote") == ["Scout", "Forge", "Scout 2"] and room_targets({**r, "responder": "mentions"}, "hello") == []
        assert room_targets({**r, "responder": "everyone"}, "hi") == r["members"] and room_targets(r, "@Forge @Forge") == ["Forge"]
        assert room_brief(r, "Forge").startswith("You are Forge in the room “Scout & co.” with Scout, Scout 2.") and "one paragraph" in room_brief(r, "Forge")
        assert room_line(r) == "No messages yet" and room_line(r, {"role": "monday", "text": " Done.\n", "meta": {"bot": "Forge"}}) == "Forge: Done."
        r2 = room_save(db, "Desk", ["Forge"], "everyone", rid=r["id"])
        assert r2["id"] == r["id"] and r2["at"] == r["at"] and [x["id"] for x in rooms(db)].count(r["id"]) == 1, "a change keeps it"
        remove(db, "Forge")
        assert find(db, "Forge") is None and db.one("SELECT id FROM groups WHERE name='Forge'"), "the group stays"
        assert room_find(db, r["id"]) is None and room_find(db, room["id"]), "a room left with no one goes; the others stay"
        # webhooks: off until turned on, only the secret's hash kept, a wrong secret refused, at most HOOK_RATE fires a minute
        h, secret = hook_new(db, "  Scout,   summarize this GitHub issue ")
        assert h["prompt"] == "Scout, summarize this GitHub issue" and h["enabled"] is False and secret not in str(db.setting(HOOKS))
        assert "hash" not in hook_view(h) and hook_check(h, secret) and not hook_check(h, secret + "x") and not hook_check(h, "")
        assert hook_update(db, h["id"], enabled=True)["enabled"] and hooks(db)[0]["enabled"] and hook_update(db, "nope", enabled=True) is None
        assert hook_update(db, h["id"], fired=5.0)["runs"] == 1 and hooks(db)[0]["last_at"] == 5.0
        assert [hook_rate(h["id"], 100.0 + i) for i in range(7)] == [True] * 6 + [False] and hook_rate(h["id"], 161.0), "6 a minute"
        ps = hook_script(h, secret, 8765)
        assert f"/api/hooks/{h['id']}" in ps and secret in ps and "127.0.0.1:8765" in ps
        for bad in ("", "x"):
            try:
                hook_new(db, bad)
                raise AssertionError("a webhook needs words")
            except ValueError:
                pass
        assert hook_remove(db, h["id"]) and not hooks(db) and not hook_remove(db, h["id"])
        db.c.close()  # Windows won't remove an open database file
    print("bots self-check: OK", f"(templates: {', '.join(t['name'] for t in TEMPLATES.values())})")
