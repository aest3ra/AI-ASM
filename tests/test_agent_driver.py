import asyncio

from sqlmodel import Session, select

from ai_asm.agent.client import HeuristicMockLLMClient
from ai_asm.agent.context import AgentMemory, NetworkDelta
from ai_asm.agent.driver import (
    AgentObservation,
    PlaywrightToolPage,
    _exploration_status,
    _form_status,
    _form_memory_key,
    _made_meaningful_progress,
    _network_delta,
    _visible_forms_from_snapshot,
    run_agent_interactions,
    run_mock_agent_interactions,
)
from ai_asm.agent.form_data import FormDataSet
from ai_asm.agent.network import NetworkEventBuffer
from ai_asm.agent.tools import ToolCall, ToolResult
from ai_asm.config import ScopeConfig
from ai_asm.crawler.scope import Scope
from ai_asm.shared.decision_trace import DecisionTrace
from ai_asm.storage.db import FlaggedItem, Scan, open_db


def test_heuristic_mock_client_emits_safe_tools_from_snapshot():
    async def run():
        clicked_keys: set[str] = set()
        client = HeuristicMockLLMClient(max_clicks=5, clicked_keys=clicked_keys)

        response = await client.complete({
            "snapshot": {
                "refs": [
                    {
                        "ref": "r1",
                        "tag": "button",
                        "text": "Open settings",
                    },
                    {
                        "ref": "r2",
                        "tag": "button",
                        "text": "Sign Out",
                    },
                    {
                        "ref": "r3",
                        "tag": "form",
                        "text": "Feedback",
                        "form_method": "POST",
                        "input_types": ["text"],
                        "input_fields": [
                            {"type": "email", "name": "email"},
                        ],
                    },
                    {
                        "ref": "r4",
                        "tag": "form",
                        "text": "Login",
                        "form_method": "POST",
                        "input_types": ["password"],
                    },
                ]
            }
        })

        assert response.tool_calls == [
            ToolCall(id="mock-scroll", name="scroll", arguments={"direction": "full"}),
            ToolCall(id="mock-click-r1", name="click_ref", arguments={"ref": "r1"}),
            ToolCall(id="mock-submit-r3", name="submit_form", arguments={"ref": "r3"}),
            ToolCall(id="mock-give-up", name="give_up", arguments={"reason": "mock_done"}),
        ]
        assert clicked_keys == {"button|open settings|"}

    asyncio.run(run())


class FakeLocator:
    def __init__(self, page, ref: str):
        self.page = page
        self.ref = ref

    async def click(self, **kwargs):
        self.page.actions.append(("click", self.ref))
        if self.ref == "r1":
            self.page.url = "https://example.com/settings"

    async def fill(self, text, **kwargs):
        self.page.actions.append(("fill", self.ref, text))

    async def inner_text(self, **kwargs):
        return f"text:{self.ref}"


class FakePage:
    def __init__(self):
        self.url = "https://example.com/dashboard"
        self.actions = []

    async def evaluate(self, script, arg=None):
        if "window.__aiAsmAgentRoutes" in script:
            if "window.__aiAsmAgentRoutes || []" in script:
                return ["https://example.com/settings"]
            return None
        if "document.querySelectorAll" in script:
            return {
                "url": self.url,
                "refs": [
                    {
                        "ref": "r1",
                        "tag": "button",
                        "text": "Open settings",
                    },
                    {
                        "ref": "r2",
                        "tag": "button",
                        "text": "Logout",
                    },
                    {
                        "ref": "r3",
                        "tag": "form",
                        "text": "Feedback",
                        "form_method": "POST",
                        "input_types": ["text"],
                        "input_fields": [
                            {"type": "email", "name": "email"},
                        ],
                    },
                ],
            }
        if "data-ai-asm-ref" in script:
            self.actions.append(("submit_form", arg))
            return {"ok": True}
        return None

    def locator(self, selector):
        ref = selector.split('"')[1]
        return FakeLocator(self, ref)


