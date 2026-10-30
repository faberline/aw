"""Request timeline evidence is read-only and never authorizes a merge."""
import copy
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

m=load('request_history_test',HERE.parent/'scripts/request_history.py')
worker=load('request_history_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
HEAD='d'*40;BASE='b'*40;TIME='2026-10-29T17:59:04Z'


def event(kind,identity,actor='github-actions'):
    return {'__typename':kind,'id':identity,'createdAt':TIME,
            'actor':{'__typename':'Bot','login':actor}}


class Api:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def api(self,path,method='GET',data=None):
        if path!='graphql' or method!='POST' or not data['query'].lstrip().startswith('query('):
            raise AssertionError('read-only GraphQL query required')
        cursor=data['variables']['after'];self.calls.append(cursor)
        page=self.pages[cursor]
        if callable(page):page=page()
        return dict(data=dict(repository=dict(nameWithOwner='o/r',pullRequest=dict(number=9,
            timelineItems=copy.deepcopy(page)))))


def page(nodes,total,more=False,cursor=None):
    return dict(totalCount=total,nodes=nodes,pageInfo=dict(hasNextPage=more,endCursor=cursor))


class RequestHistoryTests(unittest.TestCase):
    def setUp(self):
        self.policy=worker.policy();values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=m.guard.build('o/r',9,HEAD,7,self.policy,123,2,lambda n:next(values))
        self.pull=dict(state='OPEN',headRefOid=HEAD,baseRefOid=BASE,baseRefName='main',
            autoMergeRequest=dict(mergeMethod='SQUASH',enabledAt=TIME,
                enabledBy={'__typename':'Bot','login':'github-actions'},
                commitHeadline='Merge PR #9',commitBody='Closes #7'))
        self.before=dict(repo='o/r',pr=9,events=[])
        api=Api({None:page([{'__typename':'PullRequestCommit'},event('AutoSquashEnabledEvent','enable-1')],2)})
        self.after=m.stable(api,'o/r',9)

    def test_bound_request_remains_observation_only(self):
        value=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        self.assertFalse(value['authorizes_merge'])
        self.assertEqual(m.unchanged(value,self.after,self.pull,self.intent,self.policy,BASE),
                         dict(state='observed_unchanged',authorizes_merge=False))

    def test_acknowledgement_binds_the_guard_but_never_authorizes_merge(self):
        observed=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        ack=m.acknowledgement(dict(clientMutationId=self.intent['sha256']),observed,self.intent,self.policy)
        self.assertFalse(ack['authorizes_merge'])
        self.assertEqual(ack['payload']['guard_sha256'],self.intent['sha256'])
        self.assertEqual(ack['payload']['observation_sha256'],observed['sha256'])
        self.assertEqual((ack['payload']['run'],ack['payload']['attempt']),(123,2))

    def test_missing_changed_or_forged_acknowledgement_is_refused(self):
        observed=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        for response in ({},None,dict(clientMutationId='f'*64)):
            with self.subTest(response=response),self.assertRaisesRegex(RuntimeError,'request_ack_unknown'):
                m.acknowledgement(response,observed,self.intent,self.policy)
        forged=copy.deepcopy(observed);forged['payload']['owner']='f'*56
        with self.assertRaisesRegex(RuntimeError,'request_ack_unknown'):
            m.acknowledgement(dict(clientMutationId=self.intent['sha256']),forged,self.intent,self.policy)

    def test_logged_ack_is_bound_but_never_authenticates_its_source(self):
        observed=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        ack=m.acknowledgement(dict(clientMutationId=self.intent['sha256']),observed,self.intent,self.policy)
        line=m.ACK_MARKER+m.canonical(ack)
        found=m.find_acknowledgement(line,observed,self.intent,self.policy)
        self.assertEqual(found,ack)
        self.assertFalse(found['authorizes_merge'])
        with self.assertRaisesRegex(RuntimeError,'request_ack_duplicate'):
            m.find_acknowledgement(line+'\n'+line,observed,self.intent,self.policy)
        ack['payload']['client_mutation_id']='f'*64
        ack['sha256']=m.hashlib.sha256(m.canonical(ack['payload']).encode()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'request_ack_unknown'):
            m.find_acknowledgement(m.ACK_MARKER+m.canonical(ack),observed,self.intent,self.policy)

    def test_authenticated_native_inspection_rechecks_guard_and_request_without_authority(self):
        observed=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        ack=m.acknowledgement(dict(clientMutationId=self.intent['sha256']),observed,self.intent,self.policy)
        log='\n'.join((m.guard.MARKER+m.canonical(self.intent),m.MARKER+m.canonical(observed),
                       m.ACK_MARKER+m.canonical(ack)))
        calls=[]
        closer=SimpleNamespace(require_current_policy=lambda *args:calls.append('policy'),
                               guard_identity=lambda *args:calls.append('guard'))
        native=SimpleNamespace(provider_pull=lambda *args:copy.deepcopy(self.pull))
        with patch.object(m.guard,'authenticate_native_log',return_value=(self.policy,log)) as source, \
             patch.object(m.guard.worker,'read_policy',return_value=self.policy), \
             patch.object(m.guard.review,'sibling',side_effect=lambda name:closer if name=='guarded_close' else native), \
             patch.object(m,'stable',return_value=self.after):
            result=m.inspect_native_ack(None,'o/r',{},'e'*40,123,2,9,HEAD,lambda *args:'')
        source.assert_called_once()
        self.assertEqual(result['state'],'observed_unchanged')
        self.assertFalse(result['authorizes_merge'])
        self.assertEqual(result['acknowledgement_sha256'],ack['sha256'])
        self.assertEqual(calls.count('guard'),4)

    def test_native_inspection_refuses_unknown_source_and_replaced_request(self):
        observed=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        ack=m.acknowledgement(dict(clientMutationId=self.intent['sha256']),observed,self.intent,self.policy)
        log='\n'.join((m.guard.MARKER+m.canonical(self.intent),m.MARKER+m.canonical(observed),
                       m.ACK_MARKER+m.canonical(ack)))
        closer=SimpleNamespace(require_current_policy=lambda *args:None,guard_identity=lambda *args:None)
        changed=copy.deepcopy(self.pull);changed['autoMergeRequest']['commitBody']='Closes #8'
        native=SimpleNamespace(provider_pull=lambda *args:changed)
        with patch.object(m.guard,'authenticate_native_log',side_effect=RuntimeError('unknown_source')):
            with self.assertRaisesRegex(RuntimeError,'unknown_source'):
                m.inspect_native_ack(None,'o/r',{},'e'*40,123,2,9,HEAD,lambda *args:'')
        with patch.object(m.guard,'authenticate_native_log',return_value=(self.policy,log)), \
             patch.object(m.guard.worker,'read_policy',return_value=self.policy), \
             patch.object(m.guard.review,'sibling',side_effect=lambda name:closer if name=='guarded_close' else native), \
             patch.object(m,'stable',return_value=self.after):
            with self.assertRaises(RuntimeError):
                m.inspect_native_ack(None,'o/r',{},'e'*40,123,2,9,HEAD,lambda *args:'')

    def test_native_inspection_refuses_guard_change_during_observation(self):
        observed=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        ack=m.acknowledgement(dict(clientMutationId=self.intent['sha256']),observed,self.intent,self.policy)
        log='\n'.join((m.guard.MARKER+m.canonical(self.intent),m.MARKER+m.canonical(observed),
                       m.ACK_MARKER+m.canonical(ack)))
        calls=[]
        def guard_identity(*args):
            calls.append(True)
            if len(calls)==2:raise RuntimeError('guard_changed')
        closer=SimpleNamespace(require_current_policy=lambda *args:None,guard_identity=guard_identity)
        native=SimpleNamespace(provider_pull=lambda *args:copy.deepcopy(self.pull))
        with patch.object(m.guard,'authenticate_native_log',return_value=(self.policy,log)), \
             patch.object(m.guard.worker,'read_policy',return_value=self.policy), \
             patch.object(m.guard.review,'sibling',side_effect=lambda name:closer if name=='guarded_close' else native), \
             patch.object(m,'stable',return_value=self.after):
            with self.assertRaisesRegex(RuntimeError,'guard_changed'):
                m.inspect_native_ack(None,'o/r',{},'e'*40,123,2,9,HEAD,lambda *args:'')

    def test_same_second_disable_and_reenable_cannot_reuse_identity(self):
        value=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        api=Api({None:page([event('AutoSquashEnabledEvent','enable-1'),
                            event('AutoMergeDisabledEvent','disable-2'),
                            event('AutoSquashEnabledEvent','enable-3')],3)})
        later=m.snapshot(api,'o/r',9)
        with self.assertRaisesRegex(RuntimeError,'request_history_changed'):
            m.unchanged(value,later,self.pull,self.intent,self.policy,BASE)
        with self.assertRaisesRegex(RuntimeError,'request_history_changed'):
            m.bind(self.before,later,self.pull,self.intent,self.policy,BASE)

    def test_request_actor_payload_head_and_base_cannot_change(self):
        value=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        for field,replacement in (('commitBody','Closes #8'),('mergeMethod','MERGE'),
                                  ('enabledBy',{'__typename':'User','login':'human'})):
            with self.subTest(field=field):
                pull=copy.deepcopy(self.pull);pull['autoMergeRequest'][field]=replacement
                with self.assertRaises(RuntimeError):m.unchanged(value,self.after,pull,self.intent,self.policy,BASE)
        for field in ('headRefOid','baseRefOid'):
            pull=copy.deepcopy(self.pull);pull[field]='f'*40
            with self.assertRaises(RuntimeError):m.unchanged(value,self.after,pull,self.intent,self.policy,BASE)

    def test_missing_or_foreign_enable_event_never_binds(self):
        for node in (event('AutoSquashEnabledEvent','one','other'),
                     event('AutoRebaseEnabledEvent','one')):
            snapshot=m.snapshot(Api({None:page([node],1)}),'o/r',9)
            with self.assertRaisesRegex(RuntimeError,'request_history_target'):
                m.bind(self.before,snapshot,self.pull,self.intent,self.policy,BASE)

    def test_full_pagination_and_unknown_pages_fail_closed(self):
        api=Api({None:page([{'__typename':'PullRequestCommit'}],2,True,'next'),
                 'next':page([event('AutoSquashEnabledEvent','one')],2)})
        self.assertEqual(len(m.snapshot(api,'o/r',9)['events']),1)
        self.assertEqual(api.calls,[None,'next'])
        for pages in ({None:page([event('AutoSquashEnabledEvent','one')],2)},
                      {None:page([event('AutoSquashEnabledEvent','one')],2,True,'next'),
                       'next':page([event('AutoSquashEnabledEvent','one')],2)},
                      {None:page([None],1)}):
            with self.subTest(pages=pages),self.assertRaises(RuntimeError):m.snapshot(Api(pages),'o/r',9)

    def test_unstable_timeline_and_forged_record_are_not_accepted(self):
        calls=[]
        def changing():
            calls.append(True)
            return page([event('AutoSquashEnabledEvent','one' if len(calls)==1 else 'two')],1)
        with self.assertRaisesRegex(RuntimeError,'request_history_changed'):
            m.stable(Api({None:changing}),'o/r',9)
        value=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        value['payload']['owner']='f'*56
        with self.assertRaisesRegex(RuntimeError,'request_history_record'):
            m.unchanged(value,self.after,self.pull,self.intent,self.policy,BASE)

    def test_malformed_event_cannot_form_a_matching_record(self):
        for key,replacement in (('id',''),('created_at','2026-99-99T00:00:00Z'),
                                ('actor_type','Unexpected')):
            after=copy.deepcopy(self.after);after['events'][0][key]=replacement
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'request_history_unknown'):
                m.bind(self.before,after,self.pull,self.intent,self.policy,BASE)

    def test_recomputed_record_hash_cannot_hide_changed_preexisting_events(self):
        value=m.bind(self.before,self.after,self.pull,self.intent,self.policy,BASE)
        value['payload']['previous_events_sha256']='0'*64
        value['sha256']=m.hashlib.sha256(m.canonical(value['payload']).encode()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'request_history_changed'):
            m.unchanged(value,self.after,self.pull,self.intent,self.policy,BASE)


if __name__=='__main__':unittest.main()
