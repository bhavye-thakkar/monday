"""The owner's own organizing in the workspace's sidebar (2026-10-09: "a + after Tasks so that I can add tasks myself too… rename the
chats… three dots: delete, rename, pin, move to group… add groups too… archive, and maintain the archives").

- Items: Monday's tasks (kind "task", ref = its id), the owner's to-dos ("todo", ref = its id) and the main chat ("chat", "main": rename
  only). Per item a pin, a group, an archive date and a delete date (table `side`). Renaming a task changes its title everywhere; the
  words the owner said stay as its prompt.
- Delete: a task is only hidden (what Monday did stays in Activity and its receipts); a to-do is gone. A task still running can't be
  archived or deleted: it is stopped first.
- The + box: Jev reads what the owner typed (jev.todo: who would carry it out, does it involve money); the gates are here. Anything that
  involves money is never started from the + box until the owner says so (their words, 2026-10-09): Jev's pick becomes their list, and
  "Monday, do it" asks once more. When Jev isn't answering nothing is recommended and the owner picks; the money words still hold.
"""
import re
import time

from .db import ACTIVE

KINDS = ("task", "todo", "chat")
WHO_MIN = 0.60       # Jev's "who" from which the + box recommends it; below, the owner picks
MONEY_HOLD = 0.50    # Jev's "involves money" from which a + task waits for the owner's word (MONEY_WORDS hold it too)
TITLE_MAX = 200
GROUP_MAX = 60
JUDGE_MAX = 300      # characters of a + task Jev reads
CHAT_TITLE_KEY = "main_chat_title"
CHAT_TITLE = "Main chat"
# Wide on purpose: a false hit costs the owner one more click, a miss could start a payment (router.permit still asks before paying).
MONEY_WORDS = re.compile(r"\b(?:pay|pays|paid|paying|payments?|buy|buying|bought|purchas(?:e|es|ed|ing)|checkout|subscri(?:be|bing|ption)|"
                         r"transfer\s+(?:money|funds|rs|inr|\$)|send\s+(?:money|rs|inr|\$)|upi|g\s?pay|paytm|phonepe|bank|credit\s+card|"
                         r"debit\s+card|invoices?|bills?|rent|emi|donat(?:e|ion)|recharge|order\s+(?:food|online|from|a|an|some|the|my)|"
                         r"book\s+(?:a\s+|an\s+|the\s+|my\s+)?(?:flight|train|bus|hotel|cab|taxi|ticket|tickets|room|table|stay))\b", re.I)


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _check(db, kind, ref):
    """The item must exist (and a task must not be deleted). Raises KeyError for 404, ValueError for 400."""
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r}.")
    if kind == "chat":
        if ref != "main":
            raise KeyError("No such chat.")
    elif kind == "task":
        if not db.one("SELECT id FROM tasks WHERE id=?", ref) or db.one("SELECT 1 AS x FROM side WHERE kind='task' AND ref=? AND "
                                                                           "deleted_at IS NOT NULL", ref):
            raise KeyError("No such task.")
    elif not str(ref).isdigit() or not db.one("SELECT id FROM todos WHERE id=?", int(ref)):
        raise KeyError("No such to-do.")


def _running(db, kind, ref):
    return kind == "task" and (db.one("SELECT status FROM tasks WHERE id=?", ref) or {}).get("status") in ACTIVE


def _upsert(db, kind, ref, **cols):
    keys = ", ".join(cols)
    db.c.execute(f"INSERT INTO side (kind, ref, {keys}) VALUES (?, ?, {', '.join('?' * len(cols))}) "
                 f"ON CONFLICT(kind, ref) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in cols)}", [kind, str(ref), *cols.values()])


def state(db):
    """Everything the sidebar needs besides the task list itself: groups, the owner's to-dos, each organized item's pin / group / archive,
    the tasks those name (an old pinned or archived task is shown even when it's past the recent list), the deleted task ids (so a page
    that loaded one before drops it) and the main chat's name."""
    meta = db.all("SELECT kind, ref, pinned_at, group_id, archived_at FROM side WHERE deleted_at IS NULL")
    refs = [m["ref"] for m in meta if m["kind"] == "task"]
    tasks = db.all(f"SELECT id, title, status, agent, created_at FROM tasks WHERE id IN ({','.join('?' * len(refs))})", *refs) if refs else []
    return {"groups": db.all("SELECT * FROM groups ORDER BY created_at"), "todos": db.all("SELECT * FROM todos ORDER BY created_at DESC"),
            "meta": meta, "tasks": tasks, "deleted": [r["ref"] for r in db.all("SELECT ref FROM side WHERE kind='task' AND deleted_at IS NOT NULL")],
            "chat_title": db.setting(CHAT_TITLE_KEY) or CHAT_TITLE}


