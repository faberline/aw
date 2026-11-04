import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('docker_snapshot', Path(__file__).resolve().parents[1] / 'trusted_docker_snapshot.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
CID = 'a' * 64


class SnapshotTests(unittest.TestCase):
    def test_valid_snapshot_binds_exact_id(self):
        with patch.object(module, '_capture', return_value=('{"Id":"' + CID + '"}').encode()) as capture:
            self.assertEqual(module.inspect(CID)['Id'], CID)
        self.assertEqual(capture.call_args.args[0], ['/usr/bin/docker', 'inspect', '--format', '{{json .}}', CID])

    def test_invalid_ids_duplicate_json_and_wrong_identity_fail(self):
        for value in ('bad', CID.upper(), CID + 'x'):
            with self.subTest(value=value), self.assertRaises(ValueError): module.inspect(value)
        for data in (b'{"Id":"bad"}', b'{"Id":"a","Id":"a"}', b'{"x":NaN}', b'{"x":1e9999}', b'[]'):
            with self.subTest(data=data), patch.object(module, '_capture', return_value=data), self.assertRaises(ValueError):
                module.inspect(CID)

    def test_network_snapshot_uses_fixed_local_command(self):
        with patch.object(module, '_capture', return_value=('{"Id":"' + CID + '"}').encode()) as capture:
            self.assertEqual(module.inspect_network(CID)['Id'], CID)
        self.assertEqual(capture.call_args.args[0], ['/usr/bin/docker', 'network', 'inspect', '--format', '{{json .}}', CID])
        with self.assertRaises(ValueError): module.inspect_network('not-a-network-id')
        for raw in (b'{"Id":"bad"}', b'{"Id":"a","Id":"a"}'):
            with self.subTest(raw=raw), patch.object(module, '_capture', return_value=raw), self.assertRaises(ValueError):
                module.inspect_network(CID)

    def test_output_limit_and_timeout_are_enforced(self):
        with self.assertRaisesRegex(RuntimeError, 'discard runner'):
            module._capture([sys.executable, '-c', 'import sys;sys.stdout.write("x"*1100000)'], 5)
        with self.assertRaisesRegex(RuntimeError, 'discard runner'):
            module._capture([sys.executable, '-c', 'import time;time.sleep(2)'], 0.1)

    def test_reaped_nonzero_child_is_not_signalled(self):
        with patch.object(module.os, 'killpg', side_effect=AssertionError('reaped child signalled')):
            with self.assertRaises(ValueError):
                module._capture([sys.executable, '-c', 'raise SystemExit(1)'], 5)

    def test_exited_leader_with_open_output_requires_runner_disposal(self):
        code = 'import os,time; pid=os.fork(); os._exit(0) if pid else time.sleep(2)'
        with self.assertRaisesRegex(RuntimeError, 'discard runner'):
            module._capture([sys.executable, '-c', code], 0.5)
