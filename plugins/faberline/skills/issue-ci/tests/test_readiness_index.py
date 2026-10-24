"""Offline producer/index/transport fixtures. No cloud job, product code or API."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_lifecycle as legacy
import test_readiness as phase1

ci = legacy.ci
index = legacy.load('index_contract', legacy.SKILLS / 'issue-ci/scripts/readiness_index.py')
r = index.r
emitter = legacy.load('assertion_emitter', legacy.SKILLS / 'issue-ci/scripts/assertion_record.py')
HEAD, BASE, EVALUATOR = phase1.HEAD, phase1.BASE, phase1.EVALUATOR


class Harness(phase1.Harness):
    def __init__(self, empty=False):
        super().__init__()
        self.pull['draft'] = False
        self.pull['base']['ref'] = 'main'
        self.pull['base']['repo'] = dict(full_name='o/r')
        self.producer['run_number'] = 90
        self.empty = empty
        self.history = [self.producer]
        self.inventory_hook = None
        self.inventory_reads = 0
        self.page_override = None

    def api(self, path, method='GET', data=None):
        if path.startswith('repos/o/r/pulls?state=open&'):
            self.inventory_reads += 1
            if self.inventory_hook: self.inventory_hook(self.inventory_reads)
            if self.page_override is not None: return copy.deepcopy(self.page_override)
            return [] if self.empty or '&page=2' in path else [copy.deepcopy(self.pull)]
        if '/actions/workflows/readiness.yml/runs?' in path:
            return dict(workflow_runs=copy.deepcopy(self.history))
        if '/actions/runs/' in path:
            for run in self.history:
                if path == 'repos/o/r/actions/runs/' + str(run['id']): return copy.deepcopy(run)
        return super().api(path, method, data)

    def produce_index(self):
        with self.patches(), patch.object(r, 'job_log', side_effect=lambda repo, job: self.logs[job]):
            value = index.produce(ci, 'o/r', self.document, EVALUATOR, 90)
        output = io.StringIO()
        with contextlib.redirect_stdout(output): index.emit(value)
        self.logs[901] = output.getvalue()
        return value

    def verify_index(self, value=None):
        with self.patches(), patch.object(r, 'job_log', side_effect=lambda repo, job: self.logs[job]) as log:
            result = index.verify(ci, value, 'o/r', self.document, EVALUATOR) if value is not None else index.discover(ci, 'o/r', self.document, EVALUATOR)
        return result, log


class IndexTests(unittest.TestCase):
    def test_producer_index_roundtrip_and_bounded_summary_are_read_only(self):
        h = Harness(); value = h.produce_index()
        verified, log = h.verify_index()
        self.assertEqual(verified, value)
        summary = index.summary(verified)
        self.assertEqual(summary['status'], 'ready')
        self.assertEqual(summary['counts'], dict(ready=1, wait=0, fail=0))
        self.assertEqual(summary['ready'][0]['head'], HEAD)
        self.assertEqual(h.writes, [])
        self.assertTrue(all(call.args[1] == 901 for call in log.call_args_list), 'index preflight must not reread test logs')

    def test_complete_empty_queue_is_authenticated_not_zero_check_success(self):
        h = Harness(empty=True); value = h.produce_index()
        self.assertEqual(value['payload']['coverage']['count'], 0)
        verified, _ = h.verify_index()
        self.assertEqual(index.summary(verified)['status'], 'no_ready')
        self.assertTrue(verified['payload']['producer_pins'])

    def test_missing_partial_or_duplicate_index_is_not_empty_queue(self):
        h = Harness(empty=True); value = h.produce_index()
        for log in ['', 'arbitrary PR artifact says []', h.logs[901] + h.logs[901]]:
            h.logs[901] = log
            with self.subTest(log=log), self.assertRaises(r.Refused): h.verify_index()
        h = Harness(); value = h.produce_index()
        forged = copy.deepcopy(value); forged['payload']['coverage']['complete'] = False
        forged['sha256'] = r.digest(forged['payload'])
        with self.assertRaisesRegex(r.Refused, 'index_coverage'): h.verify_index(forged)

    def test_hash_is_not_signature_and_fake_artifact_or_url_cannot_authorize(self):
        h = Harness(); value = h.produce_index()
        forged = copy.deepcopy(value)
        forged['payload']['entries'][0]['receipt']['payload']['status'] = 'wait'
        forged['sha256'] = r.digest(forged['payload'])
        with self.assertRaisesRegex(r.Refused, 'index_unpublished'): h.verify_index(forged)
        for extra in ['artifact', 'url']:
            with self.subTest(extra=extra), self.assertRaisesRegex(r.Refused, 'index_schema'):
                h.verify_index(dict(value, **{extra: 'https://untrusted.invalid/code'}))

    def test_queue_head_base_description_draft_new_or_closed_pr_invalidate(self):
        for change in ['head', 'base', 'description', 'draft', 'empty']:
            h = Harness(); value = h.produce_index()
            if change == 'head': h.pull['head']['sha'] = 'e' * 40
            if change == 'base': h.pull['base']['sha'] = 'e' * 40
            if change == 'description': h.pull['body'] = 'References #7; different slice'
            if change == 'draft': h.pull['draft'] = True
            if change == 'empty': h.empty = True
            with self.subTest(change=change), self.assertRaises(r.Refused): h.verify_index(value)

    def test_policy_content_source_evaluator_and_workflow_definition_invalidate(self):
        for change in ['policy', 'source', 'evaluator', 'definition']:
            h = Harness(); value = h.produce_index()
            if change == 'policy': h.config['qualification_blockers'] = ['coverage missing']
            if change == 'source':
                prior = h.document; h.document = lambda: dict(prior(), source_commit='f' * 40)
            if change == 'evaluator':
                value = copy.deepcopy(value); value['payload']['evaluator_sha'] = 'f' * 40; value['sha256'] = r.digest(value['payload'])
            if change == 'definition': h.config['qualification']['trusted_workflows'][phase1.GATE]['definition_sha'] = 'f' * 40
            with self.subTest(change=change), self.assertRaises(r.Refused): h.verify_index(value)

    def test_latest_gate_rerun_or_status_and_inventory_metadata_invalidate(self):
        for change in ['attempt', 'status', 'files']:
            h = Harness(); value = h.produce_index()
            if change == 'attempt': h.run['run_attempt'] = 3
            if change == 'status': h.run.update(status='in_progress', conclusion=None)
            if change == 'files': h.changes[0]['sha'] = 'e' * 40
            with self.subTest(change=change), self.assertRaisesRegex(r.Refused, 'stale_index'): h.verify_index(value)

    def test_newest_producer_pending_failure_or_attempt_never_falls_back(self):
        for change in ['new_pending', 'new_failed', 'attempt', 'foreign']:
            h = Harness(); value = h.produce_index()
            if change.startswith('new_'):
                h.history.append(dict(h.producer, id=91, run_number=91,
                    status='in_progress' if change == 'new_pending' else 'completed',
                    conclusion=None if change == 'new_pending' else 'failure'))
            if change == 'attempt': h.producer['run_attempt'] = 2
            if change == 'foreign': h.producer['head_repository'] = dict(full_name='fork/r')
            with self.subTest(change=change), self.assertRaises(r.Refused): h.verify_index(value)

    def test_empty_queue_also_requires_producer_source_and_exact_aw_pin(self):
        h = Harness(empty=True); value = h.produce_index()
        h.source['o/r', phase1.PRODUCER] += '\n# unreviewed producer\n'
        with self.assertRaises(r.Refused): h.verify_index(value)

    def test_limits_pagination_duplicate_and_foreign_candidates_fail_closed(self):
        h = Harness()
        h.page_override = [dict(h.pull, number=i) for i in range(1, index.MAX_PRS + 1)]
        with h.patches(), self.assertRaisesRegex(r.Refused, 'index_limit'): index.inventory(ci, 'o/r')
        h.page_override = [h.pull, h.pull]
        with h.patches(), self.assertRaisesRegex(r.Refused, 'index_limit'): index.inventory(ci, 'o/r')
        h = Harness(); h.pull['head']['repo']['full_name'] = 'fork/r'
        with self.assertRaisesRegex(r.Refused, 'index_inventory'): h.produce_index()

    def test_generation_or_verification_changes_are_not_partial_snapshot(self):
        h = Harness(); h.inventory_hook = lambda count: h.pull['head'].update(sha='e' * 40) if count == 2 else None
        with self.assertRaisesRegex(r.Refused, 'stale_index'): h.produce_index()
        h = Harness(); value = h.produce_index(); reads = 0
        def mutate():
            nonlocal reads
            reads += 1
            if reads == 1: h.run['run_attempt'] = 3
        h.snapshot_hook = mutate
        with self.assertRaisesRegex(r.Refused, 'stale_index'): h.verify_index(value)

    def test_qualified_failure_wait_and_blocker_are_distinct(self):
        for variant, expected in [('failure', 'no_ready'), ('pending', 'no_ready'), ('blocker', 'blocked')]:
            h = Harness()
            if variant == 'failure': h.job['conclusion'] = 'failure'
            if variant == 'pending': h.job.update(status='in_progress', conclusion=None)
            if variant == 'blocker': h.config['qualification_blockers'] = ['real coverage gap']
            value = h.produce_index(); h.verify_index(value)
            with self.subTest(variant=variant): self.assertEqual(index.summary(value)['status'], expected)

    def test_api_error_is_not_a_complete_empty_index(self):
        h = Harness(empty=True)
        with patch.object(ci, 'api', side_effect=RuntimeError('connection unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'connection unavailable'):
                index.discover(ci, 'o/r', h.document, EVALUATOR)

    def test_mechanical_key_survives_rerun_but_not_semantic_inputs(self):
        h = Harness(); receipt = h.produce_index()['payload']['entries'][0]['receipt']
        key = index.mechanical_key(receipt)
        rerun = copy.deepcopy(receipt); rerun['payload']['producer']['run_attempt'] = 3
        rerun['payload']['evidence'][0]['run_attempt'] = 4; rerun['sha256'] = r.digest(rerun['payload'])
        self.assertEqual(index.mechanical_key(rerun), key)
        for field in ['head', 'base', 'policy_sha256', 'evaluator_sha', 'pr_description_sha256']:
            changed = copy.deepcopy(receipt); changed['payload']['inputs'][field] = 'f' * len(changed['payload']['inputs'][field])
            changed['sha256'] = r.digest(changed['payload'])
            self.assertNotEqual(index.mechanical_key(changed), key)
        self.assertNotEqual(index.review_work_key(key, 'a' * 64), index.review_work_key(key, 'b' * 64))
        with self.assertRaisesRegex(r.Refused, 'review_context'): index.review_work_key(key, 'done')

    def test_oversized_index_refuses_instead_of_truncating(self):
        h = Harness(empty=True); value = h.produce_index()
        value['payload']['producer_pins'][0]['path'] = 'x' * index.MAX_INDEX_BYTES
        value['sha256'] = r.digest(value['payload'])
        with self.assertRaisesRegex(r.Refused, 'index_limit'): index.validate(value)


class AssertionV2Tests(unittest.TestCase):
    def test_v2_numeric_job_identity_comes_from_api_transport(self):
        h = Harness(); record = h.assertion(); record['version'] = 2; del record['job_id']
        h.logs[101] = r.ASSERTION_V2_MARKER + json.dumps(record)
        value = h.produce_index()
        self.assertEqual(value['payload']['entries'][0]['receipt']['payload']['status'], 'ready')
        self.assertEqual(value['payload']['entries'][0]['receipt']['payload']['evidence'][0]['jobs'][0]['id'], 101)

    def test_mixed_versions_version_marker_mismatch_and_spoofed_binding_refuse(self):
        for variant in ['mixed', 'mismatch', 'head', 'base', 'attempt', 'zero']:
            h = Harness(); record = h.assertion(); record['version'] = 2; del record['job_id']
            if variant == 'head': record['head'] = 'f' * 40
            if variant == 'base': record['base'] = 'f' * 40
            if variant == 'attempt': record['run_attempt'] = 1
            if variant == 'zero': record['assertions'] = {}
            log = (r.ASSERTION_MARKER if variant == 'mismatch' else r.ASSERTION_V2_MARKER) + json.dumps(record)
            h.logs[101] = log + ('\n' + h.logs[101] if variant == 'mixed' else '')
            with self.subTest(variant=variant):
                self.assertEqual(h.produce_index()['payload']['entries'][0]['receipt']['payload']['status'], 'fail')

    def event_env(self, path):
        path.write_text(json.dumps(dict(number=8, repository=dict(full_name='o/r'), pull_request=dict(
            head=dict(sha=HEAD), base=dict(sha=BASE, repo=dict(full_name='o/r'))))))
        return dict(GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(path), GITHUB_REPOSITORY='o/r',
                    GITHUB_RUN_ID='10', GITHUB_RUN_ATTEMPT='2')

    def test_enforcer_helper_measures_before_after_without_api_or_test_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.event_env(Path(tmp) / 'event.json')
            with patch.object(emitter.subprocess, 'check_output', side_effect=[HEAD+'\n', 'commit\n'] * 2) as git, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                started = emitter.execution_context(tmp, env)
                record = emitter.publish('docs-copy', ['section', 'links'], dict(section=True, links=True), tmp, started, env)
            self.assertEqual(record['version'], 2)
            self.assertNotIn('job_id', record)
            self.assertEqual(len(git.call_args_list), 4)
            self.assertTrue(all(call.args[0][0] == 'git' for call in git.call_args_list))
            self.assertTrue(output.getvalue().startswith(emitter.r.ASSERTION_V2_MARKER))

    def test_enforcer_zero_missing_failed_skip_or_drift_never_emits_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.event_env(Path(tmp) / 'event.json')
            started = dict(repo='o/r', pr=8, head=HEAD, base=BASE, run_id=10, run_attempt=2)
            for observed in [{}, {'case': False}, {'case': 'passed'}, {'case': True, 'extra': True}]:
                with patch.object(emitter.subprocess, 'check_output', side_effect=[HEAD+'\n', 'commit\n']), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(emitter.r.Refused): emitter.publish('docs', ['case'], observed, tmp, started, env)
                    self.assertEqual(output.getvalue(), '')
            with patch.object(emitter.subprocess, 'check_output', side_effect=['f'*40+'\n', 'commit\n']):
                with self.assertRaisesRegex(emitter.r.Refused, 'assertion_checkout'):
                    emitter.publish('docs', ['case'], dict(case=True), tmp, started, env)


class IndexDistributionTests(unittest.TestCase):
    def test_index_action_guard_refuses_self_foreign_nonterminal_and_wrong_event(self):
        action = phase1.action
        h = Harness()
        normal = dict(action='completed', workflow_run=dict(path=phase1.GATE, event='pull_request',
                      status='completed', head_repository=dict(full_name='o/r')))
        for change in ['self', 'foreign', 'pending', 'target']:
            event = copy.deepcopy(normal)
            if change == 'self': event['workflow_run']['path'] = phase1.PRODUCER
            if change == 'foreign': event['workflow_run']['head_repository']['full_name'] = 'fork/r'
            if change == 'pending': event['workflow_run']['status'] = 'in_progress'
            if change == 'target': event['workflow_run']['event'] = 'pull_request_target'
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'event.json'; path.write_text(json.dumps(event))
                env = dict(AW_ACTION_REF=EVALUATOR, GITHUB_REPOSITORY='o/r', INPUT_OPERATION='index',
                           GITHUB_RUN_ID='90', GITHUB_EVENT_PATH=str(path))
                with patch.dict(action.os.environ, env, clear=True), patch.object(action, 'controller', return_value=ci), \
                        patch.object(action, 'ci_module', return_value=index), \
                        patch.object(action, 'trusted_policy', return_value=h.document()), \
                        patch.object(index, 'produce') as produce:
                    with self.assertRaisesRegex(RuntimeError, 'index wake'): action.main()
                    produce.assert_not_called()

    def test_index_action_reconciles_queue_without_pr_input_and_verifies_summary(self):
        action = phase1.action
        h = Harness(empty=True); value = h.produce_index()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'event.json'; path.write_text(json.dumps(dict(action='completed',workflow_run=dict(
                path=phase1.GATE,event='pull_request',status='completed',head_repository=dict(full_name='o/r')))))
            env = dict(AW_ACTION_REF=EVALUATOR,GITHUB_REPOSITORY='o/r',INPUT_OPERATION='index',
                       GITHUB_RUN_ID='90',GITHUB_EVENT_PATH=str(path))
            with patch.dict(action.os.environ,env,clear=True), patch.object(action,'controller',return_value=ci), \
                    patch.object(action,'ci_module',return_value=index), patch.object(action,'trusted_policy',return_value=h.document()), \
                    patch.object(index,'produce',return_value=value) as produce, \
                    patch.object(index,'emit') as emit, patch.object(ci,'main') as lifecycle:
                self.assertEqual(action.main(),0); emit.assert_called_once_with(value); lifecycle.assert_not_called()
                self.assertEqual(produce.call_args.args[-1],90)
            env['INPUT_OPERATION']='verify-index'
            with patch.dict(action.os.environ,env,clear=True), patch.object(action,'controller',return_value=ci), \
                    patch.object(action,'ci_module',return_value=index), patch.object(index,'discover',return_value=value), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(action.main(),3)
            self.assertEqual(json.loads(out.getvalue())['status'],'no_ready')

    def test_strict_index_records_duplicate_keys_unknown_version_and_completed_flags_refuse(self):
        h=Harness(); value=h.produce_index()
        h.logs[901] += '\n' + index.MARKER + '{"payload":{"version":1,"version":1},"sha256":"fake"}'
        with self.assertRaisesRegex(r.Refused,'malformed_record'):h.verify_index()
        for variant in ['version','completed']:
            h=Harness(); value=h.produce_index(); bad=copy.deepcopy(value)
            if variant=='version':bad['payload']['version']=2
            else:bad['payload']['entries'][0]['completed_review']=True
            bad['sha256']=r.digest(bad['payload'])
            with self.subTest(variant=variant),self.assertRaises(r.Refused):h.verify_index(bad)

    def test_exact_inventory_cap_can_prove_end_and_labels_do_not_prove_qualification(self):
        h=Harness()
        rows=[dict(h.pull,number=n) for n in range(1,index.MAX_PRS+1)]
        with patch.object(ci,'api',side_effect=lambda path:[] if '&page=2' in path else rows):
            self.assertEqual(len(index.inventory(ci,'o/r')),index.MAX_PRS)
        value=h.produce_index();h.pull['labels']=[dict(name='done')]
        verified,_=h.verify_index(value)
        self.assertEqual(index.summary(verified)['counts']['ready'],1)
        self.assertNotIn('completed_review',verified['payload']['entries'][0])

    def test_schemas_and_templates_keep_consumer_unactivated(self):
        schema=json.loads((legacy.SKILLS/'issue-ci/templates/readiness-index.schema.json').read_text())
        h=Harness(); value=h.produce_index(); summary=index.summary(value)
        self.assertEqual(set(schema['$defs']['index']['required']),set(value))
        self.assertEqual(set(schema['$defs']['index']['properties']['payload']['required']),set(value['payload']))
        self.assertEqual(set(schema['$defs']['summary']['required']),set(summary))
        template=(legacy.SKILLS/'issue-ci/templates/readiness-index.yml.example').read_text()
        self.assertIn('REPLACE_WITH_REVIEWED_40_HEX_AW_SHA',template)
        self.assertNotIn('issues: write',template);self.assertNotIn('checks: write',template)
        source=(legacy.SKILLS.parents[2]/'.github/actions/issue-ci/action.yml').read_text()
        self.assertIn("if: inputs.operation == 'assertion-library'",source)
        self.assertIn("if: inputs.operation != 'assertion-library'",source)
        import yaml
        root=yaml.compose(source,Loader=yaml.BaseLoader)
        outputs=next(value for key,value in root.value if key.value=='outputs')
        self.assertIn('assertion-library',{key.value for key,value in outputs.value})

    def test_exact_named_receipt_transport_reuses_full_verifier(self):
        h=Harness();value=h.produce_index();entry=value['payload']['entries'][0]
        with h.patches(),patch.object(r,'job_log',side_effect=lambda repo,job:h.logs[job]):
            result=index.named_receipt(ci,'o/r',8,HEAD,entry['receipt']['sha256'],h.document,EVALUATOR)
            self.assertEqual(result,entry['receipt'])
            with self.assertRaisesRegex(r.Refused,'stale_index'):
                index.named_receipt(ci,'o/r',8,HEAD,'f'*64,h.document,EVALUATOR)
