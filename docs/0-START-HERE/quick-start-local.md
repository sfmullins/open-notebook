# Quick Start — Local Docker Compose

```bash
cp .env.example .env
# set OPEN_NOTEBOOK_ENCRYPTION_KEY
docker compose up -d --build
```

The standard Compose file starts Open Notebook and PostgreSQL/pgvector as separate services.

- UI: http://localhost:8502
- API: http://localhost:5055
- API docs: http://localhost:5055/docs

Configure an AI provider under **Settings → API Keys**. For local Ollama, use `examples/docker-compose-ollama.yml` or configure an existing endpoint.

See [Installation](../1-INSTALLATION/index.md) and [Database Configuration](../5-CONFIGURATION/database.md).
