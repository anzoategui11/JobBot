import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    brave_exe: Path
    profile_dir: Path
    port: int
    data_dir: Path
    cv_path: Path


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def load(path: Path = ROOT / "config.toml") -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return Config(
        brave_exe=_path(raw["brave"]["exe"]),
        profile_dir=_path(raw["brave"]["profile_dir"]),
        port=int(raw["brave"]["port"]),
        data_dir=_path(raw["data"]["dir"]),
        cv_path=_path(raw["data"]["cv"]),
    )
