"""Per-request JSONL logging for scan debugging."""

from __future__ import annotations

import json
from pathlib import Path

from ai_asm.crawler.types import CapturedRequest


def write_request_log(
    path: str | Path,
    captures: list[CapturedRequest],
    *,
    scan_id: int,
) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for cap in captures:
            f.write(json.dumps({
                "scan_id": scan_id,
                "page_url": cap.page_url,
                "request_id": cap.request_id,
                "method": cap.method,
                "url": cap.url,
                "resource_type": cap.resource_type,
                "source": cap.source,
                "response_status": cap.response_status,
                "response_mime": cap.response_mime,
                "body_truncated": cap.response_body_truncated,
                "body_fetch_error": cap.body_fetch_error,
            }, ensure_ascii=False) + "\n")
