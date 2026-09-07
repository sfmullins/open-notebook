# Communication Templates

These templates are for Vält-fork release metadata. This fork currently has no
automated container-publishing workflow, so release communication must not advertise
an upstream registry image as a Vält artefact.

## GitHub release notes structure

```text
**<one-line verdict: what this release is and who should care>**

## Security hardening        (if applicable)
## New features              (user language, issue/PR refs)
## Performance               (if applicable)
## Notable fixes
## Behaviour changes         (anything requiring an operator/config change)
## Validation
    <state the permanent CI and any release-specific/manual evidence actually run>
## Thanks
    <credit contributors>

Full details in the CHANGELOG.
```

Be precise about what protections do and what legitimate use remains supported. Do
not claim a test, artefact, registry push, or migration was performed unless there is
evidence for the exact release candidate.

## Thanks — collecting contributors

1. Collect commit authors in the release range:
   `git log <last-tag>..<tag> --pretty='%an <%ae>' | sort | uniq -c | sort -rn`
2. Resolve non-obvious names to GitHub handles through their merged PRs.
3. Credit each human contributor for the material change they shipped.
4. Thank issue reporters collectively where appropriate.
5. Exclude automation/bot accounts from human contributor credits.

## Short announcement skeleton

```text
Open Notebook <version> — <one-line reason this release matters>

<2–3 lines covering the headline changes>

Validation: <only the checks actually completed on the candidate>

Operator note: <behaviour/config changes, if any>

Release notes: <this fork's GitHub release URL, if a release was actually created>
```

Do **not** add a `docker pull lfnovo/open_notebook:*` line, `v1-dev`, `v1-latest`, or
other upstream registry tag as a Vält-fork installation instruction. If a Vält
publishing workflow is introduced later, communication templates must be updated in
the same reviewed change with the exact registry, digest/tag semantics, and
verification evidence.

The owner controls any external announcement/publication unless they explicitly
delegate it.
