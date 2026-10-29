"""Candidate GitHub-backed ledger adapter, not wired into any workflow.

Only cooperating, upgraded actors are serialized. Each issue has its own ref,
so a history limit cannot hold unrelated issues. A caller must authenticate
the pinned genesis and ruleset from trusted policy. Ambiguous writes are never
blindly retried. Old workers and humans outside the protocol are not protected.
"""
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import quote

_spec=importlib.util.spec_from_file_location('aw_guard_ledger',Path(__file__).with_name('guard_ledger.py'))
ledger=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(ledger)
need=ledger.need
Refused=ledger.Refused
BRANCH_PREFIX='aw-issue-guard/issue-'
GENESIS_MARKER='AW_ISSUE_GUARD_GENESIS_V1 '
MAX_DEPTH=100000
PAGE_SIZE=100
READ_REF='''query($owner:String!,$name:String!,$ref:String!){
  repository(owner:$owner,name:$name){nameWithOwner ref(qualifiedName:$ref){name target{__typename oid}}}
}'''
RULESET='''query($owner:String!,$name:String!,$id:Int!){
  repository(owner:$owner,name:$name){nameWithOwner ruleset(databaseId:$id){
    databaseId enforcement target bypassActors(first:1){totalCount pageInfo{hasNextPage}}
  }}
}'''


def graphql(api,query,variables):
    value=api.api('graphql',method='POST',data=dict(query=query,variables=variables))
    need(isinstance(value,dict) and not value.get('errors') and isinstance(value.get('data'),dict),
         'guard_provider_unknown')
    return value['data']


def branch(issue):
    need(type(issue) is int and ledger.ISSUE.fullmatch(str(issue)), 'guard_issue')
    return BRANCH_PREFIX+str(issue)


def provider_rules(api,repo,ruleset_id,issue):
    need(isinstance(repo,str) and ledger.REPO.fullmatch(repo)
         and type(ruleset_id) is int and ruleset_id>0,'guard_policy_identity')
    rules=api.api(f'repos/{repo}/rules/branches/{quote(branch(issue),safe="")}')
    need(isinstance(rules,list) and len([rule for rule in rules if isinstance(rule,dict)
         and rule.get('type')=='non_fast_forward' and rule.get('ruleset_id')==ruleset_id])==1,
         'guard_force_rule_missing')
    policy=api.api(f'repos/{repo}/rulesets/{ruleset_id}')
    need(isinstance(policy,dict) and type(policy.get('id')) is int and policy['id']==ruleset_id
         and policy.get('enforcement')=='active' and policy.get('target')=='branch',
         'guard_ruleset_unknown')
    bypass=policy.get('bypass_actors')
    if bypass is None:
        owner,name=repo.split('/')
        found=graphql(api,RULESET,dict(owner=owner,name=name,id=ruleset_id)).get('repository')
        row=found.get('ruleset') if isinstance(found,dict) else None
        actors=row.get('bypassActors') if isinstance(row,dict) else None
        need(isinstance(found,dict) and found.get('nameWithOwner')==repo and isinstance(row,dict)
             and type(row.get('databaseId')) is int and row['databaseId']==ruleset_id
             and row.get('enforcement')=='ACTIVE' and row.get('target')=='BRANCH'
             and isinstance(actors,dict) and type(actors.get('totalCount')) is int
             and actors['totalCount']==0 and isinstance(actors.get('pageInfo'),dict)
             and actors['pageInfo'].get('hasNextPage') is False, 'guard_bypass_unknown')
    else:
        need(bypass==[], 'guard_bypass_unknown')


