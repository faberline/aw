"""Do not use a previous PR head's CI handoff as a current retry."""
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

GITHUB_ACTIONS_APP_ID = 15368


def attestation_dependencies():
    readiness_spec=importlib.util.spec_from_file_location('drift_readiness',Path(__file__).resolve().parents[2]/'issue-ci/scripts/readiness.py')
    readiness=importlib.util.module_from_spec(readiness_spec);readiness_spec.loader.exec_module(readiness)
    development_spec=importlib.util.spec_from_file_location('drift_development',Path(__file__).resolve().parents[2]/'issue-ci/scripts/development_pipeline.py')
    development=importlib.util.module_from_spec(development_spec);development_spec.loader.exec_module(development)
    return readiness,development


# Each revision's report framing and final per-PR semantics need independent
# review before admission here. Unknown versions hold; this is not a CI gate.
REVIEWED_WRITERS={'4223a4534f0197e1ea23004b9388ffd1eea34639'}
MARKER='AW_DEVELOPMENT_FAILURE_REPORT_V1 '


def reporter_source(client,repo,main,config):
    readiness,development=attestation_dependencies()
    spec=config.get('development_reporter')
    readiness.need(config.get('completion_owner')=='local' and isinstance(spec,dict)
                   and set(spec)=={'path','id','definition_sha','job','link_job'}
                   and type(spec.get('id')) is int and spec['id']>0
                   and all(isinstance(spec.get(k),str) for k in ('path','definition_sha','job','link_job'))
                   and re.fullmatch(r'\.github/workflows/[\w.-]+\.ya?ml',spec['path'])
                   and re.fullmatch('[0-9a-f]{40}',spec['definition_sha']),
                   'drift_writer_policy','reviewed reporter identity required')
    raw=readiness.source_bytes(SimpleNamespace(api=client.api),repo,spec['path'],main,spec['definition_sha'])
    workflow=development.yaml_document(raw);jobs=workflow.get('jobs',{})
    job=jobs.get(spec['job'],{});links=jobs.get(spec['link_job'],{})
    steps=job.get('steps')
    readiness.need(isinstance(steps,list) and len(steps)==1 and isinstance(steps[0],dict),
                   'drift_writer_source','one fixed action required')
    use=steps[0].get('uses','');match=re.fullmatch(r'faberline/aw/\.github/actions/issue-ci@([0-9a-f]{40})',use)
    readiness.need(match is not None and match[1] in REVIEWED_WRITERS,'drift_writer_source','reviewed writer SHA required')
    expected={'uses':use,'with':{'operation':'development-report'}}
    readiness.need(set(workflow)=={'name','on','permissions','concurrency','jobs'} and workflow['permissions']=={}
                   and workflow['concurrency']=={'group':'issue-ci-handoff','cancel-in-progress':'false'}
                   and set(jobs)=={spec['job'],spec['link_job']}
                   and set(job)=={'runs-on','timeout-minutes','permissions','steps','needs','if'}
                   and job.get('runs-on')=='ubuntu-latest' and job.get('needs')==spec['link_job']
                   and job.get('if')=='${{ always() }}' and job.get('steps')==[expected]
                   and job.get('permissions')=={'actions':'read','contents':'read','pull-requests':'read','issues':'write'}
                   and set(links)=={'runs-on','timeout-minutes','permissions','steps'}
                   and links.get('runs-on')=='ubuntu-latest' and links.get('steps')==[expected]
                   and links.get('permissions')=={'actions':'read','contents':'read','pull-requests':'write','issues':'read'},
                   'drift_writer_source','fixed default-branch writer jobs required')
    return spec


