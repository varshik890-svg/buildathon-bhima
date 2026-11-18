"""request -> Plan with 1-3 Tasks.

Tries the local Talker, Claude Haiku, and then the offline heuristic.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from bhima import config, talker
from bhima.models import Plan, Task
KINDS = ("code", "scrape", "question")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tasks": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "files_hint": {"type": "array", "items": {"type": "string"}},
                    "acceptance": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "detail", "kind", "files_hint", "acceptance"],
            },
        },
    },
    "required": ["summary", "tasks"],
}

PROMPT = """You are the planner for a coding harness. Split the user's request into 1 to 3 small,
independent tasks for a coding agent working in the project below. Prefer ONE task unless the
request clearly has separate parts. kind is "code" (edit files), "scrape" (fetch and sort web
data) or "question" (answer without editing). files_hint lists existing paths likely involved.
acceptance lists checkable outcomes (tests that must pass, behaviour to show).

Project files:
{files}

Request: {request}
"""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _list_files(project_dir: Path, limit: int = 60) -> list[str]:
    try:
        from bhima.context import iter_files

        return [p.relative_to(project_dir).as_posix() for p in iter_files(project_dir)][:limit]
    except Exception:
        return []


def _to_plan(data: dict, request: str, project_dir: Path, request_id: str) -> Plan:
    known = set(_list_files(project_dir, 500))
    tasks = []
    for t in (data.get("tasks") or [])[:3]:
        kind = t.get("kind") if t.get("kind") in KINDS else "code"
        hints = [h.replace("\\", "/").lstrip("./") for h in (t.get("files_hint") or []) if isinstance(h, str)]
        if known:
            hints = [h for h in hints if h in known] or hints[:3]
        tasks.append(Task(id=_new_id("t"), request_id=request_id,
                          title=str(t.get("title") or request[:80]).strip(),
                          detail=str(t.get("detail") or request).strip(),
                          kind=kind, files_hint=hints[:6],
                          acceptance=[str(a) for a in (t.get("acceptance") or [])][:6]))
    if not tasks:
        raise ValueError("plan had no tasks")
    return Plan(id=_new_id("p"), request_id=request_id, tasks=tasks,
                test_command=config.DEFAULT_TEST_COMMAND,
                summary=str(data.get("summary") or request[:120]))


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            d = json.loads(c)
            if isinstance(d, dict):
                return d
        except ValueError:
            continue
    return None


def _claude_exe() -> str | None:
    return shutil.which("claude") or shutil.which("claude.exe")


def _valid_talker_plan(data: object) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return False
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 3:
        return False
    for task in tasks:
        if not isinstance(task, dict) or task.get("kind") not in KINDS:
            return False
        if not all(isinstance(task.get(key), str) for key in ("title", "detail")):
            return False
        for key in ("files_hint", "acceptance"):
            items = task.get(key)
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                return False
    return True


def make_plan(request: str, project_dir: Path, request_id: str | None = None, timeout: int = 120) -> Plan:
    project_dir = Path(project_dir)
    request_id = request_id or _new_id("r")
    prompt = PROMPT.format(files="\n".join(_list_files(project_dir)) or "(empty)", request=request)
    response = talker.chat([{"role": "user", "content": prompt}], json_schema=PLAN_SCHEMA)
    try:
        data = json.loads(response) if response is not None else None
    except (ValueError, RecursionError):
        data = None
    if _valid_talker_plan(data):
        plan = _to_plan(data, request, project_dir, request_id)
        plan.planned_by = "talker-local"
        return plan

    exe = _claude_exe()
    if exe:
        cmd = [exe, "-p", "--output-format", "json", "--model", "haiku", "--effort", "low",
               "--tools", "", "--disallowedTools", "*", "--strict-mcp-config",
               "--json-schema", json.dumps(PLAN_SCHEMA)]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, cwd=str(project_dir) if project_dir.exists() else None,
                                  env=config.child_env())
            outer = json.loads(proc.stdout or "{}")
            data = outer.get("structured_output")
            if isinstance(data, str):
                data = _extract_json(data)
            if not isinstance(data, dict):
                data = _extract_json(str(outer.get("result") or ""))
            if isinstance(data, dict) and data.get("tasks"):
                plan = _to_plan(data, request, project_dir, request_id)
                plan.summary = plan.summary + " [planner: claude haiku]"
                plan.planned_by = "claude-haiku"
                return plan
        except (subprocess.SubprocessError, OSError, ValueError, AttributeError, TypeError):
            pass
    return make_plan_offline(request, project_dir, request_id)


# ----------------------------------------------------------- offline fallback
_SCRAPE = re.compile(r"\b(scrape|crawl|fetch|download)\b|https?://", re.I)
_QUESTION = re.compile(r"^\s*(what|why|how|which|who|where|when|explain|describe|summari[sz]e|is|does|can)\b|\?\s*$",
                       re.I)
_SPLIT = re.compile(r"(?:;|\n+|\.\s+(?:then|also)\s+|\b(?:and then|and also|then)\b)", re.I)


def _guess_kind(text: str) -> str:
    if _SCRAPE.search(text):
        return "scrape"
    if _QUESTION.search(text) and not re.search(r"\b(fix|add|implement|change|write|refactor|make)\b", text, re.I):
        return "question"
    return "code"


def _guess_hints(text: str, project_dir: Path) -> list[str]:
    files = _list_files(project_dir, 500)
    hints = []
    for f in files:
        name = Path(f).name
        stem = Path(f).stem
        if f in text or name in text or (len(stem) > 3 and re.search(rf"\b{re.escape(stem)}\b", text, re.I)):
            hints.append(f)
    if not hints and files:
        try:
            from types import SimpleNamespace

            from bhima.context import rank_files

            fake = SimpleNamespace(title=text, detail="", acceptance=[], files_hint=[])
            hints = [r["path"] for r in rank_files(fake, project_dir)[:2] if r["score"] > 0]
        except Exception:
            hints = []
    return hints[:4]


def make_plan_offline(request: str, project_dir: Path, request_id: str | None = None) -> Plan:
    project_dir = Path(project_dir)
    request_id = request_id or _new_id("r")
    parts = [p.strip(" .,-") for p in _SPLIT.split(request) if p and p.strip(" .,-")]
    parts = [p for p in parts if len(p) > 8][:3] or [request.strip()]
    tasks = []
    for p in parts:
        kind = _guess_kind(p)
        acceptance = []
        if kind == "code":
            acceptance.append(f"`{config.DEFAULT_TEST_COMMAND}` passes")
        elif kind == "scrape":
            acceptance.append("rows are extracted, sorted and saved as JSON or CSV")
        else:
            acceptance.append("answer cites the files it relied on")
        tasks.append(Task(id=_new_id("t"), request_id=request_id, title=p[:80], detail=p, kind=kind,
                          files_hint=_guess_hints(p, project_dir) if kind != "scrape" else [],
                          acceptance=acceptance))
    return Plan(id=_new_id("p"), request_id=request_id, tasks=tasks, test_command=config.DEFAULT_TEST_COMMAND,
                summary=f"{len(tasks)} task(s) from request [planner: offline heuristic]", planned_by="offline")
