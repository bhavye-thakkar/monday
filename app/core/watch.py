"""The owner's own coding agents, watched (2026-10-15, the owner's Dotpals reference: "see what your coding agent actually did", "it
lives in your notch"). Claude Code, Codex and Gemini CLI write every session to a log on this PC (~/.claude/projects/<project>/<session>.jsonl,
~/.codex/sessions/YYYY/MM/DD/rollout-….jsonl, ~/.gemini/tmp/<project>/chats/session-….jsonl). This reads the logs that changed lately,
each from where it left off, and says, per session: what was asked, the plan and where it is, the steps (a command run, a file edited or
read), the last edit as a diff, whether the tests passed and what changed since, and once it ended, how long it took and its own last
words; and, on the owner's click, the day's story. Codex's log carries its plan's limits too (5-hour and week use); the others' don't,
so theirs show N/A. Monday's own runs of these CLIs (in its data folder) aren't the owner's and are left out.

Read only: nothing is written, nothing leaves this PC, and Monday Core keeps the summaries in memory only (AGENT_WATCH is never stored).

    python -m core.watch          the readers on made-up logs (no files)
    python -m core.watch --now    the sessions on this PC now
"""
import base64
import difflib
import glob
import json
import os
import re
import time
import zlib
from datetime import datetime
from pathlib import Path

from . import evidence

HOME = Path(os.path.expanduser("~"))
ACTIVE_S = 90.0          # a log written this recently, with no end since the last request: the agent is at work
RECENT_S = 1800.0        # a session shows this long after its log last changed
TAIL_BYTES = 1_500_000   # a log seen for the first time is read from its last 1.5 MB; after that, only the lines it gained
STEPS_KEPT = 24          # a request's steps kept (reads and searches too; the timeline shows its runs and edits)
STEPS_SHOWN = 8          # of those, the latest the notch's Steps view lists
HELPERS_S = 60.0         # a helper agent that finished stays listed this long (Dotpals')
DIFF_LINES = 30   # an edit's removed and added lines kept (the notch's rows when the agent's log has no hunks of its own)
DIFF_ROWS, DIFF_CHARS = 60, 400  # the rows of the last edit sent to the notch (diff_words), and a row's text at most
STORY_KEPT = 50          # finished requests a summary keeps for the day's story
STORY_SHOWN = 1          # requests the notch shows of it, as Dotpals' panel does (Copy today has them all; 2 made it 1,000 px tall)
PUSH = re.compile(r"\bgit\s+push\b")
APPDATA = Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
KIRO = APPDATA / "Kiro" / "User" / "globalStorage" / "kiro.kiroagent"  # Kiro's own agent: a file per request, in a folder per workspace
KIRO_RUNS = "414d1636299d2b9e4ce7e17fb11f63e9"  # (that folder's name in each workspace's: the same hash in every one here, 2026-10-21)
LOGS = (("Claude Code", str(HOME / ".claude" / "projects" / "*" / "*.jsonl")), ("Codex", str(HOME / ".codex" / "sessions" / "**" / "rollout-*.jsonl")),
        ("Gemini CLI", str(HOME / ".gemini" / "tmp" / "*" / "chats" / "session-*.jsonl")),
        # the owner, 2026-10-21: "the chatgpt and the other ides" too. Kilo Code in any VS Code-like editor (VS Code, Cursor, Kiro,
        # Antigravity: each keeps its extensions' data under its own name), and Kiro's own agent. ponytail: Windows' folders only
        ("Kilo Code", str(APPDATA / "*" / "User" / "globalStorage" / "kilocode.kilo-code" / "tasks" / "*" / "ui_messages.json")),
        ("Kiro", str(KIRO / "*" / KIRO_RUNS / "*")))
MONDAY_DATA = Path(__file__).resolve().parent.parent / "data"   # Monday's own runs of these CLIs (data/cli-chat) aren't the owner's
# Claude Code's hooks ask about a session (core/agent_hooks.py, the owner's OK 2026-10-17): Dotpals' fix loop, commit gate, two-agents guard
FIX_TRIES = 2            # times one request's stop is sent back (the tests failed after its change, or it claims a pass its log doesn't show)
GUARD_S = 600.0          # another session's edit of the same file this recent: the next edit asks the owner first
EDITS_KEPT = 200         # files a session remembers editing (full paths, with when), across its requests
# Dotpals' alerts and brief (its bridge/ui/story.js stuck, brief.js): going in circles, two agents on one file, the day's brief
STUCK_TIMES, STUCK_QUIET_S = 3, 600.0   # a command failing this many times (its last try too); or nothing working this long, still trying
STUCK_FLIPS = 2                         # one file changed back and forth: an edit undoing an earlier one this many times
LIVE_S = 300.0                          # a session with something in its log this recently is still working, not unfinished


def _ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _new(agent):
    return _fresh({"agent": agent, "project": None, "cwd": None, "plan": [], "tests": None, "limits": None, "last": None, "story": [],
                   "name": None,  # the session's name as its app shows it (Claude Code's custom-title record): the notch's title (the owner, 2026-10-20)
                   "context": None,  # context: {"used": tokens of the agent's last call, "window": what fits, when its log says}
                   "entry": None,  # where it runs, as its log says: Claude Code's entrypoint, Codex's originator (agent_window)
                   "kit": {"skills": {}, "tools": {}, "helpers": 0}})  # Dotpals' "Using": skills, MCP tools, helper agents (the session's)


def _fresh(s, asked=None, started=None):
    """A summary's per-request part, empty: what the owner asked, and what the agent did about it since."""
    s.update(asked=asked, started=started, steps=[], diff=None, done_at=None, took=None, said=None,
             files={}, runs=0, read=set(), test_runs=[], test_label=None, pushed=False, sent_back=0,
             flagged=[], cut_at=None, code_after=[], edit_count={}, committed=False)  # Dotpals' flags, why it stopped, readiness, Files
    return s


def _entry(s):
    """A request as the day's story tells it."""
    dirs = {Path(p).parent.name for p in s["read"]}
    return {"asked": s["asked"], "started": s["started"], "said": s["said"], "done_at": s["done_at"], "took": s["took"],
            "files": {k: list(v) for k, v in s["files"].items()}, "runs": s["runs"], "reads": len(s["read"]),
            "read_in": next(iter(dirs)) if len(dirs) == 1 else None, "test_runs": list(s["test_runs"]),
            "test_label": s["test_label"], "pushed": s["pushed"]}


def _changed(old, new):
    """The lines an edit really removed and added (difflib), not the whole text it replaced."""
    a, b = old.splitlines(), new.splitlines()
    minus, plus = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            minus += a[i1:i2]
            plus += b[j1:j2]
    return minus, plus


