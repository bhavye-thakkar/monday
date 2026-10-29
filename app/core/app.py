"""HTTP layer for Monday Core: the REST API and one SSE event stream, for Monday's own window (core/qtwindow.py, through native.Core). No pages.

Trust model (the owner, 2026-10-15: "fully desktop app only", "its own messaging system only")
- Only this computer: the loopback, no proxy headers. Anything else is refused; there is no pairing, no other device, no tunnel.
- Host allowlist (DNS rebinding) and Origin/Sec-Fetch-Site checks (drive-by requests from websites to 127.0.0.1) guard every
  state-changing call.
- Owner-only calls (2026-10-17): answering an approval, answering an agent's permission request, installing a tool, writing a key and
  adding or removing an engine need the window token (`owner_only`): any other process on this computer, an agent's own `curl` included,
  is refused. (Reading OpenMausBot showed its API let any local process approve its own request; Monday's did too.)
"""
import asyncio
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import bots, chrome, google, memory, probes, providers, router, side, speech, tools
from . import jev as jevmod  # app.py has an endpoint named jev
from .monday import ROOT, TEST, VERSION, Monday

PORT = int(os.environ.get("MONDAY_PORT", "8765"))
HOST = "127.0.0.1"  # this computer only (2026-10-15: no phone, no LAN, no tunnel)
LOADED: dict = {}  # the files of Monday's own modules this Core runs, and their mtimes when it started (stale: GET /api/hello)


def stale():
    """Has the code this Core runs changed on disk since it started? Only its own modules count: the window's (qtwindow, qtnotch…) don't."""
    for path, at in LOADED.items():
        try:
            if os.stat(path).st_mtime > at + 1:
                return True
        except OSError:
            return True
    return False
UPLOADS = ROOT / "data" / "uploads"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
SSE_PING_S = 15
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip")
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
ACTIVITY_MAX = 200
WINDOW_HEADER = "x-monday-window"  # the window token: core/native.py's Core client sends it on every call, from window.token

core: Monday = None
window_token = None  # made at start, written beside the database (window.token); Core's workers never get it


def owner_only(request: Request, what):
    """Only Monday's own window (and its notch, the same client) may do this: it holds the token Core wrote at start. Raises 403."""
    # ponytail: the token is a file the owner's account can read, so a process that goes looking for window.token gets past this; it stops
    # an agent's own curl and anything that doesn't know Monday. Upgrade path: map the request's source port to its process
    # (GetExtendedTcpTable) and accept only the desktop app's.
    if not window_token or not hmac.compare_digest(request.headers.get(WINDOW_HEADER) or "", window_token):
        print(time.strftime("%H:%M:%S"), f"refused: a request to {what} without Monday's window token", flush=True)
        raise HTTPException(403, f"Only Monday's own window can {what}.")


def is_local(request: Request):
    """This computer itself: the loopback, with no proxy in between."""
    ip = request.client.host if request.client else ""
    return ip in ("127.0.0.1", "::1") and not any(h in request.headers for h in PROXY_HEADERS)


def source_of(request: Request):
    """The guard lets nothing but this computer in: Monday's window, or the preflight's tests (its header), which stay out of the
    owner's chat (Monday.TEST)."""
    return "preflight" if request.headers.get("x-monday-test") == "preflight" else "desktop"


def quiet_resets(loop, ctx):
    """The event loop's error reports, less one: Windows' proactor telling of a client that hung up (WinError 10054 in
    _call_connection_lost, CPython gh-83191) with a traceback, 74 of core.log's 81 by 2026-10-26, nothing broken. Any other goes on."""
    if isinstance(ctx.get("exception"), ConnectionResetError) and "_call_connection_lost" in repr(ctx.get("handle")):
        return
    loop.default_exception_handler(ctx)


@asynccontextmanager
async def lifespan(app):
    global core, window_token
    asyncio.get_running_loop().set_exception_handler(quiet_resets)
    core = Monday()
    here = os.path.dirname(os.path.abspath(__file__))
    LOADED.update({f: os.stat(f).st_mtime for f in (getattr(m, "__file__", None) for m in list(sys.modules.values()))
                   if f and os.path.dirname(os.path.abspath(f)) == here})
    window_token = secrets.token_urlsafe(24)  # new each start: the window re-reads the file when it changes
    (core.db.path.parent / "window.token").write_text(window_token, encoding="utf-8")
    await core.start()
    print(f"\n  {core.name} Core {VERSION} on http://127.0.0.1:{PORT} (for Monday's window, core/qtwindow.py; no pages, this computer only)")
    yield
    await core.shutdown()


