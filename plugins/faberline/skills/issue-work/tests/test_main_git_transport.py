"""Transport-only subprocess fixtures; no live repos, guards or product code."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transport_filter', Path(__file__).resolve().parents[1]/'scripts/main_filter_safety.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class GitTransportTests(unittest.TestCase):
    def invoke(self, code, data=None, timeout=1.0, limit=1_000_000):
        original = subprocess.Popen
        calls = []
        def child(command, **kwargs):
            self.assertNotIn('input', kwargs)
            self.assertEqual(kwargs['close_fds'], True)
            self.assertNotEqual(kwargs['stdout'], subprocess.PIPE)
            self.assertNotEqual(kwargs['stderr'], subprocess.PIPE)
            if data is None:
                self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            else:
                self.assertNotEqual(kwargs['stdin'], subprocess.PIPE)
            process = original([sys.executable, '-I', '-B', '-c', code], **kwargs)
            calls.append(process)
            return process
        try:
            with patch.object(helper.subprocess, 'Popen', side_effect=child), patch.object(helper, 'COMMAND_TIMEOUT', timeout), patch.object(helper, 'LIMIT', limit):
                return helper.git('.', 'fixture', input=data)
        finally:
            self.assertTrue(all(p.poll() is not None for p in calls), 'own fixture child must be reaped')

    def test_eof_with_pipe_boundary_inputs(self):
        for size in (0,11,8191,8192,8193,10516,16383,16384,16385,65536):
            with self.subTest(size=size):
                data=b'x'*size
                actual=self.invoke('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())',data)
                self.assertEqual(actual,data)

    def test_dual_outputs_do_not_block_input_or_eof(self):
        data=b'x'*65536
        code='import sys; sys.stdout.buffer.write(b"O"*131072); sys.stdout.buffer.flush(); sys.stderr.buffer.write(b"E"*131072); sys.stderr.buffer.flush(); sys.stdout.buffer.write(sys.stdin.buffer.read())'
        self.assertEqual(self.invoke(code,data),b'O'*131072+data)

    def test_nonreader_is_stopped_by_independent_deadline(self):
        started=time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.invoke('import time; time.sleep(10)', b'x'*262144, timeout=.15)
        self.assertLess(time.monotonic()-started,2)

    def test_stdout_and_stderr_overflow_fail_closed_and_stop_own_child(self):
        for stream in ('stdout','stderr'):
            with self.subTest(stream=stream),self.assertRaisesRegex(ValueError,'output bound'):
                self.invoke('import sys,time; sys.'+stream+'.buffer.write(b"X"*131072); sys.'+stream+'.buffer.flush(); time.sleep(10)',limit=4096)

    def test_final_output_bound_checked_after_child_exit(self):
        with self.assertRaisesRegex(ValueError,'output bound'):
            self.invoke('import sys; sys.stdout.buffer.write(b"X"*131072)',limit=4096)

    def test_oversized_input_refuses_before_spawn(self):
        with patch.object(helper.subprocess,'Popen',side_effect=AssertionError('must not spawn')),patch.object(helper,'LIMIT',8):
            with self.assertRaisesRegex(ValueError,'input bound'):
                helper.git('.', 'fixture',input=b'x'*9)

    def test_optional_missing_config_and_other_exit_failures_preserved(self):
        original=subprocess.Popen
        def launch(command,**kwargs):
            return original([sys.executable,'-I','-B','-c','raise SystemExit(1)'],**kwargs)
        with patch.object(helper.subprocess,'Popen',side_effect=launch):
            self.assertEqual(helper.git('.','config',optional=True),b'')
            with self.assertRaisesRegex(ValueError,'Git read failed'):
                helper.git('.','config')

    def test_late_completed_result_does_not_bypass_deadline(self):
        class Completed:
            def poll(self):return 0
            def kill(self):raise AssertionError('completed child must not be signalled')
        with patch.object(helper.subprocess,'Popen',return_value=Completed()),patch.object(helper.time,'monotonic',side_effect=[0,2]),patch.object(helper,'COMMAND_TIMEOUT',1):
            with self.assertRaises(subprocess.TimeoutExpired):
                helper.git('.','fixture')

    def test_temporary_guard_descriptor_is_not_inherited(self):
        with tempfile.TemporaryFile() as guard:
            os.set_inheritable(guard.fileno(),True)
            # The child inspects only the deliberately known fixture descriptor.
            # fstat must fail after close_fds even though it was inheritable.
            code='import os;\ntry: os.fstat('+str(guard.fileno())+'); print("inherited")\nexcept OSError: print("closed")'
            self.assertEqual(self.invoke(code),b'closed\n')

    def test_exact_synthetic_git_inventory(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',GIT_ATTR_NOSYSTEM='1'):
            subprocess.run(['git','init','-q',tmp],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2)
            paths=[('p/'+str(i).zfill(6)+'-'+'x'*26).encode() for i in range(292)]
            paths[0]+=b'xxxx';data=b'\0'.join(paths)+b'\0'
            self.assertEqual(len(data),10516)
            output=helper.git(tmp,'check-attr','-z','--stdin','filter',input=data)
            self.assertEqual(len(output),16064)
            self.assertEqual(output,b''.join(p+b'\0filter\0unspecified\0' for p in paths))


if __name__=='__main__':unittest.main()
