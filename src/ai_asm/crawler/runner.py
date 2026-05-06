"""BFS site crawler that orchestrates per-page captures."""

from __future__ import annotations

import asyncio
import signal
import time
from collections import deque
from typing import Any, Callable
from urllib.parse import parse_qsl, urljoin, urlparse

from playwright.async_api import async_playwright
from sqlmodel import Session, select

from ai_asm.config import AuthConfig, LimitsConfig, StaticProbeAuthMode
from ai_asm.crawler.browser import capture_page
from ai_asm.crawler.interactions import trigger_interactions
from ai_asm.crawler.links import extract_links
from ai_asm.crawler.probe import probe_static_get_candidates
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import (
    CapturedRequest,
    FrontierItem,
    ScanDiagnostics,
    StaticProbeAuthProfile,
)
from ai_asm.normalizer.static import ApiCandidate
from ai_asm.normalizer.url import templatize_path
from ai_asm.storage.db import FrontierState
from ai_asm.storage.repo import (
    complete_frontier_item,
    load_pending_frontier,
    update_frontier_status,
    upsert_frontier_item,
)

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
        if parsed.path not in ("", "/") and "." not in parsed.path.rsplit("/", 1)[-1]:
            return parsed._replace(path="/").geturl()
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


def _route_seen_key(url: str) -> tuple[str, str, str, str]:
    """Dedup key for browser states.

    Hash-routed SPAs often expose both `/#/login` and `/login` for the same UI
    state. Treat them as one frontier item while preserving query differences.
    """
    parsed = urlparse(url)
    route_path = parsed.path or "/"
    route_query = parsed.query
    fragment = parsed.fragment
    if fragment.startswith("/") or fragment.startswith("!/"):
        frag = fragment.lstrip("!")
        frag_parsed = urlparse(frag)
        route_path = frag_parsed.path or "/"
        route_query = frag_parsed.query
    return (
        parsed.scheme,
        parsed.hostname or "",
        route_path.rstrip("/") or "/",
        route_query,
    )


def _frontier_seen_key(item: FrontierItem) -> tuple[str, str, str, str, str | None]:
    return (*_route_seen_key(item.url), item.dom_signature)


def _has_out_of_scope_redirect_target(url: str, scope: Scope) -> bool:
    parsed = urlparse(url)
    redirect_param_names = {
        "to", "url", "target", "redirect", "redirect_uri", "return", "returnto",
        "next", "continue",
    }
    for name, value in parse_qsl(parsed.query, keep_blank_values=False):
        if name.lower() not in redirect_param_names:
            continue
        target = urljoin(url, value)
        target_parsed = urlparse(target)
        if target_parsed.scheme in {"http", "https"} and not scope.allows(target):
            return True
    return False


