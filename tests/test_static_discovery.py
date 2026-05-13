from orbis.config import ScopeConfig
from orbis.crawler.scope import Scope
from orbis.crawler.types import CapturedRequest
from orbis.analyzer.js_ast import extract_candidates
from orbis.normalizer.static import discover_api_candidates


def cap(url: str, body: str, mime: str = "application/javascript") -> CapturedRequest:
    return CapturedRequest(
        request_id="r",
        method="GET",
        url=url,
        resource_type="Script",
        response_mime=mime,
        response_body=body,
    )


def test_discovers_scoped_api_paths_from_text_assets():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/app/main.js",
                "'/api/users/123'; fetch('/rest/products/search?q=apple')",
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/api/users/{id}"),
        ("x.test", "/rest/products/search"),
    ]


def test_does_not_import_openapi_documents_as_static_candidates():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/openapi.json",
                """
                {
                  "openapi": "3.0.0",
                  "paths": {
                    "/users/v1": {"get": {}},
                    "/books/v1/{book}": {"get": {}}
                  }
                }
                """,
                mime="application/json",
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert candidates == []


def test_discovers_prefixless_paths_from_api_docs_html():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/apidoc/index.html",
                """
                <h1>API Documentation</h1>
                <p>GET /booking</p>
                <p>GET /booking/1</p>
                <p>POST /auth</p>
                <a href="/apidoc/assets/app.js">asset</a>
                """,
                mime="text/html",
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/auth"),
        ("x.test", "/booking"),
        ("x.test", "/booking/{id}"),
    ]


def test_discovers_prefixless_httpbin_doc_links():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/",
                """
                <title>HTTP Client Testing Service</title>
                <h2>Endpoints</h2>
                <a href="/get">GET</a>
                <a href="/post">POST</a>
                <a href="/forms/post">Form POST</a>
                <a href="/anything"><code>/anything/:anything</code></a>
                <a href="/style.css">style</a>
                """,
                mime="text/html",
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/anything"),
        ("x.test", "/anything/{id}"),
        ("x.test", "/forms/post"),
        ("x.test", "/get"),
        ("x.test", "/post"),
    ]


def test_discovers_html_form_actions_and_action_links_without_page_link_noise():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/ko/index.do",
                """
                <html>
                  <title>University website</title>
                  <nav>
                    <a href="/ko/about/history.do">History</a>
                    <a href="/ko/research/result.do">Research</a>
                    <a href="/cms/etcResourceOpen.do?site=ko">Resource</a>
                    <a href="/cms/print/print.do">Print</a>
                  </nav>
                  <form method="GET" action="/ko/search/result.do">
                    <input name="q">
                  </form>
                  <p>Request information from the office.</p>
                  <p>Response times may vary.</p>
                </html>
                """,
                mime="text/html",
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/cms/etcResourceOpen.do"),
        ("x.test", "/cms/print/print.do"),
        ("x.test", "/ko/search/result.do"),
    ]


def test_does_not_treat_regular_cms_page_links_as_static_endpoints():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/ko/index.do",
                """
                <html>
                  <a href="/ko/about/history.do">History</a>
                  <a href="/ko/research/result.do">Research</a>
                  <a href="/ko/campuslife/notice.do">Notice</a>
                </html>
                """,
                mime="text/html",
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert candidates == []


def test_discovers_apidoc_data_js_paths_without_vendor_noise():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/apidoc/api_data.js",
                """
                define({ "api": [
                  {"type": "post", "url": "auth"},
                  {"type": "get", "url": "booking/:id"},
                  {"type": "get", "url": "ping"}
                ]});
                """,
                mime="application/javascript",
            ),
            cap(
                "https://x.test/apidoc/vendor/jquery.min.js",
                "define({}); var url = '/not-an-endpoint';",
                mime="application/javascript",
            ),
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/auth"),
        ("x.test", "/booking/{id}"),
        ("x.test", "/ping"),
    ]


def test_skips_out_of_scope_absolute_candidates():
    candidates = discover_api_candidates(
        [cap("https://x.test/app.js", "'https://evil.test/api/users'")],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert candidates == []


def test_skips_non_textual_bodies():
    candidates = discover_api_candidates(
        [cap("https://x.test/app.png", "'/api/users'", mime="image/png")],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert candidates == []


def test_skips_api_like_domain_path_fragments():
    candidates = discover_api_candidates(
        [cap("https://x.test/app.js", "'//api.example.com/v1'; '/api/users'")],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/api/users")
    ]


def test_discovers_api_marker_below_service_prefix_without_trimming_prefix():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/app.js",
                """
                fetch("/identity/api/auth/login", { method: "POST" });
                axios.get("https://x.test/workshop/api/mechanic/mechanic_report");
                """,
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/identity/api/auth/login"),
        ("x.test", "/workshop/api/mechanic/mechanic_report"),
    ]


def test_trims_trailing_expression_artifacts():
    candidates = discover_api_candidates(
        [cap("https://x.test/app.js", "\"/rest/basket/$\";")],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template, c.sample_url) for c in candidates] == [
        ("x.test", "/rest/basket", "https://x.test/rest/basket")
    ]


