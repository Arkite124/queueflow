# 가입·IP 제한 운영 반영

이 문서는 저장소 공통 배포 절차다. 실제 서버 경로·연결 IP·비밀번호·Tunnel token·실행 결과는 서버의 Git 제외 경로에만 보관한다.

## 반영할 동작

- 방문자가 아이디·비밀번호만으로 직접 가입하고, 로그인 계정별로 작업과 파일을 조회한다.
- 이메일 수집과 비밀번호 찾기는 제공하지 않는다. 관리자 계정은 기존 CLI로만 생성한다.
- 같은 IPv4 주소는 최대 3개 계정, IPv6는 같은 `/64` 네트워크에서 최대 3개 계정까지 가입한다. 초과 가입은 429이며 기존 계정 로그인은 유지한다.
- 계정 수는 DB에 유지되고 동시 가입도 PostgreSQL에서 직렬화한다. 비활성 계정도 포함한다.
- 내부 예외 목록은 기본 빈 값이다. 내부 주소나 connector 주소를 자동 면제하지 않는다.
- 신규 API 이전에 Pages가 배포되면 신규 가입을 숨기고 기존 로그인을 유지한다. API 배포 후 새로고침해 가입을 확인한다.

## 1. 서버 코드와 기존 운영 확인

서버의 기존 QueueFlow 저장소 루트에서 실행한다. 기존 변경이 있거나 다른 브랜치에서 운영 중이면 먼저 내용을 검토하고 강제 reset하지 않는다.

```powershell
git status --short --branch
git fetch origin
git switch main
git pull --ff-only origin main
git log -1 --oneline

$qfCompose = @('-p', 'queueflow-server', '-f', 'docker-compose.yml', '-f', 'docker-compose.production.yml')
docker compose @qfCompose ps
docker compose @qfCompose images
```

배포 전 DB와 파일의 일관된 시점 백업을 서버의 기존 절차로 확보한다. DB 비밀번호·볼륨 이름을 변경하거나 `down -v`를 실행하지 않는다. 이 업데이트를 위해 기존 포트폴리오 서비스나 Windows 예약 작업을 다시 등록할 필요는 없다.

## 2. Cloudflare 확인

- 기존 QueueFlow Tunnel이 실행 중이며 공개 API hostname이 서버의 `http://127.0.0.1:8100`으로 연결되는지 확인한다. 이는 cloudflared가 서버 Windows 호스트에서 실행되는 구성 기준이다.
- 도메인의 Pseudo IPv4는 Off를 사용한다. Overwrite Headers는 사용하지 않는다.
- Remove visitor IP headers Managed Transform은 끈다. `CF-Connecting-IP`가 전달되어야 한다.
- Tunnel token과 실제 운영 IP를 저장소에 올리지 않는다. 기존 DNS·Tunnel을 삭제하거나 새로 만들지 않는다.

