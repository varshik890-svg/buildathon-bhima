"""The Bhima flow (state machine).

submit -> plan -> [human approves plan] -> for each task:
    route -> context pack -> worker -> commit -> checks
    retry up to MAX_RETRIES on the same CLI with the failure fed back,
    then the other CLI once, then ask the human
-> [human approves merge of the exact SHA] -> merge --no-ff -> memory.propose
-> [human approves memory entries] -> memory.apply

All state lives in SQLite (store.py), so `approve <id>` works from a fresh
process (CLI) as well as from the long-running Telegram bot.
Events go to every `on_event(callback)` subscriber as dicts
{"kind", "id", "text", "ref"}; kinds: received, plan_ready, task_started,
round_done, task_done, needs_approval, merged, memory_proposal, error, info.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from . import checks, config, gitops
from .models import ContextPack, Plan, Route, RunResult, Task, plan_from_dict, task_from_dict, to_dict
from .store import Store, new_id

EventCallback = Callable[[dict], None]

OTHER_CLI = {"claude": "codex", "codex": "claude"}
FALLBACK_MODEL = {"claude": "sonnet", "codex": "gpt-5.5"}


# --------------------------------------------------------------- defaults --
# Each dependency is resolved lazily so a missing/broken module owned by
# another builder only degrades that step instead of breaking the harness.

def _default_planner(request: str, project_dir: Path, request_id: str) -> Plan:
    try:
        from . import planner
        return planner.make_plan(request, project_dir, request_id=request_id)
    except Exception as exc:  # stub fallback: one task straight from the request
        return Plan(id=new_id("p"), request_id=request_id,
                    tasks=[Task(id=new_id("t"), request_id=request_id, title=request[:60], detail=request,
                                kind="code", files_hint=[], acceptance=["all tests pass"])],
                    test_command=config.DEFAULT_TEST_COMMAND, summary=f"single task (planner unavailable: {exc})")


def _default_router(task: Task, history: list[RunResult]) -> Route:
    try:
        from . import router
        return router.route(task, history)
    except Exception as exc:
        effort = "medium" if not history else "high"
        return Route(cli="claude", model="sonnet", effort=effort, needs_plan=False, confidence=1.0,
                     source="rules", reason=f"stub route (router unavailable: {exc})")


def _default_context(task: Task, project_dir: Path, budget: int, history: list[RunResult]) -> ContextPack:
    try:
        from . import context
        try:
            return context.build_pack(task, project_dir, budget, history=history)
        except TypeError:
            return context.build_pack(task, project_dir, budget)
    except Exception as exc:
        text = f"(context pack unavailable: {exc})"
        return ContextPack(task_id=task.id, text=text, files=list(task.files_hint), tokens_est=len(text) // 4)


def _default_worker(name: str):
    from .workers import get_worker
    return get_worker(name)


class _DefaultMemory:
    def propose(self, run: RunResult) -> list[str]:
        try:
            from . import memory
            return list(memory.propose(run) or [])
        except Exception:
            return []

    def apply(self, entries: list[str]) -> None:
        from . import memory
        memory.apply(entries)


def _last_decision() -> dict | None:
    try:
        from . import router
        return getattr(router, "last_decision", None)
    except Exception:
        return None


def tokens_of(usage: dict) -> int:
    u = usage or {}
    return int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)) + \
        int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))


# ----------------------------------------------------------- orchestrator --

class Orchestrator:
    def __init__(self, store: Store | None = None, workspace: Path | None = None, background: bool = True,
                 planner=None, router=None, context=None, worker_factory=None, memory=None,
                 template: Path | None = None, max_retries: int = config.MAX_RETRIES):
        self.store = store or Store()
        self.workspace = Path(workspace) if workspace else gitops.workspace_path()
        self.template = template
        self.background = background
        self.plan_fn = planner or _default_planner
        self.route_fn = router or _default_router
        self.context_fn = context or _default_context
        self.worker_factory = worker_factory or _default_worker
        self.memory = memory or _DefaultMemory()
        self.max_retries = max_retries
        self._callbacks: list[EventCallback] = []
        self._lock = threading.RLock()  # one workspace -> one git operation/run at a time
        self._threads: list[threading.Thread] = []

    # ---------------------------------------------------------- events --
    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    def emit(self, kind: str, id_: str, text: str, ref: str = "", **data) -> None:
        ev = {"kind": kind, "id": id_, "text": text, "ref": ref or id_, **data}
        try:
            self.store.add_event(kind, ev["ref"], text, data)
        except Exception:
            pass
        for cb in list(self._callbacks):
            try:
                cb(ev)
            except Exception:
                traceback.print_exc()

    def _spawn(self, fn, *args) -> None:
        if not self.background:
            fn(*args)
            return
        t = threading.Thread(target=self._guard, args=(fn, *args), daemon=True)
        self._threads.append(t)
        t.start()

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:
            traceback.print_exc()
            self.emit("error", "", f"{type(exc).__name__}: {exc}")

    def wait(self, timeout: float | None = None) -> None:
        """Block until background work finishes (tests / CLI)."""
        deadline = None if timeout is None else time.time() + timeout
        while True:
            alive = [t for t in self._threads if t.is_alive()]
            if not alive:
                return
            for t in alive:
                t.join(None if deadline is None else max(0.0, deadline - time.time()))
            if deadline is not None and time.time() >= deadline:
                return

    def ensure_workspace(self, reset: bool = False) -> Path:
        with self._lock:
            self.workspace = gitops.create_workspace(self.workspace.name, template=self.template,
                                                     reset=reset, base_dir=self.workspace.parent)
        return self.workspace

    # ---------------------------------------------------------- submit --
    def submit(self, request: str) -> str:
        rid = self.store.add_request(request)
        self.emit("received", rid, f"Request received: {request}")
        self._spawn(self._plan, rid, request)
        return rid

    def submit_plan(self, plan: Plan, auto_approve: bool = False) -> str:
        """Use a ready-made plan (e.g. a seeded demo task), skipping the planner."""
        rid = self.store.add_request(plan.summary or plan.tasks[0].title, rid=plan.request_id or None)
        plan.request_id = rid
        for t in plan.tasks:
            t.request_id = rid
        self._save_plan(rid, plan)
        if auto_approve:
            appr = self.store.find_approval(plan.id)
            self.approve(appr["id"])
        return rid

    def _plan(self, rid: str, request: str) -> None:
        self.ensure_workspace()
        self.store.set_request_status(rid, "planning")
        plan = self.plan_fn(request, self.workspace, rid)
        plan.request_id = rid
        self._save_plan(rid, plan)

    def _save_plan(self, rid: str, plan: Plan) -> None:
        plan.test_command = config.DEFAULT_TEST_COMMAND
        self.store.save_plan(plan.id, rid, to_dict(plan), status="pending")
        for t in plan.tasks:
            t.request_id = rid
            self.store.save_task(t.id, rid, plan.id, to_dict(t), status="planned")
        aid = self.store.add_approval("plan", plan.id, plan.summary)
        self.store.set_request_status(rid, "awaiting_plan_approval")
        lines = [f"Plan {plan.id}: {plan.summary}", f"Tests: {plan.test_command}"]
        lines += [f"  {i + 1}. [{t.kind}] {t.id}: {t.title}" for i, t in enumerate(plan.tasks)]
        lines.append(f"Approve with: approve {aid}")
        self.emit("plan_ready", aid, "\n".join(lines), ref=rid, plan_id=plan.id)

    # ------------------------------------------------------ approvals --
    def _resolve_approval(self, target_id: str) -> dict | None:
        target_id = str(target_id).strip()
        appr = self.store.find_approval(target_id)
        if appr:
            return appr
        # request id -> its newest pending approval
        for a in reversed(self.store.pending_approvals()):
            plan = self.store.get_plan(a["target_id"])
            task = self.store.get_task(a["target_id"])
            if (plan and plan["request_id"] == target_id) or (task and task["request_id"] == target_id):
                return a
        return None

    def approve(self, target_id: str) -> str:
        appr = self._resolve_approval(target_id)
        if not appr:
            return self._approval_unavailable(target_id)
        if not self.store.decide_approval(appr["id"], "approved"):
            return f"Approval {appr['id']} already decided."
        kind = appr["kind"]
        if kind == "plan":
            self.store.set_plan_status(appr["target_id"], "approved")
            self._spawn(self._run_plan, appr["target_id"])
            return f"Plan {appr['target_id']} approved."
        if kind == "merge":
            # Telegram invokes approve on a worker thread, so this result can
            # include the actual merge outcome without blocking its event loop.
            return self._merge(appr["target_id"], appr["detail"])
        if kind == "memory":
            entries = json.loads(appr["detail"] or "[]")
            try:
                self.memory.apply(entries)
                msg = f"Saved {len(entries)} memory entr{'y' if len(entries) == 1 else 'ies'}."
            except Exception as exc:
                msg = f"Memory not saved: {exc}"
            self.emit("info", appr["id"], msg)
            return msg
        if kind == "retry":
            self._spawn(self._run_task_by_id, appr["target_id"], appr["detail"])
            return f"Retry approved for task {appr['target_id']}."
        return f"Approved {appr['id']}."

    def deny(self, target_id: str) -> str:
        appr = self._resolve_approval(target_id)
        if not appr:
            return self._approval_unavailable(target_id)
        if not self.store.decide_approval(appr["id"], "denied"):
            return f"Approval {appr['id']} already decided."
        kind, tid = appr["kind"], appr["target_id"]
        if kind == "plan":
            self.store.set_plan_status(tid, "denied")
            plan = self.store.get_plan(tid)
            if plan:
                self.store.set_request_status(plan["request_id"], "denied")
                for t in self.store.tasks_for_request(plan["request_id"]):
                    self.store.update_task(t["id"], status="cancelled")
        elif kind in ("merge", "retry"):
            self.store.update_task(tid, status="denied")
            self._refresh_request(self.store.get_task(tid)["request_id"])
        msg = f"Denied {kind} {tid}."
        self.emit("info", appr["id"], msg)
        return msg

    def _approval_unavailable(self, target_id: str) -> str:
        aid = str(target_id).strip()
        if self.store.get_approval(aid):
            return f"Approval {aid} already decided."
        return f"Nothing pending for {target_id}."

    def _refresh_request(self, request_id: str) -> None:
        with self._lock:
            statuses = {t["status"] for t in self.store.tasks_for_request(request_id)}
            if not statuses:
                return
            terminal = {"merged", "denied", "rejected"}
            if statuses <= terminal:
                status = "done"
            elif statuses <= terminal | {"done"}:
                status = "answered"
            elif statuses & {"planned", "running"}:
                status = "running"
            elif statuses & {"merge_conflict", "merge_failed", "failed"}:
                status = "awaiting_retry"
            else:
                status = "awaiting_merge"
            self.store.set_request_status(request_id, status)

    # ------------------------------------------------------------ runs --
    def _run_plan(self, plan_id: str) -> None:
        row = self.store.get_plan(plan_id)
        plan = plan_from_dict(row["data"])
        self.ensure_workspace()
        self.store.set_request_status(plan.request_id, "running")
        for task in plan.tasks:
            self.run_task(task)
        self._refresh_request(plan.request_id)

    def _run_task_by_id(self, task_id: str, detail: str) -> None:
        row = self.store.get_task(task_id)
        self.ensure_workspace()
        restart = row["status"] in {"merge_conflict", "merge_failed"}
        self.store.set_request_status(row["request_id"], "running")
        self.run_task(task_from_dict(row["data"]), extra_round=True, merge_feedback=detail if restart else "")
        self._refresh_request(row["request_id"])

    def _history(self, task_id: str) -> list[RunResult]:
        keys = set(RunResult.__dataclass_fields__)
        history = []
        for row in self.store.runs_for_task(task_id):
            data = {k: v for k, v in row["data"].items() if k in keys}
            data.update(ok=bool(row["data"].get("passed", row["ok"])), model=row["model"])
            history.append(RunResult(**data))
        return history

    def _route_for_round(self, task: Task, history: list[RunResult], first_cli: str | None,
                         switch: bool) -> Route:
        route = self.route_fn(task, history)
        if first_cli is None:
            return route
        want = OTHER_CLI.get(first_cli, "claude") if switch else first_cli
        if route.cli == want:
            return route
        model = FALLBACK_MODEL.get(want, "sonnet")
        if want == "codex":
            try:
                from . import router
                model = router.codex_model() or model
            except Exception:
                pass
        why = f"switched to {want} after {first_cli} failed" if switch else f"pinned to {want} for retry"
        return Route(cli=want, model=model, effort=route.effort, needs_plan=route.needs_plan,
                     confidence=route.confidence, source=route.source, reason=f"{route.reason} | {why}")

    def run_task(self, task: Task, extra_round: bool = False, merge_feedback: str = "") -> dict:
        """Run one task through route -> pack -> worker -> commit -> checks with retries."""
        with self._lock:
            return self._run_task_locked(task, extra_round, merge_feedback)

    def _run_task_locked(self, task: Task, extra_round: bool, merge_feedback: str) -> dict:
        ws = self.workspace
        test_command = config.DEFAULT_TEST_COMMAND
        history = self._history(task.id)
        start_round = len(history)
        self.store.update_task(task.id, status="running")
        if gitops.has_changes(ws):
            gitops.discard_changes(ws)
        task_row = self.store.get_task(task.id)
        branch = task_row["branch"] if task_row else None
        if merge_feedback:
            branch = gitops.branch_name(f"{task.id}-retry-{start_round}")
        branch = gitops.start_task_branch(ws, task.id, branch=branch)
        self.store.update_task(task.id, branch=branch)
        self.emit("task_started", task.id, f"Task {task.id}: {task.title} (branch {branch})", ref=task.request_id)

        # schedule: rounds 0..max_retries on first CLI, then one on the other CLI
        schedule = ["first"] * (1 + self.max_retries) + ["switch"]
        if extra_round:
            schedule = ["first"]
        first_cli = history[0].cli if history else None
        feedback = ""
        if history and extra_round:
            last_checks = self.store.checks_for_task(task.id)
            feedback = last_checks[-1]["data"].get("output", "")[-2000:] if last_checks else ""
        if merge_feedback:
            feedback = f"The previous candidate could not be merged. Reapply the task on current main.\n{merge_feedback}"
        sha, last_run, last_check = None, None, None
        passed = False
        skipped_switch = False
        for i, step in enumerate(schedule):
            rnd = start_round + i
            route = self._route_for_round(task, history, first_cli, switch=(step == "switch"))
            if first_cli is None:
                first_cli = route.cli
            decision = _last_decision() if self.route_fn is _default_router else None
            self.store.add_decision(task.id, rnd, {"route": to_dict(route), "router": decision})
            pack = self.context_fn(task, ws, config.CONTEXT_BUDGET_TOKENS, history)
            worker = self.worker_factory(route.cli)
            if hasattr(worker, "test_command"):
                worker.test_command = test_command
            run = worker.run(task, pack, route, ws, rnd, feedback=feedback)
            run.model = route.model
            if step == "switch" and not run.ok and run.error and "not installed" in run.error:
                skipped_switch = True
                self.store.add_run(task.id, rnd, route.cli, route.model, route.effort, False, to_dict(run))
                self.emit("round_done", task.id, f"Round {rnd}: {route.cli} unavailable ({run.error})",
                          ref=task.request_id)
                break
            new_sha = gitops.commit_all(ws, f"bhima {task.id} round {rnd} ({route.cli} {route.model}/{route.effort})"
                                            f": {task.title}")
            sha = new_sha or sha
            if task.kind == "question" and not new_sha:
                check = checks.CheckResult(task.id, True, "(question: no tests run)", run.assumptions)
            else:
                check = checks.run_checks(task, ws, test_command, run.report)
            visible_passed, hidden_passed = check.tests_passed, None
            if run.ok and visible_passed and new_sha:
                hidden_passed, _ = checks.verify_hidden_commit(task.id, ws, new_sha)
                if hidden_passed is False:
                    check.tests_passed = False
                    check.output += "\nSeeded hidden acceptance test failed for the candidate commit."
            if not check.assumptions:
                check.assumptions = list(run.assumptions)
            changed = bool(new_sha) or task.kind == "question"
            passed = run.ok and check.tests_passed and changed
            cli_ok = run.ok
            run.ok = passed
            if not changed and not run.error:
                run.error = "No file changes."
            run_data = to_dict(run)
            run_data.update({"cli_ok": cli_ok, "effort": route.effort, "route_source": route.source,
                             "route_reason": route.reason, "pack_tokens": pack.tokens_est, "sha": new_sha,
                             "tests_passed": check.tests_passed, "visible_tests_passed": visible_passed,
                             "hidden_tests_passed": hidden_passed, "passed": passed})
            self.store.add_run(task.id, rnd, route.cli, route.model, route.effort, passed, run_data)
            self.store.add_check(task.id, rnd, check.tests_passed, to_dict(check))
            history.append(run)
            last_run, last_check = run, check
            self.emit("round_done", task.id,
                      f"Round {rnd}: {route.cli} {route.model}/{route.effort} -> "
                      f"{'PASS' if passed else 'FAIL'} ({run.duration_s:.0f}s, {tokens_of(run.usage)} tok)",
                      ref=task.request_id)
            if passed:
                break
            reasons = []
            if not changed:
                reasons.append("You made no file changes. The task requires code changes.")
            feedback = checks.failure_feedback(check, run.error) + ("\n" + "\n".join(reasons) if reasons else "")
        rounds = len(history) - start_round
        if last_run is not None and passed:
            sha = new_sha
            if task.kind == "question" and not sha:
                self.store.update_task(task.id, status="done")
                self.emit("task_done", task.id, f"Answer:\n{last_run.report}", ref=task.request_id)
                self._propose_memory(last_run)
                return {"task_id": task.id, "passed": True, "sha": None, "rounds": rounds}
            self.store.update_task(task.id, status="awaiting_merge", sha=sha)
            aid = self.store.add_approval("merge", task.id, sha)
            stat = gitops.diff_stat(ws, sha)
            assumptions = "\n".join(f"  - {a}" for a in last_check.assumptions) or "  - none listed"
            self.emit("task_done", task.id, f"Task {task.id} passed checks in {rounds} round(s).",
                      ref=task.request_id)
            self.emit("needs_approval", aid,
                      f"Merge task {task.id} ({task.title})\ncommit {sha[:10]} on {branch}\n"
                      f"{stat}\nAssumptions to check:\n{assumptions}\nApprove with: approve {aid}",
                      ref=task.request_id, task_id=task.id, sha=sha)
            gitops.git(ws, "checkout", "-q", gitops.MAIN)
            return {"task_id": task.id, "passed": True, "sha": sha, "rounds": rounds}

        self.store.update_task(task.id, status="failed", sha=sha)
        aid = self.store.add_approval("retry", task.id, sha or "")
        why = last_run.error if last_run else "no run"
        if last_check and not last_check.tests_passed and last_check.output:
            why = last_check.output.splitlines()[-1]
        note = " (other CLI not installed)" if skipped_switch else ""
        self.emit("needs_approval", aid,
                  f"Task {task.id} still failing after {rounds} round(s){note}: {why}\n"
                  f"Approve to try one more round, deny to drop it: approve {aid}",
                  ref=task.request_id, task_id=task.id)
        if gitops.has_changes(ws):
            gitops.discard_changes(ws)
        gitops.git(ws, "checkout", "-q", gitops.MAIN)
        return {"task_id": task.id, "passed": False, "sha": sha, "rounds": rounds}

    # ----------------------------------------------------------- merge --
    def _merge(self, task_id: str, sha: str) -> str:
        with self._lock:
            task = self.store.get_task(task_id)
            try:
                ok, info = gitops.merge_sha(self.workspace, sha, f"bhima: merge task {task_id} ({sha[:10]})")
            except (gitops.GitError, OSError) as exc:
                ok, info = False, str(exc)
            if not ok:
                status = "merge_conflict" if "conflict" in info.lower() else "merge_failed"
                self.store.update_task(task_id, status=status)
                self._refresh_request(task["request_id"])
                aid = self.store.add_approval("retry", task_id, info)
                msg = f"Merge failed: {info}"
                self.emit("needs_approval", aid, f"{msg}\nApprove to retry the task on current main.",
                          ref=task["request_id"], task_id=task_id)
                return msg
            self.store.update_task(task_id, status="merged")
            self._refresh_request(task["request_id"])
        msg = f"Merged task {task_id} ({sha[:10]}) into main as {info[:10]}."
        self.emit("merged", task_id, msg)
        runs = self._history(task_id)
        if runs:
            self._propose_memory(runs[-1])
        return msg

    def _propose_memory(self, run: RunResult) -> None:
        entries = self.memory.propose(run)
        if not entries:
            return
        aid = self.store.add_approval("memory", run.task_id, json.dumps(entries))
        self.emit("memory_proposal", aid, "Remember this?\n" + "\n".join(f"- {e}" for e in entries)
                  + f"\nApprove with: approve {aid}", task_id=run.task_id)

    # ---------------------------------------------------------- status --
    def status(self) -> str:
        lines = []
        reqs = self.store.list_requests(limit=5)
        if not reqs:
            lines.append("No requests yet.")
        for r in reqs:
            lines.append(f"{r['id']} [{r['status']}] {r['text'][:70]}")
            for t in self.store.tasks_for_request(r["id"]):
                runs = self.store.runs_for_task(t["id"])
                last = runs[-1] if runs else None
                extra = f" last: {last['cli']} {last['model']}/{last['effort']}" if last else ""
                sha = f" sha {t['sha'][:10]}" if t.get("sha") else ""
                lines.append(f"  {t['id']} [{t['status']}] {t['data'].get('title', '')[:50]}"
                             f" rounds={len(runs)}{extra}{sha}")
        pend = self.store.pending_approvals()
        if pend:
            lines.append("Pending approvals:")
            for a in pend:
                lines.append(f"  {a['id']} {a['kind']} -> {a['target_id']}")
        return "\n".join(lines)


# ------------------------------------------------ module-level interface --
_instance: Orchestrator | None = None


def get_orchestrator(**kwargs) -> Orchestrator:
    global _instance
    if _instance is None or kwargs:
        _instance = Orchestrator(**kwargs)
    return _instance


def configure(**kwargs) -> Orchestrator:
    return get_orchestrator(**kwargs)


def submit(request: str) -> str:
    return get_orchestrator().submit(request)


def approve(target_id: str) -> str:
    return get_orchestrator().approve(target_id)


def deny(target_id: str) -> str:
    return get_orchestrator().deny(target_id)


def status() -> str:
    return get_orchestrator().status()


def on_event(callback: EventCallback) -> None:
    get_orchestrator().on_event(callback)
