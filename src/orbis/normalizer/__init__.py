"""Raw captures → grouped endpoints + parameter catalog."""

from orbis.normalizer.pipeline import normalize
from orbis.normalizer.types import NormalizedEndpoint, NormalizedParameter

__all__ = [
    "normalize",
    "discover_api_candidates",
    "ApiCandidate",
    "NormalizedEndpoint",
    "NormalizedParameter",
]


def __getattr__(name: str):
    if name in {"ApiCandidate", "discover_api_candidates"}:
        from orbis.normalizer.static import ApiCandidate, discover_api_candidates

        return {
            "ApiCandidate": ApiCandidate,
            "discover_api_candidates": discover_api_candidates,
        }[name]
    raise AttributeError(name)
