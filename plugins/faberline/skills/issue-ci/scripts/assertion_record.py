"""Identity/nonzero emitter for a reviewed consumer enforcer, not a test runner.

The caller owns and actually executes its assertions. This helper never invents
results, runs test commands, queries Actions, or changes labels/permissions.
"""
import importlib.util
import os
from pathlib import Path
import re
import subprocess

_spec = importlib.util.spec_from_file_location('assertion_readiness', Path(__file__).with_name('readiness.py'))
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)


def execution_context(checkout, environ=None):
    env = os.environ if environ is None else environ
    r.need(env.get('GITHUB_EVENT_NAME') == 'pull_request', 'assertion_context', 'only an actual pull_request test job')
    raw = Path(env['GITHUB_EVENT_PATH']).read_bytes()
    r.need(len(raw) <= 262144, 'assertion_context', 'event payload exceeds limit')
    event = r.strict_json_loads(raw)
    pull = event['pull_request']
    repo = env['GITHUB_REPOSITORY']
    number = event.get('number')
    head, base = pull['head']['sha'], pull['base']['sha']
    r.need(event.get('repository', {}).get('full_name') == repo
           and pull['base'].get('repo', {}).get('full_name') == repo
           and r.REPO.fullmatch(repo) and type(number) is int and number > 0
           and r.HEX.fullmatch(head) and r.HEX.fullmatch(base), 'assertion_context', 'exact PR/repo/head/base required')
    run, attempt = env['GITHUB_RUN_ID'], env['GITHUB_RUN_ATTEMPT']
    r.need(re.fullmatch('[1-9][0-9]*', run) and re.fullmatch('[1-9][0-9]*', attempt),
           'assertion_context', 'exact run/attempt required')
    # Metadata only; never execute a PR script, fsmonitor or test command here.
    git = ['git', '-c', 'core.fsmonitor=false', '-C', str(checkout)]
    measured = subprocess.check_output(git + ['rev-parse', '--verify', 'HEAD^{commit}'], text=True).strip()
    base_type = subprocess.check_output(git + ['cat-file', '-t', base], text=True).strip()
    r.need(measured == head and base_type == 'commit', 'assertion_checkout',
           'checkout must measure the current PR head and contain the frozen base commit')
    return dict(repo=repo, pr=number, head=measured, base=base, run_id=int(run), run_attempt=int(attempt))


def publish(check, expected_names, observed, checkout, started, environ=None):
    current = execution_context(checkout, environ)
    r.need(started == current, 'assertion_checkout', 'execution context moved while assertions ran')
    r.need(isinstance(check, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', check)
           and isinstance(expected_names, list) and bool(expected_names)
           and all(isinstance(n, str) and n.strip() for n in expected_names)
           and len(set(expected_names)) == len(expected_names)
           and isinstance(observed, dict) and set(observed) == set(expected_names)
           and all(value is True for value in observed.values()),
           'assertion_results', 'actual nonzero exact roster must all pass; no missing/skip/false results')
    record = dict(version=2, **current, check=check,
                  assertions={name: 'passed' for name in sorted(observed)})
    print(r.ASSERTION_V2_MARKER + r.canonical(record))
    return record
