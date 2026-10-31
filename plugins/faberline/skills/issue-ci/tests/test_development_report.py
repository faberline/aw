"""CI label reporter controls. No GitHub requests, model calls or product execution."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_lifecycle as legacy
import test_qualification as qualification

ci=legacy.ci
SOURCE,EVALUATOR,HEAD='a'*40,'b'*40,'c'*40
REPO='o/r';PATH='.github/workflows/issue-ci.yml'


class Provider:
    def __init__(self):
        self.raw=(Path(__file__).parents[1]/'templates/development-report.yml.example').read_text().replace('REVIEWED_AW_COMMIT',EVALUATOR)
        self.bad_blob=False;self.moved=False;self.reads=0;self.calls=[];self.writes=[];self.comments=[]
        self.pull=dict(number=8,state='open',title='Fix',body='Closes #7',head=dict(sha=HEAD,repo={'full_name':REPO}),base=dict(ref='main'))
        self.pulls=[self.pull]
        self.run=dict(id=100,run_attempt=1,workflow_id=55,path=PATH,event='workflow_run',head_sha=SOURCE,head_branch='main',head_repository={'full_name':REPO})
        self.issues={}
    def blob(self):return hashlib.sha1(b'blob '+str(len(self.raw.encode())).encode()+b'\0'+self.raw.encode()).hexdigest()
    def config(self):return dict(completion_owner='local',workflows=copy.deepcopy(legacy.GATES),lifecycle_mode='failure-only',qualification_blockers=['fixture content hold'],development_reporter=dict(path=PATH,id=55,definition_sha=self.blob(),job='report'))
    def api(self,path,method='GET',data=None):
        self.calls.append((path,method,data))
        if method!='GET':
            self.writes.append((path,method,data));result=dict(id=len(self.comments)+1,body=data.get('body','') if data else'',user={'login':'github-actions[bot]'})
            if method=='POST'and path.endswith('/comments'):self.comments.append(result)
            return result
        if path==f'repos/{REPO}':return dict(default_branch='main')
        if path==f'repos/{REPO}/actions/runs/100':return self.run
        if '/contents/'in path:return dict(type='file',encoding='base64',sha='0'*40 if self.bad_blob else self.blob(),content=base64.b64encode(self.raw.encode()).decode())
        if '/commits/main'in path:
            self.reads+=1
            return dict(sha='d'*40 if self.moved and self.reads>1 else SOURCE)
        if '/pulls/'in path:return copy.deepcopy(next(p for p in self.pulls if p['number']==int(path.rsplit('/',1)[1])))
        if '/issues/'in path:
            n=int(path.rsplit('/',1)[1])
            return copy.deepcopy(self.issues.get(n,dict(number=n,state='open',labels=[dict(name='doing')],
                repository_url=f'https://api.github.com/repos/{REPO}',url=f'https://api.github.com/repos/{REPO}/issues/{n}')))
        raise AssertionError((path,method,data))
    def pages(self,path,*args):return self.comments if path.endswith('/comments')else self.pulls


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.provider=Provider()
        self.env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=REPO,GITHUB_WORKFLOW_SHA=SOURCE,AW_ACTION_REF=EVALUATOR,GITHUB_EVENT_NAME='workflow_run',GITHUB_RUN_ATTEMPT='1')
    def invoke(self,process=None,config=None):
        p=self.provider
        with patch.dict(os.environ,self.env,clear=True),patch.object(ci,'api',side_effect=p.api)as api,patch.object(ci,'pages',side_effect=p.pages),patch.object(ci,'process_head',side_effect=process)as run,contextlib.redirect_stdout(io.StringIO())as output:
            result=ci.development_report(REPO,dict(source_commit=SOURCE,config=config or p.config()),EVALUATOR,100)
            self.assertIs(ci.api,api)
        return result,run,output.getvalue()
    def test_default_source_and_all_current_prs_are_read_without_completion_writes(self):
        p=self.provider;p.pulls.append(dict(p.pull,number=9,body='Closes #10',head=dict(sha='d'*40,repo={'full_name':REPO})))
        result,run,output=self.invoke()
        self.assertEqual(result,0);self.assertEqual(run.call_count,2);self.assertFalse(p.writes)
        self.assertTrue(all(c.kwargs['check_event']is False for c in run.call_args_list))
        self.assertIn('AW_DEVELOPMENT_FAILURE_REPORT_V1 ',output)
    def test_one_refused_pr_cannot_starve_the_remaining_queue(self):
        p=self.provider;p.pulls.append(dict(p.pull,number=9,body='Closes #10'))
        def process(args,*_,**kwargs):
            if args.pr==8:raise RuntimeError('old head')
        result,run,_=self.invoke(process)
        self.assertEqual(result,2);self.assertEqual(run.call_count,2)
    def test_foreign_or_wrong_target_prs_are_not_mutated(self):
        p=self.provider;p.pulls=[dict(p.pull,head=dict(sha=HEAD,repo={'full_name':'foreign/repo'})),dict(p.pull,base=dict(ref='release'))]
        result,run,_=self.invoke();self.assertEqual(result,0);run.assert_not_called();self.assertFalse(p.writes)
    def test_no_canonical_issue_gets_only_a_deduplicated_pr_comment(self):
        p=self.provider;p.pull['body']='No canonical issue'
        result,run,_=self.invoke();self.assertEqual(result,0);run.assert_not_called()
        self.assertEqual(len(p.writes),1);self.assertEqual(p.writes[0][:2],('repos/o/r/issues/8/comments','POST'))
    def test_invalid_pr_closed_and_foreign_links_only_diagnose_the_source_pr(self):
        for kind in ('pr','closed','foreign'):
            self.provider=Provider();p=self.provider
            target=p.api('repos/o/r/issues/7')
            if kind=='pr':target['pull_request']={}
            elif kind=='closed':target['state']='closed'
            else:target['repository_url']='https://api.github.com/repos/x/r'
            p.issues[7]=target
            with self.subTest(kind=kind):
                result,run,_=self.invoke();self.assertEqual(result,0);run.assert_not_called()
                self.assertEqual(len(p.writes),1)
                self.assertEqual(p.writes[0][:2],('repos/o/r/issues/8/comments','POST'))
                self.invoke();self.assertEqual(len(p.writes),1)
    def test_writer_revalidates_issue_type_even_if_inner_processor_would_write(self):
        def process(*args,**kwargs):
            target=self.provider.api('repos/o/r/issues/7');target['pull_request']={}
            self.provider.issues[7]=target
            ci.api('repos/o/r/issues/7/comments','POST',dict(body='must not reach the wrong PR'))
        result,_,_=self.invoke(process)
        self.assertEqual(result,2);self.assertEqual(self.provider.writes,[])
    def test_failure_comment_and_todo_are_allowed_with_current_source(self):
        def process(args,*_,**kwargs):
            kwargs['before_mutation']()
            ci.api('repos/o/r/issues/7/comments','POST',{'body':'Observed unit failure; job evidence'})
            ci.api('repos/o/r/issues/7/labels','POST',{'labels':['todo']})
            ci.api('repos/o/r/issues/7/labels/doing','DELETE')
        result,_,_=self.invoke(process);self.assertEqual(result,0);self.assertEqual(len(self.provider.writes),3)
    def test_merge_close_wrong_labels_status_and_wrong_issue_writes_never_reach_the_provider(self):
        requests=[('repos/o/r/pulls/8/merge','POST',{}),('repos/o/r/issues/7','PATCH',{'state':'closed'}),
                  ('repos/o/r/issues/7/labels','POST',{'labels':['label:done']}),('repos/o/r/statuses/'+HEAD,'POST',{'state':'success'}),
                  ('repos/o/r/issues/99/labels','POST',{'labels':['todo']})]
        for path,method,data in requests:
            self.provider.writes=[]
            def process(*args,**kwargs):ci.api(path,method,data)
            with self.subTest(path=path,data=data):
                result,_,_=self.invoke(process);self.assertEqual(result,2);self.assertFalse(self.provider.writes)
    def test_done_label_requires_fresh_complete_qualified_ci_but_never_closes(self):
        cfg=qualification.config();cfg.update(completion_owner='local',development_reporter=self.provider.config()['development_reporter'])
        scoped=qualification.scope(cfg)
        def process(*args,**kwargs):
            ci.api('repos/o/r/issues/7/labels','POST',{'labels':['done']})
            ci.api('repos/o/r/issues/7/labels/todo','DELETE')
        with patch.object(ci,'qualify_config',return_value=scoped),patch.object(ci,'snapshot',return_value=qualification.evidence(scoped)):
            result,_,_=self.invoke(process,config=cfg)
        self.assertEqual(result,0)
        self.assertEqual(self.provider.writes,[('repos/o/r/issues/7/labels','POST',{'labels':['done']}),('repos/o/r/issues/7/labels/todo','DELETE',None)])
    def test_done_label_refuses_missing_skipped_cancelled_failed_or_changed_ci(self):
        cfg=qualification.config();cfg.update(completion_owner='local',development_reporter=self.provider.config()['development_reporter'])
        scoped=qualification.scope(cfg)
        def process(*args,**kwargs):ci.api('repos/o/r/issues/7/labels','POST',{'labels':['done']})
        for conclusion in (None,'skipped','cancelled','failure','neutral'):
            self.provider.writes=[];snapshots=qualification.evidence(scoped)
            snapshots[qualification.WORKFLOW][1][0]['conclusion']=conclusion
            with self.subTest(conclusion=conclusion),patch.object(ci,'qualify_config',return_value=scoped),patch.object(ci,'snapshot',return_value=snapshots):
                result,_,_=self.invoke(process,config=cfg)
                self.assertEqual(result,2);self.assertFalse(self.provider.writes)
        def moved_process(*args,**kwargs):
            self.provider.pull['head']['sha']='e'*40
            ci.api('repos/o/r/issues/7/labels','POST',{'labels':['done']})
        with patch.object(ci,'qualify_config',side_effect=AssertionError('stale head must refuse before qualification')):
            result,_,_=self.invoke(moved_process,config=cfg)
        self.assertEqual(result,2);self.assertFalse(self.provider.writes)
    def test_default_movement_holds_before_any_failure_write(self):
        self.provider.moved=True
        result,_,_=self.invoke(lambda *a,**k:ci.api('repos/o/r/issues/7/labels','POST',{'labels':['todo']}))
        self.assertEqual(result,2);self.assertFalse(self.provider.writes)
    def test_foreign_or_stale_environment_and_run_identity_refuse(self):
        for field,value in [('GITHUB_ACTIONS','false'),('GITHUB_REPOSITORY','foreign/repo'),('GITHUB_WORKFLOW_SHA','d'*40),('GITHUB_EVENT_NAME','pull_request')]:
            old=self.env.copy();self.env[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):self.invoke()
            self.env=old
        for field,value in [('workflow_id',1),('head_sha','d'*40),('path','.github/workflows/other.yml'),('run_attempt',2),('head_branch','pr-branch')]:
            old=self.provider.run.copy();self.provider.run[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):self.invoke()
            self.provider.run=old
        self.assertFalse(self.provider.writes)
    def test_source_blob_extra_steps_and_extra_permissions_refuse(self):
        self.provider.bad_blob=True
        with self.assertRaises(RuntimeError):self.invoke()
        self.provider.bad_blob=False;original=self.provider.raw
        for raw in [original.replace('contents: read','contents: write'),original.replace('    steps:','    env: {GH_TOKEN: untrusted}\n    steps:'),original+'      - run: echo untrusted\n']:
            self.provider.raw=raw
            with self.subTest(raw=raw),self.assertRaises(RuntimeError):self.invoke()
        self.assertFalse(self.provider.writes)
    def test_local_owner_and_actual_producer_spec_are_required(self):
        for mutate in [lambda c:c.pop('completion_owner'),lambda c:c.pop('development_reporter'),lambda c:c.update(native_auto_merge={})]:
            config=self.provider.config();mutate(config)
            with self.subTest(config=config),self.assertRaises(RuntimeError):self.invoke(config=config)
        self.assertFalse(self.provider.writes)

if __name__=='__main__':unittest.main()
