"""Persistent issue leases under the shared GitHub guard; no expiry or stealing."""
import hashlib
import re
import uuid

PROTOCOL = 'aw-issue-leases-v1'
PATH = 'issue-work-leases.json'
BRANCH = 'aw-coordination'
PHASES = ('claiming', 'coding', 'submitted')


def nonce(repo, owner):
    if not isinstance(owner, str) or not owner:
        raise ValueError('stable local owner required')
    return hashlib.sha256(('aw-issue-owner-v1\0' + repo + '\0' + owner).encode()).hexdigest()


class Ledger:
    def __init__(self, repo, coordination, request=None):
        if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('invalid lease repository')
        self.repo, self.guard = repo, coordination
        self.request = request or coordination.GitHub()
        self.endpoint = f'repos/{repo}/contents/{PATH}'

    def read(self):
        self.guard.verify_for(self.repo, required=True)
        result = self.request('GET', self.endpoint + '?ref=' + BRANCH)
        if (not isinstance(result, dict) or result.get('type') != 'file' or result.get('path') != PATH
                or result.get('encoding') != 'base64' or not isinstance(result.get('sha'), str)
                or not re.fullmatch(r'[0-9a-f]{40}', result['sha'])):
            raise ValueError('invalid lease file identity')
        state = self.guard.decode(result.get('content'))
        if set(state) != {'protocol', 'repo', 'generation', 'leases'} or state['protocol'] != PROTOCOL or state['repo'] != self.repo or not isinstance(state['leases'], list):
            raise ValueError('invalid lease ledger')
        try:
            uuid.UUID(state['generation'])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('invalid ledger generation') from exc
        seen = set()
        for lease in state['leases']:
            if (not isinstance(lease, dict) or set(lease) != {'issue', 'owner_nonce', 'phase'}
                    or type(lease['issue']) is not int or lease['issue'] < 1
                    or not isinstance(lease['owner_nonce'], str) or not re.fullmatch(r'[0-9a-f]{64}', lease['owner_nonce'])
                    or lease['phase'] not in PHASES or lease['issue'] in seen):
                raise ValueError('invalid lease record')
            seen.add(lease['issue'])
        return state, result['sha']

    def get(self, issue):
        state, _ = self.read()
        return next((v for v in state['leases'] if v['issue'] == issue), None)

    def require_unleased(self, issue):
        if self.get(issue) is not None:
            raise ValueError('issue has a persistent worker lease')

    def _write(self, state, sha):
        self.guard.mark_mutation_for(self.repo, required=True)
        self.request('PUT', self.endpoint, {'message': 'aw issue lease transition', 'branch': BRANCH,
                     'sha': sha, 'content': self.guard.encode(state)})
        current, _ = self.read()
        if current != state:
            raise ValueError('lease write outcome unknown; preserve state; no retry')

    def transition(self, issue, owner, phase, previous=None):
        if type(issue) is not int or issue < 1 or phase not in PHASES:
            raise ValueError('invalid lease transition')
        if (previous, phase) not in ((None, 'claiming'), ('claiming', 'coding'), ('coding', 'submitted')):
            raise ValueError('unsupported lease transition')
        token = nonce(self.repo, owner)
        state, sha = self.read()
        old = next((v for v in state['leases'] if v['issue'] == issue), None)
        if previous is None and old is not None:
            raise ValueError('issue already leased; no takeover')
        if previous is not None and (old is None or old['owner_nonce'] != token or old['phase'] != previous):
            raise ValueError('lease owner or phase mismatch')
        state['leases'] = [v for v in state['leases'] if v['issue'] != issue]
        state['leases'].append({'issue': issue, 'owner_nonce': token, 'phase': phase})
        state['leases'].sort(key=lambda v: v['issue'])
        state['generation'] = str(uuid.uuid4())
        self._write(state, sha)

    def release(self, issue, owner):
        state, sha = self.read()
        old = next((v for v in state['leases'] if v['issue'] == issue), None)
        if old is None or old['owner_nonce'] != nonce(self.repo, owner) or old['phase'] != 'submitted':
            raise ValueError('cannot release foreign or incomplete lease')
        state['leases'] = [v for v in state['leases'] if v['issue'] != issue]
        state['generation'] = str(uuid.uuid4())
        self._write(state, sha)
