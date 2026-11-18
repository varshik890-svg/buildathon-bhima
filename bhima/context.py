"""Task -> ContextPack: the text a worker sees before it starts.

The pack is built to fit a token budget (estimated at 4 chars/token):
  1. Task statement and acceptance criteria
  2. "Why these files": the ranking, with the terms that matched
  3. Repo map: tree to depth 2 plus top-level symbols of every .py file
  4. Contents of the best-ranked files, until the budget is used up
  5. Memory snapshot and skill index
  6. Notes from earlier rounds (only when this task failed before)

Ranking is BM25 over files, where each file is a document made of three
fields: its path (weight 3), its symbol names (weight 2) and its body
(weight 1). Identifiers are split (snake_case, camelCase) so a task saying
"parse price" matches `parse_price`. Files named in files_hint are pinned to
the top; tests that pair with a top-ranked file get a boost.
"""
from __future__ import annotations

import ast
import math
import os
import re
from collections import Counter
from pathlib import Path

from bhima.models import ContextPack

CHARS_PER_TOKEN = 4
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".bhima", ".pytest_cache",
             ".mypy_cache", "dist", "build", ".idea", ".vscode", ".tox", ".ruff_cache"}
TEXT_EXT = {".py", ".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".csv",
            ".html", ".js", ".ts", ".css", ".sh", ".ps1", ".sql", ".rst"}
MAX_FILE_BYTES = 200_000
FIELD_WEIGHTS = {"path": 3, "symbols": 2, "body": 1}
K1, B = 1.5, 0.75
HINT_BONUS = 100.0  # files_hint always wins
STOP = set("""a an and are as at be by can do does for from has have how i if in into is it its
of on or our should so that the their them then there this to up use we what when where which
with would you your make sure add new get set all any each not no task file files code please
self def return none true false import class""".split())


def est_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def tokenize(text: str) -> list[str]:
    """Words, with identifiers split: parse_priceList -> parse, price, list."""
    out = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]*|\d+", text):
        parts = re.split(r"_+", raw)
        for p in parts:
            for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", p):
                w = w.lower()
                if len(w) > 1 and w not in STOP:
                    out.append(_stem(w))
    return out


def _stem(w: str) -> str:
    """Tiny suffix stripper so 'prices'/'price', 'sorting'/'sort' meet."""
    for suf in ("ing", "ers", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


# --------------------------------------------------------------- repo scan
def iter_files(root: Path) -> list[Path]:
    """Text files under root, pruning vendored/hidden dirs while walking."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                if p.suffix.lower() in TEXT_EXT and p.stat().st_size <= MAX_FILE_BYTES:
                    files.append(p)
            except OSError:
                continue
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def py_symbols(text: str) -> list[str]:
    """Top-level functions/classes (and methods of classes) via ast."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return re.findall(r"^(?:def|class)\s+(\w+)", text, re.M)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            out.append(f"def {node.name}({args})")
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out.append(f"class {node.name}" + (f" [{', '.join(methods[:8])}]" if methods else ""))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper():
            out.append(node.targets[0].id)
    return out


def repo_map(root: Path, files: list[Path], symbols: dict[str, list[str]], depth: int = 2) -> str:
    lines = [f"{root.name}/"]
    shown_dirs = set()
    for f in files:
        rel = f.relative_to(root)
        for i in range(1, min(len(rel.parts), depth)):
            d = "/".join(rel.parts[:i])
            if d not in shown_dirs:
                shown_dirs.add(d)
                lines.append("  " * i + rel.parts[i - 1] + "/")
        if len(rel.parts) <= depth:
            key = rel.as_posix()
            syms = symbols.get(key)
            suffix = f"  :: {'; '.join(syms[:10])}" if syms else ""
            lines.append("  " * len(rel.parts) + rel.parts[-1] + suffix)
    return "\n".join(lines)


# --------------------------------------------------------------- BM25
class BM25:
    """Okapi BM25 over pre-tokenized documents."""

    def __init__(self, docs: dict[str, list[str]], k1: float = K1, b: float = B):
        self.k1, self.b = k1, b
        self.tf = {d: Counter(toks) for d, toks in docs.items()}
        self.len = {d: len(toks) for d, toks in docs.items()}
        self.avg = (sum(self.len.values()) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], doc: str) -> tuple[float, dict[str, float]]:
        tf, dl = self.tf[doc], self.len[doc]
        total, contrib = 0.0, {}
        for t in set(query):
            f = tf.get(t, 0)
            if not f:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1))
            s = self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            contrib[t] = s
            total += s
        return total, contrib


def _doc_tokens(rel: str, symbols: list[str], body: str) -> list[str]:
    toks = tokenize(rel.replace("/", " ")) * FIELD_WEIGHTS["path"]
    toks += tokenize(" ".join(symbols)) * FIELD_WEIGHTS["symbols"]
    toks += tokenize(body)
    return toks


def _norm_hint(h: str) -> str:
    return h.replace("\\", "/").strip().lstrip("./")


