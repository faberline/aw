#!/usr/bin/env python3
"""One worker lease per repository; never reset branches or infer CI success."""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def git(*args):
    return run('git', '-c', 'core.fsmonitor=false', *args)


def gh(*args):
    return json.loads(run('gh', *args))


def refuse(message):
    raise RuntimeError(message)


def branch_exists(ref):
    return subprocess.run(['git', 'show-ref', '--verify', '--quiet', ref]).returncode == 0


def branch_of(n):
    # An existing PR is authoritative, including a CI retry.
    matches = []
    for b in (f'issue/{n}', f'agy/issue-{n}'):
        for pr in gh('pr', 'list', '--head', b, '--state', 'open', '--json', 'number,headRefName,isCrossRepository'):
            if pr.get('isCrossRepository'):
                refuse(f'foreign PR branch collision for issue #{n}; resolve association')
            matches.append(pr)
    if len(matches) > 1:
        refuse(f'multiple open PRs for issue #{n}; resolve association')
    if matches:
        return matches[0]['headRefName'], matches[0]['number']
    for b in (f'issue/{n}', f'agy/issue-{n}'):
        if branch_exists(f'refs/heads/{b}') or branch_exists(f'refs/remotes/origin/{b}'):
            return b, None
    return f'issue/{n}', None


def prepare(main, n, branch):
    expected = main / '.claude/worktrees' / f'issue-{n}'
    # Preserve an existing branch checked out at any registered location.
    records = git('worktree', 'list', '--porcelain').split('\n\n')
    for record in records:
        lines = record.splitlines()
        if f'branch refs/heads/{branch}' in lines:
            wt = Path(lines[0].removeprefix('worktree '))
            if wt == main:
                refuse('issue branch is in main checkout; leave it untouched')
            return wt
    if expected.exists():
        refuse(f'{expected} exists but is not registered on {branch}')
    expected.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(f'refs/heads/{branch}'):
        git('worktree', 'add', str(expected), branch)
    elif branch_exists(f'refs/remotes/origin/{branch}'):
        git('worktree', 'add', '--track', '-b', branch, str(expected), f'origin/{branch}')
    else:
        git('worktree', 'add', '--no-track', '-b', branch, str(expected), 'origin/main')
    return expected


