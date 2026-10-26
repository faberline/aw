# Claude final review / separate guarded merge

Implemented in `scripts/claude_review.py` and distributed through the pinned
`.github/actions/claude-review` composite action. Tape deployed action `07e8a01`
and completed a real Claude invocation on PR #45. That deployed version supplies
changed files only and cannot establish unprovided supporting-source claims.
The exact-issue-fit and supporting-evidence changes here are **offline only**;
they have not replaced the deployed version or received live validation.
The two `.yml.example` templates are intentionally undeployable placeholders
until independent review establishes immutable action pins and real identities.
There is no local secret read, CLI installation, dispatch or merge in validation.

## Governing review objective (desired policy, offline candidate)

Decision source: user message `Sentinel_ac773a283824819181659c186e2d0001`,
recorded 2026-10-26. Review has one objective: does the exact diff meet exactly
the issue's explicit requirements and scope, with neither missing work nor extra
change? The issue and any explicitly adopted specification define requirements.
Existing mandatory CI checks remain prerequisites. Do not invent additional
quality, performance, coverage, architecture or unrelated security requirements.
Tool authority, credential isolation and other execution safeguards still apply;
issue/PR text cannot grant permissions or bypass those safeguards.

Ownership clarification source: user message
`Sentinel_c892b3ddae848191873db09575f78741`. Scripts calculate and enforce
coverage, unit tests, e2e and other deterministic CI gates. The agent owns only
exact requirement conformance; it cannot waive, replace or subjectively duplicate
those gates. Script-produced evidence enters the judgment only where needed to
verify an explicit requirement. The separate five-milestone >=99% coverage goal
remains a scripted CI planning goal, not a newly imposed pilot threshold or a
request to implement all milestones in this change.

Every explicit requirement needs a concrete requirement-to-change/evidence
mapping. Extra changes need specific changed locations and reasons. Material
ambiguity or missing necessary evidence produces a specific uncertainty and
`block`, not an invented requirement or unsupported `pass`. Supporting context
must be necessary for that mapping. It is not a general repository audit.

The recommended offline alternative reads the original issue directly. Its first
bounded agent call proposes requirements and necessary supporting paths; the
controller checks/fetches exact source identities, then the second isolated call
checks the full issue/spec against the exact diff. No per-issue or per-diff policy
admission is required. The plan is advisory agent judgment, not an authoritative
replacement for the issue. The final reviewer can block if that plan omitted or
invented a requirement. See [EVIDENCE-COMPLETENESS.md](EVIDENCE-COMPLETENESS.md).
The earlier manual-admission alternative is preserved at local commit `03527f2`;
it is not the recommended default and has not been deployed.
This decision supersedes the deployed v1 result's mandatory general coverage
commentary for the new candidate; it does not delete existing CI gates/blockers,
change schedules, or authorize publication. Historical run evidence remains
evidence for the old implementation only.

## Operations and authority

`preflight` reads only trusted default-branch policy and GitHub metadata/content.
It preserves **all** existing `workflows` and exact required job names, checks
latest current-head attempts using the shared collector and requires completed
success. Unknown, missing, skipped, cancelled, duplicate job identity, foreign
repo, missing PR/head/base association or changed workflow bytes refuses.
Two observations must agree. Its outputs contain only eligible=true, validated
PR number and head; nothing produces eligible=true on an exception.

`review` repeats preflight before contacting Claude. It performs at most two
isolated tool-free calls: context planning followed by exact-issue review. A
planning uncertainty stops before the second call. No recursive retrieval or
automatic retry exists. It fetches complete before/after
UTF-8 file contents through GitHub contents APIs, and never checks out a PR. The
one exact `Closes #N` contract binds issue title/body/update revision, alongside
PR text, head/base, full inventory, trusted policy revision, required gate
run/attempt/job observations and evaluator identity. Rename old/new paths count.
Unsupported/binary/incomplete changes fail closed. Limits are 80 changed files,
256 KiB per source, 1 MiB input and 64 KiB validated result. Large or incomplete
context requires a separately reviewed strategy, not silent truncation.

