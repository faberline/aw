---
name: aw-supervisor
description: "Run one supervisor pass over the aw issue flow of one repository. Finds root causes, files decision and pipeline issues in faberline/aw, and updates the status board. Use when a routine prompt asks to run the aw-supervisor skill for `<owner>/<repo>`."
user-invocable: true
---

# aw-supervisor

## Quick exit

Do this before you read the rest of this file. It needs `aw` 0.7.80 or newer. With an older `aw`, or when `aw supervise` is an unknown command, skip it and run in full.

1. Run `aw supervise --check --repo <owner>/<repo>` (from the routine prompt). It reads through REST and writes nothing. Exit 0 is `quick`, exit 2 is `full`, exit 1 is an error.
2. `quick`: the state fingerprint is the one in the board and the last full run is under 6 hours old. Do only this, then stop and do not read the rest of this file:
   - Read the body of the board `status(<repo>)` (step 10). Replace the time after `更新：` with `date -u` of this run. Change nothing else, the marker line included. PATCH the body from a file (G22, G26).
   - Final answer, one line: `快速結束：沒有變化（<fp>）`.
3. `full`, an error, or no marker in the board: run the whole skill. The first line starts with `full reason=` and gives the reason.
4. At the end of a full run, after your last write, run the check again and write its `fp` in the board (step 10) as `<!-- aw:supervise fp=<fp> full=<UTC time> -->`, with `date -u +%Y-%m-%dT%H:%M:%SZ`. The second run counts your own writes. If the second check fails, write no marker: the next run is a full run.

The fingerprint covers: the main commit and the result of its workflows (G28), the count of `stage:spec`, `stage:tdd` and `stage:review` issues, `blocked` issues with their update times and their closed blockers (G10, G53), `decision`, `pipeline` and `needs-human` issues with their update times (G21, G54), queued and running runs, and open pull requests with their update time, merge state and failed checks. The UTC day changes at 04:00 (a full run each day, for G47). The fingerprint cannot see: a time-based item (stuck stage, quiet routine, slow queue), a comment on an issue that has no control label, and the check of a pull request that stays green. A full run at least every 6 hours covers them.

The supervisor watches the pipeline of one repository.
The routine prompt gives the repository as `<owner>/<repo>`.
Below, `<repo>` means the repository name without the owner.
`<target>` means `<owner>/<repo>`.

Other routines run the spec, tdd and review stages.
The supervisor does not run a stage.
The supervisor does not claim issues.
Run one pass, then stop.

Nobody reads your final answer in time.
Nobody reads your comments in `<target>` in time.
The control desk reads only faberline/aw issues, once each hour.
The desk round starts with `aw desk patrol`: it lists what to look at in every repository (main-red, blocked-new, stuck-stage, pr-stuck, queue-empty, waits-for-done, waits-for-split, decision-open, pipeline-open). The desk still reads each item; the list does not replace the check.
So each finding that needs a person or the desk must be in a faberline/aw issue.
It must also be in the status board (step 10).

## Write authorization

The routine prompt lists the allowed GitHub writes.
Do only those writes.
Use `gh api` REST only.
Do not edit other labels, merge, approve or push.
Do not close issues or pull requests, except under G21, G54 and G60 (below).
Do not change rulesets, settings, secrets or workflow files.
Do not delete anything.
When a write is not on the list, make a `decision` issue instead.
One exception: G21 (below) lets you execute a label-only desk decision.
The routine prompt must list the G21 label writes; without them, G21 does not run.
Another exception: G28 (below) lets you add four labels to a product issue that you open for a red `main`.
The routine prompt must list the G28 label writes; without them, G28 does not run.
Another exception: G41 (below) lets you move an issue from `stage:review` back to `stage:tdd` when no open pull request closes it.
The routine prompt must list the G41 label writes; without them, G41 does not run, and the item goes in a `pipeline` issue.
Another exception: G33 (below) lets you answer a blocked test issue of the known class "core v0.5.0 leader no-op" and remove `blocked`.
The routine prompt must list the G21 label writes for it; without them, G33 does not run, and the item goes to the desk.
Another exception: G47 (below) lets you add labels to the issue for a new RustSec advisory, and set its priority label to `p0`.
The routine prompt must list the G47 label writes; without them, G47 does not run, and the item goes to the desk.
Another exception: G53 (below) lets you remove `blocked`, set one `stage:*` label and post one comment on a stale blocked issue.
The routine prompt must list the G53 label writes; without them, G53 only reports on the status board.
Another exception: G54 (below) lets you execute a `中控可決定` item that has a recommended option: make the children of a split, edit a `Waits for:` line, add or remove `blocked`, and close a split or replaced parent.
The routine prompt must list the G54 writes; without them, G54 does not run, and the item goes to the desk as before.
Another exception: G58 (below) lets you remove `blocked` one time from an issue whose tdd or spec run was stopped by a tool refusal.
The routine prompt must list the G21 label writes for it; without them, G58 does not run, and the item goes in a `pipeline` issue.
Another exception: G60 (below) lets you close an issue as done when every acceptance criterion already holds on the default branch.
The routine prompt must list the G60 writes; without them, G60 does not run, and the item goes to the desk.
Another exception: G61 (below) uses the G47 label writes on the upgrade issue for a dated ignore entry.
The routine prompt must list them for G61; without them, the issue has no labels and the label change goes to the desk.
Another exception: G67 (below) lets you add `stage:spec`, `agent:claude` and `p0` to the one issue that moves an old aw pin of the workflows.
The routine prompt must list the G67 label writes; without them, the issue has no labels and the label change goes to the desk.
Another exception: G71 (below) lets you add `stage:spec`, `agent:claude` and `p2` to the one release readiness issue that you open for an empty queue.
The routine prompt must list the G71 label writes; without them, the issue has no labels and the label change goes to the desk.
Another exception: G72 (below) lets you correct the stage labels of a closed issue that a merged pull request completed.
The routine prompt must list the G72 label writes; without them, G72 only reports on the status board.
G25 (ghost runs) and G27 (the CI queue tracker) do not add writes. G27 only adds comments on faberline/aw#372.

This cloud session blocks GitHub GraphQL.
Do not use `gh issue`, `gh pr` or `gh repo`.
`gh auth status` can say the token is invalid. Ignore that.

## Text safety (G5)

Do not write the characters `->` or a bare `>` in an issue body or a comment.
A desk guard reads them wrong.
Write the word `to` or `then`.

## Steps

### 1. Check access

Run `gh api repos/<target> --jq .full_name`.
When it fails, stop.
Report `gh unavailable` with the exact output.

### 2. Build `aw`

Build `aw` from the faberline/aw checkout:
`cargo install --locked --path <aw checkout> --root ~/.local/share/aw-rs`.
Do not run `aw claim`, `aw report` or `aw release`.
Read `docs/standards/ci.md` in the aw checkout.
Read `plugins/faberline/skills/aw-worker/SKILL.md` in the aw checkout.
Now you know the flow.

### 3. Read decisions first

List the closed issues in faberline/aw that have the label `decision`.
Keep those with a title that starts with `decision(<repo>)`.
Keep those closed in the last 7 days.

For each answer, check two things.
The answer names an action that you may do.
You have not done it yet.
You have not done it when no comment of yours links the answer.
Then do it now.
Comment the link on the affected issue or pull request.

Then follow through.
Do this for each `decision(<repo>)` or `pipeline(<repo>)` issue closed in the last 24 hours.
Check that `<target>` really moved:

- The affected issue lost `blocked`.
- A stage routine claimed it or commented within 2 hours.
- A caller pin of an aw workflow SHA is updated, when the fix needs it.
- A failed run is rerun once, when the fix makes a rerun useful.

When the work did not move, write it in the status board under `已解除但沒人接手`.
When it did not move for more than 2 hours, create a `pipeline` issue.

A decision that has a comment "executed by supervisor" is done (G21, below).
Do not do its actions again.

After a decision removes `blocked`, do the claim check (G4, below).

### 4. Find problems

Look only at these:

- a. Issues whose last aw signal is `blocked`.
- b. Open pull requests with a failed check.
  Also open pull requests with no new commit, check or comment in the last 3 hours.
- c. The newest `ci` run on the default branch, when it failed.
- d. Issues with a `stage:*` label and an `agent:*` claim label.
  Their claimed branch has no new commit for 3 hours.
- e. Blocked issues whose reason is gone (G10, below).
- f. Stage liveness (G8 and backpressure G40, below).
- g. Review loops (G12, below).
- h. Parent issues whose children are all done (G13, below).
- i. Issues with `stage:tdd` and `blocked` whose last signal says the spec is incomplete (G16, below).
- j. Issues with `stage:spec` and `blocked` whose last signal proposes a split, and no child exists (G17, below). The same round, not later (G31, below).
- k. Issues whose last aw signal is `blocked` and asks a person a question, with no open `decision` issue that links them (G20, below).
- l. Queued runs with zero jobs for more than 24 hours (ghost runs, G25, below).
  List them only. A ghost run is not a problem.
- m. Issues with a `stage:*` label and no `blocked` label that nobody touched for more than 2 hours (pickup age, G34, below).
- n. Issues in `stage:review` whose last review signal says that no open pull request closes them (G41, below).
- o. Sent-back issues that no run touched for more than 2 hours while their stage is in rework-only mode (G42, below).
- p. Every open issue with `blocked`, not only the changed ones (stale block sweep, G53, below).
- q. In a full run: the aw pins of the workflows on the default branch (G67, below).
- r. An empty queue: no open issue has `stage:spec`, `stage:tdd` or `stage:review` (G71, below).
- s. In a full run: closed issues that still have `blocked`, `stage:spec`, `stage:tdd` or `stage:review` (G72, below).

### 5. Find root causes before you act

Read the issue, the signal comment, the pull request and the failed job log.
Group the problems by cause.
Follow the dependency chain to the first cause.
Issue A blocks B, and B blocks C.
Before you name a cause for a CI or infrastructure failure, apply G14 (below).
Then pick exactly one action for each root cause:

- **Infrastructure failure.**
  Examples: runner lost, network, registry, timeout, GKE capacity.
  The job was not rerun yet.
  Rerun the failed jobs once:
  `gh api -X POST repos/<target>/actions/runs/<id>/rerun-failed-jobs`.
  Comment what you saw.
  For a runner or network failure, apply G15 (below) first.
- **The spec is unclear, too large or wrong.**
  The fix stays inside the issue's goal.
  Comment on the issue with the diagnosis.
  Add a concrete proposal for the planner: scope, split, commands.
  The spec routine plans.
  Do not change labels.
- **A real defect in `<target>` that no open issue covers.**
  Create one issue with the evidence and no labels.
  One exception: a red `main` issue gets four labels under G28 (below).
  Write the body from a file and read it back (G26).
- **A defect in aw, the aw-worker skill, `aw ci check` or a shared CI standard.**
  Create a `pipeline` issue in faberline/aw.
  Use the title `pipeline(<repo>): <short>`.
- **It needs the owner or the desk.**
  Examples: money or billing, rulesets or branch protection, visibility, deletes, secrets, frozen contracts or pinned hashes, product scope, a choice between two valid directions, a label change or issue closure that you may not do.
  First test the item against G54 (below).
  An item of type `中控可決定` that has a recommended option and only G54 writes is yours: execute it in this pass and do not send it to the desk.
  Otherwise create a `decision` issue in faberline/aw.
  Use the title `decision(<repo>): <short>`.
- **Already reported.**
  Do not stop there.
  Check that the tracking issue is still open.
  Check that it still agrees with the facts.
  When the facts changed, comment the new facts on the tracking issue.
  Then apply step 9.

#### Cross-repository cause (G2)

An issue in another repository can block `<target>`.
An issue in faberline/aw can also block it.
Then find the faberline/aw issue whose title names that other repository too.
You may add `urgent` to that issue.
Comment there with the list of issues it blocks in `<target>`.
Use links.

#### Cross-repository answers (G7)

Sometimes the answer to a question is in another repository.
Then paste the full answer text into the issue of `<target>`.
Add the link.
A stage routine can read only its own repository.
A link alone is not enough.

### 6. Search before you create

Before you create any issue, search the target repository.
Search the open issues.
Search the issues closed in the last 7 days.
Search for the same failure, not only for the same title (G43, G54).
When an open one exists, add a comment there instead.
When a closed decision answers the question, follow the decision.
Do not ask again.
One root cause gets one issue.
The issue lists each affected issue and pull request.
A runner or network failure can hit more than one repository.
Then search faberline/aw for the error text first (G15, below).

### 7. Verify before you propose

Each command, option, file path, label and issue number that you write in an issue must exist.
Check it with the built `aw --help`, `ls`, `git` or `gh api`.
Do not propose an `aw` command that you did not find in `--help`.
Do not leave an unverified fact in an issue or in your final answer.
One API call can check it.

### 8. Decision issue format

A `decision` issue body is in Traditional Chinese (Taiwan).
Keep it short and plain.
It has this format:

- First line: `類型：需要使用者` or `類型：中控可決定`.
  Use `需要使用者` only in these cases: money or billing; an action that cannot be undone; deletes; rulesets; visibility; secrets; frozen contracts; a case in the "never in G54" list (G54); you have no recommendation.
  A conflict between the issue text and the spec is not `需要使用者` when one option keeps the acceptance of the issue as written: that option is `中控可決定` and you execute it (G54, "Keep the acceptance as written").
  Use `中控可決定` for all other cases.
  A release tag or a paid run that a test needs is not `需要使用者` when an unreleased build or a run that exists meets the acceptance as written (G56).
  A choice of direction is not `需要使用者` when the recommended option keeps the acceptance, starts no paid run and no tag, and needs only a new issue in another Faberline cloud repository (G57).
  A test that a reference test replaces is not `需要使用者` when the three facts of G59 are shown: the desk decides it, and you write the recommendation.
  A `blocked` signal whose only cause is a tool refusal gets no `decision` issue (G58).
  A recommended option that keeps every acceptance criterion, costs no money, makes no tag and does not change the product direction is `中控可決定`, also when you cannot execute it yourself (G62).
  A spec that changes or drops an acceptance criterion of the issue with no desk decision is a `中控可決定` item: the recommended option keeps the acceptance as written (G64).
  A major upgrade of a shared dependency whose spec does not list every workspace crate that uses it is a `中控可決定` item: the recommended option sends the issue back to `stage:spec` (G65).
  A recommended option that is only a wait, with no write, no cost and no change of product direction, gets no `decision` issue and no `needs-human` label: write `Waits for:` on the issue and keep `blocked` (G66).
  A spec that cannot read a file of another repository, when that repository is public, is a `中控可決定` item: the option gives the commit, the path and a checksum, and the tdd run copies the file (G68).
  An acceptance criterion that the desk wrote, and whose premise the spec proves false, is a `中控可決定` item: the desk can restate it (G69).
  A test that fails because an upstream release changed a public signature on purpose, when the fix is one expected string with the same assertion strength, is a `中控可決定` item (G70).
  An empty queue is not a `decision` item when the next work is free: open the release readiness issue yourself (G71). The paid step or the tag that is left after that check is a `需要使用者` item with the number in TWD (G71).
  A `需要使用者` decision issue also gets the label `needs-human`.
  A `中控可決定` item that passes the G54 checks is not sent to the desk: you execute it (G54).
