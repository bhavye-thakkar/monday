"""Claude Code's PermissionRequest hook, for the owner's own Claude Code (2026-10-16, the owner: "set up approving my Claude Code's
prompts from the notch (Ctrl+Alt+Y / Ctrl+Alt+N)"). When Claude Code is about to ask the owner for permission, it runs this: the
request goes to Monday (POST /api/agents/ask), the notch (or Monday's window, when open) shows it, and the owner's Allow / Deny there,
or Ctrl+Alt+Y / Ctrl+Alt+N from any app, is Claude Code's answer. Claude Code's own prompt stays up meanwhile, and whichever answers
first decides.

It never allows anything by itself: with Monday not running, or no answer in WAIT_S, it prints nothing and Claude Code asks as it always
did. Standard library only (Claude Code runs it with the system Python, windowless); nothing is written anywhere.

Only the desktop app's sessions (HOST): the docs promise the app's own prompt runs alongside the hook there. Not Monday's own Claude
Code workers (headless; they already ask through Monday's approvals, so this would ask twice), other headless runs, or the terminal,
where Claude Code's prompt may wait for the hook.

    "C:\\Program Files\\Python314\\pythonw.exe" E:\\monday\\app\\core\\approve_hook.py < event.json   (Claude Code does this)
    python core/approve_hook.py --check                                                           (no Monday needed)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('MONDAY_PORT', '8765')}"
WAIT_S = 110   # the owner's answer, at most this long (the hook's timeout in settings.json is 120 s); then Claude Code's prompt alone
POLL_S = 25    # one wait on Monday (it answers at once when the owner does)
HOST = "claude-desktop"  # CLAUDE_CODE_ENTRYPOINT of the Claude desktop app's sessions (its hooks inherit it)


def ours(env):
    return env.get("CLAUDE_CODE_ENTRYPOINT") == HOST


def call(method, path, body=None, timeout=5.0, base=None):
    req = urllib.request.Request((base or BASE) + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def decision_json(decision, answers=None, tool_input=None):
    """What Claude Code reads: allow, deny (with why, for Claude), or None: nothing printed, so its own prompt decides. A question of its
    (AskUserQuestion) allowed with the owner's picks: its input with them as "answers" (question → pick), so the app shows it answered."""
    if decision == "allow" and answers:
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow", "updatedInput": {**(tool_input or {}), "answers": answers}}}}
    if decision == "allow":
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}
    if decision == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "deny", "message": "The owner denied this in Monday's notch."}}}
    return None


def ask(event, wait_s=WAIT_S, base=None):
    """Claude Code's event -> (the owner's "allow" / "deny" through Monday, their answers to its questions or None), or (None, None):
    Monday not there, or no answer in time."""
    try:
        aid = call("POST", "/api/agents/ask", {"tool_name": event.get("tool_name") or "", "tool_input": event.get("tool_input") or {},
                                               "cwd": event.get("cwd") or "", "session_id": event.get("session_id") or "",
                                               "wait_s": wait_s},  # the notch counts it down (Dotpals' "29s")
                   timeout=2.0, base=base)["id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        wait = max(1, min(POLL_S, int(deadline - time.monotonic())))
        try:
            r = call("GET", f"/api/agents/ask/{aid}?wait={wait}", timeout=wait + 5.0, base=base)
            d, answers = r.get("decision"), r.get("answers")
        except (OSError, ValueError, AttributeError):
            return None, None
        if d in ("allow", "deny"):
            return d, answers if isinstance(answers, dict) else None
        if d == "gone":
            return None, None
    try:  # no answer: the notch lets it go, and Claude Code's own prompt is the one to answer
        call("POST", f"/api/agents/ask/{aid}", {"decision": "gone"}, timeout=2.0, base=base)
    except (OSError, ValueError):
        pass
    return None, None


def read_event(stdin=None):
    """Claude Code's event: the JSON it writes to the hook, in UTF-8, read as bytes and decoded here. Windows' Python reads a pipe in the
    ANSI code page (cp1252 on this PC), so every "·", "—", "…" or quote of a request or a question reached the notch garbled ("Â·",
    "â€”"; the owner, 2026-10-20: "sometimes i see a caped a"), and its answer went back keyed by the garbled words. None: not JSON."""
    stdin = stdin or sys.stdin
    try:
        raw = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read()
        return json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except ValueError:
        return None


def main():
    event = read_event()
    if not ours(os.environ) or not isinstance(event, dict) or event.get("hook_event_name") not in (None, "PermissionRequest"):
        return
    decision, answers = ask(event)
    out = decision_json(decision, answers, event.get("tool_input") if isinstance(event.get("tool_input"), dict) else None)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    if "--check" in sys.argv:
        allow, deny = decision_json("allow"), decision_json("deny")
        assert allow["hookSpecificOutput"] == {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}
        assert deny["hookSpecificOutput"]["decision"]["behavior"] == "deny" and deny["hookSpecificOutput"]["decision"]["message"]
        assert decision_json(None) is None and decision_json("gone") is None, "no answer: nothing printed, Claude Code asks itself"
        import io
        said = {"tool_input": {"questions": [{"question": "Claude Code · voice — Open ↗ “Other…”?"}]}}
        pipe = lambda b: io.TextIOWrapper(io.BytesIO(b), encoding="cp1252")  # noqa: E731  (a pipe as Windows' Python opens it here)
        assert read_event(pipe(json.dumps(said, ensure_ascii=False).encode("utf-8"))) == said, "Claude Code's UTF-8, word for word"
        assert read_event(pipe(b"not json")) is None and read_event(pipe(b"")) is None
        qin = {"questions": [{"question": "Reload now?", "options": [{"label": "Yes"}, {"label": "No"}]}]}
        got = decision_json("allow", {"Reload now?": "Yes"}, qin)["hookSpecificOutput"]["decision"]
        assert got == {"behavior": "allow", "updatedInput": {**qin, "answers": {"Reload now?": "Yes"}}}, "its question answered with the pick"
        assert decision_json("deny", {"Reload now?": "Yes"}, qin)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
        assert ours({"CLAUDE_CODE_ENTRYPOINT": "claude-desktop"}), "the desktop app's sessions ask through Monday"
        assert not any(ours(e) for e in ({}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, {"CLAUDE_CODE_ENTRYPOINT": "cli"})), \
            "Monday's own workers (headless, they ask through Monday already) and the terminal don't"
        t0 = time.monotonic()
        assert ask({"tool_name": "Bash", "tool_input": {"command": "git push"}}, base="http://127.0.0.1:9") == (None, None), "Monday not there"
        assert time.monotonic() - t0 < 5, "and it doesn't keep Claude Code waiting"
        print("approve hook self-check: OK")
        sys.exit(0)
    try:
        main()
    except Exception:  # never in Claude Code's way: its own prompt then
        pass
    sys.exit(0)
