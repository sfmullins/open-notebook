"""Compatibility facade for the former surreal-commands registry module."""

from __future__ import annotations

from dataclasses import dataclass

from surreal_commands import _REGISTRY


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
