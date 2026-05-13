from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from orbis.normalizer.pipeline import canonical_api_path
from orbis.storage.db import Endpoint, StaticCandidate, open_db

PLACEHOLDER_RE = re.compile(r"\{[^/{}]+\}")
API_MARKER_RE = re.compile(r"/(?:api|rest|graphql|b2b)(?=/|$)", re.IGNORECASE)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare orbis path discovery with an OpenAPI spec.",
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--scan-id", type=int)
    args = parser.parse_args()

    expected = read_expected_paths(Path(args.spec))
    discovered = read_discovered_paths(Path(args.db), scan_id=args.scan_id)
    covered = expected & discovered
    missing = expected - discovered
    extra = discovered - expected
    pct = (len(covered) / len(expected) * 100) if expected else 0.0

    print(
        f"expected={len(expected)} discovered={len(discovered)} "
        f"covered={len(covered)} coverage={pct:.1f}%"
    )
    if missing:
        print("\nmissing:")
        for path in sorted(missing):
            print(f"  {path}")
    if extra:
        print("\nextra:")
        for path in sorted(extra):
            print(f"  {path}")


def read_expected_paths(path: Path) -> set[str]:
    spec = _read_spec(path)
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return set()
    return {
        canonical_coverage_path(raw_path)
        for raw_path in paths
        if API_MARKER_RE.search(raw_path)
    }


def read_discovered_paths(db_path: Path, *, scan_id: int | None) -> set[str]:
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
        if API_MARKER_RE.search(row.path_template)
    }


def canonical_coverage_path(path: str) -> str:
    return PLACEHOLDER_RE.sub("{id}", canonical_api_path(path))


def _read_spec(path: Path) -> dict[str, Any]:
    text = path.read_text()
    return json.loads(text)


if __name__ == "__main__":
    main()
