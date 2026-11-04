import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

root=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
module=load('test_writer_attestation',root/'issue-work/scripts/ci_retry.py')
readiness=load('test_attestation_readiness',root/'issue-ci/scripts/readiness.py')
development=load('test_attestation_development',root/'issue-ci/scripts/development_pipeline.py')
SHA='4223a4534f0197e1ea23004b9388ffd1eea34639';MAIN='b'*40;HEAD='a'*40


class Client:
    repo='o/r'
    def __init__(self):
        template=(root/'issue-ci/templates/development-report.yml.example').read_text()
        self.source=template.replace('REVIEWED_AW_COMMIT',SHA)
        self.spec={'id':55,'path':'.github/workflows/issue-ci.yml','definition_sha':self.blob(),
                   'job':'report','link_job':'source-pr-links'}
        self.run={'id':100,'workflow_id':55,'path':self.spec['path'],'head_sha':MAIN,'head_branch':'main',
                  'head_repository':{'full_name':'o/r'},'event':'push','status':'completed',
                  'conclusion':'failure','run_attempt':1}
        self.job={'id':101,'name':'report','run_id':100,'run_attempt':1,'head_sha':MAIN,
                  'status':'completed','conclusion':'failure','started_at':'2026-11-03T00:00:00Z',
                  'completed_at':'2026-11-03T00:00:10Z'}
        self.state={'state':'todo','head':HEAD,'evidence':[],'reasons':['PR is behind current main']}
        self.report={'repo':'o/r','source':MAIN,'run':100,'role':'report',
                     'results':[{'pr':9,'head':HEAD,'state':'processed'},{'pr':10,'head':'c'*40,'state':'held'}]}
        self.extra=''
        self.cutoff='2026-11-03T00:00:01Z'
    def blob(self):
        data=self.source.encode();return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    def api(self,path):
        if '/contents/' in path:
            return {'type':'file','encoding':'base64','sha':self.blob(),'content':base64.b64encode(self.source.encode()).decode()}
        if '/workflows/55/runs?' in path:return {'total_count':1,'workflow_runs':[copy.deepcopy(self.run)]}
        raise AssertionError(path)
    def command(self,*args):return {'total_count':1,'jobs':[copy.deepcopy(self.job)]}
    def log(self,*args):return ('2026-11-03T00:00:02.1234567Z '+json.dumps(self.state)+'\n'
                                '2026-11-03T00:00:03Z '+module.MARKER+json.dumps(self.report)+'\n'+self.extra)
    def invoke(self):
        config={'completion_owner':'local','development_reporter':self.spec}
        with patch.object(module,'attestation_dependencies',return_value=(readiness,development)), \
             patch.object(readiness,'job_log',side_effect=self.log):
            return module.attest_writer(self,self.repo,MAIN,config,9,HEAD,'PR is behind current main',self.cutoff)


class WriterAttestationTests(unittest.TestCase):
    def test_trusted_finalized_pr_result_survives_unrelated_pr_failure(self):
        self.assertEqual(Client().invoke(),{'run':100,'attempt':1,'job':101})

    def test_expired_newer_log_does_not_hide_valid_current_epoch_proof(self):
        c=Client();original=c.api
        def api(path):
            if '/workflows/55/runs?' in path:
                return {'total_count':2,'workflow_runs':[copy.deepcopy(c.run),dict(c.run,id=99)]}
            return original(path)
        c.api=api
        def command(*args):
            job=copy.deepcopy(c.job)
            if '/runs/99/' in args[-1]:job.update(id=102,run_id=99)
            return {'total_count':1,'jobs':[job]}
        c.command=command
        def logs(repo,job):
            if job==101:raise subprocess.CalledProcessError(1,['gh','api'])
            report={**c.report,'run':99}
            return ('2026-11-03T00:00:02.1234567Z '+json.dumps(c.state)+'\n'
                    '2026-11-03T00:00:03Z '+module.MARKER+json.dumps(report))
        with patch.object(module,'attestation_dependencies',return_value=(readiness,development)), \
             patch.object(readiness,'job_log',side_effect=logs):
            result=module.attest_writer(c,c.repo,MAIN,{'completion_owner':'local','development_reporter':c.spec},
                                        9,HEAD,'PR is behind current main',c.cutoff)
        self.assertEqual(result,{'run':99,'attempt':1,'job':102})

    def test_missing_ambiguous_stale_or_cancelled_proof_never_qualifies(self):
        for problem in ('missing-final','target-held','duplicate-head','wrong-reason','duplicate-state',
                        'cancelled-run','cancelled-job','stale-run','wrong-attempt','unreviewed-sha','extra-step','old-observation','new-owner-epoch','unexplained-job-failure'):
            c=Client()
            if problem=='missing-final':c.report['results']=[]
            if problem=='target-held':c.report['results'][0]['state']='held'
            if problem=='duplicate-head':c.report['results'].append({'pr':11,'head':HEAD,'state':'processed'})
            if problem=='wrong-reason':c.state['reasons']=['different reason']
            if problem=='duplicate-state':c.extra='2026-11-03T00:00:04Z '+json.dumps(c.state)
            if problem=='cancelled-run':c.run['conclusion']='cancelled'
            if problem=='cancelled-job':c.job['conclusion']='cancelled'
            if problem=='stale-run':c.run['head_sha']='f'*40
            if problem=='wrong-attempt':c.job['run_attempt']=2
            if problem=='unreviewed-sha':c.source=c.source.replace(SHA,'f'*40);c.spec['definition_sha']=c.blob()
            if problem=='extra-step':c.source=c.source.replace('operation: development-report','operation: development-report\n      - run: echo untrusted',1);c.spec['definition_sha']=c.blob()
            if problem=='old-observation':c.job['started_at']='2026-11-03T00:00:04Z'
            if problem=='new-owner-epoch':c.cutoff='2026-11-03T00:00:02Z'
            if problem=='unexplained-job-failure':c.report['results']=c.report['results'][:1]
            with self.subTest(problem=problem):
                try:value=c.invoke()
                except (RuntimeError,ValueError):continue
                self.assertFalse(value)


if __name__=='__main__':unittest.main()
