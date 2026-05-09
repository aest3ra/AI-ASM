"""Deterministic local action planner for obvious browser tasks."""

from __future__ import annotations

from typing import Any

from ai_asm.agent.safety import (
    interaction_key,
    is_click_candidate,
    label_for_ref,
    matches_danger,
)
from ai_asm.agent.tools import ToolCall

MAX_LOCAL_ACTIONS = 4
NAVIGATION_KEYWORDS = {
    "account",
    "admin",
    "api key",
    "basket",
    "billing",
    "cart",
    "chat",
    "chatbot",
    "complain",
    "contact",
    "dashboard",
    "forgot",
    "help",
    "home",
    "login",
    "menu",
    "more",
    "my page",
    "order",
    "photo",
    "profile",
    "register",
    "search",
    "setting",
    "sign in",
    "sign up",
    "support",
    "tab",
    "team",
    "user",
    "메뉴",
    "검색",
    "로그인",
    "설정",
    "장바구니",
    "프로필",
}
OAUTH_KEYWORDS = {"google", "github", "facebook", "oauth", "sso"}
SAFE_OVERLAY_KEYWORDS = {
    "close welcome",
    "cookie banner",
    "cookie message",
    "dismiss cookie",
    "welcome banner",
}


def plan_local_actions(context: dict[str, Any]) -> list[ToolCall]:
    """Return safe, obvious actions that do not need an LLM decision.

    The LLM is useful for semantic exploration. Filling a visible form with
    explicit test values is a mechanical step, so the driver can do it locally
    and reserve LLM calls for less structured UI decisions.
    """
    if _should_give_up(context):
        return [
            ToolCall(
                id="local-give-up",
                name="give_up",
                arguments={
                    "reason": "no useful visible local or LLM action remains",
                },
            )
        ]

    actions = _plan_ready_submit(context)
    if actions:
        return actions[:MAX_LOCAL_ACTIONS]

    actions = _plan_visible_form(context)
    if actions:
        return actions[:MAX_LOCAL_ACTIONS]

    actions = _plan_navigation_click(context)
    if actions:
        return actions

    return []


def _should_give_up(context: dict[str, Any]) -> bool:
    status = context.get("exploration_status")
    return isinstance(status, dict) and bool(status.get("should_give_up"))


def _plan_ready_submit(context: dict[str, Any]) -> list[ToolCall]:
    form_status = context.get("form_status")
    if not isinstance(form_status, dict):
        return []
    ready = form_status.get("ready_to_submit")
    if not isinstance(ready, list):
        return []
    for form in ready:
        if not isinstance(form, dict):
            continue
        action = _submit_action_for_form(form)
        if action is not None:
            return [action]
    return []


def _plan_visible_form(context: dict[str, Any]) -> list[ToolCall]:
    visible_forms = context.get("visible_forms")
    if not isinstance(visible_forms, list):
        return []
    attempted = _attempted_forms(context)
    typed = _typed_forms(context)
    for form in visible_forms:
        if not isinstance(form, dict):
            continue
        key = str(form.get("memory_key") or "")
        if key and key in attempted:
            continue
        if key and key in typed:
            fields = _field_actions(form)
            submit = _submit_action_for_form(form)
            if fields:
                if submit is not None:
                    return [*fields, submit]
                return fields
            if submit is not None:
                return [submit]
            return [_scroll_action()]
        fields = _field_actions(form)
        submit = _submit_action_for_form(form)
        if fields and submit is not None:
            return [*fields, submit]
        if fields:
            return [*fields, _scroll_action()]
    return []


def _plan_navigation_click(context: dict[str, Any]) -> list[ToolCall]:
    if _has_unattempted_form(context):
        return []
    snapshot = context.get("snapshot")
    refs = snapshot.get("refs") if isinstance(snapshot, dict) else []
    if not isinstance(refs, list):
        return []

    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        score = _navigation_score(ref)
        if score <= 0:
            continue
        candidates.append((score, -index, ref))

    if not candidates:
        return []
    _, _, ref = max(candidates)
    ref_id = str(ref.get("ref") or "")
    if not ref_id:
        return []
    return [
        ToolCall(
            id=f"local-nav-click-{ref_id}",
            name="click_ref",
            arguments={
                "ref": ref_id,
                "reason": "open safe navigation control without an LLM call",
            },
        )
    ]


