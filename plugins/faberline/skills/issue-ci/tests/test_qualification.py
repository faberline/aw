"""Affected offline contract/evidence tests; never dispatch a test job."""
import base64
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

import test_lifecycle as legacy

ci = legacy.ci
q = ci.qualification_module()
action = legacy.load('issue_ci_action', legacy.SKILLS / 'issue-ci/scripts/action.py')
HEAD, BASE, BLOB = 'a' * 40, 'b' * 40, 'd' * 40
WORKFLOW = '.github/workflows/pr.yml'


def config():
    return dict(lifecycle_mode='full', qualification_blockers=[],
                workflows=[dict(path=WORKFLOW, jobs=['policy', 'unit', 'functional', 'perf'])],
                qualification=dict(version=1,
                    checks={name: dict(workflow=WORKFLOW, job=job, kind=kind) for name, job, kind in
                            [('policy', 'policy', 'policy'), ('unit', 'unit', 'unit'),
                             ('functional', 'functional', 'functional_e2e'), ('perf', 'perf', 'performance')]},
                    trusted_workflows={WORKFLOW: dict(id=77, definition_sha=BLOB)},
                    always=['policy'], protected_paths=['scripts/ci/**', '.github/actions/**',
                                                       '.github/issue-ci-required-gates.json'],
                    rules=[dict(paths=['src/**'], require=['unit', 'functional', 'perf'], exempt={}),
                           dict(paths=['docs/**', '*.md'], require=[], exempt={
                               'unit': 'documentation has no runtime behavior',
                               'functional_e2e': 'documentation changes no deployed behavior',
                               'performance': 'documentation changes no workload behavior'})]))


def pr():
    return dict(number=8, state='open', body='Closes #7', changed_files=1,
                base=dict(sha=BASE), head=dict(sha=HEAD, ref='issue/7', repo={'full_name': 'o/r'}))


def files(name='src/code.rs'):
    return [dict(filename=name, status='modified')]


def scope(cfg=None, pull=None, changes=None, definitions=None):
    pull = pull or pr()
    if definitions is None:
        definitions = {(WORKFLOW, ref): dict(sha=BLOB) for ref in (pull['head']['sha'], pull['base']['sha'])}
    return q.scoped_config(cfg or config(), pull, changes or files(), definitions)


def evidence(cfg):
    return {w['path']: (dict(id=i, run_number=i, run_attempt=2, status='completed',
                            conclusion='success', html_url=f'https://github.com/o/r/actions/runs/{i}'),
                       [dict(name=name, status='completed', conclusion='success',
                             html_url=f'https://github.com/o/r/actions/runs/{i}/job/{j}')
                        for j, name in enumerate(w['jobs'])])
            for i, w in enumerate(cfg['workflows'], 1)}


