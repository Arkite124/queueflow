# QueueFlow contributor instructions

- Work only inside this independent project; do not change the parent portfolio.
- PostgreSQL is authoritative. Redis delivery is at least once.
- Every worker mutation must verify its current, unexpired lease and fencing token.
- Commit job state, artifacts, attempt history and outbox records in transactions.
- Acknowledge stream messages only after durable outcome or retry scheduling.
- Never log CSV values, expose storage keys, or trust client filenames as paths.
- Use Alembic migrations. Never reset databases or delete Docker volumes as routine setup.
- Run unit tests, frontend lint/build and isolated PostgreSQL/Redis tests where available.
- Report skipped infrastructure checks explicitly. Never invent benchmark numbers.
- Do not push, publish, modify DNS/tunnels, restart existing services, or edit OS schedules.