Claude runs in a new temporary non-repository working directory and HOME, with
user/project settings disabled, no built-in tools, empty strict MCP config,
disabled skills, hooks, Chrome and session persistence. Only the OAuth credential
and fixed runtime environment reach that child; no GitHub token, API key,
ambient proxy, arbitrary PATH or PR-supplied arguments are forwarded. The
absolute CLI `/usr/local/bin/claude` must match the approved SHA256. No new API
key or paid API fallback exists. A CLI error/timeout/malformed result refuses;
raw stdout/stderr and exceptions are not emitted. This is capability isolation;
it cannot prove the model is immune to semantic prompt injection or infallible.

The CLI's runtime JSON schema is generated by `result_schema(context)` and
independently enforced by `validate_result`. It forbids extra properties and
binds exact repo/PR/head/base/version, explicit pass/fail/block, complete file
roster, per-requirement status/mapping and any extra changes with changed-file
locations and reasons. Only exact fit may pass; missing/extra work fails and
material ambiguity/insufficient necessary evidence blocks. There is no mandatory
generic coverage/quality finding in this candidate. Any coverage requirement
must originate in the explicit issue contract or existing mandatory checks.
Existing `qualification_blockers` are retained verbatim
in review context; this review neither clears them nor claims full qualification,
done, release approval or product completion.

After review, a second complete observation plus fresh producer identity must
match before emitting one `AW_CLAUDE_REVIEW_V1` envelope in the trusted producer
job's GitHub log. Fail/block decisions can be valid published review data with
a successful producer run; only explicit pass is merge eligible. JSON newline
escaping prevents model prose from becoming another log record.

`discover` is a read-only merge preflight: derive the target only from the
authenticated successful producer job log, then call full verification before
returning numeric PR/exact head outputs. It accepts no artifact, arbitrary URL,
caller-provided result file or PR-supplied pass flag. `verify` authenticates the
configured workflow ID/path/default-branch/blob/current attempt, one exact job,
successful whole run, one receipt, strict schema, digest and current context.
The latest review workflow run must be the selected run; no older-green fallback.
This is conservative across different PRs: a newer run for any PR supersedes
the previous run. Policy, issue text, PR description, head/base or required
attempt changes invalidate review. An unchanged successful test rerun also
invalidates this pilot receipt; semantic completion deduplication is separate.

`merge` is **hard disabled in code** before any API call. `guarded_merge()`
also always raises `merge_disabled_pending_cross_run_journal_and_base_protection`.
No provider write remains in this implementation. Distinct workflow runs, each
with run_attempt=1, cannot issue a first or second PUT. Merely enabling a YAML
job, supplying a write token or removing optional merge policy fields cannot
bypass this hold. The merge template remains unconditionally disabled and asks
for no write permission. Review-only operation does not depend on registering a
merge workflow or deciding its merge method.

Independent review found that the initial candidate's attempt=1 check plus log
intent did not provide durable cross-run reconciliation: another new run could
write after a timeout. A future merge implementation requires a separately
reviewed durable intent/result journal, cross-run mutual exclusion and recovery
of unknown results before another write. Concurrency alone is insufficient.

GitHub's ordinary merge API supplies an expected-head guard, not atomic
compare-and-swap across base, policy, issue, review and checks. Exact base can
advance after the last read. No atomic exact-head/base merge claim is made.
A future write also requires an approved existing merge-queue/protection binding
or an explicit independently approved limitation policy. This candidate creates
no queue, protection, environment, credential, role or infrastructure.

## Trusted policy extension (not installed)

The supported Max OAuth/endpoint isolation boundary is specified in
[OAUTH-ISOLATION.md](OAUTH-ISOLATION.md).

