"""Worker protocol plus shared helpers (prompt building, subprocess, raw output)."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from .. import config
from ..models import ContextPack, Route, RunResult, Task

REPORT_FORMAT = """\
When you are done, reply with a short report in exactly this format:

## Summary:
<one or two sentences on what you changed>
## Files changed:
- <path>
## Tests:
<the test command you ran and whether it passed>
## Assumptions:
- <every assumption you made that the human should check; write "- none" if none>
"""


class Worker(Protocol):
    name: str

    def run(self, task: Task, pack: ContextPack, route: Route, workdir: Path, round: int,
            feedback: str = "") -> RunResult: ...


def build_prompt(task: Task, pack: ContextPack, feedback: str = "",
                 test_command: str = config.DEFAULT_TEST_COMMAND) -> str:
    parts = [
        f"You are working in a git repository (the current directory). Task {task.id}: {task.title}",
        "",
        task.detail,
    ]
    if task.acceptance:
        parts += ["", "Acceptance criteria:"] + [f"- {a}" for a in task.acceptance]
    if task.files_hint:
        parts += ["", "Likely files: " + ", ".join(task.files_hint)]
    if pack and pack.text:
        parts += ["", "# Context pack", pack.text]
    if feedback:
        parts += ["", "# Previous attempt failed. Fix this:", feedback]
    parts += [
        "",
        "Rules: edit files only inside this directory. Do not run git. Do not touch .bhima/.",
        f"Run `{test_command}` before you finish and make sure it passes.",
        "",
        REPORT_FORMAT,
    ]
    return "\n".join(parts)


def find_exe(name: str) -> str | None:
    """Resolve a CLI on PATH (handles Windows .cmd shims)."""
    for cand in (name, f"{name}.cmd", f"{name}.exe"):
        path = shutil.which(cand)
        if path:
            return path
    return None


def run_cli(cmd: list[str], cwd: Path, timeout: int = config.WORKER_TIMEOUT_S,
            stdin_text: str | None = None) -> tuple[int, str, str, float, bool]:
    """Run a command; returns (returncode, stdout, stderr, seconds, timed_out)."""
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              input=stdin_text)
        return proc.returncode, proc.stdout or "", proc.stderr or "", time.time() - start, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return -1, out, err, time.time() - start, True


def save_raw(cli: str, task_id: str, round_: int, payload: dict) -> str:
    config.ensure_dirs()
    path = config.RUNS_DIR / f"{task_id}-r{round_}-{cli}-{int(time.time())}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)
