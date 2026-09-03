#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(rel: str, content: str) -> None:
    path = ROOT / rel
    path.write_text(content.strip() + "\n", encoding="utf-8")


def replace(rel: str, old: str, new: str) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")


write(
    "docs/5-CONFIGURATION/database.md",
    r'''# Database — PostgreSQL / pgvector

Open Notebook uses PostgreSQL as its runtime database. The database must provide the `vector` extension from pgvector.

## Docker Compose

The repository `docker-compose.yml` starts PostgreSQL/pgvector automatically. Its defaults are:

```dotenv
POSTGRES_USER=open_notebook
POSTGRES_PASSWORD=open_notebook
POSTGRES_DB=open_notebook
```

The application container receives an internal DSN using the Compose service hostname `postgres`.

For credentials or external deployments, set the canonical application DSN explicitly:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

`POSTGRES_URL` is an alias used by deployment tooling. When both are set, keep it identical to `DATABASE_URL`.

## Host PostgreSQL with a containerized app

A container cannot reach a host-only PostgreSQL listener through its own `127.0.0.1`. Use a deliberately reachable host address (for example `host.docker.internal` where supported), configure PostgreSQL authentication/firewall rules appropriately, and do not expose port 5432 publicly.

## Native development

For a native checkout, PostgreSQL/pgvector can run locally:

```dotenv
DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook
```

The application initializes its schema automatically. It does not install or administer the PostgreSQL server.

## Migrating a legacy SurrealDB database

SurrealDB is supported only as a **migration source**, not as an application runtime database. Use `scripts/migrate_surreal_to_postgres.py`; see [Dockerless PostgreSQL](../7-DEVELOPMENT/dockerless-postgresql.md) for migration notes. The migration refuses to import into a non-empty PostgreSQL target unless explicitly handled by the operator.
''',
)

write(
    "docs/5-CONFIGURATION/index.md",
    r'''# Configuration

Open Notebook runtime configuration is environment-based. PostgreSQL/pgvector is the only runtime database.

## Environment file

Copy the repository example and edit it for your deployment:

```bash
cp .env.example .env
```

At minimum, set a stable encryption key:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
```

For a native or external PostgreSQL deployment, set:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

The standard `docker-compose.yml` constructs the application DSN from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` automatically.

See [Database](database.md) for PostgreSQL details and [Environment Reference](environment-reference.md) for the complete supported variable set.

## AI providers

Provider credentials should normally be stored through **Settings → API Keys**. After adding a provider credential, test the connection, discover models, and register the models you want Open Notebook to use.

- [Ollama](ollama.md)
- [OpenAI-compatible providers](openai-compatible.md)
- [Local STT](local-stt.md)
- [Local TTS](local-tts.md)

## Worker concurrency

The background queue is PostgreSQL-backed. Control worker concurrency at launch time with:

```dotenv
OPEN_NOTEBOOK_WORKER_MAX_TASKS=5
```

Use `1` for strictly sequential execution on constrained local-model/GPU setups.

## Networking and reverse proxies

Use `API_URL`, `INTERNAL_API_URL`, `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` as documented in the [Environment Reference](environment-reference.md). Keep local/PostgreSQL service addresses out of an outbound HTTP proxy path.

## Security

- Keep `OPEN_NOTEBOOK_ENCRYPTION_KEY` stable and secret; changing it prevents existing provider credentials from being decrypted.
- Use strong PostgreSQL credentials outside isolated development environments.
- Do not publish PostgreSQL port 5432 to the public internet.
- Set `OPEN_NOTEBOOK_PASSWORD` when instance-level password protection is required.
''',
)

write(
    "docs/5-CONFIGURATION/advanced.md",
    r'''# Advanced Configuration

This page covers current PostgreSQL-native runtime tuning. For the authoritative variable list, see [Environment Reference](environment-reference.md).

## Background worker

The PostgreSQL command queue worker defaults to five concurrent jobs:

```dotenv
OPEN_NOTEBOOK_WORKER_MAX_TASKS=5
```

Set it to `1` for sequential processing or lower it when local LLM/TTS/STT hardware is saturated. Retry/backoff policy is managed internally by the command queue; obsolete database-specific retry variables are not supported.

## PostgreSQL

Use a PostgreSQL/pgvector database and configure a DSN:

```dotenv
DATABASE_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
POSTGRES_URL=postgresql://open_notebook:strong-password@127.0.0.1:5432/open_notebook
```

See [Database](database.md) for container/host networking guidance.

## API timeouts and uploads

```dotenv
API_CLIENT_TIMEOUT=300
OPEN_NOTEBOOK_MAX_UPLOAD_SIZE_MB=100
```

A reverse proxy may impose a lower request-size or timeout limit; configure it consistently with the application.

## Provider transport

```dotenv
ESPERANTO_LLM_TIMEOUT=60
ESPERANTO_TTS_TIMEOUT=300
ESPERANTO_SSL_VERIFY=true
# ESPERANTO_SSL_CA_BUNDLE=/path/to/internal-ca.pem
```

Disable TLS verification only in controlled development environments.

## Content extraction

Optional extraction runtimes are deliberately outside the minimal runtime image. Where the deployment supports them:

```dotenv
OPEN_NOTEBOOK_ENABLE_DOCLING=false
OPEN_NOTEBOOK_ENABLE_CRAWL4AI=false
```

External extraction services can be configured with the variables documented in the environment reference.

## Proxy configuration

```dotenv
HTTP_PROXY=http://proxy.example:8080
HTTPS_PROXY=http://proxy.example:8080
NO_PROXY=localhost,127.0.0.1,postgres,host.docker.internal,.local
```

Do not route local PostgreSQL traffic through an HTTP proxy.

## Instance security

```dotenv
OPEN_NOTEBOOK_PASSWORD=replace-with-a-strong-password
```

For production deployments, also use strong PostgreSQL credentials, firewall database access, terminate TLS at a trusted reverse proxy, and retain a stable `OPEN_NOTEBOOK_ENCRYPTION_KEY`.
''',
)

