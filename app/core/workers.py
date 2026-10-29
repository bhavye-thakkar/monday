"""Worker adapters: each drives a real agent process and reports only what it observes.

- Claude Code: `claude -p` in stream-json mode; per-tool permission prompts are relayed to the owner
  over the SDK control protocol (can_use_tool).
- Codex: `codex exec --json` confined to the project by its workspace-write sandbox. exec mode cannot
  relay approvals, so Monday asks once before launch (see monday.py).
- Hermes: `hermes acp` (Agent Client Protocol over stdio); dangerous-command approvals arrive as
  session/request_permission and are relayed to the owner.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import evidence, probes
from .agents import EDITORS, HANDOFF, HEADLESS

STREAM_LIMIT = 1 << 24  # tool results can be large single JSON lines
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# An ACP agent (Hermes, a custom engine) that says nothing this long mid-turn (no message, no tool update, no approval pending) is stuck,
# and nothing else would end the task: 2026-10-16 Hermes' own browser_exec sat 94 s on one image search, the card "Working…" all along.
ACP_IDLE_S = 180
ACP_TICK_S = 2.0
# ...and one whole turn, however talkative, ends here: ACP_IDLE_S bounds its silence, not its length (2026-10-27: a Hermes loop that keeps
# clicking is never "quiet"). Stopped the same way, FAILED "ran for 30 min"; the owner asks again or says what to do differently.
ACP_MAX_S = 1800
# Hermes' own browser tools (browser_navigate, browser_click, browser_exec…) drive a separate, hidden Chromium of Hermes' own. A desktop
# request is the owner's real desktop and their own browser (2026-10-20: "it must not simply open YouTube inside Monday's own application"),
# so a Hermes task that reaches for one of these is stopped and FAILS in words; it never runs on quietly in a browser nobody can see.
HERMES_OWN_BROWSER = re.compile(r"^browser_\w+", re.I)
HERMES_OFFLINE_MODEL = "hermes-qwen3-4b:latest"  # Hermes' own tuned variant on this PC: its sessions' model while Monday is offline
WORKER_NOTE = ("You are being driven by Monday, the owner's local orchestrator, from their desktop. "
               "Work inside the current project. Report concretely what you changed and how you verified it.")
# What two tasks can't hold at once (Monday.lease, monday.py: the second waits, saying who has it): the screen (one cursor, one window in
# front: Hermes, Monday's own hands, an editor's own agent working in its window) or a project's files (a coding agent, by its folder).
# An agent with neither (chat, research) shares nothing and runs alongside anything. 2026-10-27: TASK-0078/79/80, three screen loops at
# once on one screen, "control not identifiable". ponytail: one lease per resource, no queue order or priority beyond who asked first.
RESOURCE = {"hermes": "screen", "computer": "screen", **{k: "screen" for k in HANDOFF}, **{k: "project" for k in HEADLESS}}


def resource_of(agent, project_path=None, engines=None):
    """The resource a task of `agent` holds while it runs: "screen", "project:<its folder>" or None (nothing shared). An engine from
    data/engines.json (core/engines.py load's entries): the screen when its entry has Monday's desktop-control MCP server (mcp), else its
    project when it codes (coding), else nothing."""
    r = RESOURCE.get(agent)
    if r is None and (engines or {}).get(agent):
        e = engines[agent]
        r = "screen" if e.get("mcp") else "project" if e.get("coding") else None
    if r == "project":
        return f"project:{os.path.normcase(os.path.abspath(project_path))}" if project_path else None
    return r


class WorkerError(Exception):
    pass


async def spawn(args, cwd, env=None, limit=STREAM_LIMIT):
    return await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env or probes.worker_env(), limit=limit,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW)


def kill_tree(proc):
    """Agents spawn shells and node processes; stop the whole tree, not just the parent."""
    if proc is None or proc.returncode is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()


async def _drain(stream, sink, limit=4000):
    """Keep the tail of stderr for error reports (and keep the pipe from filling up)."""
    while True:
        line = await stream.readline()
        if not line:
            return
        sink.append(line.decode("utf-8", "replace"))
        while sum(map(len, sink)) > limit:
            sink.pop(0)


def _brief(name, inp):
    inp = inp or {}
    for key in ("command", "file_path", "path", "notebook_path", "pattern", "url", "query", "description"):
        if inp.get(key):
            return f"{name}: {str(inp[key])[:160]}"
    return name


def _under(path, root):
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def _text(content):
    """A tool result's text: a string, or a list of text blocks (Claude Code, ACP)."""
    if isinstance(content, str):
        return content
    return "\n".join(str(b.get("text") or (b.get("content") or {}).get("text") or "") for b in content or [] if isinstance(b, dict)) \
        if isinstance(content, list) else json.dumps(content) if content else ""


def _edit_patch(inp):
    """An edit's removed and added lines from its tool input (Edit / MultiEdit's old and new text, Write's whole text): what
    core/evidence.py reads to tell a fixed test from a weakened one."""
    if "old_string" not in inp and "edits" not in inp:
        return {"minus": [], "plus": str(inp.get("content") or "").splitlines()}
    minus, plus = [], []
    for e in inp.get("edits") or [inp]:
        p = evidence.patch_of(e.get("old_string"), e.get("new_string"))
        minus, plus = minus + p["minus"], plus + p["plus"]
    return {"minus": minus, "plus": plus}


class Worker:
    key = ""
    proc = None

    async def run(self, run):
        raise NotImplementedError

    async def stop(self):
        kill_tree(self.proc)


# -- Claude Code -----------------------------------------------------------------
CLAUDE_KIND = {"Bash": "RUN_COMMAND", "PowerShell": "RUN_COMMAND", "Write": "WRITE", "Edit": "WRITE",
               "MultiEdit": "WRITE", "NotebookEdit": "WRITE", "WebFetch": "NETWORK", "WebSearch": "NETWORK",
               "Read": "READ", "Glob": "READ", "Grep": "READ"}
CLAUDE_WRITES = ("Write", "Edit", "MultiEdit", "NotebookEdit")


class ClaudeCode(Worker):
    key = "claude_code"

    async def run(self, run):
        exe = probes.claude_exe()
        if not exe:
            raise WorkerError("Claude Code is not installed.")
        # acceptEdits: file edits inside the project proceed (the owner asked for the work);
        # commands, network and anything else still come back to the owner as permission requests.
        args = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "acceptEdits", "--append-system-prompt", WORKER_NOTE]
        self.proc = proc = await spawn(args, run.cwd)
        err = []
        drain = asyncio.create_task(_drain(proc.stderr, err))
        pending = set()

        def send(obj):
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write((json.dumps(obj) + "\n").encode())

        async def answer(ev):
            req = ev["request"]
            tool, inp = req.get("tool_name", "?"), req.get("input") or {}
            target = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
            outside = bool(target) and not _under(target, run.cwd)
            ok = await run.ask(CLAUDE_KIND.get(tool, "OTHER"), f"Claude Code wants to use {tool}",
                               _brief(tool, inp), outside_project=outside)
            resp = ({"behavior": "allow", "updatedInput": inp} if ok
                    else {"behavior": "deny", "message": "The owner denied this in Monday."})
            send({"type": "control_response", "response": {"subtype": "success", "request_id": ev["request_id"], "response": resp}})
            await proc.stdin.drain()

        send({"type": "control_request", "request_id": "monday_init", "request": {"subtype": "initialize", "hooks": None}})
        send({"type": "user", "session_id": "default", "parent_tool_use_id": None,
              "message": {"role": "user", "content": run.prompt}})
        await proc.stdin.drain()

        result = None
        async for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                run.started(model=ev.get("model"), session=ev.get("session_id"), permission_mode=ev.get("permissionMode"))
            elif t == "assistant":
                if ev.get("error"):
                    run.log(f"Claude Code error: {ev['error']}")
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "text" and c.get("text", "").strip():
                        run.say(c["text"])
                    elif c.get("type") == "tool_use":
                        run.action(_brief(c.get("name", "tool"), c.get("input")))
                        inp = c.get("input") or {}
                        if c.get("name") in CLAUDE_WRITES:
                            path = inp.get("file_path") or inp.get("notebook_path")
                            if path:
                                run.file(path)
                                run.tool(c.get("id"), "edit", path=path, patch=_edit_patch(inp))
                        elif c.get("name") in ("Bash", "PowerShell"):
                            run.tool(c.get("id"), "run", cmd=inp.get("command"))
            elif t == "user":
                for c in ev.get("message", {}).get("content", []) if isinstance(ev.get("message", {}).get("content"), list) else []:
                    if c.get("type") == "tool_result":
                        text = _text(c.get("content"))
                        run.tool_done(c.get("tool_use_id"), not c.get("is_error"), text)  # Claude Code says ok or not, never the exit code
                        if c.get("is_error"):
                            run.log("Tool error: " + text[:300])
            elif t == "control_request" and ev.get("request", {}).get("subtype") == "can_use_tool":
                task = asyncio.create_task(answer(ev))
                pending.add(task)
                task.add_done_callback(pending.discard)
            elif t == "result":
                result = ev
                proc.stdin.close()
                break

        await proc.wait()
        drain.cancel()
        if result is None:
            raise WorkerError("Claude Code exited without a result. " + "".join(err)[-600:].strip())
        run.usage(cost_usd=result.get("total_cost_usd"), turns=result.get("num_turns"),
                  duration_ms=result.get("duration_ms"), usage=result.get("usage"))
        if result.get("is_error") or result.get("subtype") != "success":
            raise WorkerError(result.get("result") or result.get("subtype") or "Claude Code reported an error.")
        return result.get("result") or ""

    async def stop(self):
        if self.proc and self.proc.returncode is None and self.proc.stdin and not self.proc.stdin.is_closing():
            try:
                self.proc.stdin.write(b'{"type":"control_request","request_id":"monday_stop","request":{"subtype":"interrupt"}}\n')
                await asyncio.wait_for(self.proc.wait(), 3)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass
        kill_tree(self.proc)


