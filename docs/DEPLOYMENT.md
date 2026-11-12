# 운영 배포

프로젝트 루트에서 Docker Desktop Linux engine과 Compose를 사용한다. PostgreSQL이 최종 상태의 기준이며 named volume은 업데이트 때 보존한다.

## 운영 설정

비밀 환경 파일 `.env`는 서버에서만 보관한다. 신규 DB 비밀번호는 난수로 생성하고 기존 DB의 비밀번호를 환경 파일만 수정해 변경하지 않는다.
공개 배포에는 session 인증, Secure cookie, 정확한 HTTPS 프런트 origin이 필요하다. 프런트 빌드의 `VITE_API_BASE_URL`은 공개 API 주소다.

```powershell
$qfCompose = @('-p', 'queueflow-server', '-f', 'docker-compose.yml', '-f', 'docker-compose.production.yml')
docker compose @qfCompose config --quiet
docker compose @qfCompose up -d --build --scale worker=1
docker compose @qfCompose exec -T api python -m scripts.readiness
```

계정은 `scripts.manage_users`로 만들고 비밀번호는 터미널의 숨김 입력을 사용한다. 자세한 인증 정책은 [AUTHENTICATION.md](AUTHENTICATION.md)를 참고한다.

## 프런트와 API 업데이트

개발은 develop에서 진행하고 승인된 변경을 main으로 반영한다.
Pages의 Git 연동은 프런트 배포를 담당한다. API는 별도 Docker 이미지 빌드·컨테이너 교체가 필요하다.
예약 기동은 이미 빌드된 이미지로 실행하며 자동 Git pull·build를 수행하지 않는다.

```powershell
docker compose @qfCompose build api
docker compose @qfCompose up -d --no-deps --no-build --wait api
```

운영 반영은 승인된 범위에서 실행한다. 기존 포트·서비스·볼륨을 확인하고 프런트/API 버전을 함께 검증한다.
인증, CSV 처리, 개별 파일과 ZIP/Excel 다운로드, 사용자별 접근 차단을 실제 HTTPS에서 확인한다.

## 예약 운영

`scripts/install_schedule.ps1`은 기본 미리보기이며 `-Apply`는 준비된 로컬 런타임을 사용하는 QueueFlow 전용 예약 작업을 등록한다.
07:00 시작, 23:57 drain·정리, 사용자 로그인 시 시작을 지원한다. Docker Desktop 방식은 자동 Windows 로그인이 전제다.
실행 파일·Compose 스냅샷·제한된 ACL의 토큰 파일·작업 영수증은 `output/server-runtime/`에만 둔다.
이 운영 디렉터리는 일반 테스트 결과 정리와 함께 삭제하지 않는다. PC별 설치 기록은 커밋하지 않는다.

스케줄러는 프로젝트 컨테이너와 자신이 만든 Tunnel만 관리한다. 기존 PC 종료·부팅 설정과 다른 서비스는 별도 관리한다.
실제 전원 OFF/ON 주기는 설정 등록만으로 검증됐다고 간주하지 않는다.

## 종료와 보존

drain 후 실행 중 작업이 끝난 것을 확인하고 정지한다. 다음 기동 후 resume한다.
데이터베이스와 파일의 일관된 시점 백업을 준비하고 `down -v`를 일반 운영 절차에 넣지 않는다.
호스트 경로·호스트명·검증 로그·원본 자원 측정·개인 인계 문서는 Git 제외 경로에 보관한다.
공통 [검증 방법](TESTING.md)과 [성능 측정 방법](PERFORMANCE_TESTING.md)을 참고한다.
