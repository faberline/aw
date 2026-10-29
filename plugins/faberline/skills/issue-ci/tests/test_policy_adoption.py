"""Exact reviewed policy adoption is fail-closed; all providers are synthetic."""
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_issue_contract_v2 as v2

spec=importlib.util.spec_from_file_location('policy_adoption_test',Path(__file__).parents[1]/'scripts/policy_adoption.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
HEAD,BASE,EVALUATOR='a'*40,'b'*40,'c'*40
SHA='d'*64


def grant():
    return dict(version=1,repo='o/r',pr=8,issue=7,head=HEAD,base=BASE,base_ref='main',evaluator=EVALUATOR,
        policy_blob='e'*40,base_policy_blob='f'*40,issue_sha256=SHA,pr_sha256=SHA,files_sha256=SHA,
        protected_paths=['.github/workflows/pr.yml'],ci=dict(run_id=111,attempt=1,jobs_sha256=SHA),
        review=dict(workflow_path='.github/workflows/review.yml',workflow_id=2,workflow_blob='e'*40,
            runner_path='plugins/faberline/skills/issue-ci/scripts/policy_adoption_target_review.py',runner_blob='f'*40,source_commit='1'*40,branch='codex/target-review',
            run_id=222,attempt=1,job='review',job_id=223,receipt_sha256=SHA),
        publisher=dict(workflow_path='.github/workflows/adopt.yml',workflow_id=3,workflow_blob='e'*40,
            source_commit='2'*40,branch='codex/adopt-once',job='adopt'),
        grant_review=dict(repo='o/r',pr=77,head='3'*40,blob='4'*40,workflow_path='.github/workflows/grant-review.yml',
            workflow_id=9,workflow_blob='5'*40,source_commit='6'*40,branch='codex/grant-review',
            run_id=999,attempt=1,job='grant-review',job_id=998))


def ci_data():
    cfg=v2.policy();workflow=cfg['workflows'][0]
    run=dict(id=111,run_attempt=1,workflow_id=77,path=workflow['path'],head_sha=HEAD,event='pull_request',
        head_repository={'full_name':'o/r'},status='completed',conclusion='success',
        pull_requests=[dict(number=8,head={'sha':HEAD},base={'sha':BASE})])
    jobs=[dict(id=i+1,name=name,status='completed',conclusion='success',html_url='https://example.test/job')
          for i,name in enumerate(workflow['jobs'])]
    rows=[{k:j[k] for k in ('id','name','status','conclusion')} for j in jobs]
    g=grant();g['ci']['jobs_sha256']=a.r.digest(sorted(rows,key=lambda x:x['name']))
    return g,cfg,run,jobs


class GrantTests(unittest.TestCase):
    def test_only_one_complete_exact_grant_can_be_selected(self):
        g=grant();document={'version':1,'grants':[g]}
        self.assertEqual(a.choose_grant(document,'o/r',EVALUATOR),g)
        for doc in ({'version':1,'grants':[]},{'version':1,'grants':[g,g]},
                    {'version':True,'grants':[g]}, {'version':1,'grants':[g],'extra':1}):
            with self.subTest(doc=doc),self.assertRaises(RuntimeError):a.choose_grant(doc,'o/r',EVALUATOR)
        with self.assertRaises(RuntimeError):a.choose_grant(document,'x/r',EVALUATOR)

    def test_malformed_broad_or_incomplete_grants_are_rejected(self):
        edits=[lambda g:g.update(head='branch'),lambda g:g.update(pr=True),lambda g:g.update(base_ref='dev'),
               lambda g:g.update(protected_paths=[]),lambda g:g.update(protected_paths=['../gate']),
               lambda g:g['publisher'].update(branch='main'),lambda g:g['review'].update(job_id=0),
               lambda g:g.update(extra='ignored')]
        for edit in edits:
            g=grant();edit(g)
            with self.subTest(g=g),self.assertRaises(RuntimeError):a.grant_spec(g)

    def test_file_list_requires_exact_nonempty_complete_names_and_blob_ids(self):
        pull={'changed_files':1};files=[{'filename':'src/x','status':'modified','sha':'f'*40}]
        _,items=a.file_inventory(pull,files);self.assertEqual(items[0]['previous'],'src/x')
        for p,f in [({'changed_files':2},files),({'changed_files':3000},files),
                    (pull,[dict(files[0],sha='bad')]),(pull,[dict(files[0],status='renamed')])]:
            with self.subTest(p=p,f=f),self.assertRaises(RuntimeError):a.file_inventory(p,f)

    def test_review_receipt_needs_every_file_and_nonempty_met_requirements(self):
        g=grant();inventory=[dict(path='src/x')]
        receipt=dict(version=1,repo=g['repo'],pr=g['pr'],issue=g['issue'],head=g['head'],base=g['base'],
            pr_sha256=g['pr_sha256'],issue_sha256=g['issue_sha256'],files_sha256=g['files_sha256'],
            ci_jobs_sha256=g['ci']['jobs_sha256'],reviewed_files=['src/x'],
            requirements=[dict(id='R1',status='met',evidence='Source and test evidence')],decision='pass',findings=[],uncertainties=[])
        g['review']['receipt_sha256']=a.r.digest(receipt)
        self.assertEqual(a.review_result(g,receipt,inventory),receipt)
        edits=[lambda x:x.update(head='0'*40),lambda x:x.update(decision='fail'),lambda x:x.update(findings=['bug']),
               lambda x:x.update(uncertainties=['unknown']),lambda x:x.update(reviewed_files=[]),
               lambda x:x.update(requirements=[]),lambda x:x['requirements'][0].update(status='uncertain')]
        for edit in edits:
            bad=copy.deepcopy(receipt);edit(bad)
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):a.review_result(g,bad,inventory)


class AdoptionCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.g=grant();self.pull=v2.pull();self.pull.update(draft=False)
        self.pull['base'].update(sha=BASE,ref='main',repo={'full_name':'o/r'})
        self.pull['head'].update(sha=HEAD,repo={'full_name':'o/r'})
        self.pull.update(number=8,state='open',body='Closes #7\n')
        self.issue=v2.issue();self.issue.update(assignees=[],updated_at='2026-10-29T00:00:00Z')
        self.g['issue_sha256']=a.r.digest({k:self.issue[k] for k in ('number','title','body')})
        self.g['pr_sha256']=a.r.digest([self.pull['title'],self.pull['body']])
        self.receipt=dict(decision='pass',id='synthetic reviewed result')
        self.g['review']['receipt_sha256']=a.r.digest(self.receipt)
        self.env=dict(GITHUB_ACTIONS='true',GITHUB_EVENT_NAME='push',GITHUB_REPOSITORY='o/r',
            GITHUB_RUN_ID='333',GITHUB_RUN_ATTEMPT='2',GITHUB_WORKFLOW_SHA=self.g['publisher']['source_commit'])
        parent=self
        class API:
            def api(self,path):
                if path=='repos/o/r/actions/runs/333':return dict(id=333,run_attempt=2)
                if path=='repos/o/r/pulls/8':return copy.deepcopy(parent.pull)
                if path=='repos/o/r/issues/7':return copy.deepcopy(parent.issue)
                raise AssertionError(path)
        self.api=API()

    def test_checkpoint_binds_grant_review_and_source_attempt(self):
        with patch.dict(os.environ,self.env,clear=True):
            result=a.prepare_adoption_close_record(self.api,dict(source_commit='9'*40),self.g,EVALUATOR,333,self.receipt)
        self.assertEqual(result['payload']['adoption_sha256'],a.r.digest(self.g))
        self.assertEqual(result['payload']['review_sha256'],self.g['review']['receipt_sha256'])
        self.assertEqual(result['payload']['adoption_source'],'9'*40)
        self.assertEqual(result['payload']['checkpoint']['issue'],7)
        self.assertEqual(result['payload']['source_attempt'],2)

    def test_changed_context_contract_or_review_refuses(self):
        for mutate in (lambda:self.env.update(GITHUB_RUN_ATTEMPT='1'),
                       lambda:self.env.update(GITHUB_WORKFLOW_SHA='f'*40),
                       lambda:self.issue.update(body='changed'),
                       lambda:self.receipt.update(decision='fail')):
            with self.subTest(mutate=mutate):
                self.setUp();mutate()
                with patch.dict(os.environ,self.env,clear=True),self.assertRaises(RuntimeError):
                    a.prepare_adoption_close_record(self.api,dict(source_commit='9'*40),self.g,EVALUATOR,333,self.receipt)


