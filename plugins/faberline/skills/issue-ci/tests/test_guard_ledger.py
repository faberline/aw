"""Synthetic concurrent transitions; no GitHub or Git ref mutations."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'scripts/guard_ledger.py'
spec=importlib.util.spec_from_file_location('guard_ledger',path)
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)


def owner(char,kind='worker',head=None):
    return dict(owner=char*32,kind=kind,head=head)


class SyntheticRef:
    """Model Git's non-force update: the current OID must be an ancestor."""
    def __init__(self):
        self.state=dict(version=1,repo='o/r',sequence=0,locks={})
        self.current='0'*40
        self.parents={self.current:None}
        self.states={self.current:copy.deepcopy(self.state)}
    def commit(self,parent,state,nonce):
        raw=json.dumps([parent,state,nonce],sort_keys=True).encode()
        sha=hashlib.sha1(raw).hexdigest()
        self.parents[sha]=parent;self.states[sha]=copy.deepcopy(state)
        return sha
    def update_nonforce(self,child):
        cursor=child
        while cursor is not None and cursor!=self.current:
            cursor=self.parents[cursor]
        if cursor is None:
            raise g.Refused('non_fast_forward')
        self.current=child;self.state=copy.deepcopy(self.states[child])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ref=SyntheticRef()

    def test_two_claims_from_same_state_cannot_both_win(self):
        before=self.ref.current
        first=g.transition(self.ref.state,'o/r',7,'claim',owner('a'))
        second=g.transition(self.ref.state,'o/r',7,'claim',owner('b'))
        first_commit=self.ref.commit(before,first,'operation-a')
        second_commit=self.ref.commit(before,second,'operation-b')
        self.ref.update_nonforce(first_commit)
        with self.assertRaisesRegex(g.Refused,'non_fast_forward'):
            self.ref.update_nonforce(second_commit)
        self.assertEqual(self.ref.state['locks']['7'],owner('a'))
        with self.assertRaisesRegex(g.Refused,'guard_held'):
            g.transition(self.ref.state,'o/r',7,'claim',owner('b'))

    def test_stale_release_cannot_remove_new_owner(self):
        held=g.transition(self.ref.state,'o/r',7,'claim',owner('a'))
        acquired=self.ref.commit(self.ref.current,held,'a-acquire');self.ref.update_nonforce(acquired)
        release=g.transition(self.ref.state,'o/r',7,'release',owner('a'))
        old_release=self.ref.commit(acquired,release,'old-release')
        good_release=self.ref.commit(acquired,release,'good-release');self.ref.update_nonforce(good_release)
        second=g.transition(self.ref.state,'o/r',7,'claim',owner('b'))
        self.ref.update_nonforce(self.ref.commit(self.ref.current,second,'b-acquire'))
        with self.assertRaisesRegex(g.Refused,'non_fast_forward'):
            self.ref.update_nonforce(old_release)
        with self.assertRaisesRegex(g.Refused,'guard_not_owner'):
            g.transition(self.ref.state,'o/r',7,'release',owner('a'))
        self.assertEqual(self.ref.state['locks']['7'],owner('b'))

    def test_different_issues_can_retry_without_losing_an_owner(self):
        base=self.ref.current
        a=g.transition(self.ref.state,'o/r',7,'claim',owner('a'))
        b=g.transition(self.ref.state,'o/r',8,'claim',owner('b'))
        a_commit=self.ref.commit(base,a,'a');b_commit=self.ref.commit(base,b,'b')
        self.ref.update_nonforce(a_commit)
        with self.assertRaisesRegex(g.Refused,'non_fast_forward'):
            self.ref.update_nonforce(b_commit)
        refreshed=g.transition(self.ref.state,'o/r',8,'claim',owner('b'))
        self.ref.update_nonforce(self.ref.commit(self.ref.current,refreshed,'b-retry'))
        self.assertEqual(set(self.ref.state['locks']),{'7','8'})
        self.assertEqual(self.ref.state['sequence'],2)

    def test_invalid_and_foreign_state_cannot_be_used(self):
        for value in (dict(self.ref.state,version=True),dict(self.ref.state,sequence=-1),
                      dict(self.ref.state,locks={'7':owner('a'),'8':owner('a')}),
                      dict(self.ref.state,locks={'01':owner('a')}),
                      dict(self.ref.state,locks={'7':owner('a','merge')})):
            with self.subTest(value=value),self.assertRaises(g.Refused):
                g.validate(value)
        with self.assertRaises(g.Refused):
            g.transition(self.ref.state,'o/r',True,'claim',owner('a'))
        with self.assertRaises(g.Refused):
            g.transition(self.ref.state,'o/r',7,'release',owner('a'))
        with self.assertRaisesRegex(g.Refused,'guard_repository'):
            g.transition(self.ref.state,'other/repo',7,'claim',owner('a'))
        self.assertEqual(self.ref.state['locks'],{})

    def test_transition_does_not_mutate_original_or_claim(self):
        state=copy.deepcopy(self.ref.state);claim=owner('c','merge','a'*40)
        next_state=g.transition(state,'o/r',7,'claim',claim)
        claim['head']='b'*40
        self.assertEqual(state['locks'],{})
        self.assertEqual(next_state['locks']['7']['head'],'a'*40)

    def test_child_proposal_binds_parent_and_preserves_other_owners(self):
        state=g.transition(self.ref.state,'o/r',7,'claim',owner('a'))
        candidate=g.proposal(state,'o/r','b'*40,8,'claim',owner('b'),'c'*32)
        verified=g.verify_proposal(state,'o/r','b'*40,candidate)
        self.assertEqual(verified['locks'],{'7':owner('a'),'8':owner('b')})
        for mutate in (lambda x:x['payload'].update(parent='d'*40),
                       lambda x:x['payload']['ledger']['locks'].pop('7'),
                       lambda x:x.update(sha256='0'*64),
                       lambda x:x['payload'].update(operation='bad')):
            with self.subTest(mutate=mutate):
                bad=copy.deepcopy(candidate);mutate(bad)
                with self.assertRaises(g.Refused):
                    g.verify_proposal(state,'o/r','b'*40,bad)

    def test_stale_child_cannot_be_rebased_by_reusing_its_record(self):
        initial=copy.deepcopy(self.ref.state)
        stale=g.proposal(initial,'o/r','a'*40,7,'claim',owner('a'),'c'*32)
        advanced=g.transition(initial,'o/r',8,'claim',owner('b'))
        with self.assertRaises(g.Refused):
            g.verify_proposal(advanced,'o/r','a'*40,stale)
        with self.assertRaises(g.Refused):
            g.verify_proposal(initial,'o/r','b'*40,stale)

    def test_provider_commit_must_have_exact_parent_tree_and_transition(self):
        state=copy.deepcopy(self.ref.state);parent,child,tree='a'*40,'b'*40,'c'*40
        candidate=g.proposal(state,'o/r',parent,7,'claim',owner('a'),'d'*32)
        commit=dict(sha=child,tree=dict(sha=tree),parents=[dict(sha=parent)],message=g.commit_message(candidate))
        self.assertEqual(g.verify_commit(state,'o/r',parent,child,tree,commit)['locks']['7'],owner('a'))
        changes=(lambda c:c.update(sha='f'*40),lambda c:c['tree'].update(sha='f'*40),
                 lambda c:c.update(parents=[dict(sha='f'*40)]),
                 lambda c:c.update(parents=[dict(sha=parent),dict(sha=parent)]),
                 lambda c:c.update(message=c['message']+' '),
                 lambda c:c.update(message=g.MARKER+'{"x":1,"x":2}'))
        for change in changes:
            with self.subTest(change=change):
                bad=copy.deepcopy(commit);change(bad)
                with self.assertRaises(g.Refused):
                    g.verify_commit(state,'o/r',parent,child,tree,bad)


if __name__=='__main__':
    unittest.main()
