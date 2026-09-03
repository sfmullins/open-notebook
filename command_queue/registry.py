"""Registry facade for the PostgreSQL command queue."""

from __future__ import annotations

from dataclasses import dataclass

from command_queue import _REGISTRY


@dataclass(frozen=True, slots=True)
class CommandRegistration:
    app_id: str
    name: str


def get_all_commands() -> list[CommandRegistration]:
    """Return the commands registered in this process, sorted deterministically."""
    return [
        CommandRegistration(app_id=app, name=name)
        for app, name in sorted(_REGISTRY)
    ]
