"""Monday's chat, its words (2026-10-15, the owner: "a chat interface like the earlier one"): what core/qtwindow.py's Chat page shows,
from Core's messages and events. The WinForms view that lived here was retired on 2026-10-19; these pure parts stayed:

- md_blocks / md_spans: the markdown Monday's models write, as blocks and spans to draw (headings, lists, bold, italic, code);
- plain, when, took: a message's text without markdown, a chat's time, how long a task took;
- step_lines, tests_line: a task card's latest steps and whether the code as it stands was tested (Core's line, ✓ ✗ ? !);
- SUGGESTIONS: a new chat's four commands that fill the box (nothing is sent by itself).

    python -m core.chat --check     the pure parts (markdown, the chats' times, a task's steps and tests)
"""
import re
import time

SUGGESTIONS = ("What can you do?", "Open Notepad and write a shopping list", "Play Blinding Lights on Spotify",
               "Search the web for today's news")
STATES = {  # a task's state -> (words, palette key)
    "QUEUED": ("Queued", "AZURE"), "PLANNING": ("Planning", "AZURE"), "RUNNING": ("Running", "AZURE"), "EXECUTING": ("Running", "AZURE"),
    "WAITING_FOR_PERMISSION": ("Waiting for you", "AMBER"), "COMPLETED": ("Done", "GOOD"), "FAILED": ("Failed", "BAD"),
    "CANCELLED": ("Stopped", "GREY")}
RUNNING = ("QUEUED", "PLANNING", "RUNNING", "EXECUTING", "WAITING_FOR_PERMISSION")
STEPS_SHOWN = 6  # a task card's latest steps (Codex's live list); the count says how many there were
STEP_MARK = {"done": "✓", "completed": "✓", "ok": "✓", "running": "●", "failed": "✗", "error": "✗", "skipped": "–"}


