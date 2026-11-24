import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class GroqConfig:
    fast_model: str = "openai/gpt-oss-20b"       # field matching
    draft_model: str = "openai/gpt-oss-120b"     # open-ended answers
    fallback_model: str = "openai/gpt-oss-120b"  # used when fast_model hits its per-minute limit
    reasoning_effort: str = "low"                # gpt-oss only: low | medium | high


@dataclass(frozen=True)
class OpenRouterConfig:
    fast_model: str = "openai/gpt-oss-20b"
    draft_model: str = "openai/gpt-oss-120b"
    reasoning_effort: str = "low"
    zdr: bool = True                             # Zero Data Retention endpoints only


@dataclass(frozen=True)
class ClaudeConfig:
    exe: Path = Path.home() / ".local" / "bin" / "claude.exe"
    sandbox: Path = ROOT / ".llm-sandbox"
    fast_model: str = "haiku"
    draft_model: str = "sonnet"
    extra_flags: list[str] = field(default_factory=list)
    use_json_schema: bool = False   # --json-schema's StructuredOutput tool is denied by the lockdown


@dataclass(frozen=True)
class LlmConfig:
    backend: str = "groq"           # groq | claude-cli
    backup: str = "openrouter"      # openrouter | "" (none); only used when its API key is stored
    timeout_s: float = 30
    groq: GroqConfig = field(default_factory=GroqConfig)
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)


@dataclass(frozen=True)
class Config:
    brave_exe: Path
    profile_dir: Path
    port: int
    data_dir: Path
    cv_path: Path
    llm: LlmConfig = field(default_factory=LlmConfig)


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def _llm(raw: dict) -> LlmConfig:
    d, g, c, o = LlmConfig(), GroqConfig(), ClaudeConfig(), OpenRouterConfig()
    gr, cr, orr = raw.get("groq", {}), raw.get("claude", {}), raw.get("openrouter", {})
    return LlmConfig(
        backend=raw.get("backend", d.backend),
        backup=raw.get("backup", d.backup),
        openrouter=OpenRouterConfig(
            fast_model=orr.get("fast_model", o.fast_model),
            draft_model=orr.get("draft_model", o.draft_model),
            reasoning_effort=orr.get("reasoning_effort", o.reasoning_effort),
            zdr=bool(orr.get("zdr", o.zdr)),
        ),
        timeout_s=float(raw.get("timeout_s", d.timeout_s)),
        groq=GroqConfig(
            fast_model=gr.get("fast_model", g.fast_model),
            draft_model=gr.get("draft_model", g.draft_model),
            fallback_model=gr.get("fallback_model", g.fallback_model),
            reasoning_effort=gr.get("reasoning_effort", g.reasoning_effort),
        ),
        claude=ClaudeConfig(
            exe=_path(cr["exe"]) if "exe" in cr else c.exe,
            sandbox=_path(cr["sandbox"]) if "sandbox" in cr else c.sandbox,
            fast_model=cr.get("fast_model", c.fast_model),
            draft_model=cr.get("draft_model", c.draft_model),
            extra_flags=list(cr.get("extra_flags", [])),
            use_json_schema=bool(cr.get("use_json_schema", c.use_json_schema)),
        ),
    )


def load(path: Path = ROOT / "config.toml") -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return Config(
        brave_exe=_path(raw["brave"]["exe"]),
        profile_dir=_path(raw["brave"]["profile_dir"]),
        port=int(raw["brave"]["port"]),
        data_dir=_path(raw["data"]["dir"]),
        cv_path=_path(raw["data"]["cv"]),
        llm=_llm(raw.get("llm", {})),
    )
