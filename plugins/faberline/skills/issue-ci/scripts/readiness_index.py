"""Bounded authenticated producer-log index. Discovery data, not review authority."""
import copy
import importlib.util
from pathlib import Path
import re
from urllib.parse import quote

_spec = importlib.util.spec_from_file_location('index_readiness', Path(__file__).with_name('readiness.py'))
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)

MARKER = 'AW_REVIEW_INDEX_V1 '
MAX_PRS = 50
MAX_INDEX_BYTES = 262144
MAX_PUBLICATION_BYTES = r.MAX_LOG // 2
MAX_SUMMARY_BYTES = 65536


def policy_identity(document):
    r.need(r.HEX.fullmatch(document['source_commit'])
           and re.fullmatch(r'\.github/[\w.-]+\.json', document['path']),
           'index_policy', 'frozen default-branch policy source required')
    return dict(path=document['path'], source=document['source_commit'], sha256=r.digest(document['config']))


def pull_identity(repo, pull):
    number = pull.get('number')
    head, base = pull.get('head', {}), pull.get('base', {})
    r.need(pull.get('state') == 'open' and type(number) is int and number > 0
           and r.HEX.fullmatch(head.get('sha', '')) and r.HEX.fullmatch(base.get('sha', ''))
           and head.get('repo', {}).get('full_name') == repo
           and isinstance(head.get('ref'), str) and bool(head['ref'])
           and isinstance(base.get('ref'), str) and bool(base['ref'])
           and type(pull.get('draft')) is bool,
           'index_inventory', 'closed, foreign or incomplete PR identity; never omit unknown candidates')
    return dict(pr=number, head=head['sha'], base=base['sha'], head_ref=head['ref'], base_ref=base['ref'],
                head_repo=repo, draft=pull['draft'], description_sha256=r.digest([pull.get('title'), pull.get('body')]))


def inventory(ci, repo):
    """At most two bounded pages; never treat a capped/partial list as empty."""
    r.need(r.REPO.fullmatch(repo), 'index_identity', 'explicit repository required')
    items = []
    for page in (1, 2):
        batch = ci.api(f'repos/{repo}/pulls?state=open&sort=created&direction=asc&per_page={MAX_PRS}&page={page}')
        r.need(isinstance(batch, list) and len(batch) <= MAX_PRS,
               'index_inventory', 'invalid open PR page')
        items.extend(pull_identity(repo, value) for value in batch)
        r.need(len(items) <= MAX_PRS and len({v['pr'] for v in items}) == len(items),
               'index_limit', 'PR cap or duplicate pagination; incomplete inventory cannot qualify')
        if len(batch) < MAX_PRS:
            return sorted(items, key=lambda value: value['pr'])
    raise r.Refused('index_limit', 'no authoritative end of PR inventory')


def mechanical_key(receipt):
    inputs = r.validate_receipt(receipt)['inputs']
    return r.digest(['aw-review-mechanical-v1', inputs['repo'], inputs['pr'], inputs['head'], inputs['base'],
                     inputs['policy_sha256'], inputs['evaluator_sha'], inputs['pr_description_sha256']])


def review_work_key(mechanical, issue_contract_sha256):
    """Join external semantic context. Not a completed-review ledger or lease."""
    r.need(all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
               for value in (mechanical, issue_contract_sha256)),
           'review_context', 'verified mechanical key and external issue/slice digest required')
    return r.digest(['aw-review-work-v1', mechanical, issue_contract_sha256])


def scope_record(config):
    return dict(workflows=config['workflows'], context=config['_qualification_context'])


