# Release Process

This document governs release confidence for the Vält `open-notebook` fork.

The fork currently has **one permanent GitHub Actions workflow**:
`.github/workflows/test.yml`. It verifies the source tree, migration boundary,
frontend security state, and final container artefact. It does **not** publish
Docker/OCI images, development images, Git tags, or GitHub releases.

Historical upstream instructions that refer to `build-and-release.yml`, `v1-dev`,
`v1-latest`, `lfnovo/*` registries, or release-triggered image publication are not
operative in this fork.

The underlying confidence model originated in the upstream release process; see
[ADR-005](../docs/7-DEVELOPMENT/decisions/ADR-005-release-confidence-process.md).

## Release model

- Patch releases contain backwards-compatible fixes.
- Minor releases contain backwards-compatible features and improvements.
- Major releases require explicit planning for breaking changes and migrations.
- Pull requests merge to `main` only after the permanent verification gates are
  satisfied.
- A tag or GitHub release is release metadata only. It does not imply that a Vält
  runtime artefact has been published.

## Permanent verification gates

A candidate is not releasable unless the `test.yml` run for the candidate commit is
green. The workflow currently enforces:

1. **Runtime boundary** — PostgreSQL/pgvector is the only application database;
   removed Surreal runtime APIs/configuration may not re-enter the normal runtime.
2. **Backend quality** — dependency lock, Ruff, mypy, and the full backend pytest
   suite.
3. **Frontend quality/security** — clean npm install, lint, tests, production build,
   and `npm audit --audit-level=moderate`.
4. **Legacy migration parity** — a real pinned SurrealDB 2.6.5 source is migrated to
   PostgreSQL and checked for record/ID, relation, notebook/podcast, embedding,
   vector-search, text-search, and non-empty-target parity.
5. **Final-image SBOM/licence policy** — the final image is built, inventoried with
   the pinned Syft release, and checked by the fail-closed licence policy.
6. **Documentation links** — repository documentation links are checked.

Run or inspect the workflow with:

```bash
gh workflow run test.yml --ref main
gh run list --workflow=test.yml --limit 1
gh run watch <run-id> --exit-status
```

## Confidence process

### 0. Changelog audit

Diff `git log <last-tag>..main` against `[Unreleased]` in the changelog when preparing
a versioned release. Every material merged change should be represented.

### 1. Risk-based test matrix

For changes beyond the permanent suite, map each material change to what it can
break and the evidence required to validate it. Security changes must also prove
that the protection does not block legitimate operation.

### 2. Test the artefact, not only the repository

A green source suite is not proof that a container artefact starts correctly. For a
local candidate:

```bash
make docker-build-local
make release-test TAG=<new> OLD_TAG=<previous>
```

The Dockerfile/container path is an operator/developer deployment reference. It is
not, by itself, the Vält shipped-userland boundary.

### 3. Fix loop

Any blocker becomes a focused PR. Re-run the permanent suite after each merge and
repeat artefact/manual checks where the fix can affect them. Pre-existing unrelated
issues should not be pulled into a release unless they block the release boundary.

## PostgreSQL and legacy migration boundary

PostgreSQL/pgvector is the only runtime database.

SurrealDB is retained solely as a legacy import source and CI migration fixture. The
migration utility is `scripts/migrate_surreal_to_postgres.py`; migration-specific
`SURREAL_*` settings are permitted only at that boundary. They are not runtime
configuration.

The importer must refuse a non-empty PostgreSQL target unless the operator
explicitly requests the documented override. Record-only, relation-only,
source-embedding-only, and record-embedding-only targets all count as non-empty.

## Runtime and licence boundary

Vält shipped userland follows the project's permissive-only policy, with the Linux
kernel handled under its separately defined exception outside this application
repository.

The final-artifact SBOM/licence job is fail closed: unknown or disallowed runtime
licences fail CI rather than being silently accepted.

Boundary rules relevant to this fork:

- `imageio-ffmpeg` must not introduce a bundled FFmpeg executable into the final
  artefact.
- FFmpeg, when media functionality requires it, is operator supplied/external and is
  not redistributed as a Vält userland component.
- Ollama and Speaches integrations are optional external services. Their software,
  model, and container licences are evaluated separately from Vält redistribution.
- SurrealDB 2.6.5 is a migration-test fixture, not a shipped runtime dependency.
- Container/base-OS packages are inventoried as the deployment-reference image
  boundary; they must not be conflated with the Vält permissive-only shipped-userland
  policy.

## Cutting version metadata

When a version is required:

1. Confirm the exact `main` commit has a fully green `test.yml` run.
2. Open a focused cut PR for the version/changelog change.
3. Merge only after its permanent CI is green.
4. Create the Git tag only from the verified `main` commit.
5. If required, create GitHub release notes from that tag.

Creating either the tag or GitHub release **does not publish an image in this
fork**.

## Publishing boundary

Automated image publication is intentionally absent.

Do not:

- invoke `build-and-release.yml` — it does not exist in this fork;
- claim that `main` publishes `v1-dev`;
- claim that GitHub release publication pushes `v1-latest`;
- substitute upstream `lfnovo/*` registry images for a Vält-built release; or
- add ad-hoc registry credentials/publishing commands to bypass the permanent
  compliance gates.

If automated Vält artefact publishing is introduced later, it must arrive through a
separately reviewed engineering change. At minimum it must:

1. use immutable/pinned build inputs where practical;
2. publish only an artefact built from the verified commit;
3. run the final-image SBOM/licence policy on the exact artefact being published;
4. preserve the external FFmpeg/Ollama/Speaches boundaries;
5. preserve PostgreSQL-only normal runtime behaviour; and
6. make registry destinations and promotion semantics explicit.

Until then, the release process ends at verified local artefacts plus optional Git
metadata.

## Known gotchas

- **Never leave a version bump uncommitted.** Keep version/changelog changes in a
  focused cut PR so they cannot leak into unrelated work.
- **A post-tag blocker requires re-verification.** Move/recreate release metadata only
  after the fix is merged and the new commit has passed the full permanent suite and
  applicable artefact gates.
- **Containerised app + host services:** credentials pointing at local Ollama or LM
  Studio instances generally require a host-reachable address such as
  `host.docker.internal`, depending on the operator environment.
- **Legacy database migration is separate from normal boot.** Normal application
  startup must never require SurrealDB.
- **Release-candidate data copies use PostgreSQL from `DATABASE_URL`.** Do not infer a
  database from whichever local process happens to be running.
- **Local image tags can shadow other tags.** Confirm which image digest is actually
  under test whenever validating a candidate.
- **Judge optional-runtime gating on a clean artefact, not a developer venv.** A local
  environment can contain optional packages installed out of band.

## Retro

After a release or major hardening change, update this document when the verified
process changes. Documentation must describe the workflows and boundaries that
actually exist in this fork; it must not inherit upstream publication assumptions by
accident.
