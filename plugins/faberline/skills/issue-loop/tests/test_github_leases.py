import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import uuid

ROOT = Path(__file__).parents[1] / 'scripts'
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
lease_module = load('github_leases')
guard = load('github_guard')
REPO = 'faberline/aw'

class Store:
    def __init__(self):
        self.guard = {'protocol': guard.PROTOCOL, 'repo': REPO, 'status': 'free', 'generation': str(uuid.uuid4()), 'owner': None}
        self.ledger = {'protocol': lease_module.PROTOCOL, 'repo': REPO, 'generation': str(uuid.uuid4()), 'leases': []}
        self.fail_ledger = False
        self.writes = 0
    def __call__(self, method, path, payload=None):
        name = 'guard' if 'issue-work-guard.json' in path else 'ledger'
        value = getattr(self, name)
        sha = hashlib.sha1(guard.encode(value).encode()).hexdigest()
        if method == 'GET':
            return {'type': 'file', 'path': guard.PATH if name == 'guard' else lease_module.PATH, 'encoding': 'base64', 'sha': sha, 'content': guard.encode(value)}
        assert payload['sha'] == sha
        if name == 'ledger':
            self.writes += 1
            if self.fail_ledger: raise RuntimeError('uncertain write')
        setattr(self, name, guard.decode(payload['content']))
        return {}

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.ledger = lease_module.Ledger(REPO, guard, self.store)
    def held(self):
        return guard.shared_guard(REPO, required=True, request=self.store, owner=str(uuid.uuid4()), mode='github')
    def test_guard_required(self):
        with self.assertRaises(guard.Refused): self.ledger.transition(1, 'secret', 'claiming')
        self.assertEqual(self.store.writes, 0)
    def test_claiming_blocks_planner_and_full_owned_release(self):
        with self.held():
            self.ledger.transition(1, 'secret', 'claiming')
            self.assertNotIn('secret', json.dumps(self.store.ledger))
            with self.assertRaisesRegex(ValueError, 'persistent worker lease'): self.ledger.require_unleased(1)
            self.ledger.transition(1, 'secret', 'coding', previous='claiming')
            self.ledger.transition(1, 'secret', 'submitted', previous='coding')
            with self.assertRaisesRegex(ValueError, 'foreign'): self.ledger.release(1, 'other')
            self.ledger.release(1, 'secret')
            self.ledger.require_unleased(1)
        self.assertEqual(self.store.ledger['leases'], [])
    def test_malformed_missing_and_foreign_refuse(self):
        with self.held():
            self.store.ledger['leases'] = [{'issue': True, 'owner_nonce': 'a'*64, 'phase': 'claiming'}]
            with self.assertRaises(ValueError): self.ledger.read()
            self.store.ledger['leases'] = []
            self.ledger.transition(1, 'secret', 'claiming')
            with self.assertRaises(ValueError): self.ledger.transition(1, 'other', 'coding', previous='claiming')
    def test_missing_ledger_is_not_created(self):
        def missing(method, path, payload=None):
            if 'issue-work-leases.json' in path:
                raise RuntimeError('missing ledger')
            return self.store(method, path, payload)
        ledger = lease_module.Ledger(REPO, guard, missing)
        with self.held():
            with self.assertRaisesRegex(RuntimeError, 'missing ledger'):
                ledger.transition(1, 'secret', 'claiming')
        self.assertEqual(self.store.writes, 0)

    def test_wrong_source_identity_is_refused(self):
        def wrong(method, path, payload=None):
            result = self.store(method, path, payload)
            if 'issue-work-leases.json' in path:
                result['path'] = 'other.json'
            return result
        ledger = lease_module.Ledger(REPO, guard, wrong)
        with self.held():
            with self.assertRaisesRegex(ValueError, 'file identity'):
                ledger.read()
    def test_uncertain_write_no_retry_guard_retained(self):
        self.store.fail_ledger = True
        with self.assertRaisesRegex(RuntimeError, 'uncertain write'):
            with self.held(): self.ledger.transition(1, 'secret', 'claiming')
        self.assertEqual(self.store.writes, 1)
        self.assertEqual(self.store.guard['status'], 'held')

if __name__ == '__main__': unittest.main()
