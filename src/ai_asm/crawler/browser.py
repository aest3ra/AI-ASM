"""Per-page network capture via Playwright + CDP.

Designed to be reused across many pages by `Crawler`: the caller provides a
shared `BrowserContext`, this module only owns the per-page lifecycle and
returns counters describing what happened.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from playwright.async_api import BrowserContext

from ai_asm.crawler.types import (
    CapturedRequest,
    InteractionStats,
    PageDiagnostics,
)

MAX_BODY_BYTES = 256 * 1024  # 256 KB cap per response body


async def capture_page(
    context: BrowserContext,
    url: str,
    *,
    nav_timeout_ms: int = 20_000,
    settle_ms: int = 2_000,
    interact: Callable[..., Awaitable[InteractionStats | None]] | None = None,
    extract_links: Callable[..., Awaitable[list[str]]] | None = None,
) -> tuple[list[CapturedRequest], list[str], PageDiagnostics]:
    """Open `url`, capture every request, return (captures, links, diagnostics).

    `interact(page)` runs after `goto` to trigger lazy XHRs.
    `extract_links(page)` returns hrefs the runner should consider visiting.
    Failures are recorded in diagnostics rather than raised.
    """
    captured: dict[str, CapturedRequest] = {}
    diag = PageDiagnostics()

    page = await context.new_page()
    client = await context.new_cdp_session(page)
    await client.send("Network.enable")

    def on_request(event: dict) -> None:
        req = event["request"]
        captured[event["requestId"]] = CapturedRequest(
            request_id=event["requestId"],
            method=req["method"],
            url=req["url"],
            resource_type=event.get("type", "Other"),
            request_headers=req.get("headers", {}) or {},
            post_data=req.get("postData"),
        )

    def on_response(event: dict) -> None:
        rid = event["requestId"]
        cap = captured.get(rid)
        if cap is None:
            return
        r = event["response"]
        cap.response_status = r["status"]
        cap.response_headers = r.get("headers", {}) or {}
        cap.response_mime = r.get("mimeType")

    client.on("Network.requestWillBeSent", on_request)
    client.on("Network.responseReceived", on_response)

    links: list[str] = []
    # `domcontentloaded` is far more reliable than `networkidle` on real-world
    # sites that long-poll or fire periodic beacons.
    try:
        await page.goto(url, timeout=nav_timeout_ms, wait_until="domcontentloaded")
    except Exception as e:
        diag.nav_error = type(e).__name__

    await asyncio.sleep(settle_ms / 1000)

    # Extract links BEFORE interactions — a click can navigate the page away
    # and wipe the DOM links we wanted to enqueue.
    if extract_links:
        try:
            links = await extract_links(page)
        except Exception:
            links = []

    if interact:
        try:
            stats = await interact(page)
            if stats is not None:
                diag.interactions = stats
        except Exception:
            pass

    await _fill_response_bodies(client, captured)
    diag.body_fetch_failures = sum(
        1 for c in captured.values() if c.body_fetch_error
    )

    await page.close()
    return list(captured.values()), links, diag


async def _fill_response_bodies(
    client, captured: dict[str, CapturedRequest]
) -> None:
    for rid, cap in captured.items():
        if cap.response_status is None:
            continue
        try:
            res = await client.send("Network.getResponseBody", {"requestId": rid})
            body = res.get("body", "") or ""
            if len(body) > MAX_BODY_BYTES:
                cap.response_body = body[:MAX_BODY_BYTES]
                cap.response_body_truncated = True
            else:
                cap.response_body = body
        except Exception as e:
            cap.body_fetch_error = type(e).__name__
