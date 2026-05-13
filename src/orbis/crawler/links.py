"""Extract crawl-worthy links from a loaded page."""

from __future__ import annotations

from playwright.async_api import Page


async def extract_links(page: Page) -> list[str]:
    """Return absolute crawl-worthy links from common DOM route hints.

    Dedup and fragment handling are deferred to the runner: SPA hash routes
    (`#/foo`) need to be preserved here so the runner can decide based on its
    own normalization rules. Stripping fragments here would collapse every
    hash-routed view into the same URL.
    """
    hrefs = await page.evaluate(
        """
        () => {
            const urls = [];
            const push = value => {
                if (!value) return;
                try {
                    if (value.startsWith("#/") || value.startsWith("#!/")) {
                        urls.push(window.location.origin + "/" + value);
                        return;
                    }
                    urls.push(new URL(value, window.location.href).href);
                } catch (_) {}
            };

            for (const a of document.querySelectorAll("a[href]")) {
                push(a.getAttribute("href"));
            }
            for (const el of document.querySelectorAll(
                "[routerlink], [ng-reflect-router-link], [data-router-link]"
            )) {
                push(
                    el.getAttribute("routerlink") ||
                    el.getAttribute("ng-reflect-router-link") ||
                    el.getAttribute("data-router-link")
                );
            }
            return urls;
        }
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
