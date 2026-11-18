"""Textual pilot tests with offline services and temporary SQLite state."""
from __future__ import annotations

import asyncio
from threading import get_ident

from textual.widgets import Footer, Header, Input, RichLog, Static

from bhima import cli, config, tui
from bhima.store import Store


class FakeBackend:
    def __init__(self):
        self.submitted = []
        self.approved = []
        self.denied = []

    def on_event(self, callback):
        self.emit = callback

    def submit(self, text):
        self.submitted.append(text)
        self.submit_thread = get_ident()
        return "r1"

    def approve(self, approval_id):
        self.approved.append(approval_id)
        return f"Approved {approval_id}"

    def deny(self, approval_id):
        self.denied.append(approval_id)
        return f"Denied {approval_id}"

    def status(self):
        return "Fake status"


class FakeHealth:
    def refresh(self):
        pass

    def snapshot(self):
        return dict.fromkeys(("Talker", "Claude", "Codex"), (True, "")) | {"GLiDE": (False, "")}


def make_app(tmp_path, *, store=None, ask_talker=lambda text: f"Reply to {text}"):
    backend = FakeBackend()
    app = tui.BhimaApp(backend, db_path=tmp_path / "test.db", store=store,
                       ask_talker=ask_talker, health=FakeHealth())
    return app, backend


def transcript(app):
    return "\n".join(line.text for line in app.query_one(RichLog).lines)


async def enter(pilot, text):
    await pilot.press(*text, "enter")
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def test_app_mounts(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test() as pilot:
            assert app.query_one(Header)
            assert app.query_one(Footer)
            assert app.focused is app.query_one(Input)
            await pilot.pause()
            models = app.query_one("#models", Static).content
            assert "Talker ● up" in models and "GLiDE ○ down" in models

    asyncio.run(scenario())


def test_plain_text_shows_talker_reply(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await enter(pilot, "hello Bhima")
            assert "YOU › hello Bhima" in transcript(app)
            assert "BHIMA › Reply to hello Bhima" in transcript(app)

    asyncio.run(scenario())


def test_ask_submits_task(tmp_path):
    app, backend = make_app(tmp_path)

    async def scenario():
        async with app.run_test() as pilot:
            await enter(pilot, "/ask fix invoice")
            assert backend.submitted == ["fix invoice"]
            assert backend.submit_thread != get_ident()
            assert "BHIMA › Request r1" in transcript(app)

    asyncio.run(scenario())


def test_worker_approval_event_and_shortcuts(tmp_path):
    app, backend = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await asyncio.to_thread(backend.emit, {
                "kind": "plan_ready", "id": "a1", "text": "Plan ready",
            })
            await asyncio.to_thread(backend.emit, {
                "kind": "needs_approval", "id": "a2", "text": "Merge requires approval",
            })
            await pilot.pause()
            output = transcript(app)
            assert "HARNESS · needs_approval › Merge requires approval" in output
            assert "Approval a2 · /approve a2 or /deny a2" in output
            await pilot.press("ctrl+a")
            await app.workers.wait_for_complete()
            assert backend.approved == ["a2"]
            await pilot.press("ctrl+d")
            await app.workers.wait_for_complete()
            assert backend.denied == ["a1"]

    asyncio.run(scenario())


def test_empty_store_and_offline_talker(tmp_path):
    store = Store(tmp_path / "test.db")
    try:
        app, _ = make_app(tmp_path, store=store, ask_talker=lambda text: None)

        async def scenario():
            async with app.run_test(size=(120, 30)) as pilot:
                await enter(pilot, "hello")
                assert "No tasks yet" in app.query_one("#plan", Static).content
                assert "No pending approvals" in app.query_one("#approvals", Static).content
                assert "BHIMA › Talker offline. Use /ask to start a task." in transcript(app)

        asyncio.run(scenario())
    finally:
        store.close()


def test_sidebar_polls_sqlite(tmp_path):
    store = Store(tmp_path / "test.db")
    try:
        app, _ = make_app(tmp_path, store=store)

        async def scenario():
            async with app.run_test(size=(120, 35)) as pilot:
                await app.workers.wait_for_complete()
                request = store.add_request("Fix invoice")
                store.save_plan("p1", request, {"planned_by": "talker-local"})
                store.save_task("t1", request, "p1", {"title": "Fix invoice"}, status="running")
                store.add_decision("t1", 0, {
                    "route": {"cli": "codex", "model": "gpt", "effort": "high",
                              "source": "glide", "reason": "High confidence"},
                    "router": {"answers": {"route": {"probs": {"codex": 0.75}},
                                           "effort": {"probabilities": [0, 0.25, 0.75]}}},
                })
                store.add_event("context_pack", "t1", "Context", {"why_lines": ["pricing.py matched invoice"]})
                store.add_run("t1", 0, "codex", "gpt", "high", False, {
                    "usage": {"input_tokens": 10, "output_tokens": 5}, "duration_s": 1.5,
                    "visible_tests_passed": True, "hidden_tests_passed": False,
                })
                store.add_check("t1", 0, False, {"assumptions": ["integer amounts"]})
                approval_id = store.add_approval("retry", "t1")

                def panel(name):
                    return app.query_one(f"#{name}", Static).content

                async with asyncio.timeout(4):
                    while approval_id not in panel("approvals"):
                        await pilot.pause(0.05)
                assert "Planned by talker-local" in panel("plan")
                assert "running · round 1" in panel("plan")
                for value in ("codex gpt · high", "glide", "High confidence", "━", "75%"):
                    assert value in panel("route")
                assert "pricing.py matched invoice" in panel("context")
                for value in ("Visible  PASS", "Hidden  FAIL", "integer amounts"):
                    assert value in panel("checks")
                assert "Rounds 1 · Tokens 15" in panel("totals")
                assert "Time 1.5s" in panel("totals")

        asyncio.run(scenario())
    finally:
        store.close()


def test_cli_tui_entrypoint(monkeypatch):
    started = []
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(tui, "run", lambda: started.append(True))
    assert cli.main(["tui"]) == 0
    assert started == [True]


def test_slash_commands(tmp_path, monkeypatch):
    app, backend = make_app(tmp_path)
    monkeypatch.setattr(tui.report, "render", lambda: "Fake report")
    monkeypatch.setattr(tui.memory, "snapshot", lambda: "Fake memory")
    monkeypatch.setattr(tui.skills, "index", lambda: "Fake skills")

    def fake_scrape(url, key, *, desc):
        assert (url, key, desc) == ("https://example.com", "Unit price", True)
        return [{"Unit price": 25}]

    monkeypatch.setattr(tui.scrape, "scrape", fake_scrape)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            prompt = app.query_one(Input)
            for command, expected in (
                ("/status", "Fake status"), ("/report", "Fake report"),
                ("/memory", "Fake memory"), ("/skills", "Fake skills"),
                ('/scrape https://example.com "Unit price" desc', "25"),
                ("/approve a3", "Approved a3"), ("/deny a4", "Denied a4"),
                ("/help", "Plain text chats with Talker."),
            ):
                prompt.value = command
                await enter(pilot, "")
                assert expected in transcript(app)
            assert backend.approved == ["a3"] and backend.denied == ["a4"]
            prompt.value = "/clear"
            await enter(pilot, "")
            assert transcript(app) == ""
            prompt.value = "/quit"
            await pilot.press("enter")
        assert not app.is_running

    asyncio.run(scenario())
