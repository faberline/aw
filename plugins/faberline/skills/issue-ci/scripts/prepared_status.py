"""Draft pre-status evidence and read-only status observation; not deployed.

An integrity hash is not authentication. The recovery caller must authenticate
the writer and guard, verify the actual merge and issue, and establish exclusive
writer authority before treating status observations as evidence.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import secrets

_spec=importlib.util.spec_from_file_location('aw_prepared_observation',Path(__file__).with_name('post_merge_observation.py'))
observation=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(observation)
_guard_spec=importlib.util.spec_from_file_location('aw_prepared_merge_guard',Path(__file__).with_name('merge_guard.py'))
guard=importlib.util.module_from_spec(_guard_spec);_guard_spec.loader.exec_module(guard)
need=observation.need
MARKER='AW_PREPARED_STATUS_V1 '
BOT_ID=41898282


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True)


def build(checkpoint,pending_id,nonce=None):
    """Make a unique candidate before requesting success. Does not write."""
    entries=observation.parse_records(observation.MARKER+canonical(checkpoint))
    need(len(entries)==1 and type(pending_id) is int and 0<pending_id<2**53,
         'prepared_status_shape')
    nonce=secrets.token_hex(16) if nonce is None else nonce
    need(isinstance(nonce,str) and re.fullmatch(r'[0-9a-f]{32}',nonce),'prepared_status_shape')
    payload=dict(version=1,checkpoint=checkpoint,pending_id=pending_id,nonce=nonce)
    return dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())


def target_identity(repo,review_run,source_run,source_attempt,nonce):
    need(isinstance(repo,str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo)
         and all(observation.review.integer(x) for x in (review_run,source_run,source_attempt))
         and isinstance(nonce,str) and re.fullmatch(r'[0-9a-f]{32}',nonce),'prepared_status_shape')
    return (f'https://github.com/{repo}/actions/runs/{review_run}'
            f'#aw-status-{source_run}-{source_attempt}-{nonce}')


def build_guarded(checkpoint,policy,intent,pending,nonce):
    """Bind a pre-success record to the acknowledged pending status and guard."""
    p=guard.validate(intent,policy);r=checkpoint.get('payload',{});c=r.get('checkpoint',{})
    need(all(p[k]==c.get(k) for k in ('repo','pr','issue','head'))
         and p['run']==r.get('source_run') and p['attempt']==r.get('source_attempt')
         and p['policy_source']==r.get('source_commit'),'prepared_status_identity')
    url=target_identity(c['repo'],r['review_run'],p['run'],p['attempt'],nonce)
    need(isinstance(pending,dict) and observation.review.integer(pending.get('id'))
         and pending.get('state')=='pending' and pending.get('context')=='claude/verified-merge'
         and pending.get('target_url')==url and isinstance(pending.get('creator'),dict)
         and pending['creator'].get('id')==BOT_ID and pending['creator'].get('type')=='Bot'
         and pending['creator'].get('login')=='github-actions[bot]','prepared_pending_unknown')
    return build(checkpoint,pending['id'],nonce)


def validate(value):
    need(isinstance(value,dict) and set(value)=={'payload','sha256'} and isinstance(value['payload'],dict),
         'prepared_status_shape')
    p=value['payload']
    need(set(p)=={'version','checkpoint','pending_id','nonce'} and type(p['version']) is int and p['version']==1,
         'prepared_status_shape')
    need(value==build(p['checkpoint'],p['pending_id'],p['nonce']),'prepared_status_digest')
    return p


def target_for(checkpoint,nonce):
    """Construct the same target before posting the pending status."""
    entries=observation.parse_records(observation.MARKER+canonical(checkpoint))
    need(len(entries)==1 and isinstance(nonce,str) and re.fullmatch(r'[0-9a-f]{32}',nonce),
         'prepared_status_shape')
    record=checkpoint['payload'];repo=record['checkpoint']['repo']
    return target_identity(repo,record['review_run'],record['source_run'],record['source_attempt'],nonce)


def target(value):
    p=validate(value)
    return target_for(p['checkpoint'],p['nonce'])


def observe(value,statuses):
    """Check matching pending and newer latest success, without authorizing it."""
    p=validate(value);url=target(value)
    need(isinstance(statuses,list),'prepared_status_unknown')
    same=[x for x in statuses if isinstance(x,dict) and isinstance(x.get('context'),str)
          and x['context'].casefold()=='claude/verified-merge']
    need(same and all(type(x.get('id')) is int and 0<x['id']<2**53 for x in same)
         and len({x['id'] for x in same})==len(same),'prepared_status_unknown')
    pending=[x for x in same if x['id']==p['pending_id']]
    def mine(item,state):
        return (item.get('state')==state and item.get('target_url')==url
                and isinstance(item.get('creator'),dict) and item['creator'].get('id')==BOT_ID
                and item['creator'].get('login')=='github-actions[bot]')
    need(len(pending)==1 and mine(pending[0],'pending'),'prepared_pending_unknown')
    latest=max(same,key=lambda item:item['id'])
    need(latest['id']>p['pending_id'] and mine(latest,'success'),'prepared_success_unknown')
    return dict(state='observed_success',status_id=latest['id'],target=url,close_authorized=False)


def find_native_prepared(api,repo,document,evaluator,run,attempt,pr,head,log_reader):
    """Authenticate one prepared record from the exact completed attempt.

    Failure or cancellation may still have left an accepted status. The result
    remains a checkpoint, not a merge or close authorization.
    """
    _,log=guard.authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader)
    records=observation.review.r.records(log,MARKER);found=[]
    for value in records:
        p=validate(value);r=p['checkpoint']['payload'];c=r['checkpoint']
        need(r['source_run']==run and r['source_attempt']==attempt
             and r['source_commit']==document['source_commit'] and r['evaluator']==evaluator
             and c['repo']==repo and r['adoption_sha256'] is None and r['adoption_source'] is None,
             'prepared_status_source')
        if c['pr']==pr and c['head']==head:found.append(value)
    need(len(found)<=1,'prepared_status_duplicate')
    return found[0] if found else None


def authenticate_native_prepared(api,repo,document,evaluator,run,attempt,pr,head,log_reader):
    value=find_native_prepared(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    need(value is not None,'prepared_status_missing_or_duplicate')
    return value
