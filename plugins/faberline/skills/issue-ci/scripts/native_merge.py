"""Queue GitHub native auto-merge after authenticated review and qualification.

GitHub owns the durable request and final merge. This module never calls the
immediate merge endpoint. A required head status keeps a queued request blocked
until the final fresh verification. No PR checkout, model, or PR artifact runs.
"""
import os
import re
import secrets
from urllib.parse import quote

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "aw_native_claude_review", Path(__file__).with_name("claude_review.py"))
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)
need = review.need

QUEUE = """mutation($input:EnablePullRequestAutoMergeInput!){
  enablePullRequestAutoMerge(input:$input){pullRequest{
    id state headRefOid baseRefOid baseRefName
    autoMergeRequest{mergeMethod enabledAt enabledBy{login __typename} commitHeadline commitBody}
    mergeCommit{oid}
  }}
}"""
READ = """query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){pullRequest(number:$number){
    id state headRefOid baseRefOid baseRefName isDraft
    autoMergeRequest{mergeMethod enabledAt enabledBy{login __typename} commitHeadline commitBody}
    mergeCommit{oid}
  }}
}"""
PROVIDER_REPOSITORY_POLICY = """query($owner:String!,$name:String!){
  repository(owner:$owner,name:$name){nameWithOwner autoMergeAllowed squashMergeAllowed
    defaultBranchRef{name target{oid}}}
}"""
PROVIDER_RULESET_POLICY = """query($owner:String!,$name:String!,$id:Int!){
  repository(owner:$owner,name:$name){nameWithOwner ruleset(databaseId:$id){
    databaseId enforcement target bypassActors(first:1){totalCount pageInfo{hasNextPage}}
  }}
}"""
DISABLE = """mutation($input:DisablePullRequestAutoMergeInput!){
  disablePullRequestAutoMerge(input:$input){pullRequest{id state headRefOid
    autoMergeRequest{mergeMethod enabledBy{login __typename}} mergeCommit{oid}
  }}
}"""
EVENTS = ("workflow_run", "issues", "pull_request_target", "repository_dispatch")
TRIGGERS = {
    "workflow_run": {"types": ["requested", "in_progress", "completed"]},
    "issues": {"types": ["edited", "reopened", "closed", "labeled", "unlabeled"]},
    "pull_request_target": {"types": ["opened", "reopened", "synchronize", "edited", "ready_for_review", "converted_to_draft"]},
    "repository_dispatch": {"types": ["aw-verified-merge-reconcile"]},
}


def policy(document, require_qualification=True):
    review.policy(document)
    config = document["config"]
    review.ci.validate_adoption(config)
    handoff_policy(config)
    if require_qualification:
        need(config.get("lifecycle_mode") == "full"
             and config.get("qualification_blockers") == [], "qualification_hold")
    p = config.get("native_auto_merge")
    need(review.exact(p, "version enabled status_context status_app_id merge_method ruleset_id producer")
         and type(p["version"]) is int and p["version"] == 1
         and p["enabled"] is True and p["status_context"] == "claude/verified-merge"
         and review.integer(p["status_app_id"]) and review.integer(p["ruleset_id"])
         and p["merge_method"] == "SQUASH", "native_merge_policy")
    producer = p["producer"]
    prior = review.policy(document)
    need(review.exact(producer, "path id definition_sha job")
         and review.integer(producer["id"]) and review.sha(producer["definition_sha"])
         and isinstance(producer["path"], str)
         and producer["path"] == ".github/workflows/issue-ci.yml"
         and review.text(producer["job"], 200)
         and producer["path"] not in {w["path"] for w in config["workflows"]}
         and producer["path"] != prior["producer"]["path"]
         and producer["id"] != prior["producer"]["id"], "native_producer_policy")
    return p


def handoff_policy(config):
    """Keep old CI journal identities when only review metadata is added."""
    prior = config.get("ci_handoff_policy", config)
    need(isinstance(prior, dict) and (prior is config or "ci_handoff_policy" not in prior),
         "ci_policy_snapshot")
    review.ci.validate_config(prior)
    need(all(prior.get(key) == config.get(key) for key in
             ("workflows", "lifecycle_mode", "qualification_blockers", "qualification")),
         "ci_policy_semantics_changed")
    return prior


