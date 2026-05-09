"""Shared safety helpers for active scanner actions."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

DANGER_KEYWORDS = {
    "logout",
    "log out",
    "log-out",
    "log_out",
    "sign out",
    "sign-out",
    "sign_out",
    "signout",
    "logoff",
    "delete",
    "destroy",
    "remove",
    "withdraw",
    "unsubscribe",
    "회원탈퇴",
    "탈퇴",
    "로그아웃",
    "삭제",
}


def matched_keyword(text: str, keywords: set[str]) -> str | None:
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def dangerous_url_keyword(url: str) -> str | None:
    """Return the matched dangerous keyword for URLs we should not actively hit."""
    parsed = urlparse(url)
    text = unquote(" ".join(part for part in (
        parsed.path,
        parsed.query,
        parsed.fragment,
    ) if part))
    return matched_keyword(text, DANGER_KEYWORDS)


def is_dangerous_url(url: str) -> bool:
    return dangerous_url_keyword(url) is not None
