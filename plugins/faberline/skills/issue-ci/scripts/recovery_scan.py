"""Draft read-only recovery scan; discovery results never authorize writes."""
import importlib.util
from pathlib import Path
import re
from urllib.parse import quote


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_recovery_scan_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


discovery=sibling('guard_discovery')
guard=sibling('merge_guard')
observer=sibling('post_merge_observation')
closer=sibling('guarded_close')
prepared=sibling('prepared_status')
direct=sibling('direct_merge')
review=guard.review
need=guard.need


def adoption_module():
    return sibling('adoption_guard')


def inspect_prepared(api,repo,document,current_policy,evaluator,run,attempt,intent,log_reader):
    """Observe a pre-status record without authorizing a close."""
    p=intent['payload'];pr=p['pr'];head=p['head']
    value=prepared.find_native_prepared(api,repo,document,evaluator,run,attempt,pr,head,log_reader)
    if value is None:
        return None
    evidence=value['payload']['checkpoint'];checkpoint=evidence['payload']['checkpoint']
    prepared.per_pr.guard_binding(p,evidence['payload'])
    need(checkpoint['issue']==p['issue'] and checkpoint['repo']==repo
         and checkpoint['pr']==pr and checkpoint['head']==head,'recovery_checkpoint_mismatch')
    old=guard.worker.config(dict(document['config']['issue_guard'],policy_source=document['source_commit']))
    guard.worker.config(current_policy);guard.worker.check_runtime(current_policy)
    need(all(old[k]==current_policy[k] for k in ('genesis','ruleset_id','runtime')),
         'merge_guard_policy_changed')
    closer.guard_identity(api,repo,current_policy,intent)
    started=observer.source_started_at(api,evidence,require_success=False)
    result=observer.recovery_plan(api,checkpoint,started)
    closer.guard_identity(api,repo,current_policy,intent)
    if result['state']=='observed_closed':
        # The issue is already closed. This is only a candidate for releasing
        # the same guard; it is never authority to close an open issue.
        return dict(state='closed_prepared_recovery',status='not_required',close_authorized=False)
    if result['state']=='waiting':
        refusal=direct.find_refusal(api,repo,document,evaluator,run,attempt,intent,log_reader,guard,review)
        if refusal is not None:
            # A trusted, completed writer received a definitive provider
            # refusal. Reauthenticate ownership and block before releasing.
            return dict(state='admission_recovery',status='definitive_refusal',close_authorized=False)
    statuses=api.pages(f'repos/{repo}/commits/{head}/statuses')
    status=prepared.observe(value,statuses)
    state='prepared_revalidation' if result['state']=='guard_required' else result['state']
    return dict(state=state,status=status['state'],close_authorized=False)


def historical_document(api,repo,run,attempt):
    """Read immutable policy for the exact run, not the current PR branch."""
    need(isinstance(repo,str) and guard.provider.ledger.REPO.fullmatch(repo)
         and type(run) is int and run>0 and type(attempt) is int and attempt>0,'recovery_source')
    meta=api.api(f'repos/{repo}');branch=meta.get('default_branch')
    need(meta.get('full_name')==repo and review.text(branch,255),'recovery_source')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    source=row.get('head_sha') if isinstance(row,dict) else None
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('head_branch')==branch
         and row.get('head_repository',{}).get('full_name')==repo
         and row.get('event') in ('issues','workflow_run','repository_dispatch')
         and review.sha(source),'recovery_source')
    comparison=api.api(f'repos/{repo}/compare/{source}...{quote(branch,safe="")}')
    need(comparison.get('status') in ('identical','ahead')
         and comparison.get('base_commit',{}).get('sha')==source,'recovery_source')
    raw=review.blob(api,repo,'.github/issue-ci-required-gates.json',source)
    config=review.ci.strict_json_loads(raw)
    need(isinstance(config,dict) and review.sha(config.get('aw_revision')),'recovery_source')
    return dict(config=config,source_commit=source),config['aw_revision']


def source_event(api,repo,run,attempt):
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('event') in
             ('issues','workflow_run','repository_dispatch','push'),'recovery_source')
    return row['event']


def source_path(api,repo,run,attempt):
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('event')=='push'
         and isinstance(row.get('path'),str) and row['path'].startswith('.github/workflows/'),
         'recovery_source')
    return row['path']


