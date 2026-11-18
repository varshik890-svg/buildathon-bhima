"""Tests for bhima.telegram_bot: no network, fakes only."""

import asyncio
from types import SimpleNamespace

import pytest

from bhima import telegram_bot as tb

OWNER = 111


# ---------------------------------------------------------------- fakes ---

class FakeBot:
    def __init__(self):
        self.messages = []
        self.documents = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))

    async def send_document(self, chat_id, document, filename, caption=None, reply_markup=None):
        self.documents.append((chat_id, filename, document.getvalue().decode(), reply_markup))


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.caption = None
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)

    async def reply_document(self, document, filename):
        self.replies.append(("doc", filename))


class FakeQuery:
    def __init__(self, data, text="[approve?] needs_approval (t1)"):
        self.data = data
        self.message = FakeMessage(text)
        self.answers = []
        self.edited = None

    async def answer(self, text=None):
        self.answers.append(text)

    async def edit_message_text(self, text):
        self.edited = text


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.fail = False

    def submit(self, request):
        self.calls.append(("submit", request))
        return "r1"

    def approve(self, target):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("approve", target))

    def deny(self, target):
        self.calls.append(("deny", target))

    def status(self):
        return "idle"

    def report(self):
        return "report"

    def memory_snapshot(self):
        return "mem"

    def skills_index(self):
        return "skills"

    def scrape(self, url, sort_by):
        self.calls.append(("scrape", url, sort_by))
        return [{"a": 2}, {"a": 1}]

    def on_event(self, cb):
        self.cb = cb


def make_bot():
    backend = FakeBackend()
    bot = tb.BhimaBot("TOKEN", OWNER, backend)
    bot.app = SimpleNamespace(bot=FakeBot())
    return bot, backend


def update_from(chat_id, text="", query=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id),
                           effective_message=FakeMessage(text), callback_query=query)


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------- auth ---

def test_is_owner_exact_match_only():
    assert tb.is_owner(111, 111)
    assert tb.is_owner("111", 111)
    assert not tb.is_owner(222, 111)
    assert not tb.is_owner(111, None)
    assert not tb.is_owner(None, 111)
    assert not tb.is_owner("abc", 111)


def test_parse_chat_id():
    assert tb.parse_chat_id(" -100123 ") == -100123
    assert tb.parse_chat_id("") is None
    assert tb.parse_chat_id("x") is None


def test_stranger_is_ignored_silently():
    bot, backend = make_bot()
    upd = update_from(999, "hello")
    run(bot.cmd_status(upd, SimpleNamespace(args=[])))
    run(bot.on_text(upd, SimpleNamespace()))
    assert upd.effective_message.replies == []
    assert backend.calls == []


def test_stranger_button_is_rejected():
    bot, backend = make_bot()
    q = FakeQuery("approve:t1")
    run(bot.on_button(update_from(999, query=q), SimpleNamespace()))
    assert backend.calls == []
    assert q.edited is None


# ------------------------------------------------------------ splitting ---

def test_split_short_message_is_single_chunk():
    assert tb.split_message("hi") == ["hi"]


def test_split_respects_limit_and_keeps_content():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(300))
    chunks = tb.split_message(text, limit=4000)
    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_without_newlines_hard_cuts():
    text = "a" * 9001
    chunks = tb.split_message(text, limit=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1001]
    assert "".join(chunks) == text


# ------------------------------------------------------------ callbacks ---

def test_parse_callback():
    assert tb.parse_callback("approve:t1") == ("approve", "t1")
    assert tb.parse_callback("deny:plan:abc") == ("deny", "plan:abc")
    assert tb.parse_callback("APPROVE: x ") == ("approve", "x")
    assert tb.parse_callback("delete:t1") is None
    assert tb.parse_callback("approve:") is None
    assert tb.parse_callback("") is None
    assert tb.parse_callback(None) is None


