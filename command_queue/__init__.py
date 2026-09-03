"""PostgreSQL-backed durable command queue for Open Notebook.

The queue is independent of the application domain and uses PostgreSQL for durable jobs. Jobs are durable, claimed with
``FOR UPDATE SKIP LOCKED`` and protected by renewable leases so a crashed worker can
be recovered without duplicating healthy long-running work.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import socket
import threading
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Type
from uuid import UUID, uuid4

import psycopg
from loguru import logger
from pydantic import BaseModel, ConfigDict

from open_notebook.database.postgres import (
    db_connection,
    ensure_schema,
    get_database_url,
    normalize_json,
)


class ExecutionContext(BaseModel):
    command_id: str


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    execution_context: Optional[ExecutionContext] = None


class CommandOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class CommandStatus(BaseModel):
    id: str
    status: str
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    created: Optional[datetime] = None
    updated: Optional[datetime] = None

    def is_success(self) -> bool:
        return self.status == "completed"


class _RegisteredCommand:
    def __init__(
        self,
        func: Callable[[Any], Awaitable[Any]],
        input_type: Type[CommandInput],
        retry: Optional[dict[str, Any]],
    ) -> None:
        self.func = func
        self.input_type = input_type
        self.retry = retry or {}


_REGISTRY: dict[tuple[str, str], _RegisteredCommand] = {}
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY_SYNC = False


def _queue_schema_sync() -> None:
    global _SCHEMA_READY_SYNC
    if _SCHEMA_READY_SYNC:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY_SYNC:
            return
        with psycopg.connect(get_database_url(), autocommit=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS command_job (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    app text NOT NULL,
                    command_name text NOT NULL,
                    input jsonb NOT NULL,
                    status text NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','completed','failed')),
                    attempt integer NOT NULL DEFAULT 0,
                    max_attempts integer NOT NULL DEFAULT 1,
                    run_after timestamptz NOT NULL DEFAULT now(),
                    lease_until timestamptz,
                    worker_id text,
                    result jsonb,
                    error_message text,
                    created timestamptz NOT NULL DEFAULT now(),
                    updated timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS command_job_claim_idx "
                "ON command_job(status, run_after, lease_until, created)"
            )
        _SCHEMA_READY_SYNC = True


def command(
    name: str,
    *,
    app: str,
    retry: Optional[dict[str, Any]] = None,
) -> Callable[[Callable[[Any], Awaitable[Any]]], Callable[[Any], Awaitable[Any]]]:
    """Register an async command handler using the legacy decorator contract."""

    def decorator(func: Callable[[Any], Awaitable[Any]]):
        params = list(inspect.signature(func).parameters.values())
        annotation = params[0].annotation if params else CommandInput
        input_type: Type[CommandInput]
        if isinstance(annotation, type) and issubclass(annotation, CommandInput):
            input_type = annotation
        else:
            input_type = CommandInput
        _REGISTRY[(app, name)] = _RegisteredCommand(func, input_type, retry)
        return func

    return decorator


def _registered_max_attempts(registered: _RegisteredCommand | None) -> int:
    if registered is None:
        return 1
    return max(1, int(registered.retry.get("max_attempts", 1)))


def _max_attempts_for(app: str, name: str) -> int:
    return _registered_max_attempts(_REGISTRY.get((app, name)))


def submit_command(app: str, command_name: str, input_data: Dict[str, Any]) -> str:
    """Durably enqueue a command and return its stable ``command:<uuid>`` ID."""
    _queue_schema_sync()
    command_id = uuid4()
    payload = normalize_json(input_data)
    with psycopg.connect(get_database_url(), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO command_job(id, app, command_name, input, max_attempts)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            """,
            (
                command_id,
                app,
                command_name,
                json.dumps(payload),
                _max_attempts_for(app, command_name),
            ),
        )
    return f"command:{command_id}"


