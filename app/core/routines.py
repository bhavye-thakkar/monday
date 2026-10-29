"""Routines (2026-10-17, after OpenMausBot's server/routines.ts, MIT, THIRD_PARTY_NOTICES.md): one of the owner's bots does something on a
schedule. A routine is a prompt for a bot, once at a date and time or every day at a time on chosen weekdays, with how long it usually takes
(for the week's calendar). When it comes due its run is queued; a run starts when that bot isn't already on a routine, as an ordinary request
in the bot's name (Monday.command: Jev and the rules decide, approvals as for anything typed), and its receipt follows how that request
went: working, waiting on the owner's OK, done, failed, cancelled. A run Monday was off for by more than CATCH_UP_S is a "missed" receipt,
never run late. Kept in data/routines.json; a run still going when Monday stopped is told as failed after a restart.

    python -m core.routines     self-check (no Monday, no bot: a made-up clock)
"""
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

CATCH_UP_S = 12 * 3600   # due longer ago than this when Monday next looks (it was off): missed, not run late
MAX_RUNS = 500           # receipts kept
TICK_S = 10.0            # how often Monday looks for a routine that's due
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]  # weekdays as OpenMausBot counts them: 0 is Sunday
DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
ACTIVE = ("running", "waiting")


def _days(days):
    out = sorted({d for d in days if isinstance(d, int) and 0 <= d <= 6}) if isinstance(days, (list, tuple)) else []
    return out or list(ALL_DAYS)


def clean_schedule(s):
    """{"type": "once", "at": epoch s} or {"type": "daily", "time": "HH:MM", "weekdays": [0..6]}; ValueError otherwise."""
    s = s if isinstance(s, dict) else {}
    if s.get("type") == "once":
        try:
            return {"type": "once", "at": float(s["at"])}
        except (KeyError, TypeError, ValueError):
            raise ValueError("Choose a valid date and time")
    if s.get("type") == "daily":
        t = str(s.get("time") or "")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ValueError("Time must be HH:MM")
        return {"type": "daily", "time": t, "weekdays": _days(s.get("weekdays"))}
    raise ValueError("Choose once or every day")


