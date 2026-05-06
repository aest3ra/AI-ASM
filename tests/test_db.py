import sqlite3
from pathlib import Path

from sqlmodel import Session, select

from ai_asm.normalizer.types import NormalizedEndpoint, NormalizedParameter
from ai_asm.normalizer.static import ApiCandidate
from ai_asm.storage.db import (
    Endpoint,
    FlaggedItem,
    FrontierState,
    Parameter,
    Scan,
    ScanSummary,
    StaticCandidate,
    open_db,
)
from ai_asm.storage.repo import (
    complete_frontier_item,
    load_pending_frontier,
    record_flagged_item,
    save_endpoints,
    save_scan_summary,
    save_static_candidates,
    update_frontier_status,
    upsert_frontier_item,
)


def test_create_and_query(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    with Session(engine) as s:
        scan = Scan(target="https://example.com")
        s.add(scan)
        s.commit()
        s.refresh(scan)

        ep = Endpoint(
            scan_id=scan.id,
            method="GET",
            host="example.com",
            path_template="/users/{id}",
            sample_url="https://example.com/users/42",
        )
        s.add(ep)
        s.commit()
        s.refresh(ep)

        s.add(Parameter(
            endpoint_id=ep.id, location="query", name="page",
            type_inferred="int", sample_values_json="[1,2]",
        ))
        s.commit()

        rows = s.exec(select(Endpoint)).all()
        assert len(rows) == 1
        assert rows[0].path_template == "/users/{id}"

        s.add(StaticCandidate(
            scan_id=scan.id,
            host="example.com",
            path_template="/api/users",
            sample_url="https://example.com/api/users",
            source_url="https://example.com/app.js",
            probed=True,
            observed=False,
        ))
        s.commit()

        candidates = s.exec(select(StaticCandidate)).all()
        assert len(candidates) == 1
        assert candidates[0].probed is True


def _scan(session: Session, target: str = "https://example.com", auth: str | None = None) -> Scan:
    scan = Scan(target=target, auth_state_path=auth)
    session.add(scan)
    session.commit()
    session.refresh(scan)
    return scan


def _normalized_endpoint(
    *,
    sample_url: str = "https://example.com/api/users?page=1",
    seen_count: int = 2,
    page_samples: list[str] | None = None,
) -> NormalizedEndpoint:
    ep = NormalizedEndpoint(
        method="GET",
        host="example.com",
        path_template="/api/users",
        sample_url=sample_url,
        seen_count=seen_count,
    )
    ep.parameters[("query", "page")] = NormalizedParameter(
        location="query",
        name="page",
        type_inferred="int",
        sample_values=page_samples or ["1"],
        seen_count=seen_count,
    )
    return ep


def test_phase0_save_endpoints_accumulates_same_auth_context(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    with Session(engine) as s:
        scan1 = _scan(s, auth="user.json")
        scan2 = _scan(s, auth="user.json")

        first = save_endpoints(
            s,
            scan1.id,
            [_normalized_endpoint(seen_count=2, page_samples=["1"])],
            auth_state_path="user.json",
            auth_label="user",
            provenance="cdp_capture",
        )
        second = save_endpoints(
            s,
            scan2.id,
            [_normalized_endpoint(
                sample_url="https://example.com/api/users?page=2",
                seen_count=3,
                page_samples=["2"],
            )],
            auth_state_path="user.json",
            auth_label="user",
            provenance="init_script",
        )

        assert (first.endpoints_added, first.endpoints_updated) == (1, 0)
        assert (second.endpoints_added, second.endpoints_updated) == (0, 1)

        rows = s.exec(select(Endpoint)).all()
        assert len(rows) == 1
        assert rows[0].seen_count == 5
        assert rows[0].first_seen_scan_id == scan1.id
        assert rows[0].last_seen_scan_id == scan2.id
        assert rows[0].auth_state_path == "user.json"

        params = s.exec(select(Parameter)).all()
        assert len(params) == 1
        assert params[0].seen_count == 5
        assert params[0].sample_values_json == '["1", "2"]'


def test_phase0_same_endpoint_is_separate_for_different_auth_contexts(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    with Session(engine) as s:
        user_scan = _scan(s, auth="user.json")
        admin_scan = _scan(s, auth="admin.json")

        save_endpoints(
            s,
            user_scan.id,
            [_normalized_endpoint()],
            auth_state_path="user.json",
            auth_label="user",
        )
        save_endpoints(
            s,
            admin_scan.id,
            [_normalized_endpoint()],
            auth_state_path="admin.json",
            auth_label="admin",
        )

        rows = s.exec(select(Endpoint)).all()
        assert len(rows) == 2
        assert {row.auth_state_path for row in rows} == {"user.json", "admin.json"}
        assert len({row.auth_context_id for row in rows}) == 2


def test_phase0_flagged_items_are_deduplicated(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    with Session(engine) as s:
        scan = _scan(s, auth="user.json")

        first = record_flagged_item(
            s,
            scan_id=scan.id,
            flag_kind="agent_blacklist",
            item_kind="click",
            url="https://example.com/logout",
            description="Click on Sign Out",
            page_url="https://example.com/dashboard",
            auth_state_path="user.json",
        )
        second = record_flagged_item(
            s,
            scan_id=scan.id,
            flag_kind="agent_blacklist",
            item_kind="click",
            url="https://example.com/logout",
            description="Click on Sign Out",
            page_url="https://example.com/dashboard",
            auth_state_path="user.json",
        )

        assert first.id == second.id
        assert len(s.exec(select(FlaggedItem)).all()) == 1


def test_phase0_frontier_and_scan_summary_helpers(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    with Session(engine) as s:
        scan = _scan(s, auth="user.json")

        item = upsert_frontier_item(
            s,
            scan_id=scan.id,
            url="https://example.com/dashboard",
            dom_signature="abc123",
        )
        same = upsert_frontier_item(
            s,
            scan_id=scan.id,
            url="https://example.com/dashboard",
            dom_signature="abc123",
        )

        assert item.id == same.id
        assert len(s.exec(select(FrontierState)).all()) == 1
        assert [row.id for row in load_pending_frontier(s, scan.id)] == [item.id]

        update_frontier_status(s, item.id, "done")
        assert load_pending_frontier(s, scan.id) == []

        second = upsert_frontier_item(
            s,
            scan_id=scan.id,
            url="https://example.com/settings",
        )
        complete_frontier_item(
            s,
            second.id,
            status="done",
            dom_signature="sig-settings",
        )
        completed = s.get(FrontierState, second.id)
        assert completed.dom_signature == "sig-settings"
        assert completed.status == "done"
        assert completed.completed_at is not None

        save_scan_summary(
            s,
            scan_id=scan.id,
            auth_state_path="user.json",
            pages_visited=3,
            endpoints_added=2,
            summary_text="phase 0 smoke",
        )
        save_scan_summary(
            s,
            scan_id=scan.id,
            auth_state_path="user.json",
            pages_visited=4,
            endpoints_updated=1,
        )

        summaries = s.exec(select(ScanSummary)).all()
        assert len(summaries) == 1
        assert summaries[0].pages_visited == 4
        assert summaries[0].endpoints_added == 2
        assert summaries[0].endpoints_updated == 1


def test_save_static_candidates_is_idempotent_for_resume(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    candidate = ApiCandidate(
        host="example.com",
        path_template="/api/users",
        sample_url="https://example.com/api/users",
        source_url="https://example.com/app.js",
    )

    with Session(engine) as s:
        scan = _scan(s)
        save_static_candidates(
            s,
            scan.id,
            [candidate],
            observed_keys=set(),
            probed_urls={candidate.sample_url},
        )
        save_static_candidates(
            s,
            scan.id,
            [candidate],
            observed_keys={(candidate.host, candidate.path_template)},
            probe_errors={candidate.sample_url: "timeout"},
        )

        rows = s.exec(select(StaticCandidate)).all()
        assert len(rows) == 1
        assert rows[0].probed is True
        assert rows[0].observed is True
        assert rows[0].probe_error == "timeout"


def test_phase0_open_db_migrates_v1_tables(tmp_path: Path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE scan ("
        "id INTEGER PRIMARY KEY, target TEXT NOT NULL, started_at TIMESTAMP, "
        "finished_at TIMESTAMP, pages_crawled INTEGER, endpoints_found INTEGER)"
    )
    conn.execute(
        "CREATE TABLE endpoint ("
        "id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL, method TEXT NOT NULL, "
        "host TEXT NOT NULL, path_template TEXT NOT NULL, sample_url TEXT NOT NULL, "
        "seen_count INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()

    engine = open_db(db_path)
    with engine.connect() as c:
        scan_cols = {
            row[1]
            for row in c.exec_driver_sql("PRAGMA table_info(scan)").fetchall()
        }
        endpoint_cols = {
            row[1]
            for row in c.exec_driver_sql("PRAGMA table_info(endpoint)").fetchall()
        }

    assert {"auth_state_path", "schema_version"} <= scan_cols
    assert {
        "auth_context_id",
        "provenance",
        "response_schema_json",
        "auth_state_path",
        "first_seen_scan_id",
        "last_seen_scan_id",
    } <= endpoint_cols
