"""Draft guarded close for an authenticated native writer; not wired or deployed.

The shared guard only coordinates actors that obey it. Rollout requires proof
that all active workers and writers use it. External actors can bypass it.
"""
import importlib.util
from pathlib import Path


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_guarded_close_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


merge_guard=sibling('merge_guard')
observation=sibling('post_merge_observation')
worker=merge_guard.worker
provider=merge_guard.provider
need=provider.need


def authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,head,log_reader):
    """Bind a success checkpoint to the specific attempt, even after reruns."""
    _,log=merge_guard.authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader,
                                               require_success=True)
    records=observation.parse_records(log);matches=[]
    for evidence in records:
        p=evidence['payload'];c=p['checkpoint']
        need(p['source_run']==run and p['source_attempt']==attempt
             and p['source_commit']==document['source_commit'] and p['evaluator']==evaluator
             and p['adoption_sha256'] is None and p['adoption_source'] is None
             and c['repo']==repo,'merge_close_source_mismatch')
        if c['pr']==pr and c['head']==head:matches.append(evidence)
    need(len(matches)==1,'merge_close_record_missing_or_duplicate')
    return matches[0]


def guard_identity(api,repo,policy,intent):
    """Read the same owner and exact last claim; never reacquire it."""
    p=intent['payload']
    worker.policy_ancestry(api,repo,p,policy['policy_source'])
    state=provider.read_chain(api,repo,policy['genesis'],policy['ruleset_id'],p['issue'])
    claim=dict(owner=p['owner'],kind='merge',head=p['head'])
    need(state['state']['locks'].get(str(p['issue']))==claim
         and p['claim_op'] in state['operations'] and p['release_op'] not in state['operations']
         and state['last_operation']==p['claim_op'],'merge_guard_not_owner')
    return state


def release(api,repo,policy,intent):
    """Release just this completed claim, or observe its completed release."""
    p=intent['payload']
    worker.policy_ancestry(api,repo,p,policy['policy_source'])
    state=provider.read_chain(api,repo,policy['genesis'],policy['ruleset_id'],p['issue'])
    claim=dict(owner=p['owner'],kind='merge',head=p['head'])
    current=state['state']['locks'].get(str(p['issue']))
    if current is None:
        need(p['claim_op'] in state['operations'] and p['release_op'] in state['operations']
             and state['last_operation']==p['release_op'],'merge_guard_release_unknown')
        return
    need(current==claim and p['claim_op'] in state['operations'] and p['release_op'] not in state['operations']
         and state['last_operation']==p['claim_op'],'merge_guard_not_owner')
    result=provider.update(api,repo,policy['genesis'],policy['ruleset_id'],p['issue'],
                           'release',claim,p['release_op'])
    need(str(p['issue']) not in result['state']['locks'],'merge_guard_release_unknown')


def require_current_policy(api,repo,expected):
    """Refuse a source or settings change before any guarded write."""
    current=worker.read_policy(api,repo)
    need(current==expected,'merge_guard_policy_changed')


def reconcile_native(api,repo,document,current_policy,evaluator,run,attempt,pr,head,log_reader):
    """Authenticate source and guard, then close only a verified merged issue.

    Caller supplies policy loaded from the current default branch. Old and new
    policy must retain the same guard settings and have verified ancestry. An
    unknown close response is held and later observed; it is not blindly retried.
    This function does not discover candidates or schedule its own retry.
    """
    old=worker.config(dict(document['config']['issue_guard'],policy_source=document['source_commit']))
    worker.config(current_policy);worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    intent=merge_guard.authenticate_native_intent(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    evidence=authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    p=intent['payload'];record=evidence['payload'];checkpoint=record['checkpoint']
    need(record['source_attempt']==attempt and record['source_run']==run
         and record['source_commit']==document['source_commit'] and record['evaluator']==evaluator
         and checkpoint['repo']==repo and checkpoint['pr']==pr and checkpoint['head']==head
         and checkpoint['issue']==p['issue'] and p['repo']==repo and p['pr']==pr and p['head']==head,
         'merge_guard_checkpoint_mismatch')
    started=observation.source_started_at(api,evidence)
    planned=observation.recovery_plan(api,checkpoint,started)
    if planned['state'] in ('waiting','not_merged'):
        return dict(state=planned['state'],issue=p['issue'])
    if planned['state']=='guard_required':
        require_current_policy(api,repo,current_policy)
        guard_identity(api,repo,current_policy,intent)
        # Re-run all mutable issue and ownership checks after obtaining the
        # guard, then immediately confirm it still belongs to this intent.
        again=observation.recovery_plan(api,checkpoint,started)
        need(again['state']=='guard_required','merge_close_state_changed')
        require_current_policy(api,repo,current_policy)
        guard_identity(api,repo,current_policy,intent)
        result=api.api(f"repos/{repo}/issues/{p['issue']}",method='PATCH',
                       data=dict(state='closed',state_reason='completed'))
        need(isinstance(result,dict) and result.get('number')==p['issue']
             and result.get('state')=='closed','merge_close_result_unknown')
    else:
        need(planned['state']=='observed_closed','merge_close_state_changed')
    final=observation.observe(api,checkpoint)
    need(final['state']=='observed_closed','merge_close_result_unknown')
    require_current_policy(api,repo,current_policy)
    release(api,repo,current_policy,intent)
    return dict(state='closed',issue=p['issue'],merge=final['merge'])
