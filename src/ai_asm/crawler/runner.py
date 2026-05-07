"""BFS site crawler that orchestrates per-page captures."""

from __future__ import annotations

import asyncio
import inspect
import signal
import time
from collections import deque
from typing import Any, Callable

from playwright.async_api import async_playwright

from ai_asm.agent.driver import run_agent_interactions
from ai_asm.agent.form_data import FormDataSet
from ai_asm.config import AgentConfig, AuthConfig, LimitsConfig
from ai_asm.crawler.browser import capture_page
from ai_asm.crawler.frontier import (
    FrontierManager,
    frontier_seen_key as _frontier_seen_key,
    has_out_of_scope_redirect_target as _has_out_of_scope_redirect_target,
    normalize_url,
    route_seen_key as _route_seen_key,
    template_key as _template_key,
)
from ai_asm.crawler.links import extract_links
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import (
    CapturedRequest,
    FrontierItem,
    ScanDiagnostics,
)

ProgressCallback = Callable[..., None]
PageCapturedCallback = Callable[
    [list[CapturedRequest], ScanDiagnostics],
    object,
]


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
        on_page_captured: PageCapturedCallback | None = None,
        decision_trace: Any | None = None,
        auth_state_path: str | None = None,
        agent: AgentConfig | None = None,
    ) -> None:
        self.auth = auth
        self.scope = scope
        self.limits = limits
        self.headless = headless
        self.on_progress = on_progress
        self.analyzer_dispatcher = analyzer_dispatcher
        self.clicked_interactions: set[str] = set()
        self.attempted_form_keys: set[str] = set()
        self.db_engine = db_engine
        self.scan_id = scan_id
        self.resume = resume
        self.on_page_captured = on_page_captured
        self.decision_trace = decision_trace
        self.auth_state_path = auth_state_path
        self.agent = agent or AgentConfig()
        self.form_data = FormDataSet.load(self.agent.form_data_path)
        self.frontier = FrontierManager(
            scope=scope,
            cap=limits.max_visits_per_template,
            db_engine=db_engine,
            scan_id=scan_id,
        )
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
            queue, seen = self.frontier.load_resume()
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
                    self.frontier.mark_in_progress(frontier_item)
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
                        self.frontier.complete(
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
                    await self._emit_page_captured(page_caps, diag)

                    for link in page_links:
                        self._try_enqueue(
                            normalize_url(link), queue, seen, diag, cap,
                        )

                    self.frontier.complete(
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
        self.frontier.try_enqueue(
            url,
            queue,
            seen,
            diag,
            dom_signature=dom_signature,
            replay_steps_json=replay_steps_json,
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

    async def _trigger_interactions(self, page, *, network_events=None):
        return await run_agent_interactions(
            page,
            scope=self.scope,
            clicked_keys=self.clicked_interactions,
            mode=self.agent.mode,
            model=self.agent.model,
            temperature=self.agent.temperature,
            max_steps=self.agent.max_steps_per_page,
            form_data=self.form_data,
            trace=self.decision_trace,
            db_engine=self.db_engine,
            scan_id=self.scan_id,
            auth_state_path=self.auth_state_path,
            attempted_form_keys=self.attempted_form_keys,
            network_events=network_events,
        )

    async def _emit_page_captured(
        self,
        page_caps: list[CapturedRequest],
        diag: ScanDiagnostics,
    ) -> None:
        if self.on_page_captured is None:
            return
        result = self.on_page_captured(page_caps, diag)
        if inspect.isawaitable(result):
            await result

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
