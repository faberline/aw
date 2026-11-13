# Claude final review and guarded merge: implementation boundary

Design candidate only. This file installs no workflow, changes no permissions,
activates no schedule and provides no acceptance receipt. The existing
qualification, readiness and index code remains the source of deterministic
test evidence. A new implementation must be reviewed before consumer rollout.

The first implementation candidate is now in `scripts/claude_review.py` and
`.github/actions/claude-review`. See [its operation/security contract](CLAUDE-FINAL-REVIEW.md)
for implemented behavior, isolated test commands and explicit rollout blockers.
The steps below describe the original design boundary, not deployed behavior.

## Observed shared state

The shared action already supplies qualification, authenticated readiness and
index operations. `issue-review` records a human/agent judgement using the
legacy done flow. It neither launches a Claude final-review job nor grants
merge authority. The index documentation explicitly leaves semantic completion
storage and review-session coordination as follow-ups. An all-green producer
is insufficient: the named receipt and actual required tests must qualify.

## Next implementation slice

1. Add a pure shared validator for a versioned final-review result and its
   authenticated execution metadata. Bind repository, PR, exact head/base,
   policy/evaluator identity, current prerequisite evidence, issue/spec digest,
   reviewer identity and explicit completed-pass decision. Missing, invalid,
   stale, failed or unknown evidence must refuse. A JSON pass claim or digest
   alone is not authenticated reviewer authority.
2. Add the bounded final-review orchestrator separately from the deterministic
   validator. Validate required tests at exact HEAD before review, and refresh
   identity/evidence immediately before publishing. Review text/PR content is
   untrusted input. Keep the secret out of PR-executing or arbitrary-tool steps;
   the trusted executor and permitted Claude tools need an explicit reviewed
   contract. No token-bearing `pull_request_target` checkout of PR code.
3. Add a distinct guarded-merge entry point with independent fresh verification
   of tests, final-review provenance and exact HEAD. Use the provider's expected
   head guard for the merge request, bind/recheck base and policy, and record
   uncertain outcomes for reconciliation before any retry. A completed review
   is not authorization to invoke this entry point. Its write capability must
   be isolated from the reviewer and test collector.
4. Wire only the Tape pilot after shared code and consumer policy independently
   pass review. Freeze real workflow IDs, job names, immutable source pins and
   trusted policy from the actual default branch. Preserve existing failure-only
   lifecycle and real blockers; do not activate a stale disabled candidate.
5. Coverage progression to 99% is a five-milestone objective. Establish actual
   applicable pilot assertions and negative evidence first; do not introduce a
   blanket immediate 99% pilot threshold or clear real coverage gaps.

Local unit fixtures should cover wrong repo/head/base/spec, unknown outcomes,
forged reviewer results, stale/restarted gate attempts, duplicate completion,
concurrent head movement and uncertain merge response reconciliation. Consumer
CI evidence and independent review remain required before publication. No CI
dispatch, merge, credential provisioning or cross-repository rollout is part
of this design checkpoint.
