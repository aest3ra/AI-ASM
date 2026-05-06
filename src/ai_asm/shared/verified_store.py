"""Verified endpoints reached by browser execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class VerifiedEndpoint:
    method: str
    url: str
    host: str
    path_template: str
    page_url: str
    provenance: str
    seen_count: int = 1

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.method.upper(), self.host, self.path_template)


class VerifiedStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._items: dict[tuple[str, str, str], VerifiedEndpoint] = {}

    async def mark(self, endpoint: VerifiedEndpoint) -> bool:
        async with self._lock:
            key = endpoint.key
            existing = self._items.get(key)
            if existing is not None:
                existing.seen_count += endpoint.seen_count
                return False
            endpoint.method = endpoint.method.upper()
            self._items[key] = endpoint
            return True

    async def seen(self, method: str, host: str, path_template: str) -> bool:
        async with self._lock:
            return (method.upper(), host, path_template) in self._items

    async def for_export(self) -> list[VerifiedEndpoint]:
        async with self._lock:
            return list(self._items.values())

    async def count(self, host: str | None = None) -> int:
        async with self._lock:
            if host is None:
                return len(self._items)
            return sum(1 for item in self._items.values() if item.host == host)
