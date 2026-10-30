"""Real worker flow with disposable Git and a synthetic guarded provider."""
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

TESTS=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

fixture=load('guarded_main_fixture',TESTS/'test_main_sync.py')
guard_test=load('guarded_provider_fixture',TESTS/'test_worker_guard.py')
work=fixture.work


class GuardedHandoffTests(unittest.TestCase):
    setUp=fixture.MainSyncTests.setUp
    command=fixture.MainSyncTests.command
    git=fixture.MainSyncTests.git
    publish=fixture.MainSyncTests.publish

    def exercise(self,uncertain=False,later_owner=False,dispatch_uncertain=False):
        owner='guarded-contract-owner';issue_number,pr_number=7,9
        issue_path=self.main/'.claude/worktrees/issue-7'
        self.git(self.main,'remote','set-url','origin','https://github.com/o/r.git')
        item=dict(number=7,title='fixture implementation',body='bounded contract',state='open',
                  created_at='2026-01-01',labels=['todo'])
        calls=[]
        class GitHub:
            repo='o/r';pr=None
            def pages(client,path):
                if '/pulls?' in path:return [copy.deepcopy(client.pr)] if client.pr else []
                return [copy.deepcopy(item)]
            def issue(client,n):
                assert n==issue_number
                return copy.deepcopy(item)
            def patch(client,n,**values):
                assert n==issue_number
                calls.append(('labels',values));item.update(values)
            def api(client,path):
                if path==f'repos/{client.repo}/pulls/{pr_number}':return copy.deepcopy(client.pr)
                raise AssertionError(path)
        client=GitHub();provider=guard_test.fixture.Provider();policy=guard_test.policy()
        def run(*args):
            if args[0]=='git':
                if args[-2:]==('rev-parse','--git-common-dir'):return str(self.main/'.git')
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
                client.pr=dict(number=pr_number,state='open',body='Closes #7\n',
                    head=dict(ref='issue/7',sha=head,repo=dict(full_name=client.repo)),
                    base=dict(ref='main',repo=dict(full_name=client.repo)))
                return 'https://github.com/o/r/pull/9'
            if args[:3] in (('gh','issue','comment'),('gh','pr','comment')):return 'recorded in fixture'
            raise AssertionError(args)
        def exists(ref):
            return subprocess.run(['git','show-ref','--verify','--quiet',ref],cwd=self.main).returncode==0
        def process_identity(token):
            assert token==owner
            return dict(owner=token,host=socket.gethostname(),pid=999999,birth='fixture',released=False)
        def invoke(args):
            with patch.object(sys,'argv',['work.py',*args]), \
                 patch.dict(os.environ,ISSUE_WORK_OWNER=owner,ISSUE_MAIN_EXCLUSIVE_OWNER=''), \
                 patch.object(work,'run',side_effect=run),patch.object(work,'branch_exists',side_effect=exists), \
                 patch.object(work.queue,'Client',return_value=client), \
                 patch.object(work.tree_state,'owner_record',side_effect=process_identity), \
                 patch.object(work.worker_guard,'Api',return_value=provider), \
                 patch.object(work.worker_guard,'read_policy',return_value=policy),contextlib.redirect_stdout(io.StringIO()):
                return work.main()
        self.assertEqual(invoke(['next']),0)
        lease_path=self.main/'.git/issue-work.lock'
        lease=json.loads(lease_path.read_text());intent=lease['guard_intent']
        held=work.worker_guard.provider.read_chain(provider,'o/r',policy['genesis'],policy['ruleset_id'],7)
        self.assertEqual(held['state']['locks']['7'],work.worker_guard.descriptor(intent))
        (issue_path/'implemented').write_text('fixture work\n')
        self.git(issue_path,'add','implemented');self.git(issue_path,'commit','-qm','fixture implementation')
        summary=Path(self.tmp.name)/'summary.md';summary.write_text('fixture summary')
        if uncertain or dispatch_uncertain:
            if uncertain:provider.fail_patch='after'
            if dispatch_uncertain:provider.fail_dispatch=True
            with self.assertRaisesRegex(RuntimeError,'guard_update_unknown' if uncertain else 'dispatch outcome unknown'):
                invoke(['finish','7',str(summary)])
            lease=json.loads(lease_path.read_text())
            self.assertEqual(lease['phase'],'guard_release')
            comments_before=sum(1 for call in calls if call[:3] in (('gh','issue','comment'),('gh','pr','comment')))
            with self.assertRaisesRegex(RuntimeError,'complete the prior guarded submission'):
                invoke(['next'])
            provider.fail_patch=None
            provider.fail_dispatch=False
            if later_owner:
                new_claim=dict(owner='f'*32,kind='merge',head='a'*40)
                work.worker_guard.provider.update(provider,'o/r',policy['genesis'],policy['ruleset_id'],
                                                   7,'claim',new_claim,'e'*32)
                with self.assertRaisesRegex(RuntimeError,'worker_guard_not_owner'):
                    invoke(['finish','7',str(summary)])
                current=work.worker_guard.provider.read_chain(provider,'o/r',policy['genesis'],policy['ruleset_id'],7)
                self.assertEqual(current['state']['locks']['7'],new_claim)
                self.assertTrue(lease_path.exists())
                return
            self.assertEqual(invoke(['finish','7',str(summary)]),0)
            comments_after=sum(1 for call in calls if call[:3] in (('gh','issue','comment'),('gh','pr','comment')))
            self.assertEqual(comments_after,comments_before)
        else:
            self.assertEqual(invoke(['finish','7',str(summary)]),0)
        self.assertFalse(lease_path.exists())
        self.assertEqual(provider.dispatches,['o/r'] * (2 if dispatch_uncertain else 1))
        released=work.worker_guard.provider.read_chain(provider,'o/r',policy['genesis'],policy['ruleset_id'],7)
        self.assertEqual(released['state']['locks'],{})
        self.assertEqual(item['labels'],['doing'])

    def test_worker_keeps_guard_until_submission(self):self.exercise()
    def test_uncertain_release_recovers_without_repeating_submission(self):self.exercise(uncertain=True)
    def test_later_merge_owner_is_preserved_during_recovery(self):self.exercise(uncertain=True,later_owner=True)
    def test_uncertain_wake_retries_without_repeating_submission(self):self.exercise(dispatch_uncertain=True)


if __name__=='__main__':unittest.main()