def writer_contract(api, repo, document, evaluator):
    """Bind the shared lifecycle mutex and the single data-only writer step."""
    p = policy(document, require_qualification=False)
    spec = p["producer"]
    raw = review.blob(api, repo, spec["path"], document["source_commit"], spec["definition_sha"])
    review.r.literal_uses(raw)  # Reject duplicate keys, aliases and mutable uses.
    import yaml
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    need(workflow.get("concurrency") == dict(group="issue-ci-handoff", **{"cancel-in-progress": "false"}),
         "lifecycle_mutex_missing")
    need(set(workflow.get("jobs", {})) == {spec["job"]}, "writer_jobs")
    job = workflow["jobs"][spec["job"]]
    need(set(job) == {"runs-on", "timeout-minutes", "permissions", "steps"}
         and job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == "30"
         and job["permissions"] == {"actions": "read", "contents": "write",
             "pull-requests": "write", "statuses": "write", "issues": "write"}
         and job["steps"] == [{"uses": "faberline/aw/.github/actions/native-auto-merge@" + evaluator}],
         "writer_contract")
    trigger = workflow.get("on")
    prior = review.policy(document)["producer"]
    source = review.blob(api, repo, prior["path"], document["source_commit"], prior["definition_sha"])
    review.r.literal_uses(source)
    producer = yaml.load(source, Loader=yaml.BaseLoader)
    names = []
    for gate in document["config"]["workflows"]:
        path = gate["path"]
        raw = review.blob(api, repo, path, document["source_commit"],
                          review.policy(document)["gates"][path]["definition_sha"])
        review.r.literal_uses(raw)
        names.append(yaml.load(raw, Loader=yaml.BaseLoader)["name"])
    need(producer.get("name") == "Claude final review", "writer_review_trigger")
    expected = {k: dict(v) for k, v in TRIGGERS.items()}
    expected["workflow_run"]["workflows"] = names + [producer["name"]]
    expected["pull_request_target"]["branches"] = [api.api(f"repos/{repo}")["default_branch"]]
    need(trigger == expected, "writer_trigger")


def writer_identity(api, repo, document, run_id):
    """Authenticate PR-target source from GitHub's base workflow context.

    Run metadata may name a fork's head. It never selects source or code here.
    Other events retain the ordinary default-branch producer checks.
    """
    spec = policy(document, require_qualification=False)["producer"]
    event = os.environ["GITHUB_EVENT_NAME"]
    if event != "pull_request_target":
        return review.producer_identity(api, repo, document, run_id, False, spec, EVENTS)
    meta = api.api(f"repos/{repo}")
    default = meta["default_branch"]
    source = os.environ.get("GITHUB_WORKFLOW_SHA")
    need(review.sha(source) and os.environ.get("GITHUB_WORKFLOW_REF") == f'{repo}/{spec["path"]}@refs/heads/{default}'
         and os.environ.get("GITHUB_REF") == f"refs/heads/{default}", "writer_base_workflow_context")
    run = api.api(f"repos/{repo}/actions/runs/{run_id}")
    need(run.get("id") == run_id and run.get("workflow_id") == spec["id"]
         and run.get("path") == spec["path"] and run.get("event") == event
         and review.integer(run.get("run_attempt")), "writer_run_identity")
    review.blob(api, repo, spec["path"], source, spec["definition_sha"])
    review.blob(api, repo, spec["path"], document["source_commit"], spec["definition_sha"])
    jobs = api.pages(f'repos/{repo}/actions/runs/{run_id}/attempts/{run["run_attempt"]}/jobs', "jobs")
    need(len([j for j in jobs if j.get("name") == spec["job"] and review.integer(j.get("id"))]) == 1,
         "writer_job_identity")


def reconcile_ci(api, repo, document, pulls, process):
    """The same writer reconciles existing CI and Claude on every event.

    A replaced pending CI or outcome wake therefore loses neither operation.
    Preserve the existing managed-branch and closing-link selection exactly.
    """
    rows = []
    for pull in sorted(pulls, key=lambda p: p["number"]):
        head = pull.get("head", {})
        match = re.fullmatch(r"(?:issue/|agy/issue-)([0-9]+)", head.get("ref", ""))
        if (not match or (head.get("repo") or {}).get("full_name") != repo
                or not re.search(rf'(?im)^Closes #{int(match[1])}\s*$', pull.get("body") or "")):
            continue
        row = dict(pr=pull["number"], head=head["sha"], state="ci_processed")
        args = SimpleNamespace(repo=repo, pr=pull["number"], head=head["sha"], apply=True, run_id=None)
        try:
            process(args, handoff_policy(document["config"]), check_event=False)
        except Exception:
            row.update(state="ci_held", reason="ci_handoff_refused_or_unknown")
        rows.append(row)
    return rows


