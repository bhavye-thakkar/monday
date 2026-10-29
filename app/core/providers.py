"""Chat AIs Monday can switch to besides Ollama: "Monday, use Claude / ChatGPT / Gemini / Grok / DeepSeek".

Two kinds, one interface (pick_model, stream):
- API providers need their own key in Monday/.env (or the environment). Keys are read server-side only, never exported
  to worker processes, never logged or returned. Models come from each provider's /models list (newest matching one,
  or <PROVIDER>_MODEL to pin), so names don't go stale.
- CLI providers (style "cli"): the owner's own installed CLI, signed in the way the owner set it up (Gemini CLI: their
  Google sign-in; Grok's CLI: their grok.com account). Monday runs the CLI; it never reads the CLI's credentials. Chat
  runs with no file, command or web tools, in an empty folder, so an answer can't see or change the owner's files;
  research may use the CLI's web search and nothing else.
Chat always has Ollama behind it as the fallback.
"""
import asyncio
import json
import re
import shutil
import time
import uuid
from pathlib import Path

import httpx

from .jev import read_env_file, setting
from .probes import tls

CLI_DIR = Path(__file__).resolve().parent.parent / "data" / "cli-chat"  # the CLIs' workspace for chat: kept empty
# Gemini CLI project settings there (its documented general.maxAttempts): it retries a 503/429 up to 10 times, logging
# only in debug mode, so an outage took 30 s+ to surface. Two attempts: one retry, then Monday says so and falls back.
GEMINI_SETTINGS = json.dumps({"general": {"maxAttempts": 2}})
CLI_NOISE = re.compile(r"true color|ripgrep|skill conflict|deprecat|loaded cached credentials|^\s*$", re.I)
CLI_TRACE = re.compile(r"^\s*at\s+\S|^\s*[{}\[\]],?\s*$|^\s+\w+:\s*\S+,?\s*$|node:internal|^Node\.js v", re.I)  # a stack frame or a dumped object's line: never the reason
CLI_ERR_KEEP = 200_000  # characters of what a CLI printed that are read for its reason (the last 2000 once held only stack frames)

PROVIDERS = {
    "anthropic": {"label": "Claude", "key": "ANTHROPIC_API_KEY", "base": "https://api.anthropic.com/v1", "style": "anthropic",
                  "pick": r"^claude-", "prefer": "sonnet"},
    "openai": {"label": "ChatGPT", "key": "OPENAI_API_KEY", "base": "https://api.openai.com/v1", "style": "openai",
               "pick": r"^(gpt-\d|o\d)"},
    # The owner's Grok CLI (xAI's `grok`, also the Grok Build coding agent) and its grok.com sign-in: no API key. Its
    # models come from `grok models`. Every answer uses the owner's Grok plan ("Teal." cost $0.018 on 2026-09-30). An
    # empty --tools list leaves all 25 tools on, so chat keeps one harmless tool (its to-do list): no files, no
    # commands, no web. Not a research model: with its tools limited it couldn't search ("couldn't reach a live
    # source"), and its search sits behind a generic use_tool that could also start paid image generation.
    "xai": {"label": "Grok", "style": "cli", "dialect": "grok", "exe": "grok", "login": "run `grok login` in a terminal once",
            "chat": ["--tools", "todo_write", "--disable-web-search"], "install": "install xAI's Grok CLI and sign in (`grok login`)"},
    # The owner's Gemini CLI and its Google sign-in (no API key). It doesn't list its models: these are the aliases it
    # accepts; the reply's stats name the model that actually answered. Measured 2026-09-28: flash 7 s; pro refused
    # on the free tier (quota limit 0).
    "gemini": {"label": "Gemini", "style": "cli", "dialect": "gemini", "npm": "gemini", "models": ("flash", "flash-lite", "pro", "auto"),
               "prefer": "flash", "login": "run `gemini` in a terminal once and sign in with Google",
               "chat": ["--approval-mode", "plan"], "install": "npm install -g @google/gemini-cli",
               # research: its Google Search and page fetch run without asking (nothing else does: plan mode can't
               # approve a search headless, it waits forever). Measured 2026-09-30: google_web_search ran this way.
               "research": ["--approval-mode", "default", "--allowed-tools", "google_web_search,web_fetch"]},
    "deepseek": {"label": "DeepSeek", "key": "DEEPSEEK_API_KEY", "base": "https://api.deepseek.com", "style": "openai",
                 "pick": r"^deepseek-"},
    # Mistral's own API (a free "Experiment" key from console.mistral.ai works), else its models through the owner's OpenRouter key:
    # via() picks the way (the owner, 2026-10-10: "add Mistral"). Its -latest names always exist, so nothing goes stale.
    "mistral": {"label": "Mistral", "key": "MISTRAL_API_KEY", "base": "https://api.mistral.ai/v1", "style": "openai",
                "pick": r"^mistral-(?:large|medium|small)-latest$", "prefer": "large", "via": ("openrouter", "mistralai/mistral-")},
    # A free-tier OpenRouter key (no credits) reaches only the ":free" models: openrouter_free() asks OpenRouter once per key, and
    # choose() then keeps to those (checked 2026-10-10: the owner's key is free-tier; 15 free models, none of them Mistral's).
    "openrouter": {"label": "OpenRouter", "key": "OPENROUTER_API_KEY", "base": "https://openrouter.ai/api/v1", "style": "openai",
                   "pick": r"."},
}
ALIASES = {  # what the owner says -> (provider, model preference)
    "claude": ("anthropic", None), "anthropic": ("anthropic", None), "sonnet": ("anthropic", "sonnet"),
    "opus": ("anthropic", "opus"), "haiku": ("anthropic", "haiku"),
    "chatgpt": ("openai", None), "gpt": ("openai", None), "openai": ("openai", None),
    "grok": ("xai", None), "x": ("xai", None), "xai": ("xai", None), "x ai": ("xai", None),
    "gemini": ("gemini", None), "google": ("gemini", None), "gemini pro": ("gemini", "pro"), "gemini flash": ("gemini", "flash"),
    "deepseek": ("deepseek", None), "openrouter": ("openrouter", None),
    "mistral": ("mistral", None), "mistral ai": ("mistral", None), "le chat": ("mistral", None),
}
_NOT_CHAT = re.compile(r"embed|audio|tts|whisper|image|dall-e|vision|realtime|search|transcribe|moderation|computer-use|"
                       r"instruct|codex|preview-tts|live|veo|imagen|aqa", re.I)