def next_occurrence(s, after):
    """The next time on this computer's clock strictly after `after` (epoch s), or None (a single time already past)."""
    if s["type"] == "once":
        return s["at"] if s["at"] > after else None
    hour, minute = map(int, s["time"].split(":"))
    days, base = set(_days(s.get("weekdays"))), datetime.fromtimestamp(after)
    for offset in range(9):
        d = (base + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if d.timestamp() > after and (d.weekday() + 1) % 7 in days:
            return d.timestamp()
    return None


def schedule_words(s):
    """"Every day at 08:30", "Weekdays at 08:30", "Mon, Wed at 18:00", "Once, Tue 21 Oct 09:00"."""
    if s["type"] == "once":
        return "Once, " + time.strftime("%a %d %b %H:%M", time.localtime(s["at"]))
    days = _days(s.get("weekdays"))
    when = "Every day" if days == ALL_DAYS else "Weekdays" if days == [1, 2, 3, 4, 5] else "Weekends" if days == [0, 6] else \
        ", ".join(DAY_NAMES[d] for d in days)
    return f"{when} at {s['time']}"


def clean(d):
    """A routine as the owner wrote it, checked: name, prompt, bot, enabled, schedule, minutes (15 to 240, for the calendar)."""
    name, prompt, bot = str(d.get("name") or "").strip()[:80], str(d.get("prompt") or "").strip()[:20000], str(d.get("bot") or "").strip()
    if not name:
        raise ValueError("Give the routine a name")
    if not prompt:
        raise ValueError("Tell the bot what to do")
    if not bot:
        raise ValueError("Choose a bot")
    try:
        minutes = round(float(d.get("minutes") or 30))
    except (TypeError, ValueError):
        minutes = 30
    return {"name": name, "prompt": prompt, "bot": bot, "enabled": d.get("enabled") is not False, "schedule": clean_schedule(d.get("schedule")),
            "minutes": min(240, max(15, minutes))}


class Routines:
    """The routines and their runs (receipts). Pure bookkeeping: Monday's loop asks what's due (due), what can start (startable), and tells
    it what happened (started, task, waiting, finished)."""

    def __init__(self, path, now=time.time):
        self.path, self.now = Path(path), now
        try:
            disk = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            disk = {}
        self.routines, self.runs = list(disk.get("routines") or []), list(disk.get("runs") or [])
        lost = [r for r in self.runs if r["status"] in ACTIVE + ("queued",)]
        for r in lost:  # Monday stopped while these were going: nothing of this process still runs them
            r.update(status="failed", finished_at=self.now(), error="Monday restarted while this routine was running" if r["status"] != "queued"
                     else "Monday restarted before this run started")
        if lost:
            self.save()

    def save(self):
        del self.runs[:-MAX_RUNS]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "routines": self.routines, "runs": self.runs}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, rid):
        return next((r for r in self.routines if r["id"] == rid), None)

    def _first(self, s, now):
        return max(s["at"], now) if s["type"] == "once" else next_occurrence(s, now)

    def create(self, d):
        c, at = clean(d), self.now()
        r = {"id": uuid.uuid4().hex[:10], **c, "next_at": self._first(c["schedule"], at) if c["enabled"] else None, "created_at": at, "updated_at": at}
        self.routines.insert(0, r)
        self.save()
        return r

    def update(self, rid, patch):
        r = self.get(rid)
        if not r:
            return None
        c = clean({**{k: r[k] for k in ("name", "prompt", "bot", "enabled", "schedule", "minutes")}, **{k: v for k, v in patch.items() if v is not None}})
        r.update(c, next_at=self._first(c["schedule"], self.now()) if c["enabled"] else None, updated_at=self.now())
        if not c["enabled"]:
            for run in self.runs:
                if run["routine"] == rid and run["status"] == "queued":
                    run.update(status="cancelled", finished_at=self.now(), error="The routine was paused before this run started")
        self.save()
        return r

    def remove(self, rid):
        r = self.get(rid)
        if not r:
            return False
        self.routines.remove(r)
        for run in self.runs:
            if run["routine"] == rid and run["status"] == "queued":
                run.update(status="cancelled", finished_at=self.now(), error="The routine was deleted before this run started")
        self.save()
        return True

    def _run(self, r, scheduled, manual):
        run = {"id": uuid.uuid4().hex[:10], "routine": r["id"], "name": r["name"], "prompt": r["prompt"], "bot": r["bot"], "minutes": r["minutes"],
               "scheduled_for": scheduled, "status": "queued", "manual": manual, "created_at": self.now(), "message_id": None, "task_id": None,
               "started_at": None, "finished_at": None, "output": None, "error": None}
        self.runs.append(run)
        return run

    def run_now(self, rid):
        r = self.get(rid)
        if not r:
            return None
        run = self._run(r, self.now(), True)
        self.save()
        return run

    def due(self, now=None):
        """The routines whose time has come: a queued run each (or a missed receipt, when Monday was off past CATCH_UP_S), their next time
        set; a single time is then off. -> the new runs."""
        now, out = now or self.now(), []
        for r in self.routines:
            if not r["enabled"] or r.get("next_at") is None or r["next_at"] > now:
                continue
            run = self._run(r, r["next_at"], False)
            if now - r["next_at"] > CATCH_UP_S:
                run.update(status="missed", finished_at=now, error="Monday wasn't running for more than 12 hours after its time")
            out.append(run)
            once = r["schedule"]["type"] == "once"
            r.update(next_at=None if once else next_occurrence(r["schedule"], max(now, r["next_at"])), enabled=r["enabled"] and not once, updated_at=now)
        if out:
            self.save()
        return out

    def startable(self, busy=lambda bot: False):
        """Queued runs that can start now, oldest first: one at a time per bot (a run of its already going, or the bot busy, waits)."""
        going = {r["bot"].lower() for r in self.runs if r["status"] in ACTIVE}
        out = []
        for run in sorted((r for r in self.runs if r["status"] == "queued"), key=lambda r: r["scheduled_for"]):
            if run["bot"].lower() not in going and not busy(run["bot"]):
                going.add(run["bot"].lower())
                out.append(run)
        return out

    def find(self, rid):
        return next((r for r in self.runs if r["id"] == rid), None)

    def started(self, rid, message_id):
        run = self.find(rid)
        if run:
            run.update(status="running", started_at=self.now(), message_id=message_id)
            self.save()
        return run

    def by_message(self, message_id):
        return next((r for r in self.runs if r["status"] in ACTIVE and r.get("message_id") == message_id), None) if message_id else None

    def by_task(self, task_id):
        return next((r for r in self.runs if r["status"] in ACTIVE and r.get("task_id") == task_id), None) if task_id else None

    def tasked(self, run, task_id):
        run["task_id"] = task_id
        self.save()

    def waiting(self, run, yes=True):
        run["status"] = "waiting" if yes else "running"
        self.save()

    def finished(self, run, ok, output=None, error=None, cancelled=False):
        run.update(status="cancelled" if cancelled else "completed" if ok else "failed", finished_at=self.now(),
                   output=(str(output).strip()[:2000] or None) if output else run.get("output"), error=(str(error)[:500] if error else None))
        self.save()
        return run

    def cancel(self, rid):
        run = self.find(rid)
        if not run or run["status"] not in ACTIVE + ("queued",):
            return None
        return self.finished(run, False, error="Cancelled by the owner", cancelled=True)

    def between(self, start, end):
        """Receipts scheduled in [start, end), newest first, and each routine's times in it that are still to come (the week's calendar)."""
        runs = sorted((r for r in self.runs if start <= r["scheduled_for"] < end), key=lambda r: -r["scheduled_for"])
        ahead = []
        for r in self.routines:
            t = r.get("next_at") if r["enabled"] else None
            while t is not None and t < end and len(ahead) < 200:
                if t >= start:
                    ahead.append({"routine": r["id"], "name": r["name"], "bot": r["bot"], "at": t, "minutes": r["minutes"]})
                t = None if r["schedule"]["type"] == "once" else next_occurrence(r["schedule"], t)
        return runs, sorted(ahead, key=lambda x: x["at"])