def _cut(x, n):
    """One line, cut at a word with "…" when longer than n."""
    x = " ".join(str(x).split())
    return x if len(x) <= n else x[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _under(p, root):
    return os.path.normcase(str(p)).startswith(os.path.normcase(str(root)).rstrip("\\/") + os.sep)


def _ours(s, path):
    """A file of the session's project (a relative path is): a scratch file elsewhere isn't code its tests cover."""
    p, root = str(path or ""), s.get("cwd")
    return not root or not os.path.isabs(p) or _under(p, root)


def _mondays(s):
    """A session Monday ran itself, in its own data folder (its chats through the CLIs): not one of the owner's agents."""
    return bool(s.get("cwd")) and _under(s["cwd"], MONDAY_DATA)


def _request(s, text, ts):
    """A new request from the owner: the one before it goes to the day's story, this one starts empty."""
    if s["asked"]:
        s["story"].append(_entry(s))
        del s["story"][:-STORY_KEPT]
    _fresh(s, " ".join(text.split())[:300], ts)


def _step(s, kind, label, sid=None, test=False, push=False, path=None, cmd=None):
    """A step the agent took, from the record being read (its time is s["last"]); _result says how it went and how long it took."""
    s["steps"].append({"kind": kind, "label": " ".join(str(label).split())[:120], "ok": None, "id": sid, "test": test, "push": push,
                       "at": s["last"], "took": None, "cmd": cmd})
    del s["steps"][:-STEPS_KEPT]
    if kind == "RUN":
        s["runs"] += 1
        if cmd:  # Dotpals' "worth a second look", kept for the whole request (its steps are only the last STEPS_KEPT)
            _flag(s, evidence.flags([(cmd, None)]))
    elif kind == "READ" and path:
        s["read"].add(str(path))
        _flag(s, evidence.flags(read=[path]))


def _flag(s, found):
    s["flagged"] += [f for f in found if f not in s["flagged"]]


def _ok(x):
    """How a step went: a test run by what its output said (evidence.verdict), anything else by its exit."""
    return x.get("passed", x["ok"]) if x["test"] else x["ok"]


def _notified(s, text, ts):
    """Claude Code's note that something it started in the background ended (a helper agent, a command): that step's result now."""
    sid, status = re.search(r"<tool-use-id>([^<\s]+)</tool-use-id>", text), re.search(r"<status>(\w+)</status>", text)
    st = next((x for x in s["steps"] if sid and x["id"] == sid.group(1) and x.get("bg")), None)
    if st and status:
        st["bg"] = False
        _result(s, st["id"], status.group(1) == "completed", ts)


def _result(s, sid, ok, ts, out=""):
    for st in reversed(s["steps"]):
        if st["id"] == sid:
            if st.get("bg") and ok:
                return  # started in the background: its call returns at once, its task notification says how it ended (_notified)
            st["ok"] = ok
            st["took"] = round(ts - st["at"], 3) if ts and st["at"] else None
            if st["test"]:  # the tests' own output decides, as Dotpals reads it (core/evidence.py): unclear is None, never a pass
                v = evidence.verdict({"done": True, "ok": ok, "out": evidence.clip(out), "cmd": st.get("cmd") or ""})
                passed = {"passed": True, "failed": False}.get(v["state"])
                s["tests"] = {"ok": passed, "at": ts, "changed_since": [], "evidence": evidence.evidence_text(v)}
                s["test_runs"].append(passed)
                s["test_label"], st["passed"], s["code_after"] = st["label"], passed, []
            s["pushed"] = s["pushed"] or (st["push"] and ok)
            s["committed"] = s["committed"] or bool(ok and st.get("cmd") and any(evidence.GIT_COMMIT.match(x) for x in evidence.parts(st["cmd"])))
            return


def _edited(s, path, minus, plus):
    full = str(path or "")
    full = full if os.path.isabs(full) else os.path.join(s["cwd"], full) if s.get("cwd") and full and full != "?" else None
    if full:  # the two-agents guard: which files this session edited, and when (kept across its requests)
        edits = s.setdefault("edits", {})
        edits.pop(norm := os.path.normcase(os.path.normpath(full)), None)
        edits[norm] = s["last"]
        for k in list(edits)[:-EDITS_KEPT]:
            del edits[k]
    name = Path(path or "?").name
    s["diff"] = {"file": name, "path": str(path or "?"), "minus": minus[:DIFF_LINES], "plus": plus[:DIFF_LINES], "removed": len(minus),
                 "added": len(plus)}
    _flag(s, evidence.flags(changed=[path or ""]))
    step = s["steps"][-1] if s["steps"] else {}
    if step.get("kind") == "EDIT" and step["label"] == name:  # one file's edit
        step["path"] = full or str(path or "")
        if minus or plus:  # what it took out and put in (stuck's flip)
            step["swap"] = (os.path.normcase(str(path)), *(zlib.crc32("\n".join(sorted(x)).encode()) for x in (minus, plus)))
        if evidence.TEST_FILE.search(str(path or "")):  # a test file's lines, for the tests-weakened check (readiness)
            step["patch"] = {"minus": minus[:200], "plus": plus[:200]}
    if not _ours(s, path):
        return  # a scratch file elsewhere: shown as the last edit, not counted as the project's change
    f = s["files"].setdefault(name, [0, 0])
    f[0], f[1] = f[0] + len(plus), f[1] + len(minus)
    s["edit_count"][name] = s["edit_count"].get(name, 0) + 1
    if s["test_runs"] and not evidence.NOT_CODE.search(name) and name not in s["code_after"]:
        s["code_after"].append(name)  # changed after this request's last test run
    if s["tests"] and s["tests"]["ok"] and name not in s["tests"]["changed_since"] and not evidence.NOT_CODE.search(name):
        s["tests"]["changed_since"].append(name)  # the tests passed before this change: not tested since (a doc or an image isn't code)


def _plan_set(s, items, done_states=("completed", "done")):
    s["plan"] = [[str(it.get("content") or it.get("step") or ""), str(it.get("status") or "pending")] for it in items or [] if isinstance(it, dict)]


def _plan_advance(s):
    """The desktop app's update_plan {"advance": true}: the step in progress is done, the next pending one starts."""
    for it in s["plan"]:
        if it[1] == "in_progress":
            it[1] = "completed"
            break
    for it in s["plan"]:
        if it[1] == "pending":
            it[1] = "in_progress"
            break


def _plan_mark(s, done, start):
    """The desktop app's update_plan {"done": [2, 3], "start": 4}: those steps (1-based) done, that one started."""
    for n in [*(done or []), start]:
        if isinstance(n, int) and 0 < n <= len(s["plan"]):
            s["plan"][n - 1][1] = "in_progress" if n == start else "completed"


_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content>", re.S)


def _owner_text(c):
    """What the owner typed, from a user record's content, or None (tool results, injected context, interruptions). A picture sent
    with the words (an image block beside the text) is still their words (2026-10-20: a screenshot made the request invisible, and the
    session read as idle); words after a pasted block are the ask, the paste itself isn't."""
    if isinstance(c, list):
        if not c or not all(isinstance(b, dict) and b.get("type") in ("text", "image") for b in c):
            return None
        t = " ".join(b.get("text") or "" for b in c if b.get("type") == "text")
    else:
        t = c if isinstance(c, str) else ""
    t = t.strip()
    if t.startswith("<pasted_content"):
        rest = _PASTED.sub("", t).strip()
        t = rest or t
    return t if t and not t.startswith(("<", "[Request interrupted", "Caveat:")) else None


def _patched(s, tr):
    """Claude Code's own result of the last edit (a record's toolUseResult): its hunks, with their line numbers and the lines around
    them (structuredPatch), for the notch (the owner, 2026-10-20: "i want to see the real edits too in the notch"); a new file has
    none, so its content is its hunk, from line 1. Another file's result, or one with neither, leaves the edit as it was."""
    d = s.get("diff")
    if not d or Path(str(tr.get("filePath") or "")).name != d["file"]:
        return
    hunks = [{"old": h.get("oldStart"), "new": h.get("newStart"), "lines": [str(x) for x in (h.get("lines") or [])[:DIFF_ROWS * 2]]}
             for h in tr.get("structuredPatch") or [] if isinstance(h, dict)]
    if not hunks and tr.get("type") == "create" and isinstance(tr.get("content"), str):
        hunks = [{"old": 0, "new": 1, "lines": ["+" + x for x in tr["content"].splitlines()[:DIFF_ROWS * 2]]}]
    if hunks:
        d["hunks"], d["path"] = hunks, str(tr["filePath"])


def claude(records, s=None):
    """Claude Code's transcript records (the desktop app's sessions are the same format) -> the session's summary (s, carried on)."""
    s = s or _new("Claude Code")
    for r in records:
        ts, t, msg = _ts(r.get("timestamp")), r.get("type"), r.get("message") or {}
        s["last"] = ts or s["last"]
        if r.get("cwd"):
            s["cwd"], s["project"] = r["cwd"], Path(r["cwd"]).name
        s["entry"] = r.get("entrypoint") or s["entry"]  # "claude-desktop", "cli", "claude-vscode"…
        if t == "custom-title":  # the session's name, as the Claude app's sidebar has it (the last one written wins)
            s["name"] = " ".join(str(r.get("customTitle") or "").split())[:80] or s["name"]
        c = msg.get("content")
        if t == "attachment" and "<task-notification>" in str((r.get("attachment") or {}).get("prompt") or ""):
            _notified(s, r["attachment"]["prompt"], ts)  # a background task's end comes as an attachment
        if t == "user" and not (r.get("isMeta") or r.get("isCompactSummary")):  # Claude Code's own context summary isn't the owner asking
            said = _owner_text(c)
            if said:
                _request(s, said, ts)
            text = c if isinstance(c, str) else " ".join(str(b.get("text") or "") for b in c if isinstance(b, dict)) if isinstance(c, list) else ""
            if "<task-notification>" in text:
                _notified(s, text, ts)
            elif "[Request interrupted" in text and s["asked"] and not s["done_at"]:
                s["cut_at"] = s["cut_at"] or ts  # the owner stopped it before it finished (Dotpals' "cut")
            for b in c if isinstance(c, list) else ():
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out = b.get("content")
                    out = out if isinstance(out, str) else "\n".join(x.get("text") or "" for x in out if isinstance(x, dict)) if isinstance(out, list) else ""
                    _result(s, b.get("tool_use_id"), not b.get("is_error"), ts, out)
            if isinstance(r.get("toolUseResult"), dict) and "structuredPatch" in r["toolUseResult"]:
                _patched(s, r["toolUseResult"])
        elif t == "assistant":
            u = msg.get("usage")
            if isinstance(u, dict):  # what the model read for this call: its context now (Claude Code's log doesn't say how much fits)
                s["context"] = {"used": sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                                "window": None}
            for b in c if isinstance(c, list) else ():
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    s["said"] = " ".join(b["text"].split())[:2000]  # its closing words: the claim check reads them
                if b.get("type") != "tool_use":
                    continue
                n, i = b.get("name") or "", b.get("input") or {}
                if n in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
                    _step(s, "EDIT", Path(i.get("file_path") or i.get("notebook_path") or "?").name, b.get("id"))
                    if n == "Write":
                        _edited(s, i.get("file_path"), [], (i.get("content") or "").splitlines())
                    else:
                        minus, plus = [], []
                        for e in i.get("edits") or [i]:
                            m, p = _changed(e.get("old_string") or "", e.get("new_string") or "")
                            minus, plus = minus + m, plus + p
                        _edited(s, i.get("file_path"), minus, plus)
                elif n in ("Bash", "PowerShell"):
                    cmd = i.get("command") or ""
                    _step(s, "RUN", i.get("description") or cmd.splitlines()[0] if cmd else "a command", b.get("id"), evidence.is_test(cmd),
                          bool(PUSH.search(cmd)), cmd=cmd)
                    s["steps"][-1]["bg"] = bool(i.get("run_in_background"))
                elif n in ("Agent", "Task"):  # a helper agent (Dotpals' helpers): done when its result comes, or its task notification
                    _step(s, "AGENT", i.get("description") or i.get("subagent_type") or "a helper", b.get("id"))
                    s["steps"][-1].update(who=i.get("subagent_type") or "helper", bg=bool(i.get("run_in_background")))
                    s["kit"]["helpers"] += 1
                elif n == "Skill" and i.get("skill"):
                    plugin, _, skill = str(i["skill"]).rpartition(":")
                    _kit(s, "skills", f"{skill} ({plugin})" if plugin else skill)
                elif n == "Read":
                    _step(s, "READ", Path(i.get("file_path") or "?").name, b.get("id"), path=i.get("file_path"))
                elif n in ("Grep", "Glob"):
                    _step(s, "SEARCH", i.get("pattern") or "", b.get("id"))
                elif n == "TodoWrite":
                    _plan_set(s, i.get("todos"))
                elif n.endswith("update_plan"):
                    if i.get("items"):
                        _plan_set(s, i["items"])
                    elif i.get("advance"):
                        _plan_advance(s)
                    else:
                        _plan_mark(s, i.get("done"), i.get("start"))
                elif n.startswith("mcp__"):  # Dotpals' "Using": the MCP tools it called (the app's own plan list above isn't one)
                    _kit(s, "tools", _server(n))
            if msg.get("stop_reason") == "end_turn" and s["started"]:
                s["done_at"] = ts
                s["took"] = round(ts - s["started"], 1) if ts and s["started"] else None
            elif msg.get("stop_reason") == "tool_use" and s["done_at"]:  # at work again after its turn ended (a Stop hook sent it back,
                s["done_at"] = s["took"] = None  # the owner's words woke it, a cut reply went on): 2026-10-22, it read "done" and took none
    return s


def _kit(s, kind, name):
    k = s["kit"][kind]
    k[name] = k.get(name, 0) + 1


def _server(tool):
    """An MCP tool's server, as Dotpals names it: "mcp__plugin_engineering_github__create_issue" is github (engineering); a claude.ai
    connector's server is an id, so it is a connector."""
    server = tool.split("__")[1] if tool.count("__") >= 2 else tool[5:]
    m = re.match(r"^plugin[ _]([\w-]+?)[ _]([\w-]+)$", server)
    return f"{m.group(2)} ({m.group(1)})" if m else "connector" if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", server) else server


BUILD = re.compile(r"\b(build|tsc|make|compile|cargo\s+check)\b", re.I)
_CMD = re.compile(r"""\bcmd\s*:\s*(["'`])((?:\\.|(?!\1).)*)\1""", re.S)
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def _codex_call(s, code, sid):
    code = code if isinstance(code, str) else json.dumps(code)
    if "*** Begin Patch" in code:
        files = _PATCH_FILE.findall(code)
        _step(s, "EDIT", ", ".join(Path(f).name for f in files) or "a patch", sid)
        lines = code.splitlines()
        _edited(s, files[-1] if files else "?", [ln[1:] for ln in lines if ln.startswith("-") and not ln.startswith("---")],
                [ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++")])
        return
    m = _CMD.search(code)
    cmd = (m.group(2).encode().decode("unicode_escape", "ignore") if m else code).strip()
    _step(s, "RUN", evidence.shown(cmd.splitlines()[0]) if cmd else "a command", sid, evidence.is_test(cmd), bool(PUSH.search(cmd)), cmd=cmd)


def codex(records, s=None):
    """Codex's rollout records -> the session's summary (its rate limits included; s, carried on)."""
    s = s or _new("Codex")
    for r in records:
        ts, p = _ts(r.get("timestamp")), r.get("payload") or {}
        s["last"] = ts or s["last"]
        ty = p.get("type")
        if r.get("type") == "session_meta" and p.get("cwd"):
            s["cwd"], s["project"], s["entry"] = p["cwd"], Path(p["cwd"]).name, p.get("originator") or s["entry"]  # "Codex Desktop", …
        elif ty == "message" and p.get("role") == "user":
            said = _owner_text([{"type": "text", "text": c.get("text") or ""} for c in p.get("content") or [] if c.get("type") == "input_text"])
            if said:
                _request(s, said, ts)
        elif ty == "message" and p.get("role") == "assistant":
            text = " ".join(c.get("text") or "" for c in p.get("content") or [] if c.get("type") == "output_text").strip()
            if text:
                s["said"] = " ".join(text.split())[:600]
        elif ty in ("custom_tool_call", "function_call") and p.get("name") == "update_plan":
            try:
                _plan_set(s, json.loads(p.get("arguments") or p.get("input") or "{}").get("plan"))
            except (ValueError, AttributeError):
                pass
        elif ty in ("custom_tool_call", "function_call"):
            _codex_call(s, p.get("input") or p.get("arguments") or "", p.get("call_id"))
        elif ty in ("custom_tool_call_output", "function_call_output"):
            out = p.get("output")
            text = " ".join(o.get("text") or "" for o in out if isinstance(o, dict)) if isinstance(out, list) else str(out or "")
            _result(s, p.get("call_id"), not re.search(r"(?i)exit code:?\s*[1-9]|script failed|\berror:", text), ts, text)
        elif ty == "token_count":
            rl, lim = p.get("rate_limits") or {}, {}
            for w in (rl.get("primary"), rl.get("secondary")):
                if isinstance(w, dict) and w.get("used_percent") is not None:
                    lim[window_name(w.get("window_minutes") or 0)] = round(float(w["used_percent"]))
            s["limits"] = lim or s["limits"]
            info = p.get("info") or {}
            if isinstance(info.get("last_token_usage"), dict):  # Codex says how much its last call read, and how much fits
                s["context"] = {"used": info["last_token_usage"].get("total_tokens") or 0, "window": info.get("model_context_window")}
        elif ty == "task_complete":
            s["done_at"] = ts
            s["took"] = round((p.get("duration_ms") or 0) / 1000, 1) or (round(ts - s["started"], 1) if ts and s["started"] else None)
            if p.get("last_agent_message"):
                s["said"] = " ".join(str(p["last_agent_message"]).split())[:600]
    return s


def window_name(mins):
    """A rate limit's window in the owner's words: "5-hour", "week", "month" (Codex's Go plan writes 43,200 minutes, seen 2026-10-26: it
    read as "week" before)."""
    return "month" if mins >= 40000 else "week" if mins >= 10000 else f"{round(mins / 60)}-hour"


LIMITS_TAIL = 400_000   # bytes of a Codex log read from its end for the limits it last wrote


def plan_use(logs=None):
    """The owner's plan use as their agents' own logs last said it (the 2026-10-26 recording: "Your plan, always in sight"). Codex writes
    its limits into its session log (token_count's rate_limits: used %, the window, when it resets, the plan); Claude Code's log doesn't,
    so Claude isn't here (the notch says N/A). -> [{"agent", "plan", "windows": [{"name", "used", "resets_at"}], "at"}]; [] when no log
    has them. Reads the newest few logs' ends only."""
    try:
        files = sorted(glob.glob(logs or LOGS[1][1], recursive=True), key=os.path.getmtime, reverse=True)[:3]
    except OSError:
        return []
    for f in files:
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - LIMITS_TAIL))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in reversed(tail.splitlines()):
            if '"rate_limits"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rl = (rec.get("payload") or {}).get("rate_limits") or {}
            wins = [{"name": window_name(w.get("window_minutes") or 0), "used": round(float(w["used_percent"])), "resets_at": w.get("resets_at")}
                    for w in (rl.get("primary"), rl.get("secondary")) if isinstance(w, dict) and w.get("used_percent") is not None]
            if wins:
                return [{"agent": "Codex", "plan": str(rl.get("plan_type") or "").capitalize() or None, "windows": wins,
                         "at": _ts(rec.get("timestamp")) or os.path.getmtime(f)}]
    return []


def _gemini_call(s, tc, ts):
    """One of Gemini CLI's tool calls (its result is in the same record)."""
    n, a, sid = tc.get("name") or "", tc.get("args") or {}, tc.get("id")
    path = a.get("file_path") or a.get("absolute_path") or a.get("path")
    if n in ("replace", "edit"):
        _step(s, "EDIT", Path(path or "?").name, sid)
        _edited(s, path, *_changed(a.get("old_string") or "", a.get("new_string") or ""))
    elif n == "write_file":
        _step(s, "EDIT", Path(path or "?").name, sid)
        _edited(s, path, [], (a.get("content") or "").splitlines())
    elif n == "run_shell_command":
        cmd = a.get("command") or ""
        _step(s, "RUN", a.get("description") or cmd.splitlines()[0] if cmd else "a command", sid, evidence.is_test(cmd), bool(PUSH.search(cmd)), cmd=cmd)
    elif n in ("read_file", "read_many_files"):
        _step(s, "READ", Path(path or "?").name, sid, path=path)
    elif n in ("glob", "search_file_content", "grep", "list_directory"):
        _step(s, "SEARCH", a.get("pattern") or path or "", sid)
    elif n == "write_todos":
        _plan_set(s, [{"content": t.get("description"), "status": t.get("status")} for t in a.get("todos") or [] if isinstance(t, dict)])
        return
    else:
        return
    if tc.get("status") in ("success", "error", "cancelled"):
        _result(s, sid, tc["status"] == "success", _ts(tc.get("timestamp")) or ts, tc.get("resultDisplay") if isinstance(tc.get("resultDisplay"), str) else "")


def gemini(records, s=None):
    """Gemini CLI's chat records -> the session's summary. Each "$set" line carries the whole message list, so the summary is built
    again from the newest one (where it ran, set from its folder by _place, carries on)."""
    msgs = next((r["$set"]["messages"] for r in reversed(records) if isinstance(r.get("$set"), dict)
                 and isinstance(r["$set"].get("messages"), list)), None)
    if msgs is None:
        return s or _new("Gemini CLI")
    old, s = s or {}, _new("Gemini CLI")
    s.update(project=old.get("project"), cwd=old.get("cwd"))
    for m in msgs:
        if not isinstance(m, dict):
            continue
        ts, c = _ts(m.get("timestamp")), m.get("content")
        s["last"] = ts or s["last"]
        text = c if isinstance(c, str) else " ".join(p.get("text") or "" for p in c if isinstance(p, dict)) if isinstance(c, list) else ""
        if m.get("type") == "user":
            said = _owner_text(text)
            if said:
                _request(s, said, ts)
        elif m.get("type") == "gemini":
            if text.strip():
                s["said"] = " ".join(text.split())[:600]
            tk = m.get("tokens")
            if isinstance(tk, dict) and (tk.get("input") or tk.get("total")):  # (its log doesn't say how much fits either)
                s["context"] = {"used": tk.get("input") or tk.get("total"), "window": None}
            for tc in m.get("toolCalls") or []:
                if isinstance(tc, dict):
                    _gemini_call(s, tc, ts)
            # its turn ends with an answer that calls no tool; one that calls tools goes on
            s["done_at"] = None if m.get("toolCalls") or not text.strip() or not s["started"] else ts
            s["took"] = round(ts - s["started"], 1) if s["done_at"] and ts else None
    return s


KILO_EDITS = ("appliedDiff", "editedExistingFile", "newFileCreated", "insertContent", "searchAndReplace")
KILO_SEARCHES = ("searchFiles", "listFilesTopLevel", "listFilesRecursive", "codebaseSearch", "listCodeDefinitionNames")
_SEARCH_REPLACE = re.compile(r"<<<<<<< SEARCH\n(?::start_line:\s*\d+\n)?(?:-+\n)?(.*?)\n?=======\n(.*?)\n?>>>>>>> REPLACE", re.S)
KIRO_EDITS = ("replace", "create", "write", "fsWrite", "fsAppend", "strReplace")
EDITORS = {"code": "vscode", "cursor": "cursor", "kiro": "kiro", "antigravity": "antigravity", "windsurf": "windsurf"}  # its folder -> APPS


def _hunks_of(old, new, around=3):
    """An edit's real hunks from the whole file before and after it (as Claude Code's structuredPatch gives them): each with its first
    line's numbers and its " " / "-" / "+" lines."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(around):
        lines = []
        for op, i1, i2, j1, j2 in group:
            lines += [" " + x for x in a[i1:i2]] if op == "equal" else ["-" + x for x in a[i1:i2]] + ["+" + x for x in b[j1:j2]]
        out.append({"old": group[0][1] + 1, "new": group[0][3] + 1, "lines": lines[:DIFF_ROWS * 2]})
    return out


def kilo(records, s=None):
    """Kilo Code's task (the extension's ui_messages.json, in VS Code or another editor; rewritten whole as it works) -> the session's
    summary, built again from the newest list (where it runs carries on: _place reads it once). Its first words are the task, "user_
    feedback" the owner's next requests; its tools (reads, searches, edits as SEARCH/REPLACE blocks or a new file's lines), its commands,
    and its closing "completion_result". A step is taken as done when the agent goes on past it, failed on its "diff_error"."""
    msgs = next((r for r in reversed(records) if isinstance(r, list)), None)
    if msgs is None:
        return s or _new("Kilo Code")
    old, s = s or {}, _new("Kilo Code")
    s.update(project=old.get("project"), cwd=old.get("cwd"), entry=old.get("entry"))
    for m in (m for m in msgs if isinstance(m, dict)):
        ts, kind, text, ask = (m.get("ts") or 0) / 1000 or None, m.get("say") or m.get("ask"), str(m.get("text") or ""), m.get("type") == "ask"
        s["last"] = ts or s["last"]
        if not ask and (kind == "user_feedback" or (kind in ("text", "task") and not s["asked"])):
            if text.strip():
                _request(s, text, ts)
        elif ask and kind == "tool":
            try:
                t = json.loads(text)
            except ValueError:
                continue
            name, path = t.get("tool"), str(t.get("path") or "")
            if name in KILO_EDITS:
                _step(s, "EDIT", Path(path or "?").name, ts)
                minus, plus, diff = [], [], str(t.get("diff") or "")
                for a, b in _SEARCH_REPLACE.findall(diff):
                    mm, pp = _changed(a, b)
                    minus, plus = minus + mm, plus + pp
                if name == "newFileCreated":
                    plus = str(t.get("content") or "").splitlines()
                elif not minus and not plus:  # (a unified diff)
                    minus = [x[1:] for x in diff.splitlines() if x.startswith("-") and not x.startswith("---")]
                    plus = [x[1:] for x in diff.splitlines() if x.startswith("+") and not x.startswith("+++")]
                _edited(s, path, minus, plus)
            elif name == "readFile":
                _step(s, "READ", Path(path or "?").name, ts, path=path)
            elif name in KILO_SEARCHES:
                _step(s, "SEARCH", t.get("regex") or t.get("query") or path or "", ts)
        elif ask and kind == "command" and text.strip():
            _step(s, "RUN", evidence.shown(text.splitlines()[0]), ts, evidence.is_test(text), bool(PUSH.search(text)), cmd=text)
        elif kind == "diff_error" and s["steps"]:
            s["steps"][-1]["ok"] = False
        elif not ask and kind == "completion_result":
            s["said"], s["done_at"] = " ".join(text.split())[:600] or s["said"], ts
            s["took"] = round(ts - s["started"], 1) if ts and s["started"] else None
        elif not ask and kind == "text" and text.strip():
            s["said"] = " ".join(text.split())[:600]
        for st in s["steps"][:-1]:  # (gone past it: done, unless it said it failed)
            st["ok"] = True if st["ok"] is None else st["ok"]
    if s["done_at"] and s["steps"] and s["steps"][-1]["ok"] is None:
        s["steps"][-1]["ok"] = True
    return s


def kiro(records, s=None):
    """Kiro's own agent, one request (its execution file, rewritten whole as it works) -> the session's summary: the owner's prompt,
    its actions (reads, searches, commands, edits: the whole file before and after, so the last edit has its real hunks), its words,
    and how it ended (succeed; failed or aborted: stopped before it finished)."""
    d = next((r for r in reversed(records) if isinstance(r, dict) and isinstance(r.get("actions"), list)), None)
    if d is None:
        return s or _new("Kiro")
    old, s = s or {}, _new("Kiro")
    s.update(project=old.get("project"), cwd=old.get("cwd"), entry="kiro", chat=d.get("chatSessionId"))
    t0 = (d.get("startTime") or 0) / 1000 or None
    s["last"] = t0
    asked = ((d.get("input") or {}).get("data") or {}).get("userPrompt") if isinstance((d.get("input") or {}).get("data"), dict) else None
    asked = re.split(r"\s*<(?:EnvironmentContext|environment_details)>", str(asked or ""))[0]  # (the context Kiro adds isn't the owner's)
    if asked:
        _request(s, asked, t0)
    last_edit = None
    for a in (a for a in d["actions"] if isinstance(a, dict)):
        ts = (a.get("emittedAt") or a.get("endTime") or 0) / 1000 or None
        s["last"] = ts or s["last"]
        kind, inp, sid = a.get("actionType"), a.get("input") if isinstance(a.get("input"), dict) else {}, a.get("actionId")
        if kind in KIRO_EDITS:
            path = str(inp.get("file") or inp.get("path") or "?")
            _step(s, "EDIT", Path(path).name, sid)
            _edited(s, path, *_changed(str(inp.get("original") or ""), str(inp.get("modified") or inp.get("content") or "")))
            last_edit = inp
        elif kind in ("readFiles", "readFile"):
            files = [f for f in inp.get("files") or [inp] if isinstance(f, dict) and f.get("path")]
            if not files:
                continue
            _step(s, "READ", ", ".join(Path(str(f["path"])).name for f in files), sid, path=files[0]["path"])
        elif kind == "search":
            _step(s, "SEARCH", inp.get("query") or inp.get("pattern") or inp.get("regex") or "", sid)
        elif kind == "runCommand":
            cmd = str(inp.get("command") or "")
            _step(s, "RUN", evidence.shown(cmd.splitlines()[0]) if cmd else "a command", sid, evidence.is_test(cmd), bool(PUSH.search(cmd)), cmd=cmd)
        elif kind == "say" and isinstance(a.get("output"), dict) and a["output"].get("message"):
            s["said"] = " ".join(str(a["output"]["message"]).split())[:600]
            continue
        elif kind == "displayError" and s["steps"]:
            s["steps"][-1]["ok"] = False
            continue
        else:
            continue
        if a.get("actionState") in ("Success", "Accepted", "Error"):
            _result(s, sid, a["actionState"] != "Error", ts)
    if last_edit and isinstance(last_edit.get("originalContent"), str) and isinstance(last_edit.get("modifiedContent"), str) and s.get("diff"):
        s["diff"]["hunks"] = _hunks_of(last_edit["originalContent"], last_edit["modifiedContent"])
    if d.get("status") in ("succeed", "failed", "aborted") and d.get("endTime"):
        s["done_at"] = d["endTime"] / 1000
        s["took"] = round(s["done_at"] - t0, 1) if t0 else None
        if d["status"] != "succeed":
            s["cut_at"] = s["done_at"]
    return s


def _kilo_place(path, s):
    """A Kilo Code task's folder (its first request says "Current Workspace Directory (…)") and its editor (VS Code, Cursor, Kiro…:
    the folder its data is under), read once."""
    task = Path(path).parent
    try:
        with open(task / "api_conversation_history.json", "rb") as f:
            m = re.search(rb"Current Workspace Directory \(([^)]+)\)", f.read(400_000))
        s["cwd"] = os.path.normpath(m.group(1).decode("utf-8", "replace")) if m else None
    except OSError:
        s["cwd"] = None
    s["entry"] = EDITORS.get(task.parents[4].name.lower()) if len(task.parents) > 4 else None  # (<editor>\User\globalStorage\…\tasks\<id>)


KILO_TOOLS = {"apply_diff": "EDIT", "write_to_file": "EDIT", "insert_content": "EDIT", "search_and_replace": "EDIT", "edit_file": "EDIT",
              "delete_file": "EDIT", "read_file": "READ", "search_files": "SEARCH", "list_files": "SEARCH", "codebase_search": "SEARCH",
              "list_code_definition_names": "SEARCH", "execute_command": "RUN"}


def _kilo_history(task, s):
    """The current request's steps from Kilo Code's API history, when its message list shows none: a step it runs without asking is
    only there (each tool call with its time; a result marked is_error failed). Its edits' SEARCH / REPLACE lines too."""
    try:
        h = json.loads((task / "api_conversation_history.json").read_bytes())
    except (OSError, ValueError):
        return
    since = (s.get("started") or 0) * 1000
    bad = {b.get("tool_use_id") for m in h if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), list)
           for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")}
    for m in (m for m in h if isinstance(m, dict) and m.get("role") == "assistant" and (m.get("ts") or 0) >= since):
        for b in (b for b in m.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in KILO_TOOLS):
            i, kind, sid = b.get("input") if isinstance(b.get("input"), dict) else {}, KILO_TOOLS[b["name"]], b.get("id")
            path, cmd = str(i.get("path") or ""), str(i.get("command") or "")
            if kind == "RUN":
                _step(s, "RUN", evidence.shown(cmd.splitlines()[0]) if cmd else "a command", sid, evidence.is_test(cmd), bool(PUSH.search(cmd)), cmd=cmd)
            elif kind == "READ":
                _step(s, "READ", Path(path or "?").name, sid, path=path)
            elif kind == "SEARCH":
                _step(s, "SEARCH", i.get("regex") or i.get("query") or path, sid)
            else:
                _step(s, "EDIT", Path(path or "?").name, sid)
                pairs = [_changed(a, z) for a, z in _SEARCH_REPLACE.findall(str(i.get("diff") or ""))]
                plus = str(i.get("content") or "").splitlines() if b["name"] == "write_to_file" else [x for _m, p in pairs for x in p]
                _edited(s, path, [x for mm, _p in pairs for x in mm], plus)
            s["steps"][-1]["ok"] = sid not in bad


