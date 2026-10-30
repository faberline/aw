"""The exact guarded publisher needs close permission, not arbitrary code."""
import copy
import json
import unittest
from unittest.mock import patch
import yaml

import test_policy_adoption as fixture

a=fixture.a


class PublisherClosePermissionTests(unittest.TestCase):
    def setUp(self):
        self.grant=fixture.grant();spec=self.grant['publisher']
        self.workflow=dict(name='Reviewed policy adoption',
            on=dict(push=dict(branches=[spec['branch']],paths=[spec['workflow_path']])),permissions={},
            concurrency=dict(group='issue-ci-handoff',**{'cancel-in-progress':False}),
            jobs={spec['job']:{'runs-on':'ubuntu-latest','timeout-minutes':30,
                'permissions':dict(actions='read',contents='write',**{'pull-requests':'write','statuses':'write','issues':'write'}),
                'steps':[dict(uses='faberline/aw/.github/actions/policy-adoption@'+self.grant['evaluator'])]}})
        self.candidate=dict(issue_guard=dict(enabled=True),issue_guard_rollout=dict(source='f'*40))
        self.run=dict(id=333,workflow_id=spec['workflow_id'],path=spec['workflow_path'],head_sha=spec['source_commit'],
                      head_branch=spec['branch'],head_repository={'full_name':self.grant['repo']},
                      event='push',run_attempt=1,status='in_progress')
        self.calls=[];owner=self
        class Provider:
            def api(self,path):return copy.deepcopy(owner.run)
            def pages(self,path,key):return [dict(id=334,name=spec['job'],status='in_progress')]
        self.api=Provider()

    def source(self,api,repo,path,ref,expected):
        self.calls.append((repo,path,ref,expected))
        spec=self.grant['publisher']
        if path==spec['workflow_path']:
            self.assertEqual((ref,expected),(spec['source_commit'],spec['workflow_blob']))
            return yaml.safe_dump(self.workflow)
        self.assertEqual((path,ref,expected),('.github/issue-ci-required-gates.json',self.grant['head'],self.grant['policy_blob']))
        return json.dumps(self.candidate)

    def check(self,required=True):
        with patch.object(a.review,'blob',side_effect=self.source):
            return a.publisher_provenance(self.api,self.grant,333,'push',require_issue_write=required)

    def test_exact_guarded_policy_and_fixed_action_allow_close_permission(self):
        self.assertEqual(self.check()['job_id'],334)
        self.assertEqual(len(self.calls),2)

    def test_read_only_is_legacy_only_and_cannot_enter_guarded_completion(self):
        self.workflow['jobs'][self.grant['publisher']['job']]['permissions']['issues']='read'
        with self.assertRaisesRegex(RuntimeError,'publisher_issue_permission'):self.check()
        self.assertEqual(self.check(required=False)['job_id'],334)

    def test_write_without_exact_guard_policy_is_refused(self):
        for candidate in ({},dict(issue_guard=dict(enabled=False)),
                          dict(issue_guard=dict(enabled=True)),
                          dict(issue_guard=dict(enabled=True),issue_guard_rollout=dict(source='unknown'))):
            self.candidate=candidate
            with self.subTest(candidate=candidate),self.assertRaisesRegex(RuntimeError,'publisher_issue_permission'):
                self.check()

    def test_extra_code_permissions_or_mutable_action_remain_refused(self):
        baseline=copy.deepcopy(self.workflow)
        changes=[lambda j:j['steps'].append(dict(run='untrusted command')),
                 lambda j:j['permissions'].update(actions='write'),
                 lambda j:j['steps'][0].update(uses='faberline/aw/.github/actions/policy-adoption@main')]
        for change in changes:
            self.workflow=copy.deepcopy(baseline);change(self.workflow['jobs'][self.grant['publisher']['job']])
            with self.subTest(change=change),self.assertRaises(RuntimeError):self.check()
