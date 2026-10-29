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
STATUS_CREATOR_ID=41898282
STATUS_APP_ID=15368


def matching_statuses(statuses):
    """Refuse ambiguous status origins before treating a merge as blocked.

    The protected rule is bound to GitHub Actions. A same-named status from
    another actor cannot prove that the protected Actions status is pending.
    """
    need(isinstance(statuses,list) and all(isinstance(item,dict)
         and isinstance(item.get('context'),str) for item in statuses), 'admission_status_unknown')
    matches=[item for item in statuses if item['context'].casefold()=='claude/verified-merge']
    need(all(type(item.get('id')) is int and item['id']>0 for item in matches)
         and len({item['id'] for item in matches})==len(matches), 'admission_status_unknown')
    if matches:
        creator=matches[0].get('creator')  # GitHub returns the newest status first.
        need(isinstance(creator,dict) and type(creator.get('id')) is int
             and creator['id']==STATUS_CREATOR_ID and creator.get('type')=='Bot'
             and creator.get('login')=='github-actions[bot]', 'admission_status_origin_unknown')
    return matches


def blocking_rule(api,repo,policy,pull):
    """Authenticate the current strict Actions-bound branch protection."""
    document=native.review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    need(isinstance(document,dict) and document.get('source_commit')==policy['policy_source'],
         'admission_policy_changed')
    native_policy=native.policy(document,require_qualification=False)
    need(native_policy.get('status_app_id')==STATUS_APP_ID,'admission_status_app_unknown')
    base=pull.get('base') if isinstance(pull,dict) else None
    need(isinstance(base,dict) and native.review.text(base.get('ref'),255)
         and native.review.sha(base.get('sha')),'admission_target_changed')
    context=dict(base_ref=base['ref'],base=base['sha'])
    return native.base_rules(api,repo,context,native_policy)


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
    matches=matching_statuses(statuses)
    # A missing commit status is not proof of a blocked check: GitHub can
    # satisfy the same required context with a check run instead.
    need(bool(matches),'admission_status_missing')
    latest=matches[0]  # GitHub returns statuses newest first.
    need(latest.get('state') in ('pending','failure','error'),'admission_status_not_blocked')
    rule=blocking_rule(api,repo,policy,rest)
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
    need(blocking_rule(api,repo,policy,rest_final)==rule,'admission_rule_changed')
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    close.release(api,repo,policy,intent)
    return dict(state='released',issue=p['issue'])


def cancel_blocked(api,repo,policy,intent):
    """Retain any request until its specific owner is proven.

    A shared Actions bot identity alone cannot identify the originating run.
    Cancellation and its race with merging remain unimplemented.
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
    # All Actions workflows share the same bot identity. That identity does
    # not establish which run requested this merge. Until provenance is bound
    # to the guard, retain it rather than cancel another actor's request.
    return dict(state='retained',issue=p['issue'])
