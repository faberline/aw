"""Signed Claude supply verification refuses credentials and altered bytes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

script=Path(__file__).parents[1]/'scripts'/'policy_adoption_provision.py'
spec=importlib.util.spec_from_file_location('policy_adoption_provision_test',script)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
SUPPLY=json.loads(script.with_name('policy_adoption_supply.json').read_text())


class ProvisionTests(unittest.TestCase):
    def test_unreviewed_or_wrong_origin_fails_before_network(self):
        for change in (dict(enabled=False),dict(origin='https://evil.invalid'),dict(binary_sha256='bad'),dict(cli_version='other')):
            calls=[];supply=dict(SUPPLY,**change)
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                p.provision(supply,'/unused',reader=lambda *args:calls.append(args))
            self.assertEqual(calls,[])

    def test_fixed_paths_hash_and_signature_are_required(self):
        binary=b'synthetic verified binary';binary_hash=hashlib.sha256(binary).hexdigest()
        manifest=json.dumps({'version':p.VERSION,'platforms':{'linux-x64':{'checksum':binary_hash}}}).encode()
        supply=dict(SUPPLY,binary_sha256=binary_hash,manifest_sha256=hashlib.sha256(manifest).hexdigest())
        blobs={supply['manifest_path']:manifest,supply['signature_path']:b'sig',supply['signing_key_path']:b'key',supply['binary_path']:binary}
        signatures=[]
        def reader(path,limit):return blobs[path]
        def verify(directory,raw,signature,key):signatures.append((raw,signature,key))
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'verified'
            p.provision(supply,dest,reader=reader,verify=verify)
            self.assertEqual(dest.read_bytes(),binary);self.assertEqual(len(signatures),1)
            with self.assertRaises(RuntimeError):p.provision(supply,dest,reader=reader,verify=verify)
            blobs[supply['binary_path']]=b'tampered'
            with self.assertRaisesRegex(RuntimeError,'binary_hash_mismatch'):p.provision(supply,Path(directory)/'second',reader=reader,verify=verify)

    def test_provisioning_refuses_any_model_or_github_credential(self):
        for name in ('GH_TOKEN','GITHUB_TOKEN','CLAUDE_CODE_OAUTH_TOKEN','ANTHROPIC_API_KEY'):
            env={'GITHUB_ACTIONS':'true','RUNNER_OS':'Linux',name:'synthetic'}
            with self.subTest(name=name),patch.dict(os.environ,env,clear=True),patch.object(p.platform,'machine',return_value='x86_64'),patch.object(p.sys,'argv',['provision']):
                with self.assertRaisesRegex(RuntimeError,'credentialed_provisioning_refused'):p.main()
