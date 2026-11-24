"""Phase 5: hotkey -> local rules -> (stub) LLM -> fill -> CV -> panel -> answers -> profile.md."""

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from jobbot import browser as jb
from jobbot import profile
from jobbot.config import load
from jobbot.flow import Session
from jobbot.llm.base import LLMError
from jobbot.model import JobInfo, MapResult

BRAVE = load().brave_exe
SCRIPT = jb.AGENT_JS.read_text(encoding="utf-8")
FIX = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(not BRAVE.exists(), reason="Brave not installed")

PROFILE = """# JobBot profile

## Contact
- First name: Jane
- Last name: Doe
- Email: jane@example.com
- Phone: +1 555 0100
- City: Austin
- Province: Capital Federal
- Country: Argentina
- LinkedIn: linkedin.com/in/janedoe

## Preferences
- Notice period: two weeks

## Salary history
- Expected salary (USD / month): 5000

## Roles
- ML Engineer: {cv}
- Data Engineer: {cv}

## Portals
- linkedin.com: LinkedIn
- indeed.com: Indeed

## Experience summary
I build ranking models.

## Q&A bank
"""


class StubBackend:
    """Answers the visa radio and leaves PyTorch years unknown."""
    def __init__(self, delay=0.0):
        self.requests, self.batches, self.delay = [], [], delay

    async def draft_answers(self, batch):
        self.batches.append(batch)
        await asyncio.sleep(self.delay)
        return {q.id: f"Draft ({q.language}, {batch.role}): {q.question}" for q in batch.questions}

    async def extract(self, system, prompt, schema, name):
        return {"company": "Initech", "position": "ML Engineer", "location": "Buenos Aires"}

    async def map_fields(self, req):
        self.requests.append(req)
        by_label = {f.label: f.id for f in req.fields}
        fills = {}
        if "Do you require visa sponsorship?" in by_label:
            fills[by_label["Do you require visa sponsorship?"]] = "No"
        unknown = {fid: "How many years of PyTorch?" for lbl, fid in by_label.items() if "PyTorch" in lbl}
        return MapResult(JobInfo("Initech", "ML Engineer", "Buenos Aires", "en"), fills, unknown)


async def _open(tmp_path, start="https://jobbot.test/apply.html"):
    p = await async_playwright().start()
    b = await p.chromium.launch(executable_path=str(BRAVE), headless=True)
    ctx = await b.new_context()

    async def fulfill(route):
        path = FIX / route.request.url.rsplit("/", 1)[-1]
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=path.read_bytes())

    async def linkedin(route):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                            body=(FIX / "listing.html").read_bytes())
    await ctx.route("https://jobbot.test/**", fulfill)
    await ctx.route("https://www.linkedin.com/**", linkedin)
    await ctx.add_init_script(script=SCRIPT)
    prof = tmp_path / "profile.md"
    cv = tmp_path / "Jane Doe - ML.pdf"
    cv.write_bytes(b"%PDF-1.4\n%fake\n")
    prof.write_text(PROFILE.replace("{cv}", str(cv)), encoding="utf-8")
    backend = StubBackend(delay=0.3)
    session = Session(prof, cv, backend, SCRIPT, state_path=tmp_path / "state.json",
                      log_path=tmp_path / "applications.csv", pending_path=tmp_path / "pending.csv")
    ctx.on("page", session.provenance.track)
    await ctx.expose_binding(jb.BINDING, session.handle)
    page = await ctx.new_page()
    await page.goto(start)
    return p, b, page, session, backend, prof


def _q(session, page, label_part):
    spec = session.states[page].spec
    return next(q for q in spec["questions"] if label_part in q["label"])


