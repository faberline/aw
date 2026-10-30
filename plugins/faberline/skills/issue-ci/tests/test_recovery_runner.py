"""The unwired recovery coordinator reauthenticates each candidate."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/recovery_runner.py'
spec=importlib.util.spec_from_file_location('recovery_runner_draft',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class RunnerTests(unittest.TestCase):
    def test_applied_merged_outcome_is_reauthenticated_before_guard_release(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,
                       kind='merged_outcome',state='merged_outcome_applied')
        calls=[]
        merged=SimpleNamespace(release_applied=lambda *args:(calls.append(args) or dict(state='todo_guard_released')))
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]), \
             patch.object(m.scan,'sibling',return_value=merged):
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'todo_guard_released')
        self.assertEqual(len(calls),1)
    def test_refuses_missing_guard_policy_before_scan(self):
        with patch.object(m.worker,'read_policy',return_value=None),patch.object(m.scan,'scan') as scan:
            with self.assertRaisesRegex(RuntimeError,'merge_guard_policy_missing'):
                m.reconcile(None,'o/r',None)
        scan.assert_not_called()

    def test_candidates_are_reauthenticated_and_one_failure_does_not_stop_others(self):
        candidates=[dict(issue=1,state='worker_held'),
                    dict(issue=2,pr=7,head='a'*40,run=21,attempt=1,state='guard_required'),
                    dict(issue=3,pr=8,head='b'*40,run=22,attempt=2,state='observed_closed')]
        calls=[]
        def source(api,repo,run,attempt):
            calls.append(('source',run,attempt));return dict(source_commit='c'*40),'d'*40
        def reconcile(api,repo,document,policy,evaluator,run,attempt,pr,head,reader):
            calls.append(('close',run,attempt,pr,head))
            if run==21:raise RuntimeError('merge_guard_not_owner')
            return dict(state='closed',issue=3,merge='e'*40)
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=candidates), \
             patch.object(m.scan,'historical_document',side_effect=source), \
             patch.object(m.close,'reconcile_native',side_effect=reconcile):
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual([x['state'] for x in result],['worker_held','held','closed'])
        self.assertEqual(result[1]['reason'],'merge_guard_not_owner')
        self.assertEqual(calls,[('source',21,1),('close',21,1,7,'a'*40),
                                ('source',22,2),('close',22,2,8,'b'*40)])

    def test_prepared_observation_cannot_bypass_successful_checkpoint(self):
        candidate=dict(issue=7,pr=9,head='a'*40,run=123,attempt=2,
                       state='guard_required',status='observed_success',close_authorized=False)
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]), \
             patch.object(m.scan,'historical_document',return_value=({'source_commit':'c'*40},'d'*40)), \
             patch.object(m.close,'reconcile_native',side_effect=RuntimeError('merge_guard_source_not_success')):
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'held')
        self.assertEqual(result[0]['reason'],'merge_guard_source_not_success')

    def test_prepared_candidate_uses_independent_revalidation_path(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,
                       state='prepared_revalidation',status='observed_success',close_authorized=False)
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]), \
             patch.object(m.scan,'historical_document',return_value=({'source_commit':'c'*40},'d'*40)), \
             patch.object(m.close,'reconcile_native_prepared',return_value=dict(state='closed',issue=7)) as recover, \
             patch.object(m.close,'reconcile_native') as old:
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'closed');recover.assert_called_once();old.assert_not_called()

    def test_abandoned_admission_is_reauthenticated_before_safe_settle(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,state='admission_recovery')
        intent=dict(payload=dict(pr=9,issue=7,head='a'*40))
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]), \
             patch.object(m.scan,'historical_document',return_value=({'source_commit':'c'*40},'d'*40)), \
             patch.object(m.scan.guard,'identify_native_owner',return_value=intent) as identify, \
             patch.object(m.admission,'cancel_blocked',return_value=dict(state='released',issue=7)) as settle, \
             patch.object(m.close,'reconcile_native') as close:
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'released');identify.assert_called_once();settle.assert_called_once()
        close.assert_not_called()

    def test_already_closed_prepared_candidate_reauthenticates_before_release(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,
                       state='closed_prepared_recovery',close_authorized=False)
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]), \
             patch.object(m.scan,'historical_document',return_value=({'source_commit':'c'*40},'d'*40)), \
             patch.object(m.close,'release_closed_prepared',return_value=dict(
                 state='released_after_observed_closed',issue=7,merge='e'*40)) as release, \
             patch.object(m.close,'reconcile_native') as close:
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'released_after_observed_closed')
        release.assert_called_once();close.assert_not_called()

    def test_adoption_candidate_is_rediscovered_before_guarded_close(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,
                       kind='adoption',state='guard_required')
        adoption=type('Adoption',(),{'discover_source':lambda this,*args:dict(
            document={'source_commit':'c'*40},evaluator='d'*40,intent={})})()
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]),patch.object(m.scan,'adoption_module',return_value=adoption), \
             patch.object(m.close,'reconcile_adoption',return_value=dict(state='closed',issue=7,merge='e'*40)) as close:
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'closed');close.assert_called_once()

    def test_closed_prepared_adoption_reauthenticates_without_closing(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,
                       kind='adoption',state='closed_prepared_recovery')
        adoption=type('Adoption',(),{'discover_source':lambda this,*args:dict(
            document={'source_commit':'c'*40},evaluator='d'*40,intent={})})()
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]),patch.object(m.scan,'adoption_module',return_value=adoption), \
             patch.object(m.close,'release_adoption_closed_prepared',return_value=dict(
                 state='released_after_observed_closed',issue=7,merge='e'*40)) as release, \
             patch.object(m.close,'reconcile_adoption') as close:
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'released_after_observed_closed')
        release.assert_called_once();close.assert_not_called()

    def test_prepared_adoption_uses_independent_revalidation_path(self):
        candidate=dict(issue=7,pr=9,head='a'*40,owner='b'*56,run=123,attempt=2,
                       kind='adoption',state='adoption_prepared_revalidation')
        adoption=type('Adoption',(),{'discover_source':lambda this,*args:dict(
            document={'source_commit':'c'*40},evaluator='d'*40,intent={})})()
        with patch.object(m.worker,'read_policy',return_value={'current':True}), \
             patch.object(m.scan,'scan',return_value=[candidate]),patch.object(m.scan,'adoption_module',return_value=adoption), \
             patch.object(m.close,'reconcile_adoption_prepared',return_value=dict(state='closed',issue=7)) as recover:
            result=m.reconcile(None,'o/r',lambda r,j:'')
        self.assertEqual(result[0]['state'],'closed');recover.assert_called_once()


if __name__=='__main__':unittest.main()
