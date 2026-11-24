"""Write values into the page (via agent.js, frame by frame) and attach the CV.

Only elements tagged data-jobbot-id by the extractor are touched. Nothing here clicks buttons,
submits, navigates or scrolls.
"""

from pathlib import Path

from playwright.async_api import Frame

from .fields import Extraction
from .model import FormField


async def values(ex: Extraction, ids: list[str]) -> dict[str, str | None]:
    """Current full text of fields (None = no longer on the page)."""
    out: dict[str, str | None] = {}
    per_frame: dict[Frame, list[str]] = {}
    for fid in ids:
        if (frame := ex.frame_of.get(fid)) is not None:
            per_frame.setdefault(frame, []).append(fid)
    for frame, fids in per_frame.items():
        try:
            out.update(await frame.evaluate("ids => window.__jobbotValues(ids)", fids))
        except Exception:
            out.update({fid: None for fid in fids})
    return out


async def apply(ex: Extraction, fills: dict[str, str], if_empty: bool = False,
                choices: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Fill {field id: value}. Returns {field id: status} ("ok", "typed", "manual", "no such option", ...).
    if_empty: leave fields you've typed into meanwhile alone ("kept").
    choices: filled with {id: options seen} for dropdowns whose list had no match (status "choose")."""
    by_id = {f.id: f for f in ex.fields}
    per_frame: dict[Frame, list[dict]] = {}
    for fid, value in fills.items():
        f = by_id.get(fid)
        frame = ex.frame_of.get(fid)
        if f is None or frame is None or f.kind in ("file", "password"):
            continue
        per_frame.setdefault(frame, []).append({"id": fid, "kind": f.kind, "value": value, "ifEmpty": if_empty})
    out: dict[str, str] = {}
    for frame, items in per_frame.items():
        try:
            got = await frame.evaluate("items => window.__jobbotFill(items)", items)
            for fid, s in got.items():
                if isinstance(s, dict):              # dropdown opened but nothing matched: its options
                    if choices is not None:
                        choices[fid] = [str(o)[:120] for o in s.get("options") or []][:150]
                    s = str(s.get("status", "choose"))
                out[fid] = s
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
