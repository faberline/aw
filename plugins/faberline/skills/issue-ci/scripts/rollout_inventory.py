"""Read-only GitHub Actions inventory; it is never rollout authorization.

Two matching reads are useful evidence, not a lock. Local or cloud launchers,
manual users, later GitHub runs, and in-flight work outside Actions are not
visible here. A consumer must not treat this report as proof of quiescence.
"""
import re


STATUSES = ('queued', 'in_progress', 'requested', 'waiting', 'pending')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_PAGES = 1000


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def snapshot(api, repo):
    """Return every visible nonterminal run, or refuse partial evidence."""
    need(isinstance(repo, str) and REPO.fullmatch(repo), 'inventory_repository')
    rows, ids = [], set()
    for status in STATUSES:
        total = None
        count = 0
        for page in range(1, MAX_PAGES + 1):
            result = api.api(f'repos/{repo}/actions/runs?status={status}&per_page=100&page={page}')
            need(isinstance(result, dict) and type(result.get('total_count')) is int
                 and result['total_count'] >= 0 and isinstance(result.get('workflow_runs'), list)
                 and len(result['workflow_runs']) <= 100, 'inventory_page_unknown')
            if total is None:
                total = result['total_count']
            need(total == result['total_count'], 'inventory_changed')
            batch = result['workflow_runs']
            for run in batch:
                need(isinstance(run, dict) and type(run.get('id')) is int and run['id'] > 0
                     and run['id'] not in ids and type(run.get('run_attempt')) is int and run['run_attempt'] > 0
                     and type(run.get('workflow_id')) is int and run['workflow_id'] > 0
                     and run.get('status') == status and isinstance(run.get('head_sha'), str)
                     and SHA.fullmatch(run['head_sha']) and isinstance(run.get('path'), str)
                     and re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', run['path'])
                     and isinstance(run.get('head_repository'), dict)
                     and isinstance(run['head_repository'].get('full_name'), str)
                     and REPO.fullmatch(run['head_repository']['full_name']), 'inventory_run_unknown')
                ids.add(run['id'])
                row = {key: run[key] for key in ('id', 'run_attempt', 'workflow_id', 'status', 'head_sha', 'path')}
                row['head_repository'] = run['head_repository']['full_name']
                rows.append(row)
            count += len(batch)
            need(count <= total, 'inventory_changed')
            if count == total:
                break
            need(len(batch) == 100, 'inventory_page_unknown')
        else:
            raise RuntimeError('inventory_page_limit')
    return sorted(rows, key=lambda row: row['id'])


def observe(api, repo):
    """Compare two reads and state explicitly that actor rollout is unknown."""
    first = snapshot(api, repo)
    second = snapshot(api, repo)
    need(first == second, 'inventory_changed')
    return dict(version=1, repo=repo, runs=first, actor_rollout='unknown',
                quiescence_proven=False, authorizes_writes=False)
