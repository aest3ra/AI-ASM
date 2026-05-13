"""Browser-agent driver backed by planner, mock, or LLM tool clients."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlmodel import Session

from orbis.agent.budget import BudgetTracker
from orbis.agent.client import HeuristicMockLLMClient, MockLLMClient, OpenAIClient
from orbis.agent.context import (
    ActionRecord,
    AgentMemory,
    NetworkDelta,
    request_key,
)
from orbis.agent.form_data import FormDataSet
from orbis.agent.loop import AgentLoop
from orbis.agent.network import NetworkEventBuffer
from orbis.agent.planner import plan_local_actions
from orbis.agent.safety import (
    classify_form_text,
    is_click_candidate,
    label_for_ref,
    matches_danger,
    safe_tool_arguments,
)
from orbis.agent.snapshot import capture_agent_snapshot, compute_dom_signature
from orbis.agent.tools import ToolCall, ToolExecutor, ToolResult
from orbis.config import DEFAULT_AGENT_MODEL
from orbis.crawler.scope import Scope
from orbis.crawler.types import FormStats, InteractionStats
from orbis.normalizer.url import templatize_path
from orbis.shared.decision_trace import DecisionTrace
from orbis.storage.repo import record_flagged_item

ROUTE_RECORDER_SCRIPT = """
() => {
    if (window.__aiAsmAgentRouteRecorderInstalled) return;
    window.__aiAsmAgentRouteRecorderInstalled = true;
    window.__aiAsmAgentRoutes = window.__aiAsmAgentRoutes || [];
    const record = () => window.__aiAsmAgentRoutes.push(window.location.href);
    const push = history.pushState;
    const replace = history.replaceState;
    history.pushState = function(...args) {
        const ret = push.apply(this, args);
        record();
        return ret;
    };
    history.replaceState = function(...args) {
        const ret = replace.apply(this, args);
        record();
        return ret;
    };
    window.addEventListener("popstate", record);
    window.addEventListener("hashchange", record);
}
"""

SCROLL_SCRIPT = """
async () => {
    const step = 600;
    const total = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
    );
    for (let y = 0; y < total; y += step) {
        window.scrollTo(0, y);
        await new Promise(r => setTimeout(r, 80));
    }
    window.scrollTo(0, 0);
}
"""

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SUBMIT_FORM_SCRIPT = r"""
async (payload) => {
  const ref = payload.ref;
  const providedValues = payload.values || {};
  const form = document.querySelector(`[data-orbis-ref="${ref}"]`);
  if (!form || form.tagName.toLowerCase() !== "form") {
    return { ok: false, error: "form not found" };
  }
  const method = (form.method || "GET").toUpperCase();
  if (method !== "POST") return { ok: false, error: "non-post form" };

  function dummyFor(field) {
    if (field.name && providedValues[field.name] !== undefined) {
      return String(providedValues[field.name]);
    }
    const type = (field.type || "text").toLowerCase();
    const hint = (field.name + " " + (field.placeholder || "") + " " + (field.id || "")).toLowerCase();
    if (type === "email" || hint.includes("email")) return "test@example.com";
    if (type === "tel" || hint.includes("phone") || hint.includes("tel")) return "5551234567";
    if (type === "url" || hint.includes("url") || hint.includes("website")) return "https://example.com";
    if (type === "number") return "1";
    if (type === "date") return "2025-01-01";
    if (type === "time") return "12:00";
    if (type === "datetime-local") return "2025-01-01T12:00";
    return "test";
  }

  const enctype = (form.enctype || "application/x-www-form-urlencoded").toLowerCase();
  if (enctype.includes("multipart")) return { ok: false, error: "multipart form" };

  const fields = form.querySelectorAll("input, textarea, select");
  const values = [];
  for (const f of fields) {
    if (!f.name) continue;
    const t = (f.type || "text").toLowerCase();
    if (["submit", "button", "reset", "image", "file"].includes(t)) continue;
    if (t === "hidden") { values.push([f.name, f.value]); continue; }
    if (t === "checkbox") {
      if (f.checked) values.push([f.name, f.value || "on"]);
      continue;
    }
    if (t === "radio") {
      if (f.checked) values.push([f.name, f.value]);
      continue;
    }
    if (f.tagName === "SELECT") {
      const opt = f.options[f.selectedIndex] || f.options[0];
      if (opt) values.push([f.name, opt.value]);
      continue;
    }
    values.push([f.name, dummyFor(f)]);
  }

  let body, headers = {};
  if (enctype.includes("json")) {
    const obj = {};
    for (const [k, v] of values) obj[k] = v;
    body = JSON.stringify(obj);
    headers["Content-Type"] = "application/json";
  } else {
    const usp = new URLSearchParams();
    for (const [k, v] of values) usp.append(k, v);
    body = usp.toString();
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  }

  const response = await fetch(form.action || window.location.href, {
    method: "POST",
    body,
    headers,
    credentials: "same-origin",
    redirect: "manual",
  });
  const location = response.headers.get("Location") || response.headers.get("location") || "";
  if (
    response.redirected ||
    response.type === "opaqueredirect" ||
    (response.status >= 300 && response.status < 400)
  ) {
    return {
      ok: false,
      error: "redirected form response",
      status: response.status,
      redirected: response.redirected,
      location,
      url: response.url || "",
    };
  }
  return { ok: true, status: response.status, url: response.url || "" };
}
"""


async def run_agent_interactions(
    page,
    *,
    scope: Scope,
    clicked_keys: set[str] | None = None,
    mode: str = "planner",
    model: str | None = None,
    temperature: float = 0.0,
    max_clicks: int = 12,
    max_steps: int | None = None,
    form_data: FormDataSet | None = None,
    trace: DecisionTrace | None = None,
    db_engine=None,
    scan_id: int | None = None,
    auth_state_path: str | None = None,
    attempted_form_keys: set[str] | None = None,
    network_events: NetworkEventBuffer | None = None,
) -> InteractionStats:
    await _install_route_recorder(page)
    forms = form_data or FormDataSet()
    initial_snapshot = await capture_agent_snapshot(page)
    adapter = PlaywrightToolPage(page, initial_snapshot, form_data=forms)
    memory = AgentMemory(
        attempted_form_keys=attempted_form_keys
        if attempted_form_keys is not None
        else set()
    )
    memory.remember_requests(
        network_events.snapshot()
        if network_events is not None
        else await _request_records(page)
    )
    force_llm_next = False
    planner_no_progress_count = 0
    llm_no_progress_count = 0

    if mode == "llm":
        client = OpenAIClient(
            model=model or DEFAULT_AGENT_MODEL,
            temperature=temperature,
        )
        local_planner = None
    elif mode == "hybrid":
        client = OpenAIClient(
            model=model or DEFAULT_AGENT_MODEL,
            temperature=temperature,
        )

        def local_planner(context: dict[str, Any]) -> list[ToolCall]:
            nonlocal force_llm_next
            if force_llm_next:
                force_llm_next = False
                return []
            calls = plan_local_actions(context)
            if len(calls) == 1 and calls[0].name == "give_up":
                return []
            return calls
    elif mode == "mock":
        client = HeuristicMockLLMClient(
            max_clicks=max_clicks,
            clicked_keys=clicked_keys,
            allow_password_forms=form_data is not None,
        )
        local_planner = None
    else:
        client = MockLLMClient()
        local_planner = plan_local_actions
    budget = BudgetTracker(max_steps=max_steps or max_clicks + 32)
    executor = ToolExecutor(
        page=adapter,
        scope=scope,
        budget=budget,
        trace=trace,
        db_engine=db_engine,
        scan_id=scan_id,
        page_url=getattr(page, "url", None),
        auth_state_path=auth_state_path,
    )
    max_turns = max_steps or max_clicks + 32
    before_observation: AgentObservation | None = None
    before_cursor = 0
    before_seen_request_keys: set[tuple[str, str, str, str]] = set()
    no_progress_count = 0

    async def context_factory() -> dict[str, Any]:
        nonlocal before_observation
        snapshot = await capture_agent_snapshot(page)
        filtered_snapshot = memory.filter_snapshot(snapshot)
        adapter.update_snapshot(snapshot)
        before_observation = await observe_agent_state(
            page,
            network_events=network_events,
        )
        memory.remember_state(
            before_observation.url,
            before_observation.dom_signature,
        )
        visible_forms = _visible_forms_from_snapshot(filtered_snapshot, forms)
        return {
            "page_url": getattr(page, "url", None),
            "snapshot": filtered_snapshot,
            "visible_forms": visible_forms,
            "form_status": _form_status(visible_forms, memory),
            "exploration_status": _exploration_status(
                filtered_snapshot,
                visible_forms,
            ),
            "form_test_data": forms.summary(),
            "current_state": before_observation.as_context(),
            "goals": _default_goals(auth_state_path=auth_state_path),
            "memory": memory.summary(),
        }

    async def before_action(turn: int, call: ToolCall) -> None:
        nonlocal before_observation, before_cursor, before_seen_request_keys
        if network_events is not None:
            records = network_events.snapshot()
            before_cursor = len(records)
            before_seen_request_keys = {
                endpoint_surface_key(record) for record in records
            }
            return
        before_observation = await observe_agent_state(page)
        before_cursor = len(before_observation.requests)
        before_seen_request_keys = {
            endpoint_surface_key(record) for record in before_observation.requests
        }

    async def after_action(
        turn: int,
        call: ToolCall,
        result: ToolResult,
        source: str,
    ) -> bool:
        nonlocal before_observation, no_progress_count
        nonlocal force_llm_next, planner_no_progress_count, llm_no_progress_count
        if network_events is not None:
            await network_events.wait_after(
                before_cursor,
                timeout_ms=_network_wait_timeout_ms(call.name),
            )
        else:
            await asyncio.sleep(0.25)
        after = await observe_agent_state(
            page,
            network_events=network_events,
        )
        before = before_observation or after
        ref_info = await adapter.describe_ref(str(call.arguments.get("ref") or ""))
        new_records = (
            network_events.since(before_cursor)
            if network_events is not None
            else None
        )
        delta = _network_delta(
            before,
            after,
            new_records=new_records,
            seen_request_keys=before_seen_request_keys,
            scope=scope,
        )
        record = ActionRecord(
            turn=turn,
            tool=call.name,
            arguments=_safe_action_arguments(call),
            ok=result.ok,
            error=result.error,
            ref_label=label_for_ref(ref_info) if ref_info else None,
            network_delta=delta,
        )
        memory.remember_requests(after.requests)
        memory.remember_action(record, ref_info)
        if call.name in {"type_ref", "select_ref"} and result.ok:
            memory.remember_typed_form(_form_memory_key(
                ref_info,
                page_url=before.url or after.url,
            ))
        if _is_form_attempt(call, ref_info):
            memory.remember_form_attempt(_form_memory_key(
                ref_info,
                page_url=before.url or after.url,
            ))
        if trace is not None:
            await trace.log_action_record(
                page_url=getattr(page, "url", None),
                payload=record.as_dict(),
            )
            await trace.log_state_checkpoint(
                page_url=getattr(page, "url", None),
                payload={
                    "turn": turn,
                    "url": after.url,
                    "dom_signature": after.dom_signature,
                    "action_history_len": len(memory.history.items()),
                },
            )
        executor.page_url = getattr(page, "url", None)
        made_progress = _made_meaningful_progress(
            call,
            result,
            delta,
            after,
            memory,
        )
        memory.remember_state(after.url, after.dom_signature)
        no_progress_count = 0 if made_progress else no_progress_count + 1
        if mode == "hybrid":
            if source == "local_planner":
                planner_no_progress_count = (
                    0 if made_progress else planner_no_progress_count + 1
                )
                if planner_no_progress_count >= 2:
                    force_llm_next = True
                    planner_no_progress_count = 0
                    no_progress_count = 0
                    return True
            elif source == "llm":
                llm_no_progress_count = (
                    0 if made_progress else llm_no_progress_count + 1
                )
                if made_progress:
                    planner_no_progress_count = 0
                return llm_no_progress_count < 3
        return no_progress_count < 3

    try:
        loop = AgentLoop(
            client=client,
            executor=executor,
            trace=trace,
            page_url=getattr(page, "url", None),
        )
        await loop.run_page(
            context_factory=context_factory,
            before_action=before_action,
            after_action=after_action,
            local_planner=local_planner,
            max_turns=max_turns,
        )
    except Exception as exc:
        if trace is not None:
            await trace.log_llm_failure(
                page_url=getattr(page, "url", None),
                payload={"error": type(exc).__name__},
            )
        if db_engine is not None and scan_id is not None:
            with Session(db_engine) as session:
                record_flagged_item(
                    session,
                    scan_id=scan_id,
                    flag_kind="agent_llm_failed",
                    item_kind="llm_call",
                    description=f"LLM failed on page: {type(exc).__name__}",
                    page_url=getattr(page, "url", None),
                    context_json={"error": type(exc).__name__},
                    auth_state_path=auth_state_path,
                )
    await adapter.collect_recorded_routes()
    return adapter.stats


async def run_mock_agent_interactions(page, **kwargs) -> InteractionStats:
    return await run_agent_interactions(page, mode="mock", **kwargs)


class PlaywrightToolPage:
    def __init__(
        self,
        page,
        snapshot: dict[str, Any],
        *,
        form_data: FormDataSet | None = None,
    ) -> None:
        self.page = page
        self.snapshot = snapshot
        self.form_data = form_data or FormDataSet()
        self.last_aborted_mutations: list[dict[str, Any]] = []
        self.refs = {
            str(info.get("ref")): info
            for info in snapshot.get("refs", [])
            if isinstance(info, dict) and info.get("ref")
        }
        self.stats = _stats_from_snapshot(snapshot)

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.refs = {
            str(info.get("ref")): info
            for info in snapshot.get("refs", [])
            if isinstance(info, dict) and info.get("ref")
        }

    async def describe_ref(self, ref: str) -> dict[str, Any]:
        return dict(self.refs.get(ref) or {})

    async def navigate(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=20_000)

    async def click_ref(self, ref: str) -> None:
        async def action() -> None:
            before = getattr(self.page, "url", None)
            await self._attach_placeholder_files_for_ref(ref)
            await self.page.locator(_ref_selector(ref)).click(
                timeout=2_000,
                no_wait_after=True,
            )
            after = getattr(self.page, "url", None)
            if after and after != before:
                _add_discovered_url(self.stats, after)

        await self._capture_and_abort_mutations(action)
        self.stats.buttons_clicked += 1

    async def type_ref(self, ref: str, text: str) -> None:
        await self.page.locator(_ref_selector(ref)).fill(text, timeout=2_000)

    async def select_ref(self, ref: str, value: str) -> None:
        await self.page.locator(_ref_selector(ref)).select_option(
            value,
            timeout=2_000,
        )

    async def submit_form(self, ref: str) -> None:
        info = self.refs.get(ref) or {}
        result = None
        error: Exception | None = None

        async def action() -> None:
            nonlocal result
            await self._attach_placeholder_files_for_ref(ref)
            result = await self.page.evaluate(
                SUBMIT_FORM_SCRIPT,
                {
                    "ref": ref,
                    "values": self.form_data.values_for_form(info),
                },
            )

        try:
            await self._capture_and_abort_mutations(action)
        except Exception as exc:
            error = exc
        if self.last_aborted_mutations:
            self.stats.forms.submitted += 1
            return
        if error is not None:
            raise error
        if isinstance(result, dict) and result.get("ok"):
            self.stats.forms.submitted += 1
            return
        self.stats.forms.skipped_danger += 1
        error = "form submit failed"
        if isinstance(result, dict) and result.get("error"):
            error = str(result.get("error"))
        raise RuntimeError(error)

    async def _capture_and_abort_mutations(self, action) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []

        if not hasattr(self.page, "route") or not hasattr(self.page, "unroute"):
            self.last_aborted_mutations = []
            await action()
            return captured

        async def handler(route):
            request = route.request
            method = request.method.upper()
            if method in MUTATING_METHODS:
                captured.append(_mutation_request_summary(request))
                await route.abort()
                return
            # Keep the outer scope route in the chain when Playwright supports it.
            # Older versions lack fallback(), so continue_() is the best effort path.
            if hasattr(route, "fallback"):
                await route.fallback()
                return
            await route.continue_()

        self.last_aborted_mutations = []
        await self.page.route("**/*", handler)
        try:
            await action()
            if hasattr(self.page, "wait_for_timeout"):
                await self.page.wait_for_timeout(1000)
        finally:
            await self.page.unroute("**/*", handler)
        self.last_aborted_mutations = captured
        return captured

    async def _attach_placeholder_files_for_ref(self, ref: str) -> None:
        try:
            tokens = await self.page.locator(_ref_selector(ref)).evaluate(
                """
                (el) => {
                    const form = el.tagName.toLowerCase() === "form"
                        ? el
                        : el.closest("form");
                    if (!form) return [];
                    return Array.from(form.querySelectorAll('input[type="file"]'))
                        .map((input, index) => {
                            const token = `orbis-file-${Date.now()}-${index}`;
                            input.setAttribute("data-orbis-file-input", token);
                            return token;
                        });
                }
                """
            )
        except Exception:
            return
        if not isinstance(tokens, list):
            return
        for token in tokens:
            if not isinstance(token, str) or not token:
                continue
            try:
                await self.page.locator(
                    f'[data-orbis-file-input="{token}"]'
                ).set_input_files([{
                    "name": "orbis-placeholder.txt",
                    "mimeType": "text/plain",
                    "buffer": b"orbis dry-run placeholder file",
                }])
            except Exception:
                continue

    async def scroll(self, direction: str = "down") -> None:
        if direction == "full":
            await self.page.evaluate(SCROLL_SCRIPT)
        else:
            delta = -700 if direction == "up" else 700
            await self.page.evaluate("(dy) => window.scrollBy(0, dy)", delta)

    async def get_text(self, ref: str) -> str:
        return await self.page.locator(_ref_selector(ref)).inner_text(timeout=2_000)

    async def collect_recorded_routes(self) -> None:
        try:
            routes = await self.page.evaluate("() => window.__aiAsmAgentRoutes || []")
        except Exception:
            return
        if not isinstance(routes, list):
            return
        for route in routes:
            _add_discovered_url(self.stats, route)


async def _install_route_recorder(page) -> None:
    try:
        await page.evaluate(ROUTE_RECORDER_SCRIPT)
    except Exception:
        pass


def _stats_from_snapshot(snapshot: dict[str, Any]) -> InteractionStats:
    stats = InteractionStats()
    refs = [
        item for item in snapshot.get("refs", [])
        if isinstance(item, dict)
    ]
    click_items = [item for item in refs if is_click_candidate(item)]
    stats.buttons_seen = len(click_items)
    stats.buttons_skipped_danger = sum(
        1 for item in click_items if matches_danger(item)
    )
    form_stats = FormStats()
    for item in refs:
        if str(item.get("tag") or "").lower() != "form":
            continue
        form_stats.seen += 1
        input_types = {str(value).lower() for value in item.get("input_types") or []}
        if "password" in input_types:
            form_stats.skipped_password += 1
        elif str(item.get("form_method") or "").upper() != "POST":
            form_stats.skipped_get += 1
        elif matches_danger(item):
            form_stats.skipped_danger += 1
    stats.forms = form_stats
    return stats


def _ref_selector(ref: str) -> str:
    return f'[data-orbis-ref="{ref}"]'


def _add_discovered_url(stats: InteractionStats, url: str | None) -> None:
    if not url or url in stats.discovered_urls:
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        return
    stats.discovered_urls.append(url)


@dataclass(frozen=True)
class AgentObservation:
    url: str | None
    dom_signature: str | None
    requests: list[dict[str, Any]]

    def as_context(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "dom_signature": self.dom_signature,
            "observed_request_count": len(self.requests),
        }


async def observe_agent_state(
    page,
    *,
    network_events: NetworkEventBuffer | None = None,
) -> AgentObservation:
    return AgentObservation(
        url=getattr(page, "url", None),
        dom_signature=await compute_dom_signature(page),
        requests=(
            network_events.snapshot()
            if network_events is not None
            else await _request_records(page)
        ),
    )


async def _request_records(page) -> list[dict[str, Any]]:
    try:
        records = await page.evaluate("() => window.__aiAsmRequests || []")
    except Exception:
        return []
    if not isinstance(records, list):
        return []
    return [
        record for record in records
        if isinstance(record, dict) and record.get("url")
    ]


def _network_delta(
    before: AgentObservation,
    after: AgentObservation,
    *,
    new_records: list[dict[str, Any]] | None = None,
    seen_request_keys: set[tuple[str, str, str, str]] | None = None,
    scope: Scope | None = None,
) -> NetworkDelta:
    if new_records is None:
        new_records = _new_request_records(before.requests, after.requests)
    api_records = _scope_request_records(_api_request_records(new_records), scope)
    unique_records = _unique_request_records(
        new_records,
        seen_request_keys if seen_request_keys is not None else {
            endpoint_surface_key(record) for record in before.requests
        },
    )
    unique_api_records = _scope_request_records(
        _api_request_records(unique_records),
        scope,
    )
    return NetworkDelta(
        new_requests_count=len(new_records),
        new_requests=tuple(
            f"{request_key(record)[0]} {request_key(record)[1]}"
            for record in new_records[:12]
        ),
        api_new_requests_count=len(api_records),
        api_new_requests=tuple(
            f"{request_key(record)[0]} {request_key(record)[1]}"
            for record in api_records[:12]
        ),
        unique_new_requests_count=len(unique_records),
        unique_new_requests=tuple(
            f"{request_key(record)[0]} {request_key(record)[1]}"
            for record in unique_records[:12]
        ),
        unique_api_new_requests_count=len(unique_api_records),
        unique_api_new_requests=tuple(
            f"{request_key(record)[0]} {request_key(record)[1]}"
            for record in unique_api_records[:12]
        ),
        before_url=before.url,
        after_url=after.url,
        before_dom_signature=before.dom_signature,
        after_dom_signature=after.dom_signature,
    )


def _unique_request_records(
    records: list[dict[str, Any]],
    seen_keys: set[tuple[str, str, str, str]],
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    local_seen = set(seen_keys)
    for record in records:
        key = endpoint_surface_key(record)
        if key in local_seen:
            continue
        local_seen.add(key)
        unique.append(record)
    return unique


def _scope_request_records(
    records: list[dict[str, Any]],
    scope: Scope | None,
) -> list[dict[str, Any]]:
    if scope is None:
        return records
    return [
        record for record in records
        if _scope_allows_request_url(str(record.get("url") or ""), scope)
    ]


def _scope_allows_request_url(url: str, scope: Scope) -> bool:
    parsed = urlparse(url)
    if parsed.scheme in {"ws", "wss"}:
        http_scheme = "https" if parsed.scheme == "wss" else "http"
        url = urlunparse(parsed._replace(scheme=http_scheme))
    return scope.allows(url)


def endpoint_surface_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    method, url = request_key(record)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = templatize_path(parsed.path or "/")
    query_names = "&".join(sorted(
        name for name in _query_names(parsed.query) if name
    ))
    return method, host, path, query_names


def _query_names(query: str) -> list[str]:
    if not query:
        return []
    names = []
    for part in query.split("&"):
        if not part:
            continue
        names.append(part.split("=", 1)[0])
    return names


def _new_request_records(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return requests appended after the observation point.

    Agent progress must count repeated calls to the same URL. A set-diff on
    `(method, url)` hides polling, retries, form resubmits, and SPA reloads.
    The init script records `ts`; use it first, then fall back to append count
    for synthetic tests or older traces without timestamps.
    """
    if len(after) > len(before):
        return after[len(before):]
    before_ts = [
        ts for record in before
        if (ts := _request_ts(record)) is not None
    ]
    after_ts = [
        ts for record in after
        if (ts := _request_ts(record)) is not None
    ]
    if before_ts and after_ts:
        cutoff = max(before_ts)
        return [
            record for record in after
            if (ts := _request_ts(record)) is not None and ts > cutoff
        ]
    before_keys = {request_key(record) for record in before}
    return [
        record for record in after
        if request_key(record) not in before_keys
    ]


