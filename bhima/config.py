"""Paths, constants and a tiny .env loader (no extra dependencies)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".bhima"
DB_PATH = STATE_DIR / "state.db"
RUNS_DIR = STATE_DIR / "runs"
LOGS_DIR = STATE_DIR / "logs"
DEMO_DIR = ROOT / "demo"
TEMPLATE_DIR = DEMO_DIR / "template"
DEMO_TASKS = DEMO_DIR / "tasks.json"


def _workspaces_dir() -> Path:
    data_dir = Path(os.environ["LOCALAPPDATA"]) if sys.platform == "win32" else Path.home() / ".local" / "share"
    return data_dir / "bhima" / "workspaces"


WORKSPACES_DIR = _workspaces_dir()
DEFAULT_WORKSPACE = "demo"
DEFAULT_TEST_COMMAND = "python -m pytest -q"
MAX_RETRIES = 2            # same CLI, failure fed back
WORKER_TIMEOUT_S = 15 * 60
CHECK_TIMEOUT_S = 5 * 60
CONTEXT_BUDGET_TOKENS = 6000
CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(python -m pytest*)"
CLAUDE_MAX_TURNS = 30

_env_values: dict[str, str] | None = None


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read .env into the harness configuration without exporting it."""
    global _env_values
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
    _env_values = values
    return values


def get(key: str, default: str = "") -> str:
    values = _env_values if _env_values is not None else load_env()
    return os.environ.get(key, values.get(key, default))


def codex_via_wsl() -> bool:
    default = "true" if os.name == "nt" else "false"
    return get("CODEX_VIA_WSL", default).strip().lower() in {"1", "true", "yes", "on"}


def child_env() -> dict[str, str]:
    secret_markers = ("TOKEN", "KEY", "SECRET", "PASSWORD")
    return {name: value for name, value in os.environ.items()
            if not any(marker in name.upper() for marker in secret_markers)}


def ensure_dirs() -> None:
    for d in (STATE_DIR, WORKSPACES_DIR, RUNS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
