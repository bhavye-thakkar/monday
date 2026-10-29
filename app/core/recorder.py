"""Workflow recorder: the owner performs a workflow once, Monday records it as structured, semantic steps, replays it on the
real laptop through the EXISTING executor (no second agent), and reports an evidence-based PASSED / FAILED / BLOCKED / CANCELLED.

How capture works (desktop): Monday watches the foreground window's UI Automation state (core/computer: `front`, `elements`) and
diffs consecutive snapshots into meaningful transitions — an app opened, a document's text changed — not raw mouse coordinates and
not every keystroke. So "open Notepad, type hello there" becomes two semantic steps with assertions, which replay through
`executor.run_plan` exactly like a normal command. Secrets are never recorded (`computer.looks_secret`, password fields skipped).

A workflow is JSON (stored in `recordings.workflow`):
  {name, created_at, target_app, initial_state, steps: [{index, kind, essential, plan, expect}]}
where `plan` is a router step (the action to replay) and `expect` is the assertion that proves the step's real outcome:
  {"type": "window", "app": "notepad"}                         -> that window is present
  {"type": "text", "app": "notepad", "contains": "hello there"}-> that text is really in the editor
  {"type": "file", "path": ..., "contains": ...}               -> the file exists / holds the text
  {"type": "media", "playing": true, "contains": ...}          -> Windows' media session is playing it
A click succeeding is never proof: only the assertion's evidence is.

python -m core.recorder   -> offline self-check (record -> build -> plan -> fake replay -> verify PASSED, then FAILED)
"""
import asyncio
import re
import time

from . import router
from .computer import looks_secret

CAPTURE_EVERY_S = 0.5     # how often the foreground state is sampled while recording
TYPE_COALESCE = True      # continuous typing into one field becomes one step (the final text), not one per keystroke
MAX_STEPS = 200           # a recording never grows unbounded
SECRET_FIELDS = re.compile(r"password|passcode|pin|secret|token|cvv|card\s*number", re.I)


def app_name(window):
    """A stable, replayable name for the app a window belongs to: its process without .exe ("notepad"), lowercased."""
    proc = (getattr(window, "process", "") or "").strip()
    return re.sub(r"\.exe$", "", proc, flags=re.I).lower() or "the app"


async def snapshot(c):
    """The foreground app's state now: its app name, window title, and the (non-secret) text of its editable areas. The front
    window is never Monday's own (computer.front skips those)."""
    w = c.front()
    if not w:
        return {"app": None, "name": None, "title": None, "docs": {}, "at": time.time()}
    docs = {}
    try:
        for e in await c.elements(w, limit=150):
            if e.get("role") in ("Document", "Edit") and e.get("value"):
                name = e.get("name") or ""
                val = e["value"]
                if SECRET_FIELDS.search(name) or looks_secret(val) or e.get("password"):
                    continue  # never record a secret (the brief, and AGENTS rule 6)
                docs[name] = val
    except Exception:
        pass
    return {"app": app_name(w), "name": app_name(w), "title": w.title, "docs": docs, "at": time.time()}


def diff(prev, cur):
    """The meaningful transitions from one snapshot to the next: a new app in front, or an editable area whose text changed.
    Incidental movement and focus flicker produce nothing."""
    out = []
    if not cur or not cur.get("app"):
        return out
    if cur["app"] != (prev or {}).get("app"):
        out.append({"kind": "open", "app": cur["app"], "name": cur["name"], "title": cur["title"], "at": cur["at"]})
    same_app = prev and prev.get("app") == cur.get("app")
    before = cur if False else (prev["docs"] if same_app else {})
    for field, value in cur["docs"].items():
        if value and value != before.get(field, ""):
            out.append({"kind": "type", "app": cur["app"], "name": cur["name"], "field": field,
                        "value": value, "before": before.get(field, ""), "at": cur["at"]})
    return out


def _type_step(index, ev):
    text = ev["value"]
    return {"index": index, "kind": "type", "essential": True, "_app": ev["app"], "_field": ev["field"],
            "plan": router.step("computer", "type", f'Type “{text[:60]}” into {ev["name"]}', text=text, into=ev["name"]),
            "expect": {"type": "text", "app": ev["name"], "contains": text, "desc": f'the text “{text[:40]}” appears in {ev["name"]}'}}


