from __future__ import annotations

import argparse
import re
from pathlib import Path

from sqlmodel import Session, select

from ai_asm.normalizer.pipeline import canonical_api_path
from ai_asm.storage.db import Endpoint, StaticCandidate, open_db

PLACEHOLDER_RE = re.compile(r"\{[^/{}]+\}")

PUBLIC_UNAUTH_PATHS = {
    "/api/Challenges",
    "/api/Deliverys",
    "/api/Deliverys/{id}",
    "/api/Feedbacks",
    "/api/Hints",
    "/api/Products",
    "/api/Products/{id}",
    "/api/Quantitys",
    "/api/Recycles",
    "/api/Recycles/{id}",
    "/api/SecurityAnswers",
    "/api/SecurityQuestions",
    "/api/Users",
    "/rest/2fa/verify",
    "/rest/admin/application-configuration",
    "/rest/admin/application-version",
    "/rest/captcha",
    "/rest/chatbot/respond",
    "/rest/chatbot/status",
    "/rest/continue-code",
    "/rest/continue-code-findIt",
    "/rest/continue-code-findIt/apply/{continueCode}",
    "/rest/continue-code-fixIt",
    "/rest/continue-code-fixIt/apply/{continueCode}",
    "/rest/continue-code/apply/{continueCode}",
    "/rest/country-mapping",
    "/rest/deluxe-membership",
    "/rest/languages",
    "/rest/memories",
    "/rest/products/search",
    "/rest/products/{id}/reviews",
    "/rest/repeat-notification",
    "/rest/track-order/{id}",
    "/rest/user/change-password",
    "/rest/user/login",
    "/rest/user/reset-password",
    "/rest/user/security-question",
    "/rest/user/whoami",
    "/rest/web3/nftMintListen",
    "/rest/web3/nftUnlocked",
    "/rest/web3/submitKey",
    "/rest/web3/walletExploitAddress",
    "/rest/web3/walletNFTVerify",
}

PUBLIC_UNAUTH_GET_PATHS = {
    "/api/Challenges",
    "/api/Deliverys",
    "/api/Deliverys/{id}",
    "/api/Feedbacks",
    "/api/Hints",
    "/api/Products",
    "/api/Products/{id}",
    "/api/Quantitys",
    "/api/Recycles",
    "/api/Recycles/{id}",
    "/api/SecurityQuestions",
    "/rest/admin/application-configuration",
    "/rest/admin/application-version",
    "/rest/captcha",
    "/rest/chatbot/status",
    "/rest/continue-code",
    "/rest/continue-code-findIt",
    "/rest/continue-code-fixIt",
    "/rest/country-mapping",
    "/rest/deluxe-membership",
    "/rest/languages",
    "/rest/memories",
    "/rest/products/search",
    "/rest/products/{id}/reviews",
    "/rest/repeat-notification",
    "/rest/track-order/{id}",
    "/rest/user/security-question",
    "/rest/user/whoami",
    "/rest/web3/nftMintListen",
    "/rest/web3/nftUnlocked",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare ai-asm results with the Juice Shop expected API path list.",
    )
    parser.add_argument("--db", default="asm.db")
    parser.add_argument(
        "--expected",
        default="tests/fixtures/juice_shop_expected_api_paths.txt",
    )
    parser.add_argument("--scan-id", type=int)
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Compare against unauthenticated Juice Shop API paths only.",
    )
    parser.add_argument(
        "--get-only",
        action="store_true",
        help="Compare against unauthenticated GET paths only.",
    )
    args = parser.parse_args()

    expected = filter_expected(
        read_expected(Path(args.expected)),
        public_only=args.public_only,
        get_only=args.get_only,
    )
    discovered = read_discovered(Path(args.db), scan_id=args.scan_id)
    covered = expected & discovered
    missing = expected - discovered
    extra = discovered - expected

    pct = (len(covered) / len(expected) * 100) if expected else 0.0
    print(f"expected={len(expected)} discovered={len(discovered)} covered={len(covered)} coverage={pct:.1f}%")
    if missing:
        print("\nmissing:")
        for path in sorted(missing):
            print(f"  {path}")
    if extra:
        print("\nextra:")
        for path in sorted(extra):
            print(f"  {path}")


def read_expected(path: Path) -> set[str]:
    return {
        canonical_coverage_path(line.strip())
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def read_discovered(db_path: Path, *, scan_id: int | None) -> set[str]:
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
        if row.path_template.startswith(("/api", "/rest", "/b2b"))
    }


def filter_expected(
    expected: set[str],
    *,
    public_only: bool,
    get_only: bool,
) -> set[str]:
    normalized = {canonical_coverage_path(path) for path in expected}
    if get_only:
        return normalized & {canonical_coverage_path(p) for p in PUBLIC_UNAUTH_GET_PATHS}
    if public_only:
        return normalized & {canonical_coverage_path(p) for p in PUBLIC_UNAUTH_PATHS}
    return normalized


def canonical_coverage_path(path: str) -> str:
    return PLACEHOLDER_RE.sub("{id}", canonical_api_path(path))


if __name__ == "__main__":
    main()
