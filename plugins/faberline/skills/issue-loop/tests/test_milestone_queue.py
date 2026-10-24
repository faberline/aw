"""Offline queue/adapter regression fixtures, never dispatch live work."""
import contextlib
import copy
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
    spec = importlib.util.spec_from_file_location(name, SKILLS / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


q = load('queue_test', 'issue-loop/scripts/milestone_queue.py')
work = load('queue_work_test', 'issue-work/scripts/work.py')
plan = load('queue_plan_test', 'issue-plan/scripts/plan.py')


def m(n=1, title='tape@0.9.0', state='open', **kw):
    return dict(number=n, title=title, state=state, **kw)


def issue(n=1, milestone=None, state='todo', **kw):
    return dict(number=n, title=f'issue {n}', state='open', created_at='2026-01-01',
                labels=[] if state is None else [dict(name=state)],
                milestone=m() if milestone is None else milestone, body='', **kw)


def snapshot(issues, milestones=None, blockers=None):
    milestones = milestones or [m()]
    blockers = blockers or {}
    return dict(repo='faberline/tape', issues=issues, milestones=milestones, blockers=blockers,
                decision=q.select('faberline/tape', issues, milestones, blockers))


class VersionTests(unittest.TestCase):
    def test_numeric_order_not_dates_or_milestone_number(self):
        self.assertLess(q.version('tape@0.9', 'faberline/tape'), q.version('0.10.0', 'faberline/tape'))
        self.assertLess(q.version('1.0.0-rc.2', 'faberline/tape'), q.version('1.0.0-rc.10', 'faberline/tape'))
        self.assertLess(q.version('1.0.0-rc.10', 'faberline/tape'), q.version('1.0.0', 'faberline/tape'))
        self.assertEqual(q.version('0.9', 'faberline/tape'), q.version('v0.9.0+build', 'faberline/tape'))

    def test_invalid_or_ambiguous_titles_refused(self):
        for title in ('0.09', '0.9.x', 'release 0.9', '0.9 / 0.10', 'keep@0.9', '0.9.0-01', ' 0.9', '2026-10-24', 'core:0.9'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                q.version(title, 'faberline/tape')

    def test_core_is_repository_batch_no_crate_lookup(self):
        self.assertEqual(q.version('core@0.10.0', 'faberline/core'), q.version('0.10', 'faberline/core'))


class SelectionTests(unittest.TestCase):
    def test_version_before_priority_and_number_due_date(self):
        older = m(900, '0.9', due_on='2099-01-01')
        newer = m(1, '0.10', due_on='2000-01-01')
        a, b = issue(99, older), issue(1, newer)
        a['labels'].append(dict(name='priority:p3'))
        b['labels'].append(dict(name='priority:p0'))
        self.assertEqual(q.select('faberline/tape', [b, a], [newer, older], {})['selected'], 99)

    def test_priority_then_oldest_then_number_within_batch(self):
        a, b, c = issue(3), issue(2), issue(1)
        a['labels'].append(dict(name='priority:p0'))
        self.assertEqual(snapshot([c, b, a])['decision']['selected'], 3)
        a['labels'] = [dict(name='todo')]
        self.assertEqual(snapshot([b, a, c])['decision']['selected'], 1)
        b['created_at'] = '2020-01-01'
        self.assertEqual(snapshot([c, b, a])['decision']['selected'], 2)

    def test_empty_invalid_and_closed_milestones_do_not_deadlock(self):
        self.assertEqual(snapshot([issue()], [m(), m(2, '0.1'), m(3, 'old prose'), m(4, '0.2', 'closed')])['decision']['selected'], 1)
        self.assertEqual(snapshot([], [m(2, '0.1')])['decision']['state'], 'idle')

    def test_earlier_blocked_backlog_doing_done_review_are_barriers(self):
        newer = m(2, '0.10')
        for state in ('blocked', None, 'doing', 'done'):
            with self.subTest(state=state):
                result = snapshot([issue(1, state=state), issue(2, newer)], [m(), newer])['decision']
                self.assertIsNone(result['selected'])
                self.assertEqual(result['state'], 'blocked')
                self.assertIn('#1: lifecycle', result['reasons'][0])

    def test_same_batch_other_ready_work_can_continue(self):
        self.assertEqual(snapshot([issue(1, state='blocked'), issue(2)])['decision']['selected'], 2)

    def test_missing_invalid_closed_or_unknown_assignment_blocks_new_claims(self):
        for assigned in (None, m(title='ambiguous'), m(state='closed'), m(99)):
            a = issue()
            a['milestone'] = assigned
            catalog = [assigned] if assigned and assigned['number'] != 99 else [m()]
            result = snapshot([a, issue(2)], catalog)['decision']
            self.assertIsNone(result['selected'])
            self.assertTrue(result['metadata_errors'])

    def test_dependency_wait_never_skips_to_later_batch(self):
        newer = m(2, '0.10')
        result = snapshot([issue(), issue(2, newer)], [m(), newer], {1: ['waits for external/repo#8']})['decision']
        self.assertIsNone(result['selected'])
        self.assertIn('waits for', result['reasons'][0])


class ProseTests(unittest.TestCase):
    def test_incoming_directions_and_markdown_cross_repo(self):
        refs, unknown = q.prose_dependencies('Depends on [#9](https://github.com/faberline/aw/issues/73) and faberline/core#4\nBlocked by #6\nBlocks #8\nReferences #10', 'faberline/tape')
        self.assertEqual(refs, [('faberline/aw', 'issues', 73), ('faberline/core', 'issues', 4), ('faberline/tape', 'issues', 6)])
        self.assertEqual(unknown, [])

    def test_heading_and_unknown_prerequisite_fail_closed(self):
        refs, unknown = q.prose_dependencies('## Dependencies\n- #8\n\nDepends on a decision about quotas', 'faberline/tape')
        self.assertEqual(refs, [('faberline/tape', 'issues', 8)])
        self.assertEqual(len(unknown), 1)

    def test_partial_pr_does_not_complete_parent_defer15_real_spec(self):
        text = '#10 owns that actuator. #15 stays open after this PR until #10 lands and its scaling step is added.'
        self.assertEqual(q.prose_dependencies(text, 'faberline/defer')[0], [('faberline/defer', 'issues', 10)])

    def test_fenced_examples_and_outgoing_not_incoming(self):
        self.assertEqual(q.prose_dependencies('```\nDepends on #1\n```\nBlocks #31\nUnblocks #4', 'faberline/tape'), ([], []))


class ClauseParserTests(unittest.TestCase):
    def test_current_issue_subject_is_not_a_target(self):
        self.assertEqual(q.prose_dependencies('#15 depends on #10', 'faberline/defer', 15),
                         ([('faberline/defer', 'issues', 10)], []))

    def test_mixed_direction_both_orders_and_boundaries(self):
        for text in ('Depends on #10; blocks #20', 'Blocks #20; depends on #10',
                     'Depends on #10 and blocks #20', 'Blocks #20 and depends on #10',
                     'Depends on #10. Blocks #20', 'Blocks #20； depends on #10'):
            with self.subTest(text=text):
                self.assertEqual(q.prose_dependencies(text, 'faberline/defer', 15),
                                 ([('faberline/defer', 'issues', 10)], []))

    def test_reference_names_are_not_direction_or_cue_syntax(self):
        for name in ('blocks', 'unblocks', 'dependencies', 'prerequisites', 'blocked-by'):
            for target in (f'faberline/{name}#20', f'https://github.com/faberline/{name}/issues/20'):
                with self.subTest(target=target):
                    self.assertEqual(q.prose_dependencies(f'Depends on #10 and {target}', 'faberline/defer', 15),
                                     (sorted([('faberline/defer', 'issues', 10), (f'faberline/{name}', 'issues', 20)]), []))
        # Owner tokens and PR URLs receive the same reference protection.
        self.assertEqual(q.prose_dependencies('Depends on https://github.com/blocks/tape/pull/20', 'faberline/defer', 15),
                         ([('blocks/tape', 'pull', 20)], []))

    def test_explicit_self_target_excluded_with_blocking_diagnostic(self):
        refs, unknown = q.prose_dependencies('Depends on #15', 'faberline/defer', 15)
        self.assertEqual(refs, [])
        self.assertIn('self prerequisite #15', unknown[0])

    def test_other_issue_subject_or_unseparated_incoming_is_ambiguous(self):
        for text in ('#8 depends on #10', '#8 depends on #10 and blocks #20',
                     'Depends on #10 and #8 depends on #20',
                     'Depends on #10, #20 blocks #30'):
            with self.subTest(text=text):
                self.assertTrue(q.prose_dependencies(text, 'faberline/defer', 15)[1])


class FakeClient(q.Client):
    def __init__(self, issues=None, milestones=None):
        self.repo = 'faberline/tape'
        self.issues = copy.deepcopy(issues or [issue()])
        self.milestones = copy.deepcopy(milestones or [m()])
        self.comment_rows = []
        self.native = []
        self.targets = {}
        self.writes = []
        self.before_snapshot = None
        self.snapshots = 0

    def issue(self, n, repo=None):
        for i in self.issues:
            if i['number'] == n and (not repo or repo == self.repo):
                return copy.deepcopy(i)
        return copy.deepcopy(self.targets[(repo or self.repo, n)])

    def pages(self, path):
        if '/comments' in path:
            return copy.deepcopy(self.comment_rows)
        if '/dependencies/blocked_by' in path:
            return copy.deepcopy(self.native)
        if '/milestones?' in path:
            return copy.deepcopy(self.milestones)
        if '/pulls?' in path:
            return []
        if '/issues?' in path:
            return copy.deepcopy(self.issues)
        raise AssertionError(path)

    def api(self, path):
        if '/milestones/' in path:
            return copy.deepcopy(next(m for m in self.milestones if m['number'] == int(path.rsplit('/', 1)[1])))
        if '/pulls/' in path:
            return self.targets[('pull', int(path.rsplit('/', 1)[1]))]
        raise AssertionError(path)

    def snapshot(self, **kw):
        self.snapshots += 1
        if self.before_snapshot:
            self.before_snapshot(self)
        return super().snapshot(**kw)

    def patch(self, n, **kw):
        self.writes.append((n, kw))
        for i in self.issues:
            if i['number'] == n:
                for k, v in kw.items():
                    i[k] = next(m for m in self.milestones if m['number'] == v) if k == 'milestone' else v
                return copy.deepcopy(i)
        raise AssertionError(n)


class DependencyTests(unittest.TestCase):
    def test_native_and_prose_are_union_done_label_not_resolution(self):
        c = FakeClient()
        c.issues[0]['body'] = 'Depends on #2'
        c.targets[('faberline/tape', 2)] = issue(2, state='done')
        c.native = [dict(html_url='https://github.com/faberline/tape/issues/3')]
        c.targets[('faberline/tape', 3)] = dict(issue(3), state='closed')
        waits = c.blockers(c.issue(1), {1: m()})
        self.assertEqual(len(waits), 1)
        self.assertIn('#2', waits[0])

    def test_closed_unmerged_pr_blocks_merged_resolves(self):
        c = FakeClient()
        c.issues[0]['body'] = 'Depends on https://github.com/faberline/tape/pull/9'
        c.targets[('faberline/tape', 9)] = dict(state='closed', pull_request={})
        c.targets[('pull', 9)] = dict(merged_at=None)
        self.assertTrue(c.blockers(c.issue(1), {1: m()}))
        c.targets[('pull', 9)]['merged_at'] = '2026-01-01'
        self.assertFalse(c.blockers(c.issue(1), {1: m()}))

    def test_real_defer15_dependency_version_inversion(self):
        c = FakeClient([issue(15, m(1, 'defer@0.5.0'))], [m(1, 'defer@0.5.0'), m(2, 'defer@0.7.0')])
        c.repo = 'faberline/defer'
        c.comment_rows = [dict(body='<!-- issue-plan:spec -->\n#15 stays open after this PR until #10 lands and its scaling step is added.')]
        c.targets[('faberline/defer', 10)] = issue(10, m(2, 'defer@0.7.0'))
        decision = c.snapshot()['decision']
        self.assertIsNone(decision['selected'])
        self.assertIn('dependency/version inversion', decision['reasons'][0])
        self.assertEqual(c.writes, [])

    def test_403_dependency_read_never_means_empty(self):
        c = FakeClient()
        original = c.pages
        def pages(path):
            if 'blocked_by' in path:
                raise subprocess.CalledProcessError(1, 'gh api')
            return original(path)
        with patch.object(c, 'pages', side_effect=pages), self.assertRaises(subprocess.CalledProcessError):
            c.snapshot()

    def test_comments_beyond_first_page_included(self):
        c = FakeClient()
        c.comment_rows = [dict(body='unrelated') for _ in range(150)] + [dict(body='Depends on #8')]
        c.targets[('faberline/tape', 8)] = issue(8)
        self.assertIn('#8', c.snapshot()['decision']['reasons'][0])


class ClauseSelectionTests(unittest.TestCase):
    def client(self, text):
        c = FakeClient([issue(15)])
        c.repo = 'faberline/defer'
        c.milestones[0]['title'] = 'defer@0.9'
        c.issues[0]['body'] = text
        c.targets[('faberline/defer', 10)] = issue(10)
        c.targets[('faberline/defer', 20)] = issue(20)
        return c

    def test_subject_and_mixed_directions_wait_for_incoming_only(self):
        for text in ('#15 depends on #10', 'Depends on #10; blocks #20',
                     'Blocks #20; depends on #10'):
            with self.subTest(text=text):
                c = self.client(text)
                waits = c.blockers(c.issue(15), {1: c.milestones[0]})
                self.assertEqual(len(waits), 1)
                self.assertIn('#10 ', waits[0])
                self.assertNotIn('#15 ', waits[0])
                self.assertNotIn('#20 ', waits[0])
                self.assertIsNone(c.snapshot()['decision']['selected'])
                # #20 and the current #15 remain open; closing only #10 must
                # make this issue eligible, without self/outgoing deadlock.
                c.targets[('faberline/defer', 10)]['state'] = 'closed'
                self.assertEqual(c.blockers(c.issue(15), {1: c.milestones[0]}), [])
                self.assertEqual(c.snapshot()['decision']['selected'], 15)
                self.assertEqual(c.writes, [])

    def test_cross_repo_direction_word_target_remains_required(self):
        for target in ('faberline/blocks#20', 'https://github.com/faberline/blocks/issues/20'):
            with self.subTest(target=target):
                c = self.client(f'Depends on #10 and {target}')
                c.targets[('faberline/defer', 10)]['state'] = 'closed'
                c.targets[('faberline/blocks', 20)] = issue(20)
                waits = c.blockers(c.issue(15), {1: c.milestones[0]})
                self.assertEqual(len(waits), 1)
                self.assertIn('faberline/blocks#20', waits[0])
                self.assertIsNone(c.snapshot()['decision']['selected'])
                c.targets[('faberline/blocks', 20)]['state'] = 'closed'
                self.assertEqual(c.snapshot()['decision']['selected'], 15)
                self.assertEqual(c.writes, [])

    def test_explicit_self_or_ambiguous_subject_stays_fail_closed(self):
        for text in ('Depends on #15', '#8 depends on #10'):
            with self.subTest(text=text):
                c = self.client(text)
                c.targets[('faberline/defer', 10)]['state'] = 'closed'
                result = c.snapshot()['decision']
                self.assertIsNone(result['selected'])
                self.assertTrue(any('self prerequisite' in r or 'ambiguous prerequisite subject' in r for r in result['reasons']))


class PaginationTests(unittest.TestCase):
    def test_all_pages_including_exact_page_boundary(self):
        c = q.Client.__new__(q.Client)
        with patch.object(c, 'api', side_effect=[[{}] * 100, [{}] * 100, []]) as api:
            self.assertEqual(len(c.pages('repos/o/r/issues?state=open')), 200)
        self.assertEqual(api.call_args_list[-1].args, ('repos/o/r/issues?state=open&per_page=100&page=3',))

    def test_non_array_and_duplicate_inventory_refused(self):
        c = q.Client.__new__(q.Client)
        with patch.object(c, 'api', return_value={}):
            with self.assertRaises(ValueError):
                c.pages('repos/o/r/issues')
        c = FakeClient([issue(), issue()])
        with self.assertRaises(ValueError):
            c.snapshot()

    def test_pr_rows_do_not_create_issue_frontier(self):
        c = FakeClient([dict(issue(4, m(4, '0.1')), pull_request={}), issue()])
        self.assertEqual(c.snapshot()['decision']['selected'], 1)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeClient([issue(state=None)])
        self.c.issues[0]['body'] = '\n'.join('## ' + v for v in ('Goal', 'Acceptance', 'Never', 'Gate'))

    def test_adopt_requires_valid_existing_milestone_and_preserves_labels(self):
        self.c.issues[0]['labels'] = ['app:tape', 'custom']
        with contextlib.redirect_stdout(io.StringIO()):
            plan.adopt(self.c, 1, 'p0')
        self.assertEqual(set(self.c.issues[0]['labels']), {'todo', 'app:tape', 'custom', 'priority:p0'})

    def test_missing_closed_future_or_dependency_refuses_before_todo(self):
        for scenario in ('missing', 'closed', 'future', 'dependency'):
            with self.subTest(scenario=scenario):
                self.setUp()
                if scenario == 'missing':
                    self.c.issues[0]['milestone'] = None
                if scenario == 'closed':
                    self.c.milestones[0]['state'] = 'closed'
                if scenario == 'future':
                    older = m(2, '0.1')
                    self.c.milestones.append(older)
                    self.c.issues.append(issue(2, older, state='blocked'))
                if scenario == 'dependency':
                    self.c.issues[0]['body'] += '\nDepends on #8'
                    self.c.targets[('faberline/tape', 8)] = issue(8)
                with self.assertRaises(ValueError):
                    plan.adopt(self.c, 1, 'p1')
                self.assertFalse(any('todo' in kw.get('labels', []) for _, kw in self.c.writes))

    def test_frontier_change_during_adoption_refuses(self):
        def change(c):
            if c.snapshots == 2:
                c.milestones.append(m(2, '0.1'))
                c.issues.append(issue(2, m(2, '0.1'), state='blocked'))
        self.c.before_snapshot = change
        with self.assertRaisesRegex(ValueError, 'changed during adoption'):
            plan.adopt(self.c, 1, 'p0')
        self.assertEqual(self.c.writes, [])

    def test_prerequisite_added_to_backlog_during_adoption_refuses(self):
        def change(c):
            if c.snapshots == 2:
                c.comment_rows.append(dict(body='Depends on #8'))
                c.targets[('faberline/tape', 8)] = issue(8)
        self.c.before_snapshot = change
        with self.assertRaisesRegex(ValueError, 'changed during adoption'):
            plan.adopt(self.c, 1, 'p0')
        self.assertEqual(self.c.writes, [])

    def test_priority_normalizes_only_priority_namespace(self):
        self.assertEqual(plan.reprioritize({'todo', 'app:tape', 'priority:p0', 'priority:p3', 'custom'}, 'p2'), ['app:tape', 'custom', 'priority:p2', 'todo'])

    def test_new_missing_milestone_argument_never_creates(self):
        with self.assertRaisesRegex(ValueError, 'usage'):
            plan.execute(self.c, ['new', 'title', 'body', 'p0'])
        self.assertEqual(self.c.writes, [])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.common = Path(self.tmp.name) / '.git'
        self.common.mkdir()
        self.c = FakeClient()
        self.preparing = lambda *args: Path(self.tmp.name) / 'preserved-dirty-worktree'
        self.pr = None

    def invoke(self, args=('next',)):
        with patch.object(sys, 'argv', ['work.py', *args]), patch.dict(os.environ, ISSUE_WORK_OWNER='mine'), patch.object(work, 'git', side_effect=lambda *a: str(self.common) if a[0] == 'rev-parse' else ''), patch.object(work.queue, 'Client', return_value=self.c), patch.object(work, 'branch_of', side_effect=lambda n, **kw: (f'issue/{n}', self.pr)), patch.object(work, 'prepare', side_effect=self.preparing), contextlib.redirect_stdout(io.StringIO()):
            return work.main()

    def lease(self, **kw):
        data = dict(issue=1, owner='mine', since=0, pid=os.getpid(), host=socket.gethostname(), phase='coding')
        data.update(kw)
        (self.common / 'issue-work.lock').write_text(json.dumps(data))

    def test_new_claim_uses_same_guard_revalidates_then_preserves_labels(self):
        self.c.issues[0]['labels'].append(dict(name='app:tape'))
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(set(self.c.issues[0]['labels']), {'doing', 'app:tape'})
        self.assertEqual(self.c.snapshots, 2)
        self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['phase'], 'coding')

    def test_frontier_added_during_prepare_refuses_no_label_or_lease(self):
        def prepare(*args):
            earlier = m(2, '0.1')
            self.c.milestones.append(earlier)
            self.c.issues.append(issue(2, earlier, state='blocked'))
            return Path(self.tmp.name)
        self.preparing = prepare
        with self.assertRaisesRegex(RuntimeError, 'changed during preparation'):
            self.invoke()
        self.assertEqual(self.c.writes, [])
        self.assertFalse((self.common / 'issue-work.lock').exists())

    def test_dependency_change_during_prepare_refuses(self):
        def prepare(*args):
            self.c.issues[0]['body'] = 'Depends on #9'
            self.c.targets[('faberline/tape', 9)] = issue(9)
            return Path(self.tmp.name)
        self.preparing = prepare
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertEqual(self.c.writes, [])

    def test_owned_active_work_ignores_reordered_or_missing_milestones(self):
        self.lease()
        self.c.issues[0]['labels'] = ['doing']
        self.c.issues[0]['milestone'] = None
        self.pr = 9
        self.assertEqual(self.invoke(('next', '--mine')), 0)
        self.assertEqual(self.c.snapshots, 0)
        self.assertEqual(self.c.writes, [])

    def test_existing_same_pr_ci_retry_continues_before_frontier(self):
        self.c.issues[0]['milestone'] = None
        self.pr = 9
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.c.snapshots, 0)
        self.assertEqual(self.c.writes[0][1]['labels'], ['doing'])

    def test_owned_blocked_or_prerequisite_wait_keeps_lease(self):
        for scenario in ('blocked', 'dependency'):
            self.setUp()
            self.lease()
            self.c.issues[0]['labels'] = ['blocked' if scenario == 'blocked' else 'doing']
            if scenario == 'dependency':
                self.c.issues[0]['body'] = 'Depends on #8'
                self.c.targets[('faberline/tape', 8)] = issue(8)
            with self.assertRaises((RuntimeError, ValueError)):
                self.invoke(('next', '--mine'))
            self.assertEqual(self.c.writes, [])
            self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['issue'], 1)

    def test_uncertain_label_write_retains_intent_no_retry(self):
        with patch.object(self.c, 'patch', side_effect=OSError('network outcome uncertain')) as write:
            with self.assertRaises(OSError):
                self.invoke()
        self.assertEqual(write.call_count, 1)
        self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['phase'], 'claiming')

    def test_recover_confirmed_remote_doing_intent_does_not_replay_patch(self):
        self.lease(phase='claiming')
        self.c.issues[0]['labels'] = ['doing']
        self.pr = 9
        self.assertEqual(self.invoke(('next', '--mine')), 0)
        self.assertEqual(self.c.writes, [])
        self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['phase'], 'coding')

