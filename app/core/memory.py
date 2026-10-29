"""Memory: what Monday keeps for the long term in the Obsidian vault (Monday/), and the brain graph built from it.

Only meaningful things are written, as plain markdown with [[wikilinks]] (so Obsidian's own graph shows the same
connections as brain.html):
- decisions ("Use FastAPI for the Monday backend", "we decided …")  -> Decisions.md
- preferences (a chosen voice, default coding agent, browser, model) -> Preferences.md
- project history (a coding agent finished and changed files)       -> Projects/<project>.md
- what Jev rated worth keeping from a finished exchange (jev.MEMORY_CLASSES): a report or researched answer -> Research/<title> (date).md
  plus a pointer line in Memories.md; a long-term fact or a project finding -> Memories.md; "session" stays in the database for a few hours
Computer actions ("opened Chrome"), chat and status questions are never written.

The brain graph is built from real sources only: the vault's notes and links, Monday's projects and the agents that
worked on them (tasks table), recent tasks as temporary activity nodes, and, for a focus, the Graphify code graph.
Self-check: python -m core.memory
"""
import json
import hashlib
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = Path(os.environ["MONDAY_VAULT"]).resolve() if os.environ.get("MONDAY_VAULT") else ROOT / "Monday"  # a test Core must not write into the owner's vault
NOTES = {"decision": "Decisions.md", "preference": "Preferences.md", "long_term": "Memories.md", "project": "Memories.md", "research": "Memories.md"}
SESSION_TTL_S = 6 * 3600  # a "session" memory is shown to the chat models this long and then forgotten; it never reaches the vault
CONTEXT_CHARS = 1400      # the most memory put in front of a model
TEMP_TASKS = 30          # recent tasks shown as temporary activity nodes
TEMP_WINDOW_S = 24 * 3600
FOCUS_HOPS = 2
FORGOTTEN = "Forgotten.md"  # what the owner told Monday to forget: kept out of every search and prompt, never deleted
STALE_DAYS = 90             # a remembered line older than this is shown as possibly out of date
_ITEM = re.compile(r"^- (?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (?P<text>.+?)(?:\s+#(?P<kind>[\w-]+))?$")
MAX_CODE_NODES = 60
_SCOPE = (r"backend|frontend|front\s*end|back\s*end|api|database|db|auth(?:entication)?|login|ui|styling|css|tests?|testing|"
          r"deployment|deploys?|hosting|server|storage|state|routing|framework|stack|mobile\s+app|web\s*app|cache|queue|logging|payments?")
_RULES = [
    ("decision", re.compile(r"^(?:let'?s\s+|we'?ll\s+|we\s+will\s+|i'?ll\s+|going\s+to\s+)?(?:use|go\s+with|stick\s+with|switch\s+to|standardi[sz]e\s+on)\s+"
                            r"(?P<what>[\w.+#/-]+(?:\s+[\w.+#/-]+){0,2})\s+(?:for|as|in)\s+(?:the\s+|our\s+|my\s+)?(?P<scope>(?:[\w-]+\s+){0,3}(?:"
                            + _SCOPE + r"))$", re.I)),
    ("decision", re.compile(r"^(?:we\s+(?:have\s+)?decided|i(?:'ve|\s+have)?\s+decided|decision:)\s+(?:to\s+|that\s+|on\s+)?(?P<text>.{4,300})$", re.I)),
    ("preference", re.compile(r"^(?:i\s+(?:prefer|like|want)\s+(?P<text>.{3,200}?)\s+(?:from\s+now\s+on|always|by\s+default)|"
                              r"(?:from\s+now\s+on|always),?\s+(?P<text2>.{3,200})|i\s+prefer\s+(?P<text3>.{3,200}))$", re.I)),
    ("fact", re.compile(r"^(?:remember|note|keep\s+in\s+mind|don'?t\s+forget)\s+(?:that\s+)?(?P<text>.{3,300})$", re.I)),
]


