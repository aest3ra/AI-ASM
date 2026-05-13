import json

import yaml
from sqlmodel import Session

from orbis.output.flagged import (
    load_flagged_items,
    render_curl,
    render_http,
    render_postman,
    render_yaml,
)
from orbis.storage.db import Scan, open_db
from orbis.storage.repo import record_flagged_item


def test_flagged_yaml_export_preserves_context(tmp_path):
    db_path, _auth_path = _flagged_db(tmp_path)

    items = load_flagged_items(db_path, kind="agent_blacklist")
    rendered = render_yaml(items)
    data = yaml.safe_load(rendered)

    assert len(data) == 1
    assert data[0]["flag_kind"] == "agent_blacklist"
    assert data[0]["context"] == {"matched": "logout"}


def test_flagged_curl_and_http_exports_include_storage_state_cookies(tmp_path):
    db_path, _auth_path = _flagged_db(tmp_path)
    items = load_flagged_items(db_path)

    curl = render_curl(items)
    http = render_http(items)

    assert "curl -X POST https://example.com/api/logout" in curl
    assert "-H 'Cookie: session=abc'" in curl
    assert "POST https://example.com/api/logout" in http
    assert "Cookie: session=abc" in http


def test_flagged_postman_export_is_valid_collection(tmp_path):
    db_path, _auth_path = _flagged_db(tmp_path)
    items = load_flagged_items(db_path)

    collection = json.loads(render_postman(items))

    assert collection["info"]["name"] == "orbis flagged items"
    assert collection["item"][0]["request"]["method"] == "POST"
    assert collection["item"][0]["request"]["header"][0]["key"] == "Cookie"


def test_flagged_exports_warn_when_storage_state_cannot_be_loaded(tmp_path):
    db_path = tmp_path / "orbis.db"
    broken_auth = tmp_path / "broken.json"
    broken_auth.write_text("{")
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)
        record_flagged_item(
            session,
            scan_id=scan.id,
            flag_kind="agent_scope",
            method="GET",
            url="https://example.com/api/admin",
            description="out of scope",
            auth_state_path=broken_auth,
        )

    items = load_flagged_items(db_path)

    assert "# WARNING: could not load storage_state cookies" in render_curl(items)
    assert "# WARNING: could not load storage_state cookies" in render_http(items)
    assert "cookie_warning" in render_postman(items)


def _flagged_db(tmp_path):
    db_path = tmp_path / "orbis.db"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "cookies": [
            {
                "name": "session",
                "value": "abc",
                "domain": "example.com",
            },
        ],
    }))
    engine = open_db(db_path)
    with Session(engine) as session:
        scan = Scan(target="https://example.com/")
        session.add(scan)
        session.commit()
        session.refresh(scan)
        record_flagged_item(
            session,
            scan_id=scan.id,
            flag_kind="agent_blacklist",
            item_kind="click",
            method="POST",
            url="https://example.com/api/logout",
            description="Click on Logout",
            page_url="https://example.com/dashboard",
            context_json={"matched": "logout"},
            auth_state_path=auth_path,
        )
    return db_path, auth_path
