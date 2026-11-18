"""Workers wrap the coding CLIs. get_worker(name) returns one."""
from __future__ import annotations


def get_worker(name: str):
    if name == "claude":
        from .claude import ClaudeWorker
        return ClaudeWorker()
    if name == "codex":
        from .codex import CodexWorker
        return CodexWorker()
    raise ValueError(f"unknown worker {name!r}")
