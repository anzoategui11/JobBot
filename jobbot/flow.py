"""What happens on Ctrl+Alt+G, and when you answer questions in the panel.

hotkey  -> extract fields -> local rules -> LLM for the rest -> fill -> attach CV -> panel
          -> (background) AI drafts for open questions -> fill empty ones -> panel badges them
answers -> fill your answers -> save the ticked ones to profile.md -> conflicts shown for confirmation
role    -> remembered; unedited drafts are redrafted for the new role
log     -> form prefilled (portal from tab history, company/position from the posting) -> applications.csv
        -> fields you edited or typed yourself are offered for saving to profile.md
"""

import asyncio
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from playwright.async_api import Page

from . import applog, fields, filler, profile, rules
from .llm import prompts
from .provenance import Provenance
from . import state as saved_state
from . import vault
from .llm.base import Backend, LLMError
from .model import CHOICE_KINDS, DraftBatch, DraftQuestion, FormField, JobInfo, MapRequest

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
    drafts: dict[str, str] = field(default_factory=dict)        # field id -> AI draft written (not saved)
    gen: int = 0                                                # bumps per hotkey; stale draft tasks don't repaint
    task: asyncio.Task | None = None                            # running draft job
    original: dict[str, str] = field(default_factory=dict)      # field id -> value before JobBot touched the page
    cv_used: str = ""
    learn: dict[str, tuple[Question, str]] = field(default_factory=dict)   # lid -> (where to save, value)
    logged: bool = False
    known: dict[str, FormField] = field(default_factory=dict)  # every field seen in this tab (all steps)
    edits: dict[str, str] = field(default_factory=dict)        # field id -> last value reported by the page
    apply_url: str = ""                                         # where you first pressed the hotkey in this tab
    salary: str = ""                                            # salary you gave in the panel for this application


def _answer_value(f: FormField, value: str) -> str:
    if f.kind == "checkbox":
        return "true" if value.strip().casefold() in {"yes", "true", "sí", "si"} else "false"
    return value


async def show(page: Page, spec: dict) -> None:
    await fields.evaluate(page.main_frame, "s => window.__jobbotPanel && window.__jobbotPanel.render(s)", spec,
                          timeout=5)


async def show_text(page: Page, text: str) -> None:
    await fields.evaluate(page.main_frame, "t => window.__jobbotPanel && window.__jobbotPanel.show(t)", text,
                          timeout=5)


