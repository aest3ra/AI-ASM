from ai_asm.config import ScopeConfig
from ai_asm.crawler.browser import (
    INIT_SCRIPT,
    body_limit_for,
    init_script_record_to_capture,
    should_abort_request,
)
from ai_asm.crawler.scope import Scope


def test_should_abort_out_of_scope_http_requests():
    scope = Scope(ScopeConfig(include_domains=["example.com"]))

    assert should_abort_request("https://github.com/app.js", scope)
    assert not should_abort_request("https://example.com/app.js", scope)
    assert not should_abort_request("data:image/png;base64,abc", scope)
    assert not should_abort_request("about:blank", scope)


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
