"""Terminal: shell commands for Monday, run only after router.permit() has ruled on them (command_risk tiers).

- run():    a command to completion in a project folder; output streams to the task log line by line.
- start():  a long-running command (a dev server) that stays up until its task is stopped.
- detect(): the test and dev-server commands a project declares (Monday's own `commands` on the project row,
            .claude/launch.json, package.json, pytest, Cargo, go.mod, .NET, Maven, Gradle).
- port_owners(): which process holds a TCP port.
- test_summary(): pass/fail counts and failing test names from common runners' output (no model needed).
Self-check: python -m core.terminal
"""
import asyncio
import base64
import json
import re
import sys
import time
from collections import deque
from pathlib import Path

import psutil

from . import probes

RUN_TIMEOUT_S = 15 * 60
TAIL_LINES = 200
SERVER_READY_S = 25
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_POWERSHELL = re.compile(r"\b(?:get|set|new|remove|start|stop|select|where|foreach|sort|test|invoke|out|write|measure|import|"
                         r"export|convertto|convertfrom)-[a-z]+\b|\$[a-z_]\w*|\$\(", re.I)
_READY = re.compile(r"listening|ready\b|started\s+server|server\s+(?:is\s+)?running|compiled\s+successfully|local:\s+http|"
                    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(?::\d+)?|application\s+startup\s+complete", re.I)
_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(?::\d+)?[^\s'\"<>)]*", re.I)


def shell(cmd):
    """argv for a command line: PowerShell for PowerShell syntax (sent encoded, so quoting survives), else cmd.exe."""
    if _POWERSHELL.search(cmd):
        # No progress records: redirected, PowerShell would print them as CLIXML noise.
        script = "$ProgressPreference='SilentlyContinue'; " + cmd
        return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()], False
    return cmd, True


async def _spawn(cmd, cwd, extra=None):
    argv, use_shell = shell(cmd)
    env = {**probes.worker_env(), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0", "NO_COLOR": "1", "CI": "1", **(extra or {})}
    kw = dict(cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
              limit=1 << 20, creationflags=NO_WINDOW)
    return await (asyncio.create_subprocess_shell(argv, **kw) if use_shell else asyncio.create_subprocess_exec(*argv, **kw))


def _clean(raw):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw.decode("utf-8", "replace")).rstrip("\r\n")


def kill_tree(proc):
    if proc is None or proc.returncode is not None:
        return
    try:
        parent = psutil.Process(proc.pid)
        for p in parent.children(recursive=True) + [parent]:
            try:
                p.kill()
            except psutil.Error:
                pass
    except psutil.Error:
        pass


async def run(cmd, cwd, on_line=None, timeout=RUN_TIMEOUT_S, keep=None, env=None):
    """Run to completion. Returns {"exit_code", "tail", "lines", "seconds", "timed_out"}; exit_code None = didn't finish. env: added variables."""
    t0 = time.time()
    proc = await _spawn(cmd, cwd, env)
    if keep is not None:
        keep["proc"] = proc  # lets a stop request kill it
    tail, n = deque(maxlen=TAIL_LINES), 0

    async def read():
        nonlocal n
        async for raw in proc.stdout:
            line = _clean(raw)
            n += 1
            tail.append(line)
            if on_line:
                on_line(line)

    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(read(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        kill_tree(proc)
    code = proc.returncode
    if code is not None and code > 0x7FFFFFFF:  # Windows reports negative exit codes as large unsigned numbers
        code -= 1 << 32
    return {"exit_code": None if timed_out else code, "tail": list(tail), "lines": n,
            "seconds": round(time.time() - t0, 1), "timed_out": timed_out}


async def start(cmd, cwd, on_line=None, port=None, ready_s=SERVER_READY_S):
    """Start a long-running command. Waits until it is ready (its port listens, or it prints a ready/URL line), exits,
    or ready_s passes. Returns (proc, {"ready", "url", "exit_code", "tail", "reader"})."""
    proc = await _spawn(cmd, cwd)
    tail, seen = deque(maxlen=TAIL_LINES), {"url": None, "ready": False}

    async def read():
        async for raw in proc.stdout:
            line = _clean(raw)
            tail.append(line)
            if on_line:
                on_line(line)
            m = _URL.search(line)
            if m and not seen["url"]:
                seen["url"] = m.group(0).rstrip(".,;")
            if _READY.search(line):
                seen["ready"] = True

    reader = asyncio.create_task(read())
    end = time.monotonic() + ready_s
    while time.monotonic() < end and proc.returncode is None and not seen["ready"]:
        if port and port_owners(port):
            seen["ready"] = True
        await asyncio.sleep(0.3)
    url = seen["url"] or (f"http://localhost:{port}" if port and seen["ready"] else None)
    return proc, {"ready": seen["ready"] and proc.returncode is None, "url": url, "exit_code": proc.returncode, "tail": tail, "reader": reader}


def port_owners(port):
    """Processes listening on (or holding) a TCP port: [{"pid", "name", "cmdline", "status"}]."""
    out = []
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.Error:
        return out
    for c in sorted(conns, key=lambda c: c.status != "LISTEN"):  # a process's listening socket describes it best
        if c.laddr and c.laddr.port == port and c.status in ("LISTEN", "ESTABLISHED", "NONE") and c.pid:
            if any(o["pid"] == c.pid for o in out):
                continue
            try:
                p = psutil.Process(c.pid)
                name, cmdline = p.name(), " ".join(p.cmdline())[:300]
            except psutil.Error:
                name, cmdline = "?", ""
            out.append({"pid": c.pid, "name": name, "cmdline": cmdline, "status": c.status})
    return out


def _has_pytest():
    import importlib.util
    return importlib.util.find_spec("pytest") is not None


def detect(path, commands=None):
    """{"test": {"command", "source"}, "dev": {"command", "source", "port"}} from what the project itself declares."""
    root = Path(path)
    out = {}
    commands = commands or {}
    for kind in ("test", "dev"):
        if commands.get(kind):
            out[kind] = {"command": commands[kind], "source": "Monday's project settings", "port": commands.get(f"{kind}_port")}
    launch = root / ".claude" / "launch.json"
    if "dev" not in out and launch.exists():
        try:
            cfg = (json.loads(launch.read_text(encoding="utf-8")).get("configurations") or [None])[0]
            if cfg and cfg.get("runtimeExecutable"):
                out["dev"] = {"command": " ".join([cfg["runtimeExecutable"], *cfg.get("runtimeArgs", [])]),
                              "source": ".claude/launch.json", "port": cfg.get("port")}
        except (ValueError, OSError):
            pass
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
        except (ValueError, OSError):
            scripts = {}
        runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        if "test" not in out and scripts.get("test") and "no test specified" not in scripts["test"]:
            out["test"] = {"command": f"{runner} test", "source": "package.json"}
        for s in ("dev", "start", "serve"):
            if "dev" not in out and scripts.get(s):
                port = re.search(r"(?:--port[= ]|-p\s+|PORT=)(\d{2,5})", scripts[s])
                out["dev"] = {"command": f"{runner} run {s}", "source": "package.json", "port": int(port.group(1)) if port else None}
    if "test" not in out:
        py = [root / "pytest.ini", root / "conftest.py", root / "tests", root / "test"]
        pyproject = root / "pyproject.toml"
        asks = (root / "pytest.ini").exists() or (root / "conftest.py").exists() or \
            (pyproject.exists() and "pytest" in pyproject.read_text(encoding="utf-8", errors="ignore"))
        if asks or (any(p.exists() for p in py) and _has_pytest()):
            out["test"] = {"command": "python -m pytest -q", "source": "pytest"}
        elif any(p.exists() for p in py):  # a tests folder, and pytest isn't installed here (2026-10-17, live: "No module named pytest"
            d = "tests" if (root / "tests").exists() else "test"  # read as failing tests): the standard library's own runner
            out["test"] = {"command": f"python -m unittest discover -s {d} -t .", "source": "unittest"}
        elif (root / "Cargo.toml").exists():
            out["test"] = {"command": "cargo test", "source": "Cargo.toml"}
        elif (root / "go.mod").exists():
            out["test"] = {"command": "go test ./...", "source": "go.mod"}
        elif list(root.glob("*.sln")) or list(root.glob("*.csproj")):
            out["test"] = {"command": "dotnet test", "source": ".NET project"}
        elif (root / "pom.xml").exists():
            out["test"] = {"command": "mvn -q test", "source": "pom.xml"}
        elif (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
            out["test"] = {"command": "gradlew test" if (root / "gradlew.bat").exists() else "gradle test", "source": "Gradle"}
    if "dev" not in out and (root / "manage.py").exists():
        out["dev"] = {"command": "python manage.py runserver", "source": "Django manage.py", "port": 8000}
    return out


_ERR = re.compile(r"error|exception|assert|traceback|failed|failure|cannot|not found|undefined|expected", re.I)


def test_summary(tail, exit_code, cmd=""):
    """Deterministic reading of test output by core/evidence.py's rules (2026-10-17, after Dotpals): the runner's own summary beats the
    exit code, and nothing run, a pipe or an unfinished run is unclear rather than passed. {"state": passed | failed | unclear, "ok",
    "passed", "failed", "skipped", "failures": [...], "runner", "counts", "note", "why", "where", "first_error"}; the counts are None when
    no runner's summary was found."""
    from . import evidence
    text = "\n".join(tail)
    v = evidence.verdict({"done": True, "ok": None if exit_code is None else exit_code == 0, "out": text, "cmd": cmd, "stopped": exit_code is None})
    f, ok = v["facts"], v["state"] == "passed"
    i = next((i for i, l in enumerate(tail) if _ERR.search(l) and l.strip()), None)
    first_error = " · ".join(l.strip() for l in tail[i:i + 4] if l.strip()) if i is not None else None  # the line + what it says
    why = None if ok else evidence.reason(text)
    return {"state": v["state"], "ok": ok, "passed": f["passed"] if f["parsed"] else None,
            "failed": f["failed"] + f["errors"] if f["parsed"] else None, "skipped": f["skipped"] if f["parsed"] else None,
            "failures": f["failing"], "runner": f["runner"], "counts": evidence.count_words(f) if f["parsed"] else "",
            "note": v.get("note"), "why": (why or {}).get("why"), "where": (why or {}).get("where"), "first_error": None if ok else first_error}


if __name__ == "__main__":  # python -m core.terminal
    assert shell("git status") == ("git status", True)
    argv, sh = shell("Get-Process | Select-Object -First 3")
    assert not sh and argv[0] == "powershell" and argv[3] == "-EncodedCommand"
    s = test_summary(["tests/test_a.py ..F", "FAILED tests/test_a.py::test_login - AssertionError: 401 != 200",
                      "==== 1 failed, 12 passed in 0.52s ===="], 1)
    assert (s["passed"], s["failed"], s["failures"], s["ok"]) == (12, 1, ["tests/test_a.py::test_login"], False), s
    s = test_summary(["Tests:       2 failed, 8 passed, 10 total"], 1)
    assert (s["passed"], s["failed"]) == (8, 2), s
    s = test_summary(["router self-check: OK 41 routes"], 0)
    assert s["ok"] and (s["passed"], s["failed"]) == (1, 0), s
    s = test_summary(["Traceback (most recent call last):", "AssertionError: ('x', 1)"], 1)
    assert not s["ok"] and s["state"] == "failed" and s["first_error"].startswith("Traceback"), s
    s = test_summary(["Tests: 0 total"], 0)
    assert s["state"] == "unclear" and not s["ok"] and s["note"] == "no tests actually ran", "nothing ran isn't a pass"
    s = test_summary(["all good"], 0, "npm test | tail -3")
    assert s["state"] == "unclear" and s["counts"] == "", "a pipe's exit code is the pipe's"
    s = test_summary(["  ● sum › adds", "    Expected: 3", "    Received: -1", "Tests: 1 failed, 1 total"], 1)
    assert (s["failed"], s["why"]) == (1, "expected 3, got -1"), s
    here = Path(__file__).resolve().parent.parent
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:  # a tests folder: pytest when it's installed here, else the standard library's runner
        (Path(_d) / "tests").mkdir()
        _want = "python -m pytest -q" if _has_pytest() else "python -m unittest discover -s tests -t ."
        assert detect(_d)["test"]["command"] == _want, detect(_d)
        (Path(_d) / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        assert detect(_d)["test"]["command"] == "python -m pytest -q", "a project that asks for pytest gets it"
    d = detect(here, {"test": "python -m core.router"})
    assert d["test"]["command"] == "python -m core.router" and d["dev"]["source"] == ".claude/launch.json" and d["dev"]["port"] == 8765, d

    async def live():
        out = []
        r = await run("python --version", str(here), out.append)
        assert r["exit_code"] == 0 and out and out[0].startswith("Python"), r
        r = await run("Write-Output 'hi from powershell'", str(here))
        assert r["exit_code"] == 0 and r["tail"] == ["hi from powershell"], r
        r = await run('echo "quoted arg"', str(here))
        assert r["tail"] == ['"quoted arg"'], r
    asyncio.run(live())
    print("terminal self-check: OK")
