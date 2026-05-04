"""ai-asm CLI entry point."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import typer
from pydantic import ValidationError
from rich import print
from rich.table import Table
from sqlmodel import Session, select

from ai_asm.config import load_config
from ai_asm.crawler.runner import Crawler
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import ScanDiagnostics
from ai_asm.normalizer import NormalizedEndpoint, normalize
from ai_asm.storage.db import Endpoint, Parameter, Scan, open_db
from ai_asm.storage.repo import save_endpoints

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def scan(
    config_path: Path = typer.Argument(..., exists=True, readable=True),
    db_path: Path = typer.Option(Path("asm.db"), "--db"),
    out_dir: Path = typer.Option(Path("captures"), "--out"),
    headless: bool = typer.Option(True, "--headless/--no-headless"),
    only_dynamic: bool = typer.Option(
        False, "--only-dynamic",
        help="Only show XHR/Fetch/Document endpoints in the summary table.",
    ),
) -> None:
    """Crawl the target and persist normalized endpoints."""
    try:
        config = load_config(config_path)
    except ValidationError as e:
        typer.secho(f"config error in {config_path}:\n{e}", fg="red", err=True)
        raise typer.Exit(1)

    scope = Scope(config.scope)
    target = str(config.target)

    if not scope.allows(target):
        typer.secho(
            f"target {target} is outside scope {config.scope.include_domains}",
            fg="red", err=True,
        )
        raise typer.Exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = open_db(db_path)

    print(
        f"[bold cyan]scanning[/bold cyan] {target}  "
        f"max_pages={config.limits.max_pages} "
        f"max_dur={config.limits.max_duration_sec}s "
        f"rate={config.limits.rate_limit_rps}rps "
        f"per_template={config.limits.max_visits_per_template} "
        f"({'headless' if headless else 'headed'})"
    )

    def progress(*, url, idx, qsize, total_reqs, page_requests=None, error=None):
        if error:
            print(f"  [red][{idx}][/red] {url or '-'}  error={error}")
        else:
            path = urlparse(url).path or "/"
            print(
                f"  [{idx:>3}] {path:<60.60s}  "
                f"+{page_requests:>3} reqs  queue={qsize}  total={total_reqs}"
            )

    crawler = Crawler(
        auth=config.auth, scope=scope, limits=config.limits,
        headless=headless, on_progress=progress,
    )

    started = time.monotonic()
    captured, scan_diag = asyncio.run(crawler.crawl(target))
    elapsed = time.monotonic() - started

    endpoints = normalize(captured)

    with Session(engine) as session:
        scan_row = Scan(
            target=target,
            pages_crawled=scan_diag.pages_crawled,
            endpoints_found=len(endpoints),
            finished_at=datetime.utcnow(),
        )
        session.add(scan_row)
        session.commit()
        session.refresh(scan_row)
        scan_id = scan_row.id

        save_endpoints(session, scan_id, endpoints)

    out_path = out_dir / f"scan-{scan_id}-{int(time.time())}.json"
    out_path.write_text(json.dumps(
        [asdict(c) for c in captured], ensure_ascii=False, indent=2,
    ))

    _print_summary(captured, endpoints, scope, elapsed, only_dynamic)
    _print_diagnostics(scan_diag, config.limits.max_visits_per_template)
    print(f"\n[dim]raw saved to[/dim] {out_path}")
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
    table.add_row("links enqueued (after dedupe + cap)", str(diag.links_enqueued))
    table.add_row(f"links capped (template hit ≥{cap})", str(diag.links_skipped_template_cap))
    table.add_row("buttons clicked", str(diag.buttons_clicked))
    table.add_row("buttons skipped (danger)", str(diag.buttons_skipped_danger))
    table.add_row("POST forms submitted", str(diag.forms_submitted))
    table.add_row("POST forms skipped (password)", str(diag.forms_skipped_password))
    table.add_row("POST forms skipped (danger/file)", str(diag.forms_skipped_other))
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
