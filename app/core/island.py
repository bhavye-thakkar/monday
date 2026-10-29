"""The island's widgets (2026-10-24, the owner's recording "TwoCode for Windows, part 5: Make the island yours"): the open notch's Home
as a grid the owner arranges, its rules alone. Pure: no Qt (core/qtnotch.py draws it; data/desktop.json keeps it as "island").
docs/adr/0006-island-widgets.md.

The grid is COLS x ROWS cells and a widget is Small (1 x 1), Wide (2 x 1) or Big (2 x 2). A layout is a list of {"w", "size", "x", "y"}:
which widget, how big, its top left cell. Held, the tiles wiggle and can be removed, resized and moved; + adds one or puts a ready-made
layout in place (LAYOUTS). Each widget is on the grid at most once.

Also here, as rules: the focus timer (25 minutes, kept across restarts), the clipboard's list (in memory, a private copy never kept), and
what the clock, the music and the AI card say.

python -m core.island --check
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import time

COLS, ROWS = 4, 2
GRID = (COLS, ROWS)                   # classic's Home; a look may have its own (dims: the floating island's ISLAND_GRID)
SIZES = {"small": (1, 1), "wide": (2, 1), "big": (2, 2)}
SIZE_WORDS = {"small": "Small", "wide": "Wide", "big": "Big"}
CYCLE = ("small", "wide", "big")      # the resize badge's order, round again
WIDGETS = {  # key -> (its name in the menu, its colour: its tile in the menu and its block in a layout's picture, its line with nothing to say)
    "music": ("Music", "#3DB67C", "Nothing playing"),
    "ai": ("AI", "#E9806A", "All quiet"),
    "focus": ("Focus", "#F2A24C", "Focus"),
    "tray": ("Tray", "#4A7BDA", "Empty"),
    "clipboard": ("Clipboard", "#3FC1A4", "Last copies"),
    "note": ("Note", "#F2C94C", "Write it down"),
    "settings": ("Settings", "#9E83F2", "Sound, auto-close"),
    "clock": ("Clock", "#7482E6", ""),
    # the 2026-10-26 recording ("Coucou for Windows 0.2"): the agents beside the session, plan use, GitHub
    "agents": ("Agents", "#E26FA6", "No agents open"),
    "plan": ("Plan", "#E2A93B", "No plan use read yet"),
    "github": ("GitHub", "#E35D5B", "Not signed in"),
}
MENU = ("music", "ai", "focus", "tray", "clipboard", "note", "settings", "clock", "agents", "plan", "github")  # the menu's two columns, row by row
LAYOUTS = {  # key -> (its name, its widgets as (key, size, x, y)): the recordings' ready-made layouts
    # the 2026-10-26 recording's Home: the session on the left, the agents on the right (the default since then; the owner's own stays theirs)
    "agents": ("Agents", (("ai", "big", 0, 0), ("agents", "big", 2, 0))),
    "default": ("Classic", (("ai", "big", 0, 0), ("music", "small", 2, 0), ("focus", "small", 3, 0), ("tray", "small", 2, 1),
                            ("settings", "small", 3, 1))),
    "small8": ("8 small", (("ai", "small", 0, 0), ("music", "small", 1, 0), ("focus", "small", 2, 0), ("clock", "small", 3, 0),
                           ("tray", "small", 0, 1), ("clipboard", "small", 1, 1), ("note", "small", 2, 1), ("settings", "small", 3, 1))),
    "big2": ("2 big", (("ai", "big", 0, 0), ("music", "big", 2, 0))),
    "wide4": ("4 wide", (("ai", "wide", 0, 0), ("music", "wide", 2, 0), ("focus", "wide", 0, 1), ("clock", "wide", 2, 1))),
    "music": ("Music", (("music", "big", 0, 0), ("ai", "wide", 2, 0), ("clock", "small", 2, 1), ("focus", "small", 3, 1))),
}
DEFAULT = "agents"    # Home with nothing stored, and the menu's first layout (2026-10-26; "default" is the 2026-10-24 one, now "Classic")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")   # (not strftime: its words follow the C library's locale)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# -- the grid ---------------------------------------------------------------------------------------------------------------------------
def item(w, size, x, y):
    return {"w": w, "size": size, "x": int(x), "y": int(y)}


def cells(it):
    """The cells a placed widget covers."""
    cw, ch = SIZES[it["size"]]
    return {(it["x"] + i, it["y"] + j) for i in range(cw) for j in range(ch)}


def taken(layout, skip=None):
    """Every cell the layout covers (but `skip`'s, a widget being moved or resized)."""
    out = set()
    for it in layout:
        if it["w"] != skip:
            out |= cells(it)
    return out


def inside(size, x, y, dims=GRID):
    cw, ch = SIZES[size]
    return 0 <= x and 0 <= y and x + cw <= dims[0] and y + ch <= dims[1]


def fits(size, x, y, used, dims=GRID):
    cw, ch = SIZES[size]
    return inside(size, x, y, dims) and not any((x + i, y + j) in used for i in range(cw) for j in range(ch))


def spot(layout, size, skip=None, near=None, dims=GRID):
    """(x, y) where a widget of this size fits: one covering `near` (a cell) first, else the first free place row by row; None: no room."""
    used = taken(layout, skip)
    cw, ch = SIZES[size]
    if near is not None:
        for j in range(ch):
            for i in range(cw):
                if fits(size, near[0] - i, near[1] - j, used, dims):
                    return near[0] - i, near[1] - j
    for y in range(dims[1]):
        for x in range(dims[0]):
            if fits(size, x, y, used, dims):
                return x, y
    return None


def free(layout, dims=GRID):
    """The free cells, row by row (each one an empty slot with its + in edit mode)."""
    used = taken(layout)
    return [(x, y) for y in range(dims[1]) for x in range(dims[0]) if (x, y) not in used]


def in_use(layout):
    return {it["w"] for it in layout}


def find(layout, key):
    return next((it for it in layout if it["w"] == key), None)


def normal(stored, dims=GRID, default=None):
    """A stored layout made valid (a hand-edited desktop.json can't break Home): known widgets at known sizes, inside the grid, each once,
    none over another (a later one that overlaps is dropped). Not a list: the default layout (the look's own `default`, else DEFAULT's).
    An empty list stays empty: the owner removed them all."""
    if not isinstance(stored, list):
        return [item(*t) for t in default] if default is not None else preset(DEFAULT)
    out, used = [], set()
    for it in stored:
        try:
            w, size, x, y = it["w"], it["size"], it["x"], it["y"]
        except (KeyError, TypeError):
            continue
        if w not in WIDGETS or size not in SIZES or not all(type(v) is int for v in (x, y)) or find(out, w) or not fits(size, x, y, used, dims):
            continue
        out.append(item(w, size, x, y))
        used |= cells(out[-1])
    return out


def preset(name):
    return [item(*t) for t in LAYOUTS.get(name, LAYOUTS[DEFAULT])[1]]


def which_preset(layout):
    """The ready-made layout this one is, if it is one (the menu marks it), else None."""
    key = lambda lay: sorted((it["w"], it["size"], it["x"], it["y"]) for it in lay)  # noqa: E731
    return next((k for k in LAYOUTS if key(preset(k)) == key(layout)), None)


def _settle(layout, moved, others, dims=GRID):
    """`moved` placed; each of `others` (pushed out of its cells) put back at its own place when that is free again, else in the first
    place it fits. -> the new layout, or None when one of them can't find room."""
    gone = {o["w"] for o in others} | {moved["w"]}
    out = [it for it in layout if it["w"] not in gone] + [moved]
    for o in others:
        at = (o["x"], o["y"]) if fits(o["size"], o["x"], o["y"], taken(out), dims) else spot(out, o["size"], dims=dims)
        if at is None:
            return None
        out.append(item(o["w"], o["size"], *at))
    return out


def add(layout, key, size, at=None, dims=GRID):
    """`key` at `size` on the grid: over cell `at` when it fits there (an empty slot's +), else the first place with room. A widget
    already there is moved to its new size (one of each). -> the new layout, or None when there is no room for it."""
    rest = [it for it in layout if it["w"] != key]
    old = find(layout, key)
    pos = spot(rest, size, near=at if at is not None else ((old["x"], old["y"]) if old else None), dims=dims)
    return None if pos is None else rest + [item(key, size, *pos)]


def remove(layout, key):
    return [it for it in layout if it["w"] != key]


def resize(layout, key, dims=GRID):
    """The resize badge: `key` at the next size of CYCLE it can take where it is (its top left, or a cell up or left of it when the
    grid's edge is in the way), the widgets it then covers moved to the room left. -> (the new layout, its new size), or (layout, None)
    when no other size has room."""
    it = find(layout, key)
    if not it:
        return layout, None
    i = CYCLE.index(it["size"])
    for size in (CYCLE[(i + k) % len(CYCLE)] for k in range(1, len(CYCLE))):
        cw, ch = SIZES[size]
        for dx, dy in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
            if (dx and cw == 1) or (dy and ch == 1) or not inside(size, it["x"] + dx, it["y"] + dy, dims):
                continue
            moved = item(key, size, it["x"] + dx, it["y"] + dy)
            out = _settle(layout, moved, [o for o in layout if o["w"] != key and cells(o) & cells(moved)], dims)
            if out is not None:
                return out, size
    return layout, None


def move(layout, key, x, y, dims=GRID):
    """A tile dragged with its top left to (x, y): there if it fits, the widgets it lands on going to the place it left (shifted as the
    drag went: a swap) or, failing that, back to their own place or the first room. -> the new layout, or the same one when it can't go."""
    it = find(layout, key)
    if not it or (x, y) == (it["x"], it["y"]) or not inside(it["size"], x, y, dims):
        return layout
    moved = item(key, it["size"], x, y)
    hit = [o for o in layout if o["w"] != key and cells(o) & cells(moved)]
    out = [o for o in layout if o["w"] != key and o not in hit] + [moved]
    used = taken(out)
    for o in hit:
        s = item(o["w"], o["size"], o["x"] - (x - it["x"]), o["y"] - (y - it["y"]))
        if not fits(s["size"], s["x"], s["y"], used, dims):
            break
        out.append(s)
        used |= cells(s)
    else:
        return out
    out = _settle(layout, moved, hit, dims)
    return out if out is not None else layout


def snap(left, top, pitch_x, pitch_y, size, dims=GRID):
    """Where a dragged tile lands: the cell nearest its top left (px from the grid's corner; pitch: a cell and its gap), kept inside."""
    cw, ch = SIZES[size]
    return max(0, min(dims[0] - cw, round(left / pitch_x))), max(0, min(dims[1] - ch, round(top / pitch_y)))


def thumb(layout):
    """A layout's little picture (the menu's buttons): (x, y, w, h, colour) per widget, in cells."""
    return [(it["x"], it["y"], *SIZES[it["size"]], WIDGETS[it["w"]][1]) for it in layout]


# -- the notch's three looks (the owner's brief of 2026-10-29, four recordings; docs/adr/0011) ----------------------------------------------
# One notch, one Core: a look changes how the notch is drawn and what Home shows, never what Monday does (its chat, tasks, approvals).
UI_KEY = "notch_ui"   # data/desktop.json: {"mode": the look chosen, "island": {"layout", "at"}, "limits": {"order", "hidden", "at"}}
MODES = {  # key -> (its name, one line on what it is): Settings → General's three choices, in this order
    "classic": ("Classic notch", "The bar under the top edge, Home's widget grid: as before"),
    "island": ("Floating island", "A floating pill with your pet's eyes; Home's tiles glow while something happens"),
    "limits": ("AI limits", "Your AIs' use and when it resets on Home; the busiest one on the closed bar"),
}
ISLAND_GRID = (3, 1)  # the floating island's Home: one row of three tall tiles (the TwoCode "Home got a glow-up" recording)
ISLAND_LAYOUT = (("ai", "small", 0, 0), ("music", "small", 1, 0), ("focus", "small", 2, 0))   # ...its Claude Code, Music, Focus


def _at(v):
    """A kept place {"cx", "y"} in logical px, or None (not one)."""
    if isinstance(v, dict) and all(isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) for k in ("cx", "y")):
        return {"cx": float(v["cx"]), "y": float(v["y"])}
    return None