class EvidenceTests(unittest.TestCase):
    class API:
        evaluate_evidence=staticmethod(a.ci.evaluate_evidence)
        def __init__(self,run,jobs):self.run=run;self.jobs=jobs
        def snapshot(self,*args):return {v2.base.WORKFLOW:(copy.deepcopy(self.run),copy.deepcopy(self.jobs))}

    def test_complete_ci_is_bound_to_latest_run_attempt_and_every_job(self):
        g,cfg,run,jobs=ci_data();api=self.API(run,jobs)
        with patch.object(a.review,'blob',return_value='trusted'):
            self.assertEqual(len(a.candidate_evidence(api,g,cfg)),4)
            for change in ('attempt','base','workflow','missing','extra','skipped','failed'):
                bad=self.API(copy.deepcopy(run),copy.deepcopy(jobs))
                if change=='attempt':bad.run['run_attempt']=2
                if change=='base':bad.run['pull_requests'][0]['base']['sha']='0'*40
                if change=='workflow':bad.run['workflow_id']=42
                if change=='missing':bad.jobs.pop()
                if change=='extra':bad.jobs.append(dict(bad.jobs[0],id=99,name='unknown'))
                if change=='skipped':bad.jobs[0]['conclusion']='skipped'
                if change=='failed':bad.jobs[0]['conclusion']='failure'
                with self.subTest(change=change),self.assertRaises(RuntimeError):a.candidate_evidence(bad,g,cfg)

    def test_untrusted_or_nonterminal_publisher_cannot_authorize(self):
        g=grant();p=g['publisher'];workflow='''name: Reviewed policy adoption
on:
  push:
    branches: [codex/adopt-once]
    paths: [.github/workflows/adopt.yml]
permissions: {}
concurrency:
  group: issue-ci-handoff
  cancel-in-progress: false
jobs:
  adopt:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      actions: read
      contents: write
      pull-requests: write
      statuses: write
      issues: read
    steps:
      - uses: faberline/aw/.github/actions/policy-adoption@'''+EVALUATOR+'\n'
        run=dict(id=333,workflow_id=3,path=p['workflow_path'],head_sha=p['source_commit'],head_branch=p['branch'],
                 head_repository={'full_name':'o/r'},event='push',run_attempt=1,status='in_progress')
        class API:
            def api(self,path):return run
            def pages(self,path,key):return [{'id':334,'name':'adopt','status':'in_progress'}]
        with patch.object(a.review,'blob',return_value=workflow):
            a.publisher_provenance(API(),g,333,'push')
            with self.assertRaises(RuntimeError):a.publisher_provenance(API(),g,333,'pull_request')
            with self.assertRaises(RuntimeError):a.publisher_provenance(API(),g,333,'push',completed=True)
            run.update(status='completed',conclusion='success')
            class CompletedAPI(API):
                def pages(self,path,key):return [{'id':334,'name':'adopt','status':'completed','conclusion':'success'}]
            identity=a.publisher_provenance(CompletedAPI(),g,333,'push',completed=True)
            self.assertEqual(identity['job_id'],334)
            class FailedJobAPI(API):
                def pages(self,path,key):return [{'id':334,'name':'adopt','status':'completed','conclusion':'failure'}]
            with self.assertRaises(RuntimeError):a.publisher_provenance(FailedJobAPI(),g,333,'push',completed=True)
            with self.assertRaises(RuntimeError):a.publisher_provenance(CompletedAPI(),g,333,'push')
        with patch.object(a.review,'blob',return_value=workflow.replace('issues: read','issues: write')):
            with self.assertRaises(RuntimeError):a.publisher_provenance(API(),g,333,'push')