def update(db, kind, ref, changes):
    """changes: any of title (str), pinned (bool), group_id (int, or None for no group), archived (bool)."""
    _check(db, kind, ref)
    if kind == "chat" and set(changes) - {"title"}:
        raise ValueError("The main chat can only be renamed.")
    if changes.get("archived") and _running(db, kind, ref):
        raise ValueError("That task is still running. Stop it or let it finish first.")
    if "title" in changes:
        t = _clean(changes["title"], TITLE_MAX)
        if not t:
            raise ValueError("A name can't be empty.")
        if kind == "task":
            db.update("tasks", "id", ref, title=t)
        elif kind == "todo":
            db.update("todos", "id", int(ref), title=t)
        else:
            db.set_setting(CHAT_TITLE_KEY, t)
    now, cols = time.time(), {}
    if "pinned" in changes:
        cols["pinned_at"] = now if changes["pinned"] else None
    if "group_id" in changes:
        g = changes["group_id"]
        if g is not None and not db.one("SELECT id FROM groups WHERE id=?", g):
            raise KeyError("No such group.")
        cols["group_id"] = g
    if "archived" in changes:
        cols["archived_at"] = now if changes["archived"] else None
    if cols:
        _upsert(db, kind, ref, **cols)


def delete(db, kind, ref):
    """A task: hidden for good (its record stays for Activity). A to-do: gone. The main chat can't be deleted."""
    _check(db, kind, ref)
    if kind == "chat":
        raise ValueError("The main chat can't be deleted.")
    if _running(db, kind, ref):
        raise ValueError("That task is still running. Stop it or let it finish first.")
    if kind == "task":
        _upsert(db, "task", ref, deleted_at=time.time(), pinned_at=None, group_id=None)
    else:
        db.c.execute("DELETE FROM todos WHERE id=?", (int(ref),))
        db.c.execute("DELETE FROM side WHERE kind='todo' AND ref=?", (str(ref),))


def group_create(db, name):
    n = _clean(name, GROUP_MAX)
    if not n:
        raise ValueError("A group needs a name.")
    if db.one("SELECT id FROM groups WHERE lower(name)=lower(?)", n):
        raise ValueError(f"There's already a group called {n}.")
    return db.insert("groups", name=n, created_at=time.time())


def group_rename(db, gid, name):
    n = _clean(name, GROUP_MAX)
    if not db.one("SELECT id FROM groups WHERE id=?", gid):
        raise KeyError("No such group.")
    if not n:
        raise ValueError("A group needs a name.")
    if db.one("SELECT id FROM groups WHERE lower(name)=lower(?) AND id<>?", n, gid):
        raise ValueError(f"There's already a group called {n}.")
    db.update("groups", "id", gid, name=n)


def group_delete(db, gid):
    """The group goes; what was in it stays, outside any group."""
    if not db.one("SELECT id FROM groups WHERE id=?", gid):
        raise KeyError("No such group.")
    db.c.execute("UPDATE side SET group_id=NULL WHERE group_id=?", (gid,))
    db.c.execute("DELETE FROM groups WHERE id=?", (gid,))


def todo_create(db, title, money=False, jev=None, group_id=None):
    t = _clean(title, TITLE_MAX)
    if not t:
        raise ValueError("A to-do needs some words.")
    if group_id is not None and not db.one("SELECT id FROM groups WHERE id=?", group_id):
        raise KeyError("No such group.")
    tid = db.insert("todos", title=t, created_at=time.time(), money=int(bool(money) or bool(MONEY_WORDS.search(t))), jev=jev)
    if group_id is not None:
        _upsert(db, "todo", tid, group_id=group_id)
    return db.one("SELECT * FROM todos WHERE id=?", tid)


def todo_done(db, tid, done):
    _check(db, "todo", str(tid))
    db.update("todos", "id", int(tid), done_at=time.time() if done else None)


async def judge(jev, text):
    """Jev's read of a + task, with the gates applied: who (monday / owner / None = no recommendation) and whether it involves money (then
    it waits for the owner's word). by: "jev" when Jev answered, else "rules" (the money words alone, nothing recommended)."""
    t = _clean(text, JUDGE_MAX)
    words = bool(MONEY_WORDS.search(t))
    r = await jev.todo(t) if t else {"status": "empty"}
    if r.get("status") != "ok":
        why = {"not_configured": "Jev isn't set up", "offline": "Jev is offline", "timeout": "Jev didn't answer in time",
               "empty": "nothing to read yet"}.get(r.get("status"), "Jev couldn't answer")
        return {"who": None, "money": words, "by": "rules", "jev": r.get("status"), "detail": f"{why}, so you choose.",
                "latency_ms": r.get("latency_ms")}
    p = r.get("money")
    return {"who": r.get("who") if (r.get("who_confidence") or 0) >= WHO_MIN else None, "who_confidence": r.get("who_confidence"),
            "money": words or (p or 0) >= MONEY_HOLD, "money_p": p, "by": "jev", "jev": "ok", "latency_ms": r.get("latency_ms"),
            "model": r.get("model")}


