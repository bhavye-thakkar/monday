"""AI usage limits (the owner's brief of 2026-10-29 and the Glasswave recording "Your AI limits, live in the notch"; docs/adr/0011):
one card per AI the owner uses, each read from the source that AI supports, never estimated. Pure rules and the readers, no Qt. The
notch reads them once, on its source thread (Notch._load_sources), and all three notch modes draw the same cards.

A card: {"key", "name", "status", "plan", "windows", "note", "source", "at", "read_at"}
  status: one of STATUS (never one in place of another: a failed read is not "exhausted", a key present is not "signed in")
  windows: [{"name", "used" (% used, 0-100) or None, "count" ((used, limit) or None), "resets_at" (epoch s) or None}]
  at: when the source said it (epoch s), read_at: when Monday asked.

python -m core.usage --check   the rules, against stand-in sources
python -m core.usage --live    this PC's real sources (reads only, nothing written, no key printed)
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import island as I

STATUS = {  # status -> (its words on the card, its dot: ok / wait / bad / off)
    "ok": ("Up to date", "ok"),
    "stale": ("Last known", "wait"),           # the last good reading, kept while the source can't be read now (said with its age)
    "unavailable": ("Couldn't reach it", "bad"),
    "auth": ("Sign in needed", "wait"),        # the CLI says it isn't signed in (its own word, not the desktop app's)
    "expired": ("Key refused", "bad"),         # the provider answered 401/403 to the key in .env
    "unsupported": ("No usage source", "off"),  # the provider offers no way to read its use (said, never guessed)
    "exhausted": ("Limit reached", "bad"),     # a window the source itself says is at 100 %
    "limited": ("Rate limited", "wait"),       # the source answered 429 to Monday's own question
    "local_ok": ("Running here", "ok"),
    "local_down": ("Not running", "off"),
    "none": ("Nothing read yet", "off"),       # set up, but its source hasn't said anything yet
}
ORDER = ("claude_code", "codex", "copilot", "kiro", "kilo", "gemini", "cursor", "antigravity", "windsurf", "openrouter", "ollama",
         "jev")   # the cards' order unless the owner moved them (Mode 3's layout); a card only for what is set up or installed here
NAMES = {"claude_code": "Claude Code", "codex": "Codex", "copilot": "GitHub Copilot", "kiro": "Kiro", "kilo": "Kilo", "gemini": "Gemini CLI",
         "cursor": "Cursor", "antigravity": "Antigravity", "windsurf": "Windsurf", "openrouter": "OpenRouter", "ollama": "Ollama", "jev": "Jev"}
# the IDEs and CLIs whose makers document no usage source for one person (the research of 2026-10-29): what the card says instead
NO_SOURCE = {
    "gemini": "Gemini CLI documents no way to read its quota (its /stats is interactive only)",
    "cursor": "Cursor shows its usage only on its dashboard (its usage API is for Enterprise teams)",
    "antigravity": "Antigravity shows its quota only in its own settings (its /usage is interactive only)",
    "windsurf": "Windsurf shows its quota only in the app and on its plan page (its API is for Enterprise teams)",
}
CMD_S = 20.0   # an IDE's own CLI is given this long to answer

WINDOW_WORDS = {"5-hour": "5 hours", "week": "This week", "month": "This month"}   # Codex's windows as the recording names them
HIGH, FULL = 80, 95          # % used from which a window is "high" (amber) and "full" (red): Monday's own lines, and the nudges'
STALE_S = 6 * 3600           # a reading older than this is "Last known", however it was read
EVERY_S, MOST_S = 120.0, 1800.0   # a source asked at most this often; after failures, twice as long each time, up to MOST_S
TIMEOUT_S = 6.0
OLLAMA_CLOUD_NOTE = "Ollama's API doesn't report cloud use"
CLAUDE_LIMITS = Path(__file__).resolve().parent.parent / "data" / "claude-limits.json"  # Claude Code's limits, kept by Monday's status line
# relay (agent_hooks.py statusline) when the owner put it in Claude Code's status line
CLAUDE_WINDOWS = (("five_hour", "5 hours"), ("seven_day", "This week"))
CODEX_SOURCE = "Codex's own session log (a local file; OpenAI documents no format for it)"


def card(key, status, windows=(), note="", source="", at=None, plan=None, now=None):
    return {"key": key, "name": NAMES.get(key, key), "status": status if status in STATUS else "unavailable", "plan": plan,
            "windows": list(windows), "note": note, "source": source, "at": at, "read_at": time.time() if now is None else now}


def window(name, used=None, count=None, resets_at=None, unit="requests"):
    return {"name": name, "used": None if used is None else max(0, min(100, int(round(float(used))))), "count": count,
            "resets_at": resets_at, "unit": unit}


# -- what a card says ---------------------------------------------------------------------------------------------------------------------
def percent(w):
    """A window's % used: its own %, or used / limit for a request count. None when it has neither (never made up)."""
    if w.get("used") is not None:
        return w["used"]
    c = w.get("count")
    if c and c[1]:
        return max(0, min(100, int(round(100 * c[0] / c[1]))))
    return None


def heat(pct):
    """'ok', 'high' (from HIGH %) or 'full' (from FULL %); None without a %."""
    return None if pct is None else "full" if pct >= FULL else "high" if pct >= HIGH else "ok"


