from orbis.schema.inferrer import (
    infer_json_schema,
    infer_schema_from_json_bodies,
    merge_json_schemas,
)


def test_infers_object_union_with_required_intersection():
    schema = infer_schema_from_json_bodies([
        '{"id": 1, "name": "alice"}',
        '{"id": 2, "active": true}',
    ])

    assert schema == {
        "type": "object",
        "properties": {
            "active": {"type": "boolean"},
            "id": {"type": "integer"},
            "name": {"type": "string"},
        },
        "additionalProperties": True,
        "required": ["id"],
    }


def test_infers_array_item_union():
    schema = infer_json_schema([
        [{"id": 1}],
        [{"id": 2, "name": "bob"}],
    ])

    assert schema["type"] == "array"
    assert schema["items"]["properties"]["id"] == {"type": "integer"}
    assert schema["items"]["properties"]["name"] == {"type": "string"}
    assert schema["items"]["required"] == ["id"]


def test_merges_nullable_without_empty_one_of_variant():
    schema = merge_json_schemas(
        {"type": "object", "properties": {"id": {"type": "integer"}}},
        {"nullable": True},
    )

    assert schema == {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "nullable": True,
    }


def test_invalid_json_bodies_are_ignored():
    assert infer_schema_from_json_bodies(["not json", ""]) is None


def test_integer_and_number_merge_to_number():
    assert infer_json_schema([1, 1.5]) == {"type": "number"}


def test_large_mixed_type_union_collapses_to_any_schema():
    schema = infer_json_schema([1, "1", True, {"id": 1}])

    assert schema == {}
