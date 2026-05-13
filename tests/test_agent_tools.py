import asyncio

from sqlmodel import Session, select

from orbis.agent.budget import BudgetExceeded, BudgetTracker
from orbis.agent.tools import ToolCall, ToolExecutor
from orbis.config import ScopeConfig
from orbis.crawler.scope import Scope
from orbis.shared.decision_trace import DecisionTrace
from orbis.storage.db import FlaggedItem, Scan, open_db


class FakeToolPage:
    def __init__(self):
        self.actions = []
        self.refs = {
            "safe": {"text": "Open settings", "aria_label": "Open settings"},
            "logout": {"text": "Sign Out", "aria_label": "Sign Out"},
            "upload": {
                "text": "Upload profile photo",
                "aria_label": "Upload profile photo",
                "input_types": ["file"],
            },
            "external-form": {
                "text": "Send feedback",
                "form_action": "https://evil.example.net/submit",
            },
            "delete-form": {
                "text": "Delete account",
                "form_action": "https://example.com/account/delete",
            },
            "href-fallback": {
                "text": "Docs",
                "href": "/docs",
            },
        }

    async def describe_ref(self, ref):
        return self.refs.get(ref, {"text": "", "aria_label": ""})

    async def navigate(self, url):
        self.actions.append(("navigate", url))

    async def click_ref(self, ref):
        self.actions.append(("click_ref", ref))

    async def type_ref(self, ref, text):
        self.actions.append(("type_ref", ref, text))

    async def select_ref(self, ref, value):
        self.actions.append(("select_ref", ref, value))

    async def submit_form(self, ref):
        self.actions.append(("submit_form", ref))

    async def scroll(self, direction="down"):
        self.actions.append(("scroll", direction))

    async def get_text(self, ref):
        self.actions.append(("get_text", ref))
        return f"text:{ref}"


class FailingToolPage(FakeToolPage):
    async def click_ref(self, ref):
        raise TimeoutError("blocked")


class DryRunToolPage(FakeToolPage):
    async def click_ref(self, ref):
        self.actions.append(("click_ref", ref))
        self.last_aborted_mutations = [
            {
                "method": "POST",
                "url": "https://example.com/api-internal/apply-job?jobid=1",
                "resource_type": "fetch",
                "post_data_length": 32,
                "aborted": True,
            }
        ]


def _executor(tmp_path):
    engine = open_db(tmp_path / "agent.db")
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)
        scan_id = scan.id

    trace = DecisionTrace(scan_id=scan_id)
    executor = ToolExecutor(
        page=FakeToolPage(),
        scope=Scope(ScopeConfig(include_domains=["example.com"])),
        budget=BudgetTracker(max_steps=5),
        trace=trace,
        db_engine=engine,
        scan_id=scan_id,
        page_url="https://example.com/dashboard",
        auth_state_path="auth.json",
    )
    return executor, engine, trace


def test_tool_executor_runs_safe_tools_and_logs_trace(tmp_path):
    async def run():
        executor, _, trace = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t1",
            name="click_ref",
            arguments={"ref": "safe"},
        ))

        assert result.ok is True
        assert executor.page.actions == [("click_ref", "safe")]
        events = await trace.events()
        assert [event.kind for event in events] == ["tool_call"]
        assert events[0].payload["tool"] == "click_ref"

    asyncio.run(run())


def test_tool_executor_converts_runtime_failures_to_tool_results(tmp_path):
    async def run():
        executor, _, trace = _executor(tmp_path)
        executor.page = FailingToolPage()

        result = await executor.execute(ToolCall(
            id="t-runtime",
            name="click_ref",
            arguments={"ref": "safe"},
        ))

        assert result.ok is False
        assert result.error == "tool failed: TimeoutError: blocked"
        events = await trace.events()
        assert events[0].kind == "tool_call"
        assert events[0].payload["ok"] is False

    asyncio.run(run())


def test_tool_executor_falls_back_to_in_scope_href_when_click_times_out(tmp_path):
    async def run():
        executor, _, _ = _executor(tmp_path)
        executor.page = FailingToolPage()

        result = await executor.execute(ToolCall(
            id="t-fallback",
            name="click_ref",
            arguments={"ref": "href-fallback"},
        ))

        assert result.ok is True
        assert result.data["fallback"] == "navigate"
        assert result.data["url"] == "https://example.com/docs"
        assert executor.page.actions == [("navigate", "https://example.com/docs")]

    asyncio.run(run())