# OpenRouter names its chat models "...-instruct", "...-it": only what can't chat is skipped there, and its ":batch" doubles.
_NOT_CHAT_OR = re.compile(r"embed|audio|tts|whisper|image-|transcribe|moderation|safety|guard|:batch", re.I)
_SMALL = re.compile(r"mini|nano|lite|small|tiny|lightning|preview|note|omni|code|coder", re.I)  # on a free key, the bigger general models first
OPENROUTER_FREE = {}  # per OpenRouter key: whether it is free-tier (asked once)
LAST_OK = {}  # provider -> when it last answered (stream), "ollama" -> when it was last seen online (registry): the registry's last_ok_at
NOT_PROVEN = "key saved, not proven yet (Monday finds out on first use)"  # a key present is never "authenticated" (registry)


def parse(chat_model):
    """'anthropic/claude-sonnet-5' -> ('anthropic', 'claude-sonnet-5'); anything else (Ollama names, 'auto') -> None."""
    prov, _, model = (chat_model or "").partition("/")
    return (prov, model) if prov in PROVIDERS and model else None


def key_for(provider):
    c = PROVIDERS[provider]
    return setting(c["key"]) if "key" in c else None


def cli_entry(provider):
    """How to start a CLI provider: a global npm package as [node, script], or an installed executable."""
    c = PROVIDERS[provider]
    if "npm" in c:
        from .agents import npm_entry
        return npm_entry(c["npm"])
    exe = shutil.which(c["exe"])
    return [exe] if exe else None


def ready(provider):
    """(True, None) when Monday can ask this AI now, else (False, what the owner can do about it)."""
    c = PROVIDERS[provider]
    if c["style"] == "cli":
        return (True, None) if cli_entry(provider) else (False, f"the {c['label']} CLI isn't installed ({c['install']})")
    return (True, None) if key_for(provider) else (False, f"add {c['key']}=<your key> to Monday\\.env on this computer "
                                                          "(it stays there, never in chat or on the screens)")


def configured():
    """{provider: True/False}: a key present (never the key) or the CLI installed."""
    vals = read_env_file()
    return {p: bool(cli_entry(p)) if c["style"] == "cli" else bool(setting(c["key"], vals)) for p, c in PROVIDERS.items()}


def via(provider, prefer=None):
    """(provider, prefer) that reaches `provider`: itself when it is set up, else the gateway it is reachable through (Mistral's models
    through the owner's OpenRouter key), else itself, whose ready() says what to set up."""
    gw = PROVIDERS[provider].get("via")
    if gw and not ready(provider)[0] and ready(gw[0])[0]:
        return gw
    return provider, prefer


async def openrouter_free():
    """Whether the OpenRouter key is free-tier (no credits: only OpenRouter's ":free" models answer it). Asked once per key."""
    key = key_for("openrouter")
    if key not in OPENROUTER_FREE:
        async with httpx.AsyncClient(timeout=15, verify=tls()) as h:
            r = await h.get("https://openrouter.ai/api/v1/key", headers=_headers("openrouter", key))
        OPENROUTER_FREE[key] = r.status_code == 200 and bool((r.json().get("data") or {}).get("is_free_tier"))
    return OPENROUTER_FREE[key]


