"""Console rendering for scan results."""

from __future__ import annotations

from collections import Counter

from rich import print
from rich.table import Table

from ai_asm.analyzer.dispatcher import DispatchStats
from ai_asm.crawler.probe import static_probe_skip_reason
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest, ScanDiagnostics
from ai_asm.normalizer import ApiCandidate, NormalizedEndpoint
from ai_asm.scan.orchestrator import ScanRunResult


def print_scan_result(
    result: ScanRunResult,
    *,
    scope: Scope,
    max_visits_per_template: int,
    only_dynamic: bool,
) -> None:
    print_summary(
        result.scoped_captured,
        result.endpoints,
        scope,
        result.elapsed,
        only_dynamic,
    )
    print_repeated_api_requests(result.scoped_captured)
    print_url_surface_summary(result.url_surfaces)
    print_diagnostics(result.diagnostics, max_visits_per_template)
    print_dispatcher_stats(result.dispatcher.stats, result.pending_candidates)
    print_static_candidates(
        result.static_candidates,
        result.endpoints,
        result.probed_urls,
        result.probe_errors,
    )
    if result.excluded_count:
        print(
            f"[dim]excluded {result.excluded_count} out-of-scope raw requests "
            "from endpoint normalization[/dim]"
        )
    print(f"\n[dim]raw saved to[/dim] {result.raw_path}")
    print(f"[dim]request log saved to[/dim] {result.request_log_path}")
    print(f"[dim]trace saved to[/dim] {result.trace_path}")
    print(f"[dim]scan id {result.scan_id} in[/dim] {result.db_path}")


def print_summary(
    captured: list[CapturedRequest],
    endpoints: list[NormalizedEndpoint],
    scope: Scope,
    elapsed: float,
    only_dynamic: bool,
) -> None:
    dynamic = [e for e in endpoints if e.is_dynamic]
    print(
        f"\n[bold]{len(captured)} raw → {len(endpoints)} endpoints "
        f"({len(dynamic)} dynamic) in {elapsed:.1f}s[/bold]"
    )

    rows = dynamic if only_dynamic else endpoints
    table = Table(
        title="Endpoints (dynamic first)", show_header=True, header_style="bold",
    )
    table.add_column("scope")
    table.add_column("kind")
    table.add_column("method")
    table.add_column("host")
    table.add_column("path", overflow="fold")
    table.add_column("count", justify="right")
    table.add_column("params (loc:name)", overflow="fold")

    sorted_eps = sorted(
        rows,
        key=lambda e: (
            not e.is_dynamic,
            -len(e.parameters),
            -e.seen_count,
            e.host,
            e.path_template,
        ),
    )
    for ep in sorted_eps[:50]:
        in_scope = "✓" if scope.allows(f"https://{ep.host}{ep.path_template}") else "·"
        kind = "dyn" if ep.is_dynamic else "static"
        params = ", ".join(
            f"{p.location}:{p.name}" for p in ep.parameters.values()
        ) or "-"
        table.add_row(
            in_scope, kind, ep.method, ep.host, ep.path_template,
            str(ep.seen_count), params,
        )
    print(table)
    if len(sorted_eps) > 50:
        print(f"[dim]... {len(sorted_eps) - 50} more endpoints (see DB)[/dim]")


def print_diagnostics(diag: ScanDiagnostics, cap: int) -> None:
    table = Table(title="Coverage diagnostics", show_header=False)
    table.add_column("metric", style="bold")
    table.add_column("value", justify="right")
    table.add_row("pages crawled", str(diag.pages_crawled))
    table.add_row("pages failed", str(diag.pages_failed))
    table.add_row("dom signatures observed", str(len(diag.dom_signatures_seen)))
    table.add_row("links enqueued (after dedupe + cap)", str(diag.links_enqueued))
    table.add_row(f"links capped (template hit ≥{cap})", str(diag.links_skipped_template_cap))
    table.add_row("links skipped (external redirect)", str(diag.links_skipped_external_redirect))
    table.add_row("links skipped (danger)", str(diag.links_skipped_danger))
    table.add_row("links skipped (file/download)", str(diag.links_skipped_file))
    table.add_row("buttons clicked", str(diag.buttons_clicked))
    table.add_row("buttons skipped (danger)", str(diag.buttons_skipped_danger))
    table.add_row("POST forms submitted", str(diag.forms_submitted))
    table.add_row("POST forms skipped (password)", str(diag.forms_skipped_password))
    table.add_row("POST forms skipped (danger/blocked)", str(diag.forms_skipped_other))
    table.add_row("static GET candidates probed", str(diag.static_gets_probed))
    table.add_row("static GET probe failures", str(diag.static_get_probe_failures))
    table.add_row("static probe auth profiles", str(diag.static_probe_auth_profiles))
    table.add_row(
        "static probes with learned auth",
        str(diag.static_probe_auth_headers_applied),
    )
    table.add_row("init_script requests recorded", str(diag.init_script_requests_recorded))
    table.add_row("init_script requests added", str(diag.init_script_requests_added))
    table.add_row("out-of-scope requests aborted", str(diag.out_of_scope_requests_aborted))
    table.add_row("body fetch errors", str(diag.body_fetch_failures))
    print(table)

    capped = diag.top_capped_templates(n=5)
    if not capped:
        return
    cap_table = Table(
        title=f"Top capped templates (cap={cap})",
        show_header=True,
        header_style="bold",
    )
    cap_table.add_column("host")
    cap_table.add_column("template", overflow="fold")
    cap_table.add_column("seen", justify="right")
    cap_table.add_column("visited", justify="right")
    for (host, tpl), total, visited in capped:
        cap_table.add_row(host, tpl, str(total), str(visited))
    print(cap_table)


