---
name: git-commit
description: "Commit the current working-tree changes with a meaningful conventional commit message. Use when the user asks to git commit, commit this, or save the current changes as a commit."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/commit.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/commit.sh *)
---

# /faberline:git-commit

Turn the current working-tree changes into one well-formed commit, then stop.
The mechanics live in `${CLAUDE_SKILL_DIR}/scripts/commit.sh`; your job is the two things it
cannot do — read the diff and author the message.

`${CLAUDE_SKILL_DIR}` is the folder of this SKILL.md. Claude Code sets it.
In Codex or AGY, use the absolute path of that folder instead.

## Rules

- Inspect the complete diff before writing the message; never commit content
  you have not read.
- Write a conventional commit message `<type>(<scope>): <summary>`, with a
  body when the change is non-trivial. Do not add generated-with or
  co-authored trailers unless the user explicitly asks.
- When the user scoped the commit to certain paths, pass exactly those paths
  to the script; it stages everything otherwise.
- Do not work around a `refused:` exit — an in-progress merge/rebase means
  stop and report; never resolve it just to commit.
- Never run bare `git stash` / `git stash pop`; the stash stack is shared
  across worktrees and other sessions.

## Instructions

1. Inspect what would be committed:

```bash
git -c core.fsmonitor=false status --short
git -c core.fsmonitor=false diff
git -c core.fsmonitor=false diff --cached
```

2. Author the message from the diff, then run the script:

```bash
${CLAUDE_SKILL_DIR}/scripts/commit.sh -m "<type>(<scope>): <summary>"
```

A multi-line message goes through `-F <file>`; a scoped commit appends the
paths: `${CLAUDE_SKILL_DIR}/scripts/commit.sh -m "…" -- <path>…`.

3. Read its exit:
   - `0` — committed; it printed `log -1 --stat` plus the leftover status.
     Report the subject and anything deliberately left uncommitted.
   - `2` — refused (in-progress operation, nothing to commit, or no
     message). Report the printed reason; do not force past it.
