"""Queue GitHub native auto-merge after authenticated review and qualification.

GitHub owns the durable request and final merge. This module never calls the
immediate merge endpoint. A required head status keeps a queued request blocked
until the final fresh verification. No PR checkout, model, or PR artifact runs.
"""
import os
import re
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
    autoMergeRequest{mergeMethod enabledAt enabledBy{login} commitHeadline commitBody}
    mergeCommit{oid}
  }}
}"""
READ = """query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){pullRequest(number:$number){
    id state headRefOid baseRefOid baseRefName isDraft
    autoMergeRequest{mergeMethod enabledAt enabledBy{login} commitHeadline commitBody}
    mergeCommit{oid}
  }}
}"""
DISABLE = """mutation($input:DisablePullRequestAutoMergeInput!){
  disablePullRequestAutoMerge(input:$input){pullRequest{id state headRefOid
    autoMergeRequest{mergeMethod enabledBy{login}} mergeCommit{oid}
  }}
}"""
EVENTS = ("workflow_run", "issues", "pull_request_target")
TRIGGERS = {
    "workflow_run": {"types": ["requested", "in_progress", "completed"]},
    "issues": {"types": ["edited", "reopened", "closed", "labeled", "unlabeled"]},
    "pull_request_target": {"types": ["opened", "reopened", "synchronize", "edited", "ready_for_review", "converted_to_draft"]},
}


def policy(document, require_qualification=True):
    review.policy(document)
    config = document["config"]
    review.ci.validate_adoption(config)
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
            process(args, document["config"], check_event=False)
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


def withdraw_approval(api, repo, number, head):
    """Revoke our prior authorization before attempting any fresh verification.

    A policy/base/issue/index failure must not leave a previous passing head
    status or our native request behind. Reconciliation calls this for the
    complete open queue before building its review index. No blind retries.
    """
    pull = provider_pull(api, repo, number)
    need(pull.get("state") == "OPEN" and pull["headRefOid"] == head, "withdraw_target_changed")
    statuses = api.pages(f"repos/{repo}/commits/{head}/statuses")
    owned = [s for s in statuses if s.get("context") == "claude/verified-merge"
             and s.get("creator", {}).get("login") == "github-actions[bot]"]
    prior = max(owned, key=lambda s: s["id"]) if owned else None
    request = pull.get("autoMergeRequest")
    ours = request and (request.get("enabledBy", {}).get("login") == "github-actions[bot]" or prior)
    if not ours and (prior is None or prior.get("state") != "success"):
        return
    api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
        state="pending", context="claude/verified-merge",
        description="Previous approval withdrawn; fresh verification required"))
    again = provider_pull(api, repo, number)
    need(again.get("state") == "OPEN" and again["headRefOid"] == head, "withdraw_target_changed")
    if ours and again.get("autoMergeRequest") is not None:
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
         and request.get("enabledBy", {}).get("login") == "github-actions[bot]"
         and request.get("commitHeadline") == headline and request.get("commitBody") == body,
         "native_request_missing_or_changed")


def base_rules(api, repo, context, p):
    meta = api.api(f"repos/{repo}")
    need(meta.get("default_branch") == context["base_ref"]
         and meta.get("allow_auto_merge") is True
         and meta.get("allow_squash_merge") is True, "provider_repository_policy")
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
    need(ruleset.get("enforcement") == "active" and ruleset.get("target") == "branch"
         and ruleset.get("bypass_actors") == [], "provider_bypass_or_unknown")


def qualification(api, repo, number, context, document):
    pull = api.api(f"repos/{repo}/pulls/{number}")
    review.pull_identity(pull, repo, number, context["head"])
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


def enqueue(api, repo, number, head, load_document, evaluator, review_run,
            verify=review.verify_review):
    """Native request + required head status form the cross-run write protocol.

    The caller must be the pinned merge producer in the repository-wide
    serialized workflow. It re-evaluates every candidate on each review wake.
    Unknown mutation results are observed, never blindly retried here.
    """
    need(not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "model_credential_in_merge")
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
    # Block this exact head before creating a native request. A different head
    # has no passing status from this operation and cannot inherit its approval.
    api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
        state="pending", context=p["status_context"],
        description="Rechecking authenticated review and qualified CI",
        target_url=f"https://github.com/{repo}/actions/runs/{review_run}"))
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
        api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
            state="success", context=p["status_context"],
            description="Exact-head Claude PASS and qualified CI verified",
            target_url=f"https://github.com/{repo}/actions/runs/{review_run}"))
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


def return_doing(api, repo, number, head, load_document, evaluator, run_id, verify):
    """Only a fresh authenticated fail/block returns an open issue to doing.

    All lifecycle writers use issue-ci-handoff. Re-reads make interrupted
    add/remove operations recoverable without replacing unrelated labels.
    A failed CI job or missing review receipt is not a Claude decision.
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
        return issue["number"], names

    issue, labels = current()
    api.api(f"repos/{repo}/statuses/{head}", method="POST", data=dict(
        state="failure", context="claude/verified-merge", description="Claude review rejected this exact head",
        target_url=f"https://github.com/{repo}/actions/runs/{run_id}"))
    if "doing" not in labels:
        api.api(f"repos/{repo}/issues/{issue}/labels", method="POST", data={"labels": ["doing"]})
    for name in ("todo", "done"):
        _, labels = current()
        need("doing" in labels, "doing_write_unknown")
        if name in labels:
            api.api(f"repos/{repo}/issues/{issue}/labels/{quote(name, safe='')}", method="DELETE")
    _, labels = current()
    need(labels & {"todo", "doing", "done"} == {"doing"}, "doing_result_unknown")
    return dict(state="returned_doing", issue=issue, head=head,
                decision=receipt["payload"]["result"]["decision"])


