"""Phase 4: field extraction from saved fixtures in a throwaway headless Brave."""

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from jobbot import browser as jb
from jobbot import fields
from jobbot.config import load

BRAVE = load().brave_exe
SCRIPT = jb.AGENT_JS.read_text(encoding="utf-8")
FIX = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(not BRAVE.exists(), reason="Brave not installed")


async def _extract(name: str, preload=False) -> tuple[fields.Extraction, object]:
    """Serve tests/fixtures/* at https://jobbot.test/ and extract from <name>."""
    p = await async_playwright().start()
    b = await p.chromium.launch(executable_path=str(BRAVE), headless=True)
    ctx = await b.new_context()

    async def fulfill(route):
        path = FIX / (route.request.url.rsplit("/", 1)[-1] or "index.html")
        if path.exists():
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=path.read_bytes())
        else:
            await route.fulfill(status=404, body="")
    await ctx.route("https://jobbot.test/**", fulfill)
    if not preload:
        await ctx.add_init_script(script=SCRIPT)
    page = await ctx.new_page()
    await page.goto(f"https://jobbot.test/{name}")
    await page.wait_for_load_state("load")
    ex = await fields.extract(page, SCRIPT)
    return ex, (p, b, page)


async def _close(handles):
    p, b, _ = handles
    await b.close()
    await p.stop()


def by_label(ex):
    return {f.label: f for f in ex.fields}


def test_plain_form_labels_and_kinds():
    async def go():
        ex, h = await _extract("plain.html")
        f = by_label(ex)
        assert f["First name"].kind == "text" and f["First name"].required
        assert f["First name"].autocomplete == "given-name"
        assert f["Email"].kind == "email"
        assert f["Phone number"].kind == "tel"
        assert f["City"].kind == "text"
        assert f["LinkedIn profile"].kind == "text"              # nearest preceding text
        assert f["Country"].options == ["Argentina", "Brazil"]    # placeholder option dropped
        assert f["Why Acme?"].kind == "textarea" and f["Why Acme?"].current_value == "draft"
        assert f["Resume/CV"].kind == "file" and ".pdf" in f["Resume/CV"].accept   # hidden file input kept
        assert f["Password"].kind == "password" and f["Password"].current_value == ""  # never read out
        assert f["I agree to the privacy policy"].current_value == "false"
        labels = set(f)
        assert not labels & {"Hidden field", "Disabled field", "token", "Submit"}
        assert "Senior AI Engineer" in ex.job_text and ex.headings["site"] == "Acme Careers"
        ids = [x.id for x in ex.fields]
        assert len(ids) == len(set(ids))
        page = h[2]                                               # ids are tagged on the elements
        assert await page.get_attribute("#fn", "data-jobbot-id") == f["First name"].id
        await _close(h)
    asyncio.run(go())


def test_ids_stable_across_hotkeys():
    async def go():
        ex, h = await _extract("plain.html")
        again = await fields.extract(h[2], SCRIPT)
        assert [x.id for x in ex.fields] == [x.id for x in again.fields]
        await _close(h)
    asyncio.run(go())


def test_shadow_dom_pierced():
    async def go():
        ex, h = await _extract("shadow.html")
        f = by_label(ex)
        assert f["Last name"].kind == "text"
        assert f["Years of experience"].kind == "number"          # nested shadow root + aria-labelledby
        await _close(h)
    asyncio.run(go())


def test_iframe_fields_and_job_text():
    async def go():
        ex, h = await _extract("iframe.html")
        f = by_label(ex)
        assert f["Email address"].kind == "email" and f["When can you start?"].kind == "date"
        assert ex.frame_of[f["Email address"].id] is not h[2].main_frame
        assert "Airflow pipelines" in ex.job_text
        await _close(h)
    asyncio.run(go())


def test_preexisting_tab_without_init_script():
    async def go():
        ex, h = await _extract("iframe.html", preload=True)       # fields.extract injects on demand
        assert {"Email address", "When can you start?"} <= set(by_label(ex))
        await _close(h)
    asyncio.run(go())


def test_radio_groups_and_checkboxes():
    async def go():
        ex, h = await _extract("radio.html")
        f = by_label(ex)
        visa = f["Do you require visa sponsorship?"]
        assert visa.kind == "radio" and visa.options == ["Yes", "No"] and visa.current_value == "No"
        assert visa.required
        reloc = f["Are you willing to relocate?"]                  # visually hidden radios, preceding text
        assert reloc.kind == "radio" and reloc.options == ["Yes", "No"]
        arr = f["Work arrangement"]
        assert arr.kind == "radio" and arr.options == ["Remote", "Hybrid"] and arr.current_value == "Hybrid"
        assert f["Which languages do you speak? — English"].current_value == "true"
        assert f["Which languages do you speak? — German"].kind == "checkbox"
        assert sum(1 for x in ex.fields if x.kind == "radio") == 3
        await _close(h)
    asyncio.run(go())


def test_comboboxes_and_contenteditable():
    async def go():
        ex, h = await _extract("combobox.html")
        f = by_label(ex)
        assert f["Location (city)"].kind == "combobox"
        assert f["Location (city)"].options == ["Buenos Aires", "Córdoba"]
        assert f["How did you hear about us?"].kind == "combobox"
        assert f["Cover letter"].kind == "textarea"
        await _close(h)
    asyncio.run(go())


def test_summary_and_token_estimate():
    async def go():
        ex, h = await _extract("plain.html")
        text = fields.summary(ex)
        assert "Nothing was filled" in text and "First name · text" in text
        assert 0 < jb.estimate_tokens(ex, "## Contact\n- First name: Jane\n") < 3000
        await _close(h)
    asyncio.run(go())


def test_open_dialog_is_the_only_form():
    async def go():
        ex, h = await _extract("modal.html")
        assert [x.label for x in ex.fields] == ["What is your current location?"]   # no page filters, no iframe
        assert ex.headings["dialog"] == "Apply to Jobgether"
        assert "Identification Accuracy" in ex.job_text                              # job text still from the page
        assert "Apply to Jobgether" in fields.summary(ex)
        await _close(h)
    asyncio.run(go())


def test_custom_dropdown_label_from_its_wrapper():
    async def go():
        ex, h = await _extract("customselect.html")
        assert [(x.label, x.kind) for x in ex.fields] == [
            ("Full Name", "text"), ("Location", "combobox"), ("Seniority", "combobox")]
        await _close(h)
    asyncio.run(go())
