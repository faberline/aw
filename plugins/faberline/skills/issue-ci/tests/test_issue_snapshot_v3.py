"""A full roster and an exact issue snapshot are mandatory for v3."""
import copy
import base64
import hashlib
import json
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


if __name__ == '__main__':
    unittest.main()
