"""Local rules that run before/after the LLM: what the bot may fill, what it must ask, what it fills itself.

Deterministic and cheap: obvious contact fields and saved Q&A answers never cost an LLM call, and the
hard rules (salary, consent, passwords, never overwrite what's on the page) can't be talked around by a
model or by text on the page.
"""

import re
from dataclasses import dataclass, field

from . import profile as prof_mod
from .model import CHOICE_KINDS, FormField
from .profile import Profile

SALARY = re.compile(
    r"salar|compensation|\bpay\b|\bpaid\b|expected (rate|income)|hourly rate|desired rate|rate expectation|"
    r"\bctc\b|remunera|sueldo|pretensi[oó]n|\brenta\b|honorarios", re.I)
CONSENT = re.compile(
    r"\b(agree|consent|acknowledge|certify|accept|confirm)\b|terms|privacy|policy|gdpr|"
    r"acepto|consiento|t[ée]rminos|privacidad|declaro|confirmo|opt.?out|opt.?in", re.I)
FREE_TEXT_START = re.compile(r"^\s*(describe|explain|tell us|why|what (makes|motivates|excites|interests)|"
                             r"how would you|cover letter|describ|cu[ée]ntanos|por qu[ée]|explica)", re.I)
CV_LABEL = re.compile(r"resum|r[ée]sum[ée]|\bcv\b|curr[ií]cul", re.I)
COVER = re.compile(r"cover|carta|motivation", re.I)

# Sections a label may be looked up in (Roles/Portals/Salary are bot config, not answers).
ANSWER_SECTIONS = ("Contact", "Work authorization", "Preferences", "Demographics", "Other facts")
OTHER_FACTS = "Other facts"
SALARY_SECTION = "Salary history"

# normalised label / autocomplete token -> profile key ("__full__" = first + last)
SYNONYMS = {
    "full name": "__full__", "name": "__full__", "your name": "__full__", "nombre completo": "__full__",
    "first name": "First name", "given name": "First name", "nombre": "First name", "nombres": "First name",
    "last name": "Last name", "surname": "Last name", "family name": "Last name", "apellido": "Last name",
    "apellidos": "Last name",
    "email": "Email", "email address": "Email", "e mail": "Email", "correo": "Email",
    "username": "Email", "user name": "Email", "email or username": "Email", "username or email": "Email",
    "email address or username": "Email", "usuario": "Email", "correo o usuario": "Email",
    "sign in email": "Email", "login": "Email",
    "correo electronico": "Email", "correo electrónico": "Email",
    "phone": "Phone", "phone number": "Phone", "mobile": "Phone", "mobile phone": "Phone",
    "mobile phone number": "Phone", "telefono": "Phone", "teléfono": "Phone", "celular": "Phone",
    "province": "Province", "state": "Province", "provincia": "Province", "state province": "Province",
    "state / province": "Province", "state or province": "Province", "province / state": "Province",
    "province or state": "Province", "estado / provincia": "Province", "region": "Province",
    "city": "City", "ciudad": "City", "country": "Country", "pais": "Country", "país": "Country",
    "linkedin": "LinkedIn", "linkedin profile": "LinkedIn", "linkedin url": "LinkedIn",
    "linkedin profile url": "LinkedIn", "github": "GitHub", "github url": "GitHub", "github profile": "GitHub",
    "website": "Website", "portfolio": "Website", "personal website": "Website", "portfolio url": "Website",
}
AUTOCOMPLETE = {"given-name": "First name", "family-name": "Last name", "name": "__full__", "email": "Email",
                "tel": "Phone", "tel-national": "Phone", "address-level2": "City", "address-level1": "Province", "country-name": "Country",
                "country": "Country"}
URL_KEYS = {"LinkedIn", "GitHub", "Website"}


ENGLISH = re.compile(r"\b(english|ingl[eé]s)\b", re.I)
# English proficiency options, best first; the first pattern that matches an option gives its rank.
LEVELS = [
    (6, r"biling"),                                 # "Native or bilingual" counts: you are bilingual
    (-1, r"nativ|mother tongue|lengua materna"),   # Native alone is never chosen: you're not a native speaker
    (5, r"\bc2\b|expert|fluent|fluid|full professional|dominio total"),
    (3, r"upper.?intermediate|intermedio.?alto|\bb2\b"),
    (4, r"advanced|avanzad|proficien|\bc1\b|professional working"),
    (2, r"intermedi|\bb1\b|conversational|conversacional|limited working"),
    (1, r"basic|b[aá]sic|elementa|beginner|principiante|\ba[12]\b"),
    (0, r"^\s*(none|ninguno|no)\s*$"),
]