def _field_actions(form: dict[str, Any]) -> list[ToolCall]:
    fields = form.get("fields")
    if not isinstance(fields, list):
        return []
    actions: list[ToolCall] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        ref = str(field.get("ref") or "")
        value = field.get("test_value")
        if not ref or value is None:
            continue
        if str(field.get("tag") or "").lower() == "select":
            actions.append(ToolCall(
                id=f"local-select-{ref}",
                name="select_ref",
                arguments={
                    "ref": ref,
                    "value": str(value),
                    "reason": "select visible form option with configured test data",
                },
            ))
            continue
        actions.append(ToolCall(
            id=f"local-type-{ref}",
            name="type_ref",
            arguments={
                "ref": ref,
                "text": str(value),
                "reason": "fill visible form field with configured test data",
            },
        ))
    return actions


def _has_unattempted_form(context: dict[str, Any]) -> bool:
    visible_forms = context.get("visible_forms")
    if not isinstance(visible_forms, list):
        return False
    attempted = _attempted_forms(context)
    for form in visible_forms:
        if not isinstance(form, dict):
            continue
        key = str(form.get("memory_key") or "")
        if not key or key not in attempted:
            return True
    return False


def _navigation_score(ref: dict[str, Any]) -> int:
    if not is_click_candidate(ref) or matches_danger(ref):
        return 0
    ref_id = str(ref.get("ref") or "")
    if not ref_id:
        return 0
    label = label_for_ref(ref)
    haystack = " ".join(
        str(ref.get(key) or "")
        for key in ("text", "aria_label", "name", "href", "role")
    ).lower()
    if not label and not str(ref.get("href") or ""):
        return 0
    if any(keyword in haystack for keyword in OAUTH_KEYWORDS):
        return 0

    role = str(ref.get("role") or "").lower()
    tag = str(ref.get("tag") or "").lower()
    href = str(ref.get("href") or "")
    score = 0
    if href:
        score += 30
    if role in {"tab", "menuitem"}:
        score += 25
    if tag == "a":
        score += 20
    if any(keyword in haystack for keyword in NAVIGATION_KEYWORDS):
        score += 50
    if any(keyword in haystack for keyword in SAFE_OVERLAY_KEYWORDS):
        score += 80
    if interaction_key(ref, label):
        score += 1
    return score


def _submit_action_for_form(form: dict[str, Any]) -> ToolCall | None:
    candidates = form.get("submit_candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            ref = str(candidate.get("ref") or "")
            if ref:
                return ToolCall(
                    id=f"local-submit-click-{ref}",
                    name="click_ref",
                    arguments={
                        "ref": ref,
                        "reason": "submit visible form after filling test data",
                    },
                )

    form_ref = str(form.get("form_ref") or "")
    method = str(form.get("method") or "").upper()
    if form_ref and method == "POST":
        return ToolCall(
            id=f"local-submit-form-{form_ref}",
            name="submit_form",
            arguments={
                "ref": form_ref,
                "reason": "submit visible POST form after filling test data",
            },
        )
    return None


def _attempted_forms(context: dict[str, Any]) -> set[str]:
    memory = context.get("memory")
    if not isinstance(memory, dict):
        return set()
    attempted = memory.get("attempted_forms")
    if not isinstance(attempted, list):
        return set()
    return {str(item) for item in attempted}


def _typed_forms(context: dict[str, Any]) -> set[str]:
    memory = context.get("memory")
    if not isinstance(memory, dict):
        return set()
    typed = memory.get("forms_with_typed_fields")
    if not isinstance(typed, list):
        return set()
    return {str(item) for item in typed}


def _scroll_action() -> ToolCall:
    return ToolCall(
        id="local-scroll-for-submit",
        name="scroll",
        arguments={
            "direction": "down",
            "reason": "reveal remaining form controls without an LLM call",
        },
    )
