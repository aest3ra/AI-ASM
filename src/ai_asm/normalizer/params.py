"""Extract parameters from a captured request and infer their types."""

from __future__ import annotations

import json
import re
from http.cookies import SimpleCookie
from typing import Iterable
from urllib.parse import parse_qsl, urlparse

from ai_asm.crawler.types import CapturedRequest

# Headers the browser sets automatically. Treating these as user-defined
# parameters would drown the real custom headers in noise.
SKIP_HEADERS = frozenset(h.lower() for h in {
    # Browser-set, not user/app-defined
    "accept", "accept-encoding", "accept-language", "accept-charset",
    "cache-control", "connection", "content-length", "content-type",
    "cookie", "host", "origin", "pragma", "referer", "upgrade-insecure-requests",
    "user-agent", "dnt", "te",
    # Conditional/cache validators (browser auto, no semantic value for ASM)
    "if-modified-since", "if-none-match", "if-match",
    "if-unmodified-since", "if-range",
    # Range requests (PDFs, video chunks)
    "range",
    # Network hints / client info
    "save-data", "priority", "device-memory", "viewport-width",
    "downlink", "ect", "rtt",
    # Vendor tracking / prefetch hints
    "x-client-data", "purpose",
})
SKIP_HEADER_PREFIXES = ("sec-", ":")  # Sec-Ch-Ua, Sec-Fetch-*, HTTP/2 pseudo


_CACHE_BUSTER = re.compile(r"[0-9a-fA-F]+")


def extract_query(url: str) -> Iterable[tuple[str, str]]:
    query = urlparse(url).query
    pairs = parse_qsl(query, keep_blank_values=True)
    # Cache busters like `?01323` parse as [("01323", "")]. Skip them.
    if (
        len(pairs) == 1
        and pairs[0][1] == ""
        and "=" not in query
        and _CACHE_BUSTER.fullmatch(pairs[0][0] or "")
    ):
        return []
    return pairs


def extract_headers(headers: dict[str, str]) -> Iterable[tuple[str, str]]:
    for k, v in headers.items():
        kl = k.lower()
        if kl in SKIP_HEADERS:
            continue
        if any(kl.startswith(pfx) for pfx in SKIP_HEADER_PREFIXES):
            continue
        yield k, v


def extract_cookies(headers: dict[str, str]) -> Iterable[tuple[str, str]]:
    raw = headers.get("cookie") or headers.get("Cookie")
    if not raw:
        return
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return
    for k, morsel in jar.items():
        yield k, morsel.value


def extract_body(
    headers: dict[str, str], post_data: str | None
) -> Iterable[tuple[str, str]]:
    if not post_data:
        return
    ct = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
    primary = ct.split(";")[0].strip()

    if primary == "application/x-www-form-urlencoded" or (not primary and "=" in post_data):
        yield from parse_qsl(post_data, keep_blank_values=True)
        return

    if primary == "application/json" or (not primary and post_data.lstrip().startswith(("{", "["))):
        try:
            data = json.loads(post_data)
        except Exception:
            return
        if isinstance(data, dict):
            for k, v in data.items():
                yield k, _scalar(v)
        return
    # multipart/form-data and other types skipped in MVP


def _scalar(v) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)[:200]
    return str(v)


_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+\.\d+")


def infer_type(value: str) -> str:
    if value == "":
        return "empty"
    if value.lower() in ("true", "false"):
        return "bool"
    if _FLOAT.fullmatch(value):
        return "float"
    if _INT.fullmatch(value):
        return "int"
    if value.startswith(("{", "[")) and value.endswith(("}", "]")):
        return "json"
    return "string"


def extract_all(req: CapturedRequest) -> list[tuple[str, str, str]]:
    """Yield (location, name, value) for every parameter on this request."""
    out: list[tuple[str, str, str]] = []
    for k, v in extract_query(req.url):
        out.append(("query", k, v))
    for k, v in extract_headers(req.request_headers):
        out.append(("header", k, v))
    for k, v in extract_cookies(req.request_headers):
        out.append(("cookie", k, v))
    for k, v in extract_body(req.request_headers, req.post_data):
        out.append(("body", k, v))
    return out