def lease_available(lease, owner, mine, now):
    if not lease:
        return True
    if mine and lease.get('owner') == owner:
        return True
    # TTL alone never steals a live session's lease. Unknown/legacy hosts fail closed.
    if lease.get('host') != socket.gethostname() or now - lease.get('since', now) < int(os.getenv('ISSUE_LOCK_TTL', '7200')):
        return False
    pid = lease.get('pid')
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command not in ('next', 'finish', 'block'):
        refuse('usage: work.sh next [--mine] | finish N summary-file | block N evidence-file')
    common = Path(git('rev-parse', '--git-common-dir')).resolve()
    checkout = common.parent
    lease_path = common / 'issue-work.lock'
    # Kernel lock releases even after crash. Never unlink this guard inode.
    with (common / 'issue-work.guard').open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('busy: another command owns the repository guard')
            return 3
        owner = os.getenv('ISSUE_WORK_OWNER', '')
        if not owner:
            refuse('set ISSUE_WORK_OWNER to a stable unique conversation token')
        lease = None
        if lease_path.exists():
            try:
                lease = json.loads(lease_path.read_text())
                if (not isinstance(lease, dict) or not isinstance(lease.get('issue'), int)
                        or lease['issue'] <= 0 or not isinstance(lease.get('owner'), str)
                        or not lease['owner'] or not isinstance(lease.get('since'), int)
                        or not isinstance(lease.get('pid'), int) or lease['pid'] <= 0
                        or not isinstance(lease.get('host'), str) or not lease['host']):
                    raise ValueError('invalid lease schema')
            except (ValueError, TypeError):
                refuse('legacy/invalid issue-work.lock: verify old session ended before manual recovery; preserve its worktree')
        if command == 'next':
            mine = sys.argv[2:] == ['--mine']
            if sys.argv[2:] and not mine:
                refuse('usage: next [--mine]')
            now = int(time.time())
            if not lease_available(lease, owner, mine, now):
                print('busy: lease belongs to another active or unverified session')
                return 3
            git('fetch', '-q', 'origin')
            issues = gh('issue', 'list', '--state', 'open', '--label', 'todo', '--limit', '200', '--json', 'number,createdAt,labels')
            def priority(issue):
                values = [int(l['name'][-1]) for l in issue['labels'] if len(l['name']) == 11 and l['name'].startswith('priority:p') and l['name'][-1].isdigit()]
                return min(values, default=2), issue['createdAt']
            issues.sort(key=priority)
            selected = None
            if lease:
                prior = gh('issue', 'view', str(lease['issue']), '--json', 'number,state,labels')
                labels = {l['name'] for l in prior['labels']}
                if prior['state'] == 'OPEN' and 'doing' in labels and not labels & {'todo', 'done', 'blocked'}:
                    branch, pr = branch_of(prior['number'])
                    # A coding lease may be a CI retry with an existing PR.
                    # Submitted leases wait even if finish's final comment failed.
                    if not pr or lease.get('phase') == 'coding':
                        selected = prior
            if selected is None:
                selected = next((i for i in issues if not {'doing', 'done', 'blocked'} & {l['name'] for l in i['labels']}), None)
            if selected is None:
                if lease:
                    lease_path.unlink()
                print('idle: no todo work; submitted doing issues wait for CI')
                return 3
            n = selected['number']
            branch, pr = branch_of(n)
            wt = prepare(checkout, n, branch)
            temp_lease = common / 'issue-work.lock.new'
            temp_lease.write_text(json.dumps(dict(issue=n, owner=owner, since=now, pid=os.getppid(), host=socket.gethostname(), phase='coding')))
            temp_lease.replace(lease_path)
            if 'todo' in {l['name'] for l in selected['labels']}:
                run('gh', 'issue', 'edit', str(n), '--remove-label', 'todo', '--add-label', 'doing')
            print(f'issue={n}\nbranch={branch}\nworktree={wt}\npr={pr or "none"}')
            return 0
        if len(sys.argv) != 4 or not sys.argv[2].isdigit():
            refuse('expected N and a nonempty evidence/summary file')
        n, file = int(sys.argv[2]), Path(sys.argv[3]).resolve()
        if not file.is_file() or not file.read_text().strip():
            refuse('empty or missing summary/evidence file')
        if not lease or lease.get('issue') != n or lease.get('owner') != owner:
            refuse('this session does not own the issue lease')
        if command == 'block':
            run('gh', 'issue', 'comment', str(n), '--body-file', str(file))
            # Keep lease: evidence does not grant another session coding ownership.
            print(f'reported evidence for #{n}; lifecycle and lease unchanged')
            return 0
        issue = gh('issue', 'view', str(n), '--json', 'state,labels')
        if issue['state'] != 'OPEN' or {l['name'] for l in issue['labels']} & {'todo', 'done', 'blocked'} or 'doing' not in {l['name'] for l in issue['labels']}:
            refuse('finish requires an open doing issue')
        branch, pr = branch_of(n)
        wt = prepare(checkout, n, branch)
        if git('-C', str(wt), 'status', '--porcelain'):
            refuse(f'uncommitted changes in {wt}; preserve and commit them first')
        if int(git('-C', str(wt), 'rev-list', '--count', 'origin/main..HEAD')) == 0:
            refuse('no implementation commits over origin/main')
        # Normal push only: CI retries must never overwrite another writer.
        git('-C', str(wt), 'push', '-u', 'origin', f'HEAD:refs/heads/{branch}')
        if pr is None:
            title = gh('issue', 'view', str(n), '--json', 'title')['title']
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as body:
                body.write(f'Closes #{n}\n\n{file.read_text()}')
                body.flush()
                run('gh', 'pr', 'create', '--base', 'main', '--head', branch, '--title', title, '--body-file', body.name)
            _, pr = branch_of(n)
        else:
            run('gh', 'pr', 'comment', str(pr), '--body-file', str(file))
        lease['phase'] = 'submitted'
        temp_lease = common / 'issue-work.lock.new'
        temp_lease.write_text(json.dumps(lease))
        temp_lease.replace(lease_path)
        run('gh', 'issue', 'comment', str(n), '--body', f'Work submitted in #{pr}; waiting for required CI on the current head.')
        lease_path.unlink()
        print(f'pr={pr}; issue #{n} remains doing; waiting for CI')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        sys.exit(2)
