"""Small local state that survives restarts (last role picked). Lives in state/ under the code dir (git-ignored)."""

import json
from pathlib import Path

from .config import ROOT

DEFAULT = ROOT / "state" / "state.json"


def load(path: Path = DEFAULT) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict, path: Path = DEFAULT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)
