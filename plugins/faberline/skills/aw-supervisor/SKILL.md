---
name: aw-supervisor
description: "Run one supervisor pass over the aw issue flow of one repository. Finds root causes, files decision and pipeline issues in faberline/aw, and updates the status board. Use when a routine prompt asks to run the aw-supervisor skill for `<owner>/<repo>`."
user-invocable: true
---

# aw-supervisor

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
So each finding that needs a person or the desk must be in a faberline/aw issue.
It must also be in the status board (step 10).

## Write authorization

The routine prompt lists the allowed GitHub writes.
Do only those writes.
Use `gh api` REST only.
Do not edit other labels, merge, approve or push.
Do not close issues or pull requests.
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
Another exception: G47 (below) lets you add labels to the issue for a new RustSec advisory.
The routine prompt must list the G47 label writes; without them, G47 does not run, and the item goes to the desk.
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
  Create a `decision` issue in faberline/aw.
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
  Use `需要使用者` only in these cases: money or billing; an action that cannot be undone; deletes; rulesets; visibility; secrets; frozen contracts; you have no recommendation.
  Use `中控可決定` for all other cases.
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

### 10. Status board

Do this in each run, also in a normal run.
Do it as the last write.
An unchanged state is not a reason to skip it (G22, below).

Find the open issue in faberline/aw with the label `status` and the exact title `status(<repo>)`.
Create it when there is none.
Replace its body (PATCH) with the current state.
Write the body to a file and post it from that file (G26).
Write it in Traditional Chinese (Taiwan):

- `更新：<UTC time>` and `總結：正常`, `總結：異常` or `總結：排隊` (G23: slow CI queue, with the numbers).
- `## CI 排隊` (G23): wall time, longest job, queued and in-progress runs, and the `behind` PR count.
- `ghost runs` (G25): the ghost run ids, one line. Write `none` when there are none.
- `## 根因`.
  One bullet for each root cause.
  Each bullet has: `緊急` when urgent; what it is; since when; what it blocks, with the chain; who it waits for (使用者, 中控, a routine, or another repository); the faberline/aw issue link.
- `## 規格卡住` (G31): each open issue whose last signal is `stage=spec outcome=blocked`, one line each: the issue link, the age of the signal (from `date -u`, in hours or days), `split` or `Decision`, and the link of the open decision item. Write `none` when there are none.
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

### Re-check blocked issues (G10)

In each run, look at each issue with the label `blocked`.
Check if the reason still holds.
Examples of a gone reason: the failed check is now green; the dependency issue is now closed; the tag now exists.

Check the fact first.
Read the pull request or the issue that the blocked issue waited for.
Use `gh api repos/<owner>/<repo>/pulls/<n>` or `gh api repos/<owner>/<repo>/issues/<n>`.
Note its state.
Note its merge time (`merged_at`) or its close time (`closed_at`).
A pull request can be closed but not merged.
Then say so in the facts, and check that the work it carried is not needed.
Write these facts in the decision.

#### A wait for a fix in another repository (pin check)

An issue can wait for a fix in another repository.
This repository can pin that repository by tag.
Then the wait condition is met only when both are true:

- a tag that contains the fix exists in the other repository;
- this repository pins that tag.

A closed upstream issue is not enough.
A fix that is only on the `main` branch of the other repository is not enough.
In the same pass, check both facts and write both in the decision:

- the pin: the `tag =` lines in `Cargo.toml` of this repository;
- the upstream tags: `gh api repos/<owner>/<repo>/tags` and the commit that holds the fix.

When one fact is missing, the reason is not gone.
Do not remove `blocked`.
Do not recommend to remove it.
Say what is missing: a new upstream tag, or a pin change in this repository.
Example failure: decision faberline/aw#347 (relay) recommended to remove `blocked` from relay#18 and relay#11 because faberline/core#214 was closed.
The same decision said that relay pins core tag `v0.4.14` and that the fixes were only on core `main`.
The desk chose the other option.

When the reason is gone, a note is not enough.
Make the item with its `執行清單` (below).
Then test it against the four checks of G21.
When it passes all four, execute it in this same pass under G21.
Do not open a `decision` issue for it, and do not wait for the desk.
Every prerequisite closed with `stage:done` is the usual case.
The fix is label-only (remove `blocked`), the type is `類型：中控可決定` and it has one option.
This passes the four checks.
Re-read the labels and the cause first, as G21 says.
G21 keeps all its limits.