quick_common = r'''# Quick Start — Local Docker Compose

This is the supported local quick start. It runs Open Notebook with PostgreSQL/pgvector.

## 1. Configure

```bash
cp .env.example .env
```

Edit `.env` and set a stable encryption key:

```dotenv
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-random-secret
```

## 2. Start

```bash
docker compose up -d --build
```

The standard Compose file starts PostgreSQL/pgvector and Open Notebook. PostgreSQL is bound to localhost only; the application reaches it through the internal `postgres` service hostname.

## 3. Open

- UI: http://localhost:8502
- API: http://localhost:5055
- API docs: http://localhost:5055/docs

## 4. Configure an AI provider

Open **Settings → API Keys**, add your provider credential, test the connection, discover models, and register the models you intend to use.

For local Ollama, use the pinned example in `examples/docker-compose-ollama.yml` or configure an existing Ollama endpoint in Settings.

See [Installation](../1-INSTALLATION/index.md) and [Database Configuration](../5-CONFIGURATION/database.md) for other deployment modes.
'''
write("docs/0-START-HERE/quick-start-local.md", quick_common)

write(
    "docs/0-START-HERE/quick-start-openai.md",
    r'''# Quick Start — OpenAI

Open Notebook runs on PostgreSQL/pgvector. Provider credentials are stored through the application rather than embedded in the database configuration.

## Start the stack

```bash
cp .env.example .env
# set OPEN_NOTEBOOK_ENCRYPTION_KEY in .env
docker compose up -d --build
```

Open http://localhost:8502, then go to **Settings → API Keys**:

1. Add an OpenAI credential.
2. Test the connection.
3. Discover models.
4. Register the chat and embedding models you want to use.

The standard Compose deployment starts PostgreSQL/pgvector automatically. See [Database Configuration](../5-CONFIGURATION/database.md) if using an external database.
''',
)

write(
    "docs/0-START-HERE/quick-start-cloud.md",
    r'''# Quick Start — Cloud / Server

Use the standard PostgreSQL/pgvector Docker Compose deployment as the base, then place Open Notebook behind your TLS reverse proxy.

```bash
cp .env.example .env
# set a strong OPEN_NOTEBOOK_ENCRYPTION_KEY and PostgreSQL credentials
docker compose up -d --build
```

## Required production changes

- Set strong `POSTGRES_PASSWORD` and keep `DATABASE_URL`/`POSTGRES_URL` consistent where explicitly configured.
- Do not expose PostgreSQL port 5432 publicly.
- Set `OPEN_NOTEBOOK_PASSWORD` if instance-level password protection is required.
- Terminate HTTPS at a trusted reverse proxy and set `API_URL` when the public API origin differs from automatic same-origin routing.
- Configure provider credentials through **Settings → API Keys**.

See [Docker Compose installation](../1-INSTALLATION/docker-compose.md), [Database Configuration](../5-CONFIGURATION/database.md), and [Reverse Proxy](../5-CONFIGURATION/reverse-proxy.md).
''',
)

write(
    "docs/0-START-HERE/quick-start-external-ollama.md",
    r'''# Quick Start — Existing Ollama

Start Open Notebook with PostgreSQL/pgvector:

```bash
cp .env.example .env
# set OPEN_NOTEBOOK_ENCRYPTION_KEY
docker compose up -d --build
```

Then add an Ollama credential in **Settings → API Keys**.

For an Ollama server on the Docker host, the container normally needs a host-reachable URL such as:

```text
http://host.docker.internal:11434
```

For a remote Ollama server, use its reachable HTTP endpoint. Test the connection, discover models, and register the models you need.

If you want Ollama managed in the same Compose project, use `examples/docker-compose-ollama.yml`, which pins the Ollama container version rather than following a mutable `latest` tag.
''',
)

