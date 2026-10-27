#!/usr/bin/env python3
"""Planner adapter for the shared milestone queue; never edits a human body."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

spec = importlib.util.spec_from_file_location('milestone_queue', Path(__file__).resolve().parents[2] / 'issue-loop/scripts/milestone_queue.py')
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


def sections(text):
    for name in ('Goal', 'Acceptance', 'Never', 'Gate'):
        if not re.search(r'^## ' + name + r'(?:\s|$)', text, re.M):
            raise ValueError(f"spec has no '## {name}' section")


def body(file):
    text = Path(file).read_text()
    if not text.strip():
        raise ValueError('empty file')
    return text


def priority(value):
    if value not in ('p0', 'p1', 'p2', 'p3'):
        raise ValueError('priority must be p0, p1, p2, or p3')
    return value


def reprioritize(labels, value):
    return sorted({l for l in labels if not re.fullmatch(r'priority:p[0-9]', l)} | {f'priority:{priority(value)}'})


def required_open(client, n):
    issue = client.issue(int(n))
    if 'pull_request' in issue:
        raise ValueError('planner mutation requires an issue, not a pull request')
    if issue['state'] != 'open':
        raise ValueError('issue is not open')
    return issue


def room(snapshot):
    count = sum('todo' in queue.labels(i) for i in snapshot['issues'])
    maximum = int(os.getenv('ISSUE_TODO_MAX', '3'))
    if maximum < 1 or count >= maximum:
        raise ValueError(f'todo queue full: {count}, maximum={maximum}')


def milestone(client, number):
    if not str(number).isdigit() or int(number) < 1:
        raise ValueError('--milestone must be an existing milestone NUMBER')
    m = client.api(f'repos/{client.repo}/milestones/{int(number)}')
    if m['state'] != 'open':
        raise ValueError('milestone is closed')
    queue.version(m['title'], client.repo)
    return m


def adopt(client, n, value, number=None):
    issue = required_open(client, n)
    if queue.labels(issue) & queue.STATES:
        raise ValueError('adopt requires a backlog issue without lifecycle labels')
    approved_spec = client.spec(issue, client.comments(n))
    sections(approved_spec)
    if number is not None:
        milestone(client, number)
        client.patch(n, milestone=int(number))
    # Full inventory and dependency revalidation before todo, under shared guard.
    snapshot = client.snapshot(include=(int(n),))
    issue = next(i for i in snapshot['issues'] if i['number'] == int(n))
    if queue.labels(issue) & queue.STATES:
        raise ValueError('issue lifecycle changed before adoption')
    queue.milestone_version(issue, client.repo, {m['number']: m for m in snapshot['milestones']})
    # Invalid metadata anywhere cannot be used to jump an unknown batch.
    if snapshot['decision'].get('metadata_errors'):
        raise ValueError('; '.join(snapshot['decision']['reasons']))
    frontier = min(queue.milestone_version(i, client.repo, {m['number']: m for m in snapshot['milestones']}) for i in snapshot['issues'])
    if queue.milestone_version(issue, client.repo, {m['number']: m for m in snapshot['milestones']}) != frontier:
        raise ValueError('earlier unresolved milestone; leave later work in backlog')
    waits = snapshot['blockers'][int(n)]
    if waits:
        raise ValueError('; '.join(waits))
    room(snapshot)
    # Revalidate the entire frontier after potentially long prerequisite reads.
    if queue.fingerprint(client.snapshot(include=(int(n),))) != queue.fingerprint(snapshot):
        raise ValueError('queue/milestone/prerequisite changed during adoption')
    # Last issue/catalog read before the label write.
    fresh = required_open(client, n)
    if queue.issue_identity(fresh) != queue.issue_identity(issue):
        raise ValueError('issue changed during adoption; retry after status')
    current_m = milestone(client, issue['milestone']['number'])
    if queue.milestone_identity(current_m) != queue.milestone_identity(next(m for m in snapshot['milestones'] if m['number'] == current_m['number'])):
        raise ValueError('milestone changed during adoption')
    if client.spec(fresh, client.comments(n)) != approved_spec:
        raise ValueError('spec changed during adoption')
    if queue.issue_identity(required_open(client, n)) != queue.issue_identity(fresh):
        raise ValueError('issue changed immediately before adoption')
    client.patch(n, labels=reprioritize(queue.labels(fresh) | {'todo'}, value))
    print(f'issue #{n} is todo, priority:{value}, milestone={current_m["title"]}')


def execute(client, args):
    command, *args = args
    if command == 'status' and not args:
        snapshot = client.snapshot()
        print(json.dumps(dict(snapshot['decision'], read_only_snapshot=True,
                              snapshot_consistency='non-atomic', mutation_authorized=False), sort_keys=True))
        todo = sum('todo' in queue.labels(i) for i in snapshot['issues'])
        maximum = int(os.getenv('ISSUE_TODO_MAX', '3'))
        backlog = [i for i in snapshot['issues'] if not queue.labels(i) & queue.STATES]
        untriaged = sum(not any(re.fullmatch(r'priority:p[0-3]', l) for l in queue.labels(i)) for i in backlog)
        print(f'room={max(0, maximum - todo)} (todo={todo}, todo_max={maximum}) backlog={len(backlog)} untriaged={untriaged}')
        catalog = {m['number']: m for m in snapshot['milestones']}
        def key(i):
            try:
                return (0, queue.milestone_version(i, client.repo, catalog), queue.priority(i))
            except ValueError:
                return (1, (), queue.priority(i))
        for i in sorted(snapshot['issues'], key=key):
            print(json.dumps(dict(issue=i['number'], title=i['title'], labels=sorted(queue.labels(i)), milestone=(i.get('milestone') or {}).get('title'), waits=snapshot['blockers'].get(i['number'], [])), sort_keys=True))
        return 0
    if command == 'new' and len(args) == 5 and args[3] == '--milestone':
        title, file, value, _, number = args
        value, text = priority(value), body(file)
        sections(text)
        m = milestone(client, number)
        snapshot = client.snapshot()
        room(snapshot)
        if any(i['title'] == title for i in snapshot['issues']):
            raise ValueError('an open issue already has this title')
        # Create with validated milestone but WITHOUT todo. A failed adoption leaves
        # an identifiable backlog issue; never retry a create after uncertainty.
        issue = client.command('api', '--method', 'POST', f'repos/{client.repo}/issues', payload=dict(title=title, body=text, milestone=m['number'], labels=[f'priority:{value}']))
        print(f'created backlog: {issue["html_url"]}', flush=True)
        adopt(client, issue['number'], value)
        return 0
    if command == 'adopt' and (len(args) == 2 or len(args) == 4 and args[2] == '--milestone'):
        adopt(client, args[0], priority(args[1]), args[3] if len(args) == 4 else None)
        return 0
    if command == 'milestone' and len(args) == 2:
        n, number = args
        issue = required_open(client, n)
        if queue.labels(issue) & {'doing', 'done'}:
            raise ValueError('do not reassign active/review work through planner')
        m = milestone(client, number)
        client.patch(n, milestone=m['number'])
        print(f'issue #{n} assigned {m["title"]}; lifecycle unchanged')
        return 0
    if command == 'prioritize' and len(args) == 2:
        n, value = args
        issue = required_open(client, n)
        client.patch(n, labels=reprioritize(queue.labels(issue), value))
        return 0
    if command == 'spec' and len(args) == 2:
        n, file = args
        required_open(client, n)
        text = body(file)
        sections(text)
        client.command('api', '--method', 'POST', f'repos/{client.repo}/issues/{int(n)}/comments', payload=dict(body=f'{queue.SPEC_MARK}\n\n{text}'))
        return 0
    if command == 'block' and len(args) == 2:
        n, file = args
        issue = required_open(client, n)
        if queue.labels(issue) & {'doing', 'done', 'blocked'}:
            raise ValueError('block requires backlog or todo, no live ownership')
        client.command('api', '--method', 'POST', f'repos/{client.repo}/issues/{int(n)}/comments', payload=dict(body=body(file)))
        fresh = required_open(client, n)
        if queue.labels(fresh) & {'doing', 'done', 'blocked'}:
            raise ValueError('lifecycle changed; comment retained, labels unchanged')
        client.patch(n, labels=sorted((queue.labels(fresh) - {'todo'}) | {'blocked'}))
        return 0
    raise ValueError('usage: status | new TITLE FILE pN --milestone NUMBER | adopt N pN [--milestone NUMBER] | milestone N NUMBER | spec N FILE | prioritize N pN | block N FILE')


def main():
    # A remote inventory read neither needs nor grants the writer's guard.
    # Every mutation still enters the original exclusive path below and reads
    # its own current state; status is never an admission/ownership receipt.
    if sys.argv[1:] == ['status']:
        return execute(queue.Client(), ['status'])
    if sys.argv[1:2] == ['--cloud']:
        if len(sys.argv) < 3:
            raise ValueError('expected --cloud COMMAND')
        client = queue.Client(repo=os.environ['GH_REPO'])
        if sys.argv[2:] == ['status']:
            return execute(client, ['status'])
        with queue.coordination.shared_guard(client.repo, required=True):
            return execute(client, sys.argv[2:])
    common = Path(subprocess.check_output(['git', '-c', 'core.fsmonitor=false', 'rev-parse', '--git-common-dir'], text=True).strip()).resolve()
    with (common / 'issue-work.guard').open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('busy: another command owns the repository guard')
            return 3
        if len(sys.argv) < 2:
            raise ValueError('expected plan command')
        client = queue.Client()
        with queue.coordination.shared_guard(client.repo):
            return execute(client, sys.argv[1:])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        sys.exit(2)
