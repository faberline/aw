"""Read-only index content-visibility guard shared by cleanup and main FF."""
import hashlib
import os
import re
from pathlib import Path
import subprocess


def git(path, *args):
    return subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
        '-c', 'core.hooksPath=/dev/null', '-C', str(path), *args], stderr=subprocess.PIPE)


def inspect(path):
    path = Path(path)
    for key in ('core.sparseCheckout', 'core.sparseCheckoutCone', 'index.sparse'):
        try:
            value = git(path, 'config', '--bool', '--get', key).decode().strip()
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 1:
                continue
            raise
        if value != 'false':
            raise ValueError('sparse/unknown index configuration; preserve worktree')
    common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())
    sparse = Path(git(path, 'rev-parse', '--git-path', 'info/sparse-checkout').decode().strip())
    if not sparse.is_absolute():
        sparse = path / sparse
    for file in {sparse, common/'info/sparse-checkout'}:
        try:
            os.lstat(file)
        except FileNotFoundError:
            continue
        raise ValueError('sparse checkout metadata present; preserve worktree')
    flags = git(path, 'ls-files', '-v', '-z')
    if len(flags) > 32_000_000:
        raise ValueError('index flag inventory bound exceeded')
    # -v lowercases assume-unchanged tags; S/s denotes skip-worktree. Only
    # ordinary cached H records certify content visibility. Other tags hold.
    if any(not item.startswith(b'H ') for item in flags.split(b'\0') if item):
        raise ValueError('hidden/nonordinary index flags (assume-unchanged/skip-worktree); preserve bytes and flags')
    return dict(flags_sha256=hashlib.sha256(flags).hexdigest(), sparse=False)


def configured_filters(path):
    """Names only: callers must hold or safely suppress all configured drivers."""
    try:
        names = git(path, 'config', '--name-only', '--get-regexp', r'^filter\..*\.(clean|smudge|process|required)$')
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 1:
            raise
        names = b''
    if len(names) > 16_000:
        raise ValueError('filter configuration bound exceeded')
    keys = sorted(set(names.decode().splitlines()))
    if not all(re.fullmatch(r'filter\.[A-Za-z0-9_.-]+\.(clean|smudge|process|required)', k) for k in keys):
        raise ValueError('unsupported filter configuration identity')
    return keys
