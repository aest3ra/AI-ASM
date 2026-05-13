"""Output types of the normalization pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ParamLocation = Literal["query", "header", "body", "cookie"]


@dataclass
class NormalizedParameter:
    location: ParamLocation
    name: str
    type_inferred: str
    sample_values: list[str] = field(default_factory=list)
    seen_count: int = 0


DYNAMIC_RESOURCE_TYPES = frozenset({"XHR", "Fetch", "Document"})


@dataclass
class NormalizedEndpoint:
    method: str
    host: str
    path_template: str
    sample_url: str
    seen_count: int = 0
    resource_types: set[str] = field(default_factory=set)
    parameters: dict[tuple[ParamLocation, str], NormalizedParameter] = field(
        default_factory=dict
    )

    @property
    def is_dynamic(self) -> bool:
        return bool(self.resource_types & DYNAMIC_RESOURCE_TYPES)
