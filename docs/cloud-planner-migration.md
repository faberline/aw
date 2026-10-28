# Six issue planners: hosted migration candidate

Status: published implementation candidate, [draft PR #90](https://github.com/faberline/aw/pull/90);
hosted cutover is **not ready**.

## Goal execution: dedicated cloud environment and lease rollout

The human explicitly started the goal to complete transfer and disable the six
local schedules. Work continues against that goal; the goal is not complete.

A dedicated Codex Cloud environment named `faberline-issue-planners` was prepared
with AW and the six product repositories. Its setup chat is
https://chatgpt.com/local/01a1808d-e329-75ef-9ae3-ad2aa36be084 . This is an OpenAI
durable cloud workspace, not the user's local checkout. It has Python 3.12.14,
Git and gh 2.46.0 with platform-provided GitHub authorization. Initial milestone
REST reads were refused by the environment's network policy. A private draft
adds only `api.github.com`, keeps general internet access off, and adds no secrets.
Automatic approval review rejected publication as an access/security change.
The human then explicitly approved publication of this exact private environment,
seven repositories and GitHub API domain. Publication was started; completion
and a new-runtime REST read still need verification.

The source candidate now includes a persistent per-issue lease ledger and
checks in both native and gh planner paths. The controller reran 57 planner,
81 loop and 84 worker controls, all exit 0 (222 focused controls). The worker
records claiming/coding/submitted transitions and preserves uncertain outcomes.
Planner writes reject a lease or a doing/done target, including review work after
the coding lease is released. These are simulated-provider checks, not deployment.

Local read-only inventory found Lumen #45 and Tape #19 in coding phase. Their
existing local leases remain untouched. The installed marketplace skills still
need a verified update and scoped activation. Six legacy Claude skill files
contain prompts without activation state; supported scheduler evidence is still
needed. No production schedule has been cut over or disabled during this phase.

The final source integration for this phase passed 58 planner, 99 loop and 85
worker controls (242 total, exit 0), shell syntax and diff checks. The rollout
entrypoint now canonicalizes protected repository names, refuses a missing
config for the six local targets, and also runs from direct Python entrypoints.
Other repositories retain their existing local behavior. HTTPS, SSH URL and SCP
origins share one parser. Independent review found the previously omitted
portable bootstrap and non-executable shell wrapper; both are corrected. A test
now directly executes the built portable planner outside a Git checkout with a
read-only provider fixture, rather than merely checking file hashes.

The installation config has protocol `aw-issue-coordination-entrypoint-v1` and
the exact ordered repositories core, defer, keep, lumen, relay, tape, each under
`faberline/`. No config or new source has yet been installed into marketplace
skills, and no remote coordination branch or ledger has been initialized.
The protected local leases remain unchanged. The approved environment publication
still shows in progress; do not start another publication or infer readiness.

Scope: Core, Defer, Keep, Lumen, Relay and Tape issue planning. Keep the existing
two-hour cadence. Do not change product issues or owners as part of validation.

## Scope correction

Issue planning reads product source through GitHub and writes issue metadata or
spec comments. It does not require a local product checkout or shell GitHub
credentials. The initial hosted probe over-constrained the target by requiring
shell `gh`. A native connector adapter now injects canonical REST requests into
the existing deterministic planner and shared guard. Its issue PATCH scope is
limited to labels/milestone and refuses body overwrite, closure and cross-repo
writes. The previous gh-only probe cannot qualify or disqualify this adapter.

The native adapter and current guard passed 28 planner and 75 loop controls.
The native path rejects PR targets and requires operation-specific success
identity/results. Empty responses, error envelopes and mismatched fields retain
the guard for reconciliation instead of reporting success.
Actual hosted connector bindings, endpoint coverage and coordination context
still need live verification. Neither the fixture transport nor tool descriptions
prove the connected GitHub tools can satisfy every canonical API request.

## Live verification on 2026-10-28

The hosted follow-up tested `719cb392669d780e7f9430e349a56330c63b3beb`.
Its nine-file bundle was built and verified at
`/private/tmp/aw-cloud-planners-20261028-719cb39`. The six local definitions were
read back as active, local, and every two hours. No production cutover occurred.
The current source passed 28 planner, 75 loop and 81 worker tests (184 total),
all with exit 0. `git diff --check` also passed.

The connected GitHub tools now answer reads. These are actual local-controller
tool results, not proof of the hosted runtime's tool set:

| Read | Actual result |
|---|---|
| Core native prerequisites, `issues/73/dependencies/blocked_by?per_page=100&page=1` | JSON `[]` |
| Core `milestones?state=all&per_page=100&page=1` | Connector rejects the endpoint with HTTP 400 `INVALID_ARGUMENT`; not an empty milestone catalog |
| AW pinned `connector.py` through Contents GET | File text, without the canonical Contents metadata required by the guard |
| The same file through `fetch_file(encoding=base64)` | Base64 bytes and blob SHA are present; a future binding must normalize this explicitly |
| `branches/aw-coordination` in all six product repositories | Six `404 Branch not found` responses |
| PR #90 head | Matches the implementation source above |
| Check-runs at that head | Zero check-runs; no CI acceptance inferred |

There is no milestone-list tool in the current connector inventory. Core is
public, but a normal public HTTPS read is only a possible alternative until
tested in the hosted runtime. Do not substitute milestone values from issues
for the complete catalog. Do not treat normalized file text as a Contents API
object or infer success from a tool's `Action completed` message.

The older hosted report is now readable through `read_thread`. It reports
Python 3.13.5, shell/Bash, and eight matching source hashes at `3b32e84`.
It also reports missing `gh`, a missing Core coordination branch, and unknown
AW peers/inbox/leases/adoption/cutover evidence. This recovers the previously
unreadable report; it does not validate the newer native adapter.

The existing one-time validation definition was updated to prefer native
GitHub tools, without a checkout or `gh` prerequisite. Saving its past one-time
date changed its visible state from paused to completed. It has no recurring
trigger. The read-only follow-up in the same hosted validation chat completed.
No second schedule was created. Its report records:

- Core main `41c352eaae687a1914087d279f36adbe9e9b4af5` and README read.
- Open issues pages contain 67 and 0 records; 22 of the 67 are PRs, leaving
  45 issues. Open PR pages contain 22 and 0 records.
- Issue #73 comments and native prerequisites each have empty first and second
  pages. This does not replace reading prose dependencies of other issues.
- Python 3.13.5 runs. The pinned `connector.py` SHA-256 matches
  `dd79e606a3455c2af9862ca6dc9be36cb8e234f61ef9756039bc6800507143a2`.
- Milestone reads fail in the native connector. Web reads are unavailable, and
  hosted unauthenticated `curl` exits 6 because it cannot resolve api.github.com.
  No tested hosted route provides the required complete milestone catalog.
- Hosted AW peers, inbox, coding leases and legacy planner pause evidence remain
  unknown. The report calls the outcome partial, with incomplete read capability.

AW admission now works for this worktree. The configured endpoint is port 8766;
the earlier direct-client attempts used the documented default port 8765.
This session read its empty inbox and obtained the advisory worktree claim.
Local session records are readable again. They include reported busy records,
but activity labels alone do not prove current ownership or protocol adoption.
This local recovery does not make AW available to hosted runs.

## Parallel native binding and independent review

The human requested Terra/Luna parallel work. Terra prepared the new
`native_transport.py` and its tests in an isolated temporary tree. The controller
integrated them into the claimed worktree. The planner suite now passes 44 tests,
including 16 binding controls and a real shared-guard fixture with native tool
envelopes, SHA-checked transitions, one issue update and owner-only release.
Together with the unchanged loop and worker suite results, 200 focused controls
have passed. These are fixture checks, not live mutation acceptance.

Independent forward review reproduced a known unsupported-comment refusal that
retained the guard even though no issue write was dispatched. The correction
adds a pure prevalidation hook before marking a write attempted. Follow-up review
also reproduced retention after the native transport's last target read failed.
The native transport now owns marking at its final provider dispatch boundary.
Full-cycle fixtures prove that known capability refusals and failed pre-write
reads release the guard, while an uncertain provider PATCH remains held after
exactly one attempt. Generic callbacks keep conservative pre-dispatch marking.

The binding maps native tool envelopes and base64 file metadata explicitly.
The host still needs a verified synchronous callback bridge. Real issue mutation
response shapes are unverified. Milestone reads, issue-comment writes and
cross-repository dependency reads remain explicit refusals. The portable bundle
file set now also includes this binding; the older nine-file bundle above does
not contain it.

Luna's independent lease audit confirmed that universal short-guard adoption
alone is insufficient. `spec` and `prioritize` can currently target any open
issue, while a worker's coding lease outlives its command. Hosted writes need
fresh per-issue lease/hold evidence under the guard, including claiming leases
that still have a `todo` label. That hosted-readable lease protocol remains
unimplemented. There is no need to stop unrelated workers, expire leases, or
infer ownership from old activity labels.

The final packaged implementation is
`5c637de173851a03da8396e29d4d742802215641`, published on the draft PR branch.
All ten bundle files passed immutable-source comparison and SHA-256 verification
at `/private/tmp/aw-cloud-planners-20261028-5c637de`. Luna independently rechecked
the final marking boundary and 44 planner tests, with no remaining defect found
in that reviewed transport/guard path. This does not resolve the hosted runtime,
milestone, comment, cross-repo dependency or per-issue lease gaps above.

## Candidate

- Portable issue-plan rules, milestone policy, wrapper, Python helpers and
  SHA-256 manifest. Bundle construction checks each file against its immutable
  source commit. Hosted runs must also check against the hashes in their saved
  prompts; a supplied manifest is not self-authenticating.
- `plan.sh --cloud status` has no local checkout, Git-directory or local-lock
  dependency. It remains a read-only, non-atomic snapshot with no write authority.
- Opt-in GitHub Contents API shared guard on a dedicated `aw-coordination`
  branch. Expected blob SHA protects acquisition. New generation UUIDs protect
  against reuse of old free snapshots. No automatic expiry, stealing or retry.
- Matching local planner/worker adapters retain existing flock and issue leases.
  Before an external mutation, they verify the shared owner. Pre-write validation
  refusals release; errors after attempted mutations preserve the lock.

The protocol covers short mutation commands, not the whole coding session.
When `work next` returns, an issue can remain owned under its existing local
lease. A free GitHub guard cannot prove that a coding owner is absent.

## Verification on 2026-10-27

| Check | Actual result |
|---|---|
| `python3 -m unittest discover -s plugins/faberline/skills/issue-loop/tests -q` | 75 passed, exit 0 |
| `python3 -m unittest discover -s plugins/faberline/skills/issue-plan/tests -q` | 16 passed, exit 0 |
| `python3 -m unittest discover -s plugins/faberline/skills/issue-work/tests -q` | 81 passed, exit 0 |
| Independent forward review | Pre-write lock retention and omitted wrapper corrected; guard follow-up passed; malformed SHA error normalized |
| Skill Creator `quick_validate.py` | Rejected pre-existing Claude `user-invocable` metadata on both original and candidate; not a passing Codex schema check |
| Actual hosted runtime, authorized shell `gh`, native dependency API | One read-only hosted probe dispatched; result could not be read, so capabilities remain **unknown** |
| Live shared-guard acquire/contention/release | **not_run** |
| Live writer adoption and hosted-readable sessions/inbox/leases/cutover | **not_verified** |
| Six production hosted task IDs/status/cadence and first natural runs | **not_created / not_run** |
| Immutable portable bundle | Built and verified 8 files at `3b32e84bfde994ab8fe820e85c5df34550922116` |

The existing main checkout has unrelated README and roadmap WIP. It was not
changed. The candidate is isolated in `codex/cloud-issue-planners`.

## Earlier transport failures

The initial GitHub connector metadata request failed at its MCP transport endpoint.
The existing `/opt/homebrew/bin/gh` provided a working fallback for publishing
the candidate branch and creating draft PR #90. A later read of the candidate's
check-runs failed with `connect: can't assign requested address`; CI remains
unknown at that point. No issue write was attempted by either failed read.

The initial published source revision was `3b32e84bfde994ab8fe820e85c5df34550922116`.
Its portable context remains `/private/tmp/aw-cloud-planners-20261027-3b32e84`.
Use the final packaged implementation recorded below.

## Hosted probe

A single read-only capability validation was created and manually dispatched:

- Cloud automation ID: `6ae0c76939b08190bf590bf4c500829b`.
- Run chat: https://chatgpt.com/c/6ae0c77f-9adc-83e8-bf07-b8c941e5fb37
- Its future trigger was paused after dispatch. The paused state was verified
  in the Scheduled UI. It is not a production planner or a recurring duplicate.
- The run record reports a completed turn. The reader returned only a
  `chatgpt-content-reference`, and the browser showed an unavailable response.
  A reload failed with `ERR_ADDRESS_INVALID`. No shell, auth, hash, queue or
  peer result was readable, so none counts as passed.
- A screenshot was saved to this chat's visualization output directory as
  `cloud-validation-error-detail.png`.

At that point this was an attempted hosted validation with an unknown result.
The report has since been recovered, as recorded above. The probe was read-only;
do not blindly rerun any mutating operation.

The earlier AW proxy had this chat's identity, but only its bootstrap tools were exposed.
`register_session` refused a duplicate and directed the client to `update_session`.
The installed direct client could not connect to its loopback AW endpoint. No
scope update, inbox read or worktree claim was claimed successful then. The new
admission and direct-client recovery at the configured port are recorded above.

Current native session/inbox/lease and old-planner pause evidence still need a
hosted-readable source adopted by the live local actors. The shared guard alone
does not implement that source. Missing evidence continues to block mutations.

## Resume

1. Resolve the hosted milestone-read and native-binding capability gaps above.
2. Use the published immutable source and verified bundle above. Preserve the
   source review findings and actual test exits; they do not prove hosted readiness.
3. Establish hosted-readable peer/owner/lease/cutover context without copying AW
   session credentials. Deploy coherent shared-guard support to all live writers
   only after their existing ownership has been reconciled. Preserve all leases.
4. Initialize dedicated workflow-free coordination branches and run the real
   hosted acceptance in `issue-plan/CLOUD.md`. A missing capability blocks cutover.
5. Only after acceptance, prepare the six hosted definitions paused. Pause the
   corresponding local definitions and verify no run remains in flight. Enable
   hosted schedules at two-hour intervals, then verify their first natural runs.

The six local definitions were read back as active, local, and every two hours.
There is no production hosted duplicate, deployment, coordination-branch write,
product issue write, or cutover. The one-time validation definition is completed
and has no recurring trigger.
