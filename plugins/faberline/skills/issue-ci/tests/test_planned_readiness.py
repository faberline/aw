import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_readiness as previous
import test_issue_contract_v2 as fixtures

spec = importlib.util.spec_from_file_location('planned_readiness_test', Path(__file__).parents[1] / 'scripts/planned_readiness.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PlannedReadinessTests(unittest.TestCase):
    def setUp(self):
        self.h = previous.Harness()
        self.h.config['lifecycle_mode'] = 'full'
        self.h.config['qualification']['version'] = 2
        self.h.config['qualification']['contracts'] = {'9': fixtures.policy()['qualification']['contracts']['7']}
        self.h.config['planner_acceptance_mapping'] = {'version': 1, 'trusted_actor_ids': [42]}
        for value in self.h.config['qualification']['checks'].values():
            value['job'] = 'policy'
        self.h.pull['base']['repo'] = {'full_name': 'o/r'}
        self.h.pull['draft'] = False
        self.h.changes = previous.qualified.files('src/code.rs')
        self.h.producer['head_sha'] = previous.BASE
        self.issue = fixtures.issue()
        self.issue['body'] = '## Goal\nFix\n## Acceptance\n- [ ] A result\n## Never\nKeep\n## Gate\nTest\n'
        mapping = {'schema':'AW_ACCEPTANCE_PLAN_V1','spec_sha256':hashlib.sha256(self.issue['body'].encode()).hexdigest(),
                   'items':[{'acceptance':'A result','checks':['unit']}]}
        self.comments = [{'id':12,'user':{'id':42},'updated_at':'2026-11-04T00:00:00Z',
                          'body':'<!-- issue-plan:acceptance-map:v1 -->\n'+json.dumps(mapping)}]
        records = []
        for check in self.h.config['qualification']['checks']:
            row = self.h.assertion(); row['check'] = check; row['assertions'] = {check+'-assertion':'passed'}
            records.append(previous.r.ASSERTION_MARKER + json.dumps(row))
        self.h.logs[101] = '\n'.join(records)
        self.planner = module.load(Path(__file__).parents[1] / 'scripts/test_plan.py', 'test_plan')
        self.mapping = module.load(Path(__file__).parents[1] / 'scripts/acceptance_plan.py', 'acceptance_plan')
        self.after_second = None

    def document(self):
        return dict(config=copy.deepcopy(self.h.config), source_commit=previous.BASE,
                    path='.github/issue-ci-required-gates.json')

    def run_collection(self):
        pull_reads = 0
        def api(path, *args, **kwargs):
            nonlocal pull_reads
            if path == 'repos/o/r/pulls/8':
                pull_reads += 1
                if pull_reads == 3 and self.after_second:
                    self.after_second()
            if path == 'repos/o/r/issues/7': return copy.deepcopy(self.issue)
            return self.h.api(path, *args, **kwargs)
        def pages(path, *args):
            if path == 'repos/o/r/issues/7/comments': return copy.deepcopy(self.comments)
            return self.h.pages(path, *args)
        def load(path, name):
            return {'readiness': previous.r, 'test_plan': self.planner, 'acceptance_plan': self.mapping}[name]
        with patch.object(module, 'load', side_effect=load), patch.object(previous.ci, 'api', side_effect=api), \
             patch.object(previous.ci, 'pages', side_effect=pages), \
             patch.object(previous.r, 'job_log', side_effect=lambda repo, job: self.h.logs[job]):
            return module.collect(previous.ci, 'o/r', 8, previous.HEAD, self.document, previous.EVALUATOR, 90, True)

    def test_authenticated_full_roster_is_evidence_only(self):
        value = self.run_collection()
        self.assertEqual(value['qualification'], 'not_evaluated')
        self.assertEqual({row['check'] for row in value['assertions']}, set(self.h.config['qualification']['checks']))
        self.assertIn('linked issue has no reviewed contract', value['selection']['execution']['unresolved'])
        self.assertEqual(self.h.writes, [])

    def test_untrusted_producer_failed_or_missing_test_never_proves_execution(self):
        for change in ('producer', 'failed', 'missing', 'base', 'mapping', 'foreign-run', 'skipped-job'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'producer': self.h.producer['head_sha'] = 'f'*40
                if change == 'base': self.h.pull['base']['sha'] = 'f'*40
                if change == 'mapping': self.comments[0]['user']['id'] = 99
                if change == 'foreign-run': self.h.run['pull_requests'][0]['base']['sha'] = 'e'*40
                if change == 'skipped-job': self.h.job['conclusion'] = 'skipped'
                if change == 'failed': self.h.logs[101] = self.h.logs[101].replace('"unit-assertion": "passed"', '"unit-assertion": "failed"')
                if change == 'missing': self.h.logs[101] = '\n'.join(line for line in self.h.logs[101].splitlines() if 'unit-assertion' not in line)
                with self.assertRaises((RuntimeError, ValueError)): self.run_collection()

    def test_changes_between_complete_observations_never_produce_evidence(self):
        original = self.document
        reads = 0
        def changed_document():
            nonlocal reads
            reads += 1
            value = original()
            if reads == 2:
                value['config']['planner_acceptance_mapping']['trusted_actor_ids'].append(43)
            return value
        self.document = changed_document
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
            self.run_collection()

    def test_final_recheck_refuses_head_base_mapping_and_attempt_changes(self):
        for change in ('head','base','mapping','attempt','producer','draft','number'):
            self.setUp()
            def mutate():
                if change == 'head': self.h.pull['head']['sha'] = 'f'*40
                if change == 'base': self.h.pull['base']['sha'] = 'f'*40
                if change == 'mapping': self.comments[0]['body'] += ' '
                if change == 'attempt': self.h.run['run_attempt'] = 3
                if change == 'producer': self.h.producer['run_attempt'] = 2
                if change == 'draft': self.h.pull['draft'] = True
                if change == 'number': self.h.pull['number'] = 9
            self.after_second = mutate
            with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError)):
                self.run_collection()

    def test_receipt_requires_exact_authenticated_producer_log(self):
        payload = self.run_collection()
        receipt = {'payload': payload, 'sha256': previous.r.digest(payload)}
        producer_log = module.MARKER + json.dumps(receipt)
        def api(path, *args, **kwargs):
            if path == 'repos/o/r/issues/7': return copy.deepcopy(self.issue)
            return self.h.api(path, *args, **kwargs)
        def pages(path, *args):
            if path == 'repos/o/r/issues/7/comments': return copy.deepcopy(self.comments)
            return self.h.pages(path, *args)
        def load(path, name):
            return {'readiness': previous.r, 'test_plan': self.planner, 'acceptance_plan': self.mapping}[name]
        with patch.object(module,'load',side_effect=load), patch.object(previous.ci,'api',side_effect=api), \
             patch.object(previous.ci,'pages',side_effect=pages), \
             patch.object(previous.r,'job_log',side_effect=lambda repo, job: producer_log if job == 901 else self.h.logs[job]):
            self.assertEqual(module.verify(previous.ci, receipt, 'o/r', 8, previous.HEAD,
                                           self.document, previous.EVALUATOR),receipt)
            for invalid_log in ('', producer_log+'\n'+producer_log):
                with self.subTest(log=invalid_log), patch.object(previous.r,'job_log',side_effect=lambda repo, job:
                     invalid_log if job == 901 else self.h.logs[job]):
                    with self.assertRaisesRegex(RuntimeError,'unpublished_receipt'):
                        module.verify(previous.ci,receipt,'o/r',8,previous.HEAD,self.document,previous.EVALUATOR)
            forged=copy.deepcopy(receipt);forged['payload']['assertions'][0]['count']=100
            forged['sha256']=previous.r.digest(forged['payload'])
            with self.assertRaisesRegex(RuntimeError,'unpublished_receipt'):
                module.verify(previous.ci,forged,'o/r',8,previous.HEAD,self.document,previous.EVALUATOR)


if __name__ == '__main__':
    unittest.main()
