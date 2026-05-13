from sqlmodel import Session, select

from orbis.config import ScopeConfig
from orbis.crawler.scope import Scope
from orbis.crawler.types import CapturedRequest
from orbis.normalizer.static import ApiCandidate
from orbis.storage.db import Scan, UrlSurface, open_db
from orbis.storage.repo import save_url_surfaces
from orbis.surface import discover_url_surfaces, surfaces_from_static_candidates


def _scope() -> Scope:
    return Scope(ScopeConfig(include_domains=["example.com"]))


def test_url_surface_separates_page_routes_from_api_endpoints():
    captures = [
        CapturedRequest(
            request_id="doc",
            method="GET",
            url="https://example.com/ko/about/history.do",
            resource_type="Document",
            response_status=200,
            response_mime="text/html",
            response_body="""
            <a href="/ko/about/campus-map.do?campus=2">Campus</a>
            <a href="/cms/print/print.do">Print</a>
            <form method="get" action="/ko/search/result.do"></form>
            """,
        ),
        CapturedRequest(
            request_id="api",
            method="GET",
            url="https://example.com/search/front/rcmdApi.jsp?q=cu",
            resource_type="Fetch",
            response_status=200,
            response_mime="application/json",
        ),
    ]

    surfaces = discover_url_surfaces(captures, _scope())
    by_path = {surface.path_template: surface for surface in surfaces}

    assert by_path["/ko/about/history.do"].route_kind == "page_route"
    assert by_path["/ko/about/campus-map.do"].route_kind == "page_route"
    assert by_path["/cms/print/print.do"].route_kind == "action_route"
    assert by_path["/ko/search/result.do"].route_kind == "action_route"
    assert by_path["/search/front/rcmdApi.jsp"].route_kind == "api_endpoint"


def test_static_candidates_become_surface_without_becoming_endpoint():
    surfaces = surfaces_from_static_candidates([
        ApiCandidate(
            host="example.com",
            path_template="/api/users",
            sample_url="https://example.com/api/users",
            source_url="https://example.com/app.js",
        ),
    ])

    assert len(surfaces) == 1
    assert surfaces[0].route_kind == "api_endpoint"
    assert surfaces[0].observed is False
    assert surfaces[0].source_kind == "js_static"


def test_unobserved_post_forms_stay_action_routes_without_api_evidence():
    captures = [
        CapturedRequest(
            request_id="doc",
            method="GET",
            url="https://example.com/ko/about/history.do",
            resource_type="Document",
            response_status=200,
            response_mime="text/html",
            response_body="""
            <form method="post" action="/cms/print/print.do"></form>
            <form method="post" action="/api/search"></form>
            """,
        )
    ]

    surfaces = discover_url_surfaces(captures, _scope())
    by_path = {
        (surface.method, surface.path_template): surface
        for surface in surfaces
    }

    assert by_path[("POST", "/cms/print/print.do")].route_kind == "action_route"
    assert by_path[("POST", "/api/search")].route_kind == "api_endpoint"


def test_download_like_acl_routes_are_file_surface_not_page_routes():
    surfaces = discover_url_surfaces([
        CapturedRequest(
            request_id="file",
            method="GET",
            url="https://example.com/ilos/co/file_download.acl?FILE_SEQ=1",
            resource_type="Document",
            response_status=200,
            response_mime="text/html",
        )
    ], _scope())

    assert surfaces[0].route_kind == "file"


def test_observed_post_xhr_html_is_api_endpoint_surface():
    surfaces = discover_url_surfaces([
        CapturedRequest(
            request_id="xhr",
            method="POST",
            url="https://example.com/ilos/main/main_schedule.acl",
            resource_type="XHR",
            response_status=200,
            response_mime="text/html",
        )
    ], _scope())

    assert surfaces[0].route_kind == "api_endpoint"


def test_save_url_surfaces_merges_same_route(tmp_path):
    engine = open_db(tmp_path / "surface.db")
    with Session(engine) as session:
        scan = Scan(target="https://example.com")
        session.add(scan)
        session.commit()
        session.refresh(scan)

        surfaces = discover_url_surfaces([
            CapturedRequest(
                request_id="one",
                method="GET",
                url="https://example.com/api/users",
                resource_type="Fetch",
                response_status=200,
                response_mime="application/json",
            ),
            CapturedRequest(
                request_id="two",
                method="GET",
                url="https://example.com/api/users",
                resource_type="Fetch",
                response_status=200,
                response_mime="application/json",
            ),
        ], _scope())
        save_url_surfaces(session, scan.id, surfaces)

        rows = session.exec(select(UrlSurface)).all()
        assert len(rows) == 1
        assert rows[0].route_kind == "api_endpoint"
        assert rows[0].seen_count == 2
