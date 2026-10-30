"""Guarded synchronous merge and issue close, pending rollout verification.

This is called only by the authenticated default-branch writer. Unknown
provider outcomes retain the guard for recovery; they are never retried here.
"""
import ast
import json
import re
import secrets
import subprocess


REFUSAL_MARKER = 'AW_DIRECT_MERGE_REFUSAL_V1 '


def native_enabled_at(api, evaluator, review):
    """Check immutable native writer source without executing historical code."""
    raw = review.blob(api, 'faberline/aw', 'plugins/faberline/skills/issue-ci/scripts/native_merge.py', evaluator)
    try:
        tree = ast.parse(raw)
    except (SyntaxError, TypeError, ValueError):
        raise RuntimeError('direct_source_unknown') from None
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == 'sibling' and node.args and isinstance(node.args[0], ast.Constant)
               and node.args[0].value == 'direct_merge' for node in ast.walk(tree))


def merge_http(repo, number, data):
    """Return a definitive merge or a documented refusal; all else is unknown.

    A transport error or a malformed response cannot prove that GitHub did
    not accept the merge. Never log the response headers or credentials.
    """
    result = subprocess.run(['gh', 'api', '--include', '--method', 'PUT',
        f'repos/{repo}/pulls/{number}/merge', '--input', '-'],
        input=json.dumps(data), text=True, capture_output=True, timeout=45)
    match = re.fullmatch(r'HTTP/\d(?:\.\d)? (\d{3})[^\r\n]*\r?\n(?:[^\r\n]*\r?\n)*?\r?\n(.*)',
                         result.stdout, re.S)
    if not match:
        raise RuntimeError('direct_response_unknown')
    code = int(match.group(1))
    try:
        value = json.loads(match.group(2))
    except (TypeError, ValueError):
        raise RuntimeError('direct_response_unknown') from None
    if code in (405, 409) and result.returncode != 0 and isinstance(value, dict) \
            and isinstance(value.get('message'), str) and value['message']:
        return dict(state='refused', http_status=code)
    if code == 200 and result.returncode == 0 and isinstance(value, dict) and value.get('merged') is True:
        return dict(state='merged', value=value)
    raise RuntimeError('direct_response_unknown')


def refusal_record(intent, policy, status, guard, review):
    """Bind a confirmed provider refusal to the authenticated guard intent."""
    p = guard.validate(intent, policy)
    review.need(type(status) is int and status in (405, 409), 'direct_refusal_unknown')
    payload = dict(version=1, repo=p['repo'], pr=p['pr'], issue=p['issue'], head=p['head'],
                   owner=p['owner'], run=p['run'], attempt=p['attempt'],
                   intent_sha256=intent['sha256'], http_status=status)
    return dict(payload=payload, sha256=review.r.digest(payload))


