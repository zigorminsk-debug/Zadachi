"""SQLite storage (stdlib only).

The database is portable: zadachi.db lives next to Zadachi.exe (or next
to Zadachi.py in dev mode), so replacing/reinstalling the program file
picks the existing database up again. A database created by older
versions in %APPDATA%\\Zadachi is copied/merged automatically on the
first run, so no tasks are lost by the move. If the program folder is
not writable (e.g. Program Files), the %APPDATA% location is used.

Tables
------
calls       -- every incoming/outgoing call event received from the phone
tasks       -- user tasks/requests with optional reminder time
contacts    -- learned name<->phone pairs (used for suggestions)
Task/title and name suggestions are served from accumulated history
(tasks + calls + contacts) so that typing offers previous values.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    name TEXT DEFAULT '',
    status TEXT DEFAULT 'incoming',      -- incoming / ringing / answered / missed / ended / outgoing
    note TEXT DEFAULT '',
    device TEXT DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    phone TEXT DEFAULT '',
    name TEXT DEFAULT '',
    remind_at INTEGER DEFAULT 0,          -- unix ts, 0 = no reminder
    done INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    call_id INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS contacts (
    phone TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    seen INTEGER DEFAULT 1,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_phone ON calls(phone);
CREATE INDEX IF NOT EXISTS idx_tasks_remind ON tasks(remind_at, done);
"""


def portable_db_path() -> str:
    """zadachi.db next to the program file (exe when frozen, Zadachi.py in dev)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "zadachi.db")


def legacy_db_path() -> str:
    """Where versions <= the %APPDATA% layout kept the database."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Zadachi", "zadachi.db")


def _dir_writable(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".zadachi-write-test-", dir=folder)
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False


def _row_counts(conn) -> dict:
    counts = {}
    for table in ("calls", "tasks", "contacts"):
        try:
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = 0
    return counts


def _merge_legacy_if_empty(portable_path: str, legacy_path: str):
    """Import the old %APPDATA% DB if the portable one has no data yet."""
    conn = sqlite3.connect(portable_path)
    try:
        conn.executescript(SCHEMA)
        if sum(_row_counts(conn).values()):
            return
        conn.execute("ATTACH DATABASE ? AS legacy", (legacy_path,))
        try:
            if not sum(_row_counts(conn).values()):
                # counts above were read before ATTACH; read legacy counts
                legacy_total = 0
                for table in ("calls", "tasks", "contacts"):
                    try:
                        legacy_total += conn.execute(
                            f"SELECT COUNT(*) FROM legacy.{table}").fetchone()[0]
                    except sqlite3.Error:
                        pass
                if legacy_total:
                    conn.execute("INSERT INTO calls SELECT * FROM legacy.calls")
                    conn.execute("INSERT INTO tasks SELECT * FROM legacy.tasks")
                    conn.execute(
                        "INSERT OR IGNORE INTO contacts SELECT * FROM legacy.contacts")
                    conn.commit()
        finally:
            conn.execute("DETACH DATABASE legacy")
    finally:
        conn.close()


def resolve_db_path(portable_path: str | None = None,
                    legacy_path: str | None = None) -> str:
    """Prefer the portable DB next to the program; migrate the legacy one.

    Falls back to the legacy %APPDATA% path when the program folder is
    not writable.
    """
    portable = portable_path or portable_db_path()
    legacy = legacy_path or legacy_db_path()
    if os.path.normcase(os.path.abspath(portable)) != os.path.normcase(
            os.path.abspath(legacy)):
        folder = os.path.dirname(os.path.abspath(portable))
        if _dir_writable(folder):
            try:
                if os.path.isfile(legacy):
                    if not os.path.exists(portable):
                        shutil.copy2(legacy, portable)
                    elif os.path.isfile(portable):
                        _merge_legacy_if_empty(portable, legacy)
                return portable
            except (OSError, sqlite3.Error):
                pass  # fall through to the legacy location
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    return legacy