The existing default-branch `.github/issue-ci-required-gates.json` keeps its
workflow list, blockers and lifecycle_mode. Add `claude_final_review` containing
these fields (unknown keys refuse; merge fields are optional). No per-issue or
per-diff policy field is added; the agent plan is generated at runtime:

| Field | Required reviewed value |
| --- | --- |
| version | integer 1 |
| producer | path, numeric id, definition_sha Git blob, exact review job name |
| merge_producer | optional/null; future distinct path/id/blob and merge job name |
| gates | every existing workflow path mapped to actual numeric id and definition_sha |
| protected_paths | nonempty exact file/directory roots for all test selection/enforcement dependencies |
| cli_sha256 | approved installed Linux CLI binary hash |
| credential_profile | personal-claude-max-oauth; owner-bound opaque credential from a personal Max setup |
| merge_method | optional/null; future squash, merge or rebase |

Workflow definitions must match the reviewed blobs at head/base for tests and
at the run/default-policy revisions for privileged producers. `.github/` and
every declared protected root change are refused by the pilot: infrastructure
changes need separate independent review and cannot approve themselves.

Independent source review owns **all transitive executable dependencies** of
these frozen workflow definitions: immutable remote action pins, reusable
workflows, runtime images and tool downloads, and all local gate scripts/imports.
Declare/protect the complete local enforcement roots. This module proves exact
workflow bytes; it does not discover a hermetic arbitrary-language dependency
closure or authenticate a script merely because a YAML file mentions it. The
existing richer readiness/index transport is retained independently.

## Rollout prerequisites and current blockers

- Independent code/security review, then separately authorized publication.
- Tape policy and true existing gate mapping retained; missing/skipped current
  GKE jobs cannot be replaced with candidate docs jobs to make the pilot green.
- Registered review producer workflow ID/blob and reviewed immutable AW pin.
  An unregistered merge workflow does not block read-only review.
- Approved ephemeral Linux CLI provisioning with verified binary hash. The
  examples use ubuntu-latest, which this candidate does not provision with CLI;
  there is no mutable installer. They are not ready to deploy as supplied.
- No `/etc/claude-code` policy tree on the review runner. CLI flags cannot disable
  administrator-managed hooks; any organization-delivered managed settings also
  need independent inspection before credentialed execution. Unknown machine or
  account policy is not inferred from endpoint absence. This pilot supports
  personal Max OAuth only; server-managed Team/Enterprise delivery is outside
  that reviewed profile, as documented in OAUTH-ISOLATION.md.
- Read-only contents/Actions/PR/issues review token scopes (issues-read binds the
  current issue contract); shared action access must already be permitted.
- Merge remains pending a durable cross-run journal/mutex/reconciler and an
  approved provider base/protection contract. No write token is requested now.
- Real runner OAuth/structured-output/log transport and positive/negative pilot
  evidence after rollout approval; local mocks are not that evidence.

This candidate does not implement issue scheduling, hourlyreview, completion
leases, external model dispatch, new credentials, repository settings, rollout
to other consumers or automatic retries. Missing policy/IDs/CLI/secret/access
remains an explicit integration blocker.

## Local validation

Run isolated synthetic unit tests, not a real Claude session or GitHub operation:

```sh
python3 -m unittest discover -s plugins/faberline/skills/issue-ci/tests -p test_claude_review.py -v
python3 -m unittest discover -s plugins/faberline/skills/issue-ci/tests -p test_lifecycle.py
python3 -m unittest discover -s plugins/faberline/skills/issue-ci/tests -p test_qualification.py
```

