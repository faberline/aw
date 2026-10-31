"""Milestone release decisions over verified snapshots. No I/O or mutations."""
import hashlib
import json
import re

VERSION = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
FAILURES = {'failure', 'timed_out', 'action_required', 'startup_failure'}


def require(ok, reason):
    if not ok:
        raise ValueError('release: ' + reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def goal_identity(milestone):
    return {k: milestone.get(k) for k in ('number', 'title', 'description')}


def repair_marker(repo, milestone, tag, head, evidence):
    attempts = sorted({(r.get('workflow'), r.get('run'), r.get('attempt'))
                       for r in evidence.get('failures', [])})
    return '<!-- aw-release-failure:' + digest([repo, milestone, tag, head, attempts]) + ' -->'


def contract(config, repo, milestone, checks):
    require(isinstance(config, dict) and set(config) == {'version', 'repo', 'milestones'}
            and type(config['version']) is int and config['version'] == 1 and config['repo'] == repo,
            'invalid reviewed release configuration')
    item = config['milestones'].get(str(milestone['number']))
    require(isinstance(item, dict) and set(item) == {'version', 'goal_sha256', 'acceptance', 'publication'},
            'existing milestone needs a reviewed release contract')
    require(isinstance(item['version'], str) and re.fullmatch(VERSION, item['version']), 'invalid Semantic Version')
    require(isinstance(milestone.get('description'), str) and milestone['description'].strip()
            and item['goal_sha256'] == digest(goal_identity(milestone)), 'milestone goal changed or is empty')
    require(isinstance(item['acceptance'], list) and bool(item['acceptance']), 'goal acceptance is missing')
    names = []
    for goal in item['acceptance']:
        require(isinstance(goal, dict) and set(goal) == {'goal', 'issues', 'checks'}
                and isinstance(goal['goal'], str) and goal['goal'].strip(), 'invalid goal acceptance')
        names.append(goal['goal'])
        require(isinstance(goal['issues'], list) and bool(goal['issues'])
                and all(type(n) is int and n > 0 for n in goal['issues'])
                and len(set(goal['issues'])) == len(goal['issues']), 'goal needs explicit issue evidence')
        require(isinstance(goal['checks'], list) and bool(goal['checks'])
                and all(isinstance(c, str) and c in checks for c in goal['checks'])
                and len(set(goal['checks'])) == len(goal['checks']), 'goal needs real full-plan checks')
    require(len(names) == len(set(names)), 'duplicate goal acceptance')
    require(item['publication'] == 'github-release', 'unsupported publication proof')
    return item


def covers_release(snapshot, head):
    """The frozen commit must still contain all currently approved goal work."""
    if head == snapshot['head']:
        return True  # Current-main delivery and contract were verified above.
    proof = snapshot.get('release_commits', {}).get(head, {})
    return (proof.get('landed') is True
            and proof.get('pulls') == sorted(p['number'] for p in snapshot['pulls'])
            and proof.get('contract_sha256') == digest(snapshot['contract']))


def evaluate(snapshot):
    """One next action. Provider adapter must authenticate and recheck inputs."""
    result = dict(schema='AW_RELEASE_ASSESSMENT_V1', repo=snapshot['repo'],
                  milestone=snapshot['milestone']['number'], head=snapshot['head'],
                  version=snapshot['contract']['version'], release_head=snapshot['head'],
                  state='hold', action=None, reasons=[])
    result['fingerprint'] = digest(snapshot)
    milestone, item = snapshot['milestone'], snapshot['contract']
    if milestone.get('state') == 'closed':
        open_view = dict(snapshot, milestone=dict(milestone, state='open'))
        verified = evaluate(open_view)
        if verified['state'] == 'ready_close':
            result.update(state='complete')
        else:
            result['reasons'] = ['milestone was closed without verified final release completion']
        return result
    require(milestone.get('state') == 'open', 'unknown milestone state')
    require(isinstance(snapshot['issues'], list) and bool(snapshot['issues']), 'milestone issue inventory is empty')
    numbers = [i['number'] for i in snapshot['issues']]
    require(len(numbers) == len(set(numbers)), 'duplicate milestone issue')
    require(all(i.get('state') in ('open', 'closed') for i in snapshot['issues']), 'unknown issue state')
    required = {n for goal in item['acceptance'] for n in goal['issues']}
    require(required <= set(numbers), 'goal evidence references an issue outside this milestone')
    open_issues = sorted(i['number'] for i in snapshot['issues'] if i['state'] != 'closed')
    unmerged = sorted(p['number'] for p in snapshot['pulls'] if not p.get('merged_at') or not p.get('_landed'))
    if open_issues or unmerged:
        result.update(state='work', reasons=[f'open issues: {open_issues}', f'unmerged or unlanded PRs: {unmerged}'])
        return result
    represented = {n for p in snapshot['pulls'] for n in p['_issues'] if p.get('_landed')}
    require(required <= represented, 'closed goal issue lacks a merged landed PR')
    version = item['version']
    tags = snapshot['tags']
    require(isinstance(tags, dict) and all(isinstance(k, str) and isinstance(v, str)
            and re.fullmatch('[0-9a-f]{40}', v) for k, v in tags.items()), 'invalid tag inventory')
    final = 'v' + version
    candidates = sorted((int(match.group(1)), name, sha) for name, sha in tags.items()
                        if (match := re.fullmatch(re.escape(final) + r'-rc\.([1-9][0-9]*)', name)))
    rc = candidates[-1] if candidates else None
    if final in tags:
        require(rc is not None and tags[final] == rc[2], 'formal tag is not the latest RC commit')
        require(covers_release(snapshot, rc[2]), 'formal commit lacks current landed goal work or release contract')
        require(snapshot['evidence'].get(rc[1], {}).get('status') == 'success', 'formal tag lacks passing RC evidence')
        active = final
    else:
        active = rc[1] if rc else None
    if active is None:
        result.update(state='ready_rc', action='tag', tag=final + '-rc.1')
        return result
    result['release_head'] = tags[active]
    evidence = snapshot['evidence'].get(active, {'status': 'pending', 'reasons': ['full test run is missing']})
    require(evidence.get('status') in ('pending', 'failure', 'success'), 'unknown full-test verdict')
    if evidence['status'] == 'pending':
        result.update(state='testing', tag=active, reasons=evidence.get('reasons', []))
    elif evidence['status'] == 'failure':
        marker = repair_marker(snapshot['repo'], milestone['number'], active, tags[active], evidence)
        repairs = [i for i in snapshot['issues'] if (i.get('body') or '').startswith(marker)]
        repaired = bool(repairs) and all(i['state'] == 'closed' and i['number'] in represented for i in repairs)
        if active != final and rc[2] != snapshot['head'] and repaired:
            result.update(state='ready_rc', action='tag', tag=final + f'-rc.{rc[0] + 1}',
                          release_head=snapshot['head'])
        else:
            result.update(state='repair', action='failure_issue', tag=active,
                          reasons=evidence.get('reasons', []), failures=evidence.get('failures', []))
            if active == final:
                result['reasons'] = result['reasons'] + ['Keep the formal tag. A code fix needs a new reviewed SemVer version.']
    elif active != final:
        if not covers_release(snapshot, rc[2]):
            result.update(state='ready_rc', action='tag', tag=final + f'-rc.{rc[0] + 1}',
                          release_head=snapshot['head'])
        else:
            result.update(state='ready_final', action='tag', tag=final)
    elif not snapshot.get('publication'):
        result.update(state='ready_publish', action='publish', tag=final)
    else:
        publication = snapshot['publication']
        require(publication.get('tag_name') == final and publication.get('draft') is False
                and publication.get('prerelease') is False and bool(publication.get('published_at')),
                'publication is not a final published release')
        result.update(state='ready_close', action='close', tag=final)
    return result
