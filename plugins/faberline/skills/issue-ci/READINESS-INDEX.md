# Authenticated review index v1 (not deployed)

This bounded follow-up to aw #75 reuses the phase-one evaluator merged at
`1d89514b674a055753c82468614e6af97702a121`. It adds producer-wide discovery and
metadata-only index freshness verification, not another qualification engine.
No consumer is changed, no done activation or semantic review completion is
claimed, and no atomic review-session lease or pre-model trigger is installed.
Tape owns its existing docs/6-test enforcers; no product validator is copied here.

## Producer and transport

The same pinned shared action gains `operation: index`. A consumer default-
branch `workflow_run` workflow invokes it after an allowlisted PR test workflow
completes. It must exclude its own workflow, never checkout/execute PR code,
and use the existing phase-one read permissions: contents, actions and pull
requests. No checks-write/issues-write, artifact upload action, secret, credential,
repository setting, manual dispatch, scheduler or paid capacity is needed.

The producer uses the same default-branch policy and `review_readiness.producer`
identity as phase one. It freezes that policy, verifies the producer's source
and transitive immutable aw reference, inventories every open same-repository
PR, calls the existing `readiness.produce` for each, then rechecks policy,
complete inventory and gate metadata before publishing. Unknown API data,
foreign PRs (unsupported by phase one), capped pages and collection races refuse
publication instead of silently omitting candidates. Every event reconciles the
whole bounded queue, so GitHub concurrency pending replacement does not select
only the old event head. No lifecycle labels are read or changed for eligibility.

Transport is the authenticated producer job log, not an artifact. After all
collection succeeds it prints one ordinary phase-one receipt per PR, then one
`AW_REVIEW_INDEX_V1 <canonical envelope>` record. The entire producer job AND run
must complete success before consumption. A run may successfully publish known
wait/fail results; a green producer is not review readiness. Self/unconfigured,
foreign, non-PR or nonterminal wakes are refused by the index action guard.
The workflow template also excludes itself; do not use a wildcard event trigger.

Limits: 50 PRs, at most two 50-item inventory pages, 256 KiB index, 4 MiB combined
index/receipt publication, 8 MiB downloaded producer log, and 64 KiB consumer
summary. Reaching a limit is an explicit integration blocker, never truncated
coverage or no_ready. Records and arrays are deterministically ordered. The
producer run/attempt plus full inventory and per-gate observation hashes identify
the observed revision; they do not assert an external webhook stream watermark.

## Canonical schema and discovery

`templates/readiness-index.schema.json` describes the published index envelope
and verified thin summary. It references only the pinned sibling phase-one
receipt schema. Schema/digest validation alone is NOT authentication.

An index binds repo, evaluator SHA, default-branch policy path/source/hash,
producer workflow/path/blob/head/run/attempt/job, producer pin proof, complete
sorted inventory and its hash, plus every receipt and computed applicability
scope. Inventory includes PR number, current head/base/ref/repository, draft
state and title/body digest. Unknown or missing metadata never means empty.
PR body, labels, artifact names or arbitrary URLs cannot choose the index source.

The authoritative locator is the caller-selected repository and trusted default-
branch policy's configured producer workflow/job. `verify-index` discovers the
latest workflow run by run number/latest attempt without filtering to success.
It will NOT fall back to an older green run if the latest is pending, failed,
cancelled, foreign or malformed. It authenticates source/pins even for zero PRs,
then requires exactly one index and the exact complete receipt stream in that
successful job's GitHub log. Hashes are integrity fingerprints, not signatures.

It compares live full inventory, current policy/evaluator/producer and each PR's
files, applicability and exact latest gate/job metadata using the shared
qualification/snapshot functions. It does not reread assertion/test logs for the
whole queue. A second metadata observation and final inventory/policy/producer
check catch movement during collection. Head/base/body/ref/draft, files, policy
source/content, enforcer/workflow definitions, required set or latest gate/
producer attempt changes invalidate the old index, including rerun in progress.
No GitHub cross-resource CAS exists; named receipt verification is still required
before review and again before publishing findings.

Fixed trusted checkout + phase-one hash-locked PyYAML environment:

```text
<venv>/bin/python -I <AW>/plugins/faberline/skills/issue-review/scripts/receipt.py verify-index OWNER/REPO EVALUATOR_SHA
```

The exact selected receipt has a second fixed entry point:

```text
<venv>/bin/python -I <AW>/plugins/faberline/skills/issue-review/scripts/receipt.py index-receipt OWNER/REPO PR HEAD EVALUATOR_SHA RECEIPT_SHA256
```

This re-discovers the authenticated index, requires the caller-selected exact
candidate/digest from preflight, invokes the original full named receipt
verifier and prints that envelope (0/3/2 for ready/wait/fail). A caller may
redirect stdout into its own task-local JSON data file; never execute it. It
avoids a consumer-authored artifact/log downloader. Use this before semantic
review and again before findings; a changed digest is stale, not auto-upgraded.

No index URL, arbitrary artifact or file containing executable code is accepted.
The action also exposes `operation: verify-index`; both print only the bounded
canonical thin summary. Exit 0 = verified eligible candidates; 3 = verified
complete no_ready; 2 = known integration blockers or any invalid/stale/missing/
API error. Summary `ready` contains PR/head/base, receipt digest, mechanical key
and up to eight display links (with total link count), not the entire queue's
logs/diffs. Missing index is never no_ready. A normal latest producer still in
progress has no authoritative consumable snapshot yet; fail closed without
falling back or asking the model to rescan all PRs.

