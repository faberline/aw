"""Authenticated negative decisions and lifecycle recovery; no live API calls."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_merge as native

_spec = importlib.util.spec_from_file_location('review_lifecycle', Path(__file__).parents[1] / 'scripts/native_merge.py')
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


class Lifecycle(native.Provider):
    def __init__(self):
        super().__init__()
        self.pull['body'] = 'Closes #7\n'
        self.issue = dict(number=7, state='open', title='Contract', body='Do exactly this.',
                          labels=[dict(name='done'), dict(name='bug')])
        self.receipt['payload']['result'] = dict(decision='fail')
        self.receipt['payload']['context']['issue_sha256'] = m.review.r.digest(
            {k:self.issue[k] for k in ('number','title','body')})
        self.issue_writes=[]
        self.after_add=None
        self.comments=[]
        self.after_ready=None
        self.fail_applied=False
        self.after_post=None

    def pages(self,path,key=None):
        if path=='repos/o/r/issues/7/comments':return copy.deepcopy(self.comments)
        return super().pages(path,key)

    def api(self,path,method='GET',data=None):
        if path == 'repos/o/r/issues/7' and method == 'GET': return copy.deepcopy(self.issue)
        if path=='repos/o/r/issues/7/comments' and method=='POST':
            value=dict(id=len(self.comments)+1,body=data['body'],user=dict(login='github-actions[bot]'))
            self.comments.append(value)
            if self.after_post:self.after_post()
            return copy.deepcopy(value)
        if path.startswith('repos/o/r/issues/comments/') and method=='PATCH':
            if ':applied -->' in data['body'] and self.fail_applied:raise RuntimeError('interrupted checkpoint')
            value=next(c for c in self.comments if str(c['id'])==path.rsplit('/',1)[1])
            value['body']=data['body']
            if ':ready -->' in data['body'] and self.after_ready:self.after_ready()
            return copy.deepcopy(value)
        if path == 'repos/o/r/issues/7/labels' and method == 'POST':
            self.issue_writes.append(('add',data['labels']))
            names={x['name'] for x in self.issue['labels']}
            self.issue['labels'] += [dict(name=n) for n in data['labels'] if n not in names]
            if self.after_add:self.after_add()
            return copy.deepcopy(self.issue['labels'])
        if path.startswith('repos/o/r/issues/7/labels/') and method == 'DELETE':
            from urllib.parse import unquote
            name=unquote(path.rsplit('/',1)[1]);self.issue_writes.append(('remove',name))
            self.issue['labels']=[x for x in self.issue['labels'] if x['name']!=name]
            return None
        return super().api(path,method,data)

    def reject(self):
        return m.return_todo(self,'o/r',1,native.HEAD,self.load,native.EVALUATOR,200,self.verify)


class ReviewLifecycleTests(unittest.TestCase):
    def test_fail_and_block_return_only_linked_open_issue_to_todo(self):
        for decision in ('fail','block'):
            p=Lifecycle();p.receipt['payload']['result']['decision']=decision
            self.assertEqual(p.reject()['state'],'returned_todo')
            self.assertEqual({x['name'] for x in p.issue['labels']},{'todo','bug'})
            self.assertEqual([w[0] for w in p.writes],['failure'])  # Revokes approval, never merges.
            p.issue_writes.clear();p.reject();self.assertEqual(p.issue_writes,[])

    def test_closed_blocked_changed_contract_or_head_never_writes_labels(self):
        mutations=(lambda p:p.issue.update(state='closed'),
                   lambda p:p.issue['labels'].append(dict(name='blocked')),
                   lambda p:p.issue.update(body='Changed contract'),
                   lambda p:p.pull['head'].update(sha='f'*40),
                   lambda p:p.pull.update(body='Closes #8\nCloses #7\n'),
                   lambda p:p.receipt['payload']['result'].update(decision='pass'))
        for mutate in mutations:
            p=Lifecycle();mutate(p)
            with self.subTest(mutate=mutate),self.assertRaises(RuntimeError):p.reject()
            self.assertEqual(p.issue_writes,[])

    def test_contract_drift_after_add_keeps_old_label_for_safe_recovery(self):
        p=Lifecycle();p.after_add=lambda:p.issue.update(body='New contract')
        with self.assertRaises(RuntimeError):p.reject()
        self.assertEqual(p.issue_writes,[('add',['todo'])])
        self.assertIn('done',{x['name'] for x in p.issue['labels']})

    def test_interrupted_mixed_labels_finish_without_readding_todo(self):
        p=Lifecycle();p.issue['labels'] += [dict(name='doing'),dict(name='todo')]
        p.reject()
        self.assertEqual(p.issue_writes,[('remove','doing'),('remove','done')])

    def test_failure_only_qualification_hold_still_handles_rejected_review(self):
        p=Lifecycle();p.document['config'].update(lifecycle_mode='failure-only',qualification_blockers=['Unresolved coverage'])
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else []
        index=lambda *a:{1:dict(id=200,status='completed',conclusion='success')}
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,index,p.verify,p.verify)
        self.assertEqual(rows[0]['state'],'returned_todo')
        self.assertEqual([w[0] for w in p.writes],['failure'])

    def test_failure_only_v2_negative_receipt_still_returns_todo(self):
        import test_issue_contract_v2 as v2
        p=Lifecycle();contract=v2.policy()['qualification']
        contract['contracts']['7']['title_sha256']=v2.digest(p.issue['title'])
        contract['contracts']['7']['body_sha256']=v2.digest(p.issue['body'])
        p.document['config'].update(lifecycle_mode='failure-only',qualification_blockers=[],qualification=contract)
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else []
        index=lambda *a:{1:dict(id=200,status='completed',conclusion='success')}
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,index,p.verify,p.verify)
        self.assertEqual(rows[0]['state'],'returned_todo')
        self.assertEqual({x['name'] for x in p.issue['labels']},{'todo','bug'})
        self.assertEqual([w[0] for w in p.writes],['failure'])

    def test_a_green_review_with_a_coverage_hold_cannot_queue_merge(self):
        p=Lifecycle();p.receipt['payload']['result']['decision']='pass'
        p.document['config'].update(lifecycle_mode='failure-only',qualification_blockers=['Unresolved coverage'])
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else []
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,
            lambda *a:{1:dict(id=200,status='completed',conclusion='success')},p.verify,p.verify)
        self.assertEqual(rows[0]['reason'],'qualification_hold')
        self.assertEqual(p.writes,[]);self.assertEqual(p.issue_writes,[])

    def test_pass_then_fail_disables_native_request_before_returning_issue_to_todo(self):
        p=Lifecycle();p.auto_complete=False;p.receipt['payload']['result']['decision']='pass'
        p.run();p.writes.clear();p.receipt['payload']['result']['decision']='fail'
        original=p.pages
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else [] if '/workflows/' in path else original(path,key)
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,
            lambda *a:{1:dict(id=200,status='completed',conclusion='failure')},p.verify,p.verify)
        self.assertEqual(rows[0]['state'],'returned_todo')
        self.assertEqual([w[0] for w in p.writes],['pending','disable','failure'])
        self.assertIsNone(p.native['autoMergeRequest'])
        self.assertEqual({l['name'] for l in p.issue['labels']},{'todo','bug'})

    def test_applied_rejection_never_undoes_later_worker_claim(self):
        p=Lifecycle();p.reject()
        p.issue['labels']=[dict(name='doing'),dict(name='bug')]
        p.issue_writes.clear()
        self.assertEqual(p.reject()['state'],'already_returned_todo')
        self.assertEqual(p.issue_writes,[])
        self.assertEqual({l['name']for l in p.issue['labels']},{'doing','bug'})

    def test_ready_checkpoint_recovers_after_application_without_undoing_claim(self):
        p=Lifecycle();p.fail_applied=True
        with self.assertRaisesRegex(RuntimeError,'interrupted checkpoint'):p.reject()
        self.assertIn(':ready -->',p.comments[0]['body'])
        p.issue['labels']=[dict(name='doing'),dict(name='bug')]
        p.issue_writes.clear();p.fail_applied=False
        p.reject()
        self.assertEqual(p.issue_writes,[])
        self.assertEqual({l['name']for l in p.issue['labels']},{'doing','bug'})
        self.assertIn(':applied -->',p.comments[0]['body'])

    def test_missing_add_confirmation_cannot_checkpoint_ready_or_remove_done(self):
        p=Lifecycle();original=p.api
        def api(path,method='GET',data=None):
            result=original(path,method,data)
            return None if path.endswith('/labels')and method=='POST'else result
        p.api=api
        with self.assertRaisesRegex(RuntimeError,'todo_write_unknown'):p.reject()
        self.assertIn(':pending -->',p.comments[0]['body'])
        self.assertIn('done',{l['name']for l in p.issue['labels']})

    def test_pending_checkpoint_finishes_once_without_duplicate_add(self):
        p=Lifecycle();original_body=p.issue['body']
        p.after_add=lambda:p.issue.update(body='New contract')
        with self.assertRaises(RuntimeError):p.reject()
        p.issue['body']=original_body;p.after_add=None;p.issue_writes.clear()
        p.reject()
        self.assertEqual(p.issue_writes,[('remove','done')])
        self.assertEqual(len(p.comments),1)
        self.assertIn(':applied -->',p.comments[0]['body'])

    def test_other_author_checkpoint_is_not_trusted(self):
        p=Lifecycle();p.reject()
        p.comments[0]['user']['login']='someone'
        p.issue['labels']=[dict(name='done'),dict(name='bug')]
        p.issue_writes.clear();p.reject()
        self.assertEqual(p.issue_writes,[('add',['todo']),('remove','done')])

    def test_duplicate_or_malformed_authenticated_checkpoint_refuses_labels(self):
        for mutate in [lambda p:p.comments.append(copy.deepcopy(p.comments[0])),
                       lambda p:p.comments[0].update(body=p.comments[0]['body']+'extra')]:
            p=Lifecycle();p.reject();mutate(p);p.issue_writes.clear()
            with self.assertRaises(RuntimeError):p.reject()
            self.assertEqual(p.issue_writes,[])

    def test_distinct_fresh_negative_receipt_can_return_a_new_retry(self):
        p=Lifecycle();p.reject()
        p.issue['labels']=[dict(name='doing'),dict(name='bug')]
        p.issue_writes.clear()
        p.receipt['payload']['result']['decision']='block'
        p.reject()
        self.assertEqual(p.issue_writes,[('add',['todo']),('remove','doing')])
        self.assertEqual(len(p.comments),2)


    def test_existing_todo_intent_starts_ready_before_a_worker_can_claim_it(self):
        p=Lifecycle();p.issue['labels']=[dict(name='todo'),dict(name='bug')]
        def stop_after_post():raise RuntimeError('interrupted after intent')
        p.after_post=stop_after_post
        with self.assertRaisesRegex(RuntimeError,'interrupted after intent'):p.reject()
        self.assertIn(':ready -->',p.comments[0]['body'])
        p.issue['labels']=[dict(name='doing'),dict(name='bug')]
        p.after_post=None;p.issue_writes.clear()
        p.reject()
        self.assertEqual(p.issue_writes,[])
        self.assertEqual({l['name']for l in p.issue['labels']},{'doing','bug'})
        self.assertIn(':applied -->',p.comments[0]['body'])

    def test_unmanaged_issue_cannot_enter_unsafe_pending_add_window(self):
        p=Lifecycle();p.issue['labels']=[dict(name='bug')]
        with self.assertRaisesRegex(RuntimeError,'issue_unmanaged'):p.reject()
        self.assertEqual(p.issue_writes,[]);self.assertEqual(p.comments,[])

    def test_lifecycle_drift_during_history_read_refuses_new_intent(self):
        p=Lifecycle()
        def history(path,key=None):
            p.issue['labels']=[dict(name='bug')]
            return []
        p.pages=history
        with self.assertRaisesRegex(RuntimeError,'issue_unmanaged'):p.reject()
        self.assertEqual(p.issue_writes,[]);self.assertEqual(p.comments,[])



if __name__=='__main__':unittest.main()
