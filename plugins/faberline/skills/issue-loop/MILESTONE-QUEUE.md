# Planner milestone admission; worker consumes todo

Milestone ordering and dependency admission belong to Claude **plan** only.
`issue-plan/scripts/plan.py` uses `scripts/milestone_queue.py` for that policy.
AGY `issue-work/scripts/work.py` consumes already-planned exclusive todo issues
by priority and creation time; it uses only shared REST/label helpers, not the
milestone frontier or dependency parser. Milestones remain assigned metadata.
Core milestones are repo development batches; crate versions remain independent.
No schedule, permission, CI qualification, release gate or done policy changes.

## Planner ordering and frontier

Every open **issue**, including backlog, blocked, doing and done awaiting review,
must reference an existing **open** version milestone before planner admission
to todo. This rule is not re-run by AGY on existing todo. Pull requests and closed historical issues are excluded. Missing,
closed, absent or malformed assigned milestones produce explicit metadata
planner blockers, never a default version. An unreferenced empty milestone does not
block, even if open or named with an old non-version title. No manual closure of
empty milestones is required.

Titles must match `[repo@][v]MAJOR.MINOR[.PATCH][-prerelease][+build]` in full;
`repo` may be the exact repository basename or `owner/repo`. For example,
`core@0.10.0`, `v0.10`, `0.10.0-rc.2` are valid. Prose titles, wrong repository
prefixes, leading-zero numeric parts, ranges and multiple versions are refused.
Numeric SemVer precedence applies (`0.9 < 0.10`, `rc.2 < rc.10 < final`);
omitted patch means zero and build metadata does not order work. Equivalent
version titles form the same batch. Milestone numbers and due/creation dates
never order batches.

The frontier is the lowest version with any open issue. Only exclusive `todo`
issues at that frontier without unresolved prerequisites are planner-eligible. Within a
batch: p0, p1, p2, p3 (missing defaults p2), oldest issue creation timestamp,
then issue number for deterministic ties. Priority never jumps a version.
Blocked/backlog/doing/review work can coexist with eligible work in the **same**
batch; if none is eligible, report that frontier's reasons and do not jump to a
later batch. Milestone assignment itself adds no test/release acceptance gate.
The planner never decides that a partial PR completes its parent issue.

## Planner dependencies and ambiguous prose

Read native incoming `dependencies/blocked_by` with full pagination; no native
edges are created or inverted. Also read the issue body and **all** comments,
including spec comments. Only explicit work-item declarations, work-item
subjects or tracker relations establish issue prerequisites. Component
architecture prose such as `ingest and persistence depend on each other` and
its quoted code-comment example do not. A direct `Depends on quota approval`,
`This issue depends on ...`, dependency-section entry or malformed issue
reference remains unknown and fail-closed. Recognize explicit `Depends on`, `Blocked by/on`,
`Dependency/Dependencies:` or `Prerequisite(s):` (also headings), `Waits for`,
`依賴:` / `前置條件:` / `先決條件:` (ASCII or full-width colon), and `stays/remains open ... until` declarations. Dependency headings
or colon-only declarations include following bullet lines until another normal
paragraph, heading or blank line. A fenced code example is ignored.

References can be `#N`, `owner/repo#N`, or a GitHub issue/PR URL; Markdown URL
identity wins over its display text. `Blocks #N` and `Unblocks #N` are outgoing
and are never treated as incoming. Incoming targets begin **after** their cue;
`#15 depends on #10` therefore has only target #10. Semicolons (including
full-width) and sentence boundaries separate explicit clauses: both
`Depends on #10; blocks #20` and `Blocks #20; depends on #10` retain only #10.
Reference spans are masked before cue/direction matching; names such as
`faberline/blocks#20` and `/blocks/issues/20` remain reference data. An inline
outgoing cue outside references also ends an incoming target span. Ambiguous subject or
direction boundaries and multiple incoming cues without a clause separator
produce an unknown blocker. An explicit current-issue **target** is excluded
from the dependency set and reported as a self-prerequisite blocker, whereas
a current-issue subject is not an error. Generic references and arbitrary typed
relationship arrays are not imported. A dependency declaration without a
resolvable reference is an explicit unknown prerequisite, not eligibility;
`Dependencies: none` or a dependency-section `- none` is an empty declaration,
not a waiver of other/native prerequisites. Contradictory or outdated prose
requires triage; an arbitrary later "resolved" comment cannot erase an earlier
reference. This bounded grammar cannot infer every natural-language statement:
planners must normalize ambiguous prerequisites into explicit incoming
references before queueing. Ordinary statements such as "no new dependencies"
are not incoming dependency declarations. No LLM interpretation or arbitrary executable gate
text runs in the selector.

