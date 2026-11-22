"""Exercise inject/agent.js in a throwaway headless Brave (no profile, no network)."""

import asyncio

import pytest
from playwright.async_api import async_playwright

from jobbot import browser as jb
from jobbot.config import load

BRAVE = load().brave_exe
SCRIPT = jb.AGENT_JS.read_text(encoding="utf-8")
FORM = "<html><body><input id='a'></body></html>"

pytestmark = pytest.mark.skipif(not BRAVE.exists(), reason="Brave not installed")


def run(coro):
    return asyncio.run(coro)


async def _setup(p):
    b = await p.chromium.launch(executable_path=str(BRAVE), headless=True)
    ctx = await b.new_context()
    got = []
    await ctx.expose_binding(jb.BINDING, lambda source, msg: got.append(msg))
    return b, ctx, got


async def _open(ctx, html: str):
    """Real navigation to a fake https URL (set_content's document.open() would wipe the
    window listeners that the init script installed, which real sites don't do)."""
    async def fulfill(route):
        await route.fulfill(status=200, content_type="text/html", body=html)
    await ctx.route("https://jobbot.test/**", fulfill)
    page = await ctx.new_page()
    await page.goto("https://jobbot.test/")
    return page


async def _wait(got, n=1):
    for _ in range(50):
        if len(got) >= n:
            return
        await asyncio.sleep(0.05)


def test_hotkey_reaches_python_and_types_nothing():
    async def go():
        async with async_playwright() as p:
            b, ctx, got = await _setup(p)
            await ctx.add_init_script(script=SCRIPT)
            page = await _open(ctx, FORM)
            await page.locator("#a").click()
            await page.keyboard.press("Control+Alt+KeyG")
            await _wait(got)
            assert got and got[0]["type"] == "hotkey" and got[0]["top"] is True
            assert await page.locator("#a").input_value() == ""
            await b.close()
    run(go())


def test_hotkey_inside_iframe_is_forwarded():
    async def go():
        async with async_playwright() as p:
            b, ctx, got = await _setup(p)
            await ctx.add_init_script(script=SCRIPT)
            page = await _open(ctx, "<iframe srcdoc=\"<input id='x'>\"></iframe>")
            frame = page.frame_locator("iframe")
            await frame.locator("#x").click()
            await page.keyboard.press("Control+Alt+KeyG")
            await _wait(got)
            assert got and got[0]["top"] is False
            await b.close()
    run(go())


def test_existing_tab_injection_and_no_double_listener():
    async def go():
        async with async_playwright() as p:
            b, ctx, got = await _setup(p)
            page = await _open(ctx, FORM)          # loaded BEFORE any init script
            await jb.inject_existing(page, SCRIPT)
            await jb.inject_existing(page, SCRIPT)  # second injection must be a no-op
            await page.locator("#a").click()
            await page.keyboard.press("Control+Alt+KeyG")
            await _wait(got)
            await asyncio.sleep(0.2)
            assert len(got) == 1
            await b.close()
    run(go())


def test_panel_renders_in_shadow_root():
    async def go():
        async with async_playwright() as p:
            b, ctx, _ = await _setup(p)
            await ctx.add_init_script(script=SCRIPT)
            page = await _open(ctx, FORM)
            await jb.show_panel(page, "hello <b>not html</b>")
            text = await page.evaluate(
                "document.querySelector('[data-jobbot-ui]').shadowRoot.querySelector('.body').textContent")
            assert text == "hello <b>not html</b>"
            await page.locator(".close").click()   # locators pierce open shadow roots
            assert await page.evaluate(
                "document.querySelector('[data-jobbot-ui]').style.display") == "none"
            await b.close()
    run(go())
