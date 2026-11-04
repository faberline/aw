"""Read authenticated test evidence for a planner-mapped, unregistered issue.

This is execution evidence only. It does not authorize a lifecycle transition,
review, or merge. All provider data must come from the trusted CI controller.
"""
import importlib.util
from pathlib import Path

MARKER = 'AW_PLANNED_EXECUTION_V1 '
PLANNED_EVENTS=('workflow_run','workflow_dispatch')

def load(path, name):
    spec = importlib.util.spec_from_file_location('planned_readiness_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_once(ci, repo, number, head, document, evaluator_sha, producer_run_id, completed=False, log_cache=None):
    readiness = load(Path(__file__).with_name('readiness.py'), 'readiness')
    planner = load(Path(__file__).with_name('test_plan.py'), 'test_plan')
    readiness.need(readiness.REPO.fullmatch(repo) and type(number) is int and number > 0
                   and readiness.HEX.fullmatch(head) and readiness.HEX.fullmatch(evaluator_sha),
                   'invalid_identity', 'exact repository, PR, head and evaluator required')
    config = document['config']
    contract = readiness.validate_contract(ci, config)
    readiness.need(document.get('path') == '.github/issue-ci-required-gates.json'
                   and readiness.HEX.fullmatch(document.get('source_commit', '')),
                   'untrusted_policy', 'exact trusted default-branch policy required')
    root = f'repos/{repo}'
    pull = ci.api(f'{root}/pulls/{number}')
    readiness.need(type(pull.get('number')) is int and pull['number'] == number and pull.get('state') == 'open'
                   and pull.get('head', {}).get('sha') == head
                   and all(pull.get(side, {}).get('repo', {}).get('full_name') == repo for side in ('head', 'base')),
                   'stale_pr', 'current same-repository PR required')
    base = pull.get('base', {}).get('sha')
    readiness.need(base == document['source_commit'], 'stale_base', 'policy source must equal current PR base')
    q = ci.qualification_module()
    number_issue = q.linked_issue(pull)
    readiness.need(type(number_issue) is int, 'issue_identity', 'one canonical issue is required')
    issue = ci.api(f'{root}/issues/{number_issue}')
    comments = ci.pages(f'{root}/issues/{number_issue}/comments')
    files = ci.pages(f'{root}/pulls/{number}/files')
    definitions = {(workflow['path'], ref): ci.api(f'{root}/contents/{workflow["path"]}?ref={ref}')
                   for workflow in config['workflows'] for ref in (head, base)}
    selection = planner.execute_planned_issue(config, repo, head, base, pull=pull, files=files,
                                               issue=issue, comments=comments, definitions=definitions)
    test_plan = selection['execution']['plan']
    scoped = q.scoped_config(config, pull, files, definitions, issue)
    scoped['_qualification_context']['selected'] = test_plan['selected']
    wanted = {}
    for name, check in test_plan['checks'].items():
        wanted.setdefault(check['workflow'], set()).add(check['job'])
    scoped['workflows'] = [dict(path=path, jobs=sorted(jobs)) for path, jobs in sorted(wanted.items())]
    producer = readiness.producer_identity(ci, repo, config, producer_run_id, completed,allowed_events=PLANNED_EVENTS)
    readiness.need(producer['head'] == document['source_commit'], 'untrusted_producer',
                   'producer must use the exact current policy source')
    pins = readiness.pin_proof(ci, repo, pull, scoped, producer['head'], evaluator_sha)
    diagnostics = {}
    snapshots = ci.snapshot(repo, head, scoped, diagnostics)
    readiness.need(not diagnostics, 'snapshot_diagnostics', 'CI inventory is incomplete or ambiguous')
    result = ci.evaluate_evidence(scoped['workflows'], snapshots)
    readiness.need(not result['failures'] and not result['pending'], 'gate_not_success', 'all selected jobs must pass')
    proofs = []
    for name in test_plan['selected']:
        check = test_plan['checks'][name]
        readiness.need(name in contract['assertions'] or name in contract.get('policy_outcomes',{}),
                       'missing_assertion_contract', 'reviewed assertion roster or policy outcome required')
        run, jobs = snapshots[check['workflow']]
        readiness.need(run.get('path') == check['workflow'] and run.get('head_sha') == head
                       and run.get('event') == 'pull_request'
                       and run.get('head_repository', {}).get('full_name') == repo
                       and q.trusted_run(run, scoped) and run.get('status') == 'completed'
                       and run.get('conclusion') == 'success', 'untrusted_run', 'trusted current run required')
        matches = [job for job in jobs if job.get('name') == check['job']]
        readiness.need(len(matches) == 1 and type(matches[0].get('id')) is int and matches[0]['id'] > 0
                       and matches[0].get('status') == 'completed' and matches[0].get('conclusion') == 'success',
                       'untrusted_job', 'one successful exact job required')
        if name in contract.get('policy_outcomes',{}):
            log=None
            if log_cache is not None:
                key=(repo,matches[0]['id'])
                if key not in log_cache:log_cache[key]=readiness.job_log(repo,matches[0]['id'])
                log=log_cache[key]
            proofs.append(readiness.policy_outcome_evidence(repo,config,name,run,matches[0],authenticated_log=log))
            continue
        job_rules = {key: contract['assertions'][key] for key, value in config['qualification']['checks'].items()
                     if value['workflow'] == check['workflow'] and value['job'] == check['job']
                     and key in contract['assertions']}
        log=None
        if log_cache is not None:
            key=(repo,matches[0]['id'])
            if key not in log_cache:log_cache[key]=readiness.job_log(repo,matches[0]['id'])
            log=log_cache[key]
        proofs.append(readiness.assertion_evidence(repo, pull, run, matches[0], name,
                                                   contract['assertions'][name], job_rules,authenticated_log=log))
    return dict(schema='AW_AUTHENTICATED_PLANNED_EXECUTION_V1', repo=repo, pr=number, head=head, base=base,
                policy_sha256=readiness.digest(config), selection=selection, producer=producer, pins=pins,
                assertions=proofs, observation_sha256=readiness.digest(snapshots),
                pr_text_sha256=readiness.digest([pull.get('title'), pull.get('body')]),
                files_sha256=readiness.digest(readiness.changed_inventory(files)),
                qualification='not_evaluated')


def collect(ci, repo, number, head, load_document, evaluator_sha, producer_run_id, completed=False):
    """Refuse if any authenticated input changes across two complete reads."""
    # Completed job IDs are immutable; metadata is still re-read each time.
    log_cache=getattr(ci,'_planned_job_log_cache',None)
    if log_cache is None:log_cache={}
    first_doc = load_document()
    first = collect_once(ci, repo, number, head, first_doc, evaluator_sha, producer_run_id, completed,log_cache)
    second_doc = load_document()
    second = collect_once(ci, repo, number, head, second_doc, evaluator_sha, producer_run_id, completed,log_cache)
    if first_doc != second_doc or first != second:
        raise RuntimeError('planned execution inputs changed during collection')
    readiness = load(Path(__file__).with_name('readiness.py'), 'readiness')
    mapping = load(Path(__file__).with_name('acceptance_plan.py'), 'acceptance_plan')
    root = f'repos/{repo}'
    pull = ci.api(f'{root}/pulls/{number}')
    if (pull.get('number') != number or type(pull.get('number')) is not int
            or pull.get('state') != 'open' or pull.get('draft') is not False or pull.get('head', {}).get('sha') != head
            or pull.get('base', {}).get('sha') != second['base']
            or any(pull.get(side, {}).get('repo', {}).get('full_name') != repo for side in ('head', 'base'))
            or readiness.digest([pull.get('title'), pull.get('body')]) != second['pr_text_sha256']):
        raise RuntimeError('PR changed after planned execution collection')
    q = ci.qualification_module()
    issue_number = q.linked_issue(pull)
    if issue_number != second['selection']['publication']['issue']:
        raise RuntimeError('linked issue changed after planned execution collection')
    issue = ci.api(f'{root}/issues/{issue_number}')
    comments = ci.pages(f'{root}/issues/{issue_number}/comments')
    settings = second_doc['config']['planner_acceptance_mapping']
    spec = mapping.current_spec(issue, comments, settings['trusted_actor_ids'])
    plan = second['selection']['execution']['plan']
    test_ids = [name for name in plan['selected'] if plan['checks'][name]['kind'] != 'policy']
    if mapping.published_mapping(repo, issue, spec, comments, settings['trusted_actor_ids'], test_ids) != second['selection']['publication']:
        raise RuntimeError('issue or mapping changed after planned execution collection')
    files = ci.pages(f'{root}/pulls/{number}/files')
    if readiness.digest(readiness.changed_inventory(files)) != second['files_sha256']:
        raise RuntimeError('changed-file inventory moved after planned execution collection')
    wanted = {}
    for name, check in plan['checks'].items():
        wanted.setdefault(check['workflow'], set()).add(check['job'])
    scoped = dict(qualification=second_doc['config']['qualification'],
                  workflows=[dict(path=path, jobs=sorted(jobs)) for path, jobs in sorted(wanted.items())],
                  _qualification_context=dict(pr=number, head=head, base=second['base'], untrusted_workflows=[]))
    diagnostics = {}
    if (readiness.digest(ci.snapshot(repo, head, scoped, diagnostics)) != second['observation_sha256']
            or diagnostics or readiness.producer_identity(ci, repo, second_doc['config'], producer_run_id, completed,
                                                          allowed_events=PLANNED_EVENTS) != second['producer']
            or load_document() != second_doc):
        raise RuntimeError('CI, producer or policy changed after planned execution collection')
    return second


def produce(ci, repo, number, head, load_document, evaluator_sha, producer_run_id):
    readiness = load(Path(__file__).with_name('readiness.py'), 'readiness')
    payload = collect(ci, repo, number, head, load_document, evaluator_sha, producer_run_id)
    receipt = dict(payload=payload, sha256=readiness.digest(payload))
    readiness.need(len(readiness.canonical(receipt).encode()) <= 262144,
                   'receipt_limit', 'planned execution receipt is too large')
    return receipt


def verify(ci, receipt, repo, number, head, load_document, evaluator_sha):
    """Authenticate the producer log and independently recheck current inputs."""
    readiness = load(Path(__file__).with_name('readiness.py'), 'readiness')
    readiness.need(isinstance(receipt, dict) and set(receipt) == {'payload', 'sha256'}
                   and isinstance(receipt['payload'], dict)
                   and readiness.digest(receipt['payload']) == receipt['sha256'],
                   'invalid_receipt', 'planned execution receipt digest differs')
    payload = receipt['payload']
    readiness.need(payload.get('schema') == 'AW_AUTHENTICATED_PLANNED_EXECUTION_V1'
                   and payload.get('qualification') == 'not_evaluated'
                   and payload.get('repo') == repo and payload.get('pr') == number and payload.get('head') == head,
                   'invalid_receipt', 'planned execution receipt target differs')
    producer = payload.get('producer')
    readiness.need(isinstance(producer, dict) and type(producer.get('run_id')) is int
                   and producer['run_id'] > 0 and type(producer.get('job_id')) is int and producer['job_id'] > 0,
                   'invalid_receipt', 'planned execution producer identity missing')
    document = load_document()
    actual = readiness.producer_identity(ci, repo, document['config'], producer['run_id'], completed=True,
                                         allowed_events=PLANNED_EVENTS)
    readiness.need(actual == producer, 'untrusted_producer', 'published producer differs')
    published = readiness.records(readiness.job_log(repo, producer['job_id']), MARKER)
    matches = [item for item in published if isinstance(item, dict)
               and isinstance(item.get('payload'), dict) and item['payload'].get('pr') == number
               and item['payload'].get('head') == head]
    readiness.need(len(matches) == 1 and matches[0] == receipt, 'unpublished_receipt',
                   'producer must publish this exact receipt once')
    current = collect(ci, repo, number, head, load_document, evaluator_sha, producer['run_id'], completed=True)
    readiness.need(current == payload and load_document() == document, 'inputs_changed',
                   'planned execution inputs changed after publication')
    return receipt
