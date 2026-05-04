from ai_asm.normalizer.params import extract_query


def test_cache_buster_skipped():
    assert list(extract_query("https://x/a.css?01323")) == []
    assert list(extract_query("https://x/a.js?deadbeef")) == []


def test_real_query_kept_even_if_value_empty():
    # `?foo=` is a real (empty) value, not a cache buster
    assert list(extract_query("https://x/a?foo=")) == [("foo", "")]


def test_real_query_kept_with_named_param():
    assert list(extract_query("https://x/a?page=1")) == [("page", "1")]
