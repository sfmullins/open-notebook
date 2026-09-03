#!/usr/bin/env python3
"""Fail CI if a SurrealDB runtime can leak back into shipped code.

Historical documentation, tests and the one-time SurrealDB -> PostgreSQL importer
may describe or connect to SurrealDB. Normal application, deployment and package
surfaces may not import the SurrealDB SDK or ship the SurrealDB server image/binary.

This deliberately does not ban the word "SurrealQL": upstream Open Notebook is
MIT-licensed and its query strings are not the BSL-licensed SurrealDB server
implementation. Query compatibility is a technical migration concern; this gate
protects the actual runtime and distribution boundary.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DIRS = (
    ROOT / "api",
    ROOT / "commands",
    ROOT / "open_notebook",
    ROOT / "surreal_commands",
    ROOT / "deploy",
    ROOT / "examples",
    ROOT / "scripts",
)
ROOT_RUNTIME_FILES = (
    ROOT / "Dockerfile",
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.dev.yml",
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
    prohibited_dependencies = {"surrealdb", "surreal-commands"}
    for prohibited in sorted(prohibited_dependencies):
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
    if any(str(item).startswith("surrealdb") for item in package_include):
        failures.append("pyproject.toml: local surrealdb compatibility package is still shipped")

    lock = ROOT / "uv.lock"
    if lock.exists():
        lock_text = lock.read_text(encoding="utf-8")
        for prohibited in ("surrealdb", "surreal-commands"):
            if re.search(
                rf'(?m)^name\s*=\s*"{re.escape(prohibited)}"\s*$', lock_text
            ):
                failures.append(
                    f"uv.lock: prohibited {prohibited} package remains in the locked dependency graph"
                )
    return failures


def main() -> int:
    failures = check_runtime() + check_packaging()
    if failures:
        print("Surreal runtime boundary violations detected:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nAllowed exceptions: the one-time SurrealDB importer and historical/docs/test references.\n"
            "Normal runtime and deployment surfaces must remain PostgreSQL-only.",
            file=sys.stderr,
        )
        return 1

    print("Surreal runtime boundary clean: no server image/binary or SDK dependency in shipped runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
