"""DB write helpers for normalized scan results."""

from __future__ import annotations

import json

from sqlmodel import Session

from ai_asm.normalizer.types import NormalizedEndpoint
from ai_asm.storage.db import Endpoint, Parameter


def save_endpoints(
    session: Session, scan_id: int, endpoints: list[NormalizedEndpoint]
) -> None:
    """Persist a list of normalized endpoints + their parameters under one scan."""
    for ne in endpoints:
        ep = Endpoint(
            scan_id=scan_id,
            method=ne.method,
            host=ne.host,
            path_template=ne.path_template,
            sample_url=ne.sample_url,
            seen_count=ne.seen_count,
        )
        session.add(ep)
        session.flush()  # get ep.id without committing

        for param in ne.parameters.values():
            session.add(Parameter(
                endpoint_id=ep.id,
                location=param.location,
                name=param.name,
                type_inferred=param.type_inferred,
                sample_values_json=json.dumps(param.sample_values, ensure_ascii=False),
                seen_count=param.seen_count,
            ))
    session.commit()