class NetworkRecordingLocator(FakeLocator):
    async def click(self, **kwargs):
        self.page.network_events.record(
            method="POST",
            url="https://example.com/api/login",
        )
        await super().click(**kwargs)


class NetworkRecordingPage(FakePage):
    def __init__(self, network_events):
        super().__init__()
        self.network_events = network_events

    def locator(self, selector):
        ref = selector.split('"')[1]
        return NetworkRecordingLocator(self, ref)


class ButtonOnlyNetworkPage(NetworkRecordingPage):
    async def evaluate(self, script, arg=None):
        if "window.__aiAsmAgentRoutes" in script:
            if "window.__aiAsmAgentRoutes || []" in script:
                return []
            return None
        if "document.querySelectorAll" in script:
            return {
                "url": self.url,
                "refs": [
                    {
                        "ref": "r1",
                        "tag": "a",
                        "text": "Open settings",
                        "href": "/settings",
                    },
                ],
            }
        if "__aiAsmRequests" in script:
            return []
        return None


class EmptyPage(FakePage):
    async def evaluate(self, script, arg=None):
        if "window.__aiAsmAgentRoutes" in script:
            if "window.__aiAsmAgentRoutes || []" in script:
                return []
            return None
        if "document.querySelectorAll" in script:
            return {"url": self.url, "refs": []}
        if "__aiAsmRequests" in script:
            return []
        return None


class AmbiguousPage(FakePage):
    async def evaluate(self, script, arg=None):
        if "window.__aiAsmAgentRoutes" in script:
            if "window.__aiAsmAgentRoutes || []" in script:
                return []
            return None
        if "document.querySelectorAll" in script:
            return {
                "url": self.url,
                "refs": [
                    {
                        "ref": "r1",
                        "tag": "div",
                        "role": "button",
                        "text": "",
                    },
                ],
            }
        if "__aiAsmRequests" in script:
            return []
        return None


class RedirectingFormPage(FakePage):
    async def evaluate(self, script, arg=None):
        if isinstance(arg, dict) and "data-ai-asm-ref" in script:
            self.actions.append(("submit_form", arg))
            return {"ok": False, "error": "redirected form response"}
        return await super().evaluate(script, arg)


class FakeRequest:
    def __init__(
        self,
        *,
        method: str,
        url: str,
        resource_type: str = "fetch",
        post_data: str = "",
    ):
        self.method = method
        self.url = url
        self.resource_type = resource_type
        self.post_data = post_data
        self.headers = {"content-type": "application/json"}


class FakeRoute:
    def __init__(self, request: FakeRequest):
        self.request = request
        self.events: list[str] = []

    async def abort(self):
        self.events.append("abort")

    async def fallback(self):
        self.events.append("fallback")

    async def continue_(self):
        self.events.append("continue")


class DryRunRoutePage(FakePage):
    def __init__(self):
        super().__init__()
        self.route_handler = None
        self.route_calls: list[tuple[str, str]] = []

    async def route(self, pattern, handler):
        self.route_calls.append(("route", pattern))
        self.route_handler = handler

    async def unroute(self, pattern, handler):
        self.route_calls.append(("unroute", pattern))
        assert handler is self.route_handler
        self.route_handler = None

    async def wait_for_timeout(self, timeout):
        self.route_calls.append(("wait", str(timeout)))

    async def dispatch(self, request: FakeRequest) -> list[str]:
        assert self.route_handler is not None
        route = FakeRoute(request)
        await self.route_handler(route)
        return route.events


class FileInputLocator(FakeLocator):
    async def evaluate(self, script):
        self.page.actions.append(("evaluate-file-inputs", self.ref))
        return ["file-token"]

    async def set_input_files(self, files):
        self.page.actions.append(("set_input_files", self.ref, files))


class FileInputPage(FakePage):
    def locator(self, selector):
        if "data-ai-asm-file-input" in selector:
            return FileInputLocator(self, "file-token")
        ref = selector.split('"')[1]
        return FileInputLocator(self, ref)


