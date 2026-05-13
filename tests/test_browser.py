import asyncio
import time

from orbis.config import ScopeConfig
from orbis.crawler.browser import (
    INIT_SCRIPT,
    _close_page_safely,
    _collect_dom_snapshot_capture,
    _fill_response_bodies,
    body_limit_for,
    init_script_record_to_capture,
    should_abort_request,
)
from orbis.crawler.scope import Scope
from orbis.crawler.types import CapturedRequest


def test_should_abort_out_of_scope_active_http_requests():
    scope = Scope(ScopeConfig(include_domains=["example.com"]))

    assert should_abort_request("https://github.com/api/users", scope)
    assert should_abort_request(
        "https://github.com/api/users",
        scope,
        resource_type="fetch",
    )
    assert should_abort_request(
        "https://github.com/dashboard",
        scope,
        resource_type="document",
    )
    assert not should_abort_request("https://example.com/app.js", scope)
    assert not should_abort_request("data:image/png;base64,abc", scope)
    assert not should_abort_request("about:blank", scope)


def test_should_allow_out_of_scope_passive_dependencies_for_hydration():
    scope = Scope(ScopeConfig(include_domains=["example.com"]))

    assert not should_abort_request(
        "https://cdn.example.net/app.mjs",
        scope,
        resource_type="script",
    )
    assert not should_abort_request(
        "https://cdn.example.net/font.woff2",
        scope,
        resource_type="font",
    )
    assert not should_abort_request(
        "https://cdn.example.net/style.css",
        scope,
        resource_type="stylesheet",
    )


def test_body_limit_allows_large_javascript_for_static_analysis():
    assert body_limit_for("application/javascript") >= 5 * 1024 * 1024
    assert body_limit_for("text/html") >= 2 * 1024 * 1024
    assert body_limit_for("application/json") >= 1024 * 1024


def test_init_script_contains_network_hooks():
    assert "window.fetch" in INIT_SCRIPT
    assert "XMLHttpRequest" in INIT_SCRIPT
    assert "WebSocket" in INIT_SCRIPT
    assert "EventSource" in INIT_SCRIPT
    assert "window.Request" in INIT_SCRIPT


def test_init_script_record_to_capture_resolves_relative_url():
    cap = init_script_record_to_capture(
        {"source": "fetch", "method": "post", "url": "/api/users"},
        page_url="https://example.com/app",
        request_id="init-script:1",
    )

    assert cap is not None
    assert cap.method == "POST"
    assert cap.url == "https://example.com/api/users"
    assert cap.resource_type == "Fetch"
    assert cap.source == "init_script"


def test_init_script_record_to_capture_skips_non_http_url():
    assert init_script_record_to_capture(
        {"source": "fetch", "url": "data:text/plain,ok"},
        page_url="https://example.com",
        request_id="init-script:1",
    ) is None


def test_close_page_safely_times_out_stuck_playwright_close():
    class StuckClient:
        async def detach(self):
            await asyncio.sleep(10)

    class StuckPage:
        async def close(self):
            await asyncio.sleep(10)

    async def run():
        started = time.monotonic()
        await _close_page_safely(StuckPage(), StuckClient(), timeout_sec=0.01)
        assert time.monotonic() - started < 0.2

    asyncio.run(run())


def test_collect_dom_snapshot_capture_adds_document_body_when_cdp_body_missing():
    class Page:
        url = "https://example.com/app"

        async def content(self):
            return "<html><body><a href='/api/users'>Users</a></body></html>"

    async def run():
        cap = await _collect_dom_snapshot_capture(
            Page(),
            page_url="https://example.com/app",
            existing=[],
        )
        assert cap is not None
        assert cap.url == "https://example.com/app"
        assert cap.resource_type == "Document"
        assert cap.source == "dom_snapshot"
        assert "/api/users" in (cap.response_body or "")

    asyncio.run(run())


def test_collect_dom_snapshot_capture_skips_when_cdp_body_exists():
    class Page:
        url = "https://example.com/app"

        async def content(self):
            raise AssertionError("page.content should not be called")

    existing = [
        CapturedRequest(
            request_id="document",
            method="GET",
            url="https://example.com/app",
            resource_type="Document",
            response_body="<html>ok</html>",
        )
    ]

    async def run():
        cap = await _collect_dom_snapshot_capture(
            Page(),
            page_url="https://example.com/app",
            existing=existing,
        )
        assert cap is None

    asyncio.run(run())


def test_fill_response_bodies_uses_stable_capture_snapshot():
    captured = {
        "r1": CapturedRequest(
            request_id="r1",
            method="GET",
            url="https://example.com/",
            resource_type="Document",
            response_status=200,
            response_mime="text/html",
        )
    }

    class MutatingClient:
        async def send(self, method, payload):
            captured["late"] = CapturedRequest(
                request_id="late",
                method="GET",
                url="https://example.com/late.js",
                resource_type="Script",
                response_status=200,
                response_mime="application/javascript",
            )
            return {"body": "<html>ok</html>"}

    async def run():
        await _fill_response_bodies(MutatingClient(), captured)
        assert captured["r1"].response_body == "<html>ok</html>"
        assert captured["late"].response_body is None

    asyncio.run(run())