When the item fails a G21 check, the supervisor may not change the label, so the desk must do it.
An example is an `執行清單` with an action that G21 does not allow.
Another is a cause that you cannot verify from the repository, the run or the signal.
The desk acts only on an `執行清單`.
Open one `decision` issue for each run of `<target>` for these items.
Put each stale `blocked` issue of this run in that one issue.
Use `類型：中控可決定`.
Use one option.
Its `執行清單` has these lines for each stale issue:

- the label to remove (`blocked`);
- the `stage:*` label to add, when the issue has none (G4);
- the comment text for the issue, in one line.

Search the open faberline/aw issues first (step 6).
A `decision(<repo>)` issue can be open already.
Then you may add the item there in a comment.
The comment must have the full `執行清單`.
A comment that only says "the reason is gone" is not enough.
Do not create a second decision issue for the same blocked issue.
Do not change the label yourself, except under G21.

Example failure: core#163 stayed `blocked` after all 46 checks of its pull request became green.
Example failure: the lumen supervisor found every prerequisite of a `blocked` issue closed with `stage:done`.
It opened faberline/aw#342 for the label removal.
The item passed all four G21 checks.
The desk had to execute it by hand one pass later.

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

### tdd blocked on an incomplete spec (G16)

The tdd stage can find that the spec is incomplete.
The correct report is `respec`: the issue goes back to `stage:spec` without `blocked`.
A `blocked` report keeps `stage:tdd` and adds `blocked`.
Then no routine takes the issue.
The spec routine does not read `stage:tdd`.
The tdd routine does not take `blocked`.
This is a pipeline defect, not a question for a person.

For each open issue in `<target>` with `stage:tdd` and `blocked`, read the last aw signal.
The signal has `stage=tdd` and `outcome=blocked`.
Its text says that the spec is incomplete, unclear or lacks a point.
Examples: `spec unclear:`, `spec incomplete`, `the spec is missing points`.
Then this is a root cause named "tdd blocked on spec".

Open a `decision` issue in faberline/aw in the same run.
Do not wait for one hour.
Do not wait for the planner.
Use `類型：中控可決定`.
Use one option.
Write these facts: the link to the signal; its time; the missing points that the signal lists.
The `執行清單` has these lines for each such issue:

- remove `blocked`;
- remove `stage:tdd` and add `stage:spec`;
- the comment text in one line, with the missing points for the new spec.

Search the open faberline/aw issues first (step 6).
When an open `decision(<repo>)` issue exists, add a comment there with the full `執行清單`.
Do not change the labels yourself.

Also name the defect.
Search the open faberline/aw issues with the label `pipeline` for `tdd blocked on spec`.
When one exists, add one comment there with the issue link and the signal link.
When none exists, create one `pipeline(<repo>)` issue with these words in the title.
Write the `aw` version of the tdd run when a comment or a log shows it: an `aw` older than 0.7.26 refuses `respec`.

