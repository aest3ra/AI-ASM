"""Event-driven static analyzer dispatcher with centralized throttles."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from orbis.analyzer import docs, html, inline, js_ast, manifest
from orbis.crawler.scope import Scope
from orbis.crawler.types import CapturedRequest
from orbis.shared.candidate_store import CandidateEndpoint, CandidateStore
from orbis.shared.decision_trace import DecisionTrace
from orbis.shared.response_store import ResponseStore


@dataclass(frozen=True)
class DispatcherLimits:
    js_max_bytes: int = 5 * 1024 * 1024
    json_max_bytes: int = 1024 * 1024
    html_max_bytes: int = 2 * 1024 * 1024
    per_analysis_timeout_sec: float = 5.0
    concurrency: int = 4
    queue_max: int = 100
    same_url_min_interval_sec: float = 5.0
    same_url_max_count: int = 3


@dataclass
class DispatchStats:
    accepted: int = 0
    deduped: int = 0
    candidates_added: int = 0
    rejected: Counter[str] = field(default_factory=Counter)


class AnalyzerDispatcher:
    def __init__(
        self,
        *,
        scope: Scope,
        candidates: CandidateStore,
        responses: ResponseStore,
        trace: DecisionTrace,
        limits: DispatcherLimits | None = None,
        on_reject: Callable[
            [str, CapturedRequest, str | None, dict | None],
            object,
        ] | None = None,
    ) -> None:
        self.scope = scope
        self.candidates = candidates
        self.responses = responses
        self.trace = trace
        self.limits = limits or DispatcherLimits()
        self.on_reject = on_reject
        self.stats = DispatchStats()
        self._semaphore = asyncio.Semaphore(self.limits.concurrency)
        self._seen: set[tuple[str, str]] = set()
        self._last_by_url: dict[str, float] = {}
        self._count_by_url: defaultdict[str, int] = defaultdict(int)

    async def dispatch_capture(
        self,
        cap: CapturedRequest,
        *,
        page_url: str | None = None,
    ) -> None:
        body = cap.response_body
        if not body:
            return
        if not self.scope.allows(cap.url):
            await self._reject("track1_scope", cap, page_url)
            return

        kind = _mime_kind(cap.response_mime)
        if kind is None:
            await self._reject("track1_mime", cap, page_url)
            return

        body_bytes = len(body.encode("utf-8", errors="ignore"))
        if body_bytes > self._max_bytes(kind):
            await self._reject("track1_size", cap, page_url, {"bytes": body_bytes})
            return

        url_key = _url_without_query(cap.url)
        body_hash = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
        dedup_key = (url_key, body_hash)
        if dedup_key in self._seen:
            self.stats.deduped += 1
            return

        now = time.monotonic()
        if self._count_by_url[url_key] >= self.limits.same_url_max_count:
            await self._reject("track1_rate_limit", cap, page_url)
            return
        last_seen = self._last_by_url.get(url_key)
        if (
            last_seen is not None
            and now - last_seen < self.limits.same_url_min_interval_sec
        ):
            await self._reject("track1_rate_limit", cap, page_url)
            return

        self._seen.add(dedup_key)
        self._last_by_url[url_key] = now
        self._count_by_url[url_key] += 1

        try:
            async with self._semaphore:
                found = await asyncio.wait_for(
                    self._analyze(cap, kind),
                    timeout=self.limits.per_analysis_timeout_sec,
                )
        except asyncio.TimeoutError:
            await self._reject("track1_timeout", cap, page_url)
            return
        except Exception as e:
            await self._reject(
                "track1_parse_failed", cap, page_url, {"error": type(e).__name__},
            )
            return

        self.stats.accepted += 1
        await self.trace.log_dispatch(
            page_url=page_url,
            accepted=True,
            reason="accepted",
            url=cap.url,
            payload={"mime": cap.response_mime, "kind": kind},
        )
        for candidate in found:
            added = await self.candidates.add(candidate)
            if added:
                self.stats.candidates_added += 1
                await self.trace.log_candidate_added(
                    page_url=page_url,
                    method=candidate.method,
                    url=candidate.url,
                    source_kind=candidate.source_kind,
                )

    async def dispatch_many(
        self,
        captures,
        *,
        page_url: str | None = None,
    ) -> None:
        queue: asyncio.Queue[CapturedRequest] = asyncio.Queue(
            maxsize=self.limits.queue_max,
        )

        async def worker() -> None:
            while True:
                cap = await queue.get()
                try:
                    await self.dispatch_capture(cap, page_url=page_url)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(self.limits.concurrency)
        ]
        try:
            for cap in captures:
                if not cap.response_body or cap.body_fetch_error:
                    continue
                try:
                    queue.put_nowait(cap)
                except asyncio.QueueFull:
                    await self._reject("track1_queue_full", cap, page_url)
            await queue.join()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _analyze(
        self,
        cap: CapturedRequest,
        kind: str,
    ) -> list[CandidateEndpoint]:
        await self.responses.observe(
            method=cap.method,
            url=cap.url,
            status=cap.response_status,
            mime=cap.response_mime,
            body=cap.response_body,
        )
        body = cap.response_body or ""
        if kind == "html":
            return [
                *html.extract_candidates(body, base_url=cap.url, scope=self.scope),
                *inline.extract_candidates(body, base_url=cap.url, scope=self.scope),
                *docs.extract_candidates(body, base_url=cap.url, scope=self.scope),
            ]
        if kind == "json":
            return [
                *js_ast.extract_candidates(
                    body,
                    base_url=cap.url,
                    scope=self.scope,
                    source_kind="static_inline",
                ),
            ]
        if _looks_manifest(cap.url):
            return manifest.extract_candidates(
                body,
                base_url=cap.url,
                scope=self.scope,
            )
        return js_ast.extract_candidates(body, base_url=cap.url, scope=self.scope)

    async def _reject(
        self,
        reason: str,
        cap: CapturedRequest,
        page_url: str | None,
        payload: dict | None = None,
    ) -> None:
        self.stats.rejected[reason] += 1
        await self.trace.log_dispatch(
            page_url=page_url,
            accepted=False,
            reason=reason,
            url=cap.url,
            payload=payload,
        )
        if self.on_reject is not None:
            result = self.on_reject(reason, cap, page_url, payload)
            if inspect.isawaitable(result):
                await result

    def _max_bytes(self, kind: str) -> int:
        if kind == "js":
            return self.limits.js_max_bytes
        if kind == "json":
            return self.limits.json_max_bytes
        return self.limits.html_max_bytes


def _mime_kind(mime: str | None) -> str | None:
    if not mime:
        return None
    lowered = mime.lower()
    if (
        lowered.startswith("image/")
        or lowered.startswith("font/")
        or lowered.startswith("audio/")
        or lowered.startswith("video/")
        or lowered == "text/css"
        or lowered == "application/octet-stream"
    ):
        return None
    if "javascript" in lowered or "ecmascript" in lowered:
        return "js"
    if "json" in lowered:
        return "json"
    if lowered in {"text/html", "application/xhtml+xml"}:
        return "html"
    return None


def _url_without_query(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def _looks_manifest(url: str) -> bool:
    lowered = url.lower()
    return "manifest" in lowered or "__build_manifest" in lowered
