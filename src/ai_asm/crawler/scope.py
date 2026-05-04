"""Scope checks: is a URL inside the allowed crawl boundary?"""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse

from ai_asm.config import ScopeConfig


class Scope:
    def __init__(self, config: ScopeConfig) -> None:
        self._domain_patterns = [d.lower() for d in config.include_domains]
        self._exclude_paths = config.exclude_paths

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if not self._host_matches(parsed.hostname or ""):
            return False
        if self._path_excluded(parsed.path or "/"):
            return False
        return True

    def _host_matches(self, host: str) -> bool:
        host = host.lower()
        return any(fnmatch.fnmatch(host, pat) for pat in self._domain_patterns)

    def _path_excluded(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._exclude_paths)
