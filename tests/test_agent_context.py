from orbis.agent.context import ActionRecord, AgentMemory, NetworkDelta


def test_agent_memory_filters_failed_refs_by_stable_label():
    memory = AgentMemory()
    failed_ref = {
        "ref": "r1",
        "tag": "button",
        "text": "Account menu",
        "aria_label": "Account menu",
    }
    memory.remember_action(
        ActionRecord(
            turn=0,
            tool="click_ref",
            arguments={"ref": "r1"},
            ok=False,
            error="tool failed: TimeoutError",
        ),
        failed_ref,
    )

    snapshot = {
        "refs": [
            {
                "ref": "r9",
                "tag": "button",
                "text": "Account menu",
                "aria_label": "Account menu",
            },
            {
                "ref": "r10",
                "tag": "button",
                "text": "Orders",
                "aria_label": "Orders",
            },
        ],
    }

    filtered = memory.filter_snapshot(snapshot)

    assert [ref["text"] for ref in filtered["refs"]] == ["Orders"]
    assert memory.summary()["failed_refs"] == [
        {
            "ref": "r1",
            "label": "Account menu",
            "error": "tool failed: TimeoutError",
        },
    ]


def test_agent_memory_filters_clicked_click_refs_but_keeps_fields():
    memory = AgentMemory()
    clicked_ref = {
        "ref": "r1",
        "tag": "button",
        "text": "Login",
    }
    memory.remember_action(
        ActionRecord(
            turn=0,
            tool="click_ref",
            arguments={"ref": "r1"},
            ok=True,
        ),
        clicked_ref,
    )

    snapshot = {
        "refs": [
            {
                "ref": "r9",
                "tag": "button",
                "text": "Login",
            },
            {
                "ref": "r10",
                "tag": "input",
                "name": "email",
                "text": "Email",
            },
        ],
    }

    filtered = memory.filter_snapshot(snapshot)

    assert [ref["ref"] for ref in filtered["refs"]] == ["r10"]


def test_agent_memory_filters_typed_fields_but_keeps_buttons():
    memory = AgentMemory()
    typed_ref = {
        "ref": "r1",
        "tag": "input",
        "type": "email",
        "name": "email",
        "text": "Email",
    }
    memory.remember_action(
        ActionRecord(
            turn=0,
            tool="type_ref",
            arguments={"ref": "r1", "text": "user@example.com"},
            ok=True,
        ),
        typed_ref,
    )

    snapshot = {
        "refs": [
            {
                "ref": "r9",
                "tag": "input",
                "type": "email",
                "name": "email",
                "text": "Email",
            },
            {
                "ref": "r10",
                "tag": "button",
                "text": "Login",
            },
        ],
    }

    filtered = memory.filter_snapshot(snapshot)

    assert [ref["ref"] for ref in filtered["refs"]] == ["r10"]
    assert memory.summary()["typed_ref_count"] == 1


def test_agent_memory_filters_selected_fields():
    memory = AgentMemory()
    selected_ref = {
        "ref": "r1",
        "tag": "select",
        "name": "category",
        "text": "Category",
    }
    memory.remember_action(
        ActionRecord(
            turn=0,
            tool="select_ref",
            arguments={"ref": "r1", "value": "title"},
            ok=True,
        ),
        selected_ref,
    )

    snapshot = {
        "refs": [
            {
                "ref": "r9",
                "tag": "select",
                "name": "category",
                "text": "Category",
            },
            {
                "ref": "r10",
                "tag": "button",
                "text": "Search",
            },
        ],
    }

    filtered = memory.filter_snapshot(snapshot)

    assert [ref["ref"] for ref in filtered["refs"]] == ["r10"]
    assert memory.summary()["typed_ref_count"] == 1


def test_agent_memory_tracks_visited_state_summary():
    memory = AgentMemory()

    memory.remember_state("https://example.com/#/login", "abc")

    assert memory.has_seen_state("https://example.com/#/login", "abc") is True
    assert memory.has_seen_state("https://example.com/#/login", "def") is False
    assert memory.summary()["visited_urls"] == ["https://example.com/#/login"]


def test_agent_memory_tracks_typed_and_attempted_forms():
    memory = AgentMemory()

    memory.remember_typed_form("login::https://example.com/login")
    memory.remember_form_attempt("login::https://example.com/login")

    summary = memory.summary()
    assert summary["forms_with_typed_fields"] == []
    assert summary["attempted_forms"] == ["login::https://example.com/login"]


def test_agent_memory_can_share_attempted_forms_across_pages():
    shared = set()
    first = AgentMemory(attempted_form_keys=shared)
    second = AgentMemory(attempted_form_keys=shared)

    first.remember_form_attempt("login::https://example.com/login")

    assert second.summary()["attempted_forms"] == [
        "login::https://example.com/login",
    ]


def test_network_delta_reports_state_changes():
    delta = NetworkDelta(
        new_requests_count=1,
        new_requests=("GET https://example.com/api/me",),
        api_new_requests_count=1,
        api_new_requests=("GET https://example.com/api/me",),
        before_url="https://example.com/",
        after_url="https://example.com/account",
        before_dom_signature="a",
        after_dom_signature="b",
    )

    assert delta.url_changed is True
    assert delta.dom_changed is True
    assert delta.as_dict()["new_requests"] == ["GET https://example.com/api/me"]
    assert delta.as_dict()["api_new_requests"] == ["GET https://example.com/api/me"]
