import importlib.util
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace

spec=importlib.util.spec_from_file_location('planned_reconcile_test',Path(__file__).parents[1]/'scripts/planned_reconcile.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ReconcileTests(unittest.TestCase):
    def test_each_doing_pr_has_independent_numeric_wake_even_when_another_fails(self):
        base='a'*40
        config={'planned_ci_qualification':{'version':1},'review_readiness':{'producer':{'path':'.github/workflows/proof.yml'},
                 'sources':{'aw':{'repo':'faberline/aw','path':'.github/actions/issue-ci/action.yml','ref':'b'*40}}}}
        doc={'config':config,'source_commit':base}
        def pull(n):
            return {'number':n,'state':'open','draft':False,'head':{'repo':{'full_name':'o/r'}},
                    'base':{'repo':{'full_name':'o/r'},'sha':base}}
        def api(path):
            return {'state':'open','labels':[{'name':'doing'}]} if '/issues/' in path else {'default_branch':'main'}
        ci=SimpleNamespace(validate_config=lambda x:None,api=api,pages=lambda p:[pull(1),pull(2)],
                           qualification_module=lambda:SimpleNamespace(linked_issue=lambda p:p['number']))
        def need(ok,*args):
            if not ok:raise RuntimeError(str(args))
        readiness=SimpleNamespace(validate_contract=lambda *a:None,pin_proof=lambda *a,**k:None,need=need)
        action=SimpleNamespace(trusted_policy=lambda *a,**k:doc)
        producer=SimpleNamespace(validate_source=lambda *a:None)
        calls=[]
        def dispatch(path,payload):
            calls.append(payload)
            if payload['inputs']['pr']=='1':raise subprocess.CalledProcessError(1,'gh')
        with self.assertRaisesRegex(RuntimeError,'PRs: 1'):
            module.run('o/r',ci,action,readiness,producer,dispatch)
        self.assertEqual([x['inputs']['pr'] for x in calls],['1','2'])
        calls.clear()
        def failed_first(path):
            if path.endswith('/issues/1'):raise subprocess.CalledProcessError(1,'gh')
            return api(path)
        ci.api=failed_first
        with self.assertRaisesRegex(RuntimeError,'PRs: 1'):
            module.run('o/r',ci,action,readiness,producer,dispatch)
        self.assertEqual([x['inputs']['pr'] for x in calls],['2'])

    def test_repo_without_opt_in_never_dispatches(self):
        action=SimpleNamespace(trusted_policy=lambda *a,**k:{'config':{}})
        self.assertEqual(module.run('o/r',None,action,None,None,lambda *a:self.fail()),[])


if __name__=='__main__':unittest.main()
