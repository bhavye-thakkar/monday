"""AI tools on this computer: what is installed, how Monday can use it, and what is known about its login.

Discovery reports only what it can see:
- auth "verified":   the tool itself said it is logged in (Claude Code `auth status`, Codex `login status`)
- auth "configured": a key or saved login exists, but Monday hasn't proven it works (it will find out on first use)
- auth "not_logged_in" / "blocked": the tool said no, or it hit a plan limit (Monday remembers until when)
- auth "unknown":    Monday can't tell (editors and desktop apps sign in inside themselves), or the check itself failed
- auth "offline":    the server it needs (Ollama) isn't answering
Integration says how Monday can use it: "headless" (Monday runs coding tasks with it), "handoff" (an editor whose own
command line takes a prompt for its agent: Monday hands the prompt over and sees only the files that change), "editor"
(Monday opens projects in it), "app" (Monday can open it), "models" (chat). Nothing here bypasses a login, a plan or a quota.
Each tool also gets one `status` in the owner's five words (tools.STATUSES, `status_of`): available, unavailable,
detected but unsupported, not authenticated, error.
Since 2026-10-27 each row is also an entry of the authentication registry (`auth_fields`): its sign-in `context` (a CLI's own login, a
desktop app's session, an editor's, a local server: separate contexts, never equated: the Claude app signed in says nothing about the
Claude CLI, whose `claude -p` runs with a worker's environment), `auth_status` in seven words derived from `auth`, `required`,
`checked_at`, `last_ok_at`, `last_error`, `capabilities` and `remediation`. The old fields and words stay as they are.
Self-check: python -m core.agents
"""
import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import probes
from .jev import read_env_file
from .tools import AVAILABLE, ERROR, NOT_AUTHENTICATED, UNAVAILABLE, UNSUPPORTED

NO_INTERFACE = "Detected application, but no supported automation interface is available."

HOME = Path.home()
VERSION_TIMEOUT_S = 15
HEADLESS = ("claude_code", "codex", "gemini_cli", "kilo", "grok_build")   # agents with a Monday adapter (workers.py)
PREFERENCE = ("claude_code", "codex", "gemini_cli", "kilo", "grok_build")  # tie-break order when nothing is measured yet
# Editors with an AI agent of their own: (key, name, the command its installer puts on PATH), in the order they are fallen back to
# (the owner named Antigravity and Cursor first, 2026-10-05). One is usable only if that command has the `chat` subcommand.
EDITORS = (("antigravity", "Antigravity IDE", "antigravity-ide"), ("cursor", "Cursor", "cursor"), ("kiro", "Kiro", "kiro"), ("vscode", "VS Code", "code"))
HANDOFF = tuple(k for k, _, _ in EDITORS)
CUSTOM = {}  # engines from data/engines.json (core/engines.py register): key -> entry; drivable over ACP when their command is here
_versions, _chat = {}, {}
# The authentication registry's words (2026-10-27): where a tool's sign-in lives, and its status in seven words. "expired" is in the
# vocabulary for a probe that can tell a lapsed sign-in from a missing one; none does yet, so no row says it.
CONTEXTS = ("cli", "desktop_app", "api_key", "cloud", "local_runtime", "editor")
AUTH_STATUSES = ("authenticated", "not_authenticated", "expired", "configuration_missing", "connection_unavailable", "unknown", "not_applicable")
AUTH_STATUS = {"verified": "authenticated", "configured": "unknown", "not_logged_in": "not_authenticated", "blocked": "authenticated",
               "offline": "connection_unavailable", "n/a": "configuration_missing"}  # auth "unknown": see auth_fields
AUTH_ERROR = {"not_logged_in": "not_logged_in", "blocked": "usage_limit", "offline": "offline", "n/a": "not_installed"}  # last_error by auth
CONTEXT_OF = {"app": "desktop_app", "editor": "editor", "handoff": "editor", "models": "local_runtime"}  # by integration; the rest are CLIs
CAPABILITIES = {"headless": "coding tasks", "handoff": "a prompt to its own agent", "editor": "open", "app": "open", "models": "chat",
                "acp": "tasks over ACP", "installed (no Monday adapter yet)": "open", "own window": "open"}  # by integration