def test_hotkey_fills_and_asks(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        await session.handle({"page": page}, {"type": "hotkey"})

        v = page.input_value
        assert await v("#fn") == "Jane" and await v("#ln") == "Doe"            # local, no LLM
        assert await v("#em") == "prefilled@example.com"                         # never overwritten
        assert await v("#ph") == "+1 555 0100"
        assert await v("#li") == "https://linkedin.com/in/janedoe"
        assert await page.eval_on_selector("#cty", "s => s.selectedOptions[0].text") == "Argentina"
        assert await page.is_checked("input[name=visa][value=n]")              # from the LLM
        assert await v("#sal") == ""                                            # salary: asked
        assert not await page.is_checked("#terms")                              # consent: never
        assert await page.evaluate("document.querySelector('#cv').files[0].name") == "Jane Doe - ML.pdf"
        assert await page.evaluate("document.querySelector('#cl').files.length") == 0
        assert not await page.evaluate("!!window.__clicked || !!window.__submitted")

        sent = {f.label for f in backend.requests[0].fields}                    # only what rules couldn't do
        assert "First name" not in sent and "Expected salary (USD / month)" not in sent
        assert "I agree to the privacy policy" not in sent and "Resume/CV" not in sent
        assert "Do you require visa sponsorship?" in sent

        spec = session.states[page].spec
        labels = [q["label"] for q in spec["questions"]]
        assert "Years of PyTorch experience" in labels
        assert _q(session, page, "Expected salary")["value"] == "5000"           # last answer suggested
        assert any("privacy policy" in m for m in spec["manual"])
        assert "CV attached" in spec["status"] and "submit yourself" in spec["status"]
        panel = await page.evaluate("document.querySelector('[data-jobbot-ui]').shadowRoot.textContent")
        assert "Needs your answer" in panel and "Do these yourself" in panel

        # second press on the same page: CV not attached twice, filled values left alone
        await session.handle({"page": page}, {"type": "hotkey"})
        assert "CV attached" not in session.states[page].spec["status"]
        assert len(backend.requests) == 2 and "First name" not in {f.label for f in backend.requests[1].fields}
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_answers_fill_page_and_save_profile(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        await session.handle({"page": page}, {"type": "hotkey"})
        yrs, sal = _q(session, page, "PyTorch"), _q(session, page, "salary")
        notice = _q(session, page, "Notice period")
        assert notice["options"] == ["Immediately", "2 weeks", "1 month"]
        answers = [{"qid": yrs["qid"], "value": "3", "save": True},
                   {"qid": sal["qid"], "value": "6000", "save": True},
                   {"qid": notice["qid"], "value": "2 weeks", "save": True},
                   {"qid": "q999", "value": "forged", "save": True}]
        await session.handle({"page": page}, {"type": "answers", "answers": answers})

        assert await page.input_value("#yrs") == "3"
        assert await page.input_value("#sal") == "6000"
        assert await page.eval_on_selector("#np", "s => s.selectedOptions[0].text") == "2 weeks"
        text = prof.read_text(encoding="utf-8")
        pr = profile.load(prof)
        assert pr.get("Other facts", "Years of PyTorch experience") == "3"
        assert pr.get("Salary history", "Expected salary (USD / month)") == "6000"   # last answer replaced
        assert "forged" not in text
        assert pr.get("Preferences", "Notice period") == "two weeks"                  # conflict, not overwritten

        spec = session.states[page].spec
        done = {yrs["qid"], sal["qid"], notice["qid"]}
        assert not [q for q in spec["questions"] if q["qid"] in done]
        (c,) = spec["conflicts"]
        assert "two weeks" in c["text"] and "2 weeks" in c["text"]
        await session.handle({"page": page}, {"type": "replace", "cid": c["cid"]})
        assert profile.load(prof).get("Preferences", "Notice period") == "2 weeks"
        assert session.states[page].spec["conflicts"] == []
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_llm_failure_still_fills_locally_and_asks(tmp_path):
    class Down:
        async def map_fields(self, req):
            raise LLMError("Groq rate limit reached - try again in 12s")

    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        session.backend = Down()
        await session.handle({"page": page}, {"type": "hotkey"})
        assert await page.input_value("#fn") == "Jane"
        spec = session.states[page].spec
        assert any("12s" in n for n in spec["notes"])
        assert "Do you require visa sponsorship?" in [q["label"] for q in spec["questions"]]
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_combobox_picks_from_list(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        await session.handle({"page": page}, {"type": "hotkey"})
        loc = _q(session, page, "Location")
        await session.handle({"page": page}, {"type": "answers",
                                              "answers": [{"qid": loc["qid"], "value": "Bogo", "save": False}]})
        assert await page.evaluate("window.__picked || ''") == "Bogotá"         # typed, then picked from list
        assert await page.input_value("#loc") == "Bogotá"
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_drafts_fill_open_questions_in_their_language(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        await session.handle({"page": page}, {"type": "hotkey"})
        st = session.states[page]
        assert "Drafting 3 answers" in st.spec["status"] and st.spec["role"] == "ML Engineer"
        assert st.spec["roles"] == ["ML Engineer", "Data Engineer"]
        await page.fill("#proj", "My own words")          # typed while the AI was drafting
        await st.task
        assert await page.input_value("#why") == "Draft (en, ML Engineer): Why do you want to work at Initech?"
        assert (await page.input_value("#es")).startswith("Draft (es, ML Engineer)")
        assert await page.input_value("#proj") == "My own words"                 # never overwritten
        (batch,) = backend.batches                                                # one request for all
        assert len(batch.questions) == 3
        assert "jane@example.com" not in batch.profile_md and "5000" not in batch.profile_md
        assert "I build ranking models." in batch.profile_md
        assert "Initech is hiring" in batch.job_text
        spec = st.spec
        assert "Why do you want to work at Initech?" in spec["drafts"] and "Describe a project" not in str(spec["drafts"])
        assert "Drafted 2 answers" in spec["status"] and "Drafting" not in spec["status"]
        assert any("already typed" in n for n in spec["notes"])
        panel = await page.evaluate("document.querySelector('[data-jobbot-ui]').shadowRoot.textContent")
        assert "AI draft" in panel
        assert "Draft (" not in prof.read_text(encoding="utf-8")                  # drafts never saved
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_role_is_remembered_and_unedited_drafts_redrafted(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        await session.handle({"page": page}, {"type": "hotkey"})
        await session.states[page].task
        await page.fill("#why", "Edited by me")
        await session.handle({"page": page}, {"type": "role", "role": "Data Engineer"})
        await session.states[page].task
        assert await page.input_value("#why") == "Edited by me"
        assert (await page.input_value("#proj")).startswith("Draft (en, Data Engineer)")
        assert [q.question for q in backend.batches[-1].questions] == [
            "Describe a project you are proud of", "¿Por qué quieres trabajar con nosotros?"]
        assert Session(prof, Path("x"), backend, SCRIPT, state_path=tmp_path / "state.json").saved["role"] \
            == "Data Engineer"
        await session.handle({"page": page}, {"type": "role", "role": "Astronaut"})   # not in ## Roles: ignored
        assert session.saved["role"] == "Data Engineer"
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_draft_failure_turns_into_questions(tmp_path):
    class NoDrafts(StubBackend):
        async def draft_answers(self, batch):
            raise LLMError("Groq rate limit reached - try again in 30s")

    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        session.backend = NoDrafts()
        await session.handle({"page": page}, {"type": "hotkey"})
        await session.states[page].task
        spec = session.states[page].spec
        assert "Why do you want to work at Initech?" in [q["label"] for q in spec["questions"]]
        assert any("30s" in n for n in spec["notes"])
        await b.close()
        await p.stop()
    asyncio.run(go())


def _rows(path):
    import csv
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_log_from_company_site_with_learned_edits(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path)
        await session.handle({"page": page}, {"type": "hotkey"})
        await session.states[page].task
        await page.fill("#ln", "Doe-Smith")                       # you corrected what JobBot filled
        await page.fill("#yrs", "4")                              # you typed an answer yourself
        await page.evaluate("document.querySelector('#yrs').remove()")   # e.g. Easy Apply moved to the next step
        await page.fill("#why", "my own why")                     # free text: never offered
        await page.fill("#sal", "6000")                           # salary typed on the page: goes to the log
        await session.handle({"page": page}, {"type": "log_form"})
        form = session.states[page].spec["log_form"]
        assert form == {"portal": "Company site", "company": "Initech", "position": "ML Engineer",
                        "link": "https://jobbot.test/apply.html", "location": "Buenos Aires", "salary": "6000",
                        "notes": ""}
        await session.handle({"page": page}, {"type": "log", "row": {**form, "notes": "referral"}})
        (row,) = _rows(tmp_path / "applications.csv")
        assert row["Portal"] == "Company site" and row["Company"] == "Initech" and row["Status"] == "Applied"
        assert row["Expected salary"] == "6000"
        assert row["CV used"] == "Jane Doe - ML.pdf" and row["Role"] == "ML Engineer" and row["Notes"] == "referral"
        spec = session.states[page].spec
        assert spec["status"].startswith("Logged") and spec["log_form"] is None and not spec["can_log"]
        learn = {x["label"]: x for x in spec["learn"]}
        assert set(learn) == {"Last name", "Years of PyTorch experience"}
        assert learn["Years of PyTorch experience"]["value"] == "4"
        await session.handle({"page": page}, {"type": "learn", "ids": [x["lid"] for x in spec["learn"]]})
        pr = profile.load(prof)
        assert pr.get("Other facts", "Years of PyTorch experience") == "4"
        assert pr.get("Contact", "Last name") == "Doe"                       # conflict: asked, not overwritten
        assert len(session.states[page].spec["conflicts"]) == 1

        await session.handle({"page": page}, {"type": "log", "row": form})  # second time: duplicate
        assert "Already logged" in session.states[page].spec["status"]
        assert len(_rows(tmp_path / "applications.csv")) == 1
        await b.close()
        await p.stop()
    asyncio.run(go())


def test_portal_follows_the_tab_that_opened_the_form(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path, "https://www.linkedin.com/jobs/view/123/")
        async with page.context.expect_page() as info:
            await page.click("#ext")                              # noopener link to the company site
        popup = await info.value
        await popup.wait_for_load_state()
        portals = profile.load(prof).portals()
        assert await session.provenance.portal(popup, portals) == "LinkedIn"
        await page.click("#same")                                 # same tab: LinkedIn -> company site
        await page.wait_for_url("https://jobbot.test/apply.html")
        assert await session.provenance.portal(page, portals) == "LinkedIn"
        direct = await page.context.new_page()
        await direct.goto("https://jobbot.test/apply.html")
        assert await session.provenance.portal(direct, portals) == "Company site"
        await b.close()
        await p.stop()
    asyncio.run(go())


class WorkdayStub(StubBackend):
    """First pass: answers the authorization question. Second pass (real options): picks CABA for Province."""
    async def map_fields(self, req):
        self.requests.append(req)
        fills = {}
        for f in req.fields:
            if f.label.startswith("Are you currently legally authorized"):
                fills[f.id] = "Yes"
            if f.label == "Province" and f.kind == "select":
                assert f.options == ["Buenos Aires", "Ciudad Autónoma de Buenos Aires", "Córdoba"]
                fills[f.id] = "Ciudad Autónoma de Buenos Aires"
        return MapResult(JobInfo("Acme", "Data Engineer", "Buenos Aires", "en"), fills, {})


def test_workday_dropdowns_resume_and_second_pass(tmp_path):
    async def go():
        p, b, page, session, backend, prof = await _open(tmp_path, "https://jobbot.test/workday.html")
        session.backend = WorkdayStub()
        await session.handle({"page": page}, {"type": "hotkey"})
        text = lambda sel: page.inner_text(sel)
        assert await text("#q1") == "Yes"
        assert await text("#q2") == "Ciudad Autónoma de Buenos Aires"       # "Capital Federal" -> real option
        assert await text("#q3") == "Select One"                             # opt-out: yours to decide
        assert await page.evaluate("document.querySelectorAll('[role=listbox]').length") == 0   # lists closed
        first = {f.label for f in session.backend.requests[0].fields}
        assert "Province" not in first                                       # filled locally, then re-picked
        spec = session.states[page].spec
        assert any("opt out" in m for m in spec["manual"])
        q4 = next(q for q in spec["questions"] if "24 months" in q["label"])
        assert q4["options"] == [] and q4["kind"] == "combobox"
        assert await page.evaluate("document.querySelector('[data-ui=resume]').files.length") == 1
        assert await page.evaluate("document.querySelector('[data-ui=cover_letter]').files.length") == 0
        await b.close()
        await p.stop()
    asyncio.run(go())
