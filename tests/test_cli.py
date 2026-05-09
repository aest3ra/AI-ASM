from pathlib import Path

import yaml
from sqlmodel import Session
from typer.testing import CliRunner

import ai_asm.cli as cli
from ai_asm.cli import (
    _default_db_path,
    _find_resume_scan,
    _resolve_scan_paths,
    _write_default_scan_outputs,
    app,
)
from ai_asm.normalizer.types import NormalizedEndpoint
from ai_asm.storage.db import Scan, open_db
from ai_asm.storage.repo import (
    record_flagged_item,
    save_endpoints,
    upsert_frontier_item,
    update_frontier_status,
)


runner = CliRunner()


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


def test_default_db_path_uses_timestamp_host_and_url_hash(monkeypatch):
    monkeypatch.setattr(cli, "_timestamp_for_filename", lambda: "20260509-124501")

    path = _default_db_path("https://www.catholic.ac.kr/ko/index.do")

    assert path.parent == Path("runs")
    assert path.name.startswith("20260509-124501_www-catholic-ac-kr_")
    assert path.suffix == ".db"
    assert len(path.stem.rsplit("_", 1)[1]) == 6


def test_resolve_scan_paths_defaults_out_dir_to_db_stem(monkeypatch):
    monkeypatch.setattr(cli, "_timestamp_for_filename", lambda: "20260509-124501")

    db_path, out_dir = _resolve_scan_paths(
        "http://localhost:3000/",
        db_path=None,
        out_dir=None,
        resume=None,
    )

    assert db_path.name.startswith("20260509-124501_localhost-3000_")
    assert out_dir == db_path.with_suffix("")


def test_resolve_scan_paths_keeps_explicit_db_accumulation():
    db_path, out_dir = _resolve_scan_paths(
        "https://x.test/",
        db_path=Path("manual.db"),
        out_dir=None,
        resume=None,
    )

    assert db_path == Path("manual.db")
    assert out_dir == Path("manual")


def test_resolve_scan_paths_avoids_out_dir_collision_for_db_without_suffix():
    db_path, out_dir = _resolve_scan_paths(
        "https://x.test/",
        db_path=Path("manual"),
        out_dir=None,
        resume=None,
    )

    assert db_path == Path("manual")
    assert out_dir == Path("manual-artifacts")


def test_export_command_writes_openapi_yaml(tmp_path: Path):
    db_path = tmp_path / "asm.db"
    out_path = tmp_path / "api.yaml"
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = _scan(session, "https://x.test")
        save_endpoints(
            session,
            scan.id,
            [NormalizedEndpoint(
                method="GET",
                host="x.test",
                path_template="/api/ping",
                sample_url="https://x.test/api/ping",
                seen_count=1,
            )],
        )

    result = runner.invoke(app, ["export", str(db_path), "-o", str(out_path)])

    assert result.exit_code == 0
    assert yaml.safe_load(out_path.read_text())["paths"]["/api/ping"]["get"]


def test_flagged_command_writes_yaml_export(tmp_path: Path):
    db_path = tmp_path / "asm.db"
    out_path = tmp_path / "flagged.yaml"
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = _scan(session, "https://x.test")
        record_flagged_item(
            session,
            scan_id=scan.id,
            flag_kind="track1_scope",
            item_kind="url",
            url="https://outside.test/api",
            description="out of scope",
        )

    result = runner.invoke(
        app,
        ["flagged", str(db_path), "--export", "yaml", "-o", str(out_path)],
    )

    assert result.exit_code == 0
    rows = yaml.safe_load(out_path.read_text())
    assert rows[0]["flag_kind"] == "track1_scope"


def test_login_help_shows_short_out_alias():
    result = runner.invoke(app, ["login", "--help"])

    assert result.exit_code == 0
    assert "-o" in result.output
    assert "--out" in result.output


def test_default_scan_outputs_write_openapi_and_flagged_files(tmp_path: Path):
    db_path = tmp_path / "asm.db"
    out_dir = tmp_path / "run"
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = _scan(session, "https://x.test")
        save_endpoints(
            session,
            scan.id,
            [NormalizedEndpoint(
                method="GET",
                host="x.test",
                path_template="/api/ping",
                sample_url="https://x.test/api/ping",
                seen_count=1,
            )],
        )
        record_flagged_item(
            session,
            scan_id=scan.id,
            flag_kind="agent_budget",
            description="step budget exceeded",
        )

    outputs = _write_default_scan_outputs(db_path, out_dir)

    assert yaml.safe_load(outputs["openapi"].read_text())["paths"]["/api/ping"]["get"]
    assert yaml.safe_load(outputs["flagged_yaml"].read_text())[0]["flag_kind"] == "agent_budget"
    flagged_sh = outputs["flagged_curl"]
    assert "Generated by ai-asm flagged" in flagged_sh.read_text()
    assert flagged_sh.stat().st_mode & 0o111
