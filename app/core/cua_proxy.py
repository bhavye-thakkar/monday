"""MCP stdio proxy between Hermes and cua-driver: only the desktop tools Monday hands an AI.

Hermes starts this through ACP `mcpServers` (see workers.Hermes). It runs `cua-driver mcp --direct` and forwards
JSON-RPC lines both ways, keeping only ALLOWED tools in `tools/list` and refusing calls to anything else.
An allow-list, not a deny-list: tools a future driver adds (installs, recordings, clipboard…) stay out until
someone adds them here. It also keeps the tool surface small, which a text-only model needs: with all 57 tools
Hermes' model searched them, then fell back to its slow browser tool (measured 2026-09-24).
Run directly for a self-check: python -m core.cua_proxy --check
"""
import json
import os
import subprocess
import sys
import threading
import time

# Read the screen as text (UI Automation) and act on apps. Not allowed: kill_app, clipboard, recordings, installs,
# driver config, file transfer in browsers, screenshots (the model can't see pixels) and session/debug plumbing.
ALLOWED = {
    "list_apps", "list_windows", "get_window_state", "get_accessibility_tree", "verify_state", "get_screen_size",
    "launch_app", "bring_to_front", "invoke_menu",
    "click", "double_click", "right_click", "scroll", "type_text", "press_key", "hotkey", "set_value",
}


ACTS = {"click", "double_click", "right_click", "scroll", "type_text", "press_key", "hotkey", "set_value", "invoke_menu", "bring_to_front"}
MINE = ("Monday's own window, notch and pet are the owner's: an agent may not act on them (only the owner answers Monday's approvals). "
        "Work on the other apps.")
MINE_TTL_S = 5.0  # Monday's own processes looked up again at most this often (its window restarts with a new pid)
_mine = (0.0, frozenset())
# Monday's cursor (the owner, 2026-10-24: "it should be using its cursor"): cua-driver acts in the background (UIA Invoke, posted keys) and
# draws nothing for it, so an agent's work was invisible. The driver can draw an agent cursor of its own over the screen (a session's
# overlay, move_cursor; the owner's real mouse never moves): before each act on an element a snapshot placed, it glides there first.
CURSOR = "monday"          # the driver session that owns the cursor
CURSOR_GLIDE_S = 0.3       # it reaches the element before the act
CURSOR_WAIT_S = 5.0        # one cursor call's answer, at most
FRAMES_MAX = 2000          # element centres kept from the latest snapshots


def frames_of(result, frames):
    """A get_window_state result's elements: each one's centre on the screen by its token (the driver's frames are screen pixels)."""
    for e in ((result or {}).get("structuredContent") or {}).get("elements") or []:
        f = e.get("frame") if isinstance(e, dict) else None
        if f and e.get("element_token"):
            frames[e["element_token"]] = (f["x"] + f["w"] / 2, f["y"] + f["h"] / 2)
    while len(frames) > FRAMES_MAX:
        frames.pop(next(iter(frames)))


def cursor_point(name, args, frames):
    """Where an acting call lands, when a snapshot placed its element (a token is "<snapshot_id>:<element_index>"), else None."""
    if name not in ACTS or not isinstance(args, dict):
        return None
    token = args.get("element_token") or (f"{args['snapshot_id']}:{args['element_index']}"
                                          if args.get("snapshot_id") and args.get("element_index") is not None else None)
    return frames.get(token)


def cursor_calls(point, started):
    """The driver calls that show Monday's cursor at `point`: its session and cursor once, then the move (the overlay only)."""
    first = [("start_session", {"session": CURSOR}), ("set_agent_cursor_enabled", {"session": CURSOR, "enabled": True})] if not started else []
    return first + [("move_cursor", {"session": CURSOR, "x": round(point[0]), "y": round(point[1]), "scope": "window"})]


