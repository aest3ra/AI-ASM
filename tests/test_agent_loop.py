import asyncio

from ai_asm.agent.budget import BudgetTracker
from ai_asm.agent.client import AgentResponse, MockLLMClient
from ai_asm.agent.loop import AgentLoop
from ai_asm.agent.tools import ToolCall, ToolResult
from ai_asm.shared.decision_trace import DecisionTrace


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.budget = BudgetTracker(max_steps=10)

    async def execute(self, call):
        self.calls.append(call)
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            ok=True,
            data={"called": call.name},
        )


def test_mock_agent_loop_executes_tool_calls_and_logs_turn():
    async def run():
        trace = DecisionTrace(scan_id=1)
        executor = FakeExecutor()
        client = MockLLMClient([
            AgentResponse(
                tool_calls=[
                    ToolCall(id="1", name="scroll", arguments={"direction": "down"}),
                    ToolCall(id="2", name="give_up", arguments={"reason": "done"}),
                ],
                input_tokens=10,
                output_tokens=3,
            ),
        ])

        result = await AgentLoop(
            client=client,
            executor=executor,
            trace=trace,
            page_url="https://example.com/",
        ).run_once({"snapshot": "button list"})

        assert [call.name for call in executor.calls] == ["scroll", "give_up"]
        assert [item.tool for item in result.tool_results] == ["scroll", "give_up"]
        events = await trace.events()
        assert [event.kind for event in events] == ["agent_turn"]
        assert events[0].payload["tool_calls"] == ["scroll", "give_up"]

    asyncio.run(run())


def test_mock_client_default_gives_up_when_script_empty():
    async def run():
        response = await MockLLMClient().complete({})
        assert response.tool_calls == [
            ToolCall(id="mock-give-up", name="give_up", arguments={"reason": "mock_done"}),
        ]

    asyncio.run(run())


def test_agent_loop_run_page_observes_between_single_actions():
    async def run():
        trace = DecisionTrace(scan_id=1)
        executor = FakeExecutor()
        contexts = []
        after_calls = []
        state = {"failed_refs": []}
        client = MockLLMClient([
            AgentResponse(
                tool_calls=[
                    ToolCall(id="1", name="click_ref", arguments={"ref": "r1"}),
                    ToolCall(id="ignored", name="click_ref", arguments={"ref": "r2"}),
                ],
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(id="2", name="scroll", arguments={"direction": "down"}),
                ],
            ),
        ])

        async def context_factory():
            contexts.append(dict(state))
            return {"memory": dict(state)}

        async def after_action(turn, call, result):
            after_calls.append((turn, call.name, result.ok))
            state["failed_refs"] = ["r1"]
            return True

        result = await AgentLoop(
            client=client,
            executor=executor,
            trace=trace,
            page_url="https://example.com/",
        ).run_page(
            context_factory=context_factory,
            after_action=after_action,
            max_turns=2,
        )

        assert [call.name for call in executor.calls] == ["click_ref", "scroll"]
        assert [call.arguments for call in executor.calls] == [
            {"ref": "r1"},
            {"direction": "down"},
        ]
        assert after_calls == [(0, "click_ref", True), (1, "scroll", True)]
        assert contexts == [{"failed_refs": []}, {"failed_refs": ["r1"]}]
        assert result.turns == 2
        events = await trace.events()
        assert [event.payload["turn"] for event in events] == [0, 1]

    asyncio.run(run())