NOT_PROVEN = "saved sign-in, not proven yet (Monday finds out on first use)"
REQUIRED = ("ollama", "hermes")  # always needed (Monday.readiness); the default coder only while no other coder can run (mark_required)
_last_ok = {}  # key -> when its sign-in was last seen (auth verified or configured), for the tools no probe (probes.Health.meta) remembers
# One-click setup (2026-10-13, after the owner's MausBot reference): each tool's own documented installer (a shell line, run with its
# output streamed to the page) and its own sign-in (a console window the owner finishes in; Monday never sees the credentials).
# Checked against the tools' own documentation and npm on 2026-10-13. No command is invented: a tool without a known one shows its site.
SETUP = {
    "claude_code": {"install": 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"',
                    "login": ["claude"], "login_how": "In Claude Code's window type /login and finish in your browser, then press Check again.",
                    "site": "https://claude.com/claude-code"},
    "codex": {"install": "npm install -g @openai/codex", "login": ["codex", "login"], "login_how": "Sign in with your ChatGPT account in the browser Codex opens.",
              "site": "https://github.com/openai/codex"},
    "gemini_cli": {"install": "npm install -g @google/gemini-cli", "login": ["gemini"], "login_how": "Pick a sign-in in Gemini CLI's window (Google account or a key).",
                   "site": "https://github.com/google-gemini/gemini-cli"},
    "kilo": {"install": "npm install -g @kilocode/cli", "login": ["kilo", "auth", "login"],
             "login_how": "Sign in in Kilo's window (its Kilo Gateway, or a provider's key).", "site": "https://kilocode.ai"},
    "grok_build": {"login": ["grok", "login"], "login_how": "Sign in at grok.com in the browser it opens.", "site": "https://x.ai/cli",
                   "install_how": "x.ai's installer is per platform: its site says how."},
    "copilot_cli": {"install": "npm install -g @github/copilot", "login": ["copilot"], "login_how": "Type /login in Copilot's window.",
                    "site": "https://github.com/github/copilot-cli"},
    "droid": {"install": "npm install -g droid", "login": ["droid"], "login_how": "Droid asks you to sign in through your browser on its first run.",
              "site": "https://docs.factory.com/cli"},
    "openclaw": {"install": "npm install -g openclaw@latest", "login": ["openclaw", "onboard"], "needs_node": (24, 16),
                 "login_how": "OpenClaw's onboarding sets up its model provider (your key or sign-in) and its daemon.", "site": "https://docs.openclaw.ai/install"},
    "hermes": {"site": "https://hermes-agent.nousresearch.com", "install_how": "Hermes' installer is per platform: its site says how."},
    "kiro": {"site": "https://kiro.dev"}, "cursor": {"site": "https://cursor.com"}, "antigravity": {"site": "https://antigravity.google"},
    "freebuff": {"install": "npm install -g freebuff", "login": ["freebuff", "login"], "login_how": "Sign in in Freebuff's window.",
                 "site": "https://freebuff.com"},
}


def node_version():
    """(major, minor) of the Node on PATH, or None."""
    out = (_versions.get("node") or "")
    if not out:
        try:
            import subprocess
            out = subprocess.run([shutil.which("node") or "node", "--version"], capture_output=True, text=True, timeout=10,
                                 creationflags=0x08000000 if os.name == "nt" else 0).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = "?"
        _versions["node"] = out
    m = re.match(r"v?(\d+)\.(\d+)", out)
    return (int(m[1]), int(m[2])) if m else None


def setup_for(key, installed=False):
    """What Connections may offer for a tool: an install command (when it isn't installed and one is known, and its requirements hold
    here), a sign-in command, its site. Words, never an invented command."""
    s = SETUP.get(key) or {}
    out = {"site": s.get("site"), "login": s.get("login") if installed else None, "login_how": s.get("login_how"),
           "install": None if installed else s.get("install"), "install_how": s.get("install_how")}
    need = s.get("needs_node")
    if out["install"] and need:
        have = node_version()
        if not have or have < need:
            out["install"], out["install_how"] = None, (f"needs Node {need[0]}.{need[1]}+; this PC has "
                                                        f"{'.'.join(map(str, have)) if have else 'no Node on PATH'}. Update Node, then install.")
    return out


def npm_entry(name):
    """A global npm CLI as [node, script]: the .cmd/.ps1 shims mangle arguments and hide the real process."""
    shim = shutil.which(name + ".cmd") or shutil.which(name)
    if not shim or not shim.lower().endswith((".cmd", ".ps1")):
        return None
    shim = Path(shim).with_suffix(".cmd")
    try:
        text = shim.read_text(errors="ignore")
    except OSError:
        return None
    # the script the shim hands to node, the one followed by its arguments (Kilo 7's is bin\kilo, without an extension: 2026-10-15
    # it was missed, and Kilo looked uninstalled); a shim that runs a program itself isn't a node script
    m = re.search(r'"%dp0%\\([^"]+)"\s*%\*', text)
    node = (shim.parent / "node.exe") if (shim.parent / "node.exe").exists() else shutil.which("node")
    script = shim.parent / m.group(1) if m and not re.search(r"\.(?:exe|cmd|bat|ps1)$", m.group(1), re.I) else None
    return [str(node), str(script)] if node and script and script.exists() else None


def editor_cli(cmd):
    """An editor's command line as [its exe, its cli.js]: what its .cmd shim runs. The shim itself can't be used: cmd.exe would
    take a prompt's quotes, percent signs and line breaks apart."""
    shim = shutil.which(cmd + ".cmd") or shutil.which(cmd)
    if not shim or not shim.lower().endswith(".cmd"):
        return None
    try:
        parts = re.findall(r'"%~dp0([^"]+)"', Path(shim).read_text(errors="ignore"))
    except OSError:
        return None
    paths = [(Path(shim).parent / p).resolve() for p in parts[:2]]
    return [str(p) for p in paths] if len(paths) == 2 and paths[1].suffix == ".js" and all(p.is_file() for p in paths) else None


async def editor_chat(cmd):
    """[exe, cli.js] when this editor's command line has the `chat` subcommand that takes a prompt, else None. Asked of its own
    `chat --help`, which only prints. Never by trying a command: one this build doesn't know is taken for a path to open, and the
    editor starts (measured 2026-10-05 with `cursor desktop ls`)."""
    if cmd not in _chat:
        entry = editor_cli(cmd)
        out = (await probes.run(*entry, "chat", "--help", timeout=VERSION_TIMEOUT_S, env={**probes.worker_env(), "ELECTRON_RUN_AS_NODE": "1"}))[1] if entry else ""
        _chat[cmd] = entry if "chat [options] [prompt]" in (out or "") else None
    return _chat[cmd]


