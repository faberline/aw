"""Build the exact issue binding for repositories that explicitly adopt v3."""
import hashlib
import json
import re


def snapshot(repo, number, issue, spec):
    if (not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or type(number) is not int or number <= 0 or not isinstance(issue, dict)
            or issue.get('number') != number or issue.get('state') != 'open'
            or issue.get('url') != f'https://api.github.com/repos/{repo}/issues/{number}'
            or issue.get('repository_url') != f'https://api.github.com/repos/{repo}'
            or 'pull_request' in issue):
        raise ValueError('invalid current issue identity')
    result = dict(schema='AW_ISSUE_SNAPSHOT_V1', repository=repo, issue=number)
    for field in ('title', 'body'):
        value = issue.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('missing issue ' + field)
        result[field + '_sha256'] = hashlib.sha256(value.encode('utf-8')).hexdigest()
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError('missing planner specification')
    result['spec_sha256'] = hashlib.sha256(spec.encode('utf-8')).hexdigest()
    return result


def encoded(repo, number, issue, spec):
    return json.dumps(snapshot(repo, number, issue, spec), sort_keys=True, indent=2) + '\n'
