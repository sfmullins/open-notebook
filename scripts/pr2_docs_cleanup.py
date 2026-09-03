#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text.strip() + "\n", encoding="utf-8")

def replace(rel: str, old: str, new: str) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")

write("docs/5-CONFIGURATION/database.md", """
# Database — PostgreSQL / pgvector

Open Notebook uses PostgreSQL as its runtime database. The database must provide the `vector` extension from pgvector.

## Docker Compose

The repository `docker-compose.yml` starts PostgreSQL/pgvector automatically:

```dotenv
POSTGRES_USER=open_notebook
POSTGRES_PASSWORD=open_notebook
POSTGRES_DB=open_notebook
```

For native or external deployments set the application DSN explicitly:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

`POSTGRES_URL` is an alias used by deployment tooling. When both are set, keep it identical to `DATABASE_URL`.

A container cannot reach host PostgreSQL through its own `127.0.0.1`; use a deliberately reachable host address and appropriate PostgreSQL authentication/firewall rules. Never expose port 5432 publicly.

The application initializes its schema automatically. It does not install or administer PostgreSQL.

## Legacy migration

SurrealDB is supported only as a migration source. Use `scripts/migrate_surreal_to_postgres.py`; see [Dockerless PostgreSQL](../7-DEVELOPMENT/dockerless-postgresql.md). The importer refuses to write to a non-empty PostgreSQL target.
""")

write("docs/5-CONFIGURATION/index.md", """
# Configuration

Open Notebook runtime configuration is environment-based. PostgreSQL/pgvector is the only runtime database.

Copy the example file and set a stable encryption key:

```bash
cp .env.example .env
```

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
```

For native or external PostgreSQL:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

The standard Docker Compose deployment constructs its internal DSN from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

See [Database](database.md) and [Environment Reference](environment-reference.md).

## AI providers

Store provider credentials through **Settings → API Keys**, then test the connection, discover models, and register the models you intend to use.

## Worker concurrency

```dotenv
OPEN_NOTEBOOK_WORKER_MAX_TASKS=5
```

Use `1` for sequential execution on constrained local-model/GPU setups.

## Security

Keep `OPEN_NOTEBOOK_ENCRYPTION_KEY` stable and secret, use strong PostgreSQL credentials outside isolated development, do not publish PostgreSQL publicly, and set `OPEN_NOTEBOOK_PASSWORD` where instance-level password protection is required.
""")

write("docs/5-CONFIGURATION/advanced.md", """
# Advanced Configuration

For the authoritative variable list, see [Environment Reference](environment-reference.md).

## Background worker

```dotenv
OPEN_NOTEBOOK_WORKER_MAX_TASKS=5
```

Retry/backoff is managed internally by the PostgreSQL command queue; obsolete database-specific retry variables are not supported.

## PostgreSQL

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

See [Database](database.md).

## API and provider transport

```dotenv
API_CLIENT_TIMEOUT=300
OPEN_NOTEBOOK_MAX_UPLOAD_SIZE_MB=100
ESPERANTO_LLM_TIMEOUT=60
ESPERANTO_TTS_TIMEOUT=300
ESPERANTO_SSL_VERIFY=true
```

## Optional extraction runtimes

```dotenv
OPEN_NOTEBOOK_ENABLE_DOCLING=false
OPEN_NOTEBOOK_ENABLE_CRAWL4AI=false
```

## Proxy

```dotenv
HTTP_PROXY=http://proxy.example:8080
HTTPS_PROXY=http://proxy.example:8080
NO_PROXY=localhost,127.0.0.1,postgres,host.docker.internal,.local
```

Do not route local PostgreSQL traffic through an HTTP proxy.
""")

write("docs/0-START-HERE/quick-start-local.md", """
# Quick Start — Local Docker Compose

```bash
cp .env.example .env
# set OPEN_NOTEBOOK_ENCRYPTION_KEY
docker compose up -d --build
```

The standard Compose file starts Open Notebook and PostgreSQL/pgvector as separate services.

- UI: http://localhost:8502
- API: http://localhost:5055
- API docs: http://localhost:5055/docs

Configure an AI provider under **Settings → API Keys**. For local Ollama, use `examples/docker-compose-ollama.yml` or configure an existing endpoint.

See [Installation](../1-INSTALLATION/index.md) and [Database Configuration](../5-CONFIGURATION/database.md).
""")

write("docs/0-START-HERE/quick-start-openai.md", """
# Quick Start — OpenAI

```bash
cp .env.example .env
# set OPEN_NOTEBOOK_ENCRYPTION_KEY
docker compose up -d --build
```

Open http://localhost:8502, then use **Settings → API Keys** to add an OpenAI credential, test it, discover models, and register the models you need.

The standard stack starts PostgreSQL/pgvector automatically. See [Database Configuration](../5-CONFIGURATION/database.md) for external database deployments.
""")

write("docs/0-START-HERE/quick-start-cloud.md", """
# Quick Start — Cloud / Server

Use the standard PostgreSQL/pgvector Docker Compose deployment behind your TLS reverse proxy.

```bash
cp .env.example .env
docker compose up -d --build
```

Before exposing the service:

- set a strong `OPEN_NOTEBOOK_ENCRYPTION_KEY`;
- set strong PostgreSQL credentials;
- do not expose PostgreSQL port 5432 publicly;
- set `OPEN_NOTEBOOK_PASSWORD` if instance-level password protection is required;
- configure provider credentials through **Settings → API Keys**.

See [Docker Compose](../1-INSTALLATION/docker-compose.md), [Database](../5-CONFIGURATION/database.md), and [Reverse Proxy](../5-CONFIGURATION/reverse-proxy.md).
""")

