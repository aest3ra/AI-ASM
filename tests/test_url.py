import pytest

from orbis.normalizer.url import templatize_path


@pytest.mark.parametrize("raw,expected", [
    ("/users/123", "/users/{id}"),
    ("/api/v1/orders/9876/items/42", "/api/v1/orders/{id}/items/{id}"),
    ("/posts/550e8400-e29b-41d4-a716-446655440000", "/posts/{uuid}"),
    ("/files/deadbeefcafef00d", "/files/{hash}"),
    ("/calendar/2025-03-14", "/calendar/{date}"),
    ("/", "/"),
    ("/static", "/static"),
    ("/static/", "/static/"),
])
def test_templatize(raw, expected):
    assert templatize_path(raw) == expected


def test_versioned_segments_kept():
    # `v1` has a single digit, shouldn't be touched
    assert templatize_path("/v1/users/5") == "/v1/users/{id}"


def test_embedded_digits_collapsed():
    # Mixed segments with long digit runs (e.g. timestamps embedded in filenames)
    assert templatize_path(
        "/app/board/attach/image/thumb_220224_1776128639000.do"
    ) == "/app/board/attach/image/thumb_{n}_{n}.do"


def test_short_digits_in_mixed_segment_kept():
    # Short numbers (< 4 digits) inside mixed segments stay
    assert templatize_path("/v12/abc") == "/v12/abc"


@pytest.mark.parametrize("raw,expected", [
    # Random mixed-case slug with extension → {slug}.<ext>
    ("/files/IXpwbBepRmUuLmmmVNgQhzEueH.pdf", "/files/{slug}.pdf"),
    # Random mixed-case slug without extension → {slug}
    ("/share/aBcDeFgHiJkLmNoPqRsTuVwXyZ", "/share/{slug}"),
])
def test_random_slug_templatized(raw, expected):
    assert templatize_path(raw) == expected


@pytest.mark.parametrize("path", [
    # Real CSS/JS filenames with hyphens kept
    "/_res/css/cms-common.css",
    "/_res/js/main-event.widget.js",
    # Short names kept (under threshold)
    "/static/main.js",
    # All-lowercase kept (not mixed case)
    "/users/johnsmithresume",
    # Hyphenated slugs kept (separators present)
    "/blog/some-very-long-article-title",
])
def test_meaningful_slug_kept(path):
    assert templatize_path(path) == path
