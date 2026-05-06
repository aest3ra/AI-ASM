from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_asm.config import load_config


def write(tmp: Path, body: str) -> Path:
    path = tmp / "scan.yaml"
    path.write_text(body)
    return path


def test_minimal_config(tmp_path: Path):
    cfg = load_config(write(tmp_path, """
target: https://example.com
scope:
  include_domains: [example.com]
"""))
    assert str(cfg.target) == "https://example.com/"
    assert cfg.scope.include_domains == ["example.com"]
    assert cfg.limits.max_pages == 200
    assert cfg.auth.type == "none"
    assert cfg.static_probe_auth == "cookie-only"


def test_storage_state_requires_path(tmp_path: Path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, """
target: https://example.com
scope:
  include_domains: [example.com]
auth:
  type: storage_state
"""))


def test_empty_scope_defaults_to_target_host(tmp_path: Path):
    """Omitting include_domains should restrict scope to the target hostname only."""
    cfg = load_config(write(tmp_path, """
target: https://www.example.com/
"""))
    assert cfg.scope.include_domains == ["www.example.com"]


def test_explicit_scope_overrides_default(tmp_path: Path):
    """When user lists include_domains, the loader does not touch them."""
    cfg = load_config(write(tmp_path, """
target: https://www.example.com/
scope:
  include_domains: [example.com, "*.example.com"]
"""))
    assert cfg.scope.include_domains == ["example.com", "*.example.com"]


def test_empty_explicit_list_falls_back_to_target_host(tmp_path: Path):
    """Explicit empty list is treated the same as omission."""
    cfg = load_config(write(tmp_path, """
target: https://www.example.com/
scope:
  include_domains: []
"""))
    assert cfg.scope.include_domains == ["www.example.com"]


def test_full_config(tmp_path: Path):
    cfg = load_config(write(tmp_path, """
target: https://api.example.com
scope:
  include_domains: [example.com, "*.example.com"]
  exclude_paths: [/logout]
limits:
  max_pages: 50
  rate_limit_rps: 1
auth:
  type: storage_state
  storage_state_path: ./auth.json
static_probe_auth: learned
"""))
    assert cfg.limits.max_pages == 50
    assert cfg.auth.storage_state_path == Path("./auth.json")
    assert "/logout" in cfg.scope.exclude_paths
    assert cfg.static_probe_auth == "learned"
