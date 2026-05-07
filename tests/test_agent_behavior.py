import json

from ai_asm.agent.behavior import analyze_trace_file


def test_agent_behavior_detects_click_timeout_and_missing_feedback_loop(tmp_path):
    trace = tmp_path / "trace.jsonl"
    events = [
        {
            "kind": "agent_turn",
            "page_url": "https://example.com/",
            "payload": {
                "source": "local_planner",
                "tool_calls": ["click_ref", "click_ref", "click_ref"],
                "input_tokens": 100,
                "output_tokens": 20,
            },
        },
        {
            "kind": "tool_call",
            "page_url": "https://example.com/",
            "payload": {
                "tool": "click_ref",
                "ok": False,
                "error": "tool failed: TimeoutError",
            },
        },
        {
            "kind": "tool_call",
            "page_url": "https://example.com/",
            "payload": {
                "tool": "click_ref",
                "ok": False,
                "error": "tool failed: TimeoutError",
            },
        },
        {
            "kind": "tool_call",
            "page_url": "https://example.com/",
            "payload": {
                "tool": "click_ref",
                "ok": False,
                "error": "tool failed: TimeoutError",
            },
        },
        {
            "kind": "agent_turn",
            "page_url": "https://example.com/contact",
            "payload": {
                "tool_calls": [],
                "input_tokens": 50,
                "output_tokens": 10,
            },
        },
        {
            "kind": "action_record",
            "page_url": "https://example.com/contact",
            "payload": {
                "network_delta": {
                    "new_requests_count": 1,
                    "api_new_requests_count": 1,
                    "dom_changed": True,
                    "url_changed": False,
                },
            },
        },
        {
            "kind": "state_checkpoint",
            "page_url": "https://example.com/contact",
            "payload": {"dom_signature": "abc"},
        },
    ]
    trace.write_text("\n".join(json.dumps(event) for event in events))

    summary = analyze_trace_file(trace)

    assert summary.turns == 2
    assert summary.local_planner_turns == 1
    assert summary.tool_calls == 3
    assert summary.failed_tool_calls == 3
    assert summary.timeout_failures == 3
    assert summary.empty_turns == 1
    assert summary.pages_with_failed_tools_no_followup == 1
    assert summary.action_records == 1
    assert summary.state_checkpoints == 1
    assert summary.actions_with_new_requests == 1
    assert summary.actions_with_api_new_requests == 1
    assert summary.actions_with_dom_change == 1
    assert summary.click_failure_rate == 1.0
    assert any("High click failure rate" in item for item in summary.findings)
    assert any("not fed back" in item for item in summary.findings)


def test_agent_behavior_counts_successful_form_and_scroll(tmp_path):
    trace = tmp_path / "trace.jsonl"
    events = [
        {
            "kind": "agent_turn",
            "page_url": "https://example.com/",
            "payload": {
                "tool_calls": ["scroll", "submit_form"],
            },
        },
        {
            "kind": "tool_call",
            "page_url": "https://example.com/",
            "payload": {"tool": "scroll", "ok": True},
        },
        {
            "kind": "tool_call",
            "page_url": "https://example.com/",
            "payload": {"tool": "submit_form", "ok": True},
        },
    ]
    trace.write_text("\n".join(json.dumps(event) for event in events))

    summary = analyze_trace_file(trace)

    assert summary.tool_success["scroll"] == 1
    assert summary.tool_success["submit_form"] == 1
    assert not any("No form submission" in item for item in summary.findings)
    assert not any("No scroll" in item for item in summary.findings)
