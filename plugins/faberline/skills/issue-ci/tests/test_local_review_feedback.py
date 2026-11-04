import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('local_feedback', Path(__file__).parents[1] / 'scripts/development_pipeline.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
HEAD, BASE = 'a' * 40, 'b' * 40


class LocalReviewFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.config = {'local_review_feedback': {'version': 1, 'reviewer_ids': [42]}}

    def comment(self, number=3, actor=42, outcome='fail', head=HEAD, base=BASE, evidence='Found a defect.'):
        return {'id': number, 'user': {'id': actor}, 'updated_at':'2026-11-04T00:00:00Z',
                'body': f'<!-- issue-review:7:{head}:{base} -->\nReview: {outcome} head {head} base {base}.\n{evidence}\nLifecycle unchanged; no merge.'}

    def read(self, comments, config=None):
        return module.local_review_failure('o/r', 7, 8, HEAD, BASE, comments,
                                           self.config if config is None else config)

    def test_only_authenticated_exact_head_base_failure_is_returned(self):
        value = self.read([self.comment()])
        self.assertEqual(value['comment_id'], 3)
        self.assertEqual(value['url'], 'https://github.com/o/r/pull/8#issuecomment-3')
        self.assertIsNone(self.read([self.comment(head='c' * 40)]))
        self.assertIsNone(self.read([self.comment(base='c' * 40)]))
        self.assertIsNone(self.read([self.comment(outcome='pass')]))
        self.assertIsNone(self.read([self.comment(outcome='block')]))

    def test_later_pass_does_not_erase_failure_for_same_head_and_base(self):
        result = self.read([self.comment(), self.comment(number=4, outcome='pass')])
        self.assertEqual(result['comment_id'], 3)

    def test_untrusted_malformed_and_unordered_receipts_refuse(self):
        self.assertIsNone(self.read([self.comment(actor=99)]))
        self.assertEqual(self.read([self.comment(actor=99),self.comment(number=4)])['comment_id'],4)
        for comments in ([self.comment(evidence='')],
                         [self.comment(), self.comment()], [{'id': 2, 'body': None}]):
            with self.subTest(comments=comments), self.assertRaises(RuntimeError):
                self.read(comments)

    def test_feature_requires_explicit_valid_policy(self):
        self.assertIsNone(self.read([self.comment()], {}))
        for settings in ({'version': True, 'reviewer_ids': [42]}, {'version': 1, 'reviewer_ids': []},
                         {'version': 1, 'reviewer_ids': [42, 42]}):
            with self.subTest(settings=settings), self.assertRaises(RuntimeError):
                self.read([], {'local_review_feedback': settings})


if __name__ == '__main__':
    unittest.main()
