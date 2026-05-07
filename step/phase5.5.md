# Phase 5.5 - State-aware Multi-turn Agent

- `AgentLoop.run_page()` 추가: `observe -> one action -> observe` 반복 구조.
- `AgentMemory` 추가: 실패 ref, 클릭 ref, 최근 action, 관측 request를 다음 턴 context에 유지.
- action별 `NetworkDelta` / `ActionRecord` 기록.
- trace에 `action_record`, `state_checkpoint` 이벤트 추가.
- LLM context에 현재 state, goals, memory, form test data를 포함.
- 새 snapshot 생성 시 기존 `data-ai-asm-ref`를 제거해 ref 중복 클릭 오류를 방지.
- 행동 분석 CLI가 action별 request/dom/url 변화량을 출력.
- 리뷰 정리:
  - OpenAI LLM 호출에 retry/backoff 추가.
  - `temperature=0.0` 기본값 명시 및 CLI/config 연결.
  - model 기본값을 config 상수로 단일화.
  - form submit 전 scope 검사 및 fetch redirect manual 처리.
  - LLM context를 compact snapshot/memory 형태로 압축.
  - `gpt-5-mini`의 temperature 미지원 400 응답은 temperature 없이 1회 재호출.
  - 로그인/검색 등 form 정보를 `visible_forms`로 구조화해 field ref와 test value를 agent context에 제공.
  - 이미 성공적으로 누른 click ref와 이미 방문한 URL/state를 memory에 유지해 반복 클릭을 줄임.
  - 이미 입력한 field ref를 snapshot에서 제거해 같은 login/search field 반복 입력을 방지.
  - form memory에 `attempted_forms`를 추가해 실패한 로그인/등록 폼을 페이지마다 반복 시도하지 않도록 함.
  - `form_status.partially_filled` / `ready_to_submit`을 context에 추가해 필드 입력 중인 폼과 제출 가능한 폼을 구분.
  - `exploration_status.should_give_up`을 context에 추가해 남은 action이 없을 때 stop을 유도.
  - request delta를 unique URL set 비교에서 append/timestamp 기반 비교로 변경해 같은 API 재호출도 progress로 잡음.
  - agent progress request source를 JS hook fallback이 아니라 CDP capture stream cursor 기반으로 통일.
  - `AgentLoop.before_action` hook을 추가해 LLM 응답 대기 중 발생한 background request가 action delta에 섞이지 않도록 함.
  - progress 판정을 `new_requests` 또는 처음 보는 URL+DOM state 중심으로 변경.
  - tool call에 `reason` 인자를 허용해 trace에서 행동 이유를 확인 가능하게 함.
  - 필수 tool 인자가 비어 있으면 Playwright timeout 전에 `agent_invalid_args`로 즉시 reject.
  - Ant Design 계열 폼처럼 field `name`이 비어 있고 visible text에만 "Email"이 있는 경우도 form data lookup에 사용.
  - 개별 input의 `type`을 form 전체 input type 목록보다 우선해 password field를 정확히 분류.
  - Juice Shop benchmark용 form data 예시 추가: `examples/juice_shop_form_data.yaml`.

검증:
- unit: `198 passed`
- LLM live smoke: `gpt-5-mini`, budget=1, `llm_failure=0`, tool 1/1 성공.
- Juice Shop login-target smoke: `type_ref`로 email/password 입력, `POST /rest/user/login` 및 Authorization 포함 API 관측.
- crAPI login smoke: test 계정 로그인, `POST /identity/api/auth/login`, Authorization 포함 dashboard/vehicle/shop/community API 관측.
- Juice Shop unauth smoke: action 6/6 성공, 새 request 생성 action 2개.
- Juice Shop auth smoke: action 18/18 성공, 새 request 생성 action 6개.

남은 과제:
- pending static candidate를 goal에 직접 주입.
- form submit 전략은 추가 개선 가능: submit candidate 클릭과 `submit_form` 선택 정책을 실사이트 trace로 계속 조정.
- action history를 frontier replay에 연결.
