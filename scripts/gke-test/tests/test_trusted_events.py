import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('trusted_events', Path(__file__).resolve().parents[1] / 'trusted_events.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NETWORK, MARKER, TEST = 'a' * 64, 'b' * 64, 'c' * 64


def events():
    plan = [('connect', MARKER), ('disconnect', MARKER), ('connect', TEST),
            ('disconnect', TEST), ('connect', MARKER), ('disconnect', MARKER)]
    return [{'Type': 'network', 'Action': action, 'Actor': {'ID': NETWORK, 'Attributes': {'container': container}},
             'timeNano': index + 1} for index, (action, container) in enumerate(plan)]


class EventTests(unittest.TestCase):
    def verify(self, rows):
        return module.verify_interval(rows, network_id=NETWORK, marker_id=MARKER, test_id=TEST)

    def test_exact_barriers_and_test_membership(self):
        self.assertEqual(self.verify(events()), {'network_id': NETWORK, 'start_ns': 2, 'end_ns': 5})

    def test_missing_reordered_foreign_or_duplicate_events_fail(self):
        for problem in ('missing', 'foreign', 'reordered', 'time', 'wrong-network', 'wrong-type', 'actor-missing'):
            rows = copy.deepcopy(events())
            if problem == 'missing': rows.pop(2)
            if problem == 'foreign': rows[2]['Actor']['Attributes']['container'] = 'd' * 64
            if problem == 'reordered': rows[2], rows[3] = rows[3], rows[2]
            if problem == 'time': rows[3]['timeNano'] = rows[2]['timeNano']
            if problem == 'wrong-network': rows[2]['Actor']['ID'] = 'd' * 64
            if problem == 'wrong-type': rows[2]['Type'] = 'container'
            if problem == 'actor-missing': rows[2]['Actor'] = None
            with self.subTest(problem=problem), self.assertRaises(ValueError): self.verify(rows)


if __name__ == '__main__':
    unittest.main()