Example failure: faberline/relay#134 and faberline/lumen#307 got a tdd `blocked` signal for an incomplete spec on 2026-11-11.
Each issue waited about 50 minutes for a label move (faberline/aw#305, faberline/aw#307).

### Split proposed, no children (G17)

The spec stage can find that an issue is too large for one pull request.
When the split needs no decision, the correct report is `split`.
`aw` then makes the child issues and closes the parent.
A `blocked` report with a split proposal makes no issue.
The issue keeps `stage:spec` and `blocked`, and no routine takes it.
This is a pipeline defect, not a question for a person.

For each open issue in `<target>` with `stage:spec` and `blocked`, read the last aw signal.
The signal has `stage=spec` and `outcome=blocked`.
Its text says that the issue is too large or names a split: `too large`, `split`, `child issues`, `more than one pull request`.
Then look for children: issues whose body has `Part of #<n>`, the sub-issues of the issue, and issue links in a later comment.
When no child exists, this is a root cause named "spec split without children".
Do not wait for a second pass or for a time threshold (G31).

Read the first line of the signal text.

1. It names a decision (`Decision: ...`): two designs, a change of an acceptance criterion, or money.
   Open one `decision` issue in faberline/aw for all such issues of `<target>` (G15).
   Each option is one of the proposed splits.
   The `執行清單` lists the child issues to make for each option, with title, milestone and priority.
2. It names no decision.
   Then the spec run did not use `split`.
   Open a `decision` issue with `類型：中控可決定` and one option.
   The `執行清單` has these lines: the child issues to make (title, the milestone and the priority label of the parent, `agent:claude`, `stage:spec`); one comment on the parent with the links; close the parent as replaced.
   Also name the defect: search the open faberline/aw issues with the label `pipeline` for `spec split without children`.
   When one exists, add one comment with the issue link and the signal link.
   When none exists, create one `pipeline(<repo>)` issue with these words in the title.
   Write the `aw` version of the spec run when a comment or a log shows it.
3. The signal says `made N of M child issues`.
   `aw` stopped part way. The `執行清單` lists only the children that are not made.

A child of a split has `<!-- aw:child parent=<n> -->` on the first line of its body.
From `aw` 0.7.30 a child (depth 1) can split once more: a spec run reports `split` and `aw` makes the grandchildren.
A grandchild (depth 2) cannot split again; its spec run reports `blocked` with `Decision: split grandchild #<n> again`.
Treat that as case 1.
An older `aw` also refuses a child; its spec run reports `blocked` with `Decision: split child #<n> again`.
Treat that as case 1 too.

Do not make the child issues yourself and do not change the labels.

Example failure: faberline/lumen#311, #307 and #310 on 2026-11-12 (faberline/aw#315) and faberline/keep#70 (faberline/aw#313).
Each got a spec `blocked` signal with a correct split proposal. No routine made the children.

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
The one exception is G21 (below).

Example failure: lumen#323 (22:21Z) and lumen#324 (22:27Z) on 2026-11-11.
Each got a spec `blocked` signal with "Decision needed" and a recommendation.
The pass of 23:11Z opened no decision issue for them.

### A spec blocked signal is raised in the same round (G31)

A spec `blocked` signal waits for a person or for the desk.
No routine answers it, so each hour of delay is a lost hour (G17, G20).
Example failure: lumen#288, #289, #290, #291 and #299 got a spec `blocked` signal with a split on 2026-11-11 and no child issue.
The lumen supervisor raised the decision on 2026-11-14 (faberline/aw#394), more than 2 days later.
Another: sift#66 on 2026-11-14 10:10Z, a `Decision:` line with a recommended choice.

In each round, for each open issue in `<target>` with `blocked` whose last `aw:signal` is `stage=spec outcome=blocked`, do this.
This rule sets the timing of G17 and G20. It does not replace their item format or their G21 limits.

1. The signal proposes a split and the issue has no child.
   A child exists when the sub-issues API of the issue is not empty (`gh api repos/<owner>/<repo>/issues/<n>/sub_issues`), or when an issue body starts with `Part of #<n>`.
   Raise the decision in this same round.
   Use the type `類型：中控可決定`.
   The `執行清單` is the split: the child issues to make (G17 case 2).
   Do not wait for a time threshold.
2. The signal has a `Decision:` line with a recommended choice.
   Raise the decision in this same round (G17 case 1, G20).
   Copy the recommended choice as option A, in the words of the signal.
   Before you copy it, verify the facts that the choice depends on (step 7, G3).
   Write the result under the facts.
   When a fact does not hold, say so under option A and give your own option, or use `類型：需要使用者` when you have none (G3).
3. Search for a precedent before you open a new decision.
   Search closed `decision` issues in faberline/aw, of any age, for the same pattern in the same repo.
   Example: `gh api -X GET search/issues -f q='repo:faberline/aw is:issue is:closed label:decision "decision(<repo>)" split'`.
   Read the best matches.
   When one has the same pattern, cite it in the facts of the new decision, with its link and its chosen option, so the desk can apply it at once (faberline/aw#315 is the example for a split without children).
   A precedent never replaces the new decision. Cite it, then raise the item.
4. Put all items of the run in the one decision issue of the run (step 8), or add them as a comment to the open `decision(<repo>)` issue (step 6), as G20 says.
   Make one item for one signal.
5. The board lists these issues under `## 規格卡住` (step 10), with the age of the signal.

The limits of G17 stay: do not make the child issues and do not change the labels, except under G21.

### Execute a label-only desk decision (G21)

The desk is sometimes offline.
A `decision` item of type `中控可決定` can then wait for hours, only for one label removal.
Example failure: faberline/core#147 waited from 09:40Z to 14:0xZ for one label removal, and the p0 issue faberline/relay#138 waited 9 hours (faberline/aw#326, #328, #330, #331, #333 and #334).
So in this case the supervisor executes the item itself and does not wait for the desk.

Apply G21 to an item only when all of these are true:

1. The item has the type `類型：中控可決定` and exactly one option.
2. That option is the recommendation of the stage run (G20), or a verified "the cause is gone" (step 7, G10, G14).
3. Its `執行清單` has only these actions, on issues that exist: remove the label `blocked`; add one `stage:*` label, or change the `stage:*` label to another one; post a comment.
4. The item is not of type `類型：需要使用者`.

Never apply G21 to an item whose `執行清單` has any other action.
These are never in G21:

- create or close an issue in a product repository (this includes the children of a split, G17);
- merge or approve a pull request, or change a pull request;
- rerun a workflow that costs money, or any other action that costs money;
- change a milestone;
- any change to a ruleset, a setting, a secret, a workflow file, a routine or a schedule.

An item with such an action keeps waiting for the desk.
An item of type `需要使用者` keeps waiting for the user.
When one `decision` issue has items of both kinds, execute only the items that pass the four checks.

An item that you find in this same pass is also in G21.
Do not first open a `decision` issue and wait for the desk.
Make the item, test the four checks, and execute it in the same pass.
Example: a G10 check finds that every prerequisite of a `blocked` issue is closed with `stage:done`.
The fix is label-only, the type is `類型：中控可決定` and there is one option.
Then do this in the same pass:

1. Read the labels of the blocked issue again (G19).
2. Comment the evidence on the blocked issue: each prerequisite, its state and its `closed_at`.
3. Remove `blocked`.
4. Add the item to the status board under `這一輪做了什麼`, with the link.

Open no `decision` issue for it.
The limits do not change: label-only, one option, re-read before writing, no merge, no paid action.
When a check fails, make the `decision` issue as before.

Before you execute an item, in the same pass:

1. Read the current labels of the issue (G19) and its last aw signal.
   When the labels no longer match what the item expects (another session already moved the issue, `blocked` is already gone, or a `stage:*` label is already set), do not execute.
   Write the new state in a comment on the decision issue.
2. Read the cause again (step 7, G14).
   Check that the cause is still gone, or that the recommendation of the stage run is still true.
   When the issue waits for a fix in another repository, apply the pin check of G10 again.
   Read it from the repository, the run or the signal, not from the old decision text.
   When it is not true any more, do not execute.
   Say so in a comment on the decision issue.
3. Check that the issue has a `stage:*` label and an `agent:*` label after the write (G4).
   When the list does not give the label and the issue has none, do not execute.

Then execute the list, in the order of the list, with `gh api` REST.
The writes of one executed item count as one write for the limit of step 11.
When the limit is reached, leave the rest for the next pass.
After the writes, read the labels again (G19) and do the claim check (G4).

Then write on the decision issue one comment that starts with `executed by supervisor`.
It lists each write with its link, and the time (G1).
Close the decision issue when every item in it is executed, or is an item that the supervisor passed over because its cause is gone.
When some items stay open, do not close it.
The comment says which items wait, and for whom.
This close is the one close that the supervisor may do.
It never closes any other issue or pull request.

When a write fails, stop the item, write the error in the comment, and keep the decision issue open for the desk.
Do not retry more than once.

Add each executed item to the status board under `這一輪做了什麼`, with the link.

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
- Do not add or change a `stage:*`, `agent:*` or `expedite` label on any other issue. G21 and G41 are the only other label changes.
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

When the routine of that stage produced nothing in 2 hours, a board line is not enough.
This is a `pipeline` report to the desk.
Search the open faberline/aw issues with the label `pipeline` for the stage and `<repo>` first (step 6).
When one is open, add one comment with the new numbers.
When none is open, create one `pipeline(<repo>)` issue in the same pass.
The issue has: the stage, the issues in the list with the hours, the queue depth, the time of the last output of the routine, and the claim check of each issue (G4, G6, G30).
Check G8 first: when every issue waits for an open prerequisite, this is not an alarm and not a pipeline issue.
Check G40 too: when the stage is in backpressure, the routine skipped by design. Do not open or update a `pipeline` issue for it.

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
   Do not change the priority label yourself.

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
- The named issue is merged and the issue still has `blocked`: ask the desk to remove `blocked`, in the same way as before.
  Use a `decision` issue of type `中控可決定` (step 8) and write the exact label change.
  This extends G10.

G38 exception to G21: this is the only case where the supervisor adds `blocked`.
It applies only to an issue with a `stage:tdd` label and a `Waits for:` line with numbered prerequisites that are open (G24).
All other uses of adding `blocked` stay with the desk.
The routine prompt must list this label write; without it, ask the desk with a `decision` issue (step 8).
`aw claim` also passes over such an issue without a label, so the label is for the planner and the status board.

A `Waits for:` line with no issue number (only a title or text) is a spec gap.
Find the issue by title in `<target>` and say so in the facts, and report it as a spec gap on the status board.
The spec skill must write the issue number (`aw-worker`, spec step).
Do not change the label for this line.
When the body `Waits for:` line has no number but the latest aw signal on the issue has `Waits for: #N` (as in aw#498), open one `中控可決定` decision (step 8) that asks the desk to fix the body line and add `blocked`; do not add `blocked` yourself.
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

### Search for the same failure before you open an issue (G43)

Before you open any issue for a failure (red `main`, failed job, failed test), search the open issues of `<target>` for the same run id and for the same failing test name or the same error text. Use REST (`search/issues` with `repo:<target> is:issue is:open`, or the list call and a filter).

- When an open issue names the same run id or the same failing test, add a comment with the new run link there. Do not open a second issue.
- When none does, open the issue, and put the run id and the failing test name in the title or the first lines of the body, so the next search finds it.

This check comes before G28. G28 labels only an issue that this run created.

### An ownerless flake (G44)

A flaky test can fail review again and again while no one owns it (evidence: faberline/lumen#121 was `blocked` since 2026-11-10 with a stale pull request, and its flake kept faberline/lumen#17 in a review loop).

For each open issue in `<target>` that tracks a flaky or intermittent test, do this when one of these is true: the issue has `blocked`, or it has no `stage:*` label, or its pull request is stale (no commit and no aw signal for 2 days, time from `date -u`, G1). And the flake has failed a review or a `main` run since then (read the `failed` review signals and the `main` runs; name the run).

1. Do not only comment.
2. Search for a split issue (step 6, G15, G43). When one is open, add the new run link there and stop.
3. Otherwise open one decision issue (step 8, G3) with one option: split the failing test into a new issue with the labels `stage:spec`, `p1` and `expedite`. The type is `類型：中控可決定`. The `執行清單` creates that issue, and links the old one. G21 does not cover it (it is not label-only), so the desk executes it.

A review `failed` signal with a line `flake: <test> outside the diff, no tracking issue` (aw-worker, review) is the same case: search the open issues for the test name, and when none tracks it, open the tracking issue (G43 first) with the run id and the test name in the title.

**Labels for the issue this run opens (faberline/aw#567).** No routine claims an issue without labels. Add the labels `stage:spec`, `agent:claude`, `p1` and `expedite` to the tracking issue that this run creates, when all of these are true:

1. The same test failed with the same error text in two or more runs (name the run ids).
2. At least one of those runs is on a pull request that does not touch the test.
3. No open issue covers the test (the search above found none).

The issue body gives the run ids. Label only the issue that this run created. Do not rerun more than once, close issues or change other labels.

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
3. Add the labels `stage:spec`, `agent:claude`, `p1` and `expedite` to the issue that this run created. When the issue already has a `stage:*` label, add only `expedite`, and change `p2` or `p3` to `p1`.
4. Read the labels of the issue again in this pass, just before you write "no labels" in a board or a decision. A pass that wrote "no labels" from an old read was wrong in aw#572 and aw#573.

Do not close issues, merge or change other labels. Do not use this rule for a failure that has no advisory ID (use G28 or G44).


### Many branch updates and no merge (G48)

The review stage must update one branch of a repository at a time. A review that posts more than one `branch updated` comment for one repository within 30 minutes, with no merge after them, is a defect. It is not progress (evidence, 2026-11-17: seven updates in seven minutes in lumen; the board said "review is working"; faberline/aw#587).

1. Read the `branch updated` signals of the repository (`gh api` on the issue comments of the `stage:review` issues, or the comment list of the repository since 30 minutes ago). Count the ones in any 30 minutes window.
2. When the count is above one, and no pull request of the repository merged after the first of them, open one `pipeline` issue, or reuse the open one (G43). Write the issue numbers and the time of each `branch updated` comment, and the merge times (or "no merge").
3. In the board or the decision, do not write "review is working" or "not the bottleneck" for this repository. Write the issue number of the `pipeline` issue.

A single `branch updated` per 30 minutes is normal. Do not close issues, merge or change labels.