def ref_head(api,repo,issue,allow_missing=False):
    need(isinstance(repo,str) and ledger.REPO.fullmatch(repo),'guard_repository')
    owner,name=repo.split('/')
    name_of_branch=branch(issue)
    found=graphql(api,READ_REF,dict(owner=owner,name=name,ref='refs/heads/'+name_of_branch)).get('repository')
    need(isinstance(found,dict) and found.get('nameWithOwner')==repo and 'ref' in found,'guard_ref_unknown')
    ref=found.get('ref')
    if ref is None and allow_missing:
        return None
    need(isinstance(ref,dict) and ref.get('name')==name_of_branch and isinstance(ref.get('target'),dict)
         and ref['target'].get('__typename')=='Commit' and isinstance(ref['target'].get('oid'),str)
         and ledger.SHA.fullmatch(ref['target']['oid']), 'guard_ref_missing_or_unknown')
    return ref['target']['oid']


def genesis_message(repo):
    value=dict(version=1,repo=repo,sequence=0,locks={})
    ledger.validate(value)
    return GENESIS_MARKER+json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)


def verified_genesis(api,repo,genesis):
    need(isinstance(genesis,str) and ledger.SHA.fullmatch(genesis),'guard_genesis_unknown')
    origin=api.api(f'repos/{repo}/git/commits/{genesis}')
    need(isinstance(origin,dict) and origin.get('sha')==genesis
         and origin.get('message')==genesis_message(repo)
         and isinstance(origin.get('tree'),dict) and isinstance(origin['tree'].get('sha'),str)
         and ledger.SHA.fullmatch(origin['tree']['sha'])
         and isinstance(origin.get('parents'),list) and len(origin['parents'])==1
         and isinstance(origin['parents'][0],dict) and isinstance(origin['parents'][0].get('sha'),str)
         and ledger.SHA.fullmatch(origin['parents'][0]['sha']), 'guard_genesis_unknown')
    parent=origin['parents'][0]['sha']
    base=api.api(f'repos/{repo}/git/commits/{parent}')
    need(isinstance(base,dict) and base.get('sha')==parent and isinstance(base.get('tree'),dict)
         and base['tree'].get('sha')==origin['tree']['sha'], 'guard_genesis_tree_changed')
    meta=api.api(f'repos/{repo}')
    need(isinstance(meta,dict) and meta.get('full_name')==repo
         and isinstance(meta.get('default_branch'),str) and bool(meta['default_branch']), 'guard_default_unknown')
    comparison=api.api(f"repos/{repo}/compare/{parent}...{quote(meta['default_branch'],safe='')}")
    need(isinstance(comparison,dict) and comparison.get('status') in ('identical','ahead')
         and isinstance(comparison.get('base_commit'),dict)
         and comparison['base_commit'].get('sha')==parent, 'guard_genesis_not_on_default')
    return origin


