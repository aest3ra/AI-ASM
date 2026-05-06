"""Build manifest candidate extraction."""

from __future__ import annotations

from ai_asm.analyzer.js_ast import extract_candidates as extract_js_candidates
from ai_asm.crawler.scope import Scope
from ai_asm.shared.candidate_store import CandidateEndpoint


def extract_candidates(
    body: str,
    *,
    base_url: str,
    scope: Scope,
) -> list[CandidateEndpoint]:
    # Phase 1 keeps manifest handling conservative: treat manifest payloads as
    # text assets and extract only explicit API-looking URLs.
    return extract_js_candidates(
        body,
        base_url=base_url,
        scope=scope,
        source_kind="static_manifest",
    )
