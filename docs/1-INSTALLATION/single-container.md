# Single-container installation

The legacy single-container database layout is not supported by the PostgreSQL-native runtime.

Use:

- [Docker Compose](docker-compose.md) — recommended; PostgreSQL/pgvector and Open Notebook are separate services.
- [From source](from-source.md) — application processes against an existing PostgreSQL/pgvector instance.
- [Windows native](windows-native.md) — Windows-native guidance.

Separating the database from the application container keeps application image replacement, rollback, and recreation out of the data lifecycle.
