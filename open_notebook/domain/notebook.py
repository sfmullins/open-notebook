import os
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Union

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from command_queue import submit_command
from open_notebook.database.embeddings import (
    count_source_embeddings,
    delete_source_embeddings,
    text_search_pg,
    vector_search_pg,
)
from open_notebook.database.record_id import RecordID
from open_notebook.database.repository import (
    ensure_record_id,
    repo_delete_relations,
    repo_delete_where,
    repo_list,
    repo_related_records,
    repo_relation_count,
    repo_relations,
)
from open_notebook.domain.base import ObjectModel
from open_notebook.exceptions import DatabaseOperationError, InvalidInputError


class Notebook(ObjectModel):
    table_name: ClassVar[str] = "notebook"
    name: str
    description: str
    archived: Optional[bool] = False
    last_viewed_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise InvalidInputError("Notebook name cannot be empty")
        return v

    async def get_sources(self, include_full_text: bool = False) -> List["Source"]:
        try:
            rows = await repo_related_records(
                "reference",
                target=self.id,
                related_side="source",
                order_by="updated",
                descending=True,
            )
            sources = []
            for row in rows:
                data = dict(row)
                if not include_full_text:
                    data.pop("full_text", None)
                sources.append(Source(**data))
            return sources
        except Exception as e:
            logger.error(f"Error fetching sources for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def get_notes(self, include_content: bool = False) -> List["Note"]:
        try:
            rows = await repo_related_records(
                "artifact",
                target=self.id,
                related_side="source",
                order_by="updated",
                descending=True,
            )
            notes = []
            for row in rows:
                data = dict(row)
                data.pop("embedding", None)
                if not include_content:
                    data.pop("content", None)
                notes.append(Note(**data))
            return notes
        except Exception as e:
            logger.error(f"Error fetching notes for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def get_context(self) -> str:
        """
        Build long-form notebook context for podcast and LLM workflows.

        Normal list retrieval omits large source/note bodies, so this method uses
        opt-in full-content fetches and formats only substantive context blocks.
        """
        sources = await self.get_sources(include_full_text=True)
        notes = await self.get_notes(include_content=True)
        context_blocks = []

        insights_by_source = await SourceInsight.get_for_sources(
            [source.id for source in sources if source.id]
        )
        for source in sources:
            source_context = await source.get_context(
                context_size="long",
                insights=insights_by_source.get(source.id or "", []),
            )
            if isinstance(source_context, dict):
                title = source_context.get("title") or source.title or "Untitled source"
                full_text = source_context.get("full_text")
                insights = source_context.get("insights") or []

                content_parts = []
                if full_text:
                    content_parts.append(str(full_text))

                insight_lines = []
                for insight in insights:
                    if not isinstance(insight, dict):
                        continue

                    insight_content = insight.get("content")
                    if not insight_content:
                        continue

                    insight_type = insight.get("insight_type") or "Insight"
                    insight_lines.append(f"- {insight_type}: {insight_content}")

                if insight_lines:
                    content_parts.append("Insights:\n" + "\n".join(insight_lines))

                content = "\n\n".join(content_parts).strip()
            else:
                title = source.title or "Untitled source"
                content = str(source_context).strip()

            if content:
                context_blocks.append(f"## Source: {title}\n\n{content}")

        for note in notes:
            note_context = note.get_context(context_size="long")
            if isinstance(note_context, dict):
                title = note_context.get("title") or note.title or "Untitled note"
                content = note_context.get("content")
                content = str(content).strip() if content else ""
            else:
                title = note.title or "Untitled note"
                content = str(note_context).strip()

            if content:
                context_blocks.append(f"## Note: {title}\n\n{content}")

        return "\n\n".join(context_blocks)

    async def get_chat_sessions(self) -> List["ChatSession"]:
        try:
            rows = await repo_related_records(
                "refers_to",
                target=self.id,
                related_side="source",
                order_by="updated",
                descending=True,
            )
            return [ChatSession(**row) for row in rows]
        except Exception as e:
            logger.error(
                f"Error fetching chat sessions for notebook {self.id}: {str(e)}"
            )
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def get_delete_preview(self) -> Dict[str, Any]:
        """Return the records affected by deleting this notebook."""
        try:
            note_count = await repo_relation_count("artifact", target=self.id)
            references = await repo_relations("reference", target=self.id)
            exclusive_count = 0
            shared_count = 0
            for relation in references:
                source_id = relation["in"]
                assigned = await repo_relation_count("reference", source=source_id)
                if assigned <= 1:
                    exclusive_count += 1
                else:
                    shared_count += 1
            return {
                "note_count": note_count,
                "exclusive_source_count": exclusive_count,
                "shared_source_count": shared_count,
            }
        except Exception as e:
            logger.error(f"Error getting delete preview for notebook {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def delete(self, delete_exclusive_sources: bool = False) -> Dict[str, int]:
        if self.id is None:
            raise InvalidInputError("Cannot delete notebook without an ID")
        try:
            deleted_notes = 0
            deleted_sources = 0
            unlinked_sources = 0
            deleted_chat_sessions = 0

            for note in await self.get_notes():
                await note.delete()
                deleted_notes += 1
            await repo_delete_relations("artifact", target=self.id)

            references = await repo_relations("reference", target=self.id)
            if delete_exclusive_sources:
                for relation in references:
                    source_id = relation["in"]
                    assigned = await repo_relation_count("reference", source=source_id)
                    if assigned <= 1:
                        try:
                            await (await Source.get(source_id)).delete()
                            deleted_sources += 1
                        except Exception as e:
                            logger.warning(
                                f"Failed to delete exclusive source {source_id}: {e}"
                            )
                    else:
                        unlinked_sources += 1
            else:
                unlinked_sources = len(references)
            await repo_delete_relations("reference", target=self.id)

            for chat_session in await self.get_chat_sessions():
                await chat_session.delete()
                deleted_chat_sessions += 1
            await super().delete()
            return {
                "deleted_notes": deleted_notes,
                "deleted_sources": deleted_sources,
                "unlinked_sources": unlinked_sources,
                "deleted_chat_sessions": deleted_chat_sessions,
            }
        except Exception as e:
            logger.error(f"Error deleting notebook {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(f"Failed to delete notebook: {e}")


class Asset(BaseModel):
    file_path: Optional[str] = None
    url: Optional[str] = None


class SourceEmbedding(ObjectModel):
    table_name: ClassVar[str] = "source_embedding"
    content: str
    source: Optional[str] = None

    async def get_source(self) -> "Source":
        if not self.source:
            raise DatabaseOperationError(f"Embedding {self.id} has no source reference")
        return await Source.get(str(self.source))


class SourceInsight(ObjectModel):
    table_name: ClassVar[str] = "source_insight"
    insight_type: str
    content: str
    source: Optional[str] = None

    @classmethod
    async def get_for_sources(
        cls, source_ids: List[str]
    ) -> Dict[str, List["SourceInsight"]]:
        grouped: Dict[str, List[SourceInsight]] = {sid: [] for sid in source_ids if sid}
        if not grouped:
            return grouped
        try:
            rows = await repo_list(
                "source_insight", in_filters={"source": grouped.keys()}
            )
        except Exception as e:
            logger.error(f"Error batch-fetching insights for sources: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError("Failed to fetch insights for sources")
        for row in rows:
            key = str(row.get("source"))
            grouped.setdefault(key, []).append(cls(**row))
        return grouped

    async def get_source(self) -> "Source":
        if not self.source:
            raise DatabaseOperationError(f"Insight {self.id} has no source reference")
        return await Source.get(str(self.source))

    async def save_as_note(self, notebook_id: Optional[str] = None) -> Any:
        source = await self.get_source()
        note = Note(
            title=f"{self.insight_type} from source {source.title}",
            content=self.content,
        )
        await note.save()
        if notebook_id:
            await note.add_to_notebook(notebook_id)
        return note


class Source(ObjectModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    table_name: ClassVar[str] = "source"
    asset: Optional[Asset] = None
    title: Optional[str] = None
    topics: Optional[List[str]] = Field(default_factory=list)
    full_text: Optional[str] = None
    last_viewed_at: Optional[datetime] = None
    command: Optional[Union[str, RecordID]] = Field(
        default=None, description="Link to PostgreSQL command queue processing job"
    )

    @field_validator("command", mode="before")
    @classmethod
    def parse_command(cls, value):
        """Normalize command IDs from strings or serialized RecordID mappings."""
        if isinstance(value, str) and value:
            return ensure_record_id(value)
        if isinstance(value, dict):
            return RecordID.parse(value)
        return value

    @field_validator("id", mode="before")
    @classmethod
    def parse_id(cls, value):
        """Parse id field to handle both string and RecordID inputs"""
        if value is None:
            return None
        if isinstance(value, RecordID):
            return str(value)
        return str(value) if value else None

    async def get_status(self) -> Optional[str]:
        """Get the processing status of the associated command"""
        if not self.command:
            return None

        try:
            from command_queue import get_command_status

            status = await get_command_status(str(self.command))
            return status.status if status else "unknown"
        except Exception as e:
            logger.warning(f"Failed to get command status for {self.command}: {e}")
            return "unknown"

    async def get_processing_progress(self) -> Optional[Dict[str, Any]]:
        """Get detailed processing information for the associated command"""
        if not self.command:
            return None

        try:
            from command_queue import get_command_status

            status_result = await get_command_status(str(self.command))
            if not status_result:
                return None

            # Extract execution metadata if available
            result = getattr(status_result, "result", None)
            execution_metadata = (
                result.get("execution_metadata", {}) if isinstance(result, dict) else {}
            )

            return {
                "status": status_result.status,
                "started_at": execution_metadata.get("started_at"),
                "completed_at": execution_metadata.get("completed_at"),
                "error": getattr(status_result, "error_message", None),
                "result": result,
            }
        except Exception as e:
            logger.warning(f"Failed to get command progress for {self.command}: {e}")
            return None

    async def get_context(
        self,
        context_size: Literal["short", "long"] = "short",
        insights: Optional[List["SourceInsight"]] = None,
    ) -> Dict[str, Any]:
        # Callers looping over many sources can batch-fetch insights up front
        # via SourceInsight.get_for_sources() and pass them in here, instead
        # of paying a separate query per source.
        insight_objects = (
            insights if insights is not None else await self.get_insights()
        )
        insights = [insight.model_dump() for insight in insight_objects]
        if context_size == "long":
            return dict(
                id=self.id,
                title=self.title,
                insights=insights,
                full_text=self.full_text,
            )
        else:
            return dict(id=self.id, title=self.title, insights=insights)

    async def get_embedded_chunks(self) -> int:
        try:
            return await count_source_embeddings(ensure_record_id(self.id))
        except Exception as e:
            logger.error(f"Error fetching chunks count for source {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(f"Failed to count chunks for source: {str(e)}")

    async def get_insights(self) -> List[SourceInsight]:
        try:
            rows = await repo_list("source_insight", filters={"source": self.id})
            return [SourceInsight(**row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching insights for source {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError("Failed to fetch insights for source")

    async def add_to_notebook(self, notebook_id: str) -> Any:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        await Notebook.get(notebook_id)  # raises NotFoundError if invalid/missing
        return await self.relate("reference", notebook_id)

    async def vectorize(self) -> str:
        """
        Submit vectorization as a background job using the embed_source command.

        This method leverages the job-based architecture to prevent HTTP connection
        pool exhaustion when processing large documents. The embed_source command:
        1. Detects content type from file path
        2. Chunks text using content-type aware splitter
        3. Generates all embeddings in batches
        4. Bulk inserts source_embedding records

        Returns:
            str: The command/job ID that can be used to track progress via the commands API

        Raises:
            ValueError: If source has no text to vectorize
            DatabaseOperationError: If job submission fails
        """
        logger.info(f"Submitting embed_source job for source {self.id}")

        try:
            if not self.full_text or not self.full_text.strip():
                raise ValueError(f"Source {self.id} has no text to vectorize")

            # Submit the embed_source command
            command_id = submit_command(
                "open_notebook",
                "embed_source",
                {"source_id": str(self.id)},
            )

            command_id_str = str(command_id)
            logger.info(
                f"Embed source job submitted for source {self.id}: "
                f"command_id={command_id_str}"
            )

            return command_id_str

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to submit embed_source job for source {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def add_insight(self, insight_type: str, content: str) -> str:
        """
        Submit insight creation as an async command (fire-and-forget).

        Submits a create_insight command that handles database operations with
        automatic retry logic for transaction conflicts. The command also submits
        an embed_insight command for async embedding.

        This method returns immediately after submitting the command - it does NOT
        wait for the insight to be created. Use this for batch operations where
        throughput is more important than immediate confirmation.

        Args:
            insight_type: Type/category of the insight
            content: The insight content text

        Returns:
            command_id for optional tracking

        Raises:
            InvalidInputError: If insight_type or content is empty
            DatabaseOperationError: If submitting the command fails. Matches
                vectorize()'s contract - callers (transformation.py, source.py)
                run inside PostgreSQL command queue jobs whose outer exception
                handling already retries transient failures, so a swallowed
                submission failure here previously meant a transformation
                could report success while the insight was silently never
                persisted.
        """
        if not insight_type or not content:
            raise InvalidInputError("Insight type and content must be provided")

        try:
            # Submit create_insight command (fire-and-forget)
            # Command handles retries internally for transaction conflicts
            command_id = submit_command(
                "open_notebook",
                "create_insight",
                {
                    "source_id": str(self.id),
                    "insight_type": insight_type,
                    "content": content,
                },
            )
            logger.info(
                f"Submitted create_insight command {command_id} for source {self.id} "
                f"(type={insight_type})"
            )
            return str(command_id)

        except Exception as e:
            logger.exception(
                f"Error submitting create_insight for source {self.id}: {e}"
            )
            raise DatabaseOperationError(e)

    def _prepare_save_data(self) -> dict:
        """Override to ensure command field is always RecordID format for database"""
        data = super()._prepare_save_data()

        # Ensure command field is RecordID format if not None
        if data.get("command") is not None:
            data["command"] = ensure_record_id(data["command"])

        return data

    async def delete(self) -> bool:
        """Delete source and clean up associated file, embeddings, and insights."""
        # Clean up uploaded file if it exists
        if self.asset and self.asset.file_path:
            file_path = Path(self.asset.file_path)
            if file_path.exists():
                try:
                    os.unlink(file_path)
                    logger.info(f"Deleted file for source {self.id}: {file_path}")
                except Exception as e:
                    logger.warning(
                        f"Failed to delete file {file_path} for source {self.id}: {e}. "
                        "Continuing with database deletion."
                    )
            else:
                logger.debug(
                    f"File {file_path} not found for source {self.id}, skipping cleanup"
                )

        # Delete associated embeddings and insights to prevent orphaned records
        try:
            source_id = ensure_record_id(self.id)
            await delete_source_embeddings(source_id)
            await repo_delete_where("source_insight", filters={"source": self.id})
            logger.debug(f"Deleted embeddings and insights for source {self.id}")
        except Exception as e:
            logger.warning(
                f"Failed to delete embeddings/insights for source {self.id}: {e}. "
                "Continuing with source deletion."
            )

        # Call parent delete to remove database record
        return await super().delete()


class Note(ObjectModel):
    table_name: ClassVar[str] = "note"
    title: Optional[str] = None
    note_type: Optional[Literal["human", "ai"]] = None
    content: Optional[str] = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, v):
        if v is not None and not v.strip():
            raise InvalidInputError("Note content cannot be empty")
        return v

    async def save(self) -> Optional[str]:
        """
        Save the note and submit embedding command.

        Overrides ObjectModel.save() to submit an async embed_note command
        after saving, instead of inline embedding.

        Returns:
            Optional[str]: The command_id if embedding was submitted, None
                otherwise (either no content to embed, or submission failed)
        """
        # Call parent save (without embedding)
        await super().save()

        # Submit embedding command (fire-and-forget) if note has content.
        # Unlike Source.vectorize()/add_insight() (explicit, dedicated calls
        # whose whole point is the submission), this runs automatically
        # inside save() - the note itself is already durably saved above,
        # so a submission hiccup here shouldn't fail an otherwise-successful
        # save with a 500. Best-effort: log and move on.
        if self.id and self.content and self.content.strip():
            try:
                command_id = submit_command(
                    "open_notebook",
                    "embed_note",
                    {"note_id": str(self.id)},
                )
                logger.debug(f"Submitted embed_note command {command_id} for {self.id}")
                return command_id
            except Exception as e:
                logger.error(f"Failed to submit embed_note command for {self.id}: {e}")
                return None

        return None

    async def add_to_notebook(self, notebook_id: str) -> Any:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        await Notebook.get(notebook_id)  # raises NotFoundError if invalid/missing
        return await self.relate("artifact", notebook_id)

    def get_context(
        self, context_size: Literal["short", "long"] = "short"
    ) -> Dict[str, Any]:
        if context_size == "long":
            return dict(id=self.id, title=self.title, content=self.content)
        else:
            return dict(
                id=self.id,
                title=self.title,
                content=self.content[:100] if self.content else None,
            )


class ChatSession(ObjectModel):
    table_name: ClassVar[str] = "chat_session"
    nullable_fields: ClassVar[set[str]] = {"model_override"}
    title: Optional[str] = None
    model_override: Optional[str] = None

    async def relate_to_notebook(self, notebook_id: str) -> Any:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        return await self.relate("refers_to", notebook_id)

    async def relate_to_source(self, source_id: str) -> Any:
        if not source_id:
            raise InvalidInputError("Source ID must be provided")
        return await self.relate("refers_to", source_id)


async def text_search(
    keyword: str, results: int, source: bool = True, note: bool = True
):
    if not keyword:
        raise InvalidInputError("Search keyword cannot be empty")
    try:
        return await text_search_pg(keyword, results, source, note)
    except Exception as e:
        logger.error(f"Error performing text search: {str(e)}")
        logger.exception(e)
        raise DatabaseOperationError(e)


async def vector_search(
    keyword: str,
    results: int,
    source: bool = True,
    note: bool = True,
    minimum_score=0.2,
):
    if not keyword:
        raise InvalidInputError("Search keyword cannot be empty")
    try:
        from open_notebook.utils.embedding import generate_embedding

        embed = await generate_embedding(keyword)
        return await vector_search_pg(
            embed,
            results,
            source=source,
            note=note,
            minimum_score=minimum_score,
        )
    except Exception as e:
        logger.error(f"Error performing vector search: {str(e)}")
        logger.exception(e)
        raise DatabaseOperationError(e)
