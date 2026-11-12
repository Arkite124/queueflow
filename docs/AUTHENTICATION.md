# 초대 계정 데모 인증

현재 일반 회원가입은 제공하지 않습니다. 관리자가 서버 CLI에서 계정을 만든 뒤 별도 경로로 초대한 사람에게 전달합니다. 사용자 작업/파일은 DB user UUID에 귀속되며, client가 owner 헤더나 폼 필드로 다른 사용자를 선택할 수 없습니다. 기존 `local` 소유 작업은 새 사용자에게 자동 이전하지 않습니다.

## 실행 모드

| 모드 | 설정 | 용도 |
|---|---|---|
| local | DEPLOYMENT_MODE=local, AUTH_MODE=local | localhost 단일 사용자, 기존 개발 테스트 |
| 로그인 개발 | DEPLOYMENT_MODE=local, AUTH_MODE=session, COOKIE_SECURE=false | localhost에서 초대 계정 흐름 검증 |
| 공개 초대 데모 | DEPLOYMENT_MODE=public, AUTH_MODE=session, COOKIE_SECURE=true, 정확한 HTTPS CORS origin | 검증을 마친 별도 운영 환경 |

public 모드에서 세션 인증/Secure cookie/정확한 HTTPS origin이 빠지면 앱 설정이 실패합니다. `docker-compose.production.yml`의 API 설정이 이를 강제합니다. 이 파일은 실제 운영 서버에 아직 적용하지 않았습니다.

## 계정 만들기와 폐기

스키마를 `0002`까지 적용한 QueueFlow 컨테이너에서 실행합니다. 비밀번호는 터미널의 숨김 입력으로 받고 인자·Git·로그에 쓰지 않습니다. 기본 계정/공통 운영 비밀번호를 자동 생성하지 않습니다.

```powershell
docker compose exec api python -m scripts.manage_users create owner --admin
docker compose exec api python -m scripts.manage_users create invited-user
docker compose exec api python -m scripts.manage_users reset-password invited-user
docker compose exec api python -m scripts.manage_users disable invited-user
```

비밀번호 변경/비활성화 시 해당 계정의 모든 세션을 폐기합니다. username은 3–64자의 소문자/숫자/점/하이픈/밑줄이며, 비밀번호는 12–128자입니다. 관리자 여부는 신규 계정 생성 때만 지정합니다. 관리자도 다른 사용자의 작업/파일을 우회 조회하는 API는 제공하지 않습니다.

## 세션·CSRF·실패 제한

- Python 표준 `hashlib.scrypt`의 N=32768,r=8,p=3과 독립 16바이트 salt로 비밀번호를 저장합니다. 비교는 상수 시간 함수를 사용합니다.
- 세션은 암호학적 난수 32바이트의 opaque token입니다. DB에는 SHA-256 digest만 보관하고 원문 token을 응답 JSON/localStorage에 저장하지 않습니다.
- HttpOnly, host-only, SameSite=Lax cookie를 사용합니다. public 모드에서는 Secure와 `__Host-` 이름 prefix가 필수입니다. 기본 만료 8시간, 최대 설정 24시간, 계정당 활성 세션 최대 5개입니다.
- 로그인은 JSON과 허용 Origin을 검사합니다. 변경 API는 세션별 CSRF token과 Origin을 모두 검사합니다. 프론트는 token을 메모리에만 보관하며 세션 종료 시 계정별 query cache를 지웁니다.
- 계정별 5회 연속 실패 후 5분 잠금은 DB에 기록되어 API 재시작 후에도 유지됩니다. 추가로 프로세스/IP 기준 분당 10회 제한과 동시 비밀번호 계산 2개 제한이 있습니다. 이 보조 IP 제한은 proxy 뒤에서 여러 사용자를 묶을 수 있고 다중 API 인스턴스를 통합하지 않습니다.
- 작업/이벤트/입력·결과 파일/취소/집계는 로그인 소유자 범위로 조회합니다. `/ready`와 worker/Redis 운영 지표는 관리자만 조회합니다. `/health`는 프로세스 확인용 공개 endpoint입니다.
- 컨테이너 healthcheck는 인증 HTTP를 우회하는 공개 endpoint를 만들지 않고, 내부 CLI `scripts.readiness`로 DB/Redis/storage/migration을 검사합니다.

## 프론트엔드와 API 도메인

공개 데모는 `queueflow.youngjoong.dev`와 `queueflow-api.youngjoong.dev`처럼 같은 site의 HTTPS 도메인을 전제로 합니다. CORS_ORIGINS에는 프론트의 정확한 origin을 넣고 fetch는 credentials를 포함합니다. `pages.dev`와 `youngjoong.dev`는 서로 다른 site이므로 SameSite=Lax 로그인 API를 그대로 연결하면 cookie가 전송되지 않을 수 있습니다. 공개 로그인 전에 프론트 custom domain을 연결하고 실제 브라우저에서 확인해야 합니다. 이를 해결하려고 cookie 보안을 자동으로 낮추지 않았습니다.

## 확인한 범위

SQLite + HTTPS TestClient에서 세션 cookie 속성·만료·폐기·계정 분리·멱등 키 범위·파일 접근·CSRF/Origin·관리자 범위·실패 잠금·public 설정 오류를 검증했습니다. 재부팅 후 실제 PostgreSQL/Redis 전체 테스트 54개와 격리 Compose 시나리오 8개를 통과했고, 로컬 Chrome에서 로그인·CSV 업로드·다운로드·계정 전환·세션 만료를 확인했습니다. 공개 HTTPS 도메인의 cookie·CORS·CSRF는 서버 배포 단계에서 확인합니다. 근거는 [홈 PC 검증 기록](TESTING.md), 이어갈 명령은 [서버 PC 인계 문서](DEPLOYMENT.md)에 있습니다.

설계 참고: [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html), [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [Starlette response cookies](https://www.starlette.io/responses/).
