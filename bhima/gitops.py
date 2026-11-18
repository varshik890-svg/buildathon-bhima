"""Git operations on the *workspace* repo (never on the Bhima repo, never push).

A workspace is `.bhima/workspaces/<name>`, its own git repo created from
demo/template. Each task works on branch `bhima/task-<id>`; approved SHAs are
merged into `main` with --no-ff.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config

MAIN = "main"
WS_GITIGNORE = ".bhima/\n__pycache__/\n*.pyc\n.pytest_cache/\n"


class GitError(RuntimeError):
    pass


def git(ws: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "user.name=bhima", "-c", "user.email=bhima@localhost",
           "-c", "core.autocrlf=false", *args]
    proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def branch_name(task_id: str) -> str:
    return f"bhima/task-{task_id}"


def workspace_path(name: str = config.DEFAULT_WORKSPACE) -> Path:
    return config.WORKSPACES_DIR / name


def create_workspace(name: str = config.DEFAULT_WORKSPACE, template: Path | None = None,
                     reset: bool = False, base_dir: Path | None = None) -> Path:
    """Copy the template into a fresh git repo with one initial commit on main."""
    template = Path(template or config.TEMPLATE_DIR)
    ws = Path(base_dir or config.WORKSPACES_DIR) / name
    if ws.exists() and reset:
        _rmtree(ws)
    if (ws / ".git").exists():
        return ws
    ws.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, ws, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
    (ws / ".gitignore").write_text(WS_GITIGNORE, encoding="utf-8")
    git(ws, "init", "-q", "-b", MAIN)
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "initial: workspace from template")
    return ws


def _rmtree(path: Path) -> None:
    import os
    import stat

    def onerr(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onexc=onerr)


def current_branch(ws: Path) -> str:
    return git(ws, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def head_sha(ws: Path, ref: str = "HEAD") -> str:
    return git(ws, "rev-parse", ref).stdout.strip()


def start_task_branch(ws: Path, task_id: str) -> str:
    """Create (or reuse) the task branch off main and check it out."""
    br = branch_name(task_id)
    git(ws, "checkout", "-q", MAIN)
    exists = git(ws, "rev-parse", "--verify", "--quiet", br, check=False).returncode == 0
    if exists:
        git(ws, "checkout", "-q", br)
    else:
        git(ws, "checkout", "-q", "-b", br)
    return br


def has_changes(ws: Path) -> bool:
    return bool(git(ws, "status", "--porcelain").stdout.strip())


def commit_all(ws: Path, message: str) -> str | None:
    """Commit every change in the workspace (except .bhima). Returns SHA, or None if clean."""
    git(ws, "add", "-A", "--", ".", ":(exclude).bhima")
    staged = git(ws, "diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        return None
    git(ws, "commit", "-q", "-m", message)
    return head_sha(ws)


def discard_changes(ws: Path) -> None:
    git(ws, "reset", "-q", "--hard")
    git(ws, "clean", "-fdq", "-e", ".bhima")


def diff_stat(ws: Path, sha: str, base: str = MAIN) -> str:
    return git(ws, "diff", "--stat", f"{base}...{sha}", check=False).stdout.strip()


def diff(ws: Path, sha: str, base: str = MAIN) -> str:
    return git(ws, "diff", f"{base}...{sha}", check=False).stdout


def changed_files(ws: Path, sha: str, base: str = MAIN) -> list[str]:
    out = git(ws, "diff", "--name-only", f"{base}...{sha}", check=False).stdout
    return [l for l in out.splitlines() if l.strip()]


def merge_sha(ws: Path, sha: str, message: str | None = None) -> tuple[bool, str]:
    """Merge exactly `sha` into main with --no-ff. Aborts on conflict. Never pushes."""
    if git(ws, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
        return False, f"unknown commit {sha}"
    if has_changes(ws):
        discard_changes(ws)
    git(ws, "checkout", "-q", MAIN)
    proc = git(ws, "merge", "--no-ff", "-m", message or f"bhima: merge {sha[:10]}", sha, check=False)
    if proc.returncode != 0:
        git(ws, "merge", "--abort", check=False)
        return False, (proc.stdout + proc.stderr).strip() or "merge conflict"
    return True, head_sha(ws)
