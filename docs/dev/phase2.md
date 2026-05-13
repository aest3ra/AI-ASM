# Phase 2 - Browser Hook / DOM Signature

## 목표
- 브라우저 runtime에서 발생하는 요청을 더 잘 캡처한다.
- SPA 상태를 URL만이 아니라 DOM signature로도 구분할 준비를 한다.

## 진행 내용
- Playwright `add_init_script` hook을 추가했다.
  - `fetch`
  - `Request`
  - `XMLHttpRequest.open`
  - `WebSocket`
  - `EventSource`
- init script가 기록한 요청을 `CapturedRequest(source="init_script")`로 변환한다.
- a11y snapshot 기반 `dom_signature` 계산 로직을 추가했다.
- scan diagnostics에 다음 값을 추가했다.
  - `dom_signatures_seen`
  - `init_script_requests_recorded`
  - `init_script_requests_added`
  - `out_of_scope_requests_aborted`
- CLI diagnostics 출력에 DOM signature / init script 지표를 추가했다.

## 검증
- Juice Shop smoke scan에서 `init_script requests recorded`, `init_script requests added`가 출력됨.
- `request log`에 `source=init_script` 요청이 남는 것 확인.
- DOM signature 관련 단위 테스트 통과.