def mondays_pids():
    """The desktop app's processes (`-m core.desktop`: its window, notch, pet, marks), looked up at most every MINE_TTL_S."""
    global _mine
    if time.monotonic() - _mine[0] > MINE_TTL_S:
        import psutil
        pids = set()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if "core.desktop" in " ".join(p.info["cmdline"] or ()):
                    pids.add(p.info["pid"])
            except (psutil.Error, TypeError):
                continue
        _mine = (time.monotonic(), frozenset(pids))
    return _mine[1]


def target_pid(args):
    """The process an acting call reaches: its pid, else its window's (window_id), else the window under x, y, else the window with the
    keyboard (keys and typing go there). None when the OS doesn't say."""
    if isinstance(args.get("pid"), int):
        return args["pid"]
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    if isinstance(args.get("window_id"), int):
        hwnd = args["window_id"]
    elif isinstance(args.get("x"), (int, float)) and isinstance(args.get("y"), (int, float)):
        hwnd = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(int(args["x"]), int(args["y"]))), 2)  # GA_ROOT
    else:
        hwnd = u.GetForegroundWindow()
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def filter_line(line, list_ids):
    """cua-driver -> Hermes: strip denied tools from a tools/list result. Returns the (possibly rewritten) line."""
    try:
        msg = json.loads(line)
    except ValueError:
        return line
    if isinstance(msg, dict) and msg.get("id") in list_ids and isinstance(msg.get("result"), dict):
        list_ids.discard(msg["id"])
        msg["result"]["tools"] = [t for t in msg["result"].get("tools", []) if t.get("name") in ALLOWED]
        return (json.dumps(msg) + "\n").encode()
    return line


