"""HTML candidate extraction for links, forms, and preload references."""

from __future__ import annotations

from html.parser import HTMLParser

from ai_asm.analyzer.common import candidate_from_url, dedupe_candidates
from ai_asm.crawler.scope import Scope
from ai_asm.shared.candidate_store import CandidateEndpoint


class _CandidateHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value or "" for name, value in attrs}
        if tag == "form":
            self.refs.append((attr.get("method", "GET"), attr.get("action", "")))
        elif tag == "a":
            self.refs.append(("GET", attr.get("href", "")))
        elif tag == "link":
            self.refs.append(("GET", attr.get("href", "")))


def extract_candidates(
    body: str,
    *,
    base_url: str,
    scope: Scope,
    source_kind: str = "static_html",
) -> list[CandidateEndpoint]:
    parser = _CandidateHTMLParser()
    parser.feed(body)

    candidates: list[CandidateEndpoint] = []
    for method, raw_url in parser.refs:
        if not raw_url:
            continue
        candidate = candidate_from_url(
            raw_url,
            method=method,
            base_url=base_url,
            scope=scope,
            source_kind=source_kind,
        )
        if candidate:
            candidates.append(candidate)
    return dedupe_candidates(candidates)