def test_playwright_tool_page_aborts_mutations_and_falls_back_for_gets():
    async def run():
        page = DryRunRoutePage()
        adapter = PlaywrightToolPage(page, {"refs": []})

        async def action():
            post_events = await page.dispatch(FakeRequest(
                method="POST",
                url="https://example.com/api/save",
                post_data='{"ok":true}',
            ))
            get_events = await page.dispatch(FakeRequest(
                method="GET",
                url="https://example.com/app.js",
                resource_type="script",
            ))
            assert post_events == ["abort"]
            assert get_events == ["fallback"]

        captured = await adapter._capture_and_abort_mutations(action)

        assert captured == [
            {
                "method": "POST",
                "url": "https://example.com/api/save",
                "resource_type": "fetch",
                "content_type": "application/json",
                "post_data_length": 11,
                "aborted": True,
            }
        ]
        assert adapter.last_aborted_mutations == captured
        assert page.route_calls == [
            ("route", "**/*"),
            ("wait", "1000"),
            ("unroute", "**/*"),
        ]

    asyncio.run(run())


def test_playwright_tool_page_attaches_placeholder_file_inputs():
    async def run():
        page = FileInputPage()
        adapter = PlaywrightToolPage(page, {"refs": []})

        await adapter._attach_placeholder_files_for_ref("submit")

        assert page.actions[0] == ("evaluate-file-inputs", "submit")
        action, ref, files = page.actions[1]
        assert action == "set_input_files"
        assert ref == "file-token"
        assert files[0]["name"] == "ai-asm-placeholder.txt"
        assert files[0]["mimeType"] == "text/plain"
        assert files[0]["buffer"] == b"ai-asm dry-run placeholder file"

    asyncio.run(run())


def test_mock_agent_driver_runs_snapshot_to_tool_executor_flow():
    async def run():
        page = FakePage()

        stats = await run_mock_agent_interactions(
            page,
            scope=Scope(ScopeConfig(include_domains=["example.com"])),
            clicked_keys=set(),
            form_data=FormDataSet(fields={"email": "flow@example.com"}),
        )

        assert page.actions == [
            ("click", "r1"),
            ("submit_form", {"ref": "r3", "values": {"email": "flow@example.com"}}),
        ]
        assert stats.buttons_clicked == 1
        assert stats.forms.submitted == 1
        assert stats.discovered_urls == ["https://example.com/settings"]

    asyncio.run(run())


def test_agent_driver_logs_llm_failures(monkeypatch):
    class FailingOpenAIClient:
        def __init__(self, **kwargs):
            pass

        async def complete(self, context):
            raise RuntimeError("api down")

    async def run():
        monkeypatch.setattr("ai_asm.agent.driver.OpenAIClient", FailingOpenAIClient)
        trace = DecisionTrace(scan_id=1)

        stats = await run_agent_interactions(
            AmbiguousPage(),
            scope=Scope(ScopeConfig(include_domains=["example.com"])),
            mode="llm",
            trace=trace,
        )

        assert stats.buttons_clicked == 0
        events = await trace.events()
        assert events[0].kind == "llm_failure"
        assert events[0].payload == {"error": "RuntimeError"}

    asyncio.run(run())


def test_agent_driver_records_llm_failure_flagged_item(monkeypatch, tmp_path):
    class FailingOpenAIClient:
        def __init__(self, **kwargs):
            pass

        async def complete(self, context):
            raise RuntimeError("api down")

    async def run():
        monkeypatch.setattr("ai_asm.agent.driver.OpenAIClient", FailingOpenAIClient)
        engine = open_db(tmp_path / "scan.db")
        with Session(engine) as session:
            scan = Scan(target="https://example.com/")
            session.add(scan)
            session.commit()
            session.refresh(scan)
            scan_id = scan.id

        await run_agent_interactions(
            AmbiguousPage(),
            scope=Scope(ScopeConfig(include_domains=["example.com"])),
            mode="llm",
            db_engine=engine,
            scan_id=scan_id,
            auth_state_path="auth.json",
        )

        with Session(engine) as session:
            rows = session.exec(select(FlaggedItem)).all()
        assert rows
        assert rows[0].flag_kind == "agent_llm_failed"
        assert rows[0].item_kind == "llm_call"
        assert rows[0].auth_state_path == "auth.json"

    asyncio.run(run())


