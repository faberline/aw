"""Validate planner-supplied acceptance-to-check mappings without executing them.

This module validates structure only. It is not a qualification decision and
does not establish that a named check actually proves an acceptance item.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re

MARKER = '<!-- issue-plan:acceptance-map:v1 -->'
SPEC_MARKER = '<!-- issue-plan:spec -->'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate mapping field: ' + key)
        result[key] = value
    return result


def decode(text):
    if not isinstance(text, str):
        raise ValueError('mapping must be JSON text')
    try:
        return json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid JSON constant')))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid mapping JSON') from exc


def acceptance_items(spec):
    """Extract an unambiguous numbered acceptance list from a planner spec."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError('planner specification is missing')
    headings = list(re.finditer(r'^## ([^\r\n]+)\s*$', spec, re.MULTILINE))
    matches = [i for i, match in enumerate(headings) if match.group(1).strip() == 'Acceptance']
    if len(matches) != 1:
        raise ValueError('exactly one Acceptance section is required')
    index = matches[0]
    start = headings[index].end()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(spec)
    lines = spec[start:end].splitlines()
    items = []
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(r'\s*- \[ \] (\S(?:.*\S)?)\s*', line)
        if not match:
            raise ValueError('Acceptance must contain single-line unchecked items only')
        items.append(match.group(1))
    if not items or len(set(items)) != len(items):
        raise ValueError('Acceptance items must be nonempty and unique')
    return items


def validate(spec, text, check_ids):
    """Return a digest and mappings only when all issue items name known checks."""
    if (not isinstance(check_ids, (list, tuple, set))
            or not check_ids or any(not isinstance(item, str) or not item.strip() for item in check_ids)):
        raise ValueError('trusted check inventory is missing or invalid')
    known = set(check_ids)
    if len(known) != len(check_ids):
        raise ValueError('trusted check inventory has duplicates')
    obj = decode(text)
    if not isinstance(obj, dict) or set(obj) != {'schema', 'spec_sha256', 'items'}:
        raise ValueError('invalid mapping fields')
    if obj['schema'] != 'AW_ACCEPTANCE_PLAN_V1':
        raise ValueError('unknown mapping schema')
    digest = hashlib.sha256(spec.encode('utf-8')).hexdigest() if isinstance(spec, str) else ''
    if obj['spec_sha256'] != digest:
        raise ValueError('mapping does not bind the current planner specification')
    expected = acceptance_items(spec)
    rows = obj['items']
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('mapping must cover each Acceptance item once')
    for expected_item, row in zip(expected, rows):
        if not isinstance(row, dict) or set(row) != {'acceptance', 'checks'} or row['acceptance'] != expected_item:
            raise ValueError('mapping items differ from the current Acceptance list')
        checks = row['checks']
        if (not isinstance(checks, list) or not checks
                or any(not isinstance(name, str) or name not in known for name in checks)
                or len(set(checks)) != len(checks)):
            raise ValueError('mapping names missing, duplicate or unknown checks')
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'spec_sha256': digest, 'plan_sha256': hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
            'checks': sorted({name for row in rows for name in row['checks']})}


def prepare_comment(spec, text, check_ids):
    """Produce a stable planner comment after validating every item and check."""
    validate(spec, text, check_ids)
    value = decode(text)
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(content.encode('utf-8')) + 1 > 65536:
        raise ValueError('planner acceptance mapping is too large')
    return MARKER + '\n' + content


def verify_execution(spec, text, test_plan, receipts):
    """Join the mapping to complete existing test receipts, without qualification."""
    loader = importlib.util.spec_from_file_location('aw_acceptance_test_plan',
                                                    Path(__file__).with_name('test_plan.py'))
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    module.validate_plan(test_plan)
    check_ids = [name for name in test_plan['selected'] if test_plan['checks'][name]['kind'] != 'policy']
    mapping = validate(spec, text, check_ids)
    execution = module.verify_execution(test_plan, receipts)
    return {'schema': 'AW_ACCEPTANCE_EXECUTION_V1', 'spec_sha256': mapping['spec_sha256'],
            'acceptance_plan_sha256': mapping['plan_sha256'], 'test_plan_sha256': test_plan['sha256'],
            'repo': test_plan['repo'], 'head': test_plan['head'], 'base': test_plan['base'],
            'mapped_checks': mapping['checks'], 'execution': execution,
            'qualification': 'not_evaluated'}


