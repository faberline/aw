# Claude pass to native auto-merge

This is a separate adapter for GitHub native auto-merge. The legacy
`guarded_merge` operation stays disabled. Publishing this source does not
activate a consumer or approve its qualification policy.

The human requested merge after the latest commit passes CI and Claude review.
Core #74 and Lumen #185 were delivered under that direct authorization after
their exact head, base, successful CI and authenticated review were rechecked.
Core merged to main. Lumen merged to its existing dependency branch. Those
one-off reviews are not receipts from the formal default-branch producer.

## Required consumer state

For merge approval, the consumer needs a separately reviewed complete qualification contract,
`lifecycle_mode: full`, no unresolved global qualification blockers, and the
existing nonempty unit, functional/E2E and performance selection rules. Unknown
paths, missing evidence and unapproved applicability stay blocked. Version 2
also binds the linked source issue title and body and requires an explicit
per-category decision. Unknown or unresolved issue requirements block paid
review and merging. Inspecting a proposed contract in PR CI does not approve it
or authorize a protected gate change. Changing a
label or clearing a string list does not supply this evidence.

The default-branch `claude_final_review` policy binds the review producer.
The separate `native_auto_merge` policy binds the native merge producer.
Each identity has a workflow ID, definition blob hash and exact job name.
The legacy merge-producer fields stay disabled. The review producer uses the
reviewed immutable action. It remains read-only. Only its
model step receives the existing personal Claude credential. The merge job
gets no model credential and executes no PR source or artifact.

The review workflow uses this exact run-name shape so completed and active
attempts have an authenticated target:

```yaml
run-name: AW Claude review PR ${{ github.event.workflow_run.pull_requests[0].number }} head ${{ github.event.workflow_run.head_sha }}
```

The new review action also prints one strict target record before collection
and paid execution. The metadata index selects the latest candidate for each current PR/head.
The result verifier compares its one target record with the run title and
authenticated producer identity. A newer failed, skipped, cancelled or active
attempt for that same PR/head supersedes its old PASS. Another PR's review does not supersede it.
The original verifier's default behavior remains unchanged for old callers.

The consumer additionally declares this native-provider contract with its
actual reviewed ruleset and integration IDs. `enabled: false` is preparation
state; the adapter refuses it.

```json
{
  "native_auto_merge": {
    "version": 1,
    "enabled": false,
    "status_context": "claude/verified-merge",
    "status_app_id": 15368,
    "merge_method": "SQUASH",
    "ruleset_id": 1,
    "producer": {
      "path": ".github/workflows/issue-ci.yml",
      "id": 1,
      "definition_sha": "REVIEWED_NATIVE_WORKFLOW_BLOB_SHA",
      "job": "native-auto-merge"
    }
  }
}
```

The IDs and workflow blob are placeholders, not deployed identities. Native auto-merge and squash
must be enabled in the repository. An active branch ruleset must require
`claude/verified-merge` from the declared integration and require tests against
the current base. This proposal accepts no ruleset bypass actors. Repository
workflow writers and ruleset administrators remain trusted authorities.
The adapter reads actual active rules; the checked-in declaration is not proof
that the provider enforces them.

## Native write sequence

1. Verify the formal review producer, current attempt and strict passing receipt.
   Recollect the exact head, base, issue, source inventory and configured CI.
2. Compute qualification with the approved selector. Every required category
   needs real passing evidence or its explicit reviewed applicability decision.
3. Read the current provider PR, repository setting and active strict ruleset.
4. Publish a pending status on that exact head. Enable the native request with
   `expectedHeadOid`, the PR node ID and squash method.
5. Verify the same receipt, issue, CI, policy, native request, head and base
   again. Only then publish the passing head status.
6. Read GitHub's request or merged result. Record the observed state.

GitHub stores the pending auto-merge request and final PR result across runs.
The adapter does not implement a second immediate merge endpoint. A queued
request cannot inherit a passing status from a different head. An error after
a write is held for observation. The same attempt does not blindly repeat the
queue or success write. An already merged matching head is observed without a
new write. Existing native requests with another merge method are refused.

The outcome workflow serializes all status and lifecycle writes in the existing
repository-wide `issue-ci-handoff` group.
Each wake paginates all open PRs and all producer attempts. It re-evaluates the
complete queue in stable PR order. A refused PR does not stop another PR. This
recovers PRs whose pending wake was replaced. GitHub applies the final merge and closes the single issue named by the
required `Closes #N` line, because only the default target branch is accepted.
No issue is closed while a request is merely queued.

