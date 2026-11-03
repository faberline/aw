import importlib.util
import base64
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ci_retry', Path(__file__).resolve().parents[1] / 'scripts/ci_retry.py')
retry = importlib.util.module_from_spec(spec); spec.loader.exec_module(retry)
worker_spec = importlib.util.spec_from_file_location('worker_retry_tests', Path(__file__).resolve().parents[1] / 'scripts/work.py')
worker = importlib.util.module_from_spec(worker_spec); worker_spec.loader.exec_module(worker)
HEAD = 'a' * 40
BASE = 'b' * 40


class Client:
    repo = 'o/r'
    def __init__(self):
        self.pull = {'number': 9, 'state': 'open', 'body':'Closes #7', 'head': {'sha': HEAD, 'ref':'issue/7', 'repo': {'full_name': self.repo}},
                     'base': {'sha': BASE, 'ref':'main', 'repo': {'full_name': self.repo}}}
        self.rows = [{'id': 1, 'updated_at': '2026-11-03T00:00:00Z', 'user': {'login': 'github-actions[bot]'},
                      'body': '<!-- issue-ci:' + 'd' * 64 + ':applied -->\nCI: todo for PR #9 head `' + HEAD + '`.\nhttps://github.com/o/r/actions/runs/10'}]
        self.rows.append({'id':2,'updated_at':'2026-11-03T00:00:01Z','user':{'login':'github-actions[bot]'},
            'body':'<!-- aw-job-failure:' + 'e'*64 + ' -->\nCI job failed for PR #9.\n\n- Head: `'+HEAD+'`; base: `'+BASE+'`.\n- Workflow: .github/workflows/ci.yml; run 10, attempt 1, job 20.'})
        self.checks = [{'id': 20, 'head_sha': HEAD, 'name': 'test', 'status': 'completed',
                        'conclusion': 'failure', 'app': {'id': 15368}}]
    def comments(self, number): return self.rows
    def pages(self, path): return []
    def api(self, path):
        if '/pulls/' in path: return self.pull
        if '/actions/workflows/' in path:
            return {'total_count':1, 'workflow_runs':[{'id':10, 'workflow_id':77,
                    'path':'.github/workflows/ci.yml','head_sha':HEAD,'event':'pull_request',
                    'run_number':1,'run_attempt':1,'status':'completed','conclusion':'success',
                    'pull_requests':[{'number':9,'head':{'sha':HEAD},'base':{'sha':BASE}}]}]}
        if '/contents/.github/workflows/' in path: return {'sha':'c'*40}
        if '/contents/' in path:
            raw=json.dumps({'qualification':{'trusted_workflows':{'.github/workflows/ci.yml':{'id':77,'definition_sha':'c'*40}}}}).encode()
            return {'type':'file','encoding':'base64','content':base64.b64encode(raw).decode()}
        if '/actions/runs/' in path:
            run_id=int(path.rsplit('/',1)[1])
            return {'id':run_id,'workflow_id':77,'path':'.github/workflows/ci.yml','head_sha':HEAD,
                    'event':'pull_request','run_number':run_id-9,'run_attempt':1,'status':'completed',
                    'conclusion':'failure','pull_requests':[{'number':9,'head':{'sha':HEAD},'base':{'sha':BASE}}]}
        if '/actions/jobs/' in path:
            job_id=int(path.rsplit('/',1)[1])
            return {'id':job_id,'run_id':10 if job_id==20 else 11,'name':'test','run_attempt':1,
                    'head_sha':HEAD,'status':'completed','conclusion':'failure'}
        return {'total_count': len(self.checks), 'check_runs': self.checks}


