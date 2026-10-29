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

    def execute(self,attempts,settle_error=False,block_error=False):
        self.guard.settle_error=settle_error
        def withdraw(api,repo,number,head):self.calls.append(('withdraw',number))
        def enqueue(api,repo,number,*args,**kwargs):
            self.calls.append(('enqueue',number));return dict(state='queued')
        def block(api,repo,number,head):
            self.calls.append(('block',number))
            if block_error:raise TimeoutError('unknown blocking status response')
        with patch.object(m,'withdraw_approval',side_effect=withdraw),patch.object(m,'enqueue',side_effect=enqueue), \
             patch.object(m,'block_unadmitted',side_effect=block):
            return m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,self.api.verify,
                               guarded=self.guard)

    def test_owned_issue_is_skipped_without_stopping_other_prs(self):
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        rows=self.execute(attempts)
        self.assertEqual([r['state'] for r in rows],['held','queued','held'])
        self.assertEqual(rows[0]['reason'],'guard_unavailable')
        self.assertIn(('block',1),self.calls)
        self.assertLess(self.calls.index(('block',1)),self.calls.index(('begin',2)))
        self.assertEqual(rows[2]['guard'],'released')
        self.assertNotIn(('withdraw',1),self.calls)
        self.assertIn(('withdraw',2),self.calls);self.assertIn(('enqueue',2),self.calls)
        self.assertIn(('settle',3),self.calls);self.assertNotIn(('settle',2),self.calls)

    def test_unknown_block_response_holds_only_that_pr_without_retry(self):
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        rows=self.execute(attempts,block_error=True)
        self.assertEqual(rows[0]['reason'],'guard_block_result_unknown')
        self.assertEqual(self.calls.count(('block',1)),1)
        self.assertEqual(rows[1]['state'],'queued')
        self.assertNotIn(('settle',1),self.calls)

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
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted'), \
             patch.object(m,'enqueue',side_effect=RuntimeError('unknown response')):
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,self.api.verify,
                             guarded=self.guard)
        self.assertEqual(rows[1]['reason'],'verification_or_provider_failed')
        self.assertNotIn(('settle',2),self.calls)


class BlockUnadmittedTests(unittest.TestCase):
    def setUp(self):
        self.statuses=[];self.writes=[];self.pull=dict(state='OPEN',headRefOid=HEAD,autoMergeRequest=None)
        self.actor=dict(id=41898282,type='Bot',login='github-actions[bot]')
        self.fail_after_write=False

    def status(self,number,state,actor=None):
        return dict(id=number,state=state,context='claude/verified-merge',creator=actor or self.actor)

    def pages(self,path):
        self.assertEqual(path,'repos/o/r/commits/'+HEAD+'/statuses')
        return copy.deepcopy(self.statuses)

    def api(self,path,method='GET',data=None):
        self.assertEqual((path,method),('repos/o/r/statuses/'+HEAD,'POST'))
        self.writes.append(copy.deepcopy(data))
        status=dict(id=99,creator=copy.deepcopy(self.actor),**data)
        self.statuses.insert(0,status)
        if self.fail_after_write:raise TimeoutError('unknown response')
        return copy.deepcopy(status)

    def run_block(self,pulls=None):
        with patch.object(m,'provider_pull',side_effect=pulls if pulls is not None else lambda *args:copy.deepcopy(self.pull)):
            return m.block_unadmitted(self,'o/r',1,HEAD)

    def test_stale_success_gets_pending_without_cancelling_request(self):
        for actor in ('github-actions','human'):
            with self.subTest(actor=actor):
                self.setUp();self.statuses=[self.status(1,'success')]
                self.pull['autoMergeRequest']=dict(enabledBy=dict(login=actor))
                before=copy.deepcopy(self.pull['autoMergeRequest'])
                self.run_block()
                self.assertEqual([item['state'] for item in self.writes],['pending'])
                self.assertEqual(self.pull['autoMergeRequest'],before)

    def test_queued_request_without_status_gets_pending(self):
        self.pull['autoMergeRequest']=dict(enabledBy=dict(login='another-actor'))
        self.run_block()
        self.assertEqual([item['state'] for item in self.writes],['pending'])

    def test_missing_status_and_request_still_blocks_a_same_named_check_run(self):
        self.run_block()
        self.assertEqual([item['state'] for item in self.writes],['pending'])

    def test_existing_trusted_block_is_not_repeated(self):
        self.statuses=[self.status(2,'pending'),self.status(1,'success')]
        self.pull['autoMergeRequest']=dict(enabledBy=dict(login='human'))
        self.run_block();self.assertEqual(self.writes,[])

    def test_foreign_pending_cannot_hide_old_actions_success(self):
        foreign=dict(id=42,type='User',login='other')
        self.statuses=[self.status(2,'pending',foreign),self.status(1,'success')]
        self.run_block();self.assertEqual([item['state'] for item in self.writes],['pending'])

    def test_ambiguous_write_is_not_retried_or_used_as_approval(self):
        self.pull['autoMergeRequest']=dict(enabledBy=dict(login='human'));self.fail_after_write=True
        with self.assertRaises(TimeoutError):self.run_block()
        self.assertEqual([item['state'] for item in self.writes],['pending'])
        self.assertIsNotNone(self.pull['autoMergeRequest'])

    def test_pending_superseded_before_readback_is_unknown(self):
        self.pull['autoMergeRequest']=dict(enabledBy=dict(login='human'))
        original=self.pages;calls=[]
        def pages(path):
            calls.append(True)
            result=original(path)
            if len(calls)==2:result.insert(0,self.status(100,'success'))
            return result
        with patch.object(self,'pages',side_effect=pages), \
             self.assertRaisesRegex(RuntimeError,'guard_block_result_unknown'):
            self.run_block()
        self.assertEqual([item['state'] for item in self.writes],['pending'])
        self.assertIsNotNone(self.pull['autoMergeRequest'])

    def test_head_change_after_pending_is_unknown(self):
        self.pull['autoMergeRequest']=dict(enabledBy=dict(login='human'))
        changed=dict(self.pull,headRefOid='f'*40)
        with self.assertRaisesRegex(RuntimeError,'guard_block_target_changed'):
            self.run_block([self.pull,changed])
        self.assertEqual([item['state'] for item in self.writes],['pending'])

    def test_changed_head_or_unreadable_status_never_silently_succeeds(self):
        wrong=dict(self.pull,headRefOid='f'*40)
        with self.assertRaisesRegex(RuntimeError,'guard_block_target_changed'):self.run_block([wrong])
        self.assertEqual(self.writes,[])
        self.statuses=[dict(id=None,state='success',context='claude/verified-merge')]
        with self.assertRaisesRegex(RuntimeError,'guard_block_status_unknown'):self.run_block()
        self.assertEqual(self.writes,[])


if __name__=='__main__':unittest.main()
