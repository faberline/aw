# aw-supervisor: tdd blocked on an incomplete spec: G16 and G31

This file is part of the `aw-supervisor` skill. `SKILL.md` says when to read it.
The rules keep their numbers and their text. The Extra rules of `SKILL.md` apply to them.

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
Do not change the labels yourself, except under G21 or G54: the item is label-only with one option, so execute it in the same pass when its checks pass, and open no `decision` issue.

Also name the defect.
Search the open faberline/aw issues with the label `pipeline` for `tdd blocked on spec`.
When one exists, add one comment there with the issue link and the signal link.
When none exists, create one `pipeline(<repo>)` issue with these words in the title.
Write the `aw` version of the tdd run when a comment or a log shows it: an `aw` older than 0.7.26 refuses `respec`.

Example failure: faberline/relay#134 and faberline/lumen#307 got a tdd `blocked` signal for an incomplete spec on 2026-11-11.
Each issue waited about 50 minutes for a label move (faberline/aw#305, faberline/aw#307).

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

The limits of G17 stay: do not make the child issues and do not change the labels, except under G21 or G54.
An item that passes the G54 checks is executed in this same round, and no decision is raised for it.