def test_agent_driver_records_redirected_form_as_failed_tool():
    async def run():
        trace = DecisionTrace(scan_id=1)

        stats = await run_mock_agent_interactions(
            RedirectingFormPage(),
            scope=Scope(ScopeConfig(include_domains=["example.com"])),
            clicked_keys={"button|open settings|"},
            form_data=FormDataSet(fields={"email": "flow@example.com"}),
            trace=trace,
        )

        assert stats.forms.submitted == 0
        assert stats.forms.skipped_danger == 1
        events = await trace.events()
        form_events = [
            event for event in events
            if event.kind == "tool_call" and event.payload["tool"] == "submit_form"
        ]
        assert form_events[0].payload["ok"] is False
        assert "redirected form response" in form_events[0].payload["error"]

    asyncio.run(run())


def test_agent_driver_uses_cdp_network_buffer_for_planner_action_delta():
    async def run():
        network_events = NetworkEventBuffer()
        trace = DecisionTrace(scan_id=1)

        await run_agent_interactions(
            ButtonOnlyNetworkPage(network_events),
            scope=Scope(ScopeConfig(include_domains=["example.com"])),
            mode="planner",
            max_steps=2,
            network_events=network_events,
            trace=trace,
        )

        events = await trace.events()
        records = [
            event.payload
            for event in events
            if event.kind == "action_record"
        ]
        assert records[0]["network_delta"]["new_requests_count"] == 1
        assert records[0]["network_delta"]["api_new_requests_count"] == 1
        assert records[0]["network_delta"]["new_requests"] == [
            "POST https://example.com/api/login",
        ]
        assert records[0]["network_delta"]["api_new_requests"] == [
            "POST https://example.com/api/login",
        ]

    asyncio.run(run())


def test_visible_forms_context_groups_field_refs_and_test_values():
    snapshot = {
        "refs": [
            {
                "ref": "f1",
                "tag": "form",
                "text": "Login",
                "form_method": "POST",
                "form_action": "https://example.com/login",
            },
            {
                "ref": "e1",
                "tag": "input",
                "name": "email",
                "text": "Email",
                "input_types": ["email"],
                "form_method": "POST",
                "form_action": "https://example.com/login",
            },
            {
                "ref": "p1",
                "tag": "input",
                "name": "password",
                "text": "Password",
                "input_types": ["password"],
                "form_method": "POST",
                "form_action": "https://example.com/login",
            },
            {
                "ref": "s1",
                "tag": "button",
                "text": "Login",
                "form_method": "POST",
                "form_action": "https://example.com/login",
            },
        ],
    }

    forms = _visible_forms_from_snapshot(
        snapshot,
        FormDataSet(fields={"email": "user@example.com", "password": "secret"}),
    )

    assert forms == [
        {
            "form_ref": "f1",
            "label": "Login",
            "method": "POST",
            "action": "https://example.com/login",
            "submit_text": None,
            "fields": [
                {
                    "ref": "e1",
                    "tag": "input",
                    "type": "email",
                    "name": "email",
                    "placeholder": "Email",
                    "test_value": "user@example.com",
                },
                {
                    "ref": "p1",
                    "tag": "input",
                    "type": "password",
                    "name": "password",
                    "placeholder": "Password",
                    "test_value": "secret",
                },
            ],
            "submit_candidates": [{"ref": "s1", "label": "Login"}],
            "kind": "login",
            "field_count": 2,
            "memory_key": "login:POST:https://example.com/login",
        },
    ]


