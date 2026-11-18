"""Tiny client for a GLiDE decision server (System One format).

GLiDE answers typed questions (choice / noul / score) about a state in one
forward pass. We use it for routing. Every failure returns None so callers
can fall back to rules.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000"
MODEL_NAME = "glide-0.4b"


def _setting(name: str, default: str = "") -> str:
    """Read from bhima.config if it defines the name, else from the environment."""
    try:
        from bhima import config  # owned by agent 1; optional here

        val = getattr(config, name, None)
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get(name, default) or default


def base_url() -> str:
    return _setting("GLIDE_URL", DEFAULT_URL).rstrip("/")


def _headers() -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "bhima-harness"}
    key = _setting("GLIDE_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def decide(state: dict, questions: dict, timeout: float = 5) -> dict | None:
    """POST state+questions; return {question_name: answer_dict} or None."""
    body = json.dumps({"model": MODEL_NAME, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(base_url() + "/v1/systemone", data=body,
                                 headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if not isinstance(data, dict) or "error" in data:
        return None
    answers = data.get("answers", data)  # server wraps answers; tolerate bare form
    if not isinstance(answers, dict):
        return None
    return answers


def health(timeout: float = 2) -> bool:
    req = urllib.request.Request(base_url() + "/health", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
            return resp.status == 200 and data.get("status", "ok") == "ok"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False
