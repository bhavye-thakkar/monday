"""The definition of done for Monday's own coding tasks (2026-10-17, after Dotpals' Talk's tasks: bridge/tasks.js makePlan, doneItem,
doneChecks; MIT, THIRD_PARTY_NOTICES.md). What the owner's words ask of the work beyond "it works": only these folders change, a folder
isn't touched, a command they name passes, new tests, no new dependencies, a commit, a push. Read by rules from the request's own words
(a phrase that is negated, "don't commit", is no item; nothing is guessed), sent to the agent as its definition of done, and checked
afterwards against what the task really did: its edits and its commands, in order (core/evidence.py steps, Monday.Run.trail).

    definition(text, test_command)          [{"kind", "text", ...}]: the tests first, then what the words ask
    bullets(done, test_command)             the definition as the agent reads it
    checks(done, files, steps, root)        [{"ok": True | False | None, "text"}] for each item but the tests (Monday checks those itself)
    verdict(checks, tests, files)           {"state": ready | not-ready | no-changes, "problems"}, and words() for the reply

    python -m core.done      self-check
"""
import os
import re

from . import evidence

_PATH = r"`?(?P<{n}>[\w.@-]+(?:[\\/][\w.@-]+)*[\\/]?)`?"
_UNTOUCHED = re.compile(r"\b(?:don'?t|do\s+not|never|without)\s+(?:touch(?:ing)?|chang(?:e|ing)|modify(?:ing)?|edit(?:ing)?)\s+"
                        r"(?:anything\s+in\s+|any\s+files?\s+in\s+|files?\s+in\s+|the\s+)?" + _PATH.format(n="p") +
                        r"(?:\s+(?:folder|directory|dir))?(?=[\s,.;:!?)]|$)", re.I)
_ONLY = re.compile(r"\bonly\s+(?:touch|change|modify|edit)\s+(?:files\s+in\s+|code\s+in\s+|the\s+)?" + _PATH.format(n="a") +
                   r"(?:\s+(?:folder|directory|dir))?(?:\s+(?:and|or)\s+(?:the\s+)?" + _PATH.format(n="b") + r")?(?=[\s,.;:!?)]|$)", re.I)
_MUST_PASS = re.compile(r"`(?P<c>[^`\n]{2,120})`\s+(?:still\s+)?(?:passes|pass(?:es|ing)?|is\s+green|must\s+pass|has\s+to\s+pass)\b|"
                        r"\b(?:make\s+sure|ensure|check\s+that)\s+`(?P<d>[^`\n]{2,120})`\s+(?:still\s+)?(?:passes|pass)\b", re.I)
_ADD_TESTS = re.compile(r"\b(?:add|write|with|include|plus)\s+(?:some\s+|new\s+|unit\s+|a\s+|the\s+|more\s+|regression\s+|missing\s+){0,3}tests?\b|"
                        r"\btest\s+coverage\b|\bcover(?:ed)?\s+(?:it\s+)?(?:by|with)\s+tests?\b", re.I)
_NO_DEPS = re.compile(r"\b(?:no|without|don'?t\s+add|do\s+not\s+add|avoid(?:ing)?)\s+(?:any\s+)?(?:new\s+|extra\s+|more\s+|other\s+)?"
                      r"(?:dependenc(?:y|ies)|deps|packages?|librar(?:y|ies)|libs)\b", re.I)
_COMMIT = re.compile(r"\bcommit(?:ting)?\b(?!\s+(?:message|hash|history|log))", re.I)
_PUSH = re.compile(r"\bpush(?:ing)?\b(?=\s*(?:$|[.,;!]|\s+(?:it|them|that|this|to\b|the\s+(?:branch|changes?|fix|work|commits?)|upstream|origin)))", re.I)
_NOT = re.compile(r"(?:\bdon'?t|\bdo\s+not|\bnever|\bwithout|\bno\s+need\s+to|\bnot|\bno)\s+(?:\w+\s+){0,2}$", re.I)
_TIDY = re.compile(r"^[.\\/]+|[\\/]+$")

LOCKFILE = re.compile(r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|bun\.lockb?|requirements[^/]*\.txt|poetry\.lock|Pipfile\.lock|"
                      r"Cargo\.lock|go\.sum|Gemfile\.lock|composer\.lock|uv\.lock)$", re.I)
MANIFEST = re.compile(r"(^|/)(package\.json|pyproject\.toml|Pipfile|Cargo\.toml|go\.mod|Gemfile|composer\.json)$", re.I)
_ADD = re.compile(r"^(?:(?:npm|pnpm|yarn|bun)\s+(?:install|i|add)|pip3?\s+install|python\s+-m\s+pip\s+install|uv\s+(?:add|pip\s+install)|"
                  r"poetry\s+add|cargo\s+add|go\s+get|gem\s+install|bundle\s+add|composer\s+require)\s+", re.I)
