"""Core flow tests with fake planner/router/worker: no claude/codex calls."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bhima import checks, gitops, report
from bhima.models import ContextPack, Plan, Route, RunResult, Task
from bhima.orchestrator import Orchestrator
from bhima.store import Store
from bhima.workers import base as wbase
from bhima.workers import claude as wclaude
from bhima.workers import codex as wcodex

REPORT_OK = "## Summary:\nfixed\n## Files changed:\n- invoice/pricing.py\n## Tests:\npass\n" \
            "## Assumptions:\n- quantity is always an int\n- none of the callers rely on > 10\n"


def fix_boundary(ws: Path) -> None:
    p = ws / "invoice" / "pricing.py"
    p.write_text(p.read_text(encoding="utf-8").replace("quantity > BULK", "quantity >= BULK"), encoding="utf-8")


def break_tests(ws: Path) -> None:
    p = ws / "invoice" / "pricing.py"
    p.write_text(p.read_text(encoding="utf-8").replace("return round(total, 2)", "return 0.0"), encoding="utf-8")


class FakeWorker:
    """Applies `actions[cli][n]` on the n-th call for that CLI."""

    def __init__(self, cli: str, script: dict, calls: list):
        self.name = cli
        self.script = script
        self.calls = calls

    def run(self, task, pack, route, workdir, round, feedback=""):
        n = sum(1 for c in self.calls if c["cli"] == self.name)
        self.calls.append({"cli": self.name, "round": round, "feedback": feedback, "model": route.model})
        actions = self.script.get(self.name, [])
        action = actions[min(n, len(actions) - 1)] if actions else None
        if action == "missing":
            return RunResult(task.id, round, self.name, False, "", [], {}, 0.0, "", error=f"{self.name} not installed")
        if action == "fix":
            if (Path(workdir) / "invoice" / "pricing.py").read_text().count("return 0.0"):
                gitops.git(Path(workdir), "checkout", "HEAD~0", "--", ".")
                p = Path(workdir) / "invoice" / "pricing.py"
                p.write_text(p.read_text().replace("return 0.0", "return round(total, 2)"))
            fix_boundary(Path(workdir))
        elif action == "break":
            break_tests(Path(workdir))
        usage = {"input_tokens": 100, "output_tokens": 50}
        return RunResult(task.id, round, self.name, True, REPORT_OK, checks.parse_assumptions(REPORT_OK),
                         usage, 1.5, "", None)


def make_plan(rid="", n=1, kind="code"):
    tasks = [Task(id=f"t{i + 1}", request_id=rid, title=f"task {i + 1}", detail="fix bulk boundary",
                  kind=kind, files_hint=["invoice/pricing.py"], acceptance=["tests pass"]) for i in range(n)]
    return Plan(id="p1", request_id=rid, tasks=tasks, test_command="python -m pytest -q", summary="fix it")


class FakeMemory:
    def __init__(self):
        self.applied = []

    def propose(self, run):
        return [f"lesson from {run.task_id}"]

    def apply(self, entries):
        self.applied.extend(entries)


@pytest.fixture
def make_orch(tmp_path):
    def _make(script, plan=None):
        calls: list = []
        events: list = []
        mem = FakeMemory()
        store = Store(tmp_path / "state.db")
        orch = Orchestrator(
            store=store, workspace=tmp_path / "ws" / "demo", background=False,
            planner=lambda req, d, rid: plan or make_plan(rid),
            router=lambda task, hist: Route("claude", "sonnet", "low" if not hist else "medium",
                                            False, 0.9, "rules", "fake"),
            context=lambda task, d, b, h: ContextPack(task.id, "ctx", [], 1),
            worker_factory=lambda cli: FakeWorker(cli, script, calls),
            memory=mem)
        orch.on_event(events.append)
        return orch, calls, events, mem
    return _make


def kinds(events):
    return [e["kind"] for e in events]


def test_happy_path_plan_run_merge_memory(make_orch):
    orch, calls, events, mem = make_orch({"claude": ["fix"]})
    rid = orch.submit("make bulk discount apply at 10")
    plan_ev = [e for e in events if e["kind"] == "plan_ready"][0]
    assert orch.store.get_request(rid)["status"] == "awaiting_plan_approval"
    assert not calls  # nothing runs before the human approves the plan

    orch.approve(plan_ev["id"])
    assert len(calls) == 1
    merge_ev = [e for e in events if e["kind"] == "needs_approval"][0]
    assert "quantity is always an int" in merge_ev["text"]
    sha = merge_ev["sha"]
    ws = orch.workspace
    # main is untouched until merge approval
    assert "quantity > BULK" in (ws / "invoice" / "pricing.py").read_text()

    orch.approve(merge_ev["id"])
    assert "merged" in kinds(events)
    assert "quantity >= BULK" in (ws / "invoice" / "pricing.py").read_text()
    assert gitops.current_branch(ws) == "main"
    parents = gitops.git(ws, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert sha in parents  # --no-ff merge of the exact approved SHA
    assert orch.store.get_task("t1")["status"] == "merged"

    mem_ev = [e for e in events if e["kind"] == "memory_proposal"][0]
    orch.approve(mem_ev["id"])
    assert mem.applied == ["lesson from t1"]
    assert checks.verify_hidden("t1", ws)[0] is True


def test_retry_feeds_failure_back_then_passes(make_orch):
    orch, calls, events, _ = make_orch({"claude": ["break", "fix"]})
    orch.submit("x")
    orch.approve("p1")  # approve by plan id works too
    assert [c["round"] for c in calls] == [0, 1]
    assert calls[0]["feedback"] == ""
    assert "test command failed" in calls[1]["feedback"].lower()
    runs = orch.store.runs_for_task("t1")
    assert [r["data"]["passed"] for r in runs] == [False, True]
    assert orch.store.get_task("t1")["status"] == "awaiting_merge"


def test_switches_cli_after_retries_exhausted(make_orch):
    orch, calls, events, _ = make_orch({"claude": ["break"], "codex": ["fix"]})
    orch.submit("x")
    orch.approve("p1")
    assert [c["cli"] for c in calls] == ["claude", "claude", "claude", "codex"]
    assert calls[-1]["model"] != "sonnet"
    assert orch.store.get_task("t1")["status"] == "awaiting_merge"


def test_gives_up_and_asks_human_when_all_fail(make_orch):
    orch, calls, events, _ = make_orch({"claude": ["break"], "codex": ["missing"]})
    orch.submit("x")
    orch.approve("p1")
    assert orch.store.get_task("t1")["status"] == "failed"
    ask = [e for e in events if e["kind"] == "needs_approval"][-1]
    assert "still failing" in ask["text"] and "not installed" in ask["text"]
    assert gitops.current_branch(orch.workspace) == "main"
    # human says: one more go
    orch.script = None
    calls_before = len(calls)
    orch.worker_factory = lambda cli: FakeWorker(cli, {"claude": ["fix"]}, [])
    orch.approve(ask["id"])
    assert orch.store.get_task("t1")["status"] == "awaiting_merge"
    assert len(orch.store.runs_for_task("t1")) == calls_before + 1


def test_no_change_counts_as_failure(make_orch):
    orch, calls, events, _ = make_orch({"claude": [None, "fix"]})
    orch.submit("x")
    orch.approve("p1")
    assert "no file changes" in calls[1]["feedback"]
    assert orch.store.get_task("t1")["status"] == "awaiting_merge"


def test_deny_plan_and_merge(make_orch):
    orch, calls, events, _ = make_orch({"claude": ["fix"]}, plan=make_plan(n=2))
    rid = orch.submit("x")
    orch.deny(rid)  # deny by request id
    assert not calls
    assert orch.store.get_request(rid)["status"] == "denied"

    orch2, calls2, events2, _ = make_orch({"claude": ["fix"]})
    orch2.store = orch.store
    rid2 = orch2.submit("y")
    plan_ev = [e for e in events2 if e["kind"] == "plan_ready"][0]
    orch2.approve(plan_ev["id"])
    merge_ev = [e for e in events2 if e["kind"] == "needs_approval"][0]
    orch2.deny(merge_ev["id"])
    assert "quantity > BULK" in (orch2.workspace / "invoice" / "pricing.py").read_text()
    assert orch2.store.get_task("t1")["status"] == "rejected"


def test_merge_conflict_aborts(tmp_path):
    ws = gitops.create_workspace("c", base_dir=tmp_path)
    gitops.start_task_branch(ws, "a")
    (ws / "README.md").write_text("A\n")
    sha_a = gitops.commit_all(ws, "a")
    gitops.start_task_branch(ws, "b")
    (ws / "README.md").write_text("B\n")
    sha_b = gitops.commit_all(ws, "b")
    assert gitops.merge_sha(ws, sha_a)[0] is True
    ok, info = gitops.merge_sha(ws, sha_b)
    assert ok is False
    assert not gitops.has_changes(ws)
    assert (ws / "README.md").read_text() == "A\n"
    assert gitops.merge_sha(ws, "deadbeef")[0] is False


def test_workspace_is_own_repo_and_ignores_state(tmp_path):
    ws = gitops.create_workspace("w", base_dir=tmp_path)
    assert (ws / ".git").exists()
    gitops.start_task_branch(ws, "z")
    (ws / ".bhima").mkdir()
    (ws / ".bhima" / "junk.txt").write_text("x")
    assert gitops.commit_all(ws, "nothing") is None
    (ws / "new.py").write_text("x = 1\n")
    sha = gitops.commit_all(ws, "new")
    assert gitops.changed_files(ws, sha) == ["new.py"]


def test_parse_assumptions_variants():
    assert checks.parse_assumptions(REPORT_OK) == ["quantity is always an int", "none of the callers rely on > 10"]
    assert checks.parse_assumptions("## Assumptions:\n- none\n") == []
    assert checks.parse_assumptions("**Assumptions:**\n1. a\n2) b\n") == ["a", "b"]
    assert checks.parse_assumptions("no heading here") == []


def test_claude_output_parsing():
    obj = {"type": "result", "subtype": "success", "is_error": False, "result": REPORT_OK,
           "session_id": "s1", "total_cost_usd": 0.01, "num_turns": 3,
           "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5}}
    assert wclaude.parse_output(json.dumps(obj))["result"] == REPORT_OK
    assert wclaude.parse_output(json.dumps([{"type": "system"}, obj]))["session_id"] == "s1"
    assert wclaude.parse_output("warn\n" + json.dumps(obj))["num_turns"] == 3
    u = wclaude.usage_from(obj)
    assert u["input_tokens"] == 10 and u["total_cost_usd"] == 0.01
    cmd = wclaude.build_cmd("claude", Route("claude", "sonnet", "low", False, 1, "rules", ""))
    assert "--dangerously-skip-permissions" not in cmd and "--max-turns" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Edit,Write,Glob,Grep,Bash(python -m pytest*)"


def test_codex_jsonl_parsing():
    lines = [{"type": "thread.started"},
             {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
             {"type": "turn.completed", "usage": {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3}},
             {"type": "item.completed", "item": {"type": "agent_message", "text": REPORT_OK}},
             {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
    msg, usage, err = wcodex.parse_jsonl("\n".join(json.dumps(x) for x in lines))
    assert msg == REPORT_OK and err is None
    assert usage["input_tokens"] == 8 and usage["output_tokens"] == 4 and usage["turns"] == 2
    cmd = wcodex.build_cmd("codex", Route("codex", "gpt-5.5", "high", False, 1, "rules", ""), Path("w"))
    assert 'model_reasoning_effort="high"' in cmd and "workspace-write" in cmd
    assert not any("dangerously" in c for c in cmd)


def test_codex_missing_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(wcodex, "find_exe", lambda name: None)
    t = Task("t9", "r", "x", "y", "code", [], [])
    r = wcodex.CodexWorker().run(t, ContextPack("t9", "", [], 0), Route("codex", "m", "low", False, 1, "rules", ""),
                                 tmp_path, 0)
    assert r.ok is False and r.error == "codex not installed"


def test_prompt_has_report_format_and_feedback():
    t = Task("t1", "r", "Fix it", "do the thing", "code", ["a.py"], ["tests pass"])
    p = wbase.build_prompt(t, ContextPack("t1", "PACKTEXT", [], 1), feedback="boom")
    assert "PACKTEXT" in p and "boom" in p and p.rstrip().endswith("none\" if none>")
    assert "## Assumptions:" in p


def test_report_renders_from_store(make_orch):
    orch, calls, events, _ = make_orch({"claude": ["break", "fix"]})
    orch.submit("x")
    orch.approve("p1")
    text = report.render(orch.store)
    assert "t1" in text and "300" in text  # 2 rounds x 150 tokens


def test_demo_tasks_file_is_consistent():
    data = json.loads((Path(__file__).resolve().parent.parent / "demo" / "tasks.json").read_text())
    assert len(data["tasks"]) == 5
    kinds_ = {t["kind"] for t in data["tasks"]}
    assert "scrape" in kinds_
    for t in data["tasks"]:
        assert checks.hidden_test_for(t["id"]) is not None


def test_demo_template_tests_pass_and_hidden_tests_fail(tmp_path):
    ws = gitops.create_workspace("tpl", base_dir=tmp_path)
    ok, out = checks.run_tests(ws)
    assert ok, out
    for tid in ("t1", "t2", "t3", "t4", "t5"):
        passed, _ = checks.verify_hidden(tid, ws)
        assert passed is False, tid
    assert not gitops.has_changes(ws)