def _request_ts(record: dict[str, Any]) -> float | None:
    value = record.get("ts")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


API_RESOURCE_TYPES = {"Fetch", "XHR", "WebSocket", "EventSource"}
API_PATH_MARKERS = ("/api/", "/api-", "/api_", "/rest/", "/graphql", "/gql")
STATIC_PATH_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".map",
    ".mp3",
    ".mp4",
    ".otf",
    ".png",
    ".svg",
    ".ttf",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
}


def _api_request_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if _is_api_request_record(record)]


def _is_api_request_record(record: dict[str, Any]) -> bool:
    method, url = request_key(record)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        return False
    path = (parsed.path or "").lower()
    if _looks_static_asset_path(path):
        return False
    if any(marker in path for marker in API_PATH_MARKERS):
        return True
    resource_type = str(record.get("resource_type") or "")
    if resource_type in API_RESOURCE_TYPES:
        return True
    return method != "GET"


def _looks_static_asset_path(path: str) -> bool:
    return any(path.endswith(ext) for ext in STATIC_PATH_EXTENSIONS)


def _mutation_request_summary(request) -> dict[str, Any]:
    post_data = request.post_data or ""
    return {
        "method": request.method.upper(),
        "url": request.url,
        "resource_type": request.resource_type,
        "content_type": request.headers.get("content-type"),
        "post_data_length": len(post_data),
        "aborted": True,
    }


