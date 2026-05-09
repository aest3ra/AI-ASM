"""Classify discovered URLs separately from confirmed API endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from ai_asm.analyzer import html
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer.pipeline import API_MARKER_RE, canonical_api_path
from ai_asm.normalizer.static import ApiCandidate
from ai_asm.normalizer.url import templatize_path
from ai_asm.safety import is_download_url

RouteKind = str

ASSET_SUFFIXES = (
    ".js",
    ".css",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".webm",
)
FILE_SUFFIXES = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".hwp",
)
HTML_MIME_MARKERS = ("html", "xhtml")


@dataclass(frozen=True)
class UrlSurfaceRecord:
    method: str
    host: str
    path_template: str
    sample_url: str
    source_kind: str
    observed: bool
    route_kind: RouteKind
    api_score: int
    evidence: dict[str, object]
    source_url: str | None = None
    status_code: int | None = None
    mime: str | None = None
    resource_type: str | None = None
    seen_count: int = 1


def discover_url_surfaces(
    captures: list[CapturedRequest],
    scope: Scope,
) -> list[UrlSurfaceRecord]:
    """Build URL surface records from observed network and HTML references."""
    surfaces: list[UrlSurfaceRecord] = []
    for cap in captures:
        if not scope.allows(cap.url):
            continue
        surfaces.append(_surface_from_capture(cap))
        if cap.response_body and _looks_htmlish(cap.response_mime):
            surfaces.extend(_surfaces_from_html(cap.response_body, cap.url, scope))
    return merge_url_surfaces(surfaces)


def surfaces_from_static_candidates(
    candidates: list[ApiCandidate],
) -> list[UrlSurfaceRecord]:
    surfaces = [
        classify_url(
            candidate.sample_url,
            method="GET",
            source_kind="js_static",
            observed=False,
            source_url=candidate.source_url,
            evidence={"source": "static_candidate"},
        )
        for candidate in candidates
    ]
    return merge_url_surfaces(surfaces)


def merge_url_surfaces(
    surfaces: list[UrlSurfaceRecord],
) -> list[UrlSurfaceRecord]:
    merged: dict[tuple[str, str, str, str], UrlSurfaceRecord] = {}
    for surface in surfaces:
        key = (
            surface.method,
            surface.host,
            surface.path_template,
            surface.route_kind,
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = surface
            continue
        evidence = dict(existing.evidence)
        evidence.update(surface.evidence)
        source_kinds = set(str(evidence.get("source_kinds", "")).split(","))
        source_kinds.discard("")
        source_kinds.add(existing.source_kind)
        source_kinds.add(surface.source_kind)
        evidence["source_kinds"] = ",".join(sorted(source_kinds))
        merged[key] = replace(
            existing,
            observed=existing.observed or surface.observed,
            seen_count=existing.seen_count + surface.seen_count,
            api_score=max(existing.api_score, surface.api_score),
            evidence=evidence,
            status_code=existing.status_code or surface.status_code,
            mime=existing.mime or surface.mime,
            resource_type=existing.resource_type or surface.resource_type,
        )
    return sorted(
        merged.values(),
        key=lambda s: (s.route_kind, s.host, s.path_template, s.method),
    )


def classify_url(
    url: str,
    *,
    method: str = "GET",
    source_kind: str,
    observed: bool,
    source_url: str | None = None,
    status_code: int | None = None,
    mime: str | None = None,
    resource_type: str | None = None,
    evidence: dict[str, object] | None = None,
) -> UrlSurfaceRecord:
    parsed = urlparse(url)
    path = parsed.path or "/"
    lowered_path = path.lower()
    lowered_mime = (mime or "").lower()
    method = method.upper()

    score = 0
    signals: list[str] = []
    kind: RouteKind = "unknown"

    if lowered_path.endswith(ASSET_SUFFIXES):
        kind = "asset"
        score -= 5
        signals.append("asset_suffix")
    elif lowered_path.endswith(FILE_SUFFIXES) or is_download_url(url):
        kind = "file"
        score -= 2
        signals.append(
            "file_suffix" if lowered_path.endswith(FILE_SUFFIXES) else "download_url",
        )
    else:
        strong_api_signal = False
        if API_MARKER_RE.search(lowered_path):
            score += 4
            strong_api_signal = True
            signals.append("api_path_marker")
        if "json" in lowered_mime:
            score += 4
            strong_api_signal = True
            signals.append("json_mime")
        if resource_type in {"XHR", "Fetch"}:
            score += 3
            strong_api_signal = True
            signals.append("xhr_fetch")
        if method not in {"GET", "HEAD"}:
            score += 3
            signals.append("non_get_method")
        if source_kind in {"html_form", "html_action_link"}:
            score += 1
            signals.append(source_kind)
        if source_kind == "js_static":
            score += 2
            signals.append("js_static")
        if resource_type == "Document":
            score -= 3
            signals.append("document_resource")
        if any(marker in lowered_mime for marker in HTML_MIME_MARKERS):
            score -= 4
            signals.append("html_mime")

        if (
            observed
            and resource_type in {"XHR", "Fetch"}
            and method not in {"GET", "HEAD"}
        ):
            kind = "api_endpoint"
        elif source_kind in {"html_form", "html_action_link"} and not strong_api_signal:
            kind = "action_route"
        elif score >= 4:
            kind = "api_endpoint"
        else:
            kind = (
                "page_route"
                if _looks_page_route(path, lowered_mime, resource_type)
                else "unknown"
            )

    evidence_data = dict(evidence or {})
    evidence_data.update({
        "signals": signals,
        "query_present": bool(parsed.query),
    })
    host = parsed.hostname or ""
    path_template = templatize_path(canonical_api_path(path))
    return UrlSurfaceRecord(
        method=method,
        host=host,
        path_template=path_template,
        sample_url=url,
        source_kind=source_kind,
        observed=observed,
        route_kind=kind,
        api_score=score,
        evidence=evidence_data,
        source_url=source_url,
        status_code=status_code,
        mime=mime,
        resource_type=resource_type,
    )


def evidence_json(surface: UrlSurfaceRecord) -> str:
    return json.dumps(surface.evidence, ensure_ascii=False, sort_keys=True)


def _surface_from_capture(cap: CapturedRequest) -> UrlSurfaceRecord:
    return classify_url(
        cap.url,
        method=cap.method,
        source_kind=_source_kind_for_capture(cap),
        observed=True,
        source_url=cap.page_url,
        status_code=cap.response_status,
        mime=cap.response_mime,
        resource_type=cap.resource_type,
        evidence={"request_source": cap.source},
    )


def _surfaces_from_html(
    body: str,
    base_url: str,
    scope: Scope,
) -> list[UrlSurfaceRecord]:
    surfaces: list[UrlSurfaceRecord] = []
    for ref in html.extract_refs(body, base_url=base_url):
        if not scope.allows(ref.absolute_url):
            continue
        source_kind = "html_form"
        if ref.kind == "asset":
            source_kind = "html_asset"
        elif ref.kind == "link":
            source_kind = (
                "html_action_link"
                if html.looks_action_link(ref.raw_url, base_url)
                else "html_link"
            )
        surfaces.append(classify_url(
            ref.absolute_url,
            method=ref.method,
            source_kind=source_kind,
            observed=False,
            source_url=base_url,
            evidence={"html_ref_kind": ref.kind},
        ))
    return surfaces


def _source_kind_for_capture(cap: CapturedRequest) -> str:
    if cap.source == "init_script":
        return "init_script"
    if cap.resource_type == "Document":
        return "cdp_document"
    if cap.resource_type in {"XHR", "Fetch"}:
        return "cdp_fetch"
    return "cdp_capture"


def _looks_htmlish(mime: str | None) -> bool:
    if not mime:
        return False
    lowered = mime.lower()
    return any(marker in lowered for marker in HTML_MIME_MARKERS)


def _looks_page_route(path: str, mime: str, resource_type: str | None) -> bool:
    if resource_type == "Document":
        return True
    if any(marker in mime for marker in HTML_MIME_MARKERS):
        return True
    if path.endswith((".do", ".jsp", ".php", ".asp", ".aspx")):
        return True
    return "/" in path and "." not in path.rsplit("/", 1)[-1]
