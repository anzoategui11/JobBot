"""OpenRouter backup adapter + automatic fallback chain + "already applied" lookup. No network."""

import asyncio
import json

import httpx
import pytest

from jobbot import applog, config
from jobbot.llm import get_backend
from jobbot.llm.base import LLMError
from jobbot.llm.chain import Chain
from jobbot.llm.groq import GroqBackend, RateLimited
from jobbot.llm.openrouter import OpenRouterBackend
from jobbot.model import FormField, MapRequest

FIELDS = [FormField("f1", "text", "First name")]
GOOD = {"job": {"company": "Acme", "position": "AI Eng", "location": "Remote", "language": "en"},
        "fills": [{"id": "f1", "value": "Jane"}], "unknown": []}


def _ok(content: dict):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}]})


def _router(handler, **kw):
    return OpenRouterBackend("openai/gpt-oss-20b", "openai/gpt-oss-120b", "openai/gpt-oss-120b", "low",
                             api_key="sk-or-test", transport=httpx.MockTransport(handler), **kw)


def test_openrouter_request_is_private_strict_and_quiet():
    seen = {}

    def handler(req):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return _ok(GOOD)

    async def go():
        b = _router(handler)
        res = await b.map_fields(MapRequest(FIELDS, "p", "j"))
        await b.aclose()
        return res

    assert asyncio.run(go()).fills == {"f1": "Jane"}
    body = seen["body"]
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-or-test"
    assert seen["headers"]["x-openrouter-title"] == "JobBot"
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny", "zdr": True}
    assert body["reasoning"] == {"effort": "low", "exclude": True} and "reasoning_effort" not in body
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"]


@pytest.mark.parametrize("status,body,expect", [
    (402, {"error": {"code": 402, "message": "Insufficient credits"}}, "out of credits"),
    (429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, "OpenRouter rate limit"),
])
def test_openrouter_errors_are_readable(status, body, expect):
    async def go():
        b = _router(lambda req: httpx.Response(status, json=body, headers={"retry-after": "5"}))
        try:
            await b.map_fields(MapRequest(FIELDS, "p", "j"))
        finally:
            await b.aclose()

    with pytest.raises(LLMError, match=expect):
        asyncio.run(go())


class Fake:
    NAME = "Fake"

    def __init__(self, fail: LLMError | None = None):
        self.fail, self.calls = fail, 0

    async def map_fields(self, req):
        self.calls += 1
        if self.fail:
            raise self.fail
        return f"answer from {self.NAME}"


def test_chain_uses_backup_only_when_primary_fails():
    async def go():
        ok, backup = Fake(), Fake()
        c = Chain(ok, backup)
        assert await c.map_fields(None) == "answer from Fake" and backup.calls == 0 and c.take_note() == ""

        busy = Fake(RateLimited("openai/gpt-oss-20b", 12.0))
        backup.NAME = "OpenRouter"
        c = Chain(busy, backup)
        assert await c.map_fields(None) == "answer from OpenRouter"
        note = c.take_note()
        assert "OpenRouter backup" in note and "12s" in note and c.take_note() == ""

        c = Chain(Fake(LLMError("Groq down")), Fake(LLMError("no credits")))
        with pytest.raises(LLMError, match="Groq down.*backup: no credits"):
            await c.map_fields(None)
    asyncio.run(go())


def test_backup_only_when_its_key_is_stored(monkeypatch):
    cfg = config.load()
    monkeypatch.setattr("jobbot.llm.openrouter.get_api_key", lambda: None)
    assert isinstance(get_backend(cfg), GroqBackend)
    monkeypatch.setattr("jobbot.llm.openrouter.get_api_key", lambda: "sk-or-x")
    b = get_backend(cfg)
    assert isinstance(b, Chain) and isinstance(b.backup, OpenRouterBackend)
    assert isinstance(get_backend(cfg, with_backup=False), GroqBackend)


def test_previous_application_lookup(tmp_path):
    log, pend = tmp_path / "applications.csv", tmp_path / "pending.csv"
    applog.log(log, {"Portal": "LinkedIn", "Company": "Acme Corp", "Position": "Senior Data Engineer",
                     "Application link": "https://www.linkedin.com/jobs/view/42/"}, pend)
    hit = applog.previous(log, ["https://www.linkedin.com/jobs/search/?currentJobId=42"], pending=pend)
    assert hit and hit[0] == "link"
    hit = applog.previous(log, ["https://acme.com/careers/7"], "ACME corp", "Senior Data Engineer (Remote)", pend)
    assert hit and hit[0] == "similar"
    assert applog.previous(log, ["https://acme.com/careers/7"], "Acme Corp", "ML Engineer", pend) is None
    assert applog.previous(log, ["https://other.com/x"], "Initech", "Senior Data Engineer", pend) is None
    assert applog.previous(tmp_path / "missing.csv", ["https://x.com"], "A", "B", pend) is None