class MutationSafetyTests(unittest.TestCase):
    def setup_run(self):
        g=grant();document=dict(config=dict(version=1,grants=[g]),source_commit='9'*40)
        provider=dict(id='PR_8',state='OPEN',headRefOid=HEAD,baseRefOid=BASE,baseRefName='main',isDraft=False,autoMergeRequest=None,mergeCommit=None)
        context=dict(repo='o/r',pr=8,head=HEAD,base=BASE,base_ref='main')
        policy=dict(status_context='claude/verified-merge',status_app_id=15368,merge_method='SQUASH',ruleset_id=9)
        state=dict(context=context,issue=7,policy=policy,provider=provider,receipt={'decision':'pass'},ci_jobs=[{'id':1}])
        env=dict(GITHUB_REPOSITORY='o/r',GITHUB_EVENT_NAME='push',GITHUB_RUN_ID='333',GITHUB_SHA='2'*40,
                 GITHUB_WORKFLOW_SHA='2'*40,GITHUB_REF='refs/heads/codex/adopt-once')
        class API:
            def __init__(self):self.writes=[]
            def api(self,path,method='GET',data=None):
                if method!='GET':self.writes.append((path,method,data));return {}
                if path=='repos/faberline/aw/pulls/77':return {}
                raise AssertionError(path)
        return g,document,state,env,API()

    def test_unmerged_grant_holds_before_any_write(self):
        import os
        g,document,state,env,api=self.setup_run()
        with patch.dict(os.environ,env,clear=True):
            with self.assertRaisesRegex(RuntimeError,'grant_pr_identity'):
                a.enqueue(api,'o/r',EVALUATOR,333,'push',grant_loader=lambda:document)
        self.assertEqual(api.writes,[])

    def test_evidence_drift_after_pending_never_writes_success(self):
        import os
        g,document,state,env,api=self.setup_run();changed=copy.deepcopy(state);changed['ci_jobs']=[{'id':2}]
        with patch.dict(os.environ,env,clear=True),patch.object(a,'verify_grant_attestation'),\
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=[state,changed]):
            result=a.enqueue(api,'o/r',EVALUATOR,333,'push',grant_loader=lambda:document)
        self.assertEqual(result['state'],'held_after_pending')
        self.assertEqual([entry[2]['state'] for entry in api.writes],['pending'])

    def test_rechecked_evidence_can_queue_only_exact_head(self):
        import os
        g,document,state,env,api=self.setup_run();queued=copy.deepcopy(state)
        queued['provider']['autoMergeRequest']=dict(mergeMethod='SQUASH',enabledBy={'login':'github-actions','__typename':'Bot'},
                                                     commitHeadline='Merge PR #8',commitBody='Closes #7')
        response={'enablePullRequestAutoMerge':{'pullRequest':queued['provider']}}
        with patch.dict(os.environ,env,clear=True),patch.object(a,'verify_grant_attestation'),\
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=[state,state,queued]),\
             patch.object(a.native,'graphql',return_value=response) as graphql,\
             patch.object(a.native,'provider_pull',return_value=queued['provider']):
            result=a.enqueue(api,'o/r',EVALUATOR,333,'push',grant_loader=lambda:document)
        self.assertEqual(result['state'],'queued')
        self.assertEqual([entry[2]['state'] for entry in api.writes],['pending','success'])
        self.assertEqual(graphql.call_args.args[2]['input']['expectedHeadOid'],HEAD)
        self.assertEqual(graphql.call_args.args[2]['input']['commitBody'],'Closes #7')

    def test_record_is_emitted_only_after_successful_status(self):
        g,document,state,env,api=self.setup_run();queued=copy.deepcopy(state)
        queued['provider']['autoMergeRequest']=dict(mergeMethod='SQUASH',enabledBy={'login':'github-actions','__typename':'Bot'},
                                                     commitHeadline='Merge PR #8',commitBody='Closes #7')
        response={'enablePullRequestAutoMerge':{'pullRequest':queued['provider']}}
        out=io.StringIO()
        def prepare(*args):
            self.assertEqual([entry[2]['state'] for entry in api.writes],['pending'])
            return dict(test='prepared')
        with patch.dict(os.environ,env,clear=True),patch.object(a,'verify_grant_attestation'),\
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=[state,state,queued]),\
             patch.object(a.native,'graphql',return_value=response),\
             patch.object(a.native,'provider_pull',return_value=queued['provider']),contextlib.redirect_stdout(out):
            result=a.enqueue(api,'o/r',EVALUATOR,333,'push',grant_loader=lambda:document,prepare_record=prepare)
        self.assertEqual(result['state'],'queued')
        self.assertEqual([entry[2]['state'] for entry in api.writes],['pending','success'])
        self.assertIn('AW_POST_MERGE_CHECKPOINT_V1',out.getvalue())

