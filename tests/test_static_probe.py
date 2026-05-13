from orbis.crawler.probe import _is_probeable_url, static_probe_skip_reason


def test_static_probe_skips_dangerous_urls():
    assert not _is_probeable_url("https://x.test/ilos/lo/logout.acl")
    assert not _is_probeable_url("https://x.test/community/material_delete.acl")
    assert static_probe_skip_reason("https://x.test/community/material_delete.acl") == "danger"


def test_static_probe_skips_download_urls():
    assert not _is_probeable_url("https://x.test/ilos/co/file_download.acl?FILE_SEQ=1")
    assert static_probe_skip_reason("https://x.test/ilos/mp/file_down.acl") == "download"


def test_static_probe_allows_safe_get_candidates():
    assert _is_probeable_url("https://x.test/api/search?q=test")
