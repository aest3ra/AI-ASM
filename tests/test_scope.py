import pytest

from orbis.config import ScopeConfig
from orbis.crawler.scope import Scope


@pytest.fixture
def scope() -> Scope:
    return Scope(ScopeConfig(
        include_domains=["example.com", "*.example.com"],
        exclude_paths=["/logout", "/admin/danger"],
    ))


@pytest.mark.parametrize("url", [
    "https://example.com/",
    "https://example.com/users/1",
    "https://api.example.com/v1/products",
    "http://example.com/",
])
def test_in_scope(scope: Scope, url: str):
    assert scope.allows(url) is True


@pytest.mark.parametrize("url", [
    "https://other.com/",
    "https://example.org/",
    "https://example.com.evil.com/",
    "ftp://example.com/file",
    "https://example.com/logout",
    "https://example.com/admin/danger/wipe",
])
def test_out_of_scope(scope: Scope, url: str):
    assert scope.allows(url) is False


def test_subdomain_wildcard_does_not_match_apex_only_when_pattern_excludes_it():
    scope = Scope(ScopeConfig(include_domains=["*.example.com"]))
    assert scope.allows("https://api.example.com/x") is True
    assert scope.allows("https://example.com/x") is False
