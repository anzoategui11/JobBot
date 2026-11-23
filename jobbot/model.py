"""Shared data types passed between extraction, the LLM backend and the filler."""

from dataclasses import dataclass, field

# Kinds the extractor may report. "password" and "file" are handled locally and never sent to the LLM.
CHOICE_KINDS = {"select", "radio"}
LOCAL_ONLY_KINDS = {"password", "file"}


@dataclass
class FormField:
    id: str                     # data-jobbot-id assigned in the page
    kind: str                   # text|email|tel|number|date|select|radio|checkbox|textarea|file|password|combobox
    label: str
    options: list[str] = field(default_factory=list)
    required: bool = False
    current_value: str = ""
    autocomplete: str = ""      # the page's autocomplete hint (e.g. "given-name"), a strong signal
    accept: str = ""            # file inputs only: accepted types, used to spot the CV upload


@dataclass
class JobInfo:
    company: str = ""
    position: str = ""
    location: str = ""
    language: str = "en"        # en | es | other


@dataclass
class MapRequest:
    fields: list[FormField]
    profile_md: str
    job_text: str
    page_url: str = ""
    page_title: str = ""


@dataclass
class MapResult:
    job: JobInfo
    fills: dict[str, str]                 # field id -> value
    unknown: dict[str, str]               # field id -> question to ask the user
    dropped: list[str] = field(default_factory=list)   # why LLM output was rejected (for the panel/log)


@dataclass
class DraftRequest:
    question: str
    job_text: str
    profile_md: str
    role: str = ""
    language: str = "en"
    max_words: int = 180


@dataclass
class DraftQuestion:
    id: str                     # field id on the page
    question: str
    language: str = "en"        # en | es | other (= the question's own language)
    max_words: int = 150


@dataclass
class DraftBatch:
    """Several open questions answered in one request (the profile is sent once: free-tier token budget)."""
    questions: list[DraftQuestion]
    job_text: str
    profile_md: str
    role: str = ""
