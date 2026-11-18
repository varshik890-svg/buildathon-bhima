"""Paths, constants and a tiny .env loader (no extra dependencies)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".bhima"
DB_PATH = STATE_DIR / "state.db"
WORKSPACES_DIR = STATE_DIR / "workspaces"
RUNS_DIR = STATE_DIR / "runs"
LOGS_DIR = STATE_DIR / "logs"
DEMO_DIR = ROOT / "demo"
TEMPLATE_DIR = DEMO_DIR / "template"
DEMO_TASKS = DEMO_DIR / "tasks.json"

DEFAULT_WORKSPACE = "demo"
DEFAULT_TEST_COMMAND = "python -m pytest -q"
MAX_RETRIES = 2            # same CLI, failure fed back
WORKER_TIMEOUT_S = 15 * 60
CHECK_TIMEOUT_S = 5 * 60
CONTEXT_BUDGET_TOKENS = 6000
CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(python -m pytest*)"
CLAUDE_MAX_TURNS = 30

_loaded = False


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE lines from .env into os.environ (existing vars win)."""
    global _loaded
    path = path or (ROOT / ".env")
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            values[key] = val
            os.environ.setdefault(key, val)
    _loaded = True
    return values


def get(key: str, default: str = "") -> str:
    if not _loaded:
        load_env()
    return os.environ.get(key, default)


def ensure_dirs() -> None:
    for d in (STATE_DIR, WORKSPACES_DIR, RUNS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
