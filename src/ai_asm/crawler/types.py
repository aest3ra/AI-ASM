"""Captured network records produced by the browser worker."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class CapturedRequest:
    request_id: str
    method: str
    url: str
    resource_type: str
    request_headers: dict[str, str] = field(default_factory=dict)
    post_data: str | None = None

    response_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_mime: str | None = None
    response_body: str | None = None
    response_body_truncated: bool = False
    body_fetch_error: str | None = None


@dataclass
class FormStats:
    """How many <form> elements were considered and what we did with them."""

    seen: int = 0
    submitted: int = 0
    skipped_danger: int = 0
    skipped_password: int = 0
    skipped_file: int = 0
    skipped_get: int = 0


@dataclass
class InteractionStats:
    """How many UI elements `trigger_interactions` saw and acted on."""

    buttons_seen: int = 0
    buttons_clicked: int = 0
    buttons_skipped_danger: int = 0
    forms: FormStats = field(default_factory=FormStats)


@dataclass
class PageDiagnostics:
    """Per-page record returned by `capture_page`. Aggregated immediately by
    the runner into `ScanDiagnostics`; not retained per-page."""

    nav_error: str | None = None
    body_fetch_failures: int = 0
    interactions: InteractionStats = field(default_factory=InteractionStats)


@dataclass
class ScanDiagnostics:
    """Aggregated counters across an entire BFS scan."""

    pages_crawled: int = 0
    pages_failed: int = 0
    links_enqueued: int = 0
    links_skipped_template_cap: int = 0
    body_fetch_failures: int = 0
    buttons_clicked: int = 0
    buttons_skipped_danger: int = 0
    forms_submitted: int = 0
    forms_skipped_password: int = 0
    forms_skipped_other: int = 0  # danger + file + multipart
    template_visits: Counter[tuple[str, str]] = field(default_factory=Counter)
    template_seen: Counter[tuple[str, str]] = field(default_factory=Counter)

    def top_capped_templates(self, n: int = 5) -> list[tuple[tuple[str, str], int, int]]:
        """Return up to `n` ((host, template), total_seen, visited) for templates capped."""
        rows = []
        for key, total in self.template_seen.items():
            visited = self.template_visits.get(key, 0)
            if total > visited:
                rows.append((key, total, visited))
        rows.sort(key=lambda r: -r[1])
        return rows[:n]
