"""Candidate endpoints discovered by Track 1 and offered to Track 2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class CandidateEndpoint:
    method: str
    url: str
    host: str
    path_template: str
    source_url: str
    source_kind: str
    seen_count: int = 1
    status: str = "pending"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.method.upper(), self.host, self.path_template)


class CandidateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._items: dict[tuple[str, str, str], CandidateEndpoint] = {}

    async def add(self, candidate: CandidateEndpoint) -> bool:
        async with self._lock:
            key = candidate.key
            existing = self._items.get(key)
            if existing is not None:
                existing.seen_count += candidate.seen_count
                return False
            candidate.method = candidate.method.upper()
            self._items[key] = candidate
            return True

    async def top_n(self, n: int) -> list[CandidateEndpoint]:
        async with self._lock:
            pending = [
                item for item in self._items.values()
                if item.status == "pending"
            ]
            pending.sort(
                key=lambda item: (-item.seen_count, item.host, item.path_template),
            )
            return pending[:n]

    async def pending_count(self) -> int:
        async with self._lock:
            return sum(1 for item in self._items.values() if item.status == "pending")

    async def mark_verified(self, method: str, host: str, path_template: str) -> None:
        async with self._lock:
            item = self._items.get((method.upper(), host, path_template))
            if item is not None:
                item.status = "verified"
