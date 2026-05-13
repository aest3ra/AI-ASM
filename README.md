# orbis

Bug bounty용 AI Attack Surface Mapper. 대상 URL을 받아 모든 엔드포인트, 파라미터, 헤더, body를 자동으로 수집하는 도구.

## 현재 상태

**v0.1**: Playwright/CDP 캡처, event-driven static analyzer, SQLite 결과 저장,
OpenAI 기반 browser agent, deterministic local planner, form test data, request/action
trace, OpenAPI/flagged 자동 생성.

## 설치

```bash
uv sync
uv run playwright install chromium
```

## 사용

```bash
# 스캔 + 정규화 + DB 저장
uv run orbis scan https://target.example
```

`--db`를 생략하면 매 스캔마다 새 DB와 같은 이름의 artifact 디렉토리를 만듭니다.

```text
runs/
  orbis-20260509-124501_careers-bancoplata-mx_a13f9c.db
  orbis-20260509-124501_careers-bancoplata-mx_a13f9c/
    orbis-scan-1-....json
    orbis-requests-1.jsonl
    orbis-trace-1.jsonl
    api.yaml
    flagged.yaml
    flagged.sh
```

명시적으로 같은 DB에 누적하려면 `--db`를 지정합니다.

```bash
# 옵션
uv run orbis scan examples/scan_config.yaml \
    --db ./scans/run1.db \
    --no-headless          # 브라우저 창을 띄워서 동작 관찰

# 단일 endpoint 상세 확인 (DB만 읽음, 재크롤 없음)
uv run orbis inspect ./scans/run1.db <endpoint_id>
```

`orbis-scan-{id}-{ts}.json`, `orbis-requests-{id}.jsonl`, `orbis-trace-{id}.jsonl` 에
raw capture, 안전 redacted request log, agent trace가 저장됩니다.
`api.yaml`, `flagged.yaml`, `flagged.sh`는 scan 종료 후 자동 생성됩니다.

## 로그인 세션

```bash
uv run orbis login https://target.example -o auth.json
uv run orbis scan https://target.example --auth auth.json
```

권한별 계정은 storage state를 따로 만들고 여러 번 scan한 뒤 DB를 누적해서 본다.

## 구조

```
src/orbis/
├── cli.py                  scan / login / inspect
├── agent/                  LLM client, loop, tools, local planner, trace analysis
├── analyzer/               event-driven static analyzer dispatcher/extractors
├── config.py               YAML config 로더 + Pydantic 검증
├── crawler/
│   ├── browser.py          per-page Playwright + CDP 캡처
│   ├── frontier.py         URL + DOM signature frontier state
│   ├── runner.py           Crawler — BFS frontier, rate limit, template cap
│   ├── scope.py            도메인/경로 화이트리스트 필터
│   └── types.py            CapturedRequest, PageDiagnostics, ScanDiagnostics
├── output/                 console/artifact 출력
├── scan/                   CLI 밖 orchestration
├── normalizer/
│   ├── url.py              path templatize (id/uuid/hash/date/n/slug)
│   ├── params.py           query/header/body/cookie 추출 + 타입 추론
│   └── pipeline.py         raw → endpoint 그룹핑
├── storage/
│   ├── db.py               SQLite 스키마
│   └── repo.py             save_endpoints
└── surface/                URL surface 분류
```

## Config 예시

URL 하나로 실행할 수 있고, 반복 재사용하거나 세부 설정이 필요하면
`examples/scan_config.yaml`, `examples/catholic.yaml` 같은 config 파일을 넘긴다.
핵심 필드:

| 키 | 설명 |
|---|---|
| `target` | 스캔 시작 URL |
| `scope.include_domains` | **옵션** — 비우면 target URL 호스트 하나로 자동 제한 (strict-host). 서브도메인까지 보려면 `*.example.com` 명시 |
| `scope.exclude_paths` | 경로 prefix로 제외 (`/logout` 등) |
| `limits.max_pages` | 최대 크롤 페이지 수 |
| `limits.max_duration_sec` | 최대 스캔 시간 |
| `limits.rate_limit_rps` | 초당 요청 수 |
| `limits.max_visits_per_template` | 같은 path_template 최대 방문 횟수 (default 3, 게시판 폭증 방지) |
| `auth.type` | `none` 또는 `storage_state` |
| `auth.storage_state_path` | Playwright `storage_state` JSON 경로 |
| `static_probe_auth` | static GET probe 인증 모드. 기본 `cookie-only`, 옵션 `none`/`learned` |
| `agent.mode` | `planner`, `hybrid`, `mock`, 또는 `llm`. 기본 `planner` |
| `agent.model` | OpenAI model 이름 |
| `agent.max_steps_per_page` | 페이지당 agent tool step cap |
| `agent.form_data_path` | form test data YAML 경로 |

인증 파일은 CLI에서 일회성으로 바꿀 수 있다.

```bash
uv run orbis scan https://target.example --auth auth.json
```

## 테스트

```bash
uv run pytest
```
