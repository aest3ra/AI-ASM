from collections import deque

from ai_asm.config import AuthConfig, LimitsConfig, ScopeConfig
from ai_asm.crawler.runner import Crawler, _template_key, normalize_url
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import ScanDiagnostics


def test_normalize_strips_plain_anchor():
    assert normalize_url("https://x/page#section") == "https://x/page"


def test_normalize_strips_anchor_keeping_query():
    assert normalize_url("https://x/page?foo=1#frag") == "https://x/page?foo=1"


def test_normalize_idempotent():
    u = "https://x/page?a=1"
    assert normalize_url(normalize_url(u)) == u


def test_normalize_keeps_spa_hash_route():
    """`#/login` is a route in hash-routing SPAs (juice-shop, older Angular)."""
    assert normalize_url("https://x/#/login") == "https://x/#/login"
    assert normalize_url("https://x/#/admin/users") == "https://x/#/admin/users"


def test_normalize_keeps_legacy_hashbang_route():
    """`#!/foo` is the older AngularJS hashbang convention."""
    assert normalize_url("https://x/#!/about") == "https://x/#!/about"


def test_template_key_uses_hash_route_path():
    """SPAs with hash routes need the hash path to differentiate templates."""
    assert _template_key("https://x/#/users/1") == ("x", "/users/{id}")
    assert _template_key("https://x/#/users/2") == ("x", "/users/{id}")
    assert _template_key("https://x/#/login") == ("x", "/login")


def test_template_key_collapses_numeric_id():
    assert _template_key("https://x/board/view/123") == ("x", "/board/view/{id}")
    assert _template_key("https://x/board/view/124") == ("x", "/board/view/{id}")


def _make_crawler(cap: int = 3) -> Crawler:
    return Crawler(
        auth=AuthConfig(type="none"),
        scope=Scope(ScopeConfig(include_domains=["x.test"])),
        limits=LimitsConfig(max_visits_per_template=cap),
    )


def test_template_cap_enforced_within_same_template():
    """First N urls of a template enqueue; the rest are skipped."""
    crawler = _make_crawler(cap=3)
    diag = ScanDiagnostics()
    queue: deque[str] = deque()
    seen: set[str] = set()
    for i in range(10):
        crawler._try_enqueue(f"https://x.test/board/view/{i}", queue, seen, diag, cap=3)
    assert len(queue) == 3
    assert diag.links_enqueued == 3
    assert diag.links_skipped_template_cap == 7


def test_template_cap_independent_per_template():
    """Different templates have independent caps."""
    crawler = _make_crawler(cap=2)
    diag = ScanDiagnostics()
    queue: deque[str] = deque()
    seen: set[str] = set()
    urls = [
        "https://x.test/board/view/1",
        "https://x.test/board/view/2",
        "https://x.test/board/view/3",   # capped
        "https://x.test/news/article/1",
        "https://x.test/news/article/2",
        "https://x.test/news/article/3", # capped
    ]
    for u in urls:
        crawler._try_enqueue(u, queue, seen, diag, cap=2)
    assert diag.links_enqueued == 4
    assert diag.links_skipped_template_cap == 2


def test_template_seen_tracks_total_including_capped():
    crawler = _make_crawler(cap=2)
    diag = ScanDiagnostics()
    queue: deque[str] = deque()
    seen: set[str] = set()
    for i in range(5):
        crawler._try_enqueue(f"https://x.test/p/{i}", queue, seen, diag, cap=2)
    key = ("x.test", "/p/{id}")
    assert diag.template_seen[key] == 5
    assert diag.template_visits[key] == 2
    assert diag.top_capped_templates() == [(key, 5, 2)]


def test_already_seen_url_does_not_count_twice():
    crawler = _make_crawler(cap=3)
    diag = ScanDiagnostics()
    queue: deque[str] = deque()
    seen: set[str] = {"https://x.test/p/1"}
    crawler._try_enqueue("https://x.test/p/1", queue, seen, diag, cap=3)
    assert len(queue) == 0
    assert diag.links_enqueued == 0


def test_enqueue_marks_seen_to_block_duplicates():
    """Same URL discovered on multiple pages must enqueue exactly once.

    Regression: previously `seen` was populated only at dequeue time, so
    duplicates filled the queue and inflated `template_visits` past the cap.
    """
    crawler = _make_crawler(cap=3)
    diag = ScanDiagnostics()
    queue: deque[str] = deque()
    seen: set[str] = set()
    url = "https://x.test/p/1"
    for _ in range(5):
        crawler._try_enqueue(url, queue, seen, diag, cap=3)
    assert len(queue) == 1
    assert diag.links_enqueued == 1
    assert diag.template_visits[("x.test", "/p/{id}")] == 1


def test_out_of_scope_url_blocked_at_enqueue():
    crawler = _make_crawler(cap=3)
    diag = ScanDiagnostics()
    queue: deque[str] = deque()
    seen: set[str] = set()
    crawler._try_enqueue("https://other.com/", queue, seen, diag, cap=3)
    assert len(queue) == 0
    assert diag.links_enqueued == 0
    assert diag.template_seen == {}
