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


async def trigger_interactions(page: Page, *, max_clicks: int = 12) -> InteractionStats:
    """Scroll, click safe buttons, then auto-submit POST forms via fetch().

    POST forms run last so any `<form>` rendered by clicks is included.
    """
    stats = InteractionStats()
    await _scroll_full(page)
    await _click_safe_buttons(page, max_clicks, stats)
    stats.forms = await submit_post_forms(page)
    return stats


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
    page: Page, max_clicks: int, stats: InteractionStats
) -> None:
    selector = "button:visible, [role=button]:visible, [role=tab]:visible"
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
            await el.click(timeout=800, no_wait_after=True)
            stats.buttons_clicked += 1
            await asyncio.sleep(0.1)  # let triggered XHRs leave the wire
        except Exception:
            continue
