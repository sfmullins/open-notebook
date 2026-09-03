# Quick Start — Development

## Prerequisites

- Python 3.12
- Git
- uv
- Node.js 22
- PostgreSQL 17 with pgvector

For local development, PostgreSQL can run natively or in Docker.

## 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/open-notebook.git
cd open-notebook
git remote add upstream https://github.com/lfnovo/open-notebook.git
uv sync
cd frontend && npm install && cd ..
```

## 2. Configure

```bash
cp .env.example .env
```

Set a stable encryption key and PostgreSQL DSN:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
```

## 3. Start PostgreSQL/pgvector

Example local container:

```bash
docker run -d --name open-notebook-postgres \
  -e POSTGRES_USER=open_notebook \
  -e POSTGRES_PASSWORD=open_notebook \
  -e POSTGRES_DB=open_notebook \
  -p 127.0.0.1:5432:5432 \
  pgvector/pgvector:0.8.6-pg17-bookworm
```

## 4. Start application services

In separate terminals:

```bash
# API
uv run --env-file .env run_api.py

# Worker
uv run --env-file .env open-notebook-command-worker --import-modules commands --max-tasks 5

# Frontend
cd frontend && npm run dev
```

## 5. Verify

- API health: http://localhost:5055/health
- API docs: http://localhost:5055/docs
- Frontend: http://localhost:3000

## Quality commands

```bash
uv run ruff check .
uv run python -m mypy .
uv run pytest tests/
cd frontend && npm run lint && npm run test:coverage && npm run build
```

See [Development Setup](development-setup.md), [Architecture](architecture.md), and [Dockerless PostgreSQL](dockerless-postgresql.md) for deeper guidance.