# -- Codex -------------------------------------------------------------------------
class Codex(Worker):
    key = "codex"

    async def run(self, run):
        exe = probes.codex_exe()
        if not exe:
            raise WorkerError("Codex is not installed.")
        # Prompt goes over stdin ("-"): no command-line quoting or length limits.
        args = [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", run.cwd, "-"]
        self.proc = proc = await spawn(args, run.cwd)
        err = []
        drain = asyncio.create_task(_drain(proc.stderr, err))
        proc.stdin.write(run.prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        failure, final = None, ""
        async for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type")
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if t == "thread.started":
                run.started(session=ev.get("thread_id"))
            elif t == "turn.started":
                run.action("Thinking")
            elif t in ("item.started", "item.updated", "item.completed"):
                if it == "command_execution":
                    cmd = item.get("command", "")
                    if t == "item.started":
                        run.action(f"$ {evidence.shown(cmd)[:160]}")  # its PowerShell wrapper off: the command as read
                        run.tool(item.get("id"), "run", cmd=cmd)
                    elif t == "item.completed":
                        run.log(f"$ {evidence.shown(cmd)[:200]} -> exit {item.get('exit_code')}")
                        code = item.get("exit_code")
                        run.tool_done(item.get("id"), None if code is None else code == 0, item.get("aggregated_output"), code, cmd=cmd)
                elif it == "file_change" and t == "item.completed":
                    for ch in item.get("changes", []):
                        run.file(ch.get("path"))
                        run.tool(f"{item.get('id')}:{ch.get('path')}", "edit", path=ch.get("path"))  # Codex says which file, not the text
                        run.log(f"{ch.get('kind', 'change')}: {ch.get('path')}")
                elif it == "agent_message" and t == "item.completed":
                    final = item.get("text", "")
                    run.say(final)
                elif it == "reasoning" and t == "item.started":
                    run.action("Reasoning")
                elif it == "web_search":
                    run.action(f"Searching: {item.get('query', '')[:120]}")
                elif it == "mcp_tool_call" and t == "item.started":
                    run.action(f"{item.get('server', '')}.{item.get('tool', '')}")
                elif it == "error":
                    run.log(f"Codex: {item.get('message', '')}")
            elif t == "turn.completed":
                run.usage(usage=ev.get("usage"))
            elif t == "turn.failed":
                failure = (ev.get("error") or {}).get("message") or "Codex turn failed."
            elif t == "error":
                failure = ev.get("message") or failure
        code = await proc.wait()
        drain.cancel()
        if failure or code != 0:
            raise WorkerError(failure or ("Codex exited with code %s. %s" % (code, "".join(err)[-400:].strip())))
        return final


# -- Hermes (Agent Client Protocol) ------------------------------------------------
ACP_KIND = {"execute": "RUN_COMMAND", "edit": "WRITE", "delete": "DELETE", "move": "WRITE",
            "fetch": "NETWORK", "read": "READ", "search": "READ"}
# Hermes' own words for a turn its model never answered (E:\hermes-agent\agent\turn_failure_copy.py): it ends such a turn normally
# (stopReason end_turn) with this text, so the text is the only sign. 2026-10-15: two runs were stored COMPLETED with Kilo's HTTP 401.
HERMES_FAILED = re.compile(
    r"so the model can't be reached|the model didn't answer|retrying won't help|\nProvider said: |"
    r"(?:rate-limited every one of|reported it was overloaded on all|returned a server error on all|didn't respond in time on any of) \d+ "
    r"attempts|didn't answer after \d+ attempts|isn't available on .{1,80}\. Pick a different model with /model|"
    r"sent back an empty or broken reply|Hermes hit repeated errors and stopped this turn|has grown too long for .{1,80} to read|"
    r"^Your request was not processed\.|^This turn did not complete\.", re.M)


# A spare (2026-10-16): Hermes' start took 25 s before its first action (initialize 2 s, session/new 15.8 s, 23 s with the screen-control
# MCP server), the agent built inside session/new. So one process is started ahead: initialized, its session open in the home folder, taken
# by the next task of that worker. Still one task per process (Stop kills its whole tree, as before); ~240 MB while it waits.
SPARE_MAX_S = 1800  # older than this, a spare is replaced: Hermes reads its config (model, provider) when a session opens
_spares = {}        # worker key -> the asyncio.Task warming its spare


async def _handshake(worker, cwd):
    """`worker`'s process, initialized, with a session open in `cwd`: what Acp.run does first, done ahead."""
    argv = worker.argv()
    if not argv:
        raise WorkerError(f"{worker.label} is not installed.")
    proc = await spawn(argv, cwd, env=worker.env(), limit=64 << 20)
    err = []
    drain = asyncio.create_task(_drain(proc.stderr, err))

    async def call(i, method, params):
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n").encode())
        await proc.stdin.drain()
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise WorkerError(f"{worker.label} exited while starting. " + "".join(err)[-400:].strip())
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == i and "error" in m:
                raise WorkerError(f"{worker.label}: {m['error'].get('message')}")
            if m.get("id") == i and "result" in m:
                return m["result"]
    try:
        init = await call(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "monday-core", "version": "0.1"}})
        mcp = worker.mcp()
        s = await call(2, "session/new", {"cwd": cwd, "mcpServers": mcp})
    except BaseException:
        kill_tree(proc)
        drain.cancel()
        raise
    return {"proc": proc, "err": err, "drain": drain, "init": init, "s": s, "mcp": mcp, "cwd": cwd, "at": time.monotonic()}


def warm(worker, cwd):
    """Start `worker`'s spare unless one is warming or ready (Monday: at start, and after each of its tasks)."""
    t = _spares.get(worker.key)
    if t and not t.done():
        return
    if t and not t.cancelled() and t.exception() is None:
        sp = t.result()
        if sp["proc"].returncode is None and time.monotonic() - sp["at"] < SPARE_MAX_S:
            return
        kill_tree(sp["proc"])
    t = _spares[worker.key] = asyncio.create_task(_handshake(worker, cwd))
    t.add_done_callback(lambda f: f.cancelled() or f.exception())  # a failed warm-up is only a slower start, never an error


async def take_spare(worker, cwd):
    """The ready spare for this task (one still warming is waited for: it is ahead of a cold start), or None."""
    t = _spares.get(worker.key)
    if not t:
        return None
    try:
        sp = await asyncio.shield(t)  # a Stop during the wait leaves it for the next task
    except asyncio.CancelledError:
        if t.cancelled():
            _spares.pop(worker.key, None)
            return None
        raise
    except Exception:
        _spares.pop(worker.key, None)
        return None
    if _spares.get(worker.key) is not t:
        return None  # another task took it meanwhile
    _spares.pop(worker.key)
    if sp["cwd"] != cwd or sp["proc"].returncode is not None or time.monotonic() - sp["at"] > SPARE_MAX_S:
        kill_tree(sp["proc"])
        sp["drain"].cancel()
        return None
    return sp


def drop_spares():
    """Monday is stopping: no spare outlives it."""
    for t in _spares.values():
        if t.done() and not t.cancelled() and t.exception() is None:
            kill_tree(t.result()["proc"])
        else:
            t.cancel()
    _spares.clear()


