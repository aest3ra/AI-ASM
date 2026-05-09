from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.juice_shop_coverage import (  # noqa: E402
    filter_expected as filter_juice_expected,
)
from scripts.juice_shop_coverage import read_discovered as read_juice_discovered  # noqa: E402
from scripts.juice_shop_coverage import read_expected as read_juice_expected  # noqa: E402
from scripts.openapi_path_coverage import (  # noqa: E402
    canonical_coverage_path,
    read_discovered_paths as read_openapi_discovered,
)
from scripts.openapi_path_coverage import read_expected_paths as read_openapi_expected  # noqa: E402
from sqlmodel import Session, func, select  # noqa: E402

from ai_asm.agent.behavior import analyze_trace_file  # noqa: E402
from ai_asm.storage.db import Endpoint, Scan, StaticCandidate, open_db  # noqa: E402


@dataclass
class CoverageResult:
    type: str
    expected: int = 0
    discovered: int = 0
    covered: int = 0
    percent: float = 0.0
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


@dataclass
class AgentMetrics:
    turns: int = 0
    llm_turns: int = 0
    local_planner_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    actions_with_api_new_requests: int = 0
    failed_tool_calls: int = 0
    timeout_failures: int = 0
    tool_requested: dict[str, int] = field(default_factory=dict)


@dataclass
class DbMetrics:
    endpoints: int = 0
    static_candidates: int = 0
    methods: dict[str, int] = field(default_factory=dict)
    provenance: dict[str, int] = field(default_factory=dict)


@dataclass
class ThresholdResult:
    ok: bool = True
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BenchResult:
    target: str
    description: str
    started_at: str
    elapsed_seconds: float
    scan_id: int | None
    command: list[str]
    run_dir: str
    db_path: str
    out_dir: str
    scan_log: str
    trace_path: str | None
    db: DbMetrics
    coverage: CoverageResult
    agent: AgentMetrics
    thresholds: ThresholdResult


