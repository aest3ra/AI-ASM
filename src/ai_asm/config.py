"""Scan configuration loaded from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, HttpUrl, model_validator

StaticProbeAuthMode = Literal["none", "cookie-only", "learned"]


class ScopeConfig(BaseModel):
    """Crawl scope. If `include_domains` is omitted, the loader fills it with
    the target URL's exact hostname (strict-host default). To allow subdomains
    or extra hosts, list them explicitly with optional `*` wildcards."""

    include_domains: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)


class LimitsConfig(BaseModel):
    max_pages: int = Field(default=200, gt=0)
    max_duration_sec: int = Field(default=600, gt=0)
    rate_limit_rps: float = Field(default=2.0, gt=0)
    max_visits_per_template: int = Field(default=3, gt=0)


class AuthConfig(BaseModel):
    type: Literal["none", "storage_state"] = "none"
    storage_state_path: Path | None = None

    @model_validator(mode="after")
    def _path_required_for_storage_state(self) -> "AuthConfig":
        if self.type == "storage_state" and self.storage_state_path is None:
            raise ValueError(
                "auth.storage_state_path is required when auth.type='storage_state'"
            )
        return self


class ScanConfig(BaseModel):
    target: HttpUrl
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    static_probe_auth: StaticProbeAuthMode = "cookie-only"

    @model_validator(mode="after")
    def _default_scope_to_target_host(self) -> "ScanConfig":
        """If the user didn't list include_domains, fall back to target host only.

        Subdomain and extra-host crawling is opt-in: set scope.include_domains
        explicitly with `*.example.com` style patterns to expand."""
        if not self.scope.include_domains:
            host = urlparse(str(self.target)).hostname
            if host:
                self.scope.include_domains = [host]
        return self


def load_config(path: str | Path) -> ScanConfig:
    """Load and validate a YAML scan config."""
    raw = yaml.safe_load(Path(path).read_text())
    return ScanConfig.model_validate(raw)
