# Phase 1 - Static Analyzer / Dispatcher

## 목표
- 기존 정적 후보 탐지를 event-driven Track 1 구조로 분리한다.
- JS/HTML/inline/manifest 분석 결과를 CandidateStore에 쌓는다.

## 진행 내용
- `shared/` store 계층을 추가했다.
  - `CandidateStore`
  - `VerifiedStore`
  - `ResponseStore`
  - `DecisionTrace`
  - `RegistryFacade`
- `analyzer/` 패키지를 추가했다.
  - `dispatcher.py`
  - `html.py`
  - `inline.py`
  - `manifest.py`
  - `js_ast.py`
  - `common.py`
- `page.on("response")` 기반으로 JS/HTML/JSON 응답을 dispatcher에 전달한다.
- MIME, size, scope, dedupe, rate limit, timeout, queue limit을 dispatcher 한 곳에서 처리한다.
- request log export를 추가해 각 요청을 jsonl로 디버깅할 수 있게 했다.
- static analyzer를 강화했다.
  - `fetch`, `Request`, `XHR.open`, `jQuery`, `axios` 패턴 지원
  - service prefix 경로 지원: `/identity/api/...`, `/workshop/api/...`
  - 상수 테이블 조합 지원: `base + endpoints.LOGIN`
  - `<id>` placeholder를 `{id}`로 정규화

## 검증
- Juice Shop 무인증 GET coverage: `29/30 = 96.7%`
- Juice Shop public path coverage: `42/43 = 97.7%`
- crAPI OpenAPI path coverage: `30/40 = 75.0%`
- static analyzer 관련 테스트 추가 및 통과.