def classify(text):
    """(kind, sentence) when the owner stated something worth keeping, else None. Pure rules; Jev's `remember` score
    can add a request these don't catch, never remove one."""
    t = re.sub(r"^(?:hey|ok|okay)?\s*monday[,\s]+", "", (text or "").strip(), flags=re.I).strip().rstrip(".!")
    if not t or t.endswith("?"):
        return None
    for kind, rx in _RULES:
        m = rx.match(t)
        if m:
            g = {k: v for k, v in m.groupdict().items() if v}
            if kind == "decision" and "what" in g:
                return kind, f"Use {g['what']} for {g['scope']}"
            return kind, (g.get("text") or g.get("text2") or g.get("text3") or t).strip()
    return None


def _links(text, known):
    """Wrap the first mention of each name Monday knows (projects, agents, tools) in a [[wikilink]] so it connects in the graph. Text already
    inside a link is left alone."""
    out = text
    for name in sorted(known, key=len, reverse=True):
        parts = re.split(r"(\[\[.*?\]\])", out)  # the even parts are outside any link
        for i in range(0, len(parts), 2):
            parts[i], n = re.subn(rf"\b({re.escape(name)})\b", r"[[\1]]", parts[i], count=1, flags=re.I)
            if n:
                break
        out = "".join(parts)
    return out


def remember(kind, text, known=(), source=None):
    """Append one dated line to the vault note for `kind`. Returns the note's path."""
    VAULT.mkdir(parents=True, exist_ok=True)
    note = VAULT / NOTES.get(kind, "Notes.md")
    if not note.exists():
        note.write_text(f"# {note.stem}\n\nWritten by [[Monday]]. One dated line per item, newest last.\n\n", encoding="utf-8")
    body = _links(text.strip(), known)
    if kind == "preference" and ":" in text:  # "Voice: X" again when X is already the latest voice is not news
        key = text.split(":", 1)[0].strip()
        latest = [l for l in note.read_text(encoding="utf-8").splitlines() if f"· {key}:" in l]
        if latest and f"· {body}" in latest[-1]:
            return note
    line = f"- {time.strftime('%Y-%m-%d %H:%M')} · {body}" + (f" ({source})" if source else "") + f" #{kind}\n"
    with note.open("a", encoding="utf-8") as f:
        f.write(line)
    return note


def project_history(project, agent, task_id, title, files, known=()):
    """A coding agent finished work in a project: one line in Projects/<project>.md (the project's history)."""
    folder = VAULT / "Projects"
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / f"{re.sub(r'[\\\\/:*?\"<>|]', '_', project)}.md"
    if not note.exists():
        note.write_text(f"# {project}\n\nProject history kept by [[Monday]].\n\n", encoding="utf-8")
    shown = ", ".join(f"`{f}`" for f in files[:8]) + (f" and {len(files) - 8} more" if len(files) > 8 else "")
    with note.open("a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} · [[{agent}]] {title.rstrip('.')} ({task_id}); changed {shown} #history\n")
    return note


def gist(text, limit=160):
    """The first sentence of an answer, without its bullets and markdown, cut at a word."""
    t = re.sub(r"^[\s•\-*#>]+", "", (text or "").strip())
    t = re.split(r"(?<=[.!?])\s|\n", t, maxsplit=1)[0].replace("*", "")
    while re.search(r"\[[^\[\]]*\]", t):  # "[measured: 0 words]" references are for the report's reader, not for a one-line pointer
        t = re.sub(r"\s*\[[^\[\]]*\]", "", t)
    t = t.strip()
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


def short(text, limit=70):
    """A request as a title: one line, cut at a word, without an ellipsis or characters a file name can't hold."""
    t = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|…]', " ", text or "")).strip(" .")
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0]


