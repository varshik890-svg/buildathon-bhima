"""Live, read-only view of the harness's SQLite state."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import textwrap
import threading
import time
import unicodedata
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import config, glide_client, talker
from .report import tokens_of
from .router import EFFORTS, ROUTES
from .workers.base import find_exe

_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_RESET = "\x1b[0m"


def _talker_health(probe: Callable[[], bool]) -> tuple[bool, str]:
    start = time.monotonic()
    up = probe()
    return up, f" ({(time.monotonic() - start) * 1000:.0f} ms)"


def _codex_health() -> tuple[bool, str]:
    via_wsl = config.codex_via_wsl()
    detail = f" (CODEX_VIA_WSL={str(via_wsl).lower()})"
    if not via_wsl:
        return bool(find_exe("codex")), detail
    wsl = find_exe("wsl.exe")
    if not wsl:
        return False, detail
    try:
        result = subprocess.run(
            [wsl, "-d", "Ubuntu", "--shell-type", "none", "--", "bash", "-lc",
             "source ~/.nvm/nvm.sh && command -v codex"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=3, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0, detail
    except (OSError, subprocess.TimeoutExpired):
        return False, detail


class HealthCache:
    """Refresh each probe independently and render from its last result."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, tuple[bool, str]] = {}
        self._due: dict[str, float] = {}

    def _refresh(self, name: str, probe: Callable[[], tuple[bool, str]]) -> None:
        try:
            result = probe()
        except Exception:
            result = False, ""
        with self._lock:
            self._results[name] = result
            self._due[name] = time.monotonic() + 5

    def refresh(self) -> None:
        talker_probe, glide_probe = talker.health, glide_client.health
        probes = {"Talker": lambda: _talker_health(talker_probe), "GLiDE": lambda: (glide_probe(), ""),
                  "Claude": lambda: (bool(find_exe("claude")), ""), "Codex": _codex_health}
        now = time.monotonic()
        with self._lock:
            for name, probe in probes.items():
                if now >= self._due.get(name, 0):
                    self._due[name] = float("inf")
                    threading.Thread(target=self._refresh, args=(name, probe), daemon=True).start()

    def snapshot(self) -> dict[str, tuple[bool, str]]:
        with self._lock:
            return self._results.copy()


_health = HealthCache()


