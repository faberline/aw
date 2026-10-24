#!/usr/bin/env python3
"""Codex post-merge maintenance: plan first, one explicitly verified apply."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.parse import quote


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


HERE = Path(__file__).resolve().parent
state = module('worktree_state', HERE / 'worktree_state.py')
index_safety = module('index_safety', HERE / 'index_safety.py')
queue = module('queue_transport', HERE.parents[1] / 'issue-loop/scripts/milestone_queue.py')
SHA = re.compile(r'[0-9a-f]{40}')
ADMIN = {'HEAD', 'gitdir', 'commondir', 'index', 'ORIG_HEAD', 'COMMIT_EDITMSG', 'logs/HEAD'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def code_hash():
    return digest({str(p.name): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                   (Path(__file__).resolve(), HERE/'worktree_state.py', HERE/'index_safety.py', Path(queue.__file__))})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def records(main):
    result = []
    for block in state.git(main, 'worktree', 'list', '--porcelain', '-z').split(b'\0\0'):
        if not block:
            continue
        fields = {}
        for line in block.split(b'\0'):
            key, _, value = line.partition(b' ')
            fields[key.decode()] = os.fsdecode(value)
        result.append(fields)
    require(len(result) <= 1000, 'inventory bound exceeded; no truncated cleanup')
    require(len({r['worktree'] for r in result}) == len(result), 'duplicate worktree paths')
    return result


def receipt(data):
    required = {'repo','pr','reviewed_head','base_sha','base_branch','merge_sha','merge_tree'}
    require(isinstance(data, dict) and set(data) == required, 'invalid merge receipt fields')
    require(isinstance(data['pr'], int) and not isinstance(data['pr'], bool) and data['pr'] > 0, 'invalid PR')
    require(re.fullmatch(r'[\w.-]+/[\w.-]+', data['repo']) is not None, 'invalid repository')
    require(isinstance(data['base_branch'], str) and data['base_branch'] and len(data['base_branch']) < 200, 'invalid base branch')
    for key in ('reviewed_head','base_sha','merge_sha','merge_tree'):
        require(isinstance(data[key], str) and SHA.fullmatch(data[key]), f'invalid {key}')
    return data


def owners_inactive(record):
    require(record.get('phase') == 'submitted', 'AW owner has not submitted/released work')
    require(isinstance(record.get('owners'), list) and record['owners'], 'unknown owner/session history')
    for owner in record['owners']:
        require(owner.get('released') is True and isinstance(owner.get('owner'), str) and owner['owner']
                and isinstance(owner.get('birth'), str) and owner['birth'], 'owner/session release is unverified')
        require(owner.get('host') == socket.gethostname(), 'foreign/unknown session host')
        pid = owner.get('pid')
        require(isinstance(pid, int) and not isinstance(pid, bool) and pid > 0, 'invalid session PID')
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            raise ValueError('session liveness unknown')
        raise ValueError('owner/session PID is live (including PID reuse); hold')


def filter_options(path):
    # Do not infer raw-byte equivalence from cached filtered working-tree stats,
    # and never execute a clean/smudge/process driver. Filter-aware support is a
    # separate reviewed policy, not a reason to clear or ignore configuration.
    keys = index_safety.configured_filters(path)
    require(not keys, 'configured Git filters require a separately reviewed content policy; hold before status/removal')
    return keys, []


def local_snapshot(ctx, record):
    n, path = record['issue'], Path(record['identity']['path'])
    require(record.get('schema') == 1 and record.get('created_by') == 'aw-issue-work-v1', 'no AW creation provenance')
    require(isinstance(n, int) and not isinstance(n, bool) and n > 0, 'invalid issue identity')
    require(record['context'] == ctx, 'repo/origin/common identity changed')
    require(path != Path(ctx['main']) and Path(ctx['common']) not in path.parents, 'main/admin directories are protected')
    require(not record.get('protected'), 'explicitly protected worktree')
    require(re.fullmatch(r'(?:issue/'+str(n)+r'|agy/issue-'+str(n)+r')(?:-[A-Za-z0-9][\w.-]*)?', record['branch']) is not None, 'branch outside AW issue scope is report-only')
    require(path not in Path(__file__).resolve().parents, 'controller is inside target; use trusted installed AW source')
    require(Path.cwd() != path and path not in Path.cwd().parents, 'current working directory is protected')
    inventory = records(ctx['main'])
    matches = [r for r in inventory if r['worktree'] == str(path)]
    require(len(matches) == 1, 'missing/moved registration; never prune')
    registered = matches[0]
    require(not any(k in registered for k in ('locked','prunable','bare','detached')), 'locked/prunable/detached tree held')
    require(registered.get('branch') == 'refs/heads/' + record['branch'], 'registered branch changed')
    require(state.identity(ctx['main'], path) == record['identity'], 'path/admin inode or owner changed')
    lease = Path(ctx['common']) / 'issue-work.lock'
    require(not lease.is_symlink(), 'symlink lease; unknown owner')
    if lease.exists():
        require(lease.is_file() and lease.stat().st_uid == os.getuid() and lease.stat().st_size < 64000, 'unknown/oversized lease file')
        lock = state.strict_json(lease.read_text())
        require(isinstance(lock, dict) and isinstance(lock.get('issue'), int) and not isinstance(lock.get('issue'), bool)
                and lock['issue'] > 0 and isinstance(lock.get('owner'), str) and lock['owner']
                and lock.get('host') == socket.gethostname() and isinstance(lock.get('since'), int) and not isinstance(lock.get('since'), bool)
                and isinstance(lock.get('pid'), int) and not isinstance(lock.get('pid'), bool) and lock['pid'] > 0
                and lock.get('phase') in ('coding','claiming','submitted'), 'unknown/foreign lease schema')
        require(lock['issue'] != n and lock['owner'] not in {o['owner'] for o in record['owners']}, 'active/recoverable owner lease holds tree')
    owners_inactive(record)
    head = state.git(path, 'rev-parse', 'HEAD').decode().strip()
    require(registered['HEAD'] == head == state.git(ctx['main'], 'rev-parse', 'refs/heads/'+record['branch']).decode().strip(), 'HEAD/ref changed')
    index = index_safety.inspect(path)
    # .gitattributes is untrusted data, never permission to run a driver.
    filter_keys, options = filter_options(path)
    staged = state.git(path, 'ls-files', '--stage', '-z')
    require(len(staged) < 32_000_000 and not any(item.startswith(b'160000 ') for item in staged.split(b'\0')), 'submodule/oversized index is report-only')
    require(not state.git(path, 'ls-files', '--stage', '--', '.gitmodules'), 'submodule repositories are report-only')
    dirty = state.git(path, *options, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored=matching', '--ignore-submodules=all')
    require(not dirty, 'tracked/untracked/ignored content present; preserve all')
    admin = Path(record['identity']['admin'])
    metadata = {}
    for item in admin.rglob('*'):
        require(not item.is_symlink() and item.stat().st_uid == os.getuid(), 'symlink/foreign worktree admin metadata')
        if item.is_file():
            rel = item.relative_to(admin).as_posix()
            require(rel in ADMIN and item.stat().st_size <= 16_000_000, 'unknown/in-progress/oversized admin state')
            metadata[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
        else:
            require(item.is_dir() and (item.relative_to(admin).as_posix() == 'logs'
                    or item.relative_to(admin).as_posix() == 'refs' and not any(item.iterdir())), 'unknown admin entry')
    require(sum((admin/k).stat().st_size for k in metadata) <= 32_000_000, 'admin state bound exceeded')
    history = set(state.git(path, 'reflog', '--format=%H').decode().split())
    orig = admin / 'ORIG_HEAD'
    if orig.exists():
        history.add(orig.read_text().strip())
    require(all(SHA.fullmatch(h) for h in history), 'unknown reflog/ORIG_HEAD identity')
    return dict(path=str(path), head=head, registered=registered, admin_hashes=metadata, history=sorted(history), filter_keys=filter_keys, index=index)


def ancestor(main, a, b):
    result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-C', str(main),
                             'merge-base', '--is-ancestor', a, b], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(result.returncode in (0,1), 'missing/corrupt commit objects; refresh separately, no cleanup')
    return result.returncode == 0


def proof(ctx, record, binding, client):
    # Trusted REST metadata, never artifacts or PR-controlled executable code.
    repo = client.api(f'repos/{ctx["repo"]}')
    require(repo['full_name'].lower() == ctx['repo'], 'remote repository identity mismatch')
    require(repo['default_branch'] == binding['base_branch'], 'default/base branch changed')
    default = client.api(f'repos/{ctx["repo"]}/branches/{quote(binding["base_branch"], safe="")}')['commit']['sha']
    require(SHA.fullmatch(default), 'invalid fresh default head')
    pr = client.api(f'repos/{ctx["repo"]}/pulls/{binding["pr"]}')
    require(pr['number'] == binding['pr'], 'remote PR identity mismatch')
    require(pr.get('merged') is True and pr.get('merged_at') and pr['state'] == 'closed', 'PR not actually MERGED')
    require(pr['head']['repo'] and pr['head']['repo']['full_name'].lower() == ctx['repo'] and pr['base']['repo']['full_name'].lower() == ctx['repo'], 'foreign PR repository')
    require(pr['head']['ref'] == record['branch'] and pr['base']['ref'] == binding['base_branch'], 'PR branch identity mismatch')
    require(pr['head']['sha'] == binding['reviewed_head'] == record['submitted_head'], 'PR/review/submission head mismatch')
    require(pr['merge_commit_sha'] == binding['merge_sha'], 'merge commit changed')
    require(record['pr'] == binding['pr'] and binding['repo'].lower() == ctx['repo'], 'receipt/registry PR mismatch')
    issue = client.issue(record['issue'])
    require(issue['state'] == 'closed', 'issue still open/partial work; preserve reuse')
    for opened in client.pages(f'repos/{ctx["repo"]}/pulls?state=open'):
        require(opened['head']['sha'] != binding['reviewed_head'] and not (
            opened['head']['repo'] and opened['head']['repo']['full_name'].lower() == ctx['repo'] and
            opened['head']['ref'] == record['branch']), 'another open PR uses this branch/head')
    merge = client.api(f'repos/{ctx["repo"]}/git/commits/{binding["merge_sha"]}')
    require(merge['sha'] == binding['merge_sha'], 'remote merge identity mismatch')
    require(merge['tree']['sha'] == binding['merge_tree'], 'remote merge tree mismatch')
    main, head, landed = ctx['main'], binding['reviewed_head'], binding['merge_sha']
    parents = state.git(main, 'rev-list', '--parents', '-n', '1', landed).decode().split()[1:]
    require(parents and parents[0] == binding['base_sha'] and parents == [p['sha'] for p in merge['parents']], 'merge base/parents mismatch')
    require(state.git(main, 'rev-parse', landed+'^{tree}').decode().strip() == binding['merge_tree'], 'local/remote landed tree mismatch')
    require(ancestor(main, landed, default), 'merge not reachable from fresh default branch')
    if ancestor(main, head, landed):
        kind, aggregate = 'reachable', None
    else:
        require(len(parents) == 1, 'unsupported merge topology')
        bases = state.git(main, 'merge-base', '--all', binding['base_sha'], head).decode().split()
        require(len(bases) == 1, 'ambiguous aggregate merge base')
        args = ('diff-tree', '-r', '--raw', '-z', '--no-commit-id', '--no-renames', '--no-ext-diff', '--no-textconv', '--no-abbrev')
        proposed = state.git(main, *args, bases[0], head)
        actual = state.git(main, *args, binding['base_sha'], landed)
        require(proposed and proposed == actual, 'squash/rebase aggregate blob/mode/path proof differs; hold')
        kind, aggregate = 'exact-single-parent-aggregate', hashlib.sha256(proposed).hexdigest()
    recovery_size = int(state.git(main, 'rev-list', '--disk-usage', '--objects', head).decode().strip())
    require(0 < recovery_size <= 256 * 1024 * 1024, 'self-contained recovery estimate exceeds 256 MiB bound')
    # Local reads LAST, after potentially long remote calls.
    local = local_snapshot(ctx, record)
    require(local['head'] == head, 'additional local commit/head; preserve')
    for h in local['history']:
        require(ancestor(main, h, head) or ancestor(main, h, default), 'unique reflog/ORIG_HEAD commit; preserve')
    return dict(binding=binding, default_head=default, kind=kind, aggregate=aggregate, recovery_size=recovery_size, local=local, registry_record=record)


def inspect(ctx, binding, client):
    data = state.load(ctx['common'])
    inventory = records(ctx['main'])
    reports = [dict(path=r['worktree'], status='report-only', reason='no matching AW creation/released PR provenance') for r in inventory]
    candidates = [r for r in data['trees'].values() if r.get('pr') == binding['pr']]
    require(len(candidates) == 1, 'requires exactly one AW-created tree for this PR; historical/app trees report-only')
    evidence = proof(ctx, candidates[0], binding, client)
    for report in reports:
        if report['path'] == evidence['local']['path']:
            report.update(status='eligible', reason='fresh proof recorded; explicit apply still required')
    return evidence, reports


def read_json(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size < 4_000_000, 'invalid/oversized input file')
    return state.strict_json(path.read_text())


def apply(ctx, plan, client):
    require(plan.get('status') == 'eligible', 'held/non-eligible dry-run is never applicable')
    require(plan.get('schema') == 1 and plan.get('context') == ctx and plan.get('code_hash') == code_hash(), 'plan version/repo/controller mismatch')
    require(isinstance(plan.get('created'), int) and 0 <= time.time()-plan['created'] <= 600, 'expired/future plan; generate a new dry-run')
    expected = plan['evidence']
    binding = receipt(expected['binding'])
    fresh, _ = inspect(ctx, binding, client)
    require(fresh == expected, 'remote/owner/path/content changed since dry-run')
    identity = digest(dict(context=ctx, code_hash=plan['code_hash'], evidence=expected))
    store = Path(ctx['common']) / 'issue-work.maintenance'
    require(not store.is_symlink(), 'symlink recovery store')
    store.mkdir(mode=0o700, exist_ok=True)
    archive = store / identity
    require(not archive.exists(), 'existing apply journal: inspect uncertain outcome; never replay')
    archive.mkdir(mode=0o700)
    # Keep branch; preserve full branch history plus all allowed worktree admin
    # evidence before removal. Dirty/ignored/unique content never enters apply.
    bundle = archive / 'branch.bundle'
    state.git(ctx['main'], 'bundle', 'create', str(bundle), 'refs/heads/'+fresh['registry_record']['branch'])
    os.chmod(bundle, 0o600)
    require(bundle.stat().st_size <= 256 * 1024 * 1024, 'recovery bundle exceeds bound; preserve source tree')
    state.git(ctx['main'], 'bundle', 'verify', str(bundle))
    heads = state.git(ctx['main'], 'bundle', 'list-heads', str(bundle)).decode().splitlines()
    require(heads == [fresh['local']['head']+' refs/heads/'+fresh['registry_record']['branch']], 'recovery bundle identity mismatch')
    saved = {'branch.bundle': hashlib.sha256(bundle.read_bytes()).hexdigest()}
    for name, sha in fresh['local']['admin_hashes'].items():
        data = (Path(fresh['registry_record']['identity']['admin'])/name).read_bytes()
        require(hashlib.sha256(data).hexdigest() == sha, 'admin content changed while archiving')
        target = archive/'admin'/name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o600)
        saved['admin/'+name] = sha
    journal = archive / 'receipt.json'
    journal.write_text(json.dumps(dict(plan=plan, saved=saved, outcome='prepared'), sort_keys=True)+'\n')
    journal.chmod(0o600)
    # Repeat all trusted reads under the same guard, with local content/owner
    # checks last. The remaining filesystem race assumes cooperating AW owners.
    final, _ = inspect(ctx, binding, client)
    require(final == fresh, 'changed during recovery preparation; removal aborted')
    for name, sha in saved.items():
        target = archive/name
        require(target.is_file() and not target.is_symlink() and hashlib.sha256(target.read_bytes()).hexdigest() == sha, 'recovery evidence changed; hold')
    state.git(ctx['main'], 'bundle', 'verify', str(bundle))
    require(local_snapshot(ctx, final['registry_record']) == final['local'], 'local state changed immediately before removal')
    try:
        filter_keys, options = filter_options(fresh['local']['path'])
        require(filter_keys == fresh['local']['filter_keys'], 'filter identity changed before removal')
        require(index_safety.inspect(fresh['local']['path']) == fresh['local']['index'], 'index visibility changed immediately before removal')
        state.git(ctx['main'], *options, 'worktree', 'remove', fresh['local']['path'])
    except (OSError, subprocess.CalledProcessError):
        journal.write_text(json.dumps(dict(plan=plan, saved=saved, outcome='uncertain; inspect registration/path before any retry'), sort_keys=True)+'\n')
        raise
    require(not Path(fresh['local']['path']).exists() and not any(r['worktree'] == fresh['local']['path'] for r in records(ctx['main'])), 'removal outcome uncertain; inspect receipt before retry')
    require(state.git(ctx['main'], 'rev-parse', 'refs/heads/'+fresh['registry_record']['branch']).decode().strip() == fresh['local']['head'], 'branch changed during removal; recovery retained')
    journal.write_text(json.dumps(dict(plan=plan, saved=saved, outcome='removed; branch retained'), sort_keys=True)+'\n')
    data = state.load(ctx['common'])
    previous = data['trees'].pop(fresh['local']['path'])
    data.setdefault('retired', []).append(dict(record=previous, recovery=str(archive), binding=binding))
    state.save(ctx['common'], data)
    return dict(outcome='removed', path=fresh['local']['path'], recovery=str(archive), branch_retained=fresh['registry_record']['branch'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan','apply'), nargs='?', default='plan')
    parser.add_argument('--receipt', help='Codex verified merge binding JSON; required for dry-run')
    parser.add_argument('--plan', help='previous dry-run JSON; required for explicit apply')
    args = parser.parse_args(argv)
    ctx = state.context(Path.cwd())
    with (Path(ctx['common'])/'issue-work.guard').open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps(dict(status='held', reason='repository guard busy')))
            return 3
        client = queue.Client(ctx['repo'])
        if args.operation == 'plan':
            require(args.receipt and not args.plan, 'plan requires --receipt only')
            binding = receipt(read_json(args.receipt))
            try:
                evidence, reports = inspect(ctx, binding, client)
            except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
                reports = [dict(path=r['worktree'], status='report-only') for r in records(ctx['main'])]
                print(json.dumps(dict(schema=1, status='held', reason=str(exc), context=ctx, reports=reports), sort_keys=True))
                return 3
            print(json.dumps(dict(schema=1, status='eligible', context=ctx, created=int(time.time()), code_hash=code_hash(), evidence=evidence, reports=reports), sort_keys=True))
        else:
            require(args.plan and not args.receipt, 'apply requires --plan only')
            print(json.dumps(apply(ctx, read_json(args.plan), client), sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'held: {exc}; no automatic retry/prune/force', file=sys.stderr)
        sys.exit(2)
