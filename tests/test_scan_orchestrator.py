import asyncio

from sqlmodel import Session, select

from orbis.config import ScanConfig
from orbis.crawler.types import CapturedRequest
from orbis.normalizer import normalize
from orbis.scan.orchestrator import ScanOrchestrator
from orbis.shared.candidate_store import CandidateStore
from orbis.storage.db import Endpoint, Scan, ScanSummary, open_db


def test_orchestrator_saves_page_captures_by_provenance(tmp_path):
    engine = open_db(tmp_path / "scan.db")
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)
        scan_id = scan.id

    orchestrator = ScanOrchestrator(
        config=ScanConfig.model_validate({"target": "https://example.com/"}),
        db_path=tmp_path / "scan.db",
        out_dir=tmp_path / "captures",
        headless=True,
    )
    captures = [
        CapturedRequest(
            request_id="cdp",
            method="GET",
            url="https://example.com/api/users",
            resource_type="XHR",
            source="cdp",
            response_status=200,
            response_mime="application/json",
            response_body='{"users": [{"id": 1}]}',
        ),
        CapturedRequest(
            request_id="probe",
            method="GET",
            url="https://example.com/api/admin",
            resource_type="Fetch",
            source="static_probe",
            response_status=200,
            response_mime="application/json",
        ),
    ]

    asyncio.run(orchestrator._save_captures(
        engine,
        scan_id,
        CandidateStore(),
        captures,
    ))

    with Session(engine) as session:
        rows = session.exec(select(Endpoint)).all()

    assert {(row.path_template, row.provenance) for row in rows} == {
        ("/api/users", "cdp_capture"),
        ("/api/admin", "static_probe"),
    }
    users = next(row for row in rows if row.path_template == "/api/users")
    assert users.response_schema_json is not None
    assert '"users"' in users.response_schema_json


def test_orchestrator_finish_scan_saves_agent_token_summary(tmp_path):
    engine = open_db(tmp_path / "scan.db")
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)
        scan_id = scan.id

    trace_path = tmp_path / "orbis-trace-1.jsonl"
    trace_path.write_text(
        '{"ts":1,"scan_id":1,"page_url":"https://example.com/",'
        '"kind":"agent_turn","payload":{"source":"llm",'
        '"tool_calls":["click_ref"],"input_tokens":100,'
        '"output_tokens":9,"cache_read_input_tokens":25}}\n'
    )
    orchestrator = ScanOrchestrator(
        config=ScanConfig.model_validate({"target": "https://example.com/"}),
        db_path=tmp_path / "scan.db",
        out_dir=tmp_path / "captures",
        headless=True,
    )
    captures = [
        CapturedRequest(
            request_id="api",
            method="GET",
            url="https://example.com/api/users",
            resource_type="XHR",
            source="cdp",
            response_status=200,
            response_mime="application/json",
        ),
    ]
    endpoints = normalize(captures, api_only=True)

    orchestrator._finish_scan(
        engine,
        scan_id,
        diag=type("Diag", (), {"pages_crawled": 1, "pages_failed": 0})(),
        endpoints=endpoints,
        static_candidates=[],
        url_surfaces=[],
        probed_urls=set(),
        probe_errors={},
        elapsed=1.5,
        trace_path=trace_path,
    )

    with Session(engine) as session:
        summary = session.get(ScanSummary, scan_id)

    assert summary is not None
    assert summary.tokens_input == 100
    assert summary.tokens_output == 9
    assert summary.cache_hit_input == 25
    assert summary.cache_hit_rate == 0.25
