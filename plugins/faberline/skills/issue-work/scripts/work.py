#!/usr/bin/env python3
"""One worker lease per repository; never reset branches or infer CI success."""
import fcntl
import importlib.util
import json
import os
import re
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


def work_identity(issue):
    # Milestones are planner metadata, not worker selection/eligibility input.
    return dict(number=issue['number'], state=issue['state'],
                labels=sorted(queue.labels(issue)), title=issue.get('title'),
                body=issue.get('body'), updated_at=issue.get('updated_at'),
                created_at=issue.get('created_at'))


def current_work(client, n):
    issue = client.issue(n)
    if issue['state'] != 'open' or queue.labels(issue) & queue.STATES not in ({'todo'}, {'doing'}):
        refuse('owned/repair work is not exclusively open todo or doing; preserve lease/worktree')
    return issue


def planned_snapshot(client):
    # Consume the existing plan. No milestone/catalog/comment/dependency reads.
    issues = [i for i in client.pages(f'repos/{client.repo}/issues?state=open&labels=todo')
              if 'pull_request' not in i and i['state'] == 'open'
              and queue.labels(i) & queue.STATES == {'todo'}]
    if len({i['number'] for i in issues}) != len(issues):
        refuse('unstable paginated todo inventory; no truncated or duplicate claim')
    issues.sort(key=queue.priority)
    return dict(issues=issues, selected=issues[0]['number'] if issues else None)


def planned_identity(snapshot):
    return [work_identity(i) for i in snapshot['issues']]


def sync_main(checkout, owner, fetched):
    """Best-effort FF-only primary checkout maintenance, under the caller guard.

    A hold skips this maintenance only; fresh-origin issue work remains valid.
    The caller attests exclusive primary-checkout ownership with the existing
    owner token. This is coordination context, not a credential or lease bypass.
    """
    def held(reason):
        return dict(status='held', reason=reason, fetched=fetched)
    if not isinstance(fetched, str) or not re.fullmatch(r'[0-9a-f]{40}', fetched):
        return held('cannot bind exact fetched origin/main')
    try:
        def snapshot():
            branch = git('--no-optional-locks', '-C', str(checkout), 'symbolic-ref', '--quiet', '--short', 'HEAD')
            head = git('--no-optional-locks', '-C', str(checkout), 'rev-parse', 'HEAD')
            remote = git('--no-optional-locks', '-C', str(checkout), 'rev-parse', 'refs/remotes/origin/main')
            dirty = git('--no-optional-locks', '-C', str(checkout), 'status', '--porcelain=v1', '-z', '--untracked-files=all')
            return branch, head, remote, dirty
        before = snapshot()
        branch, head, remote, dirty = before
        if branch != 'main':
            return held('primary checkout is not on main; never switch it')
        if remote != fetched:
            return held('origin/main changed after fetch')
        if dirty:
            return held('primary checkout has tracked/staged/untracked content; preserve it')
        common = Path(git('--no-optional-locks', '-C', str(checkout), 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if (common / 'issue-work.lock').exists() or (common / 'issue-work.lock').is_symlink():
            return held('existing/unknown issue owner lease; preserve main and issue worktree')
        if any((common / name).exists() for name in ('MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','rebase-merge','rebase-apply')):
            return held('primary checkout has an in-progress Git operation')
        if head == fetched:
            return dict(status='current', head=head, fetched=fetched)
        if not owner or os.getenv('ISSUE_MAIN_EXCLUSIVE_OWNER') != owner:
            return held('exclusive primary-checkout owner coordination is not confirmed')
        ahead, behind = map(int, git('--no-optional-locks', '-C', str(checkout), 'rev-list', '--left-right', '--count', head+'...'+fetched).split())
        if ahead:
            return held('local main is ahead/diverged; no reset/stash/rebase')
        if snapshot() != before or (common / 'issue-work.lock').exists():
            return held('primary checkout/ref/owner changed before fast-forward')
        # No hooks/autostash and never overwrite valuable ignored path collisions.
        git('-c', 'core.hooksPath=/dev/null', '-C', str(checkout), 'merge', '--ff-only',
            '--no-edit', '--no-autostash', '--no-overwrite-ignore', fetched)
        if git('-C', str(checkout), 'rev-parse', 'HEAD') != fetched:
            return held('fast-forward outcome uncertain; inspect without retry')
        return dict(status='updated', before=head, head=fetched, commits=behind)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return held(f'Git maintenance refused/uncertain; inspect, no automatic retry: {exc}')


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
            git('fetch', '-q', 'origin', 'refs/heads/main:refs/remotes/origin/main')
            fetched = git('rev-parse', '--verify', 'refs/remotes/origin/main^{commit}')
            print('main_update=' + json.dumps(sync_main(checkout, owner, fetched), sort_keys=True))
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
                        selected = current_work(client, prior['number'])
                        continuation = True
                elif lease.get('phase') in ('coding', 'claiming'):
                    # CI retry can reclaim the same PR, never replace a live owner
                    # with another issue after a metadata/lifecycle change.
                    if prior['state'] == 'open' and ls & queue.STATES == {'todo'}:
                        branch, pr = association(prior['number'])
                        if pr:
                            selected = current_work(client, prior['number'])
                            continuation = True
                        elif lease.get('phase') == 'claiming':
                            # An unexecuted initial claim is not already coding.
                            # Revalidate the planned todo ordering instead of granting a retry
                            # a new continuity exemption. Never switch its issue.
                            snapshot = planned_snapshot(client)
                            if snapshot['selected'] != prior['number']:
                                refuse('pending initial claim no longer eligible; preserve intent/worktree')
                            selected = next(i for i in snapshot['issues'] if i['number'] == prior['number'])
                    if selected is None:
                        refuse('owned issue paused by lifecycle change; preserve lease/worktree')
            if selected is None:
                snapshot = planned_snapshot(client)
                if snapshot['selected'] is None:
                    if lease and lease.get('phase') not in ('coding', 'claiming'):
                        lease_path.unlink()
                    print('idle: no planned todo work; submitted doing issues wait for CI')
                    return 3
                selected = next(i for i in snapshot['issues'] if i['number'] == snapshot['selected'])
            n = selected['number']
            branch, pr = association(n)
            wt = prepare(checkout, n, branch)
            if continuation:
                # Recheck lifecycle after potentially long prepare; planning policy
                # is not re-run by an already-owned worker.
                fresh = current_work(client, n)
                if work_identity(fresh) != work_identity(selected):
                    refuse('owned/repair issue changed during preparation; preserve worktree and retry')
            else:
                fresh_snapshot = planned_snapshot(client)
                if planned_identity(fresh_snapshot) != planned_identity(snapshot):
                    refuse('planned todo/spec changed during preparation; no claim made')
                fresh = next(i for i in fresh_snapshot['issues'] if i['number'] == n)
            # Last read immediately precedes intent/label write, under repository
            # guard. GitHub labels have no distributed CAS; remote actors remain a
            # documented race. An uncertain PATCH retains intent, never replays it.
            if association(n, fresh=True) != (branch, pr):
                refuse('PR/branch association changed before claim; preserve worktree')
            if work_identity(client.issue(n)) != work_identity(fresh):
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