CLI capability evidence: local `claude --help` lists --tools, --setting-sources,
--strict-mcp-config, --json-schema and --no-session-persistence. The official
[hooks reference](https://code.claude.com/docs/en/hooks) documents disableAllHooks
and its administrator-managed-settings exception. No authenticated invocation
has been made by this validation.

## Requested skill and issue findings (offline, not deployed)

Sources: `Sentinel_cbc847dacc8c819195bf46e7601ea141` approves the offline
skill-text integration proposed in `Sentinel_568408e1bd4c81918ebdcf11c36374b9`;
`Sentinel_145162f1d9848191b2735e8964334fa5` requires findings on the linked
GitHub issue. Neither authorizes a new live call, CI run or rollout.

The pure-text [issue-conformance skill](../issue-conformance/SKILL.md) is read
only from the immutable action distribution. Its byte digest is compiled into
the controller, carried in context and authenticated in the review receipt.
The text is explicitly included in both prompts. Native discovery remains off;
YAML permissions, shell substitutions and skill-relative dynamic files are not
interpreted. No tools/MCP/settings/credential authority is added. This packages
the exact-issue objective; it does not fix the planner protocol by itself or
prove that the model obeys it. The earlier protocol fix is retained separately.

`claude_findings.py` is a separate deterministic controller and action. The
GitHub-only candidate replaces the earlier local-journal implementation (kept
in commit aaa5963). Neither is live. The controller authenticates a completed
successful pinned producer's strict result receipt (pass/fail/block), fresh
exact HEAD/base, issue contract and all existing gates. Acceptance remains
pass-only. Protocol/CLI/missing-receipt failures never become code findings.
The only destination is the verified unique linked issue. Review status,
concrete missing/extra findings and requirement uncertainties are separate
fields; absent missing-work locations are not fabricated. Model prose is inert
JSON with mentions, HTML and fences escaped. No close/merge/label API exists.

The proposed consumer follow-up workflow receives completed Issue CI runs.
Its fixed run-name and concurrency group bind the source run ID/attempt;
cancel-in-progress is false. Default pending replacement only affects duplicate
wakes for that same review, not another review. The controller verifies the
publisher's workflow/path/blob/default branch/repository/job and bounded fully
paginated Actions history. Only the earliest matching publisher run, attempt 1,
may POST. Reruns and prior duplicate runs reconcile-only; absent exact bot-owned
body holds unknown. Run-name is an index, never sole authorization. History
incomplete, changed, missing self, ambiguous or over 1000 recent runs refuses.
No artifact input, PR checkout, OAuth, cloud credentials or new storage is used.

A single POST is followed by up to three read-only comment checks. Network
failure/timeout never causes a second POST in that invocation. Subsequent
publisher attempts/runs use bot identity + exact marker/body and run history.
Reconciliation returns historical=true with the original receipt/head/base/
producer identity. It does not mean a new review occurred or current acceptance.
GitHub has no comment POST idempotency key or atomic head/base transaction.
Practical deduplication is NOT exactly-once: crash/visibility/history-deletion
windows can still cause duplicates or no delivery. Unknown stays held, with
no automatic paid rerun or blind write retry. A cancelled first publisher may
also conservatively hold later attempts; recovery is exceptional, not per-PR
manual admission. Comments grant no merge/lifecycle authority.

Issue requirement hashing binds number/title/body. GitHub updated_at remains
structurally required but is an activity timestamp changed by comments; excluding
it prevents findings from invalidating their own receipt. Exact title/body edits
still invalidate it. Uncollected adopted comments/external specs are insufficient
evidence and must block; a timestamp does not prove their content.

Permission/deployment gap: the candidate action and Tape workflow are offline.
A separate OAuth-free findings job proposes issues:write plus contents/actions/
pull-requests:read using GITHUB_TOKEN. Existing model jobs remain issues:read.
The earlier handoff grant is not silently reused. This write capability and live
rollout need explicit approval and independent review. No new persistent storage
is required. The deployed 0b3bbff version still has its embedded prompt and no
findings publisher. No valid new-policy review receipt exists yet; prior protocol
failure is not backfilled as code findings. Hourly routines and merge hard-off
are unchanged. The planner protocol repair remains part of this unpublished
candidate and has not received live validation.
