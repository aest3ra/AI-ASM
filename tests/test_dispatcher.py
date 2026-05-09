import asyncio

from ai_asm.analyzer.dispatcher import AnalyzerDispatcher, DispatcherLimits
from ai_asm.config import ScopeConfig
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest
from ai_asm.shared.candidate_store import CandidateStore
from ai_asm.shared.decision_trace import DecisionTrace
from ai_asm.shared.response_store import ResponseStore


def _cap(
    *,
    url: str = "https://example.com/app.js",
    body: str = "fetch('/api/users/123')",
    mime: str = "application/javascript",
) -> CapturedRequest:
    return CapturedRequest(
        request_id="r1",
        method="GET",
        url=url,
        resource_type="Script",
        response_status=200,
        response_mime=mime,
        response_body=body,
    )


def _dispatcher(**limits):
    candidates = CandidateStore()
    responses = ResponseStore()
    trace = DecisionTrace(scan_id=1)
    dispatcher = AnalyzerDispatcher(
        scope=Scope(ScopeConfig(include_domains=["example.com"])),
        candidates=candidates,
        responses=responses,
        trace=trace,
        limits=DispatcherLimits(**limits),
    )
    return dispatcher, candidates, responses, trace


def test_dispatcher_extracts_static_candidates_from_js():
    async def run():
        dispatcher, candidates, _, trace = _dispatcher()

        await dispatcher.dispatch_capture(_cap(
            body="""
            fetch('/api/users/123');
            axios.post('/api/orders', { id: 1 });
            """,
        ))

        found = await candidates.top_n(10)
        assert {(c.method, c.path_template) for c in found} == {
            ("GET", "/api/users/{id}"),
            ("POST", "/api/orders"),
        }
        assert dispatcher.stats.accepted == 1
        assert [event.kind for event in await trace.events()] == [
            "dispatch_accepted",
            "candidate_added",
            "candidate_added",
        ]

    asyncio.run(run())


def test_dispatcher_does_not_import_openapi_documents_as_static_candidates():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher()

        await dispatcher.dispatch_capture(_cap(
            url="https://example.com/openapi.json",
            mime="application/json",
            body="""
            {
                "openapi": "3.0.0",
                "paths": {
                    "/users/v1": {"get": {}}
                }
            }
            """,
        ))

        return await candidates.top_n(10)

    found = asyncio.run(run())
    assert found == []


def test_dispatcher_extracts_prefixless_candidates_from_api_docs_html():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher()

        await dispatcher.dispatch_capture(_cap(
            url="https://example.com/apidoc/index.html",
            mime="text/html",
            body="""
            <h1>API docs</h1>
            <p>GET /booking</p>
            <p>GET /booking/1</p>
            <p>POST /auth</p>
            """,
        ))

        return await candidates.top_n(10)

    found = asyncio.run(run())
    assert {(c.method, c.path_template) for c in found} == {
        ("GET", "/booking"),
        ("GET", "/booking/{id}"),
        ("POST", "/auth"),
    }


def test_dispatcher_extracts_html_form_actions_and_action_links():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher()

        await dispatcher.dispatch_capture(_cap(
            url="https://example.com/ko/index.do",
            mime="text/html",
            body="""
            <a href="/ko/about/history.do">History</a>
            <a href="/cms/etcResourceOpen.do?site=ko">Resource</a>
            <form method="GET" action="/ko/search/result.do">
              <input name="q">
            </form>
            """,
        ))

        return await candidates.top_n(10)

    found = asyncio.run(run())
    assert {(c.method, c.path_template) for c in found} == {
        ("GET", "/cms/etcResourceOpen.do"),
        ("GET", "/ko/search/result.do"),
    }


def test_dispatcher_extracts_composed_angular_http_candidates():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher()

        await dispatcher.dispatch_capture(_cap(
            body="""
            class ProductsService {
              hostServer = ".";
              host = this.hostServer + "/api/Products";
              find(e) { return this.http.get(this.host + "/", {params: e}); }
              get(e) { return this.http.get(`${this.host}/${e}?d=${Date.now()}`); }
              put(e, body) { return this.http.put(`${this.host}/${e}`, body); }
            }
            class Web3Service {
              hostServer = ".";
              host = this.hostServer + "/rest/web3";
              nftUnlocked() { return this.http.get(this.host + "/nftUnlocked"); }
            }
            """,
        ))

        found = await candidates.top_n(20)
        assert {(c.method, c.path_template) for c in found} == {
            ("GET", "/api/Products"),
            ("GET", "/api/Products/{id}"),
            ("PUT", "/api/Products/{id}"),
            ("GET", "/rest/web3/nftUnlocked"),
        }

    asyncio.run(run())


