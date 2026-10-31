"""Candidate worker-side guard operations; not yet deployed.

The caller must authenticate the default-branch policy and persist the intent
before any remote operation. Existing workers that do not use this module are
not serialized. This module does not edit issues or claim a local worktree.
"""
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import quote

_path=Path(__file__).resolve().parents[2]/'issue-ci/scripts/guard_github.py'
_spec=importlib.util.spec_from_file_location('aw_worker_guard_provider',_path)
provider=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(provider)
need=provider.need
POLICY_BLOB='''query($owner:String!,$name:String!,$expression:String!){
  repository(owner:$owner,name:$name){nameWithOwner object(expression:$expression){
    __typename ... on Blob{oid byteSize isBinary text}
  }}
}'''


class Api:
    """Use the existing gh client; this adapter never handles credentials."""
    def __init__(self,client):
        self.client=client

    def api(self,path,method='GET',data=None):
        root=f'repos/{self.client.repo}'
        need(method in ('GET','POST','PATCH') and isinstance(path,str)
             and (path=='graphql' or (path==root and method=='GET') or path.startswith(root+'/'))
             and '://' not in path, 'worker_guard_api_path')
        if '..' in path:
            relative=path.removeprefix(f'repos/{self.client.repo}/')
            need(method=='GET' and bool(re.fullmatch(
                r'compare/[0-9a-f]{40}\.\.\.[A-Za-z0-9_%.-]+(?:\?per_page=100&page=[1-9][0-9]*)?',relative))
                and '..' not in relative.split('...',1)[1].split('?',1)[0], 'worker_guard_api_path')
        if path=='graphql':
            need(method=='POST' and isinstance(data,dict) and isinstance(data.get('query'),str)
                 and data['query'].lstrip().startswith('query('), 'worker_guard_api_path')
        else:
            need((method=='GET' and data is None) or (method in ('POST','PATCH') and isinstance(data,dict)),
                 'worker_guard_api_path')
        return self.client.command('api','--method',method,path,payload=data)

    def dispatch(self,repo):
        need(repo==self.client.repo and provider.ledger.REPO.fullmatch(repo),'worker_guard_repository')
        # GitHub returns 204 with no JSON body. A failed/unknown response is not
        # treated as success; a later identical wake is safe to retry.
        subprocess.check_output(['gh','api','--method','POST',f'repos/{repo}/dispatches','--input','-'],
            input=json.dumps(dict(event_type='aw-verified-merge-reconcile')),text=True)


def dispatch_wake(api,repo):
    """Wake a trusted writer; the event never approves a PR by itself."""
    need(isinstance(repo,str) and provider.ledger.REPO.fullmatch(repo),'worker_guard_repository')
    api.dispatch(repo)


def config(value):
    need(isinstance(value,dict) and set(value)=={'version','enabled','genesis','ruleset_id','policy_source','runtime'}
         and type(value['version']) is int and value['version']==1 and value['enabled'] is True
         and isinstance(value['genesis'],str) and provider.ledger.SHA.fullmatch(value['genesis'])
         and type(value['ruleset_id']) is int and value['ruleset_id']>0
         and isinstance(value['policy_source'],str) and provider.ledger.SHA.fullmatch(value['policy_source']),
         'worker_guard_policy')
    runtime=value['runtime']
    need(isinstance(runtime,dict) and set(runtime)=={'worker','provider','ledger'}
         and all(isinstance(v,str) and len(v)==64 and all(c in '0123456789abcdef' for c in v)
                 for v in runtime.values()), 'worker_guard_runtime')
    return value


