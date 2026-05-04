"""BFS site crawler that orchestrates per-page captures."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from ai_asm.config import AuthConfig, LimitsConfig
from ai_asm.crawler.browser import capture_page
from ai_asm.crawler.interactions import trigger_interactions
from ai_asm.crawler.links import extract_links
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest, ScanDiagnostics
from ai_asm.normalizer.url import templatize_path

ProgressCallback = Callable[..., None]


def normalize_url(url: str) -> str:
    """Strip plain anchor fragments while preserving SPA hash routes.

    `#section1` is just a scroll target and never affects what the server sees,
    so it can be stripped. But many SPAs (older Angular, jQuery Mobile, …)
    encode the route in the hash, e.g. `#/login` or `#!/about` — those URLs
    must be kept distinct or the crawler will collapse every SPA view into
    one entry and miss the entire app.
    """
    parsed = urlparse(url)
    fragment = parsed.fragment
    if fragment.startswith("/") or fragment.startswith("!/"):
        return url
    if not fragment:
        return url
    return parsed._replace(fragment="").geturl()


def _template_key(url: str) -> tuple[str, str]:
    """Group key used by the per-template visit cap: (host, path_template)."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    # Hash-routed SPAs put the meaningful path in the fragment.
    if parsed.fragment.startswith("/") or parsed.fragment.startswith("!/"):
        hash_path = parsed.fragment.lstrip("!")  # `!/foo` -> `/foo`
        path = path.rstrip("/") + hash_path
    return parsed.hostname or "", templatize_path(path)


class Crawler:
    def __init__(
        self,
        *,
        auth: AuthConfig,
        scope: Scope,
        limits: LimitsConfig,
        headless: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.auth = auth
        self.scope = scope
        self.limits = limits
        self.headless = headless
        self.on_progress = on_progress

    async def crawl(
        self, start_url: str
    ) -> tuple[list[CapturedRequest], ScanDiagnostics]:
        all_captures: list[CapturedRequest] = []
        diag = ScanDiagnostics()
        seen: set[str] = set()
        queue: deque[str] = deque()

        deadline = time.monotonic() + self.limits.max_duration_sec
        rate_delay = 1.0 / self.limits.rate_limit_rps
        last_request_at = 0.0
        cap = self.limits.max_visits_per_template

        # Seed
        self._try_enqueue(normalize_url(start_url), queue, seen, diag, cap)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(**self._context_kwargs())

            try:
                while queue and diag.pages_crawled < self.limits.max_pages:
                    if time.monotonic() > deadline:
                        self._progress(
                            url=None, idx=diag.pages_crawled, qsize=len(queue),
                            total_reqs=len(all_captures),
                            error="duration limit reached",
                        )
                        break

                    url = queue.popleft()
                    # `seen` is populated at enqueue time, so a popped URL is
                    # always fresh and in scope — no re-check needed.

                    wait = (last_request_at + rate_delay) - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    last_request_at = time.monotonic()

                    try:
                        page_caps, page_links, page_diag = await capture_page(
                            context, url,
                            interact=trigger_interactions,
                            extract_links=extract_links,
                        )
                    except Exception as e:
                        diag.pages_failed += 1
                        self._progress(
                            url=url, idx=diag.pages_crawled, qsize=len(queue),
                            total_reqs=len(all_captures), error=type(e).__name__,
                        )
                        continue

                    diag.pages_crawled += 1
                    if page_diag.nav_error:
                        diag.pages_failed += 1
                    diag.body_fetch_failures += page_diag.body_fetch_failures
                    diag.buttons_clicked += page_diag.interactions.buttons_clicked
                    diag.buttons_skipped_danger += page_diag.interactions.buttons_skipped_danger
                    fs = page_diag.interactions.forms
                    diag.forms_submitted += fs.submitted
                    diag.forms_skipped_password += fs.skipped_password
                    diag.forms_skipped_other += fs.skipped_danger + fs.skipped_file

                    all_captures.extend(page_caps)

                    for link in page_links:
                        self._try_enqueue(
                            normalize_url(link), queue, seen, diag, cap,
                        )

                    self._progress(
                        url=url, idx=diag.pages_crawled, qsize=len(queue),
                        total_reqs=len(all_captures),
                        page_requests=len(page_caps),
                    )
            finally:
                await browser.close()

        return all_captures, diag

    def _try_enqueue(
        self,
        url: str,
        queue: deque[str],
        seen: set[str],
        diag: ScanDiagnostics,
        cap: int,
    ) -> None:
        """Add `url` to the queue unless out-of-scope, already seen, or its
        template has already hit the visit cap.

        Marks `url` as seen at enqueue time (not at dequeue time) so that the
        same URL discovered on multiple pages is enqueued at most once.
        """
        if url in seen or not self.scope.allows(url):
            return
        key = _template_key(url)
        diag.template_seen[key] += 1
        if diag.template_visits[key] >= cap:
            diag.links_skipped_template_cap += 1
            return
        queue.append(url)
        seen.add(url)
        diag.template_visits[key] += 1
        diag.links_enqueued += 1

    def _context_kwargs(self) -> dict:
        kw: dict = {}
        if (
            self.auth and self.auth.type == "storage_state"
            and self.auth.storage_state_path
        ):
            kw["storage_state"] = str(self.auth.storage_state_path)
        return kw

    def _progress(self, **kw) -> None:
        if self.on_progress:
            self.on_progress(**kw)
