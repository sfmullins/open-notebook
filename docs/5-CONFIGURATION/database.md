# Database — PostgreSQL / pgvector

Open Notebook uses PostgreSQL as its runtime database. The database must provide the `vector` extension from pgvector.

## Docker Compose

The repository `docker-compose.yml` starts PostgreSQL/pgvector automatically:

```dotenv
POSTGRES_USER=open_notebook
POSTGRES_PASSWORD=open_notebook
POSTGRES_DB=open_notebook
```

For native or external deployments set the application DSN explicitly:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

`POSTGRES_URL` is an alias used by deployment tooling. When both are set, keep it identical to `DATABASE_URL`.

A container cannot reach host PostgreSQL through its own `127.0.0.1`; use a deliberately reachable host address and appropriate PostgreSQL authentication/firewall rules. Never expose port 5432 publicly.

The application initializes its schema automatically. It does not install or administer PostgreSQL.

## Legacy migration

SurrealDB is supported only as a migration source. Use `scripts/migrate_surreal_to_postgres.py`; see [Dockerless PostgreSQL](../7-DEVELOPMENT/dockerless-postgresql.md). The importer refuses to write to a non-empty PostgreSQL target.