def read_policy(api,repo):
    """Read a pinned default-branch policy; absent config means legacy mode."""
    need(isinstance(repo,str) and provider.ledger.REPO.fullmatch(repo),'worker_guard_repository')
    meta=api.api(f'repos/{repo}')
    need(isinstance(meta,dict) and meta.get('full_name')==repo
         and isinstance(meta.get('default_branch'),str) and bool(meta['default_branch']), 'worker_guard_default')
    branch=quote(meta['default_branch'],safe='')
    source=api.api(f'repos/{repo}/commits/{branch}').get('sha')
    need(isinstance(source,str) and provider.ledger.SHA.fullmatch(source),'worker_guard_default')
    owner,name=repo.split('/')
    response=api.api('graphql',method='POST',data=dict(query=POLICY_BLOB,variables=dict(
        owner=owner,name=name,expression=source+':.github/issue-ci-required-gates.json')))
    need(isinstance(response,dict) and not response.get('errors') and isinstance(response.get('data'),dict),
         'worker_guard_policy_source')
    repository=response['data'].get('repository')
    need(isinstance(repository,dict) and repository.get('nameWithOwner')==repo and 'object' in repository,
         'worker_guard_policy_source')
    blob=repository['object']
    if blob is None:
        current=api.api(f'repos/{repo}/commits/{branch}')
        fresh_meta=api.api(f'repos/{repo}')
        need(isinstance(current,dict) and current.get('sha')==source and isinstance(fresh_meta,dict)
             and fresh_meta.get('full_name')==repo and fresh_meta.get('default_branch')==meta['default_branch'],
             'worker_guard_default_changed')
        return None
    need(isinstance(blob,dict) and blob.get('__typename')=='Blob' and blob.get('isBinary') is False
         and type(blob.get('byteSize')) is int and 0<=blob['byteSize']<=262144
         and isinstance(blob.get('text'),str), 'worker_guard_policy_source')
    raw=blob['text'].encode('utf-8')
    actual=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    need(len(raw)==blob['byteSize'] and blob.get('oid')==actual,'worker_guard_policy_source')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate key')
            result[key]=value
        return result
    try:
        document=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,TypeError,RecursionError):
        raise provider.Refused('worker_guard_policy_source') from None
    need(isinstance(document,dict),'worker_guard_policy_source')
    current=api.api(f'repos/{repo}/commits/{branch}')
    fresh_meta=api.api(f'repos/{repo}')
    need(isinstance(current,dict) and current.get('sha')==source and isinstance(fresh_meta,dict)
         and fresh_meta.get('full_name')==repo and fresh_meta.get('default_branch')==meta['default_branch'],
         'worker_guard_default_changed')
    if 'issue_guard_cutover' in document:
        marker=document['issue_guard_cutover']
        need('issue_guard' not in document and isinstance(marker,dict)
             and set(marker)=={'version','phase','nonce'} and type(marker['version']) is int
             and marker['version']==1 and marker['phase']=='drain'
             and isinstance(marker['nonce'],str) and re.fullmatch(r'[0-9a-f]{32}',marker['nonce']),
             'worker_guard_cutover')
        return dict(cutover=marker,policy_source=source)
    if 'issue_guard' not in document:
        return None
    guard=document['issue_guard']
    need(isinstance(guard,dict) and 'policy_source' not in guard,'worker_guard_policy')
    policy=config(dict(guard,policy_source=source))
    check_runtime(policy)
    return policy


def draining(value):
    return isinstance(value,dict) and 'cutover' in value


def check_runtime(policy):
    config(policy)
    paths={'worker':Path(__file__).resolve(), 'provider':_path.resolve(),
           'ledger':_path.resolve().with_name('guard_ledger.py')}
    for key,path in paths.items():
        need(hashlib.sha256(path.read_bytes()).hexdigest()==policy['runtime'][key],'worker_guard_runtime')
    return True


def new_intent(issue,policy,random_bytes=secrets.token_hex):
    """Create an opaque public owner and operation IDs before any remote write."""
    config(policy)
    need(type(issue) is int and provider.ledger.ISSUE.fullmatch(str(issue)), 'worker_guard_issue')
    owner,claim_op,release_op=(random_bytes(16) for _ in range(3))
    value=dict(version=1,issue=issue,owner=owner,claim_op=claim_op,release_op=release_op,
               policy_source=policy['policy_source'],genesis=policy['genesis'],ruleset_id=policy['ruleset_id'],
               runtime=dict(policy['runtime']))
    intent(value,policy,policy['policy_source'])
    return value


