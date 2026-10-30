"""Read a merged grant as data for a later, product-side independent review.

This module does not authorize a merge.  In particular, merging the AW data
does not satisfy the separate grant-review requirement.  The product publisher
must still authenticate a review receipt before it can use the grant.
"""
import importlib.util
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import quote


spec = importlib.util.spec_from_file_location(
    'merged_grant_policy', Path(__file__).with_name('policy_adoption.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
need = a.need
MARKER = 'AW_MERGED_GRANT_REVIEW_V1 '
NONPASS_MARKER = 'AW_MERGED_GRANT_REVIEW_NONPASS_V1 '


class ReviewNotPassed(RuntimeError):
    def __init__(self, evidence):
        super().__init__('merged_grant_review_not_pass')
        self.evidence = evidence


def document(raw):
    value = a.ci.strict_json_loads(raw)
    need(isinstance(value, dict) and set(value) == {'version', 'grants'}
         and type(value['version']) is int and value['version'] == 1
         and isinstance(value['grants'], list), 'merged_grant_document')
    for grant in value['grants']:
        a.grant_spec(grant)
    return value


def merged_data(api, repo, number, head, evaluator):
    """Bind a squash-merged, data-only grant to AW's current default branch."""
    need(a.review.integer(number) and a.review.sha(head) and a.review.sha(evaluator),
         'merged_grant_input')
    root = 'repos/' + a.GRANTS_REPO
    pull = api.api(f'{root}/pulls/{number}')
    need(pull.get('number') == number and pull.get('state') == 'closed'
         and pull.get('merged') is True and pull.get('head', {}).get('sha') == head
         and pull.get('head', {}).get('repo', {}).get('full_name') == a.GRANTS_REPO
         and pull.get('base', {}).get('repo', {}).get('full_name') == a.GRANTS_REPO
         and pull.get('base', {}).get('ref') == 'main'
         and a.review.sha(pull.get('merge_commit_sha')), 'merged_grant_pr_identity')
    merge = pull['merge_commit_sha']
    commit = api.api(f'{root}/commits/{merge}')
    parents = commit.get('parents') if isinstance(commit, dict) else None
    need(isinstance(commit, dict) and commit.get('sha') == merge
         and isinstance(parents, list) and len(parents) == 1 and isinstance(parents[0], dict)
         and a.review.sha(parents[0].get('sha')), 'merged_grant_squash_commit')
    parent = parents[0]['sha']
    main = api.api(f'{root}/commits/main').get('sha')
    need(a.review.sha(main), 'merged_grant_default')
    compare = api.api(f'{root}/compare/{merge}...{main}')
    need(compare.get('status') in ('identical', 'ahead'), 'merged_grant_not_in_default')
    files = api.pages(f'{root}/pulls/{number}/files')
    _, inventory = a.file_inventory(pull, files)
    need(len(inventory) == 1 and inventory[0]['path'] == a.GRANTS_PATH
         and inventory[0]['status'] == 'modified', 'merged_grant_only_data')
    blob = inventory[0]['sha']
    merged_files = api.pages(f'{root}/commits/{merge}', 'files')
    need(isinstance(merged_files, list) and len(merged_files) == 1
         and merged_files[0].get('filename') == a.GRANTS_PATH
         and merged_files[0].get('status') == 'modified' and merged_files[0].get('sha') == blob,
         'merged_grant_commit_scope')
    proposed = document(a.review.blob(api, a.GRANTS_REPO, a.GRANTS_PATH, head, blob))
    merged = document(a.review.blob(api, a.GRANTS_REPO, a.GRANTS_PATH, merge, blob))
    previous = document(a.review.blob(api, a.GRANTS_REPO, a.GRANTS_PATH, parent))
    current = document(a.review.blob(api, a.GRANTS_REPO, a.GRANTS_PATH, main))
    need(proposed == merged, 'merged_grant_merge_changed')
    matches = [g for g in merged['grants'] if g['repo'] == repo and g['evaluator'] == evaluator]
    need(len(matches) == 1 and 'grant_review' not in matches[0], 'merged_grant_candidate')
    grant = matches[0]
    need(not any(g['repo'] == repo and g['evaluator'] == evaluator for g in previous['grants'])
         and [g for g in merged['grants'] if g is not grant] == previous['grants'],
         'merged_grant_scope')
    need([g for g in current['grants'] if g['repo'] == repo and g['evaluator'] == evaluator] == [grant],
         'merged_grant_current_changed')
    issue_number = a.review.linked_issue(pull)
    issue = api.api(f'{root}/issues/{issue_number}')
    need(issue.get('number') == issue_number and issue.get('state') in ('open', 'closed')
         and a.review.text(issue.get('title'), 200) and a.review.text(issue.get('body'), 65536),
         'merged_grant_issue')
    # Refuse data assembled across a default-branch update.
    need(api.api(f'{root}/commits/main').get('sha') == main, 'merged_grant_default_changed')
    return dict(grant=grant, grant_pr=dict(number=number, head=head, merge=merge, parent=parent,
                default=main, blob=blob, title=pull.get('title'), body=pull.get('body')),
                grant_issue={k: issue[k] for k in ('number', 'title', 'body')})


def packet(api, repo, number, head, evaluator):
    """Bind merged grant data to the current, still-open product PR."""
    data = merged_data(api, repo, number, head, evaluator)
    grant = data['grant']
    proof = None
    try:
        verified = a.evaluate(api, grant, a.r.job_log)
    except a.review.Refused as error:
        if str(error) != 'guarded_adoption_not_ready':
            raise
        # Reading a guarded candidate is allowed only after the same rollout
        # proof required by the target review and the publisher. This performs
        # no writes and does not grant the reviewer a guard or merge authority.
        candidate = a.ci.strict_json_loads(a.review.blob(
            api, repo, '.github/issue-ci-required-gates.json', grant['head'], grant['policy_blob']))
        recovery = a.review.sibling('recovery_entry')
        recovery.verify_rollout(api, repo, dict(config=candidate, source_commit=grant['head']))
        proof = a._GUARDED_POLICY_PROOF
        verified = a._evaluate_guarded(api, grant, a.r.job_log)
    a.latest_target_review(api, grant)
    policy = a.candidate_policy(api, grant, proof=proof)
    target_issue = api.api(f"repos/{repo}/issues/{grant['issue']}")
    target_pull = api.api(f"repos/{repo}/pulls/{grant['pr']}")
    # Recheck AW data after product reads to avoid mixing two source states.
    need(merged_data(api, repo, number, head, evaluator) == data, 'merged_grant_data_changed')
    return dict(**data, target_issue={k: target_issue[k] for k in ('number', 'title', 'body')},
                target_pr={k: target_pull.get(k) for k in ('number', 'title', 'body')},
                candidate_policy=policy, verified_target=verified)


def produce(api, repo, number, head, evaluator, token, invoke):
    """Review immutable data; return a receipt only if every input stays stable."""
    before = packet(api, repo, number, head, evaluator)
    result = invoke(before, token)
    after = packet(api, repo, number, head, evaluator)
    need(before == after, 'merged_grant_review_inputs_changed')
    need(a.review.exact(result, 'decision summary findings uncertainties')
         and result['decision'] in ('pass', 'fail', 'block') and a.review.text(result['summary'], 4000)
         and isinstance(result['findings'], list) and isinstance(result['uncertainties'], list)
         and all(a.review.text(x, 4000) for x in result['findings'] + result['uncertainties']),
         'merged_grant_review_not_pass')
    source = before['grant_pr']
    if result['decision'] != 'pass' or result['findings'] or result['uncertainties']:
        grant = before['grant']
        raise ReviewNotPassed(dict(version=1, repo=repo, target_pr=grant['pr'], issue=grant['issue'],
            target_head=grant['head'], target_base=grant['base'], grant_pr=number, grant_head=head,
            grant_merge=source['merge'], grant_default=source['default'], grant_blob=source['blob'],
            grant_digest=a.r.digest(grant), pr_sha256=grant['pr_sha256'], issue_sha256=grant['issue_sha256'],
            packet_sha256=a.r.digest(before), decision=result['decision'], summary=result['summary'],
            findings=result['findings'], uncertainties=result['uncertainties']))
    return dict(version=1, repo=repo, target_pr=before['grant']['pr'],
                target_head=before['grant']['head'], grant_pr=number, grant_head=head,
                grant_merge=source['merge'], grant_parent=source['parent'],
                grant_default=source['default'], grant_blob=source['blob'],
                grant_pr_sha256=a.r.digest([source['title'], source['body']]),
                grant_issue_sha256=a.r.digest(before['grant_issue']),
                grant_digest=a.r.digest(before['grant']), packet_sha256=a.r.digest(before),
                decision='pass', findings=[], uncertainties=[])


def workflow_contract(raw, spec, evaluator):
    """Require one read-only review and one serialized outcome writer."""
    a.r.literal_uses(raw)
    import yaml
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    expected_job = {'runs-on': 'ubuntu-latest', 'timeout-minutes': '70',
                    'permissions': {'actions': 'read', 'contents': 'read', 'pull-requests': 'read', 'issues': 'read'},
                    'steps': [{'uses': 'faberline/aw/.github/actions/policy-adoption-merged-grant-review@' + evaluator,
                               'with': {'grant-pr': str(spec['grant_pr']), 'grant-head': spec['grant_head'],
                                        'claude-token': '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}}]}
    outcome_job = {'needs': 'review', 'if': 'always()', 'runs-on': 'ubuntu-latest', 'timeout-minutes': '30',
                   'permissions': {'actions': 'read', 'contents': 'read', 'pull-requests': 'read',
                                   'issues': 'write', 'statuses': 'write'},
                   'steps': [{'uses': 'faberline/aw/.github/actions/policy-adoption-merged-grant-outcome@' + evaluator,
                              'with': {'grant-pr': str(spec['grant_pr']), 'grant-head': spec['grant_head'],
                                       'target-pr': str(spec['target_pr']), 'target-head': spec['target_head'],
                                       'review-run': '${{ github.run_id }}'}}]}
    need(workflow == {'name': 'Review merged product grant',
                      'on': {'push': {'branches': [spec['branch']], 'paths': [spec['workflow_path']]}},
                      'permissions': {}, 'concurrency': {'group': 'issue-ci-handoff', 'cancel-in-progress': 'false'},
                      'jobs': {'review': expected_job, 'outcome': outcome_job}}, 'merged_grant_workflow')


def verify(api, repo, evaluator, attestation, log_reader, merged=False):
    """Authenticate a product-side review; merged mode only observes recovery evidence."""
    fields = ('grant_pr grant_head target_pr target_head workflow_path workflow_id workflow_blob source_commit branch '
              'run_id attempt job_id')
    need(type(merged) is bool and isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
         and a.review.exact(attestation, fields) and a.review.sha(evaluator)
         and all(a.review.integer(attestation[k]) for k in ('grant_pr', 'target_pr', 'workflow_id', 'run_id', 'attempt', 'job_id'))
         and all(a.review.sha(attestation[k]) for k in ('grant_head', 'target_head', 'workflow_blob', 'source_commit'))
         and a.valid_path(attestation['workflow_path'])
         and attestation['workflow_path'].startswith('.github/workflows/')
         and isinstance(attestation['branch'], str)
         and re.fullmatch(r'codex/[A-Za-z0-9._/-]+', attestation['branch'])
         and '..' not in attestation['branch'], 'merged_grant_attestation')
    spec = attestation
    raw = a.review.blob(api, repo, spec['workflow_path'], spec['source_commit'], spec['workflow_blob'])
    workflow_contract(raw, spec, evaluator)
    run = api.api(f"repos/{repo}/actions/runs/{spec['run_id']}")
    need(run.get('id') == spec['run_id'] and run.get('workflow_id') == spec['workflow_id']
         and run.get('path') == spec['workflow_path'] and run.get('head_sha') == spec['source_commit']
         and run.get('head_branch') == spec['branch'] and run.get('event') == 'push'
         and run.get('head_repository', {}).get('full_name') == repo
         and run.get('run_attempt') == spec['attempt'] and run.get('status') == 'completed'
         and run.get('conclusion') == 'success' and a.review.integer(run.get('run_number')),
         'merged_grant_review_run')
    runs_path = (f"repos/{repo}/actions/workflows/{spec['workflow_id']}/runs?branch="
                 + quote(spec['branch'], safe='') + '&event=push')
    def latest():
        runs = api.pages(runs_path, 'workflow_runs')
        need(isinstance(runs, list) and 0 < len(runs) <= 500
             and all(isinstance(item, dict) and all(a.review.integer(item.get(k))
                     for k in ('id', 'run_number', 'run_attempt')) for item in runs)
             and len({item['id'] for item in runs}) == len(runs)
             and len({item['run_number'] for item in runs}) == len(runs), 'merged_grant_review_runs')
        selected = max(runs, key=lambda item: item['run_number'])
        need(selected['id'] == spec['run_id'] and selected['run_number'] == run['run_number']
             and selected['run_attempt'] == spec['attempt'], 'merged_grant_review_not_latest')
        return sorted((item['id'], item['run_number'], item['run_attempt']) for item in runs)
    before_runs = latest()
    jobs = api.pages(f"repos/{repo}/actions/runs/{spec['run_id']}/attempts/{spec['attempt']}/jobs", 'jobs')
    reviews = [job for job in jobs if job.get('name') == 'review']
    outcomes = [job for job in jobs if job.get('name') == 'outcome']
    need(len(jobs) == 2 and len(reviews) == 1 and reviews[0].get('id') == spec['job_id']
         and reviews[0].get('status') == 'completed' and reviews[0].get('conclusion') == 'success'
         and len(outcomes) == 1 and a.review.integer(outcomes[0].get('id'))
         and outcomes[0].get('status') == 'completed' and outcomes[0].get('conclusion') == 'success',
         'merged_grant_review_job')
    rows = a.r.records(log_reader(repo, spec['job_id']), MARKER)
    need(len(rows) == 1 and isinstance(rows[0], dict), 'merged_grant_review_receipt')
    current = (merged_data(api, repo, spec['grant_pr'], spec['grant_head'], evaluator) if merged
               else packet(api, repo, spec['grant_pr'], spec['grant_head'], evaluator))
    grant = current['grant']; source = current['grant_pr']
    need(grant['pr'] == spec['target_pr'] and grant['head'] == spec['target_head'],
         'merged_grant_target_changed')
    if merged:
        a.evaluate_merged(api, grant, log_reader)
        packet_sha = rows[0].get('packet_sha256')
        need(isinstance(packet_sha, str) and re.fullmatch('[0-9a-f]{64}', packet_sha),
             'merged_grant_review_receipt')
    else:
        packet_sha = a.r.digest(current)
    expected = dict(version=1, repo=repo, target_pr=grant['pr'], target_head=grant['head'],
                    grant_pr=spec['grant_pr'], grant_head=spec['grant_head'], grant_merge=source['merge'],
                    grant_parent=source['parent'], grant_default=source['default'], grant_blob=source['blob'],
                    grant_pr_sha256=a.r.digest([source['title'], source['body']]),
                    grant_issue_sha256=a.r.digest(current['grant_issue']),
                    grant_digest=a.r.digest(grant), packet_sha256=packet_sha,
                    decision='pass', findings=[], uncertainties=[])
    need(rows == [expected], 'merged_grant_review_receipt')
    if merged:
        need(merged_data(api, repo, spec['grant_pr'], spec['grant_head'], evaluator) == current,
             'merged_grant_data_changed')
    need(latest() == before_runs, 'merged_grant_review_changed')
    fresh = api.api(f"repos/{repo}/actions/runs/{spec['run_id']}")
    keys = ('id', 'workflow_id', 'path', 'head_sha', 'head_branch', 'event', 'head_repository',
            'run_attempt', 'run_number', 'status', 'conclusion')
    need(isinstance(fresh, dict) and all(fresh.get(k) == run.get(k) for k in keys),
         'merged_grant_review_changed')
    return expected


def discover(api, grant, evaluator, log_reader, merged=False):
    """Find and authenticate the latest review on the target-specific branch.

    The grant is still inert unless a trusted publisher calls this function and
    rechecks the returned receipt before writing.  Discovery never executes the
    product branch or accepts workflow inputs as authorization.
    """
    a.grant_spec(grant)
    need(grant['evaluator'] == evaluator and 'grant_review' not in grant, 'merged_grant_discovery_input')
    repo = grant['repo']
    branch = f"codex/merged-grant-{grant['pr']}-{grant['head']}"
    path = '.github/workflows/review-merged-product-grant.yml'
    runs = api.pages(f"repos/{repo}/actions/runs?branch={quote(branch, safe='')}&event=push", 'workflow_runs')
    need(isinstance(runs, list) and len(runs) <= 500
         and all(isinstance(row, dict) and isinstance(row.get('path'), str) for row in runs),
         'merged_grant_discovery_runs')
    selected = [row for row in runs if row['path'] == path]
    need(selected and all(row.get('event') == 'push' and row.get('head_branch') == branch
                          and isinstance(row.get('head_repository'), dict)
                          and row['head_repository'].get('full_name') == repo
                          and all(a.review.integer(row.get(k)) for k in ('id', 'workflow_id', 'run_number', 'run_attempt'))
                          and a.review.sha(row.get('head_sha')) for row in selected)
         and len({row['id'] for row in selected}) == len(selected)
         and len({row['workflow_id'] for row in selected}) == 1
         and len({row['run_number'] for row in selected}) == len(selected), 'merged_grant_discovery_runs')
    run = max(selected, key=lambda row: row['run_number'])
    raw = a.review.blob(api, repo, path, run['head_sha'])
    encoded = raw.encode('utf-8')
    blob = hashlib.sha1(b'blob ' + str(len(encoded)).encode() + b'\0' + encoded).hexdigest()
    import yaml
    data = yaml.load(raw, Loader=yaml.BaseLoader)
    try:
        inputs = data['jobs']['review']['steps'][0]['with']
        pr, head = inputs['grant-pr'], inputs['grant-head']
    except (TypeError, KeyError, IndexError):
        raise a.review.Refused('merged_grant_discovery_workflow') from None
    need(isinstance(pr, str) and re.fullmatch(r'[1-9][0-9]*', pr) and a.review.sha(head),
         'merged_grant_discovery_workflow')
    jobs = api.pages(f"repos/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", 'jobs')
    reviews = [job for job in jobs if isinstance(job, dict) and job.get('name') == 'review']
    need(isinstance(jobs, list) and len(reviews) == 1 and a.review.integer(reviews[0].get('id')),
         'merged_grant_discovery_job')
    att = dict(grant_pr=int(pr), grant_head=head, target_pr=grant['pr'], target_head=grant['head'],
               workflow_path=path, workflow_id=run['workflow_id'],
               workflow_blob=blob, source_commit=run['head_sha'], branch=branch,
               run_id=run['id'], attempt=run['run_attempt'], job_id=reviews[0]['id'])
    receipt = verify(api, repo, evaluator, att, log_reader, merged=merged)
    need(receipt['grant_digest'] == a.r.digest(grant), 'merged_grant_discovery_mismatch')
    return dict(attestation=att, receipt=receipt)


def authorize(api, repo, evaluator, log_reader, grant_loader=None, merged=False):
    """Bind discovery to one unchanged, trusted default-branch grant document.

    This read-only helper does not write a status, merge a PR or close an issue.
    The caller must still enforce all guarded writer and recovery invariants.
    """
    loader = a.load_grants if grant_loader is None else grant_loader
    before = loader()
    need(isinstance(before, dict) and a.review.sha(before.get('source_commit')),
         'merged_grant_source')
    config = before.get('config')
    need(isinstance(config, dict) and set(config) == {'version', 'grants'}
         and type(config['version']) is int and config['version'] == 1
         and isinstance(config['grants'], list), 'merged_grant_document')
    grants = [a.grant_spec(value) for value in config['grants']]
    matches = [grant for grant in grants if grant['repo'] == repo and grant['evaluator'] == evaluator]
    need(len(matches) == 1 and 'grant_review' not in matches[0], 'merged_grant_missing_or_ambiguous')
    grant = matches[0]
    evidence = discover(api, grant, evaluator, log_reader, merged=merged)
    need(evidence['receipt']['grant_default'] == before['source_commit']
         and loader() == before, 'merged_grant_source_changed')
    return dict(document=before, grant=grant, evidence=evidence)


def main():
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    number = os.environ.get('INPUT_GRANT_PR', '')
    head = os.environ.get('INPUT_GRANT_HEAD', '')
    evaluator = os.environ.get('AW_ACTION_REF', '')
    need(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
         and re.fullmatch(r'[1-9][0-9]*', number) and a.review.sha(head)
         and a.review.sha(evaluator), 'merged_grant_input')
    review_spec = importlib.util.spec_from_file_location(
        'merged_grant_model', Path(__file__).with_name('policy_adoption_grant_review.py'))
    model = importlib.util.module_from_spec(review_spec)
    review_spec.loader.exec_module(model)
    try:
        receipt = produce(a.ci, repo, int(number), head, evaluator,
                          os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'), model.invoke)
    except ReviewNotPassed as error:
        print(NONPASS_MARKER + a.r.canonical(error.evidence), flush=True)
        raise SystemExit(1) from None
    print(MARKER + a.r.canonical(receipt), flush=True)


if __name__ == '__main__':
    main()
