# aw-supervisor: Split proposed, no children: G17

This file is part of the `aw-supervisor` skill. `SKILL.md` says when to read it.
The rules keep their numbers and their text. The Extra rules of `SKILL.md` apply to them.

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

Do not make the child issues yourself and do not change the labels, except under G54.
Case 2 and case 3 have one option, so G54 applies: make the children in the same pass.
Case 1 goes to G54 only when the signal recommends one split and no "never in G54" case applies.

Example failure: faberline/lumen#311, #307 and #310 on 2026-11-12 (faberline/aw#315) and faberline/keep#70 (faberline/aw#313).
Each got a spec `blocked` signal with a correct split proposal. No routine made the children.