class Acp(Worker):
    """Drives an agent that speaks ACP (the Agent Client Protocol: JSON-RPC 2.0, one object per line, on stdio): Hermes, and any engine
    from data/engines.json (core/engines.py, 2026-10-13). Approvals (session/request_permission) reach the owner; messages and tool calls
    stream to the task. Subclasses say the command (argv), its environment, its MCP servers and the note added to the prompt."""
    key, label = "acp", "the agent"
    own_browser_blocked = False  # Hermes: its own browser tools end the task (HERMES_OWN_BROWSER); a coding engine may browse docs

    def __init__(self):
        self.session = None
        self._id = 0
        self._calls = {}
        self._perm_ids = set()
        self._send_lock = asyncio.Lock()

    def argv(self):
        raise NotImplementedError

    def env(self):
        return probes.worker_env()

    def mcp(self):
        return []

    async def offline_model(self, run, current):
        """The model this session switches to when Monday is offline (`modelId` for session/set_model), or None: stay on its own."""
        return None

    def note(self, mcp):
        # cua-driver 0.28's contract (its own tool descriptions): an element_index needs the snapshot_id of the get_window_state it came
        # from, or its element_token (both in one). The old note named only element_index: 2026-10-16 every click of a Calculator task
        # failed for want of a snapshot_id, the driver paused the tool, and Hermes ended the turn as if done
        return ("\n\n[Monday: to use desktop apps on this computer, use the mcp__computer__* tools. mcp__computer__launch_app opens an "
                "app and gives its pid; mcp__computer__list_windows gives window_id; mcp__computer__get_window_state {pid, window_id} reads "
                "a window: each control in `elements` has an `element_token`. Act on a control by passing that element_token (with pid) to "
                "mcp__computer__click, mcp__computer__set_value or mcp__computer__type_text; never an element_index alone (it needs the "
                "snapshot_id it came from). A token is good only until the next get_window_state of that window, so read the window again "
                "after each change, and check the result there before you say it is done. mcp__computer__press_key / "
                "mcp__computer__hotkey {pid} send keys. To write something new in an editor (Notepad, Word…), start a new document or tab "
                "first (ctrl+n) unless the owner names one that is open: never add it to what is already there. "
                "Use the owner's installed desktop apps, where they are already signed in "
                "(e.g. WhatsApp Desktop, Calculator, Notepad). Websites too are the owner's own browser on this desktop: open a web "
                "address with mcp__computer__launch_app {\"urls\": [\"https://…\"]} (Windows opens it in their default browser, signed in as "
                "they are, as a new tab of its window), then mcp__computer__list_windows to find that browser's window and work in it "
                "like any other app. NEVER use browser_navigate, browser_click, browser_type, browser_exec or any other browser tool of "
                "your own: those run a separate hidden browser the owner can't see, and Monday stops the task if you do.]" if mcp else "")

    async def _send(self, obj):
        async with self._send_lock:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self.proc.stdin.drain()

    async def _call(self, method, params):
        self._id += 1
        fut = asyncio.get_running_loop().create_future()
        self._calls[self._id] = fut
        await self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        return await fut

    async def run(self, run):
        sp = await take_spare(self, run.cwd)  # started ahead (warm): initialized, its session open
        if sp:
            proc, err, drain = sp["proc"], sp["err"], sp["drain"]
            self.proc, self._id = proc, 2  # its initialize and session/new were requests 1 and 2
        else:
            argv = self.argv()
            if not argv:
                raise WorkerError(f"{self.label} is not installed.")
            self.proc = proc = await spawn(argv, run.cwd, env=self.env(), limit=64 << 20)
            err = []
            drain = asyncio.create_task(_drain(proc.stderr, err))
        tools, messages = {}, {}  # toolCallId -> info ; messageId -> text
        state = {"msg": None, "heard": time.monotonic(), "asking": 0, "doing": None}  # the idle watchdog's view (ACP_IDLE_S)

        def flush_message():
            mid = state["msg"]
            if mid and messages.get(mid, "").strip():
                run.say(messages[mid])
            state["msg"] = None

        async def answer_permission(msg):
            p = msg.get("params") or {}
            tc = p.get("toolCall") or {}
            raw = tc.get("rawInput") or {}
            kind = ACP_KIND.get(tc.get("kind"), "OTHER")
            args = raw.get("arguments") or {}
            detail = raw.get("command") or args.get("path") or tc.get("title") or "?"
            target = args.get("path")
            verb = {"RUN_COMMAND": "run a command", "WRITE": "change a file", "DELETE": "delete something",
                    "NETWORK": "use the network"}.get(kind, "do something that needs approval")
            # Expire just before Hermes does: an answer after Hermes' own timeout would be ignored by it,
            # so showing it as "approved" would be a lie.
            state["asking"] += 1  # the owner's turn, not the agent's silence
            try:
                ok = await run.ask(kind, f"{self.label} wants to {verb}", detail,
                                   outside_project=bool(target) and not _under(target, run.cwd),
                                   timeout=max(30.0, probes.hermes_approval_timeout() - 15))
            finally:
                state["asking"] -= 1
                state["heard"] = time.monotonic()
            opts = p.get("options") or []
            # Never "allow always": that would silently edit Hermes' own config for every future session.
            want = "allow_once" if ok else "reject_once"
            choice = next((o["optionId"] for o in opts if o.get("kind") == want), None)
            outcome = {"outcome": "selected", "optionId": choice} if choice else {"outcome": "cancelled"}
            try:
                await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})
            except (OSError, ConnectionError, RuntimeError, AttributeError):
                pass  # Hermes already exited; nothing is waiting for this answer

        def on_update(u):
            su = u.get("sessionUpdate")
            if su == "agent_message_chunk":
                mid = u.get("messageId")
                if mid != state["msg"]:
                    flush_message()
                    state["msg"] = mid
                messages[mid] = messages.get(mid, "") + ((u.get("content") or {}).get("text") or "")
            elif su == "agent_thought_chunk":
                if state["msg"] != "thought":
                    flush_message()
                    state["msg"] = "thought"
                    run.action("Thinking")
            elif su == "tool_call":
                flush_message()
                tools[u.get("toolCallId")] = u
                state["doing"] = u.get("title") or u.get("kind") or "tool"
                run.action(state["doing"])
                if self.own_browser_blocked and HERMES_OWN_BROWSER.match(str(u.get("title") or "")) and not state.get("strayed"):
                    state["strayed"] = str(u.get("title"))[:120]  # its own hidden browser, not the owner's desktop: stopped (HERMES_OWN_BROWSER)
                    run.log(f"{self.label} reached for its own browser ({state['strayed']}); stopping it")
                    asyncio.create_task(self.stop())
                if u.get("kind") == "execute":
                    raw = u.get("rawInput") if isinstance(u.get("rawInput"), dict) else {}
                    run.tool(u.get("toolCallId"), "run", cmd=raw.get("command") or (raw.get("arguments") or {}).get("command") or u.get("title"))
            elif su == "tool_call_update":
                tid = u.get("toolCallId") or ""
                info = tools.get(tid) or {}
                if tid.startswith(("perm-check", "edit-approval")):
                    return
                status = u.get("status")
                if status in ("completed", "failed"):
                    run.log(f"{info.get('title') or tid}: {status}")
                    kind = u.get("kind") or info.get("kind")
                    if kind == "execute":  # "completed" means the tool ran, not that the command exited 0: only a stated exit code says that
                        raw = u.get("rawOutput") if isinstance(u.get("rawOutput"), dict) else {}
                        code = raw.get("exit_code", (raw.get("metadata") or {}).get("exit"))
                        ok = code == 0 if isinstance(code, int) else False if status == "failed" else None
                        run.tool_done(tid, ok, _text(u.get("content")) or str(raw.get("output") or ""), code if isinstance(code, int) else None)
                    if status == "completed" and kind == "edit":
                        for c in u.get("content") or []:
                            if c.get("type") == "diff":
                                run.file(c.get("path"))
                                run.tool(f"{tid}:{c.get('path')}", "edit", path=c.get("path"), patch=evidence.patch_of(c.get("oldText"), c.get("newText")))
                        for loc in info.get("locations") or []:
                            run.file(loc.get("path"))
            elif su == "usage_update":
                run.usage(context_used=u.get("used"), context_size=u.get("size"))

        async def reader():
            async for raw in proc.stdout:
                state["heard"] = time.monotonic()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._calls.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(WorkerError(f"{self.label}: {msg['error'].get('message')} {msg['error'].get('data') or ''}".strip()))
                        else:
                            fut.set_result(msg["result"])
                elif msg.get("method") == "session/update":
                    on_update((msg.get("params") or {}).get("update") or {})
                elif msg.get("method") == "session/request_permission":
                    asyncio.create_task(answer_permission(msg))
                elif "id" in msg and msg.get("method"):
                    await self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
            for fut in self._calls.values():  # process ended: fail anything still waiting
                if not fut.done():
                    fut.set_exception(WorkerError(f"{self.label} exited. " + "".join(err)[-600:].strip()))

        read_task = asyncio.create_task(reader())
        try:
            if sp:
                init, mcp, s = sp["init"], sp["mcp"], sp["s"]
            else:
                init = await self._call("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                                       "clientInfo": {"name": "monday-core", "version": "0.1"}})
                mcp = self.mcp()  # screen control, when configured (see core/cua_proxy.py)
                s = await self._call("session/new", {"cwd": run.cwd, "mcpServers": mcp})
            self.session = s["sessionId"]
            model = (s.get("models") or {}).get("currentModelId")
            local = await self.offline_model(run, model)
            if local:  # offline: this session on the model on this PC (Hermes' `session/set_model`: this session only, its config untouched)
                try:
                    await self._call("session/set_model", {"sessionId": self.session, "modelId": local})
                except WorkerError as e:
                    raise WorkerError(f"I'm offline, and {self.label} couldn't move to {local.split(':', 1)[1]} on this PC: {e}") from None
                run.log(f"Offline: {self.label} works on {local.split(':', 1)[1]}, on this PC, for this task.")
                model = local
            run.started(model=model, session=self.session,
                        hermes=(init.get("agentInfo") or {}).get("version"), engine=(init.get("agentInfo") or {}).get("name"),
                        screen_control=bool(mcp) or None, started_ahead=bool(sp) or None)
            prompt = run.prompt + self.note(mcp)
            turn = asyncio.ensure_future(self._call("session/prompt", {"sessionId": self.session,
                                                                        "prompt": [{"type": "text", "text": prompt}]}))
            t0 = time.monotonic()
            while not turn.done():
                await asyncio.wait({turn}, timeout=ACP_TICK_S)
                if turn.done():
                    break
                quiet = not state["asking"] and time.monotonic() - state["heard"] > ACP_IDLE_S
                if quiet or time.monotonic() - t0 > ACP_MAX_S:  # stuck, or the whole turn past its cap (ACP_MAX_S): stopped, said as that
                    turn.cancel()
                    await self.stop()
                    doing = f" (its last step: {state['doing']})" if state["doing"] else ""
                    why = f"went quiet for {ACP_IDLE_S // 60} minutes" if quiet else f"ran for {ACP_MAX_S // 60} min"
                    raise WorkerError(f"{self.label} {why}{doing}, so I stopped it. Nothing it said it finished was checked; ask again, "
                                      "or say what to do differently.")
            if state.get("strayed"):  # stopped by Monday (HERMES_OWN_BROWSER): said as that, not as "exited", once its process is gone
                await self.stop()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except (asyncio.TimeoutError, OSError):
                    pass
                if turn.done() and not turn.cancelled():
                    turn.exception()  # (the turn ended with the process: its error is this one, said below)
                raise WorkerError(f"{self.label} reached for its own hidden browser ({state['strayed']}) instead of your desktop, so Monday "
                                  f"stopped it. Web pages are your own browser's: nothing was opened or done there.")
            res = turn.result()
        finally:
            flush_message()
        u = res.get("usage") or {}
        run.usage(input_tokens=u.get("inputTokens"), output_tokens=u.get("outputTokens"))
        final = next((messages[m] for m in reversed(list(messages)) if messages[m].strip()), "")
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            kill_tree(proc)
        read_task.cancel()
        drain.cancel()
        if res.get("stopReason") == "cancelled":
            raise WorkerError(f"{self.label} stopped (cancelled).")
        if res.get("stopReason") == "refusal":
            raise WorkerError(f"{self.label} refused the request.")
        if final.strip().startswith("Error:") or HERMES_FAILED.search(final):  # Hermes reports agent failures as message text
            raise WorkerError(final.strip()[:1000])
        return final

    async def stop(self):
        if self.proc and self.proc.returncode is None and self.session:
            try:
                await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session}})
                await asyncio.wait_for(self.proc.wait(), 8)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass
        kill_tree(self.proc)


