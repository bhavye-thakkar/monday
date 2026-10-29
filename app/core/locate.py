"""Where a project is (2026-10-13): "open the Monday code in VS Code" while the owner works on another drive, with the project on their
Desktop. Staged, never a guess, never a drive-wide scan on its own:
1. what is known: the registry (the projects table) and VS Code's own record of recently opened folders (its storage.json, read only);
2. the folders the owner approved (settings.search_roots; the Desktop and Documents by default), walked a few levels deep, skipping
   dependency, build, cache and system folders, matching folder names and project markers (.git, package.json, pyproject.toml…);
3. anything wider (the home folder, a whole drive) only after the owner's yes (a permission card; the executor asks), remembered in
   search_roots so the same scope isn't asked for twice; "forget the search folders" revokes it;
4. several matches: their real paths are listed and the owner picks ("the one on my Desktop", "the second one");
5. the chosen folder opens in the editor (computer.open_project verifies a window) and, for VS Code, its own record of the active window
   is read back to confirm the exact path.
Self-check: python -m core.locate
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote

from . import files

SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv", "env", ".cache", "dist", "build", "target", "out", ".next", ".nuxt", "bin",
        "obj", "appdata", "$recycle.bin", "system volume information", "windows", "program files", "program files (x86)", "programdata",
        ".idea", ".vs", "library", "site-packages", "conda-pkgs", "pipcache", "hf", "tmp", "temp", ".gradle", ".m2", ".npm", ".nuget", "jmods",
        "include", "lib", "lib64", "share", "onedrive - personal", "windowsapps", "wpsystem", "config.msi", "gradle-caches", ".tox", "coverage"}
MARKERS = (".git", "package.json", "pyproject.toml", "setup.py", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "CMakeLists.txt", ".vscode",
           "requirements.txt", "pubspec.yaml", "*.code-workspace", "*.sln")
DEPTH = 4                 # levels below an approved root
MAX_DIRS = 20000          # ponytail: a bounded walk per request (~0.5 s on this PC); an index if that ever takes seconds
DEFAULT_ROOTS = ("desktop", "documents")
CHOICE_S = 120            # how long an answer to "which one?" is waited for
# VS Code's own records, read only. APPDATA is redirected inside a packaged process (CLAUDE.md), so the real path is spelled out.
VSCODE_USER = Path.home() / "AppData" / "Roaming" / "Code" / "User"
NOISE = {"the", "my", "our", "a", "an", "project", "projects", "repo", "repository", "codebase", "code", "folder", "directory", "app",
         "application", "workspace", "source", "sources", "src"}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def words_of(ref):
    """'the Monday code' -> ['monday']; "monday's code" -> ['monday']; 'campus connect app' -> ['campus', 'connect']."""
    n = norm(re.sub(r"[’']s\b", "", str(ref or "")))
    return [w for w in n.split() if w not in NOISE] or n.split()


def label_of(ref):
    """The project as the owner said it, for the wording: 'the campus connect project' -> 'campus connect'."""
    return re.sub(r"^(?:the|my|our)\s+|\s+(?:project|projects|repo|repository|codebase|code|folder|app)$", "", str(ref or "").strip(), flags=re.I).strip() or str(ref or "")


def from_uri(uri):
    """'file:///c%3A/Users/x/My%20Project' -> Path('C:/Users/x/My Project'), or None."""
    if not str(uri or "").startswith("file:///"):
        return None
    p = unquote(str(uri)[8:])
    return Path(p[0].upper() + p[1:]) if re.match(r"^[a-zA-Z]:", p) else Path("/" + p)


def vscode_recent(user=VSCODE_USER):
    """Folders VS Code opened, most recent first, from its workspaceStorage (one folder per workspace, dated) and storage.json. Read only."""
    seen, out = set(), []

    def add(p, when):
        if p and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append((when, p))
    try:
        for d in (user / "workspaceStorage").iterdir():
            f = d / "workspace.json"
            if f.exists():
                try:
                    add(from_uri(json.loads(f.read_text(encoding="utf-8")).get("folder")), f.stat().st_mtime)
                except (OSError, ValueError):
                    continue
    except OSError:
        pass
    try:
        s = json.loads((user / "globalStorage" / "storage.json").read_text(encoding="utf-8"))
        last = ((s.get("windowsState") or {}).get("lastActiveWindow") or {}).get("folder")
        add(from_uri(last), time.time() + 1)  # the window open now: first of all
        for ws in ((s.get("profileAssociations") or {}).get("workspaces") or {}):
            add(from_uri(ws), 0)
    except (OSError, ValueError):
        pass
    return [p for _, p in sorted(out, key=lambda x: -x[0])]


def vscode_active_folder(user=VSCODE_USER, since=None):
    """The folder of VS Code's active window, by its own record (storage.json), or None. `since`: None too when the record is older
    than that (VS Code writes it when a window closes, not when one opens: 2026-10-16 it still named the window active at 16:20 after
    Monday opened E:\\monday\\app, whose window was there, titled "app")."""
    f = user / "globalStorage" / "storage.json"
    try:
        if since is not None and f.stat().st_mtime < since:
            return None
        s = json.loads(f.read_text(encoding="utf-8"))
        return from_uri(((s.get("windowsState") or {}).get("lastActiveWindow") or {}).get("folder"))
    except (OSError, ValueError):
        return None


def markers(path):
    try:
        names = {e.name.lower() for e in os.scandir(path)}
    except OSError:
        return []
    out = [m for m in MARKERS if not m.startswith("*") and m.lower() in names]
    out += [m for m in MARKERS if m.startswith("*") and any(n.endswith(m[1:]) for n in names)]
    return out


def match(name, words):
    """How well a folder name fits the words: 3 exact, 2 every word in order, 1 every word somewhere, 0 no."""
    n = norm(name)
    if n == " ".join(words):
        return 3
    if " ".join(words) in n:
        return 2
    return 1 if all(w in n.split() or w in n for w in words) else 0


def walk(root, words, depth=DEPTH, budget=MAX_DIRS):
    """Folders under `root` (to `depth` levels) whose name fits the words, with their project markers; skips SKIP and hidden folders.
    Bounded by `budget` folders looked at. -> [(path, fit, markers)]."""
    out, seen = [], 0
    stack = [(Path(root), 0)]
    while stack and seen < budget:
        d, lvl = stack.pop()
        try:
            entries = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        seen += 1
        for e in entries:
            low = e.name.lower()
            if low in SKIP or (low.startswith(".") and low != ".vscode"):
                continue
            fit = match(e.name, words)
            if fit:
                out.append((Path(e.path), fit, markers(e.path)))
            if lvl + 1 < depth:
                stack.append((Path(e.path), lvl + 1))
    return out


def approved_roots(db):
    """The folders the owner lets Monday search: settings.search_roots, else their Desktop and Documents."""
    saved = db.setting("search_roots")
    if isinstance(saved, list) and saved:
        return [Path(p) for p in saved if p]
    return [p for p in (files.known_folder(n) for n in DEFAULT_ROOTS) if p]


def approve_root(db, path):
    roots = [str(p) for p in approved_roots(db)]
    if str(path) not in roots:
        roots.append(str(path))
    db.set_setting("search_roots", roots)
    return roots


def revoke_roots(db):
    """Back to the defaults (the Desktop and Documents): the owner's "forget the search folders"."""
    db.set_setting("search_roots", None)


