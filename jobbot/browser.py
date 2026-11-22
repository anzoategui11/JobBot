"""Attach to Brave over CDP, inject the page agent, and dispatch messages from pages."""

import asyncio
from pathlib import Path

from playwright.async_api import Page, async_playwright

from .config import Config
from .launcher import ensure_brave

AGENT_JS = Path(__file__).resolve().parent / "inject" / "agent.js"
BINDING = "__jobbot"


async def show_panel(page: Page, text: str) -> None:
    await page.main_frame.evaluate("t => window.__jobbotPanel && window.__jobbotPanel.show(t)", text)


async def on_message(source: dict, msg: dict) -> None:
    page: Page = source["page"]
    kind = msg.get("type") if isinstance(msg, dict) else None
    if kind == "hotkey":
        print(f"[hotkey] {page.url}", flush=True)
        title = await page.title()
        await show_panel(page, f"Hotkey received on: {title or page.url}")
    else:
        print(f"[ignored message] {msg!r}", flush=True)


async def inject_existing(page: Page, script: str) -> None:
    """Init scripts only reach new documents; run the agent in tabs that were already open."""
    for frame in page.frames:
        try:
            await frame.evaluate(script)
        except Exception as e:  # detached/navigating frames are expected
            print(f"[inject skipped] {frame.url[:80]}: {type(e).__name__}", flush=True)


async def run(cfg: Config) -> None:
    launched = await ensure_brave(cfg)
    print("Brave started." if launched else "Attached to running JobBot Brave.", flush=True)
    script = AGENT_JS.read_text(encoding="utf-8")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cfg.port}", is_local=True)
        ctx = browser.contexts[0]
        await ctx.expose_binding(BINDING, on_message)
        await ctx.add_init_script(script=script)
        for page in ctx.pages:
            await inject_existing(page, script)

        closed = asyncio.Event()
        browser.on("disconnected", lambda _: closed.set())
        print("JobBot ready - press Ctrl+Alt+G on a page. Close Brave to quit.", flush=True)
        await closed.wait()
    print("Brave closed - JobBot stopped.", flush=True)
