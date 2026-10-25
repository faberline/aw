"""Narrow main-FF qualification: all attribute sources must be absent.

No filter disabling, attribute interpretation, or filter execution. Cleanup does
not use this exception. The caller owns the existing guard/exclusive-owner check.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

LIMIT = 16_000_000
OVERRIDES = ('GIT_ATTR_SOURCE', 'GIT_ATTR_NOSYSTEM', 'GIT_CONFIG_COUNT',
             'GIT_CONFIG_PARAMETERS', 'GIT_INDEX_FILE', 'GIT_DIR',
             'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY',
             'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE')


COMMAND_TIMEOUT = 30
REAP_TIMEOUT = 1
POLL_INTERVAL = 0.01


def git(path, *args, input=None, optional=False):
    # Python 3.12/macOS pipe controls can stall inside communicate beyond its
    # timeout. Finite regular-file stdin has kernel EOF regardless of the parent
    # keeping the descriptor open; output files avoid full-duplex pipe waits.
    if input is not None and (not isinstance(input, (bytes, bytearray)) or len(input) > LIMIT):
        raise ValueError('attribute proof input bound/type exceeded')
    command = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
               '-c', 'core.hooksPath=/dev/null', '-C', str(path), *args]
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        if input is not None:
            source.write(input)
            source.seek(0)
        child = None
        deadline = time.monotonic() + COMMAND_TIMEOUT
        try:
            child = subprocess.Popen(command, stdin=source if input is not None else subprocess.DEVNULL,
                                     stdout=output, stderr=errors, close_fds=True)
            # Deadline/size enforcement is independent of communicate/pipe IO.
            # The caller's guard is non-inherited; never signal a caller/group.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, COMMAND_TIMEOUT)
                if os.fstat(output.fileno()).st_size > LIMIT or os.fstat(errors.fileno()).st_size > LIMIT:
                    raise ValueError('attribute proof output bound exceeded')
                code = child.poll()
                if code is not None:
                    break
                time.sleep(min(POLL_INTERVAL, remaining))
            # A child can finish after the last size observation. Bound reads
            # too; polling caps accepted output, not instantaneous disk growth.
            output.seek(0)
            errors.seek(0)
            data, error_data = output.read(LIMIT + 1), errors.read(LIMIT + 1)
            if len(data) > LIMIT or len(error_data) > LIMIT:
                raise ValueError('attribute proof output bound exceeded')
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, COMMAND_TIMEOUT)
            if code:
                if optional and code == 1:
                    return b''
                raise ValueError('attribute proof Git read failed: ' + args[0])
            return data
        finally:
            # Only this invocation's Popen child may be stopped. No context-
            # manager __exit__ or unbounded wait can hide the original deadline.
            if child is not None and child.poll() is None:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=REAP_TIMEOUT)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError('owned Git reap uncertain; hold without retry') from exc


def digest(value):
    return hashlib.sha256(value).hexdigest()


def absent(path):
    # lstat catches dangling attribute-source links too. No source is read.
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        raise ValueError('configured Git filters/attributes source present; hold before status/checkout')
    # Bind default platform directory aliases (e.g. macOS /etc -> /private/etc)
    # without accepting any attribute-source file, including a symlink.
    resolved = path.resolve()
    ancestor = resolved.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    info = ancestor.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('attribute source parent is not a directory')
    return str(path), str(resolved), str(ancestor), info.st_dev, info.st_ino


def inspect(path, incoming):
    if not isinstance(incoming, str) or not re.fullmatch(r'[0-9a-f]{40}', incoming):
        raise ValueError('attribute proof requires exact incoming commit')
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('attribute proof requires canonical checkout path')
    if any(key in os.environ for key in OVERRIDES):
        raise ValueError('attribute/index/config environment override; no absence proof')
    environment = {k: v for k, v in os.environ.items()
                   if k.startswith('GIT_') or k in ('HOME', 'XDG_CONFIG_HOME')}
    # Hash relevant effective values and origin/scope identity; never emit driver
    # command values. Includes are represented, not rewritten or disabled.
    config = git(path, 'config', '--null', '--show-origin', '--show-scope', '--get-regexp',
                 r'^(filter\..*|core\.attributesfile|include\.path|includeif\..*\.path)$', optional=True)
    if git(path, 'config', '--name-only', '--get-regexp', r'^core\.attributesfile$', optional=True):
        raise ValueError('attributesFile override; no absence proof')
    head = git(path, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    if git(path, 'rev-parse', '--verify', 'refs/remotes/origin/main^{commit}').decode().strip() != incoming:
        raise ValueError('incoming ref changed during attribute proof')
    index = git(path, 'ls-files', '--stage', '-z')
    inventories = dict(index=index)
    paths = set()
    for record in index.split(b'\0'):
        if not record:
            continue
        metadata, filename = record.split(b'\t', 1)
        if metadata.split()[0] not in (b'100644', b'100755', b'120000'):
            raise ValueError('submodule/unknown index mode; no absence proof')
        if metadata.split()[-1] != b'0':
            raise ValueError('unmerged index; no absence proof')
        paths.add(filename)
    for label, sha in (('head', head), ('incoming', incoming)):
        records = git(path, 'ls-tree', '-r', '--full-tree', '-z', sha)
        inventories[label] = records
        for record in records.split(b'\0'):
            if not record:
                continue
            metadata, filename = record.split(b'\t', 1)
            if metadata.split()[0] not in (b'100644', b'100755', b'120000'):
                raise ValueError('submodule/unknown tree mode; no absence proof')
            paths.add(filename)
    if not paths or len(paths) > 100_000:
        raise ValueError('empty/oversized path inventory; no absence proof')
    parents = {path}
    for filename in paths:
        parts = filename.split(b'/')
        if any(p in (b'', b'.', b'..') for p in parts):
            raise ValueError('unsupported repository path')
        # Git's tree/index attribute lookup is byte-sensitive, whereas checkout
        # may alias names on the target filesystem (e.g. macOS ignorecase).
        # Do not infer filesystem equivalence from core.ignorecase or a clean
        # check-attr result. Restrict this narrow proof to printable ASCII path
        # components; Unicode/other normalization equivalence remains unknown.
        if any(any(c < 32 or c > 126 for c in part) for part in parts):
            raise ValueError('uncertain filesystem name equivalence; no absence proof')
        if any(part.lower() == b'.gitattributes' for part in parts):
            raise ValueError('configured Git filters/attributes tracked source or case alias; hold before status/checkout')
        parent = path
        for part in parts[:-1]:
            parent = parent / os.fsdecode(part)
            parents.add(parent)
            if len(parents) > 10_000:
                raise ValueError('attribute ancestor inventory bound exceeded')
    # Include root and every current/index/incoming path ancestor, even when an
    # incoming directory does not yet exist. Untracked attributes must also hold.
    for parent in parents:
        try:
            mode = os.lstat(parent).st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError('symlink/non-directory path ancestor; no absence proof')
    common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())
    info = Path(git(path, 'rev-parse', '--git-path', 'info/attributes').decode().strip())
    if not info.is_absolute():
        info = path / info
    sources = {p / '.gitattributes' for p in parents} | {info, common / 'info/attributes'}
    for variable in ('GIT_ATTR_GLOBAL', 'GIT_ATTR_SYSTEM'):
        source = git(path, 'var', variable).decode().strip()
        if not source or not Path(source).is_absolute():
            raise ValueError('unknown default attributes source')
        sources.add(Path(source))
    if sum(len(os.fsencode(p)) for p in sources) > LIMIT:
        raise ValueError('attribute source identity bound exceeded')
    source_identity = [absent(p) for p in sorted(sources)]
    inventory = b'\0'.join(sorted(paths)) + b'\0'
    attributes = {}
    for label, options in (('worktree', ()), ('index', ('--cached',)),
                           ('head', ('--source='+head,)), ('incoming', ('--source='+incoming,))):
        output = git(path, 'check-attr', *options, '-z', '--stdin', 'filter', input=inventory)
        fields = output.split(b'\0')
        if fields[-1:] != [b''] or len(fields) != 3 * len(paths) + 1:
            raise ValueError('incomplete attribute result; no absence proof')
        for offset, filename in enumerate(sorted(paths)):
            if fields[offset*3:offset*3+3] != [filename, b'filter', b'unspecified']:
                raise ValueError('effective filter/unknown attribute result; no absence proof')
        attributes[label] = digest(output)
    # Recheck after Git's attribute reads and immediately before the caller's
    # status/FF. The guard is cooperative, not a filesystem CAS.
    if (git(path, 'ls-files', '--stage', '-z') != index
            or git(path, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip() != head
            or git(path, 'rev-parse', '--verify', 'refs/remotes/origin/main^{commit}').decode().strip() != incoming
            or git(path, 'config', '--null', '--show-origin', '--show-scope', '--get-regexp',
                   r'^(filter\..*|core\.attributesfile|include\.path|includeif\..*\.path)$', optional=True) != config
            or {k: v for k, v in os.environ.items()
                if k.startswith('GIT_') or k in ('HOME', 'XDG_CONFIG_HOME')} != environment
            or [absent(p) for p in sorted(sources)] != source_identity):
        raise ValueError('attribute/config/index identity changed during absence proof')
    return dict(head=head, incoming=incoming, path_count=len(paths), paths=digest(inventory),
                inventories={k: digest(v) for k, v in inventories.items()}, config=digest(config),
                environment=digest(json.dumps(environment, sort_keys=True).encode()),
                absent_sources=digest(json.dumps(source_identity).encode()), attributes=attributes)
