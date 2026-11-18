"""Interactive chat and live harness state for the terminal."""
from __future__ import annotations

import shlex
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import get_ident

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, RichLog, Static
from textual.worker import Worker

from . import config, dashboard, memory, report, scrape, skills, talker
from .orchestrator import Orchestrator
from .router import EFFORTS, ROUTES
from .store import Store

MODELS = ("Talker", "GLiDE", "Claude", "Codex")
APPROVAL_EVENTS = {"plan_ready", "needs_approval", "memory_proposal"}
PANELS = {
    "plan": "Plan",
    "route": "Route",
    "context": "Context",
    "checks": "Checks",
    "totals": "Totals",
}
HELP = """Plain text chats with Talker.
/ask <task>                  Make a plan for a coding task
/approve <id> · /deny <id>   Decide a pending approval
/status · /report            Show tasks, approvals, and usage
/memory · /skills            Show saved knowledge and available skills
/scrape <url> [key] [asc|desc]
/clear · /quit · /help
Ctrl+A / Ctrl+D decide the latest pending approval."""


def _plan_text(state: dict) -> str:
    planner = dashboard._data(state.get("plan")).get("planned_by")
    rounds = {}
    for key in ("runs", "decisions"):
        for row in state.get(key, []):
            task_id = row["task_id"]
            rounds[task_id] = max(rounds.get(task_id, 0), row["round"] + 1)
    tasks = state.get("tasks", [])
    if not planner and not tasks:
        return "No tasks yet"
    lines = [f"Planned by {planner}" if planner else "Planner pending"]
    for task in tasks:
        task_id = task["id"]
        if title := dashboard._data(task).get("title"):
            lines.append(title)
        lines.append(f"  {task_id} · {task['status']} · round {rounds.get(task_id, 0)}")
    return "\n".join(lines)


def _probability_bars(answer: dict, options) -> list[str]:
    values = answer.get("probabilities") or answer.get("probs") or {}
    if isinstance(values, list):
        values = dict(zip(options, values))
    if not isinstance(values, dict):
        return []
    aliases = {str(index): name for index, name in enumerate(options)}
    lines = []
    for name, value in values.items():
        if isinstance(value, (int, float)) and 0 <= value <= 1:
            fill = round(value * 5)
            bar = "━" * fill + "┄" * (5 - fill)
            lines.append(f"{aliases.get(str(name), name):<13} {bar} {value:>4.0%}")
    return lines


def _route_text(state: dict) -> str:
    row = state.get("decision")
    decision = dashboard._data(row)
    route = decision.get("route")
    if not route:
        return "No routing decision yet"
    router = decision.get("router") or {}
    source = route.get("source") or router.get("used") or "unknown"
    lines = [
        f"{route['cli']} {route['model']} · {route['effort']}",
        f"{row['task_id']} · round {row['round'] + 1} · {source}",
    ]
    if reason := route.get("reason"):
        lines.append(reason.removeprefix(f"{source}: "))
    answers = router.get("answers") or {}
    for key, options in (("route", ROUTES), ("effort", EFFORTS)):
        if bars := _probability_bars(answers.get(key) or {}, options):
            lines.extend([f"GLiDE {key}", *bars])
    return "\n".join(lines)


def _checks_text(state: dict) -> Text:
    check = state.get("check")
    run = None
    if check is not None:
        run = next((row for row in reversed(state.get("runs", []))
                    if (row["task_id"], row["round"]) == (check["task_id"], check["round"])), None)
    results = {**dashboard._data(run), **dashboard._data(check)}
    visible = results.get("visible_tests_passed", check["tests_passed"] if check is not None else None)
    hidden = results.get("hidden_tests_passed")
    text = Text()
    if check is not None:
        text.append(f"{check['task_id']} · round {check['round'] + 1}\n", style="dim")
    for name, result in (("Visible", visible), ("Hidden", hidden)):
        text.append(f"{name}  ")
        label, style = ("pending", "dim") if result is None else ("PASS", "green") if result else ("FAIL", "red")
        text.append(f"{label}\n", style=style)
    assumptions = results.get("assumptions") or []
    text.append(f"Assumptions  {len(assumptions)}")
    for assumption in assumptions:
        text.append(f"\n• {assumption}")
    return text


def _panel_contents(state: dict) -> dict[str, str | Text]:
    pack = dashboard._data(state.get("pack"))
    files = pack.get("why_lines") or pack.get("files") or []
    runs = state.get("runs", [])
    run_data = [dashboard._data(row) for row in runs]
    tokens = sum(report.tokens_of(data.get("usage")) for data in run_data)
    seconds = sum(float(data.get("duration_s") or 0) for data in run_data)
    return {
        "plan": _plan_text(state),
        "route": _route_text(state),
        "context": "\n".join(["Why these files", *files]) if files else "No context pack yet",
        "checks": _checks_text(state),
        "totals": f"Rounds {len(runs)} · Tokens {tokens:,}\nTime {seconds:.1f}s",
    }


