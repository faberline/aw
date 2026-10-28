"""Planner transport for hosted tool adapters; no gh CLI, Git, or checkout.

The caller supplies request(method, repository-relative REST path, payload).
GET returns canonical REST JSON. The tool adapter must complete pagination one
page at a time and preserve errors. POST issue creation must return the actual
number/html_url. An uncertain tool response raises; it must never be replayed.
"""
import importlib.util
from pathlib import Path
import re

spec = importlib.util.spec_from_file_location('connector_plan', Path(__file__).with_name('plan.py'))
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
queue = plan.queue
lease_spec = importlib.util.spec_from_file_location('planner_github_leases', Path(__file__).resolve().parents[2] / 'issue-loop/scripts/github_leases.py')
leases = importlib.util.module_from_spec(lease_spec)
lease_spec.loader.exec_module(leases)


class Client(queue.Client):
    def __init__(self, repo, request):
        self.request = request
        super().__init__(repo=repo)

    def command(self, *args, payload=None):
        if len(args) == 2 and args[0] == 'api' and payload is None:
            method, path = 'GET', args[1]
        elif len(args) == 4 and args[:2] == ('api', '--method'):
            method, path = args[2:]
        else:
            raise ValueError('connector supports canonical REST operations only')
        if not isinstance(path, str) or not path.startswith('repos/') or '..' in path or '://' in path:
            raise ValueError('invalid connector API path')
        if method != 'GET':
            issue = re.escape(f'repos/{self.repo}/issues')
            permitted = (
                method == 'PATCH' and re.fullmatch(issue + r'/[1-9][0-9]*', path)
                and isinstance(payload, dict) and bool(payload)
                and set(payload) <= {'labels', 'milestone'}
            ) or (
                method == 'POST' and re.fullmatch(issue + r'/[1-9][0-9]*/comments', path)
                and isinstance(payload, dict) and set(payload) == {'body'}
            ) or (
                method == 'POST' and path == f'repos/{self.repo}/issues'
                and isinstance(payload, dict)
                and set(payload) == {'title', 'body', 'milestone', 'labels'}
            )
            if not permitted:
                raise ValueError('connector mutation outside planner issue scope')
            # A transport can refuse known unsupported requests before a write
            # is marked attempted. This hook must be pure and perform no I/O.
            prevalidate = getattr(self.request, 'prevalidate', None)
            if prevalidate is not None:
                prevalidate(method, path, payload)
            queue.coordination.verify_for(self.repo, required=True)
            if path != f'repos/{self.repo}/issues':
                number = int(path.split('/issues/', 1)[1].split('/')[0])
                leases.Ledger(self.repo, queue.coordination, self.request).require_unleased(number)
                target = self.issue(number)
                if (not isinstance(target, dict) or type(target.get('number')) is not int
                        or target['number'] != number
                        or target.get('html_url') != f'https://github.com/{self.repo}/issues/{number}'
                        or target.get('state') != 'open' or 'pull_request' in target):
                    raise ValueError('native planner target is not the expected open issue')
                if not isinstance(target.get('labels'), list) or queue.labels(target) & {'doing', 'done'}:
                    raise ValueError('native planner target is active or awaiting review')
            # Only a transport that owns this contract may mark at its own
            # final dispatch boundary. Other callbacks remain conservative.
            if getattr(self.request, 'marks_mutations', False) is not True:
                queue.coordination.mark_mutation_for(self.repo, required=True)
        response = self.request(method, path, payload)
        if (not isinstance(response, (dict, list))
                or method != 'GET' and not isinstance(response, dict)):
            raise ValueError('connector did not return canonical REST JSON')
        if method == 'POST' and path == f'repos/{self.repo}/issues':
            number = response.get('number') if isinstance(response, dict) else None
            if (type(number) is not int or number < 1
                    or response.get('html_url') != f'https://github.com/{self.repo}/issues/{number}'):
                raise ValueError('issue creation outcome needs reconciliation; no retry')
        elif method == 'PATCH':
            number = int(path.rsplit('/', 1)[1])
            valid = (type(response.get('number')) is int and response['number'] == number
                     and response.get('html_url') == f'https://github.com/{self.repo}/issues/{number}'
                     and response.get('state') == 'open' and 'pull_request' not in response)
            if 'labels' in payload:
                labels = response.get('labels')
                valid = valid and isinstance(labels, list) and all(
                    isinstance(v, str) or isinstance(v, dict) and isinstance(v.get('name'), str)
                    for v in labels)
                if valid:
                    valid = queue.labels(response) == set(payload['labels'])
            if 'milestone' in payload:
                milestone = response.get('milestone')
                valid = (valid and isinstance(milestone, dict)
                         and type(milestone.get('number')) is int
                         and milestone['number'] == payload['milestone'])
            if not valid:
                raise ValueError('issue update outcome needs reconciliation; no retry')
        elif method == 'POST':
            number = int(path.split('/issues/', 1)[1].split('/')[0])
            comment_id = response.get('id')
            if (type(comment_id) is not int or comment_id < 1
                    or response.get('body') != payload['body']
                    or response.get('issue_url') != f'https://api.github.com/repos/{self.repo}/issues/{number}'
                    or response.get('html_url') != f'https://github.com/{self.repo}/issues/{number}#issuecomment-{comment_id}'):
                raise ValueError('comment creation outcome needs reconciliation; no retry')
        return response


def run(repo, args, request, owner=None):
    """Run the unchanged planner policy through native connected GitHub tools."""
    client = Client(repo, request)
    if list(args) == ['status']:
        return plan.execute(client, ['status'])
    with queue.coordination.shared_guard(repo, required=True, mode='github',
                                         owner=owner, request=request):
        return plan.execute(client, list(args))
