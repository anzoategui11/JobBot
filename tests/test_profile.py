import asyncio
from pathlib import Path

from jobbot import profile
from jobbot.config import load as load_cfg
from jobbot.profile import Conflict
from jobbot.seed import SEED_SCHEMA, render, seed

SAMPLE = """# JobBot profile

## Contact
- First name: Jane
-   Last name :   Doe
* Email: jane@example.com
- Phone:
- LinkedIn: https://linkedin.com/in/jane

## Experience summary
I build things: mostly Python.
- not a fact: just prose that looks like one

## Q&A bank

### Why do you want to work here?
Because the mission matters.
Second line.

### Years of Python?
5
"""


def _write(tmp_path: Path, text: str = SAMPLE, newline: str = "\n") -> Path:
    p = tmp_path / "profile.md"
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return p


def test_parse_tolerates_odd_whitespace_and_bullets(tmp_path):
    prof = profile.load(_write(tmp_path))
    assert prof.get("contact", "first name") == "Jane"
    assert prof.get("Contact", "Last name") == "Doe"
    assert prof.get("Contact", "Email") == "jane@example.com"
    assert prof.get("Contact", "Phone") is None                    # blank counts as missing
    assert prof.get("Contact", "LinkedIn") == "https://linkedin.com/in/jane"   # colons in value kept
    assert prof.find("email") == "jane@example.com"
    assert prof.qa() == {"Why do you want to work here?": "Because the mission matters.\nSecond line.",
                         "Years of Python?": "5"}


def test_append_new_same_blank_and_conflict(tmp_path):
    p = _write(tmp_path)
    assert profile.append(p, "Contact", "City", "Austin") == "added"
    assert profile.append(p, "contact", "city", "  austin ") == "same"
    assert profile.append(p, "Contact", "Phone", "+1 555") == "filled"
    c = profile.append(p, "Contact", "First name", "Janet")
    assert isinstance(c, Conflict) and c.existing == "Jane" and c.proposed == "Janet"
    prof = profile.load(p)
    assert prof.get("Contact", "First name") == "Jane"            # conflict never overwrites
    assert prof.get("Contact", "City") == "Austin" and prof.get("Contact", "Phone") == "+1 555"
    text = p.read_text(encoding="utf-8")
    # City inserted right after the last Contact fact, before the next section
    assert text.index("- City: Austin") < text.index("## Experience summary")
    assert "I build things: mostly Python." in text                 # prose untouched


def test_append_creates_missing_section_and_replace(tmp_path):
    p = _write(tmp_path)
    assert profile.append(p, "Preferences", "Notice period", "2 weeks") == "added"
    assert profile.load(p).get("Preferences", "Notice period") == "2 weeks"
    profile.replace(p, "Contact", "First name", "Janet")
    assert profile.load(p).get("Contact", "First name") == "Janet"


def test_values_are_single_line(tmp_path):
    p = _write(tmp_path)
    profile.append(p, "Contact", "City", "Austin\n## Hacked section\n- x: y")
    prof = profile.load(p)
    assert prof.section("Hacked section") is None
    assert prof.get("Contact", "City") == "Austin ## Hacked section - x: y"


def test_qa_add_same_and_conflict(tmp_path):
    p = _write(tmp_path)
    assert profile.add_qa(p, "Do you have a driver's license?", "Yes") == "added"
    assert profile.add_qa(p, "Years of Python?", "5") == "same"
    assert isinstance(profile.add_qa(p, "years of python?", "6"), Conflict)
    qa = profile.load(p).qa()
    assert qa["Do you have a driver's license?"] == "Yes"
    assert qa["Why do you want to work here?"].startswith("Because")


def test_crlf_preserved(tmp_path):
    p = _write(tmp_path, newline="\r\n")
    profile.append(p, "Contact", "City", "Austin")
    raw = p.read_bytes()
    assert b"- City: Austin\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_append_to_missing_file_creates_it(tmp_path):
    p = tmp_path / "profile.md"
    assert profile.append(p, "Contact", "Email", "a@b.c") == "added"
    assert profile.load(p).get("Contact", "Email") == "a@b.c"


# ------------------------------------------------------------ seeding

FAKE = {
    "contact": {"first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "phone": "",
                "city": "Austin", "country": "USA", "linkedin": "", "github": "github.com/jd", "website": ""},
    "headline": "AI Engineer",
    "experience_summary": "I build RAG systems.",
    "experience": [{"title": "AI Engineer", "company": "Acme", "location": "Remote", "start": "2023",
                    "end": "Present", "highlights": ["Cut latency: 40%", "Shipped agents"]}],
    "education": [{"degree": "BSc CS", "school": "UT", "year": "2020"}],
    "projects": [{"name": "Kalshi monitor", "description": "Alerts on price moves.", "technologies": ["Airflow"]}],
    "skills": [{"category": "Programming", "items": ["Python", "SQL"]}],
    "languages": [{"language": "Spanish", "level": "Native"}],
    "certifications": [],
}


def test_render_parses_back(tmp_path):
    md = render(FAKE, Path(r"C:\cv\AI.pdf"))
    prof = profile.parse(md)
    assert prof.get("Contact", "First name") == "Jane"
    assert prof.get("Contact", "Phone") is None
    assert prof.get("Demographics", "Hispanic or Latino") == "Yes"
    assert prof.get("Roles", "AI Engineer") == r"C:\cv\AI.pdf"
    assert prof.get("Portals", "linkedin.com") == "LinkedIn"
    assert prof.get("Languages", "Spanish") == "Native"
    assert prof.section("Experience").entries == {}                # ': ' in bullets neutralised
    assert prof.section("Q&A bank") is not None
    assert prof.get("Skills", "Programming") == "Python, SQL"
    assert "### Kalshi monitor" in md and "Alerts on price moves. (Airflow)" in md


def _all_strict(schema: dict) -> bool:
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False or set(schema["required"]) != set(schema["properties"]):
            return False
        return all(_all_strict(s) for s in schema["properties"].values())
    if schema.get("type") == "array":
        return _all_strict(schema["items"])
    return True


def test_seed_schema_is_groq_strict_compatible():
    assert _all_strict(SEED_SCHEMA)


def test_seed_never_overwrites_existing_profile(tmp_path, monkeypatch):
    import dataclasses
    cfg = dataclasses.replace(load_cfg(), data_dir=tmp_path)
    (tmp_path / "profile.md").write_text("mine", encoding="utf-8")
    monkeypatch.setattr("jobbot.seed.pdf_text", lambda p: "cv text " * 50)

    class FakeBackend:
        async def extract(self, *a, **k):
            return FAKE

    out = asyncio.run(seed(cfg, FakeBackend()))
    assert out.name == "profile.seed.md"
    assert (tmp_path / "profile.md").read_text(encoding="utf-8") == "mine"


def test_portals_accepts_bare_host():
    prof = profile.parse("## Portals\n<!-- c -->\n- linkedin.com: LinkedIn\n- https://www.ProsMarketplace.com/\n")
    assert prof.portals() == {"linkedin.com": "LinkedIn", "prosmarketplace.com": "prosmarketplace.com"}
