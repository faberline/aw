"""Guarded release adapter. Read trusted data; never execute product programs."""
import base64
import fnmatch
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


def load(name, path=None):
    spec = importlib.util.spec_from_file_location('aw_release_' + name,
                                                path or Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = load('release_state')
tests = load('test_plan', Path(__file__).resolve().parents[2] / 'issue-ci/scripts/test_plan.py')
development = load('development', Path(__file__).resolve().parents[2] / 'issue-ci/scripts/development_pipeline.py')
require = state.require


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


class Provider:
    def __init__(self, repo):
        require(isinstance(repo, str) and re.fullmatch(r'[\w.-]+/[\w.-]+', repo), 'invalid repository')
        self.repo = repo

    def request(self, path, method='GET', data=None, optional=False):
        require(path.startswith('repos/' + self.repo + '/') or path == 'repos/' + self.repo, 'foreign API request')
        require('..' not in path and '://' not in path, 'invalid API path')
        command = ['gh', 'api', '--method', method, path]
        if data is not None:
            command += ['--input', '-']
        result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            if optional and '(HTTP 404)' in result.stderr:
                return None
            raise RuntimeError('release provider request failed: ' + method + ' ' + path)
        return strict_json(result.stdout)

    def api(self, path):
        return self.request(path)

    def optional(self, path):
        return self.request(path, optional=True)

    def pages(self, path, key=None):
        rows = []
        for page in range(1, 1001):
            data = self.api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
            batch = data.get(key) if key else data
            require(isinstance(batch, list), 'incomplete paginated inventory')
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise ValueError('release pagination exceeded; no truncated decisions')

    def write(self, path, method, data):
        root = 'repos/' + self.repo + '/'
        require((method == 'POST' and path in (root + 'git/refs', root + 'issues', root + 'releases'))
                or (method == 'POST' and re.fullmatch(re.escape(root) + r'issues/[1-9][0-9]*/comments', path))
                or (method == 'PATCH' and re.fullmatch(re.escape(root) + r'milestones/[1-9][0-9]*', path)
                    and data == {'state': 'closed'}), 'release write outside permitted lifecycle')
        return self.request(path, method, data)


def blob(provider, path, revision):
    require(re.fullmatch('[0-9a-f]{40}', revision) is not None, 'immutable source required')
    data = provider.api(f'repos/{provider.repo}/contents/{path}?ref={revision}')
    require(isinstance(data, dict) and data.get('type') == 'file' and data.get('encoding') == 'base64'
            and type(data.get('size')) is int and 0 < data['size'] <= 1048576, 'invalid source blob')
    raw = base64.b64decode(''.join(data['content'].split()), validate=True)
    require(len(raw) == data['size'] and data.get('sha') == hashlib.sha1(
        b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(), 'source blob identity differs')
    return raw, data['sha']


def workflow_accepts_tag(raw, tag):
    document = development.yaml_document(raw.decode())
    push = document.get('on', {}).get('push')
    require(isinstance(push, dict) and isinstance(push.get('tags'), list) and push['tags']
            and not {'paths', 'paths-ignore', 'tags-ignore'} & set(push), 'full tests need an unfiltered tag trigger')
    require(all(isinstance(pattern, str) and not pattern.startswith('!') for pattern in push['tags'])
            and any(fnmatch.fnmatchcase(tag, pattern) for pattern in push['tags']), 'tag is not accepted by full tests')


def full_evidence(provider, policy, tag, head):
    root = 'repos/' + provider.repo
    # The run API reports head_branch, so reject an ambiguous same-name branch.
    require(provider.optional(root + '/git/ref/heads/' + quote(tag, safe='')) is None,
            'tag name is ambiguous with a branch')
    result = dict(status='success', reasons=[], failures=[], runs=[])
    for workflow in policy['workflows']:
        identity = policy['qualification']['trusted_workflows'][workflow['path']]
        raw, sha = blob(provider, workflow['path'], head)
        require(sha == identity['definition_sha'], 'unreviewed tagged workflow definition')
        workflow_accepts_tag(raw, tag)
        rows = provider.pages(f'{root}/actions/workflows/{identity["id"]}/runs?head_sha={head}&event=push', 'workflow_runs')
        ids = [row.get('id') for row in rows]
        require(all(type(n) is int and n > 0 for n in ids) and len(ids) == len(set(ids)), 'unstable test-run inventory')
        matches = [r for r in rows if r.get('event') == 'push' and r.get('head_branch') == tag
                   and r.get('head_sha') == head and r.get('workflow_id') == identity['id']
                   and r.get('path', '').split('@')[0] == workflow['path']
                   and r.get('head_repository', {}).get('full_name') == provider.repo]
        if not matches:
            result['reasons'].append(workflow['path'] + ': full tag run is missing')
            if result['status'] != 'failure': result['status'] = 'pending'
            continue
        run = max(matches, key=lambda r: r['id'])
        current = provider.api(f'{root}/actions/runs/{run["id"]}')
        require(all(current.get(k) == run.get(k) for k in ('id', 'workflow_id', 'head_sha', 'head_branch', 'run_attempt', 'status', 'conclusion')),
                'test run changed during verification')
        require(type(run.get('run_attempt')) is int and run['run_attempt'] > 0, 'missing test attempt')
        jobs = provider.pages(f'{root}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs', 'jobs')
        require(all(j.get('run_id') == run['id'] and j.get('run_attempt') == run['run_attempt']
                    and j.get('head_sha') == head for j in jobs), 'stale or foreign job attempt')
        result['runs'].append(dict(id=run['id'], attempt=run['run_attempt'], workflow=workflow['path'],
                                   head=head, tag=tag))
        failed = []
        for job in jobs:
            if job.get('conclusion') in state.FAILURES:
                expected = f'https://github.com/{provider.repo}/actions/runs/{run["id"]}/job/{job["id"]}'
                require(job.get('html_url') == expected, 'failure job URL differs')
                failed.append(dict(workflow=workflow['path'], run=run['id'], attempt=run['run_attempt'],
                                   job=job['id'], name=job['name'], conclusion=job['conclusion'], url=expected,
                                   steps=[s.get('name') for s in job.get('steps', []) if s.get('conclusion') in state.FAILURES]))
        if run.get('status') != 'completed':
            result['reasons'].append(workflow['path'] + ': latest attempt is running')
            if result['status'] != 'failure': result['status'] = 'pending'
            continue
        result['failures'].extend(failed)
        if failed or run.get('conclusion') in state.FAILURES:
            result['status'] = 'failure'
            result['reasons'].append(workflow['path'] + ': confirmed full-test failure')
            continue
        names = [j.get('name') for j in jobs]
        complete = (len(names) == len(set(names)) and set(names) == set(workflow['jobs'])
                    and bool(jobs) and all(j.get('status') == 'completed' and j.get('conclusion') == 'success' for j in jobs)
                    and run.get('conclusion') == 'success')
        if not complete:
            result['reasons'].append(workflow['path'] + ': missing, cancelled, skipped or unknown full-test jobs')
            if result['status'] != 'failure': result['status'] = 'pending'
    return result


def collect(provider, number):
    require(type(number) is int and number > 0, 'existing milestone number required')
    root = 'repos/' + provider.repo
    meta = provider.api(root)
    head = provider.api(root + '/commits/' + quote(meta['default_branch'], safe=''))['sha']
    release_raw, release_blob = blob(provider, '.github/aw-release.json', head)
    policy_raw, policy_blob = blob(provider, '.github/issue-ci-required-gates.json', head)
    release_config, policy = strict_json(release_raw), strict_json(policy_raw)
    plan = tests.select(policy, 'all', provider.repo, head, head)
    milestone = provider.api(f'{root}/milestones/{number}')
    item = state.contract(release_config, provider.repo, milestone, plan['checks'])
    rows = provider.pages(f'{root}/issues?state=all&milestone={number}')
    issues = [i for i in rows if 'pull_request' not in i]
    numbers = {i['number'] for i in issues}
    comparisons = {}
    def ancestor(commit, target):
        require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit), 'merged PR has no exact commit')
        if commit == target:
            return True
        if (commit, target) not in comparisons:
            comparison = provider.api(f'{root}/compare/{commit}...{target}')
            comparisons[commit, target] = (type(comparison.get('behind_by')) is int
                and comparison['behind_by'] == 0 and comparison.get('merge_base_commit', {}).get('sha') == commit)
        return comparisons[commit, target]
    pulls = []
    for pull in provider.pages(root + '/pulls?state=all'):
        related = set(map(int, re.findall(r'(?im)^\s*(?:closes|fixes|resolves)\s+#([1-9][0-9]*)\s*$', pull.get('body') or '')))
        related &= numbers
        if not related and (pull.get('milestone') or {}).get('number') != number:
            continue
        pull['_issues'] = sorted(related)
        pull['_landed'] = False
        if pull.get('merged_at'):
            sha = pull.get('merge_commit_sha')
            pull['_landed'] = ancestor(sha, head)
        pulls.append(pull)
    tags = {}
    for row in provider.pages(root + '/tags'):
        require(row['name'] not in tags, 'duplicate tag inventory')
        tags[row['name']] = row['commit']['sha']
    final = 'v' + item['version']
    candidates = sorted((int(match.group(1)), name) for name in tags
                        if (match := re.fullmatch(re.escape(final) + r'-rc\.([1-9][0-9]*)', name)))
    evidence, release_commits = {}, {}
    for tag in ([candidates[-1][1]] if candidates else []) + ([final] if final in tags else []):
        tagged_raw, tagged_policy_blob = blob(provider, '.github/issue-ci-required-gates.json', tags[tag])
        tagged_policy = strict_json(tagged_raw)
        tagged_plan = tests.select(tagged_policy, 'all', provider.repo, tags[tag], tags[tag])
        require(development.retains_workflows(tagged_policy, policy), 'current policy dropped or changed a prior release gate')
        tagged_release, tagged_release_blob = blob(provider, '.github/aw-release.json', tags[tag])
        tagged_config = strict_json(tagged_release)
        require(isinstance(tagged_config, dict) and tagged_config.get('version') == 1
                and tagged_config.get('repo') == provider.repo and isinstance(tagged_config.get('milestones'), dict),
                'invalid tagged release configuration')
        tagged_contract = tagged_config['milestones'].get(str(number))
        release_commits[tags[tag]] = dict(landed=ancestor(tags[tag], head),
            pulls=sorted(p['number'] for p in pulls if p.get('merged_at') and ancestor(p['merge_commit_sha'], tags[tag])),
            contract_sha256=state.digest(tagged_contract), plan_sha256=tagged_plan['sha256'],
            release_blob=tagged_release_blob, policy_blob=tagged_policy_blob)
        evidence[tag] = full_evidence(provider, tagged_policy, tag, tags[tag])
    if not candidates and final not in tags:
        # Validate the entry before creating a tag that cannot start full tests.
        for workflow in policy['workflows']:
            raw, sha = blob(provider, workflow['path'], head)
            require(sha == policy['qualification']['trusted_workflows'][workflow['path']]['definition_sha'], 'unreviewed release workflow')
            workflow_accepts_tag(raw, final + '-rc.1')
    publication = provider.optional(root + '/releases/tags/' + quote(final, safe='')) if final in tags else None
    result = dict(repo=provider.repo, head=head, milestone=milestone, contract=item, issues=issues,
                  pulls=pulls, tags=tags, evidence=evidence, publication=publication, release_commits=release_commits,
                  plan_sha256=plan['sha256'], release_blob=release_blob, policy_blob=policy_blob)
    require(provider.api(root + '/commits/' + quote(meta['default_branch'], safe=''))['sha'] == head,
            'default branch changed during release assessment')
    return result