class Hermes(Acp):
    """`hermes-acp` (next to hermes.exe in its venv), with Monday's desktop-control MCP server when cua-driver is configured."""
    key, label = "hermes", "Monday's agent"  # its name in what the owner reads ("Monday's agent wants to run a command"; the owner, 2026-10-20: no Hermes in the UI)
    own_browser_blocked = True

    def argv(self):
        exe = probes.hermes_acp_exe()
        return (probes.hermes_monday(exe) or [exe]) if exe else None  # (on Windows as Monday.exe: Task Manager's one "Monday")

    def env(self):
        env = probes.worker_env()
        env["HERMES_EXEC_ASK"] = "1"  # route execute_code through the same approval callback
        return env

    def mcp(self):
        return probes.computer_use_mcp()

    async def offline_model(self, run, current):
        """Offline, Hermes' cloud model can't answer: its session moves to HERMES_OFFLINE_MODEL on this PC (the owner, 2026-10-24: "when I'm
        offline, have Hermes use hermes-qwen3-4b so open-ended computer tasks work without internet"), on the endpoint it already uses
        (Hermes calls the configured Ollama base_url "custom": `custom:gemma4:31b-cloud` -> `custom:hermes-qwen3-4b:latest`; measured
        2026-10-24: the switch takes ~1 s, also with Hermes' own internet cut). None online, when its model already runs here, or when
        that model isn't on this PC."""
        health = getattr(getattr(run, "core", None), "health", None)
        if health is None or (current and not current.endswith("cloud")) or (await health.internet())["status"] == "online":
            return None
        if not any(m["name"] == HERMES_OFFLINE_MODEL and not m["remote"] for m in (await health.ollama()).get("models") or []):
            return None
        return f"{current.split(':', 1)[0] if current and ':' in current else 'ollama'}:{HERMES_OFFLINE_MODEL}"


class AcpEngine(Acp):
    """An engine from data/engines.json (core/engines.py): its own command line, Monday's desktop-control MCP server only when its entry asks."""

    def __init__(self, entry):
        super().__init__()
        self.entry, self.key, self.label = entry, entry["key"], entry["name"]

    def argv(self):
        return self.entry.get("argv")

    def mcp(self):
        return probes.computer_use_mcp() if self.entry.get("mcp") else []


def worker_for(key, engines=None):
    """The worker for an agent key: a built-in one, else an engine from the config (core/engines.py)."""
    if key in WORKERS:
        return WORKERS[key]()
    e = (engines or {}).get(key)
    if e:
        return AcpEngine(e)
    raise WorkerError(f"Unknown worker {key}")


# -- JSON-lines coding CLIs (Gemini CLI, Kilo Code) ---------------------------------------------------------------
# Headless modes that cannot relay approvals: Monday asks once before launch (like Codex), and each runs in its most
# conservative non-interactive mode that still lets it edit the project.
LIMIT_WORDS = re.compile(r"usage limit|quota|rate[\s-]?limit|resource[_\s]exhausted|\b429\b|too many requests|credit balance|"
                         r"out of credits|any credits|no credits|licenses? yet|insufficient (?:funds|balance|credits)|billing", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]")


class JsonCli(Worker):
    name = ""

    def argv(self, run, entry):
        raise NotImplementedError

    def entry(self):
        """How to start the CLI: a global npm package as [node, script]."""
        from .agents import npm_entry
        return npm_entry(self.npm)

    def stdin(self, run):
        return None

    def event(self, run, ev, state):
        raise NotImplementedError

    async def run(self, run):
        entry = self.entry()
        if not entry:
            raise WorkerError(f"{self.name} is not installed.")
        self.proc = proc = await spawn(self.argv(run, entry), run.cwd)
        err, state = [], {"final": "", "failure": None, "text": []}
        drain = asyncio.create_task(_drain(proc.stderr, err))
        data = self.stdin(run)
        if data is not None:
            proc.stdin.write(data.encode())
            await proc.stdin.drain()
        proc.stdin.close()
        async for raw in proc.stdout:
            line = _ANSI.sub("", raw.decode("utf-8", "replace")).strip()  # some CLIs redraw a TUI around their JSON
            if not line:
                continue
            try:
                ev = json.loads(line[line.index("{"):]) if "{" in line else None
            except ValueError:
                ev = None
            if not isinstance(ev, dict):
                run.log(line[:300])
                continue
            self.event(run, ev, state)
            if state.get("abort"):  # an error retrying can't fix (bad key, no credits): stop now, don't wait out retries
                kill_tree(proc)
                break
        code = await proc.wait()
        drain.cancel()
        tail = "".join(err)[-600:].strip()
        failure = state["failure"] or (None if code == 0 else f"{self.name} exited with code {code}. {tail}".strip())
        if failure:
            if LIMIT_WORDS.search(failure + " " + tail):
                run.limit_hit(self.key, failure + " " + tail)
            raise WorkerError(failure)
        return state["final"] or "".join(state["text"]).strip()


class GeminiCLI(JsonCli):
    """`gemini` headless: prompt on stdin, --output-format stream-json, --approval-mode auto_edit (edits yes, shell no)."""
    key, name, npm = "gemini_cli", "Gemini CLI", "gemini"

    def argv(self, run, entry):
        return [*entry, "--output-format", "stream-json", "--approval-mode", "auto_edit", "-p", WORKER_NOTE]

    def stdin(self, run):
        return run.prompt

    def event(self, run, ev, state):
        t = ev.get("type")
        if t == "init":
            run.started(model=ev.get("model"), session=ev.get("session_id"))
        elif t == "message" and ev.get("role") == "assistant":
            state["text"].append(ev.get("content") or "")
            if not ev.get("delta"):
                run.say(ev.get("content") or "")
        elif t == "tool_use":
            params = ev.get("parameters") or {}
            run.action(_brief(ev.get("tool_name") or "tool", params))
            path = params.get("file_path") or params.get("absolute_path")
            if path and re.search(r"write|edit|replace", ev.get("tool_name") or "", re.I):
                run.file(path)
                run.tool(ev.get("tool_id"), "edit", path=path, patch=_edit_patch(params))
            elif re.search(r"shell|command", ev.get("tool_name") or "", re.I):  # (auto_edit runs none; a mode that does is still read)
                run.tool(ev.get("tool_id"), "run", cmd=params.get("command"))
        elif t == "tool_result":
            out = ev.get("output") if isinstance(ev.get("output"), str) else _text(ev.get("output") or ev.get("error") or "")
            run.tool_done(ev.get("tool_id"), ev.get("status") == "success", out)
            if ev.get("status") not in (None, "success"):
                run.log(f"{ev.get('tool_id') or 'tool'}: {str(ev.get('output') or ev.get('error') or ev.get('status'))[:300]}")
        elif t == "error":
            run.log(f"Gemini CLI: {ev.get('message')}")
            if ev.get("severity") == "error":
                state["failure"] = ev.get("message")
        elif t == "result":
            st = ev.get("stats") or {}
            run.usage(input_tokens=st.get("input_tokens"), output_tokens=st.get("output_tokens"), duration_ms=st.get("duration_ms"))
            if ev.get("status") not in (None, "success"):
                state["failure"] = (ev.get("error") or {}).get("message") if isinstance(ev.get("error"), dict) else str(ev.get("error") or "Gemini CLI reported an error.")
            state["final"] = "".join(state["text"]).strip()


