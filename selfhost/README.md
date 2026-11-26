# Self-hosted Flagsmith Experimentation in one command

A starter kit: open-source Flagsmith with Experimentation on your machine, bootstrapped through the API and checked by
Preflight. Nothing to click in a dashboard.

```bash
pip install -e ".[mcp]"          # from the repo root
preflight selfhost up            # writes selfhost/.env with random secrets, starts Docker, bootstraps
```

`up` runs `docker compose up -d` and then `bootstrap`, which (idempotently):

1. waits for `http://localhost:8000/health/liveness/`
2. checks `/version/` reports `experimentation_enabled`
3. creates the first admin (or logs in), an organisation, a project, and Development + Production environments
4. connects each environment to the **Flagsmith database** warehouse
5. creates an organisation API key and writes everything to `selfhost/.env.local` (git-ignored, never printed)

Then use any Preflight command against it:

```bash
preflight --env-file selfhost/.env.local check path/to/app
preflight --env-file selfhost/.env.local plan --flag my_flag --variant b --event signup --name "…" --hypothesis "…"
preflight --env-file selfhost/.env.local smoke --flag my_flag --event signup
preflight selfhost down            # stop; add --wipe to delete the database volume
```

Dashboard: http://localhost:8000. The admin email and password are in `selfhost/.env`.

## Point your app at it

Both SDKs send experiment events to **Flagsmith Cloud** by default, even when flags come from your own server. Set
both URLs, or your self-hosted experiment records nothing. Preflight flags this as **PF006**.

```js
await flagsmith.init({
  environmentID: '<client-side key>',
  api: 'http://localhost:8000/api/v1/',
  // Must be nested: @flagsmith/flagsmith 12.6 ignores a top-level `eventsApiUrl`.
  eventProcessorConfig: { eventsApiUrl: 'http://localhost:8000/api/' },
  enableEvents: true,
})
```

```python
from flagsmith import Flagsmith
from flagsmith.analytics import EventProcessorConfig

flagsmith = Flagsmith(
    environment_key="<key>",
    api_url="http://localhost:8000/api/v1/",
    enable_events=True,
    event_processor_config=EventProcessorConfig(events_api_url="http://localhost:8000/api/"),
)
```

## What's in the compose file

The four services from Flagsmith's official `docker-compose.yml` (Postgres 15, `migrate-db`, the API on :8000, the
task processor on :8001) with `EXPERIMENTATION_ENABLED=true`. One change: secrets come from `selfhost/.env`
instead of the hard-coded `secret` / `password`. Pin a version with `FLAGSMITH_TAG=<tag>` in `selfhost/.env`.

The Flagsmith-database warehouse stores events in the same Postgres. Flagsmith describes it as fine for evaluation;
for production volume use its ingestion service with Kafka and ClickHouse or Databricks.

## Verified (2026-11-26, Windows 11, Docker Desktop 27.0.3, 8 GB)

- `preflight selfhost up`: all four containers healthy, `migrate-db` exited 0, bootstrap succeeded on the first run;
  a second run created nothing new.
- `plan` + `apply --start` created a flag, variation, metric and experiment #1 and started it.
- `smoke`, 120 synthetic identities: Flagsmith counted exactly the 64 / 56 identities and 23 + 14 conversions sent.
- `verdict`: "No clear winner yet" (6.4% chance to win, interval crosses zero). Correct for synthetic data.
