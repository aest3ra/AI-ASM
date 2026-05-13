from orbis.agent.planner import plan_local_actions


def test_local_planner_fills_visible_form_then_clicks_submit():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "login:POST:https://example.com/login",
                "fields": [
                    {"ref": "email", "test_value": "user@example.com"},
                    {"ref": "password", "test_value": "secret"},
                ],
                "submit_candidates": [{"ref": "login", "label": "Login"}],
            },
        ],
        "memory": {"attempted_forms": []},
    })

    assert [action.name for action in actions] == [
        "type_ref",
        "type_ref",
        "click_ref",
    ]
    assert [action.arguments["ref"] for action in actions] == [
        "email",
        "password",
        "login",
    ]
    assert actions[0].arguments["text"] == "user@example.com"


def test_local_planner_uses_select_ref_for_select_fields():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "search:GET:https://example.com/search",
                "fields": [
                    {
                        "ref": "kind",
                        "tag": "select",
                        "test_value": "title",
                    },
                    {"ref": "q", "tag": "input", "test_value": "term"},
                ],
                "submit_candidates": [{"ref": "search", "label": "Search"}],
            },
        ],
        "memory": {"attempted_forms": []},
    })

    assert [action.name for action in actions] == [
        "select_ref",
        "type_ref",
        "click_ref",
    ]
    assert actions[0].arguments == {
        "ref": "kind",
        "value": "title",
        "reason": "select visible form option with configured test data",
    }


def test_local_planner_uses_submit_form_for_post_form_without_submit_button():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "feedback:POST:https://example.com/feedback",
                "method": "POST",
                "form_ref": "f1",
                "fields": [{"ref": "body", "test_value": "hello"}],
                "submit_candidates": [],
            },
        ],
        "memory": {"attempted_forms": []},
    })

    assert [action.name for action in actions] == ["type_ref", "submit_form"]
    assert actions[1].arguments["ref"] == "f1"


def test_local_planner_scrolls_after_filling_form_without_visible_submit():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "login::https://example.com/#/login",
                "fields": [
                    {"ref": "email", "test_value": "user@example.com"},
                    {"ref": "password", "test_value": "secret"},
                ],
                "submit_candidates": [],
            },
        ],
        "memory": {"attempted_forms": []},
    })

    assert [action.name for action in actions] == [
        "type_ref",
        "type_ref",
        "scroll",
    ]


def test_local_planner_fills_remaining_fields_and_submits_typed_form():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "login::https://example.com/#/login",
                "fields": [
                    {"ref": "email", "test_value": "user@example.com"},
                    {"ref": "password", "test_value": "secret"},
                ],
                "submit_candidates": [{"ref": "login", "label": "Login"}],
            },
        ],
        "memory": {
            "attempted_forms": [],
            "forms_with_typed_fields": ["login::https://example.com/#/login"],
        },
    })

    assert [action.name for action in actions] == ["type_ref", "type_ref", "click_ref"]
    assert [action.arguments["ref"] for action in actions] == [
        "email",
        "password",
        "login",
    ]


def test_local_planner_submits_typed_form_when_no_fields_remain():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "login::https://example.com/#/login",
                "fields": [],
                "submit_candidates": [{"ref": "login", "label": "Login"}],
            },
        ],
        "memory": {
            "attempted_forms": [],
            "forms_with_typed_fields": ["login::https://example.com/#/login"],
        },
    })

    assert [action.name for action in actions] == ["click_ref"]
    assert actions[0].arguments["ref"] == "login"


def test_local_planner_skips_attempted_forms():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "login:POST:https://example.com/login",
                "fields": [{"ref": "email", "test_value": "user@example.com"}],
                "submit_candidates": [{"ref": "login", "label": "Login"}],
            },
        ],
        "memory": {
            "attempted_forms": ["login:POST:https://example.com/login"],
        },
    })

    assert actions == []


def test_local_planner_submits_ready_form_before_calling_llm():
    actions = plan_local_actions({
        "form_status": {
            "ready_to_submit": [
                {
                    "memory_key": "search:GET:https://example.com/search",
                    "submit_candidates": [{"ref": "search", "label": "Search"}],
                },
            ],
        },
    })

    assert [action.name for action in actions] == ["click_ref"]
    assert actions[0].arguments["ref"] == "search"


def test_local_planner_gives_up_on_empty_exploration_state():
    actions = plan_local_actions({
        "exploration_status": {"should_give_up": True},
    })

    assert [action.name for action in actions] == ["give_up"]


def test_local_planner_clicks_safe_navigation_without_llm():
    actions = plan_local_actions({
        "snapshot": {
            "refs": [
                {"ref": "r1", "tag": "button", "text": "Delete account"},
                {"ref": "r2", "tag": "button", "text": "Account menu"},
                {"ref": "r3", "tag": "a", "text": "About", "href": "/about"},
            ],
        },
        "visible_forms": [],
        "memory": {"attempted_forms": []},
    })

    assert [action.name for action in actions] == ["click_ref"]
    assert actions[0].arguments["ref"] == "r2"


def test_local_planner_prioritizes_safe_overlay_dismissal():
    actions = plan_local_actions({
        "snapshot": {
            "refs": [
                {"ref": "r1", "tag": "button", "text": "Account menu"},
                {"ref": "r2", "tag": "a", "aria_label": "dismiss cookie message"},
            ],
        },
        "visible_forms": [],
        "memory": {"attempted_forms": []},
    })

    assert [action.name for action in actions] == ["click_ref"]
    assert actions[0].arguments["ref"] == "r2"


def test_local_planner_skips_oauth_and_danger_navigation():
    actions = plan_local_actions({
        "snapshot": {
            "refs": [
                {"ref": "r1", "tag": "button", "text": "Login with Google"},
                {"ref": "r2", "tag": "button", "text": "Logout"},
            ],
        },
        "visible_forms": [],
        "memory": {"attempted_forms": []},
    })

    assert actions == []


def test_local_planner_leaves_ambiguous_unattempted_form_to_llm():
    actions = plan_local_actions({
        "visible_forms": [
            {
                "memory_key": "form:POST:https://example.com/upload",
                "fields": [],
                "submit_candidates": [],
            },
        ],
        "snapshot": {
            "refs": [
                {"ref": "r1", "tag": "button", "text": "Account menu"},
            ],
        },
        "memory": {"attempted_forms": []},
    })

    assert actions == []