def english_level(f: FormField) -> str | None:
    """For an English-level dropdown/radio, the highest level offered; plain "Native" is skipped (your standing instruction)."""
    if f.kind not in CHOICE_KINDS or not ENGLISH.search(f.label):
        return None
    ranked = []
    for i, opt in enumerate(f.options):
        rank = next((r for r, pat in LEVELS if re.search(pat, opt, re.I)), None)
        if rank is not None:
            ranked.append((rank, i, opt))
    usable = [r for r in ranked if r[0] >= 0]
    return max(usable)[2] if len(ranked) >= 2 and usable else None


def norm_label(s: str) -> str:
    s = re.sub(r"[*:?¿()\[\]]", " ", s.casefold()).replace("-", " ")
    return " ".join(s.split())


def is_salary(f: FormField) -> bool:
    return bool(SALARY.search(f.label))


def is_consent(f: FormField) -> bool:
    if f.kind in ("checkbox", "radio"):
        return bool(CONSENT.search(f.label))
    # Dropdowns whose question is about opting in/out or consenting are yours to answer too.
    return f.kind in ("select", "combobox") and bool(re.search(r"opt.?out|opt.?in|\bconsent", f.label, re.I))


def is_free_text(f: FormField) -> bool:
    return f.kind == "textarea" or (f.kind == "text" and bool(FREE_TEXT_START.search(f.label)))


def is_cv_input(f: FormField, all_fields: list[FormField]) -> bool:
    if f.kind != "file" or COVER.search(f.label) or COVER.search(f.hint):
        return False
    if CV_LABEL.search(f.label) or CV_LABEL.search(f.hint.replace("_", " ").replace("-", " ")):
        return True
    files = [x for x in all_fields if x.kind == "file"]
    # A lone, unlabelled-ish upload that takes PDFs is the CV slot; with several, only the labelled one is.
    return len(files) == 1 and (not f.accept or "pdf" in f.accept.lower())


def has_value(f: FormField) -> bool:
    """Something is already there (prefilled by the site or typed by you): never overwrite it."""
    return bool(f.current_value) and not (f.kind == "checkbox" and f.current_value == "false")


def _profile_value(p: Profile, key: str) -> str | None:
    if key == "__full__":
        first, last = _profile_value(p, "First name"), _profile_value(p, "Last name")
        return f"{first} {last}" if first and last else None
    for sec in ANSWER_SECTIONS:
        if v := p.get(sec, key):
            if key in URL_KEYS and not re.match(r"https?://", v, re.I):
                v = "https://" + v
            return v
    return None


def _lookup(f: FormField, p: Profile, qa: dict[str, str]) -> str | None:
    label = norm_label(f.label)
    key = AUTOCOMPLETE.get(f.autocomplete.split()[-1] if f.autocomplete else "") or SYNONYMS.get(label)
    if key:
        return _profile_value(p, key)
    for sec in ANSWER_SECTIONS:                       # label equal to a profile key
        for k, v in p.items(sec):
            if v and norm_label(k) == label:
                return v
    if f.kind in ("text", "textarea", "combobox") and label in qa:
        return qa[label]
    return None


def _fit(f: FormField, value: str) -> str | None:
    """Value adapted to the field kind, or None if it doesn't fit (e.g. not one of the options)."""
    if f.kind in CHOICE_KINDS:
        for opt in f.options:
            if norm_label(opt) == norm_label(value):
                return opt
        return None
    if f.kind == "checkbox":
        return None                                   # yes/no meaning needs context: leave to the LLM
    return value


@dataclass
class Plan:
    local: dict[str, str] = field(default_factory=dict)          # id -> value, filled without the LLM
    llm: list[FormField] = field(default_factory=list)            # sent to map_fields
    salary: list[FormField] = field(default_factory=list)         # always asked
    consent: list[FormField] = field(default_factory=list)        # you tick these yourself
    free_text: list[FormField] = field(default_factory=list)      # drafts come in Stage 6; asked for now
    cv: list[FormField] = field(default_factory=list)             # upload the CV here
    skipped: list[FormField] = field(default_factory=list)        # already has a value / other file
    passwords: list[FormField] = field(default_factory=list)      # empty password boxes: filled locally from the vault


