"""Compact DOM snapshots for browser-state deduplication."""

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
            rect.width > 0 && rect.height > 0;
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


async def compute_dom_signature(page: Page) -> str | None:
    try:
        items = await page.evaluate(DOM_SIGNATURE_SCRIPT)
    except Exception:
        return None
    return dom_signature_from_items(items)


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
