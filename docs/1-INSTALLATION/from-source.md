# From Source Installation

Run Open Notebook directly from source for development or a native deployment. PostgreSQL 17 with pgvector is the only runtime database.

## Prerequisites

- Python 3.11 or 3.12
- Node.js 22
- Git
- [uv](https://github.com/astral-sh/uv)
- PostgreSQL 17 with the `vector` extension available
- an AI-provider account/key, or a reachable local model provider such as Ollama

Docker is optional. It is only needed if you choose to run PostgreSQL or other supporting services in containers.

## 1. Clone the repository

```bash
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
```

For a fork:

```bash
git clone https://github.com/YOUR_USERNAME/open-notebook.git
cd open-notebook
git remote add upstream https://github.com/lfnovo/open-notebook.git
```

## 2. Install locked dependencies

```bash
uv sync --frozen
cd frontend
npm ci
cd ..
```

## 3. Start PostgreSQL

Use a native installation or the repository Compose database service:

```bash
docker compose up -d postgres
```

A typical local DSN is:

```text
postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
```

The database must provide pgvector. On first startup the application initializes its schema and attempts `CREATE EXTENSION IF NOT EXISTS vector`; either grant the application role permission to do this or create the extension as an administrator beforehand.

## 4. Configure environment

```bash
cp .env.example .env
```

Set at least:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-secret
DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
INTERNAL_API_URL=http://127.0.0.1:5055
```

Configure AI-provider credentials through the UI after startup.

## 5. Start the application

With PostgreSQL already healthy:

```bash
make start-all
```

This starts the API, background worker, and frontend. PostgreSQL remains an external service.

For separate terminals:

```bash
# Terminal 1
make api

# Terminal 2
make worker

# Terminal 3
cd frontend && npm run dev
```

The worker is required for queued source processing, embeddings, transformations, and other background commands.

## 6. Access

- Development frontend: `http://localhost:3000`
- API: `http://localhost:5055`
- API docs: `http://localhost:5055/docs`
- PostgreSQL: `127.0.0.1:5432`

Packaged/container deployments expose the frontend on port `8502` by default.

## Development checks

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

## Database connection troubleshooting

Check native PostgreSQL:

```bash
pg_isready -h 127.0.0.1 -p 5432 -U open_notebook -d open_notebook
```

Or, if using the repository Compose database service:

```bash
docker compose ps postgres
docker compose logs postgres
```

`make database` performs a reachability check; it does not start or install PostgreSQL.

## Existing data from older releases

The previous database engine is supported only as a migration source. See [Local Development Setup](../7-DEVELOPMENT/development-setup.md#one-time-migration-from-a-legacy-data-store) for the importer command and safety rules.

## Next steps

- [Installation overview](index.md)
- [Architecture](../7-DEVELOPMENT/architecture.md)
- [Environment reference](../5-CONFIGURATION/environment-reference.md)
