"""Whole output-contract audit: deterministic generation, no CLI/API/network.

Standard JSON Schema validation is an offline oracle, not proof of enforcement
by Claude Code. Cross-field semantic/provenance exceptions are named below.
"""
import contextlib
import copy
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_claude_review import g, Harness, HEAD, BASE, result, SKILL_BYTES, git_blob
try:
    import jsonschema
except ImportError:
    jsonschema=None


def fixture(files=1,requirements=1):
    h=Harness()
    h.files=[dict(h.files[0],filename='src/file'+str(i)+'.py') for i in range(files)]
    h.pull['changed_files']=files
    h.plan['requirements']=[dict(h.plan['requirements'][0],id='r'+str(i+1)) for i in range(requirements)]
    context,_=h.collect(True)
    return h,context


def field_paths(schema,prefix=''):
    result=set()
    for key,node in schema.get('properties',{}).items():
        path=prefix+'.'+key if prefix else key
        result.add(path); result.update(field_paths(node,path))
    if isinstance(schema.get('items'),dict): result.update(field_paths(schema['items'],prefix+'[]'))
    return result


@unittest.skipIf(jsonschema is None,'existing optional jsonschema unavailable; complete contract audit not run')
class OutputContractAuditTests(unittest.TestCase):
    def schema_accepts(self,value,context):
        schema=g.result_schema(context)
        for cls in (jsonschema.Draft7Validator,jsonschema.Draft202012Validator):
            cls.check_schema(schema)
            if not cls(schema).is_valid(value): return False
        return True
    def parity(self,value,context,accepted):
        self.assertEqual(self.schema_accepts(value,context),accepted)
        try: actual=g.validate_result(value,context); runtime=True
        except g.Refused: actual=None; runtime=False
        self.assertEqual(runtime,accepted)
        return actual
    def residual(self,value,context,code):
        self.assertTrue(self.schema_accepts(value,context))
        with self.assertRaisesRegex(g.Refused,'^'+code+'$'):g.validate_result(value,context)
    def test_every_wire_field_is_in_the_audit_inventory(self):
        expected_final={'version','repo','pr','head','base','decision','fit','summary','reviewed_files',
            'contract_uncertainties','requirement_plan_sha256','requirement_results','extra_changes',
            'requirement_results[].id','requirement_results[].status','requirement_results[].detail',
            'requirement_results[].source_ids','requirement_results[].locations',
            *{'requirement_results[].locations[].'+k for k in ('path','side','start','end','reason')},
            *{'extra_changes[].'+k for k in ('path','side','start','end','reason')}}
        expected_plan={'version','requirements','adopted_specs','uncertainties',
            *{'requirements[].'+k for k in ('id','requirement','issue_quote','supporting_paths')},
            'adopted_specs[].path','adopted_specs[].issue_quote'}
        _,c=fixture()
        self.assertEqual(field_paths(g.result_schema(c)),expected_final)
        self.assertEqual(field_paths(g.plan_schema()),expected_plan)
        doc=(Path(__file__).parents[1]/'OUTPUT-CONTRACT-AUDIT.md').read_text()
        for name in expected_final|expected_plan:self.assertIn('`'+name+'`',doc)
    def test_reviewed_files_exact_enum_cardinality_and_uniqueness(self):
        for n in (1,2,4,80):
            _,c=fixture(n);v=result(c);self.parity(v,c,True)
            candidates=[[],v['reviewed_files'][:-1],v['reviewed_files']+['src/support.py'],
                        ['src/support.py']+v['reviewed_files'][1:],'not-an-array',[{}]*n]
            if n>1:candidates.append([v['reviewed_files'][0]]*n)
            for files in candidates:
                with self.subTest(count=n,files_type=type(files).__name__):
                    bad=copy.deepcopy(v);bad['reviewed_files']=files;self.parity(bad,c,False)
            reordered=copy.deepcopy(v);reordered['reviewed_files'].reverse();self.parity(reordered,c,True)
    def test_exact_requirement_cardinality_and_whole_item_duplicates(self):
        for n in (1,2,4,16):
            _,c=fixture(requirements=n);v=result(c);self.parity(v,c,True)
            for count in (0,n-1,n+1):
                bad=copy.deepcopy(v);bad['requirement_results']=(v['requirement_results']*(n+2))[:count]
                self.parity(bad,c,False)
            if n>1:
                bad=copy.deepcopy(v);bad['requirement_results']=[copy.deepcopy(v['requirement_results'][0])]*n
                self.parity(bad,c,False)
    def test_all_required_shapes_and_identity_constants(self):
        _,c=fixture();base=result(c)
        for key in base:
            bad=copy.deepcopy(base);del bad[key];self.parity(bad,c,False)
        for key,wrong in [('version',2),('repo','other/repo'),('pr',2),('head','f'*40),('base','f'*40),
                          ('requirement_plan_sha256','f'*64),('decision','PASS'),('fit','EXACT')]:
            bad=copy.deepcopy(base);bad[key]=wrong;self.parity(bad,c,False)
        for target in ('root','claim','location'):
            for mode in ('extra','missing'):
                bad=copy.deepcopy(base)
                obj=bad if target=='root' else bad['requirement_results'][0] if target=='claim' else bad['extra_changes']
                if target=='location':obj=bad['requirement_results'][0]['locations'][0]
                if mode=='extra':obj['private-sentinel']='never-log'
                else:del obj[next(iter(obj))]
                self.parity(bad,c,False)
    def test_final_text_contract_matches_unicode_and_length(self):
        _,c=fixture()
        for value,valid in [('x',True),('x'*4000,True),('x'*4001,False),('',False),(' \t\n',False),
                            ('\u0085',False),('\ufeff',False),('\u200b',True),('\U0001f600'*4000,True),(None,False)]:
            for field in ('summary','detail','reason','uncertainty'):
                v=result(c)
                if field=='summary':v['summary']=value
                elif field=='detail':v['requirement_results'][0]['detail']=value
                elif field=='reason':v['requirement_results'][0]['locations'][0]['reason']=value
                else:v.update(contract_uncertainties=[value],decision='block',fit='uncertain')
                with self.subTest(field=field,length=len(value) if isinstance(value,str) else None):self.parity(v,c,valid)
    def test_json_mathematical_integers_canonicalize_without_value_changes(self):
        _,c=fixture()
        v=result(c);v['version']=1.0;v['pr']=float(v['pr'])
        for loc in v['requirement_results'][0]['locations']:loc.update(start=float(loc['start']),end=float(loc['end']))
        actual=self.parity(v,c,True)
        self.assertIs(type(actual['version']),int);self.assertIs(type(actual['pr']),int)
        for loc in actual['requirement_results'][0]['locations']:self.assertIs(type(loc['start']),int)
        for field in ('version','pr','start','end'):
            for value in (True,False,1.5,'1',None,float('inf'),float('nan')):
                bad=result(c)
                if field in ('version','pr'):bad[field]=value
                else:bad['requirement_results'][0]['locations'][0][field]=value
                with self.assertRaises(g.Refused):g.validate_result(bad,c)
    def test_context_location_enums_bounds_and_empty_context(self):
        _,c=fixture();v=result(c)
        for field,value in [('path','supporting/only.py'),('side','file'),('start',-1),('end',999999),('reason',' '),('start',True)]:
            bad=copy.deepcopy(v);bad['requirement_results'][0]['locations'][0][field]=value;self.parity(bad,c,False)
        empty=copy.deepcopy(c);empty['changed_ranges']=[]
        blocked=result(empty);blocked.update(decision='block',fit='uncertain')
        blocked['requirement_results'][0].update(status='insufficient_evidence',locations=[])
        self.parity(blocked,empty,True)
        bad=copy.deepcopy(blocked);bad['extra_changes']=v['requirement_results'][0]['locations'];self.parity(bad,empty,False)
    def test_source_id_membership_count_uniqueness_and_no_sources(self):
        h,c=fixture();v=result(c)
        for ids in ([],v['requirement_results'][0]['source_ids'][:1],['f'*64]*2,
                    [v['requirement_results'][0]['source_ids'][0]]*2,['src/support.py']*2):
            bad=copy.deepcopy(v);bad['requirement_results'][0]['source_ids']=ids;self.parity(bad,c,False)
        h.plan['requirements'][0]['supporting_paths']=[];empty,_=h.collect(True)
        self.parity(result(empty),empty,True)
        bad=result(empty);bad['requirement_results'][0]['source_ids']=['f'*64];self.parity(bad,empty,False)
    def test_deterministic_generated_valid_examples_cover_decisions_and_counts(self):
        rng=random.Random(104729);count=0
        for files,claims in itertools.product((1,2,4),(1,2,3,4)):
            _,c=fixture(files,claims)
            for _ in range(12):
                v=copy.deepcopy(result(c));statuses=[]
                for claim in v['requirement_results']:
                    claim['status']=rng.choice(('met','missing','ambiguous','insufficient_evidence'));statuses.append(claim['status'])
                    rng.shuffle(claim['locations']);rng.shuffle(claim['source_ids'])
                if rng.choice((True,False)):v['extra_changes']=copy.deepcopy(c['changed_ranges']);v['extra_changes']=[dict(x,reason='Explicit extra scope finding') for x in v['extra_changes']]
                if rng.randrange(5)==0:v['contract_uncertainties']=['Explicit contract ambiguity']
                uncertain=bool(v['contract_uncertainties']) or any(x in ('ambiguous','insufficient_evidence') for x in statuses)
                missing='missing' in statuses;extra=bool(v['extra_changes'])
                v['fit']='uncertain' if uncertain else 'missing_and_extra' if missing and extra else 'missing' if missing else 'extra' if extra else 'exact'
                v['decision']='block' if uncertain else 'fail' if missing or extra else 'pass'
                rng.shuffle(v['reviewed_files']);rng.shuffle(v['requirement_results'])
                self.parity(v,c,True);count+=1
        self.assertEqual(count,144)
    def test_known_cross_field_residuals_remain_fail_closed(self):
        _,c=fixture(requirements=2);v=result(c)
        v['requirement_results'][1]['id']='r1';v['requirement_results'][1]['detail']='Different object, same ID'
        self.residual(v,c,'result_claim_id_duplicate')
        _,c=fixture();v=result(c);v['requirement_results'][0]['locations']=[]
        self.residual(v,c,'result_requirement_mapping')
        v=result(c);v['decision']='fail';self.residual(v,c,'result_issue_fit_inconsistent')
        v=result(c);v['fit']='missing';self.residual(v,c,'result_issue_fit_inconsistent')
        v=result(c);v['contract_uncertainties']=['Actual issue ambiguity'];self.residual(v,c,'result_issue_fit_inconsistent')
        v=result(c);v['requirement_results'][0]['locations']=v['requirement_results'][0]['locations'][:1]
        self.residual(v,c,'result_diff_unmapped')
        h,c=fixture();h.code=b'new\n'*3;h.before_code=b'old\n'*3;h.files[0]['sha']=git_blob(h.code);c,_=h.collect(True)
        v=result(c);v['requirement_results'][0]['locations'][0].update(start=3,end=1)
        self.residual(v,c,'result_location_not_changed')
    def test_known_per_requirement_source_association_residual(self):
        h=Harness();h.plan['requirements'].append(dict(h.plan['requirements'][0],id='r2',supporting_paths=['src/other.py']))
        h.supporting.update({(ref,'src/other.py'):b'synthetic other source\n' for ref in (HEAD,BASE)})
        c,_=h.collect(True);v=result(c)
        v['requirement_results'][0]['source_ids']=v['requirement_results'][1]['source_ids'][:]
        self.residual(v,c,'result_evidence_sources_association')
        h.plan['requirements'].append(dict(h.plan['requirements'][0],id='r3',supporting_paths=[]))
        c,_=h.collect(True);v=result(c);v['requirement_results'][0]['source_ids']=[]
        self.residual(v,c,'result_evidence_sources_count')
    def test_aggregate_byte_limit_is_an_explicit_runtime_residual(self):
        _,c=fixture();v=result(c)
        locs=v['requirement_results'][0]['locations']*10
        v['requirement_results'][0]['locations']=[dict(x,reason='x'*4000) for x in locs]
        self.residual(v,c,'result_limit')
    def test_schema_keyword_inventory_has_no_unreviewed_conditionals(self):
        allowed={'type','properties','required','additionalProperties','items','minItems','maxItems','uniqueItems',
                 'enum','const','minLength','maxLength','pattern','minimum','maximum','description'}
        def visit(node):
            self.assertLessEqual(set(node),allowed)
            for child in node.get('properties',{}).values():visit(child)
            if isinstance(node.get('items'),dict):visit(node['items'])
        _,c=fixture();visit(g.plan_schema());visit(g.result_schema(c))
    def test_schema_argument_reserve_covers_maximum_planner_metadata(self):
        _,initial=fixture(4);initial.update(evidence_claims=[],supporting_sources=[],requirement_plan_sha256=None)
        after=copy.deepcopy(initial);ids=[format(i,'064x') for i in range(32)]
        after.update(requirement_plan_sha256='f'*64,supporting_sources=[dict(id=v) for v in ids],
                     evidence_claims=[dict(id='r'+str(i+1),source_ids=ids) for i in range(16)])
        delta=len(g.r.canonical(g.result_schema(after)).encode())-len(g.r.canonical(g.result_schema(initial)).encode())
        self.assertLessEqual(delta,g.SCHEMA_PLAN_RESERVE)


