"""A full roster and an exact issue snapshot are mandatory for v3."""
import copy
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import unittest
from unittest.mock import patch
import test_issue_contract_v2 as previous

q = previous.q
ci = previous.ci


def fixture():
    config = previous.policy()
    config['qualification']['version'] = 3
    config['qualification']['issue_snapshot_path'] = '.github/issue-contract-snapshots/{issue}.json'
    config['qualification']['contracts']['6'] = config['qualification']['contracts'].pop('7')
    issue = previous.issue()
    issue['_aw_issue_spec'] = issue['body']
    issue['_aw_issue_snapshot'] = dict(schema='AW_ISSUE_SNAPSHOT_V1', repository='o/r', issue=7,
        **{k + '_sha256': hashlib.sha256(issue[k].encode()).hexdigest() for k in ('title', 'body')},
        spec_sha256=hashlib.sha256(issue['_aw_issue_spec'].encode()).hexdigest())
    return config, issue


class SnapshotTests(unittest.TestCase):
    def test_unresolved_snapshot_runs_all_tests_without_qualification(self):
        config, issue = fixture()
        pull = previous.pull(); pull['draft'] = False
        definitions = {(previous.base.WORKFLOW, ref): {'sha': previous.base.BLOB}
                       for ref in (previous.base.HEAD, previous.base.BASE)}
        spec = importlib.util.spec_from_file_location('v3_execution_plan',
            previous.base.legacy.SKILLS / 'issue-ci/scripts/test_plan.py')
        planner = importlib.util.module_from_spec(spec); spec.loader.exec_module(planner)
        result = planner.execute_unregistered_issue(config, 'o/r', previous.base.HEAD, previous.base.BASE,
            pull=pull, files=previous.base.files(), issue=issue, definitions=definitions)
        self.assertEqual(result['plan']['selected'], sorted(config['qualification']['checks']))
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.assertIn('linked issue has unresolved required gates', result['unresolved'])
        scoped = q.scoped_config(config, pull, previous.base.files(), definitions, issue)
        self.assertEqual(ci.evaluate_policy(scoped, previous.base.evidence(scoped))[0], 'pending')
        issue['_aw_issue_spec'] = 'changed'
        with self.assertRaises(ValueError):
            planner.execute_unregistered_issue(config, 'o/r', previous.base.HEAD, previous.base.BASE,
                pull=pull, files=previous.base.files(), issue=issue, definitions=definitions)

    def test_full_roster_even_for_docs(self):
        config, issue = fixture()
        plan = q.select(config, previous.pull(), previous.base.files('docs/guide.md'), issue)
        self.assertIn('linked issue has unresolved required gates', plan['issues'])
        self.assertEqual(set(plan['selected']), set(config['qualification']['checks']))

    def test_changed_foreign_missing_and_blocked_never_qualify(self):
        config, original = fixture()
        for key, value in [('body', 'changed'), ('title', 'changed'), ('state', 'closed'),
                           ('labels', [{'name': 'blocked'}]), ('_aw_issue_snapshot', None),
                           ('_aw_issue_spec', 'changed spec')]:
            issue = copy.deepcopy(original); issue[key] = value
            with self.subTest(key=key):
                self.assertTrue(q.select(config, previous.pull(), previous.base.files(), issue)['issues'])
        for key, value in [('repository', 'x/r'), ('issue', 8), ('body_sha256', '0' * 64)]:
            issue = copy.deepcopy(original); issue['_aw_issue_snapshot'][key] = value
            with self.subTest(key=key):
                self.assertTrue(q.select(config, previous.pull(), previous.base.files(), issue)['issues'])

    def test_snapshot_path_and_all_categories_are_required(self):
        config, _ = fixture()
        config['qualification']['issue_snapshot_path'] = 'other.json'
        with self.assertRaises(RuntimeError):
            q.validate(config)

    def test_existing_reviewed_unresolved_and_hashes_remain_blocking(self):
        config, issue = fixture()
        config['qualification']['contracts']['7'] = copy.deepcopy(config['qualification']['contracts']['6'])
        config['qualification']['contracts']['7']['unresolved'] = ['missing required issue evidence']
        plan = q.select(config, previous.pull(), previous.base.files(), issue)
        self.assertIn('linked issue has unresolved required gates', plan['issues'])
        config['qualification']['contracts']['7']['body_sha256'] = '0' * 64
        plan = q.select(config, previous.pull(), previous.base.files(), issue)
        self.assertIn('issue snapshot differs from existing reviewed contract', plan['issues'])

    def test_exact_head_blob_is_validated_and_loaded(self):
        config, issue = fixture()
        snapshot = issue.pop('_aw_issue_snapshot')
        issue.pop('_aw_issue_spec')
        raw = json.dumps(snapshot).encode()
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        metadata = dict(type='file', encoding='base64', size=len(raw), sha=blob,
                        content=base64.b64encode(raw).decode())
        calls = []
        def api(path):
            calls.append(path)
            if '/issues/7' in path: return issue
            if '/contents/.github/issue-contract-snapshots/7.json' in path: return metadata
            return {'sha': previous.base.BLOB}
        with patch.object(ci, 'api', side_effect=api), patch.object(ci, 'pages', return_value=[]):
            scoped = ci.qualify_config('o/r', previous.pull(), config, previous.base.files())
        self.assertIn('linked issue has unresolved required gates', scoped['_qualification_context']['issues'])
        self.assertTrue(any('issue-contract-snapshots/7.json?ref=' + previous.base.HEAD in path for path in calls))
        metadata['sha'] = '0' * 40
        with patch.object(ci, 'api', side_effect=api), patch.object(ci, 'pages', return_value=[]):
            scoped = ci.qualify_config('o/r', previous.pull(), config, previous.base.files())
        self.assertIn('linked issue snapshot is missing or invalid', scoped['_qualification_context']['issues'])

    def test_latest_planner_comment_changes_snapshot_requirement(self):
        config, issue = fixture()
        issue['_aw_issue_spec'] = q.planner_spec(issue, [{'body': '<!-- issue-plan:spec -->\nnew acceptance'}])
        self.assertIn('linked issue planner specification changed or is missing',
                      q.select(config, previous.pull(), previous.base.files(), issue)['issues'])

    def test_missing_snapshot_still_reports_failed_ci_and_returns_todo(self):
        lifecycle = previous.base.legacy
        harness = lifecycle.ApplyTests('test_current_head_success_preserves_other_labels')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        config, issue = fixture()
        issue.pop('_aw_issue_snapshot'); issue.pop('_aw_issue_spec')
        issue['labels'] = [{'name': 'doing'}, {'name': 'priority:p1'}]
        harness.issue = issue
        harness.pr = previous.pull()
        snapshots = previous.base.evidence(config)
        snapshots[previous.base.WORKFLOW][1][1]['conclusion'] = 'failure'
        failure = [RuntimeError('snapshot unavailable')]
        def api(path, method='GET', data=None):
            if method == 'GET' and '/contents/.github/issue-contract-snapshots/' in path:
                raise failure[0]
            if method == 'GET' and '/contents/.github/workflows/' in path:
                return {'sha': previous.base.BLOB}
            return harness.api(path, method, data)
        def pages(path, *args):
            return previous.base.files() if '/pulls/8/files' in path else harness.comments
        args = ci.argparse.Namespace(repo='o/r', pr=8, head=previous.base.HEAD, apply=True, run_id=None)
        for error in (RuntimeError('missing'), RuntimeError('invalid blob'), subprocess.CalledProcessError(1, 'gh')):
            failure[0] = error
            harness.issue['labels'] = [{'name': 'doing'}, {'name': 'priority:p1'}]
            harness.writes.clear(); harness.comments.clear()
            with self.subTest(error=error), patch.object(ci, 'api', side_effect=api), patch.object(ci, 'pages', side_effect=pages), \
                 patch.object(ci, 'snapshot', return_value=snapshots), \
                 patch.object(ci, 'untrusted_failure_snapshots', return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ci.process_head(args, config, check_event=False), 0)
            self.assertIn(('repos/o/r/issues/7/labels', 'POST', {'labels': ['todo']}), harness.writes)
            self.assertTrue(any(method == 'POST' and path.endswith('/comments') for path, method, _ in harness.writes))


if __name__ == '__main__':
    unittest.main()
