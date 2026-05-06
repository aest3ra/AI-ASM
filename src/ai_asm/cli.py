"""ai-asm CLI entry point."""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import typer
from pydantic import ValidationError
from rich import print
from rich.table import Table
from sqlmodel import Session, select

from ai_asm.analyzer.dispatcher import AnalyzerDispatcher, DispatchStats
from ai_asm.config import AuthConfig, StaticProbeAuthMode, load_config
from ai_asm.crawler.probe import learn_static_probe_auth_profiles
from ai_asm.crawler.runner import Crawler
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import ScanDiagnostics
from ai_asm.normalizer import (
    ApiCandidate,
    NormalizedEndpoint,
    discover_api_candidates,
    normalize,
)
from ai_asm.shared.candidate_store import CandidateStore
from ai_asm.shared.decision_trace import DecisionTrace
from ai_asm.shared.response_store import ResponseStore
from ai_asm.storage.db import Endpoint, FlaggedItem, FrontierState, Parameter, Scan, open_db
from ai_asm.output.request_log import write_request_log
from ai_asm.storage.repo import (
    record_flagged_item,
    save_endpoints,
    save_scan_summary,
    save_static_candidates,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def scan(
    config_path: Path = typer.Argument(..., exists=True, readable=True),
    db_path: Path = typer.Option(Path("asm.db"), "--db"),
    out_dir: Path = typer.Option(Path("captures"), "--out"),
    auth: Path | None = typer.Option(
        None,
        "--auth",
        exists=True,
        readable=True,
        help="Override config auth with a Playwright storage_state JSON.",
    ),
    auth_label: str | None = typer.Option(
        None,
        "--auth-label",
        help="Human label for this auth context, e.g. user or admin.",
    ),
    headless: bool = typer.Option(True, "--headless/--no-headless"),
    only_dynamic: bool = typer.Option(
        False, "--only-dynamic",
        help="Only show XHR/Fetch/Document endpoints in the summary table.",
    ),
    resume: Path | None = typer.Option(
        None,
        "--resume",
        exists=True,
        readable=True,
        help="Resume pending frontier from an existing ai-asm DB.",
    ),
    static_probe_auth: str | None = typer.Option(
        None,
        "--static-probe-auth",
        help=(
            "Auth mode for static GET probes: none, cookie-only, or learned. "
            "Defaults to config/static_probe_auth."
        ),
    ),
) -> None:
    """Crawl the target and persist normalized endpoints."""
    try:
        config = load_config(config_path)
    except ValidationError as e:
        typer.secho(f"config error in {config_path}:\n{e}", fg="red", err=True)
        raise typer.Exit(1)

    if auth is not None:
        config.auth = AuthConfig(type="storage_state", storage_state_path=auth)
    if static_probe_auth is not None:
        config.static_probe_auth = _parse_static_probe_auth(static_probe_auth)

    scope = Scope(config.scope)
    target = str(config.target)
    auth_state_path = _auth_state_path(config.auth)

    if not scope.allows(target):
        typer.secho(
            f"target {target} is outside scope {config.scope.include_domains}",
            fg="red", err=True,
        )
        raise typer.Exit(1)

    if resume is not None:
        db_path = resume

    out_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = open_db(db_path)
    with Session(engine) as session:
        if resume is not None:
            scan_row = _find_resume_scan(session, target, auth_state_path)
            if scan_row is None:
                typer.secho(
                    f"no resumable scan for {target} auth={auth_state_path or 'none'} in {db_path}",
                    fg="red",
                    err=True,
                )
                raise typer.Exit(1)
        else:
            scan_row = Scan(target=target, auth_state_path=auth_state_path)
            session.add(scan_row)
            session.commit()
            session.refresh(scan_row)
        scan_id = scan_row.id
    assert scan_id is not None

    print(
        f"[bold cyan]scanning[/bold cyan] {target}  "
        f"max_pages={config.limits.max_pages} "
        f"max_dur={config.limits.max_duration_sec}s "
        f"rate={config.limits.rate_limit_rps}rps "
        f"per_template={config.limits.max_visits_per_template} "
        f"auth={auth_state_path or 'none'} "
        f"static_probe_auth={config.static_probe_auth} "
        f"({'headless' if headless else 'headed'})"
        f"{' resume=' + str(scan_id) if resume is not None else ''}"
    )

    def progress(*, url, idx, qsize, total_reqs, page_requests=None, error=None):
        if error:
            print(f"  [red][{idx}][/red] {url or '-'}  error={error}")
        else:
            path = _display_route(url)
            print(
                f"  [{idx:>3}] {path:<60.60s}  "
                f"+{page_requests:>3} reqs  queue={qsize}  total={total_reqs}"
            )

    candidate_store = CandidateStore()
    response_store = ResponseStore()
    def record_dispatch_rejection(reason, cap, page_url, payload):
        with Session(engine) as session:
            record_flagged_item(
                session,
                scan_id=scan_id,
                flag_kind=reason,
                item_kind="url",
                url=cap.url,
                method=cap.method,
                description=f"Track 1 rejected {cap.url}: {reason}",
                page_url=page_url,
                context_json=payload,
                auth_state_path=auth_state_path,
            )

    decision_trace = DecisionTrace(scan_id=scan_id)
    dispatcher = AnalyzerDispatcher(
        scope=scope,
        candidates=candidate_store,
        responses=response_store,
        trace=decision_trace,
        on_reject=record_dispatch_rejection,
    )

    crawler = Crawler(
        auth=config.auth, scope=scope, limits=config.limits,
        headless=headless, on_progress=progress,
        analyzer_dispatcher=dispatcher,
        db_engine=engine,
        scan_id=scan_id,
        resume=resume is not None,
    )

    started = time.monotonic()
    captured, scan_diag = asyncio.run(crawler.crawl(target))

    scoped_captured = [c for c in captured if scope.allows(c.url)]
    static_candidates = discover_api_candidates(scoped_captured, scope)
    probed_urls: set[str] = set()
    probe_errors: dict[str, str] = {}
    if static_candidates:
        auth_profiles = learn_static_probe_auth_profiles(
            scoped_captured,
            mode=config.static_probe_auth,
            scope=scope,
        )
        probe_caps, probed_urls, probe_errors = asyncio.run(
            crawler.probe_static_get(
                static_candidates,
                auth_mode=config.static_probe_auth,
                auth_profiles=auth_profiles,
            )
        )
        captured.extend(probe_caps)
        scan_diag.static_gets_probed += len(probed_urls)
        scan_diag.static_get_probe_failures += len(probe_errors)
        scan_diag.static_probe_auth_profiles += len(auth_profiles)
        scan_diag.static_probe_auth_headers_applied += sum(
            1 for cap in probe_caps if _has_header(cap.request_headers, "authorization")
        )
        scoped_captured = [c for c in captured if scope.allows(c.url)]
        static_candidates = discover_api_candidates(scoped_captured, scope)

    elapsed = time.monotonic() - started
    endpoints = normalize(scoped_captured, api_only=True)
    asyncio.run(_reconcile_candidates(candidate_store, endpoints))

    with Session(engine) as session:
        scan_row = session.get(Scan, scan_id)
        if scan_row is None:
            typer.secho(f"scan id {scan_id} disappeared from {db_path}", fg="red", err=True)
            raise typer.Exit(1)
        scan_row.pages_crawled = scan_diag.pages_crawled
        scan_row.endpoints_found = len(endpoints)
        scan_row.finished_at = datetime.utcnow()
        session.commit()

        endpoint_stats = save_endpoints(
            session,
            scan_id,
            endpoints,
            auth_state_path=auth_state_path,
            auth_label=auth_label,
            provenance="cdp_capture",
        )
        save_static_candidates(
            session,
            scan_id,
            static_candidates,
            {(e.host, e.path_template) for e in endpoints},
            probed_urls=probed_urls,
            probe_errors=probe_errors,
        )
        flagged_count = len(session.exec(
            select(FlaggedItem).where(FlaggedItem.scan_id == scan_id)
        ).all())
        save_scan_summary(
            session,
            scan_id=scan_id,
            auth_state_path=auth_state_path,
            pages_visited=scan_diag.pages_crawled,
            pages_failed=scan_diag.pages_failed,
            endpoints_added=endpoint_stats.endpoints_added,
            endpoints_updated=endpoint_stats.endpoints_updated,
            flagged_count=flagged_count,
            elapsed_seconds=elapsed,
        )

    out_path = out_dir / f"scan-{scan_id}-{int(time.time())}.json"
    out_path.write_text(json.dumps(
        [asdict(c) for c in captured], ensure_ascii=False, indent=2,
    ))
    request_log_path = out_dir / f"requests-{scan_id}.jsonl"
    write_request_log(request_log_path, captured, scan_id=scan_id)

    _print_summary(scoped_captured, endpoints, scope, elapsed, only_dynamic)
    _print_repeated_api_requests(scoped_captured)
    _print_diagnostics(scan_diag, config.limits.max_visits_per_template)
    pending_candidates = asyncio.run(candidate_store.pending_count())
    _print_dispatcher_stats(dispatcher.stats, pending_candidates)
    _print_static_candidates(static_candidates, endpoints)
    excluded = len(captured) - len(scoped_captured)
    if excluded:
        print(f"[dim]excluded {excluded} out-of-scope raw requests from endpoint normalization[/dim]")
    print(f"\n[dim]raw saved to[/dim] {out_path}")
    print(f"[dim]request log saved to[/dim] {request_log_path}")
    print(f"[dim]scan id {scan_id} in[/dim] {db_path}")


def _print_summary(
    captured: list,
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
            not e.is_dynamic,                # dynamic first
            -len(e.parameters),               # then by param richness
            -e.seen_count,
            e.host, e.path_template,
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


def _print_diagnostics(diag: ScanDiagnostics, cap: int) -> None:
    table = Table(title="Coverage diagnostics", show_header=False)
    table.add_column("metric", style="bold")
    table.add_column("value", justify="right")
    table.add_row("pages crawled", str(diag.pages_crawled))
    table.add_row("pages failed", str(diag.pages_failed))
    table.add_row("dom signatures observed", str(len(diag.dom_signatures_seen)))
    table.add_row("links enqueued (after dedupe + cap)", str(diag.links_enqueued))
    table.add_row(f"links capped (template hit ≥{cap})", str(diag.links_skipped_template_cap))
    table.add_row("links skipped (external redirect)", str(diag.links_skipped_external_redirect))
    table.add_row("buttons clicked", str(diag.buttons_clicked))
    table.add_row("buttons skipped (danger)", str(diag.buttons_skipped_danger))
    table.add_row("POST forms submitted", str(diag.forms_submitted))
    table.add_row("POST forms skipped (password)", str(diag.forms_skipped_password))
    table.add_row("POST forms skipped (danger/file)", str(diag.forms_skipped_other))
    table.add_row("static GET candidates probed", str(diag.static_gets_probed))
    table.add_row("static GET probe failures", str(diag.static_get_probe_failures))
    table.add_row("static probe auth profiles", str(diag.static_probe_auth_profiles))
    table.add_row(
        "static probes with learned auth",
        str(diag.static_probe_auth_headers_applied),
    )
    table.add_row(
        "init_script requests recorded",
        str(diag.init_script_requests_recorded),
    )
    table.add_row(
        "init_script requests added",
        str(diag.init_script_requests_added),
    )
    table.add_row("out-of-scope requests aborted", str(diag.out_of_scope_requests_aborted))
    table.add_row("body fetch errors", str(diag.body_fetch_failures))
    print(table)

    capped = diag.top_capped_templates(n=5)
    if capped:
        cap_table = Table(
            title=f"Top capped templates (cap={cap})",
            show_header=True, header_style="bold",
        )
        cap_table.add_column("host")
        cap_table.add_column("template", overflow="fold")
        cap_table.add_column("seen", justify="right")
        cap_table.add_column("visited", justify="right")
        for (host, tpl), total, visited in capped:
            cap_table.add_row(host, tpl, str(total), str(visited))
        print(cap_table)


def _print_repeated_api_requests(captured: list) -> None:
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


def _print_dispatcher_stats(stats: DispatchStats, pending_candidates: int) -> None:
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


async def _reconcile_candidates(
    candidate_store: CandidateStore,
    endpoints: list[NormalizedEndpoint],
) -> None:
    for endpoint in endpoints:
        await candidate_store.mark_verified(
            endpoint.method,
            endpoint.host,
            endpoint.path_template,
        )


def _print_static_candidates(
    candidates: list[ApiCandidate], endpoints: list[NormalizedEndpoint]
) -> None:
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
    table.add_column("source", overflow="fold")
    for c in new_candidates[:50]:
        table.add_row(c.host, c.path_template, c.source_url)
    print(table)
    if len(new_candidates) > 50:
        print(f"[dim]... {len(new_candidates) - 50} more static candidates[/dim]")


def _find_resume_scan(
    session: Session,
    target: str,
    auth_state_path: str | None,
) -> Scan | None:
    scans = session.exec(
        select(Scan)
        .where(
            Scan.target == target,
            Scan.auth_state_path == auth_state_path,
        )
        .order_by(Scan.id.desc())
    ).all()
    for scan in scans:
        pending = session.exec(
            select(FrontierState.id).where(
                FrontierState.scan_id == scan.id,
                FrontierState.status.in_(["pending", "in_progress"]),
            )
        ).first()
        if pending is not None:
            return scan
    return None


def _auth_state_path(auth: AuthConfig) -> str | None:
    if auth.type == "storage_state" and auth.storage_state_path:
        return str(auth.storage_state_path)
    return None


def _parse_static_probe_auth(value: str) -> StaticProbeAuthMode:
    allowed = {"none", "cookie-only", "learned"}
    normalized = value.strip().lower()
    if normalized not in allowed:
        typer.secho(
            f"invalid --static-probe-auth {value!r}; expected one of: "
            f"{', '.join(sorted(allowed))}",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)
    return normalized  # type: ignore[return-value]


def _has_header(headers: dict[str, str], name: str) -> bool:
    wanted = name.lower()
    return any(key.lower() == wanted for key in headers)


def _display_route(url: str | None) -> str:
    if not url:
        return "-"
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.fragment:
        path = f"{path}#{parsed.fragment}"
    return path


@app.command()
def inspect(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
    endpoint_id: int = typer.Argument(...),
) -> None:
    """Show full detail (params, samples) for a single endpoint."""
    engine = open_db(db_path)
    with Session(engine) as session:
        ep = session.get(Endpoint, endpoint_id)
        if ep is None:
            typer.secho(f"endpoint id {endpoint_id} not found in {db_path}", fg="red", err=True)
            raise typer.Exit(1)
        params = session.exec(
            select(Parameter).where(Parameter.endpoint_id == endpoint_id)
        ).all()

    print(f"[bold cyan]endpoint #{ep.id}[/bold cyan] [bold]{ep.method}[/bold] {ep.host}{ep.path_template}")
    print(f"  sample url:  {ep.sample_url}")
    print(f"  seen count:  {ep.seen_count}")

    if not params:
        print("  [dim]no parameters extracted[/dim]")
        return

    table = Table(title="Parameters", show_header=True, header_style="bold")
    table.add_column("location")
    table.add_column("name")
    table.add_column("type")
    table.add_column("seen", justify="right")
    table.add_column("samples", overflow="fold")
    for p in sorted(params, key=lambda x: (x.location, x.name)):
        try:
            samples = json.loads(p.sample_values_json)
            sample_str = ", ".join(repr(s) for s in samples[:5])
        except Exception:
            sample_str = p.sample_values_json
        table.add_row(p.location, p.name, p.type_inferred, str(p.seen_count), sample_str)
    print(table)


@app.command()
def login(
    target_url: str = typer.Argument(..., help="URL to open for the login flow."),
    out: Path = typer.Option(Path("auth.json"), "--out"),
) -> None:
    """Open a headed browser, let the user authenticate, save the session state.

    Works with any login flow (forms, OAuth, SSO, 2FA, CAPTCHA) because the
    user drives the browser. The resulting `storage_state.json` captures
    cookies + localStorage + sessionStorage, which `scan` can replay.
    """
    asyncio.run(_login_flow(target_url, out))


async def _login_flow(url: str, out_path: Path) -> None:
    from playwright.async_api import async_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        print(
            f"\n[bold cyan]opened[/bold cyan] {url}\n"
            "[bold]Log in in the browser, then press Enter here to save:[/bold]"
        )
        await asyncio.to_thread(input)

        state = await context.storage_state(path=str(out_path))
        cookie_count = len(state.get("cookies", []))
        ls_origins = state.get("origins", [])
        ls_count = sum(len(o.get("localStorage", [])) for o in ls_origins)

        await browser.close()

    print(
        f"\n[green]✓[/green] saved {out_path} "
        f"(cookies: {cookie_count}, localStorage entries: {ls_count})\n"
        "\n[dim]Add this to your scan config:[/dim]\n"
        f"  auth:\n"
        f"    type: storage_state\n"
        f"    storage_state_path: {out_path}\n"
    )


@app.command()
def serve(
    db_path: Path = typer.Option(Path("asm.db"), "--db"),
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve the result viewer (not yet implemented)."""
    print(f"[yellow]viewer not yet implemented (Day 11+); db={db_path} host={host} port={port}[/yellow]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
