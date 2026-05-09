# Current Changes - Planner-first ASM v2

## 목표
- LLM 의존도를 낮추고 deterministic planner를 기본 실행 경로로 만든다.
- URL surface와 API endpoint를 분리해 출력한다.
- SPA/Framer류 사이트에서 외부 passive dependency 차단 때문에 hydration이 깨지는 문제를 줄인다.
- file input이 있는 form도 안전하게 탐색하되, mutating request는 서버에 보내지 않고 캡처 후 abort한다.

## 주요 변경
- Agent 실행 경로를 `run_page()` 중심으로 통일했다.
  - `planner` 모드를 기본값으로 사용한다.
  - `mock`, `llm`도 같은 action trace / network delta 경로를 탄다.
- `NetworkEventBuffer` cursor 기반 delta를 사용한다.
  - 같은 URL로 반복 호출되는 API도 action별 request delta로 잡힌다.
- deterministic planner를 강화했다.
  - form field 입력, select 선택, submit 후보 클릭을 LLM 없이 수행한다.
  - 이미 입력한 field는 다음 snapshot에서 제거해 중복 입력을 줄인다.
  - typed form에 남은 field가 있으면 submit 전에 계속 채운다.
  - typed form의 남은 field와 submit 후보가 같이 있으면 같은 batch로 처리한다.
- form test data를 개선했다.
  - `type=text`라도 name/text가 `email`, `phone`, `linkedin/url`이면 적절한 기본값을 사용한다.
- agent snapshot을 개선했다.
  - viewport 아래 form/control도 snapshot에 포함한다.
  - form 관련 요소를 먼저 refs에 배치해 max refs에 밀리지 않게 했다.
- safe dry-run submit을 추가했다.
  - click/submit 중 `POST`, `PUT`, `PATCH`, `DELETE` 요청은 캡처 후 abort한다.
  - file input에는 placeholder 파일을 자동 첨부한다.
  - abort된 mutation은 tool result/trace에 `aborted_mutations`로 남긴다.
- trace/log용 tool argument redaction을 `safety.safe_tool_arguments()`로 통합했다.
- scope route 정책을 조정했다.
  - out-of-scope active navigation/fetch는 차단한다.
  - script/css/font/image 같은 passive dependency는 hydration을 위해 허용한다.
- API 분류를 확장했다.
  - `/api-internal`, `/api-*`, `/api_*` 계열을 API marker로 인식한다.
- URL surface 출력이 추가됐다.
  - API endpoint, page route, action route, asset/file 등을 분리 집계한다.
- Phase 6 출력 계층이 추가됐다.
  - JSON response body 기반 response schema를 endpoint에 누적 저장한다.
  - `ai-asm export`로 OpenAPI 3.0 YAML을 생성한다.
  - `ai-asm flagged`로 flagged item을 `yaml`, `curl`, `http`, `postman` 형식으로 export한다.
  - schema 병합은 `integer + number`를 `number`로 합치고, 과도한 mixed `oneOf`를 축소한다.
  - storage_state cookie를 읽지 못하면 flagged export에 warning을 남긴다.
- 기본 scan 저장 정책을 변경했다.
  - `--db`가 없으면 매 실행마다 `runs/<timestamp>_<host-slug>_<urlhash6>.db`를 새로 만든다.
  - `--out`이 없으면 같은 stem의 artifact 디렉토리를 사용한다.
  - 확장자 없는 `--db` 경로는 artifact 충돌을 피하려고 `<db>-artifacts`를 사용한다.
  - 누적 저장이 필요할 때만 `--db`를 명시한다.
- `scan` 종료 후 사용자 결과물을 자동 생성한다.
  - `api.yaml`
  - `flagged.yaml`
  - `flagged.sh`
  - `export` / `flagged` 명령은 재생성·필터링용으로 유지한다.
- Swagger/OpenAPI 노출 탐지는 고려 대상에서 제외했다.

## 검증
- 전체 테스트:
  - `uv run pytest -q`
  - 결과: `272 passed`
- 자동 DB 경로 smoke:
  - `--db/--out` 없이 `/tmp`에서 실제 URL 스캔
  - 생성 예: `runs/20260509-125418_careers-bancoplata-mx_7bff30.db`
- 자동 export smoke:
  - scan 종료 후 `api.yaml`, `flagged.yaml`, `flagged.sh` 자동 생성 확인
- Bancoplata careers smoke:
  - 대상: `https://careers.bancoplata.mx/`
  - 결과 endpoint:
    - `GET /api-internal/jobs`
    - `GET /api-internal/jobs/{id}`
    - `POST /api-internal/apply-job`
  - `POST /api-internal/apply-job?jobid=5148265008`는 trace에서 `aborted: true`로 캡처됨.

## 관찰
- planner만으로 below-the-fold 신청 form 탐색과 submit request 캡처가 가능해졌다.
- file upload form을 완전히 막는 방식보다 dry-run abort 방식이 더 실용적이다.
- 다만 실제 POST는 서버에 전달되지 않으므로 response schema 추론은 불가능하다.
- form submit 통계는 dry-run 정책과 맞게 `danger/blocked` 중심으로 정리했다.
- 코드 리뷰 후 dry-run mutation abort와 placeholder file attach 단위 테스트를 추가했다.
- Phase 6 이후 OpenAPI/flagged exporter 단위 테스트와 CLI smoke 테스트를 추가했다.

## 남은 과제
- dry-run으로 캡처한 mutation endpoint를 export/flagged 관점에서 더 명확히 표시할지 결정 필요.
- URL surface benchmark expected set을 사이트별로 더 늘려야 한다.
- planner rule은 계속 단순하게 유지하고, LLM은 fallback/experimental 성격으로 두는 방향이 현재 코드와 가장 잘 맞는다.
