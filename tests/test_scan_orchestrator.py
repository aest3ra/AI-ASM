import asyncio

from sqlmodel import Session, select

from ai_asm.config import ScanConfig
from ai_asm.crawler.types import CapturedRequest
from ai_asm.scan.orchestrator import ScanOrchestrator
from ai_asm.shared.candidate_store import CandidateStore
from ai_asm.storage.db import Endpoint, Scan, open_db


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
