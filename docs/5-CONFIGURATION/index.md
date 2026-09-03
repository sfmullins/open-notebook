# Configuration

Open Notebook runtime configuration is environment-based. PostgreSQL/pgvector is the only runtime database.

Copy the example file and set a stable encryption key:

```bash
cp .env.example .env
```

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
```

For native or external PostgreSQL:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

The standard Docker Compose deployment constructs its internal DSN from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

See [Database](database.md) and [Environment Reference](environment-reference.md).

## AI providers

Store provider credentials through **Settings → API Keys**, then test the connection, discover models, and register the models you intend to use.

## Worker concurrency

```dotenv
OPEN_NOTEBOOK_WORKER_MAX_TASKS=5
```

Use `1` for sequential execution on constrained local-model/GPU setups.

## Security

Keep `OPEN_NOTEBOOK_ENCRYPTION_KEY` stable and secret, use strong PostgreSQL credentials outside isolated development, do not publish PostgreSQL publicly, and set `OPEN_NOTEBOOK_PASSWORD` where instance-level password protection is required.
