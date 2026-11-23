"""Adapter: Claude Code CLI in headless mode, locked down to text-in / JSON-out.

Lockdown (verified flags, CLI 2.1.330 --help + llm-selftest):
  --safe-mode            no CLAUDE.md, skills, plugins, hooks, MCP, custom agents
  --restricted           ignore user/project/local settings files; no code-running tools
  --tools ""             no built-in tools
  --disallowedTools *    deny anything else that might appear
  --strict-mcp-config    no MCP servers (none given via --mcp-config)
  --permission-prompts none + --permission-mode dontAsk   anything that would prompt is denied
Plus: empty working dir, API-key env vars removed (so the Claude subscription login is used).
NOT --bare: it skips OAuth, which breaks subscription login.
NOT --json-schema by default: its StructuredOutput tool is denied by this lockdown, which wastes
turns (~2x latency); JSON is requested in the system prompt and parsed/validated locally instead.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..model import DraftRequest, MapRequest, MapResult
from . import prompts
from .base import LLMError
from .schemas import DRAFT_SCHEMA, MAP_SCHEMA, extract_json, validate_draft, validate_map

SCRUB_EXACT = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"}
SCRUB_PREFIX = ("CLAUDE_CODE_USE_",)

LOCKDOWN = [
    "--safe-mode",
    "--restricted",          # also ignore user/project/local settings files (keeps subscription auth)
    "--tools", "",
    "--disallowedTools", "*",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--permission-mode", "dontAsk",
    "--permission-prompts", "none",
]


def scrubbed_env(base: dict[str, str] | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if k.upper() not in SCRUB_EXACT and not k.upper().startswith(SCRUB_PREFIX)}
    env.update(extra or {})
    return env


@dataclass
class CallMeta:
    model: str
    seconds: float
    raw: dict = field(repr=False, default_factory=dict)


class ClaudeCli:
    def __init__(self, exe: Path, sandbox: Path, fast_model: str = "haiku", draft_model: str = "sonnet",
                 timeout_s: float = 90, extra_flags: list[str] | None = None, use_json_schema: bool = True):
        self.exe, self.sandbox = Path(exe), Path(sandbox)
        self.fast_model, self.draft_model = fast_model, draft_model
        self.timeout_s = timeout_s
        self.extra_flags = list(extra_flags or [])
        self.use_json_schema = use_json_schema
        self.last_meta: CallMeta | None = None

    def argv(self, model: str, system: str, schema: dict | None) -> list[str]:
        args = [str(self.exe), "-p", *LOCKDOWN, *self.extra_flags,
                "--model", model, "--output-format", "json", "--system-prompt", system]
        if schema is not None and self.use_json_schema:
            args += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        return args

    async def call(self, model: str, system: str, prompt: str, schema: dict | None,
                   env_extra: dict[str, str] | None = None) -> dict:
        """Run one locked-down request; returns the parsed JSON payload (not yet validated)."""
        self.sandbox.mkdir(parents=True, exist_ok=True)
        if not self.use_json_schema and schema is not None:
            system += "\n\nReply with ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)
        t0 = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *self.argv(model, system, schema),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.sandbox), env=scrubbed_env(extra=env_extra),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise LLMError(f"Claude CLI timed out after {self.timeout_s:.0f}s")
        secs = time.perf_counter() - t0

        try:
            data = json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            msg = (err or out).decode("utf-8", "replace").strip()[:500]
            raise LLMError(f"Claude CLI exited {proc.returncode} without JSON: {msg}")
        self.last_meta = CallMeta(model=model, seconds=secs, raw=data)
        if data.get("is_error"):
            raise LLMError(f"Claude CLI error ({data.get('subtype')}): {str(data.get('result'))[:500]}")

        payload = data.get("structured_output")
        if isinstance(payload, dict):
            return payload
        try:
            return extract_json(str(data.get("result", "")))
        except ValueError as e:
            raise LLMError(f"unusable model output: {e}")

    async def map_fields(self, req: MapRequest) -> MapResult:
        raw = await self.call(self.fast_model, prompts.MAP_SYSTEM, prompts.map_prompt(req), MAP_SCHEMA)
        return validate_map(raw, req.fields)

    async def extract(self, system: str, prompt: str, schema: dict, name: str) -> dict:
        return await self.call(self.draft_model, system, prompt, schema)

    async def draft_answer(self, req: DraftRequest) -> str:
        raw = await self.call(self.draft_model, prompts.DRAFT_SYSTEM, prompts.draft_prompt(req), DRAFT_SCHEMA)
        try:
            return validate_draft(raw)
        except ValueError as e:
            raise LLMError(str(e))