def wider_scopes(roots):
    """What a wider search would mean, for the owner's yes: their home folder and each fixed drive not already approved."""
    scopes = [Path.home()]
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        d = Path(f"{letter}:/")
        if d.exists():
            scopes.append(d)
    done = {str(r).lower().rstrip("\\/") for r in roots}
    return [s for s in scopes if str(s).lower().rstrip("\\/") not in done]


def candidates(ref, projects, roots, recent=None):
    """Stages 1 and 2: the registry, VS Code's recents, then the approved roots. -> [{"path", "name", "source", "fit", "markers"}], best
    first, one per folder; a registered project named like the words is first of all."""
    words = words_of(ref)
    if not words:
        return []
    found = {}

    def add(path, source, fit, marks=None):
        p = Path(path)
        key = str(p).lower().rstrip("\\/")
        if not p.is_dir():
            return
        if key not in found or found[key]["fit"] < fit:
            found[key] = {"path": str(p), "name": p.name, "source": source, "fit": fit, "markers": marks if marks is not None else markers(p)}
    for p in projects or []:
        fit = max(match(p.get("name") or "", words), match(Path(p.get("path") or "").name, words))
        if fit:
            add(p["path"], "registry", fit + 1)  # what the owner registered outranks a folder of the same name elsewhere
    if recent is None:  # a folder VS Code opened inside AppData or a cache is no project of the owner's
        recent = [p for p in vscode_recent() if not ({x.lower() for x in p.parts} & SKIP)]
    for p in recent:
        fit = match(p.name, words)
        if fit:
            add(p, "vs code recent", fit)
    for root in roots:
        for p, fit, marks in walk(root, words):
            add(p, f"under {root}", fit, marks)
    rows = sorted(found.values(), key=lambda r: (-r["fit"], -len(r["markers"]), len(r["path"])))
    return rows


