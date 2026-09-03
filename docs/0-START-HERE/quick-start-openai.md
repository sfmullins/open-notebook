# Quick Start — OpenAI

```bash
cp .env.example .env
# set OPEN_NOTEBOOK_ENCRYPTION_KEY
docker compose up -d --build
```

Open http://localhost:8502, then use **Settings → API Keys** to add an OpenAI credential, test it, discover models, and register the models you need.

The standard stack starts PostgreSQL/pgvector automatically. See [Database Configuration](../5-CONFIGURATION/database.md) for external database deployments.