def _scrape_text(args: str) -> str:
    usage = "Usage: /scrape <url> [key] [asc|desc]"
    try:
        words = shlex.split(args)
    except ValueError:
        return usage
    if not words or not words[0].lower().startswith(("http://", "https://")):
        return usage
    url, *key = words
    order = key.pop().lower() if key and key[-1].lower() in {"asc", "desc"} else "asc"
    rows = scrape.scrape(url, " ".join(key) or None, desc=order == "desc")
    if not rows:
        return "No rows found."
    preview = rows[:20]
    columns = list(dict.fromkeys(column for row in preview for column in row))
    table = report.table(columns, [[row.get(column, "") for column in columns] for row in preview])
    return f"{table}\nShowing {len(preview)} of {len(rows)} rows." if len(preview) < len(rows) else table


class BhimaApp(App):
    TITLE = "BHIMA"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+a", "approve_latest", "Approve latest", priority=True),
        Binding("ctrl+d", "deny_latest", "Deny latest", priority=True),
        Binding("ctrl+l", "clear", "Clear", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]
    CSS = """
    Header, Footer {
        background: $panel;
    }
    #models {
        height: auto;
        min-height: 1;
        padding: 0 2;
        background: $surface;
    }
    #workspace {
        height: 1fr;
    }
    #chat {
        width: 2fr;
        height: 1fr;
        min-width: 16;
    }
    #transcript {
        height: 1fr;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    #prompt {
        dock: bottom;
        border: tall $panel;
    }
    #prompt:focus {
        border: tall $accent;
    }
    #sidebar {
        width: 1fr;
        min-width: 30;
        max-width: 52;
        height: 1fr;
        padding: 0 1;
        background: $surface;
        border-left: solid $panel;
    }
    #details {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    .panel {
        height: auto;
        padding: 0 1;
        border-top: solid $panel;
        border-title-color: $accent;
        border-title-style: bold;
    }
    #approvals {
        dock: bottom;
        border-title-color: $warning;
    }
    """

    def __init__(self, backend, *, store: Store | None = None, db_path: Path | None = None,
                 ask_talker: Callable[[str], str | None] | None = None,
                 health: dashboard.HealthCache | None = None) -> None:
        super().__init__()
        self.backend = backend
        self.store = store
        self.db_path = db_path or (store.path if store is not None else config.DB_PATH)
        self.ask_talker = talker.ask if ask_talker is None else ask_talker
        self.health = dashboard.HealthCache() if health is None else health
        self._pending: dict[str, str] = {}
        self._deciding: set[str] = set()
        self._approval_revision = 0
        self._sidebar_worker: Worker | None = None
        self._panel_cache = _panel_contents({})
        self._health_cache: dict = {}
        self._ui_thread: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="◆", time_format="%H:%M:%S")
        yield Static(self._model_status({}), id="models", markup=False)
        with Horizontal(id="workspace"):
            with Vertical(id="chat"):
                yield RichLog(id="transcript", wrap=True, min_width=1, max_lines=4000)
                yield Input(placeholder="Chat with Bhima, or /ask <task>   (/help)", id="prompt")
            with Vertical(id="sidebar"):
                with VerticalScroll(id="details"):
                    for key, title in PANELS.items():
                        panel = Static(self._panel_cache[key], id=key, classes="panel", markup=False)
                        panel.border_title = title
                        yield panel
                approvals = Static("No pending approvals", id="approvals", classes="panel", markup=False)
                approvals.border_title = "Approvals waiting"
                yield approvals
        yield Footer()

    def on_mount(self) -> None:
        self._ui_thread = get_ident()
        self.backend.on_event(self._receive_event)
        self.query_one("#prompt", Input).focus()
        self._tick()
        self.set_interval(1, self._tick)

    @staticmethod
    def _model_status(snapshot: dict) -> Text:
        text = Text()
        for index, name in enumerate(MODELS):
            if index:
                text.append("   ")
            up, _ = snapshot.get(name, (None, ""))
            badge, style = ("○ checking", "dim") if up is None else ("● up", "green") if up else ("○ down", "red")
            text.append(f"{name} ")
            text.append(badge, style=style)
        return text

    def _tick(self) -> None:
        self.health.refresh()
        snapshot = self.health.snapshot()
        if snapshot != self._health_cache:
            self._health_cache = snapshot
            self.query_one("#models", Static).update(self._model_status(snapshot))
        if self._sidebar_worker is None or self._sidebar_worker.is_finished:
            self._sidebar_worker = self._refresh_sidebar(self._approval_revision)

    @work(thread=True)
    def _refresh_sidebar(self, revision: int) -> None:
        try:
            state = dashboard._read(self.db_path)
            approvals = self.store.pending_approvals() if self.store is not None else None
        except (sqlite3.Error, OSError):
            return
        self._deliver(self._update_sidebar, _panel_contents(state), approvals, revision)

    def _update_sidebar(self, contents: dict, approvals: list[dict] | None, revision: int) -> None:
        for key, value in contents.items():
            if value != self._panel_cache[key]:
                self.query_one(f"#{key}", Static).update(value)
        self._panel_cache = contents
        # Keep a newer event or approval decision when a poll was already in flight.
        if approvals is not None and revision == self._approval_revision:
            self._pending = {row["id"]: f"{row['kind']} → {row['target_id']}" for row in approvals}
            self._update_approvals()

    def _update_approvals(self) -> None:
        panel = self.query_one("#approvals", Static)
        count = len(self._pending)
        lines = [f"{aid}  {label}" for aid, label in list(self._pending.items())[-2:]]
        if count > len(lines):
            lines.insert(0, f"+{count - len(lines)} more · /status")
        content = "\n".join(lines) or "No pending approvals"
        if content != panel.content:
            panel.update(content)

    def _deliver(self, callback: Callable, *args) -> None:
        if not self.is_running:
            return
        if get_ident() == self._ui_thread:
            callback(*args)
        else:
            try:
                self.call_from_thread(callback, *args)
            except RuntimeError:
                if self.is_running:
                    raise

    def _receive_event(self, event: dict) -> None:
        self._deliver(self._show_event, dict(event))

    def _show_event(self, event: dict) -> None:
        kind = event["kind"]
        message = str(event.get("text", ""))
        if kind in APPROVAL_EVENTS and (aid := event.get("id")):
            self._approval_revision += 1
            self._pending.pop(aid, None)
            self._pending[aid] = kind.replace("_", " ")
            self._update_approvals()
            message += f"\nApproval {aid} · /approve {aid} or /deny {aid} · Ctrl+A / Ctrl+D for latest"
        self._write(f"HARNESS · {kind}", message, "yellow")

    def _write(self, prefix: str, message: str, style: str) -> None:
        entry = Text()
        entry.append(f"{prefix} › ", style=f"bold {style}")
        entry.append(message)
        self.query_one("#transcript", RichLog).write(entry)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        self._write("YOU", text, "cyan")
        if not text.startswith("/"):
            self._chat(text)
            return
        parts = text.split(maxsplit=1)
        self._command(parts[0].lower(), parts[1] if len(parts) == 2 else "")

    @work(thread=True)
    def _chat(self, text: str) -> None:
        try:
            reply = self.ask_talker(text)
        except Exception as exc:
            reply = f"Talker error: {exc}"
        if reply is None:
            reply = "Talker offline. Use /ask to start a task."
        self._deliver(self._write, "BHIMA", reply, "green")

    def _command(self, verb: str, args: str) -> None:
        if verb == "/ask":
            if args:
                self._run_command(lambda: f"Request {self.backend.submit(args)}")
            else:
                self._write("BHIMA", "Usage: /ask <task>", "green")
        elif verb in {"/approve", "/deny"}:
            if args and len(args.split()) == 1:
                self._decide(verb[1:], args)
            else:
                self._write("BHIMA", f"Usage: {verb} <id>", "green")
        elif verb == "/status":
            self._run_command(self.backend.status)
        elif verb == "/report":
            self._run_command(lambda: report.render(self.store) if self.store is not None else report.render())
        elif verb == "/memory":
            self._run_command(memory.snapshot)
        elif verb == "/skills":
            self._run_command(skills.index)
        elif verb == "/scrape":
            self._run_command(lambda: _scrape_text(args))
        elif verb == "/help":
            self._write("BHIMA", HELP, "green")
        elif verb == "/clear":
            self.action_clear()
        elif verb == "/quit":
            self.exit()
        else:
            self._write("BHIMA", f"Unknown command {verb}. Use /help.", "green")

    @work(thread=True)
    def _run_command(self, command: Callable, approval_id: str | None = None) -> None:
        try:
            result = command()
        except Exception as exc:
            self._deliver(self._command_done, f"{type(exc).__name__}: {exc}", approval_id, False)
        else:
            self._deliver(self._command_done, result, approval_id, True)

    def _command_done(self, result, approval_id: str | None, success: bool) -> None:
        if approval_id is not None:
            self._deciding.discard(approval_id)
            if success:
                self._approval_revision += 1
                self._pending.pop(approval_id, None)
                self._update_approvals()
        if result is not None:
            self._write("BHIMA", str(result), "green" if success else "red")

    def _decide(self, action: str, aid: str) -> None:
        if aid not in self._deciding:
            self._deciding.add(aid)
            decide = self.backend.approve if action == "approve" else self.backend.deny
            self._run_command(lambda: decide(aid), approval_id=aid)

    def _decide_latest(self, action: str) -> None:
        if self._pending:
            self._decide(action, next(reversed(self._pending)))
        else:
            self._write("BHIMA", "No pending approvals.", "green")

    def action_approve_latest(self) -> None:
        self._decide_latest("approve")

    def action_deny_latest(self) -> None:
        self._decide_latest("deny")

    def action_clear(self) -> None:
        self.query_one("#transcript", RichLog).clear()


def run() -> None:
    backend = Orchestrator()
    BhimaApp(backend, store=backend.store).run()
