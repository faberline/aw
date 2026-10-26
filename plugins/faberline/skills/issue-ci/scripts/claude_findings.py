"""Offline candidate: separate deterministic issue-comment publisher.

No model, merge, close, labels, arbitrary destinations or persistent store.
Practical deduplication uses authenticated comments, trusted run history and
workflow concurrency. Exactly-once delivery across crash/visibility/history-loss
windows is NOT guaranteed. Unknown writes never license an automatic retry.
"""
import os
import sys
from datetime import datetime
from urllib.parse import quote
from pathlib import Path
import re

# Loaded from the same immutable action tree, never a receipt/PR-selected module.
import importlib.util
_spec = importlib.util.spec_from_file_location('aw_findings_review', Path(__file__).with_name('claude_review.py'))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def safe_json(value):
    # JSON data in a code block: no mentions, HTML, fences or active model links.
    body = g.r.canonical(value)
    for char in ('@', '<', '>', '&', '`'):
        body = body.replace(char, '\\u%04x' % ord(char))
    return body


def render(receipt, issue):
    payload = receipt['payload']; context = payload['context']; result = payload['result']
    g.validate_result(result, context)
    requirements = {r['id']: r for r in context['requirement_plan']['requirements']}
    findings = []
    uncertainties = [dict(kind='contract', detail=v) for v in result['contract_uncertainties']]
    for item in result['requirement_results']:
        if item['status'] == 'missing':
            requirement = requirements[item['id']]
            findings.append(dict(kind=item['status'], requirement_id=item['id'],
                requirement=requirement['requirement'], issue_quote=requirement['issue_quote'],
                detail=item['detail'], locations=item['locations']))
        elif item['status'] in ('ambiguous', 'insufficient_evidence'):
            uncertainties.append(dict(kind=item['status'], requirement_id=item['id'],
                detail=item['detail'], locations=item['locations']))
    for item in result['extra_changes']:
        findings.append(dict(kind='extra', requirement='Outside the explicit issue scope; see requirements below.',
                             locations=[item]))
    record = dict(repo=context['repo'], issue=issue, pr=context['pr'], head=context['head'], base=context['base'],
        review_sha256=receipt['sha256'], producer=payload['producer'], review_status=result['decision'], fit=result['fit'],
        requirements=list(requirements.values()), findings=findings,
        uncertainties=uncertainties, summary=result['summary'])
    marker = '<!-- AW_ISSUE_CONFORMANCE_V1 ' + g.r.digest(record) + ' -->'
    body = marker + '\nIssue-conformance review (CI gates are verified separately).\n```json\n' + safe_json(record) + '\n```'
    g.need(len(body.encode()) <= 60000, 'findings_body_limit')
    return body


def reconcile(api, intent):
    comments = api.pages(intent['target'])
    matches = [c for c in comments if c.get('body') == intent['body']
               and c.get('user', {}).get('login') == 'github-actions[bot]'
               and c.get('user', {}).get('type') == 'Bot']
    g.need(len(matches) <= 1, 'findings_duplicate_receipts')
    if matches:
        g.need(g.integer(matches[0].get('id')), 'findings_comment_identity')
        return dict(status='confirmed', historical=True, comment_id=matches[0]['id'],
                    issue=intent['issue'], head=intent['head'], base=intent['base'],
                    review_sha256=intent['review_sha256'], producer=intent['producer'])
    return None


def publisher_policy(document):
    g.policy(document)
    p = document['config'].get('claude_findings')
    g.need(g.exact(p, 'version path definition_sha job') and type(p['version']) is int
           and p['version'] == 1 and p['path'] == '.github/workflows/claude-findings.yml'
           and g.sha(p['definition_sha']) and p['job'] == 'publish-findings', 'findings_policy')
    return p