class Crawler:
    def __init__(
        self,
        *,
        auth: AuthConfig,
        scope: Scope,
        limits: LimitsConfig,
        headless: bool = True,
        on_progress: ProgressCallback | None = None,
        analyzer_dispatcher: Any | None = None,
        db_engine: Any | None = None,
        scan_id: int | None = None,
        resume: bool = False,
    ) -> None:
        self.auth = auth
        self.scope = scope
        self.limits = limits
        self.headless = headless
        self.on_progress = on_progress
        self.analyzer_dispatcher = analyzer_dispatcher
        self.clicked_interactions: set[str] = set()
        self.db_engine = db_engine
        self.scan_id = scan_id
        self.resume = resume
        self._stop_requested = False

    async def crawl(
        self, start_url: str
    ) -> tuple[list[CapturedRequest], ScanDiagnostics]:
        all_captures: list[CapturedRequest] = []
        diag = ScanDiagnostics()
        seen: set = set()
        queue: deque[FrontierItem] = deque()

        deadline = time.monotonic() + self.limits.max_duration_sec
        rate_delay = 1.0 / self.limits.rate_limit_rps
        last_request_at = 0.0
        cap = self.limits.max_visits_per_template

        if self.resume:
            queue, seen = self._load_resume_frontier()
        if not queue:
            self._try_enqueue(normalize_url(start_url), queue, seen, diag, cap)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(**self._context_kwargs())
            restore_sigint = self._install_sigint_handler()

            try:
                while (
                    queue
                    and diag.pages_crawled < self.limits.max_pages
                    and not self._stop_requested
                ):
                    if time.monotonic() > deadline:
                        self._progress(
                            url=None, idx=diag.pages_crawled, qsize=len(queue),
                            total_reqs=len(all_captures),
                            error="duration limit reached",
                        )
                        break

                    frontier_item = queue.popleft()
                    url = frontier_item.url
                    self._mark_frontier_in_progress(frontier_item)
                    # `seen` is populated at enqueue time, so a popped URL is
                    # always fresh and in scope — no re-check needed.

                    wait = (last_request_at + rate_delay) - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    last_request_at = time.monotonic()

                    try:
                        page_caps, page_links, page_diag = await capture_page(
                            context, url,
                            interact=self._trigger_interactions,
                            extract_links=extract_links,
                            analyzer_dispatcher=self.analyzer_dispatcher,
                            request_scope=self.scope,
                        )
                    except Exception as e:
                        diag.pages_failed += 1
                        self._complete_frontier(
                            frontier_item,
                            status="failed",
                            dom_signature=None,
                        )
                        self._progress(
                            url=url, idx=diag.pages_crawled, qsize=len(queue),
                            total_reqs=len(all_captures), error=type(e).__name__,
                        )
                        continue

                    diag.pages_crawled += 1
                    if page_diag.nav_error:
                        diag.pages_failed += 1
                    if page_diag.dom_signature:
                        diag.dom_signatures_seen.add(page_diag.dom_signature)
                        frontier_item.dom_signature = page_diag.dom_signature
                        seen.add(_frontier_seen_key(frontier_item))
                    diag.body_fetch_failures += page_diag.body_fetch_failures
                    diag.init_script_requests_recorded += (
                        page_diag.init_script_requests_recorded
                    )
                    diag.init_script_requests_added += page_diag.init_script_requests_added
                    diag.out_of_scope_requests_aborted += (
                        page_diag.out_of_scope_requests_aborted
                    )
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

                    self._complete_frontier(
                        frontier_item,
                        status="failed" if page_diag.nav_error else "done",
                        dom_signature=page_diag.dom_signature,
                    )

                    self._progress(
                        url=url, idx=diag.pages_crawled, qsize=len(queue),
                        total_reqs=len(all_captures),
                        page_requests=len(page_caps),
                    )

            finally:
                restore_sigint()
                await browser.close()

        return all_captures, diag

    async def probe_static_get(
        self, candidates: list[ApiCandidate],
        *,
        auth_mode: StaticProbeAuthMode = "cookie-only",
        auth_profiles: dict[str, StaticProbeAuthProfile] | None = None,
    ) -> tuple[list[CapturedRequest], set[str], dict[str, str]]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context_kwargs = {} if auth_mode == "none" else self._context_kwargs()
            context = await browser.new_context(**context_kwargs)
            try:
                return await probe_static_get_candidates(
                    context,
                    candidates,
                    auth_mode=auth_mode,
                    auth_profiles=auth_profiles,
                )
            finally:
                await browser.close()

    def _try_enqueue(
        self,
        url: str,
        queue: deque[FrontierItem],
        seen: set,
        diag: ScanDiagnostics,
        cap: int,
        dom_signature: str | None = None,
        replay_steps_json: str | None = None,
    ) -> None:
        """Add `url` to the queue unless out-of-scope, already seen, or its
        template has already hit the visit cap.

        Marks `url` as seen at enqueue time (not at dequeue time) so that the
        same URL discovered on multiple pages is enqueued at most once.
        """
        item = FrontierItem(
            url=url,
            dom_signature=dom_signature,
            replay_steps_json=replay_steps_json,
        )
        route_key = _frontier_seen_key(item)
        if route_key in seen or not self.scope.allows(url):
            return
        if _has_out_of_scope_redirect_target(url, self.scope):
            diag.links_skipped_external_redirect += 1
            return
        key = _template_key(url)
        diag.template_seen[key] += 1
        if diag.template_visits[key] >= cap:
            diag.links_skipped_template_cap += 1
            return
        self._persist_frontier_enqueue(item)
        queue.append(item)
        seen.add(route_key)
        diag.template_visits[key] += 1
        diag.links_enqueued += 1

    def _load_resume_frontier(self) -> tuple[deque[FrontierItem], set]:
        queue: deque[FrontierItem] = deque()
        seen: set = set()
        if self.db_engine is None or self.scan_id is None:
            return queue, seen
        with Session(self.db_engine) as session:
            for row in session.exec(
                select(FrontierState).where(FrontierState.scan_id == self.scan_id)
            ).all():
                item = FrontierItem(
                    url=row.url,
                    dom_signature=row.dom_signature,
                    replay_steps_json=row.replay_steps_json,
                    db_id=row.id,
                )
                seen.add(_frontier_seen_key(item))
                seen.add(_frontier_seen_key(FrontierItem(row.url)))
            for row in load_pending_frontier(session, self.scan_id):
                queue.append(FrontierItem(
                    url=row.url,
                    dom_signature=row.dom_signature,
                    replay_steps_json=row.replay_steps_json,
                    db_id=row.id,
                ))
        return queue, seen

    def _persist_frontier_enqueue(self, item: FrontierItem) -> None:
        if self.db_engine is None or self.scan_id is None:
            return
        with Session(self.db_engine) as session:
            row = upsert_frontier_item(
                session,
                scan_id=self.scan_id,
                url=item.url,
                dom_signature=item.dom_signature,
                replay_steps_json=item.replay_steps_json,
                status="pending",
            )
            item.db_id = row.id

    def _mark_frontier_in_progress(self, item: FrontierItem) -> None:
        if self.db_engine is None or item.db_id is None:
            return
        with Session(self.db_engine) as session:
            update_frontier_status(session, item.db_id, "in_progress")

    def _complete_frontier(
        self,
        item: FrontierItem,
        *,
        status: str,
        dom_signature: str | None,
    ) -> None:
        if self.db_engine is None or item.db_id is None:
            return
        with Session(self.db_engine) as session:
            complete_frontier_item(
                session,
                item.db_id,
                status=status,
                dom_signature=dom_signature,
            )

    def _install_sigint_handler(self):
        previous = signal.getsignal(signal.SIGINT)

        def handler(signum, frame):
            if self._stop_requested:
                raise KeyboardInterrupt
            self._stop_requested = True

        try:
            signal.signal(signal.SIGINT, handler)
        except ValueError:
            return lambda: None

        def restore() -> None:
            signal.signal(signal.SIGINT, previous)

        return restore

    async def _trigger_interactions(self, page):
        return await trigger_interactions(
            page,
            clicked_keys=self.clicked_interactions,
        )

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
