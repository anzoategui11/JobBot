"""Adapter: GroqCloud chat completions (OpenAI-compatible HTTP API).

Docs (console.groq.com/docs, read 11/22/2026):
- POST https://api.groq.com/openai/v1/chat/completions, header `Authorization: Bearer <key>`.
- Text at choices[0].message.content. `max_completion_tokens` (max_tokens is deprecated).
- Strict structured outputs: response_format={"type":"json_schema","json_schema":{"name","strict":true,"schema"}}
  on openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.8-27b, meta-models/muse-spark-open.
  Strict needs every property in `required` and additionalProperties:false on every object.
  Other models: {"type":"json_object"} + schema in the system prompt.
- 429 + `retry-after` header when rate limited. Error body {"error":{"message","type"}}.
- A plain HTTP API has no tools, so there is nothing to lock down; LLM output is still validated locally.

API key: env GROQ_API_KEY, else Windows Credential Manager (`python -m jobbot set-groq-key`).
"""

import json
import os
import time
from dataclasses import dataclass, field

import httpx
import keyring

from ..model import DraftBatch, DraftRequest, MapRequest, MapResult
from . import prompts
from .base import LLMError
from .schemas import DRAFT_SCHEMA, DRAFTS_SCHEMA, MAP_SCHEMA, extract_json, validate_draft, validate_drafts, validate_map

BASE_URL = "https://api.groq.com/openai/v1"
KEYRING_SERVICE = "JobBot"
KEYRING_USER = "groq-api-key"
STRICT_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "meta-models/muse-spark-open"}


class RateLimited(LLMError):
    def __init__(self, model: str, retry_after: float | None):
        self.model, self.retry_after = model, retry_after
        wait = f" - try again in {retry_after:.0f}s" if retry_after else ""
        super().__init__(f"Groq rate limit reached for {model}{wait}")


class BadGeneration(LLMError):
    """Groq rejected the model's own output as not matching the strict schema (HTTP 400 json_validate_failed)."""
    def __init__(self, detail: str):
        super().__init__(f"Groq model output failed the schema: {detail}")


def get_api_key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def set_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)


@dataclass
class CallMeta:
    model: str
    seconds: float
    remaining_tokens: str | None = None     # x-ratelimit-remaining-tokens (per minute)
    remaining_requests: str | None = None   # x-ratelimit-remaining-requests (per day)
    raw: dict = field(repr=False, default_factory=dict)


class GroqBackend:
    def __init__(self, fast_model: str = "openai/gpt-oss-20b", draft_model: str = "openai/gpt-oss-120b",
                 fallback_model: str = "", reasoning_effort: str = "low", timeout_s: float = 30,
                 api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.fast_model, self.draft_model, self.fallback_model = fast_model, draft_model, fallback_model
        self.reasoning_effort = reasoning_effort
        self._api_key = api_key
        self._timeout, self._transport = timeout_s, transport
        self._client: httpx.AsyncClient | None = None
        self.last_meta: CallMeta | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            key = self._api_key or get_api_key()
            if not key:
                raise LLMError("No Groq API key. Create one at console.groq.com/keys, then run: "
                               "uv run python -m jobbot set-groq-key")
            # One long-lived client: keep-alive avoids a TLS handshake per hotkey press.
            self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=self._timeout, transport=self._transport,
                                             headers={"Authorization": f"Bearer {key}"})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def body(self, model: str, system: str, prompt: str, schema: dict, name: str, max_tokens: int) -> dict:
        if model in STRICT_MODELS:
            fmt = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
        else:
            fmt = {"type": "json_object"}
            system += "\n\nReply with ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_completion_tokens": max_tokens,
            "response_format": fmt,
        }
        if model.startswith("openai/gpt-oss") and self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    async def call(self, model: str, system: str, prompt: str, schema: dict, name: str,
                   max_tokens: int = 2048) -> dict:
        t0 = time.perf_counter()
        try:
            r = await self._http().post("/chat/completions",
                                        json=self.body(model, system, prompt, schema, name, max_tokens))
        except httpx.HTTPError as e:
            raise LLMError(f"Groq request failed: {type(e).__name__}: {e}")
        secs = time.perf_counter() - t0
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            raise RateLimited(model, float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
        try:
            data = r.json()
        except ValueError:
            raise LLMError(f"Groq HTTP {r.status_code}: non-JSON response")
        if r.status_code >= 400:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            msg = err.get("message") or str(data)[:300]
            if err.get("failed_generation"):
                msg += f" | failed_generation: {str(err['failed_generation'])[:400]}"
            raise BadGeneration(msg) if err.get("code") == "json_validate_failed" else \
                LLMError(f"Groq HTTP {r.status_code}: {msg}")
        self.last_meta = CallMeta(model, secs, r.headers.get("x-ratelimit-remaining-tokens"),
                                  r.headers.get("x-ratelimit-remaining-requests"), data)
        try:
            content = data["choices"][0]["message"]["content"] or ""
            return extract_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"unusable Groq output: {e}")

    async def map_fields(self, req: MapRequest) -> MapResult:
        args = (prompts.MAP_SYSTEM, prompts.map_prompt(req), MAP_SCHEMA, "form_fill")
        try:
            raw = await self.call(self.fast_model, *args)
        except (RateLimited, BadGeneration):     # busy, or the small model broke the schema: try the big one
            if not self.fallback_model:
                raise
            raw = await self.call(self.fallback_model, *args)
        return validate_map(raw, req.fields)

    async def extract(self, system: str, prompt: str, schema: dict, name: str) -> dict:
        return await self.call(self.draft_model, system, prompt, schema, name, max_tokens=6000)

    async def draft_answer(self, req: DraftRequest) -> str:
        raw = await self.call(self.draft_model, prompts.DRAFT_SYSTEM, prompts.draft_prompt(req),
                              DRAFT_SCHEMA, "draft_answer", max_tokens=1500)
        try:
            return validate_draft(raw)
        except ValueError as e:
            raise LLMError(str(e))

    async def draft_answers(self, batch: DraftBatch) -> dict[str, str]:
        budget = min(6000, 800 + sum(q.max_words * 3 for q in batch.questions))   # words->tokens + reasoning
        raw = await self.call(self.draft_model, prompts.DRAFTS_SYSTEM, prompts.drafts_prompt(batch),
                              DRAFTS_SCHEMA, "draft_answers", max_tokens=budget)
        return validate_drafts(raw, batch.questions)