def _kiro_place(s):
    """A Kiro request's folder: its chat's file sits in a folder named for the workspace (base64, "=" written "_")."""
    for f in glob.glob(str(KIRO / "workspace-sessions" / "*" / f"{s.get('chat') or '-'}.json")):
        try:
            s["cwd"] = base64.b64decode(Path(f).parent.name.replace("_", "=").replace("-", "/")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
        return


def _place(agent, path, s):
    """Where a session ran when its log doesn't say: a Gemini CLI session's folder (~/.gemini/tmp/<project>/, with .project_root), a Kilo
    Code task's (its first request) and its editor, a Kiro request's (its chat's workspace)."""
    if agent == "Kilo Code" and not s.get("cwd"):
        _kilo_place(path, s)
    if agent == "Kilo Code" and s.get("asked") and not s["steps"]:
        _kilo_history(Path(path).parent, s)
    if agent == "Kiro" and not s.get("cwd"):
        _kiro_place(s)
    if agent in ("Kilo Code", "Kiro"):
        s["project"] = Path(s["cwd"]).name if s.get("cwd") else s.get("project")
    if agent == "Gemini CLI" and not s.get("cwd"):
        d = Path(path).parent.parent
        try:
            s["cwd"] = (d / ".project_root").read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        s["project"] = Path(s["cwd"]).name if s.get("cwd") else d.name
    return s


READERS = {"Claude Code": claude, "Codex": codex, "Gemini CLI": gemini, "Kilo Code": kilo, "Kiro": kiro}


def read_new(path, start=None, n=TAIL_BYTES):
    """A JSON-lines log's whole lines from byte start on (None: its last n bytes, the first line, cut, skipped) -> (records, where the
    next read starts). A line still being written waits for that next read. A JSON file its agent rewrites whole as it works (Kilo Code's
    task, Kiro's request): all of it, as one record ([] when caught mid-write: read again when it next changes)."""
    if not str(path).endswith(".jsonl"):
        with open(path, "rb") as f:
            data = f.read()
        try:
            return [json.loads(data)], len(data)
        except ValueError:
            return [], 0
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        cut = start is None and size > n
        start = max(0, size - n) if start is None else start
        f.seek(start)
        data = f.read(size - start)
    end = data.rfind(b"\n") + 1
    out = []
    for ln in data[:end].split(b"\n")[1 if cut else 0:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out, start + end


def state(s, mtime, now=None):
    """working / done / idle for a summary, from its own end and how recently its log changed."""
    now = now or time.time()
    if not s["done_at"] and not s.get("cut_at") and now - mtime <= ACTIVE_S and (s["asked"] or s["steps"]):  # a long request can be older than the first read
        return "working"
    return "done" if s["done_at"] else "idle"


def plan_line(s):
    """ "2/4 · Fixing the rounding in billing.ts" from the plan, or None."""
    if not s["plan"]:
        return None
    done = sum(st in ("completed", "done") for _, st in s["plan"])
    now = next((c for c, st in s["plan"] if st == "in_progress"), None) or next((c for c, st in s["plan"] if st == "pending"), None)
    return f"{min(done + 1, len(s['plan']))}/{len(s['plan'])} · {now}" if now else f"{done}/{len(s['plan'])} done"


def summary(path, cache, agent="Claude Code"):
    """One log's summary, read on from where the cache left it (scan reads them all this way; a hook asks about one session)."""
    st = os.stat(path)
    hit = cache.get(path)
    if not hit or hit[0] != st.st_mtime or hit[1] != st.st_size:
        go_on = hit and st.st_size >= hit[2]  # a log cut short (rewritten) is read afresh
        recs, end = read_new(path, hit[2] if go_on else None)
        cache[path] = hit = (st.st_mtime, st.st_size, end, _place(agent, path, READERS[agent](recs, hit[3] if go_on else None)))
    return hit[3]


def _code(s):
    return [n for n in s["files"] if not evidence.NOT_CODE.search(n)]


def flags_of(s):
    """Dotpals' "worth a second look" for the request: what was flagged as it happened, and the same command failing 3 times."""
    out = list(s["flagged"])
    for f in evidence.flags([(x["cmd"], _ok(x)) for x in s["steps"] if x["kind"] == "RUN" and x.get("cmd") and x["ok"] is not None]):
        if all(f["text"] != g["text"] for g in out):
            out.append(f)
    return out


def helpers(s, now=None):
    """Dotpals' helpers: the helper agents this request started that still work, or finished in the last HELPERS_S."""
    now = now or time.time()
    return [x for x in s["steps"] if x["kind"] == "AGENT" and (x["ok"] is None or now - (x["at"] or 0) - (x["took"] or 0) <= HELPERS_S)]


def helpers_line(s, now=None):
    """"Helpers · 1 working: Explore: find the VAT code ✓, general-purpose: review the fix", or ""."""
    hs = helpers(s, now)
    busy = sum(x["ok"] is None for x in hs)
    return f"Helpers · {f'{busy} working' if busy else 'all done'}: " + ", ".join(
        f"{x.get('who') or 'helper'}: {_cut(x['label'], 40)}" + ("" if x["ok"] is None else " ✓" if x["ok"] else " ✗") for x in hs[-3:]) if hs else ""


def toolkit_line(s):
    """Dotpals' "Using" row: the skills, MCP tools and helper agents the session used, the most used first; "" when none."""
    k = s.get("kit") or {}
    top = lambda d: ", ".join(n for n, _ in sorted(d.items(), key=lambda kv: -kv[1])[:5])  # noqa: E731
    bits = ([f"skills {top(k['skills'])}"] if k.get("skills") else []) + ([f"tools {top(k['tools'])}"] if k.get("tools") else []) + \
        ([_n(k["helpers"], "helper agent")] if k.get("helpers") else [])
    return "Using · " + " · ".join(bits) if bits else ""


def _left_failing(s, once=False):
    """What the request left failing: a command whose last tries all failed (2 or more; with once, a test or build that failed once)."""
    runs = {}
    for x in s["steps"]:
        if x["kind"] == "RUN" and x.get("cmd") and x["ok"] is not None:
            runs.setdefault(" ".join(x["cmd"].split())[:200], []).append(_ok(x))
    for cmd, oks in reversed(list(runs.items())):
        n = next((i for i, ok in enumerate(reversed(oks)) if ok is not False), len(oks))  # its last tries that failed, in a row
        if n >= 2:
            return f"`{_cut(cmd, 60)}` still fails after {n} tries"
        if n == 1 and once and (evidence.is_test(cmd) or BUILD.search(cmd)):
            return f"`{_cut(cmd, 60)}` failed and wasn't fixed"
    return None


def why_stopped(s):
    """Why the request ended, in plain words, as Dotpals reads it from what happened (agents don't say), or None while it goes on."""
    if s.get("cut_at"):
        return "Stopped before it finished (interrupted)"
    if not s.get("done_at"):
        return None
    gave_up = _left_failing(s)
    if gave_up:
        return f"Stopped trying: {gave_up.replace(' still fails ', ' still failed ')}"
    if s["test_runs"] and s["test_runs"][-1] is False:
        return "Said it was done, but the tests are failing"
    if any(re.search(r"\?\s*\**\s*$", x) for x in re.split(r"(?<=[.!?])\s+", (s.get("said") or "").strip())[-2:]):
        return "Finished with a question for you"
    return "Finished: the agent said it was done"


def claims_of(s):
    """Dotpals' claim check of the request's closing words against its log: files, tests, a commit, a push (core/evidence.py)."""
    if not s.get("done_at") or not s.get("said"):
        return []
    changed = list(s["files"]) + list((s.get("edits") or {})) + [p for x in s["steps"] if x["kind"] == "RUN" and x.get("cmd")
                                                                 for p in evidence.command_edits({"kind": "run", "done": True, "ok": x["ok"], "cmd": x["cmd"]})]
    ran = [("git commit", True)] * s["committed"] + [("git push", True)] * bool(s["pushed"])  # kept for the whole request, unlike its steps
    tests = {"ran": bool(s["test_runs"]), "ok": s["test_runs"][-1] if s["test_runs"] else None, "since": s["code_after"]}
    return evidence.claim_check(s["said"], changed, ran, tests)


def _ev_steps(s):
    """The request's kept runs and edits as core/evidence.py reads steps (its tests-weakened rule)."""
    out = []
    for x in s["steps"]:
        if x["kind"] == "RUN" and x.get("cmd") and x["ok"] is not None:
            out.append({"kind": "run", "at": x["at"] or 0, "cmd": x["cmd"], "done": True, "ok": _ok(x), "out": ""})
        elif x["kind"] == "EDIT" and x.get("path") and x["ok"] is not False:
            out.append({"kind": "edit", "at": x["at"] or 0, "files": [x["path"]], "patch": x.get("patch") or {"minus": [], "plus": []}, "ok": x["ok"]})
    return out


def _commit_tested(s):
    """Whether the request's last commit came after a passing test run, with no code changed between; None when it made no commit."""
    steps = [x for x in s["steps"] if x["ok"] is not None]
    commits = [x for x in steps if x["kind"] == "RUN" and x["ok"] and x.get("cmd") and any(evidence.GIT_COMMIT.match(p) for p in evidence.parts(x["cmd"]))]
    if not commits:
        return None
    at = commits[-1]["at"] or 0
    runs = [x for x in steps if x["kind"] == "RUN" and x["test"] and (x["at"] or 0) < at]
    return bool(runs) and _ok(runs[-1]) is True and not any(
        x["kind"] == "EDIT" and (runs[-1]["at"] or 0) < (x["at"] or 0) < at and not evidence.NOT_CODE.search(x["label"]) for x in steps)


def readiness(s):
    """Dotpals' "Ready to merge?" for a finished request that changed code: the checks a reviewer would make. {"ready", "checks": [[ok,
    text]], "problems"} or None (still working, or no code changed). Only the tests the agent ran count: Monday can't see the owner's, or CI."""
    if not s.get("done_at") or not _code(s):
        return None
    ran = bool(s["test_runs"])
    checks = [[ran, "Tests ran" if ran else "No tests ran"]]
    if ran:
        since, last = s["code_after"], s["test_runs"][-1]
        checks += [[not since, f"Changed {', '.join(since[:3])}{f' +{len(since) - 3}' if len(since) > 3 else ''} after the last test run" if since
                    else "Tests ran after the last change"],
                   [last is True, "Tests passed" if last else "Tests are failing" if last is False else "Test result unclear"]]
        weak = last and evidence.weakened(_ev_steps(s), s.get("cwd"))
        if weak:  # green because the tests changed, not the code
            checks.append([False, f"Changed the tests to make them pass: {weak['text']}"])
    left = _left_failing(s, once=True)
    checks.append([not left, left or "No failures left behind"])
    risky = [f["text"] for f in flags_of(s) if f["level"] == "warn"]
    checks.append([not risky, risky[0] if risky else "Nothing risky"])
    tested = _commit_tested(s)
    if tested is not None:
        checks.append([tested, "The commit was tested" if tested else "Committed without a passing test run after the last change"])
    checks += [[False, evidence.claim_line(c)] for c in claims_of(s) if c["rule"] != "tests-pass"]  # the test checks above say why
    problems = [text for ok, text in checks if not ok]
    return {"ready": not problems, "checks": checks, "problems": problems}


def readiness_line(r):
    """"✓ Ready to merge: …" or "! Not ready to merge: Tests are failing · Changed .env, which usually holds secrets", for the card."""
    if not r:
        return ""
    if r["ready"]:
        return "✓ Ready to merge: tests ran after the last change and passed, nothing risky"
    more = len(r["problems"]) - 3
    return "! Not ready to merge: " + " · ".join(r["problems"][:3]) + (f" · and {more} more" if more > 0 else "")


def check_words(s):
    """The Steps' Check: Ready to merge? as a checklist (else why it stopped and what it said it did that it didn't), then what's worth a
    second look. [] while there's nothing to say."""
    r = readiness(s)
    if r:
        out = ["Ready to merge" if r["ready"] else "Not ready to merge"] + [("✓ " if ok else "! ") + text for ok, text in r["checks"]]
    else:
        out = [x for x in [why_stopped(s)] if x] + ["! " + evidence.claim_line(c) for c in claims_of(s)]
    return out + [("! " if f["level"] == "warn" else "· ") + f["text"] for f in flags_of(s) if "! " + f["text"] not in out]


def stop_reason(s, tries):
    """Claude Code wants to stop (its Stop hook): what Monday sends it back to do, or None to let it stop. Dotpals' fix loop and claim
    check on the session's own log: tests that failed after its change go back to it (once more only while it says they pass), and a
    pass it claims that its log doesn't show goes back to be run; at most FIX_TRIES times a request, and never a request that changed no code."""
    if tries >= FIX_TRIES or not _code(s):
        return None
    t, claim = s["tests"], evidence.claims_pass(s.get("said") or "")
    if t and t["ok"] is False:
        if tries and not claim:
            return None  # sent back once, and now it says how it stands: the notch shows the failing tests to the owner
        return (f"Monday's check: the tests failed after your change ({t['evidence']}). Fix the code, not the tests, and run them again. "
                "If they were failing before your change too, say so plainly instead.")
    if claim and (not t or t["ok"] is None or t["changed_since"]):
        why = (f"{', '.join(t['changed_since'][:4])} changed after they last passed" if t and t["changed_since"] else
               "their output didn't show a pass" if t else "no test ran for this request")
        return f"Monday's check: you said “{claim[:120]}”, but {why}. Run the tests and say what they show."
    return None


def gate_reason(s, cmd):
    """`git commit` / `git push` (its PreToolUse hook), Dotpals' gate: ("deny", why) while the tests fail after this request's change,
    ("ask", why) when no passing run came after it, else None. A request that changed no code isn't gated."""
    code = _code(s)
    if not code:
        return None
    what, t = ("push" if PUSH.search(cmd or "") else "commit"), s["tests"]
    if t and t["ok"] is False:
        return "deny", f"Monday's gate: the tests failed after this change ({t['evidence']}). Fix them before you {what}, or ask the owner to {what} anyway."
    if not t or t["ok"] is None or t["changed_since"]:
        changed = t["changed_since"] if t and t["changed_since"] else code
        return "ask", f"Monday's gate: {', '.join(changed[:4])} changed and no passing test run came after. {what.capitalize()} anyway?"
    return None


def guard_reason(sessions, me, path, now):
    """The two-agents guard (its PreToolUse hook on an edit): another of the owner's sessions edited this file in the last GUARD_S. Why to
    ask first, or None. sessions: {its log: its summary}; me: this session's log."""
    p = os.path.normcase(os.path.normpath(str(path or "")))
    if not os.path.isabs(p):
        return None
    for key, s in sessions.items():
        at = (s.get("edits") or {}).get(p)
        if at and key != me and now - at <= GUARD_S and not _mondays(s):
            mins = max(1, round((now - at) / 60))
            return (f"Monday's guard: {s['agent']}{' in ' + s['project'] if s.get('project') else ''} edited {Path(path).name} {mins} min "
                    "ago in another session. Edit it here too?")
    return None


def stuck(s, now):
    """Dotpals' "going in circles", while a request is still going: the same command failed STUCK_TIMES times and its last try failed too,
    or an edit to one file did; or that file keeps changing back and forth (an edit putting back what an earlier one took out, and taking
    out what it put in, STUCK_FLIPS times); or nothing has worked for STUCK_QUIET_S though it keeps trying (2+ steps failed since). A step
    still running may settle it, and a log quiet for STUCK_QUIET_S isn't going any more: no alert then. -> (a key, told once per request,
    and the words) or None."""
    steps = [x for x in s.get("steps") or [] if x["kind"] in ("RUN", "EDIT")]
    if s.get("done_at") or not steps or steps[-1]["ok"] is None or now - max(x["at"] or 0 for x in steps) > STUCK_QUIET_S:
        return None
    runs = {}
    for x in steps:
        if x["kind"] == "RUN" and x.get("cmd"):
            runs.setdefault(" ".join(x["cmd"].split())[:200], []).append(x)
        elif x["kind"] == "EDIT":
            runs.setdefault(("edit", x["label"]), []).append(x)
    for cmd, xs in runs.items():
        failed = sum(x["ok"] is False for x in xs)
        if failed >= STUCK_TIMES and xs[-1]["ok"] is False:
            what = f"Editing `{cmd[1]}`" if isinstance(cmd, tuple) else f"`{_cut(cmd, 60)}`"
            return f"repeat:{cmd}", f"Going in circles? {what} still fails after {failed} tries"
    undone, done = {}, []
    for x in steps:
        if x["kind"] == "EDIT" and x["ok"] and x.get("swap"):
            path, gone, put = x["swap"]
            if (path, put, gone) in done:
                undone[x["label"]] = undone.get(x["label"], 0) + 1
            done.append(x["swap"])
    name, n = max(undone.items(), key=lambda kv: kv[1], default=(None, 0))
    if n >= STUCK_FLIPS:  # one key for the reason, whichever file: told once per request (Dotpals')
        return "flip", f"`{name}` keeps changing back and forth: an edit undone {n} times"
    ok_at = max([x["at"] or 0 for x in steps if x["ok"]] or [s.get("started") or 0])
    failed_since = [x for x in steps if x["ok"] is False and (x["at"] or 0) > ok_at]
    if ok_at and now - ok_at >= STUCK_QUIET_S and len(failed_since) >= 2:
        return f"quiet:{ok_at}", f"Nothing has worked for {round((now - ok_at) / 60)} minutes: {len(failed_since)} steps failed since"
    return None


def clashes(sessions, now):
    """Dotpals' two agents, one file: two of the owner's sessions edited the same file within GUARD_S of each other. -> [(a key, told once,
    the words: "Codex is editing billing.ts, which Claude Code (shop) changed 2 min ago")]."""
    out, seen = [], {}
    for key, s in sessions.items():
        for path, at in (s.get("edits") or {}).items():
            seen.setdefault(path, []).append((at or 0, key, s))
    for path, hits in seen.items():
        hits.sort(key=lambda h: h[0])
        for (a_at, a_key, a), (b_at, b_key, b) in zip(hits, hits[1:]):
            if a_key != b_key and b_at - a_at <= GUARD_S and now - b_at <= GUARD_S and not _mondays(a) and not _mondays(b):
                mins = max(1, round((b_at - a_at) / 60))
                out.append((f"clash:{path}:{a_key}:{b_key}", f"{b['agent']} is editing {Path(path).name}, which {a['agent']}"
                            f"{' (' + a['project'] + ')' if a.get('project') else ''} changed {mins} min ago"))
    return out


def alerts(items, now, told):
    """The alerts not told yet for scan()'s sessions: going in circles, then two agents on one file. told (kept by the caller) remembers
    each one told, its last 200. -> the AGENT_ALERT data: [{"kind", "text", "agent", "project"}]."""
    found = [("stuck", it["summary"], (it["key"], it["summary"].get("started"), why[0]), why[1]) for it in items if (why := stuck(it["summary"], now))]
    found += [("clash", {}, (key,), words) for key, words in clashes({it["key"]: it["summary"] for it in items}, now)]
    out = []
    for kind, s, key, words in found:
        if key not in told:
            told[key] = now
            out.append({"kind": kind, "text": words, "agent": s.get("agent"), "project": s.get("project")})
    for k in list(told)[:-200]:
        del told[k]
    return out


def brief(now=None, logs=None):
    """Dotpals' morning brief: what the owner's agents did since yesterday began, per project: its requests, the ones left unfinished, tests
    failing or untested, claims of passing tests the log doesn't show. Read from the logs changed since then (their last TAIL_BYTES)."""
    now = now or time.time()
    since = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - 86400
    projects = {}
    for agent, path in logs if logs is not None else [(a, p) for a, pattern in LOGS for p in glob.glob(pattern, recursive=True)]:
        try:
            mtime = os.stat(path).st_mtime
            if mtime < since or "subagents" in path:
                continue
            s = READERS[agent](read_new(path)[0])
        except (OSError, ValueError):
            continue
        if _mondays(s):
            continue
        entries = [e for e in s["story"] + ([_entry(s)] if s.get("asked") else []) if (e.get("started") or 0) >= since]
        if not entries:
            continue
        pr = projects.setdefault(s.get("project") or "a project", {"requests": 0, "unfinished": 0, "failing": False, "check": 0})
        pr["requests"] += len(entries)
        if s.get("asked") and not s.get("done_at") and now - mtime > LIVE_S:
            pr["unfinished"] += 1
        code = [n for n in s["files"] if not evidence.NOT_CODE.search(n)]
        t = s.get("tests")
        if (t and t["ok"] is False) or (code and (not t or t["changed_since"])):
            pr["failing"] = True
        if evidence.claims_pass(s.get("said") or "") and not (t and t["ok"] and not t["changed_since"]):
            pr["check"] += 1
    return projects


def brief_line(projects, label="Since yesterday"):
    """One line for the notch, as Dotpals' pal says it: "Since yesterday: 9 requests in 2 projects · 1 unfinished · tests failing or
    untested in shop · 2 things to check", or "" when nothing happened."""
    if not projects:
        return ""
    n = sum(p["requests"] for p in projects.values())
    left, check = sum(p["unfinished"] for p in projects.values()), sum(p["check"] for p in projects.values())
    failing = [name for name, p in projects.items() if p["failing"]]
    return " · ".join(x for x in (f"{label}: {_n(n, 'request')} in {_n(len(projects), 'project')}", f"{left} unfinished" if left else "",
                                  f"tests failing or untested in {', '.join(failing[:3])}" + (f" +{len(failing) - 3}" if len(failing) > 3 else "") if failing else "",
                                  f"{_n(check, 'thing')} to check" if check else "") if x)


def scan(cache=None, now=None):
    """The sessions whose logs changed in the last RECENT_S, newest first: [{"key", "state", "summary", "mtime"}]. cache (a dict kept by
    the caller) holds each log's last (mtime, size, where its read ended, summary): a log that didn't change isn't read again, one that
    grew is read from where it ended, its summary carried on."""
    now, cache = now or time.time(), cache if cache is not None else {}
    out = []
    for agent, pattern in LOGS:
        for p in glob.glob(pattern, recursive=True):
            try:
                st = os.stat(p)
            except OSError:
                continue
            if now - st.st_mtime > RECENT_S or "subagents" in p:
                continue
            try:
                s = summary(p, cache, agent)
            except OSError:
                continue
            if not _mondays(s):
                out.append({"key": p, "state": state(s, st.st_mtime, now), "summary": s, "mtime": st.st_mtime})
    return sorted(out, key=lambda x: -x["mtime"])


CHAT_TURNS = 8  # a session's chat in the notch: its last turns
NOTE_MARK = "A message from the user, typed in Monday's notch:"  # the owner's words Monday's hooks hand a session (Monday.take_notes)
NOTE_MARKS = (NOTE_MARK, "A message from the user, typed in Monday's notch while you worked:")  # (and as it read until 2026-10-22)


def _note_in(r):
    """The owner's words Monday's hooks handed a session (NOTE_MARKS), from the record Claude Code wrote for them: Stop's feedback (a meta
    user message), PreToolUse's added context (an attachment) or the wake hook's note (a task notification: an attachment's prompt, or
    a user message); None for any other record (never the agent's own words, nor a tool's output that quotes them)."""
    a = r.get("attachment") or {}
    c = (r.get("message") or {}).get("content") if r.get("type") == "user" and not r.get("isCompactSummary") else \
        a.get("content") or a.get("prompt") if r.get("type") == "attachment" else None
    for s in c if isinstance(c, list) else [c]:
        s = s.get("text") if isinstance(s, dict) else s
        mark = next((m for m in NOTE_MARKS if isinstance(s, str) and m in s), None)
        if mark:
            return s.split(mark, 1)[1].strip()
    return None


def chat_turns(path, agent, n=CHAT_TURNS):
    """A watched session's chat for the notch (the owner, 2026-10-17: "if the Claude is working, show the Claude chat there"): its last
    n turns, oldest first, [{"who": "you" | agent, "text"}]: the owner's words and the agent's own, from its log's tail (not its tools)."""
    out = []

    def add(who, text):
        text = (text or "").strip()
        if text:
            if out and out[-1]["who"] == who and who != "you":  # one answer said in several pieces is one turn
                out[-1]["text"] += "\n" + text
            else:
                out.append({"who": who, "text": text})
    for r in read_new(path, None)[0]:
        if isinstance(r, list) and agent == "Kilo Code":  # its task: the first words and "user_feedback" are the owner's
            for i, m in enumerate(x for x in r if isinstance(x, dict) and x.get("type") == "say"):
                kind = m.get("say")
                if kind == "user_feedback" or (kind == "text" and i == 0):
                    add("you", m.get("text"))
                elif kind in ("text", "completion_result"):
                    add(agent, m.get("text"))
            continue
        if isinstance(r, dict) and agent == "Kiro":  # one request: the owner's prompt, then its words
            add("you", str(((r.get("input") or {}).get("data") or {}).get("userPrompt") or "") if isinstance((r.get("input") or {}).get("data"), dict) else "")
            for a in r.get("actions") or []:
                if isinstance(a, dict) and a.get("actionType") == "say" and isinstance(a.get("output"), dict):
                    add(agent, a["output"].get("message"))
            continue
        if not isinstance(r, dict):
            continue
        msg, p = r.get("message") or {}, r.get("payload") or {}
        note = _note_in(r) if agent == "Claude Code" else None
        if note:  # what the owner wrote from the notch, where Claude Code read it
            add("you", note)
        elif agent == "Claude Code" and r.get("type") == "user" and not (r.get("isMeta") or r.get("isCompactSummary")):
            add("you", _owner_text(msg.get("content")))
        elif agent == "Claude Code" and r.get("type") == "assistant" and isinstance(msg.get("content"), list):
            add(agent, " ".join(b.get("text") or "" for b in msg["content"] if isinstance(b, dict) and b.get("type") == "text"))
        elif agent == "Codex" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            kind = "input_text" if p["role"] == "user" else "output_text"
            text = [{"type": "text", "text": c.get("text") or ""} for c in p.get("content") or [] if c.get("type") == kind]
            add("you", _owner_text(text)) if p["role"] == "user" else add(agent, " ".join(b["text"] for b in text))
    return out[-n:]


def story(path, agent, now=None):
    """Dotpals' Story for one log, on the owner's click: today's requests (local time) as entries, oldest first, and the summary they
    came from. The log is read whole, unlike the watch's tail ponytail: ~0.5 s for a 36 MB session here; a per-day offset if it drags)."""
    midnight = datetime.fromtimestamp(now or time.time()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    s = _place(agent, path, READERS[agent](read_new(path, 0)[0]))
    entries = s["story"] + ([{**_entry(s), "current": True}] if s["asked"] else [])
    return [e for e in entries if (e["started"] or 0) >= midnight], s


def _dur(took):
    return f"{int(took // 3600)}h {int(took % 3600 // 60)}m" if took >= 3600 else f"{int(took // 60)}m {int(took % 60)}s" if took >= 60 \
        else f"{took:.0f}s"


def _n(k, word):
    return f"{k} {word}" + ("" if k == 1 else "s")


def _tests(runs):
    """A request's test runs in plain words: "Tests failed once, then passed"; an unclear last run (None) is said as unclear."""
    if runs[-1] is None:
        return "Tests unclear (the output didn't say)"
    if not runs[-1]:
        return "Tests failed"
    fails = runs.count(False)
    return "Tests passed" if not fails else f"Tests failed {'once' if fails == 1 else f'{fails} times'}, then passed"


def story_words(agent, entries, s, shown=None):
    """The day in plain words, as Dotpals' Story tells it: the totals, tests gone stale, then each request, newest first (all of them,
    or the last `shown`)."""
    hm = lambda t: time.strftime("%H:%M", time.localtime(t)) if t else "?"  # noqa: E731
    files = {f for e in entries for f in e["files"]}
    work = sum(e["took"] or 0 for e in entries)
    lines = [f"Today · {_n(len(entries), 'request')} · {_n(len(files), 'file')} changed · {_n(sum(e['runs'] for e in entries), 'command')}"
             f" · {_dur(work)} of agent work"] + [x for x in [toolkit_line(s)] if x]
    t = s.get("tests")
    if t and t["ok"] and t["changed_since"]:
        lines.append(f"! Tests passed at {hm(t['at'])}; {', '.join(t['changed_since'][:3])} changed since: not tested")
    for e in list(reversed(entries))[:shown]:
        lines += ["", f"{hm(e['started'])} You asked: {_cut(e['asked'], 160)}"]
        if e["said"]:
            lines.append(f"{agent}: {_cut(e['said'], 300)}")
        lines += [f"· Changed {name}  +{a} −{r}" for name, (a, r) in list(e["files"].items())[:3]]
        if len(e["files"]) > 3:
            lines.append(f"· …and {_n(len(e['files']) - 3, 'more file')}")
        if e["test_runs"]:
            lines.append(f"· {_tests(e['test_runs'])}" + (f" ({e['test_label']})" if e["test_label"] else ""))
        if e["pushed"]:
            lines.append("· Pushed")
        if e["reads"]:
            lines.append(f"· Looked through {_n(e['reads'], 'file')}" + (f" in {e['read_in']}/" if e["read_in"] else ""))
        lines.append(f"✓ Done in {_dur(e['took'] or 0)}" if e["done_at"] else "Working…" if e.get("current") else "Stopped before it ended")
    if shown and len(entries) > shown:
        lines += ["", f"(Copy today has all {len(entries)} requests.)"]
    return lines


QUESTIONS_MAX, OPTIONS_MAX = 4, 6   # Claude Code's AskUserQuestion: at most this many questions, options each, as the notch shows them


def ask_questions(tool, inp):
    """Claude Code asking the owner (AskUserQuestion, through its PermissionRequest hook): [{question, header, multi, options:
    [{label, description}]}] for the notch and the window to offer, or None for anything else. Words are cut, never invented."""
    if str(tool or "") != "AskUserQuestion" or not isinstance(inp, dict) or not isinstance(inp.get("questions"), list):
        return None
    out = []
    for q in inp["questions"][:QUESTIONS_MAX]:
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        opts = [{"label": _cut(o.get("label"), 80), "description": _cut(o.get("description") or "", 200)}
                for o in (q.get("options") if isinstance(q.get("options"), list) else [])[:OPTIONS_MAX]
                if isinstance(o, dict) and str(o.get("label") or "").strip()]
        out.append({"question": str(q["question"]).strip(), "header": _cut(q.get("header") or "", 24), "multi": q.get("multiSelect") is True,
                    "options": opts})
    return out or None


def answers_cover(questions, answers):
    """The owner answered every one of Claude Code's questions (answers: question → their pick, a multi-select's picks joined)."""
    return isinstance(answers, dict) and all(isinstance(answers.get(q["question"]), str) and answers[q["question"]].strip()
                                             for q in questions or ())


def ask_words(tool, inp):
    """What an agent asks permission for, in words (Claude Code's PermissionRequest hook, core/approve_hook.py): (what, detail)."""
    tool, inp = str(tool or ""), inp if isinstance(inp, dict) else {}
    if tool in ("Bash", "PowerShell"):
        return "run a command", _cut((inp.get("command") or "").splitlines()[0] if inp.get("command") else inp.get("description") or "", 160)
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return ("write a file" if tool == "Write" else "edit a file"), str(inp.get("file_path") or inp.get("notebook_path") or "")
    if tool == "WebFetch":
        return "open a web page", str(inp.get("url") or "")
    if tool == "WebSearch":
        return "search the web", _cut(inp.get("query") or "", 160)
    if tool == "AskUserQuestion":  # a question, not a permission: the notch shows its options (ask_questions)
        qs = ask_questions(tool, inp) or [{"question": ""}]
        return "ask you" + (f" {len(qs)} questions" if len(qs) > 1 else ""), _cut(qs[0]["question"], 160)
    if tool == "ExitPlanMode":  # its plan, waiting for the owner's go
        plan = next((ln.strip("# ").strip() for ln in str(inp.get("plan") or "").splitlines() if ln.strip("# ").strip()), "")
        return "start on its plan", _cut(plan, 160)
    if tool.startswith("mcp__"):
        return f"use {tool.split('__')[-1].replace('_', ' ')}", _cut(json.dumps(inp, ensure_ascii=False), 160)
    return f"use {tool or 'a tool'}", _cut(json.dumps(inp, ensure_ascii=False), 160)


HANDOFF_AGENTS = {"codex": ("Codex", ()), "claude_code": ("Claude Code", ()), "gemini_cli": ("Gemini CLI", ("-i",))}  # Dotpals' three


def handoff_prompt(path):
    """The next agent's first words for a hand-off: they only name the note (Dotpals'), so nothing of the session's is on a command line."""
    return f"Read {path} and continue the work it describes."


def handoff_command(exe, args):
    """The console command that starts the next agent (Dotpals' launchCommand, on Windows): `cmd /s /k` with the agent's full path (a bare
    name would be looked for in the project's folder first) and each of its words quoted. ValueError for a quote, a % or a line break,
    which cmd can't keep literal inside quotes."""
    words = [str(exe), *map(str, args)]
    if any(re.search(r'["%\r\n]', w) for w in words):
        raise ValueError("The agent's path or the note's has characters a console can't take safely.")
    return 'cmd.exe /s /k "' + " ".join(w if w.startswith("-") and " " not in w else f'"{w}"' for w in words) + '"'


_PASTED = re.compile(r"<pasted_content[^>]*>.*?(?:</pasted_content>|$)", re.S)  # the desktop app's pasted text, in the owner's words


def _plain(text):
    """The owner's words without the desktop app's pasted-content wrapping (the pasted text itself stays in the session's log)."""
    return _PASTED.sub("[pasted text]", str(text or ""))


def _quoted(text, n=2000):
    """Words as a Markdown block quote (the owner's, or an agent's), cut at n."""
    t = _plain(text).strip()
    t = t if len(t) <= n else t[:n - 1] + "…"
    return "\n".join(f"> {ln}".rstrip() for ln in t.replace("\r\n", "\n").split("\n"))


def handoff_note(agent, entries, s, now=None):
    """Dotpals' hand-off note: what one of the owner's sessions asked for and did, for another agent to pick up (Continue in, or Copy).
    Markdown from the session's own log (story: today's requests, and the summary): the asks, the latest request, the files it changed,
    how the tests stand, what's left on its plan, what's worth a second look and its last words. None when there's nothing yet."""
    if not entries and not s.get("asked") and not s.get("edits"):
        return None
    now, cwd = now or time.time(), s.get("cwd")
    asks = [e["asked"] for e in entries if e.get("asked")]
    first, last = (asks[0], asks[-1]) if asks else (s.get("asked"), s.get("asked"))
    out = [f"# Hand-off from {agent}" + (f" · {s['project']}" if s.get("project") else ""), "",
           "You're continuing work another agent started. Read this, check the current state (the files below, `git status`, the tests), "
           "then continue: " + (f"“{_cut(_plain(last), 200)}”." if last else "the work described below."), ""]
    if first:
        out += ["## The original ask", "", _quoted(first), ""]
    if last and last != first:
        out += ["## The last request", "", _quoted(last), ""]
    if entries:  # the latest request in a few lines, the ones before it a sentence each
        e = entries[-1]
        out += ["## What was done", ""]
        if e.get("files"):
            out.append("Changed: " + ", ".join(f"`{n}` +{a} −{r}" for n, (a, r) in list(e["files"].items())[:12]) + ("…" if len(e["files"]) > 12 else ""))
        if e.get("test_runs"):
            out.append(_tests(e["test_runs"]) + (f" ({e['test_label']})" if e.get("test_label") else ""))
        if e.get("pushed"):
            out.append("Pushed.")
        out.append("Finished." if e.get("done_at") else "Still working, or stopped before it ended.")
        before = entries[:-1][-4:]
        if before:
            out += ["", "Before that:"] + [f"- “{_cut(_plain(b.get('asked')), 100)}”: " + (_cut(b["said"], 140) if b.get("said") else
                                           f"changed {_n(len(b['files']), 'file')}" if b.get("files") else "no changes") for b in before]
        out.append("")
    root = os.path.normcase(os.path.normpath(cwd)) if cwd else None
    edits = list(s.get("edits") or {})
    paths = [p for p in edits if not root or _under(p, root)][-30:]  # the project's: its scratch files and notes elsewhere are its own
    if paths:
        away = len(edits) - len([p for p in edits if not root or _under(p, root)])
        out += ["## Files changed in this session", ""] + [f"- `{os.path.relpath(p, root) if root else p}`" for p in paths] + \
            ([f"- and {_n(away, 'file')} outside the project (scratch files, notes)"] if away else []) + [""]
    t = s.get("tests")
    if t:
        said = "Tests passed" if t["ok"] else "Tests unclear" if t["ok"] is None else "Tests failed"
        out += ["## Tests", "", said + (f": {t['evidence']}" if t.get("evidence") else "")
                + (f"; {', '.join(t['changed_since'][:6])} changed since, not tested" if t["ok"] and t.get("changed_since") else "")
                + ". (Only the tests the agent ran: run them again to be sure.)", ""]
    todo = [(text, st) for text, st in s.get("plan") or [] if st not in ("completed", "done")]
    if todo:
        out += [f"## Still on its plan ({len(s['plan']) - len(todo)} of {len(s['plan'])} done)", ""] + \
            [f"- [ ] {_cut(text, 160)}" + (" (it was on this one)" if st == "in_progress" else "") for text, st in todo[:12]] + [""]
    warn = [f["text"] for f in flags_of(s) if f["level"] == "warn"]
    if warn:
        out += ["## Worth a second look", ""] + [f"- {w}" for w in warn[-6:]] + [""]
    said = s.get("said") or next((e["said"] for e in reversed(entries) if e.get("said")), None)
    if said:
        out += [f"## {agent}'s last message", "", _quoted(said, 2500), ""]
    out += ["---", f"_Written by Monday from {agent}'s session in {cwd or 'a folder its log leaves out'}, "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}. Monday only sees what the agent did through its tools: check the files "
            "for how things really stand._"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def ask_flags(tool, inp):
    """Dotpals' flags for a step about to happen, for the approval card: "Force-pushes to git", "Changes .env, which usually holds secrets"."""
    tool, inp = str(tool or ""), inp if isinstance(inp, dict) else {}
    path = inp.get("file_path") or inp.get("notebook_path")
    cmds = [(inp["command"], None)] if tool in ("Bash", "PowerShell") and inp.get("command") else []
    return [f["text"] for f in evidence.flags(cmds, changed=[path] if path and tool != "Read" else [], read=[path] if path and tool == "Read" else [],
                                              before=True)]


def words(item):
    """A watched session for the notch: (title, detail lines). The title says who and what now; the lines what was asked and the tests
    (the last edit is diff_words: the notch's code card; the plan, the context and the limits are meters: its bars)."""
    s, st = item["summary"], item["state"]
    # the session's own name first, as its app shows it ("/loop , /ponytail:ponytail-gain": the owner, 2026-10-20), else who and where
    who = s.get("name") or (f"{s['agent']}" + (f" · {s['project']}" if s.get("project") else ""))
    last = next((x for x in reversed(s["steps"]) if x["label"]), None)
    if st == "working":
        verb = {"EDIT": "Editing ", "READ": "Reading ", "SEARCH": "Searching ", "AGENT": "A helper: "}.get(last["kind"], "") if last else ""  # a run says itself
        title = f"{who} · " + (plan_line(s) or (verb + last["label"] if last else "working"))
    elif st == "done":
        title = f"{who} · done" + (f" in {_dur(s['took'])}" if s.get("took") else "")
    else:
        title = f"{who} · quiet"
    lines = []
    if s.get("asked"):
        lines.append(f"You asked: {_cut(s['asked'], 160)}")
    if st == "working":  # Dotpals' helpers, and anything worth a second look as it happens
        warn = [f["text"] for f in flags_of(s) if f["level"] == "warn"]
        lines += [x for x in [helpers_line(s), "Worth a second look: " + " · ".join(warn[:2]) if warn else ""] if x]
    if st == "done":  # Dotpals' "Done. In plain words": its own last words and what it changed, the diff is in the Story
        if s.get("said"):
            lines.append(_cut(s["said"], 300))
        if s["files"]:
            lines.append(f"Changed {_n(len(s['files']), 'file')}: {', '.join(list(s['files'])[:3])}" + ("…" if len(s["files"]) > 3 else ""))
        r = readiness(s)  # Dotpals' Ready to merge?, or why it stopped and what it said it did that it didn't
        why = why_stopped(s) if not r else None
        lines += [readiness_line(r)] if r else [why] * bool(why and not why.startswith("Finished: ")) + \
            ["! " + evidence.claim_line(c) for c in claims_of(s)[:2]]
    elif s.get("cut_at"):
        lines.append(why_stopped(s))
    t = s.get("tests")
    if t:  # what the output itself said ("Tests passed · 48 passed"), core/evidence.py; unclear is never said as passed or failed
        said = "Tests passed" if t["ok"] else "Tests unclear" if t["ok"] is None else "Tests failed"
        lines.append(said + (f" · {t['evidence']}" if t.get("evidence") and t["ok"] is not None else f": {t['evidence']}" if t.get("evidence") else "")
                     + (f" · {', '.join(t['changed_since'][:3])} changed since: not tested" if t["ok"] and t["changed_since"] else ""))
    if s.get("sent_back"):  # Dotpals' fix loop, through Claude Code's Stop hook (core/agent_hooks.py)
        lines.append(f"Monday sent it back to check its work ({s['sent_back']} of {FIX_TRIES})")
    return title, lines


def meters(item):
    """Dotpals' bars under the card: the plan (the step it is on, of all, while it works), the context (a bar only where the log says
    what fits) and the agent's limits (Codex's 5-hour and week). Each {"kind", "label", "of"}: "of" fills the bar, None is words only,
    never a guessed bar."""
    s, out = item["summary"], []
    if item["state"] == "working" and plan_line(s):
        done = sum(x in ("completed", "done") for _, x in s["plan"])
        on = any(x in ("in_progress", "pending") for _, x in s["plan"])
        out.append({"kind": "plan", "label": f"Plan {plan_line(s)}", "of": round(min(done + on, len(s["plan"])) / len(s["plan"]), 3)})
    if context_meter(s.get("context")):
        out.append(context_meter(s["context"]))
    lim = s.get("limits")
    return out + ([{"kind": "use", "label": f"{s['agent']} {k} {v}%", "of": round(min(max(v, 0), 100) / 100, 3)} for k, v in lim.items()]
                  if lim else [{"kind": "use", "label": f"{s['agent']} use: N/A (its log doesn't say)", "of": None}])


def diff_words(item):
    """The last edit, for the notch's code card (the owner, 2026-10-20: "i want to see the real edits too in the notch like this one",
    the Claude app's diff): {"path", "file", "removed", "added", "rows": [[sign, old line, new line, text]], "more"}. Claude Code's own
    hunks with their line numbers and the lines around them ("@" where one starts, " " around, "-" removed, "+" added); where the
    agent's log has none (Codex, Gemini, or before Claude Code's result is read), its removed lines then its added ones, no numbers made
    up. At most DIFF_ROWS rows; "more" counts the lines not sent. Kept while the session shows, done too. [] before any edit."""
    d = item["summary"].get("diff")
    if not d:
        return []
    rows = []
    for h in d.get("hunks") or [{"old": None, "new": None, "lines": ["-" + x for x in d["minus"]] + ["+" + x for x in d["plus"]]}]:
        o, n = h.get("old"), h.get("new")
        rows.append(["@", o, n, ""])
        for ln in h["lines"]:
            if ln.startswith("\\"):  # ("\ No newline at end of file": the patch's own note, not a line)
                continue
            sign, text = (ln[:1], ln[1:]) if ln[:1] in ("-", "+", " ") else (" ", ln)
            rows.append([sign, o if sign != "+" else None, n if sign != "-" else None, text[:DIFF_CHARS]])
            o = o + 1 if o is not None and sign != "+" else o
            n = n + 1 if n is not None and sign != "-" else n
    more = sum(r[0] != "@" for r in rows[DIFF_ROWS:]) + (0 if d.get("hunks") else
                                                        d["removed"] - len(d["minus"]) + d["added"] - len(d["plus"]))
    return {"path": d.get("path") or d["file"], "file": d["file"], "removed": d["removed"], "added": d["added"], "rows": rows[:DIFF_ROWS],
            "more": more}


APPS = {  # an agent that lives in an app (its log's entry -> the app's processes), and the dock's editors (their keys)
    "claude-desktop": ("claude.exe",), "claude-vscode": ("code.exe",), "codex_vscode": ("code.exe",), "Codex Desktop": ("chatgpt.exe", "codex.exe"),
    "codex_work_desktop": ("chatgpt.exe", "codex.exe"),  # (the ChatGPT app's Codex names itself this since 2026-10: the owner's, 2026-10-21)
    "antigravity": ("antigravity.exe",), "cursor": ("cursor.exe",), "kiro": ("kiro.exe",), "vscode": ("code.exe",),
}
CLIS = {"Claude Code": ("claude.exe", "claude-code"), "Codex": ("codex.exe", "codex"), "Gemini CLI": ("gemini.exe", "gemini")}  # its own
# process, or node running its package


IDES = {  # an open IDE (or agent app) window is one of the notch's agents (the owner, 2026-10-17: "whichever IDE, its logo ... two Kiros,
    # two agents with the folder name"): its process -> (key, name). No CLIs and no Hermes: they have no window of their own to show
    "code.exe": ("vscode", "VS Code"), "cursor.exe": ("cursor", "Cursor"), "kiro.exe": ("kiro", "Kiro"), "antigravity.exe": ("antigravity", "Antigravity"), "antigravity ide.exe": ("antigravity", "Antigravity"),
    "windsurf.exe": ("windsurf", "Windsurf"), "trae.exe": ("trae", "Trae"), "void.exe": ("void", "Void"), "zed.exe": ("zed", "Zed"),
    "devenv.exe": ("visualstudio", "Visual Studio"), "idea64.exe": ("idea", "IntelliJ IDEA"), "pycharm64.exe": ("pycharm", "PyCharm"),
    "webstorm64.exe": ("webstorm", "WebStorm"), "rider64.exe": ("rider", "Rider"), "studio64.exe": ("androidstudio", "Android Studio"),
    "claude.exe": ("claude", "Claude"), "codex.exe": ("codex", "Codex"),
    "chatgpt.exe": ("codex", "ChatGPT"),  # (the ChatGPT app is where its Codex chats run: OpenAI.Codex's ChatGPT.exe, 2026-10-21)
}


def ide_folder(title):
    """The folder an IDE window has open, from its title: "app.py - monday - Visual Studio Code" and "monday - Kiro" -> "monday",
    JetBrains' "monday – app.py" -> "monday"; "" when it says none ("app.py - Visual Studio Code", the Claude app's "Claude")."""
    t = re.sub(r"^[●•*]\s*|\s*\[[^\]]*\]$", "", title or "").strip()
    if " – " in t and " - " not in t:
        return t.split(" – ")[0].strip()
    parts = [x.strip() for x in t.split(" - ") if x.strip()]
    if len(parts) < 2 or (len(parts) == 2 and re.search(r"\.\w{1,6}$", parts[0])):
        return ""
    return re.sub(r"\s*\((?:Workspace|Untrusted|Restricted Mode)\)$", "", parts[-2] if len(parts) > 2 else parts[0])


def ide_windows(wins, exe_of=lambda pid: None):
    """Every open IDE window (wins: [(hwnd, pid, title, process)], top first) as one of the notch's agents: [{key, name, folder, hwnd,
    pid, exe}]; exe_of(pid) gives its program, for its own logo."""
    return [{"key": IDES[p.lower()][0], "name": IDES[p.lower()][1], "folder": ide_folder(t), "hwnd": h, "pid": pid, "exe": exe_of(pid)}
            for h, pid, t, p in wins if (p or "").lower() in IDES and (t or "").strip()]


def agent_window(agent, entry, cwd, wins, procs, cwd_of):
    """Where one of the owner's agents runs, to switch to it (the owner, 2026-10-16: "if there is a Claude Code working on the project
    and I click on that, then redirect to the Claude Code or Claude chat; same with Codex, Kiro, Cursor, Antigravity, Gemini"). The
    window of the app it lives in (the Claude app for its desktop sessions, VS Code, the Codex app; the one on its project first), else
    its own process (the one in the session's folder first) and up its parents to the first with a window: a CLI's terminal.
    wins: [(hwnd, pid, title, process)]; procs: {pid: (name, parent pid, command line)}; cwd_of(pid): its folder. -> (hwnd, title) or None."""
    project = os.path.basename(str(cwd or "").rstrip("\\/")).lower()
    if entry in APPS:  # it lives in an app: that app's window, or none (the Codex in VS Code isn't the Codex app's)
        app = [w for w in wins if w[3].lower() in APPS[entry]]
        return next(((w[0], w[2]) for w in app if project and project in w[2].lower()), (app[0][0], app[0][2]) if app else None)
    exe, pkg = CLIS.get(agent, (None, None))
    own = [pid for pid, (name, _, cmd) in procs.items() if exe and (name.lower() == exe or (name.lower() == "node.exe" and pkg in cmd.lower()))]
    own.sort(key=lambda pid: not (cwd and os.path.normcase(cwd_of(pid) or "") == os.path.normcase(str(cwd))))
    shown = {}
    for w in wins:
        shown.setdefault(w[1], w)
    for pid in own:
        for _ in range(12):  # up its parents to the first with a window
            if pid in shown:
                return shown[pid][0], shown[pid][2]
            pid = procs.get(pid, ("", 0, ""))[1]
            if not pid:
                break
    return None


def context_meter(c):
    """Dotpals' context bar: how full the agent's context is when its log says what fits (Codex), else only what it used (no bar)."""
    if not c or not c.get("used"):
        return None
    used = f"{round(c['used'] / 1000)}k"
    if not c.get("window"):
        return {"kind": "context", "label": f"Context {used} tokens (its log doesn't say how many fit)", "of": None}
    of = min(1.0, c["used"] / c["window"])
    return {"kind": "context", "label": f"Context {round(of * 100)}% ({used} of {round(c['window'] / 1000)}k)", "of": round(of, 3)}


def _took(t):
    return f"{round(t * 1000)} ms" if t < 1 else _dur(t)


VIEWS = {  # Dotpals' filters of the timeline: what each shows, its chip, the noun its count takes
    "key": (lambda st: st["kind"] in ("RUN", "EDIT"), "Key steps", "key step"),
    "changes": (lambda st: st["kind"] == "EDIT", "Changes", "change"),
    "commands": (lambda st: st["kind"] == "RUN", "Commands", "command"),
    "problems": (lambda st: st["ok"] is False, "Problems", "problem"),
    "all": (lambda st: True, "All", "step"),
}


def steps_words(s, shown=STEPS_SHOWN, view="key"):
    """Dotpals' step timeline for the request now, through one of its filters (VIEWS; key steps: the runs and edits), oldest first,
    each with how long it took, which try it was, and a failure a later try fixed; the first line sums them up."""
    steps, wanted = s["steps"], VIEWS[view][0]
    seen, tries = {}, []
    for st in steps:  # tries count over every step: a filter doesn't renumber them
        k = (st["kind"], st["label"])
        seen[k] = seen.get(k, 0) + 1
        tries.append(seen[k])
    lines, n, bad, fixed = [], 0, 0, 0
    for i, st in enumerate(steps):
        if not wanted(st):
            continue
        fix = next((tries[j] for j in range(i + 1, len(steps)) if (steps[j]["kind"], steps[j]["label"]) == (st["kind"], st["label"])
                    and steps[j]["ok"]), None) if st["ok"] is False else None
        n, bad, fixed = n + 1, bad + (st["ok"] is False), fixed + bool(fix)
        bits = [st["kind"], st["label"]] + ([f"try {tries[i]}"] if tries[i] > 1 else []) + ([_took(st["took"])] if st["took"] else [])
        lines.append(" · ".join(bits) + (" ✓" if st["ok"] else " ✗" if st["ok"] is False else " …") + (f" fixed on try {fix}" if fix else ""))
    if not n:
        return []
    mend = ", fixed" if fixed == bad else f", {fixed} fixed" if fixed else ""
    head = _n(bad, "problem") + mend if view == "problems" else \
        _n(n, VIEWS[view][2]) + (f" · {_n(bad, 'problem')}{mend}" if bad else " · no problems")
    return [head] + lines[-shown:]


def steps_views(s):
    """The notch's Steps, as Dotpals' Tools and Files: [view, its chip with its count, its lines] for each filter with something in
    it, then the files this request changed (+ / −). [] before the request's first run or edit."""
    if not steps_words(s):
        return []
    out = [[v, f"{chip} {sum(map(want, s['steps']))}", steps_words(s, view=v)] for v, (want, chip, _) in VIEWS.items() if any(map(want, s["steps"]))]
    only = sorted({Path(x).name for x in s["read"]} - set(s["files"]), key=str.lower)
    if s["files"] or only:  # Dotpals' Files: what it changed (+ −, how often), then what it only read
        out.append(["files", f"Files {len(s['files'])}", [f"Changed · {len(s['files'])}" if s["files"] else "Nothing changed yet"]
                    + [f"{name}  +{a} −{r}" + (f" · edited {s['edit_count'][name]}×" if s["edit_count"].get(name, 0) > 1 else "")
                       for name, (a, r) in s["files"].items()]
                    + ([f"Only read · {len(only)}: {', '.join(only[:8])}" + (f" and {len(only) - 8} more" if len(only) > 8 else "")] if only else [])])
    check = check_words(s)
    if check:  # Dotpals' Ready to merge?, why it stopped, the claim check and the flags
        bad = sum(x.startswith("! ") for x in check)
        out.append(["check", f"Check {bad}" if bad else "Check ✓", check])
    return out


if __name__ == "__main__":
    import sys
    if "--now" in sys.argv:
        for it in scan():
            t, ls = words(it)
            print(f"[{it['state']}] {t}")
            d = diff_words(it)
            edit = [f"{d['path']}  −{d['removed']} +{d['added']}"] + [f"{r[0]} {r[2] if r[0] != '-' else r[1] or ''} {r[3]}"
                                                                      for r in d["rows"] if r[0] != "@"] if d else []
            for ln in ls + edit + [m["label"] for m in meters(it)]:
                print("    " + ln)
        sys.exit(0)
    assert [ide_folder(t) for t in ("app.py - monday - Visual Studio Code", "monday - Kiro", "● notch.py - app - Cursor", "Welcome - shop - Kiro",
                                    "app.py - Visual Studio Code", "Claude", "monday – notch.py", "shop (Workspace) - Visual Studio Code [Administrator]")] \
        == ["monday", "monday", "app", "shop", "", "", "monday", "shop"], "an IDE window's folder, from its title"
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:  # a session's chat: the owner's words and the agent's, not its tools or injected context
        _log = Path(_d) / "s.jsonl"
        _log.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "message": {"content": "Fix the rounding"}},
            {"type": "user", "isMeta": True, "message": {"content": "<system-reminder>x</system-reminder>"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "name": "Bash", "input": {}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed it."}]}},
            {"type": "user", "message": {"content": "thanks"}},
            {"type": "attachment", "attachment": {"type": "hook_additional_context", "hookEvent": "PreToolUse",
                                                  "content": [NOTE_MARK + "\nuse port 9000"]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Quoting: " + NOTE_MARK + " no"}]}},
            {"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback:\n" + NOTE_MARK + "\nand add a test"}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "grep: " + NOTE_MARK + " x"}]}},
            {"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback:\n" + NOTE_MARKS[1] + "\nas it read before"}},
            {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "<task-notification>\n<summary>Monday: your words from "
                                                  "the notch</summary>\n</task-notification>\nMonday: " + NOTE_MARK + "\nare you there?"}}]) + "\n",
            encoding="utf-8")
        assert chat_turns(_log, "Claude Code") == [{"who": "you", "text": "Fix the rounding"}, {"who": "Claude Code", "text": "Looking.\nFixed it."},
                                                   {"who": "you", "text": "thanks"}, {"who": "you", "text": "use port 9000"},
                                                   {"who": "Claude Code", "text": "Quoting: " + NOTE_MARK + " no"},
                                                   {"who": "you", "text": "and add a test"}, {"who": "you", "text": "as it read before"},
                                                   {"who": "you", "text": "are you there?"}], \
            ("the owner's words from the notch show where Claude Code read them (its hooks' records: the wake hook's too), never from its "
             "own words or a tool's output", chat_turns(_log, "Claude Code"))
    found = ide_windows([(1, 10, "monday - Kiro", "Kiro.exe"), (2, 11, "Windows Terminal", "WindowsTerminal.exe"), (3, 12, "shop - Kiro", "kiro.exe"),
                         (4, 13, "Claude", "claude.exe"), (5, 14, "Hermes", "python.exe")], exe_of=lambda pid: f"C:\\x\\{pid}.exe")
    assert [(a["name"], a["folder"], a["exe"]) for a in found] == [("Kiro", "monday", "C:\\x\\10.exe"), ("Kiro", "shop", "C:\\x\\12.exe"),
                                                                   ("Claude", "", "C:\\x\\13.exe")], "two Kiros, two agents; no terminal, no Hermes"
    T = lambda s: f"2026-10-15T20:{s}Z"  # noqa: E731
    recs = [
        {"type": "user", "timestamp": T("10:00"), "cwd": r"C:\Users\dev\shop", "message": {"role": "user", "content": "Fix the VAT rounding, then push to main"}},
        {"type": "assistant", "timestamp": T("10:05"), "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {"todos": [{"content": "Find the VAT rounding bug", "status": "completed"},
                                                                                   {"content": "Fix the rounding in billing.ts", "status": "in_progress"},
                                                                                   {"content": "Run the tests", "status": "pending"}, {"content": "Push", "status": "pending"}]}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "npm test", "description": "Run the tests"}}]}},
        {"type": "user", "timestamp": T("10:20"), "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "48 passed"}]}},
        {"type": "assistant", "timestamp": T("10:30"), "message": {"content": [{"type": "tool_use", "id": "t3", "name": "Edit", "input": {
            "file_path": r"C:\Users\dev\shop\src\billing.ts", "old_string": "const vat = Math.floor(net * rate * 100) / 100;",
            "new_string": "const vat = Math.round(net * rate * 100) / 100;"}}]}},
        {"type": "user", "isMeta": True, "timestamp": T("10:31"), "message": {"content": "<system-reminder>ignore me</system-reminder>"}},
    ]
    s = claude(recs)
    assert s["asked"] == "Fix the VAT rounding, then push to main" and s["project"] == "shop", s
    assert _owner_text([{"type": "image", "source": {}}, {"type": "text", "text": "continue and fix it"}]) == "continue and fix it", "a screenshot with the words"
    assert _owner_text([{"type": "text", "text": "<system-reminder>x</system-reminder>"}]) is None and _owner_text([{"type": "tool_result"}]) is None
    assert _owner_text('<pasted_content id="1">the whole spec\nmore</pasted_content>\n\n and also do X') == "and also do X", "the words after a paste"
    assert _owner_text('<pasted_content id="1">only a paste</pasted_content>') is None and _owner_text("[Request interrupted by user]") is None
    assert plan_line(s) == "2/4 · Fix the rounding in billing.ts", plan_line(s)
    assert s["diff"] == {"file": "billing.ts", "path": r"C:\Users\dev\shop\src\billing.ts", "minus": ["const vat = Math.floor(net * rate * 100) / 100;"],
                         "plus": ["const vat = Math.round(net * rate * 100) / 100;"], "removed": 1, "added": 1}
    assert s["tests"] == {"ok": True, "at": _ts(T("10:20")), "changed_since": ["billing.ts"], "evidence": "48 passed"}, \
        "what its output said, and changed after the tests passed: not tested since"
    assert words({"summary": s, "state": "working"})[1][-1] == "Tests passed · 48 passed · billing.ts changed since: not tested"
    assert words({"summary": s, "state": "working"})[0].startswith("Claude Code · shop · "), "no name in the log: who and where"
    named = claude([{"type": "custom-title", "customTitle": "/loop , /ponytail:ponytail-gain", "sessionId": "x"}], s)  # the app's sidebar name
    assert named["name"] == "/loop , /ponytail:ponytail-gain" and words({"summary": named, "state": "working"})[0].startswith("/loop , /ponytail:ponytail-gain · "), \
        "the session's name leads the title (the owner, 2026-10-20)"
    assert words({"summary": named, "state": "done"})[0] == "/loop , /ponytail:ponytail-gain · done", words({"summary": named, "state": "done"})[0]
    # Claude Code's hooks (core/agent_hooks.py): the fix loop on Stop, the commit gate, the two-agents guard, on this made-up log
    assert s["edits"] == {os.path.normcase(r"C:\Users\dev\shop\src\billing.ts"): _ts(T("10:30"))}, s["edits"]
    assert stop_reason(s, 0) is None, "changed since the pass, but it claims nothing: it may stop (the notch says not tested)"
    s["said"] = "Fixed the rounding. All 48 tests pass."
    assert "billing.ts changed after they last passed" in stop_reason(s, 0) and stop_reason(s, FIX_TRIES) is None, stop_reason(s, 0)
    assert gate_reason(s, "git commit -m vat")[0] == "ask" and "billing.ts" in gate_reason(s, "git push origin main")[1]
    s["tests"] = {**s["tests"], "ok": False, "evidence": "1 failed, 47 passed · test_vat_rounding"}
    assert "failed after your change (1 failed, 47 passed" in stop_reason(s, 0) and gate_reason(s, "git push")[0] == "deny"
    assert stop_reason(s, 1) and not stop_reason({**s, "said": "The rounding test still fails; it failed before my change too."}, 1), \
        "sent back once: again only while it claims a pass"
    s["tests"] = {**s["tests"], "ok": True, "changed_since": [], "evidence": "48 passed"}
    assert stop_reason(s, 0) is None and gate_reason(s, "git commit -m x") is None, "a pass after the last change: nothing to say"
    assert gate_reason({**s, "files": {"README.md": [1, 0]}}, "git push") is None, "no code changed: not gated"
    other = claude([{**recs[0], "cwd": r"C:\Users\dev\shop"}, recs[3]])
    me = {"edits": {}}
    now = _ts(T("15:30"))  # (T takes minutes:seconds: five minutes after the edit)
    assert "Claude Code in shop edited billing.ts 5 min ago" in guard_reason({"other.jsonl": other, "me.jsonl": me}, "me.jsonl",
                                                                               r"C:\Users\dev\shop\src\billing.ts", now)
    assert guard_reason({"other.jsonl": other}, "other.jsonl", r"C:\Users\dev\shop\src\billing.ts", now) is None, "its own edit"
    assert guard_reason({"other.jsonl": other}, "me.jsonl", r"C:\Users\dev\shop\src\billing.ts", now + GUARD_S) is None, "long ago"
    assert guard_reason({"other.jsonl": other}, "me.jsonl", r"C:\Users\dev\shop\src\cart.ts", now) is None, "another file"
    # Dotpals' alerts: going in circles, two agents on one file (once each), and the morning brief
    st = lambda ok, cmd="npm test", at=0.0: {"kind": "RUN", "label": cmd, "ok": ok, "id": None, "test": True, "push": False, "at": at, "took": 1, "cmd": cmd}  # noqa: E731
    circling = {**_new("Codex"), "steps": [st(False, at=1), st(False, at=2), st(True, "ls", 3), st(False, at=4)], "started": 1}
    assert stuck(circling, 10)[1] == "Going in circles? `npm test` still fails after 3 tries" and stuck({**circling, "done_at": 9}, 10) is None
    assert stuck({**circling, "steps": circling["steps"][:2]}, 10) is None and stuck({**circling, "steps": circling["steps"] + [st(None, at=5)]}, 10) is None
    assert stuck(circling, 4 + STUCK_QUIET_S + 1) is None, "left going in circles long ago (a Monday restart reads it again): not going any more"
    flipper = _new("Claude Code")
    flipper["cwd"] = r"C:\Users\dev\shop"
    for n_, (old, new) in enumerate([("a = 1", "a = 2"), ("a = 2", "a = 1"), ("a = 1", "a = 2"), ("a = 2", "a = 1")]):
        flipper["last"] = 10.0 + n_
        _step(flipper, "EDIT", "app.py", f"e{n_}")
        _edited(flipper, "app.py", *_changed(old, new))
        flipper["steps"][-1]["ok"] = True
    sw = [x["swap"] for x in flipper["steps"]]
    assert sw[0] == sw[2] and sw[1] == (sw[0][0], sw[0][2], sw[0][1]), sw
    assert stuck({**flipper, "steps": flipper["steps"][:2]}, 20) is None, "undone once: not yet"
    assert stuck(flipper, 20) == ("flip", "`app.py` keeps changing back and forth: an edit undone 3 times"), stuck(flipper, 20)
    misses = {**_new("Claude Code"), "steps": [{**x, "ok": False} for x in flipper["steps"][:3]], "started": 9}
    assert stuck(misses, 20)[1] == "Going in circles? Editing `app.py` still fails after 3 tries", "an edit to one file failing 3 times"
    _step(flipper, "EDIT", "a.py, b.py", "e9")
    _edited(flipper, "b.py", ["x"], ["y"])
    assert "swap" not in flipper["steps"][-1], "a patch over several files mixes their lines: not one file's edit"
    quiet = {**_new("Codex"), "steps": [st(True, "ls", 100), st(False, "make a", 300), st(False, "make b", 500)], "started": 50}
    assert stuck(quiet, 500) is None and stuck(quiet, 100 + STUCK_QUIET_S + 1)[1].startswith("Nothing has worked for 10 minutes: 2 steps failed")
    later = claude([{**recs[0], "cwd": r"C:\Users\dev\shop"}, recs[3]])
    later["agent"], later["edits"] = "Codex", {k: v + 120 for k, v in other["edits"].items()}
    found = clashes({"a.jsonl": other, "b.jsonl": later}, now)
    assert len(found) == 1 and found[0][1] == "Codex is editing billing.ts, which Claude Code (shop) changed 2 min ago", found
    assert not clashes({"a.jsonl": other}, now) and not clashes({"a.jsonl": other, "b.jsonl": later}, now + 2 * GUARD_S), "one session, or long ago"
    told = {}
    said = alerts([{"key": "c.jsonl", "summary": circling}], 10, told)
    assert [(a["kind"], a["agent"], a["text"]) for a in said] == [("stuck", "Codex", "Going in circles? `npm test` still fails after 3 tries")], said
    assert alerts([{"key": "c.jsonl", "summary": circling}], 11, told) == [], "told once"
    pair = [{"key": "a.jsonl", "summary": other}, {"key": "b.jsonl", "summary": later}]
    said = alerts(pair, now, told)
    assert [(a["kind"], a["agent"]) for a in said] == [("clash", None)] and said[0]["text"].startswith("Codex is editing billing.ts") and not alerts(pair, now + 1, told)
    told = dict.fromkeys(range(250), 0)
    assert alerts([], 0, told) == [] and len(told) == 200 and 249 in told, "it remembers the last 200"
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "s.jsonl"
        log.write_text("\n".join(json.dumps({**r, "timestamp": datetime.fromtimestamp(time.time() - 3600).isoformat()}) for r in recs[:4]) + "\n", encoding="utf-8")
        os.utime(log, (time.time() - 3600, time.time() - 3600))  # its last line an hour ago: no longer working, so unfinished
        pr = brief(time.time(), [("Claude Code", str(log))])
        assert pr == {"shop": {"requests": 1, "unfinished": 1, "failing": True, "check": 0}}, pr
        assert brief_line(pr) == "Since yesterday: 1 request in 1 project · 1 unfinished · tests failing or untested in shop", brief_line(pr)
        assert brief_line({}) == "" and brief(time.time(), [("Claude Code", str(Path(d) / "gone.jsonl"))]) == {}
    piped = claude([recs[0], {"type": "assistant", "timestamp": T("10:05"), "message": {"content": [
        {"type": "tool_use", "id": "p1", "name": "Bash", "input": {"command": "npm test | tail -3"}}]}},
        {"type": "user", "timestamp": T("10:06"), "message": {"content": [{"type": "tool_result", "tool_use_id": "p1", "content": "done"}]}}])
    assert piped["tests"]["ok"] is None and words({"summary": piped, "state": "working"})[1][-1].startswith("Tests unclear: its output went through a pipe"), \
        ("a piped run's exit code is the pipe's: unclear, never passed", piped["tests"])
    grep = claude([recs[0], {"type": "assistant", "timestamp": T("10:05"), "message": {"content": [
        {"type": "tool_use", "id": "g1", "name": "Bash", "input": {"command": "grep -rn \"npm test\" README.md"}}]}}])
    assert not grep["steps"][-1]["test"], "a command that mentions a test command isn't a test run"
    assert state(s, mtime=_ts(T("10:31")), now=_ts(T("10:50"))) == "working" and s["done_at"] is None
    s2 = claude(recs + [{"type": "assistant", "timestamp": T("11:47"), "message": {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": "Fixed the VAT rounding in billing.ts: it rounded down. All 48 tests pass."}]}}])
    assert state(s2, mtime=_ts(T("11:47")), now=_ts(T("11:50"))) == "done" and s2["took"] == 107.0
    title, lines = words({"summary": s2, "state": "done"})
    assert title == "Claude Code · shop · done in 1m 47s" and lines[1].startswith("Fixed the VAT rounding"), (title, lines)
    s3 = claude(recs + [{"type": "assistant", "timestamp": T("10:40"), "message": {"content": [{"type": "tool_use", "id": "t4", "name": "Write", "input": {
        "file_path": r"C:\Users\dev\AppData\Local\Temp\try.ps1", "content": "# a scratch try, " + "word " * 40}}]}}])
    assert s3["tests"]["changed_since"] == ["billing.ts"] and s3["diff"]["file"] == "try.ps1", "a scratch file outside the project isn't untested code"
    whole = diff_words({"summary": s3, "state": "working"})
    assert whole["rows"][1] == ["+", None, None, "# a scratch try, " + "word " * 40] and whole["path"].endswith("try.ps1"), \
        "a line whole, not cut at a word: the notch wraps it"
    assert diff_words({"summary": s, "state": "working"}) == {"path": r"C:\Users\dev\shop\src\billing.ts", "file": "billing.ts", "removed": 1,
                                                              "added": 1, "more": 0, "rows": [["@", None, None, ""],
                                                              ["-", None, None, "const vat = Math.floor(net * rate * 100) / 100;"],
                                                              ["+", None, None, "const vat = Math.round(net * rate * 100) / 100;"]]}, \
        "the code card's rows before Claude Code's own result is read: removed then added, no line numbers made up"
    assert diff_words({"summary": s2, "state": "done"})["file"] == "billing.ts" and diff_words({"summary": claude(recs[:1]), "state": "working"}) == [], \
        "done: its last edit still shown (the owner wants the real edits); no edit yet: no card"
    real = claude(recs + [{"type": "user", "timestamp": T("10:31"), "toolUseResult": {  # Claude Code's own result of that edit
        "filePath": r"C:\Users\dev\shop\src\billing.ts", "oldString": "x", "newString": "y", "structuredPatch": [
            {"oldStart": 11, "oldLines": 3, "newStart": 11, "newLines": 3, "lines": [
                " export function vat(net, rate) {", "-  const vat = Math.floor(net * rate * 100) / 100;",
                "+  const vat = Math.round(net * rate * 100) / 100;", "   return vat", "\\ No newline at end of file"]},
            {"oldStart": 40, "oldLines": 1, "newStart": 40, "newLines": 2, "lines": [" }", "+export const RATE = 0.2"]}]},
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t3", "content": "The file has been updated."}]}}])
    assert diff_words({"summary": real, "state": "working"})["rows"] == [
        ["@", 11, 11, ""], [" ", 11, 11, "export function vat(net, rate) {"], ["-", 12, None, "  const vat = Math.floor(net * rate * 100) / 100;"],
        ["+", None, 12, "  const vat = Math.round(net * rate * 100) / 100;"], [" ", 13, 13, "  return vat"],
        ["@", 40, 40, ""], [" ", 40, 40, "}"], ["+", None, 41, "export const RATE = 0.2"]], \
        "Claude Code's own hunks: each line by its number, the old for a removed one and the new for an added one, the lines around kept"
    other = claude(recs + [{"type": "user", "toolUseResult": {"filePath": r"C:\x\other.ts", "structuredPatch": [{"oldStart": 1, "newStart": 1,
                                                                                                                "lines": ["+z"]}]}}])
    assert "hunks" not in other["diff"], "another file's result isn't this edit's"
    made = claude(recs + [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t9", "name": "Write", "input": {
        "file_path": r"C:\Users\dev\shop\src\new.ts", "content": "a\nb"}}]}},
        {"type": "user", "toolUseResult": {"type": "create", "filePath": r"C:\Users\dev\shop\src\new.ts", "content": "a\nb", "structuredPatch": []},
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t9", "content": "File created"}]}}])
    assert diff_words({"summary": made, "state": "working"})["rows"] == [["@", 0, 1, ""], ["+", None, 1, "a"], ["+", None, 2, "b"]], \
        "a new file: its lines from 1"
    long = claude(recs + [{"type": "user", "toolUseResult": {"filePath": r"C:\Users\dev\shop\src\billing.ts", "structuredPatch": [
        {"oldStart": 1, "newStart": 1, "lines": [f"+line {i}" for i in range(100)]}]}}])
    lw = diff_words({"summary": long, "state": "working"})
    assert len(lw["rows"]) == DIFF_ROWS and lw["more"] == 101 - DIFF_ROWS and lw["rows"][-1][2] == DIFF_ROWS - 1, \
        "at most DIFF_ROWS rows to the notch, the rest counted"
    assert not any(x.startswith(("− ", "+ ")) for x in words({"summary": s, "state": "working"})[1]), "the edit is the card's, not the words'"
    assert meters({"summary": s, "state": "working"}) == [{"kind": "plan", "label": "Plan 2/4 · Fix the rounding in billing.ts", "of": 0.5},
                                                          {"kind": "use", "label": "Claude Code use: N/A (its log doesn't say)", "of": None}], \
        "Dotpals' bars: the plan on step 2 of 4; Claude's limits aren't in its log, so words only"
    assert [m["kind"] for m in meters({"summary": s2, "state": "done"})] == ["use"], "done: no plan bar"
    assert not any(x.startswith(("Plan ", "Context ")) or " use: " in x for x in words({"summary": s, "state": "working"})[1]), \
        "the plan, the context and the limits are the bars', not the words'"
    assert words({"summary": claude(recs[:1] + recs[3:4]), "state": "working"})[0] == "Claude Code · shop · Editing billing.ts"
    up = lambda i, inp: {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": "mcp__ccd_turn__update_plan", "input": inp}]}}  # noqa: E731
    p = claude([up("p1", {"items": [{"content": c, "status": "pending"} for c in "abcd"]}), up("p2", {"done": [2, 1], "start": 3}),
                up("p3", {"done": [9]})])
    assert plan_line(p) == "3/4 · c", "the app's done/start marks, a step out of range ignored"
    tool = lambda at, *uses: {"type": "assistant", "timestamp": T(at), "message": {"content": [  # noqa: E731
        {"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in uses]}}
    res = lambda at, *rs: {"type": "user", "timestamp": T(at), "message": {"content": [  # noqa: E731
        {"type": "tool_result", "tool_use_id": i, "content": out, "is_error": bad} for i, out, bad in rs]}}
    day = [recs[0], tool("10:02", ("r1", "Read", {"file_path": r"C:\Users\dev\shop\src\billing.ts"}), ("b1", "Bash", {"command": "npm test"})),
           res("10:03", ("b1", "1 failed, 47 passed", True)), recs[3],
           tool("10:40", ("b2", "Bash", {"command": "npm test"}), ("b3", "Bash", {"command": "git push origin main", "description": "Push to main"})),
           res("10:41", ("b2", "48 passed", False), ("b3", "main -> main", False)),
           {"type": "assistant", "timestamp": T("11:47"), "message": {"stop_reason": "end_turn", "content": [
               {"type": "text", "text": "Fixed the VAT rounding in billing.ts: it rounded down. All 48 tests pass, and it's pushed to main."}]}},
           {"type": "user", "timestamp": T("12:00"), "message": {"content": "Make the checkout button bigger"}},
           tool("12:05", ("e2", "Edit", {"file_path": r"C:\Users\dev\shop\src\app.tsx", "old_string": '<div>\n  <Button size="s" />\n</div>',
                                         "new_string": '<div>\n  <Button size="l" />\n</div>'}))]
    sd = claude(day)
    entries = sd["story"] + [{**_entry(sd), "current": True}]
    e1 = entries[0]
    assert [e["asked"] for e in entries] == ["Fix the VAT rounding, then push to main", "Make the checkout button bigger"], entries
    assert (e1["files"], e1["test_runs"], e1["pushed"], e1["reads"], e1["read_in"], e1["runs"], e1["took"]) == \
        ({"billing.ts": [1, 1]}, [False, True], True, 1, "src", 3, 107.0), e1
    assert entries[1]["files"] == {"app.tsx": [1, 1]}, "difflib: the one line that changed, not the three replaced"
    hm = lambda at: time.strftime("%H:%M", time.localtime(_ts(T(at))))  # noqa: E731
    assert story_words("Claude Code", entries, sd) == [
        "Today · 2 requests · 2 files changed · 3 commands · 1m 47s of agent work",
        f"! Tests passed at {hm('10:41')}; app.tsx changed since: not tested",
        "", f"{hm('12:00')} You asked: Make the checkout button bigger", "· Changed app.tsx  +1 −1", "Working…",
        "", f"{hm('10:00')} You asked: Fix the VAT rounding, then push to main",
        "Claude Code: Fixed the VAT rounding in billing.ts: it rounded down. All 48 tests pass, and it's pushed to main.",
        "· Changed billing.ts  +1 −1", "· Tests failed once, then passed (npm test)", "· Pushed", "· Looked through 1 file in src/",
        "✓ Done in 1m 47s"], story_words("Claude Code", entries, sd)
    assert story_words("Claude Code", entries, sd, shown=1)[-1] == "(Copy today has all 2 requests.)"
    assert _tests([False, False, True]) == "Tests failed 2 times, then passed" and _tests([True, False]) == "Tests failed"
    assert steps_words(claude(day[:7])) == [  # the first request, to its end: Dotpals' "fixed on try 2"
        "4 key steps · 1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2", "EDIT · billing.ts …",  # (T("10:02") is 20:10:02)
        "RUN · npm test · try 2 · 1s ✓", "RUN · Push to main · 1s ✓"], steps_words(claude(day[:7]))
    assert steps_words(claude(recs[:1])) == [] and (_took(0.65), _took(65)) == ("650 ms", "1m 5s")
    first = claude(day[:7])  # Dotpals' filters of the same request: its chips count, its tries stay numbered
    assert steps_words(first, view="problems") == ["1 problem, fixed", "RUN · npm test · 1s ✗ fixed on try 2"], steps_words(first, view="problems")
    assert steps_words(first, view="changes") == ["1 change · no problems", "EDIT · billing.ts …"] and \
        steps_words(first, view="commands")[0] == "3 commands · 1 problem, fixed", steps_words(first, view="commands")
    assert [(v, chip) for v, chip, _ in steps_views(first)] == [("key", "Key steps 4"), ("changes", "Changes 1"), ("commands", "Commands 3"),
                                                                ("problems", "Problems 1"), ("all", "All 5"), ("files", "Files 1"),
                                                                ("check", "Check ✓")], steps_views(first)
    assert steps_views(first)[-2][2] == ["Changed · 1", "billing.ts  +1 −1"] and steps_views(claude(recs[:1])) == [], \
        "Dotpals' Files: what this request changed; no steps yet, no Steps"
    # Dotpals' Ready to merge?, why it stopped, the claim check, the flags, the helpers and "Using" (pass 9)
    assert steps_views(first)[-1][2] == ["Ready to merge", "✓ Tests ran", "✓ Tests ran after the last change", "✓ Tests passed",
                                         "✓ No failures left behind", "✓ Nothing risky"], steps_views(first)[-1][2]
    assert readiness_line(readiness(first)) == "✓ Ready to merge: tests ran after the last change and passed, nothing risky" and \
        readiness_line(readiness(first)) in words({"summary": first, "state": "done"})[1] and readiness(sd) is None, "the next request: still working"
    bad = claude([{"type": "user", "timestamp": T("30:00"), "cwd": r"C:\Users\dev\shop", "message": {"content": "Wire the payment key"}},
                  tool("30:01", ("k1", "Edit", {"file_path": r"C:\Users\dev\shop\.env", "old_string": "KEY=", "new_string": "KEY=abc"}),
                       ("k2", "Edit", {"file_path": r"C:\Users\dev\shop\src\pay.ts", "old_string": "a", "new_string": "b"}),
                       ("k3", "Bash", {"command": "npm test"})),
                  res("30:02", ("k1", "ok", False), ("k2", "ok", False), ("k3", "1 failed, 47 passed", True)),
                  {"type": "assistant", "timestamp": T("30:03"), "message": {"stop_reason": "end_turn", "content": [
                      {"type": "text", "text": "Wired the key in pay.ts and committed the fix."}]}}])
    r = readiness(bad)
    assert r["problems"] == ["Tests are failing", "`npm test` failed and wasn't fixed", "Changed .env, which usually holds secrets",
                             "Said it committed, but no commit ran"], r
    assert readiness_line(r) == ("! Not ready to merge: Tests are failing · `npm test` failed and wasn't fixed · Changed .env, which usually holds "
                                 "secrets · and 1 more") and readiness_line(r) in words({"summary": bad, "state": "done"})[1]
    assert why_stopped(bad) == "Said it was done, but the tests are failing" and [c["rule"] for c in claims_of(bad)] == ["commit"]
    asked = claude([recs[0], {"type": "assistant", "timestamp": T("31:00"), "message": {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": "I found two places that round. Which one should I change?"}]}}])
    assert why_stopped(asked) == "Finished with a question for you" and readiness(asked) is None and \
        words({"summary": asked, "state": "done"})[1][-1] == "Finished with a question for you", "no code changed: why it stopped instead"
    hp = claude([recs[0], tool("40:00", ("a1", "Agent", {"description": "Find the VAT code", "subagent_type": "Explore"}),
                               ("a2", "Agent", {"description": "Review the fix", "subagent_type": "general-purpose", "run_in_background": True})),
                 res("40:30", ("a1", "Found it in billing.ts", False), ("a2", "Async agent launched", False)),
                 tool("40:31", ("k1", "Skill", {"skill": "ponytail:ponytail-gain"}), ("k2", "Skill", {"skill": "loop"}), ("k3", "Skill", {"skill": "loop"}),
                      ("m1", "mcp__plugin_engineering_github__create_issue", {}), ("m2", "mcp__fec71dca-d3ec-4e60-a9c9-5e566e637d4b__firecrawl_search", {}),
                      ("m3", "mcp__ccd_session__mark_chapter", {}), ("m4", "mcp__ccd_session__start_session", {}),
                      ("m5", "mcp__ccd_turn__update_plan", {"items": [{"content": "x", "status": "in_progress"}]}))])
    assert helpers_line(hp, _ts(T("40:40"))) == "Helpers · 1 working: Explore: Find the VAT code ✓, general-purpose: Review the fix", \
        helpers_line(hp, _ts(T("40:40")))
    assert toolkit_line(hp) == "Using · skills loop, ponytail-gain (ponytail) · tools ccd_session, github (engineering), connector · 2 helper agents", \
        toolkit_line(hp)
    assert plan_line(hp) == "1/1 · x", "the app's plan tool is still the plan, not one of the tools it used"
    claude([{"type": "attachment", "timestamp": T("41:00"), "attachment": {"type": "queued_command", "prompt":
             "<task-notification>\n<task-id>x</task-id>\n<tool-use-id>a2</tool-use-id>\n<status>completed</status>\n</task-notification>"}}], hp)
    assert [x["ok"] for x in hp["steps"] if x["kind"] == "AGENT"] == [True, True] and helpers_line(hp, _ts(T("43:00"))) == "", \
        "a background helper ends with its task notification; a finished one goes after HELPERS_S"
    bgr = claude([recs[0], tool("50:00", ("g1", "Bash", {"command": "npm test", "run_in_background": True})),
                  res("50:01", ("g1", "Command running in background with ID: b1", False))])
    assert bgr["steps"][-1]["ok"] is None and not bgr["test_runs"], "a background run isn't over when its call returns"
    claude([{"type": "user", "timestamp": T("52:00"), "message": {"content": "<task-notification>\n<tool-use-id>g1</tool-use-id>\n<status>failed</status>"
                                                                          "\n</task-notification>"}}], bgr)
    assert bgr["steps"][-1]["ok"] is False and bgr["test_runs"] == [False] and bgr["asked"] == recs[0]["message"]["content"], \
        "its notification ends it, and isn't the owner asking"
    claude([{"type": "user", "timestamp": T("52:05"), "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}}], bgr)
    assert why_stopped(bgr) == "Stopped before it finished (interrupted)" and state(bgr, _ts(T("52:05")), _ts(T("52:06"))) == "idle" and \
        "Stopped before it finished (interrupted)" in words({"summary": bgr, "state": "idle"})[1]
    fv = claude([recs[0], tool("60:00", ("f1", "Read", {"file_path": r"C:\Users\dev\shop\src\cart.ts"}),
                               ("f2", "Read", {"file_path": r"C:\Users\dev\shop\src\billing.ts"})),
                 recs[3], tool("60:05", ("f3", "Edit", {"file_path": r"C:\Users\dev\shop\src\billing.ts", "old_string": "round", "new_string": "ceil"}))])
    assert dict((v, ls) for v, _, ls in steps_views(fv))["files"] == ["Changed · 1", "billing.ts  +2 −2 · edited 2×", "Only read · 1: cart.ts"], \
        steps_views(fv)
    risky = claude([recs[0], tool("70:00", ("x1", "Bash", {"command": "git push --force origin main"}))])
    assert "Worth a second look: Force-pushed to git" in words({"summary": risky, "state": "working"})[1] and \
        steps_views(risky)[-1][:2] == ["check", "Check 1"], "a flag as it happens, before the request ends"
    assert ask_flags("Bash", {"command": "git push --force origin main"}) == ["Force-pushes to git (can overwrite others' work)"]
    # Dotpals' hand-off note (Continue in, Copy) from this made-up day, and the console command that starts the next agent
    note = handoff_note("Claude Code", entries, sd, now=_ts(T("12:10")))
    assert note.startswith("# Hand-off from Claude Code · shop\n\nYou're continuing work another agent started.") and \
        "then continue: “Make the checkout button bigger”." in note, note[:300]
    assert "## The original ask\n\n> Fix the VAT rounding, then push to main\n" in note and "## The last request\n\n> Make the checkout button bigger\n" in note
    assert "## What was done\n\nChanged: `app.tsx` +1 −1\nStill working, or stopped before it ended." in note, note
    assert "- “Fix the VAT rounding, then push to main”: Fixed the VAT rounding in billing.ts: it rounded down." in note
    assert "## Files changed in this session\n\n- `src\\billing.ts`\n- `src\\app.tsx`\n" in note, note
    assert "## Tests\n\nTests passed: 48 passed; app.tsx changed since, not tested. (Only the tests the agent ran" in note
    assert "## Claude Code's last message\n\n> Fixed the VAT rounding in billing.ts: it rounded down." in note and note.endswith("really stand._\n")
    assert handoff_note("Codex", [], _new("Codex")) is None, "nothing to hand off yet"
    sd2 = claude(day[:1] + [tool("10:01", ("w1", "Write", {"file_path": r"C:\Users\dev\AppData\Local\Temp\scratch\probe.py", "content": "x"}))], claude(day))
    sd2["asked"] = 'look at this <pasted_content id="a1">LONG LOG\nmore</pasted_content> and fix it'
    n2 = handoff_note("Claude Code", [], sd2)
    assert "- and 1 file outside the project (scratch files, notes)" in n2 and "probe.py" not in n2, n2
    assert "> look at this [pasted text] and fix it" in n2 and "pasted_content" not in n2, n2
    exe, nf = r"C:\Users\me\AppData\Local\OpenAI\Codex\bin\x\codex.exe", r"E:\monday\app\data\handoff\n.md"
    assert handoff_command(exe, [handoff_prompt(nf)]) == f'cmd.exe /s /k ""{exe}" "Read {nf} and continue the work it describes.""'
    assert handoff_command(r"C:\npm\gemini.CMD", ["-i", "Read x.md"]) == 'cmd.exe /s /k ""C:\\npm\\gemini.CMD" -i "Read x.md""'
    for bad in ('a"b', "100%", "x\ny"):
        try:
            handoff_command(exe, [bad])
            raise AssertionError(bad)
        except ValueError:
            pass
    assert ask_flags("Edit", {"file_path": r"C:\shop\.env"}) == ["Changes .env, which usually holds secrets"] and \
        ask_flags("Read", {"file_path": r"C:\Users\dev\.ssh\id_rsa"}) == ["Reads id_rsa, which usually holds secrets"] and \
        ask_flags("Bash", {"command": "npm test"}) == []
    W = [(1, 100, "Claude", "claude.exe"), (2, 200, "land_intelligence.py - land prediction - Visual Studio Code", "Code.exe"),
         (3, 300, "Windows PowerShell", "WindowsTerminal.exe"), (4, 400, "Codex", "ChatGPT.exe")]  # where the owner's agents run
    P = {101: ("claude.exe", 100, "claude.exe"), 301: ("pwsh.exe", 300, "pwsh"), 302: ("claude.exe", 301, "claude"),
         303: ("node.exe", 301, r"node C:\npm\node_modules\@google\gemini-cli\dist\index.js"), 401: ("codex.exe", 400, "codex app-server")}
    F = {101: r"E:\monday", 302: r"C:\shop", 303: r"C:\docs"}.get
    assert agent_window("Claude Code", "claude-desktop", r"E:\monday", W, P, F) == (1, "Claude"), "a desktop session: the Claude app"
    assert agent_window("Claude Code", "cli", r"C:\shop", W, P, F) == (3, "Windows PowerShell"), "a CLI in its folder: up to its terminal"
    assert agent_window("Gemini CLI", None, r"C:\docs", W, P, F) == (3, "Windows PowerShell"), "Gemini CLI, through node"
    assert agent_window("Codex", "Codex Desktop", r"C:\shop", W, P, F) == (4, "Codex"), "the Codex app"
    assert agent_window("Claude Code", "claude-vscode", r"C:\x\land prediction", W, P, F)[0] == 2 and \
        agent_window("Cursor", "cursor", None, W, P, F) is None, "VS Code on its project; an editor that isn't open: nowhere to go"
    assert agent_window("Codex", "cli", r"C:\x", [], {}, F) is None, "not running: nowhere to go"
    assert agent_window("Codex", "Codex Desktop", None, W[1:2], {401: ("codex.exe", 200, "codex")}, F) is None, \
        "the Codex app closed: not the Codex running in VS Code"
    assert claude([{"type": "user", "entrypoint": "claude-desktop", "cwd": r"E:\monday", "message": {"content": "hi"}}])["entry"] == \
        "claude-desktop" and codex([{"type": "session_meta", "payload": {"cwd": r"C:\shop", "originator": "Codex Desktop"}}])["entry"] == \
        "Codex Desktop", "where each runs, from its own log"
    assert ask_words("Bash", {"command": "git push origin main\necho done", "description": "Push"}) == ("run a command", "git push origin main")
    q2 = {"questions": [{"question": "Which pills?", "header": "Pills", "multiSelect": True,
                         "options": [{"label": "Agents", "description": "yours"}, {"label": ""}, "x", {"label": "IDE"}]},
                        {"question": "  ", "options": []}, {"question": "Reload now?", "options": [{"label": "Yes"}, {"label": "No"}]}]}
    assert ask_questions("AskUserQuestion", q2) == [
        {"question": "Which pills?", "header": "Pills", "multi": True, "options": [{"label": "Agents", "description": "yours"},
                                                                                     {"label": "IDE", "description": ""}]},
        {"question": "Reload now?", "header": "", "multi": False, "options": [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]}], \
        "Claude Code's questions with their options, blanks dropped, nothing invented"
    assert ask_questions("Bash", q2) is None and ask_questions("AskUserQuestion", {"questions": "x"}) is None
    qq = ask_questions("AskUserQuestion", q2)
    assert answers_cover(qq, {"Which pills?": "Agents, IDE", "Reload now?": "Yes"}) and not answers_cover(qq, {"Which pills?": "Agents"}) \
        and not answers_cover(qq, {"Which pills?": " ", "Reload now?": "Yes"}) and not answers_cover(qq, None), "every question answered"
    assert ask_words("AskUserQuestion", q2) == ("ask you 2 questions", "Which pills?") and ask_words("AskUserQuestion", {})[0] == "ask you"
    assert ask_words("ExitPlanMode", {"plan": "# Fix the notch\n\nsteps"}) == ("start on its plan", "Fix the notch")
    assert ask_words("Edit", {"file_path": r"C:\shop\billing.ts"}) == ("edit a file", r"C:\shop\billing.ts")
    assert ask_words("mcp__github__create_issue", {"title": "x"}) == ("use create issue", '{"title": "x"}') and ask_words(None, None)[0] == "use a tool"
    c1 = claude([{"type": "assistant", "timestamp": T("12:00"), "message": {"content": [], "usage": {
        "input_tokens": 1200, "cache_creation_input_tokens": 30000, "cache_read_input_tokens": 20000, "output_tokens": 300}}}])
    assert c1["context"] == {"used": 51200, "window": None} and \
        context_meter(c1["context"]) == {"kind": "context", "label": "Context 51k tokens (its log doesn't say how many fit)", "of": None}, \
        "Claude's log doesn't say what fits: tokens in words, no bar"
    c2 = codex([{"type": "event_msg", "timestamp": T("12:00"), "payload": {"type": "token_count", "info": {
        "last_token_usage": {"total_tokens": 22189}, "model_context_window": 258400}}}])
    assert context_meter(c2["context"]) == {"kind": "context", "label": "Context 9% (22k of 258k)", "of": 0.086} and context_meter(None) is None, \
        context_meter(c2["context"])
    assert (_dur(18868), _dur(107), _dur(58)) == ("5h 14m", "1m 47s", "58s")
    import tempfile
    with tempfile.TemporaryDirectory() as d:  # the Story reads the log whole, today only (yesterday's request isn't in today's story)
        p = os.path.join(d, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in [{"type": "user", "timestamp": "2026-10-14T10:00:00Z", "message": {"content": "Old one"}}, *day]))
        got, _ = story(p, "Claude Code", now=_ts(T("12:10")))
        assert [e["asked"] for e in got] == [e["asked"] for e in entries] and got[-1].get("current"), got
        ctx = {"id": "m0", "timestamp": T("00:00"), "type": "user", "content": [{"text": "<session_context>This is the Gemini CLI.</session_context>"}]}
        gm = [{"sessionId": "x", "projectHash": "h", "startTime": T("00:00"), "kind": "main"}, {"$set": {"messages": [ctx]}},
              {"$set": {"messages": [ctx, {"id": "m1", "timestamp": T("00:01"), "type": "user", "content": [{"text": "Make the install one line"}]},
                                     {"id": "m2", "timestamp": T("00:02"), "type": "gemini", "content": "", "toolCalls": [
                                         {"id": "g1", "name": "write_todos", "status": "success", "args": {"todos": [
                                             {"description": "Edit install.md", "status": "in_progress"}, {"description": "Check it", "status": "pending"}]}},
                                         {"id": "g2", "name": "read_file", "status": "success", "args": {"absolute_path": r"E:\docs\install.md"}},
                                         {"id": "g3", "name": "replace", "status": "success", "args": {"file_path": r"E:\docs\install.md",
                                          "old_string": "## Install\nnpm install -g dotpals", "new_string": "## Install in one line\nnpx dotpals setup"}},
                                         {"id": "g4", "name": "run_shell_command", "status": "error", "args": {"command": "npm test"}}]},
                                     {"id": "m3", "timestamp": T("00:30"), "type": "gemini", "content": [{"text": "Install is one line now."}]}]}}]
        slug = os.path.join(d, "docs-slug")  # ~/.gemini/tmp/<project>/ with .project_root
        os.makedirs(os.path.join(slug, "chats"))
        with open(os.path.join(slug, ".project_root"), "w", encoding="utf-8") as f:
            f.write(r"E:\docs")
        g = _place("Gemini CLI", os.path.join(slug, "chats", "session-1.jsonl"), gemini(gm))
        assert (g["project"], g["asked"], plan_line(g), g["said"], g["took"]) == ("docs", "Make the install one line", "1/2 · Edit install.md",
                                                                                "Install is one line now.", 29.0), g
        assert g["files"] == {"install.md": [2, 2]} and len(g["read"]) == 1 and g["test_runs"] == [False] and \
            state(g, mtime=_ts(T("00:30")), now=_ts(T("00:40"))) == "done", g
        assert gemini(gm, g)["cwd"] == r"E:\docs", "where it ran carries on from read to read"
        assert gemini(gm[:2])["asked"] is None, "Gemini's own context isn't the owner asking"
    assert _mondays({"cwd": str(MONDAY_DATA / "cli-chat")}) and not _mondays({"cwd": r"E:\unispace\land prediction"}) and not _mondays({})
    with tempfile.TemporaryDirectory() as d:  # a log read as it grows, a line caught half-written, = the log read at once
        p = os.path.join(d, "s.jsonl")
        third = json.dumps(recs[2])
        with open(p, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs[:2]) + third[:20])
        got, end = read_new(p)
        s4 = claude(got)
        assert s4["tests"] is None and plan_line(s4), "the half-written result isn't read yet"
        with open(p, "a", encoding="utf-8") as f:
            f.write(third[20:] + "\n" + "".join(json.dumps(r) + "\n" for r in recs[3:]))
        got, end2 = read_new(p, end)
        assert claude(got, s4) == claude(recs) and end2 == os.path.getsize(p), "read as it grows = read at once"
    assert claude([{"type": "user", "message": {"content": "[Request interrupted by user]"}}])["asked"] is None
    assert claude([{"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued from a previous conversation"}}])["asked"] is None
    turn = [{"type": "user", "timestamp": T("10:00"), "message": {"content": "fix the tests"}},
            {"type": "assistant", "timestamp": T("10:05"), "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}}]
    again = turn + [{"type": "user", "isMeta": True, "timestamp": T("10:06"), "message": {"content": "Stop hook feedback:\nthe tests failed"}},
                    {"type": "assistant", "timestamp": T("10:07"), "message": {"stop_reason": "tool_use", "content": [
                        {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "npm test"}}]}}]
    assert claude(turn)["done_at"] and claude(again)["done_at"] is None and claude(again)["asked"] == "fix the tests" and \
        state(claude(again), _ts(T("10:07")), now=_ts(T("10:08"))) == "working", "at work again after its turn ended: working, not done"
    assert claude(again + [turn[1] | {"timestamp": T("10:09")}])["took"] == 9.0, "and done when it ends again"
    cx = [{"type": "session_meta", "timestamp": T("00:00"), "payload": {"cwd": r"E:\monday\app"}},
          {"type": "response_item", "timestamp": T("00:01"), "payload": {"type": "message", "role": "user", "content": [
              {"type": "input_text", "text": "<environment_context>…</environment_context>"}]}},
          {"type": "response_item", "timestamp": T("00:02"), "payload": {"type": "message", "role": "user", "content": [
              {"type": "input_text", "text": "add a rate limit to login"}]}},
          {"type": "response_item", "timestamp": T("00:03"), "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                                                                         "input": 'const r = await tools.exec_command({cmd:"pytest -q"})'}},
          {"type": "response_item", "timestamp": T("00:04"), "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": [
              {"type": "input_text", "text": "Script completed\nOutput:\n1 failed, Exit code: 1"}]}},
          {"type": "response_item", "timestamp": T("00:05"), "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c2", "input":
              "tools.apply_patch(`*** Begin Patch\n*** Update File: src/routes/login.ts\n-app.post('/login', login);\n+app.post('/login', limiter, login);\n*** End Patch`)"}},
          {"type": "event_msg", "timestamp": T("00:06"), "payload": {"type": "token_count", "rate_limits": {
              "primary": {"used_percent": 64.2, "window_minutes": 300}, "secondary": {"used_percent": 30.0, "window_minutes": 10080}}}},
          {"type": "event_msg", "timestamp": T("01:00"), "payload": {"type": "task_complete", "duration_ms": 58000, "last_agent_message": "Added a limiter."}}]
    c = codex(cx)
    assert c["asked"] == "add a rate limit to login" and c["project"] == "app" and c["limits"] == {"5-hour": 64, "week": 30}, c
    assert [(m["label"], m["of"]) for m in meters({"summary": c, "state": "working"}) if m["kind"] == "use"] == \
        [("Codex 5-hour 64%", 0.64), ("Codex week 30%", 0.3)], "Dotpals' limits bars, from Codex's own log"
    assert [(x["kind"], x["label"], x["ok"]) for x in c["steps"]] == [("RUN", "pytest -q", False), ("EDIT", "login.ts", None)], c["steps"]
    assert c["tests"]["ok"] is False and c["diff"]["file"] == "login.ts" and c["diff"]["added"] == 1 and c["took"] == 58.0 and c["said"] == "Added a limiter."
    # 2026-10-21, the owner: "the chatgpt and the other ides" too. Kilo Code's task as its ui_messages.json has it (a list, rewritten whole)
    K = lambda ts, typ, kind, text="": {"ts": ts * 1000, "type": typ, typ: kind, "text": text}  # noqa: E731
    tool = lambda **t: json.dumps(t)  # noqa: E731
    task = [K(100, "say", "text", "Fix the logo size"), K(101, "say", "api_req_started", "{}"),
            K(102, "ask", "tool", tool(tool="readFile", path="lib/main.dart")),
            K(103, "ask", "tool", tool(tool="appliedDiff", path="pubspec.yaml", diff="<<<<<<< SEARCH\nsize: 48\n=======\nsize: 64\n>>>>>>> REPLACE")),
            K(104, "say", "diff_error", "no match"), K(105, "ask", "command", "flutter test"), K(106, "say", "command_output", "All passed"),
            K(110, "say", "completion_result", "The logo is 64 now."), K(120, "say", "user_feedback", "and the splash too"),
            K(121, "ask", "tool", tool(tool="newFileCreated", path="lib/splash.dart", content="class Splash {}\n// done"))]
    k1 = kilo([task[:8]])
    assert (k1["agent"], k1["asked"], k1["said"], k1["done_at"], k1["took"]) == ("Kilo Code", "Fix the logo size", "The logo is 64 now.", 110, 10.0), k1
    assert [(x["kind"], x["label"], x["ok"]) for x in k1["steps"]] == [("READ", "main.dart", True), ("EDIT", "pubspec.yaml", False),
                                                                     ("RUN", "flutter test", True)], k1["steps"]
    assert (k1["diff"]["minus"], k1["diff"]["plus"]) == (["size: 48"], ["size: 64"]), "a SEARCH / REPLACE block: its lines out and in"
    k2 = kilo([task], {**k1, "cwd": r"C:\x\app", "entry": "vscode"})
    assert k2["asked"] == "and the splash too" and k2["story"][0]["asked"] == "Fix the logo size" and not k2["done_at"] \
        and k2["diff"]["plus"] == ["class Splash {}", "// done"] and (k2["cwd"], k2["entry"]) == (r"C:\x\app", "vscode"), \
        "its next request, rebuilt from the whole list, where it runs carried on"
    assert kilo([], k2) is k2 and kilo([{"not": "a list"}]) == _new("Kilo Code"), "caught mid-write: the last summary stands"
    with _tf.TemporaryDirectory() as _d:  # where a task runs and in which editor: its first request, its editor's folder
        tdir = Path(_d) / "Code" / "User" / "globalStorage" / "kilocode.kilo-code" / "tasks" / "t1"
        tdir.mkdir(parents=True)
        (tdir / "ui_messages.json").write_text(json.dumps(task[:8]), encoding="utf-8")
        (tdir / "api_conversation_history.json").write_text(json.dumps([{"role": "user", "content": [{"type": "text", "text":
            "<environment_details># Current Workspace Directory (c:/Users/x/garba/frontend) Files</environment_details>"}]}]), encoding="utf-8")
        placed = _place("Kilo Code", str(tdir / "ui_messages.json"), kilo(read_new(str(tdir / "ui_messages.json"))[0]))
        assert (placed["cwd"], placed["project"], placed["entry"]) == (os.path.normpath("c:/Users/x/garba/frontend"), "frontend", "vscode"), placed
        assert chat_turns(str(tdir / "ui_messages.json"), "Kilo Code") == [{"who": "you", "text": "Fix the logo size"},
                                                                         {"who": "Kilo Code", "text": "The logo is 64 now."}]
        quiet = Path(_d) / "Code" / "User" / "globalStorage" / "kilocode.kilo-code" / "tasks" / "t2"  # its steps run without asking: history only
        quiet.mkdir(parents=True)
        (quiet / "ui_messages.json").write_text(json.dumps([K(300, "say", "text", "Zoom the map"), K(320, "say", "completion_result", "Done.")]),
                                                encoding="utf-8")
        (quiet / "api_conversation_history.json").write_text(json.dumps([
            {"role": "user", "ts": 300_000, "content": [{"type": "text", "text": "# Current Workspace Directory (c:/x/maps) Files"}]},
            {"role": "assistant", "ts": 305_000, "content": [{"type": "tool_use", "id": "u1", "name": "apply_diff", "input": {"path": "lib/map.dart",
                                                         "diff": "<<<<<<< SEARCH\nzoom: false\n=======\nzoom: true\n>>>>>>> REPLACE"}}]},
            {"role": "user", "ts": 306_000, "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "ok"}]},
            {"role": "assistant", "ts": 310_000, "content": [{"type": "tool_use", "id": "u2", "name": "execute_command", "input": {"command": "flutter build"}}]},
            {"role": "user", "ts": 311_000, "content": [{"type": "tool_result", "tool_use_id": "u2", "is_error": True, "content": "failed"}]}]),
            encoding="utf-8")
        q = summary(str(quiet / "ui_messages.json"), {}, "Kilo Code")
        assert [(x["kind"], x["label"], x["ok"]) for x in q["steps"]] == [("EDIT", "map.dart", True), ("RUN", "flutter build", False)] \
            and (q["diff"]["minus"], q["diff"]["plus"]) == (["zoom: false"], ["zoom: true"]), \
            "steps it ran without asking aren't in its message list: from its history (2026-10-21, the owner's newest task had none listed)"
    # Kiro's own agent: one request's execution file (rewritten whole); its last edit has the whole file before and after
    before, after = "a\nb\nc\nd\ne\nf\ng\nh\n", "a\nb\nc\nD\ne\nf\ng\nh\n"
    run = {"executionId": "e1", "chatSessionId": "c1", "status": "succeed", "startTime": 200_000, "endTime": 260_000,
           "input": {"data": {"userPrompt": "Rename d", "previousMessages": []}},
           "actions": [{"actionType": "readFiles", "actionId": "1", "actionState": "Accepted", "emittedAt": 201_000, "input": {"files": [{"path": "src/x.py"}]}},
                       {"actionType": "replace", "actionId": "2", "actionState": "Accepted", "emittedAt": 230_000,
                        "input": {"file": "src/x.py", "original": "d", "modified": "D", "originalContent": before, "modifiedContent": after}},
                       {"actionType": "runCommand", "actionId": "3", "actionState": "Error", "emittedAt": 240_000, "input": {"command": "pytest"}},
                       {"actionType": "say", "actionId": "4", "actionState": "Success", "emittedAt": 250_000, "output": {"message": "Renamed it."}}]}
    r1 = kiro([run])
    assert (r1["agent"], r1["entry"], r1["asked"], r1["said"], r1["took"]) == ("Kiro", "kiro", "Rename d", "Renamed it.", 60.0), r1
    assert [(x["kind"], x["label"], x["ok"]) for x in r1["steps"]] == [("READ", "x.py", True), ("EDIT", "x.py", True), ("RUN", "pytest", False)]
    assert diff_words({"summary": r1, "state": "done"})["rows"][:5] == [["@", 1, 1, ""], [" ", 1, 1, "a"], [" ", 2, 2, "b"], [" ", 3, 3, "c"],
                                                                         ["-", 4, None, "d"]], "its last edit with real line numbers"
    assert kiro([{**run, "status": "aborted"}])["cut_at"] == 260 and not kiro([{**run, "status": "running", "endTime": None}])["done_at"]
    assert kiro([{**run, "input": {"data": {"userPrompt": "Rename d <EnvironmentContext>OS: Windows</EnvironmentContext>"}}}])["asked"] == "Rename d", \
        "the context Kiro adds to a prompt isn't the owner's words"
    # plan use (2026-10-26): the newest Codex log's own limits, as this PC's Go plan writes them (a 30-day window is a month, not a week)
    assert (window_name(300), window_name(10080), window_name(43200)) == ("5-hour", "week", "month")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "2026", "10", "21"))
        line = {"timestamp": "2026-10-21T10:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "limit_id": "codex", "primary": {"used_percent": 3.0, "window_minutes": 43200, "resets_at": 1795170812}, "secondary": None,
            "plan_type": "go"}}}
        with open(os.path.join(d, "2026", "10", "21", "rollout-a.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-10-21T09:59:00Z", "type": "event_msg", "payload": {"type": "user_message"}}) + "\n")
            fh.write(json.dumps(line) + "\n" + json.dumps({"type": "event_msg", "payload": {"type": "agent_message"}}) + "\n")
        got = plan_use(os.path.join(d, "**", "rollout-*.jsonl"))
        assert got == [{"agent": "Codex", "plan": "Go", "windows": [{"name": "month", "used": 3, "resets_at": 1795170812}],
                        "at": _ts("2026-10-21T10:00:00Z")}], got
        assert plan_use(os.path.join(d, "none", "*.jsonl")) == [], "no log: nothing, never a guess"
    print("watch self-check: OK")