def choose(provider, items, prefer=None, free=False):
    """The model to use from a provider's /models list. Chat models matching its pattern; `prefer` when any match it (a vendor's
    "mistralai/..." must match: never another company's model in its place); a free-tier OpenRouter key keeps to ":free" models, the
    bigger general ones first; then the newest."""
    c = PROVIDERS[provider]
    skip = _NOT_CHAT_OR if provider == "openrouter" else _NOT_CHAT
    items = [m for m in items if re.search(c["pick"], m.get("id", "")) and not skip.search(m["id"])]
    if free:  # its free models: ":free" ones, or any it prices at nothing (2026-10-15 it listed only "openrouter/free", a router to them)
        items = [m for m in items if m["id"].endswith(":free") or _zero(m.get("pricing"))]
    prefer = prefer or c.get("prefer")
    if prefer and any(prefer in m["id"] for m in items):
        items = [m for m in items if prefer in m["id"]]
    elif prefer and "/" in prefer:
        vendor = prefer.split("/")[0]
        raise RuntimeError(f"{c['label']} has no {'free ' if free else ''}model from {vendor} right now"
                           + (" (this key is free-tier: only OpenRouter's free models answer it; add credits, or the vendor's own key)" if free else ""))
    if not items:
        raise RuntimeError(f"{c['label']} lists no chat model I can use" + (" with a free-tier key" if free else "") + ".")
    if free:
        size = lambda m: max([float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b\b", m["id"])] or [0])  # noqa: E731
        return max(items, key=lambda m: (not _SMALL.search(m["id"].split("/")[-1]), size(m), m.get("created") or 0))
    return max(items, key=lambda m: (m.get("created") or 0, str(m.get("created_at") or ""),
                                     [int(n) for n in re.findall(r"\d+", m["id"])[:3]], m["id"]))


def _zero(pricing):
    """A model's /models pricing that asks nothing for a prompt or an answer."""
    return isinstance(pricing, dict) and all(str(pricing.get(k, "x")).strip() in ("0", "0.0") for k in ("prompt", "completion"))


def _headers(provider, key):
    if PROVIDERS[provider]["style"] == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


async def pick_model(provider, prefer=None):
    """<PROVIDER>_MODEL if pinned, else the newest chat model from the provider's /models list."""
    c = PROVIDERS[provider]
    pinned = setting(f"{provider.upper()}_MODEL")
    if pinned:
        return pinned
    if c["style"] == "cli":  # the CLI's own list (Grok: `grok models`, its default first), or the aliases it accepts
        models = await cli_models(provider)
        return prefer if prefer in models else c["prefer"] if c.get("prefer") in models else models[0]
    async with httpx.AsyncClient(timeout=15, verify=tls()) as h:
        r = await h.get(f"{c['base']}/models", headers=_headers(provider, key_for(provider)))
    if r.status_code != 200:
        raise RuntimeError(f"{c['label']} rejected the key or the request (HTTP {r.status_code}).")
    free = provider == "openrouter" and await openrouter_free()
    return choose(provider, r.json().get("data") or [], prefer, free)["id"].removeprefix("models/")


def parse_grok_models(text):
    """`grok models` -> its model names, the default first; RuntimeError when it isn't signed in."""
    if re.search(r"not authenticated|not (?:signed|logged) in", text, re.I):
        raise RuntimeError(f"Grok isn't signed in: {PROVIDERS['xai']['login']}.")
    names = re.findall(r"^\s*\*\s*([\w.:-]+)", text, re.M)
    default = re.search(r"default model:\s*([\w.:-]+)", text, re.I)
    names = ([default.group(1)] if default else []) + [n for n in names if not default or n != default.group(1)]
    if not names:
        raise RuntimeError(f"Grok's CLI listed no models ({text.strip()[:120]}).")
    return names


async def cli_models(provider):
    c = PROVIDERS[provider]
    if c["dialect"] == "gemini":
        return list(c["models"])
    from .probes import run as run_cmd
    code, out, err = await run_cmd(*cli_entry(provider), "models", timeout=30)
    return parse_grok_models(f"{out or ''}{err or ''}")


def registry(ollama=None):
    """Every chat AI Monday knows, in the owner's five words (tools.STATUSES) with the reason: Ollama (Monday's cached
    health), the providers above, and FreeLLMAPI (no endpoint configured). The coding agents are core/agents.py's.
    Since 2026-10-27 each row is also an entry of the authentication registry, in core/agents.py auth_fields' words: context (a key in
    Monday\\.env, the owner's CLI and its own sign-in, the local Ollama server, a cloud endpoint: separate, never equated), auth_status
    (a key or a CLI present is "unknown" until an answer proves it, never "authenticated"), required, checked_at, last_ok_at (LAST_OK),
    last_error, capabilities, remediation. Never a key's value: names and words only."""
    from .tools import AVAILABLE, UNAVAILABLE
    now = time.time()

    def row(key, name, ok, detail, context, auth_status, error=None, fix=None, required=False, **extra):
        return {"key": key, "name": name, "status": AVAILABLE if ok else UNAVAILABLE, "detail": detail, "context": context,
                "auth_status": auth_status, "required": required, "checked_at": now, "last_ok_at": LAST_OK.get(key), "last_error": error,
                "capabilities": "chat", "remediation": fix, **extra}
    rows = []
    if ollama is not None:
        on = ollama.get("status") == "online"
        if on:
            LAST_OK["ollama"] = now
        rows.append(row("ollama", "Ollama", on, f"{len(ollama.get('models') or [])} models; cloud first, local as fallback" if on else "its server isn't answering",
                        "local_runtime", "authenticated" if on else "connection_unavailable", None if on else "offline",
                        None if on else "Monday starts Ollama itself; if it stays down, see %LOCALAPPDATA%\\Ollama\\monday_serve.log", required=True))
    for p, c in PROVIDERS.items():
        ok, why = ready(p)
        cli = c["style"] == "cli"
        how = {"gemini": "your Gemini CLI and its Google sign-in (free tier)",
               "grok": "your Grok CLI and its grok.com sign-in (each answer uses your Grok plan)"}.get(
            c.get("dialect"), "key set in Monday\\.env; proven on first use")
        if p == "openrouter" and ok and OPENROUTER_FREE.get(key_for(p)):
            how = "key set in Monday\\.env; free tier, so only OpenRouter's free models answer it"
        gw = c.get("via")
        if not ok and gw and ready(gw[0])[0]:  # reachable through a gateway: `use Mistral` tries it and says if it can't
            why += f"; or through {PROVIDERS[gw[0]]['label']} with your key there, when it has a {c['label']} model that key may use"
        rows.append(row(p, c["label"], ok, how if ok else why, "cli" if cli else "api_key", "unknown" if ok else "configuration_missing",
                        None if ok else "not_installed" if cli else "key_missing",
                        (c["login"] if cli else NOT_PROVEN) if ok else (c["install"] if cli else why),  # a CLI's own sign-in word
                        key_env=c.get("key")))  # the .env name a pasted key goes to (Connections); None for a CLI's own sign-in
    rows.append(row("freellmapi", "FreeLLMAPI", False, "no FreeLLMAPI endpoint configured", "cloud", "configuration_missing", "not_configured",
                    "no FreeLLMAPI endpoint configured"))
    return rows


def transcript(messages):
    """Ollama-style messages as one prompt for a CLI: the system text, then the conversation, the owner's turn last."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [f"{'Owner' if m['role'] == 'user' else 'Monday'}: {m['content']}" for m in messages if m["role"] in ("user", "assistant")]
    return f"{system}\n\nThe conversation so far (the owner's latest message is last):\n\n" + "\n\n".join(turns)


def cli_error(label, stderr, failure=None):
    """A CLI's failure in plain words: a quota (with its limit and retry time), a sign-in, else its last real line."""
    text = f"{failure or ''}\n{stderr}"
    if re.search(r"\b503\b|overloaded|service unavailable", text, re.I) and not re.search(r"\b429\b|quota", text, re.I):
        return f"{label}'s service is overloaded right now (HTTP 503); try again in a minute."
    if re.search(r"\b429\b|quota|resource[_\s]exhausted|rate[\s-]?limit", text, re.I):
        lim, mdl = re.search(r"limit:\s*(\d+)", text), re.search(r"model:\s*([\w.-]+)", text)
        wait = re.search(r"retry in\s*([\d.]+)\s*s", text, re.I)
        what = f" for {mdl.group(1)}" if mdl else ""
        if re.search(r"daily quota|per\s*day", text, re.I):  # the day's allowance is gone: a retry in seconds won't help
            return f"{label}'s daily free quota{what} is used up" + (f" ({lim.group(1)} requests a day)" if lim else "") + "; it resets tomorrow."
        return (f"{label} refused it: quota reached{what}" + (f" (limit {lim.group(1)})" if lim else "")
                + (f"; retry in {float(wait.group(1)):.0f}s" if wait else "") + ".")
    if re.search(r"set an auth method", text, re.I):  # signed in, but no way of signing in chosen (2026-10-15: oauth_creds.json, no settings.json)
        return f"{label}'s CLI has no sign-in method chosen: run `gemini` in a terminal once and choose “Login with Google”."
    if re.search(r"api[_\s]?key|not\s+(?:signed|logged)\s+in|login|authenticat|oauth|credential", text, re.I):
        login = next((c["login"] for c in PROVIDERS.values() if c["label"] == label and c.get("login")), "sign in with its CLI")
        return f"{label} isn't signed in: {login}."
    lines = [ln.strip() for ln in text.splitlines() if not CLI_NOISE.search(ln) and not CLI_TRACE.search(ln)]
    said = next((ln for ln in reversed(lines) if re.search(r"error|failed|cannot|unable|denied|invalid|not found", ln, re.I)), lines[-1] if lines else "")
    return f"{label}: {(said or 'the CLI failed without saying why')[:200]}"


REPLY_AS = "Reply to the owner's latest message as Monday."


def cli_argv(provider, entry, model, research, prompt_file=None):
    """The command line for one headless answer. Gemini reads the conversation on stdin; Grok from a prompt file."""
    c = PROVIDERS[provider]
    mode = c["research"] if research and c.get("research") else c["chat"]
    if c["dialect"] == "grok":
        return [*entry, "--prompt-file", str(prompt_file), "--output-format", "streaming-json", "-m", model, *mode, "--cwd", str(CLI_DIR)]
    return [*entry, "-o", "stream-json", *mode, "-m", model, "-p", REPLY_AS]


def cli_event(dialect, ev, info=None):
    """One JSON line of a CLI's stream -> (a piece of the answer or None, a failure or None). Fills `info` (model,
    usage, cost). Formats captured from gemini 0.61 (2026-09-28) and grok 1.0.44 (2026-09-30)."""
    t, info = ev.get("type"), info if info is not None else {}
    if dialect == "grok":
        if t == "text":
            return ev.get("data") or None, None
        if t == "tool_call":  # what it said before a step and after it: separate lines, not "…sentence.I couldn't…"
            return "\n", None
        if t == "usage":
            u = ev.get("usage") or {}
            info["usage"] = {k: u.get(k) for k in ("input_tokens", "output_tokens") if u.get(k) is not None}
        elif t == "end":
            if ev.get("total_cost_usd") is not None:
                info.setdefault("usage", {})["cost_usd"] = ev["total_cost_usd"]
            if ev.get("stopReason") not in (None, "end_turn"):
                return None, f"Grok stopped: {ev.get('stopReason')}"
        elif t == "error":
            return None, str(ev.get("message") or ev.get("data") or ev.get("error") or "the CLI reported an error")
        return None, None
    if t == "init":
        info["model"] = ev.get("model")
    elif t == "message" and ev.get("role") == "assistant" and ev.get("content"):
        return ev["content"], None
    elif t == "error" and ev.get("severity") == "error":
        return None, ev.get("message")
    elif t == "result":
        info["usage"] = {k: v for k, v in (ev.get("stats") or {}).items() if k in ("input_tokens", "output_tokens", "total_tokens", "duration_ms")}
        if ev.get("status") not in (None, "success"):
            e = ev.get("error")
            return None, e.get("message") if isinstance(e, dict) else str(e or "the CLI reported an error")
    return None, None


async def _cli_stream(provider, model, messages, timeout, info, research=False):
    """The owner's CLI, headless (cli_argv): in an empty folder, with Monday's worker environment; chat without file,
    command or web tools, research with its web search. Yields the answer as it arrives; fills `info` with the model
    and usage from the CLI's own stats."""
    c, entry = PROVIDERS[provider], cli_entry(provider)
    if not entry:
        raise RuntimeError(ready(provider)[1])
    CLI_DIR.mkdir(parents=True, exist_ok=True)
    if c["dialect"] == "gemini":
        cfg = CLI_DIR / ".gemini" / "settings.json"
        if not cfg.exists() or cfg.read_text(encoding="utf-8") != GEMINI_SETTINGS:
            cfg.parent.mkdir(exist_ok=True)
            cfg.write_text(GEMINI_SETTINGS, encoding="utf-8")
    prompt, pf = transcript(messages), None
    if c["dialect"] == "grok":  # its prompt comes from a file (a long conversation won't fit a command line)
        pf = CLI_DIR / f"prompt-{uuid.uuid4().hex}.txt"
        pf.write_text(f"{prompt}\n\n{REPLY_AS}", encoding="utf-8")
    try:
        async for piece in _cli_run(provider, cli_argv(provider, entry, model, research, pf), None if pf else prompt, timeout, info):
            yield piece
    finally:
        if pf:
            pf.unlink(missing_ok=True)


async def _cli_run(provider, argv, stdin_text, timeout, info):
    from .probes import worker_env
    from .workers import kill_tree
    c = PROVIDERS[provider]
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(CLI_DIR), env=worker_env(), stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 24)  # a web page read can be one long line
    if stdin_text is not None:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    err, failure, deadline = [], None, time.monotonic() + timeout

    async def drain():
        async for line in proc.stderr:
            err.append(line.decode("utf-8", "replace"))
            if re.search(r"\b429\b|\b503\b|quota|resource[_\s]exhausted", err[-1], re.I):
                kill_tree(proc)  # the CLI would retry for a minute: say "quota"/"overloaded" now (chat then falls back)
    reader = asyncio.create_task(drain())
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), max(0.1, deadline - time.monotonic()))
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            try:
                ev = json.loads(line[line.index("{"):]) if "{" in line else None
            except ValueError:
                ev = None
            if not isinstance(ev, dict):
                continue
            piece, bad = cli_event(c["dialect"], ev, info)
            if piece:
                yield piece
            failure = bad or failure
    except asyncio.TimeoutError:
        kill_tree(proc)
        raise RuntimeError(f"{c['label']} didn't answer within {timeout}s.")
    code = await proc.wait()
    await asyncio.wait_for(reader, 5)
    if failure or code:
        raise RuntimeError(cli_error(c["label"], "".join(err)[-CLI_ERR_KEEP:], failure))