def default_db_path() -> str:
    return resolve_db_path()


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------- calls ----------
    def add_call(self, phone, status="incoming", name="", note="", device=""):
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO calls(phone,name,status,note,device,created_at) VALUES(?,?,?,?,?,?)",
                (phone, name, status, note, device, now),
            )
            self._conn.commit()
            call_id = cur.lastrowid
        if name:
            self.learn_contact(phone, name)
        return call_id

    def list_calls(self, limit=300):
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM calls ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------- tasks ----------
    def add_task(self, title, phone="", name="", remind_at=0, call_id=0):
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks(title,phone,name,remind_at,done,created_at,call_id) VALUES(?,?,?,?,0,?,?)",
                (title, phone, name, int(remind_at or 0), now, int(call_id or 0)),
            )
            self._conn.commit()
            task_id = cur.lastrowid
        if name and phone:
            self.learn_contact(phone, name)
        return task_id

    def list_tasks(self, include_done=False, limit=500):
        q = "SELECT * FROM tasks"
        if not include_done:
            q += " WHERE done=0"
        q += " ORDER BY (remind_at=0), remind_at, id DESC LIMIT ?"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, (limit,))]

    def get_task(self, task_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def update_task(self, task_id, title, phone="", name="", remind_at=0,
                    done=None):
        """Edit an existing task (used by the double-click edit dialog)."""
        with self._lock:
            if done is None:
                self._conn.execute(
                    "UPDATE tasks SET title=?, phone=?, name=?, remind_at=? WHERE id=?",
                    (title, phone, name, int(remind_at or 0), task_id))
            else:
                self._conn.execute(
                    "UPDATE tasks SET title=?, phone=?, name=?, remind_at=?, done=? WHERE id=?",
                    (title, phone, name, int(remind_at or 0),
                     1 if done else 0, task_id))
            self._conn.commit()
        if name and phone:
            self.learn_contact(phone, name)

    def set_task_done(self, task_id, done=True):
        with self._lock:
            self._conn.execute("UPDATE tasks SET done=? WHERE id=?", (1 if done else 0, task_id))
            self._conn.commit()

    def snooze_task(self, task_id, seconds=600):
        """Move reminder into the future (default +10 min)."""
        new_ts = int(time.time()) + int(seconds)
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET remind_at=?, done=0 WHERE id=?", (new_ts, task_id))
            self._conn.commit()
        return new_ts

    def due_tasks(self, now=None):
        now = int(now or time.time())
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM tasks WHERE done=0 AND remind_at>0 AND remind_at<=? ORDER BY remind_at",
                (now,))]

    def delete_task(self, task_id):
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self._conn.commit()

    # ---------- contacts / suggestions ----------
    def learn_contact(self, phone, name):
        if not phone or not name:
            return
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO contacts(phone,name,seen,updated_at) VALUES(?,?,1,?)
                   ON CONFLICT(phone) DO UPDATE SET name=excluded.name,
                   seen=seen+1, updated_at=excluded.updated_at""",
                (phone, name, now),
            )
            self._conn.commit()

    def name_for_phone(self, phone):
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM contacts WHERE phone=?", (phone,)).fetchone()
            if row:
                return row["name"]
            row = self._conn.execute(
                "SELECT name FROM calls WHERE phone=? AND name<>'' ORDER BY id DESC LIMIT 1",
                (phone,)).fetchone()
            return row["name"] if row else ""

    def suggest_titles(self, prefix="", limit=10):
        """Previously entered task titles matching prefix, most used first."""
        return self._suggest(
            "SELECT title AS v, COUNT(*) c FROM tasks WHERE title LIKE ? GROUP BY title ORDER BY c DESC, MAX(id) DESC LIMIT ?",
            prefix, limit)

    def suggest_names(self, prefix="", limit=10):
        return self._suggest(
            "SELECT name AS v, COUNT(*) c FROM ("
            " SELECT name FROM tasks WHERE name LIKE ? UNION ALL"
            " SELECT name FROM calls WHERE name LIKE ? UNION ALL"
            " SELECT name FROM contacts WHERE name LIKE ?)"
            " WHERE name<>'' GROUP BY name ORDER BY c DESC LIMIT ?",
            prefix, limit, multi=True)

    def suggest_phones(self, prefix="", limit=10):
        return self._suggest(
            "SELECT phone AS v, COUNT(*) c FROM ("
            " SELECT phone FROM tasks WHERE phone LIKE ? UNION ALL"
            " SELECT phone FROM calls WHERE phone LIKE ?)"
            " WHERE phone<>'' GROUP BY phone ORDER BY c DESC, MAX(rowid) DESC LIMIT ?",
            prefix, limit, multi=True)

    def _suggest(self, sql, prefix, limit, multi=False):
        like = f"%{prefix}%"
        n_like = sql.count("?") - 1  # last placeholder is LIMIT
        params = tuple([like] * n_like + [limit])
        with self._lock:
            return [r["v"] for r in self._conn.execute(sql, params)]
