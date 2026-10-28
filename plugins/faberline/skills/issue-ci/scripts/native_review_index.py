"""Per-PR review-attempt index from authenticated producer metadata and logs."""
import re

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "aw_native_review_index_controller", Path(__file__).with_name("claude_review.py"))
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)

TITLE = re.compile(r"AW Claude review PR ([1-9][0-9]*) head ([0-9a-f]{40})")


def attempts(api, repo, document, runs, log_reader=review.r.job_log):
    """Unknown target history refuses; another PR's newer review is independent."""
    spec = review.policy(document)["producer"]
    meta = api.api(f"repos/{repo}")
    latest = {}
    seen = set()
    invalid_targets = set()
    for run in runs:
        if (run.get("workflow_id") != spec["id"] or run.get("path") != spec["path"]
                or run.get("event") != "workflow_run"
                or run.get("head_repository", {}).get("full_name") != repo
                or run.get("head_branch") != meta["default_branch"]):
            continue
        target = TITLE.fullmatch(run.get("display_title", ""))
        if target is None:
            continue  # No target means no approval or superseding evidence.
        if not (review.integer(run.get("id")) and review.integer(run.get("run_number"))
                and review.integer(run.get("run_attempt"))):
            invalid_targets.add(int(target[1]))
            continue
        key = run["id"], run["run_attempt"]
        candidate = dict(run)
        try:
            source = api.api(f'repos/{repo}/contents/{spec["path"]}?ref={run["head_sha"]}')
            if source.get("sha") != spec["definition_sha"]:
                continue
        except Exception:
            candidate["_native_index_error"] = "review_source_unknown"
        if key in seen:
            candidate["_native_index_error"] = "review_attempt_duplicate"
        seen.add(key)
        if run.get("status") == "completed":
            try:
                identity = review.producer(api, repo, document, run["id"], False)
                jobs = api.pages(f'repos/{repo}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs', "jobs")
                job = next(j for j in jobs if j["id"] == identity["job_id"])
            except Exception:
                candidate["_native_index_error"] = "review_job_identity_unknown"
                job = {"conclusion": "skipped"}
            number, head = int(target[1]), target[2]
            if job.get("conclusion") == "skipped":
                # This is not passing review evidence. A later real attempt
                # still supersedes the earlier one; its verification refuses.
                pass
            else:
                try:
                    records = review.r.records(log_reader(repo, identity["job_id"]), review.TARGET_MARKER)
                    review.need(len(records) == 1 and review.exact(records[0], "version repo pr head")
                        and type(records[0]["version"]) is int and records[0]["version"] == 1
                        and review.integer(records[0]["pr"]) and records[0] == dict(
                            version=1, repo=repo, pr=number, head=head), "review_target_record")
                except Exception:
                    candidate["_native_index_error"] = "review_target_record_unknown"
        review.need(target is not None, "review_target_title_unknown")
        number = int(target[1])
        previous = latest.get(number)
        if previous is None or (run["run_number"], run["run_attempt"]) >= (
                previous["run_number"], previous["run_attempt"]):
            latest[number] = candidate
    for number in invalid_targets:
        latest[number] = {"_native_index_error": "review_attempt_identity_unknown"}
    return latest


def result(api, repo, number, head, load_document, evaluator, run_id,
           log_reader=review.r.job_log):
    document = load_document()

    def latest(values):
        indexed = attempts(api, repo, document, values, log_reader)
        review.need(number in indexed, "review_target_missing")
        review.need(not indexed[number].get("_native_index_error"), "review_target_record_unknown")
        return indexed[number]

    receipt = review.verify_result_receipt(api, repo, number, head, load_document,
        evaluator, run_id, log_reader=log_reader, latest_selector=latest, allow_rejected=True)
    return receipt


def verify(api, repo, number, head, load_document, evaluator, run_id,
           log_reader=review.r.job_log):
    receipt = result(api, repo, number, head, load_document, evaluator, run_id, log_reader)
    review.need(receipt["payload"]["result"]["decision"] == "pass", "review_not_pass")
    return receipt
