"""Unified trace stream for analyzer and future agent decisions."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    "action_record",
    "state_checkpoint",
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
    def __init__(
        self,
        *,
        scan_id: int = 0,
        path: str | Path | None = None,
        reset: bool = True,
    ) -> None:
        self.scan_id = scan_id
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if reset:
                self.path.write_text("")
        self._lock = asyncio.Lock()
        self._events: list[TraceEvent] = []

    async def log_turn(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("agent_turn", page_url, payload)

    async def log_tool(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("tool_call", page_url, payload)

    async def log_tool_rejected(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("tool_rejected", page_url, payload)

    async def log_llm_failure(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("llm_failure", page_url, payload)

    async def log_action_record(self, *, page_url: str | None, payload: dict) -> None:
        await self._append("action_record", page_url, payload)

    async def log_state_checkpoint(
        self,
        *,
        page_url: str | None,
        payload: dict,
    ) -> None:
        await self._append("state_checkpoint", page_url, payload)

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
            event = TraceEvent(
                ts=time.time(),
                scan_id=self.scan_id,
                page_url=page_url,
                kind=kind,
                payload=payload,
            )
            self._events.append(event)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "ts": event.ts,
                                "scan_id": event.scan_id,
                                "page_url": event.page_url,
                                "kind": event.kind,
                                "payload": event.payload,
                            },
                            ensure_ascii=False,
                            default=str,
                        ) + "\n"
                    )