class AdditionalBoundaryTests(unittest.TestCase):
    def test_planner_uses_same_guard_and_refuses_without_api_calls(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            with (Path(tmp) / 'issue-work.guard').open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(plan.subprocess, 'check_output', return_value=tmp), patch.object(plan.queue, 'Client') as client, patch.object(sys, 'argv', ['plan.py', 'status']), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(plan.main(), 3)
                client.assert_not_called()

    def test_closed_historical_missing_milestone_is_ignored(self):
        closed = dict(issue(2), state='closed', milestone=None)
        c = FakeClient([closed, issue()])
        self.assertEqual(c.snapshot()['decision']['selected'], 1)

    def test_list_get_ancillary_fields_do_not_change_identity(self):
        a = issue()
        b = dict(a, closed_by=None, repository={'id': 999}, reactions={})
        self.assertEqual(q.issue_identity(a), q.issue_identity(b))
        self.assertNotEqual(q.issue_identity(a), q.issue_identity(dict(b, body='changed spec')))

    def test_dependency_bullet_without_reference_is_unknown(self):
        self.assertEqual(q.prose_dependencies('## Dependencies\n- quota decision', 'faberline/tape')[1], ['- quota decision'])
        self.assertEqual(q.prose_dependencies('Dependencies: none', 'faberline/tape'), ([], []))

    def test_no_new_dependencies_is_not_an_incoming_prerequisite(self):
        self.assertEqual(q.prose_dependencies('- No new dependencies or credentials.\nKeep dependency versions unchanged.', 'faberline/tape'), ([], []))

    def test_ambiguous_typed_direction_is_never_imported(self):
        self.assertEqual(q.prose_dependencies('{"typedQueueRelationships":[{"source":8,"target":31,"type":"blocks"}]}', 'faberline/tape'), ([], []))

    def test_new_creates_milestone_backlog_before_attempting_todo(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'spec.md'
            file.write_text('\n'.join('## '+v for v in ('Goal','Acceptance','Never','Gate')))
            c = FakeClient([issue(2, state='blocked')])
            writes = []
            def command(*args, payload=None):
                writes.append((args, payload))
                new = dict(issue(3, state=None), body=payload['body'], html_url='https://github.com/faberline/tape/issues/3')
                c.issues.append(new)
                return new
            with patch.object(c, 'command', side_effect=command), patch.object(plan, 'adopt', side_effect=ValueError('queue changed')), contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(ValueError):
                    plan.execute(c, ['new', 'title', str(file), 'p1', '--milestone', '1'])
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0][1]['milestone'], 1)
            self.assertNotIn('todo', writes[0][1]['labels'])
            self.assertIn('issues/3', out.getvalue())

    def test_branch_lookup_uses_complete_paged_inventory_and_refuses_forks(self):
        c = FakeClient()
        prs = [dict(number=7, head=dict(ref='issue/1', repo=dict(full_name=c.repo)))]
        with patch.object(work.queue, 'Client', return_value=c), patch.object(c, 'pages', return_value=prs) as pages:
            self.assertEqual(work.branch_of(1), ('issue/1', 7))
            self.assertEqual(pages.call_args.args, ('repos/faberline/tape/pulls?state=open',))
            prs[0]['head']['repo']['full_name'] = 'other/tape'
            with self.assertRaisesRegex(RuntimeError, 'foreign'):
                work.branch_of(1)


class PendingClaimTests(unittest.TestCase):
    setUp = WorkerTests.setUp
    invoke = WorkerTests.invoke
    lease = WorkerTests.lease

    def test_unexecuted_initial_claim_does_not_bypass_new_frontier(self):
        self.lease(phase='claiming')
        earlier = m(2, '0.1')
        self.c.milestones.append(earlier)
        self.c.issues.append(issue(2, earlier, state='blocked'))
        with self.assertRaisesRegex(RuntimeError, 'pending initial claim no longer eligible'):
            self.invoke(('next', '--mine'))
        self.assertEqual(self.c.writes, [])
        self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['phase'], 'claiming')

    def test_final_issue_read_change_refuses(self):
        real = self.c.issue
        count = [0]
        def read(n, repo=None):
            count[0] += 1
            value = real(n, repo)
            if count[0] == 1:
                value['labels'] = ['blocked']
            return value
        # New claims do not read GET until the final guard read.
        with patch.object(self.c, 'issue', side_effect=read), self.assertRaisesRegex(RuntimeError, 'immediately before claim'):
            self.invoke()
        self.assertEqual(self.c.writes, [])


if __name__ == '__main__':
    unittest.main()
