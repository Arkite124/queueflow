# Windows 로컬 운영과 Cloudflare 배포 준비

## 로컬 전제와 충돌

홈 PC는 Windows 11이며 Docker Desktop을 D드라이브에 설치하고 재부팅 후 실제 Linux 컨테이너에서 전체 테스트와 복구·인증·자원 검증을 마쳤습니다. 서버 PC에서 이어갈 명령과 설정은 [서버 PC 인계 문서](DEPLOYMENT.md), 실측 근거는 [최신 배포 조건](TESTING.md)에 있습니다. 아래 기본 Compose 명령은 localhost 개발용이며 서버 배포는 인계 문서의 production override를 함께 사용합니다.

홈 PC checkout은 `<workspace-root>\queueflow`, 저장소는 `Arkite124/queueflow`입니다. 초기 구현은 main의 `96400b2`, 인증과 배포 검증을 포함한 후속 구현은 `feat/release-readiness`의 `bfe6764`와 그 이후 인계 문서 커밋에 있습니다.

```powershell
Set-Location <workspace-root>\queueflow
Copy-Item .env.example .env
docker compose config
docker compose up -d --build --scale worker=3
docker compose ps
docker compose logs --tail 100 api dispatcher worker
```

Compose는 PostgreSQL/Redis healthcheck → storage-init와 Alembic migrate 완료 → API/dispatcher/worker 순서로 기동합니다. app 프로세스는 UID 10001로 실행하며 storage-init은 새 files 볼륨의 **루트 디렉터리** 권한만 초기화합니다. PostgreSQL·Redis·files는 프로젝트별 named volume에 보존합니다. worker에 고정 container_name이나 host 포트가 없으므로 scale이 가능합니다. frontend Docker 서비스는 `ui` profile로 선택합니다.

API 포트를 바꾸면 `.env`의 API_PORT, frontend VITE_API_BASE_URL을 함께 바꿉니다. frontend origin을 바꾸면 CORS_ORIGINS도 명시합니다. `VITE_` 값은 빌드 결과에 공개되므로 토큰/비밀번호를 넣지 마세요. PostgreSQL 비밀번호 예시는 단순 문자만 사용합니다. 특수 문자를 사용할 때 DATABASE_URL의 URL 인코딩과 Compose 설정을 함께 검토하세요.

## 내구성과 종료

Redis AOF `appendfsync everysec`로 최근 약 1초 쓰기가 전원 장애에 유실될 수 있습니다. PostgreSQL의 QUEUED 재조정으로 재발행할 수 있지만 DB와 파일 볼륨이 함께 남아 있어야 합니다. Redis persistence가 작업 성공의 기준은 아닙니다.

파일은 flush/fsync 후 DB에 연결합니다. Linux에서는 파일 디렉터리도 fsync하지만 실제 Docker Desktop/호스트 스토리지의 전원 장애 내구성은 검증하지 않았습니다. DB와 files 볼륨의 백업/복구 지점은 운영자가 함께 관리해야 합니다. `docker compose down -v`는 이 문서의 운영 명령이 아닙니다.

23:57 정리 연결 지점:

```powershell
docker compose exec api python -m scripts.maintenance drain
# /api/v1/stats와 작업 상세에서 RUNNING이 종료되는지 확인
docker compose stop --timeout 180
```

07:00 재기동 연결 지점:

```powershell
docker compose up -d
docker compose exec api python -m scripts.maintenance resume
```

이 명령은 QueueFlow 전용 스택에서만 실행하세요. 기존 Windows 스케줄러/BIOS/기존 AI 서버의 스케줄은 설치하거나 변경하지 않습니다. drain 플래그는 파일 볼륨에 남습니다. 강제 종료된 작업은 lease·영속 재시도·재발행으로 복구하도록 구현했으며 실서비스 검증은 필요합니다.

## Cloudflare Pages 준비만 수행

| 설정 | 예정값 |
|---|---|
| Project | queueflow |
| Production branch | main |
| Root directory | frontend |
| Build command | npm run build |
| Build output | dist |
| Frontend | https://queueflow.youngjoong.dev |
| API | https://queueflow-api.youngjoong.dev |
| Production VITE_API_BASE_URL | https://queueflow-api.youngjoong.dev |

`frontend/public/_redirects`가 `/* /index.html 200`을 포함하므로 Pages 배포 시 클라이언트 라우트 새로고침을 준비합니다. 로컬 Vite의 `/new` 직접 요청은 HTTP 200을 확인했지만 실제 Pages 라우팅은 미검증입니다. Pages는 정적 React만 호스팅하며 FastAPI/PostgreSQL/Redis/worker는 별도 Windows 서버의 Docker Compose에서 실행합니다.

홈 PC에서 실제 PostgreSQL/Redis 테스트 54개, 전체 스택 복구 시나리오 8개, 초대 인증과 Chrome 업로드·다운로드 검증을 통과했습니다. worker 1/2/4개의 CPU·RAM을 측정하고 worker 1개로 운영 후보 자원 한도를 확인했습니다. 후속 배포에서는 QueueFlow API용 Tunnel ingress와 두 custom domain을 연결한 뒤 실제 HTTPS cookie·CORS·CSRF와 파일 다운로드를 확인합니다. 프론트 Pages는 사용자 승인으로 등록했고 최초 배포 성공을 화면에서 확인했습니다. 구체적인 근거는 [홈 PC 검증 기록](TESTING.md)에 있습니다.

초기 main push와 Pages 등록은 사용자 승인 이후 진행했습니다. 새 변경의 main 병합/운영 배포, DNS 변경, 기존 Tunnel 변경, 운영 서버 Compose 적용/재시작은 별도 승인 범위입니다. 이번 후속 작업에서는 기존 운영 서버에 적용하거나 재시작하지 않았습니다.
