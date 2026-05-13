# Phase 6 - Schema / OpenAPI / Flagged Output

## 목표
- 누적 DB에서 API endpoint를 OpenAPI 3.0 YAML로 출력한다.
- 스캔 중 JSON response body에서 response schema를 추론해 endpoint에 누적 저장한다.
- 자동화가 차단한 flagged item을 사람이 검토 가능한 포맷으로 출력한다.

## 구현
- `schema/inferrer.py`
  - 여러 JSON response body를 OpenAPI 호환 JSON Schema로 합친다.
  - object property는 union, required는 intersection으로 계산한다.
  - `integer + number`는 `number`로 합치고, 과도한 mixed `oneOf`는 any schema로 축소한다.
- `scan/orchestrator.py`
  - page checkpoint 저장 시 response schema를 endpoint key별로 추론한다.
  - `save_endpoints()`에 schema를 넘겨 기존 endpoint schema와 병합한다.
- `output/openapi.py`
  - DB의 endpoint/parameter/response schema를 OpenAPI 3.0.3 YAML로 변환한다.
  - 같은 path/method가 권한별로 여러 row여도 하나의 operation으로 병합하고 `x-orbis-endpoints`에 provenance를 남긴다.
  - 변환 중 DB model 객체를 mutate하지 않도록 별도 parameter DTO를 사용한다.
- `output/flagged.py`
  - flagged item을 `yaml`, `curl` 형식으로 출력한다.
  - storage_state cookies를 읽어 curl 요청에 주입한다.
  - storage_state를 읽지 못하면 출력 결과에 warning을 남긴다.
- `cli.py`
  - scan 종료 후 `api.yaml`, `flagged.yaml`, `flagged.sh`를 자동 생성한다.
  - 공개 `export` 명령과 flagged export/filter 옵션은 제거했다.

## 제외
- Swagger/OpenAPI 문서 노출 탐지 및 자동 import는 구현하지 않았다.
- Dry-run으로 abort된 mutation의 response schema는 추론하지 않는다.

## 검증
- `uv run pytest -q`
- 결과: `267 passed`
- CLI help 확인:
  - `uv run orbis --help`
  - `uv run orbis scan --help`
