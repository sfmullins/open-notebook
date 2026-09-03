#!/usr/bin/env python3
"""Enforce PostgreSQL-only shipped runtime; SurrealDB is migration-source only."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = tuple(
    ROOT / path
    for path in (
        "api",
        "commands",
        "open_notebook",
        "command_queue",
        "deploy",
        "examples",
        "scripts",
    )
)
ROOT_FILES = tuple(
    ROOT / path
    for path in (
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.override.yml.example",
        "Makefile",
        "dev-init.sh",
        "start.sh",
        "supervisord.conf",
    )
)
ALLOWED = {
    ROOT / "scripts" / "migrate_surreal_to_postgres.py",
    ROOT / "scripts" / "check_no_surreal_runtime.py",
}
SUFFIXES = {".py", ".toml", ".yml", ".yaml", ".sh", ".conf", ".env", ""}
PATTERNS = (
    (
        re.compile(r"(?m)^\s*(?:from|import)\s+surrealdb\b"),
        "SurrealDB Python SDK import",
    ),
    (
        re.compile(r"surrealdb/surrealdb(?::[^\s\"']+)?", re.I),
        "SurrealDB server image",
    ),
    (
        re.compile(r"\brepo_query\b"),
        "generic SurrealQL compatibility query API",
    ),
    (
        re.compile(r"\blegacy_query_compat\b"),
        "legacy query compatibility module",
    ),
    (
        re.compile(r"\bsurreal_commands\b"),
        "obsolete Surreal-named command package",
    ),
    (
        re.compile(r"\bSURREAL_[A-Z0-9_]+\b"),
        "obsolete SurrealDB runtime configuration",
    ),
)

ACTIVE_DOC_FILES = (
    ROOT / ".env.example",
    ROOT / ".github" / "ISSUE_TEMPLATE" / "installation_issue.yml",
    ROOT / ".github" / "RELEASE_PROCESS.md",
    ROOT / ".agents" / "skills" / "release" / "runbook.md",
    ROOT / ".claude" / "skills" / "release" / "runbook.md",
)
ACTIVE_DOC_ALLOWED = {
    ROOT / "docs" / "7-DEVELOPMENT" / "dockerless-postgresql.md",
    ROOT / "docs" / "7-DEVELOPMENT" / "decisions" / "ADR-001-surrealdb.md",
}
ACTIVE_DOC_PATTERNS = (
    (
        re.compile(r"\bSURREAL_[A-Z0-9_]+\b"),
        "obsolete SurrealDB runtime configuration in active documentation",
    ),
    (
        re.compile(r"surrealdb/surrealdb(?::[^\s`\"']+)?", re.I),
        "SurrealDB runtime image in active documentation",
    ),
)


def files() -> list[Path]:
    found = {path for path in ROOT_FILES if path.exists()}
    for directory in RUNTIME_DIRS:
        if directory.exists():
            found.update(
                path
                for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() in SUFFIXES
            )
    return sorted(found)


def active_docs() -> list[Path]:
    found = {path for path in ACTIVE_DOC_FILES if path.exists()}
    docs = ROOT / "docs"
    if docs.exists():
        found.update(path for path in docs.rglob("*.md") if path.is_file())
    return sorted(found)


def dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0].strip().lower()


def main() -> int:
    failures: list[str] = []
    for path in files():
        if path in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, description in PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {description}")

    for path in active_docs():
        if path in ACTIVE_DOC_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, description in ACTIVE_DOC_PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {description}")

    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    dependencies = project.get("project", {}).get("dependencies", [])
    for prohibited in ("surrealdb", "surreal-commands", "surreal_commands"):
        if any(
            dependency_name(str(requirement)) == prohibited
            for requirement in dependencies
        ):
            failures.append(f"pyproject.toml: prohibited dependency {prohibited}")

    legacy_parser = ROOT / "open_notebook/database/legacy_query_compat.py"
    if legacy_parser.exists():
        failures.append("legacy_query_compat.py must not exist")

    if failures:
        print(
            "PostgreSQL-only runtime boundary violations detected:",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("PostgreSQL-only runtime and active-document boundary clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
