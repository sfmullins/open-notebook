---
name: release
description: Orchestrate verification and release metadata for the Vält Open Notebook fork. Use for changelog audit, risk-based testing, local artefact gates, cut PRs and optional GitHub release metadata. This fork does not currently publish container images from GitHub Actions.
---

# Vält Open Notebook Release Orchestrator

The source of truth is `.github/RELEASE_PROCESS.md`; read it first. The permanent
workflow is `.github/workflows/test.yml`. It verifies the candidate but does not
publish images, tags, or releases.

Read `${CLAUDE_SKILL_DIR}/gates.md` before acting.

## Ground rules

- Every repository change goes through a branch and PR; do not push directly to
  `main`.
- Do not invoke or recreate `build-and-release.yml`: it is not part of this fork.
- Do not treat upstream `lfnovo/*`, `v1-dev`, or `v1-latest` artefacts as Vält
  releases.
- PostgreSQL/pgvector is the only normal runtime database. SurrealDB is permitted
  only as a legacy migration source/CI fixture.
- The final-artifact SBOM/licence gate is mandatory for the container reference
  artefact.

## Phase 0 — scope and changelog audit

1. Fetch tags and identify the last relevant version.
2. Compare `git log <last-tag>..origin/main --oneline` with `[Unreleased]`.
3. Add missing changelog entries through a focused PR when required.
4. Review dependency/security findings relevant to the release.

## Phase 1 — risk-based test matrix

Instantiate `${CLAUDE_SKILL_DIR}/test-matrix.md` against the actual diff. For every
material change identify what can break, who is affected, and what evidence proves
correct behaviour. Security changes must also prove legitimate use still works.

## Phase 2 — permanent automated gates

The authoritative result is the `test.yml` run for the candidate commit. It covers:

- runtime/database boundary enforcement;
- backend lock, Ruff, mypy, and pytest;
- frontend lint, tests, production build, and npm audit;
- real SurrealDB 2.6.5 → PostgreSQL migration parity;
- final-image SBOM/licence enforcement; and
- documentation link checks.

Local equivalents may be run for diagnosis, but do not replace the GitHub Actions
result for the exact candidate commit.

## Phase 3 — local artefact gate and manual checks

When a container candidate matters:

```bash
make docker-build-local
make release-test TAG=<new> OLD_TAG=<previous>
```

Use a clean artefact for optional-runtime checks. Do not infer container behaviour
from a developer venv that may contain packages installed out of band.

FFmpeg remains operator supplied/external. Ollama and Speaches are optional external
services. Do not bundle or reclassify them as Vält runtime components during a
release.

## Phase 4 — fix loop

For each blocker: reproduce → root cause → focused PR with regression coverage →
permanent CI → merge when authorized. Re-run artefact/manual checks where the fix
can affect them. Unrelated pre-existing issues should not become release scope by
accident.

## Phase 5 — cut version metadata

1. Start from current, green `main`.
2. Open a focused cut PR for version/changelog changes.
3. Merge only after the exact cut commit passes `test.yml`.
4. Create the Git tag from the verified `main` commit.
5. Re-run any local artefact/manual gates required by the change.

There is no image-publish phase in this fork.

## Phase 6 — optional GitHub release metadata

A GitHub release may be created only with the owner's explicit authorization. It is
metadata only: publication does **not** trigger image publication.

Draft notes using `${CLAUDE_SKILL_DIR}/comms-templates.md`, make the Vält fork status
clear, and never claim a registry artefact exists unless a separately reviewed
publishing process has actually produced and verified one.

## Phase 7 — cleanup and retro

Stop local stacks/watchers, remove temporary dumps, and verify a clean working tree.
If the real process changed, update `.github/RELEASE_PROCESS.md` and these release
skill files in the same PR so documentation cannot drift back to upstream
assumptions.