class RetryTests(unittest.TestCase):
    def test_exact_current_failed_check_can_retry(self):
        self.assertTrue(retry.retry_ready(Client(), 7, 9))

    def test_stale_journal_running_or_successful_current_head_waits(self):
        c = Client(); c.pull['head']['sha'] = 'b' * 40
        self.assertFalse(retry.retry_ready(c, 7, 9))
        for state, conclusion in [('in_progress', None), ('queued', None), ('completed', 'success'), ('completed', 'cancelled')]:
            c = Client(); c.checks[0].update(status=state, conclusion=conclusion)
            with self.subTest(state=state, conclusion=conclusion): self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_latest_check_replaces_older_failure(self):
        c = Client(); c.checks.append(dict(c.checks[0], id=21, conclusion='success'))
        self.assertFalse(retry.retry_ready(c, 7, 9))
        c.checks[1]['status'] = 'in_progress'; c.checks[1]['conclusion'] = None
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_same_job_name_from_another_workflow_does_not_hide_failure(self):
        class TwoWorkflows(Client):
            def api(self, path):
                if path.endswith('/actions/runs/11'):
                    return {'id':11,'workflow_id':78,'head_sha':HEAD,'run_number':99,'run_attempt':1}
                return super().api(path)
        c=TwoWorkflows(); c.checks.append(dict(c.checks[0], id=21, conclusion='success'))
        self.assertTrue(retry.retry_ready(c, 7, 9))

    def test_third_party_same_name_is_not_an_actions_job(self):
        c = Client()
        c.checks.insert(0, dict(c.checks[0], id=99, app={'id':999}, conclusion='success'))
        original = c.api
        def api(path):
            if path.endswith('/actions/jobs/99'):
                raise AssertionError('third-party check must not be queried as an Actions job')
            return original(path)
        c.api = api
        self.assertTrue(retry.retry_ready(c, 7, 9))

    def test_report_must_match_current_base_and_run_attempt(self):
        c=Client(); c.pull['base']['sha']='c'*40
        self.assertFalse(retry.retry_ready(c, 7, 9))
        c=Client(); c.rows[1]['body']=c.rows[1]['body'].replace('attempt 1','attempt 2')
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_old_pending_journal_does_not_block_current_applied_failure(self):
        c = Client()
        old = dict(c.rows[0], id=3, body=c.rows[0]['body'].replace(':applied', ':pending').replace(HEAD, 'f'*40))
        c.rows.insert(0, old)
        self.assertTrue(retry.retry_ready(c, 7, 9))
        old['body'] = old['body'].replace('f'*40, HEAD)
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_untrusted_or_missing_journal_without_human_todo_waits(self):
        c = Client(); c.rows[0]['user']['login'] = 'someone-else'
        self.assertFalse(retry.retry_ready(c, 7, 9))
        c.checks[0].update(status='in_progress', conclusion=None)
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_explicit_request_waits_for_current_ci_but_needs_no_ci_failure(self):
        c = Client(); c.checks[0]['conclusion'] = 'success'
        c.rows = []
        c.pages = lambda path: [dict(id=9, created_at='2026-11-03T01:00:00Z', event='labeled',
                                    label={'name':'todo'}, actor={'type':'User','login':'alice'})]
        self.assertTrue(retry.retry_ready(c, 7, 9))
        c.checks[0].update(status='in_progress', conclusion=None)
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_todo_waits_while_workflow_runs_between_jobs(self):
        c = Client(); c.rows = []; c.checks[0]['conclusion'] = 'success'
        c.pages = lambda path: [dict(id=9, created_at='2026-11-03T01:00:00Z', event='labeled',
                                    label={'name':'todo'}, actor={'type':'User','login':'alice'})]
        original = c.api
        def api(path):
            result = original(path)
            if '/actions/workflows/' in path:
                result['workflow_runs'][0]['status'] = 'in_progress'
            return result
        c.api = api
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_ci_retry_waits_for_new_workflow_even_between_jobs(self):
        c = Client(); original = c.api
        def api(path):
            result = original(path)
            if '/actions/workflows/' in path:
                result['workflow_runs'][0]['status'] = 'in_progress'
            return result
        c.api = api
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_wrong_head_foreign_incomplete_checks_refuse(self):
        c = Client(); c.pull['head']['repo']['full_name'] = 'other/r'
        self.assertFalse(retry.retry_ready(c, 7, 9))
        c = Client(); c.checks[0]['head_sha'] = 'b' * 40
        self.assertFalse(retry.retry_ready(c, 7, 9))
        c = Client(); c.checks = []
        self.assertFalse(retry.retry_ready(c, 7, 9))
        c = Client(); c.checks.append(dict(c.checks[0]))
        self.assertFalse(retry.retry_ready(c, 7, 9))

    def test_stale_first_candidate_does_not_starve_next_issue(self):
        c = Client(); c.pull['head']['sha'] = 'b' * 40
        snapshot = {'issues': [{'number': 7}, {'number': 8}], 'selected': 7}
        result = worker.eligible_snapshot(c, snapshot, lambda number: ('issue/' + str(number), 9 if number == 7 else None))
        self.assertEqual(result['selected'], 8)

    def test_active_wait_requires_trusted_workflow_pr_head_base_and_attempt(self):
        class Active(Client):
            def api(self, path):
                if '/actions/jobs/' in path:
                    return {'id':20,'run_id':10,'run_attempt':1,'head_sha':HEAD}
                if '/actions/runs/' in path:
                    return {'id':10,'workflow_id':77,'path':'.github/workflows/ci.yml','head_sha':HEAD,
                            'event':'pull_request','run_attempt':1,'status':'in_progress',
                            'pull_requests':[{'number':9,'head':{'sha':HEAD},'base':{'sha':BASE}}]}
                return super().api(path)
        c = Active(); c.checks[0]['status'] = 'in_progress'
        c.checks.insert(0, dict(c.checks[0], id=99, app={'id':999}))
        original = c.api
        def api(path):
            if path.endswith('/actions/jobs/99'):
                raise AssertionError('third-party check must not be queried as an Actions job')
            return original(path)
        c.api = api
        self.assertTrue(retry.active_required(c, c.pull, c.checks))
        c.pull['base']['sha'] = 'c' * 40
        self.assertFalse(retry.active_required(c, c.pull, c.checks))


if __name__ == '__main__': unittest.main()
