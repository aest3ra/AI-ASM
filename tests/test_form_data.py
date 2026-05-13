from orbis.agent.form_data import FormDataSet


def test_form_data_loads_defaults_and_field_overrides(tmp_path):
    path = tmp_path / "forms.yaml"
    path.write_text("""
defaults:
  text: default-text
fields:
  email: user@example.com
  message: hello
""")

    data = FormDataSet.load(path)

    assert data.value_for_field({"type": "text", "name": "unknown"}) == "default-text"
    assert data.value_for_field({"type": "email", "name": "email"}) == "user@example.com"
    assert data.value_for_field({"tag": "textarea", "name": "message"}) == "hello"


def test_form_values_are_keyed_by_field_name():
    data = FormDataSet(fields={"email": "user@example.com"})

    values = data.values_for_form({
        "input_fields": [
            {"type": "email", "name": "email"},
            {"type": "password", "name": "password"},
        ]
    })

    assert values == {
        "email": "user@example.com",
        "password": "Password123!",
    }


def test_form_data_uses_visible_text_as_lookup_key():
    data = FormDataSet(fields={"email": "user@example.com"})

    assert data.value_for_field({"type": "text", "text": "Email"}) == "user@example.com"


def test_form_data_infers_semantic_defaults_from_text_inputs():
    data = FormDataSet()

    assert data.value_for_field({"type": "text", "name": "email"}) == "orbis@example.com"
    assert data.value_for_field({"type": "text", "name": "phone"}) == "5551234567"
    assert data.value_for_field({"type": "text", "name": "linkedin"}) == "https://example.com"