def _data(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {}
    try:
        value = json.loads(row["data"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read(db_path: Path) -> dict:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        request = conn.execute("SELECT * FROM requests ORDER BY created DESC, rowid DESC LIMIT 1").fetchone()
        state = {
            "request": request,
            "decision": conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone(),
            "events": conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 8").fetchall(),
            "approvals": conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0],
        }
        if request is None:
            return state
        args = (request["id"],)
        state["plan"] = conn.execute(
            "SELECT * FROM plans WHERE request_id=? ORDER BY created DESC, rowid DESC LIMIT 1", args
        ).fetchone()
        tasks = conn.execute("SELECT * FROM tasks WHERE request_id=? ORDER BY created, rowid", args).fetchall()
        state["tasks"] = tasks
        for table in ("runs", "decisions"):
            state[table] = conn.execute(
                f"SELECT {table}.* FROM {table} JOIN tasks ON tasks.id={table}.task_id "
                f"WHERE tasks.request_id=? ORDER BY {table}.id", args
            ).fetchall()
        if tasks:
            latest = max(enumerate(tasks), key=lambda pair: (pair[1]["updated"], pair[0]))[1]
            args = (latest["id"],)
            state["pack"] = conn.execute(
                "SELECT * FROM events WHERE kind='context_pack' AND ref=? ORDER BY id DESC LIMIT 1", args
            ).fetchone()
            state["check"] = conn.execute(
                "SELECT * FROM checks WHERE task_id=? ORDER BY id DESC LIMIT 1", args
            ).fetchone()
        return state


def _route_text(route) -> str:
    if not route:
        return "pending"
    return f"{route['cli']} {route['model']}/{route['effort']}"


def _probabilities(answer: dict, options) -> str:
    values = answer.get("probabilities") or answer.get("probs") or {}
    if isinstance(values, list):
        values = dict(zip(options, values))
    if not isinstance(values, dict):
        return ""
    aliases = {str(index): name for index, name in enumerate(options)}
    return " | ".join(f"{aliases.get(str(name), name)} {value:.0%}"
                      for name, value in values.items() if isinstance(value, (int, float)))


def _columns(value: str, width: int) -> tuple[str, int]:
    # Stored request and event text can contain terminal controls or wide characters.
    value = " ".join(value.split())
    chars = []
    used = 0
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("C"):
            continue
        size = 0 if category in {"Mn", "Me"} else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + size > width:
            break
        chars.append(char)
        used += size
    return "".join(chars), used


def render_frame(width: int | None = None, *, db_path: Path | None = None,
                 color: bool = False) -> str:
    """Render one frame, with no cursor movement or synchronous health probes."""
    _health.refresh()
    try:
        state = _read(db_path or config.DB_PATH)
    except (sqlite3.Error, OSError):
        state = {}
    health = _health.snapshot()
    if width is None:
        width = shutil.get_terminal_size((100, 24)).columns
    lines: list[str] = []

    def styled(text: str, tone: str) -> str:
        return f"{tone}{text}{_RESET}" if color and tone else text

    def add(text: str, tone: str = "") -> None:
        lines.append(styled(_columns(text, width)[0], tone))

    add(f"BHIMA HARNESS  {datetime.now():%H:%M:%S}", _YELLOW)
    add("Models", _DIM)
    for group in (("Talker", "GLiDE"), ("Claude", "Codex")):
        parts, remaining = [], width
        for name in group:
            up, detail = health.get(name, (None, ""))
            badge = "○ checking" if up is None else "● up" if up else "○ down"
            part, used = _columns(f"{name} {badge}{detail}", remaining)
            parts.append(styled(part, _DIM if up is None else _GREEN if up else _RED))
            remaining -= used + 2
            if remaining <= 0:
                break
        lines.append("  ".join(parts))

    request = state.get("request")
    tasks, runs, decisions = (state.get(key, []) for key in ("tasks", "runs", "decisions"))
    add(f"Latest request: {request['text'] if request else 'no requests yet'}")
    if planner := _data(state.get("plan")).get("planned_by"):
        add(f"planned by: {planner}", _DIM)

    add("Tasks", _DIM)
    latest_runs = {run["task_id"]: run for run in runs}
    latest_decisions = {decision["task_id"]: decision for decision in decisions}
    if not tasks:
        add("No tasks yet", _DIM)
    for task in tasks:
        decision = latest_decisions.get(task["id"])
        run = latest_runs.get(task["id"])
        row = max((row for row in (run, decision) if row), key=lambda row: row["round"], default=None)
        route_data = _data(decision) if decision and decision["round"] == row["round"] else {}
        round_number = row["round"] + 1 if row else 0
        max_rounds = route_data.get("max_rounds", max(config.MAX_RETRIES + 2, round_number))
        route = _route_text(route_data.get("route") or run)
        status = task["status"]
        tone = (_RED if status in {"failed", "merge_failed", "merge_conflict", "denied"}
                else _GREEN if status in {"done", "merged"} else _YELLOW)
        title = _data(task).get("title")
        summary = f"{task['id']} [{status}] round {round_number}/{max_rounds} | {route}"
        add(f"{summary} | {title}" if title else summary, tone)

    last_decision = state.get("decision")
    decision = _data(last_decision)
    route = decision.get("route") or {}
    origin = f" | {last_decision['task_id']} round {last_decision['round'] + 1}" if last_decision else ""
    add(f"Route (why): {_route_text(route)}{origin}")
    if route:
        source = route.get("source") or (decision.get("router") or {}).get("used")
        reason = route.get("reason")
        if source or reason:
            add(f"{source or 'unknown'} ({reason.removeprefix(f'{source}: ')})" if reason else source, _DIM)
        answers = (decision.get("router") or {}).get("answers") or {}
        for key, label, options in (("route", "GLiDE route", ROUTES), ("effort", "GLiDE effort", EFFORTS)):
            if probs := _probabilities(answers.get(key) or {}, options):
                for line in textwrap.wrap(f"{label}: {probs}", width=max(width, 1), break_long_words=False,
                                          break_on_hyphens=False):
                    add(line)

    pack = _data(state.get("pack"))
    add("Context pack", _DIM)
    for reason in pack.get("why_lines") or pack.get("files") or ["No context pack recorded"]:
        add(reason, _DIM)

    check = state.get("check")
    data = _data(check)
    if check is not None:
        run = next((r for r in reversed(runs)
                    if (r["task_id"], r["round"]) == (check["task_id"], check["round"])), None)
        results = {**_data(run), **data}
        visible = results.get("visible_tests_passed", check["tests_passed"])
        hidden = results.get("hidden_tests_passed")
        assumptions = results.get("assumptions") or []
        add(f"Checks ({check['task_id']}, round {check['round'] + 1})", _DIM)
    else:
        visible = hidden = None
        assumptions = []
        add("Checks", _DIM)
    for label, result in (("Visible tests", visible), ("Hidden acceptance tests", hidden)):
        status, tone = ("pending", _DIM) if result is None else ("PASS", _GREEN) if result else ("FAIL", _RED)
        add(f"{label}: {status}", tone)
    add(f"Assumptions: {len(assumptions)}", _YELLOW if assumptions else _DIM)

    run_data = [_data(run) for run in runs]
    tokens = sum(tokens_of(data.get("usage")) for data in run_data)
    seconds = sum(float(data.get("duration_s") or 0) for data in run_data)
    rounds = f"{len(runs)} round" + ("s" if len(runs) != 1 else "")
    add(f"Totals: {rounds} | {tokens} tokens | {seconds:.1f} seconds "
        f"| approvals waiting: {state.get('approvals', 0)}")

    add("Last 8 events", _DIM)
    for event in state.get("events", []):
        when = datetime.fromtimestamp(event["created"]).strftime("%H:%M:%S")
        add(f"{when} [{event['kind']}] {event['message']}", _DIM)
    return "\n".join(lines)


def run(*, once: bool = False, interval: float = 1.0) -> None:
    if once:
        print(render_frame())
        return
    if os.name == "nt":
        os.system("")  # Enable virtual-terminal processing in Windows consoles.
    try:
        print("\x1b[?25l", end="", flush=True)
        while True:
            # Leave the last line at its current cursor position to avoid scrolling.
            print("\x1b[2J\x1b[H" + render_frame(color=True), end="", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        print(_RESET + "\x1b[?25h", flush=True)