def complete_runs(client,path):
    rows=[];total=None
    for page in range(1,1001):
        value=client.api(f'{path}?per_page=100&page={page}')
        if not isinstance(value,dict) or type(value.get('total_count')) is not int or not isinstance(value.get('workflow_runs'),list):
            raise RuntimeError('invalid writer run inventory')
        if total is None:total=value['total_count']
        if value['total_count']!=total:raise RuntimeError('writer run inventory moved')
        chunk=value['workflow_runs'];rows.extend(chunk)
        if len(chunk)<100:break
    else:raise RuntimeError('writer run inventory incomplete')
    if len(rows)!=total or any(not isinstance(row,dict) or type(row.get('id')) is not int for row in rows) \
            or len({row['id'] for row in rows})!=len(rows):
        raise RuntimeError('writer run inventory incomplete or duplicate')
    return rows


def timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z',value):
        raise ValueError('valid UTC timestamp required')
    seconds=value[:19];fraction=value[20:-1] if '.' in value else ''
    return datetime.strptime(seconds,'%Y-%m-%dT%H:%M:%S').replace(
        tzinfo=timezone.utc,microsecond=int((fraction+'000000')[:6]))


def proof_in_log(readiness,log,repo,main,run_id,pr,head,reason,cutoff,started,completed,conclusion):
    reports=readiness.records(log,MARKER)
    if len(reports)!=1:return False
    report=reports[0]
    if (not isinstance(report,dict) or set(report)!={'repo','source','run','role','results'}
            or (report['repo'],report['source'],report['run'],report['role'])!=(repo,main,run_id,'report')
            or not isinstance(report['results'],list)):
        return False
    if (any(not isinstance(row,dict) or type(row.get('pr')) is not int or row['pr']<=0
            or not isinstance(row.get('head'),str) or not re.fullmatch('[0-9a-f]{40}',row['head'])
            or row.get('state') not in ('processed','held') for row in report['results'])
            or len({row['pr'] for row in report['results']})!=len(report['results'])):
        return False
    if conclusion=='failure' and not any(row['state']=='held' for row in report['results']):
        return False
    rows=[row for row in report['results'] if isinstance(row,dict) and row.get('head')==head]
    if len(rows)!=1 or rows[0]!={'head':head,'pr':pr,'state':'processed'}:return False
    states=[];terminal=[]
    for index,line in enumerate(log.splitlines()):
        match=re.match(r'^(\d{4}-\d\d-\d\dT\S+)\s+(.*)$',line)
        if not match:continue
        when=timestamp(match[1]);line=match[2]
        if line.startswith(MARKER):terminal.append((index,when))
        if not line.startswith('{'):continue
        try:value=readiness.strict_json_loads(line)
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and value.get('head')==head and 'state' in value and 'reasons' in value:
            states.append((index,when,value))
    return (len(terminal)==1 and len(states)==1 and timestamp(started)<=states[0][1]
            # Provider event timestamps have only second precision. The
            # entire cutoff second must end before this observation.
            and states[0][1]>=timestamp(cutoff)+timedelta(seconds=1)
            and states[0][0]<terminal[0][0] and states[0][1]<=terminal[0][1]<timestamp(completed)+timedelta(seconds=1)
            and set(states[0][2])=={'state','head','evidence','reasons'}
            and states[0][2].get('state')=='todo' and isinstance(states[0][2].get('reasons'),list)
            and reason in states[0][2]['reasons'])


