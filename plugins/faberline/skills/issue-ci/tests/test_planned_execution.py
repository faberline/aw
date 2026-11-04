import copy
import hashlib
import json
import unittest
import test_unregistered_execution as base


class PlannedExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.UnregisteredExecutionTests()
        self.fixture.setUp()
        self.config = self.fixture.policy
        self.config['planner_acceptance_mapping'] = {'version': 1, 'trusted_actor_ids': [42]}
        self.config['review_readiness'] = {'version': 1,
            'producer': {'path':'.github/workflows/evidence.yml','id':99,'definition_sha':'e'*40,'job':'publish'},
            'sources': {'enforcer': {'repo':'o/r','ref':'e'*40,'path':'scripts/test-enforcer.py','blob_sha':'f'*40}},
            'assertions': {name:{'names':[name+'::actual'],'enforcers':['enforcer']}
                           for name in self.config['qualification']['checks']}}
        self.issue = self.fixture.issue
        self.issue['body'] = '## Goal\nFix\n## Acceptance\n- [ ] Expected outcome\n## Never\nKeep behavior\n## Gate\nTests\n'
        self.mapping = {'schema': 'AW_ACCEPTANCE_PLAN_V1',
                        'spec_sha256': hashlib.sha256(self.issue['body'].encode()).hexdigest(),
                        'items': [{'acceptance': 'Expected outcome', 'checks': ['unit']}]}
        self.comments = [{'id': 12, 'user': {'id': 42}, 'updated_at': '2026-11-04T00:00:00Z',
                          'body': '<!-- issue-plan:acceptance-map:v1 -->\n' + json.dumps(self.mapping)}]

    def run_plan(self):
        return base.planner.execute_planned_issue(self.config, 'o/r', base.fixtures.base.HEAD,
            base.fixtures.base.BASE, pull=self.fixture.pull, files=self.fixture.files, issue=self.issue,
            comments=self.comments, definitions=self.fixture.definitions)

    def test_mapping_is_bound_to_full_roster_but_never_qualifies(self):
        result = self.run_plan()
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.assertEqual(result['publication']['comment_id'], 12)
        self.assertEqual(result['publication']['checks'], ['unit'])
        self.assertEqual(result['execution']['plan']['selected'], sorted(self.config['qualification']['checks']))
        self.assertIn('linked issue has no reviewed contract', result['execution']['unresolved'])

    def test_missing_untrusted_or_policy_only_mapping_refuses(self):
        for change in ('missing', 'untrusted', 'policy', 'stale', 'newer_invalid'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'missing': self.comments = []
                if change == 'untrusted': self.comments[0]['user']['id'] = 99
                if change == 'policy':
                    self.mapping['items'][0]['checks'] = ['policy']
                    self.comments[0]['body'] = '<!-- issue-plan:acceptance-map:v1 -->\n' + json.dumps(self.mapping)
                if change == 'stale': self.issue['body'] += 'changed'
                if change == 'newer_invalid': self.comments.append({'id': 13, 'user': {'id': 99},
                                                                    'body': self.comments[0]['body']})
                with self.assertRaises(ValueError): self.run_plan()

    def test_invalid_policy_and_untrusted_workflow_still_refuse(self):
        for change in ('disabled', 'workflow', 'issue', 'head', 'files'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'disabled': del self.config['planner_acceptance_mapping']
                if change == 'workflow': self.fixture.definitions[(base.fixtures.base.WORKFLOW, base.fixtures.base.HEAD)] = {'sha': 'f' * 40}
                if change == 'issue': self.issue['state'] = 'closed'
                if change == 'head': self.fixture.pull['head']['sha'] = 'd' * 40
                if change == 'files': self.fixture.pull['changed_files'] = 2
                with self.assertRaises(ValueError): self.run_plan()

    def test_protected_paths_and_existing_contract_holds_remain(self):
        self.config['qualification']['protected_paths'].append('src/**')
        result = self.run_plan()
        self.assertTrue(any('gate infrastructure change requires reviewed policy update' in reason
                            for reason in result['execution']['unresolved']))
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.config['qualification']['contracts']['7'] = copy.deepcopy(self.config['qualification']['contracts']['8'])
        self.config['qualification']['contracts']['7']['unresolved'] = ['missing required evidence']
        with self.assertRaises(ValueError): self.run_plan()

    def test_case_evidence_binds_mapping_and_complete_roster_without_qualification(self):
        selected = self.run_plan()
        plan = selected['execution']['plan']
        receipts = []
        for name in plan['selected']:
            tests = [name + '::actual']
            receipts.append(dict(check=name, repo=plan['repo'], head=plan['head'], base=plan['base'],
                                 plan_sha256=plan['sha256'], conclusion='success', expected=tests,
                                 executed=tests, failures=[], skipped=[]))
        extra = dict(pull=self.fixture.pull, files=self.fixture.files, definitions=self.fixture.definitions)
        result = base.planner.verify_planned_execution(self.config, selected, self.issue, self.comments, receipts, **extra)
        self.assertEqual(result['qualification'], 'not_evaluated')
        self.assertIn('linked issue has no reviewed contract', result['unresolved'])
        for change in ('missing', 'stale', 'skipped', 'mapping', 'policy', 'omitted_test'):
            with self.subTest(change=change):
                current_receipts = copy.deepcopy(receipts)
                current_issue, current_comments, config = copy.deepcopy(self.issue), copy.deepcopy(self.comments), copy.deepcopy(self.config)
                if change == 'missing': current_receipts.pop()
                if change == 'stale': current_receipts[0]['head'] = 'f' * 40
                if change == 'skipped': current_receipts[0]['skipped'] = ['test']
                if change == 'mapping': current_comments.append({'id': 13, 'user': {'id': 42}, 'updated_at':'2026-11-04T00:00:00Z',
                                                                  'body': current_comments[0]['body']})
                if change == 'policy': config['planner_acceptance_mapping']['trusted_actor_ids'] = [42, 43]
                if change == 'omitted_test':
                    target = next(row for row in current_receipts if row['expected'])
                    target['expected'] = target['executed'] = ['invented::test']
                with self.assertRaises(ValueError):
                    base.planner.verify_planned_execution(config, selected, current_issue, current_comments, current_receipts, **extra)

    def test_missing_reviewed_assertion_roster_is_never_replaced_by_receipts(self):
        del self.config['review_readiness']['assertions']['unit']
        selected=self.run_plan();plan=selected['execution']['plan']
        receipts=[dict(check=name,repo=plan['repo'],head=plan['head'],base=plan['base'],plan_sha256=plan['sha256'],
                       conclusion='success',expected=[name+'::actual'],executed=[name+'::actual'],failures=[],skipped=[])
                  for name in plan['selected']]
        with self.assertRaisesRegex(ValueError,'reviewed expected-test inventory is incomplete'):
            base.planner.verify_planned_execution(self.config,selected,self.issue,self.comments,receipts,
                pull=self.fixture.pull,files=self.fixture.files,definitions=self.fixture.definitions)


if __name__ == '__main__':
    unittest.main()