def seed_system_note(settings: dict, projects: list, agents: list) -> None:
    """Write/overwrite Monday/System.md with real current configuration.
    Called on startup and whenever settings or projects change.
    Only non-default values are written to avoid noise on a fresh install."""
    VAULT.mkdir(parents=True, exist_ok=True)
    lines = ["# System\n\n",
             "Monday's current configuration. Written by [[Monday]] on startup.\n\n"]
    for label, key, default in (
        ("voice", "tts_provider", "browser"),
        ("coding_agent", "default_coder", "claude_code"),
        ("chat_model", "chat_model", "auto"),
    ):
        value = settings.get(key, default)
        if value and value != default:
            lines.append(f"- {label}: {value} #preference\n")
    for p in projects or []:
        name = p.get("name", "")
        path = p.get("path", "")
        if name:
            lines.append(f"- project [[{name}]] at `{path}` #project\n")
    for a in agents or []:
        if a.get("installed") and a.get("name"):
            kind = a.get("kind", "agent")
            lines.append(f"- agent [[{a['name']}]] ({kind}) #agent\n")
    (VAULT / "System.md").write_text("".join(lines), encoding="utf-8")


def keep(kind, title, text, known=(), source=None, asked=None, by=None):
    """Write what Jev rated worth keeping. -> the note written or appended to, None for a kind that never reaches the vault (ignore, session).
    decision / preference / long_term keep the owner's own words (title); research keeps the whole answer as a note of its own and points to it
    from Memories.md; project keeps a line about it."""
    if kind in ("decision", "preference", "long_term"):
        return remember(kind, title, known, source)
    if kind == "project":
        return remember(kind, f"{title}: {gist(text)}", known, source)
    if kind != "research":
        return None
    folder = VAULT / "Research"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{short(title)} ({time.strftime('%Y-%m-%d')})"
    note, n = folder / f"{stem}.md", 2
    while note.exists():  # the same question asked again the same day is a second note, not an overwrite
        note, n = folder / f"{stem} {n}.md", n + 1
    note.write_text(f"# {title}\n\n{time.strftime('%Y-%m-%d %H:%M')} · asked: “{asked or title}”" + (f" · {source}" if source else "")
                    + (f" · written by {by}" if by else "") + f"\nKept by [[Monday]], rated research by [[Jev]].\n\n{text.strip()}\n\n#research\n", encoding="utf-8")
    remember("research", f"[[{note.stem}]]: {gist(text)}", known, source)
    return note


def context(question, session=(), limit=4, chars=CONTEXT_CHARS):
    """What a model should know before it answers: what the owner stated as a preference, what they are in the middle of (`session`), and the
    vault lines that share words with the question. "" when there is nothing, so nothing is added to the prompt."""
    lines = []
    prefs = VAULT / "Preferences.md"
    if prefs.exists():  # only the owner's own words ("(message 12)"), not Monday's settings log
        lines += [f"Preference: {l[2:].strip()}" for l in prefs.read_text(encoding="utf-8").splitlines()
                  if l.startswith("- ") and re.search(r"\(message \d+\)", l)][-5:]
    lines += [f"Right now: {s}" for s in session]
    seen = set(lines)
    lines += [f"{h['note']}: {h['text']}" for h in search(question, limit) if f"{h['note']}: {h['text']}" not in seen]
    out = ""
    for l in lines:
        if len(out) + len(l) + 3 > chars:
            break
        out += f"- {l}\n"
    return ("What you remember about the owner and their work (Obsidian vault and this session). Use it when it is relevant; it is not a "
            "complete record, and never claim a memory that is not listed:\n" + out) if out else ""