def test_tool_executor_rejects_missing_required_args_without_page_action(tmp_path):
    async def run():
        executor, engine, trace = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t-missing",
            name="type_ref",
            arguments={},
        ))

        assert result.ok is False
        assert result.error == "rejected: missing required args: ref, text"
        assert executor.page.actions == []
        events = await trace.events()
        assert events[0].kind == "tool_rejected"
        assert events[0].payload["flag_kind"] == "agent_invalid_args"
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows[0].flag_kind == "agent_invalid_args"
        assert rows[0].item_kind == "type_ref"

    asyncio.run(run())


def test_tool_executor_runs_select_ref(tmp_path):
    async def run():
        executor, _, _ = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t-select",
            name="select_ref",
            arguments={"ref": "safe", "value": "title"},
        ))

        assert result.ok is True
        assert executor.page.actions == [("select_ref", "safe", "title")]

    asyncio.run(run())


def test_tool_executor_reports_aborted_mutations_from_dry_run_click(tmp_path):
    async def run():
        executor, _, _ = _executor(tmp_path)
        executor.page = DryRunToolPage()

        result = await executor.execute(ToolCall(
            id="t-dry-run",
            name="click_ref",
            arguments={"ref": "safe"},
        ))

        assert result.ok is True
        assert result.data["aborted_mutations"] == [
            {
                "method": "POST",
                "url": "https://example.com/api-internal/apply-job?jobid=1",
                "resource_type": "fetch",
                "post_data_length": 32,
                "aborted": True,
            }
        ]

    asyncio.run(run())


def test_tool_executor_rejects_out_of_scope_navigate_and_records_flag(tmp_path):
    async def run():
        executor, engine, trace = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t2",
            name="navigate",
            arguments={"url": "https://evil.example.net/admin"},
        ))

        assert result.ok is False
        assert "out of scope" in result.error
        assert executor.page.actions == []
        assert [event.kind for event in await trace.events()] == ["tool_rejected"]
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert [(row.flag_kind, row.item_kind, row.url) for row in rows] == [
            ("agent_scope", "navigate", "https://evil.example.net/admin"),
        ]

    asyncio.run(run())


def test_tool_executor_rejects_blacklisted_click(tmp_path):
    async def run():
        executor, engine, _ = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t3",
            name="click_ref",
            arguments={"ref": "logout"},
        ))

        assert result.ok is False
        assert "blacklist" in result.error
        assert executor.page.actions == []
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows[0].flag_kind == "agent_blacklist"
        assert rows[0].item_kind == "click"

    asyncio.run(run())


def test_tool_executor_rejects_blacklisted_navigation(tmp_path):
    async def run():
        executor, engine, _ = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t3b",
            name="navigate",
            arguments={"url": "https://example.com/ilos/lo/logout.acl"},
        ))

        assert result.ok is False
        assert "blacklist" in result.error
        assert executor.page.actions == []
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows[0].flag_kind == "agent_blacklist"
        assert rows[0].item_kind == "navigate"

    asyncio.run(run())


def test_tool_executor_allows_upload_labeled_forms(tmp_path):
    async def run():
        executor, engine, _ = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t4",
            name="submit_form",
            arguments={"ref": "upload"},
        ))

        assert result.ok is True
        assert executor.page.actions == [("submit_form", "upload")]
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows == []

    asyncio.run(run())


def test_tool_executor_rejects_blacklisted_submit_form(tmp_path):
    async def run():
        executor, engine, _ = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t4b",
            name="submit_form",
            arguments={"ref": "delete-form"},
        ))

        assert result.ok is False
        assert "blacklist" in result.error
        assert executor.page.actions == []
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows[0].flag_kind == "agent_blacklist"
        assert rows[0].item_kind == "form_submit"

    asyncio.run(run())


def test_tool_executor_rejects_out_of_scope_form_action(tmp_path):
    async def run():
        executor, engine, trace = _executor(tmp_path)

        result = await executor.execute(ToolCall(
            id="t-form-scope",
            name="submit_form",
            arguments={"ref": "external-form"},
        ))

        assert result.ok is False
        assert "out of scope" in result.error
        assert executor.page.actions == []
        events = await trace.events()
        assert events[0].kind == "tool_rejected"
        assert events[0].payload["flag_kind"] == "agent_scope"
        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows[0].item_kind == "form_submit"
        assert rows[0].url == "https://evil.example.net/submit"

    asyncio.run(run())


def test_budget_tracker_enforces_step_cap():
    budget = BudgetTracker(max_steps=1)
    budget.consume_step()

    try:
        budget.consume_step()
    except BudgetExceeded as exc:
        assert "step budget exceeded" in str(exc)
    else:
        raise AssertionError("BudgetExceeded was not raised")
