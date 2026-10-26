# Offline exact-issue-fit and supporting-source contract candidate

This change is not deployed. The successful Tape #45 production invocation used
the previous changed-files-only protocol. Its pass receipt proves that invocation
worked; it does not prove the unprovided reconciliation implementation was
reviewed. Do not reinterpret that receipt as evidence for this new protocol.

## Narrow admission contract

The trusted default-branch `claude_final_review` policy must now include
`evidence_contracts`. No legacy/default/PR-supplied fallback exists. An entry is
independently reviewed before admission and has exactly this shape:

```json
{
  "acceptance_sha256": "SHA256_OF_CANONICAL_NUMBER_TITLE_BODY_OBJECT",
  "diff_sha256": "SHA256_OF_CANONICAL_FULL_BEFORE_AFTER_CHANGE_RECORDS",
  "changed_paths": ["crates/tape-operator/src/lib.rs"],
  "claims": [
    {
      "id": "render_purity",
      "requirement": "Check the rustdoc claim that desired child rendering performs no cluster I/O.",
      "supporting_paths": [
        "crates/tape-operator/src/render.rs",
        "crates/tape-operator/src/render/backup.rs",
        "crates/tape-operator/src/render/observability.rs",
        "crates/tape-operator/src/render/placement.rs",
        "crates/tape-operator/src/render/statefulset.rs"
      ]
    },
    {
      "id": "controller_connection",
      "requirement": "Check that the reconciliation module implements Tape's ManagedService contract and delegates to the shared Kubernetes controller.",
      "supporting_paths": ["crates/tape-operator/src/reconcile.rs"]
    }
  ]
}
```

This example is illustrative, not an approved Tape contract. Replace the hash
with SHA256 of canonical JSON `{number, title, body}` for the exact issue.
Canonical JSON uses sorted keys, ASCII escapes, and comma/colon separators with
no whitespace. Cosmetic issue metadata updates do not require new admission;
title/body changes do. The receipt still binds the existing full issue snapshot,
including its update timestamp, so drift during a review remains a refusal.
The diff hash is SHA256 of the canonical ordered records
`[{path, previous, before, after, before_source, after_source}]`, sorted by path,
with full decoded before/after UTF-8 text and JSON null for an absent side.
Each source metadata object binds `{blob, mode, size}` from the exact commit's
Git tree. Executable-bit changes cannot reuse a content-only admission and are
shown explicitly in the packet. A new assertion or extra behavior in the same
allowed file cannot reuse an old admission: changed bytes require independent
contract review even when issue text and paths stay unchanged.

Each `claims` entry is an explicit issue requirement, not a general quality wish.
The trusted reviewer inventories all explicit requirements and any explicitly
adopted specification; the machine does not infer that inventory. Only sources
necessary to verify those requirements belong in `supporting_paths`. An empty
list is allowed explicitly when complete changed-file bytes are sufficient; it
is not an implicit fallback for an unregistered requirement.

The changed-path set must match exactly. Renames from outside that set refuse.
Unknown issue acceptance hashes refuse before the model. Contract selection,
claim requirements and source paths come only from the trusted policy, never
from the PR, an artifact, a model-generated dependency list or an arbitrary
`verified` flag. Existing protected-path and test gates still apply first.

## What machines establish

For every declared source, the collector reads the immutable base and head Git
trees, rejects truncated/ambiguous inventories, and requires a regular file.
It fetches the full UTF-8 bytes and checks Git blob identity and tree size.
Receipts bind path, commit, Git blob, mode, byte count, content SHA256 and source ID.
Both versions and the per-claim source IDs enter the review context. Full bytes
enter the model packet; no silent excerpt or truncation is accepted.

Bounds: at most 32 admitted issue contracts, 16 claims per contract, 16 distinct
supporting paths, 256 KiB per source version, 10,000 tree entries and the existing
1 MiB total packet limit. Exceeding a bound blocks rather than dropping context.
The packet is validated again before credential lookup/CLI execution. Collection
after the model and fresh receipt verification bind the same sources again.

The strict model result must echo the contract digest and include exactly one
`requirement_results` assessment per explicit requirement, referencing every
prescribed source ID and mapping met work to real changed-file locations.
Pure-text diff ranges are computed from full before/after bytes; fabricated,
unchanged or supporting-only locations cannot satisfy a met mapping. File-only
changes such as empty-file metadata and executable-bit changes have an explicit
zero-line `file` marker. Every changed range must be accounted for by requirement
or extra-work locations; an unclassified changed line refuses.
The exact matcher disables popularity-based autojunk after stripping equal
prefix/suffix lines. If the remaining base/head line-count product exceeds
1,000,000 it blocks rather than approximating location evidence.
Missing claim/source, stale digest, `missing`, `ambiguous` or
`insufficient_evidence` cannot accompany `pass`. Extra changes carry specific
changed locations and reasons. The validator derives fit and decision:
exact/pass; missing, extra or both/fail; uncertainty/block. Inconsistent
combinations refuse. A valid block remains a non-passing review receipt.
A statement such as "I reviewed everything" cannot replace delivered bytes,
machine identity checks or structured requirement mappings.

All repository text, including supporting source, stays untrusted model data.
No import, build, shell execution or repository checkout is introduced into the
credentialed reviewer. The existing tools/MCP/hooks/environment isolation and
hard-disabled merge writer remain unchanged.
Deterministic coverage/unit/e2e gates remain owned by scripts. Their evidence is
used only when needed to map an explicit requirement; the agent cannot waive or
replace them and this candidate adds no coverage threshold or general quality
certification field. The separate five-milestone >=99% goal is preserved.

## What this does not establish

The contract is a human-reviewed declaration of relevant evidence, not an
automatically proven transitive dependency closure. The machine proves that the
declared evidence arrived intact and that the result meets the strict contract;
it cannot prove semantic correctness, that a model actually understood every
line, or that the human omitted no necessary source. Source-ID echoes and
`met` statuses alone are not proof of semantic completeness or the absence of
extra behavior. Correct extraction of all issue requirements and selection of
necessary sources remain an independent admission responsibility. If an external
dependency or additional evidence is needed, the model must block and the
contract must receive another independent review. Cross-repository source
fetching and model-driven retrieval are outside this candidate.

## Deployment decision and remaining work

The tradeoff is deliberately narrow: each issue contract and exact admitted diff
needs trusted default-branch admission. Every patch revision needs readmission;
there is no path-only reuse. This is stricter
than a broad automatic dependency guess and can block ordinary PRs until their
contracts are reviewed. Agree on that pilot maintenance cost before deployment;
do not silently replace it with a PR-controlled declaration or wildcard bypass.

Deployment needs independent review, a newly published immutable AW action,
consumer policy admission, refreshed producer blob binding/action pin and a
separately authorized bounded live validation plan. The current consumer policy
has no `evidence_contracts`; pointing it at this candidate would refuse. Old
receipts do not satisfy the new result/context schema. No such publication or
consumer change is included here. No additional GKE/Claude run was launched.

Automated merge remains hard-disabled. Durable cross-run reconciliation, mutex
and provider base/protection binding are separate unresolved requirements.