def read_chain(api,repo,genesis,ruleset_id,issue):
    """Validate every transition from an independently approved genesis."""
    need(isinstance(genesis,str) and ledger.SHA.fullmatch(genesis),'guard_genesis_unknown')
    provider_rules(api,repo,ruleset_id,issue)
    head=ref_head(api,repo,issue)
    origin=verified_genesis(api,repo,genesis)
    state=dict(version=1,repo=repo,sequence=0,locks={});parent=genesis;tree=origin['tree']['sha'];operations=set()
    seen=set();total=None;depth=0;last_operation=None
    for page in range(1,MAX_DEPTH//PAGE_SIZE+2):
        comparison=api.api(f'repos/{repo}/compare/{genesis}...{head}?per_page={PAGE_SIZE}&page={page}')
        need(isinstance(comparison,dict) and comparison.get('status') in ('identical','ahead')
             and type(comparison.get('total_commits')) is int and 0<=comparison['total_commits']<=MAX_DEPTH
             and type(comparison.get('ahead_by')) is int and comparison['ahead_by']==comparison['total_commits']
             and type(comparison.get('behind_by')) is int and comparison['behind_by']==0
             and isinstance(comparison.get('base_commit'),dict) and comparison['base_commit'].get('sha')==genesis
             and isinstance(comparison.get('merge_base_commit'),dict) and comparison['merge_base_commit'].get('sha')==genesis
             and isinstance(comparison.get('commits'),list) and len(comparison['commits'])<=PAGE_SIZE,
             'guard_history_unknown')
        need((head==genesis and comparison['status']=='identical' and comparison['total_commits']==0)
             or (head!=genesis and comparison['status']=='ahead' and comparison['total_commits']>0),
             'guard_history_unknown')
        if total is None:total=comparison['total_commits']
        need(total==comparison['total_commits'],'guard_history_changed')
        for row in comparison['commits']:
            need(isinstance(row,dict) and isinstance(row.get('commit'),dict),'guard_history_unknown')
            commit=dict(sha=row.get('sha'),parents=row.get('parents'),tree=row['commit'].get('tree'),
                        message=row['commit'].get('message'))
            need(commit['sha'] not in seen,'guard_history_limit_or_cycle')
            state=ledger.verify_commit(state,repo,parent,commit['sha'],tree,commit)
            operation=json.loads(commit['message'][len(ledger.MARKER):])['payload']['operation']
            need(operation not in operations,'guard_operation_replayed')
            operations.add(operation);last_operation=operation;seen.add(commit['sha']);depth+=1
            need(set(state['locks']) <= {str(issue)},'guard_foreign_issue')
            need(depth<=total,'guard_history_unknown')
            parent=commit['sha']
        if depth==total:break
        need(bool(comparison['commits']),'guard_history_unknown')
    else:
        raise Refused('guard_history_limit_or_cycle')
    need(parent==head,'guard_history_unknown')
    # A concurrent move means this read is not an authority to write.
    need(ref_head(api,repo,issue)==head,'guard_ref_changed')
    return dict(head=head,tree=tree,state=state,depth=depth,operations=sorted(operations),last_operation=last_operation)


def ensure_ref(api,repo,genesis,ruleset_id,issue):
    """Create an empty, protected issue ref once; never reset an existing ref."""
    provider_rules(api,repo,ruleset_id,issue)
    verified_genesis(api,repo,genesis)
    existing=ref_head(api,repo,issue,allow_missing=True)
    if existing is not None:
        return read_chain(api,repo,genesis,ruleset_id,issue)
    name='refs/heads/'+branch(issue)
    try:
        result=api.api(f'repos/{repo}/git/refs',method='POST',data=dict(ref=name,sha=genesis))
    except Exception:
        raise Refused('guard_create_unknown') from None
    need(isinstance(result,dict) and result.get('ref')==name and isinstance(result.get('object'),dict)
         and result['object'].get('type')=='commit' and result['object'].get('sha')==genesis,
         'guard_create_unknown')
    return read_chain(api,repo,genesis,ruleset_id,issue)


def update(api,repo,genesis,ruleset_id,issue,action,claim,operation):
    """Attempt one CAS transition; on uncertainty, hold and re-read later."""
    before=read_chain(api,repo,genesis,ruleset_id,issue)
    need(before['depth'] < MAX_DEPTH,'guard_history_limit_or_cycle')
    need(operation not in before['operations'],'guard_operation_replayed')
    candidate=ledger.proposal(before['state'],repo,before['head'],issue,action,claim,operation)
    message=ledger.commit_message(candidate)
    try:
        commit=api.api(f'repos/{repo}/git/commits',method='POST',data=dict(
            message=message,tree=before['tree'],parents=[before['head']]))
    except Exception:
        raise Refused('guard_commit_unknown') from None
    child=commit.get('sha') if isinstance(commit,dict) else None
    ledger.verify_commit(before['state'],repo,before['head'],child,before['tree'],commit)
    provider_rules(api,repo,ruleset_id,issue)
    try:
        result=api.api(f'repos/{repo}/git/refs/heads/{branch(issue)}',method='PATCH',data=dict(sha=child,force=False))
    except Exception:
        raise Refused('guard_update_unknown') from None
    need(isinstance(result,dict) and result.get('ref')=='refs/heads/'+branch(issue) and isinstance(result.get('object'),dict)
         and result['object'].get('type')=='commit' and result['object'].get('sha')==child,
         'guard_update_unknown')
    need(ref_head(api,repo,issue)==child,'guard_update_unknown')
    provider_rules(api,repo,ruleset_id,issue)
    return dict(head=child,state=candidate['payload']['ledger'])
