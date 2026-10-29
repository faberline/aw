"""Draft writer admission; the production writer does not call this yet.

Keep the guard during a native request. An unknown provider result is a hold.
This protocol requires every active worker and writer to use the same guard.
"""
import importlib.util
import os
from pathlib import Path
import re


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_guarded_admission_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


merge_guard=sibling('merge_guard')
close=sibling('guarded_close')
native=sibling('native_merge')
worker=merge_guard.worker
need=merge_guard.need


def begin(api,repo,document,evaluator,number,head,emit,random_bytes=None):
    """Persist intent then acquire before any writer lifecycle mutation."""
    run=os.environ.get('GITHUB_RUN_ID','');attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(os.environ.get('GITHUB_ACTIONS')=='true'
         and os.environ.get('GITHUB_EVENT_NAME') in ('issues','workflow_run','repository_dispatch')
         and os.environ.get('GITHUB_REPOSITORY')==repo
         and os.environ.get('GITHUB_WORKFLOW_SHA')==document['source_commit']
         and os.environ.get('AW_ACTION_REF')==evaluator
         and isinstance(evaluator,str) and merge_guard.provider.ledger.SHA.fullmatch(evaluator)
         and re.fullmatch(r'[1-9][0-9]*',run) and re.fullmatch(r'[1-9][0-9]*',attempt),
         'admission_source')
    policy=worker.read_policy(api,repo)
    need(policy is not None and policy['policy_source']==document['source_commit'],'admission_policy')
    pull=api.api(f'repos/{repo}/pulls/{number}')
    native.review.pull_identity(pull,repo,number,head)
    issue=native.review.linked_issue(pull)
    args=(repo,number,head,issue,policy,int(run),int(attempt))
    intent=merge_guard.build(*args) if random_bytes is None else merge_guard.build(*args,random_bytes=random_bytes)
    merge_guard.acquire(api,policy,intent,emit)
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    fresh=api.api(f'repos/{repo}/pulls/{number}')
    native.review.pull_identity(fresh,repo,number,head)
    need(native.review.linked_issue(fresh)==issue,'admission_issue_changed')
    return dict(policy=policy,intent=intent)


def settle(api,repo,policy,intent):
    """Keep a pending merge; release only after observing a blocked open PR.

    This is a draft. GitHub concurrency and cancellation behavior must be
    verified in hosted tests before this can govern a production writer.
    """
    p=intent['payload']
    need(all(p[k]==policy[k] for k in ('genesis','ruleset_id','runtime')),'admission_policy_changed')
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    pull=native.provider_pull(api,repo,p['pr'])
    need(pull.get('headRefOid')==p['head'],'admission_target_changed')
    if pull.get('state')=='MERGED' or pull.get('autoMergeRequest') is not None:
        return dict(state='retained',issue=p['issue'])
    need(pull.get('state')=='OPEN','admission_target_changed')
    rest=api.api(f"repos/{repo}/pulls/{p['pr']}")
    native.review.pull_identity(rest,repo,p['pr'],p['head'])
    need(native.review.linked_issue(rest)==p['issue'],'admission_issue_changed')
    statuses=api.pages(f"repos/{repo}/commits/{p['head']}/statuses")
    need(isinstance(statuses,list),'admission_status_unknown')
    matches=[item for item in statuses if isinstance(item,dict) and isinstance(item.get('context'),str)
             and item['context'].casefold()=='claude/verified-merge']
    need(all(type(item.get('id')) is int and item['id']>0 for item in matches)
         and len({item['id'] for item in matches})==len(matches),'admission_status_unknown')
    if matches:
        latest=matches[0]  # GitHub returns statuses newest first.
        need(latest.get('state') in ('pending','failure','error'),'admission_status_not_blocked')
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    final=native.provider_pull(api,repo,p['pr'])
    need(final.get('state')=='OPEN' and final.get('headRefOid')==p['head']
         and final.get('autoMergeRequest') is None,'admission_target_changed')
    rest_final=api.api(f"repos/{repo}/pulls/{p['pr']}")
    native.review.pull_identity(rest_final,repo,p['pr'],p['head'])
    need(native.review.linked_issue(rest_final)==p['issue'],'admission_issue_changed')
    again=api.pages(f"repos/{repo}/commits/{p['head']}/statuses")
    need(again==statuses,'admission_status_changed')
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    close.release(api,repo,policy,intent)
    return dict(state='released',issue=p['issue'])


def cancel_blocked(api,repo,policy,intent):
    """Draft recovery for a blocked owned request; never cancel a foreign one.

    GitHub's cancellation/merge race must be validated before rollout. A lost
    response is a hold; a later wake observes the provider before proceeding.
    """
    p=intent['payload']
    need(all(p[k]==policy[k] for k in ('genesis','ruleset_id','runtime')),'admission_policy_changed')
    close.require_current_policy(api,repo,policy);close.guard_identity(api,repo,policy,intent)
    pull=native.provider_pull(api,repo,p['pr'])
    need(pull.get('headRefOid')==p['head'],'admission_target_changed')
    if pull.get('state')=='MERGED':return dict(state='retained',issue=p['issue'])
    need(pull.get('state')=='OPEN','admission_target_changed')
    request=pull.get('autoMergeRequest')
    if request is None:
        return settle(api,repo,policy,intent)
    if not native.actions_request(request):
        return dict(state='retained',issue=p['issue'])
    statuses=api.pages(f"repos/{repo}/commits/{p['head']}/statuses")
    need(isinstance(statuses,list),'admission_status_unknown')
    matches=[item for item in statuses if isinstance(item,dict) and isinstance(item.get('context'),str)
             and item['context'].casefold()=='claude/verified-merge']
    need(all(type(item.get('id')) is int and item['id']>0 for item in matches)
         and len({item['id'] for item in matches})==len(matches),'admission_status_unknown')
    if matches:
        latest=matches[0]  # GitHub returns statuses newest first.
        if latest.get('state')=='success':return dict(state='retained',issue=p['issue'])
        need(latest.get('state') in ('pending','failure','error'),'admission_status_unknown')
    close.require_current_policy(api,repo,policy);close.guard_identity(api,repo,policy,intent)
    native.withdraw_approval(api,repo,p['pr'],p['head'])
    return settle(api,repo,policy,intent)
