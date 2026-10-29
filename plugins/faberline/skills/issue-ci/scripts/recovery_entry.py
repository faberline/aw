"""Draft authenticated entry for issue recovery; not deployed to any repo.

Recovery must not be enabled until all actors obey the shared guard and the
remaining writer crash/retry and policy-adoption requirements are complete.
"""
import importlib.util
import os
from pathlib import Path
import re


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_recovery_entry_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


native=sibling('native_merge')
runner=sibling('recovery_runner')
review=native.review
need=review.need
DISPATCH='aw-verified-merge-recover'
WRITER_NAME='Issue CI and Claude outcome'


def policy(document):
    """Require the existing complete qualification and an exact recovery policy."""
    native.policy(document)
    config=document['config'];value=config.get('issue_recovery')
    need(review.exact(value,'version enabled cron producer') and type(value['version']) is int
         and value['version']==1 and value['enabled'] is True
         and value['cron']=='13,43 * * * *','recovery_policy')
    spec=value['producer']
    need(review.exact(spec,'path id definition_sha job') and spec['path']=='.github/workflows/issue-recovery.yml'
         and review.integer(spec['id']) and review.sha(spec['definition_sha']) and spec['job']=='recovery',
         'recovery_policy')
    guard=config.get('issue_guard')
    need(isinstance(guard,dict) and 'policy_source' not in guard,'recovery_policy')
    runner.worker.config(dict(guard,policy_source=document['source_commit']))
    return value


def workflow_contract(api,repo,document,evaluator):
    """Bind a single pinned writer, restricted events and the lifecycle mutex."""
    value=policy(document);spec=value['producer']
    need(review.sha(evaluator),'recovery_action_identity')
    raw=review.blob(api,repo,spec['path'],document['source_commit'],spec['definition_sha'])
    review.r.literal_uses(raw)
    import yaml
    workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    branch=writer_branch(api,repo,document)
    need(isinstance(workflow,dict) and set(workflow)=={'name','on','permissions','concurrency','jobs'}
         and workflow['name']=='Verified issue recovery' and workflow['permissions']=={}
         and workflow['concurrency']=={'group':'issue-ci-handoff','cancel-in-progress':'false'}
         and workflow['on']=={'schedule':[{'cron':value['cron']}],
                              'repository_dispatch':{'types':[DISPATCH]},
                              'workflow_run':{'workflows':[WRITER_NAME],
                                              'types':['completed'],'branches':[branch]}}
         and set(workflow['jobs'])=={'recovery'},'recovery_workflow_contract')
    job=workflow['jobs']['recovery']
    need(job=={'runs-on':'ubuntu-latest','timeout-minutes':'30',
               'permissions':{'actions':'read','contents':'write','issues':'write',
                              'pull-requests':'read','statuses':'read'},
               'steps':[{'uses':'faberline/aw/.github/actions/verified-issue-recovery@'+evaluator}]},
         'recovery_workflow_contract')
    return spec


def writer_branch(api,repo,document):
    """Bind the wake source name to the pinned default-branch writer."""
    spec=native.policy(document)['producer']
    raw=review.blob(api,repo,spec['path'],document['source_commit'],spec['definition_sha'])
    review.r.literal_uses(raw)
    import yaml
    writer=yaml.load(raw,Loader=yaml.BaseLoader)
    meta=api.api(f'repos/{repo}')
    need(isinstance(writer,dict) and writer.get('name')==WRITER_NAME
         and meta.get('full_name')==repo and review.text(meta.get('default_branch'),255),
         'recovery_writer_identity')
    return meta['default_branch']


def validate_wake(api,repo,document,evaluator,run,event,payload):
    """Authenticate the current default-branch workflow, never event inputs."""
    value=policy(document);spec=workflow_contract(api,repo,document,evaluator)
    need(event in ('schedule','repository_dispatch','workflow_run') and review.integer(run) and isinstance(payload,dict),
         'recovery_wake_identity')
    if event=='schedule':
        need(payload.get('schedule')==value['cron'],'recovery_wake_identity')
    elif event=='repository_dispatch':
        need(payload.get('action')==DISPATCH and payload.get('repository',{}).get('full_name')==repo,
             'recovery_wake_identity')
    else:
        source=payload.get('workflow_run');expected=native.policy(document)['producer']
        need(payload.get('action')=='completed' and payload.get('repository',{}).get('full_name')==repo
             and isinstance(source,dict) and review.integer(source.get('id'))
             and review.integer(source.get('run_attempt')),'recovery_wake_identity')
        actual=api.api(f"repos/{repo}/actions/runs/{source['id']}")
        branch=writer_branch(api,repo,document)
        need(isinstance(actual,dict) and actual.get('id')==source['id']
             and actual.get('run_attempt')==source['run_attempt']
             and actual.get('workflow_id')==expected['id'] and actual.get('path')==expected['path']
             and actual.get('status')=='completed' and actual.get('head_branch')==branch
             and actual.get('head_repository',{}).get('full_name')==repo
             and actual.get('event') in ('issues','workflow_run','repository_dispatch'),
             'recovery_wake_identity')
    identity=review.producer_identity(api,repo,document,run,False,spec,events=('schedule','repository_dispatch','workflow_run'))
    need(identity['head']==document['source_commit'],'recovery_wake_identity')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{identity["run_attempt"]}')
    need(row.get('id')==run and row.get('run_attempt')==identity['run_attempt']
         and row.get('status')=='in_progress' and row.get('event')==event,'recovery_wake_identity')
    jobs=api.pages(f'repos/{repo}/actions/runs/{run}/attempts/{identity["run_attempt"]}/jobs','jobs')
    matches=[job for job in jobs if job.get('name')==spec['job'] and job.get('id')==identity['job_id']]
    need(len(matches)==1 and matches[0].get('status')=='in_progress','recovery_wake_identity')
    return identity


def verify_rollout(api,repo,document):
    """No rollout evidence format is implemented yet; always fail closed."""
    raise review.Refused('recovery_rollout_not_verified')


def main():
    need(os.environ.get('GITHUB_ACTIONS')=='true' and not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'),
         'recovery_wake_identity')
    repo=os.environ.get('GITHUB_REPOSITORY','');evaluator=os.environ.get('AW_ACTION_REF','')
    run=os.environ.get('GITHUB_RUN_ID','');event=os.environ.get('GITHUB_EVENT_NAME','')
    need(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) and review.sha(evaluator)
         and re.fullmatch(r'[1-9][0-9]*',run),'recovery_wake_identity')
    api=review.ci
    document=review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    meta=api.api(f'repos/{repo}');branch=meta.get('default_branch')
    need(meta.get('full_name')==repo and os.environ.get('GITHUB_WORKFLOW_SHA')==document['source_commit']
         and os.environ.get('GITHUB_WORKFLOW_REF')==f'{repo}/.github/workflows/issue-recovery.yml@refs/heads/{branch}'
         and os.environ.get('GITHUB_REF')==f'refs/heads/{branch}','recovery_wake_identity')
    payload=api.strict_json_loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    validate_wake(api,repo,document,evaluator,int(run),event,payload)
    # Fail closed until rollout proves every active writer and worker uses the
    # guard, and the writer crash window and durable wake are resolved.
    verify_rollout(api,repo,document)
    results=runner.reconcile(api,repo,review.r.job_log)
    print('AW_ISSUE_RECOVERY_V1 '+review.r.canonical(dict(version=1,repo=repo,run=int(run),results=results)))
    return 2 if any(row.get('state')=='held' for row in results) else 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError,ValueError,KeyError,OSError) as error:
        print('recovery held: '+str(error))
        raise SystemExit(2)
