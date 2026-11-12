# 데이터베이스

## 스키마

| 테이블 | 책임 | 제약/인덱스 |
|---|---|---|
| jobs | 논리 작업·현재 상태·lease·token·영속 재시도 시각 | `(owner_id,idempotency_key)` UNIQUE, status/attempt CHECK, owner+created/status+retry/status+lease 인덱스 |
| job_attempts | 실행별 시작/종료/시간·실패 원인 | job FK, `(job_id,attempt_number)` UNIQUE |
| job_events | 생성·발행·실행·복구·종결 이력 | job FK와 인덱스 |
| job_artifacts | 입력/정제/오류/요약의 크기·SHA-256·저장 키 | job FK, `(job_id,artifact_type)` UNIQUE |
| outbox_events | JOB / DEAD_LETTER 전달 의도와 발행 시각 | job FK, published+created 인덱스 |
| worker_heartbeats | 워커별 마지막 관측·현재 작업 | worker_id PK |

시각은 UTC epoch double로 저장하고 API에서는 ISO-8601로 반환합니다. 소유권 판단은 PostgreSQL `clock_timestamp()`를 사용합니다. SQLite 단위 테스트에서만 호스트 시간을 사용합니다. 토큰과 실행 시도는 작업 행 잠금 안에서 증가합니다.

## 트랜잭션

1. 생성: job + input artifact + JOB_CREATED + Outbox. 멱등 키는 optional이며 NULL이면 같은 파일도 새 작업입니다. 옵션은 Pydantic 기본값을 포함한 정규 JSON 값으로 비교합니다. 옵션 리스트의 순서도 계약의 일부이며 임의 정렬로 의미를 바꾸지 않습니다.
2. 입장 제어: PostgreSQL advisory transaction lock으로 작업 개수와 보수적 저장 공간 예약을 직렬화합니다. 멱등 키 중복은 먼저 확인하여 기존 작업을 돌려주고 DB UNIQUE도 유지합니다. SQLite에서는 UNIQUE만 검증하므로 입장 제한 동시성은 검증되지 않았습니다.
3. 획득: 작업 행 `FOR UPDATE`, QUEUED 여부, 증가 token, 실행 시도, lease, WORKER_STARTED/JOB_RECLAIMED.
4. 갱신/완료/실패: 같은 작업 행 잠금, RUNNING + worker_id + token + 유효 lease 검사. artifacts·SUCCEEDED·실행 종료·JOB_COMPLETED를 하나의 트랜잭션에 확정합니다.
5. 재시도: RETRY_WAIT와 next_retry_at을 기록한 뒤 ACK. dispatcher 재조정이 due 작업을 QUEUED로 돌리며 새 Outbox를 함께 생성합니다. 프로세스 메모리 타이머에만 의존하지 않습니다.
6. 최종 실패: FAILED + 실행 종료 + JOB_FAILED + DEAD_LETTER Outbox를 함께 확정합니다.

메시지 재전달 시 종결 작업은 상태를 바꾸지 않습니다. 현재 소유자가 있는 RUNNING 작업은 pending idle과 관계없이 재획득하지 않습니다. 만료 실행은 RUNNING → RETRY_WAIT를 거쳐 새 시도로 처리합니다.

## 마이그레이션

```powershell
alembic -c backend/alembic.ini upgrade head
alembic -c backend/alembic.ini current
# DB 없이 PostgreSQL DDL 생성 확인
alembic -c backend/alembic.ini upgrade head --sql
```

`0001_initial.py`는 고정된 초기 스키마이며 모델을 import하여 매 실행 시 바뀌는 migration이 아닙니다. API/워커 시작 시 `create_all`, drop/reset을 수행하지 않습니다. `Base.metadata.create_all`은 SQLite 단위 테스트에만 사용합니다. 통합 테스트는 고유 PostgreSQL 스키마에 실제 Alembic을 실행하고 모델과 schema drift를 비교하도록 작성했습니다. 이 통합 테스트는 현재 미실행입니다.

현재 DB 이벤트/Outbox 이력은 자동 삭제하지 않습니다. 운영 규모에서 아카이빙·파티셔닝·보존기간이 필요합니다. 백업 시 DB와 파일 볼륨의 일관된 복구 지점을 관리해야 합니다.
