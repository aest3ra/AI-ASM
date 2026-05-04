import pytest

from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer.params import (
    extract_all,
    extract_body,
    extract_cookies,
    extract_headers,
    extract_query,
    infer_type,
)


def test_query_extraction():
    pairs = list(extract_query("https://x/a?foo=1&bar=hi&empty="))
    assert pairs == [("foo", "1"), ("bar", "hi"), ("empty", "")]


def test_headers_skip_browser_noise():
    h = {
        "user-agent": "Mozilla", "accept": "*/*",
        "sec-fetch-mode": "cors", "Authorization": "Bearer abc",
        "X-Custom": "yes",
    }
    out = dict(extract_headers(h))
    assert out == {"Authorization": "Bearer abc", "X-Custom": "yes"}


@pytest.mark.parametrize("noisy_header", [
    "range", "if-modified-since", "if-none-match", "if-match",
    "if-unmodified-since", "if-range",
    "save-data", "priority", "device-memory", "viewport-width",
    "x-client-data", "purpose",
])
def test_skip_extended_noise_headers(noisy_header):
    out = dict(extract_headers({noisy_header: "anything", "X-Real": "keep"}))
    assert out == {"X-Real": "keep"}


def test_cookies():
    h = {"cookie": "sid=abc; theme=dark"}
    assert dict(extract_cookies(h)) == {"sid": "abc", "theme": "dark"}


def test_body_form():
    h = {"content-type": "application/x-www-form-urlencoded"}
    assert dict(extract_body(h, "siteId=ko&yearMonth=202605")) == {
        "siteId": "ko", "yearMonth": "202605",
    }


def test_body_json():
    h = {"content-type": "application/json"}
    assert dict(extract_body(h, '{"name":"alice","age":30,"tags":["a","b"]}')) == {
        "name": "alice", "age": "30", "tags": '["a", "b"]',
    }


def test_body_no_content_type_falls_back():
    # Heuristic: looks like form data → parse as form
    assert dict(extract_body({}, "k=1&v=2")) == {"k": "1", "v": "2"}


@pytest.mark.parametrize("v,expected", [
    ("", "empty"),
    ("true", "bool"), ("FALSE", "bool"),
    ("42", "int"), ("-7", "int"),
    ("3.14", "float"),
    ('{"a":1}', "json"),
    ("hello", "string"),
])
def test_infer_type(v, expected):
    assert infer_type(v) == expected


def test_extract_all_combines_locations():
    req = CapturedRequest(
        request_id="r1", method="POST",
        url="https://api.x/users?foo=1",
        resource_type="XHR",
        request_headers={
            "content-type": "application/json",
            "x-api-key": "k",
            "cookie": "sid=zz",
            "user-agent": "Mozilla",
        },
        post_data='{"name":"alice"}',
    )
    out = extract_all(req)
    locs = {(loc, name) for loc, name, _ in out}
    assert ("query", "foo") in locs
    assert ("header", "x-api-key") in locs
    assert ("cookie", "sid") in locs
    assert ("body", "name") in locs
    assert not any(loc == "header" and name == "user-agent" for loc, name, _ in out)
