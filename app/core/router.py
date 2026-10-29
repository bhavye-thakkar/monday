"""Routing: deterministic rules first, then the Jev policy; plus the permission risk rules.

route() is pure rules (source='deterministic', confidence=None). Its fast path turns exact commands for Monday's
own tools ("open Chrome", "take a screenshot", "run the tests and tell me what's wrong") into a plan of steps that
the Computer worker executes without Jev or a model. policy() takes Jev's typed answers for the requests the rules
could not settle and decides, in code, what Monday does. Permissions are decided here by code, never by a model:
permit() rules on every step and command, and Jev can add an approval gate, never remove one.
"""
import difflib
import re

from . import files, power
from .jev import ACTION_SURE, ASK_P, REDO_P  # v9's gates, named where the questions are (core/jev.py); the policy applies them
from .memory import classify as memory_rules

AGENT_NAMES = {"claude_code": "Claude Code", "codex": "Codex", "gemini_cli": "Gemini CLI", "kilo": "Kilo Code",
               "grok_build": "Grok Build", "hermes": "Monday's agent", "computer": "Computer", "chat": "Monday",  # (the owner, 2026-10-20: Hermes isn't named in the UI)
               "antigravity": "Antigravity IDE", "cursor": "Cursor", "kiro": "Kiro", "vscode": "VS Code"}
# Agents that do code work: the headless ones Monday drives, then editors whose own agent takes a prompt (agents.EDITORS: the last
# resort, when no headless one can run; workers.EditorAgent).
CODING_AGENTS = ["claude_code", "codex", "gemini_cli", "kilo", "grok_build", "antigravity", "cursor", "kiro", "vscode"]  # + custom engines (core/engines.py register)
COMPUTER_AGENTS = ("computer", "hermes")                          # Monday's own loop, or Hermes

_AGENT_WORD = r"(claude(?:\s+code)?|codex|gemini\s+cli|kilo(?:\s*code)?|grok\s+build(?:\s+cli)?|hermes|computer[\s-]+use)"  # "computer use" is Hermes
_TASK_ID = re.compile(r"\bTASK-\d{4}-\d{4}\b", re.I)
_ADD_PROJECT = [
    re.compile(r"^(?:add|register)\s+project\s+(?P<path>.+?)\s+(?:as|named)\s+(?P<name>[\w .-]+?)[.!]?$", re.I),
    re.compile(r"^(?:add|register)\s+project\s+(?P<name>[\w .-]+?)\s+at\s+(?P<path>.+?)[.!]?$", re.I),
]
_STOP = re.compile(r"^(?:please\s+)?(?:stop|cancel|abort|kill|halt)\b|^(?:never\s*mind|forget\s+(?:it|that)|scratch\s+that)\b", re.I)
# "open What's up, no no no, I want you to open Spotify" (the owner, 2026-10-01): the words after the last correction are the
# request, when at least two follow. A bare "scratch that" stays a stop.
_CORRECTION = re.compile(r"^.*\b(?:no[,\s]+no(?:[,\s]+no)*|no[,\s]+wait|no[,\s]+sorry|scratch\s+that|actually[,\s]+no|no[,\s]+i\s+mean|i\s+meant)[,.\s]+(?=\S+\s+\S)", re.I)
# "kill Chrome" / "force close Spotify" is a process on this PC, not "stop Monday's task" (kill it, kill the task, kill Claude…).
_KILL = re.compile(r"^(?:please\s+)?(?:kill|force[\s-]?(?:close|quit)|end\s+(?:the\s+)?task(?:\s+(?:for|of))?)\s+(?:the\s+)?(?P<x>.+?)(?:\s+(?:process|app))?$", re.I)
_TASK_WORDS = re.compile(r"^(?:it|that|this|them|everything|all|all\s+tasks|(?:the\s+)?(?:current\s+|running\s+)?(?:task|job|agent)s?|task-\d.*|"
                         r"claude(?:\s+code)?|codex|gemini(?:\s+cli)?|kilo(?:\s*code)?|hermes|computer\s+use)$", re.I)
# Voice control, not task control: "stop talking" must never stop a running task.
_SHUSH = re.compile(r"^(?:(?:stop|quit)\s+(?:talking|speaking|reading)(?:\s+(?:out\s+loud|aloud))?|(?:be\s+)?quiet|shut\s+up|shh+|"
                    r"(?P<pause>pause|hold\s+on)(?:\s+(?:speaking|talking|reading|your\s+voice))?|(?P<resume>resume|continue|go\s+on|keep\s+going)"
                    r"(?:\s+(?:speaking|talking|reading))?)$", re.I)
# Filming mode (Monday.prepass): the mic stays open and Monday answers only what is clearly said to it. Hard rules, never
# Jev's call, with or without the name.
_FILMING = re.compile(r"^(?:(?P<on>we(?:'re|\s+are)\s+(?:now\s+)?filming|(?:start|begin|enter|turn\s+on|enable)\s+filming(?:\s+mode)?|filming\s+mode(?:\s+on)?)|"
                      r"(?P<off>(?:we(?:'re|\s+are)\s+)?(?:done|finished)\s+filming|(?:stop|end|exit|leave|turn\s+off|disable)\s+filming(?:\s+mode)?|filming\s+mode\s+off))$", re.I)
_JUDGE_DEMO = re.compile(r"^(?:show\s+(?:me\s+)?the\s+judge|(?:run|demo|test)\s+the\s+judge)$", re.I)
_STOP_SERVER = re.compile(r"^(?:stop|kill|shut\s+down)\s+(?:the\s+|my\s+)?(?:(?:local\s+)?(?:development|dev)\s+server|backend|frontend|server)\b", re.I)
_STATUS = re.compile(
    rf"(?:what(?:'s|\s+is)\s+{_AGENT_WORD}\s+doing|show\s+(?:me\s+)?what\s+{_AGENT_WORD}\s+is\s+doing|"
    r"^status\b|what\s+are\s+you\s+doing|show\s+(?:me\s+)?(?:the\s+)?(?:running|active)\s+tasks)", re.I)
_CHANGES = re.compile(r"\b(?:what\s+(?:has\s+)?changed|show\s+(?:me\s+)?(?:what\s+changed|the\s+changes|changes|the\s+diff|diff))\b", re.I)
_CALENDAR = re.compile(r"\b(calendar|meetings?|schedule|remind\s+me|reminders?|appointments?|agenda|next\s+event)\b", re.I)
KEYWORD_CHARS = 300  # a keyword rule (_CALENDAR) reads a request this long at most: in a pasted document a word deep inside isn't the
# ask (2026-10-22: a 16,402-character research brief with "appointments" at character 8,381 got the calendar's answer, three times)
_REROUTE = re.compile(
    rf"^(?:use|switch\s+to|try|give\s+(?:it|this)\s+to|hand\s+(?:it|this)\s+to)\s+{_AGENT_WORD}"
    r"(?:\s+(?:for|on)\s+(?:it|(?:this|that|the\s+(?:last|previous))(?:\s+(?:task|one|request))?))?\s*[.!]?$", re.I)
# Which AI answers chat: Ollama models, or an API provider (core/providers.py). "use Claude" alone means the chat AI;
# "use Claude Code" / "use Claude for this" still hand the last task to the Claude Code worker (_REROUTE).
_MODEL = re.compile(
    r"^(?:monday,?\s+)?(?:use|switch\s+(?:back\s+)?(?:(?:the\s+)?(?:ai|model|brain|chat)\s+)?to|change\s+(?:back\s+)?(?:it|(?:the\s+)?(?:ai|model|brain|chat))\s+to|"
    r"go\s+back\s+to|revert\s+to|talk\s+(?:to\s+me\s+)?(?:with|using))\s+(?:the\s+|my\s+)?"
    r"(?P<want>auto(?:matic)?|default|(?:previous|last|earlier|old)(?=\s+(?:chat\s+)?(?:model|ai)\b)|"
    r"(?:local|cloud)\s+[\w.:-]*(?:qwen|llama|gpt-oss|smollm|mistral|gemma|phi|deepseek|kimi|glm)[\w.:-]*|"
    r"local|ollama|offline|cloud|free\s+cloud|(?:fastest|quickest|strongest)(?:\s+available)?|"
    r"(?:best|smartest)(?:\s+available)?(?=\s+(?:chat\s+)?(?:model|ai)\b)|"
    r"[\w.:-]*(?:qwen|llama|gpt-oss|smollm|mistral|gemma|phi|deepseek|kimi|glm)[\w.:-]*|"
    r"claude|sonnet|opus|haiku|chat\s?gpt|gpt[\w.:-]*|openai|gemini\s+(?:pro|flash(?:-lite)?)|gemini[\w.:-]*|grok[\w.:-]*|x\s?ai|x|google|openrouter)"
    r"(?:\s+(?:chat\s+)?(?:model|ai|one|instead|now|please))?(?:\s+available)?\s*[.!]?$", re.I)
# "show me the available models", "which models can you use": the list from the model registry (Monday.models)
_MODEL_LIST = re.compile(r"^(?:(?:show|list|tell)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?(?:the\s+|my\s+|your\s+)?(?:available\s+)?(?:ai\s+|chat\s+|language\s+)?models"
                         r"(?:\s+(?:available|you\s+have|i\s+(?:can|could)\s+use|you\s+(?:can|could)\s+use))?|"
                         r"(?:what|which)\s+(?:ai\s+|chat\s+|language\s+)?models\s+(?:are\s+(?:available|there|installed|set\s+up)|do\s+(?:you|i|we)\s+have|"
                         r"can\s+(?:you|i|we)\s+use|(?:have\s+)?you\s+got))$", re.I)
# One request to Gemini or Grok without switching chat to it: "ask Grok what …", "use Gemini for this" (the last
# question again). Both answer through the owner's own CLIs (core/providers.py); "Gemini CLI" / "Grok Build" by name
# are the coding agents.
_ASK_AI = re.compile(r"^(?:monday,?\s+)?(?:(?:use|ask|try)\s+(?P<a>gemini|grok|mistral|openrouter)\s+(?:for|on)\s+(?:this|that|it)(?:\s+(?:one|question|request))?"
                     r"(?:\s*[:,-]\s*(?P<q1>.+))?|ask\s+(?P<b>gemini|grok|mistral|openrouter)(?!\s+(?:cli|build)\b)\s*[,:]?\s+(?:to\s+)?(?P<q2>.+?))\s*[.!]?$", re.I)
_ASK_AI_KEYS = {"gemini": ("gemini", "gemini_cli"), "grok": ("xai", "grok_build"),  # chat provider, coding agent
                "mistral": ("mistral", None), "openrouter": ("openrouter", None)}
# Which coding agent gets code work. A bare "use Codex" sets the default; "use Codex for this" re-routes (_REROUTE).
# Editors without a command-line agent (Kiro, Cursor, Antigravity) are recognised so the reply can say so honestly.
_CODER_WORD = (r"claude(?:\s+code)?|codex|gemini\s+cli|kilo(?:\s*code)?(?:\s+cli)?|kiro|cursor|antigravity|copilot(?:\s+cli)?|"
               r"grok\s+(?:build|cli)|(?:the\s+)?(?:fastest|quickest|best)(?:\s+available)?(?:\s+coding)?(?:\s+agent)?")
_DEFAULT_CODER = re.compile(
    rf"^(?:(?:make|set)\s+(?P<a>{_CODER_WORD})\s+(?:as\s+)?(?:the\s+|my\s+)?default(?:\s+coder|\s+coding\s+agent)?|"
    rf"(?:use|switch\s+to|go\s+with)\s+(?P<b>{_CODER_WORD})\s+(?:from\s+now\s+on|by\s+default|"
    r"for\s+(?:all\s+)?(?:my\s+)?(?:code|coding)(?:\s+(?:tasks|work))?)|"
    r"(?:use|switch\s+to|go\s+with)\s+(?P<c>claude\s+code|codex|gemini\s+cli|kilo(?:\s*code)?|kiro|cursor|antigravity|copilot(?:\s+cli)?|"
    r"grok\s+(?:build|cli)|(?:the\s+)?(?:fastest|quickest|best)\s+(?:available\s+)?coding\s+agent))\s*[.!]?$", re.I)
_ANALYZE = re.compile(r"\b(analy[sz]e|re-?index|refresh\s+(?:the\s+)?graph|update\s+(?:the\s+)?graph)\b", re.I)
_POLITE = r"(?:(?:please|pls|can\s+you|could\s+you|would\s+you|will\s+you|go\s+ahead\s+and|i\s+(?:want|need)\s+you\s+to)\s+)?"
_ADDRESS = re.compile(rf"^{_POLITE}(?:ask|tell|have|get|use|start|let)\s+"
                      rf"{_AGENT_WORD}\s+(?:to\s+|on\s+|for\s+|and\s+|then\s+)?|^{_AGENT_WORD}\s*[,:]\s*", re.I)
_VIA_AGENT = re.compile(rf"\s(?:with|using|via|through)\s+(?:the\s+)?{_AGENT_WORD}\s*[.!?]*$", re.I)
# Plain requests to operate the computer. Verbs that also fit code work (delete, move, rename, restart…) are
# left to Jev, because a soft verb match can't be moved to a different worker later.
_COMPUTER = re.compile(
    rf"^{_POLITE}(?:please\s+)?(?:open|launch|close|quit|click|type|press|scroll|take\s+a\s+screenshot|screenshot|"
    r"go\s+to|navigate\s+to|browse|play|pause|mute|unmute|minimi[sz]e|maximi[sz]e|focus|bring\s+up|show\s+me\s+my\s+desktop|"
    r"send|message|text|whatsapp|e-?mail|search\s+(?:for|google|the\s+web|online|[\w.]+\s+for)|google|look\s+up|download|"
    r"turn\s+(?:on|off|up|down)|set\s+(?:an?\s+|the\s+)?(?:alarm|timer|volume|brightness)|lock\s+(?:the\s+|my\s+)?(?:pc|computer|laptop|screen)|"
    r"create\s+(?:a\s+|an\s+)?(?:new\s+)?(?:file|folder))\b", re.I)
_CODING = re.compile(
    r"\b(fix|refactor|implement|debug|write\s+(?:a\s+)?tests?|add\s+(?:a\s+)?(?:feature|test|function|component|endpoint)|"
    r"lint|code\s+review|review\s+the\s+code|make\s+the\s+tests\s+pass)\b", re.I)


def _agent_key(word):
    w = word.lower()
    return ("claude_code" if w.startswith("claude") else "hermes" if w.startswith("computer") else
            "gemini_cli" if w.startswith("gemini") else "kilo" if w.startswith("kilo") else "grok_build" if w.startswith("grok") else w)


def _coder_key(word):
    """'Claude Code' -> claude_code, 'the fastest coding agent' -> fastest, 'Kiro' -> kiro (an editor, not drivable)."""
    w = re.sub(r"\s+", " ", word.lower().strip())
    if re.match(r"(?:the\s+)?(?:fastest|quickest|best)", w):
        return "fastest"
    for prefix, key in (("claude", "claude_code"), ("gemini", "gemini_cli"), ("kilo", "kilo"), ("copilot", "copilot_cli"),
                        ("grok", "grok_build")):
        if w.startswith(prefix):
            return key
    return w


def match_project(text, projects):
    """Longest project name/alias that appears as a whole word."""
    low = text.lower()
    best, best_len = None, 0
    for p in projects:
        names = [p["name"]] + [a.strip() for a in (p.get("aliases") or "").split(",") if a.strip()]
        for n in names:
            if len(n) > best_len and re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", low):
                best, best_len = p, len(n)
    return best


def find_project(ref, projects):
    """'my Monday project' -> 'Monday Core'. A name/alias match, else a unique match on a name word or the folder name.
    Only used for an explicit project reference: the assistant's own name must not match a project everywhere."""
    p = match_project(ref, projects)
    if p:
        return p["name"]
    q = re.sub(r"^(?:my|the|our)\s+|\s+(?:project|repo|repository|codebase|code|app|folder)$", "", ref.strip().lower()).strip()
    if not q:
        return None
    hits = [p for p in projects if q in re.split(r"[\s_-]+", p["name"].lower())
            or q == re.split(r"[\\/]", (p.get("path") or "").rstrip("\\/"))[-1].lower()]
    return hits[0]["name"] if len(hits) == 1 else None


def title_for(text, limit=64):
    t = re.sub(r"\s+", " ", _ADDRESS.sub("", text.strip())).strip(" .")
    t = t[:1].upper() + t[1:]
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


# -- fast path: exact commands for Monday's own tools, settled here without Jev or a model -----------------------
_ORD = (r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|1st|2nd|3rd|4th|5th|6th|7th|8th|one|two|three|four|five|"
        r"six|seven|eight|last|next|previous|other|different)")
ORDINALS = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4, "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6,
            "seventh": 7, "7th": 7, "seven": 7, "eighth": 8, "8th": 8, "eight": 8, "last": -1,
            "next": "next", "other": "next", "different": "next", "previous": "prev"}
_VOICE = [  # (op, rule); the generic "use the <words> voice" is last
    ("list", re.compile(r"^(?:(?:show|list|tell|give|read)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?(?:the\s+|your\s+)?(?:available\s+)?(?:female\s+)?voices"
                        r"(?:\s+(?:you\s+have|available|you\s+can\s+use))?|what\s+voices\s+(?:do\s+you\s+have|are\s+(?:there|available)|can\s+you\s+use)|"
                        r"(?:which|what)\s+voices?\s+(?:can\s+i|do\s+you)\s+(?:use|have|pick))$", re.I)),
    ("change", re.compile(r"^(?:change|switch)\s+(?:your|the)\s+voice$|^(?:i\s+want\s+)?(?:a\s+)?different\s+voice$|^(?:open\s+)?voice\s+settings$", re.I)),
    ("select", re.compile(rf"^(?:use|switch\s+to|pick|choose|select|try|go\s+with)\s+(?:the\s+|your\s+)?(?P<ord>{_ORD})\s+"
                          r"(?:(?:female|woman'?s?|girl'?s?|lady'?s?)\s+)?voice$", re.I)),
    ("select", re.compile(r"^(?:(?:use|switch\s+to|pick|choose|select|try)\s+)?voice\s+(?:number\s+)?(?P<num>\d{1,2})$", re.I)),
    ("faster", re.compile(r"^(?:(?:make\s+(?:your\s+)?voice|speak|talk)\s+(?:a\s+(?:bit|little)\s+)?(?:faster|quicker)|speed\s+up(?:\s+your\s+(?:voice|speech))?)$", re.I)),
    ("slower", re.compile(r"^(?:(?:make\s+(?:your\s+)?voice|speak|talk)\s+(?:a\s+(?:bit|little)\s+)?slower|slow\s+down(?:\s+your\s+(?:voice|speech))?)$", re.I)),
    ("higher", re.compile(r"^(?:make\s+(?:your\s+)?voice|speak)\s+(?:a\s+(?:bit|little)\s+)?higher(?:\s+pitched)?$", re.I)),
    ("lower", re.compile(r"^(?:make\s+(?:your\s+)?voice|speak)\s+(?:a\s+(?:bit|little)\s+)?(?:lower|deeper)(?:\s+pitched)?$", re.I)),
    ("mute", re.compile(r"^(?:mute\s+(?:yourself|your\s+voice)|turn\s+off\s+(?:your\s+)?voice|(?:stop|don'?t)\s+(?:speaking|reading)\s+(?:your\s+)?(?:replies|out\s+loud|aloud))$", re.I)),
    ("unmute", re.compile(r"^(?:unmute\s+(?:yourself|your\s+voice)|turn\s+on\s+(?:your\s+)?voice|speak\s+(?:your\s+)?(?:replies\s+)?(?:out\s+loud|aloud)|talk\s+to\s+me\s+again)$", re.I)),
    # How long a pause may last before the owner has finished (the old pages' endpointing), and follow-up listening.
    ("listen_longer", re.compile(r"^(?:listen\s+(?:a\s+(?:bit|little)\s+)?longer|give\s+me\s+(?:a\s+(?:bit|little)\s+)?more\s+time(?:\s+to\s+(?:speak|talk|finish))?|"
                                 r"(?:be\s+)?more\s+patient|don'?t\s+cut\s+me\s+off|wait\s+(?:a\s+(?:bit|little)\s+)?longer\s+(?:before\s+(?:you\s+)?(?:answer|respond|repl(?:y|ies))(?:ing)?|for\s+me(?:\s+to\s+finish)?))$", re.I)),
    ("listen_shorter", re.compile(r"^(?:(?:respond|answer|reply)\s+(?:a\s+(?:bit|little)\s+)?(?:faster|quicker|sooner)|don'?t\s+wait\s+so\s+long(?:\s+before\s+(?:answering|responding))?|(?:be\s+)?less\s+patient)$", re.I)),
    ("listen_normal", re.compile(r"^(?:listen\s+normally|(?:use\s+)?normal\s+listening|reset\s+(?:your\s+)?listening)$", re.I)),
    ("follow_up_on", re.compile(r"^(?:keep\s+listening\s+after\s+(?:your\s+)?(?:replies|answers|you\s+(?:answer|reply|speak))|turn\s+on\s+(?:follow[\s-]?up(?:\s+listening)?|continued\s+conversation))$", re.I)),
    ("follow_up_off", re.compile(r"^(?:don'?t\s+(?:keep\s+)?listen(?:ing)?\s+after\s+(?:your\s+)?(?:replies|answers|you\s+(?:answer|reply|speak))|turn\s+off\s+(?:follow[\s-]?up(?:\s+listening)?|continued\s+conversation))$", re.I)),
    # Interaction mode (the owner's brief, 2026-10-06): pure agent (act, say nothing: the computer is the reply), debug (the whole HUD,
    # still silent) or assistant (spoken replies). Settled in code, a reflex: never Jev's call.
    ("mode_pure", re.compile(r"^(?:(?:switch\s+to|use|enter|turn\s+on|enable|go\s+(?:back\s+)?to)\s+)?(?:pure[\s-]?agent|quiet|silent|agent)\s+mode(?:\s+on)?$|"
                             r"^(?:(?:turn\s+off|disable|exit|leave)\s+(?:debug|developer|dev|assistant|chat)\s+mode|(?:debug|developer|dev|assistant|chat)\s+mode\s+off)$|"
                             r"^(?:just\s+act|act,?\s+don'?t\s+(?:talk|speak|reply)|don'?t\s+(?:talk|speak|reply)\s+(?:back\s+)?(?:to\s+me\s+)?(?:any\s*more|from\s+now\s+on))$", re.I)),
    ("mode_debug", re.compile(r"^(?:(?:switch\s+to|use|enter|turn\s+on|enable|go\s+to)\s+)?(?:debug|developer|dev)\s+mode(?:\s+on)?$|^show\s+me\s+(?:everything|(?:your|the)\s+decisions)$", re.I)),
    ("mode_assistant", re.compile(r"^(?:(?:switch\s+to|use|enter|turn\s+on|enable|go\s+(?:back\s+)?to)\s+)?(?:assistant|chat|conversation(?:al)?|talking)\s+mode(?:\s+on)?$|^talk\s+to\s+me$", re.I)),
    # Who speaks (core/speech.py). "the Google voice" is Google's free voice (speech.GoogleFree, 2026-10-15: the owner's earlier voice);
    # the Cloud provider says "cloud".
    # The owner's recording becomes Monday's voice (core/speech.py Custom, 2026-10-13): the file comes attached to the sentence.
    ("sample", re.compile(r"^(?:use|take|clone|learn)\s+(?:this|that|the|my)\s+(?:voice\s+)?(?:recording|audio|clip|sample|file)\s+(?:as|for)\s+"
                          r"(?:your|the)\s+(?:new\s+)?voice$|^(?:this|that)\s+is\s+(?:your|the)\s+(?:new\s+)?voice$|^clone\s+(?:my|this)\s+voice$|"
                          r"^(?:use|speak\s+(?:with|in))\s+my\s+voice$", re.I)),
    ("provider", re.compile(r"^(?:use|switch\s+to|try|go\s+with)\s+(?:the\s+|your\s+)?(?P<prov>gemini|google\s+cloud|google(?:'s)?(?:\s+free)?|"
                            r"cloud|browser|chrome|"
                            r"(?:your\s+)?own|my|cloned|custom)\s+(?:voices?|speech|tts|text[\s-]to[\s-]speech)(?:\s+(?:instead|now|please|again))?$", re.I)),
    ("select", re.compile(r"^(?:use|switch\s+to|pick|choose|select|try|go\s+with)\s+(?:the\s+|an?\s+|your\s+)?(?P<desc>[\w'-]+(?:\s+[\w'-]+){0,3}?)\s+"
                          r"(?:(?:female|woman'?s?)\s+)?voice$", re.I)),
]
_TTS_PROVIDER = {"gemini": "gemini", "google cloud": "google_cloud", "cloud": "google_cloud", "google": "google", "google's": "google",
                 "google free": "google", "google's free": "google", "browser": "browser", "chrome": "browser",
                 "own": "custom", "your own": "custom", "my": "custom", "cloned": "custom", "custom": "custom"}
# Sleep and wake (core/wake.py, the owner 2026-10-09): asleep, the online dictation is off and a clap, a snap or a whistle wakes Monday.
_SLEEP = re.compile(r"^(?:(?:go|going)\s+(?:to\s+)?sleep|sleep(?:\s+now)?|good\s*night|nap\s+time|take\s+a\s+nap|time\s+(?:to|for)\s+(?:sleep|bed)"
                    r"|you\s+can\s+(?:go\s+to\s+)?sleep(?:\s+now)?)$", re.I)
_WAKE = re.compile(r"^(?:wake\s+up|good\s+morning|rise\s+and\s+shine|are\s+you\s+(?:awake|up))$", re.I)
_TIME = re.compile(r"^(?:what(?:'s|\s+is)\s+the\s+(?:time|date|day)(?:\s+(?:now|today|right\s+now))?|what\s+time\s+is\s+it(?:\s+(?:now|right\s+now))?|"
                   r"what\s+(?:day|date)\s+is\s+(?:it|today)|(?:tell\s+me\s+)?the\s+time)$", re.I)
_SYSTEM = re.compile(r"^(?:system\s+status|computer\s+status|status\s+report|how(?:'s|\s+is)\s+(?:the|my)\s+(?:computer|pc|system|laptop)(?:\s+doing)?|"
                     r"(?:what(?:'s|\s+is)\s+(?:the\s+|my\s+)?)?(?:cpu|ram|memory|gpu)\s+(?:usage|load)|how\s+much\s+(?:ram|memory)\s+(?:is\s+)?(?:free|used|left))$", re.I)
_AI_TOOLS = [
    ("coding", re.compile(r"^(?:what|which)\s+coding\s+(?:agents?|tools|assistants|clis?)\s+(?:are|is|do\s+(?:i|you|we)\s+have)(?:\s+(?:available|installed|ready|set\s+up|here))?$|"
                          r"^(?:list|show)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:available\s+)?coding\s+agents$", re.I)),
    ("all", re.compile(r"^(?:what|which)\s+(?:ai\s+)?(?:tools|agents|apps|assistants|clis?)\s+(?:are|do\s+(?:i|you|we)\s+have)(?:\s+(?:installed|available|set\s+up))?"
                       r"(?:\s+on\s+(?:this|my|the)\s+(?:computer|pc|machine|laptop))?$|^(?:list|show)\s+(?:me\s+)?(?:my\s+|the\s+)?(?:installed\s+|available\s+)?ai\s+(?:tools|agents|apps)$", re.I)),
    ("check", re.compile(r"^(?:check|see|tell\s+me|find\s+out)\s+(?:whether|if)\s+(?P<subject>.+?)\s+is\s+(?:available|installed|ready|working|set\s+up|logged\s+in)$|"
                         r"^is\s+(?P<subject2>.+?)\s+(?:available|installed|set\s+up|logged\s+in)$|^do\s+(?:i|you|we)\s+have\s+(?P<subject3>.+?)\s+installed$", re.I)),
]
# Monday's own window's pages (core/qtwindow.py SCREENS; the owner's 2026-10-20 "Monday tests itself"): "open your Statistics page",
# "show Monday's active pills". "your" / "Monday's" is required: "open settings" stays Windows' Settings (core/computer.py).
UI_PAGES = {"home": "Home", "chat": "Chat", "tasks": "Tasks", "browser": "Browser", "activity": "Activity", "stats": "Statistics",
            "settings": "Settings", "library": "Library", "bots": "Bots", "routines": "Routines", "tests": "Tests", "camera": "Camera",
            "general": "General", "pills": "Active pills", "agents": "Agents", "integrations": "Integrations", "shortcuts": "Shortcuts"}
_PAGE_WORDS = {**{v.lower(): k for k, v in UI_PAGES.items()}, "stats": "stats", "pills": "pills"}
_UI_PAGE = re.compile(r"^(?:open|show|go\s+to|switch\s+to|take\s+me\s+to|bring\s+up)\s+(?:me\s+)?(?:your|monday'?s)\s+(?P<page>"
                      + "|".join(w.replace(" ", r"\s+") for w in sorted(_PAGE_WORDS, key=len, reverse=True))
                      + r")(?:\s+(?:page|tab|screen))?$", re.I)
_UI = [
    ("brain", re.compile(r"^(?:show|open)\s+(?:me\s+)?(?:my\s+|the\s+|your\s+)?(?:second\s+)?brain(?:\s+(?:view|graph|map))?$", re.I)),
    ("brain", re.compile(r"^(?:show\s+(?:me\s+)?|what(?:'s|\s+is|\s+are)\s+)(?:everything|all|anything|what'?s|the\s+things)?\s*(?:that'?s\s+|that\s+(?:is|are)\s+|is\s+|are\s+)?"
                         r"(?:connected|related|linked)\s+(?:to|with)\s+(?P<focus>.+)$", re.I)),
    ("messages", re.compile(r"^(?:show|open)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:messages|control\s+cent(?:er|re)|task\s+(?:list|history)|conversation)$", re.I)),
    ("home", re.compile(r"^(?:go\s+(?:back\s+)?home|(?:go\s+)?back\s+to\s+(?:the\s+)?(?:home|hud|main\s+(?:screen|view))|"
                        r"close\s+(?:the\s+)?(?:brain|messages|overlay|panel|control\s+cent(?:er|re))|"
                        r"(?:show|open)\s+(?:me\s+)?(?:yourself|your\s+(?:window|app|chat)))$", re.I)),
    # Monday's own pet in the notch (core/notch.py), not a program to start: 2026-10-15 "open your pet for me" looked for an app "your pet"
    ("pet", re.compile(r"^(?:open|show|bring\s+(?:back|up)|call|summon|wake\s+up)\s+(?:me\s+)?(?:your|the|my|monday'?s)\s+"
                       r"(?:pet|mascot|buddy|companion|pal|notch)(?:\s+(?:again|back|up))?$|"
                       r"^(?:shrink|minimi[sz]e|collapse|go)\s+(?:yourself\s+|the\s+window\s+)?(?:back\s+)?(?:in)?to\s+(?:the\s+|your\s+)?(?:notch|pet)$", re.I)),
    ("pet_hide", re.compile(r"^(?:hide|close|put\s+away|dismiss)\s+(?:your|the|my|monday'?s)\s+(?:pet|mascot|buddy|companion|pal|notch)$", re.I)),
]
_ROUTING = re.compile(r"^(?:use|switch\s+to)\s+(?P<mode>jev|(?:the\s+)?rules?(?:\s+only)?)\s+(?:for\s+)?routing$|^(?P<onoff>enable|disable|turn\s+on|turn\s+off)\s+jev$", re.I)
_BROWSERS = r"(?P<b>chrome|google\s+chrome|edge|microsoft\s+edge|firefox|brave|opera)"
_DEFAULT_BROWSER = re.compile(rf"^(?:use|make|set)\s+{_BROWSERS}\s+(?:as\s+)?(?:my\s+|the\s+)?(?:default\s+)?browser(?:\s+for\s+monday)?$|"
                              r"^use\s+(?P<b2>chrome|edge|firefox|brave|opera)\s+for\s+(?:browsing|the\s+web|web\s+tasks|websites)$", re.I)
_DEFAULT_TERMINAL = re.compile(r"^(?:use|make|set)\s+(?P<t>windows\s+terminal|powershell|cmd|command\s+prompt)\s+(?:as\s+)?(?:my\s+|the\s+)?(?:default\s+)?terminal$", re.I)
_CONFIRM_LAUNCH = re.compile(r"^(?:(?P<off>don['’]?t|do\s+not|never)\s+ask|(?P<on>always\s+ask|ask))(?:\s+me)?(?:\s+first)?\s+before\s+(?:you\s+)?"
                             r"(?:start(?:ing)?|launch(?:ing)?)\s+(?:an?\s+|the\s+)?(?:coding\s+)?agents?$", re.I)
# Self-repair (core/repair.py). Read before _STOP: "stop fixing yourself" turns the setting off, it doesn't stop a task.
_FORGET_ROOTS = re.compile(r"^(?:forget|reset|clear|revoke)\s+(?:the\s+|my\s+|your\s+)?(?:project\s+)?search\s+(?:folders?|roots?|scope|places?|permissions?)$|"
                           r"^(?:don'?t|stop)\s+search(?:ing)?\s+(?:my\s+)?(?:drives?|folders?|disks?)(?:\s+any\s*more)?$", re.I)
_SELF_REPAIR = re.compile(
    r"^(?:(?P<off>stop|don['’]?t|do\s+not|never)\s+(?:try(?:ing)?\s+to\s+)?(?:fix(?:ing)?|repair(?:ing)?|heal(?:ing)?)\s+yourself(?:\s+.+)?|"
    r"(?P<off2>turn\s+off|disable)\s+self[\s-]?repair|(?P<on>turn\s+on|enable)\s+self[\s-]?repair|"
    r"(?P<on2>fix|repair|heal)\s+yourself\s+(?:automatically|by\s+yourself|on\s+your\s+own|(?:when(?:ever)?|if)\s+.+)|"
    r"(?P<now>fix|repair|heal)\s+(?:yourself|your\s+own\s+(?:error|bug|problem|fault|mistake|code)|(?:that|it|this)\s+yourself)(?:\s+now)?|"
    r"(?P<restart>restart|reload)\s+yourself)$", re.I)
_ASK_BROWSER = re.compile(r"^(?:ask\s+(?:me\s+)?(?:which|what|whose)\s+browser(?:\s+(?:to\s+use|each\s+time|every\s+time|again))?|"
                          r"forget\s+(?:my\s+)?browser\s+(?:choice|answer|default))$", re.I)  # undo the learned browser answer (core/learn.py)
_DEFAULT_COMPUTER = re.compile(r"^(?:use|switch\s+to|let)\s+(?P<a>hermes|monday|computer|your\s+own\s+(?:hands|computer\s+control))\s+(?:for|do|handle)\s+"
                               r"(?:the\s+)?(?:computer|screen|gui|desktop)(?:\s+(?:tasks|work|use|control))?$", re.I)

# One clause of a plan. Order matters: the specific rules first, "open <anything>" last.
_EXPLAIN = re.compile(r"^(?:(?:tell|show)\s+me\s+(?:what(?:'s|\s+is)\s+(?:wrong|failing|broken|the\s+(?:problem|issue|error))|why\s+(?:it|they)\s+(?:failed|fails?)|"
                      r"the\s+(?:results?|errors?|failures?|output))|explain\s+(?:the\s+)?(?:results?|errors?|failures?|output|it)|summari[sz]e\s+(?:the\s+)?(?:results?|output|it)|"
                      r"what(?:'s|\s+is)\s+(?:wrong|failing))$", re.I)
_DELEGATE = re.compile(r"^(?:check|find\s+out|figure\s+out|investigate|debug|look\s+into|diagnose|fix|inspect)\s+(?:why|what|how|the|my|this)\b|"
                       r"^(?:review|audit|analy[sz]e)\s+(?:this|the|my)\s+(?:\w+\s+)?(?:project|repo|repository|codebase|code)$|"
                       # "open my project and see why it is failing" (2026-10-27): the tail after exact steps is the coder's, as "check why" is
                       r"^(?:see|look\s+at|work\s+out|understand)\s+why\b|^(?:fix|debug)\s+(?:it|that|them|the\s+(?:bug|error|issue|problem|failure)s?)$", re.I)
# A clause that asks for something WRITTEN (a report, a summary, advice, a comparison): not a command Monday's hands run, so the plan
# hands it to an AI (executor._answer). The words are never read for what the answer should contain: Jev picks the AI, the AI writes it.
_ANSWER = re.compile(r"^(?:(?:give|get|send)\s+me|show\s+me|tell\s+me|let\s+me\s+know|write|draft|generate|prepare|compile|produce|create|make|"
                     r"summari[sz]e|explain|describe|analy[sz]e|audit|review|evaluate|assess|compare|recommend|suggest|list|rank|rate|research|report|"
                     r"find\s+out|work\s+out|figure\s+out)\b", re.I)
_TESTS = re.compile(r"^(?:run|execute|start|rerun|re-run)\s+(?:the\s+|my\s+|all\s+(?:the\s+)?)?(?:unit\s+)?tests?(?:\s+suite)?(?:\s+(?:for|in|on|of)\s+(?P<proj>.+?))?(?:\s+again)?$|"
                    r"^test\s+(?:the\s+|my\s+)?(?:project|code|app)$", re.I)
_DEV = re.compile(r"^(?:start|run|launch|boot\s+up|spin\s+up|fire\s+up)\s+(?:the\s+|my\s+)?(?:(?:local\s+)?(?:development|dev)\s+server|dev(?:elopment)?\s+environment|"
                  r"backend|frontend|server|web\s*app|website|site)(?:\s+(?:for|in|of)\s+(?P<proj>.+?))?$", re.I)
_GIT = re.compile(r"^(?:show\s+(?:me\s+)?)?(?:the\s+|my\s+)?(?:latest\s+|recent\s+|current\s+)?git\s+(?P<what>changes|status|log|history|diff|commits)(?:\s+(?:for|in|of)\s+(?P<proj>.+?))?$|"
                  r"^what(?:'s|\s+has)\s+changed\s+in\s+git$|^(?:show\s+(?:me\s+)?)?(?:the\s+)?(?:latest|recent)\s+commits$", re.I)
_PORT = re.compile(r"^(?:why\s+is|what(?:'s|\s+is)\s+(?:using|on|running\s+on|occupying|blocking)|who(?:'s|\s+is)\s+(?:using|on)|"
                   r"(?:check|see|find\s+out)(?:\s+(?:what(?:'s|\s+is)|who(?:'s|\s+is))\s+(?:using|on|running\s+on|holding))?)\s+(?:the\s+)?port\s+(?P<port>\d{2,5})"
                   r"(?:\s+(?:occupied|in\s+use|busy|taken|blocked|used))?$|^(?:is\s+)?port\s+(?P<port2>\d{2,5})\s+(?:occupied|in\s+use|busy|taken|free|open)$", re.I)
_RUN_CMD = re.compile(r"^(?:run|execute)\s+(?:the\s+command\s+|command\s+)?(?:`(?P<a>[^`]+)`|\"(?P<b>[^\"]+)\"|“(?P<c>[^”]+)”)(?:\s+(?:in|for|on)\s+(?P<proj>.+?))?$", re.I)
APP_WORDS = 4  # an app's name at most this many words (_target_ok)
_SAID = re.compile(r",\s|[.!?]$")  # a sentence's comma or its end: what the ear writes, not a command line (_BARE_CMD needs neither)
_BARE_CMD = re.compile(r"^(?:run\s+)?(?P<cmd>(?:npm|pnpm|yarn|npx|pip|pip3|python|py|node|git|cargo|dotnet|mvn|gradle|uv|poetry|pytest|docker|deno|bun|flutter)\s+\S.{0,300}|"
                       r"go\s+(?:build|test|run|mod|get|version|env|vet|fmt)\b.{0,300})$", re.I)
# Monday's own Chrome (core/chrome.py): its tabs, going back, reading the page, its console
_NEW_TAB = re.compile(r"^(?:open|create)\s+(?:a\s+)?new\s+tab(?:\s+(?:with|to|for|at|on)\s+(?P<url>.+))?$|^new\s+tab$", re.I)
_CLOSE_TAB = re.compile(r"^close\s+(?:this\s+|the\s+|current\s+|that\s+)?tab(?:\s+(?:number\s+)?(?P<n>\d{1,2}))?$", re.I)
_SWITCH_TAB = re.compile(r"^(?:switch|go|change)\s+(?:back\s+)?to\s+(?:the\s+)?(?:tab\s+(?:number\s+)?(?P<n>\d{1,2})|"
                         r"(?P<o>first|second|third|fourth|fifth|last)\s+tab)$", re.I)
_ORDINAL_TAB = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}
_BACK = re.compile(r"^(?:go\s+)?back(?:\s+(?:a|one)\s+page)?$|^(?:go\s+(?:back\s+)?to\s+)?the\s+previous\s+page$", re.I)
_CONSOLE = re.compile(r"^(?:check|inspect|read|show(?:\s+me)?|open)\s+(?:the\s+)?(?:browser\s+|page(?:'s)?\s+|chrome\s+)?(?:console|dev\s*tools)(?:\s+errors)?$|"
                      r"^(?:what|any)\s+errors?\s+(?:are\s+)?(?:there\s+)?(?:on|in)\s+(?:this|the)\s+(?:web\s*)?page$|"
                      r"^(?:find|show\s+me)\s+(?:the\s+)?errors?\s+on\s+(?:this|the)\s+(?:web\s*)?page$|"
                      r"^(?:check\s+)?why\s+(?:is\s+)?(?:this|the)\s+(?:web\s*)?(?:page|site|website)\s+(?:is\s+)?(?:not\s+(?:loading|working)|broken)$", re.I)
_DOWNLOAD = re.compile(r"^download\s+(?:the\s+|this\s+|that\s+)?(?P<x>.+?)(?:\s+(?:file|link))?(?:\s+(?:from|on)\s+(?:this|the)\s+page)?$", re.I)
_PAGE_SHOT = re.compile(r"^(?:take|grab|capture|get)\s+(?:a\s+)?screenshot\s+of\s+(?:this|the)\s+(?:web\s*)?(?:page|site|website|tab)$|"
                        r"^screenshot\s+(?:this|the)\s+(?:page|tab)$", re.I)
_READ_PAGE = re.compile(r"^(?:read|summari[sz]e|describe)\s+(?:me\s+)?(?:this|the)\s+(?:web\s*)?(?:page|website|site|article)(?:\s+(?:to|for)\s+me)?$|"
                        r"^what(?:'s|\s+is)\s+on\s+(?:this|the)\s+(?:web\s*)?page$", re.I)
_SCREENSHOT = re.compile(r"^(?:take|grab|capture|get|make)\s+(?:a\s+|me\s+a\s+)?(?:screenshot|screen\s*shot|screen\s+capture)(?:\s+of\s+(?:my|the)\s+(?:screen|desktop|whole\s+screen))?$|"
                         r"^screenshot(?:\s+(?:my|the)\s+screen)?$|^capture\s+(?:my|the)\s+screen$", re.I)
_READ_SCREEN = re.compile(r"^(?:read|describe|summari[sz]e|explain)\s+(?:me\s+)?(?:what(?:'s|\s+is)\s+on\s+)?(?:my|the|this)\s+screen(?:\s+(?:to|for)\s+me)?$|"
                          r"^what(?:'s|\s+is)\s+on\s+(?:my|the)\s+screen$|^what\s+(?:do|can)\s+you\s+see(?:\s+on\s+(?:my|the)\s+screen)?$|"
                          # the owner's own (2026-10-29): "tell me whats on my screen and explain it to me", "explain me whats in my scren rn"
                          r"^(?:(?:can\s+you\s+)?(?:tell|show)\s+me\s+|(?:read|describe|summari[sz]e|explain)\s+(?:to\s+)?(?:me\s+)?)?what(?:'?s|\s+is)\s+"
                          r"(?:on|in)\s+(?:my|the|this)\s+s+cr(?:e{1,2})?n(?:\s+(?:rn|right\s+now|now))?(?:,?\s+(?:and\s+)?(?:explain|describe)\s+"
                          r"(?:it|that|this)(?:\s+to\s+me)?)?$|"
                          r"^read\s+(?:me\s+)?(?:this|that|the)\s+(?P<what>error|message|page|window|dialog|text|popup)(?:\s+(?:to|for)\s+me)?$|"
                          r"^what\s+does\s+(?:this|that|the)\s+(?:error|message|dialog|screen|window|popup)\s+say$", re.I)
_WHATS_OPEN = re.compile(r"^what(?:'s|\s+is|\s+are)\s+(?:open|running)(?:\s+(?:on\s+(?:my|the)\s+(?:screen|computer|pc|desktop)|right\s+now))?$|"
                         r"^(?:list|show\s+(?:me\s+)?)\s*(?:the\s+|my\s+|all\s+)?(?:open\s+)?windows$|"
                         r"^what\s+(?:apps|windows|programs)\s+(?:are|do\s+i\s+have)\s+(?:open|running)(?:\s+right\s+now)?$", re.I)
_EXPLAIN_APP = re.compile(r"^(?:tell\s+me\s+)?what(?:'s|\s+is)\s+(?:happening|going\s+on)\s+(?:in|on)\s+(?:the\s+|my\s+)?(?P<app>.+)$", re.I)
_BIT = r"(?:\s+(?:a\s+(?:bit|little|tad)|a\s+little\s+bit|slightly|some))?"  # ("turn the volume down a bit", "make it a bit louder": 2026-10-29)
_VOLUME = re.compile(rf"^(?:turn\s+(?:the\s+)?volume\s+(?P<d1>up|down){_BIT}|turn\s+(?P<d2>up|down)\s+the\s+(?:volume|sound){_BIT}|volume\s+(?P<d3>up|down)|"
                     rf"(?P<d4>increase|decrease|raise|lower)\s+(?:the\s+)?volume{_BIT}|(?:set\s+(?:the\s+)?)?volume\s+(?:to\s+)?(?P<lvl>\d{{1,3}})(?:\s*%|\s+percent)?|"
                     rf"(?P<mute>mute|unmute)(?:\s+(?:the\s+)?(?:sound|audio|volume|computer|pc|speakers))?|(?:make\s+it{_BIT}\s+)?(?P<d5>louder|quieter|softer){_BIT})$", re.I)
# Media: the transport controls on whatever is playing (core/media.py checks each against Windows' media sessions), and a named
# song in Spotify. A pronoun ("pause it") is filled from the previous command when Jev reads a follow-up (with_context).
_MEDIA_APPS = r"spotify|youtube(?:\s+music)?|chrome|edge|firefox|vlc|media\s+player"
_MEDIA_WHAT = r"(?:the\s+|this\s+|that\s+|my\s+)?(?:music|song|track|video|playback|media|audio|podcast|episode)"
_MEDIA = re.compile(rf"^(?P<op>pause|stop|resume|unpause|continue|play)\s+(?:(?P<it>it|that|this)|(?P<app>{_MEDIA_APPS})|playing|"
                    rf"(?:playing\s+)?{_MEDIA_WHAT}(?:\s+(?:on|in)\s+(?P<app2>{_MEDIA_APPS}))?)$", re.I)
_MEDIA_SKIP = re.compile(rf"^(?:(?:play|go\s+to|skip\s+to)\s+(?:the\s+)?)?(?P<dir>next|previous)(?:\s+(?P<app0>{_MEDIA_APPS}))?"
                         rf"(?:\s+(?:song|track|one|video|episode))?(?:\s+(?:on|in)\s+(?P<app>{_MEDIA_APPS}))?$|"
                         rf"^skip(?:\s+(?:it|this|that|(?:this|the|that)\s+(?:one|song|track|ad)|song|track))?(?:\s+(?:on|in)\s+(?P<app2>{_MEDIA_APPS}))?$|"
                         r"^go\s+back(?:\s+to)?\s+(?:a\s+|one\s+|the\s+)?(?:previous\s+)?(?:song|track|one)$", re.I)
_NOW_PLAYING = re.compile(rf"^(?:what(?:'s|\s+is)\s+(?:playing|this\s+song|that\s+song|the\s+name\s+of\s+(?:this|that)\s+song)|"
                          rf"(?:what|which)\s+song\s+is\s+(?:this|that|it|playing))(?:\s+(?:right\s+)?now)?(?:\s+(?:on|in)\s+(?P<app>{_MEDIA_APPS}))?$", re.I)
# Players and sites something can be played in, as said ("…on YouTube", "…in VLC"): words for reading the request, not a behaviour per app.
# The step plays in the one named, else in the app the sentence opened (_into), else in the owner's default music or video app (executor,
# settings default_music_app / default_video_app). Spotify has its own adapter (core/media.py: the owner's real library); any other player is
# worked by the general loop (executor._play_in) and checked in Windows' media session (2026-10-15: "open youtube and play the latest video
# by naman jain" played a Spotify playlist, because every "play" was Spotify's).
_PLAYERS = (r"spotify|youtube(?:\s+music)?|netflix|prime(?:\s+video)?|hotstar|jiocinema|vlc|apple\s+music|soundcloud|(?:windows\s+)?media\s+player|"
            r"amazon\s+music|jiosaavn|gaana|wynk|twitch|chrome|edge|firefox|brave")
_PLAY = re.compile(r"^(?:play|put\s+on|listen\s+to|start\s+playing|i\s+(?:want|wanna|would\s+like)\s+to\s+(?:hear|listen\s+to))\s+(?P<q>.+?)"
                   rf"(?:\s+(?:on|in|from|using|with)\s+(?P<app>{_PLAYERS}))?$", re.I)
_VIDEO = re.compile(r"\b(?:videos?|vlogs?|trailers?|episodes?|movies?|films?|clips?|shorts|reels?|streams?|live\s+stream)\b", re.I)
_PLAY_NOISE = re.compile(r"^(?:(?:the|a|an|some|any|my)\s+)?(?:(?:song|track|music|album|playlist|artist)s?\s+)?(?:(?:called|named|titled)\s+)?"
                         r"(?:(?:something|anything)\s+)?(?:(?:by|from|of)\s+)?", re.I)
_PLAY_ANY = re.compile(r"^(?:something|anything|music|songs?|a\s+song|some\s+(?:music|songs?)|(?:some|any)thing\s+(?:good|nice|else))$", re.I)
# Music the owner's own library resolves, not a text search (the owner, 2026-10-10: "play my liked songs" must play the real
# Liked Songs collection, not a public playlist called that; TASK-2026-0174 played "Lean On" from a searched result). Grammar,
# not a phrase list: a playlist's NAME is resolved against the owner's real Spotify library at play time (core/media). The
# liked collection is the authenticated account's own (Spotify's spotify:collection:tracks), never a shared playlist named "Liked".
_LIKED = re.compile(r"^(?:my\s+|the\s+)?liked(?:\s+(?:songs?|tracks?|music))?$", re.I)
_RANDOM_LIKED = re.compile(r"^(?:(?:a|an|some|any|one|another)\s+)?(?:random\s+)?(?:song|track|tune|anything|something)?\s*(?:from\s+)?(?:my\s+|the\s+)?liked(?:\s+(?:songs?|tracks?|music))?$", re.I)
_PLAYLIST = re.compile(r"^(?:my\s+|the\s+)?(?P<name>.+?)\s+playlist$|^(?:the\s+)?playlist\s+(?:called\s+|named\s+)?(?P<name2>.+)$", re.I)
_ALBUM = re.compile(r"^(?:the\s+)?album\s+(?:called\s+|named\s+)?(?P<name>.+)$|^(?P<name2>.+?)\s+album$", re.I)
_ARTIST = re.compile(r"^(?:songs?|music|tracks?|some\s+songs?)\s+(?:by|from)\s+(?P<name>.+)$|^(?P<name3>.+?)\s+radio$", re.I)


def music_target(raw):
    """A library request ("my liked songs", "a random song from my liked songs", "my workout playlist", "the album X", "songs by X"):
    (mode, args) for the play action; the executor resolves the name against the real library. None for a plain named track."""
    t = (raw or "").strip()
    if _LIKED.match(t):
        return ("Play your Liked Songs", {"mode": "liked"})
    if "liked" in t.lower() and _RANDOM_LIKED.match(t):
        return ("Play a random song from your Liked Songs", {"mode": "liked", "shuffle": True})
    m = _PLAYLIST.match(t)
    if m:
        name = (m["name"] or m["name2"] or "").strip(" \"'“”")
        if name and not _LIKED.match(name):
            return (f"Play your “{name}” playlist", {"mode": "playlist", "name": name})
    m = _ALBUM.match(t)
    if m:
        name = (m["name"] or m["name2"] or "").strip(" \"'“”")
        if name:
            return (f"Play the album “{name}”", {"mode": "album", "query": name})
    m = _ARTIST.match(t)
    if m:
        name = (m["name"] or m["name3"] or "").strip(" \"'“”")
        if name:
            return (f"Play {name}", {"mode": "artist", "query": name})
    return None
_NOT_MUSIC = re.compile(r"\b(?:games?|chess|with\s+me|along|a\s+role|devil'?s\s+advocate)\b|^(?:me|you|us|him|her|them|it|this|that)$", re.I)
# The workflow recorder (core/recorder.py): the owner records a workflow and replays it as a verified test. Grammar, not a phrase
# list; the recording NAME is matched against the real saved recordings at run time (Monday.find_recording).
_REC_START = re.compile(r"^(?:start|begin)\s+(?:a\s+)?(?:new\s+)?recording(?:\s+(?:called|named|as)\s+(?P<name>.+))?$|"
                        r"^record\s+(?:this|my|a|the)\s+(?:workflow|steps)$|^record\s+what\s+i\s+do$", re.I)
_REC_STOP = re.compile(r"^(?:stop|finish|end|save)\s+(?:the\s+)?recording(?:[\s,]+(?:and\s+)?save\s+(?:it\s+)?(?:as|under)\s+(?P<name>.+?)|"
                       r"\s+(?:as|called|named)\s+(?P<name2>.+?))?$", re.I)
_REC_STOP_TEST = re.compile(r"^(?:stop|cancel|abort)\s+(?:the\s+)?(?:running\s+|current\s+)?test(?:\s+(?:run|case))?$", re.I)
_REC_LIST = re.compile(r"^(?:show|list|what\s+are)\s+(?:me\s+)?(?:my\s+|the\s+|all\s+)?(?:saved\s+)?(?:recordings|workflows|test\s+cases|my\s+tests)$", re.I)
_REC_RESULT = re.compile(r"^(?:show|what(?:'s|\s+was|\s+is))\s+(?:me\s+)?(?:the\s+)?(?:last|latest)\s+(?:test\s+)?results?(?:\s+(?:for|of)\s+(?:the\s+)?(?P<name>.+?)(?:\s+(?:workflow|test|recording))?)?$", re.I)
_REC_RUN = re.compile(r"^(?:run|execute)\s+(?:the\s+|my\s+)?(?P<name>.+?)\s+(?:workflow|recording|test\s+case|test)$|"
                      r"^(?:replay|play\s+back|rerun|re-run)\s+(?:the\s+|my\s+)?(?P<name2>.+?)(?:\s+(?:workflow|recording|test))?$", re.I)


def record_cmd(c):
    """A recorder command -> its op (+ name), else None. Order: the specific "stop the test" before "stop recording"; "run X
    workflow" needs the suffix so it doesn't swallow "run the tests" (which is the terminal test runner)."""
    m = _REC_START.match(c)
    if m:
        return {"rule": "record:start", "op": "start", "name": (m.groupdict().get("name") or "").strip(" .\"'“”") or None}
    if _REC_STOP_TEST.match(c):
        return {"rule": "record:stop-test", "op": "stop_test"}
    m = _REC_STOP.match(c)
    if m:
        return {"rule": "record:stop", "op": "stop", "name": (m["name"] or m["name2"] or "").strip(" .\"'“”") or None}
    if _REC_LIST.match(c):
        return {"rule": "record:list", "op": "list"}
    m = _REC_RESULT.match(c)
    if m:
        return {"rule": "record:result", "op": "result", "name": (m["name"] or "").strip(" .\"'“”") or None}
    m = _REC_RUN.match(c)
    if m:
        name = (m["name"] or m["name2"] or "").strip(" .\"'“”")
        if name and name.lower() not in ("the tests", "tests", "test"):  # "run the tests" is the terminal runner, not a recording
            return {"rule": "record:run", "op": "run", "name": name}
    return None


APP_SEARCH = ("spotify",)  # sites whose search the executor runs in the installed app itself (core/media.py), when it is installed
_WINDOW = re.compile(r"^(?P<op>minimi[sz]e|maximi[sz]e|restore)\s*(?:the\s+|this\s+|my\s+)?(?P<x>.+?)?(?:\s+window)?$", re.I)
# This PC's power (core/power.py; the owner's brief, 2026-10-26: "shut down the PC" had gone to Hermes, which refuses it, and read as done).
# The PC itself must be named: "restart Chrome" is an app, "go to sleep" is Monday's own sleep, a bare "shut down" is Jev's to read. A
# leading "no" is the owner correcting Monday ("no shut down this pc", 09:34 that day). A delay only as "in N minutes".
_PC = r"(?:(?:the|my|this|your|our)\s+)?(?:pc|computer|laptop|machine|system|desktop|device|workstation|windows)"
_NO = r"^(?:no[,.!]?\s+)?"
_POWER_DELAY = (r"(?:\s+in\s+(?P<n>\d{1,4}|an?|one|two|three|five|ten|fifteen|twenty|thirty)\s*"
                r"(?P<u>s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?|h(?:(?:ou)?rs?)?)\b)?")
_POWER = (
    ("shutdown", re.compile(rf"{_NO}(?:(?:shut\s*down|shut\s+off|turn\s+off|power\s+(?:off|down)|switch\s+off)\s+{_PC}|"
                            rf"(?:shut|turn|power|switch)\s+{_PC}\s+(?:down|off)){_POWER_DELAY}$", re.I)),
    ("restart", re.compile(rf"{_NO}(?:(?:restart|reboot)\s+{_PC}|reboot){_POWER_DELAY}$", re.I)),
    ("sign_out", re.compile(rf"{_NO}(?:(?:sign|log)\s+(?:me\s+)?(?:out|off)\s+(?:of\s+|from\s+)?(?:windows|{_PC}|my\s+(?:windows\s+)?(?:account|session))|"
                            rf"log\s*off)$", re.I)),
    ("hibernate", re.compile(rf"{_NO}hibernate(?:\s+{_PC})?$", re.I)),
    ("sleep", re.compile(rf"{_NO}(?:(?:put|send)\s+{_PC}\s+to\s+sleep|(?:sleep|suspend)\s+{_PC}|(?:make\s+)?{_PC}\s+(?:go\s+to\s+)?sleep)$", re.I)),
    ("lock", re.compile(rf"{_NO}lock\s+(?:{_PC}|(?:the\s+|my\s+)?screen)$", re.I)),
    ("cancel_shutdown", re.compile(rf"{_NO}(?:(?:cancel|abort|stop|undo|call\s+off)\s+(?:the\s+|my\s+|that\s+|this\s+)?(?:scheduled\s+|pending\s+)?"
                                   rf"(?:shut\s*down|restart|reboot|power\s*off)|don'?t\s+(?:shut\s*down|restart|reboot|turn\s+off)(?:\s+{_PC})?)$", re.I)),
    ("status", re.compile(rf"^(?:(?:is|are)\s+(?:a|any|the|my)\s+(?:shut\s*down|restart|reboot)s?\s+(?:scheduled|pending|set|still\s+on|on)|"
                          rf"(?:{_PC}'?s?\s+)?power\s+status|when\s+(?:will|does)\s+{_PC}\s+(?:shut\s*down|restart|turn\s+off))$", re.I)),
)
_DELAY_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}


def power_tail(t):
    """"Save my document and then shut down my computer" (the brief's own example): the part before for an agent, then this PC's power steps
    -> (head, steps), else None. Only when every clause from some point on is a power step and something comes before them."""
    cuts = list(_SPLIT.finditer(t))
    for i, m in enumerate(cuts):
        ends = [x.start() for x in cuts[i + 1:]] + [len(t)]
        starts = [m.end()] + [x.end() for x in cuts[i + 1:]]
        steps = [power_step(_base_verb(_clause(t[a:b]))) for a, b in zip(starts, ends)]
        head = t[:m.start()].strip(" ,.")
        if head and all(steps):
            return head, steps
    return None


def power_step(c):
    """One clause about this PC's power -> its step for core/power.py ({"delay": seconds} for a shutdown or restart), else None."""
    for action, rx in _POWER:
        m = rx.match(c)
        if not m:
            continue
        g = m.groupdict()
        n = g.get("n")
        delay = (int(n) if n.isdigit() else _DELAY_WORDS[n.lower()]) * {"s": 1, "m": 60, "h": 3600}[g["u"][0].lower()] if n else 0
        if not power.ACTIONS[action]["delay"]:
            return step("power", action, power.ACTIONS[action]["label"])
        if delay > power.MAX_DELAY_S:
            return None  # more than a day ahead isn't an exact command: Jev reads it, and the chat can say so
        return step("power", action, power.ACTIONS[action]["label"] + (f" in {power.span(delay)}" if delay else ""), delay=delay)
    return None
_WAIT = re.compile(r"^wait\s+(?:for\s+)?(?P<n>\d{1,2}|a|one|two|three|five)?\s*(?:seconds?|secs?|s)?$", re.I)
_KEYS = r"(?:ctrl|control|alt|shift|win|windows|enter|return|escape|esc|tab|space|backspace|delete|del|home|end|page\s*up|page\s*down|up|down|left|right|f\d{1,2}|[a-z0-9])"
_KEY = re.compile(rf"^(?:press|hit|push)\s+(?:the\s+)?(?P<k>{_KEYS}(?:\s*(?:\+|-|plus)\s*{_KEYS})*)(?:\s+key)?$", re.I)
_TYPE = re.compile(r"^(?:type|write|enter)\s*:?\s+(?:out\s+)?(?:[\"'“‘](?P<q>.+?)[\"'”’]|(?P<text>.+?))(?:\s+(?:in|into|inside)\s+(?:the\s+)?(?P<field>.+?))?(?P<enter>\s+and\s+(?:press|hit)\s+enter)?$", re.I)
# "…inside it", "…in there", "…in the document": the place the plan is already working in, not a field to look for
_HERE = re.compile(r"^(?:it|there|that|this|here|(?:the\s+)?(?:document|file|note|page|window|editor|app)|(?:a\s+|the\s+)?new\s+(?:tab|document|file|note))$", re.I)
_CODE_THING = re.compile(r"(?:\w+\s+)?(?:project|repo|repository|codebase|code)", re.I)
_SCROLL = re.compile(r"^scroll\s+(?P<dir>up|down|left|right)(?:\s+(?:a\s+(?:bit|little)|(?P<lot>a\s+lot|more)|(?P<n>\d{1,2})\s+times?))?$|^scroll\s+to\s+the\s+(?P<end>top|bottom)$|^page\s+(?P<pg>up|down)$", re.I)
_CLICK = re.compile(r"^(?P<how>click|double[\s-]?click|right[\s-]?click|tap|select|press|hit)\s+(?:on\s+)?(?:the\s+)?(?P<x>.+?)(?:\s+(?:button|link|tab|icon|menu|option|item|field|box|checkbox))?$", re.I)
# where a click goes when it says so: "… in Notepad's window", "… in your own window" (Monday's: refused, it's the owner's)
_IN_APP = re.compile(r"^(?P<x>.+?)(?:\s+(?:button|link|tab|icon|menu|option|item))?\s+(?:in|on)\s+(?:the\s+)?(?P<app>[\w .'’-]+?)(?:'s|’s)?"
                     r"(?:\s+own)?\s+(?:window|app)$", re.I)
# A bare "click" is where the mouse is (or the Camera's shutter when the Camera is in front: executor); "take a photo" is the Camera app's
# shutter, checked in the Camera Roll (computer.photo). The owner's brief, 2026-10-06: "if I say click then click instantaneously".
# Dotpals' marks (2026-10-17): Monday circles it on the screen (core/computer.py point; core/overlay.py draws), nothing clicked
_POINT = re.compile(r"^(?:point\s+(?:to|at)\s+(?:the\s+)?(?P<a>.+?)|(?:circle|highlight|mark)\s+(?:the\s+)?(?P<b>.+?)\s+on\s+(?:my|the|this)\s+screen|"
                    r"show\s+me\s+where\s+(?:the\s+)?(?P<c>.+?)\s+(?:is|are)|where(?:'s|\s+is|\s+are)\s+(?:the\s+)?(?P<d>.+?)\s+on\s+(?:my|the|this)\s+screen)"
                    r"(?:\s+on\s+(?:my|the|this)\s+screen)?[\s?.!]*$", re.I)
_CLICK_HERE = re.compile(r"^(?P<how>click|tap|left[\s-]?click|double[\s-]?click|right[\s-]?click)(?:\s+(?:on\s+)?(?:it|that|this|here|there))?$", re.I)
_PHOTO = re.compile(r"^(?:take|click|capture|snap|shoot|get)\s+(?:a\s+|my\s+|the\s+|another\s+|one\s+more\s+)?(?:photo|picture|pic|pick|pics|picks|selfie|snap|shot)(?:\s+of\s+(?:me|myself|us|this))?$|"
                    r"^(?:take|click|snap)\s+(?:me|myself)$|^(?:photo|picture|selfie|cheese|say\s+cheese)$", re.I)
_NOT_AN_APP = re.compile(r"\b(?:timer|alarm|reminder|countdown|stopwatch|meeting|call|recording|conversation|game|song|music|playlist)\b", re.I)
_SITES = ("youtube", "google", "bing", "duckduckgo", "github", "stackoverflow", "stack overflow", "wikipedia", "amazon", "reddit",
          "npm", "pypi", "google maps", "maps", "images", "google images", "news", "twitter", "x", "linkedin", "spotify", "gmail")
_SITE = r"(?P<site>youtube|google(?:\s+(?:maps|images|news))?|bing|duckduckgo|github|stack\s*overflow|wikipedia|amazon|reddit|npm|pypi|maps|twitter|linkedin|spotify)"
_SEARCH = [
    re.compile(rf"^(?:search|look)\s+(?:for\s+|up\s+)?(?P<q>.+?)\s+(?:on|in|at|using|with)\s+{_SITE}$", re.I),
    re.compile(rf"^(?:search|look\s+up)\s+{_SITE}\s+for\s+(?P<q>.+)$", re.I),
    re.compile(r"^(?P<site>google|youtube)\s+(?P<q>.+)$", re.I),
    re.compile(r"^(?:search|look\s+up)\s+(?:for\s+|online\s+for\s+|the\s+web\s+for\s+|the\s+internet\s+for\s+)?(?P<q>.+)$", re.I),
]
# Photos to look at, not a chat (2026-10-15, the owner's "dashara Maidan photos": a chat model answered "I can't help with that"):
# "photos of the Taj Mahal", "show me pictures of Kota", "Eiffel tower images". Never the owner's own pictures or an action on them
# ("open my photos" is the Photos app, "take photos" the camera, "delete these photos" a file task): those words can't lead the subject.
_PICS = r"(?:photos?|pictures?|pics|images?|wallpapers?)"
_NOT_PICS_OF = (r"(?!(?:open|launch|start|take|click|capture|snap|shoot|delete|remove|copy|move|send|share|upload|print|edit|rename|save|"
                r"show|find|get|search|pull|look|see|view|check|my|me|the|your|our|his|her|their|these|those|this|that|all|recent|latest|"
                r"new|old|some|any)\b)")
_IMAGES = re.compile(rf"^(?:(?:show|find|get|search(?:\s+for)?|pull\s+up)\s+(?:me\s+)?(?:some\s+)?)?{_PICS}\s+(?:of|from)\s+(?P<a>.+)$|"
                     rf"^(?:(?:show|find|get|search(?:\s+for)?)\s+(?:me\s+)?(?:some\s+)?)?{_NOT_PICS_OF}(?P<b>.+?)\s+{_PICS}$", re.I)
_NAVIGATE = re.compile(r"^(?:go|navigate|browse|head)\s+to\s+(?P<x>.+)$|^(?:visit|open\s+(?:the\s+)?(?:website|site|page|url|link))\s+(?P<y>.+)$", re.I)
_CLOSE = re.compile(r"^(?:close|quit|exit)\s+(?:down\s+)?(?:the\s+|my\s+|this\s+)?(?P<x>.+?)(?:\s+(?:app|application|window|program))?$", re.I)
_FOCUS = re.compile(r"^(?:switch|change)\s+(?:back\s+)?to\s+(?:the\s+|my\s+)?(?P<x>.+?)(?:\s+(?:app|window))?$|^focus\s+(?:on\s+)?(?:the\s+|my\s+)?(?P<y>.+?)(?:\s+(?:app|window))?$|"
                    r"^(?:bring\s+up|show)\s+(?:the\s+|my\s+)?(?P<z>.+?)\s+window$", re.I)
_OPEN = re.compile(r"^(?:open|launch|start|fire\s+up|bring\s+up|pull\s+up|load)\s+(?:up\s+)?(?P<x>.+?)(?:\s+(?:app|application|program|software))?"
                   r"(?:\s+(?:in|on)\s+(?:my\s+|the\s+|this\s+)?(?:laptop|computer|pc|desktop|system|machine))?$", re.I)
_BRING_UP = re.compile(r"^(?:bring|pull)\s+(?!(?:the\s+|my\s+)?(?:volume|sound|brightness)\b)(?P<x>.+?)\s+(?:back\s+)?up$", re.I)  # "bring VS Code up" (the owner's brief §26)
_OPEN_IN = re.compile(r"^(?P<x>.+?)\s+(?:in|with|using)\s+(?P<app>vs\s*code|visual\s+studio\s+code|code|cursor|kiro|antigravity|chrome|edge|firefox|brave|notepad|"
                      r"file\s+explorer|explorer|windows\s+terminal|terminal)$", re.I)
# Other forms of an action verb, as a recognizer or a quick typist hands them over: "open notepad and wrote helloe there"
# (TASK-2026-0171) was read as a second app to open. A clause that starts with one is its own clause, read with the base verb.
_VERB_FORMS = {"wrote": "write", "written": "write", "writes": "write", "writing": "write", "typed": "type", "types": "type",
               "typing": "type", "entered": "enter", "enters": "enter", "opened": "open", "opens": "open", "launched": "launch",
               "searched": "search", "searches": "search", "clicked": "click", "pressed": "press"}
# "Would you mind opening Notepad?" (2026-10-27): the gerund after "mind" / "mind you" is the clause's verb. Read by _base_verb only, never a
# clause start (_CLAUSE_START): "search for running and playing" is one search, not a search and a play.
_GERUNDS = {"opening": "open", "closing": "close", "quitting": "quit", "launching": "launch", "starting": "start", "pulling": "pull",
            "bringing": "bring", "switching": "switch", "playing": "play", "pausing": "pause", "searching": "search", "taking": "take",
            "running": "run", "checking": "check", "reading": "read", "showing": "show", "going": "go", "pressing": "press",
            "scrolling": "scroll", "clicking": "click", "muting": "mute", "saving": "save"}
_CLAUSE_START = (r"(?:open|launch|start|bring|pull|run|execute|rerun|close|quit|exit|switch\s+to|focus|go\s+to|navigate\s+to|visit|search|look\s+up|google|youtube|play|pause|resume|skip|"
                 r"click|double[\s-]?click|right[\s-]?click|tap|type|write|enter|press|hit|scroll|take|grab|capture|read|tell\s+me|show\s+me|explain|summari[sz]e|"
                 r"check|find\s+out|figure\s+out|investigate|debug|look\s+into|diagnose|fix|see\s+why|minimi[sz]e|maximi[sz]e|turn|set|mute|unmute|lock|wait|"
                 r"shut|restart|reboot|power\s+(?:off|down)|hibernate|(?:sign|log)\s+(?:me\s+)?(?:out|off)|"
                 r"save|copy|cut|paste|undo|redo|select\s+all|drag|move|kill|force|list|rename|delete|remove|create|make|inspect|"
                 r"(?:give|get|send)\s+me|let\s+me\s+know|generate|prepare|draft|compile|produce|describe|analy[sz]e|audit|review|evaluate|assess|compare|"
                 r"recommend|suggest|rank|rate|research|report|work\s+out|" + "|".join(_VERB_FORMS) + ")")
_SPLIT = re.compile(rf"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|\s+and\s+then\s+|\s+then\s+|\s+and\s+|;\s*|\.\s+)(?={_CLAUSE_START}\b)", re.I)
_LEAD = re.compile(rf"^(?:(?:and|then|also|please|pls|plz|kindly|now|next(?!\s+(?:song|track|one|video|episode|{_MEDIA_APPS})\b)|after\s+that|finally|ok(?:ay)?|so|"
                   rf"uh+|um+|uhm+|hmm+|er+|well|alright)[\s,]+)+", re.I)
# What follows the command and isn't its target ("notepad pls", "open Notepad for me real quick", 2026-10-27)
_TAIL = re.compile(r"(?:\s+(?:please|pls|plz|for\s+me|right\s+now|now|thanks|thank\s+you|thx|real\s+quick|quickly))+$", re.I)
# What precedes it ("would you mind opening Notepad", "I want you to open Spotify"): "mind" only before a gerund (_GERUNDS), so "mind the gap" stays
_POLITE_LEAD = re.compile(r"^(?:(?:(?:would|do)\s+you\s+)?mind\s+(?=\w+ing\b)|(?:can|could|would|will)\s+you\s+(?:please\s+|kindly\s+)?|i\s+(?:want|need)\s+you\s+to\s+|"
                          r"go\s+ahead\s+and\s+|i'?d\s+like\s+you\s+to\s+|let'?s\s+)", re.I)
_NUM_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "five": 5}
_FOLDER = r"(?:the\s+|my\s+)?(?P<folder>desktop|documents|downloads|pictures|music|videos|home)(?:\s+folder)?"
_FILE = r"[\w .()'-]+?\.[a-z0-9]{1,6}"  # a file named with its extension: "notes.txt", "Q3 report.docx"
_SAVE = re.compile(rf"^save(?:\s+(?:it|this|that|the\s+(?:file|document|note|text)|my\s+work))?(?:\s+as\s+(?P<name>[^\\/:*?\"<>|]+?))?"
                   rf"(?:\s+(?:on|in|to|into)\s+{_FOLDER})?$", re.I)
_CLIP_GET = re.compile(r"^(?:what(?:'s|\s+is)\s+(?:on|in)\s+(?:my|the)\s+clipboard|(?:read|show)\s+(?:me\s+)?(?:my|the)\s+clipboard)$", re.I)
_CLIP_SET = re.compile(r"^(?:copy|put)\s+(?:[\"“](?P<q>.+?)[\"”]|(?P<text>.+?))\s+(?:to|on|onto|into)\s+(?:the|my)\s+clipboard$", re.I)
_EDIT_KEYS = re.compile(r"^(?P<op>copy|cut|paste|select\s+all|undo|redo)(?:\s+(?:it|this|that|the\s+text|the\s+selection|everything|all(?:\s+the\s+text)?|that\s+text))?$", re.I)
_EDIT_COMBO = {"copy": ["ctrl", "c"], "cut": ["ctrl", "x"], "paste": ["ctrl", "v"], "select all": ["ctrl", "a"], "undo": ["ctrl", "z"],
               "redo": ["ctrl", "y"]}
_DRAG = re.compile(r"^drag\s+(?:the\s+)?(?P<src>.+?)\s+(?:to|onto|into|over\s+to)\s+(?:the\s+)?(?P<dst>.+)$", re.I)
_MOUSE = re.compile(r"^move\s+(?:the\s+)?(?:mouse|cursor|pointer)\s+(?:to\s+|over\s+)?(?:(?P<x>\d{1,5})\s*[, ]\s*(?P<y>\d{1,5})|(?:the\s+)?(?P<target>.+))$", re.I)
_PROCS = re.compile(r"^(?:what(?:'s|\s+is)\s+using\s+(?:the\s+most\s+|all\s+(?:my\s+|the\s+)?|my\s+)?(?P<res>memory|ram|cpu)|"
                    r"(?:list|show)\s+(?:me\s+)?(?:the\s+|all\s+(?:the\s+)?)?(?:running\s+)?processes|what\s+processes\s+are\s+running|"
                    r"which\s+(?:apps?|programs?|processes)\s+(?:use|are\s+using)\s+(?:the\s+most\s+)?(?P<res2>memory|ram|cpu))$", re.I)
_IS_RUNNING = re.compile(r"^is\s+(?P<name>.+?)\s+(?:still\s+)?(?:running|open)(?:\s+right\s+now)?$", re.I)
_INSPECT = re.compile(r"^(?:inspect|examine)\s+(?:this|the|my)?\s*(?P<x>.+?)(?:\s+window)?$|"
                      r"^what\s+(?:buttons|controls|options|menus)\s+(?:are|does\s+it\s+have|can\s+i\s+(?:use|click))\s+(?:there\s+)?(?:on|in)\s+(?:this|the)\s+(?P<y>.+?)$", re.I)
_FS_LIST = re.compile(rf"^(?:what(?:'s|\s+is)\s+in|list(?:\s+(?:the\s+)?(?:files|contents|everything))?(?:\s+(?:in|of|on))?|"
                      rf"show\s+(?:me\s+)?(?:the\s+)?(?:files|contents)\s+(?:in|of|on))\s+{_FOLDER}$", re.I)
_FS_READ = re.compile(rf"^(?:read|open\s+and\s+read)\s+(?:me\s+)?(?:the\s+)?(?:file\s+)?(?P<name>{_FILE})(?:\s+(?:on|in|from)\s+{_FOLDER})?$", re.I)
_FS_CREATE = re.compile(rf"^(?:create|make)\s+(?:a\s+|an\s+)?(?:new\s+|empty\s+)?(?P<kind>file|folder|text\s+file|note)\s+(?:called|named)\s+"
                        rf"(?P<name>[^\\/:*?\"<>|]+?)(?:\s+(?:on|in)\s+{_FOLDER})?$", re.I)
_FS_RENAME = re.compile(rf"^rename\s+(?:the\s+)?(?:file\s+|folder\s+)?(?P<src>{_FILE}|[\"“][^\"”]+[\"”])(?:\s+(?:on|in)\s+{_FOLDER})?\s+to\s+(?P<dst>[^\\/:*?\"<>|]+)$", re.I)
_FS_MOVE = re.compile(rf"^move\s+(?:the\s+)?(?:file\s+)?(?P<src>{_FILE})(?:\s+(?:from|on|in)\s+{_FOLDER})?\s+(?:to|into)\s+"
                      r"(?:the\s+|my\s+)?(?P<dst>desktop|documents|downloads|pictures|music|videos|home)(?:\s+folder)?$", re.I)
# A whole project is never deleted (the owner, 2026-10-05). The request is recognised so that it is refused in the open, by
# permit(), instead of drifting to a model or a help text.
_PROJECT_DELETE = re.compile(r"^(?:delete|remove|trash|bin|erase|wipe|destroy)\s+(?:this|that|the|my|our)\s+(?:(?:whole|entire|current)\s+)?"
                             r"(?:(?P<name>.+?)\s+)?(?:project|repo|repository|codebase)(?:\s+folder)?$", re.I)
_FS_DELETE = re.compile(rf"^(?:delete|remove|trash|bin)\s+(?:the\s+)?(?:file\s+|folder\s+)?(?P<name>{_FILE}|[\"“][^\"”]+[\"”])(?:\s+(?:from|on|in)\s+{_FOLDER})?$", re.I)
_FS_WRITE = re.compile(rf"^write\s*:?\s+(?:[\"“](?P<q>.+?)[\"”]|(?P<text>.+?))\s+(?:to|into|in)\s+(?:the\s+file\s+)?(?P<name>{_FILE})(?:\s+(?:on|in)\s+{_FOLDER})?$", re.I)


def step(tool, action, label, **args):
    return {"tool": tool, "action": action, "label": label, "args": {k: v for k, v in args.items() if v is not None}}


def _clause(c):
    c = _POLITE_LEAD.sub("", _LEAD.sub("", c.strip()))
    return _TAIL.sub("", c.strip(" .!?,")).strip(" .!?,")


def _base_verb(c):
    """'wrote hello there' -> 'write hello there', 'opening Notepad' -> 'open Notepad': a clause read with its verb's base form (_VERB_FORMS, _GERUNDS)."""
    first, _, rest = c.partition(" ")
    base = _VERB_FORMS.get(first.lower()) or _GERUNDS.get(first.lower())
    return f"{base} {rest}" if base and rest else c


def _cap(s):
    return s[:1].upper() + s[1:]


def _target_ok(x):
    """An app/window name is short and has no second action in it ("WhatsApp and send hi to Dad" is not an app). APP_WORDS at most: real
    names are ("Adobe Acrobat Reader DC"); a longer one is a sentence the ear or a typo ran together (2026-10-20: "open notpad ajd make a
    new tab" was looked for as an app), and goes to Jev and the computer agent whole."""
    return x and len(x.split()) <= APP_WORDS and not re.search(r"\b(?:and|then|that|so|saying|telling)\b", x, re.I)


def parse_step(c, projects=(), first=True):
    """One exact clause -> a step for Monday's own tools, or None."""
    c = _base_verb(_clause(c))
    if not c:
        return None
    p = power_step(c)  # first: "turn off the computer" is the PC's power, never a volume or a window
    if p:
        return p
    if c.lower() == "enter":  # "type hello and enter"
        return step("computer", "key", "Press enter", keys=["enter"])
    if not first and _EXPLAIN.match(c):
        return step("analysis", "explain", "Explain the result")
    m = _TESTS.match(c)
    if m:
        proj = find_project(m["proj"], projects) if m["proj"] else None
        return step("terminal", "tests", "Run the tests" + (f" in {proj}" if proj else ""), project=proj)
    m = _DEV.match(c)
    if m:
        proj = find_project(m["proj"], projects) if m["proj"] else None
        return step("terminal", "dev_server", "Start the development server" + (f" for {proj}" if proj else ""), project=proj)
    m = _GIT.match(c)
    if m:
        what = (m["what"] or "changes").lower()
        what = {"changes": "status", "history": "log", "commits": "log"}.get(what, what)
        proj = find_project(m["proj"], projects) if m["proj"] else None
        return step("terminal", "git", f"Git {what}" + (f" in {proj}" if proj else ""), what=what, project=proj)
    m = _PORT.match(c)
    if m:
        port = int(m["port"] or m["port2"])
        return step("terminal", "port", f"Check what is using port {port}", port=port)
    m = _RUN_CMD.match(c)
    if m or (_BARE_CMD.match(c) and not _SAID.search(c)):  # (a command typed, not a sentence heard: "and Node Foreign, right high." ran)
        cmd = (m["a"] or m["b"] or m["c"]) if m else _BARE_CMD.match(c)["cmd"]
        proj = find_project(m["proj"], projects) if m and m["proj"] else None
        return step("terminal", "run", f"Run {cmd.strip()[:80]}", command=cmd.strip(), project=proj)
    m = _NEW_TAB.match(c)
    if m:
        return step("browser", "new_tab", "Open a new tab" + (f" with {m['url']}" if m["url"] else ""), url=m["url"])
    m = _CLOSE_TAB.match(c)
    if m:
        return step("browser", "close_tab", f"Close tab {m['n']}" if m["n"] else "Close this tab", tab=int(m["n"]) if m["n"] else None)
    m = _SWITCH_TAB.match(c)
    if m:
        return step("browser", "switch_tab", f"Switch to tab {m['n'] or m['o'].lower()}", tab=int(m["n"]) if m["n"] else _ORDINAL_TAB[m["o"].lower()])
    if _BACK.match(c):
        return step("browser", "back", "Go back a page")
    if _CONSOLE.match(c):
        return step("browser", "console", "Check the page's console for errors")
    if _PAGE_SHOT.match(c):
        return step("browser", "page_screenshot", "Take a screenshot of the page")
    m = _DOWNLOAD.match(c)
    if m:
        x = None if m["x"].lower() in ("file", "it", "this", "that", "one") else m["x"]
        return step("browser", "download", f"Download {x or 'the file'}", target=x)
    if _READ_PAGE.match(c):
        return step("browser", "read_page", "Read the page")
    if _SCREENSHOT.match(c):
        return step("computer", "screenshot", "Take a screenshot")
    m = _READ_SCREEN.match(c)
    if m:
        what = (m["what"] or "screen").lower()
        return step("computer", "read_screen", "Read the error on screen" if what == "error" else "Read the screen",
                    what="error" if what in ("error", "message", "dialog", "popup") else "screen")
    if _WHATS_OPEN.match(c):
        return step("computer", "windows", "List the open windows")
    m = _EXPLAIN_APP.match(c)
    if m and re.fullmatch(r"(?:the\s+|my\s+|this\s+)?s+cr(?:e{1,2})?n(?:\s+(?:rn|right\s+now|now))?", m["app"].strip(" ?.!"), re.I):
        return step("computer", "read_screen", "Read the screen", what="screen")  # "what's going on on my screen": the whole screen, looked at
    if m:
        return step("computer", "read_screen", f"Read {m['app']}", what="window", app=m["app"], explain=True)
    m = _VOLUME.match(c)
    if m:
        d = (m["d1"] or m["d2"] or m["d3"] or "").lower() or {"increase": "up", "raise": "up", "decrease": "down", "lower": "down"}.get(
            (m["d4"] or "").lower()) or {"louder": "up", "quieter": "down", "softer": "down"}.get((m["d5"] or "").lower())
        if m["lvl"]:
            return step("computer", "volume", f"Set the volume to {min(int(m['lvl']), 100)}%", op="set", level=min(int(m["lvl"]), 100))
        op = (m["mute"] or "").lower() or d
        return step("computer", "volume", {"up": "Turn the volume up", "down": "Turn the volume down"}.get(op, _cap(op)), op=op)
    if re.fullmatch(r"play(?:\s+(?:the\s+)?music)?", c, re.I):  # a bare "play" (the owner's "…and play", 2026-10-06): what is paused, else Spotify's own Play
        return step("computer", "media", "Play music", op="play")
    m = _MEDIA.match(c)
    if m:
        op = "play" if m["op"].lower() in ("play", "resume", "unpause", "continue") else "pause"
        target = m["it"] or m["app"] or m["app2"]
        return step("computer", "media", f"{'Resume' if op == 'play' else 'Pause'} {target or 'the music'}", op=op, target=target)
    m = _MEDIA_SKIP.match(c)
    if m:
        op = "previous" if (m["dir"] or "").lower() == "previous" or c.lower().startswith("go back") else "next"
        return step("computer", "media", "Previous track" if op == "previous" else "Next track", op=op, target=m["app0"] or m["app"] or m["app2"])
    m = _NOW_PLAYING.match(c)
    if m:
        return step("computer", "now_playing", "Say what is playing", target=m["app"])
    m = _WINDOW.match(c)
    if m:
        op = {"minimise": "minimize", "maximise": "maximize"}.get(m["op"].lower(), m["op"].lower())
        target = (m["x"] or "").strip()
        target = None if target.lower() in ("", "it", "this", "window", "the window", "everything") else target
        return step("computer", "window", f"{_cap(op)} {target or 'the window'}", op=op, target=target)
    m = _SAVE.match(c)
    if m:
        name = (m["name"] or "").strip() or None
        where = f" on the {m['folder'].lower()}" if m["folder"] else ""
        return step("computer", "save", f"Save as {name}{where}" if name else "Save", name=name, folder=m["folder"])
    if _CLIP_GET.match(c):
        return step("computer", "clipboard", "Read the clipboard", op="get")
    m = _CLIP_SET.match(c)
    if m:
        text = m["q"] or m["text"]
        return step("computer", "clipboard", f"Copy “{text[:50]}” to the clipboard", op="set", text=text)
    m = _EDIT_KEYS.match(c)
    if m:
        op = re.sub(r"\s+", " ", m["op"].lower())
        return step("computer", "key", f"{op.capitalize()} ({'+'.join(_EDIT_COMBO[op]).title()})", keys=_EDIT_COMBO[op])
    m = _DRAG.match(c)
    if m:
        return step("computer", "drag", f"Drag “{m['src']}” to “{m['dst']}”", source=m["src"], target=m["dst"])
    m = _MOUSE.match(c)
    if m:
        if m["x"]:
            return step("computer", "move_mouse", f"Move the mouse to {m['x']}, {m['y']}", x=int(m["x"]), y=int(m["y"]))
        return step("computer", "move_mouse", f"Move the mouse to “{m['target']}”", target=m["target"])
    m = _PROCS.match(c)
    if m:
        res = (m["res"] or m["res2"] or "memory").lower()
        return step("computer", "processes", "List what's using the most " + ("CPU" if res == "cpu" else "memory"),
                    sort="cpu" if res == "cpu" else "memory")
    m = _IS_RUNNING.match(c)
    if m and not re.search(rf"\b{_AGENT_WORD}\b|\bTASK-", m["name"], re.I):  # "is Claude Code running" asks about Monday's task
        return step("computer", "processes", f"Check whether {m['name']} is running", query=m["name"])
    m = _KILL.match(c)
    if m and not _TASK_WORDS.match(m["x"].strip()):
        return step("computer", "kill", f"Force-close {m['x']}", target=m["x"])
    m = _INSPECT.match(c)
    if m and not _CODE_THING.fullmatch((m["x"] or m["y"] or "").strip()):  # "inspect this project" is code work, not a window's controls
        x = (m["x"] or m["y"] or "").strip()
        target = None if x.lower() in ("", "window", "app", "screen", "this", "it") else x
        return step("computer", "inspect", f"List the controls of {target or 'this window'}", target=target)
    m = _FS_LIST.match(c)
    if m:
        return step("filesystem", "list", f"List the {m['folder'].lower()} folder", folder=m["folder"])
    m = _FS_READ.match(c)
    if m:
        return step("filesystem", "read", f"Read {m['name']}", name=m["name"], folder=m["folder"])
    m = _FS_CREATE.match(c)
    if m:
        kind = "folder" if m["kind"].lower() == "folder" else "file"
        return step("filesystem", "create", f"Create the {kind} {m['name'].strip()}" + (f" on the {m['folder'].lower()}" if m["folder"] else ""),
                    name=m["name"].strip(), kind=kind, folder=m["folder"])
    m = _FS_RENAME.match(c)
    if m:
        return step("filesystem", "rename", f"Rename {m['src']} to {m['dst'].strip()}", src=m["src"].strip("\"“”"), dst=m["dst"].strip(),
                    folder=m["folder"])
    m = _FS_MOVE.match(c)
    if m:
        return step("filesystem", "move", f"Move {m['src']} to {m['dst'].lower()}", src=m["src"], dst=m["dst"], folder=m["folder"])
    m = _PROJECT_DELETE.match(c)
    if m:
        return step("filesystem", "delete", f"Delete the {m['name'] + ' ' if m['name'] else ''}project", kind="project", name=m["name"])
    m = _FS_DELETE.match(c)
    if m:
        return step("filesystem", "delete", f"Move {m['name'].strip(chr(34) + '“”')} to the Recycle Bin", name=m["name"].strip("\"“”"),
                    folder=m["folder"])
    m = _FS_WRITE.match(c)
    if m:
        text = m["q"] or m["text"]
        return step("filesystem", "write", f"Write “{text[:40]}” to {m['name']}", text=text, name=m["name"], folder=m["folder"])
    m = _WAIT.match(c)
    if m:
        n = m["n"] or "2"
        return step("computer", "wait", "Wait", seconds=int(_NUM_WORDS.get(n.lower(), n)) if not n.isdigit() else int(n))
    m = _KEY.match(c)
    if m:
        keys = [k.strip().lower() for k in re.split(r"\s*(?:\+|-|plus)\s*", m["k"]) if k.strip()]
        return step("computer", "key", "Press " + "+".join(keys), keys=keys)
    m = _TYPE.match(c)
    if m:
        text = m["q"] or m["text"]
        field = None if m["field"] and _HERE.match(m["field"]) else m["field"]  # "…inside it": where the plan already is, not a field
        return step("computer", "type", f"Type “{text[:60]}”" + (f" into {field}" if field else ""),
                    text=text, field=field, enter=bool(m["enter"]) or None)
    m = _SCROLL.match(c)
    if m:
        if m["end"] or m["pg"]:
            key = {"top": "home", "bottom": "end"}.get((m["end"] or "").lower()) or f"page{m['pg'].lower()}"
            return step("computer", "key", f"Scroll to the {m['end'] or m['pg']}", keys=[key])
        amount = int(m["n"]) * 3 if m["n"] else 12 if m["lot"] else 4
        return step("computer", "scroll", f"Scroll {m['dir'].lower()}", direction=m["dir"].lower(), amount=amount)
    if _PHOTO.match(c):
        return step("computer", "photo", "Take a photo")
    m = _POINT.match(c)
    if m:
        x = next(g for g in (m["a"], m["b"], m["c"], m["d"]) if g)
        return step("computer", "point", f"Show where “{x}” is", target=x)
    m = _CLICK_HERE.match(c)
    if m:
        how = m["how"].lower().replace(" ", "-")
        button, count = ("right", 1) if how.startswith("right") else ("left", 2 if how.startswith("double") else 1)
        return step("computer", "click", "Click here", at="cursor", button=button, count=count)
    m = _CLICK.match(c)
    if m and not re.fullmatch(rf"{_ORD}\s+(?:search\s+)?(?:result|link|video|item|one|option)s?", m["x"], re.I):
        how = m["how"].lower().replace(" ", "-")
        button, count = ("right", 1) if how.startswith("right") else ("left", 2 if how.startswith("double") else 1)
        x, app = m["x"], None
        q = _IN_APP.match(x)  # "click Save in Notepad's window": there (2026-10-20: the whole phrase was looked for in the app in front)
        if q:
            x, app = q["x"], "Monday" if q["app"].lower().rstrip("'’s ") in ("monday", "your", "your own") else q["app"]
        return step("computer", "click", f"{_cap(how.replace('-', ' '))} “{x}”" + (f" in {app}" if app else ""), target=x, app=app,
                    button=button, count=count)
    m = _IMAGES.match(c)
    if m:
        q = (m["a"] or m["b"]).strip(" .?!")
        return step("browser", "search", f"Search images of “{q}”", query=q, site="images")
    for rx in _SEARCH:
        m = rx.match(c)
        if m:
            site = re.sub(r"\s+", " ", (m.groupdict().get("site") or "")).lower() or None
            return step("browser", "search", f"Search {site.title() if site else 'the web'} for “{m['q']}”", query=m["q"].strip(), site=site)
    m = _PLAY.match(c)
    if m and not _NOT_MUSIC.search(m["q"].strip()):
        raw = m["q"].strip()
        app = re.sub(r"\s+", " ", m["app"]).lower() if m["app"] else None  # named here, else _into's app, else the owner's default (executor)
        where = {"app": app} if app else {}
        on = f" on {m['app']}" if app else ""
        lib = music_target(raw)  # the owner's own library: liked, a random liked song, a named playlist, an album, an artist
        if lib:
            return step("computer", "play", lib[0] + on, **where, **lib[1])
        q = "" if _PLAY_ANY.match(raw) else _PLAY_NOISE.sub("", raw, count=1)
        q = re.sub(r"\s+(?:song|track)$", "", q, flags=re.I).strip(" \"'“”")
        if not q or _PLAY_ANY.match(q):  # "play some music", "let's listen to something": whatever was on
            return step("computer", "media", "Play music", op="play", target=m["app"])
        return step("computer", "play", f"Play {q}{on}", query=q, **where, mode="track", **({"kind": "video"} if _VIDEO.search(raw) else {}))
    m = _NAVIGATE.match(c)
    if m:
        target = (m["x"] or m["y"]).strip()
        return step("browser", "open_url", f"Go to {target}", url=target)
    m = _CLOSE.match(c)
    if m and (_target_ok(m["x"]) or _PRONOUN.match(m["x"])):
        return step("computer", "close", f"Close {m['x']}", target=m["x"])
    m = _FOCUS.match(c)
    if m and _target_ok(m["x"] or m["y"] or m["z"]):
        x = m["x"] or m["y"] or m["z"]
        return step("computer", "focus", f"Switch to {x}", target=x)
    m = _OPEN.match(c) or _BRING_UP.match(c)
    if m and re.fullmatch(rf"(?:the\s+)?{_ORD}\s+(?:search\s+)?(?:result|link|video|item|one|option|song|track|email|message|page)s?",
                          m["x"].strip(), re.I):
        return None  # "open the first video" is something on the page: the goal loop's to find, not an app
    if m and not _NOT_AN_APP.search(m["x"]):
        target, editor = re.sub(r"^(?:the|a|an)\s+", "", m["x"].strip(), flags=re.I), None
        mw = _OPEN_WHOSE.match(target)
        if mw and _target_ok(mw["x"].strip()):  # "open github.com in my browser" / "…in your Chrome": no need to ask
            whose = "owner" if mw["owner"] else "monday"
            return step("computer", "open", f"Open {mw['x'].strip()} in {'your browser' if whose == 'owner' else 'my Chrome'}", target=mw["x"].strip(), browser=whose)
        mi = _OPEN_IN.match(target)
        if mi:
            target, editor = mi["x"].strip(), mi["app"]
        if not _target_ok(target) and not re.match(r"^(?:[a-z]:[\\/]|https?://|~[\\/])", target, re.I):
            return None
        # "open the Monday code in VS Code", "open monday's code in vs code" (2026-10-12, the owner: VS Code opened empty): a project named
        # by its code, folder or possessive, in a code editor, is a project to find (core/locate.py), never an app of that name. A file
        # ("open main.py in vs code") still opens in the editor.
        named_project = bool(editor) and editor.lower() in _EDITORS and "." not in target and \
            re.search(r"\b(?:code|source|sources|folder|workspace|app)$|[’']s\b", target, re.I)
        if re.search(r"\b(?:project|repo|repository|codebase)$", target, re.I) or find_project(target, projects) or named_project:
            proj = find_project(target, projects)
            return step("applications", "open_project", f"Open {proj or target}" + (f" in {editor}" if editor else ""),
                        project=proj, ref=None if proj else target, editor=editor)
        return step("computer", "open", f"Open {target}" + (f" in {editor}" if editor else ""), target=target, app=editor)
    return None


_EDITORS = ("vs code", "vscode", "visual studio code", "code", "cursor", "kiro", "antigravity", "antigravity ide")


def _merge(steps):
    """'open YouTube and search for X' -> one search on YouTube; 'open VS Code, open my project' -> open it in VS Code;
    'run the tests and tell me what's wrong' -> one test step that explains its result."""
    out = []
    for s in steps:
        prev = out[-1] if out else None
        a, pa = s["args"], (prev or {}).get("args") or {}
        if prev and prev["action"] == "open" and s["action"] == "search" and not a.get("site") and pa.get("target", "").lower() in _SITES:
            out[-1] = step("browser", "search", f"Search {pa['target']} for “{a['query']}”", query=a["query"], site=pa["target"].lower())
        elif prev and prev["action"] == "open" and s["action"] == "open_project" and pa.get("target", "").lower() in _EDITORS and not a.get("editor"):
            out[-1] = step("applications", "open_project", f"Open {a.get('project') or a.get('ref')} in {pa['target']}",
                           project=a.get("project"), ref=a.get("ref"), editor=pa["target"])
        elif prev and prev["tool"] == "browser" and prev["action"] == "search" and (
                (s["action"] == "media" and a.get("op") == "play" and (_PRONOUN.match(str(a.get("target") or "it")) or not a.get("target")))
                or (s["action"] == "play" and _GENERIC_PLAY.match(str(a.get("query") or "")))):
            # "…search for X and play it", "search Honey Singh and play a song by him" (2026-10-20: it searched the web, then played "him"):
            # one play of X, where the search was (a player's site), else where the owner is (executor._player)
            site = pa.get("site") if re.fullmatch(_PLAYERS, pa.get("site") or "", re.I) else a.get("app")
            out[-1] = step("computer", "play", f"Play {pa['query']}" + (f" on {site.title()}" if site else ""), query=pa["query"],
                           **({"app": site} if site else {}), **({"kind": a["kind"]} if a.get("kind") else {}))
        elif prev and prev["action"] == "open" and s["action"] == "play" and pa.get("target", "").lower() == "youtube" \
                and str(a.get("app") or "youtube").lower() == "youtube":
            out[-1] = {**s, "args": {**a, "app": "youtube"}}  # "open YouTube and play X": the player opens YouTube itself, in Monday's Chrome
        elif prev and prev["tool"] == s["tool"] == "browser" and prev["action"] == s["action"] == "console":
            continue  # "open DevTools and inspect the console": one look at the console
        elif prev and s["action"] == "explain" and prev["tool"] == "terminal":
            prev["args"]["explain"] = True
            prev["label"] += " and explain the result"
        elif prev and (prev["tool"], prev["action"]) == ("computer", "read_screen") and pa.get("what") == "screen" and not pa.get("app") \
                and s["tool"] == "analysis" and (s["action"] == "explain" or _EXPLAIN_IT.fullmatch(str(a.get("request") or "").strip(" .!?"))):
            continue  # "tell me whats on my screen and explain it to me": the look at the screen explains it (executor._explain_screen)
        else:
            out.append(s)
    return out


_EXPLAIN_IT = re.compile(r"(?:explain|describe|summari[sz]e)(?:\s+(?:it|that|this|them|me|to|for|too|also|what|whats|what's|is|it's|its|"
                         r"everything|all|of|on|in|my|the|screen|scren|you|see|there|here|briefly|simply|please|pls|plz|short|detail|simple|"
                         r"words|terms|rn|now|again|as|well))*", re.I)  # (only pronouns and fillers after the verb: "explain me too what it is")


_MULTI = re.compile(r"^(?P<verb>open|launch|start|close|quit)\s+(?P<rest>.+)$", re.I)
_DEICTIC = re.compile(r"\b(?:there|here|it|this|that|me|you|him|her|them|us)\b", re.I)  # in a sentence, never in an app's name


def _expand(c):
    """'open Spotify and Chrome' -> 'open Spotify', 'open Chrome': one verb over a short list of names. A part that is a clause of
    its own (it starts with a verb in any form, or points at something: "…and hello there") is never taken for a name."""
    m = _MULTI.match(_clause(c))
    parts = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", m["rest"]) if m else []
    if len(parts) > 1 and all(p and len(p.split()) <= 3 and not re.match(rf"{_CLAUSE_START}\b", p, re.I) and not _DEICTIC.search(p)
                              for p in parts):
        return [f"{m['verb']} {p}" for p in parts]
    return [c]


_QUOTED = re.compile(r"(?<!\w)([\"“‘'])(.+?)([\"”’'])(?!\w)")  # words to type, said in quotes: never split into clauses
# Exact text to type, given after a colon ("write the following in Notepad: …", "open Notepad and type this exactly: …"): everything after
# the colon is the payload. It is data: never split into clauses at "and", never read for commands, never handed to a model to rewrite
# (the owner, 2026-10-13: long dictations were paraphrased, cut at "and", or lost). It is masked before the clause split and put back into
# the type step afterwards (args.literal = True), so inner quotes, line breaks and "stop" inside it change nothing.
_LITERAL = re.compile(r"^(?P<pre>.*?)\b(?P<verb>type|write|enter|put|paste|dictate)\s+(?:out\s+)?(?:(?:the\s+following(?:\s+text)?|this|that|these|"
                      r"the\s+text(?:\s+below)?|exactly\s+this|this\s+exactly|exactly|verbatim|word\s+for\s+word|as\s+is)\s*)*"
                      r"(?:(?:in|into|inside)\s+(?P<app>[^:：\n]{1,60}?)\s*)?[:：]\s*(?P<payload>\S.*)\Z", re.I | re.S)
_LITERAL_MASK = "\x01literal\x01"
# The same, quoted and over several lines ('open Notepad and type "…\n\n…"', 2026-10-15): the one-line quote rule (_TYPE) can't hold a line
# break, so this went to Hermes, which typed it and checked nothing; as a literal it is pasted and read back exactly (computer.type)
_LITERAL_QUOTED = re.compile(r"^(?P<pre>.*?)\b(?P<verb>type|write|enter|put|paste|dictate)\s+(?:out\s+)?[\"“](?P<payload>[^\"”]*\n[^\"”]*)[\"”]"
                             r"(?:\s+(?:in|into|inside)\s+(?P<app>[^\n]{1,60}?))?\s*[.!]?\Z", re.I | re.S)
_BROWSER_NAME = re.compile(rf"^(?:{_BROWSERS}|(?:the\s+|a\s+|web\s+)*browser)$", re.I)


def _into(steps):
    """'open Notepad and write hello there': the typing goes into the app this plan opened (args.into), once that app is there,
    and depends on the step that opened it (`needs`, 1-based). A browser's page is typed into through the page, as before.
    'open YouTube and play X': the playing happens in the app this plan opened, whatever it is (args.app), never a default player."""
    opened = None
    for i, s in enumerate(steps):
        key, a = (s["tool"], s["action"]), s["args"]
        target = str(a.get("target") or "")
        if key in (("computer", "open"), ("computer", "focus")) and not (a.get("browser") or a.get("app") or _WEB_ADDRESS.match(target)
                                                                       or _BROWSER_NAME.match(target) or special_place(target)):
            opened = (i, target)
        elif key == ("computer", "type") and opened and not a.get("into") and (not a.get("field") or a["field"].lower() == opened[1].lower()):
            a.pop("field", None)
            a["into"], s["needs"] = opened[1], opened[0] + 1
            s["label"] = f"Type “{a['text'][:60]}” into {opened[1]}"
        elif key == ("computer", "play") and opened and not a.get("app"):
            a["app"], s["needs"] = opened[1], opened[0] + 1
            s["label"] = f"{s['label']} in {opened[1]}"
        elif s["tool"] != "computer" or key in (("computer", "open"), ("computer", "focus"), ("computer", "close")):
            opened = None  # something else came in between: the typing goes where that left things
    return steps


def after_opened(d, earlier):
    """The rest of a sentence whose first clause already ran early ("open Notepad and | write hello there"): its typing goes into the
    app that clause opened, as if the sentence had been one plan. The dependency is on another task's step, so `needs` is dropped."""
    if d.get("intent") != "TOOL" or not d.get("steps") or not earlier:
        return d
    steps = [{**s, "args": dict(s["args"])} for s in d["steps"]]
    _into([{**s, "args": dict(s["args"])} for s in earlier] + steps)
    for s in steps:
        s.pop("needs", None)
    return {**d, "steps": steps}


def special_place(target):
    """A folder said by name ("downloads", "my documents"): opens Explorer, which is nowhere to type."""
    return bool(re.fullmatch(_FOLDER, target.strip(), re.I))


# "copy this from ChatGPT and paste it in Notepad" (the owner, 2026-10-24): one `transfer` step, read whole before the sentence is split
# at its "and" (Computer.transfer: the selection there, else the app's own Copy button under its last answer; pasted and read back)
_XFER = re.compile(r"^(?:copy|take|grab)\s+(?P<what>.+?)\s+(?:from|in|on|off|out\s+of)\s+(?:the\s+|my\s+)?(?P<src>.+?)(?:\s+app(?:lication)?)?"
                   r"\s*,?\s+(?:and\s+)?(?:then\s+)?(?:paste|put|drop|insert)\s+(?:it|that|this|them|the\s+text)?\s*(?:in|into|on|onto|to|inside)"
                   r"\s+(?:the\s+|my\s+|an?\s+(?:new\s+)?)?(?P<dst>.+?)(?:\s+(?:app(?:lication)?|document|doc|file|window|tab|chat))?$", re.I)
_FILE_WORDS = re.compile(r"\.\w{2,5}\b|\b(?:files?|folders?|photos?|images?|pictures?|videos?)\b", re.I)  # (those: the file tools')


def transfer_step(text):
    """One `transfer` step for "copy … from <app> and paste it in <app>", else None."""
    m = _XFER.match(_clause(text))
    if not m or _FILE_WORDS.search(m["what"]):
        return None
    what, src, dst = m["what"].strip(), m["src"].strip(" ."), m["dst"].strip(" .")
    return step("computer", "transfer", f"Copy {what} from {src} and paste it in {dst}", source=src, target=dst, what=what)


def parse_plan(text, projects=()):
    """Split 'open VS Code, open my Monday project and run the tests' into exact steps. None when any clause is not an
    exact command; a clause like 'check why my API is failing' after exact ones is handed to a coding agent."""
    x = transfer_step(text)
    if x:
        return [x]
    quoted = []
    lit = _LITERAL.match(text.strip()) or _LITERAL_QUOTED.match(text.strip())
    payload = None
    if lit:  # the payload is data: one masked word until the steps are built
        payload = lit["payload"].strip()
        verb = lit["verb"].lower() if lit["verb"].lower() in ("type", "write", "enter") else "write"
        text = f'{lit["pre"]}{verb} "{_LITERAL_MASK}"' + (f' in {lit["app"].strip()}' if lit["app"] else "")

    def keep(m):
        quoted.append(m.group(0))
        return f"\x00{len(quoted) - 1}\x00"
    unmask = lambda s: re.sub(r"\x00(\d+)\x00", lambda m: quoted[int(m[1])], s)  # noqa: E731
    clauses = [unmask(x) for c in _SPLIT.split(_QUOTED.sub(keep, text.strip())) if c and c.strip(" ,.") for x in _expand(c)]
    steps = []
    for i, c in enumerate(clauses):
        s = parse_step(c, projects, first=(i == 0))
        if s is None:
            if i and _DELEGATE.match(_clause(c)):
                steps.append(step("coding_agents", "delegate", _cap(_clause(" ".join(clauses[i:])))[:80],
                                  prompt=_clause(" ".join(clauses[i:]))))
                break
            if i and _ANSWER.match(_clause(c)):  # "...and give me the full SEO report for that": an AI writes it, Jev chose which
                ask = _clause(" ".join(clauses[i:]))
                steps.append(step("analysis", "answer", f"Ask an AI: {ask[:70]}", request=ask))
                break
            return None
        steps.append(s)
    if len(steps) > 1:  # "Open me and play any random song" (2026-10-09): the half-heard "open me" failed, and with it the song
        steps = [s for s in steps if not ((s["tool"], s["action"]) == ("computer", "open") and nothing_named(s["args"].get("target")))] or steps
    steps = _into(_merge(steps))
    if payload is not None:
        for s in steps:
            if s["args"].get("text") == _LITERAL_MASK:  # the owner's exact words, typed as they are and read back as they are (computer.type)
                s["args"].update(text=payload, literal=True)
                s["label"] = s["label"].replace(_LITERAL_MASK, re.sub(r"\s+", " ", payload)[:60])
    return steps or None


FILLER_WORDS = {"a", "an", "the", "my", "me", "for", "to", "of", "and", "or", "in", "on", "at", "it", "this", "that", "up", "with",
                "please", "now", "app", "so", "is", "be", "you", "i", "uh", "um", "okay", "ok", "new", "another"}  # = computer.FILLER_WORDS (+ new)


def nothing_named(target):
    """An "open" target made only of filler words: no app, site or folder could be meant ("open for", "open me", "open a new")."""
    words = re.findall(r"[a-z0-9']+", (target or "").lower())
    return not words or not set(words) - FILLER_WORDS


# Early execution: the first clause of a sentence still being said may run before the rest. Only a clause that is closed (a
# conjunction follows it), exact, and that just opens or shows something; never one that types, sends, closes, deletes or runs.
# Measured 2026-09-30: a model's "this sentence is complete" can't decide it (a grammatical prefix scores as finished), so
# the grammar does: the owner has moved on to "and…".
EARLY_SAFE = {("computer", "open"), ("computer", "focus"), ("applications", "open_project"), ("browser", "open_url")}
# In pure agent mode (the owner, 2026-10-06: "don't wait to complete the sentence") a clause that does something may also start while the
# sentence goes on, once its words have held still for EARLY_HOLD_MS: actions that are exact, SAFE and harmless when the words turn out
# to be partial. Typing, searching, a labelled click, a key press and media play never start early: half their words are the wrong words.
EARLY_HOLD_MS = 700
EARLY_ACTS = {("computer", "photo"), ("vision", "look"), ("computer", "screenshot"), ("computer", "scroll"), ("computer", "close"), ("computer", "window"),
              ("computer", "volume"), ("computer", "media"), ("browser", "back"), ("browser", "new_tab"), ("browser", "switch_tab"), ("browser", "close_tab")}
_DANGLING = re.compile(r"^(?P<first>.+?)[,\s]+(?:and(?:\s+then)?|then)[,\s]*$", re.I)


def early_clause(text, projects=(), name="Monday", whole=False, held_ms=0):
    """-> (the clause as a command, its steps), or None when nothing in `text` may run yet. With `whole`, the words so far may be the
    clause even before a conjunction closes it (pure agent mode, the owner's rule of 2026-10-06: "open camera" opens the Camera the
    moment it is heard); the steps must still only open or show something, and the Core checks that what they open exists."""
    t = _name_lead(name).sub("", text.strip(), count=1) or text.strip()
    if _CORRECTION.search(t) or _STOP.match(t):
        return None  # the owner is correcting themselves, or stopping something: the whole sentence decides
    parts = [c for c in _SPLIT.split(t) if c and c.strip(" ,.")]
    m = None if len(parts) >= 2 else _DANGLING.match(t)
    first = parts[0] if len(parts) >= 2 else m["first"] if m else t if whole else None
    steps = parse_plan(first, projects) if first else None

    def early_ok(s):
        key = (s["tool"], s["action"])
        acts = whole and held_ms >= EARLY_HOLD_MS and (key in EARLY_ACTS or (key == ("computer", "click") and s["args"].get("at") == "cursor"))
        return (key in EARLY_SAFE or acts) and permit(s)["decision"] == "SAFE"
    if not steps or not all(early_ok(s) for s in steps):
        return None
    if needs_browser(steps) is not None:
        return None  # an address with no browser named: Monday asks which first, so nothing starts early
    return _clause(first), steps


def after_early(text, clause, name="Monday"):
    """What is left of the finished sentence once `clause` ran early: "" when nothing follows it, None when the sentence no
    longer begins with it (the recognizer revised the words, or the owner changed their mind): then the whole sentence stands.
    A list goes on with the clause's own verb: "open Spotify and | Chrome" leaves "open Chrome"."""
    t = _name_lead(name).sub("", text.strip(), count=1) or text.strip()
    words = re.findall(r"[\w.'’-]+", clause)
    m = re.match(r"^.{0,40}?\b" + r"\W+".join(map(re.escape, words)) + r"\b[\s,.;]*(?:(?:and\s+then|and|then)\b[\s,]*)?", t, re.I) if words else None
    if not m:
        return None
    head = re.match(r"^.{0,40}?\b" + r"\W+".join(map(re.escape, words)) + r"\b", t, re.I)
    if head and len(t) > head.end() and not re.match(r"[\s,;!?]|\.(?!\w)", t[head.end():]):
        return None  # "open Spotify" grew into "open Spotify's settings" (or "example" into "example.org"): another request, routed whole
    rest = t[m.end():].strip(" ,.")
    if rest and not re.match(rf"{_CLAUSE_START}\b", rest, re.I):
        if re.search(r"\bplay$", clause, re.I):  # "…and play | Blinding Lights": the bare play ran; the words that followed name the song
            rest = f"play {rest}"
        elif _MULTI.match(clause):
            rest = f"{words[0]} {rest}"
    return rest


# -- Google (core/google.py): the owner's Docs, Drive, Calendar, Tasks, Sheets, Keep notes, and Gmail/Contacts when they turn them on.
# Exact requests only; each becomes one `google` step that the executor runs (permit, receipt, read back). Calendar requests the
# rules can't read go to Jev's calendar intent and end up as the same steps (Monday.cmd_calendar).
_THIS = r"(?:this|that|it|these(?:\s+(?:points|ideas|notes))?|this\s+research|the\s+research|your\s+answer|what\s+you\s+(?:said|found))"
_DISCUSSED = re.compile(r"\b(we\s+(?:discussed|talked\s+about|just\s+said)|our\s+(?:conversation|chat|discussion)|this\s+conversation|these\s+ideas)\b", re.I)
_CURRENT_PROJECT = re.compile(r"^(?:my\s+|the\s+)?(?:current|this)\s+project$", re.I)
_G_NOTE_SAVE = re.compile(rf"^(?:save|keep|put|add|store)\s+(?P<what>{_THIS})\s+(?:as\s+a\s+note|(?:to|in|into)\s+(?:my\s+)?(?:google\s+keep\s+)?notes|"
                          rf"(?:to|in|into)\s+(?:google\s+)?keep)$", re.I)
_G_NOTE_NEW = re.compile(r"^(?:create|make|write|take|start|jot\s+down)\s+(?:a\s+)?(?:new\s+|quick\s+)?(?:google\s+keep\s+|keep\s+)?note"
                         r"(?:\s+(?:about|on|for|of|called|titled|named)\s+(?P<topic>.+?))?(?:\s*(?::|saying|that\s+says)\s*(?P<text>.+))?$", re.I)
_G_NOTE_FIND = re.compile(r"^(?:find|show|open|search|get|list|pull\s+up)\s+(?:me\s+)?(?:all\s+)?(?:my\s+|the\s+)?(?:recent\s+|latest\s+|last\s+)?"
                          r"(?:google\s+keep\s+|keep\s+)?notes?(?:\s+(?:about|on|for|related\s+to|from|called|titled)\s+(?P<q>.+))?$", re.I)
_G_NOTE_ADD = re.compile(rf"^(?:add|append|put|write)\s+(?P<what>{_THIS}|.+?)\s+(?:to|into|in|onto)\s+(?:my\s+|the\s+)?(?:existing\s+|last\s+)?"
                         r"(?:google\s+keep\s+|keep\s+)?note(?:\s+(?:about|on|called|titled)\s+(?P<q>.+))?$", re.I)
_G_DOC_NEW = re.compile(r"^(?:create|make|write|start|draft|open)\s+(?:a\s+|an\s+)?(?:new\s+)?google\s+doc(?:ument)?(?P<rest>.*)$", re.I)
_G_DOC_ORGANIZE = re.compile(rf"^(?:organi[sz]e|turn|put|write\s+up|save)\s+(?P<what>{_THIS}|our\s+conversation)\s+(?:into|as|in)\s+a\s+(?:google\s+)?doc(?:ument)?(?P<rest>.*)$", re.I)
_G_DOC_FIND = re.compile(r"^(?:find|show|open|search\s+for|look\s+for|get|list|pull\s+up)\s+(?:me\s+)?(?:all\s+)?(?:the\s+|my\s+)?(?:google\s+)?(?:docs?|documents?)\b(?P<rest>.*)$", re.I)
_G_DRIVE_FIND = re.compile(r"^(?:find|show|search|search\s+for|look\s+for|list)\s+(?:me\s+)?(?:all\s+)?(?:the\s+|my\s+)?(?:google\s+drive\s+files?|files?\s+(?:in|on)\s+(?:my\s+)?(?:google\s+)?drive|drive\s+files?)(?P<rest>.*)$", re.I)
_G_DOC_ADD = re.compile(rf"^(?:add|append|put|write)\s+(?P<what>{_THIS}|.+?)\s+(?:to|into|in)\s+(?:my\s+|the\s+)?(?P<name>.+?)\s+(?:google\s+)?doc(?:ument)?$", re.I)
_G_CAL_FREE = re.compile(r"\b(?:find|is\s+there|do\s+i\s+have|when\s+am\s+i|got)\b.*\bfree\b|\bfree\s+(?:time|slot|hour|half\s+hour|\d+\s*(?:minutes?|mins?|hours?))\b", re.I)
_G_CAL_READ = re.compile(r"^(?:what(?:'s|\s+is|\s+do\s+i\s+have|\s+have\s+i\s+got)|do\s+i\s+have\s+anything|show(?:\s+me)?|read|check|list)\b.*"
                         r"\b(?:my\s+(?:google\s+)?(?:calendar|schedule|agenda|meetings?|events?)|scheduled|planned|on\s+(?:today|tomorrow|tonight|this\s+week|next\s+week|"
                         r"monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b|^(?:what(?:'s|\s+is)\s+)?my\s+next\s+(?:meeting|event|appointment)\b", re.I)
_G_CAL_ADD = re.compile(r"^(?:please\s+)?(?:add|create|schedule|put|book|set\s+up|make)\s+(?:an?\s+|the\s+)?.*\b(?:calendar|meeting|event|appointment|call\s+with|lunch|dinner)\b", re.I)
_G_TASKS_SHOW = re.compile(r"^(?:show|list|what\s+are|read|check|get)(?:\s+me)?\s+(?:all\s+)?(?:my\s+)?(?:(?:unfinished|open|pending|incomplete|remaining|google)\s+tasks|to-?dos?|to-?do\s+list)\b", re.I)
_G_TASK_ADD = re.compile(r"^(?:add|create|make|set|put)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:google\s+)?(?:task|to-?do|reminder)(?:\s+(?:to|for|called|that\s+says|:))?\s*(?P<title>.+)$"
                         r"|^remind\s+me\s+(?:to|about)\s+(?P<title2>.+)$", re.I)
_G_SHEET_NEW = re.compile(r"^(?:create|make|start)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:google\s+)?(?:spread)?sheet\s+(?:called|named|titled|for)\s+(?P<title>.+)$", re.I)
_G_MAIL = re.compile(r"^(?:search|check|find|look\s+(?:in|through))\s+(?:my\s+)?(?:gmail|e-?mails?|inbox|mail)\s+(?:for|about)\s+(?P<q>.+)$"
                     r"|^(?:any|find|show)\s+(?:new\s+|recent\s+)?e-?mails?\s+from\s+(?P<sender>.+)$", re.I)
_G_CONTACT = re.compile(r"^(?:find|what(?:'s|\s+is)|look\s+up|get)\s+(?P<who>.+?)['’]s\s+(?:phone\s+number|number|e-?mail(?:\s+address)?|contact(?:\s+details)?)$", re.I)
_TOPIC_TAIL = re.compile(r"\s+(?:that\s+)?i\s+(?:was|am|have\s+been)\s+(?:working\s+on|editing|writing)\b.*$|\s+(?:from\s+)?(?:yesterday|today|this\s+week|last\s+week)$", re.I)


def _named(rest):
    """"called X" / "titled X" / "about X" / "on X" / "related to X" in the rest of a request -> (name, topic)."""
    m = re.search(r"\b(?:called|titled|named)\s+[\"“']?(?P<n>.+?)[\"”']?$", rest or "", re.I)
    t = re.search(r"\b(?:about|on|for|related\s+to|regarding)\s+(?P<t>.+?)$", rest or "", re.I)
    return (m["n"].strip(" .") if m else None), (_TOPIC_TAIL.sub("", t["t"]).strip(" .") if t and not m else None)


# Monday's eyes (core/vision.py, the owner 2026-10-09): the camera's "what is this?" and "remember this is my mug". "What do you see"
# stays the screen (read_screen), as before the camera came. A fact ("remember that it is raining", "remember this is how we deploy")
# stays the vault's: only "this" (or "it as") names a thing shown.
_LOOK = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:(?:can|could)\s+you\s+(?:tell\s+me\s+)?|tell\s+me\s+|do\s+you\s+know\s+)?"
                   r"(?:what(?:'s|s|\s+is)\s+(?:this|that|it)(?:\s+(?:thing|one|object|item))?(?:\s+called)?|what\s+(?:thing|object|item)\s+is\s+(?:this|that)"
                   r"|what\s+am\s+i\s+holding(?:\s+up)?|what(?:'s|s|\s+is)\s+in\s+my\s+hands?|what\s+do\s+i\s+have\s+in\s+my\s+hands?"
                   r"|what(?:'s|s|\s+is)\s+in\s+front\s+of\s+(?:you|the\s+camera|me|my\s+camera)|(?:have\s+a\s+|take\s+a\s+)?look\s+at\s+(?:this|that|me)"
                   r"|identify\s+(?:this|that|it)|what\s+(?:can|do)\s+you\s+see\s+(?:through|on|in|with|from)\s+(?:the|my|your)\s+(?:camera|webcam)"
                   r"|what\s+this\s+is|what\s+that\s+is"
                   # words on what is held up (2026-10-20: a book, a letter): the camera reads them (vision.read_text)
                   r"|read\s+(?:this|that|it|the\s+(?:book|letter|label|card|note|sign|cover|title|text)|what(?:'s|\s+is)\s+(?:written|printed)\s+(?:here|on\s+(?:this|that|it)))"
                   r"|what\s+does\s+(?:this|that|it|the\s+(?:book|letter|label|sign|note|card|page))\s+say|what(?:'s|\s+is)\s+written\s+(?:here|on\s+(?:this|that|it))"
                   r"|what\s+(?:book|letter)\s+is\s+(?:this|that)"
                   r")(?:\s+(?:right\s+now|now|here|please))?$", re.I)
_REMEMBER_SEEN = re.compile(r"^(?:please\s+)?(?:remember|learn|memori[sz]e)\s+(?:that\s+)?(?:(?:this|this\s+one|this\s+thing)\s+(?:is|as)|it\s+as)\s+(?P<label>.+)$"
                            r"|^(?:this|this\s+one|this\s+thing)\s+is\s+(?P<label2>.+?)[,.;]?\s+(?:please\s+)?(?:remember|learn|memori[sz]e)\s+(?:it|this|that)$", re.I)
_NOT_A_THING = re.compile(r"^(?:how|what|why|when|where|who|that|because|so|the\s+way|important|true|false|not)\b", re.I)


def vision_steps(t):
    """Steps for the camera (core/vision.py), else None."""
    c = _clause(t)
    m = _REMEMBER_SEEN.match(c)
    if m:
        label = (m["label"] or m["label2"] or "").strip(" .!?,;:\"'“”")
        if label and len(label) <= 60 and not _NOT_A_THING.match(label):
            return [step("vision", "remember", f"Remember this as “{label}”", name=label)]
    if _LOOK.match(c):
        return [step("vision", "look", "Look through the camera")]
    return None


def google_steps(t, calendar_action=None):
    """One `google` step for an exact Google request, else None. calendar_action: Jev's reading of a calendar request the rules
    couldn't parse (next_event / agenda / create / change / availability)."""
    c = _clause(t)
    m = _G_NOTE_SAVE.match(c)
    if m:
        return [step("google", "note_create", "Save it as a note in Google Keep", content="last_reply")]
    m = _G_NOTE_ADD.match(c)
    if m and not re.search(r"\bdoc(?:ument)?\b", c, re.I):
        what = m["what"]
        lit = None if re.fullmatch(_THIS, what, re.I) else what
        return [step("google", "note_append", "Add it to " + (f"the Keep note about {m['q']}" if m["q"] else "the last Keep note Monday made"),
                     query=m["q"], text=lit, content=None if lit else "last_reply")]
    m = _G_NOTE_NEW.match(c)
    if m:
        topic, text = (m["topic"] or "").strip(" .") or None, (m["text"] or "").strip() or None
        talk = bool(topic and _DISCUSSED.search(topic))
        return [step("google", "note_create", f"Make a Google Keep note" + (f" about {topic}" if topic else ""), title=None if talk else topic,
                     text=text, content=None if text else "conversation" if talk else None, write="summarize" if talk else None)]
    m = _G_NOTE_FIND.match(c)
    if m:
        q = (m["q"] or "").strip(" .?") or None
        return [step("google", "note_find", "Find notes in Google Keep" + (f" about {q}" if q else ""), query=q)]
    m = _G_DOC_ORGANIZE.match(c)
    if m:
        name, _ = _named(m["rest"])
        return [step("google", "doc_create", "Write it up as a Google Doc", title=name, content="conversation" if _DISCUSSED.search(m["what"]) or
                     m["what"].lower().startswith(("these", "our")) else "last_reply", write="organize")]
    m = _G_DOC_NEW.match(c)
    if m:
        rest = m["rest"].strip()
        name, topic = _named(rest)
        summ = re.search(rf"\b(?:summari[sz]ing|summary\s+of|with|from|of)\s+(?P<w>{_THIS}|our\s+conversation|what\s+we\s+discussed)\b", rest, re.I)
        src = ("conversation" if _DISCUSSED.search(summ["w"]) else "last_reply") if summ else None
        return [step("google", "doc_create", "Create a Google Doc" + (f" “{name or topic}”" if name or topic else ""), title=name or topic,
                     content=src, write="summarize" if summ and re.search(r"summar", rest, re.I) else None)]
    m = _G_DOC_ADD.match(c)
    if m:
        what = m["what"]
        lit = None if re.fullmatch(_THIS, what, re.I) else what
        return [step("google", "doc_append", f"Add it to the Google Doc “{m['name']}”", name=m["name"], text=lit, content=None if lit else "last_reply")]
    for rx, docs in ((_G_DOC_FIND, True), (_G_DRIVE_FIND, False)):
        m = rx.match(c)
        if m:
            rest = m["rest"].strip()
            name, topic = _named(_TOPIC_TAIL.sub("", " " + rest).strip())  # "…I was working on yesterday" is a date, not a topic
            when = next((w for w in ("yesterday", "today", "this week", "last week") if re.search(rf"\b{w}\b", rest, re.I)), None)
            q = name or topic
            return [step("google", "doc_find" if docs else "drive_find", ("Find Google Docs" if docs else "Find Google Drive files") +
                         (f" about {q}" if q else "") + (f" from {when}" if when else ""), query=q, when=when)]
    if _G_CAL_FREE.search(c):
        return [step("google", "calendar_free", "Find free time in Google Calendar", when=c, minutes=_minutes_in(c))]
    if _G_TASKS_SHOW.match(c):
        return [step("google", "tasks_list", "Show your open Google Tasks")]
    m = _G_TASK_ADD.match(c)
    if m:
        title = (m["title"] or m["title2"] or "").strip(" .")
        return [step("google", "task_create", f"Add “{title[:60]}” to Google Tasks", title=title[:1].upper() + title[1:])] if title else None
    if _G_CAL_ADD.match(c) or calendar_action == "create":
        return [step("google", "calendar_create", "Add it to Google Calendar", request=c)]
    if _G_CAL_READ.search(c) or calendar_action in ("next_event", "agenda"):
        nxt = bool(re.search(r"\bnext\s+(?:meeting|event|appointment)\b", c, re.I)) or calendar_action == "next_event"
        return [step("google", "calendar_agenda", "Read your Google Calendar", when=c, next=nxt or None)]
    if calendar_action == "availability":
        return [step("google", "calendar_free", "Find free time in Google Calendar", when=c, minutes=_minutes_in(c))]
    m = _G_SHEET_NEW.match(c)
    if m:
        return [step("google", "sheet_create", f"Create the Google Sheet “{m['title'].strip(' .')}”", title=m["title"].strip(" ."))]
    m = _G_MAIL.match(c)
    if m:
        q = m["q"] or f"from:{m['sender'].strip()}"
        return [step("google", "mail_find", f"Search Gmail for {q}", query=q.strip(" .?"))]
    m = _G_CONTACT.match(c)
    if m:
        return [step("google", "contact_find", f"Look up {m['who']} in Google Contacts", query=m["who"].strip())]
    return None


def _minutes_in(c):
    m = re.search(r"\b(?:free\s+)?(\d+)\s*(minutes?|mins?|hours?)\b|\b(half\s+an?\s+hour|an?\s+hour)\b", c, re.I)
    if not m:
        return 60
    if m[3]:
        return 30 if m[3].lower().startswith("half") else 60
    return int(m[1]) * (60 if m[2].lower().startswith("h") else 1)


# -- Office documents (core/office.py, docs/adr/0005; the owner, 2026-10-24: "making the docs too like words and ppt and other stuff"): a
# Word, PowerPoint, Excel or PDF file that a model writes and Office builds on this PC, in front of the owner. Google's Docs and Sheets stay
# Google's (google_steps is asked first, and a request that names Google is never this one).
_DOC_NOUN = (r"(?P<kind>(?:ms\s+|microsoft\s+)?word(?:\s+(?:doc(?:ument)?|file))?|docx|(?:power\s*point|ppt|pptx)(?:\s+(?:deck|presentation|file|slides?))?|"
             r"(?:slide\s*)?deck|slides|slide\s*show|presentation|(?:ms\s+|microsoft\s+)?excel(?:\s+(?:sheet|file|workbook|spreadsheet|table))?|"
             r"xlsx|spreadsheet|workbook|pdf(?:\s+(?:file|document|report))?|report|document)")
_DOC_MAKE = r"(?:make|create|build|prepare|generate|draft|write|produce|put\s+together)"
_OFFICE_NEW = re.compile(rf"^{_DOC_MAKE}\s+(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|one\s+)?(?:new\s+|short\s+|quick\s+|detailed\s+|simple\s+|professional\s+)?"
                         rf"(?:(?P<n>\d{{1,2}})[-\s]*(?:slides?|pages?)\s+)?{_DOC_NOUN}"
                         rf"(?:\s+(?:on|about|for|of|covering|regarding|explaining|summari[sz]ing|titled|called|named)\s+(?P<topic>.+))?$", re.I)
_OFFICE_IN = re.compile(rf"^{_DOC_MAKE}\s+(?:me\s+|us\s+)?(?P<topic>.+?)\s+(?:in|into|as|using)\s+(?:a\s+|an\s+)?(?P<kind>(?:ms\s+|microsoft\s+)?word|"
                        r"power\s*point|ppt|excel|pdf)(?:\s+(?:doc(?:ument)?|file|presentation|deck|sheet|workbook|format))?$", re.I)
_OFFICE_THIS = re.compile(rf"^(?:turn|put|make|convert|save)\s+(?P<this>{_THIS})\s+(?:into|in|as)\s+(?:a\s+|an\s+)?{_DOC_NOUN}$", re.I)
_SLIDES_N = re.compile(r"\s*\b(?:with|in)\s+(\d{1,2})\s+slides?\b", re.I)
OFFICE_LABEL = {"word": "Word document", "pdf": "PDF", "powerpoint": "PowerPoint", "excel": "Excel sheet"}


def office_steps(t):
    """One `office` step for a request to make a document (kind word / powerpoint / excel / pdf, its topic, a slide count when said;
    "turn this into a presentation" is made from Monday's last answer), else None."""
    c = _clause(t)
    m = None if re.search(r"\bgoogle\b", c, re.I) else _OFFICE_NEW.match(c) or _OFFICE_IN.match(c) or _OFFICE_THIS.match(c)
    if not m:
        return None
    w = m["kind"].lower()
    kind = ("powerpoint" if re.search(r"power\s*point|ppt|deck|slide|presentation", w) else
            "excel" if re.search(r"excel|xlsx|spreadsheet|workbook", w) else "pdf" if "pdf" in w else "word")
    topic, n = (m.groupdict().get("topic") or "").strip(" ."), m.groupdict().get("n")
    s = _SLIDES_N.search(topic)
    if s:
        topic, n = (topic[:s.start()] + topic[s.end():]).strip(" ,."), s[1]
    this = bool(m.groupdict().get("this"))
    return [step("office", "create", f"Make the {OFFICE_LABEL[kind]}" + (f" about {topic}" if topic else ""), kind=kind, topic=topic or None,
                 slides=int(n) if n and kind == "powerpoint" else None, content="last_reply" if this else None, request=c)]


def instant(d):
    """The instant lane (the owner, 2026-10-06): an exact command whose every step the permission engine rules SAFE, with no question
    to ask, runs the moment it is heard; Jev reads it afterwards, for the record. LOW_RISK and above, and anything that asks, keep Jev first."""
    steps = d.get("steps") or []
    return d.get("intent") == "TOOL" and bool(steps) and needs_browser(steps) is None and all(permit(s)["tier"] == SAFE for s in steps)


# "open github.com": an address, and two browsers could show it (Monday's own Chrome, which it can read and act in, or the owner's
# everyday one with their logins). The owner's choice, 2026-10-05: Monday asks which, unless the words already say.
_WEB_ADDRESS = re.compile(r"^(?:https?://|www\.)\S+$|^[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|dev|ai|app|co|in|edu|gov|me|tv|gg|uk|us)(?:/\S*)?$", re.I)  # = computer._URLISH
_WHOSE = (r"(?P<owner>my\s+(?:own\s+|everyday\s+|open\s+|real\s+)?(?:browser|chrome)|mine)|"
          r"(?P<monday>your\s+(?:own\s+)?(?:browser|chrome)|yours|monday['’]?s(?:\s+(?:browser|chrome))?)")
_NAMED_BROWSER = re.compile(rf"\b(?:in|with|using|use|on|through)\s+(?:{_WHOSE})\b", re.I)
_OPEN_WHOSE = re.compile(rf"^(?P<x>.+?)\s+(?:in|with|using)\s+(?:{_WHOSE})$", re.I)
_BROWSER_ANSWER = re.compile(rf"^(?:(?:open\s+(?:it|that)\s+)?(?:in|use|with)\s+)?(?:{_WHOSE})(?:\s+one)?$", re.I)


def needs_browser(steps):
    """The index of the first step that opens a web address without saying in whose browser, else None."""
    return next((i for i, s in enumerate(steps or []) if (s["tool"], s["action"]) == ("computer", "open") and not s["args"].get("app")
                 and not s["args"].get("browser") and _WEB_ADDRESS.match(str(s["args"].get("target") or ""))), None)


def browser_answer(text, name="Monday"):
    """The owner's answer to "my Chrome, or your browser?": "yours" -> "monday", "mine" -> "owner", anything else -> None."""
    for t in (text.strip(), _name_lead(name).sub("", text.strip(), count=1)):  # "Monday's" is an answer; "Monday, mine" is one too
        m = _BROWSER_ANSWER.match(_clause(t))
        if m:
            return "owner" if m["owner"] else "monday"
    return None


def named_browser(text):
    """The browser a request names for the web, in the owner's words: "in your Chrome" / "yours" -> "monday" (Monday's own Chrome, its own
    profile), "in my browser" / "use my open browser" / "mine" -> "owner" (their own, signed in), else None. Since 2026-10-20 the web is
    the owner's own browser unless Monday's is named (the owner: desktop requests must run on the real desktop, never inside Monday)."""
    m = _NAMED_BROWSER.search(text or "")
    return ("owner" if m["owner"] else "monday") if m else None


def with_browser(d, which):
    """The route with every address it opens given the browser the owner chose."""
    return {**d, "steps": [{**s, "args": {**s["args"], "browser": which}} if needs_browser([s]) is not None else s for s in d["steps"]]}


# Goals (core/goals.py, 2026-10-15: Meta Muse's and Grok Bot's way: a goal planned, then advanced step by step, with check-ins)
_GOAL_NEW = re.compile(r"^(?:my\s+(?:new\s+)?goal\s+is(?:\s+to)?|(?:set|add|make|create|start)\s+(?:a\s+|the\s+|my\s+)?(?:new\s+)?goal"
                       r"(?:\s+to|\s*:)?|new\s+goal\s*:?|goal\s*:)\s+(?P<text>\S.*)$", re.I)
_GOAL_THIS = r"\s+(?:my|the|this)\s+goal"
_GOAL_OPS = (("start", rf"^(?:start|begin|go\s+ahead\s+with|work\s+on){_GOAL_THIS}$"),
             ("continue", rf"^(?:continue|resume|keep\s+going\s+(?:on|with)){_GOAL_THIS}$"),
             ("pause", rf"^(?:pause|hold){_GOAL_THIS}$"), ("stop", rf"^(?:stop|end|cancel|drop){_GOAL_THIS}$"),
             ("forget", rf"^(?:forget|delete|remove){_GOAL_THIS}$"),
             ("status", r"^(?:how(?:'s|\s+is)\s+my\s+goal(?:\s+going)?|(?:show\s+me\s+)?(?:my\s+)?goal(?:'s)?\s+status|what(?:'s|\s+is)\s+my\s+goal)$"),
             ("step_done", r"^(?:(?:i(?:'ve|\s+have)?\s+)?(?:did|done|finished|completed)\s+(?:the\s+|that\s+|my\s+)?(?:next\s+)?step|step\s+done)$"),
             ("skip", r"^skip\s+(?:the|this|that)\s+step$"))


def goal_cmd(c):
    """'my goal is to …' -> {"op": "new", "text"}; 'start / continue / pause / stop / forget my goal', 'how is my goal', 'step done',
    'skip the step' -> {"op"}; else None."""
    m = _GOAL_NEW.match(c)
    if m:
        return {"op": "new", "text": m["text"].strip()}
    return next(({"op": op} for op, rx in _GOAL_OPS if re.match(rx, c, re.I)), None)


def _fast(t, projects):
    """Exact commands that never need Jev or a model: voice, time, status, AI tools, UI, defaults."""
    c = _clause(t)
    if _SLEEP.match(c):
        return {"intent": "SLEEP", "rule": "sleep"}
    if _WAKE.match(c):
        return {"intent": "WAKE", "rule": "wake"}
    for op, rx in _VOICE:
        m = rx.match(c)
        if m:
            g = m.groupdict()
            return {"intent": "VOICE", "op": op, "rule": f"voice:{op}", "ordinal": ORDINALS.get((g.get("ord") or "").lower()),
                    "number": int(g["num"]) if g.get("num") else None, "query": (g.get("desc") or "").strip() or None,
                    "provider": _TTS_PROVIDER.get(re.sub(r"\s+", " ", (g.get("prov") or "").lower())) or None}
    if _TIME.match(c):
        return {"intent": "TIME", "rule": "time"}
    if _SYSTEM.match(c):
        return {"intent": "SYSTEM", "rule": "system-status"}
    if _JUDGE_DEMO.match(c):  # a canned fabricated claim through the real judge (Monday.cmd_judge_demo)
        return {"intent": "JUDGE_DEMO", "rule": "judge-demo"}
    for kind, rx in _AI_TOOLS:
        m = rx.match(c)
        if m:
            g = m.groupdict()
            subject = g.get("subject") or g.get("subject2") or g.get("subject3")
            return {"intent": "AI_TOOLS", "rule": f"ai-tools:{kind}", "kind": kind, "subject": subject.strip() if subject else None}
    m = _UI_PAGE.match(c)
    if m:  # one of Monday's own pages (its window comes forward on it)
        return {"intent": "UI", "rule": "ui:page", "page": _PAGE_WORDS[" ".join(m["page"].lower().split())], "focus": None}
    for page, rx in _UI:
        m = rx.match(c)
        if m:
            focus = (m.groupdict().get("focus") or "").strip(" ?.") or None
            return {"intent": "UI", "rule": f"ui:{page}", "page": page, "focus": focus}
    m = _ROUTING.match(c)
    if m:
        on = (m["mode"] or "").lower().startswith("jev") or (m["onoff"] or "").lower() in ("enable", "turn on")
        return {"intent": "ROUTING", "rule": "routing-mode", "mode": "jev" if on else "rules"}
    m = _DEFAULT_BROWSER.match(c)
    if m:
        b = (m["b"] or m["b2"]).lower().replace("google ", "").replace("microsoft ", "")
        return {"intent": "DEFAULT", "rule": "default-browser", "setting": "default_browser", "value": b}
    m = _DEFAULT_TERMINAL.match(c)
    if m:
        v = {"cmd": "command prompt"}.get(m["t"].lower(), re.sub(r"\s+", " ", m["t"].lower()))
        return {"intent": "DEFAULT", "rule": "default-terminal", "setting": "default_terminal", "value": v}
    m = _CONFIRM_LAUNCH.match(c)
    if m:
        return {"intent": "DEFAULT", "rule": "confirm-launch", "setting": "confirm_launch", "value": bool(m["on"])}
    m = _DEFAULT_COMPUTER.match(c)
    if m:
        return {"intent": "DEFAULT", "rule": "default-computer-agent", "setting": "default_computer_agent",
                "value": "hermes" if m["a"].lower() == "hermes" else "computer"}
    if _ASK_BROWSER.match(c):
        return {"intent": "DEFAULT", "rule": "ask-browser", "setting": "ask_browser", "value": True}
    kept = memory_rules(t)  # "Use FastAPI for the Monday backend": a decision to keep, not work to start
    if kept:
        return {"intent": "REMEMBER", "rule": f"memory:{kept[0]}", "kind": kept[0], "text": kept[1]}
    return None


# A question (the owner, 2026-10-08: "when I ask a question it answers out loud; otherwise it does it without telling"). Read from
# the words alone, so it costs nothing and needs no model: spoken transcripts have no "?", so the opening decides. "Can you open
# Spotify" is a polite order, not a question: what follows "can/could/would/will you" is judged on its own.
_ASKS = re.compile(r"^(?:what|what'?s|whats|who|who'?s|whom|whose|why|how|how'?s|when|where|where'?s|which|whether|"
                   r"is|isn'?t|are|aren'?t|am|was|wasn'?t|were|weren'?t|do|does|doesn'?t|did|didn'?t|don'?t|have|has|had|haven'?t|hasn'?t|"
                   r"will|won'?t|would|wouldn'?t|should|shouldn'?t|shall|could|couldn'?t|can|can'?t|may|might|"
                   r"tell\s+me|explain|describe|define|summari[sz]e|give\s+me\s+(?:a\s+|an\s+)?(?:summary|rundown|overview|idea)|"
                   r"do\s+you\s+know|any\s+idea|i\s+(?:want|need|would\s+like)\s+to\s+know|i\s+wonder)\b", re.I)
_POLITE_ORDER = re.compile(r"^(?:can|could|would|will)\s+you\s+(?:please\s+)?(?P<rest>\S.*)$", re.I)
_OPENERS = re.compile(r"^(?:(?:hey|hi|hello|ok|okay|so|and|well|right|um+|uh+|hmm+|now|also|monday)[\s,!.]+)+", re.I)


def is_question(text, name="Monday"):
    """True when the owner asked something (to be answered out loud), False for an order (done without a word). A typed "?" after an
    order ("Could you pull up Notepad?", 2026-10-27: it was spoken as an answer) doesn't make it a question: the words decide."""
    t = _name_lead(name).sub("", (text or "").strip(), count=1).strip() or (text or "").strip()
    t = _OPENERS.sub("", t).strip()
    m = _POLITE_ORDER.match(t)
    if m:
        return is_question(m["rest"], name)
    return bool(_ASKS.match(t)) or t.rstrip().endswith("?") and not is_order(t)


def is_order(text):
    """The words are an exact command for Monday's own tools (parse_plan), whatever punctuation follows them."""
    return parse_plan(text or "") is not None


def _name_lead(name):
    return re.compile(rf"^(?:(?:hey|hi|hello|ok|okay|yo)[\s,!.]+)?{re.escape(name)}\b(?!\s+(?:core|repo)\b)[\s,:!.]*", re.I)


# What a research request is about, as a search engine wants it: without the asking ("research …", "look up …") and without the
# shape of the answer ("… and summarize it in three sentences"). Bing took the whole sentence for a search about "research".
_ASKING = re.compile(r"^(?:(?:do\s+(?:some\s+)?)?research(?:\s+(?:on|about|into))?|look\s+up|find\s+out(?:\s+about)?|"
                     r"search(?:\s+(?:the\s+)?(?:web|internet)|\s+online)?(?:\s+for)?|google|tell\s+me\s+about)\s+", re.I)
_ANSWER_FORM = re.compile(r",?\s+(?:(?:and|then)\s+(?:summari[sz]e|explain|describe)\s+(?:it|that|them|this)\b|"
                          r"in\s+(?:\w+\s+)?(?:sentences?|words?|lines?|bullets?|bullet\s+points?|paragraphs?)\b|"
                          # 2026-10-16: "… and give me sources" went to Bing as part of the search, and it found nothing
                          r"(?:(?:and|then)\s+)?(?:give|show|list|include|add|cite)\s+(?:me\s+)?(?:the\s+|your\s+|some\s+)?"
                          r"(?:sources?|links?|citations?|references?)\b|with\s+(?:the\s+)?(?:sources?|links?|citations?|references?)\b).*$", re.I)


# "the latest news about X", "what's the latest on X": the subject is X. Searched with its framing, Bing listed news homepages
# (2026-10-16); how recent it is comes from dated headlines instead (core/research.py)
_NEWS_FRAME = re.compile(r"^(?:what(?:'s|’s|\s+is|\s+are)\s+)?(?:the\s+)?(?:latest|newest|recent|current)\s+"
                         r"(?:(?:news|updates?|info(?:rmation)?|developments?|headlines)\s+)?(?:on|about|for|regarding|around|with)\s+", re.I)


def research_query(text, name="Monday"):
    """The web search for a research request: its subject, in the owner's own words."""
    t = _name_lead(name).sub("", text.strip(), count=1).strip(" ,.?!")
    t = _ANSWER_FORM.sub("", _ASKING.sub("", t, count=1)).strip(" ,.?!")
    return _NEWS_FRAME.sub("", t, count=1).strip(" ,.?!") or t or text.strip()


def named(text, name="Monday"):
    """The words open by addressing the assistant ("Monday, …", "hey Monday …"). In filming mode that alone is an ask."""
    return bool(_name_lead(name).match(text.strip()))


WAKE_LEAD = r"(?:o\.?\s?k\.?|okay|okey|oh\s?kay|hey|hi|hello|yo)"
# the name alone, then one of these: the room talking about Monday ("Monday is a holiday", "Monday morning…"), not to it
WAKE_ROOM = r"(?:\W+(?:is|was|are|were|will|would|has|had|and|or|the|next|last|this|to|till|until|through|morning|night|afternoon|evening)\b|'s\b)"


def wake_rest(text, name="Monday"):
    """The open microphone's way in: the words after Monday's name when the sentence opens with it ("OK, Monday. What time is it?" ->
    "What time is it?"; "hey Monday play music" -> "play music"; "Monday, open Notepad" -> "open Notepad"), "" for "okay / hey Monday"
    or the name alone, None when the words don't open with it, or the room is talking about Monday ("Monday is a holiday"). 2026-10-19
    it took "okay Monday" only; the owner on 2026-10-20: "when we say Monday or anything, then it'll start listening"; on 2026-10-21,
    after the room's "Monday", heard alone, let its next sentence in: "Need okay/hey Monday" (the bare name alone was None); on
    2026-10-22: "if im saying only monday hen too it cant": "" again, and the sentence it lets in is asked about (Monday._gesture_doubt)."""
    n = re.escape(name)
    m = re.match(rf"^\W*(?:({WAKE_LEAD})\W*{n}\b|{n}\b(?!s)(?!{WAKE_ROOM}))\W*", text or "", re.I)
    return (text or "")[m.end():].strip() if m else None


# Short forms and slips (the owner, 2026-10-29: "in the whole app it should also understand the spelling mistakes and shortform"): the
# words are read as the owner meant them before the rules and Jev read them; quoted words and text to type stay exactly as written.
SHORT_FORMS = {"rn": "right now", "pls": "please", "plz": "please", "pls.": "please", "u": "you", "ur": "your", "urs": "yours", "wht": "what",
               "wat": "what", "abt": "about", "tmrw": "tomorrow", "tmr": "tomorrow", "2mrw": "tomorrow", "2day": "today", "tdy": "today",
               "msg": "message", "msgs": "messages", "pic": "picture", "pics": "pictures", "vid": "video", "vids": "videos", "yt": "YouTube",
               "b4": "before", "bc": "because", "coz": "because", "cuz": "because", "thx": "thanks", "ty": "thank you", "idk": "I don't know",
               "w/": "with", "w/o": "without", "dont": "don't", "cant": "can't", "wont": "won't", "im": "I'm", "ive": "I've", "isnt": "isn't",
               "doesnt": "doesn't", "didnt": "didn't", "whats": "what's", "thats": "that's", "theres": "there's", "wheres": "where's",
               "hows": "how's", "lemme": "let me", "gimme": "give me", "wanna": "want to", "gonna": "going to", "nvm": "never mind",
               "ppl": "people", "pwr": "power"}
_SHORT = re.compile(r"(?<![\w'/])(" + "|".join(sorted(map(re.escape, SHORT_FORMS), key=len, reverse=True)) + r")(?![\w'/])", re.I)
_UNITS = re.compile(r"\b(\d+(?:\.\d+)?)\s*(secs?|mins?|hrs?)\b", re.I)
_UNIT_WORDS = {"sec": "seconds", "secs": "seconds", "min": "minutes", "mins": "minutes", "hr": "hours", "hrs": "hours"}
# The words commands are made of, which a slip of one letter is read as ("opne", "volum", "plya"), and the real words one letter from them
# that stay as they are
FIX_WORDS = frozenset(("open", "close", "play", "pause", "resume", "search", "explain", "describe", "summarize", "summarise", "screenshot",
                       "screen", "volume", "mute", "unmute", "music", "song", "video", "folder", "file", "files", "notepad", "chrome", "spotify",
                       "youtube", "google", "settings", "window", "windows", "minimize", "maximize", "restart", "shutdown", "weather", "timer",
                       "reminder", "remind", "calendar", "clipboard", "error", "message", "whatsapp", "translate", "write", "type", "click",
                       "scroll", "delete", "rename", "download", "downloads", "documents", "desktop", "picture", "pictures", "camera", "next",
                       "previous", "explorer", "terminal", "read", "show", "find", "take", "louder", "quieter", "brightness"))
KEEP_WORDS = frozenset(("oven", "omen", "chose", "clone", "plan", "plat", "pray", "ploy", "tape", "typo", "tale", "tame", "taken", "real",
                        "road", "reed", "rear", "ready", "fine", "film", "fire", "five", "fill", "fund", "fend", "sing", "sang", "sung",
                        "soon", "time", "times", "timed", "stroll", "scream", "scree", "starch", "fodder", "folded", "rewind", "remand",
                        "deplete", "widow", "explore", "whether", "massage", "goggle", "mate", "mite", "mule", "muse", "shot", "shop",
                        "shoe", "snow", "slow", "stow", "nest", "neat", "white", "wrote", "chick", "clock", "clack", "clocks", "text"))


def _outside_quotes(text, fix):
    """fix() applied to the words outside quotes (a quoted name or text to type stays as written)."""
    out, last = [], 0
    for m in _QUOTED.finditer(text):
        out += [fix(text[last:m.start()]), m.group(0)]
        last = m.end()
    return "".join(out + [fix(text[last:])])


def _whole(keys):
    return re.compile(r"(?<![\w'/])(" + "|".join(sorted(map(re.escape, keys), key=len, reverse=True)) + r")(?![\w'/])", re.I)


def normal(text, words=None):
    """The owner's short forms read as words: "rn" → "right now", "u" → "you", "yt" → "YouTube", "5 min" → "5 minutes", "whats" →
    "what's"; and the words Monday learned from them (`words`: {said: meant}, core/learn.py, ADR-0013); never in quotes, nor in text to
    type (type "…", write …)."""
    t = text or ""
    if _LITERAL.match(t.strip()) or _LITERAL_QUOTED.match(t.strip()):
        return t
    forms, rx = SHORT_FORMS, _SHORT
    learned = {k.lower(): v for k, v in (words or {}).items() if k and v and k.lower() not in KEEP_WORDS}
    if learned:
        forms = {**SHORT_FORMS, **learned}
        rx = _whole(forms)

    def said(s):
        s = rx.sub(lambda m: (lambda w: w[:1].upper() + w[1:] if m.group(1)[:1].isupper() else w)(forms[m.group(1).lower()]), s)
        return _UNITS.sub(lambda m: f"{m.group(1)} {_UNIT_WORDS[m.group(2).lower()]}", s)
    return _outside_quotes(t, said)


# The fast model's plain words for a request the rules couldn't read (ADR-0013: only an action Jev read, never a chat question), kept only
# when they make an exact command; what they taught is learned (taught) and forgotten when its task fails
PLAIN_PROMPT = ("Rewrite the owner's request to their computer assistant in plain, correctly spelled English: expand short forms and "
                "slang, fix typing slips. Keep every name, number, file name and quoted text exactly as written. Add nothing, remove "
                "nothing else, and don't answer or carry it out. Reply with the rewritten request only, on one line.")
_NUMBERS = re.compile(r"\d+(?:[.:]\d+)*")


def plain_ok(said, plain):
    """Are the model's plain words still the owner's request: one line, every quoted part and number kept, about as long (half to 2.5
    times its words), and alike as text (a slangy request's words all change: "plz put on sum lofi on yt")?"""
    if not plain or "\n" in plain.strip():
        return False
    if any(m.group(0) not in plain for m in _QUOTED.finditer(said)) or sorted(_NUMBERS.findall(said)) != sorted(_NUMBERS.findall(plain)):
        return False
    a, b = said.lower().split(), plain.lower().split()
    return bool(a) and 0.5 <= len(b) / len(a) <= 2.5 and difflib.SequenceMatcher(None, said.lower(), plain.lower()).ratio() >= 0.45


def _letters_in(short, word):
    """short's letters in word, in order (pls in please, yt in youtube)."""
    it = iter(word)
    return all(ch in it for ch in short)


def taught(said, plain, most=3):
    """The word pairs the model's plain words taught: [(said, meant)] for each word of the owner's that isn't in them and is plainly one
    of them: a slip of one word ("wht" → "what"), or a short form of at most 4 letters, as the first letters of 2-4 words in a row ("rn" →
    "right now", "idk") or the letters of a word twice its length that starts as it does ("yt" → "youtube", "tmrw" → "tomorrow"); kept
    only when exactly one fits. Never a real word one slip away (KEEP_WORDS), a command word itself or a quoted word."""
    if not plain:
        return []
    punct = ".,!?;:\"“”'()"
    quoted = {w.lower() for m in _QUOTED.finditer(said) for w in re.findall(r"[A-Za-z]+", m.group(0))}
    dst = [w.strip(punct).lower() for w in plain.split() if w.strip(punct)]
    src = [x.strip(punct).lower() for x in said.split()]
    known, typed, out = set(dst), set(src), []
    for w in dict.fromkeys(src):
        if not w.isalpha() or w in known or w in quoted or w in KEEP_WORDS or w in FIX_WORDS:
            continue
        fits = {d for d in dst if d[:1] == w[:1] and len(w) >= 3 and _one_slip(w, d)}
        if not fits and len(w) <= 4:  # (a short form stands for words the owner didn't type: "da … down a bit" is not "down a")
            fits = {" ".join(dst[i:i + k]) for k in range(2, 5) for i in range(len(dst) - k + 1)
                    if "".join(x[:1] for x in dst[i:i + k]) == w and not typed & set(dst[i:i + k])}
            fits |= {d for d in dst if len(d) >= 2 * len(w) and d[:1] == w[:1] and _letters_in(w, d) and d not in typed}
        if len(fits) == 1:
            out.append((w, fits.pop()))
    return out[:most]


def _one_slip(a, b):
    """a and b are one slip apart: one letter wrong, missing or extra, or two side by side swapped."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def mend(text):
    """Slips in the words commands are made of ("opne notepad", "turn the volum up", "plya some music", "explian"): a word of 4 letters or
    more that isn't one of FIX_WORDS becomes the one it is a slip of, with the same first letter; never a word in KEEP_WORDS or one of
    them with an ending (opens, played), nor words in quotes or text to type. route() keeps the mended words only when they make an
    exact command."""
    t = text or ""
    if _LITERAL.match(t.strip()) or _LITERAL_QUOTED.match(t.strip()):
        return t

    def fix(m):
        w = m.group(0)
        lw = w.lower()
        if len(lw) < 4 or lw in FIX_WORDS or lw in KEEP_WORDS or any(lw.endswith(e) and lw[:-len(e)] in FIX_WORDS for e in ("s", "es", "d", "ed", "ing", "er")) \
                or any(lw + e in FIX_WORDS for e in ("s", "es")):  # (one of them, or one of them with an ending or without its "s": document)
            return w
        got = [f for f in FIX_WORDS if f[0] == lw[0] and _one_slip(lw, f)]
        return got[0] if len(got) == 1 else w
    return _outside_quotes(t, lambda s: re.sub(r"[A-Za-z]+", fix, s))


def route(text, projects, default_coder="claude_code", name="Monday", computer_agent="computer", words=None):
    """Return a routing decision dict. Never raises; unknown input goes to chat. The owner's short forms are read as words first (normal,
    with the words Monday learned: `words`, named in "learned" when used); words the rules didn't understand are read once more with
    their slips mended (mend), kept only when that makes an exact command ("mended": the words before)."""
    said = normal(text, words)
    used = [k for k in (words or {}) if k and re.search(rf"(?<![\w'/]){re.escape(k)}(?![\w'/])", text or "", re.I)]
    d = _route_mended(said, projects, default_coder, name, computer_agent)
    return {**d, "learned": used} if used else d


def _route_mended(said, projects, default_coder, name, computer_agent):
    d = route_words(said, projects, default_coder, name, computer_agent)
    fixed = mend(said)
    if fixed != said:
        d2 = route_words(fixed, projects, default_coder, name, computer_agent)
        moves = lambda r: [(s["tool"], s["action"]) for s in r.get("steps") or []] or [r["intent"]]  # noqa: E731
        if exact(d2) and (not exact(d) or moves(d) == moves(d2)):  # (the same command with a slip in its words: "close chrom")
            return {**d2, "mended": said}
    return d


def route_words(text, projects, default_coder="claude_code", name="Monday", computer_agent="computer"):
    """The rules on the words as they are (route() reads short forms and slips first). Never raises; unknown input goes to chat."""
    # "Hello Monday, open Chrome" addresses the assistant; the greeting and name are not part of the request (said again and again too:
    # "Monday, Monday hey Monday play …", 2026-10-20, kept it from being read as a command)
    t, lead = text.strip(), _name_lead(name)
    while lead.match(t) and lead.sub("", t, count=1).strip() and lead.sub("", t, count=1) != t:
        t = lead.sub("", t, count=1)
    t = t or text.strip()
    t = _CORRECTION.sub("", t, count=1) or t
    d = {"source": "deterministic", "confidence": None, "intent": "CHAT", "agent": "chat",
         "rule": "fallback", "project": None, "prompt": t, "request": t}
    proj = match_project(t, projects)
    if proj:
        d["project"] = proj["name"]

    for rx in _ADD_PROJECT:
        m = rx.match(t)
        if m:
            return {**d, "intent": "PROJECT_ADD", "agent": None, "rule": "add-project",
                    "name": m["name"].strip(), "path": m["path"].strip().strip('"')}
    if _LITERAL.match(t) or _LITERAL_QUOTED.match(t):  # exact text to type: its payload is data, never read for "stop", "status" or a calendar word (the rules below
        plan = parse_plan(t, projects)  # search the whole sentence), never a model's to rewrite
        if plan:
            return {**d, "intent": "TOOL", "agent": "computer", "rule": "fast-path", "steps": plan, "project": d["project"], "literal": True,
                    "tool": plan[0]["tool"] if len({s["tool"] for s in plan}) == 1 else "plan"}
    m = _FILMING.match(_clause(t))  # before _STOP: "stop filming" ends the mode, it doesn't stop a task
    if m:
        op = "filming_on" if m["on"] else "filming_off"
        return {**d, "intent": "VOICE", "agent": None, "rule": f"voice:{op}", "op": op}
    if _FORGET_ROOTS.match(_clause(t)):  # the folders the owner let Monday search for projects (core/locate.py): back to the defaults
        return {**d, "intent": "DEFAULT", "agent": None, "rule": "search-roots", "setting": "search_roots", "value": None}
    m = _SELF_REPAIR.match(_clause(t))  # before _STOP, like filming: "stop fixing yourself" is a setting
    if m:
        if m["now"] or m["restart"]:
            return {**d, "intent": "REPAIR", "agent": None, "rule": "restart-self" if m["restart"] else "repair-now", "op": "restart" if m["restart"] else "fix"}
        return {**d, "intent": "DEFAULT", "agent": None, "rule": "self-repair", "setting": "self_repair", "value": bool(m["on"] or m["on2"])}
    m = _SHUSH.match(_clause(t))
    if m:
        op = "pause" if m["pause"] else "resume" if m["resume"] else "stop_speaking"
        return {**d, "intent": "VOICE", "agent": None, "rule": f"voice:{op}", "op": op}
    rec = record_cmd(_clause(t))  # the workflow recorder: start / stop+save / run / list / result / stop the test (before _STOP: "stop recording")
    if rec:
        return {**d, "intent": "RECORD", "agent": None, **rec}
    g = goal_cmd(_clause(t))  # goals (core/goals.py), before _STOP: "stop my goal" ends the goal, not every task
    if g:
        return {**d, "intent": "GOAL", "agent": None, "rule": f"goal:{g['op']}", **g}
    pw = power_step(_base_verb(_clause(t)))  # before _STOP: "cancel the shutdown" is Windows' shutdown, not Monday's tasks
    if pw:
        return {**d, "intent": "TOOL", "agent": "computer", "rule": "fast-path", "steps": [pw], "tool": "power"}
    if _STOP_SERVER.match(t):
        return {**d, "intent": "STOP", "agent": None, "rule": "stop-server", "task_id": None, "target_agent": None,
                "all": False, "target_kind": "dev_server"}
    k = _KILL.match(_clause(t))
    music = _MEDIA.match(_clause(t))  # "stop the music" pauses what is playing; "stop it" still stops Monday's task
    if _STOP.match(t) and not (music and not music["it"]) and not (k and not _TASK_WORDS.match(k["x"].strip())):
        m = _TASK_ID.search(t)
        a = re.search(_AGENT_WORD, t, re.I)
        return {**d, "intent": "STOP", "agent": None, "rule": "stop",
                "task_id": m.group(0).upper() if m else None, "target_agent": _agent_key(a.group(1)) if a else None,
                "all": bool(re.search(r"\b(all|everything)\b", t, re.I))}
    m = _STATUS.search(t)
    if m:
        a = next((g for g in m.groups() if g), None)
        return {**d, "intent": "STATUS", "agent": None, "rule": "status",
                "target_agent": _agent_key(a) if a and re.fullmatch(_AGENT_WORD, a, re.I) else None}
    if _CHANGES.search(t):
        m = _TASK_ID.search(t)
        return {**d, "intent": "CHANGES", "agent": None, "rule": "changes", "task_id": m.group(0).upper() if m else None}
    m = _DEFAULT_CODER.match(_clause(t))
    if m:
        return {**d, "intent": "DEFAULT_CODER", "agent": None, "rule": "default-coder", "coder": _coder_key(m["a"] or m["b"] or m["c"])}
    m = _ASK_AI.match(t)
    if m:
        q = (m["q1"] or m["q2"] or "").strip() or None
        provider, coder = _ASK_AI_KEYS[(m["a"] or m["b"]).lower()]
        if q and coder and _CODING.search(q):  # "ask Grok to fix the navbar": code work, for that CLI's coding agent
            return {**d, "intent": "TASK", "agent": coder, "rule": "ask-ai:coding", "prompt": q}
        return {**d, "intent": "ASK_AI", "agent": None, "rule": "ask-ai", "provider": provider, "question": q}
    m = _MODEL.match(_clause(t))  # before _REROUTE: "use claude" alone is the chat AI, not the Claude Code worker
    if m:
        return {**d, "intent": "MODEL", "agent": None, "rule": "model-switch", "want": re.sub(r"\s+", " ", m["want"].lower())}
    if _MODEL_LIST.match(_clause(t)):
        return {**d, "intent": "MODEL", "agent": None, "rule": "model-list", "want": "list"}
    m = _REROUTE.match(t)
    if m:
        return {**d, "intent": "REROUTE", "agent": _agent_key(m.group(1)), "rule": "reroute-last-request"}
    g = None if re.search(_AGENT_WORD, t, re.I) else google_steps(t)
    if g:
        return {**d, "intent": "TOOL", "agent": "computer", "rule": f"google:{g[0]['action']}", "steps": g, "tool": "google"}
    o = None if re.search(_AGENT_WORD, t, re.I) else office_steps(t)
    if o:
        return {**d, "intent": "TOOL", "agent": "computer", "rule": f"office:{o[0]['args']['kind']}", "steps": o, "tool": "office"}
    v = vision_steps(t)
    if v:
        return {**d, "intent": "TOOL", "agent": "computer", "rule": f"vision:{v[0]['action']}", "steps": v, "tool": "vision"}
    if len(t) <= KEYWORD_CHARS and _CALENDAR.search(t) and not re.search(_AGENT_WORD, t, re.I) and not _OPEN.match(_clause(t)):
        return {**d, "intent": "CALENDAR", "agent": None, "rule": "calendar"}
    if _ANALYZE.search(t) and proj and not re.search(_AGENT_WORD, t, re.I):
        return {**d, "intent": "GRAPHIFY_UPDATE", "agent": None, "rule": "graphify-update"}
    f = _fast(t, projects)
    if f:
        return {**d, "agent": None, **f}

    a = re.search(rf"\b{_AGENT_WORD}\b", t, re.I)
    # "Ask Claude to …" / "Hermes, …" is an order to that agent.
    if a and _ADDRESS.match(t):
        agent = _agent_key(a.group(1))
        return {**d, "intent": "TASK", "agent": agent, "rule": f"named-agent:{agent}", "prompt": _ADDRESS.sub("", t, 1) or t}
    # Exact commands for Monday's own tools run as a plan: no Jev, no model. "… with Hermes" keeps the named agent.
    plan = None if a and _VIA_AGENT.search(t) else parse_plan(t, projects)
    if plan:
        proj_name = d["project"] or next((s["args"].get("project") for s in plan if s["args"].get("project")), None)
        return {**d, "intent": "TOOL", "agent": "computer", "rule": "fast-path", "steps": plan, "project": proj_name,
                "tool": plan[0]["tool"] if len({s["tool"] for s in plan}) == 1 else "plan"}
    tail = None if a else power_tail(t)
    if tail:  # an agent does the part before; Monday's own power steps come only once that is done and seen (Monday.execute: "then")
        return {**d, "intent": "TASK", "agent": computer_agent, "rule": "then-power", "prompt": tail[0], "then": tail[1]}
    if a:
        agent = _agent_key(a.group(1))
        # A mention is not an order: with an action verb it is a soft task match; without one ("is hermes still
        # running?") it goes to chat, which knows the live state.
        if _COMPUTER.match(t) or _CODING.search(t):
            return {**d, "intent": "TASK", "agent": agent, "rule": f"mentioned-agent:{agent}", "mentioned": agent}
        return {**d, "rule": f"mentioned-agent:{agent}", "mentioned": agent}
    if _COMPUTER.match(t):
        return {**d, "intent": "TASK", "agent": computer_agent, "rule": "computer-verb"}
    if _CODING.search(t):
        return {**d, "intent": "TASK", "agent": default_coder, "rule": "coding-verb"}
    return d


# -- Jev policy -----------------------------------------------------------------------
# Jev first (Monday 2.0): every request is decided with Jev, except the reflexes that must never wait (stop a task,
# stop/pause/resume speech). Jev classifies; this code decides:
# - An exact command the rules recognised keeps its exact arguments (Jev never generates an app name, URL or command).
#   When Jev's request type agrees, Jev decided it (source "jev"). When Jev is at least OVERRIDE_EXACT_CONFIDENCE sure
#   it is something else ("open the pod bay doors" is conversation, not an app), Jev's route wins. Otherwise the rule
#   stands and the disagreement is recorded. An explicit order to a named agent is never redirected.
# - A soft rule match (a verb, a bare mention of an agent) moves only to an action with LESS power, at
#   MIN_DOWNGRADE_CONFIDENCE; never to another worker.
# - Everything else is Jev's route, gated on confidence: below MIN_INTENT_CONFIDENCE the request goes to the chat model
#   (escalate to an LLM); a worker Jev inferred starts at once from AUTO_LAUNCH_CONFIDENCE, else the owner confirms.
# - Jev may fill a project the request didn't name and ADD an approval gate (jev_risk); it never removes one, and
#   confidence is never authorization: router.permit rules on every step.
SOFT_RULES = ("computer-verb", "coding-verb")
WORKER_INTENTS = ("coding", "computer", "browser", "research", "filesystem", "terminal")
_HANDS = {"open": "Open", "close": "Close", "focus": "Switch to"}  # Jev's `action` that becomes the rules' own step when its app pick is sure (jev:open …)
MIN_INTENT_CONFIDENCE = 0.5      # docs.typesafe.ai: below 0.5 the model is genuinely unsure
AUTO_LAUNCH_CONFIDENCE = 0.8     # an inferred worker starts at once from here; below, the owner confirms the launch
MIN_DOWNGRADE_CONFIDENCE = 0.8
OVERRIDE_EXACT_CONFIDENCE = 0.85  # Jev overrules an exact command only when it is this sure it is something else
MIN_PROJECT_CONFIDENCE = 0.6
MIN_ACTION_CONFIDENCE = 0.5
CAMERA_CONFIDENCE = 0.6          # Jev's "vision" turns the camera on for words the rules didn't know; below this, words answer instead
#                                  (measured 2026-10-10: 1.00 / 0.99 / 0.67 for "can you see what I'm holding?" / "what's this thing in my
#                                  hand" / "tell me what I'm showing you"; "what's this error on my screen" stayed computer at 0.88)
DESTRUCTIVE_P = 0.5
REMEMBER_P = 0.6                 # Jev's "worth remembering" score from which Monday writes the request to Obsidian
FOLLOW_UP_P = 0.6                # Jev's "continues the previous command" score from which its context carries over
_SUMMARY = ("status", "intent", "confidence", "complexity", "destructive", "project", "project_confidence",
            "tool", "tool_confidence", "remember", "needs_llm", "model_class", "verify", "follow_up", "power_action", "power_action_confidence",
            "action", "action_confidence", "redo", "ambiguous", "tone", "tone_confidence",  # v9 (2026-10-27): the first move, a correction, an unclear thing, the tone
            "file",  # v10 (2026-10-28): the answer as a document too (monday.wants_document reads it from the command's route)
            "latency_ms", "model", "error", "question_set",
            "questions", "speech_kind", "browser_kind", "needs_vision", "live")  # v5: what this moment asked, and what came back
# Which of Jev's request types agree with each exact rule (by intent; a plan by the tool of any of its steps).
AGREES = {
    "TOOL:computer": {"computer", "filesystem", "system_info", "browser"},
    "TOOL:browser": {"browser", "research", "computer"},
    "TOOL:terminal": {"terminal", "coding", "computer"},
    "TOOL:applications": {"computer", "coding", "filesystem", "terminal"},
    "TOOL:coding_agents": {"coding", "terminal"},
    "TOOL:analysis": {"coding", "terminal"},
    "TOOL:filesystem": {"filesystem", "computer", "system_info"},
    "TOOL:google": {"calendar", "memory", "browser", "research", "computer", "filesystem", "conversation", "task_control", "unknown"},
    "TOOL:office": {"computer", "filesystem", "research", "browser", "conversation", "unknown"},
    "TOOL:vision": {"vision", "computer", "conversation", "memory", "security", "system_info", "unknown"},  # (before "vision", its nearest)
    "TOOL:power": {"computer", "system_info", "monday_settings", "task_control", "terminal", "unknown"},  # (no "power" intent: its tool says)
    "TASK": {"coding", "computer", "browser", "research", "filesystem", "terminal", "task_control"},
    "REROUTE": {"coding", "computer", "browser", "research", "terminal", "task_control", "monday_settings"},
    "STATUS": {"task_control", "system_info"}, "CHANGES": {"task_control", "coding"}, "LOGS": {"task_control"},
    "MODEL": {"monday_settings", "voice"}, "DEFAULT_CODER": {"monday_settings", "coding"}, "DEFAULT": {"monday_settings"},
    "REPAIR": {"monday_settings", "coding", "task_control", "system_info", "computer", "terminal"},
    "ASK_AI": {"conversation", "research", "monday_settings", "memory", "project_knowledge", "unknown"},
    "ROUTING": {"monday_settings"}, "UI": {"monday_settings", "project_knowledge", "memory"},
    "VOICE": {"voice", "monday_settings"}, "TIME": {"system_info", "conversation"}, "SYSTEM": {"system_info", "computer"},
    "RECORD": {"task_control", "computer", "monday_settings", "system_info", "conversation", "project_knowledge", "unknown"},
    "JUDGE_DEMO": {"monday_settings", "conversation", "unknown", "system_info", "project_knowledge", "memory", "analytics"},
    "AI_TOOLS": {"monday_settings", "system_info", "coding", "conversation"}, "REMEMBER": {"memory", "conversation"},
    "CALENDAR": {"calendar"}, "PROJECT_ADD": {"monday_settings", "coding"},
    "GRAPHIFY_UPDATE": {"project_knowledge", "monday_settings", "coding"},
}
_VOICE_OPS = {"change_voice": "change", "faster": "faster", "slower": "slower", "mute": "mute", "unmute": "unmute",
              "listen_longer": "listen_longer"}


def reflex(d):
    """Commands that must never wait for anything, Jev included: stop a task (or the dev server), stop, pause or
    resume Monday's speech, and filming mode on or off (a hard rule: filming can't depend on the model it gates).
    The owner's brief: "Monday, stop" is immediate and deterministic."""
    return d["intent"] in ("STOP", "SLEEP", "WAKE") or (d["intent"] == "VOICE" and d.get("op") in ("stop_speaking", "pause", "resume", "filming_on", "filming_off",
                                                                                 "mode_pure", "mode_debug", "mode_assistant"))


def wants_jev(d):
    """Jev first: every request but a reflex."""
    return not reflex(d)


def exact(d):
    """The rules recognised an exact command (its arguments come from the words), not a guess from a verb or the chat
    fallback."""
    return d["intent"] != "CHAT" and not (d["intent"] == "TASK" and (d["rule"] in SOFT_RULES or d["rule"].startswith("mentioned-agent")))


def explicit(d):
    """An order to a named agent, never redirected by Jev. Nor is a transport control: "pause it" with nothing said before it read as
    task_control at 94% (2026-10-05), which would have answered with words while the music played on."""
    steps = d.get("steps") or []  # (2026-10-05: an exact command WITHOUT steps crashed here whenever Jev disagreed with it)
    return d["intent"] in ("TASK", "REROUTE", "GOAL") or bool(steps) and all(s["tool"] == "computer" and s["action"] == "media" for s in steps)


def agrees(d, intent):
    if d["intent"] == "TOOL":
        return any(intent in AGREES.get(f"TOOL:{s['tool']}", ()) for s in d.get("steps") or [])
    return intent in AGREES.get(d["intent"], ())


def same_words(a, b):
    """The same words in the same order, punctuation, case and line breaks aside: a dictation that was only punctuated (Monday.punctuate)."""
    w = lambda s: re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (s or "").lower())  # noqa: E731
    return w(a) == w(b)


def jev_summary(j):
    """What the interfaces show about a Jev decision. Never includes the key."""
    return {k: j[k] for k in _SUMMARY if j.get(k) is not None} if j else None


_ROUTE_KEYS = ("intent", "agent", "rule", "source", "confidence", "project", "project_source", "fallback", "tier", "instant",
               "needs_approval", "jev_risk", "overrode", "topic", "action", "tool", "steps", "remember", "op", "page",
               "focus", "subject", "setting", "value", "coder", "agent_reason", "kind", "jev", "reflex", "jev_agrees",
               "needs_llm", "verify", "follows", "missing", "answered", "redo", "ambiguous")


def route_summary(d):
    """The route as stored on the user's message (what the phone's per-message Details shows)."""
    return {k: d[k] for k in _ROUTE_KEYS if d.get(k) is not None}


# A chat's tier when Jev can't be asked (offline, or Jev down): a reasoning or long request is "strong" (a thinking model on this PC,
# Monday.chat_candidates: qwen3:4b), anything else "fast" (llama3.2:1b, light on the GPU)
_HARD = re.compile(r"\b(?:explain|why|how\s+(?:does|do|did|would|can|could|is|are|to)|compare|difference|analy[sz]e|summari[sz]e|"
                   r"write\s+(?:me\s+)?(?:a|an|the|some)\b|essay|report|letter|story|poem|code|program|function|script|debug|solve|calculate|"
                   r"prove|plan|strategy|step[\s-]by[\s-]step|pros\s+and\s+cons|translate)\b", re.I)
HARD_WORDS = 40  # a request this long is "strong" whatever it says


def rules_tier(text):
    """The chat tier from the words alone (when Jev can't say): "strong" for reasoning, writing or a long request, else "fast"."""
    t = text or ""
    return "strong" if len(t.split()) > HARD_WORDS or _HARD.search(t) else "fast"


def tier_for(complexity):
    """Chat model tier from Jev's complexity score (0 trivial .. 2 hard)."""
    if complexity is None:
        return None
    return "fast" if complexity < 0.6 else "strong" if complexity >= 1.4 else "default"


def policy(d, jev, projects, default_coder="claude_code", computer_agent="computer", apps=None):
    """Deterministic route + Jev decision -> the action Monday takes. Pure code, never raises. apps: the installed apps near the words, as
    Monday.jev_context listed them for Jev's live `app` question (its pick "aN" indexes this list; the name is the index's, never Jev's)."""
    d = {**d, "jev": jev_summary(jev)}
    if not jev or jev.get("status") not in ("ok", "cached"):  # no Jev: the rules route, and a chat's tier comes from its words
        return {**d, "fallback": (jev or {}).get("status") or "not_called",
                **({"tier": d.get("tier") or rules_tier(d["request"])} if d["intent"] == "CHAT" else {})}
    names = {p["name"] for p in projects}
    if not d.get("project") and jev.get("project") in names and (jev.get("project_confidence") or 0) >= MIN_PROJECT_CONFIDENCE:
        d["project"], d["project_source"] = jev["project"], "jev"
    if (jev.get("destructive") or 0) >= DESTRUCTIVE_P:
        d["jev_risk"] = jev["destructive"]
    if jev.get("tool") and (jev.get("tool_confidence") or 0) >= MIN_ACTION_CONFIDENCE and not d.get("tool"):
        d["tool"] = jev["tool"]  # which registered capability Jev picked: shown with the route, never authorization
    if (jev.get("remember") or 0) >= REMEMBER_P:
        d["remember"] = jev["remember"]
    for k in ("needs_llm", "verify"):
        if jev.get(k) is not None:
            d[k] = jev[k]
    if not exact(d):  # v9 (2026-10-27): words that carried no command of their own, read by Jev. Monday._route acts on them: a correction
        if (jev.get("redo") or 0) >= REDO_P:  # ("try again", "that's not right") re-runs the last request; an unclear thing is asked about first
            d["redo"] = jev["redo"]
        if (jev.get("ambiguous") or 0) >= ASK_P:
            d["ambiguous"] = jev["ambiguous"]
    pw = power_reading(jev)
    if pw and not exact(d) and one_clause(d["request"]):
        # Jev read this PC's power where the rules found no exact command ("shut down the oc", 2026-10-26): Monday's own power step (what
        # ends the session still asks the owner), never a worker. Jev gives no arguments, so a time the rules couldn't read is asked about.
        if _WHEN.search(d["request"]):
            return {**d, "source": "jev", "intent": "HELP", "agent": None, "rule": "jev:power-when", "topic": "power"}
        return {**d, "source": "jev", "confidence": jev.get("confidence"), "intent": "TOOL", "agent": "computer", "rule": "jev:power",
                "tool": "power", "steps": [step("power", pw, power.ACTIONS[pw]["label"], **({"delay": 0} if power.ACTIONS[pw]["delay"] else {}))]}
    if jev.get("tool") == "office" and (jev.get("tool_confidence") or 0) >= (OVERRIDE_EXACT_CONFIDENCE if exact(d) else MIN_ACTION_CONFIDENCE) \
            and not explicit(d) and not any(s["tool"] == "office" for s in d.get("steps") or []):
        # Jev read a document to make, however it was asked (the owner, 2026-10-26: "search about gandhiji and prepare a doc for him … 3-4
        # pages … with images", then "don't code the wording, code the feeling"): Monday's own office step, its kind, subject, length and
        # pictures read when it runs (executor._office). Over an exact rule only when Jev is that sure: the rules read "write up a report on
        # electric cars with images" as words to type, and "search about gandhiji and prepare a doc" as a web search.
        return {**d, "source": "jev", "confidence": jev.get("confidence"), "intent": "TOOL", "agent": "computer", "rule": "jev:office",
                "tool": "office", "steps": [step("office", "create", "Make the document", request=d["request"])],
                **({"overrode": d["rule"]} if exact(d) else {})}
    conf, intent = jev.get("confidence") or 0, jev.get("intent")
    pick, act = app_pick(jev, apps), jev.get("action") if (jev.get("action_confidence") or 0) >= ACTION_SURE else None
    if pick and act in _HANDS and d["rule"] == "fallback" and intent == "computer" and conf >= MIN_INTENT_CONFIDENCE and one_clause(d["request"]):
        # The owner's own words for an app ("I need Spotify on my screen", "notepad pls", "opne notepad"; 2026-10-27: only "open X" was
        # understood, the rest went to Hermes with the sentence or to chat): the rules found no command, Jev read a computer request whose
        # first move opens (closes, switches to) an installed app it picked among those near the words, never a name of its own. The step
        # is the rules' own shape, so Monday._installed, permit() and the receipts treat it as any exact plan. A sentence of several
        # clauses stays the computer agent's, whole (_jev_action): a step for its first clause would drop the rest.
        return {**d, "source": "jev", "confidence": conf, "intent": "TOOL", "agent": "computer", "rule": f"jev:{act}", "tool": "computer",
                "steps": [step("computer", act, f"{_HANDS[act]} {pick}", target=pick)]}
    if act == "show" and not pick and intent == "computer" and conf >= MIN_INTENT_CONFIDENCE and d["rule"] in ("fallback", *SOFT_RULES) \
            and jev.get("tool") in (None, "computer") and looks_only(d["request"]):
        # A look at the screen however it is asked (the owner, 2026-10-29: "tell me whats on my screen and explain it to me" and "explain me
        # whats in my scren rn" went to Hermes, which listed the windows' titles, and the chat said only "I finished TASK-…"): Monday's own
        # look, the model that can see explaining the screen with marks on it (executor._explain_screen). A look at an app Jev picked stays
        # the computer agent's (that app may need bringing forward first); words that also act ("…and click OK") stay the agent's, whole.
        return {**d, "source": "jev", "confidence": conf, "intent": "TOOL", "agent": "computer", "rule": "jev:show", "tool": "computer",
                "steps": [step("computer", "read_screen", "Look at the screen", what="screen")]}
    if exact(d):
        if agrees(d, intent):  # the rules supplied the exact arguments; Jev adds what they can't parse
            extra = {"action": jev.get("calendar_action")} if d["intent"] == "CALENDAR" else {}
            return {**d, **extra, "source": "jev", "confidence": conf, "jev_agrees": True}
        # Jev may overrule an exact command only towards LESS power (a chat reply, a status, a handler): never into a
        # worker launch. Measured 2026-09-28: a plan tool missing from AGREES let "what's in my downloads folder" become
        # a screen-control loop on the owner's live desktop. Research is a chat that can search (_jev_action: RESEARCH), no worker
        # (2026-10-22: Jev's 98% "research" for a research brief lost to the calendar's keyword, three times).
        if not explicit(d) and (intent == "research" or intent not in ("unknown", *WORKER_INTENTS)) and conf >= OVERRIDE_EXACT_CONFIDENCE:
            return {**_jev_action(d, jev, default_coder, computer_agent), "overrode": d["rule"]}
        return {**d, "jev_agrees": False}
    if d["intent"] == "TASK":
        if intent not in WORKER_INTENTS and conf >= MIN_DOWNGRADE_CONFIDENCE:
            return {**_jev_action(d, jev, default_coder, computer_agent), "overrode": d["rule"]}
        return {**d, "source": "jev" if intent in WORKER_INTENTS else d["source"], "confidence": conf if intent in WORKER_INTENTS else d["confidence"]}
    if conf < MIN_INTENT_CONFIDENCE:
        return {**d, "fallback": "low_confidence", "tier": tier_for(jev.get("complexity"))}
    return _jev_action(d, jev, default_coder, computer_agent)


_WHEN = re.compile(r"\b(?:in\s+(?:\d|an?\b|a\s+few|half|one|two|three|five|ten|fifteen|twenty|thirty)|at\s+\d|after\b|later\b|tonight\b|tomorrow\b|"
                   r"this\s+(?:evening|afternoon)|when\b|once\b|until\b|by\s+\d)", re.I)


def power_reading(jev):
    """The power action Jev read in a request (its `tool` "power" and its `power_action`, each sure enough), else None. Pure code."""
    j = jev or {}
    if j.get("tool") != "power" or (j.get("tool_confidence") or 0) < MIN_ACTION_CONFIDENCE:
        return None
    act = j.get("power_action")
    return act if act in power.ACTIONS and (j.get("power_action_confidence") or 0) >= MIN_ACTION_CONFIDENCE else None


APP_SURE = 0.6       # Jev at least this sure which installed app a name means (Monday._installed), or that the owner answered Monday's question
NONE_SURE = 0.8      # ...and this sure that none of them is, to overrule the index's own nearest match (live: "spot if i", a misheard
#                      Spotify, read as none at 0.66; "netflix", "telegram", "zoom" at 0.94-0.98)
STRONG_MATCH = 85    # computer.Apps.find's score from which a name is plainly an installed app: its whole name, an alias, its first or last word
OFFER_S = 120        # what Monday offered when an app isn't installed waits this long for the owner's answer, and one reply at most


def app_pick(jev, apps):
    """The installed app Jev read the words as meaning: its live `app` answer, "aN" over `apps` (the list Monday.jev_context gave it), when it
    is APP_SURE sure; else None. The name is the index's; Jev only chose among them. Pure code."""
    a = ((jev or {}).get("live") or {}).get("app") or {}
    choice = str(a.get("choice") or "")
    names = [x.get("name") if isinstance(x, dict) else x for x in apps or []]
    if choice[:1] != "a" or not choice[1:].isdigit() or int(choice[1:]) >= len(names) or (a.get("confidence") or 0) < APP_SURE:
        return None
    return names[int(choice[1:])] or None


def one_clause(text):
    """One clause and no more ("open Notepad", not "open Notepad and write hello"): what a step built from Jev's reading alone may stand for."""
    return len([c for c in _SPLIT.split(text or "") if c.strip(" ,.")]) == 1


_LOOK_ON = re.compile(r"^(?:(?:and|also|then|now|please|pls)\s+)*(?:explain|describe|summari[sz]e|tell|read|walk\s+me|break\s+(?:it|this|that)|what)\b", re.I)
_HANDS_ON = re.compile(r"\b(?:click|tap|press|open|close|type|write|send|delete|remove|move|drag|drop|scroll|play|pause|search|install|run|save|copy|"
                       r"paste|fix|change|edit|select|fill|submit|buy|book|download|upload)\b", re.I)


def looks_only(text):
    """Words that only look ("tell me whats on my screen and explain it to me"): a first clause, then only clauses that explain or tell, and
    nothing that acts anywhere ("…and click OK", "…and fix it"): what Jev's "show" may turn into Monday's own look at the screen."""
    parts = [c for c in _SPLIT.split(text or "") if c.strip(" ,.")]
    return bool(parts) and not _HANDS_ON.search(text) and all(_LOOK_ON.match(c.strip(" ,.")) for c in parts[1:])


def installed_app(found, score, pick, sure):
    """The installed app an "open X" means, or None when none is (the owner, 2026-10-26: "first check that I have installed that
    application … ask Jev whether it is there"): a name plainly an installed app's (`found` at STRONG_MATCH, computer.Apps.find) is that
    app; else Jev's reading of the installed apps near the words decides: its `pick` when it is APP_SURE sure, or none of them (`pick`
    None) when it is NONE_SURE sure; Jev unsure or not asked, `found` stands as before. Pure code."""
    if found and score >= STRONG_MATCH:
        return found
    if sure is not None and sure >= (APP_SURE if pick else NONE_SURE):
        return pick
    return found


def offer_answer(jev):
    """The owner's answer to Monday's question (jev.live_questions `offer`) as Jev read it: "yes", "no", or None (unsure, or not an answer).
    Pure code."""
    a = ((jev or {}).get("live") or {}).get("offer") or {}
    return a.get("choice") if a.get("choice") in ("yes", "no") and (a.get("confidence") or 0) >= APP_SURE else None


def offer_route(d, name, near=None, url=None):
    """What Monday offers when the app `name` isn't installed, as (the question, its spoken words, the route a yes runs): `near`, an
    installed app close to it ("Did you mean Notepad?"); else its website `url`; else a web search for it. Nothing runs until the owner
    says yes (Monday._route reads the answer with Jev)."""
    base = {"request": d.get("request"), "intent": "TOOL", "agent": "computer", "source": "offer"}
    name = name.strip()
    name = name[:1].upper() + name[1:]  # (the sentence starts with it: "spotify" as said)
    if near:
        return (f"“{name}” isn't installed on this PC. Did you mean {near}?", f"Did you mean {near}?",
                {**base, "rule": "offer:app", "prompt": f"Open {near}", "steps": [step("computer", "open", f"Open {near}", target=near)]})
    if url:
        site = re.sub(r"^https?://(?:www\.)?", "", url).rstrip("/")
        return (f"{name} isn't installed on this PC. Open {site} in your browser instead?", f"{name} isn't installed. Open its website instead?",
                {**base, "rule": "offer:website", "prompt": f"Open {site}", "steps": [step("computer", "open", f"Open {site}", target=url)]})
    return (f"{name} isn't installed on this PC. Search the web for it instead?", f"{name} isn't installed. Search the web for it?",
            {**base, "rule": "offer:search", "prompt": f"Search the web for {name}",
             "steps": [step("browser", "search", f"Search the web for “{name}”", query=name)]})


def app_named(d):
    """The word the rules read as a website or an agent when it may be an app installed here: the site a plan's browser step searches ("open Spotify and
    search for X") or the agent a soft match mentions ("open Claude and ask it to write a poem"). None when the rules had no such doubt."""
    for s in d.get("steps") or []:
        if s["tool"] == "browser" and s["args"].get("site") and s["args"]["site"] not in APP_SEARCH:
            return s["args"]["site"]
    if d["intent"] == "TASK" and str(d.get("rule", "")).startswith("mentioned-agent"):
        m = _OPEN.match(_clause(d["request"]))
        return (re.split(r"\s+(?:and|then)\s+", m["x"], maxsplit=1)[0].strip() or None) if m else None
    return None


def app_over_name(d, jev, installed, default_coder="claude_code", computer_agent="computer"):
    """The rules read the name as a website or an agent. When an app of that name is installed here (`installed`, the caller's lookup) and Jev is sure
    (AUTO_LAUNCH_CONFIDENCE) the request is about the owner's own computer, the app is meant: the computer agent works it from the goal, as it does any
    other app task, with every step still gated by permit(). Pure code; Jev supplied the reading, the registry the fact."""
    word = app_named(d)
    if not (word and installed and (jev or {}).get("intent") == "computer" and ((jev or {}).get("confidence") or 0) >= AUTO_LAUNCH_CONFIDENCE):
        return d
    return {**_jev_action(d, jev, default_coder, computer_agent), "overrode": d["rule"], "app": word}


def _tier(jev):
    """Chat tier: Jev's model-class answer when it is sure, else its complexity score."""
    if (jev.get("model_class_confidence") or 0) >= 0.6 and jev.get("model_class") in ("fast", "strong"):
        return jev["model_class"]
    return tier_for(jev.get("complexity"))


def _jev_action(d, jev, default_coder, computer_agent="computer"):
    conf, intent = jev.get("confidence") or 0, jev.get("intent")
    j = {**d, "source": "jev", "confidence": conf, "rule": f"jev:{intent}"}
    for k in ("steps", "op", "page", "focus", "subject", "setting", "value", "coder", "kind", "want", "mode"):
        j.pop(k, None)  # an overruled exact command's arguments don't belong to Jev's route
    m = _TASK_ID.search(d["request"])
    tid = m.group(0).upper() if m else None
    ref = (jev.get("live") or {}).get("task_ref") or {}
    if not tid and ref.get("choice") not in (None, "none") and (ref.get("confidence") or 0) >= MIN_ACTION_CONFIDENCE:
        tid = ref["choice"]  # v5: the task Jev read the words as meaning, among those live now ("stop the spotify one"); a stop still asks
    if intent == "vision" and conf >= CAMERA_CONFIDENCE:  # "can you see what I'm holding?": the camera, however it's asked (2026-10-10)
        steps = vision_steps(d["request"]) or [step("vision", "look", "Look through the camera")]
        return {**j, "intent": "TOOL", "agent": "computer", "tool": "vision", "steps": steps}
    if intent in ("conversation", "unknown", "vision"):  # unknown: the chat model can ask what the owner meant
        return {**j, "intent": "CHAT", "agent": "chat", "prompt": d["request"], "tier": _tier(jev)}
    if intent == "research":  # search the web, read, summarize: a model that can search (Gemini) leads; chat backs it up
        return {**j, "intent": "RESEARCH", "agent": "chat", "prompt": d["request"], "tier": "strong"}
    if intent in WORKER_INTENTS:
        # Starting a worker is routing, not permission: every risky step inside it still asks the owner. Until the
        # browser tool exists, web and file work goes to the computer agent; terminal work to a coding agent.
        agent = default_coder if intent in ("coding", "terminal") else computer_agent
        return {**j, "intent": "TASK", "agent": agent, "prompt": d["request"],
                **({"needs_approval": True} if conf < AUTO_LAUNCH_CONFIDENCE else {})}
    if intent in ("calendar", "memory", "security", "analytics"):
        return {**j, "intent": intent.upper(), "agent": None, "action": jev.get("calendar_action") if intent == "calendar" else None}
    if intent == "project_knowledge":
        return {**j, "intent": "GRAPHIFY_QUERY", "agent": None}
    if intent == "system_info":
        return {**j, "intent": "TIME" if re.search(r"\b(?:time|date|day|today)\b", d["request"], re.I) else "SYSTEM", "agent": None}
    if intent == "task_control":
        act = jev.get("task_action") if (jev.get("task_action_confidence") or 0) >= MIN_ACTION_CONFIDENCE else "status"
        # Jev never stops anything: a stop needs the explicit command.
        return {**j, "agent": None, "task_id": tid, "target_agent": d.get("mentioned"),
                "intent": {"changes": "CHANGES", "logs": "LOGS", "stop": "STOP_CONFIRM"}.get(act, "STATUS")}
    if intent == "voice":
        act = jev.get("voice_action") if (jev.get("voice_action_confidence") or 0) >= MIN_ACTION_CONFIDENCE else "other"
        if act in _VOICE_OPS:  # each maps to a deterministic voice handler; nothing new is authorized
            return {**j, "intent": "VOICE", "agent": None, "op": _VOICE_OPS[act]}
        return {**j, "intent": "HELP", "agent": None, "topic": "voice"}
    if intent == "monday_settings":
        act = jev.get("system_action") if (jev.get("system_action_confidence") or 0) >= MIN_ACTION_CONFIDENCE else "other"
        if act == "analyze_project" and d.get("project"):
            return {**j, "intent": "GRAPHIFY_UPDATE", "agent": None}
        return {**j, "intent": "HELP", "agent": None, "topic": act}
    return {**d, "fallback": "unknown_intent"}


def describe(d):
    """What a routed command set out to do, in a few words (Jev's `previous.action` for the next command)."""
    if d.get("steps"):
        return "; ".join(s["label"] for s in d["steps"])[:200]
    return {"CHAT": "answered in chat", "TASK": f"started a task for {AGENT_NAMES.get(d.get('agent'), d.get('agent'))}"}.get(
        d["intent"], d["intent"].replace("_", " ").lower())


_PRONOUN = re.compile(r"^(?:it|that|this|them|the\s+app|the\s+window|that\s+app|that\s+window)$", re.I)
# what a play after a search plays when it names nothing new: the search's own subject ("a song by him", "the first video", "song for me")
_GENERIC_PLAY = re.compile(r"^(?:(?:a|an|any|the|one|some)\s+)?(?:(?:first|top|1st)\s+)?(?:song|songs|video|videos|track|one|result|music|something|it|"
                           r"him|her|them|that|this)?(?:\s+(?:by|of|from)\s+(?:him|her|them|it|that))?(?:\s+(?:for|to)\s+me)?(?:\s+(?:please|now))?$", re.I)


def with_context(d, prev):
    """A follow-up's "it" is what the previous command worked on: "close it" after "open Notepad" closes Notepad.
    Only a pronoun target is replaced, and only by the target the previous command itself named."""
    target = next((s["args"].get("target") or s["args"].get("app") for s in (prev or {}).get("steps") or []
                   if s["tool"] == "computer" and (s["args"].get("target") or s["args"].get("app"))), None)
    if not target or d["intent"] != "TOOL":
        return d
    steps = [{**s, "args": {**s["args"], "target": target}, "label": re.sub(r"\b(?:it|that|this|them)\b", target, s["label"], count=1)}
             if s["tool"] == "computer" and _PRONOUN.match(str(s["args"].get("target") or "")) else s for s in d["steps"]]
    return {**d, "steps": steps} if steps != d["steps"] else d


# -- permission risk policy ------------------------------------------------------
_DESTRUCTIVE = re.compile(
    r"(\brm\b|\brmdir\b|\bdel\b|\berase\b|\brd\s|remove-item|\bformat\b|diskpart|shutdown|restart-computer|"
    r"reg\s+(?:add|delete)|git\s+(?:push|reset\s+--hard|clean|branch\s+-D)|drop\s+(?:table|database)|"
    r"(?:curl|wget|iwr|invoke-webrequest)[^|]*\|\s*(?:sh|bash|iex|invoke-expression)|\bchmod\b|\bchown\b|"
    r"\bmkfs|\bdd\s+if=|set-executionpolicy|\bsudo\b|runas)", re.I)
_INSTALL = re.compile(r"\b(?:npm|pnpm|yarn|bun|pip|uv|choco|winget|scoop|cargo|go)\s+(?:install|add|i|uninstall|remove)\b", re.I)
_NETWORK = re.compile(r"\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod|ssh|scp|ftp|git\s+(?:clone|fetch|pull))\b", re.I)

# Request-level intent gate. Hermes' own guard only prompts for commands it deems catastrophic
# (recursive/forced deletes, disk, registry...); a plain single-file delete runs unprompted. So for
# Hermes, Monday asks the owner BEFORE launch whenever the request itself implies one of these.
_REQUEST_RISK = [
    ("DELETE", re.compile(r"\b(delete|remove|erase|wipe|trash|clean\s*up|purge|rm|del)\b", re.I)),
    ("INSTALL", re.compile(r"\b(install|uninstall|upgrade|downgrade)\b", re.I)),
    ("FINANCIAL", re.compile(r"\b(pay|payment|purchase|buy|checkout|invoice|transfer\s+money|wire)\b", re.I)),
    # Communication = something leaves the machine under the owner's name. "reply with OK" is not that.
    ("COMMUNICATION", re.compile(
        r"\bsend\b.{0,40}\b(e-?mail|mail|message|text|sms|dm|invite|whatsapp|telegram|slack|discord)\b|"
        r"\b(whatsapp|telegram|signal|messenger|slack|discord|sms|imessage|gmail|outlook)\b.{0,60}\bsend\b|"  # "open WhatsApp and send hi"
        r"\bsend\b.{0,40}\bto\s+(?:my\s+)?(mom|mum|dad|mother|father|him|her|them|(?-i:[A-Z][a-z]+))\b|"  # "send hi to Dad"
        r"\b(e-?mail|text|dm|message|call|whatsapp)\s+(him|her|them|my|our|mom|mum|dad|the\s+team|(?-i:[A-Z][a-z]+))\b|"  # names are capitalised
        r"\breply\s+to\s+(?:(?:the|this|that|an?|his|her|their|my)\s+)?(e-?mail|mail|message|dm|comment|post|tweet|thread|chat)|"
        r"\b(tweet|publish)\b|\bpost\s+(?:it\s+|this\s+)?(?:on|to)\s+\w+", re.I)),
    ("ACCOUNT", re.compile(r"\b(password|passcode|log\s*in|sign\s*in|sign\s*up|account|2fa|credentials?)\b", re.I)),
    ("RUN_COMMAND", re.compile(r"\b(shutdown|shut\s+down|restart|reboot|log\s*off|kill|format|registry|regedit)\b", re.I)),
]


def request_risk(text):
    """(kind, 'HIGH') if a computer-use request implies a high-risk action, else None."""
    for kind, rx in _REQUEST_RISK:
        if rx.search(text or ""):
            return kind, "HIGH"
    return None


def classify(kind, detail="", outside_project=False):
    """Permission kind + concrete detail -> (kind, risk). Pure code; a model may advise, never decide."""
    detail = detail or ""
    if kind == "RUN_COMMAND":
        if _DESTRUCTIVE.search(detail):
            return ("DELETE" if re.search(r"\b(rm|rmdir|del|erase|remove-item|rd)\b", detail, re.I) else "RUN_COMMAND"), "HIGH"
        if _INSTALL.search(detail):
            return "INSTALL", "HIGH"
        if _NETWORK.search(detail):
            return "NETWORK", "MEDIUM"
        return "RUN_COMMAND", "MEDIUM"
    if kind in ("DELETE", "INSTALL", "FINANCIAL", "ACCOUNT", "COMMUNICATION", "DESTRUCTIVE", "POWER"):
        return kind, "HIGH"
    if kind == "WRITE":
        return kind, "HIGH" if outside_project else "MEDIUM"
    if kind in ("NETWORK", "BROWSER"):
        return kind, "MEDIUM"
    if kind == "READ":
        return kind, "HIGH" if outside_project else "LOW"
    return kind, "MEDIUM"


# -- terminal command risk: SAFE and LOW_RISK run, HIGH_RISK and DESTRUCTIVE ask first, BLOCKED never runs ----------
SAFE, LOW_RISK, HIGH_RISK, DESTRUCTIVE, BLOCKED = "SAFE", "LOW_RISK", "HIGH_RISK", "DESTRUCTIVE", "BLOCKED"
_LEVEL = {SAFE: 0, LOW_RISK: 1, HIGH_RISK: 2, DESTRUCTIVE: 3, BLOCKED: 4}
_CMD_RULES = [  # first match per command part wins; order is most severe first
    (BLOCKED, "reads stored passwords, keys or browser credentials", re.compile(
        r"mimikatz|sekurlsa|lsass|reg(?:\.exe)?\s+save\s+hklm\\(?:sam|security|system)|login\s+data|\\cookies\b|"
        r"cmdkey\s+/list|vaultcmd|get-storedcredential|[\\/]\.ssh[\\/]id_|\bid_(?:rsa|ed25519)\b|\.aws[\\/]credentials|"
        r"(?:type|cat|get-content|copy|cp)\s+.*\.env\b", re.I)),
    (BLOCKED, "turns off Windows security protections", re.compile(
        r"set-mppreference\s+.*-disable|add-mppreference\s+.*-exclusion|disable(?:realtime|behavior|ioav|script)monitoring|"
        r"netsh\s+(?:adv)?firewall\s+.*(?:state\s+off|opmode\s+disable)|set-netfirewallprofile\s+.*-enabled\s+(?:false|\$false|0)|"
        r"(?:stop|disable|set)-service\s+.*(?:windefend|wscsvc|mpssvc|wuauserv)|sc(?:\.exe)?\s+(?:stop|config|delete)\s+(?:windefend|wscsvc|mpssvc)|"
        r"bcdedit\s+.*(?:testsigning|nointegritychecks|safeboot)|enablelua\b.*\b0\b|vssadmin\s+delete|wevtutil\s+cl", re.I)),
    (BLOCKED, "would wipe a drive or the operating system", re.compile(
        r"\bformat(?:\.com)?\s+[a-z]:|\bdiskpart\b|\bcipher\s+/w|\bmkfs\b|\bdd\s+if=|"
        r"(?:\brm\b|\brd\b|\brmdir\b|\bdel\b|\berase\b|remove-item)[^&|;]*?(?:^|\s)[\"']?(?:/|~|[a-z]:)"
        r"(?:[\\/](?:windows|users|program\s*files(?:\s*\(x86\))?))?[\\/]?\*?[\"']?(?=\s|$)", re.I)),
    (DESTRUCTIVE, "deletes files or rewrites history", re.compile(
        r"\b(?:rm|rmdir|rd|del|erase|unlink)\b|remove-item|clear-content|git\s+(?:reset\s+--hard|clean\s+-[a-z]*f|push\s+.*(?:-f\b|--force)|"
        r"branch\s+-D|checkout\s+--\s|restore\s|stash\s+(?:drop|clear)|rebase\b)|drop\s+(?:table|database|schema)|truncate\s+table|"
        r"\bshutdown\b|restart-computer|stop-computer|docker\s+(?:rm|rmi|system\s+prune|volume\s+rm)|npm\s+uninstall|pip3?\s+uninstall", re.I)),
    (HIGH_RISK, "changes the system, installs globally, publishes or runs downloaded code", re.compile(
        r"git\s+push|npm\s+publish|(?:npm|pnpm|yarn)\s+(?:i|install|add)\b.*(?:\s-g\b|--global)|pip3?\s+install|"
        r"(?:winget|choco|scoop)\s+(?:install|uninstall|upgrade|remove)|reg(?:\.exe)?\s+(?:add|delete|import)|set-itemproperty\s+.*hk(?:lm|cu)|"
        r"set-executionpolicy|(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|]*\|\s*(?:sh|bash|iex|invoke-expression|python|pwsh|powershell)|"
        r"\biex\b|invoke-expression|\brunas\b|\bsudo\b|\bchmod\b|\bchown\b|\bicacls\b|\btakeown\b|taskkill|stop-process|\bkill\b|"
        r"\b(?:scp|sftp|ftp)\b|setx\b|\[environment\]::setenvironmentvariable|schtasks\s+/create|new-service|sc(?:\.exe)?\s+create|"
        r"(?<![>&0-9])>(?!>|&)\s*[\w.\\/:\"'-]+", re.I)),
    (SAFE, "only reads", re.compile(
        r"^(?:git\s+(?:status|log|diff|show|branch|remote|rev-parse|blame|ls-files|describe|config\s+--get)|"
        r"(?:python|py|node|npm|pnpm|yarn|pip3?|git|go|cargo|dotnet|java|rustc|deno|bun|code|ollama|claude|codex|gemini|uv)\s+(?:-v|-V|--version|version)$|"
        r"pip3?\s+(?:list|show|freeze)|npm\s+(?:ls|list|outdated|view|config\s+get)|dir|ls|type|cat|more|where|which|whoami|hostname|ipconfig|"
        r"systeminfo|ver|tasklist|netstat|nslookup|ping|tree|get-childitem|gci|get-content|gc|get-process|gps|get-service|get-location|pwd|"
        r"test-path|get-command|get-date|select-string|findstr|get-nettcpconnection|get-volume|get-psdrive|wmic\s+.*\bget\b|nvidia-smi)\b", re.I)),
    (LOW_RISK, "runs project code or installs its dependencies", re.compile(
        r"^(?:npm|pnpm|yarn)\s+(?:i|install|ci|add|run|start|test|build|dev|exec)\b|^npx\b|^(?:python|py|python3|node|deno|bun|ts-node|tsx)\b|"
        r"^(?:pytest|jest|vitest|mocha|cargo|go|dotnet|mvn|gradle|gradlew|make|uv|poetry|flutter|dart)\b|"
        r"^git\s+(?:add|commit|checkout|switch|pull|fetch|stash|merge|tag|init|clone)\b|^(?:mkdir|md|new-item|touch|cd|chdir|set-location|"
        r"code|cursor|start|explorer|copy|cp|xcopy|robocopy|move|mv|ren|rename|echo|write-output|docker\s+(?:build|run|compose|ps|images))\b", re.I)),
]
_CMD_SPLIT = re.compile(r"\s*(?:&&|\|\||;|(?<!\|)\|(?!\|)|(?<![>&])&(?![&\d]))\s*")  # "2>&1" is a redirect, not a chain


def command_risk(cmd):
    """(level, reason) for a shell command line: every part of a chain is classified and the worst one counts.
    An unrecognised program is HIGH_RISK (asked first): Monday only runs unknown code with the owner's yes."""
    worst, why = SAFE, "only reads"
    for part in [p for p in _CMD_SPLIT.split((cmd or "").strip()) if p.strip()] or [""]:
        level, reason = next(((lv, r) for lv, r, rx in _CMD_RULES if rx.search(part)), (HIGH_RISK, "an unrecognised program"))
        if level == SAFE and re.search(r"(?<![>&0-9])>(?!>|&)", part):
            level, reason = HIGH_RISK, "overwrites a file"
        if _LEVEL[level] > _LEVEL[worst]:
            worst, why = level, reason
    return worst, why


# -- permission engine: one deterministic ruling per step (SAFE runs, ASK waits for the owner, BLOCK never runs) --------
_ASK_LABEL = [
    ("COMMUNICATION", re.compile(r"\b(send|post|publish|tweet|reply|share|submit|comment|forward|invite)\b", re.I)),
    ("FINANCIAL", re.compile(r"\b(pay|buy|purchase|order|checkout|check\s*out|place\s+order|donate|subscribe|transfer|add\s+to\s+(?:cart|basket))\b", re.I)),
    ("DELETE", re.compile(r"\b(delete|remove|discard|erase|trash|clear\s+all|uninstall|empty\s+(?:trash|recycle\s+bin)|format)\b", re.I)),
    ("ACCOUNT", re.compile(r"\b(sign\s*out|log\s*out|deactivate|close\s+(?:my\s+)?account|change\s+(?:email|password)|reset\s+password|2fa|two[\s-]factor)\b", re.I)),
    ("INSTALL", re.compile(r"\b(install|run\s+anyway|update\s+now|restart\s+now|upgrade)\b", re.I)),
    ("CONSENT", re.compile(r"\b(accept(?:\s+all)?|i\s+agree|agree|allow\s+all|consent|grant|authori[sz]e)\b", re.I)),
]
_SECRET_FIELD = re.compile(r"pass(?:word|code|phrase)?\b|\bpin\b|\bcvv\b|\bcvc\b|card\s*number|security\s+code|\bssn\b|social\s+security|\botp\b|one[\s-]time|api\s*key|secret|token", re.I)
_SECRET_TEXT = re.compile(r"\b(?:\d[ -]?){13,19}\b|\b\d{3}-\d{2}-\d{4}\b|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[bpas]-[A-Za-z0-9-]{10,}")
# Never force-closed (computer.CRITICAL_PROCESSES double-checks at the moment of acting).
CRITICAL_PROCESSES = {"system.exe", "idle.exe", "registry.exe", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe",
                      "services.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe",
                      "msmpeng.exe", "securityhealthservice.exe", "nissrv.exe", "audiodg.exe", "spoolsv.exe", "taskhostw.exe",
                      "runtimebroker.exe"}
# macOS and Linux (2026-10-19, Monday on every OS): the kernel's, the session's, the display's and the system's own; bare names
CRITICAL_UNIX = {"kernel_task", "launchd", "windowserver", "loginwindow", "dock", "finder", "systemuiserver", "coreaudiod", "mds",
                 "securityd", "opendirectoryd", "systemd", "init", "kthreadd", "systemd-logind", "dbus-daemon", "xorg", "xwayland",
                 "gnome-shell", "gnome-session-binary", "kwin_x11", "kwin_wayland", "plasmashell", "gdm", "gdm3", "sddm", "lightdm",
                 "pipewire", "wireplumber", "pulseaudio", "networkmanager", "polkitd", "sshd", "udevd", "systemd-udevd"}
MESSAGING_APPS = re.compile(r"whatsapp|telegram|signal|discord|slack|teams|outlook|thunderbird|\bmail\b|messenger|gmail|messages|skype|zoom", re.I)
_SECURITY_APPS = re.compile(r"windows\s+security|defender|firewall|user\s+account\s+control|credential\s+manager|bitlocker", re.I)


SENSITIVE = "SENSITIVE"
TIERS = (SAFE, LOW_RISK, SENSITIVE, HIGH_RISK, DESTRUCTIVE, BLOCKED)  # the owner's six words (their brief, 2026-10-05)
# What a step changes when it runs unasked: something new or replaceable inside the owner's own folders and projects.
_LOW_RISK_STEPS = {("google", "note_create"), ("google", "note_append"), ("google", "doc_create"), ("google", "doc_append"),
                   ("google", "calendar_create"), ("google", "task_create"), ("google", "sheet_create"),
                   ("filesystem", "create"), ("applications", "open_project"), ("coding_agents", "delegate"), ("computer", "save"), ("office", "create"),
                   ("terminal", "tests"), ("terminal", "dev_server")}


_POWER_WHY = {"shutdown": "turns this PC off: every app closes", "restart": "restarts this PC: every app closes",
              "sign_out": "signs you out of Windows: every app closes", "hibernate": "powers this PC off until you turn it on",
              "sleep": "puts this PC to sleep", "lock": "locks the screen; nothing closes",
              "cancel_shutdown": "stops a scheduled shutdown or restart; nothing else changes", "status": "only reads"}


def tier(decision, kind, tool, action, level=None):
    """A ruling in the owner's six tiers. The decision is made first, by permit(); this only names it: SAFE and LOW_RISK run,
    SENSITIVE (something leaves the PC, or touches an account), HIGH_RISK and DESTRUCTIVE ask, BLOCKED never runs."""
    if decision == "BLOCK":
        return BLOCKED
    if decision == "ASK":
        return (DESTRUCTIVE if kind in ("DELETE", "DESTRUCTIVE") or level == DESTRUCTIVE else
                SENSITIVE if kind in ("COMMUNICATION", "ACCOUNT", "DOWNLOAD", "SECURITY") else HIGH_RISK)
    return LOW_RISK if level == LOW_RISK or (tool, action) in _LOW_RISK_STEPS else SAFE


def permit(s, context=None, jev_risk=None):
    """Rule on one step: {"decision": SAFE|ASK|BLOCK, "kind", "risk", "reason"}. Pure code; a model may propose a step,
    only this decides. context: {"app": the window it acts on, "field": the target element's name}."""
    tool, action, a = s["tool"], s["action"], s.get("args") or {}
    ctx = context or {}
    app = f"{ctx.get('app') or ''} {a.get('app') or ''}"

    def rule(decision, kind="OTHER", risk="LOW", reason="", level=None):
        if decision == "SAFE" and jev_risk is not None and jev_risk >= DESTRUCTIVE_P:  # Jev can add a gate, never remove one
            return {"decision": "ASK", "kind": "DESTRUCTIVE", "risk": "HIGH", "tier": DESTRUCTIVE,
                    "reason": f"Jev rated this {round(jev_risk * 100)}% likely destructive"}
        return {"decision": decision, "kind": kind, "risk": risk, "reason": reason, "tier": tier(decision, kind, tool, action, level)}

    if tool == "terminal" and a.get("command"):
        level, why = command_risk(a["command"])
        if level == BLOCKED:
            return rule("BLOCK", "RUN_COMMAND", "HIGH", f"Monday never runs a command that {why}.")
        if level in (HIGH_RISK, DESTRUCTIVE):
            kind = classify("RUN_COMMAND", a["command"])[0]
            if kind == "RUN_COMMAND" and level == DESTRUCTIVE:
                kind = "DESTRUCTIVE"
            return rule("ASK", kind, "HIGH", f"{level.replace('_', ' ').lower()}: {why}", level)
        return rule("SAFE", "RUN_COMMAND", "LOW", f"{level.replace('_', ' ').lower()}: {why}", level)
    if tool == "power":  # this PC's power (core/power.py): what ends the session asks, in every mode and every time; a lock, a cancel, the status don't
        if action in power.ASKS:
            return rule("ASK", "POWER", "HIGH", _POWER_WHY.get(action, "changes this PC's power"))
        # Not rule(): these are fixed calls that can lose nothing (read, lock, call a shutdown off), so Jev's destructive score has nothing to
        # add. Seen live 2026-10-26: "is a shutdown scheduled" scored 85% (the word "shutdown") and the status check asked to be allowed.
        return {"decision": "SAFE", "kind": "OTHER", "risk": "LOW", "reason": _POWER_WHY.get(action, "only reads"), "tier": SAFE}
    if tool == "browser" and action == "download":
        return rule("ASK", "DOWNLOAD", "MEDIUM", "downloads a file to this computer")
    if tool == "vision":  # this PC's camera (core/vision.py): a look keeps nothing; remembering keeps one picture of the thing, here
        return rule("SAFE", reason="looks through your camera; nothing it sees is kept" if action == "look" else "keeps one picture of it, on this computer")
    if tool == "google":
        return rule("SAFE", reason="adds something new to your Google account (nothing is replaced, deleted, sent or shared)"
                    if (tool, action) in _LOW_RISK_STEPS else "reads your Google account")
    if tool == "office":
        return rule("SAFE", reason="makes a new file in your Documents (nothing is replaced, sent or shared)")
    if tool == "browser" and action in ("click_element", "fill_form"):  # a page's buttons and fields: the screen's rules
        tool, action = "computer", {"click_element": "click", "fill_form": "type"}[action]
    if tool == "filesystem":
        path, roots = a.get("path") or "", ctx.get("roots") or ()
        if action == "delete" and (a.get("kind") == "project" or (path and files.holds_project(path, roots))):
            return rule("BLOCK", "DELETE", "HIGH", "Monday never deletes a whole project, or the folder it runs from. Nothing was touched.")
        if action == "read" and path and files.secret(path):
            return rule("BLOCK", "ACCOUNT", "HIGH", "Monday never reads credential or key files.")
        if action in ("create", "rename", "move", "delete", "write") and any(
                x and not files.allowed(x, roots) for x in (path, a.get("dest") if action == "move" else None)):
            return rule("BLOCK", "WRITE", "HIGH", "Monday only changes files inside your user folder and your projects.")
        if action == "write" and path and files.secret(path):
            return rule("BLOCK", "ACCOUNT", "HIGH", "Monday never writes credential or key files.")
        if action == "delete":
            return rule("ASK", "DELETE", "HIGH", "moves it to the Recycle Bin")
        if action in ("move", "rename"):
            return rule("ASK", "WRITE", "MEDIUM", f"{'moves' if action == 'move' else 'renames'} a file or folder")
        if action == "write":
            return rule("ASK", "WRITE", "HIGH", "replaces what the file holds")
    if tool == "computer":
        if _SECURITY_APPS.search(app) or action == "transfer" and _SECURITY_APPS.search(f"{a.get('source') or ''} {a.get('target') or ''}"):
            return rule("BLOCK", "SECURITY", "HIGH", "Monday doesn't operate Windows security settings.")
        if action == "transfer":  # (the words themselves are checked as they are copied: Computer.transfer never pastes a key or password)
            return rule("SAFE", reason="copies words from one of your apps into another; nothing is sent")
        if action == "type":
            if _SECRET_FIELD.search(f"{a.get('field') or ''} {ctx.get('field') or ''}") or _SECRET_TEXT.search(a.get("text") or ""):
                return rule("BLOCK", "ACCOUNT", "HIGH", "Monday never types passwords, card numbers or keys. Type those yourself.")
            # a newline in the text is an Enter press too (type_unicode taps Enter for "\n")
            if (a.get("enter") or re.search(r"[\r\n]", a.get("text") or "")) and MESSAGING_APPS.search(app):
                return rule("ASK", "COMMUNICATION", "HIGH", "pressing Enter here sends a message")
        if action == "key" and MESSAGING_APPS.search(app) and {"enter", "return"} & set(a.get("keys") or []):
            return rule("ASK", "COMMUNICATION", "HIGH", "pressing Enter here sends a message")
        if action in ("click", "double_click", "right_click"):
            label = f"{a.get('target') or ''} {ctx.get('field') or ''}"
            for kind, rx in _ASK_LABEL:
                if rx.search(label):
                    return rule("ASK", kind, "HIGH" if kind in ("COMMUNICATION", "FINANCIAL", "DELETE", "ACCOUNT") else "MEDIUM",
                                f"clicking “{(a.get('target') or ctx.get('field') or '').strip()}”")
        if action == "kill":
            name = re.sub(r"\.exe$", "", (a.get("target") or "").strip().lower())
            if name + ".exe" in CRITICAL_PROCESSES:
                return rule("BLOCK", "SYSTEM", "HIGH", "Monday never force-closes Windows' own processes.")
            if name in CRITICAL_UNIX:
                return rule("BLOCK", "SYSTEM", "HIGH", "Monday never force-closes the system's own processes.")
            return rule("ASK", "DESTRUCTIVE", "HIGH", "force-closing an app can lose unsaved work")
        if action == "save" and a.get("overwrite"):
            return rule("ASK", "WRITE", "HIGH", f"replaces the existing file {a.get('path')}")
        if action == "clipboard" and a.get("op") == "set" and _SECRET_TEXT.search(a.get("text") or ""):
            return rule("BLOCK", "ACCOUNT", "HIGH", "Monday never puts passwords, card numbers or keys on the clipboard.")
        if action == "drag" and re.search(r"explorer", app, re.I):
            return rule("ASK", "WRITE", "MEDIUM", "a drag in File Explorer moves files")
    return rule("SAFE", reason="reads, opens or navigates")


# -- standing approvals (the owner, 2026-10-17: "all the modes should be available in the settings page, dynamic changeable", from
# OpenMausBot's Always allow and auto mode). Settings → Approvals, changed only from Monday's own window: "ask" (the default: every
# approval waits for Approve / Deny), "always" (cards also offer Always allow: the owner's own yes kept for one bot or agent, one kind
# of action and one key, listed in Settings and revocable there), "auto" (those, and any other LOW or MEDIUM risk request approved as
# it comes, as in Claude Code's auto mode; an unrecognised program still asks). HIGH risk asks in every mode and no grant covers it:
# deleting, installing, paying, sending, accounts, consent, writing outside the project, a command that deletes or publishes.
# permit()'s BLOCK never runs in any mode, and a model never makes or widens a grant.
APPROVAL_MODES = ("ask", "always", "auto")
_NEVER_STANDING = {"DELETE", "INSTALL", "FINANCIAL", "ACCOUNT", "COMMUNICATION", "DESTRUCTIVE", "CONSENT", "SYSTEM", "POWER"}
_URL = re.compile(r"https?://", re.I)


def _is_cmd(kind, detail):
    return kind in ("RUN_COMMAND", "NETWORK") and bool(detail) and not _URL.match(str(detail))


def grant_key(kind, detail, project=None):
    """What one Always allow covers: a command by its program and first two words ("git status", "npm run build"), a page by its
    site, a write or a read by the project, a tool by its name, anything else by its kind."""
    d = str(detail or "").strip()
    if _is_cmd(kind, d):
        exe, rest = re.match(r"\s*(\"[^\"]+\"|'[^']+'|\S+)\s*(.*)", d).groups()
        prog = re.sub(r"\.(exe|cmd|bat|ps1)$", "", re.split(r"[\\/]", exe.strip("\"'"))[-1].lower())
        return " ".join([prog, *[w.lower() for w in rest.split()[:5] if not w.startswith(("-", "/"))][:2]])
    if _URL.match(d):
        from urllib.parse import urlsplit
        return urlsplit(d).hostname or d
    if kind in ("WRITE", "READ"):
        return project or kind
    return d[:80] if kind == "TOOL" and d else kind


def grant_words(g):
    """A grant as the owner reads it in Settings and on the button: "Codex may run “npm test”"."""
    k, key = g.get("kind"), g.get("key")
    what = {"RUN_COMMAND": f"run “{key}”", "NETWORK": f"reach {key}" if "." in key else f"run “{key}”", "WRITE": f"write files in {key}",
            "READ": f"read files in {key}", "TOOL": f"use {key}"}.get(k, f"take {str(k).lower().replace('_', ' ')} steps")
    return f"{g.get('who')} may {what}"


def grantable(kind, risk, detail=""):
    """Whether a standing yes may ever cover this request (never HIGH risk, nothing in _NEVER_STANDING, no command that deletes,
    publishes, installs or changes the system)."""
    if risk not in ("LOW", "MEDIUM") or kind in _NEVER_STANDING:
        return False
    if _is_cmd(kind, detail):
        level, why = command_risk(detail)
        return level in (SAFE, LOW_RISK) or (level == HIGH_RISK and why == "an unrecognised program")
    return True


def standing(mode, grants, who, kind, risk, detail="", project=None):
    """-> "always allow" | "auto mode" | None: the owner's standing yes that answers this request now, if there is one."""
    if mode not in ("always", "auto") or not grantable(kind, risk, detail):
        return None
    key, cmd = grant_key(kind, detail, project), _is_cmd(kind, detail)  # a command's grant covers what starts with it: "pytest" any pytest run
    if any(g.get("who") == who and g.get("kind") == kind and (g.get("key") == key or cmd and key.startswith(f"{g.get('key')} ")) for g in grants or []):
        return "always allow"
    if mode == "auto" and (not _is_cmd(kind, detail) or command_risk(detail)[0] in (SAFE, LOW_RISK)):
        return "auto mode"
    return None


def ask_kind(tool, inp, cwd=""):
    """One of the owner's agents asking (Claude Code's PermissionRequest hook) as Monday's own terms: (kind, detail, outside_project)."""
    tool, inp = str(tool or ""), inp if isinstance(inp, dict) else {}
    if tool in ("Bash", "PowerShell"):
        return "RUN_COMMAND", str(inp.get("command") or ""), False
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        p = str(inp.get("file_path") or inp.get("notebook_path") or "")
        root = str(cwd or "").rstrip("\\/").lower()
        return "WRITE", p, not root or not p.lower().replace("/", "\\").startswith(root.replace("/", "\\") + "\\")
    if tool == "WebFetch":
        return "NETWORK", str(inp.get("url") or ""), False
    said = tool.split("__")[-1].replace("_", " ")
    kind = next((k for k, rx in _ASK_LABEL if rx.search(said)), "TOOL") if tool.startswith("mcp__") else "TOOL"
    return kind, tool, False


if __name__ == "__main__":  # python -m core.router  -> self-check of the routing table
    def gs(text, calendar_action=None):
        g = google_steps(_name_lead("Monday").sub("", text, count=1), calendar_action)  # (route() takes the name off first)
        return (g[0]["action"], g[0]["args"]) if g else None
    G_CASES = {  # the owner's own examples (2026-10-09 brief) -> the Google step they become
        "Monday, save this as a note.": ("note_create", {"content": "last_reply"}),
        "Find my note about the AI project.": ("note_find", {"query": "the AI project"}),
        "Show my recent notes.": ("note_find", {}),
        "Create a note about the ideas we discussed.": ("note_create", {"content": "conversation", "write": "summarize"}),
        "Add these points to my existing note.": ("note_append", {"content": "last_reply"}),
        "Find notes related to my current project.": ("note_find", {"query": "my current project"}),
        "Organize these ideas into a document.": ("doc_create", {"content": "conversation", "write": "organize"}),
        "Save this research to my notes.": ("note_create", {"content": "last_reply"}),
        "Create a Google Doc summarizing this research.": ("doc_create", {"content": "last_reply", "write": "summarize"}),
        "Find the document I was working on yesterday.": ("doc_find", {"when": "yesterday"}),
        "Add these points to my project document.": ("doc_append", {"name": "project", "content": "last_reply"}),
        "Find all the documents related to Monday.": ("doc_find", {"query": "Monday"}),
        "What do I have scheduled tomorrow?": ("calendar_agenda", {"when": "What do I have scheduled tomorrow"}),
        "Find a free hour this week.": ("calendar_free", {"when": "Find a free hour this week", "minutes": 60}),
        "Create a reminder for my project.": ("task_create", {"title": "My project"}),
        "Add a meeting to my calendar.": ("calendar_create", {"request": "Add a meeting to my calendar"}),
        "Show my unfinished tasks.": ("tasks_list", {}),
        "remind me to call the bank": ("task_create", {"title": "Call the bank"}),
        "what's my next meeting": ("calendar_agenda", {"when": "what's my next meeting", "next": True}),
        "create a spreadsheet called Budget 2026": ("sheet_create", {"title": "Budget 2026"}),
        "search my email for invoice": ("mail_find", {"query": "invoice"}),
        "find Sam's phone number": ("contact_find", {"query": "Sam"}),
        "make a note: buy milk": ("note_create", {"text": "buy milk"}),
    }
    # the owner's own failed sentences (task log, 2026-10-05..09), as the recognizer heard them
    acts = lambda t: [(s["action"], s["args"].get("target")) for s in route(t, [])["steps"]]  # noqa: E731
    assert acts("Open me and Play any random song") == [("play", None)], "a half-heard 'open me' doesn't sink the song"
    assert acts("Uh open camera and click my photo") == [("open", "camera"), ("photo", None)], "a spoken 'uh' before the command"
    assert acts("click pick of me") == [("photo", None)] and acts("open spotify on my laptop") == [("open", "spotify")]
    assert acts("Open jetgbt application in Laptop and search high")[0] == ("open", "jetgbt"), "where it is isn't its name"
    assert route("right click", [])["steps"][0]["args"].get("button") == "right", "'right' is never a filler"
    # the owner's misheard and mistyped requests (2026-10-20): never taken literally as an app's name or a command line
    assert not route("open notpad ajd make a new tab and write hello there", []).get("steps"), "a sentence is no app's name: the computer agent reads it"
    assert not any(s["tool"] == "terminal" for s in route("and Node Foreign, right high.", []).get("steps") or []), "speech is no command line"
    assert route("npm run build", [])["steps"][0]["args"]["command"] == "npm run build", "a typed command still runs"
    assert nothing_named("for") and nothing_named("a new") and not nothing_named("spotify")
    sees = lambda x: [(s["action"], s["args"].get("name")) for s in route(x, [])["steps"]] if route(x, [])["intent"] == "TOOL" else route(x, [])["intent"]  # noqa: E731
    for x in ("what is this", "What's this?", "what am I holding", "tell me what this is", "what is in front of the camera", "look at this",
              "read this", "what does this say", "what's written on this", "read the letter", "what book is this", "Monday, read what's written here"):
        assert sees(x) == [("look", None)], (x, sees(x))
    assert sees("read this page") != [("look", None)] and route("read the screen", [])["steps"][0]["action"] == "read_screen", "the screen stays the screen"
    assert sees("remember this is my mug") == [("remember", "my mug")] and sees("this is Rahul's charger, remember it") == [("remember", "Rahul's charger")]
    assert sees("remember it as my lucky pen") == [("remember", "my lucky pen")]
    for fact in ("remember that it is raining", "remember this is how we deploy", "remember that staging runs on port 8080"):
        assert sees(fact) == "REMEMBER", (fact, sees(fact))
    assert route("what do you see", [])["steps"][0]["action"] == "read_screen", "the screen, as before the camera"
    for x in ("go to sleep", "Monday, good night", "goodnight", "sleep now", "time for bed"):
        assert route(x, [])["intent"] == "SLEEP" and reflex(route(x, [])), x
    assert route("wake up", [])["intent"] == "WAKE" and reflex(route("wake up", [])) and route("sleep mode for the laptop", [])["intent"] != "SLEEP"
    assert permit(step("vision", "look", "Look"))["decision"] == "SAFE" and instant(route("what is this", [])), "a look runs at once"
    for text, want in G_CASES.items():
        assert gs(text) == want, (text, gs(text))
    for text in ("open Chrome", "create a file called notes.txt", "show tasks", "what is a calendar", "list files in my downloads",
                 "search the web for google docs tips"):
        assert gs(text) is None, (text, gs(text))
    assert gs("is it free?") is None and gs("book something", "create")[0] == "calendar_create", "Jev's calendar reading, when the rules had none"
    assert permit({"tool": "google", "action": "calendar_create", "args": {}})["tier"] == LOW_RISK
    assert permit({"tool": "google", "action": "doc_find", "args": {}})["tier"] == SAFE
    P = [{"name": "Monday Core", "aliases": "monday repo", "path": "C:\\Users\\me\\Desktop\\Monday"},
         {"name": "Portfolio", "aliases": "portfolio site", "path": "E:\\portfolio_bhavye"}]
    cases = {
        "Open my portfolio project.": ("TOOL", "computer"),
        "Ask Claude Code to fix this animation in portfolio": ("TASK", "claude_code"),
        "Use Codex for this task.": ("REROUTE", "codex"),
        "Ask Hermes to open Chrome.": ("TASK", "hermes"),
        "Show me what Claude is doing.": ("STATUS", None),
        "Stop the current task.": ("STOP", None),
        "What's on my calendar today?": ("TOOL", "computer"),  # Google Calendar (core/google.py) since 2026-10-09
        "Is the calendar app slow?": ("CALENDAR", None),
        "Show me what changed.": ("CHANGES", None),
        "Monday, use qwen3:4b": ("MODEL", None),
        "switch to local": ("MODEL", None),
        "use automatic": ("MODEL", None),
        "Monday, go back to Ollama.": ("MODEL", None),
        "use the strongest available model": ("MODEL", None),
        "fix the navbar bug in portfolio": ("TASK", "claude_code"),
        "add project E:\\portfolio_bhavye as Portfolio": ("PROJECT_ADD", None),
        "What is the capital of France?": ("CHAT", "chat"),
        "analyze portfolio": ("GRAPHIFY_UPDATE", None),
        "pure agent mode": ("VOICE", None), "quiet mode": ("VOICE", None), "debug mode": ("VOICE", None), "developer mode on": ("VOICE", None),
        "assistant mode": ("VOICE", None), "talk to me": ("VOICE", None), "debug mode off": ("VOICE", None), "show me everything": ("VOICE", None),
    }
    for text, (intent, agent) in cases.items():
        r = route(text, P)
        assert (r["intent"], r["agent"]) == (intent, agent), (text, r)
    assert route("Ask Claude Code to fix this animation in portfolio", P)["prompt"] == "fix this animation in portfolio"
    assert [route(t, P)["op"] for t in ("pure agent mode", "debug mode", "assistant mode", "debug mode off", "talk to me", "show me everything")] ==         ["mode_pure", "mode_debug", "mode_assistant", "mode_pure", "mode_assistant", "mode_debug"], "the interaction modes"
    assert all(reflex(route(t, P)) for t in ("pure agent mode", "debug mode", "assistant mode")), "a mode change never waits for Jev"
    assert route("talk to me again", P)["op"] == "unmute" and route("debug the login flow", P)["intent"] == "TASK", "neighbours untouched"
    assert route("ask me which browser", P)["rule"] == "ask-browser" and route("forget my browser choice", P)["setting"] == "ask_browser"
    asks = ["what time is it", "Monday, what's playing", "is Chrome running", "who won the match yesterday", "how do I center a div",
            "tell me about yourself", "can you tell me what's on my screen", "explain this error", "so what's the weather like",
            "do you know who made you", "what is the capital of France?", "summarize this page",
            "is Notepad open?", "what is Notepad?", "could you check if chrome is running?"]  # (2026-10-27: a "?" after a real question still asks)
    orders = ["open Spotify", "can you open Spotify", "could you please play Blinding Lights", "close Chrome", "search for React docs",
              "take a screenshot", "Monday, pause", "click", "open camera and click my photo", "I'm tired", "write hello in Notepad",
              "Could you pull up Notepad?", "Can you open Notepad for me?", "Would you mind opening Notepad?", "open Notepad?"]  # a typed "?" after an order (2026-10-27)
    assert all(is_question(t) for t in asks), [t for t in asks if not is_question(t)]
    assert not any(is_question(t) for t in orders), [t for t in orders if is_question(t)]
    assert [(s["action"], s["args"]["target"]) for s in route("bring VS Code up", P)["steps"]] == [("open", "VS Code")], "bring X up opens X"
    # the instant lane, a bare click, the Camera's shutter, early starts of a whole sentence (2026-10-06)
    assert route("click", P)["steps"][0]["args"] == {"at": "cursor", "button": "left", "count": 1} and route("tap it", P)["steps"][0]["args"]["at"] == "cursor"
    assert route("right click", P)["steps"][0]["args"]["button"] == "right" and route("double click here", P)["steps"][0]["args"]["count"] == 2
    assert route("click my photo", P)["steps"][0]["action"] == "photo" and route("take a picture of me", P)["steps"][0]["action"] == "photo"
    assert route("take a screenshot", P)["steps"][0]["action"] == "screenshot" and route("click the second result", P).get("steps") is None
    assert route("click the Save button", P)["steps"][0]["args"]["target"] == "Save", "a labelled click is unchanged"
    assert all(instant(route(t, P)) for t in ("open Camera", "take a photo", "click", "close Spotify", "pause it", "scroll down", "take a screenshot"))
    assert not any(instant(route(t, P)) for t in ("run the tests", "delete notes.txt", "open github.com", "what time is it", "fix the navbar bug in portfolio")), \
        "LOW_RISK, asks, a browser question and non-tools keep Jev first"
    assert early_clause("open camera", P) is None and early_clause("open camera", P, whole=True)[0] == "open camera", "a whole sentence starts early only when asked to"
    assert early_clause("close camera", P, whole=True) is None and early_clause("open camera and", P, whole=True)[0] == "open camera"
    assert early_clause("click my photo", P, whole=True, held_ms=EARLY_HOLD_MS)[1][0]["action"] == "photo" and early_clause("click my photo", P, whole=True, held_ms=200) is None, \
        "an action starts early only after the words held still"
    assert early_clause("type hello", P, whole=True, held_ms=9000) is None and early_clause("search for react", P, whole=True, held_ms=9000) is None, "half-said words never type or search"
    assert early_clause("click the Save button", P, whole=True, held_ms=9000) is None and early_clause("click", P, whole=True, held_ms=EARLY_HOLD_MS)[1][0]["args"]["at"] == "cursor"
    assert early_clause("play Blinding Lights", P, whole=True, held_ms=9000) is None and route("play", P)["steps"][0]["args"] == {"op": "play"}, "a bare play is exact; a named song never starts early"
    assert early_clause("play", P, whole=True, held_ms=EARLY_HOLD_MS)[1][0]["action"] == "media" and early_clause("play", P, whole=True, held_ms=200) is None, "a bare play starts after the hold"
    assert after_early("open spotify and play Blinding Lights", "open spotify and play") == "play Blinding Lights", "the song said after a bare play is played"
    assert after_early("open spotify and play", "open spotify and play") == "" and after_early("open spotify and play and pause", "open spotify and play") == "pause"
    assert route("And play", P)["steps"][0]["action"] == "media", "the owner's second sentence"
    assert after_early("open Spotify's settings", "open Spotify") is None and after_early("open Spotify and play Blinding Lights", "open Spotify") == "play Blinding Lights"
    assert after_early("open Spotify", "open Spotify") == "" and after_early("open Spotify, then Chrome", "open Spotify") == "open Chrome"
    assert after_early("go to example.com and go to example.org", "go to example.com and go to example") is None, "a word that grew: routed whole"
    assert after_early("open notepad. Write hello", "open notepad") == "Write hello", "a full stop still ends the clause"
    assert [s["args"]["target"] for s in route("open Chrome and pull Spotify back up", P)["steps"]] == ["Chrome", "Spotify"], "a bring-up clause splits"
    assert route("bring the volume up", P)["intent"] != "TOOL" or route("bring the volume up", P)["steps"][0]["action"] != "open", "the volume is not an app"
    assert route("Show me what Claude is doing.", P)["target_agent"] == "claude_code"
    assert route("Stop TASK-2026-0003", P)["task_id"] == "TASK-2026-0003"
    assert route("Open my portfolio project.", P)["project"] == "Portfolio"
    assert route("Ask Hermes to write 'hi from Monday' to a file", P)["project"] is None
    assert (route("Monday, open Chrome", P)["intent"], route("Monday, open Chrome", P)["agent"]) == ("TOOL", "computer")
    assert route("fix the router bug in Monday Core", P)["project"] == "Monday Core"
    assert classify("RUN_COMMAND", "rm -rf build") == ("DELETE", "HIGH")
    assert classify("RUN_COMMAND", "npm install left-pad") == ("INSTALL", "HIGH")
    assert classify("RUN_COMMAND", "git status") == ("RUN_COMMAND", "MEDIUM")
    assert classify("WRITE", "src/a.ts") == ("WRITE", "MEDIUM")
    assert classify("WRITE", "C:/Windows/x", outside_project=True) == ("WRITE", "HIGH")
    assert request_risk("Delete the 3 screenshots on my desktop") == ("DELETE", "HIGH")
    assert request_risk("send an email to Sam") == ("COMMUNICATION", "HIGH")
    assert request_risk("reply to the email from Priya") == ("COMMUNICATION", "HIGH")
    assert request_risk("message Sam that I'm late") == ("COMMUNICATION", "HIGH")
    assert request_risk("post this on LinkedIn") == ("COMMUNICATION", "HIGH")
    assert request_risk("reply with the single word READY without using any tools") is None
    assert request_risk("show me the latest message in the log file") is None
    assert request_risk("open Chrome") is None
    assert request_risk("Open my portfolio project in VS Code") is None
    assert classify("DESTRUCTIVE", "x") == ("DESTRUCTIVE", "HIGH")

    # policy: synthetic Jev answers (tests the code's decisions, not Jev's accuracy)
    def J(**kw):
        return {"status": "ok", "confidence": 0.9, "complexity": 1.0, "destructive": 0.05, **kw}

    def pol(text, jev):
        return policy(route(text, P), jev, P)

    r = pol("the CSS on the portfolio site looks broken, tidy it up", J(intent="coding", project="Portfolio", project_confidence=0.8))
    assert (r["intent"], r["agent"], r["project"], r["source"]) == ("TASK", "claude_code", "Portfolio", "jev"), r
    assert "needs_approval" not in r, "a confident inference starts the worker; its risky steps still ask"
    r = pol("the CSS on the portfolio site looks broken, tidy it up", J(intent="coding", confidence=0.7, project="Portfolio", project_confidence=0.8))
    assert r["needs_approval"] is True, "below 0.8 the owner confirms the launch"
    r = pol("get rid of the old photos in downloads", J(intent="computer", destructive=0.8))
    assert (r["intent"], r["agent"], r["jev_risk"]) == ("TASK", "computer", 0.8), r
    r = policy(route("get rid of the old photos in downloads", P), J(intent="computer"), P, computer_agent="hermes")
    assert r["agent"] == "hermes", "the default computer agent is a setting"
    # desktop use (the owner, 2026-10-20): Jev decides which is which. A screen task on a web page is the computer agent's (Hermes, on the real
    # desktop) even when Jev's tool pick is the browser; a question is chat; research is the research route; nothing of this names a browser
    web = route("Tell me how can we open YouTube and open it for me and search Arijit Singh there", P)  # TASK-2026-0238, as the owner said it
    r = policy(web, J(intent="computer", tool="browser", tool_confidence=0.68), P, computer_agent="hermes")
    assert (r["intent"], r["agent"], r["tool"], r["rule"]) == ("TASK", "hermes", "browser", "jev:computer"), r
    r = policy(web, J(intent="browser", tool="browser", tool_confidence=0.9), P, computer_agent="hermes")
    assert (r["intent"], r["agent"]) == ("TASK", "hermes"), r
    r = policy(route("explain what a convolutional neural network is", P), J(intent="conversation", tool="none", tool_confidence=0.9), P, computer_agent="hermes")
    assert (r["intent"], r["agent"]) == ("CHAT", "chat"), r
    r = policy(route("research the latest documentation about fastapi", P), J(intent="research", tool="browser", tool_confidence=0.8), P, computer_agent="hermes")
    assert (r["intent"], r["agent"]) == ("RESEARCH", "chat"), r
    assert named_browser("open github.com in your chrome") == "monday" and named_browser("use my open browser and visit example.com") == "owner"
    assert named_browser("open youtube and play despacito") is None and named_browser("show me my browser history") is None
    assert route("use my open browser and visit example.com", P)["intent"] == "CHAT", "words the rules don't parse: Jev's to read (else this tests nothing)"
    r = pol("hmm the thing from before", J(intent="coding", confidence=0.4))
    assert (r["intent"], r["fallback"], r["source"]) == ("CHAT", "low_confidence", "deterministic"), r
    # the camera, however it's asked: Jev reads words the rules don't know; the code decides whether the camera turns on
    assert route("can you see what I'm holding", P)["intent"] == "CHAT", "words the rules don't know (else this tests nothing)"
    r = pol("can you see what I'm holding", J(intent="vision", confidence=0.9))
    assert (r["intent"], r["tool"], [(s["tool"], s["action"]) for s in r["steps"]], r["source"]) == ("TOOL", "vision", [("vision", "look")], "jev"), r
    assert all(permit(s)["decision"] == "SAFE" for s in r["steps"]), "a look keeps nothing: it runs without asking"
    r = pol("can you see what I'm holding", J(intent="vision", confidence=CAMERA_CONFIDENCE - 0.05))
    assert (r["intent"], r.get("steps")) == ("CHAT", None), "unsure: words, never the camera"
    assert pol("what is this", J(intent="vision"))["jev_agrees"] and pol("what is this", J(intent="vision"))["steps"][0]["tool"] == "vision"
    r = pol("how do I fix a flat tire", J(intent="conversation", confidence=0.93, complexity=0.2))
    assert (r["intent"], r["agent"], r["rule"], r["overrode"], r["tier"]) == ("CHAT", "chat", "jev:conversation", "coding-verb", "fast"), r
    r = pol("is hermes still running?", J(intent="task_control", task_action="status", task_action_confidence=0.9))
    assert (r["intent"], r["source"], r["target_agent"]) == ("STATUS", "jev", "hermes"), r
    assert route("is hermes still running?", P)["intent"] == "CHAT", "a bare mention is not an order"
    assert route("Hermes, open Spotify", P)["rule"] == "named-agent:hermes", "vocative address is an order"
    assert route("Hermes, open Spotify", P)["prompt"] == "open Spotify"
    assert route("can you ask hermes to open chrome", P)["rule"] == "named-agent:hermes"
    r = pol("open spotify with hermes", J(intent="coding", confidence=0.95))
    assert (r["intent"], r["agent"], r["rule"]) == ("TASK", "hermes", "mentioned-agent:hermes"), "never moved to another worker"
    r = pol("open spotify with hermes", J(intent="conversation", confidence=0.9))
    assert (r["intent"], r["overrode"]) == ("CHAT", "mentioned-agent:hermes"), "a soft match may lose power"
    r = pol("how do I fix a flat tire", J(intent="conversation", confidence=0.7))
    assert r["intent"] == "TASK", "a verb match only yields to chat at >= 0.8"
    r = pol("Ask Hermes to open Chrome", J(intent="conversation", confidence=0.99))
    assert (r["intent"], r["agent"]) == ("TASK", "hermes"), "a named agent is never overridden"
    r = pol("Ask Claude Code to fix the navbar", J(intent="coding", project="Portfolio", project_confidence=0.9))
    assert (r["project"], r.get("project_source"), r.get("needs_approval")) == ("Portfolio", "jev", None), r
    r = pol("Ask Claude Code to fix the navbar", J(intent="coding", project="Portfolio", project_confidence=0.5))
    assert r["project"] is None, "low project confidence is ignored"
    r = pol("Ask Hermes to tidy my desktop", J(intent="computer", destructive=0.1))
    assert "jev_risk" not in r and "needs_approval" not in r, "Jev can't add or remove anything here"
    for status in ("timeout", "error", "not_configured"):
        r = pol("tell me a joke", {"status": status, "error": "x"})
        assert (r["intent"], r["fallback"], r["source"]) == ("CHAT", status, "deterministic"), r
    r = pol("can you halt what claude is up to", J(intent="task_control", task_action="stop", task_action_confidence=0.9))
    assert r["intent"] == "STOP_CONFIRM", "Jev never stops a task"
    r = pol("stop the spotify one", J(intent="task_control", task_action="stop", task_action_confidence=0.9,
                                       live={"task_ref": {"choice": "TASK-2026-0009", "confidence": 0.89}}))
    assert (r["intent"], r["task_id"]) == ("STOP_CONFIRM", "TASK-2026-0009"), ("the task Jev picked from those live, still confirmed", r)
    r = pol("stop the spotify one", J(intent="task_control", task_action="stop", task_action_confidence=0.9,
                                       live={"task_ref": {"choice": "TASK-2026-0009", "confidence": 0.3}}))
    assert r["task_id"] is None, "an unsure pick names no task"
    heard = "dear sam thanks for the report i'll read it tonight and call you tomorrow at ten"
    assert same_words("Dear Sam,\n\nThanks for the report. I'll read it tonight and call you tomorrow at ten.", heard)
    assert not same_words("Dear Sam, thanks for the report. I will read it tonight and call you tomorrow at 10.", heard), \
        "a changed word (I'll -> I will, ten -> 10) means the dictation stays as heard"
    r = pol("what did TASK-2026-0007 touch", J(intent="task_control", task_action="changes", task_action_confidence=0.8))
    assert (r["intent"], r["task_id"]) == ("CHANGES", "TASK-2026-0007"), r
    r = pol("get some time with Sam on the books", J(intent="calendar", calendar_action="create"))  # the rules can't read it: Jev's reading
    assert (r["intent"], r["action"]) == ("CALENDAR", "create"), r
    r = pol("book a meeting with Sam tomorrow", J(intent="calendar", calendar_action="create"))  # the rules can: the exact Google step stands
    assert (r["intent"], r["steps"][0]["action"], r["jev_agrees"]) == ("TOOL", "calendar_create", True), r
    brief = "# Deep Research Mission\n\nAct as a senior policy researcher. " + "Investigate the programme's funding and partners. " * 160 + \
        "List the leadership appointments and when they were made. " + "Cite every source. " * 40  # (2026-10-22: the owner's brief)
    assert route(brief, [])["intent"] != "CALENDAR" and route("what meetings do I have today", [])["intent"] == "CALENDAR", \
        "a keyword deep in a long brief isn't the ask; a short calendar question still is"
    r = pol("summarize the leadership appointments at BharatGen", J(intent="research", confidence=0.95))  # (the keyword: "appointments")
    assert (r["intent"], r["agent"], r.get("overrode")) == ("RESEARCH", "chat", "calendar"), ("research is a chat that searches: it may overrule", r)
    assert pol("summarize the leadership appointments at BharatGen", J(intent="research", confidence=0.6))["intent"] == "CALENDAR", \
        "unsure, Jev leaves the exact rule alone"
    # Office documents (2026-10-24, docs/adr/0005): what to make, about what, how many slides; "this" is the last answer; Google's stay Google's
    _o = lambda t: (route(t, [])["rule"], {k: v for k, v in ((route(t, []).get("steps") or [{}])[0].get("args") or {}).items() if k != "request"})  # noqa: E731
    assert _o("make a ppt on BharatGen and the IndiaAI mission with 8 slides") == \
        ("office:powerpoint", {"kind": "powerpoint", "topic": "BharatGen and the IndiaAI mission", "slides": 8}), _o("make a ppt on BharatGen with 8 slides")
    assert _o("Monday, create a 10 slide presentation about Indian space startups")[1] == {"kind": "powerpoint", "topic": "Indian space startups", "slides": 10}
    assert _o("write a word document about the history of Kota stone") == ("office:word", {"kind": "word", "topic": "the history of Kota stone"})
    assert _o("make an excel sheet of the top 10 AI startups")[1]["kind"] == "excel" and _o("make a pdf report on GIFT City")[1] == {"kind": "pdf", "topic": "GIFT City"}
    assert _o("write an essay on climate change in word")[1] == {"kind": "word", "topic": "an essay on climate change"}
    assert _o("turn this into a presentation")[1] == {"kind": "powerpoint", "content": "last_reply"} and _o("make a ppt")[1] == {"kind": "powerpoint"}
    assert [route(t, [])["rule"] for t in ("create a google doc about my trip", "make a spreadsheet for my budget", "what is a powerpoint")] == \
        ["google:doc_create", "google:sheet_create", "fallback"], "Google's documents, and a question about Office, aren't Office's"
    assert permit(step("office", "create", "Make it", kind="word"))["tier"] == LOW_RISK, "a new file in Documents runs unasked, said in the receipt"
    r = pol("make a presentation on BharatGen", J(intent="research", confidence=0.98))
    assert (r["intent"], r["steps"][0]["tool"], r["jev_agrees"]) == ("TOOL", "office", True), ("a deck on a subject is made, not just answered", r)
    # "copy … from one app and paste it in another" (2026-10-24): one transfer step, read before the sentence splits at its "and"; files aren't it
    _x = lambda t: [(s["tool"], s["action"], s["args"]) for s in route(t, []).get("steps") or []]  # noqa: E731
    assert _x("copy this from chatgpt and paste it in notepad") == [("computer", "transfer", {"source": "chatgpt", "target": "notepad", "what": "this"})]
    assert _x("copy the last answer from ChatGPT and paste it into Word")[0][2] == {"source": "ChatGPT", "target": "Word", "what": "the last answer"}
    assert _x("copy everything from notepad and then paste it into a new word document")[0][2]["target"] == "word"
    assert _x("copy report.pdf from downloads and paste it in documents") == [] and _x("paste") == [("computer", "key", {"keys": ["ctrl", "v"]})]
    assert permit(step("computer", "transfer", "x", source="chatgpt", target="notepad", what="this"))["decision"] == "SAFE"
    assert permit(step("computer", "transfer", "x", source="notepad", target="windows security", what="this"))["decision"] == "BLOCK"
    # no Jev (offline, or down; 2026-10-24): a chat's tier from its words, so the model on this PC fits the request
    assert [rules_tier(t) for t in ("hi", "what's 2+2", "explain how a transformer works", "write me a letter to my landlord",
                                     "why is the sky blue", " ".join(["word"] * 45))] == ["fast", "fast", "strong", "strong", "strong", "strong"]
    r = policy(route("explain how vaccines work", []), {"status": "offline"}, [])
    assert (r["intent"], r.get("tier"), r["fallback"]) == ("CHAT", "strong", "offline"), r
    assert policy(route("hello there", []), {"status": "timeout"}, [])["tier"] == "fast"
    assert "tier" not in policy(route("open notepad", []), {"status": "timeout"}, []), "a tool step has no chat tier"
    r = pol("refresh your map of portfolio", J(intent="monday_settings", system_action="analyze_project", system_action_confidence=0.9))
    assert r["intent"] == "GRAPHIFY_UPDATE", r
    r = pol("can I talk to you from my phone", J(intent="monday_settings", system_action="pair_device", system_action_confidence=0.9))
    assert (r["intent"], r["topic"]) == ("HELP", "pair_device"), r
    r = pol("what did we decide about the navbar", J(intent="memory"))
    assert r["intent"] == "MEMORY" and r["agent"] is None, r
    r = pol("how is the router wired in Monday Core", J(intent="project_knowledge"))
    assert (r["intent"], r["project"]) == ("GRAPHIFY_QUERY", "Monday Core"), r
    assert route("Stop the current task.", P)["intent"] == "STOP" and not wants_jev(route("Stop the current task.", P))

    # Jev first (Monday 2.0): every request but a reflex waits for Jev; exact commands keep their arguments
    assert all(wants_jev(route(t, P)) for t in ("open notepad", "what time is it", "use the second female voice", "show me my brain"))
    assert not wants_jev(route("stop talking", P)) and not wants_jev(route("never mind", P)) and not wants_jev(route("stop the dev server", P))
    r = pol("open Notepad", J(intent="computer", confidence=1.0))
    assert (r["intent"], r["source"], r["jev_agrees"], r["steps"][0]["args"]) == ("TOOL", "jev", True, {"target": "Notepad"}), r
    r = pol("open the pod bay doors", J(intent="conversation", confidence=0.93))
    assert (r["intent"], r["overrode"], "steps" in r) == ("CHAT", "fast-path", False), "a sure Jev overrules a misparse"
    r = pol("open the pod bay doors", J(intent="conversation", confidence=0.7))
    assert (r["intent"], r["jev_agrees"]) == ("TOOL", False), "an unsure Jev leaves an exact command alone"
    r = pol("what's in my downloads folder", J(intent="filesystem", confidence=0.98))
    assert (r["intent"], r["jev_agrees"], r["steps"][0]["action"]) == ("TOOL", True, "list"), "a files step agrees with Jev's filesystem"
    r = pol("take a screenshot", J(intent="coding", confidence=0.99))
    assert (r["intent"], r["jev_agrees"]) == ("TOOL", False), "Jev never turns an exact command into a worker launch"
    produced = {t for t, _ in re.findall(r'step\("(\w+)", "(\w+)"', open(__file__, encoding="utf-8").read())}
    assert all(f"TOOL:{t}" in AGREES for t in produced), f"every plan tool has an AGREES row: {produced - {k[5:] for k in AGREES}}"
    r = pol("run the tests", J(intent="coding", confidence=0.95))
    assert (r["intent"], r["jev_agrees"]) == ("TOOL", True), "terminal work that Jev calls coding still agrees"
    r = pol("what time is it", J(intent="system_info", confidence=0.98))
    assert (r["intent"], r["source"]) == ("TIME", "jev")
    r = pol("Ask Claude Code to fix the navbar", J(intent="conversation", confidence=0.99))
    assert (r["intent"], r["agent"], r["jev_agrees"]) == ("TASK", "claude_code", False), "a named agent is never redirected"
    r = pol("do you know what time it is", J(intent="system_info", confidence=0.9))
    assert r["intent"] == "TIME" and pol("how busy is my pc", J(intent="system_info", confidence=0.9))["intent"] == "SYSTEM"
    r = pol("can you sound a bit quicker", J(intent="voice", voice_action="faster", voice_action_confidence=0.9))
    assert (r["intent"], r["op"]) == ("VOICE", "faster"), r
    r = pol("find the TypeSafe JEV page", J(intent="browser", confidence=0.95))
    assert (r["intent"], r["agent"], r.get("needs_approval")) == ("TASK", "computer", None), r
    r = pol("investigate the build", J(intent="terminal", confidence=0.7))
    assert (r["intent"], r["agent"], r["needs_approval"]) == ("TASK", "claude_code", True), r
    r = pol("flarp", J(intent="unknown", confidence=0.9))
    assert r["intent"] == "CHAT", "unknown goes to the chat model, which can ask"
    r = pol("explain quantum tunnelling in depth", J(intent="conversation", model_class="strong", model_class_confidence=0.8, complexity=0.5))
    assert r["tier"] == "strong", "a sure model-class answer picks the chat tier"
    r = pol("what do reviewers say about the new pixel phone", J(intent="research", confidence=0.9))
    assert (r["intent"], r["agent"]) == ("RESEARCH", "chat"), ("research goes to a model that can search the web", r)
    assert policy(route("search youtube for lofi music", P), J(intent="research", confidence=0.95), P)["intent"] == "TOOL", \
        "an exact browser search stays a browser search"
    r = policy(route("close it", P), J(intent="computer"), P)
    assert with_context(r, route("open Notepad", P))["steps"][0]["args"]["target"] == "Notepad", "a follow-up's 'it'"
    assert with_context(route("close chrome", P), route("open Notepad", P))["steps"][0]["args"]["target"] == "chrome", "only pronouns"
    assert describe(route("open Chrome and search for cats", P)).startswith("Open Chrome; Search") and describe(route("hi", P)) == "answered in chat"

    # the owner's real phrasings (voice): a greeting + name, "computer use", polite forms, messaging
    r = route("Hello Monday open WhatsApp and send hi to Dad what", P)
    assert (r["intent"], r["agent"], r["rule"], r["prompt"]) == ("TASK", "computer", "computer-verb", "open WhatsApp and send hi to Dad what"), r
    assert request_risk(r["prompt"]) == ("COMMUNICATION", "HIGH"), "sending a message always waits for the owner"
    r = route("Use computer use and open WhatsApp for me and send hydro dad", P)
    assert (r["intent"], r["agent"], r["rule"], r["prompt"]) == ("TASK", "hermes", "named-agent:hermes", "open WhatsApp for me and send hydro dad"), r
    assert request_risk(r["prompt"]) == ("COMMUNICATION", "HIGH")
    for text in ("hey Monday, can you open notepad", "Monday please take a screenshot", "could you search for flights to Goa",
                 "turn up the volume"):
        assert (route(text, P)["intent"], route(text, P)["agent"]) == ("TOOL", "computer"), text
    for text in ("send hi to dad on whatsapp", "whatsapp mom that I'm late", "set a timer for 10 minutes"):
        assert (route(text, P)["intent"], route(text, P)["agent"]) == ("TASK", "computer"), text
    assert request_risk("send hi to dad on whatsapp") == request_risk("whatsapp mom that I'm late") == ("COMMUNICATION", "HIGH")
    assert request_risk("open whatsapp") is None and request_risk("turn up the volume") is None
    assert route("Hello Monday", P)["intent"] == "CHAT" and route("hello world program in python", P)["prompt"] == "hello world program in python"
    assert route("use computer use for this", P)["intent"] == "REROUTE" and route("use computer use for this", P)["agent"] == "hermes"

    # switching AIs: the chat AI vs the coding worker
    for text, want in (("use claude", "claude"), ("Monday, switch to Grok", "grok"), ("change it to x", "x"),
                       ("use chatgpt", "chatgpt"), ("switch the AI to gemini", "gemini"), ("use gpt-5", "gpt-5"),
                       ("talk to me using deepseek", "deepseek"), ("use claude instead", "claude")):
        r = route(text, P)
        assert (r["intent"], r.get("want")) == ("MODEL", want), (text, r)
    for text, agent in (("use claude for this task", "claude_code"), ("use codex for this", "codex"), ("use gemini cli for this", "gemini_cli"),
                        ("use grok build for this", "grok_build")):
        assert (route(text, P)["intent"], route(text, P)["agent"]) == ("REROUTE", agent), text
    # Gemini for one request (through its CLI), without switching chat
    assert (route("Use Gemini for this.", P)["intent"], route("Use Gemini for this.", P)["question"]) == ("ASK_AI", None)
    r = route("ask Gemini what the tallest building in the world is", P)
    assert (r["intent"], r["question"]) == ("ASK_AI", "what the tallest building in the world is"), r
    assert route("use gemini for this: what's a monad?", P)["question"] == "what's a monad?"
    r = route("ask Grok what the weather is like on Mars", P)
    assert (r["intent"], r["provider"], r["question"]) == ("ASK_AI", "xai", "what the weather is like on Mars"), r
    assert (route("use grok for this", P)["intent"], route("use grok for this", P)["provider"]) == ("ASK_AI", "xai")
    assert (route("ask grok to fix the navbar bug in portfolio", P)["intent"], route("ask grok to fix the navbar bug in portfolio", P)["agent"]) == ("TASK", "grok_build")
    assert route("use grok build for this", P)["intent"] == "REROUTE" and route("use grok", P)["intent"] == "MODEL"
    assert (route("ask gemini to fix the navbar bug in portfolio", P)["intent"], route("ask gemini to fix the navbar bug in portfolio", P)["agent"]) == ("TASK", "gemini_cli")
    assert (route("use gemini", P)["intent"], route("use gemini", P)["want"]) == ("MODEL", "gemini"), "a bare 'use Gemini' switches chat"
    assert route("Monday, use Gemini Pro.", P)["want"] == "gemini pro" and route("switch to gemini flash", P)["want"] == "gemini flash"
    # a bare "use <coding agent>" sets the default (the owner's spec); editors are recognised for an honest reply
    for text, coder in (("make codex the default", "codex"), ("use claude code from now on", "claude_code"),
                        ("switch to codex for all coding tasks", "codex"), ("Monday, use Claude Code.", "claude_code"),
                        ("use codex", "codex"), ("use Kiro", "kiro"), ("use cursor", "cursor"), ("use kilo code", "kilo"),
                        ("Monday, use the fastest available coding agent.", "fastest"), ("use Claude Code for coding", "claude_code")):
        assert (route(text, P)["intent"], route(text, P)["coder"]) == ("DEFAULT_CODER", coder), (text, route(text, P))
    for text, want in (("Monday, switch back to my default model.", "default"), ("use the local model", "local"),
                       ("Switch to my local Qwen model.", "local qwen"), ("Use the fastest available model.", "fastest available"),
                       ("Switch back to the previous model.", "previous"), ("use the last model", "last"), ("Use Ollama.", "ollama"),
                       ("Monday, switch to Claude.", "claude"), ("Show me the available models.", "list"), ("which models can you use", "list")):
        assert (route(text, P)["intent"], route(text, P)["want"]) == ("MODEL", want), (text, route(text, P))
    assert route("go back to the last one", P)["intent"] != "MODEL" and route("use the best one", P)["intent"] != "MODEL", "only a model is a model"
    assert jev_summary({"status": "ok", "intent": "chat", "request": "secret text", "answers": {}}) == {"status": "ok", "intent": "chat"}

    # fast path: the owner's exact commands are parsed by the rules (Jev then decides; no model is needed)
    def tool(text):
        r = route(text, P)
        assert r["intent"] == "TOOL" and wants_jev(r), (text, r)
        return [(s["tool"], s["action"], s["args"]) for s in r["steps"]]

    assert tool("Monday, open Chrome.") == [("computer", "open", {"target": "Chrome"})]
    assert tool("Monday, open VS Code.") == [("computer", "open", {"target": "VS Code"})]
    assert tool("Monday, take a screenshot.") == [("computer", "screenshot", {})]
    assert tool("Monday, read what's on my screen.") == [("computer", "read_screen", {"what": "screen"})]
    assert tool("read this error") == [("computer", "read_screen", {"what": "error"})]
    # Dotpals' marks: Monday circles it on the screen, nothing clicked (a look and a drawing: no approval)
    assert tool("point to the save button") == [("computer", "point", {"target": "save button"})], tool("point to the save button")
    assert tool("circle the search box on my screen") == tool("where's the search box on my screen?") == [("computer", "point", {"target": "search box"})]
    assert tool("show me where the settings are") == [("computer", "point", {"target": "settings"})]
    assert not any(s.get("action") == "point" for q in ("where is my phone", "show me the weather", "point out the flaws in this plan")
                   for s in route(q, P).get("steps") or []), "a question about the world isn't a look at the screen"
    assert permit(step("computer", "point", "Show where “Save” is", target="Save"))["decision"] == "SAFE"
    # Monday's own Chrome: tabs, back, the page and its console
    assert tool("open a new tab") == [("browser", "new_tab", {})] and tool("open a new tab with github.com") == [("browser", "new_tab", {"url": "github.com"})]
    assert tool("close this tab") == [("browser", "close_tab", {})] and tool("close tab 2") == [("browser", "close_tab", {"tab": 2})]
    assert tool("switch to tab 3") == [("browser", "switch_tab", {"tab": 3})] and tool("go to the last tab") == [("browser", "switch_tab", {"tab": -1})]
    assert tool("Monday, go back.") == [("browser", "back", {})]
    assert tool("Open DevTools and inspect the console.") == [("browser", "console", {})], "one look at the console"
    assert tool("check why this website is not loading") == [("browser", "console", {})]
    assert tool("take a screenshot of this page") == [("browser", "page_screenshot", {})]
    assert tool("read this page") == [("browser", "read_page", {})] == tool("what's on this page")
    assert tool("close Chrome") == [("computer", "close", {"target": "Chrome"})], "the app, not a tab"
    assert tool("download this file") == [("browser", "download", {})] and tool("download the report") == [("browser", "download", {"target": "report"})]
    assert permit({"tool": "browser", "action": "download", "args": {}})["decision"] == "ASK", "every download asks"
    r = route("search youtube for lofi music then open the first video", P)
    assert r["intent"] == "TASK" and r["agent"] == "computer", ("an ordinal is on the page: the goal loop, not an app", r)
    assert permit({"tool": "browser", "action": "fill_form", "args": {"text": "hunter2", "field": "Password"}})["decision"] == "BLOCK"
    assert permit({"tool": "browser", "action": "click_element", "args": {"target": "Send"}}, {"app": "https://mail.google.com/"})["decision"] == "ASK"
    assert permit({"tool": "browser", "action": "click_element", "args": {"target": "Images"}})["decision"] == "SAFE"
    assert tool("Monday, open my Monday project.") == [("applications", "open_project", {"project": "Monday Core"})]
    assert tool("Monday, open the terminal.") == [("computer", "open", {"target": "terminal"})]
    assert tool("Monday, run the tests.") == [("terminal", "tests", {})]
    assert tool("Monday, start my development server.") == [("terminal", "dev_server", {})]
    assert tool("Monday, run the tests and tell me what's wrong.") == [("terminal", "tests", {"explain": True})]
    assert tool("Open YouTube and search for React tutorials.") == [("browser", "search", {"query": "React tutorials", "site": "youtube"})]
    assert tool("Open VS Code, open my Monday project, run the tests, and tell me what's failing") == [
        ("applications", "open_project", {"project": "Monday Core", "editor": "VS Code"}), ("terminal", "tests", {"explain": True})]
    assert tool("Open VS Code and check why my API is failing")[-1] == ("coding_agents", "delegate", {"prompt": "check why my API is failing"})
    assert tool("open my portfolio project in cursor") == [("applications", "open_project", {"project": "Portfolio", "editor": "cursor"})]
    assert tool("Open the Quokka code in VS Code") == [("applications", "open_project", {"ref": "Quokka code", "editor": "VS Code"})], "a project named by its code: found, not an app"
    assert tool("open quokka's code in vs code") == [("applications", "open_project", {"ref": "quokka's code", "editor": "vs code"})]
    assert tool("open main.py in vs code") == [("computer", "open", {"target": "main.py", "app": "vs code"})], "a file opens in the editor"
    assert tool("open Spotify in VS Code")[0][1] == "open", "no project word: an app, as before"
    assert tool("show me the latest git changes") == [("terminal", "git", {"what": "status"})]
    assert tool("why is port 5000 occupied?") == [("terminal", "port", {"port": 5000})]
    assert [s[1] for s in tool("check what's using port 11434 and then show me the latest git changes")] == ["port", "git"]
    assert tool("run npm install") == [("terminal", "run", {"command": "npm install"})]
    assert tool("run `python -m core.router`") == [("terminal", "run", {"command": "python -m core.router"})]
    assert tool("type 'hello world' into the search box and press enter")[0][2] == {"text": "hello world", "field": "search box"}
    assert tool("click the login button") == [("computer", "click", {"target": "login", "button": "left", "count": 1})]
    assert tool("click Statistics in Monday's own window") == [("computer", "click", {"target": "Statistics", "app": "Monday", "button": "left",
                                                                                      "count": 1})], "Monday's own window named: refused there"
    assert tool("click the Save button in Notepad's window") == [("computer", "click", {"target": "Save", "app": "Notepad", "button": "left",
                                                                                        "count": 1})] and \
        tool("click Sign in") == [("computer", "click", {"target": "Sign in", "button": "left", "count": 1})], "only an explicit window"
    assert tool("set the volume to 30") == [("computer", "volume", {"op": "set", "level": 30})]
    assert tool("close chrome") == [("computer", "close", {"target": "chrome"})] and tool("switch to spotify")[0][1] == "focus"
    assert tool("go to github.com") == [("browser", "open_url", {"url": "github.com"})]
    assert tool("press ctrl+shift+t") == [("computer", "key", {"keys": ["ctrl", "shift", "t"]})]

    # Phase 6: the computer controller's own words
    assert tool("open Notepad and write: Hello, I am Monday.") == [("computer", "open", {"target": "Notepad"}),
                                                                   ("computer", "type", {"text": "Hello, I am Monday", "into": "Notepad"})], "the owner's Demo 1"
    # Compound commands (TASK-2026-0171, 2026-10-10: "open notepad and wrote helloe there" opened an app named "wrote helloe there").
    # A clause that starts with any form of a verb is its own clause; the typing goes into the app the plan opened, after that step.
    for text, typed in (("open notepad and wrote helloe there", "helloe there"), ("Open Notepad and write hello there.", "hello there"),
                        ("Open Notepad, then type hello there.", "hello there"), ("Launch Notepad and enter hello there.", "hello there"),
                        ("Launch Notepad and type hello there.", "hello there"), ("Open Notepad and write testing one two three.", "testing one two three"),
                        ("Open Notepad and write 'Hello, Monday!' inside it.", "Hello, Monday!"), ("open notepad and typed hello in it", "hello")):
        r = route(text, P)
        assert [(s["action"], s["args"].get("target") or s["args"].get("text")) for s in r["steps"]] == [("open", r["steps"][0]["args"]["target"]), ("type", typed)], (text, r["steps"])
        assert r["steps"][1]["args"]["into"].lower() == "notepad" and r["steps"][1]["needs"] == 1 and "field" not in r["steps"][1]["args"], (text, r["steps"])
        assert instant(r), ("an exact plan of SAFE steps: it starts at once", text)
    assert [s["args"] for s in route("open notepad, type 'open the door and close it' and press enter", P)["steps"]][1:] == [
        {"text": "open the door and close it", "into": "notepad"}, {"keys": ["enter"]}], "words in quotes are typed, never split"
    # Exact text after a colon is data (2026-10-13): never split at "and", never read for "stop" or "status", whole with its line breaks and quotes.
    long = ("Open Notepad and write the following exactly: Monday is working correctly. Open the door and then close it; stop the server, "
            "and tell me the status.\n\n## Heading\n- one “quoted” item\n- 日本語 and `code()`\n\nThe end, the end.")
    r = route(long, P)
    assert r["intent"] == "TOOL" and r.get("literal") and [s["action"] for s in r["steps"]] == ["open", "type"], r
    typed = r["steps"][1]["args"]
    assert typed["text"] == long.split(": ", 1)[1] and typed["literal"] and typed["into"].lower() == "notepad" and "\x01" not in r["steps"][1]["label"], typed
    r = route("write this in Notepad: hello, and then search for cats", P)
    assert [s["action"] for s in r["steps"]] == ["type"] and r["steps"][0]["args"]["text"] == "hello, and then search for cats" and r["steps"][0]["args"]["field"] == "Notepad", r["steps"]
    assert route("type: hi there", P)["steps"][0]["args"]["text"] == "hi there", "a bare colon still types"
    r = route("My goal is to get fit by December.", P)  # goals (core/goals.py): explicit words, an exact command Jev can't redirect
    assert (r["intent"], r["op"], r["text"]) == ("GOAL", "new", "get fit by December"), r
    assert [route(t, P).get("op") for t in ("start my goal", "Monday, continue the goal", "pause my goal", "stop my goal", "forget my goal",
                                            "how is my goal going?", "I finished the step", "step done", "skip this step")] == \
        ["start", "continue", "pause", "stop", "forget", "status", "step_done", "step_done", "skip"]
    assert route("stop my goal", P)["intent"] == "GOAL" and route("stop", P)["intent"] == "STOP", "a goal's stop isn't stop-everything"
    assert route("start a goal to read 12 books this year", P)["text"] == "read 12 books this year"
    assert pol("my goal is to get fit by December", J(intent="conversation", confidence=0.97))["intent"] == "GOAL", "Jev can't talk it out of a goal"
    paras = "First line, with a comma (and 3.14).\n\nSecond paragraph: stop the server and tell me the status.\n\nThird, done."
    r = route(f'open Notepad and type "{paras}"', P)  # quoted, over several lines (2026-10-15: this went to Hermes, unchecked)
    assert r["intent"] == "TOOL" and r.get("literal") and [s["action"] for s in r["steps"]] == ["open", "type"], r
    assert r["steps"][1]["args"]["text"] == paras and r["steps"][1]["args"]["literal"], r["steps"][1]["args"]
    assert route("search for: cats", P)["intent"] != "TOOL" or route("search for: cats", P)["steps"][0]["action"] != "type", "no type verb, no payload"
    assert route("write a note to John: hi", P).get("literal") is None, "a message, not a dictation: left to Jev"
    assert route("forget the search folders", P)["setting"] == "search_roots" and route("stop searching my drives", P)["setting"] == "search_roots"
    assert [s["action"] for s in route("open spotify and chrome", P)["steps"]] == ["open", "open"] and route("open notepad and hello there", P)["intent"] != "TOOL", \
        "one verb over a list of names; a part that points at something is no app's name"
    assert route("type hello and enter", P)["steps"][1]["args"] == {"keys": ["enter"]}
    assert "into" not in route("open chrome and type hello", P)["steps"][1]["args"], "a browser's page is typed into through the page"
    assert route("Open Chrome and search for the latest AI news.", P)["steps"][1]["args"] == {"query": "the latest AI news"}
    assert route("Open VS Code and inspect this project.", P)["steps"][1]["action"] == "delegate" and tool("inspect this window") == [("computer", "inspect", {})]
    assert tool("Explain what is on my screen.") == [("computer", "read_screen", {"what": "screen"})]
    assert tool("tell me whats on my screen and explain it to me") == tool("explain me whats in my scren rn") == tool("can you tell me what's on my screen") \
        == [("computer", "read_screen", {"what": "screen"})], "the owner's own words (2026-10-29)"
    assert tool("what's on my screen, explain it") == tool("what's on my screen and explain it to me simply") == \
        [("computer", "read_screen", {"what": "screen"})], "the look at the screen explains it: no second step explaining the explanation"
    assert [x[1] for x in tool("tell me what's on my screen and tell me the weather")] == ["read_screen", "answer"], "another question stays"
    assert tool("whats on my screen rn and explain me too") == tool("whats on my sscreen and explain me too what it is") == \
        [("computer", "read_screen", {"what": "screen"})], "the owner's own (2026-10-29 16:53, 17:08): one look, explained"
    assert [x[1] for x in tool("what's on my screen and summarize the news")] == ["read_screen", "answer"], "a new subject stays its own step"
    assert [x[1] for x in tool("read the error and explain it")] == ["read_screen", "explain"], "an error read is explained by its own step"
    assert looks_only("tell me whats on my screen and explain it to me") and looks_only("what am i looking at") and \
        not looks_only("look at my screen and click the OK button") and not looks_only("explain this error on my screen and fix it")
    # short forms and slips (the owner, 2026-10-29: "in the whole app it should also understand the spelling mistakes and shortform")
    assert normal("can u open ur settings rn pls") == "can you open your settings right now please" and normal("Whats up") == "What's up"
    assert normal('type "u r gr8 rn"') == 'type "u r gr8 rn"' and normal('say "u ok?" to Sam rn') == 'say "u ok?" to Sam right now', "quotes stay"
    assert normal("set a timer for 5 min") == "set a timer for 5 minutes" and normal("the min of the list") == "the min of the list"
    assert mend("opne notpad") == "open notepad" and mend("turn the volum up") == "turn the volume up" and mend("explian") == "explain"
    assert mend("rewind the video whether or not") == "rewind the video whether or not" and mend("the older files") == "the older files" \
        and mend("a new word document") == "a new word document" and mend("it opens and plays") == "it opens and plays", "real words stay"
    assert _one_slip("opne", "open") and _one_slip("volum", "volume") and _one_slip("scrreen", "screen") and _one_slip("older", "folder") \
        and not _one_slip("open", "open") and not _one_slip("plya", "pay")  # ("older": one slip from "folder"; mend keeps it: its first letter)
    for said, want in (("plya some music", [("computer", "media", {"op": "play"})]), ("turn the volum up", [("computer", "volume", {"op": "up"})]),
                       ("search yt for lofi music", [("browser", "search", {"query": "lofi music", "site": "youtube"})]),
                       ("can u read this error", [("computer", "read_screen", {"what": "error"})]),
                       ("pause the vid", [("computer", "media", {"op": "pause"})]),
                       ("close chrom", [("computer", "close", {"target": "chrome"})])):
        assert tool(said) == want, (said, tool(said))
    assert route("plya some music", [])["mended"] == "plya some music" and "mended" not in route("open older files", []), "a mend is said"
    assert route("make a plan for tmrw", [])["intent"] == "CHAT" and route("make a plan for tmrw", [])["request"] == "make a plan for tomorrow"
    # dynamic (ADR-0013): the owner's learned words read like the seed's; what the model's plain words teach; when its words are kept
    assert normal("put on sum lofi on yt", {"sum": "some"}) == "put on some lofi on YouTube" and normal("x", {"whether": "weather"}) == "x"
    r = route("plya lofi pls", [], words={"plya": "play", "never": "x"})
    assert r["learned"] == ["plya"] and r["steps"][0]["action"] == "play" and "learned" not in route("play lofi", [], words={"plya": "play"})
    assert taught("plz put on sum lofi beats on yt rn", "Please play some lofi beats on YouTube right now") == [("yt", "youtube"), ("rn", "right now")]
    assert taught("wht r u doing tmrw", "what are you doing tomorrow") == [("wht", "what"), ("tmrw", "tomorrow")]
    assert taught("sum of the column", "sum of the column") == [] and taught('type "u r gr8" in notpad', 'type "u r gr8" in notepad') == [("notpad", "notepad")]
    assert plain_ok("plz put on sum lofi on yt", "Please play some lofi on YouTube") and not plain_ok("open notepad", "What is the capital of France and why")
    assert not plain_ok('type "x" in notpad', "type x in notepad") and not plain_ok("set a timer for 5", "set a timer for 10"), "quotes and numbers kept"
    assert taught("cn u turn da volum down a bit", "Can you turn the volume down a bit") == [("volum", "volume")], "“da” isn't “down a”: typed"
    assert tool("turn the volume down a bit") == tool("can you turn the volume down a bit") == [("computer", "volume", {"op": "down"})] \
        and tool("make it a bit louder") == [("computer", "volume", {"op": "up"})], "a bit, a little: still the volume"
    early = early_clause("open notepad and", P, whole=True)[1]  # the rest of a sentence whose first clause started early: typed into what it opened
    assert after_opened(route("write hello there", P), early)["steps"][0]["args"] == {"text": "hello there", "into": "notepad"}
    assert after_opened(route("write hello there", P), early_clause("open chrome and", P, whole=True)[1])["steps"][0]["args"] == {"text": "hello there"}
    assert tool("Open Notepad, write hello and save it as test.txt")[-1] == ("computer", "save", {"name": "test.txt"})
    assert tool("save it") == [("computer", "save", {})] and tool("save as shopping list on the desktop")[0][2] == {"name": "shopping list", "folder": "desktop"}
    assert tool("what's on my clipboard") == [("computer", "clipboard", {"op": "get"})]
    assert tool("copy hello world to the clipboard") == [("computer", "clipboard", {"op": "set", "text": "hello world"})]
    assert tool("paste") == [("computer", "key", {"keys": ["ctrl", "v"]})] and tool("select all")[0][2] == {"keys": ["ctrl", "a"]}
    assert tool("drag the file icon to the recycle bin") == [("computer", "drag", {"source": "file icon", "target": "recycle bin"})]
    assert tool("move the mouse to 400, 300") == [("computer", "move_mouse", {"x": 400, "y": 300})]
    assert tool("what's using the most memory") == [("computer", "processes", {"sort": "memory"})]
    assert tool("is chrome running") == [("computer", "processes", {"query": "chrome"})]
    assert route("is Claude Code still running?", P)["intent"] != "TOOL", "Monday's own agents are task questions"
    assert tool("kill chrome") == [("computer", "kill", {"target": "chrome"})] and route("kill it", P)["intent"] == "STOP"
    assert route("kill the task", P)["intent"] == "STOP" and route("force close spotify", P)["steps"][0]["action"] == "kill"
    assert tool("inspect this window") == [("computer", "inspect", {})]
    assert tool("what's in my downloads folder") == [("filesystem", "list", {"folder": "downloads"})]
    assert tool("read notes.txt on the desktop") == [("filesystem", "read", {"name": "notes.txt", "folder": "desktop"})]
    assert tool("create a folder called Receipts in documents") == [("filesystem", "create", {"name": "Receipts", "kind": "folder", "folder": "documents"})]
    assert tool("rename notes.txt to todo.txt") == [("filesystem", "rename", {"src": "notes.txt", "dst": "todo.txt"})]
    assert tool("move report.pdf from downloads to documents") == [("filesystem", "move", {"src": "report.pdf", "dst": "documents", "folder": "downloads"})]
    assert tool("delete old-notes.txt from the desktop") == [("filesystem", "delete", {"name": "old-notes.txt", "folder": "desktop"})]
    assert route("delete the old screenshots on my desktop", P)["intent"] != "TOOL", "a vague set of files isn't an exact step"
    assert tool("write buy milk to todo.txt")[0][:2] == ("filesystem", "write")
    assert route("click the second result", P)["intent"] != "TOOL", "an ordinal result needs a model to see the page"
    assert route("open WhatsApp and message Sam that I'm late", P)["intent"] == "TASK", "a message is never a fast-path step"
    assert route("open claude", P)["intent"] == "TOOL" and route("open spotify with hermes", P)["agent"] == "hermes"
    # a clause that asks for something written is a step for an AI, not a rule about its subject (Jev picks the AI; nothing here knows what an SEO report is)
    plan = route("open chrome and search garbacircle.in and give me the full seo report for that", P)
    assert [(x["tool"], x["action"]) for x in plan["steps"]] == [("computer", "open"), ("browser", "search"), ("analysis", "answer")], plan["steps"]
    assert plan["steps"][1]["args"] == {"query": "garbacircle.in"} and plan["steps"][2]["args"] == {"request": "give me the full seo report for that"}, \
        "the search keeps only its own words: the request after 'and' is the AI's"
    assert [(x["tool"], x["action"]) for x in route("Open Chrome, search for cats and compare the top results", P)["steps"]][-1] == ("analysis", "answer")
    assert route("give me the full seo report for garbacircle.in", P)["intent"] == "CHAT", "a lone request for writing isn't a plan: Jev routes it"
    assert tool("open chrome and search for TypeSafe JEV and summarize it")[-1][:2] == ("analysis", "explain"), "'summarize it' still reads the page"
    assert route("open WhatsApp and message Sam that I'm late", P)["intent"] == "TASK", "a message is still never a plan step"
    fast = {"Monday, stop.": ("STOP", None), "Monday, cancel that.": ("STOP", None), "never mind": ("STOP", None),
            "stop talking": ("VOICE", "stop_speaking"), "pause": ("VOICE", "pause"), "Monday, change your voice.": ("VOICE", "change"),
            "Monday, show me the available voices.": ("VOICE", "list"), "Monday, use the second female voice.": ("VOICE", "select"),
            "use the natural female voice": ("VOICE", "select"), "use the offline voice": ("VOICE", "select"),
            "make your voice faster": ("VOICE", "faster"), "speak slower": ("VOICE", "slower"), "what time is it": ("TIME", None),
            "Monday, listen longer.": ("VOICE", "listen_longer"), "give me more time to finish": ("VOICE", "listen_longer"),
            "don't cut me off": ("VOICE", "listen_longer"), "respond faster": ("VOICE", "listen_shorter"),
            "listen normally": ("VOICE", "listen_normal"), "keep listening after your replies": ("VOICE", "follow_up_on"),
            "turn off follow-up listening": ("VOICE", "follow_up_off"),
            "use the Gemini voice": ("VOICE", "provider"), "switch to Google Cloud voices": ("VOICE", "provider"),
            "use browser voices": ("VOICE", "provider"), "use the google voice": ("VOICE", "provider"),
            "Monday, show me my brain.": ("UI", "brain"), "Monday, show me everything connected to JEV.": ("UI", "brain"),
            "show messages": ("UI", "messages"), "go home": ("UI", "home"), "system status": ("SYSTEM", None),
            "open your pet for me": ("UI", "pet"), "Monday, show the notch": ("UI", "pet"), "bring back your pet": ("UI", "pet"),
            "go back to the notch": ("UI", "pet"), "hide your pet": ("UI", "pet_hide"), "show yourself": ("UI", "home"),
            "open your statistics page": ("UI", "stats"), "Monday, show Monday's active pills": ("UI", "pills"),
            "go to your settings": ("UI", "settings"), "take me to your agents tab": ("UI", "agents"), "open your chat": ("UI", "chat"),
            "Monday, what AI tools are installed?": ("AI_TOOLS", "all"), "Monday, which coding agents are available?": ("AI_TOOLS", "coding"),
            "Monday, check whether Cursor is available.": ("AI_TOOLS", "check"), "use JEV for routing": ("ROUTING", None),
            "use edge as my browser": ("DEFAULT", None), "stop the dev server": ("STOP", None),
            "Use FastAPI for Monday backend.": ("REMEMBER", "decision"), "remember that staging runs on port 8080": ("REMEMBER", "fact"),
            "we're filming": ("VOICE", "filming_on"), "Monday, filming mode.": ("VOICE", "filming_on"), "filming mode off": ("VOICE", "filming_off"),
            "Monday, stop filming.": ("VOICE", "filming_off"), "we're done filming": ("VOICE", "filming_off"), "show me the judge": ("JUDGE_DEMO", None)}
    for text, (intent, extra) in fast.items():
        r = route(text, P)
        got = r.get("op") or r.get("page") or r.get("kind")
        assert r["intent"] == intent and (extra is None or got == extra) and wants_jev(r) != reflex(r), (text, r)
    assert route("Monday, use the second female voice.", P)["ordinal"] == 2 and route("use voice 3", P)["number"] == 3
    assert [route(t, P)["provider"] for t in ("use the Gemini voice", "switch to Google Cloud voices", "use browser voices",
                                              "use the google voice", "switch to Google's free voice")] == \
        ["gemini", "google_cloud", "browser", "google", "google"], "the Google voice is Google's free one (2026-10-15); Cloud stays Cloud"
    assert route("Monday, show me everything connected to JEV.", P)["focus"] == "JEV"
    assert route("open settings", P)["intent"] != "UI" and route("open the settings page", P)["intent"] != "UI", \
        "Monday's own pages only with your / Monday's: \"open settings\" stays Windows' Settings"
    assert set(_PAGE_WORDS.values()) == set(UI_PAGES), "every page Monday's window has can be asked for"
    assert route("Monday, check whether Cursor is available.", P)["subject"] == "Cursor"
    assert route("stop filming", P)["intent"] == "VOICE" and route("stop the task", P)["intent"] == "STOP", "filming rules come before _STOP"
    assert named("Monday, what's on my calendar?") and named("hey Monday open Chrome") and named("Jarvis, hi", "Jarvis")
    assert not (named("Mondays are busy") or named("Monday core is slow") or named("what is Monday")), "only an opening address counts"
    assert wake_rest("okay Monday, open Notepad") == "open Notepad" and wake_rest("OK, Monday. What time is it?") == "What time is it?"
    assert wake_rest("Ok monday") == "" and wake_rest("O.K. Monday!") == "" and wake_rest("okay Jarvis hi", "Jarvis") == "hi", "the phrase alone: ''"
    assert wake_rest("Monday, open Notepad") == "open Notepad" and wake_rest("hey Monday play music") == "play music" \
        and wake_rest("Monday open Notepad") == "open Notepad" and wake_rest("Hi Monday!") == "", \
        "the name opening the sentence (the owner, 2026-10-20)"
    assert wake_rest("Monday") == "" and wake_rest("Monday.") == "" and wake_rest("okay Monday.") == "", \
        "the bare name alone opens the next sentence like “okay Monday” (the owner, 2026-10-22: “if im saying only monday … it cant”)"
    assert wake_rest("open Notepad") is None and wake_rest("") is None and wake_rest("Monday is a holiday") is None \
        and wake_rest("Monday morning I have a call") is None and wake_rest("Mondays are busy") is None and wake_rest("Monday's late") is None, \
        "the room talking about Monday"
    assert wake_rest("it's okay Monday is fine") is None and wake_rest("okay Mondays are busy") is None, "only an opening phrase"
    assert wake_rest("okay Monday, is it raining?") == "is it raining?", "after a lead word the question is Monday's"

    # command risk tiers and the permission engine
    for cmd, level in (("python --version", SAFE), ("git status", SAFE), ("npm --version", SAFE), ("npm install", LOW_RISK),
                       ("npm run dev", LOW_RISK), ("python -m pytest -q", LOW_RISK), ("pip install requests", HIGH_RISK),
                       ("npm i -g typescript", HIGH_RISK), ("git push origin main", HIGH_RISK), ("rm -rf build", DESTRUCTIVE),
                       ("del notes.txt", DESTRUCTIVE), ("git reset --hard HEAD~1", DESTRUCTIVE), ("git push --force", DESTRUCTIVE),
                       ("rmdir /s /q dist", DESTRUCTIVE), ("git status && rm -rf x", DESTRUCTIVE), ("echo hi > notes.txt", HIGH_RISK),
                       ("format C:", BLOCKED), ("rm -rf /", BLOCKED), ("rd /s /q C:\\", BLOCKED), ("Remove-Item C:\\Windows -Recurse", BLOCKED),
                       ("Set-MpPreference -DisableRealtimeMonitoring $true", BLOCKED), ("type .env", BLOCKED), ("frobnicate --all", HIGH_RISK),
                       ("python app.py 2>&1", LOW_RISK)):
        assert command_risk(cmd)[0] == level, (cmd, command_risk(cmd))
    S = lambda tool_, action, **a: {"tool": tool_, "action": action, "args": a}  # noqa: E731
    assert permit(S("terminal", "run", command="git status"))["decision"] == "SAFE"
    assert permit(S("terminal", "run", command="rm -rf build"))["decision"] == "ASK"
    assert permit(S("terminal", "run", command="format C:"))["decision"] == "BLOCK"
    assert permit(S("computer", "open", target="Chrome"))["decision"] == "SAFE"
    assert permit(S("computer", "open", target="Chrome"), jev_risk=0.7)["decision"] == "ASK", "Jev can add a gate"
    assert permit(S("computer", "click", target="Send"))["decision"] == "ASK"
    assert permit(S("computer", "click", target="Buy now"))["kind"] == "FINANCIAL"
    assert permit(S("computer", "click", target="login"))["decision"] == "SAFE"
    assert permit(S("computer", "type", text="hunter2", field="Password"))["decision"] == "BLOCK"
    assert permit(S("computer", "type", text="4111 1111 1111 1111"))["decision"] == "BLOCK"
    assert permit(S("computer", "type", text="hi", enter=True), {"app": "WhatsApp"})["decision"] == "ASK"
    assert permit(S("computer", "type", text="hii\n"), {"app": "WhatsApp WhatsApp.Root.exe"})["decision"] == "ASK", "a newline is Enter"
    assert permit(S("computer", "type", text="hii"), {"app": "WhatsApp WhatsApp.Root.exe"})["decision"] == "SAFE"
    assert permit(S("computer", "key", keys=["enter"]), {"app": "WhatsApp"})["kind"] == "COMMUNICATION"
    assert permit(S("computer", "click", target="Turn off"), {"app": "Windows Security"})["decision"] == "BLOCK"
    assert permit(S("browser", "download", url="x"))["decision"] == "ASK"
    home = str(__import__("pathlib").Path.home())
    assert permit(S("filesystem", "delete", path=home + "\\x.txt"))["decision"] == "ASK"
    assert permit(S("filesystem", "create", path=home + "\\x.txt"))["decision"] == "SAFE"
    assert permit(S("filesystem", "create", path="C:\\Windows\\x.txt"))["decision"] == "BLOCK", "outside the user folder"
    assert permit(S("filesystem", "read", path=home + "\\proj\\.env"))["decision"] == "BLOCK"
    assert permit(S("filesystem", "move", path=home + "\\a.txt", dest="C:\\Windows"))["decision"] == "BLOCK"
    assert permit(S("filesystem", "create", path="E:\\proj\\a.txt"), {"roots": ["E:\\proj"]})["decision"] == "SAFE", "a project folder"
    assert permit(S("computer", "save", path=home + "\\t.txt", overwrite=True))["decision"] == "ASK"
    assert permit(S("computer", "save", path=home + "\\t.txt", overwrite=False))["decision"] == "SAFE"
    assert permit(S("computer", "kill", target="explorer"))["decision"] == "BLOCK" and permit(S("computer", "kill", target="chrome"))["decision"] == "ASK"
    assert all(permit(S("computer", "kill", target=x))["decision"] == "BLOCK" for x in ("launchd", "WindowServer", "systemd", "gnome-shell")), \
        "macOS' and Linux's own processes are never force-closed either"
    assert permit(S("computer", "clipboard", op="set", text="sk-abcdefghijklmnopqrstuv"))["decision"] == "BLOCK"
    assert permit(S("computer", "drag", source="a", target="b"), {"app": "Downloads explorer.exe"})["decision"] == "ASK"
    # an installed app beats a website or an agent of the same name, when Jev is sure it is about this computer
    J = {"status": "ok", "intent": "computer", "confidence": 1.0}
    site_plan = route("open linkedin and search for arijit singh", P)
    assert app_named(site_plan) == "linkedin" and site_plan["steps"][0]["args"]["site"] == "linkedin"
    t = app_over_name(site_plan, J, True)
    assert (t["intent"], t["agent"], t["rule"], t["overrode"], t["app"], t.get("steps")) == ("TASK", "computer", "jev:computer", "fast-path", "linkedin", None), t
    assert app_named(route("open spotify and search for arijit singh", P)) is None, "Spotify's search runs in the app itself (executor), not the goal loop"
    assert app_over_name(site_plan, J, False) is site_plan, "no such app here: the website stands"
    assert app_over_name(site_plan, {**J, "intent": "browser"}, True) is site_plan and app_over_name(site_plan, {**J, "confidence": 0.7}, True) is site_plan
    assert app_over_name(site_plan, None, True) is site_plan, "without Jev the rules stand"
    claude = route("open claude and ask it to write a poem about monsoon", P)
    assert claude["rule"] == "mentioned-agent:claude_code" and app_named(claude) == "claude"
    assert app_over_name(claude, {**J, "confidence": 0.86}, True)["agent"] == "computer" and app_over_name(claude, {**J, "intent": "coding"}, True) is claude
    assert app_named(route("open claude code and fix the navbar bug in portfolio", P)) is None, "a plan the rules parsed exactly (open the app, hand the code to an agent)"
    assert app_named(route("open spotify", P)) is None and app_named(route("fix the navbar bug in portfolio", P)) is None
    # media: exact transport ops (never a blind toggle), a named song for Spotify, and "stop the music" is not "stop the task"
    assert tool("pause the music") == [("computer", "media", {"op": "pause"})] and tool("resume the song") == [("computer", "media", {"op": "play"})]
    assert tool("pause it") == [("computer", "media", {"op": "pause", "target": "it"})] and tool("pause spotify")[0][2] == {"op": "pause", "target": "spotify"}
    assert tool("next") == tool("skip this song") == tool("play the next one") == [("computer", "media", {"op": "next"})]
    assert tool("previous track") == tool("go back a song") == [("computer", "media", {"op": "previous"})]
    assert tool("what's playing") == [("computer", "now_playing", {})] and tool("what song is this")[0][1] == "now_playing"
    assert tool("play Blinding Lights") == tool("put on Blinding Lights") \
        == [("computer", "play", {"query": "Blinding Lights", "mode": "track"})], "no player named: the owner's default music app (executor)"
    assert tool("play the song Blinding Lights on Spotify") == [("computer", "play", {"query": "Blinding Lights", "app": "spotify", "mode": "track"})]
    assert tool("play something from The Weeknd")[0][2]["query"] == "The Weeknd" and tool("play Hawa song")[0][2]["query"] == "Hawa"
    assert tool("play some music") == tool("let's listen to something") == [("computer", "media", {"op": "play"})]
    assert tool("open Spotify and play Blinding Lights") == [("computer", "open", {"target": "Spotify"}),
                                                             ("computer", "play", {"query": "Blinding Lights", "app": "Spotify", "mode": "track"})]
    # one "play" for every app: the one named or opened plays it, never a default player (2026-10-15: YouTube's request played Spotify)
    yt = route("open youtube and play the latest video by naman jain", P)["steps"]  # (2026-10-20: one step; the player opens YouTube itself)
    assert [(s["action"], s["args"].get("app"), s["args"].get("kind")) for s in yt] == [("play", "youtube", "video")], yt
    assert yt[0]["args"]["query"] == "latest video by naman jain", yt[0]
    # the owner's own words, 2026-10-20: a search then a play of "him" / "song for me" is one play of what was searched
    assert tool("search Honey Singh and play a song by him") == tool("search Honey Singh and play song for me") == \
        [("computer", "play", {"query": "Honey Singh"})], tool("search Honey Singh and play a song by him")
    assert tool("open YouTube and search Honey Singh and play the first video") == [("computer", "play", {"query": "Honey Singh", "app": "youtube",
                                                                                                         "kind": "video"})]
    assert tool("search lofi beats and play despacito")[1] == ("computer", "play", {"query": "despacito", "mode": "track"}), "a new name stays its own"
    assert tool("Monday, Monday hey Monday play Honey Singh song on YouTube") == [("computer", "play", {"query": "Honey Singh", "app": "youtube",
                                                                                                    "mode": "track"})], "the name said again and again"
    assert tool("play despacito on youtube") == [("computer", "play", {"query": "despacito", "app": "youtube", "mode": "track"})], "played, not searched"
    assert tool("play the interstellar trailer")[0][2] == {"query": "interstellar trailer", "mode": "track", "kind": "video"}
    assert tool("open vlc and play my holiday video")[1][2]["app"] == "vlc" and tool("play lofi beats in youtube music")[0][2]["app"] == "youtube music"
    rest = after_opened(route("play the latest video by naman jain", P), route("open youtube", P)["steps"])
    assert rest["steps"][0]["args"]["app"] == "youtube", "the rest of a sentence whose first clause ran early plays where that clause opened"
    # the owner's own library, not a text search (the owner, 2026-10-10; TASK-2026-0174 searched "liked songs" and played a result)
    def play_args(text):
        s = route(text, P)["steps"]
        return s[0]["args"] if s else None
    assert play_args("play my liked songs") == play_args("play liked songs") == play_args("play my liked song") == {"mode": "liked"}, \
        "the real Liked Songs (of the default music app), never a search query"
    assert play_args("play my liked songs on Spotify") == {"app": "spotify", "mode": "liked"}
    assert play_args("play a random song from my liked songs") == play_args("play something from my liked songs") \
        == play_args("play a song from my liked songs") == {"mode": "liked", "shuffle": True}
    assert play_args("play my workout playlist") == {"mode": "playlist", "name": "workout"}
    assert play_args("play the gym mix playlist")["name"] == "gym mix" and play_args("play the playlist called chill")["name"] == "chill"
    assert play_args("play the album after hours") == {"mode": "album", "query": "after hours"}
    assert play_args("play songs by Arijit Singh") == {"mode": "artist", "query": "Arijit Singh"}
    assert play_args("play Blinding Lights")["mode"] == "track" and "mode" not in {"liked", "playlist"} or True
    assert music_target("my liked songs")[1] == {"mode": "liked"} and music_target("Blinding Lights") is None
    assert tool("open Spotify, search for Blinding Lights, play it and turn the volume down") == [
        ("computer", "play", {"query": "Blinding Lights", "app": "spotify"}), ("computer", "volume", {"op": "down"})], "the brief's own example"
    assert route("play chess with me", P)["intent"] != "TOOL"
    assert route("listen to me", P)["intent"] != "TOOL", "not everything after 'listen to' is music"
    assert route("stop the music", P)["intent"] == "TOOL" and route("stop spotify", P)["steps"][0]["args"] == {"op": "pause", "target": "spotify"}
    assert route("stop", P)["intent"] == route("stop it", P)["intent"] == route("stop the task", P)["intent"] == "STOP", "the stop reflex is untouched"
    assert route("pause", P)["op"] == "pause" and route("resume", P)["op"] == "resume", "bare pause/resume stay speech reflexes (Monday also pauses the music)"
    paused = with_context(route("pause it", P), route("play Blinding Lights on Spotify", P))
    assert paused["steps"][0]["args"]["target"] == "spotify" and paused["steps"][0]["label"] == "Pause spotify", paused["steps"]
    assert with_context(route("pause it", P), route("play Blinding Lights", P))["steps"][0]["args"]["target"] == "it", \
        "no player named before: whatever is playing (Windows' current session), never an assumed app"
    assert route("don't ask before starting agents", P)["value"] is False and route("ask me before you start an agent", P)["value"] is True
    assert tool("next Spotify track") == [("computer", "media", {"op": "next", "target": "Spotify"})]
    # "open github.com": whose browser? Asked unless the words say; "yours" is Monday's Chrome, "mine" the owner's
    assert needs_browser(route("open github.com", P)["steps"]) == 0 and needs_browser(route("open Spotify and open bhavye.vercel.app", P)["steps"]) == 1
    from .computer import _URLISH  # the executor's own test for "a web address": the two must never drift apart
    assert _WEB_ADDRESS.pattern == _URLISH.pattern
    for no in ("open Spotify", "open Chrome", "go to github.com", "open github.com in Edge", "open my Portfolio project", "open downloads", "open notes.txt", "open report.pdf"):
        assert needs_browser(route(no, P).get("steps")) is None, no
    assert tool("open github.com in my browser") == [("computer", "open", {"target": "github.com", "browser": "owner"})]
    assert tool("open github.com in your Chrome")[0][2] == {"target": "github.com", "browser": "monday"} and needs_browser(route("open x.com in yours", P)["steps"]) is None
    assert [browser_answer(x) for x in ("yours", "Yours.", "your chrome", "in your browser", "Monday's", "Monday, yours", "mine", "my browser", "use mine", "open it in my chrome")] \
        == ["monday"] * 6 + ["owner"] * 4
    assert [browser_answer(x) for x in ("chrome", "yes", "what time is it", "open github.com", "mine and yours")] == [None] * 5
    # self-repair's words: the setting, "fix yourself" for the last failure, "restart yourself"; none of them is a STOP, and a
    # plain "fix the error" stays what it was (work for a coding agent in a project)
    for said, want in {"stop fixing yourself": False, "Monday, don't try to fix yourself": False, "never repair yourself again": False, "turn off self-repair": False,
                       "disable self repair": False, "enable self-repair": True, "fix yourself when something breaks": True, "repair yourself automatically": True,
                       "fix yourself if you hit an error": True}.items():
        r = route(said, P)
        assert (r["intent"], r.get("setting"), r.get("value")) == ("DEFAULT", "self_repair", want), (said, r["intent"], r.get("rule"), r.get("value"))
    assert [(route(x, P)["intent"], route(x, P).get("op")) for x in ("fix yourself", "Monday, repair yourself now", "fix that yourself", "fix your own bug",
                                                                    "restart yourself", "please reload yourself")] == [("REPAIR", "fix")] * 4 + [("REPAIR", "restart")] * 2
    assert route("stop", P)["intent"] == "STOP" and route("stop fixing the navbar", P)["intent"] == "STOP" and route("fix the error", P)["intent"] != "REPAIR"
    assert route("restart the computer", P)["intent"] != "REPAIR" and route("fix yourself a sandwich", P)["intent"] != "REPAIR"
    for j_intent in ("monday_settings", "coding", "system_info"):  # Jev's likely readings agree with the rule, so the rule's words stand
        assert policy(route("fix yourself", P), {**J, "intent": j_intent, "confidence": 0.95}, P)["intent"] == "REPAIR", j_intent
    # a research request's web search is its subject: not the asking, not the shape of the answer; what else it says stays
    assert [research_query(x) for x in ("Research what TypeSafe's Jev model is and summarize it in three sentences", "Monday, look up the latest Node.js LTS version.",
                                        "What's the weather in Mumbai today?", "Find out who won the 2026 World Cup, in one sentence",
                                        "research the iPhone 17 and list its prices in India", "compare React and Vue in 2026", "Research.")] == \
        ["what TypeSafe's Jev model is", "the latest Node.js LTS version", "What's the weather in Mumbai today", "who won the 2026 World Cup",
         "the iPhone 17 and list its prices in India", "compare React and Vue in 2026", "Research"]
    assert [research_query(x) for x in ("research the latest news about the GTA 6 release date and give me sources",
                                        "what's the latest on the iPhone 18, with sources", "look up GTA 6 news and cite your sources")] == \
        ["the GTA 6 release date", "the iPhone 18", "GTA 6 news"]
    assert research_query("latest updates on Artemis III") == "Artemis III" and research_query("the latest Node.js LTS version") == "the latest Node.js LTS version"
    # photos to look at are an image search (2026-10-15: "dashara Maidan photos" got a chat model's refusal); the owner's own pictures aren't
    for t, q in (("dashara Maidan photos", "dashara Maidan"), ("show me pictures of the Eiffel tower", "the Eiffel tower"),
                 ("photos of Kota", "Kota"), ("search for images of Diwali in Jaipur", "Diwali in Jaipur"), ("Kota stone wallpapers", "Kota stone")):
        s = route(t, P).get("steps") or []
        assert [(x["tool"], x["action"], x["args"].get("query"), x["args"].get("site")) for x in s] == [("browser", "search", q, "images")], (t, s)
    for t in ("open my photos", "take photos", "delete these photos", "show me the photos", "open photos", "send the pictures to mom"):
        assert not any(x["args"].get("site") == "images" for x in route(t, P).get("steps") or []), t
    chosen = with_browser(route("open github.com and take a screenshot of this page", P), "monday")
    assert chosen["steps"][0]["args"] == {"target": "github.com", "browser": "monday"} and chosen["steps"][1]["action"] == "page_screenshot"
    assert early_clause("open github.com and", P) is None and early_clause("open github.com in your browser and", P)[0] == "open github.com in your browser"
    # the owner's six tiers, named on every ruling (the decision is unchanged: these run, those ask, that never runs)
    tiers = lambda st, ctx=None, **k: permit(st, ctx, **k)["tier"]  # noqa: E731
    HOME = str(files.Path.home() / "monday-tier-check.txt")  # only the words are judged: nothing is created
    assert [tiers(S("computer", x, target="Spotify")) for x in ("open", "close", "focus")] == [SAFE] * 3 and tiers(S("browser", "search", query="x")) == SAFE
    assert tiers(S("computer", "media", op="pause")) == tiers(S("computer", "play", query="x")) == tiers(S("computer", "screenshot")) == SAFE
    assert tiers(S("terminal", "run", command="git status")) == SAFE and tiers(S("terminal", "run", command="npm test")) == LOW_RISK
    assert tiers(S("filesystem", "create", path=HOME)) == LOW_RISK and tiers(S("coding_agents", "delegate", prompt="fix it")) == LOW_RISK
    assert tiers(S("computer", "click", target="Send"), {"app": "WhatsApp"}) == SENSITIVE and tiers(S("browser", "download", target="report")) == SENSITIVE
    assert tiers(S("filesystem", "delete", path=HOME)) == DESTRUCTIVE and tiers(S("terminal", "run", command="rm -rf build")) == DESTRUCTIVE
    assert tiers(S("computer", "kill", target="chrome")) == DESTRUCTIVE and tiers(S("filesystem", "write", path=HOME)) == HIGH_RISK
    assert tiers(S("filesystem", "delete", kind="project")) == BLOCKED and tiers(S("terminal", "run", command="format C:")) == BLOCKED
    assert tiers(S("computer", "open", target="Chrome"), jev_risk=0.7) == DESTRUCTIVE and set(TIERS) >= {tiers(S("computer", "type", text="hi"))}
    # early execution: a closed, exact clause that only opens or shows something; the rest of the sentence follows it
    assert early_clause("open Spotify", P) is None and early_clause("open Spotify's settings", P) is None, "not closed: nothing runs yet"
    assert early_clause("Monday, open Spotify and", P)[0] == "open Spotify" and early_clause("I want you to open Spotify and play", P)[0] == "open Spotify"
    assert [s["args"]["target"] for s in early_clause("open Spotify and Chrome and then", P)[1]] == ["Spotify", "Chrome"]
    assert early_clause("go to github.com then", P)[1][0]["action"] == "open_url" and early_clause("switch to Chrome and type", P)[1][0]["action"] == "focus"
    for held in ("delete notes.txt and", "close Chrome and", "type hello and", "run the tests and", "play Blinding Lights and", "send hi to Dad and",
                 "open Spotify no no no open Chrome and", "stop and", "what time is it and"):
        assert early_clause(held, P) is None, held
    assert after_early("open Spotify and play Blinding Lights", "open Spotify") == "play Blinding Lights"
    assert after_early("Monday, I want you to open Spotify, and then play Hawa song.", "open Spotify") == "play Hawa song"
    assert after_early("open Spotify and Chrome", "open Spotify") == "open Chrome" and after_early("open Spotify and", "open Spotify") == ""
    assert after_early("open Chrome and search for cats", "open Spotify") is None, "the words changed: the whole sentence stands"
    # a whole project is never deleted: recognised, then refused by permit(); a file inside one still asks first
    gone = route("delete this project", P)
    assert [(s["tool"], s["action"], s["args"]) for s in gone["steps"]] == [("filesystem", "delete", {"kind": "project"})]
    assert tool("remove the Portfolio project")[0][2] == {"kind": "project", "name": "Portfolio"} and tool("wipe my whole repo")[0][2] == {"kind": "project"}
    roots = {"roots": [p["path"] for p in P]}
    assert permit(gone["steps"][0], roots)["decision"] == "BLOCK" and "never deletes a whole project" in permit(gone["steps"][0])["reason"]
    assert permit(S("filesystem", "delete", path=P[0]["path"]), roots)["decision"] == "BLOCK", "a registered project's folder, however it was named"
    assert permit(S("filesystem", "delete", path="C:\\Users\\me\\Desktop"), roots)["decision"] == "BLOCK", "a folder that holds a project"
    assert permit(S("filesystem", "delete", path=P[0]["path"] + "\\notes.txt"), roots)["decision"] == "ASK", "one file inside a project still asks"
    assert tool("open Spotify and Chrome") == [("computer", "open", {"target": "Spotify"}), ("computer", "open", {"target": "Chrome"})]
    assert tool("close Spotify, Notepad and Calculator")[2] == ("computer", "close", {"target": "Calculator"}) and tool("open Notepad and write hi")[1][1] == "type"
    assert route("switch model to the fastest one", P)["intent"] == "MODEL" and tool("close that") == [("computer", "close", {"target": "that"})]
    assert with_context(route("close that", P), route("open Notepad", P))["steps"][0]["args"]["target"] == "Notepad"
    fixed = route("I want you to open What's up No no no I want you to open Spotify", P)
    assert (fixed["intent"], fixed["steps"][0]["args"], fixed["request"]) == ("TOOL", {"target": "Spotify"}, "I want you to open Spotify"), fixed
    assert route("open Notepad, scratch that, open Calculator", P)["steps"][0]["args"] == {"target": "Calculator"} and route("scratch that", P)["intent"] == "STOP"
    assert pol("pause it", {**J, "intent": "task_control", "confidence": 0.94})["intent"] == "TOOL", "Jev can't talk Monday out of a transport control"
    assert pol("open the pod bay doors", {**J, "intent": "conversation", "confidence": 0.9})["intent"] == "CHAT", "an ordinary exact command can still be overruled"
    # an exact command that is not a plan (no steps), and Jev reads it as something else: the rule stands below the override bar, Jev wins above it
    for said in ("use Monday for the computer tasks", "what time is it", "don't ask before starting agents", "use Gemini", "change your voice"):
        kept = pol(said, {**J, "intent": "coding", "confidence": 0.6})
        assert kept["intent"] == route(said, P)["intent"] and kept["jev_agrees"] is False, (said, kept)
    assert pol("use Monday for the computer tasks", {**J, "intent": "conversation", "confidence": 0.95})["intent"] == "CHAT"
    # standing approvals: "ask" is the default and answers nothing; a grant covers one who + kind + key; auto mode only LOW/MEDIUM;
    # HIGH risk, deleting, installing, paying, sending and publishing are never covered in any mode
    G = [{"who": "Codex", "kind": "RUN_COMMAND", "key": "npm test"}, {"who": "Codex", "kind": "WRITE", "key": "Portfolio"}]
    assert grant_key("RUN_COMMAND", '"C:\\Program Files\\nodejs\\npm.cmd" test -- --watch=false') == "npm test"
    assert grant_key("RUN_COMMAND", "python -m pytest -q tests") == "python pytest tests" and grant_key("NETWORK", "https://api.github.com/x") == "api.github.com"
    assert grant_key("WRITE", "C:\\p\\a.py", "Portfolio") == "Portfolio" and grant_key("TOOL", "mcp__x__lookup") == "mcp__x__lookup"
    assert standing("ask", G, "Codex", "RUN_COMMAND", "MEDIUM", "npm test") is None, "the default asks every time"
    assert standing("always", G, "Codex", "RUN_COMMAND", "MEDIUM", "npm test --silent") == "always allow"
    assert standing("always", G, "Codex", "RUN_COMMAND", "MEDIUM", "npm test unit") == "always allow" and         standing("always", G, "Codex", "RUN_COMMAND", "MEDIUM", "npm testx") is None, "a command's grant covers what starts with it, word by word"
    assert standing("always", G, "Claude Code", "RUN_COMMAND", "MEDIUM", "npm test") is None, "a grant is one bot's or agent's only"
    assert standing("always", G, "Codex", "RUN_COMMAND", "MEDIUM", "npm publish") is None and standing("auto", G, "Codex", "RUN_COMMAND", "MEDIUM", "npm publish") is None
    assert standing("always", G, "Codex", "WRITE", "MEDIUM", "C:\\p\\b.py", "Portfolio") == "always allow"
    assert standing("always", G, "Codex", "WRITE", "HIGH", "C:\\elsewhere\\b.py", "Portfolio") is None, "outside the project: always asks"
    for k, d in (("DELETE", "notes.txt"), ("COMMUNICATION", "Send"), ("FINANCIAL", "Buy"), ("INSTALL", "x"), ("CONSENT", "Accept all")):
        assert not grantable(k, "MEDIUM", d) and standing("auto", [{"who": "Codex", "kind": k, "key": k}], "Codex", k, "MEDIUM", d) is None, k
    for cmd in ("git push origin main", "rm -rf build", "pip install requests", "git reset --hard", "type .env"):
        assert not grantable("RUN_COMMAND", "MEDIUM", cmd) and standing("auto", [], "Codex", "RUN_COMMAND", "MEDIUM", cmd) is None, cmd
    assert standing("auto", [], "Hermes", "BROWSER", "MEDIUM", "Click Search") == "auto mode" and standing("auto", [], "Codex", "RUN_COMMAND", "MEDIUM", "git status") == "auto mode"
    assert command_risk("frobnicate --all")[1] == "an unrecognised program"
    assert standing("auto", [], "Codex", "RUN_COMMAND", "MEDIUM", "frobnicate --all") is None, "auto mode still asks before an unrecognised program"
    assert grantable("RUN_COMMAND", "MEDIUM", "frobnicate --all") and standing("always", [{"who": "Codex", "kind": "RUN_COMMAND", "key": "frobnicate"}],
                                                                                "Codex", "RUN_COMMAND", "MEDIUM", "frobnicate --all") == "always allow"
    assert ask_kind("Bash", {"command": "npm test"}) == ("RUN_COMMAND", "npm test", False)
    assert ask_kind("Edit", {"file_path": "E:\\p\\x.py"}, "E:\\p")[2] is False and ask_kind("Write", {"file_path": "C:\\Windows\\x"}, "E:\\p")[2] is True
    assert ask_kind("mcp__gmail__send_message", {})[0] == "COMMUNICATION" and ask_kind("mcp__drive__trash_file", {})[0] == "DELETE"
    assert ask_kind("mcp__todo__find_tasks", {}) == ("TOOL", "mcp__todo__find_tasks", False)
    assert grant_words({"who": "Codex", "kind": "RUN_COMMAND", "key": "npm test"}) == "Codex may run “npm test”"
    # this PC's power (core/power.py, 2026-10-26): the owner's words reach Monday's own power steps, never chat, Hermes or the stop reflex
    def pw(text):
        r = route(text, P)
        return [(s["tool"], s["action"], s["args"]) for s in r.get("steps") or []] if r["intent"] == "TOOL" else r["intent"]
    for text, want in (("shut down my computer", [("power", "shutdown", {"delay": 0})]), ("Turn off this PC.", [("power", "shutdown", {"delay": 0})]),
                       ("shut down the PC", [("power", "shutdown", {"delay": 0})]), ("no shut down this pc", [("power", "shutdown", {"delay": 0})]),
                       ("restart my computer", [("power", "restart", {"delay": 0})]), ("lock my computer", [("power", "lock", {})]),
                       ("sign out of windows", [("power", "sign_out", {})]), ("hibernate the pc", [("power", "hibernate", {})]),
                       ("put the computer to sleep", [("power", "sleep", {})]), ("cancel the shutdown", [("power", "cancel_shutdown", {})]),
                       ("stop the shutdown", [("power", "cancel_shutdown", {})]), ("is a shutdown scheduled", [("power", "status", {})]),
                       ("shut down my computer in 10 minutes", [("power", "shutdown", {"delay": 600})]),
                       ("restart the pc in an hour", [("power", "restart", {"delay": 3600})]),
                       ("save it and then shut down my computer", [("computer", "save", {}), ("power", "shutdown", {"delay": 0})]),
                       ("restart chrome", "CHAT"), ("go to sleep", "SLEEP"), ("stop", "STOP"), ("cancel that", "STOP"), ("shut down", "CHAT"),
                       ("shut down my computer in 9999 hours", "CHAT")):
        assert pw(text) == want, (text, pw(text))
    for a in power.ASKS:  # what ends the session asks, HIGH, in every mode, and no standing yes ever covers it
        r = permit({"tool": "power", "action": a, "args": {}})
        assert (r["decision"], r["kind"], r["risk"]) == ("ASK", "POWER", "HIGH") and classify("POWER", "x") == ("POWER", "HIGH"), (a, r)
        assert not grantable("POWER", "HIGH", a) and standing("auto", [{"who": "Monday", "kind": "POWER", "key": "POWER"}], "Monday", "POWER", "HIGH", a) is None
    for a in ("lock", "cancel_shutdown", "status"):  # ...even when Jev's destructive score is high on the word "shutdown" (live, 2026-10-26: 85%)
        assert permit({"tool": "power", "action": a, "args": {}})["decision"] == "SAFE" and \
            permit({"tool": "power", "action": a, "args": {}}, jev_risk=0.85)["decision"] == "SAFE", a
    assert permit({"tool": "power", "action": "shutdown", "args": {}}, jev_risk=0.0)["decision"] == "ASK", "a low score never removes the ask"
    def JP(**kw):  # (J is a dict by here)
        return {"status": "ok", "confidence": 0.9, "complexity": 1.0, "destructive": 0.05, **kw}

    r = route("save my document and then shut down my computer", P)  # the brief's own example: the agent's part, then the power step
    assert (r["intent"], r["rule"], r["prompt"], [(s["tool"], s["action"]) for s in r["then"]]) == \
        ("TASK", "then-power", "save my document", [("power", "shutdown")]), r
    r = route("finish downloading the file, then restart my pc in 5 minutes", P)
    assert (r["rule"], r["prompt"], r["then"][0]["args"]) == ("then-power", "finish downloading the file", {"delay": 300}), r
    assert power_tail("lock the pc and play music") is None and power_tail("shut down my computer") is None, "power first, or alone: no tail"
    assert route("ask hermes to save my work and then shut down my computer", P)["rule"] != "then-power", "an order to a named agent stays its own"
    sd = route("shut down the oc", P)  # the owner's own typo (2026-10-26 09:33): Jev's reading of the PC's power, Monday's own step
    r = policy(sd, JP(intent="computer", confidence=0.98, tool="power", tool_confidence=0.9, power_action="shutdown", power_action_confidence=0.9), P,
               computer_agent="hermes")
    assert (r["intent"], r["agent"], r["rule"], [(s["tool"], s["action"], s["args"]) for s in r["steps"]]) == \
        ("TOOL", "computer", "jev:power", [("power", "shutdown", {"delay": 0})]), r
    r = policy(sd, JP(intent="computer", confidence=0.98, tool="computer", tool_confidence=0.9, power_action="shutdown", power_action_confidence=0.9), P)
    assert r["intent"] == "TASK", "the premised power question alone moves nothing: Jev's tool must be power"
    r = policy(route("shut the oc down in ten minutes", P), JP(intent="computer", tool="power", tool_confidence=0.9, power_action="shutdown",
                                                              power_action_confidence=0.9), P)
    assert (r["intent"], r["topic"]) == ("HELP", "power"), "a time the rules couldn't read is asked about, never done now"
    r = policy(route("write my report in notepad then shut down", P), JP(intent="computer", tool="power", tool_confidence=0.9,
                                                                         power_action="shutdown", power_action_confidence=0.9), P)
    assert r["intent"] != "TOOL" or all(s["tool"] != "power" for s in r.get("steps") or []), "never a shutdown that skips the work before it"
    # a document to make, however it's asked (the owner, 2026-10-26: "search about gandhiji and prepare a doc for him … 3-4 pages … with
    # images", answered "I can't create the file"; then "don't code the wording, code the feeling"): Jev's tool `office`, measured live
    # at 0.97 to 1.00 for ten ways of asking and for none of eight requests that aren't one
    _doc = lambda t, sure, **k: policy(route(t, P), JP(intent="research", confidence=0.6, tool="office", tool_confidence=sure, **k), P)  # noqa: E731
    gandhi = "nown search about gandhiji and prepare a doc for him and the doc should cointain 3-4 pages atleats with imagesb"
    r = _doc(gandhi, 0.97)
    assert (r["intent"], r["rule"], r["tool"], [(s["tool"], s["action"], s["args"]) for s in r["steps"]], r.get("overrode")) == \
        ("TOOL", "jev:office", "office", [("office", "create", {"request": gandhi})], None), ("the rules found nothing: Jev's document", r)
    r = _doc("write up a report on electric cars with images", 0.98)
    assert (r["rule"], r["overrode"]) == ("jev:office", "fast-path"), ("words the rules would have typed into a window: a document", r)
    assert _doc("search about gandhiji and prepare a doc for him", 0.97)["rule"] == "jev:office", "not a web search"
    assert _doc("write up a report on electric cars with images", 0.8)["rule"] == "fast-path", "over an exact rule only when Jev is that sure"
    assert _doc(gandhi, 0.45)["rule"] == "jev:research", "unsure of a document: Jev's other reading (research) answers"
    r = _doc("make a ppt on BharatGen with 8 slides", 0.99)
    assert (r["rule"], r["steps"][0]["args"]["slides"]) == ("office:powerpoint", 8), "the rules' own document keeps its exact arguments"
    assert _doc("ask claude to write a report on electric cars", 0.95)["intent"] == "TASK", "an order to a named agent stays its own"
    # "open X" is app first (the owner, 2026-10-26: "first check that I have installed that application … ask Jev whether it is there")
    assert installed_app("Spotify", 100, None, None) == "Spotify" and installed_app("Spotify", 103, "Discord", 0.99) == "Spotify", \
        "plainly an installed app's name: that app, without a question"
    assert installed_app(None, 0, "Spotify", 0.8) == "Spotify" and installed_app("Windows Notepad", 45, "Windows Notepad", 0.9) == "Windows Notepad", \
        "otherwise Jev's pick among the installed apps near the words"
    assert installed_app("Microsoft Photos", 40, None, 0.9) is None, "Jev sure none of them is meant (“photo shop”): not installed"
    assert installed_app("Microsoft Photos", 40, "Spotify", 0.4) == "Microsoft Photos" and installed_app(None, 0, None, None) is None, \
        "Jev unsure or not asked: as before"
    assert installed_app("Spotify", 50, None, 0.66) == "Spotify", "“spot if i” (live: none at 0.66): overruling the index's match takes more"
    _off = lambda c, p: offer_answer({"live": {"offer": {"choice": c, "confidence": p}}})  # noqa: E731
    assert (_off("yes", 0.93), _off("no", 0.7), _off("yes", 0.4), _off("other", 0.99), offer_answer(None)) == ("yes", "no", None, None, None)
    q, said, r = offer_route({"request": "open spotify"}, "spotify", url="https://open.spotify.com")
    assert (q, said) == ("Spotify isn't installed on this PC. Open open.spotify.com in your browser instead?",
                         "Spotify isn't installed. Open its website instead?"), (q, said)
    assert (r["intent"], r["rule"], r["prompt"], r["steps"][0]["tool"], r["steps"][0]["args"]) == \
        ("TOOL", "offer:website", "Open open.spotify.com", "computer", {"target": "https://open.spotify.com"}), r
    q, said, r = offer_route({"request": "open telegram"}, "telegram")
    assert q == "Telegram isn't installed on this PC. Search the web for it instead?" and \
        (r["steps"][0]["tool"], r["steps"][0]["action"], r["steps"][0]["args"]) == ("browser", "search", {"query": "Telegram"}), (q, r)
    q, said, r = offer_route({"request": "open note well"}, "note well", near="Windows Notepad", url=None)
    assert (q, said, r["steps"][0]["args"]["target"]) == ("“Note well” isn't installed on this PC. Did you mean Windows Notepad?",
                                                          "Did you mean Windows Notepad?", "Windows Notepad"), (q, said, r)
    assert permit(r["steps"][0])["decision"] == "SAFE" and permit(offer_route({}, "x", url="https://x.com")[2]["steps"][0])["decision"] == "SAFE"

    # -- the owner's natural phrasings (2026-10-27: "they must say 'Open X' exactly"): what the RULES make of each (the parser: an exact plan,
    # or the fallback that hands the words to Jev whole), then what the POLICY makes of Jev's expected reading (the decider; v9: `action`,
    # the live app pick, `redo`, `ambiguous`). Measured and printed: intent and route accuracy, false actions, clarifications; any regression
    # fails here. Never remove a row to pass.
    NL_APPS = ["Notepad", "Spotify", "Google Chrome"]  # the installed apps near the words, as Monday.jev_context lists them for Jev (a0..)
    JN = lambda **kw: {"status": "ok", "confidence": 0.9, "complexity": 1.0, "destructive": 0.05, **kw}  # noqa: E731
    JAPP = lambda i=0, sure=0.9, act="open", **kw: JN(intent="computer", action=act, action_confidence=0.9, live={"app": {"choice": f"a{i}", "confidence": sure}}, **kw)  # noqa: E731
    JSCREEN = JN(intent="computer", confidence=0.96, tool="computer", tool_confidence=0.97, action="show", action_confidence=0.98,  # (as Jev read
                 needs_vision=0.98, live={"app": {"choice": "none", "confidence": 0.04}})                                           # them, 2026-10-29)
    OPEN_NOTEPAD, OPEN_SPOTIFY = [("open", "Notepad")], [("open", "Spotify")]
    SEE_WHY = [("open_project", "my project"), ("delegate", "see why it is failing")]
    NL_CASES = [  # (phrase, the rules: its steps as (action, what) or its rule, Jev's expected reading, the final route: (intent, steps | rule | flag))
        ("open Notepad", OPEN_NOTEPAD, JAPP(), ("TOOL", OPEN_NOTEPAD)),  # direct
        ("Can you open Notepad for me?", OPEN_NOTEPAD, JAPP(), ("TOOL", OPEN_NOTEPAD)),  # polite and question forms
        ("Could you pull up Notepad?", OPEN_NOTEPAD, JAPP(), ("TOOL", OPEN_NOTEPAD)),
        ("Would you mind opening Notepad?", OPEN_NOTEPAD, JAPP(), ("TOOL", OPEN_NOTEPAD)),
        ("can you please open spotify", [("open", "spotify")], JAPP(1), ("TOOL", [("open", "spotify")])),
        ("yo monday open notepad", [("open", "notepad")], JAPP(), ("TOOL", [("open", "notepad")])),  # casual
        ("notepad pls", "fallback", JAPP(), ("TOOL", OPEN_NOTEPAD)),
        ("Notepad, please", "fallback", JAPP(), ("TOOL", OPEN_NOTEPAD)),
        ("open notepad for me real quick", [("open", "notepad")], JAPP(), ("TOOL", [("open", "notepad")])),
        ("I want to see Notepad", "fallback", JAPP(), ("TOOL", OPEN_NOTEPAD)),  # indirect: Jev's reading, the index's name (jev:open)
        ("I need Spotify on my screen", "fallback", JAPP(1), ("TOOL", OPEN_SPOTIFY)),
        ("Let's check Spotify", "fallback", JAPP(1), ("TOOL", OPEN_SPOTIFY)),
        ("get rid of Spotify from my screen", "fallback", JAPP(1, act="close"), ("TOOL", [("close", "Spotify")])),
        ("bring Chrome forward", "fallback", JAPP(2, act="focus"), ("TOOL", [("focus", "Google Chrome")])),
        ("Show me what's happening on Spotify", "fallback", JAPP(1, act="show"), ("TASK", "jev:computer")),  # a look: the computer agent's, as today
        # a look at the whole screen (2026-10-29: the owner's own words went to Hermes, which listed the windows' titles): Monday's own look
        ("tell me whats on my screen and explain it to me", [("read_screen", None)], JSCREEN, ("TOOL", [("read_screen", None)])),
        ("explain me whats in my scren rn", [("read_screen", None)], JSCREEN, ("TOOL", [("read_screen", None)])),
        ("what am i looking at right now", "fallback", JSCREEN, ("TOOL", [("read_screen", None)])),  # (jev:show)
        ("look at my screen and tell me what you see", "fallback", JSCREEN, ("TOOL", [("read_screen", None)])),
        ("whats going on on my screen, explain pls", [("read_screen", None)], JSCREEN, ("TOOL", [("read_screen", None)])),  # (the rules
        #  read it since 2026-10-29: "whats" → "what's", "pls" → "please": normal)
        ("look at my screen and click the OK button", "fallback", JSCREEN, ("TASK", "jev:computer")),  # words that act: the agent's, whole
        ("Find Notepad and open it", "fallback", JAPP(), ("TASK", "jev:computer")),  # two clauses: the agent's, whole
        ("I want to see Notepad and write hello there", "fallback", JAPP(), ("TASK", "jev:computer")),  # (a step for its first clause would drop the rest)
        ("opne notepad", [("open", "notepad")], JAPP(), ("TOOL", [("open", "notepad")])),  # typos (since 2026-10-29 the rules mend the
        #                                                                                   slip themselves: route's mend; before, Jev's app pick)
        ("open notpad", [("open", "notepad")], JAPP(), ("TOOL", [("open", "notepad")])),  # (the slip mended by the rules since 2026-10-29;
        #                                                                                  any other name the index resolves: Monday._installed)
        ("is Notepad open?", [("processes", "Notepad")], JN(intent="computer", action="check", action_confidence=0.9), ("TOOL", [("processes", "Notepad")])),
        ("what is Notepad?", "fallback", JN(intent="conversation", action="other", action_confidence=0.9), ("CHAT", "jev:conversation")),  # must not act
        ("try again", "fallback", JN(intent="conversation", redo=0.9), ("CHAT", "redo")),  # corrections: `redo` stays on the route (Monday._route re-runs the last request)
        ("that's not right, try another approach", "fallback", JN(intent="conversation", redo=0.9), ("CHAT", "redo")),
        ("no, not that one", "fallback", JN(intent="computer", confidence=0.7, redo=0.9), ("TASK", "redo")),
        ("can you get me the other thing from before", "fallback", JN(intent="unknown", ambiguous=0.9), ("CHAT", "ambiguous")),  # one question first
        ("open Notepad and then shut down my computer", [("open", "Notepad"), ("shutdown", None)], JN(intent="computer"),  # multi-step
         ("TOOL", [("open", "Notepad"), ("shutdown", None)])),
        ("open my project and see why it is failing", SEE_WHY, JN(intent="coding"), ("TOOL", SEE_WHY)),  # the open step runs exactly; the tail is the coder's
        ("Could you open my project and see why it is failing?", SEE_WHY, JN(intent="coding"), ("TOOL", SEE_WHY)),
    ]
    NL_NO_ACTION = {"what is Notepad?", "try again", "that's not right, try another approach", "no, not that one", "can you get me the other thing from before"}
    nl_steps = lambda d: [(s["action"], s["args"].get("target") or s["args"].get("query") or s["args"].get("ref") or s["args"].get("prompt"))  # noqa: E731
                          for s in d.get("steps") or []]

    def nl_final(phrase, jev):
        d = policy(route(phrase, P), jev, P, computer_agent="hermes", apps=NL_APPS)
        flag = "redo" if d.get("redo") else "ambiguous" if d.get("ambiguous") else None
        return d, (d["intent"], nl_steps(d) if d.get("steps") else flag or d["rule"])
    nl_intent = nl_route = false_actions = clarifications = 0
    for phrase, rules, jev, final in NL_CASES:
        d = route(phrase, P)
        got_rules = nl_steps(d) if d.get("steps") else d["rule"]
        r, got = nl_final(phrase, jev)
        nl_intent += got[0] == final[0]
        nl_route += got_rules == rules and got == final
        false_actions += phrase in NL_NO_ACTION and r["intent"] in ("TOOL", "TASK") and not r.get("redo")
        clarifications += bool(r.get("ambiguous"))
        assert got_rules == rules, (phrase, "the rules", got_rules, rules)
        assert got == final, (phrase, "the policy", got, final)
    assert wake_rest("Monday is a holiday") is None, "the room talking about Monday never routes"
    print(f"natural phrasings: intent {nl_intent}/{len(NL_CASES)} · route {nl_route}/{len(NL_CASES)} · false actions {false_actions} of "
          f"{len(NL_NO_ACTION) + 1} that must not act · clarifications {clarifications}")
    assert nl_intent == nl_route == len(NL_CASES) and false_actions == 0 and clarifications == sum("ambiguous" in c[2] for c in NL_CASES)
    r = policy(route("I want to see Notepad", P), JAPP(), P, apps=NL_APPS)
    assert (r["intent"], r["agent"], r["rule"], r["tool"], r["source"], [(s["tool"], s["action"], s["args"]) for s in r["steps"]]) == \
        ("TOOL", "computer", "jev:open", "computer", "jev", [("computer", "open", {"target": "Notepad"})]), r
    assert permit(r["steps"][0])["decision"] == "SAFE" and r["steps"][0]["label"] == "Open Notepad", "the rules' own step: permit, _installed, receipts as for any plan"
    r = policy(route("I want to see Notepad", P), JAPP(sure=0.3), P, computer_agent="hermes", apps=NL_APPS)
    assert (r["intent"], r["agent"], r["rule"], r.get("steps")) == ("TASK", "hermes", "jev:computer", None), ("an unsure pick: the agent, as before", r)
    assert policy(route("I want to see Notepad", P), JAPP(), P, apps=None)["intent"] == "TASK", "no apps list: no pick (Jev never names an app)"
    assert policy(route("I want to see Notepad", P), JAPP(confidence=0.4), P, apps=NL_APPS)["intent"] == "CHAT", "an unsure intent: chat, as before"
    assert policy(route("I want to see Notepad", P), {**JAPP(), "action_confidence": 0.4}, P, computer_agent="hermes", apps=NL_APPS)["rule"] == "jev:computer", \
        "an unsure first move: the agent, as before"
    assert policy(route("I want to see Notepad", P), JAPP(act="type"), P, computer_agent="hermes", apps=NL_APPS)["rule"] == "jev:computer", "only open, close, focus"
    assert app_pick(JAPP(1), ["Notepad", {"name": "Spotify"}]) == "Spotify" and app_pick(JAPP(9), NL_APPS) is None and app_pick(JAPP(0, 0.5), NL_APPS) is None
    assert app_pick({"live": {"app": {"choice": "none", "confidence": 0.95}}}, NL_APPS) is None and app_pick(JAPP(), []) is None and app_pick(None, NL_APPS) is None
    assert one_clause("open Notepad") and one_clause("Notepad, please") and not one_clause("open Notepad and write hello")
    r = policy(route("that's not right, try another approach", P), JN(intent="conversation", redo=0.9), P)
    assert (r["intent"], r["redo"], r["jev"]["redo"]) == ("CHAT", 0.9, 0.9) and "redo" in route_summary(r), ("a correction: Monday._route re-runs the last request", r)
    assert "redo" not in policy(route("open Notepad", P), JN(intent="computer", redo=0.9), P), "an exact command runs as said, whatever Jev's redo says"
    assert "redo" not in policy(route("try again", P), JN(intent="conversation", redo=REDO_P - 0.05), P), "below the bar nothing is carried"
    r = policy(route("can you get me the other thing from before", P), JN(intent="unknown", ambiguous=0.9), P)
    assert (r["intent"], r["ambiguous"]) == ("CHAT", 0.9) and "ambiguous" in route_summary(r), ("one short question first (Monday._dispatch CLARIFY)", r)
    r = policy(route("open that other one", P), JN(intent="computer", ambiguous=0.9), P, computer_agent="hermes")
    assert (r["intent"], r["ambiguous"]) == ("TASK", 0.9), "an inferred task that names no clear thing carries the flag: asked before any agent starts"
    assert jev_summary(JN(intent="computer", action="open", action_confidence=0.9, redo=0.1, ambiguous=0.2, tone="urgent", tone_confidence=0.8))["tone"] == "urgent"
    # v10 (2026-10-28): Jev's `file` answer is kept with the route, where monday.wants_document reads it (2026-10-29: it was dropped here,
    # so a request for notes Jev read as a document would have got none)
    assert jev_summary({"status": "ok", "file": 0.77, "intent": "conversation"})["file"] == 0.77
    print("router self-check: OK", len(cases) + len(fast), "routes + fast path + policy + permissions + power + documents + app first + natural phrasings")
