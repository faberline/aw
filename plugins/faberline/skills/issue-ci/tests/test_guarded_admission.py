"""Draft writer admission controls; no real GitHub writes are made."""
import copy
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

m=load('guarded_admission_draft',HERE.parent/'scripts/guarded_admission.py')
fixture=load('guarded_admission_provider',HERE/'test_guard_github.py')
workers=load('guarded_admission_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
SOURCE='d'*40;HEAD='a'*40;EVALUATOR='e'*40


def status(number,state,context='claude/verified-merge'):
    return dict(id=number,context=context,state=state,
                creator=dict(id=41898282,type='Bot',login='github-actions[bot]'))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.api=fixture.Provider();self.policy=workers.policy(SOURCE);self.document=dict(source_commit=SOURCE)
        self.pull=dict(number=9,state='open',draft=False,title='PR',body='Closes #7\n',
                       head=dict(sha=HEAD,repo=dict(full_name='o/r')),
                       base=dict(sha='b'*40,ref='main',repo=dict(full_name='o/r')))
        old=self.api.api
        def api(path,method='GET',data=None):
            if path=='repos/o/r/pulls/9' and method=='GET':return copy.deepcopy(self.pull)
            return old(path,method,data)
        self.api.api=api;self.statuses=[];self.api.pages=lambda path:copy.deepcopy(self.statuses)
        self.env=dict(GITHUB_ACTIONS='true',GITHUB_EVENT_NAME='repository_dispatch',GITHUB_REPOSITORY='o/r',
                      GITHUB_WORKFLOW_SHA=SOURCE,AW_ACTION_REF=EVALUATOR,GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='2')

    def begin(self,event='repository_dispatch'):
        values=iter(['a'*32,'b'*32,'c'*32]);log=[];env=dict(self.env,GITHUB_EVENT_NAME=event)
        with patch.dict(os.environ,env,clear=True),patch.object(m.worker,'read_policy',return_value=self.policy), \
             patch.object(m.close.worker,'read_policy',return_value=self.policy):
            result=m.begin(self.api,'o/r',self.document,EVALUATOR,9,HEAD,log.append,lambda n:next(values))
        self.assertTrue(log[0].startswith(m.merge_guard.MARKER))
        return result['intent']

    def settle(self,intent,pulls,rules=None):
        with patch.object(m.close.worker,'read_policy',return_value=self.policy), \
             patch.object(m.native,'provider_pull',side_effect=pulls), \
             patch.object(m,'blocking_rule',side_effect=rules if rules is not None else [dict(rule=1),dict(rule=1)]):
            return m.settle(self.api,'o/r',self.policy,intent)

    def provider(self,request=None,state='OPEN'):
        return dict(headRefOid=HEAD,state=state,autoMergeRequest=request)

    def test_open_blocked_pr_releases_only_after_rechecks(self):
        intent=self.begin();self.statuses=[status(1,'pending')]
        self.assertEqual(self.settle(intent,[self.provider(),self.provider()]),dict(state='released',issue=7))
        state=m.merge_guard.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(state['state']['locks'],{})

    def test_queued_or_merged_pr_retains_guard(self):
        for pull in (self.provider(request={'mergeMethod':'SQUASH'}),self.provider(state='MERGED')):
            self.setUp();intent=self.begin()
            self.assertEqual(self.settle(intent,[pull])['state'],'retained')
            self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_passing_status_or_changed_request_never_releases(self):
        intent=self.begin();self.statuses=[status(1,'success','Claude/Verified-Merge')]
        with self.assertRaisesRegex(RuntimeError,'admission_status_not_blocked'):
            self.settle(intent,[self.provider()])
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))
        self.statuses=[status(2,'pending')]
        with self.assertRaisesRegex(RuntimeError,'admission_target_changed'):
            self.settle(intent,[self.provider(),self.provider(request={'mergeMethod':'SQUASH'})])
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_newest_success_keeps_guard_even_when_its_id_is_smaller(self):
        intent=self.begin()
        self.statuses=[status(1,'success'),status(99,'pending')]
        with self.assertRaisesRegex(RuntimeError,'admission_status_not_blocked'):
            self.settle(intent,[self.provider()])
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))
        owned=dict(mergeMethod='SQUASH',enabledBy=dict(login='github-actions',__typename='Bot'))
        with patch.object(m.close.worker,'read_policy',return_value=self.policy), \
             patch.object(m.native,'provider_pull',return_value=self.provider(request=owned)), \
             patch.object(m.native,'withdraw_approval') as withdraw:
            result=m.cancel_blocked(self.api,'o/r',self.policy,intent)
        self.assertEqual(result['state'],'retained');withdraw.assert_not_called()

    def test_foreign_pending_cannot_hide_a_passing_actions_status(self):
        intent=self.begin()
        foreign=status(2,'pending')
        foreign['creator']=dict(id=42,type='User',login='other')
        self.statuses=[foreign,status(1,'success')]
        with self.assertRaisesRegex(RuntimeError,'admission_status_origin_unknown'):
            self.settle(intent,[self.provider()])
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_newer_trusted_pending_is_not_blocked_by_old_foreign_status(self):
        intent=self.begin();old=status(1,'success')
        old['creator']=dict(id=42,type='User',login='other')
        self.statuses=[status(2,'pending'),old]
        self.assertEqual(self.settle(intent,[self.provider(),self.provider()])['state'],'released')

    def test_missing_or_spoofed_creator_never_releases_or_cancels(self):
        creators=(None,{},dict(id=42,type='Bot',login='github-actions[bot]'),
                  dict(id=41898282,type='User',login='github-actions[bot]'),
                  dict(id=41898282,type='Bot',login='another-bot'))
        for creator in creators:
            with self.subTest(creator=creator):
                self.setUp();intent=self.begin();item=status(1,'pending');item['creator']=creator
                self.statuses=[item]
                with self.assertRaisesRegex(RuntimeError,'admission_status_origin_unknown'):
                    self.settle(intent,[self.provider()])
                owned=dict(mergeMethod='SQUASH',enabledBy=dict(login='github-actions',__typename='Bot'))
                with patch.object(m.close.worker,'read_policy',return_value=self.policy), \
                     patch.object(m.native,'provider_pull',return_value=self.provider(request=owned)), \
                     patch.object(m.native,'withdraw_approval') as withdraw:
                    with self.assertRaisesRegex(RuntimeError,'admission_status_origin_unknown'):
                        m.cancel_blocked(self.api,'o/r',self.policy,intent)
                withdraw.assert_not_called()
                self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_changed_status_between_reads_holds(self):
        intent=self.begin();calls=[]
        def pages(path):
            calls.append(path)
            return [status(1 if len(calls)==1 else 2,'pending')]
        self.api.pages=pages
        with self.assertRaisesRegex(RuntimeError,'admission_status_changed'):
            self.settle(intent,[self.provider(),self.provider()])
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_missing_or_changed_branch_protection_keeps_guard(self):
        intent=self.begin();self.statuses=[status(1,'pending')]
        with self.assertRaisesRegex(RuntimeError,'required_merge_rule_missing'):
            self.settle(intent,[self.provider()],RuntimeError('required_merge_rule_missing'))
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))
        with self.assertRaisesRegex(RuntimeError,'admission_rule_changed'):
            self.settle(intent,[self.provider(),self.provider()],[dict(rule=1),dict(rule=2)])
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_blocking_rule_uses_current_policy_and_github_actions_app(self):
        doc=dict(source_commit=SOURCE,config={});native_policy=dict(status_app_id=15368)
        with patch.object(m.native.review.action,'trusted_policy',return_value=doc), \
             patch.object(m.native,'policy',return_value=native_policy) as validate, \
             patch.object(m.native,'base_rules',return_value=dict(rule=1)) as rules:
            self.assertEqual(m.blocking_rule(self.api,'o/r',self.policy,self.pull),dict(rule=1))
            validate.assert_called_once_with(doc,require_qualification=False)
            rules.assert_called_once_with(self.api,'o/r',dict(base_ref='main',base='b'*40),native_policy)
            native_policy['status_app_id']=7
            with self.assertRaisesRegex(RuntimeError,'admission_status_app_unknown'):
                m.blocking_rule(self.api,'o/r',self.policy,self.pull)
            doc['source_commit']='f'*40
            with self.assertRaisesRegex(RuntimeError,'admission_policy_changed'):
                m.blocking_rule(self.api,'o/r',self.policy,self.pull)

    def test_pr_target_event_never_acquires(self):
        with patch.dict(os.environ,dict(self.env,GITHUB_EVENT_NAME='pull_request_target'),clear=True):
            with self.assertRaisesRegex(RuntimeError,'admission_source'):
                m.begin(self.api,'o/r',self.document,EVALUATOR,9,HEAD,lambda line:None)
        self.assertEqual(self.api.writes,[])

    def test_active_worker_claim_is_never_replaced(self):
        worker=dict(owner='f'*32,kind='worker',head=None)
        m.merge_guard.provider.update(self.api,'o/r',fixture.GENESIS,fixture.RULE,7,'claim',worker,'e'*32)
        before=list(self.api.writes)
        with self.assertRaisesRegex(RuntimeError,'merge_guard_held_or_consumed'):self.begin()
        self.assertEqual(self.api.writes,before)
        state=m.merge_guard.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(state['state']['locks']['7'],worker)

    def test_unknown_provider_state_preserves_claim(self):
        intent=self.begin()
        with self.assertRaisesRegex(RuntimeError,'provider unavailable'):
            self.settle(intent,RuntimeError('provider unavailable'))
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_blocked_owned_request_is_cancelled_then_rechecked_before_release(self):
        intent=self.begin();self.statuses=[status(1,'pending')]
        owned=dict(mergeMethod='SQUASH',enabledBy=dict(login='github-actions',__typename='Bot'))
        with patch.object(m.close.worker,'read_policy',return_value=self.policy), \
             patch.object(m.native,'provider_pull',side_effect=[self.provider(request=owned),self.provider(),self.provider()]), \
             patch.object(m,'blocking_rule',return_value=dict(rule=1)), \
             patch.object(m.native,'withdraw_approval') as withdraw:
            result=m.cancel_blocked(self.api,'o/r',self.policy,intent)
        self.assertEqual(result['state'],'released');withdraw.assert_called_once_with(self.api,'o/r',9,HEAD)

    def test_foreign_request_or_passing_status_is_never_cancelled(self):
        for request,state in ((dict(enabledBy=dict(login='human',__typename='User')),'pending'),
                              (dict(enabledBy=dict(login='github-actions',__typename='Bot')),'success')):
            self.setUp();intent=self.begin();self.statuses=[status(1,state)]
            with patch.object(m.close.worker,'read_policy',return_value=self.policy), \
                 patch.object(m.native,'provider_pull',return_value=self.provider(request=request)), \
                 patch.object(m.native,'withdraw_approval') as withdraw:
                result=m.cancel_blocked(self.api,'o/r',self.policy,intent)
            self.assertEqual(result['state'],'retained');withdraw.assert_not_called()
            self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))

    def test_unknown_cancellation_never_retries_or_releases(self):
        intent=self.begin();self.statuses=[status(1,'pending')]
        request=dict(enabledBy=dict(login='github-actions',__typename='Bot'))
        with patch.object(m.close.worker,'read_policy',return_value=self.policy), \
             patch.object(m.native,'provider_pull',return_value=self.provider(request=request)), \
             patch.object(m.native,'withdraw_approval',side_effect=TimeoutError('unknown')) as withdraw:
            with self.assertRaisesRegex(TimeoutError,'unknown'):m.cancel_blocked(self.api,'o/r',self.policy,intent)
        withdraw.assert_called_once()
        self.assertTrue(m.close.guard_identity(self.api,'o/r',self.policy,intent))


if __name__=='__main__':unittest.main()
