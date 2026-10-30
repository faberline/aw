"""Read-only identification of a crashed merged-grant outcome writer.

This does not release a lock or change an issue.  A recovery writer must
recheck all evidence and the same guard before attempting either operation.
"""
import importlib.util
from pathlib import Path
import re


def sibling(name):
    spec=importlib.util.spec_from_file_location('merged_recovery_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


outcome=sibling('policy_adoption_merged_outcome')
guard=sibling('merge_guard')
close=sibling('guarded_close')
review=guard.review
need=guard.need
PATH='.github/workflows/review-merged-product-grant.yml'
ACTION='faberline/aw/.github/actions/policy-adoption-merged-grant-outcome@'


def authenticate(api,repo,policy,run,attempt,issue,head,owner,log_reader):
    """Authenticate one terminal outcome attempt and its exact held guard."""
    guard.worker.config(policy)
    need(isinstance(repo,str) and guard.provider.ledger.REPO.fullmatch(repo)
         and all(review.integer(x) for x in (run,attempt,issue)) and review.sha(head)
         and isinstance(owner,str) and guard.provider.ledger.OWNER.fullmatch(owner),
         'merged_outcome_recovery_source')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('event')=='push'
         and row.get('path')==PATH and row.get('head_repository',{}).get('full_name')==repo
         and review.sha(row.get('head_sha')) and isinstance(row.get('head_branch'),str),
         'merged_outcome_recovery_source')
    raw=review.blob(api,repo,PATH,row['head_sha'])
    import yaml
    workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    try:
        step=workflow['jobs']['outcome']['steps'][0]
        action=step['uses'];inputs=step['with']
        grant_pr,grant_head,target_pr,target_head=(inputs[k] for k in ('grant-pr','grant-head','target-pr','target-head'))
    except (TypeError,KeyError,IndexError):
        raise review.Refused('merged_outcome_recovery_workflow') from None
    need(isinstance(action,str) and action.startswith(ACTION) and review.sha(action[len(ACTION):])
         and isinstance(grant_pr,str) and re.fullmatch(r'[1-9][0-9]*',grant_pr)
         and isinstance(target_pr,str) and re.fullmatch(r'[1-9][0-9]*',target_pr)
         and review.sha(grant_head) and target_head==head, 'merged_outcome_recovery_workflow')
    evaluator=action[len(ACTION):];number=int(target_pr);grant_number=int(grant_pr)
    outcome.m.workflow_contract(raw,dict(grant_pr=grant_number,grant_head=grant_head,target_pr=number,
        target_head=head,workflow_path=PATH,branch=row['head_branch']),evaluator)
    guard.historical_runtime(api,evaluator,policy)
    guard.worker.check_runtime(policy)
    receipt=outcome.verify(api,repo,grant_number,grant_head,number,head,evaluator,run,
                           log_reader,historical_attempt=attempt)
    need(receipt['decision'] in ('fail','block') and receipt['issue']==issue,
         'merged_outcome_recovery_receipt')
    jobs=api.pages(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs','jobs')
    writers=[job for job in jobs if job.get('name')=='outcome']
    need(len(writers)==1 and review.integer(writers[0].get('id'))
         and writers[0].get('status')=='completed', 'merged_outcome_recovery_job')
    records=review.r.records(log_reader(repo,writers[0]['id']),guard.MARKER)
    need(len(records)==1 and isinstance(records[0],dict) and isinstance(records[0].get('payload'),dict),
         'merged_outcome_recovery_intent')
    intent=records[0];p=intent['payload'];old_source=p.get('policy_source')
    need(review.sha(old_source), 'merged_outcome_recovery_policy')
    raw_policy=review.blob(api,repo,'.github/issue-ci-required-gates.json',old_source)
    config=review.ci.strict_json_loads(raw_policy)
    old_guard=config.get('issue_guard') if isinstance(config,dict) else None
    need(isinstance(old_guard,dict) and 'policy_source' not in old_guard, 'merged_outcome_recovery_policy')
    old_policy=guard.worker.config(dict(old_guard,policy_source=old_source))
    need(all(old_policy[key]==policy[key] for key in ('genesis','ruleset_id','runtime')),
         'merged_outcome_recovery_policy')
    p=guard.validate(intent,old_policy)
    need(p['repo']==repo and p['pr']==number and p['head']==head and p['issue']==issue
         and p['run']==run and p['attempt']==attempt and p['owner']==owner,
         'merged_outcome_recovery_intent')
    close.require_current_policy(api,repo,policy)
    close.guard_identity(api,repo,policy,intent)
    # Re-read the review after verifying the guard. Both are evidence, not a
    # permission to update an issue or release this guard.
    need(outcome.verify(api,repo,grant_number,grant_head,number,head,evaluator,run,
                        log_reader,historical_attempt=attempt)==receipt,
         'merged_outcome_recovery_changed')
    close.guard_identity(api,repo,policy,intent)
    return dict(pr=number,head=head,issue=issue,evaluator=evaluator,intent=intent,receipt=receipt,
                grant_pr=grant_number,grant_head=grant_head,outcome_job=writers[0]['id'])


def verify_trace_source(api,evaluator):
    """Use trace semantics only when the historical action bytes match exactly."""
    need(review.sha(evaluator),'merged_outcome_trace_source')
    root=Path(__file__).resolve().parents[5]
    paths=('plugins/faberline/skills/issue-ci/scripts/policy_adoption_merged_outcome.py',
           'plugins/faberline/skills/issue-ci/scripts/todo_mutation_trace.py',
           '.github/actions/policy-adoption-merged-grant-outcome/action.yml')
    for path in paths:
        current=(root/path).read_bytes()
        historical=review.blob(api,'faberline/aw',path,evaluator).encode('utf-8')
        need(current==historical,'merged_outcome_trace_source')


def trace_state(api,repo,evidence,log_reader):
    """Observe matching request responses, without authorizing a replay."""
    verify_trace_source(api,evidence['evaluator'])
    trace=sibling('todo_mutation_trace')
    intent=evidence['intent'];sha=intent['sha256']
    log=log_reader(repo,evidence['outcome_job'])
    owned=review.r.records(log,outcome.OWNED_MARKER)
    if not owned:
        return 'unknown'
    need(len(owned)==1 and isinstance(owned[0],dict) and set(owned[0])=={'version','intent_sha256'}
         and type(owned[0]['version']) is int and owned[0]['version']==1
         and owned[0]['intent_sha256']==sha, 'merged_outcome_owned_record')
    records=review.r.records(log,trace.MARKER)
    complete=trace.complete(records,sha)
    seals=review.r.records(log,trace.SEAL_MARKER)
    if not seals:
        return 'unknown'
    need(complete and len(seals)==1 and isinstance(seals[0],dict)
         and set(seals[0])=={'version','intent_sha256','operations'}
         and type(seals[0]['version']) is int and seals[0]['version']==1
         and seals[0]['intent_sha256']==sha and type(seals[0]['operations']) is int
         and seals[0]['operations']==len(records)//2,
         'merged_outcome_trace_seal')
    return 'completed'


def inspect(api,repo,policy,run,attempt,issue,head,owner,log_reader):
    """Observe a durable todo journal; never replay writes or release a guard."""
    evidence=authenticate(api,repo,policy,run,attempt,issue,head,owner,log_reader)
    receipt=evidence['receipt'];pr=evidence['pr']
    normalized=dict(payload=dict(context=dict(issue_sha256=receipt['issue_sha256']),
                                 result=dict(decision=receipt['decision'])),sha256=receipt['sha256'])
    digest=review.r.digest([repo,pr,head,issue,normalized])
    prefix=f'<!-- claude-return-todo:{digest}:'
    def body(phase):
        return (f'{prefix}{phase} -->\nClaude: todo for PR #{pr} head `{head}`.\n'
                f"Receipt: {receipt['sha256']}\nhttps://github.com/{repo}/actions/runs/{run}")
    def journal():
        comments=api.pages(f'repos/{repo}/issues/{issue}/comments')
        need(isinstance(comments,list) and all(isinstance(row,dict) for row in comments),
             'merged_outcome_journal_unknown')
        matches=[row for row in comments if row.get('user',{}).get('login')=='github-actions[bot]'
                 and isinstance(row.get('body'),str) and row['body'].startswith(prefix)]
        need(len(matches)<=1, 'merged_outcome_journal_ambiguous')
        if not matches:
            return None
        item=matches[0]
        need(review.integer(item.get('id')) and item['user'].get('id')==41898282
             and item['user'].get('type')=='Bot', 'merged_outcome_journal_unknown')
        phases=[phase for phase in ('pending','ready','applied') if item['body']==body(phase)]
        need(len(phases)==1, 'merged_outcome_journal_unknown')
        return (item['id'],phases[0],item['body'])
    first=journal()
    phase='absent' if first is None else first[1]
    if phase!='applied':
        return dict(pr=pr,phase=phase,trace=trace_state(api,repo,evidence,log_reader),
                    state='held',close_authorized=False)
    need(trace_state(api,repo,evidence,log_reader)=='completed', 'merged_outcome_trace_unknown')
    def target():
        pull=api.api(f'repos/{repo}/pulls/{pr}')
        review.pull_identity(pull,repo,pr,head)
        need(review.linked_issue(pull)==issue, 'merged_outcome_issue_changed')
        value=api.api(f'repos/{repo}/issues/{issue}')
        need(isinstance(value,dict) and value.get('number')==issue and value.get('state')=='open'
             and 'pull_request' not in value and all(k in value for k in ('title','body'))
             and review.r.digest({k:value[k] for k in ('number','title','body')})==receipt['issue_sha256']
             and isinstance(value.get('labels'),list), 'merged_outcome_issue_changed')
        labels=value['labels']
        need(all(isinstance(label,dict) and isinstance(label.get('name'),str) for label in labels),
             'merged_outcome_issue_changed')
        names={label['name'] for label in labels}
        need(names & {'todo','doing','done'}=={'todo'} and not names & {'blocked','agy:blocked'},
             'merged_outcome_issue_changed')
        provider=outcome.a.native.provider_pull(api,repo,pr)
        need(provider.get('state')=='OPEN' and provider.get('headRefOid')==head
             and provider.get('autoMergeRequest') is None, 'merged_outcome_request_unknown')
        return dict(issue=value,provider=provider)
    before=target()
    need(journal()==first and authenticate(api,repo,policy,run,attempt,issue,head,owner,log_reader)==evidence
         and trace_state(api,repo,evidence,log_reader)=='completed' and target()==before,
         'merged_outcome_recovery_changed')
    return dict(pr=pr,phase='applied',state='applied_observed',close_authorized=False)


def release_applied(api,repo,policy,run,attempt,issue,head,owner,log_reader):
    """Release only a fully observed todo journal and the same blocked guard."""
    document=review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    need(isinstance(document,dict) and document.get('source_commit')==policy['policy_source'],
         'merged_outcome_recovery_policy')
    sibling('recovery_entry').verify_rollout(api,repo,document)
    observed=inspect(api,repo,policy,run,attempt,issue,head,owner,log_reader)
    need(observed.get('state')=='applied_observed' and observed.get('phase')=='applied',
         'merged_outcome_not_applied')
    evidence=authenticate(api,repo,policy,run,attempt,issue,head,owner,log_reader)
    need(evidence['pr']==observed['pr']
         and inspect(api,repo,policy,run,attempt,issue,head,owner,log_reader)==observed,
         'merged_outcome_recovery_changed')
    # Settlement checks the same held owner, current rules, a blocking status,
    # an open PR and absence of any pending merge request before release.
    admission=sibling('guarded_admission')
    result=admission.settle(api,repo,policy,evidence['intent'])
    need(result.get('state')=='released', 'merged_outcome_guard_retained')
    return dict(state='todo_guard_released',pr=evidence['pr'],issue=issue,head=head)
