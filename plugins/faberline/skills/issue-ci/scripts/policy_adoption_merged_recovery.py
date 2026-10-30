"""Read-only identification of a crashed merged-grant outcome writer.

This does not release a lock or change an issue.  A recovery writer must
recheck all evidence and the same guard before attempting either operation.
"""
import importlib.util
from pathlib import Path
import re


def sibling(name):
    spec=importlib.util.spec_from_file_location('merged_recovery_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


outcome=sibling('policy_adoption_merged_outcome')
guard=sibling('merge_guard')
close=sibling('guarded_close')
review=guard.review
need=guard.need
PATH='.github/workflows/review-merged-product-grant.yml'
ACTION='faberline/aw/.github/actions/policy-adoption-merged-grant-outcome@'


def authenticate(api,repo,policy,run,attempt,issue,head,owner,log_reader):
    """Authenticate one terminal outcome attempt and its exact held guard."""
    guard.worker.config(policy)
    need(isinstance(repo,str) and guard.provider.ledger.REPO.fullmatch(repo)
         and all(review.integer(x) for x in (run,attempt,issue)) and review.sha(head)
         and isinstance(owner,str) and guard.provider.ledger.OWNER.fullmatch(owner),
         'merged_outcome_recovery_source')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('event')=='push'
         and row.get('path')==PATH and row.get('head_repository',{}).get('full_name')==repo
         and review.sha(row.get('head_sha')) and isinstance(row.get('head_branch'),str),
         'merged_outcome_recovery_source')
    raw=review.blob(api,repo,PATH,row['head_sha'])
    import yaml
    workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    try:
        step=workflow['jobs']['outcome']['steps'][0]
        action=step['uses'];inputs=step['with']
        grant_pr,grant_head,target_pr,target_head=(inputs[k] for k in ('grant-pr','grant-head','target-pr','target-head'))
    except (TypeError,KeyError,IndexError):
        raise review.Refused('merged_outcome_recovery_workflow') from None
    need(isinstance(action,str) and action.startswith(ACTION) and review.sha(action[len(ACTION):])
         and isinstance(grant_pr,str) and re.fullmatch(r'[1-9][0-9]*',grant_pr)
         and isinstance(target_pr,str) and re.fullmatch(r'[1-9][0-9]*',target_pr)
         and review.sha(grant_head) and target_head==head, 'merged_outcome_recovery_workflow')
    evaluator=action[len(ACTION):];number=int(target_pr);grant_number=int(grant_pr)
    outcome.m.workflow_contract(raw,dict(grant_pr=grant_number,grant_head=grant_head,target_pr=number,
        target_head=head,workflow_path=PATH,branch=row['head_branch']),evaluator)
    guard.historical_runtime(api,evaluator,policy)
    guard.worker.check_runtime(policy)
    receipt=outcome.verify(api,repo,grant_number,grant_head,number,head,evaluator,run,
                           log_reader,historical_attempt=attempt)
    need(receipt['decision'] in ('fail','block') and receipt['issue']==issue,
         'merged_outcome_recovery_receipt')
    jobs=api.pages(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs','jobs')
    writers=[job for job in jobs if job.get('name')=='outcome']
    need(len(writers)==1 and review.integer(writers[0].get('id'))
         and writers[0].get('status')=='completed', 'merged_outcome_recovery_job')
    records=review.r.records(log_reader(repo,writers[0]['id']),guard.MARKER)
    need(len(records)==1 and isinstance(records[0],dict) and isinstance(records[0].get('payload'),dict),
         'merged_outcome_recovery_intent')
    intent=records[0];p=intent['payload'];old_source=p.get('policy_source')
    need(review.sha(old_source), 'merged_outcome_recovery_policy')
    raw_policy=review.blob(api,repo,'.github/issue-ci-required-gates.json',old_source)
    config=review.ci.strict_json_loads(raw_policy)
    old_guard=config.get('issue_guard') if isinstance(config,dict) else None
    need(isinstance(old_guard,dict) and 'policy_source' not in old_guard, 'merged_outcome_recovery_policy')
    old_policy=guard.worker.config(dict(old_guard,policy_source=old_source))
    need(all(old_policy[key]==policy[key] for key in ('genesis','ruleset_id','runtime')),
         'merged_outcome_recovery_policy')
    p=guard.validate(intent,old_policy)
    need(p['repo']==repo and p['pr']==number and p['head']==head and p['issue']==issue
         and p['run']==run and p['attempt']==attempt and p['owner']==owner,
         'merged_outcome_recovery_intent')
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    # Re-read the review after verifying the guard. Both are evidence, not a
    # permission to update an issue or release this guard.
    need(outcome.verify(api,repo,grant_number,grant_head,number,head,evaluator,run,
                        log_reader,historical_attempt=attempt)==receipt,
         'merged_outcome_recovery_changed')
    close.guard_identity(api,repo,policy,intent)
    return dict(pr=number,head=head,issue=issue,evaluator=evaluator,intent=intent,receipt=receipt,
                grant_pr=grant_number,grant_head=grant_head)
