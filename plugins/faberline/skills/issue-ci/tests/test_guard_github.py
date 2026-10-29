"""Synthetic GitHub ledger provider tests. No remote refs or commits are changed."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit, parse_qs

path=Path(__file__).resolve().parents[1]/'scripts/guard_github.py'
spec=importlib.util.spec_from_file_location('guard_github',path)
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
REPO='o/r';GENESIS='a'*40;TREE='b'*40;BASE='c'*40;RULE=9


def owner(char,kind='worker',head=None):
    return dict(owner=char*32,kind=kind,head=head)


class Provider:
    def __init__(self):
        self.heads={7:GENESIS,8:GENESIS};self.rule=True;self.bypass=[];self.fail_patch=None;self.fail_create=None;self.writes=[]
        self.dispatches=[];self.fail_dispatch=False
        self.commits={GENESIS:dict(sha=GENESIS,tree=dict(sha=TREE),parents=[dict(sha=BASE)],
                                   message=g.genesis_message(REPO)),
                      BASE:dict(sha=BASE,tree=dict(sha=TREE))}
    @property
    def head(self):return self.heads[7]
    @head.setter
    def head(self,value):self.heads[7]=value
    def dispatch(self,repo):
        assert repo==REPO
        self.dispatches.append(repo)
        if self.fail_dispatch:raise RuntimeError('dispatch outcome unknown')
    def api(self,path,method='GET',data=None):
        if path==f'repos/{REPO}':return dict(full_name=REPO,default_branch='main')
        if path==f'repos/{REPO}/compare/{BASE}...main':return dict(status='ahead',base_commit=dict(sha=BASE))
        prefix=f'repos/{REPO}/compare/{GENESIS}...'
        if path.startswith(prefix):
            parsed=urlsplit(path);head=parsed.path.removeprefix(prefix)
            page=int(parse_qs(parsed.query)['page'][0]);size=int(parse_qs(parsed.query)['per_page'][0])
            current=head;history=[]
            while current!=GENESIS:
                item=self.commits[current];history.append(item);current=item['parents'][0]['sha']
            history.reverse();selected=history[(page-1)*size:page*size]
            return dict(status='ahead' if history else 'identical',ahead_by=len(history),behind_by=0,
                total_commits=len(history),base_commit=dict(sha=GENESIS),merge_base_commit=dict(sha=GENESIS),
                commits=[dict(sha=x['sha'],parents=x['parents'],commit=dict(tree=x['tree'],message=x['message']))
                         for x in selected])
        if path=='graphql':
            if data['query']==g.READ_REF:
                branch=data['variables']['ref'].removeprefix('refs/heads/')
                issue=int(branch.removeprefix(g.BRANCH_PREFIX))
                ref=(dict(name=branch,target=dict(__typename='Commit',oid=self.heads[issue]))
                     if issue in self.heads else None)
                return dict(data=dict(repository=dict(nameWithOwner=REPO,ref=ref)))
            if data['query']==g.RULESET:
                return dict(data=dict(repository=dict(nameWithOwner=REPO,ruleset=dict(databaseId=RULE,
                    enforcement='ACTIVE',target='BRANCH',bypassActors=dict(totalCount=0,pageInfo=dict(hasNextPage=False))))))
        if unquote(path).startswith(f'repos/{REPO}/rules/branches/{g.BRANCH_PREFIX}'):
            return [dict(type='non_fast_forward',ruleset_id=RULE)] if self.rule else []
        if path==f'repos/{REPO}/rulesets/{RULE}':
            return dict(id=RULE,enforcement='active',target='branch',bypass_actors=self.bypass)
        prefix=f'repos/{REPO}/git/commits/'
        if path.startswith(prefix) and method=='GET':return copy.deepcopy(self.commits[path[len(prefix):]])
        if path==f'repos/{REPO}/git/commits' and method=='POST':
            self.writes.append('commit')
            sha=hashlib.sha1(json.dumps(data,sort_keys=True).encode()).hexdigest()
            value=dict(sha=sha,tree=dict(sha=data['tree']),parents=[dict(sha=x) for x in data['parents']],
                       message=data['message'])
            self.commits[sha]=copy.deepcopy(value);return value
        if path==f'repos/{REPO}/git/refs' and method=='POST':
            self.writes.append('create_ref')
            issue=int(data['ref'].removeprefix('refs/heads/'+g.BRANCH_PREFIX))
            if self.fail_create=='before':raise TimeoutError('unknown create')
            if issue in self.heads:raise RuntimeError('already exists')
            self.heads[issue]=data['sha']
            if self.fail_create=='after':raise TimeoutError('unknown create after write')
            return dict(ref=data['ref'],object=dict(type='commit',sha=data['sha']))
        if path.startswith(f'repos/{REPO}/git/refs/heads/{g.BRANCH_PREFIX}') and method=='PATCH':
            self.writes.append('patch')
            assert data['force'] is False
            issue=int(path.removeprefix(f'repos/{REPO}/git/refs/heads/{g.BRANCH_PREFIX}'))
            child=data['sha']
            if self.fail_patch=='before':raise TimeoutError('unknown response')
            if self.fail_patch=='concurrent':
                parent=self.heads[issue]
                state=dict(version=1,repo=REPO,sequence=0,locks={})
                candidate=g.ledger.proposal(state,REPO,parent,issue,'claim',owner('b'),'2'*32)
                rival=hashlib.sha1(b'rival').hexdigest()
                self.commits[rival]=dict(sha=rival,tree=dict(sha=TREE),parents=[dict(sha=parent)],
                                         message=g.ledger.commit_message(candidate))
                self.heads[issue]=rival
            if self.commits[child]['parents'][0]['sha']!=self.heads[issue]:raise RuntimeError('non-fast-forward')
            self.heads[issue]=child
            if self.fail_patch=='after':raise TimeoutError('unknown response after write')
            return dict(ref='refs/heads/'+g.branch(issue),object=dict(type='commit',sha=child))
        raise AssertionError((path,method))


class ProviderTests(unittest.TestCase):
    def setUp(self):self.api=Provider()

    def update(self,issue,action,claim,operation):
        return g.update(self.api,REPO,GENESIS,RULE,issue,action,claim,operation*32)

    def test_claim_and_release_keep_other_issues(self):
        self.update(7,'claim',owner('a'),'1')
        self.update(8,'claim',owner('b'),'2')
        self.update(7,'release',owner('a'),'3')
        first=g.read_chain(self.api,REPO,GENESIS,RULE,7)
        second=g.read_chain(self.api,REPO,GENESIS,RULE,8)
        self.assertEqual(first['state']['locks'],{})
        self.assertEqual(second['state']['locks'],{'8':owner('b')})
        self.assertEqual(first['state']['sequence'],2)
        self.assertEqual(first['depth'],2)

    def test_foreign_owner_and_force_bypass_never_write(self):
        self.update(7,'claim',owner('a'),'1');self.api.writes=[]
        with self.assertRaisesRegex(g.Refused,'guard_not_owner'):
            self.update(7,'release',owner('b'),'2')
        self.assertEqual(self.api.writes,[])
        self.api.bypass=[{'id':1}]
        with self.assertRaisesRegex(g.Refused,'guard_bypass_unknown'):
            self.update(8,'claim',owner('b'),'3')
        self.assertEqual(self.api.writes,[])

    def test_missing_force_protection_refuses_before_any_write(self):
        self.api.rule=False
        with self.assertRaisesRegex(g.Refused,'guard_force_rule_missing'):
            self.update(7,'claim',owner('a'),'1')
        self.assertEqual(self.api.writes,[])

    def test_github_actions_graphql_bypass_fallback(self):
        self.api.bypass=None
        self.assertEqual(self.update(7,'claim',owner('a'),'1')['state']['locks'],{'7':owner('a')})

    def test_uncertain_patch_is_never_retried_or_reported_as_success(self):
        for when in ('before','after'):
            with self.subTest(when=when):
                self.setUp();self.api.fail_patch=when
                with self.assertRaisesRegex(g.Refused,'guard_update_unknown'):
                    self.update(7,'claim',owner('a'),'1')
                self.assertEqual(self.api.writes.count('patch'),1)
                # A later read can observe whether the original write landed.
                current=g.read_chain(self.api,REPO,GENESIS,RULE,7)
                self.assertEqual('7' in current['state']['locks'],when=='after')
                if when=='after':
                    self.api.fail_patch=None
                    with self.assertRaisesRegex(g.Refused,'guard_operation_replayed'):
                        self.update(7,'claim',owner('a'),'1')
                    self.assertEqual(self.api.writes.count('patch'),1)

    def test_concurrent_claim_wins_without_being_overwritten(self):
        self.api.fail_patch='concurrent'
        with self.assertRaisesRegex(g.Refused,'guard_update_unknown'):
            self.update(7,'claim',owner('a'),'1')
        self.assertEqual(self.api.writes.count('patch'),1)
        self.assertEqual(g.read_chain(self.api,REPO,GENESIS,RULE,7)['state']['locks'],{'7':owner('b')})

    def test_corrupt_history_and_unapproved_genesis_refuse(self):
        self.update(7,'claim',owner('a'),'1')
        self.api.commits[self.api.head]['message']+=' '
        with self.assertRaises(g.Refused):g.read_chain(self.api,REPO,GENESIS,RULE,7)
        with self.assertRaises((g.Refused,KeyError)):g.read_chain(self.api,REPO,'f'*40,RULE,7)

    def test_earlier_corruption_cannot_be_hidden_by_a_later_commit(self):
        self.update(7,'claim',owner('a'),'1');first=self.api.head
        self.update(7,'release',owner('a'),'2')
        self.api.commits[first]['message']+=' '
        with self.assertRaises(g.Refused):g.read_chain(self.api,REPO,GENESIS,RULE,7)

    def test_history_limit_never_silently_truncates_or_writes(self):
        with patch.object(g,'MAX_DEPTH',1):
            self.update(7,'claim',owner('a'),'1');self.api.writes=[]
            with self.assertRaisesRegex(g.Refused,'guard_history_limit_or_cycle'):
                self.update(7,'release',owner('a'),'2')
            self.assertEqual(self.api.writes,[])
            self.assertEqual(self.update(8,'claim',owner('b'),'3')['state']['locks'],{'8':owner('b')})

    def test_missing_issue_ref_is_created_once_and_existing_owner_preserved(self):
        result=g.ensure_ref(self.api,REPO,GENESIS,RULE,9)
        self.assertEqual(result['head'],GENESIS)
        self.assertEqual(self.api.writes,['create_ref'])
        self.update(7,'claim',owner('a'),'1');self.api.writes=[]
        current=g.ensure_ref(self.api,REPO,GENESIS,RULE,7)
        self.assertEqual(current['state']['locks'],{'7':owner('a')})
        self.assertEqual(self.api.writes,[])

    def test_uncertain_ref_creation_is_not_blindly_retried(self):
        self.api.fail_create='after'
        with self.assertRaisesRegex(g.Refused,'guard_create_unknown'):
            g.ensure_ref(self.api,REPO,GENESIS,RULE,9)
        self.assertEqual(self.api.writes.count('create_ref'),1)
        self.api.fail_create=None
        self.assertEqual(g.ensure_ref(self.api,REPO,GENESIS,RULE,9)['head'],GENESIS)
        self.assertEqual(self.api.writes.count('create_ref'),1)

    def test_invalid_guard_policy_blocks_ref_creation(self):
        self.api.rule=False
        with self.assertRaisesRegex(g.Refused,'guard_force_rule_missing'):
            g.ensure_ref(self.api,REPO,GENESIS,RULE,9)
        self.assertEqual(self.api.writes,[])

    def test_genesis_cannot_change_the_base_tree(self):
        self.api.commits[GENESIS]['tree']['sha']='f'*40
        with self.assertRaisesRegex(g.Refused,'guard_genesis_tree_changed'):
            g.ensure_ref(self.api,REPO,GENESIS,RULE,9)
        self.assertEqual(self.api.writes,[])

    def test_full_paginated_history_and_missing_page_entry(self):
        state=dict(version=1,repo=REPO,sequence=0,locks={});parent=GENESIS;claim=owner('a')
        for i in range(205):
            action='claim' if i%2==0 else 'release'
            candidate=g.ledger.proposal(state,REPO,parent,7,action,claim,f'{i:032x}')
            sha=hashlib.sha1(f'commit-{i}'.encode()).hexdigest()
            self.api.commits[sha]=dict(sha=sha,tree=dict(sha=TREE),parents=[dict(sha=parent)],
                                       message=g.ledger.commit_message(candidate))
            state=candidate['payload']['ledger'];parent=sha
        self.api.heads[7]=parent
        result=g.read_chain(self.api,REPO,GENESIS,RULE,7)
        self.assertEqual(result['depth'],205)
        self.assertEqual(result['state'],state)
        original=self.api.api
        def incomplete(path,method='GET',data=None):
            value=original(path,method,data)
            if f'/compare/{GENESIS}...' in path and 'page=2' in path:
                value['commits'].pop()
            return value
        self.api.api=incomplete
        with self.assertRaises(g.Refused):g.read_chain(self.api,REPO,GENESIS,RULE,7)

    def test_inconsistent_comparison_metadata_refuses(self):
        self.update(7,'claim',owner('a'),'1')
        original=self.api.api
        for field,value in (('status','diverged'),('ahead_by',0),('behind_by',1),
                            ('base_commit',{'sha':'f'*40}),('total_commits',True)):
            def corrupt(path,method='GET',data=None):
                result=original(path,method,data)
                if f'/compare/{GENESIS}...' in path:result[field]=value
                return result
            self.api.api=corrupt
            with self.subTest(field=field),self.assertRaises(g.Refused):
                g.read_chain(self.api,REPO,GENESIS,RULE,7)


if __name__=='__main__':
    unittest.main()