class BenchError(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ai-asm benchmark targets.")
    parser.add_argument("--config", type=Path, default=Path("bench/targets.yaml"))
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--agent", choices=("planner", "mock", "llm"))
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--skip-scan", action="store_true")
    parser.add_argument("--fail-on-threshold", action="store_true")
    args = parser.parse_args()

    config = load_targets(args.config)
    selected = select_targets(config, targets=args.targets, all_targets=args.all)
    results: list[BenchResult] = []
    failed = False
    for name in selected:
        result = run_target(name, config["targets"][name], args)
        results.append(result)
        print(result_markdown(result))
        if not result.thresholds.ok:
            failed = True

    summary_path = write_index(args.results_dir, results)
    print(f"\nbenchmark summary: {summary_path}")
    if failed and args.fail_on_threshold:
        raise SystemExit(2)


def load_targets(path: str | Path) -> dict[str, Any]:
    resolved = resolve_path(path)
    raw = yaml.safe_load(resolved.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("targets"), dict):
        raise BenchError(f"invalid benchmark config: {resolved}")
    return raw


def select_targets(
    config: dict[str, Any],
    *,
    targets: list[str] | None,
    all_targets: bool,
) -> list[str]:
    available = list(config["targets"].keys())
    if all_targets:
        return [
            name
            for name in available
            if config["targets"][name].get("enabled", True) is not False
        ]
    if targets:
        unknown = sorted(set(targets) - set(available))
        if unknown:
            raise BenchError(f"unknown target(s): {', '.join(unknown)}")
        return targets
    raise BenchError("choose --target NAME or --all")


def run_target(name: str, target: dict[str, Any], args) -> BenchResult:
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    run_dir = resolve_path(args.results_dir) / f"{name}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "scan.db"
    out_dir = run_dir / "captures"
    scan_log = run_dir / "scan.log"

    if not args.skip_health:
        check_health(str(target.get("health_url") or ""))
    check_required_files(target)

    command = scan_command(target, db_path=db_path, out_dir=out_dir, agent=args.agent)
    started_monotonic = time.monotonic()
    if not args.skip_scan:
        run_streamed(command, log_path=scan_log)
    elapsed = time.monotonic() - started_monotonic

    scan_id = latest_scan_id(db_path)
    trace_path = out_dir / f"trace-{scan_id}.jsonl" if scan_id is not None else None
    db_metrics = collect_db_metrics(db_path, scan_id=scan_id)
    coverage = compute_coverage(target.get("coverage") or {}, db_path, scan_id=scan_id)
    agent = collect_agent_metrics(trace_path)
    thresholds = evaluate_thresholds(target.get("thresholds") or {}, coverage, agent)

    result = BenchResult(
        target=name,
        description=str(target.get("description") or ""),
        started_at=started.isoformat(),
        elapsed_seconds=elapsed,
        scan_id=scan_id,
        command=command,
        run_dir=str(run_dir.relative_to(ROOT)),
        db_path=str(db_path.relative_to(ROOT)),
        out_dir=str(out_dir.relative_to(ROOT)),
        scan_log=str(scan_log.relative_to(ROOT)),
        trace_path=str(trace_path.relative_to(ROOT)) if trace_path else None,
        db=db_metrics,
        coverage=coverage,
        agent=agent,
        thresholds=thresholds,
    )
    write_result_files(run_dir, result)
    return result


def scan_command(
    target: dict[str, Any],
    *,
    db_path: Path,
    out_dir: Path,
    agent: str | None,
) -> list[str]:
    scan_config = write_bench_scan_config(
        target,
        run_dir=db_path.parent,
        agent=agent,
    )
    command = [
        "uv",
        "run",
        "ai-asm",
        "scan",
        str(scan_config),
        "--db",
        str(db_path),
        "--out",
        str(out_dir),
    ]
    if target.get("auth"):
        command.extend(["--auth", str(resolve_path(target["auth"]))])
    return command


def write_bench_scan_config(
    target: dict[str, Any],
    *,
    run_dir: Path,
    agent: str | None,
) -> Path:
    source = resolve_path(target["scan_config"])
    raw = yaml.safe_load(source.read_text()) or {}
    if not isinstance(raw, dict):
        raise BenchError(f"invalid scan config: {source}")

    if target.get("static_probe_auth"):
        raw["static_probe_auth"] = str(target["static_probe_auth"])

    agent_config = dict(raw.get("agent") or {})
    agent_config["mode"] = agent or str(target.get("agent") or agent_config.get("mode") or "planner")
    if target.get("agent_budget"):
        agent_config["max_steps_per_page"] = int(target["agent_budget"])
    if target.get("form_data"):
        agent_config["form_data_path"] = str(resolve_path(target["form_data"]))
    raw["agent"] = agent_config

    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "scan.config.yaml"
    out_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return out_path


def check_health(url: str) -> None:
    if not url:
        return
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            if response.status >= 500:
                raise BenchError(f"health check failed {url}: HTTP {response.status}")
    except Exception as exc:
        raise BenchError(f"health check failed {url}: {type(exc).__name__}") from exc


def check_required_files(target: dict[str, Any]) -> None:
    required = list(target.get("required_files") or [])
    if target.get("form_data"):
        required.append(str(target["form_data"]))
    if target.get("scan_config"):
        required.append(str(target["scan_config"]))
    missing = [
        str(resolve_path(path).relative_to(ROOT))
        for path in required
        if not resolve_path(path).exists()
    ]
    if missing:
        raise BenchError(f"missing required file(s): {', '.join(missing)}")


def run_streamed(command: list[str], *, log_path: Path) -> None:
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        code = process.wait()
    if code:
        raise subprocess.CalledProcessError(code, command)


def latest_scan_id(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    engine = open_db(db_path)
    with Session(engine) as session:
        return session.exec(select(func.max(Scan.id))).one()


def collect_db_metrics(db_path: Path, *, scan_id: int | None) -> DbMetrics:
    if scan_id is None or not db_path.exists():
        return DbMetrics()
    engine = open_db(db_path)
    with Session(engine) as session:
        endpoints = session.exec(
            select(Endpoint).where(Endpoint.last_seen_scan_id == scan_id),
        ).all()
        candidates = session.exec(
            select(StaticCandidate).where(StaticCandidate.scan_id == scan_id),
        ).all()

    methods: dict[str, int] = {}
    provenance: dict[str, int] = {}
    for endpoint in endpoints:
        methods[endpoint.method] = methods.get(endpoint.method, 0) + 1
        key = endpoint.provenance or "unknown"
        provenance[key] = provenance.get(key, 0) + 1
    return DbMetrics(
        endpoints=len(endpoints),
        static_candidates=len(candidates),
        methods=methods,
        provenance=provenance,
    )


def compute_coverage(
    coverage_cfg: dict[str, Any],
    db_path: Path,
    *,
    scan_id: int | None,
) -> CoverageResult:
    cov_type = str(coverage_cfg.get("type") or "none")
    if cov_type == "none":
        return CoverageResult(type=cov_type, skipped_reason="coverage not configured")
    try:
        if cov_type == "juice_shop":
            expected_path = resolve_path(coverage_cfg["expected"])
            expected = filter_juice_expected(
                read_juice_expected(expected_path),
                public_only=bool(coverage_cfg.get("public_only")),
                get_only=bool(coverage_cfg.get("get_only")),
            )
            discovered = read_juice_discovered(db_path, scan_id=scan_id)
        elif cov_type == "openapi":
            spec_path = resolve_path(coverage_cfg["spec"])
            if not spec_path.exists():
                return CoverageResult(
                    type=cov_type,
                    skipped_reason=f"missing OpenAPI spec: {spec_path.relative_to(ROOT)}",
                )
            expected = read_openapi_expected(spec_path)
            discovered = read_openapi_discovered(db_path, scan_id=scan_id)
        elif cov_type == "path_list":
            expected_path = resolve_path(coverage_cfg["expected"])
            expected = read_path_list(expected_path)
            discovered = read_discovered_paths_all(db_path, scan_id=scan_id)
        else:
            return CoverageResult(
                type=cov_type,
                skipped_reason=f"unknown coverage type: {cov_type}",
            )
    except Exception as exc:
        return CoverageResult(
            type=cov_type,
            skipped_reason=f"coverage failed: {type(exc).__name__}",
        )

    covered = expected & discovered
    missing = expected - discovered
    extra = discovered - expected
    percent = (len(covered) / len(expected) * 100) if expected else 0.0
    return CoverageResult(
        type=cov_type,
        expected=len(expected),
        discovered=len(discovered),
        covered=len(covered),
        percent=round(percent, 1),
        missing=sorted(missing),
        extra=sorted(extra),
    )


def read_path_list(path: Path) -> set[str]:
    return {
        canonical_coverage_path(line.strip())
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def read_discovered_paths_all(db_path: Path, *, scan_id: int | None) -> set[str]:
    engine = open_db(db_path)
    with Session(engine) as session:
        endpoint_query = select(Endpoint)
        candidate_query = select(StaticCandidate)
        if scan_id is not None:
            endpoint_query = endpoint_query.where(Endpoint.last_seen_scan_id == scan_id)
            candidate_query = candidate_query.where(StaticCandidate.scan_id == scan_id)
        endpoints = session.exec(endpoint_query).all()
        candidates = session.exec(candidate_query).all()

    return {
        canonical_coverage_path(row.path_template)
        for row in [*endpoints, *candidates]
    }


def collect_agent_metrics(trace_path: Path | None) -> AgentMetrics:
    if trace_path is None or not trace_path.exists():
        return AgentMetrics()
    summary = analyze_trace_file(trace_path)
    return AgentMetrics(
        turns=summary.turns,
        llm_turns=summary.turns - summary.local_planner_turns,
        local_planner_turns=summary.local_planner_turns,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        cache_read_input_tokens=summary.cache_read_input_tokens,
        actions_with_api_new_requests=summary.actions_with_api_new_requests,
        failed_tool_calls=summary.failed_tool_calls,
        timeout_failures=summary.timeout_failures,
        tool_requested=dict(summary.tool_requested),
    )


def evaluate_thresholds(
    thresholds: dict[str, Any],
    coverage: CoverageResult,
    agent: AgentMetrics,
) -> ThresholdResult:
    result = ThresholdResult()
    coverage_min = thresholds.get("coverage_min")
    if coverage_min is not None and coverage.skipped_reason:
        result.warnings.append(f"coverage threshold skipped: {coverage.skipped_reason}")
    elif coverage_min is not None and coverage.percent < float(coverage_min):
        result.ok = False
        result.failures.append(
            f"coverage {coverage.percent:.1f}% < {float(coverage_min):.1f}%",
        )

    max_input_tokens = thresholds.get("max_input_tokens")
    if max_input_tokens is not None and agent.input_tokens > int(max_input_tokens):
        result.ok = False
        result.failures.append(
            f"input_tokens {agent.input_tokens} > {int(max_input_tokens)}",
        )
    return result


def write_result_files(run_dir: Path, result: BenchResult) -> None:
    (run_dir / "result.json").write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False) + "\n",
    )
    (run_dir / "summary.md").write_text(result_markdown(result) + "\n")


def write_index(results_dir: Path, results: list[BenchResult]) -> Path:
    resolved = resolve_path(results_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    path = resolved / "latest.md"
    lines = ["# ai-asm Benchmark Summary", ""]
    for result in results:
        lines.append(result_markdown(result))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n")
    return path


def result_markdown(result: BenchResult) -> str:
    coverage = (
        result.coverage.skipped_reason
        if result.coverage.skipped_reason
        else (
            f"{result.coverage.covered}/{result.coverage.expected} "
            f"({result.coverage.percent:.1f}%)"
        )
    )
    status = "PASS" if result.thresholds.ok else "FAIL"
    lines = [
        f"## {result.target} [{status}]",
        "",
        f"- elapsed: {result.elapsed_seconds:.1f}s",
        f"- endpoints: {result.db.endpoints}",
        f"- static candidates: {result.db.static_candidates}",
        f"- coverage: {coverage}",
        f"- LLM turns: {result.agent.llm_turns}",
        f"- local planner turns: {result.agent.local_planner_turns}",
        f"- input/output tokens: {result.agent.input_tokens}/{result.agent.output_tokens}",
        f"- actions with API requests: {result.agent.actions_with_api_new_requests}",
        f"- run dir: `{result.run_dir}`",
    ]
    if result.coverage.missing:
        lines.append("- missing:")
        lines.extend(f"  - `{path}`" for path in result.coverage.missing[:20])
    if result.thresholds.failures:
        lines.append("- threshold failures:")
        lines.extend(f"  - {item}" for item in result.thresholds.failures)
    if result.thresholds.warnings:
        lines.append("- threshold warnings:")
        lines.extend(f"  - {item}" for item in result.thresholds.warnings)
    return "\n".join(lines)


def resolve_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


if __name__ == "__main__":
    main()
