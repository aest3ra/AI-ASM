"""Read-only summary facade for agent context construction."""

from __future__ import annotations

from dataclasses import dataclass

from ai_asm.shared.candidate_store import CandidateStore
from ai_asm.shared.decision_trace import DecisionTrace
from ai_asm.shared.response_store import ResponseStore
from ai_asm.shared.verified_store import VerifiedStore


@dataclass(frozen=True)
class RegistryFacade:
    candidates: CandidateStore
    verified: VerifiedStore
    responses: ResponseStore
    trace: DecisionTrace

    async def summary(self, host: str) -> dict:
        return {
            "host": host,
            "candidates_unverified": await self.candidates.pending_count(),
            "verified_endpoints": await self.verified.count(host),
            "response_samples": await self.responses.sample_count(host),
            "trace_events": await self.trace.count(),
        }
