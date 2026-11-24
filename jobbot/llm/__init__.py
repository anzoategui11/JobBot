from ..config import Config
from .base import Backend, LLMError


def get_backend(cfg: Config, with_backup: bool = True) -> Backend:
    main = _primary(cfg)
    if with_backup and cfg.llm.backup == "openrouter":
        from .openrouter import OpenRouterBackend, get_api_key
        if get_api_key():                         # backup is opt-in: only once you've stored its key
            from .chain import Chain
            o = cfg.llm.openrouter
            return Chain(main, OpenRouterBackend(o.fast_model, o.draft_model, o.draft_model, o.reasoning_effort,
                                                 cfg.llm.timeout_s, zdr=o.zdr))
    return main


def _primary(cfg: Config) -> Backend:
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
