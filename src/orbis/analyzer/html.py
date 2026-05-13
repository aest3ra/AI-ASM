"""HTML candidate extraction for links, forms, and preload references."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse

from orbis.analyzer.common import candidate_from_url, dedupe_candidates
from orbis.crawler.scope import Scope
from orbis.shared.candidate_store import CandidateEndpoint


_DYNAMIC_SUFFIXES = (
    ".asp",
    ".aspx",
    ".ashx",
    ".cgi",
    ".do",
    ".jsp",
    ".php",
)
_ACTION_TOKENS = {
    "ajax",
    "api",
    "autocomplete",
    "captcha",
    "down",
    "excel",
    "export",
    "json",
    "popup",
    "preview",
    "print",
    "rcmd",
    "recommend",
    "search",
    "suggest",
    "upload",
    "viewer",
}
_ACTION_SUBSTRINGS = (
    "attach",
    "download",
    "file",
    "resource",
    "thumb",
)


@dataclass(frozen=True)
class _HTMLRef:
    method: str
    raw_url: str
    kind: str


@dataclass(frozen=True)
class HTMLRef:
    method: str
    raw_url: str
    absolute_url: str
    kind: str


class _CandidateHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[_HTMLRef] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value or "" for name, value in attrs}
        if tag == "form":
            method = attr.get("method", "GET") or "GET"
            action = attr.get("action", "")
            if action:
                self.refs.append(_HTMLRef(method, action, "form"))
        elif tag == "a":
            self.refs.append(_HTMLRef("GET", attr.get("href", ""), "link"))
        elif tag == "link":
            self.refs.append(_HTMLRef("GET", attr.get("href", ""), "asset"))


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
    for ref in parser.refs:
        if not ref.raw_url:
            continue
        require_api_marker = True
        if ref.kind == "form":
            require_api_marker = False
        elif ref.kind == "link" and _looks_action_link(ref.raw_url, base_url):
            require_api_marker = False
        candidate = candidate_from_url(
            ref.raw_url,
            method=ref.method,
            base_url=base_url,
            scope=scope,
            source_kind=source_kind,
            require_api_marker=require_api_marker,
        )
        if candidate:
            candidates.append(candidate)
    return dedupe_candidates(candidates)


def extract_refs(body: str, *, base_url: str) -> list[HTMLRef]:
    """Return raw URL surface references from HTML without API filtering."""
    parser = _CandidateHTMLParser()
    parser.feed(body)

    refs: list[HTMLRef] = []
    for ref in parser.refs:
        raw_url = ref.raw_url.strip()
        if _skip_ref(raw_url):
            continue
        absolute = urljoin(base_url, raw_url)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        refs.append(HTMLRef(
            method=ref.method.upper(),
            raw_url=raw_url,
            absolute_url=absolute,
            kind=ref.kind,
        ))
    return refs


def _looks_action_link(raw_url: str, base_url: str) -> bool:
    lowered_raw = raw_url.strip().lower()
    if _skip_ref(lowered_raw):
        return False
    absolute = urljoin(base_url, raw_url)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    if not path or path == "/":
        return False
    if any(
        marker in path
        for marker in ("/api/", "/api-", "/api_", "/rest/", "/graphql", "/gql")
    ):
        return True
    has_dynamic_suffix = path.endswith(_DYNAMIC_SUFFIXES)
    return has_dynamic_suffix and _has_action_keyword(path)


def looks_action_link(raw_url: str, base_url: str) -> bool:
    return _looks_action_link(raw_url, base_url)


def _skip_ref(raw_url: str) -> bool:
    lowered = raw_url.strip().lower()
    return (
        not lowered
        or lowered.startswith("#")
        or lowered.startswith(("javascript:", "mailto:", "tel:", "data:"))
    )


def _has_action_keyword(path: str) -> bool:
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", stem)
        if token
    }
    if stem in _ACTION_TOKENS or tokens & _ACTION_TOKENS:
        return True
    return any(keyword in stem for keyword in _ACTION_SUBSTRINGS)
