# Review readiness v1 (first phase, not deployed)

One canonical implementation in aw #75; consumers use a reviewed immutable aw
commit and small default-branch configuration. This change does not install a
workflow, change any consumer/prompt/frequency, qualify a real pilot, enable done,
start a reviewer, or merge anything. Tape #40/#35 remains the first consumer; its
document-copy validator is owned there and is not implemented again here.

`ready` means deterministic prerequisites for an independent review. It does not
mean the implementation/assertions are semantically correct, the issue is fully
completed, a reviewer approved the code, or a merge is authorized. Labels never
prove CI. Failure-only mode is independent from this read-only result; actual
`qualification_blockers` and applicability gaps still refuse ready. Do not drop
blockers simply to enable a consumer.

## Reused boundaries

`qualification.py` selects the per-path union of required checks and explicit
category exemptions, freezes base, checks complete file metadata and protected
roots, and verifies workflow IDs/blob identities at head and base. The existing
`snapshot()` selects latest run/attempt, never falls back to an old green run,
and fetches jobs from that exact attempt. `evaluate_evidence()` is the shared
label-independent aggregate; its legacy adapter retains existing todo/pending/
done behavior. No lifecycle journal, coding epoch, AGY reclaim or legacy
issue-review done flow changes.

`readiness.py` adds ready/wait/fail and versioned producer-authenticated receipts.
The action adds `readiness` and `verify-readiness`; existing operations retain
their interface. `issue-review/scripts/receipt.py verify-readiness REPO PR HEAD
EVALUATOR_SHA RECEIPT_JSON` is a separate read-only verification entry; it neither
requires done nor records a completed review. Distribute that verifier from the
same reviewed pinned source; supplying a SHA argument does not attest arbitrary
local Python files.

## Consumer policy extension

The existing qualification v1 policy needs `review_readiness` with exactly:

- `version: 1`.
- `producer`: `path`, numeric workflow `id`, reviewed `definition_sha`, and exact
  display `job` name of a trusted default-branch `workflow_run` receipt producer.
  This workflow must not be a required check itself. Its trigger must explicitly
  include only test workflows, never its own name; the action also refuses its
  own/unconfigured wake path. Producer steps must not checkout/execute PR code.
- `sources`: map of stable names to `{repo, ref, path, blob_sha}`. Every `ref` is
  an immutable 40-hex commit. Declare transitive action/reusable workflow YAML
  and assertion-enforcement sources. A manifest entry alone is insufficient:
  the source bytes and Git blob hash are fetched and checked.
- `assertions`: map of check ID to `{names, enforcers}`. `names` is a nonempty,
  unique exact assertion roster; `enforcers` names reviewed source entries.
  Every selected check must have a contract, and each check needs a distinct
  workflow/job pair. Optional reviewed check metadata (e.g. command/profile)
  remains data and is included in `inputs.required_checks`; never executed here.

The collector recursively follows literal `uses:` references from both selected
head/base workflow definitions and the producer definition. Remote actions and
reusable workflows must use full commits; Docker actions use image digests.
Local consumer actions require reviewed source blobs and protected roots; local
uses inside external action/workflow sources are refused because checkout
context is not established by this first-phase interface. Local
assertion-enforcement sources are verified at the manifest ref AND current
head/base and must match protected roots. The producer's actual transitive
closure must include `faberline/aw/.github/actions/issue-ci@EVALUATOR_SHA`.

YAML dependency discovery uses the reviewed PyYAML 6.0.3 scanner and complete
node AST (`yaml.scan` / `yaml.compose` with the pure-Python `BaseLoader`). It does
not call `load`, `unsafe_load` or YAML object constructors. Keys are inspected
from parsed mapping nodes in block and flow contexts, not found by line regex.
Only literal scalar `uses` values with immutable refs are admitted. Token-level
checks reject explicit `?` keys anywhere, including adjacent flow delimiters,
and multiline quoted scalars before composition. AST checks reject complex,
escaped, dynamic, merged and duplicate keys. Aliases/anchors, explicit tags,
directives and multiple documents fail closed. Block script text stays scalar
data and cannot masquerade as an action reference. Limits are 256 KiB source,
20,000 tokens/nodes and 64 traversed levels; excessive parser nesting refuses.
Rejected syntax fixtures do not establish whether GitHub Actions accepts those
special YAML forms; runner acceptance has not been exercised.
Proof covers declared action/workflow references and enforcement sources. Source
review still owns complete enforcement imports, shell downloads/toolchain
behavior, action runtime implementation and honest assertion meaning; the
collector is not a hermetic-build or arbitrary-language dependency analyzer.
A pinned top-level workflow alone is never sufficient.