def test_visible_forms_context_groups_loose_login_fields():
    snapshot = {
        "url": "https://example.com/#/login",
        "refs": [
            {
                "ref": "e1",
                "tag": "input",
                "type": "email",
                "name": "email",
                "text": "Text field for the login email",
            },
            {
                "ref": "p1",
                "tag": "input",
                "type": "password",
                "name": "password",
                "text": "Text field for the login password",
            },
            {
                "ref": "g1",
                "tag": "button",
                "text": "Login with Google",
            },
        ],
    }

    forms = _visible_forms_from_snapshot(
        snapshot,
        FormDataSet(fields={"email": "user@example.com", "password": "secret"}),
    )

    assert forms == [
        {
            "form_ref": None,
            "label": "",
            "method": None,
            "action": "https://example.com/#/login",
            "submit_text": "",
            "fields": [
                {
                    "ref": "e1",
                    "tag": "input",
                    "type": "email",
                    "name": "email",
                    "placeholder": "Text field for the login email",
                    "test_value": "user@example.com",
                },
                {
                    "ref": "p1",
                    "tag": "input",
                    "type": "password",
                    "name": "password",
                    "placeholder": "Text field for the login password",
                    "test_value": "secret",
                },
            ],
            "submit_candidates": [],
            "kind": "login",
            "field_count": 2,
            "memory_key": "login::https://example.com/#/login",
        },
    ]


def test_visible_forms_prefers_field_own_type_over_form_input_types():
    snapshot = {
        "url": "https://example.com/login",
        "refs": [
            {
                "ref": "p1",
                "tag": "input",
                "type": "password",
                "name": "password",
                "text": "Password",
                "input_types": ["text", "password"],
            },
        ],
    }

    forms = _visible_forms_from_snapshot(snapshot, FormDataSet())

    assert forms[0]["fields"][0]["type"] == "password"


def test_visible_forms_uses_select_options_without_type_ref_fill_value():
    snapshot = {
        "url": "https://example.com/search",
        "refs": [
            {
                "ref": "s1",
                "tag": "select",
                "type": "select",
                "name": "category",
                "text": "Category",
                "form_method": "GET",
                "form_action": "https://example.com/search",
                "options": [
                    {"value": "", "label": "Choose"},
                    {"value": "title", "label": "Title"},
                ],
            },
            {
                "ref": "q1",
                "tag": "input",
                "type": "text",
                "name": "q",
                "text": "Search",
                "form_method": "GET",
                "form_action": "https://example.com/search",
            },
        ],
    }

    forms = _visible_forms_from_snapshot(snapshot, FormDataSet())

    assert forms[0]["fields"][0] == {
        "ref": "s1",
        "tag": "select",
        "type": "select",
        "name": "category",
        "placeholder": "Category",
        "test_value": "title",
        "options": [
            {"value": "", "label": "Choose"},
            {"value": "title", "label": "Title"},
        ],
    }


def test_exploration_status_marks_empty_state_for_give_up():
    assert _exploration_status({"refs": []}, []) == {
        "click_ref_count": 0,
        "type_ref_count": 0,
        "visible_form_count": 0,
        "should_give_up": True,
    }


def test_exploration_status_counts_available_actions():
    status = _exploration_status(
        {
            "refs": [
                {"ref": "r1", "tag": "button", "text": "Open"},
                {"ref": "r2", "tag": "input", "type": "text", "name": "q"},
            ],
        },
        [{"kind": "search"}],
    )

    assert status == {
        "click_ref_count": 1,
        "type_ref_count": 1,
        "visible_form_count": 1,
        "should_give_up": False,
    }