def _network_wait_timeout_ms(tool_name: str) -> int:
    if tool_name in {"click_ref", "submit_form", "navigate"}:
        return 1200
    if tool_name == "scroll":
        return 500
    return 150


def _safe_action_arguments(call: ToolCall) -> dict[str, Any]:
    return safe_tool_arguments(call.name, call.arguments)


def _default_goals(*, auth_state_path: str | None) -> list[str]:
    goals = [
        "if a visible login/register/search form has test values, fill fields and submit it before repeating nav clicks",
        "open menus, tabs, dialogs, filters, search controls, and pagination",
        "prefer account/profile/my page/settings/orders/admin areas when visible",
        "use safe forms with provided test data when non-destructive",
        "do not revisit urls or controls listed in memory unless they produced new requests",
        "avoid failed refs listed in memory",
    ]
    if auth_state_path:
        goals.insert(0, "explore authenticated user-only areas")
    return goals


def _made_meaningful_progress(
    call: ToolCall,
    result: ToolResult,
    delta: NetworkDelta,
    after: AgentObservation,
    memory: AgentMemory,
) -> bool:
    if not result.ok:
        return False
    if delta.unique_api_new_requests_count > 0:
        return True
    if call.name == "submit_form":
        return not memory.has_seen_state(after.url, after.dom_signature)
    # Successful field entry is intentionally progress even without network I/O:
    # the follow-up submit/click is what should produce the request delta.
    if call.name in {"type_ref", "select_ref"}:
        return True
    return not memory.has_seen_state(after.url, after.dom_signature)


