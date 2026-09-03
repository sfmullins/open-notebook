# Native / Dockerless PostgreSQL Runtime

Open Notebook can run without Docker. The native topology is the API, worker, and frontend backed by an independently managed PostgreSQL 17 server with pgvector.

## Runtime topology

| Component | Native process | Listener / dependency |
| --- | --- | --- |
| Frontend | Next.js | development `:3000`; packaged/native service may expose `:8502` |
| API | `uvicorn` / repository API launcher | `:5055` |
| Worker | `open-notebook-command-worker` | PostgreSQL durable queue |
| Database | PostgreSQL 17 + pgvector | private/local `:5432` |

The browser normally uses same-origin `/api/*`; the Next.js server proxies those requests to the FastAPI process using `INTERNAL_API_URL`.

## Required environment

```dotenv
DATABASE_URL=postgresql://open_notebook:CHANGE_ME@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:CHANGE_ME@127.0.0.1:5432/open_notebook
OPEN_NOTEBOOK_ENCRYPTION_KEY=CHANGE_ME
DATA_FOLDER=/var/lib/open-notebook
INTERNAL_API_URL=http://127.0.0.1:5055
```

`DATABASE_URL` is canonical. Keep `POSTGRES_URL` identical when set.

## PostgreSQL prerequisites

The server must provide the pgvector `vector` extension. Application bootstrap executes `CREATE EXTENSION IF NOT EXISTS vector`; either let the application database role create the extension or provision it once as an administrator:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Create a dedicated role/database rather than using a PostgreSQL superuser for the application.

Example Debian/Ubuntu-style setup, assuming your distribution packages PostgreSQL 17 and pgvector:

```bash
sudo -u postgres createuser --pwprompt open_notebook
sudo -u postgres createdb --owner=open_notebook open_notebook
sudo -u postgres psql -d open_notebook -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

Package names vary by distribution; use the supported PostgreSQL/pgvector packages for the host OS.

## Application installation

```bash
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
uv sync --frozen
cd frontend && npm ci && cd ..
```

Start PostgreSQL through the host service manager, then verify it before starting Open Notebook:

```bash
pg_isready -h 127.0.0.1 -p 5432 -U open_notebook -d open_notebook
make database
```

`make database` is intentionally a reachability check only.

## Start services

```bash
make start-all
```

Or run components separately:

```bash
make api
make worker
cd frontend && npm run dev
```

The API and worker both require PostgreSQL. If the database is unavailable, startup/health should fail rather than create a fallback local store.

## Persistence and recovery

Back up at least:

1. the PostgreSQL database;
2. the configured `DATA_FOLDER` if it contains generated or cached artefacts you need to preserve.

PostgreSQL holds the authoritative structured records, relations, command queue, and vector indexes. Use normal PostgreSQL backup/restore tooling appropriate to the deployment.

The command queue is durable. Worker restarts do not require recreating queued user work; lease/recovery semantics allow interrupted jobs to become claimable again according to queue policy.

## One-time import from older installations

A legacy database may remain temporarily available **only as migration input**:

```bash
uv run python scripts/migrate_surreal_to_postgres.py \
  --surreal-url http://OLD_DATABASE_HOST:8000 \
  --surreal-user root \
  --surreal-password 'OLD_PASSWORD' \
  --surreal-namespace open_notebook \
  --surreal-database open_notebook
```

The importer writes domain records, relations, source embeddings, and note/source-insight embeddings into PostgreSQL. It refuses to merge into a non-empty target unless `--allow-nonempty` is explicitly requested.

After validation, retire the old service and remove its credentials/configuration from the runtime environment.

## Runtime boundary

All normal product data paths use PostgreSQL-native typed repository helpers. CI rejects reintroduction of:

- the old client SDK in runtime code;
- the old database container in runtime deployment files;
- raw compatibility query APIs;
- old runtime environment variables.

The only allowed old-store reference in shipped code is the explicit one-time source importer and the boundary checker that polices it.

## Verification

```bash
python3 scripts/check_no_surreal_runtime.py
uv run ruff check .
uv run python -m mypy .
uv run pytest tests/
```

CI additionally executes a real legacy-to-PostgreSQL migration fixture and checks semantic parity for records, relations, and pgvector data.

Related documentation:

- [Architecture](architecture.md)
- [Local Development Setup](development-setup.md)
- [Environment Reference](../5-CONFIGURATION/environment-reference.md)
