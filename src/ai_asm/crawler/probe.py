"""Conservative probing of static GET API candidates."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Literal
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, async_playwright

from ai_asm.config import AuthConfig
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest, StaticProbeAuthProfile
from ai_asm.normalizer.static import ApiCandidate
from ai_asm.safety import is_dangerous_url, is_download_url

MAX_STATIC_GET_PROBES = 25
StaticProbeAuthMode = Literal["none", "cookie-only", "learned"]


async def probe_static_get_with_auth_context(
    auth: AuthConfig,
    candidates: list[ApiCandidate],
    *,
    headless: bool,
    auth_mode: StaticProbeAuthMode,
    auth_profiles: dict[str, StaticProbeAuthProfile] | None = None,
    max_probes: int = MAX_STATIC_GET_PROBES,
) -> tuple[list[CapturedRequest], set[str], dict[str, str]]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context_kwargs = {} if auth_mode == "none" else _context_kwargs(auth)
        context = await browser.new_context(**context_kwargs)
        try:
            return await probe_static_get_candidates(
                context,
                candidates,
                max_probes=max_probes,
                auth_mode=auth_mode,
                auth_profiles=auth_profiles,
            )
        finally:
            await browser.close()


async def probe_static_get_candidates(
    context: BrowserContext,
    candidates: list[ApiCandidate],
    *,
    max_probes: int = MAX_STATIC_GET_PROBES,
    auth_mode: StaticProbeAuthMode = "cookie-only",
    auth_profiles: dict[str, StaticProbeAuthProfile] | None = None,
) -> tuple[list[CapturedRequest], set[str], dict[str, str]]:
    """Issue same-origin GET requests for static candidates via browser fetch().

    This intentionally avoids POST/PUT/DELETE and skips URLs that still contain
    unresolved placeholders or obvious template leftovers.
    """
    page = await context.new_page()
    captures: list[CapturedRequest] = []
    probed: set[str] = set()
    errors: dict[str, str] = {}
    current_origin: str | None = None
    profiles = auth_profiles or {}
    try:
        for candidate in _probeable_candidates(candidates)[:max_probes]:
            url = candidate.sample_url
            try:
                origin = _origin(url)
                if origin != current_origin:
                    await page.goto(
                        f"{origin}/",
                        wait_until="domcontentloaded",
                        timeout=10_000,
                    )
                    current_origin = origin
                auth_headers = headers_for_static_probe(
                    url,
                    profiles,
                    mode=auth_mode,
                )
                request_headers = {
                    "Accept": "application/json,text/plain,*/*",
                    **auth_headers,
                }
                credentials = "omit" if auth_mode == "none" else "same-origin"
                result = await page.evaluate(
                    """
                    async ({url, headers, credentials}) => {
                        const response = await fetch(url, {
                            method: "GET",
                            credentials,
                            headers,
                        });
                        const text = await response.text();
                        const responseHeaders = {};
                        response.headers.forEach((value, key) => responseHeaders[key] = value);
                        return {
                            status: response.status,
                            headers: responseHeaders,
                            mime: response.headers.get("content-type") || "",
                            body: text.slice(0, 262144),
                            truncated: text.length > 262144,
                        };
                    }
                    """,
                    {
                        "url": url,
                        "headers": request_headers,
                        "credentials": credentials,
                    },
                )
                captures.append(CapturedRequest(
                    request_id=f"static-probe:{len(probed)}",
                    method="GET",
                    url=url,
                    resource_type="Fetch",
                    request_headers=request_headers,
                    page_url=candidate.source_url,
                    source="static_probe",
                    response_status=result.get("status"),
                    response_headers=result.get("headers", {}) or {},
                    response_mime=result.get("mime"),
                    response_body=result.get("body"),
                    response_body_truncated=bool(result.get("truncated")),
                ))
                probed.add(url)
                await asyncio.sleep(0.05)
            except Exception as e:
                errors[url] = type(e).__name__
    finally:
        await page.close()
    return captures, probed, errors


def learn_static_probe_auth_profiles(
    captures: list[CapturedRequest],
    *,
    mode: StaticProbeAuthMode,
    scope: Scope,
) -> dict[str, StaticProbeAuthProfile]:
    if mode != "learned":
        return {}

    counts: dict[str, Counter[tuple[tuple[str, str], ...]]] = defaultdict(Counter)
    source_urls: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
    for cap in captures:
        if cap.source == "static_probe" or not _successful(cap.response_status):
            continue
        if cap.resource_type.lower() not in {"xhr", "fetch"}:
            continue
        if not scope.allows(cap.url):
            continue
        headers = _learnable_auth_headers(cap.request_headers)
        if not headers:
            continue
        origin = _origin(cap.url)
        key = tuple(sorted(headers.items()))
        counts[origin][key] += 1
        source_urls.setdefault((origin, key), cap.url)

    profiles: dict[str, StaticProbeAuthProfile] = {}
    for origin, counter in counts.items():
        key, observed_count = counter.most_common(1)[0]
        profiles[origin] = StaticProbeAuthProfile(
            origin=origin,
            headers=dict(key),
            learned_from_url=source_urls[(origin, key)],
            observed_count=observed_count,
        )
    return profiles


def headers_for_static_probe(
    url: str,
    profiles: dict[str, StaticProbeAuthProfile],
    *,
    mode: StaticProbeAuthMode,
) -> dict[str, str]:
    if mode != "learned":
        return {}
    profile = profiles.get(_origin(url))
    if profile is None:
        return {}
    return dict(profile.headers)


def _probeable_candidates(candidates: list[ApiCandidate]) -> list[ApiCandidate]:
    out: list[ApiCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = candidate.sample_url
        if url in seen or not _is_probeable_url(url):
            continue
        seen.add(url)
        out.append(candidate)
    return out


def _is_probeable_url(url: str) -> bool:
    return static_probe_skip_reason(url) is None


def static_probe_skip_reason(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "scheme"
    if any(token in url for token in ("{", "}", "$", "`", "${", "<", ">")):
        return "template"
    if is_dangerous_url(url):
        return "danger"
    if is_download_url(url):
        return "download"
    return None


def _learnable_auth_headers(headers: dict[str, str]) -> dict[str, str]:
    value = _header_value(headers, "authorization")
    if not value:
        return {}
    return {"Authorization": value}


def _header_value(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted and value:
            return value
    return None


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def _successful(status: int | None) -> bool:
    return status is not None and 200 <= status < 400


def _context_kwargs(auth: AuthConfig) -> dict:
    if auth.type == "storage_state" and auth.storage_state_path:
        return {"storage_state": str(auth.storage_state_path)}
    return {}