def graphql(api, query, variables):
    result = api.api("graphql", method="POST", data=dict(query=query, variables=variables))
    need(isinstance(result, dict) and not result.get("errors")
         and isinstance(result.get("data"), dict), "provider_graphql")
    return result["data"]


def provider_pull(api, repo, number):
    owner, name = repo.split("/")
    result = graphql(api, READ, dict(owner=owner, name=name, number=number))
    pull = result.get("repository", {}).get("pullRequest")
    need(isinstance(pull, dict) and review.text(pull.get("id"), 200)
         and review.sha(pull.get("headRefOid")), "provider_pr")
    return pull


def actions_request(request):
    """Authenticate the GitHub GraphQL Actions bot, not a REST creator name."""
    if not isinstance(request, dict):
        return False
    actor = request.get("enabledBy")
    return isinstance(actor, dict) and actor.get("__typename") == "Bot" and actor.get("login") == "github-actions"


def withdraw_approval(api, repo, number, head):
    """Revoke our prior authorization before attempting any fresh verification.

    A policy/base/issue/index failure must not leave a previous passing head
    status or our native request behind. Reconciliation calls this for the
    complete open queue before building its review index. No blind retries.
    """
    pull = provider_pull(api, repo, number)
    need(pull.get("state") == "OPEN" and pull["headRefOid"] == head, "withdraw_target_changed")
    statuses = api.pages(f"repos/{repo}/commits/{head}/statuses")
    need(isinstance(statuses,list) and all(isinstance(item,dict) for item in statuses),"withdraw_status_unknown")
    owned = [s for s in statuses if isinstance(s.get("context"),str)
             and s['context'].casefold() == "claude/verified-merge"
             and s.get("creator", {}).get("login") == "github-actions[bot]"]
    need(all(review.integer(item.get('id')) for item in owned)
         and len({item['id'] for item in owned})==len(owned),"withdraw_status_unknown")
    prior = owned[0] if owned else None  # GitHub returns statuses newest first.
    request = pull.get("autoMergeRequest")
    ours = actions_request(request)
    if not ours and (prior is None or prior.get("state") != "success"):
        return
    api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
        state="pending", context="claude/verified-merge",
        description="Previous approval withdrawn; fresh verification required"))
    again = provider_pull(api, repo, number)
    need(again.get("state") == "OPEN" and again["headRefOid"] == head, "withdraw_target_changed")
    if ours and again.get("autoMergeRequest") is not None:
        need(actions_request(again["autoMergeRequest"]), "withdraw_request_changed")
        result = graphql(api, DISABLE, dict(input=dict(pullRequestId=again["id"])))
        observed = result.get("disablePullRequestAutoMerge", {}).get("pullRequest")
        need(isinstance(observed, dict) and observed.get("headRefOid") == head
             and observed.get("state") == "OPEN" and observed.get("autoMergeRequest") is None,
             "withdraw_result_unknown")


def binding(pull, context):
    need(pull.get("state") == "OPEN" and pull.get("isDraft") is False
         and pull.get("headRefOid") == context["head"]
         and pull.get("baseRefOid") == context["base"]
         and pull.get("baseRefName") == context["base_ref"], "provider_pr_changed")


def queued_request(pull, p, headline, body):
    request = pull.get("autoMergeRequest")
    need(isinstance(request, dict) and request.get("mergeMethod") == p["merge_method"]
         and actions_request(request)
         and request.get("commitHeadline") == headline and request.get("commitBody") == body,
         "native_request_missing_or_changed")


