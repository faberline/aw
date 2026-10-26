"""Offline candidate: separate deterministic issue-comment publisher.

No CLI/model, permission changes, merge, close, labels, arbitrary URLs or model
selected destinations. Not wired into an action. The controller must supply a
trusted durable directory shared by ALL runs/hosts before deployment; ephemeral
runner storage is insufficient. Ambiguous writes never authorize another POST.
"""
import os
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
    for item in result['requirement_results']:
        if item['status'] != 'met':
            requirement = requirements[item['id']]
            findings.append(dict(kind=item['status'], requirement_id=item['id'],
                requirement=requirement['requirement'], issue_quote=requirement['issue_quote'],
                detail=item['detail'], locations=item['locations']))
    for item in result['extra_changes']:
        findings.append(dict(kind='extra', requirement='Outside the explicit issue scope; see requirements below.',
                             locations=[item]))
    record = dict(repo=context['repo'], issue=issue, pr=context['pr'], head=context['head'], base=context['base'],
        review_sha256=receipt['sha256'], producer=payload['producer'], decision=result['decision'], fit=result['fit'],
        requirements=list(requirements.values()), findings=findings,
        uncertainties=result['contract_uncertainties'], summary=result['summary'])
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


def publish(api, repo, pr, head, load_document, evaluator, run_id, journal_dir, log_reader):
    """Controller-only API. Journal must be trusted persistent storage, not PR input.

    One exclusive durable intent per repo/PR/head/review-run. Every subsequent
    invocation ONLY reconciles. Absence after a timeout remains unknown/held;
    neither a rerun nor simultaneous process can POST a second time. The directory
    must survive worker replacement and be shared across hosts (deployment gap).
    """
    g.need(not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'), 'findings_oauth_scope_refused')
    g.need(isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
           and g.integer(pr) and g.sha(head) and g.integer(run_id), 'findings_caller_identity')
    key = g.r.digest(dict(repo=repo, pr=pr, head=head, review_run=run_id))
    root = Path(journal_dir)
    g.need(root.is_dir() and not root.is_symlink(), 'findings_durable_store_required')
    path = root / (key + '.json')
    if path.exists():
        # Stored only after full verification; do not re-publish even if issue
        # activity changed after our own comment. No receipt supplied
        # by the model, caller or artifact is accepted by this API.
        g.need(not path.is_symlink(), 'findings_journal_identity')
        intent = g.ci.strict_json_loads(path.read_bytes())
        g.need(intent['key'] == key and intent['repo'] == repo and intent['pr'] == pr
               and intent['head'] == head and intent['run_id'] == run_id
               and intent['target'] == f"repos/{repo}/issues/{intent['issue']}/comments"
               and g.integer(intent['issue']), 'findings_journal_identity')
        found = reconcile(api, intent)
        g.need(found is not None, 'findings_write_unknown_no_retry')
        return found
    receipt = g.verify_result_receipt(api, repo, pr, head, load_document, evaluator, run_id,
                              log_reader)
    context, packet = g.collect(api, repo, pr, head, load_document, evaluator,
                               source_plan=receipt['payload']['context']['requirement_plan'])
    g.need(context == receipt['payload']['context'], 'findings_context_changed')
    issue = packet['issue']['number']
    intent = dict(key=key, repo=repo, pr=pr, head=head, run_id=run_id, issue=issue,
                  review_sha256=receipt['sha256'], base=context['base'],
                  producer=receipt['payload']['producer'], target=f'repos/{repo}/issues/{issue}/comments',
                  body=render(receipt, issue))
    # fsync the intent AND parent before the network operation. Losing the
    # journal invalidates the no-retry guarantee; no fallback store is permitted.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise g.Refused('findings_concurrent_intent_reconcile_only') from None
    with os.fdopen(fd, 'w') as stream:
        stream.write(g.r.canonical(intent)); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(root, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    found = reconcile(api, intent)
    if found: return found
    # Fresh exact context/gates/producer immediately before the single POST.
    again = g.verify_result_receipt(api, repo, pr, head, load_document, evaluator, run_id,
                            log_reader)
    g.need(again == receipt, 'findings_review_changed')
    try:
        api.api(intent['target'], 'POST', {'body': intent['body']})
    except Exception:
        # Do not surface exceptions or repeat the write. Reconcile safely below.
        pass
    found = reconcile(api, intent)
    g.need(found is not None, 'findings_write_unknown_no_retry')
    return found
