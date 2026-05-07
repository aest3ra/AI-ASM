"""Minimal Mode-B agent loop contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ai_asm.agent.budget import BudgetExceeded
from ai_asm.agent.client import LLMClient
from ai_asm.agent.tools import ToolCall, ToolResult
from ai_asm.shared.decision_trace import DecisionTrace


@dataclass
class AgentLoopResult:
    tool_results: list[ToolResult] = field(default_factory=list)
    turns: int = 0


class AgentLoop:
    def __init__(
        self,
        *,
        client: LLMClient,
        executor,
        trace: DecisionTrace | None = None,
        page_url: str | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.trace = trace
        self.page_url = page_url

    async def run_once(self, context: dict[str, Any]) -> AgentLoopResult:
        response = await self.client.complete(context)
        if hasattr(self.executor, "budget"):
            self.executor.budget.consume_tokens(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
        if self.trace is not None:
            await self.trace.log_turn(
                page_url=self.page_url,
                payload={
                    "tool_calls": [call.name for call in response.tool_calls],
                    "tool_call_details": [
                        _summarize_tool_call(call)
                        for call in response.tool_calls
                    ],
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cache_read_input_tokens": response.cache_read_input_tokens,
                    "cache_creation_input_tokens": response.cache_creation_input_tokens,
                    "text": response.text,
                },
            )

        results: list[ToolResult] = []
        for call in response.tool_calls:
            try:
                results.append(await self.executor.execute(call))
            except BudgetExceeded as exc:
                results.append(ToolResult(
                    call_id=call.id,
                    tool=call.name,
                    ok=False,
                    error=f"rejected: {exc}",
                ))
                break
        return AgentLoopResult(tool_results=results, turns=1)

    async def run_page(
        self,
        *,
        context_factory: Callable[[], Awaitable[dict[str, Any]]],
        after_action: Callable[[int, ToolCall, ToolResult], Awaitable[bool | None]],
        max_turns: int,
        before_action: Callable[[int, ToolCall], Awaitable[None]] | None = None,
        local_planner: Callable[[dict[str, Any]], list[ToolCall]] | None = None,
    ) -> AgentLoopResult:
        results: list[ToolResult] = []
        for turn in range(max_turns):
            context = await context_factory()
            local_calls = local_planner(context) if local_planner is not None else []
            if local_calls:
                if self.trace is not None:
                    await self.trace.log_turn(
                        page_url=self.page_url,
                        payload={
                            "turn": turn,
                            "source": "local_planner",
                            "tool_calls": [call.name for call in local_calls],
                            "tool_call_details": [
                                _summarize_tool_call(call)
                                for call in local_calls
                            ],
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                            "text": "local planner",
                        },
                    )
                should_stop = await self._execute_batch(
                    local_calls,
                    turn=turn,
                    before_action=before_action,
                    after_action=after_action,
                    results=results,
                )
                if should_stop:
                    return AgentLoopResult(tool_results=results, turns=turn + 1)
                continue

            response = await self.client.complete(context)
            if hasattr(self.executor, "budget"):
                self.executor.budget.consume_tokens(
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            if self.trace is not None:
                await self.trace.log_turn(
                    page_url=self.page_url,
                    payload={
                        "turn": turn,
                        "tool_calls": [call.name for call in response.tool_calls],
                        "tool_call_details": [
                            _summarize_tool_call(call)
                            for call in response.tool_calls
                        ],
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cache_read_input_tokens": response.cache_read_input_tokens,
                        "cache_creation_input_tokens": response.cache_creation_input_tokens,
                        "text": response.text,
                    },
                )
            if not response.tool_calls:
                return AgentLoopResult(tool_results=results, turns=turn + 1)

            should_stop = await self._execute_batch(
                response.tool_calls,
                turn=turn,
                before_action=before_action,
                after_action=after_action,
                results=results,
            )
            if should_stop:
                return AgentLoopResult(tool_results=results, turns=turn + 1)
        return AgentLoopResult(tool_results=results, turns=max_turns)

    async def _execute_batch(
        self,
        calls: list[ToolCall],
        *,
        turn: int,
        before_action: Callable[[int, ToolCall], Awaitable[None]] | None,
        after_action: Callable[[int, ToolCall, ToolResult], Awaitable[bool | None]],
        results: list[ToolResult],
    ) -> bool:
        for call in calls[:4]:
            if before_action is not None:
                await before_action(turn, call)
            try:
                result = await self.executor.execute(call)
            except BudgetExceeded as exc:
                result = ToolResult(
                    call_id=call.id,
                    tool=call.name,
                    ok=False,
                    error=f"rejected: {exc}",
                )
            results.append(result)
            should_continue = await after_action(turn, call, result)
            if (
                call.name == "give_up"
                or result.error and "step budget exceeded" in result.error
                or should_continue is False
            ):
                return True
            if not _can_continue_batch(call, result):
                break
        return False


def _summarize_tool_call(call) -> dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": _safe_arguments(call.name, call.arguments),
    }


def _can_continue_batch(call: ToolCall, result: ToolResult) -> bool:
    if not result.ok:
        return False
    return call.name in {"type_ref", "get_text"}


def _safe_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    safe = dict(arguments)
    if tool_name == "type_ref" and "text" in safe:
        safe["text"] = f"<redacted len={len(str(safe['text']))}>"
    return safe
