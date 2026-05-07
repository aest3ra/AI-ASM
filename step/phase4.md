# Phase 4 - Mock Agent Contract

## 목표
- 기존 휴리스틱 UI 루프를 agent loop 형태로 감싼다.
- 실제 LLM 없이도 `snapshot -> tool_call -> executor -> capture -> trace` 계약을 검증한다.

## 진행 내용
- `agent/` 기본 모듈을 추가했다.
  - `budget.py`: step/token budget
  - `client.py`: `LLMClient`, `MockLLMClient`, `HeuristicMockLLMClient`
  - `loop.py`: tool-use loop
  - `tools.py`: scope/blacklist/file-upload guard가 있는 executor
  - `driver.py`: Playwright page adapter + mock agent interaction driver
  - `snapshot.py`: ref 기반 agent snapshot 생성
- `crawler/runner.py`가 기존 `interactions.py/forms.py` 대신 mock agent driver를 호출한다.
- 기존 `crawler/interactions.py`, `crawler/forms.py`는 삭제했다.
- `DecisionTrace`가 `trace-{scan_id}.jsonl` 파일에 append되도록 했다.
- scan 결과 출력에 trace artifact 경로를 추가했다.

## 검증
- Phase 4 기능 테스트를 먼저 추가했다.
  - `tests/test_agent_driver.py`
  - `tests/test_agent_loop.py`
  - `tests/test_agent_tools.py`
- 전체 테스트: `157 passed`
- Juice Shop 1-page smoke scan 통과.
  - `69 raw -> 27 endpoints`
  - trace/request/raw artifact 생성 확인

## 남은 점
- 아직 real LLM은 연결하지 않았다.
- Agent context에 `RegistryFacade.summary()`를 넣는 작업은 다음 단계에서 진행한다.
- trace inspect CLI는 아직 없다.
