"""DB write helpers for normalized scan results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ai_asm.normalizer.static import ApiCandidate
from ai_asm.normalizer.types import NormalizedEndpoint
from ai_asm.storage.db import (
    AuthContext,
    Endpoint,
    FlaggedItem,
    FrontierState,
    Parameter,
    ScanSummary,
    StaticCandidate,
)


@dataclass(frozen=True)
class EndpointSaveStats:
    endpoints_added: int = 0
    endpoints_updated: int = 0


def save_endpoints(
    session: Session,
    scan_id: int,
    endpoints: list[NormalizedEndpoint],
    *,
    auth_state_path: str | Path | None = None,
    auth_label: str | None = None,
    provenance: str | None = None,
) -> EndpointSaveStats:
    """Persist normalized endpoints using the phase-0 accumulated model.

    Same (auth context, method, host, path template) updates one catalog row
    across repeated scans; a different auth context gets a separate row.
    """
    auth_context = get_or_create_auth_context(
        session, auth_state_path=auth_state_path, label=auth_label,
    )
    added = 0
    updated = 0

    for ne in endpoints:
        ep = session.exec(
            select(Endpoint).where(
                Endpoint.auth_context_id == auth_context.id,
                Endpoint.method == ne.method,
                Endpoint.host == ne.host,
                Endpoint.path_template == ne.path_template,
            )
        ).first()

        if ep is None:
            ep = Endpoint(
                scan_id=scan_id,
                auth_context_id=auth_context.id,
                method=ne.method,
                host=ne.host,
                path_template=ne.path_template,
                sample_url=ne.sample_url,
                seen_count=ne.seen_count,
                provenance=provenance,
                auth_state_path=(
                    str(auth_state_path) if auth_state_path is not None else None
                ),
                first_seen_scan_id=scan_id,
                last_seen_scan_id=scan_id,
            )
            session.add(ep)
            session.flush()  # get ep.id without committing
            added += 1
        else:
            ep.seen_count += ne.seen_count
            ep.last_seen_scan_id = scan_id
            ep.provenance = _merge_csv(ep.provenance, provenance)
            if auth_state_path is not None:
                ep.auth_state_path = str(auth_state_path)
            if ep.first_seen_scan_id is None:
                ep.first_seen_scan_id = ep.scan_id
            updated += 1

        for param in ne.parameters.values():
            existing = session.exec(
                select(Parameter).where(
                    Parameter.endpoint_id == ep.id,
                    Parameter.location == param.location,
                    Parameter.name == param.name,
                )
            ).first()
            if existing is None:
                session.add(Parameter(
                    endpoint_id=ep.id,
                    location=param.location,
                    name=param.name,
                    type_inferred=param.type_inferred,
                    sample_values_json=json.dumps(
                        param.sample_values, ensure_ascii=False,
                    ),
                    seen_count=param.seen_count,
                ))
            else:
                existing.seen_count += param.seen_count
                existing.sample_values_json = json.dumps(
                    _merge_samples(
                        existing.sample_values_json, param.sample_values,
                    ),
                    ensure_ascii=False,
                )
    session.commit()
    return EndpointSaveStats(
        endpoints_added=added, endpoints_updated=updated,
    )


def get_or_create_auth_context(
    session: Session,
    *,
    auth_state_path: str | Path | None,
    label: str | None = None,
) -> AuthContext:
    fingerprint = _auth_fingerprint(auth_state_path)
    existing = session.exec(
        select(AuthContext).where(
            AuthContext.storage_state_fingerprint == fingerprint,
        )
    ).first()
    if existing is not None:
        if label and existing.label == "default":
            existing.label = label
        if auth_state_path is not None:
            existing.storage_state_path = str(auth_state_path)
        session.flush()
        return existing

    auth_context = AuthContext(
        label=label or _auth_label(auth_state_path),
        storage_state_path=(
            str(auth_state_path) if auth_state_path is not None else None
        ),
        storage_state_fingerprint=fingerprint,
    )
    session.add(auth_context)
    session.flush()
    return auth_context


def save_static_candidates(
    session: Session,
    scan_id: int,
    candidates: list[ApiCandidate],
    observed_keys: set[tuple[str, str]],
    *,
    probed_urls: set[str] | None = None,
    probe_errors: dict[str, str] | None = None,
) -> None:
    """Persist static API candidates discovered from text assets."""
    probed_urls = probed_urls or set()
    probe_errors = probe_errors or {}
    for candidate in candidates:
        existing = session.exec(
            select(StaticCandidate).where(
                StaticCandidate.scan_id == scan_id,
                StaticCandidate.host == candidate.host,
                StaticCandidate.path_template == candidate.path_template,
            )
        ).first()
        if existing is None:
            session.add(StaticCandidate(
                scan_id=scan_id,
                host=candidate.host,
                path_template=candidate.path_template,
                sample_url=candidate.sample_url,
                source_url=candidate.source_url,
                probed=candidate.sample_url in probed_urls,
                observed=(candidate.host, candidate.path_template) in observed_keys,
                probe_error=probe_errors.get(candidate.sample_url),
            ))
            continue

        existing.sample_url = candidate.sample_url
        existing.source_url = candidate.source_url
        existing.probed = existing.probed or candidate.sample_url in probed_urls
        existing.observed = (
            existing.observed
            or (candidate.host, candidate.path_template) in observed_keys
        )
        if candidate.sample_url in probe_errors:
            existing.probe_error = probe_errors[candidate.sample_url]
    session.commit()


def record_flagged_item(
    session: Session,
    *,
    scan_id: int,
    flag_kind: str,
    item_kind: str | None = None,
    url: str | None = None,
    method: str | None = None,
    description: str | None = None,
    page_url: str | None = None,
    context_json: str | dict[str, Any] | None = None,
    auth_state_path: str | Path | None = None,
) -> FlaggedItem:
    existing = session.exec(
        select(FlaggedItem).where(
            FlaggedItem.scan_id == scan_id,
            FlaggedItem.flag_kind == flag_kind,
            FlaggedItem.url == url,
            FlaggedItem.description == description,
        )
    ).first()
    if existing is not None:
        return existing

    item = FlaggedItem(
        scan_id=scan_id,
        flag_kind=flag_kind,
        item_kind=item_kind,
        url=url,
        method=method,
        description=description,
        page_url=page_url,
        context_json=_json_or_none(context_json),
        auth_state_path=(
            str(auth_state_path) if auth_state_path is not None else None
        ),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def upsert_frontier_item(
    session: Session,
    *,
    scan_id: int,
    url: str,
    dom_signature: str | None = None,
    replay_steps_json: str | None = None,
    status: str = "pending",
) -> FrontierState:
    existing = session.exec(
        select(FrontierState).where(
            FrontierState.scan_id == scan_id,
            FrontierState.url == url,
            FrontierState.dom_signature == dom_signature,
        )
    ).first()
    if existing is not None:
        return existing

    item = FrontierState(
        scan_id=scan_id,
        url=url,
        dom_signature=dom_signature,
        replay_steps_json=replay_steps_json,
        status=status,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def update_frontier_status(
    session: Session,
    frontier_id: int,
    status: str,
) -> FrontierState | None:
    item = session.get(FrontierState, frontier_id)
    if item is None:
        return None
    item.status = status
    if status in {"done", "failed"}:
        item.completed_at = datetime.utcnow()
    session.commit()
    session.refresh(item)
    return item


def complete_frontier_item(
    session: Session,
    frontier_id: int,
    *,
    status: str,
    dom_signature: str | None = None,
) -> FrontierState | None:
    item = session.get(FrontierState, frontier_id)
    if item is None:
        return None
    if dom_signature:
        item.dom_signature = dom_signature
    item.status = status
    if status in {"done", "failed"}:
        item.completed_at = datetime.utcnow()
    session.commit()
    session.refresh(item)
    return item


def load_pending_frontier(
    session: Session,
    scan_id: int,
) -> list[FrontierState]:
    return list(session.exec(
        select(FrontierState).where(
            FrontierState.scan_id == scan_id,
            FrontierState.status.in_(["pending", "in_progress"]),
        )
    ).all())


def save_scan_summary(
    session: Session,
    *,
    scan_id: int,
    **values: Any,
) -> ScanSummary:
    summary = session.get(ScanSummary, scan_id)
    if summary is None:
        summary = ScanSummary(scan_id=scan_id)
        session.add(summary)
        session.flush()
    for key, value in values.items():
        if value is not None and hasattr(summary, key):
            setattr(summary, key, value)
    session.commit()
    session.refresh(summary)
    return summary


def _auth_fingerprint(auth_state_path: str | Path | None) -> str:
    if auth_state_path is None:
        return "none"
    path = Path(auth_state_path)
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "path:" + str(path.expanduser().resolve())


def _auth_label(auth_state_path: str | Path | None) -> str:
    if auth_state_path is None:
        return "none"
    return Path(auth_state_path).stem or "default"


def _merge_csv(existing: str | None, new: str | None) -> str | None:
    if not new:
        return existing
    values = [part for part in (existing or "").split(",") if part]
    if new not in values:
        values.append(new)
    return ",".join(values)


def _merge_samples(existing_json: str, new_samples: list[str]) -> list[str]:
    try:
        samples = list(json.loads(existing_json))
    except Exception:
        samples = []
    for sample in new_samples:
        if sample not in samples and len(samples) < 5:
            samples.append(sample)
    return samples


def _json_or_none(value: str | dict[str, Any] | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
