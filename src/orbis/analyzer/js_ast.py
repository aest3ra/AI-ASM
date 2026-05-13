"""JavaScript API extraction.

Phase 1 intentionally keeps this regex-based. Phase 7 can replace the internals
with tree-sitter while preserving this module boundary.
"""

from __future__ import annotations

from orbis.analyzer.common import (
    candidate_from_url,
    dedupe_candidates,
    iter_static_endpoint_refs,
)
from orbis.crawler.scope import Scope
from orbis.shared.candidate_store import CandidateEndpoint


def extract_candidates(
    body: str,
    *,
    base_url: str,
    scope: Scope,
    source_kind: str = "static_js",
) -> list[CandidateEndpoint]:
    candidates: list[CandidateEndpoint] = []
    for ref in iter_static_endpoint_refs(body):
        candidate = candidate_from_url(
            ref.raw_url,
            method=ref.method,
            base_url=base_url,
            scope=scope,
            source_kind=source_kind,
        )
        if candidate:
            candidates.append(candidate)

    return dedupe_candidates(candidates)