def ui(stored):
    """The look chosen and each look's own layout and place, every value checked (a hand-edited desktop.json can't break the notch).
    Classic's grid stays under "island" and its place under "notch_at", as before (the notch reads those)."""
    s = stored if isinstance(stored, dict) else {}
    isl = s.get("island") if isinstance(s.get("island"), dict) else {}
    lim = s.get("limits") if isinstance(s.get("limits"), dict) else {}

    def keys(v):
        return list(dict.fromkeys(k for k in v if isinstance(k, str)))[:20] if isinstance(v, list) else []
    return {"mode": s.get("mode") if s.get("mode") in MODES else "classic",
            "island": {"layout": normal(isl.get("layout"), ISLAND_GRID, ISLAND_LAYOUT), "at": _at(isl.get("at"))},
            "limits": {"order": keys(lim.get("order")), "hidden": keys(lim.get("hidden")), "at": _at(lim.get("at"))}}


def ui_reset(u, mode):
    """One look back to how it started (its layout and its place), the other looks as they are. Classic's are under their own keys
    (the notch puts its grid and place back)."""
    u = ui(u)
    return u if mode == "classic" or mode not in MODES else {**u, mode: ui({})[mode]}


# -- the focus timer: the Pomodoro technique (the owner, 2026-10-25: "the 25 is the pomodoro technique") ----------------------------------
FOCUS_S = 25 * 60       # one pomodoro of focus (the recording's 25:00)
SHORT_S = 5 * 60        # the short break after each one
LONG_S = 15 * 60        # the long break after every SET_OF of them
SET_OF = 4
KINDS = {"focus": FOCUS_S, "short": SHORT_S, "long": LONG_S}
LENGTHS = (5 * 60, 15 * 60, 25 * 60, 50 * 60)  # the island's Focus page: a pomodoro's length to pick (the recording's 5 / 15 / 25 / 50 min)
MORE_S = 5 * 60         # its "+5 min" on a running timer
FOCUS_TONE_S = 120      # an end is rung in only when it came this recently (Monday was off at the end: counted, not rung; no break)


