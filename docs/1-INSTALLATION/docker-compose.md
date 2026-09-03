# Docker Compose Installation

The repository root `docker-compose.yml` is the supported container topology for this branch. It runs PostgreSQL + pgvector as a separate persistent service and the Open Notebook application as a second service.

## Prerequisites

- Docker Engine / Docker Desktop with Compose support
- an AI-provider key, or a reachable local model provider
- enough persistent disk for PostgreSQL data and user content

## 1. Get the repository Compose file

Clone the repository or download the root `docker-compose.yml`. Do not reuse Compose examples from older releases that define the previous database engine.

```bash
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
```

## 2. Configure secrets

Create `.env` beside `docker-compose.yml` and override the development defaults:

```dotenv
POSTGRES_USER=open_notebook
POSTGRES_PASSWORD=replace-with-a-strong-database-password
POSTGRES_DB=open_notebook
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-secret
```

The Compose file builds the application from the repository and constructs the database DSN internally:

```text
postgresql://<user>:<password>@postgres:5432/<database>
```

Both `DATABASE_URL` and `POSTGRES_URL` are set to that PostgreSQL connection string inside the application service.

## 3. Start services

```bash
docker compose up -d --build
```

Check health/status:

```bash
docker compose ps
docker compose logs -f postgres
docker compose logs -f open_notebook
```

The application waits for PostgreSQL health before normal startup.

## 4. Access Open Notebook

- Web UI: `http://localhost:8502`
- API: `http://localhost:5055`
- API docs: `http://localhost:5055/docs`

The PostgreSQL port is bound to `127.0.0.1:5432` by the repository Compose file for local administration. Do not expose it directly to untrusted networks.

## 5. Configure AI providers

After opening the UI:

1. go to **Settings / API Keys**;
2. add and save a provider credential;
3. test the connection;
4. discover/register the required models.

For fully local inference, configure a separately managed Ollama or compatible provider endpoint.

## Storage

The Compose topology uses:

- `postgres_data` — PostgreSQL database volume;
- `./notebook_data:/app/data` — application data/cache files.

Back up both according to the recovery requirements of your installation. PostgreSQL is the authoritative structured store.

## Common operations

Stop application and database containers without deleting data:

```bash
docker compose down
```

Restart:

```bash
docker compose up -d
```

Rebuild the application image after source changes:

```bash
docker compose up -d --build open_notebook
```

View logs:

```bash
docker compose logs -f open_notebook
docker compose logs -f postgres
```

Destroy containers **and the named PostgreSQL volume**:

```bash
docker compose down -v
```

The last command deletes database data and is not a routine reset mechanism.

## Database troubleshooting

Check PostgreSQL health:

```bash
docker compose exec postgres \
  pg_isready -U "${POSTGRES_USER:-open_notebook}" -d "${POSTGRES_DB:-open_notebook}"
```

Check that pgvector exists:

```bash
docker compose exec postgres \
  psql -U "${POSTGRES_USER:-open_notebook}" -d "${POSTGRES_DB:-open_notebook}" \
  -c 'SELECT extname, extversion FROM pg_extension WHERE extname = '\''vector'\'';'
```

If the extension is absent, ensure the database image/package includes pgvector and restart application initialization after installing it.

## Existing installations using the previous database

Do not point a PostgreSQL runtime at the old data directory. Start a fresh PostgreSQL target and run the one-time migration utility while the old database is still reachable. See [Local Development Setup](../7-DEVELOPMENT/development-setup.md#one-time-migration-from-a-legacy-data-store).

## Related documentation

- [Installation overview](index.md)
- [Environment reference](../5-CONFIGURATION/environment-reference.md)
- [Architecture](../7-DEVELOPMENT/architecture.md)
- [Security hardening](../5-CONFIGURATION/security.md)