def publication_context(api, repo, document, source, publisher_id, publisher_attempt):
    """Authenticate the current publisher and fully paginate bounded peer history.

    Fixed run-name is only an index. Workflow/path/blob/repo/event identity is
    checked independently. No model field selects run identity or concurrency.
    """
    p = publisher_policy(document)
    g.need(g.integer(publisher_id) and g.integer(publisher_attempt), 'findings_run_identity')
    current = api.api(f'repos/{repo}/actions/runs/{publisher_id}')
    meta = api.api(f'repos/{repo}')
    name = f"claude-findings-{source['run_id']}-{source['run_attempt']}"
    g.need(current.get('id') == publisher_id and current.get('run_attempt') == publisher_attempt
           and current.get('event') == 'workflow_run' and current.get('path') == p['path']
           and current.get('head_repository', {}).get('full_name') == repo
           and current.get('head_branch') == meta['default_branch']
           and current.get('display_title') == name and current.get('status') == 'in_progress'
           and g.sha(current.get('head_sha')) and g.integer(current.get('workflow_id')),
           'findings_run_identity')
    workflow = api.api(f"repos/{repo}/actions/workflows/{current['workflow_id']}")
    g.need(workflow.get('id') == current['workflow_id'] and workflow.get('path') == p['path'],
           'findings_workflow_identity')
    g.blob(api, repo, p['path'], current['head_sha'], p['definition_sha'])
    g.blob(api, repo, p['path'], document['source_commit'], p['definition_sha'])
    jobs = api.pages(f'repos/{repo}/actions/runs/{publisher_id}/attempts/{publisher_attempt}/jobs', 'jobs')
    selected = [j for j in jobs if j.get('name') == p['job']]
    g.need(len(selected) == 1 and selected[0].get('status') == 'in_progress'
           and g.integer(selected[0].get('id')), 'findings_job_identity')
    source_run = api.api(f"repos/{repo}/actions/runs/{source['run_id']}")
    created = source_run.get('created_at')
    try:
        g.need(isinstance(created, str), 'findings_history_time')
        datetime.strptime(created, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise g.Refused('findings_history_time') from None
    peers, all_ids, total = [], [], None
    for page in range(1, 11):
        url = (f"repos/{repo}/actions/workflows/{current['workflow_id']}/runs"
               f"?event=workflow_run&created={quote('>=' + created, safe='')}&per_page=100&page={page}")
        value = api.api(url)
        batch = value.get('workflow_runs') if isinstance(value, dict) else None
        g.need(isinstance(batch, list) and len(batch) <= 100 and all(isinstance(v, dict) for v in batch)
               and type(value.get('total_count')) is int, 'findings_history_incomplete')
        if total is None: total = value['total_count']
        g.need(value['total_count'] == total and 0 < total <= 1000, 'findings_history_incomplete')
        g.need(all(g.integer(v.get('id')) for v in batch), 'findings_history_identity')
        all_ids.extend(v['id'] for v in batch)
        for run in batch:
            # Any matching title in this workflow is conservatively a prior
            # attempt, even if its historical blob is no longer trusted.
            if run.get('display_title') == name:
                g.need(g.integer(run.get('id')) and g.integer(run.get('run_number'))
                       and run.get('workflow_id') == current['workflow_id']
                       and run.get('event') == 'workflow_run'
                       and run.get('head_repository', {}).get('full_name') == repo,
                       'findings_history_identity')
                peers.append(run)
        if len(all_ids) == total or len(batch) < 100:
            break
    else:
        raise g.Refused('findings_history_limit')
    # Confirm this run was visible. History inconsistency never grants POST.
    g.need(len(all_ids) == total and len(set(all_ids)) == len(all_ids), 'findings_history_incomplete')
    own = [v for v in peers if v['id'] == publisher_id]
    g.need(len(own) == 1 and own[0].get('run_attempt') == publisher_attempt
           and all(own[0].get(k) == current.get(k) for k in ('run_number', 'head_sha', 'path', 'workflow_id', 'event'))
           and len({v['id'] for v in peers}) == len(peers)
           and len({v['run_number'] for v in peers}) == len(peers), 'findings_history_incomplete')
    first = min(peers, key=lambda v: v['run_number'])
    return dict(run_id=publisher_id, run_attempt=publisher_attempt,
                workflow_id=current['workflow_id'], workflow_sha=p['definition_sha'],
                head=current['head_sha'], job_id=selected[0]['id'],
                may_post=publisher_attempt == 1 and first['id'] == publisher_id)


def publish(api, repo, pr, head, load_document, evaluator, run_id, publisher_id, publisher_attempt, log_reader):
    """One bounded POST in an authenticated first-run context; no blind retries.

    Caller workflow MUST serialize by source run+attempt, cancel-in-progress
    false. A duplicate publisher run/rerun only reconciles; absent comment holds.
    History deletion/delayed API visibility can still defeat practical dedupe:
    informational findings do not confer merge/acceptance authority.
    """
    g.need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and not os.environ.get('ANTHROPIC_API_KEY'),
           'findings_oauth_scope_refused')
    document = load_document()
    receipt = g.verify_result_receipt(api, repo, pr, head, load_document, evaluator, run_id, log_reader)
    context, packet = g.collect(api, repo, pr, head, load_document, evaluator,
                               source_plan=receipt['payload']['context']['requirement_plan'])
    g.need(context == receipt['payload']['context'], 'findings_context_changed')
    identity = publication_context(api, repo, document, receipt['payload']['producer'], publisher_id, publisher_attempt)
    issue = packet['issue']['number']
    intent = dict(repo=repo, pr=pr, head=head, issue=issue, review_sha256=receipt['sha256'],
                  base=context['base'], producer=receipt['payload']['producer'],
                  target=f'repos/{repo}/issues/{issue}/comments', body=render(receipt, issue))
    found = reconcile(api, intent)
    if found: return found
    g.need(identity['may_post'], 'findings_prior_attempt_unknown_no_retry')
    again = g.verify_result_receipt(api, repo, pr, head, load_document, evaluator, run_id, log_reader)
    g.need(again == receipt and load_document() == document, 'findings_review_changed')
    g.need(publication_context(api, repo, document, receipt['payload']['producer'], publisher_id, publisher_attempt)
           == identity, 'findings_publisher_changed')
    try:
        api.api(intent['target'], 'POST', {'body': intent['body']})
    except Exception:
        pass  # No exception text, raw response or second POST.
    for _ in range(3):
        found = reconcile(api, intent)
        if found: return found
    raise g.Refused('findings_write_unknown_no_retry')


