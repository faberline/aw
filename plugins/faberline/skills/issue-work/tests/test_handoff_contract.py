"""Combined lifecycle unit contract: disposable Git, fake GitHub/process probes.

Exercises actual worker next/finish and maintenance plan/apply. This is not live
CI, product e2e, native session certification, or an automatic done assertion.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent/'scripts'


def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fixture=load('main_fixture_for_handoff',TESTS/'test_main_sync.py')
work=fixture.work
maintenance=load('maintenance_for_handoff',SCRIPTS/'maintain-worktrees.py')


class CombinedHandoffTests(unittest.TestCase):
    setUp=fixture.MainSyncTests.setUp
    command=fixture.MainSyncTests.command
    git=fixture.MainSyncTests.git
    publish=fixture.MainSyncTests.publish

    def lifecycle(self,strategy):
        owner='contract-owner'
        issue_number,pr_number=7,9
        issue_path=self.main/'.claude/worktrees/issue-7'
        # The registry sees a stable logical GitHub origin. Only Git transport
        # is redirected to the disposable local bare repository, never network.
        self.git(self.main,'remote','set-url','origin','https://github.com/faberline/tape.git')
        item=dict(number=7,title='fixture implementation',body='bounded contract',state='open',
                  created_at='2026-01-01',labels=['todo'])
        calls=[]
        class GitHub:
            repo='faberline/tape'
            pr=None
            def pages(client,path):
                if '/pulls?' in path:
                    return [copy.deepcopy(client.pr)] if client.pr and not client.pr['merged'] else []
                return [copy.deepcopy(item)]
            def issue(client,n):
                assert n==issue_number
                return copy.deepcopy(item)
            def patch(client,n,**values):
                assert n==issue_number
                calls.append(('worker-labels',values))
                item.update(values)
            def api(client,path):
                if path=='repos/'+client.repo:
                    return dict(full_name=client.repo,default_branch='main')
                if '/branches/' in path:
                    return dict(commit=dict(sha=self.command('git','--git-dir',str(self.remote),'rev-parse','refs/heads/main')))
                if '/pulls/' in path:
                    return copy.deepcopy(client.pr)
                if '/git/commits/' in path:
                    sha=path.rsplit('/',1)[1]
                    return dict(sha=sha,tree=dict(sha=self.git(self.main,'rev-parse',sha+'^{tree}')),
                        parents=[dict(sha=p) for p in self.git(self.main,'rev-list','--parents','-n','1',sha).split()[1:]])
                raise AssertionError(path)
        client=GitHub()
        def run(*args):
            if args[0]=='git':
                if args[-2:]==('rev-parse','--git-common-dir'):
                    return str(self.main/'.git')
                if 'fetch' in args or 'push' in args:
                    args=tuple(str(self.remote) if a=='origin' else a for a in args)
                return subprocess.check_output(args,cwd=self.main,stderr=subprocess.PIPE,text=True).strip()
            calls.append(tuple(args))
            if args[:3]==('gh','issue','view'):
                fields=args[-1].split(',')
                value=dict(item,state=item['state'].upper(),labels=[dict(name=name) for name in item['labels']])
                return json.dumps({key:value[key] for key in fields})
            if args[:3]==('gh','pr','create'):
                head=self.git(issue_path,'rev-parse','HEAD')
                client.pr=dict(number=pr_number,state='open',merged=False,merged_at=None,merge_commit_sha=None,
                    head=dict(ref='issue/7',sha=head,repo=dict(full_name=client.repo)),
                    base=dict(ref='main',repo=dict(full_name=client.repo)))
                return 'https://github.com/faberline/tape/pull/9'
            if args[:3] in (('gh','issue','comment'),('gh','pr','comment')):
                return 'recorded in fixture'
            raise AssertionError(args)
        def exists(ref):
            return subprocess.run(['git','show-ref','--verify','--quiet',ref],cwd=self.main).returncode==0
        def process_identity(token):
            assert token==owner
            return dict(owner=token,host=socket.gethostname(),pid=999999,birth='known fixture process',released=False)
        def invoke(args):
            with patch.object(sys,'argv',['work.py',*args]),patch.dict(os.environ,ISSUE_WORK_OWNER=owner,ISSUE_MAIN_EXCLUSIVE_OWNER=owner),patch.object(work,'run',side_effect=run),patch.object(work,'branch_exists',side_effect=exists),patch.object(work.queue,'Client',return_value=client),patch.object(work.tree_state,'owner_record',side_effect=process_identity),contextlib.redirect_stdout(io.StringIO()) as output:
                result=work.main()
            self.assertEqual(result,0,output.getvalue())
            return output.getvalue()
        # next performs FF only in this isolated fixture, then actually creates
        # and records the issue tree. No fixture fabricates registry provenance.
        output=invoke(['next'])
        self.assertIn('"status": "updated"',output)
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.fetched)
        data=work.tree_state.load(self.main/'.git')
        created=copy.deepcopy(data['trees'][str(issue_path)])
        self.assertEqual(created['created_by'],'aw-issue-work-v1')
        self.assertEqual(created['phase'],'coding')
        self.assertIsNone(created['pr'])
        self.assertFalse(created['owners'][-1]['released'])
        (issue_path/'implemented').write_text('preserve exact implementation bytes\n')
        self.git(issue_path,'add','implemented');self.git(issue_path,'commit','-qm','implementation')
        head=self.git(issue_path,'rev-parse','HEAD')
        summary=Path(self.tmp.name)/'summary.md';summary.write_text('isolated contract fixture; no product tests')
        invoke(['finish','7',str(summary)])
        submitted=work.tree_state.load(self.main/'.git')['trees'][str(issue_path)]
        self.assertEqual(submitted['identity'],created['identity'])
        self.assertEqual(submitted['phase'],'submitted')
        self.assertEqual(submitted['pr'],pr_number)
        self.assertEqual(submitted['submitted_head'],head)
        self.assertTrue(all(o['released'] for o in submitted['owners']))
        self.assertTrue(issue_path.is_dir(),'finish must never remove the implementation tree')
        self.assertFalse((self.main/'.git/issue-work.lock').exists())
        self.assertFalse((self.main/'.git/issue-work.maintenance').exists())
        self.assertEqual(item['labels'],['doing'])
        self.assertEqual(self.command('git','--git-dir',str(self.remote),'rev-parse','refs/heads/issue/7'),head)
        # The local Git merge is fixture setup only; production maintenance has
        # no merge capability and must read back GitHub MERGED independently.
        if strategy=='squash':
            self.git(self.main,'merge','-q','--squash','issue/7')
            self.git(self.main,'commit','-qm','squash fixture')
        else:
            self.git(self.main,'merge','-q','--no-ff','--no-edit','issue/7')
        merged=self.git(self.main,'rev-parse','HEAD')
        self.git(self.main,'push','-q',str(self.remote),'main')
        binding=dict(repo=client.repo,pr=pr_number,reviewed_head=head,base_branch='main',base_sha=self.fetched,
                     merge_sha=merged,merge_tree=self.git(self.main,'rev-parse',merged+'^{tree}'))
        ctx=maintenance.state.context(self.main)
        with patch.object(maintenance.os,'kill',side_effect=ProcessLookupError):
            with self.assertRaisesRegex(ValueError,'not actually MERGED'):
                maintenance.inspect(ctx,binding,client)
            client.pr.update(state='closed',merged=True,merged_at='fixture-server-time',merge_commit_sha=merged)
            with self.assertRaisesRegex(ValueError,'partial work'):
                maintenance.inspect(ctx,binding,client)
            item['state']='closed'  # fake server's Closes linkage; no live write
            receipt=Path(self.tmp.name)/'merged.json';receipt.write_text(json.dumps(binding))
            with patch.object(maintenance.Path,'cwd',return_value=self.main),patch.object(maintenance.queue,'Client',return_value=client),contextlib.redirect_stdout(io.StringIO()) as planned:
                self.assertEqual(maintenance.main(['--receipt',str(receipt)]),0)
            plan=json.loads(planned.getvalue())
            self.assertEqual(plan['status'],'eligible')
            self.assertTrue(issue_path.exists(),'dry-run must preserve the issue tree')
            plan_file=Path(self.tmp.name)/'plan.json';plan_file.write_text(json.dumps(plan))
            with patch.object(maintenance.Path,'cwd',return_value=self.main),patch.object(maintenance.queue,'Client',return_value=client),contextlib.redirect_stdout(io.StringIO()) as applied:
                self.assertEqual(maintenance.main(['apply','--plan',str(plan_file)]),0)
            result=json.loads(applied.getvalue())
        self.assertEqual(result['outcome'],'removed')
        self.assertFalse(issue_path.exists())
        self.assertEqual(self.git(self.main,'rev-parse','refs/heads/issue/7'),head)
        retired=work.tree_state.load(self.main/'.git')
        self.assertEqual(retired['trees'],{})
        self.assertEqual(retired['retired'][0]['record']['identity'],created['identity'])
        self.assertEqual(retired['retired'][0]['record']['submitted_head'],head)
        self.assertEqual(retired['retired'][0]['binding'],binding)
        recovered=Path(self.tmp.name)/'recovered'
        self.command('git','clone','-q','--branch','issue/7',str(Path(result['recovery'])/'branch.bundle'),str(recovered))
        self.assertEqual((recovered/'implemented').read_text(),'preserve exact implementation bytes\n')
        self.assertEqual(self.git(recovered,'rev-parse','HEAD'),head)
        self.assertEqual([c for c in calls if c[0]=='worker-labels'],[('worker-labels',{'labels':['doing']})])
        self.assertFalse(any(c[:3] in (('gh','pr','merge'),('gh','issue','close')) for c in calls))

    def test_create_submit_squash_merge_cleanup_preserves_provenance(self):
        self.lifecycle('squash')

    def test_create_submit_normal_merge_cleanup_preserves_provenance(self):
        self.lifecycle('normal')


if __name__=='__main__':unittest.main()
