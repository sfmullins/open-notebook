# Installation Guide

Open Notebook uses PostgreSQL 17 with pgvector as its runtime database. Choose the installation route based on whether you want the packaged application image or a native developer setup.

## Recommended: Docker Compose

Use the repository root `docker-compose.yml` for the supported container topology:

- PostgreSQL + pgvector as a separate service with persistent storage;
- Open Notebook as the application service;
- API on port `5055`;
- web UI on port `8502`.

See [Docker Compose](docker-compose.md).

## From source

For development and contributions, run PostgreSQL separately and start the API, worker, and frontend as native processes.

See [From Source](from-source.md).

## Dockerless / native deployment

A container runtime is not required. You can install PostgreSQL 17 + pgvector natively and run all application processes from the repository/virtual environment. Development details are in [Native / Dockerless PostgreSQL Runtime](../7-DEVELOPMENT/dockerless-postgresql.md).

## Legacy installation material

Older releases used a different database engine and some historical installation files may still exist for upgrade/migration reference. They are not valid runtime instructions for this branch. Existing data is migrated with the one-time importer described in the development documentation; the old database is not a runtime dependency after migration.

## System requirements

### Minimum

- 4 GB RAM
- 2 GB application storage plus space for user content and database data
- modern x86-64/ARM64 CPU supported by your chosen Python/Node/PostgreSQL packages

### Recommended

- 8 GB+ RAM
- 10 GB+ free storage for documents, generated media, and database growth
- multi-core CPU
- optional local-AI hardware if running models on the same host

## Required configuration

At minimum, the application needs:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-secret
DATABASE_URL=postgresql://open_notebook:password@postgres-host:5432/open_notebook
```

The repository Compose file also sets `POSTGRES_URL` to the same connection string for explicit PostgreSQL deployment compatibility.

AI-provider credentials are configured after startup in the web UI.

See the [Environment Reference](../5-CONFIGURATION/environment-reference.md) for the current runtime variables.

## After installation

1. Verify the API health endpoint and open the web UI.
2. Configure one or more AI-provider credentials under **Settings / API Keys**.
3. Discover/register the models you want to use.
4. Create a notebook and add a test source.
5. Confirm the command worker processes background work successfully.

## Production notes

- Do not expose PostgreSQL directly to untrusted networks.
- Set a strong `OPEN_NOTEBOOK_ENCRYPTION_KEY` and retain it securely; losing/changing it makes stored provider credentials unreadable.
- Set explicit database credentials instead of development defaults.
- Put the UI/API behind an appropriate TLS reverse proxy for remote access.
- Back up the PostgreSQL database and application data directory according to your recovery requirements.

Related documentation:

- [Security Hardening](../5-CONFIGURATION/security.md)
- [Reverse Proxy Setup](../5-CONFIGURATION/reverse-proxy.md)
- [Local Development Setup](../7-DEVELOPMENT/development-setup.md)
