"""Checks: run the plan's test command in the workspace and parse "## Assumptions:".

`verify_hidden` additionally runs a demo task's hidden acceptance test (from
demo/hidden_tests) against the workspace, then removes it again. Used by the
baseline comparison so both arms are scored the same way.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import config
from .models import CheckResult, Task

_HEADING = re.compile(r"^\s*#{1,6}\s*assumptions\s*:?\s*$|^\s*\**assumptions\**\s*:\s*$", re.I)
_NEXT_HEADING = re.compile(r"^\s*#{1,6}\s+\S")
_NONE = {"none", "none.", "n/a", "no assumptions", "no assumptions.", "nothing"}


def parse_assumptions(text: str) -> list[str]:
    """Bullet items under the '## Assumptions:' heading (until the next heading)."""
    if not text:
        return []
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            if _HEADING.match(line):
                inside = True
            continue
        if _NEXT_HEADING.match(line):
            break
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^([-*+]|\d+[.)])\s*", "", s).strip()
        if s and s.lower() not in _NONE:
            out.append(s)
    return out


def _split_command(test_command: str) -> list[str]:
    parts = shlex.split(test_command, posix=True)
    # run "python ..." with the same interpreter as bhima (venv-safe)
    if parts and parts[0] in ("python", "python3", "py"):
        parts[0] = sys.executable
    return parts


def run_tests(workdir: Path, test_command: str = config.DEFAULT_TEST_COMMAND,
              extra_args: list[str] | None = None) -> tuple[bool, str]:
    cmd = _split_command(test_command) + list(extra_args or [])
    try:
        proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=config.CHECK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"tests timed out after {config.CHECK_TIMEOUT_S}s"
    except FileNotFoundError as exc:
        return False, f"cannot run test command: {exc}"
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, output[-4000:]


def run_checks(task: Task, workdir: Path, test_command: str, report: str = "") -> CheckResult:
    passed, output = run_tests(Path(workdir), test_command or config.DEFAULT_TEST_COMMAND)
    return CheckResult(task_id=task.id, tests_passed=passed, output=output,
                       assumptions=parse_assumptions(report))


def hidden_test_for(task_id: str, tasks_file: Path | None = None) -> Path | None:
    import json
    tasks_file = Path(tasks_file or config.DEMO_TASKS)
    if not tasks_file.exists():
        return None
    for t in json.loads(tasks_file.read_text(encoding="utf-8")).get("tasks", []):
        if str(t.get("id")) == str(task_id) and t.get("hidden_test"):
            p = tasks_file.parent / t["hidden_test"]
            return p if p.exists() else None
    return None


def verify_hidden(task_id: str, workdir: Path, hidden: Path | None = None) -> tuple[bool | None, str]:
    """Run the hidden acceptance test for a demo task. None = no hidden test."""
    hidden = hidden or hidden_test_for(task_id)
    if not hidden:
        return None, "no hidden test"
    dest = Path(workdir) / "tests" / hidden.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(hidden, dest)
    try:
        return run_tests(Path(workdir), f"python -m pytest -q {dest.relative_to(workdir).as_posix()}")
    finally:
        dest.unlink(missing_ok=True)


def failure_feedback(check: CheckResult, error: str | None = None) -> str:
    """Text fed back to the worker on retry."""
    parts = []
    if error:
        parts.append(f"The worker run reported an error: {error}")
    if not check.tests_passed:
        tail = "\n".join(check.output.splitlines()[-40:])
        parts.append(f"The test command failed. Last lines of output:\n{tail}")
    return "\n\n".join(parts)
