"""Response samples retained for later schema inference."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ResponseSample:
    method: str
    url: str
    status: int | None
    mime: str | None
    body: str | None


class ResponseStore:
    def __init__(self, *, max_samples_per_url: int = 3) -> None:
        self._lock = asyncio.Lock()
        self._max_samples_per_url = max_samples_per_url
        self._samples: dict[tuple[str, str], list[ResponseSample]] = {}

    async def observe(
        self,
        *,
        method: str,
        url: str,
        status: int | None,
        mime: str | None,
        body: str | None,
    ) -> None:
        key = (method.upper(), url)
        async with self._lock:
            samples = self._samples.setdefault(key, [])
            if len(samples) >= self._max_samples_per_url:
                return
            samples.append(ResponseSample(
                method=method.upper(),
                url=url,
                status=status,
                mime=mime,
                body=body,
            ))

    async def samples_for(self, method: str, url: str) -> list[ResponseSample]:
        async with self._lock:
            return list(self._samples.get((method.upper(), url), []))

    async def schema_for(self, method: str, url: str) -> dict | None:
        # Phase 6 owns actual schema inference. The store keeps the API stable.
        return None

    async def sample_count(self, host: str | None = None) -> int:
        async with self._lock:
            if host is None:
                return sum(len(samples) for samples in self._samples.values())
            return sum(
                len(samples)
                for (_, url), samples in self._samples.items()
                if f"://{host}" in url
            )
