# Quick Start — Cloud / Server

Use the standard PostgreSQL/pgvector Docker Compose deployment behind your TLS reverse proxy.

```bash
cp .env.example .env
docker compose up -d --build
```

Before exposing the service:

- set a strong `OPEN_NOTEBOOK_ENCRYPTION_KEY`;
- set strong PostgreSQL credentials;
- do not expose PostgreSQL port 5432 publicly;
- set `OPEN_NOTEBOOK_PASSWORD` if instance-level password protection is required;
- configure provider credentials through **Settings → API Keys**.

See [Docker Compose](../1-INSTALLATION/docker-compose.md), [Database](../5-CONFIGURATION/database.md), and [Reverse Proxy](../5-CONFIGURATION/reverse-proxy.md).
