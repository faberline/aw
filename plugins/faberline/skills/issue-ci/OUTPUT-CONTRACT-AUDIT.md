# Offline output-contract audit, 2026-10-26

This candidate is not deployed. It audits the planner AND final wire fields as
one class, preserving the phase fix, issue-only objective, skill byte binding,
existing gates, publisher, credentials and runner isolation. No model/effort pin
is introduced. No inference, push, live logging or issue comment occurred.

The deployed e06e407 attempt reached final output, then refused review_incomplete:
[GKE40358167392](https://github.com/faberline/tape/actions/runs/40358167392)
passed8/8; [review40361188564/job121707120420](https://github.com/faberline/tape/actions/runs/40361188564/job/121707120420)
failed; [publisher40361425122/job121707603858](https://github.com/faberline/tape/actions/runs/40361425122/job/121707603858)
skipped. Two CLI processes completed; zero receipt and zero issue44 comments.
The actual rejected reviewed_files AND raw planner paths were not retained and
remain UNKNOWN. A synthetic schema-valid supporting filename reproduces the
same runtime code; it is a possible example, not recovered failed-run bytes.

## Planner field inventory

S means representable in the emitted standard schema. R means retained runtime
relationship/integrity checks. Semantic necessity/truth is agent judgment, not
proved by a string matching the schema.

| JSON field | S: emitted schema | R / semantic or contextual residual |
|---|---|---|
| `version` | integer const1 | Mathematical value1, bool refused; JSON1.0 is an integer |
| `requirements` | array1..16, whole-item unique | Unique requirement IDs even when object bodies differ; completeness is semantic |
| `requirements[].id` | string enum r1..r16 | No repeated ID across entries |
| `requirements[].requirement` | nonblank explicit Unicode class,1..2000 chars | Faithful requirement extraction cannot be mechanically proved |
| `requirements[].issue_quote` | same text bounds/class | Exact substring of original title/body; literal quote is not proof of correct interpretation |
| `requirements[].supporting_paths` | unique relative-path array0..16; path1..1024 and shared grammar | Global union<=16 across requirements; exact base/head tree entries, regular blobs/modes/sizes/digests; source necessity remains semantic |
| `adopted_specs` | array0..16, whole-item unique | Unique spec path across differing objects |
| `adopted_specs[].path` | same relative-path grammar/length | Must be requested in supporting_paths and named in its quote |
| `adopted_specs[].issue_quote` | text1..2000, explicit nonblank | Literal issue text containing path; whether issue actually adopts it is semantic |
| `uncertainties` | array0..16, same bounded nonblank text | Only unresolved issue ambiguity, not missing initial source; nonempty still blocks before fetch. Classification is semantic |

All planner objects require exactly their declared keys; extra/missing keys
refuse. The path grammar rejects absolute, parent/dot segments, empty segments,
backslashes and ASCII controls. API fetch never becomes local-file access.
Phase rules remain: request absent supporting files first; final evidence
insufficiency is decided after collection. No uncertainty is suppressed.

## Final field inventory

| JSON field | S: emitted schema | R / semantic or contextual residual |
|---|---|---|
| `version` | typed integer const1 | Exact mathematical value; canonical numeric normalization below |
| `repo` | typed string const trusted repo | Exact case/value, no destination normalization |
| `pr` | typed integer const trusted PR | Exact mathematical value; never a different PR |
| `head` | typed string const exact HEAD | Unchanged exact commit/provenance checks |
| `base` | typed string const exact base | Same; this is not server-atomic merge authority |
| `decision` | typed enum pass/fail/block | Must be derived consistently from statuses/extras/uncertainties |
| `fit` | typed enum exact/missing/extra/missing_and_extra/uncertain | Same relationship; unsupported pass refuses |
| `summary` | nonblank explicit Unicode class,1..4000 chars | Truthfulness is semantic |
| `reviewed_files` | exact changed-inventory path enum; minItems=maxItems=inventory count; uniqueItems | Runtime independently checks shape/count/membership/duplicates. Supporting filenames do not belong here |
| `requirement_plan_sha256` | typed string const bound plan digest | Same digest, original issue and source snapshots freshly reverified |
| `requirement_results` | exact plan cardinality; whole-item unique | Exactly one entry per ID, including differently-worded duplicate objects |
| `requirement_results[].id` | typed string enum current claim IDs | Unique ID and complete ID set via cardinality+runtime uniqueness |
| `requirement_results[].status` | typed enum met/missing/ambiguous/insufficient_evidence | Semantic evidence status; determines fit/decision |
| `requirement_results[].detail` | bounded nonblank text | Concrete interpretation/evidence is semantic |
| `requirement_results[].source_ids` | context ID union enum, uniqueness; minimum/maximum per-claim set sizes | Exact ID-to-requirement association AND exact per-ID set/count stay runtime. Empty context requires [] (maxItems0), no dangling hex fallback |
| `requirement_results[].locations` | array<=160, or0 if no ranges | met requires nonempty; no fabricated missing-work location; complete diff accounting remains runtime |
| `requirement_results[].locations[].path` | enum actual changed-range paths | Must match path AND side AND hunk together |
| `requirement_results[].locations[].side` | enum sides actually present | Same relationship; absent file-mode side not offered |
| `requirement_results[].locations[].start` | integer within overall changed-range numeric bounds | start<=end and within one exact matching hunk |
| `requirement_results[].locations[].end` | same | Same; global bounds alone do not prove hunk membership |
| `requirement_results[].locations[].reason` | bounded nonblank text | Whether mapping is persuasive is semantic |
| `extra_changes` | array<=100, or0 if no ranges | Concrete extra-scope findings and combined complete diff accounting |
| `extra_changes[].path` | actual changed-range enum | Exact path/side/hunk membership |
| `extra_changes[].side` | actual-side enum | Same |
| `extra_changes[].start` | integer within overall bounds | Same ordered range/hunk constraint |
| `extra_changes[].end` | same | Same |
| `extra_changes[].reason` | bounded nonblank text | Extra-scope reasoning is semantic, not new generic quality requirements |
| `contract_uncertainties` | array0..16, bounded nonblank strings | Nonempty requires block; whether an issue is materially ambiguous is semantic |

Every object has required keys and additionalProperties:false. No new wire
fields, conditionals, union branches, refs, tools or per-PR approval are added.
Status-dependent counts, per-key uniqueness and per-ID source association are
expressible using a larger conditional/contains schema, but are deliberately
NOT claimed as expressed by this conservative keyword profile. They stay named,
adversarially tested runtime constraints; no CLI support is guessed for new
conditionals and no large branch grammar is added just to claim certification.

## Parsing and provenance details

JSON Schema integer includes integral numeric encodings such as1.0. Final
numeric version/PR/line fields now accept only finite mathematical integers,
reject bool/fraction/string/NaN/infinity, still require the exact identity/value
or exact source range, then canonicalize ONLY these numeric fields to int.
No path, casing, enum, digest, requirement, source ID or provenance value is
rewritten. This aligns representation, not acceptance of a different identity.
Planner version already accepted1.0. Receipt serialization receives canonical
numeric fields. Independent review must specifically check this parsing choice.

Final text now shares the planner's explicit whitespace class in BOTH schema and
runtime, instead of schema minLength vs Python strip mismatch. It narrows final
prose whitespace acceptance, including FEFF. It does not certify meaningful prose.

Rename base-side spans now cite file.previous, matching bytes actually read;
head spans cite current path. Reviewed_files continues to list current inventory
paths only. Old/new path distinction is not normalized away. Protection checks
still cover both paths, and exact blob/mode/commit provenance remains unchanged.

The full schema is passed in one argv. A120000-byte budget with3072-byte reserved
planning metadata is checked during preflight/initial collection, before paid
planning. The reservation covers32 source IDs,16 claim IDs and the plan digest;
regression checks the bound. Final emission checks the same budget. Large enums
can legitimately hold rather than hit the Linux per-argument limit after a paid
call. This is an explicit protocol-size limitation, not an issue requirement.

## Complete residual inventory

Runtime remains authoritative even if CLI ignores a keyword. Residuals include:

- Planner ID/spec-path uniqueness by key; global path union; literal quotes,
  source-request/spec reference relations; whether adoption is explicit.
- Final ID uniqueness by key, per-ID source association/count, status-to-location
  relationship, path/side/hunk coupling, ordered start/end, and complete changed
  range coverage across all claims and extras.
- Consistent derived fit/decision from status/extras/uncertainties. Agent judgment
  of missing/extra work, requirement extraction/completeness, source necessity,
  material ambiguity and whether selected evidence is semantically sufficient.
- Aggregate canonical result<=64KiB and CLI outer output<=128KiB, not derivable
  from independent per-string/array bounds. Schema argv budget is separate.
- Strict duplicate JSON object-key refusal, CLI exit/outer result identity,
  structured_output presence, secret-in-stdout refusal, byte-pinned CLI/skill,
  endpoint policy, original issue/diff/tree/blob/mode/size/source IDs, fresh gates,
  exact producer/job/run/attempt/policy identity, post-inference freshness and
  publisher comment/run provenance. Valid schema does not authenticate any of it.
- Provider/CLI may return missing or nonconforming output; exact enum casing is
  retained for paths/IDs. API documentation even notes casing caveats; no unsafe
  case-folding is introduced. Actual model/effort and internal request count are
  not pinned/observable by this candidate.

These are explicit limitations, not suppressed errors or a guarantee that a
successful model response is correct. Existing CI checks remain scripts, and
agent scope remains only exact issue conformance.

## Documented CLI compatibility versus separate API restrictions

The official [Claude Code noninteractive documentation](https://code.claude.com/docs/en/headless#get-structured-output)
says --json-schema accepts a JSON Schema and returns structured_output; invalid
schema is rejected, format is annotation-only, and structured_output can be
absent. It does NOT provide a per-keyword enforcement/version matrix for2.1.310.
We emit no format keyword and do not claim unsupported keywords were tested by
executing the real CLI. Prior runs accepted these schemas, which proves neither
all branches nor all keyword enforcement. No undocumented validate-only flag is
invented and no paid inference was used for this audit.

The separate [direct API structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#json-schema-limitations)
limits array/string/numeric constraints and regex assertions; it also describes
schema transformations and casing limitations. That endpoint's subset is NOT
assumed to describe the CLI's internal implementation. Used keywords:
type/properties/required/additionalProperties/items/enum/const/description;
minItems/maxItems/uniqueItems; minLength/maxLength/pattern; minimum/maximum.
Some of those are outside the direct API subset, including the existing planner
path lookahead. CLI handling/enforcement remains unproven. We do not silently
strip them, switch to API billing, add a guessed compatibility transform, or
waive runtime constraints. Offline Draft7+2020-12 validators and explicit Unicode
fixtures prove standard-schema behavior only, not native decoder compliance.

## Fixed diagnostics and privacy-review telemetry candidate

Final diagnostics now separate inventory shape/count/membership/duplicates,
identity field, claim shape/ID membership/duplicate/text/status, source shape/
count/duplicate/membership/association, and location shape/reason/integer fields.
Outer diagnostics separate CLI nonzero, byte limit, credential stdout, JSON,
outer status/shape and missing structured output. They never interpolate rejected
values, indices, raw stderr, paths, exception bodies or credentials.

structural_telemetry is an UNWIRED pure helper for independent privacy review.
It returns capped aggregate counts (files<=81,claims/statuses<=17), expected
counts and known decision/status enums only. It emits no actual names/IDs,
per-member bitmap, hashes, text, object keys or source/credential values. It
performs no I/O and is not called by review/main/publisher. Tests inject private
sentinels and verify zero stdout/stderr and no call path. Live logging is unchanged.
Activation, if wanted later, is a separate reviewed change, not this audit.

## Cheaper staged validation proposal, NOT authorized or executed

1. Finish offline standard-schema table/generative tests and adversarial runtime
   residual tests, plus independent review of numeric normalization, rename
   binding and the unwired telemetry privacy surface. No GKE/model cost.
2. Before paying for another GKE run, prepare and independently review a small
   opt-in synthetic-canary workflow/entrypoint using a disposable Linux runner
   and the existing verified provisioning path. Publishing that trusted wiring
   and using the existing opaque Max secret require explicit approval; no such
   workflow or canary call is added by this patch. Separately approve ONE final-schema
   canary using the same pinned native CLI/version/argv and isolated Max profile.
   Use two changed files (including a rename), multiple requirement/source sets,
   no PR/repository data, no GitHub token/write capability and no production
   receipt marker. One CLI process<=900s is a ceiling, not a known duration/cost.
   Host validates the result; failure stops without retry. A canary success does
   not prove semantic correctness, every keyword or production provenance.
3. Only after canary evidence and independent review, approve immutable AW/Tape
   pin+blob deployment through an infrastructure-only PR, exact-head merge, and
   ONE normal synchronized PR45 GKE run followed by<=2 production CLI processes
   and eligible issue44 publisher. Existing old-head GKE evidence cannot be
   treated as new-head evidence without changing the provenance contract; no
   manual dispatch/rerun bypass is assumed. Maximum: one GKE run and three CLI
   processes across canary+production, no automatic retry. Costs/Max use unknown.

No new credentials/storage/permissions/API billing, extra phases in production,
PR45 merge, automated-merge activation or cron change is proposed. Current live
validation authority is exhausted; this document grants none.