def _is_form_attempt(call: ToolCall, ref_info: dict[str, Any] | None) -> bool:
    if call.name == "submit_form":
        return True
    if call.name != "click_ref" or not ref_info:
        return False
    return _looks_like_submit_control(ref_info)


def _form_memory_key(
    ref_info: dict[str, Any] | None,
    *,
    page_url: str | None = None,
) -> str:
    if not ref_info:
        return ""
    action = str(ref_info.get("form_action") or page_url or "").strip().lower()
    method = str(ref_info.get("form_method") or "").strip().upper()
    text = " ".join(
        str(ref_info.get(key) or "")
        for key in (
            "text",
            "aria_label",
            "name",
            "submit_text",
            "form_action",
        )
    )
    kind = classify_form_text(text)
    label = " ".join(label_for_ref(ref_info).lower().split())[:80]
    if not action and not label:
        return ""
    return f"{kind}:{method}:{action or label}"


def _visible_forms_from_snapshot(
    snapshot: dict[str, Any],
    form_data: FormDataSet,
) -> list[dict[str, Any]]:
    refs = [
        item for item in snapshot.get("refs", [])
        if isinstance(item, dict)
    ]
    forms: dict[str, dict[str, Any]] = {}
    for item in refs:
        if str(item.get("tag") or "").lower() != "form":
            continue
        key = _form_group_key(item)
        forms[key] = {
            "form_ref": item.get("ref"),
            "label": label_for_ref(item),
            "method": item.get("form_method"),
            "action": item.get("form_action"),
            "submit_text": item.get("submit_text"),
            "fields": [],
            "submit_candidates": [],
        }

    page_group_key = f"PAGE {snapshot.get('url') or ''}"
    for item in refs:
        key = _form_group_key(item)
        tag = str(item.get("tag") or "").lower()
        if not key and tag in {"input", "textarea", "select"}:
            key = page_group_key
        if not key:
            continue
        form = forms.setdefault(key, {
            "form_ref": None,
            "label": "",
            "method": item.get("form_method"),
            "action": item.get("form_action") or snapshot.get("url"),
            "submit_text": "",
            "fields": [],
            "submit_candidates": [],
        })
        if tag in {"input", "textarea", "select"} and _is_form_field(item):
            field = {
                "ref": item.get("ref"),
                "tag": tag,
                "type": _field_type(item),
                "name": item.get("name"),
                "placeholder": item.get("text") or item.get("aria_label"),
                "test_value": _field_test_value(item, form_data),
                "options": item.get("options") if tag == "select" else None,
            }
            form["fields"].append({
                field_key: value
                for field_key, value in field.items()
                if value
            })
        elif is_click_candidate(item) and _looks_like_submit_control(item):
            form["submit_candidates"].append({
                "ref": item.get("ref"),
                "label": label_for_ref(item),
            })

    if page_group_key in forms:
        for item in refs:
            if not is_click_candidate(item) or not _looks_like_submit_control(item):
                continue
            candidate = {
                "ref": item.get("ref"),
                "label": label_for_ref(item),
            }
            if candidate not in forms[page_group_key]["submit_candidates"]:
                forms[page_group_key]["submit_candidates"].append(candidate)

    visible = []
    for form in forms.values():
        if not form["fields"] and not form["form_ref"]:
            continue
        form["kind"] = _classify_form(form)
        form["field_count"] = len(form["fields"])
        form["memory_key"] = _form_memory_key_from_form(form)
        visible.append(form)
    return visible[:8]


