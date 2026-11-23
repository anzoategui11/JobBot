"""`python -m jobbot seed-profile`: first draft of profile.md from the CV PDF.

The model only extracts facts into a fixed JSON shape; this module renders the Markdown,
so the file layout is always the one profile.py expects.
"""

from pathlib import Path

from pypdf import PdfReader

from .config import Config
from .llm.base import Backend
from .llm.schemas import clean

_s = {"type": "string"}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


SEED_SCHEMA = _obj({
    "contact": _obj({k: _s for k in
                     ("first_name", "last_name", "email", "phone", "city", "country", "linkedin", "github", "website")}),
    "headline": _s,
    "experience_summary": _s,
    "experience": _arr(_obj({"title": _s, "company": _s, "location": _s, "start": _s, "end": _s,
                             "highlights": _arr(_s)})),
    "education": _arr(_obj({"degree": _s, "school": _s, "year": _s})),
    "projects": _arr(_obj({"name": _s, "description": _s, "technologies": _arr(_s)})),
    "skills": _arr(_obj({"category": _s, "items": _arr(_s)})),
    "languages": _arr(_obj({"language": _s, "level": _s})),
    "certifications": _arr(_s),
})

SEED_SYSTEM = """You convert a CV into structured data for a job-application assistant.
Copy facts exactly as written in the CV. Never invent anything; use "" (or an empty list) for anything the CV doesn't state.
experience_summary: 120-200 words, first person, written in English, built only from the CV's content.
highlights: concise bullet points (max 5 per role) in English, keeping concrete numbers and technologies.
education: formal degrees only. certifications: courses and certificates (never repeat them under education).
projects: every personal/side project listed in the CV, description in one or two sentences.
skills: group as the CV groups them (category = the CV's group heading, items = the individual skills);
if the CV has no groups, use one group with category "".
Return JSON matching the schema, nothing else."""

PORTALS = [("linkedin.com", "LinkedIn"), ("indeed.com", "Indeed"), ("glassdoor.com", "Glassdoor"),
           ("computrabajo.com", "Computrabajo"), ("getonbrd.com", "Get on Board"), ("wellfound.com", "Wellfound")]

ROLES = ["AI Engineer", "ML Engineer", "Data Engineer"]


def pdf_text(path: Path) -> str:
    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages).strip()
    if len(text) < 200:
        raise ValueError(f"Could not read enough text from {path.name} (scanned image PDF?)")
    return text


def _c(d: dict, k: str) -> str:
    return clean(d.get(k, ""), 300) if isinstance(d, dict) else ""


def render(data: dict, cv_path: Path) -> str:
    c = data.get("contact") or {}
    out = [
        "# JobBot profile",
        "",
        "<!-- JobBot reads this file every time you press Ctrl+Alt+G, so edits apply immediately.",
        "     Lines like '- Key: value' are facts. Leave a value blank if unknown and JobBot will ask you.",
        "     JobBot only ever ADDS lines; it never changes a value you wrote without asking. -->",
        "",
        "## Contact",
        f"- First name: {_c(c, 'first_name')}",
        f"- Last name: {_c(c, 'last_name')}",
        f"- Email: {_c(c, 'email')}",
        f"- Phone: {_c(c, 'phone')}",
        f"- City: {_c(c, 'city')}",
        f"- Country: {_c(c, 'country')}",
        f"- LinkedIn: {_c(c, 'linkedin')}",
        f"- GitHub: {_c(c, 'github')}",
        f"- Website: {_c(c, 'website')}",
        "",
        "## Work authorization",
        "- Legally allowed to work here: ",
        "- Require visa sponsorship: ",
        "- Citizenship: ",
        "",
        "## Preferences",
        "- Willing to relocate: ",
        "- Work arrangement (remote / hybrid / on-site): ",
        "- Notice period: ",
        "- Earliest start date: ",
        "",
        "## Demographics",
        "- Hispanic or Latino: Yes",
        "- Gender: ",
        "- Race: ",
        "- Veteran status: ",
        "- Disability: ",
        "",
        "## Salary history",
        "<!-- Never auto-filled. JobBot asks every time and records your last answers here. -->",
        "",
        "## Roles",
        "<!-- Role shown in the panel dropdown, and the CV uploaded for it. -->",
        *[f"- {r}: {cv_path}" for r in ROLES],
        "",
        "## Portals",
        "<!-- website: name (name optional). Where you FOUND jobs. -->",
        *[f"- {host}: {name}" for host, name in PORTALS],
        "",
        "## Headline",
        clean(data.get("headline", ""), 300),
        "",
        "## Experience summary",
        clean(data.get("experience_summary", ""), 3000),
        "",
        "## Experience",
    ]
    for job in data.get("experience") or []:
        dates = " – ".join(x for x in (_c(job, "start"), _c(job, "end")) if x)
        out.append(f"### {_c(job, 'title')} — {_c(job, 'company')}" + (f" ({dates})" if dates else ""))
        out += [f"- {clean(h, 400).replace(': ', ' – ')}" for h in (job.get("highlights") or [])[:5]]
        out.append("")
    out.append("## Education")
    for e in data.get("education") or []:
        year = _c(e, "year")
        out.append(f"- {_c(e, 'degree')} — {_c(e, 'school')}".replace(": ", " – ") + (f" ({year})" if year else ""))
    out += ["", "## Projects"]
    for pr in data.get("projects") or []:
        tech = ", ".join(clean(t, 60) for t in pr.get("technologies") or [])
        out.append(f"### {_c(pr, 'name')}")
        out.append(clean(pr.get("description", ""), 800) + (f" ({tech})" if tech else ""))
        out.append("")
    out += ["## Skills"]
    for g in data.get("skills") or []:
        items = ", ".join(clean(s, 80) for s in g.get("items") or [])
        cat = _c(g, "category").replace(":", "")
        out.append(f"- {cat}: {items}" if cat else items)
    out += ["", "## Languages"]
    out += [f"- {_c(l, 'language')}: {_c(l, 'level')}" for l in data.get("languages") or []]
    out += ["", "## Certifications"]
    out += [f"- {clean(x, 300).replace(': ', ' – ')}" for x in data.get("certifications") or []]
    out += ["", "## Q&A bank",
            "<!-- Answers you've given to recurring questions ('### question' then the answer). JobBot adds to this. -->",
            ""]
    return "\n".join(out)


async def seed(cfg: Config, backend: Backend, force: bool = False) -> Path:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    target = cfg.data_dir / "profile.md"
    if target.exists() and not force:
        target = cfg.data_dir / "profile.seed.md"     # never overwrite the real profile
    text = pdf_text(cfg.cv_path)
    data = await backend.extract(SEED_SYSTEM, f"<cv>\n{text}\n</cv>", SEED_SCHEMA, "cv_profile")
    target.write_text(render(data, cfg.cv_path), encoding="utf-8")
    return target
