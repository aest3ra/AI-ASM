"""End-to-end scan orchestration outside of the CLI layer."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlmodel import Session, select

from ai_asm.agent.client import load_openai_api_key
from ai_asm.analyzer.dispatcher import AnalyzerDispatcher
from ai_asm.config import ScanConfig
from ai_asm.crawler.probe import (
    learn_static_probe_auth_profiles,
    probe_static_get_with_auth_context,
)
from ai_asm.crawler.runner import Crawler
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest, ScanDiagnostics
from ai_asm.normalizer import (
    ApiCandidate,
    NormalizedEndpoint,
    discover_api_candidates,
    normalize,
)
from ai_asm.normalizer.pipeline import canonical_api_path, is_api_capture
from ai_asm.normalizer.url import templatize_path
from ai_asm.output.artifacts import write_capture_artifact
from ai_asm.output.request_log import write_request_log
from ai_asm.schema.inferrer import infer_schema_from_json_bodies
from ai_asm.shared.candidate_store import CandidateStore
from ai_asm.shared.decision_trace import DecisionTrace
from ai_asm.shared.response_store import ResponseStore
from ai_asm.storage.db import FlaggedItem, FrontierState, Scan, open_db
from ai_asm.storage.repo import (
    EndpointSaveStats,
    record_flagged_item,
    save_endpoints,
    save_scan_summary,
    save_static_candidates,
    save_url_surfaces,
)
from ai_asm.surface import (
    UrlSurfaceRecord,
    discover_url_surfaces,
    surfaces_from_static_candidates,
)


@dataclass
class ScanRunResult:
    target: str
    scan_id: int
    db_path: Path
    raw_path: Path
    request_log_path: Path
    trace_path: Path
    captured: list[CapturedRequest]
    scoped_captured: list[CapturedRequest]
    endpoints: list[NormalizedEndpoint]
    static_candidates: list[ApiCandidate]
    probed_urls: set[str]
    probe_errors: dict[str, str]
    url_surfaces: list[UrlSurfaceRecord]
    diagnostics: ScanDiagnostics
    dispatcher: AnalyzerDispatcher
    pending_candidates: int
    elapsed: float
    excluded_count: int


class ScanOrchestrator:
    def __init__(
        self,
        *,
        config: ScanConfig,
        db_path: Path,
        out_dir: Path,
        headless: bool,
        auth_label: str | None = None,
        resume: bool = False,
        progress=None,
    ) -> None:
        self.config = config
        self.scope = Scope(config.scope)
        self.target = str(config.target)
        self.db_path = db_path
        self.out_dir = out_dir
        self.headless = headless
        self.auth_label = auth_label
        self.resume = resume
        self.progress = progress
        self.auth_state_path = auth_state_path(config)
        self.endpoint_stats = EndpointSaveStats()

    def run(self) -> ScanRunResult:
        if not self.scope.allows(self.target):
            raise ValueError(
                f"target {self.target} is outside scope {self.config.scope.include_domains}",
            )
        if self.config.agent.mode == "llm" and not load_openai_api_key():
            raise RuntimeError(
                "OPENAI_API_KEY is required for agent.mode='llm'. "
                "Set it in the environment or .env.",
            )

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = open_db(self.db_path)
        scan_id = self._open_scan(engine)

        candidate_store = CandidateStore()
        response_store = ResponseStore()
        trace_path = self.out_dir / f"trace-{scan_id}.jsonl"
        decision_trace = DecisionTrace(
            scan_id=scan_id,
            path=trace_path,
            reset=not self.resume,
        )
        dispatcher = AnalyzerDispatcher(
            scope=self.scope,
            candidates=candidate_store,
            responses=response_store,
            trace=decision_trace,
            on_reject=self._record_dispatch_rejection(engine, scan_id),
        )

        crawler = Crawler(
            auth=self.config.auth,
            scope=self.scope,
            limits=self.config.limits,
            headless=self.headless,
            on_progress=self.progress,
            analyzer_dispatcher=dispatcher,
            db_engine=engine,
            scan_id=scan_id,
            resume=self.resume,
            on_page_captured=lambda caps, diag: self._checkpoint_page(
                engine, scan_id, candidate_store, caps, diag,
            ),
            decision_trace=decision_trace,
            auth_state_path=self.auth_state_path,
            agent=self.config.agent,
        )

        started = time.monotonic()
        captured, scan_diag = asyncio.run(crawler.crawl(self.target))

        scoped_captured = [c for c in captured if self.scope.allows(c.url)]
        static_candidates = discover_api_candidates(scoped_captured, self.scope)
        probed_urls: set[str] = set()
        probe_errors: dict[str, str] = {}

        if static_candidates:
            auth_profiles = learn_static_probe_auth_profiles(
                scoped_captured,
                mode=self.config.static_probe_auth,
                scope=self.scope,
            )
            probe_caps, probed_urls, probe_errors = asyncio.run(
                probe_static_get_with_auth_context(
                    self.config.auth,
                    static_candidates,
                    headless=self.headless,
                    auth_mode=self.config.static_probe_auth,
                    auth_profiles=auth_profiles,
                )
            )
            captured.extend(probe_caps)
            scan_diag.static_gets_probed += len(probed_urls)
            scan_diag.static_get_probe_failures += len(probe_errors)
            scan_diag.static_probe_auth_profiles += len(auth_profiles)
            scan_diag.static_probe_auth_headers_applied += sum(
                1 for cap in probe_caps
                if _has_header(cap.request_headers, "authorization")
            )
            asyncio.run(self._save_captures(
                engine,
                scan_id,
                candidate_store,
                probe_caps,
            ))
            scoped_captured = [c for c in captured if self.scope.allows(c.url)]
            static_candidates = discover_api_candidates(scoped_captured, self.scope)

        elapsed = time.monotonic() - started
        endpoints = normalize(scoped_captured, api_only=True)
        url_surfaces = discover_url_surfaces(scoped_captured, self.scope)
        url_surfaces.extend(surfaces_from_static_candidates(static_candidates))
        asyncio.run(_reconcile_candidates(candidate_store, endpoints))
        pending_candidates = asyncio.run(candidate_store.pending_count())

        self._finish_scan(
            engine,
            scan_id,
            scan_diag,
            endpoints,
            static_candidates,
            url_surfaces,
            probed_urls,
            probe_errors,
            elapsed,
        )

        raw_path = self.out_dir / f"scan-{scan_id}-{int(time.time())}.json"
        write_capture_artifact(raw_path, captured)
        request_log_path = self.out_dir / f"requests-{scan_id}.jsonl"
        write_request_log(request_log_path, captured, scan_id=scan_id)

        return ScanRunResult(
            target=self.target,
            scan_id=scan_id,
            db_path=self.db_path,
            raw_path=raw_path,
            request_log_path=request_log_path,
            trace_path=trace_path,
            captured=captured,
            scoped_captured=scoped_captured,
            endpoints=endpoints,
            static_candidates=static_candidates,
            probed_urls=probed_urls,
            probe_errors=probe_errors,
            url_surfaces=url_surfaces,
            diagnostics=scan_diag,
            dispatcher=dispatcher,
            pending_candidates=pending_candidates,
            elapsed=elapsed,
            excluded_count=len(captured) - len(scoped_captured),
        )

    def _open_scan(self, engine) -> int:
        with Session(engine) as session:
            if self.resume:
                scan_row = find_resume_scan(
                    session,
                    self.target,
                    self.auth_state_path,
                )
                if scan_row is None:
                    raise RuntimeError(
                        f"no resumable scan for {self.target} "
                        f"auth={self.auth_state_path or 'none'} in {self.db_path}",
                    )
            else:
                scan_row = Scan(
                    target=self.target,
                    auth_state_path=self.auth_state_path,
                )
                session.add(scan_row)
                session.commit()
                session.refresh(scan_row)
            scan_id = scan_row.id
        assert scan_id is not None
        return scan_id

    def _record_dispatch_rejection(self, engine, scan_id: int):
        def record(reason, cap, page_url, payload) -> None:
            with Session(engine) as session:
                record_flagged_item(
                    session,
                    scan_id=scan_id,
                    flag_kind=reason,
                    item_kind="url",
                    url=cap.url,
                    method=cap.method,
                    description=f"Track 1 rejected {cap.url}: {reason}",
                    page_url=page_url,
                    context_json=payload,
                    auth_state_path=self.auth_state_path,
                )

        return record

    async def _checkpoint_page(
        self,
        engine,
        scan_id: int,
        candidate_store: CandidateStore,
        page_caps: list[CapturedRequest],
        diag: ScanDiagnostics,
    ) -> None:
        await self._save_captures(engine, scan_id, candidate_store, page_caps)
        with Session(engine) as session:
            scan_row = session.get(Scan, scan_id)
            if scan_row is not None:
                scan_row.pages_crawled = diag.pages_crawled
                session.commit()

    async def _save_captures(
        self,
        engine,
        scan_id: int,
        candidate_store: CandidateStore,
        captures: list[CapturedRequest],
    ) -> None:
        scoped = [cap for cap in captures if self.scope.allows(cap.url)]
        by_provenance: dict[str, list[CapturedRequest]] = defaultdict(list)
        for cap in scoped:
            by_provenance[_provenance_for_source(cap.source)].append(cap)

        for provenance, group in by_provenance.items():
            endpoints = normalize(group, api_only=True)
            if not endpoints:
                continue
            response_schemas = _response_schemas_for_captures(group)
            await _reconcile_candidates(candidate_store, endpoints)
            with Session(engine) as session:
                stats = save_endpoints(
                    session,
                    scan_id,
                    endpoints,
                    auth_state_path=self.auth_state_path,
                    auth_label=self.auth_label,
                    provenance=provenance,
                    response_schemas=response_schemas,
                )
            self.endpoint_stats = EndpointSaveStats(
                endpoints_added=self.endpoint_stats.endpoints_added + stats.endpoints_added,
                endpoints_updated=(
                    self.endpoint_stats.endpoints_updated + stats.endpoints_updated
                ),
            )

    def _finish_scan(
        self,
        engine,
        scan_id: int,
        diag: ScanDiagnostics,
        endpoints: list[NormalizedEndpoint],
        static_candidates: list[ApiCandidate],
        url_surfaces: list[UrlSurfaceRecord],
        probed_urls: set[str],
        probe_errors: dict[str, str],
        elapsed: float,
    ) -> None:
        with Session(engine) as session:
            scan_row = session.get(Scan, scan_id)
            if scan_row is None:
                raise RuntimeError(f"scan id {scan_id} disappeared from {self.db_path}")
            scan_row.pages_crawled = diag.pages_crawled
            scan_row.endpoints_found = len(endpoints)
            scan_row.finished_at = datetime.utcnow()
            session.commit()

            save_static_candidates(
                session,
                scan_id,
                static_candidates,
                {(e.host, e.path_template) for e in endpoints},
                probed_urls=probed_urls,
                probe_errors=probe_errors,
            )
            save_url_surfaces(session, scan_id, url_surfaces)
            flagged_count = len(session.exec(
                select(FlaggedItem).where(FlaggedItem.scan_id == scan_id)
            ).all())
            save_scan_summary(
                session,
                scan_id=scan_id,
                auth_state_path=self.auth_state_path,
                pages_visited=diag.pages_crawled,
                pages_failed=diag.pages_failed,
                endpoints_added=self.endpoint_stats.endpoints_added,
                endpoints_updated=self.endpoint_stats.endpoints_updated,
                flagged_count=flagged_count,
                elapsed_seconds=elapsed,
            )


def find_resume_scan(
    session: Session,
    target: str,
    auth_state_path_value: str | None,
) -> Scan | None:
    scans = session.exec(
        select(Scan)
        .where(
            Scan.target == target,
            Scan.auth_state_path == auth_state_path_value,
        )
        .order_by(Scan.id.desc())
    ).all()
    for scan in scans:
        pending = session.exec(
            select(FrontierState.id).where(
                FrontierState.scan_id == scan.id,
                FrontierState.status.in_(["pending", "in_progress"]),
            )
        ).first()
        if pending is not None:
            return scan
    return None


def auth_state_path(config: ScanConfig) -> str | None:
    if config.auth.type == "storage_state" and config.auth.storage_state_path:
        return str(config.auth.storage_state_path)
    return None


async def _reconcile_candidates(
    candidate_store: CandidateStore,
    endpoints: list[NormalizedEndpoint],
) -> None:
    for endpoint in endpoints:
        await candidate_store.mark_verified(
            endpoint.method,
            endpoint.host,
            endpoint.path_template,
        )


def _provenance_for_source(source: str) -> str:
    if source == "static_probe":
        return "static_probe"
    if source == "init_script":
        return "init_script"
    return "cdp_capture"


def _has_header(headers: dict[str, str], name: str) -> bool:
    wanted = name.lower()
    return any(key.lower() == wanted for key in headers)


def _response_schemas_for_captures(
    captures: list[CapturedRequest],
) -> dict[tuple[str, str, str], dict]:
    bodies_by_endpoint: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for cap in captures:
        if not is_api_capture(cap):
            continue
        if not cap.response_body or cap.response_body_truncated or cap.body_fetch_error:
            continue
        if not _looks_like_json_body(cap):
            continue
        parsed = urlparse(cap.url)
        host = parsed.hostname or ""
        path = canonical_api_path(parsed.path or "/")
        key = (cap.method, host, templatize_path(path))
        bodies_by_endpoint[key].append(cap.response_body)

    schemas: dict[tuple[str, str, str], dict] = {}
    for key, bodies in bodies_by_endpoint.items():
        schema = infer_schema_from_json_bodies(bodies)
        if schema:
            schemas[key] = schema
    return schemas


def _looks_like_json_body(cap: CapturedRequest) -> bool:
    mime = (cap.response_mime or "").lower()
    if "json" in mime:
        return True
    body = cap.response_body or ""
    return body.lstrip().startswith(("{", "["))
