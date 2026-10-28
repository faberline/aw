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

    def api(self,path,method='GET',data=None):
        if path == 'repos/o/r/issues/7' and method == 'GET': return copy.deepcopy(self.issue)
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
        return m.return_doing(self,'o/r',1,native.HEAD,self.load,native.EVALUATOR,200,self.verify)


class ReviewLifecycleTests(unittest.TestCase):
    def test_fail_and_block_return_only_linked_open_issue_to_doing(self):
        for decision in ('fail','block'):
            p=Lifecycle();p.receipt['payload']['result']['decision']=decision
            self.assertEqual(p.reject()['state'],'returned_doing')
            self.assertEqual({x['name'] for x in p.issue['labels']},{'doing','bug'})
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
        self.assertEqual(p.issue_writes,[('add',['doing'])])
        self.assertIn('done',{x['name'] for x in p.issue['labels']})

    def test_interrupted_mixed_labels_finish_without_readding_doing(self):
        p=Lifecycle();p.issue['labels'] += [dict(name='doing'),dict(name='todo')]
        p.reject()
        self.assertEqual(p.issue_writes,[('remove','todo'),('remove','done')])

    def test_failure_only_qualification_hold_still_handles_rejected_review(self):
        p=Lifecycle();p.document['config'].update(lifecycle_mode='failure-only',qualification_blockers=['Unresolved coverage'])
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else []
        index=lambda *a:{1:dict(id=200,status='completed',conclusion='success')}
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,index,p.verify,p.verify)
        self.assertEqual(rows[0]['state'],'returned_doing')
        self.assertEqual([w[0] for w in p.writes],['failure'])

    def test_a_green_review_with_a_coverage_hold_cannot_queue_merge(self):
        p=Lifecycle();p.receipt['payload']['result']['decision']='pass'
        p.document['config'].update(lifecycle_mode='failure-only',qualification_blockers=['Unresolved coverage'])
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else []
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,
            lambda *a:{1:dict(id=200,status='completed',conclusion='success')},p.verify,p.verify)
        self.assertEqual(rows[0]['reason'],'qualification_hold')
        self.assertEqual(p.writes,[]);self.assertEqual(p.issue_writes,[])

    def test_pass_then_fail_disables_native_request_before_returning_issue_to_doing(self):
        p=Lifecycle();p.auto_complete=False;p.receipt['payload']['result']['decision']='pass'
        p.run();p.writes.clear();p.receipt['payload']['result']['decision']='fail'
        original=p.pages
        p.pages=lambda path,key=None:[dict(p.pull)] if '/pulls?' in path else [] if '/workflows/' in path else original(path,key)
        rows=m.reconcile(p,'o/r',p.load,native.EVALUATOR,
            lambda *a:{1:dict(id=200,status='completed',conclusion='failure')},p.verify,p.verify)
        self.assertEqual(rows[0]['state'],'returned_doing')
        self.assertEqual([w[0] for w in p.writes],['pending','disable','failure'])
        self.assertIsNone(p.native['autoMergeRequest'])
        self.assertEqual({l['name'] for l in p.issue['labels']},{'doing','bug'})


if __name__=='__main__':unittest.main()