- The repository and links to each issue or pull request.
- What is blocked, since when, and what else it blocks.
- The facts the owner needs.
- Options (see G3 and G9).
  Each option says what happens when the owner picks it.
- Under each option, an `執行清單`.
  Write each write that follows, one line each.
  Each line has: the repository, the issue number, the label to add or remove, the issue to close or create, and the comment in one line.

Put all decisions of this run in one issue.

#### Options (G3)

A `decision` issue has at most two options.
Put the recommended option first.
When you have no recommendation, say so.
Then use `類型：需要使用者`.

Do not recommend `stage:spec` or `stage:tdd` for an issue whose work is an operation:
a `workflow_dispatch`, a paid run, a tag, or a manual drill.
A spec run or a tdd run changes files and opens a pull request. It cannot dispatch a workflow or run an operation.
Say that the desk or the owner must run it.
List the inputs that the operation needs, and say which of them are unknown.
Example failure: the tape supervisor recommended `stage:spec` for tape#20 (a manual `workflow_dispatch` of a release candidate), and the spec routine correctly refused.

Before a `decision` that asks for a paid run, look for a run that exists (G55, below).
Before a `decision` that asks for a release tag or a paid run as a precondition of a test, look for an unreleased build (G56, below).
For each option of a choice of direction, write which data or which acceptance criterion it changes (G57, below).

An option can depend on a fact that you can read.
Examples: a test message, a document, a closed issue, a tag.
Read it before you open the decision.
Write the result under the facts (the facts section of the decision).
An option must not say "needs confirmation" for something you can read in the repositories of the routine.
Example failure: decision faberline/aw#272 listed the cost "needs confirmation that the old decision no longer applies".
It left this check to the desk.
The supervisor could read the old decision and check it.

#### Numbers in options (G9)

Each number in an option must come from a measurement or a file.
Examples: a budget, a threshold, a count, a duration.
Link that measurement or file.
When there is no measurement, the option says "measure first".
Then the option proposes the measurement.

### 9. Urgent

A root cause is urgent when one of these is true:

- No pull request in `<target>` can merge.
- A `p0` issue is blocked.
- One cause blocks 3 or more issues.
- Its faberline/aw issue is open for more than 2 hours and has no comment after its creation.
- A stage routine is dead (G8).
- A review loop (G12) is on the release chain (G11) or has `p0` or `p1`.
- A finished parent issue (G13) stops a chain that has `p0` or `p1`.

For an urgent root cause, put the label `urgent` on its faberline/aw issue.
Create a new issue with the label, or add the label to an open one.
Mark it `緊急` in the status board.

A cause that you could not confirm gets `urgent` only when you cannot narrow it from the job log.
You cannot download a CI artifact (the blob host is blocked). You can read the job log, and the job log is often enough.
Before you write `urgent` with `cause not confirmed`, do these in the same pass:

