#!/usr/bin/env python3
"""One worker lease per repository; never reset branches or infer CI success."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


_spec = importlib.util.spec_from_file_location('milestone_queue', Path(__file__).resolve().parents[2] / 'issue-loop/scripts/milestone_queue.py')
queue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queue)


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


def branch_of(n, *, client=None, prs=None):
    # An existing PR is authoritative, including a CI retry.
    client = client or queue.Client()
    matches = []
    for pr in (prs if prs is not None else client.pages(f'repos/{client.repo}/pulls?state=open')):
        if pr['head']['ref'] not in (f'issue/{n}', f'agy/issue-{n}'):
            continue
        if not pr['head'].get('repo') or pr['head']['repo']['full_name'].lower() != client.repo.lower():
            refuse(f'foreign PR branch collision for issue #{n}; resolve association')
        matches.append(dict(number=pr['number'], headRefName=pr['head']['ref']))
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
            client = queue.Client()
            pr_inventory = None
            def association(n, fresh=False):
                nonlocal pr_inventory
                if pr_inventory is None or fresh:
                    pr_inventory = client.pages(f'repos/{client.repo}/pulls?state=open')
                return branch_of(n, client=client, prs=pr_inventory)
            selected, snapshot = None, None
            continuation = False
            if lease:
                prior = client.issue(lease['issue'])
                ls = queue.labels(prior)
                if prior['state'] == 'open' and ls & queue.STATES == {'doing'}:
                    branch, pr = association(prior['number'])
                    if not pr or lease.get('phase') in ('coding', 'claiming'):
                        selected = client.continuation(prior['number'])
                        continuation = True
                elif lease.get('phase') in ('coding', 'claiming'):
                    # CI retry can reclaim the same PR, never replace a live owner
                    # with another issue after a metadata/lifecycle change.
                    if prior['state'] == 'open' and ls & queue.STATES == {'todo'}:
                        branch, pr = association(prior['number'])
                        if pr:
                            selected = client.continuation(prior['number'])
                            continuation = True
                        elif lease.get('phase') == 'claiming':
                            # An unexecuted initial claim is not already coding.
                            # Revalidate its frontier instead of granting a retry
                            # a new continuity exemption. Never switch its issue.
                            snapshot = client.snapshot()
                            if snapshot['decision']['selected'] != prior['number']:
                                refuse('pending initial claim no longer eligible; preserve intent/worktree')
                            selected = next(i for i in snapshot['issues'] if i['number'] == prior['number'])
                    if selected is None:
                        refuse('owned issue paused by lifecycle change; preserve lease/worktree')
            if selected is None:
                # Existing same-PR CI repairs survive batch reprioritization. This
                # is continuity, not permission to start new future-batch work.
                issues = [i for i in client.pages(f'repos/{client.repo}/issues?state=open')
                          if 'pull_request' not in i and i['state'] == 'open' and queue.labels(i) & queue.STATES == {'todo'}]
                for candidate in sorted(issues, key=queue.priority):
                    branch, pr = association(candidate['number'])
                    if pr:
                        try:
                            selected = client.continuation(candidate['number'])
                        except ValueError as exc:
                            print(f'paused repair #{candidate["number"]}: {exc}')
                            continue
                        continuation = True
                        break
            if selected is None:
                snapshot = client.snapshot()
                decision = snapshot['decision']
                if decision['selected'] is None:
                    if lease and lease.get('phase') not in ('coding', 'claiming'):
                        lease_path.unlink()
                    print(json.dumps(decision, sort_keys=True))
                    return 3
                selected = next(i for i in snapshot['issues'] if i['number'] == decision['selected'])
            n = selected['number']
            branch, pr = association(n)
            wt = prepare(checkout, n, branch)
            if continuation:
                # Recheck dependencies and lifecycle after potentially long prepare.
                fresh = client.continuation(n)
                if queue.issue_identity(fresh) != queue.issue_identity(selected):
                    refuse('owned/repair issue changed during preparation; preserve worktree and retry')
            else:
                fresh_snapshot = client.snapshot()
                if queue.fingerprint(fresh_snapshot) != queue.fingerprint(snapshot):
                    refuse('queue/milestone/prerequisite changed during preparation; no claim made')
                fresh = next(i for i in fresh_snapshot['issues'] if i['number'] == n)
            # Last read immediately precedes intent/label write, under repository
            # guard. GitHub labels have no distributed CAS; remote actors remain a
            # documented race. An uncertain PATCH retains intent, never replays it.
            if association(n, fresh=True) != (branch, pr):
                refuse('PR/branch association changed before claim; preserve worktree')
            if queue.issue_identity(client.issue(n)) != queue.issue_identity(fresh):
                refuse('issue changed immediately before claim; no label write')
            temp_lease = common / 'issue-work.lock.new'
            claimed = dict(issue=n, owner=owner, since=now, pid=os.getppid(), host=socket.gethostname(), phase='claiming')
            temp_lease.write_text(json.dumps(claimed))
            temp_lease.replace(lease_path)
            if 'todo' in queue.labels(fresh):
                client.patch(n, labels=sorted((queue.labels(fresh) - {'todo'}) | {'doing'}))
                confirmed = client.issue(n)
                if confirmed['state'] != 'open' or queue.labels(confirmed) & queue.STATES != {'doing'}:
                    refuse('claim outcome changed; intent retained for read-before-retry recovery')
            claimed['phase'] = 'coding'
            temp_lease.write_text(json.dumps(claimed))
            temp_lease.replace(lease_path)
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
