"""Per-page network capture via Playwright + CDP.

Designed to be reused across many pages by `Crawler`: the caller provides a
shared `BrowserContext`, this module only owns the per-page lifecycle and
returns counters describing what happened.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext

from ai_asm.agent.network import NetworkEventBuffer
from ai_asm.agent.snapshot import compute_dom_signature
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import (
    CapturedRequest,
    InteractionStats,
    PageDiagnostics,
)

DEFAULT_MAX_BODY_BYTES = 256 * 1024
JS_MAX_BODY_BYTES = 5 * 1024 * 1024
JSON_MAX_BODY_BYTES = 1024 * 1024
HTML_MAX_BODY_BYTES = 2 * 1024 * 1024

INIT_SCRIPT = """
(() => {
    if (window.__aiAsmNetworkHookInstalled) return;
    window.__aiAsmNetworkHookInstalled = true;
    window.__aiAsmRequests = window.__aiAsmRequests || [];
    const record = (source, method, url) => {
        try {
            const absolute = new URL(String(url), document.baseURI).href;
            window.__aiAsmRequests.push({
                source,
                method: String(method || "GET").toUpperCase(),
                url: absolute,
                ts: Date.now(),
            });
        } catch (_) {}
    };

    const originalFetch = window.fetch;
    if (typeof originalFetch === "function") {
        window.fetch = function(input, init) {
            const method = init && init.method ||
                input && input.method ||
                "GET";
            const url = input && input.url || input;
            record("fetch", method, url);
            return originalFetch.apply(this, arguments);
        };
    }

    const OriginalRequest = window.Request;
    if (typeof OriginalRequest === "function") {
        window.Request = function(input, init) {
            const method = init && init.method ||
                input && input.method ||
                "GET";
            const url = input && input.url || input;
            record("request", method, url);
            return new OriginalRequest(input, init);
        };
        window.Request.prototype = OriginalRequest.prototype;
    }

    const originalOpen = typeof XMLHttpRequest !== "undefined" &&
        XMLHttpRequest.prototype.open;
    if (originalOpen) {
        XMLHttpRequest.prototype.open = function(method, url) {
            record("xhr_open", method, url);
            return originalOpen.apply(this, arguments);
        };
    }

    const OriginalWebSocket = window.WebSocket;
    if (typeof OriginalWebSocket === "function") {
        window.WebSocket = function(url, protocols) {
            record("websocket", "GET", url);
            return protocols === undefined
                ? new OriginalWebSocket(url)
                : new OriginalWebSocket(url, protocols);
        };
        window.WebSocket.prototype = OriginalWebSocket.prototype;
    }

    const OriginalEventSource = window.EventSource;
    if (typeof OriginalEventSource === "function") {
        window.EventSource = function(url, config) {
            record("eventsource", "GET", url);
            return new OriginalEventSource(url, config);
        };
        window.EventSource.prototype = OriginalEventSource.prototype;
    }
})();
"""


async def capture_page(
    context: BrowserContext,
    url: str,
    *,
    nav_timeout_ms: int = 20_000,
    settle_ms: int = 2_000,
    interact: Callable[..., Awaitable[InteractionStats | None]] | None = None,
    extract_links: Callable[..., Awaitable[list[str]]] | None = None,
    analyzer_dispatcher: Any | None = None,
    request_scope: Scope | None = None,
) -> tuple[list[CapturedRequest], list[str], PageDiagnostics]:
    """Open `url`, capture every request, return (captures, links, diagnostics).

    `interact(page)` runs after `goto` to trigger lazy XHRs.
    `extract_links(page)` returns hrefs the runner should consider visiting.
    Failures are recorded in diagnostics rather than raised.
    """
    captured: dict[str, CapturedRequest] = {}
    network_events = NetworkEventBuffer()
    diag = PageDiagnostics()

    page = await context.new_page()
    await page.add_init_script(INIT_SCRIPT)
    if request_scope is not None:
        async def scoped_route(route):
            await _route_with_scope(route, request_scope, diag)

        await page.route("**/*", scoped_route)
    client = await context.new_cdp_session(page)
    await client.send("Network.enable")

    def on_request(event: dict) -> None:
        req = event["request"]
        network_events.record(
            method=req["method"],
            url=req["url"],
            resource_type=event.get("type", "Other"),
        )
        captured[event["requestId"]] = CapturedRequest(
            request_id=event["requestId"],
            method=req["method"],
            url=req["url"],
            resource_type=event.get("type", "Other"),
            request_headers=req.get("headers", {}) or {},
            post_data=req.get("postData"),
            page_url=url,
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
            stats = await interact(page, network_events=network_events)
            if stats is not None:
                diag.interactions = stats
                links.extend(stats.discovered_urls)
        except Exception:
            pass

    if extract_links:
        try:
            links.extend(await extract_links(page))
        except Exception:
            pass

    diag.dom_signature = await compute_dom_signature(page)
    init_caps = await _collect_init_script_captures(
        page,
        page_url=url,
        existing=captured.values(),
    )
    diag.init_script_requests_recorded = len(init_caps.recorded)
    diag.init_script_requests_added = len(init_caps.added)
    for cap in init_caps.added:
        captured[cap.request_id] = cap

    await _fill_response_bodies(client, captured)
    if analyzer_dispatcher is not None:
        await _dispatch_captures(analyzer_dispatcher, captured.values(), page_url=url)

    diag.body_fetch_failures = sum(
        1 for c in captured.values() if c.body_fetch_error
    )

    await page.close()
    return list(captured.values()), _dedupe_links(links), diag


async def _route_with_scope(route, scope: Scope, diag: PageDiagnostics) -> None:
    if should_abort_request(route.request.url, scope):
        diag.out_of_scope_requests_aborted += 1
        await route.abort()
        return
    await route.continue_()


def should_abort_request(url: str, scope: Scope) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    return not scope.allows(url)


class InitScriptCaptures:
    def __init__(
        self,
        *,
        recorded: list[CapturedRequest],
        added: list[CapturedRequest],
    ) -> None:
        self.recorded = recorded
        self.added = added


async def _collect_init_script_captures(
    page,
    *,
    page_url: str,
    existing,
) -> InitScriptCaptures:
    try:
        records = await page.evaluate("() => window.__aiAsmRequests || []")
    except Exception:
        return InitScriptCaptures(recorded=[], added=[])

    recorded: list[CapturedRequest] = []
    added: list[CapturedRequest] = []
    seen = {
        (cap.method.upper(), cap.url)
        for cap in existing
    }
    for idx, record in enumerate(records):
        cap = init_script_record_to_capture(
            record,
            page_url=page_url,
            request_id=f"init-script:{idx}",
        )
        if cap is None:
            continue
        recorded.append(cap)
        key = (cap.method.upper(), cap.url)
        if key in seen:
            continue
        seen.add(key)
        added.append(cap)
    return InitScriptCaptures(recorded=recorded, added=added)


def init_script_record_to_capture(
    record: dict[str, Any],
    *,
    page_url: str,
    request_id: str,
) -> CapturedRequest | None:
    raw_url = record.get("url")
    if not raw_url:
        return None
    absolute = urljoin(page_url, str(raw_url))
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https", "ws", "wss"):
        return None
    source = str(record.get("source") or "init_script")
    method = str(record.get("method") or "GET").upper()
    return CapturedRequest(
        request_id=request_id,
        method=method,
        url=absolute,
        resource_type=_resource_type_for_init_source(source),
        page_url=page_url,
        source="init_script",
    )


def _resource_type_for_init_source(source: str) -> str:
    return {
        "fetch": "Fetch",
        "request": "Fetch",
        "xhr_open": "XHR",
        "websocket": "WebSocket",
        "eventsource": "EventSource",
    }.get(source, "Other")


def _dedupe_links(links: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        out.append(link)
    return out


async def _fill_response_bodies(
    client, captured: dict[str, CapturedRequest]
) -> None:
    for rid, cap in captured.items():
        if cap.response_status is None:
            continue
        try:
            res = await client.send("Network.getResponseBody", {"requestId": rid})
            body = res.get("body", "") or ""
            limit = body_limit_for(cap.response_mime)
            if len(body) > limit:
                cap.response_body = body[:limit]
                cap.response_body_truncated = True
            else:
                cap.response_body = body
        except Exception as e:
            cap.body_fetch_error = type(e).__name__


def body_limit_for(mime: str | None) -> int:
    if not mime:
        return DEFAULT_MAX_BODY_BYTES
    lowered = mime.lower()
    if "javascript" in lowered or "ecmascript" in lowered:
        return JS_MAX_BODY_BYTES
    if "json" in lowered:
        return JSON_MAX_BODY_BYTES
    if lowered in {"text/html", "application/xhtml+xml"}:
        return HTML_MAX_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


async def _dispatch_captures(
    analyzer_dispatcher,
    captures,
    *,
    page_url: str,
) -> None:
    if hasattr(analyzer_dispatcher, "dispatch_many"):
        await analyzer_dispatcher.dispatch_many(captures, page_url=page_url)
        return
    tasks = [
        analyzer_dispatcher.dispatch_capture(cap, page_url=page_url)
        for cap in captures
        if cap.response_body and not cap.body_fetch_error
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
