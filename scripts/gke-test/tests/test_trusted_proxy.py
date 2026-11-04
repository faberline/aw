import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('trusted_proxy', Path(__file__).resolve().parents[1] / 'trusted_proxy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProxyTests(unittest.TestCase):
    def test_fixed_command_uses_loopback_and_no_shell(self):
        self.assertEqual(module.command('lumen', 'lumen-http', 7373, 7373),
            ['/usr/local/bin/kubectl', '--kubeconfig', '/run/kubeconfig', '--namespace', 'lumen',
             'port-forward', '--address', '127.0.0.1', 'service/lumen-http', '7373:7373'])
        for args in (('--all', 'svc', 7373, 7373), ('ns', 'svc;id', 7373, 7373),
                     ('ns', 'svc', True, 7373), ('ns', 'svc', 0, 7373)):
            with self.subTest(args=args), self.assertRaises(ValueError): module.command(*args)

    def test_restart_and_termination(self):
        calls = []; stopped = {'value': False}
        class Child:
            def __init__(self, run): self.run = run; self.pid = 100 + run; self.terminated = False; self.polls = 0
            def wait(self, timeout=None):
                if self.terminated: return 0
                if self.run == 1: return 1
                stopped['value'] = True
                raise subprocess.TimeoutExpired('kubectl', timeout)
            def poll(self):
                self.polls += 1
                return 1 if self.terminated or self.run == 1 and self.polls > 1 else None
            def terminate(self): self.terminated = True; calls.append('terminate')
            def kill(self): calls.append('kill')
        def launch(argv, **kwargs):
            calls.append((argv, kwargs)); return Child(sum(isinstance(row, tuple) for row in calls))
        def pause(_seconds):
            if sum(isinstance(row, tuple) for row in calls) == 2: stopped['value'] = True
        reports = []
        module.supervise(['/fixed'], 7373, lambda: stopped['value'], launch=launch, pause=pause,
                         owns=lambda pid, port: True, report=reports.append)
        self.assertEqual(sum(isinstance(row, tuple) for row in calls), 2)
        self.assertIn('terminate', calls)
        self.assertEqual(calls[0][1]['env'], {'PATH': '/usr/local/bin', 'HOME': '/work', 'LC_ALL': 'C'})
        self.assertEqual([row['state'] for row in reports], ['ready', 'lost', 'ready', 'lost'])

    def test_ready_requires_the_child_to_own_the_exact_loopback_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory); (proc / 'net').mkdir(); (proc / '10/fd').mkdir(parents=True)
            (proc / 'net/tcp').write_text('header\n 0: 0100007F:1CCD 00000000:0000 0A 0 0 0 0 0 123\n')
            (proc / '10/fd/3').symlink_to('socket:[123]')
            self.assertTrue(module.owns_listener(10, 7373, proc=proc))
            self.assertFalse(module.owns_listener(10, 7374, proc=proc))
            self.assertFalse(module.owns_listener(11, 7373, proc=proc))

    def test_report_failure_still_terminates_child(self):
        class Child:
            pid = 123
            terminated = False
            def poll(self): return None if not self.terminated else 0
            def terminate(self): self.terminated = True
            def wait(self, timeout=None): return 0
        for failure in ('ready', 'lost'):
            child = Child(); stopped = {'value': False}
            def report(event):
                if event['state'] == failure: raise BrokenPipeError('closed report')
                stopped['value'] = True
            with self.subTest(failure=failure), self.assertRaises(BrokenPipeError):
                module.supervise(['/fixed'], 7373, lambda: stopped['value'],
                                 launch=lambda *args, **kwargs: child, pause=lambda _: None,
                                 owns=lambda *_: True, report=report)
            self.assertTrue(child.terminated)


if __name__ == '__main__':
    unittest.main()
