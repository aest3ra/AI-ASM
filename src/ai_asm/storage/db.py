"""SQLite schema for scans, endpoints, and parameters."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, create_engine


class Scan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    target: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    pages_crawled: int = 0
    endpoints_found: int = 0


class Endpoint(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "scan_id", "method", "host", "path_template",
            name="uq_endpoint_scan_method_host_path",
        ),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    method: str
    host: str
    path_template: str
    sample_url: str
    seen_count: int = 1


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


def open_db(path: str | Path):
    """Create the SQLite engine and ensure all tables exist."""
    url = f"sqlite:///{Path(path).resolve()}"
    engine = create_engine(url, echo=False)
    SQLModel.metadata.create_all(engine)
    return engine
