# 검증 재현

저장소 루트에서 실행한다. Python·Node·Docker는 PATH에서 찾으며 개인 PC의 설치 경로를 저장하지 않는다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/unit -q
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build
```

실제 PostgreSQL/Redis 검증은 운영과 분리된 테스트 Compose를 사용한다.

```powershell
docker compose -p queueflow-tests -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p queueflow-tests -f docker-compose.test.yml down
```

생성한 JUnit·JSON·로그·환경 정보와 PC별 인계 기록은 `output/` 등 Git 제외 경로에만 보관한다.
`docs/validation-results/`와 `docs/benchmark-results/`도 로컬 전용이다.
실행하지 못한 인프라 검사는 건너뛴 사실을 명시하고 측정 결과를 추정하지 않는다.

커밋 전 검사:

```powershell
git config core.hooksPath .githooks
python scripts/check_repository_hygiene.py --staged
python scripts/check_repository_hygiene.py --all
```

CI에서도 동일한 검사를 수행한다. `git add -f`로 추가한 로컬 기록과 개인 절대 경로도 검사한다.