def search(query, limit=8):
    """Lines in the vault that mention every word of the query (for "what did we decide about X")."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'_-]{2,}", (query or "").lower())]
    if not words or not VAULT.exists():
        return []
    hits = []
    for p in VAULT.rglob("*.md"):
        if ".obsidian" in p.parts or p.name == FORGOTTEN:
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            low = line.lower()
            score = sum(w in low for w in words)
            if line.startswith(("- ", "• ")) and score:
                hits.append((score, p.stem, line[2:].strip()))
    return [{"note": n, "text": t} for _, n, t in sorted(hits, key=lambda h: -h[0])[:limit]]


# -- what Monday remembers, for the owner to inspect, correct and forget -----------------------------------------
def _item_id(note, line):
    return hashlib.sha1(f"{note.relative_to(VAULT).as_posix()}|{line}".encode()).hexdigest()[:16]


def items(now=None):
    """Every remembered line in the vault, newest first: {id, note, at, text, kind, stale}. The id names the exact line, so a
    line changed in Obsidian meanwhile is never the one forgotten or corrected by mistake."""
    now, out = now or time.time(), []
    if not VAULT.exists():
        return out
    for p in VAULT.rglob("*.md"):
        if ".obsidian" in p.parts or p.name == FORGOTTEN:
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = _ITEM.match(line)
            if not m:
                continue
            at = time.mktime(time.strptime(m["at"], "%Y-%m-%d %H:%M"))
            out.append({"id": _item_id(p, line), "note": p.relative_to(VAULT).with_suffix("").as_posix(), "at": at, "text": m["text"],
                        "kind": m["kind"], "stale": now - at > STALE_DAYS * 86400})
    return sorted(out, key=lambda x: -x["at"])


def _find(item_id):
    for p in VAULT.rglob("*.md"):
        if ".obsidian" in p.parts or p.name == FORGOTTEN:
            continue
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            if _item_id(p, line.rstrip("\r\n")) == item_id:
                return p, lines, i
    raise KeyError("That memory isn't in the vault any more (it may have been changed in Obsidian). Reload and try again.")


def forget(item_id):
    """The owner's Forget: the line leaves its note and goes, dated, to Forgotten.md. Nothing is deleted (it can be put back by
    hand), and Forgotten.md is never searched or shown to a model."""
    p, lines, i = _find(item_id)
    line = lines.pop(i).rstrip("\r\n")
    p.write_text("".join(lines), encoding="utf-8")
    bin_ = VAULT / FORGOTTEN
    if not bin_.exists():
        bin_.write_text("# Forgotten\n\nWhat the owner told [[Monday]] to forget. Never searched, never shown to a model; move a line back to "
                        "put it back.\n\n", encoding="utf-8")
    with bin_.open("a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} · forgot, from {p.relative_to(VAULT).with_suffix('').as_posix()}: {line[2:]}\n")
    return {"note": p.stem, "line": line}


def correct(item_id, text):
    """The owner's correction: the line keeps its date and kind, says it was corrected, and holds their words."""
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("A correction needs the new words.")
    p, lines, i = _find(item_id)
    m = _ITEM.match(lines[i].rstrip("\r\n"))
    lines[i] = f"- {m['at']} · {text} (corrected {time.strftime('%Y-%m-%d')})" + (f" #{m['kind']}" if m["kind"] else "") + "\n"
    p.write_text("".join(lines), encoding="utf-8")
    return {"id": _item_id(p, lines[i].rstrip("\n")), "note": p.stem}


# -- brain graph -------------------------------------------------------------------------------------------------
_graph_cache = {}


