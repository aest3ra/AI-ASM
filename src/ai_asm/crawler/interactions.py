"""Best-effort UI interactions to surface lazy-loaded XHRs and SPA routes.

Safety first: we filter out buttons whose text suggests destructive actions
(delete, logout, payment, etc.). MVP only handles scroll + button clicks;
form submission is deferred until we have safer dummy-fill heuristics.
"""

from __future__ import annotations

import asyncio
import re

from playwright.async_api import Page

from ai_asm.crawler.forms import submit_post_forms
from ai_asm.crawler.types import InteractionStats

DANGER_PATTERN = re.compile(
    r"(?:delete|remove|logout|sign[\- ]?out|purchase|buy|"
    r"cancel|unsubscribe|withdraw|"
    r"삭제|탈퇴|로그아웃|결제|취소|회원\s*탈퇴|환불)",
    re.IGNORECASE,
)


def is_dangerous(text: str) -> bool:
    """True if the button label looks like it triggers a destructive action."""
    return bool(DANGER_PATTERN.search(text or ""))


async def trigger_interactions(
    page: Page,
    *,
    max_clicks: int = 12,
    clicked_keys: set[str] | None = None,
) -> InteractionStats:
    """Scroll, click safe buttons, then auto-submit POST forms via fetch().

    POST forms run last so any `<form>` rendered by clicks is included.
    """
    stats = InteractionStats()
    await _install_route_recorder(page)
    await _scroll_full(page)
    await _click_safe_buttons(page, max_clicks, stats, clicked_keys=clicked_keys)
    await _collect_recorded_routes(page, stats)
    stats.forms = await submit_post_forms(page)
    return stats


async def _install_route_recorder(page: Page) -> None:
    try:
        await page.evaluate(
            """
            () => {
                if (window.__aiAsmRouteRecorderInstalled) return;
                window.__aiAsmRouteRecorderInstalled = true;
                window.__aiAsmRoutes = window.__aiAsmRoutes || [];
                const record = () => window.__aiAsmRoutes.push(window.location.href);
                const push = history.pushState;
                const replace = history.replaceState;
                history.pushState = function(...args) {
                    const ret = push.apply(this, args);
                    record();
                    return ret;
                };
                history.replaceState = function(...args) {
                    const ret = replace.apply(this, args);
                    record();
                    return ret;
                };
                window.addEventListener("popstate", record);
                window.addEventListener("hashchange", record);
            }
            """
        )
    except Exception:
        pass


async def _collect_recorded_routes(page: Page, stats: InteractionStats) -> None:
    try:
        routes = await page.evaluate("() => window.__aiAsmRoutes || []")
    except Exception:
        return
    for route in routes:
        _add_discovered_url(stats, route)


async def _scroll_full(page: Page) -> None:
    try:
        await page.evaluate(
            """
            async () => {
                const step = 600;
                const total = Math.max(
                    document.body.scrollHeight,
                    document.documentElement.scrollHeight,
                );
                for (let y = 0; y < total; y += step) {
                    window.scrollTo(0, y);
                    await new Promise(r => setTimeout(r, 80));
                }
                window.scrollTo(0, 0);
            }
            """
        )
    except Exception:
        pass


async def _click_safe_buttons(
    page: Page,
    max_clicks: int,
    stats: InteractionStats,
    *,
    clicked_keys: set[str] | None = None,
) -> None:
    selector = (
        "button:visible, a[routerlink]:visible, a[ng-reflect-router-link]:visible, "
        "[role=button]:visible, [role=tab]:visible, [role=menuitem]:visible, "
        ".mat-mdc-menu-item:visible, .mat-mdc-list-item:visible"
    )
    try:
        elements = await page.locator(selector).all()
    except Exception:
        return
    stats.buttons_seen = len(elements)

    for el in elements:
        if stats.buttons_clicked >= max_clicks:
            return
        try:
            text = (await el.inner_text(timeout=300)).strip()
        except Exception:
            continue
        if not text:
            continue
        if is_dangerous(text):
            stats.buttons_skipped_danger += 1
            continue
        try:
            href = await el.get_attribute("href", timeout=200)
        except Exception:
            href = None
        try:
            role = await el.get_attribute("role", timeout=200)
        except Exception:
            role = None
        key = interaction_key(text, href, role)
        if clicked_keys is not None and key in clicked_keys:
            continue
        try:
            before = page.url
            await el.click(timeout=800, no_wait_after=True)
            stats.buttons_clicked += 1
            if clicked_keys is not None:
                clicked_keys.add(key)
            await asyncio.sleep(0.3)  # let triggered XHRs and SPA routes settle
            after = page.url
            if after != before:
                _add_discovered_url(stats, after)
        except Exception:
            continue


def _add_discovered_url(stats: InteractionStats, url: str | None) -> None:
    if not url or url in stats.discovered_urls:
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        return
    stats.discovered_urls.append(url)


def interaction_key(text: str, href: str | None, role: str | None) -> str:
    label = " ".join((text or "").strip().lower().split())
    target = (href or "").strip()
    kind = (role or "button").strip().lower() or "button"
    return f"{kind}|{label}|{target}"