class ApplicabilityTests(unittest.TestCase):
    def test_raw_policy_cannot_qualify_without_computed_applicability(self):
        cfg = config()
        self.assertEqual(ci.evaluate_policy(cfg, evidence(cfg))[0], 'pending')

    def test_runtime_requires_all_three_categories(self):
        scoped = scope()
        self.assertEqual(scoped['_qualification_context']['issues'], [])
        self.assertEqual(set(scoped['workflows'][0]['jobs']), {'policy', 'unit', 'functional', 'perf'})
        self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'done')

    def test_docs_exemptions_still_require_a_real_policy_check(self):
        scoped = scope(changes=files('docs/guide.md'))
        self.assertEqual(scoped['workflows'][0]['jobs'], ['policy'])
        self.assertEqual(ci.evaluate_policy(scoped, {})[0], 'pending')
        self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'done')
        cfg = config(); cfg['qualification']['always'] = []
        with self.assertRaises(RuntimeError): q.validate(cfg)

    def test_performance_exemption_is_explicit_and_per_path(self):
        cfg = config(); rule = cfg['qualification']['rules'][0]
        rule['require'].remove('perf')
        scoped = scope(cfg)
        self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'pending')
        rule['exempt']['performance'] = 'reviewed bounded path changes no workload cost'
        scoped = scope(cfg)
        self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'done')
        self.assertNotIn('perf', scoped['workflows'][0]['jobs'])

    def test_docs_exemption_does_not_leak_to_unmapped_code(self):
        pull = pr(); pull['changed_files'] = 2
        changes = files('docs/guide.md') + files('unknown/code.rs')
        scoped = scope(pull=pull, changes=changes)
        self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'pending')

    def test_overlap_unions_required_checks_and_required_wins(self):
        cfg = config()
        cfg['qualification']['rules'].append(dict(paths=['**'], require=[], exempt={
            'unit': 'broad reviewed exception', 'functional_e2e': 'broad reviewed exception',
            'performance': 'broad reviewed exception'}))
        scoped = scope(cfg)
        self.assertEqual(scoped['_qualification_context']['exemptions']['src/code.rs'], {})
        self.assertIn('perf', scoped['workflows'][0]['jobs'])

    def test_renamed_file_requires_old_and_new_path_coverage(self):
        change = dict(filename='docs/code.md', previous_filename='src/code.rs', status='renamed')
        scoped = scope(changes=[change])
        self.assertIn('perf', scoped['workflows'][0]['jobs'])
        change['previous_filename'] = 'unmapped/old.rs'
        scoped = scope(changes=[change])
        self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'pending')

    def test_incomplete_capped_duplicate_and_unknown_file_lists_never_pass(self):
        for count, changes in [(0, []), (2, files()), (3000, files()),
                               (2, files() + files()), (1, [dict(filename='src/a', status='mystery')]),
                               (1, [dict(filename='src/a', status='renamed')])]:
            pull = pr(); pull['changed_files'] = count
            scoped = q.scoped_config(config(), pull, changes,
                                    {(WORKFLOW, HEAD): dict(sha=BLOB), (WORKFLOW, BASE): dict(sha=BLOB)})
            with self.subTest(count=count, changes=changes):
                self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'pending')

    def test_gate_infrastructure_and_pr_self_approval_cannot_qualify(self):
        for path in ['.github/workflows/pr.yml', '.github/actions/gate/action.yml',
                     '.github/issue-ci-required-gates.json', 'scripts/ci/select.py']:
            cfg = config()
            cfg['qualification']['rules'].append(dict(paths=['**'], require=['unit', 'functional', 'perf'], exempt={}))
            scoped = scope(cfg, changes=files(path))
            self.assertEqual(ci.evaluate_policy(scoped, evidence(scoped))[0], 'pending')

    def test_safe_glob_boundaries(self):
        self.assertFalse(q.matches('src/deep/file.rs', 'src/*'))
        self.assertTrue(q.matches('src/deep/file.rs', 'src/**'))
        self.assertTrue(q.matches('README.md', '**/*.md'))
        for path in ['../config', '/tmp/config', 'a//b', 'a/./b', 'a\\b']:
            self.assertFalse(q.path_valid(path))

    def test_unknown_checks_missing_identity_and_self_supplied_context_refuse(self):
        variants = []
        cfg = config(); cfg['qualification']['rules'][0]['require'] = ['invented']; variants.append(cfg)
        cfg = config(); cfg['qualification']['trusted_workflows'] = {}; variants.append(cfg)
        cfg = config(); cfg['qualification']['version'] = True; variants.append(cfg)
        cfg = config(); cfg['qualification']['rules'][0]['exempt'] = {'performance': 'conflict'}; variants.append(cfg)
        cfg = config(); cfg['_qualification_context'] = {'issues': []}; variants.append(cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.json'
            for cfg in variants:
                path.write_text(json.dumps(cfg))
                with self.subTest(cfg=cfg), self.assertRaises(RuntimeError): ci.load_config(path)


class TrustedEvidenceTests(unittest.TestCase):
    def run_metadata(self):
        return dict(id=3, run_number=3, run_attempt=2, head_sha=HEAD, event='pull_request',
                    head_repository={'full_name': 'o/r'}, path=WORKFLOW, workflow_id=77,
                    pull_requests=[dict(number=8, head=dict(sha=HEAD), base=dict(sha=BASE))])

    def test_latest_attempt_only_never_uses_old_green_fallback(self):
        cfg = scope(); run = self.run_metadata()
        for change in [dict(workflow_id=999), dict(run_attempt=None), dict(pull_requests=[]),
                       dict(pull_requests=[dict(number=8, head=dict(sha=HEAD), base=dict(sha='c' * 40))]),
                       dict(pull_requests=[dict(number=9, head=dict(sha=HEAD), base=dict(sha=BASE))])]:
            bad = dict(run, id=4, run_number=4, **change)
            with patch.object(ci, 'pages', return_value=[run, bad]) as pages:
                self.assertEqual(ci.snapshot('o/r', HEAD, cfg), {})
                self.assertEqual(pages.call_count, 1)
        with patch.object(ci, 'pages', side_effect=[[run], []]) as pages:
            result = ci.snapshot('o/r', HEAD, cfg)
            self.assertEqual(result[WORKFLOW][0], run)
            self.assertIn('/runs/3/attempts/2/jobs', pages.call_args_list[1].args[0])

    def test_foreign_stale_or_wrong_event_never_becomes_evidence(self):
        run = self.run_metadata()
        for change in [dict(head_sha='f' * 40), dict(event='workflow_dispatch'),
                       dict(head_repository={'full_name': 'fork/r'})]:
            with patch.object(ci, 'pages', return_value=[dict(run, **change)]):
                self.assertEqual(ci.snapshot('o/r', HEAD, scope()), {})

    def test_head_and_base_workflow_blobs_must_both_be_reviewed(self):
        for ref in [HEAD, BASE]:
            definitions = {(WORKFLOW, HEAD): dict(sha=BLOB), (WORKFLOW, BASE): dict(sha=BLOB)}
            definitions[WORKFLOW, ref]['sha'] = 'e' * 40
            cfg = scope(definitions=definitions)
            with patch.object(ci, 'pages') as pages:
                self.assertEqual(ci.snapshot('o/r', HEAD, cfg), {})
                pages.assert_not_called()
            self.assertEqual(ci.evaluate_policy(cfg, evidence(cfg))[0], 'pending')

    def test_missing_skipped_cancelled_pending_and_failure(self):
        cfg = scope()
        self.assertEqual(ci.evaluate_policy(cfg, {})[0], 'pending')
        for value in ['skipped', 'cancelled', 'neutral', None]:
            snapshots = evidence(cfg); snapshots[WORKFLOW][1][0]['conclusion'] = value
            self.assertEqual(ci.evaluate_policy(cfg, snapshots)[0], 'pending')
        snapshots = evidence(cfg); snapshots[WORKFLOW][1].pop()
        self.assertEqual(ci.evaluate_policy(cfg, snapshots)[0], 'pending')
        snapshots = evidence(cfg); snapshots[WORKFLOW][0]['status'] = 'in_progress'
        self.assertEqual(ci.evaluate_policy(cfg, snapshots)[0], 'pending')
        snapshots = evidence(cfg); snapshots[WORKFLOW][1][0]['conclusion'] = 'failure'
        state, reasons, _ = ci.evaluate_policy(cfg, snapshots)
        self.assertEqual(state, 'todo'); self.assertIn(snapshots[WORKFLOW][1][0]['html_url'], reasons[0])

    def test_untrusted_workflow_only_contributes_verified_failures(self):
        definitions={(WORKFLOW,HEAD):dict(sha='e'*40),(WORKFLOW,BASE):dict(sha=BLOB)}
        cfg=scope(definitions=definitions);run=dict(self.run_metadata(),status='completed',
                                                   conclusion='success',html_url='https://example/run')
        job=dict(name='unit',status='completed',conclusion='success',html_url='https://example/job')
        for conclusion in ('success','skipped','cancelled'):
            with self.subTest(conclusion=conclusion),patch.object(ci,'pages',side_effect=[[run],[dict(job,conclusion=conclusion)]]):
                self.assertEqual(ci.untrusted_failure_snapshots('o/r',HEAD,cfg),{})
        with patch.object(ci,'pages',side_effect=[[run],[dict(job,conclusion='failure')]]):
            result=ci.untrusted_failure_snapshots('o/r',HEAD,cfg)
        self.assertEqual(result[WORKFLOW][1][0]['conclusion'],'failure')
        self.assertEqual(ci.evaluate_policy(cfg,{})[0],'pending')

    def test_untrusted_failure_must_bind_current_head_base_and_workflow(self):
        definitions={(WORKFLOW,HEAD):dict(sha='e'*40),(WORKFLOW,BASE):dict(sha=BLOB)}
        cfg=scope(definitions=definitions);run=dict(self.run_metadata(),status='completed',
                                                   conclusion='failure',html_url='https://example/run')
        for change in [dict(head_sha='f'*40),dict(workflow_id=99),dict(event='push'),
                       dict(head_repository={'full_name':'other/repo'}),
                       dict(pull_requests=run['pull_requests']*2),
                       dict(pull_requests=[dict(number=8,head=dict(sha=HEAD),base=dict(sha='c'*40))])]:
            with self.subTest(change=change),patch.object(ci,'pages',return_value=[dict(run,**change)]):
                self.assertEqual(ci.untrusted_failure_snapshots('o/r',HEAD,cfg),{})
        newer=dict(run,id=4,run_number=4,pull_requests=[])
        with patch.object(ci,'pages',return_value=[run,newer]):
            self.assertEqual(ci.untrusted_failure_snapshots('o/r',HEAD,cfg),{})
        newer=dict(run,id=4,run_number=4,run_attempt=None)
        with patch.object(ci,'pages',return_value=[run,newer]):
            self.assertEqual(ci.untrusted_failure_snapshots('o/r',HEAD,cfg),{})


class QualifiedHandoffTests(unittest.TestCase):
    def harness(self):
        h = legacy.ApplyTests(); h.setUp(); self.addCleanup(h.doCleanups)
        h.pr = pr(); h.config.write_text(json.dumps(config()))
        return h

    def test_complete_current_evidence_uses_existing_done_journal(self):
        h = self.harness()
        with patch.object(ci, 'qualify_config', side_effect=lambda repo, pull, cfg: scope(cfg, pull)):
            h.invoke(evidence(scope()))
        self.assertEqual({l['name'] for l in h.issue['labels']}, {'done', 'priority:p1'})

    def test_base_change_during_snapshot_refuses_label_mutation(self):
        h = self.harness(); reads = 0
        def snapshot(*args):
            nonlocal reads
            reads += 1
            if reads == 2: h.pr['base']['sha'] = 'c' * 40
            return evidence(scope())
        with patch.object(ci, 'qualify_config', side_effect=lambda repo, pull, cfg: scope(cfg, pull)):
            with self.assertRaisesRegex(RuntimeError, 'applicability changed'):
                h.invoke(snapshot_sequence=snapshot)
        self.assertTrue(all('/comments' in path for path, _, _ in h.writes))

    def test_base_or_issue_link_change_during_requalification_never_labels(self):
        for change in ['base', 'body']:
            h = self.harness(); reads = 0
            def qualify(repo, pull, cfg):
                nonlocal reads
                frozen = copy.deepcopy(pull)
                reads += 1
                if reads == 2:
                    if change == 'base': h.pr['base']['sha'] = 'c' * 40
                    else: h.pr['body'] = 'Closes #999'
                return scope(cfg, frozen)
            with self.subTest(change=change), patch.object(ci, 'qualify_config', side_effect=qualify):
                with self.assertRaises(RuntimeError): h.invoke(evidence(scope()))
            self.assertTrue(all('/comments' in path for path, _, _ in h.writes))

    def test_read_only_success_rechecks_head_base_and_latest_attempt(self):
        from argparse import Namespace
        for change in ['head', 'base', 'attempt']:
            h = self.harness(); reads = 0
            def qualify(repo, pull, cfg):
                nonlocal reads
                frozen = copy.deepcopy(pull); reads += 1
                if reads == 2 and change == 'base': h.pr['base']['sha'] = 'c' * 40
                if reads == 2 and change == 'head': h.pr['head']['sha'] = 'f' * 40
                return scope(cfg, frozen)
            first = evidence(scope()); second = copy.deepcopy(first)
            if change == 'attempt': second[WORKFLOW][0]['run_attempt'] = 3
            args = Namespace(repo='o/r', pr=8, head=HEAD, apply=False, run_id=None)
            with self.subTest(change=change), patch.object(ci, 'api', side_effect=h.api), \
                    patch.object(ci, 'qualify_config', side_effect=qualify), \
                    patch.object(ci, 'snapshot', side_effect=[first, second]):
                with self.assertRaises(RuntimeError): ci.process_head(args, config())
            self.assertEqual(h.writes, [])

    def test_same_head_new_base_cannot_reuse_success_receipt(self):
        h = self.harness()
        with patch.object(ci, 'qualify_config', side_effect=lambda repo, pull, cfg: scope(cfg, pull)):
            h.invoke(evidence(scope())); first = h.comments[-1]['body'].splitlines()[0]
            h.pr['base']['sha'] = 'c' * 40
            h.issue['labels'] = [{'name': 'doing'}]
            h.invoke(evidence(scope(pull=h.pr)))
        self.assertNotEqual(h.comments[-1]['body'].splitlines()[0], first)
        self.assertEqual(h.issue['labels'], [{'name': 'done'}])

    def test_base_change_does_not_undo_agy_failure_reclaim(self):
        h = self.harness(); failed = evidence(scope()); failed[WORKFLOW][1][0]['conclusion'] = 'failure'
        with patch.object(ci, 'qualify_config', side_effect=lambda repo, pull, cfg: scope(cfg, pull)):
            h.invoke(failed)
            h.writes.clear(); h.issue['labels'] = [{'name': 'doing'}]
            h.pr['base']['sha'] = 'c' * 40
            h.invoke(failed)
        self.assertEqual(h.writes, [])

    def test_unknown_applicability_never_writes_done(self):
        h = self.harness()
        with patch.object(ci, 'qualify_config', return_value=scope(changes=files('unknown/path'))):
            h.invoke(evidence(scope()))
        self.assertEqual(h.writes, [])

    def test_untrusted_failure_returns_todo_without_ever_qualifying_success(self):
        h=self.harness(); definitions={(WORKFLOW,HEAD):dict(sha='e'*40),(WORKFLOW,BASE):dict(sha=BLOB)}
        cfg=scope(definitions=definitions)
        run=dict(id=1,run_attempt=1,status='completed',conclusion='failure',html_url='https://example/run')
        failed={WORKFLOW:(run,[dict(name='unit',status='completed',conclusion='failure',html_url='https://example/job')])}
        args=ci.argparse.Namespace(repo='o/r',pr=8,head=HEAD,apply=True,run_id=None)
        with patch.object(ci,'api',side_effect=h.api),patch.object(ci,'pages',return_value=[]), \
             patch.object(ci,'qualify_config',return_value=cfg),patch.object(ci,'snapshot',return_value={}), \
             patch.object(ci,'untrusted_failure_snapshots',return_value=failed),contextlib.redirect_stdout(io.StringIO()):
            ci.process_head(args,config(),check_event=False)
        self.assertEqual({x['name'] for x in h.issue['labels']},{'todo','priority:p1'})
        self.assertEqual(ci.evaluate_policy(cfg,{})[0],'pending')

    def test_changed_untrusted_failure_never_changes_issue_labels(self):
        h=self.harness(); definitions={(WORKFLOW,HEAD):dict(sha='e'*40),(WORKFLOW,BASE):dict(sha=BLOB)}
        cfg=scope(definitions=definitions)
        run=dict(id=1,run_attempt=1,status='completed',conclusion='failure',html_url='https://example/run')
        failed={WORKFLOW:(run,[dict(name='unit',status='completed',conclusion='failure',html_url='https://example/job')])}
        args=ci.argparse.Namespace(repo='o/r',pr=8,head=HEAD,apply=True,run_id=None)
        with patch.object(ci,'api',side_effect=h.api),patch.object(ci,'pages',return_value=[]), \
             patch.object(ci,'qualify_config',return_value=cfg),patch.object(ci,'snapshot',return_value={}), \
             patch.object(ci,'untrusted_failure_snapshots',side_effect=[failed,{}]),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'untrusted CI failure changed'):
                ci.process_head(args,config(),check_event=False)
        self.assertEqual({x['name'] for x in h.issue['labels']},{'doing','priority:p1'})
        self.assertTrue(all('/comments' in path for path,_,_ in h.writes))


