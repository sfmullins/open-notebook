#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(rel): return (ROOT / rel).read_text(encoding='utf-8')
def write(rel, text): (ROOT / rel).write_text(text, encoding='utf-8')
def one(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected one match, got {n}')
    return text.replace(old, new, 1)

# Feature-local podcast runtime: importing the application must not require an
# externally supplied FFmpeg executable.
p='commands/podcast_commands.py'; t=read(p)
t=one(t,'''try:
    from podcast_creator import configure, create_podcast
except ImportError as e:
    logger.error(f"Failed to import podcast_creator: {e}")
    raise ValueError("podcast_creator library not available")


''','''def _load_podcast_creator():
    """Load podcast_creator only when podcast generation is requested.

    moviepy/imageio-ffmpeg resolves FFmpeg during import. FFmpeg is deliberately
    external to the Vält redistributed userland, so normal API/worker startup
    must not require it. Operators enabling podcast generation provide ffmpeg
    and may set IMAGEIO_FFMPEG_EXE to its absolute path.
    """
    try:
        from podcast_creator import configure, create_podcast
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError(
            "Podcast generation requires the optional podcast runtime and an "
            "externally installed FFmpeg executable. Install FFmpeg outside the "
            "Vält userland boundary and set IMAGEIO_FFMPEG_EXE if it is not on PATH."
        ) from exc
    return configure, create_podcast


''','podcast import block')
t=one(t,'    start_time = time.time()\n\n    try:\n','    start_time = time.time()\n\n    try:\n        configure, create_podcast = _load_podcast_creator()\n','podcast lazy load')
t=t.replace('Load Episode and Speaker profiles from SurrealDB','Load Episode and Speaker profiles from PostgreSQL')
write(p,t)

# PostgreSQL-native development startup.
write('dev-init.sh','''#!/bin/bash
# Development environment startup for PostgreSQL-native Open Notebook.

set -e

echo "=== Open Notebook Dev Startup ==="

POSTGRES_PORT=${POSTGRES_PORT:-5432}
echo "Checking PostgreSQL on port $POSTGRES_PORT..."
if ! nc -z localhost "$POSTGRES_PORT" 2>/dev/null; then
  echo "PostgreSQL not reachable on port $POSTGRES_PORT. Start PostgreSQL/pgvector first."
  exit 1
fi
echo "PostgreSQL is reachable"

echo "Syncing Python dependencies..."
uv sync

echo "Syncing frontend dependencies..."
cd frontend && npm install && cd ..

echo "Starting API backend (port 5055)..."
uv run --env-file .env run_api.py &
sleep 3

echo "Starting background worker..."
uv run --env-file .env open-notebook-command-worker --import-modules commands --max-tasks "${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
sleep 2

echo "Starting Next.js frontend (port 3000)..."
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:5055"
echo "  API Docs: http://localhost:5055/docs"
cd frontend && npm run dev
''')

write('docker-compose.override.yml.example','''# Local Docker Compose override — copy to docker-compose.override.yml.
#
# The shipped compose file binds PostgreSQL to 127.0.0.1. This example exposes
# PostgreSQL on all host interfaces for deliberate remote development access.
# Do this only behind a firewall/VPN/SSH tunnel and use non-default credentials.

services:
  postgres:
    ports: !override
      - "5432:5432"
''')

single = ROOT / 'examples/docker-compose-single.yml'
if single.exists():
    single.unlink()

# Remove the now-dead query compatibility implementation and its dedicated tests.
for rel in ('open_notebook/database/legacy_query_compat.py','tests/test_postgres_query_compat.py'):
    pth=ROOT/rel
    if pth.exists(): pth.unlink()

write('tests/test_postgres_repository.py','''"""PostgreSQL repository contract tests."""
from __future__ import annotations
import pytest
from open_notebook.database.postgres import db_connection, ensure_schema
from open_notebook.database.record_id import RecordID
from open_notebook.database.repository import repo_get, repo_relate, repo_relations, repo_upsert

async def _reset_store() -> None:
    await ensure_schema()
    async with db_connection() as connection:
        await connection.execute("TRUNCATE TABLE on_relation, on_record CASCADE")
        await connection.commit()

def test_record_id_round_trip() -> None:
    record_id = RecordID.parse("source:abc-123")
    assert record_id.table == "source"
    assert record_id.id == "abc-123"
    assert str(record_id) == "source:abc-123"

def test_record_id_round_trip_from_model_dump_shape() -> None:
    record_id = RecordID.parse({"table": "command", "id": "abc-123"})
    assert str(record_id) == "command:abc-123"

@pytest.mark.asyncio
async def test_singleton_upsert_preserves_explicit_record_id() -> None:
    await _reset_store()
    result = await repo_upsert("record", "open_notebook:default_models", {"default_chat_model": "model:chat"})
    assert result[0]["id"] == "open_notebook:default_models"
    loaded = await repo_get("open_notebook:default_models")
    assert loaded is not None
    assert loaded["default_chat_model"] == "model:chat"

@pytest.mark.asyncio
async def test_relation_direction_and_idempotency() -> None:
    await _reset_store()
    await repo_upsert("source", "source:s1", {"title": "Source"})
    await repo_upsert("notebook", "notebook:n1", {"name": "Notebook"})
    first = await repo_relate("source:s1", "reference", "notebook:n1")
    second = await repo_relate("source:s1", "reference", "notebook:n1")
    assert first[0]["in"] == "source:s1" and first[0]["out"] == "notebook:n1"
    assert second[0]["in"] == "source:s1" and second[0]["out"] == "notebook:n1"
    rows = await repo_relations("reference", source="source:s1", target="notebook:n1")
    assert len(rows) == 1
''')

# Strengthen runtime boundary.
write('scripts/check_no_surreal_runtime.py','''#!/usr/bin/env python3
"""Enforce PostgreSQL-only shipped runtime; SurrealDB is migration-source only."""
from __future__ import annotations
import re, sys, tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUNTIME_DIRS=tuple(ROOT/p for p in ("api","commands","open_notebook","command_queue","deploy","examples","scripts"))
ROOT_FILES=tuple(ROOT/p for p in ("Dockerfile","docker-compose.yml","docker-compose.dev.yml","docker-compose.override.yml.example","Makefile","dev-init.sh","start.sh","supervisord.conf"))
ALLOWED={ROOT/'scripts'/'migrate_surreal_to_postgres.py', ROOT/'scripts'/'check_no_surreal_runtime.py'}
SUFFIXES={'.py','.toml','.yml','.yaml','.sh','.conf','.env',''}
PATTERNS=(
 (re.compile(r'(?m)^\\s*(?:from|import)\\s+surrealdb\\b'),'SurrealDB Python SDK import'),
 (re.compile(r'surrealdb/surrealdb(?::[^\\s\\"\\\']+)?',re.I),'SurrealDB server image'),
 (re.compile(r'\\brepo_query\\b'),'generic SurrealQL compatibility query API'),
 (re.compile(r'\\blegacy_query_compat\\b'),'legacy query compatibility module'),
 (re.compile(r'\\bsurreal_commands\\b'),'obsolete Surreal-named command package'),
 (re.compile(r'\\bSURREAL_[A-Z0-9_]+\\b'),'obsolete SurrealDB runtime configuration'),
)
def files():
    out={p for p in ROOT_FILES if p.exists()}
    for d in RUNTIME_DIRS:
        if d.exists():
            out.update(p for p in d.rglob('*') if p.is_file() and p.suffix.lower() in SUFFIXES)
    return sorted(out)
def dependency_name(req): return re.split(r'[<>=!~;\\[\\s]',req,maxsplit=1)[0].strip().lower()
def main():
    failures=[]
    for p in files():
        if p in ALLOWED: continue
        try: txt=p.read_text(encoding='utf-8')
        except UnicodeDecodeError: continue
        for pattern,desc in PATTERNS:
            if pattern.search(txt): failures.append(f"{p.relative_to(ROOT)}: {desc}")
    with (ROOT/'pyproject.toml').open('rb') as fh: project=tomllib.load(fh)
    deps=project.get('project',{}).get('dependencies',[])
    for prohibited in ('surrealdb','surreal-commands','surreal_commands'):
        if any(dependency_name(str(x))==prohibited for x in deps): failures.append(f"pyproject.toml: prohibited dependency {prohibited}")
    if (ROOT/'open_notebook/database/legacy_query_compat.py').exists(): failures.append('legacy_query_compat.py must not exist')
    if failures:
        print('PostgreSQL-only runtime boundary violations detected:',file=sys.stderr)
        for f in failures: print(f'  - {f}',file=sys.stderr)
        return 1
    print('PostgreSQL-only runtime boundary clean.')
    return 0
if __name__=='__main__': raise SystemExit(main())
''')

# Remove stale runtime SurrealDB comments.
p='commands/source_commands.py'; t=read(p)
t=t.replace('# Handle deep queues (workaround for SurrealDB v2 transaction conflicts)','# Handle deep queues and transient database contention')
t=t.replace('# Avoid log noise during transaction conflicts','# Avoid log noise during transient contention')
write(p,t)

# Pin mutable external service examples. These are optional/operator-supplied,
# not part of the Vält redistributed userland.
for rel in ('examples/docker-compose-ollama.yml','examples/docker-compose-full-local.yml'):
    p=ROOT/rel; t=p.read_text(encoding='utf-8')
    t=t.replace('pgvector/pgvector:pg17','pgvector/pgvector:0.8.6-pg17-bookworm')
    t=t.replace('ollama/ollama:latest','ollama/ollama:0.33.2')
    t=t.replace('pull_policy: always','pull_policy: if_not_present')
    t=t.replace('ghcr.io/speaches-ai/speaches:latest-cpu','ghcr.io/speaches-ai/speaches:0.8.3-cpu')
    p.write_text(t,encoding='utf-8')
p=ROOT/'examples/docker-compose-speaches.yml'; t=p.read_text(encoding='utf-8')
t=t.replace('pgvector/pgvector:pg17','pgvector/pgvector:0.8.6-pg17-bookworm')
t=t.replace('ghcr.io/speaches-ai/speaches:latest-cpu','ghcr.io/speaches-ai/speaches:0.8.3-cpu')
t=t.replace('ghcr.io/speaches-ai/speaches:latest-cuda','ghcr.io/speaches-ai/speaches:0.8.3-cuda')
p.write_text(t,encoding='utf-8')

# Pin PostgreSQL image in CI and EasyPanel. Avoid claiming an unpublished fork
# image version: the app image field remains user-configurable and is documented
# as an operator-provided artifact.
p=ROOT/'.github/workflows/test.yml'; t=p.read_text(encoding='utf-8').replace('pgvector/pgvector:pg17','pgvector/pgvector:0.8.6-pg17-bookworm'); p.write_text(t,encoding='utf-8')
p=ROOT/'examples/easypanel/meta.yaml'; t=p.read_text(encoding='utf-8').replace('pgvector/pgvector:pg17','pgvector/pgvector:0.8.6-pg17-bookworm').replace('ghcr.io/sfmullins/open-notebook:latest','open-notebook:local'); p.write_text(t,encoding='utf-8')

# Remove diagnostic artifacts now that their findings are being remediated.
for rel in ('PR2_BOUNDARY_DIAGNOSTIC.txt','PR2_PYTEST_DIAGNOSTIC.txt','PR2_NPM_AUDIT.json','PR2_NPM_AUDIT_EXIT.txt'):
    p=ROOT/rel
    if p.exists(): p.unlink()

for rel in ('scripts/pr2_known_remediation.py','.github/workflows/pr2-known-remediation.yml'):
    p=ROOT/rel
    if p.exists(): p.unlink()
