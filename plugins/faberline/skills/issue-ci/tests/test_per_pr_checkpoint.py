"""Per-PR checkpoint controls; status data is synthetic and read-only."""
import copy
import importlib.util
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

m=load('per_pr_checkpoint_draft',HERE.parent/'scripts/per_pr_checkpoint.py')
prepared=load('per_pr_checkpoint_prepared',HERE.parent/'scripts/prepared_status.py')
obs_fixture=load('per_pr_observation_fixture',HERE/'test_post_merge_observation.py')
workers=load('per_pr_worker_fixture',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')


class PerPRTests(unittest.TestCase):
    def setUp(self):
        self.source='d'*40;self.policy=workers.policy(self.source)
        values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=m.guard.build('o/r',9,'a'*40,7,self.policy,123,2,lambda n:next(values))
        checkpoint=copy.deepcopy(obs_fixture.Fixture().checkpoint);checkpoint['repo']='o/r'
        self.evidence=m.observation.record(checkpoint,123,2,self.source,'e'*40,202,'f'*64)
        self.status=dict(id=55,state='success',context='claude/verified-merge',
            target_url='https://github.com/o/r/actions/runs/202',
            creator=dict(id=m.BOT_ID,type='Bot',login='github-actions[bot]'))

    def test_acknowledged_status_is_bound_to_guard_and_exact_pr(self):
        value=m.build(self.evidence,self.policy,self.intent,self.status)
        line=m.MARKER+m.canonical(value)+'\n'
        self.assertEqual(m.find(line,'o/r',9,'a'*40),value)
        with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_duplicate'):m.find(line+line,'o/r',9,'a'*40)
        for change in (lambda s:s.update(id=True),lambda s:s.update(state='pending'),
                       lambda s:s.update(target_url='https://github.com/o/r/actions/runs/203'),
                       lambda s:s['creator'].update(id=1)):
            bad=copy.deepcopy(self.status);change(bad)
            with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_identity'):
                m.build(self.evidence,self.policy,self.intent,bad)

    def test_provider_status_and_same_guard_are_required(self):
        value=m.build(self.evidence,self.policy,self.intent,self.status)
        class Api:
            def __init__(self,statuses):self.statuses=statuses
            def pages(self,path):
                assert path=='repos/o/r/commits/'+('a'*40)+'/statuses'
                return self.statuses
        self.assertEqual(m.verify_status(Api([self.status]),'o/r','a'*40,value,self.intent),self.evidence)
        for statuses in ([],[dict(self.status,id=56)],[dict(self.status,state='failure')],
                         [self.status,self.status]):
            with self.assertRaisesRegex(RuntimeError,'per_pr_status_unknown'):
                m.verify_status(Api(statuses),'o/r','a'*40,value,self.intent)
        other=copy.deepcopy(self.intent);other['payload']['owner']='f'*56
        with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_identity'):
            m.verify_status(Api([self.status]),'o/r','a'*40,value,other)

    def test_newer_matching_status_never_authorizes_old_success(self):
        value=m.build(self.evidence,self.policy,self.intent,self.status)
        class Api:
            def __init__(self,statuses):self.statuses=statuses
            def pages(self,path):return self.statuses
        for state in ('pending','failure','success'):
            with self.subTest(state=state):
                newer=dict(self.status,id=1,state=state)
                with self.assertRaisesRegex(RuntimeError,'per_pr_status_superseded'):
                    m.verify_status(Api([newer,self.status]),'o/r','a'*40,value,self.intent)
        unrelated=dict(self.status,id=1,context='unrelated',state='failure')
        self.assertEqual(m.verify_status(Api([unrelated,self.status]),'o/r','a'*40,value,self.intent),self.evidence)

    def test_unknown_status_list_cannot_authorize_issue_closure(self):
        value=m.build(self.evidence,self.policy,self.intent,self.status)
        class Api:
            def __init__(self,statuses):self.statuses=statuses
            def pages(self,path):return self.statuses
        for statuses in ([None,self.status],[{},self.status],[dict(self.status,id=True),self.status],
                         [dict(self.status,id=1),dict(self.status,id=1),self.status]):
            with self.subTest(statuses=statuses),self.assertRaisesRegex(RuntimeError,'per_pr_status_unknown'):
                m.verify_status(Api(statuses),'o/r','a'*40,value,self.intent)

    def test_reviewed_adoption_binds_distinct_publisher_and_policy_sources(self):
        checkpoint=copy.deepcopy(self.evidence['payload']['checkpoint']);checkpoint['base']=self.source
        grant={key:checkpoint[key] for key in ('repo','pr','issue','head','base','base_ref','issue_sha256','pr_sha256')}
        grant.update(publisher=dict(source_commit='b'*40),review=dict(run_id=202,receipt_sha256='f'*64))
        grant_source='c'*40
        evidence=m.observation.record(checkpoint,123,2,'b'*40,'e'*40,202,'f'*64,m.review.r.digest(grant),grant_source)
        value=m.build(evidence,self.policy,self.intent,self.status,grant=grant,grant_source=grant_source)
        nonce='1'*32;pending=dict(self.status,id=54,state='pending',
            target_url=prepared.target_identity('o/r',202,123,2,nonce))
        early=prepared.build_guarded(evidence,self.policy,self.intent,pending,nonce,grant=grant,grant_source=grant_source)
        self.assertEqual(early['payload']['pending_id'],54)
        class Api:
            def pages(self,path):return [self_status]
        self_status=self.status
        self.assertEqual(m.verify_status(Api(),'o/r','a'*40,value,self.intent,grant=grant,grant_source=grant_source),evidence)
        with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_identity'):
            m.verify_status(Api(),'o/r','a'*40,value,self.intent)
        changed=copy.deepcopy(grant);changed['base']='0'*40
        with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_identity'):
            m.verify_status(Api(),'o/r','a'*40,value,self.intent,grant=changed,grant_source=grant_source)


if __name__=='__main__':unittest.main()
