"""Trusted recovery wake controls. No workflow is deployed by these tests."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

m=load('recovery_entry_draft',HERE.parent/'scripts/recovery_entry.py')
workers=load('recovery_entry_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
SOURCE='a'*40;EVALUATOR='b'*40;WORKFLOW='c'*40


class EntryTests(unittest.TestCase):
    def setUp(self):
        guard=workers.policy(SOURCE);del guard['policy_source']
        self.spec=dict(path='.github/workflows/issue-recovery.yml',id=11,definition_sha=WORKFLOW,job='recovery')
        self.value=dict(version=1,enabled=True,cron='13,43 * * * *',producer=self.spec)
        self.document=dict(source_commit=SOURCE,config=dict(issue_guard=guard,issue_recovery=self.value))
        self.raw=(HERE.parent/'templates/issue-recovery.yml.example').read_text().replace(
            'REPLACE_WITH_REVIEWED_FULL_SHA',EVALUATOR)

    def test_policy_preserves_existing_qualification_and_refuses_unrecognized_guard(self):
        with patch.object(m.native,'policy') as prior:
            self.assertEqual(m.policy(self.document),self.value)
            prior.assert_called_once_with(self.document)
            bad=copy.deepcopy(self.document);bad['config']['issue_recovery']['cron']='* * * * *'
            with self.assertRaisesRegex(RuntimeError,'recovery_policy'):m.policy(bad)
            bad=copy.deepcopy(self.document);bad['config']['issue_guard']['enabled']=False
            with self.assertRaises(RuntimeError):m.policy(bad)

    def test_exact_workflow_has_one_pinned_writer_and_shared_mutex(self):
        with patch.object(m.native,'policy'),patch.object(m.review,'blob',return_value=self.raw):
            self.assertEqual(m.workflow_contract(None,'o/r',self.document,EVALUATOR),self.spec)
        for changed in (self.raw.replace('cancel-in-progress: false','cancel-in-progress: true'),
                        self.raw.replace('issues: write','issues: read'),
                        self.raw.replace('13,43 * * * *','* * * * *'),
                        self.raw.replace(EVALUATOR,'0'*40)):
            with self.subTest(changed=changed),patch.object(m.native,'policy'), \
                 patch.object(m.review,'blob',return_value=changed),self.assertRaises(RuntimeError):
                m.workflow_contract(None,'o/r',self.document,EVALUATOR)

    def test_event_and_exact_running_attempt_are_verified(self):
        identity=dict(head=SOURCE,run_attempt=2,job_id=100)
        class Api:
            def __init__(self):self.status='in_progress';self.job='in_progress';self.event='repository_dispatch'
            def api(self,path):
                assert path=='repos/o/r/actions/runs/50/attempts/2'
                return dict(id=50,run_attempt=2,status=self.status,event=self.event)
            def pages(self,path,key):
                assert path=='repos/o/r/actions/runs/50/attempts/2/jobs' and key=='jobs'
                return [dict(id=100,name='recovery',status=self.job)]
        api=Api();payload=dict(action=m.DISPATCH,repository=dict(full_name='o/r'))
        with patch.object(m,'policy',return_value=self.value), \
             patch.object(m,'workflow_contract',return_value=self.spec), \
             patch.object(m.review,'producer_identity',return_value=identity):
            self.assertEqual(m.validate_wake(api,'o/r',self.document,EVALUATOR,50,'repository_dispatch',payload),identity)
            api.job='completed'
            with self.assertRaisesRegex(RuntimeError,'recovery_wake_identity'):
                m.validate_wake(api,'o/r',self.document,EVALUATOR,50,'repository_dispatch',payload)
            api.job='in_progress';payload['repository']['full_name']='other/repo'
            with self.assertRaisesRegex(RuntimeError,'recovery_wake_identity'):
                m.validate_wake(api,'o/r',self.document,EVALUATOR,50,'repository_dispatch',payload)
            payload['repository']['full_name']='o/r';identity['head']='0'*40
            with self.assertRaisesRegex(RuntimeError,'recovery_wake_identity'):
                m.validate_wake(api,'o/r',self.document,EVALUATOR,50,'repository_dispatch',payload)
            identity['head']=SOURCE;api.event='schedule'
            self.assertEqual(m.validate_wake(api,'o/r',self.document,EVALUATOR,50,'schedule',
                                             dict(schedule='13,43 * * * *')),identity)
            with self.assertRaisesRegex(RuntimeError,'recovery_wake_identity'):
                m.validate_wake(api,'o/r',self.document,EVALUATOR,50,'schedule',dict(schedule='* * * * *'))

    def test_entry_refuses_wrong_source_before_calling_recovery(self):
        class Api:
            def api(self,path):return dict(full_name='o/r',default_branch='main')
            def strict_json_loads(self,raw):return json.loads(raw)
        with tempfile.TemporaryDirectory() as directory:
            event=Path(directory)/'event.json';event.write_text(json.dumps(dict(action=m.DISPATCH,repository=dict(full_name='o/r'))))
            env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='o/r',AW_ACTION_REF=EVALUATOR,GITHUB_RUN_ID='50',
                     GITHUB_EVENT_NAME='repository_dispatch',GITHUB_EVENT_PATH=str(event),GITHUB_WORKFLOW_SHA=SOURCE,
                     GITHUB_WORKFLOW_REF='o/r/.github/workflows/issue-recovery.yml@refs/heads/main',
                     GITHUB_REF='refs/heads/main')
            with patch.dict(os.environ,env,clear=True),patch.object(m.review,'ci',Api()), \
                 patch.object(m.review.action,'trusted_policy',return_value=self.document), \
                 patch.object(m,'validate_wake'),patch.object(m.runner,'reconcile',return_value=[] ) as runner, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'recovery_rollout_not_verified'):m.main()
                runner.assert_not_called()
                os.environ['GITHUB_WORKFLOW_SHA']='0'*40
                with self.assertRaisesRegex(RuntimeError,'recovery_wake_identity'):m.main()
                runner.assert_not_called()


if __name__=='__main__':unittest.main()
