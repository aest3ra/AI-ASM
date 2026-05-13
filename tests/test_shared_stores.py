import asyncio
import json

from orbis.shared.candidate_store import CandidateEndpoint, CandidateStore
from orbis.shared.decision_trace import DecisionTrace
from orbis.shared.facade import RegistryFacade
from orbis.shared.response_store import ResponseStore
from orbis.shared.verified_store import VerifiedEndpoint, VerifiedStore


def test_candidate_store_dedupes_and_prioritizes_pending():
    async def run():
        store = CandidateStore()
        candidate = CandidateEndpoint(
            method="GET",
            url="https://example.com/api/users/1",
            host="example.com",
            path_template="/api/users/{id}",
            source_url="https://example.com/app.js",
            source_kind="static_js",
        )

        added = await store.add(candidate)
        duplicate = await store.add(candidate)

        assert added is True
        assert duplicate is False
        assert await store.pending_count() == 1
        assert [c.path_template for c in await store.top_n(5)] == [
            "/api/users/{id}",
        ]

    asyncio.run(run())


def test_registry_facade_summarizes_stores():
    async def run():
        candidates = CandidateStore()
        verified = VerifiedStore()
        responses = ResponseStore()
        trace = DecisionTrace(scan_id=7)

        await candidates.add(CandidateEndpoint(
            method="GET",
            url="https://example.com/api/users",
            host="example.com",
            path_template="/api/users",
            source_url="https://example.com/app.js",
            source_kind="static_js",
        ))
        await verified.mark(VerifiedEndpoint(
            method="POST",
            url="https://example.com/api/orders",
            host="example.com",
            path_template="/api/orders",
            page_url="https://example.com/cart",
            provenance="cdp_capture",
        ))
        await responses.observe(
            method="GET",
            url="https://example.com/api/users",
            status=200,
            mime="application/json",
            body='{"id": 1}',
        )
        await trace.log_dispatch(
            page_url="https://example.com",
            accepted=True,
            reason="accepted",
            url="https://example.com/app.js",
        )

        summary = await RegistryFacade(
            candidates=candidates,
            verified=verified,
            responses=responses,
            trace=trace,
        ).summary("example.com")

        assert summary["host"] == "example.com"
        assert summary["candidates_unverified"] == 1
        assert summary["verified_endpoints"] == 1
        assert summary["response_samples"] == 1
        assert summary["trace_events"] == 1

    asyncio.run(run())


def test_response_store_infers_schema_for_json_samples():
    async def run():
        store = ResponseStore()
        await store.observe(
            method="GET",
            url="https://example.com/api/users",
            status=200,
            mime="application/json",
            body='{"id": 1}',
        )
        await store.observe(
            method="GET",
            url="https://example.com/api/users",
            status=200,
            mime="application/json",
            body='{"id": 2, "name": "alice"}',
        )

        schema = await store.schema_for("GET", "https://example.com/api/users")

        assert schema["properties"]["id"] == {"type": "integer"}
        assert schema["properties"]["name"] == {"type": "string"}
        assert schema["required"] == ["id"]

    asyncio.run(run())


def test_decision_trace_writes_jsonl(tmp_path):
    async def run():
        trace_path = tmp_path / "trace.jsonl"
        trace = DecisionTrace(scan_id=42, path=trace_path)

        await trace.log_turn(
            page_url="https://example.com/",
            payload={"tool_calls": ["scroll"]},
        )

        rows = [
            json.loads(line)
            for line in trace_path.read_text().splitlines()
        ]
        assert len(rows) == 1
        assert rows[0]["scan_id"] == 42
        assert rows[0]["kind"] == "agent_turn"
        assert rows[0]["payload"] == {"tool_calls": ["scroll"]}

    asyncio.run(run())