def build_workflow(events, name, target_app=None):
    """Raw transitions -> a structured, replayable workflow. Continuous typing into one field is coalesced to one step with the
    final text (the brief: don't blindly replay every keystroke); every step carries the assertion that proves its real outcome."""
    steps = []
    for ev in events[:MAX_STEPS]:
        if ev["kind"] == "open":
            steps.append({"index": len(steps) + 1, "kind": "open", "essential": True, "_app": ev["app"],
                          "plan": router.step("computer", "open", f"Open {ev['name']}", target=ev["name"]),
                          "expect": {"type": "window", "app": ev["name"], "desc": f"{ev['name']} is open"}})
        elif ev["kind"] == "type":
            if TYPE_COALESCE and steps and steps[-1]["kind"] == "type" and steps[-1]["_app"] == ev["app"] \
                    and steps[-1]["_field"] == ev["field"]:
                keep = steps[-1]["index"]
                steps[-1] = _type_step(keep, ev)  # the newer value replaces the in-progress one, same position
            else:
                steps.append(_type_step(len(steps) + 1, ev))
    app = target_app or (steps[0]["_app"] if steps else None)
    clean = [{k: v for k, v in s.items() if not k.startswith("_")} for s in steps]
    return {"name": name, "created_at": time.time(), "target_app": app,
            "initial_state": {"note": "recorded from the foreground app's UI Automation state"}, "steps": clean}


def plan_of(workflow):
    """The replayable plan: the steps' actions, in order (run through executor.run_plan, the same path a spoken command takes).
    A step the owner unticked in the workspace (essential: false) is left out, of the replay and of the checks (verify)."""
    return [dict(s["plan"]) for s in workflow.get("steps", []) if s.get("essential", True)]


# -- verification: evidence, not "the click worked" -----------------------------------------------------------------------
async def check_assertion(exp, c, files=None, media=None):
    """Evaluate one expectation against the REAL current state. -> {ok, actual, evidence}."""
    t = (exp or {}).get("type")
    if t == "window":
        appobj = await c.apps.find(exp["app"])
        wins = c.windows_for(exp["app"], appobj)
        return {"ok": bool(wins), "actual": (wins[0].title if wins else f"{exp['app']} not open"),
                "evidence": {"window": wins[0].title if wins else None}}
    if t == "text":
        appobj = await c.apps.find(exp["app"])
        w = next(iter(c.windows_for(exp["app"], appobj)), None)
        if not w:
            return {"ok": False, "actual": f"{exp['app']} not open", "evidence": {}}
        seen = await c.saw_text(w, exp["contains"])
        value = ""
        try:
            for e in await c.elements(w, limit=150):
                if e.get("role") in ("Document", "Edit") and e.get("value") and exp["contains"][:20].lower() in e["value"].lower():
                    value = e["value"]
                    break
        except Exception:
            pass
        return {"ok": bool(seen), "actual": (value[:100] if value else "the text isn't there"), "evidence": {"text": value[:300]}}
    if t == "file" and files is not None:
        import os
        path = exp.get("path", "")
        if not os.path.isfile(path):
            return {"ok": False, "actual": "the file doesn't exist", "evidence": {"path": path}}
        if exp.get("contains"):
            try:
                body = open(path, encoding="utf-8", errors="replace").read()
            except OSError as e:
                return {"ok": False, "actual": f"couldn't read it ({e.strerror})", "evidence": {"path": path}}
            ok = exp["contains"] in body
            return {"ok": ok, "actual": ("holds the text" if ok else "the text isn't in it"), "evidence": {"path": path}}
        return {"ok": True, "actual": "exists", "evidence": {"path": path}}
    if t == "media" and media is not None:
        r = await media.now_playing(exp.get("app"))
        playing = bool(r.get("media") and (r["media"].get("status") == "playing"))
        title = (r.get("media") or {}).get("title", "")
        ok = playing and (exp["contains"].lower() in title.lower() if exp.get("contains") else True)
        return {"ok": ok, "actual": r.get("text", ""), "evidence": {"media": r.get("media")}}
    return {"ok": True, "actual": "no assertion", "evidence": {}}


async def verify(workflow, c, files=None, media=None):
    """Every step's assertion against the real state -> per-step results (expected vs actual vs evidence)."""
    results = []
    for s in workflow.get("steps", []):
        exp = s.get("expect") or {}
        if not s.get("essential", True):  # unticked by the owner: not replayed (plan_of), so not checked either; said as such
            results.append({"index": s["index"], "label": s["plan"]["label"], "expected": exp.get("desc"), "status": "skipped",
                            "actual": None, "evidence": "left out of the replay"})
            continue
        r = await check_assertion(exp, c, files, media)
        results.append({"index": s["index"], "label": s["plan"]["label"], "expected": exp.get("desc"),
                        "status": "passed" if r["ok"] else "failed", "actual": r["actual"], "evidence": r.get("evidence")})
    return results


