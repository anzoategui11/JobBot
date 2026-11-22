from ..config import Config
from .base import Backend, LLMError


def get_backend(cfg: Config) -> Backend:
    llm = cfg.llm
    if llm.backend == "groq":
        from .groq import GroqBackend
        g = llm.groq
        return GroqBackend(g.fast_model, g.draft_model, g.fallback_model, g.reasoning_effort, llm.timeout_s)
    if llm.backend == "claude-cli":
        from .claude_cli import ClaudeCli
        c = llm.claude
        return ClaudeCli(c.exe, c.sandbox, c.fast_model, c.draft_model, max(llm.timeout_s, 90),
                         c.extra_flags, c.use_json_schema)
    raise ValueError(f"unknown llm.backend {llm.backend!r} in config.toml")


__all__ = ["Backend", "LLMError", "get_backend"]
