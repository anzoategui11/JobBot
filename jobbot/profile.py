"""profile.md: human-first Markdown that JobBot reads on every hotkey and appends to.

Format
------
    ## Section name
    - Key: value                ("key: value" lines; keys match case-insensitively)
    free text / ### sub-headings are kept as-is (e.g. Experience summary)

    ## Q&A bank
    ### Question text
    Answer text (one or more lines)

Rules (SPEC): re-read on every use; only append new facts; never overwrite a value the user
has, report a Conflict instead; replace only when the user explicitly confirms.
Edits are line-based (the rest of the file is untouched) and written atomically.
"""

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

QA_SECTION = "Q&A bank"
_KV = re.compile(r"^\s*[-*]\s+([^:]+?)\s*:\s*(.*?)\s*$")
_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_H3 = re.compile(r"^###\s+(.+?)\s*#*\s*$")


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _one_line(s: str) -> str:
    return " ".join(str(s).split())


@dataclass
class Section:
    name: str
    start: int                      # index of the "## " line
    end: int                        # exclusive index of the section's last line
    entries: dict[str, tuple[str, str, int]] = field(default_factory=dict)  # norm key -> (key, value, line)


@dataclass
class Profile:
    path: Path
    text: str
    sections: dict[str, Section]    # norm name -> Section

    def section(self, name: str) -> Section | None:
        return self.sections.get(_norm(name))

    def get(self, section: str, key: str) -> str | None:
        """Value for key in section, or None if missing/blank."""
        s = self.section(section)
        if s and (e := s.entries.get(_norm(key))) and e[1]:
            return e[1]
        return None

    def find(self, key: str) -> str | None:
        """Value for key in any section (first non-blank match)."""
        for s in self.sections.values():
            if (e := s.entries.get(_norm(key))) and e[1]:
                return e[1]
        return None

    def items(self, section: str) -> list[tuple[str, str]]:
        s = self.section(section)
        return [(k, v) for k, v, _ in s.entries.values()] if s else []

    def portals(self) -> dict[str, str]:
        """## Portals as {host: name}. Accepts '- host: Name' and a bare '- host' (name = host)."""
        s = self.section("Portals")
        if not s:
            return {}
        out = {}
        for line in self.text.splitlines()[s.start + 1:s.end]:
            m = re.match(r"^\s*[-*]\s+(?:https?://)?(?:www\.)?([^\s:]+?)/?\s*(?::\s*(.*?))?\s*$", line, re.I)
            if m:
                host = m.group(1).lower()
                out[host] = (m.group(2) or "").strip() or host
        return out

    def qa(self) -> dict[str, str]:
        """Q&A bank as {question: answer}."""
        s = self.section(QA_SECTION)
        if not s:
            return {}
        lines = self.text.splitlines()[s.start + 1:s.end]
        out, q, buf = {}, None, []
        for line in lines + ["### "]:
            m = _H3.match(line)
            if m or line == "### ":
                if q is not None:
                    out[q] = "\n".join(buf).strip()
                q, buf = (m.group(1) if m else None), []
            elif q is not None:
                buf.append(line)
        return out


def parse(text: str, path: Path = Path("profile.md")) -> Profile:
    lines = text.splitlines()
    sections: dict[str, Section] = {}
    cur: Section | None = None
    for i, line in enumerate(lines):
        if m := _H2.match(line):
            if cur:
                cur.end = i
            cur = Section(m.group(1), i, len(lines))
            sections.setdefault(_norm(cur.name), cur)
        elif cur is not None and (m := _KV.match(line)) and _norm(cur.name) != _norm(QA_SECTION):
            key = m.group(1).strip()
            cur.entries.setdefault(_norm(key), (key, m.group(2), i))
    return Profile(path, text, sections)


def load(path: Path) -> Profile:
    return parse(path.read_text(encoding="utf-8-sig"), path)


# ---------------------------------------------------------------- writing

@dataclass
class Conflict:
    section: str
    key: str
    existing: str
    proposed: str


def _newline(raw: str) -> str:
    return "\r\n" if "\r\n" in raw else "\n"