class AttestationTests(unittest.TestCase):
    def setup_attestation(self):
        g=grant();att=g['grant_review'];payload={k:v for k,v in g.items() if k!='grant_review'}
        doc=dict(config=dict(version=1,grants=[g]),source_commit='9'*40)
        proposal=dict(version=1,grants=[payload]);merge='8'*40;finalblob='7'*40
        workflow='''name: Reviewed policy grant
on:
  push:
    branches: [codex/grant-review]
    paths: [.github/workflows/grant-review.yml]
permissions: {}
jobs:
  grant-review:
    runs-on: ubuntu-latest
    timeout-minutes: 70
    permissions:
      actions: read
      contents: read
      pull-requests: read
      issues: read
    steps:
      - uses: faberline/aw/.github/actions/policy-adoption-grant-review@'''+EVALUATOR+'''
        with:
          grant-pr: '77'
          grant-head: '''+att['head']+'''
          claude-token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
'''
        receipt=dict(version=1,repo='o/r',target_pr=8,target_head=HEAD,grant_pr=77,
            grant_head=att['head'],grant_blob=att['blob'],grant_digest=a.r.digest(payload),
            grant_issue=97,grant_pr_sha256=a.r.digest(['Grant','Closes #97']),
            grant_issue_sha256=a.r.digest({'number':97,'title':'Grant issue','body':'Review the exact grant'}),
            decision='pass',findings=[],uncertainties=[])
        class API:
            def __init__(self):self.bad=None
            def api(self,path):
                if path=='repos/faberline/aw/pulls/77':return dict(merged=self.bad!='open',state='open' if self.bad=='open' else 'closed',
                    changed_files=1,merge_commit_sha=merge,title='Grant',body='Closes #97',
                    base={'ref':'main','sha':BASE},head={'sha':doc['source_commit'],'repo':{'full_name':'faberline/aw'}})
                if path=='repos/faberline/aw/commits/main':return {'sha':BASE}
                if path=='repos/faberline/aw/issues/97':return dict(number=97,title='Grant issue',body='Review the exact grant',state='open' if self.bad=='open' else 'closed')
                if '/compare/' in path:return {'status':'diverged' if self.bad=='history' else 'ahead'}
                if path=='repos/o/r/actions/runs/1000':return dict(id=1000,workflow_id=9,path=att['workflow_path'],
                    head_sha='0'*40,head_branch=att['branch'],event='push',head_repository={'full_name':'o/r'},
                    run_attempt=1,status='completed',conclusion='failure' if self.bad=='final_failure' else 'success')
                if path=='repos/o/r/actions/runs/999':return dict(id=999,workflow_id=9,path=att['workflow_path'],
                    head_sha=att['source_commit'],head_branch=att['branch'],event='push',head_repository={'full_name':'o/r'},
                    run_attempt=1,status='completed',conclusion='success')
                raise AssertionError(path)
            def pages(self,path,key=None):
                if '/actions/workflows/9/runs?' in path:return [] if self.bad=='missing_final' else [dict(id=1000,head_sha='0'*40)]
                if path.endswith('/runs/1000/attempts/1/jobs'):return [dict(name=att['job'],id=1001,status='completed',conclusion='success')]
                if path.endswith('/pulls/77/files'):return [dict(filename=a.GRANTS_PATH,status='modified',sha=finalblob)]
                if path.endswith('/pulls/77/commits'):return [{'sha':att['head']}]
                if path.endswith('/attempts/1/jobs'):return [dict(name=att['job'],id=att['job_id'],status='completed',conclusion='success')]
                raise AssertionError(path)
        api=API()
        def blob(api_,repo,path,ref,expected=None):
            if repo=='o/r':return workflow.replace(att['head'],doc['source_commit']) if ref=='0'*40 else workflow
            if ref==doc['source_commit']:return json.dumps(doc['config'])
            if ref==att['head']:return json.dumps(proposal)
            raise AssertionError(ref)
        return g,doc,api,receipt,blob,workflow

    def test_only_current_reviewed_grant_can_authorize(self):
        g,doc,api,receipt,blob,workflow=self.setup_attestation()
        final=dict(receipt,grant_head=doc['source_commit'],grant_blob='7'*40)
        log=lambda repo,job:a.GRANT_MARKER+json.dumps(final if job==1001 else receipt)+'\n'
        with patch.object(a.review,'blob',side_effect=blob):
            result=a.verify_grant_attestation(api,doc,g,EVALUATOR,log)
        self.assertEqual(result,receipt)

    def test_final_review_must_match_final_head_and_blob(self):
        for change in ('missing_final','final_failure','wrong_blob','wrong_head','duplicate'):
            g,doc,api,receipt,blob,workflow=self.setup_attestation()
            if change in ('missing_final','final_failure'):api.bad=change
            final=dict(receipt,grant_head=doc['source_commit'],grant_blob='7'*40)
            if change=='wrong_blob':final['grant_blob']='0'*40
            if change=='wrong_head':final['grant_head']='0'*40
            def log(repo,job):
                line=a.GRANT_MARKER+json.dumps(final if job==1001 else receipt)+'\n'
                return line*2 if change=='duplicate' and job==1001 else line
            with self.subTest(change=change),patch.object(a.review,'blob',side_effect=blob),self.assertRaises(RuntimeError):
                a.verify_grant_attestation(api,doc,g,EVALUATOR,log)

    def test_final_open_head_can_verify_earlier_attestation_without_authorizing_merge(self):
        g,doc,api,receipt,blob,workflow=self.setup_attestation();api.bad='open'
        log=lambda repo,job:a.GRANT_MARKER+json.dumps(receipt)+'\n'
        with patch.object(a.review,'blob',side_effect=blob):
            self.assertEqual(a.verify_grant_attestation(api,doc,g,EVALUATOR,log,require_merged=False),receipt)
            with self.assertRaisesRegex(RuntimeError,'grant_pr_not_merged'):
                a.verify_grant_attestation(api,doc,g,EVALUATOR,log)

    def test_untrusted_history_workflow_and_review_receipt_refuse(self):
        for change in ('history','workflow','decision','digest','duplicate'):
            g,doc,api,receipt,blob,workflow=self.setup_attestation()
            if change=='history':api.bad='history'
            if change=='workflow':workflow=workflow.replace('issues: read','issues: write')
            if change=='decision':receipt['decision']='fail'
            if change=='digest':receipt['grant_digest']='0'*64
            def source(api_,repo,path,ref,expected=None):
                return workflow if repo=='o/r' else blob(api_,repo,path,ref,expected)
            row=a.GRANT_MARKER+json.dumps(receipt)+'\n'
            log=lambda repo,job:row*2 if change=='duplicate' else row
            with self.subTest(change=change),patch.object(a.review,'blob',side_effect=source),self.assertRaises(RuntimeError):
                a.verify_grant_attestation(api,doc,g,EVALUATOR,log)

