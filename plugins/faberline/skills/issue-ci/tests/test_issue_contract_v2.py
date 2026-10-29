"""Issue-specific gates must never be inferred from a generic green build."""
import copy
import hashlib
import unittest
from unittest.mock import patch
import test_qualification as base

q, ci = base.q, base.ci


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def issue():
    return dict(number=7, state='open', url='https://api.github.com/repos/o/r/issues/7',
                repository_url='https://api.github.com/repos/o/r', title='Repair contract',
                body='Run the source-specific gates.', labels=[])


def policy():
    cfg = base.config(); data = issue()
    cfg['qualification']['version'] = 2
    cfg['qualification']['contracts'] = {'7': dict(title_sha256=digest(data['title']), body_sha256=digest(data['body']),
        applicability={kind: dict(status='required', checks=[check], reason='Required by the linked issue')
                       for kind, check in [('unit','unit'), ('functional_e2e','functional'), ('performance','perf')]},
        additional=[], unresolved=[])}
    return cfg


def pull():
    p = base.pr(); p['base']['repo'] = {'full_name':'o/r'}; p['title'] = 'Changes'; return p


def scope(cfg=None, pr=None, source=None, changes=None):
    return q.scoped_config(cfg or policy(), pr or pull(), changes or base.files(),
        {(base.WORKFLOW, ref): {'sha':base.BLOB} for ref in (base.HEAD, base.BASE)},
        issue() if source is None else source)


class IssueContractTests(unittest.TestCase):
    def test_reviewed_issue_adds_its_checks_to_path_selection(self):
        s=scope(changes=base.files('docs/guide.md'))
        self.assertEqual(s['_qualification_context']['issues'], [])
        self.assertEqual(set(s['workflows'][0]['jobs']), {'policy','unit','functional','perf'})
        self.assertEqual(s['_qualification_context']['contract']['issue'],7)

    def test_missing_foreign_blocked_closed_and_changed_issue_cannot_qualify(self):
        variants=[{}, dict(issue(), state='closed'), dict(issue(), title='Changed'),
                  dict(issue(), body='Changed'), dict(issue(), number=8),
                  dict(issue(), labels=[{'name':'blocked'}]),
                  dict(issue(), labels=None), dict(issue(), pull_request={}),
                  dict(issue(), repository_url='https://api.github.com/repos/x/r')]
        for value in variants:
            with self.subTest(value=value):
                s=scope(source=value)
                self.assertEqual(ci.evaluate_policy(s,base.evidence(s))[0],'pending')

    def test_missing_or_ambiguous_link_and_foreign_pr_are_blocked(self):
        for text in ['', 'Closes #7.', 'Closes #7\nCloses #8', 'Closes #7\nFixes #8']:
            p=pull();p['body']=text
            with self.subTest(text=text):self.assertTrue(scope(pr=p)['_qualification_context']['issues'])
        p=pull();p['head']['repo']['full_name']='other/r'
        self.assertTrue(scope(pr=p)['_qualification_context']['issues'])

    def test_unknown_and_unresolved_cannot_be_covered_by_generic_checks(self):
        for kind in q.CATEGORIES:
            c=policy();d=c['qualification']['contracts']['7']['applicability'][kind]
            d.update(status='unknown',checks=[])
            s=scope(cfg=c)
            with self.subTest(kind=kind):self.assertEqual(ci.evaluate_policy(s,base.evidence(s))[0],'pending')
        c=policy();c['qualification']['contracts']['7']['unresolved']=['Tape adapter acceptance has no verified run']
        s=scope(cfg=c);self.assertEqual(ci.evaluate_policy(s,base.evidence(s))[0],'pending')

    def test_not_applicable_does_not_remove_required_path_checks(self):
        c=policy();c['qualification']['contracts']['7']['applicability']['performance'].update(status='not_applicable',checks=[])
        self.assertIn('perf', scope(cfg=c)['workflows'][0]['jobs'])

    def test_bad_schema_is_rejected(self):
        cases=[]
        c=policy();del c['qualification']['contracts']['7']['applicability']['unit'];cases.append(c)
        c=policy();c['qualification']['contracts']['7']['title_sha256']='no';cases.append(c)
        c=policy();c['qualification']['contracts']['7']['additional']=['unknown'];cases.append(c)
        c=policy();c['qualification']['contracts']['7']['applicability']['performance']['checks']=['unit'];cases.append(c)
        c=policy();c['qualification']['contracts']['7']['applicability']['unit']['reason']='';cases.append(c)
        c=policy();c['qualification']['contracts']['7']['typo']=[];cases.append(c)
        for c in cases:
            with self.subTest(c=c),self.assertRaises(RuntimeError):q.validate(c)

    def test_v2_qualifier_fetches_only_linked_metadata(self):
        c=policy();p=pull()
        with patch.object(ci,'api',side_effect=lambda path: issue() if path.endswith('/issues/7') else {'sha':base.BLOB}) as api:
            s=ci.qualify_config('o/r',p,c,base.files())
        self.assertEqual(s['_qualification_context']['issues'],[])
        self.assertEqual(sum('/issues/7' in call.args[0] for call in api.call_args_list),1)

    def test_original_v1_does_not_request_issue_metadata(self):
        with patch.object(ci,'api',return_value={'sha':base.BLOB}) as api:
            ci.qualify_config('o/r',base.pr(),base.config(),base.files())
        self.assertTrue(all('/issues/' not in call.args[0] for call in api.call_args_list))

