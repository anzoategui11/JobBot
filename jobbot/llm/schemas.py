"""JSON schemas the LLM must answer with, plus local validation of whatever comes back.

The validator is the real safety net: LLM output is untrusted (job postings can contain
prompt injection), so only values that fit the request are ever passed on.
"""

import json
import re

from ..model import CHOICE_KINDS, LOCAL_ONLY_KINDS, FormField, JobInfo, MapResult

_str = {"type": "string"}

MAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["job", "fills", "unknown"],
    "properties": {
        "job": {
            "type": "object",
            "additionalProperties": False,
            "required": ["company", "position", "location", "language"],
            "properties": {
                "company": _str, "position": _str, "location": _str,
                "language": {"type": "string", "enum": ["en", "es", "other"]},
            },
        },
        "fills": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "value"],
            "properties": {"id": _str, "value": _str}}},
        "unknown": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "question"],
            "properties": {"id": _str, "question": _str}}},
    },
}

DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": _str},
}

MAX_VALUE = 4000
MAX_META = 200
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(s: object, limit: int = MAX_VALUE) -> str:
    return _CTRL.sub("", str(s)).strip()[:limit]


def extract_json(text: str) -> dict:
    """Parse a JSON object from model text (tolerates ```json fences / leading prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model output")
        text = text[start:end + 1]
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def _match_option(value: str, options: list[str]) -> str | None:
    v = value.strip().casefold()
    for opt in options:
        if opt.strip().casefold() == v:
            return opt
    return None


def validate_map(raw: dict, fields: list[FormField]) -> MapResult:
    by_id = {f.id: f for f in fields if f.kind not in LOCAL_ONLY_KINDS}
    dropped: list[str] = []

    job_raw = raw.get("job") if isinstance(raw.get("job"), dict) else {}
    lang = clean(job_raw.get("language", "en"), 10)
    job = JobInfo(
        company=clean(job_raw.get("company", ""), MAX_META),
        position=clean(job_raw.get("position", ""), MAX_META),
        location=clean(job_raw.get("location", ""), MAX_META),
        language=lang if lang in {"en", "es", "other"} else "other",
    )

    fills: dict[str, str] = {}
    for item in raw.get("fills") or []:
        if not isinstance(item, dict):
            continue
        fid, value = str(item.get("id", "")), clean(item.get("value", ""))
        f = by_id.get(fid)
        if f is None:
            dropped.append(f"fill for unknown field id {fid!r}")
            continue
        if not value:
            continue
        if f.kind in CHOICE_KINDS:
            opt = _match_option(value, f.options)
            if opt is None:
                dropped.append(f"{f.label!r}: {value!r} is not one of the options")
                continue
            value = opt
        elif f.kind == "checkbox":
            if value.casefold() not in {"true", "false"}:
                dropped.append(f"{f.label!r}: checkbox value {value!r} is not true/false")
                continue
            value = value.casefold()
        fills[fid] = value

    unknown: dict[str, str] = {}
    for item in raw.get("unknown") or []:
        if isinstance(item, dict):
            fid = str(item.get("id", ""))
            if fid in by_id and fid not in fills:
                unknown[fid] = clean(item.get("question", ""), 300) or by_id[fid].label

    # Anything the model silently skipped still gets asked (empty fields only).
    for fid, f in by_id.items():
        if fid not in fills and fid not in unknown and not f.current_value:
            unknown[fid] = f.label

    return MapResult(job=job, fills=fills, unknown=unknown, dropped=dropped)


def validate_draft(raw: dict) -> str:
    answer = clean(raw.get("answer", ""))
    if not answer:
        raise ValueError("empty draft")
    return answer
