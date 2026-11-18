"""Task -> Route: which CLI, which model, how much effort.

First we ask GLiDE (a small local decision model) three typed questions about
a compact state. If GLiDE is down or unsure (confidence < MIN_CONF) we use a
rule table. Either way the GLiDE answer is kept in `last_decision` so the
store can log it and we can later compare GLiDE with the rules.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from bhima import glide_client
from bhima.models import Route

MIN_CONF = 0.35

ROUTES = {
    "claude_haiku": ("claude", "haiku"),
    "claude_sonnet": ("claude", "sonnet"),
    "claude_opus": ("claude", "opus"),
    "codex": ("codex", None),  # model filled from config
}
EFFORTS = ["low", "medium", "high"]
LADDER = ["claude_haiku", "codex", "claude_sonnet", "claude_opus"]  # cheap -> strong

# Filled after every route() call: {"state", "answers", "used", "route"}.
last_decision: dict | None = None

_BIG_WORDS = re.compile(r"\b(refactor|redesign|rewrite|migrat\w*|architect\w*|across|multi[- ]file|"
                        r"several files|all (?:the )?files|module|framework|integrat\w*)\b", re.I)
_SMALL_WORDS = re.compile(r"\b(typo|rename|bug|fix|off[- ]by[- ]one|small|one[- ]line|tweak|docstring)\b", re.I)


def codex_model() -> str:
    try:
        from bhima import config

        m = getattr(config, "CODEX_MODEL", None) or config.get("CODEX_MODEL")
        if m:
            return str(m)
    except Exception:
        pass
    return os.environ.get("CODEX_MODEL", "gpt-5-codex")


def _route_key(cli: str, model: str) -> str:
    if cli == "codex":
        return "codex"
    return f"claude_{model}" if f"claude_{model}" in ROUTES else "claude_sonnet"


def task_size(task) -> str:
    text = f"{task.title} {task.detail}"
    files = len(task.files_hint or [])
    if files >= 3 or _BIG_WORDS.search(text) or len(text) > 600:
        return "large"
    if files <= 1 and (_SMALL_WORDS.search(text) or len(text) < 160):
        return "small"
    return "medium"


def summarize_history(task, history: list) -> dict:
    """Per-route record for this task and overall: tries, wins, failures."""
    mine = [r for r in history if r.task_id == task.id]
    per_route: dict[str, dict] = defaultdict(lambda: {"tries": 0, "ok": 0})
    for r in history:
        model = (r.usage or {}).get("model", "") if isinstance(r.usage, dict) else ""
        key = "codex" if r.cli == "codex" else _route_key("claude", model or "sonnet")
        per_route[key]["tries"] += 1
        per_route[key]["ok"] += int(bool(r.ok))
    failures = [r for r in mine if not r.ok]
    return {
        "prior_failures": len(failures),
        "failed_clis": sorted({r.cli for r in failures}),
        "last_error": (failures[-1].error or "tests failed")[:200] if failures else None,
        "past_results": dict(per_route),
    }


def build_state(task, history: list) -> dict:
    h = summarize_history(task, history)
    return {
        "task_kind": task.kind,
        "title": task.title,
        "detail": (task.detail or "")[:600],
        "size": task_size(task),
        "files_hint_count": len(task.files_hint or []),
        "acceptance_count": len(task.acceptance or []),
        **h,
    }


QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Which coding agent and model should do this task? Prefer the cheapest one likely to succeed; "
                        "move to a stronger one after failures.",
        "criteria": {
            "claude_haiku": "Claude Haiku: fast and cheap; questions, scraping, trivial edits",
            "claude_sonnet": "Claude Sonnet: solid general coder; multi-file changes and refactors",
            "claude_opus": "Claude Opus: strongest; hard bugs or tasks that already failed twice",
            "codex": "Codex: focused single-file bug fixes and test-driven edits",
        },
    },
    "needs_plan": {
        "type": "noul",
        "instructions": "Should the agent write a short plan before editing code?",
    },
    "effort": {
        "type": "score",
        "instructions": "How much reasoning effort does this task need?",
        "criteria": ["low: lookup, scrape or one-line change",
                     "medium: a contained bug fix or small feature",
                     "high: multi-file change, refactor or a task that failed before"],
    },
}


def rules_route(task, history: list) -> tuple[str, str, bool, str]:
    """Rule table. Returns (route_key, effort, needs_plan, reason)."""
    st = build_state(task, history)
    kind, size, fails = st["task_kind"], st["size"], st["prior_failures"]
    if kind in ("scrape", "question"):
        key, effort, why = "claude_haiku", "low", f"{kind} task -> cheap model"
    elif size == "small":
        key, effort, why = "codex", "medium", "small contained code change -> codex"
    elif size == "large":
        key, effort, why = "claude_sonnet", "high", "multi-file/refactor -> sonnet, high effort"
    else:
        key, effort, why = "claude_sonnet", "medium", "medium code task -> sonnet"
    needs_plan = size == "large" or len(task.acceptance or []) >= 3
    if fails >= 1:
        effort = EFFORTS[min(2, EFFORTS.index(effort) + 1)]
        i = LADDER.index(key)
        if fails == 1 and key == "claude_haiku":
            key = "claude_sonnet"
        why += f"; {fails} prior failure(s) -> effort {effort}"
        needs_plan = True
        if fails >= 2:
            failed_clis = set(st["failed_clis"])
            cli = ROUTES[key][0]
            if cli in failed_clis:
                key = "codex" if cli == "claude" else "claude_opus"
                why += f"; switch CLI away from {cli}"
            elif i < len(LADDER) - 1:
                key = LADDER[i + 1]
                why += f"; escalate to {key}"
    return key, effort, needs_plan, why


def _apply_failure_guard(key: str, task, history: list) -> tuple[str, str]:
    """GLiDE must not send a task back to a CLI that already failed it twice."""
    st = summarize_history(task, history)
    if st["prior_failures"] >= 2 and ROUTES[key][0] in st["failed_clis"]:
        new = "codex" if ROUTES[key][0] == "claude" else "claude_opus"
        return new, f"; guard: {ROUTES[key][0]} failed twice -> {new}"
    return key, ""


def route(task, history: list | None = None, use_glide: bool = True) -> Route:
    global last_decision
    history = history or []
    state = build_state(task, history)
    answers = glide_client.decide(state, QUESTIONS) if use_glide else None
    r_key, r_effort, r_plan, r_why = rules_route(task, history)

    used = "rules"
    key, effort, needs_plan, conf = r_key, r_effort, r_plan, 0.0
    parts = []
    if answers:
        ch = answers.get("route") or {}
        sc = answers.get("effort") or {}
        npq = answers.get("needs_plan") or {}
        ch_conf = float(ch.get("confidence") or 0)
        sc_conf = float(sc.get("confidence") or 0)
        if ch.get("choice") in ROUTES and ch_conf >= MIN_CONF:
            key, used = ch["choice"], "glide"
            conf = ch_conf
            parts.append(f"GLiDE chose {key} (conf {ch_conf:.2f})")
        else:
            parts.append(f"GLiDE route unsure ({ch.get('choice')}, conf {ch_conf:.2f}) -> rules")
        if sc.get("score") is not None and sc_conf >= MIN_CONF:
            effort = EFFORTS[max(0, min(2, int(sc["score"])))]
            used = "glide"
            conf = max(conf, sc_conf)
            parts.append(f"effort {effort} (conf {sc_conf:.2f})")
        if "noul" in npq and used == "glide":
            needs_plan = float(npq["noul"]) >= 0.5 or state["prior_failures"] > 0
        if used == "glide":
            key, guard = _apply_failure_guard(key, task, history)
            if guard:
                parts.append(guard.lstrip("; "))
            if state["prior_failures"] and EFFORTS.index(effort) < EFFORTS.index(r_effort):
                effort = r_effort  # never lower effort after a failure
                parts.append("effort raised to rules level after failure")
    else:
        parts.append("GLiDE unavailable")

    if used == "rules":
        conf = 1.0
        reason = f"rules: {r_why}" + (f" ({parts[0]})" if parts else "")
    else:
        reason = "; ".join(parts) + f" | rules would pick {r_key}/{r_effort}"

    cli, model = ROUTES[key]
    model = model or codex_model()
    result = Route(cli=cli, model=model, effort=effort, needs_plan=bool(needs_plan),
                   confidence=round(conf, 3), source=used, reason=reason)
    last_decision = {"task_id": task.id, "state": state, "answers": answers, "used": used,
                     "rules": {"route": r_key, "effort": r_effort, "needs_plan": r_plan},
                     "route": {"cli": cli, "model": model, "effort": effort}}
    return result
