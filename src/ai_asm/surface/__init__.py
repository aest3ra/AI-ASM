"""URL surface discovery and classification."""

from ai_asm.surface.classifier import (
    UrlSurfaceRecord,
    discover_url_surfaces,
    surfaces_from_static_candidates,
)

__all__ = [
    "UrlSurfaceRecord",
    "discover_url_surfaces",
    "surfaces_from_static_candidates",
]
