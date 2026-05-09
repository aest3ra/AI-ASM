"""OpenAPI 3.0 export from the accumulated endpoint catalog."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlmodel import Session, select

from ai_asm.schema.inferrer import merge_json_schemas
from ai_asm.storage.db import Endpoint, Parameter, Scan, open_db


PATH_PARAM_RE = re.compile(r"{([^}/]+)}")


@dataclass
class ExportParameter:
    location: str
    name: str
    type_inferred: str
    sample_values_json: str
    seen_count: int


def openapi_from_db(
    db_path: str | Path,
    *,
    scan_id: int | None = None,
    title: str = "ai-asm export",
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Build an OpenAPI document from saved endpoints."""
    engine = open_db(db_path)
    with Session(engine) as session:
        endpoints = _load_endpoints(session, scan_id)
        params_by_endpoint = _load_parameters(session, endpoints)
        target = _scan_target(session, scan_id)
    return build_openapi(
        endpoints,
        params_by_endpoint,
        title=title,
        version=version,
        target=target,
    )


def build_openapi(
    endpoints: list[Endpoint],
    params_by_endpoint: dict[int, list[Parameter]],
    *,
    title: str = "ai-asm export",
    version: str = "0.1.0",
    target: str | None = None,
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[Endpoint]] = defaultdict(list)
    for endpoint in endpoints:
        grouped[(endpoint.path_template, endpoint.method.upper())].append(endpoint)

    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "version": version,
            "description": "Generated from ai-asm observed API traffic.",
        },
        "paths": {},
    }
    servers = _servers_for(endpoints, target)
    if servers:
        spec["servers"] = [{"url": server} for server in servers]

    used_operation_ids: set[str] = set()
    for (path, method), rows in sorted(grouped.items()):
        operation = _operation_for(
            path,
            method,
            rows,
            params_by_endpoint,
            used_operation_ids,
        )
        spec["paths"].setdefault(path, {})[method.lower()] = operation
    return spec


def write_openapi_yaml(path: str | Path, spec: dict[str, Any]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def openapi_to_yaml(spec: dict[str, Any]) -> str:
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)


def _load_endpoints(session: Session, scan_id: int | None) -> list[Endpoint]:
    statement = select(Endpoint)
    if scan_id is not None:
        statement = statement.where(Endpoint.last_seen_scan_id == scan_id)
    return list(session.exec(statement).all())


def _load_parameters(
    session: Session,
    endpoints: list[Endpoint],
) -> dict[int, list[Parameter]]:
    ids = [endpoint.id for endpoint in endpoints if endpoint.id is not None]
    if not ids:
        return {}
    rows = session.exec(select(Parameter).where(Parameter.endpoint_id.in_(ids))).all()
    grouped: dict[int, list[Parameter]] = defaultdict(list)
    for row in rows:
        grouped[row.endpoint_id].append(row)
    return grouped


def _scan_target(session: Session, scan_id: int | None) -> str | None:
    if scan_id is None:
        return None
    scan = session.get(Scan, scan_id)
    return scan.target if scan is not None else None


def _operation_for(
    path: str,
    method: str,
    rows: list[Endpoint],
    params_by_endpoint: dict[int, list[Parameter]],
    used_operation_ids: set[str],
) -> dict[str, Any]:
    params = _parameters_for(path, rows, params_by_endpoint)
    body_schema = _request_body_schema(rows, params_by_endpoint)
    response_schema = _response_schema(rows)
    metadata = [_endpoint_metadata(row) for row in rows]

    operation: dict[str, Any] = {
        "operationId": _unique_operation_id(method, path, used_operation_ids),
        "summary": f"{method} {path}",
        "x-ai-asm-endpoints": metadata,
        "responses": {
            "200": {
                "description": "Observed response",
            },
        },
    }
    if params:
        operation["parameters"] = params
    if body_schema is not None:
        operation["requestBody"] = {
            "required": method.upper() not in {"GET", "HEAD", "OPTIONS"},
            "content": {
                "application/json": {
                    "schema": body_schema,
                },
            },
        }
    if response_schema is not None:
        operation["responses"]["200"]["content"] = {
            "application/json": {
                "schema": response_schema,
            },
        }
    return operation


