"""LLM client protocol and deterministic mock client for agent contract tests."""

from __future__ import annotations

import json
import os
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ai_asm.config import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_TEMPERATURE
from ai_asm.agent.safety import (
    interaction_key,
    is_click_candidate,
    label_for_ref,
    matches_danger,
)
from ai_asm.agent.tools import TOOL_SPECS, ToolCall

DEFAULT_OPENAI_MODEL = DEFAULT_AGENT_MODEL


@dataclass
class AgentResponse:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLMClient(Protocol):
    async def complete(self, context: dict[str, Any]) -> AgentResponse:
        ...


class MockLLMClient:
    def __init__(self, script: list[AgentResponse] | None = None) -> None:
        self._script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    async def complete(self, context: dict[str, Any]) -> AgentResponse:
        self.calls.append(context)
        if self._script:
            return self._script.pop(0)
        return AgentResponse(
            tool_calls=[
                ToolCall(
                    id="mock-give-up",
                    name="give_up",
                    arguments={"reason": "mock_done"},
                )
            ]
        )


class HeuristicMockLLMClient:
    """Deterministic Phase-4 client that emits tool calls from a snapshot.

    It keeps the production contract shaped like LLM tool-use while preserving
    the old safe-scroll/click/form behavior until a real LLM client is enabled.
    """

    def __init__(
        self,
        *,
        max_clicks: int = 12,
        clicked_keys: set[str] | None = None,
        submit_forms: bool = True,
        allow_password_forms: bool = False,
    ) -> None:
        self.max_clicks = max_clicks
        self.clicked_keys = clicked_keys if clicked_keys is not None else set()
        self.submit_forms = submit_forms
        self.allow_password_forms = allow_password_forms
        self.calls: list[dict[str, Any]] = []

    async def complete(self, context: dict[str, Any]) -> AgentResponse:
        self.calls.append(context)
        refs = _snapshot_refs(context)
        tool_calls = [
            ToolCall(
                id="mock-scroll",
                name="scroll",
                arguments={"direction": "full"},
            )
        ]

        click_count = 0
        for info in refs:
            if click_count >= self.max_clicks:
                break
            if not is_click_candidate(info):
                continue
            label = label_for_ref(info)
            if not label or matches_danger(info):
                continue
            key = interaction_key(info, label)
            if key in self.clicked_keys:
                continue
            self.clicked_keys.add(key)
            ref = str(info.get("ref") or "")
            if not ref:
                continue
            tool_calls.append(ToolCall(
                id=f"mock-click-{ref}",
                name="click_ref",
                arguments={"ref": ref},
            ))
            click_count += 1

        if self.submit_forms:
            for info in refs:
                if not _is_safe_post_form(
                    info,
                    allow_password=self.allow_password_forms,
                ):
                    continue
                ref = str(info.get("ref") or "")
                if not ref:
                    continue
                tool_calls.append(ToolCall(
                    id=f"mock-submit-{ref}",
                    name="submit_form",
                    arguments={"ref": ref},
                ))

        tool_calls.append(ToolCall(
            id="mock-give-up",
            name="give_up",
            arguments={"reason": "mock_done"},
        ))
        return AgentResponse(tool_calls=tool_calls)


class OpenAIClient:
    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        temperature: float = DEFAULT_AGENT_TEMPERATURE,
        api_key: str | None = None,
        client: Any | None = None,
        system_prompt: str | None = None,
        max_output_tokens: int = 1024,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.system_prompt = system_prompt or _default_system_prompt()
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.client = client or _make_openai_client(api_key)
        self._omit_temperature = False

    async def complete(self, context: dict[str, Any]) -> AgentResponse:
        response = await self._create_with_retry(context)
        return _agent_response_from_openai(response)

    async def _create_with_retry(self, context: dict[str, Any]) -> Any:
        last_exc: Exception | None = None
        attempt = 0
        while True:
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "instructions": self.system_prompt,
                    "tools": _openai_tools(),
                    "input": _context_text(context),
                    "max_output_tokens": self.max_output_tokens,
                    "tool_choice": "required",
                }
                if not self._omit_temperature:
                    kwargs["temperature"] = self.temperature
                return await _maybe_await(self.client.responses.create(**kwargs))
            except Exception as exc:
                last_exc = exc
                if not self._omit_temperature and _is_unsupported_temperature_error(exc):
                    self._omit_temperature = True
                    continue
                if attempt >= self.max_retries or not _is_retryable_error(exc):
                    raise
                await asyncio.sleep(_retry_delay(exc, attempt, self.retry_base_delay))
                attempt += 1
        assert last_exc is not None
        raise last_exc


