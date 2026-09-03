# Native / Dockerless PostgreSQL Runtime

This branch removes Docker and SurrealDB as runtime requirements. Open Notebook runs as three native application services (API, worker, frontend) backed by PostgreSQL with the `vector` extension.

## Runtime topology

| Component | Native process | Listener / dependency |
| --- | --- | --- |
| Frontend | Next.js `npm start` | public `:8502`; proxies `/api/*` internally |
| API | `python run_api.py` | loopback/private `:5055` |
| Worker | `open-notebook-command-worker` compatibility CLI | PostgreSQL queue |
| Database | PostgreSQL + pgvector | local/private `:5432` |

The browser uses same-origin `/api/*` by default. `API_URL` remains an explicit override only for split deployments.

## Required environment

```dotenv
DATABASE_URL=postgresql://open_notebook:CHANGE_ME@127.0.0.1:5432/open_notebook
OPEN_NOTEBOOK_ENCRYPTION_KEY=CHANGE_ME
DATA_FOLDER=/var/lib/open-notebook
INTERNAL_API_URL=http://127.0.0.1:5055
```

SurrealDB variables are no longer used by the runtime. They are accepted only by the one-time importer.

## PostgreSQL prerequisites

PostgreSQL must have the `vector` extension available. The application bootstrap executes `CREATE EXTENSION IF NOT EXISTS vector`; the database role therefore needs permission to create the extension on first install, or an administrator must run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Do not expose PostgreSQL directly to the Internet.

## Fresh native install

1. Install Python 3.11/3.12, `uv`, Node.js 22/npm, PostgreSQL and pgvector.
2. Create an `open_notebook` PostgreSQL role and database.
3. Clone/copy Open Notebook to `/opt/open-notebook`.
4. Run `uv sync` in the repository.
5. Run `npm ci && npm run build` in `frontend/`.
6. Create `/etc/open-notebook/open-notebook.env` from the environment example above.
7. Copy the units under `deploy/systemd/` to `/etc/systemd/system/`, then enable the API, worker and frontend services.

The API and worker both bootstrap the idempotent PostgreSQL foundation schema, so service ordering is not used as a correctness mechanism.

## Migrating an existing SurrealDB install

Keep the old SurrealDB service available temporarily and point `DATABASE_URL` at an empty PostgreSQL database. Then run:

```bash
uv run python scripts/migrate_surreal_to_postgres.py \
  --surreal-url http://127.0.0.1:8000 \
  --surreal-user root \
  --surreal-password '<old password>'
```

The importer:

- enumerates SurrealDB tables using `INFO FOR DB`;
- preserves existing `table:key` record IDs;
- preserves relations and source embedding order/content/vectors;
- deliberately drops historical command-queue jobs and clears stale command references;
- refuses to import into a non-empty PostgreSQL target unless `--allow-nonempty` is explicitly supplied.

Take a backup before migration. Do not destroy the SurrealDB data until record counts, notebooks, sources, notes, search and podcast metadata have been verified in PostgreSQL.

## Queue recovery semantics

The replacement queue is PostgreSQL-backed. Workers claim jobs with `FOR UPDATE SKIP LOCKED` and attach a renewable lease. A healthy long-running job periodically extends its lease; a worker crash leaves the lease to expire, after which another worker can reclaim the job. This avoids both permanently stuck `running` jobs and the duplicate execution risk of a fixed, non-renewed lease.

## Remaining compatibility boundary

The migration branch intentionally fails on an unported complex SurrealQL query rather than returning an approximate result. Complex graph/search call sites are being converted to PostgreSQL-native helpers as part of this PR. The PR must not be merged while any such call site remains reachable in normal product flows.