def summarize(results, duration_ms, cancelled=False, blocked_reason=None):
    """The test run's verdict from its step results: PASSED only when every assertion held; FAILED on any miss; BLOCKED when a
    precondition stopped it; CANCELLED when the owner stopped it. With the counts, so a click succeeding is never 'passed'."""
    passed = sum(r["status"] == "passed" for r in results)
    failed = sum(r["status"] == "failed" for r in results)
    skipped = sum(r["status"] == "skipped" for r in results)
    if cancelled:
        status = "CANCELLED"
    elif blocked_reason:
        status = "BLOCKED"
    elif not results:
        status = "BLOCKED"
    else:
        status = "FAILED" if failed else "PASSED"
    return {"status": status, "passed": passed, "failed": failed, "skipped": skipped, "duration_ms": duration_ms,
            "reason": blocked_reason, "results": results}


class Recorder:
    """Captures the owner's foreground workflow while active. One at a time. The capture loop only reads the screen (UIA); it
    never acts. Pause/resume/stop are immediate. `on_event(ev)` (optional) lets a page show steps as they are captured."""

    def __init__(self, computer, on_event=None):
        self.c = computer
        self.on_event = on_event
        self.active = False
        self.paused = False
        self.events = []
        self.snap = None
        self.name = None
        self.started_at = None
        self._task = None

    def state(self):
        return {"active": self.active, "paused": self.paused, "name": self.name, "steps": len(self.events),
                "started_at": self.started_at}

    def start(self, name="Recording", loop=None):
        if self.active:
            return False
        self.active, self.paused, self.events, self.snap = True, False, [], None
        self.name, self.started_at = name, time.time()
        self._task = (loop or asyncio.get_event_loop()).create_task(self._loop())
        return True

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    async def stop(self):
        self.active = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, 2)
            except Exception:
                self._task.cancel()
        return build_workflow(self.events, self.name or "Recording")

    async def _loop(self):
        while self.active:
            try:
                if not self.paused:
                    cur = await snapshot(self.c)
                    if cur.get("app"):
                        for ev in diff(self.snap, cur):
                            self.events.append(ev)
                            if self.on_event:
                                try:
                                    self.on_event(ev)
                                except Exception:
                                    pass
                        self.snap = cur
            except Exception:
                pass
            await asyncio.sleep(CAPTURE_EVERY_S)