class AdditionalContractBoundaryTests(unittest.TestCase):
    def test_additional_check_is_unioned_even_for_docs(self):
        c=policy();c['qualification']['contracts']['7']['additional']=['unit']
        for decision in c['qualification']['contracts']['7']['applicability'].values():
            decision.update(status='not_applicable',checks=[],reason='Reviewed issue has no additional requirement')
        s=scope(cfg=c,changes=base.files('docs/guide.md'))
        self.assertEqual(set(s['workflows'][0]['jobs']),{'policy','unit'})

    def test_valid_contract_does_not_approve_protected_changes(self):
        for path in ('.github/workflows/pr.yml','.github/actions/sneaky/action.yml',
                     '.github/issue-ci-required-gates.json','scripts/ci/selector.py'):
            c=policy();c['qualification']['rules'].append(dict(paths=['**'],require=['unit','functional','perf'],exempt={}))
            s=scope(cfg=c,changes=base.files(path))
            with self.subTest(path=path):
                self.assertTrue(any('gate infrastructure change' in item for item in s['_qualification_context']['issues']))
                self.assertEqual(ci.evaluate_policy(s,base.evidence(s))[0],'pending')

class PaidReviewContractTests(unittest.TestCase):
    def harness(self):
        import test_claude_review as review
        h=review.Harness(); cfg=policy(); item=issue()
        cfg['workflows'][0]['path']=review.WORKFLOW
        cfg['workflows'][0]['jobs']=['unit']
        for check in cfg['qualification']['checks'].values():
            check['workflow']=review.WORKFLOW;check['job']='unit'
        cfg['qualification']['trusted_workflows']={review.WORKFLOW:{'id':10,'definition_sha':review.git_blob(h.workflow)}}
        h.config.update(lifecycle_mode='full',qualification_blockers=[],qualification=cfg['qualification'])
        h.issue.update(state=item['state'],url=item['url'],repository_url=item['repository_url'],labels=[])
        h.issue['title']=item['title'];h.issue['body']=item['body']
        h.plan['requirements'][0].update(requirement=item['body'],issue_quote=item['body'])
        def qualify(repo, pull, config, files):
            definitions={(review.WORKFLOW,ref):{'sha':review.git_blob(h.workflow)} for ref in (review.HEAD,review.BASE)}
            return q.scoped_config(config,pull,files,definitions,h.issue)
        h.qualify_config=qualify
        h.evaluate_policy=ci.evaluate_policy
        return h,review

    def test_valid_v2_reaches_review_and_binds_context(self):
        h,g=self.harness();receipt=h.produce()
        self.assertEqual(receipt['payload']['result']['decision'],'pass')
        self.assertEqual(receipt['payload']['context']['qualification_v2']['contract']['issue'],7)

    def test_missing_changed_unknown_and_unresolved_stop_before_model(self):
        changes=[lambda h:h.issue.update(body='changed'),lambda h:h.issue.update(state='closed'),lambda h:h.issue.update(labels=[{'name':'blocked'}]),
                 lambda h:h.config['qualification']['contracts'].clear(),
                 lambda h:h.config['qualification']['contracts']['7']['unresolved'].append('missing source gate'),
                 lambda h:h.config['qualification']['contracts']['7']['applicability']['unit'].update(status='unknown',checks=[])]
        for change in changes:
            h,g=self.harness();change(h);calls=[]
            with self.subTest(change=change),self.assertRaises(RuntimeError):h.produce(lambda *args:calls.append(args))
            self.assertEqual(calls,[])
            self.assertEqual(h.writes,[])

    def test_issue_or_contract_drift_during_review_rejects_receipt(self):
        for target in ('issue','contract'):
            h,g=self.harness()
            def invoke(packet, policy):
                value=g.result(packet['context']) if hasattr(g,'result') else None
                if target=='issue':h.issue['body']+=' changed'
                else:h.config['qualification']['contracts']['7']['unresolved'].append('new gate')
                return value
            with self.subTest(target=target),self.assertRaises(RuntimeError):h.produce(invoke)
            self.assertEqual(h.writes,[])
