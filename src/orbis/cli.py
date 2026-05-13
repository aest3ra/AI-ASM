"""orbis CLI entry point."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import typer
from pydantic import ValidationError
from rich import print
from rich.table import Table
from sqlmodel import Session, select

from orbis.config import AuthConfig, ScanConfig, load_config
from orbis.crawler.scope import Scope
from orbis.output.console import print_scan_result
from orbis.output.flagged import (
    load_flagged_items,
    render_flagged_items,
)
from orbis.output.openapi import openapi_from_db, write_openapi_yaml
from orbis.scan.orchestrator import ScanOrchestrator, find_resume_scan
from orbis.storage.db import Endpoint, Parameter, Scan, open_db

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def scan(
    target_or_config: str = typer.Argument(
        ...,
        metavar="TARGET_OR_CONFIG",
        help="Target URL or scan config YAML path.",
    ),
    db_path: Path | None = typer.Option(
        None,
        "--db",
        help=(
            "SQLite DB path. Defaults to a new runs/orbis-<timestamp>_<host>_<hash>.db "
            "per scan."
        ),
    ),
    auth: Path | None = typer.Option(
        None,
        "--auth",
        exists=True,
        readable=True,
        help="Override config auth with a Playwright storage_state JSON.",
    ),
    headless: bool = typer.Option(True, "--headless/--no-headless"),
    resume: Path | None = typer.Option(
        None,
        "--resume",
        exists=True,
        readable=True,
        help="Resume pending frontier from an existing orbis DB.",
    ),
) -> None:
    """Crawl the target and persist normalized endpoints."""
    try:
        config = _load_scan_config(target_or_config)
    except ValidationError as e:
        typer.secho(
            f"scan target/config error in {target_or_config}:\n{e}",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)
    except OSError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)

    if auth is not None:
        config.auth = AuthConfig(type="storage_state", storage_state_path=auth)

    target = str(config.target)
    db_path, out_dir = _resolve_scan_paths(
        target,
        db_path=db_path,
        resume=resume,
    )
    scope = Scope(config.scope)
    auth_state_path = _auth_state_path(config.auth)

    print(
        f"[bold cyan]scanning[/bold cyan] {target}  "
        f"max_pages={config.limits.max_pages} "
        f"max_dur={config.limits.max_duration_sec}s "
        f"rate={config.limits.rate_limit_rps}rps "
        f"per_template={config.limits.max_visits_per_template} "
        f"auth={auth_state_path or 'none'} "
        f"agent={config.agent.mode} "
        f"db={db_path} "
        f"out={out_dir} "
        f"({'headless' if headless else 'headed'})"
        f"{' resume=' + str(db_path) if resume is not None else ''}"
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

    try:
        result = ScanOrchestrator(
            config=config,
            db_path=db_path,
            out_dir=out_dir,
            headless=headless,
            resume=resume is not None,
            progress=progress,
        ).run()
    except (RuntimeError, ValueError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)

    print_scan_result(
        result,
        scope=scope,
        max_visits_per_template=config.limits.max_visits_per_template,
    )
    outputs = _write_default_scan_outputs(result.db_path, result.raw_path.parent)
    _print_default_scan_outputs(outputs)


def _find_resume_scan(
    session: Session,
    target: str,
    auth_state_path: str | None,
) -> Scan | None:
    return find_resume_scan(session, target, auth_state_path)


def _load_scan_config(target_or_config: str) -> ScanConfig:
    candidate = Path(target_or_config).expanduser()
    if candidate.exists():
        return load_config(candidate)
    if _looks_like_config_path(target_or_config):
        raise FileNotFoundError(f"config file not found: {target_or_config}")
    return ScanConfig.model_validate({"target": target_or_config})


def _looks_like_config_path(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return False
    path = Path(value)
    return (
        path.suffix.lower() in {".yaml", ".yml"}
        or "/" in value
        or "\\" in value
    )


def _auth_state_path(auth: AuthConfig) -> str | None:
    if auth.type == "storage_state" and auth.storage_state_path:
        return str(auth.storage_state_path)
    return None


def _resolve_scan_paths(
    target: str,
    *,
    db_path: Path | None,
    resume: Path | None,
) -> tuple[Path, Path]:
    if resume is not None:
        resolved_db = resume
    elif db_path is not None:
        resolved_db = db_path
    else:
        resolved_db = _default_db_path(target)

    return resolved_db, _artifact_dir_for_db(resolved_db)


def _artifact_dir_for_db(db_path: Path) -> Path:
    if db_path.suffix:
        return db_path.with_suffix("")
    return db_path.parent / f"{db_path.name}-artifacts"


def _default_db_path(target: str, *, root: Path = Path("runs")) -> Path:
    return root / f"orbis-{_timestamp_for_filename()}_{_host_slug(target)}_{_url_hash(target)}.db"


def _timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _host_slug(target: str) -> str:
    parsed = urlparse(target)
    host = parsed.hostname or "target"
    port = f"-{parsed.port}" if parsed.port else ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"{host}{port}").strip("-").lower()
    return slug or "target"


def _url_hash(target: str) -> str:
    return hashlib.sha256(target.encode("utf-8")).hexdigest()[:6]


def _write_default_scan_outputs(db_path: Path, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "openapi": out_dir / "api.yaml",
        "flagged_yaml": out_dir / "flagged.yaml",
        "flagged_curl": out_dir / "flagged.sh",
    }

    write_openapi_yaml(outputs["openapi"], openapi_from_db(db_path))
    flagged_items = load_flagged_items(db_path)
    outputs["flagged_yaml"].write_text(
        render_flagged_items(flagged_items, "yaml"),
        encoding="utf-8",
    )
    outputs["flagged_curl"].write_text(
        render_flagged_items(flagged_items, "curl"),
        encoding="utf-8",
    )
    try:
        outputs["flagged_curl"].chmod(
            outputs["flagged_curl"].stat().st_mode | 0o111,
        )
    except OSError:
        pass
    return outputs


def _print_default_scan_outputs(outputs: dict[str, Path]) -> None:
    table = Table(title="Generated outputs", show_header=False)
    table.add_column("kind", style="bold")
    table.add_column("path", overflow="fold")
    table.add_row("openapi", str(outputs["openapi"]))
    table.add_row("flagged yaml", str(outputs["flagged_yaml"]))
    table.add_row("flagged curl", str(outputs["flagged_curl"]))
    print(table)


def _display_route(url: str | None) -> str:
    if not url:
        return "-"
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.fragment:
        path = f"{path}#{parsed.fragment}"
    return path


@app.command(hidden=True)
def flagged(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Show items that orbis detected but did not auto-test."""
    items = load_flagged_items(db_path)
    if not items:
        print("[dim]no flagged items found[/dim]")
        return
    table = Table(title="Flagged Items", show_header=True, header_style="bold")
    table.add_column("id", justify="right")
    table.add_column("scan", justify="right")
    table.add_column("kind")
    table.add_column("method")
    table.add_column("url", overflow="fold")
    table.add_column("description", overflow="fold")
    for item in items:
        table.add_row(
            str(item.id or ""),
            str(item.scan_id),
            item.flag_kind,
            item.method or "-",
            item.url or "-",
            item.description or "-",
        )
    print(table)


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
    out: Path = typer.Option(Path("auth.json"), "-o", "--out"),
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
