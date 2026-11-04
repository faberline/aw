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


def review_records(repo, issue, pr, head, base, actor):
    marker = f'<!-- issue-review:{issue}:{head}:{base} -->'
    found = []
    for page in range(1, 1001):
        comments = api(f'repos/{repo}/issues/{pr}/comments?per_page=100&page={page}')
        if not isinstance(comments, list):
            raise RuntimeError('review comment inventory invalid')
        for comment in comments:
            if comment['body'].startswith(marker) and comment['user']['login'] == actor:
                found.append(comment['body'])
        if len(comments) < 100:
            return found
    raise RuntimeError('review receipt pagination limit exceeded')


def seen(repo, issue, pr, head, base, actor):
    return bool(review_records(repo, issue, pr, head, base, actor))


def record(repo, issue, pr, head, base, outcome, text, actor, policy_source):
    document = policy_source()
    if document.get('source_commit') != base:
        raise RuntimeError('review base is not the current trusted policy source')
    settings = document['config'].get('local_review_feedback')
    if settings is not None:
        identity = api('user')
        reviewers = settings.get('reviewer_ids') if isinstance(settings, dict) else None
        if (not isinstance(reviewers, list) or not isinstance(identity, dict)
                or identity.get('login') != actor or type(identity.get('id')) is not int
                or identity['id'] not in reviewers):
            raise RuntimeError('current reviewer is not authorized by trusted policy')
    marker = f'<!-- issue-review:{issue}:{head}:{base} -->'
    body = f'{marker}\nReview: {outcome} head {head} base {base}.\n{text}\nLifecycle unchanged; no merge.'
    records = review_records(repo, issue, pr, head, base, actor)
    already_recorded = body in records
    for previous in records:
        if previous == body:
            continue
        lines = previous.splitlines()
        if len(lines) < 2:
            raise RuntimeError('existing review receipt is malformed')
        match = re.fullmatch(r'Review: (pass|fail|block) head ' + head + ' base ' + base + r'\.', lines[1])
        if not match:
            raise RuntimeError('existing review receipt is malformed')
        if match.group(1) == outcome:
            raise RuntimeError('different evidence already recorded for this outcome; inspect before retry')
        if outcome == 'pass' and match.group(1) == 'fail':
            raise RuntimeError('a failed review exists for this head and base; submit a new revision')
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'issue-ci/scripts/qualification.py'
    spec = importlib.util.spec_from_file_location('review_receipt_qualification', path)
    qualification = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qualification)
    default = api(f'repos/{repo}')['default_branch']
    def check_failures():
        if policy_source() != document:
            raise RuntimeError('trusted policy changed; review again')
        if outcome != 'pass' or settings is None:
            return
        feedback_path = path.with_name('development_pipeline.py')
        feedback_spec = importlib.util.spec_from_file_location('review_receipt_feedback', feedback_path)
        feedback = importlib.util.module_from_spec(feedback_spec)
        feedback_spec.loader.exec_module(feedback)
        comments = []
        for page in range(1, 1001):
            chunk = api(f'repos/{repo}/issues/{pr}/comments?per_page=100&page={page}')
            if not isinstance(chunk, list):
                raise RuntimeError('review comment inventory invalid')
            comments.extend(chunk)
            if len(chunk) < 100:
                break
        else:
            raise RuntimeError('review receipt pagination limit exceeded')
        if feedback.local_review_failure(repo, int(issue), int(pr), head, base, comments, document['config']):
            raise RuntimeError('a failed review exists for this head and base; submit a new revision')
    def validate_pr(current):
        if (current.get('state') != 'open' or current.get('head', {}).get('sha') != head
                or current.get('base', {}).get('sha') != base or current.get('base', {}).get('ref') != default
                or qualification.linked_issue(current) != int(issue)
                or any(current.get(side, {}).get('repo', {}).get('full_name') != repo for side in ('head', 'base'))):
            raise RuntimeError('PR head, base or linked issue changed; review again')
    current = api(f'repos/{repo}/pulls/{pr}')
    labels = api(f'repos/{repo}/issues/{issue}')
    validate_pr(current)
    names = {l['name'] for l in labels['labels']}
    if (labels.get('number') != int(issue) or labels.get('url') != f'https://api.github.com/repos/{repo}/issues/{issue}'
            or labels.get('repository_url') != f'https://api.github.com/repos/{repo}' or 'pull_request' in labels
            or labels['state'] != 'open' or names & {'todo', 'doing', 'blocked'} or 'done' not in names):
        raise RuntimeError('review requires an open exclusively done issue')
    current = api(f'repos/{repo}/pulls/{pr}')
    validate_pr(current)
    check_failures()
    if already_recorded:
        print('this review evidence was already recorded')
        return 0
    api(f'repos/{repo}/issues/{pr}/comments', {'body': body})
    return 0