def fresh_entries(ci, repo, document, evaluator, producer, coverage, entries):
    """Metadata-only cache invalidation; assertions stay in authenticated receipts."""
    r.need(len(entries) == len(coverage), 'index_coverage', 'one entry per open PR required')
    configs = []
    for identity, entry in zip(coverage, entries):
        r.need(set(entry) == {'pr', 'receipt', 'scope', 'mechanical_review_key'} and entry['pr'] == identity['pr'],
               'index_coverage', 'entries must exactly match the sorted complete inventory')
        receipt = entry['receipt']; payload = r.validate_receipt(receipt); inputs = payload['inputs']
        r.need(inputs['repo'] == repo and inputs['pr'] == identity['pr']
               and inputs['head'] == identity['head'] and inputs['base'] == identity['base']
               and inputs['pr_description_sha256'] == identity['description_sha256']
               and inputs['evaluator_sha'] == evaluator and payload['producer'] == producer
               and inputs['policy_path'] == document['path']
               and inputs['policy_source'] == document['source_commit']
               and inputs['policy_sha256'] == r.digest(document['config'])
               and entry['mechanical_review_key'] == mechanical_key(receipt),
               'index_identity', 'receipt does not bind current queue/policy/producer/evaluator')
        pull = ci.api(f'repos/{repo}/pulls/{identity["pr"]}')
        r.need(pull_identity(repo, pull) == identity, 'stale_index', 'PR identity moved')
        files = ci.pages(f'repos/{repo}/pulls/{identity["pr"]}/files')
        r.need(r.changed_inventory(files) == inputs['changed_files']
               and r.digest(r.changed_inventory(files)) == inputs['changed_inventory_sha256'],
               'stale_index', 'changed-file inventory moved')
        scoped = ci.qualify_config(repo, pull, document['config'], files=files)
        r.need(scope_record(scoped) == entry['scope'], 'stale_index', 'applicability/definition moved')
        r.need(r.digest(ci.snapshot(repo, identity['head'], scoped)) == payload['observation_sha256'],
               'stale_index', 'latest required run/attempt/job metadata moved')
        configs.append((identity, scoped, payload['observation_sha256']))
    # Collection across several PRs can outlast a rerun. Recheck without rereading logs.
    for identity, scoped, expected in configs:
        r.need(r.digest(ci.snapshot(repo, identity['head'], scoped)) == expected,
               'stale_index', 'required evidence moved during index verification')


def publication_size(index):
    payload = index['payload']
    size = len((MARKER + r.canonical(index) + '\n').encode())
    size += sum(len((r.RECEIPT_MARKER + r.canonical(entry['receipt']) + '\n').encode())
                for entry in payload['entries'])
    r.need(len(r.canonical(index).encode()) <= MAX_INDEX_BYTES and size <= MAX_PUBLICATION_BYTES,
           'index_limit', 'index/receipt publication exceeds limits; never truncate coverage')


def produce(ci, repo, load_document, evaluator, run_id):
    r.need(r.HEX.fullmatch(evaluator), 'index_identity', 'immutable evaluator SHA required')
    document = copy.deepcopy(load_document())
    r.validate_contract(ci, document['config'])
    policy = policy_identity(document)
    producer = r.producer_identity(ci, repo, document['config'], run_id)
    pins = r.pin_proof(ci, repo, None, document['config'], producer['head'], evaluator, producer_only=True)
    coverage = inventory(ci, repo)
    entries = []
    for identity in coverage:
        receipt = r.produce(ci, repo, identity['pr'], identity['head'], lambda: copy.deepcopy(document), evaluator, run_id)
        pull = ci.api(f'repos/{repo}/pulls/{identity["pr"]}')
        r.need(pull_identity(repo, pull) == identity, 'stale_index', 'queue moved during production')
        scoped = ci.qualify_config(repo, pull, document['config'])
        entries.append(dict(pr=identity['pr'], receipt=receipt, scope=scope_record(scoped),
                            mechanical_review_key=mechanical_key(receipt)))
    fresh_entries(ci, repo, document, evaluator, producer, coverage, entries)
    r.need(inventory(ci, repo) == coverage and load_document() == document
           and r.producer_identity(ci, repo, document['config'], run_id) == producer,
           'stale_index', 'queue/policy/producer moved before publication')
    payload = dict(version=1, kind='review-readiness-index', repo=repo, evaluator_sha=evaluator,
                   policy=policy, producer=producer, producer_pins=pins,
                   coverage=dict(complete=True, count=len(coverage), prs=coverage, sha256=r.digest(coverage)),
                   entries=entries)
    result = dict(payload=payload, sha256=r.digest(payload))
    publication_size(result)
    return result


