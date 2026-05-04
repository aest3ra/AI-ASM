"""Extract crawl-worthy links from a loaded page."""

from __future__ import annotations

from playwright.async_api import Page


async def extract_links(page: Page) -> list[str]:
    """Return absolute hrefs from `<a>` elements (http/https only).

    Dedup and fragment handling are deferred to the runner: SPA hash routes
    (`#/foo`) need to be preserved here so the runner can decide based on its
    own normalization rules. Stripping fragments here would collapse every
    hash-routed view into the same URL.
    """
    hrefs = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll("a[href]"))
            .map(a => a.href)
        """
    )
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        if not h or not (h.startswith("http://") or h.startswith("https://")):
            continue
        if h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out
