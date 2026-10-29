"""Only an authenticated negative target review can return an issue to todo."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_native_merge as native
import test_policy_adoption as adoption
import test_review_lifecycle as lifecycle

spec=importlib.util.spec_from_file_location('adoption_outcome_test',Path(__file__).parents[1]/'scripts/policy_adoption_review_outcome.py')
o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o)
HEAD,BASE=native.HEAD,native.BASE


def fixture(decision='fail'):
    p=lifecycle.Lifecycle();g,_,_,workflow,_=adoption.TargetReviewProvenanceTests().fixture()
    workflow=workflow.replace("target-pr: '8'","target-pr: '1'")
    source='1'*40;branch='codex/target-review';path='.github/workflows/review.yml';run_id=333
    run=dict(id=run_id,event='push',status='in_progress',head_repository={'full_name':'o/r'},
             head_sha=source,head_branch=branch,run_attempt=1,path=path)
    old_api=p.api;old_pages=p.pages
    def api(name,method='GET',data=None):
        if name=='repos/o/r/actions/runs/333':return copy.deepcopy(run)
        if name=='repos/o/r/commits/main':return {'sha':BASE}
        return old_api(name,method,data)
    def pages(name,key=None):
        if name.endswith('/actions/runs/333/attempts/1/jobs'):
            return [dict(name='review',id=223,status='completed',conclusion='success'),dict(name='outcome',id=224,status='in_progress')]
        return old_pages(name,key)
    p.api=api;p.pages=pages
    receipt=dict(version=1,repo='o/r',pr=1,issue=7,head=HEAD,base=BASE,
        pr_sha256=o.a.r.digest([p.pull['title'],p.pull['body']]),
        issue_sha256=o.a.r.digest({k:p.issue[k] for k in ('number','title','body')}),
        files_sha256='3'*64,ci_jobs_sha256='4'*64,reviewed_files=[],requirements=[],
        decision=decision,findings=['Missing acceptance evidence'] if decision=='fail' else [],
        uncertainties=['Review could not complete'] if decision=='block' else [])
    env=dict(GITHUB_REPOSITORY='o/r',GITHUB_RUN_ID='333',GITHUB_EVENT_NAME='push',GITHUB_SHA=source,
             GITHUB_WORKFLOW_SHA=source,GITHUB_REF='refs/heads/'+branch,GITHUB_WORKFLOW_REF='o/r/'+path+'@refs/heads/'+branch)
    def blob(api_,repo,path_,ref,expected=None):return workflow if repo=='o/r' else 'trusted target reviewer'
    return p,run,receipt,env,blob


class OutcomeTests(unittest.TestCase):
    def test_unresolved_planning_question_returns_issue_to_todo(self):
        p,run,receipt,env,blob=fixture('block')
        receipt['uncertainties']=[]
        receipt['requirements']=[dict(id='planning-question-'+('a'*64),status='uncertain',evidence='Missing authoritative source')]
        log=lambda *args:o.a.MARKER+json.dumps(receipt)+'\n'
        with patch.dict(os.environ,env,clear=True),patch.object(o.a.review,'blob',side_effect=blob):
            result=o.apply(p,'o/r',1,HEAD,adoption.EVALUATOR,333,log)
        self.assertEqual(result['state'],'returned_todo')
        self.assertEqual({x['name'] for x in p.issue['labels']},{'todo','bug'})
        self.assertEqual([w[0] for w in p.writes],['failure'])

    def test_authenticated_fail_or_block_returns_only_issue_to_todo(self):
        for decision in ('fail','block'):
            p,run,receipt,env,blob=fixture(decision);log=lambda *args:o.a.MARKER+json.dumps(receipt)+'\n'
            with self.subTest(decision=decision),patch.dict(os.environ,env,clear=True),patch.object(o.a.review,'blob',side_effect=blob):
                result=o.apply(p,'o/r',1,HEAD,adoption.EVALUATOR,333,log)
            self.assertEqual(result['state'],'returned_todo')
            self.assertEqual({x['name'] for x in p.issue['labels']},{'todo','bug'})
            self.assertEqual([w[0] for w in p.writes],['failure'])

    def test_pass_makes_no_lifecycle_or_status_write(self):
        p,run,receipt,env,blob=fixture('pass');log=lambda *args:o.a.MARKER+json.dumps(receipt)+'\n'
        with patch.dict(os.environ,env,clear=True),patch.object(o.a.review,'blob',side_effect=blob):
            result=o.apply(p,'o/r',1,HEAD,adoption.EVALUATOR,333,log)
        self.assertEqual(result['state'],'pass_waiting_for_separate_grant')
        self.assertEqual(p.writes,[]);self.assertEqual(p.issue_writes,[])

    def test_passing_target_cannot_queue_or_publish_without_a_grant(self):
        p,run,receipt,env,blob=fixture('pass');log=lambda *args:o.a.MARKER+json.dumps(receipt)+'\n'
        empty=dict(config=dict(version=1,grants=[]),source_commit='9'*40)
        with patch.dict(os.environ,env,clear=True),patch.object(o.a.review,'blob',side_effect=blob):
            result=o.apply(p,'o/r',1,HEAD,adoption.EVALUATOR,333,log)
            self.assertEqual(result['state'],'pass_waiting_for_separate_grant')
            with self.assertRaisesRegex(RuntimeError,'grant_missing_or_ambiguous'):
                o.a.enqueue(p,'o/r',adoption.EVALUATOR,333,'push',grant_loader=lambda:empty)
        self.assertEqual(p.writes,[])
        self.assertEqual(p.issue_writes,[])

    def test_queued_run_with_live_outcome_job_remains_actionable(self):
        p,run,receipt,env,blob=fixture('fail');run['status']='queued'
        log=lambda *args:o.a.MARKER+json.dumps(receipt)+'\n'
        with patch.dict(os.environ,env,clear=True),patch.object(o.a.review,'blob',side_effect=blob):
            result=o.apply(p,'o/r',1,HEAD,adoption.EVALUATOR,333,log)
        self.assertEqual(result['state'],'returned_todo')

    def test_changed_issue_forged_event_or_ambiguous_receipt_never_writes(self):
        for change in ('issue','event','duplicate','no_reason','bool_pr'):
            p,run,receipt,env,blob=fixture('fail')
            if change=='issue':p.issue['body']='new requirement'
            if change=='event':env['GITHUB_EVENT_NAME']='workflow_dispatch'
            if change=='no_reason':receipt['findings']=[]
            if change=='bool_pr':receipt['pr']=True
            row=o.a.MARKER+json.dumps(receipt)+'\n'
            log=lambda *args:row*2 if change=='duplicate' else row
            with self.subTest(change=change),patch.dict(os.environ,env,clear=True),patch.object(o.a.review,'blob',side_effect=blob),self.assertRaises(RuntimeError):
                o.apply(p,'o/r',1,HEAD,adoption.EVALUATOR,333,log)
            self.assertEqual(p.writes,[]);self.assertEqual(p.issue_writes,[])