def latest_producer(ci, repo, config):
    spec = config['review_readiness']['producer']
    branch = ci.api(f'repos/{repo}')['default_branch']
    path = f'repos/{repo}/actions/workflows/{quote(spec["path"].split("/")[-1], safe="")}/runs?event=workflow_run&branch={quote(branch, safe="")}'
    # Only the latest page is needed: GitHub orders workflow runs newest first.
    data = ci.api(path + '&per_page=100&page=1')
    r.need(isinstance(data, dict) and isinstance(data.get('workflow_runs'), list)
           and 0 < len(data['workflow_runs']) <= 100, 'index_missing', 'no authoritative producer run; not an empty queue')
    runs = data['workflow_runs']
    for run in runs:
        r.need(type(run.get('id')) is int and run['id'] > 0
               and type(run.get('run_number')) is int and run['run_number'] > 0
               and type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
               'index_producer', 'unknown producer metadata; do not fall back to older success')
    latest = max(runs, key=lambda value: (value['run_number'], value['run_attempt']))
    return r.producer_identity(ci, repo, config, latest['id'], completed=True)


def validate(index):
    r.need(isinstance(index, dict) and set(index) == {'payload', 'sha256'}, 'index_schema', 'index envelope required')
    payload = index['payload']
    r.need(isinstance(payload, dict) and set(payload) == {'version', 'kind', 'repo', 'evaluator_sha', 'policy',
               'producer', 'producer_pins', 'coverage', 'entries'} and type(payload['version']) is int
           and payload['version'] == 1 and payload['kind'] == 'review-readiness-index'
           and index['sha256'] == r.digest(payload), 'index_schema', 'version/fields/digest mismatch')
    r.need(isinstance(payload['entries'], list) and len(payload['entries']) <= MAX_PRS
           and isinstance(payload['coverage'], dict)
           and set(payload['coverage']) == {'complete', 'count', 'prs', 'sha256'}
           and payload['coverage']['complete'] is True
           and isinstance(payload['coverage']['prs'], list)
           and type(payload['coverage']['count']) is int
           and payload['coverage']['count'] == len(payload['entries']) == len(payload['coverage']['prs'])
           and payload['coverage']['sha256'] == r.digest(payload['coverage']['prs']),
           'index_coverage', 'incomplete/ambiguous coverage is not no_ready')
    r.need(all(isinstance(entry, dict) and type(entry.get('pr')) is int and entry['pr'] > 0
               and set(entry) == {'pr', 'receipt', 'scope', 'mechanical_review_key'}
               for entry in payload['entries']), 'index_schema', 'invalid index entry fields')
    r.need([entry['pr'] for entry in payload['entries']] == sorted({entry['pr'] for entry in payload['entries']}),
           'index_coverage', 'entries must be unique and sorted')
    publication_size(index)
    return payload