async def stream(provider, model, messages, timeout=180, info=None, research=False):
    """Yield text pieces of one streamed reply. `messages` are Ollama-style dicts (system/user/assistant). `info`, when
    given, receives what the provider reports about the reply (a CLI: its model, token usage and cost). `research`: a
    CLI with web search may use it (Gemini, Grok)."""
    c, key = PROVIDERS[provider], key_for(provider)
    if c["style"] == "cli":
        async for piece in _cli_stream(provider, model, messages, timeout, info, research):
            yield piece
        LAST_OK[provider] = time.time()  # (it answered: the registry's last_ok_at)
        return
    if not key:
        raise RuntimeError(f"no {c['key']} configured")
    if c["style"] == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {"model": model, "max_tokens": 2048, "stream": True, "system": system,
                "messages": [m for m in messages if m["role"] in ("user", "assistant")]}
        url = f"{c['base']}/messages"
    else:
        body = {"model": model, "stream": True, "messages": messages}
        url = f"{c['base']}/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10), verify=tls()) as h:
        async with h.stream("POST", url, json=body, headers={**_headers(provider, key), "Content-Type": "application/json"}) as r:
            if r.status_code != 200:
                detail = (await r.aread())[:200].decode(errors="replace")
                raise RuntimeError(f"{c['label']} HTTP {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    j = json.loads(data)
                except ValueError:
                    continue
                if j.get("type") == "error" or j.get("error"):
                    raise RuntimeError(f"{c['label']}: {(j.get('error') or {}).get('message') or j}")
                piece = ((j.get("delta") or {}).get("text") if c["style"] == "anthropic"
                         else ((j.get("choices") or [{}])[0].get("delta") or {}).get("content"))
                if piece:
                    yield piece
    LAST_OK[provider] = time.time()  # (it answered: the key is proven; the registry's last_ok_at)


if __name__ == "__main__":  # python -m core.providers  -> offline self-check
    assert parse("anthropic/claude-sonnet-5") == ("anthropic", "claude-sonnet-5")
    assert parse("openrouter/x-ai/grok-4") == ("openrouter", "x-ai/grok-4")
    assert parse("gpt-oss:120b-cloud") is None and parse("auto") is None
    assert ALIASES["x"] == ("xai", None) and ALIASES["claude"][0] == "anthropic"
    assert not _NOT_CHAT.search("claude-sonnet-5") and _NOT_CHAT.search("text-embedding-3-large")
    # Gemini is the owner's CLI and its Google sign-in: no key, read-only, the conversation as one prompt
    assert PROVIDERS["gemini"]["style"] == "cli" and key_for("gemini") is None and ALIASES["gemini pro"] == ("gemini", "pro")
    assert asyncio.run(pick_model("gemini")) == "flash" and asyncio.run(pick_model("gemini", "pro")) == "pro"
    t = transcript([{"role": "system", "content": "You are Monday."}, {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Hello."}, {"role": "user", "content": "capital of France?"}])
    assert t.startswith("You are Monday.") and t.endswith("Owner: capital of France?") and "Monday: Hello." in t
    quota = ('Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 0, '
             'model: gemini-3.1-pro\nPlease retry in 7.299322488s.", "code": 429')
    assert cli_error("Gemini", quota) == "Gemini refused it: quota reached for gemini-3.1-pro (limit 0); retry in 7s.", cli_error("Gemini", quota)
    daily = "[API Error: You have exhausted your daily quota on this model.] limit: 20, model: gemini-3.8-flash\nPlease retry in 15.4s. 429"
    assert cli_error("Gemini", daily) == "Gemini's daily free quota for gemini-3.8-flash is used up (20 requests a day); it resets tomorrow."
    assert "isn't signed in" in cli_error("Gemini", "Error: GEMINI_API_KEY environment variable not found")
    unset = "Please set an Auth method in your C:\\Users\\x\\.gemini\\settings.json or specify one of the following environment variables before running: GEMINI_API_KEY"
    assert "no sign-in method chosen" in cli_error("Gemini", unset), "signed in but no method chosen is said as that, not as signed out"
    assert choose("openrouter", [{"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
                                 {"id": "openai/gpt-6", "pricing": {"prompt": "0.000002", "completion": "0.00001"}}], free=True)["id"] == "openrouter/free", \
        "a free-tier key: a model priced at nothing counts as free (2026-10-15: no ':free' ids were listed)"
    assert cli_error("Gemini", "Warning: True color (24-bit) support not detected.\nSomething broke") == "Gemini: Something broke"
    assert cli_error("Gemini", "at async GeminiChat.streamWithRetries {\n  status: 503\n}") == \
        "Gemini's service is overloaded right now (HTTP 503); try again in a minute."
    trace = "\n".join(["    at process.processTicksAndRejections (node:internal/process/task_queues:105:5)"] * 40)
    assert cli_error("Gemini", 'Attempt 1 failed with status 503. Retrying with backoff... _ApiError: {"error":{"code":503}}\n' + trace) == \
        "Gemini's service is overloaded right now (HTTP 503); try again in a minute.", "the reason is above the stack trace, however long that is"
    assert cli_error("Gemini", "Error: the model returned no candidates\n" + trace + "\n  code: 'ERR_X',\n}\nNode.js v22.17.0") == \
        "Gemini: Error: the model returned no candidates", "never a stack frame, a dumped object or Node's version line"
    assert cli_error("Gemini", trace) == "Gemini: the CLI failed without saying why"
    assert cli_error("Gemini", "Error: ECONNRESET\n" + trace) == "Gemini: Error: ECONNRESET", "a one-word reason is still the reason"
    assert "google_web_search" in " ".join(PROVIDERS["gemini"]["research"]), "research may search the web, and only that"
    # Grok: the owner's grok CLI and its grok.com sign-in; events and model list as the real CLI printed them
    assert PROVIDERS["xai"]["style"] == "cli" and key_for("xai") is None and ALIASES["grok"] == ("xai", None)
    assert parse_grok_models("You are logged in with grok.com.\n\nDefault model: grok-4.7\n\nAvailable models:\n  * grok-4.7 (default)\n") == ["grok-4.7"]
    try:
        parse_grok_models("You are not authenticated.\n\nDefault model: grok-4.7")
        raise AssertionError("signed out must be said, not answered")
    except RuntimeError as e:
        assert "grok login" in str(e)
    info = {}
    evs = [{"type": "available_commands", "tools": ["todo_write"]}, {"type": "thought", "data": "The user wants ok."},
           {"type": "text", "data": "o"}, {"type": "text", "data": "k"},
           {"type": "usage", "usage": {"input_tokens": 158, "output_tokens": 27}},
           {"type": "end", "stopReason": "end_turn", "total_cost_usd": 0.0345}]
    out = [cli_event("grok", ev, info) for ev in evs]
    assert "".join(p for p, _ in out if p) == "ok" and not any(f for _, f in out), out
    assert info["usage"] == {"input_tokens": 158, "output_tokens": 27, "cost_usd": 0.0345}, info
    assert cli_event("grok", {"type": "end", "stopReason": "cancelled"})[1] == "Grok stopped: cancelled"
    a = cli_argv("xai", ["grok"], "grok-4.7", False, "p.txt")
    assert a[1:3] == ["--prompt-file", "p.txt"] and "--disable-web-search" in a and "todo_write" in a and "run_terminal_command" not in a, a
    assert cli_argv("xai", ["grok"], "grok-4.7", True, "p.txt") == a, "Grok isn't a research model: research asks it like chat"
    assert cli_argv("gemini", ["node", "g.js"], "flash", False)[-2:] == ["-p", REPLY_AS] and "plan" in cli_argv("gemini", ["n"], "flash", False)
    assert cli_event("gemini", {"type": "message", "role": "assistant", "content": "Paris."}) == ("Paris.", None)
    assert cli_error("Grok", "You are not authenticated.") == "Grok isn't signed in: run `grok login` in a terminal once."
    # Mistral: its own API, or through OpenRouter; a free-tier OpenRouter key only reaches ":free" models, and never another vendor's
    assert ALIASES["mistral"] == ("mistral", None) and PROVIDERS["mistral"]["via"][0] == "openrouter"
    listed = [{"id": "mistralai/mistral-large-4-0", "created": 3}, {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "created": 2},
              {"id": "liquid/lfm-2.5-2.6b:free", "created": 4}, {"id": "nvidia/nemotron-3.5-lightning:free", "created": 6},
              {"id": "openai/text-embedding-3:free", "created": 5}, {"id": "mistralai/mistral-medium-3-5:batch", "created": 9}]
    assert choose("openrouter", listed, "mistralai/mistral-")["id"] == "mistralai/mistral-large-4-0", "a paid key: the vendor's own model"
    assert choose("openrouter", listed, free=True)["id"] == "nvidia/nemotron-3-ultra-550b-a55b:free", "free: the bigger general model"
    try:
        choose("openrouter", listed, "mistralai/mistral-", free=True)
        raise AssertionError("no free Mistral model must be said, not replaced by another vendor's")
    except RuntimeError as e:
        assert "no free model from mistralai" in str(e) and "free-tier" in str(e)
    assert choose("mistral", [{"id": "mistral-large-latest"}, {"id": "mistral-embed"}, {"id": "mistral-small-latest", "created": 9}])["id"] == "mistral-large-latest"
    real_key = key_for
    for keys, want in (({"OPENROUTER_API_KEY"}, ("openrouter", "mistralai/mistral-")), ({"MISTRAL_API_KEY", "OPENROUTER_API_KEY"}, ("mistral", None)),
                       (set(), ("mistral", None))):
        key_for = lambda p, _k=keys: "k" if PROVIDERS[p].get("key") in _k else None  # noqa: E731
        assert via("mistral") == want, (keys, via("mistral"))
    key_for = real_key
    reg = {r["key"]: r for r in registry({"status": "offline"})}
    assert reg["ollama"]["status"] == "unavailable" and reg["freellmapi"]["status"] == "unavailable" and "gemini" in reg
    assert all(r["status"] in ("available", "unavailable") and r["detail"] for r in reg.values()), "every row says why"
    # the authentication registry (2026-10-27): contexts kept apart, a key present is unknown until it answers, never a key's value
    from .agents import AUTH_STATUSES, CONTEXTS
    assert (reg["ollama"]["context"], reg["ollama"]["auth_status"], reg["ollama"]["last_error"], reg["ollama"]["required"]) == \
        ("local_runtime", "connection_unavailable", "offline", True) and reg["ollama"]["last_ok_at"] is None, reg["ollama"]
    assert (reg["freellmapi"]["context"], reg["freellmapi"]["auth_status"], reg["freellmapi"]["last_error"]) == ("cloud", "configuration_missing", "not_configured")
    assert reg["gemini"]["context"] == "cli" and reg["gemini"]["key_env"] is None and (
        reg["gemini"]["remediation"] == PROVIDERS["gemini"]["login"] if reg["gemini"]["status"] == "available" else reg["gemini"]["last_error"] == "not_installed")
    assert all(r["auth_status"] in AUTH_STATUSES and r["context"] in CONTEXTS and r["checked_at"] and r["capabilities"] == "chat"
               and not r["required"] or r["key"] == "ollama" for r in reg.values()), reg
    key_for = lambda p: "sk-NEVER-SHOWN" if "key" in PROVIDERS[p] else None  # noqa: E731  (every API key present)
    reg = {r["key"]: r for r in registry({"status": "online", "models": [{"name": "m"}]})}
    assert (reg["anthropic"]["context"], reg["anthropic"]["auth_status"], reg["anthropic"]["remediation"], reg["anthropic"]["last_error"]) == \
        ("api_key", "unknown", NOT_PROVEN, None) and reg["anthropic"]["last_ok_at"] is None, "a key present isn't proven"
    assert reg["ollama"]["auth_status"] == "authenticated" and reg["ollama"]["last_ok_at"] and reg["ollama"]["remediation"] is None
    assert "NEVER-SHOWN" not in json.dumps(list(reg.values())), "never a key's value in a row"
    key_for = lambda p: None  # noqa: E731
    reg = {r["key"]: r for r in registry()}
    assert (reg["anthropic"]["auth_status"], reg["anthropic"]["last_error"]) == ("configuration_missing", "key_missing") and \
        reg["anthropic"]["remediation"].startswith("add ANTHROPIC_API_KEY=") and "ollama" not in reg, reg["anthropic"]
    # an answer proves a key: the registry's last_ok_at (the stream read as the provider sends it, through a stand-in transport)
    key_for = lambda p: "k"  # noqa: E731
    _real_client = httpx.AsyncClient

    async def _sse(request):
        return httpx.Response(200, text='data: {"type":"content_block_delta","delta":{"text":"Hi"}}\n\ndata: [DONE]\n')
    httpx.AsyncClient = lambda **k: _real_client(transport=httpx.MockTransport(_sse))
    try:
        async def _answer():
            return [p async for p in stream("anthropic", "claude-x", [{"role": "user", "content": "hi"}])]
        assert asyncio.run(_answer()) == ["Hi"] and LAST_OK.get("anthropic"), LAST_OK
        assert next(r for r in registry() if r["key"] == "anthropic")["last_ok_at"] == LAST_OK["anthropic"]
    finally:
        httpx.AsyncClient, key_for = _real_client, real_key
    print("providers self-check: OK,", ", ".join(c["label"] for c in PROVIDERS.values()))
