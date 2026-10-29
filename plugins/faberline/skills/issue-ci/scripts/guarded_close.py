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
per_pr=sibling('per_pr_checkpoint')
prepared=sibling('prepared_status')
native=sibling('native_merge')
worker=merge_guard.worker
provider=merge_guard.provider
need=provider.need


def authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,head,log_reader,intent=None):
    """Authenticate one candidate even when another PR caused the job to fail."""
    policy,log=merge_guard.authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader)
    candidate=per_pr.find(log,repo,pr,head)
    if candidate is not None:
        need(intent is not None,'merge_close_guard_missing')
        merge_guard.validate(intent,policy)
        evidence=per_pr.verify_status(api,repo,head,candidate,intent)
        p=evidence['payload'];c=p['checkpoint']
        need(p['source_run']==run and p['source_attempt']==attempt
             and p['source_commit']==document['source_commit'] and p['evaluator']==evaluator
             and p['adoption_sha256'] is None and p['adoption_source'] is None
             and c['repo']==repo and c['pr']==pr and c['head']==head,'merge_close_source_mismatch')
        return evidence
    # A legacy record has no status ID. Even a successful source job cannot
    # prove its status remains the latest one for this PR. Let recovery look
    # for a bound prepared record, otherwise keep the guard and hold.
    raise provider.Refused('merge_close_record_missing_or_duplicate')


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
    evidence=authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,head,log_reader,intent=intent)
    p=intent['payload'];record=evidence['payload'];checkpoint=record['checkpoint']
    need(record['source_attempt']==attempt and record['source_run']==run
         and record['source_commit']==document['source_commit'] and record['evaluator']==evaluator
         and checkpoint['repo']==repo and checkpoint['pr']==pr and checkpoint['head']==head
         and checkpoint['issue']==p['issue'] and p['repo']==repo and p['pr']==pr and p['head']==head,
         'merge_guard_checkpoint_mismatch')
    def revalidate():
        # The acknowledged status can be superseded while the recovery wake
        # is observing issue ownership. Re-read it under the same guard.
        fresh=authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,head,log_reader,intent=intent)
        need(fresh==evidence,'merge_close_evidence_changed')
        revalidate_native_merged(api,repo,document,evaluator,pr,head,record,p['issue'],log_reader)
    return _complete_authenticated(api,repo,current_policy,intent,evidence,revalidate=revalidate)


def revalidate_native_merged(api,repo,document,evaluator,pr,head,record,issue,log_reader):
    """Recheck the current review and complete qualified CI after merging."""
    checkpoint=record['checkpoint']
    current=merge_guard.review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    need(current['config']==document['config'],'merge_recovery_policy_changed')
    index=sibling('native_review_index').Session(api,repo,lambda:document,evaluator,log_reader)
    receipt=index.verify_merged(api,repo,pr,head,lambda:document,evaluator,record['review_run'])
    context=receipt['payload']['context']
    need(receipt['sha256']==record['review_sha256']
         and context['repo']==repo and context['pr']==pr and context['head']==head
         and context['base']==checkpoint['base'] and context['base_ref']==checkpoint['base_ref']
         and context['issue_sha256']==checkpoint['issue_sha256']
         and context['pr_description_sha256']==checkpoint['pr_sha256'],
         'merge_recovery_review_changed')
    need(native.qualification_merged(api,repo,pr,context,document)==issue,
         'merge_recovery_qualification_changed')


