#!/usr/bin/env python3
"""Fail CI if SurrealDB runtime/compatibility residue re-enters shipped code.

The one-time SurrealDB -> PostgreSQL importer is the sole runtime-tree exception:
it must understand the historical source database. Normal application,
deployment, packaging and examples must be PostgreSQL-native and may not expose
SurrealDB configuration, SDK/server dependencies, or generic SurrealQL query
compatibility.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DIRS = (
    ROOT / "api",
    ROOT / "commands",
    ROOT / "open_notebook",
    ROOT / "command_queue",
    ROOT / "deploy",
    ROOT / "examples",
    ROOT / "scripts",
)
ROOT_RUNTIME_FILES = (
    ROOT / "Dockerfile",
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.dev.yml",
    ROOT / "docker-compose.override.yml.example",
    ROOT / "Makefile",
    ROOT / "dev-init.sh",
    ROOT / "start.sh",
    ROOT / "supervisord.conf",
)
ALLOWED_RUNTIME_REFERENCES = {
    ROOT / "scripts" / "migrate_surreal_to_postgres.py",
    ROOT / "scripts" / "check_no_surreal_runtime.py",
}
TEXT_SUFFIXES = {".py", ".toml", ".yml", ".yaml", ".sh", ".conf", ".env", ""}

PROHIBITED_PATTERNS = (
    (re.compile(r"(?m)^\s*(?:from|import)\s+surrealdb\b"), "SurrealDB Python SDK import"),
    (re.compile(r"surrealdb/surrealdb(?::[^\s\"']+)?", re.I), "SurrealDB server image"),
    (re.compile(r"(?m)^\s*(?:COPY|ADD)\s+.*\bsurreal(?:\s|$)", re.I), "SurrealDB server binary"),
    (re.compile(r"\brepo_query\b"), "generic SurrealQL compatibility query API"),
    (re.compile(r"\blegacy_query_compat\b"), "legacy query compatibility module"),
    (re.compile(r"\bsurreal_commands\b"), "obsolete Surreal-named command package"),
    (re.compile(r"\bSURREAL_[A-Z0-9_]+\b"), "obsolete SurrealDB runtime configuration"),
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
        if path in ALLOWED_RUNTIME_REFERENCES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, description in PROHIBITED_PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {description}")
    return failures


def dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0].strip().lower()


def check_packaging() -> list[str]:
    failures: list[str] = []
    pyproject_path = ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)

    dependencies = pyproject.get("project", {}).get("dependencies", [])
    for prohibited in ("surrealdb", "surreal-commands", "surreal_commands"):
        if any(dependency_name(str(item)) == prohibited for item in dependencies):
            failures.append(
                f"pyproject.toml: external {prohibited} dependency is prohibited"
            )

    package_include = (
        pyproject.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
        .get("include", [])
    )
    for item in package_include:
        normalized = str(item).lower().replace("-", "_")
        if normalized.startswith(("surrealdb", "surreal_commands")):
            failures.append(
                f"pyproject.toml: obsolete Surreal compatibility package is still shipped: {item}"
            )

    lock = ROOT / "uv.lock"
    if lock.exists():
        lock_text = lock.read_text(encoding="utf-8")
        for prohibited in ("surrealdb", "surreal-commands", "surreal_commands"):
            if re.search(
                rf'(?m)^name\s*=\s*"{re.escape(prohibited)}"\s*$', lock_text
            ):
                failures.append(
                    f"uv.lock: prohibited {prohibited} package remains in the locked dependency graph"
                )

    legacy_module = ROOT / "open_notebook" / "database" / "legacy_query_compat.py"
    if legacy_module.exists():
        failures.append("open_notebook/database/legacy_query_compat.py must not exist")
    return failures


def main() -> int:
    failures = check_runtime() + check_packaging()
    if failures:
        print("PostgreSQL-only runtime boundary violations detected:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nOnly scripts/migrate_surreal_to_postgres.py may connect to or configure "
            "the historical SurrealDB source database.",
            file=sys.stderr,
        )
        return 1

    print(
        "PostgreSQL-only runtime boundary clean: no SurrealDB SDK/server/config, "
        "legacy query parser, or generic repo_query API in shipped runtime."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
