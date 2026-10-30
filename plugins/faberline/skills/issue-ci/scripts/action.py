#!/usr/bin/env python3
"""Pinned action entrypoint: fetch only the consumer's default-branch policy."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile


def controller():
    spec = importlib.util.spec_from_file_location('issue_ci_handoff', Path(__file__).with_name('handoff.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trusted_policy(ci, repo, path, metadata=False):
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise RuntimeError('invalid consumer repository')
    if not re.fullmatch(r'\.github/[\w.-]+\.json', path):
        raise RuntimeError('policy must be a JSON file directly in .github')
    meta = ci.api(f'repos/{repo}')
    branch = meta['default_branch']
    from urllib.parse import quote
    revision = ci.api(f'repos/{repo}/commits/{quote(branch, safe="")}')['sha']
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise RuntimeError('default branch has no immutable revision')
    data = ci.api(f'repos/{repo}/contents/{path}?ref={revision}')
    if data.get('encoding') != 'base64' or data.get('type') != 'file':
        raise RuntimeError('trusted policy is not a GitHub file')
    content = base64.b64decode(''.join(data['content'].split()), validate=True)
    if len(content) > 262144:
        raise RuntimeError('trusted policy exceeds size limit')
    # Parse before writing. No PR config, artifact or checkout is consulted.
    config = ci.strict_json_loads(content)
    if metadata:
        return dict(config=config, source_commit=revision, path=path)
    return content


def ci_module(name):
    spec = importlib.util.spec_from_file_location("issue_ci_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if not re.fullmatch('[0-9a-f]{40}', os.environ.get('AW_ACTION_REF', '')):
        raise RuntimeError('the shared action must use a reviewed immutable 40-hex commit reference')
    ci = controller()
    repo = os.environ['GITHUB_REPOSITORY']
    operation = os.environ.get('INPUT_OPERATION', 'handoff')
    if operation not in ('validate', 'qualify', 'handoff', 'readiness', 'verify-readiness', 'index', 'verify-index',
                         'development-preflight', 'development-plan'):
        raise RuntimeError('unknown issue-ci operation')
    if operation in ('development-preflight', 'development-plan'):
        from types import SimpleNamespace
        development = ci_module('development_pipeline')
        pr, head = os.environ.get('INPUT_PR', ''), os.environ.get('INPUT_HEAD', '')
        if not re.fullmatch('[1-9][0-9]*', pr) or not re.fullmatch('[0-9a-f]{40}', head):
            raise RuntimeError('development stages require PR number and exact head')
        adapter = SimpleNamespace(api=ci.api, pages=ci.pages)
        result = development.preflight(adapter, repo, int(pr), head, os.environ.get('INPUT_BASE') or None)
        if operation == 'development-plan' and result['status'] == 'success':
            document = trusted_policy(ci, repo, os.environ.get('INPUT_POLICY', '.github/issue-ci-required-gates.json'), metadata=True)
            ci.validate_config(document['config'])
            if document['source_commit'] != result['base']:
                raise RuntimeError('default policy moved during impact planning')
            pull = ci.api(f'repos/{repo}/pulls/{pr}')
            if pull.get('base', {}).get('sha') != result['base']:
                raise RuntimeError('PR base metadata does not match frozen current main')
            scoped = ci.qualify_config(repo, pull, document['config'])
            result['plan'] = scoped['_qualification_context']
            result['policy_source'] = document['source_commit']
            result['plan_sha256'] = development.digest(result['plan'])
            if result['plan']['issues']:
                result['status'] = 'failure'
                result['reasons'].extend(result['plan']['issues'])
            fresh = development.preflight(adapter, repo, int(pr), head, result['base'])
            if any(fresh[k] != result[k] for k in ('base', 'head', 'issue', 'issue_sha256')):
                raise RuntimeError('PR or issue moved during impact planning')
        encoded = json.dumps(result, sort_keys=True, separators=(',', ':'))
        print('AW_DEVELOPMENT_PIPELINE_V1 ' + encoded, flush=True)
        output = os.environ.get('GITHUB_OUTPUT')
        if output:
            with open(output, 'a') as stream:
                for key in ('base', 'head', 'issue', 'status', 'plan_sha256'):
                    if result.get(key) is not None:
                        stream.write(f'{key}={result[key]}\n')
                stream.write('result=' + encoded + '\n')
        return 0 if result['status'] == 'success' else 2
    if operation in ('index', 'verify-index'):
        index = ci_module('readiness_index')
        load_document = lambda: trusted_policy(ci, repo, os.environ.get('INPUT_POLICY', '.github/issue-ci-required-gates.json'), metadata=True)
        if operation == 'verify-index':
            verified = index.discover(ci, repo, load_document, os.environ['AW_ACTION_REF'])
            summary = index.summary(verified)
            print(index.r.canonical(summary))
            return {'ready': 0, 'no_ready': 3, 'blocked': 2}[summary['status']]
        document = load_document()
        index.r.validate_contract(ci, document['config'])
        event = ci.strict_json_loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
        wake = event.get('workflow_run', {})
        if (event.get('action') != 'completed' or wake.get('status') != 'completed'
                or wake.get('event') != 'pull_request'
                or wake.get('head_repository', {}).get('full_name') != repo
                or wake.get('path') == document['config']['review_readiness']['producer']['path']
                or wake.get('path') not in {w['path'] for w in document['config']['workflows']}):
            raise RuntimeError('ignore self/foreign/unconfigured/nonterminal index wake')
        run_id = os.environ.get('GITHUB_RUN_ID', '')
        if not re.fullmatch('[1-9][0-9]*', run_id):
            raise RuntimeError('index must run in its configured GitHub producer')
        index.emit(index.produce(ci, repo, load_document, os.environ['AW_ACTION_REF'], int(run_id)))
        return 0
    if operation in ('readiness', 'verify-readiness'):
        readiness = ci_module('readiness')
        pr, head = os.environ.get('INPUT_PR', ''), os.environ.get('INPUT_HEAD', '')
        if not re.fullmatch('[1-9][0-9]*', pr) or not re.fullmatch('[0-9a-f]{40}', head):
            raise RuntimeError('readiness requires PR number and exact head SHA')
        load_document = lambda: trusted_policy(ci, repo, os.environ.get('INPUT_POLICY', '.github/issue-ci-required-gates.json'), metadata=True)
        if operation == 'verify-readiness':
            receipt = readiness.verify_file(ci, os.environ.get('INPUT_RECEIPT', ''), repo, int(pr), head,
                                            load_document, os.environ['AW_ACTION_REF'])
            print(readiness.canonical(receipt))
            return {'ready': 0, 'wait': 3, 'fail': 2}[receipt['payload']['status']]
        document = load_document()
        readiness.validate_contract(ci, document['config'])
        event_path = os.environ.get('GITHUB_EVENT_PATH', '')
        event = ci.strict_json_loads(Path(event_path).read_text())
        wake = event.get('workflow_run', {})
        if (wake.get('path') == document['config']['review_readiness']['producer']['path']
                or wake.get('path') not in {w['path'] for w in document['config']['workflows']}):
            raise RuntimeError('ignore producer/unconfigured wake; no readiness receipt')
        run_id = os.environ.get('GITHUB_RUN_ID', '')
        if not re.fullmatch('[1-9][0-9]*', run_id):
            raise RuntimeError('readiness must run in the configured GitHub producer')
        receipt = readiness.produce(ci, repo, int(pr), head, load_document,
                                    os.environ['AW_ACTION_REF'], int(run_id))
        print(readiness.RECEIPT_MARKER + readiness.canonical(receipt))
        # A successfully published wait/fail receipt is valid diagnostic output.
        # Verification returns distinct status; production must finish for provenance.
        return 0
    content = trusted_policy(ci, repo, os.environ.get('INPUT_POLICY', '.github/issue-ci-required-gates.json'))
    with tempfile.TemporaryDirectory(prefix='issue-ci-policy-') as tmp:
        path = Path(tmp) / 'policy.json'
        path.write_bytes(content)
        ci.validate_adoption(ci.load_config(path))
        if operation == 'validate':
            print('Trusted default-branch policy validated; no lifecycle mutation.')
            return 0
        args = ['handoff.py', '--repo', repo, '--config', str(path)]
        if operation == 'handoff':
            run = os.environ.get('INPUT_RUN_ID', '')
            if not re.fullmatch('[1-9][0-9]*', run):
                raise RuntimeError('handoff requires a workflow_run ID')
            args += ['--reconcile', '--run-id', run]
        else:
            pr, head = os.environ.get('INPUT_PR', ''), os.environ.get('INPUT_HEAD', '')
            if not re.fullmatch('[1-9][0-9]*', pr) or not re.fullmatch('[0-9a-f]{40}', head):
                raise RuntimeError('qualify requires PR number and exact head SHA')
            args += ['--pr', pr, '--head', head]
        sys.argv = args
        return ci.main()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        sys.exit(2)