def test_submit_candidate_filters_auxiliary_auth_buttons():
    snapshot = {
        "url": "https://example.com/signup",
        "refs": [
            {
                "ref": "r1",
                "tag": "form",
                "form_method": "GET",
                "form_action": "https://example.com/signup",
            },
            {
                "ref": "r2",
                "tag": "input",
                "type": "text",
                "text": "Email",
                "form_method": "GET",
                "form_action": "https://example.com/signup",
            },
            {
                "ref": "r3",
                "tag": "button",
                "text": "Already have an Account? Login",
                "form_method": "GET",
                "form_action": "https://example.com/signup",
            },
            {
                "ref": "r4",
                "tag": "button",
                "text": "Signup",
                "form_method": "GET",
                "form_action": "https://example.com/signup",
            },
        ],
    }

    forms = _visible_forms_from_snapshot(snapshot, FormDataSet())

    assert forms[0]["submit_candidates"] == [{"ref": "r4", "label": "Signup"}]
    assert forms[0]["memory_key"] == "register:GET:https://example.com/signup"


def test_form_status_separates_partial_and_ready_to_submit_forms():
    forms = [
        {
            "memory_key": "login:POST:https://example.com/login",
            "kind": "login",
            "label": "Login",
            "method": "POST",
            "form_ref": "f1",
            "fields": [
                {
                    "ref": "p1",
                    "type": "password",
                    "name": "password",
                    "test_value": "secret",
                },
            ],
            "submit_candidates": [{"ref": "s1", "label": "Login"}],
        },
        {
            "memory_key": "search:GET:https://example.com/search",
            "kind": "search",
            "label": "Search",
            "method": "GET",
            "form_ref": "f2",
            "fields": [],
            "submit_candidates": [{"ref": "s2", "label": "Search"}],
        },
    ]
    memory = AgentMemory()
    memory.remember_typed_form("login:POST:https://example.com/login")
    memory.remember_typed_form("search:GET:https://example.com/search")

    status = _form_status(forms, memory)

    assert status["partially_filled"][0]["memory_key"] == (
        "login:POST:https://example.com/login"
    )
    assert status["partially_filled"][0]["remaining_fields"] == [
        {
            "ref": "p1",
            "type": "password",
            "name": "password",
            "test_value": "secret",
        },
    ]
    assert status["ready_to_submit"][0]["memory_key"] == (
        "search:GET:https://example.com/search"
    )


def test_form_status_ignores_attempted_forms():
    forms = [
        {
            "memory_key": "login:POST:https://example.com/login",
            "kind": "login",
            "label": "Login",
            "method": "POST",
            "form_ref": "f1",
            "fields": [],
            "submit_candidates": [{"ref": "s1", "label": "Login"}],
        },
    ]
    memory = AgentMemory()
    memory.remember_typed_form("login:POST:https://example.com/login")
    memory.remember_form_attempt("login:POST:https://example.com/login")

    assert _form_status(forms, memory) == {
        "partially_filled": [],
        "ready_to_submit": [],
    }


def test_form_memory_key_uses_page_url_for_loose_spa_fields():
    assert _form_memory_key(
        {
            "tag": "input",
            "type": "email",
            "text": "Email Login",
        },
        page_url="https://example.com/#/login",
    ) == "login::https://example.com/#/login"


def test_meaningful_progress_ignores_revisited_spa_state_without_new_requests():
    memory = AgentMemory()
    memory.remember_state("https://example.com/#/login", "sig-login")
    call = ToolCall(id="c1", name="click_ref", arguments={"ref": "r1"})
    result = ToolResult(call_id="c1", tool="click_ref", ok=True)
    delta = NetworkDelta(
        before_url="https://example.com/#/about",
        after_url="https://example.com/#/login",
        before_dom_signature="sig-about",
        after_dom_signature="sig-login",
    )
    after = AgentObservation(
        url="https://example.com/#/login",
        dom_signature="sig-login",
        requests=[],
    )

    assert _made_meaningful_progress(call, result, delta, after, memory) is False


