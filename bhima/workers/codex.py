"""Codex worker: `codex exec --json ... --sandbox workspace-write -C <workspace>`.

The prompt is passed as `-` (read from stdin) so multi-line prompts survive the
Windows .cmd shim. Parses JSONL: turn.completed usage + the final agent message.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config
from ..models import ContextPack, Route, RunResult, Task
from .base import build_prompt, find_exe, run_cli, save_raw

DEFAULT_MODEL = "gpt-5.5"


def build_cmd(exe: str, route: Route, workdir: Path) -> list[str]:
    model = route.model if route.model and not route.model.startswith(("claude", "sonnet", "opus", "haiku")) \
        else DEFAULT_MODEL
    return [exe, "exec", "--json", "-m", model, "-c", f'model_reasoning_effort="{route.effort}"',
            "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", str(workdir), "-"]


def parse_jsonl(stdout: str) -> tuple[str, dict, str | None]:
    """Return (final agent message, summed usage, error message or None)."""
    message, error = "", None
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "turns": 0}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type", "")
        if kind == "turn.completed":
            u = ev.get("usage") or {}
            for k in ("input_tokens", "cached_input_tokens", "output_tokens"):
                usage[k] += int(u.get(k) or 0)
            usage["turns"] += 1
        elif kind == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") in ("agent_message", "assistant_message") and item.get("text"):
                message = item["text"]
        elif kind in ("turn.failed", "error"):
            err = ev.get("error") or ev.get("message") or ev
            error = err.get("message") if isinstance(err, dict) else str(err)
    return message, usage, error


class CodexWorker:
    name = "codex"

    def __init__(self, exe: str | None = None, test_command: str = config.DEFAULT_TEST_COMMAND):
        self.exe = exe
        self.test_command = test_command

    def run(self, task: Task, pack: ContextPack, route: Route, workdir: Path, round: int,
            feedback: str = "") -> RunResult:
        exe = self.exe or find_exe("codex")
        if not exe:
            return RunResult(task.id, round, self.name, False, "", [], {}, 0.0, "",
                             error="codex not installed")
        prompt = build_prompt(task, pack, feedback, self.test_command)
        cmd = build_cmd(exe, route, Path(workdir))
        code, out, err, secs, timed_out = run_cli(cmd, Path(workdir), stdin_text=prompt)
        message, usage, ev_error = parse_jsonl(out)
        raw_path = save_raw(self.name, task.id, round, {
            "cmd": cmd, "prompt": prompt, "returncode": code, "stdout": out,
            "stderr": err, "duration_s": secs, "timed_out": timed_out})
        from ..checks import parse_assumptions
        ok = code == 0 and not timed_out and ev_error is None and bool(message)
        error = None
        if not ok:
            error = ("timeout" if timed_out else None) or ev_error or err.strip()[-500:] or f"exit {code}"
        return RunResult(task_id=task.id, round=round, cli=self.name, ok=ok, report=message,
                         assumptions=parse_assumptions(message), usage=usage,
                         duration_s=float(f"{secs:.2f}"), raw_path=raw_path, error=error)