def confident(rows):
    """The one folder to open without asking: a sole candidate, or a best one that fits better than every other, with project markers
    (or from the registry). Otherwise None: the owner picks."""
    if not rows:
        return None
    best = rows[0]
    if len(rows) == 1 and (best["markers"] or best["source"] == "registry"):
        return best
    others = [r for r in rows[1:] if r["fit"] >= best["fit"]]
    return best if not others and (best["markers"] or best["source"] == "registry") else None


def pick(answer, options):
    """The owner's choice among listed folders: "the one on my Desktop", "the E drive one", "the second", "number 2", a folder word."""
    t = norm(answer)
    ordinal = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "last": len(options)}
    m = re.search(r"\b(first|second|third|fourth|fifth|sixth|last)\b", t) or re.search(r"(?:\bnumber\s*|\boption\s*|^)(\d)\b", t)
    if m:  # "the second one", "number 2"; never a bare "one" ("the one on my Desktop")
        n = ordinal.get(m.group(1)) or int(m.group(1))
        if 1 <= n <= len(options):
            return options[n - 1]
    m = re.search(r"\b([a-z])\s*(?:drive|colon)\b", t)
    if m:
        hits = [o for o in options if o["path"].lower().startswith(f"{m.group(1)}:")]
        if len(hits) == 1:
            return hits[0]
    for key in ("desktop", "documents", "downloads", "onedrive", "github"):
        if key in t:
            hits = [o for o in options if key in o["path"].lower()]
            if len(hits) == 1:
                return hits[0]
    for o in options:  # a parent folder's name, or the full path
        parts = [norm(x) for x in Path(o["path"]).parts[1:]]
        if any(p and len(p) > 2 and re.search(rf"\b{re.escape(p)}\b", t) for p in parts) and sum(
                1 for x in options if any(q and len(q) > 2 and re.search(rf"\b{re.escape(q)}\b", t) for q in [norm(y) for y in Path(x["path"]).parts[1:]])) == 1:
            return o
    return None


def listing(ref, rows):
    return f"I found {len(rows)} folders that could be the {ref} project:\n" + "\n".join(
        f"{i}. {r['path']}" + (f" ({', '.join(r['markers'][:3])})" if r["markers"] else "") for i, r in enumerate(rows[:6], 1)) + \
        "\nSay which (“the one on my Desktop”, “the second one”). Which one?"


def with_path(route, chosen):
    """The saved route with the chosen folder on its open_project step: the executor opens that path, and registers it."""
    steps = [{**s, "args": {**s["args"], **({"path": chosen["path"], "project": chosen["name"]} if (s["tool"], s["action"]) == ("applications", "open_project") else {})}}
             for s in route.get("steps") or []]
    return {**route, "steps": steps}


async def find(core, run, ref):
    """Stages 1-4 for the executor. -> {"path", "name", "source"} to open; {"ask": text} when the owner must pick or say no (the choice is kept
    on the Core, CHOICE_S); {"text": why} when nothing was found. Wider folders are searched only after the owner's yes (run.ask), remembered."""
    projects = core.db.all("SELECT * FROM projects")
    roots = approved_roots(core.db)
    rows = candidates(ref, projects, roots)
    ref = label_of(ref)
    run.log(f"locate {ref!r}: {len(rows)} candidate(s) from the registry, VS Code's recents and {len(roots)} approved folder(s)")
    one = confident(rows)
    if one:
        return one
    if rows:
        core.project_choice = {"at": time.time(), "route": run.route, "options": rows[:6], "ref": ref}
        return {"ask": listing(ref, rows)}
    scopes = wider_scopes(roots)
    if not scopes:
        return {"text": f"I couldn't find a {ref} project in your known workspaces or in {', '.join(str(r) for r in roots)}."}
    title = f"Search {', '.join(str(s) for s in scopes)} for the {ref} project?"
    detail = (f"I couldn't find it in your known workspaces or in {', '.join(str(r) for r in roots)}. Searching there means reading folder names "
              f"{DEPTH} levels deep (never file contents; dependency and system folders skipped). I'll remember your yes for next time; "
              "say “forget the search folders” to take it back. Or tell me the path instead.")
    if not await run.ask("filesystem", title, detail):
        return {"text": f"I couldn't find the {ref} project in your known places, and you didn't want me to look further. Tell me its path, "
                        "or add it with “add project <folder> as <name>”."}
    for s in scopes:
        approve_root(core.db, s)
    rows = candidates(ref, projects, scopes)
    run.log(f"locate {ref!r}: {len(rows)} candidate(s) under {', '.join(str(s) for s in scopes)}")
    one = confident(rows)
    if one:
        return one
    if rows:
        core.project_choice = {"at": time.time(), "route": run.route, "options": rows[:6], "ref": ref}
        return {"ask": listing(ref, rows)}
    return {"text": f"I searched {', '.join(str(s) for s in scopes)} and found no folder named like “{ref}” with a project in it. Tell me its path."}


