"""Small HTTP client for the local llama.cpp server."""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from . import config

DEFAULT_URL = "http://127.0.0.1:8791/v1"


def _settings() -> tuple[str, float]:
    url = (config.get("TALKER_URL").strip() or DEFAULT_URL).rstrip("/")
    timeout = float(config.get("TALKER_TIMEOUT").strip() or "60")
    return url, timeout


def health() -> bool:
    try:
        url, timeout = _settings()
        with urlopen(url.removesuffix("/v1") + "/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def chat(
    messages: list[dict[str, str]],
    max_tokens: int = 512,
    temperature: float = 0.2,
    json_schema: dict | None = None,
) -> str | None:
    try:
        url, timeout = _settings()
        payload = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema},
            }
        request = Request(url + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=timeout) as response:
            content = json.load(response)["choices"][0]["message"]["content"]
        return content if isinstance(content, str) and content.strip() else None
    except Exception:
        return None


def ask(text: str, system: str | None = None) -> str | None:
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": text})
    return chat(messages)
