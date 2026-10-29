"""Tool Registry: Monday's own tools, what each can do, and what is actually built and working right now.

- Every capability carries its risk rule; router.permit() makes the per-step ruling, the text here is its summary.
- NOT_BUILT names capabilities the executor doesn't run yet. They are listed as "not built", never offered. The
  executor's self-check proves it both ways: every built capability of an executor tool reaches a handler in
  executor._act, and no NOT_BUILT one does.
- Live status comes from real checks (Monday.tool_status), in the owner's vocabulary: available, unavailable,
  detected but unsupported, not authenticated, error, each with the reason. Nothing defaults to "ready".
- Jev picks among JEV_TOOLS (its `tool` question). Availability is checked by code afterwards, never by Jev.
GET /api/tools serves the registry with live status.
"""
SAFE, ASK, BLOCK, RULED = "safe", "asks you", "never", "depends on the command"
AVAILABLE, UNAVAILABLE, UNSUPPORTED, NOT_AUTHENTICATED, ERROR = (
    "available", "unavailable", "detected_unsupported", "not_authenticated", "error")
STATUSES = (AVAILABLE, UNAVAILABLE, UNSUPPORTED, NOT_AUTHENTICATED, ERROR)
# Tools whose capabilities core/executor.py runs as plan steps; the rest are Monday Core's own handlers.
EXECUTOR_TOOLS = ("computer", "browser", "applications", "filesystem", "terminal", "coding_agents", "google", "vision", "office", "power")

