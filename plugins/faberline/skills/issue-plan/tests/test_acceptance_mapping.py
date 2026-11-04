import hashlib
import copy
import importlib.util
import json
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / 'scripts/acceptance_mapping.py'
spec = importlib.util.spec_from_file_location('planner_acceptance_mapping', path)
mapping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapping)


class PlannerAcceptanceMappingTests(unittest.TestCase):
    def setUp(self):
        self.body = '## Goal\nOutcome\n## Acceptance\n- [ ] First outcome\n## Never\nNo changes\n## Gate\nTest\n'
        self.issue = {'body': self.body}
        self.mapping = json.dumps({'schema': 'AW_ACCEPTANCE_PLAN_V1',
                                   'spec_sha256': hashlib.sha256(self.body.encode()).hexdigest(),
                                   'items': [{'acceptance': 'First outcome', 'checks': ['unit']}]})
        self.config = {'workflows': [{'path': '.github/workflows/ci.yml', 'jobs': ['policy', 'unit']}],
                       'qualification': {'version': 1,
                           'checks': {'policy': {'kind': 'policy', 'workflow': '.github/workflows/ci.yml', 'job': 'policy'},
                                      'unit': {'kind': 'unit', 'workflow': '.github/workflows/ci.yml', 'job': 'unit'}},
                           'trusted_workflows': {'.github/workflows/ci.yml': {'id': 1, 'definition_sha': 'a' * 40}},
                           'always': ['policy'], 'protected_paths': ['.github/**'],
                           'rules': [{'paths': ['**'], 'require': ['unit'], 'exempt': {}}]},
                       'planner_acceptance_mapping': {'version': 1, 'trusted_actor_ids': [42]}}

    def test_prepares_publication_without_granting_qualification(self):
        result = mapping.prepare(self.issue, [], self.config, self.mapping)
        self.assertTrue(result['body'].startswith('<!-- issue-plan:acceptance-map:v1 -->\n'))
        self.assertEqual(result['checks'], ['unit'])
        self.assertEqual(result['qualification'], 'not_evaluated')

    def test_requires_explicit_reviewed_policy_and_known_check(self):
        for config in ({**self.config, 'planner_acceptance_mapping': None},
                       {**self.config, 'planner_acceptance_mapping': {'version': True, 'trusted_actor_ids': [42]}},
                       {**self.config, 'planner_acceptance_mapping': {'version': 1, 'trusted_actor_ids': []}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                mapping.prepare(self.issue, [], config, self.mapping)
        invalid = self.mapping.replace('"unit"', '"untrusted"')
        with self.assertRaises(ValueError):
            mapping.prepare(self.issue, [], self.config, invalid)
        policy_only = self.mapping.replace('"unit"', '"policy"')
        with self.assertRaises(ValueError):
            mapping.prepare(self.issue, [], self.config, policy_only)

    def test_new_untrusted_spec_is_not_silently_ignored(self):
        comments = [{'id': 2, 'body': '<!-- issue-plan:spec -->\n' + self.body, 'user': {'id': 99}}]
        with self.assertRaisesRegex(ValueError, 'untrusted author'):
            mapping.prepare(self.issue, comments, self.config, self.mapping)

    def publication_fixture(self):
        test = self
        class Client:
            repo = 'o/r'
            def __init__(self):
                self.row = {'number': 7, 'state': 'open', 'title': 'Issue', 'body': test.body,
                            'url': 'https://api.github.com/repos/o/r/issues/7',
                            'repository_url': 'https://api.github.com/repos/o/r', 'labels': []}
                self.rows = []
                self.writes = 0
            def issue(self, number): return copy.deepcopy(self.row)
            def comments(self, number): return copy.deepcopy(self.rows)
            def command(self, *args, payload=None):
                if args == ('api', 'user'): return {'id': 42}
                self.writes += 1
                row = {'id': 12, 'user': {'id': 42}, 'updated_at':'2026-11-04T00:00:00Z', 'body': payload['body']}
                self.rows.append(row)
                return copy.deepcopy(row)
        document = {'config': self.config, 'source_commit': 'a' * 40,
                    'path': '.github/issue-ci-required-gates.json'}
        return Client(), lambda: copy.deepcopy(document)

    def test_publication_readback_and_exact_retry_are_idempotent(self):
        client, source = self.publication_fixture()
        result = mapping.publish(client, 7, self.mapping, source)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(mapping.publish(client, 7, self.mapping, source)['status'], 'existing')
        self.assertEqual(client.writes, 1)

    def test_changed_policy_or_issue_blocks_before_publication(self):
        for change in ('policy', 'issue', 'comments'):
            client, source = self.publication_fixture()
            reads = 0
            def changed_source():
                nonlocal reads
                reads += 1
                if reads == 2:
                    if change == 'issue': client.row['title'] = 'changed'
                    if change == 'comments': client.rows.append({'id': 2, 'body': 'new'})
                    if change == 'policy':
                        return {**source(), 'source_commit': 'b' * 40}
                return source()
            with self.subTest(change=change), self.assertRaises(ValueError):
                mapping.publish(client, 7, self.mapping, changed_source)
            self.assertEqual(client.writes, 0)

    def test_active_issue_and_untrusted_actor_never_publish(self):
        client, source = self.publication_fixture()
        client.row['labels'] = [{'name': 'doing'}]
        with self.assertRaisesRegex(ValueError, 'active'):
            mapping.publish(client, 7, self.mapping, source)
        client.row['labels'] = []
        original = client.command
        client.command = lambda *args, **kwargs: {'id': 99} if args == ('api', 'user') else original(*args, **kwargs)
        with self.assertRaisesRegex(ValueError, 'trusted planner'):
            mapping.publish(client, 7, self.mapping, source)
        self.assertEqual(client.writes, 0)

    def test_unknown_write_result_is_not_retried(self):
        client, source = self.publication_fixture()
        original = client.command
        def unknown(*args, **kwargs):
            if args == ('api', 'user'): return original(*args, **kwargs)
            original(*args, **kwargs)
            raise TimeoutError('lost response')
        client.command = unknown
        with self.assertRaises(TimeoutError):
            mapping.publish(client, 7, self.mapping, source)
        self.assertEqual(client.writes, 1)
        client.command = original
        self.assertEqual(mapping.publish(client, 7, self.mapping, source)['status'], 'existing')
        self.assertEqual(client.writes, 1)

    def test_changed_after_publication_is_left_pending(self):
        client, source = self.publication_fixture()
        original = client.command
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if args != ('api', 'user'): client.row['title'] = 'changed'
            return result
        client.command = changed
        with self.assertRaisesRegex(ValueError, 'changed after publication'):
            mapping.publish(client, 7, self.mapping, source)
        self.assertEqual(client.writes, 1)


if __name__ == '__main__':
    unittest.main()
