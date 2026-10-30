"""Observe one local worker installation and lease; never authorize rollout.

The caller supplies both the installation and the Git common directory. This
does not enumerate hosts, launchers, processes, future runs, or cloud workers.
Even matching files and an absent lease cannot prove that rollout is complete.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat


MAX_FILE_BYTES = 1024 * 1024
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
RUNTIME_PATHS = {
    'entry': 'issue-work/scripts/work.py',
    'worker': 'issue-work/scripts/worker_guard.py',
    'provider': 'issue-ci/scripts/guard_github.py',
    'ledger': 'issue-ci/scripts/guard_ledger.py',
}


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def strict_json(raw):
    """Reject ambiguous JSON rather than choosing one duplicate field."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result

    def constant(_value):
        raise ValueError('non-finite JSON value')

    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant)


def read_file(path):
    """Read a bounded regular file without following its final symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    need(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE_BYTES,
         'local_inventory_unsafe_file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                 'local_inventory_changed')
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise RuntimeError('local_inventory_unreadable') from error
    need(len(data) <= MAX_FILE_BYTES and
         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
         == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         'local_inventory_changed')
    try:
        current = path.lstat()
    except FileNotFoundError:
        raise RuntimeError('local_inventory_changed') from None
    need((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
         == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         'local_inventory_changed')
    return data


def snapshot(skills_root, common_dir):
    """Capture only the supplied installation and local lease file."""
    skills_root, common_dir = Path(skills_root), Path(common_dir)
    need(skills_root.is_dir() and common_dir.is_dir(), 'local_inventory_directory_unknown')
    files = {}
    for name, relative in RUNTIME_PATHS.items():
        data = read_file(skills_root / relative)
        files[name] = None if data is None else hashlib.sha256(data).hexdigest()
    raw = read_file(common_dir / 'issue-work.lock')
    if raw is None:
        lease = {'state': 'not_observed'}
    else:
        try:
            value = strict_json(raw)
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError('local_inventory_lease_unknown') from None
        need(isinstance(value, dict) and type(value.get('issue')) is int and value['issue'] > 0
             and isinstance(value.get('phase'), str) and bool(value['phase']),
             'local_inventory_lease_unknown')
        lease = {'state': 'observed', 'issue': value['issue'], 'phase': value['phase'],
                 'sha256': hashlib.sha256(raw).hexdigest()}
    return {'files': files, 'lease': lease}


def observe(skills_root, common_dir, expected, reader=snapshot):
    """Compare exact bytes twice, while keeping actor rollout unknown."""
    need(isinstance(expected, dict) and set(expected) == set(RUNTIME_PATHS)
         and all(isinstance(value, str) and SHA256.fullmatch(value) for value in expected.values()),
         'local_inventory_expected_runtime')
    first = reader(skills_root, common_dir)
    second = reader(skills_root, common_dir)
    need(first == second and isinstance(first, dict) and set(first) == {'files', 'lease'}
         and isinstance(first['files'], dict) and set(first['files']) == set(RUNTIME_PATHS),
         'local_inventory_changed')
    files = {}
    for name, digest in first['files'].items():
        need(digest is None or isinstance(digest, str) and SHA256.fullmatch(digest),
             'local_inventory_runtime_unknown')
        files[name] = {'observed_sha256': digest, 'expected_sha256': expected[name],
                       'state': 'missing' if digest is None else 'match' if digest == expected[name] else 'different'}
    return {'version': 1, 'files': files, 'lease': first['lease'], 'expected_source': 'caller_supplied',
            'actor_rollout': 'unknown',
            'quiescence_proven': False, 'authorizes_writes': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-root', required=True)
    parser.add_argument('--common-dir', required=True)
    parser.add_argument('--expected-runtime', required=True,
                        help='JSON file with entry, worker, provider and ledger SHA-256 values')
    args = parser.parse_args()
    raw = read_file(Path(args.expected_runtime))
    need(raw is not None, 'local_inventory_expected_runtime')
    expected = strict_json(raw)
    print(json.dumps(observe(args.skills_root, args.common_dir, expected), sort_keys=True))


if __name__ == '__main__':
    main()
