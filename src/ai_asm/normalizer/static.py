"""Static API candidate discovery from captured text assets."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from ai_asm.analyzer.common import iter_static_endpoint_refs
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer.pipeline import canonical_api_path
from ai_asm.normalizer.url import templatize_path

_TEXT_MIME_MARKERS = ("javascript", "ecmascript", "html", "json", "text")


@dataclass(frozen=True)
class ApiCandidate:
    host: str
    path_template: str
    sample_url: str
    source_url: str


def discover_api_candidates(
    captures: list[CapturedRequest], scope: Scope
) -> list[ApiCandidate]:
    """Find likely API paths embedded in JS/HTML/JSON response bodies.

    These are not treated as observed endpoints: they are hints for follow-up
    probing or coverage reports. The dynamic network capture remains the source
    of truth for actually observed requests.
    """
    found: dict[tuple[str, str], ApiCandidate] = {}

    for cap in captures:
        if not cap.response_body or not _looks_textual(cap.response_mime):
            continue
        for raw in _iter_candidate_urls(cap.response_body):
            absolute = urljoin(cap.url, raw)
            if not scope.allows(absolute):
                continue
            parsed = urlparse(absolute)
            host = parsed.hostname or ""
            path_template = templatize_path(canonical_api_path(parsed.path or "/"))
            key = (host, path_template)
            found.setdefault(
                key,
                ApiCandidate(
                    host=host,
                    path_template=path_template,
                    sample_url=absolute,
                    source_url=cap.url,
                ),
            )

    return sorted(found.values(), key=lambda c: (c.host, c.path_template))


def _looks_textual(mime: str | None) -> bool:
    if not mime:
        return False
    lowered = mime.lower()
    return any(marker in lowered for marker in _TEXT_MIME_MARKERS)


def _iter_candidate_urls(body: str):
    for ref in iter_static_endpoint_refs(body):
        yield _sanitize_candidate_url(ref.raw_url)


def _sanitize_candidate_url(url: str) -> str:
    if url.endswith("/$"):
        return url[:-2]
    return url.rstrip(".$,;:)")