def base_rules(api, repo, context, p):
    meta = api.api(f"repos/{repo}")
    need(meta.get("default_branch") == context["base_ref"]
         and all(meta.get(key) is None or meta.get(key) is True
                 for key in ("allow_auto_merge", "allow_squash_merge")),
         "provider_repository_policy")
    if meta.get("allow_auto_merge") is not True or meta.get("allow_squash_merge") is not True:
        owner, name = repo.split("/")
        result = graphql(api, PROVIDER_REPOSITORY_POLICY, dict(owner=owner, name=name))
        repository = result.get("repository")
        need(isinstance(repository, dict) and repository.get("nameWithOwner") == repo
             and repository.get("autoMergeAllowed") is True and repository.get("squashMergeAllowed") is True
             and isinstance(repository.get("defaultBranchRef"), dict)
             and repository["defaultBranchRef"].get("name") == context["base_ref"]
             and isinstance(repository["defaultBranchRef"].get("target"), dict)
             and repository["defaultBranchRef"]["target"].get("oid") == context["base"],
             "provider_repository_policy")
    rules = api.api(f'repos/{repo}/rules/branches/{quote(context["base_ref"], safe="")}')
    need(isinstance(rules, list), "provider_rules_unknown")
    matches = [rule for rule in rules if rule.get("type") == "required_status_checks"
               and rule.get("ruleset_id") == p["ruleset_id"]]
    need(len(matches) == 1, "required_merge_rule_missing")
    parameters = matches[0].get("parameters", {})
    checks = parameters.get("required_status_checks", [])
    need(parameters.get("strict_required_status_checks_policy") is True
         and isinstance(checks, list)
         and dict(context=p["status_context"], integration_id=p["status_app_id"]) in checks,
         "required_head_status_missing")
    ruleset = api.api(f'repos/{repo}/rulesets/{p["ruleset_id"]}')
    need(ruleset.get("enforcement") == "active" and ruleset.get("target") == "branch",
         "provider_bypass_or_unknown")
    actors = ruleset.get("bypass_actors")
    if actors is None:
        owner, name = repo.split("/")
        result = graphql(api, PROVIDER_RULESET_POLICY, dict(owner=owner, name=name, id=p["ruleset_id"]))
        repository = result.get("repository")
        item = repository.get("ruleset") if isinstance(repository, dict) else None
        bypass = item.get("bypassActors") if isinstance(item, dict) else None
        need(isinstance(repository, dict) and repository.get("nameWithOwner") == repo
             and isinstance(item, dict) and type(item.get("databaseId")) is int
             and item["databaseId"] == p["ruleset_id"] and item.get("enforcement") == "ACTIVE"
             and item.get("target") == "BRANCH" and isinstance(bypass, dict)
             and type(bypass.get("totalCount")) is int and bypass["totalCount"] == 0
             and isinstance(bypass.get("pageInfo"), dict) and bypass["pageInfo"].get("hasNextPage") is False,
             "provider_bypass_or_unknown")
    else:
        need(actors == [], "provider_bypass_or_unknown")
    return dict(base_ref=context["base_ref"], base=context["base"],
                auto_merge_allowed=True, squash_merge_allowed=True,
                ruleset_id=p["ruleset_id"], enforcement="active", target="branch",
                strict_required_status=True, status_context=p["status_context"],
                status_app_id=p["status_app_id"], bypass_actor_count=0)


def qualification(api, repo, number, context, document):
    return _qualification(api,repo,number,context,document,review.pull_identity)


def qualification_merged(api, repo, number, context, document):
    """Recheck full qualification after merge without permitting a new merge."""
    return _qualification(api,repo,number,context,document,review.merged_pull_identity)


def _qualification(api, repo, number, context, document, identity):
    pull = api.api(f"repos/{repo}/pulls/{number}")
    identity(pull, repo, number, context["head"])
    need(pull["base"]["sha"] == context["base"]
         and pull["base"]["ref"] == context["base_ref"], "base_changed")
    linked = review.linked_issue(pull)
    need(review.r.digest([pull.get("title"), pull.get("body")]) == context["pr_description_sha256"],
         "pr_description_changed")
    issue = api.api(f"repos/{repo}/issues/{linked}")
    need(issue.get("number") == linked and issue.get("state") == "open" and "pull_request" not in issue
         and not {label["name"] for label in issue.get("labels", [])} & {"blocked", "agy:blocked"},
         "issue_not_eligible")
    need(review.r.digest({k: issue[k] for k in ("number", "title", "body")}) == context["issue_sha256"],
         "issue_contract_changed")
    scoped = api.qualify_config(repo, pull, document["config"])
    snapshots = api.snapshot(repo, context["head"], scoped)
    state, _, _ = api.evaluate_policy(scoped, snapshots)
    need(state == "done", "qualification_not_ready")
    return linked


def prepare_close_record(api, repo, number, head, document, evaluator, review_run, receipt):
    """Prepare an exact checkpoint only in a trusted default-branch wake."""
    event = os.environ.get('GITHUB_EVENT_NAME')
    run, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    need(os.environ.get('GITHUB_ACTIONS') == 'true'
         and event in ('issues', 'workflow_run', 'repository_dispatch')
         and os.environ.get('GITHUB_REPOSITORY') == repo
         and os.environ.get('GITHUB_WORKFLOW_SHA') == document['source_commit']
         and review.sha(document['source_commit']) and review.sha(evaluator) and review.integer(review_run)
         and re.fullmatch(r'[1-9][0-9]*', run) and re.fullmatch(r'[1-9][0-9]*', attempt),
         'close_source_context')
    need(receipt.get('payload', {}).get('producer', {}).get('run_id') == review_run,
         'close_review_run_changed')
    pull = api.api(f'repos/{repo}/pulls/{number}')
    issue_number = review.linked_issue(pull)
    issue = api.api(f'repos/{repo}/issues/{issue_number}')
    observation = review.sibling('post_merge_observation')
    context=receipt['payload']['context']
    baseline=observation.stable_ownership(api,repo,number,head,context['base'],context['base_ref'],pull,issue,
                                          context['issue_sha256'],context['pr_description_sha256'])
    return observation.capture_verified(repo, number, head, pull, issue, receipt,
        int(run), int(attempt), document['source_commit'], evaluator,ownership_events=baseline)