class Session:
    def __init__(self, profile_path: Path, cv_path: Path, backend: Backend, script: str,
                 state_path: Path = saved_state.DEFAULT, log_path: Path | None = None,
                 pending_path: Path = applog.PENDING, get_password=vault.get):
        self.profile_path, self.cv_path, self.backend, self.script = profile_path, cv_path, backend, script
        self.log_path = log_path or profile_path.parent / "applications.csv"
        self.pending_path = pending_path
        self.get_password = get_password          # Credential Manager lookup (swappable in tests)
        self.provenance = Provenance()
        self.state_path = state_path
        self.saved = saved_state.load(state_path)
        self.states: dict[Page, TabState] = {}
        self.busy: set[Page] = set()

    # ------------------------------------------------------------------ role (manual, remembered)
    def roles(self, prof: profile.Profile) -> list[tuple[str, str]]:
        return [(k, v) for k, v in prof.items("Roles") if k]

    def role(self, prof: profile.Profile) -> str:
        names = [r for r, _ in self.roles(prof)]
        return self.saved.get("role") if self.saved.get("role") in names else (names[0] if names else "")

    def cv_for(self, prof: profile.Profile) -> Path:
        path = dict(self.roles(prof)).get(self.role(prof), "")
        return Path(path) if path and Path(path).exists() else self.cv_path

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
        if kind == "edit":                       # frequent and tiny: record, never block on busy
            fid, value = str(msg.get("id", ""))[:40], msg.get("value")
            if isinstance(value, str):
                self.state(page).edits[fid] = value[:MAX_ANSWER]
            return
        if kind not in ("hotkey", "answers", "replace", "role", "log_form", "log", "learn"):
            return
        # Logging never waits behind a slow fill (and a stuck page can't lock you out of it).
        exclusive = kind not in ("log_form", "log", "learn")
        if exclusive and page in self.busy:
            return
        if exclusive:
            self.busy.add(page)
        try:
            if kind == "hotkey":
                await self.on_hotkey(page)
            elif kind == "answers":
                await self.on_answers(page, msg.get("answers"))
            elif kind == "role":
                await self.on_role(page, str(msg.get("role", "")))
            elif kind == "log_form":
                await self.on_log_form(page)
            elif kind == "log":
                await self.on_log(page, msg.get("row"))
            elif kind == "learn":
                await self.on_learn(page, msg.get("ids"))
            else:
                await self.on_replace(page, str(msg.get("cid", "")))
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}", flush=True)
            try:
                await show_text(page, f"JobBot error: {type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            if exclusive:
                self.busy.discard(page)

    # ------------------------------------------------------------------ hotkey
    async def on_hotkey(self, page: Page) -> None:
        t0 = time.perf_counter()
        print(f"[hotkey] {page.url}", flush=True)
        await show_text(page, "Reading the form…")
        st = self.state(page)
        ex = await fields.extract(page, self.script)
        st.ex, st.questions, st.conflicts = ex, {}, {}
        st.gen += 1
        st.apply_url = st.apply_url or page.url
        for f in ex.fields:
            st.original.setdefault(f.id, f.current_value)
            st.known[f.id] = f
        prof = self._profile()
        cv_path = self.cv_for(prof)
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
                if note := getattr(self.backend, "take_note", lambda: "")():
                    notes.append(note)
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
        choices: dict[str, list[str]] = {}
        status = await filler.apply(ex, fills, choices=choices)
        if choices:
            status.update(await self._choose(ex, choices, fills, prof))
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
            elif s == "choose" and choices.get(fid):
                questions.append((replace(f, options=choices[fid]), "", "Pick from the site's own list."))
            else:
                questions.append((f, v, f"Couldn't fill it ({s}); check the value."))

        cv_note = ""
        for f in plan.cv:
            if f.id in st.cv_done:
                continue
            s = await filler.attach_cv(ex, f, cv_path)
            if s == "ok":
                st.cv_done.add(f.id)
                st.cv_used = cv_path.name
                cv_note = f" · CV attached ({cv_path.name})"
            elif s == "already attached":
                cv_note = " · CV already on the page"
            else:
                notes.append(f"CV upload: {s}")
                manual.append(f"Attach your CV yourself: {f.label or 'file upload'}")

        pw_note = ""
        if plan.passwords:
            kind = rules.page_kind(ex.fields)
            password = self.get_password()
            if not password:
                manual.append("Password: store it once with  uv run python -m jobbot set-password  "
                              "(in the JobBot folder), then press Ctrl+Alt+G again.")
            else:
                n, flagged = await filler.fill_passwords(ex, [f.id for f in plan.passwords], password)
                del password
                if n:
                    pw_note = (" · sign-up: password + confirmation filled" if kind == "signup" and n > 1
                               else " · password filled")
                    notes.append("Brave may offer to save the password: that's fine.")
                if flagged:
                    manual.append("The site doesn't accept your password's rules: choose one yourself here.")

        for fid, q in unknown.items():
            f = by_id[fid]
            questions.append((f, "", q if rules.norm_label(q) != rules.norm_label(f.label) else ""))
        for f in plan.salary:
            questions.append((f, rules.last_salary(prof, f.label), "Salary is never filled automatically."))
        for f in plan.consent:
            manual.append(f"{'Tick' if f.kind == 'checkbox' else 'Answer'} yourself: {f.label}")

        secs = time.perf_counter() - t0
        timing = f" in {secs:.1f} s" + (f" (AI {llm_secs:.1f} s)" if llm_secs is not None else "")
        head = f"Filled {ok} field{'s' if ok != 1 else ''}{timing}{cv_note}{pw_note}." if ok or cv_note or pw_note else \
            ("Nothing to fill on this page." if not (questions or plan.free_text) else f"Checked the form{timing}.")
        if "dialog" in ex.headings:
            head += f"\nForm: {ex.headings['dialog']}"
        if plan.free_text:
            head += f"\nDrafting {len(plan.free_text)} answer{'s' if len(plan.free_text) != 1 else ''}…"
        head += "\nReview everything, then submit yourself."
        st.spec = {"status": head, "questions": self._questions(st, questions, prof), "manual": manual,
                   "notes": notes, "roles": [r for r, _ in self.roles(prof)], "role": self.role(prof),
                   "drafts": [], "can_log": not st.logged, "warning": self._already_applied(page, st)}
        print(head.replace("\n", " | "), flush=True)
        await show(page, st.spec)
        if plan.free_text:
            st.task = asyncio.create_task(self._draft(page, st, st.gen, plan.free_text, prof, ex))

    # ------------------------------------------------------------------ AI drafts (background)
    async def _draft(self, page: Page, st: TabState, gen: int, todo: list[FormField],
                     prof: profile.Profile, ex: fields.Extraction, redraft: bool = False) -> None:
        try:
            job_lang = rules.detect_lang(ex.job_text) or st.job.language
            qs = [DraftQuestion(f.id, f.label, rules.detect_lang(f.label) or job_lang or "en",
                                rules.draft_words(f.label)) for f in todo]
            t0 = time.perf_counter()
            notes, questions = [], []
            try:
                drafts = await self.backend.draft_answers(DraftBatch(
                    qs, ex.job_text, rules.profile_for_drafts(prof.text), self.role(prof)))
                if note := getattr(self.backend, "take_note", lambda: "")():
                    notes.append(note)
            except LLMError as e:
                drafts = {}
                notes.append(f"AI drafts unavailable: {e}")
            secs = time.perf_counter() - t0
            status = await filler.apply(ex, drafts, if_empty=not redraft) if drafts else {}
            written = []
            for f in todo:
                s = status.get(f.id)
                if s == "ok":
                    st.drafts[f.id] = st.filled[f.id] = drafts[f.id]
                    written.append(f.label)
                elif s == "kept":
                    notes.append(f"You'd already typed in “{f.label}”, so the draft wasn't used.")
                elif not redraft:
                    questions.append((f, drafts.get(f.id, ""), "Write your answer (no AI draft)."))
            if st.gen != gen or page.is_closed():
                return                                  # you moved on to another step: don't repaint
            status_line = (f"Drafted {len(written)} answer{'s' if len(written) != 1 else ''} in {secs:.1f} s"
                           f"{' for ' + self.role(prof) if self.role(prof) else ''}.") if written else \
                "No AI drafts written."
            old = [ln for ln in st.spec.get("status", "").split("\n") if not ln.startswith("Drafting")]
            if redraft:
                old = [ln for ln in old if not ln.startswith(("Redrafting", "Drafted"))]
            head = "\n".join([*old[:-1], status_line, old[-1]]) if old else status_line
            st.spec = {**st.spec, "status": head,
                       "drafts": sorted(set(st.spec.get("drafts", [])) | set(written)),
                       "questions": st.spec.get("questions", []) + self._questions(st, questions, prof),
                       "notes": st.spec.get("notes", []) + notes}
            print(status_line, flush=True)
            await show(page, st.spec)
        except Exception as e:                           # background task: report, never crash the session
            print(f"[draft error] {type(e).__name__}: {e}", flush=True)

    async def on_role(self, page: Page, role: str) -> None:
        st = self.state(page)
        prof = self._profile()
        if role not in [r for r, _ in self.roles(prof)]:
            return
        self.saved["role"] = role
        saved_state.save(self.saved, self.state_path)
        st.spec = {**st.spec, "role": role}
        if not (st.ex and st.drafts):
            return
        current = await filler.values(st.ex, list(st.drafts))
        norm = lambda t: " ".join((t or "").split())
        untouched = [f for f in st.ex.fields if f.id in st.drafts and norm(current.get(f.id)) == norm(st.drafts[f.id])]
        if not untouched:
            return
        lines = st.spec.get("status", "").split("\n")
        st.spec["status"] = "\n".join([f"Redrafting {len(untouched)} answer(s) for {role}…", *lines[-1:]])
        await show(page, st.spec)
        st.task = asyncio.create_task(self._draft(page, st, st.gen, untouched, prof, st.ex, redraft=True))

    def _already_applied(self, page: Page, st: TabState) -> str:
        """Panel warning when applications.csv already has this application (same link, or same company + title)."""
        try:
            hit = applog.previous(self.log_path, [page.url, st.apply_url], st.job.company, st.job.position,
                                  self.pending_path)
        except Exception as e:                       # a broken CSV must never block filling
            print(f"[already-applied check] {type(e).__name__}: {e}", flush=True)
            return ""
        if not hit:
            return ""
        how, row = hit
        what = f"{row.get('Company', '')} – {row.get('Position', '')}".strip(" –")
        when = row.get("Date", "?")
        status = row.get("Status", "")
        if how == "link":
            return f"You already applied here on {when} ({what}, status: {status})."
        return f"Possibly already applied: {what} on {when} (status: {status}), from {row.get('Portal', '?')}."

    async def _choose(self, ex: fields.Extraction, choices: dict[str, list[str]], fills: dict[str, str],
                      prof: profile.Profile) -> dict[str, str]:
        """Dropdowns whose options only exist once opened (Workday): let the AI pick from the real list."""
        by_id = {f.id: f for f in ex.fields}
        asks = [replace(by_id[fid], kind="select", options=opts, current_value="")
                for fid, opts in choices.items() if opts]
        out = {fid: "choose" for fid in choices}
        if not asks:
            return out
        try:
            res = await self.backend.map_fields(MapRequest(asks, prof.text, ex.job_text, ex.url, ex.title))
        except LLMError as e:
            print(f"[choose] {e}", flush=True)
            return out
        picked = {fid: v for fid, v in res.fills.items() if fid in choices}
        for fid, s in (await filler.apply(ex, picked)).items() if picked else ():
            if s == "ok":
                out[fid] = "ok"
                fills[fid] = picked[fid]
        return out

    def _questions(self, st: TabState, items: list[tuple[FormField, str, str]], prof: profile.Profile) -> list[dict]:
        out = []
        for f, suggestion, hint in items:
            st.seq += 1
            qid = f"q{st.seq}"
            section, key = rules.save_target(f, f.label, prof)
            st.questions[qid] = Question(qid, f, section, key)
            options = f.options if (f.kind in CHOICE_KINDS or f.options) else \
                (["Yes", "No"] if f.kind == "checkbox" else [])
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
            if rules.is_salary(q.field):
                st.salary = value
            if a.get("save"):
                saves.append((q, value))

        status = await filler.apply(st.ex, fills)
        filled = sum(1 for s in status.values() if s in ("ok", "typed"))
        st.filled.update({fid: v for fid, v in fills.items() if status.get(fid) in ("ok", "typed")})

        saved, conflicts = self._save(st, saves)

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

    def _save(self, st: TabState, saves: list[tuple[Question, str]]) -> tuple[int, list[dict]]:
        """Write answers to profile.md. Returns (how many saved, conflicts to confirm in the panel)."""
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
        return saved, conflicts

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

    # ------------------------------------------------------------------ log as applied
    async def _job_info(self, page: Page, st: TabState) -> JobInfo:
        """Company / position / location: from the fill step if the AI saw the posting, else a small extract call."""
        job = st.job
        if job.company and job.position:
            return job
        ex = st.ex
        heads = ex.headings if ex else {}
        try:
            raw = await self.backend.extract(prompts.JOB_SYSTEM, prompts.job_prompt(
                page.url, await page.title(), heads, ex.job_text if ex else ""), prompts.JOB_SCHEMA, "job_info")
            got = JobInfo(*(prompts.clean_meta(raw.get(k, "")) for k in ("company", "position", "location")))
        except LLMError as e:
            print(f"[log] job info: {e}", flush=True)
            got = JobInfo(heads.get("site", ""), heads.get("h1", "") or heads.get("og_title", ""), "")
        st.job = JobInfo(job.company or got.company, job.position or got.position,
                         job.location or got.location, job.language)
        return st.job

    async def on_log_form(self, page: Page) -> None:
        st = self.state(page)
        prof = self._profile()
        await show_text(page, "Preparing the log entry…")
        job = await self._job_info(page, st)
        st.spec = {**st.spec, "status": "Check these details, then save.", "log_form": {
            "portal": await self.provenance.portal(page, prof.portals()), "company": job.company,
            "position": job.position, "link": applog.normalize_link(st.apply_url or page.url),
            "location": job.location, "salary": await self._salary(st), "notes": ""}}
        await show(page, st.spec)

    async def _salary(self, st: TabState) -> str:
        """The salary you gave for this application: panel answer, else what's in a salary field on the page."""
        if st.salary:
            return st.salary
        sal = [f for f in st.known.values() if rules.is_salary(f)]
        on_page = {f.id for f in st.ex.fields} if st.ex else set()
        now = await filler.values(st.ex, [f.id for f in sal if f.id in on_page]) if st.ex and sal else {}
        for f in sal:
            v = now.get(f.id) or st.edits.get(f.id) or ""
            if v.strip():
                return v.strip()
        return ""

    async def on_log(self, page: Page, row) -> None:
        st = self.state(page)
        if not isinstance(row, dict):
            return
        prof = self._profile()
        role = self.role(prof)
        result, written = applog.log(self.log_path, {
            "Portal": row.get("portal", ""), "Company": row.get("company", ""),
            "Position": row.get("position", ""), "Application link": row.get("link", "") or page.url,
            "CV used": st.cv_used or self.cv_for(prof).name, "Location": row.get("location", ""),
            "Notes": row.get("notes", ""), "Role": role, "Expected salary": row.get("salary", "")},
            self.pending_path)
        if result == "added":
            head = f"Logged ✓ {written['Company']} – {written['Position']} ({written['Portal']})."
        elif result == "duplicate":
            head = f"Already logged on {written.get('Date', '?')}: {written.get('Company', '')} – " \
                   f"{written.get('Position', '')}. Nothing added."
        else:
            head = "applications.csv is open (Excel?). Saved to pending.csv; it's added automatically " \
                   "next time you log or start JobBot."
        st.logged = True
        learn = await self._learn_candidates(st, prof)
        st.spec = {**st.spec, "status": head, "log_form": None, "can_log": False, "learn": learn,
                   "questions": [], "conflicts": []}
        print(f"[log] {result}: {written.get('Application link')}", flush=True)
        await show(page, st.spec)

    async def _learn_candidates(self, st: TabState, prof: profile.Profile) -> list[dict]:
        """Fields you filled or changed yourself (not drafts, salary, consent, passwords, files)."""
        norm = lambda t: " ".join((t or "").split()).casefold()
        todo = [f for f in st.known.values()
                if f.kind not in ("password", "file", "checkbox") and f.id not in st.drafts
                and not (rules.is_salary(f) or rules.is_consent(f) or rules.is_free_text(f))]
        on_page = {f.id for f in st.ex.fields} if st.ex else set()
        now = await filler.values(st.ex, [f.id for f in todo if f.id in on_page]) if st.ex else {}
        st.learn = {}
        out = []
        for f in todo:
            v = now.get(f.id)
            v = (v if v is not None else st.edits.get(f.id, "")).strip()    # earlier steps: last reported edit
            if not v:
                continue
            before = st.filled.get(f.id, st.original.get(f.id, ""))
            if norm(v) == norm(before):
                continue                                    # what JobBot (or the site) put there, unchanged
            section, key = rules.save_target(f, f.label, prof)
            existing = prof.get(section, key) if section != profile.QA_SECTION else prof.qa().get(key)
            if existing and norm(existing) == norm(v):
                continue
            st.seq += 1
            lid = f"l{st.seq}"
            st.learn[lid] = (Question(lid, f, section, key), v[:MAX_ANSWER])
            out.append({"lid": lid, "label": f.label or key, "value": v[:200]})
        return out

    async def on_learn(self, page: Page, ids) -> None:
        st = self.state(page)
        if not isinstance(ids, list):
            return
        saves = [st.learn.pop(str(i)) for i in ids[:100] if str(i) in st.learn]
        saved, conflicts = self._save(st, saves)
        st.spec = {**st.spec, "learn": [], "conflicts": conflicts,
                   "status": st.spec.get("status", "").split("\n")[0] +
                   f"\nSaved {saved} edit{'s' if saved != 1 else ''} to profile.md."}
        await show(page, st.spec)