class ExactTargetTests(unittest.TestCase):
    def setup_target(self):
        g=grant();pull=v2.pull();pull.update(draft=False);pull['base']['ref']='main'
        issue=v2.issue();cfg=v2.policy()
        cfg['qualification']['rules'].append(dict(paths=['**'],require=['unit','functional','perf'],exempt={}))
        files=[dict(filename='.github/workflows/pr.yml',status='modified',sha='f'*40)]
        _,inventory=a.file_inventory(pull,files)
        g['pr_sha256']=a.r.digest([pull['title'],pull['body']])
        g['issue_sha256']=a.r.digest({k:issue[k] for k in ('number','title','body')})
        g['files_sha256']=a.r.digest(inventory)
        provider=dict(id='PR_8',state='OPEN',headRefOid=HEAD,baseRefOid=BASE,baseRefName='main',isDraft=False)
        policy=dict(status_context='claude/verified-merge',status_app_id=15368,merge_method='SQUASH',ruleset_id=9)
        class API:
            def __init__(self):self.current=BASE
            def api(self,path):
                if path=='repos/o/r/pulls/8':return copy.deepcopy(pull)
                if path=='repos/o/r':return {'default_branch':'main'}
                if path=='repos/o/r/commits/main':return {'sha':self.current}
                if path=='repos/o/r/issues/7':return copy.deepcopy(issue)
                raise AssertionError(path)
            def pages(self,path):
                if path=='repos/o/r/pulls/8/files':return copy.deepcopy(files)
                raise AssertionError(path)
        return g,pull,issue,cfg,files,provider,policy,API()

    def test_only_exact_reviewed_protected_path_is_accepted(self):
        g,pull,issue,cfg,files,provider,policy,api=self.setup_target()
        with patch.object(a,'candidate_policy',return_value=cfg),patch.object(a,'candidate_evidence',return_value=['green']),\
             patch.object(a,'review_provenance',return_value={'decision':'pass'}),patch.object(a.native,'policy',return_value=policy),\
             patch.object(a.native,'base_rules'),patch.object(a.native,'provider_pull',return_value=provider):
            value=a.evaluate(api,g,lambda *args:'')
            self.assertEqual(value['issue'],7)
            for change in ('base','issue','file','protected','unknown'):
                old_api=api.current;old_issue=copy.deepcopy(issue);old_file=copy.deepcopy(files);old_paths=g['protected_paths'][:];old_cfg=copy.deepcopy(cfg)
                if change=='base':api.current='0'*40
                if change=='issue':issue['body']='changed'
                if change=='file':files[0]['sha']='0'*40
                if change=='protected':g['protected_paths']=['.github/workflows/other.yml']
                if change=='unknown':cfg['qualification']['contracts']['7']['unresolved']=['missing gate']
                with self.subTest(change=change),self.assertRaises(RuntimeError):a.evaluate(api,g,lambda *args:'')
                api.current=old_api;issue.clear();issue.update(old_issue);files[:]=old_file;g['protected_paths']=old_paths;cfg.clear();cfg.update(old_cfg)

