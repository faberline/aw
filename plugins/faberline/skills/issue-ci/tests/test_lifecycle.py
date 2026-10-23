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
            if path.endswith('/comments'):
                self.comments.append(dict(body=data['body'], user={'login': 'github-actions[bot]'}))
            return {}
        if '/pulls/' in path:
            self.pr_reads += 1
            if self.move_on and self.pr_reads >= self.move_on:
                return dict(self.pr, head=dict(self.pr['head'], sha='b' * 40))
            return self.pr
        if '/actions/runs/' in path:
            return self.trigger
        return self.issue

    def invoke(self, snapshots=None):
        args = ['handoff.py', '--repo', 'o/r', '--config', str(self.config), '--pr', '8', '--head', SHA, '--run-id', '1', '--apply']
        with patch.object(sys, 'argv', args), patch.object(ci, 'api', side_effect=self.api), patch.object(ci, 'pages', side_effect=lambda *a: self.comments), patch.object(ci, 'snapshot', return_value=snapshots or evidence()), contextlib.redirect_stdout(io.StringIO()):
            return ci.main()

    def test_current_head_success_preserves_other_labels(self):
        self.assertEqual(self.invoke(), 0)
        self.assertIn(('repos/o/r/issues/7/labels', 'POST', {'labels': ['done']}), self.writes)
        self.assertIn(('repos/o/r/issues/7/labels/doing', 'DELETE', None), self.writes)
        self.assertFalse(any('priority' in str(w) for w in self.writes))

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

    def test_untrusted_marker_cannot_suppress_handoff(self):
        self.invoke()
        self.comments[0]['user']['login'] = 'contributor'
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
        self.todo = [dict(number=7, createdAt='2026-01-01', labels=[{'name': 'todo'}])]
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
        with patch.object(sys, 'argv', ['work.py', *command]), patch.dict(os.environ, ISSUE_WORK_OWNER='mine'), patch.object(work, 'git', side_effect=git), patch.object(work, 'gh', side_effect=gh), patch.object(work, 'run', side_effect=lambda *a: self.calls.append(a)), patch.object(work, 'branch_of', return_value=('agy/issue-7', 8)), patch.object(work, 'prepare', return_value=Path(self.tmp.name)), contextlib.redirect_stdout(io.StringIO()):
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
        self.assertIn(('gh', 'issue', 'edit', '7', '--remove-label', 'todo', '--add-label', 'doing'), self.calls)

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
    def invoke(self, command, ci_status='0', moved=False):
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
elif args[:2] == ['pr', 'list']:
    print('8')
elif args[:2] == ['repo', 'view']:
    print('o/r')
elif args[:2] == ['pr', 'view']:
    counter = root / 'heads'
    count = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(count + 1))
    print(('b' if count and os.environ.get('MOVED') == '1' else 'a') * 40)
''')
            (root / 'git').write_text('#!/bin/bash\nif [[ "$*" == *rev-parse* ]]; then echo "$REVIEW_TEST_ROOT"; fi\n')
            (root / 'python3').write_text('#!/bin/bash\necho "CI verify $*" >> "$REVIEW_TEST_ROOT/calls"\nexit "$CI_STATUS"\n')
            for executable in (gh, root / 'git', root / 'python3'):
                executable.chmod(0o755)
            report = root / 'review.md'
            report.write_text('specific review evidence')
            args = ['bash', str(SKILLS / 'issue-review/scripts/review.sh'), command, '7']
            args += [SHA] if command == 'pass' else [str(report)]
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], REVIEW_TEST_ROOT=str(root), ISSUE_CI_CONFIG='/trusted/gates.json', CI_STATUS=ci_status, MOVED='1' if moved else '0')
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            return result, log.read_text()

    def test_pass_verifies_ci_same_head_and_only_comments(self):
        result, calls = self.invoke('pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CI verify', calls)
        self.assertIn('--head ' + SHA, calls)
        self.assertIn('Review: accepted head ' + SHA, calls)
        for forbidden in (' merge ', 'issue edit', 'issue close', '--apply'):
            self.assertNotIn(forbidden, calls)

    def test_pending_ci_refuses_acceptance(self):
        result, calls = self.invoke('pass', ci_status='2')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('pr comment', calls)

    def test_head_changed_after_verification_refuses_acceptance(self):
        result, calls = self.invoke('pass', moved=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('pr comment', calls)

    def test_fail_and_block_report_without_lifecycle_changes(self):
        for command in ('fail', 'block'):
            with self.subTest(command=command):
                result, calls = self.invoke(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('issue edit', calls)
                self.assertNotIn(' merge ', calls)


if __name__ == '__main__':
    unittest.main()
