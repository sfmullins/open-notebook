# Test Matrix Template — change → risk → evidence

Instantiate this against the real release diff. The planning unit is the **risk**:
for each change ask what can break, for whom, and what proves the intended behaviour.
For security hardening also ask whether the protection breaks legitimate use.

## Bucket A — permanent automated gates

The final candidate must pass `.github/workflows/test.yml` in full.

| Check | Evidence |
|---|---|
| Runtime boundary | `scripts/check_no_surreal_runtime.py` passes |
| Backend | lock check + Ruff + mypy + full pytest suite |
| Frontend | clean install + lint + tests + production build |
| Frontend dependency security | `npm audit --audit-level=moderate` passes |
| Legacy migration parity | real pinned SurrealDB 2.6.5 → PostgreSQL integration job passes |
| Final artefact compliance | image build + Syft SBOM + fail-closed licence policy pass |
| Documentation | link checker passes |

### Targeted probe library

Add probes when the release diff touches the relevant surface:

- Upload just under/over a body cap → accepted / clean 4xx.
- Legitimate self-hosted/localhost integrations → accepted while link-local or
  metadata endpoints remain blocked where policy requires.
- Reverse-proxy/Host/CORS behaviour → expected fallback or validation, never 5xx.
- SSE endpoints → progressive streaming rather than buffered completion.
- Every enum/allowlisted query parameter → all valid values plus at least one invalid
  value.
- Oversized arrays and unknown-provider payloads → typed 4xx, not 500.
- UI-written settings/fields → verify the complete browser → API → persistence path.
- PostgreSQL relation/ID changes → verify both domain API behaviour and storage
  semantics.
- Media paths → verify that normal application boot remains independent of an
  operator-supplied FFmpeg binary.

## Bucket B — automate when it compounds

Candidates include new end-to-end scenarios, recurring regression probes, and manual
checks that repeatedly catch real defects. Promote a check when automation will pay
for itself across future releases.

Do not create a compatibility shim merely to keep an old test green. Tests should
follow the current PostgreSQL-native repository seams.

## Bucket C — owner/manual confidence

Tailor this to the actual release. Typical checks are:

- real provider credentials for changed integrations;
- a real TTS/podcast flow when media code changed;
- focused visual/UX review of changed UI;
- local candidate-image verification on the operator environment when container
  behaviour changed.

There is no "pushed image" bucket in the current Vält fork because this repository
does not have an automated image-publishing workflow. Do not substitute an upstream
registry image for the local/verified candidate.

## Decision record

For each material release risk record:

| Change | Failure mode | A/B/C | Evidence required | Result |
|---|---|---|---|---|
| `<change>` | `<what could break>` | A | `<test/job/probe>` | pending |

A release candidate remains NO-GO while any required evidence is missing or failing.