def _day(wall):
    return _dt.datetime.fromtimestamp(wall).strftime("%Y-%m-%d")


def focus_now(f, wall=None):
    """What the timer shows from its stored state f ({"kind": focus | short | long, "ends", "left", "day", "count", "set"}):
    {"kind", "phase": "ready" | "running" | "paused" | "ended", "left", "total", "today": pomodoros finished today, "set": how many of
    this set of SET_OF are done}. "ended": its time ran out and focus_finish hasn't moved it on yet."""
    f, wall = f or {}, time.time() if wall is None else wall
    kind = f.get("kind") if f.get("kind") in KINDS else "focus"
    total = total_of(f, kind)
    out = {"kind": kind, "total": total, "today": f.get("count", 0) if f.get("day") == _day(wall) else 0, "set": int(f.get("set", 0)) % SET_OF}
    if f.get("ends"):
        left = f["ends"] - wall
        return {**out, "phase": "running" if left > 0 else "ended", "left": max(0.0, left)}
    if f.get("left") is not None:
        return {**out, "phase": "paused", "left": float(f["left"])}
    return {**out, "phase": "ready", "left": total}


def total_of(f, kind):
    """How long one `kind` lasts: a pomodoro as long as the owner picked on the Focus page (LENGTHS), else the technique's 25 min; a
    break as the technique's."""
    return float(f.get("length")) if kind == "focus" and (f or {}).get("length") in LENGTHS else float(KINDS[kind])


def focus_length(f, secs):
    """The Focus page's 5 / 15 / 25 / 50 min: the pomodoros' length from now on (one running keeps its end; one ready shows it at once).
    Anything else: unchanged."""
    f = dict(f or {})
    if secs in LENGTHS:
        f["length"] = secs
    return f


def focus_more(f, secs=MORE_S, wall=None):
    """"+5 min": a running timer's end later, a paused one's time left longer; ready or ended: unchanged."""
    f, wall = dict(f or {}), time.time() if wall is None else wall
    if f.get("ends") and f["ends"] > wall:
        f["ends"] += secs
    elif f.get("left") is not None:
        f["left"] = float(f["left"]) + secs
    return f


def focus_start(f, wall=None):
    """Start (from ready) or go on (from paused), whatever it is on: a pomodoro or a break."""
    f, wall = dict(f or {}), time.time() if wall is None else wall
    left = f.pop("left", None)
    f["ends"] = wall + (left if left is not None else total_of(f, f.get("kind") if f.get("kind") in KINDS else "focus"))
    return f


def focus_pause(f, wall=None):
    f, wall = dict(f or {}), time.time() if wall is None else wall
    if f.get("ends"):
        f["left"] = max(0.0, f.pop("ends") - wall)
    return f


def focus_reset(f):
    """Back to a pomodoro ready to start (a break skipped, or a pomodoro given up); today's count and the set stay."""
    f = dict(f or {})
    for k in ("ends", "left", "done_at"):
        f.pop(k, None)
    f["kind"] = "focus"
    return f


def focus_finish(f, wall=None):
    """Its time ran out -> (the new state, ring: it ended recently enough for its tone, what ended: "focus" | "break" | None).
    A pomodoro is counted once (on the day it ended) and its break starts from that moment: the long one after every SET_OF; a break
    over, or a pomodoro that ended while Monday was off: the next pomodoro, ready."""
    f, wall = dict(f or {}), time.time() if wall is None else wall
    ends = f.get("ends")
    if not ends or ends > wall:
        return f, False, None
    kind = f.get("kind") if f.get("kind") in KINDS else "focus"
    ring = wall - ends < FOCUS_TONE_S
    f = focus_reset(f)
    if kind != "focus":
        return f, ring, "break"
    day = _day(ends)
    f["count"] = (f.get("count", 0) if f.get("day") == day else 0) + 1
    f["day"] = day
    f["set"] = (int(f.get("set", 0)) + 1) % SET_OF
    brk = "long" if f["set"] == 0 else "short"
    if ring and ends + KINDS[brk] > wall:  # (its break, from when the pomodoro ended)
        f["kind"], f["ends"] = brk, ends + KINDS[brk]
    return f, ring, "focus"


def focus_words(st):
    """("24:59", the line under it) for the focus tile: which pomodoro of the set, or which break."""
    left = int(round(st["left"]))
    clock = f"{left // 60:02d}:{left % 60:02d}"
    if st["kind"] != "focus":
        word = "Long break" if st["kind"] == "long" else "Short break"
        return clock, f"{word} · paused" if st["phase"] == "paused" else word
    n = f"{st['set'] + 1}/{SET_OF}"
    return clock, f"Paused · {n}" if st["phase"] == "paused" else f"Pomodoro {n}"


