# Environment Reference

This document covers current runtime configuration. PostgreSQL is the only runtime database. Variables for the previous database engine are migration-only and are not valid application runtime configuration.

## Core application

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPEN_NOTEBOOK_ENCRYPTION_KEY` | Yes for stored credentials | none | Encrypts AI-provider credentials stored in PostgreSQL. Keep this stable and secret. |
| `OPEN_NOTEBOOK_PASSWORD` | No | none | Optional password protection for the Open Notebook instance. |
| `API_URL` | No | auto/same-origin | External API URL override for split deployments. |
| `INTERNAL_API_URL` | No | `http://localhost:5055` | Internal URL used by the Next.js server to proxy API requests. |
| `API_CLIENT_TIMEOUT` | No | `300` | API client timeout in seconds. |
| `API_HOST` | No | `0.0.0.0` in container runtime | FastAPI bind interface. |
| `FRONTEND_BIND_HOST` | No | `0.0.0.0` in container runtime | Next.js bind interface. |
| `OPEN_NOTEBOOK_MAX_UPLOAD_SIZE_MB` | No | `100` | Maximum request body/upload size accepted by the API. |
| `DATA_FOLDER` | No | application default | Persistent application data/cache directory. |

`OPEN_NOTEBOOK_ENCRYPTION_KEY` is not a password-reset key. If it changes or is lost, previously stored provider credentials cannot be decrypted.

## PostgreSQL

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | Yes | deployment-specific | Canonical PostgreSQL DSN. |
| `POSTGRES_URL` | No | falls back to `DATABASE_URL` where supported | Explicit PostgreSQL DSN alias used by deployment tooling. Keep identical to `DATABASE_URL` when both are set. |

Example:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

The database must provide the pgvector `vector` extension. The application initializes its PostgreSQL schema on startup; it does not install or manage the PostgreSQL server process.

## Worker concurrency

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPEN_NOTEBOOK_WORKER_MAX_TASKS` | No | `5` | Maximum number of queued jobs the background worker runs concurrently. Use `1` for constrained local-model/GPU setups. |

This value is consumed when the worker process is launched. In shell-managed deployments, export it in the process environment rather than assuming an application `.env` loader will affect a wrapper command that reads it first.

## Embeddings and chunking

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE` | No | `50` | Number of texts sent per embedding batch. Reduce for constrained/local providers. |
| `OPEN_NOTEBOOK_MIN_CHUNK_SIZE` | No | `5` | Minimum chunk size in tokens; smaller chunks are dropped before embedding. Set `0` to disable filtering. |

Embeddings are persisted in PostgreSQL/pgvector (`source_embedding_pg` and `record_embedding_pg`).

## LLM/provider transport

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `ESPERANTO_LLM_TIMEOUT` | No | `60` | LLM inference timeout in seconds. |
| `ESPERANTO_SSL_VERIFY` | No | `true` | Verify provider TLS certificates. Disable only for controlled development environments. |
| `ESPERANTO_SSL_CA_BUNDLE` | No | none | Custom CA bundle path for provider TLS. |

Provider API keys should normally be stored through **Settings / API Keys** rather than application environment variables.

## Text-to-speech

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TTS_BATCH_SIZE` | No | `5` | Concurrent TTS requests. |
| `ESPERANTO_TTS_TIMEOUT` | No | `300` | TTS provider timeout in seconds. |

## Content extraction

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `FIRECRAWL_API_KEY` | No | none | Firecrawl credential. |
| `FIRECRAWL_API_URL` | No | provider default | Base URL for a self-hosted Firecrawl service. |
| `CCORE_FIRECRAWL_PROXY` | No | `auto` | Firecrawl proxy mode passed to content-core. |
| `CCORE_FIRECRAWL_WAIT_FOR` | No | `3000` | JavaScript render wait in milliseconds passed to content-core. |
| `JINA_API_KEY` | No | none | Jina extraction credential. |
| `CRAWL4AI_API_URL` | No | none | Remote Crawl4AI endpoint. |
| `OPEN_NOTEBOOK_ENABLE_DOCLING` | No | `false` | Enable/install optional Docling runtime in deployments that support dynamic optional runtimes. |
| `OPEN_NOTEBOOK_ENABLE_CRAWL4AI` | No | `false` | Enable/install optional local Crawl4AI runtime where supported. |

Optional heavy engines are not part of the minimal runtime dependency set.

## API and CORS

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed browser origins. Set explicitly for production split-origin deployments. |

Example:

```dotenv
CORS_ORIGINS=https://notebook.example.com
```

A same-origin deployment through the Next.js proxy normally does not need a broad CORS policy.

## HTTP proxy

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `HTTP_PROXY` | No | none | HTTP proxy for outbound HTTP clients that honour the standard variable. |
| `HTTPS_PROXY` | No | none | HTTPS proxy for outbound clients. |
| `NO_PROXY` | No | none | Hosts that should bypass the HTTP proxy. |

Typical local deployment:

```dotenv
NO_PROXY=localhost,127.0.0.1,postgres,.local
```

PostgreSQL uses its native protocol rather than HTTP; proxy settings primarily affect outbound AI/content-provider HTTP traffic.

## Tracing and diagnostics

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LANGCHAIN_TRACING_V2` | No | `false` | Enable LangSmith tracing. |
| `LANGCHAIN_ENDPOINT` | No | LangSmith default | Trace endpoint. |
| `LANGCHAIN_API_KEY` | No | none | LangSmith credential. |
| `LANGCHAIN_PROJECT` | No | `Open Notebook` | LangSmith project name. |

## Minimal local configuration

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-local-secret
DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
INTERNAL_API_URL=http://127.0.0.1:5055
```

## Production example

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
OPEN_NOTEBOOK_PASSWORD=replace-with-an-access-password
DATABASE_URL=postgresql://open_notebook:strong-db-password@db.internal:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-db-password@db.internal:5432/open_notebook
API_URL=https://notebook.example.com
CORS_ORIGINS=https://notebook.example.com
```

Store secrets in your platform's secret manager rather than committing them to source control.

## Legacy AI-provider environment variables

Older deployments may contain provider-specific variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_API_BASE`, or similar. The preferred configuration is the provider credential UI. Migrate credentials into the encrypted database store and remove environment copies where practical.

## One-time legacy data import

The legacy importer accepts its own source connection arguments/environment solely while migrating old data. They are intentionally not documented as runtime configuration. Use the explicit CLI shown in [Local Development Setup](../7-DEVELOPMENT/development-setup.md#one-time-migration-from-a-legacy-data-store), complete the import, validate PostgreSQL, and retire the old database service.

## Validation

Useful checks:

```bash
printf '%s\n' "$DATABASE_URL"
pg_isready -h 127.0.0.1 -p 5432 -U open_notebook -d open_notebook
env | grep -E '^(OPEN_NOTEBOOK|DATABASE_URL|POSTGRES_URL|API_URL|INTERNAL_API_URL)=' | sort
```

Related documentation:

- [Installation](../1-INSTALLATION/index.md)
- [Docker Compose](../1-INSTALLATION/docker-compose.md)
- [Architecture](../7-DEVELOPMENT/architecture.md)
