"""Per-PR checkpoints are emitted only for acknowledged guarded statuses."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_native_merge as fixture

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

per_pr=load('per_pr_native_checkpoint',HERE.parent/'scripts/per_pr_checkpoint.py')
prepared=load('per_pr_native_prepared',HERE.parent/'scripts/prepared_status.py')
workers=load('per_pr_native_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
m=fixture.m


class PerPRNativeTests(unittest.TestCase):
    def setUp(self):
        f=fixture.DefaultWakeAndCheckpointTests();f.setUp()
        self.p=f.p;self.env=f.env;self.p.auto_complete=False
        self.policy=workers.policy(fixture.SOURCE);values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=per_pr.guard.build('o/r',1,fixture.HEAD,7,self.policy,300,1,lambda n:next(values))
        self.claim=dict(policy=self.policy,intent=self.intent)

    def run_writer(self,valid_response,valid_pending=True):
        original=self.p.api
        def api(path,method='GET',data=None):
            result=original(path,method,data)
            if method=='POST' and isinstance(data,dict) and data.get('state') in ('pending','success'):
                self.p.statuses[0]['creator']=dict(id=41898282,type='Bot',login='github-actions[bot]')
                if data['state']=='pending' and valid_pending or data['state']=='success' and valid_response:
                    return dict(self.p.statuses[0])
            return result
        self.p.api=api;out=io.StringIO()
        with patch.dict(os.environ,self.env,clear=True),contextlib.redirect_stdout(out):
            result=m.enqueue(self.p,'o/r',1,fixture.HEAD,self.p.load,fixture.EVALUATOR,200,
                             verify=self.p.verify,prepare_record=m.prepare_close_record,guarded_claim=self.claim)
        return result,out.getvalue()

    def test_acknowledged_guarded_success_emits_bound_checkpoint(self):
        result,log=self.run_writer(True)
        self.assertEqual(result['state'],'queued')
        bound=per_pr.find(log,'o/r',1,fixture.HEAD)
        self.assertIsNotNone(bound)
        self.assertEqual(per_pr.verify_status(self.p,'o/r',fixture.HEAD,bound,self.intent)['payload']['review_run'],200)
        self.assertIn('AW_PREPARED_STATUS_V1 ',log)

    def test_ambiguous_status_response_cannot_emit_checkpoint(self):
        result,log=self.run_writer(False)
        self.assertEqual(result['state'],'held_after_pending')
        self.assertNotIn(per_pr.MARKER,log)
        self.assertIn('AW_PREPARED_STATUS_V1 ',log)
        self.assertEqual([entry[0] for entry in self.p.writes],['pending','queue','success'])
        value=prepared.observation.review.r.records(log,prepared.MARKER)[0]
        observed=prepared.observe(value,self.p.statuses)
        self.assertEqual(observed['state'],'observed_success')
        self.assertFalse(observed['close_authorized'])

    def test_unacknowledged_pending_never_publishes_success(self):
        result,log=self.run_writer(True,valid_pending=False)
        self.assertEqual(result['state'],'held_after_pending')
        self.assertNotIn(prepared.MARKER,log)
        self.assertEqual([entry[0] for entry in self.p.writes],['pending','queue'])

    def test_guarded_request_without_checkpoint_provider_never_writes(self):
        with patch.dict(os.environ,self.env,clear=True):
            with self.assertRaisesRegex(RuntimeError,'guard_record_required'):
                m.enqueue(self.p,'o/r',1,fixture.HEAD,self.p.load,fixture.EVALUATOR,200,
                          verify=self.p.verify,guarded_claim=self.claim)
        self.assertEqual(self.p.writes,[])


if __name__=='__main__':unittest.main()
