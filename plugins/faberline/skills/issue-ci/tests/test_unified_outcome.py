"""One real writer identity and both reconciliations for every metadata wake."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_merge as fixture

m=fixture.m


class Identity(fixture.Provider):
    def __init__(self,event):
        super().__init__()
        self.source=b'name: trusted base writer\n'
        self.spec=self.document['config']['native_auto_merge']['producer']
        self.spec['definition_sha']=hashlib.sha1(b'blob '+str(len(self.source)).encode()+b'\0'+self.source).hexdigest()
        self.run=dict(id=300,workflow_id=self.spec['id'],path=self.spec['path'],event=event,
            head_repository=dict(full_name='o/r'),head_branch='main',head_sha=fixture.SOURCE,run_attempt=1)
        if event=='pull_request_target':
            self.run.update(head_repository=dict(full_name='fork/r'),head_branch='untrusted-pr',head_sha='f'*40)
        self.read_refs=[]

    def api(self,path,method='GET',data=None):
        if path=='repos/o/r/actions/runs/300':return copy.deepcopy(self.run)
        if path.startswith('repos/o/r/contents/'+self.spec['path']+'?ref='):
            ref=path.rsplit('=',1)[1];self.read_refs.append(ref)
            raw=self.source if ref==fixture.SOURCE else b'untrusted PR definition'
            sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            return dict(type='file',encoding='base64',sha=sha,content=base64.b64encode(raw).decode())
        return super().api(path,method,data)

    def pages(self,path,key=None):
        if path=='repos/o/r/actions/runs/300/attempts/1/jobs':
            return [dict(id=301,name=self.spec['job'])]
        return super().pages(path,key)


class UnifiedWriterTests(unittest.TestCase):
    def call_main(self,event,context=None):
        p=Identity(event)
        wake=dict(action='completed',repository=dict(full_name='o/r'),workflow_run=dict(
            id=100,workflow_id=77,path=fixture.qualification.WORKFLOW,event='pull_request'))
        if event=='issues':wake=dict(action='edited',repository=dict(full_name='o/r'))
        if event=='pull_request_target':wake=dict(action='synchronize',repository=dict(full_name='o/r'))
        if event=='repository_dispatch':wake=dict(action='aw-verified-merge-reconcile',repository=dict(full_name='o/r'))
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'event.json';file.write_text(json.dumps(wake))
            env=dict(GITHUB_ACTIONS='true',GITHUB_EVENT_NAME=event,GITHUB_REPOSITORY='o/r',
                GITHUB_RUN_ID='300',GITHUB_EVENT_PATH=str(file),AW_ACTION_REF=fixture.EVALUATOR,
                GITHUB_WORKFLOW_SHA=fixture.SOURCE,GITHUB_REF='refs/heads/main',
                GITHUB_WORKFLOW_REF='o/r/.github/workflows/issue-ci.yml@refs/heads/main')
            if context:env.update(context)
            with patch.dict(os.environ,env,clear=True),patch.object(m.review,'ci',p), \
                 patch.object(m.review.action,'trusted_policy',return_value=p.load()), \
                 patch.object(m,'writer_contract'), \
                 patch.object(m.review,'sibling',return_value=SimpleNamespace(Session=lambda *args:SimpleNamespace(attempts=object(),verify=object(),result=object()))), \
                 patch.object(m,'reconcile',return_value=[]) as reconcile,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),0)
                self.assertEqual(reconcile.call_count,1)
                self.assertIs(reconcile.call_args.args[-1],p.process_head)
        return p

    def test_entrypoint_uses_real_identity_check_for_each_event(self):
        for event in m.EVENTS:
            with self.subTest(event=event):
                p=self.call_main(event)
                self.assertTrue(p.read_refs)
                self.assertEqual(set(p.read_refs),{fixture.SOURCE})
                self.assertEqual(p.writes,[])

    def test_fork_pr_source_or_mutable_workflow_ref_cannot_authenticate_writer(self):
        for context in ({'GITHUB_WORKFLOW_SHA':'f'*40},
                        {'GITHUB_WORKFLOW_REF':'fork/r/.github/workflows/issue-ci.yml@refs/heads/untrusted-pr'},
                        {'GITHUB_REF':'refs/heads/untrusted-pr'}):
            with self.subTest(context=context),self.assertRaises(RuntimeError):
                self.call_main('pull_request_target',context)

    def test_replaced_ci_or_outcome_wake_always_runs_both_complete_reconciliations(self):
        # A single surviving run represents any replaced pending event. It must
        # process CI for all managed PRs before all Claude outcomes, every time.
        for survivor in ('ci','review','issue_edit','pr_head_change'):
            p=fixture.Provider();pulls=[dict(p.pull,number=n) for n in (2,1)]
            p.pages=lambda path,key=None:pulls if '/pulls?' in path else []
            events=[]
            def process(args,config,check_event):
                self.assertFalse(check_event);self.assertTrue(args.apply)
                events.append(('ci',args.pr))
            def enqueue(*args,**kwargs):events.append(('outcome',args[2]));return dict(state='queued')
            attempts={n:dict(id=200+n,status='completed',conclusion='success')for n in (1,2)}
            with patch.object(m,'withdraw_approval',side_effect=lambda *a:events.append(('withdraw',a[2]))), \
                 patch.object(m,'enqueue',side_effect=enqueue),contextlib.redirect_stdout(io.StringIO()):
                rows=m.reconcile(p,'o/r',p.load,fixture.EVALUATOR,lambda *a:attempts,p.verify,ci_process=process)
            self.assertEqual(events,[('withdraw',2),('withdraw',1),('ci',1),('ci',2),('outcome',1),('outcome',2)],survivor)
            self.assertEqual([r['state']for r in rows],['queued','queued'])

    def test_one_ci_handoff_error_holds_only_that_pr_after_withdrawal(self):
        p=fixture.Provider();pulls=[dict(p.pull,number=n)for n in (1,2)]
        p.pages=lambda path,key=None:pulls if '/pulls?' in path else []
        calls=[]
        def process(args,config,check_event):
            calls.append(('ci',args.pr))
            if args.pr==1:raise RuntimeError('Unknown label write result')
        def enqueue(*args,**kwargs):calls.append(('outcome',args[2]));return dict(state='queued')
        with patch.object(m,'withdraw_approval'),patch.object(m,'enqueue',side_effect=enqueue),contextlib.redirect_stdout(io.StringIO()):
            rows=m.reconcile(p,'o/r',p.load,fixture.EVALUATOR,
                lambda *a:{n:dict(id=200+n,status='completed',conclusion='success')for n in (1,2)},
                p.verify,ci_process=process)
        self.assertEqual([r['state']for r in rows],['held_after_pending','queued'])
        self.assertEqual(calls,[('ci',1),('ci',2),('outcome',2)])

    def test_nondefault_pr_base_is_filtered_before_joining_writer_concurrency(self):
        import yaml
        file=Path(__file__).parents[1]/'templates/claude-native-merge.yml.example'
        workflow=yaml.load(file.read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on']['pull_request_target']['branches'],['main'])
        self.assertNotIn('codex/dependency',workflow['on']['pull_request_target']['branches'])

    def test_unchanged_ci_policy_keeps_its_existing_journal_identity(self):
        p=fixture.Provider();original=copy.deepcopy(p.document['config'])
        p.document['config']['ci_handoff_policy']=original
        seen=[]
        m.reconcile_ci(p,'o/r',p.load(),[p.pull],lambda args,config,check_event:seen.append(config))
        self.assertEqual(seen,[original])
        self.assertEqual(m.review.r.digest(seen[0]),m.review.r.digest(original))
        p.document['config']['workflows'][0]['jobs'].append('unreviewed')
        with self.assertRaises(RuntimeError):m.handoff_policy(p.document['config'])


if __name__=='__main__':unittest.main()
