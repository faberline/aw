"""An unregistered issue may execute all tests but cannot qualify."""
import copy
import importlib.util
from pathlib import Path
import unittest
import test_issue_contract_v2 as fixtures

spec = importlib.util.spec_from_file_location('execution_plan', Path(__file__).parents[1] / 'scripts/test_plan.py')
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


class UnregisteredExecutionTests(unittest.TestCase):
    def setUp(self):
        self.policy = fixtures.policy()
        cases = self.policy['qualification']['contracts']
        cases['8'] = cases.pop('7')
        self.pull = fixtures.pull()
        self.pull['draft'] = False
        self.issue = fixtures.issue()
        self.files = fixtures.base.files()
        self.definitions = {(fixtures.base.WORKFLOW, ref): {'sha': fixtures.base.BLOB}
                            for ref in (fixtures.base.HEAD, fixtures.base.BASE)}

    def run_plan(self):
        return planner.execute_unregistered_issue(self.policy, 'o/r', fixtures.base.HEAD,
            fixtures.base.BASE, pull=self.pull, files=self.files, issue=self.issue, definitions=self.definitions)

    def test_unregistered_issue_runs_every_gate_without_qualification(self):
        result = self.run_plan()
        self.assertEqual(result['plan']['selected'], sorted(self.policy['qualification']['checks']))
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.assertEqual(result['unresolved'], ['linked issue has no reviewed contract'])
        with self.assertRaisesRegex(ValueError, 'no reviewed contract'):
            planner.select(self.policy, 'diff', 'o/r', fixtures.base.HEAD, fixtures.base.BASE,
                           pull=self.pull, files=self.files, issue=self.issue)

    def test_unknown_identity_blocked_issue_and_incomplete_diff_refuse(self):
        for mutation in ('foreign', 'closed', 'blocked', 'incomplete', 'unmapped', 'moved', 'workflow'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'foreign': self.issue['repository_url'] = 'https://api.github.com/repos/x/y'
                elif mutation == 'closed': self.issue['state'] = 'closed'
                elif mutation == 'blocked': self.issue['labels'] = [{'name':'blocked'}]
                elif mutation == 'incomplete': self.pull['changed_files'] = 2
                elif mutation == 'unmapped': self.files[0]['filename'] = 'unknown/a.rs'
                elif mutation == 'moved': self.pull['head']['sha'] = 'c' * 40
                elif mutation == 'workflow': self.definitions[(fixtures.base.WORKFLOW, fixtures.base.HEAD)] = {'sha':'e'*40}
                with self.assertRaises(ValueError): self.run_plan()

    def test_existing_unresolved_contract_is_not_reclassified(self):
        self.policy = fixtures.policy()
        self.policy['qualification']['contracts']['7']['unresolved'] = ['missing measured gate']
        with self.assertRaises(ValueError): self.run_plan()

    def test_protected_changes_remain_pending_review(self):
        self.policy['qualification']['protected_paths'].append('src/**')
        result = self.run_plan()
        self.assertIn('gate infrastructure change requires reviewed policy update: src/code.rs', result['unresolved'])
        self.assertIn('linked issue has no reviewed contract', result['unresolved'])
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.assertEqual(result['plan']['selected'], sorted(self.policy['qualification']['checks']))
        scoped = fixtures.q.scoped_config(self.policy, self.pull, self.files, self.definitions, self.issue)
        status, reasons, _ = fixtures.ci.evaluate_policy(scoped, fixtures.base.evidence(scoped))
        self.assertEqual(status, 'pending')
        self.assertTrue(any('linked issue has no reviewed contract' in reason for reason in reasons))
        self.assertTrue(any('gate infrastructure change requires reviewed policy update:' in reason for reason in reasons))


if __name__ == '__main__': unittest.main()