def reconcile_native_prepared(api,repo,document,current_policy,evaluator,run,attempt,pr,head,owner,log_reader):
    """Revalidate review and CI after a lost status response, then close safely.

    The observed status is not authority. The trusted pre-status record binds
    the original attempt and guard. The latest exact review is independently
    authenticated and the full original context and CI are rebuilt twice.
    """
    old=worker.config(dict(document['config']['issue_guard'],policy_source=document['source_commit']))
    worker.config(current_policy);worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    intent=merge_guard.authenticate_native_intent(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    p=intent['payload']
    need(p['owner']==owner and p['repo']==repo and p['pr']==pr and p['head']==head,
         'merge_guard_checkpoint_mismatch')
    value=prepared.authenticate_native_prepared(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    evidence=value['payload']['checkpoint'];record=evidence['payload'];checkpoint=record['checkpoint']
    per_pr.guard_binding(p,record)
    need(checkpoint['issue']==p['issue'],'merge_guard_checkpoint_mismatch')
    def revalidate():
        revalidate_native_merged(api,repo,document,evaluator,pr,head,record,p['issue'],log_reader)
    revalidate()
    return _complete_authenticated(api,repo,current_policy,intent,evidence,revalidate=revalidate)


def reconcile_adoption(api,repo,document,current_policy,evaluator,run,attempt,issue,head,owner,log_reader):
    """Use a separately reviewed publisher's authenticated guard and status."""
    adoption=sibling('adoption_guard')
    verified=adoption.authenticate_checkpoint(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
    old,_=adoption.source_policy(api,verified['grant'])
    worker.config(current_policy);worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    def revalidate():
        fresh=adoption.authenticate_checkpoint(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
        need(fresh==verified,'adoption_guard_evidence_changed')
        result=adoption.adoption.evaluate_merged(api,verified['grant'],log_reader)
        need(result['issue']==issue and adoption.review.r.digest(result['receipt'])==verified['evidence']['payload']['review_sha256'],
             'merge_recovery_review_changed')
    return _complete_authenticated(api,repo,current_policy,verified['intent'],verified['evidence'],revalidate=revalidate)


def reconcile_adoption_prepared(api,repo,document,current_policy,evaluator,run,attempt,issue,head,owner,log_reader):
    """Revalidate the independent grant, target review and full CI after merge."""
    adoption=sibling('adoption_guard')
    verified=adoption.find_prepared(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
    need(verified is not None,'adoption_prepared_missing')
    old,_=adoption.source_policy(api,verified['grant'])
    worker.config(current_policy);worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    intent=verified['intent'];record=verified['evidence']['payload'];p=intent['payload']
    need(p['owner']==owner and p['repo']==repo and p['issue']==issue and p['head']==head
         and record['checkpoint']['repo']==repo and record['checkpoint']['issue']==issue
         and record['checkpoint']['head']==head,'merge_guard_checkpoint_mismatch')
    def revalidate():
        fresh=adoption.find_prepared(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
        need(fresh==verified,'adoption_guard_evidence_changed')
        result=adoption.adoption.evaluate_merged(api,verified['grant'],log_reader)
        need(result['issue']==issue and adoption.review.r.digest(result['receipt'])==record['review_sha256'],
             'merge_recovery_review_changed')
    revalidate()
    return _complete_authenticated(api,repo,current_policy,intent,verified['evidence'],revalidate=revalidate)


def release_closed_prepared(api,repo,document,current_policy,evaluator,run,attempt,pr,head,owner,log_reader):
    """Release an authenticated guard only when the exact issue is already closed.

    A pre-status record cannot authorize closing an open issue. The merge and
    the already-closed issue are independently read twice before release.
    """
    old=worker.config(dict(document['config']['issue_guard'],policy_source=document['source_commit']))
    worker.config(current_policy);worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    intent=merge_guard.authenticate_native_intent(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    p=intent['payload']
    need(p['owner']==owner and p['repo']==repo and p['pr']==pr and p['head']==head,
         'merge_guard_checkpoint_mismatch')
    value=prepared.authenticate_native_prepared(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    evidence=value['payload']['checkpoint'];record=evidence['payload'];checkpoint=record['checkpoint']
    per_pr.guard_binding(p,record)
    need(checkpoint['issue']==p['issue'],'merge_guard_checkpoint_mismatch')
    return _release_observed_closed(api,repo,current_policy,intent,checkpoint)


def release_adoption_closed_prepared(api,repo,document,current_policy,evaluator,run,attempt,issue,head,owner,log_reader):
    """Release only an already-closed issue's reviewed adoption guard."""
    adoption=sibling('adoption_guard')
    verified=adoption.find_prepared(api,document,repo,evaluator,run,attempt,issue,head,owner,log_reader)
    need(verified is not None,'adoption_prepared_missing')
    old,_=adoption.source_policy(api,verified['grant'])
    worker.config(current_policy);worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    intent=verified['intent'];p=intent['payload'];checkpoint=verified['evidence']['payload']['checkpoint']
    need(p['owner']==owner and p['repo']==repo and p['issue']==issue and p['head']==head
         and checkpoint['repo']==repo and checkpoint['issue']==issue and checkpoint['head']==head,
         'merge_guard_checkpoint_mismatch')
    return _release_observed_closed(api,repo,current_policy,intent,checkpoint)


def _release_observed_closed(api,repo,current_policy,intent,checkpoint):
    """Release the same owner after two matching closed-issue observations."""
    p=intent['payload']
    require_current_policy(api,repo,current_policy)
    guard_identity(api,repo,current_policy,intent)
    first=observation.observe(api,checkpoint)
    need(first['state']=='observed_closed','merge_issue_not_already_closed')
    require_current_policy(api,repo,current_policy)
    guard_identity(api,repo,current_policy,intent)
    final=observation.observe(api,checkpoint)
    need(final==first and final['state']=='observed_closed','merge_close_state_changed')
    require_current_policy(api,repo,current_policy)
    guard_identity(api,repo,current_policy,intent)
    release(api,repo,current_policy,intent)
    return dict(state='released_after_observed_closed',issue=p['issue'],merge=final['merge'])


def _complete_authenticated(api,repo,current_policy,intent,evidence,revalidate=None):
    """Finish only after a trusted caller authenticates the source evidence."""
    p=intent['payload'];record=evidence['payload'];checkpoint=record['checkpoint']
    need(record['source_attempt']==p['attempt'] and record['source_run']==p['run']
         and checkpoint['repo']==repo and checkpoint['pr']==p['pr'] and checkpoint['head']==p['head']
         and checkpoint['issue']==p['issue'] and p['repo']==repo,'merge_guard_checkpoint_mismatch')
    started=observation.source_started_at(api,evidence,require_success=False)
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
        if revalidate is not None:
            revalidate()
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
