"""Worker guard integration against a synthetic GitHub provider only."""
import copy
import hashlib
import json
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('worker_guard',ROOT/'issue-work/scripts/worker_guard.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
fixture_spec=importlib.util.spec_from_file_location('guard_provider_fixture',ROOT/'issue-ci/tests/test_guard_github.py')
fixture=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture)
REPO='o/r';SOURCE='d'*40


def policy(source=SOURCE):
    paths={'worker':ROOT/'issue-work/scripts/worker_guard.py','provider':ROOT/'issue-ci/scripts/guard_github.py',
           'ledger':ROOT/'issue-ci/scripts/guard_ledger.py'}
    runtime={key:hashlib.sha256(path.read_bytes()).hexdigest() for key,path in paths.items()}
    return dict(version=1,enabled=True,genesis=fixture.GENESIS,ruleset_id=fixture.RULE,policy_source=source,runtime=runtime)


def make(issue=7,characters='abc'):
    values=iter(character*32 for character in characters)
    return w.new_intent(issue,policy(),lambda n:next(values))


class WorkerGuardTests(unittest.TestCase):
    def setUp(self):self.api=fixture.Provider();self.intent=make()

    def acquire(self):return w.acquire(self.api,REPO,policy(),self.intent,SOURCE)
    def release(self):return w.release(self.api,REPO,policy(),self.intent,SOURCE)

    def test_dispatch_sends_only_a_reconcile_wake_and_propagates_failure(self):
        client=type('Client',(),{'repo':REPO})()
        api=w.Api(client)
        with patch.object(w.subprocess,'check_output') as call:
            w.dispatch_wake(api,REPO)
            args,kwargs=call.call_args
            self.assertEqual(args[0],['gh','api','--method','POST','repos/o/r/dispatches','--input','-'])
            self.assertEqual(json.loads(kwargs['input']),{'event_type':'aw-verified-merge-reconcile'})
            call.side_effect=RuntimeError('unknown')
            with self.assertRaisesRegex(RuntimeError,'unknown'):w.dispatch_wake(api,REPO)
        with self.assertRaises(w.provider.Refused):w.dispatch_wake(api,'other/repo')

    def test_guard_stays_owned_until_explicit_release(self):
        self.assertEqual(self.acquire()['state'],'owned')
        self.assertEqual(self.acquire()['state'],'owned')
        self.assertTrue(w.require_owned(self.api,REPO,policy(),self.intent,SOURCE))
        current=w.provider.read_chain(self.api,REPO,fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(current['state']['locks']['7'],w.descriptor(self.intent))
        self.assertEqual(self.release()['state'],'released')
        self.assertEqual(self.release()['state'],'released')
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_not_owner'):
            w.require_owned(self.api,REPO,policy(),self.intent,SOURCE)

    def test_later_owner_is_never_released_or_reclaimed(self):
        self.acquire();self.release();other=make(characters='def')
        w.acquire(self.api,REPO,policy(),other,SOURCE)
        writes=len(self.api.writes)
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_not_owner'):
            self.release()
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_held_or_consumed'):
            self.acquire()
        self.assertEqual(len(self.api.writes),writes)

    def test_consumed_claim_does_not_reopen_after_release(self):
        self.acquire();self.release();writes=len(self.api.writes)
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_held_or_consumed'):
            self.acquire()
        self.assertEqual(len(self.api.writes),writes)

    def test_changed_guard_policy_holds_without_mutation(self):
        self.acquire();writes=len(self.api.writes)
        changed=policy();changed['ruleset_id']=10
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_intent'):
            w.release(self.api,REPO,changed,self.intent,SOURCE)
        self.assertEqual(len(self.api.writes),writes)

    def test_same_policy_can_follow_verified_default_branch_advance(self):
        self.acquire();current='e'*40;original=self.api.api
        def api(path,method='GET',data=None):
            if path==f'repos/{REPO}/compare/{SOURCE}...{current}':
                return dict(status='ahead',base_commit=dict(sha=SOURCE),merge_base_commit=dict(sha=SOURCE))
            return original(path,method,data)
        self.api.api=api
        self.assertEqual(w.release(self.api,REPO,policy(current),self.intent,current)['state'],'released')

    def test_rewritten_policy_history_holds(self):
        self.acquire();current='e'*40;original=self.api.api;writes=len(self.api.writes)
        def api(path,method='GET',data=None):
            if path==f'repos/{REPO}/compare/{SOURCE}...{current}':
                return dict(status='diverged',base_commit=dict(sha=SOURCE),merge_base_commit=dict(sha=SOURCE))
            return original(path,method,data)
        self.api.api=api
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_policy_history'):
            w.release(self.api,REPO,policy(current),self.intent,current)
        self.assertEqual(len(self.api.writes),writes)

    def test_intent_rejects_duplicate_ids_and_untrusted_config(self):
        bad=copy.deepcopy(self.intent);bad['release_op']=bad['claim_op']
        with self.assertRaises(w.provider.Refused):w.intent(bad,policy(),SOURCE)
        bad_policy=policy();bad_policy['enabled']=False
        with self.assertRaises(w.provider.Refused):w.acquire(self.api,REPO,bad_policy,self.intent,SOURCE)
        self.assertEqual(self.api.writes,[])

    def test_default_policy_is_pinned_and_runtime_is_verified(self):
        class SourceAPI:
            def __init__(self,document):
                self.raw=document;self.reads=0;self.changed=False;self.bad_sha=False;self.missing=False;self.truncated=False
            def api(self,path,method='GET',data=None):
                if path==f'repos/{REPO}':return dict(full_name=REPO,default_branch='main')
                if path==f'repos/{REPO}/commits/main':
                    self.reads+=1
                    return dict(sha='e'*40 if self.changed and self.reads>1 else SOURCE)
                if path=='graphql' and method=='POST':
                    sha=hashlib.sha1(b'blob '+str(len(self.raw)).encode()+b'\0'+self.raw).hexdigest()
                    blob=None if self.missing else dict(__typename='Blob',isBinary=False,
                        byteSize=len(self.raw)+(1 if self.truncated else 0),text=self.raw.decode(),
                        oid='0'*40 if self.bad_sha else sha)
                    return dict(data=dict(repository=dict(nameWithOwner=REPO,object=blob)))
                raise AssertionError(path)
        value=policy();del value['policy_source']
        raw=json.dumps(dict(issue_guard=value)).encode()
        self.assertEqual(w.read_policy(SourceAPI(raw),REPO),policy())
        self.assertIsNone(w.read_policy(SourceAPI(b'{"workflows": []}'),REPO))
        marker=dict(version=1,phase='drain',nonce='a'*32)
        self.assertEqual(w.read_policy(SourceAPI(json.dumps(dict(issue_guard_cutover=marker)).encode()),REPO),
                         dict(cutover=marker,policy_source=SOURCE))
        for bad in (None,dict(marker,version=True),dict(marker,phase='active'),dict(marker,nonce='x'*32),
                    dict(marker,extra=True)):
            with self.subTest(marker=bad),self.assertRaises(w.provider.Refused):
                w.read_policy(SourceAPI(json.dumps(dict(issue_guard_cutover=bad)).encode()),REPO)
        with self.assertRaises(w.provider.Refused):
            w.read_policy(SourceAPI(json.dumps(dict(issue_guard=value,issue_guard_cutover=marker)).encode()),REPO)
        absent=SourceAPI(raw);absent.missing=True
        self.assertIsNone(w.read_policy(absent,REPO))
        for change in ('head','blob','runtime','duplicate','truncated'):
            candidate=SourceAPI(raw)
            if change=='head':candidate.changed=True
            if change=='blob':candidate.bad_sha=True
            if change=='runtime':
                bad=copy.deepcopy(value);bad['runtime']['worker']='0'*64
                candidate.raw=json.dumps(dict(issue_guard=bad)).encode()
            if change=='duplicate':candidate.raw=b'{"issue_guard":null,"issue_guard":null}'
            if change=='truncated':candidate.truncated=True
            with self.subTest(change=change),self.assertRaises(w.provider.Refused):
                w.read_policy(candidate,REPO)

    def test_api_adapter_allows_only_scoped_guard_requests(self):
        class Client:
            repo=REPO
            def __init__(self):self.calls=[]
            def command(self,*args,payload=None):self.calls.append((args,payload));return {}
        client=Client();api=w.Api(client)
        api.api(f'repos/{REPO}/compare/{SOURCE}...main')
        api.api(f'repos/{REPO}/compare/{SOURCE}...{SOURCE}?per_page=100&page=1')
        api.api('graphql',method='POST',data=dict(query='query($id:ID!){node(id:$id){id}}',variables={}))
        for path,method,data in ((f'repos/{REPO}/../issues','GET',None),
                                 ('repos/other/repo/issues','GET',None),
                                 ('graphql','POST',dict(query='mutation{deleteRef(input:{}){clientMutationId}}'))):
            with self.subTest(path=path),self.assertRaises(w.provider.Refused):
                api.api(path,method,data)
        self.assertEqual(len(client.calls),3)

    def test_legacy_mode_never_ignores_an_existing_issue_guard(self):
        w.require_no_legacy_guard(self.api,REPO,9)
        with self.assertRaisesRegex(w.provider.Refused,'worker_guard_policy_missing'):
            w.require_no_legacy_guard(self.api,REPO,7)


if __name__=='__main__':
    unittest.main()
