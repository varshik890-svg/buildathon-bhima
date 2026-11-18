import io
import json
from urllib.error import URLError

import pytest

from bhima import config, planner, talker


@pytest.mark.parametrize("failure", [TimeoutError(), URLError("offline"), b"not JSON", b'{"choices": []}'])
def test_client_returns_none_on_error(monkeypatch, failure):
    def open_request(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return io.BytesIO(failure)

    monkeypatch.setattr(talker, "urlopen", open_request)
    assert talker.chat([]) is None
    assert talker.ask("hello") is None
    assert talker.health() is False


def test_client_request_format(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_env_values", None)
    monkeypatch.delenv("TALKER_URL", raising=False)
    monkeypatch.delenv("TALKER_TIMEOUT", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("TALKER_URL=http://localhost:1234/v1/\nTALKER_TIMEOUT=2.5\n")
    config.load_env(dotenv)

    def open_request(request, *, timeout):
        assert timeout == 2.5
        if isinstance(request, str):
            assert request == "http://localhost:1234/health"
            response = io.BytesIO()
            response.status = 200
            return response
        assert request.full_url == "http://localhost:1234/v1/chat/completions"
        assert request.get_method() == "POST"
        payload = json.loads(request.data)
        assert payload["messages"] == messages
        if "response_format" in payload:
            assert payload["response_format"]["json_schema"]["schema"] == planner.PLAN_SCHEMA
        return io.BytesIO(b'{"choices":[{"message":{"content":"hello"}}]}')

    messages = [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "Hi"}]
    monkeypatch.setattr(talker, "urlopen", open_request)
    assert talker.health()
    assert talker.ask("Hi", system="Be brief") == "hello"
    assert talker.chat(messages, json_schema=planner.PLAN_SCHEMA) == "hello"


def test_planner_prefers_valid_talker_plan(monkeypatch, tmp_path):
    def chat(messages, *, json_schema):
        assert "fix the project" in messages[0]["content"]
        assert json_schema == planner.PLAN_SCHEMA
        return json.dumps({
            "summary": "One fix", "test_command": "untrusted", "planned_by": "untrusted",
            "tasks": [{
                "title": "Fix", "detail": "Fix the project", "kind": "code",
                "files_hint": [], "acceptance": ["tests pass"],
            }],
        })

    def unexpected_claude():
        pytest.fail("Claude lookup should be skipped")

    monkeypatch.setattr(talker, "chat", chat)
    monkeypatch.setattr(planner, "_claude_exe", unexpected_claude)
    plan = planner.make_plan("fix the project", tmp_path)
    assert plan.planned_by == "talker-local"
    assert plan.test_command == config.DEFAULT_TEST_COMMAND
    assert plan.tasks[0].title == "Fix"


@pytest.mark.parametrize("response", [
    None, '{"summary":"bad","tasks":[]}', '{"summary":"bad","tasks":[{"title":"incomplete"}]}',
])
def test_planner_uses_offline_when_both_models_fail(monkeypatch, tmp_path, response):
    def fake_claude(cmd, **kwargs):
        assert cmd[0] == "fake-claude"
        raise planner.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(talker, "chat", lambda *args, **kwargs: response)
    monkeypatch.setattr(planner, "_claude_exe", lambda: "fake-claude")
    monkeypatch.setattr(planner.subprocess, "run", fake_claude)
    assert planner.make_plan("fix the project", tmp_path).planned_by == "offline"
