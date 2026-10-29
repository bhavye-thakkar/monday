"""Real readings only. Anything Monday cannot measure is None, which the UIs render as N/A.

- system metrics (psutil, nvidia-smi, the foreground window)
- worker/component health (Hermes, Claude Code, Codex, Ollama, Graphify, ...)
- Graphify cache state per project
- git-based change detection for projects
"""
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
ROOT = Path(__file__).resolve().parent.parent
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA
# Ollama's cloud models that its free usage credits cover (ollama.com, the owner's list, 2026-10-15), in the order chat tries them.
# Named here because a server that can't read its models folder lists none of them (/api/tags empty, 2026-10-15..23: the junction, see
# ollama_env) yet still answers them from ollama.com; a signed-out Ollama says so in each answer's error. ponytail: a fixed list; the
# owner's "use <model>" reaches any other.
OLLAMA_CLOUD = ("gpt-oss:120b-cloud", "gpt-oss:20b-cloud", "gemma4:31b-cloud", "nemotron-3-super:cloud", "nemotron-3-nano:30b-cloud",
                "nemotron-3-ultra:cloud")

# Never inherited by worker processes:
# - a parent Claude Code session's variables (the worker would wait for host-supplied auth), and
# - Monday's own secrets (the Jev key belongs to Monday Core, not to agents that can run shell commands).
_STRIP_ENV = re.compile(r"^(CLAUDE_CODE_|CLAUDECODE$|CLAUDE_AGENT_SDK_VERSION$|CLAUDE_PID$|TYPESAFE_|JEV_)")


def worker_env(owner=False):
    """A child's environment, Claude Code's own variables taken out. MONDAY_WORKER marks Monday's workers, which Claude Code's hooks for the
    owner's own sessions leave alone (core/agent_hooks.py); owner=True: a console the owner works in themselves (no mark)."""
    return {**{k: v for k, v in os.environ.items() if not _STRIP_ENV.match(k)}, **({} if owner else {"MONDAY_WORKER": "1"})}


TERMINALS = (("gnome-terminal", ("--",)), ("konsole", ("-e",)), ("xfce4-terminal", ("-x",)), ("x-terminal-emulator", ("-e",)),
             ("xterm", ("-e",)))  # Linux: the first of these found opens it


def console_argv(argv, cwd, platform=None, which=shutil.which):
    """The command that opens a visible terminal running argv in cwd, left open afterwards for the owner to read and type in: Windows a new
    console (cmd /k), macOS Terminal, Linux the first terminal app found. ValueError when there is none."""
    import shlex
    platform, argv = platform or sys.platform, [str(a) for a in argv]
    if platform == "win32":
        return ["cmd.exe", "/k", *argv]
    line = f"cd {shlex.quote(str(cwd))} && {shlex.join(argv)}"
    if platform == "darwin":
        script = line.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'tell application "Terminal" to do script "{script}"', "-e", 'tell application "Terminal" to activate']
    for name, flag in TERMINALS:
        exe = which(name)
        if exe:
            return [exe, *flag, "bash", "-lc", f"{line}; exec bash"]
    raise ValueError("No terminal app was found to open it in (gnome-terminal, konsole, xfce4-terminal or xterm).")


def console(argv, cwd, env=None):
    """console_argv, started."""
    import subprocess
    cmd = console_argv(argv, cwd)
    if sys.platform == "win32":
        return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE, env=env, cwd=cwd)
    return subprocess.Popen(cmd, env=env, cwd=cwd, start_new_session=True)


# -- executables -------------------------------------------------------------
def _version_key(p: Path):
    return tuple(int(x) for x in re.findall(r"\d+", p.name)) or (0,)


def _unshim(path):
    """npm/uv .cmd shims mangle arguments; resolve to the real .exe they launch when we can."""
    if path and path.lower().endswith(".cmd"):
        try:
            text = Path(path).read_text(errors="ignore")
            for exe in re.findall(r'"([^"%]+\.exe)"', text):
                if Path(exe).exists():
                    return exe
        except OSError:
            pass
    return path


def claude_bundles():
    """Where the desktop app keeps its bundled CLI. The app is an MSIX package, so its %APPDATA%\\Claude is redirected
    into the package's own storage: only processes started from inside the app (e.g. its Code tab) see the first path;
    everything else, such as Start Monday.cmd, finds it under Packages\\Claude_*."""
    return [ROAMING / "Claude" / "claude-code", *LOCAL.glob("Packages/Claude_*/LocalCache/Roaming/Claude/claude-code")]


def claude_exe():
    # The npm `claude.cmd` shim only launches this same file (and forwards at most 9 arguments), so it's no fallback.
    versions = [d for b in claude_bundles() for d in b.glob("*") if (d / "claude.exe").exists()]
    if versions:
        return str(max(versions, key=_version_key) / "claude.exe")
    exe = shutil.which("claude")  # a standalone install (e.g. the native installer's claude.exe)
    return exe if exe and exe.lower().endswith(".exe") else None


def claude_version(exe, printed=""):
    """The CLI's version: the desktop app's bundle names it in the folder (...\\claude-code\\2.0.37\\claude.exe); a standalone claude.exe
    says it itself (`claude --version` prints "2.1.0 (Claude Code)": `printed`). None when neither does."""
    m = re.search(r"claude-code[\\/](\d+(?:\.\d+)+)", exe or "") or re.search(r"(\d+\.\d+(?:\.\d+)?)", printed or "")
    return m.group(1) if m else None


