#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "surreal_commands"
NEW = ROOT / "command_queue"

if OLD.exists() and not NEW.exists():
    shutil.move(str(OLD), str(NEW))

TEXT_SUFFIXES = {
    ".py", ".toml", ".yml", ".yaml", ".md", ".sh", ".conf", ".service",
    ".json", ".txt", ".env", ".example", "",
}
REPLACEMENTS = (
    ("surreal_commands.worker", "command_queue.worker"),
    ("surreal_commands.registry", "command_queue.registry"),
    ("surreal_commands", "command_queue"),
    ("surreal-commands-worker", "open-notebook-command-worker"),
    ("surreal-commands", "PostgreSQL command queue"),
)

for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts:
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    if path.resolve() == Path(__file__).resolve():
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    updated = text
    for old, new in REPLACEMENTS:
        updated = updated.replace(old, new)
    if updated != text:
        path.write_text(updated, encoding="utf-8")

# Fix internal imports and stale compatibility wording after the directory move.
init_path = NEW / "__init__.py"
if init_path.exists():
    text = init_path.read_text(encoding="utf-8")
    text = text.replace(
        "PostgreSQL-backed compatibility layer for the former ``PostgreSQL command queue`` API.",
        "PostgreSQL-backed durable command queue for Open Notebook.",
    )
    text = text.replace(
        "Keeping the import surface stable avoids coupling command/domain code to the queue\nbackend while removing SurrealDB from the runtime.",
        "The queue is independent of the application domain and uses PostgreSQL for durable jobs.",
    )
    init_path.write_text(text, encoding="utf-8")

registry_path = NEW / "registry.py"
if registry_path.exists():
    text = registry_path.read_text(encoding="utf-8")
    text = text.replace(
        '"""Compatibility facade for the former PostgreSQL command queue registry module."""',
        '"""Registry facade for the PostgreSQL command queue."""',
    )
    registry_path.write_text(text, encoding="utf-8")

# Remove this one-shot helper and its workflow from the commit it produces.
workflow = ROOT / ".github" / "workflows" / "pr2-one-shot-rename.yml"
if workflow.exists():
    workflow.unlink()
Path(__file__).unlink()
