"""Hosted planner admission and portable context controls, with no live writes."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
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


plan = load('cloud_plan', SKILLS / 'issue-plan/scripts/plan.py')
bundle = load('bundle', SKILLS / 'issue-plan/scripts/bundle.py')


class CloudTests(unittest.TestCase):
    def test_hosted_status_does_not_resolve_local_git_or_open_guard(self):
        client = object()
        with patch.dict(os.environ, GH_REPO='faberline/core'), patch.object(sys, 'argv', ['plan.py', '--cloud', 'status']), patch.object(plan.queue, 'Client', return_value=client), patch.object(plan, 'execute', return_value=0) as execute, patch.object(plan.subprocess, 'check_output', side_effect=AssertionError('no local git')), patch.object(plan.Path, 'open', side_effect=AssertionError('no local guard')):
            self.assertEqual(plan.main(), 0)
        execute.assert_called_once_with(client, ['status'])

    def test_hosted_write_refuses_local_mode_before_execute(self):
        with patch.dict(os.environ, GH_REPO='faberline/core', ISSUE_COORDINATION_MODE='local'), patch.object(sys, 'argv', ['plan.py', '--cloud', 'prioritize', '7', 'p1']), patch.object(plan, 'execute') as execute:
            with self.assertRaises(plan.queue.coordination.Refused):
                plan.main()
        execute.assert_not_called()

    def test_direct_client_mutation_cannot_bypass_remote_guard(self):
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='github'), patch.object(plan.queue.subprocess, 'check_output') as transport:
            client = plan.queue.Client('faberline/core')
            with self.assertRaises(plan.queue.coordination.Refused):
                client.patch(7, labels=['todo'])
        transport.assert_not_called()

    def test_bad_repository_is_rejected_before_transport(self):
        with self.assertRaises(ValueError):
            plan.queue.Client('https://example.org/other')


class BundleTests(unittest.TestCase):
    def build(self, path):
        def committed(args):
            name = args[-1].split(':plugins/faberline/skills/', 1)[1]
            return (SKILLS / name).read_bytes()
        with patch.object(bundle.subprocess, 'check_output', side_effect=committed):
            return bundle.build(path, 'a' * 40)

    def test_portable_context_contains_six_prompts_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'context'
            manifest = self.build(path)
            self.assertEqual(bundle.verify(path), manifest)
            prompts = json.loads((path / 'prompts.json').read_text())
            self.assertEqual(set(prompts), set(bundle.REPOS))
            for repo, prompt in prompts.items():
                self.assertIn('Target faberline/' + repo, prompt)
                self.assertNotIn('/Users/', prompt)
            self.assertEqual(manifest['interval_hours'], 2)

    def test_bundled_cloud_entrypoint_runs_without_checkout_or_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / 'context'
            self.build(context)
            tools = root / 'bin'
            tools.mkdir()
            gh = tools / 'gh'
            gh.write_text('#!/usr/bin/env python3\nimport sys\n'
                          'if len(sys.argv) < 3 or sys.argv[1] != "api": sys.exit(99)\n'
                          'if "--method" in sys.argv and sys.argv[sys.argv.index("--method")+1] != "GET": sys.exit(99)\n'
                          'print("[]")\n')
            gh.chmod(0o700)
            env = dict(os.environ, GH_REPO='faberline/core',
                       ISSUE_COORDINATION_MODE='local',
                       PATH=str(tools) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(
                [str(context / 'issue-plan/scripts/plan.sh'), '--cloud', 'status'],
                cwd=root, env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout.splitlines()[0])
            self.assertTrue(snapshot['read_only_snapshot'])
            self.assertFalse(snapshot['mutation_authorized'])

    def test_tampered_missing_or_added_manifest_file_is_refused(self):
        for change in ('tamper', 'missing', 'extra', 'nonexecutable'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'context'
                self.build(path)
                target = path / 'issue-plan/scripts/plan.py'
                if change == 'tamper':
                    target.write_text('different')
                elif change == 'missing':
                    target.unlink()
                elif change == 'nonexecutable':
                    (path / 'issue-plan/scripts/plan.sh').chmod(0o600)
                else:
                    m = json.loads((path / 'manifest.json').read_text())
                    m['files']['../../foreign'] = 'a' * 64
                    (path / 'manifest.json').write_text(json.dumps(m))
                with self.assertRaises(ValueError):
                    bundle.verify(path)

    def test_wrong_source_commit_is_refused_before_creating_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'context'
            with patch.object(bundle.subprocess, 'check_output', return_value=b'other source'):
                with self.assertRaisesRegex(ValueError, 'differs'):
                    bundle.build(path, 'a' * 40)
            self.assertFalse(path.exists())

    def test_mutable_revision_and_existing_destination_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                bundle.build(Path(tmp) / 'context', 'main')
            with self.assertRaises(ValueError):
                bundle.build(tmp, 'a' * 40)


if __name__ == '__main__':
    unittest.main()