def ollama_exe():
    exe = shutil.which("ollama") or LOCAL / "Programs" / "Ollama" / "ollama.exe"
    return str(exe) if Path(exe).exists() else None


def ollama_env(env=None, home=None):
    """The environment `ollama serve` gets: a worker's, with Ollama's models folder named by its real path when the default one is reached
    through a link. Ollama 0.40 on Windows resolves each model's manifest with Go's filepath.EvalSymlinks, which fails through a directory
    junction ("The system cannot find the path specified"): on this PC %USERPROFILE%\\.ollama is a junction to E:\\ollama\\data\\dot-ollama,
    and from 2026-10-15 (0.40) to 2026-10-23 Ollama found none of the five models downloaded here (only the cloud ones answered, from
    ollama.com): no local model, so no chat without the internet. The owner's own OLLAMA_MODELS, when set, is left as it is."""
    env = dict(worker_env() if env is None else env)
    if not env.get("OLLAMA_MODELS"):
        models = Path(home or HOME) / ".ollama" / "models"
        real = Path(os.path.realpath(models))
        if real != models and real.is_dir():
            env["OLLAMA_MODELS"] = str(real)
    return env


def start_ollama():
    """`ollama serve`, hidden and detached so it keeps running like the tray app's own server would (its log:
    %LOCALAPPDATA%\\Ollama\\monday_serve.log), with its models folder's real path (ollama_env). Returns the pid, or None when there's no
    local Ollama to start."""
    import subprocess
    exe = ollama_exe()
    if not exe or not re.match(r"https?://(127\.0\.0\.1|localhost)(:|$)", OLLAMA):
        return None
    (LOCAL / "Ollama").mkdir(parents=True, exist_ok=True)
    # CREATE_NO_WINDOW, not DETACHED_PROCESS: a detached `ollama serve` has no console, so each console program it
    # starts (its model runner, llama-server.exe) got a new, visible one: a terminal window popped up on the owner's
    # desktop (seen 2026-09-28). A hidden console is inherited by the runners, so nothing shows.
    win = sys.platform == "win32"  # macOS / Linux: no console to hide; its own session, so Monday stopping doesn't take it along
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if win else 0
    with open(LOCAL / "Ollama" / "monday_serve.log", "ab") as log:
        # Break away from a job that owns Monday (a supervisor that kills its tree on stop); a job that forbids it: stay.
        for extra in (subprocess.CREATE_BREAKAWAY_FROM_JOB, 0) if win else (0,):
            try:
                return subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=flags | extra, env=ollama_env(), cwd=str(HOME), start_new_session=not win).pid
            except OSError:
                if not extra:
                    raise


_tls = None


def tls():
    """httpx's own TLS context, built once and passed as `verify=` to every client. Each `httpx.AsyncClient()` otherwise builds
    its own, which takes ~240 ms on this PC (it loads the certificate bundle), inside the event loop. Until 2026-10-05 every
    Ollama health check, chat call and provider call did: everything queued behind it, and during warm-up Jev's 2.5 s budget
    ran out there (measured: 240 ms a client, 0.06 ms with this)."""
    global _tls
    if _tls is None:
        _tls = httpx.create_ssl_context()
    return _tls


def ollama_listening():
    """Something holds Ollama's port: a server that may still be starting (its first start after a reboot spends ~55 s
    discovering GPUs before it answers) or is busy. Starting a second one then would only fail to bind."""
    import socket
    from urllib.parse import urlsplit
    u = urlsplit(OLLAMA)
    try:
        socket.create_connection((u.hostname, u.port or 11434), timeout=0.5).close()
        return True
    except OSError:
        return False


async def wait_ollama(timeout):
    """True once Ollama lists its models (its version endpoint answers earlier, while listing still fails)."""
    end = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3, verify=tls()) as c:
        while time.monotonic() < end:
            try:
                if (await c.get(f"{OLLAMA}/api/tags")).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    return False


