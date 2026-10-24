"""Isolated Git-plumbing fixtures only. No network or product commands."""
import contextlib
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/maintain-worktrees.py'
spec = importlib.util.spec_from_file_location('maintenance', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Remote:
    def __init__(self, case):
        self.case = case
        self.repo = 'faberline/tape'
        self.pr = dict(number=9, state='closed', merged=True, merged_at='2026-01-01',
            head=dict(ref='issue/7', sha=case.head, repo=dict(full_name=self.repo)),
            base=dict(ref='main', repo=dict(full_name=self.repo)), merge_commit_sha=case.merge)
        self.issue_state = 'closed'
        self.opened = []
        self.default = case.merge
        self.reads = []

    def api(self, path):
        self.reads.append(path)
        if path == 'repos/'+self.repo:
            return dict(full_name=self.repo, default_branch='main')
        if '/branches/' in path:
            return dict(commit=dict(sha=self.default))
        if '/pulls/' in path:
            return copy.deepcopy(self.pr)
        if '/git/commits/' in path:
            return dict(sha=self.case.merge, tree=dict(sha=self.case.tree),
                parents=[dict(sha=v) for v in self.case.git('rev-list','--parents','-n','1',self.case.merge).split()[1:]])
        raise AssertionError(path)

    def pages(self, path):
        self.reads.append(path)
        return copy.deepcopy(self.opened)

    def issue(self, n):
        assert n == 7
        return dict(state=self.issue_state)


class Fixture(unittest.TestCase):
    strategy = 'squash'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aw-maintenance-unit-')
        self.addCleanup(self.tmp.cleanup)
        self.main = Path(self.tmp.name).resolve()/'repo'
        self.main.mkdir()
        self.git('init','-q','-b','main')
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.git('config','core.hooksPath','/dev/null')
        self.git('remote','add','origin','https://github.com/faberline/tape.git')
        (self.main/'.gitignore').write_text('.claude/\ncache/\n')
        (self.main/'tracked').write_text('base\n')
        self.git('add','.gitignore','tracked')
        self.git('commit','-qm','base')
        self.base = self.git('rev-parse','HEAD')
        self.path = self.main/'.claude/worktrees/issue-7'
        self.git('worktree','add','-q','-b','issue/7',str(self.path))
        with patch.object(m.state, 'owner_record', return_value=dict(owner='owner-fixture',host=socket.gethostname(),pid=999999,birth='fixture-process-identity',released=False)):
            m.state.created(self.main, 7, 'issue/7', self.path, 'owner-fixture')
        (self.path/'tracked').write_text('changed\n')
        (self.path/'binary').write_bytes(b'\0binary\xff')
        self.git('-C',str(self.path),'add','tracked','binary')
        self.git('-C',str(self.path),'commit','-qm','issue')
        self.head = self.git('-C',str(self.path),'rev-parse','HEAD')
        m.state.update(self.main, 7, 'issue/7', self.path, 'owner-fixture', pr=9, submitted=True)
        if self.strategy == 'normal':
            self.git('merge','-q','--no-ff','--no-edit','issue/7')
        else:
            self.git('merge','-q','--squash','issue/7')
            self.git('commit','-qm','squashed issue')
        self.merge = self.git('rev-parse','HEAD')
        self.tree = self.git('rev-parse','HEAD^{tree}')
        self.ctx = m.state.context(self.main)
        self.binding = dict(repo='faberline/tape', pr=9, reviewed_head=self.head, base_sha=self.base,
                            base_branch='main', merge_sha=self.merge, merge_tree=self.tree)
        self.remote = Remote(self)
        self.dead = patch.object(m.os, 'kill', side_effect=ProcessLookupError)
        self.dead.start()
        self.addCleanup(self.dead.stop)

    def git(self, *args):
        return subprocess.check_output(['git','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',
            '-C',str(self.main),*args], stderr=subprocess.PIPE, text=True).strip()

    def inspect(self):
        return m.inspect(self.ctx, self.binding, self.remote)[0]

    def plan(self):
        return dict(schema=1, status='eligible', context=self.ctx, created=int(time.time()),
                    code_hash=m.code_hash(), evidence=self.inspect(), reports=[])

    def apply(self, plan):
        with (Path(self.ctx['common'])/'issue-work.guard').open('a') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return m.apply(self.ctx, plan, self.remote)

    def record(self, mutate):
        data=m.state.load(self.ctx['common'])
        mutate(data['trees'][str(self.path)])
        m.state.save(self.ctx['common'],data)


class MaintenanceTests(Fixture):
    def test_squash_binary_blob_mode_path_proof_and_plan_are_non_destructive(self):
        proof=self.inspect()
        self.assertEqual(proof['kind'],'exact-single-parent-aggregate')
        self.assertTrue(proof['aggregate'])
        self.assertTrue(self.path.is_dir())
        self.assertFalse((Path(self.ctx['common'])/'issue-work.maintenance').exists())

    def test_explicit_apply_retains_branch_bundle_and_admin_recovery(self):
        result=self.apply(self.plan())
        self.assertEqual(result['outcome'],'removed')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.git('rev-parse','refs/heads/issue/7'),self.head)
        recovery=Path(result['recovery'])
        self.assertTrue((recovery/'branch.bundle').is_file())
        self.assertTrue((recovery/'admin/logs/HEAD').is_file())
        saved=json.loads((recovery/'receipt.json').read_text())
        self.assertEqual(saved['outcome'],'removed; branch retained')
        self.assertEqual(len(m.state.load(self.ctx['common'])['retired']),1)
        self.assertEqual(m.state.load(self.ctx['common'])['trees'],{})

    def test_dirty_tracked_untracked_and_ignored_all_hold(self):
        for name in ('tracked','new-file','cache/value'):
            with self.subTest(name=name):
                p=self.path/name
                p.parent.mkdir(parents=True,exist_ok=True)
                before=p.read_bytes() if p.exists() else None
                p.write_text('valuable\n')
                with self.assertRaisesRegex(ValueError,'content present'):
                    self.inspect()
                if before is None: p.unlink()
                else: p.write_bytes(before)
        self.assertTrue(self.path.exists())

    def test_extra_local_commit_even_same_tree_holds(self):
        self.git('-C',str(self.path),'commit','--allow-empty','-qm','unique local')
        with self.assertRaisesRegex(ValueError,'additional local commit'):
            self.inspect()

    def test_unique_reflog_commit_is_not_silently_discarded(self):
        self.git('-C',str(self.path),'commit','--allow-empty','-qm','unique')
        unique=self.git('-C',str(self.path),'rev-parse','HEAD')
        # Fixture only: a moved HEAD simulates historical recovery content.
        self.git('-C',str(self.path),'reset','--soft',self.head)
        with self.assertRaisesRegex(ValueError,'unique reflog'):
            self.inspect()
        self.assertNotEqual(unique,self.head)

    def test_open_queued_or_wrong_head_merge_is_not_permission(self):
        for changes in (dict(merged=False),dict(state='open'),dict(merge_commit_sha=self.base),dict(head=dict(ref='issue/7',sha=self.base,repo=dict(full_name='faberline/tape')))):
            original=copy.deepcopy(self.remote.pr)
            self.remote.pr.update(changes)
            with self.assertRaises(ValueError): self.inspect()
            self.remote.pr=original

    def test_issue_still_open_preserves_partial_work_reuse(self):
        self.remote.issue_state='open'
        with self.assertRaisesRegex(ValueError,'partial work'):
            self.inspect()

    def test_active_unknown_foreign_and_unreleased_owner_hold(self):
        for change in ({'released':False},{'host':'other-host'},{'birth':''}):
            original=m.state.load(self.ctx['common'])
            self.record(lambda r:r['owners'][0].update(change))
            with self.assertRaises(ValueError): self.inspect()
            m.state.save(self.ctx['common'],original)
        with patch.object(m.os,'kill',return_value=None),self.assertRaisesRegex(ValueError,'PID is live'):
            self.inspect()
        with patch.object(m.os,'kill',side_effect=PermissionError),self.assertRaisesRegex(ValueError,'liveness unknown'):
            self.inspect()

    def test_existing_lease_is_hold_even_if_pid_expired(self):
        lease=Path(self.ctx['common'])/'issue-work.lock'
        lease.write_text(json.dumps(dict(issue=7,owner='owner-fixture',pid=123,host=socket.gethostname(),since=0,phase='coding')))
        with self.assertRaisesRegex(ValueError,'lease holds'):
            self.inspect()
        lease.write_text('{"issue":99,"owner":"unknown"}')
        with self.assertRaisesRegex(ValueError,'lease schema'):
            self.inspect()

    def test_open_related_pr_or_protected_record_hold(self):
        self.remote.opened=[dict(head=dict(sha=self.head,ref='other',repo=dict(full_name='faberline/tape')))]
        with self.assertRaisesRegex(ValueError,'another open PR'):
            self.inspect()
        self.remote.opened=[]
        self.record(lambda r:r.update(protected=True))
        with self.assertRaisesRegex(ValueError,'protected'):
            self.inspect()

    def test_missing_path_and_status_failure_are_not_prune_permission(self):
        original=m.state.git
        def failed(main,*args):
            if 'status' in args: raise subprocess.CalledProcessError(128,'git status')
            return original(main,*args)
        with patch.object(m.state,'git',side_effect=failed),self.assertRaises(subprocess.CalledProcessError):
            self.inspect()
        shutil.rmtree(self.path)  # only this isolated temporary fixture
        with self.assertRaises(ValueError): self.inspect()
        self.assertTrue(any(r['worktree']==str(self.path) for r in m.records(self.main)))

    def test_symlink_replaced_path_and_custom_branch_hold(self):
        other=self.main/'other'
        self.path.rename(other)
        self.path.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):
            self.inspect()
        self.path.unlink()
        other.rename(self.path)
        self.record(lambda r:r.update(branch='project-mamba'))
        with self.assertRaises(ValueError): self.inspect()

    def test_stale_plan_remote_and_content_changes_abort(self):
        plan=self.plan()
        self.remote.default=self.base
        with self.assertRaises(ValueError): self.apply(plan)
        self.remote.default=self.merge
        (self.path/'new').write_text('valuable')
        with self.assertRaises(ValueError): self.apply(plan)
        self.assertTrue(self.path.exists())

    def test_changed_record_or_expired_plan_hold(self):
        plan=self.plan()
        plan['created']-=601
        with self.assertRaisesRegex(ValueError,'expired'):
            self.apply(plan)
        plan=self.plan()
        self.record(lambda r:r['identity']['path_inode'].__setitem__(1,0))
        with self.assertRaises(ValueError): self.apply(plan)

    def test_changed_after_archive_aborts_and_keeps_recovery(self):
        plan=self.plan()
        original=m.inspect
        count=[0]
        def changed(*args):
            count[0]+=1
            if count[0]==2: (self.path/'cache/new').parent.mkdir(exist_ok=True); (self.path/'cache/new').write_text('valuable')
            return original(*args)
        with patch.object(m,'inspect',side_effect=changed),self.assertRaises(ValueError):
            self.apply(plan)
        self.assertTrue(self.path.exists())
        self.assertTrue(list((Path(self.ctx['common'])/'issue-work.maintenance').glob('*/branch.bundle')))

    def test_uncertain_remove_not_retried(self):
        plan=self.plan()
        original=m.state.git
        calls=[]
        def failing(main,*args):
            if args[-3:-1]==('worktree','remove'):
                calls.append(args)
                raise subprocess.CalledProcessError(1,'git worktree remove')
            return original(main,*args)
        with patch.object(m.state,'git',side_effect=failing),self.assertRaises(subprocess.CalledProcessError): self.apply(plan)
        with self.assertRaisesRegex(ValueError,'existing apply journal'): self.apply(plan)
        changed=dict(plan,created=plan['created']-1,reports=[dict(note='not retry authority')])
        with self.assertRaisesRegex(ValueError,'existing apply journal'): self.apply(changed)
        self.assertEqual(len(calls),1)
        self.assertTrue(self.path.exists())

    def test_default_cli_is_plan_only_and_historical_inventory_is_report_only(self):
        file=Path(self.tmp.name)/'merge.json'
        file.write_text(json.dumps(self.binding))
        with patch.object(m.Path,'cwd',return_value=self.main),patch.object(m.queue,'Client',return_value=self.remote),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(m.main(['--receipt',str(file)]),0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['status'],'eligible')
        self.assertTrue(self.path.exists())
        m.state.save(self.ctx['common'],dict(schema=1,trees={}))
        with patch.object(m.Path,'cwd',return_value=self.main),patch.object(m.queue,'Client',return_value=self.remote),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(m.main(['--receipt',str(file)]),3)
        self.assertEqual(json.loads(output.getvalue())['status'],'held')
        self.assertTrue(self.path.exists())

    def test_guard_busy_never_enters_remote_or_apply(self):
        guard=Path(self.ctx['common'])/'issue-work.guard'
        with guard.open('a') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(m.Path,'cwd',return_value=self.main),patch.object(m.queue,'Client') as client,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(['apply','--plan','unused']),3)
            client.assert_not_called()

    def test_squash_landed_blob_mode_or_path_difference_is_not_equivalent(self):
        for change in ('binary','mode','path'):
            with self.subTest(change=change):
                if change != 'binary': self.setUp()  # independent temporary Git fixture
                if change=='binary': (self.main/'binary').write_bytes(b'\0different')
                elif change=='mode': (self.main/'tracked').chmod(0o755)
                else: self.git('mv','tracked','renamed')
                self.git('add','-A')
                self.git('commit','--amend','-qm','non-equivalent landed result')
                original=(self.merge,self.tree,copy.deepcopy(self.binding),copy.deepcopy(self.remote.pr),self.remote.default)
                self.merge=self.git('rev-parse','HEAD'); self.tree=self.git('rev-parse','HEAD^{tree}')
                self.binding.update(merge_sha=self.merge,merge_tree=self.tree)
                self.remote.pr['merge_commit_sha']=self.merge; self.remote.default=self.merge
                with self.assertRaisesRegex(ValueError,'aggregate blob/mode/path proof differs'): self.inspect()
                self.merge,self.tree,self.binding,self.remote.pr,self.remote.default=original

    def test_remote_failure_is_not_cached_permission_or_retried(self):
        plan=self.plan()
        with patch.object(self.remote,'api',side_effect=subprocess.CalledProcessError(1,'gh api')) as api:
            with self.assertRaises(subprocess.CalledProcessError): self.apply(plan)
        self.assertEqual(api.call_count,1)
        self.assertFalse((Path(self.ctx['common'])/'issue-work.maintenance').exists())

    def test_locked_tree_unknown_admin_and_controller_version_hold(self):
        plan=self.plan()
        changed=dict(plan,code_hash='not-this-controller')
        with self.assertRaisesRegex(ValueError,'controller mismatch'): self.apply(changed)
        self.git('worktree','lock',str(self.path))
        with self.assertRaisesRegex(ValueError,'locked'): self.inspect()
        self.git('worktree','unlock',str(self.path))
        record=m.state.load(self.ctx['common'])['trees'][str(self.path)]
        (Path(record['identity']['admin'])/'config.worktree').write_text('valuable unknown config')
        with self.assertRaisesRegex(ValueError,'unknown/in-progress'): self.inspect()

    def test_configured_filter_is_disabled_without_persistent_config_change(self):
        sentinel=Path(self.tmp.name)/'must-not-run'
        self.git('config','filter.example.clean',f'touch {sentinel}')
        (self.path/'.gitattributes').write_text('tracked filter=example\n')
        with self.assertRaisesRegex(ValueError,'content present'): self.inspect()
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.git('config','filter.example.clean'),f'touch {sentinel}')

    def test_same_owner_continuation_keeps_process_history_then_releases(self):
        new=dict(owner='owner-fixture',host=socket.gethostname(),pid=999998,birth='second-process',released=False)
        with patch.object(m.state,'owner_record',return_value=new):
            m.state.update(self.main,7,'issue/7',self.path,'owner-fixture')
        data=m.state.load(self.ctx['common'])['trees'][str(self.path)]
        self.assertEqual(len(data['owners']),2)
        self.assertTrue(data['owners'][0]['released'])
        with self.assertRaisesRegex(ValueError,'not submitted/released'): self.inspect()
        m.state.update(self.main,7,'issue/7',self.path,'owner-fixture',pr=9,submitted=True)
        self.assertTrue(self.inspect())

    def test_historical_and_other_app_trees_are_never_adopted(self):
        m.state.save(self.ctx['common'],dict(schema=1,trees={}))
        with self.assertRaisesRegex(ValueError,'historical/app trees report-only'): self.inspect()
        self.assertTrue(self.path.exists())
        self.assertEqual(m.state.load(self.ctx['common'])['trees'],{})

    def test_arbitrary_registered_path_with_matching_provenance_needs_no_migration(self):
        old=self.path
        moved=Path(self.tmp.name).resolve()/'historical-location'
        self.git('worktree','move',str(old),str(moved))
        data=m.state.load(self.ctx['common'])
        record=data['trees'].pop(str(old))
        record['identity']=m.state.identity(self.main,moved)
        data['trees'][str(moved)]=record  # fixture of independently proven provenance
        m.state.save(self.ctx['common'],data)
        self.path=moved
        self.assertEqual(self.inspect()['local']['path'],str(moved))


