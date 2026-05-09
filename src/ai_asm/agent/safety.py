"""Shared agent safety/classification helpers."""

from __future__ import annotations

from typing import Any

from ai_asm.safety import DANGER_KEYWORDS, matched_keyword


def classify_form_text(text: str) -> str:
    lowered = text.lower()
    if "register" in lowered or "sign up" in lowered or "가입" in lowered:
        return "register"
    if "signup" in lowered or "sign-up" in lowered:
        return "register"
    if (
        "password" in lowered
        or "login" in lowered
        or "sign in" in lowered
        or "로그인" in lowered
    ):
        return "login"
    if "search" in lowered or "query" in lowered or "검색" in lowered:
        return "search"
    return "form"


def is_click_candidate(info: dict[str, Any]) -> bool:
    tag = str(info.get("tag") or "").lower()
    role = str(info.get("role") or "").lower()
    return tag in {"button", "a"} or role in {"button", "tab", "menuitem"}


def matches_danger(info: dict[str, Any]) -> bool:
    return matched_danger_keyword(info) is not None


def matched_danger_keyword(info: dict[str, Any]) -> str | None:
    return matched_keyword(_danger_text(info), DANGER_KEYWORDS)


def label_for_ref(info: dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("text", "aria_label", "name", "submit_text"):
        value = " ".join(str(info.get(key) or "").strip().split())
        normalized = value.lower()
        if not value or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(value)
    return " ".join(parts).strip()


def interaction_key(info: dict[str, Any], label: str | None = None) -> str:
    kind = str(info.get("role") or info.get("tag") or "button").strip().lower()
    normalized = " ".join((label or label_for_ref(info)).lower().split())
    href = str(info.get("href") or "").strip()
    return f"{kind}|{normalized}|{href}"


def safe_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return tool arguments safe for trace/log output."""
    safe = dict(arguments)
    if tool_name == "type_ref" and "text" in safe:
        safe["text"] = f"<redacted len={len(str(safe['text']))}>"
    return safe


def _danger_text(info: dict[str, Any]) -> str:
    return " ".join(
        str(info.get(key) or "")
        for key in ("text", "aria_label", "name", "href", "form_action", "submit_text")
    ).lower()
