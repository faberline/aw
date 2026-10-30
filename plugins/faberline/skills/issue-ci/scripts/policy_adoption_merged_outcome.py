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


def verify(api, repo, grant_pr, grant_head, target_pr, target_head, evaluator, run_id,
           log_reader=a.r.job_log):
    need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and a.review.sha(evaluator)
         and all(a.review.integer(x) for x in (grant_pr, target_pr, run_id))
         and a.review.sha(grant_head) and a.review.sha(target_head), 'merged_outcome_identity')
    run = api.api(f'repos/{repo}/actions/runs/{run_id}')
    path, branch, source, attempt = (run.get(k) for k in ('path','head_branch','head_sha','run_attempt'))
    expected_branch = f'codex/merged-grant-{target_pr}-{target_head}'
    need(run.get('id') == run_id and a.review.integer(run.get('workflow_id'))
         and a.review.integer(run.get('run_number')) and a.review.integer(attempt)
         and run.get('event') == 'push' and run.get('status') == 'in_progress'
         and run.get('head_repository', {}).get('full_name') == repo
         and path == '.github/workflows/review-merged-product-grant.yml' and branch == expected_branch
         and a.review.sha(source) and os.environ.get('GITHUB_ACTIONS') == 'true'
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
         and outcomes[0].get('status') == 'in_progress', 'merged_outcome_jobs')
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
        need(positives == [expected] and not negatives, 'merged_outcome_receipt')
        return dict(decision='pass', issue=grant['issue'], sha256=a.r.digest(expected))
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
    return dict(decision='block' if value['decision']=='block' else 'fail',
                issue=grant['issue'], sha256=a.r.digest(value))


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
    result = verify(a.ci, repo, int(grant_pr), grant_head, int(target_pr), target_head, evaluator, int(run))
    need(result['decision'] == 'pass', 'merged_outcome_guard_not_ready')
    print(a.r.canonical(dict(state='pass_waiting_for_adoption',issue=result['issue'])), flush=True)


if __name__ == '__main__':
    main()
