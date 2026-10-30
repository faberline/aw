"""A lost issue mutation response remains an explicit unknown."""
import importlib.util
import json
from pathlib import Path
import unittest
import test_review_lifecycle as lifecycle
import test_native_merge as native

spec=importlib.util.spec_from_file_location('todo_trace_test',Path(__file__).parents[1]/'scripts/todo_mutation_trace.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SHA='a'*64;HEAD='b'*40


class API:
    def __init__(self):self.calls=[];self.fail=False
    def api(self,path,method='GET',data=None):
        self.calls.append((path,method,data))
        if self.fail:raise TimeoutError('provider outcome unknown')
        return {'id':55} if path.endswith('/comments') and method=='POST' else {'ok':True}
    def pages(self,path):return ['read-only']


class TraceTests(unittest.TestCase):
    def test_success_records_matched_requests_without_exposing_body(self):
        client=API();lines=[];traced=m.TracedApi(client,'o/r',7,HEAD,SHA,lines.append)
        traced.api('repos/o/r/issues/7/comments',method='POST',data={'body':'private body'})
        traced.api('repos/o/r/issues/7/labels/doing',method='DELETE')
        records=[json.loads(line[len(m.MARKER):]) for line in lines]
        self.assertTrue(m.complete(records,SHA))
        self.assertNotIn('private body',''.join(lines))
        self.assertEqual([r['sequence'] for r in records],[1,1,2,2])
        self.assertEqual(traced.pages('x'),['read-only'])
        traced.seal()
        self.assertTrue(lines[-1].startswith(m.SEAL_MARKER))
        with self.assertRaisesRegex(RuntimeError,'sealed'):
            traced.api('repos/o/r/issues/7/labels',method='POST',data={'labels':['todo']})

    def test_unanswered_mutation_and_ambiguous_trace_fail_closed(self):
        client=API();lines=[];traced=m.TracedApi(client,'o/r',7,HEAD,SHA,lines.append)
        client.fail=True
        with self.assertRaises(TimeoutError):
            traced.api('repos/o/r/issues/7/labels',method='POST',data={'labels':['todo']})
        records=[json.loads(line[len(m.MARKER):]) for line in lines]
        self.assertFalse(m.complete(records,SHA))
        with self.assertRaisesRegex(RuntimeError,'record'):m.complete(records+records,SHA)
        changed=dict(records[0],intent_sha256='0'*64)
        with self.assertRaisesRegex(RuntimeError,'record'):m.complete([changed],SHA)

    def test_out_of_scope_mutation_and_failed_start_log_never_write(self):
        client=API();traced=m.TracedApi(client,'o/r',7,HEAD,SHA,lambda line:None)
        for path,method in [('repos/o/r/issues/8/labels','POST'),('repos/o/r/pulls/1/merge','PUT'),
                            ('repos/o/r/issues/7/labels/todo','DELETE')]:
            with self.subTest(path=path),self.assertRaisesRegex(RuntimeError,'scope'):
                traced.api(path,method=method)
        self.assertEqual(client.calls,[])
        traced=m.TracedApi(client,'o/r',7,HEAD,SHA,lambda line:(_ for _ in ()).throw(RuntimeError('log failed')))
        with self.assertRaisesRegex(RuntimeError,'log failed'):
            traced.api('repos/o/r/issues/7/labels',method='POST',data={'labels':['todo']})
        self.assertEqual(client.calls,[])

    def test_comment_patch_requires_an_id_observed_in_this_issue(self):
        client=API();traced=m.TracedApi(client,'o/r',7,HEAD,SHA,lambda line:None)
        with self.assertRaisesRegex(RuntimeError,'scope'):
            traced.api('repos/o/r/issues/comments/55',method='PATCH',data={'body':'x'})
        traced.api('repos/o/r/issues/7/comments',method='POST',data={'body':'x'})
        traced.api('repos/o/r/issues/comments/55',method='PATCH',data={'body':'y'})
        with self.assertRaisesRegex(RuntimeError,'scope'):
            traced.api('repos/o/r/issues/comments/56',method='PATCH',data={'body':'x'})

    def test_existing_journal_requires_the_exact_github_actions_actor(self):
        client=API();client.pages=lambda path:[dict(id=51,user=dict(login='github-actions[bot]',id=41898282,type='Bot')),
                                              dict(id=52,user=dict(login='github-actions[bot]',id=1,type='Bot'))]
        traced=m.TracedApi(client,'o/r',7,HEAD,SHA,lambda line:None)
        traced.pages('repos/o/r/issues/7/comments')
        traced.api('repos/o/r/issues/comments/51',method='PATCH',data={'body':'x'})
        with self.assertRaisesRegex(RuntimeError,'scope'):
            traced.api('repos/o/r/issues/comments/52',method='PATCH',data={'body':'x'})

    def test_real_todo_journal_runs_through_trace_and_retries_ready_checkpoint(self):
        provider=lifecycle.Lifecycle();provider.fail_applied=True;original_pages=provider.pages
        def pages(path,key=None):
            rows=original_pages(path,key)
            if path=='repos/o/r/issues/7/comments':
                for row in rows:row['user'].update(id=41898282,type='Bot')
            return rows
        provider.pages=pages;lines=[]
        traced=m.TracedApi(provider,'o/r',7,native.HEAD,SHA,lines.append)
        with self.assertRaisesRegex(RuntimeError,'interrupted checkpoint'):
            lifecycle.m.return_todo(traced,'o/r',1,native.HEAD,provider.load,native.EVALUATOR,200,provider.verify)
        self.assertEqual({x['name'] for x in provider.issue['labels']},{'todo','bug'})
        records=[json.loads(line[len(m.MARKER):]) for line in lines]
        self.assertFalse(m.complete(records,SHA))
        provider.fail_applied=False;retry_lines=[]
        retry=m.TracedApi(provider,'o/r',7,native.HEAD,SHA,retry_lines.append)
        result=lifecycle.m.return_todo(retry,'o/r',1,native.HEAD,provider.load,native.EVALUATOR,200,provider.verify)
        retry.seal()
        self.assertEqual(result['state'],'returned_todo')
        self.assertEqual([entry[0] for entry in provider.issue_writes],['add','remove'])
        retry_records=[json.loads(line[len(m.MARKER):]) for line in retry_lines if line.startswith(m.MARKER)]
        self.assertTrue(m.complete(retry_records,SHA))

    def test_provider_may_apply_todo_before_the_response_is_lost(self):
        provider=lifecycle.Lifecycle();original_api=provider.api;lines=[]
        def api(path,method='GET',data=None):
            result=original_api(path,method,data)
            if path=='repos/o/r/issues/7/labels' and method=='POST':
                raise TimeoutError('response lost after provider applied the label')
            return result
        provider.api=api
        traced=m.TracedApi(provider,'o/r',7,native.HEAD,SHA,lines.append)
        with self.assertRaises(TimeoutError):
            lifecycle.m.return_todo(traced,'o/r',1,native.HEAD,provider.load,native.EVALUATOR,200,provider.verify)
        self.assertIn('todo',{label['name'] for label in provider.issue['labels']})
        records=[json.loads(line[len(m.MARKER):]) for line in lines]
        self.assertFalse(m.complete(records,SHA))
        self.assertFalse(any(line.startswith(m.SEAL_MARKER) for line in lines))