def attest_writer(client,repo,main,config,pr,head,reason,cutoff):
    readiness,_=attestation_dependencies();spec=reporter_source(client,repo,main,config)
    runs=complete_runs(client,f'repos/{repo}/actions/workflows/{spec["id"]}/runs')
    for run in sorted(runs,key=lambda row:row['id'],reverse=True):
        if (run.get('workflow_id')!=spec['id'] or run.get('path')!=spec['path']
                or run.get('head_sha')!=main or run.get('head_branch')!='main'
                or (run.get('head_repository') or {}).get('full_name')!=repo
                or run.get('event') not in ('workflow_run','push','issues','repository_dispatch')
                or run.get('status')!='completed' or run.get('conclusion') not in ('success','failure')
                or type(run.get('run_attempt')) is not int or run['run_attempt']<1):
            continue
        jobs=client.command('api',f'repos/{repo}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100')
        if not isinstance(jobs,dict) or not isinstance(jobs.get('jobs'),list) or type(jobs.get('total_count')) is not int:
            raise RuntimeError('invalid writer jobs')
        if jobs['total_count']!=len(jobs['jobs']):raise RuntimeError('incomplete writer jobs')
        matches=[job for job in jobs['jobs'] if isinstance(job,dict) and job.get('name')==spec['job']]
        if len(matches)!=1:continue
        job=matches[0]
        if (type(job.get('id')) is not int or job['id']<1 or job.get('run_id')!=run['id']
                or job.get('run_attempt')!=run['run_attempt'] or job.get('head_sha')!=main or job.get('status')!='completed'
                or job.get('conclusion') not in ('success','failure')):continue
        try:
            log=readiness.job_log(repo,job['id'])
            valid=proof_in_log(readiness,log,repo,main,run['id'],pr,head,reason,cutoff,
                               job.get('started_at'),job.get('completed_at'),job.get('conclusion'))
        except (RuntimeError,ValueError,OSError,subprocess.CalledProcessError):continue
        if valid:
            return {'run':run['id'],'attempt':run['run_attempt'],'job':job['id']}
    return False


