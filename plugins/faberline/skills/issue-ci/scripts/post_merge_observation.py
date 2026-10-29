"""Read-only post-merge observations; these observations do not authorize writes.

The caller must separately authenticate the checkpoint against the trusted
pre-merge producer and serialize ownership with the worker.  This module does
not close issues, schedule retries, or treat an enqueued request as a merge.
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import quote

_spec = importlib.util.spec_from_file_location(
    "aw_post_merge_review", Path(__file__).with_name("claude_review.py"))
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)
need = review.need
MARKER = 'AW_POST_MERGE_CHECKPOINT_V1 '
OPEN_LINKS = '''query($owner:String!,$name:String!,$number:Int!,$after:String){
  repository(owner:$owner,name:$name){nameWithOwner issue(number:$number){number
    closedByPullRequestsReferences(first:100,after:$after){totalCount pageInfo{hasNextPage endCursor}
      nodes{number state repository{nameWithOwner}}}
  }}
}'''


def checkpoint_shape(value):
    """Validate structure only, not the authenticity of a checkpoint."""
    need(review.exact(value, "repo pr head base base_ref issue issue_sha256 pr_sha256 issue_updated_at labels assignees"),
         "close_checkpoint_shape")
    need(isinstance(value["repo"], str)
         and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repo"])
         and review.integer(value["pr"]) and review.integer(value["issue"])
         and review.sha(value["head"]) and review.sha(value["base"])
         and review.text(value["base_ref"], 255)
         and isinstance(value["issue_updated_at"], str)
         and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value["issue_updated_at"])
         and isinstance(value["issue_sha256"], str) and review.SHA256.fullmatch(value["issue_sha256"])
         and isinstance(value["pr_sha256"], str) and review.SHA256.fullmatch(value["pr_sha256"]),
         "close_checkpoint_identity")
    for key in ("labels", "assignees"):
        entries = value[key]
        need(isinstance(entries, list) and all(review.text(x, 255) for x in entries)
             and entries == sorted(set(entries)), "close_checkpoint_ownership")
    return value


def record(checkpoint, source_run, source_attempt, source_commit, evaluator,
           review_run, review_sha256, adoption_sha256=None, adoption_source=None):
    """Create a typed integrity record. A hash does not authenticate its source."""
    checkpoint_shape(checkpoint)
    need(review.integer(source_run) and review.integer(source_attempt)
         and review.sha(source_commit) and review.sha(evaluator)
         and review.integer(review_run) and isinstance(review_sha256, str)
         and review.SHA256.fullmatch(review_sha256)
         and ((adoption_sha256 is None and adoption_source is None)
              or (isinstance(adoption_sha256, str) and review.SHA256.fullmatch(adoption_sha256)
                  and review.sha(adoption_source))), 'close_record_identity')
    payload = dict(version=1, checkpoint=checkpoint, source_run=source_run,
                   source_attempt=source_attempt, source_commit=source_commit,
                   evaluator=evaluator, review_run=review_run, review_sha256=review_sha256,
                   adoption_sha256=adoption_sha256, adoption_source=adoption_source)
    return dict(payload=payload, sha256=review.r.digest(payload))


def parse_records(log):
    """Parse all records in one log; hashes alone do not authenticate them."""
    result, seen = [], set()
    for item in review.r.records(log, MARKER):
        need(review.exact(item, 'payload sha256') and isinstance(item['payload'], dict),
             'close_record_shape')
        p = item['payload']
        need(review.exact(p, 'version checkpoint source_run source_attempt source_commit evaluator review_run review_sha256 adoption_sha256 adoption_source')
             and type(p['version']) is int and p['version'] == 1, 'close_record_shape')
        expected = record(p['checkpoint'], p['source_run'], p['source_attempt'], p['source_commit'],
                          p['evaluator'], p['review_run'], p['review_sha256'], p['adoption_sha256'],
                          p['adoption_source'])
        need(item == expected, 'close_record_digest')
        c = p['checkpoint']
        target = (c['repo'], c['pr'], c['head'])
        need(target not in seen, 'close_record_missing_or_duplicate')
        seen.add(target)
        result.append(item)
    return result


def find_record(log, repo, pr, head):
    """Parse one target record; caller must authenticate its producer and proofs."""
    matches = [item for item in parse_records(log)
               if (item['payload']['checkpoint']['repo'], item['payload']['checkpoint']['pr'],
                   item['payload']['checkpoint']['head']) == (repo, pr, head)]
    need(len(matches) == 1, 'close_record_missing_or_duplicate')
    return matches[0]


def authenticate_native_records(api, repo, document, evaluator, source_run, log_reader):
    """Authenticate all records from one default-branch writer, not a close.

    The caller must load the historical policy from the stated immutable
    commit and separately perform issue/ownership observation and serialization.
    PR-target wake records are refused: run.head_sha may be the PR head and is
    not proof of the workflow's base commit.
    """
    native = review.sibling('native_merge')
    policy = native.policy(document)
    native.writer_contract(api, repo, document, evaluator)
    identity = review.producer_identity(api, repo, document, source_run, True,
        policy['producer'], events=('issues', 'workflow_run', 'repository_dispatch'))
    need(identity['head'] == document['source_commit'], 'close_source_commit_unknown')
    meta = api.api(f'repos/{repo}')
    branch = meta.get('default_branch')
    need(meta.get('full_name') == repo and review.text(branch, 255), 'close_default_branch_unknown')
    comparison = api.api(f"repos/{repo}/compare/{identity['head']}...{quote(branch, safe='')}")
    need(comparison.get('status') in ('identical', 'ahead')
         and comparison.get('base_commit', {}).get('sha') == identity['head'],
         'close_source_not_on_default')
    records = parse_records(log_reader(repo, identity['job_id']))
    for evidence in records:
        payload = evidence['payload']
        need(payload['checkpoint']['repo'] == repo and payload['source_run'] == source_run
             and payload['source_attempt'] == identity['run_attempt']
             and payload['source_commit'] == identity['head'] and payload['evaluator'] == evaluator
             and payload['adoption_sha256'] is None and payload['adoption_source'] is None,
             'close_record_source_mismatch')
    return records


def authenticate_native_source(api, repo, document, evaluator, source_run, pr, head, log_reader):
    records = authenticate_native_records(api, repo, document, evaluator, source_run, log_reader)
    matches = [item for item in records if item['payload']['checkpoint']['pr'] == pr
               and item['payload']['checkpoint']['head'] == head]
    need(len(matches) == 1, 'close_record_missing_or_duplicate')
    return matches[0]


def authenticate_adoption_source(api, repo, document, evaluator, source_run, pr, head, log_reader):
    """Authenticate a reviewed grant publisher. It does not authorize a close."""
    adoption = review.sibling('policy_adoption')
    need(isinstance(document, dict) and review.sha(document.get('source_commit')),
         'close_grant_source_unknown')
    grant = adoption.choose_grant(document.get('config'), repo, evaluator)
    need(grant['pr'] == pr and grant['head'] == head, 'close_grant_target_changed')
    meta = api.api(f'repos/{adoption.GRANTS_REPO}')
    branch = meta.get('default_branch')
    need(meta.get('full_name') == adoption.GRANTS_REPO and review.text(branch, 255),
         'close_grant_default_unknown')
    comparison = api.api(f"repos/{adoption.GRANTS_REPO}/compare/{document['source_commit']}...{quote(branch, safe='')}")
    need(comparison.get('status') in ('identical', 'ahead')
         and comparison.get('base_commit', {}).get('sha') == document['source_commit'],
         'close_grant_not_on_default')
    adoption.verify_grant_attestation(api, document, grant, evaluator, log_reader)
    identity = adoption.publisher_provenance(api, grant, source_run, 'push', completed=True)
    evidence = find_record(log_reader(repo, identity['job_id']), repo, pr, head)
    p = evidence['payload']
    c = p['checkpoint']
    need(p['source_run'] == source_run and p['source_attempt'] == identity['run_attempt']
         and p['source_commit'] == identity['source_commit'] and p['evaluator'] == evaluator
         and p['adoption_source'] == document['source_commit']
         and p['adoption_sha256'] == review.r.digest(grant)
         and p['review_run'] == grant['review']['run_id']
         and p['review_sha256'] == grant['review']['receipt_sha256']
         and all(c[key] == grant[key] for key in ('repo', 'pr', 'issue', 'head', 'base', 'base_ref',
                                                    'issue_sha256', 'pr_sha256')),
         'close_grant_record_mismatch')
    return evidence


def capture(repo, number, head, base, base_ref, pull, issue, issue_sha256, pr_sha256):
    """Capture identities only after the caller verifies CI and review.

    The caller must authenticate those proofs and the producer.  The captured
    object itself is not an authorization or a durable recovery record.
    """
    review.pull_identity(pull, repo, number, head)
    need(pull['base'].get('sha') == base and pull['base'].get('ref') == base_ref,
         'close_base_changed')
    issue_number = review.linked_issue(pull)
    need(type(issue.get('number')) is int and issue.get('number') == issue_number
         and issue.get('state') == 'open' and 'pull_request' not in issue
         and review.r.digest({k: issue.get(k) for k in ('number', 'title', 'body')}) == issue_sha256
         and review.r.digest([pull.get('title'), pull.get('body')]) == pr_sha256,
         'close_source_contract_changed')
    labels, assignees = issue.get('labels'), issue.get('assignees')
    need(isinstance(labels, list) and isinstance(assignees, list)
         and all(isinstance(x, dict) and review.text(x.get('name'), 255) for x in labels)
         and all(isinstance(x, dict) and review.text(x.get('login'), 255) for x in assignees),
         'close_issue_ownership_unknown')
    result = dict(repo=repo, pr=number, head=head, base=base, base_ref=base_ref,
                  issue=issue_number, issue_sha256=issue_sha256, pr_sha256=pr_sha256,
                  issue_updated_at=issue.get('updated_at'),
                  labels=sorted(x['name'] for x in labels),
                  assignees=sorted(x['login'] for x in assignees))
    checkpoint_shape(result)
    need(not {'blocked', 'agy:blocked'} & set(result['labels']), 'close_issue_blocked')
    return result


def capture_verified(repo, number, head, pull, issue, receipt, source_run, source_attempt,
                     source_commit, evaluator):
    """Prepare a record after the caller authenticates the review and CI.

    This validates the receipt's shape, digest and target. It does not replace
    the caller's independent review, gate or producer verification.
    """
    need(review.exact(receipt, 'payload sha256') and isinstance(receipt['payload'], dict)
         and review.r.digest(receipt['payload']) == receipt['sha256'], 'close_review_receipt')
    payload = receipt['payload']
    context, result, producer = payload.get('context'), payload.get('result'), payload.get('producer')
    need(isinstance(context, dict) and isinstance(result, dict) and result.get('decision') == 'pass'
         and context.get('repo') == repo and type(context.get('pr')) is int and context['pr'] == number
         and context.get('head') == head and context.get('evaluator') == evaluator
         and context.get('policy_source') == source_commit, 'close_review_target')
    need(isinstance(producer, dict) and review.integer(producer.get('run_id')), 'close_review_run_unknown')
    snapshot = capture(repo, number, head, context.get('base'), context.get('base_ref'), pull, issue,
                       context.get('issue_sha256'), context.get('pr_description_sha256'))
    return record(snapshot, source_run, source_attempt, source_commit, evaluator,
                  producer['run_id'], receipt['sha256'])


def ownership_history(events, started_at):
    """Refuse evidence of a later claim, even if the snapshot was restored.

    The caller must obtain every timeline page and authenticate ``started_at``
    from the trusted producer's GitHub run. This is still not a shared lock.
    """
    def when(value):
        need(isinstance(value, str) and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', value),
            'close_history_time_unknown')
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
        except ValueError:
            raise review.Refused('close_history_time_unknown') from None

    start = when(started_at).replace(microsecond=0)
    need(isinstance(events, list), 'close_history_unknown')
    harmless = {'commented', 'cross-referenced', 'referenced', 'mentioned', 'subscribed', 'unsubscribed'}
    for event in events:
        need(isinstance(event, dict) and review.text(event.get('event'), 100), 'close_history_unknown')
        at = when(event.get('created_at'))
        # A timestamp can have only second precision. Refuse the same second.
        if at >= start:
            need(event['event'] in harmless, 'close_ownership_epoch_changed')
    return True


def source_started_at(api, evidence):
    """Read the exact completed producer attempt's provider timestamp."""
    need(review.exact(evidence, 'payload sha256') and isinstance(evidence['payload'], dict),
         'close_source_time_unknown')
    p = evidence['payload']
    need(isinstance(p.get('checkpoint'), dict) and review.integer(p.get('source_run'))
         and review.integer(p.get('source_attempt')) and review.sha(p.get('source_commit')),
         'close_source_time_unknown')
    repo = checkpoint_shape(p['checkpoint'])['repo']
    run = api.api(f"repos/{repo}/actions/runs/{p['source_run']}/attempts/{p['source_attempt']}")
    need(run.get('id') == p['source_run'] and run.get('run_attempt') == p['source_attempt']
         and run.get('status') == 'completed' and run.get('conclusion') == 'success'
         and run.get('head_sha') == p['source_commit']
         and run.get('repository', {}).get('full_name') == repo
         and run.get('head_repository', {}).get('full_name') == repo,
         'close_source_time_unknown')
    started_at = run.get('run_started_at')
    ownership_history([], started_at)
    return started_at


