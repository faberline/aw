# aw-supervisor: Re-check blocked issues: G10 and G53

This file is part of the `aw-supervisor` skill. `SKILL.md` says when to read it.
The rules keep their numbers and their text. The Extra rules of `SKILL.md` apply to them.

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
Do not change the label yourself, except under G21 or G54.

Example failure: core#163 stayed `blocked` after all 46 checks of its pull request became green.
Example failure: the lumen supervisor found every prerequisite of a `blocked` issue closed with `stage:done`.
It opened faberline/aw#342 for the label removal.
The item passed all four G21 checks.
The desk had to execute it by hand one pass later.

### Stale block sweep (G53)

The check of G10 and G52 reads the issues that changed. A block can stay for days when nobody touches the issue (evidence, 2026-11-19: faberline/lumen had 102 open `blocked` issues; 9 of them had a reason that was gone for 5 to 10 days).
So in each run, after step 4, do this sweep over all of them.

1. List all open issues of `<target>` with the label `blocked`. Page through the list (`per_page=100`, follow `page`). Do not stop at the first page. Skip pull requests.
2. For each issue find what it waits for: the last aw signal with `outcome=blocked`, the last `desk:` or supervisor comment, and each `Waits for:` line of the body (G38).
3. Read each named blocker once: its `state` and `state_reason`. Keep the result in a table in this run and do not read the same blocker twice. The cost is one list call for each page and one read for each distinct blocker.
4. Decide for each issue:
   - Every named blocker is `closed` with `state_reason=completed`: remove `blocked` (label only), keep the `stage:*` label, and post one comment with each blocker number and its `closed_at`.
   - A blocker is `closed` with `state_reason=not_planned`: read its `split` signal or its closing comment and find the issues that replace it. Post one comment `now waits for #X, #Y` and keep `blocked`. When no replacement exists, do not write to the issue: put it on the status board as needing a decision.
   - A blocker is open: leave it.
   - A blocker cannot be read: write `cannot verify: <name>` on the board (G52).
   - The last comment is a decision of the user or of the desk that answers the blocked signal (a `desk:` comment, or a comment that quotes the user), no aw signal is newer, and the comment is more than 24 hours old: remove `blocked`, set the `stage:*` label that the decision names (`stage:spec` when it says the spec must be written again), and post one comment. When the decision does not name the next step clearly, do not guess: put it on the board.
5. Never remove `blocked` when the reason is money, a paid run, a release tag, a thing the owner deferred, or a diagnosis that the issue is too large or needs a split. G52 reads a release tag and the other rules decide about the rest.
6. Never act on a blocker that is only closed: read `state_reason` first. A blocker that is closed with another reason than `completed` or `not_planned` goes to the board.
7. Write one comment for each issue and each change. When the issue already has your comment for the same blocker state, write nothing. Do not repeat a comment in a later run.
8. Read the labels again before each write (G19) and check the `stage:*` and `agent:*` labels after it (G4).

This rule has its own limit: at most 10 issues are changed in one run, and they do not count for the limit of step 11. The rest waits for the next run, and the board says how many are left.
The board (step 10, `## 封鎖檢查`) gives two numbers in each run: how many blocked issues you checked, and how many blocks you removed or pointed again, with the issue numbers.
G53 does not close issues, create issues, merge or change a pull request, and it does not touch a routine.
Before the board says that a blocked issue never unblocks, apply the check of G54 ("Split or replaced before dead").