_INSTALL = re.compile(r"^(?:(?:npm|pnpm|yarn|bun)\s+(?:install|i|add|ci)|pip3?\s+install|python\s+-m\s+pip\s+install|uv\s+(?:add|sync|pip\s+install)|"
                      r"poetry\s+(?:add|install)|cargo\s+add|go\s+get|gem\s+install|bundle\s+(?:add|install)|composer\s+(?:require|install))\b", re.I)


def _said(rx, text):
    """The phrase is there and not negated just before it ("don't commit", "no need to push")."""
    return any(not _NOT.search(text[:m.start()]) for m in rx.finditer(text))


def _tidy(p):
    return _TIDY.sub("", str(p or "").replace("\\", "/")).strip()


def definition(text, test_command=None):
    """The definition of done a coding request's own words ask for: the project's tests first (Monday checks those itself), then each
    item the words name, in Dotpals' words."""
    text = str(text or "")
    out = [{"kind": "tests", "command": test_command, "text": f"`{test_command}` passes, run after the last change" if test_command else
            "The tests pass, run after the last change"}]
    for m in _MUST_PASS.finditer(text):
        c = " ".join((m["c"] or m["d"]).split())
        if c != test_command and all(d.get("command") != c for d in out):
            out.append({"kind": "command", "command": c, "text": f"`{c}` passes, run after the last change"})
    for m in _UNTOUCHED.finditer(text):
        p = _tidy(m["p"])
        if p and all(d.get("path") != p for d in out):
            out.append({"kind": "untouched", "path": p, "text": f"Nothing in `{p}` changes"})
    m = _ONLY.search(text)
    if m and not _NOT.search(text[:m.start()]):
        paths = list(dict.fromkeys(p for p in (_tidy(m["a"]), _tidy(m["b"])) if p))
        if paths:
            out.append({"kind": "only", "paths": paths, "text": f"Only files in {' or '.join(f'`{p}`' for p in paths)} change"})
    for kind, rx, words in (("tests-added", _ADD_TESTS, "New tests are added"), ("no-deps", _NO_DEPS, "No new dependencies are added"),
                            ("committed", _COMMIT, "The work is committed"), ("pushed", _PUSH, "The work is pushed")):
        if (_said(rx, text) if kind != "no-deps" else bool(rx.search(text))):  # "no new dependencies" is itself the negation
            out.append({"kind": kind, "text": words})
    return out


def _tests_bullet(d):
    return (f"`{d['command']}` passes" if d.get("command") else "The project’s tests pass") + ", run after your last change."


BULLETS = {
    "tests": _tests_bullet,
    "command": lambda d: f"`{d['command']}` passes, run after your last change.",
    "untouched": lambda d: f"Don’t change anything in `{d['path']}`.",
    "only": lambda d: f"Only change files in {' or '.join(f'`{p}`' for p in d['paths'])}.",
    "tests-added": lambda d: "Add tests for it.",
    "no-deps": lambda d: "Don’t add dependencies.",
    "committed": lambda d: "Commit when done.",
    "pushed": lambda d: "Push when done.",
}


def bullets(done, test_command=None):
    """The definition of done as the agent reads it, after its task (Dotpals' taskMarkdown)."""
    return "\n".join(["## Definition of done", "", *(f"- {BULLETS[d['kind']](d)}" for d in done), "",
                      f"When you've finished, run {f'`{test_command}`' if test_command else 'the tests'} once more after your final edit, "
                      "then say what you changed and how the tests went."])


def _rel(path, root):
    """A changed file as the project's own path (forward slashes, lower case on Windows), or None when it's outside the project."""
    p = str(path or "")
    if os.path.isabs(p):
        if not root:
            return None
        try:
            p = os.path.relpath(p, root)
        except ValueError:  # another drive
            return None
        if p.startswith(".."):
            return None
    p = p.replace("\\", "/").lstrip("./")
    return p.lower() if os.name == "nt" else p


def _under(f, path):
    p = path.lower() if os.name == "nt" else path
    return f == p or f.startswith(p.rstrip("/") + "/")


def _ran_after(steps, rx, last_change):
    """How a command fared, from the steps (each {"kind", "cmd", "at", "done", "ok"}): passed / failed after the last change, stale (only
    before it), not-run, or unclear (its exit unknown)."""
    runs = [s for s in steps if s.get("kind") == "run" and s.get("done") and any(rx.match(p) for p in evidence.parts(s.get("cmd") or ""))]
    if not runs:
        return "not-run"
    last = max(runs, key=lambda s: s.get("at") or 0)
    if last_change is not None and (last.get("at") or 0) < last_change:
        return "stale"
    return {True: "passed", False: "failed"}.get(last.get("ok"), "unclear")


