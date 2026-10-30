"""Authenticate an operator-audited guard activation; never mutate providers.

A local inventory or an agent's claim is not authorization. The operator must
publish the complete fenced-launcher audit as a data-only AW PR, after checking
all registered launchers and in-flight work. This module rechecks its immutable
source, declared inventory, runtime, drain, provider, and control evidence.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from datetime import datetime, timezone


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_rollout_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


native=sibling('native_merge')
review=native.review
need=review.need
AW='faberline/aw'
DATA='.github/issue-guard-rollouts.json'
MANIFEST='.github/issue-guard-actors.json'
CONTROLS='.github/workflows/native-auto-merge-controls.yml'
CASES='plugins/faberline/skills/issue-ci/scripts/rollout-conformance.json'
GROUPS={'merge','close_retry','ownership','failure_handoff','provider_unknown','source_identity'}
ENTRY='plugins/faberline/skills/issue-work/scripts/work.py'
REPOS={f'faberline/{name}' for name in ('core','lumen','keep','tape','defer','relay')}


def digest(value):
    return review.r.digest(value)


def sha256(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def epoch(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{32}',value) is not None


def timestamp(value):
    need(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z',value),
         'rollout_audit_time')
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
    except ValueError:
        raise review.Refused('rollout_audit_time') from None


def on_main(api,repo,source):
    meta=api.api(f'repos/{repo}')
    need(meta.get('full_name')==repo and meta.get('default_branch')=='main','rollout_default_changed')
    compare=api.api(f'repos/{repo}/compare/{source}...main')
    need(compare.get('status') in ('identical','ahead') and compare.get('base_commit',{}).get('sha')==source,
         'rollout_source_not_on_main')


def registry(raw):
    value=review.ci.strict_json_loads(raw)
    need(review.exact(value,'version rollouts') and type(value['version']) is int and value['version']==1
         and isinstance(value['rollouts'],list) and len(value['rollouts'])<=64,'rollout_registry')
    seen=set()
    for item in value['rollouts']:
        need(isinstance(item,dict) and item.get('repo') in REPOS and epoch(item.get('epoch'))
             and item.get('state') in ('draft','authorized','revoked'),'rollout_registry')
        key=(item['repo'],item['epoch'])
        need(key not in seen,'rollout_duplicate');seen.add(key)
    return value['rollouts']


def selected(raw,repo,nonce):
    matches=[row for row in registry(raw) if(row['repo'],row['epoch'])==(repo,nonce)]
    need(len(matches)==1 and matches[0]['state']=='authorized','rollout_authorization_missing')
    return matches[0]


def control_run(api,reference,run_id,attempt,log_reader=None):
    """Bind the complete control job to the exact reviewed source tree."""
    need(review.sha(reference) and review.integer(run_id) and review.integer(attempt),'rollout_control_identity')
    run=api.api(f'repos/{AW}/actions/runs/{run_id}/attempts/{attempt}')
    latest=api.api(f'repos/{AW}/actions/runs/{run_id}')
    stable=('id','run_attempt','workflow_id','path','event','head_sha','repository',
            'head_repository','status','conclusion')
    need(latest.get('id')==run_id and latest.get('run_attempt')==attempt
         and all(latest.get(k)==run.get(k)for k in stable),'rollout_controls_superseded')
    need(run.get('id')==run_id and run.get('run_attempt')==attempt and run.get('path')==CONTROLS
         and run.get('event')=='pull_request' and run.get('status')=='completed' and run.get('conclusion')=='success'
         and run.get('repository',{}).get('full_name')==AW and run.get('head_repository',{}).get('full_name')==AW
         and review.sha(run.get('head_sha')),'rollout_control_identity')
    identity=api.api(f'repos/{AW}/actions/workflows/native-auto-merge-controls.yml')
    need(review.integer(identity.get('id')) and run.get('workflow_id')==identity['id']
         and identity.get('path')==CONTROLS,'rollout_control_identity')
    expected=api.api(f'repos/{AW}/git/commits/{reference}')
    need(review.sha(expected.get('tree',{}).get('sha')),'rollout_control_source_changed')
    need(review.blob(api,AW,CONTROLS,reference)==review.blob(api,AW,CONTROLS,run['head_sha']),
         'rollout_control_source_changed')
    jobs=api.pages(f'repos/{AW}/actions/runs/{run_id}/attempts/{attempt}/jobs','jobs')
    need(len(jobs)==1 and jobs[0].get('name')=='complete isolated native merge controls'
         and review.integer(jobs[0].get('id')) and jobs[0].get('status')=='completed'
         and jobs[0].get('conclusion')=='success','rollout_controls_not_green')
    log=(log_reader or review.r.job_log)(AW,jobs[0]['id'])
    receipts=review.r.records(log,'AW_CONTROL_SOURCE_V1 ')
    need(len(receipts)==1 and review.exact(receipts[0],'version repo commit tree')
         and type(receipts[0]['version']) is int and receipts[0]['version']==1 and receipts[0]['repo']==AW
         and review.sha(receipts[0]['commit']) and receipts[0]['tree']==expected['tree']['sha'],
         'rollout_control_source_changed')
    return run,jobs[0]


def authorization(api,pointer,record):
    """A reviewed data-only operator grant supplies authority, not a digest."""
    source=pointer['source'];auth=record.get('authorization')
    need(review.exact(auth,'pr') and review.integer(auth['pr']),
         'rollout_authorization_identity')
    pull=api.api(f"repos/{AW}/pulls/{auth['pr']}")
    need(pull.get('number')==auth['pr'] and pull.get('state')=='closed' and pull.get('merged') is True
         and review.sha(pull.get('merge_commit_sha')) and review.sha(pull.get('head',{}).get('sha'))
         and pull.get('base',{}).get('ref')=='main' and pull.get('head',{}).get('repo',{}).get('full_name')==AW
         and pull.get('base',{}).get('repo',{}).get('full_name')==AW,
         'rollout_authorization_not_merged')
    files=api.pages(f"repos/{AW}/pulls/{auth['pr']}/files")
    need(pull.get('changed_files')==1 and len(files)==1 and files[0].get('filename')==DATA
         and files[0].get('status')=='modified' and review.sha(files[0].get('sha')),
         'rollout_authorization_not_data_only')
    raw=review.blob(api,AW,DATA,source,files[0]['sha'])
    need(selected(raw,record['repo'],record['epoch'])==record,'rollout_authorization_changed')
    compare=api.api(f"repos/{AW}/compare/{pull['merge_commit_sha']}...{source}")
    need(compare.get('status') in ('identical','ahead')
         and compare.get('base_commit',{}).get('sha')==pull['merge_commit_sha'],'rollout_authorization_not_merged')
    # The data cannot contain its own final run ID: changing it changes the
    # PR head. Discover the latest actual exact-head run after merge instead.
    runs=api.pages(f"repos/{AW}/actions/workflows/native-auto-merge-controls.yml/runs?head_sha={pull['head']['sha']}&event=pull_request",'workflow_runs')
    runs=[r for r in runs if r.get('head_sha')==pull['head']['sha'] and r.get('event')=='pull_request'
          and r.get('path')==CONTROLS and r.get('head_repository',{}).get('full_name')==AW]
    need(runs and all(review.integer(r.get('run_number')) and review.integer(r.get('run_attempt'))
         and review.integer(r.get('id')) for r in runs),'rollout_authorization_controls_missing')
    latest=max(runs,key=lambda r:(r['run_number'],r['run_attempt']))
    run,_=control_run(api,source,latest['id'],latest['run_attempt'])
    need(run['head_sha']==pull['head']['sha'] and run.get('run_number')==latest['run_number'],
         'rollout_authorization_changed')
    return timestamp(run.get('run_started_at'))


def conformance(api,controller,proof,log_reader):
    need(review.exact(proof,'run attempt') and all(review.integer(proof[k])for k in proof),
         'rollout_conformance_identity')
    _,job=control_run(api,controller,proof['run'],proof['attempt'],log_reader)
    groups=review.ci.strict_json_loads(review.blob(api,AW,CASES,controller))
    need(isinstance(groups,dict) and set(groups)==GROUPS,'rollout_conformance_roster')
    wanted=set()
    for names in groups.values():
        need(isinstance(names,list) and names and all(isinstance(n,str) and re.fullmatch(r'[A-Za-z0-9_.]+',n)for n in names)
             and len(names)==len(set(names)),'rollout_conformance_roster')
        wanted.update(names)
    log=log_reader(AW,job['id'])
    # unittest verbosity 2 emits the executed test ID and result. Require the
    # precise passing controls, not an untrusted artifact or a summary count.
    passed=set(re.findall(r'^.*?\btest\w+ \(([A-Za-z0-9_.]+)\) \.\.\. ok\s*$',log,re.M))
    need(wanted<=passed,'rollout_conformance_missing')
    return dict(run=proof['run'],attempt=proof['attempt'],job=job['id'],controls_sha256=digest(sorted(wanted)))


def policy_digest(repo,config,manifest_blob):
    return digest(dict(repo=repo,guard=config['issue_guard'],recovery=config['issue_recovery'],
                       writer=config['native_auto_merge']['producer'],controller=config['aw_revision'],
                       actor_manifest=manifest_blob))


def actors(api,repo,document,record,drained_at,authorized_at):
    raw=review.blob(api,repo,MANIFEST,document['source_commit'],record['actor_manifest'])
    manifest=review.ci.strict_json_loads(raw)
    need(review.exact(manifest,'version actors') and type(manifest['version']) is int and manifest['version']==1
         and isinstance(manifest['actors'],list) and 3<=len(manifest['actors'])<=64,'rollout_actor_manifest')
    ids=set();roles=[]
    for actor in manifest['actors']:
        need(review.exact(actor,'id role') and isinstance(actor['id'],str)
             and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',actor['id']) and actor['role'] in ('worker','writer','recovery','controller'),
             'rollout_actor_manifest')
        need(actor['id'] not in ids,'rollout_actor_duplicate');ids.add(actor['id']);roles.append(actor['role'])
    need(roles.count('writer')==1 and roles.count('recovery')==1 and 'worker'in roles,'rollout_actor_manifest')
    reports=record['actors'];need(isinstance(reports,list) and len(reports)==len(ids),'rollout_actor_missing')
    seen=set();config=document['config'];entry=review.blob(api,AW,ENTRY,config['aw_revision']).encode()
    expected=dict(config['issue_guard']['runtime'],entry=hashlib.sha256(entry).hexdigest())
    for report in reports:
        need(review.exact(report,'id epoch manifest runtime audit_sha256 inflight leases barrier_source observed_at')
             and report['id'] in ids and report['id'] not in seen and report['epoch']==record['epoch']
             and report['manifest']==record['actor_manifest'] and report['runtime']==expected
             and sha256(report['audit_sha256']) and report['inflight']==[] and report['leases']==[]
             and report['barrier_source']==record['drain']['source']
             and drained_at<=timestamp(report['observed_at'])<=authorized_at,
             'rollout_actor_not_fenced')
        seen.add(report['id'])
    return manifest


def drain(api,repo,record):
    proof=record['drain']
    need(review.exact(proof,'source run attempt record_sha256') and review.sha(proof['source'])
         and all(review.integer(proof[k])for k in ('run','attempt')) and sha256(proof['record_sha256']),
         'rollout_drain_identity')
    on_main(api,repo,proof['source'])
    raw=review.blob(api,repo,'.github/issue-ci-required-gates.json',proof['source'])
    old=dict(source_commit=proof['source'],config=review.ci.strict_json_loads(raw))
    marker=native.drain_marker(old['config'])
    need(marker is not None and marker['nonce']==record['epoch'],'rollout_drain_epoch')
    controller=old['config']['aw_revision'];native.writer_contract(api,repo,old,controller)
    events=('repository_dispatch','workflow_run','issues')
    if old['config'].get('development_pipeline') is not None:events+=('push',)
    identity=review.producer_identity(api,repo,old,proof['run'],True,
                                    native.policy(old)['producer'],events=events)
    need(identity['head']==proof['source'] and identity['run_attempt']==proof['attempt'],'rollout_drain_identity')
    log=review.r.job_log(repo,identity['job_id'])
    rows=review.r.records(log,'AW_NATIVE_MERGE_CUTOVER_V1 ')
    need(len(rows)==1 and review.exact(rows[0],'version repo producer_run results')
         and type(rows[0]['version']) is int and rows[0]['version']==1 and rows[0]['repo']==repo
         and rows[0]['producer_run']==proof['run']
         and isinstance(rows[0]['results'],list) and all(isinstance(row,dict) and row.get('state')=='withdrawn'
             and review.integer(row.get('pr')) and review.sha(row.get('head')) for row in rows[0]['results'])
         and len(rows[0]['results'])==len({row['pr']for row in rows[0]['results']})
         and digest(rows[0])==proof['record_sha256'],'rollout_drain_incomplete')
    job=api.api(f"repos/{repo}/actions/jobs/{identity['job_id']}")
    need(job.get('id')==identity['job_id'] and job.get('status')=='completed'
         and job.get('conclusion')=='success','rollout_drain_incomplete')
    return proof['source'],timestamp(job.get('completed_at'))


def live_fence(api,repo,document,record):
    """Before first activation, require the deployed drain to remain blocked."""
    head=api.api(f'repos/{repo}/commits/main').get('sha')
    need(review.sha(head),'rollout_live_source')
    config=review.ci.strict_json_loads(review.blob(api,repo,'.github/issue-ci-required-gates.json',head))
    manifest=review.blob(api,repo,MANIFEST,head).encode()
    blob=hashlib.sha1(b'blob '+str(len(manifest)).encode()+b'\0'+manifest).hexdigest()
    need(blob==record['actor_manifest'],'rollout_actor_manifest_changed')
    proposed=document['config']
    if 'issue_guard' in config:
        need(config['issue_guard']==proposed['issue_guard']
             and config.get('issue_guard_rollout')==proposed['issue_guard_rollout']
             and policy_digest(repo,config,record['actor_manifest'])==record['policy_sha256'],
             'rollout_live_epoch_changed')
    else:
        marker=native.drain_marker(config)
        need(marker is not None and marker['nonce']==record['epoch']
             and config.get('aw_revision')==proposed['aw_revision']
             and config.get('issue_recovery',{}).get('producer')==proposed['issue_recovery']['producer'],
             'rollout_live_drain_missing')
        current=dict(source_commit=head,config=config)
        native.writer_contract(api,repo,current,config['aw_revision'])
        path=f'repos/{repo}/pulls?state=open&sort=created&direction=asc'
        def inventory():
            rows=api.pages(path)
            need(isinstance(rows,list) and all(isinstance(p,dict) and review.integer(p.get('number'))
                 and review.sha(p.get('head',{}).get('sha')) for p in rows),'rollout_live_queue_unknown')
            result={p['number']:p['head']['sha'] for p in rows}
            need(len(result)==len(rows),'rollout_live_queue_unknown')
            return result
        before=inventory()
        for number,sha in before.items():
            pull=native.provider_pull(api,repo,number)
            need(pull.get('state')=='OPEN' and pull.get('headRefOid')==sha
                 and pull.get('autoMergeRequest') is None,'rollout_live_request_present')
            statuses=api.pages(f'repos/{repo}/commits/{sha}/statuses')
            matching=[s for s in statuses if isinstance(s,dict) and s.get('context')=='claude/verified-merge']
            need(matching and matching[0].get('state') in ('pending','failure','error')
                 and matching[0].get('creator',{}).get('id')==41898282
                 and matching[0].get('creator',{}).get('type')=='Bot','rollout_live_status_unblocked')
        need(inventory()==before,'rollout_live_queue_changed')
    need(api.api(f'repos/{repo}/commits/main').get('sha')==head,'rollout_live_source_changed')


def verify(api,repo,document,log_reader=None):
    """Authorize only the audited epoch and exact protected runtime contracts."""
    need(repo in REPOS and review.sha(document.get('source_commit')),'rollout_target')
    config=document['config'];pointer=config.get('issue_guard_rollout')
    need(review.exact(pointer,'version source epoch') and type(pointer['version']) is int and pointer['version']==1
         and review.sha(pointer['source']) and epoch(pointer['epoch']),'recovery_rollout_not_verified')
    on_main(api,AW,pointer['source'])
    record=selected(review.blob(api,AW,DATA,pointer['source']),repo,pointer['epoch'])
    need(review.exact(record,'version repo epoch state policy_sha256 actor_manifest actors drain conformance authorization')
         and type(record['version']) is int and record['version']==1 and sha256(record['policy_sha256'])
         and review.sha(record['actor_manifest']),'rollout_record_shape')
    need(record['policy_sha256']==policy_digest(repo,config,record['actor_manifest']),'rollout_policy_changed')
    authorized_at=authorization(api,pointer,record)
    guard=sibling('merge_guard');guard_policy=dict(config['issue_guard'],policy_source=document['source_commit'])
    guard.historical_runtime(api,config['aw_revision'],guard_policy)
    guard.worker.check_runtime(guard_policy)
    guard.provider.verified_genesis(api,repo,guard_policy['genesis'])
    # Check the namespace rule without creating a guard ref.
    guard.provider.provider_rules(api,repo,guard_policy['ruleset_id'],1)
    native.writer_contract(api,repo,document,config['aw_revision'])
    recovery=sibling('recovery_entry');recovery.policy(document)
    recovery.workflow_contract(api,repo,document,config['aw_revision'])
    for spec in (config['native_auto_merge']['producer'],config['issue_recovery']['producer']):
        deployed=api.api(f"repos/{repo}/actions/workflows/{spec['id']}")
        need(deployed.get('id')==spec['id'] and deployed.get('path')==spec['path']
             and deployed.get('state')=='active','rollout_workflow_not_active')
    drain_source,drained_at=drain(api,repo,record)
    manifest=actors(api,repo,document,record,drained_at,authorized_at)
    compare=api.api(f"repos/{repo}/compare/{drain_source}...{document['source_commit']}")
    need(compare.get('status') in ('identical','ahead') and compare.get('base_commit',{}).get('sha')==drain_source,
         'rollout_drain_not_in_history')
    controls=conformance(api,config['aw_revision'],record['conformance'],log_reader or review.r.job_log)
    live_fence(api,repo,document,record)
    # Current trusted AW state can revoke an old authorization. Never turn a
    # historical, once-valid operator record into permanent authority.
    head=api.api(f'repos/{AW}/commits/main').get('sha');need(review.sha(head),'rollout_default_changed')
    need(selected(review.blob(api,AW,DATA,head),repo,pointer['epoch'])==record,'rollout_authorization_revoked')
    need(api.api(f'repos/{AW}/commits/main').get('sha')==head,'rollout_default_changed')
    return dict(version=1,repo=repo,epoch=record['epoch'],policy_sha256=record['policy_sha256'],
                authorization_source=pointer['source'],actor_count=len(manifest['actors']),
                drain_source=drain_source,conformance=controls)
