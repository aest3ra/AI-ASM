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

DOWNLOAD_KEYWORDS = {
    "download",
    "file_down",
    "file-download",
    "file_download",
    "attach_down",
    "attachment",
    "첨부",
    "다운로드",
}


def matched_keyword(text: str, keywords: set[str]) -> str | None:
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def dangerous_url_keyword(url: str) -> str | None:
    """Return the matched dangerous keyword for URLs we should not actively hit."""
    return matched_keyword(_url_surface_text(url), DANGER_KEYWORDS)


def is_dangerous_url(url: str) -> bool:
    return dangerous_url_keyword(url) is not None


def download_url_keyword(url: str) -> str | None:
    """Return the matched download keyword for URLs we should not page-crawl."""
    return matched_keyword(_url_surface_text(url), DOWNLOAD_KEYWORDS)


def is_download_url(url: str) -> bool:
    return download_url_keyword(url) is not None


def _url_surface_text(url: str) -> str:
    parsed = urlparse(url)
    return unquote(" ".join(part for part in (
        parsed.path,
        parsed.query,
        parsed.fragment,
    ) if part))
