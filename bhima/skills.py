"""Skills: reusable how-to notes under skills/<name>/SKILL.md.

Only the index (name + one-line description) goes in every context pack;
a worker loads a full skill on demand. Frontmatter is a small YAML subset:

    ---
    name: python-tests
    description: How to run and fix pytest failures
    tags: [python, tests]
    ---
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"  # tests may point this somewhere else


def parse_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k.strip()] = [t.strip().strip("'\"") for t in v[1:-1].split(",") if t.strip()]
        else:
            meta[k.strip()] = v.strip("'\"")
    body = text[end + 4:].lstrip("\n")
    return meta, body


def all_skills() -> list[dict]:
    out = []
    if not SKILLS_DIR.exists():
        return out
    for f in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        meta.setdefault("name", f.parent.name)
        meta.setdefault("description", "")
        meta.setdefault("tags", [])
        out.append(meta)
    return out


def index() -> str:
    items = all_skills()
    if not items:
        return "(no skills yet)"
    lines = [f"- {s['name']}: {s['description']}" for s in items]
    return "Skills available (ask for one by name to load it in full):\n" + "\n".join(lines)


def load(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    f = SKILLS_DIR / safe / "SKILL.md"
    if not f.exists():
        raise KeyError(f"no skill named {name!r}")
    return f.read_text(encoding="utf-8")


def create(name: str, description: str, body: str, tags: list[str] | None = None) -> Path:
    safe = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    d = SKILLS_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    tag_s = ", ".join(tags or [])
    text = f"---\nname: {safe}\ndescription: {description}\ntags: [{tag_s}]\n---\n\n{body.strip()}\n"
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d / "SKILL.md"


def suggest(history: list, tasks: dict | None = None) -> dict | None:
    """Propose a new skill when the same task kind succeeded 2+ times.

    `history` is a list of RunResult. Kind comes from `tasks[task_id].kind`
    when given, else from a `kind` attribute, else from the task id prefix.
    Returns {"name", "description", "body", "evidence"} or None.
    """
    def kind_of(r) -> str:
        if tasks and r.task_id in tasks:
            return getattr(tasks[r.task_id], "kind", "code")
        return getattr(r, "kind", None) or "code"

    wins = [r for r in history if getattr(r, "ok", False)]
    counts = Counter(kind_of(r) for r in wins)
    existing = {s["name"] for s in all_skills()}
    for kind, n in counts.most_common():
        if n < 2:
            break
        name = f"{kind}-playbook"
        if name in existing:
            continue
        runs = [r for r in wins if kind_of(r) == kind]
        clis = Counter(r.cli for r in runs).most_common(1)[0][0]
        assumptions = [a for r in runs for a in (r.assumptions or [])][:5]
        body = (f"# {kind} playbook\n\nDerived from {n} successful runs "
                f"({', '.join(r.task_id for r in runs)}). Best CLI so far: {clis}.\n\n"
                "## Steps that worked\n- Read the ranked files first; keep the diff small.\n"
                "- Run the test command before reporting.\n- List assumptions under '## Assumptions:'.\n")
        if assumptions:
            body += "\n## Assumptions seen before\n" + "\n".join(f"- {a}" for a in assumptions) + "\n"
        return {"name": name, "description": f"What worked for {kind} tasks ({n} wins)",
                "body": body, "evidence": [r.task_id for r in runs]}
    return None