def test_meaningful_progress_accepts_new_requests_even_on_revisited_state():
    memory = AgentMemory()
    memory.remember_state("https://example.com/#/login", "sig-login")
    call = ToolCall(id="c1", name="click_ref", arguments={"ref": "r1"})
    result = ToolResult(call_id="c1", tool="click_ref", ok=True)
    delta = NetworkDelta(
        new_requests_count=1,
        new_requests=("POST https://example.com/rest/user/login",),
        api_new_requests_count=1,
        api_new_requests=("POST https://example.com/rest/user/login",),
        after_url="https://example.com/#/login",
        after_dom_signature="sig-login",
    )
    after = AgentObservation(
        url="https://example.com/#/login",
        dom_signature="sig-login",
        requests=[],
    )

    assert _made_meaningful_progress(call, result, delta, after, memory) is True


def test_meaningful_progress_ignores_asset_only_requests_on_revisited_state():
    memory = AgentMemory()
    memory.remember_state("https://example.com/#/login", "sig-login")
    call = ToolCall(id="c1", name="click_ref", arguments={"ref": "r1"})
    result = ToolResult(call_id="c1", tool="click_ref", ok=True)
    delta = NetworkDelta(
        new_requests_count=1,
        new_requests=("GET https://example.com/assets/app.js",),
        api_new_requests_count=0,
        api_new_requests=(),
        after_url="https://example.com/#/login",
        after_dom_signature="sig-login",
    )
    after = AgentObservation(
        url="https://example.com/#/login",
        dom_signature="sig-login",
        requests=[],
    )

    assert _made_meaningful_progress(call, result, delta, after, memory) is False


def test_network_delta_counts_repeated_same_url_requests_by_timestamp():
    before = AgentObservation(
        url="https://example.com/",
        dom_signature="sig",
        requests=[
            {"method": "GET", "url": "https://example.com/api/poll", "ts": 100},
        ],
    )
    after = AgentObservation(
        url="https://example.com/",
        dom_signature="sig",
        requests=[
            {"method": "GET", "url": "https://example.com/api/poll", "ts": 100},
            {"method": "GET", "url": "https://example.com/api/poll", "ts": 200},
        ],
    )

    delta = _network_delta(before, after)

    assert delta.new_requests_count == 1
    assert delta.new_requests == ("GET https://example.com/api/poll",)
    assert delta.api_new_requests_count == 1
    assert delta.api_new_requests == ("GET https://example.com/api/poll",)


def test_network_delta_falls_back_to_append_count_without_timestamps():
    before = AgentObservation(
        url="https://example.com/",
        dom_signature="sig",
        requests=[
            {"method": "POST", "url": "https://example.com/api/login"},
        ],
    )
    after = AgentObservation(
        url="https://example.com/",
        dom_signature="sig",
        requests=[
            {"method": "POST", "url": "https://example.com/api/login"},
            {"method": "POST", "url": "https://example.com/api/login"},
        ],
    )

    delta = _network_delta(before, after)

    assert delta.new_requests_count == 1
    assert delta.new_requests == ("POST https://example.com/api/login",)
    assert delta.api_new_requests_count == 1
    assert delta.api_new_requests == ("POST https://example.com/api/login",)


def test_network_delta_separates_api_requests_from_static_assets():
    before = AgentObservation(
        url="https://example.com/",
        dom_signature="sig",
        requests=[],
    )
    after = AgentObservation(
        url="https://example.com/",
        dom_signature="sig",
        requests=[
            {
                "method": "GET",
                "url": "https://example.com/assets/app.js",
                "resource_type": "Script",
            },
            {
                "method": "GET",
                "url": "https://example.com/assets/font.woff2",
                "resource_type": "Font",
            },
            {
                "method": "GET",
                "url": "https://example.com/rest/products/search?q=",
                "resource_type": "XHR",
            },
            {
                "method": "POST",
                "url": "https://example.com/login",
                "resource_type": "Document",
            },
        ],
    )

    delta = _network_delta(before, after)

    assert delta.new_requests_count == 4
    assert delta.api_new_requests_count == 2
    assert delta.api_new_requests == (
        "GET https://example.com/rest/products/search?q=",
        "POST https://example.com/login",
    )
