"""Discover and safely auto-submit POST forms via in-page fetch().

We deliberately use `fetch()` instead of `form.submit()` so the page doesn't
navigate away — that lets us iterate over many forms on one page without
losing DOM state, and the request is still observed by the CDP network tap.

Skipped form categories:
- non-POST (GET forms are reachable via normal link following)
- forms with `<input type="password">` (login/register/change-password —
  handled separately by the `ai-asm login` storageState flow)
- forms with `<input type="file">` (no sensible dummy file)
- forms whose submit button or action URL matches DANGER_PATTERN
- multipart/form-data forms (require File objects we can't synthesize)
"""

from __future__ import annotations

from playwright.async_api import Page

from ai_asm.crawler.types import FormStats

# JS executed in page context. Returns a stats dict.
# Mirrors `interactions.DANGER_PATTERN` — kept in sync manually.
_SUBMIT_FORMS_JS = r"""
async () => {
  const stats = {
    seen: 0, submitted: 0,
    skipped_danger: 0, skipped_password: 0,
    skipped_file: 0, skipped_get: 0,
  };
  const danger = /delete|remove|logout|sign[\- ]?out|purchase|buy|cancel|unsubscribe|withdraw|삭제|탈퇴|로그아웃|결제|취소|환불/i;

  function dummyFor(field) {
    const type = (field.type || 'text').toLowerCase();
    const hint = (field.name + ' ' + (field.placeholder || '') + ' ' + (field.id || '')).toLowerCase();
    if (type === 'email' || hint.includes('email')) return 'test@example.com';
    if (type === 'tel' || hint.includes('phone') || hint.includes('tel')) return '5551234567';
    if (type === 'url' || hint.includes('url') || hint.includes('website')) return 'https://example.com';
    if (type === 'number') return '1';
    if (type === 'date') return '2025-01-01';
    if (type === 'time') return '12:00';
    if (type === 'datetime-local') return '2025-01-01T12:00';
    return 'test';
  }

  const forms = Array.from(document.querySelectorAll('form'));
  stats.seen = forms.length;

  for (const form of forms) {
    const method = (form.method || 'GET').toUpperCase();
    if (method !== 'POST') { stats.skipped_get++; continue; }

    if (form.querySelector('input[type="password"]')) {
      stats.skipped_password++; continue;
    }
    if (form.querySelector('input[type="file"]')) {
      stats.skipped_file++; continue;
    }

    const submitBtn = form.querySelector('[type="submit"], button:not([type])');
    const submitText = submitBtn ? (submitBtn.innerText || submitBtn.value || '').trim() : '';
    if (submitText && danger.test(submitText)) { stats.skipped_danger++; continue; }

    const action = form.action || window.location.href;
    if (danger.test(action)) { stats.skipped_danger++; continue; }

    const enctype = (form.enctype || 'application/x-www-form-urlencoded').toLowerCase();
    if (enctype.includes('multipart')) { stats.skipped_file++; continue; }

    const fields = form.querySelectorAll('input, textarea, select');
    const values = [];
    for (const f of fields) {
      if (!f.name) continue;
      const t = (f.type || 'text').toLowerCase();
      if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') continue;
      if (t === 'password' || t === 'file') continue;  // already filtered above
      if (t === 'hidden') { values.push([f.name, f.value]); continue; }
      if (t === 'checkbox') {
        if (f.checked) values.push([f.name, f.value || 'on']);
        continue;
      }
      if (t === 'radio') {
        if (f.checked) values.push([f.name, f.value]);
        continue;
      }
      if (f.tagName === 'SELECT') {
        const opt = f.options[f.selectedIndex] || f.options[0];
        if (opt) values.push([f.name, opt.value]);
        continue;
      }
      values.push([f.name, dummyFor(f)]);
    }

    let body, headers = {};
    if (enctype.includes('json')) {
      const obj = {};
      for (const [k, v] of values) obj[k] = v;
      body = JSON.stringify(obj);
      headers['Content-Type'] = 'application/json';
    } else {
      const usp = new URLSearchParams();
      for (const [k, v] of values) usp.append(k, v);
      body = usp.toString();
      headers['Content-Type'] = 'application/x-www-form-urlencoded';
    }

    try {
      await fetch(action, { method: 'POST', body, headers, credentials: 'same-origin' });
      stats.submitted++;
    } catch (_) {}
  }

  return stats;
}
"""


async def submit_post_forms(page: Page) -> FormStats:
    """Fire every safe POST form on `page` via fetch(). Returns counters."""
    try:
        raw = await page.evaluate(_SUBMIT_FORMS_JS)
    except Exception:
        return FormStats()
    return FormStats(
        seen=raw.get("seen", 0),
        submitted=raw.get("submitted", 0),
        skipped_danger=raw.get("skipped_danger", 0),
        skipped_password=raw.get("skipped_password", 0),
        skipped_file=raw.get("skipped_file", 0),
        skipped_get=raw.get("skipped_get", 0),
    )
