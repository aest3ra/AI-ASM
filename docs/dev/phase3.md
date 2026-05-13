# Phase 3 - Frontier / Checkpoint / Resume

## 목표
- BFS frontier를 단순 URL이 아니라 상태 단위로 관리한다.
- scan 중단 후 이어서 실행할 수 있는 checkpoint 기반을 만든다.

## 진행 내용
- `FrontierItem`을 추가했다.
  - `url`
  - `dom_signature`
  - `replay_steps_json`
  - `db_id`
- runner frontier를 `deque[str]`에서 `deque[FrontierItem]`로 변경했다.
- dedupe key를 `(route, dom_signature)` 형태로 확장했다.
- `frontier_state`를 page 단위로 갱신한다.
  - `pending`
  - `in_progress`
  - `done`
  - `failed`
- CLI에 `--resume <db>` 옵션을 추가했다.
- resume 시 pending/in_progress frontier를 다시 queue에 올린다.
- SIGINT 처리를 추가했다.
  - 1회: 현재 page 처리 후 종료
  - 2회: 즉시 중단
- static candidate 저장을 resume에 안전하도록 idempotent하게 변경했다.
- static GET probe 인증 옵션을 추가했다.
  - 기본: `cookie-only`
  - 선택: `--static-probe-auth learned`
  - 실제 same-origin XHR/Fetch에서 관측된 `Authorization` 헤더만 재사용한다.

## 검증
- 전체 테스트: `168 passed`
- compileall 통과.
- Juice Shop smoke scan:
  - `frontier_state`: `done=9`
  - `dom_signature` 채워진 row: `9`
  - 무인증 GET coverage: `29/30 = 96.7%`
- Juice Shop 인증 scan:
  - `auth-juice-admin.json` storage_state로 로그인 상태 확인
  - `static_probe_auth=learned`
  - static probe 결과: `200=20`, `401=1`, `500=4`
  - 기존 cookie-only 대비 `401=7` → `401=1`
- 완료된 DB에서 `--resume` 실행 시 pending frontier 없음 에러가 정상 출력됨.

## 남은 점
- endpoint/verified 결과의 page 단위 즉시 flush는 Phase 4 Agent/VerifiedStore 통합 시 함께 정리하는 편이 낫다.