def _write_atomic(path: Path, text: str, attempts: int = 5) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    for i in range(attempts):            # OneDrive / editors can hold the file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                os.unlink(tmp)
                raise
            time.sleep(0.2 * (i + 1))


def _read(path: Path) -> tuple[str, str, list[str]]:
    raw = ""
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:   # newline="" keeps \r\n visible
            raw = f.read()
    return raw, _newline(raw), raw.splitlines()


def _join(lines: list[str], nl: str) -> str:
    return nl.join(lines).rstrip() + nl


def _insert_line(lines: list[str], prof: Profile, section: str, new_line: str) -> list[str]:
    s = prof.section(section)
    if s is None:
        while lines and not lines[-1].strip():
            lines.pop()
        return lines + ["", f"## {section}", new_line]
    last_entry = max((ln for _, _, ln in s.entries.values()), default=None)
    at = (last_entry + 1) if last_entry is not None else s.start + 1
    if last_entry is None:                       # skip blank lines right after the heading
        while at < s.end and not lines[at].strip():
            at += 1
    return lines[:at] + [new_line] + lines[at:]


def append(path: Path, section: str, key: str, value: str) -> str | Conflict:
    """Add `- key: value` under section. Returns "added", "same", "filled" (blank value set) or Conflict."""
    value, key = _one_line(value), _one_line(key)
    raw, nl, lines = _read(path)
    prof = parse(raw, path)
    s = prof.section(section)
    if s and (e := s.entries.get(_norm(key))):
        k, existing, ln = e
        if _norm(existing) == _norm(value):
            return "same"
        if existing:
            return Conflict(s.name, k, existing, value)
        lines[ln] = f"- {k}: {value}"          # key was present but blank: fill it in
        _write_atomic(path, _join(lines, nl))
        return "filled"
    _write_atomic(path, _join(_insert_line(lines, prof, section, f"- {key}: {value}"), nl))
    return "added"


def replace(path: Path, section: str, key: str, value: str) -> None:
    """Overwrite a value. Only call after the user explicitly confirmed (a Conflict prompt)."""
    raw, nl, lines = _read(path)
    prof = parse(raw, path)
    s = prof.section(section)
    if not s or _norm(key) not in s.entries:
        append(path, section, key, value)
        return
    k, _, ln = s.entries[_norm(key)]
    lines[ln] = f"- {k}: {_one_line(value)}"
    _write_atomic(path, _join(lines, nl))


def replace_qa(path: Path, question: str, answer: str) -> None:
    """Overwrite a Q&A answer. Only call after the user explicitly confirmed (a Conflict prompt)."""
    raw, nl, lines = _read(path)
    prof = parse(raw, path)
    s = prof.section(QA_SECTION)
    if s:
        for i in range(s.start + 1, s.end):
            if (m := _H3.match(lines[i])) and _norm(m.group(1)) == _norm(question):
                end = i + 1
                while end < s.end and not _H3.match(lines[end]) and not _H2.match(lines[end]):
                    end += 1
                block = answer.strip().splitlines() + [""]
                lines = lines[:i + 1] + block + lines[end:]
                _write_atomic(path, _join(lines, nl))
                return
    add_qa(path, question, answer)


def add_qa(path: Path, question: str, answer: str) -> str | Conflict:
    question, answer = _one_line(question), answer.strip()
    raw, nl, lines = _read(path)
    prof = parse(raw, path)
    for q, a in prof.qa().items():
        if _norm(q) == _norm(question):
            return "same" if _norm(a) == _norm(answer) else Conflict(QA_SECTION, q, a, answer)
    block = [f"### {question}", *answer.splitlines(), ""]
    s = prof.section(QA_SECTION)
    if s is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", f"## {QA_SECTION}", ""] + block
    else:
        at = s.end
        while at > s.start + 1 and not lines[at - 1].strip():
            at -= 1
        lines = lines[:at] + ([""] if lines[at - 1].strip() else []) + block + lines[at:]
    _write_atomic(path, _join(lines, nl))
    return "added"
