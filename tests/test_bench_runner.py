from sqlmodel import Session

from bench.run_bench import (
    AgentMetrics,
    BenchResult,
    CoverageResult,
    DbMetrics,
    ThresholdResult,
    compute_coverage,
    result_markdown,
    scan_command,
    select_targets,
)
from ai_asm.storage.db import Endpoint, StaticCandidate, open_db


def test_bench_select_targets_requires_explicit_selection():
    config = {"targets": {"juice": {}, "crapi": {}, "disabled": {"enabled": False}}}

    assert select_targets(config, targets=["juice"], all_targets=False) == ["juice"]
    assert select_targets(config, targets=None, all_targets=True) == ["juice", "crapi"]
    assert select_targets(config, targets=["disabled"], all_targets=False) == ["disabled"]


def test_bench_scan_command_includes_core_overrides(tmp_path):
    target = {
        "scan_config": "examples/crapi_scan_config.yaml",
        "form_data": "testdata/forms/default.yaml",
        "agent": "llm",
        "agent_budget": 24,
    }

    command = scan_command(
        target,
        db_path=tmp_path / "scan.db",
        out_dir=tmp_path / "captures",
        agent="mock",
    )

    assert command[:4] == ["uv", "run", "ai-asm", "scan"]
    assert "--db" in command
    assert "--out" in command
    assert command[command.index("--agent") + 1] == "mock"
    assert command[command.index("--agent-budget") + 1] == "24"
    assert "--form-data" in command


def test_bench_path_list_coverage_uses_endpoint_and_static_candidates(tmp_path):
    db_path = tmp_path / "bench.db"
    expected_path = tmp_path / "expected.txt"
    expected_path.write_text("/api/Challenges\n/users/v1\n/users/missing\n")
    engine = open_db(db_path)
    with Session(engine) as session:
        session.add(Endpoint(
            scan_id=1,
            method="GET",
            host="localhost",
            path_template="/api/Challenges",
            sample_url="http://localhost/api/Challenges",
            first_seen_scan_id=1,
            last_seen_scan_id=1,
        ))
        session.add(StaticCandidate(
            scan_id=1,
            host="localhost",
            path_template="/users/v1",
            sample_url="http://localhost/users/v1",
            source_url="http://localhost/main.js",
        ))
        session.commit()

    coverage = compute_coverage(
        {"type": "path_list", "expected": expected_path},
        db_path,
        scan_id=1,
    )

    assert coverage.expected == 3
    assert coverage.discovered == 2
    assert coverage.covered == 2
    assert coverage.percent == 66.7
    assert coverage.missing == ["/users/missing"]


def test_bench_result_markdown_contains_threshold_failures():
    result = BenchResult(
        target="fixture",
        description="",
        started_at="2026-01-01T00:00:00Z",
        elapsed_seconds=1.2,
        scan_id=1,
        command=["ai-asm"],
        run_dir="bench/results/fixture",
        db_path="bench/results/fixture/scan.db",
        out_dir="bench/results/fixture/captures",
        scan_log="bench/results/fixture/scan.log",
        trace_path=None,
        db=DbMetrics(endpoints=2, static_candidates=1),
        coverage=CoverageResult(
            type="path_list",
            expected=2,
            discovered=2,
            covered=1,
            percent=50.0,
            missing=["/api/missing"],
        ),
        agent=AgentMetrics(llm_turns=3, local_planner_turns=2, input_tokens=10),
        thresholds=ThresholdResult(ok=False, failures=["coverage 50.0% < 90.0%"]),
    )

    markdown = result_markdown(result)

    assert "## fixture [FAIL]" in markdown
    assert "coverage: 1/2 (50.0%)" in markdown
    assert "`/api/missing`" in markdown
    assert "coverage 50.0% < 90.0%" in markdown
