"""Controller-owned creation provenance; never adopt historical worktrees."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

REGISTRY = 'issue-work.trees.json'


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def git(main, *args):
    return subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                                   '-c', 'core.hooksPath=/dev/null', '-C', str(main), *args], stderr=subprocess.PIPE)


def origin_repo(url):
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?', url)
    if not match:
        raise ValueError('origin is not an exact supported GitHub repository URL')
    return match[1].lower()


def context(main):
    main = Path(main).resolve()
    common = Path(git(main, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()).resolve()
    if common != main / '.git' or not common.is_dir():
        raise ValueError('requires the primary non-bare checkout with a .git directory')
    url = git(main, 'remote', 'get-url', 'origin').decode().strip()
    return dict(main=str(main), common=str(common), repo=origin_repo(url), origin=url)


def identity(main, path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve() or path.is_symlink() or not path.is_dir():
        raise ValueError('missing, moved or symlink worktree path')
    pointer = path / '.git'
    if pointer.is_symlink() or not pointer.is_file() or pointer.stat().st_uid != os.getuid():
        raise ValueError('invalid worktree git pointer')
    admin = Path(git(path, 'rev-parse', '--absolute-git-dir').decode().strip()).resolve()
    common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()).resolve()
    if common != Path(main) / '.git' or admin.parent != common / 'worktrees':
        raise ValueError('worktree belongs to another repository/admin directory')
    def inode(p):
        s = p.stat()
        if s.st_uid != os.getuid():
            raise ValueError('foreign filesystem owner')
        return [s.st_dev, s.st_ino]
    return dict(path=str(path), path_inode=inode(path), admin=str(admin), admin_inode=inode(admin),
                pointer_inode=inode(pointer), pointer_sha256=hashlib.sha256(pointer.read_bytes()).hexdigest())


def load(common):
    file = Path(common) / REGISTRY
    if file.is_symlink():
        raise ValueError('symlink ownership registry')
    if not file.exists():
        return dict(schema=1, trees={})
    if file.is_symlink() or not file.is_file() or file.stat().st_uid != os.getuid() or file.stat().st_size > 2_000_000:
        raise ValueError('untrusted/oversized ownership registry')
    data = strict_json(file.read_text())
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('trees'), dict):
        raise ValueError('invalid ownership registry')
    return data


def save(common, data):
    # Caller holds issue-work.guard. Replacement never follows a symlink.
    directory = Path(common)
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.trees-', delete=False) as file:
        json.dump(data, file, sort_keys=True)
        file.write('\n')
        name = file.name
    os.chmod(name, 0o600)
    os.replace(name, directory / REGISTRY)


def owner_record(owner):
    if not owner:
        raise ValueError('missing controller owner')
    pid = os.getppid()
    try:
        birth = subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()
    except (subprocess.CalledProcessError, OSError):
        birth = ''
    return dict(owner=owner, host=socket.gethostname(), pid=pid, birth=birth, released=False)


def created(main, n, branch, path, owner):
    ctx = context(main)
    expected = Path(ctx['main']) / '.claude/worktrees' / f'issue-{n}'
    if Path(path) != expected or branch not in (f'issue/{n}', f'agy/issue-{n}'):
        raise ValueError('not an AW canonical issue worktree')
    data = load(ctx['common'])
    if str(path) in data['trees']:
        raise ValueError('existing provenance at newly created path; manual reconciliation required')
    data['trees'][str(path)] = dict(schema=1, created_by='aw-issue-work-v1', issue=n,
        branch=branch, context=ctx, identity=identity(ctx['main'], path),
        owners=[owner_record(owner)], phase='coding', pr=None, submitted_head=None)
    save(ctx['common'], data)


def update(main, n, branch, path, owner, pr=None, submitted=False):
    common = Path(main) / '.git'
    data = load(common)
    record = data['trees'].get(str(path))
    if record is None:
        return  # Historical/custom/app-owned trees are never adopted.
    if record['issue'] != n or record['branch'] != branch or record['identity'] != identity(main, path):
        raise ValueError('ownership identity changed; preserve worktree')
    if not submitted:
        current = owner_record(owner)
        if not record['owners'] or any(record['owners'][-1].get(k) != current[k] for k in ('owner','host','pid','birth')):
            if record['owners'] and record['owners'][-1]['owner'] == owner:
                # Same logical owner transfers between guarded command processes.
                # Keep every PID for cleanup liveness checks; do not lose history.
                record['owners'][-1]['released'] = True
            record['owners'].append(current)
        else:
            record['owners'][-1]['released'] = False
        record['phase'] = 'coding'
    else:
        if not record['owners'] or record['owners'][-1]['owner'] != owner:
            raise ValueError('cannot release another owner')
        record['owners'][-1]['released'] = True
        record['phase'] = 'submitted'
        record['pr'] = int(pr)
        record['submitted_head'] = git(path, 'rev-parse', 'HEAD').decode().strip()
    save(common, data)
