from ai_asm.config import ScopeConfig
from ai_asm.crawler.scope import Scope
from ai_asm.crawler.types import CapturedRequest
from ai_asm.analyzer.js_ast import extract_candidates
from ai_asm.normalizer.static import discover_api_candidates


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
