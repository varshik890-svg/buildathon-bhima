"""Claude Code worker: `claude -p ... --output-format json` inside the workspace.

The prompt goes in on stdin (same as `claude -p "<prompt>"`, but safe for long,
multi-line prompts on Windows). Never uses bypass/skip-permissions flags.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config
from ..models import ContextPack, Route, RunResult, Task
from .base import build_prompt, find_exe, run_cli, save_raw


def parse_assumptions(text: str) -> list[str]:
    try:
        from ..checks import parse_assumptions as _pa
        return _pa(text)
    except Exception:  # checks not importable for some reason
        return []


def build_cmd(exe: str, route: Route, max_turns: bool = True) -> list[str]:
    cmd = [exe, "-p", "--output-format", "json", "--model", route.model,
           "--effort", route.effort, "--allowedTools", config.CLAUDE_ALLOWED_TOOLS]
    if max_turns:
        cmd += ["--max-turns", str(config.CLAUDE_MAX_TURNS)]
    return cmd


def parse_output(stdout: str) -> dict:
    """Return the `result` message from claude's JSON output (object or list)."""
    stdout = stdout.strip()
    if not stdout:
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # sometimes a log line precedes the JSON; take the last JSON-looking line
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return {}
    if isinstance(data, list):
        results = [m for m in data if isinstance(m, dict) and m.get("type") == "result"]
        return results[-1] if results else {}
    return data if isinstance(data, dict) else {}


def usage_from(msg: dict) -> dict:
    u = dict(msg.get("usage") or {})
    return {
        "input_tokens": int(u.get("input_tokens") or 0),
        "output_tokens": int(u.get("output_tokens") or 0),
        "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
        "total_cost_usd": float(msg.get("total_cost_usd") or 0.0),
        "num_turns": int(msg.get("num_turns") or 0),
        "session_id": msg.get("session_id"),
    }


class ClaudeWorker:
    name = "claude"

    def __init__(self, exe: str | None = None, test_command: str = config.DEFAULT_TEST_COMMAND):
        self.exe = exe
        self.test_command = test_command

    def run(self, task: Task, pack: ContextPack, route: Route, workdir: Path, round: int,
            feedback: str = "") -> RunResult:
        exe = self.exe or find_exe("claude")
        if not exe:
            return RunResult(task.id, round, self.name, False, "", [], {}, 0.0, "",
                             error="claude not installed")
        prompt = build_prompt(task, pack, feedback, self.test_command)
        cmd = build_cmd(exe, route)
        unsupported_options = []
        secs = 0.0
        while True:
            code, out, err, elapsed, timed_out = run_cli(cmd, Path(workdir), stdin_text=prompt)
            secs += elapsed
            unknown = re.search(r"""unknown option\s*:?\s*['"]?(--effort|--max-turns)(?![\w-])""",
                                err, re.IGNORECASE)
            option = unknown.group(1).lower() if unknown else None
            if code == 0 or timed_out or option not in cmd:
                break
            index = cmd.index(option)
            del cmd[index:index + 2]
            unsupported_options.append(option)
        msg = parse_output(out)
        raw_path = save_raw(self.name, task.id, round, {
            "cmd": cmd, "prompt": prompt, "returncode": code, "stdout": out,
            "stderr": err, "duration_s": secs, "timed_out": timed_out,
            "unsupported_options": unsupported_options})
        report = str(msg.get("result") or "")
        is_error = bool(msg.get("is_error")) or code != 0 or not msg
        error = None
        if timed_out:
            error = f"timeout after {int(secs)}s"
        elif is_error:
            error = (msg.get("subtype") if msg.get("subtype") not in (None, "success") else None) \
                or (err.strip()[-500:] if err.strip() else None) or report[-500:] or f"exit {code}"
        return RunResult(task_id=task.id, round=round, cli=self.name, ok=not is_error and not timed_out,
                         report=report, assumptions=parse_assumptions(report),
                         usage=usage_from(msg) if msg else {}, duration_s=round_(secs),
                         raw_path=raw_path, error=error)


def round_(x: float) -> float:
    return float(f"{x:.2f}")
