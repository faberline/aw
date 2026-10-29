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
    before=adoption.evaluate(api,grant,log_reader)
    args=(grant['repo'],grant['pr'],grant['head'],grant['issue'],policy,run,int(attempt))
    intent=guard.build(*args) if random_bytes is None else guard.build(*args,random_bytes=random_bytes)
    emit(MARKER+canonical(record(document,grant,intent)))
    guard.acquire(api,policy,intent,emit)
    fresh=adoption.evaluate(api,grant,log_reader)
    need(fresh==before and adoption.verify_grant_attestation(api,document,grant,evaluator,log_reader)==attestation,
         'adoption_guard_evidence_changed')
    return dict(policy=policy,intent=intent)


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
