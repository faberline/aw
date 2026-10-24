#!/usr/bin/env python3
"""Authenticated, durable per-head review receipts; no lifecycle writes."""
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def api(path, data=None):
    command = ['gh', 'api', path]
    kwargs = dict(text=True)
    if data is not None:
        command += ['--method', 'POST', '--input', '-']
        kwargs['input'] = json.dumps(data)
    return json.loads(subprocess.check_output(command, **kwargs))


def seen(repo, issue, pr, head, actor):
    marker = f'<!-- issue-review:{issue}:{head} -->'
    for page in range(1, 1001):
        comments = api(f'repos/{repo}/issues/{pr}/comments?per_page=100&page={page}')
        if any(c['body'].startswith(marker) and c['user']['login'] == actor for c in comments):
            return True
        if len(comments) < 100:
            return False
    raise RuntimeError('review receipt pagination limit exceeded')


def record(repo, issue, pr, head, outcome, text, actor):
    if seen(repo, issue, pr, head, actor):
        print('review already recorded for this head')
        return 0
    current = api(f'repos/{repo}/pulls/{pr}')
    labels = api(f'repos/{repo}/issues/{issue}')
    if current['state'] != 'open' or current['head']['sha'] != head:
        raise RuntimeError('PR head changed; review again')
    names = {l['name'] for l in labels['labels']}
    if labels['state'] != 'open' or names & {'todo', 'doing', 'blocked'} or 'done' not in names:
        raise RuntimeError('review requires an open exclusively done issue')
    api(f'repos/{repo}/issues/{pr}/comments', {'body': f'<!-- issue-review:{issue}:{head} -->\nReview: {outcome} head {head}.\n{text}\nLifecycle unchanged; no merge.'})
    return 0


def verify_readiness(arguments):
    # Separate read-only flow: no done-label requirement and no review receipt write.
    if len(arguments) != 5:
        raise RuntimeError('usage: receipt.py verify-readiness REPO PR HEAD EVALUATOR_SHA RECEIPT_JSON')
    repo, pr, head, evaluator, path = arguments
    import importlib.util
    folder = Path(__file__).resolve().parents[2] / 'issue-ci/scripts'
    def load(name):
        spec = importlib.util.spec_from_file_location('review_' + name, folder / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    readiness, ci, action = load('readiness'), load('handoff'), load('action')
    if not pr.isdigit():
        raise RuntimeError('PR must be numeric')
    document = lambda: action.trusted_policy(ci, repo, '.github/issue-ci-required-gates.json', metadata=True)
    verified = readiness.verify_file(ci, path, repo, int(pr), head, document, evaluator)
    print(readiness.canonical(verified))
    return {'ready': 0, 'wait': 3, 'fail': 2}[verified['payload']['status']]


def verify_index(arguments, named=False):
    # One bounded read-only preflight. No done label, completion writes or lease.
    if len(arguments) != (5 if named else 2):
        raise RuntimeError('usage: receipt.py verify-index REPO EVALUATOR_SHA | index-receipt REPO PR HEAD EVALUATOR_SHA RECEIPT_SHA256')
    if named:
        repo, number, head, evaluator, receipt_sha = arguments
        if not number.isdigit():
            raise RuntimeError('PR must be numeric')
    else:
        repo, evaluator = arguments
    import importlib.util
    folder = Path(__file__).resolve().parents[2] / 'issue-ci/scripts'
    def load(name):
        spec = importlib.util.spec_from_file_location('review_index_' + name, folder / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    index, ci, action = load('readiness_index'), load('handoff'), load('action')
    document = lambda: action.trusted_policy(ci, repo, '.github/issue-ci-required-gates.json', metadata=True)
    if named:
        receipt = index.named_receipt(ci, repo, int(number), head, receipt_sha, document, evaluator)
        print(index.r.canonical(receipt))
        return {'ready': 0, 'wait': 3, 'fail': 2}[receipt['payload']['status']]
    verified = index.discover(ci, repo, document, evaluator)
    result = index.summary(verified)
    print(index.r.canonical(result))
    return {'ready': 0, 'no_ready': 3, 'blocked': 2}[result['status']]


def main():
    if sys.argv[1:2] == ['index-receipt']:
        return verify_index(sys.argv[2:], named=True)
    if sys.argv[1:2] == ['verify-index']:
        return verify_index(sys.argv[2:])
    if sys.argv[1:2] == ['verify-readiness']:
        return verify_readiness(sys.argv[2:])
    command, n, pr, head, *rest = sys.argv[1:]
    if command not in ('seen', 'record') or not n.isdigit() or not pr.isdigit() or not re.fullmatch('[0-9a-f]{40}', head):
        raise RuntimeError('usage: receipt.py seen|record N PR HEAD [pass|fail|block [file]]')
    repo = json.loads(run('gh', 'repo', 'view', '--json', 'nameWithOwner'))['nameWithOwner']
    actor = api('user')['login']
    common = Path(run('git', 'rev-parse', '--git-common-dir')).resolve()
    with (common / 'issue-review.guard').open('a') as guard:
        # Local reviewers serialize receipt check+write; receipt survives session
        # restarts and is verified against the authenticated reviewer identity.
        fcntl.flock(guard, fcntl.LOCK_EX)
        if command == 'seen':
            return 0 if seen(repo, n, pr, head, actor) else 1
        if not rest or rest[0] not in ('pass', 'fail', 'block'):
            raise RuntimeError('record requires pass, fail or block')
        outcome = rest[0]
        text = 'Required current-head CI independently verified. Awaiting explicit human merge.'
        if outcome != 'pass':
            if len(rest) != 2:
                raise RuntimeError('fail/block requires evidence file')
            text = Path(rest[1]).read_text()
            if not text.strip():
                raise RuntimeError('empty evidence file')
        return record(repo, n, pr, head, outcome, text, actor)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        sys.exit(2)