# -- the clipboard ----------------------------------------------------------------------------------------------------------------------
CLIP_KEEP = 1000        # every copy since the PC started (the owner, 2026-10-26: "all the things that ever copied until shutdown");
#                         ponytail: a ceiling so a long day's copying can't grow without end, not a window of the last few
CLIP_CHARS = 4000       # a copy longer than this is kept cut
BOOT_SLACK_S = 120      # the same boot: its start time read again within this (the clock can be set meanwhile)
PRIVATE = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")  # set by password managers: never read or kept
HISTORY_FLAG = "CanIncludeInClipboardHistory"   # ...and this one with a 0 in it (Windows' clipboard history leaves those out too)


def clip_private(formats):
    """Is this copy private? formats: {format name: its bytes, or None when not read}: Windows' own signs that an app (a password manager)
    asked for it to stay out of clipboard history and monitors."""
    for name, data in formats.items():
        low = name.lower()
        if any(p.lower() in low for p in PRIVATE):
            return True
        if HISTORY_FLAG.lower() in low and data is not None and len(data) >= 4 and int.from_bytes(bytes(data[:4]), "little") == 0:
            return True
    return False


def clip_add(items, text, at=None):
    """The list (newest first, [{"text", "at"}]) with a new copy on top: blank ones ignored, the same text again moved up, CLIP_KEEP kept."""
    if not (text or "").strip():
        return list(items)
    text, at = text[:CLIP_CHARS], time.time() if at is None else at
    return ([{"text": text, "at": at}] + [c for c in items if c["text"] != text])[:CLIP_KEEP]


def clips_load(path, boot):
    """The copies kept since this boot ([{"text", "at"}], newest first), else none: a restart of Monday keeps them, a restart or a shutdown
    of the PC doesn't (the owner, 2026-10-26: "until shutdown"). A file that can't be read is none."""
    try:
        d = json.loads(open(path, encoding="utf-8").read())
        if abs(float(d["boot"]) - boot) > BOOT_SLACK_S:
            return []
        return [{"text": str(c["text"])[:CLIP_CHARS], "at": float(c["at"])} for c in d["clips"]][:CLIP_KEEP]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def clips_save(path, boot, clips):
    """The copies, marked with this boot, for clips_load (never a private one: clip_private kept those out before they were added)."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"boot": boot, "clips": clips}))
    except OSError:
        pass


def clip_line(text, n=80):
    """A copy as one line: its first non-blank line, spaces collapsed, cut with … at n."""
    line = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    line = re.sub(r"\s+", " ", line).strip()
    return line if len(line) <= n else line[:n - 1].rstrip() + "…"


def ago(at, now=None):
    """"now", "3 min", "2 h", "yesterday", "5 d"."""
    s = max(0, (time.time() if now is None else now) - at)
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)} min"
    if s < 86400:
        return f"{int(s // 3600)} h"
    return "yesterday" if s < 2 * 86400 else f"{int(s // 86400)} d"


# -- plan use and GitHub (the 2026-10-26 recording: "Your plan, always in sight"; "PRs, CI and your streak") ------------------------------
WINDOW_WORDS = {"5-hour": "5 hours", "week": "Week", "month": "Month"}


def updated(at, now=None):
    """When the agent's own log last said it, as the recording's "updated just now" (here the truth: "updated 5 d ago")."""
    w = ago(at, now)
    return "updated just now" if w == "now" else "updated yesterday" if w == "yesterday" else f"updated {w} ago"


def resets(at, now=None):
    """When a limit's window starts again: "in 3h 05m" within the day, "Sun 18:00" within the week, else "Nov 20"."""
    now = time.time() if now is None else now
    left, t = at - now, time.localtime(at)
    if left <= 0:
        return "now"
    if left < 86400:
        return f"in {int(left // 3600)}h {int(left % 3600 // 60):02d}m"
    return f"{WEEKDAYS[t.tm_wday]} {t.tm_hour:02d}:{t.tm_min:02d}" if left < 7 * 86400 else f"{MONTHS[t.tm_mon - 1]} {t.tm_mday}"


def plan_rows(plans, now=None):
    """The Plan widget's words from watch.plan_use: [{"agent", "head", "rows": [(window, used %, "↻ …" or "")]}], newest first."""
    out = []
    for p in plans or []:
        rows = [(WINDOW_WORDS.get(w["name"], w["name"]), int(w["used"]), resets(w["resets_at"], now) if w.get("resets_at") else "")
                for w in p.get("windows") or []]
        out.append({"agent": p["agent"], "head": " · ".join(x for x in (p.get("plan"), updated(p["at"], now) if p.get("at") else None) if x),
                    "rows": rows})
    return out


def heat(n):
    """A day's square on the GitHub card: 0 (none) to 4 (most), GitHub's own steps."""
    return 0 if n <= 0 else 1 if n <= 2 else 2 if n <= 5 else 3 if n <= 9 else 4