def test_agent_loop_run_page_batches_same_form_fields_then_submit_click():
    async def run():
        executor = FakeExecutor()
        after_calls = []
        client = MockLLMClient([
            AgentResponse(
                tool_calls=[
                    ToolCall(id="1", name="type_ref", arguments={"ref": "email", "text": "a"}),
                    ToolCall(id="2", name="type_ref", arguments={"ref": "password", "text": "b"}),
                    ToolCall(id="3", name="click_ref", arguments={"ref": "login"}),
                    ToolCall(id="ignored", name="click_ref", arguments={"ref": "about"}),
                ],
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(id="4", name="give_up", arguments={"reason": "done"}),
                ],
            ),
        ])

        async def context_factory():
            return {}

        async def after_action(turn, call, result):
            after_calls.append((turn, call.name, result.ok))
            return True

        result = await AgentLoop(
            client=client,
            executor=executor,
        ).run_page(
            context_factory=context_factory,
            after_action=after_action,
            max_turns=2,
        )

        assert [call.name for call in executor.calls] == [
            "type_ref",
            "type_ref",
            "click_ref",
            "give_up",
        ]
        assert [call.arguments["ref"] for call in executor.calls[:3]] == [
            "email",
            "password",
            "login",
        ]
        assert after_calls == [
            (0, "type_ref", True),
            (0, "type_ref", True),
            (0, "click_ref", True),
            (1, "give_up", True),
        ]
        assert result.turns == 2

    asyncio.run(run())


def test_agent_loop_run_page_uses_local_planner_before_llm():
    async def run():
        executor = FakeExecutor()
        client = MockLLMClient([
            AgentResponse(tool_calls=[
                ToolCall(id="llm", name="click_ref", arguments={"ref": "nav"}),
            ])
        ])
        after_calls = []

        async def context_factory():
            return {"visible_forms": ["login"]}

        def local_planner(context):
            return [
                ToolCall(id="local-1", name="type_ref", arguments={"ref": "email", "text": "a"}),
                ToolCall(id="local-2", name="click_ref", arguments={"ref": "login"}),
            ]

        async def after_action(turn, call, result):
            after_calls.append((turn, call.name, result.ok))
            return True

        result = await AgentLoop(
            client=client,
            executor=executor,
        ).run_page(
            context_factory=context_factory,
            after_action=after_action,
            local_planner=local_planner,
            max_turns=1,
        )

        assert client.calls == []
        assert [call.name for call in executor.calls] == ["type_ref", "click_ref"]
        assert after_calls == [(0, "type_ref", True), (0, "click_ref", True)]
        assert result.turns == 1

    asyncio.run(run())


def test_agent_loop_run_page_can_stop_after_no_progress():
    async def run():
        executor = FakeExecutor()
        client = MockLLMClient([
            AgentResponse(tool_calls=[
                ToolCall(id="1", name="click_ref", arguments={"ref": "r1"}),
            ]),
            AgentResponse(tool_calls=[
                ToolCall(id="2", name="click_ref", arguments={"ref": "r2"}),
            ]),
        ])

        async def context_factory():
            return {}

        async def after_action(turn, call, result):
            return False

        result = await AgentLoop(
            client=client,
            executor=executor,
        ).run_page(
            context_factory=context_factory,
            after_action=after_action,
            max_turns=5,
        )

        assert [call.arguments for call in executor.calls] == [{"ref": "r1"}]
        assert result.turns == 1

    asyncio.run(run())


def test_agent_loop_run_page_calls_before_action_right_before_execute():
    async def run():
        executor = FakeExecutor()
        client = MockLLMClient([
            AgentResponse(tool_calls=[
                ToolCall(id="1", name="click_ref", arguments={"ref": "r1"}),
            ]),
        ])
        events = []

        async def context_factory():
            events.append("context")
            return {}

        async def before_action(turn, call):
            events.append(("before", turn, call.name, len(executor.calls)))

        async def after_action(turn, call, result):
            events.append(("after", turn, call.name, len(executor.calls)))
            return True

        await AgentLoop(
            client=client,
            executor=executor,
        ).run_page(
            context_factory=context_factory,
            before_action=before_action,
            after_action=after_action,
            max_turns=1,
        )

        assert events == [
            "context",
            ("before", 0, "click_ref", 0),
            ("after", 0, "click_ref", 1),
        ]

    asyncio.run(run())