if __name__ == "__main__":
    import tempfile
    clock = [datetime(2026, 10, 19, 8, 0).timestamp()]  # a Monday, 08:00 on this computer's clock
    with tempfile.TemporaryDirectory() as d:
        R = Routines(Path(d) / "routines.json", now=lambda: clock[0])
        for bad, why in (({"name": "", "prompt": "x", "bot": "Scout", "schedule": {"type": "once", "at": 1}}, "name"),
                         ({"name": "a", "prompt": "", "bot": "Scout", "schedule": {"type": "once", "at": 1}}, "what to do"),
                         ({"name": "a", "prompt": "x", "bot": "", "schedule": {"type": "once", "at": 1}}, "bot"),
                         ({"name": "a", "prompt": "x", "bot": "Scout", "schedule": {"type": "daily", "time": "25:00"}}, "HH:MM"),
                         ({"name": "a", "prompt": "x", "bot": "Scout", "schedule": {"type": "weekly"}}, "once or every day")):
            try:
                R.create(bad)
                raise AssertionError(bad)
            except ValueError as e:
                assert why in str(e), (bad, e)
        brief = R.create({"name": "Morning brief", "prompt": "Summarize my inbox", "bot": "Scout", "minutes": 5,
                          "schedule": {"type": "daily", "time": "08:30", "weekdays": [1, 2, 3, 4, 5]}})
        assert brief["minutes"] == 15 and brief["next_at"] == datetime(2026, 10, 19, 8, 30).timestamp(), brief
        assert schedule_words(brief["schedule"]) == "Weekdays at 08:30" and schedule_words({"type": "daily", "time": "07:00", "weekdays": [1, 3]}) == "Mon, Wed at 07:00"
        assert next_occurrence(brief["schedule"], datetime(2026, 10, 23, 9, 0).timestamp()) == datetime(2026, 10, 26, 8, 30).timestamp(), "Friday after: Monday"
        once = R.create({"name": "Pay rent", "prompt": "Remind me to pay rent", "bot": "Forge", "schedule": {"type": "once", "at": clock[0] + 600}})
        assert R.due() == [], "nothing due yet"
        clock[0] += 31 * 60  # 08:31
        got = R.due()
        assert sorted(r["name"] for r in got) == ["Morning brief", "Pay rent"] and all(r["status"] == "queued" for r in got)
        assert R.get(once["id"])["enabled"] is False and R.get(once["id"])["next_at"] is None, "a single time: done, then off"
        assert R.get(brief["id"])["next_at"] == datetime(2026, 10, 20, 8, 30).timestamp(), "tomorrow"
        two = R.run_now(brief["id"])  # Run now, while Scout's scheduled run waits too
        ready = R.startable()  # oldest first: Forge's 08:10, then Scout's 08:30; Scout's Run now waits for its 08:30 one
        scout, forge = (next(r for r in ready if r["bot"] == b) for b in ("Scout", "Forge"))
        assert [r["bot"] for r in ready] == ["Forge", "Scout"] and not scout["manual"] and two not in ready, "one at a time per bot"
        R.started(scout["id"], 41)
        assert R.startable() == [forge] and R.by_message(41) is scout, "Scout busy: its next waits"
        R.tasked(scout, "TASK-1")
        R.waiting(scout)
        assert R.by_task("TASK-1")["status"] == "waiting"
        R.finished(scout, True, "Three emails need you.")
        assert R.find(scout["id"])["status"] == "completed" and R.find(scout["id"])["output"] == "Three emails need you."
        assert R.startable(busy=lambda bot: bot == "Forge") == [two], "a busy bot's run waits; Scout's Run now goes"
        assert R.cancel(two["id"])["status"] == "cancelled" and R.cancel(two["id"]) is None
        R.started(forge["id"], 42)  # Forge's run, going when Monday stops
        clock[0] = datetime(2026, 10, 21, 8, 0).timestamp()  # Monday was off for two days
        R2 = Routines(Path(d) / "routines.json", now=lambda: clock[0])
        assert R2.find(forge["id"])["status"] == "failed" and "restarted" in R2.find(forge["id"])["error"], "a run lost to a restart"
        late = R2.due()
        assert [(r["name"], r["status"]) for r in late] == [("Morning brief", "missed")] and R2.get(brief["id"])["next_at"] == \
            datetime(2026, 10, 21, 8, 30).timestamp(), "two days late: a missed receipt, then on as usual"
        R2.update(brief["id"], {"enabled": False})
        assert R2.get(brief["id"])["next_at"] is None and R2.due(datetime(2026, 10, 22, 9, 0).timestamp()) == [], "paused"
        R2.update(brief["id"], {"enabled": True, "schedule": {"type": "daily", "time": "18:00", "weekdays": [3]}})
        week = datetime(2026, 10, 19).timestamp()
        runs, ahead = R2.between(week, week + 7 * 86400)
        assert [a["at"] for a in ahead] == [datetime(2026, 10, 21, 18, 0).timestamp()] and len(runs) >= 3, (ahead, runs)
        assert R2.remove(brief["id"]) and not R2.remove(brief["id"]) and R2.get(brief["id"]) is None
    print("routines self-check: OK (checked on the way in; once and every day on chosen weekdays; due, one run at a time per bot, waiting, done, "
          "cancelled; missed when Monday was off; lost to a restart; paused, changed, deleted; the week's calendar)")
