# Runbook — Vält fork release verification

This fork does **not** currently publish container images or development images from
GitHub Actions. The only permanent workflow is `.github/workflows/test.yml`, which
is a verification/compliance gate.

Do not invoke `build-and-release.yml`, assume a GitHub release publishes images, or
use upstream registry tags as evidence that this fork has shipped an artifact.

See `.github/RELEASE_PROCESS.md` for the governing release boundary.

## Verify `main`

```bash
gh workflow run test.yml --ref main
gh run list --workflow=test.yml --limit 1
gh run watch <run-id> --exit-status
```

A releasable commit must pass every permanent job, including:

- PostgreSQL runtime-boundary checks;
- backend lint, typing, and tests;
- frontend lint, tests, production build, and dependency audit;
- real SurrealDB-to-PostgreSQL migration parity;
- final-image SBOM and licence policy enforcement; and
- documentation link checks.

## Local image confidence gate

A green source tree is not by itself proof that a container artifact boots. For a
local candidate, build and exercise the image without publishing it:

```bash
make docker-build-local
make release-test TAG=<new> OLD_TAG=<previous>
```

The Dockerfile/container path is retained as an operator/developer deployment
reference. It is not the Vält shipped-userland boundary. In particular, external
FFmpeg and optional Ollama/Speaches services are not redistributed as Vält runtime
components.

## Legacy database migration verification

PostgreSQL/pgvector is the only runtime database. SurrealDB is permitted only as a
legacy migration source/test fixture.

The permanent CI migration-parity job starts the exact pinned legacy fixture and
verifies records, IDs, relationships, embeddings, vector search, text search, and
non-empty-target refusal. Do not add SurrealDB back to the normal runtime path.

For a manual legacy import, use:

```bash
python scripts/migrate_surreal_to_postgres.py --help
```

The migration-specific `SURREAL_*` source settings are allowed only at that import
boundary.

## Publishing boundary

There is intentionally no automated image-publishing workflow in this fork.
Creating a Git tag or GitHub release does **not** publish a Docker/OCI image.

If image publication is reintroduced later, it requires a separately reviewed
workflow/process that:

1. pins mutable build/runtime references;
2. preserves the permissive-only Vält shipped-userland licence policy;
3. generates and validates the final-artifact SBOM before publication;
4. never silently bundles FFmpeg or optional external model/runtime services; and
5. publishes only after the permanent verification workflow is green.

Until such a workflow exists, stop after verification; do not substitute an
upstream `lfnovo/*` image or registry operation for a Vält release.

## Cleanup

```bash
make release-stack-down 2>/dev/null || true
rm -f /tmp/dev-dump.sql
rm -rf /tmp/onrel-*
docker ps --format '{{.Names}}' | grep onrel || true
git status --short
```

The working tree should be clean before any tag or release metadata is created.