def _form_status(
    visible_forms: list[dict[str, Any]],
    memory: AgentMemory,
) -> dict[str, Any]:
    partial: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    for form in visible_forms:
        key = str(form.get("memory_key") or "")
        if not key or key in memory.attempted_form_keys:
            continue
        if key not in memory.typed_form_keys:
            continue
        fields = form.get("fields") if isinstance(form.get("fields"), list) else []
        submit_candidates = (
            form.get("submit_candidates")
            if isinstance(form.get("submit_candidates"), list)
            else []
        )
        item = {
            "memory_key": key,
            "kind": form.get("kind"),
            "label": form.get("label"),
            "method": form.get("method"),
            "form_ref": form.get("form_ref"),
            "submit_candidates": submit_candidates,
        }
        if fields:
            item["remaining_fields"] = fields
            partial.append(item)
        elif submit_candidates or form.get("form_ref"):
            ready.append(item)
    return {
        "partially_filled": partial[:4],
        "ready_to_submit": ready[:4],
    }


def _exploration_status(
    snapshot: dict[str, Any],
    visible_forms: list[dict[str, Any]],
) -> dict[str, Any]:
    refs = [
        item for item in snapshot.get("refs", [])
        if isinstance(item, dict)
    ]
    click_refs = [item for item in refs if is_click_candidate(item)]
    typeable_refs = [item for item in refs if _is_form_field(item)]
    return {
        "click_ref_count": len(click_refs),
        "type_ref_count": len(typeable_refs),
        "visible_form_count": len(visible_forms),
        "should_give_up": not click_refs and not typeable_refs and not visible_forms,
    }


