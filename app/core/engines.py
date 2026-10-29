"""Custom engines (2026-10-13, after the owner's MausBot reference): any agent that speaks ACP (the Agent Client Protocol: JSON-RPC 2.0 over
stdio, what Hermes and OpenClaw's `openclaw acp` speak) becomes a worker Monday hands work to, from one config file, data/engines.json:

    {"openclaw": {"driver": "customAcp", "displayName": "OpenClaw", "config": {"cli": "openclaw acp"}}}

The shape is MausBot's, so an entry written for it works here. Per entry: `driver` customAcp (or acp); `config.cli` the command line;
optional `config.coding` (false: it is given no code work and needs no project), `config.mcp` (true: it gets Monday's desktop-control MCP
server, as Hermes does). Discovery (core/agents.py) says whether the command is really here; the worker (core/workers.py AcpEngine) drives
it exactly as Hermes; "ask OpenClaw to …", "OpenClaw, …" and "… with OpenClaw" address it by its name (address()). Nothing here signs in
for an engine or bypasses its approvals: ACP's session/request_permission reaches the owner as it does for Hermes.
Self-check: python -m core.engines
"""
import json
import re
import shlex
import shutil
from pathlib import Path

from . import agents, router

FILE = "engines.json"
DRIVERS = ("customacp", "acp")
BUILTIN = {"openclaw": ("OpenClaw", "openclaw acp")}  # engines whose ACP command Monday knows: listed when installed, no entry needed


def path_for(data_dir):
    return Path(data_dir) / FILE


def argv_of(cli):
    """'openclaw acp' -> the real program and its arguments ([node, script, 'acp'] for an npm shim), or None when the program isn't here."""
    try:
        words = shlex.split(cli, posix=False) if cli else []
    except ValueError:
        return None
    if not words:
        return None
    head = words[0].strip('"')
    exe = agents.npm_entry(head) or ([shutil.which(head)] if shutil.which(head) else None) or ([head] if Path(head).is_file() else None)
    return (list(exe) + [w.strip('"') for w in words[1:]]) if exe else None


def load(data_dir, builtin=True):
    """({key: {"key", "name", "cli", "argv" (None when not installed), "coding", "mcp", "source"}}, errors) from data/engines.json, plus
    the BUILTIN engines that are installed."""
    out, errors, p = {}, [], path_for(data_dir)
    raw = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) or {}
        except ValueError as e:
            errors.append(f"{p.name}: {e}")
    for key, ent in (raw.items() if isinstance(raw, dict) else []):
        k = re.sub(r"[^a-z0-9_]+", "_", str(key).lower()).strip("_")
        if not k or not isinstance(ent, dict):
            continue
        if str(ent.get("driver", "")).lower() not in DRIVERS:
            errors.append(f"{key}: driver {ent.get('driver')!r} isn't supported (customAcp)")
            continue
        cfg = ent.get("config") or {}
        cli = str(cfg.get("cli") or "").strip()
        if not cli:
            errors.append(f"{key}: config.cli is missing")
            continue
        out[k] = {"key": k, "name": str(ent.get("displayName") or key).strip()[:40], "cli": cli, "argv": argv_of(cli),
                  "coding": cfg.get("coding", True) is not False, "mcp": bool(cfg.get("mcp")), "source": p.name}
    if builtin:
        for k, (name, cli) in BUILTIN.items():
            if k not in out:
                argv = argv_of(cli)
                if argv:
                    out[k] = {"key": k, "name": name, "cli": cli, "argv": argv, "coding": True, "mcp": False, "source": "built in"}
    return out, errors


def save(data_dir, key, name, cli, coding=True, mcp=False):
    """Add or replace one entry in data/engines.json (MausBot's shape; other entries untouched). -> the key."""
    k = re.sub(r"[^a-z0-9_]+", "_", str(key or name).lower()).strip("_")
    if not k or k in router.AGENT_NAMES and k not in (load(data_dir)[0]):
        raise ValueError("Pick another key: that one is a built-in agent's.")
    if not str(cli or "").strip():
        raise ValueError("The engine's command line can't be empty.")
    p = path_for(data_dir)
    raw = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) or {}
        except ValueError:
            raw = {}
    raw[k] = {"driver": "customAcp", "displayName": str(name or key).strip()[:40] or k,
              "config": {"cli": str(cli).strip(), **({"coding": False} if not coding else {}), **({"mcp": True} if mcp else {})}}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return k


