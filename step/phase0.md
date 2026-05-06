# Phase 0 - DB 누적 모델

## 목표
- 여러 번의 scan 결과를 같은 DB에 누적한다.
- 이후 `flagged`, `resume`, `summary` 기능을 위한 테이블을 먼저 고정한다.

## 진행 내용
- `scan`, `endpoint`, `parameter` 저장 구조를 누적 merge 방식으로 확장했다.
- `auth_context`를 도입해 같은 endpoint라도 auth state별로 분리 저장되게 했다.
- `flagged_items`, `frontier_state`, `scan_summary`, `static_candidate` 테이블을 추가했다.
- 기존 v1 DB를 위한 best-effort migration 유틸을 추가했다.
- endpoint 중복 저장, auth별 분리, flagged dedupe, frontier helper 테스트를 추가했다.

## 검증
- DB 저장/누적 관련 테스트 통과.
- 이후 phase에서 같은 DB에 여러 scan 결과를 누적 저장할 수 있는 기반 확보.
