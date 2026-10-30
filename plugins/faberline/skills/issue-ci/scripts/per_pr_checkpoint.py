"""Per-PR status evidence. It does not authorize a merge or close by itself."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_per_pr_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


observation=sibling('post_merge_observation')
guard=sibling('merge_guard')
review=observation.review
need=review.need
MARKER='AW_PER_PR_STATUS_CHECKPOINT_V1 '
BOT_ID=41898282


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True)


def guard_binding(p,record,grant=None,grant_source=None):
    """Bind either a native writer or a separately reviewed publisher."""
    c=record['checkpoint']
    need(all(p[key]==c[key] for key in ('repo','pr','issue','head'))
         and p['run']==record['source_run'] and p['attempt']==record['source_attempt'],
         'per_pr_checkpoint_identity')
    if grant is None:
        need(grant_source is None and record['adoption_source'] is None
             and record['adoption_sha256'] is None and p['policy_source']==record['source_commit'],
             'per_pr_checkpoint_identity')
    else:
        need(isinstance(grant,dict) and review.sha(grant_source)
             and record['adoption_source']==grant_source and record['adoption_sha256']==review.r.digest(grant)
             and p['policy_source']==grant.get('base') and record['source_commit']==grant.get('publisher',{}).get('source_commit')
             and record['review_run']==grant.get('review',{}).get('run_id')
             and record['review_sha256']==grant.get('review',{}).get('receipt_sha256')
             and all(c[key]==grant.get(key) for key in ('repo','pr','issue','head','base','base_ref','issue_sha256','pr_sha256')),
             'per_pr_checkpoint_identity')
    return True


def build(evidence,policy,intent,status,target_url=None,grant=None,grant_source=None):
    """Bind one acknowledged success response to one exact guarded PR."""
    records=observation.parse_records(observation.MARKER+canonical(evidence))
    need(len(records)==1,'per_pr_checkpoint_shape')
    p=guard.validate(intent,policy);record=evidence['payload'];c=record['checkpoint']
    base=f"https://github.com/{c['repo']}/actions/runs/{record['review_run']}"
    target=base if target_url is None else target_url
    need(isinstance(target,str) and (target==base or re.fullmatch(
        re.escape(base)+rf"#aw-status-{record['source_run']}-{record['source_attempt']}-[0-9a-f]{{32}}",target)),
        'per_pr_checkpoint_identity')
    guard_binding(p,record,grant,grant_source)
    need(isinstance(status,dict) and review.integer(status.get('id'))
         and status.get('state')=='success' and status.get('context')=='claude/verified-merge'
         and status.get('target_url')==target and isinstance(status.get('creator'),dict)
         and status['creator'].get('id')==BOT_ID and status['creator'].get('type')=='Bot'
         and status['creator'].get('login')=='github-actions[bot]', 'per_pr_checkpoint_identity')
    payload=dict(version=1,evidence=evidence,owner=p['owner'],claim_op=p['claim_op'],
                 release_op=p['release_op'],status_id=status['id'],target_url=target)
    return dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())


def find(log,repo,pr,head):
    """Find one integrity-checked record; the caller must authenticate source."""
    result=[]
    for value in review.r.records(log,MARKER):
        need(review.exact(value,'payload sha256') and isinstance(value['payload'],dict),'per_pr_checkpoint_shape')
        p=value['payload']
        need(review.exact(p,'version evidence owner claim_op release_op status_id target_url')
             and type(p['version']) is int and p['version']==1
             and all(isinstance(p[k],str) and guard.provider.ledger.OWNER.fullmatch(p[k])
                     for k in ('owner','claim_op','release_op'))
             and len({p['owner'],p['claim_op'],p['release_op']})==3
             and review.integer(p['status_id']) and isinstance(p['target_url'],str)
             and value['sha256']==hashlib.sha256(canonical(p).encode()).hexdigest(),
             'per_pr_checkpoint_shape')
        records=observation.parse_records(observation.MARKER+canonical(p['evidence']))
        need(len(records)==1,'per_pr_checkpoint_shape')
        r=p['evidence']['payload'];c=r['checkpoint']
        base=f"https://github.com/{c['repo']}/actions/runs/{r['review_run']}"
        need(p['target_url']==base or re.fullmatch(re.escape(base)+
             rf"#aw-status-{r['source_run']}-{r['source_attempt']}-[0-9a-f]{{32}}",p['target_url']),
             'per_pr_checkpoint_shape')
        if (c['repo'],c['pr'],c['head'])==(repo,pr,head):result.append(value)
    need(len(result)<=1,'per_pr_checkpoint_duplicate')
    return result[0] if result else None


def verify_status(api,repo,head,value,intent,grant=None,grant_source=None):
    """Require the exact acknowledged status to remain the newest context."""
    p=value['payload'];i=intent['payload'];e=p['evidence']['payload']
    need(all(p[k]==i[k] for k in ('owner','claim_op','release_op')),'per_pr_checkpoint_identity')
    guard_binding(i,e,grant,grant_source)
    statuses=api.pages(f'repos/{repo}/commits/{head}/statuses')
    need(isinstance(statuses,list) and all(isinstance(item,dict)
         and isinstance(item.get('context'),str) for item in statuses),'per_pr_status_unknown')
    matching=[item for item in statuses if item['context'].casefold()=='claude/verified-merge']
    need(bool(matching) and all(type(item.get('id')) is int and 0<item['id']<2**53
         for item in matching) and len({item['id'] for item in matching})==len(matching),
         'per_pr_status_unknown')
    found=[item for item in statuses if isinstance(item,dict) and type(item.get('id')) is int
           and item['id']==p['status_id']]
    target=p['target_url']
    need(len(found)==1 and found[0].get('state')=='success'
         and found[0].get('context')=='claude/verified-merge' and found[0].get('target_url')==target
         and isinstance(found[0].get('creator'),dict) and found[0]['creator'].get('id')==BOT_ID
         and found[0]['creator'].get('type')=='Bot'
         and found[0]['creator'].get('login')=='github-actions[bot]','per_pr_status_unknown')
    # GitHub returns newest first. A later pending, failure, or other actor's
    # success can withdraw or supersede this particular authorization.
    need(matching[0]['id']==p['status_id'],'per_pr_status_superseded')
    return p['evidence']
