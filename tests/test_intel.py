"""Unit tests for the intelligence layer: router, context ranking, memory,
skills, scraper and offline planner. No network, no GLiDE, no claude calls."""
from __future__ import annotations

from pathlib import Path

import pytest

from bhima import context, glide_client, memory, planner, router, scrape, skills
from bhima.models import RunResult, Task


def mk_task(title, detail="", kind="code", hints=None, acc=None, tid="t1"):
    return Task(id=tid, request_id="r1", title=title, detail=detail, kind=kind,
                files_hint=hints or [], acceptance=acc or [])


def mk_run(ok, cli="claude", rnd=0, tid="t1", error=None, report="", assumptions=None):
    return RunResult(task_id=tid, round=rnd, cli=cli, ok=ok, report=report,
                     assumptions=assumptions or [], usage={}, duration_s=1.0, raw_path="", error=error)


@pytest.fixture
def no_glide(monkeypatch):
    monkeypatch.setattr(glide_client, "decide", lambda *a, **k: None)


# ------------------------------------------------------------------ router
def test_router_rules_scrape_goes_to_haiku_low(no_glide):
    r = router.route(mk_task("Scrape the price table", kind="scrape"), [])
    assert (r.cli, r.model, r.effort, r.source) == ("claude", "haiku", "low", "rules")
    assert "scrape" in r.reason
    assert router.last_decision["answers"] is None


def test_router_rules_small_bugfix_goes_to_codex(no_glide):
    r = router.route(mk_task("Fix off-by-one bug in pagination", hints=["app/pages.py"]), [])
    assert r.cli == "codex" and r.effort == "medium"
    assert r.model == router.codex_model()


def test_router_rules_refactor_goes_to_sonnet_high(no_glide):
    t = mk_task("Refactor storage layer across modules", hints=["a.py", "b.py", "c.py"])
    r = router.route(t, [])
    assert (r.cli, r.model, r.effort) == ("claude", "sonnet", "high")
    assert r.needs_plan


def test_router_escalates_after_failure_and_switches_cli_after_two(no_glide):
    t = mk_task("Fix off-by-one bug in pagination", hints=["app/pages.py"])
    r1 = router.route(t, [mk_run(False, cli="codex")])
    assert r1.effort == "high" and r1.needs_plan
    r2 = router.route(t, [mk_run(False, cli="codex"), mk_run(False, cli="codex", rnd=1)])
    assert r2.cli == "claude"
    assert "switch CLI" in r2.reason


def _fake_answers(choice, conf, score=1, sconf=0.6):
    return {"route": {"choice": choice, "confidence": conf, "probabilities": {}},
            "needs_plan": {"noul": 0.8, "confidence": 0.6},
            "effort": {"score": score, "confidence": sconf, "probabilities": {}}}


def test_router_uses_confident_glide(monkeypatch):
    monkeypatch.setattr(glide_client, "decide", lambda *a, **k: _fake_answers("claude_opus", 0.7, 2))
    r = router.route(mk_task("Scrape the table", kind="scrape"), [])
    assert (r.source, r.model, r.effort) == ("glide", "opus", "high")
    assert r.confidence == 0.7 and r.needs_plan
    assert router.last_decision["used"] == "glide"


def test_router_ignores_unsure_glide(monkeypatch):
    monkeypatch.setattr(glide_client, "decide", lambda *a, **k: _fake_answers("claude_opus", 0.1, 2, 0.1))
    r = router.route(mk_task("Scrape the table", kind="scrape"), [])
    assert r.source == "rules" and r.model == "haiku"
    assert router.last_decision["answers"]["route"]["choice"] == "claude_opus"  # still logged


