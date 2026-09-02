#!/usr/bin/env python3
"""Fail CI if a SurrealDB runtime can leak back into shipped code.

Historical documentation, tests and the one-time SurrealDB -> PostgreSQL importer
may describe or connect to SurrealDB. Normal application, deployment and package
surfaces may not import the SurrealDB SDK or ship the SurrealDB server image/binary.

This is deliberately narrower than banning the word "SurrealQL": the upstream
Open Notebook code is MIT-licensed and its query strings are not the BSL-licensed
SurrealDB server implementation. The gate protects the actual runtime/licensing
boundary while the remaining query compatibility layer is retired separately.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DIRS = (
    ROOT / "api",
    ROOT / "commands",
    ROOT / "open_notebook",
    ROOT / "deploy",
    ROOT / "examples",
)
ROOT_RUNTIME_FILES = (
    ROOT / "Dockerfile",
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.dev.yml",
    ROOT / "Makefile",
    ROOT / "dev-init.sh",
    ROOT / "start.sh",
    ROOT / "supervisord.conf",
    ROOT / "supervisord.surrealdb.conf",
)

TEXT_SUFFIXES = {".py", ".toml", ".yml", ".yaml", ".sh", ".conf", ".env", ""}

PROHIBITED_PATTERNS = (
    (re.compile(r"(?m)^\s*(?:from|import)\s+surrealdb\b"), "SurrealDB Python SDK import"),
    (re.compile(r"surrealdb/surrealdb(?::[^\s\"']+)?", re.I), "SurrealDB server image"),
    (re.compile(r"(?m)^\s*(?:COPY|ADD)\s+.*\bsurreal(?:\s|$)", re.I), "SurrealDB server binary"),
)


def runtime_files() -> list[Path]:
    files: set[Path] = {path for path in ROOT_RUNTIME_FILES if path.exists()}
    for directory in RUNTIME_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                files.add(path)
    return sorted(files)


def check_runtime() -> list[str]:
    failures: list[str] = []
    for path in runtime_files():
        # This importer is an explicit, one-time migration boundary and is not
        # part of normal application/runtime packaging.
        if path == ROOT / "scripts" / "migrate_surreal_to_postgres.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, description in PROHIBITED_PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {description}")
    return failures


def check_packaging() -> list[str]:
    failures: list[str] = []
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if re.search(r"[\"']surrealdb(?:\[[^\]]+\])?[^\"']*[\"']", pyproject, re.I):
        failures.append("pyproject.toml: SurrealDB dependency is prohibited")
    if "surrealdb*" in pyproject:
        failures.append("pyproject.toml: local surrealdb compatibility package is still shipped")

    lock = ROOT / "uv.lock"
    if lock.exists():
        lock_text = lock.read_text(encoding="utf-8")
        if re.search(r'(?m)^name\s*=\s*"surrealdb"\s*$', lock_text):
            failures.append("uv.lock: SurrealDB SDK remains in the locked dependency graph")
    return failures


def main() -> int:
    failures = check_runtime() + check_packaging()
    if failures:
        print("Surreal runtime boundary violations detected:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nAllowed exception: scripts/migrate_surreal_to_postgres.py and historical/docs/test references.\n"
            "Normal runtime and deployment surfaces must remain PostgreSQL-only.",
            file=sys.stderr,
        )
        return 1

    print("Surreal runtime boundary clean: no server image/binary or SDK dependency in shipped runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
