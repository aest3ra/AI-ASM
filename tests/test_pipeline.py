from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer import normalize


def cap(method: str, url: str, **kw) -> CapturedRequest:
    return CapturedRequest(
        request_id=kw.get("rid", "r"),
        method=method, url=url,
        resource_type=kw.get("resource_type", "XHR"),
        request_headers=kw.get("headers", {}),
        post_data=kw.get("post_data"),
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