## Parser dependency and execution supply

New readiness-only dependency: **PyYAML==6.0.3**, from the official
[PyPI release](https://pypi.org/project/PyYAML/6.0.3/) and its
[maintainer documentation](https://pyyaml.org/wiki/PyYAMLDocumentation).
`scripts/readiness-requirements.txt` locks the SHA256 of all 72 published wheels
from `https://pypi.org/pypi/PyYAML/6.0.3/json`; no sdist or transitive dependency
is allowed. Lockfile SHA256: `eef6794465b73b5dc4ef909500bf5bcc56761b93bb1521a05a97746d1f7f298d`.
These are release-file integrity hashes, not package signatures or attestations.

For `readiness` / `verify-readiness` only, the shared composite action creates a
new venv below `RUNNER_TEMP` using the existing runner Python and bundled pip.
It installs with `--isolated --no-deps --only-binary=:all: --require-hashes
--index-url https://pypi.org/simple -r readiness-requirements.txt`, then invokes
the pinned evaluator with that interpreter in Python isolated mode (`-I`),
ignoring ambient PYTHONPATH/user-site overrides. It does not upgrade runner pip,
change global packages or request credentials/permissions. The immutable aw
commit binds the action, evaluator and lockfile together; the consumer must
review this dependency supply as part of its pinned transitive implementation.
`validate`, `qualify` and `handoff` skip the bootstrap and retain their interpreter.
The main aw CLI package dependencies and lock are unchanged.

A standalone verifier must run from the same reviewed aw revision in an isolated
environment prepared with that hash-lock; missing or different PyYAML versions
refuse with `parser_dependency`, never silently use an ambient alternative.
The affected tests exercise the parser locally, including a fresh `/tmp` venv
installed through the lock. No GitHub workflow was executed to validate runner
installation or its special YAML acceptance; a real consumer pilot is still
required before activation.

## Nonzero assertions are evidence, not a green check name

After actually running and verifying the declared assertions, the immutable/
protected gate enforcer emits exactly one log record in the selected job:

```text
AW_ASSERTIONS_V1 {"version":1,"repo":"owner/repo","pr":123,"head":"<40 hex>","base":"<40 hex>","run_id":456,"run_attempt":1,"job_id":789,"check":"docs-copy","assertions":{"section-copy":"passed","links":"passed"}}
```

This is a wire example, not executable policy or real pilot evidence. The gate
must fail if any assertion was missing/zero/failed/skipped. The record head
must be measured from the executed checkout (not merely copied from an event
variable), compared to the expected PR head; base must identify the frozen base
actually used for selection/content comparison. Runtime/image identity assertions
belong in the reviewed e2e contract when applicable. Do not echo a roster
as passed without running it. For a Cargo filter, the enforcer must prove the
expected test roster and counts, not just an exit code. Enforcement and negative
fixtures must be independently reviewed in the consumer pilot. This change
provides no six-test execution result for Tape and no generic shell-test runner.

The collector downloads only the authenticated current-attempt job's GitHub
logs, accepts GitHub timestamp prefixes, requires one bound record with the
exact configured roster, and checks every value is passed. Zero/missing/duplicate
or mismatched records refuse ready. Jobs must also be completed success. There
is no artifact input, log URL override, PR config, PR code execution, or new
credential. A PR artifact cannot supply assertion authority. Correct trusted
enforcement must prevent PR test output spoofing (e.g. extra markers make the
record ambiguous; a failed enforcer must fail its job).

## Receipt schema and verification

Canonical JSON schema: `templates/review-readiness.schema.json`. The envelope is
`{payload, sha256}`; the digest is SHA256 of sorted compact ASCII JSON payload.
A digest provides integrity, NOT authentication. Never authorize review by just
recomputing it.

All JSON decoding for this interface uses one shared `object_pairs_hook` that
rejects duplicate object keys at any depth, including equal duplicates and keys
made equal by JSON escapes. It applies to GitHub metadata, policy/config, wake
events, assertion/producer log records and receipt files. Ambiguous JSON must
not collapse a failed assertion into a later passed value before validation.

Payload binds version, status, producer workflow/path/blob/head/run/attempt/job,
repo/PR/exact head/base, complete normalized changed-file inventory and hash,
PR description hash, policy source commit/path/hash, evaluator SHA, selected
check definitions/rosters, exemptions, transitive source pins, current required
run/job conclusions/links, observation hash and assertion-record hashes/counts.
Patch bodies are omitted from the inventory; immutable head/base and file blob
identities bind content without copying the whole diff into receipts.

Generation freezes default-branch policy via GitHub API, collects twice, then
rechecks latest snapshots/producer/policy and final PR identity. Collection
changes return `inputs_changed`, not a false empty queue. Runtime/API/schema
errors refuse publication; callers must surface unknown/integration blockers.
There is no cross-resource GitHub CAS: changes after the last API read remain a
race, so verify again immediately before review and before publishing findings.

The producer prints `AW_REVIEW_READINESS_V1 <envelope>` in its own job log.
Its job and whole run must finish success before consumption. A successful
producer can report valid wait/fail data; `readiness` therefore exits 0 after
publication. Other steps failing that producer invalidate the receipt.

Verification accepts caller-selected repo/PR/head/evaluator anchors, validates
the envelope, authenticates the configured default-branch producer's workflow/
branch/event/blob/latest attempt, recomputes current evidence using current
trusted policy, and finds exactly this envelope in that authenticated successful
producer job's log. A producer may publish different PRs, but duplicate receipts
for one PR in one job are ambiguous and refused. A fabricated receipt with a
recomputed digest cannot replace the producer's record. New head/base/policy
source/content/evaluator/required set/gate attempt or producer attempt makes the
old receipt stale, including rerun in progress. Skip/cancel/neutral/unknown are
not success; missing runs/jobs or active known states wait; genuine failures,
unknown coverage/trust/assertions and real qualification blockers fail closed.

`verify-readiness` returns 0/3/2 for authenticated ready/wait/fail. Invalid or
stale receipts/API failures refuse with an error (exit 2); do not interpret them
as no_ready. Only readiness producer collection accesses assertion logs;
verification currently recomputes the same bounded evidence for one named PR.
This phase is not a constant-time receipt-only cache and does not scan all PRs.

## Integration and deliberate follow-ups

No index transport/publisher is implemented in this first phase. No consumer
has an approved index locator yet. Missing index means consumer_not_configured,
not an empty eligible queue; do not enable the draft thin prompt or fall back to
model scanning all open PRs. A later index can list these authenticated receipt
locators/digests; the index must prove completeness and is discovery, not review
authority. Assertion source/log transport must be established by real pilot CI
before any ready receipt is claimed in production.

No atomic reviewer session lease, completion ledger v2, Sol dispatch, webhook or
pre-model native hook is implemented. Existing issue-review done receipts remain
per-head legacy behavior; their guards are not PR/head review-session leases.
A later dispatcher must separate receipt fingerprint from semantic review key,
so a same-code successful rerun does not repeat a completed semantic review;
head/base/policy/issue-slice changes require explicit invalidation. It must
journal claim/session identity and recover uncertain creation before retrying.
Issue/slice specification revisions are not inferred from issue labels; the
review consumer still must bind/check that semantic context before publication.

Desktop automation still wakes its model on a schedule. This phase reduces
repeated deterministic reasoning, not model startup tokens. No new scheduler,
frequency change, paid capacity or private app API is introduced.

## Local validation

Only isolated unit/static fixtures are used in aw. The test suite covers ready
under failure-only; real blockers; nonzero/zero/missing/spoofed assertions;
unknown/protected/incomplete applicability; mutable or missing transitive pins;
foreign/self producer; fake/tampered/unpublished receipts; cancelled/skipped and
rerun states; head/base/policy/source/description changes; and collection races.
Existing issue-ci lifecycle/reclaim and legacy review tests remain required.
No cloud check or consumer activation is part of this PR.
