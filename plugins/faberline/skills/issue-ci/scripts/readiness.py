"""Read-only review prerequisites. Hashes are integrity, never authentication."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

_json_spec = importlib.util.spec_from_file_location('issue_ci_json_data', Path(__file__).with_name('json_data.py'))
_json_data = importlib.util.module_from_spec(_json_spec)
_json_spec.loader.exec_module(_json_data)
strict_json_loads = _json_data.loads


VERSION = 1
ASSERTION_MARKER = 'AW_ASSERTIONS_V1 '
RECEIPT_MARKER = 'AW_REVIEW_READINESS_V1 '
HEX = re.compile(r'[0-9a-f]{40}')
REPO = re.compile(r'[\w.-]+/[\w.-]+')
MAX_LOG = 8 * 1024 * 1024


class Refused(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(code + ': ' + detail)
        self.code = code


def need(ok, code, detail):
    if not ok:
        raise Refused(code, detail)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def job_log(repo, job_id):
    # Only GitHub's authenticated job-log endpoint. Never artifacts or PR paths.
    raw = subprocess.check_output(['gh', 'api', f'repos/{repo}/actions/jobs/{job_id}/logs'])
    need(len(raw) <= MAX_LOG, 'log_limit', 'refuse oversized evidence log')
    return raw.decode('utf-8', errors='strict')


def records(text, marker):
    need(len(text.encode()) <= MAX_LOG, 'log_limit', 'refuse oversized evidence log')
    found = []
    for line in text.splitlines():
        # GitHub prepends an RFC3339 timestamp. Echoed shell source is not a record.
        line = re.sub(r'^\d{4}-\d\d-\d\dT\S+\s+', '', line)
        if line.startswith(marker):
            try:
                found.append(strict_json_loads(line[len(marker):]))
            except (ValueError, TypeError):
                raise Refused('malformed_record', 'invalid JSON evidence record')
    return found


def validate_contract(ci, config):
    need('qualification' in config, 'missing_contract', 'readiness requires qualification v1')
    ci.validate_config(config)
    contract = config.get('review_readiness')
    need(isinstance(contract, dict) and type(contract.get('version')) is int
         and contract['version'] == VERSION, 'missing_contract', 'review_readiness.version must be 1')
    need(set(contract) == {'version', 'producer', 'sources', 'assertions'},
         'invalid_contract', 'unknown or missing review_readiness fields')
    producer = contract['producer']
    need(isinstance(producer, dict) and set(producer) == {'path', 'id', 'definition_sha', 'job'}
         and re.fullmatch(r'\.github/workflows/[\w.-]+\.ya?ml', producer.get('path', ''))
         and type(producer.get('id')) is int and producer['id'] > 0
         and HEX.fullmatch(producer.get('definition_sha', ''))
         and isinstance(producer.get('job'), str) and producer['job'].strip(),
         'invalid_producer', 'declare one exact trusted default-branch producer/job')
    need(producer['path'] not in {w['path'] for w in config['workflows']},
         'producer_cycle', 'producer cannot be its own prerequisite or wake source')
    sources = contract['sources']
    need(isinstance(sources, dict) and bool(sources), 'missing_pins', 'declare transitive sources')
    for key, source in sources.items():
        need(isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', key)
             and isinstance(source, dict) and set(source) == {'repo', 'ref', 'path', 'blob_sha'}
             and REPO.fullmatch(source.get('repo', ''))
             and HEX.fullmatch(source.get('ref', ''))
             and ci.qualification_module().path_valid(source.get('path'))
             and HEX.fullmatch(source.get('blob_sha', '')),
             'invalid_pin', 'every transitive source needs repo/path/immutable ref/blob')
    identities = [(c['workflow'], c['job']) for c in config['qualification']['checks'].values()]
    need(len(set(identities)) == len(identities), 'ambiguous_check', 'each check needs a distinct job identity')
    assertions = contract['assertions']
    need(isinstance(assertions, dict) and bool(assertions), 'missing_assertion_contract', 'declare nonzero gate assertions')
    for key, rule in assertions.items():
        need(key in config['qualification']['checks'] and isinstance(rule, dict)
             and set(rule) == {'names', 'enforcers'}
             and isinstance(rule['names'], list) and bool(rule['names'])
             and all(isinstance(n, str) and n.strip() for n in rule['names'])
             and len(set(rule['names'])) == len(rule['names'])
             and isinstance(rule['enforcers'], list) and bool(rule['enforcers'])
             and all(n in sources for n in rule['enforcers']),
             'invalid_assertion_contract', 'nonempty exact assertion roster and trusted enforcers required')
    return contract


def source_bytes(ci, repo, path, ref, expected):
    value = ci.api(f'repos/{repo}/contents/{quote(path, safe="/")}?ref={ref}')
    need(value.get('type') == 'file' and value.get('encoding') == 'base64'
         and value.get('sha') == expected, 'untrusted_source', f'unreviewed source {repo}/{path}@{ref}')
    try:
        raw = base64.b64decode(''.join(value['content'].split()), validate=True)
    except (ValueError, KeyError, TypeError):
        raise Refused('untrusted_source', 'invalid GitHub source encoding')
    need(len(raw) <= 262144 and hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == expected,
         'untrusted_source', 'source bytes must match the reviewed Git blob')
    return raw.decode('utf-8')


def literal_uses(text):
    """Parse YAML nodes before restricting syntax; no YAML object constructors."""
    try:
        import yaml
    except ImportError:
        raise Refused('parser_dependency', 'PyYAML==6.0.3 is required; use the hash-locked runtime')
    need(yaml.__version__ == '6.0.3', 'parser_dependency', 'require the reviewed PyYAML==6.0.3 parser')
    need(len(text.encode()) <= 262144, 'source_limit', 'YAML source exceeds limit')
    try:
        for index, token in enumerate(yaml.scan(text, Loader=yaml.BaseLoader)):
            need(index < 20000, 'source_limit', 'YAML token count exceeds limit')
            need(not isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken,
                                       yaml.tokens.TagToken, yaml.tokens.DirectiveToken)),
                 'unsupported_source_syntax', 'aliases, anchors, tags and directives are unsupported')
            if isinstance(token, yaml.tokens.KeyToken):
                need(text[token.start_mark.index:token.end_mark.index] != '?',
                     'unsupported_source_syntax', 'explicit mapping keys are unsupported in every context')
            if isinstance(token, yaml.tokens.ScalarToken) and token.style in ('"', "'"):
                need(token.start_mark.line == token.end_mark.line,
                     'unsupported_source_syntax', 'multiline quoted scalars/keys are unsupported')
        root = yaml.compose(text, Loader=yaml.BaseLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise Refused('unsupported_source_syntax', 'invalid or excessively nested YAML') from exc
    need(isinstance(root, yaml.nodes.MappingNode), 'unsupported_source_syntax', 'YAML root must be one mapping')
    refs = []
    visited = 0

    def visit(node, depth):
        nonlocal visited
        visited += 1
        need(depth <= 64 and visited <= 20000, 'source_limit', 'YAML node/depth limit exceeded')
        if isinstance(node, yaml.nodes.MappingNode):
            keys = set()
            for key, value in node.value:
                need(isinstance(key, yaml.nodes.ScalarNode),
                     'unsupported_source_syntax', 'complex mapping keys are unsupported')
                raw_key = text[key.start_mark.index:key.end_mark.index]
                need(key.start_mark.line == key.end_mark.line and '\\' not in raw_key
                     and key.value != '<<' and '${{' not in key.value and key.value not in keys,
                     'unsupported_source_syntax', 'multiline/escaped/dynamic/merged/duplicate keys are unsupported')
                keys.add(key.value)
                if key.value == 'uses':
                    need(isinstance(value, yaml.nodes.ScalarNode) and value.style in (None, '"', "'")
                         and key.start_mark.line == value.start_mark.line == value.end_mark.line,
                         'unsupported_source_syntax', 'uses needs a same-line literal scalar')
                    ref = value.value
                    need(bool(ref) and not re.search(r'[\s${}\[\]"\']', ref),
                         'mutable_dependency', 'dynamic or nonliteral uses is not pinned')
                    need(ref.startswith('./')
                         or re.fullmatch(r'[\w.-]+/[\w.-]+(?:/[^@]+)?@[0-9a-f]{40}', ref)
                         or re.fullmatch(r'docker://[^\s@]+@sha256:[0-9a-f]{64}', ref),
                         'mutable_dependency', 'uses requires a local action or immutable commit/digest')
                    refs.append(ref)
                visit(value, depth + 1)
        elif isinstance(node, yaml.nodes.SequenceNode):
            for value in node.value:
                visit(value, depth + 1)
        else:
            need(isinstance(node, yaml.nodes.ScalarNode), 'unsupported_source_syntax', 'unsupported YAML node')

    visit(root, 0)
    return refs


def pin_proof(ci, repo, pr, config, producer_ref, evaluator_sha):
    contract = config['review_readiness']
    manifest = contract['sources']
    proof, visited, edges = {}, set(), {}
    protected = config['qualification']['protected_paths']

    def visit(source_repo, path, ref, blob):
        key = (source_repo, path, ref)
        if key in visited:
            return
        need(len(visited) < 64, 'pin_limit', 'transitive source closure exceeds limit')
        visited.add(key)
        edges[key] = set()
        text = source_bytes(ci, source_repo, path, ref, blob)
        proof['|'.join(key)] = dict(repo=source_repo, path=path, ref=ref, blob_sha=blob)
        if not path.endswith(('.yml', '.yaml')):
            return  # Immutable executable/enforcer file; not action YAML.
        for use in literal_uses(text):
            if use.startswith('docker://'):
                need(re.fullmatch(r'docker://[^\s@]+@sha256:[0-9a-f]{64}', use),
                     'mutable_dependency', 'Docker action must use an immutable digest')
                proof['docker|' + use] = dict(docker=use)
                continue
            if use.startswith('./'):
                need(source_repo == repo, 'unsupported_local_dependency',
                     'external action/workflow local uses has ambiguous checkout context; refuse')
                subrepo, subref, relative = source_repo, ref, use[2:]
                need(ci.qualification_module().path_valid(relative), 'invalid_pin', 'unsafe local action path')
                if subrepo == repo:
                    need(any(ci.qualification_module().matches(relative + '/action.yml', p)
                             or ci.qualification_module().matches(relative + '/action.yaml', p) for p in protected),
                         'unprotected_dependency', 'local action root must be protected')
                choices = [s for s in manifest.values() if s['repo'] == subrepo
                           and s['path'] in (relative + '/action.yml', relative + '/action.yaml')
                           and (s['ref'] == subref or subrepo == repo)]
            else:
                m = re.fullmatch(r'([\w.-]+/[\w.-]+)(/[^@]+)?@([0-9a-f]{40})', use)
                need(m is not None, 'mutable_dependency', 'every remote action/workflow must use a full commit SHA')
                subrepo, relative, subref = m[1], (m[2] or '').lstrip('/'), m[3]
                candidates = [relative] if relative.endswith(('.yml', '.yaml')) else [
                    (relative + '/' if relative else '') + 'action.yml',
                    (relative + '/' if relative else '') + 'action.yaml']
                choices = [s for s in manifest.values() if s['repo'] == subrepo
                           and s['ref'] == subref and s['path'] in candidates]
            need(len(choices) == 1, 'missing_pin', 'declare exactly one reviewed transitive source for ' + use)
            child = choices[0]
            edges[key].add((subrepo, child['path'], subref))
            visit(subrepo, child['path'], subref, child['blob_sha'])

    for workflow in config['workflows']:
        blob = config['qualification']['trusted_workflows'][workflow['path']]['definition_sha']
        for ref in (pr['head']['sha'], pr['base']['sha']):
            visit(repo, workflow['path'], ref, blob)
    producer = contract['producer']
    visit(repo, producer['path'], producer_ref, producer['definition_sha'])
    reachable, pending = set(), [(repo, producer['path'], producer_ref)]
    while pending:
        node = pending.pop()
        if node not in reachable:
            reachable.add(node)
            pending.extend(edges.get(node, ()))
    need(('faberline/aw', '.github/actions/issue-ci/action.yml', evaluator_sha) in reachable,
         'evaluator_binding', 'producer must transitively invoke the exact pinned aw evaluator')
    for check in config['_qualification_context']['selected']:
        need(check in contract['assertions'], 'missing_assertion_contract', 'no reviewed assertion contract for ' + check)
        for key in contract['assertions'][check]['enforcers']:
            source = manifest[key]
            visit(source['repo'], source['path'], source['ref'], source['blob_sha'])
            if source['repo'] == repo:
                need(any(ci.qualification_module().matches(source['path'], p) for p in protected),
                     'unprotected_dependency', 'local gate enforcer must be protected')
                for ref in (pr['head']['sha'], pr['base']['sha']):
                    visit(repo, source['path'], ref, source['blob_sha'])
    return [proof[key] for key in sorted(proof)]


def producer_identity(ci, repo, config, run_id, completed=False):
    spec = config['review_readiness']['producer']
    meta = ci.api(f'repos/{repo}')
    run = ci.api(f'repos/{repo}/actions/runs/{run_id}')
    need(type(run_id) is int and run_id > 0 and run.get('id') == run_id
         and run.get('workflow_id') == spec['id'] and run.get('path') == spec['path']
         and run.get('event') == 'workflow_run'
         and run.get('head_repository', {}).get('full_name') == repo
         and run.get('head_branch') == meta['default_branch']
         and HEX.fullmatch(run.get('head_sha', ''))
         and type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
         'untrusted_producer', 'receipt must come from the configured default-branch workflow_run producer')
    if completed:
        need(run.get('status') == 'completed' and run.get('conclusion') == 'success',
             'producer_not_success', 'producer run must finish successfully before consumption')
    jobs = ci.pages(f'repos/{repo}/actions/runs/{run_id}/attempts/{run["run_attempt"]}/jobs', 'jobs')
    matching = [j for j in jobs if j.get('name') == spec['job']]
    need(len(matching) == 1 and type(matching[0].get('id')) is int,
         'untrusted_producer', 'one exact producer job is required')
    job = matching[0]
    if completed:
        need(job.get('status') == 'completed' and job.get('conclusion') == 'success',
             'producer_not_success', 'producer job must finish successfully')
    return dict(repo=repo, workflow_id=spec['id'], path=spec['path'], definition_sha=spec['definition_sha'],
                run_id=run_id, run_attempt=run['run_attempt'], head=run['head_sha'],
                job_id=job['id'], job=spec['job'], event='workflow_run', url=run['html_url'])


def assertion_evidence(repo, pr, run, job, check, rule):
    found = records(job_log(repo, job['id']), ASSERTION_MARKER)
    need(len(found) == 1, 'missing_assertions', 'exactly one trusted assertion record required for ' + check)
    record = found[0]
    expected = dict(version=VERSION, repo=repo, pr=pr['number'], head=pr['head']['sha'], base=pr['base']['sha'],
                    run_id=run['id'], run_attempt=run['run_attempt'], job_id=job['id'], check=check)
    need(isinstance(record, dict) and set(record) == set(expected) | {'assertions'}
         and all(type(record.get(k)) is type(v) and record[k] == v for k, v in expected.items()),
         'untrusted_assertions', 'assertion identity does not bind this exact execution')
    assertions = record['assertions']
    need(isinstance(assertions, dict) and bool(assertions) and set(assertions) == set(rule['names'])
         and all(v == 'passed' for v in assertions.values()),
         'zero_or_failed_assertions', 'all declared nonzero assertions must actually pass')
    return dict(check=check, count=len(assertions), assertions=assertions, record_sha256=digest(record))


def collect_once(ci, repo, number, head, document, evaluator_sha, producer_run_id, completed=False):
    need(REPO.fullmatch(repo) and type(number) is int and number > 0 and HEX.fullmatch(head)
         and HEX.fullmatch(evaluator_sha), 'invalid_identity', 'exact repo/PR/head/evaluator SHA required')
    config = document['config']
    validate_contract(ci, config)
    need(HEX.fullmatch(document['source_commit']) and re.fullmatch(r'\.github/[\w.-]+\.json', document['path']),
         'untrusted_policy', 'frozen default-branch policy identity required')
    pull = ci.api(f'repos/{repo}/pulls/{number}')
    need(pull.get('number') == number and pull.get('state') == 'open' and pull.get('head', {}).get('sha') == head
         and pull['head'].get('repo', {}).get('full_name') == repo,
         'stale_pr', 'closed, stale or foreign PR')
    files = ci.pages(f'repos/{repo}/pulls/{number}/files')
    q = ci.qualification_module()
    plan = q.select(config, pull, files)
    definitions = {(w['path'], ref): ci.api(f'repos/{repo}/contents/{w["path"]}?ref={ref}')
                   for w in plan['workflows'] for ref in (head, plan['base']) if HEX.fullmatch(ref or '')}
    scoped = q.scoped_config(config, pull, files, definitions)
    producer = producer_identity(ci, repo, config, producer_run_id, completed)
    inventory = sorted([{key: file[key] for key in ('filename', 'status', 'previous_filename', 'sha',
                        'additions', 'deletions', 'changes') if key in file} for file in files], key=canonical)
    inputs = dict(repo=repo, pr=number, head=head, base=plan['base'], changed_files=inventory,
                  changed_inventory_sha256=digest(inventory), policy_path=document['path'],
                  policy_source=document['source_commit'], policy_sha256=digest(config), evaluator_sha=evaluator_sha,
                  selected=plan['selected'], exemptions=plan['exemptions'],
                  pr_description_sha256=digest([pull.get('title'), pull.get('body')]),
                  required_checks={key: dict(config['qualification']['checks'][key],
                      assertion_names=config['review_readiness']['assertions'].get(key, {}).get('names', []))
                      for key in plan['selected']})
    reasons, evidence, assertions = [], [], []
    def reason(code, detail, links=None):
        reasons.append(dict(code=code, detail=detail, links=links or []))
    for issue in scoped['_qualification_context']['issues']:
        reason('invalid_applicability', issue)
    for blocker in config.get('qualification_blockers', []):
        reason('qualification_blocker', blocker)
    try:
        pins = pin_proof(ci, repo, pull, scoped, producer['head'], evaluator_sha)
    except Refused as exc:
        pins = []
        reason(exc.code, str(exc))
    diagnostics = {}
    snapshots = ci.snapshot(repo, head, scoped, diagnostics)
    for path, code in sorted(diagnostics.items()):
        reason(code, path)
    # Shared label-independent aggregate remains authoritative for gate results.
    aggregate = ci.evaluate_evidence(scoped['workflows'], snapshots)
    for detail in aggregate['failures']:
        reason('gate_failed', detail)
    waiting = False
    for workflow in scoped['workflows']:
        path = workflow['path']
        if path not in snapshots:
            reason('missing_run', path); waiting = True
            continue
        run, jobs = snapshots[path]
        identity = config['qualification']['trusted_workflows'][path]
        if (run.get('path') != path or run.get('head_sha') != head or run.get('event') != 'pull_request'
                or run.get('head_repository', {}).get('full_name') != repo
                or not q.trusted_run(run, scoped)):
            reason('untrusted_run', path); continue
        entry = dict(workflow=path, workflow_id=identity['id'], definition_sha=identity['definition_sha'],
                     run_id=run['id'], run_number=run['run_number'], run_attempt=run['run_attempt'],
                     status=run['status'], conclusion=run.get('conclusion'), url=run['html_url'], jobs=[])
        evidence.append(entry)
        if run['status'] in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
            reason('run_pending', path, [run['html_url']]); waiting = True
        elif run['status'] != 'completed' or run.get('conclusion') != 'success':
            reason('run_not_success', path, [run['html_url']])
        for name in workflow['jobs']:
            matches = [j for j in jobs if j.get('name') == name]
            if not matches:
                reason('missing_job', path + ' / ' + name, [run['html_url']]); waiting = True
                continue
            if len(matches) != 1:
                reason('ambiguous_job', path + ' / ' + name); continue
            job = matches[0]
            need(type(job.get('id')) is int and job['id'] > 0, 'invalid_job', 'job ID required')
            entry['jobs'].append(dict(id=job['id'], name=name, status=job['status'],
                                      conclusion=job.get('conclusion'), url=job['html_url']))
            if job['status'] in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
                reason('job_pending', path + ' / ' + name, [job['html_url']]); waiting = True
            elif job['status'] != 'completed' or job.get('conclusion') != 'success':
                reason('job_not_success', path + ' / ' + name, [job['html_url']])
            elif pins and run['status'] == 'completed' and run.get('conclusion') == 'success':
                for check in plan['selected']:
                    spec = config['qualification']['checks'][check]
                    if spec['workflow'] == path and spec['job'] == name:
                        try:
                            assertions.append(assertion_evidence(repo, pull, run, job, check,
                                              config['review_readiness']['assertions'][check]))
                        except Refused as exc:
                            reason(exc.code, str(exc), [job['html_url']])
    waiting_codes = {'missing_run', 'missing_job', 'run_pending', 'job_pending'}
    status = 'fail' if any(r['code'] not in waiting_codes for r in reasons) else 'wait' if waiting else 'ready'
    result = dict(version=VERSION, status=status, inputs=inputs, producer=producer,
                  transitive_pins=pins, evidence=evidence, observation_sha256=digest(snapshots),
                  assertions=assertions, reasons=reasons)
    return result


def produce(ci, repo, number, head, load_document, evaluator_sha, producer_run_id, completed=False):
    first_doc = load_document()
    first = collect_once(ci, repo, number, head, first_doc, evaluator_sha, producer_run_id, completed)
    second_doc = load_document()
    second = collect_once(ci, repo, number, head, second_doc, evaluator_sha, producer_run_id, completed)
    need(first == second and first_doc == second_doc, 'inputs_changed',
         'head/base/policy/source/selection/attempt/evidence changed during collection; retry')
    current_pr = ci.api(f'repos/{repo}/pulls/{number}')
    current_config = ci.qualify_config(repo, current_pr, second_doc['config'])
    need(digest(ci.snapshot(repo, head, current_config)) == second['observation_sha256']
         and producer_identity(ci, repo, second_doc['config'], producer_run_id, completed) == second['producer']
         and load_document() == second_doc, 'inputs_changed',
         'latest attempt, producer or policy moved after the second observation')
    final = ci.api(f'repos/{repo}/pulls/{number}')
    need(final.get('state') == 'open' and final['head']['sha'] == head
         and final['head'].get('repo', {}).get('full_name') == repo
         and final['base']['sha'] == second['inputs']['base']
         and digest([final.get('title'), final.get('body')]) == second['inputs']['pr_description_sha256'],
         'inputs_changed', 'PR moved after collection')
    receipt = dict(payload=second, sha256=digest(second))
    need(len(canonical(receipt).encode()) <= 262144, 'receipt_limit', 'receipt exceeds consumption limit')
    return receipt


def validate_receipt(receipt):
    need(isinstance(receipt, dict) and set(receipt) == {'payload', 'sha256'}, 'invalid_receipt', 'unknown receipt fields')
    payload = receipt['payload']
    need(isinstance(payload, dict) and set(payload) == {'version', 'status', 'inputs', 'producer',
         'transitive_pins', 'evidence', 'observation_sha256', 'assertions', 'reasons'} and type(payload.get('version')) is int
         and payload['version'] == VERSION and payload.get('status') in ('ready', 'wait', 'fail')
         and receipt['sha256'] == digest(payload), 'invalid_receipt', 'schema version or digest mismatch')
    return payload


def verify(ci, receipt, repo, number, head, load_document, evaluator_sha):
    payload = validate_receipt(receipt)
    need(payload['inputs']['repo'] == repo and payload['inputs']['pr'] == number
         and payload['inputs']['head'] == head and payload['inputs']['evaluator_sha'] == evaluator_sha,
         'receipt_identity', 'receipt is not for the caller-selected repo/PR/head/evaluator')
    run_id = payload['producer']['run_id']
    current = produce(ci, repo, number, head, load_document, evaluator_sha, run_id, completed=True)
    need(current == receipt, 'stale_receipt', 'receipt no longer matches current policy, producer and gate evidence')
    emitted = records(job_log(repo, payload['producer']['job_id']), RECEIPT_MARKER)
    # A producer may publish different PRs, but this PR must have one unambiguous receipt.
    matching = [value for value in emitted if isinstance(value, dict)
                and isinstance(value.get('payload'), dict)
                and isinstance(value['payload'].get('inputs'), dict)
                and value['payload']['inputs'].get('repo') == repo
                and value['payload']['inputs'].get('pr') == number]
    need(len(matching) == 1 and matching[0] == receipt, 'unpublished_receipt',
         'receipt must be emitted exactly once by its authenticated successful producer job')
    return current


def verify_file(ci, path, repo, number, head, load_document, evaluator_sha):
    raw = Path(path).read_bytes()
    need(len(raw) <= 262144, 'receipt_limit', 'receipt exceeds limit')
    return verify(ci, strict_json_loads(raw), repo, number, head, load_document, evaluator_sha)
