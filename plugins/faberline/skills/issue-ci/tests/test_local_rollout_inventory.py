"""Local observations are useful evidence but never grant rollout authority."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


path = Path(__file__).resolve().parents[1] / 'scripts/local_rollout_inventory.py'
spec = importlib.util.spec_from_file_location('local_rollout_inventory', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LocalRolloutInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skills = self.root / 'skills'
        self.common = self.root / 'common'
        self.skills.mkdir()
        self.common.mkdir()
        self.expected = {}
        for name, relative in m.RUNTIME_PATHS.items():
            target = self.skills / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = (name + '-pinned').encode()
            target.write_bytes(data)
            self.expected[name] = hashlib.sha256(data).hexdigest()

    def test_matching_installation_and_no_observed_lease_never_authorize(self):
        result = m.observe(self.skills, self.common, self.expected)
        self.assertEqual({row['state'] for row in result['files'].values()}, {'match'})
        self.assertEqual(result['lease'], {'state': 'not_observed'})
        self.assertEqual(result['expected_source'], 'caller_supplied')
        self.assertEqual(result['actor_rollout'], 'unknown')
        self.assertFalse(result['quiescence_proven'])
        self.assertFalse(result['authorizes_writes'])

    def test_missing_and_changed_files_are_reported_separately(self):
        (self.skills / m.RUNTIME_PATHS['worker']).unlink()
        (self.skills / m.RUNTIME_PATHS['entry']).write_text('old worker')
        result = m.observe(self.skills, self.common, self.expected)
        self.assertEqual(result['files']['worker']['state'], 'missing')
        self.assertEqual(result['files']['entry']['state'], 'different')
        self.assertFalse(result['authorizes_writes'])

    def test_lease_reports_issue_and_phase_without_owner_or_assuming_liveness(self):
        raw = json.dumps({'issue': 104, 'phase': 'coding', 'owner': 'private-owner'}).encode()
        (self.common / 'issue-work.lock').write_bytes(raw)
        result = m.observe(self.skills, self.common, self.expected)
        self.assertEqual(result['lease'], {'state': 'observed', 'issue': 104, 'phase': 'coding',
                                           'sha256': hashlib.sha256(raw).hexdigest()})
        self.assertNotIn('private-owner', json.dumps(result))
        self.assertFalse(result['authorizes_writes'])

    def test_lease_replacement_with_same_issue_and_phase_is_detected(self):
        first = m.snapshot(self.skills, self.common)
        second = {'files': dict(first['files']), 'lease': {'state': 'observed', 'issue': 104,
                                                            'phase': 'coding', 'sha256': 'a' * 64}}
        third = {'files': dict(first['files']), 'lease': dict(second['lease'], sha256='b' * 64)}
        rows = iter([second, third])
        with self.assertRaisesRegex(RuntimeError, 'local_inventory_changed'):
            m.observe(self.skills, self.common, self.expected, reader=lambda *_: next(rows))

    def test_symlink_and_oversized_runtime_are_refused(self):
        target = self.skills / m.RUNTIME_PATHS['worker']
        target.unlink()
        target.symlink_to(self.skills / m.RUNTIME_PATHS['entry'])
        with self.assertRaisesRegex(RuntimeError, 'local_inventory_unsafe_file'):
            m.observe(self.skills, self.common, self.expected)
        target.unlink()
        target.write_bytes(b'x' * (m.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(RuntimeError, 'local_inventory_unsafe_file'):
            m.observe(self.skills, self.common, self.expected)

    def test_malformed_lease_and_invalid_expected_hashes_are_refused(self):
        lock = self.common / 'issue-work.lock'
        for raw in (b'{', b'{"issue": true, "phase": "coding"}', b'{"issue": 1}',
                    b'{"issue": 1, "issue": 2, "phase": "coding"}',
                    b'{"issue": 1, "phase": "coding", "extra": NaN}'):
            lock.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(RuntimeError, 'local_inventory_lease_unknown'):
                m.observe(self.skills, self.common, self.expected)
        lock.unlink()
        for expected in ({}, dict(self.expected, worker='not-a-hash')):
            with self.subTest(expected=expected), self.assertRaisesRegex(RuntimeError, 'expected_runtime'):
                m.observe(self.skills, self.common, expected)

    def test_replacement_during_read_and_missing_directories_are_refused(self):
        first = m.snapshot(self.skills, self.common)
        second = {'files': dict(first['files'], worker='a' * 64), 'lease': first['lease']}
        rows = iter([first, second])
        with self.assertRaisesRegex(RuntimeError, 'local_inventory_changed'):
            m.observe(self.skills, self.common, self.expected, reader=lambda *_: next(rows))
        with self.assertRaisesRegex(RuntimeError, 'directory_unknown'):
            m.observe(self.root / 'missing', self.common, self.expected)

    def test_cli_reports_observation_without_mutating_files(self):
        expected_path = self.root / 'expected.json'
        expected_path.write_text(json.dumps(self.expected))
        lock = self.common / 'issue-work.lock'
        lock.write_text('{"issue":104,"phase":"coding","owner":"private-owner"}')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, str(path), '--skills-root', str(self.skills),
                                 '--common-dir', str(self.common), '--expected-runtime', str(expected_path)],
                                check=True, capture_output=True, text=True)
        value = json.loads(result.stdout)
        self.assertEqual(value['lease']['issue'], 104)
        self.assertNotIn('private-owner', result.stdout)
        self.assertFalse(value['authorizes_writes'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
