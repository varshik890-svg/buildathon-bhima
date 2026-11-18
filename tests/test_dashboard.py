"""Dashboard tests with temporary SQLite state and no network."""
from __future__ import annotations

import re
from threading import Event

import pytest

from bhima import cli, config, dashboard
from bhima.store import Store


@pytest.fixture(autouse=True)
def offline_health(monkeypatch):
    monkeypatch.setattr(dashboard.talker, "health", lambda: True)
    monkeypatch.setattr(dashboard.glide_client, "health", lambda: False)
    monkeypatch.setattr(dashboard, "find_exe", lambda name: f"/bin/{name}")
    monkeypatch.setattr(dashboard, "_codex_health", lambda: (True, " (CODEX_VIA_WSL=false)"))
    monkeypatch.setattr(dashboard, "_health", dashboard.HealthCache())


def test_render_frame_from_store(tmp_path, monkeypatch):
    path = tmp_path / "dashboard #.db"
    store = Store(path)
    try:
        request = store.add_request("Fix the invoice discount")
        store.save_plan("p1", request, {"planned_by": "talker-local"})
        store.save_task("t1", request, "p1", {"title": "Fix discount"}, "failed")
        store.add_decision("t1", 0, {
            "route": {"cli": "codex", "model": "gpt-5.5", "effort": "medium",
                      "source": "rules", "reason": "GLiDE unsure"},
            "router": {"answers": {
                "route": {"probabilities": {
                    "claude_haiku": 0.25, "claude_sonnet": 0.20, "claude_opus": 0.25, "codex": 0.30,
                }},
                "effort": {"probabilities": {"0": 0.20, "1": 0.50, "2": 0.30}},
            }},
            "max_rounds": 3,
        })
        store.add_run("t1", 0, "codex", "gpt-5.5", "medium", False, {
            "visible_tests_passed": True, "hidden_tests_passed": False,
            "usage": {"input_tokens": 100, "output_tokens": 50}, "duration_s": 1.5,
        })
        store.add_check("t1", 0, False, {"assumptions": ["assumes integer quantities"]})
        store.add_approval("retry", "t1")
        store.add_event("context_pack", "t1", "Context pack for t1", {
            "files": ["invoice/pricing.py"],
            "why_lines": ["- invoice/pricing.py (score 100.0): pinned file hint"],
        })
        for i in range(10):
            store.add_event("info", request, f"Event {i:02}")
    finally:
        store.close()

    connect = dashboard.sqlite3.connect

    def read_only(database, **kwargs):
        assert database.endswith("?mode=ro") and kwargs["uri"]
        return connect(database, **kwargs)

    monkeypatch.setattr(dashboard.sqlite3, "connect", read_only)
    frame = dashboard.render_frame(width=100, db_path=path)
    for expected in (
        "BHIMA HARNESS", "Fix the invoice discount", "planned by: talker-local",
        "t1 [failed] round 1/3 | codex gpt-5.5/medium", "rules (GLiDE unsure)",
        "claude_haiku 25%", "claude_sonnet 20%", "claude_opus 25%", "codex 30%",
        "low 20%", "medium 50%", "high 30%", "invoice/pricing.py (score 100.0): pinned file hint",
        "Visible tests: PASS", "Hidden acceptance tests: FAIL", "Assumptions: 1",
        "Totals: 1 round | 150 tokens | 1.5 seconds | approvals waiting: 1",
    ):
        assert expected in frame
    events = re.findall(r"^\d\d:\d\d:\d\d \[info\] Event (\d\d)$", frame, re.MULTILINE)
    assert events == ["09", "08", "07", "06", "05", "04", "03", "02"]
    assert max(map(len, dashboard.render_frame(width=28, db_path=path).splitlines())) <= 28


@pytest.mark.parametrize("initialized", [True, False])
def test_empty_or_missing_store(tmp_path, monkeypatch, capsys, initialized):
    path = tmp_path / "state" / "state.db"
    if initialized:
        Store(path).close()
    monkeypatch.setattr(config, "DB_PATH", path)
    assert cli.main(["dash", "--once"]) == 0
    output = capsys.readouterr().out
    assert "no requests yet" in output
    assert output.count("BHIMA HARNESS") == 1 and "\x1b" not in output
    assert path.parent.exists() == initialized


def test_health_refresh_keeps_slow_probe_off_render_thread(tmp_path, monkeypatch):
    blocked, release, committed = Event(), Event(), Event()
    cache = dashboard._health
    probes = []

    def slow_health():
        probes.append("Talker")
        blocked.set()
        return release.wait(5)

    refresh = cache._refresh

    def signal_after_refresh(name, probe):
        refresh(name, probe)
        if name == "Talker":
            committed.set()

    monkeypatch.setattr(dashboard.talker, "health", slow_health)
    monkeypatch.setattr(cache, "_refresh", signal_after_refresh)
    path = tmp_path / "missing.db"
    try:
        assert "Talker ○ checking" in dashboard.render_frame(100, db_path=path)
        assert blocked.wait(2)
        assert "Talker ○ checking" in dashboard.render_frame(100, db_path=path)
    finally:
        release.set()
    assert committed.wait(2)
    assert re.search(r"Talker ● up \(\d+ ms\)", dashboard.render_frame(100, db_path=path))
    assert probes == ["Talker"]


def test_live_dashboard_restores_cursor(tmp_path, monkeypatch, capsys):
    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(dashboard.time, "sleep", interrupt)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "missing.db")
    dashboard.run()
    output = capsys.readouterr().out
    assert output.startswith("\x1b[?25l\x1b[2J\x1b[H")
    assert output.endswith("\x1b[?25h\n")