1. Read the log of the failed job to its end (G14). For a ci-on-gke stage, the folded group of a failed Job and the job summary show the events and the last lines of the log of that Job (faberline/aw#716).
2. Narrow the cause to one of these: the code of the diff; a lock file or an advisory (G47, the lock file sweep); the manifest or the workflow; the cluster (name the event). Write what the log excludes.
3. When the log narrows the cause, name the narrowed cause and follow its rule. Do not add `urgent` for "not confirmed".
4. When the log does not narrow it, add `urgent` and name the file that the desk must read: the run id and the attempt, the artifact name and the file in it (for example run 45044890330 attempt 2, artifact `ci-test-logs`, file `events.txt`), and the question that the file answers.

Evidence, 2026-11-22: faberline/aw#716. The supervisor could not read `events.txt`, wrote `cause not confirmed` and `urgent`, and every pull request of som stayed blocked for 4 hours. The job log showed that the failed Job was `test/audit` and that it failed after 1.5 minutes. The lock file sweep of G47 gives the cause from there.

### 10. Status board

Do this in each run, also in a normal run.
Do it as the last write.
An unchanged state is not a reason to skip it (G22, below).

Find the open issue in faberline/aw with the label `status` and the exact title `status(<repo>)`.
Create it when there is none.
Replace its body (PATCH) with the current state.
Write the body to a file and post it from that file (G26).
Write it in Traditional Chinese (Taiwan):

- `更新：<UTC time>` and `總結：正常`, `總結：異常`, `總結：排隊` (G23: slow CI queue, with the numbers) or `總結：空佇列` (G71: no stage has an open issue, with the cause).
- `## CI 排隊` (G23): wall time, longest job, queued and in-progress runs, and the `behind` PR count.
- `ghost runs` (G25): the ghost run ids, one line. Write `none` when there are none.
- `## 根因`.
  One bullet for each root cause.
  Each bullet has: `緊急` when urgent; what it is; since when; what it blocks, with the chain; who it waits for (使用者, 中控, a routine, or another repository); the faberline/aw issue link.
- `## 規格卡住` (G31): each open issue whose last signal is `stage=spec outcome=blocked`, one line each: the issue link, the age of the signal (from `date -u`, in hours or days), `split` or `Decision`, and the link of the open decision item. Write `none` when there are none.
- `## 封鎖檢查` (G53): `檢查 <N> 個 blocked issue；解除或改指 <M> 個：#a, #b`, and the time of the sweep. Write the issue numbers that need a decision, one line each.
- The marker line of the quick exit, directly under `更新：`.
- `## 已解除但沒人接手`.
- `## 排隊太久` (G34): each issue with a `stage:*` label, without `blocked`, not touched for more than 2 hours, one line each. Write `none` when there are none.
- `## 這一輪做了什麼`.
  Each write of this run, with its link.

Keep no history in the body.
Read the labels of each issue that the body names in this pass, before the write (G19, below).
Read the state of each issue that the body or a decision cites as open, closed, blocked or urgent in this pass (G24, below).

#### Time (G1)

Get the time with `date -u +%Y-%m-%dT%H:%MZ`.
Run it in the same run.
Do not estimate the time.

### 11. Limits

Do at most 6 writes in one run.
The status board is always one of them.
Keep a gap of at least 2 seconds between API calls.

### 12. Final answer

Before you write it, run this end-of-run check:
the PATCH of the status board in this run is done, and its `更新：` time is from `date -u` of this run (G22).
When it is not done, do it now.
A run that ends without this PATCH is a failed run.

Write it in Traditional Chinese (Taiwan).
Keep it short and plain.
Start with one line: `supervisor <target> cloud run: <正常 or 異常>`.
The second line is the link to the status board.
List each root cause and its action, with links.
Do not send push notifications.

## Extra rules

### Rules in separate files

These rule groups are read only in the case that they name. Read the file with `Read`; it is in the same folder as this file. The rule numbers stay the same. A step that cites one of these rules with "below" means that file.

- Read `special-desk-decisions.md` (G21, G54) when an open decision issue has a desk answer that you may carry out yourself, when a label-only desk decision waits, or when a blocked signal says that the issue text and the spec or the tests do not agree.
- Read `special-blocked-recheck.md` (G10, G53) when a blocked issue is new or changed, when the blocker of a blocked issue may be closed, or in the stale block sweep.
- Read `special-split-proposed.md` (G17) when an issue has a split proposed and no child issues.
- Read `special-tdd-blocked-spec.md` (G16, G31) when a tdd issue is blocked on an incomplete spec, or a spec blocked signal needs a same-round answer.

### Claim check (G4)

An issue can be claimed only when it has all three of these:

- the label `agent:<name>`;
- a `stage:*` label;
- no `blocked` label.

Check them after a decision removes `blocked`.
Check them for each new issue that you report.
When one is missing, write the issue in the status board under `已解除但沒人接手`.
Name the missing label.
Also ask the desk in the faberline/aw issue.

### Nobody picked it up (G6)

Before you report "nobody picked it up", do two checks:

1. Apply the claim check of G4.
2. Read the last aw signal of the issue.

Then report the exact reason.

### Liveness (G8)

Do not use `updated_at` of an issue to decide that a stage routine works.
For each stage (`spec`, `tdd`, `review`), find the newest `aw:signal` comment of that stage in `<target>`.
Note its time.

A stage may have no signal for more than 3 hours.
Claimable work may exist for that stage.
Before you report a dead routine, apply G40 (backpressure) to the `tdd` and `spec` stages. A stage in backpressure is not dead.
Then check this:
a worker releases an issue that waits for an open prerequisite and takes the next one (`aw-worker` section 4).
A release writes no `aw:signal`.
So a live routine can look silent.
Read the newest supervisor, desk or review comment of each claimable issue of that stage.
Check the state of each issue or pull request that it waits for with `gh api`.
When every claimable issue of that stage waits for a prerequisite that is still open, this is not an alarm.
Do not create a `pipeline` issue.
List those issues and what each waits for on the status board, for example `tdd: #19 waits for PR 140`.
When one claimable issue does not wait, or a prerequisite is closed, the routine should have taken it.
Then this is a root cause.
Do these things:

1. Read the newest comments and the default-branch state to find the error.
2. Create a `pipeline` issue with `urgent`.
3. Mark it `緊急` in the status board.

Example failure: lumen stopped for 14 hours.
`aw claim` exited 1 in each run.
The supervisor reported `正常`.

### Release chain (G11)

A blocked issue can wait for a tag or a release of another repository.
Then name the tracker issue of that release.
List what still stands before the tag:

- open milestone issues;
- open pull requests.

Add a link for each.

### Review loop (G12)

A pull request can fail review again and again.
Each time the finding is small and new.
Nobody sees the pattern.

For each open issue with the label `stage:tdd` or `stage:review`, do this:

1. Find the current pull request of the issue.
2. Count the `aw:signal` comments with `stage=review` and `outcome=failed` for that pull request.
3. When the count is 3 or more, this is a root cause named "review loop".
   Write it in the status board.
   It is urgent (step 9) when the issue is on the release chain (G11) or has `p0` or `p1`.

For a review loop, read the last two failed verdicts.
Write one comment on the product issue in `<target>`.
Write it once for each head SHA of the pull request.
Before you write, search the comments for that SHA.
Do not write a duplicate.
The comment has:

- the exact list of the findings that remain, each with file and line;
- the instruction that tdd must check each claim against the code on `main` before it pushes;
- the name of the pattern, for example: "each fix adds unverified statements".

When the count is 5 or more, or the findings contradict each other or the spec, open one `pipeline(<repo>)` issue in faberline/aw.
First check for an open one for the same pull request.
When one is open, add a comment there.
The issue lists the pull request and the failed verdicts, with links.

Do not edit labels.
Do not approve or merge.

Example failure: core#163 (pull request 171) got six failed review signals.
Each had a small new finding.
The `status(core)` board did not name it.
The issue is on the release chain to `v0.5.0`.

### Parent issue with all children done (G13)

A parent issue can stay open after its work is done.
It has no `stage:*` label, so no stage routine claims it.
Other issues wait for it.
The chain stops, and no routine sees it.

For each open issue in `<target>` without a `stage:*` label, do this:

1. List its child issues.
   Use `gh api repos/<target>/issues/<n>/sub_issues`.
   Also read the issue links in the task list of its body.
2. Check that each child is closed and has the label `stage:done`.
   The issue must have at least one child.
3. Check that another open issue waits for this parent.
   Read the last aw signal and the body of each `blocked` issue.

When all three are true, this is a root cause named "finished parent".
Open a `decision` issue in faberline/aw in the same run.
Use `類型：中控可決定`.
Use one option.
Write these facts: each child with its link and its close time; the issue that waits; since when it waits.
The `執行清單` has these lines:

- close the parent issue, with the comment text in one line;
- remove `blocked` from each issue that waits for the parent;
- add the `stage:*` label to a waiting issue that has none (G4).

Search the open faberline/aw issues first (step 6).
When an open `decision(<repo>)` issue exists, add a comment there with the full `執行清單`.
Do not create a duplicate.

A comment on the parent that asks the planner to decide is not enough.
No routine reads it.
You may write that comment, but the `decision` issue is required.

When the chain has `p0` or `p1`, add `urgent` (step 9).
Do not close the parent yourself.
Do not change labels yourself.

Example failure: faberline/core#157 had only closed children with `stage:done`.
It stopped core#147 from 11:54Z to 14:30Z on 2026-11-11.
core#147 is on the release chain to `v0.5.0`.
The supervisor left a comment for the planner.
The desk found the problem by hand.

### Verify the cause before you name it (G14)

A failed job can show more than one warning.
The first warning is often not the cause.

Before a `decision` or `pipeline` issue names a cause for a CI or infrastructure failure, read the evidence:

1. Read the log of the failing step to its end.
   Use `gh api repos/<target>/actions/jobs/<job_id>/logs`.
   Do not stop at the first warning or the first error.
2. Read the related events.
   For a GKE job, read the autoscaler events and the scheduler events that the job prints.
   Examples: `max node group size reached`, `FailedScheduling`, `NotTriggerScaleUp`.
   Read all of them, not only the first warning.
3. Find the earliest event that explains the others.
   That event is the cause.

In the issue, write which evidence you read.
Give the run link, the job name, the step name and the exact line that shows the cause.

Sometimes you cannot read the evidence.
Examples: the log is expired; the job does not print the events.
Then write `cause not confirmed` in the issue.
List what you read and what you could not read.
Name each file that you could not read, so the desk can read it: the run id and the attempt, the artifact name and the file name.
First narrow the cause from what you can read (step 9). The label `urgent` is only for a cause that the job log does not narrow.
Do not offer options that depend on the cause.
Ask only for the missing evidence.

Example failure: faberline/aw#286 named `FailedAttachVolume` as the cause of a failed GKE test of tape.
The real cause was the autoscaler event `max node group size reached`.
The `max_nodes` value of the pool was too small.
The supervisor read only the first warning.

### One cause, one issue (G15)

A runner or network failure is not a defect of `<target>`.
Examples: DNS, an apt mirror, a registry, a GitHub outage.
The same failure can hit other repositories at the same time.
Each supervisor sees only its own repository.

Before you open a `pipeline` issue for a runner or network failure, do this:

1. Copy the exact error text from the log (G14).
   Example: `Temporary failure resolving 'azure.archive.ubuntu.com'`.
2. Search the open issues in faberline/aw for that error text.
   Use `gh api -X GET search/issues -f q='repo:faberline/aw is:issue is:open "<error text>"'`.
   Search with the host name alone too.
3. When an open issue exists, do not create a new one.
   Add one comment there with the name of `<target>` and the run link.
   Do not write a second comment for the same run.
4. When no open issue exists, create one `pipeline` issue.
   Put the exact error text in the title and in the body.
   Then the other supervisors can find it.

Mark such a failure as infrastructure, not repository code.
Write `infrastructure, not <repo> code` in the issue or the comment, and in the status board.
Do not create an issue in `<target>` for it.

Rerun the failed jobs at most once in each hour.
Before a rerun, read `run_attempt` and `updated_at` of the run.
When the last attempt is less than one hour old, do not rerun.

Example failure: the defer supervisor opened faberline/aw#294 for `Temporary failure resolving 'azure.archive.ubuntu.com'` as a defer problem.
The same failure hit tape and surface at the same time.
It was a problem of the hosted runner.

### Runs queued with zero jobs (G18)

The whole faberline organization shares 20 concurrent Actions jobs.
Many runs that stay "queued" with zero jobs mean that the 20 slots are full.
This is not a GitHub outage.
A rerun adds to the queue and makes it longer: do not rerun.
Ghost runs (G25) are not counted here.
Add the evidence (run links, the repos that wait, the time) to the one tracker, faberline/aw#372 (G27), only when the numbers changed.
Do not open a new issue for this cause.

Example failure: faberline/aw#316.

### Queue wait: slow ci runs (G23)

G18 only sees runs with zero jobs.
A queue can also be slow while every run has jobs: the work is short, the wait is long.

Known cause: the faberline organization has 20 concurrent Actions jobs for all repositories.
The organization is on the GitHub Free plan. The user decided on 2026-11-13 not to upgrade.
A long wall time with normal job times is this queue.

Do this once in each pass, with one read for each item and no polling loop:

1. Take the latest completed `ci` run on `main` and the oldest unfinished (`queued` or `in_progress`) `ci` run of `<target>`.
   Skip ghost runs (G25): a ghost run is never the oldest unfinished run.
   Read the jobs of each run once (`gh api repos/<owner>/<repo>/actions/runs/<id>/jobs`).
2. Count the `queued` and `in_progress` runs of `<target>` (one list read), without the ghost runs (G25).
3. Write on the board, under `## CI 排隊`: the run wall time (first `created_at` to last job `completed_at`, or to now for an unfinished run), the longest single job, and the two counts.
4. When the wall time is more than 2 times the longest job, or a run has waited more than 60 minutes, the summary is `總結：排隊` with these numbers. It is never `總結：正常`.
   An other root cause can still make it `總結：異常`.
5. Do not rerun a queued or slow run.
   When the wait matches the known cause, do not open a new `pipeline` issue.
   Write the numbers of step 3 on the board.
   The one tracker of this cause is faberline/aw#372 (G27). Link it on the board.
   Examples of CI-load issues: faberline/lumen#371 and faberline/core#309.
   Read the state of each cited issue first (G24). Do not cite a closed issue as active.
   Open a `pipeline` issue only when a different cause exists, and say which one on the board:
   - There is evidence of a different cause. Examples: a run has zero jobs for more than 30 minutes (ghost runs of G25 do not count), a runner error, or the wait stays after the CI-load issues are merged. G18 still applies to runs with zero jobs.
   - aw#372 is closed or missing (G24). Do not open a new issue for the same cause. Open one `decision` issue that asks the desk to reopen aw#372 or to name another tracker.
6. Also count the open PRs of `<target>` that are `behind` main and whose failed required check ran before the latest fix on `main`.
   Write the count and the two oldest PR numbers.
   Use the PR list you already read; read the check run time of a PR only when it is `behind` and has a failed check.

Example failure: the lumen board (faberline/aw#256) of 2026-11-13 12:14Z said `正常`.
Its `ci` runs took 60 to 128 minutes for 192 job-minutes of work (longest job 30 minutes), 23 runs were queued in the organization, and about 20 open PRs carried old failures and were `behind` main.

Example failure: faberline/aw#377 (kit) opened a second issue for the same queue. It was closed into faberline/aw#372.

### Read the state of a cited issue (G24)

A board or a decision can say that an issue is open, closed, blocked or urgent.
That state can change between two passes. A copied state can be wrong.

Before the board or a decision cites any issue (in faberline/aw or in another repository) as open, closed, blocked or urgent, read its state and labels in this pass.
Use `gh api repos/<owner>/<repo>/issues/<n> --jq '{state, labels: [.labels[].name]}'`, one read for each issue.
Write the state that you read.
Do not copy a state from the old board or from memory.
A closed issue is not written as open, and not as a root cause that is still active.
When you cannot read it, write `狀態未讀取`.

Example failure: the lumen board (faberline/aw#256) of 2026-11-13 cited faberline/aw#351 as `urgent，仍開著`. It had been closed some hours before.

### Read the labels in this pass (G19)

The status board names issues and says what state they have.
A label can change between two passes: a stage run adds `blocked`, the desk removes it.
So a label state from the old board body, from an old comment or from memory can be wrong.

For each issue or pull request that the new board body names, read its current labels in this pass.
Use `gh api repos/<owner>/<repo>/issues/<n> --jq '[.labels[].name]'`, or one list of the open issues of `<target>` that you read in this pass.
Do this after the other writes of the pass and before you replace the board body.

- Do not copy a label state from the old board body.
- Write "has no `blocked` label", "can be claimed" or "is ready" for an issue only when the labels that you read in this pass show it.
- An issue can be claimed only when it has no `blocked` label, it has a `stage:*` label and it has an `agent:*` label (G4).
  Also read its last aw signal: when the outcome is `blocked`, the issue is not ready, with or without the label.
  A `blocked` signal on an issue that has no `blocked` label is a root cause; write it on the board.
- When you cannot read the labels of an issue (quota, access), do not write a label state for it.
  Write `標籤未讀取` for that issue.

Example failure: the lumen board (faberline/aw#256) of 2026-11-11 23:11Z said that lumen#321, #323 and #324 had no `blocked` label and could be claimed.
lumen#323 had a spec signal with the outcome `blocked` from 22:21Z and lumen#324 one from 22:27Z.
Both issues had the `blocked` label.

### A blocked signal that asks a person (G20)

A stage run reports `blocked` when it needs an answer that it cannot give.
The signal text then asks a person: `Decision needed`, `Decision: ...`, a question, or two or more options with a recommendation.
No routine answers such a signal, and the desk acts only on an `執行清單`.
So a note on the board is not enough.

For each open issue in `<target>` whose last aw signal has the outcome `blocked`, read the signal text.
When it asks a person, there must be an open `decision` issue in faberline/aw that links the issue.
Search first (step 6).
When one exists, do not make a second one; add the new item there as a comment with the full `執行清單`.
When none exists, open it in the same pass.
Do not wait for a second pass.
Put all such signals of this run of `<target>` in the one decision issue of the run (step 8).

The decision item has these parts:

- The type (step 8): `類型：中控可決定`, or `類型：需要使用者` only in the cases that step 8 names.
  When the signal has a recommendation and no case of step 8 applies, the type is `中控可決定`.
  When one issue of the run needs the user, the decision issue has `類型：需要使用者` and each item says its own type.
- The link to the issue and the link to the signal comment, and the time of the signal.
- The question of the signal in one line.
- The options (G3, G9).
  The first option is the recommendation of the signal, with the words of the signal.
  Say that it is the recommendation of the stage run.
  When you do not agree with it, keep it as the first option and write your reason under it.
  When the signal has no recommendation, verify the facts (step 7) and give your own; when you have none, the type is `需要使用者`.
- Under each option, the `執行清單`: the answer comment for the issue in one line, the label to remove (`blocked`), the `stage:*` label to add when the issue has none (G4), and each issue to create or close.

This rule does not replace G16 and G17.
A tdd signal that says the spec is incomplete goes to G16.
A spec signal that proposes a split goes to G17; its case 1 (a `Decision:` line) makes the same decision item as this rule.
Make one item for one signal, not two.

Do not answer the question on the issue yourself and do not change the labels.
The exceptions are G21 and G54 (below).
When the signal has a recommendation, test the item against G54 before you open a `decision` issue.
When the recommended option is only a wait, open no `decision` issue: use G66 (below).

Example failure: lumen#323 (22:21Z) and lumen#324 (22:27Z) on 2026-11-11.
Each got a spec `blocked` signal with "Decision needed" and a recommendation.
The pass of 23:11Z opened no decision issue for them.

### Label a red-main issue you open (G28)

Use this rule only when all of these are true:

- the run opens an issue in `<target>` because the `ci` run on `main` is red;
- the body has the failing required job, the run link and the failing step;
- no open issue in `<target>` covers the failure (step 6).

Then add these four labels to that new issue, in the same run: `stage:spec`, `agent:claude`, `p1` and `expedite`.
`expedite` is the label that lets a spec or tdd run claim the issue while the stage is in backpressure (`aw-worker`, "Backpressure"). Without it, a red `main` waits behind the queue that its own red `main` makes longer.
Report the new issue on the status board under `這一輪做了什麼`, with its link.
Do not open a `decision` issue for this.

This rule has limits:

- It applies only to an issue that this run created for a red `main`.
- Do not add or change a `stage:*`, `agent:*` or `expedite` label on any other issue. G21, G41 and G54 are the only other label changes.
- When the fix needs a choice between two options, open a `decision` issue as before (step 5).

#### A release blocker (expedite by decision)

An open issue that a release waits for (G11: the issue blocks the release tag, or a `release` or milestone gate names it) and that stays unclaimed for 2 hours while its stage is in backpressure (G40) is a release blocker.
You do not add `expedite` to it yourself.
Open one `decision(<repo>)` issue (step 8) that proposes the label as option A (recommended): the issue number, the release it blocks, how long it is unclaimed, and the counts of G40.
The desk or the owner adds the label. When the label is on the issue, G30 and G34 apply to it as to any other issue.

Example where the desk added only the first three labels: faberline/aw#376 (defer#164). Another: faberline/aw#379 (defer#172).

### Milestone of a red-main issue (G29)

An open issue that tracks a red `main` in `<target>` and has no milestone gets the current in-progress milestone of `<target>` (the open milestone with the lowest version that has open issues).
Report the change on the status board under `這一輪做了什麼`, with the link.
Do not change an issue that already has a milestone.
The routine prompt must allow this milestone write; without it, only name the missing milestone on the board.

### A claimable issue that stays unclaimed (G30)

An issue is claimable when it is open, has a `stage:*` label and `agent:claude`, and has no `blocked` label.
When a claimable issue stays unclaimed for two rounds in a row, the board names the cause on one line.
Pick one: claim order (higher issues come first), label (missing or wrong), lock (another claim), or routine not run.
Write `cause unknown` only when none of the four fits.

### The status board is written in every run (G22)

Step 10 says to write the status board in each run.
An unchanged state is not a reason to skip the write.
"Nothing has changed since the last board" is not a reason.
"No new write is needed" is not a reason.

In every run, PATCH the body of `status(<repo>)` with a new `更新：<UTC time>`.
Get the time with `date -u` in this run (G1).
Write the new time even when every other line of the body is the same.
The old time shows that the supervisor did not run, so it must not stay.
A run that ends without this PATCH is a failed run.
Say so in the final answer and write `異常`.

Exception (quick exit): when `aw supervise --check` says `quick` (see the top of this file), the one PATCH of the `更新：` time is the whole write of the run, and the final answer is one line.
Every other run follows the text above.

Example failure: the som supervisor run of 2026-11-12 16:54Z ended with "Nothing has changed since the last board ... No new write is needed".
It made no write.
The board faberline/aw#253 stayed at `更新：2026-11-12T15:56Z`.

### Ghost runs (G25)

A ghost run is a workflow run that has all three of these:

- its state is `queued`;
- it has zero jobs (`total_count` of the jobs list is 0);
- it was created more than 24 hours ago (`created_at`, compared with `date -u`).

`gh run cancel` answers "Cannot cancel a workflow run that is completed".
A force-cancel answers 409.
A ghost run uses no job slot.

For each ghost run:

- Do not count it in the queued number (G18, G23).
- Do not report it as a blocked queue or as the oldest unfinished run.
- Do not try to cancel or delete it.
- List its run id once on the status board under `ghost runs` (step 10).
- Do not open an issue for it.

Known examples: defer 43097629607; tape 43098636145 and 43097866510; core 43098026400.

### Post the body from a file (G26)

Write each issue or comment body to a file in this run.
Post it with `gh api ... -F body=@<file>`.
Use a capital `-F`. Lowercase `-f` does not read the file. It posts the path as the text.
`gh issue` and `gh pr` are blocked in this session, so `gh api` is the only way to post.

After each create or comment, read the body back:

- for an issue: `gh api repos/<owner>/<repo>/issues/<n> --jq .body`;
- for a comment: `gh api <comment API url> --jq .body`, using the URL that the create returned.

The body must hold the real text.
It must not be a path, and it must not start with `@`.
When it is wrong, fix it in the same run (PATCH with `-F body=@<file>`).

Example failure: faberline/aw#374 had a body that was only a file path.

### One tracker for CI queue time (G27)

The CI queue of the 20-job organization limit has one tracker: faberline/aw#372, titled `pipeline(org): ci runs queue for the 20-job organization limit`.

For this cause, each supervisor does this:

- Read the last comment of `<target>` on faberline/aw#372.
- When the run id, wall time or longest job changed, add one comment with the new numbers.
  When they did not change, add no comment.
- Do not open a new `pipeline` issue for the same cause.

Open a new issue only for a different cause.
Its title names the cause, and its body says why the queue rule (G23) does not cover it.

Example failure: faberline/aw#377 (kit) opened a second issue for this cause. It was closed into faberline/aw#372.

### One cause, one decision (G32)

A new `blocked` signal can have a different cause from the open decision that you want to add it to.
Do not append it there.
A `decision` issue is about one cause.
Its title, its facts and its `執行清單` name that cause and its issues.
An item about another cause hides in a thread that the desk reads for something else.

Before you add an item to an open `decision(<repo>)` issue (step 6, G20, G31), do this:

1. Read the title and the facts of that open decision.
   Find the issue or the cause that it is about.
2. Compare it with the cause of the new signal.
   Write the cause of the new signal in one line.
3. When the cause is the same, add the item there as a comment with the full `執行清單`.
4. When the cause is different, open a new `decision(<repo>)` issue for it.
   Several signals of this run that share the same new cause go in the one new issue.
   Do not open a second issue for a cause that already has an open decision (step 6, G15).

Another issue of the same repository is not the same cause.
Another repository is not the same cause.
Two issues have the same cause only when the same fact explains both blocked signals.

Example failure: on 2026-11-14 a relay#187 item was appended to faberline/aw#381.
That decision is about relay#30.
relay#187 has another cause and needed its own decision.

### Precedent class: core v0.5.0 leader no-op (G33)

core v0.5.0 makes a new leader append a no-op entry to the log (core `docs/operations/release-notes-0.5.0/raft.md`).
A test that expects the old index values then fails.
This is a known class.
The desk answered it twice: faberline/aw#415 and faberline/aw#418.

When a stage run reports `blocked` on a test failure, apply this class only when all of these are true:

1. The test fails only because the new leader appends a no-op entry.
   The failing assertions are index values (log index, commit index, match index and the like), and each observed value is higher by the no-op.
2. The fix is only to change the expected index values, with a one-line comment that names the no-op.
3. The assertions on engine behavior stay as they are.
   No test is deleted, skipped, ignored or loosened.
   No threshold, timeout or test structure changes.
4. You verified the observed values from the test output of the stage run: read the failed job log (G14) and write each expected value, each observed value and the line that shows it.
   Include the no-op that a new leader writes before the next publish.
   Example failure: the first desk answer on faberline/aw#418 missed it, and the expected values were wrong by one.
   Do not copy the values from the signal text without reading the log.

When all four are true, you may answer and remove `blocked` yourself, in the same pass, under the limits of G21:
re-read the labels and the last signal first (G19); the write is a comment and the removal of `blocked` (and a `stage:*` label when the issue has none, G4); then do the claim check (G4).
The comment gives the expected value for each assertion, the observed values with the log line, the link of the stage run, and the precedent (faberline/aw#415, faberline/aw#418).
No `decision` issue is needed.
Add the write to the status board under `這一輪做了什麼`, with the link.

Anything wider goes to the desk as before (G20): a change of test structure, a threshold, a deleted or skipped test, a change of an engine assertion, or a value that you cannot verify from the log.
When one value of the signal does not match the log, the whole item goes to the desk.
Do not apply this class to a test that fails for another cause, even when it looks like it.

### Pickup age on the board (G34)

A claimable issue can wait for a stage routine and nobody sees how long.
G8 checks the stage as a whole. This rule checks each issue.

For each open issue in `<target>` that has a `stage:*` label and no `blocked` label, do this:

1. Read the time of the last touch: the newest of its last comment, its last label change and its last commit on its claimed branch.
   Do not use `updated_at` alone (G8).
   Get the time now with `date -u` (G1).
2. When it was not touched for more than 2 hours, list it on the board under `## 排隊太久`.
   One line has: the issue link, the hours since the last touch, the stage, and the queue depth of that stage.
   The queue depth is the count of open issues of `<target>` with the same `stage:*` label and no `blocked` label, read in this pass (G19).
3. Say whether the routine of that stage produced anything in the last 2 hours: a pull request, a comment or an `aw:signal` of that stage in `<target>`.
   Write `routine 有產出` or `routine 沒產出`.

The 2 hours are the limit for the board line only.
When the routine of that stage produced nothing for longer than the limit of G50 (two routine intervals of the stage, now 6 hours), or a run of the stage happened after the issue became claimable and did not take it, a board line is not enough.
This is a `pipeline` report to the desk.
Search the open faberline/aw issues with the label `pipeline` for the stage and `<repo>` first (step 6).
When one is open, add one comment with the new numbers.
When none is open, create one `pipeline(<repo>)` issue in the same pass.
The issue has: the stage, the issues in the list with the hours, the queue depth, the time of the last output of the routine, and the claim check of each issue (G4, G6, G30).
Check G8 first: when every issue waits for an open prerequisite, this is not an alarm and not a pipeline issue.
Check G40 too: when the stage is in backpressure, the routine skipped by design. Do not open or update a `pipeline` issue for it.
Leave out an issue whose pull request fails for a cause that an open issue tracks (G50, step 4).

Do not claim issues, do not change labels, and do not run the stage.
Do not use the 2 hours as a reason to rerun anything.

### Pinned workflow (G35)

A caller workflow pins a reusable workflow of faberline/aw with `uses: faberline/aw/.github/workflows/<file>@<ref>`.
Branches can pin different refs.
When a failure comes from such a reusable workflow, read the workflow and its scripts at the ref that the caller pins.
Do not read them at `main`.
Use `gh api -H 'Accept: application/vnd.github.raw' "repos/faberline/aw/contents/<path>?ref=<ref>"`.

Compare two pins: the pin on the failed branch and the pin on the default branch.
When the pins differ, say so in the facts, and name the first commit that the old pin lacks.
Example: tape branches with the old pin `gke-test.yml@bfa0a46` had no Lease queue.
They deleted the shared namespace of another run.
`main` already had the Lease queue, so a read at `main` hid the cause.

### Priority proposal (G36)

A long stage queue can have one issue at its head that blocks many others.
When the queue of a stage is long (G34 names the depth), do this:

1. For each open issue in the queue, count the open issues that wait for it.
   An issue waits when its body has a `Waits for: #<n>` line or a comment says it must wait for the issue.
2. Pick the issue with the highest count.
3. Propose a higher priority for it to the desk in a `decision` issue (step 8).
   Write the count and the link of each issue that waits.
   Name the exact label change.
   Do not change the priority label yourself. The one exception is the `p0` of a G47 advisory issue.

### Claim and release loop (G37)

For the tdd stage, read the effects of the last two runs of the stage.
It is a root cause when one of these is true:

- The same issue was claimed and released without a signal in two runs or more.
- The stage has claimable issues (G30) and no new pull request for 3 hours.

For each run, find the issue that it takes first (claim order) and the reason why it is released.
Read the claim and release comments and the final answer of the run.
Report it as a `pipeline` issue with `urgent` (G15 and step 9 apply).
Example: lumen tdd made no pull request for 7 hours.
Each run claimed lumen 369 first.
Its spec needs lumen 373, which was not merged.
Each run released it and stopped.

Check G8 first: a live wait for an open prerequisite is not this case unless the run also stops with nothing done.

### Waits-for check (G38)

In each run, list the open issues of `<target>` with a `stage:tdd` label whose body has a line that starts with `Waits for:`.
For each one, read the state of each named sibling issue (G24).

- The named issue is open and the issue has no `blocked` label: add `blocked` yourself, in the same pass (G21 exception below).
  Post one comment on the issue that names each open prerequisite, for example: `blocked: waits for #125 (open)`.
  Do not open a `decision` issue for this.
  Do not add `blocked` when the issue has no numbered `Waits for:` line, or when every named issue is closed.
- The named issue is merged and the issue still has `blocked`: remove `blocked` yourself under G21 or G54, in the same pass.
  Only when a check of G21 and of G54 fails, use a `decision` issue of type `中控可決定` (step 8) and write the exact label change.
  This extends G10.

A `Waits for:` text after other text in one line (for example `Part of #161. Waits for: #182`) is the same defect with a form that older `aw` did not read (faberline/aw#601; since aw 0.7.50 `aw claim` reads it after a sentence end). When this is the only defect, the named issue is open and in the same repository, and the issue has a `stage:tdd` or `stage:spec` label: edit the issue body yourself so that `Waits for: #<number>` is a line of its own (the text before it stays on its own line), add `blocked` with the usual comment, and write it on the board. Open no `decision` issue for this case. If the named issue is in another repository, or the line has no number, the old rules stay.

G38 exception to G21: this is the only case where the supervisor adds `blocked`.
It applies only to an issue with a `stage:tdd` label and a `Waits for:` line with numbered prerequisites that are open (G24).
All other uses of adding `blocked` stay with the desk.
The routine prompt must list this label write; without it, ask the desk with a `decision` issue (step 8).
`aw claim` also passes over such an issue without a label, so the label is for the planner and the status board.

Since aw 0.7.66 `aw report split` writes the numbers and `blocked` for the children of one split itself, and refuses a split with a title that names no child. So a `Waits for:` line with no number on a child that has the `aw:child` mark is no longer a normal case: report it as a spec gap and name the aw version of the run.
A `Waits for:` line with no issue number (only a title or text) is a spec gap.
Find the issue by title in `<target>` and say so in the facts, and report it as a spec gap on the status board.
The spec skill must write the issue number (`aw-worker`, spec step).
Do not change the label for this line.
Since aw 0.7.67 `aw report` does this itself when the first line of a spec or tdd signal is `Waits for: #N`: it sets the `Waits for:` line of the body to the numbers of the signal, and adds `blocked` and one comment `blocked: waits for #N (open)` while the named issue is open (a closed issue gets no `blocked`).
So for an issue whose body line is old or missing and whose latest aw signal has `Waits for: #N` with numbers of the same repository, and the named issue is open (G24), and the signal is newer than the body: do the same yourself, in the same pass: edit the body line, add `blocked` and post the comment, and write it on the board. Open no `decision` issue. This extends the G38 exception to this case only.
Every other case below stays with the desk: a prerequisite in another repository, a line with no number, a signal that is older than the body, or a signal and a body that name different open issues.
When the body `Waits for:` line has no number but the latest aw signal on the issue has `Waits for: #N` (as in aw#498), fix the body line and add `blocked` yourself under G54 when its checks pass; otherwise open one `中控可決定` decision (step 8) that asks the desk to do it.
Put the issues of one repository in one `decision` issue (G32).

### An old pin is not a pipeline cause (G39)

This rule adds to G35. A product repository can call a reusable workflow of another repository.
The call is `uses: owner/repo/.github/workflows/x.yml@<ref>`.
The file on the default branch of the source repository can differ from the file at `<ref>`.
A fix on the default branch does not reach a caller that pins an old ref.

When a job of a reusable workflow fails or is cancelled, do this:

1. Read the workflow file at the ref that the caller pins, not at the default branch.
   Read the `uses:` line in the caller workflow of `<target>` to get the ref.
   When the caller has an `aw-ref` input, read that ref too. It selects the aw files that the job uses.
2. Compare the pinned ref with the default branch of the source repository.
   Find a fix there for the symptom.
3. When the default branch has a fix for the symptom, the cause is `old pin`.
   Open an issue in the product repository to bump the pin to a ref that has the fix.
   Search the open issues of `<target>` for the same bump first (G32). When one is open, add one comment with the new run links.
4. Do not file a `pipeline` issue in faberline/aw with "cause not confirmed" for an `old pin`.
   Do not read only aw `main` and then say the cause is not confirmed (G35).

Signature of the aw#293 class:
- Two GKE jobs of different runs are cancelled in the same second.
- The jobs have no steps, and their logs return 404.
- The pinned `gke-test.yml` has a `concurrency` group.

The fix of this class is the Lease queue (aw#297, in `efe1a96`). Compare the pinned ref with it.
When the pin is older than the fix, the cause is `old pin`.

Evidence: faberline/aw#437 was filed as "cause not confirmed" because the supervisor read `gke-test.yml` on aw `main`.
The tape `ci.yml` pinned `gke-test.yml@bfa0a46`. That ref still has the `concurrency` group. See faberline/tape#138.

When the pinned file already has the fix, this rule does not apply. Go on with the normal cause search.

### Backpressure is not a stopped routine (G40)

A tdd run or a spec run skips a repository on purpose when the stages below it are stuck.
A skipped run writes no signal on GitHub.
So a healthy routine looks silent.
The rules are in `aw-worker`, section "Backpressure: count downstream before the first claim".
G8 and G34 call this rule before they report a `tdd` or `spec` stage as silent.

#### Count (before you report silence)

Count with REST, one list call for each label.
Use the same query as `aw-worker`: open issues of `<target>` that are not pull requests and do not have the label `blocked`.

- `review_waiting`: issues with `stage:review`.
- `tdd_waiting`: issues with `stage:tdd`.

Compare the counts with the limits in `aw-worker`: `BACKPRESSURE_REVIEW_LIMIT` and `BACKPRESSURE_TDD_LIMIT`.
Do not copy the numbers into your text. Read them from `aw-worker` in this pass, because they can change.

- The `tdd` stage is in backpressure when `review_waiting` is at or above `BACKPRESSURE_REVIEW_LIMIT`.
- The `spec` stage is in backpressure when `review_waiting` is at or above `BACKPRESSURE_REVIEW_LIMIT`, or `tdd_waiting` is at or above `BACKPRESSURE_TDD_LIMIT`.
- The `review` stage has no backpressure rule. G8 applies to it as before.

#### What you do for a stage in backpressure

1. Do not open a "routine silent" issue. Do not add a comment to one.
2. Write one line on the status board, for example `backpressure: review waiting 5, tdd and spec skip by design`.
3. Go on with the other checks. A stage in backpressure is not an alarm in step 9.

A stage that is not in backpressure keeps G8 and G34 as they are.

#### The real signal: the review stage is the bottleneck

Backpressure is normal for a short time. It is a problem when it does not end.
Report the review stage as the bottleneck when both are true:

- the stage is in backpressure, and
- the newest `aw:signal` of the `review` stage with outcome `passed` or `failed` in `<target>` is older than 6 hours (time from `date -u`, G1).

Then do this, in one issue for each repository:

1. Search the open faberline/aw issues with the label `pipeline` for `<repo>` and "review bottleneck" first (step 6, G15, G32).
2. When one is open, add one comment with the new facts. Do not open a second issue.
3. When none is open, open one `pipeline(<repo>)` issue "review is the bottleneck".
4. The facts: the pull requests that wait for review, how long each waits (hours), and the reason for each. Read the reason from the evidence, not from a guess (G14):
   - behind `main` (needs an update),
   - CI queued or not started (G18, G23),
   - repeated failed reviews (G12).
   Add `review_waiting`, `tdd_waiting` and the time of the last `passed` or `failed` signal.
5. This issue is urgent (step 9) only when the repository is on the release chain (G11) or has `p0` or `p1`.

Do not propose to disable or pause a tdd or spec routine. The rule is that upstream does no new work while downstream is stuck.

#### The same cause in several repositories

When the same cause is likely in more than one repository (for example one CI queue or one runner problem), do not open your own issue.
Search the open `pipeline` issues of faberline/aw for the other repositories first.
Read the state of the issue you find (G24). When it is open, link it in a comment and on your board line. Do not open a duplicate (G15, G32).

### Review with no pull request goes back to tdd (G41)

The claim of a review run posts a `blocked` signal when no open pull request closes the issue: "No open pull request closes this issue." The issue stays in `stage:review`. No review run takes it again, and no tdd run takes it.

For each open issue in `<target>` with `stage:review` and without `blocked` (labels from this pass, G19):

1. Read its comments. The last aw signal is from the `review` stage, outcome `blocked`, and its text says that no open pull request closes the issue.
2. Search the open pull requests of `<target>` for one that closes the issue (`Closes #<n>`, or a head branch for the issue). When one exists, do nothing: the review run takes it.
3. Otherwise, in the same run, remove the label `stage:review` and add the label `stage:tdd`. A tdd run starts the work again from the default branch.
4. Write one line on the status board under `這一輪做了什麼`, with the link.

Do not change any other label. Do not use this rule for an issue whose last review signal has another text. Those issues follow G10 and G20.

### A sent-back issue that nobody takes (G42)

A stage in rework-only mode (G40) takes only sent-back work, and `expedite` issues. A sent-back issue is a `stage:tdd` issue with an open pull request or a review `failed` signal as its newest, and a `stage:spec` issue with a tdd `respec` signal as its newest.

For each such issue in a stage that G40 counts as in backpressure: when no `aw:claim` or `aw:signal` comment, commit or label change on it is newer than 2 hours (time from `date -u`, G1), the rework-only claim does not reach it. Report it in one `pipeline(<repo>)` issue "sent-back issue not taken": the issue numbers, the age of the last touch in hours, and the newest error text of any claim comment on it (a claim fault such as faberline/aw#542 leaves a `blocked` signal from the claim itself). Search first (step 6, G15, G32, G43). When one is open, add a comment with the new facts.

Do not report an issue that has a live claim (G37) or the label `blocked`.

Silent end after a rework (faberline/aw#630). Also report a `stage:tdd` issue whose newest signal is a review `failed`, whose open pull request has a head commit newer than that signal, whose checks on that head are all green, and that has no `aw:claim` or `aw:signal` comment newer than 60 minutes (the rework pushed a head and did not report `ready`). The report names the issue, the head sha, the time of the review `failed` signal and the age in minutes. Read the issue comments for a note "released the claim without a signal": quote its time. This check needs no label change and no new permission: the supervisor only reports. The desk or the next tdd run decides the move.

### Search for the same failure before you open an issue (G43)

Before you open any issue for a failure (red `main`, failed job, failed test), search the open issues of `<target>` for the same run id and for the same failing test name or the same error text. Use REST (`search/issues` with `repo:<target> is:issue is:open`, or the list call and a filter).

- When an open issue names the same run id or the same failing test, add a comment with the new run link there. Do not open a second issue.
- Search the issues closed in the last 7 days too (step 6), and the issues that this run already created. A closed issue for the same run id is the answer: do not open the issue again (evidence: faberline/core#618 and faberline/core#620 were two issues for one failed run).
- When none does, open the issue, and put the run id and the failing test name in the title or the first lines of the body, so the next search finds it.

This check comes before G28. G28 labels only an issue that this run created.

### An ownerless flake (G44)

A flaky test can fail review again and again while no one owns it (evidence: faberline/lumen#121 was `blocked` since 2026-11-10 with a stale pull request, and its flake kept faberline/lumen#17 in a review loop).

For each open issue in `<target>` that tracks a flaky or intermittent test, do this when one of these is true: the issue has `blocked`, or it has no `stage:*` label, or its pull request is stale (no commit and no aw signal for 2 days, time from `date -u`, G1). And the flake has failed a review or a `main` run since then (read the `failed` review signals and the `main` runs; name the run).

1. Do not only comment.
2. Search for a split issue (step 6, G15, G43). When one is open, add the new run link there and stop.
3. Otherwise open one decision issue (step 8, G3) with one option: split the failing test into a new issue with the labels `stage:spec`, `p1` and `expedite`. The type is `類型：中控可決定`. The `執行清單` creates that issue, and links the old one. G21 does not cover it (it is not label-only). G54 covers it when its checks pass: then make the issue yourself with the labels that G54 allows, and open no `decision` issue. Add `p1` and `expedite` only when the three conditions below are true. Otherwise the desk executes it.

A review `failed` signal with a line `flake: <test> outside the diff, no tracking issue` (aw-worker, review) is the same case: search the open issues for the test name, and when none tracks it, open the tracking issue (G43 first) with the run id and the test name in the title.

**Labels for the issue this run opens (faberline/aw#567).** No routine claims an issue without labels. Add the labels `stage:spec`, `agent:claude`, `p1` and `expedite` to the tracking issue that this run creates, when all of these are true:

1. The same test failed with the same error text in two or more runs (name the run ids).
2. At least one of those runs is on a pull request that does not touch the test.
3. No open issue covers the test (the search above found none).

The issue body gives the run ids. Label only the issue that this run created. Do not rerun more than once, close issues or change other labels.

**Two classes of failure on `main`.** The wait for 2 runs above is only for the infrastructure class. Decide the class of each red `main` run before you wait, and write it on the board in one line: the class, the failing test or step, and the reason (for example `product: lock error from src, not a runner problem`).

- **Infrastructure class:** runner lost, network, registry, a timeout with no test name, a cancelled job. Keep the rule above: wait for the second run, and rerun once (step 5).
- **Product class:** one failed run opens the issue at once, when the failing step names a test and the panic, the assertion or the error text comes from product code or product state: a lock or file that is not released, data loss, a wrong result, a panic in `src`. A flaky timing guess is not a reason to wait. Evidence: faberline/sift run 44444329144 failed once with "lock Sift data root ...; another Sift process may be using it ... os error 11" in `bounded_ingest_burst_survives_drain_and_journal_reopen`, and it pointed at a lock that drain did not release.
  1. Search first (G43): an open issue with the same test name gets the run link as a comment, and no second issue.
  2. Otherwise open the issue as G28 says (labels under G28). The title or first lines have the run id and the test name. The body asks for the cause first, and says that a retry, an ignore, a skip or a longer timeout is not the fix.
  3. When you cannot tell the class, treat it as infrastructure and say on the board that you could not tell.

### A threshold is not a desk decision (G45)

An option that widens or lowers a perf threshold, a baseline, a coverage ratchet or a timeout is never `類型：中控可決定`. It is `類型：需要使用者` (a user decision). The desk cannot weaken a gate (evidence: faberline/aw#559).

1. The decision must have one option that keeps the threshold. Prefer a more stable measurement, for example the median of 5 runs (core pull request 460).
2. The option that keeps the threshold goes first, as the recommendation, unless a measurement shows that it cannot work. G9 applies to every number.
3. Never use G21 for an option of this kind, also when it has one option.

### A green pull request that is behind `main` waits for the review routine (G46)

A pull request in review whose required checks on the current head are green and that is `behind` `main` for more than 2 hours does not wait for CI. CI has finished. It waits for the review routine, which must update its branch (`aw` does this after a merge and when the queue is idle; faberline/aw#563).

1. Read the state with REST: `mergeable_state` `behind` and the check runs of the head (G14). Take the time from the newest of the head commit time and the time of the last `main` commit that the branch lacks (time from `date -u`, G1).
2. Write it on the board as `waits for the review routine (<hours> h behind main, CI green)`. Do not write `waits for CI` and do not count it in the queued number (G18, G23).
3. When the repository has no review claim or signal for 2 hours, check G40 and the routine state first. Do not guess the cause.

### A new RustSec advisory (G47)

A new RustSec advisory can fail a required check in several repositories at once (evidence, 2026-11-17: RUSTSEC-2026-0543, chrono unmaintained, in tape, relay and defer; each supervisor opened a `decision` issue, and the desk gave the same answer three times: faberline/aw#572, #573, #574).

This is the cause "new advisory" when all of these are true:

1. A required check failed, and its log names a RustSec advisory ID (`RUSTSEC-<year>-<number>`). One failed run is enough, also when `main` is not red yet.
2. The lockfile did not change since the last green run of the same job (compare `Cargo.lock` at the two commits, G14).

Then do this in the same pass. Open no `decision` issue.

1. Search the open issues of `<target>` for the advisory ID (G43). When one exists, reuse it. Otherwise open one issue. Its body has: the advisory ID, the run id, the job, the crate and the dependency path, and these fix rules: add only this advisory ID to the ignore lists that the repository already uses, with a reason and an expiry date in the form of the existing entries; ignore no other advisory; change no test, threshold or required check.
2. An advisory that is a vulnerability with a safe upgrade is not ignored. The issue asks for the upgrade and not for an ignore entry.
3. Add the labels `stage:spec`, `agent:claude`, `p0` and `expedite` to the issue that this run created. When the issue already has a `stage:*` label, add only `expedite`, and change `p1`, `p2` or `p3` to `p0`.
   The priority is `p0` because the advisory fails a required check of every pull request: no pull request of `<target>` can merge until the fix merges. `expedite` alone is not enough: the claim order puts the issue behind each older `expedite` issue (evidence, 2026-11-23: RUSTSEC-2026-0594. The fix issue faberline/sift#384 had `p1` and `expedite`. It waited behind ten older `expedite` issues for two spec runs, and no pull request could merge: faberline/aw#726).
   This is the one case where you change the priority label of an existing issue (G36). Do it only on the issue whose title or body names the advisory ID. Write the old label and the new label on the board.
4. Read the labels of the issue again in this pass, just before you write "no labels" in a board or a decision. A pass that wrote "no labels" from an old read was wrong in aw#572 and aw#573.

Do not close issues, merge or change other labels. Do not use this rule for a failure that has no advisory ID (use G28 or G44), except after the lock file sweep below.

#### A failed audit job: read the lock files first

A failed `cargo deny` or `cargo audit` job is first a possible advisory. It is not a flake and not an infrastructure fault until the lock files say so (evidence, 2026-11-22: faberline/aw#716. The som supervisor and two review runs wrote `flake` and `Job starts no pod` for the job `test/audit`. The cause was RUSTSEC-2026-0592 in `som-service/Cargo.lock`. Eight repositories had an issue for the same advisory on the same day).

Use this part when the failed job is the audit or supply-chain job (`cargo deny`, `cargo audit`, a job or step with `audit`, `deny`, `advisories` or `supply` in its name), also when its log is lost, is empty or names no advisory ID. Do it before you write `flake`, `infrastructure`, `no pod` or `cause not confirmed`, and before you open a `pipeline` issue:

1. Search the organization for issues of the last 7 days that name a RustSec ID: `gh api -X GET search/issues -f q='org:faberline RUSTSEC in:title,body created:>=<date 7 days ago>'` (date from `date -u`, G1). When the search does not answer for a repository that this session cannot read, read the open faberline/aw issues and the `status(<repo>)` boards for `RUSTSEC`, and say which repositories you could not search.
2. For each advisory ID that you find, read the affected crate and the affected versions from the issue or from the advisory.
3. Search every `Cargo.lock` of `<target>`, not only the one at the root: `git ls-files '*Cargo.lock'` in the checkout, or the tree of the head commit with `gh api`. In each lock file, read the locked version of the affected crate. Write each lock file path and the version that you read.
4. When a lock file has an affected version, the cause is the advisory. Follow steps 1 to 4 of G47 above: open or reuse the fix issue with the labels `stage:spec`, `agent:claude`, `p0` and `expedite`, and name the lock file path in it. Open no `pipeline` issue and no `decision` issue for this failure. When a `pipeline` issue for it is open already, add one comment there with the advisory ID, the lock file and the link of the fix issue.
5. Only when no lock file has an affected version, go on with G14, G28 or G44.

A green `main` is not proof that the lock file is clean. A `main` whose last run of the audit job is older than the publication of a known advisory did not check that advisory. Compare the two times before you cite the green run. Condition 2 of this rule ("the lockfile did not change") is about every `Cargo.lock` of the repository, not only the root one.

A review `failed` signal with a line `flake: <job> outside the diff` that names the audit job is this case too: do the sweep before you follow G44.

### Many branch updates and no merge (G48)

The review stage must update one branch of a repository at a time. A review that posts more than one `branch updated` comment for one repository within 30 minutes, with no merge after them, is a defect. It is not progress (evidence, 2026-11-17: seven updates in seven minutes in lumen; the board said "review is working"; faberline/aw#587).

1. Read the `branch updated` signals of the repository (`gh api` on the issue comments of the `stage:review` issues, or the comment list of the repository since 30 minutes ago). Count the ones in any 30 minutes window.
2. When the count is above one, and no pull request of the repository merged after the first of them, open one `pipeline` issue, or reuse the open one (G43). Write the issue numbers and the time of each `branch updated` comment, and the merge times (or "no merge").
3. In the board or the decision, do not write "review is working" or "not the bottleneck" for this repository. Write the issue number of the `pipeline` issue.

A single `branch updated` per 30 minutes is normal. Do not close issues, merge or change labels.

### A repository pins the release before a new core release (G49)

CR3 of the core-reuse check (`docs/standards/core-reuse.md`) gives a repository 7 days to move to a new `core` release. A repository that pins the release before the current one, while the current one is younger than 7 days, passes with a `warn CR3` line. After the 7 days it fails. A failing `core-reuse check` on `main` for this reason is not a red `main` with an unknown cause.

1. Find the current `core` release (the highest `vX.Y.Z` tag, not an `-rc`) and its date. Find the repositories whose `Cargo.toml` pins the release before it (the `warn CR3` line, or the pin in the file).
2. For each such repository, open one issue, or reuse the open one (G43): title "re-pin core to <tag>", labels `stage:spec`, `agent:claude`, `p1`. Write the pin, the current release, and the date from which CR3 fails.
3. In the board, write the issue number. Do not write "red main, cause unknown" for this failure.

If the failure is on `main` after the 7 days, the same issue is the one to use (G28 order: expedite is added there). Do not edit `aw.toml` exceptions.

### A stage with claimable issues and no signal (G50)

A stage that does no work is a defect, even when each run ends without an error (evidence, 2026-11-17: the spec routine of core stopped for more than two hours on one unreadable dependency; a tdd issue that the desk sent back waited for two hours; faberline/aw#596).

1. For each repository and each stage (`spec`, `tdd`, `review`), find the open issues with that stage label that can be claimed: no `blocked`, and for a `--rework-only` run only rework (open pull request, tdd `respec` or review `failed` as the last signal, or a newer `desk:` comment) or `expedite`.
2. Find the time of the last signal of that stage in the repository.
3. Report the stage only when at least one issue can be claimed and one of these is true:
   - The last signal of the stage is older than two routine intervals of that stage. A routine now runs one time in 3 hours, so the limit is 6 hours. A stage with a second routine has a shorter interval; use it when you know it.
   - A run of that stage happened after the issue became claimable and did not take it. Name the run: its claim or signal comment on another issue, or its `backpressure:` or `no-work` line, with the time. The issue became claimable at the newest of: its `stage:*` label change, the removal of `blocked`, and the signal that sent it back.

   Then open one `pipeline` issue, or reuse the open one (G43). Write the issue numbers, the time of the last signal, the time each issue became claimable, and which of the two conditions is true. Do not write "waits for the routine".

   A signal that is younger than the limit, with no run after the issue became claimable, is the normal wait for the next run. Write it on the board (G34) and open nothing. The old limit of 2 hours was shorter than the 3-hour schedule, so a pass reported a routine that was on time (evidence, 2026-11-22: faberline/aw#717 reported the tdd stage of som after 2.5 hours; the routine was not late).
4. Do not report an issue whose pull request fails for a cause that an open issue tracks. Read the failed checks of the head of its pull request (G14) and search the open issues of `<target>` and of faberline/aw for that cause (G43). When an open issue tracks it, the issue waits for that fix, not for the routine: write `waits for <link>` on the board line of the issue, and leave it out of the `pipeline` issue (evidence: som#327 in faberline/aw#717 waited for the fault of faberline/aw#716; a tdd claim could not make its checks pass).
5. Also name each issue whose label is `stage:tdd` or `stage:spec` while its last signal is `ready` of the same stage and no `desk:` comment follows it: no run takes it.

Do not change labels. Do not touch the issues.

### A paid run that exists (G55)

A `decision` that asks the owner for a paid run costs the owner a question, and it can cost money a second time. Before you open one, look for a run that exists (evidence, 2026-11-22: faberline/aw#715 for sift#214. The spec run and the supervisor wrote `no approval on record`. The acceptance workflow had two passed runs two days earlier, and the approval ledger in the `status(desk)` issue faberline/aw#713 listed the approval as used).

Do these three reads in this pass, before the `decision`:

1. List the runs of the acceptance workflow: `gh api -X GET "repos/<target>/actions/workflows/<file>/runs" -f per_page=20`. Find the file name in `.github/workflows/` of `<target>` (for example `gke-acceptance.yml`); when the issue names a workflow, use that one. Write each run: id, date, commit, conclusion.
2. Read the approval ledger in the body of the faberline/aw issue with the title `status(desk)` (now faberline/aw#713). Find the lines for `<repo>`: each approval, its cap in TWD, and `used` or `unused` with the run.
3. When a passed run exists, compare its evidence with the acceptance points of the issue first, one point at a time: the job log (G14), the job summary and the evidence files that the run committed or printed. For each point write `proved by run <id>: <line>` or `not proved: <what is missing>`. An artifact that you cannot download is `not read`, not `not proved`: name the file that the desk must read (step 9).

Then:

- Every point is proved: open no `decision` for a run. Write the evidence in one comment on the issue. The issue then moves as its stage rules say; when only a close as completed is left, that goes to the desk (G54).
- Some points are not proved: ask only for what is not proved. Write the measured cost of the earlier run from the ledger (G9), or `not measured` and where the number will come from.
- The ledger has an unused approval that covers the run: do not ask the owner again. The request goes to the desk as `中控可決定`; the desk starts the run. You never start a paid run.
- No run exists and the ledger has no line: write that you read both, with the command and the time (G1). Then open the `decision` of type `需要使用者`.

Do not write `no approval on record` or `no run exists` before you did reads 1 and 2. This rule adds no write.

### An unreleased build or a run that exists meets the test (G56)

A `decision` that asks the owner for a release tag or a paid run, only so that a test can run, costs the owner a question that the desk can answer (evidence, 2026-11-23: faberline/aw#723 for defer#110. The supervisor typed the item `需要使用者` and asked for a second released tag. The desk decided it: the upgrade test uses the released version as N and the candidate image of `main` as N+1).

Before a `decision` asks for a release tag or a paid run as a precondition of a test, do these three reads in this pass:

1. Read the acceptance criteria of the issue, word for word. For each one, write what it needs: a version, an image or a run.
2. Look for an unreleased build. List the runs of the release-candidate workflow on `main`: `gh api -X GET "repos/<target>/actions/workflows/<file>/runs" -f branch=main -f per_page=20`. Find the file name in `.github/workflows/` of `<target>`. For the newest passed run, write its id, its commit and its image digest.
3. Look for a run that exists (G55, reads 1 and 3).

Then:

- An unreleased build or a run that exists meets each criterion as written: the type is `類型：中控可決定`, not `類型：需要使用者`. The `執行清單` says which build has which role, for example `N = the released tag <tag>; N+1 = the candidate image <digest> of main, run <id>`. It asks for no tag. Write under the facts: `no new tag is necessary: <criterion> is met by <build or run>`.
- The test needs a paid run and no run exists: G55 gives the type of that part (the approval ledger). Ask only for the run, not for a tag.
- A criterion cannot be met without a released tag: the item stays `類型：需要使用者`. Quote the criterion, and write each build and run that you read and why it does not meet the criterion.

Do not write "needs a released version" or "needs a second tag" before you did reads 1 and 2.
This rule adds no write. You never create a tag and you never start a paid run.

### A choice of direction that keeps the acceptance (G57)

A spec run can report `blocked` with two directions. That is not always a decision for the owner (evidence, 2026-11-23: faberline/aw#725. Two spec runs of sift reported `blocked` with a choice of direction, and the supervisor typed both items `需要使用者`. For sift#374 the recommended option kept the acceptance and needed only a new issue in faberline/core).

The item is `類型：中控可決定` when the recommended option (the first one, G3) passes all three checks:

1. It keeps the acceptance criteria of the issue as written.
2. It starts no paid run, and it needs no tag.
3. Outside `<target>`, it needs only a new issue in another Faberline cloud repository. The desk opens that issue. You do not write in another repository (G54).

The item stays `類型：需要使用者` when the recommended option does one of these:

- It makes the acceptance smaller: it removes or weakens a requirement or an acceptance criterion.
- It moves user data, or changes the path where user data is stored.
- It changes a public interface in a way that breaks a caller.

For each option, write one line that starts with `資料或驗收：`. The line says `不變`, or it says which data or which acceptance criterion the option changes. The desk reads this line to see why the item has its type. Do not leave the line out for the option that you do not recommend.

A `中控可決定` item of this rule goes to the desk as a `decision` issue when its `執行清單` has a write outside `<target>` (G54, check 3). The `執行清單` names the repository of the new issue, its title and the facts for its body, and says `the desk opens it`.
When the recommended option needs no write outside `<target>`, test the item against G54 and execute it when its checks pass.
When one `decision` issue has items of both types, each item says its own type (G20).
This rule adds no write.

### A tool refusal is not a decision for a person (G58)

A tdd run or a spec run can report `blocked` because a tool refused one command, or failed one time. No person must decide anything, but the label `blocked` stops every routine (evidence, 2026-11-23: faberline/aw#728. The tdd run of tape#232 could not push its rework: the push was refused one time, and no force push was tried. The issue, pull request 241 and three issues that wait for it stayed blocked).

Use this rule for an open issue of `<target>` with `blocked` when all of these are true:

1. Its last aw signal has `stage=tdd` or `stage=spec` and `outcome=blocked`.
2. The only cause in the signal text is a tool refusal or a tool error that can pass: a `git push` or a `gh` call that was refused or failed, a network error, a timeout, an HTTP 5xx answer, or a rate limit. The signal asks no question and gives no options.
3. The refused command was not a force push, a delete, or a write that a ruleset rejects. Such a refusal is a guard that works. It follows G20.

Then count the refusals on this issue. Read its comments for an earlier `blocked` signal with a tool refusal as its only cause, and for a comment that starts with `supervisor: tool refusal`.

- The first time: remove `blocked` one time yourself, in the same pass. Read the labels again first (G19). Post one comment that starts with `supervisor: tool refusal, first time`. It tells the next run what to do again: the branch and its head on GitHub (read it in this pass), the work that did not reach GitHub (for example `the local commit <sha> was not pushed; make the change again from the head of the branch`), and the review request or the signal that the work answers. After the write, do the claim check (G4). Write one line on the board under `這一輪做了什麼`, with the link.
- The second time on the same issue: do not remove `blocked`. Open one `pipeline` issue, or reuse the open one (G43). Quote the exact refusal text and the exact command of each of the two signals, with the links and the times. Say what the refusal blocks. Open no `decision` issue.

A lost local commit is not a reason to wait: the next run makes the change again. Do not ask a person to push.
The removal of `blocked` is a G21 label write. The routine prompt must list the G21 label writes for it; without them, G58 does not run, and the item goes in a `pipeline` issue. This rule adds no other write.

### A test that a reference test replaces (G59)

To delete or weaken a test so that a check passes is never a desk decision (G45, G54). To replace a test is a different case, and the desk can decide it (evidence, 2026-11-23: faberline/aw#729 for kit#302 and kit#303. The accepted spec changed `filtfilt` to match the reference implementation. The old test held values of the old behaviour. The supervisor wrote `我沒有建議` and typed the item `需要使用者`).

The item is `類型：中控可決定` when you can show all three facts by a read in this pass:

1. The accepted spec of the issue changes a behaviour so that it matches a reference implementation, and the spec names that implementation.
2. The old test holds values of the old behaviour. Name the test, its file, and each row or value.
3. New reference tests exist, or the spec says that the tdd run adds them: a fixture that the reference implementation made, and the test that reads it. Name the files.

Then write the `decision` item with the option "replace the old test" first, as the recommendation. Do not write `我沒有建議`. The item lists these three conditions, and the answer comment of its `執行清單` repeats them for the tdd run:

- (a) The new reference fixture has a row for the same input as each deleted row. When it has none, the tdd run adds the row first, and deletes the old row after that.
- (b) The text of the pull request maps each deleted test and each deleted row to the new test and row.
- (c) The change has no wider tolerance, no `#[ignore]` and no exclusion.

For each fact and for condition (a), write `shown: <file and line>` or `not shown: <what is missing>`.
The item stays `類型：需要使用者` when a fact is `not shown`, when the spec names no reference implementation, or when the option needs a wider tolerance, an ignored test or an exclusion (G45).

This is a class of decision for the desk. It is not a supervisor action: do not execute it under G21 or G54, also when it has one option. The desk approves it. A review run then checks conditions (a), (b) and (c) on the pull request.
This rule adds no write.

### The change is already on the default branch (G60)

A review run can close a pull request because the default branch already has the change. The issue then goes back to tdd, the tdd run answers `respec`, and the spec run has nothing to write: the issue moves in a loop (evidence, 2026-11-23: faberline/lumen#579. The review closed pull request 584 because `main` already had the change. The issue went back to `stage:tdd`, and the tdd run answered `respec`).

Use this rule for an open issue of `<target>` when its last aw signal is one of these:

- a review signal with the outcome `closed`, or a closed pull request of the issue, with the reason that the default branch already has the change;
- a tdd `respec` signal that says that every acceptance criterion already holds on the default branch.

Do not send the issue back to spec. Check the criteria on the default branch yourself, in this pass:

1. Read the head commit of the default branch, and the result of its required checks (G14, G28).
2. For each acceptance criterion of the issue, word for word, read the code, the test or the document at that commit. Write `holds on main: <file and line, or test name>` or `does not hold: <what is missing>`. A criterion that needs a run is proved by a passed run on that commit: name the run and the job. A criterion that you cannot read is `not read`.
3. Find the commit or the pull request that brought the change to the default branch, and write its link.

Then:

- Every criterion holds, and the required checks of the head commit passed: close the issue as done. Post one comment that starts with `supervisor: already on main`, with the commit of step 1, the list of step 2 and the link of step 3. Change the `stage:*` label to `stage:done`, remove `blocked` when the issue has it, and close the issue as completed (`state=closed`, `state_reason=completed`). Read the issue back (G26). Write one line on the board under `這一輪做了什麼`, with the link.
- A criterion does not hold: the signal was wrong. Open no close. Post one comment with the list of step 2, and send the issue to `stage:tdd` with a `中控可決定` item that names the criteria that do not hold (G20, G54).
- A criterion is `not read`, or the head commit of the default branch is red: do not close. Open one `decision` item of type `中控可決定` with the list of step 2. The desk closes the issue.

Before the close, read the labels and the last signal again (G19). When a newer signal, a new open pull request of the issue or a `desk:` comment is there, stop.
Do not use this rule for an issue with open child issues, or for a parent whose children did the work (G13 stays with the desk).
The routine prompt must list the G60 writes; without them, G60 does not run: open the `中控可決定` item with the evidence, and the desk closes the issue. This close, the close of G21 and the closes of G54 are the only closes that the supervisor may do.

### A dated ignore needs an upgrade issue (G61)

An ignore entry with an expiry date stops a failed audit check for a short time. It does not remove the advisory from the product. When no issue tracks the upgrade, the ignore expires and the check fails again in every pull request (evidence, 2026-11-23: faberline/core#641 was closed with a dated ignore for RUSTSEC-2026-0600. No issue tracked the upgrade until the tape supervisor escalated it in faberline/aw#737. Then faberline/core#648 was created with `p1`, not `p0`).

Use this rule in each full run for each advisory issue of `<target>` that closed in the last 7 days (an issue whose title or body names a `RUSTSEC-<year>-<number>` ID). Read the pull request that closed it (G14). The rule applies when both are true:

1. The pull request added or kept an ignore entry for the advisory ID with an expiry date (`deny.toml`, `audit.toml` or another ignore list of the repository).
2. A fixed version exists: the advisory names a patched version, or the audit log names an upgrade (`Solution: Upgrade to ...`). Write the crate, the locked version and the fixed version.

Then do this in the same pass:

1. Search the open issues of `<target>` for the advisory ID and for the crate name (G43). Reuse an open upgrade issue. Otherwise open one issue. Its body has: the advisory ID, the crate, the locked version and the fixed version, the ignore entry to remove (file, line and expiry date), the link of the closed issue, and these fix rules: upgrade the crate to the fixed version or later; remove the ignore entry in the same pull request; add no other ignore entry; change no test, threshold or required check.
2. Add the labels `stage:spec`, `agent:claude`, `p0` and `expedite` to it, as G47 step 3 does. When the issue already has a `stage:*` label, add only `expedite`, and change `p1`, `p2` or `p3` to `p0`. The priority is `p0` because the ignore expires on a known date: then the audit check fails for every pull request again, as in G47.
3. Find each open issue that waits for this upgrade: an issue whose body, `Waits for:` line or last signal names the advisory ID or the closed advisory issue. In `<target>`, post one comment on each: `Waits for: <link of the upgrade issue>`. Do not post it two times.
4. For a waiting issue in another repository, the routine prompt gives no write there. The supervisor of that repository posts the link: in its own pass, when an open issue of its `<target>` waits for an advisory fix in another repository, it searches the organization for the upgrade issue (`gh api -X GET search/issues -f q='org:faberline <advisory ID> in:title,body state:open'`) and posts the comment of item 3 one time. Write each waiting issue that you found in another repository on the board, with its link.
5. Write one line on the board under `這一輪做了什麼`, with the link of the upgrade issue and the expiry date of the ignore.

Do not use this rule when no fixed version exists. Then the ignore is the fix: write `no fixed version: <advisory ID>, ignore expires <date>` on the board.
The label writes are the G47 label writes. The routine prompt must list them for this rule (the words "or for the upgrade that a dated ignore entry waits for (G61)" in the G47 sentence); without them, G61 opens the issue with no labels and sends the label change to the desk as a `中控可決定` item.

### A recommended option that keeps everything is the desk's decision (G62)

A `decision` item can have a recommended option that keeps every acceptance point and costs nothing. The owner does not have to answer it, also when the supervisor cannot execute the option itself (evidence, 2026-11-23: faberline/aw#738 was typed `需要使用者`. Its recommended option kept every acceptance point and cost no money).

The item is `類型：中控可決定` when the recommended option (the first one, G3) passes all four checks:

1. It keeps every acceptance criterion of the issue as written.
2. It costs no money: it starts no paid run, no new node pool, no quota and no other paid resource.
3. It makes no release tag.
4. It does not change the product direction: no new scope, no removed scope, no frozen contract, no change to user data or a public interface (G57).

This is true also when the option needs a write that the routine prompt does not give you, or a write in another repository. Then the desk executes it: the `執行清單` names each write, and you do not execute it. When the option needs only writes that G54 allows, test it against G54 and execute it when its checks pass.
This is the same class as G57. The item stays `類型：需要使用者` only for a case of step 8 (money, an action that cannot be undone, deletes, rulesets, visibility, secrets, frozen contracts, the "never in G54" list, no recommendation).
Under the facts, write one line for each check: `驗收：不變`, `費用：0`, `標籤：無`, `方向：不變`, or what the option changes.
This rule adds no write.

### No claim loop on an outside advisory (G63)

A pull request can fail only on a dependency audit job for an advisory outside its diff. A tdd run cannot fix that in the pull request, so each run claims the issue, finds nothing to do and releases it (evidence, 2026-11-23: faberline/aw#730 and faberline/aw#733. tdd runs claimed and released faberline/lumen pull requests 582 and 583 and faberline/tape pull request 241. The only failed job was the audit for an advisory outside their diffs).

The tdd stage of `aw-worker` has the same rule: when the only failed check of a pull request is an advisory or dependency audit that also fails on the default branch, the run does not claim the issue again. It adds `blocked` with one comment `Waits for: <the advisory issue>`.

In each pass:

1. Find each open issue of `<target>` whose pull request has only one failed check, and that check is an audit or supply-chain job (as in the G47 sweep). Read the same job on the head of the default branch. When it also fails there for the same advisory ID, the failure is outside the diff.
2. Find the advisory issue: the fix issue of G47 or the upgrade issue of G61 (G43). When none is open, open it under G47 first.
3. The issue has `blocked` and the comment `Waits for: <the advisory issue>`: leave it. When the issue has neither, and a tdd or review run claimed and released it for this failure, add `blocked` and the comment yourself as G38 does for an open `Waits for:` prerequisite (a G54 write, when its checks pass). Otherwise report the loop under G37.
4. When the advisory issue is closed as completed, remove `blocked` one time from each issue whose comment waits for it, in the same pass (the stale block sweep, G53). Read the labels again first (G19). Post one comment that names the closed advisory issue and its `closed_at`. Do not remove `blocked` a second time for the same advisory issue: when the audit check fails again after the close, report it under G47.

Do not use this rule when the audit job passes on the default branch: then the diff can be the cause (G14, G47 sweep).
This rule adds no new write: the `blocked` writes are the G53 and G54 writes.

### Review checks the acceptance criteria of the issue (G64)

A spec can change an acceptance criterion of the issue without a word. A review that compares the diff only with the spec then passes a pull request that does not meet the issue (evidence, 2026-11-23: faberline/core#650 had the criterion "a free test (kind) fails for the cause before the fix and passes after it". The spec replaced it with unit tests and did not say so. The review of pull request 651 passed).

The `aw-worker` skill has these two rules:

- The spec signal lists every acceptance criterion of the issue body, word for word, and marks each one `kept` or `changed: <reason>`. A changed or dropped criterion makes the spec outcome `blocked` with a `Decision:` line for the desk, not `ready`.
- The review compares the diff with the acceptance criteria of the issue body, not only with the spec. It does not pass when a criterion is missing and no desk decision is on record.

In each pass, read each spec `ready` signal and each review `passed` signal of `<target>` that is newer than the last pass:

1. Read the acceptance criteria of the issue body, word for word.
2. For a spec `ready` signal: each criterion must be in its list, as `kept` or as `changed` with a desk decision on record (a `desk:` comment or a decision issue that names the issue). A criterion that is missing, or `changed` with no desk decision, is a spec gap: post one comment on the issue that names each criterion, and send the issue to the desk as a `中控可決定` item. The recommended option keeps the acceptance as written (G54, "Keep the acceptance as written"): the spec is written again.
3. For a review `passed` signal: when the issue body has a criterion that no test or file of the merged diff proves, and no desk decision is on record, open one `中控可決定` item. It names the criterion, the merged pull request and a new issue that proves the criterion. The pull request is merged: do not ask to revert it.

A criterion with a desk decision on record is not a gap. Write the link of the decision.
This rule adds no write.

### A major upgrade of a shared dependency covers every crate that uses it (G65)

A workspace can have more than one crate that names the same dependency. When an upgrade moves only one crate across a major version, the workspace holds two major versions of that dependency. A consumer that uses types of both crates then does not compile (evidence, 2026-11-27: faberline/core#648 moved `service-k8s` to schemars 1 and left `service-backup` on schemars 0.8. The tag `v0.5.3` broke faberline/tape and faberline/lumen, see faberline/core#656).

The `aw-worker` skill has these two rules:

- The spec of an issue that moves a dependency across a major version lists every workspace crate whose `Cargo.toml` names that dependency. Each crate is marked `moves` or `stays: <reason>`.
- The review reads every `Cargo.toml` and the lock file at the head of the pull request. A crate that still names the old major version, with no `stays: <reason>` line in the spec, is a finding.

In each pass, read each spec `ready` signal and each review `passed` signal of `<target>` that is newer than the last pass, for an issue that moves a dependency across a major version (for a `0.x` version, a change of the minor number is a major change):

1. List the crates of the default branch that name the dependency: read each `Cargo.toml` (`git ls-files '*Cargo.toml'` in a checkout, or the REST tree).
2. For a spec `ready` signal: each crate of the list must be in the spec, as `moves` or as `stays: <reason>`. A crate that is missing is a spec gap: post one comment on the issue that names each missing crate, and send the issue to the desk as a `中控可決定` item. The recommended option sends the issue back to `stage:spec` (a G54 write, when its checks pass).
3. For a review `passed` signal: read the lock file of the default branch after the merge. When it holds two major versions of the dependency and the spec has no `stays:` line for it, open one issue for the crates that stayed, or reuse the open one (G43). Write the crates, the two versions and the merged pull request. Then write one `中控可決定` item that asks the desk for its labels (the priority of the first issue). Do not ask to revert the merged pull request.
4. When the repository is `core` and a release candidate or a final tag is planned for the upgrade, write on the board: `consumer compile check: not shown for <tag>`, until a comment or a run shows that one consumer repository compiles against the candidate (desk handbook, section 5).

A `stays: <reason>` line with a reason that the code shows is not a gap.
This rule adds no write.

### A decision that is only a wait is a `Waits for:` line (G66)

A blocked signal can give options whose recommended one is "wait for X". The owner has nothing to answer: nothing is written, nothing is paid, and the product does not change. A `需要使用者` decision for it uses the owner's time and gets `needs-human` for no decision (evidence, 2026-11-27: faberline/aw#739 for faberline/sift#398. The recommended option was "wait for the next release". Its `執行清單` said "no write now", and the issue was typed `需要使用者`).

Use this rule before you open or extend a `decision` issue (G20, step 8). The item is only a wait when the recommended option (the first one, G3) passes all four checks:

1. It needs no write now, other than the `Waits for:` text and the label `blocked`.
2. It costs no money and starts no paid run.
3. It does not change the product direction or an acceptance criterion (G57, G62).
4. The thing that it waits for can be read: an issue, a pull request, a release tag, a workflow run or a merged commit (G52).

Then do this in the same pass:

1. Open no `decision` issue, and add no `needs-human` label.
2. Find the open issue that tracks the thing (G43, G24). Post one comment on the waiting issue: `Waits for: #<number>` for an issue of `<target>`, or `Waits for: <owner>/<repo>#<number>` for another repository. When no issue tracks the thing, write the thing itself, for example `Waits for: release tag sift@0.3.0 (no issue tracks it)`. Do not post the comment two times.
3. Keep `blocked`. When the issue lost `blocked`, add it as G38 does (a G54 write, when its checks pass).
4. When the body has no `Waits for:` line for the thing, edit the line under G54 when its checks pass. Otherwise the comment is enough.
5. Write one line on the board with the thing, its state and the time of the check (G52). Read the thing again in each run. When the condition is met, G52 and G53 remove `blocked`.

Do not use this rule in these cases; then step 8 applies as before:

- The thing that the issue waits for needs an owner decision that no open issue asks, for example an approval of a paid run. Ask for that decision. Do not ask "wait or not".
- Another option is better than the wait and you recommend it.
- The wait stops a `p0` issue or a release chain: report it under step 9 (urgent) also.

This rule adds no write: the comment is a normal comment, and the label and body writes are the G38 and G54 writes.

### An old aw pin in the workflows (G67)

A repository installs `aw` at a fixed commit in its workflows. A fix on aw `main` does not reach a repository with an old pin. G35 and G39 find an old pin only after a job fails. This rule finds it before (evidence, 2026-11-27: `ci.yml` of faberline/sift installed aw 0.7.38 and faberline/tape installed aw 0.7.34, while aw `main` was 0.7.80. The CR3 grace window came in aw 0.7.47 (faberline/aw#591). After the core tag `v0.5.3`, the required check `aw standards check` failed on every pull request of the two repositories: faberline/sift#419, faberline/tape#252. No rule had read the pins).

Use this rule in each full run, not in a quick run:

1. Read every workflow file of `<target>` on the default branch (`.github/workflows/*.yml`). Collect each aw pin: `cargo install ... faberline/aw --rev <sha>`, `uses: faberline/aw/.github/actions/<name>@<ref>`, `uses: faberline/aw/.github/workflows/<file>@<ref>`, and an `aw-ref:` input.
2. Compare each pin with aw `main`: `gh api "repos/faberline/aw/compare/<pin>...main"`. When `ahead_by` is 0 for every pin, stop. Read the aw version of the pin and of `main` (`Cargo.toml` at each ref).
3. List the aw commands, actions and reusable workflows that the workflows of `<target>` run, for example `aw standards check`, `aw ci check`, `aw security check`, the `coverage` action and `gke-test.yml`.
4. In the commits between the pin and `main`, find each fix for one of them: a commit whose title starts with `fix` and names the command, the action, the workflow or its standard, or that changes its files. Read the commit before you name it (G14).
5. No such fix: the pin is old and it lacks no fix. Write one board line, `aw pin: <version>, main <version>, no fix missing`. Open no issue.
6. One or more such fixes: open one issue in `<target>`, or reuse the open one. Search the open issues for `aw pin` and for the pinned commit first (G43, G39 step 3). One issue covers all pins of the repository. Its body has:
   - each pin: the file, the line, the commit and its aw version;
   - the commit and the version of aw `main`;
   - each fix that the pin lacks: the commit, its pull request and the check that it repairs, and a failed run when one exists;
   - `Waits for: none`;
   - these acceptance criteria: every aw pin in the workflows names one commit, the newest green commit of aw `main` that the spec run reads; the checks of the list pass on `main` with that pin; a new finding of the newer aw is fixed or gets a named issue; no rule is switched off, no exception is added to `aw.toml`, and no test, threshold, coverage line or timeout changes.
7. Add the labels `stage:spec`, `agent:claude` and `p0` to the issue. When the issue already has a `stage:*` label, change only `p1`, `p2` or `p3` to `p0`. The priority is `p0` because the missing fix is in a check that every pull request runs: when the check fails, no pull request of the repository can merge.
8. Write one line on the board under `這一輪做了什麼`, with the link of the issue, the pinned version and the version of aw `main`.

Pins in one repository that name different commits are also a finding: write them in the same issue.
A failed job that the old pin already causes is still a G39 case. Use the issue of this rule for it, and do not open a second one (G15).
Do not change a workflow file yourself.
The routine prompt must list the G67 label writes; without them, open the issue with no labels and send the label change to the desk as a `中控可決定` item.

### A file of a public repository that the spec cannot read (G68)

A spec run can report `blocked` because it cannot read a file of another repository: the routine has no access to that repository, or the session blocks the request.
This is not always a question for the owner (evidence, 2026-11-27: faberline/aw#750. The spec of faberline/core#669 needed one file of faberline/lumen. The supervisor wrote the item as `類型：需要使用者`. The desk decided it: lumen is public, so the desk gave the commit, the path and the SHA-256 of the file in a `desk:` comment, and the tdd run copies the file as a fixture).

Use this rule when all of these are true. Read each one in this pass (G14, G19):

1. The other repository is public. Read `gh api repos/<owner>/<name> --jq .visibility`. The answer must be `public`. When the call fails or the answer is not `public`, this rule does not apply.
2. The spec or the blocked signal names the file: the repository and the path.
3. The stage run needs the content of the file as it is (a schema, a fixture, a contract text). It does not ask for a change in the other repository.
4. The copy needs no secret, no new access for the routine and no paid action.

Then the item is `類型：中控可決定`. Write one option, as the recommendation:

- the full commit SHA of the other repository that the copy comes from (a commit, not a branch name and not a tag name);
- the path of the file at that commit;
- a SHA-256 of the file content, when you can read the file. When you cannot read it, write `checksum: the desk supplies it` and do not write a number that you did not compute;
- the instruction for the tdd run: copy the file into `<target>` as a fixture, compute the SHA-256 of the copy, and compare it with the number of the decision. When the numbers differ, or the tdd run cannot read the file, the tdd run reports `blocked` and changes nothing. It does not write the file from memory.
- the place in `<target>` where the copy records its source: the repository, the commit and the path.

The item stays `類型：需要使用者` when the other repository is private or internal, when the license of the file does not allow a copy, when the file holds a secret or personal data, or when the option needs a change in the other repository.
A copy is a snapshot. When `<target>` must follow later changes of the file, say so in the item: that is a second issue, and this rule does not decide it.

This is a class of decision for the desk. Do not execute it under G21 or G54: the desk supplies or confirms the checksum.
This rule adds no write. The routine prompt needs no new line.

### An acceptance criterion of the desk whose premise is false (G69)

"Keep the acceptance as written" (G54) says that an option that changes an acceptance criterion is `類型：需要使用者`. The reason is that the issue text is the acceptance that the owner accepted.
That reason does not hold for a criterion that the desk wrote and that the owner did not confirm (evidence, 2026-11-28: faberline/aw#752. The desk wrote criterion 2 of faberline/sift#415 before the cause was known. The spec proved that the premise of the criterion was false. The supervisor wrote the item as `類型：需要使用者`. The desk restated the criterion, and no test became weaker. The product question of the same issue, a new public field, stayed with the owner).

Use this rule when all of these are true:

1. The desk wrote the criterion. Read the author and the edit history of the issue body, or the `desk:` comment that added the criterion. When the owner wrote it, or you cannot show who wrote it, this rule does not apply.
2. No comment or signal shows that the owner confirmed that criterion, and it is not a quote of the owner's words.
3. The spec proves that the premise of the criterion is false, with evidence that you read in this pass: the file and the line, the run, or the commit. A premise is a fact about the present system that the criterion assumes. An opinion that the criterion is too hard is not such a proof.
4. The intent of the criterion can still be stated: the defect that the issue reports is still tested.

Then the item is `類型：中控可決定`. Write it with these parts:

- the criterion as written, and who wrote it (`shown: <link>`);
- the premise, and the evidence that it is false (`shown: <file and line>`);
- one recommended option: the desk restates the criterion. Give a proposed text. The new text tests the same defect at the place where the spec shows that it occurs;
- a line that says what does not change: no other criterion, no test, no threshold, no timeout and no gate becomes weaker (G45).

The item stays `類型：需要使用者` when a condition above is `not shown`, when the new text makes the acceptance smaller (it removes a case, an input or a platform), when the criterion is a frozen contract or a milestone exit condition, or when the option adds or changes a public interface.
When one issue has both kinds (a desk criterion with a false premise, and a product question), write two items. Only the first is `類型：中控可決定`.

Do not restate the criterion yourself, and do not execute this item under G21 or G54. The desk edits its own criterion and records the old text in a comment.
This rule adds no write. The routine prompt needs no new line.

### An expected string after an intended upstream signature change (G70)

A test in `<target>` can compare a text with the public signature of an upstream package. When the upstream release changes that signature on purpose, the test fails, and the fix changes the expected text.
G45 says that a weaker test needs the owner. A change of one expected string to the new correct value does not make the test weaker (evidence, 2026-11-28: faberline/aw#753. core `v0.5.4` changed a TypeScript signature on purpose (faberline/core#597 and #608). The test of faberline/defer#311 changed one expected string from `adminBackup()` to `adminBackup(options?: CallOptions)`. The supervisor wrote the item as `類型：需要使用者`. The desk decided it with five limits).

Use this rule when all of these are true. Write `shown: <link>` or `not shown: <what is missing>` for each one:

1. The upstream change is intended: a merged issue or pull request of the upstream repository, or its release notes, names the new signature. A signature that changed with no such record is a possible upstream defect: write it as a cross-repository blocker of the upstream repository, and do not use this rule.
2. The release that `<target>` uses has the change: read the tag or the lock file.
3. The change in `<target>` is only expected strings of that signature, with the same kind of assertion as before (an exact comparison stays an exact comparison).

Then the item is `類型：中控可決定`. Write one recommended option with these five limits, and repeat them in the answer comment of the `執行清單` for the tdd run:

- (a) Only the expected string of the changed signature changes. Each other expected value stays.
- (b) The assertion has no wider tolerance: no pattern, no prefix match and no `contains` in place of an exact comparison.
- (c) No `#[ignore]`, no skip and no exclusion is added.
- (d) No test and no test row is deleted.
- (e) The text of the pull request maps each old string to the new string, and links the upstream record of condition 1.

The item stays `類型：需要使用者` when a condition is `not shown`, when the fix needs a change that a limit forbids, or when the new signature removes a behavior that an acceptance criterion or a frozen contract of `<target>` names.

This is a class of decision for the desk. Do not execute it under G21 or G54, also when it has one option. A review run then checks limits (a) to (e) on the pull request.
This rule adds no write. The routine prompt needs no new line.

### An empty queue is not `正常` (G71)

A repository can have no open issue in any stage. The routines then have nothing to do. This does not mean that the product is done (evidence, 2026-11-28: faberline/aw#756. faberline/keep had no open issue for three patrols, and faberline/defer for one. Their boards said `總結：正常` and `沒有其他寫入`. Both had finished milestones on `main`, an old version in `Cargo.toml`, and no release of that work. The desk opened the next work by hand: faberline/keep#278 and faberline/defer#316).

The queue is empty when no open issue of `<target>` has the label `stage:spec`, `stage:tdd` or `stage:review`. Count the issues with `blocked` too.
When each open stage issue has `blocked`, the queue is not empty: the cause is the block. Follow G10 and G53, and do not use this rule.

Use this rule in each full run. A quick run changes only the `更新：` time, so the line `總結：空佇列` of the last full run stays.

1. Write `總結：空佇列` on the board, never `總結：正常`. A red `main` or an other root cause still makes it `總結：異常`.
2. Write the cause under `## 根因`, with what you read in this pass: the count of open issues and open pull requests, the open milestones with their open counts, the version in `Cargo.toml` on the default branch, and the newest tag or release (`gh api repos/<target>/releases/latest`, `gh api "repos/<target>/tags?per_page=1"`). Say in one line whether finished work on `main` is in a release.
3. Look for a release readiness issue: `gh api "repos/<target>/issues?state=all&per_page=100&sort=updated&direction=desc"`, and keep the issues whose title starts with `release readiness` (any case). Skip pull requests.
   - One is open: link it on the board and open nothing (G15, G32). When it has no stage label, that is a finding for the desk.
   - None is open, and none was closed in the last 14 days (`closed_at`, from `date -u`): do step 4.
   - One was closed in the last 14 days: do step 5.
4. Open one issue in `<target>`. Write the body to a file and post it from that file (G26). The body has:
   - the title `release readiness: check main against the internal-use bar and name the release path`;
   - one first line `supervisor, <date>:` with the facts of step 2;
   - the nine points of the internal-use bar, each to be answered as met or not met, with the evidence (a test name, a workflow job, a file path): 1. it installs, and a test runs the documented install path; 2. it loses no data, and backup and restore have a test; 3. high availability with a fixed 3 replicas, with a failover test; 4. security (auth, TLS, audit); 5. it can be observed (metrics, logs, a health endpoint); 6. it can be upgraded (an upgrade test from the last tag, or a written reason why there is none); 7. a stable API (a compatibility test of the public surface); 8. quality gates (the `gate` job, the coverage ratchet, the dependency audit); 9. it uses the current `core` tag;
   - the work: one new issue for each point that is not met (`stage:spec`, with a priority label), and one comment with the release path: the next version, the workflow that makes the candidate, and each step that costs money, with a number in TWD;
   - the line `Waits for: none`, alone on its line;
   - these acceptance criteria: the spec signal holds the table of nine points with evidence, read on the newest `main`; each point that is not met has an open issue, and the spec reports `split` with their numbers; when all nine are met, the spec says so and names the release path; no test, threshold or timeout is weakened; no paid run; no tag.

   Add the labels `stage:spec`, `agent:claude` and `p2`. Read the issue back (G26). Write one line on the board under `這一輪做了什麼`, with the link.
   Use faberline/keep#278 as the example of the text. Do not copy its facts.
5. The check is done. Read the closed release readiness issue, its signals and its children.
   - A child issue is open, or a point is not met and has no issue: the check has a gap. Write it on the board. For a point with no issue, open one `decision` item of type `中控可決定` that names the point.
   - All nine points are met, or each gap is closed, and only a paid step or a tag is left: open one `decision(<repo>)` issue in faberline/aw (search first, G15). Its type is `類型：需要使用者`, with the label `needs-human` (step 8): a paid run, a tag and a release are never a supervisor action (G54, "Never in G54"). The item gives the release path of the readiness issue, each paid step with its number in TWD and the place where that number is written (`shown: <link>`), and the tag. When no number in TWD is written, write `cost: not shown` and name the workflow. Do not estimate a number yourself.
   - Nothing is left (the release exists): write that on the board. This rule opens no other issue.

Never start a paid run, a `workflow_dispatch`, a tag or a release under this rule. Open at most one release readiness issue for `<target>` in 14 days.
To open the issue of step 4 costs nothing and changes no product direction, so it needs no `decision` issue.
The routine prompt must list the G71 issue and its three labels. Without them, open the issue with no labels and send the label change to the desk as a `中控可決定` item.

### The stage labels of a closed issue (G72)

A closed issue can keep `blocked` and an old stage label. The board counts and `aw desk patrol` then show work that does not exist (evidence, 2026-11-28: faberline/aw#756. The closed issue faberline/defer#312 kept `blocked` and `stage:tdd`. The supervisor wrote that it had no authority to change the labels of a closed issue, and the desk changed them by hand).

Use this rule in each full run, not in a quick run:

1. List the closed issues that still have one of the labels: `gh api "repos/<target>/issues?state=closed&labels=<label>&per_page=30"` for `blocked`, `stage:spec`, `stage:tdd` and `stage:review`. Skip pull requests.
2. For each issue, read it in this pass (G14, G19). All of these must be true:
   - `state` is `closed` and `state_reason` is `completed`;
   - a merged pull request closed it. Read `gh api repos/<target>/issues/<n>/events` and find the newest `closed` event. Its `commit_id` is not empty, and `gh api repos/<target>/commits/<commit_id>/pulls` gives a pull request with `merged_at` set and the default branch as its base;
   - nobody opened the issue again after that event.
3. Then remove `blocked` and the old `stage:spec`, `stage:tdd` or `stage:review` label, and add `stage:done`. Change no other label. Post no comment and do not open the issue again. Read the labels back (G26).
4. Write one line on the board under `這一輪做了什麼` for each issue, with the link and the pull request.

Do not use this rule for an issue that was closed as not planned (a split or replaced parent of G54 keeps its labels), for an issue that was closed with no merged pull request, or for an open issue. For such an issue with `blocked` or a stage label, write one board line. When the same line is there for a second run, put it in a `pipeline` issue.
Correct at most 10 issues in one run. Write the rest on the board for the next run.
The routine prompt must list the G72 label writes. Without them, G72 only reports on the status board.

### A runner label that GitHub retired (G51)

A job can be cancelled with no step at all. Its check run annotation then says `The configuration '<label>' is not supported`, or another text that says the runner configuration is not supported (evidence, 2026-11-17: faberline/aw#604, the `macos-14` build of the tape release candidate).
The public runner list gives the cause. Do not write `cause not confirmed` before you read it.

1. Read the README of `actions/runner-images`: `gh api repos/actions/runner-images/contents/README.md --jq .content | base64 -d`. Find the image behind the label (for example `macos-14-arm64` is `macos-14`) and read whether it is retired, and from which date.
2. When the README says the image is retired, the cause is confirmed. Write the label, the retire date and the README line in the issue. Say that no organization or plan setting is involved.
3. Open one issue in `<target>` that changes the workflow to a supported label. Give it `stage:spec` and `agent:claude`. Search the open issues of `<target>` first (G32); when one covers the label, add one comment with the new run link instead. Open no `decision` issue and ask the user nothing for this cause. A new paid run still needs its own approval (cost control); say so in the issue.
4. When the README does not list the label, or lists it as supported, write `cause not confirmed` as in step 6, and list the README as read.

The routine prompt must list the issue open and the two labels of step 3 for this rule. Without them, G51 does not run: put the fix in a `pipeline` issue, but still write the confirmed cause.

#### More than one failed job in one run

When a run has more than one failed or cancelled job, write the cause of each one. Do not stop at the first one.
Read the repository rules for the other jobs too (for example the S6 rule in `STATUS.md`): a second failure can have its own known cause that the issue does not mention.
When the run is still in progress, list the jobs that have not ended. Write them as "not ended" and add their causes in the next pass. Do not write the cause of the run as one line while a job is still open.

### A wait reason is read in each run (G52)

This rule adds to G10. G10 says to check a blocked issue. G52 says what the board may write about it (evidence, 2026-11-18: faberline/lumen#400 stayed `blocked` for "the tag exists first" for about 8 hours after core `v0.5.1` existed; the board of faberline/aw#256 repeated the wait reason and did not read the tag).

For each `blocked` issue whose reason names a thing that can be read, read that thing in each run. Do not copy the reason of the last run or of the comment.
A thing that can be read is a release tag, an issue or a pull request, a workflow run, or a merged commit, in a repository that the routine can read.

1. The condition is not met: write on the board what you read and its state, for example `tag v0.5.1 not found`.
2. The condition is met: do not write "waits for X". Follow G10 and G21:
   - When G21 allows the label change (the four checks pass), remove `blocked` yourself and write it on the board.
   - When it does not, and the blocking comment names who removes the label, or the unblock needs no new decision, test the item against G54 and execute it when its checks pass. Otherwise open or reuse one `decision` issue of type `中控可決定` (step 8) with the evidence and the exact `執行清單`. Write on the board that it is open and link it.
   - When the unblock needs a new decision, G10 and step 8 apply as before.
3. You cannot read the thing (another repository without access, HTTP 403 or 404): write `cannot verify: <name of the thing>` on the board. Do not write "waits for".
4. Each board line of a blocked issue gives the time (UTC) when the condition was last checked, for example `checked 2026-11-18T01:11Z`.

G38 is unchanged: it adds `blocked` for an open `Waits for:` prerequisite, and G52 reads that prerequisite again in each run, so a merged prerequisite is found in the same run.
This rule adds no write. The routine prompt needs no new line.

