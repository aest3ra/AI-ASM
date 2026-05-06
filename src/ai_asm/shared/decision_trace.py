"""Unified trace stream for analyzer and future agent decisions."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

TraceKind = Literal[
    "agent_turn",
    "tool_call",
    "tool_rejected",
    "dispatch_accepted",
    "dispatch_rejected",
    "candidate_added",
    "endpoint_verified",
    "llm_failure",
    "page_complete",
]


@dataclass
class TraceEvent:
    ts: float
    scan_id: int
    page_url: str | None
    kind: TraceKind
    payload: dict = field(default_factory=dict)


class DecisionTrace:
    def __init__(self, *, scan_id: int = 0) -> None:
        self.scan_id = scan_id
        self._lock = asyncio.Lock()
        self._events: list[TraceEvent] = []

    async def log_turn(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("agent_turn", page_url, payload)

    async def log_tool(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("tool_call", page_url, payload)

    async def log_dispatch(
        self,
        *,
        page_url: str | None,
        accepted: bool,
        reason: str,
        url: str,
        payload: dict | None = None,
    ) -> None:
        event_payload = {"url": url, "reason": reason}
        if payload:
            event_payload.update(payload)
        await self._append(
            "dispatch_accepted" if accepted else "dispatch_rejected",
            page_url,
            event_payload,
        )

    async def log_candidate_added(
        self,
        *,
        page_url: str | None,
        method: str,
        url: str,
        source_kind: str,
    ) -> None:
        await self._append("candidate_added", page_url, {
            "method": method,
            "url": url,
            "source_kind": source_kind,
        })

    async def events(self) -> list[TraceEvent]:
        async with self._lock:
            return list(self._events)

    async def count(self) -> int:
        async with self._lock:
            return len(self._events)

    async def _append(
        self,
        kind: TraceKind,
        page_url: str | None,
        payload: dict,
    ) -> None:
        async with self._lock:
            self._events.append(TraceEvent(
                ts=time.time(),
                scan_id=self.scan_id,
                page_url=page_url,
                kind=kind,
                payload=payload,
            ))
