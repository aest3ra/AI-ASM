"""Safe scan artifact writers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from orbis.crawler.types import CapturedRequest

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-csrf-token",
    "x-xsrf-token",
    "x-api-key",
}
SENSITIVE_HEADER_PARTS = ("token", "secret", "key", "session")
REDACTED = "<redacted>"


def write_capture_artifact(
    path: str | Path,
    captures: list[CapturedRequest],
) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        [capture_to_artifact(cap) for cap in captures],
        ensure_ascii=False,
        indent=2,
    ))


def capture_to_artifact(cap: CapturedRequest) -> dict[str, Any]:
    row = asdict(cap)
    row["request_headers"] = redact_headers(cap.request_headers)
    row["response_headers"] = redact_headers(cap.response_headers)
    if cap.post_data is not None:
        row["post_data"] = REDACTED
    if cap.response_body is not None:
        row["response_body"] = REDACTED
        row["response_body_bytes"] = len(cap.response_body.encode("utf-8", "ignore"))
    return row


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in SENSITIVE_HEADER_NAMES or any(
            part in lowered for part in SENSITIVE_HEADER_PARTS
        ):
            out[name] = REDACTED
        else:
            out[name] = value
    return out