def intent(value,policy,current_source):
    config(policy)
    need(isinstance(value,dict) and set(value)=={'version','issue','owner','claim_op','release_op','policy_source','genesis','ruleset_id','runtime'}
         and type(value['version']) is int and value['version']==1
         and type(value['issue']) is int and provider.ledger.ISSUE.fullmatch(str(value['issue']))
         and all(isinstance(value[key],str) and provider.ledger.OWNER.fullmatch(value[key])
                 for key in ('owner','claim_op','release_op'))
         and len({value[key] for key in ('owner','claim_op','release_op')})==3
         and isinstance(value['policy_source'],str) and provider.ledger.SHA.fullmatch(value['policy_source'])
         and value['genesis']==policy['genesis'] and type(value['ruleset_id']) is int
         and value['ruleset_id']==policy['ruleset_id'] and value['runtime']==policy['runtime']
         and policy['policy_source']==current_source,
         'worker_guard_intent')
    return value


def policy_ancestry(api,repo,value,current_source):
    """Permit unchanged guard policy after a verified default-branch advance."""
    old=value['policy_source']
    if old==current_source:
        return
    result=api.api(f'repos/{repo}/compare/{old}...{current_source}')
    need(isinstance(result,dict) and result.get('status')=='ahead'
         and isinstance(result.get('base_commit'),dict) and result['base_commit'].get('sha')==old
         and isinstance(result.get('merge_base_commit'),dict) and result['merge_base_commit'].get('sha')==old,
         'worker_guard_policy_history')


def descriptor(value):
    return dict(owner=value['owner'],kind='worker',head=None)


def require_no_legacy_guard(api,repo,issue):
    """Do not ignore an existing guarded issue after policy disappears."""
    need(provider.ref_head(api,repo,issue,allow_missing=True) is None, 'worker_guard_policy_missing')


def acquire(api,repo,policy,value,current_source):
    """Acquire or observe this exact persisted intent; never steal or replay."""
    intent(value,policy,current_source)
    policy_ancestry(api,repo,value,current_source)
    issue=value['issue'];claim=descriptor(value)
    state=provider.ensure_ref(api,repo,policy['genesis'],policy['ruleset_id'],issue)
    existing=state['state']['locks'].get(str(issue));operations=state['operations']
    if existing==claim:
        need(value['claim_op'] in operations and value['release_op'] not in operations
             and state['last_operation']==value['claim_op'],
             'worker_guard_claim_unknown')
        return dict(state='owned',head=state['head'])
    need(existing is None and value['claim_op'] not in operations and value['release_op'] not in operations,
         'worker_guard_held_or_consumed')
    updated=provider.update(api,repo,policy['genesis'],policy['ruleset_id'],issue,'claim',claim,value['claim_op'])
    need(updated['state']['locks'].get(str(issue))==claim,'worker_guard_claim_unknown')
    return dict(state='owned',head=updated['head'])


def require_owned(api,repo,policy,value,current_source):
    """Prove an existing lock without silently acquiring or changing it."""
    intent(value,policy,current_source)
    policy_ancestry(api,repo,value,current_source)
    issue=value['issue'];state=provider.read_chain(api,repo,policy['genesis'],policy['ruleset_id'],issue)
    need(state['state']['locks'].get(str(issue))==descriptor(value)
         and value['claim_op'] in state['operations'] and value['release_op'] not in state['operations']
         and state['last_operation']==value['claim_op'], 'worker_guard_not_owner')
    return True


def release(api,repo,policy,value,current_source):
    """Release only this intent. A later owner is never changed or reclaimed."""
    intent(value,policy,current_source)
    policy_ancestry(api,repo,value,current_source)
    issue=value['issue'];claim=descriptor(value)
    state=provider.read_chain(api,repo,policy['genesis'],policy['ruleset_id'],issue)
    existing=state['state']['locks'].get(str(issue));operations=state['operations']
    if existing is None:
        need(value['claim_op'] in operations and value['release_op'] in operations
             and state['last_operation']==value['release_op'],
             'worker_guard_release_unknown')
        return dict(state='released',head=state['head'])
    need(existing==claim and value['claim_op'] in operations and value['release_op'] not in operations
         and state['last_operation']==value['claim_op'],
         'worker_guard_not_owner')
    updated=provider.update(api,repo,policy['genesis'],policy['ruleset_id'],issue,'release',claim,value['release_op'])
    need(str(issue) not in updated['state']['locks'],'worker_guard_release_unknown')
    return dict(state='released',head=updated['head'])