def _snapshot_refs(context: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = context.get("snapshot") or {}
    refs = snapshot.get("refs") if isinstance(snapshot, dict) else []
    return [item for item in refs if isinstance(item, dict)]


def _is_safe_post_form(
    info: dict[str, Any],
    *,
    allow_password: bool = False,
) -> bool:
    tag = str(info.get("tag") or "").lower()
    if tag != "form":
        return False
    if str(info.get("form_method") or "").upper() != "POST":
        return False
    input_types = {str(item).lower() for item in info.get("input_types") or []}
    if (not allow_password and "password" in input_types) or "file" in input_types:
        return False
    return not matches_danger(info)


def load_openai_api_key(env_path: str | Path = ".env") -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    path = Path(env_path)
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == "OPENAI_API_KEY":
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


def _make_openai_client(api_key: str | None):
    from openai import OpenAI

    resolved = api_key or load_openai_api_key()
    if not resolved:
        raise RuntimeError(
            "OPENAI_API_KEY is required for --agent llm. "
            "Set it in the environment or .env.",
        )
    return OpenAI(api_key=resolved)


def _openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": spec.name,
            "description": spec.description,
            "parameters": _schema_for_tool(spec.name, spec.required_args),
        }
        for spec in TOOL_SPECS
    ]


def _schema_for_tool(name: str, required: tuple[str, ...]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "reason": {
            "type": "string",
            "description": "Short reason for this action, for trace debugging.",
        },
    }
    if name in {"click_ref", "submit_form", "get_text"}:
        properties["ref"] = {"type": "string"}
    elif name == "type_ref":
        properties["ref"] = {"type": "string"}
        properties["text"] = {"type": "string"}
    elif name == "navigate":
        properties["url"] = {"type": "string"}
    elif name == "scroll":
        properties["direction"] = {
            "type": "string",
            "enum": ["down", "up", "full"],
        }
    elif name == "give_up":
        properties["reason"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _context_text(context: dict[str, Any]) -> str:
    return (
        "Choose the next browser tools to discover reachable API endpoints.\n"
        "Prefer safe tab/menu/modal exploration, search/filter forms, and POST "
        "forms with available test data. Include a short reason argument in the "
        "tool call. Do not explain outside tool calls.\n\n"
        + json.dumps(_compact_context(context), ensure_ascii=False, default=str)
    )


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "page_url",
        "current_state",
        "goals",
        "visible_forms",
        "form_status",
        "exploration_status",
        "form_test_data",
        "memory",
    ):
        if key in context:
            out[key] = context[key]
    snapshot = context.get("snapshot")
    if isinstance(snapshot, dict):
        refs = snapshot.get("refs")
        compact_refs = []
        if isinstance(refs, list):
            compact_refs = [
                _compact_ref(ref)
                for ref in refs[:80]
                if isinstance(ref, dict)
            ]
        out["snapshot"] = {
            "url": snapshot.get("url"),
            "ref_count": len(refs) if isinstance(refs, list) else 0,
            "refs": compact_refs,
        }
    return out


def _compact_ref(ref: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ref",
        "tag",
        "type",
        "role",
        "text",
        "aria_label",
        "name",
        "href",
        "input_types",
        "form_method",
        "form_action",
        "submit_text",
    )
    out = {
        key: ref[key]
        for key in keys
        if ref.get(key)
    }
    fields = ref.get("input_fields")
    if isinstance(fields, list):
        out["input_fields"] = [
            {
                key: field[key]
                for key in ("tag", "type", "name", "id", "placeholder", "aria_label")
                if isinstance(field, dict) and field.get(key)
            }
            for field in fields[:8]
            if isinstance(field, dict)
        ]
    return out


def _agent_response_from_openai(response: Any) -> AgentResponse:
    tool_calls: list[ToolCall] = []
    text_parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            args = _json_args(getattr(item, "arguments", None))
            call_id = str(getattr(item, "call_id", None) or getattr(item, "id", ""))
            tool_calls.append(ToolCall(
                id=call_id,
                name=str(getattr(item, "name", "")),
                arguments=args,
            ))
        elif item_type == "message":
            text_parts.extend(_message_text_parts(item))
    usage = getattr(response, "usage", None)
    return AgentResponse(
        tool_calls=tool_calls,
        text="\n".join(text_parts) or getattr(response, "output_text", None),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=_usage_detail(usage, "cached_tokens"),
    )


def _usage_detail(usage: Any, name: str) -> int:
    details = getattr(usage, "input_tokens_details", None)
    if isinstance(details, dict):
        return int(details.get(name, 0) or 0)
    return int(getattr(details, name, 0) or 0)


def _json_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _message_text_parts(item: Any) -> list[str]:
    parts = []
    for content in getattr(item, "content", []) or []:
        if getattr(content, "type", None) == "output_text":
            parts.append(str(getattr(content, "text", "")))
    return parts


def _is_retryable_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(
        marker in name or marker in text
        for marker in ("timeout", "rate limit", "temporarily", "server error")
    )


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) != 400:
        return False
    text = str(exc).lower()
    return "temperature" in text and "unsupported parameter" in text


def _retry_delay(exc: Exception, attempt: int, base_delay: float) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return retry_after
    return base_delay * (2 ** attempt)


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if not headers:
        return None
    value = None
    if hasattr(headers, "get"):
        value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


def _default_system_prompt() -> str:
    path = Path(__file__).parent / "prompts" / "asm_agent.md"
    try:
        return path.read_text()
    except Exception:
        return "Explore the in-scope web application using only the provided tools."
