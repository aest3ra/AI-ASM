"""Append-only network event buffer used for agent progress deltas."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class NetworkEventBuffer:
    """Small in-memory cursor over browser request events.

    The crawler's CDP capture stream is the source of truth for saved endpoints.
    The agent uses this buffer as a cursor over that same stream so action-level
    progress is measured against requests that will also be persisted.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._event = asyncio.Event()

    def record(
        self,
        *,
        method: str,
        url: str,
        source: str = "cdp",
        resource_type: str | None = None,
        ts: float | None = None,
    ) -> None:
        if not url:
            return
        record: dict[str, Any] = {
            "method": str(method or "GET").upper(),
            "url": str(url),
            "source": source,
            "ts": ts if ts is not None else time.time(),
        }
        if resource_type:
            record["resource_type"] = resource_type
        self._records.append(record)
        self._event.set()
        self._event = asyncio.Event()

    def cursor(self) -> int:
        return len(self._records)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._records)

    def since(self, cursor: int) -> list[dict[str, Any]]:
        if cursor < 0:
            cursor = 0
        return list(self._records[cursor:])

    async def wait_after(self, cursor: int, *, timeout_ms: int = 1000) -> None:
        if len(self._records) > cursor:
            return
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), timeout_ms / 1000)
        except asyncio.TimeoutError:
            pass
