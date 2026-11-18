"""Shared data contract for every Bhima module (see ARCHITECTURE.md)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Task:
    id: str
    request_id: str
    title: str
    detail: str
    kind: str  # "code" | "scrape" | "question"
    files_hint: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)


@dataclass
class Plan:
    id: str
    request_id: str
    tasks: list[Task]
    test_command: str
    summary: str
    planned_by: str | None = None


@dataclass
class Route:
    cli: str  # "claude" | "codex"
    model: str
    effort: str  # "low" | "medium" | "high"
    needs_plan: bool
    confidence: float
    source: str  # "glide" | "rules"
    reason: str


@dataclass
class ContextPack:
    task_id: str
    text: str
    files: list[str]
    tokens_est: int


@dataclass
class RunResult:
    task_id: str
    round: int
    cli: str
    ok: bool
    report: str
    assumptions: list[str]
    usage: dict
    duration_s: float
    raw_path: str
    error: str | None = None
    model: str = ""


@dataclass
class CheckResult:
    task_id: str
    tests_passed: bool
    output: str
    assumptions: list[str]


def to_dict(obj) -> dict:
    """Dataclass -> plain dict (nested dataclasses included)."""
    return asdict(obj)


def task_from_dict(d: dict) -> Task:
    return Task(
        id=str(d["id"]),
        request_id=str(d.get("request_id", "")),
        title=d.get("title", ""),
        detail=d.get("detail", ""),
        kind=d.get("kind", "code"),
        files_hint=list(d.get("files_hint") or []),
        acceptance=list(d.get("acceptance") or []),
    )


def plan_from_dict(d: dict) -> Plan:
    return Plan(
        id=str(d["id"]),
        request_id=str(d.get("request_id", "")),
        tasks=[task_from_dict(t) for t in d.get("tasks", [])],
        test_command=d.get("test_command", "python -m pytest -q"),
        summary=d.get("summary", ""),
        planned_by=d.get("planned_by"),
    )