app = FastAPI(title="Monday Core", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").lower()
    hostname = host[1:host.index("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    if hostname not in ALLOWED_HOSTS:
        return JSONResponse({"error": "host_not_allowed"}, 421)
    if not is_local(request):
        return JSONResponse({"error": "this_computer_only"}, 403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc.lower() != host:
            return JSONResponse({"error": "cross_origin"}, 403)
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return JSONResponse({"error": "cross_site"}, 403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


# -- no pages: "/" is only where Google's consent screen sends the owner back (the OAuth loopback redirect) ---------------------
@app.get("/")
async def index(state: str | None = None, code: str | None = None, error: str | None = None):
    if state:
        return await google_back(state, code, error)
    return PlainTextResponse("Monday is a Windows app: open it from the Start menu or its tray icon. It has no web pages.")


async def google_back(state, code, error):
    def said(text, ok):
        return PlainTextResponse(f"{text}\n\nYou can close this tab and go back to Monday.", 200 if ok else 400)
    if error or not code:
        core.emit("GOOGLE_CHANGED", {"status": core.google.status(), "event": "declined"})
        return said(f"Google wasn't connected. Google said: {error or 'no code'}. Nothing was connected.", False)
    try:
        acct = await core.google.finish(state, code)
    except google.GoogleError as e:
        return said(f"Google wasn't connected. {e}", False)
    st = core.google.status()
    got = [x["label"] for x in st["services"] if x["status"] == "connected"]
    core.receipt(None, "google.connect", "success", {"email": acct.get("email"), "services": got, "scopes": acct.get("scopes")})
    core.emit("GOOGLE_CHANGED", {"status": st, "event": "connected"})
    return said(f"Google is connected. {acct.get('email') or 'Your account'}: {', '.join(got) or 'nothing yet'}.", True)


@app.get("/api/hello")
async def hello():
    """The handshake (the desktop app's, 2026-10-23): who answers, from which app folder, whether the code it runs has changed on disk
    since it started (`stale`: core/desktop.py restarts it once it is idle, when the owner launches Monday), whether it is busy (a task, a
    chat or a plan running), and how ready it is from its own checks (Monday.readiness: starting, ready or degraded; the internet apart)."""
    return {"name": core.name, "version": VERSION, "auth": "local", "pid": os.getpid(), "root": str(ROOT), "stale": stale(),
            "busy": core.busy(), **core.readiness()}


@app.post("/api/startup")
async def startup():
    """The start-up check (Monday.startup_check): the window asks it each time the owner opens Monday. Everything preflight looks at is
    started if it isn't and checked again now -> {"ok", "title", "words", "ready", "needs", "down", "unset", "offline"}."""
    return await core.startup_check()


def require_local(request: Request):
    if not is_local(request):
        raise HTTPException(403, "Only available on the Monday computer itself.")


# -- status -----------------------------------------------------------------------------------
@app.get("/api/status")
async def status(request: Request):
    snap = await core.snapshot()
    snap["client"] = {"local": is_local(request), "source": source_of(request)}
    return snap


@app.get("/api/system")
async def system():
    return core.system or await core.metrics.sample()


@app.get("/api/hermes")
async def hermes():
    return core.hermes_view()


@app.get("/api/jev")
async def jev():
    """Jev health, latency percentiles, distributions, recent decisions and the policy thresholds. Never the key."""
    import statistics as _statistics
    result = await core.jev_view()
    j = core.jev
    result["prepass"] = {
        "calls": j._prepass_calls,
        "complete_fires": j._prepass_complete_fires,
        "early_dispatches": j._prepass_early_dispatches,
        "avg_latency_ms": round(_statistics.mean(j._prepass_latencies), 1) if j._prepass_latencies else None,
    }
    return result


class SettingsIn(BaseModel):
    assistant_name: str | None = None
    default_coding_agent: Literal["claude_code", "codex", "gemini_cli", "kilo", "grok_build", "fastest"] | None = None
    default_computer_agent: Literal["computer", "hermes"] | None = None
    default_browser: str | None = None
    default_editor: str | None = None
    default_terminal: str | None = None
    routing: Literal["jev", "rules"] | None = None
    # who reads what the mic hears (core/stt.py reader_for): Google's free recognizer, Whisper alone on this PC (from the next sentence),
    # or Windows' dictation (after a restart: a different ear)
    default_stt: Literal["google", "local", "windows"] | None = None
    jev_images: bool | None = None  # Jev may look at pictures of the screen (its image pack): checking work, finding icons
    restore_points: bool | None = None  # Dotpals' restore points (core/restore.py): a request's files at its start and end, to rewind
    approval_mode: Literal["ask", "always", "auto"] | None = None  # router.standing; only from Monday's own window
    vision_cloud: bool | None = None  # a camera picture OpenCV can't name goes to a model that can see (Ollama's cloud): off by default
    remote_access: bool | None = None  # the phone companion's listener (core/remote.py, docs/adr/0009): off by default; the window only


@app.get("/api/settings")
async def get_settings():
    return {"assistant_name": core.name, **core.defaults()}


# -- the chat model: the owner's choice (the composer's picker; "use Gemini" by voice goes through Monday.cmd_model) ------------------
class ModelIn(BaseModel):
    id: str


@app.get("/api/models")
async def models():
    """Every chat model Monday can use (Ollama's, the providers'), where it runs, whether it is there, what it can do, which is chosen."""
    return await core.models(wait=False)


@app.post("/api/models/select")
async def select_model(body: ModelIn, request: Request):
    """Switch the chat model to a registry entry. Checked against the registry (never by asking a model); 409 when it isn't there."""
    r = await core.select_model(body.id.strip()[:200], by=source_of(request))
    if not r["ok"]:
        raise HTTPException(409, r["text"])
    return r


@app.post("/api/settings")
async def settings(body: SettingsIn, request: Request):
    if body.approval_mode is not None:  # what may be approved without asking: the owner's window only, never an agent's call
        owner_only(request, "change how approvals work")
    if body.remote_access is not None:  # a way in: the owner's window only, like approvals
        owner_only(request, "turn phone access on or off")
    if body.assistant_name is not None:
        name = re.sub(r"\s+", " ", body.assistant_name).strip()[:24]
        if not name:
            raise HTTPException(400, "Name can't be empty.")
        core.db.set_setting("assistant_name", name)
        core.emit("SETTINGS_CHANGED", {"assistant_name": name})
    changed = {}
    for field, key in (("default_coding_agent", "default_coder"), ("default_computer_agent", "default_computer_agent"),
                       ("default_browser", "default_browser"), ("default_editor", "default_editor"),
                       ("default_terminal", "default_terminal"), ("routing", "routing"), ("default_stt", "default_stt")):
        v = getattr(body, field)
        if v is None:
            continue
        if field in ("default_browser", "default_editor", "default_terminal"):
            app_ = await core.computer.apps.find(v)
            if not app_:
                raise HTTPException(400, f"{v} isn't installed or couldn't be located.")
            v = app_["name"].lower()
        core.db.set_setting(key, v)
        changed[field] = v
    for key in ("jev_images", "approval_mode", "restore_points", "vision_cloud"):
        if getattr(body, key) is not None:
            core.db.set_setting(key, getattr(body, key))
            changed[key] = getattr(body, key)
    if body.remote_access is not None:
        core.db.set_setting("remote_access", bool(body.remote_access))
        changed["remote_access"] = bool(body.remote_access)
        if body.remote_access:
            await core.remote.start()  # False with core.remote.error in words: GET /api/remote shows it; the setting stays theirs
        else:
            await core.remote.stop()
    if changed:
        core.receipt(None, "settings.update", "success", changed)
        core.emit("SETTINGS_CHANGED", {"defaults": core.defaults()})
        if "approval_mode" in changed:
            core.emit("APPROVALS_CHANGED", core.approvals(), persist=False)
    return {"assistant_name": core.name, **core.defaults()}


# -- tools, AI tools, apps ----------------------------------------------------------------------------------------
@app.get("/api/tools")
async def tool_registry():
    """Monday's own tools with live status (real checks) and each capability's risk and whether it's built."""
    return {"tools": tools.listing(await core.tool_status()), "statuses": tools.STATUSES}


class ShutdownIn(BaseModel):
    restart: bool = False


@app.post("/api/shutdown")
async def shutdown(body: ShutdownIn, request: Request):
    """The desktop app's Quit and Restart (core/desktop.py): this computer only. Running tasks are stopped, as on any exit."""
    require_local(request)
    core.receipt(None, "core.restart" if body.restart else "core.quit", "requested", {"by": "desktop app"})
    core.spawn(core.restart("the desktop app") if body.restart else core.quit())
    return {"ok": True, "restarting": body.restart}


@app.get("/api/learned")
async def learned():
    """What Monday learned from its own outcomes (core/learn.py): app names as heard, recipes, the browser answer. No secrets in it."""
    return core.learn.summary()


@app.get("/api/agents/watch")
async def agents_watch():
    """The coding agents the owner runs themselves, as the notch shows them (core/watch.py; read from their own logs, never stored)."""
    return {"sessions": core.watched}


class AskIn(BaseModel):
    tool_name: str = ""
    tool_input: dict = {}
    cwd: str = ""
    session_id: str = ""
    agent: str = "Claude Code"
    wait_s: float = 0.0  # how long the hook waits for the owner (its WAIT_S): the notch counts it down


@app.post("/api/agents/ask")
async def agents_ask(body: AskIn):
    """One of the owner's agents asking permission (Claude Code's PermissionRequest hook, core/approve_hook.py) -> {"id"} to wait on.
    The notch shows it; only the owner answers it."""
    return {"id": core.ask_open(body.model_dump())}


@app.get("/api/agents/ask/{aid}")
async def agents_ask_wait(aid: str, wait: float = 25.0):
    """The hook waiting for the owner's answer, at most `wait` seconds a call: {"decision": "allow" | "deny" | "gone" | null,
    "answers": the owner's picks for Claude Code's questions, or null}."""
    d = await core.ask_wait(aid, max(0.0, min(wait, 30.0)))
    return {"decision": d, "answers": (core.asks.get(aid) or {}).get("answers") if d == "allow" else None}


class HookEventIn(BaseModel):  # what core/agent_hooks.py passes on from Claude Code's hook event
    transcript_path: str = ""
    session_id: str = ""
    stop_hook_active: bool = False
    command: str = ""
    file_path: str = ""


@app.post("/api/agents/stop")
async def agents_stop(body: HookEventIn):
    """Claude Code's Stop hook: {"reason"} sends it back to finish (the tests failed after its change, or it claims a pass its log doesn't
    show; at most watch.FIX_TRIES a request). Advice read from its own log; nothing it says is acted on here."""
    return await core.agent_stop(body.model_dump())


@app.post("/api/agents/gate")
async def agents_gate(body: HookEventIn):
    """Its PreToolUse hook on `git commit` / `git push`: deny while its tests fail, ask the owner when none passed since its change."""
    return await core.agent_gate(body.model_dump())


@app.post("/api/agents/guard")
async def agents_guard(body: HookEventIn):
    """Its PreToolUse hook on an edit: ask the owner first when another of their sessions edited that file in the last watch.GUARD_S."""
    return await core.agent_guard(body.model_dump())


class CheckIn(BaseModel):
    cwd: str = ""


@app.post("/api/agents/check")
async def agents_check(body: CheckIn):
    """Claude Code's "check my work" (an MCP tool): Monday runs a known project's tests and says what they show."""
    return await core.check_work(body.cwd)


class AskAnswerIn(BaseModel):
    decision: Literal["allow", "deny", "always", "gone"]
    answers: dict[str, str] | None = None  # Claude Code's questions: each question → the owner's pick (a multi-select's joined)


@app.post("/api/agents/ask/{aid}")
async def agents_ask_answer(aid: str, body: AskAnswerIn, request: Request):
    """The owner's answer (Allow / Deny in the notch or the window, Ctrl+Alt+Y / Ctrl+Alt+N; a pick for each of Claude Code's
    questions), or "gone": the hook stopped waiting (or the owner chose to answer in Claude Code itself)."""
    from . import watch
    if body.decision != "gone":  # the hook itself only ever says "gone"
        owner_only(request, "answer an agent's request")
    if body.decision == "always" and aid in core.asks and not core.asks[aid].get("always"):
        raise HTTPException(409, "This request can't be always allowed: allow or deny it.")
    a = core.asks.get(aid) or {}
    if a.get("questions") and body.decision == "allow" and not watch.answers_cover(a["questions"], body.answers):
        raise HTTPException(400, "Pick an answer to each of its questions (or answer in Claude Code).")
    if not core.ask_answer(aid, body.decision, body.answers):
        raise HTTPException(404, "That request isn't waiting any more.")
    return {"ok": True}


@app.get("/api/agents/chat")
async def agents_chat(key: str):
    """A session the notch shows, as a chat: the owner's words and the agent's own, its last turns (core/watch.py chat_turns; read on
    this ask only, never stored). The key must be one the watch is showing: matched, never opened as given."""
    agent = next((s["agent"] for s in core.watched if s.get("key") == key), None)
    if not agent:
        raise HTTPException(404, "That isn't a session Monday is watching now.")
    from . import watch
    turns = await asyncio.to_thread(watch.chat_turns, key, agent)
    return {"agent": agent, "turns": turns + core.notes_waiting(key, [t["text"] for t in turns if t["who"] == "you"])}


class SayIn(BaseModel):
    key: str
    text: str


@app.post("/api/agents/say")
async def agents_say(body: SayIn, request: Request):
    """The notch's "Write to": the owner's words for a session it shows: a Claude Code session's own hooks hand them over before its
    next step, or its wake hook once its turn is over (Monday.say_to_agent); a Codex session gets them through Codex's own queue
    (Monday.write_to: `codex queue`). -> {"queued", "how", "why"}; not queued: the notch copies the words for the owner to paste."""
    owner_only(request, "write to an agent")
    try:
        return await core.write_to(body.key, body.text)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/agents/notes")
async def agents_notes(body: HookEventIn):
    """Its PreToolUse hook or its wake hook: the owner's words waiting for this session, handed over once (Monday.take_notes), or None."""
    return {"note": core.take_notes(body.transcript_path)}


class WaitIn(HookEventIn):
    since: float = 0.0  # when that wake hook began waiting (the newest one per session listens)


@app.post("/api/agents/waiting")
async def agents_waiting(body: WaitIn):
    """Its wake hook (core/agent_hooks.py wait), every agent_hooks.BEAT_S: it listens for this session's words -> {"stop"}: True when a
    newer one took over (Monday.agent_waiting)."""
    return core.agent_waiting(body.model_dump())


@app.get("/api/agents/hooks")
async def agents_hooks():
    """Which of Monday's hooks the owner's Claude Code has (core/agent_hooks.py installed: approvals from the notch, the Stop fix loop,
    the commit gate, the MCP check), read only, for the notch's Home card and Settings."""
    from . import agent_hooks
    return await asyncio.to_thread(agent_hooks.installed)


@app.get("/api/agents/brief")
async def agents_brief():
    """Dotpals' morning brief: what the owner's agents did since yesterday began, in one line ("" when nothing), read from their logs."""
    from . import watch
    projects = await asyncio.to_thread(watch.brief)
    return {"line": watch.brief_line(projects), "projects": projects, "date": time.strftime("%Y-%m-%d")}


@app.get("/api/routines")
async def routines_list(start: float | None = None, end: float | None = None):
    """OpenMausBot's routines: each with its schedule in words, the week's receipts and its times still to come (the calendar), the bots."""
    return core.routines_view(start, end)


class RoutineIn(BaseModel):
    name: str | None = None
    prompt: str | None = None
    bot: str | None = None
    enabled: bool | None = None
    schedule: dict | None = None
    minutes: int | None = None


def _routine_bot(body):
    if body.bot is not None and not bots.find(core.db, body.bot):
        raise HTTPException(400, f"{body.bot} isn't one of your bots.")


@app.post("/api/routines")
async def routine_add(body: RoutineIn, request: Request):
    """A new routine: a bot's prompt on a schedule, run unattended; so Monday's own window only, as a webhook is."""
    owner_only(request, "add a routine")
    _routine_bot(body)
    try:
        r = core.routines.create(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    core.receipt(None, "routine.add", "success", {"name": r["name"], "bot": r["bot"]})
    core.emit("ROUTINES_CHANGED", {}, persist=False)
    return r


@app.post("/api/routines/{rid}")
async def routine_change(rid: str, body: RoutineIn, request: Request):
    owner_only(request, "change a routine")
    _routine_bot(body)
    try:
        r = core.routines.update(rid, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not r:
        raise HTTPException(404, "No such routine.")
    core.emit("ROUTINES_CHANGED", {}, persist=False)
    return r


@app.delete("/api/routines/{rid}")
async def routine_delete(rid: str, request: Request):
    owner_only(request, "delete a routine")
    if not core.routines.remove(rid):
        raise HTTPException(404, "No such routine.")
    core.receipt(None, "routine.delete", "success", {"id": rid})
    core.emit("ROUTINES_CHANGED", {}, persist=False)
    return {"ok": True}


@app.post("/api/routines/{rid}/run")
async def routine_run(rid: str, request: Request):
    """Run now: queued like a scheduled run, started when its bot is free."""
    owner_only(request, "run a routine")
    run = core.routines.run_now(rid)
    if not run:
        raise HTTPException(404, "No such routine.")
    core.emit("ROUTINE_RUN", {"run": run}, persist=False)
    return run


@app.post("/api/routines/runs/{run_id}/cancel")
async def routine_cancel(run_id: str, request: Request):
    """A run cancelled: one still queued never starts; one going is marked cancelled (its task has its own Stop)."""
    owner_only(request, "cancel a routine's run")
    run = core.routines.cancel(run_id)
    if not run:
        raise HTTPException(404, "That run isn't queued or going.")
    core.emit("ROUTINE_RUN", {"run": run}, persist=False)
    return run


@app.get("/api/rewind")
async def rewind_points(key: str):
    """The restore points of a session the notch shows (Settings → Restore points): its requests that can be rewound, newest first."""
    return {"on": core.setting("restore_points", False) is True, "points": core.rewind_points(key)}


class RewindIn(BaseModel):
    id: str
    paths: list[str] = []
    now: str = ""


@app.post("/api/rewind/preview")
async def rewind_preview(body: RewindIn):
    """What rewinding the request would do now: each file's change, the diff back to before, and the tree a rewind must still find."""
    try:
        return await core.rewind_preview(body.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/rewind")
async def rewind(body: RewindIn, request: Request):
    """The picked files put back as they were before the request, only if nothing changed since the preview. Monday's window only."""
    owner_only(request, "rewind a request")
    try:
        return await core.rewind(body.id, body.paths, body.now)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/rewind/undo")
async def rewind_undo(body: RewindIn, request: Request):
    """The rewind undone: those files as they were just before it. Monday's window only."""
    owner_only(request, "undo a rewind")
    try:
        return await core.rewind_undo(body.id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/agents/handoff")
async def agents_handoff_note(key: str):
    """Dotpals' hand-off note for a session the notch shows (Continue in ▾, Copy), and the agents it can go to here."""
    try:
        return await core.handoff(key)
    except ValueError as e:
        raise HTTPException(404, str(e))


class HandoffIn(BaseModel):
    key: str
    agent: str


@app.post("/api/agents/handoff")
async def agents_handoff(body: HandoffIn, request: Request):
    """Continue in: the note saved and the agent started in the session's own folder, in a console of its own. Monday's window only."""
    owner_only(request, "start an agent")
    try:
        return await core.handoff(body.key, body.agent)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/agents/story")
async def agents_story(key: str):
    """Dotpals' Story: today's work of one session the notch shows now, in plain words (its log read whole on this ask only, never
    stored). The key must be one the watch is showing: it is matched, never opened as given."""
    agent = next((s["agent"] for s in core.watched if s.get("key") == key), None)
    if not agent:
        raise HTTPException(404, "That isn't a session Monday is watching now.")
    from . import watch
    entries, s = await asyncio.to_thread(watch.story, key, agent)
    return {"agent": agent, "project": s.get("project"), "requests": len(entries),
            "lines": watch.story_words(agent, entries, s, shown=watch.STORY_SHOWN), "text": "\n".join(watch.story_words(agent, entries, s))}


@app.get("/api/ai-tools")
async def ai_tools(refresh: bool = False):
    return {"tools": await core.ai_tools_list(refresh), "checked_at": core.ai_tools_at,
            "providers": providers.registry(await core.health.ollama()),  # the chat AIs, beside the coding agents
            "engine_errors": core.engine_errors, "keys": [k for k in jevmod.ENV_KEYS if jevmod.setting(k)]}  # key names set, never their values


class SetupIn(BaseModel):
    what: str = "install"  # install (the tool's own installer) | login (its own sign-in, in a console window)


@app.post("/api/ai-tools/{key}/setup")
async def ai_tool_setup(key: str, body: SetupIn, request: Request):
    """Connections' one click (this computer only): Install streams the installer's output as TOOL_SETUP events; Sign in opens the tool's own login."""
    if not is_local(request):
        raise HTTPException(403, "Installing or signing in tools is done on this computer only.")
    owner_only(request, "install or sign in a tool")
    if body.what not in ("install", "login"):
        raise HTTPException(400, "install or login")
    try:
        return await core.setup_tool(key, body.what)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/ai-tools/{key}/open")
async def ai_tool_open(key: str, request: Request):
    """The notch's dock: the tool opened in its own window in the current project (this computer only)."""
    if not is_local(request):
        raise HTTPException(403, "Tools are opened on this computer only.")
    try:
        return await core.open_tool(key)
    except ValueError as e:
        raise HTTPException(400, str(e))


class KeyIn(BaseModel):
    name: str
    value: str


@app.post("/api/keys")
async def key_save(body: KeyIn, request: Request):
    """A key pasted in Connections goes into Monday/.env (this computer only). The answer names the key, never its value."""
    if not is_local(request):
        raise HTTPException(403, "Keys are added on this computer only.")
    owner_only(request, "add a key")
    try:
        return {"saved": await core.save_key(body.name, body.value)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class EngineIn(BaseModel):
    key: str = ""
    name: str
    cli: str
    coding: bool = True
    mcp: bool = False


@app.post("/api/engines")
async def engine_save(body: EngineIn, request: Request):
    """A custom ACP engine into data/engines.json (core/engines.py; MausBot's customAcp shape). This computer only."""
    if not is_local(request):
        raise HTTPException(403, "Engines are added on this computer only.")
    owner_only(request, "add an engine")
    try:
        return {"key": await core.save_engine(body.key or body.name, body.name, body.cli, body.coding, body.mcp)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class BotIn(BaseModel):
    name: str
    role: str = ""
    instructions: str = ""
    agent: str = "auto"
    project: str | None = None
    model: str | None = None      # None: keep what the bot has; "": Monday's own choice; else a registry id (GET /api/models)
    computer: bool | None = None  # None: keep; False: the bot never gets Hermes or Monday's own hands
    face: dict | None = None      # its face (bots.look): {"kind": a pet, "body": "#RRGGBB", "accent": "#RRGGBB"}; None: keep
    pinned: bool | None = None    # OpenMausBot's roster: at the top
    hidden: bool | None = None    # folded away at the bottom of the roster
    welcome: bool = False         # a new bot made in the editor: it says hello in the chat, with OpenMausBot's first card


@app.get("/api/bots")
async def bot_list():
    """The owner's bots (core/bots.py) in the roster's order (pinned first, hidden last), each with what it is doing now (`status`, from
    its own tasks) and its unread replies, the templates (Scout, Pixel, Forge) and the agents a bot may use."""
    return {"bots": [{**b, "status": bots.status(core.db, b), "unread": bots.unread(core.db, b)} for b in bots.roster(bots.all_(core.db))],
            "templates": bots.TEMPLATES, "kinds": list(bots.PET_KINDS), "suggested": bots.pick_name([b["name"] for b in bots.all_(core.db)]),
            "agents": {"auto": f"{core.name} decides", **{k: v for k, v in router.AGENT_NAMES.items() if k != "chat"}}}


@app.post("/api/bots")
async def bot_save(body: BotIn, request: Request):
    owner_only(request, "change a bot")  # a bot's computer access and model are the owner's to set, never an agent's
    if body.model and body.model not in {m["id"] for m in (await core.models(wait=False))["models"]}:  # never a made-up model
        raise HTTPException(400, f"{body.model} isn't a model Monday has. Pick one from the models list.")
    new = bots.find(core.db, body.name) is None
    try:
        b = bots.save(core.db, body.name, body.role, body.instructions, body.agent, body.project, core.name, body.model, body.computer,
                      body.face, body.pinned, body.hidden)
    except ValueError as e:
        raise HTTPException(400, str(e))
    core.receipt(None, "bots.save", "success", {k: b[k] for k in ("name", "role", "agent")})
    core.emit("BOTS_CHANGED", {"bots": bots.all_(core.db)}, persist=False)
    core.emit("SIDE_CHANGED", side.state(core.db), persist=False)
    if new and body.welcome:  # OpenMausBot's hello: the bot's first words and its card, in the chat (a click on a choice answers it)
        core.message("monday", "text", f"Hey, I'm {b['name']}. Nice to meet you.", meta={"bot": b["name"]})
        core.message("monday", "options", bots.WELCOME_CARD["title"], meta={"bot": b["name"], "note": bots.WELCOME_CARD["note"],
                                                                           "options": bots.WELCOME_CARD["options"]})
    return {"bot": b, "bots": bots.all_(core.db)}


@app.post("/api/bots/{name}/duplicate")
async def bot_duplicate(name: str, request: Request):
    """OpenMausBot's Duplicate: the same bot under the next free name, with a sidebar group of its own."""
    owner_only(request, "change a bot")
    try:
        b = bots.duplicate(core.db, name, core.name)
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    except ValueError as e:
        raise HTTPException(400, str(e))
    core.receipt(None, "bots.duplicate", "success", {"from": name, "name": b["name"]})
    core.emit("BOTS_CHANGED", {"bots": bots.all_(core.db)}, persist=False)
    core.emit("SIDE_CHANGED", side.state(core.db), persist=False)
    return {"bot": b, "bots": bots.all_(core.db)}


@app.post("/api/bots/{name}/seen")
async def bot_seen(name: str):
    """The owner opened this bot's conversation: its unread count starts again from now."""
    if not bots.find(core.db, name):
        raise HTTPException(404, f"No bot called {name}.")
    bots.seen(core.db, name, time.time())
    core.emit("BOTS_CHANGED", {"bots": bots.all_(core.db)}, persist=False)
    return {"ok": True}


@app.post("/api/bots/{name}/unread")
async def bot_unread(name: str):
    """OpenMausBot's Mark as unread: the bot's newest reply counts as unread again."""
    if not bots.find(core.db, name):
        raise HTTPException(404, f"No bot called {name}.")
    if not bots.unseen(core.db, name):
        raise HTTPException(409, f"{name} hasn't replied yet.")
    core.emit("BOTS_CHANGED", {"bots": bots.all_(core.db)}, persist=False)
    return {"ok": True}


# -- rooms: several bots in one conversation (core/bots.py rooms, after OpenMausBot's; every member's turn routed by Jev) ----------------
class RoomIn(BaseModel):
    id: str | None = None     # None: a new room
    name: str = ""
    members: list[str] = []
    responder: Literal["lead", "everyone", "mentions"] = "lead"
    lead: str | None = None
    bulletin: str = ""


class SayIn(BaseModel):
    text: str = ""


def _room_view(r):
    last = core.db.one("SELECT role, text, meta FROM messages WHERE json_extract(meta, '$.chatroom')=? AND kind IN ('text', 'error') "
                       "ORDER BY id DESC LIMIT 1", r["id"])
    return {**r, "line": bots.room_line(r, last)}


@app.get("/api/rooms")
async def rooms_list():
    """The owner's rooms, each with its newest line ("Scout: …"), and the ways a room's members can answer."""
    return {"rooms": [_room_view(r) for r in bots.rooms(core.db)], "responders": bots.RESPONDERS}


@app.post("/api/rooms")
async def room_save(body: RoomIn, request: Request):
    owner_only(request, "change a room")
    try:
        r = bots.room_save(core.db, body.name, body.members, body.responder, body.lead, body.bulletin, body.id)
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    except ValueError as e:
        raise HTTPException(400, str(e))
    core.receipt(None, "rooms.save", "success", {k: r[k] for k in ("id", "name", "members", "responder", "lead")})
    core.emit("ROOMS_CHANGED", {"rooms": bots.rooms(core.db)}, persist=False)
    return _room_view(r)


@app.delete("/api/rooms/{rid}")
async def room_remove(rid: str, request: Request):
    owner_only(request, "change a room")
    if not bots.room_remove(core.db, rid):
        raise HTTPException(404, "No such room.")
    core.receipt(None, "rooms.remove", "success", {"id": rid})
    core.emit("ROOMS_CHANGED", {"rooms": bots.rooms(core.db)}, persist=False)
    return {"ok": True}


@app.post("/api/rooms/{rid}/say")
async def room_say(rid: str, body: SayIn, request: Request):
    """The owner's words in a room: the members it names (@Name, @everyone), else as the room says, answer one at a time."""
    if len(body.text) > 20000:
        raise HTTPException(413, "Message too long.")
    try:
        return await core.room_say(rid, body.text, source_of(request))
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/rooms/{rid}/messages")
async def room_messages(rid: str, after: int = 0, limit: int = 200):
    """A room's conversation, oldest first: who said what (`meta.bot` on a member's reply)."""
    if not bots.room_find(core.db, rid):
        raise HTTPException(404, "No such room.")
    return {"messages": core.db.all("SELECT * FROM messages WHERE json_extract(meta, '$.chatroom')=? AND id > ? AND kind IN "
                                    "('text', 'error', 'system', 'task', 'completion', 'permission') ORDER BY id LIMIT ?",
                                    rid, after, min(max(limit, 1), 500))}


@app.get("/api/teams/export")
async def team_export(name: str = "", names: str = ""):
    """OpenMausBot's team file (`openmaus.team` v1) for these bots (comma-separated names; none: every bot not hidden): name, role,
    instructions and colour only."""
    want = [n.strip().lower() for n in names.split(",") if n.strip()]
    chosen = [b for b in bots.roster(bots.all_(core.db)) if (b["name"].lower() in want if want else not b.get("hidden"))]
    if not chosen:
        raise HTTPException(404, "No bots to put in a team file.")
    return bots.team_file(chosen, name)


@app.post("/api/teams/import")
async def team_import(request: Request):
    """A team file's members as new bots (all or none), without computer access until the owner turns it on; the window's token."""
    owner_only(request, "import a team")
    raw = await request.body()
    if len(raw) > bots.TEAM_BYTES:
        raise HTTPException(413, "A team file is at most 1 MB.")
    try:
        d = json.loads(raw or b"{}")
    except ValueError:  # not JSON, or not text at all
        raise HTTPException(400, "That file isn't JSON.")
    try:
        t, made, room = bots.team_import(core.db, d, core.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    core.receipt(None, "bots.team_import", "success", {"team": t["name"], "bots": [b["name"] for b in made], "room": room["id"]})
    core.emit("BOTS_CHANGED", {"bots": bots.all_(core.db)}, persist=False)
    core.emit("ROOMS_CHANGED", {"rooms": bots.rooms(core.db)}, persist=False)
    core.emit("SIDE_CHANGED", side.state(core.db), persist=False)
    return {"team": t["name"], "room": room, "bots": made}


@app.delete("/api/bots/{name}")
async def bot_remove(name: str, request: Request):
    owner_only(request, "change a bot")
    try:
        bots.remove(core.db, name)
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    core.receipt(None, "bots.remove", "success", {"name": name})
    core.emit("BOTS_CHANGED", {"bots": bots.all_(core.db)}, persist=False)
    return {"bots": bots.all_(core.db)}


@app.delete("/api/engines/{key}")
async def engine_remove(key: str, request: Request):
    if not is_local(request):
        raise HTTPException(403, "Engines are changed on this computer only.")
    owner_only(request, "remove an engine")
    try:
        await core.remove_engine(key)
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    return {"removed": key}


@app.get("/api/apps")
async def apps(q: str | None = None, limit: int = 300):
    items = await core.computer.apps.all()
    if q:
        hit = await core.computer.apps.find(q)
        return {"match": hit and {k: hit.get(k) for k in ("name", "kind", "running", "launch_path")}}
    return {"apps": [{k: a.get(k) for k in ("name", "kind", "running")} for a in items[:max(1, min(limit, 1000))]], "count": len(items)}


# -- voice ---------------------------------------------------------------------------------------------------------
class VoicesIn(BaseModel):
    voices: list[dict] = []
    lang: str | None = None


class VoiceSettingsIn(BaseModel):
    tts_provider: str | None = None
    voice_id: str | None = None
    speed: float | None = None
    pitch: float | None = None
    enabled: bool | None = None
    patience: str | None = None
    follow_up: bool | None = None
    filming: bool | None = None
    interaction_mode: str | None = None  # pure_agent (silent, one strip) / debug (the whole HUD, silent) / assistant (spoken replies)
    wake: bool | None = None  # the open microphone acts only on what starts with Monday's name ("Monday, …", "okay Monday"; on by default)
    wake_sure: float | None = None  # 0-1: how sure Jev must be that a sentence after the bare name was said to Monday (core/voice.py DEFAULT)


@app.get("/api/voice")
async def voice_state():
    """Settings, the browser's female voices (the HUD's list), and every speech provider with its real status."""
    heard, words = core.ear.summary()
    return {"settings": core.voice_settings(), "voices": core.voice_catalog(), "providers": await core.speech_status(),
            "stt": [{"id": core.ear.id, "label": core.ear.label, "status": heard, "detail": words}, *speech.STT],
            "prices_checked": speech.PRICES_CHECKED}


@app.get("/api/voices")
async def voice_list(provider: str | None = None):
    """One provider's voices: female ones numbered ("use voice 2"), the rest with the gender the provider states."""
    if provider and provider not in speech.TTS:
        raise HTTPException(404, f"No speech provider called {provider}.")
    female, others, err = await core.voice_list(provider)
    return {"provider": provider or core.voice_settings()["tts_provider"], "voices": female, "others": others, "error": err}


class SpeakIn(BaseModel):
    text: str
    provider: str | None = None   # a preview in another provider's voice; default: the one speaking now
    voice_id: str | None = None


@app.post("/api/tts")
async def tts(body: SpeakIn):
    """Audio of `text` in a server-side voice (Gemini, Google Cloud). The browser's own voices never come here."""
    s = core.voice_settings()
    pid = body.provider or s["tts_provider"]
    pick = (s.get("choices") or {}).get(pid) or {}
    voice_id = body.voice_id or pick.get("voice_id")
    if not voice_id:
        raise HTTPException(400, "No voice chosen for that provider yet.")
    t0 = time.perf_counter()
    try:
        audio, mime = await core.speech.synthesize(pid, voice_id, body.text, pick.get("language"))
    except speech.SpeechError as e:
        raise HTTPException(502, str(e))
    return Response(audio, media_type=mime, headers={"Cache-Control": "no-store", "X-TTS-Ms": str(round((time.perf_counter() - t0) * 1000))})


@app.post("/api/voices")
async def voices_reported(body: VoicesIn):
    """Monday's window reports the voices installed in Windows (core/native.py Speaker): the "this computer" voices."""
    return {"settings": core.voice_settings(), "voices": core.report_voices(body.voices, body.lang), "stored": True}


@app.post("/api/voice/settings")
async def voice_settings(body: VoiceSettingsIn):
    try:
        return {"settings": await core.set_voice(**body.model_dump())}
    except ValueError as e:
        raise HTTPException(400, str(e))


class SampleIn(BaseModel):
    file_id: str  # an upload (POST /api/upload): the owner's recording


@app.post("/api/voice/sample")
async def voice_sample(body: SampleIn, request: Request):
    """The owner's uploaded recording becomes Monday's voice (core/speech.py Custom; the voice panel's Replace sample). This computer only."""
    if not is_local(request):
        raise HTTPException(403, "Monday's voice is set from this computer only.")
    f = core.db.one("SELECT * FROM files WHERE id=?", body.file_id)
    if not f:
        raise HTTPException(404, "No upload with that id.")
    return {"message": await core.set_voice_sample(f["path"]), "settings": core.voice_settings()}


# -- latency -------------------------------------------------------------------------------------------------------
class LatencyIn(BaseModel):
    message_id: int
    marks: dict[str, float]


@app.post("/api/latency")
async def latency_marks(body: LatencyIn, request: Request):
    allowed = {"tts_start", "tts_first_audio", "tts_end", "stt_start", "stt_open", "stt_speech", "stt_first_partial", "stt_last_word", "stt_final",
               "sent", "prepass_fired", "rendered"}  # rendered: the page drew the first reply to a spoken command
    core.db.mark(body.message_id, source_of(request), **{k: round(v) for k, v in body.marks.items() if k in allowed})
    return {"ok": True}


@app.get("/api/latency")
async def latency():
    return core.latency_stats()


@app.get("/api/stats")
async def stats(span: Literal["day", "week", "month", "all"] = "week"):
    """The window's numbers (Home's cards, Statistics): counted from SQLite, nothing estimated (Monday.stats)."""
    return core.stats(span)


# -- brain -----------------------------------------------------------------------------------------------------------
@app.get("/api/brain")
async def brain(focus: str | None = None):
    return memory.graph(core.db, core.ai_tools or [], (focus or "").strip() or None, core.name)


# -- messages & commands ----------------------------------------------------------------------
class CommandIn(BaseModel):
    text: str = ""
    client_id: str | None = None
    attachments: list[str] = []
    timing: dict[str, float] | None = None  # the HUD's speech marks (epoch ms) for latency traces
    utterance: str | None = None            # the spoken sentence this is the end of: its first clause may have run early


@app.post("/api/command")
async def command(body: CommandIn, request: Request):
    if len(body.text) > 20000:
        raise HTTPException(413, "Message too long.")
    try:
        return await core.command(body.text, source_of(request), body.client_id, body.attachments, body.timing, (body.utterance or "")[:64] or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


class EarlyIn(BaseModel):
    text: str = ""       # the words so far, still being said
    utterance: str = ""  # one id per spoken sentence; its clauses start one after another as they become exact
    held: int = 0        # how long the words have held still (ms): an action starts early only after router.EARLY_HOLD_MS


@app.post("/api/early")
async def early(body: EarlyIn, request: Request):
    """Early execution: the first clause of a sentence still being said starts now when it is closed, exact, safe and
    what it opens exists (Monday.early). Everything else waits for the whole sentence."""
    if len(body.text) > 2000:
        raise HTTPException(413, "Sentence too long.")
    return await core.early(body.text, body.utterance[:64], source_of(request), max(0, min(int(body.held), 60000)))


class PrepassIn(BaseModel):
    text: str = ""
    final: bool = False            # the sentence as it was finished: kept in the log with every answer
    filming: bool | None = None    # default: the mode Monday holds; a drill can ask either way without changing it


@app.post("/api/prepass")
async def prepass(body: PrepassIn):
    """Jev's read of one sentence while it is being said (the HUD's strip): finished? aimed at Monday? high stakes?
    In filming mode also `verdict`: whether Monday answers. Outside it nothing here sends, holds or blocks a command."""
    if len(body.text) > 2000:
        raise HTTPException(413, "Sentence too long.")
    return await core.prepass(body.text, body.final, body.filming)


@app.get("/api/messages")
async def messages(request: Request, before: int | None = None, limit: int = 50, after: int | None = None):
    """The thread, newest page first (`before`), or one chat's messages from its start (`after`, with `before` its end). The preflight's
    tests and Monday's replies to them aren't the owner's conversation: they're left out, except for the preflight reading its own."""
    limit = max(1, min(limit, 200))
    cut = "" if source_of(request) == TEST else f" AND source IS NOT '{TEST}'"  # TEST is a constant, never a request's words
    cut += " AND json_extract(meta, '$.chatroom') IS NULL"  # a room's conversation is its own (GET /api/rooms/{id}/messages)
    if after is not None:
        rows = core.db.all(f"SELECT * FROM messages WHERE id > ? AND id < ?{cut} ORDER BY id LIMIT ?", after, before or 2**62, limit)
        return {"messages": [core.enrich(m) for m in rows], "has_more": len(rows) == limit}
    rows = core.db.all(f"SELECT * FROM messages WHERE id < ?{cut} ORDER BY id DESC LIMIT ?", before or 2**62, limit)
    return {"messages": [core.enrich(m) for m in rows[::-1]], "has_more": len(rows) == limit}


@app.get("/api/chats")
async def chats():
    return {"chats": core.chats()}


@app.post("/api/chats/new")
async def new_chat():
    """Start a new chat: the chat model starts afresh after this line; the earlier chats stay in the thread and in /api/chats."""
    return {"message": core.new_chat(), "chats": core.chats()}


@app.get("/api/search")
async def search(q: str, limit: int = 30):
    q = q.strip()
    if not q:
        return {"messages": [], "tasks": [], "projects": []}
    like = f"%{q}%"
    return {
        "messages": core.db.all("SELECT * FROM messages WHERE text LIKE ? AND source IS NOT ? ORDER BY id DESC LIMIT ?", like, TEST, limit),
        "tasks": core.db.all("SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
                             "WHERE t.id LIKE ? OR t.title LIKE ? OR t.prompt LIKE ? OR t.agent LIKE ? OR p.name LIKE ? "
                             "ORDER BY t.created_at DESC LIMIT ?", like, like, like, like, like, limit),
        "projects": core.db.all("SELECT * FROM projects WHERE name LIKE ? OR path LIKE ? OR aliases LIKE ?", like, like, like),
    }


# -- tasks ------------------------------------------------------------------------------------
@app.get("/api/tasks")
async def tasks(status: str | None = None, limit: int = 100):
    statuses = [s.strip().upper() for s in status.split(",")] if status else None
    return {"tasks": core.db.tasks(statuses, max(1, min(limit, 500)))}


def _task_or_404(tid):
    t = core.task_view(tid)
    if not t:
        raise HTTPException(404, "No such task.")
    return t


@app.get("/api/tasks/{tid}")
async def task(tid: str):
    t = _task_or_404(tid)
    t["receipts"] = core.db.all("SELECT * FROM receipts WHERE task_id=? ORDER BY id", tid)
    t["live"] = tid in core.runs
    return t


@app.get("/api/tasks/{tid}/events")
async def task_events(tid: str, after: int = 0):
    _task_or_404(tid)
    return {"events": core.db.task_events(tid, after)}


@app.get("/api/tasks/{tid}/changes")
async def task_changes(tid: str):
    t = core.db.task(tid)
    if not t:
        raise HTTPException(404, "No such task.")
    files = t["files_changed"] or []
    diff = await probes.git_diff(Path(t["project_path"]), files) if t.get("project_path") and files else None
    return {"files": files, "git": diff is not None, "stat": (diff or {}).get("stat"),
            "patch": (diff or {}).get("patch"), "truncated": (diff or {}).get("truncated", False)}


@app.post("/api/tasks/{tid}/stop")
async def stop(tid: str, request: Request):
    _task_or_404(tid)
    if not await core.stop_task(tid, source_of(request)):
        raise HTTPException(409, "That task is not running.")
    return {"ok": True}


# -- the recorder: record a workflow once, replay it through the executor, keep the evidence (core/recorder.py) -----------------
class RecordingIn(BaseModel):
    name: str | None = None
    workflow: dict | None = None


def _recording_or_404(rid):
    rec = core.db.one("SELECT * FROM recordings WHERE id=?", rid)
    if not rec:
        raise HTTPException(404, "No such recording.")
    return rec


@app.get("/api/recordings")
async def recordings():
    """The saved workflows with their last verdict, and whether one is being recorded right now."""
    return {"recordings": core.recordings_list(), "recording": core.recorder.state()}


@app.post("/api/recordings/start")
async def recording_start(body: RecordingIn, request: Request):
    require_local(request)
    return await core.start_recording(body.name or "Recording")


@app.post("/api/recordings/stop")
async def recording_stop(body: RecordingIn, request: Request):
    require_local(request)
    return await core.stop_recording(body.name)


@app.get("/api/recordings/{rid}")
async def recording(rid: str):
    return {**_recording_or_404(rid), "runs": core.recording_runs(rid)}


@app.post("/api/recordings/{rid}/run")
async def recording_run(rid: str, request: Request):
    """Replay on this computer through the executor; the verdict arrives as TEST_RESULT and stays in the run's row."""
    require_local(request)
    _recording_or_404(rid)
    core.spawn(core.replay_recording(rid, source_of(request)))
    return {"ok": True}


@app.patch("/api/recordings/{rid}")
async def recording_update(rid: str, body: RecordingIn, request: Request):
    """Rename, or replace the workflow (its steps, as edited in the workspace: an unticked step isn't replayed or checked)."""
    require_local(request)
    rec = _recording_or_404(rid)
    changes = {}
    if body.name and body.name.strip():
        changes["name"] = body.name.strip()[:120]
    if body.workflow is not None:
        if not isinstance(body.workflow.get("steps"), list):
            raise HTTPException(400, "A workflow is {steps: [...]}.")
        changes["workflow"] = {**(rec["workflow"] or {}), **body.workflow}
    if changes:
        core.db.update("recordings", "id", rid, updated_at=time.time(), **changes)
        core.emit("RECORDINGS_CHANGED", {"id": rid})
    return _recording_or_404(rid)


@app.delete("/api/recordings/{rid}")
async def recording_delete(rid: str, request: Request):
    require_local(request)
    _recording_or_404(rid)
    return {"recordings": core.delete_recording(rid)}


# -- the owner's own organizing: groups, to-dos, and per item pin / group / archive / delete (core/side.py) ----------------
class SideIn(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    group_id: int | None = None   # null: out of its group
    archived: bool | None = None


class NameIn(BaseModel):
    name: str


class TodoIn(BaseModel):
    title: str
    money: bool = False
    group_id: int | None = None


class DoneIn(BaseModel):
    done: bool


class JudgeIn(BaseModel):
    text: str


def _side(fn, *args):
    """Run one change, then hand every page the new sidebar (SIDE_CHANGED, not persisted: a page reads GET /api/side when it opens)."""
    try:
        fn(core.db, *args)
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    except ValueError as e:
        raise HTTPException(400, str(e))
    s = side.state(core.db)
    core.emit("SIDE_CHANGED", s, persist=False)
    return s


@app.get("/api/side")
async def side_state():
    return side.state(core.db)


@app.post("/api/side/{kind}/{ref}")
async def side_update(kind: str, ref: str, body: SideIn):
    """Rename, pin, move to a group (group_id null: out of it), archive. Only the fields sent change."""
    return _side(side.update, kind, ref, {k: getattr(body, k) for k in body.model_fields_set})


@app.delete("/api/side/{kind}/{ref}")
async def side_delete(kind: str, ref: str):
    """A task is hidden for good (what Monday did stays in Activity); a to-do is gone."""
    return _side(side.delete, kind, ref)


@app.post("/api/groups")
async def group_create(body: NameIn):
    return _side(side.group_create, body.name)


@app.post("/api/groups/{gid}")
async def group_rename(gid: int, body: NameIn):
    return _side(side.group_rename, gid, body.name)


@app.delete("/api/groups/{gid}")
async def group_delete(gid: int):
    return _side(side.group_delete, gid)


@app.post("/api/todos/judge")  # before /api/todos/{tid}
async def todo_judge(body: JudgeIn):
    """Jev's read of what the owner typed in the + box: who would carry it out, and whether it involves money (then Monday waits for
    the owner's word). Starts nothing."""
    return await side.judge(core.jev, body.text)


@app.post("/api/todos")
async def todo_create(body: TodoIn):
    return _side(side.todo_create, body.title, body.money, None, body.group_id)


@app.post("/api/todos/{tid}")
async def todo_done(tid: int, body: DoneIn):
    return _side(side.todo_done, tid, body.done)


# -- Monday's Chrome, shared: the workspace's browser panel ---------------------------------------------------------
class BrowserControlIn(BaseModel):
    action: Literal["take", "resume", "stop"]


class BrowserNavIn(BaseModel):
    action: Literal["go", "back", "forward", "reload", "stop", "tab", "new_tab", "close_tab", "popout", "popin"]
    url: str | None = None
    i: int | None = None


class BrowserInputIn(BaseModel):
    type: Literal["click", "wheel", "key", "text"]
    x: float | None = None
    y: float | None = None
    dx: float | None = None
    dy: float | None = None
    button: str | None = None
    count: int | None = None
    key: str | None = None
    text: str | None = None


@app.get("/api/browser")
async def browser_state():
    return await core.chrome.view()


@app.post("/api/browser/control")
async def browser_control(body: BrowserControlIn, request: Request):
    return await core.browser_control(body.action, source_of(request))


@app.post("/api/browser/nav")
async def browser_nav(body: BrowserNavIn, request: Request):
    await core.browser_owner(source_of(request))
    try:
        return await core.chrome.owner_nav(body.action, body.url, body.i)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/browser/input")
async def browser_input(body: BrowserInputIn, request: Request):
    """The owner's own click/scroll/keys through the live view. Never logged: it may be a password they type."""
    await core.browser_owner(source_of(request))
    try:
        await core.chrome.owner_input(body.model_dump(exclude_none=True))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # the page navigated under the click: the next frame shows where it is
        raise HTTPException(409, f"The page didn't take that: {str(e).splitlines()[0][:160]}")
    return {"ok": True}


@app.get("/api/browser/stream")
async def browser_stream():
    """The live view as MJPEG (an <img> plays it): the page in front, from Chrome's own screencast. 409 when it isn't open."""
    if not core.chrome.ctx:
        raise HTTPException(409, "Monday's Chrome isn't open.")
    return StreamingResponse(chrome.mjpeg(core.chrome.frames()), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# -- sleep and wake (core/wake.py) -------------------------------------------------------------------------------------------
@app.post("/api/wake")
async def wake_now():
    """Wake Monday (a tap on its face): the same as clapping twice."""
    await core.wake_up("asked")
    return {"asleep": core.asleep}


@app.post("/api/sleep")
async def sleep_now():
    await core.go_to_sleep("button")
    return {"asleep": core.asleep}


# -- Monday's eyes: this PC's camera through OpenCV (core/vision.py) --------------------------------------------------------
class ForgetSeenIn(BaseModel):
    label: str


@app.get("/api/vision")
async def vision_status():
    """The models, whether the camera is on, and the things the owner taught by sight (names only: no pictures, no features)."""
    return core.vision.status()


@app.get("/api/vision/stream")
async def vision_stream(request: Request):
    """The camera, live, with what it sees boxed (MJPEG for an <img>). This computer only: the camera is never watched from outside.
    The camera opens for it and closes again when the last viewer leaves."""
    require_local(request)
    ok, why = core.vision.ready()
    if not ok:
        raise HTTPException(409, why)
    return StreamingResponse(chrome.mjpeg(core.vision.frames()), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/vision/forget")
async def vision_forget(body: ForgetSeenIn, request: Request):
    require_local(request)
    if not core.vision.forget(body.label):
        raise HTTPException(404, f"I don't know {body.label!r} by sight.")
    core.receipt(None, "vision.forget", "success", {"label": body.label})
    return core.vision.status()


# -- the desktop app's ear: Windows' own speech recognition (core/stt.py) --------------------------------------------
class SttIn(BaseModel):
    session: str
    hold: bool = False  # push-to-talk (core/native.py PushToTalk): the sentence lasts while the key is held, and its release ends it


@app.get("/api/stt")
async def stt_status(level: int = 0):
    """The ear's state and Windows' switches; `?level=1` also reads the microphone for a second (core/wake.py mic_level: peak and RMS in
    dBFS), so "it hears nothing" can be told from a quiet input."""
    st = core.ear.status()
    if level:
        from . import wake
        st["level"] = await asyncio.to_thread(wake.mic_level, 1.0)
    return st


@app.post("/api/stt/start")
async def stt_start(body: SttIn, request: Request):
    if not is_local(request):
        raise HTTPException(403, "The microphone is the computer's: only the desktop app on it can open it.")
    return {"ok": await core.ear.start(body.session[:64], hold=body.hold)}


@app.post("/api/stt/stop")
async def stt_stop(body: SttIn):
    return {"ok": await core.ear.stop(body.session[:64], "the page stopped it")}


@app.post("/api/stt/finish")
async def stt_finish(body: SttIn):
    """End the open sentence now, with its words: the window's early end of a one-step exact command (core/native.py Ear._early)."""
    return {"ok": await core.ear.finish(body.session[:64])}


@app.get("/api/stt/events")
async def stt_events(session: str, request: Request):
    """What the ear hears for one session, as it is heard (partial, final), until it ends. Never stored or logged."""
    if not is_local(request):
        raise HTTPException(403, "Only the desktop app on this computer hears its microphone.")
    q = core.ear.queue(session[:64])

    async def stream():
        try:
            yield "retry: 1000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), SSE_PING_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
                if ev["type"] == "end":
                    return
        finally:  # the page left (closed, reloaded, its window gone): its session ends with it, or it would keep Windows' recognizer
            await core.ear.stop(session[:64], "its event stream closed (the page left, or the connection dropped)")
            core.ear.drop(session[:64])

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -- Google connections, memory, activity --------------------------------------------------------------------------
class GoogleConnectIn(BaseModel):
    services: list[str]
    open: bool = False  # the desktop app: Monday opens Google's consent page in the owner's own browser (RFC 8252: never in an embedded view)


class GoogleServiceIn(BaseModel):
    service: str
    enabled: bool


@app.get("/api/google")
async def google_status(check: bool = False):
    """Connections > Google: the client, the account, each service's real state. ?check=1 asks Google (renews the token)."""
    if check:
        try:
            return await core.google.check()
        except google.GoogleError as e:
            return {**core.google.status(), "detail": str(e)}
    return core.google.status()


@app.post("/api/google/connect")
async def google_connect(body: GoogleConnectIn, request: Request):
    if not is_local(request):
        raise HTTPException(403, "Connect Google from the computer Monday runs on: Google sends you back to it.")
    try:
        url = core.google.connect_url(body.services)
    except google.GoogleError as e:
        raise HTTPException(400, str(e))
    if body.open:
        import webbrowser
        webbrowser.open(url)  # the owner's default browser (every OS), where they are signed in to Google; Monday never sees the password
    return {"url": url, "opened": body.open}


@app.post("/api/google/disconnect")
async def google_disconnect(request: Request):
    out = await core.google.disconnect()
    core.receipt(None, "google.disconnect", "success" if out["revoked"] is not False else "local_only",
                 {"by": source_of(request), **out})
    st = core.google.status()
    core.emit("GOOGLE_CHANGED", {"status": st, "event": "disconnected", "revoked": out["revoked"]})
    return {**out, "status": st}


@app.post("/api/google/service")
async def google_service(body: GoogleServiceIn, request: Request):
    try:
        core.google.set_enabled(body.service, body.enabled)
    except google.GoogleError as e:
        raise HTTPException(400, str(e))
    st = core.google.status()
    core.emit("GOOGLE_CHANGED", {"status": st, "event": f"{body.service} {'on' if body.enabled else 'off'}", "by": source_of(request)})
    return st


class MemoryCorrectIn(BaseModel):
    text: str


@app.get("/api/memory")
async def memory_items():
    """What Monday remembers (the vault's dated lines), newest first, with which are old enough to be out of date."""
    return {"items": memory.items()[:500], "vault": str(memory.VAULT), "stale_days": memory.STALE_DAYS}


@app.post("/api/memory/{iid}/forget")
async def memory_forget(iid: str, request: Request):
    try:
        out = memory.forget(iid)
    except KeyError as e:
        raise HTTPException(409, str(e.args[0]))
    core.receipt(None, "memory.forget", "success", {"note": out["note"], "by": source_of(request)})
    core.emit("MEMORY_FORGOTTEN", {"note": out["note"], "text": out["line"][2:120]})
    return {"ok": True}


@app.post("/api/memory/{iid}/correct")
async def memory_correct(iid: str, body: MemoryCorrectIn, request: Request):
    try:
        out = memory.correct(iid, body.text)
    except KeyError as e:
        raise HTTPException(409, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    core.receipt(None, "memory.correct", "success", {"note": out["note"], "by": source_of(request)})
    core.emit("MEMORY_SAVED", {"kind": "correction", "text": body.text[:120], "note": out["note"]})
    return out


@app.get("/api/activity")
async def activity(before: int | None = None, limit: int = 60):
    """The activity history: real, persisted events only (tasks, steps, approvals, the browser, Google, memory), newest first."""
    return {"items": core.activity(before, min(max(limit, 1), ACTIVITY_MAX))}


@app.post("/api/permissions/{pid}/{decision}")
async def decide(pid: int, decision: Literal["approve", "deny", "always"], request: Request):
    p = core.db.one("SELECT * FROM permissions WHERE id=?", pid)
    if not p:
        raise HTTPException(404, "No such permission request.")
    owner_only(request, f"{decision} a request")  # never an agent answering its own (AGENTS.md rule 3: only the owner's click)
    if p["status"] != "PENDING":
        raise HTTPException(409, f"Already {p['status'].lower()}.")
    if decision == "always":  # Always allow: offered only in Settings → Approvals' "always" or "auto", and never for HIGH risk
        if not p.get("always") or not core.grant(p.get("who") or p["agent"], p["kind"], p["risk"], p.get("detail"), p.get("project"), source_of(request)):
            raise HTTPException(409, "This request can't be always allowed: approve or deny it.")
    return core.decide_permission(pid, decision != "deny", source_of(request))


@app.get("/api/approvals")
async def approvals():
    """Settings → Approvals: the owner's mode (ask | always | auto) and their Always allow grants, each with its words."""
    return core.approvals()


class RevokeIn(BaseModel):
    who: str
    kind: str
    key: str


@app.post("/api/approvals/revoke")
async def approvals_revoke(body: RevokeIn, request: Request):
    owner_only(request, "change what is always allowed")
    if not core.revoke(body.who, body.kind, body.key, source_of(request)):
        raise HTTPException(404, "That isn't always allowed.")
    return core.approvals()


# -- projects & Graphify ----------------------------------------------------------------------
class ProjectIn(BaseModel):
    name: str
    path: str


@app.get("/api/projects")
async def projects():
    return {"projects": core.db.all("SELECT * FROM projects ORDER BY name")}


@app.post("/api/projects")
async def add_project(body: ProjectIn):
    if not Path(body.path).expanduser().is_dir():
        raise HTTPException(400, "That folder doesn't exist on this computer.")
    core.cmd_add_project(body.name.strip()[:60], body.path)
    return core.db.one("SELECT * FROM projects WHERE name=?", body.name.strip()[:60])


@app.get("/api/projects/{pid}/graphify")
async def project_graphify(pid: int):
    p = core.db.one("SELECT * FROM projects WHERE id=?", pid)
    if not p:
        raise HTTPException(404)
    return {"project": p, "graphify": await probes.graphify_status(Path(p["path"]))}


@app.post("/api/projects/{pid}/graphify/update")
async def project_graphify_update(pid: int):
    p = core.db.one("SELECT * FROM projects WHERE id=?", pid)
    if not p:
        raise HTTPException(404)
    core.spawn(core.cmd_graphify_update(p["name"]))
    return {"ok": True}


# -- files ------------------------------------------------------------------------------------
@app.post("/api/upload")
async def upload(request: Request):
    name = re.sub(r"[^\w.\- ()]+", "_", Path(unquote(request.headers.get("x-filename") or "file")).name)[:120] or "file"
    fid = uuid.uuid4().hex[:12]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / f"{fid}_{name}"
    size = 0
    try:
        with dest.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File is larger than 100 MB.")
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    core.db.insert("files", id=fid, name=name, path=str(dest), size=size, mime=mime, created_at=time.time())
    rec = core.db.one("SELECT * FROM files WHERE id=?", fid)
    core.receipt(None, "file.upload", "success", {"file_id": fid, "name": name, "size": size, "source": source_of(request)})
    return rec


# -- webhooks (core/bots.py, after OpenMausBot's; the owner, 2026-10-17: "add, each off until enabled") ------------------------------
class HookIn(BaseModel):
    prompt: str = ""


class HookChangeIn(BaseModel):
    enabled: bool | None = None
    prompt: str | None = None


def _hook_file(hid):
    """Its script, beside the database (a test Core keeps its own): the one place its secret is written."""
    return core.db.path.parent / "hooks" / f"{hid}.ps1"


def _hook(h):
    return {**bots.hook_view(h), "script": str(_hook_file(h["id"]))}


@app.get("/api/webhooks")
async def webhooks_list():
    return {"webhooks": [_hook(h) for h in bots.hooks(core.db)], "max": bots.HOOK_MAX, "rate": list(bots.HOOK_RATE)}


@app.post("/api/webhooks")
async def webhook_add(body: HookIn, request: Request):
    """A new webhook, off: its secret goes only into its script (never into this answer or the window)."""
    owner_only(request, "add a webhook")
    try:
        h, secret = bots.hook_new(core.db, body.prompt)
    except ValueError as e:
        raise HTTPException(400, str(e))
    f = _hook_file(h["id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(bots.hook_script(h, secret, PORT), encoding="utf-8")
    core.receipt(None, "webhook.add", "success", {"id": h["id"], "name": h["name"]})
    core.emit("WEBHOOKS_CHANGED", {}, persist=False)
    return _hook(h)


@app.post("/api/webhooks/{hid}")
async def webhook_change(hid: str, body: HookChangeIn, request: Request):
    owner_only(request, "change a webhook")
    h = bots.hook_update(core.db, hid, enabled=body.enabled, prompt=body.prompt)
    if not h:
        raise HTTPException(404, "No such webhook.")
    core.receipt(None, "webhook.change", "success", {"id": hid, "enabled": h["enabled"], "name": h["name"]})
    core.emit("WEBHOOKS_CHANGED", {}, persist=False)
    return _hook(h)


@app.delete("/api/webhooks/{hid}")
async def webhook_delete(hid: str, request: Request):
    owner_only(request, "delete a webhook")
    if not bots.hook_remove(core.db, hid):
        raise HTTPException(404, "No such webhook.")
    _hook_file(hid).unlink(missing_ok=True)
    core.receipt(None, "webhook.delete", "success", {"id": hid})
    core.emit("WEBHOOKS_CHANGED", {}, persist=False)
    return {"ok": True}


# -- the phone companion (core/remote.py, docs/adr/0009): pairing and devices are the window's; the phone itself never reaches :8765 -----
class RemoteCapabilityIn(BaseModel):
    key: Literal["approve", "control"]
    value: bool


@app.get("/api/remote")
async def remote_state():
    """Settings → Phone: on/off, the addresses, the tunnel's state in words, how many phones, the certificate's fingerprint."""
    return core.remote.state()


# -- updates (docs/adr/0010): the signed channel's state, a check now, and the owner's Update ---------------------------------------
@app.get("/api/update")
async def update_state():
    return core.update_state or await core.check_update()


@app.post("/api/update/check")
async def update_check():
    return await core.check_update()


@app.post("/api/update/install")
async def update_install(request: Request):
    """The owner's Update: the package copied and checked against the signed manifest, or a restart on the source folder. Only the
    window may ask (an agent's call never starts an install)."""
    owner_only(request, "install an update")
    try:
        return await core.install_update()
    except ValueError as e:
        raise HTTPException(409, str(e))


class ChannelIn(BaseModel):
    folder: str = ""  # empty: back to the package's own (core/update.py channel)


@app.post("/api/update/channel")
async def update_channel(body: ChannelIn, request: Request):
    """Where updates come from (Settings -> General -> Updates): a folder this PC can read, or empty for the default."""
    owner_only(request, "change where updates come from")
    folder = body.folder.strip().strip('"')
    if folder and not os.path.isdir(folder):
        raise HTTPException(400, f"{folder} isn't a folder on this PC.")
    core.db.set_setting("update_channel", folder or None)
    return await core.check_update()


@app.post("/api/remote/pairing")
async def remote_pairing(request: Request):
    """A pairing code (8 digits, 120 s, once), shown in the window with its countdown: never an agent's call."""
    owner_only(request, "pair a phone")
    return core.remote.start_pairing()


@app.get("/api/remote/devices")
async def remote_devices():
    return {"devices": core.remote.devices()}


@app.delete("/api/remote/devices/{did}")
async def remote_revoke(did: int, request: Request):
    owner_only(request, "revoke a phone")
    if not core.remote.revoke(did, by=source_of(request)):
        raise HTTPException(404, "No such device.")
    return {"ok": True}


@app.post("/api/remote/devices/{did}/capability")
async def remote_capability(did: int, body: RemoteCapabilityIn, request: Request):
    owner_only(request, "change what a phone may do")
    try:
        return {"capabilities": core.remote.set_capability(did, body.key, body.value)}
    except KeyError:
        raise HTTPException(404, "No such device.")


@app.post("/api/hooks/{hid}")
async def webhook_fire(hid: str, request: Request):
    """A program on this computer fires one of the owner's webhooks (the guard admits nothing else). Its words are the command, through
    Jev and the rules as if typed; what was posted is attached as a file: data, never instructions. Approvals apply as usual. One that is
    off answers 404 like an unknown one; a wrong secret 403; more than HOOK_RATE fires 429; more than HOOK_BYTES 413."""
    h = next((x for x in bots.hooks(core.db) if x["id"] == hid), None)
    if not h or not h.get("enabled"):
        raise HTTPException(404, "No such webhook.")
    if not bots.hook_check(h, request.headers.get("x-monday-hook") or ""):
        core.receipt(None, "webhook.fire", "refused", {"id": hid, "why": "wrong secret"})
        raise HTTPException(403, "Wrong secret.")
    now = time.time()
    if not bots.hook_rate(hid, now):
        raise HTTPException(429, f"This webhook fires at most {bots.HOOK_RATE[0]} times in {bots.HOOK_RATE[1]:.0f} s.")
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > bots.HOOK_BYTES:
            raise HTTPException(413, f"A webhook takes at most {bots.HOOK_BYTES // 1024} KB.")
    files = []
    if data.strip():
        fid = uuid.uuid4().hex[:12]
        UPLOADS.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS / f"{fid}_webhook.txt"
        dest.write_bytes(data)
        core.db.insert("files", id=fid, name="webhook payload.txt", path=str(dest), size=len(data), mime="text/plain", created_at=now)
        files = [fid]
    bots.hook_update(core.db, hid, fired=now)
    core.receipt(None, "webhook.fire", "success", {"id": hid, "name": h["name"], "bytes": len(data)})
    r = await core.command(h["prompt"], "webhook", None, files)
    return {"ok": True, "message_id": (r.get("message") or {}).get("id")}


class MailDraftIn(BaseModel):
    file_id: str


@app.post("/api/mail/draft")
async def mail_draft(body: MailDraftIn):
    """The notch's email card: Monday writes the draft for sending one of the owner's dropped files. It sends nothing: the owner's mail
    app does, when they press Send there."""
    f = core.db.one("SELECT * FROM files WHERE id=?", body.file_id)
    if not f:
        raise HTTPException(404, "Monday doesn't have that file.")
    text = await core.draft_email(f)
    if not text:
        raise HTTPException(503, "no model answered")
    return {"text": text}


@app.get("/api/files/{fid}")
async def download(fid: str):
    f = core.db.one("SELECT * FROM files WHERE id=?", fid)
    if not f or not Path(f["path"]).exists():
        raise HTTPException(404)
    inline = (f["mime"] or "").startswith("image/")
    return FileResponse(f["path"], media_type=f["mime"], filename=f["name"],
                        content_disposition_type="inline" if inline else "attachment")


# -- voice ------------------------------------------------------------------------------------
class VoiceIn(BaseModel):
    state: Literal["listening", "idle", "speaking"]
    page: str | None = None  # the HUD page's own id: two pages listening at once would run every sentence twice


EARS: dict[str, float] = {}  # HUD pages that said "listening", and when (refreshed by the page's heartbeat)
EAR_FRESH_S = 12             # a page that hasn't said so for this long is no longer listening


@app.post("/api/voice")
async def voice(body: VoiceIn, request: Request):
    """The HUD's voice state, broadcast to the other pages. One ear at a time: the first page to listen keeps it; a second page that
    opens its mic is told `duplicate` and stands down (2026-10-06: the owner had the old tab and the new app window both listening)."""
    duplicate = False
    if body.page:
        now = time.time()
        for k, ts in list(EARS.items()):
            if now - ts > EAR_FRESH_S:
                EARS.pop(k, None)
        if body.state == "listening":
            if any(k != body.page[:64] for k in EARS):
                duplicate = True
            else:
                EARS[body.page[:64]] = now
        else:
            EARS.pop(body.page[:64], None)
    core.emit("VOICE_STARTED" if body.state in ("listening", "speaking") else "VOICE_STOPPED",
              {"state": body.state, "source": source_of(request)}, persist=False)
    return {"ok": True, "duplicate": duplicate}


# -- live events --------------------------------------------------------------------------------
def _sse(ev):
    # No `event:` line: every event arrives on EventSource.onmessage; the type is inside the JSON.
    head = f"id: {ev['id']}\n" if "id" in ev else ""
    return f"{head}data: {json.dumps(ev, default=str)}\n\n"


@app.get("/api/events")
async def events(request: Request, since: int | None = None):
    last = request.headers.get("last-event-id") or since
    q = core.bus.subscribe()

    async def stream():
        try:
            yield f"retry: 3000\n\n"
            seen = 0
            if last is not None:
                for ev in core.db.events_after(int(last)):
                    ev = {"id": ev["id"], "type": ev["type"], "ts": ev["ts"], "task_id": ev["task_id"], "data": ev["data"]}
                    seen = ev["id"]
                    yield _sse(ev)
            yield _sse({"type": "HELLO", "ts": time.time(), "task_id": None,
                        "data": {"state": core.state, "name": core.name, "replayed_to": seen}})
            while True:
                if getattr(q, "overflowed", False):
                    return  # client reconnects with Last-Event-ID
                try:
                    ev = await asyncio.wait_for(q.get(), SSE_PING_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev.get("id") and ev["id"] <= seen:
                    continue
                yield _sse(ev)
        finally:
            core.bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
