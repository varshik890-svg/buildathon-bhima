"""Worker protocol plus shared helpers (prompt building, subprocess, raw output)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from contextlib import suppress
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


def _run_with_tree_kill(cmd: list[str], cwd: Path, timeout: int,
                        stdin_text: str | None) -> subprocess.CompletedProcess:
    with subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE if stdin_text is not None else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", env=config.child_env(),
                          start_new_session=os.name != "nt") as proc:
        try:
            out, err = proc.communicate(stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name == "nt":
                with suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run(["taskkill.exe", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=5, env=config.child_env())
                proc.kill()
            else:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                exc.output, exc.stderr = proc.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run_cli(cmd: list[str], cwd: Path, timeout: int = config.WORKER_TIMEOUT_S,
            stdin_text: str | None = None, *, kill_tree: bool = False) -> tuple[int, str, str, float, bool]:
    """Run a command; returns (returncode, stdout, stderr, seconds, timed_out)."""
    start = time.time()
    try:
        if kill_tree:
            proc = _run_with_tree_kill(cmd, cwd, timeout, stdin_text)
        else:
            proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout,
                                  input=stdin_text, env=config.child_env())
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
