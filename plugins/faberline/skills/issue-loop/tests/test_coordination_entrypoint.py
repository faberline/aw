import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'coordination_entrypoint', Path(__file__).parents[1] / 'scripts/coordination_entrypoint.py')
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


class EntryPointTests(unittest.TestCase):
    def write_config(self, path, value=None):
        value = value or {'protocol': entry.PROTOCOL,
                          'repositories': list(entry.TARGET_REPOS)}
        path.write_text(json.dumps(value, separators=(',', ':')) + '\n')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_missing_config_fails_closed_for_six_targets_but_not_other_repos(self):
        for repo in entry.TARGET_REPOS:
            with self.subTest(repo=repo), self.assertRaisesRegex(entry.Refused, 'config is required'):
                entry.prepare('work', ['next'], {}, config_hash=None,
                              remote=(repo, 'github.com'))
        env = entry.prepare('plan', ['status'], {'GH_REPO': 'faberline/aw'},
                            config_hash=None, remote=('faberline/aw', 'github.com'))
        self.assertNotIn('ISSUE_COORDINATION_MODE', env)

    def test_configured_target_forces_github_and_fresh_owner(self):
        owners = iter([uuid.UUID(int=1), uuid.UUID(int=2)])
        base = {'ISSUE_COORDINATION_MODE': 'local',
                'ISSUE_COORDINATION_OWNER': 'stale-owner',
                'GH_REPO': 'faberline/core'}
        first = entry.prepare('work', ['next'], base,
                              config_hash='a' * 64,
                              remote=('faberline/core', 'github.com'),
                              owner_factory=lambda: next(owners))
        second = entry.prepare('work', ['finish', '7', 'summary'], base,
                               config_hash='a' * 64,
                               remote=('faberline/core', 'github.com'),
                               owner_factory=lambda: next(owners))
        self.assertEqual(first['ISSUE_COORDINATION_MODE'], 'github')
        self.assertNotEqual(first['ISSUE_COORDINATION_OWNER'], 'stale-owner')
        self.assertNotEqual(first['ISSUE_COORDINATION_OWNER'], second['ISSUE_COORDINATION_OWNER'])
        self.assertEqual(first['GH_REPO'], 'faberline/core')

    def test_every_pinned_target_forces_github_for_plan_and_work(self):
        for repo in entry.TARGET_REPOS:
            for kind, args in [('plan', ['prioritize', '7', 'p1']), ('work', ['next'])]:
                with self.subTest(repo=repo, kind=kind):
                    result = entry.prepare(kind, args, {'ISSUE_COORDINATION_MODE': 'local'},
                                           config_hash='a' * 64,
                                           remote=(repo, 'github.com'),
                                           owner_factory=lambda: uuid.UUID(int=1))
                    self.assertEqual(result['GH_REPO'], repo)
                    self.assertEqual(result['ISSUE_COORDINATION_MODE'], 'github')
                    self.assertEqual(result['ISSUE_COORDINATION_OWNER'], str(uuid.UUID(int=1)))

    def test_other_repo_keeps_legacy_environment(self):
        original = {'GH_REPO': 'faberline/aw', 'ISSUE_COORDINATION_MODE': 'local'}
        result = entry.prepare('work', ['next'], original,
                               config_hash='a' * 64,
                               remote=('faberline/aw', 'github.com'))
        self.assertEqual(result, original)

    def test_local_origin_mismatch_refuses(self):
        for kind, args in [('plan', ['prioritize', '7', 'p1']), ('work', ['next'])]:
            with self.subTest(kind=kind), self.assertRaisesRegex(entry.Refused, 'does not match'):
                entry.prepare(kind, args, {'GH_REPO': 'faberline/tape'},
                              config_hash='a' * 64,
                              remote=('faberline/core', 'github.com'))

    def test_protected_repo_identity_is_case_canonical(self):
        for remote in ('https://GITHUB.com/FABERLINE/CORE.git',
                       'git@GitHub.com:Faberline/Core.git',
                       'ssh://git@GITHUB.com/FABERLINE/CORE.git'):
            with self.subTest(remote=remote):
                self.assertEqual(entry.parse_remote(remote),
                                 ('faberline/core', 'github.com'))
        env = entry.prepare('work', ['next'], {'GH_REPO': 'FABERLINE/CORE'},
                            config_hash='a' * 64,
                            remote=('FABERLINE/CORE', 'GITHUB.COM'),
                            owner_factory=lambda: uuid.UUID(int=3))
        self.assertEqual(env['GH_REPO'], 'faberline/core')
        self.assertEqual(env['ISSUE_COORDINATION_MODE'], 'github')
        with self.assertRaisesRegex(entry.Refused, 'does not match'):
            entry.prepare('work', ['next'], {'GH_REPO': 'faberline/tape'},
                          config_hash='a' * 64,
                          remote=('FABERLINE/CORE', 'github.com'))

    def test_unverifiable_protected_origin_refuses(self):
        with self.assertRaisesRegex(entry.Refused, 'cannot verify'):
            entry.prepare('work', ['next'], {'GH_REPO': 'faberline/core'},
                          config_hash=None, remote=None)
        with self.assertRaisesRegex(entry.Refused, 'github.com'):
            entry.prepare('work', ['next'], {}, config_hash='a' * 64,
                          remote=('faberline/core', 'github.example'))

    def test_dispatch_preserves_nontarget_status_outside_git_checkout(self):
        with tempfile.TemporaryDirectory() as outside_git:
            with patch.dict('os.environ', {'GH_REPO': 'faberline/aw',
                                           'ISSUE_COORDINATION_MODE': 'local'}), \
                    patch.object(entry.Path, 'cwd', return_value=Path(outside_git)), \
                    patch.object(entry.os, 'execve') as execve:
                entry.dispatch('plan', ['status'])
        self.assertEqual(execve.call_count, 1)
        argv = execve.call_args.args[1]
        self.assertEqual(argv[0], execve.call_args.args[0])
        self.assertTrue(argv[1].endswith('/issue-plan/scripts/plan.py'))
        self.assertEqual(execve.call_args.args[2]['GH_REPO'], 'faberline/aw')
        self.assertEqual(execve.call_args.args[2]['ISSUE_COORDINATION_MODE'], 'local')

    def test_bootstrap_refuses_protected_target_without_git_or_config(self):
        with tempfile.TemporaryDirectory() as outside_git:
            with patch.object(entry, 'load_config', return_value=None):
                for kind in ('plan', 'work'):
                    with self.subTest(kind=kind), self.assertRaisesRegex(entry.Refused, 'cannot verify GH_REPO'):
                        entry.bootstrap(kind, ['status'], {'GH_REPO': 'faberline/core'},
                                        cwd=outside_git)

    def test_direct_plan_and_work_entrypoints_call_bootstrap(self):
        root = Path(__file__).parents[2]
        for path, kind in ((root / 'issue-plan/scripts/plan.py', 'plan'),
                           (root / 'issue-work/scripts/work.py', 'work')):
            with self.subTest(path=path):
                self.assertIn(f"bootstrap('{kind}', sys.argv[1:])", path.read_text())

    def test_direct_execution_cannot_skip_protected_config_gate(self):
        root = Path(__file__).parents[2]
        env = dict(os.environ, GH_REPO='', ISSUE_COORDINATION_MODE='local')
        with tempfile.TemporaryDirectory() as checkout:
            subprocess.run(['git', 'init', '-q'], cwd=checkout, check=True)
            subprocess.run(['git', 'remote', 'add', 'origin',
                            'https://GITHUB.com/FABERLINE/CORE.git'], cwd=checkout, check=True)
            for script, args in ((root / 'issue-plan/scripts/plan.py', ['status']),
                                 (root / 'issue-work/scripts/work.py', ['next'])):
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, str(script), *args], cwd=checkout,
                                            env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('coordination config is required', result.stderr)

    def test_real_git_errors_still_fail_closed(self):
        cmd = ['git', 'rev-parse', '--show-toplevel']
        def failed(*args, **kwargs):
            raise subprocess.CalledProcessError(128, cmd, stderr='fatal: bad revision')
        with self.assertRaisesRegex(entry.Refused, 'cannot verify local'):
            entry.local_remote('/tmp', run=failed)

    def test_cloud_plan_always_requires_github_and_refreshes_owner(self):
        env = entry.prepare('plan', ['--cloud', 'prioritize', '7', 'p1'],
                            {'GH_REPO': 'faberline/core', 'ISSUE_COORDINATION_MODE': 'local'},
                            config_hash=None, remote=None,
                            owner_factory=lambda: uuid.UUID(int=9))
        self.assertEqual(env['ISSUE_COORDINATION_MODE'], 'github')
        self.assertEqual(env['ISSUE_COORDINATION_OWNER'], str(uuid.UUID(int=9)))
        self.assertEqual(env['GH_REPO'], 'faberline/core')
        casefolded = entry.prepare('plan', ['--cloud', 'status'],
                                   {'GH_REPO': 'FABERLINE/CORE'}, config_hash=None,
                                   remote=None, owner_factory=lambda: uuid.UUID(int=10))
        self.assertEqual(casefolded['GH_REPO'], 'faberline/core')
        with self.assertRaisesRegex(entry.Refused, 'exact supported'):
            entry.prepare('plan', ['--cloud', 'status'], {'GH_REPO': 'faberline/aw'},
                          config_hash=None, remote=None)

    def test_config_is_canonical_and_hashable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'coordination.json'
            self.assertIsNone(entry.load_config(path))
            expected = self.write_config(path)
            self.assertEqual(entry.load_config(path), expected)
            self.assertEqual(expected, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_malformed_or_noncanonical_config_fails_closed(self):
        cases = [
            b'{"protocol":"aw-issue-coordination-entrypoint-v1","protocol":"x","repositories":[]}',
            json.dumps({'protocol': 'wrong', 'repositories': list(entry.TARGET_REPOS)}).encode(),
            json.dumps({'protocol': entry.PROTOCOL, 'repositories': list(entry.TARGET_REPOS[:-1])}).encode(),
            json.dumps({'protocol': entry.PROTOCOL, 'repositories': list(entry.TARGET_REPOS) + ['faberline/aw']}).encode(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'coordination.json'
            for data in cases:
                with self.subTest(data=data):
                    path.write_bytes(data)
                    with self.assertRaises(entry.Refused):
                        entry.load_config(path)

    def test_remote_parsing_for_https_and_ssh(self):
        self.assertEqual(entry.parse_remote('https://github.com/faberline/core.git'),
                         ('faberline/core', 'github.com'))
        self.assertEqual(entry.parse_remote('git@github.com:faberline/tape.git'),
                         ('faberline/tape', 'github.com'))
        self.assertEqual(entry.parse_remote('ssh://git@github.com/faberline/lumen.git'),
                         ('faberline/lumen', 'github.com'))
        self.assertIsNone(entry.parse_remote('https://github.com/faberline/core/pull/1'))

    def test_worker_origin_parser_canonicalizes_all_supported_forms(self):
        for remote in ('https://GITHUB.com/FABERLINE/CORE.git',
                       'ssh://git@GITHUB.com/FABERLINE/CORE.git',
                       'git@GITHUB.com:FABERLINE/CORE.git'):
            self.assertEqual(entry.parse_remote(remote), ('faberline/core', 'github.com'))

    def test_bootstrap_requires_config_for_local_protected_origin(self):
        with patch.object(entry, 'load_config', return_value=None), \
                patch.object(entry, 'local_remote', return_value=('FABERLINE/CORE', 'GITHUB.COM')):
            for kind in ('plan', 'work'):
                with self.subTest(kind=kind), self.assertRaisesRegex(entry.Refused, 'config is required'):
                    entry.bootstrap(kind, ['status'], {})


if __name__ == '__main__':
    unittest.main()
