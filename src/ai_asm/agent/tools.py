"""Agent tool schema and safety-enforcing executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlmodel import Session

from ai_asm.agent.budget import BudgetExceeded, BudgetTracker
from ai_asm.agent.safety import (
    label_for_ref,
    matched_danger_keyword,
)
from ai_asm.crawler.scope import Scope
from ai_asm.shared.decision_trace import DecisionTrace
from ai_asm.storage.repo import record_flagged_item

ToolName = Literal[
    "click_ref",
    "type_ref",
    "navigate",
    "submit_form",
    "scroll",
    "get_text",
    "give_up",
]

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    call_id: str
    tool: str
    ok: bool
    data: Any | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: ToolName
    description: str
    required_args: tuple[str, ...] = ()


TOOL_SPECS = [
    ToolSpec("click_ref", "Click an element by snapshot ref.", ("ref",)),
    ToolSpec("type_ref", "Type text into an element by snapshot ref.", ("ref", "text")),
    ToolSpec("navigate", "Navigate to an in-scope URL.", ("url",)),
    ToolSpec("submit_form", "Submit a form by snapshot ref.", ("ref",)),
    ToolSpec("scroll", "Scroll the page.", ()),
    ToolSpec("get_text", "Read text from an element by snapshot ref.", ("ref",)),
    ToolSpec("give_up", "Stop working on the current page.", ()),
]

REQUIRED_TOOL_ARGS = {
    spec.name: spec.required_args
    for spec in TOOL_SPECS
}


class ToolExecutor:
    def __init__(
        self,
        *,
        page,
        scope: Scope,
        budget: BudgetTracker,
        trace: DecisionTrace | None = None,
        db_engine=None,
        scan_id: int | None = None,
        page_url: str | None = None,
        auth_state_path: str | None = None,
    ) -> None:
        self.page = page
        self.scope = scope
        self.budget = budget
        self.trace = trace
        self.db_engine = db_engine
        self.scan_id = scan_id
        self.page_url = page_url
        self.auth_state_path = auth_state_path

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            self.budget.consume_step()
        except BudgetExceeded as exc:
            return await self._reject(
                call,
                flag_kind="agent_budget",
                item_kind=call.name,
                description=str(exc),
                error=f"rejected: {exc}",
            )

        missing = _missing_required_args(call)
        if missing:
            return await self._reject(
                call,
                flag_kind="agent_invalid_args",
                item_kind=call.name,
                description=(
                    f"Tool call missing required args: {', '.join(missing)}"
                ),
                error=f"rejected: missing required args: {', '.join(missing)}",
                context={
                    "tool": call.name,
                    "missing": missing,
                },
            )

        try:
            if call.name == "navigate":
                return await self._navigate(call)
            if call.name == "click_ref":
                return await self._click_ref(call)
            if call.name == "type_ref":
                return await self._type_ref(call)
            if call.name == "submit_form":
                return await self._submit_form(call)
            if call.name == "scroll":
                return await self._scroll(call)
            if call.name == "get_text":
                return await self._get_text(call)
            if call.name == "give_up":
                return await self._ok(call, {"reason": call.arguments.get("reason")})
        except Exception as exc:
            return await self._tool_error(call, exc)
        return await self._reject(
            call,
            flag_kind="agent_unknown_tool",
            item_kind=call.name,
            description=f"Unknown tool {call.name}",
            error=f"rejected: unknown tool {call.name}",
        )

    async def _navigate(self, call: ToolCall) -> ToolResult:
        url = str(call.arguments.get("url") or "")
        if not self.scope.allows(url):
            return await self._reject(
                call,
                flag_kind="agent_scope",
                item_kind="navigate",
                url=url,
                description=f"Navigate rejected out of scope: {url}",
                error="rejected: out of scope",
            )
        await self.page.navigate(url)
        return await self._ok(call, {"url": url})

    async def _click_ref(self, call: ToolCall) -> ToolResult:
        ref = str(call.arguments.get("ref") or "")
        info = await self._describe_ref(ref)
        label = label_for_ref(info)
        matched = matched_danger_keyword(info)
        if matched:
            return await self._reject(
                call,
                flag_kind="agent_blacklist",
                item_kind="click",
                description=f"Click rejected by blacklist: {label}",
                context={"ref": ref, "matched_keyword": matched, "label": label},
                error="rejected: blacklist",
            )
        await self.page.click_ref(ref)
        return await self._ok(call, {"ref": ref})

    async def _type_ref(self, call: ToolCall) -> ToolResult:
        ref = str(call.arguments.get("ref") or "")
        text = str(call.arguments.get("text") or "")
        await self.page.type_ref(ref, text)
        return await self._ok(call, {"ref": ref})

    async def _submit_form(self, call: ToolCall) -> ToolResult:
        ref = str(call.arguments.get("ref") or "")
        info = await self._describe_ref(ref)
        action_url = str(info.get("form_action") or self.page_url or "")
        if action_url and not self.scope.allows(action_url):
            return await self._reject(
                call,
                flag_kind="agent_scope",
                item_kind="form_submit",
                url=action_url,
                description=f"Form submit rejected out of scope: {action_url}",
                context={
                    "ref": ref,
                    "form_action": action_url,
                    "label": label_for_ref(info),
                },
                error="rejected: out of scope",
            )
        await self.page.submit_form(ref)
        return await self._ok(call, {"ref": ref})

    async def _scroll(self, call: ToolCall) -> ToolResult:
        direction = str(call.arguments.get("direction") or "down")
        await self.page.scroll(direction)
        return await self._ok(call, {"direction": direction})

    async def _get_text(self, call: ToolCall) -> ToolResult:
        ref = str(call.arguments.get("ref") or "")
        text = await self.page.get_text(ref)
        return await self._ok(call, {"ref": ref, "text": text})

    async def _describe_ref(self, ref: str) -> dict[str, Any]:
        if hasattr(self.page, "describe_ref"):
            info = await self.page.describe_ref(ref)
            return dict(info or {})
        return {}

    async def _ok(self, call: ToolCall, data: Any | None = None) -> ToolResult:
        payload = await self._trace_payload(call, ok=True)
        if data is not None:
            payload["data"] = data
        if self.trace is not None:
            await self.trace.log_tool(page_url=self.page_url, payload=payload)
        return ToolResult(call_id=call.id, tool=call.name, ok=True, data=data)

    async def _tool_error(self, call: ToolCall, exc: Exception) -> ToolResult:
        error = _format_error(exc)
        if self.trace is not None:
            payload = await self._trace_payload(call, ok=False)
            payload["error"] = error
            await self.trace.log_tool(
                page_url=self.page_url,
                payload=payload,
            )
        return ToolResult(call_id=call.id, tool=call.name, ok=False, error=error)

    async def _reject(
        self,
        call: ToolCall,
        *,
        flag_kind: str,
        item_kind: str,
        description: str,
        error: str,
        url: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        if self.trace is not None:
            payload = await self._trace_payload(call, ok=False)
            payload.update({
                "flag_kind": flag_kind,
                "error": error,
                "context": context or {},
            })
            await self.trace.log_tool_rejected(
                page_url=self.page_url,
                payload=payload,
            )
        if self.db_engine is not None and self.scan_id is not None:
            with Session(self.db_engine) as session:
                record_flagged_item(
                    session,
                    scan_id=self.scan_id,
                    flag_kind=flag_kind,
                    item_kind=item_kind,
                    url=url,
                    method=None,
                    description=description,
                    page_url=self.page_url,
                    context_json=context,
                    auth_state_path=self.auth_state_path,
                )
        return ToolResult(call_id=call.id, tool=call.name, ok=False, error=error)

    async def _trace_payload(self, call: ToolCall, *, ok: bool) -> dict[str, Any]:
        payload = {
            "tool": call.name,
            "call_id": call.id,
            "ok": ok,
            "arguments": _safe_arguments(call),
        }
        ref_context = await self._ref_context(call)
        if ref_context:
            payload["ref_context"] = ref_context
        return payload

    async def _ref_context(self, call: ToolCall) -> dict[str, Any]:
        ref = str(call.arguments.get("ref") or "")
        if not ref:
            return {}
        try:
            info = await self._describe_ref(ref)
        except Exception:
            return {"ref": ref}
        context: dict[str, Any] = {"ref": ref}
        for key in (
            "tag",
            "role",
            "text",
            "aria_label",
            "name",
            "href",
            "form_method",
            "form_action",
            "submit_text",
        ):
            value = info.get(key)
            if value:
                context[key] = _short_value(value)
        fields = info.get("input_fields")
        if isinstance(fields, list):
            context["input_fields"] = [
                {
                    field_key: _short_value(field.get(field_key))
                    for field_key in (
                        "tag",
                        "type",
                        "name",
                        "id",
                        "placeholder",
                        "aria_label",
                    )
                    if isinstance(field, dict) and field.get(field_key)
                }
                for field in fields[:12]
                if isinstance(field, dict)
            ]
        return context


def _safe_arguments(call: ToolCall) -> dict[str, Any]:
    safe = dict(call.arguments)
    if call.name == "type_ref" and "text" in safe:
        safe["text"] = f"<redacted len={len(str(safe['text']))}>"
    return safe


def _missing_required_args(call: ToolCall) -> list[str]:
    required = REQUIRED_TOOL_ARGS.get(call.name, ())
    missing = []
    for name in required:
        value = call.arguments.get(name)
        if value is None or str(value) == "":
            missing.append(name)
    return missing


def _short_value(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_error(exc: Exception) -> str:
    message = _short_value(str(exc), limit=240)
    if message:
        return f"tool failed: {type(exc).__name__}: {message}"
    return f"tool failed: {type(exc).__name__}"
