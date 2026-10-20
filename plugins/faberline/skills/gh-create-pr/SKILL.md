---
name: gh-create-pr
description: "Open or reuse a GitHub pull request from the current branch to main, then report its mergeability and check status. Use when the user asks to create a PR, open a pull request, or PR this branch."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/create-pr.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/create-pr.sh *)
---

# /faberline:gh-create-pr

`${CLAUDE_SKILL_DIR}` is the directory that holds this SKILL.md. Claude Code
fills it in. AGY does not, and the shell expands an unset `${CLAUDE_SKILL_DIR}`
to nothing. In AGY, run the script by the absolute path of
[scripts/create-pr.sh](scripts/create-pr.sh), next to this file.

Open — or reuse — one pull request from the current branch to a base branch,
report its state, then stop. The mechanics live in `${CLAUDE_SKILL_DIR}/scripts/create-pr.sh`;
one PR per head is its invariant, so it reuses an existing open PR instead of
creating a second.

## Rules

- The argument is the base branch; no argument means `main`. The head is
  always the current branch — never open a PR for another ref.
- The script refuses an unpushed or out-of-sync branch; the fix is
  `/faberline:git-push`, not pushing from here.
- Do not merge, close, or edit the PR beyond creation, and do not wait for
  pending checks — merging is `/faberline:gh-merge-pr`'s job.

## Instructions

1. Run the script:

```bash
${CLAUDE_SKILL_DIR}/scripts/create-pr.sh [base]
```

2. Read its exit:
   - `0` — it said whether the PR was created or reused, then printed the
     PR's JSON state. Report the number, URL, created-vs-reused,
     mergeability, and each check's status.
   - `2` — refused (on the base branch itself, no upstream, or not `0 0`
     against the upstream). Report the printed reason; if it says to run
     `/faberline:git-push` first, do that when the user wants the PR.
