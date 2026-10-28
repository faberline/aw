"""Authenticated per-target attempt ordering; all data and APIs are synthetic."""
import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import unittest

import test_claude_review as fixtures

_spec = importlib.util.spec_from_file_location(
    "native_index_under_test", Path(__file__).parents[1] / "scripts/native_review_index.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


class History(fixtures.Harness):
    def __init__(self):
        super().__init__()
        self.review_run["display_title"] = f"AW Claude review PR 1 head {fixtures.HEAD}"
        self.run_map = {200: self.review_run}
        self.job_map = {200: self.review_job}
        with contextlib.redirect_stdout(io.StringIO()):
            self.receipt = self.produce()
        self.logs = {201: m.review.TARGET_MARKER + m.review.r.canonical(dict(
            version=1, repo="o/r", pr=1, head=fixtures.HEAD)) + "\n" + self.log}

    def add(self, number, run_id=300, conclusion="success", status="completed"):
        run = dict(self.review_run, id=run_id, run_number=run_id,
            status=status, conclusion=conclusion,
            display_title=f"AW Claude review PR {number} head {fixtures.HEAD}")
        job = dict(self.review_job, id=run_id + 1, status=status, conclusion=conclusion)
        self.history.append(run)
        self.run_map[run_id], self.job_map[run_id] = run, job
        self.logs[run_id + 1] = m.review.TARGET_MARKER + m.review.r.canonical(dict(
            version=1, repo="o/r", pr=number, head=fixtures.HEAD))
        return run

    def api(self, path, method="GET", data=None):
        for run_id, run in self.run_map.items():
            if path == f"repos/o/r/actions/runs/{run_id}": return copy.deepcopy(run)
        return super().api(path, method, data)

    def pages(self, path, key=None):
        for run_id, job in self.job_map.items():
            if path == f"repos/o/r/actions/runs/{run_id}/attempts/1/jobs":
                return [copy.deepcopy(job)]
        return super().pages(path, key)

    def reader(self, repo, job_id):
        return self.logs[job_id]

    def indexed(self):
        return m.attempts(self, "o/r", self.document(), self.history, self.reader)

    def target_verify(self):
        return m.verify(self, "o/r", 1, fixtures.HEAD, self.document,
            fixtures.EVALUATOR, 200, log_reader=self.reader)


class NativeReviewIndexTests(unittest.TestCase):
    def test_newer_other_pr_does_not_hide_a_current_valid_pass(self):
        h = History(); h.add(2)
        self.assertEqual(set(h.indexed()), {1, 2})
        self.assertEqual(h.target_verify(), h.receipt)
        self.assertEqual(h.writes, [])

    def test_newer_failed_skipped_or_pending_same_pr_prevents_old_pass(self):
        for conclusion, status in (("failure", "completed"), ("skipped", "completed"),
                                   (None, "in_progress"), (None, "queued")):
            h = History(); h.add(1, conclusion=conclusion, status=status)
            with self.subTest(conclusion=conclusion), self.assertRaises(RuntimeError):
                h.target_verify()
            self.assertEqual(h.writes, [])

    def test_live_other_target_is_scoped_without_unavailable_partial_logs(self):
        h = History(); h.add(2, conclusion=None, status="in_progress")
        h.logs.pop(301)
        self.assertEqual(h.target_verify(), h.receipt)

    def test_title_and_authenticated_target_record_must_agree(self):
        for mutation in (lambda h: h.review_run.update(display_title="unknown"),
                         lambda h: h.logs.update({201: ""}),
                         lambda h: h.logs.update({201: h.logs[201].replace('"version":1', '"version":true')}),
                         lambda h: h.logs.update({201: h.logs[201].replace('"pr":1', '"pr":true')}),
                         lambda h: h.logs.update({201: h.logs[201] + "\n" + h.logs[201]}),
                         lambda h: h.logs.update({201: h.logs[201].replace('"pr":1', '"pr":2')})):
            h = History(); mutation(h)
            with self.assertRaises(RuntimeError): h.target_verify()
            self.assertEqual(h.writes, [])

    def test_duplicate_run_inventory_is_not_silently_deduplicated(self):
        h = History(); h.history.append(copy.deepcopy(h.review_run))
        with self.assertRaises(RuntimeError): h.indexed()
        self.assertEqual(h.writes, [])

    def test_bad_known_target_record_does_not_poison_another_pr(self):
        h = History(); h.add(2)
        h.logs[301] = "unavailable target record"
        self.assertIn("_native_index_error", h.indexed()[2])
        self.assertEqual(h.target_verify(), h.receipt)

    def test_unassociated_skipped_wake_is_not_passing_review_evidence(self):
        h = History(); run = h.add(2, conclusion="skipped")
        run["display_title"] = "No associated PR"
        self.assertEqual(set(h.indexed()), {1})
        self.assertEqual(h.target_verify(), h.receipt)

    def test_rerun_attempt_is_not_accepted_under_an_older_job_identity(self):
        h = History(); h.review_run["run_attempt"] = 2
        with self.assertRaises((RuntimeError, AssertionError)): h.target_verify()
        self.assertEqual(h.writes, [])

    def test_authenticated_rejection_is_readable_from_failed_review_job(self):
        h = History()
        receipt = h.receipt
        value = receipt["payload"]["result"]
        value.update(decision="fail", fit="missing")
        value["requirement_results"][0]["status"] = "missing"
        receipt["sha256"] = m.review.r.digest(receipt["payload"])
        h.logs[201] = m.review.TARGET_MARKER + m.review.r.canonical(dict(
            version=1, repo="o/r", pr=1, head=fixtures.HEAD)) + "\n" + m.review.MARKER + m.review.r.canonical(receipt)
        h.review_run["conclusion"] = h.review_job["conclusion"] = "failure"
        verified = m.result(h,"o/r",1,fixtures.HEAD,h.document,fixtures.EVALUATOR,200,h.reader)
        self.assertEqual(verified["payload"]["result"]["decision"],"fail")
        with self.assertRaisesRegex(RuntimeError,"review_not_pass"):h.target_verify()
        self.assertEqual(h.writes,[])

    def test_pass_receipt_from_a_failed_job_is_not_a_passing_review(self):
        h=History();h.review_run["conclusion"]=h.review_job["conclusion"]="failure"
        with self.assertRaisesRegex(RuntimeError,"review_not_completed_result"):h.target_verify()
        self.assertEqual(h.writes,[])

    def test_unconfigured_workflow_or_foreign_branch_cannot_supply_target(self):
        for mutation in (lambda r: r.update(workflow_id=999),
                         lambda r: r.update(head_branch="untrusted"),
                         lambda r: r.update(event="push"),
                         lambda r: r.update(head_repository=dict(full_name="foreign/r"))):
            h = History(); mutation(h.review_run)
            self.assertEqual(h.indexed(), {})
            self.assertEqual(h.writes, [])


if __name__ == "__main__":
    unittest.main()
