"""Group raw captures into deduplicated endpoints with parameter catalogs."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer.params import extract_all, infer_type
from ai_asm.normalizer.types import NormalizedEndpoint, NormalizedParameter
from ai_asm.normalizer.url import templatize_path

MAX_SAMPLES_PER_PARAM = 5
API_PREFIXES = ("/api", "/rest", "/graphql", "/b2b")
API_MARKER_RE = re.compile(r"/(?:api|rest|graphql|b2b)(?=/|$)", re.IGNORECASE)
STATIC_PATH_PREFIXES = ("/assets/", "/media/")
STATIC_SUFFIXES = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)


def normalize(
    captures: list[CapturedRequest],
    *,
    api_only: bool = False,
) -> list[NormalizedEndpoint]:
    """Group `captures` by (method, host, path_template) and accumulate params."""
    by_key: dict[tuple[str, str, str], NormalizedEndpoint] = {}

    for req in captures:
        if api_only and not is_api_capture(req):
            continue
        parsed = urlparse(req.url)
        host = parsed.hostname or ""
        raw_path = parsed.path or "/"
        if api_only:
            raw_path = canonical_api_path(raw_path)
        path_template = templatize_path(raw_path)
        key = (req.method, host, path_template)

        ep = by_key.get(key)
        if ep is None:
            ep = NormalizedEndpoint(
                method=req.method,
                host=host,
                path_template=path_template,
                sample_url=req.url,
            )
            by_key[key] = ep
        ep.seen_count += 1
        ep.resource_types.add(req.resource_type)

        for location, name, value in extract_all(req):
            pkey = (location, name)
            param = ep.parameters.get(pkey)
            if param is None:
                param = NormalizedParameter(
                    location=location, name=name, type_inferred=infer_type(value),
                )
                ep.parameters[pkey] = param
            param.seen_count += 1
            if value not in param.sample_values and len(param.sample_values) < MAX_SAMPLES_PER_PARAM:
                param.sample_values.append(value)

    return list(by_key.values())


def is_api_capture(req: CapturedRequest) -> bool:
    parsed = urlparse(req.url)
    path = parsed.path or "/"
    lowered = path.lower()
    if "/socket.io/" in lowered or lowered == "/socket.io":
        return False
    if lowered.startswith(STATIC_PATH_PREFIXES):
        return False
    if lowered.endswith(STATIC_SUFFIXES):
        return False
    if API_MARKER_RE.search(lowered):
        return True
    mime = (req.response_mime or "").lower()
    return req.resource_type in {"XHR", "Fetch"} and "json" in mime


def canonical_api_path(path: str) -> str:
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path