def remove(data_dir, key):
    p = path_for(data_dir)
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if key not in raw:
        raise KeyError(f"No engine called {key} in {p.name}.")
    raw.pop(key)
    p.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register(engines):
    """Every engine as an agent the router names and the coding-agent choice may pick (router.AGENT_NAMES, router.CODING_AGENTS, agents.CUSTOM)."""
    for k in list(agents.CUSTOM):  # an entry that went away is no agent any more
        if k not in engines:
            router.AGENT_NAMES.pop(k, None)
            if k in router.CODING_AGENTS:
                router.CODING_AGENTS.remove(k)
    for k, e in engines.items():
        router.AGENT_NAMES[k] = e["name"]
        if e["coding"] and k not in router.CODING_AGENTS:
            router.CODING_AGENTS.append(k)
    agents.CUSTOM = dict(engines)


def address(text, engines):
    """'ask OpenClaw to fix the tests' / 'OpenClaw, fix the tests' / 'fix the tests with OpenClaw' -> (key, 'fix the tests'), else None."""
    t = (text or "").strip()
    for k, e in engines.items():
        n = re.escape(e["name"])
        m = (re.match(rf"^(?:please\s+)?(?:ask|tell|have|get|use|let)\s+{n}\s+(?:to\s+)?(?P<r>.+)$", t, re.I)
             or re.match(rf"^{n}\s*[,:]\s*(?P<r>.+)$", t, re.I)
             or re.match(rf"^(?P<r>.+?)\s+(?:with|using|via|through)\s+{n}\s*[.!]?$", t, re.I))
        if m:
            return k, m["r"].strip(" .!")
    return None


if __name__ == "__main__":  # python -m core.engines
    import sys
    import tempfile
    assert argv_of("python -c pass")[1:] == ["-c", "pass"] and argv_of("python -c pass")[0], "a program on PATH, with its arguments"
    assert argv_of("no-such-program-xyz acp") is None and argv_of("") is None
    with tempfile.TemporaryDirectory() as d:
        eng, errs = load(d, builtin=False)
        assert eng == {} and errs == []
        path_for(d).write_text(json.dumps({"openclaw": {"driver": "customAcp", "displayName": "OpenClaw", "config": {"cli": "openclaw acp"}},
                                           "py": {"driver": "customAcp", "displayName": "Py Agent", "config": {"cli": "python -c pass", "coding": False}},
                                           "bad": {"driver": "python", "config": {"cli": "x"}}, "none": {"driver": "acp", "config": {}}}), encoding="utf-8")
        eng, errs = load(d, builtin=False)
        assert set(eng) == {"openclaw", "py"} and len(errs) == 2, (eng, errs)
        assert eng["py"]["argv"] and not eng["py"]["coding"] and eng["openclaw"]["name"] == "OpenClaw", eng
        assert eng["openclaw"]["argv"] is None or eng["openclaw"]["argv"][-1] == "acp", "installed or not, the shape holds"
        save(d, "my_bot", "My Bot", "python -c pass", coding=True, mcp=True)
        eng, _ = load(d, builtin=False)
        assert eng["my_bot"]["mcp"] and eng["my_bot"]["coding"] and json.loads(path_for(d).read_text())["my_bot"]["driver"] == "customAcp"
        remove(d, "my_bot")
        assert "my_bot" not in load(d, builtin=False)[0]
        try:
            save(d, "hermes", "Hermes", "x")
            raise AssertionError("a built-in agent's key must be refused")
        except ValueError:
            pass
        before = list(router.CODING_AGENTS)
        register(eng)
        assert router.AGENT_NAMES["py"] == "Py Agent" and "openclaw" in router.CODING_AGENTS and "py" not in router.CODING_AGENTS, "coding: false stays out of code work"
        assert address("ask Py Agent to tidy the desk", eng) == ("py", "tidy the desk") and address("OpenClaw, fix the tests.", eng) == ("openclaw", "fix the tests")
        assert address("fix the tests with OpenClaw", eng) == ("openclaw", "fix the tests") and address("open Notepad", eng) is None
        register({})
        assert list(router.CODING_AGENTS) == before and "py" not in router.AGENT_NAMES, "gone from the config: gone as an agent"
    print("engines self-check: OK", f"(built in: {', '.join(BUILTIN)})")
    sys.exit(0)
