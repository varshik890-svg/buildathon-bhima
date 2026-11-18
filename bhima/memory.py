"""Bounded, human-approved memory (idea from Hermes-style agents, rebuilt here).

Two files under memory/:
  MEMORY.md  facts about the project and lessons learned   (cap 2200 chars)
  USER.md    facts about the human: preferences, style      (cap 1375 chars)
Entries are separated by "\\n§\\n". The caps keep the block small enough to put
in every context pack; when a write would overflow, it fails and lists the
current entries so a human (or agent) can patch or remove one first.
"""
from __future__ import annotations

import re
from pathlib import Path

SEP = "\n§\n"
CAPS = {"MEMORY.md": 2200, "USER.md": 1375}
ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "memory"  # tests may point this somewhere else


class MemoryFull(ValueError):
    """Raised when a write would push a file over its cap."""


def _path(target: str) -> Path:
    name = target if target.endswith(".md") else target.upper() + ".md"
    if name not in CAPS:
        raise ValueError(f"unknown memory file {target!r}; use one of {list(CAPS)}")
    return MEMORY_DIR / name


def _name(target: str) -> str:
    return _path(target).name


def entries(target: str = "MEMORY.md") -> list[str]:
    p = _path(target)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8").strip()
    return [e.strip() for e in text.split(SEP.strip()) if e.strip()]


def _render(items: list[str]) -> str:
    return SEP.join(items)


def _write(target: str, items: list[str]) -> None:
    text = _render(items)
    cap = CAPS[_name(target)]
    if len(text) > cap:
        listing = "\n".join(f"  [{i}] {e}" for i, e in enumerate(entries(target)))
        raise MemoryFull(
            f"{_name(target)} would be {len(text)} chars (cap {cap}). "
            f"Patch or remove an entry first. Current entries:\n{listing}")
    p = _path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _clean(entry: str) -> str:
    entry = entry.replace("§", "").strip()
    return re.sub(r"\n{3,}", "\n\n", entry)


def add(entry: str, target: str = "MEMORY.md") -> list[str]:
    entry = _clean(entry)
    items = entries(target)
    if not entry or entry in items:
        return items
    items.append(entry)
    _write(target, items)
    return items


def _find(items: list[str], match: str | int) -> int:
    if isinstance(match, int):
        if 0 <= match < len(items):
            return match
        raise KeyError(f"no entry #{match}")
    hits = [i for i, e in enumerate(items) if match.lower() in e.lower()]
    if len(hits) != 1:
        raise KeyError(f"{len(hits)} entries match {match!r}; be more specific")
    return hits[0]


def patch(match: str | int, new_entry: str, target: str = "MEMORY.md") -> list[str]:
    """Replace the single entry containing `match` (substring or index)."""
    items = entries(target)
    items[_find(items, match)] = _clean(new_entry)
    _write(target, items)
    return items


def remove(match: str | int, target: str = "MEMORY.md") -> list[str]:
    items = entries(target)
    items.pop(_find(items, match))
    _write(target, items)
    return items


def usage(target: str = "MEMORY.md") -> tuple[int, int]:
    return len(_render(entries(target))), CAPS[_name(target)]


def snapshot() -> str:
    """Frozen text block for the context pack (taken once per task)."""
    parts = []
    for name, title in (("MEMORY.md", "Project memory"), ("USER.md", "About the user")):
        used, cap = usage(name)
        pct = round(100 * used / cap) if cap else 0
        items = entries(name)
        body = "\n".join(f"- {e}" for e in items) if items else "- (empty)"
        parts.append(f"### {title} ({used}/{cap} chars, {pct}% used)\n{body}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- proposals
_LESSON_PATTERNS = [
    r"(?:lesson|learned|note|remember|convention|gotcha)s?\s*[:\-]\s*(.+)",
    r"(?:root cause|the (?:bug|problem|issue) was)\s*[:\-]?\s*(.+)",
    r"(?:fixed|fixes)\s+(?:by|via)\s+(.+)",
    r"(?:always|never)\s+.+",
]


def _short(s: str, n: int = 160) -> str:
    s = " ".join(s.split()).strip(" .-*#")
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def propose(run) -> list[str]:
    """Heuristically derive 0-3 candidate memory entries from a RunResult.

    No LLM call: we look for explicit lesson phrases in the worker report,
    record a failure that was fixed on a later round, and keep notable
    assumptions. A human approves before `apply` writes anything.
    """
    out: list[str] = []
    report = getattr(run, "report", "") or ""
    for line in report.splitlines():
        line = line.strip()
        for pat in _LESSON_PATTERNS:
            m = re.search(pat, line, re.IGNORECASE)
            if m and len(line) > 15:
                text = m.group(1) if m.groups() else m.group(0)
                out.append(_short(text))
                break
        if len(out) >= 2:
            break
    ok = getattr(run, "ok", False)
    rnd = getattr(run, "round", 0) or 0
    task_id = getattr(run, "task_id", "?")
    if ok and rnd > 0:
        out.append(_short(f"Task {task_id} needed {rnd + 1} rounds on {getattr(run, 'cli', '?')}; "
                          f"first attempt failed: {getattr(run, 'error', None) or 'tests failed'}"))
    elif not ok and getattr(run, "error", None):
        out.append(_short(f"{getattr(run, 'cli', '?')} failed on {task_id}: {run.error}"))
    for a in getattr(run, "assumptions", []) or []:
        if re.search(r"\b(conven|always|python|pytest|style|use[sd]?)\b", a, re.I):
            out.append(_short("Assumed: " + a))
            break
    seen, uniq = set(), []
    existing = {e.lower() for e in entries("MEMORY.md")}
    for e in out:
        k = e.lower()
        if k not in seen and k not in existing and len(e) > 10:
            seen.add(k)
            uniq.append(e)
    return uniq[:3]


def apply(items: list[str], target: str = "MEMORY.md") -> list[str]:
    """Write approved entries. Raises MemoryFull (with listing) on overflow."""
    current = entries(target)
    for e in items:
        e = _clean(e)
        if e and e not in current:
            current.append(e)
    _write(target, current)
    return current