A fresh authenticated `fail` or `block` decision returns the linked open issue
to `todo`. It adds todo before it removes doing or done. Other labels
remain. Each removal rechecks the exact PR, issue contract and latest review.
Closed or blocked issues, stale heads and missing/invalid review receipts are
held. An interrupted mixed-label update can finish on a later wake. Existing
CI failure-only behavior remains unchanged; the later Claude outcome owns its
requested return to todo. The writer runs in the same serialized lifecycle
group as CI. It posts and updates one receipt-bound recovery journal comment
per negative result. It never writes issue bodies or deletes branches.

See the [native merge workflow template](templates/claude-native-merge.yml.example)
and [review producer template](templates/claude-final-review.yml.example).
Consumer rollout must bind real source hashes and workflow IDs before enabling
the adapter. Preserve the other sessions' planner schedules and existing
qualification holds during preparation.

## Validation boundary

`test_native_merge.py` covers pending-before-queue ordering, the expected-head
argument, repeat verification, durable native requests, replay observation,
failure-only and qualification holds, missing/non-success/unmapped evidence,
provider rules, draft/head/base drift, method conflicts, ambiguous responses,
credential separation and complete queue processing.

`test_native_review_index.py` uses the actual review verifier with synthetic
provider metadata. It checks per-PR ordering, superseding failed/active attempts,
target-record agreement, duplicate/boolean rejection, rerun attempts and foreign
producer exclusion. The full inherited control suite remains required, with
the pinned parser and both JSON Schema interoperability validators installed.
The added hosted workflow refuses skipped controls.

These controls are not a live consumer merge proof. Read back a real current
review, qualification result, native request and final merge before declaring
a production rollout operational.