A referenced issue resolves when closed; a referenced PR resolves only when
merged. A `done` label is not closure or merged evidence. Earlier references
remaining open block their dependent. If an issue at 0.5 waits for an open issue
at 0.7, status explicitly reports `dependency/version inversion`; it neither
jumps to 0.7 nor weakens the parent's acceptance. This covers Defer #15's real
spec wording, `#15 stays open after this PR until #10 lands and its scaling step
is added`. Move the **whole** issue to the appropriate reviewed batch rather
than pretending its partial PR resolves the parent. Cross-repository waits are
reported without inventing a cross-repository version ordering.

A failed/unsupported dependency or comment read, malformed API response, or
pagination bound is an error, never an empty list. Reads use the caller's normal
`gh` authorization; no credentials or permissions are added. Lists explicitly
page 100 rows until exhaustion; a 1000-page bound refuses rather than truncates.
The dependency endpoint requires Issues read, as documented by GitHub:
https://docs.github.com/en/rest/issues/issue-dependencies#list-dependencies-an-issue-is-blocked-by

## Planner interface

- `plan.sh status`: JSON decision followed by all open issues with milestone,
  labels and todo prerequisite diagnostics. It reports blocked frontiers and
  invalid metadata without changing anything. This read-only remote snapshot
  does not open/acquire the local work guard. It is non-atomic and explicitly
  sets `mutation_authorized=false`; neither `selected` nor `room` is a claim or
  write receipt. Unknown/incomplete reads fail rather than report an empty queue.
  Mutating commands still acquire the original exclusive guard and revalidate
  current state independently of any earlier status output.
- `plan.sh new TITLE BODY_FILE pN --milestone NUMBER`: validate an existing open
  version milestone, complete spec, capacity and duplicate title; create with
  milestone/priority **without todo**, then run normal adoption. If adoption
  fails, the printed URL identifies the backlog issue; never blindly replay a
  possibly executed issue creation.
- `plan.sh adopt N pN [--milestone NUMBER]`: use the existing valid assignment or
  explicitly assign the supplied existing milestone before considering todo.
  Require the spec, current frontier, prerequisites and room. New later-batch
  work stays backlog while an earlier batch remains unresolved.
- `plan.sh milestone N NUMBER`: assign an existing validated open milestone to
  backlog/todo/blocked work; no lifecycle change. Refuse doing/done work here.
- `spec`, `prioritize`, `block`: preserve existing intent, human body and other
  labels. A dependency wait is not a new human-decision `blocked` label. `block`
  still requires a real human decision and preserves active ownership.

Migration must supply reviewed milestone assignments; the scripts never create
or guess release versions. `new` now requires the explicit option. Existing
`adopt N pN` remains usable only with a valid assignment. Old priority labels and
all unrelated metadata are preserved except normalization of the numeric
priority namespace by an explicitly requested prioritize/adopt operation.

## Worker ownership, claim and recovery boundary

AGY selects **already-planned** open exclusive todo by p0/p1/p2/p3 (default p2),
then oldest creation timestamp and issue number. A later-milestone todo remains
consumable while earlier work is blocked/backlog/doing/review. Missing, invalid
or closed milestone metadata and prose/native prerequisites do not become a
second worker admission filter. Claude planner remains responsible for new
admission; AGY still reads the issue/spec and respects its implementation scope.
An open PR does not make an unowned lower-priority todo outrank other planned
work; when its issue is selected, CI repair reuses that same PR/branch/worktree.

Planner mutations and worker claims keep the same `issue-work.guard` inode.
Planner adoption revalidates full milestone/prerequisite state. Worker claims
revalidate the complete planned todo inventory/spec identity and PR association
after preparation and immediately before labels. Neither provides distributed
GitHub CAS. Remote last-read races remain documented.

Owned coding resumes ahead of new work; real blocked/conflicting lifecycle
labels still pause it without dropping its lease. Existing own same-PR repairs
preserve dirty worktrees. Submitted doing work waits for CI. Live PID, host,
owner, TTL, malformed lease, foreign/multiple PR and main-checkout protections
remain. Finish/block and CI-owned lifecycle remain unchanged.

Before todo→doing, save a `claiming` intent in the existing lease, perform one
label PATCH preserving unrelated labels, verify doing, then save `coding`.
Errors/uncertain writes preserve intent and worktree without an automatic retry.
A later owner `next --mine` reads current remote state first; confirmed doing
recovers without replaying the PATCH. A confirmed todo may be freshly
revalidated and claimed; no unknown/foreign owner is stolen. No reset, force
push, lease bypass, worktree removal or issue-done write is added.

Deployment is a separate reviewed action. Install planner policy, shared helpers
and corrected worker coherently; an old partial bundle lacks the module and fails import
rather than silently assuming planner admission policy. Existing live sessions are
not restarted or modified by preparing this draft PR.