RAN = {
    "command": lambda c: {"passed": f"`{c}` passed after the last change", "failed": f"`{c}` failed", "stale": f"`{c}` didn't run after the last change",
                          "not-run": f"`{c}` didn't run", "unclear": f"`{c}`: how it ended is unclear"},
    "committed": lambda c: {"passed": "Committed", "failed": "`git commit` failed", "stale": "Changed code after the last commit", "not-run": "Not committed",
                            "unclear": "Commit unclear"},
    "pushed": lambda c: {"passed": "Pushed", "failed": "`git push` failed", "stale": "Changed code after the last push", "not-run": "Not pushed",
                         "unclear": "Push unclear"},
}


def checks(done, files, steps, root=None):
    """Each item but the tests against what the task did: files (the paths it changed), steps (its commands and edits, in order)."""
    changed = sorted({f for f in (_rel(p, root) for p in files or []) if f})
    edits = [at for at, _ in evidence.code_edits(steps or [], root)]
    last_change = max(edits) if edits else None
    ran = [p for s in steps or [] if s.get("kind") == "run" and s.get("done") and s.get("ok") is not False for p in evidence.parts(s.get("cmd") or "")]
    out = []
    for d in done:
        k = d["kind"]
        if k == "untouched":
            hit = [f for f in changed if _under(f, d["path"])]
            out.append({"ok": not hit, "text": f"Changed {', '.join(hit[:3])} in {d['path']}" if hit else f"Nothing in {d['path']} changed"})
        elif k == "only":
            away = [f for f in changed if not any(_under(f, p) for p in d["paths"])]
            out.append({"ok": not away, "text": f"Changed {', '.join(away[:3])} outside {' or '.join(d['paths'])}" if away else
                        f"Only files in {' or '.join(d['paths'])} changed"})
        elif k in RAN:
            rx = re.compile(re.escape(" ".join(d["command"].split())) + r"(?:\s|$)", re.I) if k == "command" else \
                evidence.GIT_COMMIT if k == "committed" else evidence.GIT_PUSH
            state = _ran_after(steps or [], rx, last_change)
            out.append({"ok": {"passed": True, "unclear": None}.get(state, False), "text": RAN[k](d.get("command"))[state]})
        elif k == "tests-added":
            tests = [f for f in changed if evidence.TEST_FILE.search(f)]
            out.append({"ok": bool(tests), "text": f"Changed {', '.join(tests[:3])}" if tests else "No test files were added or changed"})
        elif k == "no-deps":
            added = next((p for p in ran if _ADD.match(p) and any(w and not re.search(r"^-|[<>&]|^\.$|\.(?:txt|toml|cfg|in)$", w, re.I)
                                                                 for w in p[_ADD.match(p).end():].split())), None)
            locks = [f for f in changed if LOCKFILE.search(f)]
            manifest = next((f for f in changed if MANIFEST.search(f)), None)
            out.append({"ok": False, "text": f"Added a package: `{added[:70]}`"} if added else
                       {"ok": False, "text": f"Changed {', '.join(locks[:3])}"} if locks else
                       {"ok": False, "text": f"Changed {manifest} and ran an install"} if manifest and any(_INSTALL.match(p) for p in ran) else
                       {"ok": True, "text": "No new dependencies"})
    return out


def verdict(checks_, tests=None, files=None):
    """Where the task stands on its definition of done: ready (every check passed, and the tests too when they were run), not-ready (the
    problems), or no-changes (it changed no code). tests: Monday's own verdict on them (Monday.check_code), or None."""
    problems = [c["text"] for c in checks_ if c["ok"] is False]
    if tests and tests.get("changed") and tests.get("state") not in ("passing", None):
        problems.insert(0, tests.get("line") or "The tests didn't pass after the last change")
    if not files:
        return {"state": "no-changes", "problems": problems, "checks": checks_}
    return {"state": "not-ready" if problems else "ready", "problems": problems, "checks": checks_}


def words(name, v):
    """What Monday says about it, in one sentence (Dotpals' verdictWords), then the checks in a line."""
    marks = " · ".join(("✓ " if c["ok"] else "✗ " if c["ok"] is False else "? ") + c["text"] for c in v["checks"])
    head = {"ready": f"{name}'s done, and it checks out against your definition of done.",
            "not-ready": f"{name} says it's done, but it isn't ready yet: {v['problems'][0][0].lower() + v['problems'][0][1:] if v['problems'] else ''}"
                         + (f" (and {len(v['problems']) - 1} more)" if len(v["problems"]) > 1 else "") + ".",
            "no-changes": f"{name} finished without changing any code."}[v["state"]]
    return head + (f" Definition of done: {marks}." if marks else "")


