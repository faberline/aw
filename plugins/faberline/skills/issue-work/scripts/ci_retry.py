"""Do not use a previous PR head's CI handoff as a current retry."""
import base64
import json
import re

GITHUB_ACTIONS_APP_ID = 15368


def trusted_policy(client, base):
    meta = client.api(f'repos/{client.repo}/contents/.github/issue-ci-required-gates.json?ref={base}')
    try:
        if meta.get('type') != 'file' or meta.get('encoding') != 'base64':
            return None
        config = json.loads(base64.b64decode(''.join(meta['content'].split()), validate=True))
        return config['qualification']['trusted_workflows']
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def trusted_workflow(client, identity, path, base, head):
    if (not isinstance(identity, dict) or type(identity.get('id')) is not int
            or not isinstance(identity.get('definition_sha'), str)
            or not re.fullmatch('[0-9a-f]{40}', identity['definition_sha'])
            or not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', path)):
        return False
    return all(client.api(f'repos/{client.repo}/contents/{path}?ref={ref}').get('sha') == identity['definition_sha']
               for ref in (base, head))


def active_required(client, pull, checks):
    head, base = pull['head']['sha'], pull['base']['sha']
    trusted = trusted_policy(client, base)
    if not isinstance(trusted, dict):
        return False
    for check in checks:
        if check.get('status') not in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
            continue
        if (check.get('app') or {}).get('id') != GITHUB_ACTIONS_APP_ID:
            continue
        job = client.api(f"repos/{client.repo}/actions/jobs/{check['id']}")
        run_id = job.get('run_id')
        if type(run_id) is not int or run_id <= 0:
            continue
        run = client.api(f'repos/{client.repo}/actions/runs/{run_id}')
        path = run.get('path')
        identity = trusted.get(path)
        if (not isinstance(path, str) or not trusted_workflow(client, identity, path, base, head)):
            continue
        associations = run.get('pull_requests')
        if (run.get('id') == run_id and run.get('workflow_id') == identity['id']
                and run.get('head_sha') == head and run.get('event') == 'pull_request'
                and run.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested')
                and type(run.get('run_attempt')) is int and job.get('run_attempt') == run['run_attempt']
                and job.get('id') == check['id'] and job.get('head_sha') == head
                and isinstance(associations, list) and any(isinstance(pr, dict)
                    and pr.get('number') == pull['number'] and pr.get('head', {}).get('sha') == head
                    and pr.get('base', {}).get('sha') == base for pr in associations)):
            return True
    return False


def complete_checks(client, head):
    checks = []
    expected = None
    for page in range(1, 1001):
        payload = client.api(f'repos/{client.repo}/commits/{head}/check-runs?per_page=100&page={page}')
        if (not isinstance(payload, dict) or type(payload.get('total_count')) is not int
                or not isinstance(payload.get('check_runs'), list)):
            return None
        if expected is None:
            expected = payload['total_count']
        if payload['total_count'] != expected:
            return None
        batch = payload['check_runs']; checks.extend(batch)
        if len(batch) < 100:
            break
    else:
        return None
    if not checks or len(checks) != expected:
        return None
    for check in checks:
        if (not isinstance(check, dict) or type(check.get('id')) is not int
                or check.get('head_sha') != head or not isinstance(check.get('name'), str)
                or type((check.get('app') or {}).get('id')) is not int):
            return None
    if len({check['id'] for check in checks}) != len(checks):
        return None
    return checks


def manual_retry_ready(client, issue_number, pr_number, head, base, comments, journals):
    events = client.pages(f'repos/{client.repo}/issues/{issue_number}/events')
    if not isinstance(events, list):
        return False
    changes = [event for event in events if isinstance(event, dict)
               and event.get('event') in ('labeled', 'unlabeled')
               and (event.get('label') or {}).get('name') == 'todo']
    if not changes or any(type(event.get('id')) is not int or not isinstance(event.get('created_at'), str)
                          for event in changes):
        return False
    last = max(changes, key=lambda event: (event['created_at'], event['id']))
    actor = last.get('actor') or {}
    if (last['event'] != 'labeled' or actor.get('type') != 'User' or not isinstance(actor.get('login'), str)
            or (journals and last['created_at'] <= max(journals)[0])):
        return False
    exact = f'Work requested for PR #{pr_number} head `{head}` base `{base}`.'
    matches = [comment for comment in comments if isinstance(comment, dict) and type(comment.get('id')) is int
               and (comment.get('user') or {}).get('login') == actor['login']
               and comment.get('body') == exact and isinstance(comment.get('created_at'), str)
               and comment['created_at'] >= last['created_at']]
    if not matches:
        return False
    checks = complete_checks(client, head)
    if checks is None or not all(check.get('status') == 'completed' for check in checks):
        return False
    return {'kind': 'request', 'event': last['id'], 'comment': max(comment['id'] for comment in matches)}


def retry_ready(client, issue_number, pr_number):
    pull = client.api(f'repos/{client.repo}/pulls/{pr_number}')
    head = pull.get('head', {}).get('sha')
    base = pull.get('base', {}).get('sha')
    if (pull.get('number') != pr_number or pull.get('state') != 'open'
            or not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head)
            or not isinstance(base, str) or not re.fullmatch('[0-9a-f]{40}', base)
            or pull.get('base', {}).get('ref') != 'main'
            or pull.get('head', {}).get('ref') not in (f'issue/{issue_number}', f'agy/issue-{issue_number}')
            or not re.search(rf'(?m)^Closes #{issue_number}[ \t\r]*$', pull.get('body') or '')
            or (pull.get('head', {}).get('repo') or {}).get('full_name') != client.repo
            or (pull.get('base', {}).get('repo') or {}).get('full_name') != client.repo):
        return False
    body = pull.get('body') or ''
    if not isinstance(body, str) or re.findall(r'(?m)^Closes #([1-9][0-9]*)[ \t\r]*$', body) != [str(issue_number)]:
        return False
    rest = re.sub(r'(?m)^Closes #[1-9][0-9]*[ \t\r]*$', '', body)
    if re.search(r'(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[ \t\r\n]*:?[ \t\r\n]*(?:#[0-9]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+|https?://)', rest):
        return False
    comments = client.comments(issue_number)
    if not isinstance(comments, list):
        return False
    pattern = re.compile(r'\A<!-- issue-ci:[0-9a-f]{64}:applied -->\s*\nCI: todo for PR #([1-9][0-9]*) head `([0-9a-f]{40})`\.')
    journals = []
    for comment in comments:
        if not isinstance(comment, dict) or (comment.get('user') or {}).get('login') != 'github-actions[bot]':
            continue
        body = comment.get('body')
        match = pattern.match(body) if isinstance(body, str) else None
        if (isinstance(body, str) and 'issue-ci:' in body
                and re.search(rf'\bCI: todo for PR #{pr_number}\b', body) and match is None):
            return False
        if match and int(match[1]) == pr_number:
            if type(comment.get('id')) is not int or not isinstance(comment.get('updated_at'), str):
                return False
            journals.append((comment['updated_at'], comment['id'], match[2], body))
    request = manual_retry_ready(client, issue_number, pr_number, head, base, comments, journals)
    if request:
        return request
    if not journals or max(journals)[2] != head:
        return False
    journal = max(journals)[3]
    run_links = set(re.findall(r'https://github\.com/' + re.escape(client.repo) + r'/actions/runs/([1-9][0-9]*)', journal))
    if not run_links:
        return False
    # Bind the journal to a current failure reported by the trusted writer,
    # with exact PR/head/base and run attempt. A generic check failure alone
    # cannot make an old todo actionable.
    report = re.compile(rf'\A<!-- aw-job-failure:[0-9a-f]{{64}} -->\s*\nCI job failed for PR #{pr_number}\..*?'
                        rf'\n- Head: `{head}`; base: `{base}`\..*?'
                        r'\n- Workflow: ([^;]+); run ([1-9][0-9]*), attempt ([1-9][0-9]*), job ([1-9][0-9]*)\.', re.S)
    reports = [report.match(c.get('body', '')) for c in comments
               if isinstance(c, dict) and (c.get('user') or {}).get('login') == 'github-actions[bot]'
               and isinstance(c.get('body'), str)]
    reports = [match for match in reports if match]
    if not reports:
        return False
    trusted = trusted_policy(client, base)
    if not isinstance(trusted, dict):
        return False
    failure = {'failure', 'timed_out', 'startup_failure', 'action_required'}
    valid_jobs = {}
    for match in reports:
        path, run_id, attempt, job_id = match.groups()
        if run_id not in run_links:
            continue
        identity = trusted.get(path)
        if not trusted_workflow(client, identity, path, base, head):
            continue
        run = client.api(f'repos/{client.repo}/actions/runs/{run_id}')
        job = client.api(f'repos/{client.repo}/actions/jobs/{job_id}')
        if (run.get('id') == int(run_id) and run.get('workflow_id') == identity['id']
                and run.get('path') == path and run.get('head_sha') == head
                and run.get('event') == 'pull_request' and run.get('run_attempt') == int(attempt)
                and type(run.get('run_number')) is int and run['run_number'] > 0
                and isinstance(run.get('pull_requests'), list) and any(isinstance(pr, dict)
                    and pr.get('number') == pr_number and pr.get('head', {}).get('sha') == head
                    and pr.get('base', {}).get('sha') == base for pr in run['pull_requests'])
                and run.get('status') == 'completed' and run.get('conclusion') in failure
                and job.get('id') == int(job_id) and job.get('run_id') == int(run_id)
                and job.get('run_attempt') == int(attempt) and job.get('head_sha') == head
                and isinstance(job.get('name'), str)
                and job.get('status') == 'completed' and job.get('conclusion') in failure):
            valid_jobs[int(job_id)] = (identity['id'], job['name'], run['run_number'], int(attempt), int(run_id), path)
    if not valid_jobs:
        return False
    checks = complete_checks(client, head)
    if checks is None:
        return False
    for check in checks:
        if check.get('status') != 'completed':
            return False
    for job_id, (workflow_id, name, run_number, attempt, run_id, path) in sorted(valid_jobs.items()):
        candidates = []
        for check in checks:
            if check['name'] != name or (check.get('app') or {}).get('id') != GITHUB_ACTIONS_APP_ID:
                continue
            job = client.api(f"repos/{client.repo}/actions/jobs/{check['id']}")
            if (job.get('id') != check['id'] or type(job.get('run_id')) is not int
                    or job.get('head_sha') != head or job.get('name') != name):
                return False
            run = client.api(f"repos/{client.repo}/actions/runs/{job['run_id']}")
            if run.get('workflow_id') != workflow_id:
                continue
            if (run.get('head_sha') != head or type(run.get('run_number')) is not int
                    or type(run.get('run_attempt')) is not int or job.get('run_attempt') != run['run_attempt']):
                return False
            candidates.append((run['run_number'], run['run_attempt'], check['id']))
        if (candidates and max(candidates) == (run_number, attempt, job_id)
                and any(check['id'] == job_id and check.get('conclusion') in failure for check in checks)):
            return {'kind': 'ci_failure', 'workflow': path, 'run': run_id, 'attempt': attempt,
                    'job': job_id, 'name': name, 'journal': max(journals)[1]}
    return False
