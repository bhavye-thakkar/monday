"""Goals (2026-10-15, the owner: "the functionality of Meta Muse and Grok Bot, a personalized AI agent"). Muse: "once a person shares a goal,
it helps them develop a personalized plan … then advances the work on its own … and comes back when something changes or when it needs
approval"; Grok Bot: agents that keep working. In Monday's terms, built from what is here:

- a goal is a group of the owner's to-dos (core/side.py), one per step of its plan, in order. A chat model writes the plan; Jev reads each
  step as it reads any + task (side.judge: who would do it, does it involve money);
- started, Monday does its own steps one after another, each as an ordinary command: the router, Jev, router.permit and every approval,
  exactly as if the owner had typed it. Its result is watched on the bus: a step is done when its task completed or its answer came, and
  only then. A step that is the owner's (Jev's read, or "You: …" in the plan), one Jev isn't sure of, or one that involves money waits
  for the owner; a step that fails or asks something pauses the goal and says why;
- it goes on while the window is closed (Core runs in the tray) and checks in: a step done or failed, the next step the owner's, and once
  a day when nothing moved. A Core restart pauses an active goal (a step half-done is never run twice unasked).
- "my goal is …", "start / continue my goal", "pause my goal", "step done", "skip the step", "how is my goal", "stop my goal" (its to-dos
  stay), "forget my goal" (they go).

    python -m core.goals      the plan parsing, the step states and the outcome rules (no model, no Core)
"""
import asyncio
import json
import re
import time

from . import side

STEPS_MAX = 8
STEP_TIMEOUT_S = 1800.0   # a step with no result in this long is reported stalled and the goal pauses
CHECKIN_S = 86400.0       # an open goal where nothing moved in this long gets one check-in
TICK_S = 600.0            # how often the check-ins are looked for
OPEN = ("planned", "active", "waiting", "paused")
PLAN_SYSTEM = ("You turn a person's goal into a short plan. Write 3 to 7 steps, in order, as a JSON array of strings and nothing else. "
               "Each step is one concrete instruction a personal assistant on their Windows computer can carry out now (search the web and "
               "summarize, write a document, make a list, add a calendar event, open an app) or something only the person can do, written "
               "as 'You: …' (go for a run, call someone, buy something). Never a step that pays, buys or sends a message unless it starts "
               "with 'You:'.")
MARK = {"done": "✓", "running": "●", "next": "○", "waiting": "◐", "todo": "○"}


