"""Frontier state, dedupe, and checkpoint helpers for BFS crawling."""

from __future__ import annotations

from collections import deque
from urllib.parse import parse_qsl, urljoin, urlparse

from sqlmodel import Session, select

from orbis.crawler.scope import Scope
from orbis.crawler.types import FrontierItem, ScanDiagnostics
from orbis.normalizer.url import templatize_path
from orbis.safety import is_dangerous_url, is_download_url
from orbis.storage.db import FrontierState
from orbis.storage.repo import (
    complete_frontier_item,
    load_pending_frontier,
    update_frontier_status,
    upsert_frontier_item,
)


def normalize_url(url: str) -> str:
    """Strip plain anchors while preserving SPA hash routes."""
    parsed = urlparse(url)
    fragment = parsed.fragment
    if fragment.startswith("/") or fragment.startswith("!/"):
        if parsed.path not in ("", "/") and "." not in parsed.path.rsplit("/", 1)[-1]:
            return parsed._replace(path="/").geturl()
        return url
    if not fragment:
        return url
    return parsed._replace(fragment="").geturl()


def template_key(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.fragment.startswith("/") or parsed.fragment.startswith("!/"):
        hash_path = parsed.fragment.lstrip("!")
        path = path.rstrip("/") + hash_path
    return parsed.hostname or "", templatize_path(path)


def route_seen_key(url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(url)
    route_path = parsed.path or "/"
    route_query = parsed.query
    fragment = parsed.fragment
    if fragment.startswith("/") or fragment.startswith("!/"):
        frag = fragment.lstrip("!")
        frag_parsed = urlparse(frag)
        route_path = frag_parsed.path or "/"
        route_query = frag_parsed.query
    return (
        parsed.scheme,
        parsed.hostname or "",
        route_path.rstrip("/") or "/",
        route_query,
    )


def frontier_seen_key(item: FrontierItem) -> tuple[str, str, str, str, str | None]:
    return (*route_seen_key(item.url), item.dom_signature)


def has_out_of_scope_redirect_target(url: str, scope: Scope) -> bool:
    parsed = urlparse(url)
    redirect_param_names = {
        "to", "url", "target", "redirect", "redirect_uri", "return", "returnto",
        "next", "continue",
    }
    for name, value in parse_qsl(parsed.query, keep_blank_values=False):
        if name.lower() not in redirect_param_names:
            continue
        target = urljoin(url, value)
        target_parsed = urlparse(target)
        if target_parsed.scheme in {"http", "https"} and not scope.allows(target):
            return True
    return False


class FrontierManager:
    def __init__(
        self,
        *,
        scope: Scope,
        cap: int,
        db_engine=None,
        scan_id: int | None = None,
    ) -> None:
        self.scope = scope
        self.cap = cap
        self.db_engine = db_engine
        self.scan_id = scan_id

    def load_resume(self) -> tuple[deque[FrontierItem], set]:
        queue: deque[FrontierItem] = deque()
        seen: set = set()
        if self.db_engine is None or self.scan_id is None:
            return queue, seen
        with Session(self.db_engine) as session:
            for row in session.exec(
                select(FrontierState).where(FrontierState.scan_id == self.scan_id)
            ).all():
                item = FrontierItem(
                    url=row.url,
                    dom_signature=row.dom_signature,
                    replay_steps_json=row.replay_steps_json,
                    db_id=row.id,
                )
                seen.add(frontier_seen_key(item))
                seen.add(frontier_seen_key(FrontierItem(row.url)))
            for row in load_pending_frontier(session, self.scan_id):
                queue.append(FrontierItem(
                    url=row.url,
                    dom_signature=row.dom_signature,
                    replay_steps_json=row.replay_steps_json,
                    db_id=row.id,
                ))
        return queue, seen

    def try_enqueue(
        self,
        url: str,
        queue: deque[FrontierItem],
        seen: set,
        diag: ScanDiagnostics,
        *,
        dom_signature: str | None = None,
        replay_steps_json: str | None = None,
    ) -> None:
        item = FrontierItem(
            url=url,
            dom_signature=dom_signature,
            replay_steps_json=replay_steps_json,
        )
        route_key = frontier_seen_key(item)
        if route_key in seen or not self.scope.allows(url):
            return
        if is_dangerous_url(url):
            diag.links_skipped_danger += 1
            return
        if is_download_url(url):
            diag.links_skipped_file += 1
            return
        if has_out_of_scope_redirect_target(url, self.scope):
            diag.links_skipped_external_redirect += 1
            return
        key = template_key(url)
        diag.template_seen[key] += 1
        if diag.template_visits[key] >= self.cap:
            diag.links_skipped_template_cap += 1
            return
        self.persist_enqueue(item)
        queue.append(item)
        seen.add(route_key)
        diag.template_visits[key] += 1
        diag.links_enqueued += 1

    def persist_enqueue(self, item: FrontierItem) -> None:
        if self.db_engine is None or self.scan_id is None:
            return
        with Session(self.db_engine) as session:
            row = upsert_frontier_item(
                session,
                scan_id=self.scan_id,
                url=item.url,
                dom_signature=item.dom_signature,
                replay_steps_json=item.replay_steps_json,
                status="pending",
            )
            item.db_id = row.id

    def mark_in_progress(self, item: FrontierItem) -> None:
        if self.db_engine is None or item.db_id is None:
            return
        with Session(self.db_engine) as session:
            update_frontier_status(session, item.db_id, "in_progress")

    def complete(
        self,
        item: FrontierItem,
        *,
        status: str,
        dom_signature: str | None,
    ) -> None:
        if self.db_engine is None or item.db_id is None:
            return
        with Session(self.db_engine) as session:
            complete_frontier_item(
                session,
                item.db_id,
                status=status,
                dom_signature=dom_signature,
            )