This preflight still performs bounded non-LLM API freshness checks. It is not a
constant-time cache, zero-token poller, or supported native webhook dispatcher.
Existing desktop schedules and their model startup costs are unchanged.

## Review completion / deduplication boundary

Each entry has `mechanical_review_key`, based on repo/PR/head/base, policy content,
evaluator and PR description. It excludes producer and gate run IDs/attempts,
so the same code successfully rerun does not create new semantic review work.
`review_work_key(mechanical_key, issue_contract_sha256)` joins the consumer's
separately verified full issue/spec/slice revision. The receipt's PR description
hash is not a substitute for that semantic revision.

The consumer must consult its authenticated actual-review completion store and
only dedupe completed reviews matching that full key. No completed_keys file,
PR-supplied already-reviewed flag, done label or screening result is accepted as
completion authority by this index. Completion storage v2, publication/reviewer
coordination and atomic session lease remain separate work. Missing completion
integration is a consumer blocker, not permission to repeat or skip review.

## Test-job assertion evidence without new Actions permissions

V1 remains supported. V2 adds `AW_ASSERTIONS_V2` with version/repo/PR/measured
head/frozen base/run ID/attempt/check and exact nonempty assertion statuses, but
no self-reported numeric job ID. The producer already fetches the selected job's
log through the authenticated exact-attempt Actions jobs API, so that transport
binds the actual numeric job ID and workflow/job identity. The result receipt
still records that numeric job ID. A V1 marker must carry V1; V2 must carry V2;
mixed, duplicate, missing, zero, wrong-roster or wrong-identity records fail.
Do not request actions-read in the ordinary test job just to discover its ID.

The pinned shared action's `operation: assertion-library` only returns
`outputs.assertion-library`, the platform-downloaded pinned
`scripts/assertion_record.py` module path. It runs no assertion, GitHub API,
package installer or qualification step. This uses the same existing sanctioned
cross-repository action distribution; if access is unavailable, report it rather
than changing credentials, sharing settings or fetching denied sources.

A reviewed consumer enforcer imports that fixed module, calls
`execution_context(checkout)` BEFORE its real assertions, uses the returned
frozen base/head in its actual comparisons, and passes actual named boolean
observations to `publish(check, expected_names, observed, checkout, started)`.
The helper remeasures checkout HEAD and base commit after the assertions and
refuses drift, missing/extra/zero/false/non-boolean results. It prints one V2
record only after all checks pass. It never runs cargo, copies validators,
invents observations, or accepts a JSON file that claims tests passed.
The trusted consumer enforcer and negative fixtures still must prove that those
observations were actually executed. Calling this emitter is not that proof.

## Tape engineering handoff draft (do not dispatch from this PR)

Title: `ci: wire shared readiness index and real docs assertion evidence`

Prerequisites: this shared supplement independently reviewed/merged to an
immutable aw SHA, normal registered Tape workflow identities, and existing
Tape #40/#35 docs gate deployed. Keep #75 open. Implement only Tape thin policy/
enforcer/producer wiring through AGY, separate from product #35 and other repos.

Acceptance:

1. Reuse `validate-docs-copy.py` / `run-backup-docs-tests.py` and the two existing
   affected-docs jobs. Add the pinned assertion-library step; keep contents-read
   test permissions. Before/after measured HEAD must equal the exact current PR
   head; frozen base must be the actual source used for comparison. Never use
   a merge checkout or copy an expected hash into a field without measuring.
2. Publish V2 only after real docs section/link/scope assertions or the actual
   `cargo test --locked -p tape --test it -- backup_destination_docs::` command.
   The latter must enforce the exact six names, six passed, zero failed/ignored,
   and subprocess exit status; zero/partial/spoofed stdout cannot become success.
   Preserve existing negative fixtures and add missing/mixed/duplicate marker,
   wrong head/base/attempt and context-drift cases. No local cargo/e2e/perf run.
3. Put reviewed qualification/review_readiness in the actual required-gates
   policy, not merely candidate JSON. Use real registered numeric workflow IDs,
   definition blobs, exact job names, actual assertion roster and immutable
   sources/protected enforcers. Candidate id=1 is not evidence. Do not blanket-
   clear blockers: independently review any migration of irrelevant global
   missing-runtime coverage into explicit unmapped-path failure; genuine docs
   coverage blockers still prevent ready, and runtime paths remain fail closed.
   Retain failure-only and preserve the existing failure-to-todo controller.
4. Add a distinct default-branch read-only producer using `operation: index`,
   only explicit test workflow completion wakes, never itself. Use contents/
   actions/pull-requests read already required by phase one; no extra permissions.
   Do not misidentify the current Issue CI handoff as this producer. Register
   workflow ID/blob/job in reviewed policy after actual availability; no
   placeholder IDs or PR-controlled source execution.
5. After independent infrastructure review/merge and separately coordinated
   product head update, observe real nonzero test logs, successful producer
   terminal state and positive/negative fixed-version verifier results. Show
   gate/producer rerun pending, new head/base/policy and partial/missing log/index
   invalidate old readiness. No manual dispatch or invented pass artifacts.
6. Complete trusted index/transport plus semantic completion-store/coordination
   acceptance before any thin-prompt activation. Use mechanical+issue-context
   key for completed-review dedupe. Do not claim a lease or pre-model hook,
   change frequency, turn on done, merge product work or roll out other repos.

No consumer engineering issue/comment, workflow deployment or schedule is
created by this document. The parent coordinates authorization and AGY routing.
