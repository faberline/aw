"""Activation requires authenticated evidence, not an inventory's empty flag."""
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('rollout_activation',Path(__file__).parents[1]/'scripts/rollout_activation.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
AW=m.AW;REPO='faberline/core';RUNTIME='a'*40;DATA='b'*40;AW_MAIN='c'*40;HEAD='d'*40;DRAIN='e'*40
NONCE='9'*32;TREE='2'*40;RUNTIME_TREE='4'*40


def blob(raw):return hashlib.sha1(b'blob '+str(len(raw.encode())).encode()+b'\0'+raw.encode()).hexdigest()


class Provider:
    def __init__(self):
        self.manifest=dict(version=1,actors=[dict(id='worker',role='worker'),dict(id='writer',role='writer'),dict(id='recovery',role='recovery')])
        self.manifest_raw=json.dumps(self.manifest)
        self.producer=dict(path='.github/workflows/issue-ci.yml',id=11,definition_sha='1'*40,job='writer')
        self.recovery=dict(path='.github/workflows/issue-recovery.yml',id=12,definition_sha='2'*40,job='recovery')
        self.guard=dict(version=1,enabled=True,genesis='f'*40,ruleset_id=99,runtime={k:str(i)*64 for i,k in enumerate(('worker','provider','ledger'),1)})
        self.config=dict(aw_revision=RUNTIME,issue_guard=self.guard,native_auto_merge=dict(producer=self.producer),
                         issue_recovery=dict(version=2,enabled=True,producer=self.recovery),
                         issue_guard_rollout=dict(version=1,source=DATA,epoch=NONCE))
        self.document=dict(source_commit=HEAD,config=self.config)
        self.old=dict(aw_revision=RUNTIME,native_auto_merge=dict(producer=self.producer),
                      issue_recovery=dict(version=2,enabled=False,producer=self.recovery),
                      issue_guard_cutover=dict(version=1,phase='drain',nonce=NONCE))
        self.drain=dict(version=1,repo=REPO,producer_run=401,results=[dict(pr=85,head=HEAD,state='withdrawn')])
        entry='guarded entry';runtime=dict(self.guard['runtime'],entry=hashlib.sha256(entry.encode()).hexdigest())
        self.record=dict(version=1,repo=REPO,epoch=NONCE,state='authorized',actor_manifest=blob(self.manifest_raw),
                         policy_sha256=m.policy_digest(REPO,self.config,blob(self.manifest_raw)),
                         actors=[dict(id=a['id'],epoch=NONCE,manifest=blob(self.manifest_raw),runtime=copy.deepcopy(runtime),
                                      audit_sha256='1'*64,inflight=[],leases=[],barrier_source=DRAIN,observed_at='2026-10-30T10:01:00Z')for a in self.manifest['actors']],
                         drain=dict(source=DRAIN,run=401,attempt=1,record_sha256=m.digest(self.drain)),
                         conformance=dict(run=502,attempt=1),authorization=dict(pr=77))
        self.groups=json.loads((Path(m.__file__).with_name('rollout-conformance.json')).read_text())
        self.latest_attempt=1;self.active=False;self.revoked=False;self.unblocked=False;self.request=False
        self.bad_tree=False;self.bad_workflow=False;self.writes=[];self.calls=[];self.omitted_case=None

    def raw(self):return json.dumps(dict(version=1,rollouts=[self.record]))

    def api(self,path,**kwargs):
        self.calls.append(path)
        if kwargs:self.writes.append((path,kwargs));raise AssertionError('verification cannot write')
        if path in (f'repos/{AW}',f'repos/{REPO}'):
            return dict(full_name=path[6:],default_branch='main')
        if '/compare/'in path:
            before=path.split('/compare/')[1].split('...')[0]
            return dict(status='ahead',base_commit={'sha':before})
        if path==f'repos/{AW}/commits/main':return dict(sha=AW_MAIN)
        if path==f'repos/{REPO}/commits/main':return dict(sha=HEAD if self.active else DRAIN)
        if path==f'repos/{AW}/pulls/77':
            return dict(number=77,state='closed',merged=True,merge_commit_sha=DATA,changed_files=1,
                        head=dict(sha='1'*40,repo={'full_name':AW}),base=dict(ref='main',repo={'full_name':AW}))
        if path==f'repos/{AW}/actions/workflows/native-auto-merge-controls.yml':return dict(id=9,path=m.CONTROLS)
        if '/git/commits/'in path:
            reference=path.split('/git/commits/')[1]
            return dict(tree=dict(sha=RUNTIME_TREE if reference==RUNTIME else TREE))
        if path.startswith(f'repos/{AW}/actions/runs/'):
            parts=path.split('/');run=int(parts[5]);attempt=int(parts[7])if len(parts)>7 else self.latest_attempt
            return dict(id=run,run_number=run,run_attempt=attempt,workflow_id=9,path=m.CONTROLS,event='pull_request',status='completed',conclusion='success',
                        repository={'full_name':AW},head_repository={'full_name':AW},head_sha='1'*40 if run==501 else'3'*40,run_started_at='2026-10-30T10:02:00Z')
        if path==f'repos/{REPO}/actions/jobs/402':return dict(id=402,status='completed',conclusion='success',completed_at='2026-10-30T10:00:00Z')
        if path.startswith(f'repos/{REPO}/actions/workflows/'):
            i=int(path.rsplit('/',1)[1]);source=self.producer if i==11 else self.recovery
            return dict(id=i,path=source['path'],state='disabled_manually'if self.bad_workflow else 'active')
        raise AssertionError(path)

    def pages(self,path,key=None):
        if path==f'repos/{AW}/pulls/77/files':return [dict(filename=m.DATA,status='modified',sha=blob(self.raw()))]
        if path.startswith(f'repos/{AW}/actions/workflows/native-auto-merge-controls.yml/runs?'):
            return [self.api(f'repos/{AW}/actions/runs/501')]
        if path.startswith(f'repos/{AW}/actions/runs/'):
            run=int(path.split('/')[5]);return [dict(id=601 if run==501 else 602,name='complete isolated native merge controls',status='completed',conclusion='success')]
        if path.startswith(f'repos/{REPO}/pulls?'):return [dict(number=85,head={'sha':HEAD})]
        if path.endswith('/statuses'):return [dict(context='claude/verified-merge',state='success'if self.unblocked else'pending',creator={'id':41898282,'type':'Bot'})]
        raise AssertionError(path)

    def source(self,api,repo,path,ref,expected=None):
        if repo==AW and path==m.DATA:
            if ref==AW_MAIN and self.revoked:
                changed=copy.deepcopy(self.record);changed['state']='revoked';return json.dumps(dict(version=1,rollouts=[changed]))
            raw=self.raw()
        elif repo==AW and path==m.CONTROLS:raw='the fixed complete control workflow'
        elif repo==AW and path==m.CASES:raw=json.dumps(self.groups)
        elif repo==AW and path==m.ENTRY:raw='guarded entry'
        elif repo==REPO and path==m.MANIFEST:raw=self.manifest_raw
        elif repo==REPO and path=='.github/issue-ci-required-gates.json':raw=json.dumps(self.config if ref==HEAD else self.old)
        else:raise AssertionError((repo,path,ref))
        if expected is not None and blob(raw)!=expected:raise RuntimeError('blob mismatch')
        return raw

    def log(self,repo,job):
        if repo==REPO and job==402:return 'AW_NATIVE_MERGE_CUTOVER_V1 '+json.dumps(self.drain)
        tree=TREE if job==601 else RUNTIME_TREE
        if self.bad_tree:tree='0'*40
        result='AW_CONTROL_SOURCE_V1 '+json.dumps(dict(version=1,repo=AW,commit='7'*40,tree=tree))+'\n'
        if job==602:
            for names in self.groups.values():
                for name in names:
                    if name!=self.omitted_case:result+=f'{name.rsplit(".",1)[1]} ({name}) ... ok\n'
        return result

    def provider_pull(self,*args):return dict(state='OPEN',headRefOid=HEAD,autoMergeRequest={}if self.request else None)


def context(provider):
    guard=SimpleNamespace(historical_runtime=lambda *a:True,worker=SimpleNamespace(check_runtime=lambda *a:True),
                          provider=SimpleNamespace(verified_genesis=lambda *a:True,provider_rules=lambda *a:True))
    recovery=SimpleNamespace(policy=lambda doc:doc['config']['issue_recovery'],workflow_contract=lambda *a:provider.recovery)
    original=m.sibling
    def sibling(name):
        if name=='merge_guard':return guard
        if name=='recovery_entry':return recovery
        return original(name)
    from contextlib import ExitStack
    stack=ExitStack()
    stack.enter_context(patch.object(m.review,'blob',side_effect=provider.source))
    stack.enter_context(patch.object(m.review.r,'job_log',side_effect=provider.log))
    stack.enter_context(patch.object(m.review,'producer_identity',return_value=dict(head=DRAIN,run_attempt=1,job_id=402)))
    stack.enter_context(patch.object(m.native,'writer_contract'))
    stack.enter_context(patch.object(m.native,'policy',return_value=dict(producer=provider.producer)))
    stack.enter_context(patch.object(m.native,'provider_pull',side_effect=provider.provider_pull))
    stack.enter_context(patch.object(m,'sibling',side_effect=sibling))
    return stack


class ControlRunTests(unittest.TestCase):
    def test_changed_tested_tree_and_superseded_attempt_refuse(self):
        for field in ('bad_tree','latest_attempt'):
            p=Provider();setattr(p,field,True if field=='bad_tree'else 2)
            with self.subTest(field=field),context(p),self.assertRaises(RuntimeError):m.control_run(p,RUNTIME,502,1)


class ConformanceRosterTests(unittest.TestCase):
    def test_every_required_control_id_exists_in_the_executable_complete_suite(self):
        groups=json.loads(Path(m.__file__).with_name('rollout-conformance.json').read_text())
        self.assertEqual(set(groups),m.GROUPS)
        suite=unittest.TestLoader().discover(str(Path(__file__).parent))
        def ids(suite):
            for item in suite:
                if isinstance(item,unittest.TestSuite):yield from ids(item)
                else:yield item.id()
        available=set(ids(suite))
        for group,names in groups.items():
            self.assertTrue(names,group)
            self.assertEqual(len(names),len(set(names)))
            self.assertTrue(set(names)<=available,group+': '+str(set(names)-available))

    def test_committed_registry_is_explicit_and_cannot_authorize_a_draft(self):
        root=Path(__file__).resolve().parents[5]
        self.assertIsInstance(m.registry((root/m.DATA).read_text()),list)
        p=Provider();p.record['state']='draft'
        with self.assertRaises(RuntimeError):m.selected(p.raw(),REPO,NONCE)


class RolloutActivationTests(unittest.TestCase):
    def test_complete_authorized_activation_and_active_runtime_are_read_only(self):
        for active in (False,True):
            p=Provider();p.active=active
            with self.subTest(active=active),context(p):proof=m.verify(p,REPO,p.document)
            self.assertEqual(proof['actor_count'],3);self.assertEqual(proof['epoch'],NONCE);self.assertEqual(p.writes,[])

    def test_missing_proof_draft_duplicate_or_revoked_authorization_refuses(self):
        for kind in ('missing','draft','revoked','duplicate'):
            p=Provider()
            if kind=='missing':p.config.pop('issue_guard_rollout')
            if kind=='draft':p.record['state']='draft'
            if kind=='revoked':p.revoked=True
            with self.subTest(kind=kind),context(p):
                if kind=='duplicate':
                    raw=json.dumps(dict(version=1,rollouts=[p.record,p.record]))
                    with self.assertRaises(RuntimeError):m.registry(raw)
                else:
                    with self.assertRaises(RuntimeError):m.verify(p,REPO,p.document)
            self.assertEqual(p.writes,[])

    def test_actor_runtime_lease_inflight_epoch_and_observation_window_refuse(self):
        edits=[lambda r:r['runtime'].update(entry='0'*64),lambda r:r.update(leases=[dict(issue=84)]),
               lambda r:r.update(inflight=['old-worker']),lambda r:r.update(epoch='0'*32),
               lambda r:r.update(barrier_source='0'*40),lambda r:r.update(observed_at='2026-10-30T09:59:59Z'),
               lambda r:r.update(observed_at='2026-10-30T10:02:01Z')]
        for edit in edits:
            p=Provider();edit(p.record['actors'][0])
            with self.subTest(edit=edit),context(p),self.assertRaises(RuntimeError):m.verify(p,REPO,p.document)
            self.assertEqual(p.writes,[])

    def test_actor_missing_duplicate_and_changed_policy_refuse(self):
        for kind in ('missing','duplicate','policy'):
            p=Provider()
            if kind=='missing':p.record['actors'].pop()
            if kind=='duplicate':p.record['actors'][1]=copy.deepcopy(p.record['actors'][0])
            if kind=='policy':p.config['issue_guard']['ruleset_id']=100
            with self.subTest(kind=kind),context(p),self.assertRaises(RuntimeError):m.verify(p,REPO,p.document)

    def test_inactive_workflow_passing_status_and_foreign_queue_request_refuse(self):
        for field in ('bad_workflow','unblocked','request'):
            p=Provider();setattr(p,field,True)
            with self.subTest(field=field),context(p),self.assertRaises(RuntimeError):m.verify(p,REPO,p.document)

    def test_drain_epoch_unknown_result_and_missing_conformance_refuse(self):
        for kind in ('epoch','held','conformance'):
            p=Provider()
            if kind=='epoch':p.old['issue_guard_cutover']['nonce']='0'*32
            if kind=='held':p.drain['results'][0]['state']='held'
            if kind=='conformance':p.omitted_case=p.groups['close_retry'][0]
            with self.subTest(kind=kind),context(p),self.assertRaises(RuntimeError):m.verify(p,REPO,p.document)
