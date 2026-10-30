"""Pure state transitions for a cooperating issue-ownership ledger.

This module has no network or Git writes. A caller must authenticate the
ledger ref, protect it against force pushes and deletion, and update it with a
verified non-force fast-forward. It does not serialize legacy or external actors.
"""
import copy
import hashlib
import json
import re

REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
ISSUE = re.compile(r'[1-9][0-9]{0,9}')
OWNER = re.compile(r'[0-9a-f]{32,64}')
SHA = re.compile(r'[0-9a-f]{40}')
MAX_LOCKS = 4096
MAX_BYTES = 524288
MARKER = 'AW_ISSUE_GUARD_V1 '


class Refused(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Refused(reason)


def descriptor(value):
    need(isinstance(value, dict) and set(value) == {'owner', 'kind', 'head'}
         and isinstance(value['owner'], str) and OWNER.fullmatch(value['owner'])
         and value['kind'] in ('worker', 'merge')
         and (value['head'] is None or isinstance(value['head'], str) and SHA.fullmatch(value['head']))
         and (value['kind'] != 'merge' or value['head'] is not None), 'guard_descriptor')
    return value


def validate(value):
    need(isinstance(value, dict) and set(value) == {'version', 'repo', 'sequence', 'locks'}
         and type(value['version']) is int and value['version'] == 1
         and isinstance(value['repo'], str) and REPO.fullmatch(value['repo'])
         and type(value['sequence']) is int and 0 <= value['sequence'] < 2**53
         and isinstance(value['locks'], dict) and len(value['locks']) <= MAX_LOCKS,
         'guard_ledger')
    owners = set()
    for issue, item in value['locks'].items():
        need(isinstance(issue, str) and ISSUE.fullmatch(issue), 'guard_issue')
        descriptor(item)
        need(item['owner'] not in owners, 'guard_owner_duplicate')
        owners.add(item['owner'])
    need(len(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()) <= MAX_BYTES,
         'guard_ledger_size')
    return value


def transition(ledger, repo, issue, action, claim):
    """Compute one claim/release; the caller must commit it atomically."""
    validate(ledger)
    need(isinstance(repo, str) and REPO.fullmatch(repo) and ledger['repo'] == repo, 'guard_repository')
    need(type(issue) is int and ISSUE.fullmatch(str(issue)), 'guard_issue')
    need(action in ('claim', 'release'), 'guard_action')
    descriptor(claim)
    key = str(issue)
    existing = ledger['locks'].get(key)
    if action == 'claim':
        need(existing is None, 'guard_held')
        need(all(item['owner'] != claim['owner'] for item in ledger['locks'].values()), 'guard_owner_duplicate')
    else:
        need(existing == claim, 'guard_not_owner')
    next_value = copy.deepcopy(ledger)
    next_value['sequence'] += 1
    if action == 'claim':
        next_value['locks'][key] = copy.deepcopy(claim)
    else:
        del next_value['locks'][key]
    validate(next_value)
    return next_value


def proposal(ledger, repo, parent, issue, action, claim, operation):
    """Create an integrity-checked child description, not a Git commit."""
    need(isinstance(parent, str) and SHA.fullmatch(parent)
         and isinstance(operation, str) and OWNER.fullmatch(operation), 'guard_proposal_identity')
    payload = dict(version=1, repo=repo, parent=parent, issue=issue, action=action,
                   claim=copy.deepcopy(claim), operation=operation,
                   ledger=transition(ledger, repo, issue, action, claim))
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return dict(payload=payload, sha256=hashlib.sha256(raw).hexdigest())


def verify_proposal(ledger, repo, parent, candidate):
    """Check a child against its actual parent state; this is not a lock."""
    need(isinstance(candidate, dict) and set(candidate) == {'payload', 'sha256'}
         and isinstance(candidate['payload'], dict)
         and set(candidate['payload']) == {'version', 'repo', 'parent', 'issue', 'action', 'claim', 'operation', 'ledger'}
         and type(candidate['payload']['version']) is int and candidate['payload']['version'] == 1
         and candidate['payload']['repo'] == repo and candidate['payload']['parent'] == parent,
         'guard_proposal_shape')
    p = candidate['payload']
    expected = proposal(ledger, repo, parent, p['issue'], p['action'], p['claim'], p['operation'])
    need(candidate == expected, 'guard_proposal_changed')
    return copy.deepcopy(p['ledger'])


def commit_message(candidate):
    """Serialize a proposal as data in a non-product Git commit."""
    raw = json.dumps(candidate, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    need(len(raw.encode()) <= MAX_BYTES, 'guard_proposal_size')
    return MARKER + raw


def verify_commit(ledger, repo, parent, child, expected_tree, commit):
    """Validate one provider-read commit and its exact parent transition."""
    need(isinstance(parent, str) and SHA.fullmatch(parent) and isinstance(child, str) and SHA.fullmatch(child)
         and child != parent
         and isinstance(expected_tree, str) and SHA.fullmatch(expected_tree)
         and isinstance(commit, dict) and commit.get('sha') == child
         and isinstance(commit.get('tree'), dict) and commit['tree'].get('sha') == expected_tree
         and isinstance(commit.get('parents'), list) and len(commit['parents']) == 1
         and isinstance(commit['parents'][0], dict) and commit['parents'][0].get('sha') == parent,
         'guard_commit_identity')
    message = commit.get('message')
    need(isinstance(message, str) and message.startswith(MARKER)
         and len(message.encode()) <= MAX_BYTES + len(MARKER), 'guard_commit_message')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        candidate = json.loads(message[len(MARKER):], object_pairs_hook=unique)
    except (ValueError, TypeError, RecursionError):
        raise Refused('guard_commit_message') from None
    need(commit_message(candidate) == message, 'guard_commit_message')
    return verify_proposal(ledger, repo, parent, candidate)