def codex_exe():
    bins = list((LOCAL / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
    if bins:
        return str(max(bins, key=lambda p: p.stat().st_mtime))
    return _unshim(shutil.which("codex"))


def hermes_exe():
    return _unshim(shutil.which("hermes"))


def hermes_acp_exe():
    """Hermes' ACP server entry point sits next to hermes.exe in its venv."""
    exe = hermes_exe()
    if exe:
        acp = Path(exe).with_name("hermes-acp.exe" if exe.lower().endswith(".exe") else "hermes-acp")
        if acp.exists():
            return str(acp)
    return None


def hermes_monday(acp):
    """Windows: Hermes' ACP server as Monday.exe, so Task Manager shows it under Monday (2026-10-21; its launcher hermes-acp.exe, then the
    venv's python.exe, each started the next program, and a different name is a row of its own): the venv's own interpreter (the Python
    its pyvenv.cfg calls home) copied into the venv's Scripts as Monday.exe, running hermes-acp's entry point. -> argv, or None."""
    if sys.platform != "win32" or not acp:
        return None
    from .desktop import APP_EXE, monday_copy
    try:
        venv = Path(acp).parent.parent
        cfg = {k.strip().lower(): v.strip() for k, v in (line.split("=", 1) for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8")
                                                         .splitlines() if "=" in line)}
        home = Path(cfg["home"])
        entry = next(line.split("=", 1)[1].strip() for f in (venv / "Lib" / "site-packages").glob("*.dist-info/entry_points.txt")
                     for line in f.read_text(encoding="utf-8").splitlines() if line.split("=", 1)[0].strip() == Path(acp).stem)
        mod, func = entry.split(":")
        dlls = [home / f for f in (f"python{cfg.get('version_info', '').replace('.', '')[:3]}.dll", "python3.dll", "vcruntime140.dll",
                                   "vcruntime140_1.dll")]
        exe = monday_copy(home / "python.exe", venv / "Scripts" / APP_EXE, dlls)
        return [str(exe), "-c", f"import sys; from {mod} import {func}; sys.exit({func}())"] if exe else None
    except (OSError, KeyError, StopIteration, ValueError):
        return None


def graphify_python():
    saved = ROOT / "graphify-out" / ".graphify_python"
    if saved.exists():
        p = saved.read_text(encoding="utf-8").strip()
        if Path(p).exists():
            return p
    p = ROAMING / "uv" / "tools" / "graphifyy" / "Scripts" / "python.exe"
    return str(p) if p.exists() else None


def graphify_exe():
    return shutil.which("graphify")


async def run(*cmd, cwd=None, timeout=20, stdin_text=None, env=None):
    """Run a command; returns (exit_code, stdout, stderr). exit_code None = could not run/timeout. In a thread: Windows starts a
    process synchronously even under asyncio, and starting claude.exe held Core's event loop ~0.9 s (2026-10-10, asyncio debug)."""
    import subprocess
    try:
        p = await asyncio.to_thread(subprocess.run, cmd, cwd=cwd, env=env or worker_env(), input=stdin_text.encode() if stdin_text is not None else None,
                                    stdin=None if stdin_text is not None else subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                    creationflags=0x08000000 if sys.platform == "win32" else 0)  # CREATE_NO_WINDOW
    except subprocess.TimeoutExpired:  # subprocess.run has killed it
        return None, "", f"timed out after {timeout}s"
    except OSError as e:
        return None, "", str(e)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


# -- system metrics ------------------------------------------------------------
class Metrics:
    GPU_EVERY = 5.0

    def __init__(self):
        psutil.cpu_percent(None)
        self._net = psutil.net_io_counters()
        self._t = time.monotonic()
        self._gpu = None
        self._gpu_at = 0.0
        self._gpu_ok = shutil.which("nvidia-smi") is not None

    async def _gpu_sample(self):
        if not self._gpu_ok or time.monotonic() - self._gpu_at < self.GPU_EVERY:
            return self._gpu
        self._gpu_at = time.monotonic()
        code, out, _ = await run("nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                 "--format=csv,noheader,nounits", timeout=5)
        if code == 0 and out.strip():
            name, util, used, total = [x.strip() for x in out.strip().splitlines()[0].split(",")]
            self._gpu = {"name": name, "util": float(util), "vram_used_mb": float(used), "vram_total_mb": float(total)}
        else:
            self._gpu = None
        return self._gpu

    async def sample(self):
        now = time.monotonic()
        net = psutil.net_io_counters()
        dt = max(now - self._t, 1e-3)
        rx = (net.bytes_recv - self._net.bytes_recv) / dt
        tx = (net.bytes_sent - self._net.bytes_sent) / dt
        self._net, self._t = net, now
        vm = psutil.virtual_memory()
        return {
            "ts": time.time(),
            "cpu": psutil.cpu_percent(None),
            "ram": {"percent": vm.percent, "used": vm.used, "total": vm.total},
            "gpu": await self._gpu_sample(),
            "net": {"rx_bps": rx, "tx_bps": tx},
            "active_app": foreground_app(),
            "security": None,  # no owner-recognition module exists yet
        }


def foreground_app():
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = psutil.Process(pid.value).name() if pid.value else None
        return {"process": name, "title": buf.value[:120]}
    except Exception:
        return None


# -- component health ------------------------------------------------------------
INTERNET_URLS = ("https://ollama.com", "https://www.google.com/generate_204")  # either answering: this PC reaches the internet
INTERNET_TIMEOUT_S, INTERNET_TTL_S = 3.0, 30  # asked at most this long, and again at most this often (a failed cloud call asks again sooner)
INTERNET_RETRY_S = 8.0  # neither connected that fast: asked once more, this long, before "offline" (a slow network isn't none)
# a host that took the connection and then was slow to answer: the internet is there (2026-10-24, the owner's PC: the same two requests
# took 0.1-1.4 s or timed out reading, 3-6 s; one round where both did made Core "offline", so research said it couldn't search and chat
# went to the models on this PC)
REACHED = (httpx.ReadTimeout, httpx.ReadError, httpx.WriteTimeout, httpx.RemoteProtocolError)
OK_STATUSES = ("ready", "online")  # a check's answer that counts as a success (Health.meta: last_ok_at)
# The Claude CLI's own sign-in (`claude auth status`, asked with a worker's environment): the Claude desktop app's session is another
# context that never reaches it, and the two are never equated (2026-10-27: the owner, signed in to the app and not the CLI, was nagged
# at every start). This is the detail a signed-out CLI gets, here and in the registry (core/agents.py).
CLAUDE_LOGIN = "the CLI isn't signed in (the Claude desktop app's sign-in is separate): run `claude` in a terminal and /login once"
_claude_versions = {}  # (a standalone claude.exe, its mtime) -> its version: asked once per build (`claude --version`)


class Health:
    """Cached health checks. Cheap ones refresh often; ones that spawn processes refresh rarely. meta(key) says when each check last ran,
    when it last succeeded and the last answer's error category."""

    def __init__(self):
        self._cache, self._meta = {}, {}

    async def _cached(self, key, ttl, fn):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        self._cache[key] = (time.monotonic(), val)
        now, ok = time.time(), val.get("status") in OK_STATUSES
        self._meta[key] = {"checked_at": now, "last_ok_at": now if ok else self.meta(key)["last_ok_at"],
                           "last_error": None if ok else val.get("error") or val.get("status") or "error"}
        return val

    def meta(self, key):
        """A check's memory, wall clock: checked_at (its last run), last_ok_at (its last ready/online answer, kept across invalidate) and
        last_error (the last answer's category: not_logged_in, not_installed, timeout, error, offline, not_configured…; None after a
        success). Words about the check only, never a key or a token. All None for a check that hasn't run."""
        return dict(self._meta.get(key) or {"checked_at": None, "last_ok_at": None, "last_error": None})

    def invalidate(self, key=None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    def known(self, key, ttl):
        """A check's last answer while it is fresh (within ttl), never asking again: None when there is none (Monday._route reads the
        internet's this way, so routing never waits for a check)."""
        hit = self._cache.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < ttl else None

    async def internet(self):
        """Does this PC reach the internet now, the way Monday's own requests do (HTTPS, through any proxy its environment names)? Neither
        Core nor Ollama depends on it: without it Monday still answers with the models on this PC and works its files and apps; only the
        cloud models, web research and online services wait. Never asked before Monday starts, only when something needs to know."""
        async def head(c, url):
            try:
                await c.head(url)
                return None
            except REACHED:
                return None
            except httpx.HTTPError as e:
                return f"{urlsplit(url).hostname}: {type(e).__name__}"

        async def check():
            for limit in (INTERNET_TIMEOUT_S, INTERNET_RETRY_S):
                async with httpx.AsyncClient(timeout=limit, verify=tls()) as c:
                    why = await asyncio.gather(*(head(c, u) for u in INTERNET_URLS))
                if None in why:
                    return {"status": "online", "detail": f"Reaches {urlsplit(INTERNET_URLS[why.index(None)]).hostname}."}
            return {"status": "offline", "detail": f"No internet ({'; '.join(why)}). Monday works on this PC with its local models, files and "
                                                   "apps; web research and the cloud models wait until it's back."}
        return await self._cached("internet", INTERNET_TTL_S, check)

    async def ollama(self):
        async def check():
            try:
                async with httpx.AsyncClient(timeout=2, verify=tls()) as c:
                    r = await c.get(f"{OLLAMA}/api/tags")
                    r.raise_for_status()  # a 500 while it finishes starting isn't "online with no models"
                    tags = r.json().get("models", [])
                listed = [{"name": m["name"], "remote": bool(m.get("remote_host"))} for m in tags]
                # the owner's cloud models first and in their order (chat tries them so: gpt-oss first), listed by Ollama or not; then the rest
                return {"status": "online", "url": OLLAMA, "models": [{"name": n, "remote": True} for n in OLLAMA_CLOUD]
                        + [m for m in listed if m["name"] not in OLLAMA_CLOUD]}
            except Exception as e:
                return {"status": "offline", "url": OLLAMA, "models": [], "detail": type(e).__name__}
        return await self._cached("ollama", 10, check)

    async def claude(self):
        async def check():
            exe = await asyncio.to_thread(claude_exe)  # a folder and PATH scan: 1.2-1.7 s inside Core (2026-10-10), off the event loop
            if not exe:
                if any(b.exists() for b in claude_bundles()):
                    return {"status": "error", "detail": "the Claude app's CLI folder has no claude.exe right now "
                                                         "(it may be updating); Monday checks again in a minute"}
                return {"status": "not_installed"}
            code, out, err = await run(exe, "auth", "status", timeout=20)
            try:
                info = json.loads(out)
            except ValueError:
                first = next((l.strip() for l in (err or out or "").splitlines() if l.strip()), "")
                return {"status": "error", "error": "timeout" if (err or "").startswith("timed out") else "error",
                        "detail": f"`claude auth status` didn't answer: {first[:140] or f'exit {code}'}"}
            ver = claude_version(exe)
            if not ver:  # a standalone claude.exe: asked once per build of the file
                build = (exe, os.path.getmtime(exe) if os.path.exists(exe) else None)
                if build not in _claude_versions:
                    _claude_versions[build] = claude_version(exe, (await run(exe, "--version", timeout=20))[1])
                ver = _claude_versions[build]
            base = {"version": ver, "exe": exe}
            if info.get("loggedIn"):
                return {**base, "status": "ready", "detail": info.get("authMethod")}
            return {**base, "status": "not_logged_in", "detail": CLAUDE_LOGIN}
        return await self._cached("claude", 60, check)

    async def codex(self):
        async def check():
            exe = codex_exe()
            if not exe:
                return {"status": "not_installed"}
            code, out, err = await run(exe, "login", "status", timeout=20)
            text = (out + err).strip()
            if code == 0 and "logged in" in text.lower():
                return {"status": "ready", "detail": text.splitlines()[0][:120], "exe": exe}
            if code is None:  # it couldn't run or didn't answer in time: not an answer about the sign-in
                return {"status": "error", "error": "timeout" if (err or "").startswith("timed out") else "error",
                        "detail": f"`codex login status` didn't answer: {text[:140]}", "exe": exe}
            return {"status": "not_logged_in", "detail": text[:200], "exe": exe}
        return await self._cached("codex", 300, check)

    async def hermes(self):
        async def check():
            exe = hermes_exe()
            if not exe:
                return {"status": "not_installed"}
            cfg = hermes_model_config()
            provider_ok = True
            if cfg.get("provider") == "ollama":
                provider_ok = (await self.ollama())["status"] == "online"
            return {"status": "online" if provider_ok else "offline", "exe": exe,
                    "model": cfg.get("default"), "provider": cfg.get("provider"),
                    "detail": None if provider_ok else f"Model provider {cfg.get('provider')} is unreachable."}
        return await self._cached("hermes", 10, check)

    async def graphify(self):
        async def check():
            py = graphify_python()
            return {"status": "ready" if py else "not_installed", "python": py}
        return await self._cached("graphify", 300, check)

    async def all(self):
        ollama, claude, codex, hermes, graphify, internet = await asyncio.gather(
            self.ollama(), self.claude(), self.codex(), self.hermes(), self.graphify(), self.internet())
        return {  # "jev" is added by Monday from JevService.health()
            "hermes": hermes, "claude_code": claude, "codex": codex, "ollama": ollama, "graphify": graphify, "internet": internet,
            "freellmapi": {"status": "not_configured", "detail": "No FreeLLMAPI endpoint configured."},
            "ai_providers": providers_status(),
            "calendar": {"status": "not_configured", "detail": "No calendar provider connected."},
            "computer_use": ({"status": "ready", "detail": f"Monday's agent can see and operate desktop apps (cua-driver at {cua_driver()}; "
                                                           "Monday blocks killing apps, the clipboard, recordings and installs)."}
                             if cua_driver() else {"status": "not_configured", "detail": "No MONDAY_CUA_DRIVER set: Monday's agent has no screen control."}),
            "security": {"status": "not_configured", "detail": "Owner recognition is not implemented."},
            "obsidian": obsidian_status(),
        }


def obsidian_status():
    vault = ROOT / "Monday"
    if not (vault / ".obsidian").exists():
        return {"status": "not_configured", "detail": "No Obsidian vault at Monday/."}
    notes = [p for p in vault.rglob("*.md") if ".obsidian" not in p.parts]
    return {"status": "ready", "detail": f"Monday writes decisions, preferences and project history to the vault ({len(notes)} note"
                                         f"{'' if len(notes) == 1 else 's'} so far); clicks and chat are never saved."}


def cua_driver():
    """cua-driver.exe from MONDAY_CUA_DRIVER (environment or Monday/.env), or None when screen control is off."""
    from .jev import setting
    exe = setting("MONDAY_CUA_DRIVER")
    return exe if exe and Path(exe).is_file() else None


def cua_env(exe):
    """The driver's environment: telemetry and update checks off, its home next to the install (E:\\cua\\profile). The driver to run,
    MONDAY_CUA_DRIVER, is on Windows the same program as Monday.exe (desktop.monday_links: Task Manager's one "Monday", 2026-10-21)."""
    from .desktop import RUNTIME, monday_links
    return {"MONDAY_CUA_DRIVER": monday_links(exe, RUNTIME / "cua") if sys.platform == "win32" else exe,
            "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0", "CUA_DRIVER_RS_UPDATE_CHECK": "0", "USERPROFILE": str(Path(exe).parent.parent / "profile")}


def computer_use_mcp():
    """ACP `mcpServers` entry giving Hermes desktop control: cua-driver behind Monday's tool filter, or []."""
    exe = cua_driver()
    if not exe:
        return []
    return [{"name": "computer", "command": sys.executable, "args": [str(ROOT / "core" / "cua_proxy.py")],
             "env": [{"name": k, "value": v} for k, v in cua_env(exe).items()]}]


def providers_status():
    """Which chat AIs besides Ollama can answer: a key present (names only, never the keys) or the owner's CLI installed."""
    from . import providers
    P, have = providers.PROVIDERS, providers.configured()
    on = [P[p]["label"] + (" (its CLI, your sign-in)" if P[p]["style"] == "cli" else "") for p, ok in have.items() if ok]
    off = [f"{P[p]['label']} ({P[p]['key']})" for p, ok in have.items() if not ok and "key" in P[p]]
    return {"status": "ready" if on else "not_configured", "configured": on,
            "detail": (f"Ready: {', '.join(on)}. " if on else "") + (f"Add a key to Monday\\.env for: {', '.join(off)}." if off else "")}


def hermes_config():
    """Hermes' own config.yaml (read-only)."""
    try:
        import yaml
        return yaml.safe_load((LOCAL / "hermes" / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def hermes_model_config():
    return hermes_config().get("model") or {}


def hermes_approval_timeout():
    """Seconds Hermes waits for an approval before it blocks the action itself (approvals.timeout, default 300)."""
    try:
        return float((hermes_config().get("approvals") or {}).get("timeout", 300))
    except (TypeError, ValueError):
        return 300.0


# -- Graphify ------------------------------------------------------------------
_graph_stats = {}


def graph_stats(root: Path):
    """Node/edge counts of graphify-out/graph.json, cached by mtime (the file can be large)."""
    g = root / "graphify-out" / "graph.json"
    if not g.exists():
        return None
    mtime = g.stat().st_mtime
    hit = _graph_stats.get(str(g))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(g.read_text(encoding="utf-8"))
        stats = {"nodes": len(data.get("nodes", [])),
                 "edges": len(data.get("links", data.get("edges", []))),
                 "analyzed_at": mtime}
    except (ValueError, OSError):
        stats = {"nodes": None, "edges": None, "analyzed_at": mtime, "corrupt": True}
    _graph_stats[str(g)] = (mtime, stats)
    return stats


async def graphify_status(root: Path):
    """Cache state for one project: is there a graph, when was it built, what changed since."""
    stats = graph_stats(root)
    if not stats:
        return {"cached": False, "detail": "No Graphify graph for this project yet."}
    py = graphify_python()
    changed = None
    if py:
        code, out, err = await run(py, str(ROOT / "core" / "graphify_probe.py"), str(root), timeout=60)
        if code == 0:
            try:
                changed = json.loads(out)
            except ValueError:
                changed = None
    manifest = root / "graphify-out" / "manifest.json"
    return {
        "cached": not stats.get("corrupt"),
        "nodes": stats["nodes"], "edges": stats["edges"],
        "analyzed_at": manifest.stat().st_mtime if manifest.exists() else stats["analyzed_at"],
        "code_changed": len(changed["code_changed"]) if changed else None,
        "docs_changed": len(changed["docs_changed"]) if changed else None,
        "deleted": len(changed["deleted"]) if changed else None,
        "changed_files": ((changed["code_changed"] + changed["docs_changed"])[:50] if changed else None),
    }


async def graphify_relevant(root: Path, question: str, budget=800):
    """Ask the cached graph which files matter for a request (no LLM, no rescan)."""
    exe = graphify_exe()
    graph = root / "graphify-out" / "graph.json"
    if not exe or not graph.exists():
        return None
    code, out, err = await run(exe, "query", question, "--graph", str(graph), "--budget", str(budget),
                               cwd=str(root), timeout=60)
    if code != 0:
        return None
    # Lines look like: NODE <label> [src=<file> loc=L12 community=<name>]
    files = []
    for m in re.finditer(r"\[src=(.*?)\s+(?:loc|community)=", out):  # nodes without a file have "src= loc=": skipped below
        f = m.group(1).strip()
        if f and f not in files:
            files.append(f)
    return {"files": files[:25], "excerpt": out[:4000]}


async def graphify_update(root: Path):
    """Incremental AST re-extraction of changed code (graphify's own cache skips unchanged files)."""
    exe = graphify_exe()
    if not exe:
        return {"status": "skipped", "detail": "graphify is not installed"}
    t0 = time.time()
    code, out, err = await run(exe, "update", str(root), cwd=str(root), timeout=600)
    _graph_stats.pop(str(root / "graphify-out" / "graph.json"), None)
    return {"status": "success" if code == 0 else "failed", "exit_code": code,
            "seconds": round(time.time() - t0, 1), "output": (out + err).strip()[-1500:]}


# -- git -----------------------------------------------------------------------
async def git_dirty(root: Path):
    """Set of paths git reports as modified/added/deleted/untracked, or None if not a git repo."""
    code, out, _ = await run("git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=30)
    if code != 0:
        return None
    entries = [e for e in out.split("\0") if e]
    paths = set()
    skip = False
    for e in entries:
        if skip:
            skip = False
            continue
        status, path = e[:2], e[3:]
        paths.add(path)
        if status[0] in "RC":
            skip = True  # rename/copy: next entry is the old path
    return paths


async def git_diff(root: Path, files=None, max_bytes=200_000):
    code, stat, _ = await run("git", "-C", str(root), "diff", "--stat", "HEAD", "--", *(files or []), timeout=30)
    if code != 0:
        return None
    _, patch, _ = await run("git", "-C", str(root), "diff", "HEAD", "--", *(files or []), timeout=30)
    return {"stat": stat.strip(), "patch": patch[:max_bytes], "truncated": len(patch) > max_bytes}


# -- what Monday needs before anything else works ------------------------------------------------------------------------------------
# The owner, 2026-10-28: "in the core or in the preflight add the non existing and mandatory things too". Checked at every Core start
# (Monday.start: what can be made is made, the rest is said by the start-up check as the owner's to do) and by preflight. The packages
# are installer/build.py's PACKAGES by their import names: the setup copies them; one missing here is said with the command that adds it
# (2026-10-28: the phone companion's certificate needed `cryptography`, which the setup didn't carry).
NEEDED = (("fastapi", "fastapi", "Core's API"), ("uvicorn", "uvicorn", "Core's server"), ("pydantic", "pydantic", "Core's API"),
          ("httpx", "httpx", "every call Monday makes"), ("typesafe_sdk", "typesafe-sdk", "Jev"), ("psutil", "psutil", "the health checks"),
          ("requests", "requests", "reading the web"), ("yaml", "PyYAML", "settings files"), ("pypdf", "pypdf", "reading PDFs"),
          ("PIL", "pillow", "pictures and the icon"),
          ("cryptography", "cryptography", "the phone companion's certificate and the update signatures"))
NEEDED_WIN = (("win32api", "pywin32", "shortcuts and windows"), ("winrt", "winrt-runtime", "Windows' text reading and media keys"))
NEEDED_PYLIB = (("PySide6", "PySide6", "the window and the notch"),)  # beside the app in pylib (E:/monday/pylib; the setup's pylib)
NEEDED_DIRS = ("data", "data/logs", "data/tmp", "data/tmp/scratch")


def _startup_link_here(root, fix):
    """Windows' start-with-Windows shortcut, when the owner has one (General's switch): it must start this folder's Monday. -> None when it
    does or there is none; else what was done ("made: …") or what is wrong."""
    try:
        import pythoncom
        import win32com.client
        from .desktop import startup, startup_link
        p = startup_link()
        if not p.exists():
            return None
        pythoncom.CoInitialize()
        try:
            wd = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(p)).WorkingDirectory
        finally:
            pythoncom.CoUninitialize()
        if wd and os.path.normcase(os.path.realpath(wd)) == os.path.normcase(os.path.realpath(root)):
            return None
        if not fix:
            return f"the start-with-Windows shortcut starts {wd or 'nothing'}, not this Monday ({root})"
        startup(True)
        return f"made: the start-with-Windows shortcut again (it started {wd or 'nothing'})"
    except Exception:  # (no shell API here: nothing to say about a shortcut)
        return None


def mandatory(root=ROOT, fix=False, needed=None, link=None):
    """What Monday needs before anything else works: the folders it writes to (made when fix), the packages it imports (said, with the
    command that adds each), and on Windows the start-with-Windows shortcut pointing at this folder (made again when fix). link: the
    shortcut's verdict, given (the self-check). -> {"ok", "made": [...], "missing": [...], "detail"}"""
    import importlib.util
    root, made, missing = Path(root), [], []
    for d in NEEDED_DIRS:
        p = root / d
        if p.is_dir():
            continue
        if not fix:
            missing.append(f"the folder {d}")
            continue
        try:
            p.mkdir(parents=True, exist_ok=True)
            made.append(f"the folder {d}")
        except OSError as e:
            missing.append(f"the folder {d} ({e.strerror or e})")
    pylib = root.parent / "pylib"
    for name, dist, why in (needed if needed is not None else NEEDED + (NEEDED_WIN if sys.platform == "win32" else ()) + NEEDED_PYLIB):
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not (found or (pylib / name).is_dir()):
            missing.append(f"{dist} ({why}): pip install {dist}")
    said = link if link is not None else (_startup_link_here(root, fix) if sys.platform == "win32" else None)
    if said:
        (made if said.startswith("made: ") else missing).append(said.removeprefix("made: "))
    detail = ((f"Missing: {'; '.join(missing)}." if missing else "Every part Monday needs is here.")
              + (f" Made just now: {', '.join(made)}." if made else ""))
    return {"ok": not missing, "made": made, "missing": missing, "detail": detail}


if __name__ == "__main__":  # python -m core.probes: the checks that need no service running
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        real, home, plain = Path(d) / "real" / "models", Path(d) / "home", Path(d) / "plain"
        real.mkdir(parents=True)
        (plain / ".ollama" / "models").mkdir(parents=True)
        home.mkdir()
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(real.parent), str(home / ".ollama"))  # (what this PC has: %USERPROFILE%\.ollama -> E:\...)
        else:
            os.symlink(real.parent, home / ".ollama")
        same = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))  # noqa: E731
        got = ollama_env({"PATH": "x"}, home)
        assert same(got.get("OLLAMA_MODELS", ""), real) and got["PATH"] == "x", ("Ollama gets the models folder's real path", got)
        assert "OLLAMA_MODELS" not in ollama_env({}, plain), "a plain folder: Ollama's own default, nothing set"
        assert ollama_env({"OLLAMA_MODELS": "Z:/m"}, home)["OLLAMA_MODELS"] == "Z:/m", "the owner's own OLLAMA_MODELS stays"
    # the internet (2026-10-24): a host that took the connection and was slow to answer is there; nothing connecting, asked twice, is offline
    _real_client, _asked = httpx.AsyncClient, []

    def _net(fail):
        def answer(request):
            _asked.append(request.url.host)
            raise fail("stand-in", request=request)
        return lambda **k: _real_client(transport=httpx.MockTransport(answer), timeout=k.get("timeout"))
    try:
        httpx.AsyncClient = _net(httpx.ReadTimeout)
        assert asyncio.run(Health().internet())["status"] == "online" and len(_asked) == 2, "connected, then slow: online, asked once"
        _asked.clear()
        httpx.AsyncClient = _net(httpx.ConnectError)
        r = asyncio.run(Health().internet())
        assert r["status"] == "offline" and len(_asked) == 4 and "ConnectError" in r["detail"], ("asked twice before offline", r, _asked)
    finally:
        httpx.AsyncClient = _real_client
    # the sign-in probes (2026-10-27): the CLIs' own answers through the parsers, what Health.meta remembers of each check, and never a
    # key's value in an answer. The Claude desktop app's session is another context: a worker's environment carries nothing of it.
    os.environ["CLAUDE_CODE_SELF_CHECK"] = "1"
    assert "CLAUDE_CODE_SELF_CHECK" not in worker_env() and worker_env()["MONDAY_WORKER"] == "1", "a worker inherits no session of Claude's"
    assert claude_version(r"C:\Users\x\AppData\Roaming\Claude\claude-code\2.0.37\claude.exe") == "2.0.37"
    assert claude_version(r"C:\Users\x\.local\bin\claude.exe") is None and claude_version(r"C:\x\claude.exe", "2.1.0 (Claude Code)") == "2.1.0"
    _run, _claude_exe, _codex_exe, _bundles, said = run, claude_exe, codex_exe, claude_bundles, {}

    async def _stub(*cmd, **k):
        return said.get(tuple(cmd[1:]), (None, "", "timed out after 20s"))  # (what subprocess.run's timeout comes back as)
    run, claude_exe, codex_exe = _stub, lambda: r"C:\x\claude-code\2.0.37\claude.exe", lambda: r"C:\x\codex.exe"
    try:
        h = Health()
        said[("auth", "status")] = (0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiKey": "sk-ant-NEVER-SHOWN"}), "")
        r = asyncio.run(h.claude())
        assert (r["status"], r["detail"], r["version"]) == ("ready", "claude.ai", "2.0.37"), r
        m = h.meta("claude")
        assert m["checked_at"] and m["last_ok_at"] == m["checked_at"] and m["last_error"] is None, m
        assert "NEVER-SHOWN" not in json.dumps(r) and set(m) == {"checked_at", "last_ok_at", "last_error"}, "no key value in a status dict"
        h.invalidate("claude")
        said[("auth", "status")] = (0, json.dumps({"loggedIn": False}), "")
        r = asyncio.run(h.claude())
        assert (r["status"], r["detail"]) == ("not_logged_in", CLAUDE_LOGIN) and "desktop app" in r["detail"], r
        assert h.meta("claude")["last_error"] == "not_logged_in" and h.meta("claude")["last_ok_at"] == m["last_ok_at"], "the last success is kept"
        assert asyncio.run(h.claude()) is r, "cached for its TTL"
        h.invalidate("claude")
        said.clear()
        r = asyncio.run(h.claude())
        assert r["status"] == "error" and "timed out" in r["detail"] and h.meta("claude")["last_error"] == "timeout", (r, h.meta("claude"))
        h.invalidate("claude")
        claude_exe = lambda: r"C:\x\bin\claude.exe"  # noqa: E731  (a standalone install: its version from `claude --version`, asked once)
        said[("auth", "status")], said[("--version",)] = (0, json.dumps({"loggedIn": True, "authMethod": "console"}), ""), (0, "2.1.0 (Claude Code)", "")
        assert asyncio.run(h.claude())["version"] == "2.1.0" and list(_claude_versions.values()) == ["2.1.0"], _claude_versions
        h.invalidate("claude")
        claude_exe, claude_bundles = (lambda: None), (lambda: [])  # noqa: E731
        assert asyncio.run(h.claude()) == {"status": "not_installed"} and h.meta("claude")["last_error"] == "not_installed"
        assert h.meta("never") == {"checked_at": None, "last_ok_at": None, "last_error": None}, "a check that hasn't run"
        said[("login", "status")] = (0, "Logged in using ChatGPT", "")
        r = asyncio.run(h.codex())
        assert (r["status"], r["detail"]) == ("ready", "Logged in using ChatGPT") and h.meta("codex")["last_error"] is None, r
        h.invalidate("codex")
        said[("login", "status")] = (1, "Not logged in", "")
        assert asyncio.run(h.codex())["status"] == "not_logged_in" and h.meta("codex")["last_error"] == "not_logged_in"
        h.invalidate("codex")
        said.clear()
        r = asyncio.run(h.codex())
        assert r["status"] == "error" and h.meta("codex")["last_error"] == "timeout", ("a timeout isn't 'not logged in'", r)
        h.invalidate("codex")
        codex_exe = lambda: None  # noqa: E731
        assert asyncio.run(h.codex()) == {"status": "not_installed"} and h.meta("codex")["last_error"] == "not_installed"
    finally:
        run, claude_exe, codex_exe, claude_bundles = _run, _claude_exe, _codex_exe, _bundles
    # what Monday needs before anything else (2026-10-28): folders made when asked, a missing package said with its command, a shortcut
    # that starts another folder's Monday said (or made again), and nothing said when all is there
    with tempfile.TemporaryDirectory() as d:
        r = mandatory(Path(d) / "app", needed=(("json", "json", "x"),), link="")
        assert not r["ok"] and r["missing"][:1] == ["the folder data"] and not r["made"], r
        r = mandatory(Path(d) / "app", fix=True, needed=(("json", "json", "x"), ("no_such_pkg_xyz", "no-such-pkg", "a test")), link="")
        assert r["made"] == [f"the folder {x}" for x in NEEDED_DIRS] and (Path(d) / "app" / "data" / "tmp" / "scratch").is_dir(), r
        assert r["missing"] == ["no-such-pkg (a test): pip install no-such-pkg"] and r["detail"].startswith("Missing: no-such-pkg"), r
        r = mandatory(Path(d) / "app", needed=(), link="made: the start-with-Windows shortcut again (it started D:/old)")
        assert r["ok"] and r["made"] == ["the start-with-Windows shortcut again (it started D:/old)"], r
        r = mandatory(Path(d) / "app", needed=(), link="the start-with-Windows shortcut starts D:/old, not this Monday")
        assert not r["ok"] and "starts D:/old" in r["detail"], r
        assert mandatory(Path(d) / "app", needed=(), link="") == {"ok": True, "made": [], "missing": [],
                                                                    "detail": "Every part Monday needs is here."}
    here = mandatory()
    assert here["ok"], f"this PC is missing something Monday needs: {here['detail']}"
    print("probes self-check: OK")
