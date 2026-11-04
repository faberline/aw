"""GKE script checkout and reusable workflow must use the same immutable commit."""
import unittest

import test_readiness

r = test_readiness.r
SHA = 'a' * 40


def workflow(value):
    return ('name: GKE\njobs:\n  test:\n'
            '    uses: faberline/aw/.github/workflows/gke-test.yml@' + SHA + '\n'
            + value)


class GkeRefBindingTests(unittest.TestCase):
    def test_literal_matching_ref_is_accepted(self):
        ref = 'faberline/aw/.github/workflows/gke-test.yml@' + SHA
        for value in (SHA, '"' + SHA + '"', "'" + SHA + "'"):
            self.assertEqual(r.literal_uses(workflow('    with:\n      aw-ref: ' + value + '\n')), [ref])

    def test_missing_mutable_dynamic_or_mismatched_ref_is_refused(self):
        for value in ('', '    with: {}\n', '    with:\n      aw-ref: main\n',
                      '    with:\n      aw-ref: ' + 'b' * 40 + '\n',
                      '    with:\n      aw-ref: ${{ github.sha }}\n',
                      '    with:\n      aw-ref: ["' + SHA + '"]\n',
                      '    with:\n      aw-ref: ' + SHA + '\n      aw-ref: ' + SHA + '\n'):
            with self.subTest(value=value), self.assertRaises(r.Refused):
                r.literal_uses(workflow(value))

    def test_repository_case_variants_cannot_bypass_binding(self):
        source=workflow('    with:\n      aw-ref: ' + 'b' * 40 + '\n')
        for spelling in ('Faberline/aw', 'faberline/AW', 'FABERLINE/AW'):
            with self.subTest(spelling=spelling), self.assertRaises(r.Refused):
                r.literal_uses(source.replace('faberline/aw', spelling))

    def test_other_reusable_workflows_are_unaffected(self):
        use = 'example/tool/.github/workflows/test.yml@' + SHA
        self.assertEqual(r.literal_uses('jobs:\n  test:\n    uses: ' + use + '\n'), [use])


if __name__ == '__main__':
    unittest.main()