async def submit_command_async(
    app: str, command_name: str, input_data: Dict[str, Any]
) -> str:
    await ensure_schema()
    command_id = uuid4()
    payload = normalize_json(input_data)
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO command_job(id, app, command_name, input, max_attempts)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            """,
            (
                command_id,
                app,
                command_name,
                json.dumps(payload),
                _max_attempts_for(app, command_name),
            ),
        )
        await connection.commit()
    return f"command:{command_id}"


def _uuid_from_command_id(command_id: str) -> UUID:
    value = str(command_id)
    if value.startswith("command:"):
        value = value.split(":", 1)[1]
    return UUID(value)


def _status_from_row(row: dict[str, Any]) -> CommandStatus:
    return CommandStatus(
        id=f"command:{row['id']}",
        status=row["status"],
        result=row.get("result"),
        error_message=row.get("error_message"),
        created=row.get("created"),
        updated=row.get("updated"),
    )


async def get_command_status(command_id: str) -> Optional[CommandStatus]:
    await ensure_schema()
    try:
        job_id = _uuid_from_command_id(command_id)
    except (ValueError, TypeError):
        return None
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                "SELECT id, status, result, error_message, created, updated "
                "FROM command_job WHERE id=%s",
                (job_id,),
            )
        ).fetchone()
    return _status_from_row(dict(row)) if row else None


async def get_command_statuses(command_ids: list[str]) -> dict[str, CommandStatus]:
    """Batch-fetch command state without an N+1 query pattern."""
    await ensure_schema()
    parsed: list[UUID] = []
    for command_id in command_ids:
        try:
            parsed.append(_uuid_from_command_id(command_id))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return {}
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT id, status, result, error_message, created, updated "
                "FROM command_job WHERE id = ANY(%s)",
                (parsed,),
            )
        ).fetchall()
    statuses = [_status_from_row(dict(row)) for row in rows]
    return {status.id: status for status in statuses}


async def _set_completed(
    job_id: UUID,
    output: Any,
    started: datetime,
    worker_id: str,
) -> bool:
    """Commit a result only if this worker still owns the running lease."""
    completed = datetime.now(timezone.utc)
    if isinstance(output, BaseModel):
        payload = output.model_dump(mode="json")
    elif isinstance(output, dict):
        payload = normalize_json(output)
    else:
        payload = {"value": normalize_json(output)}
    payload["execution_metadata"] = {
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
    }
    async with db_connection() as connection:
        cursor = await connection.execute(
            """
            UPDATE command_job
            SET status='completed', result=%s::jsonb, error_message=NULL,
                lease_until=NULL, worker_id=NULL, updated=now()
            WHERE id=%s AND status='running' AND worker_id=%s
            """,
            (json.dumps(payload), job_id, worker_id),
        )
        await connection.commit()
        return bool(cursor.rowcount)


async def _set_failed(job_id: UUID, message: str, worker_id: str | None = None) -> bool:
    if worker_id is None:
        where = "id=%s"
        values: tuple[Any, ...] = (message[:4000], job_id)
    else:
        where = "id=%s AND status='running' AND worker_id=%s"
        values = (message[:4000], job_id, worker_id)
    async with db_connection() as connection:
        cursor = await connection.execute(
            f"""
            UPDATE command_job
            SET status='failed', error_message=%s, lease_until=NULL,
                worker_id=NULL, updated=now()
            WHERE {where}
            """,
            values,
        )
        await connection.commit()
        return bool(cursor.rowcount)


async def _invoke_job(job: dict[str, Any]) -> None:
    key = (job["app"], job["command_name"])
    registered = _REGISTRY.get(key)
    job_id: UUID = job["id"]
    worker_id = str(job.get("worker_id") or "")
    if not registered:
        await _set_failed(
            job_id,
            f"Command handler not registered: {key[0]}/{key[1]}",
            worker_id or None,
        )
        return

    # API-side submission can happen before command modules are registered in
    # that process. The worker does have decorator metadata, so upgrade the
    # persisted retry budget when execution begins.
    registered_max = _registered_max_attempts(registered)
    if int(job.get("max_attempts") or 1) < registered_max:
        job["max_attempts"] = registered_max
        async with db_connection() as connection:
            await connection.execute(
                "UPDATE command_job SET max_attempts=%s "
                "WHERE id=%s AND status='running' AND worker_id=%s",
                (registered_max, job_id, worker_id),
            )
            await connection.commit()

    started = datetime.now(timezone.utc)
    input_payload = dict(job.get("input") or {})
    input_payload["execution_context"] = {"command_id": f"command:{job_id}"}
    try:
        input_model = registered.input_type(**input_payload)
        output = await registered.func(input_model)
    except Exception as exc:
        await _handle_failure(job, registered, exc)
        return

    if not await _set_completed(job_id, output, started, worker_id):
        logger.warning(
            f"Discarded result for command {job_id}: worker {worker_id} no longer owns the lease"
        )


def _is_stop_exception(registered: _RegisteredCommand, exc: Exception) -> bool:
    stop_on = registered.retry.get("stop_on", [])
    return any(isinstance(exc, cls) for cls in stop_on if isinstance(cls, type))


async def _handle_failure(
    job: dict[str, Any], registered: _RegisteredCommand, exc: Exception
) -> None:
    attempt = int(job.get("attempt") or 1)
    max_attempts = max(
        int(job.get("max_attempts") or 1), _registered_max_attempts(registered)
    )
    worker_id = str(job.get("worker_id") or "")
    message = f"{type(exc).__name__}: {exc}"
    if _is_stop_exception(registered, exc) or attempt >= max_attempts:
        logger.error(f"Command {job['id']} failed permanently: {message}")
        await _set_failed(job["id"], message, worker_id or None)
        return

    wait_min = float(registered.retry.get("wait_min", 1))
    wait_max = float(registered.retry.get("wait_max", 60))
    base = min(wait_max, wait_min * (2 ** max(0, attempt - 1)))
    delay = base + random.uniform(0, min(base * 0.25, 5.0))
    logger.debug(
        f"Command {job['id']} retry {attempt}/{max_attempts} in {delay:.1f}s: {message}"
    )
    async with db_connection() as connection:
        cursor = await connection.execute(
            """
            UPDATE command_job
            SET status='queued', run_after=now() + (%s * interval '1 second'),
                max_attempts=%s, error_message=%s, lease_until=NULL,
                worker_id=NULL, updated=now()
            WHERE id=%s AND status='running' AND worker_id=%s
            """,
            (delay, max_attempts, message[:4000], job["id"], worker_id),
        )
        await connection.commit()
    if not cursor.rowcount:
        logger.warning(
            f"Discarded retry transition for command {job['id']}: lease ownership was lost"
        )


async def _expire_exhausted_jobs() -> None:
    """Fail abandoned jobs that have already consumed their retry budget."""
    async with db_connection() as connection:
        await connection.execute(
            """
            UPDATE command_job
            SET status='failed',
                error_message=COALESCE(error_message, 'Worker lease expired after final attempt'),
                lease_until=NULL, worker_id=NULL, updated=now()
            WHERE status='running' AND lease_until < now() AND attempt >= max_attempts
            """
        )
        await connection.execute(
            """
            UPDATE command_job
            SET status='failed',
                error_message=COALESCE(error_message, 'Retry budget exhausted'),
                updated=now()
            WHERE status='queued' AND attempt >= max_attempts
            """
        )
        await connection.commit()


async def claim_job(
    worker_id: str, lease_seconds: int = 300
) -> Optional[dict[str, Any]]:
    """Atomically claim the oldest runnable job, including abandoned leases."""
    await ensure_schema()
    await _expire_exhausted_jobs()
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                """
                WITH candidate AS (
                    SELECT id FROM command_job
                    WHERE (
                        status='queued' AND run_after <= now() AND attempt < max_attempts
                    ) OR (
                        status='running' AND lease_until IS NOT NULL
                        AND lease_until < now() AND attempt < max_attempts
                    )
                    ORDER BY created
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE command_job AS job
                SET status='running', attempt=job.attempt + 1,
                    lease_until=now() + (%s * interval '1 second'),
                    worker_id=%s, updated=now()
                FROM candidate
                WHERE job.id=candidate.id
                RETURNING job.*
                """,
                (lease_seconds, worker_id),
            )
        ).fetchone()
        await connection.commit()
    return dict(row) if row else None


async def heartbeat(job_id: UUID, worker_id: str, lease_seconds: int = 300) -> bool:
    async with db_connection() as connection:
        cursor = await connection.execute(
            """
            UPDATE command_job
            SET lease_until=now() + (%s * interval '1 second'), updated=now()
            WHERE id=%s AND status='running' AND worker_id=%s
            """,
            (lease_seconds, job_id, worker_id),
        )
        await connection.commit()
        return bool(cursor.rowcount)


async def run_claimed_job(
    job: dict[str, Any], worker_id: str, lease_seconds: int = 300
) -> None:
    """Execute one claimed job while renewing its lease in the background."""
    job_task = asyncio.create_task(_invoke_job(job))

    async def renew() -> None:
        interval = max(5, lease_seconds // 3)
        while not job_task.done():
            await asyncio.sleep(interval)
            if job_task.done():
                return
            try:
                owned = await heartbeat(job["id"], worker_id, lease_seconds)
            except Exception as exc:
                # A transient DB outage should not immediately kill useful work;
                # once connectivity returns, ownership is re-checked. If another
                # worker reclaimed the expired lease, the next heartbeat returns
                # false and the old task is cancelled/fenced.
                logger.warning(f"Heartbeat failed for command {job['id']}: {exc}")
                continue
            if not owned:
                logger.warning(
                    f"Lost lease for command {job['id']}; cancelling stale worker task"
                )
                job_task.cancel()
                return

    renew_task = asyncio.create_task(renew())
    try:
        await job_task
    except asyncio.CancelledError:
        logger.warning(f"Command {job['id']} execution cancelled after lease loss")
    finally:
        renew_task.cancel()
        with suppress(asyncio.CancelledError):
            await renew_task


async def _claim_specific(job_id: UUID, worker_id: str) -> dict[str, Any] | None:
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                """
                UPDATE command_job
                SET status='running', attempt=attempt+1, worker_id=%s,
                    lease_until=now() + interval '1 hour', updated=now()
                WHERE id=%s AND status='queued' AND run_after <= now()
                  AND attempt < max_attempts
                RETURNING *
                """,
                (worker_id, job_id),
            )
        ).fetchone()
        await connection.commit()
    return dict(row) if row else None


async def _execute_specific(command_id: str) -> CommandStatus:
    """Execute one command inline, including its configured retry schedule."""
    await ensure_schema()
    job_id = _uuid_from_command_id(command_id)
    worker_id = f"inline:{socket.gethostname()}:{threading.get_ident()}:{job_id}"

    while True:
        status = await get_command_status(command_id)
        if status is None:
            raise RuntimeError(f"Command disappeared: {command_id}")
        if status.status in {"completed", "failed"}:
            return status

        claimed = await _claim_specific(job_id, worker_id)
        if claimed is not None:
            await _invoke_job(claimed)
            continue

        # It is either waiting for retry run_after or was claimed by a regular
        # worker in the small race between submission and inline claim. Poll at
        # a low rate; the outer wait_for enforces the caller's timeout.
        await asyncio.sleep(0.1)


def execute_command_sync(
    app: str,
    command_name: str,
    input_data: Dict[str, Any],
    timeout: Optional[float] = None,
) -> CommandStatus:
    command_id = submit_command(app, command_name, input_data)

    async def execute() -> CommandStatus:
        if timeout is None:
            return await _execute_specific(command_id)
        return await asyncio.wait_for(_execute_specific(command_id), timeout=timeout)

    return asyncio.run(execute())


__all__ = [
    "CommandInput",
    "CommandOutput",
    "CommandStatus",
    "ExecutionContext",
    "claim_job",
    "command",
    "execute_command_sync",
    "get_command_status",
    "get_command_statuses",
    "heartbeat",
    "run_claimed_job",
    "submit_command",
    "submit_command_async",
]
