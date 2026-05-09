from ai_asm.agent.safety import classify_form_text, safe_tool_arguments


def test_classify_form_text_uses_shared_form_kind_rules():
    assert classify_form_text("Create account sign up") == "register"
    assert classify_form_text("이메일 비밀번호 로그인") == "login"
    assert classify_form_text("Search query") == "search"
    assert classify_form_text("Contact us") == "form"


def test_safe_tool_arguments_redacts_type_ref_text():
    assert safe_tool_arguments("type_ref", {"ref": "r1", "text": "secret"}) == {
        "ref": "r1",
        "text": "<redacted len=6>",
    }
    assert safe_tool_arguments("click_ref", {"ref": "r1"}) == {"ref": "r1"}