def test_make_callback_roundtrip_and_limit():
    assert tb.parse_callback(tb.make_callback("approve", "r-42")) == ("approve", "r-42")
    with pytest.raises(ValueError):
        tb.make_callback("approve", "x" * 80)


def test_button_approve_calls_orchestrator():
    bot, backend = make_bot()
    q = FakeQuery("approve:t1")
    run(bot.on_button(update_from(OWNER, query=q), SimpleNamespace()))
    assert backend.calls == [("approve", "t1")]
    assert q.edited.endswith("-> Approved")


def test_button_deny_calls_orchestrator():
    bot, backend = make_bot()
    q = FakeQuery("deny:t9")
    run(bot.on_button(update_from(OWNER, query=q), SimpleNamespace()))
    assert backend.calls == [("deny", "t9")]
    assert q.edited.endswith("-> Denied")


def test_button_failure_is_reported_not_raised():
    bot, backend = make_bot()
    backend.fail = True
    q = FakeQuery("approve:t1")
    run(bot.on_button(update_from(OWNER, query=q), SimpleNamespace()))
    assert "approve failed" in q.edited


# --------------------------------------------------------------- events ---

def test_format_event_dict_and_object():
    text = tb.format_event({"kind": "task_done", "id": "t1", "text": "tests pass"})
    assert text == "[done] task_done (t1)\ntests pass"
    obj = SimpleNamespace(kind="error", id=None, text="oops")
    assert tb.format_event(obj) == "[error] error\noops"


def test_needs_buttons():
    assert tb.needs_buttons({"kind": "needs_approval", "id": "t1"})
    assert tb.needs_buttons({"kind": "plan_ready", "id": "p1"})
    assert tb.needs_buttons({"kind": "memory_proposal", "id": "m1"})
    assert not tb.needs_buttons({"kind": "task_done", "id": "t1"})
    assert not tb.needs_buttons({"kind": "needs_approval", "id": None})


def test_send_event_with_buttons():
    pytest.importorskip("telegram")
    bot, _ = make_bot()
    run(bot.send_event({"kind": "needs_approval", "id": "t1", "text": "merge?"}))
    chat_id, text, markup = bot.app.bot.messages[0]
    assert chat_id == OWNER
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert datas == ["approve:t1", "deny:t1"]


def test_send_event_long_text_is_split_and_buttons_on_last():
    pytest.importorskip("telegram")
    bot, _ = make_bot()
    long = "\n".join("y" * 90 for _ in range(100))  # ~9100 chars
    run(bot.send_event({"kind": "plan_ready", "id": "p1", "text": long}))
    msgs = bot.app.bot.messages
    assert len(msgs) >= 3
    assert all(m[2] is None for m in msgs[:-1])
    assert msgs[-1][2] is not None


def test_diff_is_sent_as_document():
    bot, _ = make_bot()
    run(bot.send_text("diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b"))
    assert bot.app.bot.messages == []
    assert bot.app.bot.documents[0][1] == "diff.patch"


def test_very_long_text_is_sent_as_document():
    bot, _ = make_bot()
    run(bot.send_text("z" * (tb.DOCUMENT_THRESHOLD + 1), filename="report.txt"))
    assert bot.app.bot.documents[0][1] == "report.txt"


def test_event_from_other_thread_reaches_loop():
    bot, _ = make_bot()

    async def main():
        bot.loop = asyncio.get_running_loop()
        import threading
        t = threading.Thread(target=bot.on_orchestrator_event,
                             args=({"kind": "merged", "id": "t1", "text": "sha abc"},))
        t.start()
        t.join()
        for _ in range(50):
            if bot.app.bot.messages:
                break
            await asyncio.sleep(0.01)

    run(main())
    assert bot.app.bot.messages[0][1].startswith("[merged] merged (t1)")


# ------------------------------------------------------------- commands ---