class NormalMergeTest(Fixture):
    strategy='normal'
    def test_normal_merge_proves_reachability(self):
        self.assertEqual(self.inspect()['kind'],'reachable')


class PureTests(unittest.TestCase):
    def test_duplicate_receipt_keys_and_unknown_fields_refused(self):
        with self.assertRaises(ValueError): m.state.strict_json('{"pr":1,"pr":2}')
        with self.assertRaises(ValueError): m.receipt(dict(repo='faberline/tape',pr=1))

    def test_process_probe_denial_produces_unknown_not_inactive(self):
        with patch.object(m.state.subprocess,'check_output',side_effect=PermissionError):
            record=m.state.owner_record('owner')
        self.assertEqual(record['birth'],'')
        with self.assertRaisesRegex(ValueError,'unverified'):
            m.owners_inactive(dict(phase='submitted',owners=[dict(record,released=True)]))

    def test_supported_origin_identity_does_not_accept_other_services(self):
        for url in ('git@github.com:faberline/tape.git','https://github.com/faberline/tape.git','ssh://git@github.com/faberline/tape.git'):
            self.assertEqual(m.state.origin_repo(url),'faberline/tape')
        for url in ('/tmp/repo','https://other.example/faberline/tape','git@alias:faberline/tape.git'):
            with self.assertRaises(ValueError): m.state.origin_repo(url)


if __name__=='__main__': unittest.main()
