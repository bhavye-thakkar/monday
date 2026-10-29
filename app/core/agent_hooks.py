"""Claude Code's hooks and an MCP tool that ask Monday about the work (2026-10-17, the owner OK'd all four: "Fix loop on Stop, Commit/push
gate, Two-agents guard, MCP 'check my work'"), after Dotpals' fix loop, gate and claim check. Monday reads the session's own log
(core/watch.py, core/evidence.py) and answers; this only passes Claude Code's event on and prints Monday's answer back:
- stop: the Stop hook. When the tests failed after the session's change, or it says they pass and its log doesn't show that, Monday sends
  it back to work with why (decision "block"), at most watch.FIX_TRIES times a request (Monday.agent_stop).
- pre: the PreToolUse hook. `git commit` / `git push`: denied while the tests fail after the change, asked first when no passing run
  came after it (the gate). An edit of a file another of the owner's sessions edited in the last watch.GUARD_S: asked first (two agents).
- mcp: a stdio MCP server with one tool, check_my_work: Monday runs the project's tests and says what they show (Monday.check_work).
- Both hooks also hand over what the owner wrote to the session in Monday's notch ("Write to", Monday.say_to_agent): PreToolUse as added
  context before its next command or edit, Stop as the reason to go on when its step ends. Once each, and only the owner's words.
- wait: the wake hook (2026-10-22, the owner: "when im writing something from the notch ... it isnt comming here in the chat"), a Stop
  hook Claude Code runs in the background (asyncRewake): once a turn ends it waits for the owner's words for that session and hands them
  over by exiting 2, which wakes the session with them (WAKE_HOOK, the entry for ~/.claude/settings.json).
- statusline: Claude Code's status line (2026-10-29, the owner's brief: "all supported AI usage limits from the notch"; docs/adr/0011).
  Claude Code hands its status line command the plan's limits (rate_limits: five_hour / seven_day, used_percentage and resets_at, for
  claude.ai Pro and Max, code.claude.com/docs/en/statusline): this keeps them in data/claude-limits.json for the notch's usage cards
  (core/usage.py), then runs the owner's own status line command (the words after `--`) with the same input and prints what it prints.
  Put in ~/.claude/settings.json only with the owner's OK (STATUSLINE_CMD); until then the Claude Code card says it has no source.
It is all advice: with Monday not running, an old Monday, or any error, it prints nothing and Claude Code goes on as it would. Monday's own
workers (MONDAY_WORKER, probes.worker_env) are left alone: Monday checks their work itself (Monday.check_code). Standard library only.

    "C:\\Program Files\\Python314\\pythonw.exe" E:\\monday\\app\\core\\agent_hooks.py stop|pre|wait < event.json   (Claude Code does this)
    "C:\\Program Files\\Python314\\pythonw.exe" E:\\monday\\app\\core\\agent_hooks.py mcp                      (~/.claude.json mcpServers)
    ...pythonw.exe E:\\monday\\app\\core\\agent_hooks.py statusline -- <the owner's own status line command>        (statusLine)
    python core/agent_hooks.py --check                                                                       (no Monday needed)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = f"http://127.0.0.1:{os.environ.get('MONDAY_PORT', '8765')}"
NOTES = Path(__file__).resolve().parent.parent / "data" / f"agent_notes-{os.environ.get('MONDAY_PORT', '8765')}.json"  # that Core's list of
# logs with the owner's words waiting (Monday.say_to_agent)
GATED = re.compile(r"\bgit\b(?:\s+-{1,2}[\w-]+(?:[= ]\S+)?)*\s+(?:commit|push)\b")  # "git commit", "git -C repo push", not "git log"
EDITS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
CHECK_S = 900  # check_my_work: a project's tests may take this long
WAIT_S, BEAT_S, LOOK_S = 12 * 3600.0, 20.0, 1.5  # the wake hook waits this long after a turn ends, tells Core it is there every BEAT_S
# (Monday.agent_waiting) and looks for the owner's words every LOOK_S (Core's list, a file: waiting)
AWAY_S = 120.0  # ...and goes once Monday hasn't answered this long (a restart takes ~10 s): with Monday off, one waits per turn ended
WAKE_HOOK = {"type": "command", "command": "C:\\Program Files\\Python314\\pythonw.exe", "args": ["E:\\monday\\app\\core\\agent_hooks.py", "wait"],
             "asyncRewake": True, "timeout": int(WAIT_S) + 600, "rewakeMessage": "Monday:",
             "rewakeSummary": "Monday: your words from the notch"}  # its Stop entry in ~/.claude/settings.json (the owner's to add)
TOOL = {"name": "check_my_work",
        "description": "Ask Monday (the owner's assistant on this PC) to run this project's own tests and say what their output shows: "
                       "passed, failed (which, and why) or unclear. Use it before saying the work is done or that tests pass.",
        "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string", "description": "The project's folder; "
                                                                         "default: the folder Claude Code runs in."}}}}


def call(path, body, timeout=10.0, base=None):
    req = urllib.request.Request((base or BASE) + path, method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def ask(path, body, timeout=10.0, base=None):
    """Monday's answer, or {} when it isn't there to give one (not running, an older Monday without the route, anything else)."""
    try:
        return call(path, body, timeout, base) or {}
    except (OSError, ValueError):
        return {}


def waiting(path):
    """The owner wrote to this session in Monday's notch and the words wait (Core's list, read without asking Core: a refused connection
    costs Windows 2 s, too much before every command of every session while Monday is off)."""
    try:
        return os.path.normcase(os.path.normpath(str(path or ""))) in json.loads(NOTES.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False


def stop(event, base=None):
    """The Stop hook's event -> {"decision": "block", "reason"} to send Claude Code back, or None."""
    r = ask("/api/agents/stop", {"transcript_path": event.get("transcript_path") or "", "session_id": event.get("session_id") or "",
                                 "stop_hook_active": bool(event.get("stop_hook_active"))}, 20.0, base)
    return {"decision": "block", "reason": r["reason"]} if r.get("reason") else None