class DistributionTests(unittest.TestCase):
    def test_shared_full_cannot_fall_back_to_legacy_or_implicit_policy(self):
        cfg = config(); cfg.pop('qualification')
        with self.assertRaises(RuntimeError): ci.validate_adoption(cfg)
        cfg['lifecycle_mode'] = 'failure-only'
        self.assertEqual(ci.validate_adoption(cfg), cfg)
        cfg.pop('qualification_blockers')
        with self.assertRaises(RuntimeError): ci.validate_adoption(cfg)
        cfg = config(); cfg.pop('lifecycle_mode')
        with self.assertRaises(RuntimeError): ci.validate_adoption(cfg)

    def test_mutable_action_refs_are_refused_before_any_api(self):
        for ref in ['', 'main', 'v1', HEAD[:12]]:
            with patch.dict(action.os.environ, {'AW_ACTION_REF': ref}, clear=True), patch.object(action, 'controller') as load:
                with self.assertRaises(RuntimeError): action.main()
                load.assert_not_called()

    def test_validation_is_not_qualification_and_never_invokes_handoff(self):
        with patch.dict(action.os.environ, {'AW_ACTION_REF': HEAD, 'GITHUB_REPOSITORY': 'o/r',
                                         'INPUT_OPERATION': 'validate'}, clear=True), \
                patch.object(action, 'controller', return_value=ci), \
                patch.object(action, 'trusted_policy', return_value=json.dumps(config()).encode()), \
                patch.object(ci, 'main') as main:
            self.assertEqual(action.main(), 0)
            main.assert_not_called()

    def test_qualify_is_read_only_and_handoff_uses_serialized_reconcile(self):
        for operation in ['qualify', 'handoff']:
            env = dict(AW_ACTION_REF=HEAD, GITHUB_REPOSITORY='o/r', INPUT_OPERATION=operation,
                       INPUT_PR='8', INPUT_HEAD=HEAD, INPUT_RUN_ID='123')
            with patch.dict(action.os.environ, env, clear=True), \
                    patch.object(action, 'controller', return_value=ci), \
                    patch.object(action, 'trusted_policy', return_value=json.dumps(config()).encode()), \
                    patch.object(ci, 'main', side_effect=lambda: list(sys.argv)):
                args = action.main()
            self.assertNotIn('--apply', args)
            self.assertEqual('--reconcile' in args, operation == 'handoff')
            self.assertEqual('--pr' in args, operation == 'qualify')

    def test_policy_is_read_from_frozen_default_branch_not_pr_checkout(self):
        payload = json.dumps(config()).encode()
        calls = []
        def api(path):
            calls.append(path)
            if path == 'repos/o/r': return dict(default_branch='main')
            if '/commits/' in path: return dict(sha=BASE)
            return dict(type='file', encoding='base64', content=base64.b64encode(payload).decode())
        with patch.object(ci, 'api', side_effect=api):
            self.assertEqual(action.trusted_policy(ci, 'o/r', '.github/policy.json'), payload)
        self.assertEqual(calls[-1], 'repos/o/r/contents/.github/policy.json?ref=' + BASE)
        for path in ['../policy.json', '/tmp/policy.json', '.github/../policy.json']:
            with self.assertRaises(RuntimeError): action.trusted_policy(ci, 'o/r', path)

    def test_pr_artifact_or_nonfile_policy_is_refused(self):
        with patch.object(ci, 'api', side_effect=[dict(default_branch='main'), dict(sha=BASE),
                                                 dict(type='file', encoding='artifact', content='green')]):
            with self.assertRaises(RuntimeError): action.trusted_policy(ci, 'o/r', '.github/policy.json')
