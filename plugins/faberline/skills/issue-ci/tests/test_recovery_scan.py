"""Recovery discovery is read-only and never grants close permission."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/recovery_scan.py'
spec=importlib.util.spec_from_file_location('recovery_scan_draft',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SOURCE='a'*40;EVALUATOR='b'*40


class Api:
    def __init__(self):
        self.row=dict(id=123,run_attempt=2,status='completed',head_branch='main',
                      head_repository=dict(full_name='o/r'),event='repository_dispatch',head_sha=SOURCE)
        self.diverged=False
    def api(self,path,method='GET',data=None):
        assert method=='GET' and data is None
        if path=='repos/o/r':return dict(full_name='o/r',default_branch='main')
        if path=='repos/o/r/actions/runs/123/attempts/2':return self.row
        if path==f'repos/o/r/compare/{SOURCE}...main':
            return dict(status='diverged' if self.diverged else 'ahead',base_commit=dict(sha=SOURCE))
        raise AssertionError(path)


class RecoveryScanTests(unittest.TestCase):
    def test_historical_policy_comes_from_completed_default_branch_attempt(self):
        api=Api();raw=json.dumps(dict(aw_revision=EVALUATOR))
        with patch.object(m.review,'blob',return_value=raw) as blob:
            doc,evaluator=m.historical_document(api,'o/r',123,2)
            self.assertEqual((doc['source_commit'],evaluator),(SOURCE,EVALUATOR))
            self.assertEqual(blob.call_args.args[3],SOURCE)
            api.row['event']='pull_request_target'
            with self.assertRaisesRegex(RuntimeError,'recovery_source'):m.historical_document(api,'o/r',123,2)
            api.row['event']='repository_dispatch';api.row['status']='in_progress'
            with self.assertRaisesRegex(RuntimeError,'recovery_source'):m.historical_document(api,'o/r',123,2)
            api.row['status']='completed';api.diverged=True
            with self.assertRaisesRegex(RuntimeError,'recovery_source'):m.historical_document(api,'o/r',123,2)

    def test_scan_keeps_other_items_when_one_source_is_missing(self):
        items=[dict(issue=1,state='worker_held'),
               dict(issue=2,state='merge_held',run=123,attempt=2,head='c'*40,owner='a'*56),
               dict(issue=3,state='merge_held',run=124,attempt=1,head='d'*40,owner='b'*56)]
        def source(api,repo,run,attempt):
            if run==123:raise RuntimeError('recovery_source')
            return dict(source_commit=SOURCE),EVALUATOR
        evidence=dict(payload=dict(source_attempt=1,checkpoint=dict(issue=3)))
        with patch.object(m.discovery,'inspect',return_value=items), \
             patch.object(m,'historical_document',side_effect=source), \
             patch.object(m.guard,'identify_native_owner',return_value=dict(payload=dict(pr=9))), \
             patch.object(m.closer,'authenticate_checkpoint',return_value=evidence), \
             patch.object(m.observer,'source_started_at',return_value='2026-01-01T00:00:00Z'), \
             patch.object(m.observer,'recovery_plan',return_value=dict(state='guard_required')):
            results=m.scan(None,'o/r',{},lambda repo,job:'')
        self.assertEqual([r['state'] for r in results],['worker_held','held','guard_required'])
        self.assertEqual(results[1]['reason'],'recovery_source')
        self.assertFalse(results[1]['close_authorized']);self.assertFalse(results[2]['close_authorized'])


if __name__=='__main__':unittest.main()
