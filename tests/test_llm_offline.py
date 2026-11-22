"""Offline tests for the LLM layer: validation, prompts, CLI argv/env. No model calls."""

import asyncio
import json
from pathlib import Path

import pytest

from jobbot.llm import claude_cli
from jobbot.llm.base import LLMError
from jobbot.llm.claude_cli import ClaudeCli, scrubbed_env
from jobbot.llm.prompts import map_prompt
from jobbot.llm.schemas import MAP_SCHEMA, extract_json, validate_map
from jobbot.model import FormField, MapRequest

FIELDS = [
    FormField("f1", "text", "First name"),
    FormField("f2", "radio", "Authorized?", ["Yes", "No"]),
    FormField("f3", "checkbox", "Agree to terms"),
    FormField("f4", "password", "Password"),
    FormField("f5", "file", "Resume"),
    FormField("f6", "text", "Already filled", current_value="x"),
    FormField("f7", "text", "Nickname"),
]


def test_validator_keeps_good_and_drops_bad():
    raw = {"job": {"company": "Acme", "position": "AI Eng", "location": "Remote", "language": "es"},
           "fills": [{"id": "f1", "value": "Jane"}, {"id": "f2", "value": "yes"},
                     {"id": "f3", "value": "maybe"}, {"id": "f4", "value": "hunter2"},
                     {"id": "zz", "value": "x"}],
           "unknown": []}
    res = validate_map(raw, FIELDS)
    assert res.fills == {"f1": "Jane", "f2": "Yes"}          # option canonicalised
    assert "f4" not in res.fills and "f5" not in res.fills     # local-only kinds never filled
    assert set(res.unknown) == {"f3", "f7"}                   # skipped/invalid empty fields get asked
    assert "f6" not in res.unknown                            # already has a value
    assert res.job.company == "Acme" and res.job.language == "es"
    assert len(res.dropped) == 3


def test_validator_rejects_non_option_radio():
    res = validate_map({"job": {}, "fills": [{"id": "f2", "value": "HACKED"}], "unknown": []}, FIELDS)
    assert "f2" not in res.fills and "f2" in res.unknown


def test_prompt_never_contains_local_only_fields():
    p = map_prompt(MapRequest(FIELDS, "profile", "job"))
    assert '"f4"' not in p and '"f5"' not in p and "Password" not in p


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_env_scrub():
    env = scrubbed_env({"ANTHROPIC_API_KEY": "k", "anthropic_auth_token": "t", "CLAUDE_CODE_USE_BEDROCK": "1",
                        "PATH": "p"}, extra={"X": "1"})
    assert env == {"PATH": "p", "X": "1"}


def test_argv_lockdown_and_no_bare():
    cli = ClaudeCli(Path("claude.exe"), Path("sb"), use_json_schema=True)
    argv = cli.argv("haiku", "sys", MAP_SCHEMA)
    assert argv[1] == "-p"
    for flag in ("--safe-mode", "--restricted", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--disallowedTools") + 1] == "*"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == MAP_SCHEMA
    assert "--bare" not in argv and not any("dangerously" in a for a in argv)


class _FakeProc:
    def __init__(self, out: bytes, rc: int = 0):
        self.out, self.returncode = out, rc
        self.stdin_seen = None

    async def communicate(self, data):
        self.stdin_seen = data
        return self.out, b""


def _patch_exec(monkeypatch, payload: dict, seen: dict):
    async def fake_exec(*argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        seen["proc"] = _FakeProc(json.dumps(payload).encode())
        return seen["proc"]
    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)


def test_call_uses_stdin_sandbox_and_structured_output(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    _patch_exec(monkeypatch, {"is_error": False, "result": "", "structured_output": {"answer": "hi"}}, seen)
    cli = ClaudeCli(Path("claude.exe"), tmp_path / "sb")
    out = asyncio.run(cli.call("haiku", "sys", "PROMPT-TEXT", {"type": "object"}))
    assert out == {"answer": "hi"}
    assert seen["proc"].stdin_seen == b"PROMPT-TEXT"
    assert "PROMPT-TEXT" not in " ".join(seen["argv"])         # prompt never on the command line
    assert seen["kw"]["cwd"] == str(tmp_path / "sb")
    assert "ANTHROPIC_API_KEY" not in seen["kw"]["env"]


def test_call_falls_back_to_result_text_and_reports_errors(monkeypatch, tmp_path):
    seen = {}
    _patch_exec(monkeypatch, {"is_error": False, "result": '```json\n{"answer": "x"}\n```'}, seen)
    cli = ClaudeCli(Path("claude.exe"), tmp_path)
    assert asyncio.run(cli.call("haiku", "s", "p", None)) == {"answer": "x"}

    _patch_exec(monkeypatch, {"is_error": True, "subtype": "error_during_execution", "result": "boom"}, seen)
    with pytest.raises(LLMError, match="boom"):
        asyncio.run(cli.call("haiku", "s", "p", None))
