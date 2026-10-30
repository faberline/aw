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
            def block_held(self,api,repo,policy,intent):
                m.block_unadmitted(api,repo,intent['pr'],HEAD)
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

    def test_direct_mode_finishes_with_no_second_guard_settle(self):
        self.pulls=[self.pulls[1]]
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        from types import SimpleNamespace
        direct=SimpleNamespace(merge_and_close=lambda *args,**kwargs:dict(state='closed',issue=7))
        with patch.object(m,'withdraw_approval') as withdraw, \
             patch.object(m.review,'sibling',return_value=direct),patch.object(m,'enqueue') as queue:
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,
                self.api.verify,guarded=self.guard,prepare_record=lambda *args:None,direct_mode=True)
        self.assertEqual(rows[0]['state'],'closed')
        self.assertNotIn(('settle',2),self.calls)
        withdraw.assert_called_once_with(self.api,'o/r',2,HEAD,cancel_request=False)
        queue.assert_not_called()

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
        self.assertEqual(rows[2]['state'],'held_after_pending')
        self.assertEqual(rows[2]['reason'],'guard_result_unknown')
        self.assertNotIn(('settle',3),self.calls)

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

    def test_never_reviewed_pr_releases_guard_on_two_consecutive_wakes(self):
        pulls=[self.pulls[1]];statuses=[];writes=[];held=[]
        old_api=self.api.api
        def pages(path,key=None):
            if '/pulls?' in path:return copy.deepcopy(pulls)
            if '/statuses' in path:return copy.deepcopy(statuses)
            return []
        def api(path,method='GET',data=None):
            if path==f'repos/o/r/statuses/{HEAD}' and method=='POST':
                writes.append(copy.deepcopy(data))
                item=dict(id=99,creator=dict(id=41898282,type='Bot',login='github-actions[bot]'),**data)
                statuses.insert(0,item);return copy.deepcopy(item)
            return old_api(path,method,data)
        self.api.pages=pages;self.api.api=api
        class Guard:
            def begin(self,api,repo,document,evaluator,number,head,emit):
                if held:raise RuntimeError('guard held')
                held.append(number);return dict(policy={},intent={'pr':number})
            def settle(self,api,repo,policy,intent):
                if not statuses or statuses[0]['state']!='pending':raise RuntimeError('not blocked')
                held.clear();return dict(state='released')
            def block_held(self,api,repo,policy,intent):
                if held!=[intent['pr']]:raise RuntimeError('guard changed')
                m.block_unadmitted(api,repo,intent['pr'],HEAD)
        guard=Guard();provider=dict(state='OPEN',headRefOid=HEAD,autoMergeRequest=None)
        with patch.object(m,'provider_pull',return_value=provider),patch.object(m,'withdraw_approval'):
            for _ in range(2):
                rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:{},self.api.verify,
                                 guarded=guard)
                self.assertEqual(rows[0]['reason'],'review_missing')
                self.assertEqual(rows[0]['guard'],'released')
                self.assertEqual(held,[])
        self.assertEqual(len(writes),1)
        self.assertEqual(statuses[0]['state'],'pending')

    def test_known_prewrite_qualification_holds_release_across_wakes(self):
        for reason in ('qualification_not_ready','issue_not_eligible','required_gates_not_success',
                       'review_stale','issue_contract_changed','pr_description_changed','base_changed'):
            with self.subTest(reason=reason):
                self.setUp();self.pulls=[self.pulls[1]]
                self.pulls[0]['head']['ref']='issue/7';ci_calls=[]
                attempts={2:dict(id=200,status='completed',conclusion='success')}
                with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block, \
                     patch.object(m,'enqueue',side_effect=m.review.Refused(reason)):
                    for _ in range(2):
                        rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,
                                         self.api.verify,guarded=self.guard,
                                         ci_process=lambda args,config,check_event,before_mutation:ci_calls.append(args.pr))
                        self.assertEqual(rows[0]['reason'],reason)
                        self.assertEqual(rows[0]['guard'],'released')
                self.assertEqual(block.call_count,2)
                self.assertEqual(ci_calls,[2,2])

    def test_unknown_ci_handoff_retains_guard(self):
        self.pulls=[self.pulls[1]];self.pulls[0]['head']['ref']='issue/7'
        def unknown(*args,**kwargs):raise TimeoutError('unknown handoff outcome')
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block:
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:{},self.api.verify,
                             ci_process=unknown,guarded=self.guard)
        self.assertEqual(rows[0]['reason'],'withdrawal_or_ci_result_unknown')
        self.assertNotIn(('settle',2),self.calls)
        block.assert_not_called()

    def test_known_ci_prewrite_refusal_releases_guard_on_two_wakes(self):
        self.pulls=[self.pulls[1]];self.pulls[0]['head']['ref']='issue/7'
        def refusal(*args,**kwargs):raise RuntimeError('invalid CI qualification')
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block:
            for _ in range(2):
                rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:{},self.api.verify,
                                 ci_process=refusal,guarded=self.guard)
                self.assertEqual(rows[0]['reason'],'ci_handoff_refused_prewrite')
                self.assertEqual(rows[0]['guard'],'released')
        self.assertEqual(block.call_count,2)

    def test_ci_error_after_possible_write_retains_guard(self):
        self.pulls=[self.pulls[1]];self.pulls[0]['head']['ref']='issue/7'
        def refusal(*args,**kwargs):
            kwargs['before_mutation']()
            raise RuntimeError('unknown write outcome')
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block:
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:{},self.api.verify,
                             ci_process=refusal,guarded=self.guard)
        self.assertEqual(rows[0]['reason'],'withdrawal_or_ci_result_unknown')
        self.assertNotIn(('settle',2),self.calls)
        block.assert_not_called()

    def test_refusal_after_first_mutation_never_releases_guard(self):
        self.pulls=[self.pulls[1]]
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        def after_mutation(*args,**kwargs):
            kwargs['before_mutation']()
            raise m.review.Refused('qualification_not_ready')
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block, \
             patch.object(m,'enqueue',side_effect=after_mutation):
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,
                             self.api.verify,guarded=self.guard)
        self.assertEqual(rows[0]['reason'],'qualification_not_ready')
        self.assertNotIn(('settle',2),self.calls)
        block.assert_not_called()

    def test_failed_todo_write_never_releases_guard(self):
        self.pulls=[self.pulls[1]]
        attempts={2:dict(id=200,status='completed',conclusion='failure')}
        result=lambda *args:dict(payload=dict(result=dict(decision='fail')))
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block, \
             patch.object(m,'return_todo',side_effect=m.review.Refused('issue_contract_changed')):
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,
                             self.api.verify,result_verify=result,guarded=self.guard)
        self.assertEqual(rows[0]['reason'],'issue_contract_changed')
        self.assertNotIn(('settle',2),self.calls)
        block.assert_not_called()

    def test_unknown_failure_keeps_guard_and_never_retries_status(self):
        self.pulls=[self.pulls[1]]
        attempts={2:dict(id=200,status='completed',conclusion='success')}
        with patch.object(m,'withdraw_approval'),patch.object(m,'block_unadmitted') as block, \
             patch.object(m,'enqueue',side_effect=TimeoutError('unknown provider outcome')):
            rows=m.reconcile(self.api,'o/r',self.api.load,EVALUATOR,lambda *args:attempts,
                             self.api.verify,guarded=self.guard)
        self.assertEqual(rows[0]['reason'],'verification_or_provider_failed')
        self.assertNotIn(('settle',2),self.calls)
        block.assert_not_called()


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
