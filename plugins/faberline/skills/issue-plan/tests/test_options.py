import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).parents[1]/'scripts'
spec=importlib.util.spec_from_file_location('planner_options_test',ROOT/'options.py')
o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o)
spec=importlib.util.spec_from_file_location('planner_cli_test',ROOT/'plan.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class OptionsControls(unittest.TestCase):
    def test_product_defaults_keep_release_check_but_add_no_permission(self):
        result=o.parse([])
        self.assertEqual(result['tasks'],list(o.TASKS))
        self.assertFalse(result['mutation_authorized']);self.assertFalse(result['creates_milestones'])
        self.assertFalse(result['invents_product_capability'])

    def test_shared_role_consumes_demand_and_still_checks_release(self):
        result=o.parse(['--role','shared'])
        self.assertEqual(result['tasks'],['triage','queue','release'])

    def test_explicit_tasks_and_milestone_override_repo_defaults(self):
        config=dict(version=1,role='shared',tasks=['triage'],milestone=3)
        result=o.parse(['--tasks','release,queue','--milestone','9'],config)
        self.assertEqual(result['tasks'],['queue','release']);self.assertEqual(result['milestone'],9)

    def test_empty_duplicate_unknown_tasks_and_bad_milestones_refuse(self):
        for args in (['--tasks',''],['--tasks','release,release'],['--tasks','invent'],['--tasks','triage, queue'],['--milestone','0']):
            with self.subTest(args=args),self.assertRaises(ValueError):o.parse(args)

    def test_invalid_arguments_are_rejected_before_config_reads(self):
        with patch.object(o.Path,'exists',side_effect=AssertionError('no file read')):
            with self.assertRaises(ValueError):o.resolve(['--tasks','invent'])
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):o.resolve(['--unknown'])

    def test_config_schema_cannot_add_implicit_privileges(self):
        for config in (dict(version=1,role='agent'),dict(version=1,merge=True),dict(version=2),dict(version=1,tasks=[])):
            with self.subTest(config=config),self.assertRaises(ValueError):o.parse([],config)

    def test_options_cli_is_readonly_and_does_not_construct_provider_or_guard(self):
        output=io.StringIO()
        with (patch.object(p.queue,'Client',side_effect=AssertionError('no gh')),
              patch.object(p.subprocess,'check_output',side_effect=AssertionError('no git')),
              patch.object(sys,'argv',['plan.py','options','--tasks','queue','--milestone','9']),
              contextlib.redirect_stdout(output)):
            self.assertEqual(p.main(),0)
        self.assertEqual(json.loads(output.getvalue())['tasks'],['queue'])

    def test_bad_release_status_refuses_before_provider(self):
        with (patch.object(p.queue,'Client',side_effect=AssertionError('no gh')),
              patch.object(sys,'argv',['plan.py','release-status','--milestone','0']),
              self.assertRaises(ValueError)):
            p.main()

    def test_automatic_release_failure_cannot_enter_todo_without_real_spec(self):
        from types import SimpleNamespace
        body='<!-- aw-release-failure:abc -->\n## Goal\nRepair\n## Acceptance\nProve\n## Never\nKeep\n## Gate\nNeeds real spec\n'
        client=SimpleNamespace(issue=lambda n:dict(number=n,state='open',labels=[],body=body),
                               comments=lambda n:[],spec=lambda issue,comments:body)
        with self.assertRaisesRegex(ValueError,'exact affected unit'):p.adopt(client,7,'p0')


if __name__=='__main__':unittest.main()
