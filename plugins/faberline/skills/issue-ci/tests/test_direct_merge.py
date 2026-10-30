"""Controls for one guarded merge-and-close execution."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('direct_merge_test',
    Path(__file__).parents[1] / 'scripts/direct_merge.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HEAD, BASE, MERGE = 'a' * 40, 'b' * 40, 'c' * 40


class Provider:
    def __init__(self):
        self.writes = []
        self.state = 'open'
        self.fail = None
        self.request = None
        self.review_count = 0
        self.ownership_changed = False
        self.released = False
        self.intent = {'payload': {'repo': 'faberline/test', 'pr': 5, 'head': HEAD,
                                   'issue': 7, 'run': 1, 'attempt': 1}}
        self.claim = {'policy': {'source': BASE}, 'intent': self.intent}
        self.context = {'repo': 'faberline/test', 'pr': 5, 'head': HEAD,
                        'base': BASE, 'base_ref': 'main'}
        self.receipt = {'payload': {'context': self.context}}
        self.checkpoint = {'payload': {'checkpoint': {'ownership_events': {'seal': 1}}}}
        self.doc = {'config': {}}
        self.guard = SimpleNamespace(require_current_policy=lambda *a: None,
            guard_identity=lambda *a: None, release=self.release)
        self.prepared = SimpleNamespace(MARKER='PREPARED ',
            target_identity=lambda *a: 'target', build_guarded=lambda *a: {'ready': True})
        self.per_pr = SimpleNamespace(MARKER='BOUND ', build=lambda *a, **kw: {'bound': True},
                                      verify_status=lambda *a: None)
        self.observation = SimpleNamespace(observe=self.observe, ownership_history=self.history,
                                          no_open_linked_prs=lambda *a: None)
        modules = {'guarded_close': self.guard, 'prepared_status': self.prepared,
                   'per_pr_checkpoint': self.per_pr, 'post_merge_observation': self.observation}
        self.review = SimpleNamespace(need=self.need, sibling=lambda n: modules[n],
            sha=lambda value: isinstance(value, str) and len(value) == 40,
            r=SimpleNamespace(canonical=lambda x: json.dumps(x, sort_keys=True)))
        self.native = SimpleNamespace(review=self.review, policy=lambda d: {'status_context':'claude/verified-merge'},
            qualification=lambda *a: 7, base_rules=lambda *a: None, provider_pull=self.pull,
            binding=self.binding)

    @staticmethod
    def need(condition, reason):
        if not condition:
            raise RuntimeError(reason)

    def pull(self, *args):
        return {'state': 'OPEN', 'headRefOid': HEAD, 'baseRefOid': BASE,
                'autoMergeRequest': self.request}

    def binding(self, pull, context):
        self.need(pull['headRefOid'] == context['head'] and pull['baseRefOid'] == context['base'],
                  'changed')

    def verify(self, *args):
        self.review_count += 1
        if self.fail == 'review_changed' and self.review_count > 1:
            return {'payload': {'context': {}}}
        return self.receipt

    def prepare(self, *args):
        return self.checkpoint

    def api(self, path, method='GET', data=None):
        self.writes.append((method, path, data))
        if self.fail == 'merge_unknown' and path.endswith('/merge'):
            raise TimeoutError('unknown merge outcome')
        if path.endswith('/merge'):
            if self.fail == 'merge_refused':
                return {'merged': False, 'sha': MERGE}
            self.state = 'merged'
            return {'merged': True, 'sha': MERGE}
        if method == 'PATCH':
            if self.fail == 'close_unknown':
                raise TimeoutError('unknown close outcome')
            self.state = 'closed'
            return {'number': 7, 'state': 'closed'}
        return {'id': len(self.writes)}

    def pages(self, path):
        return []

    def observe(self, api, check):
        self.need(self.state in ('merged', 'closed') and not self.ownership_changed, 'issue_changed')
        return {'state': 'observed_closed' if self.state == 'closed' else 'observed_merged', 'merge': MERGE}

    def history(self, *args):
        self.need(not self.ownership_changed, 'issue_changed')

    def release(self, *args):
        self.released = True

    def run(self):
        return m.merge_and_close(self, 'faberline/test', 5, HEAD, lambda: self.doc,
                                 BASE, 123, self.claim, self.native, self.verify, self.prepare)


class DirectMergeTests(unittest.TestCase):
    def test_confirmed_merge_precedes_exact_issue_close(self):
        p = Provider(); result = p.run()
        self.assertEqual(result['state'], 'closed')
        self.assertEqual([(method, path) for method, path, _ in p.writes], [
            ('POST', f'repos/faberline/test/statuses/{HEAD}'),
            ('POST', f'repos/faberline/test/statuses/{HEAD}'),
            ('PUT', 'repos/faberline/test/pulls/5/merge'),
            ('PATCH', 'repos/faberline/test/issues/7')])
        self.assertEqual(p.writes[2][2]['sha'], HEAD)
        self.assertTrue(p.released)

    def test_refused_or_unknown_merge_never_closes_issue(self):
        for failure in ('merge_refused', 'merge_unknown'):
            p = Provider(); p.fail = failure
            with self.subTest(failure=failure):
                self.assertEqual(p.run()['state'], 'held_after_pending')
                self.assertFalse(any(method == 'PATCH' for method, _, _ in p.writes))
                self.assertFalse(p.released)

    def test_unknown_close_holds_guard(self):
        p = Provider(); p.fail = 'close_unknown'
        self.assertEqual(p.run()['state'], 'held_after_pending')
        self.assertFalse(p.released)

    def test_changed_review_prevents_merge(self):
        p = Provider(); p.fail = 'review_changed'
        self.assertEqual(p.run()['state'], 'held_after_pending')
        self.assertFalse(any(method == 'PUT' for method, _, _ in p.writes))

    def test_existing_auto_merge_request_prevents_any_write(self):
        p = Provider(); p.request = {'owner': 'unknown'}
        with self.assertRaises(RuntimeError):
            p.run()
        self.assertEqual(p.writes, [])

    def test_changed_issue_prevents_close(self):
        p = Provider(); p.ownership_changed = True
        self.assertEqual(p.run()['state'], 'held_after_pending')
        self.assertFalse(any(method == 'PATCH' for method, _, _ in p.writes))
        self.assertFalse(p.released)


if __name__ == '__main__':
    unittest.main()