if __name__ == "__main__":  # python -m core.side
    import asyncio
    import tempfile
    from pathlib import Path

    from .db import DB
    with tempfile.TemporaryDirectory() as d:
        db = DB(Path(d) / "t.db")
        now = time.time()
        for i, st in enumerate(("COMPLETED", "RUNNING", "FAILED")):
            db.insert("tasks", id=f"TASK-T-{i}", title=f"t{i}", prompt=f"p{i}", agent="computer", status=st, created_at=now + i)
        assert [t["id"] for t in db.tasks()] == ["TASK-T-2", "TASK-T-1", "TASK-T-0"]
        update(db, "task", "TASK-T-0", {"title": "  Renamed   task ", "pinned": True})
        assert db.task("TASK-T-0")["title"] == "Renamed task" and db.task("TASK-T-0")["prompt"] == "p0", "the words said stay as the prompt"
        g = group_create(db, "Work")
        for bad in ("work", "  "):
            try:
                group_create(db, bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        update(db, "task", "TASK-T-2", {"group_id": g, "archived": True})
        s = state(db)
        meta = {(m["kind"], m["ref"]): m for m in s["meta"]}
        assert meta[("task", "TASK-T-0")]["pinned_at"] and meta[("task", "TASK-T-2")]["group_id"] == g and meta[("task", "TASK-T-2")]["archived_at"]
        assert {t["id"] for t in s["tasks"]} == {"TASK-T-0", "TASK-T-2"} and s["chat_title"] == CHAT_TITLE
        for bad in (lambda: update(db, "task", "TASK-T-1", {"archived": True}), lambda: delete(db, "task", "TASK-T-1"),
                    lambda: update(db, "chat", "main", {"pinned": True}), lambda: delete(db, "chat", "main"),
                    lambda: update(db, "task", "TASK-T-0", {"title": " "})):
            try:
                bad()
                raise AssertionError("refused: a running task, the main chat's pin/delete, an empty name")
            except ValueError:
                pass
        for missing in (lambda: update(db, "task", "NOPE", {"pinned": True}), lambda: update(db, "todo", "99", {"pinned": True}),
                        lambda: update(db, "task", "TASK-T-0", {"group_id": 999})):
            try:
                missing()
                raise AssertionError("404")
            except KeyError:
                pass
        update(db, "chat", "main", {"title": "Home"})
        assert state(db)["chat_title"] == "Home"
        delete(db, "task", "TASK-T-2")
        assert [t["id"] for t in db.tasks()] == ["TASK-T-1", "TASK-T-0"], "a deleted task leaves every list"
        assert db.one("SELECT id FROM tasks WHERE id='TASK-T-2'") and "TASK-T-2" in state(db)["deleted"], "...but its record stays"
        try:
            update(db, "task", "TASK-T-2", {"pinned": True})
            raise AssertionError("a deleted task is gone for organizing too")
        except KeyError:
            pass
        assert todo_create(db, "pay the rent")["money"] == 1, "the money words are checked here too, not taken from the page"
        delete(db, "todo", str(db.one("SELECT id FROM todos WHERE title='pay the rent'")["id"]))
        td = todo_create(db, "Call the bank", money=True, group_id=g)
        todo_done(db, td["id"], True)
        assert db.one("SELECT done_at FROM todos WHERE id=?", td["id"])["done_at"] and td["money"] == 1
        group_delete(db, g)
        assert not state(db)["groups"] and all(m["group_id"] is None for m in state(db)["meta"]), "the group goes, its items stay"
        delete(db, "todo", str(td["id"]))
        assert not state(db)["todos"] and not db.one("SELECT 1 AS x FROM side WHERE kind='todo'"), "a deleted to-do is gone"
        db.c.close()  # Windows keeps an open database file

    class FakeJev:
        def __init__(self, r):
            self.r = r

        async def todo(self, text):
            return self.r

    async def judged():
        ok = {"status": "ok", "who": "monday", "who_confidence": 0.9, "money": 0.1, "latency_ms": 300}
        assert (await judge(FakeJev(ok), "find flights to Goa"))["who"] == "monday"
        r = await judge(FakeJev({**ok, "who_confidence": 0.5}), "something vague")
        assert r["who"] is None and not r["money"], "below WHO_MIN: no recommendation"
        r = await judge(FakeJev({**ok, "money": 0.8}), "get the new headphones")
        assert r["money"] and r["who"] == "monday", "Jev's money holds it"
        r = await judge(FakeJev(ok), "pay the electricity bill")
        assert r["money"], "the money words hold it even when Jev says no"
        r = await judge(FakeJev({"status": "not_configured"}), "buy milk")
        assert r["who"] is None and r["money"] and r["by"] == "rules" and "Jev isn't set up" in r["detail"]
        assert not (await judge(FakeJev({"status": "timeout"}), "rename the photos folder"))["money"]
    asyncio.run(judged())
    for said in ("pay rent", "book a flight to Goa", "order food from Swiggy", "renew my Netflix subscription", "send money to Rahul",
                 "recharge my phone", "buy groceries"):
        assert MONEY_WORDS.search(said), said
    for said in ("open Spotify", "find flights to Goa", "rename the chats", "call mom", "book a meeting with Sam"):
        assert not MONEY_WORDS.search(said), said
    print("side self-check: OK")
