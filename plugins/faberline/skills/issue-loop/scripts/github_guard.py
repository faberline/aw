"""Opt-in shared GitHub guard. No TTL, token handling, or automatic recovery.

All participating writers must use this guard before a cloud planner is enabled.
The existing local flock remains required for local worktree/lease operations.
"""
from contextlib import contextmanager
import base64
import json
import os
import re
import subprocess
import uuid
from urllib.parse import quote

PROTOCOL = 'aw-issue-guard-v1'
BRANCH = 'aw-coordination'
PATH = 'issue-work-guard.json'
_active = None


class Refused(RuntimeError):
    pass


class Busy(Refused):
    pass


def encode(value):
    return base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('duplicate coordination field')
        result[key] = value
    return result


def decode(value):
    try:
        result = json.loads(base64.b64decode(''.join(value.split()), validate=True),
                            object_pairs_hook=object_pairs)
    except (ValueError, TypeError, AttributeError) as error:
        raise Refused('invalid coordination content') from error
    if not isinstance(result, dict):
        raise Refused('coordination content must be an object')
    return result


class GitHub:
    """Uses the environment's existing gh authorization. Never prints tokens."""
    def __call__(self, method, path, payload=None):
        args = ['gh', 'api', '--method', method, path]
        if payload is not None:
            args += ['--input', '-']
        result = subprocess.run(args, input=None if payload is None else json.dumps(payload),
                                text=True, capture_output=True, timeout=60)
        if result.returncode:
            # Provider output can contain credentials or untrusted response text.
            raise Refused('coordination request failed; no automatic retry')
        try:
            return json.loads(result.stdout, object_pairs_hook=object_pairs)
        except ValueError as error:
            raise Refused('invalid coordination response') from error


class Guard:
    def __init__(self, repo, owner, request=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise Refused('invalid coordination repository')
        # A per-run public nonce, not a credential, PID, or native identity.
        try:
            self.owner = str(uuid.UUID(owner))
        except (ValueError, TypeError, AttributeError) as error:
            raise Refused('set ISSUE_COORDINATION_OWNER to a per-run UUID') from error
        self.repo = repo
        self.request = request or GitHub()
        self.endpoint = f'repos/{repo}/contents/{PATH}'
        self.held = None
        self.sha = None
        self.mutation_attempted = False

    def read(self):
        response = self.request('GET', self.endpoint + '?ref=' + quote(BRANCH))
        if (not isinstance(response, dict) or response.get('type') != 'file'
                or response.get('path') != PATH or response.get('encoding') != 'base64'
                or not isinstance(response.get('sha'), str)
                or not re.fullmatch(r'[0-9a-f]{40}', response['sha'])):
            raise Refused('invalid coordination file identity')
        state = decode(response.get('content'))
        if (set(state) != {'protocol', 'repo', 'status', 'generation', 'owner'}
                or state['protocol'] != PROTOCOL or state['repo'] != self.repo
                or state['status'] not in ('free', 'held')):
            raise Refused('invalid coordination state')
        try:
            uuid.UUID(state['generation'])
            if state['status'] == 'held':
                uuid.UUID(state['owner'])
            elif state['owner'] is not None:
                raise ValueError('free guard has owner')
        except (TypeError, ValueError, AttributeError) as error:
            raise Refused('invalid coordination owner/generation') from error
        return state, response['sha']

    def write(self, state, sha):
        # The expected blob SHA makes concurrent acquire/release updates conflict.
        # Never create a missing guard: installation/cutover is a separate step.
        self.request('PUT', self.endpoint, dict(message='aw issue guard transition',
                     branch=BRANCH, sha=sha, content=encode(state)))
        current, current_sha = self.read()
        if current != state:
            raise Refused('guard write outcome unknown; preserve state; no retry')
        return current_sha

    def acquire(self):
        if self.held is not None:
            raise Refused('guard already acquired by this instance')
        current, sha = self.read()
        if current['status'] != 'free':
            raise Busy('shared issue guard is held; do not steal or expire it')
        state = dict(protocol=PROTOCOL, repo=self.repo, status='held',
                     generation=str(uuid.uuid4()), owner=self.owner)
        self.sha = self.write(state, sha)
        self.held = state

    def verify(self):
        state, sha = self.read()
        if self.held is None or state != self.held or sha != self.sha:
            raise Refused('shared issue guard ownership changed')

    def release(self):
        self.verify()
        state = dict(protocol=PROTOCOL, repo=self.repo, status='free',
                     generation=str(uuid.uuid4()), owner=None)
        self.write(state, self.sha)
        self.held = None
        self.sha = None


@contextmanager
def shared_guard(repo, required=False):
    global _active
    mode = os.getenv('ISSUE_COORDINATION_MODE', 'local')
    if mode == 'local' and not required:
        yield None
        return
    if mode != 'github':
        raise Refused('hosted mutations require ISSUE_COORDINATION_MODE=github')
    if _active is not None:
        raise Refused('nested coordination acquisition is not supported')
    guard = Guard(repo, os.getenv('ISSUE_COORDINATION_OWNER', ''))
    guard.acquire()
    _active = guard
    try:
        yield guard
    except BaseException:
        # A validation refusal before a product write is safe to release. An
        # attempted write may have committed even when the caller saw an error.
        if not guard.mutation_attempted:
            guard.release()
        raise
    else:
        guard.release()
    finally:
        _active = None


def verify_for(repo):
    if os.getenv('ISSUE_COORDINATION_MODE', 'local') == 'local':
        return
    if _active is None or _active.repo != repo:
        raise Refused('GitHub mutation has no matching shared guard')
    _active.verify()


def mark_mutation_for(repo):
    verify_for(repo)
    if _active is not None:
        _active.mutation_attempted = True
