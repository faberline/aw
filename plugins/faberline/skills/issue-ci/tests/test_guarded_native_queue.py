"""Guarded queue adapter controls; no real refs or statuses are changed."""
import copy
import unittest
from unittest.mock import patch
import test_native_merge as fixture

m=fixture.m;HEAD=fixture.HEAD;EVALUATOR=fixture.EVALUATOR


class GuardedQueueTests(unittest.TestCase):
    def setUp(self):
        self.api=fixture.Provider()
        self.pulls=[dict(self.api.pull,number=i) for i in (1,2,3)]
        self.api.pages=lambda path,key=None:copy.deepcopy(self.pulls) if '/pulls?' in path else []
        self.calls=[]
        class Guard:
            def __init__(self,outer):self.outer=outer;self.settle_error=False
            def begin(self,api,repo,document,evaluator,number,head,emit):
                self.outer.calls.append(('begin',number))
                if number==1:raise RuntimeError('worker owns issue')
                return dict(policy={},intent={'pr':number})
            def settle(self,api,repo,policy,intent):
                self.outer.calls.append(('settle',intent['pr']))
                if self.settle_error:raise TimeoutError('unknown')
                return dict(state='released')
        self.guard=Guard(self)

    def execute(self,attempts,settle_error=False):
        self.guard.settle_error=settle_error
        def withdraw(api,repo,number,head):self.calls.append(('withdraw',number))
        def enqueue(api,repo,number,*args,**kwargs):
            self.calls.append(('enqueue',number));return dict(state='queued')
        with patch.object(m,'withdraw_approval',side_effect=withdraw),patch.object(m,'enqueue',side_effect=enqueue):
            return m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,self.api.verify,
                               guarded=self.guard)

    def test_owned_issue_is_skipped_without_stopping_other_prs(self):
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        rows=self.execute(attempts)
        self.assertEqual([r['state'] for r in rows],['held','queued','held'])
        self.assertEqual(rows[0]['reason'],'guard_unavailable')
        self.assertEqual(rows[2]['guard'],'released')
        self.assertNotIn(('withdraw',1),self.calls)
        self.assertIn(('withdraw',2),self.calls);self.assertIn(('enqueue',2),self.calls)
        self.assertIn(('settle',3),self.calls);self.assertNotIn(('settle',2),self.calls)

    def test_unknown_guard_release_holds_the_result(self):
        rows=self.execute({},settle_error=True)
        self.assertEqual(rows[1]['state'],'held_after_pending')
        self.assertEqual(rows[1]['reason'],'guard_result_unknown')

    def test_duplicate_queue_is_refused_before_any_mutation(self):
        self.pulls.append(copy.deepcopy(self.pulls[0]))
        with self.assertRaisesRegex(RuntimeError,'guard_queue_unknown'):self.execute({})
        self.assertEqual(self.calls,[])

    def test_unknown_enqueue_result_retains_claim(self):
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        with patch.object(m,'withdraw_approval'),patch.object(m,'enqueue',side_effect=RuntimeError('unknown response')):
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,self.api.verify,
                             guarded=self.guard)
        self.assertEqual(rows[1]['reason'],'verification_or_provider_failed')
        self.assertNotIn(('settle',2),self.calls)


if __name__=='__main__':unittest.main()
