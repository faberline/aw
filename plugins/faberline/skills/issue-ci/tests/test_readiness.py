"""Isolated offline fixtures: no network, cloud, credentials or lifecycle writes."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_lifecycle as legacy
import test_qualification as qualified

ci = legacy.ci
r = legacy.load('review_readiness', legacy.SKILLS / 'issue-ci/scripts/readiness.py')
action = qualified.action
HEAD, BASE, EVALUATOR, POLICY_SOURCE = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
GATE = '.github/workflows/pr.yml'
PRODUCER = '.github/workflows/readiness.yml'
ENFORCER = 'scripts/ci/assertions.py'
AW_ACTION = '.github/actions/issue-ci/action.yml'


def blob(text):
    raw = text.encode()
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


class Harness:
    def __init__(self):
        self.config = qualified.config()
        self.config['lifecycle_mode'] = 'failure-only'
        self.pull = qualified.pr()
        self.changes = qualified.files('docs/guide.md')
        self.source = {
            ('o/r', GATE): 'name: PR\njobs:\n  policy:\n    runs-on: ubuntu-latest\n    steps:\n      - run: python3 scripts/ci/assertions.py\n',
            ('o/r', PRODUCER): 'name: Readiness\njobs:\n  receipt:\n    steps:\n      - uses: faberline/aw/.github/actions/issue-ci@' + EVALUATOR + '\n',
            ('o/r', ENFORCER): '# reviewed nonzero enforcer fixture\n',
            ('faberline/aw', AW_ACTION): 'name: Pinned evaluator\nruns:\n  using: composite\n  steps:\n    - run: python3 pinned-readiness.py\n',
        }
        self.config['qualification']['trusted_workflows'][GATE]['definition_sha'] = blob(self.source['o/r', GATE])
        self.config['review_readiness'] = dict(version=1,
            producer=dict(path=PRODUCER, id=88, definition_sha=blob(self.source['o/r', PRODUCER]), job='publish receipt'),
            sources={
                'enforcer': dict(repo='o/r', ref=POLICY_SOURCE, path=ENFORCER, blob_sha=blob(self.source['o/r', ENFORCER])),
                'aw-action': dict(repo='faberline/aw', ref=EVALUATOR, path=AW_ACTION, blob_sha=blob(self.source['faberline/aw', AW_ACTION])),
            },
            assertions={key: dict(names=[key + '-assertion'], enforcers=['enforcer'])
                        for key in self.config['qualification']['checks']})
        self.run = dict(id=10, workflow_id=77, path=GATE, run_number=10, run_attempt=2,
            head_sha=HEAD, event='pull_request', head_repository={'full_name': 'o/r'},
            status='completed', conclusion='success', html_url='https://github.com/o/r/actions/runs/10',
            pull_requests=[dict(number=8, head=dict(sha=HEAD), base=dict(sha=BASE))])
        self.job = dict(id=101, name='policy', status='completed', conclusion='success',
                        html_url='https://github.com/o/r/actions/runs/10/job/101')
        self.producer = dict(id=90, workflow_id=88, path=PRODUCER, run_attempt=1, head_sha=POLICY_SOURCE,
            event='workflow_run', head_repository={'full_name': 'o/r'}, head_branch='main',
            status='completed', conclusion='success', html_url='https://github.com/o/r/actions/runs/90')
        self.producer_job = dict(id=901, name='publish receipt', status='completed', conclusion='success')
        self.logs = {101: r.ASSERTION_MARKER + json.dumps(self.assertion())}
        self.writes = []
        self.snapshot_hook = None

    def document(self):
        return dict(config=copy.deepcopy(self.config), source_commit=POLICY_SOURCE, path='.github/issue-ci-required-gates.json')

    def assertion(self):
        return dict(version=1, repo='o/r', pr=8, head=HEAD, base=BASE, run_id=10, run_attempt=2,
                    job_id=101, check='policy', assertions={'policy-assertion': 'passed'})

    def api(self, path, method='GET', data=None):
        if method != 'GET':
            self.writes.append((path, method)); raise AssertionError('readiness must never mutate')
        if path == 'repos/o/r': return dict(default_branch='main')
        if path == 'repos/o/r/pulls/8': return copy.deepcopy(self.pull)
        if path == 'repos/o/r/actions/runs/90': return copy.deepcopy(self.producer)
        for (repo, file), text in self.source.items():
            if path.startswith(f'repos/{repo}/contents/{file}?ref='):
                return dict(type='file', encoding='base64', sha=blob(text), content=base64.b64encode(text.encode()).decode())
        raise AssertionError('unexpected API ' + path)

    def pages(self, path, key=None):
        if path == 'repos/o/r/pulls/8/files': return copy.deepcopy(self.changes)
        if '/actions/runs/90/attempts/' in path: return [copy.deepcopy(self.producer_job)]
        if '/actions/workflows/pr.yml/runs?' in path: return [copy.deepcopy(self.run)]
        if '/actions/runs/10/attempts/' in path:
            if self.snapshot_hook: self.snapshot_hook()
            return [copy.deepcopy(self.job)]
        raise AssertionError('unexpected pages ' + path)

    def patches(self):
        return patch.multiple(ci, api=self.api, pages=self.pages)

    def produce(self, completed=False):
        with self.patches(), patch.object(r, 'job_log', side_effect=lambda repo, job: self.logs[job]):
            return r.produce(ci, 'o/r', 8, HEAD, self.document, EVALUATOR, 90, completed)

    def verify(self, receipt):
        self.logs[901] = r.RECEIPT_MARKER + json.dumps(receipt)
        with self.patches(), patch.object(r, 'job_log', side_effect=lambda repo, job: self.logs[job]):
            return r.verify(ci, receipt, 'o/r', 8, HEAD, self.document, EVALUATOR)


class ReadinessTests(unittest.TestCase):
    def test_ready_in_failure_only_is_read_only_and_never_done_or_review_pass(self):
        h = Harness(); receipt = h.produce()
        self.assertEqual(receipt['payload']['status'], 'ready')
        self.assertEqual(receipt['payload']['assertions'][0]['count'], 1)
        self.assertEqual(h.writes, [])
        self.assertNotIn('done', receipt['payload'])
        self.assertEqual(h.verify(receipt), receipt)

    def test_receipt_contains_exact_version_policy_inventory_and_transitive_pins(self):
        h = Harness(); p = h.produce()['payload']
        self.assertEqual(p['version'], 1)
        self.assertEqual(p['inputs']['head'], HEAD)
        self.assertEqual(p['inputs']['base'], BASE)
        self.assertEqual(p['inputs']['policy_source'], POLICY_SOURCE)
        self.assertEqual(p['inputs']['policy_sha256'], r.digest(h.config))
        self.assertEqual(p['inputs']['changed_inventory_sha256'], r.digest(h.changes))
        self.assertEqual(p['evidence'][0]['run_attempt'], 2)
        self.assertEqual(p['evidence'][0]['workflow_id'], 77)
        self.assertTrue(any(s.get('repo') == 'faberline/aw' and s['ref'] == EVALUATOR for s in p['transitive_pins']))

    def test_real_qualification_blockers_are_not_ignored(self):
        h = Harness(); h.config['qualification_blockers'] = ['functional coverage missing']
        p = h.produce()['payload']
        self.assertEqual(p['status'], 'fail')
        self.assertIn('qualification_blocker', [x['code'] for x in p['reasons']])

    def test_unknown_protected_and_incomplete_changes_fail_closed(self):
        for path in ['unknown/code.rs', ENFORCER, '.github/workflows/pr.yml']:
            h = Harness(); h.changes = qualified.files(path)
            with self.subTest(path=path): self.assertEqual(h.produce()['payload']['status'], 'fail')
        h = Harness(); h.pull['changed_files'] = 2
        self.assertEqual(h.produce()['payload']['status'], 'fail')

    def test_missing_run_or_job_waits_and_never_ready(self):
        for change in ['run', 'job']:
            h = Harness()
            if change == 'run': h.run['head_sha'] = 'e' * 40
            else: h.job['name'] = 'other'
            with self.subTest(change=change): self.assertEqual(h.produce()['payload']['status'], 'wait')

    def test_failure_skip_cancelled_unknown_and_pending(self):
        for conclusion in ['failure', 'skipped', 'cancelled', 'neutral', None]:
            h = Harness(); h.job['conclusion'] = conclusion
            with self.subTest(conclusion=conclusion): self.assertEqual(h.produce()['payload']['status'], 'fail')
        h = Harness(); h.run['status'] = 'in_progress'; h.run['conclusion'] = None
        self.assertEqual(h.produce()['payload']['status'], 'wait')
        h = Harness(); h.job['status'] = 'mystery'
        self.assertEqual(h.produce()['payload']['status'], 'fail')

    def test_zero_failed_skipped_missing_duplicate_and_old_attempt_assertions_fail(self):
        for assertion in [{}, {'policy-assertion': 'failed'}, {'policy-assertion': 'skipped'}, {'fake': 'passed'}]:
            h = Harness(); value = h.assertion(); value['assertions'] = assertion
            h.logs[101] = r.ASSERTION_MARKER + json.dumps(value)
            with self.subTest(assertion=assertion): self.assertEqual(h.produce()['payload']['status'], 'fail')
        for log in ['', 'artifact says green', r.ASSERTION_MARKER + '{}']:
            h = Harness(); h.logs[101] = log
            self.assertEqual(h.produce()['payload']['status'], 'fail')
        h = Harness(); h.logs[101] += '\n' + h.logs[101]
        self.assertEqual(h.produce()['payload']['status'], 'fail')
        h = Harness(); value = h.assertion(); value['run_attempt'] = 1
        h.logs[101] = r.ASSERTION_MARKER + json.dumps(value)
        self.assertEqual(h.produce()['payload']['status'], 'fail')

    def test_missing_contract_and_zero_roster_refuse_before_qualification(self):
        for field in ['qualification', 'review_readiness']:
            h = Harness(); del h.config[field]
            with self.assertRaises(r.Refused): h.produce()
        h = Harness(); h.config['review_readiness']['assertions']['policy']['names'] = []
        with self.assertRaisesRegex(r.Refused, 'invalid_assertion_contract'): h.produce()
        h = Harness(); del h.config['review_readiness']['assertions']['policy']
        h.config['review_readiness']['assertions']['unit']['names'] = ['unit']
        self.assertEqual(h.produce()['payload']['status'], 'fail')

    def test_mutable_transitive_ref_missing_pin_and_unreviewed_bytes_fail(self):
        for suffix in ['main', 'v1', '${{ inputs.version }}']:
            h = Harness(); h.source['o/r', GATE] += '      - uses: a/b@' + suffix + '\n'
            h.config['qualification']['trusted_workflows'][GATE]['definition_sha'] = blob(h.source['o/r', GATE])
            with self.subTest(ref=suffix): self.assertEqual(h.produce()['payload']['status'], 'fail')
        h = Harness(); del h.config['review_readiness']['sources']['aw-action']
        self.assertEqual(h.produce()['payload']['status'], 'fail')
        h = Harness(); h.source['o/r', ENFORCER] += '# changed\n'
        self.assertEqual(h.produce()['payload']['status'], 'fail')

    def test_enforcer_root_must_be_protected(self):
        h = Harness(); h.config['qualification']['protected_paths'] = ['.github/policy.json']
        self.assertEqual(h.produce()['payload']['status'], 'fail')

    def test_producer_must_invoke_this_evaluator_not_another_version(self):
        h = Harness(); h.source['o/r', PRODUCER] = 'name: Readiness\njobs:\n  receipt:\n    steps:\n      - run: echo forged\n'
        h.config['review_readiness']['producer']['definition_sha'] = blob(h.source['o/r', PRODUCER])
        p = h.produce()['payload']
        self.assertEqual(p['status'], 'fail')
        self.assertIn('evaluator_binding', [v['code'] for v in p['reasons']])

    def test_alias_flow_dynamic_and_escaped_source_syntax_refuses(self):
        for text in ['jobs: &jobs\n', 'steps: [{uses: a/b@main}]', '"us\\u0065s": a/b@main', 'uses:\n  a/b@main']:
            with self.subTest(text=text), self.assertRaises(r.Refused): r.literal_uses(text)

    def test_producer_cannot_be_its_own_required_gate(self):
        h = Harness(); h.config['review_readiness']['producer']['path'] = GATE
        with self.assertRaisesRegex(r.Refused, 'producer_cycle'): h.produce()

    def test_foreign_wrong_event_id_branch_and_attempt_producer_refuse(self):
        for change in [dict(workflow_id=9), dict(event='pull_request'), dict(head_branch='issue/7'),
                       dict(head_repository={'full_name': 'fork/r'}), dict(run_attempt=0)]:
            h = Harness(); h.producer.update(change)
            with self.subTest(change=change), self.assertRaises(r.Refused): h.produce()

    def test_receipt_forgery_recomputed_hash_cannot_replace_authenticated_log(self):
        h = Harness(); receipt = h.produce()
        with h.patches(), patch.object(r, 'job_log', side_effect=lambda repo, job: h.logs.get(job, 'PR artifact: green')):
            with self.assertRaisesRegex(r.Refused, 'unpublished_receipt'):
                r.verify(ci, receipt, 'o/r', 8, HEAD, h.document, EVALUATOR)
        forged = copy.deepcopy(receipt); forged['payload']['status'] = 'fail'; forged['sha256'] = r.digest(forged['payload'])
        with self.assertRaisesRegex(r.Refused, 'stale_receipt'): h.verify(forged)

    def test_tampered_unknown_version_and_artifact_fields_refuse(self):
        h = Harness(); receipt = h.produce()
        for value in [dict(receipt, artifact='self-approved'), dict(receipt, sha256='0' * 64),
                      dict(payload=dict(receipt['payload'], version=2), sha256=receipt['sha256'])]:
            with self.subTest(value=value), self.assertRaises(r.Refused): h.verify(value)

    def test_head_base_policy_and_source_changes_invalidate_receipt(self):
        for change in ['head', 'base', 'policy', 'source', 'inventory']:
            h = Harness(); receipt = h.produce()
            if change == 'head': h.pull['head']['sha'] = 'e' * 40
            if change == 'base': h.pull['base']['sha'] = 'e' * 40
            if change == 'policy': h.config['qualification_blockers'] = ['new blocker']
            if change == 'source':
                original = h.document
                h.document = lambda: dict(original(), source_commit='f' * 40)
            if change == 'inventory': h.changes[0]['sha'] = 'e' * 40
            with self.subTest(change=change), self.assertRaises(r.Refused): h.verify(receipt)

    def test_rerun_in_progress_or_new_attempt_invalidates_old_ready(self):
        for change in ['gate', 'producer']:
            h = Harness(); receipt = h.produce()
            if change == 'gate': h.run.update(run_attempt=3, status='in_progress', conclusion=None)
            else: h.producer.update(run_attempt=2, status='in_progress', conclusion=None)
            with self.subTest(change=change), self.assertRaises(r.Refused): h.verify(receipt)

    def test_changes_during_second_snapshot_refuse(self):
        for change in ['base', 'attempt', 'policy']:
            h = Harness(); reads = 0
            def mutate():
                nonlocal reads
                reads += 1
                if reads == 2:
                    if change == 'base': h.pull['base']['sha'] = 'e' * 40
                    if change == 'attempt': h.run['run_attempt'] = 3
                    if change == 'policy': h.config['qualification_blockers'] = ['new blocker']
            h.snapshot_hook = mutate
            with self.subTest(change=change), self.assertRaises(r.Refused): h.produce()

    def test_api_failure_is_not_empty_eligible_queue(self):
        h = Harness()
        with patch.object(ci, 'api', side_effect=RuntimeError('API unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'API unavailable'):
                r.produce(ci, 'o/r', 8, HEAD, h.document, EVALUATOR, 90)

    def test_wrong_consumer_selected_identity_refuses(self):
        h = Harness(); receipt = h.produce()
        with self.assertRaisesRegex(r.Refused, 'receipt_identity'):
            r.verify(ci, receipt, 'o/r', 999, HEAD, h.document, EVALUATOR)

    def test_assertion_logs_accept_github_timestamp_not_echoed_shell_or_artifact(self):
        record = dict(check='example')
        text = '2026-10-24T02:00:00.0000000Z ' + r.ASSERTION_MARKER + json.dumps(record)
        self.assertEqual(r.records(text, r.ASSERTION_MARKER), [record])
        self.assertEqual(r.records('echo "' + r.ASSERTION_MARKER + '{}"', r.ASSERTION_MARKER), [])


class DistributionTests(unittest.TestCase):
    def test_action_publishes_read_only_receipt_without_lifecycle_call(self):
        import contextlib
        import io
        h = Harness(); receipt = h.produce()
        env = dict(AW_ACTION_REF=EVALUATOR, GITHUB_REPOSITORY='o/r', INPUT_OPERATION='readiness',
                   INPUT_PR='8', INPUT_HEAD=HEAD, GITHUB_RUN_ID='90')
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / 'event.json'
            event.write_text(json.dumps(dict(workflow_run=dict(path=GATE))))
            env['GITHUB_EVENT_PATH'] = str(event)
            output = io.StringIO()
            with patch.dict(action.os.environ, env, clear=True), patch.object(action, 'controller', return_value=ci), \
                    patch.object(action, 'ci_module', return_value=r), \
                    patch.object(action, 'trusted_policy', return_value=h.document()), \
                    patch.object(r, 'produce', return_value=receipt) as produce, \
                    patch.object(ci, 'main') as main, contextlib.redirect_stdout(output):
                self.assertEqual(action.main(), 0)
                main.assert_not_called()
                self.assertEqual(produce.call_args.args[-1], 90)
            self.assertEqual(r.records(output.getvalue(), r.RECEIPT_MARKER), [receipt])

    def test_action_refuses_self_or_unconfigured_wake_before_production(self):
        h = Harness()
        for path in [PRODUCER, '.github/workflows/unconfigured.yml']:
            with tempfile.TemporaryDirectory() as tmp:
                event = Path(tmp) / 'event.json'; event.write_text(json.dumps(dict(workflow_run=dict(path=path))))
                env = dict(AW_ACTION_REF=EVALUATOR, GITHUB_REPOSITORY='o/r', INPUT_OPERATION='readiness',
                           INPUT_PR='8', INPUT_HEAD=HEAD, GITHUB_RUN_ID='90', GITHUB_EVENT_PATH=str(event))
                with patch.dict(action.os.environ, env, clear=True), patch.object(action, 'controller', return_value=ci), \
                        patch.object(action, 'ci_module', return_value=r), \
                        patch.object(action, 'trusted_policy', return_value=h.document()), \
                        patch.object(r, 'produce') as produce:
                    with self.assertRaisesRegex(RuntimeError, 'wake'): action.main()
                    produce.assert_not_called()

    def test_verify_action_returns_distinct_ready_wait_fail_without_mutation(self):
        import contextlib
        import io
        h = Harness(); receipt = h.produce()
        for state, code in [('ready', 0), ('wait', 3), ('fail', 2)]:
            value = copy.deepcopy(receipt); value['payload']['status'] = state
            env = dict(AW_ACTION_REF=EVALUATOR, GITHUB_REPOSITORY='o/r', INPUT_OPERATION='verify-readiness',
                       INPUT_PR='8', INPUT_HEAD=HEAD, INPUT_RECEIPT='/fixture/untrusted.json')
            with patch.dict(action.os.environ, env, clear=True), patch.object(action, 'controller', return_value=ci), \
                    patch.object(action, 'ci_module', return_value=r), patch.object(r, 'verify_file', return_value=value), \
                    patch.object(ci, 'main') as main, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(action.main(), code)
                main.assert_not_called()

    def test_policy_metadata_records_frozen_source_without_changing_legacy_bytes(self):
        h = Harness(); payload = json.dumps(h.config).encode()
        def api(path):
            if path == 'repos/o/r': return dict(default_branch='main')
            if '/commits/' in path: return dict(sha=POLICY_SOURCE)
            return dict(type='file', encoding='base64', content=base64.b64encode(payload).decode())
        with patch.object(ci, 'api', side_effect=api):
            self.assertEqual(action.trusted_policy(ci, 'o/r', '.github/policy.json'), payload)
            self.assertEqual(action.trusted_policy(ci, 'o/r', '.github/policy.json', metadata=True),
                             dict(config=h.config, source_commit=POLICY_SOURCE, path='.github/policy.json'))

    def test_duplicate_producer_receipt_refuses_even_with_valid_digest(self):
        h = Harness(); receipt = h.produce()
        h.logs[901] = '\n'.join([r.RECEIPT_MARKER + json.dumps(receipt)] * 2)
        with h.patches(), patch.object(r, 'job_log', side_effect=lambda repo, job: h.logs[job]):
            with self.assertRaisesRegex(r.Refused, 'unpublished_receipt'):
                r.verify(ci, receipt, 'o/r', 8, HEAD, h.document, EVALUATOR)

    def test_producer_failure_or_cancelled_job_prevents_consumption(self):
        for target in ['run', 'job']:
            h = Harness(); receipt = h.produce()
            if target == 'run': h.producer['conclusion'] = 'failure'
            else: h.producer_job['conclusion'] = 'cancelled'
            with self.subTest(target=target), self.assertRaisesRegex(r.Refused, 'producer_not_success'):
                h.verify(receipt)

    def test_newest_untrusted_gate_does_not_fall_back_to_green(self):
        for change in [dict(workflow_id=999), dict(pull_requests=[]), dict(run_attempt=None)]:
            h = Harness(); h.run.update(change)
            result = h.produce()['payload']
            self.assertEqual(result['status'], 'fail')
            self.assertIn('untrusted_latest_run', [v['code'] for v in result['reasons']])

    def test_pr_description_change_invalidates_ready_receipt(self):
        h = Harness(); receipt = h.produce(); h.pull['body'] = 'References #7; partial scope changed'
        with self.assertRaises(r.Refused): h.verify(receipt)

    def test_oversized_logs_or_receipts_refuse(self):
        with self.assertRaisesRegex(r.Refused, 'log_limit'): r.records('x' * (r.MAX_LOG + 1), r.ASSERTION_MARKER)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'receipt.json'; path.write_bytes(b' ' * 262145)
            with self.assertRaisesRegex(r.Refused, 'receipt_limit'):
                r.verify_file(ci, path, 'o/r', 8, HEAD, lambda: {}, EVALUATOR)

    def test_schema_shape_matches_fixture_and_inventory_omits_patch_text(self):
        h = Harness(); h.changes[0]['patch'] = '@@ very large untrusted diff omitted @@'
        receipt = h.produce()
        self.assertNotIn('patch', receipt['payload']['inputs']['changed_files'][0])
        schema = json.loads((legacy.SKILLS / 'issue-ci/templates/review-readiness.schema.json').read_text())
        self.assertEqual(set(schema['required']), set(receipt))
        payload_schema = schema['properties']['payload']
        self.assertEqual(set(payload_schema['required']), set(receipt['payload']))
        self.assertEqual(set(payload_schema['properties']['inputs']['required']), set(receipt['payload']['inputs']))
        self.assertEqual(set(payload_schema['properties']['producer']['required']), set(receipt['payload']['producer']))

    def test_external_local_uses_context_is_not_guessed(self):
        h = Harness()
        h.source['faberline/aw', AW_ACTION] += '    - uses: ./.github/actions/unproven\n'
        h.config['review_readiness']['sources']['aw-action']['blob_sha'] = blob(h.source['faberline/aw', AW_ACTION])
        result = h.produce()['payload']
        self.assertEqual(result['status'], 'fail')
        self.assertIn('unsupported_local_dependency', [x['code'] for x in result['reasons']])

    def test_legacy_receipt_cli_new_verify_entry_does_not_require_done(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'missing-receipt.json')
            result = subprocess.run([sys.executable, '-B', str(legacy.SKILLS / 'issue-review/scripts/receipt.py'),
                'verify-readiness', 'o/r', '8', HEAD, EVALUATOR, missing], env={'PATH': ''},
                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('missing-receipt.json', result.stderr)
        self.assertNotIn('done', result.stderr)
        self.assertNotIn('gh', result.stderr)


class InputParsingTests(unittest.TestCase):
    def test_explicit_and_multiline_yaml_keys_are_rejected(self):
        for text in [
            'steps:\n  - ? uses\n    : owner/action@main\n',
            'steps:\n  - ? "uses"\n    : owner/action@main\n',
            'steps:\n  - ?\n      uses\n    : owner/action@main\n',
            'steps:\n  - "uses"\n    : owner/action@main\n',
            'steps:\n  - "us\\\n    es": owner/action@main\n',
        ]:
            with self.subTest(text=text), self.assertRaisesRegex(r.Refused, 'unsupported_source_syntax'):
                r.literal_uses(text)
            h = Harness(); h.source['o/r', GATE] = text
            h.config['qualification']['trusted_workflows'][GATE]['definition_sha'] = blob(text)
            result = h.produce()['payload']
            self.assertEqual(result['status'], 'fail')
            self.assertIn('unsupported_source_syntax', [v['code'] for v in result['reasons']])

    def test_duplicate_assertion_failed_or_passed_then_passed_is_not_overwritten(self):
        for earlier in ['failed', 'passed']:
            h = Harness()
            raw = json.dumps(h.assertion()).replace('"policy-assertion": "passed"',
                '"policy-assertion": "' + earlier + '", "policy-assertion": "passed"')
            h.logs[101] = r.ASSERTION_MARKER + raw
            with self.subTest(earlier=earlier):
                result = h.produce()['payload']
                self.assertEqual(result['status'], 'fail')
                self.assertIn('malformed_record', [v['code'] for v in result['reasons']])

    def test_duplicate_keys_at_arbitrary_depth_in_log_records_refuse(self):
        for raw in ['{"a":1,"a":1}', '{"a":[{"b":{"c":"failed","c":"passed"}}]}',
                    '{"assertions":{"a":"failed","\\u0061":"passed"}}']:
            for marker in [r.ASSERTION_MARKER, r.RECEIPT_MARKER]:
                with self.subTest(raw=raw, marker=marker), self.assertRaisesRegex(r.Refused, 'malformed_record'):
                    r.records(marker + raw, marker)

    def test_duplicate_keys_in_authenticated_producer_receipt_refuse(self):
        h = Harness(); receipt = h.produce()
        payload = r.canonical(receipt['payload'])
        for raw in [
            '{"payload":' + payload + ',"payload":' + payload + ',"sha256":' + json.dumps(receipt['sha256']) + '}',
            r.canonical(receipt).replace('"version":1', '"version":1,"version":1', 1),
        ]:
            h.logs[901] = r.RECEIPT_MARKER + raw
            with self.subTest(raw=raw), h.patches(), \
                    patch.object(r, 'job_log', side_effect=lambda repo, job: h.logs[job]):
                with self.assertRaisesRegex(r.Refused, 'malformed_record'):
                    r.verify(ci, receipt, 'o/r', 8, HEAD, h.document, EVALUATOR)

    def test_duplicate_receipt_file_refuses_before_verification_or_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'receipt.json'
            path.write_text('{"payload":{"version":1,"version":1},"sha256":"fake"}')
            with patch.object(r, 'verify', side_effect=AssertionError('duplicate reached verifier')) as verify, \
                    patch.object(ci, 'api') as api:
                with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                    r.verify_file(ci, path, 'o/r', 8, HEAD, lambda: {}, EVALUATOR)
                verify.assert_not_called()
                api.assert_not_called()

    def test_api_and_trusted_policy_json_also_reject_duplicate_keys(self):
        with patch.object(ci.subprocess, 'check_output', return_value='{"status":"failed","status":"passed"}'):
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'): ci.api('fixture')
        h = Harness()
        raw = json.dumps(h.config).replace('"lifecycle_mode": "failure-only"',
            '"lifecycle_mode": "full", "lifecycle_mode": "failure-only"')
        def api(path):
            if path == 'repos/o/r': return dict(default_branch='main')
            if '/commits/' in path: return dict(sha=POLICY_SOURCE)
            return dict(type='file', encoding='base64', content=base64.b64encode(raw.encode()).decode())
        with patch.object(ci, 'api', side_effect=api):
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                action.trusted_policy(ci, 'o/r', '.github/policy.json', metadata=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.json'; path.write_text(raw)
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'): ci.load_config(path)

    def test_duplicate_wake_event_refuses_before_production(self):
        h = Harness()
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / 'event.json'
            wake = json.dumps(dict(path=GATE))
            event.write_text('{"workflow_run":' + wake + ',"workflow_run":' + wake + '}')
            env = dict(AW_ACTION_REF=EVALUATOR, GITHUB_REPOSITORY='o/r', INPUT_OPERATION='readiness',
                       INPUT_PR='8', INPUT_HEAD=HEAD, GITHUB_RUN_ID='90', GITHUB_EVENT_PATH=str(event))
            with patch.dict(action.os.environ, env, clear=True), patch.object(action, 'controller', return_value=ci), \
                    patch.object(action, 'ci_module', return_value=r), \
                    patch.object(action, 'trusted_policy', return_value=h.document()), \
                    patch.object(r, 'produce', side_effect=AssertionError('duplicate reached producer')) as produce:
                with self.assertRaisesRegex(ValueError, 'duplicate JSON'): action.main()
                produce.assert_not_called()

    def test_flow_adjacent_explicit_split_key_is_not_hidden(self):
        text = 'steps:\n  - {? "us\\\n      es": owner/action@main}\n'
        # Independent parser observation, not proof that GitHub accepts the workflow.
        import yaml
        node = yaml.compose(text, Loader=yaml.BaseLoader)
        self.assertEqual(node.value[0][1].value[0].value[0][0].value, 'uses')
        with self.assertRaisesRegex(r.Refused, 'unsupported_source_syntax'):
            r.literal_uses(text)
        h = Harness(); h.source['o/r', GATE] = text
        h.config['qualification']['trusted_workflows'][GATE]['definition_sha'] = blob(text)
        result = h.produce()['payload']
        self.assertEqual(result['status'], 'fail')
        self.assertIn('unsupported_source_syntax', [v['code'] for v in result['reasons']])

    def test_ast_checks_flow_keys_and_ignores_block_scalar_script_text(self):
        for text in [
            'steps: [{? "us\\\n  es": owner/action@main}]\n',
            'steps:\n  - {"us\\\n      es": owner/action@main}\n',
            'steps: [{? uses: owner/action@main}]\n',
            'steps: [{uses: owner/action@main}]\n',
        ]:
            with self.subTest(text=text), self.assertRaises(r.Refused):
                r.literal_uses(text)
            h = Harness(); h.source['o/r', GATE] = text
            h.config['qualification']['trusted_workflows'][GATE]['definition_sha'] = blob(text)
            self.assertEqual(h.produce()['payload']['status'], 'fail')
        ref = 'owner/action@' + EVALUATOR
        text = 'steps: [{uses: "' + ref + '"}]\nscript: |\n  uses: owner/not-an-action@main\n'
        self.assertEqual(r.literal_uses(text), [ref])

    def test_parser_dependency_version_is_explicit_and_fail_closed(self):
        import sys
        from types import SimpleNamespace
        for module in [None, SimpleNamespace(__version__='6.0.2')]:
            with patch.dict(sys.modules, {'yaml': module}):
                with self.assertRaisesRegex(r.Refused, 'parser_dependency'):
                    r.literal_uses('name: example\n')

    def test_ast_refuses_duplicate_keys_tags_aliases_complex_keys_and_multiple_documents(self):
        for text in [
            'steps: [{uses: owner/action@' + EVALUATOR + ', uses: owner/action@main}]',
            'x: !!python/object/apply:os.system [echo-not-executed]',
            'x: &x {a: b}\ny: *x',
            '? [a, b]\n: c',
            'name: one\n---\nname: two',
        ]:
            with self.subTest(text=text), self.assertRaises(r.Refused):
                r.literal_uses(text)
        with self.assertRaises(r.Refused):
            r.literal_uses('x: ' + '[' * 70 + 'value' + ']' * 70)

    def test_readiness_parser_lock_and_action_runtime_are_scoped(self):
        folder = legacy.SKILLS / 'issue-ci/scripts'
        lock = (folder / 'readiness-requirements.txt').read_text()
        self.assertEqual(lock.count('PyYAML==6.0.3'), 1)
        hashes = [line.strip().split('sha256:', 1)[1].rstrip(' \\')
                  for line in lock.splitlines() if '--hash=sha256:' in line]
        self.assertEqual(len(hashes), 72)
        self.assertTrue(all(len(value) == 64 and set(value) <= set('0123456789abcdef') for value in hashes))
        self.assertIn('https://pypi.org/pypi/PyYAML/6.0.3/json', lock)
        source = legacy.SKILLS.parents[2] / '.github/actions/issue-ci/action.yml'
        # Parse as nodes, never construct objects or execute the action.
        import yaml
        action_data = yaml.compose(source.read_text(), Loader=yaml.BaseLoader)
        self.assertIsInstance(action_data, yaml.nodes.MappingNode)
        raw = source.read_text()
        self.assertIn("if: inputs.operation == 'readiness' || inputs.operation == 'verify-readiness'", raw)
        self.assertIn('--no-deps --only-binary=:all: --require-hashes --index-url https://pypi.org/simple', raw)
        self.assertNotIn('pip install --upgrade', raw)
