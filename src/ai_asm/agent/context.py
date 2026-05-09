"""Agent memory and sliding context helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ai_asm.agent.safety import interaction_key, is_click_candidate, label_for_ref


@dataclass
class AgentHistory:
    max_items: int = 20
    _items: deque[dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        self._items = deque(maxlen=self.max_items)

    def append(self, item: dict[str, Any]) -> None:
        self._items.append(item)

    def items(self) -> list[dict[str, Any]]:
        return list(self._items)


@dataclass(frozen=True)
class NetworkDelta:
    new_requests_count: int = 0
    new_requests: tuple[str, ...] = ()
    api_new_requests_count: int = 0
    api_new_requests: tuple[str, ...] = ()
    before_url: str | None = None
    after_url: str | None = None
    before_dom_signature: str | None = None
    after_dom_signature: str | None = None

    @property
    def url_changed(self) -> bool:
        return bool(self.before_url and self.after_url and self.before_url != self.after_url)

    @property
    def dom_changed(self) -> bool:
        return bool(
            self.before_dom_signature
            and self.after_dom_signature
            and self.before_dom_signature != self.after_dom_signature
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "new_requests_count": self.new_requests_count,
            "new_requests": list(self.new_requests),
            "api_new_requests_count": self.api_new_requests_count,
            "api_new_requests": list(self.api_new_requests),
            "before_url": self.before_url,
            "after_url": self.after_url,
            "before_dom_signature": self.before_dom_signature,
            "after_dom_signature": self.after_dom_signature,
            "url_changed": self.url_changed,
            "dom_changed": self.dom_changed,
        }


@dataclass(frozen=True)
class ActionRecord:
    turn: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    error: str | None = None
    ref_label: str | None = None
    network_delta: NetworkDelta = field(default_factory=NetworkDelta)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "ok": self.ok,
            "error": self.error,
            "ref_label": self.ref_label,
            "network_delta": self.network_delta.as_dict(),
        }


@dataclass
class AgentMemory:
    max_actions: int = 12
    history: AgentHistory = field(init=False)
    failed_ref_keys: set[str] = field(default_factory=set)
    failed_refs: list[dict[str, str]] = field(default_factory=list)
    clicked_ref_keys: set[str] = field(default_factory=set)
    typed_ref_keys: set[str] = field(default_factory=set)
    typed_form_keys: set[str] = field(default_factory=set)
    attempted_form_keys: set[str] = field(default_factory=set)
    observed_request_keys: set[tuple[str, str]] = field(default_factory=set)
    visited_urls: set[str] = field(default_factory=set)
    visited_state_keys: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.history = AgentHistory(max_items=self.max_actions)

    def filter_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        refs = snapshot.get("refs")
        if not isinstance(refs, list):
            return snapshot
        filtered = []
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            label = label_for_ref(ref)
            key = interaction_key(ref, label)
            if key and key in self.failed_ref_keys:
                continue
            if key and key in self.clicked_ref_keys and is_click_candidate(ref):
                continue
            if key and key in self.typed_ref_keys and _is_typeable_ref(ref):
                continue
            filtered.append(ref)
        out = dict(snapshot)
        out["refs"] = filtered
        return out

    def remember_action(self, record: ActionRecord, ref_info: dict[str, Any] | None) -> None:
        self.history.append(record.as_dict())
        if not ref_info:
            return
        label = label_for_ref(ref_info)
        key = interaction_key(ref_info, label)
        if record.ok:
            if key:
                if record.tool in {"type_ref", "select_ref"}:
                    self.typed_ref_keys.add(key)
                elif is_click_candidate(ref_info):
                    self.clicked_ref_keys.add(key)
            return
        if key:
            self.failed_ref_keys.add(key)
        self.failed_refs.append({
            "ref": str(ref_info.get("ref") or ""),
            "label": label,
            "error": record.error or "",
        })

    def remember_requests(self, records: list[dict[str, Any]]) -> None:
        self.observed_request_keys.update(request_key(record) for record in records)

    def remember_typed_form(self, key: str) -> None:
        if key:
            self.typed_form_keys.add(key)

    def remember_form_attempt(self, key: str) -> None:
        if key:
            self.attempted_form_keys.add(key)
            self.typed_form_keys.discard(key)

    def remember_state(self, url: str | None, dom_signature: str | None) -> None:
        if url:
            self.visited_urls.add(url)
        key = state_key(url, dom_signature)
        if key:
            self.visited_state_keys.add(key)

    def has_seen_state(self, url: str | None, dom_signature: str | None) -> bool:
        key = state_key(url, dom_signature)
        return bool(key and key in self.visited_state_keys)

    def summary(self) -> dict[str, Any]:
        return {
            "failed_refs": self.failed_refs[-8:],
            "clicked_ref_count": len(self.clicked_ref_keys),
            "typed_ref_count": len(self.typed_ref_keys),
            "forms_with_typed_fields": sorted(self.typed_form_keys)[-8:],
            "attempted_forms": sorted(self.attempted_form_keys)[-12:],
            "observed_request_count": len(self.observed_request_keys),
            "visited_url_count": len(self.visited_urls),
            "visited_urls": sorted(self.visited_urls)[-20:],
            "recent_actions": self.history.items()[-6:],
        }


def request_key(record: dict[str, Any]) -> tuple[str, str]:
    method = str(record.get("method") or "GET").upper()
    url = str(record.get("url") or "")
    return method, url


def state_key(url: str | None, dom_signature: str | None) -> str:
    if not url or not dom_signature:
        return ""
    return f"{url}#{dom_signature}"


def _is_typeable_ref(info: dict[str, Any]) -> bool:
    tag = str(info.get("tag") or "").lower()
    if tag in {"textarea", "select"}:
        return True
    if tag != "input":
        return False
    field_type = str(info.get("type") or "text").lower()
    return field_type not in {
        "checkbox",
        "radio",
        "hidden",
        "submit",
        "button",
        "reset",
        "image",
        "file",
    }