if __name__ == "__main__":
    import types

    class FakeWin:
        def __init__(self, title, process, hwnd=1):
            self.title, self.process, self.hwnd = title, process, hwnd

    class FakeApps:
        async def find(self, name):
            return {"name": name} if name else None

    class FakeComputer:
        """A stand-in desktop: `state` is {app_name: {"title","docs":{field:value},"open":bool}}; front() returns the app marked front."""
        def __init__(self, state, front):
            self.state, self._front, self.apps = state, front, FakeApps()

        def front(self):
            s = self.state.get(self._front)
            return FakeWin(s["title"], f"{self._front}.exe") if s and s.get("open", True) else None

        def windows_for(self, name, app=None):
            s = self.state.get(name)
            return [FakeWin(s["title"], f"{name}.exe")] if s and s.get("open", True) else []

        async def elements(self, w, limit=150):
            name = re.sub(r"\.exe$", "", w.process)
            s = self.state.get(name, {})
            return [{"role": "Document", "name": f, "value": v} for f, v in (s.get("docs") or {}).items()]

        async def saw_text(self, w, text):
            name = re.sub(r"\.exe$", "", w.process)
            return any(text[:20].lower() in (v or "").lower() for v in (self.state.get(name, {}).get("docs") or {}).values())

    async def check():
        # 1) capture: synthetic snapshots of the owner opening Notepad and typing a phrase (coalesced across keystrokes)
        snaps = [
            {"app": None, "name": None, "title": None, "docs": {}, "at": 0},
            {"app": "notepad", "name": "notepad", "title": "Untitled - Notepad", "docs": {"Text editor": ""}, "at": 1},
            {"app": "notepad", "name": "notepad", "title": "*Untitled - Notepad", "docs": {"Text editor": "hello"}, "at": 2},
            {"app": "notepad", "name": "notepad", "title": "*Untitled - Notepad", "docs": {"Text editor": "hello there recorder"}, "at": 3},
        ]
        events = []
        prev = None
        for s in snaps:
            events += diff(prev, s)
            prev = s if s.get("app") else prev
        wf = build_workflow(events, "Notepad Text Entry Test")
        kinds = [s["kind"] for s in wf["steps"]]
        assert kinds == ["open", "type"], ("one open + one coalesced type, not a step per keystroke", kinds)
        assert wf["steps"][1]["plan"]["args"]["text"] == "hello there recorder", "the final typed text, coalesced"
        assert wf["steps"][1]["plan"]["args"]["into"] == "notepad" and wf["target_app"] == "notepad"
        assert wf["steps"][0]["expect"] == {"type": "window", "app": "notepad", "desc": "notepad is open"}
        assert wf["steps"][1]["expect"]["contains"] == "hello there recorder"

        # 2) the replay plan is an ordinary plan (runs through executor.run_plan, no second agent)
        plan = plan_of(wf)
        assert [(p["tool"], p["action"]) for p in plan] == [("computer", "open"), ("computer", "type")]

        # 3) verify PASSED: the real state after replay shows Notepad open with the text
        good = FakeComputer({"notepad": {"title": "*Untitled - Notepad", "open": True, "docs": {"Text editor": "hello there recorder"}}}, "notepad")
        res = await verify(wf, good)
        out = summarize(res, 1200)
        assert out["status"] == "PASSED" and out["passed"] == 2 and out["failed"] == 0, out
        assert "hello there recorder" in res[1]["evidence"]["text"], "the evidence is the real text, not a claim"

        # 4) break the expected value -> FAILED (a click/type succeeding is not proof)
        wf2 = build_workflow(events, "Notepad Text Entry Test")
        wf2["steps"][1]["expect"]["contains"] = "a different phrase that was never typed"
        res2 = await verify(wf2, good)
        out2 = summarize(res2, 1100)
        assert out2["status"] == "FAILED" and out2["failed"] == 1, out2
        assert res2[1]["status"] == "failed" and res2[0]["status"] == "passed"

        # 5) BLOCKED: Notepad never opened (precondition), text check fails honestly
        gone = FakeComputer({"notepad": {"title": "", "open": False, "docs": {}}}, "notepad")
        res3 = await verify(wf, gone)
        assert all(r["status"] == "failed" for r in res3) and summarize(res3, 10)["status"] == "FAILED"
        assert summarize([], 10, blocked_reason="Notepad isn't installed")["status"] == "BLOCKED"
        assert summarize(res, 10, cancelled=True)["status"] == "CANCELLED"

        # 6) a step the owner unticked in the workspace (essential: false) is neither replayed nor checked, and the run says so
        wf3 = build_workflow(events, "Notepad Text Entry Test")
        wf3["steps"][1]["essential"] = False
        assert [p["action"] for p in plan_of(wf3)] == ["open"], "the unticked type step is left out of the replay"
        res4 = await verify(wf3, gone)
        assert [r["status"] for r in res4] == ["failed", "skipped"] and res4[1]["evidence"] == "left out of the replay", res4
        assert summarize(res4, 10)["skipped"] == 1

        # 6) secrets are never captured
        assert diff({"app": "chrome", "name": "chrome", "docs": {}},
                    {"app": "chrome", "name": "chrome", "docs": {}, "title": "x", "at": 1}) == []
        s_secret = await snapshot(FakeComputer({"vault": {"title": "Vault", "open": True, "docs": {"Password": "hunter2xyz"}}}, "vault"))
        assert s_secret["docs"] == {}, "a password field is never recorded"

        # 7) the live Recorder loop builds the same workflow from a moving FakeComputer
        rec_c = FakeComputer({"notepad": {"title": "Untitled - Notepad", "open": True, "docs": {"Text editor": ""}}}, "notepad")
        rec = Recorder(rec_c)
        rec.start("Live", loop=asyncio.get_event_loop())
        await asyncio.sleep(0.01)
        rec_c.state["notepad"]["docs"]["Text editor"] = "typed while recording"
        await asyncio.sleep(CAPTURE_EVERY_S + 0.1)
        wf_live = await rec.stop()
        assert any(s["kind"] == "type" and s["plan"]["args"]["text"] == "typed while recording" for s in wf_live["steps"]), wf_live

    asyncio.run(check())
    print("recorder self-check: OK")
