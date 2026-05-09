from ai_asm.crawler.probe import _is_probeable_url


def test_static_probe_skips_dangerous_urls():
    assert not _is_probeable_url("https://x.test/ilos/lo/logout.acl")
    assert not _is_probeable_url("https://x.test/community/material_delete.acl")


def test_static_probe_allows_safe_get_candidates():
    assert _is_probeable_url("https://x.test/api/search?q=test")