class KiloCode(Acp):
    """Kilo 7 (`kilo acp`, opencode-based): driven over ACP like Hermes, so its approvals reach the owner mid-run. 2026-10-15: Kilo 0.x's
    `--auto --json` flags are gone (Kilo 7 has `run` and `acp`); its handshake answered here as "Kilo 7.8.3", protocol 1."""
    key, label = "kilo", "Kilo Code"

    def argv(self):
        from .agents import npm_entry  # agents imports this module
        entry = npm_entry("kilocode") or npm_entry("kilo")
        return [*entry, "acp"] if entry else None

    def note(self, mcp):
        return "\n\n" + WORKER_NOTE


GROK_TOOLS = "read_file,write,search_replace,list_dir,grep,todo_write"  # Grok Build's file tools; run_terminal_command left out


class GrokBuild(JsonCli):
    """xAI's Grok Build CLI, headless, signed in with the owner's grok.com account: `grok -p … --output-format
    streaming-json --permission-mode acceptEdits --allow write` in the project. Edits and new files yes; a terminal
    command would need an approval headless mode can't give, so none runs (Monday asked the owner before launch).
    Event format captured from grok 1.0.44 on 2026-09-30: `text` / `thought` pieces, `usage`, `tool_call`
    (toolName, kind, rawInput), `tool_call_update` (status, content), `end` (stopReason, total_cost_usd)."""
    key, name = "grok_build", "Grok Build"

    def entry(self):
        exe = shutil.which("grok")
        return [exe] if exe else None

    def argv(self, run, entry):
        # Headless, acceptEdits (and "--allow write") still cancelled `write`, a new file. So only the file tools exist
        # (--tools; no terminal-command tool at all) and those are approved (--always-approve): edits and new files, never
        # a command. The owner approved the launch itself in Monday.
        return [*entry, "-p", f"{WORKER_NOTE}\n\n{run.prompt}"[:24000], "--output-format", "streaming-json",
                "--tools", GROK_TOOLS, "--always-approve", "--cwd", run.cwd]

    def event(self, run, ev, state):
        t = ev.get("type") or ""
        if t == "text":
            state["text"].append(ev.get("data") or "")
        elif t == "thought" and not state.get("thinking"):
            state["thinking"] = True
            run.action("Thinking")
        elif t == "usage":
            u = ev.get("usage") or {}
            run.usage(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"))
        elif t == "tool_call":
            if state["text"] and not state["text"][-1].endswith("\n"):
                state["text"].append("\n")  # what it said before the step, then what it said after, as separate lines
            params = ev.get("rawInput") if isinstance(ev.get("rawInput"), dict) else {}
            run.action(_brief(str(ev.get("toolName") or ev.get("title") or "tool"), params))
            if ev.get("kind") in ("write", "edit") and (params.get("file_path") or params.get("path")):
                run.file(params.get("file_path") or params.get("path"))
                state["edit"] = run.tool(ev.get("toolCallId"), "edit", path=params.get("file_path") or params.get("path"), patch=_edit_patch(params))
        elif t == "tool_call_update" and ev.get("status") == "failed":
            why = " ".join(c.get("content", {}).get("text", "") for c in ev.get("content") or [] if isinstance(c.get("content"), dict))
            state["tool_failed"] = why.strip()[:300]
            run.log(f"Grok Build: {state['tool_failed']}")
            if state.get("edit"):  # its events carry no call id: the update is the last edit's (one tool at a time)
                run.tool_done(state["edit"]["id"], False)
        elif t == "end":
            state["final"] = "".join(state["text"]).strip()
            run.usage(cost_usd=ev.get("total_cost_usd"), turns=ev.get("num_turns"))
            if ev.get("stopReason") not in (None, "end_turn"):
                state["failure"] = f"Grok Build stopped: {ev.get('stopReason')}." + (
                    f" ({state['tool_failed']}: a step it needed wasn't allowed in Monday's headless run.)" if state.get("tool_failed") else "")
        elif t == "error":
            state["failure"] = str(ev.get("message") or ev.get("data") or ev.get("error") or "Grok Build reported an error.")[:500]


# -- Computer: Monday's own hands (plans from the fast path, or a model proposing one step at a time) -----------------
class Computer(Worker):
    key = "computer"

    async def run(self, run):
        from . import executor
        steps = run.route.get("steps")
        return await (executor.run_plan(run, steps) if steps else executor.run_goal(run, run.prompt))


# -- An editor's own agent (Antigravity IDE, Kiro, VS Code): the last resort for code work ------------------------------------------
HANDOFF_TAKE_S = 90     # the editor's command line gets this long to take the prompt (it starts the editor when it isn't running)
HANDOFF_START_S = 300   # no file changed this long after the hand-over: its agent isn't working on it
HANDOFF_QUIET_S = 45    # no further change this long after the last one: its work is taken to be over
HANDOFF_MAX_S = 1500
HANDOFF_POLL_S = 3


class EditorAgent(Worker):
    """An AI editor's own agent, reached through the editor's official command line: `<editor> chat --mode agent "<prompt>"`
    in the project folder. The agent then works inside the editor, in its own window, with the owner's sign-in there and the
    editor's own permission settings. Nothing comes back to Monday but the files that change, so that is all this reports: the
    task is over when the project changed and then stayed quiet, and its result is the list of files, never a claim about them.
    No change at all is a failure with the likely reason (a sign-in, a trust question or an approval waiting in that window).
    Measured 2026-10-05: see MONDAY2.md, Phase 19."""
    key = name = cmd = ""

    async def run(self, run):
        from . import repair
        from .agents import editor_chat
        entry = await editor_chat(self.cmd)
        if not entry:
            raise WorkerError(f"{self.name} can't be handed a prompt from the command line on this PC.")
        root = Path(run.cwd)
        before = last = await asyncio.to_thread(repair.stamps, root)
        run.started(model=f"{self.name}'s own agent")
        run.action(f"Handing the prompt to {self.name}")
        self.proc = proc = await spawn([*entry, "chat", "--mode", "agent", f"{WORKER_NOTE}\n\n{run.prompt}"[:24000]], run.cwd,
                                       env={**probes.worker_env(), "ELECTRON_RUN_AS_NODE": "1"})
        proc.stdin.close()
        try:  # waited for, not read to its end: the editor it starts may hold the pipes open for as long as it runs
            await asyncio.wait_for(proc.wait(), HANDOFF_TAKE_S)
        except asyncio.TimeoutError:
            kill_tree(proc)
            raise WorkerError(f"{self.name} didn't take the prompt within {HANDOFF_TAKE_S} seconds.") from None
        if proc.returncode:
            try:
                err = (await asyncio.wait_for(proc.stderr.read(4000), 2)).decode("utf-8", "replace")
            except asyncio.TimeoutError:
                err = ""
            said = next((ln.strip() for ln in reversed(err.splitlines()) if ln.strip()), f"exit code {proc.returncode}")
            raise WorkerError(f"{self.name} didn't take the prompt: {said[:300]}")
        run.action(f"{self.name}'s agent has the prompt; watching the project for what it changes")
        t0 = t_change = time.monotonic()
        changed = []
        while True:
            await asyncio.sleep(HANDOFF_POLL_S)
            if run.stopping:
                raise WorkerError(f"Stopped by the owner. {self.name}'s agent isn't stopped by that: stop it in its own window.")
            now = await asyncio.to_thread(repair.stamps, root)
            if now != last:
                last, t_change = now, time.monotonic()
                for f in sorted(f for f in now.keys() | before.keys() if now.get(f) != before.get(f)):
                    if f not in changed:
                        changed.append(f)
                        run.file(str(root / f))
                        run.action(f"{self.name} changed {f}")
            quiet, waited = time.monotonic() - t_change, time.monotonic() - t0
            if not changed and waited > HANDOFF_START_S:
                raise WorkerError(f"{self.name}'s agent changed nothing in {HANDOFF_START_S // 60} minutes. It may be waiting for you in its window: "
                                  "a sign-in, a question whether to trust the folder, or an approval.")
            if changed and (quiet > HANDOFF_QUIET_S or waited > HANDOFF_MAX_S):
                break
        return (f"{self.name}'s agent changed {len(changed)} file{'s' if len(changed) != 1 else ''}: {', '.join(changed[:8])}"
                f"{' and more' if len(changed) > 8 else ''}. What it says about the work is in its own window; Monday sees only the files.")

    async def stop(self):
        kill_tree(self.proc)  # the short-lived command that carries the prompt; the editor and its agent are the owner's to stop


WORKERS = {"claude_code": ClaudeCode, "codex": Codex, "gemini_cli": GeminiCLI, "kilo": KiloCode, "grok_build": GrokBuild,
           "hermes": Hermes, "computer": Computer,
           **{k: type(f"{k.title()}Agent", (EditorAgent,), {"key": k, "name": n, "cmd": c}) for k, n, c in EDITORS}}
# can't relay approvals mid-run: Monday asks once before launch whenever the request itself is risky
NO_RELAY = ("codex", "gemini_cli", "grok_build", *HANDOFF)


if __name__ == "__main__":  # python -m core.workers  -> JSON-lines adapters against output captured from the real CLIs
    import tempfile
    import types

    # EditorAgent against a stand-in editor command: it takes the prompt and returns at once; the "agent" edits a file a moment later
    async def editor_agent():
        from . import agents
        global HANDOFF_POLL_S, HANDOFF_QUIET_S, HANDOFF_START_S, HANDOFF_TAKE_S
        HANDOFF_POLL_S, HANDOFF_QUIET_S, HANDOFF_START_S, HANDOFF_TAKE_S = 0.1, 0.6, 1.5, 20
        with tempfile.TemporaryDirectory() as d:
            project, cli, got = Path(d) / "proj", Path(d) / "fake_editor.py", Path(d) / "prompt.txt"
            project.mkdir()
            (project / "a.py").write_text("A = 1\n", encoding="utf-8")
            cli.write_text(
                "import os, subprocess, sys\n"
                "mode = os.environ.get('FAKE_EDITOR', 'edit')\n"
                f"open({str(got)!r}, 'w', encoding='utf-8').write('|'.join(sys.argv[1:4]) + chr(10) + sys.argv[4])\n"
                "if mode == 'refuse':\n    sys.stderr.write('warning\\nnot signed in\\n'); sys.exit(3)\n"
                "if mode == 'edit':\n"
                "    code = \"import time; time.sleep(0.4); open('a.py', 'w').write('A = 2' + chr(10)); time.sleep(0.3); open('b.py', 'w').write('B = 1' + chr(10))\"\n"
                "    subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n", encoding="utf-8")
            real = agents.editor_chat

            async def chat(cmd):
                return [sys.executable, str(cli)] if cmd == "some-ide" else None
            agents.editor_chat = chat
            Some = type("SomeAgent", (EditorAgent,), {"key": "some", "name": "Some IDE", "cmd": "some-ide"})

            def a_run(**k):
                r = types.SimpleNamespace(cwd=str(project), prompt="Fix a fault: it says \"x\" & 100% of %PATH%\nsecond line", stopping=False, files=[], actions=[],
                                          started=lambda **k: None, usage=lambda **k: None, log=lambda t: None, say=lambda t: None)
                r.file, r.action = (lambda p: r.files.append(Path(p).name)), (lambda t: r.actions.append(t))
                vars(r).update(k)
                return r
            try:
                os.environ["FAKE_EDITOR"] = "edit"
                r = a_run()
                out = await Some().run(r)
                assert out == "Some IDE's agent changed 2 files: a.py, b.py. What it says about the work is in its own window; Monday sees only the files.", out
                assert r.files == ["a.py", "b.py"] and "Some IDE changed b.py" in r.actions, (r.files, r.actions)
                head, _, prompt = got.read_text(encoding="utf-8").partition("\n")
                assert head == "chat|--mode|agent" and prompt.endswith('Fix a fault: it says "x" & 100% of %PATH%\nsecond line') and prompt.startswith(WORKER_NOTE), (
                    "the prompt arrives whole: its quotes, percent signs and line break", head, prompt[-80:])
                for mode, want in (("nothing", "Some IDE's agent changed nothing in 0.0 minutes. It may be waiting for you in its window"),  # (this check's own, shortened wait)
                                   ("refuse", "Some IDE didn't take the prompt: not signed in")):
                    os.environ["FAKE_EDITOR"] = mode
                    try:
                        await Some().run(a_run())
                        raise AssertionError(f"{mode}: no change and a refusal are failures, each with its reason")
                    except WorkerError as e:
                        assert str(e).startswith(want), (mode, e)
                os.environ["FAKE_EDITOR"] = "nothing"
                try:
                    await Some().run(a_run(stopping=True))
                    raise AssertionError("a stop ends the wait")
                except WorkerError as e:
                    assert str(e) == "Stopped by the owner. Some IDE's agent isn't stopped by that: stop it in its own window.", e
                try:
                    await type("NoAgent", (EditorAgent,), {"key": "no", "name": "No IDE", "cmd": "no-ide"})().run(a_run())
                    raise AssertionError("an editor without the command can't take a prompt")
                except WorkerError as e:
                    assert str(e) == "No IDE can't be handed a prompt from the command line on this PC.", e
            finally:
                agents.editor_chat = real
                os.environ.pop("FAKE_EDITOR", None)
    asyncio.run(editor_agent())
    assert set(HANDOFF) <= set(WORKERS) and set(HANDOFF) <= set(NO_RELAY) and WORKERS["kiro"].name == "Kiro" and WORKERS["antigravity"].cmd == "antigravity-ide"
    run = types.SimpleNamespace(said=[], actions=[], logs=[], tools=[], dones=[], say=lambda t: run.said.append(t), action=lambda t: run.actions.append(t),
                                log=lambda t: run.logs.append(t), file=lambda p: None, started=lambda **k: None, usage=lambda **k: None,
                                tool=lambda cid, kind, **k: run.tools.append((cid, kind, k)) or {"id": cid},
                                tool_done=lambda cid, ok, out=None, code=None, cmd=None: run.dones.append((cid, ok, out, code)))
    k = KiloCode()  # Kilo 7 speaks ACP: its approvals are relayed (not asked once before launch), and Monday's note goes with the task
    assert isinstance(k, Acp) and "kilo" not in NO_RELAY and WORKER_NOTE in k.note([]) and (k.argv() is None or k.argv()[-1] == "acp")
    assert "new document or tab" in Hermes().note(["computer"]) and not Hermes().note([]), \
        "2026-10-20: Hermes typed into the open Notepad document ('here therehello im hello'), not a new one"
    g, st = GeminiCLI(), {"final": "", "failure": None, "text": []}
    for ev in ({"type": "init", "model": "gemini-2.5-pro"}, {"type": "message", "role": "assistant", "content": "RE", "delta": True},
               {"type": "message", "role": "assistant", "content": "ADY", "delta": True}, {"type": "result", "status": "success", "stats": {}}):
        g.event(run, ev, st)
    assert st["final"] == "READY" and not st["failure"], st
    gb, st = GrokBuild(), {"final": "", "failure": None, "text": []}
    for ev in ({"type": "available_commands", "tools": ["read_file"]}, {"type": "thought", "data": "The user"},  # grok 1.0.44, 2026-09-30
               {"type": "thought", "data": " wants ok."}, {"type": "text", "data": "o"}, {"type": "text", "data": "k"},
               {"type": "usage", "usage": {"input_tokens": 17594, "output_tokens": 26}},
               {"type": "end", "stopReason": "end_turn", "sessionId": "s1"}):
        gb.event(run, ev, st)
    assert st["final"] == "ok" and not st["failure"] and run.actions[-1] == "Thinking", (st, run.actions)
    gb.event(run, {"type": "end", "stopReason": "max_turns"}, st)
    assert st["failure"] == "Grok Build stopped: max_turns.", st
    st = {"final": "", "failure": None, "text": []}  # 2026-09-30, acceptEdits without --allow write: the new file was refused
    for ev in ({"type": "tool_call", "toolName": "write", "kind": "write", "status": "pending", "rawInput": {"file_path": "E:\\t\\hello.txt", "content": "hi"}},
               {"type": "tool_call_update", "status": "failed", "content": [{"type": "content", "content": {"type": "text", "text": "User cancelled the execution for tool `write`"}}]},
               {"type": "end", "stopReason": "cancelled", "total_cost_usd": 0.037, "num_turns": 1}):
        gb.event(run, ev, st)
    assert run.actions[-1] == "write: E:\\t\\hello.txt" and "tool `write`" in st["failure"] and "headless" in st["failure"], (run.actions, st)
    assert run.tools[-1][:2] == (None, "edit") and run.tools[-1][2]["patch"] == {"minus": [], "plus": ["hi"]} and run.dones[-1][:2] == (None, False), \
        ("Grok's edit is evidence, and its refusal says the edit changed nothing", run.tools, run.dones)
    # Gemini CLI's edits and their results become evidence steps (core/evidence.py), with the edit's own lines
    run.tools.clear(), run.dones.clear()
    for ev in ({"type": "tool_use", "tool_name": "replace", "tool_id": "g1", "parameters": {"file_path": "C:\\p\\test_a.py", "old_string": "assert x == 3",
                                                                                           "new_string": "assert x == 4"}},
               {"type": "tool_result", "tool_id": "g1", "status": "success", "output": "ok"}):
        g.event(run, ev, {"final": "", "failure": None, "text": []})
    assert run.tools == [("g1", "edit", {"path": "C:\\p\\test_a.py", "patch": {"minus": ["assert x == 3"], "plus": ["assert x == 4"]}})] and run.dones == [("g1", True, "ok", None)], (run.tools, run.dones)
    # Claude Code and Codex: their real streams (captured shapes), through the real run loops, against stand-in processes
    FAKE_CLAUDE = r'''
import json, sys
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
sys.stdin.readline(); sys.stdin.readline()
send({"type": "system", "subtype": "init", "model": "m", "session_id": "s1"})
send({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "src/a.js", "old_string": "a - b", "new_string": "a + b"}}]}})
send({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "edited"}]}})
send({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "npm test"}}]}})
send({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": [{"type": "text", "text": "Tests: 1 failed, 1 total"}]}]}})
send({"type": "result", "subtype": "success", "result": "done", "num_turns": 2})
'''
    FAKE_CODEX = r'''
import json, sys
sys.stdin.read()
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
send({"type": "thread.started", "thread_id": "th1"})
send({"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "pytest -q"}})
send({"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "pytest -q", "exit_code": 1, "aggregated_output": "1 failed, 2 passed in 0.1s"}})
send({"type": "item.completed", "item": {"id": "f1", "type": "file_change", "changes": [{"path": "src/a.py", "kind": "update"}]}})
send({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "Fixed."}})
'''

    async def streams():
        global spawn
        real_spawn, real_claude, real_codex = spawn, probes.claude_exe, probes.codex_exe
        with tempfile.TemporaryDirectory() as d:
            for name, script, worker, want_tools, want_dones in (
                    ("claude", FAKE_CLAUDE, ClaudeCode, [("t1", "edit"), ("t2", "run")], [("t1", True, "edited", None), ("t2", False, "Tests: 1 failed, 1 total", None)]),
                    ("codex", FAKE_CODEX, Codex, [("c1", "run"), ("f1:src/a.py", "edit")], [("c1", False, "1 failed, 2 passed in 0.1s", 1)])):
                (Path(d) / f"{name}.py").write_text(script, encoding="utf-8")
                spawn = lambda args, cwd, env=None, limit=STREAM_LIMIT, s=Path(d) / f"{name}.py": real_spawn([sys.executable, str(s)], cwd)  # noqa: E731
                probes.claude_exe = probes.codex_exe = lambda: sys.executable
                run.tools.clear(), run.dones.clear()
                r = types.SimpleNamespace(**{**vars(run), "cwd": d, "prompt": "fix it", "ask": None})
                try:
                    await asyncio.wait_for(worker().run(r), 30)
                finally:
                    spawn, probes.claude_exe, probes.codex_exe = real_spawn, real_claude, real_codex
                assert [t[:2] for t in run.tools] == want_tools and run.dones == want_dones, (name, run.tools, run.dones)
            assert run.tools[0][2]["cmd"] == "pytest -q"
    asyncio.run(streams())
    a = gb.argv(types.SimpleNamespace(prompt="p", cwd="."), ["grok"])
    assert "--always-approve" in a and "run_terminal_command" not in a[a.index("--tools") + 1], "file tools only, never commands"
    assert gb.argv(types.SimpleNamespace(prompt="fix it", cwd="C:\\p"), ["grok"])[1:3] == ["-p", f"{WORKER_NOTE}\n\nfix it"]

    # A custom engine over ACP (core/engines.py, 2026-10-13): a stand-in agent that speaks the protocol, asks one approval, then answers
    FAKE_ACP = r'''
import json, sys
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize": send({"jsonrpc": "2.0", "id": m["id"], "result": {"agentInfo": {"name": "stand-in", "version": "1"}}})
    elif m.get("method") == "session/new": send({"jsonrpc": "2.0", "id": m["id"], "result": {"sessionId": "s1"}})
    elif m.get("method") == "session/prompt":
        pid = m["id"]
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read notes.txt", "kind": "read"}}})
        send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission", "params": {"toolCall": {"kind": "execute", "rawInput": {"command": "dir"}},
              "options": [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]}})
        ans = json.loads(sys.stdin.readline())
        ok = ans.get("result", {}).get("outcome", {}).get("optionId") == "yes"
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "messageId": "m1",
              "content": {"type": "text", "text": "ran dir" if ok else "was told no"}}}})
        send({"jsonrpc": "2.0", "id": pid, "result": {"stopReason": "end_turn", "usage": {"inputTokens": 5, "outputTokens": 2}}})
'''
    # One that starts a tool and then says nothing (Hermes' browser_exec, 2026-10-16); it leaves when told to cancel
    STUCK_ACP = r'''
import json, sys
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize": send({"jsonrpc": "2.0", "id": m["id"], "result": {"agentInfo": {"name": "stuck", "version": "1"}}})
    elif m.get("method") == "session/new": send({"jsonrpc": "2.0", "id": m["id"], "result": {"sessionId": "s1"}})
    elif m.get("method") == "session/prompt":
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "browser_exec: search", "kind": "fetch"}}})
    elif m.get("method") == "session/cancel": sys.exit(0)
'''
    # Hermes as it opens a session (its own word for the Ollama base_url it is configured with: "custom"), taking session/set_model
    SWITCH_ACP = r'''
import json, sys
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
model = "custom:gemma4:31b-cloud"
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize": send({"jsonrpc": "2.0", "id": m["id"], "result": {"agentInfo": {"name": "hermes", "version": "1"}}})
    elif m.get("method") == "session/new": send({"jsonrpc": "2.0", "id": m["id"], "result": {"sessionId": "s1", "models": {"currentModelId": model}}})
    elif m.get("method") == "session/set_model":
        model = m["params"]["modelId"]
        send({"jsonrpc": "2.0", "id": m["id"], "result": {}})
    elif m.get("method") == "session/prompt":
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "messageId": "m1",
              "content": {"type": "text", "text": "on " + model}}}})
        send({"jsonrpc": "2.0", "id": m["id"], "result": {"stopReason": "end_turn"}})
'''
    # One that never stops acting (a Hermes loop clicking on and on, 2026-10-27): never quiet, so only the whole-turn cap (ACP_MAX_S) ends it
    BUSY_ACP = r'''
import json, sys, threading, time
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def chatter():
    n = 0
    while True:
        n += 1
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "tool_call", "toolCallId": "t%d" % n,
              "title": "mcp__computer__click %d" % n, "kind": "other"}}})
        time.sleep(0.05)
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize": send({"jsonrpc": "2.0", "id": m["id"], "result": {"agentInfo": {"name": "busy", "version": "1"}}})
    elif m.get("method") == "session/new": send({"jsonrpc": "2.0", "id": m["id"], "result": {"sessionId": "s1"}})
    elif m.get("method") == "session/prompt": threading.Thread(target=chatter, daemon=True).start()
    elif m.get("method") == "session/cancel": sys.exit(0)
'''
    ACP_IDLE_S, ACP_TICK_S = 0.5, 0.1  # the watchdog, quickly: silence is stopped; an approval waiting on the owner is not silence

    async def acp_engine():
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "fake_acp.py"
            script.write_text(FAKE_ACP, encoding="utf-8")
            for approve, want in ((True, "ran dir"), (False, "was told no")):
                asked = []

                async def ask(kind, title, detail, **k):
                    asked.append((kind, title, detail))
                    await asyncio.sleep(ACP_IDLE_S * 2)  # the owner takes their time: the agent is waiting, not stuck
                    return approve
                r = types.SimpleNamespace(cwd=d, prompt="list the folder", say=lambda t: None, action=lambda t: None, log=lambda t: None,
                                          file=lambda p: None, started=lambda **k: None, usage=lambda **k: None, ask=ask)
                w = worker_for("stand_in", {"stand_in": {"key": "stand_in", "name": "Stand-in", "argv": [sys.executable, str(script)], "mcp": False}})
                out = await asyncio.wait_for(w.run(r), 30)
                assert out == want and asked == [("RUN_COMMAND", "Stand-in wants to run a command", "dir")], (out, asked)
            stuck = Path(d) / "stuck_acp.py"
            stuck.write_text(STUCK_ACP, encoding="utf-8")
            w = worker_for("stuck", {"stuck": {"key": "stuck", "name": "Stuck", "argv": [sys.executable, str(stuck)], "mcp": False}})
            r = types.SimpleNamespace(cwd=d, prompt="find photos", say=lambda t: None, action=lambda t: None, log=lambda t: None,
                                      file=lambda p: None, started=lambda **k: None, usage=lambda **k: None)
            try:
                await asyncio.wait_for(w.run(r), 30)
                raise AssertionError("an agent that goes silent mid-turn must be stopped, not left running")
            except WorkerError as e:
                assert "went quiet" in str(e) and "browser_exec: search" in str(e), e
            assert w.proc.returncode is not None, "and its process is gone"

            class StandInHermes(Hermes):  # Hermes with a stand-in process: its own browser tool ends the task at once (HERMES_OWN_BROWSER, 2026-10-20)
                def argv(self):
                    return [sys.executable, str(stuck)]

                def mcp(self):
                    return []
            w = StandInHermes()
            logged = []
            r = types.SimpleNamespace(cwd=d, prompt="find photos", say=lambda t: None, action=lambda t: None, log=logged.append,
                                      file=lambda p: None, started=lambda **k: None, usage=lambda **k: None)
            try:
                await asyncio.wait_for(w.run(r), 30)
                raise AssertionError("Hermes reaching for its own browser must be stopped, not left to run out of sight")
            except WorkerError as e:
                assert "its own hidden browser (browser_exec: search)" in str(e) and "your own browser" in str(e), e
            assert w.proc.returncode is not None and any("reached for its own browser" in x for x in logged), (w.proc.returncode, logged)

            busy = Path(d) / "busy_acp.py"  # the whole-turn cap, quickly: an agent that never stops acting still ends, and says why
            busy.write_text(BUSY_ACP, encoding="utf-8")
            global ACP_MAX_S
            keep_max, ACP_MAX_S = ACP_MAX_S, 0.8
            w = worker_for("busy", {"busy": {"key": "busy", "name": "Busy", "argv": [sys.executable, str(busy)], "mcp": False}})
            acts = []
            r = types.SimpleNamespace(cwd=d, prompt="click forever", say=lambda t: None, action=acts.append, log=lambda t: None,
                                      file=lambda p: None, started=lambda **k: None, usage=lambda **k: None)
            try:
                await asyncio.wait_for(w.run(r), 30)
                raise AssertionError("an agent still going after ACP_MAX_S must be stopped, not left to run all day")
            except WorkerError as e:
                assert "ran for" in str(e) and "so I stopped it" in str(e) and "mcp__computer__click" in str(e) and "quiet" not in str(e), e
            finally:
                ACP_MAX_S = keep_max
            assert w.proc.returncode is not None and len(acts) >= 3, ("it acted all along, and its process is gone", w.proc.returncode, len(acts))
            assert ACP_MAX_S == 1800 and ACP_MAX_S // 60 == 30, "the cap is 30 minutes of one turn"

            # offline (the owner, 2026-10-24): Hermes' session moves to the model on this PC made for it, on the same endpoint; online it
            # stays on its own; a model that isn't on this PC is never asked for
            switch = Path(d) / "switch_acp.py"
            switch.write_text(SWITCH_ACP, encoding="utf-8")

            class SwitchHermes(Hermes):
                def argv(self):
                    return [sys.executable, str(switch)]

                def mcp(self):
                    return []
            for net, have, want in (("offline", True, f"on custom:{HERMES_OFFLINE_MODEL}"), ("online", True, "on custom:gemma4:31b-cloud"),
                                    ("offline", False, "on custom:gemma4:31b-cloud")):
                here = [{"name": HERMES_OFFLINE_MODEL, "remote": False}] if have else []
                core = types.SimpleNamespace(health=types.SimpleNamespace(
                    internet=lambda net=net: asyncio.sleep(0, {"status": net}),
                    ollama=lambda here=here: asyncio.sleep(0, {"status": "online", "models": here})))
                seen, said = {}, []
                r = types.SimpleNamespace(cwd=d, prompt="open notepad", say=lambda t: None, action=lambda t: None, log=said.append,
                                          file=lambda p: None, started=lambda **k: seen.update(k), usage=lambda **k: None, core=core)
                out = await asyncio.wait_for(SwitchHermes().run(r), 30)
                assert out == want and seen.get("model") == want[3:], (net, have, out, seen)
                assert any("Offline:" in x for x in said) == (want != "on custom:gemma4:31b-cloud"), said

            async def yes(kind, title, detail, **k):
                return True
            entry = {"stand_in": {"key": "stand_in", "name": "Stand-in", "argv": [sys.executable, str(script)], "mcp": False}}
            w = worker_for("stand_in", entry)  # a spare: started ahead, then the next task's (no second initialize, no second session)
            warm(w, d)
            ahead = (await asyncio.wait_for(_spares["stand_in"], 30))["proc"]
            r = types.SimpleNamespace(cwd=d, prompt="list the folder", say=lambda t: None, action=lambda t: None, log=lambda t: None,
                                      file=lambda p: None, started=lambda **k: None, usage=lambda **k: None, ask=yes)
            assert await asyncio.wait_for(w.run(r), 30) == "ran dir" and w.proc is ahead and "stand_in" not in _spares, "the spare was used"
            warm(w, d)
            ahead = (await asyncio.wait_for(_spares["stand_in"], 30))["proc"]
            elsewhere = Path(d) / "elsewhere"
            elsewhere.mkdir()
            w = worker_for("stand_in", entry)
            assert await asyncio.wait_for(w.run(types.SimpleNamespace(**{**vars(r), "cwd": str(elsewhere)})), 30) == "ran dir"
            await asyncio.wait_for(ahead.wait(), 10)
            assert w.proc is not ahead and ahead.returncode is not None, "a task in another folder starts its own; the spare is stopped"
            drop_spares()
            try:
                worker_for("nobody", {})
                raise AssertionError("an unknown agent must fail")
            except WorkerError:
                pass
            w = worker_for("gone", {"gone": {"key": "gone", "name": "Gone", "argv": None}})
            try:
                await w.run(types.SimpleNamespace(cwd=d))
                raise AssertionError("an engine whose command isn't here must say so")
            except WorkerError as e:
                assert "not installed" in str(e)
    asyncio.run(acp_engine())
    assert isinstance(worker_for("hermes"), Hermes) and Hermes.label == "Monday's agent", "Hermes is the same ACP worker; the owner reads it as Monday's agent"
    seen_401 = ("Kilo Code rejected your API key, so the model can't be reached. Update it in Settings → Providers, or run `hermes setup` "
                "in a terminal.\n\nProvider said: HTTP 401: Your authentication token is invalid. Please sign in again.")  # TASK-2026-0201
    assert HERMES_FAILED.search(seen_401) and HERMES_FAILED.search("Kilo Code didn't answer after 3 attempts.\n\nProvider said: x")
    assert HERMES_FAILED.search("Model 'x' isn't available on Ollama. Pick a different model with /model (or `hermes model`).")
    for answer in ("READY", "Done: Notepad is open and says hello.", "The details are below.\n\nDetails: 3 files changed.",
                   "I tried twice; the second attempt worked.", "The provider said yes."):
        assert not HERMES_FAILED.search(answer), ("an ordinary answer is never taken for Hermes' failure copy", answer)
    for own in ("browser_navigate: https://youtube.com", "browser_exec: search", "browser_click: ref 3", "browser_snapshot"):
        assert HERMES_OWN_BROWSER.match(own), own
    for ok in ("mcp__computer__launch_app: chrome", "mcp__computer__get_window_state", "web_search: cats", "terminal: dir", "Thinking"):
        assert not HERMES_OWN_BROWSER.match(ok), ("the desktop tools and the web fetchers are not a browser of Hermes' own", ok)
    assert Hermes.own_browser_blocked and not AcpEngine.own_browser_blocked and not KiloCode.own_browser_blocked, "a coding engine may browse docs"
    assert "launch_app {\"urls\"" in Hermes().note([{"name": "computer"}]) and "NEVER use browser_navigate" in Hermes().note([{"name": "computer"}])
    # what a task holds while it runs (Monday.lease): the screen for every agent that works the desktop (Hermes, Monday's own hands, an
    # editor's own agent), a project's folder for the coders, nothing for chat and research; an engine by its entry (data/engines.json)
    here = os.getcwd()
    for k in ("hermes", "computer", *HANDOFF):
        assert resource_of(k) == "screen" and resource_of(k, here) == "screen", k
    for k in HEADLESS:
        assert resource_of(k, here) == f"project:{os.path.normcase(here)}" and resource_of(k) is None, (k, resource_of(k, here))
    assert resource_of("codex", "proj") == f"project:{os.path.normcase(os.path.abspath('proj'))}", "a relative folder is made absolute"
    if sys.platform == "win32":
        assert resource_of("codex", here.upper()) == resource_of("claude_code", here.lower()), "one folder, however its case is written"
    assert resource_of("chat") is None and resource_of("research", here) is None and resource_of("nobody", here) is None
    engines = {"openclaw": {"key": "openclaw", "name": "OpenClaw", "cli": "openclaw acp", "argv": None, "coding": True, "mcp": False, "source": "engines.json"},
               "desk": {"key": "desk", "name": "Desk", "cli": "desk acp", "argv": None, "coding": False, "mcp": True, "source": "engines.json"},
               "talk": {"key": "talk", "name": "Talk", "cli": "talk acp", "argv": None, "coding": False, "mcp": False, "source": "engines.json"}}
    assert resource_of("openclaw", here, engines) == f"project:{os.path.normcase(here)}" and resource_of("openclaw", None, engines) is None
    assert resource_of("desk", here, engines) == "screen" and resource_of("talk", here, engines) is None
    assert resource_of("hermes", None, {"hermes": {"coding": True, "mcp": False}}) == "screen", "a built-in agent's resource is never an entry's to change"
    assert set(RESOURCE) == {"hermes", "computer", *HANDOFF, *HEADLESS} and set(RESOURCE.values()) == {"screen", "project"}
    print("workers self-check: OK")