if __name__ == "__main__":  # python -m core.locate: the stages with a made-up disk; nothing of the owner's is read except VS Code's records
    import tempfile
    assert words_of("the Monday code") == ["monday"] and words_of("my campus connect app") == ["campus", "connect"] and words_of("project") == ["project"]
    assert words_of("monday's code") == ["monday"] and words_of("Monday’s project") == ["monday"], "a possessive names the project, not an 's' folder"
    assert label_of("the campus connect project") == "campus connect" and label_of("Monday") == "Monday" and label_of("project") == "project"
    assert from_uri("file:///c%3A/Users/x/My%20Project") == Path("C:/Users/x/My Project") and from_uri("vscode-remote://x") is None
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for sub in ("Desktop/Monday", "Desktop/monday-old", "Documents/GitHub/monday", "Desktop/node_modules/monday", "Desktop/other/deep/Monday"):
            (root / sub).mkdir(parents=True)
        (root / "Desktop/Monday/.git").mkdir()
        (root / "Desktop/Monday/pyproject.toml").write_text("")
        (root / "Documents/GitHub/monday/package.json").write_text("{}")
        hits = walk(root, ["monday"])
        paths = {str(p).replace(str(root), "")[1:].replace("\\", "/") for p, _, _ in hits}
        assert "Desktop/Monday" in paths and "Documents/GitHub/monday" in paths and "Desktop/other/deep/Monday" in paths, paths
        assert "Desktop/node_modules/monday" not in paths, "dependency folders are skipped"
        rows = candidates("the Monday code", [], [root / "Desktop", root / "Documents"], recent=[])
        assert rows[0]["path"].endswith("Monday") and rows[0]["markers"] and confident(rows) is None, "two good matches: the owner picks"
        assert pick("the one in documents", rows)["path"].lower().endswith("github\\monday") or pick("the one in documents", rows)["path"].lower().endswith("github/monday")
        assert pick("the first one", rows) == rows[0] and pick("number 2", rows) == rows[1] and pick("hmm", rows) is None
        assert pick("the one in GitHub", rows)["path"].lower().endswith("monday") and "github" in pick("the one in GitHub", rows)["path"].lower()
        reg = candidates("monday", [{"name": "Monday Core", "path": str(root / "Desktop/Monday")}], [root / "Documents"], recent=[])
        assert confident(reg) and confident(reg)["source"] == "registry", "a registered project outranks a folder of the same name elsewhere"
        only = candidates("monday", [], [root / "Documents"], recent=[])
        assert confident(only) and confident(only)["path"].endswith("monday"), "a sole candidate with markers opens without asking"
        rec = candidates("monday", [], [], recent=[root / "Desktop/Monday"])
        assert rec and rec[0]["source"] == "vs code recent"
        assert not candidates("nothing here", [], [root], recent=[])
        d2 = root / "Desktop/Monday"
        assert with_path({"steps": [{"tool": "applications", "action": "open_project", "args": {"ref": "monday"}}]}, {"path": str(d2), "name": "Monday"})["steps"][0]["args"]["path"] == str(d2)
        assert "Desktop" in str(listing("monday", rows)) and "Which one" in listing("monday", rows)
        (root / "globalStorage").mkdir()  # VS Code's own record speaks only when written after the open began
        (root / "globalStorage/storage.json").write_text(json.dumps({"windowsState": {"lastActiveWindow": {"folder": "file:///e%3A/monday/app"}}}))
        assert str(vscode_active_folder(root, since=time.time() - 60)).lower().replace("/", "\\") == "e:\\monday\\app"
        assert vscode_active_folder(root, since=time.time() + 60) is None and vscode_active_folder(root / "nowhere") is None
    recent = vscode_recent()
    print("vs code recents:", len(recent), "(the first:", str(recent[0]) if recent else "none", ")")
    print("locate self-check: OK")