CONFIRM_PW = re.compile(r"confirm|verify|repeat|re.?enter|again|confirmar|repet|verific", re.I)


def page_kind(fields: list[FormField]) -> str:
    """'signup' (create a password), 'login' (enter it), or '' (no password on this page). Local, no AI."""
    pw = [f for f in fields if f.kind == "password"]
    if not pw:
        return ""
    if len(pw) >= 2 or any("new-password" in f.autocomplete for f in pw) or any(CONFIRM_PW.search(f.label) for f in pw):
        return "signup"
    return "login"


def plan(fields: list[FormField], p: Profile) -> Plan:
    qa = {norm_label(q): a for q, a in p.qa().items() if a}
    out = Plan()
    for f in fields:
        if f.kind == "file":
            (out.cv if is_cv_input(f, fields) and not f.current_value else out.skipped).append(f)
        elif f.kind == "password":
            (out.skipped if f.current_value else out.passwords).append(f)
        elif has_value(f):
            out.skipped.append(f)
        elif is_salary(f):
            out.salary.append(f)
        elif is_consent(f):
            out.consent.append(f)
        elif (v := english_level(f)) is not None:
            out.local[f.id] = v
        elif (v := _lookup(f, p, qa)) is not None and (v := _fit(f, v)) is not None:
            out.local[f.id] = v
        elif is_free_text(f):
            out.free_text.append(f)
        else:
            out.llm.append(f)
    return out


_ES = {"de", "la", "que", "el", "los", "las", "por", "para", "con", "una", "un", "tu", "su", "es", "cual", "cuál",
       "qué", "como", "cómo", "experiencia", "trabajo", "nos", "describe", "cuéntanos", "años", "puesto", "empresa"}
_EN = {"the", "and", "to", "of", "you", "your", "with", "for", "is", "are", "what", "why", "how", "do", "have",
       "experience", "work", "us", "about", "describe", "tell", "years", "role", "company", "would"}


def detect_lang(text: str) -> str:
    """'es', 'en' or '' (can't tell). Good enough to pick the answer language of a question or posting."""
    words = re.findall(r"[a-záéíóúñü]+", text.casefold())
    es = sum(w in _ES for w in words) + 2 * len(re.findall(r"[¿ñ]", text))
    en = sum(w in _EN for w in words)
    return "es" if es > en else "en" if en > es else ""


def draft_words(label: str) -> int:
    if re.search(r"cover letter|carta de presentaci", label, re.I):
        return 250
    if re.search(r"brief|short|one sentence|in a few words|breve|corto", label, re.I):
        return 60
    return 150


DRAFT_HIDE = {"contact", "demographics", "salary history", "roles", "portals"}


def profile_for_drafts(text: str) -> str:
    """profile.md without the sections a draft never needs (contact details, demographics, salary, bot config)."""
    out, skip = [], False
    for line in text.splitlines():
        if m := re.match(r"^##\s+(.+?)\s*#*\s*$", line):
            skip = " ".join(m.group(1).split()).casefold() in DRAFT_HIDE
        if not skip:
            out.append(line)
    return "\n".join(out)


def last_salary(p: Profile, label: str) -> str:
    """Suggestion for a salary question: your answer to the same question, else your most recent one."""
    items = p.items(SALARY_SECTION)
    for k, v in items:
        if norm_label(k) == norm_label(label):
            return v
    return items[-1][1] if items else ""


def save_target(f: FormField, question: str, p: Profile | None = None) -> tuple[str, str]:
    """(section, key) where an answer you typed in the panel is stored in profile.md.
    A label that already exists as a key keeps its section, so a different answer shows up as a conflict."""
    if is_salary(f):
        return SALARY_SECTION, prof_mod._one_line(f.label)[:120]
    label = prof_mod._one_line(f.label or question).rstrip(" *:")
    if p is not None:
        for sec in ANSWER_SECTIONS:
            for k, _ in p.items(sec):
                if norm_label(k) == norm_label(label):
                    return sec, k
    if is_free_text(f) or len(label) > 40 or label.endswith("?"):
        return prof_mod.QA_SECTION, label[:200]
    return OTHER_FACTS, label