class OutputBoundaryAndPrivacyTests(unittest.TestCase):
    def test_rename_locations_use_actual_base_and_head_paths(self):
        changes=[dict(path='src/new.py',previous='src/old.py',before='old\n',after='new\n',
                      before_source=dict(mode='100644'),after_source=dict(mode='100644'))]
        spans=g.changed_ranges(changes)
        self.assertEqual(spans,[dict(path='src/old.py',side='base',start=1,end=1),dict(path='src/new.py',side='head',start=1,end=1)])
        _,c=fixture();c['changed_ranges']=spans;c['inventory'][0]['path']='src/new.py'
        v=result(c);self.assertEqual(g.validate_result(v,c)['reviewed_files'],['src/new.py'])
        v['requirement_results'][0]['locations'][0]['path']='src/new.py'
        with self.assertRaisesRegex(g.Refused,'result_location_not_changed'):g.validate_result(v,c)
    def test_redacted_telemetry_is_unwired_and_has_no_content_or_identifiers(self):
        _,c=fixture();v=result(c);sentinel='private-sentinel @user `/tmp/.env`'
        v['summary']=sentinel;v['reviewed_files']=['src/file0.py',sentinel,{}]*40
        v['requirement_results'][0].update(detail=sentinel,id=sentinel,status=sentinel)
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output),patch.object(g.subprocess,'run',side_effect=AssertionError('no subprocess')):
            telemetry=g.structural_telemetry(v,c)
        encoded=g.r.canonical(telemetry)
        for forbidden in (sentinel,'src/file0.py',HEAD,c['requirement_plan_sha256'],c['supporting_sources'][0]['id']):self.assertNotIn(forbidden,encoded)
        self.assertEqual(output.getvalue(),'');self.assertEqual(telemetry['reviewed_files']['observed_count'],81)
        import ast
        tree=ast.parse(Path(g.__file__).read_text())
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='structural_telemetry' for n in ast.walk(tree)))
    def test_fixed_outer_diagnostics_do_not_echo_synthetic_content(self):
        h=Harness();_,packet=h.collect(True)
        outer=dict(type='result',subtype='success',is_error=False,structured_output={})
        cases=[('{}',1,'review_cli_nonzero'),('x'*(g.MAX_RESULT*2+1),0,'review_outer_size'),
               ('synthetic-token',0,'review_stdout_credential_refused'),('private-sentinel malformed',0,'review_outer_json'),
               ('[]',0,'review_outer_shape'),('{}',0,'review_outer_status'),
               (json.dumps(dict(type='result',subtype='success',is_error=False)),0,'review_structured_missing')]
        for stdout,code,expected in cases:
            capture=io.StringIO()
            with patch.dict(os.environ,{'CLAUDE_CODE_OAUTH_TOKEN':'synthetic-token'}),patch.object(g.os,'lstat',side_effect=FileNotFoundError),patch.object(g.Path,'read_bytes',autospec=True,side_effect=lambda path:SKILL_BYTES if path.name=='SKILL.md' else b'fixture'),patch.object(g.subprocess,'run',return_value=SimpleNamespace(returncode=code,stdout=stdout)),contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
                with self.assertRaisesRegex(g.Refused,'^'+expected+'$'):
                    g.invoke_json(packet,dict(h.config['claude_final_review'],cli_sha256=hashlib.sha256(b'fixture').hexdigest()),{})
            self.assertEqual(capture.getvalue(),'')
    def test_each_ambiguous_final_boundary_has_a_fixed_noncontent_code(self):
        _,c=fixture(2,2)
        cases=[
            (lambda v:v.update(reviewed_files='private-sentinel'),'review_incomplete_shape'),
            (lambda v:v.update(reviewed_files=[]),'review_incomplete_count'),
            (lambda v:v['reviewed_files'].__setitem__(0,'private-sentinel'),'review_incomplete_membership'),
            (lambda v:v['reviewed_files'].__setitem__(1,v['reviewed_files'][0]),'review_incomplete_duplicate'),
            (lambda v:v['requirement_results'][0].update(extra='private-sentinel'),'result_claim_shape'),
            (lambda v:v['requirement_results'][0].update(id='private-sentinel'),'result_claim_id_membership'),
            (lambda v:v['requirement_results'][1].update(id='r1'),'result_claim_id_duplicate'),
            (lambda v:v['requirement_results'][0].update(detail='  '),'result_claim_detail_text'),
            (lambda v:v['requirement_results'][0].update(status='private-sentinel'),'result_claim_status'),
            (lambda v:v['requirement_results'][0].update(source_ids='private-sentinel'),'result_evidence_sources_shape'),
            (lambda v:v['requirement_results'][0].update(source_ids=[]),'result_evidence_sources_count'),
            (lambda v:v['requirement_results'][0].update(source_ids=[v['requirement_results'][0]['source_ids'][0]]*2),'result_evidence_sources_duplicate'),
            (lambda v:v['requirement_results'][0].update(source_ids=['x'*64,'y'*64]),'result_evidence_sources_membership'),
            (lambda v:v['requirement_results'][0]['locations'][0].update(extra='private-sentinel'),'result_change_location_shape'),
            (lambda v:v['requirement_results'][0]['locations'][0].update(reason='  '),'result_change_location_reason'),
            (lambda v:v['requirement_results'][0]['locations'][0].update(start=True),'result_change_location_start_integer'),
            (lambda v:v['requirement_results'][0]['locations'][0].update(end=True),'result_change_location_end_integer'),
        ]
        for mutate,code in cases:
            value=copy.deepcopy(result(c));mutate(value);out=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out):
                with self.assertRaisesRegex(g.Refused,'^'+code+'$'):g.validate_result(value,c)
            self.assertEqual(out.getvalue(),'')

    def test_oversized_schema_refuses_before_cli_or_credential_lookup(self):
        h=Harness();_,packet=h.collect(True)
        with patch.object(g.subprocess,'run') as run,patch.object(g.os.environ,'get',side_effect=AssertionError('no credential lookup')):
            with self.assertRaisesRegex(g.Refused,'output_schema_size_limit'):
                g.invoke_json(packet,h.config['claude_final_review'],dict(description='x'*g.MAX_SCHEMA_BYTES))
            run.assert_not_called()
    def test_oversized_context_enums_refuse_before_planning(self):
        h=Harness();h.files=[dict(h.files[0],filename='src/'+str(i)+'x'*1000) for i in range(80)];h.pull['changed_files']=80
        with patch.object(g,'plan_context') as planner:
            with self.assertRaisesRegex(g.Refused,'result_schema_size_limit'):
                g.review(h,'o/r',1,HEAD,h.document,'d'*40,200,plan_invoke=planner)
            planner.assert_not_called()

if __name__=='__main__':unittest.main()
