"""Qualify one planned issue from its authenticated current CI receipt.

This module does not mutate GitHub. Protected, global and other applicability
holds remain in force. The CI writer owns the final lifecycle handoff.
"""
import importlib.util
from pathlib import Path
import subprocess
from urllib.parse import quote


def sibling(name):
    spec=importlib.util.spec_from_file_location('planned_handoff_'+name,Path(__file__).with_name(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def receipt_for(ci,repo,number,head,load_document,evaluator,producer_run_id):
    """Read one exact receipt from the authenticated completed producer job."""
    readiness=sibling('readiness');planned=sibling('planned_readiness');producer_module=sibling('planned_producer')
    document=load_document();config=document['config']
    readiness.validate_contract(ci,config)
    producer=readiness.producer_identity(ci,repo,config,producer_run_id,completed=True,allowed_events=planned.PLANNED_EVENTS)
    readiness.need(producer['head']==document['source_commit'],
                   'planned_untrusted_producer','producer is not the current policy source')
    readiness.pin_proof(ci,repo,None,config,producer['head'],evaluator,producer_only=True)
    producer_module.validate_source(ci,readiness,repo,document,evaluator)
    records=readiness.records(readiness.job_log(repo,producer['job_id']),planned.MARKER)
    matches=[row for row in records if isinstance(row,dict) and isinstance(row.get('payload'),dict)
             and row['payload'].get('repo')==repo and row['payload'].get('pr')==number
             and row['payload'].get('head')==head]
    readiness.need(len(matches)==1,'planned_receipt_missing','one exact current PR receipt required')
    verified=planned.verify(ci,matches[0],repo,number,head,load_document,evaluator)
    readiness.need(verified['payload']['producer']==producer and load_document()==document,
                   'planned_receipt_stale','producer or policy moved during verification')
    return verified


def discover(ci,repo,number,head,load_document,evaluator):
    """Find a current receipt without making older runs a prerequisite."""
    readiness=sibling('readiness')
    document=load_document(); config=document['config']
    readiness.validate_contract(ci,config)
    path=config['review_readiness']['producer']['path'].split('/')[-1]
    # Do not use GitHub's filtered workflow-run search: it caps results at 1,000.
    runs=ci.pages(f'repos/{repo}/actions/workflows/{quote(path,safe="")}/runs','workflow_runs')
    readiness.need(isinstance(runs,list),'planned_inventory','complete producer inventory required')
    seen=set()
    for run in runs:
        readiness.need(isinstance(run,dict) and type(run.get('id')) is int and run['id']>0
                       and run['id'] not in seen,'planned_inventory','invalid producer inventory')
        seen.add(run['id'])
    runs.sort(key=lambda row:row['id'],reverse=True)
    for run in runs:
        if (run.get('status')!='completed' or run.get('conclusion')!='success' or run.get('event') not in ('workflow_run','workflow_dispatch')
                or run.get('head_sha')!=document['source_commit']):
            continue
        try:
            result=receipt_for(ci,repo,number,head,load_document,evaluator,run['id'])
            readiness.need(load_document()==document,'planned_receipt_stale','policy moved during discovery')
            return result
        except (RuntimeError,ValueError,KeyError,TypeError,OSError,subprocess.CalledProcessError):
            continue
    raise RuntimeError('no current authenticated planned execution receipt')


def qualify(ci,repo,number,head,load_document,evaluator,receipt):
    readiness=sibling('readiness');planned=sibling('planned_readiness')
    document=load_document();config=document['config']
    readiness.need(config.get('lifecycle_mode')=='full' and config.get('completion_owner')=='local'
                   and not config.get('qualification_blockers'),
                   'planned_policy_hold','full local CI ownership and no global blockers required')
    verified=planned.verify(ci,receipt,repo,number,head,load_document,evaluator)
    payload=verified['payload'];selection=payload['selection']['execution']
    readiness.need(selection['unresolved']==['linked issue has no reviewed contract'],
                   'planned_protected_hold','protected or other unresolved holds remain')
    pull=ci.api(f'repos/{repo}/pulls/{number}')
    scoped=ci.qualify_config(repo,pull,config);context=scoped['_qualification_context']
    readiness.need(context['head']==head and context['base']==payload['base']
                   and context['issues']==['linked issue has no reviewed contract']
                   and not context['untrusted_workflows'] and load_document()==document,
                   'planned_applicability_hold','current scope contains unresolved holds')
    plan=selection['plan']
    readiness.need(set(plan['selected'])==set(config['qualification']['checks']),
                   'planned_incomplete_roster','complete reviewed test roster required')
    readiness.need(planned.verify(ci,receipt,repo,number,head,load_document,evaluator)==verified
                   and load_document()==document,
                   'planned_freshness_hold','evidence or policy changed during qualification')
    wanted={}
    for check in plan['checks'].values():wanted.setdefault(check['workflow'],set()).add(check['job'])
    scoped['workflows']=[dict(path=path,jobs=sorted(jobs)) for path,jobs in sorted(wanted.items())]
    context['selected']=plan['selected'];context['issues']=[]
    context['planned_receipt_sha256']=verified['sha256']
    context['planned_producer_run_id']=payload['producer']['run_id']
    context['planned_mapping']=payload['selection']['publication']
    return scoped
