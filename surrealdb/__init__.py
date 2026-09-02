"""Temporary source-compatibility shim for legacy ``surrealdb.RecordID`` imports.

The runtime no longer depends on the SurrealDB client.  Existing modules and tests
can continue importing ``RecordID`` while they are migrated to the database-neutral
location.
"""

from open_notebook.database.record_id import RecordID

__all__ = ["RecordID"]
