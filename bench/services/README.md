# Local Benchmark Candidate Services

이 디렉터리는 `juice_shop`, `crapi` 외 후보 웹 서비스를 로컬에서 띄우기 위한 Docker Compose 정의를 담는다.

## Start

```bash
docker compose -f bench/services/compose.candidates.yaml up -d
```

## Stop

```bash
docker compose -f bench/services/compose.candidates.yaml down
```

DVWA 데이터베이스까지 초기화하려면:

```bash
docker compose -f bench/services/compose.candidates.yaml down -v
```

## Services

| target | stack | URL | note |
|---|---|---|---|
| `webgoat` | Java/Spring Boot | `http://localhost:8081/WebGoat/` | WebWolf is exposed at `http://localhost:9091/WebWolf/`. |
| `dvwa` | PHP/Apache/MariaDB | `http://localhost:4280/` | First run may require DVWA DB setup in the UI. |
| `dvga` | Python/Flask/GraphQL | `http://localhost:5013/` | GraphQL-focused target. |
| `vampi` | Python/Flask/OpenAPI | `http://localhost:5002/ui/` | API benchmark with documented path-list coverage. |
| `petstore` | Java/OpenAPI | `http://localhost:5014/` | API benchmark with documented path-list coverage. |
| `restful_booker` | Node/Express | `http://localhost:5016/` | Prefixless API-doc limitation benchmark. |
| `go_httpbin` | Go | `http://localhost:5015/` | Prefixless HTTP docs/forms limitation benchmark. |
| `dvna` | Node.js/Express | `http://localhost:9092/` | Smoke only; disabled in `--all` because it is not a useful API coverage benchmark. |
| `webgoat_dotnet` | ASP.NET container | `http://localhost:9000/` | Disabled in `--all`; the old Mono image crashes on local Apple Silicon Docker. |

VAmPI and Petstore have passing path-list coverage. Restful Booker and go-httpbin have path-list fixtures to track the current prefixless API documentation limitation. The remaining smoke targets are registered in `bench/targets.yaml` with `coverage: none` until a stable expected endpoint source is added.