write(
    "docs/1-INSTALLATION/single-container.md",
    r'''# Single-container installation

The legacy single-container database layout is no longer supported by this PostgreSQL-native runtime.

Open Notebook now treats PostgreSQL/pgvector as a separate failure domain. Use one of these supported paths instead:

- [Docker Compose](docker-compose.md) — recommended; starts PostgreSQL/pgvector and Open Notebook as separate services.
- [From source](from-source.md) — run the application natively against an existing PostgreSQL/pgvector instance.
- [Windows native](windows-native.md) — native Windows development/install guidance.

Keeping the database outside the application container prevents application image replacement, rollback, or recreation from owning the household data lifecycle.
''',
)

# Narrow active-document replacements.
replace(
    ".github/ISSUE_TEMPLATE/installation_issue.yml",
    "        SURREAL_URL=ws://surrealdb:8000/rpc\n        SURREAL_USER=root\n        SURREAL_PASSWORD=***REDACTED***\n",
    "        DATABASE_URL=postgresql://open_notebook:***REDACTED***@postgres:5432/open_notebook\n        POSTGRES_URL=postgresql://open_notebook:***REDACTED***@postgres:5432/open_notebook\n",
)
replace(
    ".github/RELEASE_PROCESS.md",
    "- **SurrealDB import**: `OVERWRITE` goes after the type keyword\n  (`DEFINE FIELD OVERWRITE …`), and the exporter can leak a log line into the\n  dump — `rc-stack.sh` handles both.\n- **Multiple local SurrealDB instances**: check which one the dev `.env`\n  actually points at (`SURREAL_URL`) before exporting data.\n",
    "- **Legacy database migration** is tested separately from normal release boot.\n  PostgreSQL/pgvector is the only runtime database; use\n  `scripts/migrate_surreal_to_postgres.py` only when validating an import from a\n  pre-PostgreSQL installation.\n- **Release-candidate data copies** should be made from the PostgreSQL instance\n  identified by `DATABASE_URL`; never infer the target from a locally running\n  database process.\n",
)
replace(
    ".agents/skills/release/runbook.md",
    "# 1. Find which SurrealDB instance dev actually uses — read SURREAL_URL in .env\n#    (multiple instances may run locally; the repo-compose one on :8000 may NOT\n#    be it), and note SURREAL_DATABASE.\n# 2. Consistent export from the RUNNING instance (originals untouched):\ndocker exec <that-container> /surreal export --conn http://localhost:8000 \\\n  --user root --pass root --ns open_notebook --db <that-db> /dev/stdout > /tmp/dev-dump.surql\n",
    "# 1. Identify the PostgreSQL instance from DATABASE_URL.\n# 2. Take a consistent logical copy from the RUNNING instance (original untouched):\npg_dump --format=custom --file=/tmp/dev-dump.pg "$DATABASE_URL"\n",
)

# Queue concurrency variable was renamed with the PostgreSQL-native worker.
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
        text = re.sub(
            r"(?m)^SURREAL_COMMANDS_RETRY_[A-Z0-9_]+=.*$",
            "# Retry/backoff is managed internally by the PostgreSQL command queue.",
            text,
        )
        path.write_text(text, encoding="utf-8")

# Pin optional third-party examples to the same reviewed versions as the shipped examples.
for path in list((ROOT / "docs").rglob("*.md")) + [ROOT / "examples/README.md"]:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    text = text.replace("ollama/ollama:latest", "ollama/ollama:0.33.2")
    text = text.replace("ghcr.io/speaches-ai/speaches:latest-cpu", "ghcr.io/speaches-ai/speaches:0.8.3-cpu")
    text = text.replace("ghcr.io/speaches-ai/speaches:latest-cuda", "ghcr.io/speaches-ai/speaches:0.8.3-cuda")
    path.write_text(text, encoding="utf-8")

# Remove obsolete retry block that otherwise advertises unsupported environment variables.
conn = ROOT / "docs/6-TROUBLESHOOTING/connection-issues.md"
if conn.exists():
    text = conn.read_text(encoding="utf-8")
    text = re.sub(
        r"### Enable Retry Logic\n```bash\n(?:#.*\n)*# Retry/backoff is managed internally by the PostgreSQL command queue\.\n(?:# Retry/backoff is managed internally by the PostgreSQL command queue\.\n)*\n?# Restart\ndocker compose restart\n```",
        "### Retry behavior\n\nRetry/backoff is managed internally by the PostgreSQL command queue. If failures persist, inspect the worker and PostgreSQL logs before restarting services.",
        text,
    )
    conn.write_text(text, encoding="utf-8")

# Fail closed on stale runtime configuration in active documentation/configuration.
allowed = {
    ROOT / "docs/7-DEVELOPMENT/dockerless-postgresql.md",
    ROOT / "docs/7-DEVELOPMENT/decisions/ADR-001-surrealdb.md",
}
scan = [ROOT / ".env.example", ROOT / ".github/ISSUE_TEMPLATE/installation_issue.yml", ROOT / ".github/RELEASE_PROCESS.md", ROOT / ".agents/skills/release/runbook.md"]
scan += list((ROOT / "docs").rglob("*.md"))
failures: list[str] = []
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
