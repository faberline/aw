"""Actual snapshot/selector behavior after GitHub clears merged PR links."""
import copy
import unittest
from unittest.mock import patch

import test_qualification as fixtures
import test_issue_contract_v2 as v2

ci=fixtures.ci
HEAD,BASE=fixtures.HEAD,fixtures.BASE
PATH=fixtures.WORKFLOW


def merged_pull():
    value=v2.pull()
    value.update(state='closed',merged=True,draft=False,
                 merged_at='2026-10-30T10:00:00Z',merge_commit_sha='f'*40)
    value['base']['ref']='main'
    return value


class MergedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.pull=merged_pull();self.issue=dict(v2.issue(),state='closed')
        self.run=dict(fixtures.TrustedEvidenceTests().run_metadata(),
                      repository={'full_name':'o/r'},pull_requests=[],status='completed',conclusion='success',
                      html_url='https://github.com/o/r/actions/runs/3')
        self.jobs=fixtures.evidence(fixtures.config())[PATH][1]
        self.reads=[]

    def api(self,path):
        self.reads.append(path)
        if path=='repos/o/r/pulls/8':return copy.deepcopy(self.pull)
        if path=='repos/o/r':return dict(full_name='o/r',default_branch='main')
        if path=='repos/o/r/issues/7':return copy.deepcopy(self.issue)
        if path.startswith('repos/o/r/contents/'):return dict(sha=fixtures.BLOB)
        raise AssertionError(path)

    def snapshot(self,run=None,config=None):
        with patch.object(ci,'api',side_effect=self.api), \
             patch.object(ci,'pages',side_effect=[[run or self.run],self.jobs]):
            return ci.merged_snapshot('o/r',HEAD,config or fixtures.scope(),8,BASE)

    def test_actual_merged_pr_accepts_exact_run_with_cleared_links_read_only(self):
        result=self.snapshot()
        self.assertEqual(result[PATH][0],self.run)
        self.assertEqual(ci.evaluate_policy(fixtures.scope(),result)[0],'done')
        self.assertEqual(self.reads.count('repos/o/r/pulls/8'),2)
        with patch.object(ci,'pages',return_value=[self.run]):
            self.assertEqual(ci.snapshot('o/r',HEAD,fixtures.scope()),{})

    def test_closed_issue_is_historical_only_after_actual_merge_authentication(self):
        with patch.object(ci,'api',side_effect=self.api):
            scoped=ci.qualify_merged_config('o/r',self.pull,v2.policy(),fixtures.files())
            self.assertEqual(scoped['_qualification_context']['issues'],[])
            live=ci.qualify_config('o/r',self.pull,v2.policy(),fixtures.files())
            self.assertTrue(live['_qualification_context']['issues'])
        self.assertEqual(self.issue['state'],'closed')

    def test_open_unmerged_foreign_or_changed_merge_never_uses_cleared_links(self):
        changes=[lambda p:p.update(state='open'),lambda p:p.update(merged=False),
                 lambda p:p.update(merged_at=None),lambda p:p.update(merge_commit_sha=None),
                 lambda p:p.update(draft=True),lambda p:p['head'].update(sha='0'*40),
                 lambda p:p['base'].update(sha='0'*40),lambda p:p['base'].update(ref='other'),
                 lambda p:p['head']['repo'].update(full_name='fork/r'),
                 lambda p:p['base']['repo'].update(full_name='fork/r')]
        for change in changes:
            self.pull=merged_pull();change(self.pull)
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'merged CI target identity'):
                self.snapshot()

    def test_missing_association_untrusted_attempt_and_foreign_repository_are_held(self):
        changes=[dict(pull_requests=None),dict(pull_requests={}),dict(pull_requests=['unknown']),
                 dict(workflow_id=99),dict(run_attempt=True),
                 dict(pull_requests=[dict(number=9,head={'sha':HEAD},base={'sha':BASE})]),
                 dict(repository={'full_name':'fork/r'})]
        for change in changes:
            run=dict(self.run,**change)
            with self.subTest(change=change):
                self.assertEqual(self.snapshot(run),{})

    def test_latest_attempt_never_falls_back_to_an_older_green(self):
        old=dict(self.run,pull_requests=[dict(number=8,head={'sha':HEAD},base={'sha':BASE})])
        failed=dict(self.run,id=4,run_number=4,run_attempt=3,conclusion='failure')
        with patch.object(ci,'api',side_effect=self.api), \
             patch.object(ci,'pages',side_effect=[[old,failed],self.jobs])as pages:
            result=ci.merged_snapshot('o/r',HEAD,fixtures.scope(),8,BASE)
        self.assertEqual(result[PATH][0]['id'],4)
        self.assertEqual(ci.evaluate_policy(fixtures.scope(),result)[0],'todo')
        self.assertIn('/runs/4/attempts/3/jobs',pages.call_args_list[1].args[0])

    def test_changed_context_or_merge_during_read_is_held(self):
        bad=fixtures.scope();bad['_qualification_context']['base']='0'*40
        with self.assertRaisesRegex(RuntimeError,'merged CI qualification context'):self.snapshot(config=bad)
        calls=0
        original=self.api
        def api(path):
            nonlocal calls
            if path.endswith('/pulls/8'):
                calls+=1
                if calls==2:self.pull['merge_commit_sha']='1'*40
            return original(path)
        with patch.object(ci,'api',side_effect=api), \
             patch.object(ci,'pages',side_effect=[[self.run],self.jobs]), \
             self.assertRaisesRegex(RuntimeError,'merged CI target changed'):
            ci.merged_snapshot('o/r',HEAD,fixtures.scope(),8,BASE)