def _form_group_key(item: dict[str, Any]) -> str:
    method = str(item.get("form_method") or "").upper()
    action = str(item.get("form_action") or "")
    if not method and not action:
        return ""
    return f"{method} {action}"


def _field_type(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "").lower()
    if item_type:
        return item_type
    input_types = item.get("input_types")
    if isinstance(input_types, list) and input_types:
        return str(input_types[0]).lower()
    return str(item.get("tag") or "text").lower()


def _is_typeable_field(item: dict[str, Any]) -> bool:
    tag = str(item.get("tag") or "").lower()
    if tag == "textarea":
        return True
    if tag == "select":
        return False
    return _field_type(item) not in {
        "checkbox",
        "radio",
        "hidden",
        "submit",
        "button",
        "reset",
        "image",
        "file",
    }


def _is_form_field(item: dict[str, Any]) -> bool:
    return str(item.get("tag") or "").lower() == "select" or _is_typeable_field(item)


def _field_test_value(item: dict[str, Any], form_data: FormDataSet) -> str:
    if str(item.get("tag") or "").lower() != "select":
        return form_data.value_for_field(item)
    configured = form_data.configured_value_for_field(item)
    if configured is not None:
        return configured
    return _first_select_option_value(item)


def _first_select_option_value(item: dict[str, Any]) -> str:
    options = item.get("options")
    if not isinstance(options, list):
        return ""
    for option in options:
        if not isinstance(option, dict):
            continue
        value = str(option.get("value") or "").strip()
        if value:
            return value
    return ""


