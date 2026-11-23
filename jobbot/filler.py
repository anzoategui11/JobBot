"""Write values into the page (via agent.js, frame by frame) and attach the CV.

Only elements tagged data-jobbot-id by the extractor are touched. Nothing here clicks buttons,
submits, navigates or scrolls.
"""

from pathlib import Path

from playwright.async_api import Frame

from .fields import Extraction
from .model import FormField


async def apply(ex: Extraction, fills: dict[str, str]) -> dict[str, str]:
    """Fill {field id: value}. Returns {field id: status} ("ok", "typed", "manual", "no such option", ...)."""
    by_id = {f.id: f for f in ex.fields}
    per_frame: dict[Frame, list[dict]] = {}
    for fid, value in fills.items():
        f = by_id.get(fid)
        frame = ex.frame_of.get(fid)
        if f is None or frame is None or f.kind in ("file", "password"):
            continue
        per_frame.setdefault(frame, []).append({"id": fid, "kind": f.kind, "value": value})
    out: dict[str, str] = {}
    for frame, items in per_frame.items():
        try:
            out.update(await frame.evaluate("items => window.__jobbotFill(items)", items))
        except Exception as e:                       # frame navigated away mid-fill
            out.update({i["id"]: f"error: {type(e).__name__}" for i in items})
    return out


async def attach_cv(ex: Extraction, f: FormField, cv: Path) -> str:
    """Attach the CV to a file input. Skipped when the page already shows the file (e.g. LinkedIn's saved resumes)."""
    if not cv.exists():
        return f"CV not found: {cv}"
    frame = ex.frame_of[f.id]
    try:
        if await frame.evaluate("n => window.__jobbotPageHas(n)", cv.stem):
            return "already attached"
        await frame.locator(f'[data-jobbot-id="{f.id}"]').first.set_input_files(str(cv), timeout=5000)
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}: {str(e)[:100]}"
