"""Monday's single SQLite store.

One file (data/monday.db): messages, tasks, the event log (task logs + live-event replay),
permissions, receipts, paired devices, uploaded files, settings, projects.
All access happens on the asyncio thread, so one connection is enough.
"""
import json
import sqlite3
import time
from pathlib import Path

# Each entry is one migration; PRAGMA user_version records how many ran.
MIGRATIONS = [
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, path TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY, client_id TEXT UNIQUE, role TEXT NOT NULL, kind TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '', task_id TEXT, permission_id INTEGER, meta TEXT,
        source TEXT, created_at REAL NOT NULL);
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, agent TEXT NOT NULL,
        project_id INTEGER REFERENCES projects(id), status TEXT NOT NULL, current_action TEXT,
        files_changed TEXT NOT NULL DEFAULT '[]', result TEXT, error TEXT, usage TEXT,
        graphify TEXT, route TEXT, source TEXT,
        created_at REAL NOT NULL, started_at REAL, finished_at REAL);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, type TEXT NOT NULL, task_id TEXT,
        data TEXT NOT NULL);
    CREATE TABLE permissions (
        id INTEGER PRIMARY KEY, task_id TEXT, agent TEXT NOT NULL, kind TEXT NOT NULL,
        title TEXT NOT NULL, detail TEXT, project TEXT, risk TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, decided_at REAL, decided_by TEXT);
    CREATE TABLE receipts (
        id INTEGER PRIMARY KEY, task_id TEXT, tool TEXT NOT NULL, status TEXT NOT NULL,
        detail TEXT, started_at REAL NOT NULL, completed_at REAL);
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, name TEXT,
        created_at REAL NOT NULL, last_seen REAL);
    CREATE TABLE files (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
        mime TEXT, created_at REAL NOT NULL);
    CREATE INDEX events_task ON events(task_id, id);
    CREATE INDEX tasks_status ON tasks(status, created_at);
    CREATE INDEX messages_task ON messages(task_id);
    """,
    # 2: Jev decision log (latency, outcome, answers, the route policy chose) for analytics
    """
    CREATE TABLE jev_decisions (
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, message_id INTEGER, status TEXT NOT NULL,
        latency_ms REAL, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
        intent TEXT, confidence REAL, project TEXT, complexity REAL, destructive REAL,
        route TEXT, fallback TEXT, error TEXT, question_set TEXT, answers TEXT);
    CREATE INDEX jev_decisions_ts ON jev_decisions(ts);
    """,
    # 3: computer use. A task's plan steps (status per step), a project's own test/dev commands, and one latency trace
    # per command (client STT/TTS marks + server route/Jev/tool marks, epoch ms).
    """
    ALTER TABLE tasks ADD COLUMN steps TEXT;
    ALTER TABLE projects ADD COLUMN commands TEXT;
    CREATE TABLE latency (message_id INTEGER PRIMARY KEY, marks TEXT NOT NULL, source TEXT, updated_at REAL NOT NULL);
    """,
    # 4: the reflex layer (core/jev.py prepass + judge). One row per spoken sentence Jev judged before it was sent, and per
    # chat reply judged before it was spoken, with every answer, so the gates can be retuned from real numbers.
    """
    CREATE TABLE reflex_calls (
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, purpose TEXT NOT NULL, status TEXT NOT NULL, latency_ms REAL, model TEXT,
        input_tokens INTEGER, output_tokens INTEGER, text TEXT, answers TEXT, verdict TEXT, filming INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX reflex_calls_ts ON reflex_calls(ts);
    """,
    # 5: what Monday decided to keep (Phase 13). One row per event Jev classified, with who decided (jev / rule / code), what it was kept as
    # and where; a "session" memory lives here (expires_at) and the rest in the vault.
    """
    CREATE TABLE memory_log (
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, confidence REAL, title TEXT, text TEXT,
        note TEXT, expires_at REAL, decided_by TEXT, model TEXT, latency_ms REAL, answers TEXT);
    CREATE INDEX memory_log_ts ON memory_log(ts);
    """,
    # 6: the owner's own organizing in the workspace's sidebar (core/side.py, 2026-10-09): groups, the owner's to-dos, and per item a
    # pin, a group, an archive date and a delete date. A deleted task is only hidden (what Monday did stays in Activity and its
    # receipts); a deleted to-do is gone.
    """
    CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE todos (id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, done_at REAL,
        money INTEGER NOT NULL DEFAULT 0, jev TEXT);
    CREATE TABLE side (kind TEXT NOT NULL, ref TEXT NOT NULL, pinned_at REAL, group_id INTEGER, archived_at REAL, deleted_at REAL,
        PRIMARY KEY (kind, ref));
    """,
    # 7: the workflow recorder (core/recorder.py, 2026-10-10): a recorded workflow (its structured steps + assertions live in the
    # `workflow` JSON, editable in place) and one row per replay with its evidence-based verdict. Screenshots and other artifacts use
    # the existing files/ storage, referenced from the run's `result` JSON. No secrets in either (recorder.py skips them).
    """
    CREATE TABLE recordings (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, target_app TEXT, created_at REAL NOT NULL, updated_at REAL,
        workflow TEXT NOT NULL);
    CREATE TABLE test_runs (
        id INTEGER PRIMARY KEY, recording_id TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL,
        passed INTEGER, failed INTEGER, skipped INTEGER, duration_ms INTEGER, result TEXT, task_id TEXT);
    CREATE INDEX test_runs_rec ON test_runs(recording_id, id);
    """,
    # 8: goals (core/goals.py, 2026-10-15): the owner's words, the group whose to-dos are the plan's steps, where it stands (status,
    # the step running, when it last moved and was last checked in on, why it paused)
    """
    CREATE TABLE goals (
        id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL, text TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, progress_at REAL, checkin_at REAL, step_id INTEGER, note TEXT);
    """,
    # 9: a coding task's tests (core/evidence.py, 2026-10-17, after Dotpals): the verdict on the code as the task left it (state, counts,
    # what failed and why, who ran them and when, the repair tries), shown on its card and in the notch
    """
    ALTER TABLE tasks ADD COLUMN tests TEXT;
    """,
    # 10 (2026-10-17): who asked (a bot's name, else the agent's) and whether its card may offer Always allow (router.standing)
    """
    ALTER TABLE permissions ADD COLUMN who TEXT;
    ALTER TABLE permissions ADD COLUMN always INTEGER;
    """,
    # 11 (2026-10-27, the owner's master loop): a task may wait for others (`after`: the ids that must be COMPLETED first); the
    # request that made several tasks is their group (route.group = its message id): independent parts run side by side,
    # dependent ones in order, and the last to end reports for all of them
    """
    ALTER TABLE tasks ADD COLUMN after TEXT;
    """,
    # 12 (2026-10-27, docs/adr/0009): the phone companion's paired devices: platform and app, what each may do (approve, control),
    # where it last called from, when it was revoked or rotated, and the previous secret's hash kept a short while after a
    # rotation. Hashes only: a secret is never stored
    """
    ALTER TABLE devices ADD COLUMN platform TEXT;
    ALTER TABLE devices ADD COLUMN app_version TEXT;
    ALTER TABLE devices ADD COLUMN capabilities TEXT;
    ALTER TABLE devices ADD COLUMN last_address TEXT;
    ALTER TABLE devices ADD COLUMN revoked_at REAL;
    ALTER TABLE devices ADD COLUMN rotated_at REAL;
    ALTER TABLE devices ADD COLUMN previous_hash TEXT;
    ALTER TABLE devices ADD COLUMN previous_until REAL;
    """,
]

JSON_COLS = {"meta", "data", "files_changed", "usage", "graphify", "route", "detail", "answers", "steps", "commands", "marks", "jev",
             "workflow", "result", "tests", "after", "capabilities"}
ACTIVE = ("QUEUED", "STARTING", "RUNNING", "WAITING", "WAITING_FOR_PERMISSION", "WAITING_FOR_USER")
LATENCY_KEEP = 500
REFLEX_KEEP = 5000  # reflex_calls rows kept (the newest)


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str) and d[k][:1] in ("{", "["):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def _val(v):
    return json.dumps(v) if isinstance(v, (dict, list)) else v


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.c = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA foreign_keys=ON")
        ver = self.c.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[ver:], start=ver + 1):
            self.c.executescript(f"BEGIN;{sql};PRAGMA user_version={i};COMMIT;")

    # -- generic helpers ---------------------------------------------------
    def one(self, sql, *args):
        return _row(self.c.execute(sql, args).fetchone())

    def all(self, sql, *args):
        return [_row(r) for r in self.c.execute(sql, args).fetchall()]

    def insert(self, table, **cols):
        keys = ",".join(cols)
        marks = ",".join("?" * len(cols))
        cur = self.c.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})",
                             [_val(v) for v in cols.values()])
        return cur.lastrowid

    def update(self, table, key, key_val, **cols):
        sets = ",".join(f"{k}=?" for k in cols)
        self.c.execute(f"UPDATE {table} SET {sets} WHERE {key}=?",
                       [_val(v) for v in cols.values()] + [key_val])

    # -- settings ----------------------------------------------------------
    def setting(self, key, default=None):
        r = self.c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_setting(self, key, value):
        self.c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # -- tasks -------------------------------------------------------------
    def new_task_id(self):
        year = time.strftime("%Y")
        n = self.c.execute("SELECT COUNT(*) FROM tasks WHERE id LIKE ?", (f"TASK-{year}-%",)).fetchone()[0]
        return f"TASK-{year}-{n + 1:04d}"

    def task(self, task_id):
        return self.one("SELECT t.*, p.name AS project, p.path AS project_path FROM tasks t "
                        "LEFT JOIN projects p ON p.id=t.project_id WHERE t.id=?", task_id)

    def tasks(self, statuses=None, limit=100):
        # a task the owner deleted is gone from every list (core/side.py); its receipts and events stay
        sql = ("SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
               "WHERE t.id NOT IN (SELECT ref FROM side WHERE kind='task' AND deleted_at IS NOT NULL)")
        args = []
        if statuses:
            sql += f" AND t.status IN ({','.join('?' * len(statuses))})"
            args = list(statuses)
        return self.all(sql + " ORDER BY t.created_at DESC LIMIT ?", *args, limit)

    def fail_orphans(self):
        """Tasks cannot survive a Core restart (their worker processes died with it)."""
        now = time.time()
        orphans = self.all(f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(ACTIVE))})", *ACTIVE)
        for t in orphans:
            self.update("tasks", "id", t["id"], status="FAILED", finished_at=now,
                        error="Monday Core restarted while this task was active.")
        self.c.execute("UPDATE permissions SET status='EXPIRED', decided_at=? WHERE status='PENDING'", (now,))
        return [t["id"] for t in orphans]

    # -- latency traces ------------------------------------------------------
    def mark(self, message_id, source=None, **marks):
        """Merge timing marks (epoch ms) into one command's trace. First write of a mark wins: it's when it happened."""
        if not message_id:
            return
        row = self.one("SELECT marks FROM latency WHERE message_id=?", message_id)
        cur = (row or {}).get("marks") or {}
        cur.update({k: v for k, v in marks.items() if v is not None and k not in cur})
        self.c.execute("INSERT INTO latency(message_id, marks, source, updated_at) VALUES(?,?,?,?) ON CONFLICT(message_id) DO UPDATE "
                       "SET marks=excluded.marks, source=COALESCE(latency.source, excluded.source), updated_at=excluded.updated_at",
                       (message_id, json.dumps(cur), source, time.time()))
        if message_id % 100 == 0:
            self.c.execute("DELETE FROM latency WHERE message_id < ?", (message_id - LATENCY_KEEP * 4,))

    # -- events ------------------------------------------------------------
    def events_after(self, after_id, limit=500):
        return self.all("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", after_id, limit)

    def task_events(self, task_id, after_id=0, limit=1000):
        return self.all("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                        task_id, after_id, limit)
