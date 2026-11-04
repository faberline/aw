import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


source = Path(__file__).resolve().parents[1] / 'scripts/acceptance_plan.py'
module_spec = importlib.util.spec_from_file_location('acceptance_plan', source)
plan = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(plan)


class AcceptancePlanTests(unittest.TestCase):
    def setUp(self):
        self.spec = ('## Goal\nImprove behavior.\n## Acceptance\n'
                     '- [ ] First outcome\n- [ ] Second outcome\n'
                     '## Never\nKeep all gates.\n## Gate\nRun tests.\n')
        self.value = {'schema': 'AW_ACCEPTANCE_PLAN_V1',
                      'spec_sha256': hashlib.sha256(self.spec.encode()).hexdigest(),
                      'items': [{'acceptance': 'First outcome', 'checks': ['unit']},
                                {'acceptance': 'Second outcome', 'checks': ['e2e', 'perf']}]}
        self.checks = ['unit', 'e2e', 'perf']

    def validate(self, value=None, spec=None):
        return plan.validate(self.spec if spec is None else spec,
                             json.dumps(self.value if value is None else value), self.checks)

    def test_complete_mapping_binds_spec_and_returns_all_named_checks(self):
        result = self.validate()
        self.assertEqual(result['spec_sha256'], self.value['spec_sha256'])
        self.assertEqual(result['checks'], ['e2e', 'perf', 'unit'])
        self.assertEqual(len(result['plan_sha256']), 64)

    def test_prepared_comment_round_trips_and_never_executes_mapping_text(self):
        prepared = plan.prepare_comment(self.spec, json.dumps(self.value), self.checks)
        self.assertTrue(prepared.startswith(plan.MARKER + '\n'))
        comment = dict(id=12, user={'id': 42}, updated_at='2026-11-04T00:00:00Z', body=prepared)
        result = plan.published_mapping('o/r', self.issue(), self.spec, [comment], [42], self.checks)
        self.assertEqual(result['plan_sha256'], self.validate()['plan_sha256'])
        invalid = {**self.value, 'items': [{'acceptance': 'First outcome', 'checks': ['$(touch marker)']},
                                          self.value['items'][1]]}
        with self.assertRaises(ValueError):
            plan.prepare_comment(self.spec, json.dumps(invalid), self.checks)

    def test_changed_issue_spec_invalidates_mapping(self):
        with self.assertRaisesRegex(ValueError, 'current planner specification'):
            self.validate(spec=self.spec.replace('First outcome', 'New outcome'))

    def test_missing_extra_reordered_and_duplicate_items_are_rejected(self):
        for rows in (self.value['items'][:1], self.value['items'] + [self.value['items'][0]],
                     list(reversed(self.value['items'])), [self.value['items'][0]] * 2):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.validate({**self.value, 'items': rows})

    def test_unknown_missing_and_duplicate_checks_are_rejected(self):
        for checks in ([], ['invented'], ['unit', 'unit'], [None]):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                self.validate({**self.value, 'items': [dict(acceptance='First outcome', checks=checks),
                                                        self.value['items'][1]]})

    def test_ambiguous_acceptance_sections_and_content_are_rejected(self):
        for spec in (self.spec + '\n## Acceptance\n- [ ] Extra\n',
                     self.spec.replace('- [ ] First outcome', 'First outcome'),
                     self.spec.replace('Second outcome', 'First outcome'),
                     self.spec.replace('- [ ] First outcome', '- [x] First outcome')):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                plan.acceptance_items(spec)

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for value in ('{"schema":"one","schema":"two"}', '{"value":NaN}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan.decode(value)

    def test_missing_or_duplicate_trusted_inventory_is_rejected(self):
        for checks in ([], ['unit', 'unit'], ['unit', None]):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                plan.validate(self.spec, json.dumps(self.value), checks)

    def execution_fixture(self):
        test_plan = {'schema': 'AW_TEST_PLAN_V1', 'repo': 'o/r', 'mode': 'diff',
                     'head': 'a' * 40, 'base': 'b' * 40, 'policy_sha256': 'c' * 64,
                     'selected': sorted(self.checks),
                     'checks': {name: {'kind': 'unit'} for name in self.checks}, 'paths': ['src/main.rs']}
        canonical = json.dumps(test_plan, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        test_plan['sha256'] = hashlib.sha256(canonical.encode()).hexdigest()
        receipts = [dict(check=name, repo='o/r', head='a' * 40, base='b' * 40,
                         plan_sha256=test_plan['sha256'], conclusion='success', expected=[name + '::test'],
                         executed=[name + '::test'], failures=[], skipped=[]) for name in self.checks]
        return test_plan, receipts

    def test_execution_joins_the_complete_roster_without_granting_qualification(self):
        test_plan, receipts = self.execution_fixture()
        result = plan.verify_execution(self.spec, json.dumps(self.value), test_plan, receipts)
        self.assertEqual(result['mapped_checks'], sorted(self.checks))
        self.assertEqual(result['head'], 'a' * 40)
        self.assertEqual(result['base'], 'b' * 40)
        self.assertEqual(result['qualification'], 'not_evaluated')

    def test_execution_rejects_incomplete_stale_skipped_and_zero_test_receipts(self):
        for change in ('missing', 'stale', 'skipped', 'zero'):
            test_plan, receipts = self.execution_fixture()
            if change == 'missing': receipts.pop()
            if change == 'stale': receipts[0]['base'] = 'd' * 40
            if change == 'skipped': receipts[0]['skipped'] = ['test']
            if change == 'zero': receipts[0]['expected'] = receipts[0]['executed'] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                plan.verify_execution(self.spec, json.dumps(self.value), test_plan, receipts)

    def test_execution_rejects_mapping_to_check_outside_selected_plan(self):
        test_plan, receipts = self.execution_fixture()
        value = {**self.value, 'items': [dict(acceptance='First outcome', checks=['other']),
                                         self.value['items'][1]]}
        with self.assertRaisesRegex(ValueError, 'unknown checks'):
            plan.verify_execution(self.spec, json.dumps(value), test_plan, receipts)

    def issue(self):
        return dict(number=7, state='open', url='https://api.github.com/repos/o/r/issues/7',
                    repository_url='https://api.github.com/repos/o/r', title='Issue', body='Original acceptance', labels=[])

    def comment(self, number=12, author=42, value=None):
        return dict(id=number, user={'id': author}, updated_at='2026-11-04T00:00:00Z',
                    body=plan.MARKER + '\n' + json.dumps(self.value if value is None else value))

    def test_published_mapping_binds_issue_comment_and_trusted_author(self):
        result = plan.published_mapping('o/r', self.issue(), self.spec,
                                        [dict(id=11, body='unrelated'), self.comment()], [42], self.checks)
        self.assertEqual((result['repository'], result['issue'], result['comment_id'], result['author_id']),
                         ('o/r', 7, 12, 42))
        self.assertEqual(result['checks'], sorted(self.checks))

    def test_untrusted_or_invalid_newer_mapping_never_falls_back(self):
        for latest in (self.comment(13, 99), self.comment(13, value={}),
                       dict(id=13, user={'id': 42}, body=plan.MARKER + 'wrong')):
            with self.subTest(latest=latest), self.assertRaises(ValueError):
                plan.published_mapping('o/r', self.issue(), self.spec,
                                       [self.comment(), latest], [42], self.checks)

    def test_bad_issue_identity_or_incomplete_comment_inventory_is_rejected(self):
        for issue in ({**self.issue(), 'state': 'closed'}, {**self.issue(), 'number': 8},
                      {**self.issue(), 'pull_request': {}}, {**self.issue(), 'repository_url': 'foreign'},
                      {**self.issue(), 'labels': [{'name': 'blocked'}]}, {**self.issue(), 'labels': None}):
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                plan.published_mapping('o/r', issue, self.spec, [self.comment()], [42], self.checks)
        for comments in (None, [self.comment(), self.comment()], [self.comment(13), self.comment(12)],
                         [{'id': 1, 'body': None}]):
            with self.subTest(comments=comments), self.assertRaises(ValueError):
                plan.published_mapping('o/r', self.issue(), self.spec, comments, [42], self.checks)

    def test_changed_spec_and_unknown_planner_id_cannot_use_old_mapping(self):
        with self.assertRaisesRegex(ValueError, 'current planner specification'):
            plan.published_mapping('o/r', self.issue(), self.spec + '\nchanged', [self.comment()], [42], self.checks)
        for actors in ([], [True], [42, 42], ['42']):
            with self.subTest(actors=actors), self.assertRaises(ValueError):
                plan.published_mapping('o/r', self.issue(), self.spec, [self.comment()], actors, self.checks)

    def test_published_execution_requires_fresh_unchanged_issue_and_mapping(self):
        test_plan, receipts = self.execution_fixture()
        issue = self.issue(); issue['body'] = self.spec
        comments = [self.comment()]
        result = plan.verify_published_execution('o/r', issue, self.spec, comments, [42], test_plan, receipts)
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.assertEqual(plan.revalidate_publication(result, 'o/r', issue, self.spec,
                                                     comments, [42], self.checks), result['publication'])
        changed = {**issue, 'title': 'Altered human title'}
        with self.assertRaisesRegex(ValueError, 'changed after execution'):
            plan.revalidate_publication(result, 'o/r', changed, self.spec, comments, [42], self.checks)
        other = {**self.value, 'items': [dict(acceptance='First outcome', checks=['e2e']), self.value['items'][1]]}
        with self.assertRaisesRegex(ValueError, 'changed after execution'):
            plan.revalidate_publication(result, 'o/r', issue, self.spec,
                                        [self.comment(value=other)], [42], self.checks)

    def test_latest_spec_requires_trusted_author_and_never_falls_back(self):
        issue = self.issue()
        self.assertEqual(plan.current_spec(issue, [], [42]), issue['body'])
        first = dict(id=5, user={'id': 42}, body=plan.SPEC_MARKER + '\n' + self.spec)
        self.assertEqual(plan.current_spec(issue, [first], [42]), first['body'])
        for latest in (dict(id=6, user={'id': 99}, body=plan.SPEC_MARKER + '\nnew'),
                       dict(id=6, user={'id': 42}, body=plan.SPEC_MARKER)):
            with self.subTest(latest=latest), self.assertRaises(ValueError):
                plan.current_spec(issue, [first, latest], [42])

    def test_published_execution_rejects_foreign_test_plan(self):
        test_plan, receipts = self.execution_fixture()
        test_plan['repo'] = 'other/repo'
        with self.assertRaisesRegex(ValueError, 'repository differs'):
            plan.verify_published_execution('o/r', self.issue(), self.spec,
                                            [self.comment()], [42], test_plan, receipts)


if __name__ == '__main__':
    unittest.main()
