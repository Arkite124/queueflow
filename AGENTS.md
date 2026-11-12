# QueueFlow contributor instructions

- Work only inside this independent project; do not change the parent portfolio.
- Develop and integrate changes on `develop`; promote reviewed, validated changes from `develop` to `main` for release within the user's authorized scope.
- PostgreSQL is authoritative. Redis delivery is at least once.
- Every worker mutation must verify its current, unexpired lease and fencing token.
- Commit job state, artifacts, attempt history and outbox records in transactions.
- Acknowledge stream messages only after durable outcome or retry scheduling.
- Never log CSV values, expose storage keys, or trust client filenames as paths.
- Use Alembic migrations. Never reset databases or delete Docker volumes as routine setup.
- Run unit tests, frontend lint/build and isolated PostgreSQL/Redis tests where available.
- Report skipped infrastructure checks explicitly. Never invent benchmark numbers.
- Keep machine-local logs, benchmark/validation output and operator handoffs untracked. Never commit personal absolute paths or hostnames; run the repository hygiene check before committing.
- Push only with user authorization. Do not merge main, publish, modify DNS/tunnels, restart existing services, or edit OS schedules without specific authorization.
