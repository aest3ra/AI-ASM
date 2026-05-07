"""Shared agent safety/classification helpers."""

from __future__ import annotations

from typing import Any

DANGER_KEYWORDS = {
    "logout",
    "log out",
    "sign out",
    "delete",
    "remove",
    "withdraw",
    "unsubscribe",
    "회원탈퇴",
    "탈퇴",
    "로그아웃",
    "삭제",
}

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


def matched_keyword(text: str, keywords: set[str]) -> str | None:
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def _danger_text(info: dict[str, Any]) -> str:
    return " ".join(
        str(info.get(key) or "")
        for key in ("text", "aria_label", "name", "href", "form_action", "submit_text")
    ).lower()
