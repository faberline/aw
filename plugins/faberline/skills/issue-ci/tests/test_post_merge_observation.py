"""The read-only observation is not a merge or issue-close authorization."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/post_merge_observation.py'
spec = importlib.util.spec_from_file_location('post_merge_observation', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

REPO, HEAD, BASE, MERGE = 'owner/repo', 'a' * 40, 'b' * 40, 'c' * 40


class Fixture:
    def __init__(self):
        self.issue = dict(number=7, state='open', title='Issue', body='Contract',
                          updated_at='2026-10-28T23:00:00Z',
                          labels=[{'name': 'doing'}, {'name': 'priority:p1'}],
                          assignees=[{'login': 'worker'}])
        self.pull = dict(number=9, state='closed', merged=True, merged_at='2026-10-29T00:00:00Z',
            merged_by=dict(id=41898282, type='Bot', login='github-actions[bot]'),
            title='PR', body='Work\n\nCloses #7\n', merge_commit_sha=MERGE,
            head=dict(sha=HEAD, repo=dict(full_name=REPO)),
            base=dict(sha=BASE, ref='main', repo=dict(full_name=REPO)))
        self.meta = dict(full_name=REPO, default_branch='main')
        self.commit = dict(sha=MERGE, parents=[dict(sha=BASE)])
        self.compare = dict(status='ahead', base_commit=dict(sha=MERGE))
        self.timeline = []
        self.links = dict(totalCount=0, nodes=[], pageInfo=dict(hasNextPage=False))
        self.checkpoint = dict(repo=REPO, pr=9, issue=7, head=HEAD, base=BASE, base_ref='main',
            issue_sha256=m.review.r.digest({k: self.issue[k] for k in ('number', 'title', 'body')}),
            pr_sha256=m.review.r.digest([self.pull['title'], self.pull['body']]),
            issue_updated_at=self.issue['updated_at'],
            labels=['doing', 'priority:p1'], assignees=['worker'])
        self.calls = []

    def api(self, path, method='GET', data=None):
        self.calls.append((path, method))
        if path == 'graphql' and method == 'POST':
            if not data['query'].startswith('query('):
                raise AssertionError('observation query must not mutate')
            return dict(data=dict(repository=dict(nameWithOwner=REPO,
                issue=dict(number=7,closedByPullRequestsReferences=copy.deepcopy(self.links)))))
        if method != 'GET':
            raise AssertionError('observation must never mutate')
        values = {f'repos/{REPO}': self.meta, f'repos/{REPO}/pulls/9': self.pull,
                  f'repos/{REPO}/commits/{MERGE}': self.commit,
                  f'repos/{REPO}/compare/{MERGE}...main': self.compare,
                  f'repos/{REPO}/issues/7': self.issue}
        return copy.deepcopy(values[path])

    def pages(self, path):
        if path != f'repos/{REPO}/issues/7/timeline':
            raise AssertionError(path)
        return copy.deepcopy(self.timeline)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def observe(self):
        return m.observe(self.f, self.f.checkpoint)

    def capture(self):
        pull = copy.deepcopy(self.f.pull)
        pull.update(state='open', merged=False, draft=False)
        c = self.f.checkpoint
        return m.capture(REPO, 9, HEAD, BASE, 'main', pull, self.f.issue,
                         c['issue_sha256'], c['pr_sha256'])

    def test_ownership_baseline_accepts_earlier_changes_but_not_a_later_claim(self):
        start='2026-10-29T00:00:00Z'
        labeled=dict(id=101,event='labeled',created_at=start)
        comment=dict(event='commented',created_at=start)
        seal=m.ownership_seal([labeled,comment])
        self.assertTrue(m.ownership_history([labeled,comment],start,seal))
        with self.assertRaisesRegex(RuntimeError,'close_ownership_epoch_changed'):
            m.ownership_history([labeled,comment,dict(id=102,event='assigned',created_at=start)],start,seal)
        changed=dict(labeled,event='unlabeled')
        with self.assertRaisesRegex(RuntimeError,'close_ownership_epoch_changed'):
            m.ownership_history([changed],start,seal)
        with self.assertRaisesRegex(RuntimeError,'close_history_unknown'):
            m.ownership_seal([dict(event='labeled',created_at=start)])

    def test_baseline_requires_two_matching_complete_timelines_and_issue_reads(self):
        f=self.f;f.pull.update(state='open',merged=False,draft=False)
        f.timeline=[dict(id=101,event='labeled',created_at='2026-10-29T00:00:00Z')]
        c=f.checkpoint
        def snapshot():
            return m.stable_ownership(f,REPO,9,HEAD,BASE,'main',f.pull,f.issue,c['issue_sha256'],c['pr_sha256'])
        self.assertEqual(snapshot(),m.ownership_seal(f.timeline))
        pages=f.pages;count=[]
        def changing(path):
            count.append(True)
            if len(count)==2:f.timeline.append(dict(id=102,event='assigned',created_at='2026-10-29T00:00:00Z'))
            return pages(path)
        f.pages=changing
        with self.assertRaisesRegex(RuntimeError,'close_ownership_snapshot_changed'):snapshot()

    def test_baseline_refuses_changed_issue_during_collection(self):
        f=self.f;f.pull.update(state='open',merged=False,draft=False)
        c=f.checkpoint;pages=f.pages
        def changing(path):
            f.issue['updated_at']='2026-10-29T00:00:01Z'
            return pages(path)
        f.pages=changing
        with self.assertRaisesRegex(RuntimeError,'close_ownership_snapshot_changed'):
            m.stable_ownership(f,REPO,9,HEAD,BASE,'main',f.pull,f.issue,c['issue_sha256'],c['pr_sha256'])

    def test_checkpoint_baseline_rejects_new_claim_even_in_the_same_second(self):
        start='2026-10-29T00:00:00Z'
        self.f.timeline=[dict(id=101,event='labeled',created_at=start)]
        checkpoint=dict(self.f.checkpoint,ownership_events=m.ownership_seal(self.f.timeline))
        evidence=m.record(checkpoint,123,1,'d'*40,'e'*40,200,'f'*64)
        parsed=m.parse_records(m.MARKER+m.review.r.canonical(evidence))
        self.assertEqual(parsed,[evidence])
        self.assertEqual(m.recovery_plan(self.f,checkpoint,start)['state'],'guard_required')
        self.f.timeline.append(dict(id=102,event='assigned',created_at=start))
        with self.assertRaisesRegex(RuntimeError,'close_ownership_epoch_changed'):
            m.recovery_plan(self.f,checkpoint,start)

    def test_capture_is_only_a_snapshot(self):
        self.assertEqual(self.capture(), self.f.checkpoint)

    def test_capture_refuses_changed_or_blocked_issue(self):
        for change in (lambda f: f.issue.update(state='closed'),
                       lambda f: f.issue.update(body='changed'),
                       lambda f: f.issue['labels'].append({'name': 'blocked'}),
                       lambda f: f.checkpoint.update(pr_sha256='0' * 64)):
            with self.subTest(change=change):
                self.f = Fixture()
                change(self.f)
                with self.assertRaises(m.review.Refused):
                    self.capture()

    def test_capture_verified_binds_receipt_source_and_review_target(self):
        source, evaluator = 'd' * 40, 'e' * 40
        pull = copy.deepcopy(self.f.pull)
        pull.update(state='open', merged=False, draft=False)
        c = self.f.checkpoint
        payload = dict(version=1, producer=dict(run_id=202), result=dict(decision='pass'),
            context=dict(repo=REPO, pr=9, head=HEAD, base=BASE, base_ref='main', evaluator=evaluator,
                         policy_source=source, issue_sha256=c['issue_sha256'],
                         pr_description_sha256=c['pr_sha256']))
        def prepare(value):
            receipt = dict(payload=value, sha256=m.review.r.digest(value))
            return m.capture_verified(REPO, 9, HEAD, pull, self.f.issue, receipt,
                                      101, 2, source, evaluator)
        item = prepare(payload)
        self.assertEqual(item['payload']['review_run'], 202)
        self.assertEqual(item['payload']['checkpoint'], c)
        for change in (lambda p: p['result'].update(decision='fail'),
                       lambda p: p['context'].update(head='f' * 40),
                       lambda p: p['context'].update(policy_source='f' * 40),
                       lambda p: p['producer'].update(run_id=True)):
            with self.subTest(change=change):
                bad = copy.deepcopy(payload)
                change(bad)
                with self.assertRaises(m.review.Refused):
                    prepare(bad)
        with self.assertRaises(m.review.Refused):
            m.capture_verified(REPO, 9, HEAD, pull, self.f.issue,
                dict(payload=payload, sha256='0' * 64), 101, 2, source, evaluator)

    def test_record_integrity_does_not_authenticate_producer(self):
        item = m.record(self.f.checkpoint, 101, 2, 'd' * 40, 'e' * 40, 202, 'f' * 64)
        line = '2026-10-29T00:00:00.000Z ' + m.MARKER + m.review.r.canonical(item) + '\n'
        self.assertEqual(m.find_record(line, REPO, 9, HEAD), item)
        with self.assertRaises(m.review.Refused):
            m.find_record(line + line, REPO, 9, HEAD)
        with self.assertRaises(m.review.Refused):
            m.find_record(line.replace('"source_run":101', '"source_run":102'), REPO, 9, HEAD)
        with self.assertRaises(m.review.Refused):
            m.find_record(line, REPO, 9, 'd' * 40)
        with self.assertRaises(m.review.Refused):
            m.record(self.f.checkpoint, True, 2, 'd' * 40, 'e' * 40, 202, 'f' * 64)

    def test_scanner_keeps_all_distinct_targets_and_rejects_duplicates(self):
        one=m.record(self.f.checkpoint,101,2,'d'*40,'e'*40,202,'f'*64)
        other=copy.deepcopy(self.f.checkpoint);other.update(pr=10,head='9'*40)
        two=m.record(other,101,2,'d'*40,'e'*40,203,'f'*64)
        line=lambda item:m.MARKER+m.review.r.canonical(item)+'\n'
        self.assertEqual(m.parse_records(line(one)+line(two)),[one,two])
        self.assertEqual(m.parse_records('ordinary job output\n'),[])
        with self.assertRaises(m.review.Refused):
            m.parse_records(line(one)+line(one))

    def test_only_default_branch_writer_source_can_authenticate(self):
        source, evaluator = 'd' * 40, 'e' * 40
        item = m.record(self.f.checkpoint, 101, 2, source, evaluator, 202, 'f' * 64)
        log = m.MARKER + m.review.r.canonical(item) + '\n'
        document = dict(source_commit=source, config={})
        calls = []
        class Native:
            def policy(self, doc):
                self.assert_doc(doc)
                return dict(producer={'id': 3})
            def assert_doc(self, doc):
                assert doc == document
            def writer_contract(self, api, repo, doc, revision):
                calls.append(('contract', revision))
        def identity(api, repo, doc, run, completed, spec, events):
            calls.append(('events', events))
            self.assertEqual(events, ('issues', 'workflow_run', 'repository_dispatch'))
            self.assertTrue(completed)
            return dict(head=source, run_attempt=2, job_id=404)
        class Api:
            def api(self, path):
                if path == f'repos/{REPO}':
                    return dict(full_name=REPO, default_branch='main')
                return dict(status='ahead', base_commit=dict(sha=source))
        def verify(source_log=log):
            with patch.object(m.review, 'sibling', return_value=Native()), \
                 patch.object(m.review, 'producer_identity', side_effect=identity):
                return m.authenticate_native_source(Api(), REPO, document, evaluator, 101, 9, HEAD,
                                                    lambda repo, job: source_log)
        self.assertEqual(verify(), item)
        self.assertIn(('contract', evaluator), calls)
        wrong_attempt = m.record(self.f.checkpoint, 101, 1, source, evaluator, 202, 'f' * 64)
        with self.assertRaisesRegex(m.review.Refused, 'close_record_source_mismatch'):
            verify(m.MARKER + m.review.r.canonical(wrong_attempt))
        with self.assertRaisesRegex(m.review.Refused, 'close_record_missing_or_duplicate'):
            verify('no checkpoint')
        class DivergedApi(Api):
            def api(self, path):
                if path == f'repos/{REPO}':
                    return super().api(path)
                return dict(status='diverged',base_commit=dict(sha=source))
        with patch.object(m.review,'sibling',return_value=Native()), \
             patch.object(m.review,'producer_identity',side_effect=identity), \
             self.assertRaisesRegex(m.review.Refused,'close_source_not_on_default'):
            m.authenticate_native_source(DivergedApi(),REPO,document,evaluator,101,9,HEAD,lambda repo,job:log)

    def test_adoption_publisher_requires_the_exact_reviewed_grant(self):
        source, evaluator, grant_source = 'd' * 40, 'e' * 40, '1' * 40
        c = self.f.checkpoint
        grant = {key:c[key] for key in ('repo','pr','issue','head','base','base_ref','issue_sha256','pr_sha256')}
        grant['review'] = dict(run_id=202, receipt_sha256='f' * 64)
        document = dict(source_commit=grant_source,config={})
        item = m.record(c, 101, 2, source, evaluator, 202, 'f' * 64,
                        m.review.r.digest(grant), grant_source)
        log = m.MARKER + m.review.r.canonical(item)
        calls = []
        class Adoption:
            GRANTS_REPO = 'faberline/aw'
            def choose_grant(self, config, repo, selected):
                self.check(config, repo, selected)
                return grant
            def check(self, config, repo, selected):
                assert config == {} and repo == REPO and selected == evaluator
            def verify_grant_attestation(self, *args):
                calls.append('grant_review')
            def publisher_provenance(self, api, selected, run, event, completed=False, attempt=None):
                assert selected == grant and run == 101 and event == 'push' and completed
                assert attempt in (None,2)
                calls.append('publisher')
                return dict(run_attempt=2, job_id=404, source_commit=source)
        class API:
            def api(self, path):
                if path == 'repos/faberline/aw':
                    return dict(full_name='faberline/aw',default_branch='main')
                return dict(status='ahead',base_commit=dict(sha=grant_source))
        def verify(source_log):
            with patch.object(m.review, 'sibling', return_value=Adoption()):
                return m.authenticate_adoption_source(API(), REPO, document, evaluator,
                                                      101, 9, HEAD, lambda repo, job:source_log)
        self.assertEqual(verify(log), item)
        self.assertEqual(calls, ['grant_review','publisher'])
        with patch.object(m.review, 'sibling', return_value=Adoption()):
            self.assertEqual(m.authenticate_adoption_source(API(), REPO, document, evaluator,
                             101, 9, HEAD, lambda repo,job:log,source_attempt=2),item)
        forged = m.record(c,101,2,source,evaluator,202,'f' * 64,'0' * 64,grant_source)
        with self.assertRaisesRegex(m.review.Refused, 'close_grant_record_mismatch'):
            verify(m.MARKER + m.review.r.canonical(forged))

    def test_exact_squash_merge_is_only_an_observation(self):
        self.assertEqual(self.observe(), dict(state='observed_merged', repo=REPO,
                                              pr=9, issue=7, head=HEAD, merge=MERGE))
        self.assertTrue(all(method == 'GET' for _, method in self.f.calls))

    def test_matching_snapshot_does_not_claim_ownership_history(self):
        # A worker may have claimed and released between observations. The
        # caller still needs an authenticated ownership epoch and shared guard.
        self.f.issue['labels'] = [{'name': 'doing'}, {'name': 'priority:p1'}]
        self.assertEqual(self.observe()['state'], 'observed_merged')

    def test_later_claim_and_restored_labels_are_visible_in_history(self):
        events = [dict(event='unlabeled', created_at='2026-10-29T00:00:01Z'),
                  dict(event='labeled', created_at='2026-10-29T00:00:02Z')]
        with self.assertRaisesRegex(m.review.Refused, 'close_ownership_epoch_changed'):
            m.ownership_history(events, '2026-10-29T00:00:00Z')
        self.assertEqual(self.observe()['state'], 'observed_merged')

    def test_history_fails_closed_on_same_second_unknown_or_bad_data(self):
        start = '2026-10-29T00:00:00Z'
        for events in ([dict(event='assigned', created_at=start)],
                       [dict(event='unexpected', created_at='2026-10-29T00:00:01Z')],
                       [dict(event='labeled', created_at='not-a-time')], [dict(event='labeled')]):
            with self.subTest(events=events):
                with self.assertRaises(m.review.Refused):
                    m.ownership_history(events, start)
        self.assertTrue(m.ownership_history(
            [dict(event='labeled', created_at='2026-10-28T23:59:59Z'),
             dict(event='cross-referenced', created_at='2026-10-29T00:00:01Z')], start))
        with self.assertRaises(m.review.Refused):
            m.ownership_history([dict(event='assigned', created_at=start)],
                                '2026-10-29T00:00:00.999Z')

    def test_source_start_uses_exact_successful_attempt(self):
        item=m.record(self.f.checkpoint,101,2,'d'*40,'e'*40,202,'f'*64)
        run=dict(id=101,run_attempt=2,status='completed',conclusion='success',head_sha='d'*40,
                 repository=dict(full_name=REPO),head_repository=dict(full_name=REPO),
                 run_started_at='2026-10-29T00:00:00Z')
        class API:
            def api(self,path):
                assert path==f'repos/{REPO}/actions/runs/101/attempts/2'
                return run
        self.assertEqual(m.source_started_at(API(),item),'2026-10-29T00:00:00Z')
        for key,value in (('run_attempt',1),('conclusion','failure'),('head_sha','f'*40),
                          ('run_started_at',None)):
            original=run[key];run[key]=value
            with self.subTest(key=key),self.assertRaises(m.review.Refused):
                m.source_started_at(API(),item)
            run[key]=original
        run['conclusion']='failure'
        self.assertEqual(m.source_started_at(API(),item,require_success=False),'2026-10-29T00:00:00Z')
        run['conclusion']='neutral'
        with self.assertRaises(m.review.Refused):m.source_started_at(API(),item,require_success=False)

    def test_other_open_linked_pr_or_unknown_pagination_refuses(self):
        class Graph:
            def __init__(self):
                self.links = dict(totalCount=0, nodes=[], pageInfo=dict(hasNextPage=False))
            def api(self, path, method, data):
                self.assert_read_query(path, method, data)
                return dict(data=dict(repository=dict(nameWithOwner=REPO,
                    issue=dict(number=7, closedByPullRequestsReferences=self.links))))
            def assert_read_query(self, path, method, data):
                assert path == 'graphql' and method == 'POST'
                assert data['query'].startswith('query(') and 'mutation' not in data['query']
        graph = Graph()
        self.assertTrue(m.no_open_linked_prs(graph, REPO, 7))
        for links in (dict(totalCount=1, nodes=[dict(number=10, state='OPEN',
                                                     repository=dict(nameWithOwner=REPO))],
                           pageInfo=dict(hasNextPage=False)),
                      dict(totalCount=0, nodes=[], pageInfo=dict(hasNextPage=True)),
                      dict(totalCount=0, nodes=None, pageInfo=dict(hasNextPage=False)),
                      dict(totalCount=True, nodes=[], pageInfo=dict(hasNextPage=False))):
            with self.subTest(links=links):
                graph.links = links
                with self.assertRaises(m.review.Refused):
                    m.no_open_linked_prs(graph, REPO, 7)

    def test_link_pagination_must_be_complete_and_stable(self):
        class Graph:
            def __init__(self):
                self.last_state = 'MERGED'
            def api(self, path, method, data):
                after = data['variables']['after']
                node = dict(number=9 if after is None else 10,
                            state='MERGED' if after is None else self.last_state,
                            repository=dict(nameWithOwner=REPO))
                links = dict(totalCount=2, nodes=[node], pageInfo=dict(
                    hasNextPage=after is None, endCursor='page-1' if after is None else 'page-2'))
                return dict(data=dict(repository=dict(nameWithOwner=REPO,
                    issue=dict(number=7, closedByPullRequestsReferences=links))))
        graph = Graph()
        self.assertTrue(m.no_open_linked_prs(graph, REPO, 7))
        graph.last_state = 'OPEN'
        with self.assertRaisesRegex(m.review.Refused, 'close_other_open_pr'):
            m.no_open_linked_prs(graph, REPO, 7)

    def test_link_count_and_cursor_inconsistencies_refuse(self):
        class Graph:
            def __init__(self, links):
                self.links = links
            def api(self, path, method, data):
                return dict(data=dict(repository=dict(nameWithOwner=REPO,
                    issue=dict(number=7, closedByPullRequestsReferences=self.links))))
        node = dict(number=9, state='MERGED', repository=dict(nameWithOwner=REPO))
        variants = [dict(totalCount=2, nodes=[node], pageInfo=dict(hasNextPage=False)),
                    dict(totalCount=1, nodes=[node], pageInfo=dict(hasNextPage=True, endCursor=None)),
                    dict(totalCount=1, nodes=[node], pageInfo=dict(hasNextPage=True, endCursor='repeat'))]
        for links in variants:
            with self.subTest(links=links), self.assertRaises(m.review.Refused):
                m.no_open_linked_prs(Graph(links), REPO, 7)

    def test_already_closed_is_idempotent_observation(self):
        self.f.issue.update(state='closed', labels=[])
        self.assertEqual(self.observe()['state'], 'observed_closed')

    def test_recovery_never_authorizes_queued_or_cancelled_pr(self):
        start='2026-10-29T00:00:00Z'
        for state,expected in (('open','waiting'),('closed','not_merged')):
            with self.subTest(state=state):
                self.f.pull.update(state=state,merged=False)
                result=m.recovery_plan(self.f,self.f.checkpoint,start)
                self.assertEqual(result['state'],expected)
                self.assertIs(result['close_authorized'],False)
                self.assertTrue(all(method=='GET' for _,method in self.f.calls))

    def test_recovery_reports_guard_required_only_after_full_observation(self):
        start='2026-10-29T00:00:00Z'
        result=m.recovery_plan(self.f,self.f.checkpoint,start)
        self.assertEqual(result['state'],'guard_required')
        self.assertIs(result['close_authorized'],False)
        self.f.timeline=[dict(event='labeled',created_at='2026-10-29T00:00:01Z')]
        with self.assertRaisesRegex(m.review.Refused,'close_ownership_epoch_changed'):
            m.recovery_plan(self.f,self.f.checkpoint,start)
        self.f.timeline=[]
        self.f.links=dict(totalCount=1,nodes=[dict(number=10,state='OPEN',repository=dict(nameWithOwner=REPO))],
                          pageInfo=dict(hasNextPage=False))
        with self.assertRaisesRegex(m.review.Refused,'close_other_open_pr'):
            m.recovery_plan(self.f,self.f.checkpoint,start)

    def test_recovery_closed_issue_has_no_close_authority(self):
        self.f.issue.update(state='closed',labels=[])
        result=m.recovery_plan(self.f,self.f.checkpoint,'2026-10-29T00:00:00Z')
        self.assertEqual(result['state'],'observed_closed')
        self.assertIs(result['close_authorized'],False)

    def test_one_refused_recovery_does_not_drop_other_records(self):
        one=m.record(self.f.checkpoint,101,2,'d'*40,'e'*40,202,'f'*64)
        other=copy.deepcopy(self.f.checkpoint);other.update(pr=10,head='9'*40)
        two=m.record(other,101,2,'d'*40,'e'*40,203,'f'*64)
        visited=[]
        def plan(api,checkpoint,start):
            visited.append(checkpoint['pr'])
            if checkpoint['pr']==9:
                raise m.review.Refused('close_issue_ownership_changed')
            return dict(state='guard_required',pr=checkpoint['pr'],close_authorized=False)
        with patch.object(m,'authenticate_native_records',return_value=[one,two]), \
             patch.object(m,'source_started_at',return_value='2026-10-29T00:00:00Z'), \
             patch.object(m,'recovery_plan',side_effect=plan):
            rows=m.native_recovery_plans(None,REPO,{},'e'*40,101,None)
        self.assertEqual(visited,[9,10])
        self.assertEqual([row['state'] for row in rows],['held','guard_required'])
        self.assertTrue(all(row['close_authorized'] is False for row in rows))

    def test_adoption_recovery_authenticates_before_observation(self):
        item=m.record(self.f.checkpoint,101,2,'d'*40,'e'*40,202,'f'*64,'0'*64,'1'*40)
        with patch.object(m,'authenticate_adoption_source',return_value=item) as auth, \
             patch.object(m,'source_started_at',return_value='2026-10-29T00:00:00Z'), \
             patch.object(m,'recovery_plan',return_value=dict(state='guard_required',close_authorized=False)) as plan:
            result=m.adoption_recovery_plan(None,REPO,{},'e'*40,101,9,HEAD,None)
        auth.assert_called_once()
        self.assertEqual(plan.call_args.args[1],self.f.checkpoint)
        self.assertEqual(result['state'],'guard_required')
        self.assertFalse(result['close_authorized'])

    def test_pending_stale_foreign_or_manual_merges_refuse(self):
        changes = [lambda f: f.pull.update(state='open', merged=False),
                   lambda f: f.pull.update(state='closed', merged=False),
                   lambda f: f.pull['head'].update(sha='d' * 40),
                   lambda f: f.pull['base'].update(sha='d' * 40),
                   lambda f: f.pull['head']['repo'].update(full_name='fork/repo'),
                   lambda f: f.pull['merged_by'].update(type='User', login='human'),
                   lambda f: f.pull.update(merge_commit_sha=None),
                   lambda f: f.meta.update(default_branch='other'),
                   lambda f: f.commit.update(parents=[dict(sha='d' * 40)]),
                   lambda f: f.compare.update(status='diverged')]
        for change in changes:
            with self.subTest(change=change):
                self.f = Fixture()
                change(self.f)
                with self.assertRaises(m.review.Refused):
                    self.observe()

    def test_contract_and_owner_changes_refuse(self):
        changes = [lambda f: f.pull.update(body='Closes #8'),
                   lambda f: f.pull.update(body='Closes #7\nCloses #8'),
                   lambda f: f.pull.update(title='Changed'),
                   lambda f: f.issue.update(body='Changed'),
                   lambda f: f.issue['labels'].append({'name': 'blocked'}),
                   lambda f: f.issue.update(labels=[{'name': 'todo'}]),
                   lambda f: f.issue.update(assignees=[{'login': 'other'}]),
                   lambda f: f.issue.update(updated_at='2026-10-29T00:00:01Z'),
                   lambda f: f.issue.update(number=True)]
        for change in changes:
            with self.subTest(change=change):
                self.f = Fixture()
                change(self.f)
                with self.assertRaises(m.review.Refused):
                    self.observe()

    def test_malformed_checkpoint_refuses(self):
        changes = [lambda c: c.update(head='bad'), lambda c: c.update(pr=True),
                   lambda c: c.update(issue_updated_at='unknown'),
                   lambda c: c.update(labels=['doing', 'doing']),
                   lambda c: c.update(assignees=['z', 'a']),
                   lambda c: c.update(extra='untrusted')]
        for change in changes:
            with self.subTest(change=change):
                self.f = Fixture()
                change(self.f.checkpoint)
                with self.assertRaises(m.review.Refused):
                    self.observe()

    def test_provider_errors_are_not_suppressed(self):
        def unavailable(*args, **kwargs):
            raise OSError('provider unavailable')
        self.f.api = unavailable
        with self.assertRaises(OSError):
            self.observe()


if __name__ == '__main__':
    unittest.main()