# -- what the clock, the music and the AI card say ----------------------------------------------------------------------------------------
def clock_words(dt, h24):
    """("9:41", "AM" or "", "Sat 24 Oct", the share of the day gone) as this PC's clock writes it (h24: its 24-hour setting)."""
    if h24:
        hm, ap = f"{dt.hour:02d}:{dt.minute:02d}", ""
    else:
        hm, ap = f"{dt.hour % 12 or 12}:{dt.minute:02d}", "AM" if dt.hour < 12 else "PM"
    return hm, ap, f"{WEEKDAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]}", (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400


def music_words(x, readable=True):
    """(title, line, playing) for the music tile from the session shown (media.view: app, name, status, title, artist; or None).
    readable False: Windows' media sessions can't be read here. playing: True, False (paused) or None (nothing to play or pause)."""
    if not readable:
        return "Music", "Not on this OS" if sys.platform != "win32" else "Media sessions: N/A", None
    if not x:
        return "Music", WIDGETS["music"][2], None
    title, who, status = x.get("title") or "Untitled", x.get("artist") or x.get("name") or "", x.get("status")
    if status == "playing":
        return title, who, True
    if status == "paused":
        return title, f"Paused · {who}" if who else "Paused", False
    return title, who or (status or "").capitalize(), None


OUTCOMES = {"COMPLETED": ("✓ Done", "ok"), "FAILED": ("✗ Failed", "bad"), "CANCELLED": ("Stopped", "off")}


def last_task(t):
    """A finished task as Core lists it (GET /api/tasks) or sends it (TASK_COMPLETED…) -> {"title", "status", "at", "where"}, or None."""
    if not isinstance(t, dict) or t.get("status") not in OUTCOMES:
        return None
    p = t.get("project")
    where = p if isinstance(p, str) else p.get("name") if isinstance(p, dict) else None
    return {"title": (t.get("title") or t.get("prompt") or "A task").strip(), "status": t["status"],
            "at": t.get("finished_at") or t.get("updated_at") or t.get("created_at"), "where": where or t.get("agent") or ""}


def ai_words(state, title, last, h24=True, today=None):
    """The AI card's lines while none of the owner's sessions is on it: (title, line, sub, (outcome words, its dot) or None).
    state/title: the notch's own line (notch.State.view); last: last_task(...) of Monday's last finished task, or None."""
    if state not in ("idle", "listening", "asleep", "done", "verified", "unverified", "failed", "cancelled", "answer"):
        return title, None, None, None  # working, waiting, hearing…: the caller shows that state's own words
    if not last:
        return title, "No tasks yet", "Say what you want, or drop a file", None
    when = ""
    if last.get("at"):
        at = _dt.datetime.fromtimestamp(float(last["at"]))
        hm, ap, _d, _f = clock_words(at, h24)
        when = f"{hm} {ap}".strip()
        if at.date() != (today or _dt.date.today()):
            when = f"{WEEKDAYS[at.weekday()]} {when}"
    sub = " · ".join(x for x in (when, last.get("where") or "") if x)
    return title, f"Last: {last['title']}", sub or None, OUTCOMES.get(last["status"])


# -- Monday's own running tasks on the AI tile (2026-10-27, the owner: "multi-agent execution is not working reliably"; the notch's own line
# said only the newest task and "+N more"): every one a row, each saying what Core said of it and no more ------------------------------------
AI_ROWS = 3             # rows of tasks (Big: what fits under the head with the dock's pills), then "(+N more)"
TITLE_CHARS, ROW_CHARS = 48, 120   # a task's title in its row, and what it is on
# Monday.lease's words while a task waits for what another holds (monday.py: run.action(f"Waiting for the screen: {holder} is using it"))
LEASE_WAIT = re.compile(r"^Waiting for (?P<what>the screen|.+?): (?P<holder>TASK-\S+) is using it$")


def task_row(t, step=None, waiting=False):
    """One of Monday's running tasks as the AI tile lists it -> (its title, what it is on): "step k/N" from its plan's step (a TASK_STEP row
    with a total); "waiting for your approval" (a permission open: `waiting`, or its status); "waiting for the screen (TASK-x is using it)"
    (Monday.lease holding it back); else its current action as Core said it; "starting" before its worker is up; "Working…" only when
    nothing is known. Never a percentage: nothing here measures one."""
    title = clip_line(t.get("title") or "A task", TITLE_CHARS)
    act = clip_line(str(t.get("current_action") or ""), ROW_CHARS)
    m = LEASE_WAIT.match(act)
    if waiting or t.get("status") == "WAITING_FOR_PERMISSION":
        what = "waiting for your approval"
    elif m:
        what = f"waiting for {m['what']} ({m['holder']} is using it)"
    elif step and step.get("total") and step.get("index"):
        what = f"step {step['index']}/{step['total']}"
    elif act:
        what = act
    elif t.get("status") in ("QUEUED", "STARTING"):
        what = "starting"
    else:
        what = "Working…"
    return title, what


def task_rows(tasks, steps=None, perms=None, most=AI_ROWS):
    """Every running task of Monday's as "title · what", the newest first, `most` of them, then "(+N more)". tasks: notch.State.tasks (id ->
    the task as Core sent it, the oldest first); steps: State.steps (id -> its last TASK_STEP); perms: State.perms (the approvals open, each
    with its task_id). None running: no rows (the tile says the last finished task instead)."""
    waiting = {p.get("task_id") for p in (perms or {}).values() if isinstance(p, dict)}
    rows = [" · ".join(task_row(t, (steps or {}).get(tid), tid in waiting)) for tid, t in reversed(list((tasks or {}).items()))
            if isinstance(t, dict)]
    return rows[:most] + ([f"(+{len(rows) - most} more)"] if len(rows) > most else [])


# -- check --------------------------------------------------------------------------------------------------------------------------------
def _check():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok = ok and bool(cond)

    for k, (name, lay) in LAYOUTS.items():  # every ready-made layout is valid as it is
        p = preset(k)
        check(f"{name}: valid", normal(p) == p and len(p) == len(lay))
    check("Default and 8 small fill the grid", not free(preset("default")) and not free(preset("small8")))
    check("Music first: Music big at the left", find(preset("music"), "music") == item("music", "big", 0, 0))
    check("not a list -> Default: the 2026-10-26 recording's Home (the session, the agents beside it)", normal(None) == preset(DEFAULT)
          == [item("ai", "big", 0, 0), item("agents", "big", 2, 0)] and normal("x") == preset(DEFAULT) and DEFAULT == "agents")
    check("the owner's own layout, stored, comes back as they made it (never the new default)",
          normal([item("ai", "big", 0, 0), item("note", "small", 2, 0), item("focus", "small", 3, 0)]) ==
          [item("ai", "big", 0, 0), item("note", "small", 2, 0), item("focus", "small", 3, 0)])
    check("the new widgets have their place in the menu and the registry", {"agents", "plan", "github"} <= set(MENU) <= set(WIDGETS))
    t0 = time.mktime((2026, 10, 26, 12, 0, 0, 0, 0, -1))  # a Monday noon, this PC's own time
    check("resets: within the day in hours, within the week by its day, later by its date", (resets(t0 + 3 * 3600 + 5 * 60, t0),
          resets(t0 + 6 * 86400 + 6 * 3600, t0), resets(time.mktime((2026, 11, 20, 16, 3, 0, 0, 0, -1)), t0), resets(t0 - 5, t0)) ==
          ("in 3h 05m", "Sun 18:00", "Nov 20", "now"))
    rows = plan_rows([{"agent": "Codex", "plan": "Go", "windows": [{"name": "month", "used": 3, "resets_at": time.mktime((2026, 11, 20, 16, 3, 0, 0, 0, -1))}],
                       "at": t0 - 5 * 86400}], t0)
    check("Plan: Codex's own words, its window, its use, its reset, how old the log is", rows == [{"agent": "Codex", "head": "Go · updated 5 d ago",
                                                                                               "rows": [("Month", 3, "Nov 20")]}])
    check("Plan: nothing read, nothing said", plan_rows(None) == [] and plan_rows([]) == [] and updated(t0 - 10, t0) == "updated just now")
    check("GitHub's five greens", [heat(n) for n in (0, 1, 3, 7, 12)] == [0, 1, 2, 3, 4])
    check("an empty layout stays empty", normal([]) == [])
    junk = [{"w": "ai", "size": "big", "x": 0, "y": 0}, {"w": "ai", "size": "small", "x": 3, "y": 0}, {"w": "nope", "size": "small", "x": 2,
            "y": 0}, {"w": "music", "size": "huge", "x": 2, "y": 0}, {"w": "clock", "size": "wide", "x": 3, "y": 1},
            {"w": "note", "size": "small", "x": 1, "y": 1}, {"w": "tray", "size": "small", "x": "2", "y": 1}, {"w": "focus"},
            {"w": "settings", "size": "small", "x": 2, "y": 1}, "junk"]
    check("a hand-edited layout made valid", normal(junk) == [item("ai", "big", 0, 0), item("settings", "small", 2, 1)])
    # add, remove, resize, move (the recording: Tray removed, Clock added in its place)
    d = remove(preset("default"), "tray")
    check("removed: its cell is free", free(d) == [(2, 1)] and "tray" not in in_use(d))
    d2 = add(d, "clock", "small", at=(2, 1))
    check("added at the empty slot", find(d2, "clock") == item("clock", "small", 2, 1))
    check("no room -> None", add(preset("default"), "clock", "small") is None and add(d, "note", "big") is None)
    check("a new one goes where it fits first", find(add(remove(remove(preset("small8"), "ai"), "note"), "ai", "small"), "ai")
          == item("ai", "small", 0, 0))
    check("one already there stays put at the same size", find(add(preset("small8"), "clipboard", "small"), "clipboard")
          == item("clipboard", "small", 1, 1))
    again = add(remove(preset("default"), "music"), "ai", "wide")
    check("one of each: adding again changes its size", find(again, "ai") == item("ai", "wide", 0, 0) and sum(it["w"] == "ai" for it in again) == 1)
    r, s = resize(preset("big2"), "ai")
    check("Big resizes to Small (round again)", s == "small" and find(r, "ai") == item("ai", "small", 0, 0))
    r, s = resize([item("music", "small", 3, 0)], "music")
    check("Small to Wide at the right edge grows left", s == "wide" and find(r, "music") == item("music", "wide", 2, 0))
    r, s = resize(preset("small8"), "music")
    check("no room on a full grid: unchanged", s is None and r == preset("small8"))
    r, s = resize(remove(remove(preset("small8"), "note"), "settings"), "tray")
    check("Wide over a neighbour moves it to the room left", s == "wide" and find(r, "tray") == item("tray", "wide", 0, 1)
          and find(r, "clipboard") == item("clipboard", "small", 2, 1) and len(r) == 6)
    lay = preset("default")
    m = move(lay, "music", 3, 0)
    check("drag onto a neighbour swaps them", find(m, "music") == item("music", "small", 3, 0) and find(m, "focus") == item("focus", "small", 2, 0))
    m = move(lay, "ai", 2, 0)
    check("Big dragged over two columns of smalls: they take its place", find(m, "ai") == item("ai", "big", 2, 0)
          and {(it["x"], it["y"]) for it in m if it["w"] != "ai"} == {(0, 0), (1, 0), (0, 1), (1, 1)})
    m = move(remove(preset("default"), "tray"), "settings", 2, 1)
    check("into an empty cell", find(m, "settings") == item("settings", "small", 2, 1) and free(m) == [(3, 1)])
    check("off the grid: unchanged", move(lay, "ai", 3, 0) == lay and move(lay, "music", 4, 0) == lay)
    check("the layout given is never changed in place", lay == preset("default"))
    check("snap", snap(160, 4, 156, 64, "small") == (1, 0) and snap(470, 70, 156, 64, "big") == (2, 0) and snap(-30, 200, 156, 64, "wide") == (0, 1))
    check("which_preset", which_preset(preset("music")) == "music" and which_preset(remove(preset("music"), "ai")) is None)
    check("thumb", thumb(preset("big2")) == [(0, 0, 2, 2, WIDGETS["ai"][1]), (2, 0, 2, 2, WIDGETS["music"][1])])
    # the floating island's own grid (3 x 1): the same rules at its size, classic's untouched
    G, isl = ISLAND_GRID, [item(*t) for t in ISLAND_LAYOUT]
    check("island grid: its default is valid there and fills it", normal(isl, G) == isl and not free(isl, G) and free(isl) == free(isl, GRID))
    check("island grid: nothing stored -> its own three tiles, not classic's", normal(None, G, ISLAND_LAYOUT) == isl)
    check("island grid: Big has no room in one row", add(remove(isl, "focus"), "note", "big", dims=G) is None
          and normal([item("ai", "big", 0, 0)], G) == [])
    r, s = resize(remove(isl, "focus"), "music", G)
    check("island grid: Small to Wide, then back (Big skipped)", s == "wide" and find(r, "music") == item("music", "wide", 1, 0)
          and resize(r, "music", G)[1] == "small")
    check("island grid: a drag swaps two tiles", find(move(isl, "ai", 2, 0, G), "focus") == item("focus", "small", 0, 0))
    check("island grid: off its edge, unchanged", move(isl, "ai", 3, 0, G) == isl and snap(900, 300, 150, 120, "small", G) == (2, 0))
    check("island grid: a classic layout keeps only what fits there (Big and column 3 out)", [it["w"] for it in normal(preset("default"), G)] == ["music"])
    # the looks
    u = ui(None)
    check("no look stored: classic, the island's own default, no places", u["mode"] == "classic" and u["island"]["layout"] == isl
          and u["island"]["at"] is None and u["limits"] == {"order": [], "hidden": [], "at": None})
    bad = ui({"mode": "neon", "island": {"layout": "x", "at": {"cx": "1", "y": 2}}, "limits": {"order": ["codex", 3, "codex"], "hidden": "x",
              "at": {"cx": 10, "y": True}}})
    check("a hand-edited look made valid", bad["mode"] == "classic" and bad["island"] == {"layout": isl, "at": None}
          and bad["limits"] == {"order": ["codex"], "hidden": [], "at": None})
    kept = ui({"mode": "limits", "island": {"layout": [item("music", "wide", 0, 0)], "at": {"cx": 900, "y": 48}},
               "limits": {"order": ["openrouter"], "hidden": ["jev"], "at": {"cx": 400.5, "y": 0}}})
    check("each look keeps its own layout and place", kept["mode"] == "limits" and kept["island"]["layout"] == [item("music", "wide", 0, 0)]
          and kept["island"]["at"] == {"cx": 900.0, "y": 48.0} and kept["limits"]["hidden"] == ["jev"])
    back = ui_reset(kept, "island")
    check("a reset puts back one look only", back["island"] == {"layout": isl, "at": None} and back["limits"] == kept["limits"]
          and back["mode"] == "limits" and ui_reset(kept, "limits")["island"] == kept["island"] and ui_reset(kept, "classic") == kept)
    check("three looks, each named", list(MODES) == ["classic", "island", "limits"] and all(len(v) == 2 and v[0] for v in MODES.values()))
    # the focus timer: the Pomodoro technique (25 minutes, a 5-minute break, a 15-minute one after every fourth)
    t0 = 1_800_000_000.0
    f = focus_start({}, t0)
    now = focus_now(f, t0 + 1)
    check("a pomodoro runs 25 minutes", now["kind"] == "focus" and now["phase"] == "running" and round(now["left"]) == FOCUS_S - 1)
    p = focus_pause(f, t0 + 60)
    check("paused keeps what's left", focus_now(p, t0 + 500)["phase"] == "paused" and focus_now(p, t0 + 500)["left"] == FOCUS_S - 60.0)
    g = focus_start(p, t0 + 500)
    check("goes on from where it paused", round(g["ends"] - (t0 + 500)) == FOCUS_S - 60)
    check("ended once its time is up", focus_now(g, g["ends"] + 1)["phase"] == "ended")
    # the Focus page's lengths and +5 min (2026-10-29): the technique's 25 unless the owner picks another
    check("a length picked: 50 min ready at once, 25 again after", focus_now(focus_length({}, 50 * 60), t0)["left"] == 3000.0
          and focus_now(focus_length(focus_length({}, 50 * 60), 25 * 60), t0)["total"] == 1500.0)
    check("an unknown length is ignored", focus_length({}, 7 * 60) == {} and focus_now({}, t0)["total"] == FOCUS_S)
    r50 = focus_start(focus_length({}, 15 * 60), t0)
    check("a pomodoro started at its picked length; a break keeps its own", round(r50["ends"] - t0) == 900
          and focus_now({**focus_length({}, 15 * 60), "kind": "short"}, t0)["total"] == SHORT_S)
    more = focus_more(r50, wall=t0 + 60)
    check("+5 min on a running timer moves its end", round(more["ends"] - r50["ends"]) == MORE_S)
    check("+5 min on a paused one adds to what's left; ready: unchanged", focus_more({"left": 100.0})["left"] == 100.0 + MORE_S
          and focus_more({}, wall=t0) == {})
    fin, ring, what = focus_finish(g, g["ends"] + 1)
    st = focus_now(fin, g["ends"] + 2)
    check("a pomodoro done: counted, rung, its 5-minute break running from when it ended", ring and what == "focus" and st["today"] == 1
          and st["kind"] == "short" and st["phase"] == "running" and round(fin["ends"] - g["ends"]) == SHORT_S and st["set"] == 1)
    brk, ring_b, what_b = focus_finish(fin, fin["ends"] + 1)
    check("…its break over: rung, the next pomodoro ready (2 of 4)", ring_b and what_b == "break" and focus_now(brk, fin["ends"] + 2)["kind"] == "focus"
          and focus_now(brk, fin["ends"] + 2)["phase"] == "ready" and focus_words(focus_now(brk, fin["ends"] + 2)) == ("25:00", "Pomodoro 2/4"))
    s = brk
    for i in range(3):  # pomodoros 2, 3 and 4 (each break skipped by the owner: reset)
        s = focus_start(s, t0 + 10_000 * (i + 1))
        s, _r, _w = focus_finish(s, s["ends"] + 1)
        if i < 2:
            s = focus_reset(s)
    check("after the fourth: the 15-minute long break, the set starting again", focus_now(s, s["ends"] - 10)["kind"] == "long"
          and round(s["ends"] - (t0 + 30_000 + FOCUS_S + 1)) == LONG_S - 1 and focus_now(s, s["ends"] - 10)["set"] == 0
          and focus_words(focus_now(s, s["ends"] - 10))[1] == "Long break")
    late, ring_l, what_l = focus_finish(focus_start({}, t0), t0 + FOCUS_S + 3600)
    check("ended while Monday was off: counted, not rung, no break", not ring_l and what_l == "focus" and late["count"] == 1
          and focus_now(late, t0 + FOCUS_S + 3601)["kind"] == "focus" and focus_now(late, t0 + FOCUS_S + 3601)["phase"] == "ready")
    check("not ended: unchanged", focus_finish(f, t0 + 5) == (f, False, None))
    check("its words", focus_words({"kind": "focus", "phase": "running", "left": 1499.4, "today": 0, "set": 0}) == ("24:59", "Pomodoro 1/4")
          and focus_words({"kind": "focus", "phase": "paused", "left": 60, "today": 1, "set": 2}) == ("01:00", "Paused · 3/4")
          and focus_words({"kind": "short", "phase": "running", "left": 299, "today": 1, "set": 1}) == ("04:59", "Short break"))
    check("reset: a pomodoro ready, today's count kept", focus_now(focus_reset(fin), t0)["kind"] == "focus"
          and focus_now(focus_reset(fin), t0)["phase"] == "ready" and focus_reset(fin)["count"] == 1)
    check("an older stored timer (no kind) is a pomodoro", focus_now({"left": 1498.5}, t0)["kind"] == "focus"
          and focus_now({"left": 1498.5}, t0)["phase"] == "paused")
    # the clipboard
    check("password managers' copies are private",
          clip_private({'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"': None})
          and clip_private({"CanIncludeInClipboardHistory": b"\x00\x00\x00\x00"}) and not clip_private({"CanIncludeInClipboardHistory": b"\x01\x00\x00\x00"})
          and not clip_private({"text/plain": b"hi"}))
    c = clip_add([], "one", 1)
    c = clip_add(c, "two", 2)
    c = clip_add(c, "one", 3)
    check("the same text moves up", [x["text"] for x in c] == ["one", "two"] and clip_add(c, "  ", 4) == c)
    many = []
    for i in range(CLIP_KEEP + 5):
        many = clip_add(many, f"t{i}", i)
    check("keeps every copy up to CLIP_KEEP (1000: the owner's \"all the things that ever copied\"), the newest first",
          CLIP_KEEP >= 1000 and len(many) == CLIP_KEEP and many[0]["text"] == f"t{CLIP_KEEP + 4}")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "clips.json")
        clips_save(f, 1000.0, [{"text": "a", "at": 1.0}])
        check("the copies until the PC shuts down: back after a restart of Monday (the same boot), none after the PC's own restart, none "
              "from a file that isn't there", clips_load(f, 1030.0) == [{"text": "a", "at": 1.0}] and clips_load(f, 9000.0) == []
              and clips_load(os.path.join(d, "none.json"), 1.0) == [])
    check("clip_line", clip_line("\n\n  first   line  \nsecond") == "first line" and clip_line("x" * 100, 10) == "x" * 9 + "…")
    check("ago", ago(100, 130) == "now" and ago(0, 600) == "10 min" and ago(0, 7200) == "2 h" and ago(0, 90000) == "yesterday")
    # the words
    dt = _dt.datetime(2026, 10, 24, 9, 41, 30)
    check("clock 24 h", clock_words(dt, True)[:3] == ("09:41", "", "Sat 24 Oct"))
    check("clock 12 h", clock_words(_dt.datetime(2026, 10, 24, 21, 5), False)[:2] == ("9:05", "PM"))
    check("music: nothing / playing / paused / unreadable",
          music_words(None) == ("Music", "Nothing playing", None)
          and music_words({"title": "Late Night Commits", "artist": "Lofi Robo", "status": "playing"}) == ("Late Night Commits", "Lofi Robo", True)
          and music_words({"title": "X", "artist": "", "name": "Spotify", "status": "paused"}) == ("X", "Paused · Spotify", False)
          and music_words({}, readable=False)[2] is None)
    lt = last_task({"title": "Fix the checkout total", "status": "COMPLETED", "finished_at": dt.timestamp(), "project": "webshop"})
    check("last task", lt == {"title": "Fix the checkout total", "status": "COMPLETED", "at": dt.timestamp(), "where": "webshop"}
          and last_task({"status": "RUNNING"}) is None and last_task({"status": "FAILED", "agent": "hermes"})["where"] == "hermes")
    w = ai_words("idle", "All quiet", lt, h24=True, today=dt.date())
    check("AI card: the last task", w == ("All quiet", "Last: Fix the checkout total", "09:41 · webshop", ("✓ Done", "ok")))
    check("AI card: another day says the day", ai_words("idle", "All quiet", lt, True, today=dt.date() + _dt.timedelta(days=1))[2] == "Sat 09:41 · webshop")
    check("AI card: nothing yet", ai_words("idle", "All quiet", None)[1] == "No tasks yet")
    check("AI card: working says its own words", ai_words("working", "Fixing", lt) == ("Fixing", None, None, None))
    # Monday's own running tasks, every one a row (2026-10-27): the newest first, each as Core said it
    tasks = {"TASK-1": {"id": "TASK-1", "title": "Open Spotify and play some jazz", "status": "RUNNING", "current_action": "Open Spotify"},
             "TASK-2": {"id": "TASK-2", "title": "Find my photos", "status": "RUNNING", "current_action": "mcp__computer__launch_app: Photos"},
             "TASK-3": {"id": "TASK-3", "title": "Write a note", "status": "QUEUED", "current_action": "Waiting for the screen: TASK-2 is using it"}}
    steps = {"TASK-1": {"index": 2, "total": 4, "label": "Play jazz", "status": "running"}}
    check("AI rows: none running, no rows", task_rows({}) == [] and task_rows(None) == [])
    check("AI rows: one task, its plan's step (never the step's share as a percentage)",
          task_rows({"TASK-1": tasks["TASK-1"]}, steps) == ["Open Spotify and play some jazz · step 2/4"])
    rows = task_rows(tasks, steps)
    check("AI rows: three tasks, the newest first; one waiting for the screen says who has it (Monday.lease's words); one with no plan says "
          "what it is on", rows == ["Write a note · waiting for the screen (TASK-2 is using it)", "Find my photos · mcp__computer__launch_app: Photos",
                                    "Open Spotify and play some jazz · step 2/4"])
    six = {f"TASK-{i}": {"id": f"TASK-{i}", "title": f"Task {i}", "status": "RUNNING"} for i in range(1, 7)}
    rows = task_rows(six)
    check("AI rows: six tasks, AI_ROWS of them then (+N more); one Core said nothing of is Working…", AI_ROWS == 3
          and rows == [f"Task {i} · Working…" for i in range(6, 6 - AI_ROWS, -1)] + [f"(+{6 - AI_ROWS} more)"])
    check("AI rows: an approval open says so (the permission's task, or the task's own status)",
          task_rows({"TASK-9": {"id": "TASK-9", "title": "Send it", "status": "RUNNING"}}, perms={"p1": {"id": "p1", "task_id": "TASK-9"}})
          == ["Send it · waiting for your approval"]
          and task_rows({"TASK-9": {"id": "TASK-9", "title": "Send it", "status": "WAITING_FOR_PERMISSION", "current_action": "Send"}})
          == ["Send it · waiting for your approval"])
    check("AI rows: queued before its worker is up says starting, not Working…; a long title and action are cut",
          task_rows({"T": {"id": "T", "title": "Later", "status": "QUEUED"}}) == ["Later · starting"]
          and task_row({"title": "t" * 60, "status": "RUNNING", "current_action": "a" * 200}) == ("t" * 47 + "…", "a" * 119 + "…"))
    check("AI rows: a task waiting for another's folder, the same words", task_row({"title": "Fix it", "status": "QUEUED",
          "current_action": "Waiting for E:\\proj: TASK-2026-0012 is using it"})[1] == "waiting for E:\\proj (TASK-2026-0012 is using it)")
    everything = task_rows(tasks, steps) + task_rows(six) + task_rows({"TASK-9": {"title": "Send it", "status": "RUNNING"}}, perms={"p1": {"task_id": "TASK-9"}})
    check("AI rows: no percentage anywhere (no invented progress)", everything and not any("%" in r for r in everything))
    return ok


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if _check() else 1)
    print(__doc__)