def failure_issue(provider, snapshot, assessment, revalidate):
    tag = assessment['tag']
    sha = snapshot['tags'][tag]
    marker = state.repair_marker(provider.repo, snapshot['milestone']['number'], tag, sha,
                                 snapshot['evidence'][tag])
    matches = [i for i in snapshot['issues'] if marker in (i.get('body') or '')]
    require(len(matches) <= 1, 'ambiguous release repair issue')
    failures = assessment.get('failures', [])
    details = '\n'.join('- ' + f'{development.text(r["name"])}: {development.text(r["conclusion"])}; '
                        f'steps: {development.text(r["steps"])}; {r["url"]}' for r in failures)
    details = details or '\n'.join('- ' + development.text(reason) for reason in assessment['reasons'])
    note = ('<!-- aw-release-attempt:' + state.digest(failures or assessment['reasons']) + ' -->\n'
            + f'Full tests failed for `{tag}` at `{sha}`.\n\n' + details
            + '\n\nThis records observed failures. It does not claim an unverified root cause or weaken a gate.')
    root = 'repos/' + provider.repo
    if matches:
        issue = matches[0]
        comments = provider.pages(f'{root}/issues/{issue["number"]}/comments')
        if note in (issue.get('body') or '') or any(c.get('body') == note for c in comments):
            return dict(action='failure_issue', issue=issue['number'], changed=False)
        revalidate()
        response = provider.write(f'{root}/issues/{issue["number"]}/comments', 'POST', {'body':note})
        require(type(response.get('id')) is int, 'failure comment outcome unknown; inspect before retry')
        require(provider.api(f'{root}/issues/comments/{response["id"]}').get('body') == note,
                'failure comment readback differs')
        return dict(action='failure_issue', issue=issue['number'], changed=True)
    body = (marker + '\n## Goal\nRepair the full release-test failure for `' + tag + '`.\n\n'
            '## Acceptance\nPreserve all thresholds. Fix the observed failures and pass a new full-test candidate.\n\n'
            '## Never\nDo not move tags, bypass tests, change unrelated work or steal a lease.\n\n'
            '## Gate\nThis is an unspecced backlog repair. Before todo admission the planner must inspect '
            'the failing code and add exact affected unit commands in an issue-plan spec comment. '
            'The new candidate must also pass the unchanged full release pipeline.\n\n' + note)
    revalidate()
    response = provider.write(root + '/issues', 'POST', dict(title='Release repair: ' + tag, body=body,
                              milestone=snapshot['milestone']['number'], labels=['priority:p0']))
    require(type(response.get('number')) is int, 'repair issue outcome unknown; inspect before retry')
    fresh = provider.api(f'{root}/issues/{response["number"]}')
    require(fresh.get('body') == body and fresh.get('state') == 'open'
            and (fresh.get('milestone') or {}).get('number') == snapshot['milestone']['number'], 'repair issue readback differs')
    return dict(action='failure_issue', issue=response['number'], changed=True)