TOOLS = {
    "computer": {"label": "Computer", "description": "Open, close and switch apps and windows; click, type, press keys, drag and "
                 "scroll; save documents; read the screen and a window's controls; show where something is (circled on the screen, nothing clicked); "
                 "the clipboard; what's running; screenshots; a photo with the Camera app; "
                 "volume; what's playing, pause, resume and skip (checked against Windows' media sessions); play something in the app named or "
                 "opened (Spotify through its own adapter and the owner's library; any other player through the general screen loop), "
                 "else the owner's default music or video app.",
                 "capabilities": {"open": SAFE, "close": SAFE, "focus": SAFE, "window": SAFE, "windows": SAFE, "read_screen": SAFE,
                                  "screenshot": SAFE, "point": SAFE, "photo": SAFE, "scroll": SAFE, "volume": SAFE, "media": SAFE, "play": SAFE, "now_playing": SAFE, "wait": SAFE,
                                  "click": "asks when the label sends, pays, deletes, installs or changes an account",
                                  "type": "never types passwords, card numbers or keys; Enter in a messaging app asks",
                                  "key": "Enter in a messaging app asks", "move_mouse": SAFE,
                                  "drag": "asks in File Explorer, where a drag moves files",
                                  "save": "asks before replacing an existing file", "inspect": SAFE, "processes": SAFE,
                                  "clipboard": "never reads out or copies passwords, card numbers or keys",
                                  "transfer": "copies the selected words (or an app's last answer) into another app; nothing is sent",
                                  "kill": "asks you; Windows' own processes never"}},
    "browser": {"label": "Browser", "description": "Your own browser (the default Windows registers, or one you name), on your desktop: "
                "sites, searches and new tabs open in it signed in as you are, its page is read and worked through its window like any app's, "
                "and a YouTube video is picked there by Hermes and checked against Windows' media session. Monday's own Chrome (its own "
                "profile, driven through the page itself: DevTools, the DOM and its accessible names) only when you say “in your Chrome”.",
                "capabilities": {"open_url": SAFE, "search": SAFE, "read_page": SAFE,
                                 "click_element": "asks before send, pay, delete, post and the like", "fill_form": "never types passwords",
                                 "download": ASK, "new_tab": SAFE, "switch_tab": SAFE, "close_tab": SAFE, "back": SAFE,
                                 "page_screenshot": SAFE, "console": SAFE,
                                 "play_video": "a YouTube video: its ads skipped and pop-ups dismissed, never anything accepted"}},
    "power": {"label": "Power", "description": "This PC's own power through Windows' own calls (core/power.py, 2026-10-26): shut down, "
              "restart, sign out, hibernate, sleep where Windows lets an app start it, lock, cancel a scheduled shutdown, and its power status. "
              "Each is one fixed call; what Windows answered is what Monday says, and these never go to an agent's terminal.",
              "capabilities": {"shutdown": "asks you every time, saying what it means; right away unless you say when",
                               "restart": "asks you every time, saying what it means; right away unless you say when",
                               "sign_out": "asks you every time", "hibernate": "asks you every time",
                               "sleep": "asks you every time; Modern Standby PCs can't be put to sleep by an app",
                               "lock": SAFE, "cancel_shutdown": SAFE, "status": SAFE}},
    "applications": {"label": "Applications", "description": "Find installed apps and open projects in an editor.",
                     "capabilities": {"open_project": SAFE, "list": SAFE, "find": SAFE}},
    "filesystem": {"label": "Files", "description": "List, read, create, rename, move and delete files and folders in your user "
                   "folder and projects. Deleting goes to the Recycle Bin; credential files are never read. Opening one goes "
                   "through Computer › open.",
                   "capabilities": {"list": SAFE, "read": "never credential or key files", "create": "never overwrites",
                                    "rename": ASK, "move": ASK, "delete": "asks you; to the Recycle Bin, never permanently",
                                    "write": ASK}},
    "terminal": {"label": "Terminal", "description": "Run commands, tests and dev servers in a project; git status; check ports.",
                 "capabilities": {"run": RULED, "tests": RULED, "dev_server": RULED, "git": SAFE, "port": SAFE}},
    "coding_agents": {"label": "Coding agents", "description": "Hand code work to Claude Code, Codex, Gemini CLI or Kilo Code.",
                      "capabilities": {"delegate": "the agent's own approvals apply"}},
    "computer_agent": {"label": "Computer agent", "description": "A model proposes the next click or keystroke; Monday checks and does it.",
                       "capabilities": {"goal": "every proposed step goes through the permission rules"}},
    "ai_clients": {"label": "AI tools", "description": "Which AI apps and CLIs are installed and whether they're logged in.",
                   "capabilities": {"list": SAFE, "check": SAFE}},
    "voice": {"label": "Voice", "description": "Monday's voice: who speaks, which female voice, speed, pitch, on or off, how it listens.",
              "capabilities": {"list": SAFE, "select": SAFE, "speed": SAFE, "pitch": SAFE, "mute": SAFE}},
    "memory": {"label": "Memory (Obsidian)", "description": "Remember decisions, preferences and project history in the vault.",
               "capabilities": {"remember": SAFE, "search": SAFE}},
    "graph": {"label": "Graphify", "description": "Project structure from the cached code graph.", "capabilities": {"query": SAFE, "update": SAFE}},
    "system": {"label": "System", "description": "Time, CPU/RAM/GPU, running tasks, component health.", "capabilities": {"status": SAFE, "time": SAFE}},
    "calendar": {"label": "Calendar", "description": "Calendar events, meetings and reminders: Google Calendar, through the Google tool.",
                 "capabilities": {"read": SAFE, "create": "on your own calendar, never invites anyone"}},
    "vision": {"label": "Eyes (camera)", "description": "This PC's camera through OpenCV (core/vision.py): say what the things in front of it "
               "are, or in a photo you send, and remember a thing you show it under your own name for it. Nothing it sees is kept, "
               "except one picture of what you ask it to remember.",
               "capabilities": {"look": SAFE, "remember": "keeps one picture of it, on this computer"}},
    "google": {"label": "Google", "description": "Your Google account through Google's own APIs and your consent (core/google.py): Docs, Drive "
               "(names and dates), Calendar, Tasks, Sheets; Gmail and Contacts read only, when you turn them on. Google Keep through "
               "keep.google.com in Monday's Chrome (a browser route: Keep has no API for personal accounts). Every write is read back; "
               "nothing is deleted, sent or shared.",
               "capabilities": {"note_create": "adds a note", "note_find": SAFE, "note_append": "adds to the end of a note",
                                "doc_create": "adds a document", "doc_find": SAFE, "doc_append": "adds to the end, keeps the rest", "drive_find": SAFE,
                                "calendar_agenda": SAFE, "calendar_free": SAFE, "calendar_create": "your own calendar only, no guests",
                                "tasks_list": SAFE, "task_create": "adds a task (never twice)", "sheet_create": "adds a sheet",
                                "mail_find": "read only, when you turn Gmail on", "contact_find": "read only, when you turn Contacts on"}},
    "office": {"label": "Documents (Office)", "description": "Word, PowerPoint and Excel files made on this PC (core/office.py): a model writes "
               "them, from the web's sources when online, and Office builds them in front of you with Monday's cursor on each slide; "
               "PDFs through Word. Saved as a new file in your Documents (never over one), counted, and left open.",
               "capabilities": {"create": "a new file in your Documents"}},
    "taiga_s1": {"label": "Taiga-S1 (FreeCAD)", "description": "A 1.2M-parameter System-1 model (shhivv/taiga-s1, MIT; core/taiga.py: 4.92 MB of "
                 "weights, one decision in ~17 ms on this CPU) that picks the next FreeCAD PartDesign command from FreeCAD's live state and a "
                 "feature goal. It drives FreeCAD only, through FreeCAD's own API; no chat, no screen, no other app. FreeCAD isn't installed "
                 "here, so it is detected, not supported.",
                 "capabilities": {"freecad_next_command": "proposes only; a FreeCAD step would go through the permission rules"}},
}
# Registered but not built: offered to nobody, listed as "not built" (Phase 8 builds the browser ones). The executor
# self-check fails if one of these starts reaching a handler unannounced.
NOT_BUILT = {("applications", "list"), ("applications", "find"), ("taiga_s1", "freecad_next_command")}
# The spec's other names for the same capabilities (so a lookup by any of them works).
ALIASES = {"keyboard": "computer", "mouse": "computer", "screenshot": "computer", "window_manager": "computer", "clipboard": "computer",
           "chrome": "browser", "web_search": "browser", "files": "filesystem", "obsidian": "memory", "ai": "ai_clients"}