def test_discovers_angular_service_composed_api_paths():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/main.js",
                """
                class ProductsService {
                  hostServer = ".";
                  host = this.hostServer + "/api/Products";
                  find(e) { return this.http.get(this.host + "/", {params: e}); }
                  get(e) { return this.http.get(`${this.host}/${e}?d=${Date.now()}`); }
                }
                class TrackOrderService {
                  hostServer = ".";
                  host = this.hostServer + "/rest/track-order";
                  find(e) { return this.http.get(`${this.host}/${e}`); }
                }
                """,
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/api/Products"),
        ("x.test", "/api/Products/{id}"),
        ("x.test", "/rest/track-order/{id}"),
    ]


def test_discovers_service_base_plus_literal_suffixes():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/chunk.js",
                """
                class Web3Service {
                  hostServer = ".";
                  host = this.hostServer + "/rest/web3";
                  nftUnlocked() { return this.http.get(this.host + "/nftUnlocked"); }
                  nftMintListen() { return this.http.get(this.host + "/nftMintListen"); }
                }
                class SecurityQuestionService {
                  hostServer = ".";
                  findBy(e) {
                    return this.http.get(this.hostServer + "/rest/user/security-question?email=" + e);
                  }
                }
                """,
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/rest/user/security-question"),
        ("x.test", "/rest/web3/nftMintListen"),
        ("x.test", "/rest/web3/nftUnlocked"),
    ]


def test_does_not_turn_query_string_variable_into_path_id():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/main.js",
                """
                whoAmI(e) {
                  let i = e && e.length > 0 ? `?fields=${e.join(",")}` : "";
                  return this.http.get(this.hostServer + "/rest/user/whoami" + i);
                }
                """,
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template, c.sample_url) for c in candidates] == [
        ("x.test", "/rest/user/whoami", "https://x.test/rest/user/whoami")
    ]


def test_discovers_local_alias_used_in_http_call():
    candidates = discover_api_candidates(
        [
            cap(
                "https://x.test/chunk.js",
                """
                class Web3Service {
                  hostServer = ".";
                  host = this.hostServer + "/rest/web3";
                  submitKey(t) {
                    let e = this.host + "/submitKey", r = {privateKey: t};
                    return this.http.post(e, r);
                  }
                  verifyNFTWallet(t) {
                    let e = this.host + "/walletNFTVerify", r = {walletAddress: t};
                    return this.http.post(e, r);
                  }
                }
                """,
            )
        ],
        Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.host, c.path_template) for c in candidates] == [
        ("x.test", "/rest/web3/submitKey"),
        ("x.test", "/rest/web3/walletNFTVerify"),
    ]


def test_discovers_fetch_and_request_methods_from_options():
    candidates = extract_candidates(
        """
        fetch("/api/login", { method: "POST", body: "{}" });
        fetch("/api/products");
        new Request("/rest/orders/" + id, { method: "PATCH" });
        """,
        base_url="https://x.test/app.js",
        scope=Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert sorted((c.method, c.path_template) for c in candidates) == [
        ("GET", "/api/products"),
        ("PATCH", "/rest/orders/{id}"),
        ("POST", "/api/login"),
    ]


def test_discovers_xhr_open_method_and_url():
    candidates = extract_candidates(
        """
        const xhr = new XMLHttpRequest();
        xhr.open("DELETE", "/api/users/" + userId);
        xhr.send();
        """,
        base_url="https://x.test/app.js",
        scope=Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.method, c.path_template) for c in candidates] == [
        ("DELETE", "/api/users/{id}")
    ]


def test_discovers_jquery_url_and_settings_signatures():
    candidates = extract_candidates(
        """
        $.get("/api/profile");
        jQuery.post("/api/comments", { body: "ok" });
        $.ajax({ url: "/rest/admin/users", type: "PUT" });
        $.ajax("/rest/reports/" + reportId, { method: "PATCH" });
        """,
        base_url="https://x.test/app.js",
        scope=Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert sorted((c.method, c.path_template) for c in candidates) == [
        ("GET", "/api/profile"),
        ("PATCH", "/rest/reports/{id}"),
        ("POST", "/api/comments"),
        ("PUT", "/rest/admin/users"),
    ]


def test_discovers_axios_object_signature_without_asset_false_positive():
    candidates = extract_candidates(
        """
        axios({ url: "/api/audit-log", method: "POST" });
        const image = { url: "/assets/logo.png", method: "GET" };
        const websocket = { url: "wss://x.test/socket.io/", method: "GET" };
        """,
        base_url="https://x.test/app.js",
        scope=Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert [(c.method, c.path_template) for c in candidates] == [
        ("POST", "/api/audit-log")
    ]


def test_discovers_constant_table_composed_fetch_paths():
    candidates = extract_candidates(
        """
        const identity = "identity/";
        const workshop = "workshop/";
        const endpoints = {
          LOGIN: "api/auth/login",
          PRODUCTS: "api/shop/products",
          ORDER: "api/shop/orders/<orderId>",
        };
        function login(body) {
          return fetch(identity + endpoints.LOGIN, { method: "POST", body });
        }
        function products(token) {
          return fetch(workshop + endpoints.PRODUCTS, { method: "GET" });
        }
        function order(id) {
          return fetch(workshop + endpoints.ORDER.replace("<orderId>", id));
        }
        """,
        base_url="https://x.test/app.js",
        scope=Scope(ScopeConfig(include_domains=["x.test"])),
    )

    assert sorted((c.method, c.path_template) for c in candidates) == [
        ("GET", "/workshop/api/shop/orders/{id}"),
        ("GET", "/workshop/api/shop/products"),
        ("POST", "/identity/api/auth/login"),
    ]
