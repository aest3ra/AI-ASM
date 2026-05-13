import json

from orbis.crawler.types import CapturedRequest
from orbis.output.artifacts import capture_to_artifact, write_capture_artifact


def test_capture_artifact_redacts_sensitive_values(tmp_path):
    cap = CapturedRequest(
        request_id="1",
        method="POST",
        url="https://example.com/api/users",
        resource_type="Fetch",
        request_headers={
            "Authorization": "Bearer secret",
            "Cookie": "sid=secret",
            "Accept": "application/json",
        },
        response_headers={"Set-Cookie": "sid=secret", "Content-Type": "json"},
        post_data='{"password":"secret"}',
        response_body='{"token":"secret"}',
    )

    row = capture_to_artifact(cap)

    assert row["request_headers"]["Authorization"] == "<redacted>"
    assert row["request_headers"]["Cookie"] == "<redacted>"
    assert row["request_headers"]["Accept"] == "application/json"
    assert row["response_headers"]["Set-Cookie"] == "<redacted>"
    assert row["post_data"] == "<redacted>"
    assert row["response_body"] == "<redacted>"
    assert row["response_body_bytes"] == len('{"token":"secret"}')

    out = tmp_path / "scan.json"
    write_capture_artifact(out, [cap])
    saved = json.loads(out.read_text())
    assert saved[0]["request_headers"]["Authorization"] == "<redacted>"
