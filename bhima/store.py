"""SQLite state store at .bhima/state.db.

Tables: requests, plans, tasks, runs, checks, approvals, decisions, events.
Rich objects are stored as JSON in a `data` column next to a few query columns.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY, text TEXT, status TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, request_id TEXT, data TEXT, status TEXT, created REAL);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, request_id TEXT, plan_id TEXT, data TEXT, status TEXT,
    sha TEXT, branch TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, round INTEGER, cli TEXT,
    model TEXT, effort TEXT, ok INTEGER, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, round INTEGER,
    tests_passed INTEGER, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, kind TEXT, target_id TEXT, status TEXT, detail TEXT,
    created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, round INTEGER, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref TEXT, message TEXT,
    data TEXT, created REAL);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # -- low level -------------------------------------------------------
    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -- requests --------------------------------------------------------
    def add_request(self, text: str, rid: str | None = None) -> str:
        rid = rid or new_id("r")
        now = time.time()
        self._exec("INSERT INTO requests VALUES (?,?,?,?,?)", (rid, text, "new", now, now))
        return rid

    def set_request_status(self, rid: str, status: str) -> None:
        self._exec("UPDATE requests SET status=?, updated=? WHERE id=?", (status, time.time(), rid))

    def get_request(self, rid: str) -> dict | None:
        return self._one("SELECT * FROM requests WHERE id=?", (rid,))

    def list_requests(self, limit: int = 20) -> list[dict]:
        return self._all("SELECT * FROM requests ORDER BY created DESC LIMIT ?", (limit,))

    # -- plans -----------------------------------------------------------
    def save_plan(self, plan_id: str, request_id: str, data: dict, status: str = "pending") -> None:
        self._exec("INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?)",
                   (plan_id, request_id, json.dumps(data), status, time.time()))

    def get_plan(self, plan_id: str) -> dict | None:
        row = self._one("SELECT * FROM plans WHERE id=?", (plan_id,))
        if row:
            row["data"] = json.loads(row["data"])
        return row

    def plan_for_request(self, request_id: str) -> dict | None:
        row = self._one("SELECT * FROM plans WHERE request_id=? ORDER BY created DESC", (request_id,))
        if row:
            row["data"] = json.loads(row["data"])
        return row

    def set_plan_status(self, plan_id: str, status: str) -> None:
        self._exec("UPDATE plans SET status=? WHERE id=?", (status, plan_id))

    # -- tasks -----------------------------------------------------------
    def save_task(self, task_id: str, request_id: str, plan_id: str, data: dict,
                  status: str = "pending") -> None:
        now = time.time()
        self._exec("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                   (task_id, request_id, plan_id, json.dumps(data), status, None, None, now, now))

    def update_task(self, task_id: str, **fields) -> None:
        allowed = {"status", "sha", "branch"}
        sets = [f"{k}=?" for k in fields if k in allowed]
        if not sets:
            return
        args = tuple(v for k, v in fields.items() if k in allowed)
        self._exec(f"UPDATE tasks SET {', '.join(sets)}, updated=? WHERE id=?",
                   args + (time.time(), task_id))

    def get_task(self, task_id: str) -> dict | None:
        row = self._one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row:
            row["data"] = json.loads(row["data"])
        return row

    def tasks_for_request(self, request_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM tasks WHERE request_id=? ORDER BY created", (request_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # -- runs / checks / decisions --------------------------------------
    def add_run(self, task_id: str, round_: int, cli: str, model: str, effort: str,
                ok: bool, data: dict) -> int:
        cur = self._exec("INSERT INTO runs (task_id, round, cli, model, effort, ok, data, created)"
                         " VALUES (?,?,?,?,?,?,?,?)",
                         (task_id, round_, cli, model, effort, int(ok), json.dumps(data), time.time()))
        return cur.lastrowid

    def runs_for_task(self, task_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM runs WHERE task_id=? ORDER BY id", (task_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def all_runs(self) -> list[dict]:
        rows = self._all("SELECT * FROM runs ORDER BY id")
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def add_check(self, task_id: str, round_: int, passed: bool, data: dict) -> int:
        cur = self._exec("INSERT INTO checks (task_id, round, tests_passed, data, created)"
                         " VALUES (?,?,?,?,?)",
                         (task_id, round_, int(passed), json.dumps(data), time.time()))
        return cur.lastrowid

    def checks_for_task(self, task_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM checks WHERE task_id=? ORDER BY id", (task_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def add_decision(self, task_id: str, round_: int, data: dict) -> int:
        cur = self._exec("INSERT INTO decisions (task_id, round, data, created) VALUES (?,?,?,?)",
                         (task_id, round_, json.dumps(data), time.time()))
        return cur.lastrowid

    def decisions_for_task(self, task_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM decisions WHERE task_id=? ORDER BY id", (task_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # -- approvals -------------------------------------------------------
    def add_approval(self, kind: str, target_id: str, detail: str = "") -> str:
        aid = new_id("a")
        self._exec("INSERT INTO approvals VALUES (?,?,?,?,?,?,?)",
                   (aid, kind, target_id, "pending", detail, time.time(), None))
        return aid

    def get_approval(self, aid: str) -> dict | None:
        return self._one("SELECT * FROM approvals WHERE id=?", (aid,))

    def pending_approvals(self) -> list[dict]:
        return self._all("SELECT * FROM approvals WHERE status='pending' ORDER BY created")

    def find_approval(self, target_id: str, status: str = "pending") -> dict | None:
        return self._one("SELECT * FROM approvals WHERE (id=? OR target_id=?) AND status=?"
                         " ORDER BY created DESC", (target_id, target_id, status))

    def decide_approval(self, aid: str, status: str) -> None:
        self._exec("UPDATE approvals SET status=?, decided=? WHERE id=?", (status, time.time(), aid))

    # -- events ----------------------------------------------------------
    def add_event(self, kind: str, ref: str, message: str, data: dict | None = None) -> int:
        cur = self._exec("INSERT INTO events (kind, ref, message, data, created) VALUES (?,?,?,?,?)",
                         (kind, ref, message, json.dumps(data or {}), time.time()))
        return cur.lastrowid

    def events(self, ref: str | None = None, limit: int = 50) -> list[dict]:
        if ref:
            return self._all("SELECT * FROM events WHERE ref=? ORDER BY id DESC LIMIT ?", (ref, limit))
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
