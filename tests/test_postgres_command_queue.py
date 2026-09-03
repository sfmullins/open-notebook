"""Integration coverage for PostgreSQL command queue recovery semantics."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from open_notebook.database.postgres import db_connection, ensure_schema
from command_queue import claim_job, heartbeat


async def _reset_queue() -> None:
    await ensure_schema()
    async with db_connection() as connection:
        await connection.execute("TRUNCATE TABLE command_job")
        await connection.commit()


@pytest.mark.asyncio
async def test_claim_recovers_expired_running_job() -> None:
    await _reset_queue()
    job_id = uuid4()
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO command_job(
                id, app, command_name, input, status, attempt, max_attempts,
                worker_id, lease_until
            ) VALUES (%s, 'open_notebook', 'noop', '{}'::jsonb, 'running', 1, 3,
                      'dead-worker', now() - interval '1 second')
            """,
            (job_id,),
        )
        await connection.commit()

    claimed = await claim_job("replacement-worker", lease_seconds=60)

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert claimed["worker_id"] == "replacement-worker"
    assert claimed["attempt"] == 2
    assert claimed["lease_until"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_claim_does_not_steal_live_running_job() -> None:
    await _reset_queue()
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO command_job(
                app, command_name, input, status, attempt, max_attempts,
                worker_id, lease_until
            ) VALUES ('open_notebook', 'noop', '{}'::jsonb, 'running', 1, 3,
                      'healthy-worker', now() + interval '10 minutes')
            """
        )
        await connection.commit()

    assert await claim_job("other-worker", lease_seconds=60) is None


@pytest.mark.asyncio
async def test_heartbeat_extends_owned_lease() -> None:
    await _reset_queue()
    job_id = uuid4()
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO command_job(
                id, app, command_name, input, status, attempt, max_attempts,
                worker_id, lease_until
            ) VALUES (%s, 'open_notebook', 'noop', '{}'::jsonb, 'running', 1, 3,
                      'worker-1', now() + interval '5 seconds')
            """,
            (job_id,),
        )
        before_row = await (
            await connection.execute(
                "SELECT lease_until FROM command_job WHERE id=%s", (job_id,)
            )
        ).fetchone()
        assert before_row is not None
        before = before_row["lease_until"]
        await connection.commit()

    assert await heartbeat(job_id, "worker-1", lease_seconds=120) is True

    async with db_connection() as connection:
        after_row = await (
            await connection.execute(
                "SELECT lease_until FROM command_job WHERE id=%s", (job_id,)
            )
        ).fetchone()
        assert after_row is not None
        after = after_row["lease_until"]

    assert after > before


@pytest.mark.asyncio
async def test_heartbeat_cannot_extend_another_workers_lease() -> None:
    await _reset_queue()
    job_id = uuid4()
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO command_job(
                id, app, command_name, input, status, attempt, max_attempts,
                worker_id, lease_until
            ) VALUES (%s, 'open_notebook', 'noop', '{}'::jsonb, 'running', 1, 3,
                      'worker-1', now() + interval '1 minute')
            """,
            (job_id,),
        )
        await connection.commit()

    assert await heartbeat(job_id, "worker-2", lease_seconds=120) is False
