"""Core flow tests with fake planner/router/worker: no claude/codex calls."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bhima import checks, config, gitops, report
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
        elif action == "unrelated":
            (workdir / "unrelated.txt").write_text("visible tests still pass")
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
    assert orch.store.get_request(rid)["status"] == "awaiting_merge"
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
    assert orch.store.get_request(rid)["status"] == "done"

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


def test_hidden_acceptance_checks_clean_candidate_before_merge(make_orch, monkeypatch):
    orch, calls, events, _ = make_orch({"claude": ["unrelated", "fix"]})
    actual_verify = checks.verify_hidden
    candidates = []

    def verify(task_id, checkout, hidden):
        assert checkout != orch.workspace
        assert gitops.head_sha(checkout) == gitops.head_sha(orch.workspace)
        assert not gitops.has_changes(checkout)
        assert not any((orch.workspace / "tests").glob("test_hidden_*.py"))
        assert not any(e.get("sha") for e in events if e["kind"] == "needs_approval")
        candidates.append(checkout)
        return actual_verify(task_id, checkout, hidden)

    monkeypatch.setattr(checks, "verify_hidden", verify)
    orch.submit("x")
    orch.approve("p1")
    assert len(candidates) == 2 and not any(p.exists() for p in candidates)
    assert "hidden acceptance test failed" in calls[1]["feedback"]
    assert orch.store.find_approval("t1")["kind"] == "merge"


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


def test_router_history_uses_round_outcomes_and_model(make_orch):
    from bhima import router

    orch, _, _, _ = make_orch({"claude": ["unrelated", None, "fix"]})
    observed = []

    def route(task, history):
        observed.append(router.summarize_history(task, history))
        return Route("claude", "haiku", "low", False, 1, "rules", "fake")

    orch.route_fn = route
    orch.submit("x")
    orch.approve("p1")
    assert observed[2]["prior_failures"] == 2
    assert observed[2]["past_results"] == {"claude_haiku": {"tries": 2, "ok": 0}}
    assert orch.store.runs_for_task("t1")[1]["data"]["visible_tests_passed"]
    assert [(r.model, r.ok) for r in orch._history("t1")] == [
        ("haiku", False), ("haiku", False), ("haiku", True),
    ]


def test_saved_plan_command_is_never_executed(make_orch, monkeypatch):
    import sys

    orch, _, _, _ = make_orch({"claude": ["fix"]})
    rid = orch.submit("x")
    data = orch.store.get_plan("p1")["data"]
    data["test_command"] = "untrusted-command"
    orch.store.save_plan("p1", rid, data)
    actual_run = subprocess.run
    commands = []

    def run(cmd, **kwargs):
        if cmd[0] != "git":
            assert not kwargs.get("shell")
            commands.append(cmd)
        return actual_run(cmd, **kwargs)

    monkeypatch.setattr(checks.subprocess, "run", run)
    orch.approve("p1")
    assert commands[0] == [sys.executable, "-m", "pytest", "-q"]
    assert orch.store.find_approval("t1")["kind"] == "merge"


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
    assert orch2.store.get_task("t1")["status"] == "denied"
    assert orch2.store.get_request(rid2)["status"] == "done"


def test_merge_conflict_can_be_retried_on_current_main(make_orch):
    orch, calls, events, _ = make_orch({"claude": ["fix"]})
    rid = orch.submit("x")
    orch.approve("p1")
    approval = orch.store.find_approval("t1")
    pricing = orch.workspace / "invoice" / "pricing.py"
    pricing.write_text(pricing.read_text().replace("quantity > BULK_THRESHOLD", "quantity > BULK_THRESHOLD "))
    gitops.commit_all(orch.workspace, "independent main edit")

    assert orch.approve(approval["id"]).startswith("Merge failed:")
    assert orch.store.get_task("t1")["status"] == "merge_conflict"
    assert orch.store.get_request(rid)["status"] == "awaiting_retry"
    assert "already decided" in orch.deny(approval["id"])
    retry = orch.store.find_approval("t1")
    assert retry["kind"] == "retry"
    orch.approve(retry["id"])
    assert "current main" in calls[-1]["feedback"]
    assert orch.store.get_request(rid)["status"] == "awaiting_merge"
    second = orch.store.find_approval("t1")
    assert second["kind"] == "merge" and second["detail"] != approval["detail"]
    assert orch.approve(second["id"]).startswith("Merged task")
    assert orch.store.get_request(rid)["status"] == "done"


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
    (ws / ".env").write_text("DEMO=fake")
    for name in (".venv", "__pycache__", "node_modules", "build", "dist"):
        (ws / name).mkdir()
        (ws / name / "generated").touch()
    assert gitops.commit_all(ws, "nothing") is None
    (ws / "new.py").write_text("x = 1\n")
    sha = gitops.commit_all(ws, "new")
    assert gitops.changed_files(ws, sha) == ["new.py"]


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_default_workspace_uses_platform_data_dir(tmp_path, monkeypatch, platform):
    from types import SimpleNamespace

    from bhima import config

    monkeypatch.setattr(config, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    data_dir = tmp_path / ("localappdata" if platform == "win32" else "home/.local/share")
    monkeypatch.setattr(config, "WORKSPACES_DIR", config._workspaces_dir())
    expected = data_dir / "bhima" / "workspaces" / "demo"
    assert gitops.workspace_path() == expected
    assert gitops.create_workspace() == expected


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


def test_claude_retries_and_records_unsupported_effort(tmp_path, monkeypatch):
    commands = []
    raw = {}

    def run(cmd, cwd, stdin_text):
        commands.append(cmd.copy())
        if "--effort" in cmd:
            return 1, "", "error: unknown option '--effort'", 0.1, False
        if "--max-turns" in cmd:
            return 1, "", "error: unknown option '--max-turns'", 0.1, False
        return 0, json.dumps({"result": REPORT_OK}), "", 0.1, False

    def save_raw(cli, task_id, rnd, payload):
        raw.update(payload)
        return "fake-run.json"

    monkeypatch.setattr(wclaude, "run_cli", run)
    monkeypatch.setattr(wclaude, "save_raw", save_raw)
    task = make_plan().tasks[0]
    pack = ContextPack(task.id, "", [], 0)
    route = Route("claude", "sonnet", "medium", False, 1, "rules", "")
    result = wclaude.ClaudeWorker(exe="fake-claude").run(task, pack, route, tmp_path, 0)
    assert result.ok and result.duration_s == 0.3
    assert len(commands) == 3 and "--effort" not in commands[1] and "--max-turns" in commands[1]
    assert "--effort" not in commands[2] and "--max-turns" not in commands[2]
    assert raw["unsupported_options"] == ["--effort", "--max-turns"]


def test_codex_jsonl_parsing():
    lines = [{"type": "thread.started"},
             {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
             {"type": "turn.completed", "usage": {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3}},
             {"type": "item.completed", "item": {"type": "agent_message", "text": REPORT_OK}},
             {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
    msg, usage, err = wcodex.parse_jsonl("warning\n{invalid json}\n" + "\n".join(json.dumps(x) for x in lines))
    assert msg == REPORT_OK and err is None
    assert usage == {"input_tokens": 8, "cached_input_tokens": 2, "output_tokens": 4, "turns": 2}


@pytest.mark.parametrize("event", [
    {"type": "error", "message": "request failed"},
    {"type": "turn.failed", "error": {"message": "request failed"}},
])
def test_codex_jsonl_errors(event):
    assert wcodex.parse_jsonl(json.dumps(event))[2] == "request failed"


def test_codex_native_command():
    cmd = wcodex.build_cmd("codex", Route("codex", "gpt-5.5", "high", False, 1, "rules", ""), Path("w"))
    assert cmd == ["codex", "exec", "--json", "-m", "gpt-5.5", "-c", 'model_reasoning_effort="high"',
                   "--sandbox", "workspace-write", "-C", "w", "-"]


@pytest.mark.parametrize(("workspace", "wsl_path"), [
    (r"C:\Users\x", "/mnt/c/Users/x"),
    (r"E:\Users\O'Brien & $USER\`work`", "/mnt/e/Users/O'Brien & $USER/`work`"),
])
def test_codex_wsl_command(workspace, wsl_path):
    route = Route("codex", "custom_model-1.2", "medium", False, 1, "rules", "")
    cmd = wcodex.build_cmd("wsl.exe", route, PureWindowsPath(workspace), via_wsl=True)
    assert cmd[:-1] == ["wsl.exe", "-d", "Ubuntu", "--shell-type", "none", "--", "bash", "-lc"]
    assert f"cd {shlex.quote(wsl_path)} &&" in cmd[-1]
    assert shlex.split(cmd[-1]) == [
        "source", "~/.nvm/nvm.sh", "&&", "cd", wsl_path, "&&", "exec", "timeout", "--signal=KILL",
        str(config.WORKER_TIMEOUT_S), "codex", "exec", "--json", "-m", "custom_model-1.2", "-c",
        'model_reasoning_effort="medium"', "--sandbox", "workspace-write", "-",
    ]


@pytest.mark.parametrize(("model", "effort", "error"), [
    ("", "low", "Invalid Codex model"),
    ("gpt-5.5; echo invalid", "low", "Invalid Codex model"),
    ("claude$(echo invalid)", "low", "Invalid Codex model"),
    ("gpt-5.5", "xhigh", "Invalid Codex reasoning effort"),
    ("gpt-5.5", 'high"; echo invalid', "Invalid Codex reasoning effort"),
])
def test_codex_rejects_invalid_launch_options(monkeypatch, tmp_path, model, effort, error):
    route = Route("codex", model, effort, False, 1, "rules", "")
    with pytest.raises(ValueError, match=error):
        wcodex.build_cmd("wsl.exe", route, tmp_path, via_wsl=True)
    monkeypatch.setattr(config, "codex_via_wsl", lambda: True)
    monkeypatch.setattr(wcodex, "find_exe", lambda name: "fake-wsl")
    monkeypatch.setattr(wcodex, "run_cli", lambda *a, **kw: pytest.fail("invalid launch must be refused"))
    task = make_plan().tasks[0]
    result = wcodex.CodexWorker().run(task, ContextPack(task.id, "", [], 0), route, tmp_path, 0)
    assert not result.ok and result.error.startswith(error)


@pytest.mark.parametrize(("os_name", "override", "expected"), [
    ("nt", "", True), ("posix", "", False), ("nt", "false", False), ("posix", "true", True),
])
def test_codex_wsl_config(monkeypatch, tmp_path, os_name, override, expected):
    monkeypatch.delenv("CODEX_VIA_WSL", raising=False)
    monkeypatch.setattr(config, "os", SimpleNamespace(name=os_name, environ=config.os.environ))
    monkeypatch.setattr(config, "_env_values", None)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"CODEX_VIA_WSL={override}\n" if override else "", encoding="utf-8")
    config.load_env(dotenv)
    assert config.codex_via_wsl() is expected


@pytest.mark.parametrize(("returncode", "error"), [(0, None), (137, "timeout")])
def test_codex_wsl_worker_passes_prompt_on_stdin(monkeypatch, tmp_path, returncode, error):
    task = make_plan().tasks[0]
    task.detail = "first line\nsecond line with 'quotes' and $(shell text)"
    pack = ContextPack(task.id, "", [], 0)
    route = Route("codex", "gpt-5.5", "low", False, 1, "rules", "")
    calls = []

    def find_exe(name):
        assert name == "wsl.exe"
        return "fake-wsl.exe"

    def run_cli(cmd, cwd, *, timeout, stdin_text, kill_tree):
        assert cwd == tmp_path and kill_tree and timeout > config.WORKER_TIMEOUT_S
        assert cmd[0] == "fake-wsl.exe" and task.detail not in cmd[-1]
        assert stdin_text == wbase.build_prompt(task, pack)
        calls.append(cmd)
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": REPORT_OK}}
        return returncode, json.dumps(event), "", 0.1, False

    monkeypatch.setattr(config, "codex_via_wsl", lambda: True)
    monkeypatch.setattr(wcodex, "find_exe", find_exe)
    monkeypatch.setattr(wcodex, "run_cli", run_cli)
    monkeypatch.setattr(wcodex, "save_raw", lambda *args: "fake-run.json")
    result = wcodex.CodexWorker().run(task, pack, route, tmp_path, 0)
    assert len(calls) == 1 and result.ok is (error is None)
    assert result.error == error and result.report == REPORT_OK


def test_codex_missing_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "codex_via_wsl", lambda: False)
    monkeypatch.setattr(wcodex, "find_exe", lambda name: None)
    t = Task("t9", "r", "x", "y", "code", [], [])
    r = wcodex.CodexWorker().run(t, ContextPack("t9", "", [], 0), Route("codex", "m", "low", False, 1, "rules", ""),
                                 tmp_path, 0)
    assert r.ok is False and r.error == "codex not installed"


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_worker_timeout_kills_process_tree(monkeypatch, tmp_path, os_name):
    cmd = ["fake-worker"]
    proc = MagicMock(pid=123)
    proc.__enter__.return_value = proc
    proc.communicate.side_effect = [subprocess.TimeoutExpired(cmd, 1), ("partial output", "")]
    popen = MagicMock(return_value=proc)
    taskkill = MagicMock()
    killpg = MagicMock()
    monkeypatch.setattr(wbase, "os", SimpleNamespace(name=os_name, killpg=killpg))
    monkeypatch.setattr(wbase, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", taskkill)

    code, out, err, _, timed_out = wbase.run_cli(cmd, tmp_path, timeout=1, stdin_text="prompt", kill_tree=True)

    assert (code, out, err, timed_out) == (-1, "partial output", "", True)
    assert popen.call_args.kwargs["start_new_session"] is (os_name == "posix")
    assert proc.communicate.call_args_list[0].args == ("prompt",)
    if os_name == "nt":
        assert taskkill.call_args.args[0] == ["taskkill.exe", "/F", "/T", "/PID", "123"]
        proc.kill.assert_called_once()
    else:
        killpg.assert_called_once_with(123, 9)


def test_planner_and_worker_get_clean_child_environment(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from bhima import config, planner

    monkeypatch.setattr(config, "_env_values", None)
    monkeypatch.setenv("BHIMA_CHILD_KEEP", "preserved")
    secret_names = ("GH_TOKEN", "GITHUB_TOKEN", "test_api_key", "SSH_SECRET_FILE", "DB_PASSWORD")
    for name in secret_names:
        monkeypatch.setenv(name, "fake")
    dotenv = tmp_path / ".env"
    dotenv.write_text("BHIMA_FILE_ONLY=private-config")
    config.load_env(dotenv)
    assert config.get("BHIMA_FILE_ONLY") == "private-config"
    assert "BHIMA_FILE_ONLY" not in os.environ
    calls = []

    def run(cmd, **kwargs):
        env = kwargs["env"]
        assert not set(secret_names) & env.keys()
        assert "BHIMA_FILE_ONLY" not in env
        assert env["BHIMA_CHILD_KEEP"] == "preserved"
        calls.append(cmd[0])
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(planner, "_claude_exe", lambda: "fake-planner")
    monkeypatch.setattr(planner.talker, "chat", lambda *args, **kwargs: None)
    wbase.run_cli(["fake-worker"], tmp_path)
    planner.make_plan("question", tmp_path)
    assert calls == ["fake-worker", "fake-planner"]
    assert all(os.environ[name] == "fake" for name in secret_names)


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


def test_planner_source_is_saved_and_shown(make_orch):
    from bhima.models import plan_from_dict

    plan = make_plan()
    plan.planned_by = "talker-local"
    orch, _, events, _ = make_orch({}, plan=plan)
    rid = orch.submit("fix it")
    data = orch.store.get_plan(plan.id)["data"]
    assert data["planned_by"] == "talker-local"
    assert plan_from_dict(data).planned_by == "talker-local"
    data.pop("planned_by")
    assert plan_from_dict(data).planned_by is None
    event = next(event for event in events if event["kind"] == "plan_ready")
    assert "planned by: talker-local" in event["text"]
    assert f"{rid} planned by: talker-local" in report.render(orch.store)


def test_demo_tasks_file_is_consistent():
    data = json.loads((Path(__file__).resolve().parent.parent / "demo" / "tasks.json").read_text())
    assert [t["id"] for t in data["tasks"]] == ["t1", "t2", "t3", "t4", "t5", "t6", "t7"]
    kinds_ = {t["kind"] for t in data["tasks"]}
    assert "scrape" in kinds_
    for t in data["tasks"]:
        assert checks.hidden_test_for(t["id"]) is not None


def test_demo_template_tests_pass_and_hidden_tests_fail(tmp_path):
    ws = gitops.create_workspace("tpl", base_dir=tmp_path)
    ok, out = checks.run_tests(ws)
    assert ok, out
    for tid in ("t1", "t2", "t3", "t4", "t5", "t6", "t7"):
        passed, _ = checks.verify_hidden(tid, ws)
        assert passed is False, tid
    assert not gitops.has_changes(ws)


def test_cli_status_report_and_demo_plan(tmp_path, monkeypatch, capsys):
    from bhima import cli, config, orchestrator
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "ws")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(orchestrator, "_instance", None)
    assert cli.main(["status"]) == 0
    assert "No requests yet" in capsys.readouterr().out
    assert cli.main(["report"]) == 0
    plan = cli.demo_plan("t5")
    assert plan.tasks[0].id.startswith("t5-") and plan.tasks[0].kind == "scrape"
    assert checks.hidden_test_for(plan.tasks[0].id).name == "test_hidden_t5.py"


def test_cli_commands_finish_in_foreground(make_orch, monkeypatch):
    from bhima import cli, config, orchestrator

    orch, calls, _, _ = make_orch({"claude": ["fix"]})
    backgrounds = []

    def configure(*, background):
        backgrounds.append(background)
        orch.background = background
        return orch

    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(config, "_env_values", None)
    monkeypatch.setattr(orchestrator, "configure", configure)
    assert cli.main(["ask", "fix it"]) == 0
    plan = orch.store.find_approval("p1")
    assert plan["kind"] == "plan"
    assert cli.main(["approve", plan["id"]]) == 0
    merge = orch.store.find_approval("t1")
    assert calls and merge["kind"] == "merge"
    assert cli.main(["deny", merge["id"]]) == 0
    assert orch.store.get_task("t1")["status"] == "denied"
    assert cli.main(["demo", "t1", "--yes"]) == 0
    assert any(a["kind"] == "merge" and a["target_id"].startswith("t1-")
               for a in orch.store.pending_approvals())
    assert backgrounds == [False] * 4
    assert not orch._threads


def test_module_level_api_for_bot(tmp_path, monkeypatch):
    from bhima import orchestrator
    events = []
    orch = Orchestrator(store=Store(tmp_path / "s.db"), workspace=tmp_path / "ws" / "demo", background=True,
                        planner=lambda r, d, rid: make_plan(rid),
                        router=lambda t, h: Route("claude", "sonnet", "low", False, 1, "rules", ""),
                        context=lambda t, d, b, h: ContextPack(t.id, "", [], 0),
                        worker_factory=lambda cli: FakeWorker(cli, {"claude": ["fix"]}, []), memory=FakeMemory())
    monkeypatch.setattr(orchestrator, "_instance", orch)
    orchestrator.on_event(events.append)
    rid = orchestrator.submit("hi")
    orch.wait(30)
    ev = [e for e in events if e["kind"] == "plan_ready"][0]
    assert len(ev["id"]) <= 55 and set(ev) >= {"kind", "id", "text"}
    orchestrator.approve(ev["id"])
    orch.wait(60)
    m = [e for e in events if e["kind"] == "needs_approval"][0]
    orchestrator.approve(m["id"])
    orch.wait(30)
    assert "merged" in kinds(events)
    assert rid in orchestrator.status()
