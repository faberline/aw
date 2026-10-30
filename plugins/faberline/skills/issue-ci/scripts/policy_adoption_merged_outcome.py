"""Authenticate the product-side merged-grant review outcome.

This draft does not change issue state.  A negative outcome remains blocked
until the issue worker and this writer share a recoverable ownership guard.
"""
import importlib.util
import os
from pathlib import Path
import re
from urllib.parse import quote

spec = importlib.util.spec_from_file_location(
    'merged_outcome_policy', Path(__file__).with_name('policy_adoption_merged_grant.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
a = m.a
need = a.need
OWNED_MARKER = 'AW_MERGED_OUTCOME_GUARD_OWNED_V1 '


def verify(api, repo, grant_pr, grant_head, target_pr, target_head, evaluator, run_id,
           log_reader=a.r.job_log, historical_attempt=None, *, detail_sink=None):
    """Read current or completed-attempt evidence; never authorize a write."""
    need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and a.review.sha(evaluator)
         and all(a.review.integer(x) for x in (grant_pr, target_pr, run_id))
         and a.review.sha(grant_head) and a.review.sha(target_head)
         and (historical_attempt is None or a.review.integer(historical_attempt)), 'merged_outcome_identity')
    historical = historical_attempt is not None
    run_path = (f'repos/{repo}/actions/runs/{run_id}/attempts/{historical_attempt}' if historical
                else f'repos/{repo}/actions/runs/{run_id}')
    run = api.api(run_path)
    need(isinstance(run,dict), 'merged_outcome_context')
    path, branch, source, attempt = (run.get(k) for k in ('path','head_branch','head_sha','run_attempt'))
    expected_branch = f'codex/merged-grant-{target_pr}-{target_head}'
    need(run.get('id') == run_id and a.review.integer(run.get('workflow_id'))
         and a.review.integer(run.get('run_number')) and a.review.integer(attempt)
         and run.get('event') == 'push'
         and run.get('head_repository', {}).get('full_name') == repo
         and path == '.github/workflows/review-merged-product-grant.yml' and branch == expected_branch
         and a.review.sha(source),
         'merged_outcome_context')
    if historical:
        need(attempt == historical_attempt and run.get('status') == 'completed'
             and run.get('conclusion') in ('success','failure','cancelled','timed_out'),
             'merged_outcome_context')
    else:
        need(run.get('status') == 'in_progress' and os.environ.get('GITHUB_ACTIONS') == 'true'
             and os.environ.get('GITHUB_REPOSITORY') == repo and os.environ.get('GITHUB_RUN_ID') == str(run_id)
             and os.environ.get('GITHUB_RUN_ATTEMPT') == str(attempt) and os.environ.get('GITHUB_EVENT_NAME') == 'push'
             and os.environ.get('GITHUB_SHA') == source and os.environ.get('GITHUB_WORKFLOW_SHA') == source
             and os.environ.get('GITHUB_REF') == 'refs/heads/' + branch
             and os.environ.get('GITHUB_WORKFLOW_REF') == repo + '/' + path + '@refs/heads/' + branch,
             'merged_outcome_context')
    workflow = a.review.blob(api, repo, path, source)
    m.workflow_contract(workflow, dict(grant_pr=grant_pr,grant_head=grant_head,target_pr=target_pr,
                                       target_head=target_head,workflow_path=path,branch=branch), evaluator)
    runs_path = (f"repos/{repo}/actions/workflows/{run['workflow_id']}/runs?branch="
                 + quote(branch, safe='') + '&event=push')
    runs = api.pages(runs_path, 'workflow_runs')
    need(isinstance(runs, list) and 0 < len(runs) <= 500
         and all(isinstance(row, dict) and all(a.review.integer(row.get(k))
                 for k in ('id','run_number','run_attempt')) for row in runs)
         and len({row['id'] for row in runs}) == len(runs)
         and len({row['run_number'] for row in runs}) == len(runs), 'merged_outcome_runs')
    latest = max(runs, key=lambda row: row['run_number'])
    need(latest['id'] == run_id and latest['run_number'] == run['run_number']
         and latest['run_attempt'] == attempt, 'merged_outcome_not_latest')
    jobs = api.pages(f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs')
    reviews = [job for job in jobs if job.get('name') == 'review']
    outcomes = [job for job in jobs if job.get('name') == 'outcome']
    need(len(jobs) == 2 and len(reviews) == 1 and a.review.integer(reviews[0].get('id'))
         and reviews[0].get('status') == 'completed' and reviews[0].get('conclusion') in ('success','failure')
         and len(outcomes) == 1 and a.review.integer(outcomes[0].get('id'))
         and outcomes[0]['id'] != reviews[0]['id']
         and ((not historical and outcomes[0].get('status') == 'in_progress')
              or (historical and outcomes[0].get('status') == 'completed'
                  and outcomes[0].get('conclusion') in ('success','failure','cancelled','timed_out'))),
         'merged_outcome_jobs')
    def stable():
        later=api.api(run_path)
        keys=('id','workflow_id','run_number','run_attempt','event','status','conclusion',
              'head_repository','path','head_branch','head_sha')
        need(isinstance(later,dict) and all(later.get(k)==run.get(k) for k in keys),
             'merged_outcome_changed')
        current_runs=api.pages(runs_path,'workflow_runs')
        def run_inventory(rows):
            need(isinstance(rows,list) and len(rows)<=500
                 and all(isinstance(row,dict) and all(a.review.integer(row.get(k))
                         for k in ('id','run_number','run_attempt')) for row in rows),
                 'merged_outcome_changed')
            return sorted((row['id'],row['run_number'],row['run_attempt']) for row in rows)
        need(run_inventory(current_runs)==run_inventory(runs), 'merged_outcome_changed')
        current_jobs=api.pages(f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs','jobs')
        def job_inventory(rows):
            need(isinstance(rows,list) and all(isinstance(row,dict) and a.review.integer(row.get('id'))
                 and isinstance(row.get('name'),str) for row in rows), 'merged_outcome_changed')
            return sorted((row['id'],row['name'],row.get('status'),row.get('conclusion')) for row in rows)
        need(job_inventory(current_jobs)==job_inventory(jobs), 'merged_outcome_changed')
    log = log_reader(repo, reviews[0]['id'])
    positives = a.r.records(log, m.MARKER)
    negatives = a.r.records(log, m.NONPASS_MARKER)
    current = m.packet(api, repo, grant_pr, grant_head, evaluator)
    grant, source_grant = current['grant'], current['grant_pr']
    need(grant['repo'] == repo and grant['pr'] == target_pr and grant['head'] == target_head,
         'merged_outcome_target')
    if reviews[0]['conclusion'] == 'success':
        expected = dict(version=1,repo=repo,target_pr=target_pr,target_head=target_head,
                        grant_pr=grant_pr,grant_head=grant_head,grant_merge=source_grant['merge'],
                        grant_parent=source_grant['parent'],grant_default=source_grant['default'],
                        grant_blob=source_grant['blob'],grant_pr_sha256=a.r.digest([source_grant['title'],source_grant['body']]),
                        grant_issue_sha256=a.r.digest(current['grant_issue']),grant_digest=a.r.digest(grant),
                        packet_sha256=a.r.digest(current),decision='pass',findings=[],uncertainties=[])
        need(len(positives)==1 and isinstance(positives[0],dict)
             and type(positives[0].get('version')) is int
             and all(a.review.integer(positives[0].get(k)) for k in ('target_pr','grant_pr'))
             and positives == [expected] and not negatives, 'merged_outcome_receipt')
        result=dict(decision='pass', issue=grant['issue'], issue_sha256=grant['issue_sha256'],
                    sha256=a.r.digest(expected))
        stable()
        return result
    need(not positives and len(negatives) == 1 and isinstance(negatives[0], dict), 'merged_outcome_receipt')
    value = negatives[0]
    fields = ('version repo target_pr issue target_head target_base grant_pr grant_head grant_merge grant_default '
              'grant_blob grant_digest pr_sha256 issue_sha256 packet_sha256 decision summary findings uncertainties')
    need(a.review.exact(value, fields) and type(value['version']) is int and value['version'] == 1
         and all(a.review.integer(value[k]) for k in ('target_pr','issue','grant_pr'))
         and value['repo'] == repo and value['target_pr'] == target_pr and value['target_head'] == target_head
         and value['issue'] == grant['issue'] and value['target_base'] == grant['base']
         and value['grant_pr'] == grant_pr and value['grant_head'] == grant_head
         and value['grant_merge'] == source_grant['merge'] and value['grant_default'] == source_grant['default']
         and value['grant_blob'] == source_grant['blob'] and value['grant_digest'] == a.r.digest(grant)
         and value['pr_sha256'] == grant['pr_sha256'] and value['issue_sha256'] == grant['issue_sha256']
         and value['packet_sha256'] == a.r.digest(current) and value['decision'] in ('pass','fail','block')
         and a.review.text(value['summary'], 4000) and isinstance(value['findings'], list)
         and isinstance(value['uncertainties'], list)
         and all(a.review.text(x, 4000) for x in value['findings'] + value['uncertainties'])
         and (value['decision'] != 'pass' or value['findings'] or value['uncertainties']),
         'merged_outcome_receipt')
    result=dict(decision='block' if value['decision']=='block' else 'fail',
                issue=grant['issue'], issue_sha256=grant['issue_sha256'], sha256=a.r.digest(value))
    stable()
    if detail_sink is not None:
        detail_sink(dict(context=dict(base=grant['base']),result=dict(value,decision=result['decision']),
                         producer=dict(run_id=run_id,run_attempt=attempt,job_id=reviews[0]['id'])))
    return result


def verify_recovery_workflow(api, repo, document, evaluator):
    """Require the active, pinned recovery workflow before a guarded write.

    This authenticates a recovery path, not a guarantee that every interrupted
    request is recoverable. Unknown responses keep their guard. Actor adoption
    remains a separate prerequisite and is checked before this function.
    """
    recovery = a.review.sibling('recovery_entry')
    need(a.review.sha(evaluator) and os.environ.get('AW_ACTION_REF') == evaluator,
         'merged_outcome_recovery_not_ready')
    spec = recovery.workflow_contract(api, repo, document, evaluator)
    need(isinstance(spec, dict) and a.review.integer(spec.get('id'))
         and spec.get('path') == '.github/workflows/issue-recovery.yml',
         'merged_outcome_recovery_not_ready')

    def observe():
        workflow = api.api(f"repos/{repo}/actions/workflows/{spec['id']}")
        need(isinstance(workflow, dict) and workflow.get('id') == spec['id']
             and workflow.get('path') == spec['path'] and workflow.get('state') == 'active',
             'merged_outcome_recovery_not_ready')
        current = a.review.action.trusted_policy(
            api, repo, '.github/issue-ci-required-gates.json', metadata=True)
        need(current == document, 'merged_outcome_recovery_not_ready')
        return (workflow['id'], workflow['path'], workflow['state'])

    first = observe()
    need(recovery.workflow_contract(api, repo, document, evaluator) == spec
         and observe() == first, 'merged_outcome_recovery_not_ready')


def verify_recovery_ready(api, repo, document, evaluator):
    """Keep negative writes held until interrupted outcomes are recoverable."""
    verify_recovery_workflow(api, repo, document, evaluator)
    # An active workflow alone cannot recover every incomplete todo journal or
    # resolve a provider request whose response was lost. Keep the hard hold.
    raise a.review.Refused('merged_outcome_recovery_not_ready')


def apply(api, repo, grant_pr, grant_head, target_pr, target_head, evaluator, run_id,
          log_reader=a.r.job_log):
    """Apply a negative outcome only with rollout proof and a shared guard."""
    details=[]
    def current():
        details.clear()
        return verify(api,repo,grant_pr,grant_head,target_pr,target_head,evaluator,run_id,log_reader,
                      detail_sink=details.append)
    receipt = current()
    if receipt['decision'] == 'pass':
        return dict(state='pass_waiting_for_adoption',issue=receipt['issue'])
    document = a.review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    recovery = a.review.sibling('recovery_entry')
    recovery.verify_rollout(api,repo,document)
    # Actor rollout alone does not establish an active recovery workflow.
    verify_recovery_ready(api,repo,document,evaluator)
    guard = a.review.sibling('merge_guard')
    close = a.review.sibling('guarded_close')
    admission = a.review.sibling('guarded_admission')
    policy = guard.worker.read_policy(api,repo)
    need(policy is not None and policy['policy_source'] == document['source_commit'], 'merged_outcome_guard_policy')
    guard.worker.check_runtime(policy)
    provider = a.native.provider_pull(api,repo,target_pr)
    need(provider.get('state') == 'OPEN' and provider.get('headRefOid') == target_head
         and provider.get('autoMergeRequest') is None, 'merged_outcome_request_unknown')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(re.fullmatch(r'[1-9][0-9]*',attempt), 'merged_outcome_context')
    intent = guard.build(repo,target_pr,target_head,receipt['issue'],policy,run_id,int(attempt))
    guard.acquire(api,policy,intent,lambda line:print(line,flush=True))
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    print(OWNED_MARKER+a.r.canonical(dict(version=1,intent_sha256=intent['sha256'])),flush=True)
    def checked(*_args):
        close.require_current_policy(api,repo,policy)
        close.guard_identity(api,repo,policy,intent)
        fresh=current()
        need(fresh == receipt, 'merged_outcome_changed')
        return dict(payload=dict(context=dict(issue_sha256=receipt['issue_sha256']),
                                 result=dict(decision=receipt['decision'])), sha256=receipt['sha256'])
    trace = a.review.sibling('todo_mutation_trace')
    traced = trace.TracedApi(api,repo,receipt['issue'],target_head,intent['sha256'],
                             lambda line:print(line,flush=True))
    result = a.native.return_todo(traced,repo,target_pr,target_head,lambda:document,evaluator,run_id,checked,
                                 diagnostic=lambda:details[0])
    traced.seal()
    settlement = admission.settle(api,repo,policy,intent)
    need(settlement.get('state') == 'released', 'merged_outcome_guard_retained')
    return result


def main():
    repo = os.environ.get('GITHUB_REPOSITORY','')
    grant_pr, target_pr, run = (os.environ.get(name,'') for name in
                                 ('INPUT_GRANT_PR','INPUT_TARGET_PR','INPUT_REVIEW_RUN'))
    grant_head, target_head = os.environ.get('INPUT_GRANT_HEAD',''), os.environ.get('INPUT_TARGET_HEAD','')
    evaluator = os.environ.get('AW_ACTION_REF','')
    need(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
         and all(re.fullmatch(r'[1-9][0-9]*', value) for value in (grant_pr,target_pr,run))
         and a.review.sha(grant_head) and a.review.sha(target_head) and a.review.sha(evaluator),
         'merged_outcome_identity')
    result = apply(a.ci, repo, int(grant_pr), grant_head, int(target_pr), target_head, evaluator, int(run))
    print(a.r.canonical(result), flush=True)


if __name__ == '__main__':
    main()
