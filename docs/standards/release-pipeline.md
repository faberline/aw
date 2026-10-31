# Shared diff/all tests and milestone release pipeline

Standard ID: **RELEASE-PIPELINE-1**. Owner: **faberline/aw**.

AW defines stages, selection and evidence contracts. Products own commands,
test content, features, platforms, job/batch counts, images, Kustomize and
numeric thresholds. All existing applicable gates remain required. AW has
no Claude review requirement. CI starts no paid review and never merges or
closes PR issues. Local independent product review remains outside CI.

## One test implementation, two modes

Reuse the product's tests and runners for both entries. A PR uses `diff`;
each SemVer RC and formal tag uses `all`. An entry sets event identity and
mode; it does not maintain another copy of tests or thresholds.

Both follow the four [development stages](development-pipeline.md):
preflight; test plan; unit/static; functional/E2E and performance. Every later
stage waits for all required earlier work. Products choose parallel batches.

PR preflight checks current main, merge conflicts and one canonical open
issue. Tag preflight checks immutable tag/commit identity, landed milestone
work, version and goal contract. It does not demand a fictitious PR issue.

Diff selection uses complete changed paths, transitive users and issue gates.
Unknown coverage uses a reviewed full fallback or fails. All selection uses
every check in the approved inventory, including all feature/platform rows.
An unresolved failure-only policy cannot authorize a full release. A missing
test category, empty matrix, zero-test execution, duplicate case or mismatch
between discovered and executed test names fails. Keep explicit reviewed
applicability; a mode flag cannot waive an existing product gate.

The shared `issue-ci/scripts/test_plan.py` produces `AW_TEST_PLAN_V1` with repo,
mode, exact head/base, complete check IDs, preserved check definitions,
policy digest and plan digest. `verify_execution` checks per-case discovered
and executed names, failures and skips. Its output is a control result, not
authenticated CI qualification or publication permission. Existing trusted
product enforcers and current-run verification remain authoritative.

## Planner and release contract

The user sets milestone goals and authorizes milestone creation. The planner
works within that scope. Core product demand links back to the source app's
issue and milestone; Core does not invent a separate product roadmap.

The optional product `.github/issue-plan.json` selects version 1 defaults:
`role` (`product` or `shared`), `tasks` and an existing `milestone` number.
`issue-plan --tasks ... --milestone N --role ...` overrides one pass. Tasks are
triage, queue, gaps, dependencies and release. Both role defaults include
release. Loop companions retain these options on every tick and add timing
only. Neither parsing nor installation starts work or changes a schedule.

The reviewed default branch supplies `.github/aw-release.json`:

```json
{
  "version": 1,
  "repo": "faberline/example",
  "milestones": {
    "7": {
      "version": "1.2.3",
      "goal_sha256": "SHA256_OF_NUMBER_TITLE_DESCRIPTION",
      "acceptance": [
        {"goal": "The user-approved outcome", "issues": [12], "checks": ["real-check-id"]}
      ],
      "publication": "github-release"
    }
  }
}
```

The goal digest is canonical JSON over `number`, `title` and `description`.
Each acceptance item needs real issue and full-plan check evidence. Products
review this mapping with the normal protected-policy process. Missing mapping
or goal evidence creates a scoped planner gap, never an invented approval.
An issue being closed or a partial PR merging into a dependency branch does
not prove delivery on the default branch.

## RC lifecycle

1. Read complete milestone issues, related PRs, landed commits and goal evidence.
   Wait for all issues to close and related PRs to land. Fill real goal gaps
   in the same milestone before attempting an RC.
2. Freeze the landed commit and create the next unique `vX.Y.Z-rc.N`. Verify
   the full workflows accept that tag before creating it. Confirm the actual
   exact-tag run; a tag push alone proves no test execution.
3. Run all tests. Keep original functional, E2E, performance, security,
   platform, artifact and version checks. Preserve every expanded case result.
4. Confirmed failures produce a deduplicated repair issue in the same milestone
   with tag, commit, run/attempt, failed jobs/steps and direct evidence. Use
   normal backlog and guarded queue admission. Do not relabel a worker that
   already claimed work. An observed failure is not a proven root cause.
5. After the repair closes and its PR lands, reassess the goal and make the
   next RC. Preserve failed tags. Do not generate extra RCs while a run is
   queued, pending or unknown, or to bypass a known failure.
6. Promote only the exact passing RC commit to `vX.Y.Z`. The formal tag also
   runs all tests. Verify final publication before closing the milestone.
7. A formal-tag code failure keeps the tag and milestone intact. Repair the
   code and version contract, then use a new SemVer version and RC sequence.
   Never overwrite an immutable tag or published version.

The planner's `release-status` is read-only. `release-advance` requires its
fingerprint, the original exclusive repository guard and fresh provider reads.
It performs one next action. Unknown write responses require reconciliation
before retry. Tag, publication and closure readback remain separate evidence.
No planner action closes a product issue or merges its PR.

## Evidence and adoption

Bind required full workflows to reviewed workflow IDs and source blob hashes.
Select the latest actual tag run and attempt, read all job pages, reject
missing/duplicate/cancelled/skipped/neutral jobs and verify complete success.
Reject foreign sources and ambiguous same-name branch/tag runs. Preserve
case-level nonzero enforcers; a green metadata job cannot prove product tests.

Adopt Core, Lumen, Keep, Tape, Defer, then Relay. Retain current required gates
until the new entry proves their complete coverage. Fixture controls cover
diff/all retention, nonzero rosters, stale inputs, unknown outcomes, duplicate
failure delivery, RC progression, final-tag tests and publication-before-close.
Product adoption also needs real current-head CI and independent local review.
Shared control success alone does not activate a product or publish a release.

Terraform, GKE infrastructure, schedules, client settings and the separate
MCP/legacy-CLI retirement are not changed by this standard.
