import json

from scripts.openapi_path_coverage import (
    canonical_coverage_path,
    read_expected_paths,
)


def test_read_expected_paths_filters_to_api_like_paths(tmp_path):
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.1",
        "paths": {
            "/identity/api/auth/login": {"post": {}},
            "/workshop/api/mechanic/mechanic_report": {"get": {}},
            "/assets/logo.png": {"get": {}},
        },
    }))

    assert read_expected_paths(spec) == {
        "/identity/api/auth/login",
        "/workshop/api/mechanic/mechanic_report",
    }


def test_canonical_coverage_path_treats_placeholder_names_as_equivalent():
    assert canonical_coverage_path("/identity/api/v2/user/videos/{video_id}") == (
        "/identity/api/v2/user/videos/{id}"
    )
