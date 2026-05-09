# Changelog

## v0.1.0 - 2026-05-09

Initial usable release of `ai-asm` as an API surface reconstruction tool.

### Added
- Playwright/CDP based scanner with strict scope enforcement.
- Deterministic planner-first browser agent for navigation and safe form exploration.
- Safe dry-run mutation capture: mutating requests are captured and aborted before reaching the server.
- Event-driven static analyzer for HTML, inline data, manifests, JavaScript, and documented route text.
- URL surface classification separate from verified API endpoints.
- SQLite result store with auth-context-aware endpoint deduplication.
- Per-scan automatic output layout:
  - `runs/<timestamp>_<host>_<hash>.db`
  - matching artifact directory
  - `api.yaml`
  - `flagged.yaml`
  - `flagged.sh`
- OpenAPI 3.0 export from observed endpoints and inferred response schemas.
- Flagged item export for manual review in YAML, curl, HTTP Client, and Postman formats.
- Request log, redacted raw capture artifact, and decision trace output.
- Benchmark harness and local candidate service compose file.

### Notes
- `planner` is the default agent mode.
- `llm` mode remains available as an opt-in fallback/experimental mode.
- Swagger/OpenAPI document discovery/import is intentionally not part of v0.1.0.
