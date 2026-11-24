"""Attach to Brave over CDP, inject the page agent, and dispatch messages from pages."""

import asyncio
from pathlib import Path

from playwright.async_api import Page, async_playwright

from .config import Config
from . import applog, fields
from .flow import Session
from .llm import get_backend
from .launcher import ensure_brave
from .llm.prompts import MAP_SYSTEM, map_prompt
from .model import MapRequest

AGENT_JS = Path(__file__).resolve().parent / "inject" / "agent.js"
BINDING = "__jobbot"


async def show_panel(page: Page, text: str) -> None:
    await page.main_frame.evaluate("t => window.__jobbotPanel && window.__jobbotPanel.show(t)", text)


def estimate_tokens(ex: fields.Extraction, profile_md: str) -> int:
    """Rough map-request size (~4 chars/token) to stay inside Groq's free-tier tokens-per-minute."""
    req = MapRequest(ex.fields, profile_md, ex.job_text, ex.url, ex.title)
    return (len(MAP_SYSTEM) + len(map_prompt(req))) // 4


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
        backend = get_backend(cfg)
        session = Session(cfg.data_dir / "profile.md", cfg.cv_path, backend, script)
        if n := applog.flush_pending(session.log_path):
            print(f"Wrote {n} pending application(s) to {session.log_path.name}.", flush=True)
        ctx.on("page", session.provenance.track)            # new tabs: remember who opened them
        await ctx.expose_binding(BINDING, session.handle)
        await ctx.add_init_script(script=script)
        for page in ctx.pages:
            session.provenance.track(page)
            await inject_existing(page, script)

        closed = asyncio.Event()
        browser.on("disconnected", lambda _: closed.set())
        print("JobBot ready - press Ctrl+Alt+G on a page. Close Brave to quit.", flush=True)
        await closed.wait()
        if hasattr(backend, "aclose"):
            await backend.aclose()
    print("Brave closed - JobBot stopped.", flush=True)