def print_repeated_api_requests(captured: list[CapturedRequest]) -> None:
    counts = Counter(
        c.url for c in captured
        if (
            c.url.startswith(("http://", "https://"))
            and ("/api/" in c.url or "/rest/" in c.url or "/b2b/" in c.url)
        )
    )
    repeated = [(url, count) for url, count in counts.most_common(8) if count >= 5]
    if not repeated:
        return
    table = Table(title="Repeated API requests", show_header=True, header_style="bold")
    table.add_column("count", justify="right")
    table.add_column("url", overflow="fold")
    for url, count in repeated:
        table.add_row(str(count), url)
    print(table)


def print_url_surface_summary(surfaces) -> None:
    if not surfaces:
        return
    counts = Counter(surface.route_kind for surface in surfaces)
    table = Table(title="URL surface classification", show_header=False)
    table.add_column("kind", style="bold")
    table.add_column("count", justify="right")
    for kind in (
        "api_endpoint",
        "action_route",
        "page_route",
        "file",
        "asset",
        "unknown",
    ):
        count = counts.get(kind, 0)
        if count:
            table.add_row(kind, str(count))
    print(table)


def print_dispatcher_stats(stats: DispatchStats, pending_candidates: int) -> None:
    table = Table(title="Track 1 dispatcher", show_header=False)
    table.add_column("metric", style="bold")
    table.add_column("value", justify="right")
    table.add_row("accepted assets", str(stats.accepted))
    table.add_row("deduped assets", str(stats.deduped))
    table.add_row("candidates added", str(stats.candidates_added))
    table.add_row("candidates pending", str(pending_candidates))
    for reason, count in sorted(stats.rejected.items()):
        table.add_row(f"rejected {reason}", str(count))
    print(table)


def print_static_candidates(
    candidates: list[ApiCandidate],
    endpoints: list[NormalizedEndpoint],
    probed_urls: set[str] | None = None,
    probe_errors: dict[str, str] | None = None,
) -> None:
    probed_urls = probed_urls or set()
    probe_errors = probe_errors or {}
    observed = {(e.host, e.path_template) for e in endpoints}
    new_candidates = [
        c for c in candidates if (c.host, c.path_template) not in observed
    ]
    if not new_candidates:
        return

    table = Table(
        title="Static API candidates (not observed on the wire)",
        show_header=True,
        header_style="bold",
    )
    table.add_column("host")
    table.add_column("path", overflow="fold")
    table.add_column("probe")
    table.add_column("source", overflow="fold")
    for c in new_candidates[:50]:
        table.add_row(
            c.host,
            c.path_template,
            _probe_status(c, probed_urls, probe_errors),
            c.source_url,
        )
    print(table)
    if len(new_candidates) > 50:
        print(f"[dim]... {len(new_candidates) - 50} more static candidates[/dim]")


def _probe_status(
    candidate: ApiCandidate,
    probed_urls: set[str],
    probe_errors: dict[str, str],
) -> str:
    if candidate.sample_url in probed_urls:
        return "probed"
    if candidate.sample_url in probe_errors:
        return f"error: {probe_errors[candidate.sample_url]}"
    reason = static_probe_skip_reason(candidate.sample_url)
    if reason == "danger":
        return "not probed: danger"
    if reason == "download":
        return "not probed: file/download"
    if reason:
        return f"not probed: {reason}"
    return "not probed: cap"
