#!/usr/bin/env python3
"""Inspect a candidate contract as untrusted data. Never grants qualification."""
import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


def load(name):
    spec = importlib.util.spec_from_file_location('proposal_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = load('handoff')
q = ci.qualification_module()
POLICY = '.github/issue-ci-required-gates.json'


def require(condition, message):
    if not condition:
        raise RuntimeError('candidate contract inspection: ' + message)


def identity(pr, repo, number, head, base):
    return (isinstance(pr, dict) and type(number) is int and number > 0 and pr.get('number') == number
            and pr.get('state') == 'open' and pr.get('head', {}).get('sha') == head
            and pr.get('base', {}).get('sha') == base
            and pr.get('head', {}).get('repo', {}).get('full_name') == repo
            and pr.get('base', {}).get('repo', {}).get('full_name') == repo)


def inspect(repo, event, api):
    require(isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo), 'bad repository')
    require(isinstance(event, dict) and event.get('repository', {}).get('full_name') == repo, 'event repository mismatch')
    pr = event.get('pull_request'); number = event.get('number')
    require(isinstance(pr, dict), 'missing PR event')
    head, base = pr.get('head', {}).get('sha'), pr.get('base', {}).get('sha')
    require(isinstance(head, str) and re.fullmatch('[0-9a-f]{40}', head)
            and isinstance(base, str) and re.fullmatch('[0-9a-f]{40}', base)
            and identity(pr, repo, number, head, base), 'bad event PR identity')
    path = f'repos/{repo}/pulls/{number}'
    current = api(path)
    require(identity(current, repo, number, head, base), 'stale or foreign PR')
    issue_number = q.linked_issue(current)
    require(issue_number is not None, 'one canonical linked issue required')
    issue_path = f'repos/{repo}/issues/{issue_number}'
    issue = api(issue_path)
    data = api(f'repos/{repo}/contents/{POLICY}?ref={head}')
    require(isinstance(data, dict) and data.get('type') == 'file' and data.get('encoding') == 'base64'
            and isinstance(data.get('sha'), str) and re.fullmatch('[0-9a-f]{40}', data['sha'])
            and type(data.get('size')) is int and 0 < data['size'] <= 1048576
            and isinstance(data.get('content'), str), 'invalid candidate policy metadata')
    try:
        raw = base64.b64decode(''.join(data['content'].split()), validate=True)
        require(len(raw) == data['size'], 'candidate policy size mismatch')
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
        require(blob == data['sha'], 'candidate policy blob mismatch')
        config = ci.strict_json_loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise RuntimeError('candidate policy encoding or JSON is invalid') from error
    ci.validate_config(config)
    require(config['qualification']['version'] == 2, 'candidate contract version 2 required')
    context, issues, selected = q.issue_contract(config['qualification'], current, issue)
    require(not issues, '; '.join(issues))
    final = api(path); final_issue = api(issue_path)
    require(identity(final, repo, number, head, base) and final.get('body') == current.get('body')
            and final.get('title') == current.get('title') and final_issue == issue,
            'PR or linked issue changed during inspection')
    return dict(schema='ISSUE_CONTRACT_INSPECTION_V1', qualification='not_evaluated',
                policy_approval='not_evaluated', pr=number, head=head, base=base,
                proposed_policy_blob=data['sha'], issue=context['issue'],
                title_sha256=context['title_sha256'], body_sha256=context['body_sha256'],
                contract_sha256=context['contract_sha256'], selected=sorted(selected))


def main():
    require(os.environ.get('GITHUB_EVENT_NAME') == 'pull_request', 'pull_request event required')
    event = ci.strict_json_loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    print(json.dumps(inspect(os.environ.get('GITHUB_REPOSITORY'), event, ci.api), sort_keys=True))


if __name__ == '__main__':
    main()
