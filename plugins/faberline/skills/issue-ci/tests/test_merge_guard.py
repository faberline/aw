"""No real refs, writes, or workflow dispatches in these controls."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

g=load('merge_guard_draft',HERE.parent/'scripts/merge_guard.py')
fixture=load('merge_guard_fixture',HERE/'test_guard_github.py')
workers=load('merge_guard_policy_fixture',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')


class MergeGuardTests(unittest.TestCase):
    def setUp(self):
        self.api=fixture.Provider();self.policy=workers.policy();self.log=[]
        values=iter(['a'*32,'b'*32,'c'*32])
        self.record=g.build('o/r',9,'d'*40,7,self.policy,123,2,lambda n:next(values))

    def acquire(self):return g.acquire(self.api,self.policy,self.record,self.log.append)

    def test_intent_precedes_mutation_and_holds_exact_merge_head(self):
        def emit(line):
            self.assertEqual(self.api.writes,[]);self.log.append(line)
        result=g.acquire(self.api,self.policy,self.record,emit)
        self.assertEqual(result['state'],'owned')
        self.assertTrue(self.log[0].startswith(g.MARKER))
        held=g.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(held['state']['locks']['7'],dict(owner=self.record['payload']['owner'],kind='merge',head='d'*40))

    def test_failed_emission_never_claims(self):
        def fail(line):raise RuntimeError('no durable output')
        with self.assertRaisesRegex(RuntimeError,'no durable output'):
            g.acquire(self.api,self.policy,self.record,fail)
        self.assertEqual(self.api.writes,[])

    def test_unknown_claim_observed_only_for_same_intent(self):
        self.api.fail_patch='after'
        with self.assertRaisesRegex(RuntimeError,'guard_update_unknown'):self.acquire()
        self.api.fail_patch=None
        self.assertEqual(self.acquire()['state'],'owned')
        self.assertEqual(self.api.writes.count('patch'),1)
        other=copy.deepcopy(self.record);other['payload']['claim_op']='e'*32
        other['sha256']=g.hashlib.sha256(g.canonical(other['payload']).encode()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'merge_guard_claim_unknown'):
            g.acquire(self.api,self.policy,other,self.log.append)

    def test_discovered_owner_must_match_one_authenticated_intent(self):
        log=g.MARKER+g.canonical(self.record)+'\n';owner=self.record['payload']['owner']
        def lookup(value=owner):
            return g.identify_native_owner(None,'o/r',{},'e'*40,123,2,7,'d'*40,value,None)
        with patch.object(g,'authenticate_native_log',return_value=(self.policy,log)) as source:
            self.assertEqual(lookup(),self.record)
            with self.assertRaisesRegex(RuntimeError,'merge_guard_record_missing_or_duplicate'):
                lookup('0'*56)
            source.return_value=(self.policy,log+log)
            with self.assertRaisesRegex(RuntimeError,'merge_guard_record_missing_or_duplicate'):lookup()
            source.side_effect=RuntimeError('source unknown')
            with self.assertRaisesRegex(RuntimeError,'source unknown'):lookup()

    def test_worker_owner_and_tampered_intent_hold(self):
        g.provider.update(self.api,'o/r',fixture.GENESIS,fixture.RULE,7,'claim',fixture.owner('f'),'e'*32)
        writes=len(self.api.writes)
        with self.assertRaisesRegex(RuntimeError,'merge_guard_held_or_consumed'):self.acquire()
        self.assertEqual(len(self.api.writes),writes)
        bad=copy.deepcopy(self.record);bad['payload']['head']='f'*40
        with self.assertRaisesRegex(RuntimeError,'merge_guard_intent'):
            g.acquire(self.api,self.policy,bad,self.log.append)
        self.assertEqual(len(self.api.writes),writes)

    def test_authentication_uses_exact_completed_attempt_and_trusted_source(self):
        source=self.policy['policy_source'];config=dict(self.policy);del config['policy_source']
        document=dict(source_commit=source,config=dict(issue_guard=config))
        spec=dict(id=1,path='.github/workflows/issue-ci.yml',definition_sha='f'*40,job='writer')
        class Native:
            def policy(self,doc,require_qualification=True):return dict(producer=spec)
            def writer_contract(self,api,repo,doc,evaluator):pass
        class Source:
            def __init__(self):
                self.row=dict(id=123,run_attempt=2,workflow_id=1,path=spec['path'],event='repository_dispatch',
                    status='completed',conclusion='failure',head_repository=dict(full_name='o/r'),
                    head_branch='main',head_sha=source)
                self.job_conclusion='success'
            def api(self,path):
                if path=='repos/o/r':return dict(full_name='o/r',default_branch='main')
                if path=='repos/o/r/actions/runs/123/attempts/2':return copy.deepcopy(self.row)
                if path==f'repos/o/r/compare/{source}...main':return dict(status='ahead',base_commit=dict(sha=source))
                raise AssertionError(path)
            def pages(self,path,key):
                assert path=='repos/o/r/actions/runs/123/attempts/2/jobs' and key=='jobs'
                return [dict(id=7,name='writer',status='completed',conclusion=self.job_conclusion)]
        api=Source();line=g.MARKER+g.canonical(self.record)+'\n'
        def check(log=line):
            return g.authenticate_native_intent(api,'o/r',document,'e'*40,123,2,9,'d'*40,lambda r,j:log)
        with patch.object(g.review,'sibling',return_value=Native()),patch.object(g.review,'blob'), \
             patch.object(g,'historical_runtime',return_value=True):
            self.assertEqual(check(),self.record)
            with self.assertRaisesRegex(RuntimeError,'merge_guard_source_not_success'):
                g.authenticate_native_log(api,'o/r',document,'e'*40,123,2,lambda r,j:line,require_success=True)
            api.row['conclusion']='success'
            self.assertEqual(g.authenticate_native_log(api,'o/r',document,'e'*40,123,2,lambda r,j:line,
                                                       require_success=True)[1],line)
            api.job_conclusion='failure'
            with self.assertRaisesRegex(RuntimeError,'merge_guard_source_not_success'):
                g.authenticate_native_log(api,'o/r',document,'e'*40,123,2,lambda r,j:line,require_success=True)
            with self.assertRaisesRegex(RuntimeError,'merge_guard_record_missing_or_duplicate'):check(line+line)
            api.row['event']='pull_request_target'
            with self.assertRaisesRegex(RuntimeError,'merge_guard_source'):check()
            api.row['event']='repository_dispatch';api.row['status']='in_progress'
            with self.assertRaisesRegex(RuntimeError,'merge_guard_source'):check()
            api.row['status']='completed';api.row['head_sha']='0'*40
            with self.assertRaisesRegex(RuntimeError,'merge_guard_source'):check()

    def test_historical_runtime_is_read_from_exact_aw_commit(self):
        paths={'worker':'plugins/faberline/skills/issue-work/scripts/worker_guard.py',
               'provider':'plugins/faberline/skills/issue-ci/scripts/guard_github.py',
               'ledger':'plugins/faberline/skills/issue-ci/scripts/guard_ledger.py'}
        root=HERE.parents[4]
        files={path:(root/path).read_bytes() for path in paths.values()}
        class Source:
            def __init__(self):self.diverged=False;self.bad=False
            def api(self,path,method='GET',data=None):
                if path=='repos/faberline/aw':return dict(full_name='faberline/aw',default_branch='main')
                if path=='repos/faberline/aw/compare/'+('e'*40)+'...main':
                    return dict(status='diverged' if self.diverged else 'ahead',base_commit=dict(sha='e'*40))
                if path=='graphql':
                    expr=data['variables']['expression'];assert expr.startswith('e'*40+':')
                    raw=files[expr[41:]]
                    oid=g.hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
                    return dict(data=dict(repository=dict(nameWithOwner='faberline/aw',object=dict(
                        __typename='Blob',isBinary=False,byteSize=len(raw),text=raw.decode(),oid='0'*40 if self.bad else oid))))
                raise AssertionError(path)
        source=Source()
        self.assertTrue(g.historical_runtime(source,'e'*40,self.policy))
        source.bad=True
        with self.assertRaisesRegex(RuntimeError,'merge_guard_runtime_source'):
            g.historical_runtime(source,'e'*40,self.policy)
        source.bad=False;source.diverged=True
        with self.assertRaisesRegex(RuntimeError,'merge_guard_runtime_source'):
            g.historical_runtime(source,'e'*40,self.policy)


if __name__=='__main__':unittest.main()
