"""Run Codex in the workspace, using Ubuntu on Windows by default.

Prompts are read from stdin. The JSONL stream provides usage, messages and errors.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PureWindowsPath

from .. import config
from ..models import ContextPack, Route, RunResult, Task
from .base import build_prompt, find_exe, run_cli, save_raw

DEFAULT_MODEL = "gpt-5.5"
WSL_TIMEOUT_GRACE_S = 5


def _wsl_path(workdir: Path | PureWindowsPath) -> str:
    path = PureWindowsPath(workdir)
    if path.is_absolute() and re.fullmatch(r"[a-zA-Z]:", path.drive):
        return "/".join(("", "mnt", path.drive[0].lower(), *path.parts[1:]))
    if not path.drive and str(workdir).startswith("/"):
        return workdir.as_posix()
    raise ValueError("Codex via WSL requires an absolute drive or Linux workspace path")


def build_cmd(exe: str, route: Route, workdir: Path | PureWindowsPath, *,
              via_wsl: bool = False) -> list[str]:
    model = route.model
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9._-]+", model):
        raise ValueError("Invalid Codex model. Use letters, digits, dots, underscores or hyphens.")
    if model.startswith(("claude", "sonnet", "opus", "haiku")):
        model = DEFAULT_MODEL
    if route.effort not in ("low", "medium", "high"):
        raise ValueError("Invalid Codex reasoning effort. Use low, medium or high.")

    args = ["exec", "--json", "-m", model, "-c", f'model_reasoning_effort="{route.effort}"',
            "--sandbox", "workspace-write"]
    if via_wsl:
        # The Windows relay can exit while its Linux command is still running.
        # GNU timeout signals the Codex process group inside Ubuntu.
        codex_cmd = shlex.join(["timeout", "--signal=KILL", str(config.WORKER_TIMEOUT_S), "codex", *args, "-"])
        script = f"source ~/.nvm/nvm.sh && cd {shlex.quote(_wsl_path(workdir))} && exec {codex_cmd}"
        # Give Bash the script as one argument without a second shell expanding it.
        return [exe, "-d", "Ubuntu", "--shell-type", "none", "--", "bash", "-lc", script]
    return [exe, *args, "-C", str(workdir), "-"]


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
        via_wsl = config.codex_via_wsl()
        exe = find_exe("wsl.exe") if via_wsl else self.exe or find_exe("codex")
        if not exe:
            missing = "wsl.exe" if via_wsl else "codex"
            return RunResult(task.id, round, self.name, False, "", [], {}, 0.0, "",
                             error=f"{missing} not installed")
        workdir = Path(workdir).absolute()
        prompt = build_prompt(task, pack, feedback, self.test_command)
        timeout = config.WORKER_TIMEOUT_S + (WSL_TIMEOUT_GRACE_S if via_wsl else 0)
        try:
            cmd = build_cmd(exe, route, workdir, via_wsl=via_wsl)
            code, out, err, secs, timed_out = run_cli(cmd, workdir, timeout=timeout, stdin_text=prompt,
                                                      kill_tree=True)
        except (OSError, ValueError) as exc:
            return RunResult(task.id, round, self.name, False, "", [], {}, 0.0, "", error=str(exc))
        timed_out = timed_out or (via_wsl and code in (137, -9))
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