def test_dispatcher_extracts_local_alias_http_candidates():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher()

        await dispatcher.dispatch_capture(_cap(
            body="""
            class Web3Service {
              hostServer = ".";
              host = this.hostServer + "/rest/web3";
              submitKey(t) {
                let e = this.host + "/submitKey", r = {privateKey: t};
                return this.http.post(e, r);
              }
            }
            """,
        ))

        found = await candidates.top_n(20)
        assert {(c.method, c.path_template) for c in found} == {
            ("POST", "/rest/web3/submitKey"),
        }

    asyncio.run(run())


def test_dispatcher_rejects_mime_size_and_scope():
    async def run():
        dispatcher, candidates, _, trace = _dispatcher(js_max_bytes=4)

        await dispatcher.dispatch_capture(_cap(body="fetch('/api/users')"))
        await dispatcher.dispatch_capture(_cap(
            url="https://example.com/logo.png",
            body="binary",
            mime="image/png",
        ))
        await dispatcher.dispatch_capture(_cap(
            url="https://other.com/app.js",
            body="fetch('/api/other')",
        ))

        assert await candidates.pending_count() == 0
        assert dispatcher.stats.rejected["track1_size"] == 1
        assert dispatcher.stats.rejected["track1_mime"] == 1
        assert dispatcher.stats.rejected["track1_scope"] == 1
        assert [event.kind for event in await trace.events()] == [
            "dispatch_rejected",
            "dispatch_rejected",
            "dispatch_rejected",
        ]

    asyncio.run(run())


def test_dispatcher_dedupes_same_url_and_body():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher()
        cap = _cap()

        await dispatcher.dispatch_capture(cap)
        await dispatcher.dispatch_capture(cap)

        assert dispatcher.stats.accepted == 1
        assert dispatcher.stats.deduped == 1
        assert await candidates.pending_count() == 1

    asyncio.run(run())


def test_dispatcher_calls_reject_recorder():
    async def run():
        recorded = []
        candidates = CandidateStore()
        responses = ResponseStore()
        trace = DecisionTrace(scan_id=1)

        dispatcher = AnalyzerDispatcher(
            scope=Scope(ScopeConfig(include_domains=["example.com"])),
            candidates=candidates,
            responses=responses,
            trace=trace,
            on_reject=lambda reason, cap, page_url, payload: recorded.append(
                (reason, cap.url, page_url, payload),
            ),
        )

        await dispatcher.dispatch_capture(_cap(
            url="https://example.com/logo.png",
            body="binary",
            mime="image/png",
        ), page_url="https://example.com")

        assert recorded == [
            ("track1_mime", "https://example.com/logo.png", "https://example.com", None),
        ]

    asyncio.run(run())


def test_dispatcher_dispatch_many_applies_queue_backpressure():
    async def run():
        dispatcher, _, _, _ = _dispatcher(
            queue_max=1,
            concurrency=1,
            same_url_min_interval_sec=0,
        )
        caps = [
            _cap(
                url=f"https://example.com/app-{idx}.js",
                body=f"fetch('/api/users/{idx}')",
            )
            for idx in range(20)
        ]

        await dispatcher.dispatch_many(caps, page_url="https://example.com/")

        assert dispatcher.stats.rejected["track1_queue_full"] > 0

    asyncio.run(run())


def test_dispatcher_skips_vendor_js_without_api_markers_quickly():
    async def run():
        dispatcher, candidates, _, _ = _dispatcher(
            same_url_min_interval_sec=0,
        )
        vendor_body = "function ajax(){};" * 50_000

        await asyncio.wait_for(
            dispatcher.dispatch_capture(_cap(body=vendor_body)),
            timeout=1,
        )

        assert dispatcher.stats.accepted == 1
        assert await candidates.pending_count() == 0

    asyncio.run(run())
