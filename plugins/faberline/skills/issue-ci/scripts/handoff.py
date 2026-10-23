#!/usr/bin/env python3
"""Trusted CI controller. Reads GitHub metadata, never executes PR code/artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote


def api(path, method='GET', data=None):
    command = ['gh', 'api', '--method', method, path]
    kwargs = dict(text=True)
    if data is not None:
        command += ['--input', '-']
        kwargs['input'] = json.dumps(data)
    output = subprocess.check_output(command, **kwargs)
    return json.loads(output) if output.strip() else None


def pages(path, key=None):
    items = []
    for page in range(1, 1001):
        value = api(f'{path}{"&" if "?" in path else "?"}per_page=100&page={page}')
        batch = value[key] if key else value
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise RuntimeError('pagination limit reached; refuse partial evidence')


def load_config(path):
    config = json.loads(Path(path).read_text())
    workflows = config['workflows']
    if not workflows or len({w['path'] for w in workflows}) != len(workflows):
        raise RuntimeError('required workflow paths must be nonempty and unique')
    for w in workflows:
        if not re.fullmatch(r'\.github/workflows/[\w.-]+\.ya?ml', w['path']) or not w['jobs'] or not all(isinstance(j, str) and j.strip() for j in w['jobs']) or len(set(w['jobs'])) != len(w['jobs']):
            raise RuntimeError('each workflow needs an explicit nonempty unique job-name list')
    return config


def evaluate(workflows, snapshots):
    """Aggregate latest runs of ALL configured workflows; no vacuous all-green."""
    pending, failures, evidence = [], [], []
    for workflow in workflows:
        path = workflow['path']
        snapshot = snapshots.get(path)
        if not snapshot:
            pending.append(f'{path}: missing run')
            continue
        run, jobs = snapshot
        evidence.append(f'{run["id"]}:{run.get("run_attempt", 1)}')
        if run['status'] != 'completed':
            pending.append(f'{path}: {run["status"]}')
        elif run.get('conclusion') in ('failure', 'timed_out', 'action_required', 'startup_failure'):
            failures.append(f'{path}: {run["conclusion"]} {run["html_url"]}')
        elif run.get('conclusion') != 'success':
            pending.append(f'{path}: {run.get("conclusion")} {run["html_url"]}')
        for name in workflow['jobs']:
            matching = [j for j in jobs if j['name'] == name]
            if not matching:
                pending.append(f'{path} / {name}: missing job')
            for job in matching:
                link = job['html_url']
                if job['status'] != 'completed':
                    pending.append(f'{path} / {name}: {job["status"]} {link}')
                elif job.get('conclusion') in ('failure', 'timed_out', 'action_required', 'startup_failure'):
                    failures.append(f'{path} / {name}: {job["conclusion"]} {link}')
                elif job.get('conclusion') != 'success':
                    # Skips need a separate future policy; never assume justification.
                    pending.append(f'{path} / {name}: {job.get("conclusion")} {link}')
    state = 'todo' if failures else 'pending' if pending else 'done'
    return state, failures + pending, sorted(evidence)


def snapshot(repo, sha, config):
    root = f'repos/{repo}'
    snapshots = {}
    for workflow in config['workflows']:
        runs = pages(f'{root}/actions/workflows/{quote(workflow["path"].split("/")[-1], safe="")}/runs?head_sha={sha}&event=pull_request', 'workflow_runs')
        runs = [r for r in runs if r['head_sha'] == sha and r.get('event') == 'pull_request' and r.get('head_repository', {}).get('full_name') == repo and r.get('path') == workflow['path']]
        if not runs:
            continue
        latest = max(runs, key=lambda r: (r['run_number'], r.get('run_attempt', 1)))
        # Run-attempt endpoint prevents jobs from a previous attempt filling holes.
        jobs = pages(f'{root}/actions/runs/{latest["id"]}/attempts/{latest.get("run_attempt", 1)}/jobs', 'jobs')
        snapshots[workflow['path']] = latest, jobs
    return snapshots


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--config', required=True, help='trusted default-branch config, never from PR')
    parser.add_argument('--pr', required=True, type=int)
    parser.add_argument('--head', required=True)
    parser.add_argument('--run-id', type=int, help='workflow_run event ID; required for mutation')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', args.repo) or not re.fullmatch('[0-9a-f]{40}', args.head):
        raise RuntimeError('invalid repository or SHA')
    config = load_config(args.config)
    root = f'repos/{args.repo}'
    pr_path = f'{root}/pulls/{args.pr}'
    pr = api(pr_path)
    if pr['state'] != 'open' or pr['head']['sha'] != args.head or pr['head']['repo']['full_name'] != args.repo:
        raise RuntimeError('closed, foreign, or stale PR head; no mutation')
    match = re.fullmatch(r'(?:issue/|agy/issue-)([0-9]+)', pr['head']['ref'])
    if not match:
        raise RuntimeError('PR branch has no supported issue association')
    issue = int(match[1])
    if not re.search(rf'(?im)^Closes #{issue}\s*$', pr.get('body') or ''):
        raise RuntimeError('PR body must explicitly link its branch issue with Closes #N')
    snapshots = snapshot(args.repo, args.head, config)
    state, reasons, evidence = evaluate(config['workflows'], snapshots)
    print(json.dumps(dict(state=state, head=args.head, evidence=evidence, reasons=reasons)))
    if not args.apply:
        return 0 if state == 'done' else 2
    if args.run_id is None:
        raise RuntimeError('--apply requires workflow_run event --run-id')
    event_run = api(f'{root}/actions/runs/{args.run_id}')
    if event_run['head_sha'] != args.head or event_run.get('event') != 'pull_request' or str(event_run['id']) not in {e.split(':')[0] for e in evidence}:
        raise RuntimeError('stale/unconfigured triggering run; no mutation')
    if state == 'pending':
        return 0
    issue_path = f'{root}/issues/{issue}'
    current = api(issue_path)
    labels = {l['name'] for l in current['labels']}
    if current['state'] != 'open' or not labels & {'todo', 'doing', 'done'} or 'blocked' in labels:
        raise RuntimeError('issue is closed, blocked, or unmanaged; no mutation')
    # A failed head is handed back once, even if another workflow finishes
    # later or a run is retried while AGY is already coding the correction.
    # Success still keys all latest run attempts and the trusted gate policy.
    receipt_runs = evidence if state == 'done' else []
    digest = hashlib.sha256(json.dumps([args.head, receipt_runs, config, state], sort_keys=True).encode()).hexdigest()
    marker = f'<!-- issue-ci:{digest} -->'
    comments = pages(f'{issue_path}/comments')
    duplicate = any(marker in c['body'] and c['user']['login'] == 'github-actions[bot]' for c in comments)
    if duplicate:
        print('duplicate head/run handoff; no mutation')
        return 0
    # Re-read immediately before writes. Workflow concurrency must also serialize
    # all controller invocations for this repository (see the template).
    fresh = api(pr_path)
    if fresh['state'] != 'open' or fresh['head']['sha'] != args.head:
        raise RuntimeError('PR moved during evaluation; no mutation')
    detail = '\n'.join(reasons) if reasons else 'All explicitly required workflow/job gates passed.'
    urls = '\n'.join(s[0]['html_url'] for s in snapshots.values())
    api(f'{issue_path}/comments', 'POST', {'body': f'{marker}\nCI: {state} for PR #{args.pr} head `{args.head}`.\n{detail}\n{urls}'})
    # Journal first: a replay must never undo a worker's later todo -> doing.
    # If interrupted mid-write, report/reconcile manually; do not replay coding.
    fresh = api(pr_path)
    if fresh['head']['sha'] != args.head or fresh['state'] != 'open':
        raise RuntimeError('PR moved before label write; no label mutation')
    if snapshot(args.repo, args.head, config) != snapshots:
        raise RuntimeError('CI runs changed during evaluation; wait for next handoff')
    # Preserve unrelated labels; partial/API failures are surfaced, never hidden.
    if state not in labels:
        api(f'{issue_path}/labels', 'POST', {'labels': [state]})
    for old in sorted((labels & {'todo', 'doing', 'done'}) - {state}):
        api(f'{issue_path}/labels/{old}', 'DELETE')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        sys.exit(2)