class TargetReviewProvenanceTests(unittest.TestCase):
    def fixture(self):
        g=grant();spec=g['review'];inventory=[dict(path='src/x')]
        receipt=dict(version=1,repo=g['repo'],pr=g['pr'],issue=g['issue'],head=g['head'],base=g['base'],
            pr_sha256=g['pr_sha256'],issue_sha256=g['issue_sha256'],files_sha256=g['files_sha256'],
            ci_jobs_sha256=g['ci']['jobs_sha256'],reviewed_files=['src/x'],
            requirements=[dict(id='R1',status='met',evidence='Current source and CI')],decision='pass',findings=[],uncertainties=[])
        spec['receipt_sha256']=a.r.digest(receipt)
        workflow='''name: Reviewed policy target
on:
  push:
    branches: [codex/target-review]
    paths: [.github/workflows/review.yml]
permissions: {}
jobs:
  review:
    runs-on: ubuntu-latest
    timeout-minutes: 70
    permissions:
      actions: read
      contents: read
      pull-requests: read
      issues: read
    steps:
      - uses: faberline/aw/.github/actions/policy-adoption-target-review@'''+EVALUATOR+'''
        with:
          target-pr: '8'
          target-head: '''+HEAD+'''
          claude-token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
  outcome:
    needs: review
    runs-on: ubuntu-latest
    timeout-minutes: 30
    concurrency:
      group: issue-ci-handoff
      cancel-in-progress: false
    permissions:
      actions: read
      contents: write
      pull-requests: write
      statuses: write
      issues: write
    steps:
      - uses: faberline/aw/.github/actions/policy-adoption-review-outcome@'''+EVALUATOR+'''
        with:
          target-pr: '8'
          target-head: '''+HEAD+'''
          review-run: ${{ github.run_id }}
'''
        class API:
            def api(self,path):return dict(id=222,workflow_id=2,path=spec['workflow_path'],head_sha=spec['source_commit'],
                head_branch=spec['branch'],head_repository={'full_name':'o/r'},event='push',run_attempt=1,status='completed',conclusion='success')
            def pages(self,path,key=None):return [dict(id=223,name='review',status='completed',conclusion='success')]
        return g,inventory,receipt,workflow,API()

    def test_review_is_produced_only_by_pinned_data_only_action(self):
        g,inventory,receipt,workflow,api=self.fixture();seen=[]
        def blob(api_,repo,path,ref,expected=None):
            seen.append((repo,path,ref,expected))
            return workflow if repo=='o/r' else 'trusted target action source'
        log=lambda repo,job:a.MARKER+json.dumps(receipt)+'\n'
        with patch.object(a.review,'blob',side_effect=blob):
            self.assertEqual(a.review_provenance(api,g,inventory,log),receipt)
        self.assertIn((a.GRANTS_REPO,g['review']['runner_path'],EVALUATOR,g['review']['runner_blob']),seen)
        with patch.object(a.review,'blob',side_effect=lambda api_,repo,path,ref,expected=None:workflow.replace('issues: read','issues: write') if repo=='o/r' else 'trusted'):
            with self.assertRaisesRegex(RuntimeError,'target_review_workflow'):a.review_provenance(api,g,inventory,log)
        with patch.object(a.review,'blob',side_effect=blob):
            with self.assertRaisesRegex(RuntimeError,'receipt_missing_or_ambiguous'):a.review_provenance(api,g,inventory,lambda *args:log(None,None)*2)

