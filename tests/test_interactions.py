import pytest

from ai_asm.crawler.interactions import interaction_key, is_dangerous


@pytest.mark.parametrize("text", [
    "Delete account",
    "Logout",
    "Sign out",
    "sign-out",
    "Cancel subscription",
    "Buy now",
    "Purchase",
    "Unsubscribe",
    "삭제",
    "탈퇴하기",
    "로그아웃",
    "결제하기",
    "취소",
    "환불 신청",
])
def test_dangerous(text: str):
    assert is_dangerous(text)


@pytest.mark.parametrize("text", [
    "Search",
    "Next",
    "Show more",
    "Filter",
    "Sort",
    "Apply",
    "더보기",
    "검색",
    "확인",
    "닫기",
])
def test_safe(text: str):
    assert not is_dangerous(text)


def test_empty():
    assert not is_dangerous("")
    assert not is_dangerous(None)  # type: ignore[arg-type]


def test_interaction_key_normalizes_text_and_route_target():
    assert interaction_key("  Login  ", "/#/login", "button") == "button|login|/#/login"
    assert interaction_key("Login", "/#/login", "button") == interaction_key(
        " login ",
        "/#/login",
        "button",
    )
