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

from loguru import logger
from pydantic import BaseModel, ConfigDict

from open_notebook.database.postgres import db_connection, ensure_schema, normalize_json


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
        input_model: Type[BaseModel],
        output_model: Optional[Type[BaseModel]],
        timeout: int,
        retries: int,
    ) -> None:
        self.func = func
        self.input_model = input_model
        self.output_model = output_model
        self.timeout = timeout
        self.retries = retries


COMMAND_REGISTRY: Dict[tuple[str, str], _RegisteredCommand] = {}


def _run_async_submission(factory: Callable[[], Awaitable[str]]) -> str:
    """Run an async submission from either sync or async-hosted call sites.

    Legacy domain APIs still expose a synchronous ``submit_command`` helper.
    When called inside a running event loop, execute the short database bridge
    in a dedicated thread rather than nesting event loops in the caller thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: list[str] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=runner, name="open-notebook-command-submit")
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    if not result:
        raise RuntimeError("Command submission thread returned no result")
    return result[0]


def command(
    app: str,
    name: str,
    input_model: Type[BaseModel],
    output_model: Optional[Type[BaseModel]] = None,
    timeout: int = 300,
    retries: int = 0,
):
    """Register an async command handler."""

    def decorator(func: Callable[[Any], Awaitable[Any]]):
        COMMAND_REGISTRY[(app, name)] = _RegisteredCommand(
            func=func,
            input_model=input_model,
            output_model=output_model,
            timeout=timeout,
            retries=retries,
        )
        return func

    return decorator


def submit_command(app: str, command_name: str, input_data: Dict[str, Any]) -> str:
    """Durably enqueue a command from a synchronous compatibility call site."""
    return _run_async_submission(
        lambda: submit_command_async(app, command_name, input_data)
    )


async def submit_command_async(
    app: str, command_name: str, input_data: Dict[str, Any]
) -> str:
    await ensure_schema()
    job_id = uuid4()
    payload = normalize_json(input_data)
    registered = COMMAND_REGISTRY.get((app, command_name))
    max_attempts = (registered.retries + 1) if registered else 1
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO command_job(id, app, command_name, input, max_attempts)
            VALUES (%s,%s,%s,%s::jsonb,%s)
            """,
            (job_id, app, command_name, json.dumps(payload), max_attempts),
        )
        await connection.commit()
    return str(job_id)


async def get_command_status(command_id: str) -> Optional[CommandStatus]:
    await ensure_schema()
    try:
        job_id = UUID(command_id)
    except ValueError:
        return None
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                """
                SELECT id, status, result, error_message, created, updated
                FROM command_job WHERE id=%s
                """,
                (job_id,),
            )
        ).fetchone()
    if not row:
        return None
    return CommandStatus(
        id=str(row["id"]),
        status=row["status"],
        result=row["result"],
        error_message=row["error_message"],
        created=row["created"],
        updated=row["updated"],
    )