# -- the pure parts ------------------------------------------------------------------------------------------------------------------
def md_blocks(text):
    """The markdown Monday's models write, as blocks: ("h", level, text) · ("li", marker, text, depth) · ("code", lang, text) ·
    ("quote", text) · ("hr",) · ("p", text). A paragraph keeps its line breaks; a fence left open (a reply cut short) is still code."""
    out, code, lang, para = [], None, "", []

    def flush():
        if para:
            out.append(("p", "\n".join(para)))
            para.clear()
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if code is not None:
            if line.strip().startswith("```"):
                out.append(("code", lang, "\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush()
            code, lang = [], s[3:].strip()
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            flush()
            out.append(("h", len(m.group(1)), m.group(2).strip()))
            continue
        m = re.match(r"(\s*)([-*+•]|\d+[.)])\s+(.*)", line)
        if m:
            flush()
            out.append(("li", m.group(2), m.group(3).strip(), len(m.group(1).expandtabs(4)) // 2))
            continue
        if re.fullmatch(r"(?:[-*_]\s*){3,}", s):
            flush()
            out.append(("hr",))
            continue
        if s.startswith(">"):
            flush()
            out.append(("quote", s.lstrip(">").strip()))
            continue
        if not s:
            flush()
            continue
        para.append(s)
    if code is not None:
        out.append(("code", lang, "\n".join(code)))
    flush()
    return out


_SPAN = re.compile(r"(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|(?<![\w*])\*[^*\s][^*\n]*\*(?!\w)|(?<!\w)_[^_\s][^_\n]*_(?!\w)|\[[^\]\n]+\]\([^)\s]+\))")


def md_spans(text):
    """A line's inline markdown as (text, style): "" plain, "b" bold, "i" italic, "code", "link" (its words; the address stays out)."""
    out = []
    for part in _SPAN.split(text or ""):
        if not part:
            continue
        if part.startswith("`"):
            out.append((part[1:-1], "code"))
        elif part.startswith(("**", "__")):
            out.append((part[2:-2], "b"))
        elif part.startswith(("*", "_")):
            out.append((part[1:-1], "i"))
        elif part.startswith("["):
            out.append((re.match(r"\[([^\]]+)\]", part).group(1), "link"))
        else:
            out.append((part, ""))
    return out


def web_link(url):
    """A link clicked in a reply, as the owner's browser may open it: a web address only (a reply's words are a model's, never a way
    to start a program or open a file), else None. 2026-10-16: research answers list their sources, and a click did nothing."""
    url = (url or "").strip()
    return url if re.match(r"^https?://[^\s\"'<>]+$", url, re.I) else None


def plain(text):
    """What a formatted reply says, without its markdown: what Copy puts on the clipboard."""
    lines = []
    for b in md_blocks(text):
        if b[0] == "li":
            lines.append(("  " * b[3]) + ("• " if not b[1][0].isdigit() else b[1] + " ") + "".join(t for t, _ in md_spans(b[2])))
        elif b[0] in ("p", "quote"):
            lines.append("".join(t for t, _ in md_spans(b[1])))
        elif b[0] == "h":
            lines.append("".join(t for t, _ in md_spans(b[2])))
        elif b[0] == "code":
            lines.append(b[2])
    return "\n".join(lines)


def when(ts, now=None):
    """A chat's time as the list shows it: 14:05 today, Mon 14:05 this week, 12 Oct earlier, N/A unknown."""
    if not ts:
        return "N/A"
    now = now or time.time()
    t, n = time.localtime(ts), time.localtime(now)
    if (t.tm_year, t.tm_yday) == (n.tm_year, n.tm_yday):
        return time.strftime("%H:%M", t)
    if now - ts < 6 * 86400:
        return time.strftime("%a %H:%M", t)
    return time.strftime("%d %b", t).lstrip("0")


def personal_suggestions(todos=(), tasks=(), projects=(), n=4):
    """A new chat's suggestions from the owner's own things (the owner, 2026-10-15: "like Meta Muse": proactive, from what they mentioned
    once): an open to-do, the last task that failed, their projects; the fixed ones fill the rest. Each only fills the box."""
    out = [f"Do my to-do: {t['title']}" for t in todos if t.get("title") and not t.get("done") and not t.get("archived")][:2]
    out += [f"Try again: {t['title']}" for t in tasks if t.get("status") == "FAILED" and t.get("title")][:1]
    out += [f"Open {p['name']} in VS Code" for p in projects if p.get("name")][:1]
    return list(dict.fromkeys(out + list(SUGGESTIONS)))[:n]


def step_lines(steps):
    """A task's steps as its card lists them: (mark, label), the latest STEPS_SHOWN."""
    out = [(STEP_MARK.get((s.get("status") or "").lower(), "·"), s.get("label") or s.get("text") or s.get("action") or "")
           for s in steps or () if isinstance(s, dict)]
    return [x for x in out if x[1]][-STEPS_SHOWN:]


TEST_MARK = {"passing": ("✓", "GOOD"), "failing": ("✗", "BAD"), "unclear": ("?", "AMBER"), "stale": ("!", "AMBER"),
             "untested": ("!", "AMBER"), "running": ("●", "AZURE")}


def tests_line(t):
    """A coding task's tests as its card says them: Core's own line (core/evidence.py `line`, TASK_TESTS) with a mark, and a test the
    agent changed to make it pass said too. (text, palette key), or None when no code changed (`changed`) or Core said nothing."""
    tests = (t or {}).get("tests")
    if not tests or not tests.get("changed") or not tests.get("line"):
        return None
    mark, color = TEST_MARK.get(tests.get("state"), ("·", "MUTED"))
    if tests.get("weakened"):  # passing because a test was changed is not a pass to show in green
        return f"{mark}  {tests['line']}\n!  The agent {tests['weakened']}", "BAD" if color == "BAD" else "AMBER"
    return f"{mark}  {tests['line']}", color


def took(t):
    """How long a finished task took, from its own times; "" while it runs or when they aren't there."""
    start, end = t.get("started_at") or t.get("created_at"), t.get("completed_at") or t.get("finished_at")
    if t.get("status") in RUNNING or not (start and end) or end < start:
        return ""
    s = end - start
    return f"{s:.1f} s" if s < 60 else f"{int(s // 60)} min {int(s % 60)} s"


# -- the view --------------------------------------------------------------------------------------------------------------------------


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        reply = ("# Plan\nI'll do **two** things:\n- open *Notepad*\n- type `hello`\n  - then save\n1. first\n2) second\n\n"
                 "> a quote\n\n---\n```python\nprint('hi')\n```\nsee [the docs](https://x.y) and snake_case_name stays")
        b = md_blocks(reply)
        assert [x[0] for x in b] == ["h", "p", "li", "li", "li", "li", "li", "quote", "hr", "code", "p"], b
        assert b[0] == ("h", 1, "Plan") and b[4] == ("li", "-", "then save", 1) and b[5][1] == "1." and b[6][1] == "2)", b
        assert b[9] == ("code", "python", "print('hi')"), b[9]
        assert md_spans("I'll do **two** things") == [("I'll do ", ""), ("two", "b"), (" things", "")]
        assert web_link("https://example.org/a?b=1") == "https://example.org/a?b=1" and not any(
            web_link(u) for u in ("file:///C:/x", "ms-settings:privacy", "javascript:alert(1)", "C:\\Windows\\a.exe")), "a click opens web addresses only"
        assert md_spans("see [the docs](https://x.y) and snake_case_name stays") == [("see ", ""), ("the docs", "link"),
                                                                                       (" and snake_case_name stays", "")], "an address stays out"
        assert md_spans("a *b* `c`") == [("a ", ""), ("b", "i"), (" ", ""), ("c", "code")]
        assert md_blocks("```\nnot closed") == [("code", "", "not closed")], "a reply cut short is still code"
        assert md_blocks("line one\nline two\n\nnext") == [("p", "line one\nline two"), ("p", "next")], "a paragraph keeps its breaks"
        assert plain(reply).startswith("Plan\nI'll do two things:\n• open Notepad\n• type hello\n  • then save\n1. first"), plain(reply)
        assert "**" not in plain(reply) and "https://" not in plain(reply)
        now = time.mktime((2026, 10, 15, 17, 30, 0, 0, 0, -1))
        assert when(now - 600, now) == "17:20" and when(None) == "N/A" and when(now - 30 * 86400, now) == "15 Sep"
        assert took({"status": "COMPLETED", "started_at": 100.0, "completed_at": 104.25}) == "4.2 s"
        assert took({"status": "RUNNING", "started_at": 100.0}) == "" and took({"status": "FAILED", "started_at": 0, "completed_at": 125}) == ""
        assert took({"status": "FAILED", "created_at": 10.0, "completed_at": 135.0}) == "2 min 5 s"
        sug = personal_suggestions([{"title": "Call the bank", "done": False}, {"title": "old", "done": True}],
                                   [{"title": "Open Spotify", "status": "FAILED"}, {"title": "x", "status": "COMPLETED"}], [{"name": "Monday Core"}])
        assert sug == ["Do my to-do: Call the bank", "Try again: Open Spotify", "Open Monday Core in VS Code", SUGGESTIONS[0]], sug
        assert personal_suggestions() == list(SUGGESTIONS), "nothing of the owner's yet: the fixed ones"
        assert step_lines([{"label": "Open Notepad", "status": "done"}, {"label": "Type it", "status": "running"}, {"status": "done"}]) == \
            [("✓", "Open Notepad"), ("●", "Type it")], "a step with no words isn't listed"
        assert len(step_lines([{"label": f"s{i}", "status": "done"} for i in range(9)])) == STEPS_SHOWN
        # a coding task's tests (the integration session's TASK_TESTS, 2026-10-17): Core's line, marked; nothing when no code changed
        assert tests_line({}) is None and tests_line({"tests": None}) is None
        assert tests_line({"tests": {"state": "passing", "changed": False, "line": "Tests passed · 3 passed (Monday ran them; no code changed)"}}) is None
        assert tests_line({"tests": {"state": "passing", "changed": True, "line": "Tests passed · 48 passed (Monday ran them after the last change)"}}) == \
            ("✓  Tests passed · 48 passed (Monday ran them after the last change)", "GOOD")
        assert tests_line({"tests": {"state": "failing", "changed": True, "line": "Tests failed · 1 failed"}}) == ("✗  Tests failed · 1 failed", "BAD")
        assert tests_line({"tests": {"state": "stale", "changed": True, "line": "Not tested since b.js changed"}}) == ("!  Not tested since b.js changed", "AMBER")
        assert tests_line({"tests": {"state": "running", "changed": True, "line": "Running the tests: npm test"}})[1] == "AZURE"
        assert tests_line({"tests": {"state": "passing", "changed": True, "line": "Tests passed · 5 passed",
                                     "weakened": "changed what an assertion expects in math.test.js"}}) == \
            ("✓  Tests passed · 5 passed\n!  The agent changed what an assertion expects in math.test.js", "AMBER"), "a pass by a changed test isn't green"
        print("chat self-check: OK")
