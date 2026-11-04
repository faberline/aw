import copy
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_readiness as fixture

spec=importlib.util.spec_from_file_location('planned_producer_test',Path(__file__).parents[1]/'scripts/planned_producer.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
r=fixture.r


class PlannedProducerTests(unittest.TestCase):
    def setUp(self):
        self.base='a'*40;self.head='b'*40;self.evaluator='c'*40
        self.document={'source_commit':self.base,'config':{'workflows':[{'path':'.github/workflows/ci.yml'}]}}
        self.producer={'head':self.base,'event':'workflow_run','run_id':10}
        self.wake={'id':20,'run_attempt':1,'event':'pull_request','status':'completed',
                   'path':'.github/workflows/ci.yml','head_sha':self.head,'head_repository':{'full_name':'o/r'}}
        self.event={'action':'completed','workflow_run':copy.deepcopy(self.wake)}
        self.pulls=[self.pull(1),self.pull(2),self.pull(3,'d'*40)]
        self.calls=[];self.max_log=r.MAX_LOG;self.padding={}

    def pull(self,n,head=None):
        return {'number':n,'state':'open','draft':False,'body':f'Closes #{n}',
                'head':{'sha':head or self.head,'repo':{'full_name':'o/r'}},
                'base':{'sha':self.base,'repo':{'full_name':'o/r'}}}

    def invoke(self,event_name='workflow_run',event=None):
        def produce(ci,repo,n,head,load_document,evaluator,run_id):
            self.calls.append(n)
            if n==1:raise RuntimeError('candidate unavailable')
            return {'payload':{'producer':copy.deepcopy(self.producer),'repo':repo,'pr':n,'head':head,
                               'padding':'x'*self.padding.get(n,0)},'sha256':'d'*64}
        planned=SimpleNamespace(produce=produce,PLANNED_EVENTS=('workflow_run','workflow_dispatch'),MARKER='AW_PLANNED_EXECUTION_V1 ')
        fake=SimpleNamespace(validate_contract=lambda *a:None,producer_identity=lambda *a,**k:copy.deepcopy(self.producer),
                             pin_proof=lambda *a,**k:None,need=r.need,HEX=r.HEX,canonical=r.canonical,MAX_LOG=self.max_log)
        ci=SimpleNamespace(api=lambda path:({'state':'open','labels':[{'name':'doing'}]} if '/issues/' in path else copy.deepcopy(self.wake)),pages=lambda *args:copy.deepcopy(self.pulls),
                           qualification_module=lambda:SimpleNamespace(linked_issue=lambda pull:int(pull['body'].split('#')[1])))
        with patch.object(module,'sibling',side_effect=lambda name:{'readiness':fake,'planned_readiness':planned}[name]), \
             patch.object(module,'validate_source'):
            return module.produce(ci,'o/r',lambda:copy.deepcopy(self.document),self.evaluator,10,event_name,
                                  copy.deepcopy(self.event if event is None else event))

    def test_one_held_pr_does_not_block_another_same_head(self):
        result=self.invoke()
        self.assertEqual(self.calls,[1,2])
        self.assertEqual(result['held'],[1])
        self.assertEqual([row['payload']['pr'] for row in result['receipts']],[2])
        self.assertEqual(result['qualification'],'not_evaluated')

    def test_wrong_wake_attempt_or_repository_refuses(self):
        for change in ('attempt','foreign','head','unconfigured'):
            self.setUp()
            if change=='attempt':self.event['workflow_run']['run_attempt']=2
            if change=='foreign':self.event['workflow_run']['head_repository']={'full_name':'x/r'}
            if change=='head':self.event['workflow_run']['head_sha']='e'*40
            if change=='unconfigured':self.event['workflow_run']['path']='.github/workflows/other.yml'
            with self.subTest(change=change),self.assertRaises(RuntimeError):self.invoke()
            self.assertEqual(self.calls,[])

    def test_targeted_reconciliation_processes_only_the_requested_pr(self):
        self.producer['event']='workflow_dispatch'
        result=self.invoke('workflow_dispatch',{'inputs':{'pr':'3'}})
        self.assertEqual(self.calls,[3])
        self.assertEqual([row['payload']['pr'] for row in result['receipts']],[3])
        for inputs in ({}, {'pr':'0'}, {'pr':'3','head':'x'}, {'pr':'3;bad'}):
            with self.assertRaises(RuntimeError):self.invoke('workflow_dispatch',{'inputs':inputs})

    def test_duplicate_inventory_refuses_instead_of_silent_omission(self):
        self.pulls.append(copy.deepcopy(self.pulls[0]))
        with self.assertRaisesRegex(RuntimeError,'planned_producer_inventory'):self.invoke()

    def test_oversized_receipt_does_not_block_other_candidates(self):
        self.pulls=[self.pull(2),self.pull(3)]
        self.max_log=2000;self.padding={2:2000}
        result=self.invoke()
        self.assertEqual(result['held'],[2])
        self.assertEqual([row['payload']['pr'] for row in result['receipts']],[3])

    def test_producer_source_is_exact_read_only_and_scoped_to_configured_ci(self):
        path='.github/workflows/proof.yml';ci_path='.github/workflows/ci.yml'
        self.document['config'].update(review_readiness={'producer':{'path':path,'definition_sha':'d'*40,
                                          'job':'planned test evidence'}},
            qualification={'trusted_workflows':{ci_path:{'definition_sha':'e'*40}}})
        template=(Path(__file__).parents[1]/'templates/planned-producer.yml.example').read_text()
        template=template.replace('PRODUCT_CI_WORKFLOW_NAME','CI').replace('REVIEWED_AW_COMMIT',self.evaluator)
        ci=SimpleNamespace(development_module=fixture.ci.development_module)
        def source(ci,repo,path,ref,sha):return template if path.endswith('proof.yml') else 'name: CI\n'
        with patch.object(r,'source_bytes',side_effect=source):
            module.validate_source(ci,r,'o/r',self.document,self.evaluator)
        for invalid in (template.replace('actions: read','actions: write'),
                        template.replace('workflows: ["CI"]','workflows: ["CI", "Other"]'),
                        template.replace('operation: planned-produce','operation: handoff'),
                        template.replace('        type: string','        type: choice')):
            with self.subTest(invalid=invalid),patch.object(r,'source_bytes',side_effect=lambda ci,repo,path,ref,sha:
                    invalid if path.endswith('proof.yml') else 'name: CI\n'),self.assertRaises(RuntimeError):
                module.validate_source(ci,r,'o/r',self.document,self.evaluator)


if __name__=='__main__':unittest.main()
