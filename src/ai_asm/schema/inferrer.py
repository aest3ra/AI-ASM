"""Small JSON Schema inferrer for observed API responses."""

from __future__ import annotations

import json
from typing import Any

MAX_ONE_OF_VARIANTS = 3


def infer_schema_from_json_bodies(bodies: list[str | None]) -> dict[str, Any] | None:
    """Infer an OpenAPI-compatible schema from JSON response bodies."""
    values: list[Any] = []
    for body in bodies:
        if body is None:
            continue
        text = body.strip()
        if not text:
            continue
        try:
            values.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    if not values:
        return None
    return infer_json_schema(values)


def infer_json_schema(values: list[Any]) -> dict[str, Any]:
    """Infer a conservative schema that accepts every observed JSON value."""
    if not values:
        return {}
    schema = _schema_for_value(values[0])
    for value in values[1:]:
        schema = merge_json_schemas(schema, _schema_for_value(value))
    return schema


def merge_json_schemas(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Merge two inferred schemas into a schema that accepts both."""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)

    left = dict(left)
    right = dict(right)
    if left == right:
        return left

    left_nullable = bool(left.pop("nullable", False))
    right_nullable = bool(right.pop("nullable", False))
    nullable = left_nullable or right_nullable
    if not left:
        merged = dict(right)
        if nullable:
            merged["nullable"] = True
        return merged
    if not right:
        merged = dict(left)
        if nullable:
            merged["nullable"] = True
        return merged

    if left.get("type") == "object" and right.get("type") == "object":
        merged = _merge_object_schemas(left, right)
        if nullable:
            merged["nullable"] = True
        return merged

    if left.get("type") == "array" and right.get("type") == "array":
        merged = {
            "type": "array",
            "items": merge_json_schemas(
                _items_schema(left),
                _items_schema(right),
            ) or {},
        }
        if nullable:
            merged["nullable"] = True
        return merged

    if left.get("type") == right.get("type"):
        merged = {"type": left.get("type")}
        if nullable:
            merged["nullable"] = True
        return merged

    if _numeric_types(left, right):
        merged = {"type": "number"}
        if nullable:
            merged["nullable"] = True
        return merged

    variants = _flatten_one_of(left) + _flatten_one_of(right)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant in variants:
        key = json.dumps(variant, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(variant)
    if len(deduped) > MAX_ONE_OF_VARIANTS:
        return {"nullable": True} if nullable else {}
    out: dict[str, Any] = {"oneOf": deduped}
    if nullable:
        out["nullable"] = True
    return out


def _schema_for_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullable": True}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {
            "type": "array",
            "items": infer_json_schema(value) if value else {},
        }
    if isinstance(value, dict):
        properties = {
            str(key): _schema_for_value(item)
            for key, item in value.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(str(key) for key in value),
            "additionalProperties": True,
        }
    return {"type": "string"}


def _merge_object_schemas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_props = _properties(left)
    right_props = _properties(right)
    names = sorted(set(left_props) | set(right_props))
    properties: dict[str, Any] = {}
    for name in names:
        if name in left_props and name in right_props:
            properties[name] = merge_json_schemas(left_props[name], right_props[name])
        elif name in left_props:
            properties[name] = left_props[name]
        else:
            properties[name] = right_props[name]

    required = sorted(set(left.get("required") or []) & set(right.get("required") or []))
    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        out["required"] = required
    return out


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _items_schema(schema: dict[str, Any]) -> dict[str, Any]:
    items = schema.get("items")
    return items if isinstance(items, dict) else {}


def _numeric_types(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return {left.get("type"), right.get("type")} == {"integer", "number"}


def _flatten_one_of(schema: dict[str, Any]) -> list[dict[str, Any]]:
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        return [
            item for item in one_of
            if isinstance(item, dict)
        ]
    return [schema]
