# HTTP API v1

기본 local 모드는 localhost 단일 사용자입니다. 초대 데모의 session 모드는 로그인 계정의 UUID를 owner로 사용하며 클라이언트가 임의 owner 헤더로 바꿀 수 없습니다. 변경 요청에는 세션의 X-CSRF-Token이 필요합니다. 공개 회원가입은 없습니다. 상세 모드는 [인증 문서](AUTHENTICATION.md)를 참고하세요. 모든 오류는 `{"error":{"code":"...","message":"..."}}`이며 내부 경로·SQL·CSV 값·traceback을 노출하지 않습니다.

| 메서드 / 경로 | 동작 |
|---|---|
| GET /health | API 프로세스 응답. worker 가용성을 뜻하지 않음 |
| GET /ready | 관리자만 DB/migration, Redis, storage, accepting_jobs 체크. 준비 안 되면 503 |
| GET /api/v1/auth/me | 현재 계정·역할·CSRF token·모드. 미로그인은 401 |
| POST /api/v1/auth/login | 초대 계정 JSON username/password → HttpOnly cookie, 계정/CSRF 응답 |
| POST /api/v1/auth/logout | 현재 세션 폐기. CSRF 필수 |
| POST /api/v1/jobs | multipart `file`, optional `options` JSON 문자열, optional `Idempotency-Key`; 영속 등록 후 202 |
| GET /api/v1/jobs | `status`, `q`, `limit` 1–100, `offset` 0–100000. `{items,total,limit,offset}` |
| GET /api/v1/jobs/{uuid} | 상태·실제 행 수·옵션·시각·최대 3개 시도 이력 |
| POST /api/v1/jobs/{uuid}/cancel | 대기/재시도는 즉시 취소, 실행 중은 요청 플래그. 종결 작업은 그대로 반환 |
| GET /api/v1/jobs/{uuid}/events | 최신순, limit 1–200 / offset, 페이지 응답 |
| GET /api/v1/jobs/{uuid}/artifacts | 공개 metadata와 download_url. 내부 storage_key는 제외 |
| GET /api/v1/jobs/{uuid}/artifacts/{type} | input / cleaned / errors / summary. attachment, nosniff, no-store |
| GET /api/v1/stats | DB 상태별 개수·평균 처리시간·실제 heartbeat·큐 관측·최근 복구/실패 |

session 모드에서는 작업과 파일 API가 모두 로그인을 요구하고, 목록·집계·다운로드·취소는 해당 계정 범위입니다. 운영 worker/Redis 정보는 관리자에게만 반환합니다. 로그인 실패/만료는 401, CSRF/관리자 권한 실패는 403이며 다른 계정의 작업은 존재를 구분하지 않는 404입니다.

## 등록과 오류

```json
{
  "required_columns": ["id", "email", "created_at"],
  "required_values": ["id", "email"],
  "trim_whitespace": true,
  "remove_empty_rows": true,
  "normalize_columns": true,
  "duplicate_columns": ["id"],
  "remove_duplicates": true,
  "invalid_rows": "exclude",
  "safe_export": true,
  "column_types": {"id":"integer", "email":"email", "created_at":"date"}
}
```

헤더 정규화는 strip → 소문자 → 연속 공백을 `_`로 치환합니다. 규칙의 컬럼명은 **정규화 후 헤더 이름**이어야 합니다. 정규화 후 중복 헤더, 빈 헤더, 존재하지 않는 규칙 컬럼은 거부합니다. 최대 헤더 수 100, 이름 128자, 셀 16,384자, 물리 행 262,144자, 전체 데이터 100,000행입니다. 데이터 행의 합산 셀 문자 수도 제한합니다. CSV parser는 strict 모드이며 UTF-8 외 인코딩은 지원하지 않습니다.

순서: 헤더 정규화 → 선택적 값 trim → 빈 행 제거 → 행 너비/필수/타입 검사 → 유효 행의 중복 검사 → 정책에 따른 유지/제외 → 내보내기 escape. invalid row는 중복 키를 예약하지 않습니다. `duplicate_columns=[]`이면 전체 행으로 중복을 검사합니다. 최초 유효 행을 유지하며 `remove_duplicates=false`이면 중복을 보고하되 유지합니다. `invalid_rows=keep`은 타입/필수/너비 오류 행을 보존합니다. 필수 값이 아닌 빈 값은 타입 검사를 건너뜁니다. 날짜는 YYYY-MM-DD, 이메일은 기본 구문 검사이며 완전한 RFC 검증이나 주소 존재 검사가 아닙니다.

`errors.csv`는 `row_number,column,error_type,description`을 포함합니다. 번호는 헤더를 1로 하는 **논리 CSV 레코드 번호**이며 여러 물리 줄로 된 quoted field를 한 레코드로 셉니다. 개인정보가 될 수 있는 실제 셀 값은 보고서 오류 설명에 복제하지 않습니다. 원본은 input artifact로 별도 보존합니다.

데이터 오류 행은 작업 실패가 아닙니다. 처리 가능한 CSV라면 오류 행이 있어도 SUCCEEDED와 오류 보고서가 반환됩니다. 손상된 구문/UTF-8, 행/셀/출력 한도 초과는 FAILED로 분류하고 재시도하지 않습니다. API 기본 헤더 검사에서 이미 탐지하면 작업 생성 전에 422입니다. 전체 데이터 검증은 워커에서 수행합니다.

대표 HTTP 코드: 202 등록, 404 작업/파일 미존재 또는 다른 사용자 범위, 409 멱등 키 충돌, 410 보존기간 만료, 413 수신 한도, 422 요청/헤더/규칙 오류, 429 입장/분당 업로드 제한, 503 drain/의존성 오류. 동일 키·동일 hash·동일 정규 옵션은 같은 job_id를 반환합니다. 키를 생략하거나 새 키를 사용하면 동일 파일도 새 작업입니다.

통계의 queue가 null이면 미관측입니다. stream_length는 ACK된 이력도 포함하므로 대기 작업 수로 표시하지 않습니다. heartbeat가 lease 기간보다 오래되면 STALE이며 실제 관측이 없는 워커/처리량/성공률을 만들어 표시하지 않습니다. 프론트엔드는 비종결 상세를 2초 간격으로 조회하고 종결 후 중단합니다. 목록/운영 화면은 5초 폴링, 탭이 백그라운드면 주기 폴링을 중단합니다.