write("docs/0-START-HERE/quick-start-external-ollama.md", """
# Quick Start — Existing Ollama

Start Open Notebook with PostgreSQL/pgvector:

```bash
cp .env.example .env
docker compose up -d --build
```

Add an Ollama credential under **Settings → API Keys**. For Ollama running on the Docker host, use a host-reachable URL such as `http://host.docker.internal:11434` where supported.

For an Ollama service managed in the same Compose project, use `examples/docker-compose-ollama.yml`, which pins the Ollama image rather than following a mutable tag.
""")

write("docs/1-INSTALLATION/single-container.md", """
# Single-container installation

The legacy single-container database layout is not supported by the PostgreSQL-native runtime.

Use:

- [Docker Compose](docker-compose.md) — recommended; PostgreSQL/pgvector and Open Notebook are separate services.
- [From source](from-source.md) — application processes against an existing PostgreSQL/pgvector instance.
- [Windows native](windows-native.md) — Windows-native guidance.

Separating the database from the application container keeps application image replacement, rollback, and recreation out of the data lifecycle.
""")

replace(
    ".github/ISSUE_TEMPLATE/installation_issue.yml",
    """        SURREAL_URL=ws://surrealdb:8000/rpc
        SURREAL_USER=root
        SURREAL_PASSWORD=***REDACTED***
""",
    """        DATABASE_URL=postgresql://open_notebook:***REDACTED***@postgres:5432/open_notebook
        POSTGRES_URL=postgresql://open_notebook:***REDACTED***@postgres:5432/open_notebook
""",
)

release = ROOT / ".github/RELEASE_PROCESS.md"
text = release.read_text(encoding="utf-8")
text = re.sub(
    r"- \*\*SurrealDB import\*\*:.*?before exporting data\.\n",
    "- **Legacy database migration** is tested separately from normal release boot. PostgreSQL/pgvector is the only runtime database; use `scripts/migrate_surreal_to_postgres.py` only when validating an import from a pre-PostgreSQL installation.\n- **Release-candidate data copies** must identify PostgreSQL from `DATABASE_URL` rather than from whichever local database process happens to be running.\n",
    text,
    flags=re.S,
)
release.write_text(text, encoding="utf-8")

runbook = ROOT / ".agents/skills/release/runbook.md"
text = runbook.read_text(encoding="utf-8")
text = re.sub(
    r"# 1\. Find which SurrealDB instance.*?/tmp/dev-dump\.surql\n",
    '''# 1. Identify the PostgreSQL instance from DATABASE_URL.
# 2. Take a consistent logical copy from the running instance:
pg_dump --format=custom --file=/tmp/dev-dump.pg "$DATABASE_URL"
''',
    text,
    flags=re.S,
)
runbook.write_text(text, encoding="utf-8")

for rel in (
    "docs/6-TROUBLESHOOTING/index.md",
    "docs/6-TROUBLESHOOTING/ai-chat-issues.md",
    "docs/6-TROUBLESHOOTING/connection-issues.md",
    "docs/6-TROUBLESHOOTING/quick-fixes.md",
):
    path = ROOT / rel
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = text.replace("SURREAL_COMMANDS_MAX_TASKS", "OPEN_NOTEBOOK_WORKER_MAX_TASKS")
        text = re.sub(r"(?m)^SURREAL_COMMANDS_RETRY_[A-Z0-9_]+=.*$", "# Retry/backoff is managed internally by the PostgreSQL command queue.", text)
        path.write_text(text, encoding="utf-8")

for path in list((ROOT / "docs").rglob("*.md")) + [ROOT / "examples/README.md"]:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = text.replace("ollama/ollama:latest", "ollama/ollama:0.33.2")
        text = text.replace("ghcr.io/speaches-ai/speaches:latest-cpu", "ghcr.io/speaches-ai/speaches:0.8.3-cpu")
        text = text.replace("ghcr.io/speaches-ai/speaches:latest-cuda", "ghcr.io/speaches-ai/speaches:0.8.3-cuda")
        path.write_text(text, encoding="utf-8")

allowed = {
    ROOT / "docs/7-DEVELOPMENT/dockerless-postgresql.md",
    ROOT / "docs/7-DEVELOPMENT/decisions/ADR-001-surrealdb.md",
}
scan = [
    ROOT / ".env.example",
    ROOT / ".github/ISSUE_TEMPLATE/installation_issue.yml",
    ROOT / ".github/RELEASE_PROCESS.md",
    ROOT / ".agents/skills/release/runbook.md",
]
scan += list((ROOT / "docs").rglob("*.md"))
failures = []
for path in scan:
    if not path.exists() or path in allowed:
        continue
    text = path.read_text(encoding="utf-8")
    if re.search(r"\bSURREAL_[A-Z0-9_]+\b", text):
        failures.append(f"{path.relative_to(ROOT)}: obsolete SURREAL_* runtime variable")
    if re.search(r"surrealdb/surrealdb(?::[^\s`\"']+)?", text, re.I):
        failures.append(f"{path.relative_to(ROOT)}: SurrealDB runtime image")

if failures:
    print("Remaining active-document SurrealDB residue:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)

print("Active PostgreSQL documentation cleanup complete.")