_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def limit_until(text):
    """'…try again at 11:02 PM on Oct 7th, 2026' -> epoch seconds (local time), or None when no future date is given."""
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", text or "", re.I)
    if not m:
        return None
    t = re.search(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?", text, re.I)
    hh, mm = (int(t.group(1)), int(t.group(2))) if t else (0, 0)
    if t and t.group(3):
        hh = hh % 12 + (12 if t.group(3).lower().startswith("p") else 0)
    try:
        ts = time.mktime((int(m.group(3) or time.localtime().tm_year), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)), hh, mm, 0, 0, 0, -1))
    except (OverflowError, ValueError):
        return None
    return ts if ts > time.time() else None


def codex_limit():
    """Codex's own last word on usage, from its session logs (shared by the CLI, the IDE extension and the app): the
    message of the newest finished turn when that turn hit the plan's usage limit, else None. Read-only; costs no quota."""
    root = Path(os.environ.get("CODEX_HOME") or HOME / ".codex") / "sessions"
    try:
        files = sorted(root.glob("*/*/*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    except OSError:
        return None
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"task_complete"' in line:
                try:
                    err = json.loads(line)["payload"].get("error") or {}
                except (ValueError, KeyError, AttributeError):
                    continue
                return err.get("message") if err.get("codex_error_info") == "usage_limit_exceeded" else None
    return None


async def _version(argv):
    key = " ".join(argv)
    if key not in _versions:
        code, out, err = await probes.run(*argv, "--version", timeout=VERSION_TIMEOUT_S)
        m = re.search(r"\d+\.\d+(?:\.\d+)?", (out or "") + (err or ""))
        _versions[key] = m.group(0) if code == 0 and m else None
    return _versions[key]


def _gemini_auth():
    try:
        s = json.loads((HOME / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    kind = ((s.get("security") or {}).get("auth") or {}).get("selectedType") or s.get("selectedAuthType")
    env_file = {}
    for p in (HOME / ".gemini" / ".env", HOME / ".env"):
        env_file.update(read_env_file(p))  # names only are checked; values never leave this function
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or env_file.get("GEMINI_API_KEY") or env_file.get("GOOGLE_API_KEY"))
    # A saved Google sign-in answers even when the settings name an API key: measured 2026-09-28, headless replies with
    # no key anywhere. Only the file's existence is checked; the CLI reads it, Monday never does.
    if kind in ("oauth-personal", "login-with-google") or (HOME / ".gemini" / "oauth_creds.json").exists():
        return "configured", "saved Google sign-in"
    if kind == "gemini-api-key":
        return ("configured", "uses a Gemini API key") if has_key else ("not_logged_in", "set to use a Gemini API key, but none is set (GEMINI_API_KEY)")
    if kind:
        return "configured", f"auth type {kind}"
    return "not_logged_in", "never signed in (run `gemini` once)"


def status_of(t):
    """One discovered tool in the owner's five words: can Monday use it right now, and if not, which kind of no."""
    if not t["installed"]:
        return UNAVAILABLE
    if t["integration"] in ("handoff", "acp"):  # its agent takes a prompt (an editor's, or over ACP); its sign-in is its own business
        return AVAILABLE
    if t["integration"] not in ("headless", "models"):  # an editor or app, or a CLI Monday has no adapter for yet
        return UNSUPPORTED
    return {"verified": AVAILABLE, "configured": AVAILABLE, "not_logged_in": NOT_AUTHENTICATED, "blocked": UNAVAILABLE,
            "offline": UNAVAILABLE}.get(t["auth"], ERROR)


def auth_fields(t, meta=None):
    """The registry's words for one row, derived from what was seen (`auth`), never guessed: context, auth_status (AUTH_STATUSES: verified
    -> authenticated; configured -> unknown, not proven; not logged in -> not_authenticated; blocked -> authenticated with last_error
    usage_limit; offline -> connection_unavailable; not installed -> configuration_missing; unknown on an app, an editor or a CLI without
    an adapter -> not_applicable, its sign-in lives inside it and Monday can't read it; unknown on a headless CLI -> unknown, its check
    failed), checked_at and last_ok_at (the probe's memory `meta` when there is one, else now / when it was last seen authenticated),
    last_error (a category word), capabilities, remediation (what the owner can do), required (False here: mark_required)."""
    auth, integ, s, meta = t["auth"], t["integration"], t.get("setup") or {}, meta or {}
    headless = integ == "headless"
    status = AUTH_STATUS.get(auth) or ("unknown" if headless else "not_applicable")
    error = AUTH_ERROR.get(auth) or (meta.get("last_error") or "check_failed" if headless and auth == "unknown" else None)
    now = time.time()
    if auth in ("verified", "configured"):
        _last_ok[t["key"]] = now
    if status == "not_authenticated":
        fix = s.get("login_how") or t.get("detail")
    elif status == "configuration_missing":
        fix = s.get("install_how") or (f"install it: {s['install']}" if s.get("install") else f"see {s['site']}" if s.get("site") else t.get("detail"))
    elif auth == "configured":
        fix = NOT_PROVEN
    elif status in ("connection_unavailable", "unknown") or auth == "blocked":
        fix = t.get("detail") or None
    else:
        fix = None
    return {"context": CONTEXT_OF.get(integ, "cli"), "auth_status": status, "required": False,
            "checked_at": meta.get("checked_at") or now, "last_ok_at": meta["last_ok_at"] if "last_ok_at" in meta else _last_ok.get(t["key"]),
            "last_error": error, "capabilities": "computer work" if t.get("kind") == "computer agent" else CAPABILITIES.get(integ),
            "remediation": fix or None}


def mark_required(rows, default="claude_code"):
    """`required` on each row: Ollama and Hermes always (Monday.readiness), the default coder only while no other coder can run
    (then its missing sign-in blocks coding tasks; otherwise it is optional, nothing to nag about); every other row False."""
    others = any(usable(t) for t in rows if t["key"] != default and (t["kind"] == "coding agent" or t.get("coding")))
    for t in rows:
        t["required"] = t["key"] in REQUIRED or (t["key"] == default and not others)
    return rows


async def _grok_auth(exe):
    """Grok Build's own answer: `grok models` lists without spending anything, and says when nobody is signed in."""
    code, out, err = await probes.run(exe, "models", timeout=VERSION_TIMEOUT_S)
    text = f"{out or ''}{err or ''}"
    if re.search(r"not authenticated|not (?:signed|logged) in", text, re.I):
        return "not_logged_in", "signed out: run `grok login` once"
    who = re.search(r"logged in with ([\w.-]+)", text, re.I)
    return (("configured", f"signed in with {who.group(1).rstrip('.') if who else 'your account'}; model "
             + ", ".join(re.findall(r"\*\s*([\w.-]+)", text)[:3])) if code == 0 else ("unknown", text.strip()[:120]))


async def _kilo_auth(entry):
    """Kilo's own answer (Kilo 7, 2026-10-15: `kilo auth list` names its sign-ins; Monday never reads the credentials file)."""
    code, out, err = await probes.run(*entry, "auth", "list", timeout=VERSION_TIMEOUT_S)
    return kilo_signins(_ANSI.sub("", out + err))


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def kilo_signins(text):
    """`kilo auth list` -> (auth, detail): its sign-ins by name ("•  Kilo Gateway oauth"), or that there are none."""
    names = [re.sub(r"\s+(?:oauth|api|wellknown)$", "", ln.strip()[1:].strip()) for ln in text.splitlines() if ln.strip().startswith(("•", "â€¢"))]
    names = [n for n in names if n]
    return ("configured", f"signed in: {', '.join(names)}") if names else ("not_logged_in", "no sign-in (run `kilo auth login`)")


async def discover(core):
    """Every AI tool Monday can see, as a list of dicts. Uses cached health checks where Monday already has them."""
    health, apps = core.health, core.computer.apps
    try:
        installed = await apps.all()
    except Exception:
        installed = []

    def app_named(*names):
        for n in names:
            for a in installed:
                if a["name"].lower() == n.lower():
                    return a
        return None

    out = []

    def add(key, name, kind, integration, installed_, exe=None, version=None, auth="unknown", detail="", meta=None, **extra):
        t = {"key": key, "name": name, "kind": kind, "integration": integration, "installed": bool(installed_),
             "exe": exe, "version": version, "auth": auth if installed_ else "n/a", "detail": detail or ("" if installed_ else "not installed"), **extra}
        t = {**t, "status": status_of(t), "setup": setup_for(key, bool(installed_))}
        out.append({**t, **auth_fields(t, meta)})  # (meta: the probe's memory of this check, core/probes.py Health.meta)

    claude, codex = await asyncio.gather(health.claude(), health.codex())
    add("claude_code", "Claude Code", "coding agent", "headless", claude["status"] != "not_installed", claude.get("exe"),
        claude.get("version"), {"ready": "verified", "not_logged_in": "not_logged_in"}.get(claude["status"], "unknown"),
        {"ready": f"logged in ({claude.get('detail')})", "not_logged_in": probes.CLAUDE_LOGIN}.get(claude["status"], claude.get("detail") or ""),
        meta=health.meta("claude"))
    block = core.agent_block("codex")
    if not block and codex["status"] == "ready" and (until := limit_until(codex_limit())):
        block = {"until": until, "until_text": time.strftime("%d %b %H:%M", time.localtime(until))}  # Codex said so in its log
    add("codex", "Codex", "coding agent", "headless", codex["status"] != "not_installed", codex.get("exe"),
        await _version([codex["exe"]]) if codex.get("exe") else None,
        "blocked" if block else {"ready": "verified", "not_logged_in": "not_logged_in"}.get(codex["status"], "unknown"),
        (f"usage limit until {block['until_text']}" if block else codex.get("detail") or ""), blocked_until=(block or {}).get("until"),
        meta=health.meta("codex"))
    g = npm_entry("gemini")
    auth, detail = _gemini_auth() if g else ("n/a", "")
    block = core.agent_block("gemini_cli")
    add("gemini_cli", "Gemini CLI", "coding agent", "headless", g, g and g[1], await _version(g) if g else None,
        "blocked" if block else auth, f"usage limit until {block['until_text']}" if block else detail)
    k = npm_entry("kilocode") or npm_entry("kilo")
    auth, detail = await _kilo_auth(k) if k else ("n/a", "")
    block = core.agent_block("kilo")
    add("kilo", "Kilo Code CLI", "coding agent", "headless", k, k and k[1], await _version(k) if k else None,
        "blocked" if block else auth, f"usage limit until {block['until_text']}" if block else detail)
    grok = shutil.which("grok") or shutil.which("agent")
    out_v = (await probes.run(grok, "--version", timeout=VERSION_TIMEOUT_S))[1] if grok else ""
    if "grok" in out_v.lower():  # xAI's "Grok Build" CLI installs as grok.exe / agent.exe
        auth, why = await _grok_auth(grok)
        block = core.agent_block("grok_build")
        add("grok_build", "Grok Build CLI", "coding agent", "headless", True, grok, (re.search(r"\d+\.\d+(?:\.\d+)?", out_v) or [None])[0],
            "blocked" if block else auth, f"usage limit until {block['until_text']}" if block else why)
    copilot = shutil.which("copilot") or npm_entry("copilot")
    add("copilot_cli", "GitHub Copilot CLI", "coding agent", "not installed" if not copilot else "installed (no Monday adapter yet)",
        copilot, copilot if isinstance(copilot, str) else None, None, "unknown", "" if copilot else "not installed (npm install -g @github/copilot)")
    fb = shutil.which("freebuff")  # 2026-10-15: interactive only (its --help: --cwd, --continue, login), so it can't be handed a task
    add("freebuff", "Freebuff", "coding agent", "own window" if fb else "not installed", fb, fb, None, "unknown",
        "works in its own window only (it takes no task from outside): Monday opens it in your project" if fb else "not installed")
    commands = {k: c for k, _, c in EDITORS}
    for key, name, names, cli in (("cursor", "Cursor", ("Cursor",), "cursor-agent"), ("kiro", "Kiro", ("Kiro",), "kiro-cli"),
                                  ("antigravity", "Antigravity IDE", ("Antigravity IDE", "Antigravity"), "agy"),
                                  ("vscode", "VS Code", ("Visual Studio Code",), None)):
        a = app_named(*names)
        agent_cli = shutil.which(cli) if cli else None
        chat = await editor_chat(commands[key]) if a else None
        note = ("its agent CLI is installed, but Monday has no adapter for it yet" if agent_cli else
                f"{NO_INTERFACE} Its agent works inside the editor; Monday can open projects in it" + (f" ({cli} not found)" if cli else ""))
        if key == "vscode":
            exts = [p.name for p in (HOME / ".vscode" / "extensions").glob("*") if re.search(r"copilot|codex|gemini|kilo|claude|cline", p.name, re.I)]
            note = (f"{NO_INTERFACE} Monday can open projects in it; its AI extensions: "
                    + (", ".join(sorted({re.sub(r"-\d.*$", "", e) for e in exts})) or "none"))
            chat = chat if any("copilot" in e.lower() for e in exts) else None  # its chat is Copilot's: without that extension nobody answers
        if chat:  # the last resort for coding work: the prompt goes to its own agent, which works in its own window
            note = (f"Monday can hand a prompt to its own agent (`{commands[key]} chat`), which then works in its window with your sign-in and "
                    "its own permission settings. Monday sees the files it changes, nothing else")
        add(key, name, "editor", "handoff" if chat else "editor", a, a and (a.get("launch_path") or a.get("bundle_id")), None, "unknown", note)
    for key, name, names in (("chatgpt", "ChatGPT", ("ChatGPT",)), ("claude_app", "Claude (desktop)", ("Claude",)),
                             ("ms_copilot", "Microsoft Copilot", ("Copilot",))):
        a = app_named(*names)
        add(key, name, "app", "app", a, a and a.get("launch_path"), None, "unknown",  # its session is its own, never read (auth_fields)
            f"{NO_INTERFACE} Monday can open it. Its sign-in is its own; Monday can't read it." if a else "not installed")
    ol = await health.ollama()
    ol_exe = next((str(p) for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe",
                                    Path(shutil.which("ollama") or "")) if str(p) not in ("", ".") and p.is_file()), None)  # its logo (the notch's pills)
    add("ollama", "Ollama", "models", "models", ol["status"] != "not_installed", ol_exe, None,
        "verified" if ol["status"] == "online" else "offline",
        f"{len(ol.get('models') or [])} models" if ol["status"] == "online" else "the Ollama server isn't running", meta=health.meta("ollama"))
    hm = await health.hermes()
    add("hermes", "Hermes Agent", "computer agent", "headless", hm["status"] != "not_installed", hm.get("exe"), None,
        {"online": "verified", "offline": "offline"}.get(hm["status"], "unknown"),
        hm.get("detail") if hm["status"] == "offline" else f"{hm.get('model')} via {hm.get('provider')}" if hm.get("model") else "",
        meta=health.meta("hermes"))
    # 2026-10-13 (the owner's MausBot reference): Droid, OpenClaw and the engines of data/engines.json (core/engines.py), each as it is here
    droid = shutil.which("droid") or npm_entry("droid")
    add("droid", "Droid (Factory)", "coding agent", "installed (no Monday adapter yet)" if droid else "not installed", droid,
        droid if isinstance(droid, str) else (droid and droid[1]), None, "unknown", "" if droid else "not installed")
    for key, e in CUSTOM.items():
        here = bool(e.get("argv"))
        add(key, e["name"], "agent", "acp" if here else "not installed", here, e["argv"][0] if here else None, None, "unknown",
            (f"a custom engine, driven over ACP: `{e['cli']}`" if here else f"not found: `{e['cli'].split()[0]}` isn't on this PC") +
            (f" ({e['source']})" if e.get("source") else ""), custom=True, cli=e["cli"], coding=e.get("coding", True))
    if "openclaw" not in CUSTOM:
        add("openclaw", "OpenClaw", "agent", "not installed", False, None, None, "unknown", "not installed: once it is, Monday drives `openclaw acp`")
    return mark_required(out, core.db.setting("default_coder", "claude_code") if getattr(core, "db", None) else "claude_code")


def usable(t):
    return bool(t["installed"]) and (t["integration"] == "acp" or (t["integration"] == "headless" and t["auth"] in ("verified", "configured")))


def handoff(t):
    """An installed editor whose own agent can be handed a prompt: usable when no command-line agent is."""
    return bool(t["installed"]) and t["integration"] == "handoff"


def pick_coder(tools, wanted=None, default="claude_code", durations=None):
    """(agent key or None, reason). The owner's explicit choice wins; then the default when it can run; then the agent
    that finished coding tasks fastest here; then a fixed preference. Only drivable agents with a usable login count.
    When none of those can run, an editor whose own agent takes a prompt (HANDOFF order) is the last resort."""
    by = {t["key"]: t for t in tools}
    order = (*PREFERENCE, *(k for k, e in CUSTOM.items() if e.get("coding", True)))  # custom engines after the built-in ones
    ok = [k for k in order if k in by and usable(by[k])]
    apps = [k for k in HANDOFF if k in by and handoff(by[k])]
    durations = durations or {}

    def why_not(k):
        t = by.get(k)
        if not t or not t["installed"]:
            return f"{t['name'] if t else k} isn't installed"
        return {"not_logged_in": f"{t['name']} isn't logged in", "blocked": f"{t['name']} hit a usage limit ({t['detail']})"}.get(
            t["auth"], f"{t['name']} can't run ({t['detail'] or t['auth']})")

    if wanted:
        return (wanted, f"you asked for {by[wanted]['name'] if wanted in by else wanted}") if wanted in ok or wanted in apps or wanted not in by else (
            None, why_not(wanted))
    if default in apps:
        return default, f"{by[default]['name']} is your default coding agent (its own agent, in its window)"
    if not ok:
        why = "; ".join(why_not(k) for k in PREFERENCE if k in by)
        if apps:
            return apps[0], f"no command-line coding agent can run{f' ({why})' if why else ''}, so {by[apps[0]]['name']}'s own agent takes it, in its window"
        return None, "no coding agent can run: " + why
    if default in ok:
        return default, f"{by[default]['name']} is your default coding agent"
    fastest = sorted(ok, key=lambda k: (durations.get(k) is None, durations.get(k) or 0, order.index(k)))
    choice = fastest[0]
    if default == "fastest":
        took = durations.get(choice)
        return choice, f"{by[choice]['name']} is the fastest available" + (f" (median {round(took)}s per task here)" if took else " (no timings yet)")
    return choice, f"using {by[choice]['name']} because {why_not(default)}"


def summary(tools, only=None):
    """Plain-language list for chat and voice. only: 'coding' for coding agents."""
    rows = [t for t in tools if t["installed"] and (only != "coding" or t["kind"] == "coding agent" or handoff(t))]
    if not rows:
        return "No coding agents are installed." if only == "coding" else "I found no AI tools on this computer."
    word = {AVAILABLE: "available", UNAVAILABLE: "unavailable", UNSUPPORTED: "detected but unsupported",
            NOT_AUTHENTICATED: "not authenticated", ERROR: "error"}
    lines = []
    for t in rows:
        st = t.get("status") or status_of(t)
        how = {"headless": "Monday can run tasks with it", "models": "chat models",
               "handoff": "Monday can hand its own agent a prompt when no command-line agent can run"}.get(t["integration"], "") if st == AVAILABLE else ""
        lines.append(f"• {t['name']}{' ' + t['version'] if t.get('version') else ''}: {word[st]}"
                     + (f". {how}" if how else "") + (f". {t['detail'].rstrip('.')}" if t.get("detail") else "") + ".")
    missing = [t["name"] for t in tools if not t["installed"] and (only != "coding" or t["kind"] == "coding agent")]
    return "\n".join(lines) + (f"\nNot installed: {', '.join(missing)}." if missing else "")


if __name__ == "__main__":  # python -m core.agents
    T = [{"key": "claude_code", "name": "Claude Code", "installed": True, "integration": "headless", "auth": "not_logged_in", "kind": "coding agent", "detail": "not logged in"},
         {"key": "codex", "name": "Codex", "installed": True, "integration": "headless", "auth": "blocked", "kind": "coding agent", "detail": "usage limit until 7 Oct"},
         {"key": "gemini_cli", "name": "Gemini CLI", "installed": True, "integration": "headless", "auth": "configured", "kind": "coding agent", "detail": ""},
         {"key": "kilo", "name": "Kilo Code CLI", "installed": True, "integration": "headless", "auth": "configured", "kind": "coding agent", "detail": ""},
         {"key": "kiro", "name": "Kiro", "installed": True, "integration": "editor", "auth": "unknown", "kind": "editor", "detail": ""}]
    assert pick_coder(T, default="claude_code")[0] == "gemini_cli" and "Claude Code" in pick_coder(T, default="claude_code")[1]
    assert pick_coder(T, default="kilo") == ("kilo", "Kilo Code CLI is your default coding agent")
    # editors whose own agent takes a prompt: the last resort, in the owner's order, and their own choice when they make one the default
    E = [{**t, "auth": "blocked"} if t["key"] in ("gemini_cli", "kilo") else t for t in T] + [
        {"key": "kiro", "name": "Kiro", "installed": True, "integration": "handoff", "auth": "unknown", "kind": "editor", "detail": ""},
        {"key": "antigravity", "name": "Antigravity IDE", "installed": True, "integration": "handoff", "auth": "unknown", "kind": "editor", "detail": ""},
        {"key": "cursor", "name": "Cursor", "installed": True, "integration": "editor", "auth": "unknown", "kind": "editor", "detail": ""}]
    got = pick_coder(E, default="claude_code")
    assert got[0] == "antigravity" and got[1].startswith("no command-line coding agent can run (Claude Code isn't logged in; Codex hit a usage limit") \
        and got[1].endswith("so Antigravity IDE's own agent takes it, in its window"), got
    assert pick_coder([t for t in E if t["key"] != "antigravity"], default="claude_code")[0] == "kiro", "the next editor in the order"
    assert pick_coder(E[-3:], default="claude_code") == ("antigravity", "no command-line coding agent can run, so Antigravity IDE's own agent takes it, in its window")
    assert "• Kiro: available. Monday can hand its own agent a prompt" in summary(E, "coding") and "Cursor" not in summary(E, "coding")
    assert pick_coder(T + E[-3:], default="claude_code")[0] == "gemini_cli", "a command-line agent that can run always comes before an editor"
    assert pick_coder(T + E[-3:], default="kiro") == ("kiro", "Kiro is your default coding agent (its own agent, in its window)")
    assert pick_coder(E, "kiro")[0] == "kiro" and pick_coder(E, "cursor") == (None, "Cursor can't run (unknown)"), "an editor that takes no prompt can't be asked for"
    assert status_of(E[-2]) == AVAILABLE and status_of(E[-1]) == UNSUPPORTED and handoff(E[-2]) and not handoff(E[-1]) and not usable(E[-2])
    import tempfile
    with tempfile.TemporaryDirectory() as d:  # an editor's shim -> what it really runs
        (Path(d) / "app" / "resources" / "app" / "out").mkdir(parents=True)
        (Path(d) / "app" / "bin").mkdir()
        (Path(d) / "app" / "Some IDE.exe").write_bytes(b"")
        (Path(d) / "app" / "resources" / "app" / "out" / "cli.js").write_text("", encoding="utf-8")
        (Path(d) / "app" / "bin" / "some-ide.cmd").write_text('@echo off\nset ELECTRON_RUN_AS_NODE=1\n"%~dp0..\\Some IDE.exe" "%~dp0..\\resources\\app\\out\\cli.js" %*\n', encoding="utf-8")
        real, shutil.which = shutil.which, lambda c: str(Path(d) / "app" / "bin" / "some-ide.cmd") if c.startswith("some-ide") else None
        try:
            assert editor_cli("some-ide") == [str((Path(d) / "app" / "Some IDE.exe").resolve()), str((Path(d) / "app" / "resources" / "app" / "out" / "cli.js").resolve())]
            assert editor_cli("no-such-ide") is None
        finally:
            shutil.which = real
    assert pick_coder(T, default="fastest", durations={"kilo": 40, "gemini_cli": 90})[0] == "kilo"
    assert pick_coder(T, wanted="codex")[0] is None and "usage limit" in pick_coder(T, wanted="codex")[1]
    assert pick_coder([{**t, "auth": "not_logged_in"} for t in T])[0] is None
    assert "Kiro" in summary(T) and "Kiro" not in summary(T, "coding")
    # the owner's five words: what Monday can do with each tool right now
    st = {t["key"]: status_of(t) for t in T}
    assert st == {"claude_code": NOT_AUTHENTICATED, "codex": UNAVAILABLE, "gemini_cli": AVAILABLE, "kilo": AVAILABLE, "kiro": UNSUPPORTED}, st
    assert status_of({"installed": False, "integration": "headless", "auth": "n/a"}) == UNAVAILABLE
    assert status_of({"installed": True, "integration": "installed (no Monday adapter yet)", "auth": "not_logged_in"}) == UNSUPPORTED, \
        "a CLI without Monday's adapter can't be used by Monday, signed in or not"
    assert status_of({"installed": True, "integration": "headless", "auth": "unknown"}) == ERROR, "a check that failed is an error, never available"
    assert "not authenticated" in summary(T) and "detected but unsupported" in summary(T)
    # the authentication registry (2026-10-27): each auth word's status, contexts kept apart, app rows not applicable, what is required
    S = {t["key"]: auth_fields({**t, "setup": setup_for(t["key"], True)}) for t in T}
    assert [S[k]["auth_status"] for k in ("claude_code", "codex", "gemini_cli", "kilo", "kiro")] == \
        ["not_authenticated", "authenticated", "unknown", "unknown", "not_applicable"], S
    assert (S["claude_code"]["last_error"], S["codex"]["last_error"], S["gemini_cli"]["last_error"], S["kiro"]["last_error"]) == \
        ("not_logged_in", "usage_limit", None, None), "blocked is signed in with a usage limit; configured isn't an error"
    assert S["claude_code"]["context"] == "cli" and S["kiro"]["context"] == "editor" and S["claude_code"]["remediation"] == SETUP["claude_code"]["login_how"]
    assert S["gemini_cli"]["remediation"] == NOT_PROVEN and S["codex"]["remediation"] == "usage limit until 7 Oct" and S["kiro"]["remediation"] is None
    assert S["claude_code"]["capabilities"] == "coding tasks" and S["kiro"]["capabilities"] == "open" and S["gemini_cli"]["last_ok_at"], S["gemini_cli"]
    assert S["claude_code"]["last_ok_at"] is None and S["claude_code"]["checked_at"] and not any(f["required"] for f in S.values()), "required: mark_required"
    app = auth_fields({"key": "claude_app", "installed": True, "integration": "app", "auth": "unknown", "kind": "app", "detail": "", "setup": {}})
    assert (app["auth_status"], app["context"], app["last_error"], app["remediation"]) == ("not_applicable", "desktop_app", None, None), \
        ("the desktop app's session is its own: never read, never taken for the CLI's", app)
    failed = auth_fields({"key": "grok_build", "installed": True, "integration": "headless", "auth": "unknown", "kind": "coding agent", "detail": "x", "setup": {}})
    assert (failed["auth_status"], failed["last_error"]) == ("unknown", "check_failed"), "a headless CLI whose check failed"
    probed = auth_fields({**T[0], "auth": "unknown", "setup": {}}, {"checked_at": 5.0, "last_ok_at": 1.0, "last_error": "timeout"})
    assert (probed["last_error"], probed["checked_at"], probed["last_ok_at"]) == ("timeout", 5.0, 1.0), "the probe's memory, when there is one"
    off = auth_fields({"key": "ollama", "installed": True, "integration": "models", "auth": "offline", "kind": "models", "detail": "the Ollama server isn't running", "setup": {}})
    assert (off["auth_status"], off["context"], off["capabilities"], off["last_error"], off["remediation"]) == \
        ("connection_unavailable", "local_runtime", "chat", "offline", "the Ollama server isn't running"), off
    hm = auth_fields({"key": "hermes", "installed": True, "integration": "headless", "auth": "verified", "kind": "computer agent", "detail": "", "setup": {}})
    assert (hm["auth_status"], hm["capabilities"], hm["last_error"]) == ("authenticated", "computer work", None)
    gone = auth_fields({"key": "droid", "installed": False, "integration": "not installed", "auth": "n/a", "kind": "coding agent", "detail": "not installed",
                        "setup": setup_for("droid")})
    assert (gone["auth_status"], gone["last_error"]) == ("configuration_missing", "not_installed") and "npm install -g droid" in gone["remediation"], gone
    assert all(f["auth_status"] in AUTH_STATUSES and f["context"] in CONTEXTS for f in (*S.values(), app, failed, probed, off, hm, gone))
    R = {t["key"]: t for t in mark_required([dict(t) for t in T] + [{"key": "ollama", "kind": "models", "installed": True, "integration": "models", "auth": "verified"},
                                                                      {"key": "hermes", "kind": "computer agent", "installed": True, "integration": "headless", "auth": "verified"}])}
    assert [k for k, t in R.items() if t["required"]] == ["ollama", "hermes"], "another coder can run: the signed-out default is optional"
    R = {t["key"]: t for t in mark_required([{**t, "auth": "not_logged_in"} for t in T], "claude_code")}
    assert R["claude_code"]["required"] and not R["codex"]["required"] and not R["kiro"]["required"], "no coder can run: the default is required"
    assert not any(t["required"] for t in mark_required([dict(t) for t in T], "fastest")), "no single default: none required"
    assert limit_until("try again at Oct 7th, 2099 11:02 PM.") == time.mktime((2099, 10, 7, 23, 2, 0, 0, 0, -1))
    assert limit_until("try again at Oct 7th, 2020 11:02 PM.") is None, "a past date blocks nothing"
    import tempfile
    with tempfile.TemporaryDirectory() as d:  # Codex's session log: only the newest finished turn counts
        day = Path(d) / "sessions" / "2099" / "01" / "01"
        day.mkdir(parents=True)
        msg = "You've hit your usage limit. Try again at Oct 7th, 2099 11:02 PM."
        turn = lambda err: json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "error": err}})  # noqa: E731
        os.environ["CODEX_HOME"] = d
        (day / "rollout-1.jsonl").write_text(turn({"message": msg, "codex_error_info": "usage_limit_exceeded"}) + "\n")
        assert codex_limit() == msg
        (day / "rollout-1.jsonl").write_text(turn({"message": msg, "codex_error_info": "usage_limit_exceeded"}) + "\n" + turn(None) + "\n")
        assert codex_limit() is None, "a later turn that worked means the limit is over"
    listed = "┌  Credentials ~\\.local\\share\\kilo\\auth.json\n│\n●  Kilo Gateway oauth\n│\n└  1 credentials\n".replace("●", "•")
    assert kilo_signins(listed) == ("configured", "signed in: Kilo Gateway"), kilo_signins(listed)
    assert kilo_signins("┌  Credentials\n│\n└  0 credentials\n")[0] == "not_logged_in"
    with tempfile.TemporaryDirectory() as d:  # npm's .cmd shims: the script handed to node, with or without an extension
        tail = 'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\{}" %*\r\n'
        head = '@ECHO off\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n)\r\n'
        for name, script, want in (("kilo", r"@kilocode\cli\bin\kilo", True), ("gem", r"@google\gemini-cli\dist\index.js", True),
                                   ("nat", r"tool\bin\tool.exe", False)):
            (Path(d) / f"{name}.cmd").write_text(head + tail.format(script))
            (Path(d) / "node_modules" / Path(script)).parent.mkdir(parents=True, exist_ok=True)
            (Path(d) / "node_modules" / Path(script)).write_text("")
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
            got = npm_entry(name)
            assert (got is not None and got[1].endswith(Path(script).name)) == want, (name, got)
    print("npm entries:", npm_entry("gemini"), npm_entry("kilocode"))
    print("agents self-check: OK")
