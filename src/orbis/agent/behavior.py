"""Analyze agent trace events into actionable behavior diagnostics."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PageBehavior:
    page_url: str
    turns: int = 0
    tool_calls: int = 0
    failed_tool_calls: int = 0
    timeout_failures: int = 0
    empty_turns: int = 0


@dataclass
class AgentBehaviorSummary:
    events: int = 0
    pages: int = 0
    turns: int = 0
    local_planner_turns: int = 0
    empty_turns: int = 0
    tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    rejected_tool_calls: int = 0
    timeout_failures: int = 0
    llm_failures: int = 0
    action_records: int = 0
    state_checkpoints: int = 0
    actions_with_new_requests: int = 0
    actions_with_api_new_requests: int = 0
    actions_with_dom_change: int = 0
    actions_with_url_change: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    tool_requested: Counter[str] = field(default_factory=Counter)
    tool_success: Counter[str] = field(default_factory=Counter)
    tool_failed: Counter[str] = field(default_factory=Counter)
    failed_ref_labels: Counter[str] = field(default_factory=Counter)
    page_behaviors: list[PageBehavior] = field(default_factory=list)
    pages_with_failed_tools_no_followup: int = 0
    click_timeout_pages: int = 0
    findings: list[str] = field(default_factory=list)

    @property
    def tool_failure_rate(self) -> float:
        if not self.tool_calls:
            return 0.0
        return self.failed_tool_calls / self.tool_calls

    @property
    def click_failure_rate(self) -> float:
        total = self.tool_success["click_ref"] + self.tool_failed["click_ref"]
        if not total:
            return 0.0
        return self.tool_failed["click_ref"] / total


def analyze_trace_file(path: str | Path) -> AgentBehaviorSummary:
    return analyze_trace_events(_load_events(path))


def analyze_trace_events(events: list[dict[str, Any]]) -> AgentBehaviorSummary:
    summary = AgentBehaviorSummary(events=len(events))
    per_page = defaultdict(lambda: {
        "turns": 0,
        "tool_calls": 0,
        "failed_tool_calls": 0,
        "timeout_failures": 0,
        "empty_turns": 0,
    })

    for event in events:
        page_url = str(event.get("page_url") or "")
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if page_url:
            page = per_page[page_url]
        else:
            page = None

        if kind == "agent_turn":
            summary.turns += 1
            if payload.get("source") == "local_planner":
                summary.local_planner_turns += 1
            tool_calls = [
                str(tool) for tool in payload.get("tool_calls") or []
            ]
            summary.tool_requested.update(tool_calls)
            summary.input_tokens += int(payload.get("input_tokens") or 0)
            summary.output_tokens += int(payload.get("output_tokens") or 0)
            summary.cache_read_input_tokens += int(
                payload.get("cache_read_input_tokens") or 0,
            )
            if not tool_calls:
                summary.empty_turns += 1
                if page is not None:
                    page["empty_turns"] += 1
            if page is not None:
                page["turns"] += 1

        elif kind == "tool_call":
            tool = str(payload.get("tool") or "")
            ok = bool(payload.get("ok"))
            error = str(payload.get("error") or "")
            summary.tool_calls += 1
            if ok:
                summary.successful_tool_calls += 1
                summary.tool_success[tool] += 1
            else:
                summary.failed_tool_calls += 1
                summary.tool_failed[tool] += 1
                ref_label = _ref_label(payload)
                if ref_label:
                    summary.failed_ref_labels[ref_label] += 1
                if "TimeoutError" in error:
                    summary.timeout_failures += 1
                    if page is not None:
                        page["timeout_failures"] += 1
            if page is not None:
                page["tool_calls"] += 1
                if not ok:
                    page["failed_tool_calls"] += 1

        elif kind == "tool_rejected":
            tool = str(payload.get("tool") or "")
            summary.rejected_tool_calls += 1
            summary.tool_failed[tool] += 1

        elif kind == "llm_failure":
            summary.llm_failures += 1

        elif kind == "action_record":
            summary.action_records += 1
            delta = payload.get("network_delta") or {}
            if int(delta.get("new_requests_count") or 0) > 0:
                summary.actions_with_new_requests += 1
            if int(delta.get("api_new_requests_count") or 0) > 0:
                summary.actions_with_api_new_requests += 1
            if delta.get("dom_changed"):
                summary.actions_with_dom_change += 1
            if delta.get("url_changed"):
                summary.actions_with_url_change += 1

        elif kind == "state_checkpoint":
            summary.state_checkpoints += 1

    summary.page_behaviors = [
        PageBehavior(
            page_url=page_url,
            turns=data["turns"],
            tool_calls=data["tool_calls"],
            failed_tool_calls=data["failed_tool_calls"],
            timeout_failures=data["timeout_failures"],
            empty_turns=data["empty_turns"],
        )
        for page_url, data in sorted(per_page.items())
    ]
    summary.pages = len(summary.page_behaviors)
    summary.pages_with_failed_tools_no_followup = sum(
        1 for page in summary.page_behaviors
        if page.turns <= 1 and page.failed_tool_calls > 0
    )
    summary.click_timeout_pages = sum(
        1 for page in summary.page_behaviors
        if page.timeout_failures > 0
    )
    summary.findings = _findings(summary)
    return summary


def _findings(summary: AgentBehaviorSummary) -> list[str]:
    findings: list[str] = []
    if summary.llm_failures:
        findings.append(f"LLM API failure occurred {summary.llm_failures} time(s).")
    if summary.click_failure_rate >= 0.5 and summary.tool_failed["click_ref"] >= 3:
        findings.append(
            "High click failure rate: snapshot likely exposes blocked, hidden, "
            "or overlay-covered controls as equally actionable.",
        )
    if summary.pages_with_failed_tools_no_followup:
        findings.append(
            "Failed tools are not fed back into a second agent turn on the same "
            "page, so the model cannot recover from bad clicks.",
        )
    if summary.action_records and not summary.actions_with_api_new_requests:
        findings.append(
            "No agent action produced a new API request; LLM behavior did not "
            "directly expand API coverage in this trace.",
        )
    if summary.empty_turns:
        findings.append(
            "Some turns returned no tool calls; prompt/tool-choice needs a "
            "clearer exploration fallback.",
        )
    if (
        summary.tool_success["submit_form"] == 0
        and summary.tool_requested["submit_form"] == 0
    ):
        findings.append(
            "No form submission was attempted; the agent is mostly clicking "
            "instead of using available test data.",
        )
    if summary.tool_success["scroll"] == 0 and summary.tool_requested["scroll"] == 0:
        findings.append(
            "No scroll tool was used; below-the-fold controls may be underexplored.",
        )
    if not findings:
        findings.append("No obvious behavior issue detected in this trace.")
    return findings


def _ref_label(payload: dict[str, Any]) -> str:
    ref_context = payload.get("ref_context")
    if not isinstance(ref_context, dict):
        return ""
    ref = str(ref_context.get("ref") or "")
    label = (
        str(ref_context.get("text") or "")
        or str(ref_context.get("aria_label") or "")
        or str(ref_context.get("name") or "")
        or str(ref_context.get("href") or "")
    )
    label = " ".join(label.split())
    if not label and not ref:
        return ""
    if len(label) > 80:
        label = label[:77] + "..."
    return f"{ref} {label}".strip()


def _load_events(path: str | Path) -> list[dict[str, Any]]:
    events = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events
