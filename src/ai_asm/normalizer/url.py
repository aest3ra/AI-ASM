"""URL path templatization: collapse high-cardinality segments to placeholders."""

from __future__ import annotations

import re

_NUMERIC = re.compile(r"\d+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_HEX = re.compile(r"[0-9a-fA-F]{16,}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EMBEDDED_NUM = re.compile(r"\d{4,}")  # 4+ consecutive digits inside a segment
_ALNUM_ONLY = re.compile(r"[A-Za-z0-9]+")
_SLUG_MIN_LEN = 24  # tight enough to skip most natural CamelCase names


def templatize_path(path: str) -> str:
    """Replace numeric/UUID/hash/date segments with `{id}`/`{uuid}`/etc."""
    if not path:
        return path
    parts = path.split("/")
    out: list[str] = []
    for p in parts:
        if not p:
            out.append(p)
            continue
        out.append(_replace_segment(p))
    return "/".join(out)


def _replace_segment(seg: str) -> str:
    if _UUID.fullmatch(seg):
        return "{uuid}"
    if _DATE.fullmatch(seg):
        return "{date}"
    if _LONG_HEX.fullmatch(seg):
        return "{hash}"
    if _NUMERIC.fullmatch(seg):
        return "{id}"

    # Slug check, extension-aware. e.g. `IXpwbBepRmUuLmmmVNgQhzEueH.pdf`
    # -> `{slug}.pdf`. Conservative heuristic: long, mixed-case, no separators.
    if "." in seg:
        stem, _, ext = seg.rpartition(".")
        if _looks_like_random_slug(stem):
            return "{slug}." + ext
    elif _looks_like_random_slug(seg):
        return "{slug}"

    # Fallback: collapse embedded digit runs inside a mixed segment, e.g.
    # `thumb_220224_1776128639000.do` -> `thumb_{n}_{n}.do`.
    return _EMBEDDED_NUM.sub("{n}", seg)


def _looks_like_random_slug(s: str) -> bool:
    """True if `s` resembles a random hash/nanoid: long, mixed-case, alphanumeric only."""
    if len(s) < _SLUG_MIN_LEN:
        return False
    if not _ALNUM_ONLY.fullmatch(s):
        return False
    has_lower = any(c.islower() for c in s)
    has_upper = any(c.isupper() for c in s)
    return has_lower and has_upper
