"""Local rules: what is filled without the LLM, what is always asked, what is never touched."""

from pathlib import Path

from jobbot import profile, rules
from jobbot.model import FormField

PROF = profile.parse("""## Contact
- First name: Jane
- Last name: Doe
- Email: jane@example.com
- Country: Argentina
- GitHub: github.com/jane

## Demographics
- Gender: Female

## Roles
- AI Engineer: C:\\cv.pdf

## Q&A bank
### Why do you want to join us?
I like the mission.
""")


def F(label, kind="text", **kw):
    return FormField(id=label, kind=kind, label=label, **kw)


def test_salary_and_consent_detection():
    assert rules.is_salary(F("Expected salary (USD)")) and rules.is_salary(F("Pretensión salarial"))
    assert rules.is_salary(F("What are your compensation expectations?"))
    assert not rules.is_salary(F("Years of experience"))
    assert rules.is_consent(F("I agree to the Terms", "checkbox"))
    assert rules.is_consent(F("Acepto la política de privacidad", "checkbox"))
    assert not rules.is_consent(F("Willing to relocate?", "checkbox"))
    assert not rules.is_consent(F("I agree to the Terms", "text"))


def test_cv_input_detection():
    cv, cover = F("Resume/CV", "file"), F("Cover letter", "file")
    assert rules.is_cv_input(cv, [cv, cover]) and not rules.is_cv_input(cover, [cv, cover])
    lone = F("Attach", "file", accept=".pdf,.doc")
    assert rules.is_cv_input(lone, [lone])
    other = F("Attach another file", "file")
    assert not rules.is_cv_input(other, [F("Resume", "file"), other])


def test_plan_routes_fields():
    fields = [
        F("Full name"), F("E-mail"), F("x", autocomplete="family-name"),
        F("Country", "select", options=["Brazil", "Argentina"]),
        F("GitHub"), F("Gender", "radio", options=["Male", "Female"]),
        F("Why do you want to join us?", "textarea"), F("Tell us about a project"),
        F("Expected salary"), F("I accept the privacy policy", "checkbox"),
        F("Resume", "file"), F("Password", "password"), F("Phone", current_value="+1 555"),
        F("Do you need sponsorship?", "radio", options=["Yes", "No"]), F("AI Engineer"),
    ]
    plan = rules.plan(fields, PROF)
    assert plan.local == {"Full name": "Jane Doe", "E-mail": "jane@example.com", "x": "Doe",
                          "Country": "Argentina", "GitHub": "https://github.com/jane", "Gender": "Female",
                          "Why do you want to join us?": "I like the mission."}          # Q&A reused
    assert [f.label for f in plan.free_text] == ["Tell us about a project"]
    assert [f.label for f in plan.salary] == ["Expected salary"]
    assert [f.label for f in plan.consent] == ["I accept the privacy policy"]
    assert [f.label for f in plan.cv] == ["Resume"]
    assert {f.label for f in plan.skipped} == {"Password", "Phone"}
    assert [f.label for f in plan.llm] == ["Do you need sponsorship?", "AI Engineer"]  # Roles isn't an answer


def test_choice_not_in_options_goes_to_llm():
    plan = rules.plan([F("Country", "select", options=["Chile", "Peru"])], PROF)
    assert not plan.local and plan.llm


def test_save_targets():
    assert rules.save_target(F("Expected salary"), "")[0] == "Salary history"
    assert rules.save_target(F("Address"), "") == ("Other facts", "Address")
    assert rules.save_target(F("How did you hear about us?"), "")[0] == profile.QA_SECTION
    assert rules.save_target(F("gender"), "", PROF) == ("Demographics", "Gender")     # existing key keeps section


def test_last_salary_suggestion():
    p = profile.parse("## Salary history\n- Expected salary (USD): 5000\n- Desired monthly pay: 4000\n")
    assert rules.last_salary(p, "Expected salary (USD)") == "5000"
    assert rules.last_salary(p, "Something else") == "4000"
    assert rules.last_salary(profile.parse(""), "x") == ""


def test_bot_code_never_clicks_navigates_or_submits():
    root = Path(__file__).resolve().parent.parent / "jobbot"
    for name in ("filler.py", "flow.py", "browser.py", "fields.py"):
        src = (root / name).read_text(encoding="utf-8")
        for bad in (".click(", ".press(", ".goto(", ".submit(", "requestSubmit", ".dispatch_event("):
            assert bad not in src, f"{name} contains {bad}"
    js = (root / "inject" / "agent.js").read_text(encoding="utf-8")
    assert "submit(" not in js and "location.href =" not in js and "scrollIntoView" not in js


def test_english_level_always_highest():
    f = F("¿Cuál es tu nivel de inglés?", "select", options=["Básico", "Intermedio", "Avanzado", "Experto"])
    assert rules.english_level(f) == "Experto"
    assert rules.plan([f], PROF).local == {f.label: "Experto"}
    g = F("English proficiency", "radio", options=["Native or bilingual", "Full professional", "Elementary"])
    assert rules.english_level(g) == "Native or bilingual"                   # bilingual counts
    n = F("Nivel de inglés", "select", options=["Básico", "Avanzado", "Nativo"])
    assert rules.english_level(n) == "Avanzado"                              # plain Native never
    m = F("English level", "select", options=["Fluent", "Bilingual", "Native speaker"])
    assert rules.english_level(m) == "Bilingual"
    h = F("English level", "select", options=["A1", "A2", "B1", "B2", "C1", "C2"])
    assert rules.english_level(h) == "C2"
    i = F("English level", "select", options=["Upper intermediate", "Advanced", "Intermediate"])
    assert rules.english_level(i) == "Advanced"
    assert rules.english_level(F("Do you speak English?", "radio", options=["Yes", "No"])) is None
    assert rules.english_level(F("Nivel de alemán", "select", options=["Básico", "Experto"])) is None


def test_province_opt_out_and_resume_hint():
    prof = profile.parse("## Contact\n- Province: Capital Federal\n")
    plan = rules.plan([F("State / Province", "combobox"), F("x", autocomplete="address-level1")], prof)
    assert plan.local == {"State / Province": "Capital Federal", "x": "Capital Federal"}
    assert rules.is_consent(F("You can choose to opt out of AI screening", "combobox"))
    assert not rules.is_consent(F("Province", "combobox"))
    upload = F("Upload a file or drag and drop here", "file", hint="resume")
    cover = F("Upload a file", "file", hint="cover_letter")
    assert rules.is_cv_input(upload, [upload, cover]) and not rules.is_cv_input(cover, [upload, cover])
