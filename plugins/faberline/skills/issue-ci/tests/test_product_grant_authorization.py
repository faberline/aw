"""AW grants need complete controls and actual product CI/review, no AW model."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import test_policy_adoption as fixtures

a=fixtures.a


class ProductAuthorizationTests(unittest.TestCase):
    def test_only_actual_rollout_verification_can_admit_guarded_bootstrap(self):
        for verified in (True,False):
            g,doc,_,api,blob,result=self.fixture()
            candidate=dict(issue_guard={'enabled':True},issue_guard_rollout={'epoch':'a'*32})
            def sources(provider,repo,path,ref,*args):
                if repo==g['repo'] and path=='.github/issue-ci-required-gates.json':
                    return json.dumps(candidate)
                return blob(provider,repo,path,ref,*args)
            calls=[]
            def verify(provider,repo,document):
                calls.append((repo,document))
                if not verified:raise RuntimeError('deployment not proved')
            with self.subTest(verified=verified),patch.object(a.review,'blob',side_effect=sources), \
                 patch.object(a,'latest_target_review'), \
                 patch.object(a,'evaluate',side_effect=a.review.Refused('guarded_adoption_not_ready')), \
                 patch.object(a.review,'sibling',return_value=SimpleNamespace(verify_rollout=verify))as sibling, \
                 patch.object(a,'_evaluate_guarded',return_value=result)as guarded:
                if verified:
                    a.verify_grant_attestation(api,doc,g,g['evaluator'])
                    guarded.assert_called_once()
                else:
                    with self.assertRaisesRegex(RuntimeError,'deployment not proved'):
                        a.verify_grant_attestation(api,doc,g,g['evaluator'])
                    guarded.assert_not_called()
                sibling.assert_called_once_with('recovery_entry')
            self.assertEqual(calls,[(g['repo'],dict(config=candidate,source_commit=g['head']))])
            self.assertEqual(api.writes,[])

    def test_another_refusal_never_enters_guarded_bootstrap(self):
        g,doc,_,api,blob,_=self.fixture()
        with patch.object(a.review,'blob',side_effect=blob),patch.object(a,'latest_target_review'), \
             patch.object(a,'evaluate',side_effect=a.review.Refused('candidate_ci_not_green')), \
             patch.object(a.review,'sibling')as sibling,patch.object(a,'_evaluate_guarded')as guarded, \
             self.assertRaisesRegex(RuntimeError,'candidate_ci_not_green'):
            a.verify_grant_attestation(api,doc,g,g['evaluator'])
        sibling.assert_not_called();guarded.assert_not_called()

    def fixture(self):
        grant=fixtures.grant();grant.pop('grant_review');grant['repo']='faberline/core'
        grant['grant_authorization']=dict(version=1,kind='product-evidence',pr=77)
        receipt=dict(decision='pass',findings=[],uncertainties=[])
        grant['review']['receipt_sha256']=a.r.digest(receipt)
        document=dict(source_commit='6'*40,config=dict(version=1,grants=[grant]))
        state=dict(comparison='ahead',extra_file=False,changed_workflow=False,target_merged=False,default='main',
                   jobs=[dict(id=900,name='complete isolated native merge controls',status='completed',conclusion='success',html_url='https://example.test/job')],
                   run=dict(id=899,run_number=1,run_attempt=1,status='completed',conclusion='success',html_url='https://example.test/run',
                            workflow_id=9,path='.github/workflows/native-auto-merge-controls.yml',head_sha='3'*40,event='pull_request',
                            head_repository={'full_name':'faberline/aw'},repository={'full_name':'faberline/aw'},pull_requests=[]))
        class API:
            writes=[]
            queries=None
            def api(self,path,**kwargs):
                if kwargs:self.writes.append((path,kwargs));raise AssertionError('authorization must be read-only')
                if path=='repos/faberline/aw':return dict(full_name='faberline/aw',default_branch=state['default'])
                if path.endswith('/pulls/77'):
                    return dict(number=77,state='closed',merged=True,merge_commit_sha='5'*40,changed_files=1,
                                head=dict(sha='3'*40,repo={'full_name':'faberline/aw'}),
                                base=dict(sha='4'*40,ref='main',repo={'full_name':'faberline/aw'}))
                if '/compare/'in path:
                    before=path.split('/compare/')[1].split('...')[0]
                    return dict(status=state['comparison'],base_commit={'sha':before},behind_by=0,merge_base_commit={'sha':before})
                if '/actions/workflows/'in path:return dict(id=9,path='.github/workflows/native-auto-merge-controls.yml')
                if path=='repos/faberline/core/pulls/8':return dict(state='closed'if state['target_merged']else'open',merged=state['target_merged'])
                raise AssertionError(path)
            def pages(self,path,key=None):
                if key=='workflow_runs':
                    self.queries=path
                    return [state['run'],*state.get('later_runs',[])]
                if key=='jobs':return state['jobs']
                files=[dict(filename=a.GRANTS_PATH,status='modified',sha='7'*40)]
                if state['extra_file']:files.append(dict(filename='source.py',status='added',sha='8'*40))
                return files
            evaluate_evidence=staticmethod(a.ci.evaluate_evidence)
        def blob(api,repo,path,ref,*args):
            if path==a.GRANTS_PATH:return json.dumps(document['config'])
            if state['changed_workflow']and ref=='3'*40:return 'changed'
            return 'reviewed complete AW controls'
        result=dict(issue=7,receipt=receipt)
        return grant,document,state,API(),blob,result

    def verify(self,change=None,merged=False):
        g,doc,state,api,blob,result=self.fixture()
        if change:change(g,doc,state,result)
        state['target_merged']=merged
        with patch.object(a.review,'blob',side_effect=blob),patch.object(a,'latest_target_review')as latest, \
             patch.object(a,'evaluate',return_value=result)as evaluate, \
             patch.object(a,'evaluate_merged',return_value=result)as evaluate_merged, \
             patch.object(a.review,'sibling',side_effect=AssertionError('AW Claude must not run')):
            proof=a.verify_grant_attestation(api,doc,g,g['evaluator'])
        return proof,api,latest,evaluate,evaluate_merged

    def test_explicit_new_mode_requires_one_exact_grant_and_no_legacy_attestation(self):
        g,doc,*_=self.fixture()
        self.assertEqual(a.choose_grant(doc['config'],'faberline/core',g['evaluator']),g)
        for change in (lambda x:x['grant_authorization'].update(pr=True),
                       lambda x:x['grant_authorization'].update(kind='skip'),
                       lambda x:x.update(grant_review=fixtures.grant()['grant_review'])):
            bad=copy.deepcopy(g);change(bad)
            with self.assertRaises(RuntimeError):a.grant_spec(bad)

    def test_complete_control_and_independent_product_evidence_authorize_read_only(self):
        proof,api,latest,evaluate,merged=self.verify()
        self.assertEqual(proof['kind'],'product-evidence')
        self.assertEqual(proof['control_run'],899)
        self.assertEqual(api.writes,[])
        self.assertIn('head_sha='+'3'*40+'&event=pull_request',api.queries)
        latest.assert_called_once();evaluate.assert_called_once();merged.assert_not_called()

    def test_merged_target_still_rechecks_product_review_and_ci(self):
        _,_,latest,evaluate,merged=self.verify(merged=True)
        latest.assert_called_once();evaluate.assert_not_called();merged.assert_called_once()

    def test_unmerged_aw_proposal_and_aw_target_never_authorize(self):
        g,doc,_,api,blob,_=self.fixture()
        with self.assertRaisesRegex(RuntimeError,'product_grant_source'):
            a.verify_product_authorization(api,doc,g,g['evaluator'],None,False)
        with self.assertRaisesRegex(RuntimeError,'product_grant_source'):
            self.verify(lambda g,*_:g.update(repo='faberline/aw'))
        original=api.api
        def open_pull(path,**kwargs):
            result=original(path,**kwargs)
            if path.endswith('/pulls/77'):result.update(merged=False,state='open')
            return result
        api.api=open_pull
        with patch.object(a.review,'blob',side_effect=blob),self.assertRaisesRegex(RuntimeError,'not_merged'):
            a.verify_grant_attestation(api,doc,g,g['evaluator'])

    def test_changed_source_extra_code_and_unmerged_history_refuse(self):
        for field,value in [('extra_file',True),('changed_workflow',True),('comparison','diverged')]:
            def change(g,doc,state,result):state[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):self.verify(change)

    def test_missing_failed_skipped_cancelled_partial_or_duplicate_aw_controls_refuse(self):
        edits=[lambda s:s['jobs'].clear(),lambda s:s['jobs'].append(copy.deepcopy(s['jobs'][0])),
               lambda s:s['jobs'][0].update(conclusion='failure'),lambda s:s['jobs'][0].update(conclusion='skipped'),
               lambda s:s['run'].update(status='in_progress'),lambda s:s['run'].update(conclusion='cancelled')]
        for edit in edits:
            with self.subTest(edit=edit),self.assertRaises(RuntimeError):
                self.verify(lambda g,doc,state,result:edit(state))

    def test_changed_product_receipt_or_issue_refuse(self):
        for field,value in [('issue',9),('receipt',dict(decision='fail'))]:
            with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'evidence_changed'):
                self.verify(lambda g,doc,state,result:result.update({field:value}))

    def test_later_review_failure_and_product_ci_failure_are_not_waived(self):
        g,doc,_,api,blob,result=self.fixture()
        for boundary in ('latest_target_review','evaluate'):
            with patch.object(a.review,'blob',side_effect=blob), \
                 patch.object(a,'latest_target_review',side_effect=RuntimeError('latest failed')if boundary=='latest_target_review'else None), \
                 patch.object(a,'evaluate',side_effect=RuntimeError('product CI failed')if boundary=='evaluate'else None), \
                 self.subTest(boundary=boundary),self.assertRaises(RuntimeError):
                a.verify_grant_attestation(api,doc,g,g['evaluator'])

    def test_foreign_wrong_head_or_wrong_workflow_control_run_refuses(self):
        for fields in ({'head_sha':'f'*40},{'workflow_id':10},{'run_attempt':True},
                       {'head_repository':{'full_name':'fork/aw'}},{'repository':{'full_name':'fork/aw'}}):
            with self.subTest(fields=fields),self.assertRaises(RuntimeError):
                self.verify(lambda g,doc,state,result:state['run'].update(fields))

    def test_newer_failing_aw_control_attempt_cannot_fall_back_to_old_green(self):
        def later(g,doc,state,result):
            state['later_runs']=[dict(state['run'],id=901,run_number=2,conclusion='failure')]
        with self.assertRaisesRegex(RuntimeError,'controls_not_green'):self.verify(later)

    def test_changed_aw_default_is_not_authority(self):
        with self.assertRaisesRegex(RuntimeError,'default_changed'):
            self.verify(lambda g,doc,state,result:state.update(default='other'))

    def test_new_authorization_never_changes_the_required_legacy_guard_refusal(self):
        # New proof has a separate marker. Bare data must still fail before a
        # queue or status write, even if a caller requests the direct path.
        check=fixtures.MutationSafetyTests()
        check.test_merged_data_grant_never_uses_unguarded_queue()


if __name__=='__main__':unittest.main()
