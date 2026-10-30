"""Draft merge-side ownership intent and acquisition; not connected to a writer.

The caller must authenticate the workflow source and exact qualification first.
The emitted record is an integrity record, not an authority to merge or close.
Recovery must authenticate the producer and observe the actual merge separately.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
from urllib.parse import quote

_path=Path(__file__).resolve().parents[2]/'issue-work/scripts/worker_guard.py'
_spec=importlib.util.spec_from_file_location('aw_merge_worker_guard',_path)
worker=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(worker)
provider=worker.provider
need=provider.need
MARKER='AW_MERGE_GUARD_INTENT_V1 '
_review_spec=importlib.util.spec_from_file_location('aw_merge_guard_review',Path(__file__).with_name('claude_review.py'))
review=importlib.util.module_from_spec(_review_spec);_review_spec.loader.exec_module(review)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True)


def build(repo,pr,head,issue,policy,run,attempt,random_bytes=secrets.token_hex):
    worker.config(policy)
    need(isinstance(repo,str) and provider.ledger.REPO.fullmatch(repo)
         and type(pr) is int and 0<pr<2**53
         and isinstance(head,str) and provider.ledger.SHA.fullmatch(head)
         and type(issue) is int and provider.ledger.ISSUE.fullmatch(str(issue))
         and type(run) is int and 0<run<2**64
         and type(attempt) is int and 0<attempt<2**32,'merge_guard_identity')
    owner=f'{run:016x}{attempt:08x}'+random_bytes(16)
    claim_op,release_op=random_bytes(16),random_bytes(16)
    payload=dict(version=1,repo=repo,pr=pr,head=head,issue=issue,run=run,attempt=attempt,
        owner=owner,claim_op=claim_op,release_op=release_op,policy_source=policy['policy_source'],
        genesis=policy['genesis'],ruleset_id=policy['ruleset_id'],runtime=dict(policy['runtime']))
    value=dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())
    validate(value,policy)
    return value


def validate(value,policy):
    worker.config(policy)
    keys={'version','repo','pr','head','issue','run','attempt','owner','claim_op','release_op',
          'policy_source','genesis','ruleset_id','runtime'}
    need(isinstance(value,dict) and set(value)=={'payload','sha256'} and isinstance(value['payload'],dict)
         and set(value['payload'])==keys,'merge_guard_intent')
    p=value['payload']
    need(type(p['version']) is int and p['version']==1
         and isinstance(p['repo'],str) and provider.ledger.REPO.fullmatch(p['repo'])
         and type(p['pr']) is int and 0<p['pr']<2**53
         and isinstance(p['head'],str) and provider.ledger.SHA.fullmatch(p['head'])
         and type(p['issue']) is int and provider.ledger.ISSUE.fullmatch(str(p['issue']))
         and type(p['run']) is int and 0<p['run']<2**64
         and type(p['attempt']) is int and 0<p['attempt']<2**32
         and isinstance(p['owner'],str) and len(p['owner'])==56
         and provider.ledger.OWNER.fullmatch(p['owner'])
         and p['owner'].startswith(f"{p['run']:016x}{p['attempt']:08x}")
         and all(isinstance(p[k],str) and provider.ledger.OWNER.fullmatch(p[k])
                 for k in ('claim_op','release_op'))
         and len({p['owner'],p['claim_op'],p['release_op']})==3
         and p['policy_source']==policy['policy_source'] and p['genesis']==policy['genesis']
         and type(p['ruleset_id']) is int and p['ruleset_id']==policy['ruleset_id']
         and p['runtime']==policy['runtime']
         and value['sha256']==hashlib.sha256(canonical(p).encode()).hexdigest(),
         'merge_guard_intent')
    return p


def acquire(api,policy,value,emit):
    """Persist intent before a remote mutation; never take another owner."""
    p=validate(value,policy)
    emit(MARKER+canonical(value))
    claim=dict(owner=p['owner'],kind='merge',head=p['head'])
    state=provider.ensure_ref(api,p['repo'],p['genesis'],p['ruleset_id'],p['issue'])
    existing=state['state']['locks'].get(str(p['issue']))
    if existing==claim:
        need(p['claim_op'] in state['operations'] and p['release_op'] not in state['operations']
             and state['last_operation']==p['claim_op'],'merge_guard_claim_unknown')
        return dict(state='owned',head=state['head'])
    need(existing is None and p['claim_op'] not in state['operations']
         and p['release_op'] not in state['operations'],'merge_guard_held_or_consumed')
    result=provider.update(api,p['repo'],p['genesis'],p['ruleset_id'],p['issue'],
                           'claim',claim,p['claim_op'])
    need(result['state']['locks'].get(str(p['issue']))==claim,'merge_guard_claim_unknown')
    return dict(state='owned',head=result['head'])


def historical_runtime(api,evaluator,policy):
    """Verify historical bytes without executing old or PR-provided code."""
    worker.config(policy)
    aw='faberline/aw';meta=api.api(f'repos/{aw}');branch=meta.get('default_branch')
    need(meta.get('full_name')==aw and review.text(branch,255),'merge_guard_runtime_source')
    comparison=api.api(f'repos/{aw}/compare/{evaluator}...{quote(branch,safe="")}')
    need(comparison.get('status') in ('identical','ahead')
         and comparison.get('base_commit',{}).get('sha')==evaluator,'merge_guard_runtime_source')
    paths={'worker':'plugins/faberline/skills/issue-work/scripts/worker_guard.py',
           'provider':'plugins/faberline/skills/issue-ci/scripts/guard_github.py',
           'ledger':'plugins/faberline/skills/issue-ci/scripts/guard_ledger.py'}
    for key,path in paths.items():
        response=api.api('graphql',method='POST',data=dict(query=worker.POLICY_BLOB,variables=dict(
            owner='faberline',name='aw',expression=evaluator+':'+path)))
        need(isinstance(response,dict) and not response.get('errors') and isinstance(response.get('data'),dict),
             'merge_guard_runtime_source')
        repo=response['data'].get('repository');blob=repo.get('object') if isinstance(repo,dict) else None
        need(isinstance(repo,dict) and repo.get('nameWithOwner')==aw and isinstance(blob,dict)
             and blob.get('__typename')=='Blob' and blob.get('isBinary') is False
             and type(blob.get('byteSize')) is int and 0<=blob['byteSize']<=262144
             and isinstance(blob.get('text'),str),'merge_guard_runtime_source')
        raw=blob['text'].encode('utf-8')
        git_id=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        need(len(raw)==blob['byteSize'] and blob.get('oid')==git_id
             and hashlib.sha256(raw).hexdigest()==policy['runtime'][key], 'merge_guard_runtime_source')
    return True


def authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader,require_success=False):
    """Authenticate a completed trusted attempt and return its job log.

    This only identifies a lock owner. It does not authorize a release, merge,
    or close. Old attempts are fetched explicitly rather than inferred from a
    later rerun. Missing logs, wrong source, or duplicate records are a hold.
    """
    need(isinstance(document,dict) and isinstance(document.get('config'),dict)
         and isinstance(document.get('source_commit'),str)
         and provider.ledger.SHA.fullmatch(document['source_commit'])
         and isinstance(evaluator,str) and provider.ledger.SHA.fullmatch(evaluator)
         and type(run) is int and run>0 and type(attempt) is int and attempt>0
         and type(require_success) is bool,
         'merge_guard_source')
    native=review.sibling('native_merge')
    spec=native.policy(document,require_qualification=False)['producer']
    native.writer_contract(api,repo,document,evaluator)
    configured=document['config'].get('issue_guard')
    need(isinstance(configured,dict) and 'policy_source' not in configured,'merge_guard_source')
    policy=worker.config(dict(configured,policy_source=document['source_commit']))
    historical_runtime(api,evaluator,policy)
    meta=api.api(f'repos/{repo}')
    branch=meta.get('default_branch')
    need(meta.get('full_name')==repo and review.text(branch,255),'merge_guard_source')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('workflow_id')==spec['id'] and row.get('path')==spec['path']
         and row.get('event') in ('issues','workflow_run','repository_dispatch')
         and row.get('status')=='completed' and row.get('conclusion') in ('success','failure','cancelled','timed_out')
         and row.get('head_repository',{}).get('full_name')==repo
         and row.get('head_branch')==branch and row.get('head_sha')==document['source_commit'],
         'merge_guard_source')
    review.blob(api,repo,spec['path'],document['source_commit'],spec['definition_sha'])
    comparison=api.api(f"repos/{repo}/compare/{document['source_commit']}...{quote(branch,safe='')}")
    need(comparison.get('status') in ('identical','ahead')
         and comparison.get('base_commit',{}).get('sha')==document['source_commit'],
         'merge_guard_source')
    jobs=api.pages(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs','jobs')
    matches=[job for job in jobs if job.get('name')==spec['job']]
    need(len(matches)==1 and review.integer(matches[0].get('id'))
         and matches[0].get('status')=='completed','merge_guard_source')
    if require_success:
        need(row.get('conclusion')=='success' and matches[0].get('conclusion')=='success',
             'merge_guard_source_not_success')
    return policy,log_reader(repo,matches[0]['id'])


def authenticate_native_intent(api,repo,document,evaluator,run,attempt,pr,head,log_reader):
    """Identify one authenticated lock owner; never authorize a write."""
    policy,log=authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader)
    records=review.r.records(log,MARKER)
    found=[]
    for value in records:
        p=validate(value,policy)
        need(p['repo']==repo and p['run']==run and p['attempt']==attempt,'merge_guard_source')
        if p['pr']==pr and p['head']==head:found.append(value)
    need(len(found)==1,'merge_guard_record_missing_or_duplicate')
    return found[0]


def identify_native_owner(api,repo,document,evaluator,run,attempt,issue,head,owner,log_reader):
    """Find the PR bound to a discovered guard by authenticating its source."""
    policy,log=authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader)
    found=[]
    for value in review.r.records(log,MARKER):
        p=validate(value,policy)
        need(p['repo']==repo and p['run']==run and p['attempt']==attempt,'merge_guard_source')
        if p['issue']==issue and p['head']==head and p['owner']==owner:found.append(value)
    need(len(found)==1,'merge_guard_record_missing_or_duplicate')
    return found[0]