def dispatch_default_wake(api, repo, results):
    """Wake one default-branch reconciliation for any deferred candidates."""
    if not any(row.get('state') == 'default_wake_required' for row in results):
        return False
    api.api(f'repos/{repo}/dispatches', method='POST',
            data=dict(event_type='aw-verified-merge-reconcile'))
    return True


def dispatch_recovery_wake(api,repo,document,evaluator,recovery,results):
    """Wake recovery after a guarded request; its inputs never authorize close."""
    if not any(row.get('state') in ('queued','merged','already_merged') for row in results):
        return False
    recovery.workflow_contract(api,repo,document,evaluator)
    api.api(f'repos/{repo}/dispatches',method='POST',data=dict(event_type='aw-verified-merge-recover'))
    return True


def enqueue(api, repo, number, head, load_document, evaluator, review_run,
            verify=review.verify_review, prepare_record=None, guarded_claim=None):
    """Native request + required head status form the cross-run write protocol.

    The caller must be the pinned merge producer in the repository-wide
    serialized workflow. It re-evaluates every candidate on each review wake.
    Unknown mutation results are observed, never blindly retried here.
    """
    need(not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "model_credential_in_merge")
    need(guarded_claim is None or prepare_record is not None,'guard_record_required')
    document = load_document()
    p = policy(document)
    before = provider_pull(api, repo, number)
    if before.get("state") == "MERGED":
        need(before["headRefOid"] == head and review.sha(before.get("mergeCommit", {}).get("oid")),
             "merged_identity_unknown")
        return dict(state="already_merged", head=head, merge=before["mergeCommit"]["oid"])
    receipt = verify(api, repo, number, head, load_document, evaluator, review_run)
    context = receipt["payload"]["context"]
    need(context.get("repo") == repo and context.get("pr") == number
         and context.get("head") == head, "review_target")
    linked = qualification(api, repo, number, context, document)
    base_rules(api, repo, context, p)
    binding(before, context)
    headline, body = f"Merge PR #{number}", f"Closes #{linked}"
    request = before.get("autoMergeRequest")
    need(request is None or request.get("mergeMethod") == p["merge_method"],
         "existing_merge_method_conflict")
    if request is not None:
        queued_request(before, p, headline, body)
    target=f"https://github.com/{repo}/actions/runs/{review_run}"
    prepared_module = None
    nonce = None
    if guarded_claim is not None:
        prepared_module=review.sibling('prepared_status')
        intent=guarded_claim['intent']['payload'];nonce=secrets.token_hex(16)
        target=prepared_module.target_identity(repo,review_run,intent['run'],intent['attempt'],nonce)
    # Block this exact head before creating a native request. A different head
    # has no passing status from this operation and cannot inherit its approval.
    pending_response=api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
        state="pending", context=p["status_context"],
        description="Rechecking authenticated review and qualified CI",
        target_url=target))
    try:
        current = provider_pull(api, repo, number)
        binding(current, context)
        need(load_document() == document, "policy_changed")
        if current.get("autoMergeRequest") is None:
            result = graphql(api, QUEUE, dict(input=dict(
                pullRequestId=before["id"], expectedHeadOid=head,
                mergeMethod=p["merge_method"], clientMutationId=receipt["sha256"],
                commitHeadline=headline, commitBody=body)))
            queued = result.get("enablePullRequestAutoMerge", {}).get("pullRequest")
            need(isinstance(queued, dict) and queued.get("headRefOid") == head,
                 "native_request_response")
            queued_request(queued, p, headline, body)
        else:
            queued_request(current, p, headline, body)
        # Repeat source/CI/issue/producer verification after queueing. Passing
        # status is the final write. An abandoned native request stays blocked.
        fresh = verify(api, repo, number, head, load_document, evaluator, review_run)
        need(fresh == receipt and load_document() == document, "review_or_policy_changed")
        need(qualification(api, repo, number, context, document) == linked, "linked_issue_changed")
        base_rules(api, repo, context, p)
        final = provider_pull(api, repo, number)
        binding(final, context)
        queued_request(final, p, headline, body)
        checkpoint = (prepare_record(api, repo, number, head, document, evaluator, review_run, fresh)
                      if prepare_record is not None else None)
        if guarded_claim is not None:
            prepared=prepared_module.build_guarded(checkpoint,guarded_claim['policy'],guarded_claim['intent'],
                                                    pending_response,nonce)
            print(prepared_module.MARKER+review.r.canonical(prepared),flush=True)
        response = api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
            state="success", context=p["status_context"],
            description="Exact-head Claude PASS and qualified CI verified",
            target_url=target))
        if checkpoint is not None:
            if guarded_claim is None:
                observation = review.sibling('post_merge_observation')
                print(observation.MARKER + review.r.canonical(checkpoint), flush=True)
            else:
                per_pr = review.sibling('per_pr_checkpoint')
                bound = per_pr.build(checkpoint,guarded_claim['policy'],guarded_claim['intent'],response,target_url=target)
                print(per_pr.MARKER + review.r.canonical(bound),flush=True)
    except Exception:
        # A response can be lost after a write. Observe provider state on the
        # next wake; never retry a queue or success write in this attempt.
        return dict(state="held_after_pending", head=head,
                    reason="verification_or_provider_result_unknown")
    after = provider_pull(api, repo, number)
    need(after["headRefOid"] == head, "head_changed_after_status")
    if after.get("state") == "MERGED":
        need(review.sha(after.get("mergeCommit", {}).get("oid")), "merge_result_missing")
        return dict(state="merged", head=head, merge=after["mergeCommit"]["oid"])
    need(after.get("state") == "OPEN"
         and after.get("autoMergeRequest", {}).get("mergeMethod") == p["merge_method"],
         "native_request_unknown")
    return dict(state="queued", head=head)