def test_glide_client_returns_none_when_server_down(monkeypatch):
    monkeypatch.setenv("GLIDE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(glide_client, "_setting", lambda n, d="": "http://127.0.0.1:9" if n == "GLIDE_URL" else "")
    assert glide_client.decide({"x": 1}, router.QUESTIONS, timeout=1) is None
    assert glide_client.health(timeout=1) is False


# ------------------------------------------------------------------ context
@pytest.fixture
def proj(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "def parse_price(text):\n    return float(text.strip('$'))\n\n"
        "def apply_discount(price, pct):\n    return price * (1 - pct / 100)\n", encoding="utf-8")
    (tmp_path / "shop" / "cart.py").write_text(
        "class Cart:\n    def add(self, item):\n        self.items.append(item)\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import parse_price\n\ndef test_parse():\n    assert parse_price('$3') == 3\n",
        encoding="utf-8")
    (tmp_path / "README.md").write_text("A tiny shop demo.\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("def parse_price(): pass\n", encoding="utf-8")
    return tmp_path


def test_tokenize_splits_identifiers():
    assert context._words("parsePrice apply_discount GLiDE") == ["parse", "price", "apply", "discount", "glide"]
    assert context.tokenize("prices sorting parsed") == context.tokenize("price sort parse")


def test_bm25_ranks_relevant_file_first_and_explains(proj):
    ranked = context.rank_files(mk_task("Discount is applied wrong", "apply_discount gives wrong price"), proj)
    assert ranked[0]["path"] == "shop/pricing.py"
    assert any("discount" in w for w in ranked[0]["why"])
    assert all(".venv" not in r["path"] for r in ranked)


def test_files_hint_pins_and_test_pairs(proj):
    ranked = context.rank_files(mk_task("Make it faster", hints=["shop/pricing.py"]), proj)
    assert ranked[0]["path"] == "shop/pricing.py"
    assert ranked[0]["why"][0] == "named in files_hint"
    paths = [r["path"] for r in ranked]
    assert paths.index("tests/test_pricing.py") < paths.index("shop/cart.py")


def test_build_pack_within_budget_with_notes(proj, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path_factory.mktemp("mem"))
    t = mk_task("Fix parse_price for commas", "parse_price('1,200') should be 1200")
    pack = context.build_pack(t, proj, budget_tokens=800,
                              history=[mk_run(False, error="AssertionError: 1.0 != 1200")])
    assert pack.files[0] == "shop/pricing.py"
    assert "## Why these files" in pack.text and "Notes from earlier rounds" in pack.text
    assert "AssertionError" in pack.text and "def parse_price(text)" in pack.text
    assert pack.tokens_est <= 800


# ------------------------------------------------------------------ memory
@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path)
    return tmp_path


def test_memory_add_patch_remove(mem):
    memory.add("Use pytest -q")
    memory.add("Prices are in INR")
    assert memory.entries() == ["Use pytest -q", "Prices are in INR"]
    memory.patch("INR", "Prices are in INR paise")
    memory.remove(0)
    assert memory.entries() == ["Prices are in INR paise"]
    assert "§" not in memory.entries()[0]


def test_memory_cap_rejects_and_lists_entries(mem):
    memory.add("first entry")
    with pytest.raises(memory.MemoryFull) as e:
        memory.add("x" * 2300)
    assert "first entry" in str(e.value) and "cap 2200" in str(e.value)
    with pytest.raises(memory.MemoryFull):
        memory.add("y" * 1400, target="USER.md")
    assert memory.entries() == ["first entry"]


def test_memory_snapshot_and_propose(mem):
    memory.add("Known fact")
    snap = memory.snapshot()
    assert "Project memory" in snap and "% used" in snap and "Known fact" in snap
    run = mk_run(True, rnd=1, report="Done.\nLesson: parse_price must strip commas before float()",
                 assumptions=["Uses pytest for all tests"])
    props = memory.propose(run)
    assert 1 <= len(props) <= 3
    assert any("strip commas" in p for p in props)
    memory.apply(props)
    assert len(memory.entries()) == 1 + len(props)


# ------------------------------------------------------------------ skills
def test_skills_index_load_suggest(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)
    skills.create("python-tests", "Run pytest", "Body here", tags=["python"])
    idx = skills.index()
    assert "python-tests: Run pytest" in idx and "Body here" not in idx
    assert "Body here" in skills.load("python-tests")
    assert skills.suggest([mk_run(True)]) is None
    s = skills.suggest([mk_run(True, tid="a"), mk_run(True, tid="b")])
    assert s and s["name"] == "code-playbook" and s["evidence"] == ["a", "b"]


def test_repo_starter_skills_parse():
    names = {s["name"] for s in skills.all_skills()}
    assert {"python-tests", "scrape-and-sort", "safe-edits"} <= names


# ------------------------------------------------------------------ scrape
HTML = """<html><body><script>var x='<td>no</td>';</script>
<table><tr><th>Name</th><th>Price</th></tr>
<tr><td>Widget</td><td>$1,200</td></tr>
<tr><td>Gadget</td><td>$15.50</td></tr>
<tr><td>Thing</td><td></td></tr>
<tr><td>Doohickey</td><td>$300</td></tr></table>
<ul><li>alpha</li><li>beta <b>bold</b></li></ul></body></html>"""


def test_scrape_parse_and_numeric_sort():
    rows = scrape.parse(HTML)
    table = [r for r in rows if "Name" in r]
    assert [r["Name"] for r in table] == ["Widget", "Gadget", "Thing", "Doohickey"]
    items = [r["item"] for r in rows if "item" in r]
    assert items == ["alpha", "beta bold"]
    s = scrape.sort_rows(table, "price")
    assert [r["Name"] for r in s] == ["Gadget", "Doohickey", "Widget", "Thing"]
    s = scrape.sort_rows(table, "Price", desc=True)
    assert s[0]["Name"] == "Widget" and s[-1]["Name"] == "Thing"
    assert scrape.numeric("3.2k") == 3200 and scrape.numeric("n/a") is None
    assert scrape.to_csv(table).splitlines()[0] == "Name,Price,_table"


def test_scrape_picks_table_with_sort_column_and_reads_local_file(tmp_path):
    page = tmp_path / "p.html"
    nav = "<table><tr><th>Links</th></tr><tr><td>Population stats</td></tr></table>"
    page.write_text(nav + HTML, encoding="utf-8")
    rows = scrape.scrape(str(page), sort_by="price", desc=True)
    assert [r["Name"] for r in rows][:3] == ["Widget", "Doohickey", "Gadget"]
    assert scrape.numeric("−0.18%") == -0.18


def test_scrape_rejects_non_http():
    with pytest.raises(ValueError):
        scrape.fetch("file:///etc/passwd")


# ------------------------------------------------------------------ planner
def test_planner_offline_fallback(proj):
    p = planner.make_plan_offline("Fix parse_price for commas; then scrape https://example.com prices", proj)
    assert len(p.tasks) == 2
    assert p.tasks[0].kind == "code" and "shop/pricing.py" in p.tasks[0].files_hint
    assert p.tasks[1].kind == "scrape"
    assert p.test_command == "python -m pytest -q"


def test_planner_falls_back_when_claude_missing(proj, monkeypatch):
    monkeypatch.setattr(planner, "_claude_exe", lambda: None)
    p = planner.make_plan("What does Cart do?", proj)
    assert p.tasks[0].kind == "question" and "offline" in p.summary


def test_planner_parses_json_from_result_text(proj):
    d = planner._extract_json('Here:\n```json\n{"summary": "s", "tasks": [{"title": "x"}]}\n```')
    plan = planner._to_plan(d, "req", proj, "r9")
    assert plan.tasks[0].title == "x" and plan.tasks[0].kind == "code"


def test_planner_disables_tools_and_reads_request_from_stdin(proj, monkeypatch):
    from types import SimpleNamespace

    request = "a multiline request\nwith more details"
    monkeypatch.setattr(planner, "_claude_exe", lambda: "fake-claude")

    def run(cmd, **kwargs):
        assert request not in " ".join(cmd)
        assert request in kwargs["input"]
        assert cmd[cmd.index("--tools") + 1] == ""
        assert cmd[cmd.index("--disallowedTools") + 1] == "*"
        return SimpleNamespace(stdout='{"structured_output": {"tasks": [{"title": "fake plan"}]}}')

    monkeypatch.setattr(planner.subprocess, "run", run)
    assert planner.make_plan(request, proj).tasks[0].title == "fake plan"
