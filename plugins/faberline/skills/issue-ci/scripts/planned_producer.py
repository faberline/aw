"""Publish independent per-PR execution receipts from current trusted CI."""
import importlib.util
from pathlib import Path
import re
import subprocess


def sibling(name):
    spec=importlib.util.spec_from_file_location('planned_producer_'+name,Path(__file__).with_name(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def validate_source(ci,readiness,repo,document,evaluator):
    config=document['config'];producer=config['review_readiness']['producer'];source=document['source_commit']
    raw=readiness.source_bytes(ci,repo,producer['path'],source,producer['definition_sha'])
    readiness.literal_uses(raw)
    workflow=ci.development_module().yaml_document(raw);names=[]
    for required in config['workflows']:
        path=required['path']
        ci_raw=readiness.source_bytes(ci,repo,path,source,config['qualification']['trusted_workflows'][path]['definition_sha'])
        readiness.literal_uses(ci_raw)
        name=ci.development_module().yaml_document(ci_raw).get('name')
        readiness.need(isinstance(name,str) and bool(name.strip()),'planned_producer_source','static CI workflow name required')
        names.append(name)
    triggers=workflow.get('on');jobs=workflow.get('jobs');matches=[]
    if isinstance(jobs,dict):matches=[job for job in jobs.values() if isinstance(job,dict) and job.get('name')==producer['job']]
    expected={'uses':f'faberline/aw/.github/actions/issue-ci@{evaluator}','with':{'operation':'planned-produce'}}
    readiness.need(isinstance(triggers,dict) and set(triggers)=={'workflow_run','workflow_dispatch'}
                   and triggers['workflow_run']=={'workflows':names,'types':['completed']}
                   and len(names)==len(set(names)) and triggers['workflow_dispatch']=={'inputs':{'pr':{
                       'description':'Pull request number to recheck','required':'true','type':'string'}}}
                   and workflow.get('permissions')=={} and 'concurrency' not in workflow
                   and isinstance(jobs,dict) and len(jobs)==1 and len(matches)==1
                   and set(matches[0])=={'name','runs-on','timeout-minutes','permissions','steps'}
                   and matches[0]['permissions']=={'actions':'read','contents':'read','pull-requests':'read','issues':'read'}
                   and matches[0]['steps']==[expected],
                   'planned_producer_source','dedicated read-only producer workflow required')


def produce(ci,repo,load_document,evaluator,run_id,event_name,event):
    readiness=sibling('readiness');planned=sibling('planned_readiness')
    document=load_document();config=document['config'];readiness.validate_contract(ci,config)
    producer=readiness.producer_identity(ci,repo,config,run_id,allowed_events=planned.PLANNED_EVENTS)
    readiness.need(producer['head']==document['source_commit'] and producer['event']==event_name,
                   'planned_producer_source','producer source or event differs')
    readiness.pin_proof(ci,repo,None,config,producer['head'],evaluator,producer_only=True)
    validate_source(ci,readiness,repo,document,evaluator)
    wake_sha=None;target_pr=None
    if event_name=='workflow_run':
        wake=event.get('workflow_run',{})
        readiness.need(event.get('action')=='completed' and wake.get('status')=='completed'
                       and wake.get('event')=='pull_request' and type(wake.get('id')) is int
                       and type(wake.get('run_attempt')) is int and wake['run_attempt']>0
                       and wake.get('head_repository',{}).get('full_name')==repo
                       and wake.get('path') in {item['path'] for item in config['workflows']},
                       'planned_producer_wake','configured same-repository terminal PR CI required')
        run=ci.api(f'repos/{repo}/actions/runs/{wake["id"]}')
        readiness.need(run.get('id')==wake['id'] and run.get('run_attempt')==wake['run_attempt']
                       and run.get('event')=='pull_request' and run.get('status')=='completed'
                       and run.get('path')==wake['path'] and run.get('head_sha')==wake.get('head_sha')
                       and run.get('head_repository',{}).get('full_name')==repo
                       and isinstance(run.get('head_sha'),str) and readiness.HEX.fullmatch(run['head_sha']),
                       'planned_producer_wake','provider CI wake identity differs')
        wake_sha=run['head_sha']
    else:
        inputs=event.get('inputs')
        readiness.need(event_name=='workflow_dispatch' and isinstance(inputs,dict) and set(inputs)=={'pr'}
                       and isinstance(inputs['pr'],str) and re.fullmatch(r'[1-9][0-9]*',inputs['pr']) is not None,
                       'planned_producer_wake','one numeric PR for reconciliation required')
        target_pr=int(inputs['pr'])
    pulls=ci.pages(f'repos/{repo}/pulls?state=open&sort=created&direction=asc')
    readiness.need(isinstance(pulls,list),'planned_producer_inventory','complete PR list required')
    q=ci.qualification_module();receipts=[];held=[];seen=set();size=0
    for pull in pulls:
        readiness.need(isinstance(pull,dict) and type(pull.get('number')) is int and pull['number']>0
                       and pull['number'] not in seen,'planned_producer_inventory','invalid or duplicate PR')
        number=pull['number'];seen.add(number)
        head=pull.get('head',{});base=pull.get('base',{})
        if ((target_pr is not None and number!=target_pr) or pull.get('state')!='open' or pull.get('draft') is not False
                or (head.get('repo') or {}).get('full_name')!=repo
                or (base.get('repo') or {}).get('full_name')!=repo
                or base.get('sha')!=document['source_commit'] or not isinstance(head.get('sha'),str)
                or not readiness.HEX.fullmatch(head['sha']) or (wake_sha is not None and head['sha']!=wake_sha)
                or q.linked_issue(pull) is None):
            continue
        try:
            issue=ci.api(f'repos/{repo}/issues/{q.linked_issue(pull)}')
            labels=issue.get('labels')
            readiness.need(issue.get('state')=='open' and 'pull_request' not in issue
                           and isinstance(labels,list) and all(isinstance(label,dict)
                           and isinstance(label.get('name'),str) for label in labels),
                           'planned_producer_issue','current issue state is incomplete')
            if {label['name'] for label in labels}&{'todo','doing','done'} not in ({'doing'},{'done'}):
                continue
            receipt=planned.produce(ci,repo,number,head['sha'],load_document,evaluator,run_id)
            readiness.need(receipt['payload']['producer']==producer,'planned_producer_source','producer changed')
            record_size=len((planned.MARKER+readiness.canonical(receipt)+'\n').encode())
            if size+record_size>readiness.MAX_LOG//2:
                held.append(number)
                continue
            receipts.append(receipt);size+=record_size
        except (RuntimeError,ValueError,KeyError,TypeError,OSError,subprocess.CalledProcessError):
            held.append(number)
    readiness.need(load_document()==document and readiness.producer_identity(
        ci,repo,config,run_id,allowed_events=planned.PLANNED_EVENTS)==producer,
        'planned_producer_stale','policy or producer changed before publication')
    # Overflow is explicit per PR; the next targeted dispatch retries it.
    return {'producer':producer,'receipts':receipts,'held':sorted(held),'qualification':'not_evaluated'}


def emit(bundle):
    readiness=sibling('readiness');planned=sibling('planned_readiness')
    for receipt in bundle['receipts']:print(planned.MARKER+readiness.canonical(receipt))
    print('AW_PLANNED_PRODUCER_V1 '+readiness.canonical(dict(producer=bundle['producer'],held=bundle['held'],
          receipt_count=len(bundle['receipts']),qualification='not_evaluated')))
