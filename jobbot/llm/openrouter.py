"""Adapter: OpenRouter chat completions (OpenAI-compatible). Used as the automatic backup when Groq is out.

Docs (openrouter.ai/docs, read 11/24/2026):
- POST https://openrouter.ai/api/v1/chat/completions, `Authorization: Bearer <key>`.
- Strict structured outputs: same response_format as OpenAI (json_schema + strict: true); per-provider support,
  so `provider.require_parameters: true` routes only to endpoints that honour it.
- Privacy: `provider.data_collection: "deny"` (no providers that store/train on prompts) and `provider.zdr: true`
  (Zero Data Retention endpoints only).
- Reasoning: `reasoning: {effort, exclude}`; exclude keeps the reasoning text out of the response.
- 429 (+ Retry-After when known) when rate limited; 402 when the account is out of credits.
- gpt-oss models have no :free variant (checked /api/v1/models); paid cost is ~$0.03-0.25 per million tokens,
  i.e. a fraction of a cent per application.

API key: env OPENROUTER_API_KEY, else Windows Credential Manager (`python -m jobbot set-openrouter-key`).
"""

import os

import keyring

from .base import LLMError
from .groq import GroqBackend

KEYRING_SERVICE = "JobBot"
KEYRING_USER = "openrouter-api-key"


def get_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def set_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)


class OpenRouterBackend(GroqBackend):
    NAME = "OpenRouter"
    BASE_URL = "https://openrouter.ai/api/v1"
    NO_KEY = ("No OpenRouter API key. Create one at openrouter.ai/keys, then run: "
              "uv run python -m jobbot set-openrouter-key")

    def __init__(self, *args, zdr: bool = True, **kw):
        super().__init__(*args, **kw)
        self.zdr = zdr

    def api_key(self) -> str | None:
        return get_api_key()

    def headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}", "X-OpenRouter-Title": "JobBot"}

    def strict(self, model: str) -> bool:
        return True                                   # require_parameters only routes to endpoints that support it

    def body(self, model, system, prompt, schema, name, max_tokens):
        body = super().body(model, system, prompt, schema, name, max_tokens)
        body.pop("reasoning_effort", None)
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort, "exclude": True}
        body["provider"] = {"require_parameters": True, "data_collection": "deny", "zdr": self.zdr}
        return body

    def http_error(self, status: int, err: dict, msg: str) -> LLMError:
        if status == 402:
            return LLMError("OpenRouter: out of credits - add some at openrouter.ai/settings/credits")
        if status == 404 and "endpoint" in msg.lower():
            return LLMError(f"OpenRouter: no provider meets the privacy/JSON requirements for this model ({msg})")
        return super().http_error(status, err, msg)
