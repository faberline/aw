"""Diff/all selection and actual per-case execution controls, no product execution."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('shared_test_plan', Path(__file__).parents[1] / 'scripts/test_plan.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
HEAD, BASE, REPO = 'a' * 40, 'b' * 40, 'org/app'
WORKFLOW = '.github/workflows/tests.yml'


def config():
    checks = {name:dict(kind=kind, workflow=WORKFLOW, job=name) for name, kind in
              [('format','policy'), ('unit-a','unit'), ('unit-b','unit'),
               ('e2e','functional_e2e'), ('performance','performance')]}
    return dict(lifecycle_mode='full', qualification_blockers=[], workflows=[dict(path=WORKFLOW,jobs=list(checks))],
                qualification=dict(version=1, checks=checks, always=['format'], protected_paths=['scripts/**'],
                    trusted_workflows={WORKFLOW:dict(id=1,definition_sha='c'*40)},
                    rules=[dict(paths=['src/**'],require=['unit-a','e2e','performance'],exempt={}),
                           dict(paths=['docs/**'],require=[],exempt={k:'Reviewed documentation-only scope' for k in ('unit','functional_e2e','performance')})]))


def selection(mode='all', value=None, files=None):
    pull=dict(number=1,changed_files=1,head=dict(sha=HEAD,repo={'full_name':REPO}),
              base=dict(sha=BASE,repo={'full_name':REPO}))
    return t.select(value or config(),mode,REPO,HEAD,BASE,pull=pull,
                    files=files if files is not None else [dict(filename='src/a.rs',status='modified')])


def receipts(plan):
    return [dict(check=name, repo=REPO, head=HEAD, base=BASE, plan_sha256=plan['sha256'],
                 conclusion='success', expected=[name+'::real_test'], executed=[name+'::real_test'], failures=[], skipped=[])
            for name in plan['selected']]


class TestPlanControls(unittest.TestCase):
    def test_all_selects_unaffected_cases_and_complete_inventory(self):
        diff, full = selection('diff'), selection('all')
        self.assertNotIn('unit-b',diff['selected'])
        self.assertEqual(full['selected'],sorted(config()['qualification']['checks']))
        self.assertEqual(full['checks'],config()['qualification']['checks'])

    def test_diff_preserves_old_path_for_rename(self):
        value=config();value['qualification']['rules'].append(dict(paths=['old/**'],require=['unit-b','e2e','performance'],exempt={}))
        result=selection('diff',value,[dict(filename='src/a.rs',previous_filename='old/a.rs',status='renamed')])
        self.assertIn('unit-b',result['selected']);self.assertIn('old/a.rs',result['paths'])

    def test_unknown_path_or_incomplete_inventory_does_not_select_zero(self):
        for files in ([],[dict(filename='new/a.rs',status='added')]):
            with self.subTest(files=files), self.assertRaises(ValueError):selection('diff',files=files)

    def test_blocked_or_failure_only_policy_cannot_be_approved_by_mode(self):
        for change in (dict(lifecycle_mode='failure-only'),dict(qualification_blockers=['Missing performance'])):
            value=config();value.update(change)
            for mode in ('diff','all'):
                with self.subTest(change=change,mode=mode),self.assertRaises(ValueError):selection(mode,value)

    def test_all_rejects_a_missing_full_category(self):
        value=config();del value['qualification']['checks']['performance'];value['qualification']['rules'][0]['require'].remove('performance')
        value['qualification']['rules'][0]['exempt']['performance']='No declaration yet'
        with self.assertRaisesRegex(ValueError,'category'):selection('all',value)

    def test_evidence_cannot_reuse_a_different_policy_or_mode(self):
        plan=selection();rows=receipts(plan)
        for field in ('head','base','plan_sha256','repo'):
            changed=copy.deepcopy(rows);changed[0][field]='different'
            with self.subTest(field=field),self.assertRaises(ValueError):t.verify_execution(plan,changed)
        changed=copy.deepcopy(plan);changed['mode']='diff'
        with self.assertRaisesRegex(ValueError,'digest'):t.verify_execution(changed,rows)

    def test_each_case_executes_the_exact_discovered_names(self):
        plan=selection();rows=receipts(plan)
        self.assertEqual(t.verify_execution(plan,rows)['cases'],plan['selected'])
        self.assertEqual(t.verify_execution(plan,rows)['qualification'],'not_evaluated')
        for change in ('missing','duplicate','extra','zero','skipped','failed','names','conclusion'):
            altered=copy.deepcopy(rows)
            if change=='missing':altered.pop()
            elif change=='duplicate':altered.append(copy.deepcopy(altered[-1]))
            elif change=='extra':altered[-1]['check']='unknown'
            elif change=='zero':altered[-1].update(expected=[],executed=[])
            elif change=='skipped':altered[-1]['skipped']=['one']
            elif change=='failed':altered[-1]['failures']=['one']
            elif change=='names':altered[-1]['executed']=['wrong']
            else:altered[-1]['conclusion']='neutral'
            with self.subTest(change=change),self.assertRaises(ValueError):t.verify_execution(plan,altered)

    def test_metadata_policy_check_does_not_invent_test_names(self):
        plan=selection();rows=receipts(plan)
        next(r for r in rows if r['check']=='format').update(expected=[],executed=[])
        self.assertEqual(t.verify_execution(plan,rows)['status'],'success')

    def test_invalid_mode_and_foreign_head_refuse(self):
        with self.assertRaisesRegex(ValueError,'mode'):selection('fast')
        with self.assertRaises(ValueError):t.select(config(),'all',REPO,'main',BASE)


if __name__=='__main__':unittest.main()
