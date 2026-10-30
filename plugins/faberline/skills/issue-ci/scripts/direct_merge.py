"""Guarded synchronous merge and issue close, pending rollout verification.

This is called only by the authenticated default-branch writer. Unknown
provider outcomes retain the guard for recovery; they are never retried here.
"""
import secrets


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
        merged = api.api(f'repos/{repo}/pulls/{number}/merge', method='PUT', data=dict(
            sha=head, merge_method='squash', commit_title=f'Merge PR #{number}',
            commit_message=f'Closes #{issue}'))
        need(isinstance(merged, dict) and merged.get('merged') is True and review.sha(merged.get('sha')),
             'direct_merge_result_unknown')
        check = checkpoint['payload']['checkpoint']
        observed = observation.observe(api, check)
        need(observed['merge'] == merged['sha'], 'direct_merge_result_changed')
        if observed['state'] == 'observed_merged':
            # The checkpoint seals the ownership timeline. Guard identity is
            # checked again immediately before closing the issue.
            events = api.pages(f'repos/{repo}/issues/{issue}/timeline')
            observation.ownership_history(events, '1970-01-01T00:00:00Z', check['ownership_events'])
            observation.no_open_linked_prs(api, repo, issue)
            guard.require_current_policy(api, repo, issue_policy)
            guard.guard_identity(api, repo, issue_policy, intent)
            need(observation.observe(api, check) == observed, 'direct_issue_changed')
            closed = api.api(f'repos/{repo}/issues/{issue}', method='PATCH',
                             data=dict(state='closed', state_reason='completed'))
            need(isinstance(closed, dict) and closed.get('number') == issue
                 and closed.get('state') == 'closed', 'direct_close_result_unknown')
        else:
            need(observed['state'] == 'observed_closed', 'direct_issue_changed')
        final = observation.observe(api, check)
        need(final['state'] == 'observed_closed' and final['merge'] == merged['sha'],
             'direct_close_result_unknown')
        guard.require_current_policy(api, repo, issue_policy)
        guard.release(api, repo, issue_policy, intent)
        return dict(state='closed', head=head, issue=issue, merge=merged['sha'])
    except Exception:
        # A provider may have accepted the last write. Keep the guard and the
        # authenticated pre-write record; recovery must observe, not retry.
        return dict(state='held_after_pending', head=head, reason='direct_result_unknown')
