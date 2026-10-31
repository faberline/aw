"""Keep native PR-comment and issue-label permissions in separate jobs."""
import json
import subprocess
import unittest
from unittest.mock import patch
import test_development_report as base

ci=base.ci
LINKS='source-pr-links'


class PermissionTests(unittest.TestCase):
    setUp=base.ReporterTests.setUp
    invoke=base.ReporterTests.invoke

    def workflow(self):
        return ci.development_module().yaml_document(self.provider.raw)

    def test_manifest_separates_native_comment_and_label_capabilities(self):
        jobs=self.workflow()['jobs']
        self.assertEqual(set(jobs),{'report',LINKS})
        self.assertEqual(jobs[LINKS]['permissions'],dict(actions='read',contents='read',issues='read',**{'pull-requests':'write'}))
        self.assertEqual(jobs['report']['permissions'],dict(actions='read',contents='read',issues='write',**{'pull-requests':'read'}))
        self.assertEqual(jobs['report']['needs'],LINKS)
        self.assertEqual(jobs['report']['if'],'${{ always() }}')

    def test_native_pr_comment_permission_model_reproduces_deployed_403(self):
        p=self.provider;p.pull['body']='No canonical issue'
        jobs=self.workflow()['jobs'];role=LINKS if LINKS in jobs else 'report'
        self.env['GITHUB_JOB']=role;original=p.api
        def native(path,method='GET',data=None):
            if method=='POST'and path=='repos/o/r/issues/8/comments':
                if jobs[role]['permissions']['pull-requests']!='write':
                    raise subprocess.CalledProcessError(1,['gh','api','--method','POST',path,'--input','-'],
                        output=json.dumps(dict(message='Resource not accessible by integration',status='403')))
            return original(path,method,data)
        p.api=native
        result,process,_=self.invoke()
        self.assertEqual(result,0);process.assert_not_called()
        self.assertEqual(len(p.writes),1)
        self.assertEqual(p.writes[0][:2],('repos/o/r/issues/8/comments','POST'))

    def test_runtime_binds_job_identity_and_refuses_missing_or_unknown_role(self):
        for role in (None,'other','report-copy'):
            if role is None:self.env.pop('GITHUB_JOB',None)
            else:self.env['GITHUB_JOB']=role
            with self.subTest(role=role),self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual(self.provider.writes,[])

    def test_issue_writer_defers_link_diagnostic_without_pr_permission(self):
        self.env['GITHUB_JOB']='report';self.provider.pull['body']='No canonical issue'
        result,process,_=self.invoke()
        self.assertEqual(result,0);process.assert_not_called()
        self.assertEqual(self.provider.writes,[])

    def test_source_pr_role_never_calls_issue_lifecycle_processor(self):
        self.env['GITHUB_JOB']=LINKS
        result,process,_=self.invoke()
        self.assertEqual(result,0);process.assert_not_called()
        self.assertEqual(self.provider.writes,[])

    def test_permission_union_and_unsafe_job_dependencies_are_rejected(self):
        original=self.provider.raw
        mutations=[original.replace('issues: read','issues: write'),
                   original.replace('pull-requests: read','pull-requests: write'),
                   original.replace('    needs: source-pr-links\n',''),
                   original.replace('if: ${{ always() }}','if: ${{ success() }}'),
                   original.replace('actions: read','actions: write')]
        for raw in mutations:
            self.provider.raw=raw
            with self.subTest(raw=raw),self.assertRaises(RuntimeError):self.invoke()
            self.assertEqual(self.provider.writes,[])
        self.provider.raw=original

    def test_source_pr_role_cannot_write_labels_issues_reviews_merges_or_close(self):
        self.env['GITHUB_JOB']=LINKS;self.provider.pull['body']='No canonical issue'
        module=ci.development_module()
        requests=[('repos/o/r/issues/7/comments','POST',dict(body='wrong issue')),
                  ('repos/o/r/issues/8/labels','POST',dict(labels=['done'])),
                  ('repos/o/r/issues/7','PATCH',dict(state='closed')),
                  ('repos/o/r/pulls/8','PATCH',dict(state='closed')),
                  ('repos/o/r/pulls/8/merge','PUT',dict()),
                  ('repos/o/r/pulls/8/reviews','POST',dict(event='APPROVE')),
                  ('repos/o/r/statuses/'+base.HEAD,'POST',dict(state='success')),
                  ('repos/o/r/issues/99/comments','POST',dict(body='wrong PR'))]
        for path,method,data in requests:
            def report(api,*args,**kwargs):api.api(path,method,data)
            with self.subTest(path=path),patch.object(ci,'development_module',return_value=module),patch.object(module,'report_missing_issue',side_effect=report):
                result,_,_=self.invoke();self.assertEqual(result,2)
                self.assertEqual(self.provider.writes,[])


if __name__=='__main__':unittest.main()