def no_open_linked_prs(api, repo, issue_number):
    """Refuse any open closing-reference PR; this check is not a lock."""
    need(isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
         and review.integer(issue_number), 'close_link_input')
    owner, name = repo.split('/')
    after, total, seen, cursors = None, None, set(), set()
    for _ in range(1000):
        result = api.api('graphql', method='POST', data=dict(
            query=OPEN_LINKS, variables=dict(owner=owner, name=name, number=issue_number, after=after)))
        need(isinstance(result, dict) and not result.get('errors') and isinstance(result.get('data'), dict),
             'close_link_evidence_unknown')
        repository = result['data'].get('repository')
        need(isinstance(repository, dict) and repository.get('nameWithOwner') == repo,
             'close_link_evidence_unknown')
        issue = repository.get('issue')
        need(isinstance(issue, dict) and type(issue.get('number')) is int and issue['number'] == issue_number,
             'close_link_evidence_unknown')
        links = issue.get('closedByPullRequestsReferences')
        need(isinstance(links, dict) and type(links.get('totalCount')) is int and links['totalCount'] >= 0
             and isinstance(links.get('nodes'), list) and len(links['nodes']) <= 100
             and isinstance(links.get('pageInfo'), dict)
             and type(links['pageInfo'].get('hasNextPage')) is bool,
             'close_link_evidence_unknown')
        if total is None:
            total = links['totalCount']
        need(total == links['totalCount'], 'close_link_evidence_changed')
        for node in links['nodes']:
            need(isinstance(node, dict) and review.integer(node.get('number'))
                 and node.get('state') in ('OPEN', 'CLOSED', 'MERGED')
                 and isinstance(node.get('repository'), dict)
                 and isinstance(node['repository'].get('nameWithOwner'), str)
                 and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', node['repository']['nameWithOwner']),
                 'close_link_evidence_unknown')
            identity = (node['repository']['nameWithOwner'], node['number'])
            need(identity not in seen, 'close_link_evidence_unknown')
            seen.add(identity)
            need(node['state'] != 'OPEN', 'close_other_open_pr')
        if not links['pageInfo']['hasNextPage']:
            need(len(seen) == total, 'close_link_evidence_unknown')
            return True
        cursor = links['pageInfo'].get('endCursor')
        need(review.text(cursor, 1024) and cursor not in cursors and links['nodes'], 'close_link_evidence_unknown')
        cursors.add(cursor)
        after = cursor
    raise review.Refused('close_link_page_limit')


