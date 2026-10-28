"""Fail-closed binding from planner requests to connected GitHub MCP tools.

The host must provide a synchronous call_tool(name, args), and the *same*
coordination module used by connector.Client. No runtime bridge is provided.
"""
import base64
import json
import re
import uuid
from urllib.parse import parse_qsl, urlsplit


class Refused(RuntimeError):
    pass


class ProviderError(Refused):
    def __init__(self, envelope):
        super().__init__('native provider error; preserve outcome and do not retry')
        self.envelope = envelope


class Unsupported(Refused):
    pass


def _envelope(value):
    if not isinstance(value, dict):
        raise Refused('invalid MCP envelope; outcome unknown')
    if value.get('isError') is True:
        raise ProviderError(value)
    if value.get('isError') not in (None, False):
        raise Refused('invalid MCP error flag')
    data = value.get('structuredContent')
    if not isinstance(data, dict):
        raise Refused('missing MCP structuredContent; outcome unknown')
    if data.get('isError') is True or 'error' in data:
        raise ProviderError(value)
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def _string_list(v):
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


class NativeTransport:
    def __init__(self, repo, call_tool, coordination):
        if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise Refused('invalid repository')
        self.repo, self.call_tool, self.coordination = repo, call_tool, coordination
        self.prefix = f'repos/{repo}/'
        self.guard_read = None
        self.issued_held = None

    def _call(self, name, args):
        # No write is retried, even if callback raises or the result is malformed.
        return _envelope(self.call_tool('mcp__codex_apps__github_' + name, args))

    def _get(self, path):
        parts = urlsplit(path)
        if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith(self.prefix):
            raise Refused('request outside repository')
        sub = parts.path[len(self.prefix):]
        if re.fullmatch(r'milestones(?:/[1-9][0-9]*)?', sub):
            raise Unsupported('native GitHub fetch does not support milestone endpoints')
        if sub == 'contents/issue-work-guard.json':
            if parse_qsl(parts.query, keep_blank_values=True) != [('ref', 'aw-coordination')]:
                raise Refused('invalid guard ref')
            d = self._call('fetch_file', {'repository_full_name': self.repo,
                'path': 'issue-work-guard.json', 'ref': 'aw-coordination', 'encoding': 'base64'})
            if d.get('encoding') != 'base64' or not isinstance(d.get('content'), str) or not re.fullmatch(r'[0-9a-f]{40}', str(d.get('sha', ''))):
                raise Refused('invalid native guard file')
            try:
                base64.b64decode(''.join(d['content'].split()), validate=True)
            except ValueError as e:
                raise Refused('invalid native guard content') from e
            self.guard_read = (d['sha'], self._guard_state(d['content']))
            return {'type': 'file', 'path': 'issue-work-guard.json', 'encoding': 'base64', 'sha': d['sha'], 'content': d['content']}
        if not re.fullmatch(r'(?:issues(?:/[1-9][0-9]*(?:/comments|/dependencies/blocked_by)?)?|issues/comments/[1-9][0-9]*|pulls/[1-9][0-9]*)', sub):
            raise Refused('unsupported GET endpoint')
        if not re.fullmatch(r'[A-Za-z0-9_=&%.-]*', parts.query):
            raise Refused('invalid query')
        d = self._call('fetch', {'url': 'https://api.github.com/' + path})
        if not isinstance(d.get('content'), str):
            raise Refused('native fetch has no JSON content')
        try:
            result = json.loads(d['content'])
        except ValueError as e:
            raise Refused('invalid native fetch JSON') from e
        if not isinstance(result, (dict, list)) or isinstance(result, dict) and ('message' in result and 'documentation_url' in result or 'error' in result):
            raise ProviderError(d)
        return result

    def _guard_state(self, encoded):
        try:
            state = json.loads(base64.b64decode(''.join(encoded.split()), validate=True), object_pairs_hook=_unique)
            if set(state) != {'protocol', 'repo', 'status', 'generation', 'owner'} or state['protocol'] != 'aw-issue-guard-v1' or state['repo'] != self.repo or state['status'] not in ('free', 'held'):
                raise ValueError()
            uuid.UUID(state['generation'])
            if state['status'] == 'held':
                uuid.UUID(state['owner'])
            elif state['owner'] is not None:
                raise ValueError()
            return state
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            raise Refused('invalid guard state') from e

    def prevalidate(self, method, path, payload):
        """Validate an issue request without I/O or changes to guard state."""
        if not isinstance(path, str) or '..' in path or not path.startswith(self.prefix):
            raise Refused('request outside repository')
        if not isinstance(payload, dict):
            raise Refused('invalid mutation')
        m = re.fullmatch(re.escape(self.prefix) + r'issues/([1-9][0-9]*)(/comments)?', path)
        create = method == 'POST' and path == self.prefix + 'issues'
        patch = method == 'PATCH' and m and not m.group(2)
        comment = method == 'POST' and m and m.group(2)
        if create:
            if set(payload) != {'title', 'body', 'milestone', 'labels'} or not all(isinstance(payload[k], str) and payload[k] for k in ('title', 'body')) or type(payload['milestone']) is not int or payload['milestone'] < 1 or not _string_list(payload['labels']):
                raise Refused('invalid issue creation')
        elif patch:
            if not payload or not set(payload) <= {'labels', 'milestone'} or 'labels' in payload and not _string_list(payload['labels']) or 'milestone' in payload and (type(payload['milestone']) is not int or payload['milestone'] < 1):
                raise Refused('invalid issue update')
        elif comment:
            raise Unsupported('native comment tool is documented for PR Conversation only')
        else:
            raise Refused('mutation outside planner scope')
        return m, create, patch

    def __call__(self, method, path, payload=None):
        if not isinstance(path, str) or '..' in path or not path.startswith(self.prefix):
            raise Refused('request outside repository')
        if method == 'GET' and payload is None:
            return self._get(path)
        guard_path = self.prefix + 'contents/issue-work-guard.json'
        if method == 'PUT' and path == guard_path:
            if not isinstance(payload, dict) or set(payload) != {'branch', 'message', 'sha', 'content'} or payload['branch'] != 'aw-coordination' or payload['message'] != 'aw issue guard transition' or not re.fullmatch(r'[0-9a-f]{40}', str(payload['sha'])) or not isinstance(payload['content'], str):
                raise Refused('invalid guard update')
            try:
                content = base64.b64decode(payload['content'], validate=True).decode('utf-8')
            except (ValueError, UnicodeError) as e:
                raise Refused('invalid guard content') from e
            new = self._guard_state(payload['content'])
            if self.guard_read is None or self.guard_read[0] != payload['sha'] or self.guard_read[1]['status'] == new['status'] or self.guard_read[1]['generation'] == new['generation']:
                raise Refused('guard transition needs matching read and new generation')
            if new['status'] == 'free' and self.guard_read[1] != self.issued_held:
                raise Refused('guard release does not match acquired state')
            self.guard_read = None
            result = self._call('update_file', {'repository_full_name': self.repo, 'path': 'issue-work-guard.json', 'branch': 'aw-coordination', 'sha': payload['sha'], 'content': content, 'message': payload['message']})
            self.issued_held = new if new['status'] == 'held' else None
            return result
        m, create, patch = self.prevalidate(method, path, payload)
        self.coordination.verify_for(self.repo, required=True)
        number = int(m.group(1)) if m else None
        if number:
            self._open_issue(number)
        self.coordination.mark_mutation_for(self.repo, required=True)
        if create:
            response = self._call('create_issue', {'repository_full_name': self.repo, **payload})
            number = response.get('number')
            if type(number) is not int or number < 1 or response.get('html_url') != f'https://github.com/{self.repo}/issues/{number}':
                raise Refused('issue creation outcome unknown; no retry')
            issue = self._open_issue(number)
            if issue.get('title') != payload['title'] or issue.get('body') != payload['body'] or not isinstance(issue.get('labels'), list) or {x.get('name') for x in issue['labels'] if isinstance(x, dict)} != set(payload['labels']) or not isinstance(issue.get('milestone'), dict) or issue['milestone'].get('number') != payload['milestone']:
                raise Refused('issue creation readback mismatch; no retry')
            return issue
        if patch:
            response = self._call('update_issue', {'repository_full_name': self.repo, 'issue_number': number, **payload})
            if type(response.get('number')) is not int or response['number'] != number or response.get('html_url') != f'https://github.com/{self.repo}/issues/{number}':
                raise Refused('issue update outcome unknown; no retry')
            issue = self._open_issue(number)
            if 'labels' in payload and (not isinstance(issue.get('labels'), list) or {x.get('name') for x in issue['labels'] if isinstance(x, dict)} != set(payload['labels'])):
                raise Refused('issue labels readback mismatch; no retry')
            if 'milestone' in payload and (not isinstance(issue.get('milestone'), dict) or issue['milestone'].get('number') != payload['milestone']):
                raise Refused('issue milestone readback mismatch; no retry')
            return issue
    def _open_issue(self, number):
        result = self._get(self.prefix + f'issues/{number}')
        if not isinstance(result, dict) or type(result.get('number')) is not int or result['number'] != number or result.get('html_url') != f'https://github.com/{self.repo}/issues/{number}' or result.get('state') != 'open' or 'pull_request' in result:
            raise Refused('issue readback mismatch; no retry')
        return result
