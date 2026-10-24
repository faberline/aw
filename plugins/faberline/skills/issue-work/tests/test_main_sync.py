"""Affected isolated Git fixtures, no product/network tests."""
import contextlib
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
        self.tmp=tempfile.TemporaryDirectory(prefix='aw-main-ff-unit-')
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name).resolve()
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
