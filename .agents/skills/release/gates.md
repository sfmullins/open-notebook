# Gates — what needs a human, what does not

This file defines the action boundary for release verification in the Vält fork.
The governing process is `.github/RELEASE_PROCESS.md`.

## May be done autonomously once release work is underway

- Run tests, builds, probes and analysis.
- Start/stop disposable local development and test services.
- Create branches, commits and pull requests.
- Build/pull container images locally for verification.
- Run the local release-test harness.
- Dispatch `.github/workflows/test.yml` manually when useful.

The permanent CI workflow is verification-only. There is no authorized automated
container-publishing workflow in this fork.

## Requires explicit, in-session authorization from the owner

| Action | Why |
|---|---|
| Merging a PR authored during the session | Repository state change requiring explicit owner authority unless already granted for the run |
| Creating/publishing a GitHub release | Public release metadata |
| Introducing or using a registry-publishing workflow | Creates externally distributed artefacts and changes the supply-chain boundary |
| Creating GitHub issues | External repository artefacts the owner may not want |
| Bulk/mass labelling | Broad shared-state modification |
| Touching owner/dev production-like data | Work only on explicit copies; never mutate originals |

## Never

- Push directly to `main`.
- Invoke a nonexistent/removed `build-and-release.yml` workflow.
- Use upstream `lfnovo/*`, `v1-dev`, or `v1-latest` artefacts as substitutes for a
  Vält release.
- Mark a gate complete while required checks are failing.
- Weaken the PostgreSQL-only runtime boundary to make tests pass.
- Add SurrealDB to normal runtime; it remains migration/test-fixture only.
- Bypass or downgrade the final-image SBOM/licence policy for publication.

## Re-test policy after a fix

- Permanent `test.yml`: **always** on the final candidate commit.
- Local smoke/image gate: repeat when the fix can affect the built artefact or boot
  path.
- Manual owner checks: repeat when the fix touches what was manually verified.
- Final-image SBOM/licence enforcement: always applies to the exact container
  reference artefact built by CI.

## GO / NO-GO

A candidate is GO for version metadata only when permanent CI is fully green and all
release-specific risk checks are satisfied. A separate explicit decision and a
separately reviewed publishing mechanism are required before any Vält artefact is
distributed externally.