GitHub documents the expected-head auto-merge input in the
[GraphQL reference](https://docs.github.com/en/graphql/reference/pulls#enablepullrequestautomergeinput)
and the active strict rule metadata in the
[branch rules API](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch).

## Requested lifecycle

- Claude PASS plus complete approved qualification: queue native squash merge.
  GitHub closes the explicitly linked issue only after the merge completes.
- Claude fail or block: return the linked open issue to `todo`.
- CI pending/failing, missing review, stale inputs or coverage hold: do not merge.

`test_review_lifecycle.py` covers rejection, blocked/closed issues, contract or
head drift, unrelated labels, interrupted recovery and retained coverage holds.
The writer contract also verifies the shared mutex, one pinned action step,
exact permissions and the trusted review trigger.

## Revocation and closing scope

Each outcome wake first withdraws this adapter's old passing head statuses and
native requests across the complete open queue. It then builds the index and
verifies fresh eligibility. A refused or unknown review never leaves its old
approval active. A valid negative review publishes failure before changing labels.
The workflow also wakes on review start, PR metadata/head changes, and issue
contract/state/label changes. These are metadata events; no PR source executes.

Only one canonical `Closes #N` body line is allowed. Other closing keyword
references in the title or body are refused. Native squash input fixes the
commit headline and body to the PR number and that issue. It never inherits
closing references from individual commit messages. All six consumers currently
use bare `todo`, `doing`, `done`, `blocked`, matching the existing CI controller.
The adapter preserves that namespace. Both blocked spellings fail closed.

The included findings publisher authenticates failed jobs with valid negative
receipts. It still rejects a passing receipt from a failed job. The hosted
control suite also includes the inherited `scripts/security/tests` controls.

## One lossless writer

Install the outcome template as the replacement `.github/workflows/issue-ci.yml`,
not as a second workflow in the same concurrency group. Policy requires that
path. The one writer wakes on every configured CI workflow and Claude producer,
plus issue/PR metadata events. Every surviving run first revokes old approvals,
then reconciles existing CI handoff for the complete managed queue, then
reconciles Claude outcomes for the complete open queue. A displaced pending wake
therefore loses neither kind of work. A CI handoff error holds that PR after
withdrawal; other PRs continue.

PR-target run metadata may describe a fork's source branch. Writer identity for
that event instead binds GitHub's base workflow ref and immutable workflow SHA,
with the reviewed default-branch definition and exact job. It never loads the
PR workflow or source. Issues and workflow-run events retain the original
same-repository/default-branch checks. `test_unified_outcome.py` exercises the
real entrypoint identity for every event and both reconciliations after
replacement of each kind of pending wake.

## Bounded history work and preserved CI journals

One `native_review_index.Session` owns the complete metadata index for a wake.
It selects current open PR/head candidates before reading any source or log.
Verification refreshes only the metadata since the high-water run and the
selected run's current attempt. Immutable Git content and completed job logs
are cached within that wake; mutable PR, issue, gate and provider state stays
fresh. A missing or over-limit delta refuses instead of accepting old evidence.
The findings publisher uses the same per-PR verifier. A newer other-PR or
unassociated wake cannot hide an authenticated negative result.

The large-history control uses 4,001 runs and five real verifications. It
requires one initial paginated history read, one latest-candidate log download,
zero historical-run queries and a bounded API call count. This is an isolated
index control, not a guarantee that arbitrary repository traffic never reaches
GitHub's provider limits.

The template filters PR-target events to the verified default base before they
join concurrency. Replace `main` if the verified default branch has another
name. A non-default dependency-branch event cannot displace a legitimate wake.

Consumer policy may retain its exact previous CI configuration in
`ci_handoff_policy`. The unified writer passes that snapshot to existing CI
handoff, preserving its authenticated journal identity when only review
metadata is added. Required workflows, mode, qualification rules and blockers
must be exactly equal to the new root policy; any semantic change refuses.
This avoids repeating an already-applied failure handoff or undoing an AGY
retry during rollout.


Each authenticated negative receipt has a pending/ready/applied issue journal.
The writer supplies todo before removing doing/done and preserves other labels.
A ready checkpoint without todo means a later actor consumed that label. It
finishes the checkpoint without adding todo again. An applied receipt never
replays its label writes, so it cannot undo a later agent claim. A new verified
negative receipt starts its own handoff. Current review, PR head, issue contract
and blocked/closed checks still apply before every label operation.

When todo already exists, the first durable journal starts at ready. A new
pending handoff requires an existing managed doing/done label. This keeps the
old lifecycle label until ready is durable, so an agent cannot claim a sole
todo during that window. Unmanaged issues remain held instead of guessing a
lifecycle or creating a pending sole-todo race.

### Source issue contract v2

A v2 qualification policy retains every v1 path rule, trusted workflow,
check ID and protected path. It adds a `contracts` object keyed by the
canonical linked issue number. Each entry has these exact fields:

```json
{
  "title_sha256": "<SHA-256 of the exact UTF-8 issue title>",
  "body_sha256": "<SHA-256 of the exact UTF-8 issue body>",
  "applicability": {
    "unit": {"status": "required", "checks": ["unit-check"], "reason": "Issue requires unit tests"},
    "functional_e2e": {"status": "unknown", "checks": [], "reason": "Source-specific gate has no evidence"},
    "performance": {"status": "not_applicable", "checks": [], "reason": "Reviewed issue has no performance requirement"}
  },
  "additional": [],
  "unresolved": ["Missing source-specific functional gate"]
}
```

A required check must exist in the policy and have the named category. Each
reason must describe the reviewed issue, not merely assume a gate is absent.
`unknown` or a nonempty `unresolved` list holds qualification. Required and
additional checks add to the path-selected set. Neither an exemption nor a
candidate inspection removes existing checks. The qualified result binds the
issue number, exact content hashes, reviewed contract hash, head and base.

### Separately reviewed protected-policy adoption

A protected policy or CI change cannot pass its own ordinary qualification.
The separate policy-adoption actions support a one-target bootstrap. Publishing
these actions does not activate a grant. The default grant file starts empty.
All ordinary protected-path, failure-only and qualification holds stay in place.

1. A fixed target-review workflow runs only the pinned target-review action.
   It reads PR source and issue text through GitHub metadata. It never checks
   out or executes PR code. It validates the current base, complete CI jobs,
   source issue and proposed policy before tool-free Claude runs. Its log
   receipt binds all file identities and each reviewed issue requirement. A
   separate serialized outcome job authenticates that receipt. A failed or
   blocked review returns the issue to `todo`; a pass makes no lifecycle write.
   A successful workflow job by itself is not a passing review receipt.
2. A data-only AW PR proposes a grant for one exact target head and base. It
   names the exact candidate and prior policy blobs, issue and PR hashes,
   protected paths, all current CI jobs, and the authenticated target review.
   The fixed grant-review action independently checks this data and its current
   evidence. Claude receives no tools and no candidate code executes. A passing
   log receipt binds the grant data before the receipt metadata is appended.
   Run the same independent review again on the final PR head. It verifies the
   earlier receipt and reviews the final grant file. Its own receipt remains
   outside the grant, so the review cannot change the head it reviewed.
3. After the data-only grant is independently reviewed and merged to AW main,
   a fixed serialized publisher verifies its ancestry, the grant-review log,
   the target-review log, GitHub ruleset, candidate policy, original job set,
   complete current CI, issue and PR identities. It writes pending, queues
   native squash auto-merge at the exact head, repeats the checks, then writes
   the required success status. The squash message closes the linked issue.
   A missing grant, unknown evidence, stale base, changed contract, non-success
   job or unapproved protected path cannot authorize the status.

The target-review and grant-review workflows have only the pinned actions and
explicit permissions required by their role. The privileged publisher has no
model credential, checkout, arbitrary shell or PR artifact input. Its workflow
shares the `issue-ci-handoff` concurrency group with the existing writer.
No status, merge request or active grant is created merely by publishing these
sources. GitHub owns the final merge and enforces its strict base rule. The
controller cannot make GitHub's issue updates and its final merge atomic, so it
rechecks issue and source identities immediately before the final status write.
