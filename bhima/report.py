"""Plain-text tables: rounds, tokens, time per task, and the baseline comparison."""
from __future__ import annotations

import json

from . import config
from .store import Store


def tokens_of(usage: dict) -> int:
    u = usage or {}
    return sum(int(u.get(k, 0) or 0) for k in
               ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    sep = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(cells[0]), sep] + [fmt(r) for r in cells[1:]])


def task_rows(store: Store, limit: int = 20) -> list[list]:
    rows = []
    for req in store.list_requests(limit=limit):
        for t in store.tasks_for_request(req["id"]):
            runs = store.runs_for_task(t["id"])
            if not runs:
                rows.append([t["id"], t["data"].get("title", "")[:32], t["status"], 0, "-", 0, "0.0", "-"])
                continue
            toks = sum(tokens_of(r["data"].get("usage", {})) for r in runs)
            secs = sum(float(r["data"].get("duration_s", 0) or 0) for r in runs)
            clis = "->".join(dict.fromkeys(f"{r['cli']}:{r['model']}/{r['effort']}" for r in runs))
            last = runs[-1]["data"]
            rows.append([t["id"], t["data"].get("title", "")[:32], t["status"], len(runs), clis, toks,
                         f"{secs:.1f}", "pass" if last.get("tests_passed") else "fail"])
    return rows


def render(store: Store | None = None) -> str:
    own = store is None
    store = store or Store()
    try:
        rows = task_rows(store)
        if not rows:
            return "No runs yet."
        out = [table(["task", "title", "status", "rounds", "route", "tokens", "secs", "tests"], rows)]
        total_tok = sum(r[5] for r in rows)
        total_rounds = sum(r[3] for r in rows)
        total_s = sum(float(r[6]) for r in rows)
        out.append(f"\nTotal: {len(rows)} task(s), {total_rounds} round(s), {total_tok} tokens, {total_s:.0f}s")
        base = config.STATE_DIR / "baseline.json"
        if base.exists():
            out.append("\nBaseline (last run):\n" + render_baseline(json.loads(base.read_text(encoding="utf-8"))))
        return "\n".join(out)
    finally:
        if own:
            store.close()


def render_baseline(results: list[dict]) -> str:
    rows = []
    for r in results:
        p, h = r.get("plain", {}), r.get("harness", {})
        rows.append([r["task_id"], r.get("title", "")[:28],
                     _pf(p.get("verified")), p.get("rounds", 1), p.get("tokens", 0), f"{p.get('secs', 0):.0f}",
                     _pf(h.get("verified")), h.get("rounds", 0), h.get("tokens", 0), f"{h.get('secs', 0):.0f}"])
    head = ["task", "title", "plain ok", "rnd", "tokens", "secs", "bhima ok", "rnd", "tokens", "secs"]
    text = table(head, rows)
    pn = sum(1 for r in results if r.get("plain", {}).get("verified"))
    hn = sum(1 for r in results if r.get("harness", {}).get("verified"))
    return text + f"\n\nHidden acceptance tests passed: plain claude -p {pn}/{len(results)}, bhima {hn}/{len(results)}"


def _pf(v) -> str:
    return "-" if v is None else ("PASS" if v else "FAIL")