def notify_failure(repo, issue, pr, head, base, policy_source, required=None):
    """Wake the CI-owned writer for a verified failure; never change labels."""
    document = policy_source()
    config = document['config']
    if 'local_review_feedback' not in config:
        raise RuntimeError('local review feedback is not enabled; receipt retained, CI handoff pending')
    import importlib.util
    folder = Path(__file__).resolve().parents[2] / 'issue-ci/scripts'
    def load(name):
        spec = importlib.util.spec_from_file_location('review_feedback_' + name, folder / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    development, qualification = load('development_pipeline'), load('qualification')
    target = api(f'repos/{repo}/pulls/{pr}')
    linked = qualification.linked_issue(target)
    if (target.get('state') != 'open' or target.get('head', {}).get('sha') != head
            or target.get('base', {}).get('sha') != base or linked != int(issue)
            or any(target.get(side, {}).get('repo', {}).get('full_name') != repo for side in ('head', 'base'))):
        raise RuntimeError('review failure target changed; no CI wake')
    def comments():
        rows = []
        for page in range(1, 1001):
            chunk = api(f'repos/{repo}/issues/{pr}/comments?per_page=100&page={page}')
            if not isinstance(chunk, list):
                raise RuntimeError('review comment inventory is invalid')
            rows.extend(chunk)
            if len(chunk) < 100:
                return rows
        raise RuntimeError('review comment inventory exceeds pagination bound')
    def read_feedback():
        rows = comments()
        feedback = development.local_review_failure(repo, int(issue), int(pr), head, base, rows, config)
        if required is not None:
            matches = [row for row in rows if row.get('body') == required['body']
                       and isinstance(row.get('user'), dict) and row['user'].get('id') == required['author_id']]
            if len(matches) != 1:
                raise RuntimeError('required failure receipt is missing or changed; CI handoff pending')
        return feedback
    feedback = read_feedback()
    if feedback is None:
        if required is not None:
            raise RuntimeError('required failure receipt is not trusted; CI handoff pending')
        return 0
    if policy_source() != document or api(f'repos/{repo}/pulls/{pr}') != target:
        raise RuntimeError('policy or PR changed before CI wake')
    if read_feedback() != feedback:
        raise RuntimeError('review evidence changed before CI wake')
    subprocess.run(['gh', 'api', '--method', 'POST', f'repos/{repo}/dispatches', '--input', '-'],
                   input=json.dumps({'event_type': 'aw-development-reconcile'}), text=True, check=True,
                   stdout=subprocess.DEVNULL)
    print('CI handoff requested for the failed local review')
    return 0


def verify_policy(path, base, policy_source):
    """Require the local CI manifest to match the exact current base policy."""
    document = policy_source()
    if (not isinstance(document, dict) or document.get('source_commit') != base
            or document.get('path') != '.github/issue-ci-required-gates.json'):
        raise RuntimeError('reviewed base is not the current trusted policy source')
    import importlib.util
    folder = Path(__file__).resolve().parents[2] / 'issue-ci/scripts'
    spec = importlib.util.spec_from_file_location('review_policy_handoff', folder / 'handoff.py')
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    local = ci.strict_json_loads(Path(path).read_text())
    if local != document.get('config'):
        raise RuntimeError('local CI manifest differs from the trusted base policy')
    ci.validate_config(local)
    if policy_source() != document:
        raise RuntimeError('trusted policy changed during verification')
    print(json.dumps(document['config'], sort_keys=True, separators=(',', ':')))
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
    if sys.argv[1:2] == ['verify-policy']:
        if len(sys.argv) != 5:
            raise RuntimeError('usage: receipt.py verify-policy REPO BASE CONFIG')
        repo, base, path = sys.argv[2:]
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not re.fullmatch('[0-9a-f]{40}', base):
            raise RuntimeError('invalid review policy target')
        import importlib.util
        action_path = Path(__file__).resolve().parents[2] / 'issue-ci/scripts/action.py'
        spec = importlib.util.spec_from_file_location('review_policy_action', action_path)
        action = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action)
        ci = action.controller()
        policy = lambda: action.trusted_policy(ci, repo, '.github/issue-ci-required-gates.json', metadata=True)
        return verify_policy(path, base, policy)
    if sys.argv[1:2] == ['index-receipt']:
        return verify_index(sys.argv[2:], named=True)
    if sys.argv[1:2] == ['verify-index']:
        return verify_index(sys.argv[2:])
    if sys.argv[1:2] == ['verify-readiness']:
        return verify_readiness(sys.argv[2:])
    command, n, pr, head, base, *rest = sys.argv[1:]
    if (command not in ('seen', 'record', 'notify', 'notify-required') or not n.isdigit() or not pr.isdigit()
            or not re.fullmatch('[0-9a-f]{40}', head) or not re.fullmatch('[0-9a-f]{40}', base)):
        raise RuntimeError('usage: receipt.py seen|record|notify|notify-required N PR HEAD BASE [arguments]')
    repo = json.loads(run('gh', 'repo', 'view', '--json', 'nameWithOwner'))['nameWithOwner']
    if command in ('notify', 'notify-required'):
        import importlib.util
        path = Path(__file__).resolve().parents[2] / 'issue-ci/scripts/action.py'
        spec = importlib.util.spec_from_file_location('review_feedback_action', path)
        action = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action)
        ci = action.controller()
        policy = lambda: action.trusted_policy(ci, repo, '.github/issue-ci-required-gates.json', metadata=True)
        required = None
        if command == 'notify-required':
            if len(rest) != 1:
                raise RuntimeError('notify-required needs the original review evidence file')
            text = Path(rest[0]).read_text()
            identity = api('user')
            if not text.strip() or not isinstance(identity, dict) or type(identity.get('id')) is not int:
                raise RuntimeError('review evidence or current identity missing')
            body = (f'<!-- issue-review:{n}:{head}:{base} -->\nReview: fail head {head} base {base}.\n'
                    f'{text}\nLifecycle unchanged; no merge.')
            required = {'body': body, 'author_id': identity['id']}
        return notify_failure(repo, n, pr, head, base, policy, required)
    actor = api('user')['login']
    common = Path(run('git', 'rev-parse', '--git-common-dir')).resolve()
    with (common / 'issue-review.guard').open('a') as guard:
        # Local reviewers serialize receipt check+write; receipt survives session
        # restarts and is verified against the authenticated reviewer identity.
        fcntl.flock(guard, fcntl.LOCK_EX)
        if command == 'seen':
            return 0 if seen(repo, n, pr, head, base, actor) else 1
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
        import importlib.util
        action_path = Path(__file__).resolve().parents[2] / 'issue-ci/scripts/action.py'
        action_spec = importlib.util.spec_from_file_location('review_record_action', action_path)
        action = importlib.util.module_from_spec(action_spec)
        action_spec.loader.exec_module(action)
        ci = action.controller()
        policy = lambda: action.trusted_policy(ci, repo, '.github/issue-ci-required-gates.json', metadata=True)
        return record(repo, n, pr, head, base, outcome, text, actor, policy)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        sys.exit(2)