def pre(event, base=None):
    """The PreToolUse hook's event -> Claude Code's permissionDecision (deny / ask, with why), or None: nothing printed, as before."""
    tool, inp = event.get("tool_name") or "", event.get("tool_input") or {}
    body = {"transcript_path": event.get("transcript_path") or "", "session_id": event.get("session_id") or ""}
    if tool in ("Bash", "PowerShell") and GATED.search(str(inp.get("command") or "")):
        r = ask("/api/agents/gate", {**body, "command": str(inp.get("command"))}, 10.0, base)
    elif tool in EDITS:
        r = ask("/api/agents/guard", {**body, "file_path": str(inp.get("file_path") or inp.get("notebook_path") or "")}, 5.0, base)
    elif tool in ("Bash", "PowerShell"):
        r = {}
    else:
        return None
    out = {"hookEventName": "PreToolUse"}
    if r.get("decision") in ("deny", "ask"):
        out |= {"permissionDecision": r["decision"], "permissionDecisionReason": r["reason"]}
    note = ask("/api/agents/notes", body, 5.0, base).get("note") if waiting(body["transcript_path"]) else None  # the owner's, from the notch
    if note:
        out["additionalContext"] = note
    return {"hookSpecificOutput": out} if len(out) > 1 else None


def alive(pid):
    """Whether the process that started this one (Claude Code) still runs: once it's gone there is nobody left to wake."""
    if not pid:
        return True
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    try:
        return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def wait(event, base=None, until=WAIT_S, parent=None, look=LOOK_S, beat=BEAT_S, away=AWAY_S):
    """The wake hook's event (a turn ended) -> the owner's words for that session once they come (main prints them and exits 2: Claude
    Code wakes it with them), or None: a newer wait for the session took over (a later turn ended), Claude Code is gone, Monday hasn't
    answered for `away`, or `until` passed. Meanwhile Monday is told every `beat` that it listens, so Write to keeps the words for it
    though it isn't working."""
    body = {"transcript_path": event.get("transcript_path") or "", "session_id": event.get("session_id") or "", "pid": os.getpid(),
            "since": time.time()}
    parent = os.getppid() if parent is None else parent
    end, told, lost = time.monotonic() + until, 0.0, None
    while time.monotonic() < end and alive(parent):
        now = time.monotonic()
        if now >= told:
            r = ask("/api/agents/waiting", body, 5.0, base)
            if r.get("stop"):
                return None
            lost = None if r else lost or now
            if lost is not None and now - lost >= away:
                return None
            told = time.monotonic() + (beat if r else min(beat, 5.0))
        if waiting(body["transcript_path"]):
            note = ask("/api/agents/notes", body, 5.0, base).get("note")
            if note:
                return note
        time.sleep(look)
    return None


