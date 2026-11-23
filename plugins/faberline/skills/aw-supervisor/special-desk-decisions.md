# aw-supervisor: Execute a desk decision: G21 and G54

This file is part of the `aw-supervisor` skill. `SKILL.md` says when to read it.
The rules keep their numbers and their text. The Extra rules of `SKILL.md` apply to them.

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

An item with such an action is not in G21. Test it against G54 (below): G54 allows the children of a split and the close of a split or replaced parent.
An item that G54 does not allow either keeps waiting for the desk.
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
This close, the parent close of G54 and the close of G60 are the only closes that the supervisor may do.
It never closes any other issue or pull request.

When a write fails, stop the item, write the error in the comment, and keep the decision issue open for the desk.
Do not retry more than once.

Add each executed item to the status board under `這一輪做了什麼`, with the link.

### Execute a desk decision yourself (G54)

The owner decided on 2026-11-22: the supervisor executes a `類型：中控可決定` item itself.
Only a hard problem and a `類型：需要使用者` item go to the desk or the owner.
Before this rule the desk did simple work by hand, one pass later: it made the children of a split, took the recommended option, and closed a replaced parent.
G54 widens G21. G21 stays the rule for a label-only item with one option.
Every re-read check of G21 applies to G54 too.

#### When G54 applies

Apply G54 to an item only when all of these are true:

1. The item has the type `類型：中控可決定` (step 8), and no case of the "never in G54" list applies.
2. The item has a recommended option.
   It is the recommendation of the stage run (G20), the one option that a rule of this skill gives (G10, G16, G17, G38, G44, G52), or your own recommendation after you verified each fact (step 7, G9, G14).
   When the item has two options, take the recommended one (the first one, G3).
   Do not take the other option.
3. Each write of its `執行清單` is in `<target>` and is on the list of allowed writes below.
4. The routine prompt lists the G54 writes.

When check 2 fails (no recommendation), or you cannot say which type the item has, do not execute.
Open the `decision` issue for the desk as before (step 8).
When check 4 fails, G54 does not run: open the `decision` issue as before.

The item can be one that you find in this pass, or one in an open `decision(<repo>)` issue that this supervisor opened in an earlier pass.
For an item of this pass, open no `decision` issue.

#### Allowed writes, in `<target>` only

- Create the child issues of a split (G17, G44).
  A child gets these labels: `stage:spec` or the `stage:*` label of the parent; `agent:claude`; the priority label of the parent (`p0` to `p3`); `blocked` when the child waits for a sibling that is open.
  The first line of the body is `<!-- aw:child parent=<n> -->`. The second line is `Part of #<n>.` (G17).
  A child that waits for a sibling has `Waits for: #<m>` as a line of its own (G38). Create the sibling first, so the number exists.
  When the parent body has a section `Common rules`, copy that section into each child, word for word. Do not shorten it and do not rewrite it.
  Do not set a milestone on a child. Write `milestone 未設定` and the milestone of the parent on the board line of the child.
- Edit the `Waits for:` line in the body of an issue that depends on the parent or on a replaced issue, so it names the replacements. Change only that line.
- Remove or add the label `blocked`, and add or change one `stage:*` label (as in G21 and G38).
- Post a comment.
- Close a parent that is split or replaced, as not planned (`state=closed`, `state_reason=not_planned`).
  Before the close, post one comment on the parent that names each replacement with its link.
  Close a parent only after each replacement exists and you read it back (G26).
- In faberline/aw: close the `decision(<repo>)` issue that this supervisor opened, with a comment of one line that starts with `executed by supervisor` and says what you did, with the links.

#### Never in G54

These limits do not change:

- merge or approve a pull request, or change a pull request;
- a paid action, a paid rerun or a `workflow_dispatch`;
- a tag or a release;
- a milestone change;
- a change to a ruleset, a setting, a secret, a workflow file, a routine or a schedule;
- a weaker test, threshold, timeout or coverage ratchet (G45), also a deleted, skipped or ignored test. A test that a reference test replaces is a class that the desk can decide (G59); you still do not execute it;
- a choice of product direction: scope, an acceptance criterion, a frozen contract, or two valid designs with no clear recommendation (G57 says when a choice of direction with a recommended option is not such a choice);
- a close of an issue as completed, or a close of an issue that is not split or replaced (the finished parent of G13 stays with the desk). The one exception is G60: every acceptance criterion already holds on the default branch;
- a priority label change on an existing issue (G36) and the `expedite` label for a release blocker (G28). The `p0` of a G47 advisory issue is not in this limit: G47 allows it;
- a write in another repository. The one exception is the faberline/aw decision issue.

The line "a choice of product direction" has one exception: see "Keep the acceptance as written" below.

