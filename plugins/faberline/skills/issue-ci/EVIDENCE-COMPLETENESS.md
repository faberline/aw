# Offline agent context alternative: exact issue fit

This alternative is not deployed. Production Tape #45 proved the earlier single
Claude invocation and strict receipt path, not this context-planning alternative.
The original manual-admission candidate remains reviewable at local AW commit
`03527f2b7b31496e5ad6b80335963161460498d8` in its separate worktree.

## Why per-diff admission is not the default

The earlier candidate implements no automatic contract generator/adopter. A
trusted default-branch policy maintainer/controller must add a requirement and
source manifest and arrange its separate admission. A new issue/title/body,
changed path set, before/after bytes or Git mode/blob/size change requires a new
admission. Ordinary PRs without that policy update refuse. A human or trusted
process could prepare proposals, but none automatically adopts them there; in
practice this adds an unrequested manual/process bottleneck to each patch.
Do not deploy that requirement merely to claim stronger completeness guarantees.

## Recommended bounded agent flow

1. The controller reads the authoritative issue and exact PR diff, binds current
   head/base, complete regular-file bytes/modes/blobs/sizes and existing successful
   required CI gates. There is no requirement registry or default-branch edit per
   issue or diff.
2. One isolated agent call extracts explicit issue requirements and proposes only
   necessary same-repository supporting paths. Each requirement quotes original
   issue text. Explicitly adopted specifications have a path and literal issue
   reference. Material ambiguity is reported specifically and stops the flow.
3. The controller validates request shape, exact quote presence, safe paths and
   hard bounds, then fetches complete supporting/spec source from exact base/head
   trees. No model tool, arbitrary URL, shell or repository execution is enabled.
4. A second isolated agent call reads the full original issue, explicitly adopted
   spec/source and exact diff. The plan is advisory: omissions/invented items or
   material uncertainty are reported in `contract_uncertainties`, requiring block.
   Per-requirement mappings, all-changed-range accounting and extra-change
   locations/reasons follow the exact-issue-fit schema. Scripts remain responsible
   for coverage, unit, e2e and other deterministic CI gates; the agent neither
   duplicates nor waives them.
5. A fresh collection after review must match. Receipts bind the agent plan and
   every source/diff identity. Verification revalidates that authenticated plan
   against the current issue and refetches the same exact source context.

Maximum two CLI processes, each with a 900-second timeout, distinct fresh HOME/
config/cwd, no tools/MCP/hooks/GitHub token. There is no automatic retry, recursive
context expansion or human per-diff admission. The first call may consume Max
quota even if a requested source is then missing; missing source prevents the
final-review call and pass receipt. No live call was made for this candidate.

## Mechanical integrity versus agent judgment

Machines enforce: successful existing gates; exact issue/PR/head/base snapshots;
request/schema/quote/path/count limits; complete regular Git file identities,
mode, size, SHA256 and source IDs; no silent truncation; actual changed-location
bounds/accounting; consistent exact/pass, missing-or-extra/fail and uncertainty/
block; current authenticated producer and receipt digest. Changed-source bytes
and modes are computed and bound automatically, not manually pre-approved.

Machines do not prove: that every requirement was extracted, that a path is
semantically necessary, that a reference truly adopts a normative spec, that the
model understood all supplied text, or that no extra behavior exists. Exact
quotes and structured source echoes are integrity checks, not completeness or
correctness attestations. These remain the agent's judgment against the full
issue and explicitly adopted spec, with specific uncertainty/block when needed.
Do not market this flow as machine-certified semantic completeness.

Bounds remain 80 changed files, 16 requirements, 16 distinct supporting paths,
256 KiB per source version, 10,000 tree entries, 1 MiB packet and 64 KiB result.
Exact line alignment strips equal prefix/suffix and disables autojunk; remaining
line-count product above 1,000,000 blocks rather than approximating locations.
Only same-repository base/head sources are supported. If an issue adopts an
external or separately versioned specification, the agent must identify that
unsupported evidence need and block; this alternative does not silently claim
to have fetched it. Insufficient context after the second call blocks without
automatically starting another call.

## Before any deployment

Obtain independent code review and a separate bounded live-validation plan.
Compared with the deployed single-call pilot, this flow uses up to two Max calls
and up to 1800 seconds of CLI subprocess time, with unknown quota/cost effects.
There is no new human-admission decision to approve, credential, permission,
coverage threshold or general quality certification field. The five-milestone
>=99% coverage goal stays separate. Consumer action/producer binding still needs
normal reviewed deployment. No publication, live CI, merge or schedule change
is included. Automated merge remains hard-disabled; its durable reconciliation,
mutual exclusion and base/protection contract are separate unresolved work.
