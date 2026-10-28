import base64
import importlib.util
import hashlib
import uuid
import tempfile
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('native_transport', Path(__file__).parents[1] / 'scripts/native_transport.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

REPO = 'faberline/aw'
P = f'repos/{REPO}/'
SHA = 'a' * 40
FREE = {'protocol': 'aw-issue-guard-v1', 'repo': REPO, 'status': 'free', 'generation': '2b2b2b2b-0000-4000-8000-000000000000', 'owner': None}
HELD = {'protocol': 'aw-issue-guard-v1', 'repo': REPO, 'status': 'held', 'generation': '1b1b1b1b-0000-4000-8000-000000000000', 'owner': '3b3b3b3b-0000-4000-8000-000000000000'}
def enc(value):
    return base64.b64encode(json.dumps(value).encode()).decode()
def env(value):
    return {'content': [{'type': 'text', 'text': 'Action completed.'}], 'structuredContent': value, 'isError': False}
def issue():
    return {'number': 7, 'html_url': f'https://github.com/{REPO}/issues/7', 'state': 'open', 'labels': [{'name': 'todo'}]}
class Guard:
    def __init__(self): self.calls = []
    def verify_for(self, repo, required): self.calls.append(('verify', repo, required))
    def mark_mutation_for(self, repo, required): self.calls.append(('mark', repo, required))
class TransportTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.results = []
        self.guard = Guard()
        def call(name, args):
            self.calls.append((name, args))
            result = self.results.pop(0)
            if isinstance(result, Exception): raise result
            return result
        self.t = native.NativeTransport(REPO, call, self.guard)
    def test_get_raw_rest_json(self):
        self.results = [env({'content': json.dumps([issue()])})]
        self.assertEqual(self.t('GET', P + 'issues?state=open&per_page=100&page=2'), [issue()])
        self.assertEqual(self.calls[0][1]['url'], 'https://api.github.com/' + P + 'issues?state=open&per_page=100&page=2')
    def test_guard_exact_ref_sha_and_transition(self):
        self.results = [env({'content': enc(FREE), 'encoding': 'base64', 'sha': SHA}), env({'content_sha': 'b' * 40})]
        got = self.t('GET', P + 'contents/issue-work-guard.json?ref=aw-coordination')
        self.assertEqual(got['sha'], SHA)
        self.t('PUT', P + 'contents/issue-work-guard.json', {'message': 'aw issue guard transition', 'branch': 'aw-coordination', 'sha': SHA, 'content': enc(HELD)})
        self.assertEqual(self.calls[0][1], {'repository_full_name': REPO, 'path': 'issue-work-guard.json', 'ref': 'aw-coordination', 'encoding': 'base64'})
        self.assertEqual(self.calls[1][1]['sha'], SHA)
        self.assertEqual(json.loads(self.calls[1][1]['content']), HELD)
    def test_guard_wrong_ref_and_sha_refused(self):
        with self.assertRaises(native.Refused): self.t('GET', P + 'contents/issue-work-guard.json?ref=main')
        with self.assertRaises(native.Refused): self.t('PUT', P + 'contents/issue-work-guard.json', {'message': 'aw issue guard transition', 'branch': 'aw-coordination', 'sha': SHA, 'content': enc(HELD)})
        self.assertEqual(self.calls, [])
    def test_provider_error_preserves_envelope(self):
        error = {'isError': True, 'content': [{'type': 'text', 'text': 'provider failed'}]}
        self.results = [error]
        with self.assertRaises(native.ProviderError) as caught: self.t('GET', P + 'issues/7')
        self.assertIs(caught.exception.envelope, error)
    def test_missing_and_provider_json_error_fail_closed(self):
        self.results = [{'content': []}, env({'content': '{"message":"Not Found","documentation_url":"x"}'})]
        with self.assertRaises(native.Refused): self.t('GET', P + 'issues/7')
        with self.assertRaises(native.ProviderError): self.t('GET', P + 'issues/7')
    def test_write_uncertainty_does_not_retry(self):
        self.results = [env({'content': json.dumps(issue())}), RuntimeError('timeout')]
        with self.assertRaisesRegex(RuntimeError, 'timeout'): self.t('PATCH', P + 'issues/7', {'labels': ['todo']})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.guard.calls[-1], ('mark', REPO, True))
    def test_update_uses_snapshot_identity_and_raw_readback(self):
        self.results = [env({'content': json.dumps(issue())}), env({'number': 7, 'html_url': issue()['html_url']}), env({'content': json.dumps(issue())})]
        self.assertEqual(self.t('PATCH', P + 'issues/7', {'labels': ['todo']}), issue())
        self.assertEqual(len(self.calls), 3)
    def test_cross_repo_body_state_and_closed_target_refused(self):
        for method, path, body in [('GET', 'repos/other/aw/issues/7', None), ('PATCH', P+'issues/7', {'body': 'x'}), ('PATCH', P+'issues/7', {'state': 'closed'})]:
            with self.assertRaises(native.Refused): self.t(method, path, body)
        closed = dict(issue(), state='closed')
        self.results = [env({'content': json.dumps(closed)})]
        with self.assertRaises(native.Refused): self.t('PATCH', P+'issues/7', {'labels':['todo']})
        self.assertEqual(len(self.calls), 1)
    def test_milestone_catalog_is_explicitly_unsupported(self):
        with self.assertRaises(native.Unsupported): self.t('GET', P+'milestones?state=all')
        self.assertEqual(self.calls, [])
    def test_bool_patch_identity_is_refused_without_readback(self):
        self.results = [env({'content': json.dumps(issue())}), env({'number': True, 'html_url': f'https://github.com/{REPO}/issues/1'})]
        self.results[0] = env({'content': json.dumps(dict(issue(), number=1, html_url=f'https://github.com/{REPO}/issues/1'))})
        with self.assertRaises(native.Refused): self.t('PATCH', P+'issues/1', {'labels':['todo']})
        self.assertEqual(len(self.calls), 2)

    def test_real_shared_guard_acquire_write_verify_and_release(self):
        connector_spec = importlib.util.spec_from_file_location('native_fixture_connector', Path(__file__).parents[1] / 'scripts/connector.py')
        connector = importlib.util.module_from_spec(connector_spec)
        connector_spec.loader.exec_module(connector)
        coord = connector.queue.coordination
        state = {'guard': FREE.copy(), 'issue': issue(), 'guard_writes': [], 'issue_writes': 0}
        def file_sha():
            return hashlib.sha1(json.dumps(state['guard'], sort_keys=True).encode()).hexdigest()
        def call(name, args):
            if name.endswith('_fetch_file'):
                if args['path'] == 'issue-work-leases.json':
                    return env({'content': enc({'protocol': 'aw-issue-leases-v1', 'repo': REPO,
                        'generation': str(uuid.uuid4()), 'leases': []}), 'encoding': 'base64', 'sha': 'c' * 40,
                        'display_url': f'https://github.com/{REPO}/blob/aw-coordination/issue-work-leases.json'})
                self.assertEqual(args['ref'], 'aw-coordination')
                return env({'content': enc(state['guard']), 'encoding': 'base64', 'sha': file_sha()})
            if name.endswith('_update_file'):
                self.assertEqual(args['sha'], file_sha())
                old = state['guard'].copy()
                state['guard'] = json.loads(args['content'])
                state['guard_writes'].append((old, state['guard'].copy()))
                return env({'content_sha': file_sha()})
            if name.endswith('_fetch'):
                return env({'content': json.dumps(state['issue'])})
            if name.endswith('_update_issue'):
                self.assertEqual(state['guard']['status'], 'held')
                self.assertEqual(coord._active.held, state['guard'])
                state['issue_writes'] += 1
                state['issue']['labels'] = [{'name': x} for x in args['labels']]
                return env({'number': 7, 'html_url': state['issue']['html_url']})
            self.fail(name)
        transport = native.NativeTransport(REPO, call, coord)
        owner = str(uuid.uuid4())
        with coord.shared_guard(REPO, required=True, mode='github', owner=owner, request=transport) as acquired:
            self.assertEqual(state['guard']['owner'], owner)
            acquired.verify()
            connector.Client(REPO, transport).patch(7, labels=['todo'])
            acquired.verify()
        self.assertEqual(state['guard']['status'], 'free')
        self.assertEqual(state['issue_writes'], 1)
        self.assertEqual([(a['status'], b['status']) for a, b in state['guard_writes']], [('free', 'held'), ('held', 'free')])
        self.assertIsNone(coord._active)

    def test_native_guard_and_ledger_round_trip(self):
        connector_spec = importlib.util.spec_from_file_location('ledger_connector', Path(__file__).parents[1] / 'scripts/connector.py')
        connector = importlib.util.module_from_spec(connector_spec)
        connector_spec.loader.exec_module(connector)
        coord = connector.queue.coordination
        files = {'issue-work-guard.json': FREE.copy(), 'issue-work-leases.json':
            {'protocol': 'aw-issue-leases-v1', 'repo': REPO, 'generation': str(uuid.uuid4()), 'leases': []}}
        def sha(path):
            return hashlib.sha1(json.dumps(files[path], sort_keys=True).encode()).hexdigest()
        def call(name, args):
            path = args['path']
            if name.endswith('_fetch_file'):
                return env({'content': enc(files[path]), 'encoding': 'base64', 'sha': sha(path),
                            'display_url': f'https://github.com/{REPO}/blob/aw-coordination/{path}'})
            if name.endswith('_update_file'):
                self.assertEqual(args['sha'], sha(path))
                files[path] = json.loads(args['content'])
                return env({'content_sha': sha(path)})
            self.fail(name)
        transport = native.NativeTransport(REPO, call, coord)
        ledger = connector.leases.Ledger(REPO, coord, transport)
        with coord.shared_guard(REPO, required=True, mode='github', owner=str(uuid.uuid4()), request=transport):
            ledger.transition(7, 'local-owner', 'claiming')
            with self.assertRaisesRegex(ValueError, 'persistent worker lease'):
                ledger.require_unleased(7)
            ledger.transition(7, 'local-owner', 'coding', previous='claiming')
            ledger.transition(7, 'local-owner', 'submitted', previous='coding')
            ledger.release(7, 'local-owner')
        self.assertEqual(files['issue-work-leases.json']['leases'], [])
        self.assertEqual(files['issue-work-guard.json']['status'], 'free')

    def _real_run_fixture(self, uncertain=False, fail_target_read=False):
        connector_spec = importlib.util.spec_from_file_location('native_run_connector', Path(__file__).parents[1] / 'scripts/connector.py')
        connector = importlib.util.module_from_spec(connector_spec)
        connector_spec.loader.exec_module(connector)
        coord = connector.queue.coordination
        state = {'guard': FREE.copy(), 'issue': issue(), 'writes': [], 'issue_attempts': 0, 'issue_reads': 0}
        def sha():
            return hashlib.sha1(json.dumps(state['guard'], sort_keys=True).encode()).hexdigest()
        def call(name, args):
            if name.endswith('_fetch_file'):
                if args['path'] == 'issue-work-leases.json':
                    return env({'content': enc({'protocol': 'aw-issue-leases-v1', 'repo': REPO,
                        'generation': str(uuid.uuid4()), 'leases': []}), 'encoding': 'base64', 'sha': 'c' * 40,
                        'display_url': f'https://github.com/{REPO}/blob/aw-coordination/issue-work-leases.json'})
                return env({'content': enc(state['guard']), 'encoding': 'base64', 'sha': sha()})
            if name.endswith('_update_file'):
                self.assertEqual(args['sha'], sha())
                state['guard'] = json.loads(args['content'])
                state['writes'].append(state['guard']['status'])
                return env({'content_sha': sha()})
            if name.endswith('_fetch'):
                state['issue_reads'] += 1
                if fail_target_read and state['issue_reads'] == 3:
                    raise RuntimeError('native target read failed')
                return env({'content': json.dumps(state['issue'])})
            if name.endswith('_update_issue'):
                state['issue_attempts'] += 1
                self.assertEqual(state['guard']['status'], 'held')
                if uncertain:
                    raise RuntimeError('uncertain provider write')
            self.fail(name)
        return connector, native.NativeTransport(REPO, call, coord), state

    def test_connector_spec_refusal_releases_real_guard(self):
        connector, transport, state = self._real_run_fixture()
        with tempfile.NamedTemporaryFile(mode='w') as source:
            source.write('## Goal\nA\n## Acceptance\nB\n## Never\nC\n## Gate\nD\n')
            source.flush()
            with self.assertRaises(native.Unsupported):
                connector.run(REPO, ['spec', '7', source.name], transport, str(uuid.uuid4()))
        self.assertEqual(state['writes'], ['held', 'free'])
        self.assertEqual(state['guard']['status'], 'free')
        self.assertEqual(state['issue_attempts'], 0)
        self.assertIsNone(connector.queue.coordination._active)

    def test_failed_native_target_read_releases_real_guard(self):
        connector, transport, state = self._real_run_fixture(fail_target_read=True)
        with self.assertRaisesRegex(RuntimeError, 'native target read failed'):
            connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
        self.assertEqual(state['issue_reads'], 3)
        self.assertEqual(state['writes'], ['held', 'free'])
        self.assertEqual(state['guard']['status'], 'free')
        self.assertEqual(state['issue_attempts'], 0)
        self.assertIsNone(connector.queue.coordination._active)

    def test_uncertain_real_guarded_write_stays_held(self):
        connector, transport, state = self._real_run_fixture(uncertain=True)
        with self.assertRaisesRegex(RuntimeError, 'uncertain provider write'):
            connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
        self.assertEqual(state['writes'], ['held'])
        self.assertEqual(state['guard']['status'], 'held')
        self.assertEqual(state['issue_attempts'], 1)
        self.assertIsNone(connector.queue.coordination._active)

    def test_comment_is_unsupported_without_provider_call(self):
        with self.assertRaises(native.Unsupported): self.t('POST', P+'issues/7/comments', {'body': 'note'})
        self.assertEqual(self.calls, [])

    def test_no_guard_no_write(self):
        class Deny:
            def verify_for(self, *args, **kwargs): raise RuntimeError('no guard')
        self.t.coordination = Deny()
        with self.assertRaisesRegex(RuntimeError, 'no guard'): self.t('POST', P+'issues', {'title': 't', 'body': 'b', 'milestone': 1, 'labels': ['priority:p1']})
        self.assertEqual(self.calls, [])

if __name__ == '__main__': unittest.main()
