"""Isolated planner status controls: synthetic remote data and temporary guards.

No real gh, process inventory, shared guard, lease or product execution.
"""
import contextlib
import copy
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('readonly_plan', SKILLS / 'issue-plan/scripts/plan.py')
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


def snapshot():
    return dict(issues=[dict(number=7, title='Synthetic issue', state='open', labels=['todo'],
                            milestone=dict(number=1, title='0.9', state='open'), created_at='2026-01-01')],
                milestones=[dict(number=1, title='0.9', state='open')], blockers={7: []},
                decision=dict(state='ready', selected=7, frontier=[0, 9, 0], reasons=[]))


class Client:
    repo = 'faberline/tape'

    def __init__(self, value=None):
        self.value = snapshot() if value is None else value
        self.reads = 0

    def snapshot(self):
        self.reads += 1
        return copy.deepcopy(self.value)

    def patch(self, *args, **kwargs):
        raise AssertionError('status must not write')


class StatusTests(unittest.TestCase):
    def invoke(self, client, args=('status',)):
        output = io.StringIO()
        with patch.object(plan.queue, 'Client', return_value=client), patch.object(sys, 'argv', ['plan.py', *args]), contextlib.redirect_stdout(output):
            result = plan.main()
        return result, output.getvalue()

    def test_status_does_not_resolve_git_or_open_guard(self):
        client = Client()
        with patch.object(plan.subprocess, 'check_output', side_effect=AssertionError('no git/process')), patch.object(plan.Path, 'open', side_effect=AssertionError('no guard open')):
            result, output = self.invoke(client)
        self.assertEqual(result, 0)
        self.assertEqual(client.reads, 1)
        decision = json.loads(output.splitlines()[0])
        self.assertEqual(decision['selected'], 7)
        self.assertTrue(decision['read_only_snapshot'])
        self.assertFalse(decision['mutation_authorized'])
        self.assertEqual(decision['snapshot_consistency'], 'non-atomic')
        self.assertIn('room=2', output)

    def test_status_succeeds_while_synthetic_writer_keeps_exclusive_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = Path(directory) / 'issue-work.guard'
            guard.write_bytes(b'synthetic writer evidence')
            before = guard.stat()
            with guard.open('r+') as writer:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(plan.subprocess, 'check_output', side_effect=AssertionError('status must not inspect local guard')):
                    self.assertEqual(self.invoke(Client())[0], 0)
                with guard.open() as observer:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(observer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(guard.read_bytes(), b'synthetic writer evidence')
                self.assertEqual(guard.stat().st_ino, before.st_ino)

    def test_every_mutation_still_refuses_held_guard_before_client_or_writes(self):
        commands = [('new',), ('adopt',), ('milestone',), ('spec',), ('prioritize',), ('block',), ('status', 'extra'), ('unknown',)]
        with tempfile.TemporaryDirectory() as directory:
            with (Path(directory) / 'issue-work.guard').open('a') as writer:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for args in commands:
                    with self.subTest(args=args), patch.object(plan.subprocess, 'check_output', return_value=directory), patch.object(plan.queue, 'Client') as client, patch.object(sys, 'argv', ['plan.py', *args]), contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(plan.main(), 3)
                        client.assert_not_called()

    def test_readonly_status_does_not_admit_later_mutation(self):
        client = Client()
        self.assertEqual(self.invoke(client)[0], 0)
        with tempfile.TemporaryDirectory() as directory:
            with (Path(directory) / 'issue-work.guard').open('a') as writer:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(plan.subprocess, 'check_output', return_value=directory):
                    result, output = self.invoke(client, ('prioritize', '7', 'p1'))
                self.assertEqual(result, 3)
                self.assertIn('busy:', output)
        self.assertEqual(client.reads, 1)

    def test_mutation_with_free_guard_still_uses_guarded_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            with patch.object(plan.subprocess, 'check_output', return_value=directory), patch.object(plan, 'execute', return_value=0) as execute:
                self.assertEqual(self.invoke(client, ('prioritize', '7', 'p1'))[0], 0)
            execute.assert_called_once_with(client, ['prioritize', '7', 'p1'])
            self.assertTrue((Path(directory) / 'issue-work.guard').is_file())

    def test_failed_incomplete_unknown_snapshot_has_no_empty_or_ready_output(self):
        for error in (ValueError('incomplete inventory'), RuntimeError('unknown prerequisites'), OSError('source unavailable')):
            client = Client()
            output = io.StringIO()
            with self.subTest(error=error), patch.object(client, 'snapshot', side_effect=error), patch.object(plan.queue, 'Client', return_value=client), patch.object(plan.subprocess, 'check_output', side_effect=AssertionError('no guard')), patch.object(sys, 'argv', ['plan.py', 'status']), contextlib.redirect_stdout(output):
                with self.assertRaises(type(error)):
                    plan.main()
            self.assertEqual(output.getvalue(), '')

    def test_metadata_and_dependency_blockers_stay_explicit(self):
        value = snapshot()
        value['decision'] = dict(state='blocked', selected=None, frontier=None, reasons=['milestone missing'], metadata_errors=['milestone missing'])
        value['blockers'] = {7: ['unknown dependency']}
        result, output = self.invoke(Client(value))
        self.assertEqual(result, 0)
        decision = json.loads(output.splitlines()[0])
        self.assertEqual(decision['metadata_errors'], ['milestone missing'])
        self.assertIsNone(decision['selected'])
        self.assertFalse(decision['mutation_authorized'])
        self.assertIn('unknown dependency', output)

    def test_each_status_reads_fresh_snapshot_without_reusing_previous_decision(self):
        client = Client()
        first = self.invoke(client)[1]
        client.value['issues'][0]['labels'] = ['doing']
        client.value['decision'].update(state='blocked', selected=None, reasons=['coding in progress'])
        second = self.invoke(client)[1]
        self.assertEqual(client.reads, 2)
        self.assertEqual(json.loads(first.splitlines()[0])['selected'], 7)
        self.assertIsNone(json.loads(second.splitlines()[0])['selected'])
        self.assertIn('coding in progress', second)


if __name__ == '__main__':
    unittest.main()
