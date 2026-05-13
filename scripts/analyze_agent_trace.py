from __future__ import annotations

import argparse
from pathlib import Path

from orbis.agent.behavior import analyze_trace_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze orbis agent behavior from trace jsonl.",
    )
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()

    summary = analyze_trace_file(args.trace)
    print(f"events={summary.events}")
    print(f"pages={summary.pages}")
    print(f"turns={summary.turns}")
    print(f"local_planner_turns={summary.local_planner_turns}")
    print(f"empty_turns={summary.empty_turns}")
    print(f"tool_calls={summary.tool_calls}")
    print(f"successful_tool_calls={summary.successful_tool_calls}")
    print(f"failed_tool_calls={summary.failed_tool_calls}")
    print(f"timeout_failures={summary.timeout_failures}")
    print(f"action_records={summary.action_records}")
    print(f"state_checkpoints={summary.state_checkpoints}")
    print(f"actions_with_new_requests={summary.actions_with_new_requests}")
    print(f"actions_with_api_new_requests={summary.actions_with_api_new_requests}")
    print(f"actions_with_dom_change={summary.actions_with_dom_change}")
    print(f"actions_with_url_change={summary.actions_with_url_change}")
    print(f"tool_failure_rate={summary.tool_failure_rate:.1%}")
    print(f"click_failure_rate={summary.click_failure_rate:.1%}")
    print(f"input_tokens={summary.input_tokens}")
    print(f"output_tokens={summary.output_tokens}")
    print(f"cache_read_input_tokens={summary.cache_read_input_tokens}")
    print(f"tool_requested={dict(summary.tool_requested)}")
    print(f"tool_success={dict(summary.tool_success)}")
    print(f"tool_failed={dict(summary.tool_failed)}")
    print(f"failed_refs={dict(summary.failed_ref_labels.most_common(10))}")
    print(
        "pages_with_failed_tools_no_followup="
        f"{summary.pages_with_failed_tools_no_followup}",
    )
    print(f"click_timeout_pages={summary.click_timeout_pages}")
    print("\nfindings:")
    for finding in summary.findings:
        print(f"- {finding}")


if __name__ == "__main__":
    main()
