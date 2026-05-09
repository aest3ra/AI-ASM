"""Inline script/data extraction."""

from __future__ import annotations

import re

from ai_asm.analyzer.common import contains_api_marker
from ai_asm.analyzer.js_ast import extract_candidates as extract_js_candidates
from ai_asm.crawler.scope import Scope
from ai_asm.shared.candidate_store import CandidateEndpoint

_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def extract_candidates(
    html_body: str,
    *,
    base_url: str,
    scope: Scope,
) -> list[CandidateEndpoint]:
    candidates: list[CandidateEndpoint] = []
    for match in _SCRIPT_RE.finditer(html_body):
        script_body = match.group("body")
        if not contains_api_marker(script_body):
            continue
        candidates.extend(extract_js_candidates(
            script_body,
            base_url=base_url,
            scope=scope,
            source_kind="static_inline",
        ))
    return candidates
