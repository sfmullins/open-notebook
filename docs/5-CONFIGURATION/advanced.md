# Advanced Configuration

For the authoritative variable list, see [Environment Reference](environment-reference.md).

## Background worker

```dotenv
OPEN_NOTEBOOK_WORKER_MAX_TASKS=5
```

Retry/backoff is managed internally by the PostgreSQL command queue; obsolete database-specific retry variables are not supported.

## PostgreSQL

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

See [Database](database.md).

## API and provider transport

```dotenv
API_CLIENT_TIMEOUT=300
OPEN_NOTEBOOK_MAX_UPLOAD_SIZE_MB=100
ESPERANTO_LLM_TIMEOUT=60
ESPERANTO_TTS_TIMEOUT=300
ESPERANTO_SSL_VERIFY=true
```

## Optional extraction runtimes

```dotenv
OPEN_NOTEBOOK_ENABLE_DOCLING=false
OPEN_NOTEBOOK_ENABLE_CRAWL4AI=false
```

## Proxy

```dotenv
HTTP_PROXY=http://proxy.example:8080
HTTPS_PROXY=http://proxy.example:8080
NO_PROXY=localhost,127.0.0.1,postgres,host.docker.internal,.local
```

Do not route local PostgreSQL traffic through an HTTP proxy.
