"""Fetch a page, extract tables and lists, sort, save as JSON/CSV. Stdlib only.

    python -m bhima.scrape <url> --sort <key> [--desc] [--csv out.csv] [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

USER_AGENT = "bhima-harness"
MAX_BYTES = 2_000_000
TIMEOUT = 15


def fetch(url: str, timeout: float = TIMEOUT, max_bytes: int = MAX_BYTES) -> str:
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("only http(s) URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
        charset = resp.headers.get_content_charset() or "utf-8"
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return raw.decode(charset, errors="replace")


class _Extractor(HTMLParser):
    """Collects every <table> (as rows of cells) and every <ul>/<ol> (as items)."""

    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[tuple[list[str], bool]]] = []  # rows: (cells, is_header_row)
        self.lists: list[list[str]] = []
        self._tstack: list[list] = []
        self._row: list[str] | None = None
        self._row_hdr = False
        self._cell: list[str] | None = None
        self._lstack: list[list[str]] = []
        self._item: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "table":
            self._tstack.append([])
        elif tag == "tr" and self._tstack:
            self._row, self._row_hdr = [], True
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "td":
                self._row_hdr = False
        elif tag in ("ul", "ol") and not self._tstack:
            self._lstack.append([])
        elif tag == "li" and self._lstack:
            self._item = []
        elif tag == "br":
            self._text(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_norm("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._tstack:
            if any(self._row):
                self._tstack[-1].append((self._row, self._row_hdr))
            self._row = None
        elif tag == "table" and self._tstack:
            t = self._tstack.pop()
            if t:
                self.tables.append(t)
        elif tag == "li" and self._item is not None and self._lstack:
            text = _norm("".join(self._item))
            if text:
                self._lstack[-1].append(text)
            self._item = None
        elif tag in ("ul", "ol") and self._lstack:
            items = self._lstack.pop()
            if items:
                self.lists.append(items)

    def handle_data(self, data):
        if not self._skip:
            self._text(data)

    def _text(self, s):
        if self._cell is not None:
            self._cell.append(s)
        elif self._item is not None:
            self._item.append(s)


def _norm(s: str) -> str:
    return " ".join(s.split())


def _table_to_dicts(rows: list[tuple[list[str], bool]], table_no: int) -> list[dict]:
    if not rows:
        return []
    first, is_hdr = rows[0]
    if is_hdr or len(rows) > 1:
        header, body = first, rows[1:]
    else:
        header, body = [], rows
    header = [h or f"col{i + 1}" for i, h in enumerate(header)]
    seen: dict[str, int] = {}
    uniq = []
    for h in header:  # make duplicate header names unique
        seen[h] = seen.get(h, 0) + 1
        uniq.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    out = []
    for cells, _ in body:
        width = max(len(uniq), len(cells))
        keys = uniq + [f"col{i + 1}" for i in range(len(uniq), width)]
        row = {keys[i]: (cells[i] if i < len(cells) else "") for i in range(width)}
        row["_table"] = table_no
        out.append(row)
    return out


def parse(html: str) -> list[dict]:
    """Extract rows from all tables, then items from all lists."""
    p = _Extractor()
    p.feed(html)
    p.close()
    rows: list[dict] = []
    for i, t in enumerate(p.tables, 1):
        rows.extend(_table_to_dicts(t, i))
    for i, items in enumerate(p.lists, 1):
        rows.extend({"item": it, "list": i} for it in items)
    return rows


_NUM = re.compile(r"^[^\d\-+.]*([-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+)\s*([kmb%]?)\b", re.I)
_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}


def numeric(v) -> float | None:
    """'1,200' -> 1200, '$15.5' -> 15.5, '3.2k' -> 3200, '42%' -> 42; else None."""
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.match(str(v).strip().replace("−", "-").replace("–", "-"))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return n * _MULT.get(m.group(2).lower(), 1)


def _resolve_key(rows: list[dict], key: str) -> str:
    keys = {k for r in rows for k in r}
    if key in keys:
        return key
    low = {k.lower(): k for k in keys}
    if key.lower() in low:
        return low[key.lower()]
    for k in keys:
        if key.lower() in k.lower():
            return k
    raise KeyError(f"no column like {key!r}; columns: {sorted(keys)}")


def sort_rows(rows: list[dict], key: str, desc: bool = False) -> list[dict]:
    """Numeric-aware sort. Numbers sort before text; blanks always last."""
    if not rows:
        return rows
    k = _resolve_key(rows, key)
    filled = [r for r in rows if str(r.get(k, "")).strip()]
    blank = [r for r in rows if not str(r.get(k, "")).strip()]
    nums = [r for r in filled if numeric(r[k]) is not None]
    text = [r for r in filled if numeric(r[k]) is None]
    nums.sort(key=lambda r: numeric(r[k]), reverse=desc)
    text.sort(key=lambda r: str(r[k]).lower(), reverse=desc)
    return nums + text + blank


def scrape(url: str, sort_by: str | None = None, desc: bool = False) -> list[dict]:
    """url may be http(s) or a local .html path (handy for saved pages)."""
    if not re.match(r"^https?://", url, re.I) and os.path.isfile(url):
        with open(url, encoding="utf-8", errors="replace") as f:
            html = f.read(MAX_BYTES)
    else:
        html = fetch(url)
    rows = parse(html)
    if not sort_by:
        return rows
    return sort_rows(best_group(rows, sort_by), sort_by, desc)


def best_group(rows: list[dict], key: str) -> list[dict]:
    """Pages carry many tables (navboxes, footers). Keep the one that has the
    sort column, preferring exact > case-insensitive > substring header match,
    then the table with the most numeric values in that column."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(("t", r["_table"]) if "_table" in r else ("l", r.get("list")), []).append(r)

    def rank(g):
        cols = _columns(g)
        k = key.lower()
        if key in cols:
            q, col = 3, key
        elif any(c.lower() == k for c in cols):
            q, col = 2, next(c for c in cols if c.lower() == k)
        elif any(k in c.lower() for c in cols):
            q, col = 1, next(c for c in cols if k in c.lower())
        else:
            return (0, 0, 0)
        nums = sum(numeric(r.get(col, "")) is not None for r in g)
        return (q, nums, len(g))

    best = max(groups.values(), key=rank, default=[])
    return best if rank(best)[0] else rows


def _columns(rows: list[dict]) -> list[str]:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols


def to_csv(rows: list[dict], path=None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_columns(rows), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    text = buf.getvalue()
    if path:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return text


def to_json(rows: list[dict], path=None) -> str:
    text = json.dumps(rows, indent=2, ensure_ascii=False)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bhima.scrape", description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--sort", dest="sort_by")
    ap.add_argument("--desc", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--json")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        rows = scrape(a.url, a.sort_by, a.desc)
    except Exception as e:  # noqa: BLE001 - CLI should print, not trace
        print(f"scrape failed: {e}", file=sys.stderr)
        return 1
    if a.limit:
        rows = rows[: a.limit]
    if a.csv:
        to_csv(rows, a.csv)
    if a.json:
        to_json(rows, a.json)
    if not a.csv and not a.json:
        print(to_json(rows))
    print(f"{len(rows)} rows; columns: {', '.join(_columns(rows))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