def main():
    g.need(os.environ.get('GITHUB_ACTIONS') == 'true'
           and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run', 'trusted_workflow_run_required')
    repo, evaluator = os.environ.get('GITHUB_REPOSITORY'), os.environ.get('AW_ACTION_REF')
    g.need(isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
           and g.sha(evaluator), 'findings_caller_identity')
    g.need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and not os.environ.get('ANTHROPIC_API_KEY'),
           'findings_oauth_scope_refused')
    event = g.ci.strict_json_loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    wake = event.get('workflow_run', {})
    g.need(event.get('action') == 'completed' and wake.get('status') == 'completed'
           and wake.get('conclusion') == 'success' and g.integer(wake.get('id')), 'findings_wake')
    load_document = lambda: g.action.trusted_policy(g.ci, repo, '.github/issue-ci-required-gates.json', metadata=True)
    document = load_document()
    identity = g.producer(g.ci, repo, document, wake['id'], True)
    g.need(wake.get('workflow_id') == identity['workflow_id'] and wake.get('path') == g.policy(document)['producer']['path']
           and wake.get('run_attempt') == identity['run_attempt'] and wake.get('head_sha') == identity['head']
           and wake.get('head_repository', {}).get('full_name') == repo, 'findings_wake_identity')
    records = g.r.records(g.r.job_log(repo, identity['job_id']), g.MARKER)
    g.need(len(records) == 1 and g.exact(records[0], 'payload sha256'), 'review_record_ambiguous')
    context = records[0].get('payload', {}).get('context', {})
    g.need(context.get('repo') == repo and g.integer(context.get('pr')) and g.sha(context.get('head')),
           'findings_target_identity')
    publisher_id, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    g.need(re.fullmatch(r'[1-9][0-9]*', publisher_id) and re.fullmatch(r'[1-9][0-9]*', attempt), 'findings_run_identity')
    verified = publish(g.ci, repo, context['pr'], context['head'], load_document, evaluator,
                       wake['id'], int(publisher_id), int(attempt), g.r.job_log)
    print(g.r.canonical(verified))  # fixed keys/int/hex only, no model prose.
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except g.Refused as error:
        print('refused: ' + str(error), file=sys.stderr); sys.exit(2)
    except Exception:
        print('refused: findings_operation_unknown_no_retry', file=sys.stderr); sys.exit(2)