if __name__ == "__main__":
    d = definition("Add a rate limit to the login route. Only change files in src/api and don't touch `migrations/`. Make sure `npm run lint` "
                   "passes, add tests, no new dependencies, then commit and push it.", "npm test")
    assert [x["kind"] for x in d] == ["tests", "command", "untouched", "only", "tests-added", "no-deps", "committed", "pushed"], d
    assert d[1]["command"] == "npm run lint" and d[2]["path"] == "migrations" and d[3]["paths"] == ["src/api"], d
    assert [x["kind"] for x in definition("Fix the VAT rounding. Don't commit anything, and no need to push.", "pytest -q")] == ["tests"], \
        "negated: no commit or push item"
    assert [x["kind"] for x in definition("refactor the cart; the commit message should say why")] == ["tests"], "a commit message isn't a commit"
    assert [x["kind"] for x in definition("only touch lib and docs, with tests")] == ["tests", "only", "tests-added"] and \
        definition("only touch lib and docs")[1]["paths"] == ["lib", "docs"]
    assert definition("make the button bigger")[0]["text"] == "The tests pass, run after the last change"
    b = bullets(d, "npm test")
    assert b.startswith("## Definition of done\n\n- `npm test` passes, run after your last change.\n- `npm run lint` passes") and \
        "- Don’t change anything in `migrations`." in b and "- Push when done." in b and b.endswith("then say what you changed and how the tests went."), b

    root = os.path.abspath(os.sep + "proj")
    S = lambda kind, at, cmd=None, files=(), ok=True: {"kind": kind, "at": at, "cmd": cmd, "files": list(files), "done": True, "ok": ok,  # noqa: E731
                                                      "patch": {"minus": [], "plus": ["x"]}}
    steps = [S("run", 1, "npm run lint", ok=False), S("edit", 2, files=[os.path.join(root, "src", "api", "login.ts")]),
             S("edit", 3, files=[os.path.join(root, "src", "api", "login.test.ts")]), S("run", 4, "npm run lint"), S("run", 5, "npm test"),
             S("run", 6, "git add -A && git commit -m limit")]
    files = [os.path.join(root, "src", "api", "login.ts"), os.path.join(root, "src", "api", "login.test.ts")]
    c = checks(d[1:], files, steps, root)
    assert [(x["ok"], x["text"]) for x in c] == [
        (True, "`npm run lint` passed after the last change"), (True, "Nothing in migrations changed"), (True, "Only files in src/api changed"),
        (True, "Changed src/api/login.test.ts"), (True, "No new dependencies"), (True, "Committed"), (False, "Not pushed")], c
    v = verdict(c, {"changed": True, "state": "passing", "line": "Tests passed"}, files)
    assert v["state"] == "not-ready" and v["problems"] == ["Not pushed"], v
    assert words("Codex", v).startswith("Codex says it's done, but it isn't ready yet: not pushed. Definition of done: ✓ `npm run lint` passed"), words("Codex", v)
    bad = checks(d[1:], files + [os.path.join(root, "migrations", "001.sql"), os.path.join(root, "package-lock.json")],
                 steps + [S("edit", 7, files=[os.path.join(root, "x.ts")]), S("run", 8, "npm install lodash")], root)
    assert [x["text"] for x in bad if x["ok"] is False] == [
        "`npm run lint` didn't run after the last change", "Changed migrations/001.sql in migrations",
        "Changed migrations/001.sql, package-lock.json outside src/api", "Added a package: `npm install lodash`",
        "Changed code after the last commit", "Not pushed"], [x["text"] for x in bad if x["ok"] is False]
    assert verdict(c[:-1], {"changed": True, "state": "passing"}, files)["state"] == "ready" and \
        words("Codex", verdict(c[:-1], None, files)).startswith("Codex's done, and it checks out")
    assert verdict([], None, [])["state"] == "no-changes" and verdict([], {"changed": True, "state": "failing", "line": "Tests failed"}, files)["problems"] == ["Tests failed"]
    assert checks([{"kind": "no-deps"}], [os.path.join(root, "package.json")], [S("run", 1, "npm ci")], root)[0]["text"] == "Changed package.json and ran an install"
    assert checks([{"kind": "no-deps"}], [], [S("run", 1, "pip install -r requirements.txt")], root)[0]["ok"], "installing what's listed isn't a new dependency"
    print("done self-check: OK (a request's definition of done read from its words, negations left out; told to the agent; checked against "
          "its edits and commands in order; ready, not ready, no changes)")