def clock(at, h24=True):
    t = time.localtime(at)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}" if h24 else f"{(t.tm_hour % 12) or 12}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"


def reset_words(at, now=None, h24=True):
    """The recording's "Resets 11:01 AM · in 1 hr 20 min" / "Resets Thu 2:51 PM"; "Reset time not given" when the source has none."""
    now = time.time() if now is None else now
    if not at:
        return "Reset time not given"
    left, t = at - now, time.localtime(at)
    if left <= 0:
        return "Reset since this reading"
    if left < 86400:
        h, m = int(left // 3600), int(left % 3600 // 60)
        return f"Resets {clock(at, h24)} · in " + (f"{h} hr {m} min" if h else f"{m} min")
    day = I.WEEKDAYS[t.tm_wday] if left < 7 * 86400 else f"{I.MONTHS[t.tm_mon - 1]} {t.tm_mday}"
    return f"Resets {day} {clock(at, h24)}"


def value_words(w):
    """What a window's number is, in its own unit: "34%", "3 of 50 requests"."""
    c, unit = w.get("count"), w.get("unit") or "requests"
    if c:
        return f"{c[0]:g} of {c[1]:g} {unit}" if c[1] else f"{c[0]:g} {unit}"
    return f"{w['used']}%" if w.get("used") is not None else "—"


def age_words(c, now=None):
    """"2 min ago" / "updated 2 d ago" as the recording heads a card; "" with no time."""
    return I.ago(c["at"], now).replace("now", "just now") + ("" if I.ago(c["at"], now) == "now" else " ago") if c.get("at") else ""


def busiest(cards):
    """(card, window, %) of the highest real % among cards that have one: the limits notch's closed bar, and the nudges. None: none has."""
    best = None
    for c in cards or []:
        if c["status"] not in ("ok", "stale", "exhausted"):
            continue
        for w in c["windows"]:
            p = percent(w)
            if p is not None and (best is None or p > best[2]):
                best = (c, w, p)
    return best


def nudges(cards, told, now=None):
    """The windows that crossed HIGH or FULL since the owner was last told ("A nudge before you run out"): [(card, window, %, level)],
    and `told` updated ({key/window: (level, its resets_at)}: told once per level per window, again after it resets)."""
    out = []
    for c in cards or []:
        if c["status"] not in ("ok", "exhausted"):  # (a stale reading never nudges: it may be old news)
            continue
        for w in c["windows"]:
            p, k = percent(w), f"{c['key']}/{w['name']}"
            lvl = heat(p)
            if lvl not in ("high", "full"):
                continue
            was = told.get(k)
            if was and was[1] == w.get("resets_at") and (was[0] == lvl or was[0] == "full"):
                continue
            told[k] = (lvl, w.get("resets_at"))
            out.append((c, w, p, lvl))
    return out


# -- keeping the last good reading; how often to ask -------------------------------------------------------------------------------------
def merge(old, new, now=None):
    """A new read against the last one: when the source couldn't be reached or rate-limited Monday's question, the last good numbers
    stay, said as "Last known" with why; a refused key or sign-in never keeps them (they may not be this account's any more)."""
    now = time.time() if now is None else now
    if new["status"] in ("unavailable", "limited") and old and old.get("windows") and old["status"] in ("ok", "stale", "exhausted"):
        why = STATUS[new["status"]][0].lower()
        return {**old, "status": "stale", "note": f"{why} at {clock(now)}: showing the last reading", "read_at": new["read_at"]}
    if new["status"] in ("ok", "exhausted") and new.get("at") and now - new["at"] > STALE_S:
        return {**new, "status": "stale"}
    return new


def wait_for(fails, every=EVERY_S):
    """Seconds before asking a source again: `every`, twice as long after each failure in a row, at most MOST_S."""
    return min(MOST_S, every * 2 ** max(0, fails))


def failed(c):
    """A read that went wrong, or a sign-in still missing: asked again later and later (wait_for), never every two minutes."""
    return c["status"] in ("unavailable", "limited", "expired", "auth")


# -- the sources -------------------------------------------------------------------------------------------------------------------------
def codex(plans, tool=None, now=None):
    """Codex's limits as its own session log last wrote them (watch.plan_use: token_count's rate_limits); its CLI's sign-in from
    Core's look at it (/api/ai-tools)."""
    now = time.time() if now is None else now
    if tool and tool.get("status") == "not_authenticated":
        return card("codex", "auth", note="Codex isn't signed in: `codex login`", source="codex login status", now=now)
    p = next((x for x in plans or [] if x.get("agent") == "Codex"), None)
    if not p:
        return card("codex", "none", note="Codex writes its limits into its session log while it works; none logged yet",
                    source=CODEX_SOURCE, now=now)
    wins = [window(WINDOW_WORDS.get(w["name"], w["name"]), None if w.get("resets_at") and w["resets_at"] <= now else w["used"],
                   resets_at=w.get("resets_at")) for w in p.get("windows") or []]
    st = "exhausted" if any(w["used"] is not None and w["used"] >= 100 for w in wins) else "ok"
    return merge(None, card("codex", st, wins, source=CODEX_SOURCE, at=p.get("at"), plan=p.get("plan"), now=now), now)


def claude_code(tool=None, now=None, feed=None, relay=False):
    """Claude Code's plan limits as it gave them to its status line (code.claude.com/docs/en/statusline: rate_limits five_hour and
    seven_day, used % and when each resets; claude.ai Pro and Max only, after a session's first answer), kept by Monday's relay
    (agent_hooks.py statusline) in CLAUDE_LIMITS: `feed`, that file read. A window past its reset has started again: no % (the docs drop
    it too). No feed: the card says where the numbers would come from, or (`relay`: the relay is in Claude Code's status line) that they
    come with a session's first answer. Its CLI's sign-in from Core's look at it (`claude auth status`)."""
    now = time.time() if now is None else now
    if tool and tool.get("status") == "not_authenticated":
        return card("claude_code", "auth", note="Claude Code's CLI isn't signed in: run `claude` once", source="claude auth status", now=now)
    if not isinstance(feed, dict) or not any(isinstance(feed.get(k), dict) for k, _ in CLAUDE_WINDOWS):
        if relay:  # (set up, nothing said yet: Claude Code gives them after a session's first answer, on claude.ai Pro and Max only)
            return card("claude_code", "none", note="Monday's relay is in Claude Code's status line: its limits come with a session's first "
                        "answer (claude.ai Pro and Max)", source="Claude Code's status line (rate_limits)", now=now)
        return card("claude_code", "unsupported", note="Claude Code gives its plan use only to a status line: Monday's relay isn't in yours",
                    source="Claude Code's status line (rate_limits)", now=now)
    wins = []
    for k, name in CLAUDE_WINDOWS:
        w = feed.get(k)
        if isinstance(w, dict) and w.get("used_percentage") is not None:
            at = w.get("resets_at")
            wins.append(window(name, None if at and at <= now else w["used_percentage"], resets_at=at))
    st = "exhausted" if any(w["used"] is not None and w["used"] >= 100 for w in wins) else "ok"
    return merge(None, card("claude_code", st, wins, source="Claude Code's status line (rate_limits)", at=feed.get("at"), now=now), now)


def relay_on(home=None):
    """Whether Monday's relay is in Claude Code's status line (~/.claude/settings.json statusLine; put there with the owner's OK)."""
    try:
        d = json.loads((Path(home or Path.home()) / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return "agent_hooks.py statusline" in str(((d or {}).get("statusLine") or {}).get("command") or "")
    except (OSError, ValueError, AttributeError):
        return False


def claude_feed(path=None):
    """What Monday's status line relay last kept (CLAUDE_LIMITS), or None: no relay, or nothing kept yet."""
    try:
        return json.loads(Path(path or CLAUDE_LIMITS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def openrouter(get, key, now=None):
    """OpenRouter's own key endpoint (GET /api/v1/key, documented; costs nothing): the free models' requests today, the key's budget,
    and the account's credits (GET /api/v1/credits). 401/403: the key in .env refused; 429: rate limited."""
    now = time.time() if now is None else now
    head = {"Authorization": f"Bearer {key}"}
    try:
        code, body = get("https://openrouter.ai/api/v1/key", head)
    except Exception as e:  # the network, DNS, a proxy: the source couldn't be reached (not "exhausted")
        return card("openrouter", "unavailable", note=f"couldn't reach OpenRouter ({type(e).__name__})", source="OpenRouter /api/v1/key", now=now)
    if code in (401, 403):
        return card("openrouter", "expired", note=f"OpenRouter refused the key in .env (HTTP {code})", source="OpenRouter /api/v1/key", now=now)
    if code == 429:
        return card("openrouter", "limited", note="OpenRouter rate-limited Monday's question", source="OpenRouter /api/v1/key", now=now)
    if code != 200 or not isinstance(body, dict):
        return card("openrouter", "unavailable", note=f"OpenRouter answered HTTP {code}", source="OpenRouter /api/v1/key", now=now)
    d = body.get("data") or {}
    wins = []
    free = d.get("free_model_daily_requests")
    if isinstance(free, dict) and free.get("limit"):
        wins.append(window("Free models today", count=(int(free.get("used") or 0), int(free["limit"]))))
    b = d.get("effective_budget") or {}
    if (b.get("limit_usd") or 0) > 0:  # a budget on the key: its spend, as a share of it (a key with no budget has nothing to fill)
        wins.append(window(f"Key budget ({b.get('reset_interval') or 'total'})", 100 * float(b.get("spend_usd") or 0) / float(b["limit_usd"]),
                           resets_at=_iso(b.get("resets_at"))))
    try:
        code2, cred = get("https://openrouter.ai/api/v1/credits", head)
        cr = (cred or {}).get("data") or {} if code2 == 200 else {}
    except Exception:
        cr = {}
    total, spent = float(cr.get("total_credits") or 0), float(cr.get("total_usage") or 0)
    if total > 0:
        wins.append(window("Credits", 100 * spent / total))
    note = f"${spent:.2f} of ${total:.2f} credits used" if total > 0 else "no credits bought: the free models only" if d.get("is_free_tier") else ""
    st = "exhausted" if any((percent(w) or 0) >= 100 for w in wins) else "ok"
    return card("openrouter", st, wins, note=note, source="OpenRouter /api/v1/key", at=now, plan="Free" if d.get("is_free_tier") else "Credits",
                now=now)


def ollama(get, base, now=None):
    """Ollama on this PC (its own API: /api/version, /api/tags, /api/ps): running or not, its models, what is loaded now. It has no
    quota; its cloud models' use isn't in its API, which the card says."""
    now = time.time() if now is None else now
    try:
        code, ver = get(base + "/api/version", {})
        if code != 200:
            raise OSError(f"HTTP {code}")
        _c, tags = get(base + "/api/tags", {})
        _c, ps = get(base + "/api/ps", {})
    except Exception as e:
        return card("ollama", "local_down", note=f"not answering at {base} ({type(e).__name__})", source="Ollama API", now=now)
    names = [m.get("name") or "" for m in (tags or {}).get("models") or []]
    cloud = sum(1 for n in names if n.endswith("cloud") or ":cloud" in n or "-cloud" in n)
    loaded = [m.get("name") for m in (ps or {}).get("models") or []]
    note = f"{len(names) - cloud} on this PC, {cloud} cloud · " + (", ".join(loaded[:2]) + " loaded" if loaded else "none loaded")
    return card("ollama", "local_ok", note=note + f" · {OLLAMA_CLOUD_NOTE}", source="Ollama API", at=now,
                plan=f"v{(ver or {}).get('version', '?')}", now=now)


def jev(key, now=None):
    """Jev (TypeSafe System One): its limits are per second per organization (a 429 when over, docs.typesafe.ai/models); TypeSafe has no
    usage endpoint, so the card says that instead of a number."""
    return card("jev", "unsupported", note="TypeSafe has no usage endpoint: its limits are per second (a 429 when over)",
                source="docs.typesafe.ai/models", now=now) if key else None


def month_end_utc(now=None):
    """The 1st of next month, 00:00 UTC: when Copilot's and Kiro's monthly allowances start again (their docs)."""
    t = time.gmtime(time.time() if now is None else now)
    y, m = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    return float(calendar.timegm((y, m, 1, 0, 0, 0)))


def run_cmd(cmd, timeout=CMD_S):
    """(exit code, stdout, stderr) of a CLI, its own console never shown (the notch runs windowless: a console app would flash one)."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", f"{type(e).__name__}"
    dec = lambda b: (b or b"").decode("utf-8", "replace")  # noqa: E731
    return r.returncode, dec(r.stdout), dec(r.stderr)


def copilot(run, now=None):
    """GitHub Copilot's AI credits this month, from GitHub's own billing usage API (docs.github.com/en/rest/billing/usage) through the
    owner's signed-in `gh`: what was used (it gives no allowance, which depends on the plan), resetting 00:00 UTC on the 1st. The API needs
    gh's "user" scope (or a token with Plan: read); without it the card says how to give it."""
    now = time.time() if now is None else now
    src = "GitHub billing usage API (gh)"
    code, out, err = run(["gh", "api", "user", "--jq", ".login"])
    login = out.strip()
    if code != 0 or not login:
        return card("copilot", "auth", note="GitHub CLI isn't signed in: `gh auth login`", source=src, now=now)
    for kind in ("ai_credit", "premium_request"):  # (AI credits since 2026-06-01; premium requests on older plans)
        code, out, err = run(["gh", "api", f"users/{login}/settings/billing/{kind}/usage"])
        if code == 0:
            try:
                items = (json.loads(out) or {}).get("usageItems") or []
            except ValueError:
                return card("copilot", "unavailable", note="GitHub's answer wasn't readable", source=src, now=now)
            used = sum(float(i.get("grossQuantity") or 0) for i in items if "copilot" in str(i.get("product") or i.get("sku") or "").lower()
                       or kind == "ai_credit")
            unit = "AI credits" if kind == "ai_credit" else "premium requests"
            return card("copilot", "ok", [window(f"{unit[0].upper() + unit[1:]} this month", count=(round(used, 2), 0),
                                                 resets_at=month_end_utc(now), unit=unit)],
                        note="GitHub gives what was used, not your plan's allowance", source=src, at=now, now=now)
        if '"user" scope' in err or "user scope" in err:
            return card("copilot", "auth", note="GitHub's billing API needs gh's \"user\" scope: gh auth refresh -h github.com -s user",
                        source=src, now=now)
        if "rate limit" in (err + out).lower() or "HTTP 429" in err:
            return card("copilot", "limited", note="GitHub rate-limited Monday's question", source=src, now=now)
    return card("copilot", "unavailable", note=f"GitHub didn't give the usage ({(err or out).strip().splitlines()[-1][:80] if (err or out).strip() else 'no answer'})",
                source=src, now=now)


def kiro(run, cli, now=None):
    """Kiro's credits from the Kiro CLI's own /usage (kiro.dev/docs/reference/slash-commands: "In non-interactive runs, /usage prints the
    full usage breakdown"); its monthly allowance starts again 00:00 UTC on the 1st (kiro.dev/docs/billing). Its words aren't specified:
    "N of M credits" is read when it says so, else its first line is shown. No Kiro CLI here: the card says where the credits are."""
    now = time.time() if now is None else now
    src = "Kiro CLI /usage"
    if not cli:
        return card("kiro", "unsupported", note="Kiro gives its credits in the Kiro CLI's /usage (not installed here) and in the IDE",
                    source=src, now=now)
    code, out, err = run([cli, "chat", "--no-interactive", "/usage"])
    text = (out or err).strip()
    if code != 0 or not text:
        low = text.lower()
        st = "auth" if ("login" in low or "sign in" in low or "unauthor" in low) else "unavailable"
        return card("kiro", st, note=(text.splitlines()[0][:100] if text else "the Kiro CLI gave no answer"), source=src, now=now)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:/|of)\s*(\d[\d,]*(?:\.\d+)?)\s*credits", text, re.I)
    if not m:
        return card("kiro", "ok", note=text.splitlines()[0][:100], source=src, at=now, now=now)
    used, limit = (float(x.replace(",", "")) for x in m.groups())
    return card("kiro", "exhausted" if limit and used >= limit else "ok", [window("Credits this month", count=(used, limit),
                resets_at=month_end_utc(now), unit="credits")], source=src, at=now, now=now)


def kilo(run, cli, now=None):
    """Kilo's balance from its CLI's `kilo profile` (kilo.ai/docs: "show Kilo account profile"); pay as you go, so a balance and no limit
    or reset. Its words aren't specified: "balance ... $N" is read when it says so. A refused sign-in (401) says so."""
    now = time.time() if now is None else now
    src = "kilo profile"
    code, out, err = run([cli, "profile"])
    text = re.sub(r"\x1b\[[0-9;]*m", "", (out + "\n" + err)).strip()
    if "401" in text or "invalid token" in text.lower() or "not logged in" in text.lower():
        return card("kilo", "expired", note="Kilo refused its sign-in (401): sign in to Kilo again", source=src, now=now)
    m = re.search(r"balance[^\n\d$-]*\$?\s*(-?\d+(?:\.\d+)?)", text, re.I)
    if code != 0 or not m:
        return card("kilo", "unavailable", note=(text.splitlines()[0][:100] if text else "the Kilo CLI gave no answer"), source=src, now=now)
    return card("kilo", "ok", note=f"Balance ${float(m.group(1)):.2f} (pay as you go: no limit, no reset)", source=src, at=now, now=now)


def no_source(key, now=None):
    return card(key, "unsupported", note=NO_SOURCE[key], source="none documented", now=now)


def _iso(v):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def http_get(url, headers):
    """(status, JSON body or None): the readers' one way out (httpx, Monday's TLS)."""
    import httpx
    from .probes import tls
    r = httpx.get(url, headers=headers, timeout=TIMEOUT_S, verify=tls() if url.startswith("https") else True)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def read_all(tools=None, last=None, due=None, now=None, get=None, plans=None, run=None):
    """Every set-up AI's card, in ORDER. tools: Core's /api/ai-tools by key (the CLIs' own sign-in); last: the cards read before, by key
    (kept, marked "Last known", when a source can't be read now; also returned as they are for a source not `due`); due: the keys to ask
    now (None: all); plans: watch.plan_use() when the caller read it already. Never raises: a reader's own fault is its card's
    "Couldn't reach it"."""
    from . import jev as J
    from . import probes, watch
    now = time.time() if now is None else now
    tools, last, get = tools or {}, last or {}, get or http_get  # (http_get looked up now: a check stands in for it, never the network)
    run = run or run_cmd  # (the IDEs' own CLIs: a check stands in for them too)

    def installed(key, exe):  # Core's look at the CLI when it has one, else the CLI found on this PC
        st = (tools.get(key) or {}).get("status")
        return st not in (None, "unavailable", "not_installed") if st else bool(exe())

    L = os.environ.get("LOCALAPPDATA", "")

    def app(*parts):  # an IDE installed for this Windows user (its usual folder)
        return lambda: os.path.exists(os.path.join(L, "Programs", *parts))
    readers = {
        "claude_code": lambda: claude_code(tools.get("claude_code"), now, claude_feed(), relay_on()) if installed("claude_code", probes.claude_exe)
        else None,
        "codex": lambda: codex(watch.plan_use() if plans is None else plans, tools.get("codex"), now) if installed("codex", probes.codex_exe) else None,
        "copilot": lambda: copilot(run, now) if shutil.which("gh") and (installed("copilot_cli", lambda: shutil.which("copilot"))
                                                                          or installed("vscode", app("Microsoft VS Code", "Code.exe"))) else None,
        "kiro": lambda: kiro(run, shutil.which("kiro-cli"), now) if installed("kiro", app("Kiro", "Kiro.exe")) or shutil.which("kiro-cli") else None,
        "kilo": lambda: kilo(run, c, now) if (c := shutil.which("kilo")) else None,
        "gemini": lambda: no_source("gemini", now) if installed("gemini_cli", lambda: shutil.which("gemini")) else None,
        "cursor": lambda: no_source("cursor", now) if installed("cursor", app("cursor", "Cursor.exe")) else None,
        "antigravity": lambda: no_source("antigravity", now) if installed("antigravity", app("Antigravity", "Antigravity.exe")) else None,
        "windsurf": lambda: no_source("windsurf", now) if installed("windsurf", app("Windsurf", "Windsurf.exe")) else None,
        "openrouter": lambda: openrouter(get, k, now) if (k := J.setting("OPENROUTER_API_KEY")) else None,
        "ollama": lambda: ollama(get, probes.OLLAMA, now),
        "jev": lambda: jev(J.resolve_key()[0], now),
    }
    out = []
    for key in ORDER:
        if due is not None and key not in due and key in last:
            out.append(last[key])
            continue
        try:
            c = readers[key]()
        except Exception as e:
            c = card(key, "unavailable", note=f"reading it failed ({type(e).__name__})", now=now)
        if c is not None:
            out.append(merge(last.get(key), c, now))
    return out


def arrange(cards, layout):
    """Mode 3's own layout ({"order": [keys], "hidden": [keys]}, data/desktop.json): the owner's order first, then the rest in ORDER;
    hidden ones left out. An unknown or broken layout is ignored."""
    layout = layout if isinstance(layout, dict) else {}
    order = [k for k in layout.get("order") or [] if k in ORDER] if isinstance(layout.get("order"), list) else []
    hidden = set(k for k in layout.get("hidden") or [] if k in ORDER) if isinstance(layout.get("hidden"), list) else set()
    rank = {k: i for i, k in enumerate(list(dict.fromkeys(order + list(ORDER))))}
    return sorted((c for c in cards if c["key"] not in hidden), key=lambda c: rank.get(c["key"], 99))


# -- checks ------------------------------------------------------------------------------------------------------------------------------
def _check():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
        ok = ok and bool(cond)

    now = 1_800_000_000.0
    # Codex, from its log as watch.plan_use gives it
    plans = [{"agent": "Codex", "plan": "Plus", "windows": [{"name": "5-hour", "used": 86, "resets_at": now + 4800},
                                                             {"name": "week", "used": 41, "resets_at": now - 60}], "at": now - 120}]
    c = codex(plans, {"status": "available"}, now)
    check("Codex: its windows in the recording's words", [w["name"] for w in c["windows"]] == ["5 hours", "This week"], c)
    check("Codex: a window that reset since the reading has no % (not the old one)", c["windows"][1]["used"] is None)
    check("Codex: status ok, its plan and when its log said it", c["status"] == "ok" and c["plan"] == "Plus" and c["at"] == now - 120)
    check("Codex: an old log reading is Last known", codex([{**plans[0], "at": now - STALE_S - 1}], None, now)["status"] == "stale")
    check("Codex: nothing logged says so, no number", codex([], None, now)["status"] == "none" and codex([], None, now)["windows"] == [])
    check("Codex: not signed in is 'Sign in needed', not exhausted", codex(plans, {"status": "not_authenticated"}, now)["status"] == "auth")
    full = codex([{**plans[0], "windows": [{"name": "5-hour", "used": 100, "resets_at": now + 60}]}], None, now)
    check("Codex: 100% in its own log is Limit reached", full["status"] == "exhausted")
    # words
    check("reset words within the day", reset_words(now + 4800, now).startswith("Resets ") and "in 1 hr 20 min" in reset_words(now + 4800, now))
    check("reset words, no time given", reset_words(None, now) == "Reset time not given")
    check("reset words, already past", reset_words(now - 5, now) == "Reset since this reading")
    check("reset words in 12 h clock", reset_words(now + 600, now, h24=False).endswith("in 10 min") and ("AM" in reset_words(now + 600, now, False) or "PM" in reset_words(now + 600, now, False)))
    check("a count window's %", percent(window("x", count=(25, 50))) == 50 and percent(window("x")) is None)
    check("value words in each unit", value_words(window("x", 34)) == "34%" and value_words(window("x", count=(3, 50))) == "3 of 50 requests")
    check("heat lines", (heat(79), heat(80), heat(95), heat(None)) == ("ok", "high", "full", None))
    # OpenRouter, against its documented answers
    key_ok = {"data": {"is_free_tier": True, "free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38},
                       "effective_budget": {"limit_usd": 0, "spend_usd": 0, "resets_at": "2026-10-30T00:00:00.000Z"}}}

    def stand(routes):
        def get(url, headers):
            assert headers.get("Authorization", "Bearer x").startswith("Bearer"), "the key goes in the header, never the URL"
            r = routes.get(url.rsplit("/", 1)[-1])
            if isinstance(r, Exception):
                raise r
            return r
        return get
    o = openrouter(stand({"key": (200, key_ok), "credits": (200, {"data": {"total_credits": 0, "total_usage": 0}})}), "sk-x", now)
    check("OpenRouter: free requests today as a count, plan Free", o["status"] == "ok" and o["plan"] == "Free"
          and o["windows"] == [window("Free models today", count=(12, 50))], o)
    check("OpenRouter: a zero budget draws no bar", all(w["name"] != "Key budget (None)" for w in o["windows"]) and len(o["windows"]) == 1)
    paid = {"data": {"is_free_tier": False, "effective_budget": {"limit_usd": 10, "spend_usd": 9.6, "reset_interval": "monthly",
                                                                 "resets_at": "2026-11-01T00:00:00Z"}}}
    o2 = openrouter(stand({"key": (200, paid), "credits": (200, {"data": {"total_credits": 20, "total_usage": 5}})}), "sk-x", now)
    check("OpenRouter: a budget's spend as a % with its reset; credits too", [(w["name"], w["used"]) for w in o2["windows"]]
          == [("Key budget (monthly)", 96), ("Credits", 25)] and o2["windows"][0]["resets_at"] and o2["note"].startswith("$5.00 of $20.00"), o2)
    check("OpenRouter: 401 is 'Key refused', not exhausted", openrouter(stand({"key": (401, {})}), "sk-x", now)["status"] == "expired")
    check("OpenRouter: 429 is 'Rate limited'", openrouter(stand({"key": (429, {})}), "sk-x", now)["status"] == "limited")
    check("OpenRouter: no network is 'Couldn't reach it'", openrouter(stand({"key": OSError("down")}), "sk-x", now)["status"] == "unavailable")
    # the last good reading kept, said as such
    down = openrouter(stand({"key": OSError("down")}), "sk-x", now + 300)
    kept = merge(o, down, now + 300)
    check("a failed read keeps the last numbers as Last known, with why", kept["status"] == "stale" and kept["windows"] == o["windows"]
          and "couldn't reach it" in kept["note"], kept)
    check("a refused key never keeps the old numbers", merge(o, openrouter(stand({"key": (401, {})}), "sk-x", now), now)["status"] == "expired")
    check("a failure with nothing before stays a failure", merge(None, down, now)["status"] == "unavailable")
    # Ollama
    ol = ollama(stand({"version": (200, {"version": "0.40.12"}), "tags": (200, {"models": [{"name": "qwen3:4b"}, {"name": "gpt-oss:120b-cloud"}]}),
                       "ps": (200, {"models": [{"name": "qwen3:4b"}]})}), "http://127.0.0.1:11434", now)
    check("Ollama: running here, local and cloud counted, what's loaded, cloud use not reported", ol["status"] == "local_ok"
          and ol["note"].startswith("1 on this PC, 1 cloud · qwen3:4b loaded") and OLLAMA_CLOUD_NOTE in ol["note"] and ol["windows"] == [], ol)
    check("Ollama: down is 'Not running', no numbers", ollama(stand({"version": OSError("refused")}), "http://x", now)["status"] == "local_down")
    # the IDEs and coding CLIs (2026-10-29): documented sources only
    def runner(answers):  # a stand-in for the IDEs' CLIs: the longest matching key answers ("api user" isn't the billing call)
        def run(cmd):
            line = " ".join(cmd)
            for k in sorted(answers, key=len, reverse=True):
                if k in line and not (k == "api user" and "billing" in line):
                    return answers[k]
            return 1, "", "unknown command"
        return run
    end = month_end_utc(now)
    check("the month's end in UTC: the 1st, 00:00", time.gmtime(end).tm_mday == 1 and time.gmtime(end).tm_hour == 0 and end > now)
    cp = copilot(runner({"api user": (0, "owner\n", ""), "ai_credit": (0, json.dumps({"usageItems": [{"product": "Copilot", "grossQuantity": 120.5},
                                                                                                    {"product": "Copilot", "grossQuantity": 30}]}), "")}), now)
    check("Copilot: AI credits used this month from GitHub's billing API, its reset, no allowance made up", cp["status"] == "ok"
          and cp["windows"][0]["count"] == (150.5, 0) and value_words(cp["windows"][0]) == "150.5 AI credits"
          and percent(cp["windows"][0]) is None and cp["windows"][0]["resets_at"] == end, cp)
    cp2 = copilot(runner({"api user": (0, "owner", ""), "billing": (1, "", 'gh: This API operation needs the "user" scope. To request it, run: gh auth refresh -h github.com -s user')}), now)
    check("Copilot: gh without the user scope says how to give it, no number", cp2["status"] == "auth" and "gh auth refresh" in cp2["note"]
          and cp2["windows"] == [])
    check("Copilot: gh not signed in", copilot(runner({"api user": (1, "", "not logged in")}), now)["status"] == "auth")
    check("Kiro: no Kiro CLI, says where its credits are", kiro(runner({}), None, now)["status"] == "unsupported")
    kc = kiro(runner({"/usage": (0, "Credits: 312 / 1,000 credits used this month\nBonus: 0", "")}), "kiro-cli", now)
    check("Kiro: its /usage's 'N of M credits' read, the month's reset", kc["windows"][0]["count"] == (312.0, 1000.0)
          and percent(kc["windows"][0]) == 31 and kc["windows"][0]["resets_at"] == end, kc)
    kw = kiro(runner({"/usage": (0, "You have plenty left", "")}), "kiro-cli", now)
    check("Kiro: words it doesn't put as numbers are shown as they are, no number", kw["windows"] == [] and kw["note"] == "You have plenty left")
    check("Kiro: not signed in", kiro(runner({"/usage": (1, "", "Please login first")}), "kiro-cli", now)["status"] == "auth")
    kb = kilo(runner({"profile": (0, "Email: o@x\nBalance: $12.5\n", "")}), "kilo", now)
    check("Kilo: its balance, no limit or reset", kb["status"] == "ok" and "$12.50" in kb["note"] and kb["windows"] == [])
    check("Kilo: a refused sign-in (401) says so", kilo(runner({"profile": (1, "Failed to fetch balance { status: 401 }", "Error: Invalid token")}),
                                                        "kilo", now)["status"] == "expired")
    check("Gemini CLI, Cursor, Antigravity, Windsurf: no documented source, said", all(no_source(k, now)["status"] == "unsupported"
                                                                                       and no_source(k, now)["windows"] == [] for k in NO_SOURCE))
    check("a missing sign-in waits longer before it is asked again", failed(cp2))
    # unsupported ones
    check("Claude Code: no relay, no number: says where its use would come from", claude_code({"status": "available"}, now)["status"]
          == "unsupported" and claude_code(None, now, {"model": "x"})["windows"] == [])
    waiting = claude_code(None, now, None, relay=True)
    check("Claude Code: the relay in, nothing given yet: 'Nothing read yet', no number", waiting["status"] == "none" and waiting["windows"] == []
          and "first answer" in waiting["note"])
    home = Path(__file__).resolve().parent.parent / "data" / "harness" / "relay-home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command":
                                                    "python E:/monday/app/core/agent_hooks.py statusline -- powershell -File x.ps1"}}), encoding="utf-8")
    on = relay_on(home)
    (home / ".claude" / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "powershell -File x.ps1"}}), encoding="utf-8")
    off = relay_on(home)
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    broken = relay_on(home)
    import shutil
    shutil.rmtree(home, ignore_errors=True)
    check("the relay seen in Claude Code's status line, and not when it isn't (or the file is broken)", on and not off and not broken)
    feed = {"five_hour": {"used_percentage": 86.4, "resets_at": now + 4800}, "seven_day": {"used_percentage": 22.0, "resets_at": now - 5},
            "at": now - 90}
    cc = claude_code({"status": "available"}, now, feed)
    check("Claude Code: its status line's limits (the recording's 5 hours and This week), a past reset with no %", cc["status"] == "ok"
          and [(w["name"], w["used"]) for w in cc["windows"]] == [("5 hours", 86), ("This week", None)] and cc["at"] == now - 90, cc)
    check("Claude Code: an old feed is Last known", claude_code(None, now, {**feed, "at": now - STALE_S - 1})["status"] == "stale")
    check("Claude Code: 100% from its own status line is Limit reached",
          claude_code(None, now, {"five_hour": {"used_percentage": 100, "resets_at": now + 60}, "at": now})["status"] == "exhausted")
    tmpf = Path(__file__).resolve().parent.parent / "data" / "harness" / "claude-limits-check.json"
    tmpf.parent.mkdir(parents=True, exist_ok=True)
    tmpf.write_text(json.dumps(feed), encoding="utf-8")
    check("Claude Code: the relay's file read back as it was kept; none, None", claude_feed(tmpf) == feed and claude_feed(tmpf.with_name("nope.json")) is None)
    tmpf.unlink()
    check("Claude Code: CLI not signed in", claude_code({"status": "not_authenticated"}, now)["status"] == "auth")
    check("Jev: no usage endpoint said; not shown without a key", jev("k", now)["status"] == "unsupported" and jev(None, now) is None)
    # the busiest window and the nudges
    cards = [c, o, ol, claude_code(None, now)]
    b = busiest(cards)
    check("busiest: the highest real % (Codex 5 hours 86)", b and b[0]["key"] == "codex" and b[2] == 86, b)
    check("busiest: none when no card has a %", busiest([ol, claude_code(None, now)]) is None)
    told = {}
    n1 = nudges(cards, told, now)
    check("nudge once when a window passes 80%", [(x[0]["key"], x[3]) for x in n1] == [("codex", "high")])
    check("...not again at the same level", nudges(cards, told, now) == [])
    c95 = codex([{**plans[0], "windows": [{"name": "5-hour", "used": 96, "resets_at": now + 4800}]}], None, now)
    check("...again when it passes 95%", [x[3] for x in nudges([c95], told, now)] == ["full"])
    check("a Last known reading never nudges", nudges([{**c95, "status": "stale"}], {}, now) == [])
    # backoff and the owner's order
    check("backoff doubles and stops at MOST_S", (wait_for(0), wait_for(1), wait_for(2), wait_for(9)) == (120.0, 240.0, 480.0, MOST_S))
    arr = arrange(cards, {"order": ["openrouter", "nope"], "hidden": ["ollama"]})
    check("Mode 3's layout: the owner's order first, hidden left out, unknown keys ignored", [x["key"] for x in arr] == ["openrouter", "claude_code", "codex"])
    check("a broken layout is ignored", [x["key"] for x in arrange(cards, {"order": "x", "hidden": 3})] == ["claude_code", "codex", "openrouter", "ollama"])
    # read_all never raises, and only asks what is due
    calls = []

    def boom(url, headers):
        calls.append(url)
        raise OSError("no network")
    ran = []
    got = read_all({"claude_code": {"status": "available"}, "codex": {"status": "available"}}, {"openrouter": o}, due={"ollama"}, now=now, get=boom,
                   run=lambda cmd: ran.append(cmd) or (1, "", "no CLI in a check"))  # (never the owner's gh, Kiro or Kilo in a check)
    keys = [x["key"] for x in got]
    check("read_all: a source not due keeps its card, the due one is asked, the cards in ORDER",
          keys[:2] == ["claude_code", "codex"] and keys == [k for k in ORDER if k in keys]
          and next(x for x in got if x["key"] == "openrouter") is o and any("11434" in u or "/api/version" in u for u in calls), keys)
    check("every status has words and a dot", all(len(v) == 2 and v[1] in ("ok", "wait", "bad", "off") for v in STATUS.values()))
    print("usage self-check:", "OK" if ok else "FAILED")
    return ok


def _live():
    """This PC's sources as the notch would read them now (no key printed: the cards never hold one)."""
    for c in read_all():
        print(json.dumps({**c, "read_at": None}, ensure_ascii=False))


if __name__ == "__main__":
    if "--live" in sys.argv:
        _live()
    else:
        sys.exit(0 if _check() else 1)
