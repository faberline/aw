import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('trusted_roster', Path(__file__).resolve().parents[1] / 'trusted_roster.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TrustedRosterTests(unittest.TestCase):
    def test_exact_single_or_multiple_roster(self):
        self.assertEqual(module.verify_roster('e2e::operator::journey: test\n\n1 test, 0 benchmarks\n',
                                              ['e2e::operator::journey']), ['e2e::operator::journey'])
        self.assertEqual(module.verify_roster('b: test\na: test\n\n2 tests, 0 benchmarks\n', ['a', 'b']), ['a', 'b'])

    def test_zero_missing_duplicate_extra_or_non_test_entries_fail(self):
        for output, expected in (('0 tests, 0 benchmarks\n', []),
            ('0 tests, 0 benchmarks\n', ['a']), ('a: test\n1 test, 0 benchmarks\n', ['b']),
            ('a: test\na: test\n2 tests, 0 benchmarks\n', ['a', 'b']),
            ('a: test\nb: test\n2 tests, 0 benchmarks\n', ['a']),
            ('a: benchmark\n1 test, 0 benchmarks\n', ['a']),
            ('a: test\n1 test, 1 benchmark\n', ['a']),
            ('a: test\n1 test, 0 benchmarks\n', ['a', 'a'])):
            with self.subTest(output=output, expected=expected), self.assertRaises(ValueError):
                module.verify_roster(output, expected)

    def test_gke_list_requires_every_suite_test_to_be_selected(self):
        one = 'a: test\n\n1 test, 0 benchmarks\n'
        self.assertEqual(module.verify_gke_suite_rosters(one, one, ['a']), ['a'])
        full = 'a: test\nb: test\n\n2 tests, 0 benchmarks\n'
        with self.assertRaises(ValueError): module.verify_gke_suite_rosters(full, one, ['a', 'b'])

    def test_suite_requires_every_successful_nonzero_result(self):
        ok = ('running 1 test\ntest a ... ok\n\n'
              'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 2 filtered out; finished in 0.00s\n')
        self.assertEqual(module.verify_suite_execution(ok, 0, ['a']), ['a'])
        for output, code in ((ok, 1), (ok.replace('running 1 test', 'running 0 tests'), 0),
                             (ok.replace('1 passed', '0 passed'), 0), (ok.replace('0 ignored', '1 ignored'), 0),
                             (ok.replace('test a ... ok', 'test b ... ok'), 0), (ok + 'extra\n', 0)):
            with self.subTest(output=output, code=code), self.assertRaises(ValueError):
                module.verify_suite_execution(output, code, ['a'])
        both = ('running 2 tests\ntest a ... ok\ntest b ... ok\n\n'
                'test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 1.0s\n')
        self.assertEqual(module.verify_suite_execution(both, 0, ['a', 'b']), ['a', 'b'])
        with self.assertRaises(ValueError):
            module.verify_suite_execution(both.replace('test b ... ok', 'test a ... ok'), 0, ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
