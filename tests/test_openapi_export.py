import yaml
from sqlmodel import Session, select

from orbis.normalizer.types import NormalizedEndpoint, NormalizedParameter
from orbis.output.openapi import openapi_from_db, openapi_to_yaml
from orbis.storage.db import Parameter, Scan, open_db
from orbis.storage.repo import save_endpoints


def test_openapi_export_includes_params_body_and_response_schema(tmp_path):
    db_path = tmp_path / "orbis.db"
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)

        get_user = NormalizedEndpoint(
            method="GET",
            host="example.com",
            path_template="/api/users/{id}",
            sample_url="https://example.com/api/users/42?active=true",
            seen_count=1,
        )
        get_user.parameters[("query", "active")] = NormalizedParameter(
            location="query",
            name="active",
            type_inferred="bool",
            sample_values=["true"],
            seen_count=1,
        )
        login = NormalizedEndpoint(
            method="POST",
            host="example.com",
            path_template="/api/login",
            sample_url="https://example.com/api/login",
            seen_count=1,
        )
        login.parameters[("body", "email")] = NormalizedParameter(
            location="body",
            name="email",
            type_inferred="string",
            sample_values=["user@example.com"],
            seen_count=1,
        )
        save_endpoints(
            session,
            scan.id,
            [get_user, login],
            response_schemas={
                ("GET", "example.com", "/api/users/{id}"): {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            },
        )

    spec = openapi_from_db(db_path)
    assert spec["openapi"] == "3.0.3"
    get_op = spec["paths"]["/api/users/{id}"]["get"]
    assert {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}} in get_op["parameters"]
    assert {"name": "active", "in": "query", "required": False, "schema": {"type": "boolean"}} in get_op["parameters"]
    assert get_op["responses"]["200"]["content"]["application/json"]["schema"]["required"] == ["id"]

    post_op = spec["paths"]["/api/login"]["post"]
    assert post_op["requestBody"]["required"] is True
    assert post_op["requestBody"]["content"]["application/json"]["schema"] == {
        "type": "object",
        "properties": {"email": {"type": "string"}},
    }
    assert yaml.safe_load(openapi_to_yaml(spec))["paths"]["/api/login"]["post"]


def test_openapi_export_can_filter_by_last_seen_scan(tmp_path):
    db_path = tmp_path / "orbis.db"
    engine = open_db(db_path)
    with Session(engine) as session:
        scan1 = Scan(target="https://example.com/")
        scan2 = Scan(target="https://example.com/")
        session.add(scan1)
        session.add(scan2)
        session.commit()
        session.refresh(scan1)
        session.refresh(scan2)

        old_endpoint = NormalizedEndpoint(
            method="GET",
            host="example.com",
            path_template="/api/old",
            sample_url="https://example.com/api/old",
            seen_count=1,
        )
        new_endpoint = NormalizedEndpoint(
            method="GET",
            host="example.com",
            path_template="/api/new",
            sample_url="https://example.com/api/new",
            seen_count=1,
        )
        save_endpoints(session, scan1.id, [old_endpoint])
        save_endpoints(session, scan2.id, [new_endpoint])
        scan2_id = scan2.id

    spec = openapi_from_db(db_path, scan_id=scan2_id)
    assert "/api/new" in spec["paths"]
    assert "/api/old" not in spec["paths"]


def test_openapi_parameter_merge_does_not_mutate_db_rows(tmp_path):
    db_path = tmp_path / "orbis.db"
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)

        first = NormalizedEndpoint(
            method="GET",
            host="example.com",
            path_template="/api/search",
            sample_url="https://example.com/api/search?q=a",
            seen_count=1,
        )
        first.parameters[("query", "q")] = NormalizedParameter(
            location="query",
            name="q",
            type_inferred="string",
            sample_values=["a"],
            seen_count=1,
        )
        second = NormalizedEndpoint(
            method="GET",
            host="example.org",
            path_template="/api/search",
            sample_url="https://example.org/api/search?q=b",
            seen_count=1,
        )
        second.parameters[("query", "q")] = NormalizedParameter(
            location="query",
            name="q",
            type_inferred="string",
            sample_values=["b"],
            seen_count=1,
        )
        save_endpoints(session, scan.id, [first], auth_label="first")
        save_endpoints(session, scan.id, [second], auth_label="second")

    spec = openapi_from_db(db_path)
    assert spec["paths"]["/api/search"]["get"]["parameters"][0]["name"] == "q"

    with Session(engine) as session:
        rows = session.exec(select(Parameter)).all()
        assert [row.seen_count for row in rows] == [1, 1]