def return_todo(api, repo, number, head, load_document, evaluator, run_id, verify):
    """Return one authenticated negative receipt once, with durable recovery.

    The serialized lifecycle writer uses pending/ready/applied checkpoints,
    as CI handoff does. A later worker can consume a sole todo label; replay
    must never undo that claim. A CI failure or absent receipt is not a review.
    """
    receipt = verify(api, repo, number, head, load_document, evaluator, run_id)
    need(receipt["payload"]["result"]["decision"] in ("fail", "block"), "review_not_rejected")
    context = receipt["payload"]["context"]

    def current():
        fresh = verify(api, repo, number, head, load_document, evaluator, run_id)
        need(fresh == receipt, "review_changed")
        pull = api.api(f"repos/{repo}/pulls/{number}")
        review.pull_identity(pull, repo, number, head)
        linked = review.linked_issue(pull)
        issue = api.api(f"repos/{repo}/issues/{linked}")
        need(issue.get("number") == linked and issue.get("state") == "open"
             and "pull_request" not in issue, "issue_not_open")
        need(review.r.digest({k: issue[k] for k in ("number", "title", "body")})
             == context["issue_sha256"], "issue_contract_changed")
        names = {label["name"] for label in issue.get("labels", [])}
        need(not names & {"blocked", "agy:blocked"}, "issue_blocked")
        need(bool(names & {"todo", "doing", "done"}), "issue_unmanaged")
        return issue["number"], names

    issue, labels = current()
    api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
        state="failure", context="claude/verified-merge", description="Claude review rejected this exact head",
        target_url=f"https://github.com/{repo}/actions/runs/{run_id}"))
    digest = review.r.digest([repo, number, head, issue, receipt])
    prefix = f"<!-- claude-return-todo:{digest}:"

    def body(phase):
        return (f"{prefix}{phase} -->\nClaude: todo for PR #{number} head `{head}`.\n"
                f"Receipt: {receipt['sha256']}\n"
                f"https://github.com/{repo}/actions/runs/{run_id}")

    comments = api.pages(f"repos/{repo}/issues/{issue}/comments")
    journals = [comment for comment in comments
                if comment.get("user", {}).get("login") == "github-actions[bot]"
                and isinstance(comment.get("body"), str) and comment["body"].startswith(prefix)]
    need(len(journals) <= 1, "todo_journal_ambiguous")
    journal = journals[0] if journals else None
    phase = "pending"
    if journal is not None:
        matched = [value for value in ("pending", "ready", "applied") if journal["body"] == body(value)]
        need(len(matched) == 1, "todo_journal_invalid")
        phase = matched[0]
        if phase == "applied":
            current()
            return dict(state="already_returned_todo", issue=issue, head=head,
                        decision=receipt["payload"]["result"]["decision"])
    if journal is None:
        _, labels = current()  # History pagination may outlast a lifecycle change.
        phase = "ready" if "todo" in labels else "pending"
        journal = api.api(f"repos/{repo}/issues/{issue}/comments", method="POST", data={"body": body(phase)})
    comment_path = f"repos/{repo}/issues/comments/{journal['id']}"
    if phase == "pending":
        _, labels = current()
        if "todo" not in labels:
            response = api.api(f"repos/{repo}/issues/{issue}/labels", method="POST", data={"labels": ["todo"]})
            need(isinstance(response, list)
                 and any(isinstance(label, dict) and label.get("name") == "todo" for label in response),
                 "todo_write_unknown")
        current()  # Do not checkpoint a changed review/issue contract.
        api.api(comment_path, method="PATCH", data={"body": body("ready")})
    # A ready checkpoint proves that todo was supplied before cleanup. Its
    # absence now means a later actor consumed it. Never put it back.
    for name in ("doing", "done"):
        _, labels = current()
        if "todo" not in labels:
            break
        if name in labels:
            api.api(f"repos/{repo}/issues/{issue}/labels/{quote(name, safe='')}", method="DELETE")
    _, labels = current()
    need("todo" not in labels or labels & {"todo", "doing", "done"} == {"todo"}, "todo_result_unknown")
    api.api(comment_path, method="PATCH", data={"body": body("applied")})
    return dict(state="returned_todo", issue=issue, head=head,
                decision=receipt["payload"]["result"]["decision"])


