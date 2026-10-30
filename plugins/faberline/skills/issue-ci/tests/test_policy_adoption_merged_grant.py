"""A merged data grant is review input, never authorization by itself."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml

import test_policy_adoption as adoption

spec = importlib.util.spec_from_file_location(
    'merged_grant_test', Path(__file__).parents[1] / 'scripts/policy_adoption_merged_grant.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HEAD, MERGE, PARENT, MAIN, BLOB = ('3' * 40, '4' * 40, '5' * 40, '6' * 40, '7' * 40)


class Fixture:
    def __init__(self):
        self.grant = adoption.grant()
        self.grant.pop('grant_review')
        self.pull = dict(number=77, state='closed', merged=True, merge_commit_sha=MERGE,
                         title='Grant', body='Closes #97', changed_files=1,
                         head=dict(sha=HEAD, repo={'full_name': 'faberline/aw'}),
                         base=dict(ref='main', repo={'full_name': 'faberline/aw'}))
        self.commit = dict(sha=MERGE, parents=[dict(sha=PARENT)])
        self.issue = dict(number=97, state='closed', title='Grant', body='Grant requirements')
        self.files = [dict(filename=m.a.GRANTS_PATH, status='modified', sha=BLOB)]
        self.merged_files = [dict(filename=m.a.GRANTS_PATH, status='modified', sha=BLOB)]
        self.previous = []
        self.proposed = [self.grant]
        self.merged = [self.grant]
        self.current = [self.grant]
        self.compare = 'ahead'
        self.main_reads = 0
        self.changed_main = False

    def api(self, path):
        if path == 'repos/faberline/aw/pulls/77': return copy.deepcopy(self.pull)
        if path == f'repos/faberline/aw/commits/{MERGE}': return copy.deepcopy(self.commit)
        if path == 'repos/faberline/aw/commits/main':
            self.main_reads += 1
            return {'sha': '8' * 40 if self.changed_main and self.main_reads > 1 else MAIN}
        if path == f'repos/faberline/aw/compare/{MERGE}...{MAIN}': return {'status': self.compare}
        if path == 'repos/faberline/aw/issues/97': return copy.deepcopy(self.issue)
        if path == 'repos/o/r/issues/7': return dict(number=7, title='Target', body='Requirements')
        if path == 'repos/o/r/pulls/8': return dict(number=8, title='Target PR', body='Closes #7')
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path == 'repos/faberline/aw/pulls/77/files': return copy.deepcopy(self.files)
        if path == f'repos/faberline/aw/commits/{MERGE}': return copy.deepcopy(self.merged_files)
        raise AssertionError(path)

    def blob(self, api, repo, path, ref, expected=None):
        if ref in (HEAD, MERGE) and expected != BLOB: raise AssertionError(expected)
        rows = {HEAD: self.proposed, MERGE: self.merged, PARENT: self.previous, MAIN: self.current}
        return json.dumps(dict(version=1, grants=rows[ref]))

    def run(self):
        with patch.object(m.a.review, 'blob', side_effect=self.blob), \
             patch.object(m.a, 'evaluate', return_value={'ci': 'verified'}), \
             patch.object(m.a, 'latest_target_review'), \
             patch.object(m.a, 'candidate_policy', return_value={'policy': 'verified'}):
            return m.packet(self, 'o/r', 77, HEAD, adoption.EVALUATOR)


class MergedGrantTests(unittest.TestCase):
    def test_merged_data_is_only_review_input_and_keeps_exact_product_evidence(self):
        f = Fixture(); packet = f.run()
        self.assertEqual(packet['grant'], f.grant)
        self.assertEqual(packet['grant_pr']['merge'], MERGE)
        self.assertEqual(packet['verified_target'], {'ci': 'verified'})
        self.assertNotIn('decision', packet)

    def test_merged_grant_without_review_cannot_enter_existing_publisher(self):
        f = Fixture()
        with self.assertRaisesRegex(RuntimeError, 'grant_missing_or_ambiguous'):
            m.a.choose_grant(dict(version=1, grants=[f.grant]), 'o/r', adoption.EVALUATOR)

    def test_requires_merged_squash_in_default_history(self):
        changes = [lambda f: f.pull.update(merged=False),
                   lambda f: f.pull.update(merge_commit_sha='bad'),
                   lambda f: f.commit.update(parents=[]),
                   lambda f: f.commit.update(parents=[dict(sha=PARENT), dict(sha=HEAD)]),
                   lambda f: setattr(f, 'compare', 'diverged'),
                   lambda f: setattr(f, 'changed_main', True)]
        for change in changes:
            f = Fixture(); change(f)
            with self.subTest(change=change), self.assertRaises(RuntimeError): f.run()

    def test_refuses_extra_files_changed_merge_duplicate_and_stale_grants(self):
        def add_file(f):
            f.pull['changed_files'] = 2
            f.files.append(dict(filename='x', status='added', sha='9' * 40))
        def mutate_merge(f):
            altered = copy.deepcopy(f.grant); altered['head'] = '9' * 40
            f.merged = [altered]
        def duplicate(f): f.proposed = f.merged = [f.grant, f.grant]
        def stale(f): f.current = []
        def existing(f): f.previous = [f.grant]
        def altered_commit(f): f.merged_files.append(dict(filename='other', status='added', sha='9'*40))
        def wrong_commit_blob(f): f.merged_files[0]['sha'] = '9'*40
        for change in (add_file, mutate_merge, duplicate, stale, existing, altered_commit, wrong_commit_blob):
            f = Fixture(); change(f)
            with self.subTest(change=change), self.assertRaises(RuntimeError): f.run()

    def test_target_ci_or_policy_failure_cannot_form_a_review_packet(self):
        for method in ('evaluate', 'latest_target_review', 'candidate_policy'):
            f = Fixture()
            with patch.object(m.a.review, 'blob', side_effect=f.blob), \
                 patch.object(m.a, 'evaluate', return_value={'ci': 'verified'}), \
                 patch.object(m.a, 'latest_target_review'), \
                 patch.object(m.a, 'candidate_policy', return_value={'policy': 'verified'}), \
                 patch.object(m.a, method, side_effect=RuntimeError('unverified')), \
                 self.subTest(method=method), self.assertRaisesRegex(RuntimeError, 'unverified'):
                m.packet(f, 'o/r', 77, HEAD, adoption.EVALUATOR)

    def test_receipt_requires_a_pass_and_stable_inputs(self):
        f = Fixture(); before = f.run()
        passed = lambda *args: dict(decision='pass', summary='Reviewed', findings=[], uncertainties=[])
        with patch.object(m, 'packet', return_value=before):
            receipt = m.produce(f, 'o/r', 77, HEAD, adoption.EVALUATOR, 'token', passed)
        self.assertEqual(receipt['packet_sha256'], m.a.r.digest(before))
        self.assertEqual(receipt['grant_merge'], MERGE)
        changed = copy.deepcopy(before); changed['grant']['base'] = '0' * 40
        with patch.object(m, 'packet', side_effect=[before, changed]), \
             self.assertRaisesRegex(RuntimeError, 'inputs_changed'):
            m.produce(f, 'o/r', 77, HEAD, adoption.EVALUATOR, 'token', passed)
        for result in (dict(decision='fail', summary='No', findings=['issue'], uncertainties=[]),
                       dict(decision='pass', summary='Unclear', findings=[], uncertainties=['unknown']),
                       dict(decision='pass', summary='Incomplete', findings=[])):
            with patch.object(m, 'packet', return_value=before), \
                 self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'not_pass'):
                m.produce(f, 'o/r', 77, HEAD, adoption.EVALUATOR, 'token', lambda *args: result)

    def test_entry_emits_only_a_success_receipt(self):
        env = dict(GITHUB_REPOSITORY='o/r', INPUT_GRANT_PR='77', INPUT_GRANT_HEAD=HEAD,
                   AW_ACTION_REF=adoption.EVALUATOR, CLAUDE_CODE_OAUTH_TOKEN='test-token')
        result = dict(decision='pass', repo='o/r')
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(m, 'produce', return_value=result), \
             contextlib.redirect_stdout(output):
            m.main()
        self.assertEqual(output.getvalue().splitlines(), [m.MARKER + m.a.r.canonical(result)])
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(m, 'produce', side_effect=RuntimeError('failed')), \
             contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            m.main()
        self.assertNotIn(m.MARKER, output.getvalue())

    def test_verifier_authenticates_exact_latest_product_review(self):
        f = Fixture(); current = f.run(); grant = f.grant
        att = dict(grant_pr=77, grant_head=HEAD, workflow_path='.github/workflows/grant.yml',
                   workflow_id=9, workflow_blob='a'*40, source_commit='b'*40,
                   branch='codex/grant-review', run_id=100, attempt=1, job_id=101)
        workflow = {'name': 'Review merged product grant',
                    'on': {'push': {'branches': [att['branch']], 'paths': [att['workflow_path']]}},
                    'permissions': {}, 'jobs': {'review': {
                        'runs-on': 'ubuntu-latest', 'timeout-minutes': '70',
                        'permissions': {'actions': 'read', 'contents': 'read', 'pull-requests': 'read', 'issues': 'read'},
                        'steps': [{'uses': 'faberline/aw/.github/actions/policy-adoption-merged-grant-review@'+adoption.EVALUATOR,
                                   'with': {'grant-pr': '77', 'grant-head': HEAD,
                                            'claude-token': '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}}]}}}
        run = dict(id=100, workflow_id=9, path=att['workflow_path'], head_sha=att['source_commit'],
                   head_branch=att['branch'], event='push', head_repository={'full_name': 'o/r'},
                   run_attempt=1, run_number=2, status='completed', conclusion='success')
        receipt = dict(version=1, repo='o/r', target_pr=grant['pr'], target_head=grant['head'],
                       grant_pr=77, grant_head=HEAD, grant_merge=MERGE, grant_parent=PARENT,
                       grant_default=MAIN, grant_blob=BLOB,
                       grant_pr_sha256=m.a.r.digest(['Grant', 'Closes #97']),
                       grant_issue_sha256=m.a.r.digest(current['grant_issue']), grant_digest=m.a.r.digest(grant),
                       packet_sha256=m.a.r.digest(current), decision='pass', findings=[], uncertainties=[])
        class API:
            jobs = [dict(name='review', id=101, status='completed', conclusion='success')]
            runs = [dict(id=100, run_number=2, run_attempt=1)]
            def api(self, path): return copy.deepcopy(run)
            def pages(self, path, key=None):
                return copy.deepcopy(self.runs if key == 'workflow_runs' else self.jobs)
        api = API()
        def execute(log, raw=None):
            with patch.object(m.a.review, 'blob', return_value=raw or yaml.safe_dump(workflow)), \
                 patch.object(m, 'packet', return_value=current):
                return m.verify(api, 'o/r', adoption.EVALUATOR, att, lambda *args: log)
        line = m.MARKER + json.dumps(receipt) + '\n'
        self.assertEqual(execute(line), receipt)
        for log in ('', line + line, m.MARKER + json.dumps(dict(receipt, decision='fail')) + '\n'):
            with self.subTest(log=log), self.assertRaises(RuntimeError): execute(log)
        api.runs.append(dict(id=102, run_number=3, run_attempt=1))
        with self.assertRaisesRegex(RuntimeError, 'not_latest'): execute(line)
        api.runs.pop(); api.jobs[0]['conclusion'] = 'failure'
        with self.assertRaisesRegex(RuntimeError, 'review_job'): execute(line)
        api.jobs[0]['conclusion'] = 'success'
        unsafe = copy.deepcopy(workflow); unsafe['jobs']['review']['permissions']['contents'] = 'write'
        with self.assertRaisesRegex(RuntimeError, 'workflow'): execute(line, yaml.safe_dump(unsafe))
        merged_data = {k: current[k] for k in ('grant', 'grant_pr', 'grant_issue')}
        with patch.object(m.a.review, 'blob', return_value=yaml.safe_dump(workflow)), \
             patch.object(m, 'merged_data', return_value=merged_data), \
             patch.object(m.a, 'evaluate_merged', return_value={'ci': 'verified'}) as merged_verify:
            self.assertEqual(m.verify(api, 'o/r', adoption.EVALUATOR, att, lambda *args: line, merged=True), receipt)
        self.assertEqual(merged_verify.call_count, 1)
        changed = copy.deepcopy(merged_data); changed['grant_issue']['body'] = 'Changed after review'
        with patch.object(m.a.review, 'blob', return_value=yaml.safe_dump(workflow)), \
             patch.object(m, 'merged_data', return_value=changed), \
             patch.object(m.a, 'evaluate_merged', return_value={'ci': 'verified'}), \
             self.assertRaisesRegex(RuntimeError, 'review_receipt'):
            m.verify(api, 'o/r', adoption.EVALUATOR, att, lambda *args: line, merged=True)
        with patch.object(m.a.review, 'blob', return_value=yaml.safe_dump(workflow)), \
             patch.object(m, 'merged_data', return_value=merged_data), \
             patch.object(m.a, 'evaluate_merged', side_effect=RuntimeError('merged CI unverified')), \
             self.assertRaisesRegex(RuntimeError, 'merged CI unverified'):
            m.verify(api, 'o/r', adoption.EVALUATOR, att, lambda *args: line, merged=True)

    def test_discovery_only_uses_latest_target_specific_run_and_matching_grant(self):
        f = Fixture(); grant = f.grant
        branch = f"codex/merged-grant-{grant['pr']}-{grant['head']}"
        path = '.github/workflows/review-merged-product-grant.yml'
        row = dict(id=100, workflow_id=9, run_number=2, run_attempt=1, head_sha='b'*40,
                   path=path, event='push', head_branch=branch, head_repository={'full_name': 'o/r'})
        raw = yaml.safe_dump({'jobs': {'review': {'steps': [{'with': {'grant-pr': '77', 'grant-head': HEAD}}]}}})
        class API:
            runs = [row]
            def pages(self, path, key=None):
                return copy.deepcopy(self.runs if key == 'workflow_runs' else [dict(id=101)])
        api = API()
        receipt = dict(grant_digest=m.a.r.digest(grant))
        with patch.object(m.a.review, 'blob', return_value=raw), \
             patch.object(m, 'verify', return_value=receipt) as verify:
            result = m.discover(api, grant, adoption.EVALUATOR, lambda *args: '')
        self.assertEqual(result['attestation']['run_id'], 100)
        self.assertEqual(result['attestation']['branch'], branch)
        self.assertEqual(verify.call_args.args[2], adoption.EVALUATOR)
        with patch.object(m.a.review, 'blob', return_value=raw), \
             patch.object(m, 'verify', return_value=dict(grant_digest='0'*64)), \
             self.assertRaisesRegex(RuntimeError, 'discovery_mismatch'):
            m.discover(api, grant, adoption.EVALUATOR, lambda *args: '')
        newer = dict(row, id=102, run_number=3, run_attempt=1, head_sha='c'*40)
        api.runs = [row, newer]
        with patch.object(m.a.review, 'blob', return_value=raw), \
             patch.object(m, 'verify', side_effect=RuntimeError('newer review failed')), \
             self.assertRaisesRegex(RuntimeError, 'newer review failed'):
            m.discover(api, grant, adoption.EVALUATOR, lambda *args: '')
        api.runs = [row, dict(newer, workflow_id=10)]
        with self.assertRaisesRegex(RuntimeError, 'discovery_runs'):
            m.discover(api, grant, adoption.EVALUATOR, lambda *args: '')

    def test_read_only_authorization_requires_one_unchanged_trusted_grant(self):
        f = Fixture(); grant = f.grant
        document = dict(source_commit=MAIN, config=dict(version=1, grants=[grant]))
        evidence = dict(receipt=dict(grant_default=MAIN))
        with patch.object(m, 'discover', return_value=evidence):
            result = m.authorize(None, 'o/r', adoption.EVALUATOR, None, grant_loader=lambda: document)
        self.assertEqual(result['grant'], grant)
        self.assertEqual(result['evidence'], evidence)
        later = copy.deepcopy(document); later['source_commit'] = '0'*40
        changes = iter([document, later])
        with patch.object(m, 'discover', return_value=evidence), \
             self.assertRaisesRegex(RuntimeError, 'source_changed'):
            m.authorize(None, 'o/r', adoption.EVALUATOR, None, grant_loader=lambda: next(changes))
        ambiguous = dict(source_commit=MAIN, config=dict(version=1, grants=[grant, grant]))
        with patch.object(m, 'discover') as lookup, self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            m.authorize(None, 'o/r', adoption.EVALUATOR, None, grant_loader=lambda: ambiguous)
        lookup.assert_not_called()
        old = adoption.grant()
        legacy = dict(source_commit=MAIN, config=dict(version=1, grants=[old]))
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            m.authorize(None, 'o/r', adoption.EVALUATOR, None, grant_loader=lambda: legacy)
