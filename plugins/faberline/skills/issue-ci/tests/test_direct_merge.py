"""Controls for one guarded merge-and-close execution."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
        self.merge_guard = SimpleNamespace(validate=lambda intent, policy:intent['payload'])
        modules = {'guarded_close': self.guard, 'prepared_status': self.prepared,
                   'per_pr_checkpoint': self.per_pr, 'post_merge_observation': self.observation,
                   'merge_guard': self.merge_guard}
        self.review = SimpleNamespace(need=self.need, sibling=lambda n: modules[n],
            sha=lambda value: isinstance(value, str) and len(value) == 40,
            r=SimpleNamespace(canonical=lambda x: json.dumps(x, sort_keys=True), digest=lambda x:'d'*64))
        self.intent['sha256']='e'*64
        self.intent['payload']['owner']='f'*56
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
        def merge(repo, number, data):
            value=self.api(f'repos/{repo}/pulls/{number}/merge', method='PUT', data=data)
            return {'state':'merged','value':value} if value['merged'] else {'state':'refused','http_status':405}
        with patch.object(m,'merge_http',side_effect=merge):
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
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(p.run()['state'], 'held_after_pending')
                self.assertFalse(any(method == 'PATCH' for method, _, _ in p.writes))
                self.assertFalse(p.released)
                self.assertEqual(m.REFUSAL_MARKER in out.getvalue(), failure == 'merge_refused')

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

    def test_http_only_accepts_explicit_terminal_responses(self):
        for code,rc,body,state in [(200,0,{'merged':True,'sha':MERGE},'merged'),
                                   (405,1,{'message':'not mergeable'},'refused'),
                                   (409,1,{'message':'head changed'},'refused')]:
            with self.subTest(code=code),patch.object(m.subprocess,'run',return_value=SimpleNamespace(
                    returncode=rc,stdout=f'HTTP/2.0 {code} Result\r\nContent-Type: application/json\r\n\r\n'+json.dumps(body))):
                self.assertEqual(m.merge_http('faberline/test',5,{'sha':HEAD})['state'],state)
        for response in [SimpleNamespace(returncode=1,stdout=''),
                         SimpleNamespace(returncode=1,stdout='HTTP/2.0 500 Error\r\n\r\n{}'),
                         SimpleNamespace(returncode=0,stdout='HTTP/2.0 405 Error\r\n\r\n{"message":"no"}'),
                         SimpleNamespace(returncode=0,stdout='HTTP/2.0 200 OK\r\n\r\n{}')]:
            with self.subTest(response=response),patch.object(m.subprocess,'run',return_value=response):
                with self.assertRaisesRegex(RuntimeError,'direct_response_unknown'):
                    m.merge_http('faberline/test',5,{'sha':HEAD})

    def test_refusal_must_come_from_exact_authenticated_attempt_and_guard(self):
        p=Provider(); record=m.refusal_record(p.intent,p.claim['policy'],405,p.merge_guard,p.review)
        p.review.r.records=lambda log,marker:[json.loads(line[len(marker):]) for line in log.splitlines()
                                            if line.startswith(marker)]
        line=m.REFUSAL_MARKER+json.dumps(record)
        p.merge_guard.authenticate_native_log=lambda *args: (p.claim['policy'],line)
        args=(p,'faberline/test',p.doc,BASE,1,1,p.intent,lambda *args:'',p.merge_guard,p.review)
        self.assertEqual(m.find_refusal(*args),record)
        p.merge_guard.authenticate_native_log=lambda *args:(p.claim['policy'],'')
        self.assertIsNone(m.find_refusal(*args))
        p.merge_guard.authenticate_native_log=lambda *args:(p.claim['policy'],line+'\n'+line)
        with self.assertRaisesRegex(RuntimeError,'direct_refusal_duplicate'):
            m.find_refusal(*args)
        bad=json.loads(json.dumps(record));bad['payload']['owner']='0'*56
        p.merge_guard.authenticate_native_log=lambda *args:(p.claim['policy'],m.REFUSAL_MARKER+json.dumps(bad))
        self.assertIsNone(m.find_refusal(*args))
        bad=json.loads(json.dumps(record));bad['sha256']='wrong'
        p.merge_guard.authenticate_native_log=lambda *args:(p.claim['policy'],m.REFUSAL_MARKER+json.dumps(bad))
        with self.assertRaisesRegex(RuntimeError,'direct_refusal_unknown'):
            m.find_refusal(*args)


if __name__ == '__main__':
    unittest.main()
