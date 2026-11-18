"""Command line: python -m bhima <command>.

  ask "<text>" [--yes]     plan a request (and with --yes approve the plan right away)
  demo <task-id> [--yes]   run one seeded task from demo/tasks.json (skips the planner)
  approve <id> / deny <id> decide a pending plan / merge / memory / retry approval
  status                   requests, tasks and pending approvals
  report                   rounds, tokens and time per task
  reset                    recreate the demo workspace from demo/template
  bot                      start the Telegram bot (same process runs the orchestrator)
  baseline [--tasks t1,t2] plain `claude -p` vs the harness on demo/tasks.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import checks, config, gitops, report
from .models import Plan, Task, task_from_dict
from .store import Store, new_id


def _print_event(ev: dict) -> None:
    print(f"[{ev['kind']}] {ev['text']}", flush=True)


def _orch(background: bool = False):
    from . import orchestrator
    orch = orchestrator.configure(background=background)
    orch.on_event(_print_event)
    return orch


def load_demo_tasks(path: Path | None = None) -> dict:
    return json.loads(Path(path or config.DEMO_TASKS).read_text(encoding="utf-8"))


def demo_plan(task_id: str) -> Plan:
    data = load_demo_tasks()
    for t in data["tasks"]:
        if t["id"] == task_id:
            rid = new_id("r")
            task = task_from_dict({**t, "id": f"{t['id']}-{rid[1:]}", "request_id": rid})
            return Plan(id=new_id("p"), request_id=rid, tasks=[task],
                        test_command=data.get("test_command", config.DEFAULT_TEST_COMMAND),
                        summary=f"demo {t['id']}: {t['title']}")
    raise SystemExit(f"unknown demo task {task_id}; have {[t['id'] for t in data['tasks']]}")


def cmd_ask(args) -> int:
    orch = _orch()
    rid = orch.submit(args.text)
    if args.yes:
        print(orch.approve(rid))
    print(f"request {rid}")
    return 0


def cmd_demo(args) -> int:
    orch = _orch()
    orch.ensure_workspace()
    plan = demo_plan(args.task_id)
    rid = orch.submit_plan(plan, auto_approve=args.yes)
    print(f"request {rid}")
    return 0


def cmd_approve(args) -> int:
    print(_orch().approve(args.id))
    return 0


def cmd_deny(args) -> int:
    print(_orch().deny(args.id))
    return 0


def cmd_status(args) -> int:
    print(_orch().status())
    return 0


def cmd_report(args) -> int:
    print(report.render())
    return 0


def cmd_reset(args) -> int:
    ws = _orch().ensure_workspace(reset=True)
    print(f"workspace reset: {ws}")
    return 0


def cmd_bot(args) -> int:
    from .telegram_bot import run_bot
    return run_bot()


# -------------------------------------------------------------- baseline --

def run_plain(task: Task, ws: Path, model: str, effort: str) -> dict:
    """One plain `claude -p` run with just the task text: no pack, no checks, no retries."""
    from .workers.base import find_exe, run_cli, save_raw
    from .workers.claude import parse_output, usage_from
    exe = find_exe("claude")
    if not exe:
        return {"error": "claude not installed", "verified": None}
    prompt = f"{task.title}\n\n{task.detail}"
    cmd = [exe, "-p", "--output-format", "json", "--model", model, "--effort", effort,
           "--allowedTools", config.CLAUDE_ALLOWED_TOOLS, "--max-turns", str(config.CLAUDE_MAX_TURNS)]
    code, out, err, secs, timed_out = run_cli(cmd, ws, stdin_text=prompt)
    msg = parse_output(out)
    save_raw("plain", task.id, 0, {"cmd": cmd, "prompt": prompt, "stdout": out, "stderr": err})
    usage = usage_from(msg) if msg else {}
    tests_ok, _ = checks.run_tests(ws)
    verified, _ = checks.verify_hidden(task.id, ws)
    return {"rounds": 1, "tokens": report.tokens_of(usage), "secs": secs, "tests": tests_ok,
            "verified": verified, "cost": usage.get("total_cost_usd", 0.0),
            "error": "timeout" if timed_out else (None if code == 0 else err[-300:])}


def run_harness(task: Task, test_command: str) -> dict:
    from .orchestrator import Orchestrator
    store = Store(config.STATE_DIR / "baseline.db")
    orch = Orchestrator(store=store, workspace=config.WORKSPACES_DIR / f"baseline-bhima-{task.id}",
                        background=False)
    orch.on_event(_print_event)
    ws = orch.ensure_workspace(reset=True)
    task.id = f"{task.id}-{new_id('')}"  # fresh history per baseline run; hidden test matched by prefix
    task.request_id = store.add_request(f"baseline {task.id}: {task.title}")
    start = time.time()
    res = orch.run_task(task, test_command)
    secs = time.time() - start
    runs = store.runs_for_task(task.id)
    verified = None
    if res.get("sha"):
        gitops.git(ws, "checkout", "-q", res["sha"])
        verified, _ = checks.verify_hidden(task.id, ws)
        gitops.git(ws, "checkout", "-q", gitops.MAIN)
    else:
        verified = False
    return {"rounds": len(runs), "tokens": sum(report.tokens_of(r["data"].get("usage", {})) for r in runs),
            "secs": secs, "tests": res.get("passed"), "verified": verified,
            "cost": sum(float(r["data"].get("usage", {}).get("total_cost_usd", 0) or 0) for r in runs),
            "route": "->".join(dict.fromkeys(f"{r['cli']}:{r['model']}/{r['effort']}" for r in runs))}


def cmd_baseline(args) -> int:
    config.ensure_dirs()
    data = load_demo_tasks()
    wanted = set(args.tasks.split(",")) if args.tasks else None
    results = []
    for t in data["tasks"]:
        if wanted and t["id"] not in wanted:
            continue
        print(f"\n=== {t['id']}: {t['title']} ===", flush=True)
        entry = {"task_id": t["id"], "title": t["title"]}
        if not args.harness_only:
            ws = gitops.create_workspace(f"baseline-plain-{t['id']}", reset=True)
            print(f"[plain] claude -p --model {args.model} --effort {args.effort}", flush=True)
            entry["plain"] = run_plain(task_from_dict(t), ws, args.model, args.effort)
            print(f"[plain] verified={entry['plain'].get('verified')}", flush=True)
        if not args.plain_only:
            entry["harness"] = run_harness(task_from_dict(t), data.get("test_command", config.DEFAULT_TEST_COMMAND))
            print(f"[bhima] verified={entry['harness'].get('verified')}", flush=True)
        results.append(entry)
        (config.STATE_DIR / "baseline.json").write_text(json.dumps(results, indent=2, default=str),
                                                        encoding="utf-8")
    print("\n" + report.render_baseline(results))
    return 0


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    config.ensure_dirs()
    p = argparse.ArgumentParser(prog="bhima", description="Harness around Claude Code and Codex.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="plan a request")
    a.add_argument("text")
    a.add_argument("--yes", action="store_true", help="approve the plan immediately")
    a.set_defaults(fn=cmd_ask)
    d = sub.add_parser("demo", help="run one seeded demo task")
    d.add_argument("task_id")
    d.add_argument("--yes", action="store_true", help="approve the plan immediately")
    d.set_defaults(fn=cmd_demo)
    for name, fn in (("approve", cmd_approve), ("deny", cmd_deny)):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(fn=fn)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("reset").set_defaults(fn=cmd_reset)
    sub.add_parser("bot").set_defaults(fn=cmd_bot)
    b = sub.add_parser("baseline", help="plain claude -p vs bhima on demo/tasks.json")
    b.add_argument("--tasks", default="", help="comma list, e.g. t1,t3")
    b.add_argument("--model", default="sonnet")
    b.add_argument("--effort", default="medium")
    b.add_argument("--plain-only", action="store_true")
    b.add_argument("--harness-only", action="store_true")
    b.set_defaults(fn=cmd_baseline)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
