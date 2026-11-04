import copy
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_planned_readiness as fixture
import test_lifecycle as legacy

spec=importlib.util.spec_from_file_location('planned_handoff_test',Path(__file__).parents[1]/'scripts/planned_handoff.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
ci=legacy.ci


class PlannedHandoffTests(unittest.TestCase):
    def setUp(self):
        self.test=fixture.PlannedReadinessTests();self.test.setUp()
        self.payload=self.test.run_collection()
        self.document=self.test.document();self.document['config']['completion_owner']='local'
        self.receipt={'payload':self.payload,'sha256':'d'*64}
        self.context={'head':fixture.previous.HEAD,'base':fixture.previous.BASE,
                      'issues':['linked issue has no reviewed contract'],'untrusted_workflows':[]}
        self.valid=True;self.calls=0;self.fail_second=False

    def run_qualify(self):
        def verify(*args):
            self.calls+=1
            if not self.valid or (self.fail_second and self.calls==2):raise RuntimeError('unpublished or stale')
            return self.receipt
        r=SimpleNamespace(need=fixture.previous.r.need)
        planned=SimpleNamespace(verify=verify)
        ci=SimpleNamespace(api=lambda path:copy.deepcopy(self.test.h.pull),
                           qualify_config=lambda *args:{'_qualification_context':copy.deepcopy(self.context)})
        with patch.object(module,'sibling',side_effect=lambda name:{'readiness':r,'planned_readiness':planned}[name]):
            return module.qualify(ci,'o/r',8,fixture.previous.HEAD,lambda:copy.deepcopy(self.document),
                                  fixture.previous.EVALUATOR,self.receipt)

    def test_only_verified_complete_evidence_removes_missing_contract_hold(self):
        result=self.run_qualify();self.assertEqual(result['_qualification_context']['issues'],[])
        self.assertEqual(result['_qualification_context']['planned_receipt_sha256'],self.receipt['sha256'])
        self.assertEqual(self.calls,2)

    def test_stale_protected_failure_only_and_missing_roster_hold(self):
        for change in ('unpublished','changed','protected','global','failure-only','roster','base'):
            self.setUp()
            if change=='unpublished':self.valid=False
            if change=='changed':self.fail_second=True
            if change=='protected':self.payload['selection']['execution']['unresolved'].append('protected path')
            if change=='global':self.document['config']['qualification_blockers']=['hold']
            if change=='failure-only':self.document['config']['lifecycle_mode']='failure-only'
            if change=='roster':self.payload['selection']['execution']['plan']['selected'].pop()
            if change=='base':self.context['base']='f'*40
            with self.subTest(change=change),self.assertRaises(RuntimeError):self.run_qualify()

    def test_receipt_must_come_once_from_exact_current_producer_job(self):
        r=fixture.previous.r;producer=self.payload['producer'];head=fixture.previous.HEAD
        self.receipt['payload'].update(repo='o/r',pr=8,head=head)
        rows=[self.receipt]
        readiness=SimpleNamespace(validate_contract=lambda *a:None,producer_identity=lambda *a,**k:producer,
                                  pin_proof=lambda *a,**k:None,job_log=lambda *a:'log',
                                  records=lambda *a:copy.deepcopy(rows),need=r.need)
        planned=SimpleNamespace(MARKER='AW_PLANNED_EXECUTION_V1 ',PLANNED_EVENTS=('workflow_run','workflow_dispatch'),verify=lambda ci,receipt,*a:receipt)
        producer_module=SimpleNamespace(validate_source=lambda *args:None)
        ci=SimpleNamespace()
        with patch.object(module,'sibling',side_effect=lambda name:{'readiness':readiness,'planned_readiness':planned,'planned_producer':producer_module}[name]):
            result=module.receipt_for(ci,'o/r',8,head,lambda:copy.deepcopy(self.document),
                                      fixture.previous.EVALUATOR,producer['run_id'])
            self.assertEqual(result,self.receipt)
            rows.append(copy.deepcopy(self.receipt))
            with self.assertRaisesRegex(RuntimeError,'planned_receipt_missing'):
                module.receipt_for(ci,'o/r',8,head,lambda:copy.deepcopy(self.document),
                                   fixture.previous.EVALUATOR,producer['run_id'])

    def test_writer_retains_holds_for_missing_or_old_attempt_receipts(self):
        ordinary={'_qualification_context':{'issues':['linked issue has no reviewed contract']}}
        qualified={'_qualification_context':{'issues':[]}}
        pr={'number':8,'head':{'sha':fixture.previous.HEAD}}
        config={'planned_ci_qualification':{'version':1}}
        receipt={'payload':{'producer':{'run_attempt':2}}}
        def receipt_for(adapter,*args):
            self.assertIs(adapter.development_module,ci.development_module)
            return receipt
        fake=SimpleNamespace(receipt_for=receipt_for,qualify=lambda *args:qualified,
                             discover=lambda *args:receipt)
        with patch.object(ci,'qualify_config',return_value=ordinary),patch.object(ci,'planned_handoff_module',return_value=fake):
            self.assertIs(ci.planned_aware_qualify('o/r',pr,config,lambda:None,'e'*40,None),ordinary)
            self.assertIs(ci.planned_aware_qualify('o/r',pr,config,lambda:None,'e'*40,(90,1)),ordinary)
            self.assertIs(ci.planned_aware_qualify('o/r',pr,config,lambda:None,'e'*40,(90,2)),qualified)
            self.assertIs(ci.planned_aware_qualify('o/r',pr,config,lambda:None,'e'*40,'discover'),qualified)
            protected={'_qualification_context':{'issues':['gate infrastructure change requires reviewed policy update: x']}}
            with patch.object(ci,'qualify_config',return_value=protected):
                self.assertIs(ci.planned_aware_qualify('o/r',pr,config,lambda:None,'e'*40,(90,2)),protected)

    def test_discovery_skips_unusable_old_receipts_and_verifies_current_one(self):
        self.document['config']['review_readiness']['producer']['path']='.github/workflows/planned.yml'
        rows=[{'id':2,'status':'completed','conclusion':'success','event':'workflow_dispatch','head_sha':fixture.previous.BASE},
              {'id':3,'status':'completed','conclusion':'success','event':'workflow_run','head_sha':fixture.previous.BASE}]
        adapter=SimpleNamespace(pages=lambda *args:copy.deepcopy(rows))
        readiness=SimpleNamespace(validate_contract=lambda *args:None,need=fixture.previous.r.need)
        calls=[]
        def exact(*args):
            calls.append(args[-1])
            if args[-1]==3:raise RuntimeError('expired log')
            return self.receipt
        with patch.object(module,'sibling',return_value=readiness),patch.object(module,'receipt_for',side_effect=exact):
            result=module.discover(adapter,'o/r',8,fixture.previous.HEAD,lambda:copy.deepcopy(self.document),'e'*40)
            self.assertEqual(result,self.receipt)
            self.assertEqual(calls,[3,2])
            rows.append(copy.deepcopy(rows[0]))
            with self.assertRaisesRegex(RuntimeError,'planned_inventory'):
                module.discover(adapter,'o/r',8,fixture.previous.HEAD,lambda:copy.deepcopy(self.document),'e'*40)

    def test_discovery_uses_unfiltered_complete_inventory_beyond_1000_runs(self):
        self.document['config']['review_readiness']['producer']['path']='.github/workflows/planned.yml'
        rows=[{'id':n,'status':'completed','conclusion':'success','event':'workflow_run',
               'head_sha':fixture.previous.BASE if n==1 else 'f'*40} for n in range(1,1003)]
        def pages(path,key):
            self.assertNotIn('?',path)
            return rows
        readiness=SimpleNamespace(validate_contract=lambda *args:None,need=fixture.previous.r.need)
        with patch.object(module,'sibling',return_value=readiness),patch.object(module,'receipt_for',return_value=self.receipt) as exact:
            self.assertEqual(module.discover(SimpleNamespace(pages=pages),'o/r',8,fixture.previous.HEAD,
                             lambda:copy.deepcopy(self.document),'e'*40),self.receipt)
            self.assertEqual(exact.call_args.args[-1],1)


if __name__=='__main__':unittest.main()