def _looks_like_submit_control(item: dict[str, Any]) -> bool:
    label = label_for_ref(item).lower()
    if not label:
        return False
    if any(marker in label for marker in ("forgot", "already have", "forgot password")):
        return False
    if any(marker in label for marker in ("google", "facebook", "github", "oauth", "sso")):
        return False
    return any(
        marker in label
        for marker in (
            "submit",
            "save",
            "search",
            "login",
            "log in",
            "sign in",
            "signup",
            "sign up",
            "register",
            "continue",
            "확인",
            "검색",
            "로그인",
            "가입",
        )
    )


def _classify_form(form: dict[str, Any]) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            form.get("label"),
            form.get("action"),
            form.get("submit_text"),
            " ".join(str(field.get("name") or "") for field in form.get("fields") or []),
            " ".join(str(field.get("placeholder") or "") for field in form.get("fields") or []),
        )
    )
    return classify_form_text(text)


def _form_memory_key_from_form(form: dict[str, Any]) -> str:
    action = str(form.get("action") or "").strip().lower()
    method = str(form.get("method") or "").strip().upper()
    text = " ".join(
        str(form.get(key) or "")
        for key in ("label", "submit_text", "action")
    )
    kind = classify_form_text(text)
    label = " ".join(str(form.get("label") or "").lower().split())[:80]
    if not action and not label:
        return ""
    return f"{kind}:{method}:{action or label}"
