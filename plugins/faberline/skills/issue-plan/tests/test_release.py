"""Realistic isolated release state, provider evidence and guarded write controls."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('release_controls',Path(__file__).parents[1]/'scripts/release.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
s=r.state
HEAD='a'*40
OLD='b'*40
REPO='org/app'
PATH='.github/workflows/test.yml'
TAG='v1.2.3-rc.1'
FINAL='v1.2.3'


def snapshot():
    milestone=dict(number=7,title='Product milestone',description='Deliver the approved goal.',state='open')
    contract=dict(version='1.2.3',goal_sha256=s.digest(s.goal_identity(milestone)),
                  acceptance=[dict(goal='Approved goal',issues=[1],checks=['unit'])],publication='github-release')
    return dict(repo=REPO,head=HEAD,milestone=milestone,contract=contract,
                issues=[dict(number=1,state='closed',body='Approved goal')],
                pulls=[dict(number=2,merged_at='2026-10-31',_landed=True,_issues=[1])],
                tags={},evidence={},publication=None)


def tagged(final=False,status='success'):
    value=snapshot();value['tags'][TAG]=HEAD
    value['evidence'][TAG]=dict(status=status,reasons=[],failures=[])
    if final:
        value['tags'][FINAL]=HEAD;value['evidence'][FINAL]=dict(status=status,reasons=[],failures=[])
    return value


class StateControls(unittest.TestCase):
    def advanced(self, final=False):
        value=tagged(final=final);value['head']=OLD
        value['release_commits']={HEAD:dict(landed=True,pulls=[2],contract_sha256=s.digest(value['contract']))}
        return value

    def test_passing_rc_with_complete_frozen_work_survives_unrelated_main_advance(self):
        value=self.advanced();result=s.evaluate(value)
        self.assertEqual((result['state'],result['tag'],result['release_head']),('ready_final',FINAL,HEAD))

    def test_formal_full_tests_publication_and_close_survive_unrelated_main_advance(self):
        value=self.advanced(final=True)
        self.assertEqual(s.evaluate(value)['state'],'ready_publish')
        value['publication']=dict(tag_name=FINAL,draft=False,prerelease=False,published_at='now')
        self.assertEqual(s.evaluate(value)['state'],'ready_close')
        value['milestone']['state']='closed'
        self.assertEqual(s.evaluate(value)['state'],'complete')

    def test_passing_old_rc_missing_current_work_contract_or_ancestry_needs_new_rc(self):
        for change in ('missing','work','contract','ancestry'):
            value=self.advanced();proof=value['release_commits'][HEAD]
            if change=='missing':value['release_commits']={}
            elif change=='work':proof['pulls']=[]
            elif change=='contract':proof['contract_sha256']='0'*64
            else:proof['landed']=False
            with self.subTest(change=change):
                result=s.evaluate(value)
                self.assertEqual((result['state'],result['tag']),('ready_rc','v1.2.3-rc.2'))

    def test_formal_tag_cannot_publish_without_current_work_and_contract(self):
        for key,replacement in [('pulls',[]),('contract_sha256','0'*64),('landed',False)]:
            value=self.advanced(final=True);value['release_commits'][HEAD][key]=replacement
            with self.subTest(key=key),self.assertRaises(ValueError):s.evaluate(value)

    def test_complete_goal_work_creates_first_rc_not_a_formal_tag(self):
        result=s.evaluate(snapshot())
        self.assertEqual((result['state'],result['action'],result['tag']),('ready_rc','tag',TAG))

    def test_open_issue_or_unmerged_or_dependency_branch_pr_keeps_work_open(self):
        for key in ('issue','pr','landed'):
            value=snapshot()
            if key=='issue':value['issues'][0]['state']='open'
            elif key=='pr':value['pulls'][0]['merged_at']=None
            else:value['pulls'][0]['_landed']=False
            with self.subTest(key=key):
                result=s.evaluate(value);self.assertEqual(result['state'],'work');self.assertIsNone(result['action'])

    def test_closed_issue_without_merged_goal_pr_is_not_goal_completion(self):
        value=snapshot();value['pulls']=[]
        with self.assertRaisesRegex(ValueError,'landed PR'):s.evaluate(value)

    def test_empty_incomplete_or_unknown_issue_inventory_cannot_release(self):
        for issues in ([],[dict(number=1,state='unknown')],[dict(number=1,state='closed')]*2):
            value=snapshot();value['issues']=issues
            with self.subTest(issues=issues),self.assertRaises(ValueError):s.evaluate(value)

    def test_goal_evidence_cannot_reference_an_issue_outside_milestone(self):
        value=snapshot();value['contract']['acceptance'][0]['issues']=[99]
        with self.assertRaisesRegex(ValueError,'outside'):s.evaluate(value)

    def test_missing_or_running_rc_never_creates_another_rc(self):
        for evidence in ({}, {TAG:dict(status='pending',reasons=['queued'])}):
            value=tagged();value['evidence']=evidence;value['head']=OLD
            result=s.evaluate(value)
            self.assertEqual(result['state'],'testing');self.assertIsNone(result['action'])

    def test_failed_rc_requests_repair_in_same_milestone(self):
        value=tagged(status='failure');value['evidence'][TAG]['failures']=[dict(job=10)]
        result=s.evaluate(value)
        self.assertEqual((result['state'],result['action'],result['milestone']),('repair','failure_issue',7))

    def test_unrelated_new_commit_does_not_erase_required_failure_issue(self):
        value=tagged(status='failure');value['head']=OLD
        self.assertEqual(s.evaluate(value)['action'],'failure_issue')

    def test_fixed_issue_and_landed_pr_advance_unique_rc_number(self):
        value=tagged(status='failure');value['head']=OLD
        marker=s.repair_marker(REPO,7,TAG,HEAD,value['evidence'][TAG])
        value['issues'].append(dict(number=3,state='closed',body=marker))
        value['pulls'].append(dict(number=4,merged_at='now',_landed=True,_issues=[3]))
        self.assertEqual(s.evaluate(value)['tag'],'v1.2.3-rc.2')

    def test_passing_rc_promotes_only_the_same_commit(self):
        value=tagged();self.assertEqual(s.evaluate(value)['tag'],FINAL)
        value['head']=OLD
        self.assertEqual(s.evaluate(value)['tag'],'v1.2.3-rc.2')

    def test_formal_tag_also_waits_for_its_own_full_tests(self):
        value=tagged(final=True);value['evidence'][FINAL]['status']='pending'
        result=s.evaluate(value);self.assertEqual(result['state'],'testing');self.assertIsNone(result['action'])

    def test_formal_failure_keeps_milestone_open_and_requires_new_version(self):
        value=tagged(final=True);value['evidence'][FINAL]['status']='failure'
        result=s.evaluate(value)
        self.assertEqual(result['action'],'failure_issue');self.assertIn('new reviewed SemVer',' '.join(result['reasons']))

    def test_wrong_formal_commit_or_missing_rc_success_cannot_publish(self):
        for change in ('sha','rc','moved'):
            value=tagged(final=True)
            if change=='sha':value['tags'][FINAL]=OLD
            elif change=='moved':value['head']=OLD
            else:value['evidence'][TAG]['status']='failure'
            with self.subTest(change=change),self.assertRaises(ValueError):s.evaluate(value)

    def test_publication_and_final_tests_precede_milestone_closure(self):
        value=tagged(final=True)
        self.assertEqual(s.evaluate(value)['action'],'publish')
        value['publication']=dict(tag_name=FINAL,draft=False,prerelease=False,published_at='now')
        self.assertEqual(s.evaluate(value)['action'],'close')
        value['milestone']['state']='closed'
        self.assertEqual(s.evaluate(value)['state'],'complete')

    def test_draft_or_prerelease_publication_cannot_close_milestone(self):
        for field in ('draft','prerelease'):
            value=tagged(final=True);value['publication']=dict(tag_name=FINAL,draft=False,prerelease=False,published_at='now')
            value['publication'][field]=True
            with self.subTest(field=field),self.assertRaises(ValueError):s.evaluate(value)

    def test_manually_closed_milestone_is_not_a_release_completion_receipt(self):
        value=snapshot();value['milestone']['state']='closed'
        result=s.evaluate(value)
        self.assertEqual(result['state'],'hold');self.assertIsNone(result['action'])

    def test_distinct_failed_attempts_have_distinct_repair_identity(self):
        first=dict(failures=[dict(workflow=PATH,run=1,attempt=1)])
        second=dict(failures=[dict(workflow=PATH,run=1,attempt=2)])
        self.assertNotEqual(s.repair_marker(REPO,7,TAG,HEAD,first),s.repair_marker(REPO,7,TAG,HEAD,second))

    def test_reviewed_contract_binds_existing_goal_and_actual_full_checks(self):
        value=snapshot();config=dict(version=1,repo=REPO,milestones={'7':value['contract']})
        self.assertEqual(s.contract(config,REPO,value['milestone'],{'unit':{}})['version'],'1.2.3')
        for change in ('goal','version','checks','issues','empty','repo'):
            modified=copy.deepcopy(config)
            if change=='goal':modified['milestones']['7']['goal_sha256']='0'*64
            elif change=='version':modified['milestones']['7']['version']='01.2.3'
            elif change=='checks':modified['milestones']['7']['acceptance'][0]['checks']=['invented']
            elif change=='issues':modified['milestones']['7']['acceptance'][0]['issues']=[]
            elif change=='empty':modified['milestones']['7']['acceptance']=[]
            else:modified['repo']='other/repo'
            with self.subTest(change=change),self.assertRaises(ValueError):s.contract(modified,REPO,value['milestone'],{'unit':{}})


class EvidenceProvider:
    repo=REPO
    def __init__(self):
        self.raw=b'name: Tests\non:\n  push:\n    tags: ["v*"]\njobs: {}\n'
        self.sha=hashlib.sha1(b'blob '+str(len(self.raw)).encode()+b'\0'+self.raw).hexdigest()
        self.run=dict(id=10,run_attempt=1,workflow_id=12,event='push',head_branch=TAG,head_sha=HEAD,
                      path=PATH,head_repository={'full_name':REPO},status='completed',conclusion='success')
        self.jobs=[dict(id=n,name=name,status='completed',conclusion='success',run_id=10,run_attempt=1,head_sha=HEAD,
                       html_url=f'https://github.com/{REPO}/actions/runs/10/job/{n}',steps=[])
                   for n,name in enumerate(('unit','functional','performance'),1)]
        self.runs=[self.run];self.branch=None
        self.policy=dict(workflows=[dict(path=PATH,jobs=[j['name'] for j in self.jobs])],
                         qualification=dict(trusted_workflows={PATH:dict(id=12,definition_sha=self.sha)}))
    def optional(self,path):return self.branch
    def api(self,path):
        if '/contents/' in path:return dict(type='file',encoding='base64',size=len(self.raw),sha=self.sha,content=base64.b64encode(self.raw).decode())
        return copy.deepcopy(next(run for run in self.runs if path.endswith('/'+str(run['id']))))
    def pages(self,path,key=None):return copy.deepcopy(self.jobs if path.endswith('/jobs') else self.runs)


class EvidenceControls(unittest.TestCase):
    def test_exact_latest_tag_run_and_all_expanded_jobs_pass(self):
        p=EvidenceProvider();result=r.full_evidence(p,p.policy,TAG,HEAD)
        self.assertEqual(result['status'],'success');self.assertEqual(result['runs'][0]['attempt'],1)

    def test_missing_running_cancelled_skipped_partial_or_duplicate_jobs_do_not_pass(self):
        for change in ('missing','running','cancelled','skipped','partial','duplicate','neutral'):
            p=EvidenceProvider()
            if change=='missing':p.runs=[]
            elif change=='running':p.run['status']='in_progress';p.run['conclusion']=None
            elif change=='cancelled':p.run['conclusion']='cancelled'
            elif change=='skipped':p.jobs[0]['conclusion']='skipped'
            elif change=='partial':p.jobs.pop()
            elif change=='duplicate':p.jobs.append(copy.deepcopy(p.jobs[0]))
            else:p.jobs[0]['conclusion']='neutral'
            with self.subTest(change=change):self.assertEqual(r.full_evidence(p,p.policy,TAG,HEAD)['status'],'pending')

    def test_newer_failed_run_wins_over_older_green_run(self):
        p=EvidenceProvider();old=copy.deepcopy(p.run);old['id']=9;p.runs.append(old)
        p.run['conclusion']='failure';p.jobs[0].update(conclusion='failure',steps=[dict(name='Run unit',conclusion='failure')])
        result=r.full_evidence(p,p.policy,TAG,HEAD)
        self.assertEqual(result['status'],'failure');self.assertEqual(result['failures'][0]['steps'],['Run unit'])

    def test_foreign_wrong_workflow_branch_and_commit_evidence_is_ignored(self):
        for key,value in (('head_branch','main'),('event','pull_request'),('head_sha',OLD),('workflow_id',99),('path','.github/workflows/other.yml'),('head_repository',{'full_name':'other/repo'})):
            p=EvidenceProvider();p.run[key]=value
            with self.subTest(key=key):self.assertEqual(r.full_evidence(p,p.policy,TAG,HEAD)['status'],'pending')

    def test_workflow_source_and_tag_trigger_are_required(self):
        for change in ('sha','trigger','filter','branch'):
            p=EvidenceProvider()
            if change=='sha':p.policy['qualification']['trusted_workflows'][PATH]['definition_sha']='0'*40
            elif change=='branch':p.branch={'ref':'refs/heads/'+TAG}
            else:
                p.raw=(b'on:\n  pull_request:\n' if change=='trigger' else b'on:\n  push:\n    tags: ["v*"]\n    paths: ["src/**"]\n')
                p.sha=hashlib.sha1(b'blob '+str(len(p.raw)).encode()+b'\0'+p.raw).hexdigest()
                p.policy['qualification']['trusted_workflows'][PATH]['definition_sha']=p.sha
            with self.subTest(change=change),self.assertRaises(ValueError):r.full_evidence(p,p.policy,TAG,HEAD)

    def test_authenticated_blob_rejects_size_digest_and_duplicate_json(self):
        p=EvidenceProvider();p.sha='0'*40
        with self.assertRaises(ValueError):r.blob(p,PATH,HEAD)
        with self.assertRaises(ValueError):r.strict_json('{"version":1,"version":2}')

    def test_old_attempt_or_head_jobs_cannot_complete_the_latest_run(self):
        for key,value in (('run_id',9),('run_attempt',2),('head_sha',OLD)):
            p=EvidenceProvider();p.jobs[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):r.full_evidence(p,p.policy,TAG,HEAD)


class WriteProvider:
    repo=REPO
    def __init__(self,value):self.value=copy.deepcopy(value);self.writes=[];self.comments=[];self.unknown=False
    def pages(self,path,key=None):return copy.deepcopy(self.comments)
    def write(self,path,method,data):
        self.writes.append((path,method,data))
        if self.unknown:raise TimeoutError('response lost')
        if path.endswith('/git/refs'):
            tag=data['ref'].removeprefix('refs/tags/');self.value['tags'][tag]=data['sha']
            return dict(ref=data['ref'],object={'sha':data['sha']})
        if path.endswith('/comments'):
            result=dict(id=70,body=data['body']);self.comments.append(result);return result
        if path.endswith('/issues'):
            issue=dict(number=3,state='open',body=data['body'],milestone={'number':7})
            self.value['issues'].append(issue);return issue
        if path.endswith('/releases'):
            self.value['publication']=dict(data,published_at='now');return self.value['publication']
        if '/milestones/' in path:
            self.value['milestone']['state']='closed';return self.value['milestone']
        raise AssertionError(path)
    def api(self,path):
        if '/git/ref/tags/' in path:return dict(object={'sha':self.value['tags'][path.rsplit('/',1)[1]]})
        if '/releases/tags/' in path:return copy.deepcopy(self.value['publication'])
        if '/issues/comments/' in path:return copy.deepcopy(next(c for c in self.comments if c['id']==int(path.rsplit('/',1)[1])))
        if '/issues/' in path:return copy.deepcopy(next(i for i in self.value['issues'] if i['number']==int(path.rsplit('/',1)[1])))
        return copy.deepcopy(self.value['milestone'])
    def optional(self,path):return self.api(path)


class MutationControls(unittest.TestCase):
    def advance(self,p,expected=None):
        with patch.object(r,'collect',side_effect=lambda *args:copy.deepcopy(p.value)):
            return r.advance(p,7,expected or s.digest(p.value))

    def test_promotion_tags_the_passing_rc_commit_after_main_advances(self):
        p=WriteProvider(StateControls().advanced())
        result=self.advance(p)
        self.assertEqual(result['tag'],FINAL)
        self.assertEqual(p.writes,[('repos/'+REPO+'/git/refs','POST',dict(ref='refs/tags/'+FINAL,sha=HEAD))])
        self.assertEqual(p.value['tags'][FINAL],HEAD)

    def test_new_rc_after_new_goal_work_still_uses_current_main(self):
        value=StateControls().advanced();value['release_commits'][HEAD]['pulls']=[]
        p=WriteProvider(value);result=self.advance(p)
        self.assertEqual(result['tag'],'v1.2.3-rc.2')
        self.assertEqual(p.writes[0][2]['sha'],OLD)

    def test_guarded_sequence_runs_full_rc_and_formal_before_publication_and_close(self):
        p=WriteProvider(snapshot());self.assertEqual(self.advance(p)['action'],'tag')
        self.assertEqual(self.advance(p)['state'],'testing');self.assertEqual(len(p.writes),1)
        p.value['evidence'][TAG]=dict(status='success',reasons=[],failures=[])
        self.assertEqual(self.advance(p)['tag'],FINAL)
        self.assertEqual(self.advance(p)['state'],'testing');self.assertEqual(len(p.writes),2)
        p.value['evidence'][FINAL]=dict(status='success',reasons=[],failures=[])
        self.assertEqual(self.advance(p)['action'],'publish')
        self.assertEqual(self.advance(p)['action'],'close')
        self.assertEqual(self.advance(p)['state'],'complete');self.assertEqual(len(p.writes),4)

    def test_stale_assessment_and_change_before_write_refuse(self):
        p=WriteProvider(snapshot())
        with self.assertRaisesRegex(ValueError,'inputs changed'):self.advance(p,'0'*64)
        altered=copy.deepcopy(p.value);altered['issues'][0]['state']='open'
        with patch.object(r,'collect',side_effect=[copy.deepcopy(p.value),altered]),self.assertRaisesRegex(ValueError,'before write'):
            r.advance(p,7,s.digest(p.value))
        self.assertEqual(p.writes,[])

    def test_unknown_write_is_not_replayed_in_the_same_invocation(self):
        p=WriteProvider(snapshot());p.unknown=True
        with self.assertRaises(TimeoutError):self.advance(p)
        self.assertEqual(len(p.writes),1)

    def test_failure_creates_a_same_milestone_backlog_without_stealing_todo(self):
        p=WriteProvider(tagged(status='failure'))
        result=self.advance(p)
        self.assertEqual(result['issue'],3)
        data=p.writes[0][2];self.assertEqual(data['milestone'],7);self.assertEqual(data['labels'],['priority:p0'])
        self.assertNotIn('todo',data['labels']);self.assertIn('## Gate',data['body'])

    def test_duplicate_failure_reuses_issue_and_does_not_relabel_active_worker(self):
        p=WriteProvider(tagged(status='failure'));self.advance(p)
        p.value['issues'][-1].update(labels=['doing'])
        assessment=s.evaluate(tagged(status='failure'))
        with patch.object(r,'collect',return_value=copy.deepcopy(p.value)):
            r.failure_issue(p,p.value,assessment,lambda:None)
            count=len(p.writes)
            r.failure_issue(p,p.value,assessment,lambda:None)
        self.assertEqual(len(p.writes),count)
        self.assertEqual(p.value['issues'][-1]['labels'],['doing'])

    def test_write_filter_refuses_pr_merge_tag_patch_and_issue_closure(self):
        p=r.Provider(REPO)
        for path,method,data in [('pulls/2/merge','PUT',{}),('git/refs/tags/v1','PATCH',{}),('issues/1','PATCH',{'state':'closed'}),('milestones/7','PATCH',{'state':'open'})]:
            with self.subTest(path=path),patch.object(p,'request',side_effect=AssertionError('no provider write')),self.assertRaises(ValueError):
                p.write('repos/'+REPO+'/'+path,method,data)


class InventoryProvider(EvidenceProvider):
    def __init__(self):
        super().__init__()
        self.value=snapshot()
        self.policy.update(lifecycle_mode='full',qualification_blockers=[])
        self.policy['qualification'].update(version=1,checks={
            name:dict(kind=kind,workflow=PATH,job=name) for name,kind in
            [('unit','unit'),('functional','functional_e2e'),('performance','performance'),('format','policy')]},
            always=['format'],protected_paths=['scripts/**'],rules=[dict(paths=['**'],require=['unit','functional','performance'],exempt={})])
        self.policy['workflows'][0]['jobs'].append('format')
        self.jobs.append(dict(id=4,name='format',status='completed',conclusion='success',run_id=10,run_attempt=1,head_sha=HEAD,
                             html_url=f'https://github.com/{REPO}/actions/runs/10/job/4',steps=[]))
        self.release_config=dict(version=1,repo=REPO,milestones={'7':self.value['contract']})
        self.head_reads=0;self.moved=False
    def api(self,path):
        if path=='repos/'+REPO:return dict(default_branch='main')
        if '/commits/' in path:
            self.head_reads+=1
            return dict(sha=OLD if self.moved and self.head_reads>1 else HEAD)
        if '/milestones/' in path:return copy.deepcopy(self.value['milestone'])
        if '/compare/' in path:return dict(behind_by=0,merge_base_commit={'sha':HEAD})
        if '/contents/' in path and ('.github/aw-release.json' in path or '.github/issue-ci-required-gates.json' in path):
            value=self.release_config if '.github/aw-release.json' in path else self.policy
            raw=json.dumps(value).encode();sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            return dict(type='file',encoding='base64',size=len(raw),sha=sha,content=base64.b64encode(raw).decode())
        return super().api(path)
    def pages(self,path,key=None):
        if '/issues?' in path:return copy.deepcopy(self.value['issues'])
        if '/pulls?' in path:return [dict(number=2,body='Closes #1',milestone={'number':7},merged_at='now',
                                        base={'ref':'main'},merge_commit_sha=HEAD)]
        if path.endswith('/tags'):return [dict(name=name,commit={'sha':sha})for name,sha in self.value['tags'].items()]
        return super().pages(path,key)


class InventoryControls(unittest.TestCase):
    def advanced(self):
        p=InventoryProvider();p.value['tags'][TAG]=HEAD
        original=p.api
        def api(path):
            if '/commits/' in path:return dict(sha=OLD)
            return original(path)
        p.api=api
        return p

    def test_tag_preflight_uses_frozen_tag_plan_after_main_advances(self):
        p=self.advanced()
        result=r.preflight(p,TAG,HEAD,7)
        expected=r.tests.select(p.policy,'all',REPO,HEAD,HEAD)
        self.assertEqual((result['head'],result['base']),(HEAD,HEAD))
        self.assertEqual(result['plan_sha256'],expected['sha256'])

    def test_formal_tag_preflight_also_uses_its_frozen_commit(self):
        p=self.advanced();p.value['tags'][FINAL]=HEAD
        result=r.preflight(p,FINAL,HEAD,7)
        self.assertEqual((result['head'],result['base'],result['tag']),(HEAD,HEAD,FINAL))

    def test_newly_landed_milestone_pr_not_in_passing_rc_requires_another_rc(self):
        p=self.advanced();p.value['issues'].append(dict(number=3,state='closed',body='Additional goal work'))
        original_pages,original_api=p.pages,p.api
        def pages(path,key=None):
            rows=original_pages(path,key)
            if '/pulls?' in path:
                rows.append(dict(number=4,body='Closes #3',merged_at='now',base={'ref':'main'},merge_commit_sha=OLD))
            return rows
        def api(path):
            if '/compare/'+OLD+'...'+HEAD in path:return dict(behind_by=1,merge_base_commit={'sha':'c'*40})
            return original_api(path)
        p.pages=pages;p.api=api
        value=r.collect(p,7)
        self.assertEqual(value['release_commits'][HEAD]['pulls'],[2])
        result=s.evaluate(value)
        self.assertEqual((result['tag'],result['release_head']),('v1.2.3-rc.2',OLD))
        with self.assertRaises(ValueError):r.preflight(p,TAG,HEAD,7)

    def test_changed_release_contract_cannot_be_satisfied_by_an_old_rc(self):
        p=self.advanced();original=p.api
        def api(path):
            if '/contents/.github/aw-release.json?ref='+HEAD in path:
                value=copy.deepcopy(p.release_config);value['milestones']['7']['acceptance'][0]['goal']='Old approval'
                raw=json.dumps(value).encode();sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
                return dict(type='file',encoding='base64',size=len(raw),sha=sha,content=base64.b64encode(raw).decode())
            return original(path)
        p.api=api
        self.assertEqual(s.evaluate(r.collect(p,7))['tag'],'v1.2.3-rc.2')
        with self.assertRaises(ValueError):r.preflight(p,TAG,HEAD,7)

    def test_rc_removed_from_main_ancestry_cannot_be_promoted(self):
        p=self.advanced();original=p.api
        def api(path):
            if '/compare/'+HEAD+'...'+OLD in path:return dict(behind_by=1,merge_base_commit={'sha':'c'*40})
            return original(path)
        p.api=api
        self.assertFalse(r.collect(p,7)['release_commits'][HEAD]['landed'])
        self.assertEqual(s.evaluate(r.collect(p,7))['state'],'work')

    def test_landed_child_pr_is_checked_even_when_its_original_base_is_a_parent(self):
        p=self.advanced();p.value['tags']={};original=p.pages;comparisons=[];api=p.api
        def pages(path,key=None):
            values=original(path,key)
            if '/pulls?' in path:values[0]['base']['ref']='parent'
            return values
        def checked(path):
            if '/compare/' in path:comparisons.append(path)
            return api(path)
        p.pages=pages;p.api=checked
        value=r.collect(p,7)
        self.assertTrue(value['pulls'][0]['_landed'])
        self.assertTrue(comparisons)
        self.assertEqual(s.evaluate(value)['state'],'ready_rc')

    def test_real_collection_binds_goal_source_full_inventory_and_landed_work(self):
        p=InventoryProvider();value=r.collect(p,7)
        self.assertEqual(value['head'],HEAD);self.assertTrue(value['pulls'][0]['_landed'])
        self.assertEqual(s.evaluate(value)['state'],'ready_rc')
        self.assertEqual(len(value['plan_sha256']),64)

    def test_tag_collection_verifies_its_exact_full_run(self):
        p=InventoryProvider();p.value['tags'][TAG]=HEAD
        value=r.collect(p,7)
        self.assertEqual(value['evidence'][TAG]['status'],'success')
        self.assertEqual(s.evaluate(value)['state'],'ready_final')

    def test_main_movement_during_collection_refuses(self):
        p=InventoryProvider();p.moved=True
        with self.assertRaisesRegex(ValueError,'default branch changed'):r.collect(p,7)

    def test_release_preflight_is_all_and_rejects_wrong_or_missing_tag(self):
        p=InventoryProvider();p.value['tags'][TAG]=HEAD;p.run.update(status='in_progress',conclusion=None)
        result=r.preflight(p,TAG,HEAD,7)
        self.assertEqual(result['mode'],'all');self.assertEqual(result['head'],result['base'])
        for tag,head in ((TAG,OLD),('v1.2.3-rc.2',HEAD),('v01.2.3',HEAD)):
            with self.subTest(tag=tag,head=head),self.assertRaises(ValueError):r.preflight(p,tag,head,7)

    def test_release_preflight_resolves_only_a_unique_reviewed_milestone(self):
        p=InventoryProvider();p.value['tags'][TAG]=HEAD
        self.assertEqual(r.preflight(p,TAG,HEAD)['milestone'],7)
        p.release_config['milestones']['8']=copy.deepcopy(p.value['contract'])
        with self.assertRaisesRegex(ValueError,'unique'):r.preflight(p,TAG,HEAD)

    def test_closed_issue_and_unlanded_dependency_pr_does_not_release(self):
        p=InventoryProvider()
        original=p.pages
        def pages(path,key=None):
            values=original(path,key)
            if '/pulls?' in path:
                values[0]['base']['ref']='dependency'
                values[0]['merge_commit_sha']=OLD
            return values
        original_api=p.api
        def api(path):
            if '/compare/' in path:return dict(behind_by=1,merge_base_commit={'sha':'c'*40})
            return original_api(path)
        with patch.object(p,'pages',side_effect=pages),patch.object(p,'api',side_effect=api):
            self.assertEqual(s.evaluate(r.collect(p,7))['state'],'work')


if __name__=='__main__':unittest.main()
