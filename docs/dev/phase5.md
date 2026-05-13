# Phase 5 - OpenAI LLM Integration

## 목표
- 실제 LLM agent 모드를 추가한다.
- form 제출용 테스트 데이터를 별도 YAML로 관리한다.
- Mock agent와 LLM agent의 coverage를 같은 조건에서 비교한다.

## 진행 내용
- `openai` SDK 의존성을 추가했다.
- CLI 옵션을 추가했다.
  - `--agent mock|llm`
  - `--model`
  - `--agent-budget`
  - `--form-data`
- `OpenAIClient`를 추가했다.
  - OpenAI Responses API function calling 사용
  - `.env.example` 기준 `OPENAI_API_KEY` 이름만 사용
  - 실제 key 값은 출력하지 않음
- form test data를 분리했다.
  - `testdata/forms/default.yaml`
  - `agent/form_data.py`
- `submit_form`은 form field name 기준으로 test data를 주입한다.
- LLM API 실패는 trace에 `llm_failure`로 기록한다.

## 검증
- 전체 테스트: `162 passed`
- compileall 통과.
- `git diff --check` 통과.
- Juice Shop 5-page coverage 비교:
  - Mock: unauth GET `29/30 = 96.7%`, public `42/43 = 97.7%`
  - LLM: unauth GET `29/30 = 96.7%`, public `42/43 = 97.7%`

## 관찰
- LLM 연동 자체는 동작한다.
- 동일 5-page 조건에서는 Mock 대비 coverage 개선은 없었다.
- trace상 LLM이 overlay/비가시 요소를 클릭해 `TimeoutError`가 많이 발생했다.
- 다음 개선 포인트는 snapshot 품질과 tool result feedback loop다.
