"""Offline tests for the Groq adapter using httpx.MockTransport. No network."""

import asyncio
import json

import httpx
import pytest

from jobbot import config
from jobbot.llm import get_backend
from jobbot.llm.base import LLMError
from jobbot.llm.groq import GroqBackend, RateLimited
from jobbot.model import DraftRequest, FormField, MapRequest

FIELDS = [FormField("f1", "text", "First name"), FormField("f2", "password", "Password")]
GOOD = {"job": {"company": "Acme", "position": "AI Eng", "location": "Remote", "language": "en"},
        "fills": [{"id": "f1", "value": "Jane"}], "unknown": []}


def _ok(content: dict | str, headers=None):
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, headers=headers or {},
                          json={"choices": [{"message": {"role": "assistant", "content": text}}]})


def _backend(handler, **kw):
    return GroqBackend(api_key="gsk_test", transport=httpx.MockTransport(handler), **kw)


def test_request_shape_strict_schema_and_auth():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return _ok(GOOD, {"x-ratelimit-remaining-tokens": "7000"})

    async def go():
        b = _backend(handler)
        res = await b.map_fields(MapRequest(FIELDS, "profile", "job"))
        await b.aclose()
        return res, b

    res, b = asyncio.run(go())
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer gsk_test"
    assert body["model"] == "openai/gpt-oss-20b"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "low"
    assert "max_tokens" not in body and body["max_completion_tokens"] > 0
    assert "Password" not in body["messages"][1]["content"]      # local-only fields never sent
    assert res.fills == {"f1": "Jane"}
    assert b.last_meta.remaining_tokens == "7000"


def test_non_strict_model_uses_json_object():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok(GOOD)

    async def go():
        b = _backend(handler, fast_model="llama-3.1-8b-instant")
        await b.map_fields(MapRequest(FIELDS, "p", "j"))
        await b.aclose()

    asyncio.run(go())
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in seen[0]
    assert "JSON Schema" in seen[0]["messages"][0]["content"]


def test_rate_limit_falls_back_to_second_model():
    models = []

    def handler(req):
        m = json.loads(req.content)["model"]
        models.append(m)
        if m == "openai/gpt-oss-20b":
            return httpx.Response(429, headers={"retry-after": "12"},
                                  json={"error": {"message": "Rate limit reached", "type": "tokens"}})
        return _ok(GOOD)

    async def go():
        b = _backend(handler, fallback_model="openai/gpt-oss-120b")
        res = await b.map_fields(MapRequest(FIELDS, "p", "j"))
        await b.aclose()
        return res

    assert asyncio.run(go()).fills == {"f1": "Jane"}
    assert models == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


def test_schema_failure_falls_back_to_second_model():
    models = []

    def handler(req):
        m = json.loads(req.content)["model"]
        models.append(m)
        if m == "openai/gpt-oss-20b":
            return httpx.Response(400, json={"error": {"message": "Failed to generate JSON.",
                                                       "code": "json_validate_failed", "failed_generation": "{"}})
        return _ok(GOOD)

    async def go():
        b = _backend(handler, fallback_model="openai/gpt-oss-120b")
        res = await b.map_fields(MapRequest(FIELDS, "p", "j"))
        await b.aclose()
        return res

    assert asyncio.run(go()).fills == {"f1": "Jane"}
    assert models == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


def test_rate_limit_without_fallback_reports_wait():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "12"}, json={"error": {"message": "slow down"}})

    async def go():
        b = _backend(handler, fallback_model="")
        try:
            await b.map_fields(MapRequest(FIELDS, "p", "j"))
        finally:
            await b.aclose()

    with pytest.raises(RateLimited, match="12s"):
        asyncio.run(go())


def test_http_error_message_surfaces():
    def handler(req):
        return httpx.Response(401, json={"error": {"message": "Invalid API Key", "type": "invalid_request_error"}})

    async def go():
        b = _backend(handler)
        try:
            await b.draft_answer(DraftRequest("Why?", "job", "profile"))
        finally:
            await b.aclose()

    with pytest.raises(LLMError, match="401: Invalid API Key"):
        asyncio.run(go())


def test_missing_key_explains_how_to_fix(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("jobbot.llm.groq.keyring.get_password", lambda *a: None)
    b = GroqBackend(transport=httpx.MockTransport(lambda r: _ok(GOOD)))
    with pytest.raises(LLMError, match="set-groq-key"):
        asyncio.run(b.map_fields(MapRequest(FIELDS, "p", "j")))


def test_config_defaults_to_groq():
    cfg = config.load()
    assert cfg.llm.backend == "groq"
    assert type(get_backend(cfg)).__name__ == "GroqBackend"