def reconcile(api, repo, load_document, evaluator, index, verify, result_verify=None, ci_process=None,
              defer_positive=False, prepare_record=None, guarded=None):
    """Every wake covers the complete queue; one refused PR cannot drop others."""
    document = load_document()
    policy(document, require_qualification=False)
    producer = review.policy(document)["producer"]
    pulls = api.pages(f"repos/{repo}/pulls?state=open&sort=created&direction=asc")
    admitted = {}
    if guarded is not None:
        need(isinstance(pulls,list) and all(isinstance(pull,dict) and review.integer(pull.get('number'))
             and isinstance(pull.get('head'),dict) and review.sha(pull['head'].get('sha')) for pull in pulls)
             and len({pull['number'] for pull in pulls})==len(pulls),'guard_queue_unknown')
        for pull in pulls:
            number,head=pull.get('number'),pull.get('head',{}).get('sha')
            try:
                admitted[number]=guarded.begin(api,repo,document,evaluator,number,head,
                    lambda line:print(line,flush=True))
            except Exception:
                # An unknown claim may have succeeded; never retry it here.
                continue
    withdrawal_errors = set()
    for pull in pulls:
        if guarded is not None and pull.get('number') not in admitted:
            continue
        try:
            withdraw_approval(api, repo, pull["number"], pull.get("head", {}).get("sha"))
        except Exception:
            withdrawal_errors.add(pull["number"])
    ci_errors = set()
    if ci_process is not None:
        ci_rows = reconcile_ci(api, repo, document,
                               [pull for pull in pulls if pull["number"] not in withdrawal_errors
                                and (guarded is None or pull["number"] in admitted)], ci_process)
        ci_errors = {row["pr"] for row in ci_rows if row["state"] == "ci_held"}
        print("AW_UNIFIED_CI_RECONCILIATION_V1 " + review.r.canonical(ci_rows))
    runs = api.pages(f'repos/{repo}/actions/workflows/{producer["id"]}/runs', "workflow_runs")
    attempts = index(api, repo, document, runs, {pull["number"]:pull.get("head",{}).get("sha") for pull in pulls})
    results = []
    for pull in sorted(pulls, key=lambda p: p["number"]):
        number, head = pull["number"], pull.get("head", {}).get("sha")
        row = dict(pr=number, head=head, state="held")
        try:
            if guarded is not None and number not in admitted:
                row.update(reason='guard_unavailable')
                results.append(row)
                continue
            if number in withdrawal_errors or number in ci_errors:
                row.update(state="held_after_pending", reason="withdrawal_or_ci_result_unknown")
                results.append(row)
                continue
            attempt = attempts.get(number)
            need(attempt is not None, "review_missing")
            need(not attempt.get("_native_index_error"), "review_target_record_unknown")
            need(attempt.get("status") == "completed"
                 and attempt.get("conclusion") in ("success", "failure"), "latest_review_not_success")
            if result_verify is not None:
                receipt = result_verify(api, repo, number, head, load_document, evaluator, attempt["id"])
                if receipt["payload"]["result"]["decision"] in ("fail", "block"):
                    row.update(return_todo(api, repo, number, head, load_document, evaluator,
                        attempt["id"], result_verify))
                    results.append(row)
                    continue
            if defer_positive:
                need(result_verify is not None and receipt['payload']['result']['decision'] == 'pass',
                     'review_not_pass')
                row.update(state="default_wake_required")
            else:
                arguments=dict(verify=verify,prepare_record=prepare_record)
                if guarded is not None:arguments['guarded_claim']=admitted[number]
                row.update(enqueue(api, repo, number, head, load_document, evaluator,
                    attempt["id"], **arguments))
        except Exception as error:
            code = str(error)
            row["reason"] = code if isinstance(error, review.Refused) and re.fullmatch(
                r"[a-z0-9_]+", code) else "verification_or_provider_failed"
        results.append(row)
    if guarded is not None:
        safe_holds={'review_missing','review_target_record_unknown','latest_review_not_success'}
        for row in results:
            claim=admitted.get(row['pr'])
            if claim is None or row['state'] in ('queued','merged','already_merged','held_after_pending',
                                                  'default_wake_required'):
                continue
            if row['state']=='held' and row.get('reason') not in safe_holds:
                continue
            try:
                result=guarded.settle(api,repo,claim['policy'],claim['intent'])
                if result.get('state') not in ('released','retained'):
                    row.update(state='held_after_pending',reason='guard_result_unknown')
                else:
                    row['guard']=result['state']
            except Exception:
                # The provider could have accepted the release. Do not retry.
                row.update(state='held_after_pending',reason='guard_result_unknown')
    return results


