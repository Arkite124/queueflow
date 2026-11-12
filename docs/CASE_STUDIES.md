# 포트폴리오 기술 사례

## A. 긴 HTTP 요청과 백그라운드 작업 분리

**문제:** 파일 처리 전체를 요청 안에서 실행하면 브라우저 종료와 네트워크 시간 제한이 작업 실행 수명과 결합됩니다. 원인은 요청/계산/결과 저장을 한 프로세스 호출에 묶는 구조입니다.

**대안:** 동기 처리, FastAPI BackgroundTasks, 외부 task framework, 직접 Redis Streams를 검토할 수 있습니다. 요청 응답만 빠르게 만드는 메모리 background task는 프로세스 장애 후 재시도를 영속 보장하지 않습니다. 이 프로젝트는 전달/회수 원리를 드러내기 위해 Redis Streams와 DB 상태 모델을 선택했고 Celery는 사용하지 않았습니다.

**구현/재현:** `main.py`는 입력 저장·헤더 검사·DB 등록 후 202를 반환합니다. 행 검증은 `process_csv`에서 수행합니다. `tests/unit/test_api_pipeline.py`의 실제 파일 업로드→claim→처리→다운로드는 PASS입니다. PostgreSQL/Redis를 경유한 통합 경로는 코드가 있지만 미실행입니다.

**한계:** 업로드 자체의 시간과 기본 헤더 decode는 요청에 포함됩니다. 작업 완료까지의 지연은 별도 지표이며 202를 성공 처리량으로 세면 안 됩니다.

## B. Worker가 처리 중 사라지는 문제

**문제/원인:** Redis pending에는 워커 장애와 오래 걸리는 정상 실행이 모두 나타납니다. idle만 보고 재실행하면 아직 살아 있는 워커와 결과가 충돌합니다.

**대안/선택:** 무조건 pending 회수, Redis 분산 잠금, PostgreSQL 실행 소유권을 비교해 DB lease+heartbeat+fencing을 선택했습니다. `XAUTOCLAIM`은 메시지 전달 회수에만 사용하고 실행 권한은 DB에서 판단합니다.

**구현/재현:** 별도 heartbeat thread가 lease를 갱신합니다. 만료 작업은 RETRY_WAIT로 전환하고 due 시점에 새 Outbox를 만듭니다. `test_stale_worker_cannot_commit_or_renew`, `test_heartbeat_prevents_false_recovery`는 SQLite에서 PASS입니다. 실제 subprocess SIGKILL/컨테이너 종료/정상 SIGTERM 테스트는 작성했지만 미실행입니다.

**한계:** 복구는 즉시가 아니고 lease/backoff/reclaim 시간이 듭니다. shared file volume이나 DB 자체가 유실되면 lease만으로 입력을 되살릴 수 없습니다.

## C. 중복 메시지와 두 번 반영되는 결과

**문제/원인:** 결과 파일 저장 후 프로세스가 죽으면 다음 실행은 같은 작업을 다시 처리합니다. DB COMMIT 후 ACK 전 장애도 재전달을 만듭니다. 메시지 exactly-once를 가정하면 이미 완료한 작업을 덮어쓸 수 있습니다.

**대안/선택:** Redis 메시지 ID만 중복 key로 쓰는 방식은 재발행 ID 변경을 포착하지 못합니다. 전역 고정 출력 경로는 stale worker 덮어쓰기를 허용합니다. 논리 job ID의 종결 상태, 증가 token, 시도별 경로와 artifact UNIQUE를 함께 사용했습니다.

**구현/재현:** 유효 lease/token으로만 artifacts+SUCCEEDED를 확정합니다. `test_completed_job_never_runs_again`과 오래된 token 차단은 PASS입니다. 파일 저장 후 os._exit / COMMIT 후 os._exit 및 실제 Redis 중복 전달 테스트는 NOT RUN입니다.

**한계:** 재계산과 고아 파일은 발생할 수 있습니다. 보장은 논리 결과 참조의 중복 차단이며 모든 외부 부수 효과를 exactly-once로 만드는 범용 작업 실행기는 아닙니다.

## D. PostgreSQL과 Redis의 이중 쓰기 불일치

**문제/원인:** DB INSERT 성공 직후 Redis 발행이 실패하면 작업이 영원히 대기합니다. 반대로 Redis 발행 후 DB 확인 실패는 중복 발행을 만듭니다.

**대안/선택:** Redis부터 발행하는 순서 변경도 반대 방향의 유실을 만듭니다. 주기 스캔만 사용하는 방식보다 정상 경로의 의도를 명확히 보존하도록 Transactional Outbox를 선택하고 재조정을 보완책으로 사용했습니다.

**구현/재현:** 생성과 Outbox를 한 트랜잭션에 기록하고, dispatcher가 미발행 row를 잠급니다. Redis 데이터 유실은 오래 대기한 QUEUED를 재발행합니다. DB 재발행 예약 단위 테스트는 PASS이며 실제 Redis 삭제/중단과 XADD 후 rollback 테스트는 미실행입니다.

**추가 발견:** API COMMIT 응답이 유실될 때 파일을 지우면 이미 등록한 작업의 입력을 삭제할 수 있습니다. 모호한 인프라 실패에는 파일을 보존하도록 수정했고, COMMIT 완료 후 연결 오류를 주입해 입력과 Outbox 보존·멱등 재조회가 PASS했습니다. 이는 실제 DB 네트워크 단절 테스트와 구분합니다.

**한계:** Outbox는 중복 발행을 제거하지 않습니다. Redis stream/DB 이벤트의 장기 retention과 중복 발행량 관측은 개선 과제입니다.

## E. 스트리밍과 중복 key 메모리

**문제/원인:** 전체 CSV와 중복 key를 무제한 메모리에 보관하면 워커 수를 늘릴 때 메모리 사용도 함께 커집니다.

**대안/선택:** 전체 DataFrame 대신 csv iterator와 bounded physical line/field/row를 사용했습니다. 현재 최대 100,000행에서는 SHA-256 digest set을 선택했고 더 큰 범위는 지원하지 않습니다. 출력 파일도 시도당 총 64 MiB로 제한합니다.

**구현/재현:** 고정 seed 합성 데이터를 새 프로세스로 3회씩 측정했습니다. 100,000행 평균 1.7085초, 최대 peak RSS 67.7 MiB를 관측했습니다. [벤치마크 원본과 환경](PERFORMANCE_TESTING.md)에 구분해 기록했습니다.

**한계:** 이 수치는 CSV 처리기 단독 결과입니다. worker 1/2/4개, 실제 queue 대기·DB 부하·복구 시간은 미측정이며 워커 수가 늘면 빨라진다는 결론을 내리지 않았습니다. 표본 P95 3회도 운영 지연 분포를 대표하지 않습니다.