공식 설정 위치와 동작: [Tunnel 경로](https://developers.cloudflare.com/tunnel/get-started/), [Pseudo IPv4](https://developers.cloudflare.com/network/pseudo-ipv4/), [방문자 IP 헤더](https://developers.cloudflare.com/fundamentals/reference/http-headers/).

## 3. 신뢰할 connector 연결 주소 확인

`TRUSTED_PROXY_CIDRS`는 API로 들어오는 실제 TCP peer 주소다. 방문자의 공인 IP, 서버의 공인 IP, Cloudflare 전체 IP 대역을 대신 넣지 않는다. Docker gateway를 추정해 넣지도 않는다.

공개 `/health`를 호출한 직후, API 컨테이너의 활성 TCP 연결을 읽는다. 아래 명령은 설정·데이터를 변경하지 않으며 Uvicorn의 전달 헤더 처리와 무관하게 실제 연결 주소를 확인한다. 현재 API가 IPv4 `0.0.0.0:8100`으로 수신하는 구성 기준이다.

```powershell
curl.exe --fail --silent --show-error https://queueflow-api.youngjoong.dev/health

@'
from ipaddress import IPv4Address
from pathlib import Path
peers = set()
for row in Path('/proc/net/tcp').read_text().splitlines()[1:]:
    fields = row.split()
    if int(fields[1].split(':')[1], 16) == 8100 and fields[3] == '01':
        raw = fields[2].split(':')[0]
        peers.add(str(IPv4Address(bytes.fromhex(raw)[::-1])) + '/32')
print('\n'.join(sorted(peers)) if peers else 'No active API connections; call public health and retry.')
'@ | docker compose @qfCompose exec -T api python -
```

출력이 없으면 공개 health를 다시 호출하고 즉시 재실행한다. 여러 주소가 나오면 공개 요청으로 만들어지는 connector 연결을 확인하고 필요한 주소만 등록한다. 실제 값은 서버에만 보관한다.

## 4. 서버 환경 설정

기존 `.env`에서 아래 항목을 설정한다. `TRUSTED_PROXY_CIDRS`에는 앞에서 확인한 실제 `/32` 값을 입력한다. 여러 신뢰 주소가 반드시 필요한 경우에만 쉼표로 구분한다. IPv6 peer는 `/128`을 사용한다.

```dotenv
PUBLIC_REGISTRATION=true
MAX_ACCOUNTS_PER_IP=3
CLIENT_IP_SOURCE=cloudflare
REGISTRATION_EXEMPT_CIDRS=
```

`TRUSTED_PROXY_CIDRS`는 빈 값으로 배포하지 않는다. 미등록 connector, 누락·중복·잘못된 `CF-Connecting-IP`는 로그인·가입을 503으로 거부한다. 내부 사용자 예외는 나중에 `REGISTRATION_EXEMPT_CIDRS`에 실제 방문자 IP/CIDR을 명시하며, 신뢰할 connector 목록과 혼동하지 않는다. 내부 예외에도 기존 요청 횟수와 동시 계산 제한은 적용된다.

기존 session 인증·Secure cookie·정확한 HTTPS 프런트 origin·DB 비밀번호 등 나머지 운영 설정은 유지한다.

## 5. 이미지와 DB 업데이트

각 명령이 성공한 뒤 다음 명령을 실행한다. API 외 다른 실행 중 서비스는 교체하지 않는다. API 이미지는 `--no-proxy-headers`로 실행되며 방문자 IP 검증은 앱이 담당한다.

```powershell
docker compose @qfCompose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed' }
docker compose @qfCompose build api migrate
if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
docker compose @qfCompose run --rm --no-deps --entrypoint python api -c "from backend.app.core.config import Settings; s=Settings(); assert s.client_ip_source == 'cloudflare' and s.trusted_proxy_cidrs, 'Configure TRUSTED_PROXY_CIDRS first'; print('Public IP configuration validated')"
if ($LASTEXITCODE -ne 0) { throw 'IP configuration validation failed' }
docker compose @qfCompose run --rm migrate
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
docker compose @qfCompose up -d --no-deps --no-build --wait api
if ($LASTEXITCODE -ne 0) { throw 'API update failed' }
docker compose @qfCompose exec -T api python -m scripts.readiness
if ($LASTEXITCODE -ne 0) { throw 'Readiness failed' }
docker compose @qfCompose ps
```

마이그레이션 `0003`은 users 테이블에 nullable 가입 네트워크 컬럼과 인덱스만 추가한다. 기존 사용자·비밀번호·세션·작업을 보존하며, 기존 계정의 가입 IP는 추정하지 않고 NULL로 둔다. CLI로 생성하는 계정도 IP 상한 집계에서 제외된다.

실패 시 기존 볼륨을 보존하고 원인을 먼저 확인한다. 구버전 API 이미지로 되돌려야 하면 배포 전 기록한 이미지를 사용하고, 추가된 nullable 컬럼은 유지한다. 일상 복구 과정에서 DB downgrade나 볼륨 삭제를 실행하지 않는다.

## 6. 예약 운영 스냅샷 갱신

기존 예약 작업이 `output/server-runtime/`의 Compose 복사본을 사용하는지 확인한다. 사용하는 경우 API 검증을 마친 뒤 그 복사본을 최신 설정으로 갱신한다. 그렇지 않으면 다음 날 이전 환경 설정으로 API가 다시 생성될 수 있다.

아래 명령은 이미 설치된 runtime 디렉터리의 Compose 두 파일만 갱신한다. token, cloudflared 실행 파일, 실행 중 supervisor, Windows 작업 정의는 변경하지 않는다.

```powershell
$qfSnapshot = Join-Path (Get-Location) 'output/server-runtime'
if (Test-Path -LiteralPath (Join-Path $qfSnapshot 'scheduled_runtime.py')) {
    Copy-Item -LiteralPath 'docker-compose.yml' -Destination (Join-Path $qfSnapshot 'docker-compose.yml')
    Copy-Item -LiteralPath 'docker-compose.production.yml' -Destination (Join-Path $qfSnapshot 'docker-compose.production.yml')
}
```

## 7. Pages와 공개 HTTPS 검증

Pages의 production branch는 `main`, root는 `frontend`, build는 `npm run build`, output은 `dist`다. 프런트 빌드의 `VITE_API_BASE_URL`은 `https://queueflow-api.youngjoong.dev`다. 실제 배포 commit이 원격 main과 일치하는지 확인한다. Git 연동으로 배포가 시작되지 않으면 기존 프로젝트에서 최신 main을 배포한다.

```powershell
curl.exe --fail --silent --show-error https://queueflow-api.youngjoong.dev/health
curl.exe --fail --silent --show-error https://queueflow-api.youngjoong.dev/api/v1/auth/config
```

health는 ok, auth/config는 public_registration=true와 max_accounts_per_ip=3이어야 한다. config 응답만으로 실제 방문자 IP 검증이 성공했다고 간주하지 않는다.

실제 브라우저에서 다음을 확인한다. 공개 테스트용 계정은 미리 생성한 기존 계정과 구분하고 테스트 결과는 서버의 Git 제외 경로에 보관한다.

1. 기존 계정 로그인과 기존 작업 조회.
2. 신규 계정 가입 → 로그인 → CSV 업로드 → 처리 완료 → CSV·ZIP·Excel 다운로드.
3. 로그아웃·재로그인 후 동일 계정 작업 유지, 다른 계정의 작업·파일 접근 차단.
4. 같은 IP의 비예외 신규 계정 3개까지 허용, 4번째 가입은 429. 기존 등록 계정 수를 먼저 확인하며 상한은 자동 초기화되지 않는다.
5. 별도 외부 네트워크에서도 가입 허용. IPv6는 같은 `/64`면 한도를 공유한다.
6. 다른 IP로 접속해 같은 기존 계정의 로그인이 되는지 확인.
7. 다음 정상 예약 기동에서 최신 Compose 설정과 기존 데이터가 유지되는지 확인.

신규 가입만 임시 중단하려면 `PUBLIC_REGISTRATION=false`로 API를 재생성한다. 기존 계정 로그인을 유지하려면 올바른 trusted proxy 설정도 함께 유지해야 한다.