def scan(api,repo,current_policy,log_reader):
    """Inspect all guards, retaining failures and never mutating an issue."""
    items=discovery.inspect(api,repo,current_policy);results=[]
    for item in items:
        if item['state']!='merge_held':
            results.append(item);continue
        row=dict(issue=item['issue'],state='held')
        try:
            run,attempt=item['run'],item['attempt']
            if source_event(api,repo,run,attempt)=='push':
                if source_path(api,repo,run,attempt)=='.github/workflows/review-merged-product-grant.yml':
                    outcome=sibling('policy_adoption_merged_recovery')
                    evidence=outcome.inspect(api,repo,current_policy,run,attempt,item['issue'],
                                             item['head'],item['owner'],log_reader)
                    applied=evidence['state']=='applied_observed' and evidence['phase']=='applied'
                    row.update(state='merged_outcome_applied' if applied else 'held',
                               reason='merged_outcome_applied_revalidation' if applied else 'merged_outcome_recovery_not_implemented',
                               kind='merged_outcome',pr=evidence['pr'],head=item['head'],
                               owner=item['owner'],run=run,attempt=attempt,phase=evidence['phase'],
                               close_authorized=False)
                    results.append(row);continue
                adoption=adoption_module()
                source=adoption.discover_source(api,repo,run,attempt,item['issue'],item['head'],item['owner'],log_reader)
                try:
                    verified=adoption.authenticate_checkpoint(api,source['document'],repo,source['evaluator'],run,attempt,
                                                              item['issue'],item['head'],item['owner'],log_reader)
                except Exception as error:
                    if str(error)!='adoption_checkpoint_missing':raise
                    verified=adoption.find_prepared(api,source['document'],repo,source['evaluator'],run,attempt,
                                                    item['issue'],item['head'],item['owner'],log_reader)
                    if verified is not None:
                        evidence=verified['evidence']
                        started=observer.source_started_at(api,evidence,require_success=False)
                        observed=observer.recovery_plan(api,evidence['payload']['checkpoint'],started)
                        if observed['state']=='observed_closed':
                            row.update(state='closed_prepared_recovery',kind='adoption',pr=source['pr'],head=item['head'],
                                       owner=item['owner'],run=run,attempt=attempt,close_authorized=False)
                            results.append(row);continue
                        if observed['state']=='guard_required':
                            statuses=api.pages(f"repos/{repo}/commits/{item['head']}/statuses")
                            status=prepared.observe(verified['prepared'],statuses)
                            row.update(state='adoption_prepared_revalidation',kind='adoption',pr=source['pr'],
                                       head=item['head'],owner=item['owner'],run=run,attempt=attempt,
                                       status=status['state'],close_authorized=False)
                            results.append(row);continue
                        if observed['state']=='waiting':
                            refusal=adoption.find_direct_refusal(api,source['document'],repo,source['evaluator'],
                                run,attempt,item['issue'],item['head'],item['owner'],log_reader)
                            row.update(state='admission_recovery' if refusal is not None else 'waiting',
                                       kind='adoption',pr=source['pr'],head=item['head'],owner=item['owner'],
                                       run=run,attempt=attempt,close_authorized=False)
                            results.append(row);continue
                    if verified is None and adoption.direct_enabled_at(api,source['evaluator']):
                        row.update(state='held',reason='direct_attempt_unknown',close_authorized=False)
                        results.append(row);continue
                    row.update(state='admission_recovery',kind='adoption',pr=source['pr'],head=item['head'],
                               owner=item['owner'],run=run,attempt=attempt,close_authorized=False)
                    results.append(row);continue
                evidence=verified['evidence']
                started=observer.source_started_at(api,evidence,require_success=False)
                observed=observer.recovery_plan(api,evidence['payload']['checkpoint'],started)
                state=observed['state']
                if state=='waiting':
                    refusal=adoption.find_direct_refusal(api,source['document'],repo,source['evaluator'],
                        run,attempt,item['issue'],item['head'],item['owner'],log_reader)
                    if refusal is not None: state='admission_recovery'
                row.update(state=state,kind='adoption',pr=source['pr'],head=item['head'],owner=item['owner'],
                           run=run,attempt=attempt,close_authorized=False)
                results.append(row);continue
            document,evaluator=historical_document(api,repo,run,attempt)
            intent=guard.identify_native_owner(api,repo,document,evaluator,run,attempt,item['issue'],
                                               item['head'],item['owner'],log_reader)
            pr=intent['payload']['pr']
            try:
                evidence=closer.authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,item['head'],log_reader,
                                                        intent=intent)
            except Exception as error:
                if str(error) not in ('merge_guard_source_not_success','merge_close_record_missing_or_duplicate'):
                    raise
                planned=inspect_prepared(api,repo,document,current_policy,evaluator,run,attempt,intent,log_reader)
                if planned is None:
                    if direct.native_enabled_at(api,evaluator,review):
                        row.update(state='held',reason='direct_attempt_unknown',close_authorized=False)
                    else:
                        row.update(state='admission_recovery',pr=pr,head=item['head'],owner=item['owner'],
                                   run=run,attempt=attempt,close_authorized=False)
                else:
                    row.update(state=planned['state'],status=planned['status'],pr=pr,head=item['head'],owner=item['owner'],
                               run=run,attempt=attempt,close_authorized=False)
                results.append(row)
                continue
            need(evidence['payload']['source_attempt']==attempt
                 and evidence['payload']['checkpoint']['issue']==item['issue'], 'recovery_checkpoint_mismatch')
            started=observer.source_started_at(api,evidence,require_success=False)
            observed=observer.recovery_plan(api,evidence['payload']['checkpoint'],started)
            state=observed['state']
            if state=='waiting' and direct.find_refusal(api,repo,document,evaluator,run,attempt,intent,
                                                        log_reader,guard,review) is not None:
                state='admission_recovery'
                row['owner']=item['owner']
            row.update(state=state,pr=pr,head=item['head'],run=run,attempt=attempt,close_authorized=False)
        except Exception as error:
            reason=str(error)
            if not re.fullmatch(r'[a-z0-9_]+',reason):reason='recovery_evidence_unknown'
            row['reason']=reason
            row['close_authorized']=False
        results.append(row)
    return results