def plan_steps(text):
    """A model's plan -> its steps: a JSON array of strings, else its numbered or bulleted lines; [] when it isn't a plan."""
    t = (text or "").strip()
    m = re.search(r"\[.*\]", t, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            steps = [" ".join(s.split()) for s in arr if isinstance(s, str) and s.strip()]
            if steps:
                return [s[:side.TITLE_MAX] for s in steps][:STEPS_MAX]
        except ValueError:
            pass
    lines = [re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", ln).strip() for ln in t.splitlines() if re.match(r"^\s*(?:\d+[.)]|[-*•])\s+\S", ln)]
    return [ln[:side.TITLE_MAX] for ln in lines if ln][:STEPS_MAX]


def mine(step, jev):
    """May Monday do this step itself? Only when Jev read it as Monday's, it involves no money, and the plan didn't give it to the owner."""
    j = jev or {}
    return j.get("who") == "monday" and not j.get("money") and not re.match(r"^\s*you\s*:", step or "", re.I)


def outcome(ev, mid, st):
    """One bus event against a step's command (message id `mid`): ("done" | "failed" | "asked" | None, words). st: {"tasks": the tasks it
    started, "intent": how it was routed}, filled as the events come. A step routed to be done (a task, a tool plan) is done only when its
    task ends well: words alone back mean Monday is asking something first (2026-10-15: "Which project should Codex work in?" was taken
    for a finished step)."""
    k, d = ev.get("type"), ev.get("data") or {}
    tasks = st.setdefault("tasks", set())
    if k == "ROUTED" and (d.get("route") or {}).get("message_id") == mid:
        st["intent"] = d["route"].get("intent")
        return None, None
    if k == "MESSAGE_CREATED":
        m = d.get("message") or {}
        if m.get("role") != "monday" or (m.get("meta") or {}).get("reply_to") != mid:
            return None, None
        if m.get("kind") == "task" and m.get("task_id"):
            tasks.add(m["task_id"])
            return None, None
        if m.get("kind") == "permission":
            return None, None  # it waits for the owner's Approve / Deny; the task's own end decides
        if m.get("kind") == "error":
            return "failed", m.get("text") or "it failed"
        if (m.get("meta") or {}).get("question"):
            return "asked", m.get("text") or "a question"
        if m.get("kind") in ("text", "completion") and not tasks:
            return ("asked", m.get("text") or "") if st.get("intent") in ("TASK", "TOOL") else ("done", m.get("text") or "")
        return None, None
    if k in ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED"):
        t = d.get("task") or {}
        if t.get("id") in tasks or (t.get("route") or {}).get("message_id") == mid:
            if k == "TASK_COMPLETED":
                return "done", t.get("result") or ""
            return "failed", t.get("error") or ("stopped" if k == "TASK_CANCELLED" else "it failed")
    return None, None


def goal_title(text):
    """The goal's name (its group's): the words, capitalized, cut at a word with "…" when longer than a group name may be."""
    t = re.sub(r"^(?:to\s+)", "", " ".join((text or "").split()), flags=re.I).rstrip(".!")
    if len(t) > side.GROUP_MAX:
        t = t[:side.GROUP_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t[:1].upper() + t[1:] if t else "A goal"


class Goals:
    """The goals of one Monday Core (m: core.monday.Monday): new, start, pause, step done, skip, stop, forget, check-ins."""

    def __init__(self, m):
        self.m, self.db = m, m.db
        self.running = {}  # goal id -> its advancing asyncio task
        for g in self.db.all("SELECT id FROM goals WHERE status='active'"):  # a restart: nothing half-done is run again unasked
            self.db.update("goals", "id", g["id"], status="paused", note="Monday restarted while it worked on this goal.")

    # -- reading -------------------------------------------------------------------------------------------------------------------
    def latest(self):
        return self.db.one(f"SELECT * FROM goals WHERE status IN ({','.join('?' * len(OPEN))}) ORDER BY id DESC LIMIT 1", *OPEN)

    def steps(self, g):
        rows = self.db.all("SELECT t.* FROM todos t JOIN side s ON s.kind='todo' AND s.ref=CAST(t.id AS TEXT) WHERE s.group_id=? ORDER BY t.id",
                           g["group_id"])
        nxt = next((r["id"] for r in rows if not r.get("done_at")), None)
        out = []
        for r in rows:
            state = "done" if r.get("done_at") else "running" if r["id"] == g.get("step_id") and g["status"] == "active" else \
                "next" if r["id"] == nxt else "todo"
            out.append({"id": r["id"], "title": r["title"], "state": state, "mine": mine(r["title"], r.get("jev")),
                        "money": bool(r.get("money"))})
        return out

    def summary(self, g, steps=None):
        steps = steps if steps is not None else self.steps(g)
        done = sum(s["state"] == "done" for s in steps)
        lines = [f"{i}. {MARK[s['state']]} {s['title']} ({'me' if s['mine'] else 'you'})" for i, s in enumerate(steps, 1)]
        return f"**Goal: {g['title']}** · {done} of {len(steps)} steps done · {g['status']}\n\n" + "\n".join(lines)

    def say(self, g, text, **meta):
        return self.m.reply(text, kind="text", meta={"goal_id": g["id"], **meta})

    # -- the owner's words -----------------------------------------------------------------------------------------------------------
    async def handle(self, d):
        op, g = d.get("op"), self.latest()
        if op == "new":
            return await self.new(d.get("text") or "")
        if g is None:
            return self.m.reply("You have no goal open. Tell me one: “my goal is …”.")
        if op in ("start", "continue"):
            return self.start(g)
        if op == "pause":
            self.db.update("goals", "id", g["id"], status="paused", note="Paused by you.")
            return self.say(g, f"Paused: {g['title']}. “Continue my goal” picks it up again.")
        if op == "status":
            return self.say(g, self.summary(self.db.one("SELECT * FROM goals WHERE id=?", g["id"])) + (f"\n\n{g['note']}" if g.get("note") else ""))
        if op in ("step_done", "skip"):
            s = next((s for s in self.steps(g) if s["state"] != "done"), None)
            if s is None:
                return self.say(g, f"Every step of {g['title']} is already done.")
            side.todo_done(self.db, s["id"], True)
            self.db.update("goals", "id", g["id"], progress_at=time.time(), step_id=None)
            self.say(g, f"{'Done' if op == 'step_done' else 'Skipped'}: {s['title'].rstrip('.')}.")
            return self.start(self.db.one("SELECT * FROM goals WHERE id=?", g["id"]))
        if op in ("stop", "forget"):
            run = self.running.pop(g["id"], None)
            if run:
                run.cancel()
            if op == "forget":
                for s in self.steps(g):
                    side.delete(self.db, "todo", str(s["id"]))
                side.group_delete(self.db, g["group_id"])
                self.db.c.execute("DELETE FROM goals WHERE id=?", (g["id"],))
                return self.m.reply(f"Forgotten: {g['title']}, its plan and its to-dos.")
            self.db.update("goals", "id", g["id"], status="stopped")
            return self.m.reply(f"Stopped: {g['title']}. Its steps stay in your to-dos.")
        return self.m.reply("I didn't catch what to do with your goal.")

    async def new(self, text):
        """The plan: a chat model writes it, Jev reads each step, the steps become the goal's to-dos, and the plan is shown. Nothing starts."""
        text = " ".join(text.split())
        if not text:
            return self.m.reply("What's the goal?")
        written, model = await self.m.write_answer(PLAN_SYSTEM, f"The goal: {text}", kind="strong")
        steps = plan_steps(written) if written else []
        if not steps:
            why = "; ".join(model) if isinstance(model, list) else "the plan came back in a shape I couldn't read"
            return self.m.reply(f"I couldn't make a plan for that: {why}. Say it again in other words, or give me the steps.", kind="error")
        title = goal_title(text)
        name, n = title, 2
        while self.db.one("SELECT id FROM groups WHERE lower(name)=lower(?)", name):  # side.group_create refuses a name taken
            name, n = f"{title[:side.GROUP_MAX - 4]} ({n})", n + 1
        gid = side.group_create(self.db, name)
        judged = await asyncio.gather(*(side.judge(self.m.jev, s) for s in steps))
        for s, j in zip(steps, judged):
            side.todo_create(self.db, s, money=bool(j.get("money")), jev={"who": j.get("who"), "money": bool(j.get("money")), "by": j.get("by")},
                             group_id=gid)
        now = time.time()
        goal_id = self.db.insert("goals", group_id=gid, text=text, title=name, status="planned", created_at=now, progress_at=now)
        g = self.db.one("SELECT * FROM goals WHERE id=?", goal_id)
        self.m.emit("SIDE_CHANGED", side.state(self.db))
        return self.say(g, self.summary(g) + f"\n\nPlanned by {model}. Say “start my goal” and I'll do my steps one by one and tell you when "
                                               "one is yours. Every step goes through the usual approvals.")

    def start(self, g):
        if g["status"] in ("done", "stopped"):
            return self.say(g, f"{g['title']} is {g['status']}.")
        if g["id"] in self.running and not self.running[g["id"]].done():
            return self.say(g, f"I'm already working on {g['title']}.")
        self.db.update("goals", "id", g["id"], status="active", note=None)
        self.running[g["id"]] = self.m.spawn(self._advance(g["id"]))
        return self.say(g, f"Working on {g['title']}.")

    # -- advancing -----------------------------------------------------------------------------------------------------------------
    async def _advance(self, goal_id):
        while True:
            g = self.db.one("SELECT * FROM goals WHERE id=?", goal_id)
            if not g or g["status"] != "active":
                return
            steps = self.steps(g)
            todo = [s for s in steps if s["state"] != "done"]
            if not todo:
                self.db.update("goals", "id", goal_id, status="done", step_id=None, progress_at=time.time())
                self.say(g, f"Goal done: {g['title']}: all {len(steps)} steps.")
                return
            s, k = todo[0], steps.index(todo[0]) + 1
            if not s["mine"]:
                why = "it involves money" if s["money"] else "it's yours to do" if re.match(r"^\s*you\s*:", s["title"], re.I) else \
                    "it's for you, as Jev reads it"
                self.db.update("goals", "id", goal_id, status="waiting", step_id=None, checkin_at=time.time())
                self.say(g, f"{g['title']}, step {k} of {len(steps)} is yours ({why}): {s['title'].rstrip('.')}. Say “step done” when it is, "
                            "or “skip the step”.")
                return
            self.db.update("goals", "id", goal_id, step_id=s["id"])
            verdict, words = await self._run_step(s["title"])
            g = self.db.one("SELECT * FROM goals WHERE id=?", goal_id)
            if not g or g["status"] != "active":
                return
            if verdict == "done":
                side.todo_done(self.db, s["id"], True)
                self.db.update("goals", "id", goal_id, step_id=None, progress_at=time.time())
                self.m.emit("SIDE_CHANGED", side.state(self.db))
                continue
            note = {"failed": f"Step {k} didn't work: {words}", "asked": f"Step {k} needs your answer: {words}",
                    "stalled": f"Step {k} has had no result for {int(STEP_TIMEOUT_S // 60)} minutes"}[verdict]
            self.db.update("goals", "id", goal_id, status="paused", step_id=None, note=note[:500])
            self.say(g, f"{g['title']}: {note[:300]}. Paused: “continue my goal” tries it again, “skip the step” moves on.")
            return

    async def _run_step(self, text):
        """One step as an ordinary command, its result read on the bus. -> ("done" | "failed" | "asked" | "stalled", words)."""
        q = self.m.bus.subscribe()  # before the command: no event is missed
        try:
            r = await self.m.command(text, source="goal")
            mid, st, end = (r.get("message") or {}).get("id"), {}, time.monotonic() + STEP_TIMEOUT_S
            while (left := end - time.monotonic()) > 0:
                try:
                    ev = await asyncio.wait_for(q.get(), left)
                except asyncio.TimeoutError:
                    break
                verdict, words = outcome(ev, mid, st)
                if verdict:
                    return verdict, " ".join(str(words).split())[:300]
            return "stalled", ""
        finally:
            self.m.bus.unsubscribe(q)

    # -- check-ins -------------------------------------------------------------------------------------------------------------------
    async def loop(self):
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick()
            except Exception as e:  # a check-in must never stop the goals
                print("goals tick:", repr(e)[:200])

    def tick(self, now=None):
        """Once a day per open goal where nothing moved: where it stands, and what's next."""
        now = now or time.time()
        for g in self.db.all(f"SELECT * FROM goals WHERE status IN ({','.join('?' * len(OPEN))})", *OPEN):
            if now - max(g.get("progress_at") or g["created_at"], g.get("checkin_at") or 0) >= CHECKIN_S:
                self.db.update("goals", "id", g["id"], checkin_at=now)
                nxt = next((s for s in self.steps(g) if s["state"] != "done"), None)
                self.say(g, f"Checking in on {g['title']}: nothing moved in a day. " +
                         (f"Next: {nxt['title']} ({'I can do it: “continue my goal”' if nxt['mine'] else 'yours'})." if nxt else ""))


if __name__ == "__main__":
    assert plan_steps('["Search the web for beginner 5k plans and summarize the top three", "You: go for a 20-minute run"]') == [
        "Search the web for beginner 5k plans and summarize the top three", "You: go for a 20-minute run"]
    assert plan_steps("Here you go:\n1. Make a list of goals\n2) Add a calendar event at 7am\n- You: buy shoes\nThanks") == [
        "Make a list of goals", "Add a calendar event at 7am", "You: buy shoes"], "numbered or bulleted lines when it isn't JSON"
    assert plan_steps("I can't help with that.") == [] and len(plan_steps(json.dumps([f"s{i}" for i in range(12)]))) == STEPS_MAX
    assert mine("Search the web for 5k plans", {"who": "monday", "money": False})
    assert not mine("You: go for a run", {"who": "monday"}) and not mine("Order shoes", {"who": "monday", "money": True}), "yours, or money"
    assert not mine("Write a plan", {"who": None}) and not mine("Write a plan", None), "Jev unsure or not asked: the owner decides"
    st = {}
    task_msg = {"type": "MESSAGE_CREATED", "data": {"message": {"role": "monday", "kind": "task", "task_id": "T1", "meta": {"reply_to": 5}}}}
    assert outcome(task_msg, 5, st) == (None, None) and st["tasks"] == {"T1"}
    assert outcome({"type": "MESSAGE_CREATED", "data": {"message": {"role": "monday", "kind": "text", "text": "x", "meta": {"reply_to": 5}}}}, 5, st) \
        == (None, None), "a task's own end decides, not a word said meanwhile"
    assert outcome({"type": "TASK_COMPLETED", "data": {"task": {"id": "T1", "result": "Saved."}}}, 5, st) == ("done", "Saved.")
    assert outcome({"type": "TASK_FAILED", "data": {"task": {"id": "T9", "route": {"message_id": 5}, "error": "No app."}}}, 5, {}) == ("failed", "No app.")
    reply = lambda kind, **meta: {"type": "MESSAGE_CREATED", "data": {"message": {"role": "monday", "kind": kind, "text": "w", "meta": {"reply_to": 5, **meta}}}}  # noqa: E731
    assert outcome(reply("text"), 5, {}) == ("done", "w") and outcome(reply("error"), 5, {}) == ("failed", "w")
    assert outcome(reply("text", question="browser"), 5, {})[0] == "asked" and outcome(reply("text"), 6, {}) == (None, None), \
        "a question pauses; another command's reply isn't this step's"
    st = {}
    assert outcome({"type": "ROUTED", "data": {"route": {"message_id": 5, "intent": "TASK"}}}, 5, st) == (None, None) and st["intent"] == "TASK"
    assert outcome(reply("text"), 5, st) == ("asked", "w"), "routed to be done but only words came back: Monday is asking (Codex's project question)"
    st = {"intent": "CHAT"}
    assert outcome(reply("text"), 5, st) == ("done", "w"), "a step that is a question to answer is done by its answer"
    assert goal_title("to get fit by December.") == "Get fit by December"
    long_title = goal_title("understand Python decorators well enough to explain them to a friend this week")
    assert long_title == "Understand Python decorators well enough to explain them…" and len(long_title) <= side.GROUP_MAX, long_title
    print("goals self-check: OK")