def reconcile(api, repo, load_document, evaluator, index, verify, result_verify=None, ci_process=None):
    """Every wake covers the complete queue; one refused PR cannot drop others."""
    document = load_document()
    policy(document, require_qualification=False)
    producer = review.policy(document)["producer"]
    pulls = api.pages(f"repos/{repo}/pulls?state=open&sort=created&direction=asc")
    withdrawal_errors = set()
    for pull in pulls:
        try:
            withdraw_approval(api, repo, pull["number"], pull.get("head", {}).get("sha"))
        except Exception:
            withdrawal_errors.add(pull["number"])
    ci_errors = set()
    if ci_process is not None:
        ci_rows = reconcile_ci(api, repo, document,
                               [pull for pull in pulls if pull["number"] not in withdrawal_errors], ci_process)
        ci_errors = {row["pr"] for row in ci_rows if row["state"] == "ci_held"}
        print("AW_UNIFIED_CI_RECONCILIATION_V1 " + review.r.canonical(ci_rows))
    runs = api.pages(f'repos/{repo}/actions/workflows/{producer["id"]}/runs', "workflow_runs")
    attempts = index(api, repo, document, runs)
    results = []
    for pull in sorted(pulls, key=lambda p: p["number"]):
        number, head = pull["number"], pull.get("head", {}).get("sha")
        row = dict(pr=number, head=head, state="held")
        try:
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
                    row.update(return_doing(api, repo, number, head, load_document, evaluator,
                        attempt["id"], result_verify))
                    results.append(row)
                    continue
            row.update(enqueue(api, repo, number, head, load_document, evaluator,
                attempt["id"], verify=verify))
        except Exception as error:
            code = str(error)
            row["reason"] = code if isinstance(error, review.Refused) and re.fullmatch(
                r"[a-z0-9_]+", code) else "verification_or_provider_failed"
        results.append(row)
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
    index = review.sibling("native_review_index")
    results = reconcile(api, repo, load, evaluator, index.attempts, index.verify, index.result, api.process_head)
    print("AW_NATIVE_MERGE_RECONCILIATION_V1 " + review.r.canonical(dict(
        version=1, repo=repo, producer_run=int(current), results=results)))
    return 2 if any(row["state"] == "held_after_pending" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
