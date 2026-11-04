"""Wake read-only per-PR evidence producers on the existing planner pass."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


def load(name):
    path=Path(__file__).resolve().parents[2]/'issue-ci/scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location('planner_reconcile_'+name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def run(repo,ci,action,readiness,producer,dispatch):
    document=lambda:action.trusted_policy(ci,repo,'.github/issue-ci-required-gates.json',metadata=True)
    original=document();config=original['config']
    if 'planned_ci_qualification' not in config:
        return []
    ci.validate_config(config);readiness.validate_contract(ci,config)
    sources=config['review_readiness']['sources'].values()
    refs={row['ref'] for row in sources if row['repo']=='faberline/aw'
          and row['path']=='.github/actions/issue-ci/action.yml'}
    readiness.need(len(refs)==1,'planned_reconcile_policy','one AW evaluator required')
    evaluator=refs.pop()
    readiness.pin_proof(ci,repo,None,config,original['source_commit'],evaluator,producer_only=True)
    producer.validate_source(ci,readiness,repo,original,evaluator)
    meta=ci.api(f'repos/{repo}');branch=meta.get('default_branch')
    readiness.need(isinstance(branch,str) and bool(branch),'planned_reconcile_branch','default branch required')
    pulls=ci.pages(f'repos/{repo}/pulls?state=open&sort=created&direction=asc')
    targets=[];seen=set();failed=[];q=ci.qualification_module()
    for pull in pulls:
        number=pull.get('number')
        readiness.need(type(number) is int and number>0 and number not in seen,
                       'planned_reconcile_inventory','invalid PR inventory')
        seen.add(number)
        try:
            head=pull.get('head',{});base=pull.get('base',{});issue=q.linked_issue(pull)
            if (pull.get('state')!='open' or pull.get('draft') is not False or type(issue) is not int
                    or (head.get('repo') or {}).get('full_name')!=repo
                    or (base.get('repo') or {}).get('full_name')!=repo or base.get('sha')!=original['source_commit']):
                continue
            item=ci.api(f'repos/{repo}/issues/{issue}');labels=item.get('labels')
            readiness.need(isinstance(labels,list) and all(isinstance(label,dict) and isinstance(label.get('name'),str)
                           for label in labels),'planned_reconcile_inventory','incomplete issue labels')
            if item.get('state')=='open' and {x['name'] for x in labels}&{'todo','doing','done'} in ({'doing'},{'done'}):
                targets.append(number)
        except (RuntimeError,ValueError,KeyError,TypeError,OSError,subprocess.CalledProcessError):
            failed.append(number)
    workflow=config['review_readiness']['producer']['path'].split('/')[-1]
    for number in targets:
        readiness.need(document()==original and ci.api(f'repos/{repo}')['default_branch']==branch,
                       'planned_reconcile_stale','policy moved before dispatch')
        try:
            dispatch(f'repos/{repo}/actions/workflows/{quote(workflow,safe="")}/dispatches',
                     {'ref':branch,'inputs':{'pr':str(number)}})
        except (OSError,subprocess.CalledProcessError):
            failed.append(number)
    if failed:
        raise RuntimeError('planned evidence reconciliation held PRs: '+', '.join(map(str,failed)))
    return targets


def execute(repo):
    action=load('action');ci=action.controller();readiness=load('readiness');producer=load('planned_producer')
    def dispatch(path,payload):
        subprocess.run(['gh','api','--method','POST',path,'--input','-'],
                       input=json.dumps(payload),text=True,check=True,stdout=subprocess.DEVNULL)
    result=run(repo,ci,action,readiness,producer,dispatch)
    print('planned evidence reconciliation requested for PRs: '+(', '.join(map(str,result)) or 'none'))
