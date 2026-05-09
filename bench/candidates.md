# Benchmark Target Candidates

1차 coverage 벤치마크는 `juice_shop`, `crapi`, `vampi`, `petstore`로 고정한다. 추가 후보는
`bench/services/compose.candidates.yaml`과 `bench/targets.yaml`에 smoke benchmark로 등록되어 있다.
VAmPI/Petstore는 path-list coverage가 있고, Restful Booker/go-httpbin은 prefixless API 문서 한계 추적용 coverage fixture가 있다.

- 로컬 Docker 실행이 안정적이어야 한다.
- health check URL이 있어야 한다.
- coverage 기준이 있어야 한다. OpenAPI, route source, controller scan, fixture path list 중 하나.
- 특정 앱 문자열에 과적합하지 않는 개선점만 반영한다.

| 후보 | 스택 | 이유 | 실행/출처 |
|---|---|---|---|
| 후보 | 스택 | 이유 | 로컬 URL | 출처 |
|---|---|---|---|---|
| OWASP WebGoat | Java/Spring Boot, server-rendered lesson UI | 로그인/세션, 다단계 lesson UI, 서버 렌더링 흐름 검증 | `http://localhost:8081/WebGoat/` | [GitHub](https://github.com/WebGoat/WebGoat), [Docker Hub](https://hub.docker.com/r/webgoat/webgoat/) |
| DVWA | PHP/Apache/MariaDB, classic MPA | 전통 HTML form, PHP route, session 기반 탐색 검증 | `http://localhost:4280/` | [GitHub](https://github.com/digininja/DVWA), [GHCR](https://github.com/digininja/DVWA/pkgs/container/dvwa) |
| DVGA | Python/Flask + GraphQL | GraphQL endpoint capture/export, GraphiQL/introspection 관련 흐름 검증 | `http://localhost:5013/` | [GitHub](https://github.com/dolevf/Damn-Vulnerable-GraphQL-Application) |
| VAmPI | Python/Flask + OpenAPI | OpenAPI 문서 import + REST API coverage 검증 | `http://localhost:5002/ui/` | [GitHub](https://github.com/erev0s/VAmPI), [Docker Hub](https://hub.docker.com/r/erev0s/vampi) |
| Swagger Petstore | Java + OpenAPI | OpenAPI 문서 import + API path coverage 검증 | `http://localhost:5014/` | [Docker Hub](https://hub.docker.com/r/swaggerapi/petstore3) |
| Restful Booker | Node/Express | prefixless API docs에서 endpoint를 찾는지 검증 | `http://localhost:5016/` | [Docker Hub](https://hub.docker.com/r/mwinteringham/restfulbooker) |
| go-httpbin | Go | prefixless HTTP docs/form submit 탐색 검증 | `http://localhost:5015/` | [GitHub Container Registry](https://github.com/mccutchen/go-httpbin/pkgs/container/go-httpbin) |
| Damn Vulnerable NodeJS App | Node.js/Express | Express route/static analyzer와 MPA/SPA 혼합 탐색 검증 | `http://localhost:9092/` | [GitHub](https://github.com/appsecco/dvna), [Docker Hub](https://hub.docker.com/r/appsecco/dvna) |
| WebGoat.NET container | ASP.NET 컨테이너 | 비 Python/Node/Java/PHP 계열 서버 동작 다양성 확보 | `http://localhost:9000/` | [GitHub](https://github.com/appsecco/owasp-webgoat-dot-net-docker), local Apple Silicon에서 Mono crash 확인 |

권장 순서:

1. `webgoat`: Java/Spring + login/session + lesson navigation
2. `dvwa`: PHP/Apache + classic forms
3. `dvga`: GraphQL
4. `vampi`: OpenAPI import + REST API path coverage
5. `petstore`: Java OpenAPI import coverage
6. `restful_booker` / `go_httpbin`: prefixless API docs 한계 추적
7. `dvna` 또는 NodeGoat 계열: Node/Express route 추출 검증

다음 작업:

1. 각 후보의 Docker health check를 실제 확인한다.
2. smoke scan을 돌려 scanner crash 여부를 본다.
3. OpenAPI, controller route extraction, path list 중 가능한 coverage 기준을 붙인다.
4. 기준이 안정화된 후보만 coverage threshold를 부여한다.
