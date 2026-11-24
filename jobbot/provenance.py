"""Where a job was found: follows tabs back through the tab that opened them and each tab's own history.

Portal = the first URL on that trail whose site is listed in profile.md `## Portals` (current page first,
then older pages, then the opener tab); otherwise "Company site". Search engines are never listed, so a
Google result never counts.
"""

import asyncio
from urllib.parse import urlsplit

from playwright.async_api import Page

COMPANY_SITE = "Company site"
MAX_HISTORY = 40


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def match_portal(url: str, portals: dict[str, str]) -> str | None:
    host = host_of(url)
    if not host:
        return None
    for domain, name in portals.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


class Provenance:
    def __init__(self):
        self.history: dict[Page, list[str]] = {}
        self.opener: dict[Page, tuple[Page, list[str]]] = {}   # popup -> (opener, opener's trail at that moment)

    def track(self, page: Page) -> None:
        if page in self.history:
            return
        self.history[page] = [page.url] if page.url and page.url != "about:blank" else []
        page.on("framenavigated", lambda frame: self._navigated(page, frame))
        page.on("popup", lambda popup: self._popup(page, popup))
        page.on("close", lambda _: (self.history.pop(page, None), self.opener.pop(page, None)))

    def _navigated(self, page: Page, frame) -> None:
        if frame is not page.main_frame or not frame.url or frame.url == "about:blank":
            return
        h = self.history.setdefault(page, [])
        if not h or h[-1] != frame.url:
            h.append(frame.url)
            del h[:-MAX_HISTORY]

    def _popup(self, page: Page, popup: Page) -> None:
        self.opener[popup] = (page, self.trail(page))
        self.track(popup)

    def trail(self, page: Page) -> list[str]:
        """URLs to check, most relevant first: this tab now, its earlier pages, then its opener's trail."""
        own = list(reversed(self.history.get(page, [])))
        if page.url and (not own or own[0] != page.url):
            own.insert(0, page.url)
        if page in self.opener:
            own += self.opener[page][1]
        return own

    async def portal(self, page: Page, portals: dict[str, str]) -> str:
        urls = self.trail(page)
        try:
            ref = await asyncio.wait_for(page.evaluate("document.referrer"), 3)
            if ref:
                urls.append(ref)
        except Exception:
            pass
        for url in urls:
            if name := match_portal(url, portals):
                return name
        return COMPANY_SITE
