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
- f. Stage liveness (G8, below).
- g. Review loops (G12, below).
- h. Parent issues whose children are all done (G13, below).
- i. Issues with `stage:tdd` and `blocked` whose last signal says the spec is incomplete (G16, below).
- j. Issues with `stage:spec` and `blocked` whose last signal proposes a split, and no child exists (G17, below).

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

Find the open issue in faberline/aw with the label `status` and the exact title `status(<repo>)`.
Create it when there is none.
Replace its body (PATCH) with the current state.
Write it in Traditional Chinese (Taiwan):

- `更新：<UTC time>` and `總結：正常` or `總結：異常`.
- `## 根因`.
  One bullet for each root cause.
  Each bullet has: `緊急` when urgent; what it is; since when; what it blocks, with the chain; who it waits for (使用者, 中控, a routine, or another repository); the faberline/aw issue link.
- `## 已解除但沒人接手`.
- `## 這一輪做了什麼`.
  Each write of this run, with its link.

Keep no history in the body.

#### Time (G1)

Get the time with `date -u +%Y-%m-%dT%H:%MZ`.
Run it in the same run.
Do not estimate the time.

### 11. Limits

Do at most 6 writes in one run.
The status board is always one of them.
Keep a gap of at least 2 seconds between API calls.

### 12. Final answer

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

When the reason is gone, a note is not enough.
The supervisor may not change the label, so the desk must do it.
The desk acts only on an `執行清單`.
Open one `decision` issue for each run of `<target>`.
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
Do not change the label yourself.

Example failure: core#163 stayed `blocked` after all 46 checks of its pull request became green.

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
The fix is in `aw` 0.7.22 and in the aw-worker skill.
The command line of `aw` takes `respec` only from 0.7.26; an older `aw` refuses it with `invalid value 'respec'`.
So write the `aw` version of the tdd run when a comment or a log shows it.

Example failure: faberline/relay#134 and faberline/lumen#307 got a tdd `blocked` signal for an incomplete spec on 2026-11-11.
Each issue waited about 50 minutes for a label move (faberline/aw#305, faberline/aw#307).

### Split proposed, no children (G17)

The spec stage can find that an issue is too large for one pull request.
When the split needs no decision, the correct report is `split`.
`aw` (0.7.26 or later) then makes the child issues and closes the parent.
A `blocked` report with a split proposal makes no issue.
The issue keeps `stage:spec` and `blocked`, and no routine takes it.
This is a pipeline defect, not a question for a person.

For each open issue in `<target>` with `stage:spec` and `blocked`, read the last aw signal.
The signal has `stage=spec` and `outcome=blocked`.
Its text says that the issue is too large or names a split: `too large`, `split`, `child issues`, `more than one pull request`.
Then look for children: issues whose body has `Part of #<n>`, the sub-issues of the issue, and issue links in a later comment.
When no child exists after one pass of this routine, this is a root cause named "spec split without children".

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
A spec run cannot split such a child again; it reports `blocked` with `Decision: split child #<n> again`.
Treat that as case 1.

Do not make the child issues yourself and do not change the labels.

Example failure: faberline/lumen#311, #307 and #310 on 2026-11-12 (faberline/aw#315) and faberline/keep#70 (faberline/aw#313).
Each got a spec `blocked` signal with a correct split proposal. No routine made the children.
