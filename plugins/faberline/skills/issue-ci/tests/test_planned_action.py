import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_qualification as fixture


action=fixture.action
HEAD='a'*40
AW='b'*40


class PlannedActionTests(unittest.TestCase):
    def invoke(self,event,operation='planned-execution'):
        config={'workflows':[{'path':'.github/workflows/pr.yml'}],
                'review_readiness':{'producer':{'path':'.github/workflows/readiness.yml'}}}
        doc={'config':config,'source_commit':'c'*40,'path':'.github/issue-ci-required-gates.json'}
        calls=[]
        readiness=SimpleNamespace(validate_contract=lambda *args:None,canonical=lambda value:json.dumps(value))
        planned=SimpleNamespace(MARKER='AW_PLANNED_EXECUTION_V1 ',
            produce=lambda *args:(calls.append(args) or {'payload':{'qualification':'not_evaluated'},'sha256':'x'}))
        ci=SimpleNamespace(strict_json_loads=json.loads)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'event.json';path.write_text(json.dumps(event))
            env={'AW_ACTION_REF':AW,'INPUT_OPERATION':operation,'INPUT_PR':'8','INPUT_HEAD':HEAD,
                 'GITHUB_REPOSITORY':'o/r','GITHUB_EVENT_PATH':str(path),'GITHUB_RUN_ID':'10'}
            with patch.dict(os.environ,env,clear=True),patch.object(action,'controller',return_value=ci), \
                 patch.object(action,'trusted_policy',return_value=doc), \
                 patch.object(action,'ci_module',side_effect=lambda name:readiness if name=='readiness' else planned), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                result=action.main()
        return result,calls,output.getvalue()

    def event(self):
        return {'action':'completed','workflow_run':{'status':'completed','event':'pull_request',
                'path':'.github/workflows/pr.yml','head_repository':{'full_name':'o/r'}}}

    def test_trusted_wake_emits_execution_only_receipt(self):
        result,calls,output=self.invoke(self.event())
        self.assertEqual(result,0);self.assertEqual(len(calls),1)
        self.assertIn('AW_PLANNED_EXECUTION_V1 ',output)
        self.assertIn('not_evaluated',output)

    def test_foreign_self_nonterminal_and_unconfigured_wakes_refuse(self):
        for change in ('foreign','self','pending','other','event'):
            event=self.event();wake=event['workflow_run']
            if change=='foreign':wake['head_repository']={'full_name':'x/r'}
            if change=='self':wake['path']='.github/workflows/readiness.yml'
            if change=='pending':wake['status']='in_progress'
            if change=='other':wake['path']='.github/workflows/other.yml'
            if change=='event':event['action']='requested'
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'ignore'):
                self.invoke(event)

    def test_verifier_passes_untrusted_receipt_to_authenticator_without_label_write(self):
        doc={'config':{},'source_commit':'c'*40,'path':'.github/issue-ci-required-gates.json'}
        receipt={'payload':{'qualification':'not_evaluated'},'sha256':'x'}
        calls=[]
        readiness=SimpleNamespace(canonical=lambda value:json.dumps(value))
        planned=SimpleNamespace(verify=lambda *args:(calls.append(args) or receipt))
        ci=SimpleNamespace(strict_json_loads=json.loads)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'receipt.json';path.write_text(json.dumps(receipt))
            env={'AW_ACTION_REF':AW,'INPUT_OPERATION':'verify-planned-execution','INPUT_PR':'8','INPUT_HEAD':HEAD,
                 'GITHUB_REPOSITORY':'o/r','INPUT_RECEIPT':str(path)}
            with patch.dict(os.environ,env,clear=True),patch.object(action,'controller',return_value=ci), \
                 patch.object(action,'trusted_policy',return_value=doc), \
                 patch.object(action,'ci_module',side_effect=lambda name:readiness if name=='readiness' else planned), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(action.main(),0)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1],receipt)
        self.assertIn('not_evaluated',output.getvalue())


if __name__=='__main__':unittest.main()
