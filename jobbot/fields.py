"""Collect form-field descriptors and job context from every frame of a page (via agent.js)."""

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playwright.async_api import Frame, Page

from .model import FormField

KINDS = {"text", "email", "tel", "number", "date", "select", "radio", "checkbox", "textarea", "file",
         "password", "combobox"}
JOB_CHARS = 6000


@dataclass
class Extraction:
    url: str
    title: str
    fields: list[FormField]
    frame_of: dict[str, Frame]              # field id -> frame that owns it (for the filler)
    job_text: str                           # <= JOB_CHARS, top frame first, then frames with fields
    headings: dict[str, str] = field(default_factory=dict)   # h1 / og:title / site name hints
    errors: list[str] = field(default_factory=list)


def _field(d: dict) -> FormField | None:
    kind = str(d.get("kind", ""))
    fid = str(d.get("id", ""))
    if kind not in KINDS or not fid:
        return None
    return FormField(
        id=fid, kind=kind, label=str(d.get("label") or "")[:200],
        options=[str(o)[:120] for o in (d.get("options") or [])][:200],
        required=bool(d.get("required")),
        current_value="" if kind == "password" else str(d.get("current_value") or "")[:300],
        autocomplete=str(d.get("autocomplete") or "")[:40], accept=str(d.get("accept") or "")[:100],
        hint=str(d.get("hint") or "")[:120],
    )


# Security-check frames (Cloudflare, reCAPTCHA, hCaptcha, Arkose) are never read, injected into or waited on.
CHALLENGE_HOSTS = ("challenges.cloudflare.com", "hcaptcha.com", "recaptcha.net", "arkoselabs.com",
                   "funcaptcha.com")
CHALLENGE_PATHS = ("google.com/recaptcha", "gstatic.com/recaptcha")


def is_challenge(url: str) -> bool:
    low = (url or "").lower()
    host = urlsplit(low).hostname or ""
    return any(host == h or host.endswith("." + h) for h in CHALLENGE_HOSTS) or \
        any(p in low for p in CHALLENGE_PATHS)


async def evaluate(target, expression: str, arg=None, timeout: float = 10.0):
    """frame/page.evaluate that gives up instead of hanging (a frozen or half-loaded frame never answers)."""
    coro = target.evaluate(expression) if arg is None else target.evaluate(expression, arg)
    return await asyncio.wait_for(coro, timeout)


async def _frame_data(frame: Frame, script: str) -> dict:
    if is_challenge(frame.url):
        return {"ok": False, "error": "security check frame (skipped)", "fields": [], "url": frame.url}
    try:
        if not await evaluate(frame, "typeof window.__jobbotFill === 'function'", timeout=3):
            await evaluate(frame, script, timeout=5)   # frame created before attach / init script missed it
        return await evaluate(frame, "n => window.__jobbotExtract(n)", JOB_CHARS, timeout=8)
    except Exception as e:                        # detached, navigating, frozen, or a blank frame
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}", "fields": [], "url": frame.url}


async def extract(page: Page, script: str) -> Extraction:
    fields: list[FormField] = []
    frame_of: dict[str, Frame] = {}
    errors: list[str] = []
    texts: list[str] = []
    headings: dict[str, str] = {}
    modal = False
    for frame in page.frames:                     # main frame comes first
        data = await _frame_data(frame, script)
        if not data.get("ok"):
            if data.get("fields") == [] and frame is page.main_frame:
                errors.append(f"main frame: {data.get('error')}")
            continue
        if frame is page.main_frame and data.get("modal"):
            modal = True                          # an open dialog is the form: other frames are page chrome
            headings["dialog"] = str(data.get("modal_title") or "dialog")[:120]
        raw = data.get("fields") or []
        if modal and frame is not page.main_frame:
            raw = []
        got = [f for f in map(_field, raw) if f and f.id not in frame_of]
        for f in got:
            frame_of[f.id] = frame
        fields += got
        job = data.get("job") or {}
        if frame is page.main_frame:
            headings.update({k: job.get(k, "") for k in ("h1", "og_title", "site") if job.get(k)})
            texts.insert(0, job.get("text") or "")
        elif got and job.get("text"):
            texts.append(job["text"])
    job_text = "\n\n".join(t for t in texts if t)[:JOB_CHARS]
    return Extraction(page.url, await page.title(), fields, frame_of, job_text, headings, errors)


def summary(ex: Extraction) -> str:
    """Panel text for the Phase 4 dry run."""
    where = f"in the “{ex.headings['dialog']}” dialog" if "dialog" in ex.headings else \
        f"in {len(set(ex.frame_of.values()))} frame(s)"
    lines = [f"{len(ex.fields)} fields found {where}. Nothing was filled.", ""]
    for f in ex.fields:
        extra = []
        if f.required:
            extra.append("required")
        if f.options:
            extra.append(f"{len(f.options)} options")
        if f.current_value:
            extra.append(f"= {f.current_value[:30]}")
        lines.append(f"• {f.label or '(no label)'} · {f.kind} · {f.id}" + (f" ({', '.join(extra)})" if extra else ""))
    lines += ["", f"Job text: {len(ex.job_text)} chars" + (f" · {ex.headings.get('h1', '')[:60]}" if ex.headings else "")]
    lines += [f"⚠ {e}" for e in ex.errors]
    return "\n".join(lines)
