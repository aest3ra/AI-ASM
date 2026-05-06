from pathlib import Path

from sqlmodel import Session

from ai_asm.cli import _find_resume_scan
from ai_asm.storage.db import Scan, open_db
from ai_asm.storage.repo import upsert_frontier_item, update_frontier_status


def _scan(session: Session, target: str, auth: str | None = None) -> Scan:
    scan = Scan(target=target, auth_state_path=auth)
    session.add(scan)
    session.commit()
    session.refresh(scan)
    return scan


def test_find_resume_scan_uses_latest_scan_with_pending_frontier(tmp_path: Path):
    engine = open_db(tmp_path / "resume.db")
    with Session(engine) as session:
        done_scan = _scan(session, "https://x.test", None)
        done_item = upsert_frontier_item(
            session,
            scan_id=done_scan.id,
            url="https://x.test/",
        )
        update_frontier_status(session, done_item.id, "done")

        pending_scan = _scan(session, "https://x.test", None)
        upsert_frontier_item(
            session,
            scan_id=pending_scan.id,
            url="https://x.test/dashboard",
        )

        resumed = _find_resume_scan(session, "https://x.test", None)

    assert resumed.id == pending_scan.id


def test_find_resume_scan_requires_matching_auth(tmp_path: Path):
    engine = open_db(tmp_path / "resume.db")
    with Session(engine) as session:
        scan = _scan(session, "https://x.test", "admin.json")
        upsert_frontier_item(
            session,
            scan_id=scan.id,
            url="https://x.test/admin",
        )

        assert _find_resume_scan(session, "https://x.test", None) is None
