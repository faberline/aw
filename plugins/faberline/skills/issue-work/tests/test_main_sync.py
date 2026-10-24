"""Affected isolated Git fixtures, no product/network tests."""
import contextlib
import copy
import fcntl
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

p=Path(__file__).resolve().parents[1]/'scripts/work.py'
spec=importlib.util.spec_from_file_location('work_main_sync',p)
work=importlib.util.module_from_spec(spec)
spec.loader.exec_module(work)


class MainSyncTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1')
        environment.start();self.addCleanup(environment.stop)
        self.tmp=tempfile.TemporaryDirectory(prefix='aw-main-ff-unit-')
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name).resolve()
        original_attributes_git = work.main_filters.git
        def isolated_defaults(path, *args, **kwargs):
            if args in (('var', 'GIT_ATTR_GLOBAL'), ('var', 'GIT_ATTR_SYSTEM')):
                return (str(root / ('absent-'+args[1]))+'\n').encode()
            return original_attributes_git(path, *args, **kwargs)
        defaults = patch.object(work.main_filters, 'git', side_effect=isolated_defaults)
        defaults.start();self.addCleanup(defaults.stop)
        self.seed=root/'seed';self.seed.mkdir()
        self.main=root/'main'
        self.remote=root/'remote.git'
        self.command('git','init','-q','-b','main',str(self.seed))
        self.command('git','init','-q','--bare','-b','main',str(self.remote))
        self.git(self.seed,'config','user.name','Fixture')
        self.git(self.seed,'config','user.email','fixture@example.invalid')
        (self.seed/'.gitignore').write_text('.claude/\nignored/\n')
        (self.seed/'tracked').write_text('base')
        self.git(self.seed,'add','.gitignore','tracked');self.git(self.seed,'commit','-qm','base')
        self.git(self.seed,'remote','add','origin',str(self.remote))
        self.git(self.seed,'push','-q','-u','origin','main')
        self.command('git','clone','-q',str(self.remote),str(self.main))
        self.git(self.main,'config','user.name','Fixture');self.git(self.main,'config','user.email','fixture@example.invalid')
        self.base=self.git(self.main,'rev-parse','HEAD')
        (self.seed/'tracked').write_text('latest')
        self.publish()

    def command(self,*args):
        return subprocess.check_output(args,stderr=subprocess.PIPE,text=True).strip()

    def git(self,path,*args):
        return self.command('git','-c','core.fsmonitor=false','-C',str(path),*args)

    def publish(self):
        self.git(self.seed,'add','tracked');self.git(self.seed,'commit','-qm','incoming')
        self.git(self.seed,'push','-q','origin','main')
        self.git(self.main,'fetch','-q','origin','refs/heads/main:refs/remotes/origin/main')
        self.fetched=self.git(self.main,'rev-parse','refs/remotes/origin/main')

    def sync(self,exclusive=True):
        with (self.main/'.git/issue-work.guard').open('a') as guard:
            fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.dict(os.environ,ISSUE_MAIN_EXCLUSIVE_OWNER='owner' if exclusive else ''):
                return work.sync_main(self.main,'owner',self.fetched)

    def assert_held_unchanged(self, reason):
        head=self.git(self.main,'rev-parse','HEAD')
        result=self.sync()
        self.assertEqual(result['status'],'held')
        self.assertIn(reason,result['reason'])
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),head)
        return result

    def test_clean_exclusive_main_fast_forwards_to_exact_fetched_sha(self):
        self.assertEqual(self.sync()['status'],'updated')
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.fetched)
        self.assertEqual((self.main/'tracked').read_text(),'latest')
        self.assertEqual(self.sync()['status'],'current')

    def test_hidden_tracked_bytes_hold_without_clearing_index_flags(self):
        for flag in ('--assume-unchanged','--skip-worktree'):
            with self.subTest(flag=flag):
                self.git(self.main,'update-index',flag,'tracked')
                (self.main/'tracked').write_text('hidden valuable')
                self.assertEqual(self.git(self.main,'status','--porcelain'),'')
                flags=self.git(self.main,'ls-files','-v','tracked')
                self.assert_held_unchanged('hidden/nonordinary index flags')
                self.assertEqual((self.main/'tracked').read_text(),'hidden valuable')
                self.assertEqual(self.git(self.main,'ls-files','-v','tracked'),flags)
                # Clear only the disposable fixture before the next case.
                self.git(self.main,'update-index','--no-assume-unchanged','--no-skip-worktree','tracked')
                (self.main/'tracked').write_text('base')

    def test_hidden_flags_added_during_revalidation_stop_fast_forward(self):
        original=work.index_safety.inspect;calls=[0]
        def changing(path):
            calls[0]+=1
            if calls[0]==2:
                self.git(self.main,'update-index','--skip-worktree','tracked')
                (self.main/'tracked').write_text('hidden concurrent content')
            return original(path)
        with patch.object(work.index_safety,'inspect',side_effect=changing):
            self.assert_held_unchanged('hidden/nonordinary index flags')
        self.assertEqual((self.main/'tracked').read_text(),'hidden concurrent content')
        self.assertTrue(self.git(self.main,'ls-files','-v','tracked').startswith('S '))

    def test_sparse_main_settings_or_patterns_are_held(self):
        self.git(self.main,'config','core.sparseCheckout','true')
        self.assert_held_unchanged('sparse')
        self.git(self.main,'config','--unset','core.sparseCheckout')
        (self.main/'.git/info/sparse-checkout').write_text('tracked\n')
        self.assert_held_unchanged('sparse checkout metadata')

    def test_clean_filter_is_held_before_status_can_execute_repo_script(self):
        sentinel=Path(self.tmp.name)/'must-not-run-clean'
        (self.main/'driver-script').write_text('#!/bin/sh\ntouch "'+str(sentinel)+'"\n')
        (self.main/'.gitattributes').write_text('tracked filter=sentinel\n')
        (self.main/'tracked').write_text('valuable local')
        self.git(self.main,'config','filter.sentinel.clean','sh ./driver-script')
        original=work.git
        def audited(*args):
            if 'status' in args or 'merge' in args:raise AssertionError('filter guard must precede status/checkout')
            return original(*args)
        with patch.object(work,'git',side_effect=audited):
            self.assert_held_unchanged('configured Git filters')
        self.assertFalse(sentinel.exists())
        self.assertEqual((self.main/'tracked').read_text(),'valuable local')

    def test_smudge_and_process_filters_are_held_before_ff_checkout(self):
        sentinel=Path(self.tmp.name)/'must-not-run-checkout'
        (self.seed/'driver-script').write_text('#!/bin/sh\ntouch "'+str(sentinel)+'"\n')
        (self.seed/'.gitattributes').write_text('tracked filter=sentinel\n')
        self.git(self.seed,'add','driver-script','.gitattributes');self.publish()
        original=work.git
        for field in ('smudge','process'):
            with self.subTest(field=field):
                self.git(self.main,'config','filter.sentinel.'+field,'sh ./driver-script')
                def audited(*args):
                    if 'status' in args or 'merge' in args:raise AssertionError('filter guard must precede status/checkout')
                    return original(*args)
                with patch.object(work,'git',side_effect=audited):
                    self.assert_held_unchanged('configured Git filters')
                self.assertFalse(sentinel.exists())
                self.git(self.main,'config','--unset','filter.sentinel.'+field)

    def test_dirty_and_untracked_collision_preserve_user_content(self):
        (self.main/'tracked').write_text('user work')
        self.assert_held_unchanged('tracked/staged/untracked')
        self.assertEqual((self.main/'tracked').read_text(),'user work')
        (self.main/'tracked').write_text('base')
        (self.main/'.codex').mkdir();(self.main/'.codex/config.toml').write_text('local config')
        self.assert_held_unchanged('tracked/staged/untracked')
        self.assertEqual((self.main/'.codex/config.toml').read_text(),'local config')

    def test_untracked_incoming_path_is_not_autostashed_or_archived(self):
        (self.main/'incoming').write_text('valuable local')
        (self.seed/'incoming').write_text('remote content')
        self.git(self.seed,'add','incoming');self.publish()
        self.assert_held_unchanged('tracked/staged/untracked')
        self.assertEqual((self.main/'incoming').read_text(),'valuable local')
        self.assertFalse(self.git(self.main,'stash','list'))

    def test_diverged_or_locally_ahead_main_is_not_rebased(self):
        (self.main/'local').write_text('local commit')
        self.git(self.main,'add','local');self.git(self.main,'commit','-qm','local')
        self.assert_held_unchanged('ahead/diverged')

    def test_non_main_and_detached_checkout_are_not_switched(self):
        self.git(self.main,'checkout','-q','-b','other')
        self.assert_held_unchanged('not on main')
        self.assertEqual(self.git(self.main,'branch','--show-current'),'other')
        self.git(self.main,'checkout','-q','--detach')
        self.assertEqual(self.sync()['status'],'held')
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.base)

    def test_active_or_unknown_lease_holds_main(self):
        (self.main/'.git/issue-work.lock').write_text('unknown owner state')
        self.assert_held_unchanged('owner lease')

    def test_no_positive_exclusive_owner_confirmation_is_a_hold(self):
        result=self.sync(exclusive=False)
        self.assertEqual(result['status'],'held')
        self.assertIn('exclusive',result['reason'])
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.base)

    def test_existing_issue_tree_dirty_content_and_branch_are_untouched(self):
        issue=self.main/'.claude/worktrees/issue-7'
        self.git(self.main,'worktree','add','-q','-b','issue/7',str(issue),self.base)
        (issue/'tracked').write_text('in-progress issue')
        self.assertEqual(self.sync()['status'],'updated')
        self.assertEqual(self.git(issue,'rev-parse','HEAD'),self.base)
        self.assertEqual((issue/'tracked').read_text(),'in-progress issue')

    def test_ignored_noncolliding_content_is_preserved(self):
        (self.main/'ignored').mkdir();(self.main/'ignored/valuable').write_text('keep')
        self.assertEqual(self.sync()['status'],'updated')
        self.assertEqual((self.main/'ignored/valuable').read_text(),'keep')

    def test_ignored_collision_refuses_without_overwrite(self):
        (self.main/'ignored').mkdir();(self.main/'ignored/valuable').write_text('keep')
        (self.seed/'ignored').mkdir();(self.seed/'ignored/valuable').write_text('incoming')
        # Fixture prepares a tracked incoming path despite the old ignore rule.
        self.git(self.seed,'add','-f','ignored/valuable');self.publish()
        self.assertEqual(self.sync()['status'],'held')
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.base)
        self.assertEqual((self.main/'ignored/valuable').read_text(),'keep')

    def test_concurrent_primary_content_change_aborts_before_merge(self):
        original=work.git;count=[0]
        def changing(*args):
            if 'status' in args:
                count[0]+=1
                if count[0]==2:(self.main/'concurrent').write_text('keep')
            return original(*args)
        with patch.object(work,'git',side_effect=changing):
            self.assert_held_unchanged('changed before')
        self.assertEqual((self.main/'concurrent').read_text(),'keep')

    def test_changed_remote_tracking_ref_after_fetch_holds(self):
        self.git(self.main,'update-ref','refs/remotes/origin/main',self.base)
        self.assert_held_unchanged('changed after fetch')

    def test_post_merge_hook_is_not_a_local_test_dispatch(self):
        sentinel=Path(self.tmp.name)/'must-not-run'
        hook=self.main/'.git/hooks/post-merge'
        hook.write_text('#!/bin/sh\ntouch "'+str(sentinel)+'"\n');hook.chmod(0o755)
        self.assertEqual(self.sync()['status'],'updated')
        self.assertFalse(sentinel.exists())

    def test_new_remote_pr_branch_is_fetched_and_repair_uses_its_actual_head(self):
        self.git(self.seed,'checkout','-q','-b','issue/7')
        (self.seed/'tracked').write_text('actual remote PR content')
        self.git(self.seed,'add','tracked');self.git(self.seed,'commit','-qm','remote issue')
        pr_head=self.git(self.seed,'rev-parse','HEAD')
        self.git(self.seed,'push','-q','origin','issue/7')
        self.assertNotEqual(pr_head,self.fetched)
        for ref in ('refs/heads/issue/7','refs/remotes/origin/issue/7'):
            self.assertNotEqual(subprocess.run(['git','-C',str(self.main),'show-ref','--verify','--quiet',ref]).returncode,0)
        # Combined worker provenance uses the real logical GitHub origin; only
        # Git transport is routed to this disposable bare fixture.
        self.git(self.main,'remote','set-url','origin','https://github.com/faberline/tape.git')
        item=dict(number=7,title='repair',body='fixture',state='open',created_at='2026-01-01',labels=['todo'])
        class Client:
            repo='faberline/tape'
            def pages(client,path):
                if '/pulls?' in path:return [dict(number=9,head=dict(ref='issue/7',sha=pr_head,repo=dict(full_name=client.repo)))]
                return [copy.deepcopy(item)]
            def issue(client,n):return copy.deepcopy(item)
            def patch(client,n,**values):item.update(values)
        def run(*args):
            if args[-2:]==('rev-parse','--git-common-dir'):return str(self.main/'.git')
            if 'fetch' in args or 'push' in args:
                args=tuple(str(self.remote) if a=='origin' else a for a in args)
            return subprocess.check_output(args,cwd=self.main,stderr=subprocess.PIPE,text=True).strip()
        def exists(ref):return subprocess.run(['git','show-ref','--verify','--quiet',ref],cwd=self.main).returncode==0
        with patch.object(sys,'argv',['work.py','next']),patch.dict(os.environ,ISSUE_WORK_OWNER='owner',ISSUE_MAIN_EXCLUSIVE_OWNER=''),patch.object(work,'run',side_effect=run),patch.object(work,'branch_exists',side_effect=exists),patch.object(work.queue,'Client',return_value=Client()),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(work.main(),0)
        issue=self.main/'.claude/worktrees/issue-7'
        self.assertEqual(self.git(issue,'rev-parse','HEAD'),pr_head)
        self.assertEqual((issue/'tracked').read_text(),'actual remote PR content')
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.base)

    def test_known_pr_without_local_or_remote_branch_never_falls_back_to_main(self):
        def run(*args):
            if args[-2:]==('rev-parse','--git-common-dir'):return str(self.main/'.git')
            return subprocess.check_output(args,cwd=self.main,stderr=subprocess.PIPE,text=True).strip()
        def exists(ref):return subprocess.run(['git','show-ref','--verify','--quiet',ref],cwd=self.main).returncode==0
        with patch.object(work,'run',side_effect=run),patch.object(work,'branch_exists',side_effect=exists),self.assertRaisesRegex(RuntimeError,'never create its repair from main'):
            work.prepare(self.main,7,'issue/7',existing_pr=9)
        self.assertFalse((self.main/'.claude/worktrees/issue-7').exists())

    def test_failed_fetch_cannot_prepare_work_on_stale_origin(self):
        def git(*args):
            if args[0]=='rev-parse':return str(self.main/'.git')
            if args[0]=='fetch':raise subprocess.CalledProcessError(1,'git fetch')
            raise AssertionError(args)
        with patch.object(sys,'argv',['work.py','next']),patch.dict(os.environ,ISSUE_WORK_OWNER='owner'),patch.object(work,'git',side_effect=git),patch.object(work,'prepare') as prepare,patch.object(work.queue,'Client') as client,contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):work.main()
        prepare.assert_not_called();client.assert_not_called()
        self.assertEqual(self.git(self.main,'rev-parse','HEAD'),self.base)


if __name__=='__main__':unittest.main()