def advance(provider, number, expected):
    snapshot = collect(provider, number)
    assessment = state.evaluate(snapshot)
    require(isinstance(expected, str) and expected == assessment['fingerprint'], 'release inputs changed; assess again')
    def fresh():
        require(state.digest(collect(provider, number)) == expected, 'release changed before write')
    action = assessment['action']
    root = 'repos/' + provider.repo
    if action is None and assessment['state'] == 'work':
        failed = [tag for tag, evidence in snapshot['evidence'].items() if evidence['status'] == 'failure']
        if failed:
            tag = sorted(failed)[-1]
            evidence = snapshot['evidence'][tag]
            reported = failure_issue(provider, snapshot, dict(tag=tag, failures=evidence.get('failures', []),
                                      reasons=evidence.get('reasons', [])), fresh)
            return dict(assessment, repair=reported)
    if action is None:
        return assessment
    if action == 'failure_issue':
        return failure_issue(provider, snapshot, assessment, fresh)
    fresh()
    if action == 'tag':
        tag = assessment['tag']
        release_head = assessment['release_head']
        require(tag not in snapshot['tags'], 'tag exists; never overwrite')
        response = provider.write(root + '/git/refs', 'POST', dict(ref='refs/tags/' + tag, sha=release_head))
        require(response.get('ref') == 'refs/tags/' + tag and response.get('object', {}).get('sha') == release_head,
                'tag write outcome unknown; inspect before retry')
        actual = provider.api(root + '/git/ref/tags/' + quote(tag, safe=''))
        require(actual.get('object', {}).get('sha') == release_head, 'tag readback differs')
    elif action == 'publish':
        response = provider.write(root + '/releases', 'POST', dict(tag_name=assessment['tag'],
                                  draft=False, prerelease=False, generate_release_notes=True))
        require(response.get('tag_name') == assessment['tag'] and response.get('published_at'), 'publication outcome unknown')
        actual = provider.optional(root + '/releases/tags/' + quote(assessment['tag'], safe=''))
        require(isinstance(actual, dict) and actual.get('tag_name') == assessment['tag']
                and actual.get('draft') is False and actual.get('prerelease') is False
                and bool(actual.get('published_at')), 'publication readback differs')
    elif action == 'close':
        response = provider.write(f'{root}/milestones/{number}', 'PATCH', {'state':'closed'})
        require(response.get('state') == 'closed' and provider.api(f'{root}/milestones/{number}').get('state') == 'closed',
                'milestone closure outcome unknown')
    else:
        raise ValueError('unknown release action')
    return dict(action=action, milestone=number, head=snapshot['head'], release_head=assessment['release_head'],
                tag=assessment.get('tag'), verified=True)


