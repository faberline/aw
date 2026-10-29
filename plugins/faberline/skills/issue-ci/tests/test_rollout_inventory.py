"""The observed GitHub queue is never authority to enable the guard."""
import copy
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

path = Path(__file__).resolve().parents[1] / 'scripts/rollout_inventory.py'
spec = importlib.util.spec_from_file_location('rollout_inventory', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def run(number, status='queued'):
    return dict(id=number, run_attempt=1, workflow_id=10, status=status, head_sha='a' * 40,
                path='.github/workflows/ci.yml', head_repository=dict(full_name='o/r'))


class Api:
    def __init__(self, rows=None):
        self.rows = rows or {status: [] for status in m.STATUSES}
        self.calls = []

    def api(self, path, method='GET', data=None):
        if method != 'GET' or data is not None:
            raise AssertionError('inventory must be read-only')
        self.calls.append(path)
        parts = urlsplit(path)
        if parts.path != 'repos/o/r/actions/runs':
            raise AssertionError(path)
        query = parse_qs(parts.query)
        status, page = query['status'][0], int(query['page'][0])
        rows = self.rows[status]
        return dict(total_count=len(rows), workflow_runs=copy.deepcopy(rows[(page - 1) * 100:page * 100]))


class RolloutInventoryTests(unittest.TestCase):
    def test_empty_matching_reads_never_authorize_rollout(self):
        api = Api()
        result = m.observe(api, 'o/r')
        self.assertEqual(result['runs'], [])
        self.assertEqual(result['actor_rollout'], 'unknown')
        self.assertFalse(result['quiescence_proven'])
        self.assertFalse(result['authorizes_writes'])
        self.assertEqual(len(api.calls), len(m.STATUSES) * 2)

    def test_all_pages_and_fork_head_are_retained(self):
        rows = {status: [] for status in m.STATUSES}
        rows['queued'] = [run(n) for n in range(1, 102)]
        rows['queued'][-1]['head_repository']['full_name'] = 'fork/project'
        api = Api(rows)
        result = m.observe(api, 'o/r')
        self.assertEqual(len(result['runs']), 101)
        self.assertEqual(result['runs'][-1]['head_repository'], 'fork/project')
        self.assertEqual(sum('status=queued' in path and 'page=2' in path for path in api.calls), 2)
        self.assertFalse(result['authorizes_writes'])

    def test_different_successive_reads_refuse(self):
        api = Api()
        original = api.api
        def changed(path, method='GET', data=None):
            if len(api.calls) == len(m.STATUSES):
                api.rows['queued'].append(run(7))
            return original(path, method, data)
        api.api = changed
        with self.assertRaisesRegex(RuntimeError, 'inventory_changed'):
            m.observe(api, 'o/r')

    def test_partial_page_and_duplicate_or_changed_status_refuse(self):
        for fault in ('partial', 'duplicate', 'status', 'path'):
            with self.subTest(fault=fault):
                rows = {status: [] for status in m.STATUSES}
                rows['queued'] = [run(1)]
                api = Api(rows)
                original = api.api
                def malformed(path, method='GET', data=None):
                    value = original(path, method, data)
                    if 'status=queued' in path:
                        if fault == 'partial': value['total_count'] = 2
                        if fault == 'duplicate':
                            value['workflow_runs'].append(copy.deepcopy(value['workflow_runs'][0]))
                            value['total_count'] = 2
                        if fault == 'status': value['workflow_runs'][0]['status'] = 'completed'
                        if fault == 'path': value['workflow_runs'][0]['path'] = '../untrusted\npath'
                    return value
                api.api = malformed
                with self.assertRaises(RuntimeError):
                    m.snapshot(api, 'o/r')


if __name__ == '__main__':
    unittest.main()