async def _claim_job(worker_id: str, lease_seconds: int) -> Optional[dict[str, Any]]:
    await ensure_schema()
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM command_job
                    WHERE (
                        status='queued' AND run_after <= now()
                    ) OR (
                        status='running' AND lease_until < now()
                    )
                    ORDER BY created
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE command_job AS job
                SET status='running',
                    worker_id=%s,
                    lease_until=now() + (%s * interval '1 second'),
                    attempt=job.attempt + 1,
                    updated=now()
                FROM candidate
                WHERE job.id=candidate.id
                RETURNING job.*
                """,
                (worker_id, lease_seconds),
            )
        ).fetchone()
        await connection.commit()
    return dict(row) if row else None


async def _renew_lease(job_id: UUID, worker_id: str, lease_seconds: int) -> bool:
    async with db_connection() as connection:
        result = await connection.execute(
            """
            UPDATE command_job
            SET lease_until=now() + (%s * interval '1 second'), updated=now()
            WHERE id=%s AND worker_id=%s AND status='running'
            """,
            (lease_seconds, job_id, worker_id),
        )
        await connection.commit()
    return bool(result.rowcount)


async def _complete_job(
    job_id: UUID, worker_id: str, result: Any, *, error_message: str | None = None
) -> None:
    payload = normalize_json(result) if error_message is None else None
    async with db_connection() as connection:
        if error_message is None:
            await connection.execute(
                """
                UPDATE command_job
                SET status='completed', result=%s::jsonb, error_message=NULL,
                    lease_until=NULL, updated=now()
                WHERE id=%s AND worker_id=%s
                """,
                (json.dumps(payload), job_id, worker_id),
            )
        else:
            await connection.execute(
                """
                UPDATE command_job
                SET status='failed', error_message=%s,
                    lease_until=NULL, updated=now()
                WHERE id=%s AND worker_id=%s
                """,
                (error_message, job_id, worker_id),
            )
        await connection.commit()


async def _retry_job(job: dict[str, Any], worker_id: str, error_message: str) -> None:
    attempt = int(job["attempt"])
    max_attempts = int(job["max_attempts"])
    if attempt >= max_attempts:
        await _complete_job(
            job["id"], worker_id, None, error_message=error_message
        )
        return
    delay = min(60.0, (2 ** max(0, attempt - 1)) + random.random())
    async with db_connection() as connection:
        await connection.execute(
            """
            UPDATE command_job
            SET status='queued', run_after=now() + (%s * interval '1 second'),
                lease_until=NULL, worker_id=NULL, error_message=%s, updated=now()
            WHERE id=%s AND worker_id=%s
            """,
            (delay, error_message, job["id"], worker_id),
        )
        await connection.commit()


async def _heartbeat(
    job_id: UUID, worker_id: str, lease_seconds: int, interval: float
) -> None:
    while True:
        await asyncio.sleep(interval)
        if not await _renew_lease(job_id, worker_id, lease_seconds):
            return


async def _run_job(job: dict[str, Any], worker_id: str, lease_seconds: int) -> None:
    registration = COMMAND_REGISTRY.get((job["app"], job["command_name"]))
    if registration is None:
        await _retry_job(
            job,
            worker_id,
            f"Command not registered: {job['app']}.{job['command_name']}",
        )
        return

    try:
        payload = dict(job["input"])
        execution_context = payload.get("execution_context") or {}
        execution_context["command_id"] = str(job["id"])
        payload["execution_context"] = execution_context
        input_value = registration.input_model.model_validate(payload)
    except Exception as exc:
        await _complete_job(
            job["id"], worker_id, None, error_message=f"Invalid command input: {exc}"
        )
        return

    heartbeat = asyncio.create_task(
        _heartbeat(
            job["id"], worker_id, lease_seconds, max(1.0, lease_seconds / 3.0)
        )
    )
    try:
        result = await asyncio.wait_for(
            registration.func(input_value), timeout=registration.timeout
        )
        if registration.output_model is not None:
            result = registration.output_model.model_validate(result).model_dump(
                mode="json"
            )
        elif isinstance(result, BaseModel):
            result = result.model_dump(mode="json")
        await _complete_job(job["id"], worker_id, result)
    except Exception as exc:
        logger.exception(
            "Command failed: {}.{} job={}",
            job["app"],
            job["command_name"],
            job["id"],
        )
        await _retry_job(job, worker_id, str(exc))
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def run_worker(
    *,
    max_tasks: int = 5,
    poll_interval: float = 0.5,
    lease_seconds: int = 120,
    stop_event: asyncio.Event | None = None,
) -> None:
    worker_id = f"{socket.gethostname()}:{uuid4()}"
    running: set[asyncio.Task[None]] = set()
    logger.info("PostgreSQL command worker started: {}", worker_id)

    while stop_event is None or not stop_event.is_set():
        finished = {task for task in running if task.done()}
        for task in finished:
            running.remove(task)
            if not task.cancelled() and (error := task.exception()) is not None:
                logger.error("Worker task failed outside command handler: {}", error)

        while len(running) < max_tasks:
            job = await _claim_job(worker_id, lease_seconds)
            if not job:
                break
            running.add(asyncio.create_task(_run_job(job, worker_id, lease_seconds)))

        if running:
            done, _ = await asyncio.wait(
                running, timeout=poll_interval, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                running.discard(task)
                if not task.cancelled() and (error := task.exception()) is not None:
                    logger.error("Worker task failed outside command handler: {}", error)
        else:
            await asyncio.sleep(poll_interval)

    if running:
        await asyncio.gather(*running, return_exceptions=True)


def load_command_modules(module_names: list[str]) -> None:
    """Import command modules so decorators populate the registry."""
    for module_name in module_names:
        inspect.importlib.import_module(module_name)
