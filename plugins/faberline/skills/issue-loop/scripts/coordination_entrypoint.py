#!/usr/bin/env python3
"""Apply an installed, repo-scoped coordination opt-in before plan/work."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from urllib.parse import urlsplit


PROTOCOL = 'aw-issue-coordination-entrypoint-v1'
TARGET_REPOS = (
    'faberline/core', 'faberline/defer', 'faberline/keep',
    'faberline/lumen', 'faberline/relay', 'faberline/tape',
)
CONFIG = Path(__file__).resolve().parents[1] / 'coordination.json'
REMOTE_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class Refused(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('duplicate coordination config field')
        result[key] = value
    return result


def load_config(path=CONFIG):
    """Return the verified config hash, None when the opt-in file is absent."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refused('cannot inspect coordination config') from error
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise Refused('coordination config must be a small regular file')
    try:
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Refused('invalid coordination config') from error
    if (not isinstance(value, dict) or set(value) != {'protocol', 'repositories'}
            or value['protocol'] != PROTOCOL
            or value['repositories'] != list(TARGET_REPOS)):
        raise Refused('coordination config must enable the pinned six-repository set')
    return hashlib.sha256(raw).hexdigest()


def parse_remote(remote):
    """Return (owner/name, host) for common URL and SCP Git remote forms."""
    if not isinstance(remote, str) or not remote or '\x00' in remote:
        return None
    match = re.fullmatch(r'[^@/:]+@([^:]+):(.+)', remote)
    if match:
        host, path = match.groups()
    else:
        try:
            parsed = urlsplit(remote)
        except ValueError:
            return None
        if not parsed.scheme or not parsed.hostname or parsed.query or parsed.fragment:
            return None
        host, path = parsed.hostname, parsed.path.lstrip('/')
    path = path.rstrip('/')
    if path.endswith('.git'):
        path = path[:-4]
    if not REMOTE_RE.fullmatch(path):
        return None
    return canonical_repo(path), host.lower()


def canonical_repo(repo):
    """Use the pinned spelling for protected GitHub repositories."""
    if isinstance(repo, str):
        folded = repo.casefold()
        for target in TARGET_REPOS:
            if target.casefold() == folded:
                return target
    return repo


def local_remote(cwd, run=subprocess.run):
    """Read the current checkout's origin identity without fetching or writing."""
    try:
        root = run(['git', 'rev-parse', '--show-toplevel'], cwd=cwd,
                   check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except subprocess.CalledProcessError as error:
        detail = error.stderr if isinstance(error.stderr, str) else ''
        if (error.returncode == 128 and 'not a git repository' in detail.lower()
                and error.cmd == ['git', 'rev-parse', '--show-toplevel']):
            return None
        raise Refused('cannot verify local repository identity') from error
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused('cannot verify local repository identity') from error
    try:
        remote = run(['git', '-C', root, 'remote', 'get-url', 'origin'],
                     check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused('cannot verify local repository identity') from error
    identity = parse_remote(remote)
    if identity is None:
        return None
    return identity


def _is_target(repo):
    return canonical_repo(repo) in TARGET_REPOS


def prepare(kind, args, environ, *, config_hash, remote=None, owner_factory=uuid.uuid4):
    """Return a copied environment; this function performs no process or file writes."""
    if kind not in ('plan', 'work'):
        raise Refused('unknown coordination entrypoint')
    env = dict(environ)
    cloud_plan = kind == 'plan' and bool(args) and args[0] == '--cloud'
    if cloud_plan:
        supplied = env.get('GH_REPO', '')
        repo = canonical_repo(supplied)
        if not _is_target(repo):
            raise Refused('cloud planner requires an exact supported GH_REPO')
        env['GH_REPO'] = repo
        env['ISSUE_COORDINATION_MODE'] = 'github'
        env['ISSUE_COORDINATION_OWNER'] = str(owner_factory())
        return env

    # A protected GH_REPO cannot be trusted when the checkout identity is unreadable.
    if remote is None:
        if _is_target(env.get('GH_REPO', '')):
            raise Refused('cannot verify GH_REPO against the local origin')
        # Preserve legacy behavior for non-target repositories without a parseable origin.
        return env
    repo, host = remote
    host = host.lower() if isinstance(host, str) else host
    repo = canonical_repo(repo)
    supplied = env.get('GH_REPO')
    supplied_canonical = canonical_repo(supplied) if supplied else supplied
    if supplied and supplied_canonical != repo:
        raise Refused('GH_REPO does not match the local origin')
    if _is_target(repo):
        if host != 'github.com':
            raise Refused('protected repository must use its github.com origin')
        env['GH_REPO'] = repo
        if config_hash is None:
            raise Refused('coordination config is required for protected repositories')
        # A present valid config is authoritative. Environment flags cannot opt out.
        env['ISSUE_COORDINATION_MODE'] = 'github'
        env['ISSUE_COORDINATION_OWNER'] = str(owner_factory())
    elif supplied:
        env['GH_REPO'] = repo
    return env


def bootstrap(kind, args, environ=None, *, cwd=None):
    """Apply migration policy inside the real plan/work process."""
    config_hash = load_config()
    remote = None
    cloud_plan = kind == 'plan' and bool(args) and args[0] == '--cloud'
    if not cloud_plan:
        remote = local_remote(Path.cwd() if cwd is None else cwd)
    env = prepare(kind, args, os.environ if environ is None else environ,
                  config_hash=config_hash, remote=remote)
    if environ is None:
        os.environ.update(env)
    return env


def dispatch(kind, args):
    if kind not in ('plan', 'work'):
        raise Refused('unknown coordination entrypoint')
    skills = Path(__file__).resolve().parents[2]
    target = skills / ('issue-plan/scripts/plan.py' if kind == 'plan'
                       else 'issue-work/scripts/work.py')
    if not target.is_file():
        raise Refused('coordination target script is missing')
    os.execve(sys.executable, [sys.executable, str(target), *args], os.environ.copy())


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: coordination_entrypoint.py plan|work [args ...] | verify-config')
    try:
        if sys.argv[1] == 'verify-config':
            if len(sys.argv) != 2:
                raise Refused('verify-config takes no arguments')
            digest = load_config()
            if digest is None:
                raise Refused('coordination config is not installed')
            print(json.dumps({'config': str(CONFIG),
                              'sha256': digest, 'repositories': list(TARGET_REPOS)},
                             sort_keys=True))
        else:
            dispatch(sys.argv[1], sys.argv[2:])
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        raise SystemExit(2)
