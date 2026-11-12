# 보안과 보존 정책

## 현재 신뢰 경계

기본 모드는 **로컬 단일 사용자 개발용**입니다. API와 선택적 정적 프론트엔드는 Compose에서 127.0.0.1에만 바인딩합니다. PostgreSQL/Redis는 호스트 포트를 열지 않습니다. 후속 session 모드는 관리자 CLI로 초대한 계정과 사용자별 owner를 사용합니다. CORS는 인증 대체 수단이 아니며 세션·소유권·CSRF를 별도로 검사합니다. [인증 설계와 검증 범위](AUTHENTICATION.md)를 보세요.

로그인·서버 owner 결정·파일 권한·관리자 운영 지표 분리 코드는 추가했지만 실제 홈 PC 컨테이너/HTTPS cookie/운영 서버 자원 검증은 아직 남았습니다. 이 조건을 확인하기 전에 Tunnel로 공개하지 않습니다. 분산 rate limit, TLS와 운영 설정 관리도 검토해야 합니다. 기존 포트폴리오 API·DNS·Tunnel·Windows 스케줄러는 변경하지 않았습니다.

## 입력·출력

- 확장자와 MIME만 믿지 않고 UTF-8/BOM을 decode하고 strict CSV 헤더/구문을 검사합니다. 압축 파일을 해제하지 않으며 ZIP/gzip/바이너리는 CSV 검사에서 거부합니다. CP949는 지원하지 않습니다.
- 실제 ASGI 수신 바이트 한도는 파일 한도 + multipart 여유 128 KiB입니다. 파일 복사 때도 20 MiB를 재검사합니다. 파일 1개/일반 필드 1개, 옵션 필드 64 KiB로 제한합니다.
- 셀 16,384자, 물리 줄/논리 행 내용 262,144자, 헤더 100개, 데이터 100,000행, 시도별 출력 총 64 MiB 한도입니다. quoted multiline과 중복 키 digest도 이 경계 안에서 처리합니다.
- 업로드는 프로세스당 4개 동시 처리, 기본 분당 30회 요청, 활성 작업 최대 100개입니다. 분당 제한은 로컬 프로세스 메모리에 있으며 재시작/다중 API 인스턴스를 통합하지 않습니다.
- `MAX_STORAGE_BYTES` 기본 1 GiB는 DB 입장 제어 시 실제 파일 사용량과 활성 작업의 최대 3×64 MiB 출력을 보수적으로 예약하는 한도입니다. 동시에 수신 중인 최대 4×20 MiB 임시 입력, 늦게 종료하는 시도, 파일시스템 metadata까지 포함한 **강제 디스크 quota는 아닙니다**. 공개 운영에는 볼륨 quota·최소 여유 공간·추가 업로드 예약 및 정리 모니터링이 필요합니다.
- 원본 filename은 표시용 metadata만 사용합니다. 파일 경로는 서버 UUID/token으로 생성합니다. 모든 storage key는 resolve 후 root 안에 있는지 검사하며 symlink 탈출도 거부합니다. API는 storage_key/DB URL/traceback을 반환하지 않습니다.
- Python/셸/사용자 코드를 실행하지 않습니다. React가 파일명과 이벤트를 텍스트로 표시하며 `dangerouslySetInnerHTML`을 쓰지 않습니다. CSV 내용과 실제 값은 로그로 남기지 않습니다.

## CSV 수식 내보내기

기본 `safe_export=true`는 공백을 제외한 첫 글자가 `=`, `+`, `-`, `@`인 셀 앞에 작은따옴표를 붙입니다. 숫자처럼 보이는 음수도 보수적으로 escape할 수 있습니다. 정제/오류 CSV는 UTF-8 BOM으로 내보내며, `summary.json`의 `formula_cells_escaped`와 `export_policy`로 변환을 공개합니다. 이 정책은 모든 스프레드시트 제품의 해석을 보장하지 않습니다.

`safe_export=false`를 명시하면 정제된 raw 값을 내보냅니다. 원본 `input.csv` 다운로드는 애초 업로드한 바이트를 유지하므로 안전 변환 대상이 아닙니다. 신뢰하지 않는 원본을 스프레드시트에서 열 때 주의가 필요합니다. 원본과 정제본을 혼동하거나 변경 사실을 숨기지 않습니다.

## 보존·운영

로컬 기본 보존은 168시간이며 `RETENTION_HOURS`로 조정합니다. 공개 데모 후보는 24시간이지만 공개 기능은 미구현입니다. 만료 정리는 `python -m scripts.maintenance cleanup`을 명시적으로 실행합니다. 자동 스케줄은 설치하지 않았습니다. 종결 작업만 입력/출력 파일을 만료 처리하고 활성/재시도 작업 입력은 보존합니다. DB 기록은 남으며 파일 다운로드는 410입니다. 고아 시도와 24시간 이상 미등록 입력은 DB 참조 부재를 확인한 뒤 정리합니다.

테스트 failpoint는 `QUEUEFLOW_TEST_MODE=1` 환경변수에만 있고, 외부 HTTP로 워커를 죽이는 API는 없습니다. 실제 기업 데이터/개인정보는 샘플에 포함하지 않습니다. `.env`, `.deps`, 실행 데이터/출력은 Git에서 제외합니다. `.env.example`의 값은 localhost 개발용 예시이며 공개 운영 자격 증명이 아닙니다.

수동 `npm audit`와 고정 Python 35개 버전의 PyPI release metadata 조회를 수행했습니다. 발견된 취약점을 패치한 최종 결과는 npm 공지 0건, Python 공지 취약점 포함 패키지 0개입니다. 이는 조회 시점 공지 기준이며 전체 보안성을 보장하지 않습니다. lint 도구의 minimatch는 11.0.8을 사용하는 부모 패키지에만 override했습니다. CI에도 npm/PyPI 감사 단계를 작성했지만 원격 실행은 하지 않았습니다.

암호화 파일 저장, 악성 콘텐츠 스캔, 사용자별 강제 디스크 quota, 자동 retention 스케줄은 제공하지 않습니다. 초대 계정 로그인과 사용자별 활성 작업 제한은 추가했습니다. 운영 전 image digest 고정과 지속적인 dependency/image audit가 필요합니다.