# What Jev chooses between: one line each, literal, with the browser/computer boundary spelled out (measured
# 2026-09-28: "open Chrome and search for X" was read as `computer` when the browser line was only "open a website").
JEV_TOOLS = {
    "computer": "Operate desktop apps and windows on this PC: open, close or switch apps, click, type, scroll, press keys, "
                "read or capture the screen, change the volume, play, pause or skip music. Not for websites, and not the PC's power.",
    "power": "This PC's power: shut it down, turn it off, restart or reboot it, hibernate it, put it to sleep, lock it, sign out "
             "of Windows, or cancel a shutdown or restart that is scheduled.",
    "browser": "Anything on the web: open a website or web address, search the web (even when the request names the browser "
               "app, as in 'open Chrome and search for X'), or read, click or fill in something on a web page.",
    "terminal": "Run a command, the tests or a dev server in a software project, or check git or a port.",
    "coding_agents": "Write, fix, refactor, review or explain code in a project (a coding agent).",
    "filesystem": "Find, create, move, rename or delete files and folders.",
    "office": "Make a new document and write it for the owner: a Word document or doc, notes, a report, an essay, a PDF, a PowerPoint "
              "presentation or an Excel sheet on a subject, also when the request says to search the web or research the subject first. "
              "Not for opening, reading or finding a file that already exists, and not a question about how to use Office.",
    "memory": "Remember or recall a decision, preference or fact.",
    "vision": "Look through the camera at what the owner holds up or shows, or remember a thing by sight. Not the screen.",
    "voice": "Change Monday's voice, speed, whether it speaks, or how it listens.",
    "system": "Monday's own status, tasks, settings, models or projects, or this computer's status.",
    "calendar": "Calendar events, meetings or reminders.",
    "none": "No tool: a conversation or a question answered in words.",
}


def built(tool, capability):
    return (tool, capability) not in NOT_BUILT


def executor_pairs():
    """(tool, capability) pairs executor._act must run."""
    return {(t, c) for t in EXECUTOR_TOOLS for c in TOOLS[t]["capabilities"] if built(t, c)}


def resolve(name):
    """A tool by its name or one of the spec's other names ("chrome", "obsidian"), else None."""
    n = (name or "").lower()
    return n if n in TOOLS else ALIASES.get(n)


def listing(status=None):
    """The registry as served by /api/tools. status: {tool: {"status", "detail"}} from Monday.tool_status; a tool with
    no live check is N/A (status None), never assumed available."""
    out = []
    for key, t in TOOLS.items():
        st = (status or {}).get(key) or {}
        caps = [{"name": c, "risk": r, "built": built(key, c)} for c, r in t["capabilities"].items()]
        out.append({"name": key, "label": t["label"], "description": t["description"], "status": st.get("status"),
                    "detail": st.get("detail"), "available": st.get("status") == AVAILABLE,
                    "built": sum(c["built"] for c in caps), "capabilities": caps})
    return out


if __name__ == "__main__":  # python -m core.tools
    from . import router
    for tool, t in TOOLS.items():
        assert t["label"] and t["description"], tool
    # every action the fast path can produce is registered here, and built
    import re
    produced = set(re.findall(r'step\("(\w+)", "(\w+)"', open(router.__file__, encoding="utf-8").read()))
    missing = [f"{a}.{b}" for a, b in produced if a not in ("analysis",) and b not in TOOLS.get(a, {}).get("capabilities", {})]
    assert not missing, missing
    unbuilt = [f"{a}.{b}" for a, b in produced if a != "analysis" and not built(a, b)]
    assert not unbuilt, f"the fast path produces steps nothing runs: {unbuilt}"
    assert set(JEV_TOOLS) - {"none"} <= set(TOOLS)
    assert all(t in TOOLS and c in TOOLS[t]["capabilities"] for t, c in NOT_BUILT), "NOT_BUILT names only registered capabilities"
    rows = {r["name"]: r for r in listing({"computer": {"status": AVAILABLE, "detail": "x"}})}
    assert rows["computer"]["available"] and rows["browser"]["status"] is None and not rows["browser"]["available"], \
        "no live check means N/A, never available"
    caps = {c["name"]: c["built"] for c in rows["browser"]["capabilities"]}
    assert all(c["built"] for c in rows["filesystem"]["capabilities"]) and caps["read_page"] and caps["download"], caps
    assert resolve("Chrome") == "browser" and resolve("obsidian") == "memory" and resolve("nope") is None
    print("tools self-check: OK,", len(TOOLS), "tools,", sum(len(t["capabilities"]) for t in TOOLS.values()), "capabilities,",
          len(executor_pairs()), "run by the executor,", len(NOT_BUILT), "not built yet")