An item with one of the first seven has the type `類型：需要使用者`.
Open the `decision` issue in faberline/aw with the labels `decision` and `needs-human` (step 8), and stop work on that item.
Do not execute a part of it.
An item with one of the last three goes to the desk as a `decision` issue, as before (G13, G36, G28, G2).

#### Keep the acceptance as written

A stage run can report `blocked` because the issue text and the spec (or the tests) do not agree on a scope point.
This is not always a choice of product direction.
The issue text is the acceptance that the owner accepted already.

Compare each option with the acceptance criteria of the issue as written:

- An option that keeps every acceptance criterion of the issue as written, and changes the spec or the code to meet it, is `類型：中控可決定`. It is the recommended option. Execute it under G54 in this pass: remove `blocked`, change the `stage:*` label to `stage:spec`, and post one comment. The comment names each point, quotes its acceptance line, and says that the spec must cover it as the issue text says. Open no `decision` issue for it.
- An option that removes an acceptance criterion, or makes it smaller or weaker, or that weakens a test, a threshold or a gate, is `類型：需要使用者`. Never execute it. Do not make it the recommended option.

When the only way forward is of the second kind, open the `decision` with `needs-human` as before.
When the points can be decided one by one, execute the points of the first kind and open the `decision` only for the others.
When you cannot say which option keeps the acceptance (the issue text is not clear, or both options change it), the item is `類型：需要使用者`.
When the spec has more than the issue asks, and the stage run wants to drop the extra part, nothing in the issue becomes smaller: this is the first kind.

Do the checks of "Before you write" first. Read the issue text, the spec and the blocked signal in this pass (G14, G19).

Precedent: faberline/aw#703 (som#321: the proposed option made a test assertion smaller, so the owner had to decide; the option that kept the assertion needed no owner) and faberline/aw#714 (lumen#387: the supervisor asked the owner to choose between the issue text and a smaller spec; option A kept the issue as written, and the desk selected it with no new owner decision).

#### Before you write

Do the three checks of G21 "Before you execute an item" in the same pass, right before the first write: read the labels and the last aw signal again (G19), read the cause again (step 7, G14), and check the labels after the write (G4).
Also read the state of each issue that the item touches (G24): the parent, each dependent issue and each replacement.
When anything changed since you made the item, stop. Do not execute.
Examples: the parent is closed, a child exists already, another session moved a label, a newer signal or a `desk:` comment is on the issue.
Write the new state on the board, and in a comment on the decision issue when one is open.

Before you create a child, look for it: the sub-issues of the parent, an issue body with `aw:child parent=<n>` or `Part of #<n>`, and the issue links in the later comments of the parent (G17, G31).
Make only the children that do not exist (G17 case 3).

#### Split or replaced before dead

Before you write that an issue "never unblocks", is dead, or blocks a chain for ever, check whether it was split or replaced.
Do this for the issue and for each blocker that it names:

1. Read `state` and `state_reason`.
2. Read its sub-issues, its `split` signal, its closing comment, and each issue whose body has `aw:child parent=<n>` or `Part of #<n>`.
3. Read the state of each replacement (G24).

When the replacements are all closed as completed, the wait is over: follow G53.
When some are open, the issue waits for them: say which ones.
Write "never unblocks" only when the blocker is closed, has no replacement, and you list what you read.
Example failure: faberline/aw#701 said that an issue would never unblock because of faberline/lumen#100. lumen#100 was split into lumen#347, #348 and #349, and all three were completed.

#### Same failure before a new issue

Before you create any issue (a child, a tracking issue, a decision), search for the same failure.
Search the open issues and the issues closed in the last 7 days of the repository where you create it (step 6, G43).
Search for the run id, the failing test name and the error text, not only for the title.
Read the issues that this run already created too.
When one exists, use it: add a comment there, and create nothing.
Example failure: faberline/core#618 and faberline/core#620 were two issues for one failed run.

#### Order, limits and record

Execute the list in this order: create the children; read each one back (G26); comment on the parent with the links; edit the `Waits for:` lines of the dependent issues; change the labels; close the parent.
The writes of one executed item count as one write for the limit of step 11.
When the limit is reached, leave the rest for the next pass.

When a write fails, stop the item. Do not retry more than once.
Do not close the parent.
Open or keep a `decision` issue for the desk that lists what is made and what is not made (G17 case 3).

After the writes, read the labels again (G19) and do the claim check (G4) for each child and each issue that lost `blocked`.
Record each executed item:

- on the status board under `這一輪做了什麼`, with the links;
- when the item came from an open `decision` issue: one comment there that starts with `executed by supervisor`, then close it as G21 says. When some items of that issue stay open, do not close it, and say which items wait and for whom.