def refusal(line, list_ids):
    """Hermes -> cua-driver: the reply for a denied call (None = forward the line). Remembers tools/list ids."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    if not isinstance(msg, dict):
        return None
    if msg.get("method") == "tools/list":
        list_ids.add(msg.get("id"))
    name = (msg.get("params") or {}).get("name") if msg.get("method") == "tools/call" else None
    if name is not None and name not in ALLOWED:
        return (json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {
            "isError": True, "content": [{"type": "text", "text": f"Monday does not allow {name}."}]}}) + "\n").encode()
    if name in ACTS:  # never on Monday's own window: an agent clicking its Approve / Always allow would approve itself (2026-10-20)
        args = (msg.get("params") or {}).get("arguments")
        try:
            mine = target_pid(args if isinstance(args, dict) else {}) in mondays_pids()
        except Exception:  # (the guard can't tell where it lands: refused, not let through)
            mine = True
        if mine:
            return (json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {
                "isError": True, "content": [{"type": "text", "text": MINE}]}}) + "\n").encode()
    return None


def main():
    child = subprocess.Popen([os.environ["MONDAY_CUA_DRIVER"], "mcp", "--direct"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0)
    list_ids, lock, frames, started, answered = set(), threading.Lock(), {}, [False], {}

    def out(data):
        with lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def to_child():
        n = 0
        for line in sys.stdin.buffer:
            reply = refusal(line, list_ids)
            if reply:
                out(reply)
                continue
            try:
                p = json.loads(line).get("params") or {} if b'"tools/call"' in line else {}
            except (ValueError, AttributeError):
                p = {}
            point = cursor_point(p.get("name"), p.get("arguments"), frames)
            if point:  # Monday's cursor goes to the element first (its replies carry ids of Monday's own, never shown to Hermes), each
                # call answered before the next, as Monday's own hands do: sent at once, the move came before the cursor was on, unseen
                for name, args in cursor_calls(point, started[0]):
                    n += 1
                    rid = f"monday-cursor-{n}"
                    answered[rid] = threading.Event()
                    child.stdin.write((json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                                                   "params": {"name": name, "arguments": args}}) + "\n").encode())
                    child.stdin.flush()
                    answered[rid].wait(CURSOR_WAIT_S)
                    answered.pop(rid, None)
                started[0] = True
                time.sleep(CURSOR_GLIDE_S)
            child.stdin.write(line)
            child.stdin.flush()
        child.stdin.close()  # Hermes is done: cua-driver exits when its stdin closes

    threading.Thread(target=to_child, daemon=True).start()
    for line in child.stdout:
        if b'"monday-cursor-' in line and len(line) < 20000:
            try:
                rid = json.loads(line).get("id")
                if str(rid).startswith("monday-cursor-"):
                    if rid in answered:
                        answered[rid].set()
                    continue
            except (ValueError, AttributeError):
                pass
        if b'"elements"' in line:
            try:
                frames_of(json.loads(line).get("result"), frames)
            except (ValueError, AttributeError, KeyError, TypeError):
                pass
        out(filter_line(line, list_ids))
    sys.exit(child.wait())


if __name__ == "__main__":
    if "--check" in sys.argv:
        ids = set()
        assert refusal(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n', ids) is None and ids == {1}
        got = json.loads(filter_line(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "click"}, {"name": "kill_app"}, {"name": "clipboard_read"}]}}).encode(), ids))
        assert [t["name"] for t in got["result"]["tools"]] == ["click"] and not ids
        r = json.loads(refusal(b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"kill_app","arguments":{}}}\n', ids))
        assert r["id"] == 7 and r["result"]["isError"]
        _mine = (float("inf"), frozenset({4242}))  # Monday's window stood in for (pid 4242)
        call = '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"%s","arguments":%s}}\n'
        r = json.loads(refusal((call % ("click", '{"pid":4242,"element_token":"t"}')).encode(), ids))
        assert r["result"]["isError"] and "Monday's own window" in r["result"]["content"][0]["text"], "no clicking Monday's own Approve"
        assert refusal((call % ("click", '{"pid":31,"element_token":"t"}')).encode(), ids) is None, "other apps: as before"
        assert refusal((call % ("get_window_state", '{"pid":4242}')).encode(), ids) is None, "reading it is fine; acting isn't"
        real = target_pid
        target_pid = lambda a: 4242  # noqa: E731  (the keyboard is in Monday's window)
        assert refusal((call % ("type_text", '{"text":"yes"}')).encode(), ids), "keys to Monday's window refused too"
        target_pid = lambda a: 1 / 0  # noqa: E731
        assert refusal((call % ("press_key", '{"key":"enter"}')).encode(), ids), "can't tell where it lands: refused"
        target_pid = real
        # Monday's cursor: a snapshot's elements placed (screen centres by token), an act on one of them glides there first
        fr = {}
        frames_of({"structuredContent": {"snapshot_id": "s00000002", "elements": [
            {"element_index": 32, "element_token": "s00000002:32", "frame": {"x": 917, "y": 539, "w": 96, "h": 63}, "label": "Seven"},
            {"element_index": 33, "element_token": "s00000002:33", "label": "no frame"}]}}, fr)
        assert fr == {"s00000002:32": (965.0, 570.5)}, fr
        assert cursor_point("click", {"pid": 1, "element_token": "s00000002:32"}, fr) == (965.0, 570.5)
        assert cursor_point("set_value", {"snapshot_id": "s00000002", "element_index": 32}, fr) == (965.0, 570.5)
        assert cursor_point("click", {"pid": 1, "x": 5, "y": 5}, fr) is None and cursor_point("get_window_state", {"element_token": "s00000002:32"}, fr) is None
        assert [n for n, _ in cursor_calls((965.0, 570.5), False)] == ["start_session", "set_agent_cursor_enabled", "move_cursor"]
        assert cursor_calls((965.0, 570.5), True) == [("move_cursor", {"session": CURSOR, "x": 965, "y": 570, "scope": "window"})], \
            "the overlay only: scope desktop would move the owner's own mouse"
        print("cua_proxy self-check: OK,", len(ALLOWED), "tools allowed, none of them on Monday's own window; its cursor shows where it acts")
    else:
        main()
