#!/usr/bin/env python3
"""Separate, exact-head policy adoption; never execute candidate code or artifacts.

A verified default-branch grant is required for every target. New product
grants authenticate complete AW controls and the product's independent review
and CI. AW has no Claude gate. Protected-path qualification remains unchanged.
The controller uses metadata and job logs; no model credential is accepted.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import quote


def sibling(name):
    spec = importlib.util.spec_from_file_location('policy_adoption_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = sibling('native_merge')
review = native.review
ci = review.ci
q = ci.qualification_module()
r = review.r
need = review.need
MARKER = 'AW_POLICY_ADOPTION_REVIEW_V1 '
GRANTS_REPO = 'faberline/aw'
GRANTS_PATH = '.github/policy-adoption-grants.json'


def valid_path(path):
    return review.safe_path(path) and not any(ord(c) < 32 or ord(c) == 127 for c in path)


def grant_spec(value):
    """Strict grant data; a candidate cannot supply or extend its own grant."""
    fields = ('version repo pr issue head base base_ref evaluator policy_blob base_policy_blob '
              'issue_sha256 pr_sha256 files_sha256 protected_paths ci review publisher')
    need(isinstance(value,dict), 'grant_schema')
    payload={k:v for k,v in value.items() if k not in ('grant_review','grant_authorization')}
    need(review.exact(payload, fields) and type(value['version']) is int and value['version'] == 1, 'grant_schema')
    if 'grant_review' in value:
        attestation_spec(value['grant_review'],value['repo'])
    if 'grant_authorization' in value:
        authorization_spec(value['grant_authorization'])
        need('grant_review' not in value, 'grant_authorization_ambiguous')
    need(isinstance(value['repo'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value['repo'])
         and review.integer(value['pr']) and review.integer(value['issue'])
         and review.sha(value['head']) and review.sha(value['base']) and review.sha(value['evaluator'])
         and review.sha(value['policy_blob']) and review.sha(value['base_policy_blob'])
         and value['base_ref'] == 'main', 'grant_identity')
    for name in ('issue_sha256','pr_sha256','files_sha256'):
        need(isinstance(value[name], str) and re.fullmatch('[0-9a-f]{64}', value[name]), 'grant_digest')
    paths = value['protected_paths']
    need(isinstance(paths, list) and paths and all(valid_path(p) for p in paths)
         and len(set(paths)) == len(paths) and paths == sorted(paths), 'grant_protected_paths')
    data = value['ci']
    need(review.exact(data, 'run_id attempt jobs_sha256') and review.integer(data['run_id'])
         and review.integer(data['attempt']) and isinstance(data['jobs_sha256'], str)
         and re.fullmatch('[0-9a-f]{64}', data['jobs_sha256']), 'grant_ci')
    data = value['review']
    need(review.exact(data, 'workflow_path workflow_id workflow_blob runner_path runner_blob source_commit branch run_id attempt job job_id receipt_sha256')
         and valid_path(data['workflow_path']) and data['workflow_path'].startswith('.github/workflows/')
         and data['runner_path']=='plugins/faberline/skills/issue-ci/scripts/policy_adoption_target_review.py'
         and isinstance(data['branch'],str) and re.fullmatch(r'codex/[A-Za-z0-9._/-]+',data['branch'])
         and '..' not in data['branch'] and review.integer(data['workflow_id']) and review.integer(data['run_id'])
         and review.integer(data['attempt']) and review.integer(data['job_id']) and review.text(data['job'], 200)
         and all(review.sha(data[k]) for k in ('workflow_blob','runner_blob','source_commit'))
         and isinstance(data['receipt_sha256'], str) and re.fullmatch('[0-9a-f]{64}', data['receipt_sha256']), 'grant_review')
    data = value['publisher']
    need(review.exact(data, 'workflow_path workflow_id workflow_blob source_commit branch job')
         and valid_path(data['workflow_path']) and data['workflow_path'].startswith('.github/workflows/')
         and review.integer(data['workflow_id']) and review.sha(data['workflow_blob'])
         and review.sha(data['source_commit']) and isinstance(data['branch'], str)
         and re.fullmatch(r'codex/[A-Za-z0-9._/-]+',data['branch'])
         and '..' not in data['branch'] and review.text(data['job'], 200), 'grant_publisher')
    return value


def authorization_spec(value):
    need(review.exact(value,'version kind pr') and type(value['version']) is int
         and value['version']==1 and value['kind']=='product-evidence'
         and review.integer(value['pr']), 'grant_authorization_schema')
    return value


def attestation_spec(value, repo):
    fields=('repo pr head blob workflow_path workflow_id workflow_blob source_commit branch '
            'run_id attempt job job_id')
    need(review.exact(value,fields) and value['repo']==repo
         and all(review.integer(value[k]) for k in ('pr','workflow_id','run_id','attempt','job_id'))
         and all(review.sha(value[k]) for k in ('head','blob','workflow_blob','source_commit'))
         and valid_path(value['workflow_path']) and value['workflow_path'].startswith('.github/workflows/')
         and isinstance(value['branch'],str) and re.fullmatch(r'codex/[A-Za-z0-9._/-]+',value['branch'])
         and '..' not in value['branch'] and review.text(value['job'],200), 'grant_attestation_schema')
    return value


def choose_grant(document, repo, evaluator, allow_merged=False):
    need(type(allow_merged) is bool, 'grant_document')
    need(isinstance(document, dict) and set(document) == {'version','grants'}
         and type(document['version']) is int and document['version'] == 1
         and isinstance(document['grants'], list), 'grant_document')
    grants = [grant_spec(value) for value in document['grants']]
    matches = [g for g in grants if g['repo'] == repo and g['evaluator'] == evaluator
               and ('grant_review' in g or 'grant_authorization' in g or allow_merged)]
    need(len(matches) == 1, 'grant_missing_or_ambiguous')
    return matches[0]


def file_inventory(pull, files):
    paths, problems = q.changed_paths(pull, files)
    need(not problems and isinstance(files, list), 'file_inventory')
    inventory=[]
    for file in files:
        name=file.get('filename'); status=file.get('status'); previous=file.get('previous_filename')
        need(valid_path(name) and review.sha(file.get('sha'))
             and status in ('added','removed','modified','renamed'), 'file_metadata')
        if status == 'renamed':
            need(valid_path(previous), 'rename_metadata')
        else:
            previous=name
        inventory.append(dict(path=name, previous=previous, status=status, sha=file['sha']))
    inventory.sort(key=lambda item:item['path'])
    return paths,inventory


def review_result(grant, value, inventory):
    """Authenticate one bounded structured receipt emitted by the trusted job."""
    need(isinstance(value, dict) and set(value) == {'version','repo','pr','issue','head','base',
         'pr_sha256','issue_sha256','files_sha256','ci_jobs_sha256','reviewed_files',
         'requirements','decision','findings','uncertainties'}, 'review_receipt_schema')
    need(review.integer(value.get('pr')) and review.integer(value.get('issue')), 'review_receipt_identity')
    for key in ('repo','pr','issue','head','base','pr_sha256','issue_sha256','files_sha256'):
        need(value[key] == grant[key], 'review_receipt_identity')
    need(type(value['version']) is int and value['version'] == 1
         and value['ci_jobs_sha256'] == grant['ci']['jobs_sha256']
         and value['decision'] == 'pass' and value['findings'] == [] and value['uncertainties'] == [], 'review_not_pass')
    names = [item['path'] for item in inventory]
    need(isinstance(value['reviewed_files'], list) and all(isinstance(x,str) for x in value['reviewed_files'])
         and len(set(value['reviewed_files'])) == len(names)
         and sorted(value['reviewed_files']) == sorted(names), 'review_files_incomplete')
    requirements=value['requirements']
    need(isinstance(requirements, list) and bool(requirements)
         and all(review.exact(x,'id status evidence') and review.text(x['id'],200)
                 and x['status']=='met' and review.text(x['evidence'],4000) for x in requirements)
         and len({x['id'] for x in requirements}) == len(requirements), 'review_requirements_incomplete')
    need(r.digest(value) == grant['review']['receipt_sha256'], 'review_receipt_digest')
    return value


def target_workflow(raw, spec, grant):
    r.literal_uses(raw)
    import yaml
    workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    need(set(workflow)=={'name','on','permissions','jobs'} and workflow['name']=='Reviewed policy target'
         and workflow['on']=={'push':{'branches':[spec['branch']],'paths':[spec['workflow_path']]}}
         and workflow['permissions']=={} and set(workflow['jobs'])=={'review','outcome'}
         and spec['job']=='review', 'target_review_workflow')
    read_job={'runs-on':'ubuntu-latest','timeout-minutes':'70',
              'permissions':{'actions':'read','contents':'read','pull-requests':'read','issues':'read'},
              'steps':[{'uses':'faberline/aw/.github/actions/policy-adoption-target-review@'+grant['evaluator'],
                        'with':{'target-pr':str(grant['pr']),'target-head':grant['head'],
                                'claude-token':'${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}}]}
    outcome_job={'needs':'review','runs-on':'ubuntu-latest','timeout-minutes':'30',
                 'concurrency':{'group':'issue-ci-handoff','cancel-in-progress':'false'},
                 'permissions':{'actions':'read','contents':'write','pull-requests':'write',
                                'statuses':'write','issues':'write'},
                 'steps':[{'uses':'faberline/aw/.github/actions/policy-adoption-review-outcome@'+grant['evaluator'],
                           'with':{'target-pr':str(grant['pr']),'target-head':grant['head'],
                                   'review-run':'${{ github.run_id }}'}}]}
    need(workflow['jobs']['review']==read_job and workflow['jobs']['outcome']==outcome_job,
         'target_review_workflow')


def review_provenance(api, grant, inventory, log_reader):
    spec=grant['review'];repo=grant['repo'];ref=spec['source_commit']
    raw=review.blob(api,repo,spec['workflow_path'],ref,spec['workflow_blob'])
    review.blob(api,GRANTS_REPO,spec['runner_path'],grant['evaluator'],spec['runner_blob'])
    target_workflow(raw,spec,grant)
    run=api.api(f"repos/{repo}/actions/runs/{spec['run_id']}")
    need(run.get('id')==spec['run_id'] and run.get('workflow_id')==spec['workflow_id']
         and run.get('path')==spec['workflow_path'] and run.get('head_sha')==ref
         and run.get('head_branch')==spec['branch'] and run.get('head_repository',{}).get('full_name')==repo
         and run.get('event')=='push' and run.get('run_attempt')==spec['attempt']
         and run.get('status')=='completed' and run.get('conclusion')=='success', 'review_run_identity')
    jobs=api.pages(f"repos/{repo}/actions/runs/{spec['run_id']}/attempts/{spec['attempt']}/jobs",'jobs')
    matches=[j for j in jobs if j.get('name')==spec['job']]
    need(len(matches)==1 and matches[0].get('id')==spec['job_id']
         and matches[0].get('status')=='completed' and matches[0].get('conclusion')=='success', 'review_job_identity')
    records=r.records(log_reader(repo,spec['job_id']),MARKER)
    need(len(records)==1, 'review_receipt_missing_or_ambiguous')
    return review_result(grant,records[0],inventory)


def publisher_provenance(api, grant, run_id, event, completed=False, attempt=None, terminal=False):
    spec=grant['publisher'];repo=grant['repo'];ref=spec['source_commit']
    need(review.integer(run_id) and event=='push' and type(completed) is bool and type(terminal) is bool
         and not (completed and terminal)
         and (attempt is None or (completed or terminal) and review.integer(attempt)), 'publisher_event')
    raw=review.blob(api,repo,spec['workflow_path'],ref,spec['workflow_blob'])
    # Accept one fixed, serialized data-only action. A grant cannot authorize
    # candidate checkout, shell steps or a second privileged writer.
    r.literal_uses(raw)
    import yaml
    workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    need(set(workflow)=={'name','on','permissions','concurrency','jobs'}
         and workflow['name']=='Reviewed policy adoption'
         and workflow['on']=={'push':{'branches':[spec['branch']],'paths':[spec['workflow_path']]}}
         and workflow['permissions']=={}
         and workflow['concurrency']=={'group':'issue-ci-handoff','cancel-in-progress':'false'}
         and set(workflow['jobs'])=={spec['job']}, 'publisher_workflow_contract')
    job=workflow['jobs'][spec['job']]
    need(job=={'runs-on':'ubuntu-latest','timeout-minutes':'30',
               'permissions':{'actions':'read','contents':'write','pull-requests':'write',
                              'statuses':'write','issues':'read'},
               'steps':[{'uses':'faberline/aw/.github/actions/policy-adoption@'+grant['evaluator']}]},
         'publisher_workflow_contract')
    path=f'repos/{repo}/actions/runs/{run_id}'
    run=api.api(path if attempt is None else f'{path}/attempts/{attempt}')
    need(run.get('id')==run_id and run.get('workflow_id')==spec['workflow_id']
         and run.get('path')==spec['workflow_path'] and run.get('head_sha')==ref
         and run.get('head_repository',{}).get('full_name')==repo and run.get('event')==event
         and run.get('head_branch')==spec['branch']
         and review.integer(run.get('run_attempt'))
         and (attempt is None or run['run_attempt']==attempt), 'publisher_run_identity')
    if completed:
        need(run.get('status')=='completed' and run.get('conclusion')=='success', 'publisher_run_identity')
    elif terminal:
        need(run.get('status')=='completed' and run.get('conclusion') in ('success','failure','cancelled','timed_out'),
             'publisher_run_identity')
    else:
        need(run.get('status') in ('queued','in_progress'), 'publisher_run_identity')
    jobs=api.pages(f"repos/{repo}/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs",'jobs')
    matches=[j for j in jobs if j.get('name')==spec['job']]
    need(len(matches)==1 and review.integer(matches[0].get('id')), 'publisher_job_identity')
    if completed:
        need(matches[0].get('status')=='completed' and matches[0].get('conclusion')=='success',
             'publisher_job_identity')
    elif terminal:
        need(matches[0].get('status')=='completed', 'publisher_job_identity')
    else:
        need(matches[0].get('status')=='in_progress', 'publisher_job_identity')
    return dict(run_id=run_id,run_attempt=run['run_attempt'],job_id=matches[0]['id'],source_commit=ref)


_GUARDED_POLICY_PROOF=object()


def guarded_adoption_eligible(candidate,prior,proof=None):
    """Hold guard transitions unless the trusted publisher verified rollout."""
    need(isinstance(candidate,dict) and isinstance(prior,dict),'guarded_adoption_not_ready')
    # The drain marker cannot itself prove that all writers were upgraded or
    # that old work has ended. No adoption path authorizes this transition yet.
    need('issue_guard_cutover' not in candidate and 'issue_guard_cutover' not in prior,
         'guarded_cutover_not_ready')
    if 'issue_guard' not in candidate and 'issue_guard' not in prior:
        return
    need(proof is _GUARDED_POLICY_PROOF and isinstance(candidate.get('issue_guard'),dict)
         and ('issue_guard' not in prior or prior['issue_guard']==candidate['issue_guard']),
         'guarded_adoption_not_ready')


def candidate_policy(api, grant, proof=None):
    repo=grant['repo'];head=grant['head'];base=grant['base']
    path='.github/issue-ci-required-gates.json'
    candidate=ci.strict_json_loads(review.blob(api,repo,path,head,grant['policy_blob']))
    prior=ci.strict_json_loads(review.blob(api,repo,path,base,grant['base_policy_blob']))
    ci.validate_config(candidate);ci.validate_adoption(candidate);ci.validate_config(prior)
    guarded_adoption_eligible(candidate,prior,proof)
    need(candidate.get('lifecycle_mode')=='full' and candidate.get('qualification_blockers')==[]
         and candidate.get('qualification',{}).get('version')==2, 'candidate_policy_not_full_v2')
    native.handoff_policy(candidate)
    need(review.sha(candidate.get('aw_revision')), 'candidate_controller_identity')
    native.writer_contract(api,repo,dict(config=candidate,source_commit=head),candidate['aw_revision'])
    old={w['path']:set(w['jobs']) for w in prior['workflows']}
    new={w['path']:set(w['jobs']) for w in candidate['workflows']}
    retained = (review.sibling('development_pipeline').retains_workflows(prior, candidate)
                if candidate.get('development_pipeline') is not None else
                all(path in new and jobs <= new[path] for path,jobs in old.items()))
    need(retained, 'candidate_dropped_existing_gate')
    return candidate


def candidate_evidence(api, grant, candidate):
    return _candidate_evidence(api,grant,candidate,False)


def candidate_merged_evidence(api, grant, candidate):
    """Recheck the exact grant-bound jobs after merge; never authorize a write."""
    return _candidate_evidence(api,grant,candidate,True)


def _candidate_evidence(api, grant, candidate, merged):
    repo,head,base=grant['repo'],grant['head'],grant['base']
    need(len(candidate['workflows']) == 1, 'candidate_workflow_count')
    for workflow in candidate['workflows']:
        identity=candidate['qualification']['trusted_workflows'][workflow['path']]
        review.blob(api,repo,workflow['path'],head,identity['definition_sha'])
    # This context is used only to authenticate the current run association.
    # It never clears the ordinary selector's protected-path hold.
    scoped=copy.deepcopy(candidate)
    scoped['_qualification_context']=dict(pr=grant['pr'],head=head,base=base,untrusted_workflows=[])
    snapshots=api.snapshot(repo,head,scoped)
    summary=api.evaluate_evidence(candidate['workflows'],snapshots)
    need(not summary['failures'] and not summary['pending'], 'candidate_ci_not_green')
    rows=[]
    for workflow in candidate['workflows']:
        run,jobs=snapshots[workflow['path']]
        identity=candidate['qualification']['trusted_workflows'][workflow['path']]
        need(run.get('id')==grant['ci']['run_id'] and run.get('run_attempt')==grant['ci']['attempt']
             and run.get('workflow_id')==identity['id'] and run.get('path')==workflow['path']
             and run.get('head_sha')==head and run.get('event')=='pull_request'
             and run.get('head_repository',{}).get('full_name')==repo, 'candidate_ci_identity')
        links=run.get('pull_requests')
        associated=(isinstance(links,list) and len(links)==1 and links[0].get('number')==grant['pr']
                    and links[0].get('head',{}).get('sha')==head and links[0].get('base',{}).get('sha')==base)
        need(associated or (merged and links==[]),'candidate_ci_identity')
        names=[job.get('name') for job in jobs]
        need(len(names)==len(workflow['jobs']) and len(set(names))==len(names)
             and set(names)==set(workflow['jobs']), 'candidate_ci_job_inventory')
        for job in jobs:
            need(review.integer(job.get('id')) and job.get('status')=='completed'
                 and job.get('conclusion')=='success', 'candidate_ci_job_not_green')
            rows.append({key:job[key] for key in ('id','name','status','conclusion')})
    rows.sort(key=lambda item:item['name'])
    need(r.digest(rows)==grant['ci']['jobs_sha256'], 'candidate_ci_jobs_changed')
    return rows


def load_grants():
    """Read only the reviewed AW default branch, never caller or PR data."""
    return review.action.trusted_policy(ci, GRANTS_REPO, GRANTS_PATH, metadata=True)


def evaluate(api, grant, log_reader, proof=None):
    repo=grant['repo'];number=grant['pr'];head=grant['head'];base=grant['base']
    pull=api.api(f'repos/{repo}/pulls/{number}')
    review.pull_identity(pull,repo,number,head)
    meta=api.api(f'repos/{repo}')
    need(pull['base'].get('sha')==base and pull['base'].get('ref')==grant['base_ref']
         and meta.get('default_branch')==grant['base_ref'], 'target_base_changed')
    current=api.api(f"repos/{repo}/commits/{quote(grant['base_ref'],safe='')}")
    need(current.get('sha')==base, 'default_base_changed')
    issue_number=review.linked_issue(pull)
    need(issue_number==grant['issue'] and r.digest([pull.get('title'),pull.get('body')])==grant['pr_sha256'], 'pr_contract_changed')
    issue=api.api(f'repos/{repo}/issues/{issue_number}')
    need(issue.get('number')==issue_number and issue.get('state')=='open' and 'pull_request' not in issue
         and r.digest({k:issue.get(k) for k in ('number','title','body')})==grant['issue_sha256'], 'issue_contract_changed')
    files=api.pages(f'repos/{repo}/pulls/{number}/files')
    _,inventory=file_inventory(pull,files)
    need(r.digest(inventory)==grant['files_sha256'], 'pr_files_changed')
    candidate=candidate_policy(api,grant,proof)
    plan=q.select(candidate,pull,files,issue)
    prefix='gate infrastructure change requires reviewed policy update: '
    protected=sorted(item[len(prefix):] for item in plan['issues'] if item.startswith(prefix))
    need(protected==grant['protected_paths']
         and len(plan['issues'])==len(protected), 'unapproved_or_unknown_applicability')
    need(set(plan['selected'])==set(candidate['qualification']['checks']), 'incomplete_candidate_selection')
    ci_rows=candidate_evidence(api,grant,candidate)
    receipt=review_provenance(api,grant,inventory,log_reader)
    context=dict(repo=repo,pr=number,head=head,base=base,base_ref=grant['base_ref'])
    p=native.policy(dict(config=candidate,source_commit=head))
    native.base_rules(api,repo,context,p)
    provider=native.provider_pull(api,repo,number)
    native.binding(provider,context)
    return dict(context=context,issue=issue_number,policy=p,provider=provider,
                receipt=receipt,ci_jobs=ci_rows)


def _evaluate_guarded(api,grant,log_reader):
    """Internal path for a publisher that already authenticated its guard."""
    return evaluate(api,grant,log_reader,proof=_GUARDED_POLICY_PROOF)


def latest_target_review(api,grant):
    """Reject a later review attempt for the same pinned review workflow."""
    spec=grant['review'];repo=grant['repo']
    original=api.api(f"repos/{repo}/actions/runs/{spec['run_id']}")
    need(original.get('id')==spec['run_id'] and review.integer(original.get('run_number'))
         and original.get('run_attempt')==spec['attempt'],'review_superseded')
    rows=api.pages(f"repos/{repo}/actions/workflows/{spec['workflow_id']}/runs",'workflow_runs')
    need(isinstance(rows,list),'review_superseded')
    seen=[]
    for row in rows:
        if (row.get('workflow_id')!=spec['workflow_id'] or row.get('path')!=spec['workflow_path']
                or row.get('event')!='push' or row.get('head_branch')!=spec['branch']
                or row.get('head_repository',{}).get('full_name')!=repo):continue
        need(review.integer(row.get('id')) and review.integer(row.get('run_number'))
             and review.integer(row.get('run_attempt')),'review_superseded')
        seen.append(row)
    need(len({row['id'] for row in seen})==len(seen)
         and len({row['run_number'] for row in seen})==len(seen)
         and sum(row['id']==spec['run_id'] and row['run_attempt']==spec['attempt']
                 and row['run_number']==original['run_number'] for row in seen)==1
         and all((row['run_number'],row['run_attempt']) <= (original['run_number'],spec['attempt'])
                 for row in seen),'review_superseded')


def evaluate_merged(api, grant, log_reader):
    """Revalidate the reviewed adoption after merge, without writing anything.

    The caller must first authenticate the grant and the publisher's intent.
    Exact merge ancestry and issue ownership are checked by the recovery flow.
    """
    repo,number,head,base=grant['repo'],grant['pr'],grant['head'],grant['base']
    pull=api.api(f'repos/{repo}/pulls/{number}')
    review.merged_pull_identity(pull,repo,number,head)
    meta=api.api(f'repos/{repo}')
    need(pull['base'].get('sha')==base and pull['base'].get('ref')==grant['base_ref']
         and meta.get('default_branch')==grant['base_ref'],'target_base_changed')
    issue_number=review.linked_issue(pull)
    need(issue_number==grant['issue'] and r.digest([pull.get('title'),pull.get('body')])==grant['pr_sha256'],
         'pr_contract_changed')
    issue=api.api(f'repos/{repo}/issues/{issue_number}')
    need(issue.get('number')==issue_number and issue.get('state')=='open' and 'pull_request' not in issue
         and r.digest({k:issue.get(k) for k in ('number','title','body')})==grant['issue_sha256'],
         'issue_contract_changed')
    files=api.pages(f'repos/{repo}/pulls/{number}/files')
    _,inventory=file_inventory(pull,files)
    need(r.digest(inventory)==grant['files_sha256'],'pr_files_changed')
    candidate=candidate_policy(api,grant,proof=_GUARDED_POLICY_PROOF)
    current=review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    need(current['config']==candidate,'merge_recovery_policy_changed')
    plan=q.select(candidate,pull,files,issue)
    prefix='gate infrastructure change requires reviewed policy update: '
    protected=sorted(item[len(prefix):] for item in plan['issues'] if item.startswith(prefix))
    need(protected==grant['protected_paths'] and len(plan['issues'])==len(protected)
         and set(plan['selected'])==set(candidate['qualification']['checks']),
         'unapproved_or_unknown_applicability')
    ci_rows=candidate_merged_evidence(api,grant,candidate)
    receipt=review_provenance(api,grant,inventory,log_reader)
    latest_target_review(api,grant)
    current_commit=api.api(f"repos/{repo}/commits/{quote(grant['base_ref'],safe='')}")
    need(review.sha(current_commit.get('sha')),'default_base_changed')
    policy=native.policy(dict(config=candidate,source_commit=head))
    native.base_rules(api,repo,dict(base_ref=grant['base_ref'],base=current_commit['sha']),policy)
    return dict(issue=issue_number,receipt=receipt,ci_jobs=ci_rows)


GRANT_MARKER = 'AW_POLICY_ADOPTION_GRANT_REVIEW_V1 '


def grant_review_workflow(api,grant,att,evaluator,source_commit,grant_head,expected_blob=None):
    # This review workflow cannot execute candidate data or add arbitrary steps.
    raw=review.blob(api,grant['repo'],att['workflow_path'],source_commit,expected_blob)
    r.literal_uses(raw)
    import yaml
    workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    need(set(workflow)=={'name','on','permissions','jobs'} and workflow['name']=='Reviewed policy grant'
         and workflow['on']=={'push':{'branches':[att['branch']],'paths':[att['workflow_path']]}}
         and workflow['permissions']=={} and set(workflow['jobs'])=={att['job']}, 'grant_review_workflow')
    job=workflow['jobs'][att['job']]
    steps=job.get('steps') if isinstance(job,dict) else None
    need(isinstance(steps,list) and len(steps)==1 and isinstance(steps[0],dict)
         and isinstance(steps[0].get('with'),dict),'grant_review_workflow')
    inputs=steps[0]['with']
    need(review.exact(inputs,'grant-pr grant-head claude-token')
         and isinstance(inputs['grant-pr'],str) and re.fullmatch(r'[1-9][0-9]*',inputs['grant-pr'])
         and review.sha(inputs['grant-head']) and inputs['claude-token']=='${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}',
         'grant_review_workflow')
    need(job=={'runs-on':'ubuntu-latest','timeout-minutes':'70',
               'permissions':{'actions':'read','contents':'read','pull-requests':'read','issues':'read'},
               'steps':[{'uses':'faberline/aw/.github/actions/policy-adoption-grant-review@'+evaluator,
                         'with':{'grant-pr':inputs['grant-pr'],'grant-head':inputs['grant-head'],
                                 'claude-token':'${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}}]},
         'grant_review_workflow')
    target=dict(pr=int(inputs['grant-pr']),head=inputs['grant-head'])
    if grant_head is not None:
        need(target==dict(pr=att['pr'],head=grant_head),'grant_review_workflow')
    return target


def verify_final_grant_review(api,grant,att,evaluator,pr,final_blob,prior,log_reader):
    # The final receipt is external to the grant, avoiding a self-hash cycle.
    # Choose the newest run of the same trusted workflow that targets the final
    # PR head. A newer failure cannot be hidden by an older passing run.
    head=pr.get('head',{}).get('sha')
    need(review.sha(head) and review.sha(final_blob), 'final_grant_identity')
    prior_run=api.api(f"repos/{grant['repo']}/actions/runs/{att['run_id']}")
    need(isinstance(prior_run,dict) and prior_run.get('id')==att['run_id']
         and prior_run.get('workflow_id')==att['workflow_id']
         and prior_run.get('path')==att['workflow_path']
         and prior_run.get('run_attempt')==att['attempt']
         and review.integer(prior_run.get('run_number')), 'final_grant_runs')
    path=f"repos/{grant['repo']}/actions/workflows/{att['workflow_id']}/runs?branch={quote(att['branch'],safe='')}&event=push"
    runs=api.pages(path,'workflow_runs')
    need(isinstance(runs,list) and len(runs)<=500
         and all(isinstance(x,dict) and review.integer(x.get('id')) and review.integer(x.get('run_number'))
                 and review.integer(x.get('run_attempt')) for x in runs)
         and len({x['id'] for x in runs})==len(runs)
         and len({x['run_number'] for x in runs})==len(runs),'final_grant_runs')
    selected=None
    for item in sorted(runs,key=lambda x:(x['run_number'],x['run_attempt']),reverse=True):
        if item['run_number']<=prior_run['run_number']:
            continue
        need(review.sha(item.get('head_sha')),'final_grant_runs')
        try:
            target=grant_review_workflow(api,grant,att,evaluator,item['head_sha'],None)
        except (RuntimeError,KeyError,TypeError,ValueError):
            raise review.Refused('final_grant_workflow_unknown') from None
        if target!=dict(pr=att['pr'],head=head):
            continue
        selected=api.api(f"repos/{grant['repo']}/actions/runs/{item['id']}")
        break
    need(selected is not None, 'final_grant_review_missing')
    need(selected.get('id')==item['id'] and selected.get('workflow_id')==att['workflow_id']
         and selected.get('path')==att['workflow_path'] and selected.get('head_sha')==item['head_sha']
         and selected.get('run_number')==item['run_number'] and selected.get('run_attempt')==item['run_attempt']
         and selected.get('head_branch')==att['branch'] and selected.get('event')=='push'
         and selected.get('head_repository',{}).get('full_name')==grant['repo']
         and review.integer(selected.get('run_attempt')) and selected.get('status')=='completed'
         and selected.get('conclusion')=='success', 'final_grant_review_run')
    jobs=api.pages(f"repos/{grant['repo']}/actions/runs/{selected['id']}/attempts/{selected['run_attempt']}/jobs",'jobs')
    matches=[j for j in jobs if j.get('name')==att['job']]
    need(len(jobs)==1 and len(matches)==1 and review.integer(matches[0].get('id'))
         and matches[0].get('status')=='completed' and matches[0].get('conclusion')=='success', 'final_grant_review_job')
    rows=r.records(log_reader(grant['repo'],matches[0]['id']),GRANT_MARKER)
    expected=dict(prior,grant_head=head,grant_blob=final_blob)
    need(len(rows)==1 and rows[0]==expected, 'final_grant_review_receipt')
    fresh=api.pages(path,'workflow_runs')
    keys=('id','run_number','run_attempt','head_sha')
    def inventory(items):
        need(isinstance(items,list) and len(items)<=500
             and all(isinstance(row,dict) and all(review.integer(row.get(k)) for k in keys[:3])
                     and review.sha(row.get('head_sha')) for row in items)
             and len({row['id'] for row in items})==len(items)
             and len({row['run_number'] for row in items})==len(items),
             'final_grant_runs')
        return sorted(tuple(row[k] for k in keys) for row in items)
    need(inventory(fresh)==inventory(runs),'final_grant_review_changed')
    latest=api.api(f"repos/{grant['repo']}/actions/runs/{selected['id']}")
    stable=('id','workflow_id','path','head_sha','head_branch','event','head_repository',
            'run_number','run_attempt','status','conclusion')
    need(isinstance(latest,dict) and all(latest.get(k)==selected.get(k) for k in stable),
         'final_grant_review_changed')
    prior_again=api.api(f"repos/{grant['repo']}/actions/runs/{att['run_id']}")
    need(isinstance(prior_again,dict) and all(prior_again.get(k)==prior_run.get(k)
         for k in ('id','workflow_id','path','run_number','run_attempt','status','conclusion')),
         'final_grant_review_changed')
    return rows[0]


def verify_product_authorization(api, document, grant, evaluator, log_reader, require_merged):
    """AW authenticates data and product evidence. AW has no Claude gate."""
    auth=authorization_spec(grant['grant_authorization'])
    need(require_merged is True and grant['evaluator']==evaluator
         and grant['repo'] in {f'faberline/{name}' for name in ('core','lumen','keep','tape','defer','relay')}
         and isinstance(document,dict) and review.sha(document.get('source_commit')),
         'product_grant_source')
    source=document['source_commit']
    meta=api.api(f'repos/{GRANTS_REPO}')
    need(meta.get('full_name')==GRANTS_REPO and meta.get('default_branch')=='main',
         'product_grant_default_changed')
    pull=api.api(f"repos/{GRANTS_REPO}/pulls/{auth['pr']}")
    need(pull.get('number')==auth['pr'] and pull.get('state')=='closed' and pull.get('merged') is True
         and pull.get('base',{}).get('ref')=='main'
         and pull.get('head',{}).get('repo',{}).get('full_name')==GRANTS_REPO
         and pull.get('base',{}).get('repo',{}).get('full_name')==GRANTS_REPO
         and all(review.sha(sha) for sha in (pull.get('merge_commit_sha'),pull.get('head',{}).get('sha'),
                                           pull.get('base',{}).get('sha'))), 'product_grant_not_merged')
    _,inventory=file_inventory(pull,api.pages(f"repos/{GRANTS_REPO}/pulls/{auth['pr']}/files"))
    need(len(inventory)==1 and inventory[0]['path']==GRANTS_PATH and inventory[0]['status']=='modified',
         'product_grant_not_data_only')
    raw=review.blob(api,GRANTS_REPO,GRANTS_PATH,source,inventory[0]['sha'])
    need(ci.strict_json_loads(raw)==document.get('config')
         and choose_grant(document['config'],grant['repo'],evaluator)==grant, 'product_grant_data_changed')
    for before,after in ((pull['merge_commit_sha'],source),(source,'main')):
        comparison=api.api(f'repos/{GRANTS_REPO}/compare/{before}...{after}')
        need(comparison.get('status') in ('identical','ahead')
             and comparison.get('base_commit',{}).get('sha')==before, 'product_grant_not_on_main')
    # Only the grant file may change. The complete AW control workflow and
    # all its control code therefore remain identical to the reviewed base.
    path='.github/workflows/native-auto-merge-controls.yml'
    base,head=pull['base']['sha'],pull['head']['sha']
    ancestry=api.api(f'repos/{GRANTS_REPO}/compare/{base}...{head}')
    need(type(ancestry.get('behind_by')) is int and ancestry['behind_by']==0
         and ancestry.get('merge_base_commit',{}).get('sha')==base, 'product_grant_behind_base')
    workflow=review.blob(api,GRANTS_REPO,path,base)
    need(review.blob(api,GRANTS_REPO,path,head)==workflow, 'product_grant_controls_changed')
    identity=api.api(f'repos/{GRANTS_REPO}/actions/workflows/native-auto-merge-controls.yml')
    need(review.integer(identity.get('id')) and identity.get('path')==path,'product_grant_controls_identity')
    # GitHub clears run.pull_requests after merge. Bind the AW control run to
    # the merged grant's exact head and unchanged complete control source.
    # Product PR/head/base qualification below keeps its stricter association.
    runs=api.pages(f'repos/{GRANTS_REPO}/actions/workflows/native-auto-merge-controls.yml/runs?head_sha={head}&event=pull_request','workflow_runs')
    runs=[run for run in runs if run.get('head_sha')==head and run.get('event')=='pull_request'
          and run.get('path')==path and run.get('head_repository',{}).get('full_name')==GRANTS_REPO]
    need(runs and all(review.integer(run.get('run_number')) and review.integer(run.get('run_attempt'))
                     for run in runs),'product_grant_controls_missing')
    run=max(runs,key=lambda row:(row['run_number'],row['run_attempt']))
    need(review.integer(run.get('id')) and run.get('workflow_id')==identity['id']
         and run.get('repository',{}).get('full_name')==GRANTS_REPO, 'product_grant_controls_identity')
    jobs=api.pages(f"repos/{GRANTS_REPO}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs",'jobs')
    workflows=[dict(path=path,jobs=['complete isolated native merge controls'])]
    summary=api.evaluate_evidence(workflows,{path:(run,jobs)})
    need(not summary['failures'] and not summary['pending'],'product_grant_controls_not_green')
    need(len(jobs)==1 and jobs[0]['name']=='complete isolated native merge controls',
         'product_grant_controls_incomplete')
    target=api.api(f"repos/{grant['repo']}/pulls/{grant['pr']}")
    need(type(target.get('merged')) is bool and target.get('state') in ('open','closed')
         and ((target['state']=='closed')==target['merged']),'product_grant_target_state')
    latest_target_review(api,grant)
    result=(evaluate_merged(api,grant,log_reader) if target['merged'] else evaluate(api,grant,log_reader))
    need(result['issue']==grant['issue'] and r.digest(result['receipt'])==grant['review']['receipt_sha256'],
         'product_grant_evidence_changed')
    return dict(version=1,kind='product-evidence',grant_pr=auth['pr'],grant_source=source,
                grant_sha256=r.digest(grant),control_run=run['id'],control_attempt=run['run_attempt'],
                review_sha256=grant['review']['receipt_sha256'],ci=grant['ci'])


def verify_grant_attestation(api, document, grant, evaluator, log_reader=r.job_log, require_merged=True):
    """Authenticate an independent review of the exact non-attestation grant.

    The reviewed proposal can precede attaching this receipt. Only the opaque
    attestation may change afterwards; every authorized grant field must match.
    """
    if 'grant_authorization' in grant:
        return verify_product_authorization(api,document,grant,evaluator,log_reader,require_merged)
    if 'grant_review' not in grant:
        need(require_merged is True, 'merged_grant_requires_default')
        target=api.api(f"repos/{grant['repo']}/pulls/{grant['pr']}")
        need(isinstance(target,dict) and target.get('state') in ('open','closed')
             and type(target.get('merged')) is bool
             and ((target['state']=='closed') == target['merged']), 'merged_grant_target_state')
        merged_review=review.sibling('policy_adoption_merged_grant')
        result=merged_review.authorize(api,grant['repo'],evaluator,log_reader,
                                       grant_loader=load_grants,merged=target['merged'])
        need(result['document']==document and result['grant']==grant, 'merged_grant_source_changed')
        return result['evidence']
    att=attestation_spec(grant.get('grant_review'),grant['repo'])
    payload={k:v for k,v in grant.items() if k!='grant_review'}
    grant_spec(payload)
    pr=api.api(f"repos/{GRANTS_REPO}/pulls/{att['pr']}")
    need(pr.get('base',{}).get('ref')=='main'
         and pr.get('head',{}).get('repo',{}).get('full_name')==GRANTS_REPO, 'grant_pr_identity')
    if require_merged:
        need(pr.get('merged') is True and review.sha(pr.get('merge_commit_sha')), 'grant_pr_not_merged')
    else:
        need(pr.get('state')=='open' and pr.get('merged') is not True
             and pr.get('head',{}).get('sha')==document['source_commit']
             and api.api(f'repos/{GRANTS_REPO}/commits/main').get('sha')==pr.get('base',{}).get('sha'),
             'grant_proposal_not_current')
    grant_files=api.pages(f"repos/{GRANTS_REPO}/pulls/{att['pr']}/files")
    _,grant_inventory=file_inventory(pr,grant_files)
    need(len(grant_inventory)==1 and grant_inventory[0]['path']==GRANTS_PATH
         and grant_inventory[0]['status']=='modified', 'grant_pr_not_data_only')
    current_raw=review.blob(api,GRANTS_REPO,GRANTS_PATH,document['source_commit'],grant_inventory[0]['sha'])
    need(ci.strict_json_loads(current_raw)==document['config'], 'grant_default_source_changed')
    commits=api.pages(f"repos/{GRANTS_REPO}/pulls/{att['pr']}/commits")
    need(any(item.get('sha')==att['head'] for item in commits), 'grant_reviewed_head_not_in_pr')
    if require_merged:
        comparison=api.api(f"repos/{GRANTS_REPO}/compare/{pr['merge_commit_sha']}...{document['source_commit']}")
        need(comparison.get('status') in ('identical','ahead'), 'grant_merge_not_in_default_history')
    proposal=ci.strict_json_loads(review.blob(api,GRANTS_REPO,GRANTS_PATH,att['head'],att['blob']))
    need(isinstance(proposal,dict) and set(proposal)=={'version','grants'}
         and type(proposal['version']) is int and proposal['version']==1 and isinstance(proposal['grants'],list),
         'grant_proposal_schema')
    matches=[g for g in proposal['grants'] if isinstance(g,dict) and g.get('repo')==grant['repo']
             and g.get('evaluator')==evaluator]
    need(len(matches)==1 and {k:v for k,v in matches[0].items() if k!='grant_review'}==payload,
         'reviewed_grant_differs')
    grant_review_workflow(api,grant,att,evaluator,att['source_commit'],att['head'],att['workflow_blob'])
    run=api.api(f"repos/{grant['repo']}/actions/runs/{att['run_id']}")
    need(run.get('id')==att['run_id'] and run.get('workflow_id')==att['workflow_id']
         and run.get('path')==att['workflow_path'] and run.get('head_sha')==att['source_commit']
         and run.get('head_branch')==att['branch'] and run.get('event')=='push'
         and run.get('head_repository',{}).get('full_name')==grant['repo']
         and run.get('run_attempt')==att['attempt'] and run.get('status')=='completed'
         and run.get('conclusion')=='success', 'grant_review_run')
    jobs=api.pages(f"repos/{grant['repo']}/actions/runs/{att['run_id']}/attempts/{att['attempt']}/jobs",'jobs')
    matching=[j for j in jobs if j.get('name')==att['job']]
    need(len(matching)==1 and matching[0].get('id')==att['job_id']
         and matching[0].get('status')=='completed' and matching[0].get('conclusion')=='success', 'grant_review_job')
    rows=r.records(log_reader(grant['repo'],att['job_id']),GRANT_MARKER)
    need(len(rows)==1 and isinstance(rows[0],dict), 'grant_review_receipt')
    row=rows[0]
    fields=('version repo target_pr target_head grant_pr grant_head grant_blob grant_digest '
            'grant_issue grant_pr_sha256 grant_issue_sha256 decision findings uncertainties')
    need(review.exact(row,fields) and type(row['version']) is int and row['version']==1
         and row['repo']==grant['repo'] and row['target_pr']==grant['pr'] and row['target_head']==grant['head']
         and review.integer(row['target_pr']) and review.integer(row['grant_pr']) and review.integer(row['grant_issue'])
         and row['grant_pr']==att['pr'] and row['grant_head']==att['head'] and row['grant_blob']==att['blob']
         and row['grant_digest']==r.digest(payload) and row['decision']=='pass'
         and row['findings']==[] and row['uncertainties']==[], 'grant_review_receipt')
    grant_issue=review.linked_issue(pr)
    need(grant_issue==row['grant_issue'] and r.digest([pr.get('title'),pr.get('body')])==row['grant_pr_sha256'],
         'grant_pr_contract_changed')
    source_issue=api.api(f'repos/{GRANTS_REPO}/issues/{grant_issue}')
    need(source_issue.get('number')==grant_issue
         and source_issue.get('state')==('closed' if require_merged else 'open')
         and r.digest({k:source_issue.get(k) for k in ('number','title','body')})==row['grant_issue_sha256'],
         'grant_issue_contract_changed')
    if require_merged:
        verify_final_grant_review(api,grant,att,evaluator,pr,grant_inventory[0]['sha'],row,log_reader)
    return row


def prepare_adoption_close_record(api, document, grant, evaluator, run_id, receipt):
    """Snapshot a separately granted target; this does not close its issue."""
    repo,number,head=grant['repo'],grant['pr'],grant['head']
    source=grant['publisher']['source_commit']
    attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_EVENT_NAME')=='push'
         and os.environ.get('GITHUB_REPOSITORY')==repo and os.environ.get('GITHUB_RUN_ID')==str(run_id)
         and os.environ.get('GITHUB_WORKFLOW_SHA')==source and re.fullmatch(r'[1-9][0-9]*',attempt)
         and review.integer(run_id) and grant.get('evaluator')==evaluator and review.sha(evaluator)
         and r.digest(receipt)==grant['review']['receipt_sha256'], 'adoption_close_source')
    run=api.api(f'repos/{repo}/actions/runs/{run_id}')
    need(run.get('id')==run_id and run.get('run_attempt')==int(attempt), 'adoption_close_attempt')
    pull=api.api(f'repos/{repo}/pulls/{number}')
    issue=api.api(f"repos/{repo}/issues/{grant['issue']}")
    observation=review.sibling('post_merge_observation')
    baseline=observation.stable_ownership(api,repo,number,head,grant['base'],grant['base_ref'],pull,issue,
                                           grant['issue_sha256'],grant['pr_sha256'])
    snapshot=observation.capture(repo,number,head,grant['base'],grant['base_ref'],pull,issue,
                                 grant['issue_sha256'],grant['pr_sha256'],ownership_events=baseline)
    return observation.record(snapshot,run_id,int(attempt),source,evaluator,
        grant['review']['run_id'],grant['review']['receipt_sha256'],r.digest(grant),document['source_commit'])


def dispatch_adoption_recovery(api,repo):
    """Wake the currently deployed recovery workflow, if one is configured."""
    document=review.action.trusted_policy(api,repo,'.github/issue-ci-required-gates.json',metadata=True)
    if 'issue_guard' not in document['config']:
        # A bootstrap merge cannot dispatch a workflow not yet on main.
        return False
    recovery=review.sibling('recovery_entry')
    recovery.workflow_contract(api,repo,document,document['config']['aw_revision'])
    api.api(f'repos/{repo}/dispatches',method='POST',data=dict(event_type='aw-verified-merge-recover'))
    return True


def enqueue(api, grant_document, evaluator, run_id, event, grant_loader=load_grants, log_reader=r.job_log,
            prepare_record=None, direct_mode=False):
    """Queue only after separate grant, exact CI and independent review verification."""
    need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'), 'model_credential_in_publisher')
    document=grant_loader()
    need(isinstance(document,dict) and review.sha(document.get('source_commit')), 'untrusted_grant_source')
    grant=choose_grant(document['config'],grant_document,evaluator,allow_merged=True)
    # Refuse before acquiring a guard if the caller selected the legacy queue.
    need('grant_review' in grant or 'grant_authorization' in grant or direct_mode, 'merged_grant_direct_guard_required')
    attestation=verify_grant_attestation(api,document,grant,evaluator,log_reader)
    pub=grant['publisher']
    need(os.environ.get('GITHUB_REPOSITORY')==grant['repo']
         and os.environ.get('GITHUB_EVENT_NAME')==event and os.environ.get('GITHUB_RUN_ID')==str(run_id)
         and os.environ.get('GITHUB_SHA')==pub['source_commit']
         and os.environ.get('GITHUB_WORKFLOW_SHA')==pub['source_commit']
         and os.environ.get('GITHUB_REF')=='refs/heads/'+pub['branch'], 'publisher_context')
    publisher_provenance(api,grant,run_id,event)
    guarded=None;guard_action=None
    try:
        before=evaluate(api,grant,log_reader)
    except review.Refused as error:
        if str(error)!='guarded_adoption_not_ready':raise
        guard_action=review.sibling('adoption_guard')
        # This draft still fails before any mutation until rollout is proved.
        guarded=guard_action.begin(api,document,grant,evaluator,run_id,log_reader,
                                   lambda line:print(line,flush=True))
        before=_evaluate_guarded(api,grant,log_reader)
    # A newly merged data-only grant can only enter the verified direct path.
    # Never let it fall back to the legacy auto-merge queue without a guard.
    need('grant_review' in grant or 'grant_authorization' in grant or (guarded is not None and direct_mode),
         'merged_grant_direct_guard_required')
    repo,number,head=grant['repo'],grant['pr'],grant['head']
    context,p=before['context'],before['policy']
    headline,body=f'Merge PR #{number}',f"Closes #{grant['issue']}"
    request=before['provider'].get('autoMergeRequest')
    need(guarded is None or request is None,'guard_request_owner_unknown')
    if request is not None:
        native.queued_request(before['provider'],p,headline,body)
    need(grant_loader()==document, 'grant_changed')
    target=f"https://github.com/{repo}/actions/runs/{grant['review']['run_id']}"
    prepared_module=None;nonce=None
    if guarded is not None:
        need(prepare_record is not None,'guard_record_required')
        need(guard_action.recheck(api,document,grant,evaluator,run_id,guarded,log_reader)==attestation,
             'adoption_guard_review_changed')
        prepared_module=review.sibling('prepared_status');nonce=secrets.token_hex(16)
        intent=guarded['intent']['payload']
        target=prepared_module.target_identity(repo,grant['review']['run_id'],intent['run'],intent['attempt'],nonce)
    pending=api.api(f'repos/{repo}/statuses/{head}',method='POST',data=dict(
        state='pending',context=p['status_context'],description='Rechecking reviewed policy adoption',
        target_url=target))
    try:
        current=_evaluate_guarded(api,grant,log_reader) if guarded is not None else evaluate(api,grant,log_reader)
        need(current==before and grant_loader()==document
             and verify_grant_attestation(api,document,grant,evaluator,log_reader)==attestation,
             'adoption_evidence_changed')
        publisher_provenance(api,grant,run_id,event)
        if guarded is not None:
            need(guard_action.recheck(api,document,grant,evaluator,run_id,guarded,log_reader)==attestation,
                 'adoption_guard_review_changed')
        if guarded is not None and direct_mode:
            # Keep policy adoption on the same synchronous guarded protocol as
            # ordinary product PRs. The old path remains for legacy policies.
            need(current['provider'].get('autoMergeRequest') is None, 'guard_request_owner_unknown')
            checkpoint=prepare_record(api,document,grant,evaluator,run_id,current['receipt'])
            ready=prepared_module.build_guarded(checkpoint,guarded['policy'],guarded['intent'],pending,nonce,
                                               grant=grant,grant_source=document['source_commit'])
            print(prepared_module.MARKER+r.canonical(ready),flush=True)
            response=api.api(f'repos/{repo}/statuses/{head}',method='POST',data=dict(
                state='success',context=p['status_context'],description='Reviewed policy, current CI and Claude verified',
                target_url=target))
            per_pr=review.sibling('per_pr_checkpoint')
            bound=per_pr.build(checkpoint,guarded['policy'],guarded['intent'],response,target_url=target,
                               grant=grant,grant_source=document['source_commit'])
            print(per_pr.MARKER+r.canonical(bound),flush=True)
            fresh=_evaluate_guarded(api,grant,log_reader)
            need(grant_loader()==document and all(fresh[k]==before[k] for k in
                 ('context','issue','policy','receipt','ci_jobs'))
                 and fresh['provider'].get('autoMergeRequest') is None
                 and verify_grant_attestation(api,document,grant,evaluator,log_reader)==attestation,
                 'adoption_evidence_changed')
            publisher_provenance(api,grant,run_id,event)
            need(guard_action.recheck(api,document,grant,evaluator,run_id,guarded,log_reader)==attestation,
                 'adoption_guard_review_changed')
            per_pr.verify_status(api,repo,head,bound,guarded['intent'],grant=grant,
                                 grant_source=document['source_commit'])
            direct=review.sibling('direct_merge')
            outcome=direct.merge_http(repo,number,dict(sha=head,merge_method='squash',
                                                       commit_title=headline,commit_message=body))
            if outcome['state']=='refused':
                guard=review.sibling('merge_guard')
                record=direct.refusal_record(guarded['intent'],guarded['policy'],outcome['http_status'],guard,review)
                print(direct.REFUSAL_MARKER+r.canonical(record),flush=True)
                dispatch_adoption_recovery(api,repo)
                return dict(state='held_after_pending',head=head,reason='direct_merge_refused')
            merged=outcome['value']
            need(isinstance(merged,dict) and merged.get('merged') is True and review.sha(merged.get('sha')),
                 'direct_merge_result_unknown')
            close=review.sibling('guarded_close')
            observation=review.sibling('post_merge_observation')
            direct.finish_close(api,repo,grant['issue'],checkpoint,merged['sha'],guarded['policy'],
                                guarded['intent'],close,observation,review)
            return dict(state='closed',head=head,issue=grant['issue'],merge=merged['sha'])
        if current['provider'].get('autoMergeRequest') is None:
            history=review.sibling('request_history') if guarded is not None else None
            prior_history=history.stable(api,repo,number) if history is not None else None
            mutation_id=guarded['intent']['sha256'] if guarded is not None else r.digest(grant)
            result=native.graphql(api,native.QUEUE,dict(input=dict(
                pullRequestId=current['provider']['id'],expectedHeadOid=head,
                mergeMethod=p['merge_method'],clientMutationId=mutation_id,
                commitHeadline=headline,commitBody=body)))
            mutation=result.get('enablePullRequestAutoMerge',{})
            queued=mutation.get('pullRequest')
            need(isinstance(queued,dict) and queued.get('headRefOid')==head, 'queue_response')
            native.queued_request(queued,p,headline,body)
            if history is not None:
                observed=history.stable(api,repo,number)
                record=history.bind(prior_history,observed,queued,guarded['intent'],guarded['policy'],context['base'])
                ack=history.acknowledgement(mutation,record,guarded['intent'],guarded['policy'])
                print(history.MARKER+r.canonical(record),flush=True)
                print(history.ACK_MARKER+r.canonical(ack),flush=True)
        else:
            need(guarded is None,'guard_request_owner_unknown')
            native.queued_request(current['provider'],p,headline,body)
        fresh=_evaluate_guarded(api,grant,log_reader) if guarded is not None else evaluate(api,grant,log_reader)
        need(grant_loader()==document and all(fresh[k]==before[k] for k in ('context','issue','policy','receipt','ci_jobs'))
             and verify_grant_attestation(api,document,grant,evaluator,log_reader)==attestation,
             'adoption_evidence_changed')
        publisher_provenance(api,grant,run_id,event)
        native.queued_request(fresh['provider'],p,headline,body)
        checkpoint=(prepare_record(api,document,grant,evaluator,run_id,fresh['receipt'])
                    if prepare_record is not None else None)
        if guarded is not None:
            need(guard_action.recheck(api,document,grant,evaluator,run_id,guarded,log_reader)==attestation,
                 'adoption_guard_review_changed')
            prepared=prepared_module.build_guarded(checkpoint,guarded['policy'],guarded['intent'],pending,nonce,
                                                    grant=grant,grant_source=document['source_commit'])
            print(prepared_module.MARKER+r.canonical(prepared),flush=True)
        response=api.api(f'repos/{repo}/statuses/{head}',method='POST',data=dict(
            state='success',context=p['status_context'],description='Reviewed policy, current CI and Claude verified',
            target_url=target))
        if checkpoint is not None:
            if guarded is None:
                observation=review.sibling('post_merge_observation')
                print(observation.MARKER+r.canonical(checkpoint),flush=True)
            else:
                per_pr=review.sibling('per_pr_checkpoint')
                bound=per_pr.build(checkpoint,guarded['policy'],guarded['intent'],response,target_url=target,
                                   grant=grant,grant_source=document['source_commit'])
                print(per_pr.MARKER+r.canonical(bound),flush=True)
                dispatch_adoption_recovery(api,repo)
    except Exception:
        # The pending status blocks an abandoned request; no blind retry.
        if guarded is not None and direct_mode:
            try: dispatch_adoption_recovery(api,repo)
            except Exception: pass  # The periodic recovery workflow is still required.
        return dict(state='held_after_pending',head=head)
    after=native.provider_pull(api,repo,number)
    need(after.get('headRefOid')==head, 'head_changed_after_status')
    if after.get('state')=='MERGED':
        merge=after.get('mergeCommit',{}).get('oid')
        need(review.sha(merge), 'merge_identity_missing')
        return dict(state='merged',head=head,merge=merge)
    need(after.get('state')=='OPEN' and after.get('autoMergeRequest',{}).get('mergeMethod')==p['merge_method'],
         'native_request_unknown')
    return dict(state='queued',head=head)


def main():
    repo=os.environ.get('GITHUB_REPOSITORY','')
    evaluator=os.environ.get('AW_ACTION_REF','')
    run=os.environ.get('GITHUB_RUN_ID','')
    event=os.environ.get('GITHUB_EVENT_NAME','')
    need(review.sha(evaluator) and isinstance(repo,str)
         and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo)
         and re.fullmatch(r'[1-9][0-9]*',run), 'publisher_input')
    result=enqueue(ci,repo,evaluator,int(run),event,prepare_record=prepare_adoption_close_record,direct_mode=True)
    print(r.canonical(result),flush=True)
    return 0 if result['state'] in ('queued','merged','closed') else 2


if __name__=='__main__':
    import sys
    try:
        sys.exit(main())
    except (RuntimeError,KeyError,TypeError,ValueError,OSError) as error:
        print('refused: '+str(error),file=sys.stderr)
        sys.exit(2)