def trusted_policy(client, base):
    meta = client.api(f'repos/{client.repo}/contents/.github/issue-ci-required-gates.json?ref={base}')
    try:
        if meta.get('type') != 'file' or meta.get('encoding') != 'base64':
            return None
        config = json.loads(base64.b64decode(''.join(meta['content'].split()), validate=True))
        return config['qualification']['trusted_workflows']
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def trusted_workflow(client, identity, path, base, head):
    if (not isinstance(identity, dict) or type(identity.get('id')) is not int
            or not isinstance(identity.get('definition_sha'), str)
            or not re.fullmatch('[0-9a-f]{40}', identity['definition_sha'])
            or not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', path)):
        return False
    return all(client.api(f'repos/{client.repo}/contents/{path}?ref={ref}').get('sha') == identity['definition_sha']
               for ref in (base, head))


def active_required(client, pull, checks):
    head, base = pull['head']['sha'], pull['base']['sha']
    trusted = trusted_policy(client, base)
    if not isinstance(trusted, dict):
        return False
    for check in checks:
        if check.get('status') not in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
            continue
        if (check.get('app') or {}).get('id') != GITHUB_ACTIONS_APP_ID:
            continue
        job = client.api(f"repos/{client.repo}/actions/jobs/{check['id']}")
        run_id = job.get('run_id')
        if type(run_id) is not int or run_id <= 0:
            continue
        run = client.api(f'repos/{client.repo}/actions/runs/{run_id}')
        path = run.get('path')
        identity = trusted.get(path)
        if (not isinstance(path, str) or not trusted_workflow(client, identity, path, base, head)):
            continue
        associations = run.get('pull_requests')
        if (run.get('id') == run_id and run.get('workflow_id') == identity['id']
                and run.get('head_sha') == head and run.get('event') == 'pull_request'
                and run.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested')
                and type(run.get('run_attempt')) is int and job.get('run_attempt') == run['run_attempt']
                and job.get('id') == check['id'] and job.get('head_sha') == head
                and isinstance(associations, list) and any(isinstance(pr, dict)
                    and pr.get('number') == pull['number'] and pr.get('head', {}).get('sha') == head
                    and pr.get('base', {}).get('sha') == base for pr in associations)):
            return True
    return False


def complete_checks(client, head, *, allow_empty=False):
    checks = []
    expected = None
    for page in range(1, 1001):
        payload = client.api(f'repos/{client.repo}/commits/{head}/check-runs?per_page=100&page={page}')
        if (not isinstance(payload, dict) or type(payload.get('total_count')) is not int
                or not isinstance(payload.get('check_runs'), list)):
            return None
        if expected is None:
            expected = payload['total_count']
        if payload['total_count'] != expected:
            return None
        batch = payload['check_runs']; checks.extend(batch)
        if len(batch) < 100:
            break
    else:
        return None
    if (not checks and not allow_empty) or len(checks) != expected:
        return None
    for check in checks:
        if (not isinstance(check, dict) or type(check.get('id')) is not int
                or check.get('head_sha') != head or not isinstance(check.get('name'), str)
                or type((check.get('app') or {}).get('id')) is not int):
            return None
    if len({check['id'] for check in checks}) != len(checks):
        return None
    return checks


def completed_workflows(client, pr_number, head, base, *, allow_prior_base=False):
    trusted = trusted_policy(client, base)
    if not isinstance(trusted, dict) or not trusted:
        return False
    completed = {}
    for path, identity in trusted.items():
        # Repair eligibility uses the current trusted workflow identity only
        # to find active runs. A PR may itself need to repair that workflow.
        if not trusted_workflow(client, identity, path, base, base if allow_prior_base else head):
            return False
        runs, total = [], None
        for page in range(1, 1001):
            payload = client.api(f"repos/{client.repo}/actions/workflows/{identity['id']}/runs?event=pull_request&head_sha={head}&per_page=100&page={page}")
            if (not isinstance(payload, dict) or type(payload.get('total_count')) is not int
                    or not isinstance(payload.get('workflow_runs'), list)):
                return False
            if total is None:
                total = payload['total_count']
            if payload['total_count'] != total:
                return False
            batch = payload['workflow_runs']; runs.extend(batch)
            if len(batch) < 100:
                break
        else:
            return False
        if len(runs) != total or (not runs and not allow_prior_base):
            return False
        if not runs:
            completed[path]=None
            continue
        for run in runs:
            if (not isinstance(run, dict) or type(run.get('id')) is not int
                    or type(run.get('run_number')) is not int or type(run.get('run_attempt')) is not int):
                return False
        if len({run['id'] for run in runs}) != len(runs):
            return False
        # An older run can be manually re-run while a newer run is already
        # complete. It may not have emitted a check-run yet.
        if allow_prior_base and any(run.get('status')!='completed' for run in runs):
            return False
        latest = max(runs, key=lambda run: (run['run_number'], run['run_attempt'], run['id']))
        if (latest.get('workflow_id') != identity['id'] or latest.get('path') != path
                or latest.get('head_sha') != head or latest.get('event') != 'pull_request'
                or not isinstance(latest.get('pull_requests'), list) or not any(isinstance(pr, dict)
                    and pr.get('number') == pr_number and pr.get('head', {}).get('sha') == head
                    and (pr.get('base', {}).get('sha') == base or allow_prior_base
                         and isinstance(pr.get('base', {}).get('sha'),str)
                         and re.fullmatch('[0-9a-f]{40}',pr['base']['sha'])) for pr in latest['pull_requests'])
                or latest.get('status') != 'completed' or not isinstance(latest.get('conclusion'), str)):
            return False
        completed[path] = {'run': latest['id'], 'attempt': latest['run_attempt']}
    return completed


def manual_retry_ready(client, issue_number, pr_number, head, base, comments, journals):
    events = client.pages(f'repos/{client.repo}/issues/{issue_number}/events')
    if not isinstance(events, list):
        return False
    changes = [event for event in events if isinstance(event, dict)
               and event.get('event') in ('labeled', 'unlabeled')
               and (event.get('label') or {}).get('name') == 'todo']
    if not changes or any(type(event.get('id')) is not int or not isinstance(event.get('created_at'), str)
                          for event in changes):
        return False
    last = max(changes, key=lambda event: (event['created_at'], event['id']))
    actor = last.get('actor') or {}
    if (last['event'] != 'labeled' or actor.get('type') != 'User' or not isinstance(actor.get('login'), str)
            or (journals and last['created_at'] <= max(journals)[0])):
        return False
    checks = complete_checks(client, head)
    workflows = completed_workflows(client, pr_number, head, base)
    if (checks is None or not all(check.get('status') == 'completed' for check in checks) or not workflows):
        return False
    return {'kind': 'request', 'event': last['id'], 'actor': actor['login'], 'workflows': workflows}


def main_drift_retry(client,issue_number,pr_number,pull,comments,journals):
    """Prove a CI-owned todo is still caused by actual main drift/conflict."""
    head=pull['head']['sha'];base=pull['base']['sha'];root=f'repos/{client.repo}'
    if not journals or max(journals)[2]!=head:
        return False
    journal=max(journals);body=journal[3]
    behind='\nPR is behind current main\n' in body
    conflict='\nPR has merge conflicts\n' in body
    if not behind and not conflict:
        return False
    main=client.api(f'{root}/commits/main').get('sha')
    if not isinstance(main,str) or not re.fullmatch('[0-9a-f]{40}',main):
        return False
    policy=client.api(f'{root}/contents/.github/issue-ci-required-gates.json?ref={main}')
    try:
        raw=base64.b64decode(''.join(policy['content'].split()),validate=True)
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        config=json.loads(raw)
        digest=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        if (policy.get('type')!='file' or policy.get('encoding')!='base64' or len(raw)>262144
                or policy.get('sha')!=blob or f'\nPolicy: {digest}\n' not in body):
            return False
    except (AttributeError,KeyError,TypeError,ValueError):
        return False
    issue=client.api(f'{root}/issues/{issue_number}')
    labels=issue.get('labels')
    if (issue.get('number')!=issue_number or issue.get('state')!='open' or not isinstance(labels,list)
            or any(not isinstance(row,dict) or not isinstance(row.get('name'),str) for row in labels)
            or {row['name'] for row in labels}&{'todo','doing','done','blocked'}!={'todo'}):
        return False
    events=client.pages(f'{root}/issues/{issue_number}/events')
    if not isinstance(events,list):return False
    relevant=[event for event in events if isinstance(event,dict) and event.get('event')=='labeled'
              and (event.get('label') or {}).get('name') in ('todo','doing','done','blocked')]
    if not relevant or any(type(row.get('id')) is not int or not isinstance(row.get('created_at'),str) for row in relevant):
        return False
    latest=max(relevant,key=lambda row:(row['created_at'],row['id']))
    if ((latest.get('label') or {}).get('name')!='todo'
            or (latest.get('actor') or {}).get('login')!='github-actions[bot]'
            or latest['created_at']>journal[0]):
        return False
    # Both revisions are validated full SHAs; Client.api rejects any '..'
    # because other paths are not allowed to contain compare syntax.
    comparison=client.command('api',f'{root}/compare/{main}...{head}?per_page=1')
    if (not isinstance(comparison,dict) or (comparison.get('base_commit') or {}).get('sha')!=main
            or type(comparison.get('behind_by')) is not int or comparison['behind_by']<0):
        return False
    if type(pull.get('mergeable')) is not bool:
        return False
    reason=('behind' if behind and comparison['behind_by']>0 else
            'conflict' if conflict and base==main and pull.get('mergeable') is False
                          and pull.get('mergeable_state')=='dirty' else None)
    if reason is None:return False
    expected_reason='PR is behind current main' if reason=='behind' else 'PR has merge conflicts'
    times=[journal[0],latest['created_at'],issue.get('updated_at'),pull.get('updated_at')]
    if any(not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',value) for value in times):
        return False
    cutoff=max(times)
    try:
        proof=attest_writer(client,client.repo,main,config,pr_number,head,expected_reason,cutoff)
    except (RuntimeError,ValueError,KeyError,TypeError,OSError,subprocess.CalledProcessError):
        return False
    if not proof:return False
    checks=complete_checks(client,head,allow_empty=True)
    # This proves no required run is still active. A conflict can prevent a
    # new run against main entirely, so the last terminal run may use an old
    # base. Its outcome never proves the current PR passed CI.
    workflows=completed_workflows(client,pr_number,head,main,allow_prior_base=True)
    if checks is None or not all(row.get('status')=='completed' for row in checks) or not workflows:
        return False
    missing=[value is None for value in workflows.values()]
    if any(missing) and (not all(missing) or bool(checks)):
        return False
    fresh=client.api(f'{root}/pulls/{pr_number}')
    fresh_issue=client.api(f'{root}/issues/{issue_number}')
    if (fresh!=pull or fresh_issue!=issue or client.api(f'{root}/commits/main').get('sha')!=main):
        return False
    return {'kind':'main-drift','reason':reason,'main':main,'journal':journal[1],'writer':proof,
            'todo_event':latest['id'],'workflows':workflows}


def retry_ready(client, issue_number, pr_number):
    pull = client.api(f'repos/{client.repo}/pulls/{pr_number}')
    head = pull.get('head', {}).get('sha')
    base = pull.get('base', {}).get('sha')
    if (pull.get('number') != pr_number or pull.get('state') != 'open'
            or not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head)
            or not isinstance(base, str) or not re.fullmatch('[0-9a-f]{40}', base)
            or pull.get('base', {}).get('ref') != 'main'
            or pull.get('head', {}).get('ref') not in (f'issue/{issue_number}', f'agy/issue-{issue_number}')
            or not re.search(rf'(?m)^Closes #{issue_number}[ \t\r]*$', pull.get('body') or '')
            or (pull.get('head', {}).get('repo') or {}).get('full_name') != client.repo
            or (pull.get('base', {}).get('repo') or {}).get('full_name') != client.repo):
        return False
    body = pull.get('body') or ''
    if not isinstance(body, str) or re.findall(r'(?m)^Closes #([1-9][0-9]*)[ \t\r]*$', body) != [str(issue_number)]:
        return False
    rest = re.sub(r'(?m)^Closes #[1-9][0-9]*[ \t\r]*$', '', body)
    if re.search(r'(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[ \t\r\n]*:?[ \t\r\n]*(?:#[0-9]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+|https?://)', rest):
        return False
    comments = client.comments(issue_number)
    if not isinstance(comments, list):
        return False
    pattern = re.compile(r'\A<!-- issue-ci:[0-9a-f]{64}:(pending|ready|applied) -->\s*\nCI: todo for PR #([1-9][0-9]*) head `([0-9a-f]{40})`\.')
    journals = []
    for comment in comments:
        if not isinstance(comment, dict) or (comment.get('user') or {}).get('login') != 'github-actions[bot]':
            continue
        body = comment.get('body')
        match = pattern.match(body) if isinstance(body, str) else None
        if (isinstance(body, str) and 'issue-ci:' in body
                and re.search(rf'\bCI: todo for PR #{pr_number}\b', body) and match is None):
            return False
        if match and int(match[2]) == pr_number:
            if match[1] != 'applied':
                if match[3] == head:
                    return False
                continue
            if type(comment.get('id')) is not int or not isinstance(comment.get('updated_at'), str):
                return False
            journals.append((comment['updated_at'], comment['id'], match[3], body))
    request = manual_retry_ready(client, issue_number, pr_number, head, base, comments, journals)
    if request:
        return request
    drift=main_drift_retry(client,issue_number,pr_number,pull,comments,journals)
    if drift:
        return drift
    if not journals or max(journals)[2] != head:
        return False
    journal = max(journals)[3]
    run_links = set(re.findall(r'https://github\.com/' + re.escape(client.repo) + r'/actions/runs/([1-9][0-9]*)', journal))
    if not run_links:
        return False
    # Bind the journal to a current failure reported by the trusted writer,
    # with exact PR/head/base and run attempt. A generic check failure alone
    # cannot make an old todo actionable.
    report = re.compile(rf'\A<!-- aw-job-failure:[0-9a-f]{{64}} -->\s*\nCI job failed for PR #{pr_number}\..*?'
                        rf'\n- Head: `{head}`; base: `{base}`\..*?'
                        r'\n- Workflow: ([^;]+); run ([1-9][0-9]*), attempt ([1-9][0-9]*), job ([1-9][0-9]*)\.', re.S)
    reports = [report.match(c.get('body', '')) for c in comments
               if isinstance(c, dict) and (c.get('user') or {}).get('login') == 'github-actions[bot]'
               and isinstance(c.get('body'), str)]
    reports = [match for match in reports if match]
    if not reports:
        return False
    trusted = trusted_policy(client, base)
    if not isinstance(trusted, dict):
        return False
    failure = {'failure', 'timed_out', 'startup_failure', 'action_required'}
    valid_jobs = {}
    for match in reports:
        path, run_id, attempt, job_id = match.groups()
        if run_id not in run_links:
            continue
        identity = trusted.get(path)
        if not trusted_workflow(client, identity, path, base, head):
            continue
        run = client.api(f'repos/{client.repo}/actions/runs/{run_id}')
        job = client.api(f'repos/{client.repo}/actions/jobs/{job_id}')
        if (run.get('id') == int(run_id) and run.get('workflow_id') == identity['id']
                and run.get('path') == path and run.get('head_sha') == head
                and run.get('event') == 'pull_request' and run.get('run_attempt') == int(attempt)
                and type(run.get('run_number')) is int and run['run_number'] > 0
                and isinstance(run.get('pull_requests'), list) and any(isinstance(pr, dict)
                    and pr.get('number') == pr_number and pr.get('head', {}).get('sha') == head
                    and pr.get('base', {}).get('sha') == base for pr in run['pull_requests'])
                and run.get('status') == 'completed' and run.get('conclusion') in failure
                and job.get('id') == int(job_id) and job.get('run_id') == int(run_id)
                and job.get('run_attempt') == int(attempt) and job.get('head_sha') == head
                and isinstance(job.get('name'), str)
                and job.get('status') == 'completed' and job.get('conclusion') in failure):
            valid_jobs[int(job_id)] = (identity['id'], job['name'], run['run_number'], int(attempt), int(run_id), path)
    if not valid_jobs:
        return False
    workflows = completed_workflows(client, pr_number, head, base)
    if not workflows:
        return False
    checks = complete_checks(client, head)
    if checks is None:
        return False
    for check in checks:
        if check.get('status') != 'completed':
            return False
    for job_id, (workflow_id, name, run_number, attempt, run_id, path) in sorted(valid_jobs.items()):
        if workflows.get(path) != {'run': run_id, 'attempt': attempt}:
            continue
        candidates = []
        for check in checks:
            if check['name'] != name or (check.get('app') or {}).get('id') != GITHUB_ACTIONS_APP_ID:
                continue
            job = client.api(f"repos/{client.repo}/actions/jobs/{check['id']}")
            if (job.get('id') != check['id'] or type(job.get('run_id')) is not int
                    or job.get('head_sha') != head or job.get('name') != name):
                return False
            run = client.api(f"repos/{client.repo}/actions/runs/{job['run_id']}")
            if run.get('workflow_id') != workflow_id:
                continue
            if (run.get('head_sha') != head or type(run.get('run_number')) is not int
                    or type(run.get('run_attempt')) is not int or job.get('run_attempt') != run['run_attempt']):
                return False
            candidates.append((run['run_number'], run['run_attempt'], check['id']))
        if (candidates and max(candidates) == (run_number, attempt, job_id)
                and any(check['id'] == job_id and check.get('conclusion') in failure for check in checks)):
            return {'kind': 'ci_failure', 'workflow': path, 'run': run_id, 'attempt': attempt,
                    'job': job_id, 'name': name, 'journal': max(journals)[1], 'workflows': workflows}
    return False
