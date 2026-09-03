# Local Development Setup

Open Notebook now uses PostgreSQL 17 with pgvector as its only runtime database. The application does not start, embed, or manage a database process for you; PostgreSQL is an external service and must already be reachable before the API or worker starts.

## Prerequisites

- Python 3.11 or 3.12
- [uv](https://github.com/astral-sh/uv)
- Node.js 22
- PostgreSQL 17 with the `vector` extension available
- Git
- Docker or Podman only if you choose to run PostgreSQL in a container

## 1. Clone and install

```bash
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
uv sync --frozen --group dev
cd frontend
npm ci
cd ..
```

## 2. Start PostgreSQL

Use a native PostgreSQL installation or a container. For the repository Compose topology:

```bash
docker compose up -d postgres
```

For a native installation, create a database/user and ensure pgvector is available. The application creates its own schema and runs schema migrations automatically.

A typical local connection is:

```text
postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
```

`make database` is a reachability check. It deliberately does **not** install or start PostgreSQL.

## 3. Configure the environment

Copy the example file and set at least:

```bash
cp .env.example .env
```

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-local-secret
DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
INTERNAL_API_URL=http://127.0.0.1:5055
DATA_FOLDER=./data
```

`DATABASE_URL` is the canonical database connection. `POSTGRES_URL` is retained as an explicit PostgreSQL alias for deployment compatibility; keep both identical when both are set.

AI-provider credentials are configured in the UI after startup.

## 4. Start the stack

With PostgreSQL already healthy:

```bash
make start-all
```

This starts the API, command worker, and frontend. It does not own the PostgreSQL lifecycle.

To run processes separately:

```bash
# Terminal 1
make api

# Terminal 2
make worker

# Terminal 3
cd frontend && npm run dev
```

Default development endpoints:

- Frontend: `http://localhost:3000`
- API: `http://localhost:5055`
- API docs: `http://localhost:5055/docs`
- PostgreSQL: `127.0.0.1:5432`

## PostgreSQL storage model

The application uses typed repository helpers over these primary structures:

- `on_record` — generic application records keyed by table and record ID.
- `on_relation` — typed graph-style relations between records.
- `source_embedding_pg` — pgvector chunks for source search.
- `record_embedding_pg` — pgvector embeddings for notes and source insights.
- `command_job` — durable background-work queue.
- schema-migration metadata maintained by the PostgreSQL initialization layer.

Do not add raw database query compatibility APIs to product code. The runtime boundary check rejects legacy database APIs and configuration.

## Quality gates

Run the same core checks as CI:

```bash
uv lock --check
uv sync --frozen --group dev
uv run ruff check .
uv run python -m mypy .
uv run pytest tests/

cd frontend
npm ci
npm run lint
npm run test:coverage
npm run build
npm audit --audit-level=moderate
```

The repository also checks Markdown links and the PostgreSQL-only runtime boundary.

## One-time migration from a legacy data store

Existing installations from the older database architecture can be imported with:

```bash
uv run python scripts/migrate_surreal_to_postgres.py \
  --surreal-url http://127.0.0.1:8000 \
  --surreal-user root \
  --surreal-password root \
  --surreal-namespace open_notebook \
  --surreal-database open_notebook
```

The source database is migration input only. It is not a runtime dependency. The importer refuses to merge into a non-empty PostgreSQL target unless `--allow-nonempty` is explicitly supplied.

## Related documentation

- [Architecture](architecture.md)
- [Dockerless PostgreSQL](dockerless-postgresql.md)
- [From-source installation](../1-INSTALLATION/from-source.md)
- [Environment reference](../5-CONFIGURATION/environment-reference.md)
