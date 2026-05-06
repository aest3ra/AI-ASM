import json

from ai_asm.crawler.types import CapturedRequest
from ai_asm.output.request_log import write_request_log


def test_write_request_log_jsonl(tmp_path):
    path = tmp_path / "requests.jsonl"
    captures = [
        CapturedRequest(
            request_id="1",
            method="GET",
            url="https://example.com/api/users",
            resource_type="XHR",
            page_url="https://example.com/dashboard",
            response_status=200,
            response_mime="application/json",
        ),
        CapturedRequest(
            request_id="2",
            method="POST",
            url="https://example.com/api/orders",
            resource_type="Fetch",
            page_url="https://example.com/cart",
            response_status=201,
            post_data='{"id":1}',
        ),
        CapturedRequest(
            request_id="static-probe:1",
            method="GET",
            url="https://example.com/api/static",
            resource_type="Fetch",
            page_url="https://example.com/app.js",
            source="static_probe",
            response_status=200,
        ),
    ]

    write_request_log(path, captures, scan_id=42)

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [
        {
            "scan_id": 42,
            "page_url": "https://example.com/dashboard",
            "request_id": "1",
            "method": "GET",
            "url": "https://example.com/api/users",
            "resource_type": "XHR",
            "source": "cdp",
            "response_status": 200,
            "response_mime": "application/json",
            "body_truncated": False,
            "body_fetch_error": None,
        },
        {
            "scan_id": 42,
            "page_url": "https://example.com/cart",
            "request_id": "2",
            "method": "POST",
            "url": "https://example.com/api/orders",
            "resource_type": "Fetch",
            "source": "cdp",
            "response_status": 201,
            "response_mime": None,
            "body_truncated": False,
            "body_fetch_error": None,
        },
        {
            "scan_id": 42,
            "page_url": "https://example.com/app.js",
            "request_id": "static-probe:1",
            "method": "GET",
            "url": "https://example.com/api/static",
            "resource_type": "Fetch",
            "source": "static_probe",
            "response_status": 200,
            "response_mime": None,
            "body_truncated": False,
            "body_fetch_error": None,
        },
    ]