def mcp_answer(msg, base=None):
    """One JSON-RPC message from Claude Code -> the answer to write back, or None (a notification)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "monday", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call" and params.get("name") == TOOL["name"]:
        where = (params.get("arguments") or {}).get("project_dir") or os.getcwd()
        r = ask("/api/agents/check", {"cwd": where}, CHECK_S, base)
        text = r.get("text") or "Monday isn't answering: is it running? (Start it from the Start menu.)"
        result = {"content": [{"type": "text", "text": text}], "isError": not r}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Monday has no {method}."}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def read_event(stdin=None):
    """Claude Code's event, its UTF-8 read as bytes (approve_hook.read_event's way: Windows' Python reads a pipe in cp1252 here, and
    "·" came out "Â·"). Each hook runs alone, so it is here too. None: not JSON."""
    stdin = stdin or sys.stdin
    try:
        raw = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read()
        return json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except ValueError:
        return None


def mcp(stdin=sys.stdin, stdout=sys.stdout, base=None):
    """The MCP server over stdio: one JSON-RPC message per line (UTF-8, its bytes: read_event), until Claude Code closes it."""
    for line in getattr(stdin, "buffer", stdin):
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = mcp_answer(msg, base) if isinstance(msg, dict) else None
        if out:
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()


CLAUDE_LIMITS = Path(__file__).resolve().parent.parent / "data" / "claude-limits.json"  # Claude Code's plan limits as its status line got
# them (statusline); core/usage.py reads them
CHAIN_S = 3.0  # the owner's own status line command is given this long


def statusline(raw, chain=(), path=CLAUDE_LIMITS, run=subprocess.run):
    """Claude Code's status line input (bytes): its rate_limits kept (only the windows it gives, with when they reset, and when it said
    them), then the owner's own status line command run with the same input -> what it printed (bytes), or b"" (none, or it failed)."""
    try:
        event = json.loads(raw.decode("utf-8", "replace"))
        rl = event.get("rate_limits") if isinstance(event, dict) else None
        wins = {k: {"used_percentage": float(w["used_percentage"]), "resets_at": w.get("resets_at")}
                for k, w in (rl or {}).items() if k in ("five_hour", "seven_day") and isinstance(w, dict) and w.get("used_percentage") is not None}
        if wins:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**wins, "at": time.time(), "version": event.get("version")}), encoding="utf-8")
            os.replace(tmp, path)  # (whole or not at all: the notch never reads half a file)
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        pass
    if not chain:
        return b""
    try:
        return run(list(chain), input=raw, capture_output=True, timeout=CHAIN_S,  # (no console window of its own: a refresh never flashes one)
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or b""
    except (OSError, subprocess.SubprocessError, ValueError):
        return b""


def installed(home=None):
    """Which of Monday's five are in the owner's Claude Code, read only (the notch's Home card, 2026-10-17): {"approvals" (the
    PermissionRequest hook, core/approve_hook.py), "stop", "pre", "mcp", "wake" (2026-10-22: a Stop hook that wakes, WAKE_HOOK)} each
    True or False; {"error": why} when its settings can't be read. Nothing here changes them: that is the owner's to do."""
    from pathlib import Path
    home = Path(home or os.path.expanduser("~"))

    def runs(hook, script, mode=None):
        words = " ".join([str(hook.get("command") or "")] + [str(a) for a in hook.get("args") or []])
        return script in words and (mode is None or re.search(rf"(?<![\w-]){mode}(?![\w-])", words) is not None)

    def read(p):
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    try:
        hooks = read(home / ".claude" / "settings.json").get("hooks") or {}
        has = lambda event, script, mode=None: any(runs(x, script, mode) for g in hooks.get(event) or [] if isinstance(g, dict)  # noqa: E731
                                                   for x in g.get("hooks") or [] if isinstance(x, dict))
        mcp_monday = (read(home / ".claude.json").get("mcpServers") or {}).get("monday")
        wakes = any(runs(x, "agent_hooks.py", "wait") and x.get("asyncRewake") is True for g in hooks.get("Stop") or [] if isinstance(g, dict)
                    for x in g.get("hooks") or [] if isinstance(x, dict))  # (without asyncRewake it would hold every turn's end)
        return {"approvals": has("PermissionRequest", "approve_hook.py"), "stop": has("Stop", "agent_hooks.py", "stop"),
                "pre": has("PreToolUse", "agent_hooks.py", "pre"), "mcp": isinstance(mcp_monday, dict) and runs(mcp_monday, "agent_hooks.py", "mcp"),
                "wake": wakes}
    except (OSError, ValueError, AttributeError, TypeError) as e:
        return {"error": f"Couldn't read Claude Code's settings ({type(e).__name__})"}


def main(mode):
    """-> the exit code: 2 when the wake hook hands the owner's words over (Claude Code then wakes the session with what it wrote)."""
    if os.environ.get("MONDAY_WORKER"):
        return 0  # one of Monday's own workers: Monday checks its work itself
    if mode == "mcp":
        mcp()
        return 0
    if mode == "statusline":  # (its input as bytes, untouched, for the owner's own command after "--")
        args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        out = statusline(sys.stdin.buffer.read(), args)
        if out and sys.stdout:
            sys.stdout.buffer.write(out)
            sys.stdout.flush()
        return 0
    event = read_event()
    if isinstance(event, dict) and mode == "wait":
        note, out = wait(event), sys.stderr or sys.stdout
        if note and out:
            out.buffer.write(note.encode("utf-8"))  # (its bytes: a pipe is cp1252 to Windows' Python here, Claude Code reads UTF-8)
            out.flush()
            return 2
        return 0
    out = (stop if mode == "stop" else pre if mode == "pre" else lambda e: None)(event) if isinstance(event, dict) else None
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        import io
        import shutil
        import tempfile
        import time
        assert all(GATED.search(c) for c in ("git commit -m x", "git push origin main", "git -C repo push", "cd x && git commit --amend"))
        assert not any(GATED.search(c) for c in ("git log --oneline", "git status", "echo commit", "git committed", "npm push"))
        t0 = time.monotonic()
        nowhere = "http://127.0.0.1:9"
        assert stop({"transcript_path": "x"}, base=nowhere) is None, "Monday not there: it stops as it would"
        assert pre({"tool_name": "Bash", "tool_input": {"command": "git push"}}, base=nowhere) is None
        assert pre({"tool_name": "Edit", "tool_input": {"file_path": "C:\\x.py"}}, base=nowhere) is None
        assert pre({"tool_name": "Read", "tool_input": {}}, base=nowhere) is None and time.monotonic() - t0 < 10, "and keeps nothing waiting"
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        said = {"/api/agents/notes": {"note": "MARK\nuse port 9000"}, "/api/agents/guard": {"decision": "ask", "reason": "two agents"}}

        class Fake(BaseHTTPRequestHandler):  # a Monday that has the owner's words waiting
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = json.dumps(said.get(self.path, {})).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        flag = NOTES
        NOTES = Path(tempfile.mkdtemp(prefix="monday-notes-")) / "agent_notes.json"
        log = "C:\\u\\.claude\\projects\\p\\s.jsonl"
        assert pre({"tool_name": "Bash", "tool_input": {"command": "npm test"}, "transcript_path": log}, base=nowhere) is None, "no list: no ask"
        NOTES.write_text(json.dumps([os.path.normcase(log)]), encoding="utf-8")
        srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        fake = f"http://127.0.0.1:{srv.server_port}"
        try:
            assert pre({"tool_name": "Bash", "tool_input": {"command": "npm test"}, "transcript_path": log.upper()}, base=fake) == {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": "MARK\nuse port 9000"}}, "the owner's words before its next command"
            assert pre({"tool_name": "Edit", "tool_input": {"file_path": "C:\\x.py"}, "transcript_path": log}, base=fake) == {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "two agents",
                "additionalContext": "MARK\nuse port 9000"}}, "with the guard's question"
            assert pre({"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": "other.jsonl"}, base=fake) is None, \
                "another session's words: not asked for"
            said["/api/agents/waiting"] = {"stop": False}
            t0 = time.monotonic()
            assert wait({"transcript_path": log}, base=fake, parent=0, look=0.05) == "MARK\nuse port 9000" and time.monotonic() - t0 < 5, \
                "the wake hook: its session's words as soon as Core lists them"
            assert wait({"transcript_path": "other.jsonl"}, base=fake, parent=0, until=0.3, look=0.05) is None, "another session's: none"
            assert wait({"transcript_path": log}, base=nowhere, parent=0, until=0.3, look=0.05) is None, "Monday away: none, and it ends"
            t0 = time.monotonic()
            assert wait({"transcript_path": log}, base=nowhere, parent=0, look=0.05, beat=0.1, away=0.3) is None and time.monotonic() - t0 < 15, \
                "Monday away for `away`: it goes, not left waiting for hours (one per turn ended)"
            said["/api/agents/waiting"] = {"stop": True}
            assert wait({"transcript_path": log}, base=fake, parent=0, look=0.05) is None, "a newer wait for the session took over: this one goes"
            said["/api/agents/waiting"] = {"stop": False}
            import subprocess
            gone = subprocess.Popen([sys.executable, "-c", "pass"])
            gone.wait()
            assert alive(os.getpid()) and not alive(gone.pid) and wait({"transcript_path": log}, base=fake, parent=gone.pid) is None, \
                "its Claude Code gone: nobody left to wake"
            was = (sys.stdin, sys.stderr, BASE)
            sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps({"transcript_path": log}).encode()), encoding="cp1252")
            sys.stderr, BASE = io.TextIOWrapper(io.BytesIO(), encoding="cp1252"), fake
            said["/api/agents/notes"] = {"note": "MARK\nnaïve “café” ↗"}
            try:
                code = main("wait")
                sys.stderr.flush()
                woke = sys.stderr.buffer.getvalue()
            finally:
                sys.stdin, sys.stderr, BASE = was
            assert code == 2 and woke.decode("utf-8") == "MARK\nnaïve “café” ↗", ("exit 2 with the words, UTF-8: Claude Code wakes the session", code, woke)
            said.pop("/api/agents/notes")
            assert pre({"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": log}, base=fake) is None, "nothing came: nothing printed"
            assert pre({"tool_name": "Read", "tool_input": {}}, base=fake) is None, "a tool Monday's matcher doesn't name: nothing asked"
        finally:
            srv.shutdown()
            shutil.rmtree(NOTES.parent, ignore_errors=True)
            NOTES = flag
        out = io.StringIO()
        mcp(io.StringIO("\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "check_my_work", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})) + "\nnot json\n"), out, base=nowhere)
        got = [json.loads(x) for x in out.getvalue().splitlines()]
        assert [g["id"] for g in got] == [1, 2, 3, 4], "a notification gets no answer, nor does a line that isn't JSON"
        assert got[0]["result"]["serverInfo"]["name"] == "monday" and got[1]["result"]["tools"][0]["name"] == "check_my_work"
        assert got[2]["result"]["isError"] and "isn't answering" in got[2]["result"]["content"][0]["text"] and got[3]["error"]["code"] == -32601
        pipe = lambda b: io.TextIOWrapper(io.BytesIO(b), encoding="cp1252")  # noqa: E731  (a pipe as Windows' Python opens it here)
        said = {"tool_input": {"command": "echo “Claude Code · voice — ok…”", "file_path": "E:\\café\\naïve.py"}}
        assert read_event(pipe(json.dumps(said, ensure_ascii=False).encode("utf-8"))) == said, "Claude Code's UTF-8, word for word"
        assert read_event(pipe(b"{broken")) is None
        out = io.StringIO()
        mcp(pipe(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {"x": "· — ↗"}}, ensure_ascii=False).encode() + b"\n"), out)
        assert json.loads(out.getvalue())["id"] == 9, "the MCP server reads its lines as bytes too"
        home = Path(tempfile.mkdtemp(prefix="monday-hooks-"))
        try:  # the notch's Home card: which of Monday's five the owner's Claude Code has, read only
            assert installed(home) == {"approvals": False, "stop": False, "pre": False, "mcp": False, "wake": False}, "no settings: none installed"
            exe = "C:\\Program Files\\Python314\\pythonw.exe"
            (home / ".claude").mkdir()
            stops = [{"hooks": [{"type": "command", "command": "[ -f x ] || impeccable stop-hook"}]},
                     {"hooks": [{"type": "command", "command": exe, "args": ["E:\\monday\\app\\core\\agent_hooks.py", "stop"]}]},
                     {"hooks": [{**WAKE_HOOK, "asyncRewake": False}]}]
            (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
                "PermissionRequest": [{"matcher": "*", "hooks": [{"type": "command", "command": exe, "args": ["E:\\monday\\app\\core\\approve_hook.py"]}]}],
                "Stop": stops,
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f'"{exe}" E:\\monday\\app\\core\\agent_hooks.py pre'}]}]}}))
            assert installed(home) == {"approvals": True, "stop": True, "pre": True, "mcp": False, "wake": False}, \
                ("the wait without asyncRewake would hold every turn's end: not the wake hook", installed(home))
            stops[2] = {"hooks": [WAKE_HOOK]}
            (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": stops}}))
            assert installed(home)["wake"] and installed(home)["stop"] and not installed(home)["pre"], installed(home)
            (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
                "PermissionRequest": [{"matcher": "*", "hooks": [{"type": "command", "command": exe, "args": ["E:\\monday\\app\\core\\approve_hook.py"]}]}],
                "Stop": stops[2:],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f'"{exe}" E:\\monday\\app\\core\\agent_hooks.py pre'}]}]}}))
            assert installed(home)["wake"] and not installed(home)["stop"], "the wake hook isn't the fix loop's Stop hook"
            (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
                "PermissionRequest": [{"matcher": "*", "hooks": [{"type": "command", "command": exe, "args": ["E:\\monday\\app\\core\\approve_hook.py"]}]}],
                "Stop": stops,
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f'"{exe}" E:\\monday\\app\\core\\agent_hooks.py pre'}]}]}}))
            (home / ".claude.json").write_text(json.dumps({"mcpServers": {"monday": {"command": exe, "args": ["E:\\monday\\app\\core\\agent_hooks.py", "mcp"]}}}))
            assert all(installed(home).values()), ("all five: the command's words or its args", installed(home))
            (home / ".claude.json").write_text("{not json")
            assert "error" in installed(home), "unreadable: said so (the notch shows N/A), never a guess"
        finally:
            shutil.rmtree(home, ignore_errors=True)
        # the status line relay: Claude Code's limits kept (only what it gave), the owner's own command run with the same input
        tmpd = Path(tempfile.mkdtemp())
        try:
            f = tmpd / "claude-limits.json"
            ran = []

            def fake_run(cmd, input=None, capture_output=None, timeout=None, creationflags=0):
                ran.append((cmd, input))
                assert creationflags == getattr(subprocess, "CREATE_NO_WINDOW", 0), "the owner's command gets no console window of its own"
                return type("R", (), {"stdout": b"[PONYTAIL]"})()
            ev = json.dumps({"version": "2.1.309", "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
                                                                  "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}}).encode()
            assert statusline(ev, ["powershell", "-File", "x.ps1"], f, fake_run) == b"[PONYTAIL]", "the owner's own line printed as it was"
            assert ran == [(["powershell", "-File", "x.ps1"], ev)], "...run with the same input, once"
            kept = json.loads(f.read_text(encoding="utf-8"))
            assert kept["five_hour"] == {"used_percentage": 23.5, "resets_at": 1738425600} and kept["seven_day"]["used_percentage"] == 41.2
            assert kept["version"] == "2.1.309" and abs(kept["at"] - time.time()) < 60
            f.unlink()
            assert statusline(json.dumps({"model": {"id": "x"}}).encode(), [], f) == b"" and not f.exists(), \
                "no rate_limits (an API key, before the first answer): nothing kept, nothing made up"
            assert statusline(b"{not json", ["x"], f, fake_run) == b"[PONYTAIL]" and not f.exists(), "bad input: the owner's line still runs"
            boom = lambda *a, **k: (_ for _ in ()).throw(OSError("gone"))  # noqa: E731
            assert statusline(ev, ["missing.exe"], f, boom) == b"", "the owner's command failing: an empty line, never an error"
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        print("agent hooks self-check: OK")
        sys.exit(0)
    try:
        code = main(sys.argv[1] if len(sys.argv) > 1 else "")
    except Exception:  # never in Claude Code's way
        code = 0
    sys.exit(code)
