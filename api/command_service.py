from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from command_queue import get_command_status, submit_command
from open_notebook.database.postgres import db_connection, ensure_schema


class CommandService:
    """Generic service layer for command operations."""

    @staticmethod
    async def submit_command_job(
        module_name: str,
        command_name: str,
        command_args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Submit a durable PostgreSQL-backed command job."""
        try:
            # Load all shipped command modules before submission so the API
            # process can persist the correct retry budget when possible.
            import commands.embedding_commands  # noqa: F401
            import commands.podcast_commands  # noqa: F401
            import commands.source_commands  # noqa: F401

            cmd_id = submit_command(module_name, command_name, command_args)
            if not cmd_id:
                raise ValueError("Failed to get command id from submit_command")
            cmd_id_str = str(cmd_id)
            logger.info(
                f"Submitted command job: {cmd_id_str} for {module_name}.{command_name}"
            )
            return cmd_id_str
        except Exception as e:
            logger.error(f"Failed to submit command job: {e}")
            raise

    @staticmethod
    async def get_command_status(job_id: str) -> Dict[str, Any]:
        """Get status of any command job."""
        try:
            status = await get_command_status(job_id)
            return {
                "job_id": job_id,
                "status": status.status if status else "unknown",
                "result": status.result if status else None,
                "error_message": status.error_message if status else None,
                "created": str(status.created) if status and status.created else None,
                "updated": str(status.updated) if status and status.updated else None,
                "progress": None,
            }
        except Exception as e:
            logger.error(f"Failed to get command status: {e}")
            raise

    @staticmethod
    async def list_command_jobs(
        module_filter: Optional[str] = None,
        command_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List recent jobs with parameterized optional filters."""
        await ensure_schema()
        if status_filter and status_filter not in {
            "queued",
            "running",
            "completed",
            "failed",
        }:
            raise ValueError(f"Invalid command status: {status_filter}")

        clauses: list[str] = []
        params: list[Any] = []
        if module_filter:
            clauses.append("app=%s")
            params.append(module_filter)
        if command_filter:
            clauses.append("command_name=%s")
            params.append(command_filter)
        if status_filter:
            clauses.append("status=%s")
            params.append(status_filter)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 500)))

        async with db_connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT id, app, command_name, status, result, error_message, "
                    "created, updated FROM command_job"
                    + where
                    + " ORDER BY created DESC LIMIT %s",
                    tuple(params),
                )
            ).fetchall()

        return [
            {
                "job_id": f"command:{row['id']}",
                "app": row["app"],
                "command": row["command_name"],
                "status": row["status"],
                "result": row.get("result"),
                "error_message": row.get("error_message"),
                "created": str(row["created"]) if row.get("created") else None,
                "updated": str(row["updated"]) if row.get("updated") else None,
            }
            for row in rows
        ]

    @staticmethod
    async def cancel_command_job(job_id: str) -> bool:
        """Cancel a queued/running job and fence any in-flight worker result."""
        value = str(job_id)
        if value.startswith("command:"):
            value = value.split(":", 1)[1]
        try:
            command_uuid = UUID(value)
        except ValueError:
            return False

        await ensure_schema()
        async with db_connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE command_job
                SET status='failed', error_message='Cancelled by user',
                    lease_until=NULL, worker_id=NULL, updated=now()
                WHERE id=%s AND status IN ('queued','running')
                """,
                (command_uuid,),
            )
            await connection.commit()
        return bool(cursor.rowcount)