def _parameters_for(
    path: str,
    rows: list[Endpoint],
    params_by_endpoint: dict[int, list[Parameter]],
) -> list[dict[str, Any]]:
    path_params = [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        for name in PATH_PARAM_RE.findall(path)
    ]
    seen = {("path", param["name"]) for param in path_params}
    params = list(path_params)
    for param in _merged_parameters(rows, params_by_endpoint):
        if param.location == "body":
            continue
        key = (param.location, param.name)
        if key in seen:
            continue
        seen.add(key)
        params.append({
            "name": param.name,
            "in": param.location,
            "required": False,
            "schema": _schema_for_inferred_type(param.type_inferred),
        })
    return params


def _request_body_schema(
    rows: list[Endpoint],
    params_by_endpoint: dict[int, list[Parameter]],
) -> dict[str, Any] | None:
    body_params = [
        param
        for param in _merged_parameters(rows, params_by_endpoint)
        if param.location == "body"
    ]
    if not body_params:
        return None
    return {
        "type": "object",
        "properties": {
            param.name: _schema_for_inferred_type(param.type_inferred)
            for param in body_params
        },
    }


def _response_schema(rows: list[Endpoint]) -> dict[str, Any] | None:
    schema: dict[str, Any] | None = None
    for row in rows:
        try:
            next_schema = json.loads(row.response_schema_json or "null")
        except Exception:
            next_schema = None
        if not isinstance(next_schema, dict):
            continue
        schema = (
            merge_json_schemas(schema, next_schema)
            if schema is not None
            else next_schema
        )
    return schema


def _merged_parameters(
    rows: list[Endpoint],
    params_by_endpoint: dict[int, list[Parameter]],
) -> list[ExportParameter]:
    merged: dict[tuple[str, str], ExportParameter] = {}
    for row in rows:
        if row.id is None:
            continue
        for param in params_by_endpoint.get(row.id, []):
            key = (param.location, param.name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = ExportParameter(
                    location=param.location,
                    name=param.name,
                    type_inferred=param.type_inferred,
                    sample_values_json=param.sample_values_json,
                    seen_count=param.seen_count,
                )
                continue
            existing.seen_count += param.seen_count
            existing.sample_values_json = _merge_sample_json(
                existing.sample_values_json,
                param.sample_values_json,
            )
    return sorted(merged.values(), key=lambda p: (p.location, p.name))


def _schema_for_inferred_type(type_name: str) -> dict[str, Any]:
    normalized = (type_name or "string").lower()
    if normalized in {"int", "integer"}:
        return {"type": "integer"}
    if normalized in {"float", "number"}:
        return {"type": "number"}
    if normalized in {"bool", "boolean"}:
        return {"type": "boolean"}
    if normalized in {"json", "object"}:
        return {"type": "object"}
    if normalized == "array":
        return {"type": "array", "items": {}}
    return {"type": "string"}


def _merge_sample_json(left_json: str, right_json: str) -> str:
    try:
        values = list(json.loads(left_json or "[]"))
    except Exception:
        values = []
    try:
        incoming = list(json.loads(right_json or "[]"))
    except Exception:
        incoming = []
    for value in incoming:
        if value not in values and len(values) < 5:
            values.append(value)
    return json.dumps(values, ensure_ascii=False)


def _endpoint_metadata(endpoint: Endpoint) -> dict[str, Any]:
    return {
        "id": endpoint.id,
        "host": endpoint.host,
        "sample_url": endpoint.sample_url,
        "seen_count": endpoint.seen_count,
        "provenance": endpoint.provenance,
        "auth_state_path": endpoint.auth_state_path,
        "first_seen_scan_id": endpoint.first_seen_scan_id,
        "last_seen_scan_id": endpoint.last_seen_scan_id,
    }


def _unique_operation_id(
    method: str,
    path: str,
    used_operation_ids: set[str],
) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", f"{method.lower()}_{path}")
    base = base.strip("_") or method.lower()
    candidate = base
    suffix = 2
    while candidate in used_operation_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_operation_ids.add(candidate)
    return candidate


def _servers_for(endpoints: list[Endpoint], target: str | None) -> list[str]:
    servers: list[str] = []
    if target:
        origin = _origin_for_url(target)
        if origin:
            servers.append(origin)
    for endpoint in endpoints:
        origin = _origin_for_url(endpoint.sample_url)
        if origin and origin not in servers:
            servers.append(origin)
    return servers[:10]


def _origin_for_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"
