from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer import normalize
from ai_asm.normalizer.pipeline import is_api_capture


def cap(method: str, url: str, **kw) -> CapturedRequest:
    return CapturedRequest(
        request_id=kw.get("rid", "r"),
        method=method, url=url,
        resource_type=kw.get("resource_type", "XHR"),
        request_headers=kw.get("headers", {}),
        post_data=kw.get("post_data"),
        response_mime=kw.get("response_mime"),
    )


def test_groups_by_path_template():
    eps = normalize([
        cap("GET", "https://x/users/1"),
        cap("GET", "https://x/users/2"),
        cap("GET", "https://x/users/3"),
        cap("GET", "https://x/posts/9"),
    ])
    by_path = {(e.method, e.path_template): e for e in eps}
    assert (("GET", "/users/{id}")) in by_path
    assert (("GET", "/posts/{id}")) in by_path
    assert by_path["GET", "/users/{id}"].seen_count == 3


def test_accumulates_query_param_samples():
    eps = normalize([
        cap("GET", "https://x/search?q=foo&page=1"),
        cap("GET", "https://x/search?q=bar&page=2"),
        cap("GET", "https://x/search?q=baz&page=3"),
    ])
    assert len(eps) == 1
    p = eps[0].parameters[("query", "q")]
    assert sorted(p.sample_values) == ["bar", "baz", "foo"]
    assert p.seen_count == 3
    assert eps[0].parameters[("query", "page")].type_inferred == "int"


def test_groups_post_with_body_params():
    eps = normalize([
        cap("POST", "https://x/app/getMonthlySchedule.do",
            headers={"content-type": "application/x-www-form-urlencoded"},
            post_data="siteId=ko&yearMonth=202605"),
        cap("POST", "https://x/app/getMonthlySchedule.do",
            headers={"content-type": "application/x-www-form-urlencoded"},
            post_data="siteId=ko&yearMonth=202606"),
    ])
    assert len(eps) == 1
    body_params = {k: v for k, v in eps[0].parameters.items() if k[0] == "body"}
    assert ("body", "siteId") in body_params
    assert ("body", "yearMonth") in body_params
    assert body_params[("body", "yearMonth")].seen_count == 2
    assert sorted(body_params[("body", "yearMonth")].sample_values) == ["202605", "202606"]


def test_sample_values_capped():
    eps = normalize([
        cap("GET", f"https://x/x?q=v{i}") for i in range(10)
    ])
    samples = eps[0].parameters[("query", "q")].sample_values
    assert len(samples) == 5  # MAX_SAMPLES_PER_PARAM


def test_api_only_filters_socket_and_static_assets():
    eps = normalize([
        cap("GET", "https://x/socket.io/?EIO=4", resource_type="XHR"),
        cap("GET", "https://x/assets/logo.png", resource_type="Image"),
        cap("GET", "https://x/chunk-ABC.js", resource_type="Script"),
        cap("GET", "https://x/api/Users", resource_type="XHR"),
        cap("GET", "https://x/rest/products/search?q=apple", resource_type="Fetch"),
    ], api_only=True)

    assert {(e.method, e.path_template) for e in eps} == {
        ("GET", "/api/Users"),
        ("GET", "/rest/products/search"),
    }


def test_api_only_canonicalizes_trailing_slash():
    eps = normalize([
        cap("GET", "https://x/api/Challenges", resource_type="XHR"),
        cap("GET", "https://x/api/Challenges/", resource_type="XHR"),
    ], api_only=True)

    assert len(eps) == 1
    assert eps[0].path_template == "/api/Challenges"
    assert eps[0].seen_count == 2


def test_is_api_capture_accepts_json_xhr_without_api_prefix():
    assert is_api_capture(cap(
        "GET",
        "https://x/account/profile",
        resource_type="XHR",
        response_mime="application/json",
    ))


def test_is_api_capture_accepts_json_document_without_api_prefix():
    assert is_api_capture(cap(
        "GET",
        "https://x/booking",
        resource_type="Document",
        response_mime="application/json",
    ))


def test_is_api_capture_accepts_plain_document_without_api_prefix():
    assert is_api_capture(cap(
        "GET",
        "https://x/ping",
        resource_type="Document",
        response_mime="text/plain",
    ))


def test_is_api_capture_accepts_api_marker_below_service_prefix():
    assert is_api_capture(cap(
        "GET",
        "https://x/identity/api/auth/login",
        resource_type="XHR",
        response_mime="text/plain",
    ))


def test_is_api_capture_accepts_api_internal_without_response_mime():
    assert is_api_capture(cap(
        "POST",
        "https://x/api-internal/apply-job?jobid=5148265008",
        resource_type="Fetch",
        response_mime=None,
    ))


def test_is_api_capture_accepts_post_xhr_html_action_endpoint():
    assert is_api_capture(cap(
        "POST",
        "https://x/ilos/main/main_schedule.acl",
        resource_type="XHR",
        response_mime="text/html",
    ))


def test_is_api_capture_does_not_accept_get_xhr_html_fragment_by_default():
    assert not is_api_capture(cap(
        "GET",
        "https://x/ilos/message/received_list_pop_form.acl",
        resource_type="XHR",
        response_mime="text/html;charset=utf-8",
    ))


def test_is_api_capture_does_not_accept_download_routes():
    assert not is_api_capture(cap(
        "GET",
        "https://x/ilos/co/file_download.acl?FILE_SEQ=1",
        resource_type="Document",
        response_mime="text/html",
    ))
