# Automatic issue snapshots

Qualification v3 is opt-in in the reviewed default-branch policy. It uses the
fixed `.github/issue-contract-snapshot.json` data file in each PR's exact head.
The issue-work finish step creates and commits this file automatically.

The snapshot records the repository, issue number, and SHA-256 of the live
issue title, body, and latest planner specification. A planner specification is
the newest `<!-- issue-plan:spec -->` comment, or the issue body when there is
no such comment. The trusted controller reads the file from the exact PR commit
and compares it with the live issue and complete comment inventory. Missing,
malformed, foreign, blocked, closed, or changed inputs cannot qualify.

V3 selects every check in the trusted policy. It preserves existing reviewed
issue contracts, including their unresolved blockers and pinned content. This
does not prove that the issue's own acceptance criteria are met. Independent
issue review remains required. Existing v1 and v2 behavior stays unchanged.

Adoption needs a protected policy proposal, complete CI and independent
review. The product adapter must use the pinned controller to load the same
snapshot and planner specification when it builds its execution plan. Do not
activate v3 until the product runner, CI writer and installed issue-work skill
all use the same verified implementation.
