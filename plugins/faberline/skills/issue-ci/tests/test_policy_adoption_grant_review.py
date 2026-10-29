"""Independent grant review only reads data and blocks without exact evidence."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_policy_adoption as adoption

spec=importlib.util.spec_from_file_location('grant_review_test',Path(__file__).parents[1]/'scripts/policy_adoption_grant_review.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
HEAD='3'*40;BASE='4'*40;BLOB='5'*40


def fixture():
    grant=adoption.grant();grant.pop('grant_review')
    pull=dict(number=77,state='open',title='Grant',body='Closes #97',changed_files=1,
              head=dict(sha=HEAD,repo={'full_name':'faberline/aw'}),
              base=dict(sha=BASE,ref='main',repo={'full_name':'faberline/aw'}))
    issue=dict(number=97,state='open',title='Grant issue',body='Review the exact grant')
    target_issue=dict(number=7,title='Target issue',body='Target requirements')
    target_pr=dict(number=8,title='Target PR',body='Closes #7')
    class API:
        def api(self,path):
            if path=='repos/faberline/aw/pulls/77':return copy.deepcopy(pull)
            if path=='repos/faberline/aw/commits/main':return {'sha':BASE}
            if path=='repos/faberline/aw/issues/97':return copy.deepcopy(issue)
            if path=='repos/o/r/issues/7':return copy.deepcopy(target_issue)
            if path=='repos/o/r/pulls/8':return copy.deepcopy(target_pr)
            raise AssertionError(path)
        def pages(self,path,key=None):
            if path=='repos/faberline/aw/pulls/77/files':return [dict(filename=m.a.GRANTS_PATH,status='modified',sha=BLOB)]
            raise AssertionError(path)
    def blob(api,repo,path,ref,expected=None):
        if ref==HEAD:return json.dumps({'version':1,'grants':[grant]})
        if ref==BASE:return json.dumps({'version':1,'grants':[]})
        raise AssertionError(ref)
    return API(),pull,issue,grant,blob


class GrantReviewTests(unittest.TestCase):
    def run_proposal(self,api,blob):
        with patch.object(m.a.review,'blob',side_effect=blob),patch.object(m.a,'evaluate',return_value={'verified':'current'}),\
             patch.object(m.a,'candidate_policy',return_value={'validated':'policy'}):
            return m.proposal(api,'o/r',77,HEAD,adoption.EVALUATOR)

    def test_only_the_exact_data_file_and_current_target_are_reviewed(self):
        api,pull,issue,grant,blob=fixture();packet=self.run_proposal(api,blob)
        self.assertEqual(packet['grant'],grant)
        self.assertEqual(packet['verified_target'],{'verified':'current'})
        pull['head']['sha']='f'*40
        with self.assertRaises(RuntimeError):self.run_proposal(api,blob)

    def test_unknown_or_additional_grant_fails_before_review(self):
        api,pull,issue,grant,blob=fixture();grant['unexpected']=True
        with self.assertRaises(RuntimeError):self.run_proposal(api,blob)

    def test_model_has_no_tools_or_github_token(self):
        packet={'grant':'synthetic'};called=[];token='private-test-credential'
        pin=json.loads(Path(m.__file__).with_name('policy_adoption_supply.json').read_text())['binary_sha256']
        def execute(args,**kwargs):
            called.append((args,kwargs))
            return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'result','subtype':'success','is_error':False,
                'structured_output':{'decision':'pass','summary':'Reviewed','findings':[],'uncertainties':[]}}))
        with patch.object(m.Path,'read_bytes',return_value=b'synthetic-binary'),\
             patch.object(m.hashlib,'sha256',return_value=SimpleNamespace(hexdigest=lambda:pin)):
            result=m.invoke(packet,token,execute)
        self.assertEqual(result['decision'],'pass')
        args,kwargs=called[0]
        self.assertEqual(args[args.index('--tools')+1],'')
        self.assertNotIn('GH_TOKEN',kwargs['env'])
        self.assertEqual(kwargs['env']['CLAUDE_CODE_OAUTH_TOKEN'],token)
        self.assertEqual(kwargs['cwd'],kwargs['env']['HOME'])

    def test_failed_review_or_midflight_change_emits_no_pass(self):
        packet={'grant':adoption.grant(),'grant_pr':{'blob':BLOB}}
        failed=lambda *args:{'decision':'fail','findings':['bad'],'uncertainties':[]}
        with patch.object(m,'proposal',return_value=packet):
            with self.assertRaisesRegex(RuntimeError,'grant_review_not_pass'):m.produce(None,'o/r',77,HEAD,adoption.EVALUATOR,failed)
        changed=copy.deepcopy(packet);changed['grant']['base']='0'*40
        passed=lambda *args:{'decision':'pass','findings':[],'uncertainties':[]}
        with patch.object(m,'proposal',side_effect=[packet,changed]):
            with self.assertRaisesRegex(RuntimeError,'inputs_changed'):m.produce(None,'o/r',77,HEAD,adoption.EVALUATOR,passed)

class FinalGrantHeadTests(unittest.TestCase):
    run_proposal=GrantReviewTests.run_proposal
    def test_final_head_rechecks_prior_attestation_before_model(self):
        api,pull,issue,grant,blob=fixture();grant['grant_review']=adoption.grant()['grant_review']
        with patch.object(m.a,'verify_grant_attestation') as verify:
            packet=self.run_proposal(api,blob)
        self.assertEqual(packet['grant'],grant)
        self.assertFalse(verify.call_args.kwargs['require_merged'])

    def test_receipt_binds_proposal_blob_and_exact_non_attestation_payload(self):
        api,pull,issue,grant,blob=fixture();grant['grant_review']=adoption.grant()['grant_review']
        packet=dict(grant=grant,grant_pr=dict(number=77,head=HEAD,base=BASE,blob=BLOB,title='Grant',body='Closes #97'),
                    grant_issue=dict(number=97,title='Grant issue',body='Review the exact grant'))
        model=lambda *args:{'decision':'pass','findings':[],'uncertainties':[]}
        with patch.object(m,'proposal',return_value=packet):
            receipt=m.produce(None,'o/r',77,HEAD,adoption.EVALUATOR,model)
        self.assertEqual(receipt['grant_blob'],BLOB)
        self.assertEqual(receipt['grant_digest'],m.a.r.digest({k:v for k,v in grant.items() if k!='grant_review'}))
        self.assertEqual(receipt['grant_issue'],97)