def verify(ci, index, repo, load_document, evaluator):
    r.need(r.REPO.fullmatch(repo) and r.HEX.fullmatch(evaluator), 'index_identity', 'caller-selected repo and immutable evaluator required')
    payload = validate(index)
    document = copy.deepcopy(load_document())
    r.validate_contract(ci, document['config'])
    producer = latest_producer(ci, repo, document['config'])
    r.need(payload['repo'] == repo and payload['evaluator_sha'] == evaluator
           and payload['policy'] == policy_identity(document) and payload['producer'] == producer,
           'stale_index', 'index policy/evaluator/producer binding moved')
    pins = r.pin_proof(ci, repo, None, document['config'], producer['head'], evaluator, producer_only=True)
    r.need(pins == payload['producer_pins'], 'index_producer', 'producer source/pins must authenticate even an empty queue')
    log = r.job_log(repo, producer['job_id'])
    indexes = r.records(log, MARKER)
    receipts = r.records(log, r.RECEIPT_MARKER)
    r.need(len(indexes) == 1 and indexes[0] == index
           and receipts == [entry['receipt'] for entry in payload['entries']],
           'index_unpublished', 'exact index and all receipts must originate in the authenticated successful producer job')
    coverage = inventory(ci, repo)
    r.need(coverage == payload['coverage']['prs'], 'stale_index', 'open PR inventory changed')
    fresh_entries(ci, repo, document, evaluator, producer, coverage, payload['entries'])
    r.need(inventory(ci, repo) == coverage and load_document() == document
           and latest_producer(ci, repo, document['config']) == producer,
           'stale_index', 'queue/policy/latest producer changed during verification')
    return index


def discover(ci, repo, load_document, evaluator):
    r.need(r.REPO.fullmatch(repo) and r.HEX.fullmatch(evaluator), 'index_identity', 'caller-selected repo and immutable evaluator required')
    document = load_document()
    r.validate_contract(ci, document['config'])
    producer = latest_producer(ci, repo, document['config'])
    records = r.records(r.job_log(repo, producer['job_id']), MARKER)
    r.need(len(records) == 1, 'index_missing', 'one configured producer index required; no artifact or URL fallback')
    return verify(ci, records[0], repo, load_document, evaluator)


def named_receipt(ci, repo, number, head, receipt_sha256, load_document, evaluator):
    r.need(type(number) is int and number > 0 and r.HEX.fullmatch(head)
           and re.fullmatch('[0-9a-f]{64}', receipt_sha256), 'index_identity', 'exact named candidate/digest required')
    verified = discover(ci, repo, load_document, evaluator)
    matches = [entry for entry in verified['payload']['entries'] if entry['pr'] == number
               and entry['receipt']['payload']['inputs']['head'] == head
               and entry['receipt']['sha256'] == receipt_sha256]
    r.need(len(matches) == 1, 'stale_index', 'named receipt moved since preflight')
    # Reuse the original full named verifier before code review/publication.
    return r.verify(ci, matches[0]['receipt'], repo, number, head, load_document, evaluator)


def summary(index):
    payload = validate(index)
    ready, counts, codes = [], dict(ready=0, wait=0, fail=0), set()
    ordinary = {'gate_failed', 'missing_run', 'missing_job', 'run_pending', 'job_pending', 'run_not_success', 'job_not_success'}
    blockers = set()
    for entry in payload['entries']:
        receipt = entry['receipt']; result = r.validate_receipt(receipt)
        counts[result['status']] += 1
        reason_codes = {reason['code'] for reason in result['reasons']}
        codes.update(reason_codes); blockers.update(reason_codes - ordinary)
        if result['status'] == 'ready':
            inputs = result['inputs']
            links = sorted({row['url'] for row in result['evidence']})
            ready.append(dict(pr=entry['pr'], head=inputs['head'], base=inputs['base'],
                              receipt_sha256=receipt['sha256'], mechanical_review_key=entry['mechanical_review_key'],
                              links=links[:8], link_count=len(links)))
    result = dict(version=1, kind='verified-review-index', repo=payload['repo'],
                  status='ready' if ready else 'blocked' if blockers else 'no_ready',
                  complete=True, index_sha256=index['sha256'], producer=payload['producer'],
                  policy=payload['policy'], evaluator_sha=payload['evaluator_sha'],
                  counts=counts, reason_codes=sorted(codes), blockers=sorted(blockers), ready=ready)
    r.need(len(r.canonical(result).encode()) <= MAX_SUMMARY_BYTES, 'index_limit', 'summary exceeds output bound')
    return result


def emit(index):
    validate(index)
    for entry in index['payload']['entries']:
        print(r.RECEIPT_MARKER + r.canonical(entry['receipt']))
    print(MARKER + r.canonical(index))