def observe(api, checkpoint):
    """Observe an exact merge and unchanged issue without granting authority.

    A caller-supplied checkpoint is not proof.  The caller must authenticate its
    source, its CI/review receipt, and the ownership epoch before any write.
    """
    c = checkpoint_shape(checkpoint)
    repo, number, issue_number = c["repo"], c["pr"], c["issue"]
    meta = api.api(f"repos/{repo}")
    need(meta.get("full_name") == repo and meta.get("default_branch") == c["base_ref"],
         "close_default_branch_changed")
    pull = api.api(f"repos/{repo}/pulls/{number}")
    need(type(pull.get("number")) is int and pull.get("number") == number and pull.get("state") == "closed"
         and pull.get("merged") is True and review.text(pull.get("merged_at"), 100)
         and isinstance(pull.get("merged_by"), dict)
         and pull["merged_by"].get("id") == 41898282
         and pull["merged_by"].get("type") == "Bot"
         and pull["merged_by"].get("login") == "github-actions[bot]"
         and pull.get("head", {}).get("sha") == c["head"]
         and pull.get("head", {}).get("repo", {}).get("full_name") == repo
         and pull.get("base", {}).get("sha") == c["base"]
         and pull.get("base", {}).get("ref") == c["base_ref"]
         and pull.get("base", {}).get("repo", {}).get("full_name") == repo
         and review.sha(pull.get("merge_commit_sha")), "close_merge_identity")
    need(review.linked_issue(pull) == issue_number
         and review.r.digest([pull.get("title"), pull.get("body")]) == c["pr_sha256"],
         "close_pr_contract_changed")
    merge = pull["merge_commit_sha"]
    commit = api.api(f"repos/{repo}/commits/{merge}")
    need(commit.get("sha") == merge and isinstance(commit.get("parents"), list)
         and len(commit["parents"]) == 1 and commit["parents"][0].get("sha") == c["base"],
         "close_merge_parent")
    comparison = api.api(f"repos/{repo}/compare/{merge}...{quote(c['base_ref'], safe='')}")
    need(comparison.get("status") in ("identical", "ahead")
         and comparison.get("base_commit", {}).get("sha") == merge,
         "close_merge_not_on_default")
    issue = api.api(f"repos/{repo}/issues/{issue_number}")
    need(type(issue.get("number")) is int and issue.get("number") == issue_number and "pull_request" not in issue
         and issue.get("state") in ("open", "closed")
         and review.r.digest({k: issue.get(k) for k in ("number", "title", "body")}) == c["issue_sha256"],
         "close_issue_contract_changed")
    labels = issue.get("labels")
    assignees = issue.get("assignees")
    need(isinstance(labels, list) and isinstance(assignees, list)
         and all(isinstance(x, dict) and review.text(x.get("name"), 255) for x in labels)
         and all(isinstance(x, dict) and review.text(x.get("login"), 255) for x in assignees),
         "close_issue_ownership_unknown")
    if issue["state"] == "open":
        need(sorted(x["name"] for x in labels) == c["labels"]
             and sorted(x["login"] for x in assignees) == c["assignees"]
             and issue.get('updated_at') == c['issue_updated_at']
             and not {"blocked", "agy:blocked"} & set(c["labels"]),
             "close_issue_ownership_changed")
    return dict(state="observed_closed" if issue["state"] == "closed" else "observed_merged",
                repo=repo, pr=number, issue=issue_number, head=c["head"], merge=merge)


