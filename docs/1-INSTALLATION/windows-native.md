# Windows Native Installation

Open Notebook is PostgreSQL-native. SurrealDB is not a supported runtime database.

## Prerequisites

- Git
- Python 3.12
- uv
- Node.js 22
- PostgreSQL 17 with the pgvector extension

PostgreSQL can run natively on Windows or in a local container. It must be reachable before the API or worker starts.

## Install

```powershell
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
uv sync
cd frontend
npm install
cd ..
```

Copy `.env.example` to `.env` and set at least:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
```

The PostgreSQL database must provide the `vector` extension.

## Start services

Use separate terminals from the repository root.

### Terminal 1 — API

```powershell
uv run --env-file .env run_api.py
```

### Terminal 2 — worker

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run --env-file .env open-notebook-command-worker --import-modules commands --max-tasks 5
```

### Terminal 3 — frontend

```powershell
cd frontend
npm run dev
```

Open:

- UI: http://localhost:3000
- API: http://localhost:5055
- API docs: http://localhost:5055/docs

## PostgreSQL in Docker on Windows

A convenient development database is:

```powershell
docker run -d --name open-notebook-postgres `
  -e POSTGRES_USER=open_notebook `
  -e POSTGRES_PASSWORD=open_notebook `
  -e POSTGRES_DB=open_notebook `
  -p 127.0.0.1:5432:5432 `
  -v open_notebook_postgres:/var/lib/postgresql/data `
  pgvector/pgvector:0.8.6-pg17-bookworm
```

Keep port 5432 bound to localhost for local development.

## Common issues

### Database health check fails

Confirm PostgreSQL is listening and the DSN is correct:

```powershell
Test-NetConnection 127.0.0.1 -Port 5432
```

Then verify `DATABASE_URL` and `POSTGRES_URL` in `.env`. They should normally be identical.

### Worker cannot import `commands`

Set `PYTHONPATH` to the repository root before starting the worker:

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

### `DATA_FOLDER` path parsing

If an environment-file parser rejects a Windows path, set it in the shell instead:

```powershell
$env:DATA_FOLDER = "$env:USERPROFILE\open-notebook-data"
```

## Provider configuration

After startup, add provider credentials through **Settings → API Keys**, test the connection, discover models, and register the models you intend to use.

See [Database Configuration](../5-CONFIGURATION/database.md) and [Environment Reference](../5-CONFIGURATION/environment-reference.md) for the current runtime configuration.
