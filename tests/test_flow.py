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
- Country: Argentina
- LinkedIn: linkedin.com/in/janedoe

## Preferences
- Notice period: two weeks

## Salary history
- Expected salary (USD / month): 5000

## Q&A bank
"""


class StubBackend:
    """Answers the visa radio and leaves PyTorch years unknown."""
    def __init__(self):
        self.requests = []

    async def map_fields(self, req):
        self.requests.append(req)
        by_label = {f.label: f.id for f in req.fields}
        fills = {}
        if "Do you require visa sponsorship?" in by_label:
            fills[by_label["Do you require visa sponsorship?"]] = "No"
        unknown = {fid: "How many years of PyTorch?" for lbl, fid in by_label.items() if "PyTorch" in lbl}
        return MapResult(JobInfo("Initech", "ML Engineer", "Buenos Aires", "en"), fills, unknown)


async def _open(tmp_path):
    p = await async_playwright().start()
    b = await p.chromium.launch(executable_path=str(BRAVE), headless=True)
    ctx = await b.new_context()

    async def fulfill(route):
        path = FIX / route.request.url.rsplit("/", 1)[-1]
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=path.read_bytes())
    await ctx.route("https://jobbot.test/**", fulfill)
    await ctx.add_init_script(script=SCRIPT)
    prof = tmp_path / "profile.md"
    prof.write_text(PROFILE, encoding="utf-8")
    cv = tmp_path / "Jane Doe - ML.pdf"
    cv.write_bytes(b"%PDF-1.4\n%fake\n")
    backend = StubBackend()
    session = Session(prof, cv, backend, SCRIPT)
    await ctx.expose_binding(jb.BINDING, session.handle)
    page = await ctx.new_page()
    await page.goto("https://jobbot.test/apply.html")
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
        assert await v("#sal") == "" and await v("#why") == ""                 # salary + free text: asked
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
        why = _q(session, page, "Why do you want")
        answers = [{"qid": yrs["qid"], "value": "3", "save": True},
                   {"qid": sal["qid"], "value": "6000", "save": True},
                   {"qid": notice["qid"], "value": "2 weeks", "save": True},
                   {"qid": why["qid"], "value": "I like ranking problems.", "save": False},
                   {"qid": "q999", "value": "forged", "save": True}]
        await session.handle({"page": page}, {"type": "answers", "answers": answers})

        assert await page.input_value("#yrs") == "3"
        assert await page.input_value("#sal") == "6000"
        assert await page.input_value("#why") == "I like ranking problems."
        assert await page.eval_on_selector("#np", "s => s.selectedOptions[0].text") == "2 weeks"
        text = prof.read_text(encoding="utf-8")
        pr = profile.load(prof)
        assert pr.get("Other facts", "Years of PyTorch experience") == "3"
        assert pr.get("Salary history", "Expected salary (USD / month)") == "6000"   # last answer replaced
        assert "I like ranking problems" not in text and "forged" not in text
        assert pr.get("Preferences", "Notice period") == "two weeks"                  # conflict, not overwritten

        spec = session.states[page].spec
        done = {yrs["qid"], sal["qid"], notice["qid"], why["qid"]}
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
