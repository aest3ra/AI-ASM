from ai_asm.config import ScopeConfig
from ai_asm.crawler.probe import (
    headers_for_static_probe,
    learn_static_probe_auth_profiles,
)
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest


def _scope() -> Scope:
    return Scope(ScopeConfig(include_domains=["api.test"]))


def test_learned_static_probe_auth_reuses_authorization_for_same_origin():
    captures = [
        CapturedRequest(
            request_id="1",
            method="GET",
            url="https://api.test/rest/user/whoami",
            resource_type="XHR",
            request_headers={"Authorization": "Bearer abc"},
            response_status=200,
        ),
    ]

    profiles = learn_static_probe_auth_profiles(
        captures,
        mode="learned",
        scope=_scope(),
    )

    assert headers_for_static_probe(
        "https://api.test/rest/hidden",
        profiles,
        mode="learned",
    ) == {"Authorization": "Bearer abc"}


def test_static_probe_auth_does_not_leak_to_other_origin():
    captures = [
        CapturedRequest(
            request_id="1",
            method="GET",
            url="https://api.test/rest/user/whoami",
            resource_type="Fetch",
            request_headers={"Authorization": "Bearer abc"},
            response_status=200,
        ),
    ]

    profiles = learn_static_probe_auth_profiles(
        captures,
        mode="learned",
        scope=_scope(),
    )

    assert headers_for_static_probe(
        "https://other.test/rest/hidden",
        profiles,
        mode="learned",
    ) == {}


def test_cookie_only_static_probe_auth_learns_nothing():
    captures = [
        CapturedRequest(
            request_id="1",
            method="GET",
            url="https://api.test/rest/user/whoami",
            resource_type="XHR",
            request_headers={"Authorization": "Bearer abc"},
            response_status=200,
        ),
    ]

    assert learn_static_probe_auth_profiles(
        captures,
        mode="cookie-only",
        scope=_scope(),
    ) == {}


def test_learned_static_probe_auth_ignores_failed_or_out_of_scope_requests():
    captures = [
        CapturedRequest(
            request_id="401",
            method="GET",
            url="https://api.test/rest/user/whoami",
            resource_type="XHR",
            request_headers={"Authorization": "Bearer failed"},
            response_status=401,
        ),
        CapturedRequest(
            request_id="other",
            method="GET",
            url="https://other.test/rest/user/whoami",
            resource_type="XHR",
            request_headers={"Authorization": "Bearer other"},
            response_status=200,
        ),
    ]

    assert learn_static_probe_auth_profiles(
        captures,
        mode="learned",
        scope=_scope(),
    ) == {}
