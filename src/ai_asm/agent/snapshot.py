"""Compact DOM snapshots for browser-state deduplication and agent context."""

from __future__ import annotations

import hashlib
from typing import Any

from playwright.async_api import Page

DOM_SIGNATURE_SCRIPT = """
() => {
    const selector = [
        "a[href]", "button", "input", "textarea", "select", "form",
        "[role]", "[aria-label]", "[data-testid]"
    ].join(",");
    const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style && style.visibility !== "hidden" && style.display !== "none" &&
            rect.width > 0 && rect.height > 0 &&
            rect.bottom >= 0 && rect.right >= 0 &&
            rect.top <= window.innerHeight && rect.left <= window.innerWidth;
    };
    return Array.from(document.querySelectorAll(selector))
        .filter(visible)
        .slice(0, 250)
        .map((el) => [
            el.tagName.toLowerCase(),
            el.getAttribute("role") || "",
            el.getAttribute("aria-label") ||
                el.getAttribute("name") ||
                el.getAttribute("placeholder") ||
                el.getAttribute("href") ||
                (el.innerText || el.textContent || "").trim().slice(0, 80),
        ]);
}
"""

AGENT_SNAPSHOT_SCRIPT = """
(maxRefs) => {
    const selector = [
        "a[href]", "button", "input:not([type=hidden])", "textarea", "select",
        "form", "[role=button]", "[role=tab]", "[role=menuitem]"
    ].join(",");
    const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style && style.visibility !== "hidden" && style.display !== "none" &&
            rect.width > 0 && rect.height > 0 &&
            rect.bottom >= 0 && rect.right >= 0 &&
            rect.top <= window.innerHeight && rect.left <= window.innerWidth;
    };
    const labelFor = (el) => (
        el.getAttribute("aria-label") ||
        el.getAttribute("name") ||
        el.getAttribute("placeholder") ||
        (el.innerText || el.textContent || "").trim().slice(0, 120) ||
        el.getAttribute("href") ||
        ""
    );
    for (const old of Array.from(document.querySelectorAll("[data-ai-asm-ref]"))) {
        old.removeAttribute("data-ai-asm-ref");
    }
    const refs = [];
    const blockedBy = (el) => {
        const rect = el.getBoundingClientRect();
        const x = Math.min(Math.max(rect.left + rect.width / 2, 0), window.innerWidth - 1);
        const y = Math.min(Math.max(rect.top + rect.height / 2, 0), window.innerHeight - 1);
        const top = document.elementFromPoint(x, y);
        if (!top || top === el || el.contains(top)) return "";
        const topLabel = labelFor(top);
        return `${top.tagName.toLowerCase()} ${topLabel}`.trim();
    };
    for (const el of Array.from(document.querySelectorAll(selector)).filter(visible)) {
        if (refs.length >= maxRefs) break;
        const blocker = blockedBy(el);
        if (blocker) continue;
        const ref = `r${refs.length + 1}`;
        el.setAttribute("data-ai-asm-ref", ref);
        const tag = el.tagName.toLowerCase();
        const form = tag === "form" ? el : el.closest("form");
        const formFields = form
            ? Array.from(form.querySelectorAll("input, textarea, select"))
            : [];
        const inputTypes = formFields.map((field) => {
                const fieldTag = field.tagName.toLowerCase();
                return String(field.getAttribute("type") || fieldTag || "text").toLowerCase();
            });
        const submit = form
            ? form.querySelector("[type=submit], button:not([type])")
            : null;
        refs.push({
            ref,
            tag,
            type: String(
                el.getAttribute("type") ||
                (tag === "textarea" ? "textarea" : tag === "select" ? "select" : "")
            ).toLowerCase(),
            role: el.getAttribute("role") || "",
            text: labelFor(el),
            actionable: true,
            blocked_by: "",
            aria_label: el.getAttribute("aria-label") || "",
            name: el.getAttribute("name") || "",
            href: el.getAttribute("href") ||
                el.getAttribute("routerlink") ||
                el.getAttribute("ng-reflect-router-link") ||
                "",
            input_types: inputTypes,
            input_fields: formFields.map((field) => ({
                tag: field.tagName.toLowerCase(),
                type: String(field.getAttribute("type") || field.tagName || "text").toLowerCase(),
                name: field.getAttribute("name") || "",
                id: field.getAttribute("id") || "",
                placeholder: field.getAttribute("placeholder") || "",
                aria_label: field.getAttribute("aria-label") || "",
            })),
            form_method: form ? String(form.method || "GET").toUpperCase() : "",
            form_action: form ? String(form.action || window.location.href) : "",
            submit_text: submit
                ? ((submit.innerText || submit.value || "").trim().slice(0, 120))
                : "",
        });
    }
    return { url: window.location.href, refs };
}
"""


async def compute_dom_signature(page: Page) -> str | None:
    try:
        items = await page.evaluate(DOM_SIGNATURE_SCRIPT)
    except Exception:
        return None
    return dom_signature_from_items(items)


async def capture_agent_snapshot(page: Page, *, max_refs: int = 120) -> dict[str, Any]:
    """Return a compact, ref-addressable list of visible controls.

    The script writes temporary `data-ai-asm-ref` attributes that the tool
    executor's Playwright adapter can click/fill/submit later in the same page
    turn.
    """
    try:
        snapshot = await page.evaluate(AGENT_SNAPSHOT_SCRIPT, max_refs)
    except Exception:
        return {"url": getattr(page, "url", None), "refs": []}
    if not isinstance(snapshot, dict):
        return {"url": getattr(page, "url", None), "refs": []}
    refs = snapshot.get("refs")
    if not isinstance(refs, list):
        snapshot["refs"] = []
    return snapshot


def dom_signature_from_items(items: list[Any]) -> str:
    normalized = []
    for item in items:
        if not isinstance(item, (list, tuple)):
            continue
        normalized.append("|".join(_clean_part(part) for part in item[:3]))
    payload = "\n".join(normalized)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _clean_part(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
