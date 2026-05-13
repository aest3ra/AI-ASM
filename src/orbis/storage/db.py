"""SQLite schema for scans, endpoints, and phase checkpoint state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import UniqueConstraint, text
from sqlmodel import Field, SQLModel, create_engine


class Scan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    target: str
    auth_state_path: str | None = None
    schema_version: int = 2
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    pages_crawled: int = 0
    endpoints_found: int = 0


class AuthContext(SQLModel, table=True):
    __tablename__ = "auth_context"
    __table_args__ = (
        UniqueConstraint(
            "storage_state_fingerprint",
            name="uq_auth_context_fingerprint",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    label: str = "default"
    storage_state_path: str | None = None
    storage_state_fingerprint: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Endpoint(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "auth_context_id", "method", "host", "path_template",
            name="uq_endpoint_auth_method_host_path",
        ),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    auth_context_id: int | None = Field(
        default=None, foreign_key="auth_context.id", index=True,
    )
    method: str
    host: str
    path_template: str
    sample_url: str
    seen_count: int = 1
    provenance: str | None = None
    response_schema_json: str | None = None
    auth_state_path: str | None = None
    first_seen_scan_id: int | None = Field(default=None, index=True)
    last_seen_scan_id: int | None = Field(default=None, index=True)


class Parameter(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id", "location", "name",
            name="uq_parameter_endpoint_location_name",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    endpoint_id: int = Field(foreign_key="endpoint.id", index=True)
    location: str  # query | header | body | cookie
    name: str
    type_inferred: str  # string | int | bool | json | ...
    sample_values_json: str = "[]"
    seen_count: int = 1


class StaticCandidate(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "scan_id", "host", "path_template",
            name="uq_static_candidate_scan_host_path",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    host: str
    path_template: str
    sample_url: str
    source_url: str
    probed: bool = False
    observed: bool = False
    probe_error: str | None = None


class UrlSurface(SQLModel, table=True):
    __tablename__ = "url_surface"
    __table_args__ = (
        UniqueConstraint(
            "scan_id", "method", "host", "path_template", "route_kind",
            name="uq_url_surface_scan_method_host_path_kind",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    method: str
    host: str
    path_template: str
    sample_url: str
    source_kind: str
    observed: bool = False
    status_code: int | None = None
    mime: str | None = None
    resource_type: str | None = None
    route_kind: str = Field(index=True)
    api_score: int = 0
    evidence_json: str = "{}"
    source_url: str | None = None
    seen_count: int = 1


class FlaggedItem(SQLModel, table=True):
    __tablename__ = "flagged_items"
    __table_args__ = (
        UniqueConstraint(
            "scan_id", "flag_kind", "url", "description",
            name="uq_flagged_scan_kind_url_description",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    flag_kind: str
    item_kind: str | None = None
    url: str | None = None
    method: str | None = None
    description: str | None = None
    page_url: str | None = None
    context_json: str | None = None
    auth_state_path: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FrontierState(SQLModel, table=True):
    __tablename__ = "frontier_state"
    __table_args__ = (
        UniqueConstraint(
            "scan_id", "url", "dom_signature",
            name="uq_frontier_scan_url_dom",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    url: str
    dom_signature: str | None = None
    replay_steps_json: str | None = None
    status: str = Field(default="pending", index=True)
    enqueued_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


class ScanSummary(SQLModel, table=True):
    __tablename__ = "scan_summary"

    scan_id: int = Field(foreign_key="scan.id", primary_key=True)
    auth_state_path: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cache_hit_input: int = 0
    dollars_estimated: float = 0.0
    cache_hit_rate: float = 0.0
    pages_visited: int = 0
    pages_failed: int = 0
    endpoints_added: int = 0
    endpoints_updated: int = 0
    flagged_count: int = 0
    elapsed_seconds: float = 0.0
    summary_text: str | None = None


def open_db(path: str | Path):
    """Create the SQLite engine and ensure all tables exist."""
    url = f"sqlite:///{Path(path).resolve()}"
    engine = create_engine(url, echo=False)
    SQLModel.metadata.create_all(engine)
    migrate_v1_to_v2(engine)
    return engine


def migrate_v1_to_v2(engine) -> None:
    """Best-effort in-place migration for pre-v2 SQLite databases.

    The project intentionally avoids Alembic for now. Existing v1 databases are
    small, so phase migrations stay explicit and conservative.
    """
    with engine.begin() as conn:
        _add_missing_columns(conn, "scan", {
            "auth_state_path": "TEXT",
            "schema_version": "INTEGER DEFAULT 2",
        })
        _add_missing_columns(conn, "endpoint", {
            "auth_context_id": "INTEGER",
            "provenance": "TEXT",
            "response_schema_json": "TEXT",
            "auth_state_path": "TEXT",
            "first_seen_scan_id": "INTEGER",
            "last_seen_scan_id": "INTEGER",
        })
        conn.execute(text("UPDATE scan SET schema_version = 2 WHERE schema_version IS NULL"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS endpoints_dedup "
            "ON endpoint (auth_context_id, method, host, path_template)"
        ))


def _add_missing_columns(conn, table: str, columns: dict[str, str]) -> None:
    existing = {
        row[1]
        for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    }
    if not existing:
        return
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
