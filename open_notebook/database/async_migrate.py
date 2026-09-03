"""PostgreSQL startup migration manager.

The historical SurrealQL files remain loaded as migration metadata because they
encode the schema version of existing installations and are covered by regression
tests.  They are *not* executed against PostgreSQL.  The PostgreSQL runtime uses
an idempotent foundation schema and records equivalent legacy versions after the
one-time data importer has established the new store.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from loguru import logger

from .postgres import db_connection, ensure_schema

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
LEGACY_SCHEMA_VERSION = 23


class AsyncMigration:
    """Historical migration metadata retained for compatibility/tests."""

    def __init__(self, sql: str) -> None:
        self.sql = sql

    @classmethod
    def from_file(cls, file_path: str | Path) -> "AsyncMigration":
        raw_content = Path(file_path).read_text(encoding="utf-8")
        lines = []
        for line in raw_content.split("\n"):
            line = line.strip()
            if line and not line.startswith("--"):
                lines.append(line)
        return cls(" ".join(lines))

    async def run(self, bump: bool = True) -> None:
        """Record the corresponding version; never execute SurrealQL on PostgreSQL."""
        if bump:
            await bump_version()
        else:
            await lower_version()


class AsyncMigrationRunner:
    def __init__(
        self,
        up_migrations: List[AsyncMigration],
        down_migrations: List[AsyncMigration],
    ) -> None:
        self.up_migrations = up_migrations
        self.down_migrations = down_migrations

    async def run_all(self) -> None:
        current_version = await get_latest_version()
        for version in range(current_version + 1, len(self.up_migrations) + 1):
            logger.info(f"Recording PostgreSQL schema compatibility version {version}")
            await _record_version(version)

    async def run_one_up(self) -> None:
        current_version = await get_latest_version()
        if current_version < len(self.up_migrations):
            await _record_version(current_version + 1)

    async def run_one_down(self) -> None:
        await lower_version()


class AsyncMigrationManager:
    """Startup migration facade used by the API lifecycle."""

    def __init__(self) -> None:
        # Keep the legacy objects available in their historical order. Several
        # regression tests intentionally inspect particular migration SQL.
        self.up_migrations = [
            AsyncMigration.from_file(MIGRATIONS_DIR / f"{version}.surrealql")
            for version in range(1, LEGACY_SCHEMA_VERSION + 1)
        ]
        self.down_migrations = [
            AsyncMigration.from_file(MIGRATIONS_DIR / f"{version}_down.surrealql")
            for version in range(1, LEGACY_SCHEMA_VERSION + 1)
        ]
        self.runner = AsyncMigrationRunner(self.up_migrations, self.down_migrations)

    async def get_current_version(self) -> int:
        return await get_latest_version()

    async def ping(self) -> None:
        await ensure_schema()
        async with db_connection() as connection:
            await connection.execute("SELECT 1")
        await self.get_current_version()

    async def needs_migration(self) -> bool:
        return await self.get_current_version() < len(self.up_migrations)

    async def run_migration_up(self) -> None:
        await ensure_schema()
        current_version = await self.get_current_version()
        logger.info(f"Current PostgreSQL compatibility version: {current_version}")
        if current_version < len(self.up_migrations):
            await self.runner.run_all()
        logger.info(
            f"PostgreSQL schema ready at compatibility version {await self.get_current_version()}"
        )


async def get_latest_version() -> int:
    versions = await get_all_versions()
    return max((row["version"] for row in versions), default=0)


async def get_all_versions() -> List[dict]:
    await ensure_schema()
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT version, name, applied_at FROM schema_migration ORDER BY version"
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def _record_version(version: int) -> None:
    await ensure_schema()
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO schema_migration(version, name)
            VALUES (%s, %s)
            ON CONFLICT(version) DO NOTHING
            """,
            (version, f"legacy-surreal-schema-{version}"),
        )
        await connection.commit()


async def bump_version() -> None:
    current_version = await get_latest_version()
    await _record_version(current_version + 1)


async def lower_version() -> None:
    current_version = await get_latest_version()
    if current_version <= 0:
        return
    async with db_connection() as connection:
        await connection.execute(
            "DELETE FROM schema_migration WHERE version=%s", (current_version,)
        )
        await connection.commit()