def published_mapping(repo, issue, spec, comments, trusted_actor_ids, check_ids):
    """Select the latest mapping from a complete, ordered GitHub comment list.

    The caller must obtain the actor allowlist from reviewed policy and must
    separately establish repository, issue and comment-list completeness. An
    untrusted or malformed newer mapping never falls back to an older one.
    """
    if (not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or not isinstance(issue, dict) or type(issue.get('number')) is not int or issue['number'] < 1
            or issue.get('state') != 'open' or 'pull_request' in issue
            or issue.get('url') != f'https://api.github.com/repos/{repo}/issues/{issue["number"]}'
            or issue.get('repository_url') != f'https://api.github.com/repos/{repo}'):
        raise ValueError('current issue identity is invalid')
    if any(not isinstance(issue.get(field), str) or not issue[field].strip() for field in ('title', 'body')):
        raise ValueError('current issue title or body is missing')
    labels = issue.get('labels')
    if (not isinstance(labels, list) or any(not isinstance(label, dict) or not isinstance(label.get('name'), str)
                                            for label in labels)
            or any(label['name'].lower() in ('blocked', 'agy:blocked') for label in labels)):
        raise ValueError('current issue labels are missing, invalid or blocked')
    if (not isinstance(trusted_actor_ids, (list, tuple, set)) or not trusted_actor_ids
            or any(type(actor) is not int or actor < 1 for actor in trusted_actor_ids)
            or len(set(trusted_actor_ids)) != len(trusted_actor_ids)):
        raise ValueError('trusted planner identity inventory is invalid')
    if not isinstance(comments, list):
        raise ValueError('complete comment inventory is required')
    previous = 0
    selected = None
    for comment in comments:
        if (not isinstance(comment, dict) or type(comment.get('id')) is not int
                or comment['id'] <= previous or not isinstance(comment.get('body'), str)):
            raise ValueError('comment inventory is malformed or not ordered')
        previous = comment['id']
        if comment['body'].startswith(MARKER):
            selected = comment
    if selected is None:
        raise ValueError('planner acceptance mapping is missing')
    actor = selected.get('user')
    if not isinstance(actor, dict) or type(actor.get('id')) is not int or actor['id'] not in trusted_actor_ids:
        raise ValueError('latest planner acceptance mapping has untrusted author')
    payload = selected['body'][len(MARKER):]
    if not payload.startswith('\n') or len(payload.encode('utf-8')) > 65536:
        raise ValueError('planner acceptance mapping is malformed or too large')
    updated = selected.get('updated_at')
    if not isinstance(updated, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', updated):
        raise ValueError('planner acceptance mapping update time is missing')
    verified = validate(spec, payload.strip(), check_ids)
    return {**verified, 'repository': repo, 'issue': issue['number'],
            'title_sha256': hashlib.sha256(issue['title'].encode('utf-8')).hexdigest(),
            'body_sha256': hashlib.sha256(issue['body'].encode('utf-8')).hexdigest(),
            'comment_id': selected['id'], 'author_id': actor['id'], 'updated_at': updated,
            'comment_sha256': hashlib.sha256(selected['body'].encode('utf-8')).hexdigest()}


def current_spec(issue, comments, trusted_actor_ids):
    """Read the latest authenticated planner spec, or the original issue body."""
    if not isinstance(issue, dict) or not isinstance(issue.get('body'), str) or not issue['body'].strip():
        raise ValueError('original issue body is missing')
    if (not isinstance(trusted_actor_ids, (list, tuple, set)) or not trusted_actor_ids
            or any(type(actor) is not int or actor < 1 for actor in trusted_actor_ids)
            or len(set(trusted_actor_ids)) != len(trusted_actor_ids)):
        raise ValueError('trusted planner identity inventory is invalid')
    if not isinstance(comments, list):
        raise ValueError('complete comment inventory is required')
    selected, previous = None, 0
    for comment in comments:
        if (not isinstance(comment, dict) or type(comment.get('id')) is not int
                or comment['id'] <= previous or not isinstance(comment.get('body'), str)):
            raise ValueError('comment inventory is malformed or not ordered')
        previous = comment['id']
        if comment['body'].startswith(SPEC_MARKER):
            selected = comment
    if selected is None:
        return issue['body']
    actor = selected.get('user')
    if not isinstance(actor, dict) or type(actor.get('id')) is not int or actor['id'] not in trusted_actor_ids:
        raise ValueError('latest planner specification has untrusted author')
    if not selected['body'][len(SPEC_MARKER):].strip():
        raise ValueError('latest planner specification is empty')
    return selected['body']


def verify_published_execution(repo, issue, spec, comments, trusted_actor_ids, test_plan, receipts):
    """Join a published mapping to a complete roster for one exact test plan."""
    if not isinstance(test_plan, dict) or test_plan.get('repo') != repo:
        raise ValueError('test plan repository differs from the issue')
    if current_spec(issue, comments, trusted_actor_ids) != spec:
        raise ValueError('planner specification is stale')
    selected = test_plan.get('selected')
    checks = test_plan.get('checks')
    if not isinstance(selected, list) or not isinstance(checks, dict) or set(selected) != set(checks):
        raise ValueError('test plan check inventory is invalid')
    test_ids = [name for name in selected if isinstance(checks[name], dict) and checks[name].get('kind') != 'policy']
    publication = published_mapping(repo, issue, spec, comments, trusted_actor_ids, test_ids)
    selected = next(comment for comment in comments if comment['id'] == publication['comment_id'])
    execution = verify_execution(spec, selected['body'][len(MARKER):].strip(), test_plan, receipts)
    if (execution['spec_sha256'] != publication['spec_sha256']
            or execution['acceptance_plan_sha256'] != publication['plan_sha256']):
        raise ValueError('mapping changed during execution verification')
    return {'schema': 'AW_PUBLISHED_ACCEPTANCE_EXECUTION_V1',
            'publication': publication, 'execution': execution,
            'qualification': 'not_evaluated'}


def revalidate_publication(previous, repo, issue, spec, comments, trusted_actor_ids, check_ids):
    """Require fresh issue and comment reads to match before any lifecycle write."""
    if not isinstance(previous, dict) or previous.get('schema') != 'AW_PUBLISHED_ACCEPTANCE_EXECUTION_V1':
        raise ValueError('prior published execution evidence is missing')
    if current_spec(issue, comments, trusted_actor_ids) != spec:
        raise ValueError('planner specification is stale')
    fresh = published_mapping(repo, issue, spec, comments, trusted_actor_ids, check_ids)
    if previous.get('publication') != fresh:
        raise ValueError('issue or mapping changed after execution verification')
    return fresh
