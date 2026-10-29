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


if __name__=='__main__':unittest.main()
