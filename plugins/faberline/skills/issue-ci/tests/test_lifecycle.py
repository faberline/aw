"""Affected unit tests: no network, cloud jobs, or full-suite execution."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = load('handoff', SKILLS / 'issue-ci/scripts/handoff.py')
work = load('work', SKILLS / 'issue-work/scripts/work.py')
receipt = load('receipt', SKILLS / 'issue-review/scripts/receipt.py')
GATES = [{'path': '.github/workflows/unit.yml', 'jobs': ['unit']},
         {'path': '.github/workflows/cloud.yml', 'jobs': ['functional', 'e2e', 'performance']}]
SHA = 'a' * 40


def evidence():
    result = {}
    for i, w in enumerate(GATES, 1):
        run = dict(id=i, run_number=i, run_attempt=1, status='completed', conclusion='success', html_url=f'https://github.com/o/r/actions/runs/{i}')
        jobs = [dict(name=n, status='completed', conclusion='success', html_url=f'{run["html_url"]}/job/{j}') for j, n in enumerate(w['jobs'])]
        result[w['path']] = run, jobs
    return result


class GateTests(unittest.TestCase):
    def test_all_workflows_required(self):
        snapshots = evidence()
        self.assertEqual(ci.evaluate(GATES, snapshots)[0], 'done')
        snapshots.pop(GATES[1]['path'])
        self.assertEqual(ci.evaluate(GATES, snapshots)[0], 'pending')

    def test_non_success_is_never_pass(self):
        for conclusion in (None, 'cancelled', 'skipped', 'neutral', 'stale'):
            with self.subTest(conclusion=conclusion):
                snapshots = evidence()
                snapshots[GATES[1]['path']][1][0]['conclusion'] = conclusion
                self.assertEqual(ci.evaluate(GATES, snapshots)[0], 'pending')

    def test_pending_and_missing_required_jobs(self):
        snapshots = evidence()
        snapshots[GATES[1]['path']][1].pop()
        self.assertEqual(ci.evaluate(GATES, snapshots)[0], 'pending')
        snapshots = evidence()
        snapshots[GATES[0]['path']][0]['status'] = 'in_progress'
        self.assertEqual(ci.evaluate(GATES, snapshots)[0], 'pending')

    def test_failure_returns_todo_with_exact_links(self):
        snapshots = evidence()
        job = snapshots[GATES[1]['path']][1][1]
        job['conclusion'] = 'failure'
        state, reasons, _ = ci.evaluate(GATES, snapshots)
        self.assertEqual(state, 'todo')
        self.assertIn('e2e', reasons[0])
        self.assertIn(job['html_url'], reasons[0])

    def test_failed_run_with_successful_subset_not_pass(self):
        snapshots = evidence()
        snapshots[GATES[1]['path']][0]['conclusion'] = 'failure'
        self.assertEqual(ci.evaluate(GATES, snapshots)[0], 'todo')

    def test_config_refuses_empty_gates_and_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'gates.json'
            for workflows in ([], [GATES[0], GATES[0]], [dict(path='evil.py', jobs=['run'])], [dict(path=GATES[0]['path'], jobs=[])]):
                path.write_text(json.dumps(dict(workflows=workflows)))
                with self.subTest(workflows=workflows), self.assertRaises(RuntimeError):
                    ci.load_config(path)

    def test_snapshot_filters_foreign_stale_and_selects_latest_attempt(self):
        good = dict(id=3, run_number=2, run_attempt=2, head_sha=SHA, event='pull_request', head_repository={'full_name': 'o/r'}, path=GATES[0]['path'])
        old = dict(good, id=2, run_attempt=1)
        foreign = dict(good, id=99, head_repository={'full_name': 'fork/r'})
        stale = dict(good, id=100, head_sha='b' * 40)
        with patch.object(ci, 'pages', side_effect=[[old, good, foreign, stale], []]) as pages:
            result = ci.snapshot('o/r', SHA, {'workflows': [GATES[0]]})
        self.assertEqual(result[GATES[0]['path']][0]['id'], 3)
        self.assertIn('/runs/3/attempts/2/jobs', pages.call_args_list[1].args[0])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / 'gates.json'
        self.config.write_text(json.dumps({'workflows': GATES}))
        self.pr = dict(state='open', body='Closes #7', head=dict(sha=SHA, ref='issue/7', repo={'full_name': 'o/r'}))
        self.issue = dict(state='open', labels=[{'name': 'doing'}, {'name': 'priority:p1'}])
        self.writes = []
        self.comments = []
        self.pr_reads = 0
        self.move_on = None
        self.trigger = dict(head_sha=SHA, event='pull_request', id=1)

    def api(self, path, method='GET', data=None):
        if method != 'GET':
            self.writes.append((path, method, data))
            if method == 'POST' and path.endswith('/comments'):
                comment = dict(id=len(self.comments) + 1, body=data['body'], user={'login': 'github-actions[bot]'})
                self.comments.append(comment)
                return comment
            if method == 'PATCH' and '/issues/comments/' in path:
                self.comments[int(path.rsplit('/', 1)[1]) - 1]['body'] = data['body']
            if method == 'POST' and path.endswith('/labels'):
                for label in data['labels']:
                    if label not in {l['name'] for l in self.issue['labels']}:
                        self.issue['labels'].append({'name': label})
            if method == 'DELETE' and '/labels/' in path:
                label = path.rsplit('/', 1)[1]
                self.issue['labels'] = [l for l in self.issue['labels'] if l['name'] != label]
            return {}
        if '/pulls/' in path:
            self.pr_reads += 1
            if self.move_on and self.pr_reads >= self.move_on:
                return dict(self.pr, head=dict(self.pr['head'], sha='b' * 40))
            return self.pr
        if '/actions/runs/' in path:
            return self.trigger
        return self.issue

    def invoke(self, snapshots=None, snapshot_sequence=None):
        args = ['handoff.py', '--repo', 'o/r', '--config', str(self.config), '--pr', '8', '--head', SHA, '--run-id', '1', '--apply']
        with patch.object(sys, 'argv', args), patch.object(ci, 'api', side_effect=self.api), patch.object(ci, 'pages', side_effect=lambda *a: self.comments), patch.object(ci, 'snapshot', side_effect=snapshot_sequence, return_value=snapshots or evidence()), contextlib.redirect_stdout(io.StringIO()):
            return ci.main()

    def test_current_head_success_preserves_other_labels(self):
        self.assertEqual(self.invoke(), 0)
        self.assertIn(('repos/o/r/issues/7/labels', 'POST', {'labels': ['done']}), self.writes)
        self.assertIn(('repos/o/r/issues/7/labels/doing', 'DELETE', None), self.writes)
        self.assertFalse(any('/labels/priority' in w[0] for w in self.writes))

    def test_stale_head_before_and_during_evaluation(self):
        for move_on in (1, 2):
            self.move_on, self.pr_reads = move_on, 0
            with self.assertRaises(RuntimeError):
                self.invoke()
            self.assertEqual(self.writes, [])

    def test_head_moved_after_journal_never_labels(self):
        self.move_on = 3
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertTrue(self.writes)
        self.assertTrue(all('/comments' in w[0] for w in self.writes))

    def test_old_event_run_no_mutation(self):
        self.trigger['head_sha'] = 'b' * 40
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertEqual(self.writes, [])

    def test_duplicate_failure_never_undoes_later_worker_claim(self):
        snapshots = evidence()
        snapshots[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        self.invoke(snapshots)
        self.writes.clear()
        self.issue['labels'] = [{'name': 'doing'}]  # later worker claim
        self.invoke(snapshots)
        self.assertEqual(self.writes, [])

    def test_late_workflow_failure_receipt_never_reclaims_coding(self):
        first = evidence()
        first[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        first.pop(GATES[1]['path'])
        self.invoke(first)
        later = evidence()
        later[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        self.writes.clear()
        self.invoke(later)
        self.assertEqual(self.writes, [])

    def test_additional_failed_workflow_cannot_undo_claimed_coding_epoch(self):
        first = evidence()
        first[GATES[0]['path']][0]['conclusion'] = 'failure'
        first[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        first[GATES[1]['path']][0].update(status='in_progress', conclusion=None)
        self.invoke(first)
        self.assertIn('todo', {l['name'] for l in self.issue['labels']})
        self.issue['labels'] = [{'name': 'doing'}, {'name': 'priority:p1'}]
        later = evidence()
        for path in (GATES[0]['path'], GATES[1]['path']):
            later[path][0]['conclusion'] = 'failure'
            later[path][1][0]['conclusion'] = 'failure'
        # Same head, run IDs and attempts; only B's pending result changed.
        self.writes.clear()
        comments_before = len(self.comments)
        self.invoke(later)
        self.assertEqual(self.writes, [])
        self.assertEqual(len(self.comments), comments_before)
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'doing', 'priority:p1'})

    def test_snapshot_progress_after_intent_is_retryable(self):
        first = evidence()
        first[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        changed = evidence()
        changed[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        changed[GATES[1]['path']][0]['run_attempt'] = 2
        with self.assertRaises(RuntimeError):
            self.invoke(first, snapshot_sequence=[first, changed])
        self.assertIn(':pending -->', self.comments[0]['body'])
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'doing', 'priority:p1'})
        self.invoke(changed)
        self.assertIn('todo', {l['name'] for l in self.issue['labels']})
        self.assertNotIn('doing', {l['name'] for l in self.issue['labels']})
        self.assertIn(':applied -->', self.comments[0]['body'])

    def test_ready_checkpoint_survives_api_failure_and_later_worker_claim(self):
        failed = evidence()
        failed[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        actual = self.api
        def fail_delete(path, method='GET', data=None):
            if method == 'DELETE':
                raise RuntimeError('transient API failure')
            return actual(path, method, data)
        self.api = fail_delete
        with self.assertRaises(RuntimeError):
            self.invoke(failed)
        self.assertIn(':ready -->', self.comments[0]['body'])
        self.assertIn('todo', {l['name'] for l in self.issue['labels']})
        self.api = actual
        self.invoke(failed)
        self.assertIn(':applied -->', self.comments[0]['body'])
        self.issue['labels'] = [{'name': 'doing'}]
        self.writes.clear()
        self.invoke(failed)
        self.assertEqual(self.writes, [])

    def test_ready_receipt_never_readds_label_consumed_by_worker(self):
        failed = evidence()
        failed[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        self.invoke(failed)
        self.comments[0]['body'] = self.comments[0]['body'].replace(':applied -->', ':ready -->')
        self.issue['labels'] = [{'name': 'doing'}]
        self.writes.clear()
        self.invoke(failed)
        self.assertTrue(all('/issues/comments/' in path for path, _, _ in self.writes))
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'doing'})

    def test_new_failed_attempt_after_success_is_not_old_failure_receipt(self):
        failed = evidence()
        failed[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        self.invoke(failed)
        self.invoke(evidence())
        self.assertIn('done', {l['name'] for l in self.issue['labels']})
        again = evidence()
        again[GATES[0]['path']][0]['run_attempt'] = 2
        again[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        self.invoke(again)
        self.assertIn('todo', {l['name'] for l in self.issue['labels']})
        self.assertNotIn('done', {l['name'] for l in self.issue['labels']})

    def test_untrusted_marker_cannot_suppress_handoff(self):
        self.invoke()
        self.comments[0]['user']['login'] = 'contributor'
        self.issue['labels'] = [{'name': 'doing'}]
        self.writes.clear()
        self.invoke()
        self.assertTrue(self.writes)

    def test_pending_run_never_mutates(self):
        snapshots = evidence()
        snapshots[GATES[0]['path']][0]['status'] = 'queued'
        self.invoke(snapshots)
        self.assertEqual(self.writes, [])

    def test_manually_blocked_issue_is_untouched(self):
        self.issue['labels'].append({'name': 'blocked'})
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertEqual(self.writes, [])


class FailureOnlyTests(unittest.TestCase):
    api = ApplyTests.api
    invoke = ApplyTests.invoke

    def setUp(self):
        ApplyTests.setUp(self)
        self.config.write_text(json.dumps(dict(
            workflows=GATES, lifecycle_mode='failure-only',
            qualification_blockers=['functional/e2e/performance policy missing'])))

    def failed(self):
        snapshots = evidence()
        snapshots[GATES[0]['path']][1][0]['conclusion'] = 'failure'
        return snapshots

    def test_success_never_writes_done_or_comments(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.writes, [])
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'doing', 'priority:p1'})

    def test_success_without_blockers_still_cannot_write_done(self):
        self.config.write_text(json.dumps(dict(
            workflows=GATES, lifecycle_mode='failure-only', qualification_blockers=[])))
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.writes, [])

    def test_real_failure_with_another_workflow_missing_returns_todo(self):
        failed = self.failed()
        failed.pop(GATES[1]['path'])
        self.invoke(failed)
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'todo', 'priority:p1'})
        self.assertIn(failed[GATES[0]['path']][1][0]['html_url'], self.comments[0]['body'])

    def test_stale_after_journal_never_mutates_labels(self):
        self.move_on = 3
        with self.assertRaises(RuntimeError): self.invoke(self.failed())
        self.assertTrue(self.writes)
        self.assertTrue(all('/comments' in path for path, _, _ in self.writes))

    def test_missing_skipped_and_pending_never_write(self):
        for mode in ('missing-workflow', 'missing-job', 'skipped', 'pending'):
            snapshots = evidence()
            if mode == 'missing-workflow': snapshots.pop(GATES[1]['path'])
            elif mode == 'missing-job': snapshots[GATES[1]['path']][1].pop()
            elif mode == 'skipped': snapshots[GATES[1]['path']][1][0]['conclusion'] = 'skipped'
            else: snapshots[GATES[1]['path']][0]['status'] = 'in_progress'
            with self.subTest(mode=mode):
                self.assertEqual(self.invoke(snapshots), 0)
                self.assertEqual(self.writes, [])

    def test_failure_returns_doing_to_todo_with_links_and_other_labels(self):
        failed = self.failed()
        self.assertEqual(self.invoke(failed), 0)
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'todo', 'priority:p1'})
        self.assertIn(failed[GATES[0]['path']][1][0]['html_url'], self.comments[0]['body'])
        self.assertIn('CI: todo', self.comments[0]['body'])
        self.assertFalse(any('/labels/done' in path for path, _, _ in self.writes))

    def test_done_or_unclaimed_todo_issue_has_no_writes(self):
        for labels in ({'done'}, {'done', 'doing'}, {'todo'}, {'todo', 'doing'}):
            self.issue['labels'] = [{'name': label} for label in labels]
            with self.subTest(labels=labels):
                self.invoke(self.failed())
                self.assertEqual(self.writes, [])

    def test_done_during_fresh_recheck_is_never_deleted(self):
        actual = self.api
        def finish_elsewhere(path, method='GET', data=None):
            if method == 'GET' and path.endswith('/issues/7') and self.pr_reads >= 3:
                self.issue['labels'] = [{'name': 'done'}, {'name': 'priority:p1'}]
            return actual(path, method, data)
        self.api = finish_elsewhere
        with self.assertRaises(RuntimeError): self.invoke(self.failed())
        self.assertTrue(self.writes)  # intent was journaled before external finish
        self.assertTrue(all('/comments' in path for path, _, _ in self.writes))
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'done', 'priority:p1'})

    def test_done_during_second_snapshot_never_adds_todo(self):
        failed = self.failed()
        reads = 0

        def finish_during_snapshot(*args):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.issue['labels'] = [{'name': 'done'}, {'name': 'priority:p1'}]
            return failed

        with self.assertRaisesRegex(RuntimeError, 'cannot touch a done issue'):
            self.invoke(snapshot_sequence=finish_during_snapshot)
        self.assertEqual(reads, 2)
        self.assertTrue(self.writes)  # A pending journal is safe to retain.
        self.assertTrue(all('/comments' in path for path, _, _ in self.writes))
        self.assertEqual({l['name'] for l in self.issue['labels']}, {'done', 'priority:p1'})

    # Same guarantees run in failure-only mode, including the ready recovery
    # that must not re-add todo after AGY has consumed that handoff.
    test_duplicate_failure_never_undoes_later_worker_claim = ApplyTests.test_duplicate_failure_never_undoes_later_worker_claim
    test_parallel_failure_cannot_undo_claim = ApplyTests.test_additional_failed_workflow_cannot_undo_claimed_coding_epoch
    test_changed_attempt_snapshot_is_retryable = ApplyTests.test_snapshot_progress_after_intent_is_retryable
    test_partial_ready_recovery_and_claim = ApplyTests.test_ready_checkpoint_survives_api_failure_and_later_worker_claim
    test_ready_receipt_never_readds_consumed_todo = ApplyTests.test_ready_receipt_never_readds_label_consumed_by_worker
    def test_stale_head_before_and_during_evaluation(self):
        for move_on in (1, 2):
            self.move_on, self.pr_reads = move_on, 0
            with self.assertRaises(RuntimeError): self.invoke(self.failed())
            self.assertEqual(self.writes, [])
    test_old_event_run_no_mutation = ApplyTests.test_old_event_run_no_mutation


class PolicyModeTests(unittest.TestCase):
    def test_validation_refuses_malformed_modes_blockers_and_workflows(self):
        invalid = [[], None, dict(workflows={}), dict(workflows=[None]),
                   dict(workflows=[dict(path=GATES[0]['path'], jobs='unit')]),
                   dict(workflows=[dict(path=GATES[0]['path'], jobs=[[]])])]
        invalid += [dict(workflows=GATES, lifecycle_mode=value) for value in ('typo', None, False, [], {})]
        invalid += [dict(workflows=GATES, qualification_blockers=value) for value in (None, '', {}, [None], [' '])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'gates.json'
            for config in invalid:
                path.write_text(json.dumps(config))
                with self.subTest(config=config), self.assertRaises(RuntimeError): ci.load_config(path)
            for mode in ('full', 'failure-only'):
                path.write_text(json.dumps(dict(workflows=GATES, lifecycle_mode=mode, qualification_blockers=[])))
                self.assertEqual(ci.load_config(path)['lifecycle_mode'], mode)

    def test_full_mode_blockers_cannot_accidentally_produce_done(self):
        config = dict(workflows=GATES, lifecycle_mode='full', qualification_blockers=['missing e2e'])
        state, reasons, _ = ci.evaluate_policy(config, evidence())
        self.assertEqual(state, 'pending')
        self.assertIn('qualification blocked: missing e2e', reasons)
        harness = ApplyTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.config.write_text(json.dumps(config))
        harness.invoke()
        self.assertEqual(harness.writes, [])

    def test_legacy_full_policy_stays_compatible(self):
        self.assertEqual(ci.evaluate_policy(dict(workflows=GATES), evidence())[0], 'done')


class ReconcileTests(unittest.TestCase):
    def test_replacement_wake_reconciles_pr_b_even_when_event_only_names_c(self):
        from argparse import Namespace
        args = Namespace(repo='o/r', run_id=30, apply=False)
        event = dict(event='pull_request', head_repository={'full_name': 'o/r'}, path=GATES[0]['path'], pull_requests=[{'number': 30}])
        prs = [dict(number=n, body=f'Closes #{n}', head=dict(ref=f'issue/{n}', sha=str(n // 10) * 40, repo={'full_name': 'o/r'})) for n in (10, 20, 30)]
        with patch.object(ci, 'api', return_value=event), patch.object(ci, 'pages', return_value=prs), patch.object(ci, 'process_head', return_value=0) as process:
            self.assertEqual(ci.reconcile(args, {'workflows': GATES}), 0)
        self.assertEqual([c.args[0].pr for c in process.call_args_list], [10, 20, 30])
        self.assertTrue(all(c.kwargs == {'check_event': False} for c in process.call_args_list))

    def test_one_stale_pr_does_not_prevent_other_pr_reconciliation(self):
        from argparse import Namespace
        args = Namespace(repo='o/r', run_id=30, apply=False)
        event = dict(event='pull_request', head_repository={'full_name': 'o/r'}, path=GATES[0]['path'])
        prs = [dict(number=n, body=f'Closes #{n}', head=dict(ref=f'issue/{n}', sha=SHA, repo={'full_name': 'o/r'})) for n in (10, 20)]
        with patch.object(ci, 'api', return_value=event), patch.object(ci, 'pages', return_value=prs), patch.object(ci, 'process_head', side_effect=[RuntimeError('stale head'), 0]) as process:
            with self.assertRaises(RuntimeError):
                ci.reconcile(args, {'workflows': GATES})
        self.assertEqual(process.call_count, 2)


class LeaseTests(unittest.TestCase):
    def test_live_pid_never_stolen_by_ttl(self):
        lease = dict(owner='other', host=socket.gethostname(), pid=os.getpid(), since=0)
        self.assertFalse(work.lease_available(lease, 'mine', False, 100000))
        self.assertFalse(work.lease_available(lease, 'mine', True, 100000))
        self.assertTrue(work.lease_available(lease, 'other', True, 100000))

    def test_stale_dead_local_recovery_preserves_unverified_hosts(self):
        lease = dict(owner='other', host=socket.gethostname(), pid=1234, since=0)
        with patch.object(os, 'kill', side_effect=ProcessLookupError):
            self.assertTrue(work.lease_available(lease, 'mine', False, 100000))
            self.assertFalse(work.lease_available(dict(lease, host='another-mac'), 'mine', False, 100000))
            self.assertFalse(work.lease_available(lease, 'mine', False, 2))

    def test_atomic_guard_refuses_concurrent_command(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            common = Path(tmp)
            with (common / 'issue-work.guard').open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(work, 'git', return_value=tmp), patch.object(sys, 'argv', ['work.py', 'next']), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(work.main(), 3)


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.common = Path(self.tmp.name) / '.git'
        self.common.mkdir()
        self.todo = [dict(number=7, state='open', created_at='2026-01-01', labels=[{'name': 'todo'}])]
        self.lease = dict(issue=7, owner='mine', since=0, pid=os.getpid(), host=socket.gethostname())
        self.calls = []

    def invoke(self, command, gh):
        def git(*args):
            if args[0] == 'rev-parse':
                return str(self.common)
            if 'status' in args:
                return ''
            if 'rev-list' in args:
                return '1'
            return ''
        # Planner/worker selection now shares the paginated REST queue adapter.
        # Keep these lease/finish regression tests offline at that boundary.
        from unittest.mock import MagicMock
        client = MagicMock()
        raw = gh('issue', 'view', '7') if command[0] == 'next' else None
        rows = raw if isinstance(raw, list) else []
        current = dict(rows[0]) if rows else (dict(raw) if raw else {})
        current['state'] = current.get('state', 'open').lower()
        current.setdefault('number', 7)
        client.issue.side_effect = lambda n: dict(current)
        client.continuation.side_effect = lambda n: dict(current)
        client.pages.return_value = rows
        client.snapshot.return_value = dict(decision=dict(selected=None, state='idle', reasons=[]))
        def patch_issue(n, **kwargs):
            current['labels'] = [{'name': v} for v in kwargs['labels']]
            self.calls.append(('PATCH', n, tuple(kwargs['labels'])))
        client.patch.side_effect = patch_issue
        with patch.object(sys, 'argv', ['work.py', *command]), patch.dict(os.environ, ISSUE_WORK_OWNER='mine'), patch.object(work, 'git', side_effect=git), patch.object(work, 'gh', side_effect=gh), patch.object(work, 'run', side_effect=lambda *a: self.calls.append(a)), patch.object(work, 'branch_of', return_value=('agy/issue-7', 8)), patch.object(work, 'prepare', return_value=Path(self.tmp.name)), patch.object(work.queue, 'Client', return_value=client), contextlib.redirect_stdout(io.StringIO()):
            return work.main()

    def test_waiting_doing_not_resumed_even_with_mine(self):
        (self.common / 'issue-work.lock').write_text(json.dumps(self.lease))
        def gh(*args):
            return [] if args[1] == 'list' else dict(number=7, state='OPEN', labels=[{'name': 'doing'}])
        self.assertEqual(self.invoke(['next', '--mine'], gh), 3)
        self.assertEqual(self.calls, [])

    def test_mine_resumes_active_retry_even_with_existing_pr(self):
        self.lease['phase'] = 'coding'
        (self.common / 'issue-work.lock').write_text(json.dumps(self.lease))
        def gh(*args):
            return [] if args[1] == 'list' else dict(number=7, state='OPEN', labels=[{'name': 'doing'}])
        self.assertEqual(self.invoke(['next', '--mine'], gh), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['phase'], 'coding')

    def test_ci_failed_todo_reuses_pr_and_branch(self):
        self.assertEqual(self.invoke(['next'], lambda *a: self.todo), 0)
        lease = json.loads((self.common / 'issue-work.lock').read_text())
        self.assertEqual(lease['issue'], 7)
        self.assertIn(('PATCH', 7, ('doing',)), self.calls)

    def test_finish_reuses_pr_and_never_writes_lifecycle(self):
        (self.common / 'issue-work.lock').write_text(json.dumps(self.lease))
        summary = Path(self.tmp.name) / 'summary.md'
        summary.write_text('affected unit tests pass')
        def gh(*args):
            return dict(state='OPEN', labels=[{'name': 'doing'}])
        self.assertEqual(self.invoke(['finish', '7', str(summary)], gh), 0)
        self.assertFalse(any('edit' in c or 'create' in c or 'merge' in c for c in self.calls))
        self.assertFalse((self.common / 'issue-work.lock').exists())

    def test_block_only_reports_preserves_lease_and_state(self):
        lease_path = self.common / 'issue-work.lock'
        lease_path.write_text(json.dumps(self.lease))
        question = Path(self.tmp.name) / 'question.md'
        question.write_text('need a decision')
        self.assertEqual(self.invoke(['block', '7', str(question)], lambda *a: None), 0)
        self.assertTrue(lease_path.exists())
        self.assertEqual(self.calls, [('gh', 'issue', 'comment', '7', '--body-file', str(question.resolve()))])

    def test_legacy_lock_refuses_without_deleting(self):
        lease_path = self.common / 'issue-work.lock'
        lease_path.write_text('7 100')
        with self.assertRaises(RuntimeError):
            self.invoke(['next'], lambda *a: [])
        self.assertEqual(lease_path.read_text(), '7 100')

    def test_existing_dirty_branch_worktree_is_preserved(self):
        expected = Path(self.tmp.name) / 'elsewhere'
        output = f'worktree {self.tmp.name}\nHEAD abc\nbranch refs/heads/main\n\nworktree {expected}\nHEAD def\nbranch refs/heads/agy/issue-7'
        with patch.object(work, 'git', return_value=output) as git:
            self.assertEqual(work.prepare(Path(self.tmp.name), 7, 'agy/issue-7'), expected)
        self.assertEqual(git.call_count, 1)

    def test_existing_local_branch_not_reset_to_remote(self):
        with patch.object(work, 'git', return_value='') as git, patch.object(work, 'branch_exists', return_value=True):
            work.prepare(Path(self.tmp.name), 7, 'issue/7')
        args = git.call_args.args
        self.assertEqual(args[-1], 'issue/7')
        self.assertNotIn('-B', args)
        self.assertNotIn('origin/issue/7', args)


class ReviewerTests(unittest.TestCase):
    def invoke_many(self, commands, ci_status='0', moved=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / 'calls'
            gh = root / 'gh'
            gh.write_text('#!' + sys.executable + "\n" + r'''import os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ['REVIEW_TEST_ROOT'])
with (root / 'calls').open('a') as f:
    f.write(' '.join(args) + '\n')
if args[:2] == ['issue', 'view']:
    print('done')
elif args[0] == 'api':
    print('7\n9')
elif args[:2] == ['pr', 'list']:
    print('10' if 'issue/9' in args else '8')
elif args[:2] == ['repo', 'view']:
    print('o/r')
elif args[:2] == ['pr', 'view']:
    if 'statusCheckRollup' in args:
        print('4')
    else:
        counter = root / 'heads'
        count = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(count + 1))
        changed = (count and os.environ.get('MOVED') == '1') or ('8' in args and (root / 'newhead').exists())
        print(('b' if changed else 'a') * 40)
''')
            (root / 'git').write_text('#!/bin/bash\nif [[ "$*" == *rev-parse* ]]; then echo "$REVIEW_TEST_ROOT"; elif [[ "$*" == *merge-base* ]]; then echo aaaa; fi\n')
            (root / 'python3').write_text('#!' + sys.executable + "\n" + r'''import json, os, sys
from pathlib import Path
root = Path(os.environ['REVIEW_TEST_ROOT'])
args = sys.argv[1:]
with (root / 'calls').open('a') as f:
    f.write('PY ' + ' '.join(args) + '\n')
if args[0].endswith('receipt.py'):
    saved = root / 'receipts'
    receipts = json.loads(saved.read_text()) if saved.exists() else []
    key = args[2] + ':' + args[4]
    if args[1] == 'seen':
        sys.exit(0 if key in receipts else 1)
    if key not in receipts:
        receipts.append(key)
        saved.write_text(json.dumps(receipts))
    sys.exit(0)
sys.exit(int(os.environ['CI_STATUS']))
''')
            for executable in (gh, root / 'git', root / 'python3'):
                executable.chmod(0o755)
            report = root / 'review.md'
            report.write_text('specific review evidence')
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], REVIEW_TEST_ROOT=str(root), ISSUE_CI_CONFIG='/trusted/gates.json', CI_STATUS=ci_status, MOVED='1' if moved else '0')
            results = []
            for command in commands:
                if command == 'move':
                    (root / 'newhead').touch()
                    continue
                args = ['bash', str(SKILLS / 'issue-review/scripts/review.sh'), command]
                if command != 'next':
                    args += ['7', SHA]
                    if command != 'pass':
                        args += [str(report)]
                results.append(subprocess.run(args, env=env, capture_output=True, text=True))
            return results, log.read_text()

    def invoke(self, command, **kwargs):
        results, calls = self.invoke_many([command], **kwargs)
        return results[0], calls

    def test_pass_verifies_ci_same_head_and_only_records_receipt(self):
        result, calls = self.invoke('pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('handoff.py', calls)
        self.assertIn('--head ' + SHA, calls)
        self.assertIn('record 7 8 ' + SHA + ' pass', calls)
        for forbidden in (' merge ', 'issue edit', 'issue close', '--apply'):
            self.assertNotIn(forbidden, calls)

    def test_pending_ci_refuses_acceptance(self):
        result, calls = self.invoke('pass', ci_status='2')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('record 7 8', calls)

    def test_head_changed_after_verification_refuses_acceptance(self):
        result, calls = self.invoke('pass', moved=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('record 7 8', calls)

    def test_fail_and_block_report_without_lifecycle_changes(self):
        for command in ('fail', 'block'):
            with self.subTest(command=command):
                result, calls = self.invoke(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('record 7 8 ' + SHA + ' ' + command, calls)
                self.assertNotIn('issue edit', calls)
                self.assertNotIn(' merge ', calls)

    def test_next_after_pass_selects_other_done_and_new_head_is_eligible(self):
        results, calls = self.invoke_many(['next', 'pass', 'next', 'move', 'next'])
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('issue=7', results[0].stdout)
        self.assertIn('issue=9', results[2].stdout)
        self.assertIn('issue=7', results[3].stdout)
        self.assertIn('head=' + 'b' * 40, results[3].stdout)


class ReviewReceiptTests(unittest.TestCase):
    def test_record_dedupes_authenticated_same_head_comment(self):
        comments = []
        writes = []
        def api(path, data=None):
            if data is not None:
                writes.append(data)
                comments.append(dict(body=data['body'], user={'login': 'reviewer'}))
                return {}
            if '/comments?' in path:
                return comments
            if '/pulls/' in path:
                return dict(state='open', head={'sha': SHA})
            return dict(state='open', labels=[{'name': 'done'}])
        with patch.object(receipt, 'api', side_effect=api):
            receipt.record('o/r', '7', '8', SHA, 'pass', 'verified', 'reviewer')
            with contextlib.redirect_stdout(io.StringIO()):
                receipt.record('o/r', '7', '8', SHA, 'pass', 'verified', 'reviewer')
        self.assertEqual(len(writes), 1)
        self.assertIn('issue-review:7:' + SHA, writes[0]['body'])

    def test_spoofed_or_old_head_receipt_cannot_skip_review(self):
        comments = [dict(body=f'<!-- issue-review:7:{SHA} -->', user={'login': 'attacker'}), dict(body=f'<!-- issue-review:7:{"b" * 40} -->', user={'login': 'reviewer'})]
        with patch.object(receipt, 'api', return_value=comments):
            self.assertFalse(receipt.seen('o/r', '7', '8', SHA, 'reviewer'))

    def test_stale_head_or_mixed_lifecycle_cannot_record_acceptance(self):
        for pr, labels in ((dict(state='open', head={'sha': 'b' * 40}), ['done']), (dict(state='open', head={'sha': SHA}), ['done', 'doing'])):
            with self.subTest(labels=labels), patch.object(receipt, 'seen', return_value=False), patch.object(receipt, 'api', side_effect=[pr, dict(state='open', labels=[{'name': l} for l in labels])]) as api:
                with self.assertRaises(RuntimeError):
                    receipt.record('o/r', '7', '8', SHA, 'pass', 'verified', 'reviewer')
                self.assertTrue(all(len(c.args) == 1 for c in api.call_args_list))


if __name__ == '__main__':
    unittest.main()