def preflight(provider, tag, head, number=None):
    require(isinstance(tag, str) and re.fullmatch('v' + state.VERSION + r'(?:-rc\.[1-9][0-9]*)?', tag), 'invalid release tag')
    require(isinstance(head, str) and re.fullmatch('[0-9a-f]{40}', head), 'exact release commit required')
    if number is None:
        root = 'repos/' + provider.repo
        branch = provider.api(root)['default_branch']
        source = provider.api(root + '/commits/' + quote(branch, safe=''))['sha']
        raw, _ = blob(provider, '.github/aw-release.json', source)
        config = strict_json(raw)
        candidates = [int(n) for n, item in config.get('milestones', {}).items()
                      if item.get('version') == tag.removeprefix('v').split('-rc.')[0]]
        require(len(candidates) == 1, 'release version has no unique existing milestone')
        number = candidates[0]
    snapshot = collect(provider, number)
    require(snapshot['tags'].get(tag) == head and state.covers_release(snapshot, head),
            'tag or frozen goal delivery changed')
    assessment = state.evaluate(dict(snapshot, milestone=dict(snapshot['milestone'], state='open')))
    require(assessment['state'] in ('testing', 'ready_final', 'ready_publish', 'ready_close')
            and assessment.get('tag') in (tag, 'v' + snapshot['contract']['version']),
            'milestone work or release inputs are not ready: ' + '; '.join(assessment['reasons']))
    source = snapshot['release_commits'][head]
    return dict(schema='AW_RELEASE_PREFLIGHT_V1', status='success', mode='all', repo=provider.repo,
                head=head, base=head, tag=tag, milestone=number,
                plan_sha256=source['plan_sha256'], release_blob=source['release_blob'],
                policy_blob=source['policy_blob'], qualification='not_evaluated')


def arguments(command, args):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--milestone', type=int, required=True)
    if command == 'release-advance':
        parser.add_argument('--expected', required=True)
    values = parser.parse_args(args)
    require(values.milestone > 0, 'existing positive milestone number required')
    if command == 'release-advance':
        require(re.fullmatch('[0-9a-f]{64}', values.expected) is not None, 'invalid assessment fingerprint')
    return values


def execute(client, command, args):
    values = arguments(command, args)
    provider = Provider(client.repo)
    if command == 'release-status':
        result = state.evaluate(collect(provider, values.milestone))
    else:
        result = advance(provider, values.milestone, values.expected)
    print(json.dumps(result, sort_keys=True))
    return 0
