#!/usr/bin/env python3
"""Apply only an authenticated negative policy-target review to todo."""
import importlib.util
import os
from pathlib import Path
import re
import sys

spec=importlib.util.spec_from_file_location('review_outcome_policy',Path(__file__).with_name('policy_adoption.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
need=a.need


def verify(api,repo,number,head,evaluator,run_id,log_reader=a.r.job_log):
    need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and a.review.sha(evaluator)
         and a.review.integer(number) and a.review.sha(head) and a.review.integer(run_id), 'outcome_identity')
    run=api.api(f'repos/{repo}/actions/runs/{run_id}')
    path=run.get('path');branch=run.get('head_branch');source=run.get('head_sha');attempt=run.get('run_attempt')
    need(run.get('id')==run_id and run.get('event')=='push' and run.get('status') in ('queued','in_progress')
         and run.get('head_repository',{}).get('full_name')==repo and a.review.sha(source)
         and isinstance(branch,str) and a.review.integer(attempt) and isinstance(path,str)
         and os.environ.get('GITHUB_REPOSITORY')==repo and os.environ.get('GITHUB_RUN_ID')==str(run_id)
         and os.environ.get('GITHUB_EVENT_NAME')=='push' and os.environ.get('GITHUB_SHA')==source
         and os.environ.get('GITHUB_WORKFLOW_SHA')==source and os.environ.get('GITHUB_REF')=='refs/heads/'+branch
         and os.environ.get('GITHUB_WORKFLOW_REF')==repo+'/'+path+'@refs/heads/'+branch, 'outcome_run_context')
    raw=a.review.blob(api,repo,path,source)
    spec=dict(workflow_path=path,branch=branch,job='review')
    target=dict(repo=repo,pr=number,head=head,evaluator=evaluator)
    a.target_workflow(raw,spec,target)
    a.review.blob(api,a.GRANTS_REPO,'plugins/faberline/skills/issue-ci/scripts/policy_adoption_target_review.py',evaluator)
    jobs=api.pages(f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs','jobs')
    review_jobs=[j for j in jobs if j.get('name')=='review']
    writers=[j for j in jobs if j.get('name')=='outcome']
    need(len(review_jobs)==1 and a.review.integer(review_jobs[0].get('id'))
         and review_jobs[0].get('status')=='completed' and review_jobs[0].get('conclusion')=='success'
         and len(writers)==1 and a.review.integer(writers[0].get('id'))
         and writers[0].get('status')=='in_progress', 'outcome_jobs')
    rows=a.r.records(log_reader(repo,review_jobs[0]['id']),a.MARKER)
    need(len(rows)==1 and isinstance(rows[0],dict), 'outcome_receipt')
    value=rows[0]
    fields='version repo pr issue head base pr_sha256 issue_sha256 files_sha256 ci_jobs_sha256 reviewed_files requirements decision findings uncertainties'
    need(a.review.exact(value,fields) and type(value['version']) is int and value['version']==1
         and value['repo']==repo and a.review.integer(value['pr']) and value['pr']==number and value['head']==head
         and a.review.integer(value['issue']) and a.review.sha(value['base'])
         and all(isinstance(value[k],str) and re.fullmatch('[0-9a-f]{64}',value[k])
                 for k in ('pr_sha256','issue_sha256','files_sha256','ci_jobs_sha256'))
         and value['decision'] in ('pass','fail','block') and isinstance(value['findings'],list)
         and isinstance(value['uncertainties'],list) and isinstance(value['requirements'],list)
         and isinstance(value['reviewed_files'],list), 'outcome_receipt')
    if value['decision']=='pass':
        need(value['findings']==[] and value['uncertainties']==[], 'outcome_false_pass')
    else:
        need(value['findings'] or value['uncertainties']
             or any(isinstance(x,dict) and x.get('status') in ('missing','uncertain') for x in value['requirements']),
             'outcome_negative_without_reason')
    pull=api.api(f'repos/{repo}/pulls/{number}');a.review.pull_identity(pull,repo,number,head)
    need(pull['base'].get('sha')==value['base'] and pull['base'].get('ref')=='main'
         and api.api(f'repos/{repo}/commits/main').get('sha')==value['base']
         and a.review.linked_issue(pull)==value['issue']
         and a.r.digest([pull.get('title'),pull.get('body')])==value['pr_sha256'], 'outcome_pr_changed')
    issue=api.api(f"repos/{repo}/issues/{value['issue']}")
    need(issue.get('number')==value['issue'] and issue.get('state')=='open'
         and a.r.digest({k:issue.get(k) for k in ('number','title','body')})==value['issue_sha256'], 'outcome_issue_changed')
    payload=dict(context=dict(issue_sha256=value['issue_sha256']),result=dict(decision=value['decision']))
    return dict(payload=payload,sha256=a.r.digest(value))


def apply(api,repo,number,head,evaluator,run_id,log_reader=a.r.job_log):
    def check(*_args):return verify(api,repo,number,head,evaluator,run_id,log_reader)
    receipt=check()
    if receipt['payload']['result']['decision']=='pass':
        return dict(state='pass_waiting_for_separate_grant',head=head)
    return a.native.return_todo(api,repo,number,head,lambda:None,evaluator,run_id,check)


def main():
    repo=os.environ.get('GITHUB_REPOSITORY','');number=os.environ.get('INPUT_TARGET_PR','')
    head=os.environ.get('INPUT_TARGET_HEAD','');run=os.environ.get('INPUT_REVIEW_RUN','')
    evaluator=os.environ.get('AW_ACTION_REF','')
    need(bool(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo))
         and bool(re.fullmatch(r'[1-9][0-9]*',number)) and bool(re.fullmatch(r'[1-9][0-9]*',run))
         and os.environ.get('GITHUB_RUN_ID')==run and a.review.sha(head) and a.review.sha(evaluator), 'outcome_input')
    print(a.r.canonical(apply(a.ci,repo,int(number),head,evaluator,int(run))),flush=True)


if __name__=='__main__':
    try:main()
    except (RuntimeError,KeyError,TypeError,ValueError,OSError) as error:
        print('refused: '+str(error),file=sys.stderr);sys.exit(2)
