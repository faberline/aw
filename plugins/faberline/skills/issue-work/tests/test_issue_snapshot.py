import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('issue_snapshot', Path(__file__).resolve().parents[1] / 'scripts/issue_snapshot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
work_spec = importlib.util.spec_from_file_location('snapshot_work', Path(__file__).resolve().parents[1] / 'scripts/work.py')
work = importlib.util.module_from_spec(work_spec)
work_spec.loader.exec_module(work)


class IssueSnapshotTests(unittest.TestCase):
    def test_stable_exact_snapshot_changes_when_issue_changes(self):
        issue = dict(number=7, state='open', url='https://api.github.com/repos/o/r/issues/7',
                     repository_url='https://api.github.com/repos/o/r', title='Issue', body='Acceptance')
        first = module.encoded('o/r', 7, issue, 'Planner spec')
        self.assertEqual(first, module.encoded('o/r', 7, dict(issue), 'Planner spec'))
        self.assertNotEqual(first, module.encoded('o/r', 7, dict(issue, body='Changed'), 'Planner spec'))
        self.assertNotEqual(first, module.encoded('o/r', 7, issue, 'New planner spec'))
        for changed in (dict(issue, state='closed'), dict(issue, number=8),
                        dict(issue, repository_url='https://api.github.com/repos/x/r'),
                        dict(issue, body=''), dict(issue, pull_request={})):
            with self.subTest(issue=changed), self.assertRaises(ValueError):
                module.encoded('o/r', 7, changed, 'Planner spec')

    def test_worker_commits_snapshot_once_for_opted_in_policy(self):
        issue = dict(number=7, state='open', url='https://api.github.com/repos/o/r/issues/7',
                     repository_url='https://api.github.com/repos/o/r', title='Issue', body='Acceptance')
        class Client:
            repo = 'o/r'
            def issue(self, number): return issue
            def comments(self, number): return []
            def spec(self, issue, comments): return issue['body']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args): return subprocess.check_output(['git', '-C', tmp, *args], text=True).strip()
            git('init', '-q'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
            (root / '.github').mkdir()
            (root / '.github/issue-ci-required-gates.json').write_text(json.dumps({'qualification': {'version': 3,
                'issue_snapshot_path': '.github/issue-contract-snapshots/{issue}.json'}}))
            git('add', '.'); git('commit', '-qm', 'base')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            (root / 'code').write_text('work'); git('add', 'code'); git('commit', '-qm', 'work')
            before = git('rev-parse', 'HEAD')
            work.commit_issue_snapshot(root, Client(), 7)
            after = git('rev-parse', 'HEAD')
            self.assertNotEqual(before, after)
            self.assertEqual(git('diff', '--name-only', before, after), '.github/issue-contract-snapshots/7.json')
            self.assertEqual((root / '.github/issue-contract-snapshots/7.json').read_text(), module.encoded('o/r', 7, issue, issue['body']))
            work.commit_issue_snapshot(root, Client(), 7)
            self.assertEqual(git('rev-parse', 'HEAD'), after)
            attributes = root / '.git/info/attributes'
            attributes.write_text('.github/issue-contract-snapshots/7.json filter=unsafe\n')
            with self.assertRaisesRegex(RuntimeError, 'clean filter'):
                work.commit_issue_snapshot(root, Client(), 7)
            attributes.unlink()
            (root / 'valuable').write_text('preserve')
            (root / '.github/issue-contract-snapshots/7.json').unlink()
            (root / '.github/issue-contract-snapshots/7.json').hardlink_to(root / 'valuable')
            with self.assertRaisesRegex(RuntimeError, 'hard links'):
                work.commit_issue_snapshot(root, Client(), 7)
            self.assertEqual((root / 'valuable').read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
