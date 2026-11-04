"""Prepare planner mappings against an authenticated default-branch policy.

This module performs no provider writes. The caller must obtain and recheck
the policy from the default branch and serialize any subsequent publication.
"""
import importlib.util
from pathlib import Path


def load(name):
    path = Path(__file__).resolve().parents[2] / 'issue-ci/scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location('aw_planner_' + name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def prepare(issue, comments, config, mapping_text):
    """Return a publication payload; do not post it or authorize adoption."""
    qualification = load('qualification')
    mapping = load('acceptance_plan')
    policy = qualification.validate(config)
    settings = config.get('planner_acceptance_mapping')
    if (not isinstance(settings, dict) or set(settings) != {'version', 'trusted_actor_ids'}
            or type(settings['version']) is not int or settings['version'] != 1):
        raise ValueError('reviewed planner acceptance mapping policy is not enabled')
    spec = mapping.current_spec(issue, comments, settings['trusted_actor_ids'])
    tests = sorted(name for name, check in policy['checks'].items() if check['kind'] != 'policy')
    comment = mapping.prepare_comment(spec, mapping_text, tests)
    verified = mapping.validate(spec, mapping_text, tests)
    return {'body': comment, 'spec_sha256': verified['spec_sha256'],
            'plan_sha256': verified['plan_sha256'], 'checks': verified['checks'],
            'qualification': 'not_evaluated'}


def issue_identity(repo, number, issue):
    if (not isinstance(issue, dict) or type(issue.get('number')) is not int or issue['number'] != number
            or type(number) is not int or number < 1
            or issue.get('state') != 'open' or 'pull_request' in issue
            or issue.get('url') != f'https://api.github.com/repos/{repo}/issues/{number}'
            or issue.get('repository_url') != f'https://api.github.com/repos/{repo}'
            or any(not isinstance(issue.get(field), str) or not issue[field].strip() for field in ('title', 'body'))):
        raise ValueError('current issue identity is invalid')
    labels = issue.get('labels')
    if (not isinstance(labels, list) or any(not isinstance(label, dict) or not isinstance(label.get('name'), str)
                                            for label in labels)):
        raise ValueError('issue labels are invalid')
    names = sorted(label['name'] for label in labels)
    if len(set(names)) != len(names) or set(name.lower() for name in names) & {'doing', 'done', 'blocked', 'agy:blocked'}:
        raise ValueError('issue has active, blocked or invalid lifecycle labels')
    return {key: issue.get(key) for key in ('number', 'state', 'title', 'body', 'url', 'repository_url', 'milestone')} | {'labels': names}


def publish(client, number, mapping_text, policy_source):
    """Publish only after fresh guarded reads; uncertain writes are never retried."""
    repo = client.repo
    first_policy = policy_source()
    if (not isinstance(first_policy, dict) or set(first_policy) != {'config', 'source_commit', 'path'}
            or first_policy['path'] != '.github/issue-ci-required-gates.json'):
        raise ValueError('reviewed default-branch policy metadata is missing')
    import re
    if not isinstance(first_policy['source_commit'], str) or not re.fullmatch('[0-9a-f]{40}', first_policy['source_commit']):
        raise ValueError('default-branch commit identity is invalid')
    issue = client.issue(number)
    identity = issue_identity(repo, number, issue)
    comments = client.comments(number)
    prepared = prepare(issue, comments, first_policy['config'], mapping_text)
    actor = client.command('api', 'user')
    permitted = first_policy['config']['planner_acceptance_mapping']['trusted_actor_ids']
    checks = sorted(name for name, check in first_policy['config']['qualification']['checks'].items()
                    if check['kind'] != 'policy')
    if not isinstance(actor, dict) or type(actor.get('id')) is not int or actor['id'] not in permitted:
        raise ValueError('authenticated publisher is not a trusted planner')
    mapping = load('acceptance_plan')
    def require_fresh():
        if policy_source() != first_policy or issue_identity(repo, number, client.issue(number)) != identity:
            raise ValueError('policy or issue changed before mapping publication')
        if client.comments(number) != comments:
            raise ValueError('issue comments changed before mapping publication')
        if policy_source() != first_policy or issue_identity(repo, number, client.issue(number)) != identity:
            raise ValueError('policy or issue changed during mapping recheck')
    current = [c for c in comments if c['body'].startswith(mapping.MARKER)]
    if current and current[-1]['body'] == prepared['body']:
        selected = mapping.published_mapping(repo, issue, mapping.current_spec(issue, comments, permitted), comments,
                                             permitted, checks)
        require_fresh()
        return {'status': 'existing', 'comment_id': selected['comment_id'], 'plan_sha256': selected['plan_sha256']}
    require_fresh()
    # Do not catch or retry this call: a lost response could mean it succeeded.
    created = client.command('api', '--method', 'POST', f'repos/{repo}/issues/{number}/comments',
                             payload={'body': prepared['body']})
    if not isinstance(created, dict) or type(created.get('id')) is not int or created['id'] < 1:
        raise ValueError('mapping publication response unknown; inspect before retry')
    fresh_issue = client.issue(number)
    fresh_comments = client.comments(number)
    if policy_source() != first_policy or issue_identity(repo, number, fresh_issue) != identity:
        raise ValueError('policy or issue changed after publication; mapping remains pending')
    selected = mapping.published_mapping(repo, fresh_issue, mapping.current_spec(fresh_issue, fresh_comments, permitted),
                                         fresh_comments, permitted, checks)
    if (selected['comment_id'] != created['id'] or selected['author_id'] != actor['id']
            or selected['plan_sha256'] != prepared['plan_sha256']):
        raise ValueError('mapping readback differs; inspect before retry')
    return {'status': 'published', 'comment_id': selected['comment_id'], 'plan_sha256': selected['plan_sha256']}
