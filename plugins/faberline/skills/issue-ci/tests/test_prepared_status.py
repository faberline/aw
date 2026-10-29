"""Read-only controls; status observations are never close authorization."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

m=load('prepared_status_draft',HERE.parent/'scripts/prepared_status.py')
fixture=load('prepared_status_fixture',HERE/'test_post_merge_observation.py')


class PreparedStatusTests(unittest.TestCase):
    def setUp(self):
        c=fixture.Fixture().checkpoint
        self.record=m.observation.record(c,101,2,'d'*40,'e'*40,202,'f'*64)
        self.prepared=m.build(self.record,10,'1'*32)
        self.url=m.target(self.prepared)
        def status(identifier,state):
            return dict(id=identifier,state=state,context='claude/verified-merge',target_url=self.url,
                        creator=dict(id=m.BOT_ID,type='Bot',login='github-actions[bot]'))
        self.pending=status(10,'pending');self.success=status(11,'success')

    def test_matching_success_is_observation_only(self):
        result=m.observe(self.prepared,[self.success,self.pending])
        self.assertEqual(result['status_id'],11)
        self.assertFalse(result['close_authorized'])
        self.assertIn('101-2-',result['target'])
        self.assertEqual(m.target_for(self.record,'1'*32),self.url)

    def test_prepared_record_without_new_success_never_passes(self):
        for statuses in ([self.pending],[],[self.success],
                         [self.pending,dict(self.success,id=9)],
                         [dict(self.pending,id=12),self.success,self.pending],
                         [dict(self.pending,id=12,context='Claude/Verified-Merge'),self.success,self.pending]):
            with self.subTest(statuses=statuses),self.assertRaises(RuntimeError):
                m.observe(self.prepared,statuses)

    def test_foreign_stale_tampered_or_ambiguous_evidence_holds(self):
        for change in (lambda s:s.update(target_url=s['target_url']+'x'),
                       lambda s:s['creator'].update(id=1),
                       lambda s:s['creator'].update(type='User'),
                       lambda s:s.update(state='failure')):
            bad=copy.deepcopy(self.success);change(bad)
            with self.assertRaises(RuntimeError):m.observe(self.prepared,[bad,self.pending])
        with self.assertRaises(RuntimeError):m.observe(self.prepared,[self.pending,self.success,self.success])
        bad=copy.deepcopy(self.prepared);bad['payload']['pending_id']=9
        with self.assertRaises(RuntimeError):m.observe(bad,[self.pending,self.success])

    def test_provider_order_not_numeric_id_decides_which_status_is_latest(self):
        new_success=dict(self.success,id=9)
        self.assertEqual(m.observe(self.prepared,[new_success,self.pending])['status_id'],9)
        old_success=dict(self.success,id=99)
        with self.assertRaisesRegex(RuntimeError,'prepared_success_unknown'):
            m.observe(self.prepared,[self.pending,old_success])

    def test_completed_attempt_provenance_is_required_but_not_close_authority(self):
        source='d'*40;document=dict(source_commit=source)
        log=m.MARKER+m.canonical(self.prepared)+'\n'
        def check():
            return m.authenticate_native_prepared(None,'owner/repo',document,'e'*40,101,2,9,'a'*40,None)
        with patch.object(m.guard,'authenticate_native_log',return_value=({},log)) as auth:
            self.assertEqual(check(),self.prepared)
            self.assertEqual(auth.call_args.args[4:6],(101,2))
            auth.return_value=({},'ordinary log without a prepared record\n')
            self.assertIsNone(m.find_native_prepared(None,'owner/repo',document,'e'*40,101,2,9,'a'*40,None))
            with self.assertRaisesRegex(RuntimeError,'prepared_status_missing_or_duplicate'):check()
            auth.return_value=({},log+log)
            with self.assertRaisesRegex(RuntimeError,'prepared_status_duplicate'):check()
            auth.side_effect=RuntimeError('untrusted source')
            with self.assertRaisesRegex(RuntimeError,'untrusted source'):check()


if __name__=='__main__':unittest.main()
