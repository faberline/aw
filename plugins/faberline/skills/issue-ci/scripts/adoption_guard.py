"""Draft guard for reviewed policy adoption; not wired into the publisher.

The rollout verifier currently refuses all guarded adoption. This module is
not authority to bypass that hold or the separate policy and grant reviews.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import quote


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_adoption_guard_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


adoption=sibling('policy_adoption')
guard=sibling('merge_guard')
recovery=sibling('recovery_entry')
per_pr=sibling('per_pr_checkpoint')
prepared=sibling('prepared_status')
review=adoption.review
need=review.need
MARKER='AW_ADOPTION_GUARD_INTENT_V1 '


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True)


def source_policy(api,grant,require_current=False):
    """Read both exact policy blobs; never accept a caller-provided policy."""
    repo=grant['repo'];path='.github/issue-ci-required-gates.json'
    candidate=adoption.ci.strict_json_loads(review.blob(api,repo,path,grant['head'],grant['policy_blob']))
    prior=adoption.ci.strict_json_loads(review.blob(api,repo,path,grant['base'],grant['base_policy_blob']))
    need(isinstance(candidate,dict) and isinstance(prior,dict),'adoption_guard_policy')
    proposed=candidate.get('issue_guard');old=prior.get('issue_guard')
    need(isinstance(proposed,dict) and 'policy_source' not in proposed
         and ('issue_guard' not in prior or old==proposed),'adoption_guard_policy')
    policy=guard.worker.config(dict(proposed,policy_source=grant['base']))
    if require_current:
        meta=api.api(f'repos/{repo}')
        need(meta.get('full_name')==repo and meta.get('default_branch')==grant['base_ref'],
             'adoption_guard_base')
        current=api.api(f"repos/{repo}/commits/{quote(grant['base_ref'],safe='')}")
        need(current.get('sha')==grant['base'],'adoption_guard_base')
    return policy,candidate


def record(document,grant,intent):
    need(isinstance(document,dict) and review.sha(document.get('source_commit')),'adoption_guard_record')
    payload=dict(version=1,grant_source=document['source_commit'],grant_sha256=adoption.r.digest(grant),intent=intent)
    return dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())


def grant_from_default(api,document,repo,evaluator):
    """Bind the exact reviewed grant document to AW default history."""
    need(isinstance(document,dict) and review.sha(document.get('source_commit')),'adoption_guard_source')
    grant=adoption.choose_grant(document.get('config'),repo,evaluator)
    meta=api.api(f'repos/{adoption.GRANTS_REPO}');branch=meta.get('default_branch')
    need(meta.get('full_name')==adoption.GRANTS_REPO and review.text(branch,255),'adoption_guard_source')
    comparison=api.api(f"repos/{adoption.GRANTS_REPO}/compare/{document['source_commit']}...{quote(branch,safe='')}")
    need(comparison.get('status') in ('identical','ahead')
         and comparison.get('base_commit',{}).get('sha')==document['source_commit'],'adoption_guard_source')
    return grant


def begin(api,document,grant,evaluator,run,log_reader,emit,random_bytes=None):
    """Verify the grant and policy before recording and acquiring the guard."""
    pub=grant['publisher'];attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')==grant['repo']
         and os.environ.get('GITHUB_EVENT_NAME')=='push' and os.environ.get('GITHUB_RUN_ID')==str(run)
         and os.environ.get('GITHUB_SHA')==pub['source_commit']
         and os.environ.get('GITHUB_WORKFLOW_SHA')==pub['source_commit']
         and os.environ.get('GITHUB_REF')=='refs/heads/'+pub['branch']
         and os.environ.get('AW_ACTION_REF')==evaluator and re.fullmatch(r'[1-9][0-9]*',attempt),
         'adoption_guard_source')
    need(grant_from_default(api,document,grant['repo'],evaluator)==grant,'adoption_guard_source')
    attestation=adoption.verify_grant_attestation(api,document,grant,evaluator,log_reader)
    identity=adoption.publisher_provenance(api,grant,run,'push')
    need(identity['run_attempt']==int(attempt),'adoption_guard_source')
    policy,candidate=source_policy(api,grant,require_current=True)
    guard.worker.check_runtime(policy)
    # This currently always refuses. It must later authenticate actor rollout.
    recovery.verify_rollout(api,grant['repo'],dict(config=candidate,source_commit=grant['base']))
    before=adoption._evaluate_guarded(api,grant,log_reader)
    args=(grant['repo'],grant['pr'],grant['head'],grant['issue'],policy,run,int(attempt))
    intent=guard.build(*args) if random_bytes is None else guard.build(*args,random_bytes=random_bytes)
    emit(MARKER+canonical(record(document,grant,intent)))
    guard.acquire(api,policy,intent,emit)
    fresh=adoption._evaluate_guarded(api,grant,log_reader)
    need(fresh==before and adoption.verify_grant_attestation(api,document,grant,evaluator,log_reader)==attestation,
         'adoption_guard_evidence_changed')
    return dict(policy=policy,intent=intent)


def recheck(api,document,grant,evaluator,run,claim,log_reader):
    """Recheck exact grant, base and owned guard before each publisher write."""
    need(grant_from_default(api,document,grant['repo'],evaluator)==grant,'adoption_guard_source')
    attestation=adoption.verify_grant_attestation(api,document,grant,evaluator,log_reader)
    identity=adoption.publisher_provenance(api,grant,run,'push')
    policy,_=source_policy(api,grant,require_current=True)
    guard.worker.check_runtime(policy)
    need(isinstance(claim,dict) and set(claim)=={'policy','intent'} and claim['policy']==policy,
         'adoption_guard_policy')
    p=guard.validate(claim['intent'],policy)
    need(p['run']==run and p['attempt']==identity['run_attempt']
         and p['repo']==grant['repo'] and p['pr']==grant['pr']
         and p['issue']==grant['issue'] and p['head']==grant['head'],'adoption_guard_source')
    close=sibling('guarded_close')
    close.guard_identity(api,grant['repo'],policy,claim['intent'])
    return attestation


def authenticate_intent(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader):
    """Authenticate one terminal publisher intent; never authorize a close."""
    grant=grant_from_default(api,document,repo,evaluator)
    need(grant['repo']==repo and grant['issue']==issue and grant['head']==head,'adoption_guard_source')
    adoption.verify_grant_attestation(api,document,grant,evaluator,log_reader)
    identity=adoption.publisher_provenance(api,grant,run,'push',attempt=attempt,terminal=True)
    policy,_=source_policy(api,grant)
    guard.historical_runtime(api,evaluator,policy)
    items=review.r.records(log_reader(repo,identity['job_id']),MARKER);found=[]
    for item in items:
        need(review.exact(item,'payload sha256') and isinstance(item['payload'],dict), 'adoption_guard_record')
        p=item['payload']
        need(review.exact(p,'version grant_source grant_sha256 intent') and type(p['version']) is int
             and p['version']==1 and item==record(document,grant,p['intent']), 'adoption_guard_record')
        inner=guard.validate(p['intent'],policy)
        need(p['grant_source']==document['source_commit'] and p['grant_sha256']==adoption.r.digest(grant)
             and inner['repo']==repo and inner['pr']==grant['pr'] and inner['issue']==issue
             and inner['head']==head and inner['run']==run and inner['attempt']==attempt,
             'adoption_guard_record')
        if inner['owner']==owner:found.append(p['intent'])
    need(len(found)==1,'adoption_guard_missing_or_duplicate')
    return found[0]


def authenticate_checkpoint(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader):
    """Verify a reviewed publisher's exact per-PR status and guard record."""
    grant=grant_from_default(api,document,repo,evaluator)
    intent=authenticate_intent(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
    identity=adoption.publisher_provenance(api,grant,run,'push',attempt=attempt,terminal=True)
    value=per_pr.find(log_reader(repo,identity['job_id']),repo,grant['pr'],head)
    need(value is not None,'adoption_checkpoint_missing')
    evidence=per_pr.verify_status(api,repo,head,value,intent,grant=grant,grant_source=document['source_commit'])
    p=evidence['payload']
    need(p['source_run']==run and p['source_attempt']==attempt and p['evaluator']==evaluator,
         'adoption_checkpoint_source')
    return dict(evidence=evidence,intent=intent,grant=grant)


def find_prepared(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader):
    """Authenticate a reviewed publisher's pre-status record, not a close."""
    grant=grant_from_default(api,document,repo,evaluator)
    intent=authenticate_intent(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
    identity=adoption.publisher_provenance(api,grant,run,'push',attempt=attempt,terminal=True)
    found=[]
    for value in review.r.records(log_reader(repo,identity['job_id']),prepared.MARKER):
        payload=prepared.validate(value);evidence=payload['checkpoint'];record=evidence['payload']
        per_pr.guard_binding(intent['payload'],record,grant,document['source_commit'])
        need(record['source_run']==run and record['source_attempt']==attempt
             and record['evaluator']==evaluator,'adoption_checkpoint_source')
        found.append(value)
    need(len(found)<=1,'adoption_prepared_duplicate')
    if not found:return None
    return dict(evidence=found[0]['payload']['checkpoint'],intent=intent,grant=grant,prepared=found[0])


def discover_source(api,repo,run,attempt,issue,head,owner,log_reader):
    """Use an untrusted marker only to locate a subsequently verified grant."""
    need(isinstance(repo,str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo)
         and all(review.integer(x) for x in (run,attempt,issue)) and review.sha(head)
         and isinstance(owner,str) and guard.provider.ledger.OWNER.fullmatch(owner),'adoption_guard_source')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('event')=='push'
         and row.get('head_repository',{}).get('full_name')==repo,'adoption_guard_source')
    jobs=api.pages(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs','jobs')
    need(isinstance(jobs,list) and len(jobs)==1 and review.integer(jobs[0].get('id'))
         and jobs[0].get('status')=='completed','adoption_guard_source')
    markers=review.r.records(log_reader(repo,jobs[0]['id']),MARKER);matches=[]
    for item in markers:
        payload=item.get('payload') if isinstance(item,dict) else None
        wrapped=payload.get('intent') if isinstance(payload,dict) else None
        intent=wrapped.get('payload') if isinstance(wrapped,dict) else None
        if (isinstance(intent,dict) and intent.get('repo')==repo and intent.get('issue')==issue
                and intent.get('head')==head and intent.get('run')==run and intent.get('attempt')==attempt
                and intent.get('owner')==owner):
            matches.append(payload)
    need(len(matches)==1 and review.sha(matches[0].get('grant_source'))
         and isinstance(matches[0].get('grant_sha256'),str)
         and re.fullmatch(r'[0-9a-f]{64}',matches[0]['grant_sha256']), 'adoption_guard_source')
    source=matches[0]['grant_source']
    raw=review.blob(api,adoption.GRANTS_REPO,adoption.GRANTS_PATH,source)
    config=adoption.ci.strict_json_loads(raw)
    need(isinstance(config,dict) and isinstance(config.get('grants'),list),'adoption_guard_source')
    grants=[g for g in config['grants'] if isinstance(g,dict) and g.get('repo')==repo
            and g.get('issue')==issue and g.get('head')==head and adoption.r.digest(g)==matches[0]['grant_sha256']]
    need(len(grants)==1 and review.sha(grants[0].get('evaluator')),'adoption_guard_source')
    document=dict(config=config,source_commit=source);evaluator=grants[0]['evaluator']
    verified=authenticate_intent(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
    return dict(document=document,evaluator=evaluator,pr=verified['payload']['pr'],intent=verified)
