import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('trusted_image', Path(__file__).resolve().parents[1] / 'trusted_image.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = 'ghcr.io/faberline/lumen-test@sha256:' + 'a' * 64
ARGS = {'repo': 'faberline/lumen', 'signer_workflow': 'faberline/aw/.github/workflows/test-builder.yml',
        'signer_digest': 'b' * 40, 'source_digest': 'c' * 40}


class TrustedImageTests(unittest.TestCase):
    def test_platform_digest_requires_matching_index_and_unique_platform(self):
        child = 'sha256:' + 'd' * 64
        row = {'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': child, 'size': 100,
               'platform': {'os': 'linux', 'architecture': 'amd64'}}
        index = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [row]}
        def encode(value):
            raw = json.dumps(value).encode()
            image = 'ghcr.io/faberline/lumen-test@sha256:' + hashlib.sha256(raw).hexdigest()
            return raw, image
        raw, image = encode(index)
        self.assertEqual(module.platform_digest(raw, image, os_name='linux', architecture='amd64'), child)
        with self.assertRaises(ValueError):
            module.platform_digest(raw + b' ', image, os_name='linux', architecture='amd64')
        with self.assertRaises(ValueError):
            module.platform_digest(raw, image, os_name='linux', architecture='arm64')
        for platform in ({'os': 'linux', 'architecture': 'amd64', 'variant': None},
                         {'os': 'linux', 'architecture': 'amd64', 'os.version': 'other'}):
            invalid = dict(index); invalid['manifests'] = [{**row, 'platform': platform}]
            other_raw, other_image = encode(invalid)
            with self.subTest(platform=platform), self.assertRaises(ValueError):
                module.platform_digest(other_raw, other_image, os_name='linux', architecture='amd64')
        for missing in ('platform', 'size'):
            invalid_row = dict(row); invalid_row.pop(missing)
            other_raw, other_image = encode({**index, 'manifests': [invalid_row]})
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                module.platform_digest(other_raw, other_image, os_name='linux', architecture='amd64')
        for size in (0, -1, True):
            other_raw, other_image = encode({**index, 'manifests': [{**row, 'size': size}]})
            with self.subTest(size=size), self.assertRaises(ValueError):
                module.platform_digest(other_raw, other_image, os_name='linux', architecture='amd64')
        index['manifests'].append(dict(row))
        raw, image = encode(index)
        with self.assertRaises(ValueError):
            module.platform_digest(raw, image, os_name='linux', architecture='amd64')

    def test_exact_immutable_identity_and_required_flags(self):
        calls = []
        def execute(command, **kwargs):
            calls.append((command, kwargs))
        result = module.verify_image('/usr/bin/gh', IMAGE, **ARGS, execute=execute)
        self.assertEqual(result['source_digest'], 'c' * 40)
        command, kwargs = calls[0]
        self.assertEqual(command, ['/usr/bin/gh', 'attestation', 'verify', 'oci://' + IMAGE,
            '--repo', 'faberline/lumen', '--signer-workflow', ARGS['signer_workflow'],
            '--signer-digest', 'b' * 40, '--source-digest', 'c' * 40,
            '--source-ref', 'refs/heads/main', '--deny-self-hosted-runners'])
        self.assertTrue(kwargs['check'])

    def test_untrusted_inputs_and_verifier_failure_never_pass(self):
        for field, value in (('image', 'ghcr.io/faberline/lumen-test:latest'), ('repo', 'other/lumen'),
                             ('signer_workflow', 'other/aw/.github/workflows/test.yml'),
                             ('signer_digest', 'main'), ('source_digest', 'a' * 39)):
            options = dict(ARGS); image = IMAGE
            if field == 'image': image = value
            else: options[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.verify_image('/usr/bin/gh', image, **options)
        def fail(*args, **kwargs): raise subprocess.CalledProcessError(1, ['gh'])
        with self.assertRaises(subprocess.CalledProcessError):
            module.verify_image('/usr/bin/gh', IMAGE, **ARGS, execute=fail)


if __name__ == '__main__':
    unittest.main()