def find_refusal_in_log(log, repo, run, attempt, intent, policy, guard, review):
    """Inspect one already-authenticated source log for the exact refusal."""
    p = guard.validate(intent, policy)
    expected = []
    for item in review.r.records(log, REFUSAL_MARKER):
        review.need(isinstance(item, dict) and set(item) == {'payload', 'sha256'}
                    and isinstance(item['payload'], dict), 'direct_refusal_unknown')
        row = item['payload']
        review.need(set(row) == {'version', 'repo', 'pr', 'issue', 'head', 'owner', 'run',
                                'attempt', 'intent_sha256', 'http_status'}
                    and type(row['version']) is int and row['version'] == 1
                    and row['repo'] == repo and type(row['run']) is int and row['run'] == run
                    and type(row['attempt']) is int and row['attempt'] == attempt
                    and all(type(row[k]) is int and 0 < row[k] < 2**53 for k in ('pr', 'issue'))
                    and isinstance(row['head'], str) and re.fullmatch(r'[0-9a-f]{40}', row['head'])
                    and isinstance(row['owner'], str) and re.fullmatch(r'[0-9a-f]{56}', row['owner'])
                    and isinstance(row['intent_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', row['intent_sha256'])
                    and type(row['http_status']) is int and row['http_status'] in (405, 409)
                    and item['sha256'] == review.r.digest(row), 'direct_refusal_unknown')
        if row['pr'] == p['pr'] and row['head'] == p['head'] and row['owner'] == p['owner']:
            review.need(item == refusal_record(intent, policy, row['http_status'], guard, review),
                        'direct_refusal_unknown')
            expected.append(item)
    review.need(len(expected) <= 1, 'direct_refusal_duplicate')
    return expected[0] if expected else None


def find_refusal(api, repo, document, evaluator, run, attempt, intent, log_reader, guard, review):
    """Authenticate the exact completed native writer and one refusal."""
    policy, log = guard.authenticate_native_log(api, repo, document, evaluator, run, attempt, log_reader)
    return find_refusal_in_log(log, repo, run, attempt, intent, policy, guard, review)


def finish_close(api, repo, issue, checkpoint, merge, issue_policy, intent, guard, observation, review):
    """Close after an observed merge while retaining the exact owned guard."""
    need = review.need
    current = guard.worker.read_policy(api, repo)
    need(current is not None, 'merge_guard_policy_changed')
    guard.worker.config(current)
    guard.worker.check_runtime(current)
    need(all(current.get(k) == issue_policy.get(k)
             for k in ('version', 'enabled', 'genesis', 'ruleset_id', 'runtime')),
         'merge_guard_policy_changed')
    check = checkpoint['payload']['checkpoint']
    observed = observation.observe(api, check)
    need(observed['merge'] == merge, 'direct_merge_result_changed')
    if observed['state'] == 'observed_merged':
        events = api.pages(f'repos/{repo}/issues/{issue}/timeline')
        observation.ownership_history(events, '1970-01-01T00:00:00Z', check['ownership_events'])
        observation.no_open_linked_prs(api, repo, issue)
        guard.require_current_policy(api, repo, current)
        guard.guard_identity(api, repo, current, intent)
        need(observation.observe(api, check) == observed, 'direct_issue_changed')
        closed = api.api(f'repos/{repo}/issues/{issue}', method='PATCH',
                         data=dict(state='closed', state_reason='completed'))
        need(isinstance(closed, dict) and closed.get('number') == issue
             and closed.get('state') == 'closed', 'direct_close_result_unknown')
    else:
        need(observed['state'] == 'observed_closed', 'direct_issue_changed')
    final = observation.observe(api, check)
    need(final['state'] == 'observed_closed' and final['merge'] == merge,
         'direct_close_result_unknown')
    guard.require_current_policy(api, repo, current)
    guard.guard_identity(api, repo, current, intent)
    guard.release(api, repo, current, intent)


def merge_and_close(api, repo, number, head, load_document, evaluator,
                    review_run, claim, native, verify, prepare_record,
                    before_mutation=None):
    """Merge a qualified head and close its exact issue after observing it.

    The caller must authenticate the writer and acquire the issue guard. All
    cooperating actors must honor the same guard before this may be deployed.
    """
    review = native.review
    need = review.need
    guard = review.sibling('guarded_close')
    prepared = review.sibling('prepared_status')
    per_pr = review.sibling('per_pr_checkpoint')
    observation = review.sibling('post_merge_observation')
    merge_guard = review.sibling('merge_guard')
    document = load_document()
    policy = native.policy(document)
    issue_policy, intent = claim['policy'], claim['intent']
    payload = intent['payload']
    need(payload['repo'] == repo and payload['pr'] == number and payload['head'] == head,
         'direct_guard_target')
    guard.require_current_policy(api, repo, issue_policy)
    guard.guard_identity(api, repo, issue_policy, intent)
    receipt = verify(api, repo, number, head, load_document, evaluator, review_run)
    context = receipt['payload']['context']
    need(context.get('repo') == repo and context.get('pr') == number and context.get('head') == head,
         'review_target')
    issue = native.qualification(api, repo, number, context, document)
    need(issue == payload['issue'], 'direct_guard_target')
    native.base_rules(api, repo, context, policy)
    pull = native.provider_pull(api, repo, number)
    native.binding(pull, context)
    need(pull.get('autoMergeRequest') is None, 'direct_existing_request')
    nonce = secrets.token_hex(16)
    target = prepared.target_identity(repo, review_run, payload['run'], payload['attempt'], nonce)
    if before_mutation is not None:
        before_mutation()
    try:
        pending = api.api(f'repos/{repo}/statuses/{head}', method='POST', data=dict(
            state='pending', context=policy['status_context'], target_url=target,
            description='Rechecking authenticated review and qualified CI'))
        need(load_document() == document, 'policy_changed')
        need(verify(api, repo, number, head, load_document, evaluator, review_run) == receipt,
             'review_or_policy_changed')
        need(native.qualification(api, repo, number, context, document) == issue, 'linked_issue_changed')
        native.base_rules(api, repo, context, policy)
        pull = native.provider_pull(api, repo, number)
        native.binding(pull, context)
        need(pull.get('autoMergeRequest') is None, 'direct_existing_request')
        guard.require_current_policy(api, repo, issue_policy)
        guard.guard_identity(api, repo, issue_policy, intent)
        checkpoint = prepare_record(api, repo, number, head, document, evaluator, review_run, receipt)
        ready = prepared.build_guarded(checkpoint, issue_policy, intent, pending, nonce)
        print(prepared.MARKER + review.r.canonical(ready), flush=True)
        status = api.api(f'repos/{repo}/statuses/{head}', method='POST', data=dict(
            state='success', context=policy['status_context'], target_url=target,
            description='Exact-head Claude PASS and qualified CI verified'))
        bound = per_pr.build(checkpoint, issue_policy, intent, status, target_url=target)
        print(per_pr.MARKER + review.r.canonical(bound), flush=True)
        need(load_document() == document, 'policy_changed')
        need(verify(api, repo, number, head, load_document, evaluator, review_run) == receipt,
             'review_or_policy_changed')
        need(native.qualification(api, repo, number, context, document) == issue, 'linked_issue_changed')
        native.base_rules(api, repo, context, policy)
        pull = native.provider_pull(api, repo, number)
        native.binding(pull, context)
        need(pull.get('autoMergeRequest') is None, 'direct_existing_request')
        per_pr.verify_status(api, repo, head, bound, intent)
        guard.require_current_policy(api, repo, issue_policy)
        guard.guard_identity(api, repo, issue_policy, intent)
        outcome = merge_http(repo, number, dict(
            sha=head, merge_method='squash', commit_title=f'Merge PR #{number}',
            commit_message=f'Closes #{issue}'))
        if outcome['state'] == 'refused':
            record = refusal_record(intent, issue_policy, outcome['http_status'], merge_guard, review)
            print(REFUSAL_MARKER + review.r.canonical(record), flush=True)
            return dict(state='held_after_pending', head=head, reason='direct_merge_refused')
        merged = outcome['value']
        need(isinstance(merged, dict) and merged.get('merged') is True and review.sha(merged.get('sha')),
             'direct_merge_result_unknown')
        finish_close(api, repo, issue, checkpoint, merged['sha'], issue_policy, intent, guard, observation, review)
        return dict(state='closed', head=head, issue=issue, merge=merged['sha'])
    except Exception:
        # A provider may have accepted the last write. Keep the guard and the
        # authenticated pre-write record; recovery must observe, not retry.
        return dict(state='held_after_pending', head=head, reason='direct_result_unknown')
