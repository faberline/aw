"""Synthetic reproduction of pre-collection source absence; no model/API calls."""
import contextlib
import copy
import io
import unittest
from unittest.mock import patch
from test_claude_review import g, Harness, HEAD, BASE, EVALUATOR, result

PATHS=['crates/tape-operator/src/render.rs','crates/tape-operator/src/reconcile.rs']
OBSERVED_UNCERTAINTIES=[
    'render.rs and reconcile.rs were not supplied, so truthfulness of the two statements (no cluster I/O in render; reconcile connecting the ManagedService implementation to the shared controller) cannot be verified; source is needed before a pass.',
    "The diff shows three added lines, but the added text is two sentences wrapped across three lines; this matches 'three module rustdoc lines'.",
]


class PhaseContractTests(unittest.TestCase):
    def fixture(self):
        h=Harness(); h.plan['requirements'][0]['supporting_paths']=PATHS[:]
        h.supporting={(ref,path): ('synthetic source '+path+' at '+ref+'\n').encode()
                      for ref in (HEAD,BASE) for path in PATHS}
        return h

    def test_initial_gap_requests_sources_then_final_receives_exact_versions(self):
        h=self.fixture(); order=[]
        original=h.api
        def api(path,*args,**kwargs):
            if '/contents/' in path and any(p in path for p in PATHS): order.append('source')
            return original(path,*args,**kwargs)
        def planner(packet,policy):
            order.append('plan')
            self.assertEqual(packet['supporting_sources'],[])
            self.assertNotIn('source',order)
            # Correct representation of this phase: request evidence, not an
            # unsupported final decision from the intentionally partial packet.
            return copy.deepcopy(h.plan)
        def final(packet,policy):
            order.append('final')
            self.assertEqual(order.count('source'),4)
            self.assertEqual({(s['path'],s['commit']) for s in packet['supporting_sources']},
                             {(path,ref) for path in PATHS for ref in (HEAD,BASE)})
            g.validate_supporting_packet(packet)
            return result(packet['context'])
        with patch.object(h,'api',side_effect=api):
            receipt=g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,planner)
        self.assertEqual(receipt['payload']['result']['decision'],'pass')
        self.assertEqual(order[0],'plan'); self.assertLess(order.index('source'),order.index('final'))
        self.assertEqual(h.writes,[])

    def test_retained_failed_response_is_not_silently_suppressed(self):
        h=self.fixture(); h.plan['uncertainties']=OBSERVED_UNCERTAINTIES[:]
        with patch.object(g,'supporting_context') as collect, patch.object(g,'invoke_claude') as final, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(g.Refused,'context_plan_requires_clarification'):
                g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))
            collect.assert_not_called(); final.assert_not_called()
        # This proves no uncertainty suppression, NOT future model compliance.

    def test_genuine_contract_ambiguity_blocks_before_source_fetch(self):
        h=self.fixture(); h.issue['body']+=' Does limit mean per request or per client?'
        h.plan['uncertainties']=['The issue does not choose per-request versus per-client limit.']
        with patch.object(g,'supporting_context') as collect, patch.object(g,'invoke_claude') as final, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(g.Refused,'context_plan_requires_clarification'):
                g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))
            collect.assert_not_called(); final.assert_not_called()

    def test_missing_source_blocks_instead_of_final_pass(self):
        h=self.fixture(); del h.supporting[(HEAD,PATHS[0])]
        with patch.object(g,'invoke_claude') as final:
            with self.assertRaisesRegex(g.Refused,'supporting_source_missing'):
                g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))
            final.assert_not_called()

    def test_unsupported_external_parent_and_absolute_requests_block(self):
        for path in ('../outside.rs','/tmp/local.rs','https://example.com/source.rs'):
            h=self.fixture(); h.plan['requirements'][0]['supporting_paths']=[path]
            with self.subTest(path=path), patch.object(g,'invoke_claude') as final:
                with self.assertRaisesRegex(g.Refused,'context_plan_path_syntax'):
                    g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))
                final.assert_not_called()

    def test_unadopted_spec_request_is_not_promoted_to_requirement(self):
        h=self.fixture(); h.plan['adopted_specs']=[dict(path=PATHS[0],issue_quote=PATHS[0])]
        with patch.object(g,'invoke_claude') as final:
            with self.assertRaisesRegex(g.Refused,'context_plan_spec_quote_not_literal'):
                g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))
            final.assert_not_called()

    def test_contract_changes_during_planning_still_refuse(self):
        h=self.fixture()
        def planner(*a):
            h.issue['body']+=' New requirement after initial snapshot.'
            return copy.deepcopy(h.plan)
        with patch.object(g,'invoke_claude') as final:
            with self.assertRaisesRegex(g.Refused,'review_inputs_changed_during_planning'):
                g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,planner)
            final.assert_not_called()

    def test_supporting_source_drift_during_final_refuses_receipt(self):
        h=self.fixture()
        def final(packet,policy):
            h.supporting[(HEAD,PATHS[0])]+=b'changed during review\n'
            return result(packet['context'])
        with self.assertRaisesRegex(g.Refused,'review_inputs_changed'):
            g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))

    def test_final_insufficient_evidence_or_ambiguity_still_blocks(self):
        for status in ('insufficient_evidence','ambiguous'):
            h=self.fixture()
            def final(packet,policy):
                v=result(packet['context']); v.update(decision='block',fit='uncertain')
                v['requirement_results'][0]['status']=status
                return v
            receipt=g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))
            self.assertEqual(receipt['payload']['result']['decision'],'block')
            h.log=g.MARKER+g.r.canonical(receipt)
            with self.assertRaisesRegex(g.Refused,'review_not_pass'): h.verify()

    def test_final_cannot_claim_pass_with_insufficient_evidence(self):
        h=self.fixture()
        def final(packet,policy):
            v=result(packet['context']); v['requirement_results'][0]['status']='insufficient_evidence'
            return v
        with self.assertRaisesRegex(g.Refused,'result_issue_fit_inconsistent'):
            g.review(h,'o/r',1,HEAD,h.document,EVALUATOR,200,final,lambda *a:copy.deepcopy(h.plan))

    def test_phase_instructions_and_wire_schema_are_explicit(self):
        skill=g.trusted_skill()
        self.assertIn('## Planning phase',skill); self.assertIn('## Final review phase',skill)
        self.assertIn('not final review',g.PLAN_SYSTEM)
        self.assertIn('NOT an uncertainty',g.PLAN_SYSTEM)
        schema=g.plan_schema()['properties']
        self.assertIn('requested source cannot resolve',schema['uncertainties']['description'])
        self.assertIn('collection happens after planning',schema['requirements']['items']['properties']['supporting_paths']['description'])
        self.assertEqual(set(schema),{'version','requirements','adopted_specs','uncertainties'})

if __name__=='__main__': unittest.main()
