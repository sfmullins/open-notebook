# Open Notebook Architecture

## Runtime topology

```text
Browser
  │
  ▼
Next.js frontend :8502
  │  same-origin /api proxy
  ▼
FastAPI :5055 ───────────────┐
  │                          │
  │                          ▼
  │                    PostgreSQL 17 + pgvector :5432
  │                          ▲
  ▼                          │
Command worker ──────────────┘
```

The API and worker share PostgreSQL as the single authoritative runtime store. The frontend never connects to the database directly.

In development the Next.js dev server normally runs on port `3000`; the packaged runtime exposes the frontend on `8502`.

## Application layers

### Frontend

- Next.js/React application.
- Browser traffic uses same-origin `/api/*` routes.
- Server-side proxying forwards API traffic to FastAPI using `INTERNAL_API_URL`.
- No database credentials are exposed to the browser.

### API

- FastAPI owns HTTP validation, authentication, request orchestration, and public REST endpoints.
- Domain models call typed repository helpers rather than constructing database-language strings.
- Long-running work is queued rather than performed inline where appropriate.

### Command worker

The worker consumes durable jobs from PostgreSQL. Queue claims use transactional locking so multiple workers can safely compete for work without processing the same row simultaneously. Jobs have explicit state and lease/recovery semantics rather than depending on an in-memory queue.

### PostgreSQL and pgvector

PostgreSQL is the sole runtime persistence layer. The principal structures are:

| Structure | Purpose |
| --- | --- |
| `on_record` | Generic domain records keyed by logical table and record key |
| `on_relation` | Directed typed relations between records |
| `source_embedding_pg` | Source chunks and pgvector embeddings |
| `record_embedding_pg` | Note/source-insight text and pgvector embeddings |
| `command_job` | Durable background-processing queue |
| schema migration metadata | Tracks applied PostgreSQL schema revisions |

Records retain stable logical IDs such as `notebook:abc` at the domain boundary while PostgreSQL stores the table and key in structured columns.

## Search

Search is PostgreSQL-native:

- full-text search executes in PostgreSQL;
- source semantic search uses `source_embedding_pg` and pgvector;
- note/insight semantic search uses `record_embedding_pg` and pgvector;
- relation traversal is resolved through `on_relation`.

There is no runtime query compatibility layer for the previous database engine.

## Data and failure boundaries

The database is an external service. This is intentional:

- application restart does not imply database restart;
- container replacement does not replace the database volume/service;
- API and worker may be upgraded independently of PostgreSQL within supported schema compatibility;
- startup performs schema initialization/migration before serving normal work;
- a missing or unhealthy database causes startup/health failure rather than silently falling back to another store.

## Container topology

The repository Dockerfile builds a packaged application runtime containing the API, worker, and Next.js standalone server. PostgreSQL remains outside that image.

The root `docker-compose.yml` therefore defines two services:

1. `postgres` — PostgreSQL + pgvector with a persistent volume;
2. `open_notebook` — the application runtime, dependent on database health.

FFmpeg is not bundled as an implicit application dependency. Features requiring an external media runtime must detect and use the operator-provided runtime explicitly.

## Dependency and licence boundary

CI builds the final runtime image, generates a full-image SBOM with a pinned Syft release, and validates licences by dependency domain:

- Python/npm application dependencies are held to the Vält shipped-dependency licence policy and fail closed on unknown or unreviewed copyleft/prohibited licences.
- Base operating-system packages are tracked separately because a Debian/Python runtime necessarily includes system libraries under GPL/LGPL and related licences; prohibited source-restricting/network-use licences remain blocked.
- Embedded binary/Rust component detections are subordinate inventory for their owning distributed packages and are retained in the SBOM.

This distinction is deliberate: claiming the entire Debian-derived image is permissive-only would be inaccurate.

## Legacy-store migration boundary

The former database engine is supported only as **input to the one-time migration utility** `scripts/migrate_surreal_to_postgres.py`. That script talks to the old store over its HTTP SQL endpoint and writes all live application state into PostgreSQL.

Migration coverage verifies, against a pinned legacy database version:

- domain records;
- relation endpoints and relation data;
- source embeddings;
- note/source-insight embeddings;
- refusal to import into a non-empty PostgreSQL target.

The legacy client SDK, configuration variables, container image, and raw-query compatibility API are prohibited from normal runtime paths by `scripts/check_no_surreal_runtime.py`.

## Development flow

See [Local Development Setup](development-setup.md) for process startup and quality gates, and [Dockerless PostgreSQL](dockerless-postgresql.md) for native database operation.
