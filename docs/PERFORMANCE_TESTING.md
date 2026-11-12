# 성능 측정 방법

실측 원본은 개발자 PC에만 보관하며 저장소에 커밋하지 않는다.
원본에는 호스트명, Docker label과 작업 디렉터리 같은 머신 정보가 포함될 수 있다.

## 처리기 단독 측정

```powershell
python -m scripts.run_benchmark --mode processor --rows 1000 10000 100000 --repeats 3 --output output/processor-benchmark.json
```

이 수치는 API·PostgreSQL·Redis·큐 대기·워커 확장을 측정하지 않는다.
표본 수가 작으면 P95를 운영 지연 분포로 일반화하지 않는다.

## 서버의 제한된 부하 측정

```powershell
python -m scripts.server_loadtest
```

새 프로젝트 이름과 loopback 포트를 사용하는 격리 스택에서 production 자원 한도,
worker 1개, 작업당 100,000행, 동시 작업 2개를 3회 실행한다.
ZIP 및 Excel의 내용 일치를 검사하고 자원 표본을 `output/server-loadtest/`에 보관한다.
운영 스택에 대한 부하나 공개 HTTPS·인증 부하로 해석하지 않는다.
테스트 종료 시 전용 컨테이너·네트워크를 정리하고 볼륨을 보존한다.

CSV 값, 비밀번호, 세션·Tunnel 토큰은 로그나 측정 결과에 넣지 않는다.
처리량에는 포함한 구간과 제외한 구간을 함께 기록한다.
Docker 표본의 관측 최대 사용량은 순간 최대치가 아니며 호스트 앱의 영향을 포함할 수 있다.
최대 수용량·장시간 지속 부하·장애 복구 시간은 별도 실험 없이 주장하지 않는다.