def test_ask_submits_in_background():
    bot, backend = make_bot()
    upd = update_from(OWNER)

    async def main():
        await bot.cmd_ask(upd, SimpleNamespace(args=["add", "a", "test"]))
        for _ in range(50):
            if bot.app.bot.messages:
                break
            await asyncio.sleep(0.01)

    run(main())
    assert backend.calls == [("submit", "add a test")]
    assert "r1" in bot.app.bot.messages[0][1]


def test_ask_without_text_shows_usage():
    bot, backend = make_bot()
    upd = update_from(OWNER)
    run(bot.cmd_ask(upd, SimpleNamespace(args=[])))
    assert "Usage" in upd.effective_message.replies[0]
    assert backend.calls == []


def test_status_memory_skills():
    bot, _ = make_bot()
    for handler, expected in ((bot.cmd_status, "idle"), (bot.cmd_memory, "mem"),
                              (bot.cmd_skills, "skills"), (bot.cmd_report, "report")):
        upd = update_from(OWNER)
        run(handler(upd, SimpleNamespace(args=[])))
        assert upd.effective_message.replies == [expected]


def test_scrape_args():
    assert tb.parse_scrape_args(["https://x.io", "price", "desc"]) == ("https://x.io", "price desc")
    assert tb.parse_scrape_args(["https://x.io"]) == ("https://x.io", None)
    assert tb.parse_scrape_args(["ftp://x"]) is None
    assert tb.parse_scrape_args([]) is None


def test_scrape_command():
    bot, backend = make_bot()
    upd = update_from(OWNER)
    run(bot.cmd_scrape(upd, SimpleNamespace(args=["https://x.io", "a"])))
    assert backend.calls == [("scrape", "https://x.io", "a")]
    assert upd.effective_message.replies[-1].startswith("2 rows\na\n2\n1")


def test_backend_error_is_reported():
    bot, _ = make_bot()
    bot.backend.status = lambda: (_ for _ in ()).throw(RuntimeError("not ready"))
    upd = update_from(OWNER)
    run(bot.cmd_status(upd, SimpleNamespace(args=[])))
    assert "not ready" in upd.effective_message.replies[0]


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    token, chat = tb.load_settings()
    assert token and chat  # values come from config, env or .env; never printed


def test_build_app_registers_owner_filtered_handlers():
    pytest.importorskip("telegram")
    from telegram.ext import CallbackQueryHandler, CommandHandler
    bot = tb.BhimaBot("123456:FAKE-token-for-tests", OWNER, FakeBackend())
    app = bot.build_app()  # no network: Application is only constructed
    handlers = app.handlers[0]
    commands = {c for h in handlers if isinstance(h, CommandHandler) for c in h.commands}
    assert commands == {"start", "help", "ask", "status", "report", "memory", "skills", "scrape"}
    assert any(isinstance(h, CallbackQueryHandler) for h in handlers)
    assert "Chat" in repr(next(h for h in handlers if isinstance(h, CommandHandler)).filters)


def test_backend_scrape_parses_desc(monkeypatch):
    seen = {}

    def fake_scrape(url, sort_by=None, desc=False):
        seen.update(url=url, sort_by=sort_by, desc=desc)
        return []

    backend = tb.Backend()
    monkeypatch.setattr(backend, "_mod", lambda name: SimpleNamespace(scrape=fake_scrape))
    backend.scrape("https://x.io", "unit price desc")
    assert seen == {"url": "https://x.io", "sort_by": "unit price", "desc": True}
    backend.scrape("https://x.io", None)
    assert seen["sort_by"] is None and seen["desc"] is False


def test_backend_uses_module_level_orchestrator(monkeypatch):
    calls = []
    orch = SimpleNamespace(submit=lambda r: calls.append(r) or "r9", status=lambda: "busy",
                           approve=lambda i: calls.append(("a", i)), deny=lambda i: None,
                           on_event=lambda cb: None)
    backend = tb.Backend()
    monkeypatch.setattr(backend, "_mod", lambda name: orch)
    assert backend.submit("x") == "r9"
    assert backend.status() == "busy"
    backend.approve("t1")
    assert calls == ["x", ("a", "t1")]
