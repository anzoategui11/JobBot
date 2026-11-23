"""What happens on Ctrl+Alt+G, and when you answer questions in the panel.

hotkey  -> extract fields -> local rules -> LLM for the rest -> fill -> attach CV -> panel
answers -> fill your answers -> save the ticked ones to profile.md -> conflicts shown for confirmation
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page

from . import fields, filler, profile, rules
from .llm.base import Backend, LLMError
from .model import CHOICE_KINDS, FormField, JobInfo, MapRequest

MAX_ANSWER = 4000


@dataclass
class Question:
    qid: str
    field: FormField
    section: str
    key: str


@dataclass
class TabState:
    ex: fields.Extraction | None = None
    questions: dict[str, Question] = field(default_factory=dict)
    conflicts: dict[str, tuple[str, str, str]] = field(default_factory=dict)   # cid -> (section, key, value)
    filled: dict[str, str] = field(default_factory=dict)        # every value the bot wrote (Phase 7 diff)
    job: JobInfo = field(default_factory=JobInfo)
    cv_done: set[str] = field(default_factory=set)
    spec: dict = field(default_factory=dict)                    # last panel contents
    seq: int = 0


def _answer_value(f: FormField, value: str) -> str:
    if f.kind == "checkbox":
        return "true" if value.strip().casefold() in {"yes", "true", "sí", "si"} else "false"
    return value


async def show(page: Page, spec: dict) -> None:
    await page.main_frame.evaluate("s => window.__jobbotPanel && window.__jobbotPanel.render(s)", spec)


async def show_text(page: Page, text: str) -> None:
    await page.main_frame.evaluate("t => window.__jobbotPanel && window.__jobbotPanel.show(t)", text)


class Session:
    def __init__(self, profile_path: Path, cv_path: Path, backend: Backend, script: str):
        self.profile_path, self.cv_path, self.backend, self.script = profile_path, cv_path, backend, script
        self.states: dict[Page, TabState] = {}
        self.busy: set[Page] = set()

    def state(self, page: Page) -> TabState:
        if page not in self.states:
            self.states[page] = TabState()
            page.once("close", lambda _: self.states.pop(page, None))
        return self.states[page]

    def _profile(self) -> profile.Profile:
        try:
            return profile.load(self.profile_path)
        except OSError:
            return profile.parse("", self.profile_path)

    # ------------------------------------------------------------------ binding entry point
    async def handle(self, source: dict, msg: dict) -> None:
        page: Page = source["page"]
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind not in ("hotkey", "answers", "replace") or page in self.busy:
            return
        self.busy.add(page)
        try:
            if kind == "hotkey":
                await self.on_hotkey(page)
            elif kind == "answers":
                await self.on_answers(page, msg.get("answers"))
            else:
                await self.on_replace(page, str(msg.get("cid", "")))
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}", flush=True)
            try:
                await show_text(page, f"JobBot error: {type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            self.busy.discard(page)

    # ------------------------------------------------------------------ hotkey
    async def on_hotkey(self, page: Page) -> None:
        t0 = time.perf_counter()
        print(f"[hotkey] {page.url}", flush=True)
        await show_text(page, "Reading the form…")
        st = self.state(page)
        ex = await fields.extract(page, self.script)
        st.ex, st.questions, st.conflicts = ex, {}, {}
        prof = self._profile()
        plan = rules.plan(ex.fields, prof)

        fills = dict(plan.local)
        unknown: dict[str, str] = {}
        notes: list[str] = []
        llm_secs = None
        if plan.llm:
            await show_text(page, f"Filling {len(plan.llm) + len(plan.local)} fields…")
            t1 = time.perf_counter()
            try:
                res = await self.backend.map_fields(
                    MapRequest(plan.llm, prof.text, ex.job_text, ex.url, ex.title))
                fills.update(res.fills)
                unknown = res.unknown
                notes += [f"Rejected AI answer: {d}" for d in res.dropped]
                st.job = JobInfo(res.job.company or st.job.company, res.job.position or st.job.position,
                                 res.job.location or st.job.location, res.job.language)
            except LLMError as e:
                notes.append(f"AI unavailable, nothing guessed: {e}")
                unknown = {f.id: f.label for f in plan.llm}
            llm_secs = time.perf_counter() - t1
            for f in plan.llm:                     # whatever the model skipped is asked, never dropped
                if f.id not in fills and f.id not in unknown:
                    unknown[f.id] = f.label

        by_id = {f.id: f for f in ex.fields}
        status = await filler.apply(ex, fills)
        ok = 0
        manual: list[str] = []
        questions: list[tuple[FormField, str, str]] = []     # (field, suggestion, hint)
        for fid, s in status.items():
            f, v = by_id[fid], fills[fid]
            if s == "ok":
                ok += 1
                st.filled[fid] = v
            elif s == "typed":
                manual.append(f"Pick “{v}” from the suggestions under: {f.label}")
                st.filled[fid] = v
            elif s == "manual":
                manual.append(f"Choose “{v}” in: {f.label}")
            else:
                questions.append((f, v, f"Couldn't fill it ({s}); check the value."))

        cv_note = ""
        for f in plan.cv:
            if f.id in st.cv_done:
                continue
            s = await filler.attach_cv(ex, f, self.cv_path)
            if s == "ok":
                st.cv_done.add(f.id)
                cv_note = f" · CV attached ({self.cv_path.name})"
            elif s == "already attached":
                cv_note = " · CV already on the page"
            else:
                notes.append(f"CV upload: {s}")
                manual.append(f"Attach your CV yourself: {f.label or 'file upload'}")

        for fid, q in unknown.items():
            f = by_id[fid]
            questions.append((f, "", q if rules.norm_label(q) != rules.norm_label(f.label) else ""))
        for f in plan.salary:
            questions.append((f, rules.last_salary(prof, f.label), "Salary is never filled automatically."))
        for f in plan.free_text:
            questions.append((f, "", "AI drafts for open questions arrive in Stage 6."))
        for f in plan.consent:
            manual.append(f"{'Tick' if f.kind == 'checkbox' else 'Answer'} yourself: {f.label}")

        secs = time.perf_counter() - t0
        timing = f" in {secs:.1f} s" + (f" (AI {llm_secs:.1f} s)" if llm_secs is not None else "")
        head = f"Filled {ok} field{'s' if ok != 1 else ''}{timing}{cv_note}." if ok or cv_note else \
            ("Nothing to fill on this page." if not questions else f"Checked the form{timing}.")
        if "dialog" in ex.headings:
            head += f"\nForm: {ex.headings['dialog']}"
        head += "\nReview everything, then submit yourself."
        st.spec = {"status": head, "questions": self._questions(st, questions, prof), "manual": manual,
                   "notes": notes}
        print(head.replace("\n", " | "), flush=True)
        await show(page, st.spec)

    def _questions(self, st: TabState, items: list[tuple[FormField, str, str]], prof: profile.Profile) -> list[dict]:
        out = []
        for f, suggestion, hint in items:
            st.seq += 1
            qid = f"q{st.seq}"
            section, key = rules.save_target(f, f.label, prof)
            st.questions[qid] = Question(qid, f, section, key)
            options = f.options if f.kind in CHOICE_KINDS else (["Yes", "No"] if f.kind == "checkbox" else [])
            out.append({"qid": qid, "label": f.label or "(no label)", "kind": f.kind, "options": options,
                        "value": suggestion, "hint": hint, "save": True})
        return out

    # ------------------------------------------------------------------ panel answers
    async def on_answers(self, page: Page, answers) -> None:
        st = self.state(page)
        if st.ex is None or not isinstance(answers, list):
            return
        fills: dict[str, str] = {}
        saves: list[tuple[Question, str]] = []
        answered: set[str] = set()
        for a in answers[:100]:
            if not isinstance(a, dict):
                continue
            q = st.questions.get(str(a.get("qid", "")))
            value = str(a.get("value", "")).strip()[:MAX_ANSWER]
            if q is None or not value:
                continue
            if q.field.kind in CHOICE_KINDS and value not in q.field.options:
                continue
            fills[q.field.id] = _answer_value(q.field, value)
            answered.add(q.qid)
            if a.get("save"):
                saves.append((q, value))

        status = await filler.apply(st.ex, fills)
        filled = sum(1 for s in status.values() if s in ("ok", "typed"))
        st.filled.update({fid: v for fid, v in fills.items() if status.get(fid) in ("ok", "typed")})

        saved, conflicts = 0, []
        for q, value in saves:
            if q.section == profile.QA_SECTION:
                r = profile.add_qa(self.profile_path, q.key, value)
            elif q.section == rules.SALARY_SECTION:
                profile.replace(self.profile_path, q.section, q.key, value)   # it's a "last answer" log
                r = "added"
            else:
                r = profile.append(self.profile_path, q.section, q.key, value)
            if isinstance(r, profile.Conflict):
                st.seq += 1
                cid = f"c{st.seq}"
                st.conflicts[cid] = (q.section, r.key, value)
                conflicts.append({"cid": cid, "text": f"profile.md says “{r.existing[:200]}” for “{r.key}”; "
                                                      f"you typed “{value[:200]}”. Update profile.md?"})
            elif r in ("added", "filled"):
                saved += 1

        remaining = [q for q in st.spec.get("questions", []) if q["qid"] not in answered]
        failed = [st.questions[qid].field.label for qid in answered
                  if status.get(st.questions[qid].field.id) not in ("ok", "typed")]
        head = f"Filled {filled} answer{'s' if filled != 1 else ''}" + \
            (f", saved {saved} to profile.md" if saved else "") + "."
        if failed:
            head += "\nCouldn't fill: " + "; ".join(failed)
        head += "\nReview everything, then submit yourself."
        st.spec = {**st.spec, "status": head, "questions": remaining, "conflicts": conflicts}
        await show(page, st.spec)

    async def on_replace(self, page: Page, cid: str) -> None:
        st = self.state(page)
        if cid not in st.conflicts:
            return
        section, key, value = st.conflicts.pop(cid)
        if section == profile.QA_SECTION:
            profile.replace_qa(self.profile_path, key, value)
        else:
            profile.replace(self.profile_path, section, key, value)
        st.spec = {**st.spec, "status": f"Updated “{key}” in profile.md.",
                   "conflicts": [c for c in st.spec.get("conflicts", []) if c["cid"] != cid]}
        await show(page, st.spec)