def main():
    need(not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "model_credential_in_merge")
    need(os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("GITHUB_EVENT_NAME") in EVENTS, "trusted_merge_wake_required")
    repo, evaluator = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("AW_ACTION_REF")
    current = os.environ.get("GITHUB_RUN_ID", "")
    need(isinstance(repo, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
         and review.sha(evaluator) and re.fullmatch(r"[1-9][0-9]*", current), "merge_caller_identity")
    api = review.ci
    load = lambda: review.action.trusted_policy(api, repo,
        ".github/issue-ci-required-gates.json", metadata=True)
    document = load()
    native = policy(document, require_qualification=False)
    writer_identity(api, repo, document, int(current))
    writer_contract(api, repo, document, evaluator)
    event = api.strict_json_loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    wake = event.get("workflow_run", {})
    p = review.policy(document)["producer"]
    event_name = os.environ["GITHUB_EVENT_NAME"]
    need(event.get("repository", {}).get("full_name") == repo
         and event.get("action") in TRIGGERS[event_name]["types"], "merge_wake_identity")
    if event_name == "workflow_run":
        identities = {p["path"]: dict(id=p["id"]), **review.policy(document)["gates"]}
        source = identities.get(wake.get("path"))
        need(source is not None and wake.get("workflow_id") == source["id"]
             and review.integer(wake.get("id")), "merge_wake_identity")
    guarded = None
    if 'issue_guard' in document['config']:
        # The draft verifier always holds until actor rollout and recovery are
        # proved. No guarded lifecycle mutation may run before that proof.
        recovery = review.sibling('recovery_entry')
        recovery.verify_rollout(api,repo,document)
        if event_name == 'pull_request_target':
            api.api(f'repos/{repo}/dispatches',method='POST',data=dict(event_type='aw-verified-merge-reconcile'))
            print('AW_NATIVE_MERGE_RECONCILIATION_V1 '+review.r.canonical(dict(
                version=1,repo=repo,producer_run=int(current),results=[dict(state='default_wake_required')])))
            return 0
        guarded = review.sibling('guarded_admission')
    index = review.sibling("native_review_index")
    tracker = index.Session(api, repo, load, evaluator)
    results = reconcile(api, repo, load, evaluator, tracker.attempts, tracker.verify, tracker.result, api.process_head,
                        defer_positive=event_name == 'pull_request_target', prepare_record=prepare_close_record,
                        guarded=guarded)
    # PR-target runs may report the candidate head as their run SHA. Wake a
    # default-branch writer, which must revalidate the entire queue.
    dispatch_default_wake(api, repo, results)
    if guarded is not None:
        dispatch_recovery_wake(api,repo,document,evaluator,recovery,results)
    print("AW_NATIVE_MERGE_RECONCILIATION_V1 " + review.r.canonical(dict(
        version=1, repo=repo, producer_run=int(current), results=results)))
    return 2 if any(row["state"] == "held_after_pending" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
