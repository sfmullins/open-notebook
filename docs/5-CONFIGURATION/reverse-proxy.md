# Reverse Proxy

Open Notebook can run behind nginx, Caddy, Traefik, or another HTTP reverse proxy. PostgreSQL/pgvector is an internal application dependency and should not be exposed through the HTTP proxy.

## Recommended topology

```text
Internet
  |
  v
TLS reverse proxy
  |
  +--> Open Notebook frontend :8502
  |
  +--> Open Notebook API      :5055

Open Notebook services
  |
  +--> PostgreSQL/pgvector    :5432 (private only)
```

## Docker Compose example

The repository `docker-compose.yml` already keeps PostgreSQL bound to localhost and connects the application to the internal `postgres` service hostname. Put only the application ports behind the reverse proxy.

```yaml
services:
  postgres:
    image: pgvector/pgvector:0.8.6-pg17-bookworm
    environment:
      POSTGRES_USER: open_notebook
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: open_notebook
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U open_notebook -d open_notebook"]
      interval: 5s
      timeout: 5s
      retries: 20
    restart: unless-stopped

  open_notebook:
    image: open-notebook:local
    environment:
      OPEN_NOTEBOOK_ENCRYPTION_KEY: ${OPEN_NOTEBOOK_ENCRYPTION_KEY}
      DATABASE_URL: postgresql://open_notebook:${POSTGRES_PASSWORD}@postgres:5432/open_notebook
      POSTGRES_URL: postgresql://open_notebook:${POSTGRES_PASSWORD}@postgres:5432/open_notebook
      API_URL: https://notebook.example.com
    ports:
      - "127.0.0.1:8502:8502"
      - "127.0.0.1:5055:5055"
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  postgres_data:
```

## nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name notebook.example.com;

    client_max_body_size 100m;

    location /api/ {
        proxy_pass http://127.0.0.1:5055/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }

    location / {
        proxy_pass http://127.0.0.1:8502;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
```

## Caddy example

```caddy
notebook.example.com {
    handle /api/* {
        reverse_proxy 127.0.0.1:5055
    }

    handle {
        reverse_proxy 127.0.0.1:8502
    }
}
```

## Environment

For a public deployment, set a stable encryption key and the public API origin when required:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
API_URL=https://notebook.example.com
```

If frontend and API remain on the same public origin, the default same-origin routing is usually sufficient.

## Security

- Do not proxy or publish PostgreSQL port 5432 to the internet.
- Use strong PostgreSQL credentials.
- Terminate HTTPS at the reverse proxy.
- Keep `OPEN_NOTEBOOK_ENCRYPTION_KEY` stable and secret.
- Configure `OPEN_NOTEBOOK_PASSWORD` if instance-level password protection is required.
- Ensure proxy upload/time-out limits are at least as large as the corresponding application settings.

See [Database Configuration](database.md) and [Environment Reference](environment-reference.md).