class DistributionTests(unittest.TestCase):
    def test_published_source_has_no_active_grant_and_separate_credentials(self):
        root=Path(__file__).parents[5]
        data=json.loads((root/'.github/policy-adoption-grants.json').read_text())
        self.assertEqual(set(data),{'version','grants'})
        self.assertEqual(data['version'],1)
        self.assertIsInstance(data['grants'],list)
        for item in data['grants']:a.grant_spec(item)
        with self.assertRaises(RuntimeError):a.choose_grant({'version':1,'grants':[]},'o/r',EVALUATOR)
        for name in ('policy-adoption','policy-adoption-review-outcome'):
            raw=(root/'.github/actions'/name/'action.yml').read_text()
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN',raw)
            self.assertNotIn('actions/checkout',raw)
        for name in ('policy-adoption-target-review','policy-adoption-grant-review'):
            raw=(root/'.github/actions'/name/'action.yml').read_text()
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN: ''",raw)
            self.assertIn('CLAUDE_CODE_OAUTH_TOKEN: ${{ inputs.claude-token }}',raw)
            self.assertNotIn('actions/checkout',raw)

class CandidatePolicyTests(unittest.TestCase):
    def test_prior_mandatory_jobs_cannot_be_removed(self):
        g=grant();candidate=v2.policy();candidate['aw_revision']=EVALUATOR
        previous=v2.base.config()
        def blob(api,repo,path,ref,expected=None):return json.dumps(candidate if ref==HEAD else previous)
        with patch.object(a.review,'blob',side_effect=blob),patch.object(a.native,'writer_contract'):
            self.assertEqual(a.candidate_policy(None,g),candidate)
            previous['workflows'][0]['jobs'].append('previous mandatory job')
            with self.assertRaisesRegex(RuntimeError,'candidate_dropped_existing_gate'):a.candidate_policy(None,g)

    def test_unverified_writer_and_nonempty_blockers_never_adopt(self):
        g=grant();candidate=v2.policy();candidate['aw_revision']=EVALUATOR;previous=v2.base.config()
        def blob(api,repo,path,ref,expected=None):return json.dumps(candidate if ref==HEAD else previous)
        with patch.object(a.review,'blob',side_effect=blob),patch.object(a.native,'writer_contract',side_effect=RuntimeError('untrusted writer')):
            with self.assertRaisesRegex(RuntimeError,'untrusted writer'):a.candidate_policy(None,g)
        candidate['qualification_blockers']=['source acceptance missing']
        with patch.object(a.review,'blob',side_effect=blob),patch.object(a.native,'writer_contract'):
            with self.assertRaisesRegex(RuntimeError,'candidate_policy_not_full_v2'):a.candidate_policy(None,g)