def _graphify(project_path):
    g = Path(project_path) / "graphify-out" / "graph.json"
    if not g.exists():
        return None
    m = g.stat().st_mtime
    hit = _graph_cache.get(str(g))
    if hit and hit[0] == m:
        return hit[1]
    try:
        data = json.loads(g.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    _graph_cache[str(g)] = (m, data)
    return data


def graph(db, agents=(), focus=None, name="Monday"):
    """{"nodes": [{id, label, type, temporary?, detail?}], "links": [{source, target, kind}]} from real data only."""
    nodes, links = {}, []

    def node(nid, label, type_, **kw):
        nodes.setdefault(nid, {"id": nid, "label": label, "type": type_, **kw})
        return nid

    def link(a, b, kind="related"):
        if a != b and a in nodes and b in nodes:
            links.append({"source": a, "target": b, "kind": kind})

    me = node("monday", name, "core", detail="Monday Core: the orchestrator")
    node("jev", "Jev", "router", detail="TypeSafe System One: fast intent and tool routing")
    link(me, "jev", "routes with")
    for a in agents:
        if a.get("installed"):
            aid = node(f"agent:{a['key']}", a["name"], "agent", detail=f"{a['kind']} · {a['integration']} · login {a['auth']}")
            link(me, aid, "uses")
    node("agent:computer", "Computer", "agent", detail="Monday's own hands: apps, windows, keyboard, mouse, OCR")
    link(me, "agent:computer", "uses")
    projects = db.all("SELECT * FROM projects ORDER BY name")
    for p in projects:
        pid = node(f"project:{p['name'].lower()}", p["name"], "project", detail=p["path"])
        link(me, pid, "knows")
    for r in db.all("SELECT t.agent, p.name AS project, COUNT(*) AS n FROM tasks t JOIN projects p ON p.id=t.project_id GROUP BY 1, 2"):
        a, pj = f"agent:{r['agent']}", f"project:{r['project'].lower()}"
        if a not in nodes:
            node(a, r["agent"].replace("_", " ").title(), "agent")
            link(me, a, "uses")
        link(a, pj, f"worked on ({r['n']} task{'s' if r['n'] != 1 else ''})")
    # vault notes and their wikilinks
    if VAULT.exists():
        notes = [p for p in VAULT.rglob("*.md") if ".obsidian" not in p.parts and p.name != FORGOTTEN]
        for p in notes:  # every note first, so a link to one is never drawn as a bare "linked from the vault" node of the wrong kind
            node(f"note:{p.stem.lower()}", p.stem, "research" if p.parent.name == "Research" else "note",
                 detail=str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p))
        for p in notes:
            nid = f"note:{p.stem.lower()}"
            text = p.read_text(encoding="utf-8", errors="ignore")
            for target in dict.fromkeys(re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", text)):
                tid = _resolve(nodes, target) or node(f"note:{target.lower()}", target, "note", detail="linked from the vault")
                link(nid, tid, "links to")
    # recent tasks: temporary activity nodes (not memory)
    since = time.time() - TEMP_WINDOW_S
    for t in db.all("SELECT t.id, t.title, t.agent, t.status, t.files_changed, p.name AS project FROM tasks t "
                    "LEFT JOIN projects p ON p.id=t.project_id WHERE t.created_at >= ? ORDER BY t.created_at DESC LIMIT ?", since, TEMP_TASKS):
        tid = node(f"task:{t['id']}", t["id"], "task", temporary=True, detail=f"{t['title']} · {t['status'].lower()}")
        a = f"agent:{t['agent']}"
        if a in nodes:
            link(a, tid, "ran")
        if t["project"]:
            link(tid, f"project:{t['project'].lower()}", "in")
        for f in (t["files_changed"] or [])[:6]:
            fid = node(f"file:{f}", Path(f).name, "file", temporary=True, detail=f)
            link(tid, fid, "changed")
    if focus:
        _add_code(nodes, links, projects, focus, node, link)
        return _around(nodes, links, focus)
    return {"nodes": list(nodes.values()), "links": links, "matched": None}


def _resolve(nodes, label):
    low = label.strip().lower()
    return next((n["id"] for n in nodes.values() if n["label"].lower() == low), None)


def _add_code(nodes, links, projects, focus, node, link):
    """Graphify's code graph for a focus: matching code nodes and their direct neighbours, under their project."""
    low = focus.lower()
    for p in projects:
        g = _graphify(p["path"])
        if not g:
            continue
        by_id = {n["id"]: n for n in g.get("nodes", [])}
        hits = [n for n in g["nodes"] if low in (n.get("label") or "").lower() or low in (n.get("source_file") or "").lower()][:15]
        if not hits:
            continue
        keep = {n["id"] for n in hits}
        for e in g.get("links") or g.get("edges") or []:
            if len(keep) >= MAX_CODE_NODES:
                break
            if e["source"] in keep or e["target"] in keep:
                keep.update((e["source"], e["target"]))
        proj = f"project:{p['name'].lower()}"
        for cid in keep:
            n = by_id.get(cid)
            if n:
                node(f"code:{cid}", n.get("label") or cid, "code", detail=f"{n.get('source_file')}:{n.get('source_location', '')}".rstrip(":"))
        for n in hits:
            link(proj, f"code:{n['id']}", "contains")
        for e in g.get("links") or g.get("edges") or []:
            if e["source"] in keep and e["target"] in keep:
                link(f"code:{e['source']}", f"code:{e['target']}", e.get("relation") or "related")


def _around(nodes, links, focus):
    """Nodes within FOCUS_HOPS of anything whose label matches the focus."""
    low = focus.lower().strip()
    seeds = {n["id"] for n in nodes.values() if low in n["label"].lower() or low in (n.get("detail") or "").lower()}
    keep = set(seeds)
    frontier = set(seeds)
    for _ in range(FOCUS_HOPS):
        nxt = set()
        for l in links:
            if l["source"] in frontier and l["target"] not in keep:
                nxt.add(l["target"])
            if l["target"] in frontier and l["source"] not in keep:
                nxt.add(l["source"])
        keep |= nxt
        frontier = nxt
    return {"nodes": [dict(n, focus=n["id"] in seeds) for n in nodes.values() if n["id"] in keep],
            "links": [l for l in links if l["source"] in keep and l["target"] in keep], "focus": focus, "matched": len(seeds)}


if __name__ == "__main__":  # python -m core.memory
    assert classify("Use FastAPI for Monday backend.") == ("decision", "Use FastAPI for Monday backend")
    assert classify("Monday, remember that the staging server is on port 8080") == ("fact", "the staging server is on port 8080")
    assert classify("we decided to ship on Friday")[0] == "decision" and classify("I prefer dark mode from now on")[0] == "preference"
    assert classify("use the second voice") is None and classify("should we use FastAPI for the backend?") is None
    assert classify("open chrome") is None and classify("what time is it") is None
    assert _links("Claude Code fixed the login in Monday Core", ["Claude Code", "Monday Core"]) == "[[Claude Code]] fixed the login in [[Monday Core]]"
    assert _links("[[Open chrome and search]] with Chrome", ["Chrome"]) == "[[Open chrome and search]] with [[Chrome]]", "never a link inside a link"
    assert short("Open chrome and search garbacircle.in and give me the full seo report for that?", 40) == "Open chrome and search garbacircle.in" and short("a/b: c…") == "a b c"
    nodes = {"a": {"id": "a", "label": "Jev", "type": "router"}, "b": {"id": "b", "label": "x", "type": "code"},
             "c": {"id": "c", "label": "y", "type": "code"}, "d": {"id": "d", "label": "z", "type": "code"}}
    ls = [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}, {"source": "c", "target": "d"}]
    g = _around(nodes, ls, "jev")
    assert {n["id"] for n in g["nodes"]} == {"a", "b", "c"} and g["matched"] == 1
    import tempfile
    VAULT = Path(tempfile.mkdtemp())  # noqa: F811  (a throwaway vault: the check never writes to the owner's)
    for v in ("Zira", "Zira", "Heera", "Heera", "Zira"):
        remember("preference", f"Voice: {v}", ["Zira"])
    kept = [l for l in (VAULT / "Preferences.md").read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    assert len(kept) == 3, kept  # repeats of the current value are skipped, real changes are kept
    note = keep("research", "SEO report for garbacircle.in",
                "garbacircle.in has a serious crawl problem: Google sees no text.\n• the page is blank without JavaScript\n• the sitemap redirects",
                ["Jev"], "task:TASK-1", "give me the full seo report for that", "jev: research → fake-model")
    again = keep("research", "SEO report for garbacircle.in", "second run", (), "task:TASK-2")
    assert note.parent.name == "Research" and again != note and "[[Jev]]" in note.read_text(encoding="utf-8"), (note, again)
    assert keep("session", "x", "y") is None and keep("ignore", "x", "y") is None
    assert gist("• **garbacircle.in** has a serious crawl problem: Google sees no text.\nmore") == "garbacircle.in has a serious crawl problem: Google sees no text."
    assert len(gist("word " * 100)) <= 161 and gist("") == ""
    assert gist("Bots see almost no content [words [0,190], headings [0,14]] on this page. More.") == "Bots see almost no content on this page."
    pointer = [l for l in (VAULT / "Memories.md").read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    assert any(f"[[{note.stem}]]: garbacircle.in has a serious crawl problem" in l and l.endswith("(task:TASK-1) #research") for l in pointer), pointer
    assert any("blank" in h["text"] for h in search("garbacircle blank")), "the bullets of a kept report are found too"
    keep("preference", "I prefer short answers", "", source="message 7")
    ctx = context("garbacircle seo", ["opened the Monday project"])
    assert "Preference: " in ctx and "I prefer short answers" in ctx and "Right now: opened the Monday project" in ctx and "garbacircle" in ctx, ctx
    assert len(ctx) < CONTEXT_CHARS + 300 and "garbacircle" not in context("unrelated words here"), "only what the question shares words with"
    class _NoDB:
        def all(self, *a):
            return []
    by_label = {n["label"]: n["type"] for n in graph(_NoDB())["nodes"]}
    assert by_label.get(note.stem) == "research" and by_label.get("Memories") == "note", by_label  # Memories.md links to it first; it is still a research node
    (VAULT / "Preferences.md").unlink()
    (VAULT / "Memories.md").unlink()
    assert context("nothing matches") == "", "no memory, no block"
    # graph() matched key guarantee
    class _NoDB:
        def all(self, *a, **kw): return []
        def one(self, *a, **kw): return None

    _g = graph(_NoDB())
    assert "matched" in _g, "matched key must always be present without focus"
    assert _g["matched"] is None or _g["matched"] == 0
    _gf = graph(_NoDB(), focus="jev")
    assert "matched" in _gf, "matched key must be present with focus"
    print("graph matched-key self-check: OK")
    # seed_system_note self-check
    import tempfile as _tempfile, pathlib as _pathlib, shutil as _shutil
    _sv = _pathlib.Path(_tempfile.mkdtemp())
    _orig = VAULT
    try:
        VAULT = _sv  # noqa: F811 — redirect writes to the temp vault
        seed_system_note({"chat_model": "claude", "tts_provider": "browser", "default_coder": "claude_code"},
                         [{"name": "MyProject", "path": "/x"}],
                         [{"name": "Codex", "kind": "coding agent", "installed": True}])
        _c = (_sv / "System.md").read_text(encoding="utf-8")
        assert "MyProject" in _c, "project missing from System.md"
        assert "Codex" in _c, "agent missing from System.md"
        assert "claude" in _c, "non-default chat_model missing"
        assert "tts_provider" not in _c, "default tts_provider must NOT appear"
        assert "claude_code" not in _c, "default coding agent must NOT appear"
        # idempotent: second call overwrites, not appends
        seed_system_note({"chat_model": "claude"}, [], [])
        _c2 = (_sv / "System.md").read_text(encoding="utf-8")
        assert _c2.count("# System") == 1, "overwrite not append"
        print("seed_system_note self-check: OK")
    finally:
        VAULT = _orig  # noqa: F811
        _shutil.rmtree(_sv, ignore_errors=True)
    # the owner inspects, corrects and forgets: a forgotten line leaves every search but is never deleted
    _sv = _pathlib.Path(_tempfile.mkdtemp())
    try:
        VAULT = _sv  # noqa: F811
        remember("decision", "Use FastAPI for the backend")
        remember("preference", "Dark mode everywhere", source="message 3")
        rows = items()
        assert [r["text"] for r in rows][:2] in (["Dark mode everywhere (message 3)", "Use FastAPI for the backend"],
                                                 ["Use FastAPI for the backend", "Dark mode everywhere (message 3)"]), rows
        dec = next(r for r in rows if r["kind"] == "decision")
        assert dec["note"] == "Decisions" and not dec["stale"] and items(now=dec["at"] + (STALE_DAYS + 1) * 86400)[0]["stale"]
        new = correct(dec["id"], "Use FastAPI and SQLite for the backend")
        assert any("SQLite" in r["text"] and "corrected" in r["text"] for r in items()) and new["id"] != dec["id"]
        try:
            forget(dec["id"])
            raise AssertionError("an id from before the correction still matched")
        except KeyError:
            pass
        forget(new["id"])
        assert not search("FastAPI") and len(items()) == 1, "a forgotten line is never searched"
        assert "FastAPI and SQLite" in (_sv / FORGOTTEN).read_text(encoding="utf-8"), "…but it is kept, not deleted"
        print("memory controls self-check: OK")
    finally:
        VAULT = _orig  # noqa: F811
        _shutil.rmtree(_sv, ignore_errors=True)
    print("memory self-check: OK")