def recovery_plan(api, checkpoint, started_at):
    """Inspect a candidate without granting permission to close an issue.

    The caller must authenticate the source record and run start. A shared
    ownership guard must still cover the eventual read and write.
    """
    c = checkpoint_shape(checkpoint)
    repo, number = c['repo'], c['pr']
    pull = api.api(f'repos/{repo}/pulls/{number}')
    need(type(pull.get('number')) is int and pull['number'] == number
         and pull.get('head', {}).get('sha') == c['head']
         and pull.get('head', {}).get('repo', {}).get('full_name') == repo
         and pull.get('base', {}).get('sha') == c['base']
         and pull.get('base', {}).get('ref') == c['base_ref']
         and pull.get('base', {}).get('repo', {}).get('full_name') == repo
         and review.r.digest([pull.get('title'), pull.get('body')]) == c['pr_sha256']
         and review.linked_issue(pull) == c['issue'], 'close_recovery_target_changed')
    if pull.get('merged') is False and pull.get('state') in ('open', 'closed'):
        return dict(state='waiting' if pull['state'] == 'open' else 'not_merged',
                    repo=repo, pr=number, issue=c['issue'], head=c['head'], close_authorized=False)
    observation = observe(api, c)
    if observation['state'] == 'observed_closed':
        return dict(observation, close_authorized=False)
    events = api.pages(f"repos/{repo}/issues/{c['issue']}/timeline")
    ownership_history(events, started_at)
    no_open_linked_prs(api, repo, c['issue'])
    # This explicitly does not assert that no actor can claim after our reads.
    return dict(observation, state='guard_required', close_authorized=False)


def native_recovery_plans(api, repo, document, evaluator, source_run, log_reader):
    """Inspect every record in an authenticated writer run, without writes."""
    records = authenticate_native_records(api, repo, document, evaluator, source_run, log_reader)
    if not records:
        return []
    started_at = source_started_at(api, records[0])
    results = []
    for evidence in records:
        c = evidence['payload']['checkpoint']
        try:
            results.append(recovery_plan(api, c, started_at))
        except Exception as error:
            code = str(error)
            reason = code if isinstance(error, review.Refused) and re.fullmatch(r'[a-z0-9_]+', code) else 'verification_or_provider_failed'
            results.append(dict(state='held', reason=reason, repo=repo, pr=c['pr'],
                                issue=c['issue'], head=c['head'], close_authorized=False))
    return results


def adoption_recovery_plan(api, repo, document, evaluator, source_run, pr, head, log_reader):
    """Inspect one exact reviewed adoption record, without writes."""
    evidence = authenticate_adoption_source(api, repo, document, evaluator, source_run, pr, head, log_reader)
    started_at = source_started_at(api, evidence)
    return recovery_plan(api, evidence['payload']['checkpoint'], started_at)
