"""Database-neutral record identifiers.

Open Notebook historically exposed SurrealDB record IDs (``table:key``) throughout
its domain and API layers. The PostgreSQL migration keeps that public contract
without importing the SurrealDB client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RecordID:
    """Small compatibility type for the existing ``table:key`` identifier contract."""

    table: str
    id: str

    def __post_init__(self) -> None:
        if not _TABLE_RE.fullmatch(self.table):
            raise ValueError(f"Invalid record table: {self.table!r}")
        if not self.id or any(ch.isspace() for ch in self.id):
            raise ValueError(f"Invalid record id: {self.id!r}")

    @classmethod
    def parse(cls, value: Union[str, "RecordID"]) -> "RecordID":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str) or ":" not in value:
            raise ValueError(f"Record id must use 'table:key' form: {value!r}")
        table, key = value.split(":", 1)
        return cls(table=table, id=key)

    def __str__(self) -> str:
        return f"{self.table}:{self.id}"
