"""Configurable test data for safe form submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_VALUES = {
    "text": "ai-asm-test",
    "email": "ai-asm@example.com",
    "password": "Password123!",
    "tel": "5551234567",
    "url": "https://example.com",
    "number": "1",
    "date": "2025-01-01",
    "time": "12:00",
    "datetime-local": "2025-01-01T12:00",
    "textarea": "Automated ai-asm test message.",
}


@dataclass(frozen=True)
class FormDataSet:
    defaults: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_VALUES))
    fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> "FormDataSet":
        if path is None:
            return cls()
        data_path = Path(path)
        if not data_path.exists():
            return cls()
        raw = yaml.safe_load(data_path.read_text()) or {}
        defaults = {
            **DEFAULT_VALUES,
            **_string_map(raw.get("defaults") or {}),
        }
        return cls(
            defaults=defaults,
            fields=_string_map(raw.get("fields") or {}),
        )

    def values_for_form(self, form_info: dict[str, Any]) -> dict[str, str]:
        values: dict[str, str] = {}
        fields = form_info.get("input_fields") or []
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            values[name] = self.value_for_field(field)
        return values

    def value_for_field(self, field: dict[str, Any]) -> str:
        for key in _field_lookup_keys(field):
            if key in self.fields:
                return self.fields[key]
        field_type = str(field.get("type") or field.get("tag") or "text").lower()
        if field_type in self.defaults:
            return self.defaults[field_type]
        if str(field.get("tag") or "").lower() == "textarea":
            return self.defaults.get("textarea", DEFAULT_VALUES["textarea"])
        return self.defaults.get("text", DEFAULT_VALUES["text"])

    def summary(self) -> dict[str, Any]:
        return {
            "default_types": sorted(self.defaults),
            "field_keys": sorted(self.fields),
        }


def _field_lookup_keys(field: dict[str, Any]) -> list[str]:
    keys = []
    for attr in ("name", "id", "placeholder", "aria_label", "label", "text"):
        value = str(field.get(attr) or "").strip().lower()
        if value:
            keys.append(value)
    return keys


def _string_map(value: dict[str, Any]) -> dict[str, str]:
    return {
        str(key).strip().lower(): str(item)
        for key, item in value.items()
        if str(key).strip()
    }