def rank_files(task, root: Path, files: list[Path] | None = None,
               texts: dict[str, str] | None = None) -> list[dict]:
    """Return [{"path", "score", "why"}], best first. Explainable by design."""
    root = Path(root)
    files = files if files is not None else iter_files(root)
    texts = texts if texts is not None else {}
    docs, syms = {}, {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        if rel not in texts:
            texts[rel] = f.read_text(encoding="utf-8", errors="replace")
        syms[rel] = py_symbols(texts[rel]) if rel.endswith(".py") else []
        docs[rel] = _doc_tokens(rel, syms[rel], texts[rel])
    if not docs:
        return []
    query = tokenize(" ".join([task.title, task.detail or "", " ".join(task.acceptance or []),
                               " ".join(task.files_hint or [])]))
    bm = BM25(docs)
    hints = [_norm_hint(h) for h in (task.files_hint or []) if h.strip()]
    ranked = []
    for rel in docs:
        s, contrib = bm.score(query, rel)
        why = []
        if contrib:
            top = sorted(contrib.items(), key=lambda kv: -kv[1])[:4]
            why.append("matches " + ", ".join(f"'{t}'" for t, _ in top))
        if any(rel == h or rel.endswith("/" + h) or h.endswith("/" + rel) for h in hints):
            s += HINT_BONUS
            why.insert(0, "named in files_hint")
        elif any(h and Path(h).name == Path(rel).name for h in hints):
            s += HINT_BONUS / 2
            why.insert(0, "same filename as a files_hint")
        ranked.append({"path": rel, "score": s, "why": why})
    ranked.sort(key=lambda r: (-r["score"], r["path"]))
    # Pair tests with their module: test_foo.py <-> foo.py
    top_paths = [r["path"] for r in ranked[:3] if r["score"] > 0]
    stems = {Path(p).stem.removeprefix("test_") for p in top_paths if p.endswith(".py")}
    bonus = 0.5 * min(ranked[0]["score"], HINT_BONUS) if ranked else 0.0
    for r in ranked:
        if r["path"] in top_paths or not r["path"].endswith(".py"):
            continue
        stem = Path(r["path"]).stem
        partner = stem[5:] if stem.startswith("test_") else stem
        if partner in stems:
            r["score"] += bonus
            r["why"].append(f"pairs with top file for '{partner}' (module/test)")
    ranked.sort(key=lambda r: (-r["score"], r["path"]))
    return ranked


# --------------------------------------------------------------- pack
def _notes_from_history(task, history: list | None) -> str:
    if not history:
        return ""
    mine = [r for r in history if getattr(r, "task_id", None) == task.id and not getattr(r, "ok", True)]
    if not mine:
        return ""
    lines = []
    for r in mine[-3:]:
        err = (r.error or "").strip() or "tests failed"
        rep = " ".join((r.report or "").split())[:400]
        lines.append(f"- Round {r.round} on {r.cli} failed: {err[:300]}")
        if rep:
            lines.append(f"  Its report said: {rep}")
        if r.assumptions:
            lines.append(f"  It assumed: {'; '.join(r.assumptions[:4])}")
    lines.append("- Do not repeat the same approach; read the failure output above first.")
    return "\n".join(lines)


def _safe(fn, default=""):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a broken memory file must not stop a run
        return f"({default or 'unavailable'}: {e})"


def build_pack(task, project_dir: Path, budget_tokens: int = 6000, history: list | None = None) -> ContextPack:
    from bhima import memory, skills

    root = Path(project_dir)
    files = iter_files(root)
    texts = {f.relative_to(root).as_posix(): f.read_text(encoding="utf-8", errors="replace") for f in files}
    symbols = {rel: py_symbols(t) for rel, t in texts.items() if rel.endswith(".py")}
    ranked = rank_files(task, root, files, texts)

    head = [f"# Task: {task.title}", "", task.detail or "", ""]
    if task.acceptance:
        head += ["## Acceptance"] + [f"- {a}" for a in task.acceptance] + [""]
    notes = _notes_from_history(task, history)
    if notes:
        head += ["## Notes from earlier rounds", notes, ""]
    tail = ["## Memory", _safe(memory.snapshot, "memory"), "", "## Skills", _safe(skills.index, "skills"), ""]
    rmap = ["## Repo map (depth 2, top-level symbols)", repo_map(root, files, symbols), ""]

    fixed = "\n".join(head + rmap + tail)
    remaining = budget_tokens * CHARS_PER_TOKEN - len(fixed) - 600  # 600 chars for the why-list
    if remaining < 0:  # repo map too big: shrink it to a flat list of top files
        rmap = ["## Repo map (trimmed)"] + [r["path"] for r in ranked[:40]] + [""]
        fixed = "\n".join(head + rmap + tail)
        remaining = budget_tokens * CHARS_PER_TOKEN - len(fixed) - 600

    chosen, bodies, why_lines = [], [], []
    relevant = [r for r in ranked if r["score"] > 0][:8]
    for r in relevant:
        body = texts[r["path"]]
        block = f"### {r['path']}\n```\n{body}\n```\n"
        if len(block) > remaining:
            if remaining < 400:
                why_lines.append(f"- {r['path']} (score {r['score']:.1f}) skipped: over budget")
                continue
            cut = body[: remaining - 120]
            block = f"### {r['path']} (truncated to fit budget)\n```\n{cut}\n...\n```\n"
        remaining -= len(block)
        chosen.append(r["path"])
        bodies.append(block)
        why_lines.append(f"- {r['path']} (score {r['score']:.1f}): {'; '.join(r['why']) or 'weak match'}")
    if not relevant:
        why_lines.append("- No file matched the task words; rely on the repo map.")

    text = "\n".join(head + ["## Why these files"] + why_lines + [""] + rmap + ["## Files"] + bodies + tail)
    return ContextPack(task_id=task.id, text=text, files=chosen, tokens_est=est_tokens(text))
